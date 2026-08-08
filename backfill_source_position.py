"""Give already-stored rows the sheet name and row number they were read from.

Colin, 9 Aug: "if it is in the sheet I need the row number of that sheet where that
building is". The extractor records it from now on (sources/spreadsheet_extract.py), but
the 4,200-odd rows already in the table were captured before that and carry only the file
URL. A workbook of 400 lines and no line number is barely better than no link.

HOW IT RECOVERS SOMETHING NOBODY WROTE DOWN. Every stocklist row stores `source_text` —
the byte-identical text of the line it came from, kept because it is the strongest
evidence two captures are one listing (see supersede_duplicates.text_key). So the file is
re-fetched, re-parsed with the new extractor, and each stored row is matched to the
re-parsed row carrying the same text. That yields its position without a re-harvest and
without touching anything the extractor might now read differently.

ONLY TWO COLUMNS ARE WRITTEN, AND NEITHER IS IN content_hash. `_HASH_FIELDS` decides row
identity, and this file is the second attempt at a backfill in this repo — the first one
wrote `suburb` and `builder_name`, which ARE hashed, and every touched row lost its
identity and was re-inserted on the next harvest. 777 duplicate captures came from that.
source_sheet and source_row are deliberately outside the hash: a lot that shifts down two
rows when a builder inserts a header is the same lot.

A row whose text matches NOTHING in the re-fetched file is left alone and counted. That is
the honest outcome for a lot the builder has since removed, and guessing a nearby row
number would put Colin on the wrong line — worse than sending him to the file.

    python backfill_source_position.py              # dry run, reports what it would fill
    python backfill_source_position.py --apply
"""
import argparse
import re
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sources.scraper_base import normalise_money_spacing                  # noqa: E402

DB = Path(__file__).resolve().parent / "spb_research_audit.db"

# Anything the stocklist extractor can re-read. Restricting this to `.xlsx` was wrong
# and the measurement said so: of the live rows, 2,799 come from a PDF and only 463 from
# a .xlsx, with several hundred more behind Google Sheets URLs that carry no file
# extension at all. Filtering on ".xlsx" would have backfilled 13% of the stock and
# reported itself finished.
FETCHABLE = re.compile(r"\.(xlsx|xlsm|xls|pdf|csv|tsv)(\?|$)|docs\.google\.com/spreadsheets"
                       r"|sharepoint\.com|smartsheet", re.I)
# A folder listing is not a file. These answer 200 with an HTML page and yield nothing,
# so they are skipped by name rather than counted as failures.
FOLDER = re.compile(r"dropbox\.com/scl/fo/[^/]+/[A-Za-z0-9_-]+$", re.I)


def _norm(text):
    """The same normalisation supersede_duplicates.text_key uses, for the same reason:
    a vendor re-exporting "269" as "269 m2" is the same line."""
    t = normalise_money_spacing(str(text or ""))
    t = re.sub(r"(?<=\d)\s*(?:m2|m²|sqm|sq\.?m)\b", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip().lower()


def _targets(conn):
    """Rows that came out of a spreadsheet and have no position yet -> {url: [rows]}."""
    rows = conn.execute(
        "SELECT id, source_url, source_text FROM buildings "
        "WHERE COALESCE(source_row,0)=0 AND COALESCE(source_page,0)=0 "
        "AND TRIM(COALESCE(source_text,''))<>'' "
        "AND TRIM(COALESCE(source_url,''))<>''").fetchall()
    by_url = defaultdict(list)
    for rid, url, text in rows:
        u = str(url or "")
        if FETCHABLE.search(u) and not FOLDER.search(u):
            by_url[url].append((rid, text))
    return by_url


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write; otherwise report only")
    ap.add_argument("--limit-files", type=int, default=0,
                    help="stop after N files (for a quick look)")
    args = ap.parse_args(argv)

    if not DB.exists():
        print(f"!! no database at {DB}")
        return 2

    # Opening ResearchDatabase runs the ALTER TABLE for anything in
    # BUILDINGS_EXTRA_COLUMNS that the file does not have yet. Without this the script
    # dies on "no such column: source_row" against a database that simply has not been
    # opened by the app since the column was declared — which is every database until
    # the first harvest after this change.
    from database import ResearchDatabase
    ResearchDatabase()

    conn = sqlite3.connect(DB)
    by_url = _targets(conn)
    total_rows = sum(len(v) for v in by_url.values())
    print(f"  spreadsheet-sourced rows with no position : {total_rows}")
    print(f"  distinct files to re-read                 : {len(by_url)}")
    if not by_url:
        print("  nothing to do.")
        return 0

    from sources.remote_stocklist import fetch_stocklist
    from sources.spreadsheet_extract import extract_stocklist

    urls = sorted(by_url)
    if args.limit_files:
        urls = urls[:args.limit_files]

    found, outcome = {}, Counter()
    for n, url in enumerate(urls, 1):
        want = by_url[url]
        try:
            fetched = fetch_stocklist(url, label="backfill")
        except Exception as e:                                       # noqa: BLE001
            outcome[f"file unreachable ({type(e).__name__})"] += len(want)
            continue
        if not fetched:
            outcome["file returned nothing"] += len(want)
            continue

        pos = {}
        for f in fetched:
            data = getattr(f, "data", None) or getattr(f, "content", None)
            if not data:
                continue
            try:
                for listing in extract_stocklist(data, source_label=url):
                    key = _norm(listing.get("source_text"))
                    row_no = int(listing.get("source_row") or 0)
                    page_no = int(listing.get("source_page") or 0)
                    if key and (row_no or page_no):
                        pos.setdefault(key, (listing.get("source_sheet") or "",
                                             row_no, page_no))
            except Exception:                                        # noqa: BLE001
                continue

        hit = 0
        for rid, text in want:
            p = pos.get(_norm(text))
            if p:
                found[rid] = p
                hit += 1
        outcome["matched"] += hit
        outcome["no matching line in the file today"] += len(want) - hit
        print(f"    [{n}/{len(urls)}] {hit}/{len(want)} matched  {url[:72]}")

    print()
    for why, n in outcome.most_common():
        print(f"      {n:>6}  {why}")

    if not found:
        print("\n  nothing to write.")
        return 0
    if not args.apply:
        print(f"\n  would fill {len(found)} row(s). Re-run with --apply.")
        for rid, (sh, ln, pg) in list(found.items())[:5]:
            where = f"page {pg}" if pg else (f"sheet {sh!r}, row {ln}" if sh else f"row {ln}")
            print(f"      id {rid}: {where}")
        return 0

    backup = DB.with_name(f"{DB.stem}.backup-position-{datetime.now():%Y%m%d-%H%M%S}.db")
    shutil.copy2(DB, backup)
    print(f"\n  backup: {backup.name}")
    before = conn.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
    for rid, (sheet, line, page) in found.items():
        conn.execute(
            "UPDATE buildings SET source_sheet=?, source_row=?, source_page=? WHERE id=?",
            (sheet, line, page, rid))
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
    print(f"  filled {len(found)} row(s)")
    print(f"  row count {before} -> {after}"
          + ("  (unchanged, as it must be)" if before == after else "  <-- CHANGED, INVESTIGATE"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
