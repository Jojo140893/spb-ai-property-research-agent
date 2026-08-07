"""
Take Proxima's live availability counter back out of the stored estate names.

    python backfill_proxima_estate_names.py            # report only
    python backfill_proxima_estate_names.py --apply

Proxima's project header is a NAME PLUS A COUNTER — "Ahlei (85/110)" means 85 of Ahlei's
110 lots are available — and the harvest stored the whole string as `estate_name`. The
counter moves whenever a lot sells or comes back, so the estate's own NAME changed
between harvests while nothing about the estate did:

    03/08/2026   estate_name = "Atchison and Kenny Wollongong Building A (2/305)"
    07/08/2026   estate_name = "Atchison and Kenny Wollongong Building A (3/305)"

sources/proxima.py now splits the two apart, so every lot harvested from here on stores
a stable name. This applies the same split to the 1,710 rows already stored.

WHY BACKFILL RATHER THAN LEAVE HISTORY ALONE. Because the fix alone does not reach them:
record_building fills `estate_name` with COALESCE(NULLIF(estate_name,''), ?), so a
stored non-empty name WINS over what the next harvest computes. Left alone, these rows
keep their counters permanently and only lots first seen after today get a clean name —
which is the same fragmentation, now with two conventions in one column. The dashboard
would go on showing one estate under several names indefinitely.

IT IS LOSSLESS. The counter is not deleted, it is moved: each row's own pair lands in
project_available / project_total, which is where it should have been all along. A
superseded row keeps the pair it was captured with, so the history of a project's
sell-through survives the repair rather than being flattened to today's numbers.

IDENTITY IS NOT TOUCHED. estate_name is not in database._HASH_FIELDS, and _variant_key
reads source_text, not this column — so unlike reparse_from_source.py (which fills
suburb and land_sqm, both hashed, and relies on supersede to clean up afterwards) this
rewrite cannot change a single content_hash. No re-insert, no supersede churn, and the
next harvest updates exactly the rows it updated before.

WHAT COUNTS AS A COUNTER is parse_project_title's business, not this script's — the
harvest and the repair have to agree on that or they will fight each other, and real
project names on this portal contain parentheses ("Ascenta Living (DBN Homes)",
"Creation Homes (Qld) Pty Ltd") that must survive both.
"""

import argparse
import collections
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DATABASE_PATH                              # noqa: E402
from database import ResearchDatabase                         # noqa: E402
from sources.proxima import parse_project_title               # noqa: E402

CHANNEL = "Proxima"


def backup(db_path: str) -> str:
    dest = f"{db_path}.bak-estatecounter-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(db_path, dest)          # raises if it fails — intended
    return dest


def plan(conn):
    """(updates, before_names, after_names) for every stored Proxima row.

    Superseded rows are included deliberately. They are an older capture of a real lot
    and their estate name is just as fragmented as a live one's; leaving them behind
    would mean "what changed since last week" still compares two spellings of one estate.
    """
    rows = conn.execute(
        "SELECT id, estate_name, project_available, project_total FROM buildings "
        "WHERE source_channel = ? AND estate_name IS NOT NULL AND estate_name <> ''",
        (CHANNEL,)).fetchall()

    updates = []
    before, after = collections.Counter(), collections.Counter()
    for row_id, stored, had_avail, had_total in rows:
        name, avail, total = parse_project_title(stored)
        before[stored] += 1
        after[name] += 1
        # Never write a blank over a name. parse_project_title already guarantees this
        # (a header that is only a counter comes back whole), but a silently emptied
        # estate column is exactly the failure this repair exists to prevent, so it is
        # checked here too rather than assumed.
        if not name:
            continue
        change = {}
        if name != stored:
            change["estate_name"] = name
        # Only fill the counters where they are not already stored: a row harvested
        # after the fix landed has today's pair, and the pair baked into an old name is
        # older than that.
        if avail is not None and had_avail is None:
            change["project_available"] = avail
        if total is not None and had_total is None:
            change["project_total"] = total
        if change:
            updates.append((row_id, change))
    return updates, before, after


def main(apply_changes: bool) -> int:
    # project_available / project_total are new columns. _init_db's ALTER TABLE loop is
    # idempotent and additive, so this is safe on every run and on a database that has
    # already been migrated — and it means the report works before any harvest has run.
    ResearchDatabase()
    conn = sqlite3.connect(str(DATABASE_PATH))
    updates, before, after = plan(conn)

    print(f"  {CHANNEL} rows with an estate name : {sum(before.values()):,}")
    print(f"  rows to rewrite                  : {len(updates):,}")
    print(f"  distinct estate names            : {len(before)} -> {len(after)}")

    renamed = collections.Counter()
    for _rid, change in updates:
        for col in change:
            renamed[col] += 1
    for col, n in sorted(renamed.items()):
        print(f"      {n:6}  {col}")

    collapsed = [(name, n) for name, n in after.most_common()
                 if sum(1 for b in before if parse_project_title(b)[0] == name) > 1]
    if collapsed:
        print(f"\n  estates currently stored under more than one name ({len(collapsed)}):")
        for name, n in collapsed[:10]:
            spellings = sorted(b for b in before if parse_project_title(b)[0] == name)
            print(f"      {name[:52]:52} {n:5} row(s), {len(spellings)} spellings")
            for s in spellings[:3]:
                print(f"          {s[:70]}")

    # Any other channel that has picked up the same shape is worth SEEING rather than
    # silently skipping — this scopes to Proxima because that is the only source whose
    # header carries a counter, and that is a fact about today's data, not a guarantee.
    others = conn.execute(
        "SELECT source_channel, COUNT(*) FROM buildings "
        "WHERE source_channel <> ? AND estate_name IS NOT NULL AND estate_name <> '' "
        "GROUP BY source_channel", (CHANNEL,)).fetchall()
    stray = [(ch, n) for ch, n in others
             if any(parse_project_title(r[0])[1] is not None for r in conn.execute(
                 "SELECT estate_name FROM buildings WHERE source_channel = ? "
                 "AND estate_name IS NOT NULL AND estate_name <> ''", (ch,)))]
    for ch, n in stray:
        print(f"\n  NOTE: {ch} also has estate names ending in a counter ({n} row(s) "
              f"in that channel) — not touched, this script is scoped to {CHANNEL}.")

    if not apply_changes:
        print("\n  DRY RUN. Re-run with --apply to write.")
        return 0

    if not updates:
        print("\n  nothing to do.")
        return 0

    dest = backup(str(DATABASE_PATH))
    print(f"\n  backup: {dest}")
    for row_id, change in updates:
        sets = ", ".join(f"{c} = ?" for c in change)
        conn.execute(f"UPDATE buildings SET {sets} WHERE id = ?",
                     list(change.values()) + [row_id])
    conn.commit()
    print(f"  APPLIED: {len(updates):,} row(s) rewritten. "
          f"Run deploy.ps1 to publish the dashboard.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the cleaned names")
    sys.exit(main(ap.parse_args().apply))
