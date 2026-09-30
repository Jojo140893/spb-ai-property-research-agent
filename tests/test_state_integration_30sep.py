"""The three gaps round 3's review of the state fix left open (integration, 30 Sep 2026).

1. A house number's street ending in a word geo._STREET_TYPE leaves out on purpose --
   "7 Park Rise, Sommariva", "12 Banksia Retreat, Jetsonville" -- did not count as a
   street, so round 1's longer-name rule won over the locality the address names.
2. In the "<estate> <lot> <locality> Titled" layout ("Lake Argyle 1624 Mirranatwa Titled
   344") the longer-name rule gave the line-opening name's state over the locality.
3. A row held on its page (enrich_buildings.PAGE_HELD_SIGNAL) is no longer moved by a
   place name in its text -- the trade that keeps Verv 18833 in VIC -- and a genuine
   misfile of that shape on a WRONG page was reported by nothing, not even the replay,
   which reads the row's line before its label.

1 and 2 are closed on the side of caution only: the new readings feed the longer-name
rule's refusals, so they can return a row to origin/main's answer and never give it a new
state. 3 is closed by reporting, not by moving: the replay warns ('page-held:') and enrich
lists every live held row each night.
"""
import sqlite3

import pytest

import config
import enrich_buildings
import search_regressions as SR
import state_resolver as sr
from database import ResearchDatabase
from geo import SuburbGeoIndex

from tests.test_search_regressions import _card, _row, _run_with_locality


@pytest.fixture(scope="module")
def geo():
    return SuburbGeoIndex()


# ------------------------------------------------------------ 1. streets that end in Rise

@pytest.mark.parametrize("text, state", [
    ("Available Robinson River Dc Lot 37, 7 Park Rise, Sommariva Elara - Haven $570,741", "QLD"),
    ("Available Clyde North Dc Lot 7, 12 Banksia Rise, Jetsonville Acacia - Haven $600,000", "TAS"),
    ("Available Clyde North Dc Lot 7, 12 Banksia Retreat, Jetsonville Acacia - Haven", "TAS"),
    ("Available Wyndham Vale Lot 9, 3 Taylors Rise, Sommariva Elara $612,000", "QLD"),
])
def test_a_house_numbers_street_ending_in_rise_is_a_street(geo, text, state):
    assert sr.places_after_a_street(text, geo) == {state}


def test_without_a_house_number_a_rise_or_green_is_not_taken_for_a_street(geo):
    """'Mambourin Green' and 'Wollert Rise' are estates and suburbs as often as streets:
    only a house number in front makes the word a street's."""
    assert sr._house_street_type(["7", "Park", "Rise"], 2)
    assert not sr._house_street_type(["Available", "Wollert", "Rise"], 2)
    assert not sr._house_street_type(["Mambourin", "Green", "Estate"], 1)


# ------------------------------------------------------------ 2. <estate> <lot> <locality>

@pytest.mark.parametrize("text, states", [
    ("Lake Argyle 1624 Mirranatwa Titled 344", {"VIC"}),
    ("Grange West 56 Werribee Titled 400", {"VIC"}),
    ("Alice Springs 3650 Creek Junction Titled 460", {"VIC"}),
    ("Carlton River 3900 Beedelup Titled 690", {"WA"}),
    # a street after the lot is not the locality ("Lot 7, Redhead Rise"), and a line with
    # no number after its opening name has no locality position at all
    ("Available Regents Park Dc Lot 7, Redhead Rise, Jetsonville Acacia", set()),
    ("Available Clyde North House 263 Lot 12", set()),
])
def test_the_place_after_the_lot_number_is_read(geo, text, states):
    assert sr.places_after_a_lot_number(text, geo) == states


# ------------------------------------------------------------ 1 + 2 through the real enrich

_COLS = ("id", "builder_name", "source_channel", "lot_address", "suburb", "state",
         "state_source", "estate_name", "source_text", "source_url", "stocklist_file",
         "source_state_hint", "postcode", "lot_number", "superseded_by", "content_hash",
         "price", "last_seen")

# The reviews' rows (round 3, verify-2 cases/rise.json and the harness's 'A lot B Titled'
# family): no page, blank suburb, each in a file of its own. origin/main gives every one of
# them no state; round 3 gave NT, TAS/VIC and WA on the longer name.
_REVIEWED = [
    "Available Robinson River Dc Lot 37, 7 Park Rise, Sommariva Elara - Haven $570,741 Active",
    "Available Clyde North Dc Lot 7, 12 Banksia Rise, Jetsonville Acacia - Haven $600,000",
    "Available Clyde North Dc Lot 7, 12 Banksia Retreat, Jetsonville Acacia - Haven $600,000",
    "Available Wyndham Vale Lot 9, 3 Taylors Rise, Sommariva Elara $612,000",
    "Lake Argyle 1624 Mirranatwa Titled 344",
    "Carlton River 3900 Beedelup Titled 690",
]


def _db(tmp_path, lines):
    db = tmp_path / "enrich.db"
    ResearchDatabase(db)
    conn = sqlite3.connect(str(db))
    for i, line in enumerate(lines, start=1):
        url = f"https://example.invalid/own-file-{i}.xlsx"
        vals = {"id": i, "builder_name": "Reviewed", "source_channel": "E-Agent",
                "lot_address": line, "suburb": None, "state": None, "state_source": None,
                "estate_name": None, "source_text": line, "source_url": url,
                "stocklist_file": url, "source_state_hint": None, "postcode": None,
                "lot_number": None, "superseded_by": None, "content_hash": f"v2:rev{i:04d}",
                "price": 600000.0, "last_seen": "30/09/2026"}
        conn.execute(f"INSERT INTO buildings ({', '.join(_COLS)}) "
                     f"VALUES ({', '.join('?' for _ in _COLS)})", [vals[c] for c in _COLS])
    conn.commit()
    conn.close()
    return db


def _states(db):
    conn = sqlite3.connect(str(db))
    try:
        return dict(conn.execute("SELECT id, state FROM buildings").fetchall())
    finally:
        conn.close()


def test_the_longer_name_yields_to_the_address_the_row_writes(tmp_path, monkeypatch):
    db = _db(tmp_path, _REVIEWED)
    monkeypatch.setattr(config, "DATABASE_PATH", db)
    assert enrich_buildings.main([]) == 0
    got = _states(db)
    assert all(got[i] is None for i in got), got     # origin/main's answer: no state


def test_the_longer_name_still_stands_where_nothing_disagrees(tmp_path, monkeypatch):
    """Control: the Hattan shape round 1 fixed still gets its state, so the test above is
    not passing because the longer-name rule was switched off."""
    db = _db(tmp_path, ["Grange West 56 Werribee Titled 400"])
    monkeypatch.setattr(config, "DATABASE_PATH", db)
    assert enrich_buildings.main([]) == 0
    assert _states(db)[1] == "VIC"


# ------------------------------------------------------------ 3. held on a wrong page

def test_a_row_held_on_its_page_is_warned_about_when_its_label_names_another_state():
    """'Lot 27102, Windermere Mambourin' filed QLD and held on its page: the replay reads
    its line first, which does not name Mambourin, so without the label look it said
    nothing -- and a QLD page on a Mambourin (VIC) lot is the misfile origin/main moved."""
    held = _row("v2:held2710", state="QLD", state_source=enrich_buildings.PAGE_HELD_SIGNAL,
                suburb=None, lot_address="Lot 27102, Windermere Mambourin", price=600000.0)
    mambourin = _row("v2:mamb0001", state="VIC", state_source="listing text",
                     suburb="Mambourin", lot_address="Lot 1, 2 Some Road, Mambourin",
                     price=600000.0)
    db = [{"id": 27102, "content_hash": "v2:held2710", "date_checked": "15/09/2026",
           "state": "QLD", "state_source": enrich_buildings.PAGE_HELD_SIGNAL, "suburb": None,
           "source_state_hint": "QLD", "lot_address": "Lot 27102, Windermere Mambourin",
           "source_text": "Lot 27102 Bounty X 400m2 $412,000 Available"}]
    report, out = _run_with_locality([held, mambourin], db, [_card(held)], "QLD")
    assert report.failures == 0, out
    assert "page-held: shortlisted v2:held2710 (id 27102) is held QLD on its page" in out, out
    assert "names Mambourin (VIC)" in out, out


def test_a_row_held_on_its_page_whose_label_names_nothing_elsewhere_is_quiet():
    held = _row("v2:held0001", state="QLD", state_source=enrich_buildings.PAGE_HELD_SIGNAL,
                suburb=None, lot_address="Lot 7, Ripley", price=600000.0)
    db = [{"id": 7, "content_hash": "v2:held0001", "date_checked": "15/09/2026",
           "state": "QLD", "state_source": enrich_buildings.PAGE_HELD_SIGNAL, "suburb": None,
           "source_state_hint": "QLD", "lot_address": "Lot 7, Ripley",
           "source_text": "Lot 7 Ripley 400m2 $412,000 Available"}]
    report, out = _run_with_locality([held], db, [_card(held)], "QLD")
    assert "page-held:" not in out, out


def test_enrich_lists_every_live_row_held_on_its_page(tmp_path, monkeypatch, capsys):
    db = _db(tmp_path, ["Lot 27102, Windermere Mambourin"])
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE buildings SET state='QLD', state_source=?, source_state_hint='QLD'",
                 (enrich_buildings.PAGE_HELD_SIGNAL,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DATABASE_PATH", db)
    assert enrich_buildings.main([]) == 0
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines()
                if "held on its page although its label also names a place" in ln)
    count = int(line.rsplit(":", 1)[1])
    after = sqlite3.connect(str(db)).execute("SELECT state_source FROM buildings").fetchone()[0]
    # Listed when enrich keeps it held; not listed when enrich has re-decided it.
    assert count == (1 if after == enrich_buildings.PAGE_HELD_SIGNAL else 0), (out, after)
    if count:
        assert "Windermere Mambourin" in out
