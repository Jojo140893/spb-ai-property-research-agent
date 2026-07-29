"""
One-shot migration: give every existing building a stable identity.

The 373 rows already in the DB were stored before `content_hash` existed, and most
of what Coleen asked for is sitting unparsed inside their `lot_address` blob. This
script:

  1. BACKS THE DATABASE UP first, and refuses to continue if the copy fails.
     (These are rows the client has already reviewed.)
  2. Backfills identity/detail fields from the stored text — never overwriting a
     value that is already there.
  3. Tags each row's attribution scope (state_pooled vs builder).
  4. Computes content_hash for every row.
  5. REPORTS hash collisions for a human to judge — it never deletes anything.
  6. Adds the unique index (only if there are no collisions) and records a
     schema_meta marker so re-running is a no-op.

Idempotent: safe to run repeatedly.

Usage:
    python migrate_buildings_identity.py            # migrate
    python migrate_buildings_identity.py --dry-run  # report only, change nothing
    python migrate_buildings_identity.py --force    # re-run even if already marked
    python migrate_buildings_identity.py --dedupe   # also drop EXACT duplicate rows
"""

import shutil
import sqlite3
import sys
from datetime import datetime

import config
from database import (HASH_RECIPE_VERSION, ResearchDatabase,
                      building_content_hash)
from sources.feature_extract import parse_listing_features

MARKER = "buildings_identity_backfilled"
RECIPE_MARKER = "buildings_hash_recipe"


def backup_db() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = str(config.DATABASE_PATH) + f".bak-{stamp}"
    shutil.copy2(str(config.DATABASE_PATH), dest)   # raises if it fails — intended
    return dest


def main(dry_run: bool = False, force: bool = False, dedupe: bool = False):
    db = ResearchDatabase()

    stored_recipe = db.get_meta(RECIPE_MARKER)
    if db.get_meta(MARKER) and stored_recipe == HASH_RECIPE_VERSION and not force:
        print(f"[i] already migrated ({MARKER}={db.get_meta(MARKER)}, "
              f"recipe={stored_recipe}). Use --force to re-run.")
        return 0
    if db.get_meta(MARKER) and stored_recipe != HASH_RECIPE_VERSION:
        print(f"[i] hash recipe changed ({stored_recipe or 'v1'} -> {HASH_RECIPE_VERSION}); "
              "re-hashing every row so the next harvest updates in place.")

    if not dry_run:
        try:
            dest = backup_db()
            print(f"[+] backup written: {dest}")
        except Exception as e:
            print(f"[ABORT] could not back up the database: {e}")
            return 1
    else:
        print("[i] --dry-run: no backup taken, nothing will be written")

    conn = sqlite3.connect(str(config.DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM buildings").fetchall()
    print(f"[+] {len(rows)} building(s) to process")

    if not dry_run:
        # Re-hashing in place can transiently give row A the hash row B still holds,
        # which the unique index would reject even though the final state is unique.
        # It is recreated below, and only if there are no collisions.
        conn.execute("DROP INDEX IF EXISTS idx_buildings_content_hash")

    filled = {k: 0 for k in ("availability_status", "storey", "lot_number", "estate_name",
                             "postcode", "frontage_m", "attribution_scope", "content_hash")}
    hashes = {}
    collisions = []

    for r in rows:
        rid = r["id"]
        text = r["lot_address"] or ""
        feats = parse_listing_features(text)
        updates = {}

        # never overwrite an existing value
        for col in ("availability_status", "storey", "lot_number", "estate_name",
                    "postcode", "frontage_m"):
            if not (r[col] if col in r.keys() else None) and feats.get(col) is not None:
                updates[col] = feats[col]

        if not (r["attribution_scope"] if "attribution_scope" in r.keys() else None):
            # E-Agent rows came from pooled state files; portal rows named their builder
            updates["attribution_scope"] = (
                "state_pooled" if (r["source_channel"] or "").startswith("E-Agent") else "builder")

        scope = updates.get("attribution_scope") or r["attribution_scope"]
        h = building_content_hash({
            "source_channel": r["source_channel"], "attribution_scope": scope,
            "builder_name": r["builder_name"], "suburb": r["suburb"],
            "lot_number": updates.get("lot_number") or r["lot_number"],
            "house_design": None, "land_size_sqm": r["land_sqm"],
            "lot_address": text,
            # present once a row has been re-harvested by the current extractor
            "source_text": (r["source_text"] if "source_text" in r.keys() else None),
        })
        updates["content_hash"] = h
        if not (r["first_seen"] if "first_seen" in r.keys() else None):
            updates["first_seen"] = r["date_checked"]
        updates["last_seen"] = r["date_checked"]

        hashes.setdefault(h, []).append(rid)

        for k in filled:
            if k in updates:
                filled[k] += 1

        if not dry_run and updates:
            sets = ", ".join(f"{k}=?" for k in updates)
            conn.execute(f"UPDATE buildings SET {sets} WHERE id=?",
                         (*updates.values(), rid))

    collisions = {h: ids for h, ids in hashes.items() if len(ids) > 1}

    # A collision between rows that are genuinely different is the price-only sibling
    # case the extractor numbers at harvest time (verified on the APLACE, Met Invest and
    # Paramount files). Re-derive that number here, in id order — which is the order the
    # rows were read out of the file — so the migration reproduces the identity the
    # extractor computed instead of collapsing the pair.
    renumbered = 0
    for h, ids in list(collisions.items()):
        group = conn.execute(
            "SELECT id, source_text, lot_address, price, source_channel, attribution_scope,"
            " builder_name, suburb, lot_number, land_sqm FROM buildings "
            f"WHERE id IN ({','.join('?' * len(ids))}) ORDER BY id", ids).fetchall()
        distinct = {(r["source_text"] or r["lot_address"], r["price"]) for r in group}
        if len(distinct) < 2:
            continue                      # true duplicates: leave them to be judged
        seen, ordinal = set(), 0
        for r in group:
            sig = (r["source_text"] or r["lot_address"], r["price"])
            if sig in seen:
                continue
            seen.add(sig)
            if ordinal:
                new_hash = building_content_hash({
                    "source_channel": r["source_channel"],
                    "attribution_scope": r["attribution_scope"],
                    "builder_name": r["builder_name"], "suburb": r["suburb"],
                    "lot_number": r["lot_number"], "house_design": None,
                    "land_size_sqm": r["land_sqm"], "lot_address": r["lot_address"],
                    "source_text": r["source_text"], "variant_ordinal": ordinal,
                })
                if not dry_run:
                    conn.execute("UPDATE buildings SET variant_ordinal=?, content_hash=? "
                                 "WHERE id=?", (ordinal, new_hash, r["id"]))
                hashes.setdefault(new_hash, []).append(r["id"])
                hashes[h].remove(r["id"])
                renumbered += 1
            ordinal += 1
    if renumbered:
        print(f"\n  [+] {renumbered} price-only sibling(s) renumbered so both keep a "
              f"distinct identity")
    collisions = {h: ids for h, ids in hashes.items() if len(ids) > 1}

    if not dry_run:
        conn.commit()

    print("\n  backfilled:")
    for k, v in filled.items():
        print(f"    {k:<22} {v:>4}")

    print(f"\n  distinct identities: {len(hashes)} for {len(rows)} row(s)")

    # Rows that are identical in EVERY field identity uses AND in their source text and
    # price are the same listing stored twice — normally impossible, but reachable when a
    # name is normalised (blanking a wrongly-derived builder can make two rows equal).
    # Only ever removed with --dedupe, keeping the oldest row so its history survives.
    if collisions and dedupe and not dry_run:
        removed = 0
        for h, ids in list(collisions.items()):
            group = conn.execute(
                "SELECT id, source_text, lot_address, price FROM buildings "
                f"WHERE id IN ({','.join('?' * len(ids))}) ORDER BY id", ids).fetchall()
            if len({(r["source_text"] or r["lot_address"], r["price"]) for r in group}) > 1:
                continue                              # genuinely different — leave alone
            for r in group[1:]:
                conn.execute("DELETE FROM buildings WHERE id=?", (r["id"],))
                hashes[h].remove(r["id"])
                removed += 1
        if removed:
            conn.commit()
            print(f"  [+] removed {removed} exact duplicate row(s), keeping the oldest of each")
        collisions = {h: ids for h, ids in hashes.items() if len(ids) > 1}

    if collisions:
        print(f"  [!] {len(collisions)} hash collision group(s) — NOT deleting anything.")
        print("      These are either genuine duplicates or listings the recipe can't tell apart:")
        for h, ids in list(collisions.items())[:8]:
            sample = conn.execute("SELECT lot_address FROM buildings WHERE id=?", (ids[0],)).fetchone()[0]
            print(f"        {len(ids)} rows (ids {ids[:5]}): {str(sample)[:76]}")
        print("      The unique index is NOT created while collisions exist — resolve first.")
    else:
        print("  no collisions")
        if not dry_run:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_buildings_content_hash "
                         "ON buildings(content_hash)")
            conn.commit()
            print("  [+] unique index on content_hash created")

    conn.close()

    if not dry_run:
        db.set_meta("buildings_schema_version", "2")
        db.set_meta(RECIPE_MARKER, HASH_RECIPE_VERSION)
        db.set_meta(MARKER, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        if collisions:
            db.set_meta("buildings_hash_collisions", str(len(collisions)))
        print("\n[done] schema_meta updated")
    return 0


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry-run" in sys.argv, force="--force" in sys.argv,
                  dedupe="--dedupe" in sys.argv))
