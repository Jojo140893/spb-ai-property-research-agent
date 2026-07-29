"""
Tests for listing identity (content_hash) and the column spec.

The pinned-hash test is the guard that stops an innocent refactor of
building_content_hash from silently duplicating the client's whole stock table on
the next harvest.
"""

import sqlite3
import tempfile
from pathlib import Path

from database import (BUILDINGS_EXTRA_COLUMNS, HASH_RECIPE_VERSION,
                      ResearchDatabase, building_content_hash)

# A real E-Agent row shape (VIC Regional stocklist).
_ROW = {
    "source_channel": "E-Agent",
    "attribution_scope": "builder",
    "builder_name": "EVO Homes",
    "suburb": "Winter Valley",
    "lot_number": "Lot 82",
    "house_design": "Empley 15",
    "land_size_sqm": 282,
    "lot_address": "Lot 82 Aberdeen 282 142.8 12.0 Sep-26 Empley 15 3x2x2 $205,000 $335,220 $540,220 Available",
}

# Pinned: if this changes, every stored row's identity changes and the next
# harvest re-inserts the entire table as new rows. Change it only deliberately,
# alongside a HASH_RECIPE_VERSION bump and a migration.
_PINNED = "v1:d62b7860f32b1f228e7e475b478149a98afbf9bae45e0585a020b7c4c62670d1"


def test_hash_matches_pinned_value():
    """If this fails, listing identity has changed — every stored row would be
    re-inserted as new on the next harvest. Only update _PINNED deliberately,
    together with a HASH_RECIPE_VERSION bump and a migration for existing rows."""
    assert building_content_hash(_ROW) == _PINNED, (
        "content_hash recipe changed — this WILL duplicate the stock table unless "
        "HASH_RECIPE_VERSION is bumped and existing rows are re-hashed")


def test_hash_ignores_volatile_fields():
    """Price, availability, title and date must NOT affect identity — they update in place."""
    base = building_content_hash(_ROW)
    for volatile in (
        {"price": 999_999}, {"advertised_package_price": 1_234_567},
        {"availability_status": "On Hold"}, {"title_status": "Titled"},
        {"date_checked": "01/01/2027"}, {"extraction_confidence": 0.1},
        {"incentive_amount": 15000},
        # written by the enrichment pass — must not change identity
        {"state": "VIC"}, {"builder_matched": "Evo Homes Pty Ltd"},
    ):
        assert building_content_hash({**_ROW, **volatile}) == base, f"{volatile} changed the hash"


def test_hash_distinguishes_real_listings():
    """Different lots / builders / suburbs must be different rows."""
    base = building_content_hash(_ROW)
    for distinct in ({"lot_number": "Lot 83"}, {"builder_name": "Hattan Homes"},
                     {"suburb": "Tarneit"}, {"house_design": "Westgarth 22"},
                     {"land_size_sqm": 400}, {"attribution_scope": "state_pooled"}):
        assert building_content_hash({**_ROW, **distinct}) != base, f"{distinct} collided"


def test_hash_separates_designs_on_the_same_lot():
    """Verified live: the VIC stocklist lists Lot 414 twice, as "Arklay 17" and
    "Dunestone 22" — different packages at different prices, so different rows.
    NSW does the same with "Vesper SG" / "Vesper DG"."""
    base = {"source_channel": "E-Agent", "attribution_scope": "state_pooled",
            "builder_name": "", "suburb": "Colac", "lot_number": "414"}
    arklay = {**base, "lot_address": "Lot 414 Clearwater Estate (Arklay 17) 392 162.1 14.0 "
                                     "Q2-2026 Arklay 17 4x2x2 $249,000 $366,228 $615,228 Available"}
    dune = {**base, "lot_address": "Lot 414 Clearwater Estate (Dunestone 22) 392 203.5 14.0 "
                                   "Q2-2026 Dunestone 22 4x2x2 $249,000 $397,121 $646,121 Available"}
    assert building_content_hash(arklay) != building_content_hash(dune)

    # ...but the SAME package keeps its identity when price and status move
    moved = {**base, "lot_address": "Lot 414 Clearwater Estate (Arklay 17) 392 162.1 14.0 "
                                    "Q2-2026 Arklay 17 4x2x2 $249,000 $370,000 $625,000 On hold"}
    assert building_content_hash(arklay) == building_content_hash(moved)


def test_hash_never_empty_for_degenerate_rows():
    """A row with no lot number or design still gets a distinct, non-null hash."""
    a = building_content_hash({"source_channel": "E-Agent", "lot_address": "some blob A"})
    b = building_content_hash({"source_channel": "E-Agent", "lot_address": "some blob B"})
    assert a and b and a != b
    assert a.startswith(HASH_RECIPE_VERSION + ":")


def test_column_spec_types_are_valid():
    """Guards the ALTER TABLE f-string: only known types, no duplicates."""
    names = [c for c, _t in BUILDINGS_EXTRA_COLUMNS]
    assert len(names) == len(set(names)), "duplicate column in spec"
    for col, coltype in BUILDINGS_EXTRA_COLUMNS:
        assert coltype in ("TEXT", "REAL", "INTEGER"), f"{col} has bad type {coltype}"
        assert col.replace("_", "").isalnum(), f"{col} is not a safe identifier"
    spec = dict(BUILDINGS_EXTRA_COLUMNS)
    # postcode must be TEXT (leading-zero NT postcodes) and frontage numeric
    assert spec["postcode"] == "TEXT"
    assert spec["frontage_m"] == "REAL"


def test_migration_is_idempotent_and_adds_every_column():
    tmp = Path(tempfile.mkdtemp()) / "ident.db"
    ResearchDatabase(db_path=tmp)
    ResearchDatabase(db_path=tmp)          # second init must not error
    cols = [r[1] for r in sqlite3.connect(str(tmp)).execute("PRAGMA table_info(buildings)")]
    for col, _t in BUILDINGS_EXTRA_COLUMNS:
        assert col in cols, f"{col} missing after init"


def test_schema_meta_roundtrip():
    tmp = Path(tempfile.mkdtemp()) / "meta.db"
    db = ResearchDatabase(db_path=tmp)
    assert db.get_meta("buildings_schema_version") is None
    db.set_meta("buildings_schema_version", "2")
    assert db.get_meta("buildings_schema_version") == "2"
    db.set_meta("buildings_schema_version", "3")          # upsert, not duplicate
    assert db.get_meta("buildings_schema_version") == "3"


def run_all():
    tests = [
        ("hash matches pinned value", test_hash_matches_pinned_value),
        ("hash ignores volatile fields", test_hash_ignores_volatile_fields),
        ("hash distinguishes real listings", test_hash_distinguishes_real_listings),
        ("hash separates designs on same lot", test_hash_separates_designs_on_the_same_lot),
        ("hash never empty for degenerate rows", test_hash_never_empty_for_degenerate_rows),
        ("column spec types valid", test_column_spec_types_are_valid),
        ("migration idempotent + complete", test_migration_is_idempotent_and_adds_every_column),
        ("schema_meta roundtrip", test_schema_meta_roundtrip),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] identity: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] identity: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
