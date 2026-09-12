#!/usr/bin/env python3
"""tatbench CLI.

    python3 run.py --preflight                  # check everything, hit nothing hard
    python3 run.py --brands savana --limit 50   # smoke test
    python3 run.py                              # all enabled brands, full sweep
    python3 run.py --brands savana --date 2026-09-11   # resume a specific run

Stdlib only. Designed to be driven by cron or a systemd timer; see deploy/.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

from adapters import get as get_adapter
from engine import (Limiter, append_run, load_pincodes, run_brand, today_ist)

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(f"{datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def load_config(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def preflight(brand, cfg, pincodes_path):
    """Cheap checks before committing to hours of requests.

    A scheduled sweep that silently produces an empty file is worse than one
    that refuses to start, so this runs first and fails loudly.
    """
    problems, notes = [], []

    try:
        pins = load_pincodes(pincodes_path)
        notes.append(f"pincodes: {len(pins)}")
        if len(pins) < 100:
            problems.append(f"only {len(pins)} pincodes - is the list truncated?")
    except Exception as exc:
        problems.append(f"cannot read pincode list: {exc}")
        return problems, notes

    try:
        adapter = get_adapter(brand)
    except SystemExit as exc:
        return [str(exc)], notes

    rps = min(float(cfg.get("rps", 2.0)), adapter.max_rps)
    if float(cfg.get("rps", 2.0)) > adapter.max_rps:
        notes.append(f"rps lowered to adapter ceiling {adapter.max_rps}")
    notes.append(f"rps={rps} workers={cfg.get('workers', 4)} "
                 f"ETA ~{len(pins)/max(rps,0.1)/3600:.1f} h")

    # One live request against a known-good metro pincode.
    probe = str(cfg.get("probe_pincode", "110001"))
    lim = Limiter(rps)
    try:
        url, headers, method, body = adapter.build(str(cfg["sku"]), probe)
        lim.wait()
        req = urllib.request.Request(url, headers=headers, method=method, data=body)
        with urllib.request.urlopen(req, timeout=20) as resp:
            status, text = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        problems.append(f"probe {probe} -> HTTP {exc.code}")
        return problems, notes
    except Exception as exc:
        problems.append(f"probe {probe} failed: {type(exc).__name__}: {exc}")
        return problems, notes

    try:
        parsed = adapter.parse(status, text, today_ist())
    except Exception as exc:
        problems.append(f"probe parsed badly: {exc}")
        return problems, notes

    tiers = [t for t in ("fast", "std")
             if parsed.get(t) and parsed[t][0] is not None]
    notes.append(f"probe {probe} -> {parsed.get('city') or '?'} "
                 f"serviceable={parsed.get('serviceable')} tiers={tiers or 'NONE'}")

    if not tiers:
        problems.append(
            f"sku {cfg['sku']} returned no delivery tier at {probe}. "
            f"Tier eligibility is configured per-SKU and changes without notice "
            f"- pick a different sku before sweeping.")
    elif cfg.get("require_both_tiers", True) and len(tiers) < 2:
        problems.append(
            f"sku {cfg['sku']} offers only {tiers} at {probe}. Sweeping it means "
            f"tier and product are confounded - a Fast-vs-Standard comparison "
            f"built on it is comparing two different products. Pick a sku that "
            f"returns both, or set require_both_tiers=false to accept this.")

    return problems, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--brands", help="comma-separated; default = all enabled")
    ap.add_argument("--pincodes", help="override the configured pincode CSV")
    ap.add_argument("--root", help="override the configured data root")
    ap.add_argument("--date", help="run date / resume key, YYYY-MM-DD")
    ap.add_argument("--limit", type=int, default=0, help="first N pincodes only")
    ap.add_argument("--preflight", action="store_true",
                    help="run the checks and exit without sweeping")
    ap.add_argument("--stagger", type=int, default=0,
                    help="seconds to wait between brands")
    a = ap.parse_args()

    cfg = load_config(a.config)
    root = a.root or os.path.join(HERE, cfg.get("data_root", "data"))
    pincodes = a.pincodes or os.path.join(HERE, cfg["pincodes"])
    run_date = (datetime.strptime(a.date, "%Y-%m-%d").date() if a.date
                else today_ist())

    brands = ([b.strip() for b in a.brands.split(",") if b.strip()] if a.brands
              else [b for b, c in cfg["brands"].items() if c.get("enabled", True)])
    if not brands:
        raise SystemExit("no brands selected (all disabled in config?)")

    log(f"brands: {', '.join(brands)}   run_date={run_date}   root={root}")

    # ---- preflight everything first -------------------------------------
    blocked = []
    for b in brands:
        if b not in cfg["brands"]:
            blocked.append((b, [f"no config block for '{b}'"]))
            continue
        problems, notes = preflight(b, cfg["brands"][b], pincodes)
        for n in notes:
            log(f"  [{b}] {n}")
        for p in problems:
            log(f"  [{b}] PROBLEM: {p}")
        if problems:
            blocked.append((b, problems))

    if a.preflight:
        log("preflight only - exiting")
        return 1 if blocked else 0

    runnable = [b for b in brands if b not in {x for x, _ in blocked}]
    if blocked:
        log(f"skipping {len(blocked)} brand(s) that failed preflight: "
            f"{', '.join(x for x, _ in blocked)}")
    if not runnable:
        log("nothing runnable - fix the problems above")
        return 1

    stop = threading.Event()
    failed = 0
    for i, b in enumerate(runnable):
        if i and a.stagger:
            log(f"stagger: sleeping {a.stagger}s")
            time.sleep(a.stagger)
        try:
            summary = run_brand(b, cfg["brands"][b], pincodes, root, run_date,
                                limit=a.limit, stop=threading.Event(), log=log)
            append_run(root, summary)
            if summary.get("breaker_tripped"):
                failed += 1
        except KeyboardInterrupt:
            log("interrupted - progress is saved, rerun the same command to resume")
            return 130
        except Exception as exc:
            log(f"[{b}] RUN FAILED: {type(exc).__name__}: {exc}")
            failed += 1

    log("done" if not failed else f"done with {failed} brand(s) in trouble")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
