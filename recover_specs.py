"""
Fill bed / bath / car on stored rows from the source text already held against them.

Only 18% of harvested rows carried a bedroom count, which is what stopped a client brief
matching anything: of 4,192 rows just 75 had bed+bath+car+house-size together. The counts
are stated in `source_text` on far more rows than that — the parser simply could not read
"3x2x2", "5 + 5 + 3", "4 | 2 | 2" or "3B2B2C". sources.feature_extract.parse_bed_bath_car
now does; this applies it to what is already stored, so no re-harvest is needed.

Safe by construction:
  * fills BLANKS only — an existing value is never overwritten
  * bed/bath/car are not part of building_content_hash, so no row identity moves and no
    re-hash is required (unlike builder_name or land_sqm)
  * backs the database up before writing
  * report-only unless --apply

Usage:
    python recover_specs.py            # report what would change
    python recover_specs.py --apply    # write it
"""

import shutil
import sqlite3
import sys
from datetime import datetime

import config
from sources.feature_extract import parse_bed_bath_car

FIELDS = ("bedrooms", "bathrooms", "car_spaces")


def main(apply: bool = False) -> int:
    if apply:
        dest = f"{config.DATABASE_PATH}.bak-specs-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy2(str(config.DATABASE_PATH), dest)   # raises if it fails — intended
        print(f"[+] backup written: {dest}")
    else:
        print("[i] report only — nothing will be written. Use --apply to commit.")

    conn = sqlite3.connect(str(config.DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM buildings").fetchall()

    before = {f: sum(1 for r in rows if r[f] is not None) for f in FIELDS}
    filled = {f: 0 for f in FIELDS}
    by_notation, agree, disagree, clashes = {}, 0, 0, []

    for r in rows:
        got = parse_bed_bath_car(r["source_text"] or r["lot_address"] or "")
        if not got:
            continue
        updates = {}
        for f in FIELDS:
            v = got.get(f)
            if v is None:
                continue
            if r[f] is None:
                updates[f] = v
                filled[f] += 1
            elif f == "bedrooms":
                # A row that already has a count is a free accuracy test: the parser must
                # reproduce it. A disagreement is either a parser bug or a bad stored value,
                # and either way a human should see it rather than have it silently applied.
                if int(r[f]) == int(v):
                    agree += 1
                else:
                    disagree += 1
                    if len(clashes) < 10:
                        clashes.append((r["id"], r["builder_name"], r[f], v,
                                        (r["source_text"] or "")[:76]))
        if updates:
            note = got.get("notation") or "?"
            by_notation[note] = by_notation.get(note, 0) + 1
            if apply:
                sets = ", ".join(f"{k}=?" for k in updates)
                conn.execute(f"UPDATE buildings SET {sets} WHERE id=?",
                             (*updates.values(), r["id"]))
    if apply:
        conn.commit()

    print("=" * 66)
    print(f"  {len(rows)} row(s) examined")
    print("=" * 66)
    for f in FIELDS:
        print(f"  {f:<12} {before[f]:>5} -> {before[f] + filled[f]:>5}  ({filled[f]:+})")
    if by_notation:
        print("\n  by notation:")
        for note, n in sorted(by_notation.items(), key=lambda kv: -kv[1]):
            print(f"    {note:<28} {n:>5}")
    print(f"\n  accuracy check against rows that already had a bedroom count:")
    print(f"    agree {agree}   disagree {disagree}")
    for rid, b, stored, parsed, text in clashes:
        print(f"      id={rid} {str(b)[:18]:<18} stored {stored} vs parsed {parsed}: {text}")
    if not apply:
        print("\n  (nothing written — re-run with --apply)")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv))
