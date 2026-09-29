"""A state that rests only on a suburb or estate-name match gives way to the row's own text.

Colin, 15 Sep 2026, "QLD under $750k": one of the two results was "Lot 618, Riverhills QLD",
$553,284. Its line reads "Available Wollert Titled Townhouse 618 ...". Wollert is in
Victoria; the email extractor had put the ESTATE, Riverhills, in the suburb column, and
Riverhills is also a Brisbane suburb and nowhere else, so resolve_state's lone gazetteer
vote made it Queensland. enrich then treated the state as settled and never looked again.

On the 17 Sep copy the check moves exactly 25 live rows (14905 Wollert, 9565/9566
Armstrong Creek, 9568-9571 Mambourin, 18659 Murray Bridge, 10810-10817/10824/10825
Officer, 15819/15842-15844/19858 Fraser Rise, 8349 Clyde North, 15568 Moss Vale), each
reviewed by hand. The negative controls are the shapes a looser version misfired on.

Every test here runs enrich_buildings.main() over a temporary database.
"""
import sqlite3

import pytest

import config
import enrich_buildings
from database import ResearchDatabase
from geo import SuburbGeoIndex
from state_resolver import TEXT_LOCALITY_SOURCE, text_locality_elsewhere

_COLS = ("id", "builder_name", "source_channel", "lot_address", "suburb", "state",
         "state_source", "estate_name", "source_text", "source_url", "stocklist_file",
         "source_state_hint", "postcode", "superseded_by", "content_hash", "price")

_WOLLERT_LINE = ("Available Wollert Titled Townhouse 618 112 4.5 14.07 2 2 1 "
                 "$143,389 $409,895 $553,284")
_WINDERMERE_LINE = ("Lot 27102 Bounty X Inv/OO 400 sqm 184 sqm 4 2 1 2 $340,000 $401,900 "
                    "$741,900 Titled Available Brochure Link $1k Initial Deposit, 14 Days Fin")
_MURRAY_LINE = ("Murray Bridge, South $853,000.00 501 m² 223 m² 2 2 $900.00 "
                "October 2026 Available")


def _row(rid, **kw):
    row = {c: None for c in _COLS}
    row.update(id=rid, content_hash=f"v2:test{rid:04d}", source_channel="E-Agent",
               builder_name="", source_url=f"https://example.test/list-{rid}.pdf")
    row.update(kw)
    return row


def _riverhills_14905(rid=14905, **kw):
    """The row Colin was shown: estate in the suburb column, state from that match alone."""
    row = _row(rid, builder_name="Hattan Homes", source_channel="digital email",
               suburb="Riverhills", estate_name="Riverhills", state="QLD",
               state_source="suburb", lot_address=_WOLLERT_LINE[:80],
               source_text=_WOLLERT_LINE, price=553284.0,
               source_url="email:[Hattan weekly update] High-yield sub 600k packages")
    row.update(kw)
    return row


def _wollert_vic_sibling(rid=553):
    """Live VIC stock filed under Wollert -- what makes Wollert a place stock is sold in."""
    return _row(rid, builder_name="Hermitage Homes", suburb="Wollert", state="VIC",
                state_source="listing text", estate_name="Mason Quarter , Wollert",
                lot_address="1015 Mica Rd, Mason Quarter , Wollert",
                source_text="1015 Mica Rd 350 Jan-27 $443,000 Elwood 20L Duke 4 Single 191 "
                            "$386,290 $829,290 Available")


def _windermere_9568(rid=9568, **kw):
    """VIC lot on E-Agent's Queensland page: 'Windermere' guessed out of the address."""
    row = _row(rid, builder_name="Prosperity Residential", estate_name="Windermere Mambourin",
               lot_address="Lot 27102, Windermere Mambourin", source_text=_WINDERMERE_LINE,
               state="QLD", state_source="suburb + e-agent page", source_state_hint="QLD",
               stocklist_file="https://onedrive.example.test/prosperity.xlsx", price=741900.0)
    row.update(kw)
    return row


def _mambourin_vic_sibling(rid=452):
    return _row(rid, builder_name="Paramount Living", suburb="Mambourin", state="VIC",
                state_source="listing text", lot_address="516 Partnership Way",
                source_text="Lot 516 Partnership Way Mambourin Mambourin Green Q1 2027 "
                            "269sqm Single 4 2 1 148.39sqm $699,000 $699,000")


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


def _states(db):
    conn = sqlite3.connect(str(db))
    try:
        return {rid: (state, source) for rid, state, source in
                conn.execute("SELECT id, state, state_source FROM buildings")}
    finally:
        conn.close()


def _table(db):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT * FROM buildings ORDER BY id").fetchall()
    finally:
        conn.close()


def _enrich(monkeypatch, db, *argv):
    monkeypatch.setattr(config, "DATABASE_PATH", db)
    return enrich_buildings.main(list(argv))


# ------------------------------------------------------------------ the misfiled rows

def test_a_townhouse_whose_line_names_wollert_leaves_queensland(tmp_path, monkeypatch):
    """(a) id 14905. Settled on 'suburb', so today's enrich never looks at it again."""
    db = _make_db(tmp_path, [_riverhills_14905(), _wollert_vic_sibling()])
    assert _enrich(monkeypatch, db) == 0
    assert _states(db)[14905] == ("VIC", TEXT_LOCALITY_SOURCE)
    assert _states(db)[553] == ("VIC", "listing text"), "the sibling is evidence, not a target"


def test_murray_bridge_south_is_south_australia_not_murray_queensland(tmp_path, monkeypatch):
    """(b) id 18659. No suburb column; the address guess finds 'Murray', a QLD locality."""
    rows = [_row(18659, lot_address=_MURRAY_LINE, source_text=_MURRAY_LINE,
                 estate_name="Australia, 5114", state="QLD", state_source="suburb",
                 price=853000.0),
            _row(9971, builder_name="Land Build Direct", suburb="Murray Bridge", state="SA",
                 state_source="listing text", estate_name="RIVERVIEW ESTATE, Murray Bridge",
                 lot_address="Lot 506, RIVERVIEW ESTATE, Murray Bridge")]
    db = _make_db(tmp_path, rows)
    assert _enrich(monkeypatch, db) == 0
    assert _states(db)[18659] == ("SA", TEXT_LOCALITY_SOURCE)


def test_windermere_mambourin_off_the_queensland_page_is_victorian(tmp_path, monkeypatch):
    """(c) id 9568. 'suburb + e-agent page' on a GUESSED suburb rests on the name alone."""
    db = _make_db(tmp_path, [_windermere_9568(), _mambourin_vic_sibling()])
    assert _enrich(monkeypatch, db) == 0
    assert _states(db)[9568] == ("VIC", TEXT_LOCALITY_SOURCE)


def test_a_state_being_resolved_now_gets_the_same_check(tmp_path, monkeypatch):
    """Case (i): no state stored yet, and resolve_state's answer is a lone suburb vote.

    Without this the row would be written QLD tonight and moved to VIC tomorrow, and the
    answer would depend on which night the check first saw it.
    """
    db = _make_db(tmp_path, [_riverhills_14905(state=None, state_source=None),
                             _wollert_vic_sibling()])
    assert _enrich(monkeypatch, db) == 0
    assert _states(db)[14905] == ("VIC", TEXT_LOCALITY_SOURCE)


def test_a_suburb_column_holding_the_builders_estate_follows_that_estate(tmp_path, monkeypatch):
    """The estate half of the rule: the text names no place, but the suburb value IS the
    estate name and the same builder's other live rows of that estate are all in VIC."""
    line = "Available Titled Townhouse 620 112 4.5 14.07 2 2 1 $143,389 $409,895 $553,284"
    rows = [_riverhills_14905(lot_address=line, source_text=line),
            _row(19510, builder_name="Hattan Homes", suburb="Wollert", state="VIC",
                 state_source="suburb + e-agent page", estate_name="Riverhills",
                 source_state_hint="VIC", lot_address="Lot 618, Riverhills")]
    db = _make_db(tmp_path, rows)
    assert _enrich(monkeypatch, db) == 0
    assert _states(db)[14905] == ("VIC", TEXT_LOCALITY_SOURCE)


@pytest.mark.parametrize("builder, also_in_qld", [
    ("Hattan Homes", True),     # the builder has the estate in both states: says nothing
    ("", False),                # no builder: every unnamed builder's "Riverhills" at once
])
def test_an_estate_name_that_does_not_single_out_one_other_state_proves_nothing(
        tmp_path, monkeypatch, builder, also_in_qld):
    line = "Available Titled Townhouse 620 112 4.5 14.07 2 2 1 $143,389 $409,895 $553,284"
    rows = [_riverhills_14905(lot_address=line, source_text=line, builder_name=builder),
            _row(19510, builder_name=builder, suburb="Wollert", state="VIC",
                 state_source="suburb + e-agent page", estate_name="Riverhills",
                 source_state_hint="VIC", lot_address="Lot 618, Riverhills")]
    if also_in_qld:
        rows.append(_row(19600, builder_name=builder, suburb="Riverhills", state="QLD",
                         state_source="postcode", postcode="4074", estate_name="Riverhills",
                         lot_address="Lot 12, Riverhills"))
    db = _make_db(tmp_path, rows)
    assert _enrich(monkeypatch, db) == 0
    assert _states(db)[14905] == ("QLD", "suburb")


def test_a_place_with_no_stock_there_is_not_evidence():
    """Wollert is VIC-only in the gazetteer, but that alone overrules nothing: the check
    needs live stock filed there, or every one-state word in a line would be a vote."""
    geo = SuburbGeoIndex()
    assert text_locality_elsewhere(_WOLLERT_LINE, "QLD", geo, set()) is None
    assert text_locality_elsewhere(_WOLLERT_LINE, "QLD", geo, {("wollert", "VIC")}) == \
        ("Wollert", "VIC")
    assert text_locality_elsewhere(_WOLLERT_LINE, "VIC", geo, {("wollert", "VIC")}) is None


def test_a_month_is_not_a_place():
    """March is a locality in NSW and nowhere else. 'Titles expected March' is a date."""
    geo = SuburbGeoIndex()
    line = "Available Titled House Lot 12 4 2 2 $650,000 Titles expected March"
    assert text_locality_elsewhere(line, "QLD", geo, {("march", "NSW")}) is None


# ------------------------------------------------------------------ negative controls

def test_wynnum_in_a_national_file_keeps_its_state(tmp_path, monkeypatch):
    """CC-0122 Wynnum QLD. Re-resolving every 'suburb' state blanked 64 correct live states
    like this one, because their file proves two states and the row proves nothing itself.
    The check must look at the text, not re-resolve."""
    doc = "https://example.test/national-stocklist.pdf"
    rows = [_row(331, suburb="Wynnum", estate_name="Wynnum", state="QLD",
                 state_source="suburb", lot_address="CC-0122, Wynnum", stocklist_file=doc,
                 source_text="CC-0122 417 Jul-26 173 5 + 5 + 3 FLINDERS SINGLE $ 2,330 "
                             "$ 121,160 8.03% TBC $ 850,000 $ 659,400 $ 1,509,400 "
                             "Available Download"),
            _row(332, lot_address="STAGE 2 / MAMBOURIN / VIC", stocklist_file=doc),
            _row(333, lot_address="STAGE 9 / TOOWOOMBA / QLD", stocklist_file=doc)]
    db = _make_db(tmp_path, rows)
    assert _enrich(monkeypatch, db) == 0
    assert _states(db)[331] == ("QLD", "suburb")


def test_a_house_design_is_not_a_place(tmp_path, monkeypatch):
    """Tarneit VIC. 'Rhodes 15' is a design and its size; Rhodes NSW has live stock.
    (The design line is Goldstate's, id 9177; the 'Baker 14 Duke' line is in the Grove
    control below.)"""
    line = ("Sep-26 1012 Tangara Rd South 10.5 22 231 $264,000 Rhodes 15 Sovereign 3 Single "
            "132 $333,000 $597,000 Available")
    rows = [_row(587, builder_name="Hermitage Homes", suburb="Tarneit", state="VIC",
                 state_source="suburb", estate_name="Lyra", lot_address="1012 Tangara Rd, Lyra",
                 source_text=line),
            _row(5724, suburb="Rhodes", state="NSW", state_source="postcode", postcode="2138",
                 lot_address="Lot 2605 Unit 2605, Blaxland Road, RHODES, NSW, 2138")]
    db = _make_db(tmp_path, rows)
    assert _enrich(monkeypatch, db) == 0
    assert _states(db)[587] == ("VIC", "suburb")


def test_a_region_is_not_a_place(tmp_path, monkeypatch):
    """Hervey Bay QLD. 'FRASER COAST' is the council region; Fraser ACT has live stock."""
    line = ("BP-9135824 Available 05/08/2026 Bloom Estate, Nikenbah (Hervey Bay) 512 Primrose "
            "Street Nikenbah 4655 FRASER COAST QLD Estimated November 2026 713 15 $460,000 "
            "$637,700 $1,097,700")
    rows = [_row(18577, suburb="Hervey Bay", state="QLD", state_source="suburb",
                 estate_name="Bloom", lot_address="512 Primrose Street, Nikenbah",
                 source_text=line),
            _row(15900, suburb="Fraser", state="ACT", state_source="postcode", postcode="2615",
                 lot_address="12 Example Street, Fraser")]
    db = _make_db(tmp_path, rows)
    assert _enrich(monkeypatch, db) == 0
    assert _states(db)[18577] == ("QLD", "suburb")


def test_an_estate_called_the_grove_is_not_a_place(tmp_path, monkeypatch):
    """Tarneit VIC, estate 'The Grove' (Hermitage, id 587's line: '... Baker 14 Duke ...').
    The Grove NSW and Grove TAS both have stock here."""
    rows = [_row(588, builder_name="Hermitage Homes", suburb="Tarneit", state="VIC",
                 state_source="suburb", estate_name="The Grove",
                 lot_address="5523 Launch Dr, The Grove",
                 source_text="5523 Launch Dr 294 Aug-26 $375,000 Baker 14 Duke 3 Single 128 "
                             "$314,900 $689,900 Available"),
            _row(4348, suburb="The Grove", state="NSW", state_source="postcode", postcode="2580",
                 lot_address="21 Example Road, The Grove"),
            _row(4349, suburb="Grove", state="TAS", state_source="postcode", postcode="7109",
                 lot_address="4 Example Road, Grove")]
    db = _make_db(tmp_path, rows)
    assert _enrich(monkeypatch, db) == 0
    assert _states(db)[588] == ("VIC", "suburb")


def test_a_suburb_the_column_states_and_the_page_confirms_is_not_second_guessed(tmp_path,
                                                                                monkeypatch):
    """id 19510, the CORRECT copy of lot 618: column 'Wollert', E-Agent's VIC page, and a
    line that ends '| Lot 618 | Riverhills'. 'suburb + e-agent page' only rests on a name
    when the suburb was guessed; here the column said it, and Riverhills QLD having stock
    (14905 itself, before the fix) must not pull the right row into Queensland."""
    line = ("Available Wollert Titled Townhouse 618 112 4.5 Ruthven Oakland D | Lot 618 | "
            "Riverhills 14.07 Double Storey 2 2 1 $143,389 $409,895 $553,284")
    rows = [_row(19510, builder_name="Hattan Homes", suburb="Wollert", state="VIC",
                 state_source="suburb + e-agent page", source_state_hint="VIC",
                 estate_name="Riverhills", lot_address="Lot 618, Riverhills", source_text=line),
            _row(19700, builder_name="Other Builder", suburb="Riverhills", state="QLD",
                 state_source="postcode", postcode="4074", lot_address="3 Example St, Riverhills")]
    db = _make_db(tmp_path, rows)
    assert _enrich(monkeypatch, db) == 0
    assert _states(db)[19510] == ("VIC", "suburb + e-agent page")


@pytest.mark.parametrize("stored", [("VIC", "suburb"), (None, None)])
def test_a_line_quoting_its_own_estate_is_not_moved_to_the_estates_namesake(
        tmp_path, monkeypatch, stored):
    """The CORRECT copy of lot 618 as the Hattan e-mail carries it: suburb column Wollert,
    estate Riverhills, address 'Lot 618, Riverhills', no page to vote with. own_state reads
    nothing from it, so its VIC rests on the suburb column alone -- settled, or resolved
    tonight. Riverhills QLD has real stock. The estate name is a name, not an address: the
    row must stay in Victoria, or the fix for Colin's search files a VIC lot under QLD."""
    line = ("Available Wollert Titled Townhouse 618 112 4.5 Ruthven Oakland D | Lot 618 | "
            "Riverhills 14.07 Double Storey 2 2 1 $143,389 $409,895 $553,284")
    rows = [_row(19530, builder_name="Hattan Homes", source_channel="digital email",
                 suburb="Wollert", estate_name="Riverhills", lot_address="Lot 618, Riverhills",
                 source_text=line, source_url="email:[Hattan weekly update]",
                 state=stored[0], state_source=stored[1]),
            _row(19700, builder_name="Other Builder", suburb="Riverhills", state="QLD",
                 state_source="postcode", postcode="4074", lot_address="3 Example St, Riverhills")]
    db = _make_db(tmp_path, rows)
    assert _enrich(monkeypatch, db) == 0
    assert _states(db)[19530] == ("VIC", "suburb")


def test_a_row_resting_on_its_suburb_column_cannot_vouch_for_that_place(tmp_path, monkeypatch):
    """One Hattan list, two rows. 8349 has 'Ceduna' (a house design, and an SA town) in its
    suburb column and SA from that alone; its sibling has 'Clyde North' there and ends its
    address '..., Ceduna'. 8349 is the claim under suspicion, so it must not be the stock
    that makes Ceduna SA a real place -- or the sibling is moved to SA on the very night
    8349 is moved out of it."""
    def hattan_line(lot):
        return (f"Available Clyde North Q3 2026 Townhouse Lot {lot} 186 6 Embla F | Ceduna 9.66 "
                f"Single Storey 3 1 1 $317,129 $287,800 $604,929")
    rows = [_row(8349, builder_name="Hattan Homes", source_channel="digital email",
                 suburb="Ceduna", estate_name="Ceduna", state="SA", state_source="suburb",
                 lot_address="Lot 3752, Ceduna", source_text=hattan_line(3752)),
            _row(12390, builder_name="Hattan Homes", source_channel="digital email",
                 suburb="Clyde North", state="VIC", state_source="suburb",
                 lot_address="Lot 3755, Ceduna", source_text=hattan_line(3755)),
            _row(12337, builder_name="Hattan Homes", source_channel="digital email",
                 suburb="Clyde North", estate_name="Ceduna", state="VIC",
                 state_source="listing text", lot_address="Lot 3754, Ceduna",
                 source_text=hattan_line(3754))]
    db = _make_db(tmp_path, rows)
    assert _enrich(monkeypatch, db) == 0
    states = _states(db)
    assert states[8349] == ("VIC", TEXT_LOCALITY_SOURCE)
    assert states[12390] == ("VIC", "suburb")


def test_every_move_is_logged_with_the_line_that_justified_it(tmp_path, monkeypatch, capsys):
    """A reviewer of the nightly log checks each move against the line itself, so the log
    carries the stretch of text around the name -- and never a link."""
    row = _riverhills_14905(source_text=_WOLLERT_LINE + " https://drive.example.test/x?id=1")
    db = _make_db(tmp_path, [row, _wollert_vic_sibling()])
    assert _enrich(monkeypatch, db) == 0
    moved = [ln for ln in capsys.readouterr().out.splitlines() if "id 14905" in ln]
    assert len(moved) == 1
    assert "QLD -> VIC" in moved[0] and "Available Wollert Titled Townhouse 618" in moved[0]
    assert "example.test" not in moved[0]


@pytest.mark.parametrize("off", ["0", "off", "false"])
def test_the_kill_switch_stops_the_check(tmp_path, monkeypatch, off):
    monkeypatch.setenv("SPB_TEXT_LOCALITY_OVERRULE", off)
    db = _make_db(tmp_path, [_riverhills_14905(), _wollert_vic_sibling()])
    assert _enrich(monkeypatch, db) == 0
    assert _states(db)[14905] == ("QLD", "suburb")


def test_superseded_rows_are_left_alone(tmp_path, monkeypatch):
    db = _make_db(tmp_path, [_riverhills_14905(superseded_by="v2:someone-else"),
                             _wollert_vic_sibling()])
    assert _enrich(monkeypatch, db) == 0
    assert _states(db)[14905] == ("QLD", "suburb")


# ------------------------------------------------------------------ reversible, repeatable

def _mixed_catalogue():
    doc = "https://onedrive.example.test/prosperity.xlsx"
    return [
        _riverhills_14905(), _wollert_vic_sibling(),
        # settled 9568 in a file whose own rows prove two states, as the real one does
        _windermere_9568(stocklist_file=doc), _mambourin_vic_sibling(),
        _row(9600, lot_address="STAGE 2 / MAMBOURIN / VIC", stocklist_file=doc),
        _row(9601, lot_address="STAGE 9 / TOOWOOMBA / QLD", stocklist_file=doc),
        # not yet resolved, off the QLD page, 'Windermere' guessed: resolves to
        # 'suburb + e-agent page', which must be checked on the night it is first written
        _windermere_9568(rid=9700, state=None, state_source=None,
                         stocklist_file="https://onedrive.example.test/other.xlsx"),
        _row(18659, lot_address=_MURRAY_LINE, source_text=_MURRAY_LINE,
             estate_name="Australia, 5114", state="QLD", state_source="suburb"),
        _row(9971, suburb="Murray Bridge", state="SA", state_source="listing text",
             lot_address="Lot 506, RIVERVIEW ESTATE, Murray Bridge"),
        _row(331, suburb="Wynnum", estate_name="Wynnum", state="QLD", state_source="suburb",
             lot_address="CC-0122, Wynnum", stocklist_file=doc),
    ]


def test_running_enrich_twice_makes_no_further_writes(tmp_path, monkeypatch):
    db = _make_db(tmp_path, _mixed_catalogue())
    assert _enrich(monkeypatch, db) == 0
    first = _states(db)
    assert first[14905] == ("VIC", TEXT_LOCALITY_SOURCE)
    assert first[9568] == ("VIC", TEXT_LOCALITY_SOURCE)
    assert first[9700] == ("VIC", TEXT_LOCALITY_SOURCE)
    assert first[18659] == ("SA", TEXT_LOCALITY_SOURCE)
    before = _table(db)

    writes = []
    real_write = enrich_buildings._write

    def counting_write(conn, rid, **cols):
        writes.append((rid, cols))
        return real_write(conn, rid, **cols)

    monkeypatch.setattr(enrich_buildings, "_write", counting_write)
    assert _enrich(monkeypatch, db) == 0
    assert writes == [], f"the second run wrote {writes}"
    assert _table(db) == before


def test_the_rollback_command_re_resolves_every_moved_row(tmp_path, monkeypatch):
    db = _make_db(tmp_path, _mixed_catalogue())
    assert _enrich(monkeypatch, db) == 0
    moved = {rid for rid, (_s, src) in _states(db).items() if src == TEXT_LOCALITY_SOURCE}
    assert moved == {14905, 9568, 9700, 18659}

    assert _enrich(monkeypatch, db, "--rollback-text-locality") == 0
    after = _states(db)
    assert not any(src == TEXT_LOCALITY_SOURCE for _s, src in after.values())
    # Re-resolved by today's rules with the check off: back to the suburb match...
    assert after[14905] == ("QLD", "suburb")
    # ...which for the two rows with no suburb column is tonight's GUESS, and since 29 Sep
    # 2026 that reads the locality, not the word before it (state_resolver.
    # suburb_in_row_text): "Murray Bridge, South" is Murray Bridge, SA -- once 'Murray', a
    # QLD place -- and "Windermere Mambourin" is Mambourin, VIC, which disagrees with the
    # QLD page, so with the check off the page decides, labelled contested.
    assert after[18659] == ("SA", "suburb")
    assert after[9700] == ("QLD", "e-agent page (conflicting signals)")
    # ...except in a file whose own rows prove two states, which comes back blank.
    assert after[9568] == (None, None)

    # And it stays that way while the check is switched off where the nightly runs.
    monkeypatch.setenv("SPB_TEXT_LOCALITY_OVERRULE", "0")
    assert _enrich(monkeypatch, db) == 0
    assert _states(db)[14905] == ("QLD", "suburb")


# ------------------------------------------------------------------ never stops the run

@pytest.mark.parametrize("broken", ["text_locality_elsewhere", "overrule_evidence"])
def test_a_crash_in_the_check_falls_back_to_todays_states_and_exits_degraded(tmp_path,
                                                                            monkeypatch,
                                                                            broken):
    """enrich is a REQUIRED nightly step: an exception here would abort the run and keep
    yesterday's snapshot. The row keeps today's answer, everything else is still written,
    and run_daily.DEGRADED_EXIT marks the run degraded so CI goes red after publishing.

    Not 2: run_daily files 2 as a PARTIAL harvest, writes _harvest_partial, and CI then
    reports a dead channel and prescribes a portal login. The code enrich really returned
    is fed through run_daily._run to prove the runner reads it as DEGRADED, not PARTIAL."""
    import run_daily

    rows = [_riverhills_14905(), _wollert_vic_sibling(),
            # an ordinary unresolved row the rest of enrich must still write
            _row(700, suburb="Tarneit", postcode="3029", lot_address="7 Example St, Tarneit")]
    db = _make_db(tmp_path, rows)

    def boom(*_a, **_k):
        raise RuntimeError("simulated failure in the new check")

    monkeypatch.setattr(enrich_buildings, broken, boom)
    code = _enrich(monkeypatch, db)
    assert enrich_buildings.DEGRADED_EXIT == run_daily.DEGRADED_EXIT, \
        "the step and the runner must agree"
    assert code == run_daily.DEGRADED_EXIT, code
    states = _states(db)
    assert states[14905] == ("QLD", "suburb")
    assert states[700][0] == "VIC", "the rest of enrich stopped writing"

    step_dir = tmp_path / "runner"
    step_dir.mkdir()
    (step_dir / "enrich_step.py").write_text(f"import sys\nsys.exit({code})\n",
                                             encoding="utf-8")
    monkeypatch.setattr(run_daily, "APP", step_dir)
    run_daily._DEGRADED.clear()
    run_daily._PARTIAL.clear()
    try:
        assert run_daily._run("2/7  Enrich: state, suburb, builder",
                              ["enrich_step.py"], False) is True
        assert run_daily._PARTIAL == [], "a crash in the check is not a dead harvest channel"
        assert len(run_daily._DEGRADED) == 1, run_daily._DEGRADED
        assert run_daily._DEGRADED[0].startswith("2/7  Enrich")
    finally:
        run_daily._DEGRADED.clear()
        run_daily._PARTIAL.clear()
