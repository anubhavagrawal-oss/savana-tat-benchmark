#!/usr/bin/env python3
"""
build_dashboard_data.py
Aggregates the latest Savana sweep (data/savana/<date>/rows.csv) into
docs/data.json + docs/history.json for the GitHub Pages dashboard.

rows.csv columns (as actually produced by run.py):
    pincode, city_in, state, city_api, serviceable, fast_min, fast_max,
    std_min, std_max, error

city_in/state come from pincodes_tms_master.csv (joined at sweep time),
city_api is what the live endpoint returned for that pincode. We group by
city_in/state since that's the stable reference; city_api is kept in the
per-pincode data for anyone who wants to check for mismatches later.

Run from the repo root:
    python3 build_dashboard_data.py

Safe to re-run: always picks the most recently modified rows.csv under data/.
"""
import csv
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DATA_DIR = REPO_ROOT / "data"
DOCS_DIR = REPO_ROOT / "docs"
OUT_FILE = DOCS_DIR / "data.json"
HISTORY_FILE = DOCS_DIR / "history.json"


def find_latest_rows_csv():
    candidates = sorted(
        DATA_DIR.glob("*/*/rows.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise SystemExit(f"No rows.csv found under {DATA_DIR} — run a sweep first.")
    return candidates[0]


def to_float(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def mean(vals):
    vals = [v for v in vals if v is not None]
    return round(statistics.mean(vals), 2) if vals else None


def pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else 0.0


def summarize(rows):
    """rows: list of parsed dicts. Splits out errored rows before computing
    serviceability and tier means, so a network/parse error never gets
    silently counted as 'not serviceable'."""
    total = len(rows)
    valid = [r for r in rows if not r["error"]]
    n_valid = len(valid)
    serviceable = [r for r in valid if r["serviceable"]]
    n_serviceable = len(serviceable)

    return {
        "pincode_count": total,
        "error_count": total - n_valid,
        "serviceable_count": n_serviceable,
        "serviceability_pct": pct(n_serviceable, n_valid),
        "fast_mean_latest": mean([r["fast_max"] for r in serviceable]),
        "fast_mean_earliest": mean([r["fast_min"] for r in serviceable]),
        "std_mean_latest": mean([r["std_max"] for r in serviceable]),
        "std_mean_earliest": mean([r["std_min"] for r in serviceable]),
        "fast_within_7d_pct": pct(
            len([r for r in serviceable if r["fast_max"] is not None and r["fast_max"] <= 7]),
            n_serviceable,
        ),
        "both_tiers_pct": pct(
            len([r for r in serviceable if r["fast_max"] is not None and r["std_max"] is not None]),
            n_serviceable,
        ),
    }


def main():
    rows_csv = find_latest_rows_csv()
    run_dir = rows_csv.parent
    print(f"Using: {rows_csv}")

    all_rows = []
    by_state = {}
    by_city = {}

    with open(rows_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            city = (raw.get("city_in") or raw.get("city_api") or "Unknown").strip() or "Unknown"
            state = (raw.get("state") or "Unknown").strip() or "Unknown"
            row = {
                "pincode": str(raw.get("pincode", "")).strip(),
                "city": city,
                "state": state,
                "serviceable": (raw.get("serviceable") or "").strip().lower() == "yes",
                "error": (raw.get("error") or "").strip(),
                "fast_min": to_float(raw.get("fast_min")),
                "fast_max": to_float(raw.get("fast_max")),
                "std_min": to_float(raw.get("std_min")),
                "std_max": to_float(raw.get("std_max")),
            }
            all_rows.append(row)
            by_state.setdefault(state, []).append(row)
            by_city.setdefault((city, state), []).append(row)

    national = summarize(all_rows)

    state_table = []
    for state, rows in by_state.items():
        s = summarize(rows)
        s["state"] = state
        state_table.append(s)
    state_table.sort(key=lambda s: (s["fast_mean_latest"] is None, s["fast_mean_latest"] or 0), reverse=True)

    city_table = []
    for (city, state), rows in by_city.items():
        s = summarize(rows)
        s["city"] = city
        s["state"] = state
        city_table.append(s)
    city_table.sort(key=lambda s: (s["fast_mean_latest"] is None, s["fast_mean_latest"] or 0), reverse=True)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    output = {
        "generated_at": generated_at,
        "source_run": str(run_dir.relative_to(REPO_ROOT)),
        "national": national,
        "by_state": state_table,
        "by_city": city_table,
    }

    DOCS_DIR.mkdir(exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"Wrote {OUT_FILE} — {len(state_table)} states, {len(city_table)} cities.")

    history = []
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)
    history.append({
        "date": generated_at,
        "fast_mean_latest": national["fast_mean_latest"],
        "std_mean_latest": national["std_mean_latest"],
        "fast_within_7d_pct": national["fast_within_7d_pct"],
        "serviceability_pct": national["serviceability_pct"],
    })
    history = history[-104:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)
    print(f"Appended to {HISTORY_FILE} — {len(history)} points total.")


if __name__ == "__main__":
    main()
