# Baselines — what we already know

Compare new runs against this. Two measurement points so far.

---

## Headline

| | 11 Jun 2026 | 11 Sep 2026 |
|---|---|---|
| Coverage | 19,924 pincodes (full) | 425 pincodes (partial sample) |
| Product | 2127792 Fast / 2247692 Std | 2345362 (both tiers) |
| **Fast, mean latest** | 6.6 d | **8.4 d** |
| Fast, median | 7 d | 9 d |
| **Fast within 7 days** | **89%** | **18%** |
| Fast, range | 3–11 d | 4–12 d |
| **Standard, mean latest** | 12.6 d | **14.5 d** |
| Standard, median | 13 d | 14 d |
| Serviceability | 19,923 / 19,924 (99.99%) | 425 / 425 |
| Distinct Fast windows | 28 across 19,924 | 44 across 425 |
| Tier offset (std − fast, latest) | **+6 on 100%** | +6 on 74%, +7 on 17%, +5 on 9% |

---

## The three findings that matter

### 1. Fast delivery degraded sharply over the quarter

Mean latest promise went 6.6 → 8.4 days. The number that actually tells the
story is the ≤7-day share: **89% → 18%**. Whatever "Fast" meant to a customer in
June, it doesn't mean it now — most of the country is being quoted 8–9 days on
the premium tier.

This was invisible for three months because nobody was diffing one run against
the previous one. That's precisely what `analyse.py`'s movement section exists
to catch.

**Caveat:** June measured Fast on SKU 2127792, September on 2345362. Some of the
gap could be SKU-level config. The June data showed TAT is essentially a pincode
lane lookup that barely varies by product, so this is probably real — but confirm
on a full same-SKU sweep before escalating.

### 2. Standard used to be Fast + a constant. That has broken, and it's good news.

On 11 June, across all 19,923 pincodes quoting both tiers:

```
std_max − fast_max = +6   on every single pincode, zero exceptions
std_min − fast_min = +5   on every single pincode, zero exceptions
```

That is not two delivery services. That is one transit matrix with a flat pad
added, which means every Fast-vs-Standard chart built on the June data was
comparing a distribution against a shifted copy of itself.

By 11 September the offset had spread — +6 on 74%, +7 on 17%, +5 on 9% — and
distinct delivery windows had gone from 28 across 19,924 pincodes to 44 across
just 425. Both point the same way: the promise engine has moved toward genuine
per-lane modelling.

**Re-test this every run.** `analyse.py --detail` prints the distribution and a
verdict. Don't assume it stays fixed.

### 3. Tier eligibility flips per-SKU without warning

| SKU | June | September |
|---|---|---|
| 2127792 Crossed A-Line Dress | fast only | standard only |
| 2247692 Tie-Up A-Line Dress | standard only | fast only |
| 2345362 Plus Size Crossed Top | — | both ✅ |
| 2306342 Lace Up A-Line Dress | — | both ✅ |

Both original SKUs completely inverted in three months. Nobody seems to own this
setting. Preflight re-checks it every run for exactly this reason.

---

## Regional picture (11 Sep, Fast latest)

| Region | Mean |
|---|---|
| North | 7.75 d |
| Central | 8.11 d |
| West | 8.68 d |
| South | 9.21 d |

Delhi is the standout at 5.60 d. Slowest states are all southern — Goa 9.40,
Andhra 9.24, Karnataka 9.22.

**Important caveat on the September figures:** that sample covers 19 of 35
states — North, West, Central and part of South. The missing regions (East,
Northeast, Islands) are the *slow* ones; in June the Northeast averaged 7.9 days
against a 6.6 national mean. **So every September number above is biased
optimistic.** The true picture is worse than 8.4 days. A full sweep will say by
how much.

---

## Reference data in `baseline/`

| File | What it is |
|---|---|
| `tat_sample_2345362_20260911.csv` | 425 pincodes, both tiers, with city/state/region joined |
| `calibration_60_20260911.csv` | 60-pincode spread used to validate the endpoint |

Both use base date 11 Sep 2026 — day counts are calendar days from that date.

---

## What a healthy run looks like

- Serviceability ≥ 99.9%
- Errors under ~0.5% of rows
- Circuit breaker not tripped
- Both tiers populated on the large majority of rows
- Movement vs previous run under 0.75 days

Anything outside that is worth ten minutes of attention before the numbers go
into a deck.
