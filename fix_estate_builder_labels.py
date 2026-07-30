"""One-shot back-fill: take the ESTATE names out of builder_name.

229 rows were stored with an estate in builder_name at attribution_scope='builder',
because E-Agent's NSW House & Land page runs a "Builders" half and a "Projects" half and
the crawl could not see the divider. `sources/e_agent.py` no longer produces these; this
script fixes the rows already in the database.

What the source files actually say
----------------------------------
  Emerald Grove - Jordan Springs (107)  the Creation Homes NSW workbook, tab gid=0
  Kemps Estate - Austral         (107)  the SAME tab, reached by a `pli=1` spelling of
                                        the same URL — so these 107 rows are the other
                                        107 over again
  Bingara Gorge - Wilton           (5)  a separate "Dual Key" PDF
  Leppington Rise - Leppington     (10) a one-page PDF, harvested twice

  * The workbook's first cell reads "CREATION HOMES NSW STOCK LIST", and it covers
    eleven Creation Homes estates (Harvest Hill, Kemps, Emerald Grove, Sapphire, Birling,
    Bloomfield, Settlers Place, Gundari, Bingara Gorge, Parade Pemulwuy, Tranche 2).
    Both labels are therefore Creation Homes — a builder E-Agent already lists by name on
    its QLD page, so the spelling matches the 64 rows already in the database.
  * The Bingara Dual Key PDF carries the Creation Homes logo, and its four available lots
    (121/125/126/140 Bingara Drive) are line-for-line the workbook's own Bingara Gorge
    block at the same prices. The builder is Creation Homes, but only from the LOGO —
    an image, which the extractor cannot read. Setting the name here would put these rows
    permanently out of step with what the next harvest produces, so they are left blank at
    project scope and the four duplicate lots are retired instead (--retire-crossfile).
  * The Leppington Rise PDF names no builder anywhere: it is a Google Sheets export
    titled "Agent Stock List 26" whose banner is a street address. It stays blank at
    project scope, which is what makes the API show it as a development rather than
    inventing a builder for it.

Why suburb is rewritten on the Leppington rows
----------------------------------------------
Those 10 rows are 5 listings stored twice. An older extractor mistook the PDF's column
header for an estate banner, so 5 rows carry suburb='Floor Bathroom Living Office Garage
Price' and the other 5 carry the value the CURRENT extractor emits. suburb is part of
content_hash, which is why the two copies never collided. Aligning all 10 on the current
extractor's value makes them collide, so the identity migration can retire the 5 stale
ones — and keeps the rows in step with the next harvest.

Order of operations (builder_name is part of content_hash, so identity moves):

    python -X utf8 fix_estate_builder_labels.py --dry-run
    python -X utf8 fix_estate_builder_labels.py --apply
    python -X utf8 migrate_buildings_identity.py --force              # read the report
    python -X utf8 migrate_buildings_identity.py --force --dedupe     # retire the 112
    python -X utf8 fix_estate_builder_labels.py --retire-crossfile

Idempotent: re-running finds nothing to do.
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime

import config

# builder_name as stored  ->  what it should be
PLAN = {
    "Emerald Grove - Jordan Springs": {
        "builder_name": "Creation Homes", "attribution_scope": "builder",
        "builder_source": "e-agent stocklist banner",
    },
    "Kemps Estate - Austral": {
        "builder_name": "Creation Homes", "attribution_scope": "builder",
        "builder_source": "e-agent stocklist banner",
    },
    "Bingara Gorge - Wilton": {
        "builder_name": "", "attribution_scope": "project", "builder_source": "",
    },
    "Leppington Rise - Leppington": {
        "builder_name": "", "attribution_scope": "project", "builder_source": "",
        # what the current extractor reads off the file
        "suburb": "Agent Stock List 167 Ingleburn Road, Leppington",
        "estate_name": "Leppington Rise",       # E-Agent's own label; not part of identity
    },
}

# The four lots the Bingara "Dual Key" PDF and the Creation Homes workbook both publish.
# Keyed on price because neither file fills lot_number and the two render the row
# differently ("121 10C Bingara Drive ..." vs "Available 121 Bingara Drive Dual Key ...")
# — which is exactly why content_hash cannot see them as one listing.
CROSSFILE_DUP_PRICES = (1506000.0, 1523400.0, 1509200.0, 1528400.0)
DUAL_KEY_PDF = "069fe0_a66133fd3b6d4721b9c1fbc7c5b76ead.pdf"


def backup(db_path: str) -> str:
    dest = f"{db_path}.bak-estatelabels-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(db_path, dest)          # raises if it fails — intended
    return dest


def relabel(conn: sqlite3.Connection, apply: bool) -> int:
    changed = 0
    for stored, updates in PLAN.items():
        rows = conn.execute(
            "SELECT id, builder_name, attribution_scope, suburb, estate_name "
            "FROM buildings WHERE builder_name=?", (stored,)).fetchall()
        if not rows:
            print(f"  [-] {stored!r}: 0 rows (already done)")
            continue
        mark = "+" if apply else "="
        print(f"  [{mark}] {stored!r}: {len(rows)} row(s)")
        for col, val in updates.items():
            print(f"        {col:18} -> {val!r}")
        if apply:
            sets = ", ".join(f"{c}=?" for c in updates)
            conn.execute(f"UPDATE buildings SET {sets} WHERE builder_name=?",
                         (*updates.values(), stored))
        changed += len(rows)
    return changed


def retire_crossfile(conn: sqlite3.Connection, apply: bool) -> int:
    """Retire the Dual Key PDF's four lots, keeping the workbook copies — those name
    the builder, so they are strictly the more useful of the two."""
    removed = 0
    for price in CROSSFILE_DUP_PRICES:
        pdf = conn.execute(
            "SELECT id, builder_name, source_text FROM buildings "
            "WHERE price=? AND stocklist_file LIKE ? ", (price, f"%{DUAL_KEY_PDF}")).fetchall()
        keep = conn.execute(
            "SELECT id, builder_name FROM buildings "
            "WHERE price=? AND stocklist_file NOT LIKE ? AND suburb='Bingara Gorge'",
            (price, f"%{DUAL_KEY_PDF}")).fetchall()
        if not pdf:
            print(f"  [-] {price:.0f}: no Dual Key PDF row (already done)")
            continue
        if not keep:
            print(f"  [!] {price:.0f}: the workbook copy is GONE — keeping the PDF row "
                  f"(ids {[r[0] for r in pdf]}) rather than losing the listing")
            continue
        mark = "x" if apply else "="
        for r in pdf:
            print(f"  [{mark}] retire id={r[0]} (Dual Key PDF) — "
                  f"kept id={keep[0][0]} {keep[0][1]!r} @ {price:.0f}")
            if apply:
                conn.execute("DELETE FROM buildings WHERE id=?", (r[0],))
            removed += 1
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DATABASE_PATH))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--retire-crossfile", action="store_true",
                    help="retire the 4 lots the Dual Key PDF and the workbook both list "
                         "(run AFTER migrate_buildings_identity.py --force --dedupe)")
    a = ap.parse_args()
    if not (a.dry_run or a.apply or a.retire_crossfile):
        ap.error("choose --dry-run, --apply or --retire-crossfile")
    apply = bool(a.apply or a.retire_crossfile) and not a.dry_run

    print(f"[i] database: {a.db}")
    if apply:
        try:
            print(f"[+] backup written: {backup(a.db)}")
        except Exception as e:
            print(f"[ABORT] could not back up the database: {e}")
            return 1
    else:
        print("[i] --dry-run: nothing will be written")

    conn = sqlite3.connect(a.db)
    before = conn.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
    print(f"[i] {before} row(s) before\n")

    if a.retire_crossfile:
        print("Retiring cross-file duplicate lots (Bingara Gorge Dual Key):")
        n = retire_crossfile(conn, apply)
        print(f"\n  {n} row(s) {'retired' if apply else 'would be retired'}")
    else:
        print("Relabelling estate names out of builder_name:")
        n = relabel(conn, apply)
        print(f"\n  {n} row(s) {'relabelled' if apply else 'would be relabelled'}")

    if apply:
        conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
    print(f"[i] {after} row(s) after")
    conn.close()

    if apply and not a.retire_crossfile:
        print("\n[next] builder_name is part of content_hash, so identity has moved:")
        print("       python -X utf8 migrate_buildings_identity.py --force")
        print("       python -X utf8 migrate_buildings_identity.py --force --dedupe")
        print("       python -X utf8 fix_estate_builder_labels.py --retire-crossfile")
    return 0


if __name__ == "__main__":
    sys.exit(main())
