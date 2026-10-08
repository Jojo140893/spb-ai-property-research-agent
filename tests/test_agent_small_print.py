"""No agent small print reaches the people who read a search (8 Oct 2026).

A price list's notes for agents ride a flattened line into a card's title, a heading row
into its estate, an agent's file name into a building's name, and the agent's share into an
incentive: 'Single Car Space & OB Storage Agent commission=6%plus, South Yarra VIC', 'Stock
highlighted in orange below is eligible for the 100% commission offer.', 'The Albertine
External Agent price list', '$50,000 purchaser rebate/agent bonus'. Since the 600-candidate
cap was lifted (PR #27) 118 cards carried such words on 7 Oct against 4 before, to the
Research tab, Hot Leads, the Kommo note and Clare's options. Stage B (stage-b-row-labels,
4df156a / 9300a14) took them out with address_label.without_agent_text; this is that piece
alone, ported to main without the row labels, and with Stage B's bug fixed: it split a line
at every comma, so '$50,000 purchaser rebate/agent bonus' came out '$50'.

Text only. The packages, the scores, every merge and copy test, the building keys, the
paging and the counts are what they were: the candidate keeps its title and estate as read
for api/research.py's copy tests and building keys, and the parity and search-regressions
checks key a hidden row's twin on the title as shown, on both sides.
"""
import json
import re
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
for _p in (str(APP / "api"), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _candidates as C  # noqa: E402
import address_label as L  # noqa: E402
from tests import _synthetic_stock as S  # noqa: E402

# What a person must not read. "E-Agent" is the portal's name, which a channel says.
SMALL_PRINT = re.compile(r"commission|(?<!E-)\bagent|purchaser|eligib|highlighted|\bfirb\b|"
                         r"guaranteed income", re.I)
# In an incentive the buyer's offer stays: only the agent's share is small print there.
AGENTS_SHARE = re.compile(r"commission|(?<!E-)\bagent", re.I)

TITLES = [
    # live titles on the 8 Oct database copy, each with what a person now reads
    ("Single Car Space & OB Storage Agent commission=6%plus, South Yarra VIC", "South Yarra VIC"),
    ("Agentcommission=6%plus, South Yarra VIC", "South Yarra VIC"),
    ("Carstacker.Agentcommission=, South Yarra VIC", "South Yarra VIC"),
    ("4 SE- Chapel St, Agent commission=6%plus", "4 SE- Chapel St"),
    ("Lot 178, Wine cellar lot included. Agent", "Lot 178"),
    ("LISTINGSAVAILABLEFORSALEFORE-AGENT, Flagstaff VIC", "Flagstaff VIC"),
    ("2 Bed 2 Bath 1 Powder purchaser, Melbourne VIC", "Melbourne VIC"),
    ("Upfront FIRB Fee payable by, St Kilda Road VIC", "St Kilda Road VIC"),
    ("External Agent Price List, Melbourne VIC", "Melbourne VIC"),
    # a note's tail goes with the note: the part after the comma continues its sentence
    ("Price is approximately for the build + commission inc GST, estimate combined. Please",
     "<place>"),
    ("to ex GST, with guaranteed income of " + chr(0x2013) + " per annum on current "
     "availability.", "<place>"),
]
ESTATES = [
    ("Stock highlighted in orange below is eligible for the 100% commission offer.", ""),
    ("UNIT GROUND m%s MEZZANINE m%s TOTAL m%s PRICE GUARANTEED INCOME P.A.*"
     % ((chr(0xb2),) * 3), ""),
    ("Property Address FIRB Anticipated Completion Commission Rate& Payment Term", ""),
    ("Upfront FIRB Fee payable by", ""),
    ("purchaser", ""),
    # a document's heading in front of the development's own address: the address stays
    ("Agent Stock List 167 Ingleburn Road, Leppington", "167 Ingleburn Road, Leppington"),
]
CLEAN = [
    # words that are part of a real address or estate name are never small print
    "Lot 60, THE HILLS COLLECTION, Glenvale QLD",
    "12 Commissioners Gully Road, Kinglake VIC",
    "Agentia Park ,  Tarneit",
    "12 Purchase Street, Firbank VIC",         # whole words only: neither is small print
    "Eligo Rise, Highlight Estate",
    "Lot 1, offer",
    "rebate + FIRB fee covered (FIRB buyer) or $65,000 | $34,700 rebate | $65,000 rebate",
    "",
]


@pytest.mark.parametrize("raw, shown", TITLES)
def test_a_title_loses_its_small_print_and_keeps_its_address(raw, shown):
    assert L.without_agent_text(raw, "<place>") == shown


@pytest.mark.parametrize("raw, shown", ESTATES)
def test_an_estate_loses_its_small_print_and_keeps_its_name(raw, shown):
    assert L.without_agent_text(raw) == shown


@pytest.mark.parametrize("text", CLEAN)
def test_a_text_without_small_print_comes_back_as_it_was(text):
    words = L.AGENT_ONLY_TEXT if "rebate" in text else L.AGENT_TEXT
    assert L.without_agent_text(text, "<place>", words=words) is text
    assert L.without_agent_text(None) is None


def test_money_is_never_cut_at_its_comma():
    """Fails before 8 Oct 2026: Stage B split at every comma, so '$50,000 purchaser
    rebate/agent bonus' (37 live rows) came out '$50'. The amount is one part; the '/' sets
    the agent's bonus apart, and the purchaser's rebate is the offer."""
    raw = "$50,000 purchaser rebate/agent bonus"
    assert L.without_agent_text(raw, words=L.AGENT_ONLY_TEXT) == "$50,000 purchaser rebate"
    # as a title or estate the purchaser words go too -- and still nothing reads '$50'
    assert L.without_agent_text(raw, "<place>") == "<place>"
    assert L.without_agent_text("$1,250,000 to $1,300,000, Agent commission 2%") == \
        "$1,250,000 to $1,300,000"
    # a unit address keeps its slash: '3/45' is not a break
    assert L.without_agent_text("3/45 May Street Agent commission, Kew VIC") == "Kew VIC"


@pytest.mark.parametrize("raw, _shown", TITLES + ESTATES)
def test_what_comes_back_carries_no_small_print_and_a_second_pass_changes_nothing(raw, _shown):
    once = L.without_agent_text(raw, "<place>")
    assert not L.AGENT_TEXT.search(once), once
    assert L.without_agent_text(once, "<place>") is once


def test_a_title_that_was_all_small_print_falls_back_to_its_place():
    card = {"suburb": "South Yarra", "state": "VIC"}
    assert L.card_place(card) == "South Yarra VIC"
    assert L.shown_title("Agent commission=6%plus", card) == "South Yarra VIC"
    assert L.shown_title("Agent commission=6%plus", {"suburb": "", "state": ""}) == L.NO_ADDRESS
    assert L.shown_title("Lot 4, Tarneit VIC", card) == "Lot 4, Tarneit VIC"


# ---------------------------------------------------------------------- the whole search

def _small_print_stock():
    rows = S.stock(fresh=40, estates=4)
    for i, r in enumerate(rows[:8]):
        r.update(suburb="South Yarra", lot_number=None, estate_name=(
            "Stock highlighted in orange below is eligible for the 100% commission offer."),
            lot_address=("Single Car Space & OB Storage Agent commission=6%plus, South Yarra VIC"
                         if i % 2 else "Agent commission=6%plus, South Yarra VIC"))
    for r in rows[8:12]:
        r.update(estate_name="Upfront FIRB Fee payable by", lot_number=None,
                 lot_address="2 Bed 2 Bath 1 Powder purchaser")
    for r in rows[12:16]:            # a project named off 'The Albertine External Agent.pdf'
        r.update(builder_name="The Albertine External Agent", attribution_scope="project",
                 estate_name="External Agent Price List", source_doc="doc-albertine")
    for r in rows[16:20]:            # 'Pricelist New Agent.pdf': no building, one document
        r.update(builder_name="Agent", estate_name=None, source_doc="doc-agent",
                 incentive_text="$50,000 purchaser rebate/agent bonus", incentive_amount=50000.0)
    for r in rows[20:22]:
        r.update(estate_name="Agent Stock List 167 Ingleburn Road, Tarneit",
                 attribution_scope="project", builder_name="", source_doc="doc-ingleburn")
    for r in rows[22:24]:            # a heading as the estate: it names no building
        r.update(estate_name="LISTINGSAVAILABLEFORSALEFORE-AGENT", attribution_scope="project",
                 builder_name="", source_doc="doc-flagstaff")
    for r in rows[24:26]:            # a project named off 'Prince Parade Agent.pdf'
        r.update(builder_name="Prince Parade Agent", attribution_scope="project",
                 estate_name=None, source_doc="doc-prince-parade")
    # Two stale lots, one price and design, no lot number, on two documents, whose titles
    # differ only in their small print: as shown both read 'South Yarra VIC'. Two lots, so
    # both are offered to confirm -- a copy test on the title as shown would hide one.
    for n, title in enumerate(("Car stacker. Agent commission =, South Yarra VIC",
                               "Wine cellar lot included. Agent, South Yarra VIC")):
        rows.append(S.row("v2:stale-sp-%d" % n, builder_name="Old Rise Homes",
                          lot_number=None, lot_address=title, suburb="South Yarra",
                          estate_name=None, source_doc="doc-stale-sp-%d" % n,
                          price=777000.0, date_checked="20/08/2026",
                          line_key="k-stale-sp-%d" % n))
    return rows


def _answers(module):
    return (S.run(module), S.run(module, explain=True, offset=0, limit=100))


@pytest.fixture(scope="module")
def searched(tmp_path_factory):
    """The search over the small-print stock as it runs, and again with the strip turned
    into a no-op everywhere it is applied: the second is the search before 8 Oct 2026."""
    rows = _small_print_stock()
    path = S.write(tmp_path_factory.mktemp("smallprint"), rows)
    mp = pytest.MonkeyPatch()
    mp.setenv("SPB_SNAPSHOT_JSON", str(path))
    mp.delenv("SPB_MAX_CANDIDATES", raising=False)
    mp.delenv("SPB_SHORTLIST_MAX", raising=False)
    mp.setattr(C, "datetime", S.Sep16)
    import kommo_agent
    import research as module
    mp.setattr(module, "EXPLAIN_BUDGET_MS", 120000)
    mp.setattr(module, "REQUEST_DEADLINE_MS", 600000)
    now = _answers(module)
    with pytest.MonkeyPatch.context() as off:
        for where in (module, kommo_agent):
            off.setattr(where, "without_agent_text",
                        lambda value, fallback="", words=None: value, raising=False)
            off.setattr(where, "shown_title", lambda title, card: title, raising=False)
        before = _answers(module)
    yield module, rows, now, before
    mp.undo()


def _cards(resp):
    return list(resp.get("shortlist") or []) + list(resp.get("needs_confirming") or [])


def test_no_caller_of_api_research_receives_agent_small_print(searched):
    """Fails before 8 Oct 2026: the titles, estates, building names, the Kommo note's lines
    and top match, the rejected log and the comparisons carried 'Agent commission=6%plus',
    the commission-offer heading, the FIRB note and the agent's bonus."""
    _research, rows, (unpaged, paged), _before = searched
    assert unpaged["shortlist_count"] == 40, "premise: every fresh lot is shortlisted"
    for name, resp in (("unpaged (Clare's hook)", unpaged), ("paged (Research tab)", paged)):
        for card in _cards(resp):
            for field in ("lot_address", "estate_context", "building_name"):
                assert not SMALL_PRINT.search(str(card.get(field) or "")), (name, field, card)
            assert not AGENTS_SHARE.search(str(card.get("incentive_text") or "")), (name, card)
        for b in resp.get("buildings") or []:
            assert not SMALL_PRINT.search(b.get("name") or ""), b
        note = json.dumps(resp.get("kommo_payload") or {})
        assert not SMALL_PRINT.search(note), (name, note[:400])
        for entry in resp.get("rejected_log") or []:
            assert not SMALL_PRINT.search(str(entry.get("address") or "")), entry
        for html in (resp.get("comparisons") or {}).values():
            assert not SMALL_PRINT.search(html), html[:400]
    assert len(paged["needs_confirming"]) >= 2, "premise: the stale lots need confirming"


def test_the_titles_say_where_the_lot_is_and_the_offer_stays(searched):
    _research, _rows, (unpaged, paged), _before = searched
    titles = {e["lot_address"] for e in unpaged["shortlist"]}
    assert {"South Yarra VIC", "Tarneit VIC"} <= titles, sorted(titles)[:8]
    agent = [e for e in unpaged["shortlist"] if e["builder_name"] == "Agent"]
    assert agent and all(e["incentive_text"] == "$50,000 purchaser rebate"
                         and e["incentive_amount"] == 50000.0 for e in agent), agent[:1]
    estates = {e["estate_context"] for e in paged["shortlist"]}
    assert "167 Ingleburn Road, Tarneit" in estates, estates
    names = {b["name"] for b in paged["buildings"]}
    assert {"The Albertine", "Prince Parade", "Price list (builder not named)",
            "167 Ingleburn Road"} <= names, names
    # a heading is not cut into a name: 'LISTINGSAVAILABLEFORSALEFORE-AGENT' is no building
    flagstaff = {b["name"] for b in paged["buildings"] if b["key"] == "VIC|" + _research_norm(
        "LISTINGSAVAILABLEFORSALEFORE-AGENT")}
    assert flagstaff == {"Price list (builder not named)"}, flagstaff


def _research_norm(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).split())


def _without_text(resp):
    """The answer with every text the strip may change taken out: what must be equal."""
    out = json.loads(json.dumps(resp, default=str))
    for card in _cards(out):
        for field in ("lot_address", "estate_context", "incentive_text", "building_name"):
            card.pop(field, None)
    for b in out.get("buildings") or []:
        b.pop("name", None)
    for entry in out.get("rejected_log") or []:
        entry.pop("address", None)
    for key in ("kommo_payload", "comparisons", "client_report_html", "research_record_id"):
        out.pop(key, None)
    return out


def test_text_only_every_card_merge_count_and_building_key_is_as_before(searched):
    """The same cards in the same order, the same needs-confirming lots, the same building
    keys and counts, field for field -- only the stripped texts differ. Both stale South
    Yarra lots are offered: their titles as shown are one title, and a copy test on it would
    have hidden one (the candidate keeps its title as read for that)."""
    _research, _rows, now, before = searched
    for got, was in zip(now, before):
        assert _without_text(got) == _without_text(was)
    paged_now, paged_before = now[1], before[1]
    stale = sorted(e["content_hash"] for e in paged_now["needs_confirming"]
                   if e["content_hash"].startswith("v2:stale-sp-"))
    assert stale == ["v2:stale-sp-0", "v2:stale-sp-1"], stale
    assert {e["lot_address"] for e in paged_now["needs_confirming"]
            if e["content_hash"] in stale} == {"South Yarra VIC"}
    assert [e["building_key"] for e in paged_now["shortlist"]] == \
        [e["building_key"] for e in paged_before["shortlist"]]
    assert "VIC|the albertine external agent" in {b["key"] for b in paged_now["buildings"]}


def test_the_copy_tests_and_building_keys_read_the_title_and_estate_as_read(searched):
    """The candidate says the clean text and keeps the text as read beside it, and every key
    api/research.py builds from a candidate -- the needs-confirming copy test's listing key
    and lot key, the street it compares, the building a lot with no row is grouped under --
    reads the text as read, exactly as before 8 Oct 2026. Fails if any of them is keyed on
    the title or estate as shown instead."""
    research, rows, _now, _before = searched
    import _bootstrap
    raw_title = "Agent commission=6%plus, South Yarra VIC"
    raw_estate = "Stock highlighted in orange below is eligible for the 100% commission offer."
    row = next(r for r in rows if r["lot_address"] == raw_title)
    packages, _counts = C.build_packages(S.BRIEF, [row])
    processed, _rejected = _bootstrap.get_agent().score_candidates(S.BRIEF, packages)
    cand = processed[0]
    assert (cand.lot_address, cand.estate_context) == ("South Yarra VIC", "")
    assert (cand.lot_address_as_read, cand.estate_context_as_read) == (raw_title, raw_estate)
    price = cand.price_breakdown.advertised_package_price
    assert research._candidate_key(cand) == research._listing_key(raw_title, cand.house_design,
                                                                  price)
    keyed = research._candidate_key(cand, row)[1]
    assert keyed == C.same_lot_key({"lot_number": "", "lot_address": raw_title,
                                    "estate_context": raw_estate,
                                    "content_hash": row["content_hash"]}, row)
    assert research._building_of(cand, None, set()) == (
        "VIC|" + research._norm(raw_estate), "Example Homes price list")


def test_a_package_still_meets_the_card_it_was_merged_into():
    """Fails before 8 Oct 2026: the parity and search-regressions checks keyed a hidden row's
    twin on the package's title as read and the card's title as sent, so a lot merged into a
    card whose small print was taken out read as merged into a card nobody sees."""
    import parity_check as P
    import search_regressions as SR
    package = {"lot_address": "Single Car Space & OB Storage Agent commission=6%plus, "
                              "South Yarra VIC", "suburb": "South Yarra", "state": "VIC",
               "advertised_package_price": 1425000.0}
    card = dict(package, lot_address="South Yarra VIC")
    assert SR.card_title(package) == "South Yarra VIC"
    assert P._visible_key(package) == P._visible_key(card) == ("south yarra vic", 1425000)
    clean = dict(package, lot_address="Lot 5, Tarneit VIC")
    assert SR.card_title(clean) == "Lot 5, Tarneit VIC"
