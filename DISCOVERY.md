# Finding a storefront's delivery endpoint

How the Savana endpoint was found, written up so the same ten minutes of work
gets you Myntra, Nykaa Fashion, Ajio and Meesho.

Do this by hand, in your own browser, as yourself. Do not automate the
discovery step.

---

## The recipe

**1. Open a product page** and find the pincode / "check delivery" box.

**2. Open DevTools → Network**, filter to **Fetch/XHR**, and clear it.

**3. Type a pincode and submit.** Usually one request appears. If several do,
the one you want returns a date or a day-count, not a price or a cart.

**4. If nothing appears**, the page is wrapping `fetch`/`XHR` for analytics and
DevTools may be showing you a proxied copy — or the quote is coming from the
page's initial payload rather than a live call. Savana did this. Paste into the
console *before* submitting:

```js
window.__cap = [];
(() => {
  const of = window.fetch;
  window.fetch = function (...a) {
    window.__cap.push({ u: (typeof a[0] === 'string' ? a[0] : a[0]?.url), m: a[1]?.method || 'GET' });
    return of.apply(this, a);
  };
  const oo = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (m, u, ...r) {
    window.__cap.push({ u, m }); return oo.call(this, m, u, ...r);
  };
})();
// ...now submit the pincode, then:
window.__cap.map(r => r.m + ' ' + String(r.u).split('?')[0]);
```

Ignore anything matching `analytics|clarity|collect|tracking|gtm|aliyuncs|yandex`.
What remains is usually one line.

**5. Right-click the request → Copy → Copy as cURL.** That is your contract.

**6. Strip it to the bone.** This is the step that decides whether the sweep is
easy or miserable. Remove every header and cookie, then add back only what is
required to still get a 200:

```bash
curl -s 'https://api.example.com/serviceability?itemId=123&pincode=110001' | head -c 400
```

- **Works bare** → ideal. A plain GET loop. Savana is this case.
- **Needs a static header** (an API key baked into the JS bundle, an app-id) →
  fine, put it in `build()`.
- **Needs a session cookie or a bearer token** → read the next section before
  going further.

**7. Check serviceability handling.** Try a pincode that is definitely not
served (`744301`, Andaman interior, is a good probe) and note how "cannot
deliver" is expressed. It is often a different shape entirely, not just a
missing date.

**8. Check both tiers.** Try several SKUs. Express/fast tiers are frequently
per-SKU and per-pincode. Note which SKU gives you both — and re-check it on
every run, because this changes silently. On Savana, two SKUs swapped tiers
between June and September 2026.

---

## Session-bound endpoints

If the endpoint needs a token, you have three options, in order of preference:

1. **Look for an unauthenticated variant.** Many sites have a public
   serviceability check used by the PDP before login. Try the mobile-web
   origin (`m.example.com`) and the app API host — they are often laxer.
2. **Mint a token once per run** in `build()`'s setup and refresh on a 401. Be
   aware a token that expires mid-sweep produces thousands of silent failures;
   the circuit breaker will catch it, but only after 200 wasted requests.
3. **Don't.** If it needs a logged-in session, you are benchmarking with an
   account, which changes the ToS picture substantially. Escalate rather than
   engineer around it.

**Do not** solve bot protection by spoofing browser fingerprints, rotating
residential proxies, or replaying captured cookies. That converts a defensible
competitive benchmark into something else, and it is not what this tool is for.
If a brand cannot be swept honestly, record that as the finding.

---

## Expected difficulty

Ordered by what you will likely hit. Nothing here is a claim about a specific
site's current protection — verify for yourself.

| Brand | Expect |
|---|---|
| **Nykaa Fashion** | Usually the most tractable. Remember Nykaa Beauty and Nykaa Fashion are separate storefronts with different serviceability — sweep the one matching your category. |
| **Meesho** | Value-fashion and COD-heavy tier-2/3, so the most directly comparable to where Savana is weakest. |
| **Ajio** | Reliance-owned, strong tier-2/3 footprint. The most interesting comparison for the regions where Savana is slowest. |
| **Myntra** | Likely hardest. Strong bot protection; may be session-bound. Try the mobile-web origin first. If it resists honest access, say so and move on. |

---

## Before you enable a brand

- [ ] Endpoint returns 200 with no cookies and no auth
- [ ] `parse()` raises `ParseError` on anything unrecognised — never a silent zero
- [ ] Unserviceable pincode handled and verified
- [ ] Tested against ≥20 pincodes across zones, including one unserviceable
- [ ] `max_rps` ≤ 2 in the adapter
- [ ] `/robots.txt` and ToS reviewed
- [ ] Sign-off recorded in README section 6

Then register it in `adapters/__init__.py`, fill in its `sku` in `config.json`,
and run `python3 run.py --preflight --brands <name>` before enabling it.
