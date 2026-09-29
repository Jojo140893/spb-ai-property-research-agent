"""A word or number beside the lot's locality is not evidence of its state -- review round.

29 Sep 2026. Two independent reviews of the first cut (tests/test_estate_design_and_lot_
are_not_places.py) found it right for the night's rows but wrong for shapes the night did
not happen to hold, and the re-measurement found two more stocklists that were national
on a misreading:

  * the rule that two place names side by side are a design and its facade ("Rhodes
    Paddington") blinded the row-text check to the commonest misfile of all: the real
    suburb and then the word that caused the misfile -- "Trawler Walk Tarneit The Grove"
    filed NSW on a suburb column 'The Grove'. Planted that way, origin/main moved 7 of 7
    to their state and the first cut 0. A repeated name ("Mambourin Mambourin Green") was
    counted as a second place too;
  * a name refused or doubted let a shorter name inside it through: "Fraser Rise Double
    Storey" guessed Fraser, ACT; "Marsden Park Single Storey" Marsden, QLD;
  * storey counts were read as design variants, so "Lot 50, Springvale Double" and
    "Available Tarneit Double" lost their suburbs, and the design WARN could fire on them;
  * 'e-agent page (conflicting signals)' on a guessed suburb went through the row-text
    overrule, leaving the Jubilee lots one stocked name away from Queensland;
  * the design list missed the data's own typos ("Magnolia Tradtional", "Verona
    SGCormer"), and "3978 - Clyde North" lost its real postcode;
  * APLACE's VIC sheet was national on a land area read as a postcode ("Titled 2064 39 x
    54"), and Creation Homes' QLD sheet on a street name in the suburb column ("21 The
    Grove Boondall ...").

The fixtures are real rows of the 29 Sep nightly under their real ids, carrying only the
columns the rules read, with stand-in content hashes and each sheet cut down to the rows
a test needs. The enrich tests run enrich_buildings.main() over a temporary database.
"""
import sqlite3
import tempfile
from pathlib import Path

import pytest

import config
import enrich_buildings
import state_resolver as sr
from database import ResearchDatabase
from geo import SuburbGeoIndex

_COLS = ("id", "builder_name", "source_channel", "lot_address", "suburb", "state",
         "state_source", "estate_name", "source_text", "source_url", "stocklist_file",
         "source_state_hint", "postcode", "lot_number", "superseded_by", "content_hash",
         "price", "last_seen")

_TEXT = enrich_buildings.TEXT_LOCALITY_SOURCE
_PARAMOUNT = "https://www.e-agent.com.au/_files/ugd/069fe0_240bf95ec9644b07ae7514e55c6f1495.pdf"
_VERV = "https://docs.google.com/spreadsheets/d/1bPh8W2Bujp8DHpNknSv6h0LkiVnsxrIqN66PpvWfI4c/edit"
_APLACE = "https://www.e-agent.com.au/_files/ugd/069fe0_e9adabc931f64c2f92b689ef829a12a6.pdf"
_CREATION = "https://www.e-agent.com.au/_files/ugd/069fe0_8ed5ddfe9f884d999c90bd7fa2afcf0f.pdf"
_APLACE_ESTATE = "Stock highlighted in orange below is eligible for the 100% commission offer."

_LINE_22859 = ("Lot 5733 Trawler Walk Tarneit The Grove Q3 2027 162sqm $187,600 Split 3 2 2 "
               "173.34sqm $437,400 $625,000")
_LINE_12153 = ("Yarrawonga 2077 Buloke Circuit Glanmire Park Titled 2064 39 x 54 $390,000 "
               "Gower 02 181.86 4 2.5 2 $476,500 $866,500 General")
_LINE_993 = ("28 The Grove Boondall Rosewood 215 - Coastal Available 4 2 2 385 $ 533,650 "
             "$ 857,750 $ 1,391,400 Split Sep-26")
_LINE_19094 = ("2-Part Detached SS West 4327 Jubilee Wyndhamvale 263 $338,000 Enzo 10.5 3 / "
               "2 / 2 $347,050 $685,050 Brochure Masterplan Stage 43 - POS Available")


@pytest.fixture(scope="module")
def geo():
    return SuburbGeoIndex()


def _row(rid, **kw):
    row = {c: None for c in _COLS}
    row.update(id=rid, content_hash=f"v2:test{rid:05d}", source_channel="E-Agent",
               builder_name="", last_seen="29/09/2026")
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


# ------------------------------------------- a suburb and the word that caused the misfile

@pytest.mark.parametrize("text, state, suburb, want", [
    # the misfile itself: the suburb column holds the word after the real suburb
    (_LINE_22859, "NSW", "The Grove", ("Tarneit", "VIC")),
    ("Tarneit The Grove Q3 2027 162sqm", "NSW", "The Grove", ("Tarneit", "VIC")),
    # a place written straight after a street type is the address's locality
    ("3 63 Oxford Street Joyner Banksia 183 - Classic Available", "NSW", "Banksia",
     ("Joyner", "QLD")),
    ("19 The Grove Boondall Rosewood 215 - Urban Available", "NSW", "The Grove",
     ("Boondall", "QLD")),
    # a name followed by itself is the suburb and the estate named after it
    ("Lot 516 Partnership Way Mambourin Mambourin Green Q1 2027 269sqm", "NSW", "Mambourin",
     ("Mambourin", "VIC")),
    ("Single Contract Fraser Rise Fraser Rise 0 Single 0 0 0 $649,900", "NSW", "Fraser Rise",
     ("Fraser Rise", "VIC")),
    ("1 Viva Rochedale Rochedale Rosewood 215 - Urban Available", "NSW", "Viva",
     ("Rochedale", "QLD")),
])
def test_the_row_text_check_still_sees_a_suburb_before_the_misleading_word(geo, text, state,
                                                                          suburb, want):
    stocked = {("tarneit", "VIC"), ("joyner", "QLD"), ("boondall", "QLD"),
               ("mambourin", "VIC"), ("fraser rise", "VIC"), ("rochedale", "QLD")}
    assert sr.text_locality_elsewhere(text, state, geo, stocked, suburb=suburb) == want


@pytest.mark.parametrize("text, state, suburb", [
    # a design and its facade, filed in the facade's state (AVIA 7810): no alarm
    ("60 Stage 4 429m2 $390,000 August 2026 Rhodes Paddington 180m2 4 2 2 0", "QLD",
     "GLENVALE"),
    # an estate and its suburb, filed in the suburb's state (Verv 1825), no suburb column
    ("2-Part Detached SS West 123 Oaklands Bonnie Brook 392 $430,000", "VIC", ""),
    # ... and with its suburb column, which the catalogue stocks there: the column is not
    # the word under suspicion, so this is not the "Tarneit The Grove" misfile
    ("2-Part Detached SS West 123 Oaklands Bonnie Brook 392 $430,000", "VIC", "Bonnie Brook"),
])
def test_a_design_or_estate_beside_a_place_of_the_rows_state_is_still_let_go(geo, text,
                                                                           state, suburb):
    stocked = {("rhodes", "NSW"), ("oaklands", "NSW"), ("bonnie brook", "VIC")}
    assert sr.text_locality_elsewhere(text, state, geo, stocked, suburb=suburb) is None


def test_the_overrule_corrects_the_misfile_a_suburb_column_makes(tmp_path, monkeypatch):
    """22859 planted the way a suburb-column misfile looks: the column holds 'The Grove',
    the estate after Tarneit, and the state is NSW by 'suburb'. Its sibling 22860 is the
    stocked Tarneit lot. origin/main moved it to VIC; the first cut left it NSW."""
    rows = [_row(22859, builder_name="Paramount Living", lot_address="5733 Trawler Walk",
                 suburb="The Grove", state="NSW", state_source="suburb",
                 source_text=_LINE_22859, source_state_hint="VIC", lot_number="5733",
                 source_url=_PARAMOUNT, stocklist_file=_PARAMOUNT, price=625000.0),
            _row(22860, builder_name="Paramount Living", lot_address="5734 Trawler Walk",
                 suburb="Tarneit", state="VIC", state_source="suburb + e-agent page",
                 source_text=_LINE_22859.replace("5733", "5734"), source_state_hint="VIC",
                 lot_number="5734", source_url=_PARAMOUNT, stocklist_file=_PARAMOUNT,
                 price=615000.0)]
    db = _make_db(tmp_path, rows)
    assert _enrich(monkeypatch, db) == 0
    after = _rows(db)
    assert after[22859][:2] == ("VIC", _TEXT)
    assert after[22860][:2] == ("VIC", "suburb + e-agent page")
    assert _second_run_writes(monkeypatch, db) == []


# --------------------------------------------------- a name inside a longer name, a storey

@pytest.mark.parametrize("text, prefer, want", [
    ("Available Lot 12 Fraser Rise Double Storey 4 2 2", "", "Fraser Rise"),
    ("Available Lot 12 Marsden Park Single Storey 4 2 2", "", "Marsden Park"),
    ("Available Lot 12 Marsden Park Single Storey 4 2 2", "QLD", "Marsden Park"),
    ("Available Clyde North Double Storey", "NSW", "Clyde North"),
    ("Lot 5 Clyde North", "NSW", "Clyde North"),
    ("Lot 5 Marsden Park Modern", "NSW", None),       # a design's facade, and nothing inside it
])
def test_a_name_inside_a_longer_place_name_is_not_a_second_place(geo, text, prefer, want):
    assert sr.suburb_in_row_text(text, geo, prefer) == want


def test_fraser_rise_is_victorian_not_the_act(geo):
    guess = sr.suburb_in_row_text("Available Lot 12 Fraser Rise Double Storey 4 2 2", geo)
    assert sr.resolve_state(suburb=guess, geo=geo) == ("VIC", "suburb")


@pytest.mark.parametrize("text, prefer, want", [
    ("Available Tarneit Double 4 2 2", "VIC", "Tarneit"),
    ("Lot 50, Springvale Double", "VIC", "Springvale"),          # 11 Hattan rows
    ("4 Pilny Street Camira Banksia 183 - Classic", "", "Camira"),  # Camira, after the street
])
def test_a_suburb_before_a_storey_count_or_its_design_is_still_the_guess(geo, text, prefer,
                                                                       want):
    assert sr.suburb_in_row_text(text, geo, prefer) == want


def test_a_storey_count_does_not_make_a_suburb_a_design(geo):
    assert sr.design_the_state_rests_on("Available Tarneit Double 4 2 2", "Tarneit", "VIC",
                                        geo) is None


@pytest.mark.parametrize("text", [
    "Option 1 Magnolia Tradtional Double 4 3 2 10m 317.3 219.96 Q1 2026",        # 12736
    "Available Titled Townhouse 44 215 10.86 Verona SGCormer|Roselle 15.72 3 2 1",  # 17430
])
def test_the_datas_own_typos_of_a_design_variant_are_design_variants(geo, text):
    assert sr.suburb_in_row_text(text, geo, "") is None
    assert sr.design_the_state_rests_on(text, "", "NSW" if "Verona" in text else "QLD",
                                        geo) in ("Magnolia", "Verona")


@pytest.mark.parametrize("text, prefer, want", [
    ("16 Gardner Road, SOUTHERN HIGHLANDS", "NSW", None),        # not Highlands, VIC
    ("251 Cameron Grove, LAKE MACQUARIE", "NSW", None),          # not Macquarie, ACT/TAS
    # NOT after North: it starts a development's name as well as a region's
    ("Available V1602 Leased 2 Bed 2 Bath 1 Study 1 in stage 2 North Flemington Racecourse",
     "VIC", "Flemington"),
])
def test_the_tail_of_a_regions_name_is_not_the_guess(geo, text, prefer, want):
    assert sr.suburb_in_row_text(text, geo, prefer) == want


# ------------------------------------------ the page beat the label: a narrower second look

def test_a_page_state_that_beat_a_bare_word_is_not_overruled(tmp_path, monkeypatch):
    """Verv 19094, "... West 4327 Jubilee Wyndhamvale ...": the guess is Jubilee, a QLD
    place, the page is VIC and wins, labelled contested. The first cut sent that state
    through the row-text overrule, so a live 'Jubilee' QLD row anywhere moved it to
    Queensland. Here one is stocked."""
    rows = [_row(19094, builder_name="Verv Projects", lot_address=_LINE_19094[:110],
                 source_text=_LINE_19094, source_state_hint="VIC", lot_number="4327",
                 source_url=_VERV, stocklist_file=_VERV, price=685050.0),
            _row(30001, builder_name="Other Builder", suburb="Jubilee", state="QLD",
                 state_source="listing text", lot_address="Lot 7, Jubilee Road, Jubilee QLD",
                 price=700000.0)]
    db = _make_db(tmp_path, rows)
    assert _enrich(monkeypatch, db) == 0
    assert _rows(db)[19094][:2] == ("VIC", "e-agent page (conflicting signals)")


def test_only_a_label_that_states_its_locality_opens_that_state_again(geo):
    assert sr.label_locality("Lot 27102, Windermere Mambourin", geo) == "Mambourin"
    assert sr.label_locality("5629 The Grove, Tarneit", geo) == "Tarneit"
    assert sr.label_locality(_LINE_19094[:110], geo) is None
    assert sr.label_locality("803 Cameron Grove, SOUTHERN HIGHLANDS", geo) is None
    assert not enrich_buildings.rests_on_a_suburb_match(
        "e-agent page (conflicting signals)", suburb_from_column=False)


# ------------------------------------------------------------- a postcode written first

@pytest.mark.parametrize("postcode, line, lot", [
    ("3978", "3978 - Clyde North", False),        # a postcode written first, a dash between
    ("3029", "3029 Tarneit VIC", False),
    ("2765", "2765 Clyde North Available", True),  # Clyde North is VIC; 2765 is a NSW code
    ("2560", "2560 Springfield Lakes", True),      # Springfield Lakes is QLD
    ("3064", "3064 Donnybrook Road", True),        # a house on its street
])
def test_the_longest_name_after_an_opening_number_decides_what_it_is(geo, postcode, line,
                                                                     lot):
    assert sr.postcode_is_lot_number(postcode, None, (line,), geo) is lot


# ------------------------------------------------- a postcode written as another number

@pytest.mark.parametrize("postcode, line", [
    ("2064", _LINE_12153),                                             # a land area
    ("7076", "RO7 74.00 74.00 STORAGF Cold Shell 03-7076 71,550.00$ 7,851.00"),
    ("7076", "R73 48.00 48.00 STORAGF Cold Shell 0.3-7076 14,450.30 1,946.00"),
    ("2730", "West End Commercial S26 & S33 S26 & S33 2730.00 2730.00 $3,330,000"),
    ("2576", "Lot 50 1 1 1-4 196 $776,895 Lot 39-40: $2576.1 per year"),
    ("2066", "Available Lot 2, 37-39 House Carolina 24 2066.91m\u00b2 400.0 m\u00b2 4 beds"),
])
def test_a_number_the_row_writes_as_another_number_is_no_postcode(geo, postcode, line):
    assert sr.postcode_is_lot_number(postcode, None, (line,), geo)


@pytest.mark.parametrize("postcode, line", [
    ("3029", "Lot 5 Tarneit 3029 12.5 x 28"),          # before its frontage and depth
    ("3029", "Lot 5 Tarneit VIC 3029 12.5 x 28"),
    ("3217", "Lot 1205 Banksia, Seaberry Street, ARMSTRONG CREEK, VIC, 3217"),
    ("3029", "Tarneit 3029, 12.5 x 28 then Tarneit 3029"),
    ("2064", "a row that does not write the number at all"),
])
def test_a_real_postcode_is_kept(geo, postcode, line):
    assert not sr.postcode_is_lot_number(postcode, None, (line,), geo)


def test_the_land_area_proves_nothing(geo):
    kwargs = dict(postcode="2064", suburb="Yarrawonga", estate=_APLACE_ESTATE,
                  address="2077 Buloke Circuit, Glanmire Park Titled", geo=geo)
    assert sr.own_state(row_text=(_LINE_12153,), **kwargs) == ("", "")
    assert sr.own_state(**kwargs) == ("NSW", "suburb + postcode")          # PREMISE


def test_a_harvest_does_not_store_a_land_area_as_the_postcode():
    db = ResearchDatabase(db_path=Path(tempfile.mkdtemp()) / "write.db")
    db.record_building({"source_channel": "E-Agent", "attribution_scope": "builder",
                        "builder_name": "APLACE by Glenville", "suburb": "Yarrawonga",
                        "postcode": "2064", "source_text": _LINE_12153,
                        "lot_address": "2077 Buloke Circuit, Glanmire Park Titled",
                        "advertised_package_price": 866500,
                        "availability_status": "Available"})
    assert db.get_buildings()[0]["postcode"] is None


def _aplace_sheet():
    """12153 (the NSW vote, land area 2064), 12089 (proves VIC, Clyde North) and 12147, a
    Benalla lot that proves nothing itself and was blank while the sheet was national."""
    return [
        _row(12153, builder_name="APLACE by Glenville", suburb="Yarrawonga",
             lot_address="2077 Buloke Circuit, Glanmire Park Titled", source_text=_LINE_12153,
             estate_name=_APLACE_ESTATE, state="NSW", state_source="suburb + postcode",
             source_state_hint="VIC", postcode="2064", source_url=_APLACE,
             stocklist_file=_APLACE, price=866500.0),
        _row(12089, builder_name="APLACE by Glenville", suburb="Clyde North",
             lot_address="145 Hadar Way",
             source_text="Clyde North 145 Hadar Way Alma Dec-26 294 10.5 x 28 $397,000 "
                         "Mackay 03 167.08 4 2 2 $364,850 $761,850 Aplace Exclusive",
             estate_name=_APLACE_ESTATE, state="VIC", state_source="listing locality",
             source_state_hint="VIC", source_url=_APLACE, stocklist_file=_APLACE,
             price=761850.0, last_seen="24/09/2026"),
        _row(12147, builder_name="APLACE by Glenville", suburb="Benalla",
             lot_address="Benalla 508 Road Livingston Mar-27 560 14 x 40 $220,000",
             source_text="Benalla 508 Road Livingston Mar-27 560 14 x 40 $220,000 Selina 05 "
                         "203.27 4 2 2 $440,700 $660,700 Aplace Exclusive",
             estate_name=_APLACE_ESTATE, source_state_hint="VIC", source_url=_APLACE,
             stocklist_file=_APLACE, price=660700.0),
    ]


def test_the_aplace_sheet_is_victorian_once_its_land_area_is_no_postcode(tmp_path,
                                                                        monkeypatch, capsys):
    db = _make_db(tmp_path, _aplace_sheet())
    assert _enrich(monkeypatch, db) == 0
    after = _rows(db)
    assert after[12153] == ("VIC", "suburb + e-agent page", None)
    assert after[12147] == ("VIC", "suburb + e-agent page", None)
    assert after[12089][:2] == ("VIC", "listing locality")
    out = capsys.readouterr().out
    assert "NATIONAL" not in out
    assert "id 12153" in out and "2064" in out
    before = _table(db)
    assert _second_run_writes(monkeypatch, db) == []
    assert _table(db) == before


# --------------------------------------------------------- a street in the suburb column

def test_a_suburb_cell_that_is_the_rows_own_street_proves_nothing(geo):
    kwargs = dict(suburb="The Grove", address="28 The Grove", geo=geo)
    assert sr.own_state(row_text=(_LINE_993,), **kwargs) == ("", "")
    assert sr.own_state(**dict(kwargs, address="")) == ("NSW", "listing locality")  # PREMISE
    # not written after a house number: left alone
    assert sr.own_state(suburb="The Grove", address="Lot 5, The Grove", geo=geo) == \
        ("NSW", "listing locality")
    assert not sr.suburb_cell_is_the_street("Clyde North", ("145 Clyde North",))


def _creation_sheet():
    """993 (NSW over the QLD page, on 'The Grove'), 980 (QLD by Logan Reserve) and 970, a
    Camira lot that proves nothing itself."""
    return [
        _row(993, builder_name="Creation Homes", suburb="The Grove", lot_address="28 The Grove",
             source_text=_LINE_993, state="NSW", state_source="listing text (over QLD)",
             source_state_hint="QLD", source_url=_CREATION, stocklist_file=_CREATION,
             price=1391400.0, last_seen="29/07/2026"),
        _row(980, builder_name="Creation Homes", suburb="Logan Reserve", lot_address="Lot 80",
             source_text="80 Riverleigh Logan Reserve Cherry 149 - Modern Available 4 2 1 "
                         "416 $ 384,900 $ 620,000 $ 1,004,900 Split Registered",
             state="QLD", state_source="listing locality", source_state_hint="QLD",
             lot_number="80", source_url=_CREATION, stocklist_file=_CREATION,
             price=1004900.0, last_seen="29/07/2026"),
        _row(970, builder_name="Creation Homes", lot_address="4 Pilny Street",
             source_text="4A 4 Pilny Street Camira Banksia 183 - Urban Available 4 2 2 501 "
                         "$ 430,900 $ 690,000 $ 1,120,900 Split Aug-26",
             source_state_hint="QLD", source_url=_CREATION, stocklist_file=_CREATION,
             price=1120900.0, last_seen="04/08/2026"),
    ]


def test_the_creation_sheet_is_queensland_once_the_grove_is_read_as_a_street(tmp_path,
                                                                           monkeypatch,
                                                                           capsys):
    db = _make_db(tmp_path, _creation_sheet())
    assert _enrich(monkeypatch, db) == 0
    after = _rows(db)
    # the overrule of the page lost its only evidence, so the page is back -- contested,
    # because 'The Grove' in the suburb column is still a NSW name
    assert after[993][:2] == ("QLD", "e-agent page (conflicting signals)")
    assert after[970][:2] == ("QLD", "e-agent page")
    assert after[980][:2] == ("QLD", "listing locality")
    out = capsys.readouterr().out
    assert "NATIONAL" not in out
    line = next(ln for ln in out.splitlines() if "id 993" in ln)
    assert "NSW -> QLD" in line and "listing text (over QLD)" in line
    before = _table(db)
    assert _second_run_writes(monkeypatch, db) == []
    assert _table(db) == before


def test_a_proof_over_the_page_that_still_holds_is_left_alone(tmp_path, monkeypatch):
    """Hudson 1429-style: 'listing text (over QLD)' that the row still proves stays."""
    rows = [_row(1429, builder_name="Hudson Homes",
                 suburb="Leppington HomeWorld Display Homes, Leppington",
                 lot_address="Available 12 House COPPER 19A", state="NSW",
                 state_source="listing text (over QLD)", source_state_hint="QLD",
                 price=900000.0)]
    db = _make_db(tmp_path, rows)
    assert _enrich(monkeypatch, db) == 0
    assert _rows(db)[1429][:2] == ("NSW", "listing text (over QLD)")
