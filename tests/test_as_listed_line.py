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
    # (Its link words at the end go: repair round 1, 9 Oct 2026, below.)
    ("Click here Click here", "",
     "Traditional KRAUSS 204 $604,000.00 $507,227.00 $0.00 $1,111,227.00 Click here Click here",
     "Traditional KRAUSS 204 $604,000.00 $507,227.00 $0.00 $1,111,227.00",
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


# ------------------------------------------------- repair round 1 (9 Oct 2026): the cut

# Stored lines of the 8 Oct database (the snapshot's lot_address), cut at the 110th
# character by the extractors. The first round showed them as they stand, with no mark, so
# '$3,500,00' beside a card priced $3,500,000 read as a price.
CUT_IN_AMOUNT = ("10 1001 3 Bed 3 Bath, 2 Cars 3A.1 NW Storage Cage Single Car Space Single Car "
                 "Space 176.2 16.9 193.1 $3,500,00")
CUT_IN_AMOUNT_QLD = ("134.0 Theodore Level 3 3 Bed + MPR / 2 Bath Type F / S3 3.0 2.0 99.0 13.0 "
                     "112.0 North East Available $1,537,50")
CUT_AFTER_A_COMMA = ("2 2.04 3 Bed 2 Bath 2 Car B3 North | Slater Street Car Spaces: 1 Car Spaces: "
                     "1 Storage Space: 1 154 16 170 $2,")
CUT_IN_A_SECOND_AMOUNT = ("7 701 3 Bed 3 Bath, 2 Cars 3A NW Single Car Space Single Car Space "
                          "Storage Cage 192.1 126 318.1 $4,250,000 $22")
CUT_IN_A_WORD = ("North 60405 Cloverton Kalkallo Available Richmond 13 Bright $320,000 $295,430 "
                 "$615,430 NA $615,430 Settled Wes")
CUT_AT_A_SPACE = ("Geelong 11921 Warralily Armstrong Creek Available Greenvale 14 Elevate $292,500 "
                  "$313,720 $606,220 NA $606,220 ")
CUT_IN_PORTAL_LINK = ("Available 15# House SAMARIUM 19 181.13m\u00b2 300.0 m\u00b2 4 beds / 2 baths "
                      "/ 2 cars 2-part Contract $968,900 Portal Li")
CUT_IN_A_LINK = ("Tropea Yamba 190m2 4 2 2 2 $480,000 $1,000,000 Volos Available Owner Occ. / "
                 "Investor 30.09.2026 https://drive.")
CUT_AFTER_A_LONE_DOLLAR = ("Available Open List 1101 L11 B1 2 2 1 Yes 3 84 11 95 $1,075,000.00 $ "
                           "5,562.41 $ 1,930.70 S 392.22 S 90.33 $ 20")
CUT_AFTER_SMALL_PRINT = ("10 1011A 2B06 North 76 8 84 $1,375,500 $18,099 available (FIRB buyer) or "
                         "$65,000 rebate for local. Light colou")
ELLIPSIS = "\u2026"
# The extractors' cut, as api/_candidates.py reads it.
CUT = C._TRUNCATED_AT


def test_the_cut_is_the_extractors_cut():
    """address_label marks a line cut where api/_candidates reads one as cut."""
    assert AL.LINE_CUT_AT == C._TRUNCATED_AT == 110
    assert AL.CUT_MARK == ELLIPSIS
    for line in (CUT_IN_AMOUNT, CUT_IN_AMOUNT_QLD, CUT_AFTER_A_COMMA, CUT_IN_A_SECOND_AMOUNT,
                 CUT_IN_A_WORD, CUT_AT_A_SPACE, CUT_IN_PORTAL_LINK, CUT_IN_A_LINK,
                 CUT_AFTER_A_LONE_DOLLAR, CUT_AFTER_SMALL_PRINT):
        assert len(line) == CUT


@pytest.mark.parametrize("title, line, shown", [
    # What the cut left of an amount is no amount: it goes, and the line says it goes on.
    ("Melbourne VIC", CUT_IN_AMOUNT,
     "10 1001 3 Bed 3 Bath, 2 Cars 3A.1 NW Storage Cage Single Car Space Single Car Space "
     "176.2 16.9 193.1" + ELLIPSIS),
    ("Stafford QLD", CUT_IN_AMOUNT_QLD,
     "134.0 Theodore Level 3 3 Bed + MPR / 2 Bath Type F / S3 3.0 2.0 99.0 13.0 112.0 North "
     "East Available" + ELLIPSIS),
    ("St Kilda Road VIC", CUT_AFTER_A_COMMA,
     "2 2.04 3 Bed 2 Bath 2 Car B3 North | Slater Street Car Spaces: 1 Car Spaces: 1 Storage "
     "Space: 1 154 16 170" + ELLIPSIS),
    ("Melbourne VIC", CUT_IN_A_SECOND_AMOUNT,
     "7 701 3 Bed 3 Bath, 2 Cars 3A NW Single Car Space Single Car Space Storage Cage 192.1 "
     "126 318.1 $4,250,000" + ELLIPSIS),
    # A list read from a PDF writes '$ 209.47': the cut took the amount, and the '$' it
    # leaves alone goes too (St Kilda).
    ("1 3 $ $, St Kilda VIC", CUT_AFTER_A_LONE_DOLLAR,
     "Available Open List 1101 L11 B1 2 2 1 Yes 3 84 11 95 $1,075,000.00 $ 5,562.41 $ 1,930.70 "
     "S 392.22 S 90.33" + ELLIPSIS),
    # ... and so is what it left of a word.
    ("Kalkallo VIC", CUT_IN_A_WORD,
     "North 60405 Cloverton Kalkallo Available Richmond 13 Bright $320,000 $295,430 $615,430 "
     "NA $615,430 Settled" + ELLIPSIS),
    ("with timber flooring., Docklands VIC", CUT_AFTER_SMALL_PRINT,
     "10 1011A 2B06 North 76 8 84 $1,375,500 $18,099 available or $65,000 rebate for local. "
     "Light" + ELLIPSIS),
    # A cut that fell at a space left whole words: they all stay.
    ("Armstrong Creek VIC", CUT_AT_A_SPACE,
     "Geelong 11921 Warralily Armstrong Creek Available Greenvale 14 Elevate $292,500 $313,720 "
     "$606,220 NA $606,220" + ELLIPSIS),
    # A link cut short goes whole, the 'Portal' of 'Portal Li' with it.
    ("Burpengary Ferriers Creek, Burpengary QLD", CUT_IN_PORTAL_LINK,
     "Available 15# House SAMARIUM 19 181.13m\u00b2 300.0 m\u00b2 4 beds / 2 baths / 2 cars "
     "2-part Contract $968,900" + ELLIPSIS),
    ("COOLIBAH ESTATE, Lowood QLD", CUT_IN_A_LINK,
     "Tropea Yamba 190m2 4 2 2 2 $480,000 $1,000,000 Volos Available Owner Occ. / Investor "
     "30.09.2026" + ELLIPSIS),
])
def test_a_cut_line_drops_what_the_cut_left_and_says_it_goes_on(title, line, shown):
    card = {"lot_address": title, "lot_number": "", "suburb": "Somewhere", "state": "VIC"}
    assert AL.as_listed_line(line, card) == (shown, "")
    # Within what the card shows (index.html AS_LISTED_SHOWN), so never cut a second time.
    assert len(shown) <= CUT


@pytest.mark.parametrize("title, line, shown", [
    # Not on the cut: the last word is the line's own, and there is no mark.
    ("Burpengary Ferriers Creek, Burpengary QLD",
     "Available 4# House PEWTER 19 176.42m\u00b2 255.0 m\u00b2 4 beds / 2 baths / 1 car 2-part "
     "Contract $998,900 Portal Link",
     "Available 4# House PEWTER 19 176.42m\u00b2 255.0 m\u00b2 4 beds / 2 baths / 1 car 2-part "
     "Contract $998,900"),
    ("Traditional Click here Click here",
     "Traditional JULIAN 177 $595,000.00 $474,186.00 $0.00 $1,069,186.00 Click here Click here",
     "Traditional JULIAN 177 $595,000.00 $474,186.00 $0.00 $1,069,186.00"),
    ("COOLIBAH ESTATE, Lowood QLD",
     "Marbella 210 Fortitude 210m2 4 2 2 1 $488,500 $997,500 Volos Available 30.09.2026 https:",
     "Marbella 210 Fortitude 210m2 4 2 2 1 $488,500 $997,500 Volos Available 30.09.2026"),
    ("Melbourne VIC", "45 4504 2 BED $1,567,000 Portal", "45 4504 2 BED $1,567,000 Portal"),
    ("Melbourne VIC", "45 4504 2 BED $1,567,000 $1,3", "45 4504 2 BED $1,567,000 $1,3"),
    # A link word in the middle of the line stays as read.
    ("Melbourne VIC", "45 4504 Click here 2 BED $1,567,000", "45 4504 Click here 2 BED $1,567,000"),
    # 109 characters: no cut, no mark, the last word kept.
    ("Melbourne VIC", ATLAS + " " + "x" * (104 - len(ATLAS)) + " Wes",
     None),
])
def test_a_line_not_on_the_cut_keeps_its_last_word_and_has_no_mark(title, line, shown):
    card = {"lot_address": title, "lot_number": "", "suburb": "Somewhere", "state": "VIC"}
    if shown is None:
        assert len(line) == CUT - 1
        shown = line
    assert AL.as_listed_line(line, card) == (shown, "")
    assert ELLIPSIS not in shown


def test_a_cut_line_whose_new_number_was_only_a_link_or_the_cut_adds_nothing():
    """The digits of a link, or of what the cut left of an amount, are no unit's number."""
    card = {"lot_address": "HEMMANT QLD", "lot_number": "", "suburb": "Hemmant", "state": "QLD"}
    assert AL.as_listed_line("HEMMANT Available https://drive.google.com/file/d/1mdk", card) == (
        "", NOTHING_NEW)
    cut = "HEMMANT Available " + "Lorem " * 15
    cut = (cut + "x" * CUT)[:CUT - 5] + " 4504"
    assert len(cut) == CUT
    assert AL.as_listed_line(cut, card) == ("", NOTHING_NEW)


def test_a_line_cut_at_120_is_fitted_to_the_card_at_a_word():
    """sources/adaptive_extract.py cuts at 120: the line shown loses whole words off its end
    until it and the mark fit the card's 110 characters, so index.html never cuts it again
    inside a word."""
    card = {"lot_address": "Clyde North VIC", "lot_number": "", "suburb": "Clyde North",
            "state": "VIC"}
    line = ("Ceduna 3770 Available Brighton 28 Elevate $289,300 $300,000 $589,300 NA $589,300 "
            "Titled Clyde North Display Village $1,2")
    assert len(line) == 120
    shown, why = AL.as_listed_line(line, card)
    assert why == "" and len(shown) <= CUT
    assert shown == ("Ceduna 3770 Available Brighton 28 Elevate $289,300 $300,000 $589,300 NA "
                     "$589,300 Titled Clyde North Display" + ELLIPSIS)


def test_a_cut_line_of_one_word_is_cut_at_the_room():
    card = {"lot_address": "Melbourne VIC", "lot_number": "", "suburb": "Melbourne",
            "state": "VIC"}
    word = "4504" + "A" * (CUT - 4)
    assert AL.as_listed_line(word, card) == (word[:CUT - 1] + ELLIPSIS, "")


@pytest.mark.parametrize("cut_at", range(60, 111))
def test_every_cut_atlas_line_fits_the_card_and_ends_in_the_mark(cut_at):
    """Atlas's line run on past the cut and cut at every point: what is shown never holds a
    piece of an amount, always ends in the mark, and fits in the card's 110 characters."""
    whole = ATLAS + " $1,567,000 $18,053 available North East 86.8 0 86.8 " * 2
    line = whole[:cut_at] if cut_at < CUT else whole[:CUT]
    card = {"lot_address": "Melbourne VIC", "lot_number": "", "suburb": "Melbourne",
            "state": "VIC"}
    shown, why = AL.as_listed_line(line, card)
    assert why == ""
    if len(line) < CUT:
        assert shown == " ".join(line.split())
        return
    assert shown.endswith(ELLIPSIS) and len(shown) <= CUT
    for amount in __import__("re").findall(r"\$[\d,]+", shown):
        assert amount in ("$1,567,000", "$18,053"), amount


def test_the_checks_count_a_bundle_built_before_the_one_definition(monkeypatch, tmp_path):
    """search_regressions run against a bundle built before 8 Oct 2026, whose address_label
    has no names_no_lot_and_no_street: it raised ImportError, and the check failed. It counts
    with this checkout's rule now (moved unchanged, so the count is the one that bundle's own
    checker made), and leaves the bundle's address_label the one `import` finds."""
    import importlib.util
    old = tmp_path / "address_label.py"
    old.write_text("NO_ADDRESS = '(no address in source row)'\n", encoding="ascii")
    spec = importlib.util.spec_from_file_location("address_label", str(old))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, "address_label", module)
    assert SR.no_lot_and_no_street({"lot_address": "Melbourne VIC", "lot_number": ""}) is True
    assert SR.no_lot_and_no_street({"lot_address": "Lot 12, Tarneit", "lot_number": ""}) is False
    assert SR.no_lot_and_no_street({"lot_address": "Melbourne VIC", "lot_number": "7"}) is False
    assert sys.modules["address_label"] is module


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
