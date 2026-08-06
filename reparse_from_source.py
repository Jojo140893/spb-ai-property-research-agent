"""
Re-read each stocklist file with today's extractors and fill what the old ones missed.

backfill_reparse.py recovers fields from a row's OWN STORED TEXT. That cannot reach the
things this fixes, because they were never in the row:

  * THE SUBURB lives in a section banner above the rows ("Harvest Hill | 1377 Hue Hue
    Road, Wyee"), not in any column, so it is invisible to anything working from
    source_text alone. 2,835 live rows have no usable locality.
  * BED / BATH / CAR / LAND / HOUSE live in columns the sheet's header row names. The
    old extractor flattened every row to one string and scraped the numbers back out,
    which works for money and fails for bare numbers — 35% of rows lost their bedroom
    count that way, 63% their house size.

Both extractors were corrected. This applies those corrections to stock already stored,
by re-reading the file each row came from.

MATCHING. A stored row is matched to a freshly parsed one by its source_text, normalised
the same way supersede_duplicates.text_key normalises it. That is the row's own line from
the file; it is what identified duplicate captures reliably, and it is stable across the
extractor changes because none of them touch the flattened text.

STRICTLY ADDITIVE. A field is written only where the stored value is empty. A value that
is already there was read from the same file by an earlier run and may have been checked
by a human since; a re-parse is not grounds to overwrite it. So the worst case is a blank
that becomes wrong, never a right value that becomes wrong.

IDENTITY. suburb and land_sqm are both inputs to building_content_hash, so filling them
changes what the NEXT harvest computes and it will insert fresh rows rather than update
these. That is expected and self-healing: the source_text is unchanged, so
supersede_duplicates.text_key retires the old copies automatically.

    python reparse_from_source.py           # report only
    python reparse_from_source.py --apply
"""

import argparse
import collections
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DATABASE_PATH                                     # noqa: E402
from sources.scraper_base import normalise_money_spacing             # noqa: E402
from sources.spreadsheet_extract import extract_stocklist            # noqa: E402
from verify_against_source import fetch, fetchable                   # noqa: E402

# extractor field -> database column
FIELD_MAP = {
    "suburb": "suburb",
    "bedrooms": "bedrooms",
    "bathrooms": "bathrooms",
    "car_spaces": "car_spaces",
    "land_size_sqm": "land_sqm",
    "house_size_sqm": "house_sqm",
    "frontage_m": "frontage_m",
    "estate_name": "estate_name",
    "postcode": "postcode",
}


def _key(text: str) -> str:
    """The row's own line, normalised exactly as supersede_duplicates.text_key does."""
    t = normalise_money_spacing(str(text or ""))
    t = re.sub(r"(?<=\d)\s*(?:m2|m²|sqm|sq\.?m)\b", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip().lower()


def _empty(value) -> bool:
    return value is None or value == "" or value == 0


_GEO = None


def _replaceable_suburb(value, state: str) -> bool:
    """True when the stored suburb is blank OR is not a place.

    Strictly-additive is the right default for every other field, but it would leave the
    suburb column exactly as broken as it is: 1,796 rows do not hold a blank, they hold
    spreadsheet furniture — "7 Star Energy Rating", "One Part Contracts", "IN TERNAL
    BALCONY TOTAL", "COAST COUNCIL". Treating those as values worth protecting protects
    nothing. A string that resolves to no Australian locality is not a suburb, and
    replacing it with one the file actually names is not an overwrite.

    A value that DOES resolve is left alone, even if the re-parse disagrees with it.
    """
    if _empty(value):
        return True
    global _GEO
    if _GEO is None:
        from geo import SuburbGeoIndex
        _GEO = SuburbGeoIndex()
    if not _GEO.loaded:
        return False                      # no index: never touch an existing value
    return not _GEO.resolve_locality(str(value), state or "")


def _is_locality(value, state) -> bool:
    """Whether a value is a real Australian locality — the gate on every suburb written."""
    if _empty(value):
        return False
    global _GEO
    if _GEO is None:
        from geo import SuburbGeoIndex
        _GEO = SuburbGeoIndex()
    if not _GEO.loaded:
        return False
    return bool(_GEO.resolve_locality(str(value), str(state or "")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit-files", type=int, default=0)
    args = ap.parse_args()

    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM buildings WHERE superseded_by IS NULL AND source_text <> ''")]

    by_file = collections.defaultdict(list)
    for r in rows:
        url = r.get("stocklist_file") or r.get("source_url") or ""
        if fetchable(url):
            by_file[url].append(r)
    targets = sorted(by_file.items(), key=lambda kv: -len(kv[1]))
    if args.limit_files:
        targets = targets[:args.limit_files]

    print(f"  {len(rows):,} live rows with source text; "
          f"{sum(len(v) for v in by_file.values()):,} of them from {len(by_file)} "
          f"re-fetchable file(s)\n")

    filled = collections.Counter()
    updates = []           # (row_id, {column: value})
    unmatched = unreadable = 0

    for n, (url, stored) in enumerate(targets, 1):
        try:
            fresh = extract_stocklist(fetch(url), source_label=url)
        except Exception as exc:                                     # noqa: BLE001
            unreadable += 1
            print(f"  [{n:3}/{len(targets)}] UNREADABLE ({type(exc).__name__}) {url[:64]}")
            continue
        index = {}
        for f in fresh:
            k = _key(f.get("source_text") or "")
            if k:
                index.setdefault(k, f)

        hits = 0
        for row in stored:
            f = index.get(_key(row.get("source_text")))
            if not f:
                unmatched += 1
                continue
            hits += 1
            change = {}
            for src, col in FIELD_MAP.items():
                if col not in row:
                    continue
                value = f.get(src)
                if col == "suburb":
                    # Validate what is being WRITTEN, not only what is being replaced.
                    # The banner reader refuses junk, but parse_fields can still put a
                    # product type in this column, and 115 of the first 267 candidates
                    # were "Dual Key", "COAST COUNCIL", "Duplex" or "Land". Swapping one
                    # non-place for another is churn, not a repair.
                    if not _is_locality(value, row.get("state")):
                        continue
                    replaceable = _replaceable_suburb(row.get(col), row.get("state"))
                else:
                    replaceable = _empty(row.get(col))
                if replaceable and not _empty(value):
                    change[col] = value
                    filled[col] += 1
            if change:
                updates.append((row["id"], change))
        print(f"  [{n:3}/{len(targets)}] {len(stored):4} stored, {hits:4} matched  "
              f"{url.split('/')[-1][:46]}")

    print("\n  ---------------------------------------------------------------")
    print(f"  rows to enrich : {len(updates):,}")
    print(f"  rows unmatched : {unmatched:,}   files unreadable: {unreadable}")
    print("\n  fields that would be filled (blank -> value):")
    for col, n in filled.most_common():
        print(f"      {n:6}  {col}")

    if not args.apply:
        print("\n  DRY RUN. Re-run with --apply to write.")
        return 0

    for row_id, change in updates:
        sets = ", ".join(f"{c} = ?" for c in change)
        conn.execute(f"UPDATE buildings SET {sets} WHERE id = ?",
                     list(change.values()) + [row_id])
    conn.commit()
    print(f"\n  APPLIED: {len(updates):,} row(s) enriched, "
          f"{sum(filled.values()):,} field(s) filled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
