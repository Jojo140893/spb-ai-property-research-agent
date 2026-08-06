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
