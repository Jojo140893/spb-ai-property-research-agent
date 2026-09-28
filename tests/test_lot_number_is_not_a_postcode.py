"""A lot number is not a postcode, and a state read off one is not settled.

Coleen, 28 Sep 2026: "I searched for QLD properties, however, the app is reporting on VIC
properties." Her comparison report for a QLD lead under $800k offered two "Jubilee, QLD"
lots -- Verv Projects lots in the Jubilee estate at Wyndham Vale, Victoria, off E-Agent's
VICTORIA page. Their line reads "2-Part Detached SS West 4327 Jubilee Wyndhamvale ...": 4327
is the lot, the row text parser also stored it as the postcode, 4327 is a Queensland
postcode and Jubilee a Queensland locality, so own_state proved QLD by "suburb + postcode"
and enrich has treated the row as settled ever since.

On the 28 Sep nightly 18 live rows rest on a postcode equal to their own lot number, 12 in
the wrong state (6 Jubilee VIC lots filed QLD, 5 Tomorrow Homes Clyde VIC lots filed NSW,
Yarrabilba lot 6053 filed WA), and the Clyde five made Tomorrow Homes' VIC sheet "national",
leaving 128 of its rows with no state.

Every fixture is a real stored row, under its real id. The enrich tests run
enrich_buildings.main() over a temporary database; the last two write through
database.record_building, the door every harvest channel stores a row through.
"""
import sqlite3
import tempfile
from pathlib import Path

import config
import enrich_buildings
from database import ResearchDatabase, building_content_hash

_COLS = ("id", "builder_name", "source_channel", "lot_address", "suburb", "state",
         "state_source", "estate_name", "source_text", "source_url", "stocklist_file",
         "source_state_hint", "postcode", "lot_number", "superseded_by", "content_hash",
         "price")

_VERV_SHEET = "https://docs.example.test/spreadsheets/d/verv-projects"
_TOMORROW_SHEET = "https://docs.example.test/spreadsheets/d/tomorrow-homes"
_JUBILEE_LINE = ("2-Part Detached SS West 4327 Jubilee Wyndhamvale 263 $338,000 Enzo 10.5 "
                 "3 / 2 / 2 $347,050 $685,050 Brochure Masterplan Stage 43 - POS Available")


def _row(rid, **kw):
    row = {c: None for c in _COLS}
    row.update(id=rid, content_hash=f"v2:test{rid:05d}", source_channel="E-Agent",
               builder_name="")
    row.update(kw)
    return row


def _jubilee_19094(**kw):
    """The lot Coleen was shown as 'Jubilee, QLD': VIC page, QLD from its lot number."""
    row = _row(19094, builder_name="Verv Projects", lot_address=_JUBILEE_LINE[:110],
               source_text=_JUBILEE_LINE, state="QLD", state_source="suburb + postcode",
               postcode="4327", lot_number="4327", source_state_hint="VIC",
               source_url=_VERV_SHEET, stocklist_file=_VERV_SHEET, price=685050.0)
    row.update(kw)
    return row


def _verv_clyde_north_6948():
    """A row of the same sheet that proves Victoria itself."""
    line = "2-Part Detached SS Southeast 3016 Five Farms Clyde North Nov-26 350 $412,000"
    return _row(6948, builder_name="Verv Projects", suburb="Clyde North", lot_address=line,
                source_text=line, state="VIC", state_source="listing text",
                lot_number="3016", source_state_hint="VIC", source_url=_VERV_SHEET,
                stocklist_file=_VERV_SHEET)


def _tomorrow(rid, **kw):
    row = _row(rid, builder_name="Tomorrow Homes", estate_name="SOUTH EAST",
               source_state_hint="VIC", source_url=_TOMORROW_SHEET,
               stocklist_file=_TOMORROW_SHEET)
    row.update(kw)
    return row


def _tomorrow_sheet():
    """Tomorrow Homes' VIC sheet as the 28 Sep nightly holds it, cut to one row of each kind."""
    return [
        # "2704 Talladega Rd, Clyde": Clyde is in NSW and VIC; the lot number picked NSW.
        _tomorrow(11671, suburb="Clyde", lot_address="2704 Talladega Rd, Clyde",
                  source_text="10 26 Evergreen 2704 Talladega Rd Clyde 8.5 212 COMO 1 3 "
                              "$324,000 $322,840 $646,840 TH BROCHURE - 2704 AVAILABLE",
                  state="NSW", state_source="suburb + postcode", postcode="2704",
                  lot_number="2704", price=646840.0),
        # Proves VIC from its own locality. Its postcode is its lot number too, but its
        # state does not rest on it, so it is not what this rule re-opens.
        _tomorrow(16142, suburb="Clyde North", lot_address="3226 Modesta Rd, Clyde North",
                  state="VIC", state_source="listing locality", postcode="3226",
                  lot_number="3226"),
        # A state on a REAL postcode: 3978 is Clyde's, lot 2711. Settled, and left alone.
        _tomorrow(11690, suburb="Clyde", lot_address="2711 Talladega Rd, Clyde 3978",
                  state="VIC", state_source="suburb + postcode", postcode="3978",
                  lot_number="2711"),
        # One of the 128 rows that prove nothing themselves, blank while the sheet was
        # "national".
        _tomorrow(8991, lot_address="27 Smiths Lane, SOUTH EAST", lot_number="4748"),
    ]


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
    """Run enrich again and return every write it attempted."""
    writes = []
    real_write = enrich_buildings._write

    def counting_write(conn, rid, **cols):
        writes.append((rid, cols))
        return real_write(conn, rid, **cols)

    monkeypatch.setattr(enrich_buildings, "_write", counting_write)
    assert _enrich(monkeypatch, db) == 0
    monkeypatch.setattr(enrich_buildings, "_write", real_write)
    return writes


# ------------------------------------------------------------------------- enrich

def test_only_a_signal_that_used_the_postcode_cites_it():
    for signal in ("suburb + postcode", "postcode", "postcode + e-agent page",
                   "postcode + suburb + e-agent page", "listing locality + postcode"):
        assert enrich_buildings.cites_a_postcode(signal), signal
    # a repair note and a state the row named itself are not a postcode's say-so
    for signal in ("withdrawn: postcode was the lot number", "listing text (over NSW)",
                   "suburb + e-agent page", "listing locality", None, ""):
        assert not enrich_buildings.cites_a_postcode(signal), signal


def test_the_jubilee_lot_goes_back_to_the_page_it_came_from(tmp_path, monkeypatch):
    """19094: settled QLD on 'suburb + postcode' from lot 4327. Re-resolved with the number
    ignored, its sheet proves only VIC, so the E-Agent VIC page decides -- labelled as
    contested, because 'Jubilee' in its line is still a Queensland place name."""
    db = _make_db(tmp_path, [_jubilee_19094(), _verv_clyde_north_6948()])
    assert _enrich(monkeypatch, db) == 0
    assert _rows(db)[19094] == ("VIC", "e-agent page (conflicting signals)", None)
    assert _rows(db)[6948] == ("VIC", "listing text", None), "the sibling is not a target"
    before = _table(db)
    assert _second_run_writes(monkeypatch, db) == [], "the second run wrote again"
    assert _table(db) == before


def test_the_same_row_with_no_lot_number_keeps_its_postcode_state(tmp_path, monkeypatch):
    """PREMISE for the test above: the only thing that moves 19094 is that 4327 is its own
    lot. With no lot number to compare, 4327 is a postcode and the state stands."""
    db = _make_db(tmp_path, [_jubilee_19094(lot_number=None), _verv_clyde_north_6948()])
    assert _enrich(monkeypatch, db) == 0
    assert _rows(db)[19094] == ("QLD", "suburb + postcode", "4327")


def test_a_sheet_made_national_by_lot_numbers_gets_its_page_state_back(tmp_path, monkeypatch):
    """Tomorrow Homes: 11671 proved NSW off its lot number, so the VIC sheet proved two
    states, was declared national, and every row that proves nothing itself went blank."""
    db = _make_db(tmp_path, _tomorrow_sheet())
    assert _enrich(monkeypatch, db) == 0
    after = _rows(db)
    assert after[11671] == ("VIC", "suburb + e-agent page", None), "postcode must be cleared"
    assert after[8991] == ("VIC", "e-agent page", None), "the sheet is not national any more"
    # Neither of these rests on its lot number, so neither is touched.
    assert after[16142] == ("VIC", "listing locality", "3226")
    assert after[11690] == ("VIC", "suburb + postcode", "3978")
    before = _table(db)
    assert _second_run_writes(monkeypatch, db) == [], "the second run wrote again"
    assert _table(db) == before


def test_a_lot_number_state_on_a_sheet_that_is_still_national_goes_blank(tmp_path,
                                                                       monkeypatch):
    """Silkwood 9255, 'PR8851 6053 Yarrabilba ...' filed WA. Its sheet also proves NSW and
    QLD from other rows, so it stays national: with its lot number gone the row proves
    nothing, and blank beats a state from a lot number."""
    sheet = "https://docs.example.test/spreadsheets/d/silkwood"
    line = ("PR8851 6053 Yarrabilba Yarrabilba Estate March-May 2027 362 $520,900 $426,979 "
            "$947,879 $770 4.22% Mornington 188 4/2/2/2 Uncon Only")
    rows = [_row(9255, builder_name="Silkwood Homes", lot_address=line[:110], source_text=line,
                 estate_name="Yarrabilba Yarrabilba", state="WA",
                 state_source="suburb + postcode", postcode="6053", lot_number="6053",
                 source_state_hint="QLD", source_url=sheet, stocklist_file=sheet),
            _row(9300, builder_name="Silkwood Homes", suburb="Redland Bay",
                 lot_address="PR8761 4013 Redland Bay Shoreline, Redland Bay", state="QLD",
                 state_source="listing locality", source_state_hint="QLD",
                 source_url=sheet, stocklist_file=sheet),
            _row(9301, builder_name="Silkwood Homes", suburb="Round Mountain",
                 lot_address="PR8810 Cudgen Road, Round Mountain", state="NSW",
                 state_source="listing locality", source_state_hint="QLD",
                 source_url=sheet, stocklist_file=sheet)]
    db = _make_db(tmp_path, rows)
    assert _enrich(monkeypatch, db) == 0
    assert _rows(db)[9255] == (None, None, None)


def test_every_re_resolved_row_is_logged_with_its_number(tmp_path, monkeypatch, capsys):
    """The postcode column is cleared, so the log line is the record of what was there."""
    db = _make_db(tmp_path, [_jubilee_19094(), _verv_clyde_north_6948()])
    assert _enrich(monkeypatch, db) == 0
    out = capsys.readouterr().out
    assert "its postcode was its own lot number (postcode cleared): 1" in out
    line = next(ln for ln in out.splitlines() if "id 19094" in ln)
    assert "QLD -> VIC" in line and "4327" in line and "suburb + postcode" in line
    # a lot number is not a postcode contradicting the VIC page, so no clash is reported
    assert "postcode and the E-Agent page disagree" not in out
    # and a night with nothing to do says 0, which is what a healthy night looks like
    assert _enrich(monkeypatch, db) == 0
    assert "its postcode was its own lot number (postcode cleared): 0" in capsys.readouterr().out


# ------------------------------------------------------------------- the write door

_VERV_WRITE = {
    "source_channel": "E-Agent", "attribution_scope": "builder",
    "builder_name": "Verv Projects", "suburb": "", "lot_number": "4327",
    "postcode": "4327", "lot_address": _JUBILEE_LINE[:110], "source_text": _JUBILEE_LINE,
    "advertised_package_price": 685050, "availability_status": "Available",
    "land_size_sqm": 263,
}


def _fresh_db():
    return ResearchDatabase(db_path=Path(tempfile.mkdtemp()) / "write.db")


def test_a_harvest_does_not_store_the_lot_number_as_the_postcode():
    db = _fresh_db()
    assert db.record_building(dict(_VERV_WRITE)) == "new"
    (row,) = db.get_buildings()
    assert row["postcode"] is None and row["lot_number"] == "4327"
    # postcode is not an identity field: the row keeps the hash it would have had anyway,
    # so every row already stored under it is still matched by the next harvest.
    assert row["content_hash"] == building_content_hash(dict(_VERV_WRITE))
    # and a re-harvest carrying the same number does not fill it back in
    assert db.record_building(dict(_VERV_WRITE)) == "unchanged"
    assert db.get_buildings()[0]["postcode"] is None


def test_a_real_postcode_on_a_numbered_lot_is_still_stored():
    db = _fresh_db()
    db.record_building(dict(_VERV_WRITE, postcode="3024"))
    assert db.get_buildings()[0]["postcode"] == "3024"
