# tatbench — multi-brand delivery-TAT benchmark

Sweeps what a storefront **promises** a customer, per pincode, and tracks how it
moves week over week. Built for Savana; designed so competitor brands plug in as
adapters.

Python 3.8+, standard library only. Nothing to install.

---

## 1. Status

| Brand | Adapter | Enabled | Notes |
|---|---|---|---|
| **savana** | ✅ built & verified | yes | Endpoint contract verified live 7 & 11 Sep 2026 |
| myntra | ❌ stub | no | See DISCOVERY.md |
| nykaafashion | ❌ stub | no | Nykaa Beauty ≠ Nykaa Fashion |
| ajio | ❌ stub | no | |
| meesho | ❌ stub | no | |

The competitor adapters are deliberately not written. See section 6.

---

## 2. Quick start

```bash
cd /opt/tatbench
python3 run.py --preflight                    # check without sweeping
python3 run.py --brands savana --limit 50     # smoke test
python3 run.py --brands savana                # full 19,924 (~42 min)
python3 analyse.py --detail
```

Resume anything interrupted by rerunning the identical command. Completed
pincodes are skipped; failures are retried.

---

## 3. How it is put together

```
run.py             CLI + preflight
engine.py          rate limiting, retries, circuit breaker, resume, storage
analyse.py         per-run detail, cross-brand table, run-over-run trend
adapters/
  base.py          Adapter contract + parse_window (handles every date shape)
  savana.py        verified
  _template.py     copy this for a new brand
config.json        per-brand sku, rate, thresholds
data/
  runs.jsonl                      one summary line per brand-run
  <brand>/<YYYY-MM-DD>/rows.csv   raw rows
deploy/            cron + systemd
DISCOVERY.md       how to find a brand's endpoint
```

Adapters only know how to shape a request and parse a response. Everything
operational lives in the engine, so onboarding a brand is ~40 lines.

### Four things the engine does that matter

**Circuit breaker.** If ≥35% of requests fail after the first 200, the run
aborts. A sweep that has started getting 403s is being blocked; continuing
wastes hours, produces a useless file, and deepens the block. Abandoned rows are
not written, so a resume picks up cleanly.

**Adapter rate ceiling.** Each adapter declares `max_rps`. Config can lower it,
never raise it. Someone editing config at 2am cannot accidentally point 50 rps
at a competitor.

**ParseError, not silent zeros.** An unrecognised response is recorded as an
error and retried next run. If a brand changes its response shape, you get
failures — not a TAT that quietly reads as zero and drags the mean down.

**Resume-aware summaries.** A resumed run appends retry rows next to the
originals; both `engine.summarise` and `analyse.load_rows` collapse to one row
per pincode, preferring the success. Without this every resume reports inflated
errors and a wrong serviceability denominator.

---

## 4. Runtime and scheduling

| Brand | rps | 19,924 pincodes |
|---|---|---|
| savana | 8 | ~42 min |
| each competitor | 2 | ~2 h 46 min |
| **all five in one run** | | **~11.5 h** |

An 11-hour job gets killed halfway and makes every brand's numbers a different
age. `deploy/crontab.example` runs **one brand per night**, weekly, with a 06:00
resume pass and a Saturday report. Each night stays under three hours and a
blocked competitor never delays the Savana numbers you depend on.

`deploy/systemd.md` is the better option on a real server — `Persistent=true`
reruns a sweep missed while the box was down, which cron will not.

---

## 5. Reading the output

`analyse.py` gives three things:

1. **Cross-brand table** — median and p90 days to the *promised-by* date, the
   worst-case date the customer is actually shown. A run that tripped its
   breaker is flagged; its numbers are partial and must not be quoted as
   national.
2. **Movement since the previous run** — flags a mean TAT move ≥0.75d or a
   serviceability move ≥5pp. This is the reason to schedule it. Savana's Fast
   tier went from 89% within 7 days (11 Jun) to 18% (11 Sep); nobody noticed,
   because nobody was diffing runs.
3. **`--detail`** — coverage curves, lane granularity, region breakdown, and the
   tier-offset test.

### The tier-offset test

For every pincode quoting both tiers, it counts `std_max − fast_max`.

- **One value only** → the tiers are one transit matrix with a flat pad, not two
  independently modelled services. Any Fast-vs-Standard comparison is a
  distribution against a shifted copy of itself. On 11 Jun 2026 Savana was
  `+6` on all 19,923 pincodes, zero exceptions.
- **A spread** → the tiers genuinely differ by lane. On 11 Sep it was +6/74%,
  +7/17%, +5/9% — the flat constant had broken, which is the direction you want.

Also watch **distinct Fast windows**. Savana had 28 across 19,924 pincodes in
June (a coarse zone lookup) and 44 across just 425 in September (materially
finer).

---

## 6. Before you sweep a competitor — read this

Savana is yours. Myntra, Nykaa, Ajio and Meesho are not.

A weekly full sweep is **~80,000 requests per week into third-party production
systems**. That is a commercial and legal decision, not an engineering one:

- **Get sign-off.** Someone who is not the person running the script should
  agree, in writing, that this is acceptable. Check each site's `/robots.txt`
  and Terms of Service first. Record the decision here:

  | Brand | Reviewed by | Date | Decision |
  |---|---|---|---|
  | myntra | | | |
  | nykaafashion | | | |
  | ajio | | | |
  | meesho | | | |

- **Stay at 2 rps.** Nobody has regretted a slow sweep. Plenty have regretted an
  IP ban that also took out their office.
- **Send an honest User-Agent.** The adapters identify themselves. Keep it that
  way — spoofing a browser to evade bot detection is a different activity with a
  different risk profile.
- **If the breaker trips, stop and think.** Do not just rerun. Look at the error
  column: a wall of 403s means you are blocked, and hammering it again is the
  worst available response.
- **A stratified sample is the defensible alternative.** ~750 pincodes stratified
  by state gives statistically equivalent national and regional comparisons at
  ~4% of the volume. If legal is uncomfortable with full sweeps, this is the
  answer, and it costs almost nothing analytically — the lane-granularity
  numbers above show why.

---

## 7. Onboarding a brand

1. Read `DISCOVERY.md` and find the endpoint by hand in your own browser.
2. Copy `adapters/_template.py` → `adapters/<brand>.py`, fill in `build()` and
   `parse()`.
3. Register it in `adapters/__init__.py`.
4. Fill in `sku` in `config.json`, leave `enabled: false`.
5. `python3 run.py --preflight --brands <brand>`
6. `python3 run.py --brands <brand> --limit 50` and read the CSV by eye.
7. Record sign-off in section 6, then set `enabled: true`.

---

## 8. Caveats

- These are **promised** TATs, not delivered ones. Promise-vs-actual is a
  different and more useful CX analysis; this tool does not do it.
- Unauthenticated quotes are a clean baseline but are not necessarily what every
  customer sees — login state, city cookie and A/B bucket can all change it.
- Cross-brand comparisons are only fair if the SKUs are comparable. A lightweight
  accessory and a heavy coat may ship on different lanes. Note the SKU used.
- Pin `--base-date` if a sweep can span midnight IST, or one file mixes two
  day-zeros.
- Refresh the pincode list from TMS periodically; the bundled 19,924 dates from
  Jun 2026.
