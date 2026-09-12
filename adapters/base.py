"""Adapter contract for tatbench.

One adapter per brand. The engine handles scheduling, rate limiting, retries,
resume and storage; the adapter only knows how to (a) shape a request for one
pincode and (b) turn the response into a normalised row.

To add a brand, copy `_template.py`, fill in the three methods, register it in
`adapters/__init__.py`, and add a block to config.json. See DISCOVERY.md for how
to find a storefront's delivery endpoint from DevTools.
"""
from __future__ import annotations

import re
from datetime import date

MONTHS = {m: i + 1 for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
MONTHS_FULL = {m: i + 1 for i, m in enumerate(
    "January February March April May June July August September "
    "October November December".split())}

_DMON = re.compile(
    r"(\d{1,2})\s*(?:st|nd|rd|th)?[\s,]+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", re.I)
_MOND = re.compile(
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?", re.I)
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_NDAYS = re.compile(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*(?:days|business days)", re.I)
_1DAY = re.compile(r"(?:in|within)\s+(\d{1,2})\s*(?:days|business days)", re.I)


class ParseError(Exception):
    """Raised when a response is structurally unrecognised.

    The engine records this as an error row and retries on the next run, rather
    than silently writing a blank. That distinction matters: a brand quietly
    changing its response shape must show up as failures, not as a TAT of zero.
    """


def _mk(y: int, m: int, d: int, base: date):
    """Build a date, rolling the year forward when the month has wrapped."""
    year = y if y else (base.year + 1 if m < base.month else base.year)
    try:
        return date(year, m, d)
    except ValueError:
        return None


def parse_window(text: str, base: date):
    """Extract a delivery window from free text -> (min_days, max_days).

    Handles every shape seen across Indian storefronts:
      '11 Sep - 13 Sep'      -> explicit day-month range
      'Sep 11 - Sep 13'      -> month-first
      '2026-09-11'           -> ISO
      'in 5-7 days'          -> relative range (no date maths needed)
      'within 4 days'        -> single relative
      'Sun, 13 Sep'          -> single date (min == max)
    Returns (None, None) when nothing parses - the caller decides if that is an
    error or simply a tier that is not offered.
    """
    t = str(text or "")

    m = _NDAYS.search(t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return min(a, b), max(a, b)

    found = []
    for d_, mon in _DMON.findall(t):
        dt = _mk(0, MONTHS[mon[:3].title()], int(d_), base)
        if dt:
            found.append(dt)
    if not found:
        for mon, d_ in _MOND.findall(t):
            dt = _mk(0, MONTHS[mon[:3].title()], int(d_), base)
            if dt:
                found.append(dt)
    if not found:
        for y, mo, d_ in _ISO.findall(t):
            dt = _mk(int(y), int(mo), int(d_), base)
            if dt:
                found.append(dt)

    if found:
        found = found[:2]
        offs = [(x - base).days for x in found]
        return min(offs), max(offs)

    m = _1DAY.search(t)
    if m:
        n = int(m.group(1))
        return n, n

    return None, None


class Adapter:
    """Subclass this per brand."""

    name = "override-me"
    #: Requests/sec ceiling the adapter itself considers safe. config.json may
    #: lower this but never raises it above this value.
    max_rps = 4.0

    def build(self, sku: str, pincode: str):
        """-> (url, headers_dict, method, body_or_None)."""
        raise NotImplementedError

    def parse(self, status: int, text: str, base: date) -> dict:
        """-> {'serviceable': 'Yes'|'No'|'', 'city': str,
               'fast': (min,max), 'std': (min,max)}

        Raise ParseError if the response is unrecognisable, so the engine
        records a failure instead of a false zero.
        """
        raise NotImplementedError

    def tiers_for(self, status: int, text: str):
        """-> list of tier names present. Used by the SKU pre-flight check."""
        try:
            r = self.parse(status, text, date.today())
        except ParseError:
            return []
        return [t for t in ("fast", "std") if r.get(t) and r[t][0] is not None]
