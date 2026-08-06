"""
Recover the package totals that a space-as-thousands-separator threw away.

PRICE_RE used to accept a space where a comma belongs, so on a stocklist row a money
amount was glued to whatever numeric column followed it:

    Rooty Hill Available 6 Gardner Road Riverton MOD Coastal 4 2 2 450.1
        $896,000  202.15  $550,411  $1,446,411  900 Due Oct 26
        land ─┘   house ┘  build ─┘  TOTAL ───┘  weekly rent

"$896,000 202" parsed as 896000202 and "$1,446,411 900" as 1446411900. Both blew past the
$5,000,000 plausibility ceiling and were discarded, so max() of the survivors published
the BUILD component -- $550,411 for a $1,446,411 package.

sources/adaptive_extract.py and sources/scraper_base.py now parse this correctly, but a
stored row keeps whatever the harvest wrote. This re-reads each row's OWN source_text
with the fixed parser and writes back the total it states.

WHY THIS IS SAFE TO WRITE BACK
  * `price` is deliberately excluded from building_content_hash (database.py:221-241) so
    that a price move updates a row in place. Correcting it cannot re-identify the row or
    cause the next harvest to insert a duplicate. suburb, lot_number and land_sqm ARE in
    the hash and are never touched here.
  * A price is only ever RAISED, and only to a figure the row's own text states. Nothing
    is inferred, averaged or carried across rows. A row whose re-parse yields nothing, or
    yields the same or a lower number, is left exactly as it is.

Dry run by default. Pass --apply to write.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DATABASE_PATH                      # noqa: E402
from sources.adaptive_extract import parse_fields     # noqa: E402

# Ignore anything smaller than this. Real corrections here run to six figures; a tiny
# delta is a rounding artefact, not a recovered total.
MIN_CORRECTION = 2_000.0


def find_understated(conn):
    """Rows whose own source_text states a package total above what we published."""
    conn.row_factory = sqlite3.Row
    out = []
    for r in conn.execute(
            "SELECT id, price, land_price, build_price, source_channel, builder_name, "
            "       superseded_by, COALESCE(source_text,'') AS txt "
            "  FROM buildings "
            " WHERE source_text IS NOT NULL AND source_text <> ''"):
        old = float(r["price"] or 0)
        if not old:
            continue
        try:
            f = parse_fields(r["txt"])
        except Exception:
            continue                      # a row the parser cannot read is left alone
        new = f.get("advertised_package_price")
        if not new or new - old < MIN_CORRECTION:
            continue
        out.append({
            "id": r["id"], "old": old, "new": float(new),
            "land": f.get("land_price"), "build": f.get("build_price"),
            "old_land": r["land_price"], "old_build": r["build_price"],
            "channel": r["source_channel"], "builder": r["builder_name"] or "",
            "live": not r["superseded_by"], "txt": r["txt"],
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the corrections")
    ap.add_argument("--limit", type=int, default=12, help="rows to print")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DATABASE_PATH))
    rows = find_understated(conn)
    live = [r for r in rows if r["live"]]
    gap = sum(r["new"] - r["old"] for r in live)

    print(f"rows whose stored price is below the total their own text states: {len(rows)}")
    print(f"  of those, live (not superseded): {len(live)}")
    print(f"  combined understatement on live rows: ${gap:,.0f}")
    by = {}
    for r in live:
        by[r["channel"]] = by.get(r["channel"], 0) + 1
    print(f"  by channel: {by}\n")

    for r in sorted(live, key=lambda x: -(x["new"] - x["old"]))[:args.limit]:
        print(f"  id {r['id']:>5} {r['builder'][:22]:22} "
              f"${r['old']:>10,.0f} -> ${r['new']:>11,.0f}  (+${r['new']-r['old']:,.0f})")
        print(f"        {r['txt'][:96]}")

    if not args.apply:
        print(f"\nDRY RUN. Re-run with --apply to write {len(rows)} correction(s).")
        return 0

    n = 0
    for r in rows:
        # land/build are only filled in where the re-parse actually found them, so a
        # correction can never blank a breakdown the database already holds.
        conn.execute(
            "UPDATE buildings SET price = ?, "
            "       land_price  = COALESCE(?, land_price), "
            "       build_price = COALESCE(?, build_price) "
            " WHERE id = ?",
            (r["new"], r["land"], r["build"], r["id"]))
        n += 1
    conn.commit()
    print(f"\nAPPLIED: {n} row(s) corrected, ${gap:,.0f} of understatement removed "
          f"from the live table.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
