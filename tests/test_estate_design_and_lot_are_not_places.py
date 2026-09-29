"""A row's lot, its estate and its house design are not the place it is in.

29 Sep 2026. Five live stocklists had no state on 159 of the 165 live whole-row rows that
had none, because one row each made them "national" (state_resolver.file_is_national) and
their page state was dropped for every unsettled row -- and each of those rows had its
state from a word that was never its locality:

  * AVIA 2234, "2120 Stage 21 400 $627,000 ... Madrid 190 Paddington ...": lot 2120 of a
    QLD estate, stored as postcode 2120 by an older parser, NSW by "listing locality +
    postcode". Its lot_number column is empty, so the lot-number rule could not see it.
  * Silkwood 11510, "REGISTER NOW New Beith Round Mountain Estate ...": NSW by "Round
    Mountain", the ESTATE, guessed as its suburb. The lot is in New Beith, QLD.
  * Verv 11494, label "5629 The Grove, Tarneit": NSW by "The Grove", the estate. The lot is
    in Tarneit, VIC, after the comma.

and Hattan 14913/14914 ("... Verona SG Corner | Roselle ...") were filed NSW on the house
DESIGN Verona, while the nightly replay warned every night that AVIA 7810 ("... Rhodes
Paddington 180m2 ...", design and facade) names Rhodes, NSW.

The fixtures are real rows of the 29 Sep nightly under their real ids, carrying only the
columns the rules read, with their stored values -- except that the content hashes are
stand-ins, each sheet is cut down to the rows a test needs, and lines are cut before a
trailing emoji (19643). Non-ASCII characters are written as escapes. The enrich tests run
enrich_buildings.main() over a temporary database; the write-door test writes through
database.record_building.
"""
import sqlite3
import tempfile
from pathlib import Path

import pytest

import config
import enrich_buildings
import state_resolver as sr
from database import ResearchDatabase, building_content_hash
from geo import SuburbGeoIndex

_COLS = ("id", "builder_name", "source_channel", "lot_address", "suburb", "state",
         "state_source", "estate_name", "source_text", "source_url", "stocklist_file",
         "source_state_hint", "postcode", "lot_number", "superseded_by", "content_hash",
         "price", "last_seen")

_AVIA = "https://docs.google.com/spreadsheets/d/1q8gThXA4fh5uS_WZ7cEy-Udrj0OPlBO740tOzzOM4-E/edit"
_VERV = "https://docs.google.com/spreadsheets/d/1bPh8W2Bujp8DHpNknSv6h0LkiVnsxrIqN66PpvWfI4c/edit"
_HUDSON = "https://www.e-agent.com.au/_files/ugd/069fe0_fa318680bc7a4259a3222af89e79f611.pdf"
_DEANSIDE = "email:[Hattan New Release] Dual key in Deanside from 890k"

_LINE_2234 = ("2120 Stage 21 400 $627,000 July 2026 Madrid 190 Paddington 190m2 4 2 2 1 "
              "$457,400 $1,084,400 Volos Available Owner Occ & Investor 18.09.2026")
_LINE_7810 = ("60 Stage 4 429m2 $390,000 August 2026 Rhodes Paddington 180m2 4 2 2 0 "
              "$502,900 $892,900 Volos Available Owner OCC & Investor 18.09.2026")
_LINE_12359 = ("91 Stage 2 354m2 $545,000 February 2027 Rhodes Fortitude 180m2 4 2 2 1 "
               "$430,500 $975,500 Volos Available Owner Occ Only 20.08.2026")
_LINE_11510 = ("REGISTER NOW New Beith Round Mountain Estate July 2027 $542,000 $440,000 "
               "$982,000 Doncaster 191 4/2/1/2")
_LINE_14913 = ("Available Titled Townhouse 44 215 10.86 Verona SG Corner | Roselle 15.72 "
               "3 2 1 $954,950")


@pytest.fixture(scope="module")
def geo():
    return SuburbGeoIndex()


def _row(rid, **kw):
    row = {c: None for c in _COLS}
    row.update(id=rid, content_hash=f"v2:test{rid:05d}", source_channel="E-Agent",
               builder_name="", last_seen="29/09/2026")
    row.update(kw)
    return row


def _avia(rid, line, **kw):
    return _row(rid, builder_name="AVIA Homes", lot_address=line[:110], source_text=line,
                source_state_hint="QLD", source_url=_AVIA, stocklist_file=_AVIA, **kw)


def _avia_sheet():
    """2234 (the NSW vote), 2236 (a superseded row that proves QLD by LOGAN RESERVE), 7810
    and 2178 -- lot 316, suburb column 'EOI Form', no state: the kind of row the national
    verdict left blank."""
    return [
        _avia(2234, _LINE_2234, suburb="WHITE ROCK", estate_name="WHITE ROCK ESTATE",
              state="QLD", state_source="suburb + e-agent page", postcode="2120",
              price=1084400.0, last_seen="28/09/2026"),
        _avia(2236, "50 Stage 2 425m2 $590,000 Registered Tropea Carolina 190m2 4 2 2 1 "
                    "$427,100 $1,017,100 Volos Available Owner Occ & Investor 30.07.2026",
              suburb="LOGAN RESERVE", estate_name="RIVERLEIGH ESTATE", state="QLD",
              state_source="suburb + e-agent page", lot_number="50", price=1017100.0,
              superseded_by="v2:8d699e1efff967feb2c87d35ec431b69f7d7f4462930eb7c65b16a6f710edf9e",
              last_seen="04/08/2026"),
        _avia(7810, _LINE_7810, suburb="GLENVALE", estate_name="THE HILLS COLLECTION",
              state="QLD", state_source="suburb + e-agent page", lot_number="60",
              price=892900.0),
        _avia(2178, "316 Stage 3 375m2 $540,000 August 2026 Tropea Carolina 190m2 4 2 2 1 "
                    "$425,200 $965,200 Volos Available Owner Occ Only 17.07.2026",
              suburb="EOI Form", estate_name="EOI Form", lot_number="316", price=965200.0,
              last_seen="29/07/2026"),
    ]


def _verv_sheet():
    """11494 (the NSW vote), 1825 (a superseded row that proves VIC by Bonnie Brook) and
    19094, the Jubilee lot, as enrich left it on the 29 Sep nightly: no state."""
    return [
        _row(11494, builder_name="Verv Projects", lot_address="5629 The Grove, Tarneit",
             source_text="2-Part Detached SS West 5629 The Grove Tarneit 300 $350,000 Enzo "
                         "10.5 3 / 2 / 2 $363,230 $713,230 Brochure Masterplan Arial Map "
                         "Stage 56 POS Sold",
             state="NSW", state_source="listing locality", source_state_hint="VIC",
             lot_number="5629", source_url=_VERV, stocklist_file=_VERV, price=713230.0,
             last_seen="03/09/2026"),
        _row(1825, builder_name="Verv Projects", suburb="Bonnie Brook",
             lot_address="2-Part Detached SS West 123 Oaklands Bonnie Brook 392 $430,000 "
                         "Cura 20B 4 / 2 / 2 $395,850 $825,850 Brochure M",
             source_text="2-Part Detached SS West 123 Oaklands Bonnie Brook 392 $430,000 "
                         "Cura 20B 4 / 2 / 2 $395,850 $825,850 Brochure Masterplan Estate "
                         "Brochure Stage 1 - POS Available",
             estate_name="Brochure Masterplan", state="VIC",
             state_source="suburb + e-agent page", source_state_hint="VIC",
             lot_number="123", source_url=_VERV, stocklist_file=_VERV, price=825850.0,
             superseded_by="v2:8f6f99a0329c1f3082be47899f9381e9d53a7c1d422675c2f75f4b5a887114a2",
             last_seen="29/07/2026"),
        _row(19094, builder_name="Verv Projects",
             lot_address="2-Part Detached SS West 4327 Jubilee Wyndhamvale 263 $338,000 "
                         "Enzo 10.5 3 / 2 / 2 $347,050 $685,050 Brochure M",
             source_text="2-Part Detached SS West 4327 Jubilee Wyndhamvale 263 $338,000 "
                         "Enzo 10.5 3 / 2 / 2 $347,050 $685,050 Brochure Masterplan Stage "
                         "43 - POS Available",
             source_state_hint="VIC", lot_number="4327", source_url=_VERV,
             stocklist_file=_VERV, price=685050.0),
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


# ------------------------------------------------------------ the number opening the row

def test_the_number_opening_the_row_is_its_lot_not_its_postcode(geo):
    """2234: no lot_number to compare, and still 2120 is its lot, not its postcode."""
    assert sr.postcode_is_lot_number("2120", None, (_LINE_2234, _LINE_2234[:110]), geo)
    assert sr.own_state(postcode="2120", suburb="WHITE ROCK", estate="WHITE ROCK ESTATE",
                        address=_LINE_2234[:110], geo=geo, lot_number=None,
                        row_text=(_LINE_2234,)) == ("", "")
    # PREMISE: the same row read without its own line still proves NSW off 2120.
    assert sr.own_state(postcode="2120", suburb="WHITE ROCK", estate="WHITE ROCK ESTATE",
                        address=_LINE_2234[:110], geo=geo,
                        lot_number=None) == ("NSW", "listing locality + postcode")


@pytest.mark.parametrize("postcode, line", [
    ("3029", "Lot 12 Smith Street, Tarneit VIC 3029"),       # after the suburb, as written
    ("3029", "Tarneit VIC 3029"),
    ("3029", "3029 Tarneit VIC"),                            # a line written postcode-first
    ("2120", "$2120 deposit, Lot 60 Stage 4"),               # a price, not a lot
    ("2601", "Lot GRD-T2-601 Unit 149, 70 Allara Street, CANBERRA, ACT, 2601"),
])
def test_a_real_postcode_is_not_taken_for_the_lot(geo, postcode, line):
    assert not sr.postcode_opens_the_row(postcode, line, geo)
    assert not sr.postcode_is_lot_number(postcode, "GRD-T2-601", (line,), geo)


def test_a_house_number_on_its_street_is_the_lot_even_when_the_street_is_a_place(geo):
    """'2954 Lockington Road' (APLACE 7614): Lockington is a VIC place and 2954 a NSW code,
    and '3064 Donnybrook Road' would be a VIC code before a VIC place -- a street's name."""
    assert sr.postcode_opens_the_row("2954", "2954 Lockington Road", geo)
    assert sr.postcode_opens_the_row("3064", "3064 Donnybrook Road", geo)


def test_the_avia_sheet_is_queensland_once_2120_is_read_as_the_lot(tmp_path, monkeypatch,
                                                                   capsys):
    db = _make_db(tmp_path, _avia_sheet())
    assert _enrich(monkeypatch, db) == 0
    after = _rows(db)
    # 2234 keeps the state its suburb and page gave it; the lot leaves its postcode column
    assert after[2234] == ("QLD", "suburb + e-agent page", None)
    # and the sheet is no longer national, so the row that proves nothing takes the page
    assert after[2178] == ("QLD", "e-agent page", None)
    assert after[7810][:2] == ("QLD", "suburb + e-agent page")
    out = capsys.readouterr().out
    assert "NATIONAL" not in out
    line = next(ln for ln in out.splitlines() if "id 2234" in ln)
    assert "2120" in line and "(NSW range)" in line
    before = _table(db)
    assert _second_run_writes(monkeypatch, db) == [], "the second run wrote again"
    assert _table(db) == before


_AVIA_WRITE = {
    "source_channel": "E-Agent", "attribution_scope": "builder", "builder_name": "AVIA Homes",
    "suburb": "WHITE ROCK", "postcode": "2120", "lot_address": _LINE_2234[:110],
    "source_text": _LINE_2234, "advertised_package_price": 1084400,
    "availability_status": "Available",
}


def test_a_harvest_does_not_store_the_number_opening_the_row_as_the_postcode():
    db = ResearchDatabase(db_path=Path(tempfile.mkdtemp()) / "write.db")
    assert db.record_building(dict(_AVIA_WRITE)) == "new"
    (row,) = db.get_buildings()
    assert row["postcode"] is None
    # postcode is not an identity field, so the row keeps the hash it would have had
    assert row["content_hash"] == building_content_hash(dict(_AVIA_WRITE))
    # a real postcode on the same row is still stored
    db2 = ResearchDatabase(db_path=Path(tempfile.mkdtemp()) / "write.db")
    db2.record_building(dict(_AVIA_WRITE, postcode="4306"))
    assert db2.get_buildings()[0]["postcode"] == "4306"


# ------------------------------------------------------------------- the blank-suburb guess

def test_the_guess_reads_the_locality_after_the_comma(geo):
    assert sr.suburb_in_row_text("5629 The Grove, Tarneit", geo, "VIC") == "Tarneit"
    assert sr.suburb_in_row_text("5629 The Grove, Tarneit", geo, "") == "Tarneit"
    # PREMISE: what enrich's guess used to read, with no state and with 11494's stored NSW
    assert geo.find_suburb_in_text("5629 The Grove, Tarneit", "") == "The Grove"
    assert geo.find_suburb_in_text("5629 The Grove, Tarneit", "NSW") == "The Grove"


def test_the_guess_does_not_take_the_rows_estate(geo):
    estate = "New Beith Round Mountain"
    assert sr.suburb_in_row_text(_LINE_11510, geo, "QLD", estate) == "New Beith"
    assert sr.suburb_in_row_text(_LINE_11510, geo, "", estate) == "New Beith"
    assert geo.find_suburb_in_text(_LINE_11510, "NSW") == "Round Mountain"      # PREMISE


def test_the_guess_does_not_take_a_house_design(geo):
    assert sr.suburb_in_row_text(_LINE_14913, geo, "", "Springvale Double") is None
    assert geo.find_suburb_in_text(_LINE_14913, "") == "Verona"                 # PREMISE
    magnolia = ("Option 1 Magnolia Traditional Double 4 3 2 9.9 336.4 219.96 Q1 2026 "
                "$1,049,710.00 $1,009,710.00")
    assert sr.suburb_in_row_text(magnolia, geo, "") is None
    # a design followed by its facade is not a place either -- Rhodes is NSW, 7810 is QLD
    assert sr.suburb_in_row_text(_LINE_7810, geo, "QLD") != "Rhodes"
    assert sr.suburb_in_row_text(_LINE_7810, geo, "") != "Rhodes"


@pytest.mark.parametrize("text, prefer, estate, want", [
    ("2-Part Detached SS Southeast 3016 Five Farms Clyde North Nov-26 350 $441,000", "VIC",
     "", "Clyde North"),
    ("Available Clyde North House 263 10.5 Langdon 16 3 2 2 $377,100", "", "",
     "Clyde North"),
    ("Lot 12 Point Cook Rd", "", "", "Point Cook"),
    ("Available Box Hill North House", "", "", "Box Hill North"),
    ("LOT 79 STELLA ST, COLAC 3250", "", "", "Colac"),          # a postcode after the suburb
    ("Lot 12 Smith Street, Tarneit VIC 3029", "", "", "Tarneit"),
    ("Lot 5 Wadalba Estate 400m2", "", "", "Wadalba"),          # an estate named after its suburb
    ("Innes Park Available 86 Innes Park Estate Dual Living", "QLD", "Innes Park",
     "Innes Park"),
    ("114 Neale Rd, Parkside", "VIC", "Parkside", "Parkside"),
    ("Jensen Rise Estate - Wadalba 49 Road #5 Wadalba 2259 CENTRAL COAST COUNCIL", "NSW", "",
     "Wadalba"),
    ("Lot 27102, Windermere Mambourin", "QLD", "Windermere Mambourin", "Mambourin"),
    ("2-Part Detached SS West 123 Oaklands Bonnie Brook 392 $430,000 Cura 20B", "VIC", "",
     "Bonnie Brook"),
    ("276 Glenvale Alford Grove", "QLD", "", "Glenvale"),        # the name after is a street's
    ("Available V1602 Leased 2 Bed 2 Bath 1 Study 1 in stage 2 North Flemington Racecourse "
     "90 29 119", "VIC", "", "Flemington"),                     # the name after is not a VIC place
    ("Unit B109/143 South Street, Marsden Park (Lot 1) (Block B)", "", "", "Marsden Park"),
])
def test_nearby_names_the_guess_still_reads(geo, text, prefer, estate, want):
    assert sr.suburb_in_row_text(text, geo, prefer, estate) == want


def test_the_verv_sheet_is_victorian_once_the_grove_is_read_as_the_estate(tmp_path,
                                                                          monkeypatch):
    """11494 is re-resolved -- the guess it was settled on no longer supports NSW -- and
    with its vote gone the sheet is VIC, so the Jubilee lot takes the page, contested
    because 'Jubilee' in its line is a Queensland place name."""
    db = _make_db(tmp_path, _verv_sheet())
    assert _enrich(monkeypatch, db) == 0
    after = _rows(db)
    assert after[11494][:2] == ("VIC", "suburb + e-agent page")
    assert after[19094][:2] == ("VIC", "e-agent page (conflicting signals)")
    assert after[1825][:2] == ("VIC", "suburb + e-agent page"), "a proving row is left"
    before = _table(db)
    assert _second_run_writes(monkeypatch, db) == [], "the second run wrote again"
    assert _table(db) == before


def test_a_state_that_rested_on_a_design_is_re_resolved(tmp_path, monkeypatch, capsys):
    """14913, NSW by 'suburb' on "Verona", the design. Nothing else in the row or its email
    names a state, so it goes blank -- which takes it out of NSW searches. Its twin 12348,
    the same lot read with its suburb column, is VIC and is left alone. (VIC for 14913
    would need a signal enrich does not have: the email's other rows, or the twin.)"""
    twin = ("Available Springvale South Titled Townhouse 44 215 10.86 Verona SG Corner | "
            "Roselle 15.72 Double Storey 3 2 1 $954,950")
    rows = [_row(14913, builder_name="Hattan Homes", source_channel="digital email",
                 lot_address=_LINE_14913, source_text=_LINE_14913,
                 estate_name="Springvale Double", state="NSW", state_source="suburb",
                 lot_number="44", source_url=_DEANSIDE, price=954950.0),
            _row(12348, builder_name="Hattan Homes", source_channel="digital email",
                 suburb="Springvale South", lot_address=twin[:110], source_text=twin,
                 estate_name="Roselle", state="VIC", state_source="listing text",
                 lot_number="44", source_url=_DEANSIDE, price=954950.0)]
    db = _make_db(tmp_path, rows)
    assert _enrich(monkeypatch, db) == 0
    after = _rows(db)
    assert after[14913] == (None, None, None)
    assert after[12348][:2] == ("VIC", "listing text")
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "id 14913" in ln)
    assert "NSW -> -" in line and "suburb now 'none'" in line and "Verona SG" in line
    assert _enrich(monkeypatch, db) == 0
    assert "the row no longer gives: 0" in capsys.readouterr().out


def test_a_guess_that_still_names_a_place_in_the_state_is_left_alone(tmp_path, monkeypatch):
    """19643, Invision, "5519 Celestial Way, WHITE ROCK (RIPLEY)", QLD by 'suburb + e-agent
    page' on the guess White Rock -- a place in NSW and QLD, so it still supports QLD and
    the state is not re-opened. Its postcode, 5519, is the house number opening the
    label, and that does come out."""
    sheet = "https://docs.google.com/spreadsheets/d/1Fo0ve2ZUMCXAylDJM-y4WAoyijvdifio/edit"
    row = _row(19643, builder_name="Invision Homes",
               lot_address="5519 Celestial Way, WHITE ROCK (RIPLEY)",
               source_text="White Rock Available REGISTERED White Rock 5519 Celestial Way 375 "
                           "$525,000 Warrego 18 Maple 182 Low Set 4 - 2- 2 -2 $464,981 "
                           "$989,981 ESSENCE PLUS $820 $3,000 $0 $1,000",
               estate_name="WHITE ROCK (RIPLEY)", state="QLD",
               state_source="suburb + e-agent page", source_state_hint="QLD",
               postcode="5519", source_url=sheet, stocklist_file=sheet, price=989981.0)
    db = _make_db(tmp_path, [row])
    assert _enrich(monkeypatch, db) == 0
    assert _rows(db)[19643] == ("QLD", "suburb + e-agent page", None)


# -------------------------------------------------------------- which rows make a file national

def test_a_file_stays_national_on_the_votes_of_its_superseded_rows(tmp_path, monkeypatch):
    """Hudson's PDF off the QLD page proves NSW and QLD -- but the rows that prove it are
    superseded: an E-Agent PDF is a snapshot, and newer files list the same lots. Tallied
    over live rows only (measured, and rejected, 29 Sep 2026), it would not be national,
    and 1519, lot 405 at Warnervale, NSW, which proves nothing itself, would take the QLD
    page. Every row votes, so it stays blank."""
    rows = [
        _row(1433, builder_name="Hudson Homes", suburb="Bellbird, Bellbird",
             lot_address="Available 527 House COPPER 19A 177.08m\u00b2 753.0 m\u00b2 4 beds "
                         "/ 2 baths / 2 cars 2-part Contract $941,230 Portal Lin",
             state="NSW", state_source="listing locality", estate_name="Mt View Grange",
             source_state_hint="QLD", lot_number="527", stocklist_file=_HUDSON,
             source_url="https://www.e-agent.com.au/_files/ugd/069fe0_d387ccc70c2744e28f55"
                        "b5ccdeeb36af.pdf",
             superseded_by="v2:c6fc708e86871baef9ee45a20a9e898262645e7ffce97347467565d7fe30bf3f",
             price=941230.0, last_seen="04/08/2026"),
        _row(1538, builder_name="Hudson Homes", suburb="Bethania QLD, Bethania",
             lot_address="Available 5 House SAMARIUM 19 181.13m\u00b2 360.0 m\u00b2 4 beds / "
                         "2 baths / 2 cars 2-part Contract $1,049,600 Portal Li",
             state="QLD", state_source="listing text", estate_name="Berlin Grove",
             source_state_hint="QLD", lot_number="5", stocklist_file=_HUDSON,
             source_url="https://www.e-agent.com.au/_files/ugd/069fe0_09210148469644349"
                        "3bf0f6f8df49562.pdf",
             superseded_by="v2:fdd82a5782b22e07eaf1b00115d36f2bcd0c16df844674f50e4b6eda97ae639e",
             price=1049600.0, last_seen="04/08/2026"),
        _row(1519, builder_name="Hudson Homes", suburb="Warnervale",
             lot_address="Available 405 House SILVER 23 210.63m\u00b2 471.0 m\u00b2 4 beds / "
                         "2 baths / 2 cars 2-part Contract $903,820 Portal Link",
             estate_name="Aberlyn", source_state_hint="QLD", lot_number="405",
             stocklist_file=_HUDSON, source_url=_HUDSON, price=903820.0,
             last_seen="29/07/2026"),
    ]
    db = _make_db(tmp_path, rows)
    assert _enrich(monkeypatch, db) == 0
    assert _rows(db)[1519] == (None, None, None)


# -------------------------------------------------------------------- the replay's text rule

def test_a_design_and_its_facade_do_not_name_a_place_in_another_state(geo):
    """The nightly replay's warning on 7810 every night, and 12359 once the AVIA sheet was
    filed QLD. Rhodes NSW is stocked (Meriton's Rhodes apartments)."""
    stocked = {("rhodes", "NSW")}
    assert sr.text_locality_elsewhere(_LINE_7810, "QLD", geo, stocked) is None
    assert sr.text_locality_elsewhere(_LINE_12359, "QLD", geo, stocked) is None
    # the check can still fail: the same word written as a place in the same row
    placed = _LINE_7810 + " Display home: Rhodes"
    assert sr.text_locality_elsewhere(placed, "QLD", geo, stocked) == ("Rhodes", "NSW")


def test_a_design_name_is_what_a_suburb_state_can_be_seen_to_rest_on(geo):
    assert sr.design_the_state_rests_on(_LINE_14913, "", "NSW", geo) == "Verona"
    assert sr.design_the_state_rests_on(_LINE_14913, "Verona", "NSW", geo) == "Verona"
    # not a suburb followed by its design ("Camira Banksia 183 - Classic", 7864) ...
    camira = ("4B 4 Pilny Street Camira Banksia 183 - Classic Available 4 2 2 501 $ 420,900 "
              "$ 695,000 $ 1,115,900 Split Aug-26")
    assert sr.design_the_state_rests_on(camira, "Camira", "QLD", geo) is None
    # ... nor a name the row also writes as its place
    assert sr.design_the_state_rests_on(
        "Available Clyde North House 263 10.5 Langdon 16 Single Storey", "Clyde North",
        "VIC", geo) is None
