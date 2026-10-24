"""APLACE's price list, its QLD and VIC sections, and the state each lot lands in (24 Oct 2026).

APLACE by Glenville's list hangs on E-Agent's VIC page and carries Queensland lots under
"FRASER COAST", "BRISBANE WEST", "BUNDABERG" ... and Victorian lots under "VIC - DISPLAYS",
"VIC - SOUTH EAST", "VIC - NORTH" and "VIC - WEST". The nightly replay of Colin's searches
flagged two Queensland homes on the live VIC shortlist -- "1524 Gilchrist Street, BRISBANE
WEST" and "10 New Road, BRISBANE NORTH", both new in the 23 Oct list -- and a read-only live
search the same day found the mirror image, which the replay cannot see: 32 Victorian lots
(Clyde North, Wollert, Tarneit ...) on the QLD shortlist as "Bundaberg, QLD", 29 of them at or
under $750k.

  * The Victorian lots. The extractor carries the first banner's state ("QLD - DISPLAYS") and
    the BUNDABERG heading's suburb down through every later section, and the harvest stores
    that state with no signal, which enrich treated as settled. Their own cells say VIC
    (estate 'VIC', "143 Hadar Way, VIC") and so does their page:
    enrich_buildings.unsourced_state_its_text_contradicts re-opens them.
  * The two Queensland lots prove nothing themselves, and their guess "Brisbane" against the
    VIC page was "conflicting signals", which the page wins. Their address answers:
    state_resolver.address_over_page.
  * Where the page is right and only a bare place word disagrees, the page still wins: Verv
    19094 "... SS West 4327 Jubilee Wyndhamvale" (Jubilee, QLD) and Goldstate's "5708 Piebald
    St, Mandalay" (the Mandalay estate in Beveridge; Mandalay, QLD) stay VIC.

tests/fixtures/aplace_two_lists.json is the repo extractor's own output (sources.
spreadsheet_extract.extract_from_pdf, origin/main e12236b) for both public lists, stored here
through database.record_building as the harvest stores it, then enriched by
enrich_buildings.main().
"""
import json
import os
import sqlite3

import pytest

import config
import enrich_buildings
import state_resolver as sr
from database import ResearchDatabase
from geo import SuburbGeoIndex

_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                        "aplace_two_lists.json")
_COLS = ("id", "builder_name", "source_channel", "lot_address", "suburb", "state",
         "state_source", "estate_name", "source_text", "source_url", "stocklist_file",
         "source_state_hint", "postcode", "lot_number", "superseded_by", "content_hash",
         "price")
_TARGETS = ("1524 Gilchrist Street, BRISBANE WEST", "10 New Road, BRISBANE NORTH")


@pytest.fixture(scope="module")
def geo():
    return SuburbGeoIndex()


def _fixture():
    with open(_FIXTURE, encoding="ascii") as f:
        return json.load(f)


def _table(db):
    conn = sqlite3.connect(str(db))
    try:
        return {r[0]: r[1:] for r in conn.execute(
            "SELECT id, state, state_source, lot_address, stocklist_file FROM buildings")}
    finally:
        conn.close()


def _harvest_both_lists(tmp_path, monkeypatch):
    """The 16 Oct list on one night, the 23 Oct list the next, each enriched after."""
    data = _fixture()
    db = tmp_path / "aplace.db"
    store = ResearchDatabase(db)
    monkeypatch.setattr(config, "DATABASE_PATH", db)
    for one in data["lists"]:
        for r in one["rows"]:
            store.record_building(dict(r, builder_name=data["builder_name"],
                                       source_channel=data["source_channel"],
                                       stocklist_file=one["url"], source_url_or_ref=one["url"],
                                       source_state_hint=one["page"]))
        assert enrich_buildings.main([]) == 0
    return db, data


def _section_state(data):
    """{lot_address: the state of the heading it is listed under} -- 'VIC - ...' or QLD."""
    out = {}
    for one in data["lists"]:
        for r in one["rows"]:
            st = "VIC" if r["estate_context"].startswith("VIC") else "QLD"
            assert out.setdefault(r["lot_address"], st) == st
    return out


def test_every_aplace_lot_lands_in_its_sections_state(tmp_path, monkeypatch):
    """No lot in the other state; every Victorian lot VIC on its own words; the two Queensland
    lots QLD on their address. On main 42 Victorian lots are QLD and the two are VIC."""
    db, data = _harvest_both_lists(tmp_path, monkeypatch)
    truth = _section_state(data)
    got = _table(db)
    wrong = sorted((label, st, src, truth[label]) for st, src, label, _f in got.values()
                   if st and st != truth[label])
    assert wrong == [], wrong
    vic = [(st, src) for st, src, label, _f in got.values() if truth[label] == "VIC"]
    assert len(vic) == 42 and set(vic) == {("VIC", "listing text")}, vic
    assert sorted((label, st, src) for st, src, label, _f in got.values()
                  if label in _TARGETS) == sorted(
        (label, "QLD", sr.ADDRESS_OVER_PAGE_SOURCE) for label in _TARGETS)
    # and a night with nothing new writes nothing
    assert enrich_buildings.main([]) == 0
    assert _table(db) == got


def _row(rid, **kw):
    row = {c: None for c in _COLS}
    row.update(id=rid, content_hash=f"v2:aplace{rid:05d}", source_channel="E-Agent",
               builder_name="", price=700000.0)
    row.update(kw)
    return row


def _enrich_rows(tmp_path, monkeypatch, rows):
    db = tmp_path / "rows.db"
    ResearchDatabase(db)
    conn = sqlite3.connect(str(db))
    for r in rows:
        conn.execute(f"INSERT INTO buildings ({', '.join(_COLS)}) VALUES "
                     f"({', '.join('?' for _ in _COLS)})", [r[c] for c in _COLS])
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DATABASE_PATH", db)
    assert enrich_buildings.main([]) == 0
    return {i: v[:2] for i, v in _table(db).items()}


_NEW_LIST = "https://www.e-agent.com.au/_files/ugd/069fe0_398d57c59b40484ea3b63bffdabb1f25.pdf"


def _the_new_lists_rows():
    """The 23 Oct list's four rows in their own file, as the live database holds them: the two
    Queensland lots VIC on the page alone, two Victorian lots QLD from the extractor."""
    common = dict(builder_name="APLACE by Glenville", source_state_hint="VIC",
                  source_url=_NEW_LIST, stocklist_file=_NEW_LIST)
    return [
        _row(31173, lot_address="1524 Gilchrist Street, BRISBANE WEST",
             source_text="Dawn 1524 Gilchrist Street Dawn Nov-26 392 Rectangular $485,000 "
                         "Murray A 3 200.63 4 | 2 | 2 $373,520 $858,520 EOI Received",
             estate_name="BRISBANE WEST", state="VIC",
             state_source="e-agent page (conflicting signals)", **common),
        _row(31174, lot_address="10 New Road, BRISBANE NORTH",
             source_text="Joyner 10 New Road Park Lane Dec-26 199 Rectangular $380,000 "
                         "TERRACE Type C 239.31 4 | 3.5 | 2 $510,000 $890,000 EOI Received",
             estate_name="BRISBANE NORTH", state="VIC",
             state_source="e-agent page (conflicting signals)", **common),
        _row(31175, lot_address="516 Silage Crescent, VIC", suburb="Bundaberg",
             source_text="Donnybrook 516 Silage Crescent Acclaim Jan-27 294 14 x 21 $339,000 "
                         "Duval 05 149.49 4 | 2 | 2 $414,950 $753,950 Aplace Exclusive",
             estate_name="VIC", state="QLD", **common),
        _row(31176, lot_address="1403 Tabula Road, VIC", suburb="Bundaberg",
             source_text="Tarneit 1403 Tabula Road Unity Park Dec-26 299 10.5 x 28.5 "
                         "$334,500 Mackay 04 167.08 4 | 2.5 | 2 $337,450 $671,950 Aplace "
                         "Exclusive", estate_name="VIC", state="QLD", **common),
    ]


def test_the_live_rows_of_the_new_list_go_to_their_own_states(tmp_path, monkeypatch):
    got = _enrich_rows(tmp_path, monkeypatch, _the_new_lists_rows())
    assert got == {31173: ("QLD", sr.ADDRESS_OVER_PAGE_SOURCE),
                   31174: ("QLD", sr.ADDRESS_OVER_PAGE_SOURCE),
                   31175: ("VIC", "listing text"), 31176: ("VIC", "listing text")}


_JUBILEE_LINE = ("2-Part Detached SS West 4327 Jubilee Wyndhamvale 263 $338,000 Enzo 10.5 "
                 "3 / 2 / 2 $347,050 $685,050 Brochure Masterplan Stage 43 - POS Available")
_GOLDSTATE_LIST = "https://docs.example.test/e-agent/goldstate-mandalay.pdf"


def _pages_that_are_right():
    """Verv 19094 as enrich leaves it (tests/test_lot_number_is_not_a_postcode.py), and
    Goldstate's two Mandalay lots as the 26 Sep copy holds them (9167, 9168): VIC pages,
    each with a bare one-state QLD word against it."""
    mandalay = dict(builder_name="Goldstate Homes", lot_address="5708 Piebald St, Mandalay",
                    estate_name="Mandalay", lot_number="5708", source_state_hint="VIC",
                    state="VIC", state_source="e-agent page (conflicting signals)",
                    source_url=_GOLDSTATE_LIST, stocklist_file=_GOLDSTATE_LIST)
    return [
        _row(19094, builder_name="Verv Projects", lot_address=_JUBILEE_LINE[:110],
             source_text=_JUBILEE_LINE, state="VIC",
             state_source="e-agent page (conflicting signals)", lot_number="4327",
             source_state_hint="VIC", source_url="https://docs.example.test/verv",
             stocklist_file="https://docs.example.test/verv"),
        _row(9167, source_text="Mar-27 5708 Piebald St North West 12.5 28 350 $334,000 Turin "
                               "23 Panorama 4 Single 210 $372,000 $706,000 Available",
             **mandalay),
        _row(9168, source_text="Mar-27 5708 Piebald St North West 12.5 28 350 $334,000 "
                               "Lincoln 23 Co-Living - 1B Prospect 5 Single 206 $434,000 "
                               "$768,000 Available", **mandalay),
    ]


def test_the_page_stands_where_only_a_place_word_disagrees(tmp_path, monkeypatch):
    got = _enrich_rows(tmp_path, monkeypatch, _pages_that_are_right())
    assert got == {i: ("VIC", "e-agent page (conflicting signals)")
                   for i in (19094, 9167, 9168)}


def _the_briefs_rule(row_text, estate, suburb, page, geo_):
    """The rule as first asked for: a suburb of one other state, and nothing in the row a place
    of the page's state -- no street before it, no estate guard."""
    mine = [s for s in geo_.states_for_suburb(str(suburb or "")) if s in sr.VALID_STATES]
    if len(mine) != 1 or mine[0] == sr.normalise_state(page):
        return ""
    for text in list(row_text) + [estate, suburb]:
        toks, breaks = sr._tokens(text)
        if any(sr.normalise_state(page) in sts
               for sts in sr._place_spans(toks, breaks, geo_).values()):
            return ""
    return mine[0]


def test_the_page_test_sees_a_rule_without_the_address(tmp_path, monkeypatch):
    """A silent check reads as a pass: with the street and estate guards taken out, the three
    rows above go to Queensland, so the test above is what holds them."""
    monkeypatch.setattr(sr, "address_over_page", _the_briefs_rule)
    monkeypatch.setattr(enrich_buildings, "address_over_page", _the_briefs_rule)
    got = _enrich_rows(tmp_path, monkeypatch, _pages_that_are_right())
    assert {i: st for i, (st, _src) in got.items()} == {19094: "QLD", 9167: "QLD",
                                                        9168: "QLD"}


@pytest.mark.parametrize("texts, estate, suburb, page, want", [
    # the two Queensland lots: Brisbane after the street, the estate a heading of it
    (("Dawn 1524 Gilchrist Street Dawn Nov-26 392 Rectangular $485,000 Murray A 3 200.63",
      "1524 Gilchrist Street, BRISBANE WEST"), "BRISBANE WEST", "Brisbane", "VIC", "QLD"),
    (("Joyner 10 New Road Park Lane Dec-26 199 Rectangular $380,000 TERRACE Type C",
      "10 New Road, BRISBANE NORTH"), "BRISBANE NORTH", "Brisbane", "VIC", "QLD"),
    # the page's own state, or no page: nothing to take it off
    (("1524 Gilchrist Street Dawn", "1524 Gilchrist Street, BRISBANE WEST"), "BRISBANE WEST",
     "Brisbane", "QLD", ""),
    (("1524 Gilchrist Street Dawn", "1524 Gilchrist Street, BRISBANE WEST"), "BRISBANE WEST",
     "Brisbane", "", ""),
    # the place is the estate's name, or inside one that is more than a compass heading
    (("Mar-27 5708 Piebald St North West 12.5 28 350 $334,000",
      "5708 Piebald St, Mandalay"), "Mandalay", "Mandalay", "VIC", ""),
    (("1524 Gilchrist Street Dawn", "1524 Gilchrist Street, Brisbane Rise"), "Brisbane Rise",
     "Brisbane", "VIC", ""),
    # another state's place anywhere in the row, or another state written
    (("Mar-27 5708 Piebald St 350 $334,000 Turin 23 Panorama 4 Single",
      "5708 Piebald St, Mandalay"), "", "Mandalay", "VIC", ""),
    (("House & Land VIC Lot 12 Gilchrist Street", "12 Gilchrist Street, Brisbane"), "",
     "Brisbane", "NSW", ""),
    # no street before the place: a bare word in a line
    ((_JUBILEE_LINE, _JUBILEE_LINE[:110]), "", "Jubilee", "VIC", ""),
    # the suburb voted on is not that place's state alone
    (("1524 Gilchrist Street Dawn", "1524 Gilchrist Street, BRISBANE WEST"), "BRISBANE WEST",
     "", "VIC", ""),
])
def test_the_address_over_the_page(geo, texts, estate, suburb, page, want):
    assert sr.address_over_page(texts, estate, suburb, page, geo) == want


def test_resolve_state_reads_the_address_only_when_asked(geo):
    texts = ("Dawn 1524 Gilchrist Street Dawn Nov-26 392", "1524 Gilchrist Street, BRISBANE WEST")
    ask = dict(suburb="Brisbane", page_state="VIC", geo=geo, row_text=texts,
               estate="BRISBANE WEST")
    assert sr.resolve_state(**ask) == ("VIC", "e-agent page (conflicting signals)")
    assert sr.resolve_state(read_the_address=True, **ask) == ("QLD",
                                                              sr.ADDRESS_OVER_PAGE_SOURCE)


@pytest.mark.parametrize("state, source, page, mine, why, want", [
    ("QLD", None, "VIC", "VIC", "listing text", True),
    ("QLD", "", None, "VIC", "listing text", True),
    # a signal the row was given is not the harvest's say-so
    ("QLD", "suburb", "VIC", "VIC", "listing text", False),
    # the page contradicts the row's words: a different question
    ("QLD", None, "NSW", "VIC", "listing text", False),
    # the same state, or a weaker proof than the row naming it
    ("VIC", None, "VIC", "VIC", "listing text", False),
    ("QLD", None, "VIC", "VIC", "listing locality", False),
    ("QLD", None, "VIC", "", "", False),
])
def test_a_harvest_state_its_own_words_contradict(state, source, page, mine, why, want):
    r = _row(1, state=state, state_source=source, source_state_hint=page)
    assert enrich_buildings.unsourced_state_its_text_contradicts(r, mine, why) is want
