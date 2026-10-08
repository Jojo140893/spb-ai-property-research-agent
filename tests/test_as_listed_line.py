"""A card titled only by its place shows its line as listed (8 Oct 2026).

Since the 600-candidate cap was lifted, about 1,000 to 1,800 shortlisted cards of a
state-wide search -- mostly apartments: Atlas, Aria, The Brightly, Gallery Group -- are titled
'Melbourne VIC' or 'Richmond VIC' and nothing else, because the unit number is only in the
builder's line ('45 4504 2 BED + 2 BATH C05.1 North East ...'). Coleen's team could not tell
those cards apart or match one to E-Agent's unit. The row labels meant to fix it (Stage B) are
parked: labels that took part in matching merged wrong lots. So this is DISPLAY ONLY:

  * api/research.py adds as_listed -- the card's snapshot row's own line, without agent small
    print, white space made single -- to the full cards of a PAGED answer (the shortlist page
    and the needs-confirming page), and only when the card's title names no lot and no street
    and the line says a number the title does not;
  * 'names no lot and no street' has one definition, address_label.names_no_lot_and_no_street,
    which search_regressions' nightly count and parity_check read too;
  * an unpaged answer (Clare's hook) is exactly as it was, and nothing else of any answer
    moves: no id, rank, count, page, building, Kommo note or client document.
"""
import json
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
for _p in (str(APP / "api"), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _candidates as C  # noqa: E402
import address_label as AL  # noqa: E402
import search_regressions as SR  # noqa: E402
from tests import _synthetic_stock as S  # noqa: E402

ATLAS = "45 4504 2 BED + 2 BATH C05.1 North East 86.8 0 86.8 $1,567,000 $18,053 available"
# Why a card shows no line (address_label.AS_LISTED_WHY_*), as the analysis reports them.
TITLE = "the title names a lot or a street"
NO_LINE = "the row stores no line"
SMALL_PRINT = "the line is all agent small print"
NOTHING_NEW = "the line says no number the title does not (money aside)"


# ------------------------------------------------------------------ one definition

@pytest.mark.parametrize("title, lot, named", [
    ("GOLF $3,497,500", "", False),
    ("PARK RIDGE", "", False),
    ("$620,000.00 $461,312.00 Click here Click here", "", False),
    ("Melbourne VIC", "", False),
    ("Melbourne VIC", None, False),
    ("1386 Peter Thomson Circuit", "", True),
    ("Lot 3755, Ceduna", "", True),
    ("Unit 2302, Atlas", "", True),
    ("Altona Drive", "17", True),
    ("Melbourne VIC", "0", True),
])
def test_one_definition_of_a_card_that_names_no_lot_and_no_street(title, lot, named):
    card = {"lot_address": title, "lot_number": lot}
    assert AL.names_no_lot_and_no_street(card) is (not named)
    assert SR.no_lot_and_no_street(card) is (not named)


def test_the_nightly_count_reads_the_definition_the_cards_are_given_by(monkeypatch):
    """search_regressions (and parity_check through it) counts with address_label's own
    function, so the cards that gain the line and the cards the nightly counts cannot drift
    apart. Before 8 Oct 2026 it had a copy of its own."""
    seen = []
    monkeypatch.setattr(AL, "names_no_lot_and_no_street", lambda card: seen.append(card) or "it")
    assert SR.no_lot_and_no_street({"lot_address": "x"}) == "it"
    assert seen == [{"lot_address": "x"}]


# ------------------------------------------------------------------ the line, and when not

@pytest.mark.parametrize("title, lot, line, shown, why", [
    # Atlas: the unit (4504, level 45) is in the line only.
    ("Melbourne VIC", "", ATLAS, ATLAS, ""),
    # White space as one space, the line otherwise as read.
    ("Melbourne VIC", "", "  45   4504\t2 BED ", "45 4504 2 BED", ""),
    # No agent small print on any page: the commission column run into the line goes.
    ("Single Car Space & OB Storage, South Yarra VIC", "",
     "3 306 2BED 2BATH+1CAR 94 9 103 $1,425,000 available Agent commission=6%plus",
     "3 306 2BED 2BATH+1CAR 94 9 103 $1,425,000 available", ""),
    ("South Yarra VIC", "", "Agent commission=6%plus", "", SMALL_PRINT),
    # Gallery Group: the design and size tell it apart; amounts and link words alone do not
    # (the card shows its price), nor the title said again.
    ("Click here Click here", "",
     "Traditional KRAUSS 204 $604,000.00 $507,227.00 $0.00 $1,111,227.00 Click here Click here",
     "Traditional KRAUSS 204 $604,000.00 $507,227.00 $0.00 $1,111,227.00 Click here Click here",
     ""),
    ("Click here Click here", "",
     "$620,000.00 $461,312.00 $0.00 $1,081,312.00 Click here Click here", "",
     NOTHING_NEW),
    ("B-701 3 2 - 126 26 152", "", "B-701 3 2 - 126 26 152 $2,100,000", "",
     NOTHING_NEW),
    ("HEMMANT", "", "HEMMANT", "", NOTHING_NEW),
    ("Melbourne VIC", "", "Melbourne VIC", "", NOTHING_NEW),
    ("Melbourne VIC", "", "", "", NO_LINE),
    ("Melbourne VIC", "", None, "", NO_LINE),
    # A card whose title names its lot or street, or that states its lot, needs no line.
    ("Lot 12, Tarneit VIC", "", "12 Example Rise 400 m2", "", TITLE),
    ("18 Wiltipoll Road, Mambourin", "", "18 Wiltipoll Road 5 bed", "", TITLE),
    ("Melbourne VIC", "4504", ATLAS, "", TITLE),
])
def test_the_line_as_listed_and_why_not(title, lot, line, shown, why):
    card = {"lot_address": title, "lot_number": lot, "suburb": "Melbourne", "state": "VIC"}
    assert AL.as_listed_line(line, card) == (shown, why)
    assert AL.as_listed(line, card) == shown


# ------------------------------------------------------------------ through the endpoint

def _apartment(i, **kw):
    """An Atlas unit as the snapshot stores it: no lot number, no street, no estate, the
    unit only in its line -- so its card is titled by its place, 'Melbourne VIC'."""
    price = 1500000.0 + 1000.0 * i
    line = "%d %d%02d 2 BED + 2 BATH C05.1 North East 86.8 0 86.8 $%s $18,053 available" % (
        40 + i, 40 + i, i, format(int(price), ","))
    out = dict(content_hash="v2:atlas-%03d" % i, builder_name="Atlas", lot_address=line,
               lot_number="", suburb="Melbourne", state="VIC", estate_name=None,
               product_type="Apartment", bedrooms=2, bathrooms=2, car_spaces=1,
               land_sqm=None, house_sqm=86.8, source_doc="doc-atlas", price=price,
               line_key="k-atlas-%03d" % i)
    out.update(kw)
    return S.row(out.pop("content_hash"), **out)


def _stale_apartment(i):
    """One of another list, read 20/08/2026: it needs confirming."""
    return _apartment(50 + i, content_hash="v2:brightly-%03d" % i, builder_name="The Brightly",
                      source_doc="doc-brightly", date_checked="20/08/2026",
                      line_key="k-brightly-%03d" % i)


@pytest.fixture(scope="module")
def research(tmp_path_factory):
    rows = (S.stock(fresh=40, estates=4)
            + [_apartment(i) for i in range(6)] + [_stale_apartment(i) for i in range(3)])
    path = S.write(tmp_path_factory.mktemp("aslisted"), rows)
    mp = pytest.MonkeyPatch()
    mp.setenv("SPB_SNAPSHOT_JSON", str(path))
    mp.delenv("SPB_SHORTLIST_MAX", raising=False)
    mp.delenv("SPB_MAX_CANDIDATES", raising=False)
    mp.setattr(C, "datetime", S.Sep16)
    import research as module
    mp.setattr(module, "EXPLAIN_BUDGET_MS", 120000)
    yield module
    mp.undo()


def _lines():
    return {"v2:atlas-%03d" % i: _apartment(i)["lot_address"] for i in range(6)}


def _paged(research, **kw):
    return S.run(research, explain=True, offset=0, limit=500, needs_confirming_offset=0,
                 needs_confirming_limit=50, **kw)


def test_a_paged_card_titled_only_by_its_place_shows_its_line(research):
    out = _paged(research)
    by_hash = {e["content_hash"]: e for e in out["shortlist"]}
    for h, line in _lines().items():
        card = by_hash[h]
        assert card["lot_address"] == "Melbourne VIC"
        assert card["as_listed"] == line
    named = [e for e in out["shortlist"] if not e["content_hash"].startswith("v2:atlas")]
    assert named and not any("as_listed" in e for e in named)


def test_a_paged_needs_confirming_card_shows_its_line_too(research):
    out = _paged(research)
    stale = [e for e in out["needs_confirming"] if e["content_hash"].startswith("v2:brightly")]
    assert len(stale) == 3
    for e in stale:
        assert e["lot_address"] == "Melbourne VIC"
        assert e["as_listed"].startswith("%d " % int(e["as_listed"].split()[0]))
        assert "2 BED + 2 BATH" in e["as_listed"]


def test_every_card_that_shows_its_line_is_one_the_nightly_counts(research):
    out = _paged(research)
    cards = out["shortlist"] + out["needs_confirming"]
    gained = [e for e in cards if "as_listed" in e]
    assert len(gained) == 9
    assert all(SR.no_lot_and_no_street(e) for e in gained)
    counted = [e for e in out["shortlist"] if SR.no_lot_and_no_street(e)]
    assert {e["content_hash"] for e in counted} == set(_lines())


def _without_clocks(answer, drop_line=False):
    """`answer` as JSON with its wall-clock readings blanked (the Kommo note's completion
    time, the explanation's timings), and with drop_line every card's as_listed taken off."""
    answer = json.loads(json.dumps(answer, default=str))
    if drop_line:
        for e in answer.get("shortlist", []) + answer.get("needs_confirming", []):
            e.pop("as_listed", None)
    fields = (answer.get("kommo_payload") or {}).get("custom_fields_update") or {}
    if "research_completed_date" in fields:
        fields["research_completed_date"] = "<clock>"
    help_ = answer.get("search_help") or {}
    help_.pop("timing_ms", None)
    (help_.get("needs_confirming") or {}).pop("ms", None)
    return answer


def test_nothing_else_of_a_paged_answer_moves(research, monkeypatch):
    """The same search with the line switched off: card for card, field for field, the same
    answer but for as_listed -- ids, ranks, buildings, counts, pages, the Kommo note."""
    with_line = _paged(research)
    building = with_line["buildings"][0]["key"]
    with_building = S.run(research, offset=0, limit=500, building=building)
    monkeypatch.setattr(research, "as_listed", lambda line, card: "")
    without = _paged(research)
    without_building = S.run(research, offset=0, limit=500, building=building)
    assert not any("as_listed" in e for e in without["shortlist"] + without["needs_confirming"])
    assert _without_clocks(with_line, True) == _without_clocks(without)
    assert _without_clocks(with_building, True) == _without_clocks(without_building)


def test_an_unpaged_answer_is_byte_for_byte_as_it_was(research, monkeypatch):
    """Clare's hook pages nothing and never reads the line: its answer carries none -- not
    on a full card, a compact card or a needs-confirming card -- and is the very bytes it
    was (but for the clock), so the byte budget cuts no more cards."""
    hook = S.run(research)
    explained = S.run(research, explain=True)
    for answer in (hook, explained):
        assert not any("as_listed" in e for e in answer["shortlist"])
        assert not any("as_listed" in e for e in answer.get("needs_confirming", []))
    monkeypatch.setattr(research, "as_listed", lambda line, card: "never")
    again, explained_again = S.run(research), S.run(research, explain=True)
    assert (json.dumps(_without_clocks(again), sort_keys=False)
            == json.dumps(_without_clocks(hook), sort_keys=False))
    assert _without_clocks(explained_again) == _without_clocks(explained)
    assert S.size(again) == S.size(hook)


def test_the_line_reaches_no_kommo_note_and_no_client_document(research):
    out = _paged(research)
    assert any("as_listed" in e for e in out["shortlist"])
    for line in _lines().values():
        unit = line.split(" 2 BED")[0]                  # '40 4000', the unit as listed
        for field in ("kommo_payload", "client_report_html", "comparisons"):
            assert unit not in json.dumps(out.get(field), default=str), field


def test_a_compact_card_never_carries_it(research):
    assert "as_listed" not in research.COMPACT_FIELDS
