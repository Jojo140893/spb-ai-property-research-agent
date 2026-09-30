"""A number the row writes only inside another number, or as an area, is not its postcode.

29 Sep 2026. Two stored "postcodes" each kept a stocklist national -- its rows proving two
states, so E-Agent's page state was void for every row on it that proves nothing itself:

  * APLACE by Glenville 12153, E-Agent's VIC page: "Yarrawonga 2077 Buloke Circuit Glanmire
    Park Titled 2064 39 x 54 $390,000 ...". 2064 is the land area (39 x 54 m is 2106 m2).
    Yarrawonga is in VIC and NSW and 2064 is a NSW code, so own_state proved NSW by
    "suburb + postcode", and the row was filed NSW.
  * Mornington storage units 18166/18170/18173 and 18615/18618/18620: "... STORAGF Cold
    Shell 03-7076 71,550.00$ ..." -- an OCR'd date read as TAS 7076.

sources/feature_extract.parse_postcode wrote both. PR #24 (fix-state-root-causes) stops a
value already in hand at database.record_building's write check, keeps it out of every vote
and clears it in enrich -- state_resolver.postcode_written_as_another_number. This file pins
the parser, which no longer produces these numbers at all, and checks the whole path
(parser, write check, enrich) on these rows.

Measured over a copy of the 29 Sep nightly: 21 stored postcodes are written only this way
(16 live) -- "2730.00", "2066.91m2", "3815.58", "$2576.1 per year", the "4137" of an Adobe
link's "...-de30-4137-9f59-...", "03-7076", "2064 39 x 54" -- and not one is a postcode.

The fixtures are real rows of that copy under their real ids, carrying only the columns the
rules read, with their stored values; the stocklist URLs and content hashes are stand-ins,
each sheet is cut to the rows a test needs, and a row made up for a test says so.
"""
import sqlite3
import tempfile
from pathlib import Path

import pytest

import config
import enrich_buildings
from database import ResearchDatabase
from sources.feature_extract import parse_listing_features, parse_postcode
from state_resolver import postcode_written_as_another_number

_LINE_12153 = ("Yarrawonga 2077 Buloke Circuit Glanmire Park Titled 2064 39 x 54 $390,000 "
               "Gower 02 181.86 4 2.5 2 $476,500 $866,500 General")
_LINE_12147 = ("Benalla 508 Road Livingston Mar-27 560 14 x 40 $220,000 Selina 05 203.27 4 2 "
               "2 $440,700 $660,700 Aplace Exclusive")
_LINE_18166 = ("B07 74.00 74.00 STORAGF Cold Shell 03-7076 71,550.00$ 7,851.00 5 1,810.00$ "
               "436,600.00 Available Download")
_LINE_18170 = ("R73 48.00 48.00 STORAGF Cold Shell 0.3-7076 14,450.30 1,946.00 5 1,166.00$ "
               "286,000.00 Available Download")
_LINE_18129 = "Lot 50 1 1 1-4 196 $776,895 Lot 39-40: $2576.1 per year"


# ------------------------------------------------------------------------- the parser

@pytest.mark.parametrize("text", [
    "Titled 2064 39 x 54 $390,000",              # 12153: the land area before 39 x 54
    "Titled 2210 34 x 51 $390,000 Selina",       # made up: an irregular lot, 1.27 x 34 x 51
    _LINE_18166,                                 # an OCR'd date, "03-7076"
    _LINE_18170,                                 # the same, "0.3-7076"
    "Available 3815.58 1024.93",                 # 17625: decimals
    "S26 & S33 2730.00 2730.00 $3,330,000",      # 4247
    "Lot 2, 37-39 House Carolina 24 2066.91m² 400.0 m² 4 beds",   # 9430
    _LINE_18129,                                 # an owners-corporation fee
    "urn:aaid:sc:AP:30d49294- 3653-47da-9e1b-e1ef63a4daa6 Q2 2027",       # 11857, a link
    "urn:aaid:sc:AP:6f2fc578-6540- Titled 21/09/2026 2 Part",             # 11859
    "Volos Available Owner.Occ Only 27.07.2926",  # 2235, a date with a dot
])
def test_a_number_inside_another_number_or_an_area_is_not_a_postcode(text):
    assert parse_postcode(text) is None


def test_the_aplace_line_no_longer_gives_its_land_area():
    """The whole 12153 line. What is left is 2077, the lot number after the suburb, which the
    write check refuses as the number opening lot_address ("2077 Buloke Circuit, ...") --
    see test_a_harvest_stores_no_postcode_for_these_rows. This test is the land area only."""
    assert parse_postcode(_LINE_12153) != "2064"
    # and what a harvest hands record_building
    assert parse_listing_features(_LINE_18166)["postcode"] is None


@pytest.mark.parametrize("text, postcode", [
    ("1368 Margery St, Toolern Waters, Melton South 3338", "3338"),
    ("Lot 12 Smith Street, Tarneit VIC 3029", "3029"),
    ("LOT 79 STELLA ST, COLAC 3250", "3250"),
    ("Clyde North 3978 Perch", "3978"),
    ("Q1-2026 titled 400 m2 Craigieburn 3064", "3064"),
    # a postcode written before the lot's frontage and depth: 12.5 x 32 is a 400 m2 lot
    ("Lot 5 Tarneit VIC 3029 12.5 x 32 $300,000", "3029"),
    # a dash or point that does not join two numbers
    ("Tarneit VIC 3029 - Lot 5 Available", "3029"),
    ("Lot 5, Tarneit VIC 3029. Titled", "3029"),
    ("Lot 5 Tarneit VIC 3029-Titled", "3029"),
])
def test_a_real_postcode_stays(text, postcode):
    assert parse_postcode(text) == postcode


# ------------------------------------------- the parser beside the write check

@pytest.mark.parametrize("number, line", [
    ("2064", _LINE_12153), ("7076", _LINE_18166), ("7076", _LINE_18170),
    ("2576", _LINE_18129), ("3815", "Available 3815.58 1024.93"),
    ("2730", "S26 & S33 2730.00 2730.00 $3,330,000"),
])
def test_the_parser_refuses_what_the_write_check_refuses(number, line):
    """state_resolver.postcode_written_as_another_number is what the write check and enrich
    apply to a postcode already in hand; the parser decides the same numbers the same way.
    Were the parser to give a number the check refuses, a harvest would offer it every night
    for the check to drop."""
    assert postcode_written_as_another_number(number, line)
    assert parse_postcode(line) != number


@pytest.mark.parametrize("number, line", [
    ("3338", "1368 Margery St, Toolern Waters, Melton South 3338"),
    ("3029", "Lot 12 Smith Street, Tarneit VIC 3029"),
    ("3250", "LOT 79 STELLA ST, COLAC 3250"),
    ("3029", "Lot 5 Tarneit VIC 3029 12.5 x 32 $300,000"),
])
def test_a_real_postcode_passes_both(number, line):
    assert parse_postcode(line) == number
    assert not postcode_written_as_another_number(number, line)


# ------------------------------------------------------------------- the write door

def _harvested(line, lot_address, **kw):
    """The row a harvest hands database.record_building: the parser's fields, from the line."""
    b = {"source_channel": "E-Agent", "attribution_scope": "builder",
         "builder_name": "APLACE by Glenville", "suburb": "", "lot_address": lot_address,
         "source_text": line, "advertised_package_price": 866500,
         "availability_status": "Available"}
    b.update({k: v for k, v in parse_listing_features(line).items() if v is not None})
    b.update(kw)
    return b


def test_a_harvest_stores_no_postcode_for_these_rows():
    """End to end at the one door every channel writes through: 12153's line now parses to
    2077 (its lot, refused there as the number opening lot_address), 18166's to nothing. On
    origin/main before this change the parser gave 2064 and 7076, and only the check stopped
    them."""
    db = ResearchDatabase(db_path=Path(tempfile.mkdtemp()) / "write.db")
    db.record_building(_harvested(_LINE_12153, "2077 Buloke Circuit, Glanmire Park Titled",
                                  suburb="Yarrawonga"))
    db.record_building(_harvested(_LINE_18166, _LINE_18166[:110], suburb="Mornington",
                                  builder_name=""))
    db.record_building(_harvested("Lot 12 Smith Street, Tarneit VIC 3029 Available $650,000",
                                  "Lot 12 Smith Street, Tarneit VIC 3029",
                                  builder_name="Goldstate Homes"))
    stored = {r["source_text"][:10]: r["postcode"] for r in db.get_buildings()}
    assert stored == {_LINE_12153[:10]: None, _LINE_18166[:10]: None, "Lot 12 Smi": "3029"}


# ------------------------------------------------------------------------------ enrich
#
# The rows a parser fix cannot reach: values already stored. PR #24's enrich clears them and
# re-resolves what they decided; these are those rows as the 29 Sep copy holds them.

_COLS = ("id", "builder_name", "source_channel", "lot_address", "suburb", "state",
         "state_source", "estate_name", "source_text", "source_url", "stocklist_file",
         "source_state_hint", "postcode", "lot_number", "superseded_by", "content_hash",
         "price")
_APLACE = "https://docs.example.test/e-agent/aplace-vic.pdf"
_MORNINGTON = "https://docs.example.test/e-agent/mornington-storage.pdf"
_LEAKES = "https://docs.example.test/dropbox/191-leakes-rd"


def _row(rid, **kw):
    row = {c: None for c in _COLS}
    row.update(id=rid, content_hash=f"v2:test{rid:05d}", source_channel="E-Agent",
               builder_name="")
    row.update(kw)
    return row


def _aplace_12153(**kw):
    row = _row(12153, builder_name="APLACE by Glenville", suburb="Yarrawonga",
               lot_address="2077 Buloke Circuit, Glanmire Park Titled", source_text=_LINE_12153,
               state="NSW", state_source="suburb + postcode", postcode="2064",
               source_state_hint="VIC", source_url=_APLACE, stocklist_file=_APLACE,
               price=866500.0)
    row.update(kw)
    return row


def _aplace_12147():
    """A row of the same sheet that proves nothing itself: blank while it was national."""
    return _row(12147, builder_name="APLACE by Glenville", suburb="Benalla",
                lot_address=_LINE_12147[:110], source_text=_LINE_12147,
                source_state_hint="VIC", source_url=_APLACE, stocklist_file=_APLACE,
                price=660700.0)


def _mornington(rid, line, **kw):
    row = _row(rid, suburb="Mornington", lot_address=line[:110], source_text=line,
               state="VIC", postcode="7076", source_url=_MORNINGTON,
               stocklist_file=_MORNINGTON, price=436600.0)
    row.update(kw)
    return row


def _make_db(tmp_path, rows):
    db = tmp_path / "enrich.db"
    ResearchDatabase(db)
    conn = sqlite3.connect(str(db))
    for r in rows:
        conn.execute(f"INSERT INTO buildings ({', '.join(_COLS)}) "
                     f"VALUES ({', '.join('?' for _ in _COLS)})", [r[c] for c in _COLS])
    conn.commit()
    conn.close()
    return db


def _rows(db):
    conn = sqlite3.connect(str(db))
    try:
        return {rid: (state, source, postcode) for rid, state, source, postcode in
                conn.execute("SELECT id, state, state_source, postcode FROM buildings")}
    finally:
        conn.close()


def _table(db):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT * FROM buildings ORDER BY id").fetchall()
    finally:
        conn.close()


def _enrich(monkeypatch, db):
    monkeypatch.setattr(config, "DATABASE_PATH", db)
    return enrich_buildings.main([])


def _second_run_writes(monkeypatch, db):
    writes = []
    real_write = enrich_buildings._write

    def counting_write(conn, rid, **cols):
        writes.append((rid, cols))
        return real_write(conn, rid, **cols)

    monkeypatch.setattr(enrich_buildings, "_write", counting_write)
    assert _enrich(monkeypatch, db) == 0
    monkeypatch.setattr(enrich_buildings, "_write", real_write)
    return writes


def test_the_aplace_sheet_gets_its_page_state_back(tmp_path, monkeypatch, capsys):
    """12153 is re-opened with its land area ignored and comes out VIC off the page, and the
    sheet is no longer national, so 12147 takes the page's state too.

    CUT SHEET, and the whole sheet agrees: enrich over the 29 Sep copy files all 69 live
    APLACE rows VIC -- 12154's lot number 2078 no longer votes either (it opens its
    lot_address)."""
    db = _make_db(tmp_path, [_aplace_12153(), _aplace_12147()])
    assert _enrich(monkeypatch, db) == 0
    after = _rows(db)
    assert after[12153] == ("VIC", "suburb + e-agent page", None)
    # blank while the sheet was national: Benalla alone proves nothing to own_state
    assert after[12147][:2] == ("VIC", "suburb + e-agent page"), "the sheet is still national"
    out = capsys.readouterr().out
    # enrich files a misread postcode under its lot-number lines: postcode_is_lot_number
    # counts one the row writes only as another number
    assert "its postcode was its own lot number (postcode cleared): 1" in out
    line = next(ln for ln in out.splitlines() if "id 12153" in ln)
    assert "NSW -> VIC" in line and "2064" in line
    assert "NATIONAL" not in out
    before = _table(db)
    assert _second_run_writes(monkeypatch, db) == [], "the second run wrote again"
    assert _table(db) == before


def test_the_same_row_written_with_a_real_postcode_keeps_its_state(tmp_path, monkeypatch):
    """PREMISE for the test above: the only thing that moves 12153 is that 2064 is its land
    area. The same row with 2064 written as a postcode ("Yarrawonga NSW 2064", made up) is
    NSW and stays NSW."""
    line = "Yarrawonga NSW 2064 Buloke Circuit Glanmire Park Titled $390,000"
    db = _make_db(tmp_path, [_aplace_12153(source_text=line, lot_address=line),
                             _aplace_12147()])
    assert _enrich(monkeypatch, db) == 0
    assert _rows(db)[12153] == ("NSW", "suburb + postcode", "2064")


def test_a_superseded_copy_of_the_row_casts_no_vote_either(tmp_path, monkeypatch):
    """The national tally counts superseded rows too, so a superseded copy still holding the
    land area (made up: the real copies, 858 and 7656, have no postcode) must not keep the
    sheet national. Its column is left as stored -- live rows only are cleared."""
    copy = _aplace_12153(id=858, content_hash="v2:test00858", state=None, state_source=None,
                         superseded_by="v2:test12153")
    db = _make_db(tmp_path, [_aplace_12153(), _aplace_12147(), copy])
    assert _enrich(monkeypatch, db) == 0
    after = _rows(db)
    assert after[12153] == ("VIC", "suburb + e-agent page", None)
    assert after[858] == ("VIC", "suburb + e-agent page", "2064")
    assert _second_run_writes(monkeypatch, db) == []


def test_the_mornington_date_leaves_the_postcode_column(tmp_path, monkeypatch, capsys):
    """18166 and 18170 hold 7076 off "03-7076" / "0.3-7076". Their VIC state does not rest on
    it (it carries no signal), so it stays; the number leaves the column, and without its
    TAS vote the sheet no longer proves two states. 18167 (made up) proves VIC."""
    clyde = ("B10 75.00 75.00 Clyde North Available Download")
    db = _make_db(tmp_path, [
        _mornington(18166, _LINE_18166), _mornington(18170, _LINE_18170),
        _row(18167, suburb="Clyde North", lot_address=clyde, source_text=clyde,
             source_url=_MORNINGTON, stocklist_file=_MORNINGTON, price=400000.0)])
    assert _enrich(monkeypatch, db) == 0
    after = _rows(db)
    assert after[18166] == ("VIC", None, None)
    assert after[18170] == ("VIC", None, None)
    out = capsys.readouterr().out
    assert "postcode cleared, it was the live row's own lot number (state unchanged): 2" in out
    line = next(ln for ln in out.splitlines() if "id 18166" in ln)
    assert "7076" in line and "(TAS range)" in line
    assert "NATIONAL" not in out
    assert _second_run_writes(monkeypatch, db) == []


def test_a_state_from_a_fee_read_as_a_postcode_goes_blank(tmp_path, monkeypatch):
    """18129, builder '191 Leakes Rd' (Truganina, VIC): NSW by "postcode" alone, off
    "$2576.1 per year". Nothing else in the row names a place and it has no page, so it
    proves nothing: blank beats a state from a fee."""
    db = _make_db(tmp_path, [_row(18129, builder_name="191 Leakes Rd", estate_name="OCFEE:",
                                  lot_address="Lot 50, OCFEE:", source_text=_LINE_18129,
                                  state="NSW", state_source="postcode", postcode="2576",
                                  lot_number="50", source_url=_LEAKES,
                                  stocklist_file=_LEAKES, price=776895.0)])
    assert _enrich(monkeypatch, db) == 0
    assert _rows(db)[18129] == (None, None, None)


def test_a_real_postcode_is_not_touched(tmp_path, monkeypatch):
    """Goldstate 1244 (real): postcode 3338, written plainly in its label. And a portal row
    (made up) whose postcode came from an address field its text does not repeat."""
    goldstate = "https://docs.example.test/e-agent/goldstate-toolern-waters.pdf"
    portal = "https://portal.example.test/stock"
    db = _make_db(tmp_path, [
        _row(1244, builder_name="Goldstate Homes", suburb="Toolern Waters , Melton South 3338",
             lot_address="1368 Margery St, Toolern Waters , Melton South 3338",
             source_text="Titled 1368 Margery St East 8.5 32 272 $270,000 Havana 14 "
                         "Sovereign 3 Single 127 $319,000 $589,000 Available",
             state="VIC", state_source="postcode + e-agent page", postcode="3338",
             lot_number="1368", source_state_hint="VIC", source_url=goldstate,
             stocklist_file=goldstate, price=589000.0),
        _row(30001, source_channel="Proxima", suburb="Tarneit", lot_address="Lot 5",
             source_text="Lot 5 Available 4 2 2 $650,000", state="VIC",
             state_source="suburb + postcode", postcode="3029", lot_number="5",
             source_url=portal, stocklist_file=portal, price=650000.0)])
    assert _enrich(monkeypatch, db) == 0
    after = _rows(db)
    assert after[1244] == ("VIC", "postcode + e-agent page", "3338")
    assert after[30001] == ("VIC", "suburb + postcode", "3029")
