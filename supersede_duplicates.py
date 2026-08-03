"""
Mark older captures of a lot that is also stored fresher.

WHY THERE ARE ANY. `content_hash` includes `lot_number` and a key derived from the
source row's text. Both come out of the extractor, so when the extractor's output for
a lot changes between harvests — a field starts or stops being populated, a stocklist
reformats a line — the identity changes with it and the next run INSERTS a second row
instead of updating the first. Nothing is wrong with the upsert; it is doing exactly
what it was asked to. The identity simply is not stable across extractor changes, and
every improvement to extraction is therefore also a duplication event.

Found on 2026-08-01: 777 rows, 12% of the table, are surplus copies of a lot already
stored. One lot was in there three times —

    27 Jul  Direct Portal  lot_number '9'   no source text   $  962,351
    29 Jul  E-Agent        lot_number '9'   text populated   $1,058,877
    03 Aug  Direct Portal  lot_number NULL  text populated   $1,058,877

WHY THIS IS NOT "THE SAME LOT, CHEAPER ELSEWHERE". It looks like a price comparison
and it is not one. Of 225 lots carrying more than one price, 224 conflict WITHIN a
single channel — the same seller quoting two numbers, which cannot be a competing
offer — and in 152 the cheaper row is simply the OLDEST capture. Surfacing "cheaper
elsewhere" from this would recommend a July price that has since risen, which is worse
than saying nothing.

WHAT COUNTS AS THE SAME LOT. Deliberately conservative: same state, same suburb, same
address label, and an IDENTICAL spec (beds, baths, cars, house size, land size). Two
genuinely different packages on one lot differ in spec and are left alone — 36 groups
did, and they are not touched.

MARKED, NOT DELETED. The older capture is real history and its price is what was
advertised then. It keeps its row and gains a pointer to the row that replaced it.

    python supersede_duplicates.py --dry-run
    python supersede_duplicates.py
"""

import argparse
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

import config

FIELDS = ("superseded_by", "superseded_at")


def _norm(v):
    s = str(v or "").strip().lower()
    return s or None


def _parsed_date(value):
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value or "").strip(), fmt)
        except (ValueError, TypeError):
            continue
    return None


def lot_key(row):
    """Same physical lot AND the same product on it."""
    key = (_norm(row["state"]), _norm(row["suburb"]), _norm(row["lot_address"]),
           row["bedrooms"], row["bathrooms"], row["car_spaces"],
           row["house_sqm"], row["land_sqm"])
    # A row with no locality or no address label cannot be matched to anything with
    # confidence, so it is never superseded — the spec alone is not an identity.
    if key[0] is None or key[1] is None or key[2] is None:
        return None
    return key


def _freshness(row):
    """Newest wins. date_checked first, then last_seen, then insertion order.

    id is the final tie-break rather than anything clever: when two captures carry the
    same date the later INSERT is the later harvest, and picking deterministically
    matters more than picking cleverly — a run that chose differently each time would
    move Colin's selection around underneath him.
    """
    return (_parsed_date(row["date_checked"]) or datetime.min,
            _parsed_date(row["last_seen"]) or datetime.min,
            row["id"])


def find_superseded(rows):
    """[(loser_row, winner_row)] for every surplus capture."""
    groups = defaultdict(list)
    for r in rows:
        k = lot_key(r)
        if k is not None:
            groups[k].append(r)

    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=_freshness, reverse=True)
        winner = ordered[0]
        for loser in ordered[1:]:
            out.append((loser, winner))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = ap.parse_args(argv)

    print("=" * 66)
    print("  Supersede older captures of the same lot")
    print("=" * 66)

    if not args.dry_run:
        dest = f"{config.DATABASE_PATH}.bak-supersede-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy2(str(config.DATABASE_PATH), dest)
        print(f"[+] backup written: {dest}")
    else:
        print("[i] report only — nothing will be written.")

    # Instantiating ResearchDatabase is what runs the additive-column migration, so
    # the superseded_* columns exist before the first raw query touches them.
    from database import ResearchDatabase
    ResearchDatabase()

    conn = sqlite3.connect(str(config.DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM buildings")]
    pairs = find_superseded(rows)

    total = len(rows)
    live = total - len(pairs)
    print(f"    {total} row(s) stored")
    print(f"    {len(pairs)} superseded by a fresher capture of the same lot")
    print(f"    {live} distinct lot(s) remain live")

    if pairs:
        moved = [(l, w) for l, w in pairs
                 if round(float(l["price"] or 0), 2) != round(float(w["price"] or 0), 2)]
        cheaper_stale = sum(1 for l, w in moved
                            if float(l["price"] or 0) < float(w["price"] or 0))
        print()
        print(f"    of those, {len(moved)} had a price different from the fresher row")
        print(f"    and {cheaper_stale} were CHEAPER — the stale prices that would have been")
        print(f"    surfaced as 'deals' if superseded rows were left in the running")

    if not args.dry_run and pairs:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Clear first: a row that is no longer a duplicate (because the row that beat
        # it was itself removed, or the spec changed) must not stay flagged.
        conn.execute("UPDATE buildings SET superseded_by=NULL, superseded_at=NULL")
        conn.executemany(
            "UPDATE buildings SET superseded_by=?, superseded_at=? WHERE id=?",
            [(w["content_hash"], stamp, l["id"]) for l, w in pairs])
        conn.commit()
        print(f"\n    marked. Superseded rows keep their price and history; they are")
        print(f"    excluded from the benchmark and hidden from the stock table by default.")

    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
