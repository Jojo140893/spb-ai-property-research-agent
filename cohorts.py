"""
Group listings into the units a benchmark is actually computed for.

WHY COHORTS AND NOT LISTINGS. Benchmarking 5,600 listings against a provider one at a
time is 5,600 API calls a night. Most of those calls would ask the identical question:
Toowoomba City, house-and-land, 4 bed, 400-450 m2 — asked once for every listing that
happens to match. Grouping first collapses the catalogue into far fewer distinct
questions, the cohort is priced once, and the answer fans back out to every listing in
it. This is the difference between a nightly job that fits inside an API quota and one
that does not.

WHAT MAKES A COHORT. Suburb, price kind, bedroom count, land band. Price kind is in the
key and not an afterthought: a cohort is the set of things that may legitimately be
compared with one another, and comparing a land price to a package price is the mistake
this whole build is guarding against (see price_kind.py).

A row that cannot be keyed is not benchmarked. No suburb, no bedroom count, or a price
whose coverage the row never states — each of those means we do not know enough to ask
the provider a question we could trust the answer to. Those rows are counted and named,
never quietly folded into the nearest cohort.
"""

import os
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Tuple

from price_kind import BENCHMARKABLE, derive

# Land bands in m2. Config, not constants — the brief is explicit about that, and the
# right banding is an empirical question we will only answer once real comps come back.
_DEFAULT_BANDS = "300,450,600,800,1000"


def land_bands() -> List[float]:
    raw = os.environ.get("SPB_LAND_BANDS", _DEFAULT_BANDS)
    return [float(x) for x in raw.split(",") if x.strip()]


def land_band(land_sqm: Optional[float]) -> Optional[str]:
    """'450-600', or None when the row never stated a land size.

    None is a real answer, not a bucket. A listing with no land size cannot be matched
    on land size, which is one of the three things the client named.
    """
    try:
        value = float(land_sqm or 0)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    edges = land_bands()
    low = 0.0
    for edge in edges:
        if value < edge:
            return f"{low:.0f}-{edge:.0f}"
        low = edge
    return f"{low:.0f}+"


_GEO = None


def _locality(suburb: str, state: str) -> str:
    """The real locality inside whatever the extractor put in the suburb column.

    geo.resolve_locality already does this and is used on benchmark_buildings' internal
    path (benchmark_buildings.py:203-211) but NOT on its market path — reusing it here
    rather than writing a second version. Falls back to the raw value only if the geo
    index failed to load, so a missing data file degrades to today's behaviour rather
    than emptying the catalogue.
    """
    global _GEO
    if _GEO is None:
        from geo import SuburbGeoIndex
        _GEO = SuburbGeoIndex()
    if not _GEO.loaded:
        return suburb
    return _GEO.resolve_locality(suburb, state) or ""


class CohortKey(NamedTuple):
    suburb: str
    state: str
    price_kind: str
    bedrooms: int
    land_band: str

    def describe(self) -> str:
        return (f"{self.suburb} {self.state} · {self.price_kind} · "
                f"{self.bedrooms} bed · {self.land_band} m2")


# Why a row could not be keyed. Reported, never silently dropped.
SKIP_NO_SUBURB = "no suburb recorded"
SKIP_NO_BEDROOMS = "no bedroom count recorded"
SKIP_NO_LAND_BAND = "no land size recorded"
SKIP_KIND = "what the price covers is not recorded"
SKIP_NOT_BENCHMARKABLE = "this kind of price has no portal comparable"


def key_for(row: Dict[str, Any]) -> Tuple[Optional[CohortKey], str]:
    """(key, why_not). Exactly one of the two is meaningful."""
    kind, _why = derive(row)
    if kind not in BENCHMARKABLE:
        # 'unknown' and 'build_only' both land here, for different reasons worth keeping
        # apart in the report: one is a data gap, the other has no market equivalent.
        return None, SKIP_KIND if kind == "unknown" else SKIP_NOT_BENCHMARKABLE

    state = str(row.get("state") or "").strip().upper()
    # Resolve to a REAL locality before keying. Without this the largest cohorts in the
    # catalogue are spreadsheet debris — "In Ternal Balcony Total QLD" (44 listings),
    # "7 Star Energy Rating VIC" (34), "One Part Contracts VIC" (39) — which would become
    # the highest-quota provider queries in the nightly run, asked about places that do
    # not exist. It also RECOVERS rows whose suburb is buried in a composite:
    # "Kemps Estate | 155 Boyd Avenue, Austral" -> "Austral".
    suburb = _locality(str(row.get("suburb") or "").strip(), state)
    if not suburb or not state:
        return None, SKIP_NO_SUBURB

    beds = row.get("bedrooms")
    if beds in (None, "", 0):
        return None, SKIP_NO_BEDROOMS
    try:
        beds = int(float(beds))
    except (TypeError, ValueError):
        return None, SKIP_NO_BEDROOMS

    band = land_band(row.get("land_sqm"))
    if band is None:
        # A strata dwelling has no land of its own, so the band is not missing data —
        # it is inapplicable, and every apartment in a suburb is comparable regardless.
        if kind == "dwelling":
            band = "n/a"
        else:
            return None, SKIP_NO_LAND_BAND

    return CohortKey(suburb.title(), state, kind, beds, band), ""


def build(rows: Iterable[Dict[str, Any]]) -> Tuple[Dict[CohortKey, List[Dict[str, Any]]],
                                                   Counter]:
    """(cohort -> its listings, why-not counts)."""
    cohorts: Dict[CohortKey, List[Dict[str, Any]]] = defaultdict(list)
    skipped: Counter = Counter()
    for row in rows:
        key, why = key_for(row)
        if key is None:
            skipped[why] += 1
            continue
        cohorts[key].append(row)
    return cohorts, skipped


def report(cohorts, skipped, total: int) -> str:
    """The Phase 0 acceptance number, plus what it cost to get there."""
    keyed = sum(len(v) for v in cohorts.values())
    lines = [
        f"  {total:5} live priced listing(s)",
        f"  {keyed:5} grouped into {len(cohorts)} cohort(s)"
        f"  —  {keyed / len(cohorts):.1f} listings per provider call" if cohorts else "",
        f"  {sum(skipped.values()):5} not benchmarkable:",
    ]
    for why, n in skipped.most_common():
        lines.append(f"        {n:5}  {why}")
    if cohorts:
        biggest = sorted(cohorts.items(), key=lambda kv: -len(kv[1]))[:5]
        lines.append("")
        lines.append("  largest cohorts:")
        for key, members in biggest:
            lines.append(f"        {len(members):4}  {key.describe()}")
    return "\n".join(x for x in lines if x)


if __name__ == "__main__":
    import sqlite3
    import sys

    from config import DATABASE_PATH

    conn = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    live = [dict(r) for r in conn.execute(
        "SELECT * FROM buildings WHERE superseded_by IS NULL AND price > 0")]
    cohorts, skipped = build(live)
    print(report(cohorts, skipped, len(live)))
    sys.exit(0)
