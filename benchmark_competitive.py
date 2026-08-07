"""
Benchmark A — the competitive check. "Could this buyer find comparable stock cheaper?"

This is the thing Coleen actually asked for, twice:

  22 Jul  "everything normally goes to realestate.com.au ... if they go to real estate
           and get it for $600, obviously they're not going to come and buy from us."
   3 Aug  "That one is a filter. That's not the benchmark I want."

Kept strictly apart from the market-context benchmark. A new turnkey package carries a
premium over a 1990s house on the same street; that premium is FINE here, because the
buyer's alternatives genuinely include that older house, and actively misleading in a
client report. One number cannot serve both.

TWO CORRECTIONS FROM THE BRIEF, both of which decide whether this is useful or noise:

  * Compare COMPARABLE STOCK, not "the same building". A builder's package is usually
    not listed individually on a portal, so searching for the same lot returns nothing
    almost every time.
  * Flag on the cheapest comparable from a TIGHT tier, never the global minimum. In any
    suburb something is always cheaper — a smaller block, a worse pocket, a distressed
    sale — so a naive version flags ~100% of the catalogue and becomes noise.

AND ONE THIS CODEBASE ADDS. A comparable must cover the same thing our price covers. A
vacant block at $310,000 is a real listing and a catastrophic comparable for a $750,000
package: our own land and package medians sit only 15% apart, so it would not look wrong
to anyone reading the output. price_kind makes the mix impossible rather than unlikely.
"""

import os
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import price_kind
from comp_provider import (BASIS_ESTIMATE, CompQuery, RawComp, get_provider)

# Config, not constants — the brief is explicit, and the right values are an empirical
# question we cannot answer until real comps come back.
MIN_COMPS = int(os.environ.get("SPB_MIN_COMPS", "8"))
MIN_FOR_ANY_VERDICT = int(os.environ.get("SPB_MIN_COMPS_FLOOR", "4"))
MAX_ESTIMATE_SHARE = float(os.environ.get("SPB_MAX_ESTIMATE_SHARE", "0.30"))

# The ladder. Tier 1-2 are "tight" — only these may drive the flag decision, because a
# loose-tier cheapest is not the same product.
TIGHT_TIERS = (1, 2)

VERDICT_FLAG = "flag"                 # comparable stock is cheaper; show the buyer
VERDICT_PASS = "pass"                 # nothing comparable is cheaper
VERDICT_NONE = "insufficient"         # no genuine comparable; no claim either way

CONF_HIGH, CONF_MEDIUM, CONF_LOW, CONF_NONE = "high", "medium", "low", "insufficient"


@dataclass
class Tier:
    n: int
    bedrooms_tolerance: int
    land_tolerance: Optional[float]     # None = do not constrain on land
    neighbour_km: float                 # 0 = the suburb itself only


LADDER = (
    Tier(1, 0, 0.15, 0.0),
    Tier(2, 0, 0.30, 0.0),
    Tier(3, 1, None, 0.0),
    Tier(4, 0, None, 5.0),
    Tier(5, 1, None, 15.0),
)


@dataclass
class Result:
    verdict: str = VERDICT_NONE
    confidence: str = CONF_NONE
    tier: Optional[int] = None
    n_comps: int = 0
    n_excluded_no_price: int = 0
    median: Optional[float] = None
    p25: Optional[float] = None
    p75: Optional[float] = None
    cheapest: Optional[float] = None
    cheapest_url: str = ""
    percentile: Optional[float] = None
    delta_abs: Optional[float] = None
    delta_pct: Optional[float] = None
    source: str = ""
    as_at: str = ""
    note: str = ""
    comps: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def client_safe(self) -> bool:
        """Whether this may produce a client-facing claim.

        low and insufficient are internal-only, per the brief. A weak comparison stated
        confidently in a document a buyer acts on is worse than saying nothing.
        """
        return self.confidence in (CONF_HIGH, CONF_MEDIUM) and self.verdict != VERDICT_NONE


def _usable(comps: List[RawComp], kind: str) -> tuple:
    """(priced comps of the right kind, how many were dropped for having no price)."""
    same_kind = [c for c in comps if price_kind.is_comparable(c.price_kind, kind)]
    priced = [c for c in same_kind if c.price and c.price > 0]
    # A price estimate is weaker evidence; it may not dominate the set.
    estimates = [c for c in priced if c.price_basis == BASIS_ESTIMATE]
    if priced and len(estimates) / len(priced) > MAX_ESTIMATE_SHARE:
        keep = int(len(priced) * MAX_ESTIMATE_SHARE)
        priced = [c for c in priced if c.price_basis != BASIS_ESTIMATE] + estimates[:keep]
    return priced, len(same_kind) - len(priced)


def _confidence(tier: int, n: int) -> str:
    if tier <= 2 and n >= 12:
        return CONF_HIGH
    if tier <= 3 and n >= MIN_COMPS:
        return CONF_MEDIUM
    if n >= MIN_FOR_ANY_VERDICT:
        return CONF_LOW
    return CONF_NONE


def _percentile(price: float, prices: List[float]) -> float:
    below = sum(1 for p in prices if p < price)
    return round(below / len(prices) * 100.0, 1)


def evaluate(row: Dict[str, Any], provider=None, neighbours=None) -> Result:
    """The competitive check for one listing (or one cohort's representative).

    Returns a Result whose verdict is FLAG / PASS / INSUFFICIENT. Never raises: a
    provider outage degrades to "insufficient", it does not fail a user's search.
    """
    provider = provider or get_provider()
    kind, _why = price_kind.derive(row)
    price = row.get("price") or row.get("advertised_package_price")

    if kind not in price_kind.BENCHMARKABLE:
        return Result(note="what this price covers is not recorded, so it cannot be "
                           "compared to anything")
    if not price:
        return Result(note="no price to compare")

    suburb = str(row.get("suburb") or "").strip()
    state = str(row.get("state") or "").strip().upper()
    if not suburb or not state:
        return Result(note="no locality, so no comparable set can be built")

    beds = row.get("bedrooms")
    land = row.get("land_sqm") or row.get("land_size_sqm")

    for tier in LADDER:
        near = []
        if tier.neighbour_km and neighbours:
            near = neighbours(suburb, state, tier.neighbour_km)
        q = CompQuery(
            suburb=suburb, state=state, price_kind=kind,
            bedrooms=int(beds) if beds else None,
            bedrooms_tolerance=tier.bedrooms_tolerance,
            land_min=(land * (1 - tier.land_tolerance)) if (land and tier.land_tolerance) else None,
            land_max=(land * (1 + tier.land_tolerance)) if (land and tier.land_tolerance) else None,
            include_neighbours=near,
        )
        try:
            comps = provider.search_comps(q)
        except Exception:                                  # noqa: BLE001
            comps = []                                     # degrade, never fail
        priced, no_price = _usable(comps, kind)
        if len(priced) < MIN_COMPS and tier.n < LADDER[-1].n:
            continue                                       # loosen and try again

        if len(priced) < MIN_FOR_ANY_VERDICT:
            return Result(n_comps=len(priced), n_excluded_no_price=no_price,
                          tier=tier.n, source=getattr(provider, "name", ""),
                          note="not enough comparable stock to benchmark")

        prices = sorted(c.price for c in priced)
        cheapest_comp = min(priced, key=lambda c: c.price)
        res = Result(
            tier=tier.n, n_comps=len(priced), n_excluded_no_price=no_price,
            median=round(statistics.median(prices), 0),
            p25=round(prices[int(len(prices) * 0.25)], 0),
            p75=round(prices[int(len(prices) * 0.75)], 0),
            cheapest=cheapest_comp.price, cheapest_url=cheapest_comp.provider_url,
            percentile=_percentile(float(price), prices),
            source=getattr(provider, "name", ""),
            comps=[{"suburb": c.suburb, "state": c.state, "bedrooms": c.bedrooms,
                    "price": c.price, "source": getattr(provider, "name", ""),
                    "url": c.provider_url, "price_basis": c.price_basis}
                   for c in priced[:5]],
        )
        res.confidence = _confidence(tier.n, len(priced))
        res.delta_abs = round(float(price) - res.median, 0)
        res.delta_pct = round(res.delta_abs / res.median, 4) if res.median else None

        # THE FLAG DECISION. Only a tight tier may drive it: a loose-tier "cheapest" is
        # a different product, and flagging on it would mark almost the whole catalogue.
        if tier.n in TIGHT_TIERS and cheapest_comp.price < float(price):
            res.verdict = VERDICT_FLAG
            res.note = (f"comparable stock is listed from "
                        f"${cheapest_comp.price:,.0f} — below this package")
        elif tier.n in TIGHT_TIERS:
            res.verdict = VERDICT_PASS
            res.note = (f"comparable stock starts at ${cheapest_comp.price:,.0f}; "
                        f"this package is competitively priced")
        else:
            res.verdict = VERDICT_NONE
            res.note = ("only loosely comparable stock was found, so no claim is made "
                        "about whether this is cheaper")
        return res

    return Result(note="no comparable stock found at any tier")
