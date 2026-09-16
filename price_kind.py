"""
What a stored price actually COVERS — not what the property is.

This exists because of the $675,000 that went out against a $1,127,000 property. The
mistake was not really "we read the wrong field": it was that a price was only ever a
NUMBER. Nothing recorded whether it covered land, a build contract, or both, so `max()`
picked a figure and it became "the package price", and every check downstream compared
numbers with no idea what they represented.

Benchmarking gives that mistake a second, less obvious place to happen — on the OTHER
side of the comparison. A vacant block listed on a portal for $310,000 is a perfectly
real listing; drop it into the comparable set for a $750,000 house-and-land package and
the agent tells a buyer "you can find comparable stock cheaper here" and links them to
dirt. Across our own live stock the medians are only 15% apart:

    House & Land   2,248 rows   median $847,195
    Land only        145 rows   median $719,990

so a land price sitting in a package cohort does not look wrong to anyone eyeballing the
output. It has to be structurally impossible instead.

THE RULE: a comparison happens only between two prices of the same kind. Never adjusted,
never estimated across kinds, and never derived from the price itself — inferring the
kind from the number is precisely the reasoning that caused the original bug.

UNKNOWN IS AN ANSWER. 1,384 live rows record nothing that says what their price covers.
They are reported as "product type not recorded" and benchmarked against nothing, the
same way an unstated bedroom count already blocks scoring. A wrong confident comparison
is worse than an honest gap.
"""

import re
from typing import Any, Dict, Tuple

# The price covers land AND a dwelling — comparable to a portal house-and-land listing.
PACKAGE = "package"
# A strata dwelling sold whole (apartment, townhouse). The price covers the dwelling;
# there is no separate land component to be confused with.
DWELLING = "dwelling"
# The price covers land alone.
LAND_ONLY = "land_only"
# The price covers a build contract alone.
BUILD_ONLY = "build_only"
COMMERCIAL = "commercial"
# Nothing in the row says what the price covers.
UNKNOWN = "unknown"

# Kinds that may be benchmarked, each only ever against its own kind.
BENCHMARKABLE = (PACKAGE, DWELLING, LAND_ONLY)

# The vendor saying so in the row's own words. Same vocabulary verify_against_source.py
# uses to refuse to "correct" a land-only listing up to a package price.
_SAYS_LAND_ONLY = re.compile(
    r"\bland\s*only\b|\bvacant\s+land\b|\bregistered\s+land\b", re.I)
_SAYS_BUILD_ONLY = re.compile(r"\bbuild\s*only\b|\bhouse\s*only\b", re.I)

_BY_PRODUCT_TYPE = {
    "land": LAND_ONLY,
    "house & land": PACKAGE,
    "house and land": PACKAGE,
    "apartment": DWELLING,
    "townhouse": DWELLING,
    "commercial": COMMERCIAL,
}

# How far the components may miss the total and still be treated as summing to it —
# an advertised "$999,900" against a $1,000,350 sum is a round-down, not a discrepancy.
_SUM_TOLERANCE = 2_500.0


def _text(row: Dict[str, Any]) -> str:
    return " ".join(str(row.get(k) or "")
                    for k in ("source_text", "estate_name", "lot_address"))


def _num(row: Dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def derive(row: Dict[str, Any]) -> Tuple[str, str]:
    """(kind, why). `why` is stored so any verdict is traceable to the evidence for it.

    Order is deliberate: what the vendor WROTE about this row beats a product_type
    column that is blank on 32% of stock, and both beat arithmetic.
    """
    text = _text(row)
    if _SAYS_LAND_ONLY.search(text):
        return LAND_ONLY, "the source calls it land only"
    if _SAYS_BUILD_ONLY.search(text):
        return BUILD_ONLY, "the source calls it build only"

    product = str(row.get("product_type") or "").strip().lower()
    if product in _BY_PRODUCT_TYPE:
        return _BY_PRODUCT_TYPE[product], f"product type is {product!r}"

    # No product type. The row can still PROVE what its price covers by stating a land
    # price and a build price that add up to it — that is the source describing its own
    # price, not us inferring one. 382 live rows qualify.
    land, build, price = _num(row, "land_price"), _num(row, "build_price"), _num(row, "price")
    if land and build and price and abs((land + build) - price) <= _SUM_TOLERANCE:
        return PACKAGE, "the row states land + build adding up to its own price"

    # A house area and a bedroom count prove there is a DWELLING, but not that the price
    # covers the land under it — which is the only thing that matters here. 170 live rows
    # look like this and they stay unknown on purpose.
    return UNKNOWN, "nothing in the row says what the price covers"


def is_comparable(a: str, b: str) -> bool:
    """Whether two prices may be compared at all.

    Same kind, and a kind that can be benchmarked. UNKNOWN is never comparable, not even
    with another UNKNOWN: two prices that each cover something unrecorded are not
    thereby covering the same thing.
    """
    return a == b and a in BENCHMARKABLE


# --- price evidence -----------------------------------------------------------------
#
# WHAT THE ROW'S OWN TEXT SAYS ABOUT ITS PRICE, reduced to three facts the published
# snapshot can carry. The deployed search never sees source_text (build_web reads it and
# drops it: it is the whole flattened spreadsheet row), so any rule that needs to ask
# "does this list actually print this price, and is it the whole price?" has to be
# answered at build time or not at all.
#
# The rows that made it necessary, all reachable by Colin's QLD search under $750k and all
# wrong to show a buyer as the package price they are stored at:
#   South Ripley duplex (id 330)  "$725,000 Offers Over 780615 1505615"   total $1.51m
#   Redbank Plains duplex (329)   "$595,000 $732,285 732285 1327285"      total $1.33m
#   Heathwood (15897)             "Build Only $390,639"                   no land
#   Caboolture (19812)            "... $262 45000000.00% $382,201.00"     garbled merge
# and the rows it must NOT refuse: Dawn, Walloon lots 1228/1229/1231 ($721,900) and
# Silkwood Wacol ($595,000), whose text prints exactly the stored price and nothing larger.

# Stripped before any number is read. A link's path and a share id are digits nobody
# priced; a truncated "https:/" at the end of a lot_address is still a link.
#
# Matched per whitespace-separated TOKEN, never as one regex over the whole text: a
# pattern shaped like \S*\.com/\S* backtracks quadratically over a long run with no
# spaces, and build_web.py runs this on every row of a required nightly step, where a
# hang is worse than an exception.
_LINK_MARK = re.compile(r"https?:/|www\.", re.I)
_DOMAIN_WITH_PATH = re.compile(
    r"\W*[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|gov|edu|au|io|co|ly|gl|me)(?:\.au)?/", re.I)
# dd/mm/yyyy (and . or - separated), and ISO dates with an optional time.
_EVIDENCE_DATE = re.compile(
    r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"
    r"|\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b")
# Australian phone numbers as people write them: run together, or grouped the usual way.
# "0412345678" is otherwise a ten-digit number bigger than any price on the page.
# The grouping is all-or-nothing on purpose. "1300000 250" is a $1.3m total next to a
# 250 m2 lot, not "1300 000 250", and stripping it would hide exactly the larger total
# this evidence exists to find.
_EVIDENCE_PHONE = re.compile(
    r"(?<![\w$.,+])(?:"
    r"04\d{2}(?:\d{6}|[ -]\d{3}[ -]\d{3})"                       # 0412 345 678
    r"|0[2378](?:\d{8}|[ -]\d{4}[ -]\d{4})"                      # 07 3123 4567
    r"|\(0[2378]\)[ -]?\d{4}[ -]?\d{4}"                          # (07) 3123 4567
    r"|\+61[ -]?(?:4\d{2}(?:\d{6}|[ -]\d{3}[ -]\d{3})"           # +61 412 345 678
    r"|[2378](?:\d{8}|[ -]\d{4}[ -]\d{4}))"                      # +61 7 3123 4567
    r"|1[38]00(?:\d{6}|[ -]\d{3}[ -]\d{3})"                      # 1300 123 456
    r")(?![\w%]|[.,]\d)")
# E-Agent product codes: "PR8638", "PR 45", "PR84444". A code is not an amount, and the
# spaced form would otherwise read as a bare number.
_EVIDENCE_CODE = re.compile(r"\bPR[ -]?\d+\b", re.I)

_AMOUNT = r"(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?"
# "$721,900", "$ 382,201.00", "$989K", "$1.51m" -- any size. The suffix must end the
# word, so "$450 m2" and "$5m2" are not millions.
_MONEY = re.compile(r"\$\s?" + _AMOUNT + r"(?:([kKmM])(?![A-Za-z0-9\u00b2]))?")
# "45000000.00%": a merged cell reads as a percentage and is still a number on the line.
_PERCENT = re.compile(r"(?<![\w.,$])" + _AMOUNT + r"\s?%")
# A THOUSANDS-GROUPED amount counts even when letters touch it. An OCR'd price list reads
# its "$" as "S" -- id 17691 prints "Price $ 3,425,000.00S $ 2,085,000.00$ S 1,180,000.00$"
# and is stored at $2,085,000 -- and refusing "3,425,000.00S" as glued to a letter hid the
# larger price this evidence exists to find. The letter rule below is for share ids and
# codes, and those never carry thousands separators.
_GROUPED = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+)(\.\d+)?(?![\d,]|\.\d)")
# A number standing on its own. Not glued to letters, so a hex share id such as
# "651a087190a4" is not six digits of price.
_BARE = re.compile(r"(?<![\w.,$])" + _AMOUNT + r"(?![\w%]|[.,]\d)")

# A bare number counts towards the text's largest price only from six digits up
# ($100,000): below that it is a lot, a size, a postcode or a year.
_BARE_PRICE_DIGITS = 6


# The largest amount recorded. A garbled cell can hold a digit run hundreds long, which
# float() turns into infinity -- and json.dumps writes that as `Infinity`, which is not
# JSON: the browser's parse of the whole stock.json would fail. Clamped instead, still far
# above any price, and exact as a JavaScript number (below 2**53).
_MAX_AMOUNT = 10 ** 15


def _amount(whole: str, frac: str, suffix: str = "") -> float:
    value = float(whole.replace(",", "") + (frac or ""))
    value *= {"k": 1_000.0, "m": 1_000_000.0}.get((suffix or "").lower(), 1.0)
    return min(value, float(_MAX_AMOUNT))


def _compact(value: float):
    """721900 rather than 721900.0 -- the snapshot pays for every byte on every row."""
    value = round(value, 2)
    return int(value) if value == int(value) else value


def _without_links(text: str) -> str:
    kept = []
    for token in text.split():
        mark = _LINK_MARK.search(token)
        if mark:
            # "$721,900https://..." keeps the price glued in front of the link.
            token = token[:mark.start()]
        elif _DOMAIN_WITH_PATH.match(token):
            token = ""
        if token:
            kept.append(token)
    return " ".join(kept)


def evidence_text(row: Dict[str, Any]) -> str:
    """The row's text with links, dates, phone numbers and product codes removed."""
    text = " ".join(str(row.get(k) or "") for k in ("source_text", "lot_address"))
    text = _without_links(text)
    for pattern in (_EVIDENCE_DATE, _EVIDENCE_PHONE, _EVIDENCE_CODE):
        text = pattern.sub(" ", text)
    return text


def price_evidence(row: Dict[str, Any]) -> Dict[str, Any]:
    """{'price_kind', 'text_price_max', 'price_in_text'} for one stored row.

    price_kind      derive(row)'s kind: build_only, land_only, package, unknown ...
    text_price_max  the largest amount the text prints -- money of any size, bare numbers
                    of six digits or more (a thousands-grouped one even with letters
                    touching it), and percentages -- after links, dd/mm/yyyy
                    dates, phone numbers and PR codes are removed. None when it prints
                    none. Larger than the stored price means the stored price is probably
                    one component of a bigger total.
    price_in_text   True when the stored price itself is printed in the text (as money or
                    as a bare number), so the price was read off this line, not guessed.
    """
    text = evidence_text(row)
    money = []
    for m in _MONEY.finditer(text):
        money.append(_amount(m.group(1), m.group(2), m.group(3)))
    text = _MONEY.sub(" ", text)
    percent = [_amount(m.group(1), m.group(2)) for m in _PERCENT.finditer(text)]
    text = _PERCENT.sub(" ", text)
    bare = [(_amount(m.group(1), m.group(2)), len(m.group(1).replace(",", "")))
            for m in _GROUPED.finditer(text)]
    text = _GROUPED.sub(" ", text)
    bare += [(_amount(m.group(1), m.group(2)), len(m.group(1).replace(",", "")))
             for m in _BARE.finditer(text)]

    largest = money + percent + [v for v, digits in bare if digits >= _BARE_PRICE_DIGITS]
    price = _num(row, "price")
    printed = money + [v for v, _digits in bare]
    return {
        "price_kind": derive(row)[0],
        "text_price_max": _compact(max(largest)) if largest else None,
        "price_in_text": bool(price > 0 and any(abs(v - price) < 1.0 for v in printed)),
    }
