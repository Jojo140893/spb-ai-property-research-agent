"""
Listing feature extraction — the fields Coleen asked for on 28/29 July.

Most of what he wants is already present in the harvested text and was simply
never parsed out. A real stored row:

    Lot 82 Aberdeen  282  142.8  12.0  Sep-26  Empley 15  3x2x2  $205,000  $335,220  $540,220  Available
    └lot  └estate    land  house  frontage  title  design   bed×bath×car  land$    build$    total$   STATUS

This module takes one flattened row of text and returns those fields. It is kept
separate from adaptive_extract so it can be unit-tested without Playwright, and it
is called by every channel (XLSX/PDF stocklists, HTML portals, email bodies).

Two rules it holds to:
  * **Never guess.** A field it cannot establish is returned as None. A wrong
    storey or a wrong availability is worse for the client than a blank.
  * **Must see the UNTRUNCATED row.** Availability is the last token on VIC rows,
    so a truncated string loses it — that is why ~30% of already-stored rows can
    never have it recovered.
"""

import re
from typing import Any, Dict, Optional

# ---------- availability ----------
# Order matters: multi-word and negated forms are tested before the bare word.
_AVAILABILITY = (
    (re.compile(r"\bunder\s*offer\b", re.I), "Under Offer"),
    (re.compile(r"\b(?:on\s*hold|onhold)\b", re.I), "On Hold"),
    (re.compile(r"\b(?:not\s*available|unavailable)\b", re.I), "Not Available"),
    (re.compile(r"\bsold\b", re.I), "Sold"),
    (re.compile(r"\b(?:reserved|deposit\s*taken)\b", re.I), "Reserved"),
    (re.compile(r"\bleased\b", re.I), "Leased"),
    (re.compile(r"\bavailable\b", re.I), "Available"),
    (re.compile(r"\bhold\b", re.I), "On Hold"),
)

# ---------- storey ----------
_STOREY_EXPLICIT = re.compile(r"\b(single|double|two|triple)[\s-]*(?:storey|story|level)s?\b", re.I)
_STOREY_NUMERIC = re.compile(r"\b([12])\s*(?:storey|story|level)s?\b", re.I)
# Bare SINGLE / DOUBLE as a standalone token — how the QLD dual-occ stocklists print it.
_STOREY_BARE = re.compile(r"\b(SINGLE|DOUBLE)\b")
# Words that make a bare SINGLE/DOUBLE mean something else entirely.
# "Single Contract" is the big one — Paramount labels every package that way.
_STOREY_FALSE_FRIEND = re.compile(
    r"\b(?:contract|garage|lock[\s-]?up|glaz|glazed|title|titled|vanity|bowl|sink|shower|"
    r"basin|storey\s*height|key|occupancy|bed|door|gate|carport|driveway|width|fronted)\b", re.I)
_STOREY_WORD = {"single": "SINGLE", "double": "DOUBLE", "two": "DOUBLE", "triple": "TRIPLE",
                "1": "SINGLE", "2": "DOUBLE"}

# ---------- lot / stock identifier ----------
_LOT_NUM = re.compile(r"\blot\s*([0-9]{1,6}[A-Za-z]?)\b", re.I)
_STOCK_CODE = re.compile(r"\b([A-Z]{2,4}-\d{3,5})\b")

# ---------- estate ----------
_ESTATE = re.compile(r"\b([A-Z][A-Za-z'’]+(?:\s+[A-Z][A-Za-z'’]+){0,3})\s+Estate\b")

# ---------- frontage ----------
# Labelled, or an explicit metre unit. A bare decimal (the "12.0" above) is NOT
# taken — position in a flattened row is not reliable enough to be worth a wrong number.
_FRONTAGE_LABELLED = re.compile(r"frontage\s*(?:\(m\))?\s*:?\s*([0-9]{1,2}(?:\.[0-9]{1,2})?)", re.I)
_FRONTAGE_UNIT = re.compile(r"\b([0-9]{1,2}(?:\.[0-9]{1,2})?)\s*m\s*(?:frontage|wide|width)\b", re.I)

# ---------- postcode ----------
_FOUR_DIGIT = re.compile(r"\b(\d{4})\b")

# ---------- incentives ----------
_INCENTIVE_CONTEXT = (r"rebate|incentive|bonus|cash\s*back|cashback|credit|discount|gift|"
                      r"free\s+upgrade|promo(?:tion)?|grant|contribution|allowance|saving|"
                      r"off\s+the\s+price|vendor\s*(?:paid|rebate)")
_AMOUNT = r"\$\s?([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,6}|[0-9]{1,3}\s*k)"
_INCENTIVE_BEFORE = re.compile(rf"(?:{_INCENTIVE_CONTEXT})[^$\n]{{0,40}}{_AMOUNT}", re.I)
_INCENTIVE_AFTER = re.compile(rf"{_AMOUNT}[^$\n]{{0,40}}(?:{_INCENTIVE_CONTEXT})", re.I)
# Anything that means the figure is rent or a yield, not a rebate.
_MONEY_NOT_INCENTIVE = re.compile(r"p\.?w\.?|per\s*week|weekly|rent|yield|p\.?a\.?|per\s*annum|%", re.I)


def _clean(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def parse_availability(text: str) -> Optional[str]:
    t = _clean(text)
    for rx, label in _AVAILABILITY:
        if rx.search(t):
            return label
    return None


def parse_storey(text: str) -> Optional[str]:
    t = _clean(text)
    m = _STOREY_EXPLICIT.search(t)
    if m:
        return _STOREY_WORD.get(m.group(1).lower())
    m = _STOREY_NUMERIC.search(t)
    if m:
        return _STOREY_WORD.get(m.group(1))
    # Bare token: only trust it when nothing nearby redefines it.
    for m in _STOREY_BARE.finditer(t):
        window = t[max(0, m.start() - 30):m.end() + 30]
        if not _STOREY_FALSE_FRIEND.search(window):
            return m.group(1).upper()
    return None


def parse_lot_number(text: str) -> Optional[str]:
    t = _clean(text)
    m = _STOCK_CODE.search(t)          # CC-0122 style stock codes
    if m:
        return m.group(1)
    m = _LOT_NUM.search(t)
    return m.group(1) if m else None


def parse_estate(text: str, context: str = "") -> Optional[str]:
    for source in (text, context):
        m = _ESTATE.search(_clean(source))
        if m:
            return _clean(m.group(1))
    # A group/banner row is often just the estate line ("Aberdeen - Winter Valley - VIC - House & Land")
    if context:
        first = _clean(re.sub(r"^[^A-Za-z0-9]+", "", context)).split(" - ")[0]
        if 2 < len(first) <= 40 and "$" not in first:
            return first
    return None


def parse_frontage(text: str) -> Optional[float]:
    t = _clean(text)
    for rx in (_FRONTAGE_LABELLED, _FRONTAGE_UNIT):
        m = rx.search(t)
        if m:
            try:
                v = float(m.group(1))
                if 3.0 <= v <= 60.0:
                    return v
            except ValueError:
                pass
    return None


def parse_postcode(text: str) -> Optional[str]:
    """A 4-digit AU postcode. Lot numbers, dates, years and areas are excluded."""
    t = _clean(text)
    # Strip anything that produces a 4-digit look-alike, in order:
    #   dates   — "25/03/2026" otherwise yields postcode 2026
    #   months  — "Sep-26", "Q1-2026"
    #   lots    — "LOT 5532" otherwise yields postcode 5532
    t = re.sub(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", " ", t)
    t = re.sub(r"\b\d{4}-\d{1,2}-\d{1,2}\b", " ", t)
    t = re.sub(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s-]*\d{2,4}\b", " ", t, flags=re.I)
    t = re.sub(r"\bq[1-4][\s-]*\d{2,4}\b", " ", t, flags=re.I)
    t = _LOT_NUM.sub(" ", t)
    t = _STOCK_CODE.sub(" ", t)
    for m in _FOUR_DIGIT.finditer(t):
        val = int(m.group(1))
        after = t[m.end():m.end() + 6].lower()
        if re.match(r"\s*(?:m2|m²|sqm|sq)", after):        # an area, not a postcode
            continue
        if 2015 <= val <= 2035:                            # a bare year
            continue
        if 800 <= val <= 7999:
            return m.group(1)
    return None


def parse_incentive(text: str, package_price: Optional[float] = None) -> Dict[str, Any]:
    """Cash incentives / rebates — Coleen: "$15,000 being offered, $7.5 being offered".

    Runs as its OWN scan. The package-price detector is untouched: widening that to
    reach sub-$50k figures would pull weekly rent ($2,330) into the price list and
    make the existing land-price bug worse.
    """
    t = _clean(text)
    found, phrases = [], []
    for rx in (_INCENTIVE_BEFORE, _INCENTIVE_AFTER):
        for m in rx.finditer(t):
            window = t[max(0, m.start() - 40):m.end() + 40]
            if _MONEY_NOT_INCENTIVE.search(window):
                continue                                   # rent / yield, not a rebate
            raw = m.group(1).replace(",", "").strip().lower()
            try:
                val = float(raw[:-1]) * 1000 if raw.endswith("k") else float(raw)
            except ValueError:
                continue
            if not (1_000 <= val <= 150_000):
                continue
            if package_price and val >= 0.25 * package_price:
                continue                                   # too big to be an incentive
            found.append(val)
            phrases.append(_clean(m.group(0))[:80])
    if not found:
        return {"incentive_amount": None, "incentive_text": None}
    # Largest wins; never sum — stacking rules are unknowable.
    return {"incentive_amount": max(found),
            "incentive_text": " | ".join(dict.fromkeys(phrases))[:200]}


def parse_listing_features(text: str, context: str = "",
                           package_price: Optional[float] = None) -> Dict[str, Any]:
    """All of the above in one pass. Call with the UNTRUNCATED row text."""
    out: Dict[str, Any] = {
        "availability_status": parse_availability(text),
        "storey": parse_storey(text),
        "lot_number": parse_lot_number(text),
        "estate_name": parse_estate(text, context),
        "frontage_m": parse_frontage(text),
        "postcode": parse_postcode(text),
    }
    out.update(parse_incentive(text, package_price))
    return out
