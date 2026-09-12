"""TEMPLATE ADAPTER - copy to <brand>.py and fill in.

Read DISCOVERY.md first. It walks through finding a storefront's delivery
endpoint in DevTools in about ten minutes; this file is where the answer goes.

Checklist before you register a new brand
-----------------------------------------
1. The endpoint works with NO cookies and NO auth headers.
   Test it in a private window, or with curl and nothing else. If it needs a
   session token, stop and read the "session-bound endpoints" section of
   DISCOVERY.md before going further - a token that expires mid-sweep produces
   thousands of silent failures.
2. You have checked the site's /robots.txt and Terms of Service, and someone
   who is not you has agreed the sweep is acceptable. Competitor brands are
   third-party systems; this is a commercial and legal decision, not an
   engineering one.
3. max_rps is set conservatively. Start at 2. Nobody has ever regretted a slow
   sweep; plenty of people have regretted an IP ban.
4. parse() raises ParseError on anything it does not recognise, so a changed
   response shape shows up as failures rather than as a TAT of zero.
"""
from __future__ import annotations

import json
from datetime import date

from .base import Adapter, ParseError, parse_window


class BrandTemplate(Adapter):
    name = "brandname"          # must match the key in config.json
    max_rps = 2.0               # ceiling; config may lower, never raise

    def build(self, sku, pincode):
        """Return (url, headers, method, body).

        GET example:
            return (f"https://api.example.com/serviceability"
                    f"?itemId={sku}&pincode={pincode}",
                    {"Accept": "application/json",
                     "User-Agent": "tatbench/1.0 (competitive benchmarking)"},
                    "GET", None)

        POST example:
            return ("https://api.example.com/v2/delivery",
                    {"Accept": "application/json",
                     "Content-Type": "application/json"},
                    "POST",
                    json.dumps({"pincode": pincode, "sku": sku}).encode())

        Send an honest User-Agent. Spoofing a browser to evade bot detection is
        a different activity with a different risk profile, and it is not what
        this tool is for.
        """
        raise NotImplementedError

    def parse(self, status, text, base):
        """Return the normalised row. See base.Adapter.parse.

        `parse_window` already handles every date shape seen in the wild:
        '11 Sep - 13 Sep', 'Sep 11 - Sep 13', ISO, 'in 5-7 days',
        'within 4 days', and single dates. You rarely need your own date code -
        find the string and hand it over.
        """
        if status != 200:
            raise ParseError(f"HTTP {status}")
        try:
            body = json.loads(text)
        except Exception as exc:
            raise ParseError(f"not JSON: {exc}") from exc

        # --- map the brand's shape onto ours -----------------------------
        # serviceable: 'Yes' / 'No' / '' when genuinely unknown
        # city:        free text, useful for sanity-checking the pincode list
        # fast / std:  (min_days, max_days) or (None, None) if not offered
        #
        # If a brand has only ONE delivery speed, put it in 'std' and leave
        # 'fast' empty. The analysis treats a brand with no fast tier correctly;
        # it cannot tell the difference between "no express option" and
        # "express parsed wrong", so be explicit.
        raise NotImplementedError
