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

The fixtures are real rows of the 28 Sep nightly under their real ids, carrying only the
columns the rules read, with their stored values -- except that the stocklist URLs and
content hashes are stand-ins, each sheet is cut down to the rows a test needs, and a test
that changes a stored value (a premise with the lot number taken away, 19094 as enrich
leaves it) says so where it does. A cut sheet can decide differently from the whole one,
and where it does the test says so too.
(Review, 28 Sep 2026: the first version of this file said every fixture was a real stored
row under its real id, and five were not -- 11690, 9300 and 9301 were invented rows under
other rows' ids, and 6948 and 8991 had lost their stored postcodes. Those are now the real
rows.)

The enrich tests run enrich_buildings.main() over a temporary database; the write-door
tests write rows shaped like 19094 and 21209 through database.record_building, the door
every harvest channel stores a row through, and the reparse test runs
reparse_from_source.main() with the file fetch replaced.
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
    """A row of the same sheet that proves Victoria itself. Its lot, 3016, is stored as its
    postcode too, but its state rests on its own words, not on that number."""
    line = ("2-Part Detached SS Southeast 3016 Five Farms Clyde North Nov-26 350 $441,000 "
            "Cura 20B 4 / 2 / 2 $407,370 $848,370 Brochure Masterplan Location Map "
            "Stage 30 - POS Sold")
    return _row(6948, builder_name="Verv Projects", suburb="Clyde North",
                lot_address=line[:110], source_text=line, state="VIC",
                state_source="listing text", postcode="3016", lot_number="3016",
                source_state_hint="VIC", source_url=_VERV_SHEET,
                stocklist_file=_VERV_SHEET, price=848370.0)


def _tomorrow(rid, **kw):
    row = _row(rid, builder_name="Tomorrow Homes", estate_name="SOUTH EAST",
               source_state_hint="VIC", source_url=_TOMORROW_SHEET,
               stocklist_file=_TOMORROW_SHEET)
    row.update(kw)
    return row


_GOLDSTATE_LIST = "https://docs.example.test/e-agent/goldstate-toolern-waters.pdf"


def _goldstate_1244():
    """A state on a REAL postcode beside a lot number: lot 1368, postcode 3338 (Melton
    South). Goldstate's own E-Agent list, not Tomorrow Homes' -- no row on that sheet has
    a postcode that is not its lot number."""
    return _row(1244, builder_name="Goldstate Homes",
                suburb="Toolern Waters , Melton South 3338",
                estate_name="Toolern Waters , Melton South 3338",
                lot_address="1368 Margery St, Toolern Waters , Melton South 3338",
                source_text="Titled 1368 Margery St East 8.5 32 272 $270,000 Havana 14 "
                            "Sovereign 3 Single 127 $319,000 $589,000 Available",
                state="VIC", state_source="postcode + e-agent page", postcode="3338",
                lot_number="1368", source_state_hint="VIC", source_url=_GOLDSTATE_LIST,
                stocklist_file=_GOLDSTATE_LIST, price=589000.0)


def _tomorrow_sheet():
    """Tomorrow Homes' VIC sheet as the 28 Sep nightly holds it, cut to one row of each kind.
    Every row on it that has a postcode has its own lot number there."""
    return [
        # "2704 Talladega Rd, Clyde": Clyde is in NSW and VIC; the lot number picked NSW.
        _tomorrow(11671, suburb="Clyde", lot_address="2704 Talladega Rd, Clyde",
                  source_text="10 26 Evergreen 2704 Talladega Rd Clyde 8.5 212 COMO 1 3 "
                              "$324,000 $322,840 $646,840 TH BROCHURE - 2704 AVAILABLE",
                  state="NSW", state_source="suburb + postcode", postcode="2704",
                  lot_number="2704", price=646840.0),
        # Proves VIC from its own locality. Its postcode is its lot number too, but its
        # state does not rest on it, so it is not what the re-open rule reaches.
        _tomorrow(16142, suburb="Clyde North", lot_address="3226 Modesta Rd, Clyde North",
                  source_text="TITLED Smiths Lane 3226 Modesta Rd Clyde North 12.5 400 "
                              "LUCCA L 1 4 $486,000 $390,844 $876,844 TH BROCHURE - 3226 "
                              "$15,000 Post-Settlement rebate $861,844 AVAILABLE",
                  state="VIC", state_source="listing locality", postcode="3226",
                  lot_number="3226", price=876844.0),
        # Two of the 128 rows that prove nothing themselves, blank while the sheet was
        # "national". 11847's lot, 7608, is a TASMANIAN number in its postcode column.
        _tomorrow(8991, lot_address="27 Smiths Lane, SOUTH EAST",
                  source_text="05 27 Smiths Lane 4748 Davish Court Clyde North 10.5 262 "
                              "PRAIANO 1 4 $383,000 $353,345 $736,345 TH BROCHURE - 4748 "
                              "AVAILABLE",
                  postcode="4748", lot_number="4748", price=736345.0),
        _tomorrow(11847, estate_name=None, suburb="Aintree",
                  lot_address="7608 Sugar Maple Cct, Aintree",
                  source_text="TITLED Woodlea 7608 Sugar Maple Cct Aintree 13.5 279 ROMA 4B "
                              "1 4 $347,000 $337,618 $684,618 TH BROCHURE - 7608 "
                              "*Complimentary Front Landscaping – Valued up to $10K "
                              "*Community Infrastructure Levy Fee Covered AVAILABLE",
                  postcode="7608", lot_number="7608", price=684618.0),
        # A SUPERSEDED copy, lot 3719 in its postcode column, state from its own words.
        _tomorrow(8997, suburb="Clyde", lot_address="3719 Tangelo Ave, Clyde",
                  source_text="01 27 Riverfield 3719 Tangelo Ave Clyde 12.5 312 TRENTINO 1 4 "
                              "$412,000 $370,087 $782,087 TH BROCHURE - 3719 AVAILABLE",
                  state="VIC", state_source="listing text", postcode="3719",
                  lot_number="3719", price=782087.0,
                  superseded_by="v2:09777d49380fc5511252966795c90dee96b03e268905d6d0b44b"
                                "efae27786635"),
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
    ignored, this cut of its sheet proves only VIC, so the E-Agent VIC page decides --
    labelled as contested, because 'Jubilee' in its line is still a Queensland place name.

    CUT SHEET: on the whole Verv sheet 19094 comes out BLANK, not VIC, because another row
    (11494, "5629 The Grove, Tarneit", read as NSW) still makes that sheet national. Blank
    is what takes it out of a QLD search; VIC here shows the rule, not the live answer."""
    db = _make_db(tmp_path, [_jubilee_19094(), _verv_clyde_north_6948()])
    assert _enrich(monkeypatch, db) == 0
    assert _rows(db)[19094] == ("VIC", "e-agent page (conflicting signals)", None)
    # The sibling's state is not re-opened -- it rests on its own words -- but its lot
    # number, 3016, still comes out of its postcode column.
    assert _rows(db)[6948] == ("VIC", "listing text", None)
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
    db = _make_db(tmp_path, _tomorrow_sheet() + [_goldstate_1244()])
    assert _enrich(monkeypatch, db) == 0
    after = _rows(db)
    assert after[11671][:2] == ("VIC", "suburb + e-agent page")
    assert after[8991][:2] == ("VIC", "e-agent page"), "the sheet is not national any more"
    assert after[11847][:2] == ("VIC", "suburb + e-agent page")
    # Not re-opened: 16142 and 8997 rest on their own words, 1244 on a real postcode.
    assert after[16142][:2] == ("VIC", "listing locality")
    assert after[8997][:2] == ("VIC", "listing text")
    assert after[1244][:2] == ("VIC", "postcode + e-agent page")
    before = _table(db)
    assert _second_run_writes(monkeypatch, db) == [], "the second run wrote again"
    assert _table(db) == before


def test_a_lot_number_leaves_the_postcode_column_of_every_live_row(tmp_path, monkeypatch,
                                                                   capsys):
    """Review, 28 Sep 2026: only the re-opened rows had their lot number cleared, which left
    94 live rows showing it as their postcode -- 39 of them another state's number, like
    11847, VIC, postcode 7608 (a Tasmanian one). It shows in the Postcode column and the
    pick CSV. Every live row's comes out, whatever its state rests on, and no state moves
    for it: the resolver already ignored the number."""
    db = _make_db(tmp_path, _tomorrow_sheet() + [_goldstate_1244()])
    assert _enrich(monkeypatch, db) == 0
    after = _rows(db)
    for rid in (11671, 16142, 8991, 11847):
        assert after[rid][2] is None, f"{rid} still shows its lot number as its postcode"
    assert after[1244][2] == "3338", "a real postcode beside a lot number stays"
    # Live rows only: a superseded copy is shown nowhere and is left as it was stored.
    assert after[8997] == ("VIC", "listing text", "3719")
    out = capsys.readouterr().out
    # 11671 is re-opened (its line above); the other three are this line's
    assert "postcode cleared, it was the live row's own lot number (state unchanged): 3" in out
    line = next(ln for ln in out.splitlines() if "id 11847" in ln)
    assert "VIC" in line and "7608" in line and "(TAS range)" in line
    # and a night with nothing left to clear says 0
    assert _enrich(monkeypatch, db) == 0
    assert ("postcode cleared, it was the live row's own lot number (state unchanged): 0"
            in capsys.readouterr().out)


def test_clearing_the_lot_number_moves_no_state(tmp_path, monkeypatch):
    """The same sheet, once with the lot numbers this clear reaches left in the postcode
    column and once with them already gone: every row comes out in the same state on the
    same signal. That is what makes the clear safe to run over every live row. (11671 is
    left as it is in both: its stored state CITES the number, so it is the re-open's.)"""
    sheet = _tomorrow_sheet() + [_goldstate_1244()]
    kept = _make_db(tmp_path, sheet)
    blank_dir = tmp_path / "blank"
    blank_dir.mkdir()
    blank = _make_db(blank_dir, [
        dict(r, postcode=None) if (r["postcode"] == r["lot_number"] and not r["superseded_by"]
                                   and not enrich_buildings.cites_a_postcode(r["state_source"]))
        else r for r in sheet])
    # PREMISE: the two databases really do differ, in exactly the rows this clear reaches
    assert {rid for rid, v in _rows(kept).items() if v[2]} - \
        {rid for rid, v in _rows(blank).items() if v[2]} == {16142, 8991, 11847}
    assert _enrich(monkeypatch, kept) == 0
    assert _enrich(monkeypatch, blank) == 0
    assert ({rid: v[:2] for rid, v in _rows(kept).items()}
            == {rid: v[:2] for rid, v in _rows(blank).items()})


def test_a_lot_number_state_on_a_sheet_that_is_still_national_goes_blank(tmp_path,
                                                                       monkeypatch):
    """Silkwood 9255, 'PR8851 6053 Yarrabilba ...' filed WA. Its sheet also proves NSW and
    QLD from other rows, so it stays national: with its lot number gone the row proves
    nothing, and blank beats a state from a lot number. 9240 proves QLD by Bahrs Scrub;
    11510 proves NSW by the Round Mountain in its estate "New Beith Round Mountain"."""
    sheet = "https://docs.example.test/spreadsheets/d/silkwood"
    line = ("PR8851 6053 Yarrabilba Yarrabilba Estate March-May 2027 362 $520,900 $426,979 "
            "$947,879 $770 4.22% Mornington 188 4/2/2/2 Uncon Only")
    bahrs = ("PR8745 1797 Bahrs Scrub Brookhaven Estate Registered 541 $680,000 $402,185 "
             "$1,082,185 $710 3.42% Eliza 184 4/2/2/2 Finance Considered")
    beith = ("REGISTER NOW New Beith Round Mountain Estate July 2027 $542,000 $440,000 "
             "$982,000 Doncaster 191 4/2/1/2")
    rows = [_row(9255, builder_name="Silkwood Homes", lot_address=line[:110], source_text=line,
                 estate_name="Yarrabilba Yarrabilba", state="WA",
                 state_source="suburb + postcode", postcode="6053", lot_number="6053",
                 source_state_hint="QLD", source_url=sheet, stocklist_file=sheet,
                 price=947879.0),
            _row(9240, builder_name="Silkwood Homes", lot_address=bahrs[:110],
                 source_text=bahrs, estate_name="Bahrs Scrub Brookhaven", state="QLD",
                 state_source="listing locality", lot_number="1797",
                 source_state_hint="QLD", source_url=sheet, stocklist_file=sheet,
                 price=1082185.0),
            _row(11510, builder_name="Silkwood Homes", lot_address=beith, source_text=beith,
                 estate_name="New Beith Round Mountain", state="NSW",
                 state_source="listing locality", source_state_hint="QLD",
                 source_url=sheet, stocklist_file=sheet, price=982000.0)]
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


_GEOCON_LINE = "Lot GRD-T2-601 Unit 149, 70 Allara Street, CANBERRA, ACT, 2601"
_GEOCON_WRITE = {
    "source_channel": "Proxima", "attribution_scope": "builder",
    "builder_name": "Geocon Group", "suburb": "Canberra", "state": "ACT",
    "estate_name": "The Grande", "lot_number": "GRD-T2-601", "postcode": "2601",
    "lot_address": _GEOCON_LINE, "source_text": _GEOCON_LINE,
    "advertised_package_price": 618900, "availability_status": "Not Available",
}


def test_a_real_postcode_whose_digits_spell_the_lot_is_still_stored():
    """Review, 28 Sep 2026: Geocon 21209, lot GRD-T2-601 (tower 2, apartment 601), postcode
    2601 -- its real Canberra one. Joined up, the lot's digits read 2601, so the first
    version of the rule would not store it."""
    db = _fresh_db()
    db.record_building(dict(_GEOCON_WRITE))
    assert db.get_buildings()[0]["postcode"] == "2601"


# ------------------------------------------------------------------ reparse_from_source

def test_the_reparse_does_not_put_a_lot_number_back_as_a_postcode(tmp_path, monkeypatch):
    """Review, 28 Sep 2026: reparse_from_source.py --apply writes postcode with its own
    UPDATE, round record_building's guard. Its only check was that the postcode fits the
    row's state -- and the Jubilee lot is BLANK now, so 4327 would have fitted. The file
    fetch is replaced: this test reads nothing from the network."""
    import sys
    import reparse_from_source

    sheet = "https://docs.example.test/spreadsheets/d/verv-projects"
    geocon = "https://docs.example.test/proxima/geocon-the-grande"
    # 19094 as enrich now leaves it: no state, no postcode.
    stored = [_jubilee_19094(state=None, state_source=None, postcode=None),
              _row(21209, source_channel="Proxima", builder_name="Geocon Group",
                   suburb="Canberra", state="ACT",
                   estate_name="The Grande", lot_number="GRD-T2-601",
                   lot_address=_GEOCON_LINE, source_text=_GEOCON_LINE,
                   source_url=geocon, stocklist_file=geocon, price=618900.0)]
    db = _make_db(tmp_path, stored)
    # What today's extractor reads back off each line: the lot number as a postcode on the
    # Verv line, the real postcode on the Geocon one.
    fresh = {sheet: [{"source_text": _JUBILEE_LINE, "lot_number": "4327", "postcode": "4327"}],
             geocon: [{"source_text": _GEOCON_LINE, "lot_number": "GRD-T2-601",
                       "postcode": "2601"}]}
    monkeypatch.setattr(reparse_from_source, "DATABASE_PATH", db)
    monkeypatch.setattr(reparse_from_source, "fetchable", lambda url: True)
    monkeypatch.setattr(reparse_from_source, "fetch", lambda url: url)
    monkeypatch.setattr(reparse_from_source, "extract_stocklist",
                        lambda data, source_label=None: fresh[data])
    monkeypatch.setattr(sys, "argv", ["reparse_from_source.py", "--apply"])
    assert reparse_from_source.main() == 0
    after = _rows(db)
    assert after[19094][2] is None, "4327 is lot 4327, not a Queensland postcode"
    assert after[21209][2] == "2601", "PREMISE: the reparse does write a real postcode"
