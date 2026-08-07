"""
Catalogue exposure — how much of our stock is priced above the market, by builder and
by suburb.

Not asked for explicitly, but it is the direct answer to the question Coleen actually put
on 3 Aug: whether all 6,400 packages we sell are more expensive than what a buyer could
find on the open market. Benchmark A answers that one listing at a time; this answers it
for the catalogue.

Cheap once cohorts exist, because it reuses their verdicts rather than issuing its own
provider calls.

    python catalogue_exposure.py                 # summary
    python catalogue_exposure.py --by suburb     # or builder
"""

import argparse
import collections
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark_competitive import (VERDICT_FLAG, VERDICT_NONE, VERDICT_PASS,  # noqa: E402
                                   evaluate)
from comp_provider import get_provider                                        # noqa: E402


def summarise(rows: Iterable[Dict[str, Any]], provider=None, by: str = "builder"
              ) -> Dict[str, Dict[str, int]]:
    """{group: {flag, pass, insufficient, n}} — one entry per builder or suburb.

    A group where everything is "insufficient" is reported as such rather than as a clean
    bill of health: no verdict is not the same as passing, and rolling the two together
    would be the exact laundering this codebase keeps refusing.
    """
    provider = provider or get_provider()
    out: Dict[str, Dict[str, int]] = collections.defaultdict(
        lambda: {VERDICT_FLAG: 0, VERDICT_PASS: 0, VERDICT_NONE: 0, "n": 0})
    # One verdict per COHORT, fanned out — the whole reason cohorts exist. Keyed on what
    # a comparable set is built from, so two listings alike in all of it share a call.
    cache: Dict[tuple, str] = {}
    import suburb_quality
    for row in rows:
        # Group and cache on the RESOLVED locality. Off the raw column, `--by suburb`
        # printed 'IN TERNAL BALCONY TOTAL', '[Haven]' and 'GARAGE' as headings in a
        # report about which places our stock is overpriced in — and the cache key split
        # 'GLENVALE' from 'Glenvale', so the same cohort was paid for twice.
        located, _why = suburb_quality.resolve(row)
        if not suburb_quality.is_located(_why):
            located = ""
        if by == "suburb":
            # NOT the builder name. The fallback chain ended in builder_name for both
            # groupings, so a row whose suburb could not be established was filed under
            # 'Hermitage Homes' (154 rows) in a report about which SUBURBS our stock is
            # overpriced in. Its own bucket instead, named, because how much of the
            # catalogue cannot be placed at all is part of what this report is for.
            group = located or "(suburb not recorded)"
        else:
            group = str(row.get(by) or "").strip() or row.get("builder_name") or ""
        group = str(group).strip() or "(unnamed)"
        key = (located.lower(), str(row.get("state") or "").upper(),
               row.get("bedrooms"), row.get("land_sqm"), row.get("price"))
        verdict = cache.get(key)
        if verdict is None:
            verdict = evaluate(row, provider=provider).verdict
            cache[key] = verdict
        out[group][verdict] += 1
        out[group]["n"] += 1
    return dict(out)


def report(summary: Dict[str, Dict[str, int]], by: str) -> str:
    lines = [f"  {'group':34} {'rows':>6} {'flagged':>8} {'passed':>7} {'no verdict':>11}"]
    total = collections.Counter()
    for group, c in sorted(summary.items(), key=lambda kv: -kv[1][VERDICT_FLAG]):
        lines.append(f"  {group[:34]:34} {c['n']:6} {c[VERDICT_FLAG]:8} "
                     f"{c[VERDICT_PASS]:7} {c[VERDICT_NONE]:11}")
        for k in (VERDICT_FLAG, VERDICT_PASS, VERDICT_NONE, "n"):
            total[k] += c[k]
    lines.append(f"  {'':34} {'-' * 34}")
    lines.append(f"  {'ALL':34} {total['n']:6} {total[VERDICT_FLAG]:8} "
                 f"{total[VERDICT_PASS]:7} {total[VERDICT_NONE]:11}")
    if total[VERDICT_NONE] == total["n"]:
        lines.append("")
        lines.append("  Every row is 'no verdict'. That is not a clean bill of health — it")
        lines.append("  means no comparables provider is licensed, so nothing was checked.")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--by", choices=("builder", "suburb"), default="builder")
    ap.add_argument("--limit", type=int, default=0, help="rows to check (0 = all)")
    args = ap.parse_args()

    import sqlite3
    from config import DATABASE_PATH
    conn = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows: List[Dict[str, Any]] = [dict(r) for r in conn.execute(
        "SELECT * FROM buildings WHERE superseded_by IS NULL AND price > 0")]
    if args.limit:
        rows = rows[:args.limit]
    print(f"\n  catalogue exposure over {len(rows):,} live priced listing(s)\n")
    print(report(summarise(rows, by=args.by), args.by))
    return 0


if __name__ == "__main__":
    sys.exit(main())
