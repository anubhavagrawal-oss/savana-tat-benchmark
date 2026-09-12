#!/usr/bin/env python3
"""tatbench analysis - per-run detail, cross-brand comparison, run-over-run trend.

    python3 analyse.py                      # latest run of every brand
    python3 analyse.py --date 2026-09-11
    python3 analyse.py --brand savana --detail
    python3 analyse.py --out report.md      # write the markdown report

Trend detection is the point of running this on a schedule: it compares each
brand's latest run against its previous one and flags material movement. A
regression like Savana's Fast tier going from 89% within 7 days to 18% is
obvious in the trend table and invisible in any single run.
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics as st
import sys
from collections import Counter, defaultdict

from engine import read_runs

HERE = os.path.dirname(os.path.abspath(__file__))

REGION = {
    "North": ["Delhi (UT)", "Haryana", "Punjab", "Himachal Pradesh",
              "Jammu & Kashmir (UT)", "Ladakh (UT)", "Chandigarh (UT)",
              "Uttarakhand", "Uttar Pradesh", "Rajasthan"],
    "West": ["Maharashtra", "Gujarat", "Goa", "DNH & Daman & Diu (UT)"],
    "South": ["Karnataka", "Kerala", "Tamil Nadu", "Telangana",
              "Andhra Pradesh", "Puducherry (UT)"],
    "East": ["West Bengal", "Bihar", "Jharkhand", "Odisha"],
    "Central": ["Madhya Pradesh", "Chhattisgarh"],
    "Northeast": ["Assam", "Arunachal Pradesh", "Manipur", "Meghalaya",
                  "Mizoram", "Nagaland", "Tripura", "Sikkim"],
    "Islands": ["Andaman & Nicobar (UT)", "Lakshadweep (UT)"],
}
S2R = {s: r for r, ss in REGION.items() for s in ss}

# A run-over-run move bigger than this is worth a human looking at it.
MOVE_DAYS = 0.75
MOVE_PCT = 5.0


def num(v):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


def load_rows(root, brand, run_date):
    """Rows for one brand-run, collapsed to one per pincode.

    A resumed run appends a retry row alongside the original failure, so the
    file can hold both. Prefer the success, exactly as engine.summarise does.
    """
    p = os.path.join(root, brand, run_date, "rows.csv")
    if not os.path.exists(p):
        return []
    with open(p, newline="", encoding="utf-8") as fh:
        raw = list(csv.DictReader(fh))
    best = {}
    for r in raw:
        pin = r.get("pincode") or ""
        good = not (r.get("error") or "").strip()
        if pin not in best or (good and not best[pin][0]):
            best[pin] = (good, r)
    return [r for _, r in best.values()]


def pct_within(vals, d):
    return 100 * sum(1 for v in vals if v <= d) / len(vals) if vals else 0.0


def brand_detail(brand, rows, out):
    ok = [r for r in rows if not (r.get("error") or "").strip()]
    serv = [r for r in ok if r.get("serviceable") == "Yes"]
    out.append(f"\n### {brand}\n")
    out.append(f"- rows {len(rows)}, usable {len(ok)}, errored {len(rows)-len(ok)}")
    if ok:
        out.append(f"- serviceable {len(serv)}/{len(ok)} "
                   f"({100*len(serv)/len(ok):.2f}%)")
    if rows and len(ok) < len(rows):
        top = Counter((r.get("error") or "")[:40] for r in rows
                      if (r.get("error") or "").strip()).most_common(3)
        out.append(f"- top errors: {', '.join(f'{k} x{v}' for k, v in top)}")
    if not serv:
        return

    for tier, label in (("fast", "Fast"), ("std", "Standard")):
        v = [x for x in (num(r.get(f"{tier}_max")) for r in serv) if x is not None]
        if not v:
            out.append(f"- {label}: not offered / not parsed")
            continue
        vs = sorted(v)
        p90 = vs[min(len(vs)-1, int(0.9*(len(vs)-1)))]
        out.append(f"- **{label}** (latest promise): mean {st.mean(vs):.2f}d, "
                   f"median {st.median(vs)}d, p90 {p90}d, range {vs[0]}-{vs[-1]}d, "
                   f"n={len(vs)}")
        cov = "  ".join(f"≤{d}d {pct_within(vs, d):.0f}%"
                        for d in (3, 5, 7, 10, 14, 17))
        out.append(f"  - coverage: {cov}")

    # lane granularity - few distinct windows means a coarse zone lookup
    pairs = Counter((num(r.get("fast_min")), num(r.get("fast_max")))
                    for r in serv if num(r.get("fast_max")) is not None)
    if pairs:
        out.append(f"- distinct Fast windows: **{len(pairs)}** across "
                   f"{sum(pairs.values())} pincodes "
                   f"(top: {pairs.most_common(1)[0][0]} x{pairs.most_common(1)[0][1]})")

    # the offset test
    both = [r for r in serv
            if num(r.get("fast_max")) is not None and num(r.get("std_max")) is not None]
    if both:
        dmax = Counter(num(r["std_max"]) - num(r["fast_max"]) for r in both)
        out.append(f"- tier offset (std−fast, latest), n={len(both)}: "
                   + ", ".join(f"+{k}: {100*v/len(both):.0f}%"
                               for k, v in sorted(dmax.items())))
        if len(dmax) == 1:
            out.append(f"  - **flat constant (+{next(iter(dmax))}d on every pincode).** "
                       f"The tiers are one transit matrix with a pad, not two "
                       f"independently modelled services. Treat any "
                       f"Fast-vs-Standard comparison as meaningless.")
        else:
            out.append("  - offset varies by lane, so the tiers genuinely differ.")

    # regions
    byr = defaultdict(list)
    for r in serv:
        v = num(r.get("fast_max")) or num(r.get("std_max"))
        if v is not None:
            byr[S2R.get(r.get("state", ""), "?")].append(v)
    if len(byr) > 1:
        line = "  ".join(f"{rg} {st.mean(v):.1f}d"
                         for rg, v in sorted(byr.items(), key=lambda kv: st.mean(kv[1])))
        out.append(f"- by region (fastest→slowest): {line}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(HERE, "data"))
    ap.add_argument("--date", help="run date; default = each brand's latest")
    ap.add_argument("--brand")
    ap.add_argument("--detail", action="store_true", help="per-brand deep dive")
    ap.add_argument("--out", help="write markdown here as well as stdout")
    a = ap.parse_args()

    runs = read_runs(a.root)
    if not runs:
        raise SystemExit(f"no runs found under {a.root} - has anything been swept?")

    by_brand = defaultdict(list)
    for r in runs:
        by_brand[r["brand"]].append(r)
    for v in by_brand.values():
        v.sort(key=lambda r: (r["run_date"], r.get("captured_at", "")))

    brands = [a.brand] if a.brand else sorted(by_brand)
    out = ["# TAT benchmark report", ""]

    # ---- cross-brand comparison -----------------------------------------
    out.append("## Where each brand stands\n")
    out.append("| Brand | Run | Serviceable | Fast median | Fast p90 | "
               "Std median | Std p90 | Errors |")
    out.append("|---|---|---|---|---|---|---|---|")
    latest = {}
    for b in brands:
        rs = [r for r in by_brand.get(b, [])
              if not a.date or r["run_date"] == a.date]
        if not rs:
            continue
        r = rs[-1]
        latest[b] = r
        fm = r.get("fast_median", "—") if r.get("fast_n") else "—"
        fp = r.get("fast_p90", "—") if r.get("fast_n") else "—"
        sm = r.get("std_median", "—") if r.get("std_n") else "—"
        sp = r.get("std_p90", "—") if r.get("std_n") else "—"
        flag = " ⚠️" if r.get("breaker_tripped") else ""
        out.append(f"| {b}{flag} | {r['run_date']} | "
                   f"{r.get('serviceable_pct', '—')}% | {fm} | {fp} | "
                   f"{sm} | {sp} | {r.get('errors', 0)} |")
    out.append("\nMedian/p90 are **days to the promised-by date** — the worst-case "
               "date the customer is actually shown.")

    if any(r.get("breaker_tripped") for r in latest.values()):
        out.append("\n⚠️ A run marked with a warning tripped its circuit breaker — "
                   "it was aborted mid-sweep and its numbers cover only part of "
                   "the country. Do not quote them as national.")

    # ---- trend ----------------------------------------------------------
    out.append("\n## Movement since the previous run\n")
    moved = False
    for b in brands:
        rs = by_brand.get(b, [])
        if len(rs) < 2:
            out.append(f"- **{b}**: only {len(rs)} run so far — no trend yet.")
            continue
        cur, prev = rs[-1], rs[-2]
        bits = []
        for tier, label in (("fast", "Fast"), ("std", "Std")):
            c, p = cur.get(f"{tier}_mean"), prev.get(f"{tier}_mean")
            if c is None or p is None:
                continue
            d = c - p
            mark = " ⚠️" if abs(d) >= MOVE_DAYS else ""
            if mark:
                moved = True
            bits.append(f"{label} {p:.2f}→{c:.2f}d ({d:+.2f}){mark}")
        cs, ps = cur.get("serviceable_pct"), prev.get("serviceable_pct")
        if cs is not None and ps is not None:
            d = cs - ps
            mark = " ⚠️" if abs(d) >= MOVE_PCT else ""
            if mark:
                moved = True
            bits.append(f"serviceable {ps:.1f}%→{cs:.1f}% ({d:+.1f}pp){mark}")
        out.append(f"- **{b}** ({prev['run_date']} → {cur['run_date']}): "
                   + "; ".join(bits or ["no comparable metrics"]))
    out.append(f"\nFlagged when mean TAT moves ≥{MOVE_DAYS}d or serviceability "
               f"≥{MOVE_PCT}pp.")
    if not moved:
        out.append("\nNothing moved materially this run.")

    # ---- per-brand detail -----------------------------------------------
    if a.detail:
        out.append("\n## Detail\n")
        for b in brands:
            r = latest.get(b)
            if r:
                brand_detail(b, load_rows(a.root, b, r["run_date"]), out)

    text = "\n".join(out)
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"\n[written to {a.out}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
