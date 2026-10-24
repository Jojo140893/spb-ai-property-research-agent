"""A file with no page, read by its own Location column (24 Oct 2026).

Verv's master stocklist was emailed to digital@ as "[VG] - Projects | House & Land | Dual Key
Packages": three PDFs, no page, no postcode, no state written anywhere. Its columns are
Product, Contract, Region, Lot #, Estate, Location, Title Date ..., and every Location is in
Victoria -- Lara, Armstrong Creek, Wollert, Tarneit, Mernda, Donnybrook, Officer, Warragul,
Clyde North, Fraser Rise, Diggers Rest, and "Wyndhamvale" (Wyndham Vale as one word). Yet
92 of its 114 lots had no state, so no state search reached them, and 4 were QLD:

  * "2-Part Detached SS West 4327 Jubilee Wyndhamvale Titled ...": 4327 is the LOT, read as
    a Queensland postcode (email rows carry no lot number to compare it with), and Jubilee,
    its estate, is a Queensland place in the gazetteer -- QLD by "suburb + postcode". That
    one vote made the email national, and the rest proved nothing on their own.
    state_resolver.postcode_after_an_aspect: a number straight after an aspect word that
    ends no place of its state is the lot.
  * With that gone, the email is a Victorian file with no page, and its one-word locations
    still prove nothing on their own. state_resolver.location_column_reading reads the
    Location column -- the place straight before a title date, the lot a few words before
    it -- and enrich_buildings.document_states reads a page-less file with the one state
    its Location column agrees on, as a page would be.

tests/fixtures/verv_vg_email.json is the email source's own output for the three PDFs
(sources.email_inbox.EmailStocklistSource._from_message), stored here through
database.record_building as the harvest stores it, then enriched by enrich_buildings.main().
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
                        "verv_vg_email.json")
_JUBILEE = ("4327", "4328", "4316", "4317")


@pytest.fixture(scope="module")
def geo():
    return SuburbGeoIndex()


def _table(db):
    conn = sqlite3.connect(str(db))
    try:
        return {r[0]: r[1:] for r in conn.execute(
            "SELECT id, state, state_source, postcode, source_text, source_state_hint "
            "FROM buildings")}
    finally:
        conn.close()


def _harvest(tmp_path, monkeypatch, page=None):
    """The [VG] email as the harvest stores it (email_inbox.search adds the reference and
    the date), on a page when `page` is given, then enriched."""
    with open(_FIXTURE, encoding="ascii") as f:
        data = json.load(f)
    db = tmp_path / "vg.db"
    store = ResearchDatabase(db)
    monkeypatch.setattr(config, "DATABASE_PATH", db)
    for r in data["rows"]:
        store.record_building(dict(r, source_channel=data["source_channel"],
                                   source_url_or_ref="email:%s" % data["subject"][:70],
                                   date_checked="23/10/2026", verified=False,
                                   **({"source_state_hint": page} if page else {})))
    assert enrich_buildings.main([]) == 0
    return db


def test_every_lot_of_verv_s_vg_email_is_victorian(tmp_path, monkeypatch):
    """All 114 VIC, the Jubilee lots' lot numbers out of the postcode column, and a night
    with nothing new writes nothing. On main 92 are blank and the four Jubilee lots QLD."""
    db = _harvest(tmp_path, monkeypatch)
    got = _table(db)
    assert len(got) == 114
    assert {st for st, *_rest in got.values()} == {"VIC"}, sorted(
        (st, src, text[:60]) for st, src, _pc, text, _h in got.values() if st != "VIC")
    jubilee = {text.split(" West ")[1][:4]: (st, src, pc) for st, src, pc, text, _h
               in got.values() if " Jubilee Wyndhamvale " in text}
    assert jubilee == {lot: ("VIC", sr.DOCUMENT_LOCATION_SOURCE + " (conflicting signals)",
                             None) for lot in _JUBILEE}
    assert enrich_buildings.main([]) == 0
    assert _table(db) == got


def test_a_file_on_a_page_is_never_read_by_its_location_column(tmp_path, monkeypatch, geo):
    """The page decides where there is one: the same rows hung on a QLD page are not read
    by their Location column -- their own words (Fraser Rise, Diggers Rest) prove VIC and the
    file is national to the page, as before."""
    db = _harvest(tmp_path, monkeypatch, page="QLD")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM buildings")]
    conn.close()
    assert enrich_buildings.document_states(rows, geo) == {}
    assert not any(src and src.startswith(sr.DOCUMENT_LOCATION_SOURCE)
                   for _st, src, *_rest in _table(db).values())


@pytest.mark.parametrize("text, want", [
    ("2-Part Dual Key SS Geelong 1730 Austin Lara Oct-26 400 $390,800 Vanta 20", "Lara"),
    ("2-Part Townhome North 25 Havenwood Mernda Q1 2027 143 $307,000 ZimiFL", "Mernda"),
    ("2-Part Dual Key SS North 1037 Wollert Rise Wollert Titled 455 $449,900", "Wollert"),
    ("2-Part Detached SS West 542 Society 1056 Fraser Rise Sep-26 298 $381,000",
     "Fraser Rise"),
    ("2-Part Dual Key SS Geelong 116 Palomino Armstrong Creek Titled 480 $479,000",
     "Armstrong Creek"),
    ("Austin Lara Oct-26 428 $395,800 Nex 20 Nest 2 = 1 + 1", None),     # no lot before it
    # Wyndham Vale written as one word is no place: the estate is never read instead
    ("2-Part Detached SS West 4327 Jubilee Wyndhamvale Titled 263 $338,000", None),
    # a title after the locality, but no lot before it: not this column
    ("Available Wyndham Vale Titled House Lot 4317 350 12.5 Vista 16", None),
    ("Lot 5, Tarneit", None),
])
def test_the_location_column(geo, text, want):
    got = sr.location_column_reading(text, geo)
    assert (got[0] if got else None) == want


def _readings(*names):
    return [(n, s) for n, s in names]


@pytest.mark.parametrize("readings, rows, want", [
    (_readings(("Lara", ["VIC"]), ("Mernda", ["VIC"]), ("Wollert", ["VIC"]),
               ("Armstrong Creek", ["QLD", "VIC"]), ("Donnybrook", ["QLD", "VIC", "WA"])), 6,
     "VIC"),
    # another state's place among them: no one state
    (_readings(("Lara", ["VIC"]), ("Mernda", ["VIC"]), ("Wollert", ["VIC"]),
               ("Joyner", ["QLD"]), ("Tarneit", ["VIC"])), 5, ""),
    # too few readings, or too few of the file's rows
    (_readings(("Lara", ["VIC"]), ("Mernda", ["VIC"]), ("Wollert", ["VIC"]),
               ("Tarneit", ["VIC"])), 4, ""),
    (_readings(("Lara", ["VIC"]), ("Mernda", ["VIC"]), ("Wollert", ["VIC"]),
               ("Tarneit", ["VIC"]), ("Officer", ["VIC"])), 11, ""),
    # only shared names: they agree on two states, so on none
    (_readings(*[("Armstrong Creek", ["QLD", "VIC"])] * 6), 6, ""),
])
def test_the_one_state_a_location_column_agrees_on(readings, rows, want):
    assert sr.document_location_state(readings + [None], rows) == want


@pytest.mark.parametrize("pc, text, want", [
    ("4327", "2-Part Detached SS West 4327 Jubilee Wyndhamvale Titled 263", True),
    ("2632", "2-Part Detached SS North 2632 Olivine Donnybrook Titled 378", True),
    # an aspect word that ends a place of the postcode's state: the postcode
    ("3631", "Lot 12 Smith Street Shepparton North 3631", False),
    ("3978", "Lot 3 Hadar Way Clyde North 3978", False),
    ("4514", "10 New Road, BRISBANE NORTH 4514", False),
    ("4327", "Lot 4327 Jubilee", False),
])
def test_a_number_after_the_lot_s_aspect_is_the_lot(geo, pc, text, want):
    assert sr.postcode_after_an_aspect(pc, [text], geo) is want


def test_resolve_state_names_the_document_as_the_hint(geo):
    ask = dict(suburb="Lara", page_state="VIC", geo=geo)
    assert sr.resolve_state(**ask) == ("VIC", "suburb + e-agent page")
    assert sr.resolve_state(hint_source=sr.DOCUMENT_LOCATION_SOURCE, **ask) == (
        "VIC", "suburb + " + sr.DOCUMENT_LOCATION_SOURCE)
    assert sr.resolve_state(suburb="Jubilee", page_state="VIC", geo=geo,
                            hint_source=sr.DOCUMENT_LOCATION_SOURCE) == (
        "VIC", sr.DOCUMENT_LOCATION_SOURCE + " (conflicting signals)")
