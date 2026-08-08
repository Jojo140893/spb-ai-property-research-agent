"""Repair stored builder names that are a bare domain — WITHOUT touching row identity.

WHY THIS EXISTS. `enrich_buildings.py` used to guess a builder from the stocklist URL and
UPDATE the column afterwards. It stamped 192 rows "hattan.com.au" (67 still live) and 319
rows "Proxima". The write was removed at 3c5d11e and the file now refuses it structurally
(`_assert_no_hashed_fields`), so nothing is still producing these. But the rows already
written cannot heal themselves: their `content_hash` encodes `builder_key=''`, so the
harvest matches them and `record_building`'s COALESCE fill only ever fills a BLANK column —
it can never correct a non-empty wrong value. Left alone they are permanent.

WHY IT IS SAFE, AND WHY IT MUST BE DONE THIS WAY. It rewrites the COLUMN ONLY and never
`content_hash`. That is identity-inert by construction: `_HASH_FIELDS` contains
`builder_key`, so a row whose stored hash matched was necessarily produced by an identical
`builder_key`; the stored column never participates in matching. Verified by replaying
`record_building()` against a repaired row with a forwarded-shaped dict — the incoming hash
equalled the stored hash, it returned "unchanged", and the row count did not move.

WHAT WOULD HAPPEN IF YOU RECOMPUTED THE HASH INSTEAD — measured, not feared:
  * 42 of the 67 live rows' target hash is ALREADY HELD by another row, and
    `idx_buildings_content_hash` is UNIQUE, so those UPDATEs are rejected outright.
  * 186 `superseded_by` pointers currently reference those hashes and would dangle.
  * The 21 that did succeed would be orphaned, and the next harvest would INSERT fresh
    copies of them — the exact mechanism that produced 777 duplicate captures.
So: never rehash these rows, and never run `migrate_buildings_identity.py --force` or bump
`HASH_RECIPE_VERSION` against them.

Only the transformation `builder_names.py` already sanctions is applied: a bare domain
resolving to a builder whose name we already hold ("hattan.com.au" -> "Hattan Homes").
Nothing is merged on similarity. A domain we cannot resolve is left exactly as found and
reported, because an unresolved name beats an invented one.

    python repair_builder_domain_names.py              # dry run, prints every row
    python repair_builder_domain_names.py --apply       # backs up first, then writes
"""
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from builder_names import BuilderNameCanonicaliser, looks_like_domain  # noqa: E402

DB = Path(__file__).resolve().parent / "spb_research_audit.db"


def plan(conn):
    """Rows whose stored builder_name is a bare domain that resolves to a known builder."""
    known = [r[0] for r in conn.execute(
        "SELECT DISTINCT builder_name FROM buildings "
        "WHERE TRIM(COALESCE(builder_name,'')) <> ''") if r[0]]
    canon = BuilderNameCanonicaliser(known)

    rows = conn.execute(
        "SELECT id, builder_name, content_hash, superseded_by FROM buildings "
        "WHERE TRIM(COALESCE(builder_name,'')) <> ''").fetchall()

    fixable, unresolved = [], Counter()
    for rid, name, chash, superseded in rows:
        if not looks_like_domain(name):
            continue
        target = canon.canonical(name)
        if target and target != name:
            fixable.append((rid, name, target, chash, superseded))
        else:
            # A domain we hold no matching builder name for. Left as found: inventing
            # "Hattan" from "hattan.com.au" with no "Hattan Homes" to point at would be
            # exactly the guess this project forbids.
            unresolved[name] += 1
    return fixable, unresolved


def main():
    apply = "--apply" in sys.argv
    if not DB.exists():
        print(f"!! no database at {DB}")
        return 2

    conn = sqlite3.connect(DB)
    fixable, unresolved = plan(conn)

    by_change = Counter((old, new) for _, old, new, _, _ in fixable)
    live = sum(1 for _, _, _, _, s in fixable if s is None)

    print(f"{'DRY RUN — nothing written' if not apply else 'APPLYING'}")
    print()
    print(f"  rows to repair : {len(fixable)}  ({live} live, {len(fixable) - live} superseded)")
    for (old, new), n in by_change.most_common():
        print(f"      {n:>5}  {old!r}  ->  {new!r}")
    if unresolved:
        print()
        print("  left as found (no builder of that name is known):")
        for name, n in unresolved.most_common():
            print(f"      {n:>5}  {name!r}")

    if not fixable:
        print("\nNothing to do.")
        return 0

    if not apply:
        print("\nRe-run with --apply to write. A timestamped backup is taken first.")
        return 0

    backup = DB.with_name(
        f"{DB.stem}.backup-domainnames-{datetime.now():%Y%m%d-%H%M%S}.db")
    shutil.copy2(DB, backup)
    print(f"\n  backup: {backup.name}")

    before = conn.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]

    # content_hash in the WHERE clause is optimistic concurrency, not decoration: if a
    # harvest is running and has re-identified this row underneath us, the UPDATE matches
    # nothing instead of writing to a row that is no longer the one we planned against.
    matched = 0
    for rid, _old, new, chash, _s in fixable:
        cur = conn.execute(
            "UPDATE buildings SET builder_name = ? WHERE id = ? AND content_hash = ?",
            (new, rid, chash))
        matched += cur.rowcount
    conn.commit()

    after = conn.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
    print(f"  planned {len(fixable)}, updated {matched}"
          + ("" if matched == len(fixable)
             else f"  <-- {len(fixable) - matched} did not match; a harvest may be running"))
    print(f"  row count {before} -> {after}"
          + ("  (unchanged, as it must be)" if before == after else "  <-- CHANGED, INVESTIGATE"))
    return 0 if matched == len(fixable) and before == after else 1


if __name__ == "__main__":
    sys.exit(main())
