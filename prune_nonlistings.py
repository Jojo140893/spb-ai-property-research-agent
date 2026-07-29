"""
Re-apply the extractor's quality gates to rows already in the database.

A row can only be judged against the rules that existed when it was harvested. When a
gate is added later — for instance that a row which is nothing but a wall of prices is a
transposed summary, not a listing — rows stored before it stay in the client's sheet.

This re-runs the CURRENT gates over each stored row's own `source_text` and reports the
rows that would no longer be extracted. Nothing is deleted without `--apply`, and a
backup is taken first.

Usage:
    python prune_nonlistings.py            # report only
    python prune_nonlistings.py --apply    # back up, then delete the rows listed
"""

import shutil
import sqlite3
import sys
from datetime import datetime

import config
from sources.spreadsheet_extract import _is_column_header, _listing_from_row


def judge(text: str) -> str:
    """Why the current extractor would reject this row, or "" if it would keep it."""
    if not text:
        return ""                      # nothing to re-judge; leave it alone
    if _is_column_header(text):
        return "a column-header row, not a listing"
    if _listing_from_row(text, [], "", "reharvest", "") is None:
        return "no longer parses as a listing (usually a wall of prices or a summary row)"
    return ""


def main(apply: bool = False) -> int:
    conn = sqlite3.connect(str(config.DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, builder_name, lot_address, price, source_text, source_channel "
        "FROM buildings").fetchall()

    doomed = []
    for r in rows:
        why = judge(r["source_text"] or "")
        if why:
            doomed.append((r, why))

    print(f"{len(rows)} row(s) in the database; {len(doomed)} would no longer be extracted.")
    if not doomed:
        return 0
    reasons = {}
    for _r, why in doomed:
        reasons[why] = reasons.get(why, 0) + 1
    for why, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>5}  {why}")
    print("\n  examples:")
    for r, why in doomed[:8]:
        print(f"    id={r['id']} {r['builder_name'] or '(none)'} "
              f"${(r['price'] or 0):,.0f}\n        {str(r['source_text'])[:104]!r}")

    if not apply:
        print("\n[i] report only — re-run with --apply to delete these rows.")
        return 0

    dest = str(config.DATABASE_PATH) + f".bak-prune-{datetime.now():%Y%m%d-%H%M%S}"
    try:
        shutil.copy2(str(config.DATABASE_PATH), dest)     # raises if it fails — intended
        print(f"\n[+] backup written: {dest}")
    except Exception as e:
        print(f"[ABORT] could not back up the database: {e}")
        return 1
    conn.executemany("DELETE FROM buildings WHERE id=?", [(r["id"],) for r, _ in doomed])
    conn.commit()
    left = conn.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
    print(f"[done] deleted {len(doomed)} row(s); {left} remain.")
    return 0


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv))
