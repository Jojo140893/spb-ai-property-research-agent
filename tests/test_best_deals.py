"""
Tests for the best-deals selection (Colin, 30 Jul): filter the database, mark the
listings going into the weekly promotion, export those.

The failure this suite exists to prevent is silent. `promo_selected` has to stay out
of BOTH the identity hash and record_building's column dict; if it leaks into either,
nothing breaks visibly — the next harvest just quietly empties or re-identifies
Colin's selection, and he finds out a week later when the export comes back short.
Both exclusions are by allow-list, so they hold by construction, and these tests pin
that construction rather than trusting it.
"""

import tempfile
from pathlib import Path

from database import ResearchDatabase, building_content_hash, _HASH_FIELDS


def _db():
    tmp = Path(tempfile.mkdtemp())
    return ResearchDatabase(db_path=tmp / "best_deals_test.db")


def _listing(**over):
    b = {
        "builder_name": "Placeholder Developments",
        "source_channel": "Proxima",
        "lot_address": "Lot 14 Unit 14, 7 Example Avenue, SAMPLETON, NSW, 2765",
        "suburb": "Sampleton", "state": "NSW", "lot_number": "14",
        "advertised_package_price": 829990.0, "land_size_sqm": 318.0,
        "availability_status": "For Sale", "attribution_scope": "builder",
    }
    b.update(over)
    return b


# ------------------------------------------------------- identity is unaffected

def test_the_flag_is_not_part_of_identity():
    """A marked listing and an unmarked one are the SAME listing."""
    assert "promo_selected" not in _HASH_FIELDS
    b = _listing()
    assert building_content_hash(b) == building_content_hash({**b, "promo_selected": 1})
    assert building_content_hash(b) == building_content_hash({**b, "promo_selected_at": "2026-08-01"})


def test_a_re_harvest_does_not_clear_the_mark():
    """The whole point of the column, and the thing that is silent when it breaks.

    record_building writes an explicit column dict, so a field it does not name is
    left alone on upsert. This proves it end to end rather than by reading the code:
    mark a listing, harvest the same listing again, the mark is still there.
    """
    db = _db()
    b = _listing()
    assert db.record_building(b) == "new"
    h = building_content_hash(b)
    assert db.set_promo_selection([h], True) == 1

    # the same lot comes back in the next harvest, at a new price
    again = _listing(advertised_package_price=845000.0, availability_status="Sold")
    assert building_content_hash(again) == h, "price/status must not change identity"
    assert db.record_building(again) == "updated"

    rows = db.get_promo_selected()
    assert len(rows) == 1, "the re-harvest cleared Colin's selection"
    assert rows[0]["price"] == 845000.0, "the price should still have been refreshed"
    assert rows[0]["availability_status"] == "Sold"


def test_a_re_harvest_does_not_mark_anything_new():
    db = _db()
    db.record_building(_listing())
    db.record_building(_listing(lot_number="15", lot_address="Lot 15, SAMPLETON, NSW, 2765"))
    assert db.get_promo_selected() == []
    db.record_building(_listing())
    assert db.get_promo_selected() == []


# ------------------------------------------------------------------ selecting

def test_mark_and_unmark():
    db = _db()
    a, b = _listing(), _listing(lot_number="15",
                                lot_address="Lot 15, SAMPLETON, NSW, 2765")
    db.record_building(a)
    db.record_building(b)
    ha, hb = building_content_hash(a), building_content_hash(b)

    assert db.set_promo_selection([ha, hb], True) == 2
    assert len(db.get_promo_selected()) == 2
    assert db.set_promo_selection([hb], False) == 1
    got = db.get_promo_selected()
    assert len(got) == 1 and got[0]["lot_number"] == "14"


def test_marking_stamps_a_time_and_unmarking_clears_it():
    """So "what changed this week" is answerable."""
    db = _db()
    b = _listing()
    db.record_building(b)
    h = building_content_hash(b)
    db.set_promo_selection([h], True)
    assert db.get_promo_selected()[0]["promo_selected_at"]
    db.set_promo_selection([h], False)
    conn = db._get_connection()
    row = conn.execute("SELECT promo_selected, promo_selected_at FROM buildings").fetchone()
    assert row[0] == 0 and row[1] is None


def test_an_unknown_key_marks_nothing():
    """A stale key from a browser must not silently mark the wrong lot."""
    db = _db()
    db.record_building(_listing())
    assert db.set_promo_selection(["v2:notarealhash"], True) == 0
    assert db.get_promo_selected() == []


def test_empty_and_junk_input_is_safe():
    db = _db()
    db.record_building(_listing())
    assert db.set_promo_selection([], True) == 0
    assert db.set_promo_selection(None, True) == 0
    assert db.set_promo_selection(["", None], True) == 0


def test_clear_returns_everything_to_unmarked():
    db = _db()
    for i in range(3):
        db.record_building(_listing(lot_number=str(i),
                                    lot_address=f"Lot {i}, SAMPLETON, NSW, 2765"))
    hs = [building_content_hash(_listing(lot_number=str(i),
                                         lot_address=f"Lot {i}, SAMPLETON, NSW, 2765"))
          for i in range(3)]
    db.set_promo_selection(hs, True)
    assert len(db.get_promo_selected()) == 3
    assert db.clear_promo_selection() == 3
    assert db.get_promo_selected() == []


def test_a_big_selection_is_not_truncated_by_sql_limits():
    """SQLite caps bound variables, so the update is chunked. 900 > the 400 chunk."""
    db = _db()
    hs = []
    for i in range(900):
        b = _listing(lot_number=str(i), lot_address=f"Lot {i}, SAMPLETON, NSW, 2765")
        db.record_building(b)
        hs.append(building_content_hash(b))
    assert db.set_promo_selection(hs, True) == 900
    assert len(db.get_promo_selected()) == 900


# -------------------------------------------------------------------- export

def test_the_snapshot_carries_what_the_selection_needs():
    """Without a stable key in the snapshot the dashboard cannot mark anything."""
    from build_web import BUILDING_FIELDS
    assert "content_hash" in BUILDING_FIELDS, "no stable row key reaches the browser"
    assert "promo_selected" in BUILDING_FIELDS, "the published selection never reaches the browser"


def test_the_csv_export_exists_and_uses_the_normal_columns():
    from export_csv import BUILDING_COLS, export_best_deals
    labels = [lbl for _k, lbl in BUILDING_COLS]
    for expected in ("Builder / Development", "Address", "Package Price", "Postcode"):
        assert expected in labels, expected


def test_the_export_is_written_even_when_nothing_is_marked():
    """A missing file reads as a broken export; an empty one reads as 'none marked'."""
    import config, export_csv
    db = _db()
    db.record_building(_listing())
    real = config.OUTPUT_DIR
    tmp = Path(tempfile.mkdtemp())
    config.OUTPUT_DIR = tmp
    try:
        path = export_csv.export_best_deals(db)
        assert path.exists()
        text = path.read_text(encoding="utf-8-sig").strip().splitlines()
        assert len(text) == 1, "expected a header and no rows"
    finally:
        config.OUTPUT_DIR = real


def run_all():
    tests = [
        ("flag is outside identity", test_the_flag_is_not_part_of_identity),
        ("re-harvest keeps the mark", test_a_re_harvest_does_not_clear_the_mark),
        ("re-harvest marks nothing new", test_a_re_harvest_does_not_mark_anything_new),
        ("mark and unmark", test_mark_and_unmark),
        ("marking stamps a time", test_marking_stamps_a_time_and_unmarking_clears_it),
        ("unknown key marks nothing", test_an_unknown_key_marks_nothing),
        ("empty/junk input is safe", test_empty_and_junk_input_is_safe),
        ("clear unmarks everything", test_clear_returns_everything_to_unmarked),
        ("900 keys are not truncated", test_a_big_selection_is_not_truncated_by_sql_limits),
        ("snapshot carries key + flag", test_the_snapshot_carries_what_the_selection_needs),
        ("csv uses the normal columns", test_the_csv_export_exists_and_uses_the_normal_columns),
        ("empty export still written", test_the_export_is_written_even_when_nothing_is_marked),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] best-deals: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] best-deals: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
