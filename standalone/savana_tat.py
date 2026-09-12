#!/usr/bin/env python3
"""savana_tat.py - sweep Savana's delivery-promise TAT across Indian pincodes.

Stdlib only. Resumable: rerun the same command to continue and retry errors.
  python3 savana_tat.py --spu 2345362 --pincodes pincodes.csv --limit 50   # smoke test
  python3 savana_tat.py --spu 2345362 --pincodes pincodes.csv              # full sweep
"""
import argparse, csv, json, os, random, re, signal, sys, threading, time
import urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

EP = "https://api-shop-in.savana.com/n/api/intention/item/v4/deliveryInfo"
IST = timezone(timedelta(hours=5, minutes=30))
MON = {m: i + 1 for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
DATE_RE = re.compile(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)")
COLS = ["pincode", "city_in", "state", "city_api", "serviceable",
        "fast_min", "fast_max", "std_min", "std_max", "error"]
STOP = threading.Event()


class Limiter:
    """Global token bucket - caps total requests/sec across all worker threads."""
    def __init__(self, rps):
        self.gap = 1.0 / rps if rps > 0 else 0.0
        self.lock = threading.Lock()
        self.nxt = time.monotonic()

    def wait(self):
        if not self.gap:
            return
        with self.lock:
            now = time.monotonic()
            self.nxt = max(self.nxt, now) + self.gap
            delay = self.nxt - self.gap - now
        if delay > 0:
            time.sleep(delay)


def offsets(text, base):
    """'Estimated delivery by Fri, 11 Sep - Sun, 13 Sep' -> (4, 6) days from base."""
    hits = DATE_RE.findall(text or "")
    if not hits:
        return None, None
    days = []
    for d, mon in hits[:2]:
        m = MON[mon]
        y = base.year + 1 if m < base.month else base.year   # handles Dec->Jan wrap
        try:
            days.append((date(y, m, int(d)) - base).days)
        except ValueError:
            pass
    if not days:
        return None, None
    return min(days), max(days)


def parse(body, base):
    row = {c: "" for c in COLS}
    if "__err" in body:
        row["error"] = body["__err"]
        return row
    d = body.get("data") or {}
    row["city_api"] = d.get("cityName") or ""
    av = d.get("isAvailable")
    row["serviceable"] = "Yes" if av == 1 else ("No" if av == 0 else "")
    for group in d.get("shippingInfo") or []:
        for line in group or []:
            tier = (line or {}).get("deliveryType")
            if tier not in ("fast", "standard"):
                continue          # a tier is ABSENT when not offered - that is signal
            lo, hi = offsets((line or {}).get("content", ""), base)
            p = "fast" if tier == "fast" else "std"
            row[p + "_min"] = "" if lo is None else lo
            row[p + "_max"] = "" if hi is None else hi
    return row


def fetch(spu, pin, lim, timeout, retries):
    err = "unknown"
    for a in range(retries + 1):
        if STOP.is_set():
            return {"__err": "aborted"}
        lim.wait()
        req = urllib.request.Request(
            f"{EP}?spuId={spu}&pinCode={pin}",
            headers={"Accept": "application/json",
                     "User-Agent": "savana-tat-sweep/1.0 (internal ops analytics)"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}"
            if e.code != 429 and 400 <= e.code < 500:
                return {"__err": err}        # won't fix itself, stop retrying
        except Exception as e:
            err = type(e).__name__
        if a < retries:
            time.sleep(min(30.0, 1.5 * 2 ** a) * (0.6 + 0.8 * random.random()))
    return {"__err": err}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spu", required=True, help="product id; MUST offer both tiers")
    ap.add_argument("--pincodes", required=True, help="CSV with pincode[,city,state]")
    ap.add_argument("--out")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--rps", type=float, default=8.0)
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--base-date", help="day 0, YYYY-MM-DD (default: today IST)")
    a = ap.parse_args()

    base = (datetime.strptime(a.base_date, "%Y-%m-%d").date()
            if a.base_date else datetime.now(IST).date())
    out = a.out or f"tat_{a.spu}_{base:%Y%m%d}.csv"

    with open(a.pincodes, newline="", encoding="utf-8") as fh:
        src = [r for r in csv.DictReader(fh) if (r.get("pincode") or "").strip()]

    done = set()
    if os.path.exists(out):
        with open(out, newline="", encoding="utf-8") as fh:
            done = {r["pincode"] for r in csv.DictReader(fh)
                    if r.get("pincode") and not (r.get("error") or "").strip()}

    todo = [r for r in src if r["pincode"].strip() not in done]
    if a.limit:
        todo = todo[:a.limit]
    print(f"spu={a.spu} base={base} total={len(src)} done={len(done)} "
          f"this run={len(todo)} -> {out}")
    print(f"{a.workers} workers, {a.rps} req/s, ETA ~{len(todo)/max(a.rps,.1)/60:.0f} min")
    if not todo:
        print("nothing to do")
        return 0

    signal.signal(signal.SIGINT, lambda *_: (
        STOP.set(), print("\ninterrupted - progress saved, rerun to resume")))

    lim, fresh, n, errs = Limiter(a.rps), not os.path.exists(out), 0, 0
    t0, lock = time.monotonic(), threading.Lock()

    with open(out, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        if fresh:
            w.writeheader()

        def job(rec):
            row = parse(fetch(a.spu, rec["pincode"].strip(), lim, a.timeout,
                              a.retries), base)
            row["pincode"] = rec["pincode"].strip()
            row["city_in"] = (rec.get("city") or "").strip()
            row["state"] = (rec.get("state") or "").strip()
            return row

        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            for f in as_completed({pool.submit(job, r) for r in todo}):
                row = f.result()
                with lock:
                    w.writerow(row)
                    n += 1
                    errs += bool(row["error"])
                    if n % 250 == 0:
                        fh.flush()
                        rate = n / max(time.monotonic() - t0, 1e-3)
                        print(f"  {n}/{len(todo)}  {rate:.1f}/s  errors={errs}  "
                              f"~{(len(todo)-n)/max(rate,1e-3)/60:.0f} min left")

    print(f"\nwrote {n} rows to {out} ({errs} errored)")
    if errs:
        print("rerun the same command - errored pincodes retry automatically")
    return 0


if __name__ == "__main__":
    sys.exit(main())
