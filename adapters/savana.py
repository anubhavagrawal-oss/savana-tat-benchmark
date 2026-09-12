"""Savana adapter. Endpoint contract verified live 7 Sep and 11 Sep 2026.

    GET https://api-shop-in.savana.com/n/api/intention/item/v4/deliveryInfo
        ?spuId=<id>&pinCode=<6 digits>

No authentication: verified with credentials omitted - no cookies, no tokens,
no signed headers. Two query params and nothing else.

spuId is the 7-digit id in the PDP URL: savana.com/details/<slug>-<spuId>.
"""
from __future__ import annotations

import json
from datetime import date

from .base import Adapter, ParseError, parse_window

EP = "https://api-shop-in.savana.com/n/api/intention/item/v4/deliveryInfo"


class Savana(Adapter):
    name = "savana"
    # Our own API - we can be less shy here than with third parties, but this is
    # still production and still serves real customers.
    max_rps = 10.0

    def build(self, sku, pincode):
        return (
            f"{EP}?spuId={sku}&pinCode={pincode}",
            {"Accept": "application/json",
             "User-Agent": "tatbench/1.0 (internal ops analytics)"},
            "GET",
            None,
        )

    def parse(self, status, text, base):
        if status != 200:
            raise ParseError(f"HTTP {status}")
        try:
            body = json.loads(text)
        except Exception as exc:
            raise ParseError(f"not JSON: {exc}") from exc

        data = body.get("data")
        if data is None:
            raise ParseError(f"no data (ret={body.get('ret')})")

        avail = data.get("isAvailable")
        out = {
            "serviceable": "Yes" if avail == 1 else ("No" if avail == 0 else ""),
            "city": data.get("cityName") or "",
            "fast": (None, None),
            "std": (None, None),
        }

        for group in data.get("shippingInfo") or []:
            for line in group or []:
                tier = (line or {}).get("deliveryType")
                # A tier is ABSENT when not offered for this product+pincode.
                # That is signal, not a parse failure - leave it as (None, None).
                if tier == "fast":
                    out["fast"] = parse_window(line.get("content", ""), base)
                elif tier == "standard":
                    out["std"] = parse_window(line.get("content", ""), base)

        if out["serviceable"] == "Yes" and out["fast"][0] is None and out["std"][0] is None:
            # Serviceable but no parseable window at all => the response shape
            # probably changed. Fail loudly rather than record a blank.
            raise ParseError("serviceable but no tier window parsed")

        return out
