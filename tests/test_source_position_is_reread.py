"""A row number is a position, and a position is only true for the read that produced it.

Coleen clicked "Price list - row 24" on 9 Sep for a Tomorrow Homes lot read on 2 Sep and a
different property opened. Third report of the same thing. The cause was not the
extractor: it read row 24 correctly on the 2nd. It was the upsert. source_row sat in the
fill-if-empty list, so it was written once and never again, while the builder re-sorted
that workbook every day as stock sold.

Ahsan, 9 Sep: "you should verify the row every day and if it is removed from that row it
means it is sold or removed." The first half is here. The second is
repair_rows.find_stale_source_position.
"""

import sqlite3
import tempfile
from pathlib import Path

from database import ResearchDatabase

_ROW = {
    "source_channel": "E-Agent", "attribution_scope": "builder",
    "builder_name": "Tomorrow Homes", "suburb": "Fraser Rise", "lot_number": "2102",
    "lot_address": "2102 Racecourse Rd, Fraser Rise",
    "source_url": "https://e-agent.com.au/price-lists/tomorrow.xlsx",
    "source_sheet": "Stock", "source_row": 24,
    "advertised_package_price": 724841, "availability_status": "Available",
}


def _fresh_db() -> ResearchDatabase:
    tmp = Path(tempfile.mkdtemp()) / "position.db"
    db = ResearchDatabase(db_path=tmp)
    con = sqlite3.connect(str(tmp))
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_buildings_content_hash "
                "ON buildings(content_hash)")
    con.commit()
    con.close()
    return db


def _stored(db):
    return db.get_buildings()[0]


def test_a_lot_that_moved_down_the_sheet_gets_its_new_row_number():
    """The whole bug, in three lines. Stock above it sold, so it is row 11 now."""
    db = _fresh_db()
    db.record_building(dict(_ROW))
    assert _stored(db)["source_row"] == 24
    db.record_building(dict(_ROW, source_row=11))
    assert _stored(db)["source_row"] == 11, "the stored row number did not follow the sheet"


def test_the_sheet_it_lives_on_can_change_too():
    db = _fresh_db()
    db.record_building(dict(_ROW))
    db.record_building(dict(_ROW, source_sheet="Stage 3", source_row=7))
    assert _stored(db)["source_sheet"] == "Stage 3"
    assert _stored(db)["source_row"] == 7


def test_a_read_that_names_no_line_leaves_none_behind():
    """A lot that moved from a price list to a portal must not keep the workbook's row
    number. The position belongs to the document it was read in."""
    db = _fresh_db()
    db.record_building(dict(_ROW))
    db.record_building(dict(_ROW, source_url="https://tomorrowhomes.com.au/lot/2102",
                            source_sheet=None, source_row=None))
    assert not _stored(db)["source_row"]
    assert not _stored(db)["source_sheet"]


def test_a_run_that_read_no_document_changes_nothing():
    """An enrichment pass that supplies no source is not a reading of the file."""
    db = _fresh_db()
    db.record_building(dict(_ROW))
    db.record_building({k: v for k, v in _ROW.items()
                        if k not in ("source_url", "source_sheet", "source_row")})
    assert _stored(db)["source_row"] == 24


def test_a_page_number_follows_the_same_rule():
    db = _fresh_db()
    db.record_building(dict(_ROW, source_sheet=None, source_row=None, source_page=3))
    assert _stored(db)["source_page"] == 3
    db.record_building(dict(_ROW, source_sheet=None, source_row=None, source_page=5))
    assert _stored(db)["source_page"] == 5


def test_a_lot_its_source_lists_again_comes_back():
    """repair_rows retires a listing its own price list stopped carrying, and calls that
    reversible: "a listing withdrawn in error comes straight back the next time its
    source lists it again". Nothing cleared the marker, so it never did."""
    import sqlite3 as _sq
    from database import WITHDRAWN_FROM_SOURCE
    db = _fresh_db()
    db.record_building(dict(_ROW))
    rid = _stored(db)["id"]
    con = _sq.connect(str(db.db_path))
    con.execute("UPDATE buildings SET superseded_by=?, superseded_at=? WHERE id=?",
                (WITHDRAWN_FROM_SOURCE, "2026-09-01T00:00:00", rid))
    con.commit(); con.close()
    assert _stored(db)["superseded_by"] == WITHDRAWN_FROM_SOURCE
    db.record_building(dict(_ROW))                 # the source lists it again
    assert not _stored(db)["superseded_by"], "a re-listed lot stayed invisible"
    assert not _stored(db)["superseded_at"]


def test_the_other_supersessions_stay_permanent():
    """Duplicate, spec fragment and filter row are judgements about the ROW, not about
    whether the builder is still selling it. Reviving a duplicate double-counts stock."""
    import sqlite3 as _sq
    for marker in ("spec-table fragment, not a listing",
                   "spreadsheet filter row, not a listing",
                   "abc123deadbeef"):
        db = _fresh_db()
        db.record_building(dict(_ROW))
        rid = _stored(db)["id"]
        con = _sq.connect(str(db.db_path))
        con.execute("UPDATE buildings SET superseded_by=? WHERE id=?", (marker, rid))
        con.commit(); con.close()
        db.record_building(dict(_ROW))
        assert _stored(db)["superseded_by"] == marker, f"{marker!r} was wrongly revived"
