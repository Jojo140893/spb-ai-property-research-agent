"""24 Oct 2026: two kinds of lot the build published with a suburb and no state, so no state
search reached them -- the parity bar "blank: 8 obvious lots hidden as state_unknown".

* APLACE by Glenville's 16 Oct list is national (QLD and VIC sections), so enrich voids its
  VIC page state for every row that proves nothing itself. Seven new lots open their lines
  with their SUBURB column -- "Joyner 7 New Road Park Lane Estate ...", "Coomera 117 New
  Road Novella Waters ..." -- and suburb_quality read Joyner and Coomera, both Queensland
  places and nowhere else, out of that text. Graded INFERRED_FROM_TEXT, the rows were then
  skipped by the very pass that gives such a reading its state.
* Creation Homes' lot 114 came by email: "Fwd: 1x House and Land package at Novella Waters
  Estate, Coomera priced at $1,582,900". Its brochure names no place. The subject pass read
  Coomera -- in one state only -- and wrote the suburb but not the state.
"""
import json
import sqlite3
from datetime import datetime

import pytest

APLACE = "https://www.e-agent.com.au/_files/ugd/069fe0_54a6156398fd42b5bf456b943be2330c.pdf"
VIC_ONLY = "https://www.e-agent.com.au/_files/ugd/069fe0_0000000000000000000000000000000b.pdf"
SUBJECT = "email:Fwd: 1x House and Land package at Novella Waters Estate, Coomera"
TODAY = datetime.now().strftime("%d/%m/%Y")


def _row(h, **kw):
    row = {"content_hash": h, "dedup_key": h, "builder_name": "APLACE by Glenville",
           "source_channel": "E-Agent", "attribution_scope": "builder",
           "availability_status": None, "price": 900000.0, "bedrooms": 4,
           "date_checked": TODAY, "first_seen": TODAY, "last_seen": TODAY,
           "source_url": APLACE, "stocklist_file": APLACE, "source_state_hint": "VIC"}
    row.update(kw)
    return row


def _rows():
    return [
        # the two lots of the national list that prove a state themselves
        _row("v2:bridgeman", lot_address="4 New Road, QLD", suburb="Bundaberg", state="QLD",
             estate_name="QLD", source_text="Bridgeman Downs 4 New Road Canopy Feb-27 787 "
             "Rectangular $1,150,000 DISPLAY 0.12 496.65 5 | 4.5 | 2 $1,279,600 $2,429,600"),
        _row("v2:hadar", lot_address="143 Hadar Way, VIC", suburb="Bundaberg", state="VIC",
             state_source="listing text", estate_name="VIC",
             source_text="Clyde North 143 Hadar Way Alma Jun-27 294 10.5 x 28 $397,000 "
             "Mackay 01 167.08 4 | 2.5 | 2 $344,300 $716,300 Aplace Exclusive"),
        # the lots this file left with no state
        _row("v2:joyner-7", lot_address="7 New Road, New Road Park Lane",
             estate_name="New Road Park Lane", price=980000.0,
             source_text="Joyner 7 New Road Park Lane Estate Dec-26 252 Corner $430,000 "
             "TERRACE Type A 235.70 4 | 3.5 | 2 $550,000 $980,000 Aplace Exclusive"),
        _row("v2:coomera-117", lot_address="117 New Road, GOLD COAST", estate_name="GOLD COAST",
             price=1491200.0,
             source_text="Coomera 117 New Road Novella Waters Apr-27 484 Rectangular $835,000 "
             "Elara Grove 306.91 4 | 2.5 | 2 $656,200 $1,491,200 Aplace Exclusive"),
        # the same line on a list whose rows hold only VIC: the reading picks between its
        # document's states and never adds one
        _row("v2:coomera-vic-list", lot_address="117 New Road, GOLD COAST",
             estate_name="GOLD COAST", price=1491200.0, source_url=VIC_ONLY,
             stocklist_file=VIC_ONLY, builder_name="Some Builder",
             source_text="Coomera 117 New Road Novella Waters Apr-27 484 Rectangular $835,000 "
             "Elara Grove 306.91 4 | 2.5 | 2 $656,200 $1,491,200"),
        _row("v2:vic-list-other", lot_address="9 Hadar Way, VIC", suburb="Clyde North",
             state="VIC", state_source="listing locality", source_url=VIC_ONLY,
             stocklist_file=VIC_ONLY, builder_name="Some Builder",
             source_text="Clyde North 9 Hadar Way Alma $716,300"),
        # a place further into the line is an estate or a design as often as a suburb
        _row("v2:joyner-inside", lot_address="7 New Road, New Road Park Lane",
             estate_name="New Road Park Lane", price=981000.0,
             source_text="Park Lane Estate 7 New Road Joyner Dec-26 252 Corner $430,000 "
             "TERRACE Type A 235.70 4 | 3.5 | 2 $550,000 $981,000 Aplace Exclusive"),
        # Creation Homes' emailed lot 114
        {"content_hash": "v2:novella-114", "dedup_key": "v2:novella-114", "builder_name": "",
         "source_channel": "digital email", "source_url": SUBJECT, "price": 1582900.0,
         "lot_address": "Lot 114 New Road", "bedrooms": 6, "date_checked": TODAY,
         "first_seen": TODAY, "last_seen": TODAY,
         "source_text": "HOUSE & LAND FIXED PRICE PACKAGE Novella Waters Lot 114 New Road "
                        "$1,582,900* Land $845,000 Build $737,900 6 3 2"},
    ]


def _harvest_db(path, rows):
    from database import ResearchDatabase
    ResearchDatabase(path)
    conn = sqlite3.connect(str(path))
    try:
        for r in rows:
            conn.execute("INSERT INTO buildings (%s) VALUES (%s)"
                         % (", ".join(r), ", ".join("?" * len(r))), list(r.values()))
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture(scope="module")
def published(tmp_path_factory):
    import build_web
    import config
    tmp = tmp_path_factory.mktemp("own_words")
    saved = config.DATABASE_PATH, build_web._login_status
    config.DATABASE_PATH = _harvest_db(tmp / "harvest.db", _rows())
    build_web._login_status = lambda: {}
    try:
        build_web.build(tmp / "site", with_assets=False)
    finally:
        config.DATABASE_PATH, build_web._login_status = saved
    stock = json.loads((tmp / "site" / "stock.json").read_text(encoding="utf-8"))
    keys = stock["keys"]
    return {r[keys.index("content_hash")]: dict(zip(keys, r)) for r in stock["rows"]}


def test_a_national_lists_lots_take_the_state_of_the_place_their_line_opens_with(published):
    for h, suburb in (("v2:joyner-7", "Joyner"), ("v2:coomera-117", "Coomera")):
        row = published[h]
        assert (row["suburb"], row["state"]) == (suburb, "QLD"), row
        assert row["state_source"] == "row text locality + document"
    # The rows that prove their own state keep it.
    assert published["v2:bridgeman"]["state"] == "QLD"
    assert published["v2:hadar"]["state"] == "VIC"
    assert published["v2:vic-list-other"]["state"] == "VIC"


def test_only_a_state_its_document_holds_and_only_off_the_lines_opening(published):
    """The same reading on a list whose rows hold only VIC adds no state, and a place
    further into the line -- an estate or a design as often as a suburb -- gives none."""
    assert published["v2:joyner-7"]["state"] == "QLD"
    assert not published["v2:coomera-vic-list"]["state"], published["v2:coomera-vic-list"]
    assert not published["v2:joyner-inside"]["state"], published["v2:joyner-inside"]


def test_an_emailed_lot_takes_the_state_its_subject_names(published):
    row = published["v2:novella-114"]
    assert (row["suburb"], row["state"]) == ("Coomera", "QLD"), row
    assert row["state_source"] == "email subject"
