"""
Benchmark B — market context. "Is this a good buy for the suburb?"

Deliberately NOT the same number as Benchmark A, and the brief is emphatic about why: a
brand-new turnkey package carries a premium over a 1990s house on the same street. That
premium is FINE in the competitive check, where the buyer's alternatives genuinely
include the older house. It is actively misleading here, in a document that tells a buyer
their purchase is sound — benchmarking a new package against established-heavy comps makes
it look expensive every single time.

So this runs the same ladder filtered to NEW-BUILD stock first, and falls back to all
stock only with `established_comps_used` set, which the report must surface rather than
quietly absorb.

Coleen described the output on 22 Jul: "your purchase price is this, but the average house
price is this, right, so that they can see that they are doing a good buy."

WHAT IT REFUSES TO DO. Produce a client-facing claim from a weak comparison. Low or
insufficient confidence yields context that is marked internal-only, and client_report
omits the market sentence entirely rather than hedging it. A confident-sounding number in
a document a buyer acts on is the worst output this system can produce.
"""

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import price_kind
from benchmark_competitive import (CONF_HIGH, CONF_LOW, CONF_MEDIUM, CONF_NONE, LADDER,
                                   MIN_COMPS, MIN_FOR_ANY_VERDICT, _confidence, _usable)
from comp_provider import CompQuery, SuburbStats, get_provider

# SOP Step 7 bands, on the variance against the market comp set.
BANDS = ((-0.05, "Below Market Value", 15.0),
         (0.05, "Competitive Market Value", 12.5),
         (0.12, "Slightly Above Market", 10.0))
POOR = ("Poor Value", 5.0)
NEUTRAL = 7.5


@dataclass
class MarketContext:
    benchmarked: bool = False
    classification: str = "Unbenchmarked - Pending Market Data"
    value_score_contribution: float = NEUTRAL
    avg_market_price: Optional[float] = None
    variance_pct: Optional[float] = None
    n_comps: int = 0
    n_excluded_no_price: int = 0
    confidence: str = CONF_NONE
    tier: Optional[int] = None
    established_comps_used: bool = False
    source: str = ""
    as_at: str = ""
    stats: Optional[SuburbStats] = None
    comparables: List[Dict[str, Any]] = field(default_factory=list)
    data_note: str = ""

    @property
    def client_safe(self) -> bool:
        """Whether the report may state this to a buyer.

        Low confidence, or a comp set padded out with established homes, means the number
        exists but the CLAIM does not. client_report omits the sentence rather than
        hedging it — laundering a weak comparison into a confident line is precisely the
        failure the brief warns against.
        """
        return (self.benchmarked
                and self.confidence in (CONF_HIGH, CONF_MEDIUM)
                and not self.established_comps_used)


def _classify(variance: float):
    for edge, label, points in BANDS:
        if variance < edge:
            return label, points
    return POOR


def evaluate(row: Dict[str, Any], provider=None, neighbours=None,
             as_at: str = "") -> MarketContext:
    """Market context for one listing. Never raises."""
    provider = provider or get_provider()
    kind, _why = price_kind.derive(row)
    price = row.get("price") or row.get("advertised_package_price")
    # Same gate as the competitive check, and for a sharper reason: the answer here is
    # passed to provider.get_suburb_stats and rendered in the client report under "How
    # this compares to the suburb". Asking a provider for the median price of 'GARAGE'
    # and printing whatever comes back is the worst version of this bug.
    import suburb_quality
    suburb, _sub_why = suburb_quality.resolve(row)
    state = str(row.get("state") or "").strip().upper()

    if (kind not in price_kind.BENCHMARKABLE or not price
            or not suburb_quality.is_located(_sub_why) or not state):
        return MarketContext(data_note="not enough recorded about this listing to place "
                                       "it against its suburb")

    beds = row.get("bedrooms")
    land = row.get("land_sqm") or row.get("land_size_sqm")

    # New-build stock first. Falling back to everything is allowed, but it is a different
    # claim and has to be labelled as one.
    for new_only in (True, False):
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
                include_neighbours=near, new_build_only=new_only)
            try:
                comps = provider.search_comps(q)
            except Exception:                                   # noqa: BLE001
                comps = []
            if new_only:
                comps = [c for c in comps if c.is_new_build]
            priced, no_price = _usable(comps, kind)
            if len(priced) < MIN_COMPS and tier.n < LADDER[-1].n:
                continue
            if len(priced) < MIN_FOR_ANY_VERDICT:
                continue

            prices = [c.price for c in priced]
            avg = round(statistics.mean(prices), 0)
            variance = (float(price) - avg) / avg if avg else 0.0
            label, points = _classify(variance)
            ctx = MarketContext(
                benchmarked=True, classification=label, value_score_contribution=points,
                avg_market_price=avg, variance_pct=round(variance * 100.0, 1),
                n_comps=len(priced), n_excluded_no_price=no_price,
                confidence=_confidence(tier.n, len(priced)), tier=tier.n,
                established_comps_used=not new_only,
                source=getattr(provider, "name", ""), as_at=as_at,
                comparables=[{"suburb": c.suburb, "state": c.state,
                              "bedrooms": c.bedrooms, "price": c.price,
                              "source": getattr(provider, "name", ""),
                              "url": c.provider_url} for c in priced[:5]],
            )
            try:
                ctx.stats = provider.get_suburb_stats(suburb, state)
            except Exception:                                   # noqa: BLE001
                ctx.stats = None
            ctx.data_note = (
                f"Benchmarked against {len(priced)} comparable listing(s) from "
                f"{ctx.source or 'the configured source'}"
                + (" — including established homes, which carry no new-build premium"
                   if ctx.established_comps_used else "")
                + (f", as at {as_at}" if as_at else "") + ".")
            return ctx

    return MarketContext(data_note="no comparable market stock was found for this suburb, "
                                   "so no market comparison is made")
