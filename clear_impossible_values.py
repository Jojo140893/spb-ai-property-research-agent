"""
Clear stored values that cannot be true, found by the 6 Aug production audit.

Three cohorts, all of them visible on the public dashboard:

  * 3 listings advertising 41 and 51 BEDROOMS. No brief can filter those out, because
    every "minimum N bedrooms" test passes.
  * 67 listings stating a dwelling under 40 m2 — several over $3m. Apartment schedules
    print internal area and BALCONY side by side, and the extractor's "smaller figure is
    the house" rule (correct for house-and-land) took the balcony:
        "G02 Available Prestige 3B2.5B2C 184.7m2 31.8m2"  -> a 31.8 m2 home at $4,000,000
  * 171 rows holding an empty string in floorplan_url. An empty string is TRUTHY in the
    columnar snapshot, so every renderer testing `if row.floorplan_url` emitted a dead
    anchor.

The extractors now refuse all three at write time (sources/proxima.py _count,
sources/adaptive_extract.py MIN_DWELLING_SQM). This clears what is already stored.

CLEARED, NOT CORRECTED. Which of an apartment's two figures is the dwelling cannot be
recovered from the row — nothing in it says "balcony" — so nothing is swapped or
guessed. A blank with a stated reason beats a plausible number.

Every column touched here is OUTSIDE building_content_hash (which reads source_channel,
attribution_scope, builder_name, suburb, lot_number, house_design and land_sqm), so this
cannot re-identify a row and make the next harvest insert a duplicate instead of updating
it. land_sqm is deliberately left alone for exactly that reason; the next harvest will
correct it, and supersede_duplicates.text_key will retire the old copy.

Dry run by default. Pass --apply to write.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DATABASE_PATH                                    # noqa: E402

# (label, count query, update statement)
REPAIRS = (
    ("bedrooms above 12",
     "SELECT COUNT(*) FROM buildings WHERE bedrooms > 12",
     "UPDATE buildings SET bedrooms = NULL WHERE bedrooms > 12"),
    ("bathrooms above 12",
     "SELECT COUNT(*) FROM buildings WHERE bathrooms > 12",
     "UPDATE buildings SET bathrooms = NULL WHERE bathrooms > 12"),
    ("car spaces above 12",
     "SELECT COUNT(*) FROM buildings WHERE car_spaces > 12",
     "UPDATE buildings SET car_spaces = NULL WHERE car_spaces > 12"),
    ("a dwelling under 40 m2",
     "SELECT COUNT(*) FROM buildings WHERE house_sqm > 0 AND house_sqm < 40",
     "UPDATE buildings SET house_sqm = NULL WHERE house_sqm > 0 AND house_sqm < 40"),
    ("floorplan_url as an empty string",
     "SELECT COUNT(*) FROM buildings WHERE floorplan_url = ''",
     "UPDATE buildings SET floorplan_url = NULL WHERE floorplan_url = ''"),
    ("listing_url as an empty string",
     "SELECT COUNT(*) FROM buildings WHERE listing_url = ''",
     "UPDATE buildings SET listing_url = NULL WHERE listing_url = ''"),
    ("brochure_url as an empty string",
     "SELECT COUNT(*) FROM buildings WHERE brochure_url = ''",
     "UPDATE buildings SET brochure_url = NULL WHERE brochure_url = ''"),
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the corrections")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DATABASE_PATH))
    total = 0
    for label, count_sql, update_sql in REPAIRS:
        n = conn.execute(count_sql).fetchone()[0]
        total += n
        print(f"  {n:5}  {label}")
        if args.apply and n:
            conn.execute(update_sql)
    if args.apply:
        conn.commit()
        print(f"\n  APPLIED: {total} value(s) cleared.")
        for label, count_sql, _ in REPAIRS:
            left = conn.execute(count_sql).fetchone()[0]
            if left:
                print(f"    STILL PRESENT: {left} {label}")
    else:
        print(f"\n  DRY RUN. {total} value(s) would be cleared. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
