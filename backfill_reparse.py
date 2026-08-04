"""
Fill empty listing columns from the row's own stored text.

    python backfill_reparse.py            # report only, changes nothing
    python backfill_reparse.py --apply    # write the recovered values

Strictly additive by construction: stocklist_reparse.recover() refuses to return a field
the row already holds, so a stored value can never be overwritten here. If a rule turns
out to be wrong, the cost is a blank that became a wrong value — never a right value
that became a wrong one.

The report prints, for every field, the distribution of the values it is about to write
beside the distribution of the values already in the database. Those two should look
alike: recovered house sizes that cluster at 400 m2 when known ones cluster at 190 would
mean the rules are reading the land column, and no amount of per-cohort review would
have caught it. Read the comparison before passing --apply.
"""

import argparse
import collections
import sqlite3
import statistics
import sys

import config
import stocklist_reparse

FIELDS = ("house_sqm", "land_sqm", "bedrooms", "bathrooms", "car_spaces",
          "frontage_m", "suburb", "postcode", "lot_number", "estate_name",
          "street_address")


def _summary(values):
    nums = []
    for v in values:
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            pass
    if not nums:
        return f"n={len(values)} (not numeric)"
    nums.sort()
    return (f"n={len(nums):<5} median={statistics.median(nums):<9.1f} "
            f"p10={nums[int(len(nums) * .1)]:<8.1f} p90={nums[int(len(nums) * .9)]:<9.1f}")


def main(apply_changes):
    conn = sqlite3.connect(str(config.DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM buildings WHERE superseded_by IS NULL")]
    print(f"{len(rows)} live rows, {len(stocklist_reparse.RULES)} rules "
          f"across {len(stocklist_reparse._compiled())} cohorts\n")

    recovered = {}
    for row in rows:
        found = stocklist_reparse.recover(row)
        if found:
            recovered[row["id"]] = found

    new_values = collections.defaultdict(list)
    for found in recovered.values():
        for field, value in found.items():
            new_values[field].append(value)
    known_values = collections.defaultdict(list)
    for row in rows:
        for field in FIELDS:
            if row.get(field) not in (None, "", 0):
                known_values[field].append(row[field])

    print(f"{len(recovered)} of {len(rows)} rows gain at least one value\n")
    print(f"  {'field':<13} {'have now':>9} {'to add':>7}   "
          f"{'distribution of what is already stored':<52} distribution of what would be added")
    for field in FIELDS:
        add = new_values.get(field) or []
        if not add:
            continue
        have = known_values.get(field) or []
        print(f"  {field:<13} {len(have):>9} {len(add):>7}   {_summary(have):<52} {_summary(add)}")
        if field in ("suburb", "estate_name", "lot_number", "street_address"):
            sample = ", ".join(str(v) for v in add[:6])
            print(f"  {'':<13} {'':>9} {'':>7}   e.g. {sample}")

    # house cannot exceed land on a house-and-land package; this is the check that would
    # catch the two columns being read the wrong way round across a whole cohort.
    swapped = 0
    for row_id, found in recovered.items():
        row = next(r for r in rows if r["id"] == row_id)
        house = found.get("house_sqm", row.get("house_sqm"))
        land = found.get("land_sqm", row.get("land_sqm"))
        product = str(row.get("product_type") or "").lower()
        if house and land and "apartment" not in product and float(house) > float(land):
            swapped += 1
    print(f"\n  house larger than land after the fill (house-and-land only): {swapped}")

    if not apply_changes:
        print("\n(report only — pass --apply to write these values)")
        return 0

    cur = conn.cursor()
    written = 0
    for row_id, found in recovered.items():
        sets = ", ".join(f"{f}=?" for f in found)
        cur.execute(f"UPDATE buildings SET {sets} WHERE id=?",
                    (*found.values(), row_id))
        written += len(found)
    conn.commit()
    print(f"\napplied: {written} values across {len(recovered)} rows")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the recovered values")
    sys.exit(main(ap.parse_args().apply))
