# Delivery TAT Benchmark — start here

You've been handed a tool that measures **how long Savana tells a customer their
order will take**, for every pincode in India, and tracks whether that changes
over time. It's built to cover competitor storefronts too.

Read this page. It's the only one you need to get running. Everything else is
reference.

---

## 1. What this actually measures

When a customer puts their pincode into a product page, the site quotes them a
delivery window — "arrives 17–21 Sep". This tool asks that same question 19,924
times, once per pincode, and records every answer.

**It measures the promise, not the delivery.** Whether parcels actually arrive on
time is a different dataset (courier side). Don't let anyone read these numbers
as delivery performance — they're what we're *telling customers*, which is its
own thing worth watching, because it drives conversion and it drives WISMO
tickets when it's wrong.

**Why anyone cares:** between 11 June and 11 September 2026, the share of India
promised Fast delivery within 7 days fell from **89% to 18%**. Nobody noticed for
three months, because nobody was comparing one week to the next. That's the job
this tool does.

---

## 2. Setup — about 15 minutes

You need a Linux box (or a Mac) with Python 3.8+. Nothing to install, no
dependencies, no API keys.

```bash
unzip savana-tat-benchmark.zip
sudo mv savana-tat-benchmark /opt/tatbench
cd /opt/tatbench

# 1. Check everything works without hitting anything hard
python3 run.py --preflight

# 2. Small real test — 50 pincodes, about 10 seconds
python3 run.py --brands savana --limit 50

# 3. Look at what came back
head -5 data/savana/*/rows.csv
```

If preflight passes and those 50 rows have numbers in `fast_max` and `std_max`,
you're done setting up.

**If preflight fails**, it tells you why in plain English. The two common causes
are the SKU no longer offering both delivery tiers (fix: pick another, see §6)
and no network route to the endpoint (fix: check the box's egress).

---

## 3. Running it

```bash
python3 run.py --brands savana          # full sweep, ~42 minutes
python3 analyse.py --detail             # read the results
```

**It's resumable.** If it dies — dropped connection, you hit Ctrl-C, the box
rebooted — just run the exact same command again. Finished pincodes are skipped,
failed ones are retried. You cannot corrupt a run by re-running it.

**To put it on a schedule**, `deploy/crontab.example` is ready to paste into
`crontab -e`. It runs one brand per night with a resume pass each morning and a
report on Saturday. `deploy/systemd.md` is the better option on a real server.

---

## 4. Reading the output

`python3 analyse.py --detail` prints three sections.

**Where each brand stands** — a table of median and p90 days. These are days to
the *promised-by* date, i.e. the worst-case date the customer sees. That's
deliberate: it's the number they'll hold us to.

**Movement since the previous run** — the important one. It flags any brand whose
mean TAT moved ≥0.75 days or whose serviceability moved ≥5 percentage points
since last time, with a ⚠️. **If you read only one thing each week, read this.**

**Detail** — coverage curves, regional breakdown, and two checks worth
understanding:

*Distinct delivery windows.* If 19,924 pincodes only produce 28 different
answers, the promise engine is a coarse zone lookup, not a real per-pincode
model. Rising numbers mean it's getting more granular.

*Tier offset.* For every pincode offering both tiers, it subtracts Fast from
Standard. If the answer is the same number every single time, the two tiers
aren't two services — they're one transit table with a fixed number added. In
June, Standard was Fast + 6 days on **all 19,923 pincodes with zero exceptions**.
That meant every Fast-vs-Standard chart anyone had built was comparing a
distribution against a shifted copy of itself. By September the offset had
started to vary, which is the direction we want. The analysis prints a verdict —
read it before anyone quotes a tier comparison.

---

## 5. Your weekly rhythm

Once it's scheduled, this is a ten-minute job:

1. **Saturday morning:** open the week's report in `reports/`.
2. **Scan the movement section** for ⚠️ markers.
3. **If something moved**, check the detail section for which regions drove it,
   then raise it. A national mean moving a full day is not noise.
4. **If a run shows a ⚠️ next to the brand name**, the circuit breaker tripped —
   that run was aborted partway and its numbers cover only part of the country.
   Don't quote them. See §7.

---

## 6. Two things that will bite you

**The SKU changes its delivery tiers without warning.** Tier eligibility is set
per product, and it moves. Two SKUs we were using swapped tiers entirely between
June and September. Preflight re-checks this every run, so you'll be told — but
if it complains, find a replacement by trying a few product IDs:

```bash
for SPU in 2345362 2306342 2127792; do
  echo -n "$SPU: "
  curl -s "https://api-shop-in.savana.com/n/api/intention/item/v4/deliveryInfo?spuId=$SPU&pinCode=110001" \
  | python3 -c "import sys,json;d=json.load(sys.stdin).get('data',{});print([l['deliveryType'] for g in d.get('shippingInfo',[]) for l in g if l.get('deliveryType')])"
done
```

Pick one that prints both `fast` and `standard`, and put it in `config.json`.
Product IDs are the 7-digit number at the end of any product URL:
`savana.com/details/some-dress-2345362`.

**Never compare two different products across tiers.** The first version of this
analysis measured Fast on one dress and Standard on another, which made "tier"
and "product" impossible to separate — the finding looked real and wasn't. That's
why preflight refuses a SKU that only offers one tier unless you explicitly
override it.

---

## 7. When it breaks

| What you see | What it means | Do this |
|---|---|---|
| Preflight: "returned no delivery tier" | SKU lost its tiers | Pick a new SKU — §6 |
| Preflight: "offers only ['std']" | SKU is single-tier | New SKU, or set `require_both_tiers: false` if you accept the limitation |
| **CIRCUIT BREAKER TRIPPED** | ≥35% of requests failing — almost always rate-limiting or a block | **Don't just re-run.** Look at the `error` column in the CSV. A wall of 429s means slow down (halve `rps`). A wall of 403s means we're blocked — stop and escalate |
| Lots of `parse:` errors | The response shape changed | The endpoint was redesigned. Compare a live response against `adapters/savana.py` |
| Run just stops | Connection died | Re-run the same command; it resumes |

The golden rule on the breaker: it exists because a sweep that's already failing
will not fix itself, and hammering a system that's blocking you makes it worse.
Treat it as a stop sign, not a speed bump.

---

## 8. Adding competitor brands — read before you start

The tool is built for Myntra, Nykaa Fashion, Ajio and Meesho, but **their
adapters are deliberately not written**, and they're disabled in config.

Two reasons, and please don't route around either:

**It's a legal and commercial decision, not a technical one.** A weekly full
sweep of four competitors is ~80,000 requests a week into other companies'
production systems. Check each site's robots.txt and Terms of Service, and get
someone senior to agree in writing. There's a sign-off table in `README.md`
section 6 — fill it in before enabling anything.

**Find the endpoints honestly.** `DISCOVERY.md` walks through doing it by hand in
your own browser with DevTools; it takes about ten minutes per site and it's the
exact method used for Savana. What's out of bounds is defeating bot protection —
no spoofed browser fingerprints, no rotating proxies, no replayed session
cookies. If a site can't be measured honestly, write that down as the finding and
move on. Myntra is the most likely to land there.

**If legal is uncomfortable with full sweeps, there's a good answer:** a sample
of ~750 pincodes stratified by state gives statistically equivalent national and
regional comparisons at about 4% of the request volume. Given the whole country
only produces a few dozen distinct delivery windows, you lose almost nothing.

---

## 9. What's in the box

```
START-HERE.md          this page
README.md              technical runbook — architecture, config, scheduling
DISCOVERY.md           how to find a storefront's delivery endpoint
BASELINES.md           what we already know; compare new runs against it

run.py                 the CLI
engine.py              rate limiting, retries, circuit breaker, resume
analyse.py             reporting and trend detection
adapters/              one file per brand (savana.py is the worked example)
config.json            SKUs, rates, thresholds
deploy/                cron and systemd setup
pincodes_tms_master.csv  19,924 pincodes with city and state

baseline/              the September sample data, for reference
standalone/            a single-file version if you just want a quick one-off
```

---

## 10. Open questions for whoever owns this

Worth resolving early rather than inheriting:

1. **Is there an internal serviceability or TMS endpoint?** If so, point the
   Savana adapter at it — same data, no load on the production storefront,
   probably a bulk interface. This is the single biggest improvement available.
2. **Does the gateway team know this runs?** A 40-minute burst of
   identically-shaped GETs every week is exactly what a WAF rule is written to
   catch. Better to be expected than to be throttled and debug it blind.
3. **Promise vs actual.** This measures what we say. Joining it against actual
   delivered TAT from the courier data is the analysis that would genuinely
   change decisions — and this tool produces one half of it.

Questions on any of the above: ask Ashutosh.
