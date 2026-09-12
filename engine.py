"""tatbench harvest engine - brand-agnostic.

Owns scheduling, rate limiting, retries, the circuit breaker, resume and
storage. Knows nothing about any particular storefront; that lives in adapters/.

Storage layout:

    data/
      runs.jsonl                      one summary line per completed brand-run
      <brand>/<YYYY-MM-DD>/rows.csv   the raw rows, appended as they complete

Rerunning the same brand and run-date resumes into the same rows.csv: rows that
already succeeded are skipped, rows that errored are retried.
"""
from __future__ import annotations

import csv
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

from adapters import ParseError, get as get_adapter

IST = timezone(timedelta(hours=5, minutes=30))

COLS = ["pincode", "city_in", "state", "city_api", "serviceable",
        "fast_min", "fast_max", "std_min", "std_max", "error"]


def today_ist() -> date:
    return datetime.now(IST).date()


class Limiter:
    """Global token bucket - caps total requests/sec across all workers."""

    def __init__(self, rps: float):
        self.gap = 1.0 / rps if rps > 0 else 0.0
        self._lock = threading.Lock()
        self._next = time.monotonic()

    def wait(self):
        if not self.gap:
            return
        with self._lock:
            now = time.monotonic()
            self._next = max(self._next, now) + self.gap
            delay = self._next - self.gap - now
        if delay > 0:
            time.sleep(delay)


class Breaker:
    """Abort a run that is failing consistently.

    A sweep that has started getting 403s or 429s on everything is being
    blocked. Continuing wastes hours, produces a useless file, and makes the
    block worse. Trip early and loudly instead.

    Only trips after `min_samples` so a handful of early timeouts on a slow
    link cannot kill a legitimate run.
    """

    def __init__(self, threshold: float = 0.35, min_samples: int = 200):
        self.threshold = threshold
        self.min_samples = min_samples
        self.total = 0
        self.errors = 0
        self.tripped = threading.Event()
        self._lock = threading.Lock()

    def record(self, ok: bool):
        with self._lock:
            self.total += 1
            if not ok:
                self.errors += 1
            if (self.total >= self.min_samples
                    and self.errors / self.total >= self.threshold
                    and not self.tripped.is_set()):
                self.tripped.set()
                return False
        return True

    @property
    def rate(self) -> float:
        return self.errors / self.total if self.total else 0.0


def fetch(adapter, sku, pincode, lim, breaker, timeout, retries, stop):
    """One pincode, with backoff. Returns (status, text) or raises."""
    last = "unknown"
    for attempt in range(retries + 1):
        if stop.is_set() or breaker.tripped.is_set():
            return None, "aborted"
        lim.wait()
        url, headers, method, body = adapter.build(sku, pincode)
        req = urllib.request.Request(url, headers=headers, method=method, data=body)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            # 403/401 mean blocked or gated - retrying makes it worse.
            if exc.code in (401, 403):
                return None, last
            if exc.code != 429 and 400 <= exc.code < 500:
                return None, last
        except Exception as exc:
            last = type(exc).__name__
        if attempt < retries:
            time.sleep(min(45.0, 2.0 * 2 ** attempt) * (0.6 + 0.8 * random.random()))
    return None, last


def load_pincodes(path: str):
    with open(path, newline="", encoding="utf-8") as fh:
        return [{"pincode": (r.get("pincode") or "").strip(),
                 "city": (r.get("city") or "").strip(),
                 "state": (r.get("state") or "").strip()}
                for r in csv.DictReader(fh) if (r.get("pincode") or "").strip()]


def load_done(path: str):
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["pincode"] for r in csv.DictReader(fh)
                if r.get("pincode") and not (r.get("error") or "").strip()}


def run_brand(brand: str, cfg: dict, pincodes_path: str, root: str,
              run_date: date, limit: int = 0, stop=None, log=print) -> dict:
    """Harvest one brand. Returns a summary dict (also appended to runs.jsonl)."""
    stop = stop or threading.Event()
    adapter = get_adapter(brand)

    sku = str(cfg["sku"])
    rps = min(float(cfg.get("rps", 2.0)), adapter.max_rps)   # adapter ceiling wins
    workers = int(cfg.get("workers", 4))
    timeout = float(cfg.get("timeout", 20))
    retries = int(cfg.get("retries", 3))

    out_dir = os.path.join(root, brand, run_date.isoformat())
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "rows.csv")

    src = load_pincodes(pincodes_path)
    done = load_done(out_path)
    todo = [r for r in src if r["pincode"] not in done]
    if limit:
        todo = todo[:limit]

    log(f"[{brand}] sku={sku} date={run_date} total={len(src)} done={len(done)} "
        f"todo={len(todo)} rps={rps} workers={workers}")
    if not todo:
        log(f"[{brand}] already complete")
        return summarise(brand, sku, run_date, out_path, 0.0, False)

    log(f"[{brand}] ETA ~{len(todo)/max(rps,0.1)/60:.0f} min -> {out_path}")

    lim = Limiter(rps)
    breaker = Breaker(float(cfg.get("error_threshold", 0.35)),
                      int(cfg.get("breaker_min_samples", 200)))
    fresh = not os.path.exists(out_path)
    n = errs = 0
    t0 = time.monotonic()
    write_lock = threading.Lock()

    with open(out_path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        if fresh:
            w.writeheader()
            fh.flush()

        def job(rec):
            row = {c: "" for c in COLS}
            row.update(pincode=rec["pincode"], city_in=rec["city"], state=rec["state"])
            status, text = fetch(adapter, sku, rec["pincode"], lim, breaker,
                                 timeout, retries, stop)
            if status is None:
                row["error"] = text
                return row, False
            try:
                p = adapter.parse(status, text, run_date)
            except ParseError as exc:
                row["error"] = f"parse: {exc}"
                return row, False
            row["city_api"] = p.get("city", "")
            row["serviceable"] = p.get("serviceable", "")
            for key, pre in (("fast", "fast"), ("std", "std")):
                lo, hi = p.get(key) or (None, None)
                row[f"{pre}_min"] = "" if lo is None else lo
                row[f"{pre}_max"] = "" if hi is None else hi
            return row, True

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(job, r): r for r in todo}
            for fut in as_completed(futures):
                try:
                    row, ok = fut.result()
                except Exception as exc:            # never lose a whole run
                    rec = futures[fut]
                    row = {c: "" for c in COLS}
                    row.update(pincode=rec["pincode"], city_in=rec["city"],
                               state=rec["state"], error=f"worker: {exc}")
                    ok = False
                # Rows abandoned because the breaker tripped or we were asked to
                # stop are NOT written: they say nothing about the brand, and
                # leaving them out means a resume simply picks them up again.
                if row.get("error") == "aborted":
                    continue
                breaker.record(ok)
                with write_lock:
                    w.writerow(row)
                    n += 1
                    errs += (not ok)
                    if n % 500 == 0:
                        fh.flush()
                        rate = n / max(time.monotonic() - t0, 1e-3)
                        log(f"[{brand}] {n}/{len(todo)} {rate:.1f}/s "
                            f"err={errs} ({breaker.rate:.0%}) "
                            f"~{(len(todo)-n)/max(rate,1e-3)/60:.0f} min left")
                if breaker.tripped.is_set():
                    stop.set()
        fh.flush()

    tripped = breaker.tripped.is_set()
    if tripped:
        log(f"[{brand}] CIRCUIT BREAKER TRIPPED at {breaker.rate:.0%} errors "
            f"after {breaker.total} requests - aborted. "
            f"Likely rate-limited or blocked. Do NOT simply rerun: lower rps, "
            f"check for a 403/429 pattern in the error column, and confirm the "
            f"endpoint still behaves before trying again.")

    elapsed = time.monotonic() - t0
    log(f"[{brand}] wrote {n} rows ({errs} errored) in {elapsed/60:.1f} min")
    return summarise(brand, sku, run_date, out_path, elapsed, tripped)


def summarise(brand, sku, run_date, out_path, elapsed, tripped) -> dict:
    """Compute the run's headline metrics from the rows file."""
    import statistics as st

    with open(out_path, newline="", encoding="utf-8") as fh:
        raw = list(csv.DictReader(fh))

    # A resumed run appends a fresh row for each pincode it retried, so the file
    # can hold both the old failure and the new success. Collapse to one row per
    # pincode, preferring a success - otherwise every resume reports inflated
    # error counts and a wrong serviceability denominator.
    best = {}
    for r in raw:
        pin = r.get("pincode") or ""
        good = not (r.get("error") or "").strip()
        if pin not in best or (good and not best[pin][0]):
            best[pin] = (good, r)
    rows = [r for _, r in best.values()]

    ok = [r for r in rows if not (r.get("error") or "").strip()]
    serv = [r for r in ok if r.get("serviceable") == "Yes"]

    def nums(key):
        out = []
        for r in serv:
            v = (r.get(key) or "").strip()
            if v:
                try:
                    out.append(int(float(v)))
                except ValueError:
                    pass
        return out

    s = {
        "brand": brand, "sku": sku, "run_date": run_date.isoformat(),
        "captured_at": datetime.now(IST).isoformat(timespec="seconds"),
        "rows": len(rows), "ok": len(ok), "errors": len(rows) - len(ok),
        "serviceable": len(serv),
        "serviceable_pct": round(100 * len(serv) / len(ok), 2) if ok else None,
        "elapsed_min": round(elapsed / 60, 1), "breaker_tripped": tripped,
    }
    for tier in ("fast", "std"):
        v = nums(f"{tier}_max")
        if v:
            vs = sorted(v)
            s[f"{tier}_n"] = len(vs)
            s[f"{tier}_mean"] = round(st.mean(vs), 2)
            s[f"{tier}_median"] = st.median(vs)
            s[f"{tier}_p90"] = vs[min(len(vs) - 1, int(0.9 * (len(vs) - 1)))]
            s[f"{tier}_min"] = vs[0]
            s[f"{tier}_max"] = vs[-1]
        else:
            s[f"{tier}_n"] = 0
    return s


def append_run(root: str, summary: dict):
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "runs.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, sort_keys=True) + "\n")


def read_runs(root: str):
    path = os.path.join(root, "runs.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out
