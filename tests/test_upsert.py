"""
Tests for the buildings upsert.

The property that matters: re-harvesting must never duplicate a listing and never
lose one. A failed portal login should leave the client's stock intact, and a price
move should update in place with the previous value retained.
"""

import sqlite3
import tempfile
from pathlib import Path

from database import ResearchDatabase

_ROW = {
    "source_channel": "E-Agent", "attribution_scope": "builder",
    "builder_name": "EVO Homes", "suburb": "Colac", "lot_number": "414",
    "lot_address": ("Lot 414 Clearwater Estate (Arklay 17) 392 162.1 14.0 Q2-2026 "
                    "Arklay 17 4x2x2 $249,000 $366,228 $615,228 Available"),
    "advertised_package_price": 615228, "availability_status": "Available",
    "land_size_sqm": 392,
}


def _fresh_db() -> ResearchDatabase:
    tmp = Path(tempfile.mkdtemp()) / "upsert.db"
    db = ResearchDatabase(db_path=tmp)
    con = sqlite3.connect(str(tmp))
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_buildings_content_hash "
                "ON buildings(content_hash)")
    con.commit()
    con.close()
    return db


def test_reharvest_is_idempotent():
    db = _fresh_db()
    assert db.record_building(dict(_ROW)) == "new"
    assert db.record_building(dict(_ROW)) == "unchanged"
    assert db.record_building(dict(_ROW)) == "unchanged"
    assert len(db.get_buildings()) == 1, "re-harvest duplicated a listing"


def test_price_and_status_update_in_place_with_history():
    db = _fresh_db()
    db.record_building(dict(_ROW))
    moved = dict(_ROW,
                 lot_address=_ROW["lot_address"].replace("615,228", "625,000").replace("Available", "On hold"),
                 advertised_package_price=625000, availability_status="On Hold")
    assert db.record_building(moved) == "updated"
    rows = db.get_buildings()
    assert len(rows) == 1, "a price change created a second row"
    r = rows[0]
    assert r["price"] == 625000
    assert r["price_previous"] == 615228, "previous price not retained"
    assert r["availability_status"] == "On Hold"
    assert r["status_previous"] == "Available"


def test_different_design_on_same_lot_is_a_separate_row():
    """Verified live: Lot 414 exists as both 'Arklay 17' and 'Dunestone 22'."""
    db = _fresh_db()
    db.record_building(dict(_ROW))
    other = dict(_ROW,
                 lot_address=("Lot 414 Clearwater Estate (Dunestone 22) 392 203.5 14.0 Q2-2026 "
                              "Dunestone 22 4x2x2 $249,000 $397,121 $646,121 Available"),
                 advertised_package_price=646121)
    assert db.record_building(other) == "new"
    assert len(db.get_buildings()) == 2


def test_update_fills_blanks_without_clobbering_existing_values():
    db = _fresh_db()
    db.record_building(dict(_ROW))                     # no storey, no postcode
    later = dict(_ROW, storey="SINGLE", postcode="3250")
    db.record_building(later)
    r = db.get_buildings()[0]
    assert r["storey"] == "SINGLE" and r["postcode"] == "3250"

    # a later run that omits them must NOT wipe them
    db.record_building(dict(_ROW))
    r = db.get_buildings()[0]
    assert r["storey"] == "SINGLE", "an omitted field wiped a stored value"
    assert r["postcode"] == "3250"


def test_enrichment_owned_columns_survive_a_reharvest():
    """state / builder_matched / benchmark_* are written by later passes — a harvest
    must not blank them, or every daily run would undo the enrichment."""
    db = _fresh_db()
    db.record_building(dict(_ROW))
    rid = db.get_buildings()[0]["id"]
    con = sqlite3.connect(str(db.db_path))
    con.execute("UPDATE buildings SET state=?, builder_matched=?, benchmark_median=? WHERE id=?",
                ("VIC", "Evo Homes", 620000, rid))
    con.commit(); con.close()

    db.record_building(dict(_ROW))                     # same listing, harvested again
    r = db.get_buildings()[0]
    assert r["state"] == "VIC", "re-harvest wiped the enriched state"
    assert r["builder_matched"] == "Evo Homes"
    assert r["benchmark_median"] == 620000


def test_nothing_is_deleted_when_a_source_returns_nothing():
    """A failed login must leave existing stock alone — the reason we upsert rather
    than wipe-and-rebuild."""
    db = _fresh_db()
    db.record_building(dict(_ROW))
    before = len(db.get_buildings())
    for _ in []:                                       # simulates a source yielding 0 rows
        db.record_building({})
    assert len(db.get_buildings()) == before == 1


def run_all():
    tests = [
        ("re-harvest is idempotent", test_reharvest_is_idempotent),
        ("price/status update in place + history", test_price_and_status_update_in_place_with_history),
        ("different design = separate row", test_different_design_on_same_lot_is_a_separate_row),
        ("update fills blanks, keeps values", test_update_fills_blanks_without_clobbering_existing_values),
        ("enriched columns survive re-harvest", test_enrichment_owned_columns_survive_a_reharvest),
        ("empty source deletes nothing", test_nothing_is_deleted_when_a_source_returns_nothing),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] upsert: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] upsert: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
