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

Repair round (8 Oct 2026), from the verifiers' findings: a street or place named with an agent
word is kept ('12 Agent Street'); what a part said before its small print is kept ('Lot 178,
Wine cellar lot included. Agent' -- Clare could no longer confirm that lot by its words; '12
Smith St Agent commission 3%'; '3 Bed 3 Bath 1 Powder purchaser'); an incentive parts at more
breaks so the buyer's money never goes with the agent's; and '$50,000 bonus' beside the
purchaser's own offer is the agent's.
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

# What a person must not read. "E-Agent" is the portal's name, which a channel says; '12 Agent
# Street' and "Commission's Hill Estate" are a street and an estate (the stock below has both).
_NAMES = r"(?! Street)(?!(?:'|&#x27;|&#39;)s Hill Estate)"
SMALL_PRINT = re.compile(r"commission%s|(?<!E-)\bagent%s|purchaser|eligib|highlighted|\bfirb\b|"
                         r"guaranteed income" % (_NAMES, _NAMES), re.I)
# In an incentive the buyer's offer stays: only the agent's share is small print there.
AGENTS_SHARE = re.compile(r"commission|(?<!E-)\bagent", re.I)

TITLES = [
    # live titles on the 8 Oct database copy, each with what a person now reads
    ("Single Car Space & OB Storage Agent commission=6%plus, South Yarra VIC",
     "Single Car Space & OB Storage, South Yarra VIC"),
    ("Agentcommission=6%plus, South Yarra VIC", "South Yarra VIC"),
    ("Carstacker.Agentcommission=, South Yarra VIC", "Carstacker, South Yarra VIC"),
    ("4 SE- Chapel St, Agent commission=6%plus", "4 SE- Chapel St"),
    ("Lot 178, Wine cellar lot included. Agent", "Lot 178, Wine cellar lot included"),
    ("LISTINGSAVAILABLEFORSALEFORE-AGENT, Flagstaff VIC", "Flagstaff VIC"),
    ("2 Bed 2 Bath 1 Powder purchaser, Melbourne VIC", "2 Bed 2 Bath 1 Powder, Melbourne VIC"),
    ("purchaser, Melbourne VIC", "Melbourne VIC"),
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
    if "rebate" in text:
        assert L.shown_incentive(text) is text
    else:
        assert L.without_agent_text(text, "<place>") is text
    assert L.without_agent_text(None) is None
    assert L.shown_incentive(None) is None


# A street or a place named with an agent word, written as a name: an address, kept whole.
NAMED = [
    "12 Agent Street, Tarneit VIC",
    "Lot 5 Commission Road, Tarneit",
    "Lot 3, Purchaser Court, Tarneit",
    "Lot 12, Commission's Hill Estate, Tarneit",
    "Lot 5 Eligibility Lane, Tarneit",
    "Lot 5 Highlighted Way, Tarneit",
    "12 AGENT STREET, TARNEIT VIC",
    "Lot 12 Smith St/Agent Rd corner, Tarneit",
]


@pytest.mark.parametrize("title", NAMED)
def test_a_street_or_place_named_with_an_agent_word_is_kept(title):
    """Fails before the repair round (8 Oct 2026): whole words alone read 'Agent Street' as a
    note, and '12 Agent Street, Tarneit VIC' came out 'Tarneit VIC'."""
    assert L.without_agent_text(title, "<place>") is title
    assert L.shown_title(title, {"suburb": "Tarneit", "state": "VIC"}) is title


def test_a_note_is_still_a_note_when_it_is_not_written_as_a_name():
    # lower case: a note's words, not a name
    assert L.without_agent_text("commission road show, Tarneit", "<place>") == "Tarneit"
    # the type must follow the word: 'Agent commission' is a note even before a street
    assert L.without_agent_text("Lot 5, Agent commission Smith St", "<place>") == "Lot 5"


# What a part said before its small print, kept: a sentence that ended before it, a street
# address in front of it. 'Lot 178, Wine cellar lot included. Agent' is a live title.
LEADS = [
    ("Lot 178, Wine cellar lot included. Agent", "Lot 178, Wine cellar lot included"),
    ("Wine cellar lot included.Agent, South Yarra VIC", "Wine cellar lot included, South Yarra VIC"),
    ("Car stacker. Agent commission =, South Yarra VIC", "Car stacker, South Yarra VIC"),
    ("NE -river & Eastern Double Car Space & OB Storage Wine cellar lot included. Agent, "
     "South Yarra VIC", "NE -river & Eastern Double Car Space & OB Storage Wine cellar lot "
     "included, South Yarra VIC"),
    # '2.5' is no sentence's end, and the lot's description before the stop stays whole
    ("3BED2.5BATH+STUDY+2 Wine cellar lot included. Agent, South Yarra VIC",
     "3BED2.5BATH+STUDY+2 Wine cellar lot included, South Yarra VIC"),
    ("12 Smith St Agent commission 3%", "12 Smith St"),
    ("Lot 5 Smith Road Agent commission 2%, Tarneit", "Lot 5 Smith Road, Tarneit"),
    # small print in brackets goes with its brackets
    ("Lot 12, 12 Smith St (FIRB approved), Tarneit", "Lot 12, 12 Smith St, Tarneit"),
    # a lone 'purchaser' closing a line is the next column's heading run into it (Aria)
    ("3 Bed 3 Bath 1 Powder purchaser, Melbourne VIC", "3 Bed 3 Bath 1 Powder, Melbourne VIC"),
    ("Single Car Space Single Car Space Storage Cage purchaser",
     "Single Car Space Single Car Space Storage Cage"),
    # an agent's commission rate closing a line is the commission column run into it
    # ('Pricelist New Agent.pdf')
    ("Single Car Space & OB Storage Agentcommission=6%plus, South Yarra VIC",
     "Single Car Space & OB Storage, South Yarra VIC"),
    ("SE-Eastern suburb Double Car Space&OB Storage Agent commission = 6%.",
     "SE-Eastern suburb Double Car Space&OB Storage"),
]


@pytest.mark.parametrize("raw, shown", LEADS)
def test_what_a_part_said_before_its_small_print_is_kept(raw, shown):
    """Fails before the repair round (8 Oct 2026): a part with small print went whole, so
    'Lot 178, Wine cellar lot included. Agent' read 'Lot 178' and Clare, asked for 'Lot 178,
    Wine cellar lot included', could no longer confirm the lot; '12 Smith St Agent commission
    3%' lost its street; a unit's own words went with the commission or heading after them."""
    assert L.without_agent_text(raw, "<place>") == shown


def test_a_note_with_nothing_before_its_small_print_still_goes_whole():
    for raw in ("Agent commission=6%plus", "Agentcommission=6%plus",
                "Stock highlighted in orange below is eligible for the 100% commission offer.",
                "UNIT GROUND m2 MEZZANINE m2 TOTAL m2 PRICE GUARANTEED INCOME P.A.*",
                "External Agent Price List", "purchaser",
                "Property Address FIRB Anticipated Completion Commission Rate& Payment Term",
                # what is said OF the purchaser or the commission is a note, not the line's
                # own words
                "Stamp duty payable by purchaser", "Foreign purchaser",
                "Lot 5 sold to the purchaser", "3 Bed 3 Bath purchaser rebate",
                "Price includes Agent commission = 2%", "Plus Agent commission = 2%",
                "Price excludes agent commission of 2%"):
        assert L.without_agent_text(raw) == "", raw


INCENTIVES = [
    # the agent's share goes; the buyer's money and its words stay
    ("$50,000 purchaser rebate/agent bonus", "$50,000 purchaser rebate"),
    ("$5,000 cashback + agent bonus $2,000", "$5,000 cashback"),
    ("Rebate $1,250,500/agent fee", "Rebate $1,250,500"),
    ("Agent bonus $5,000; Purchaser rebate $10,000", "Purchaser rebate $10,000"),
    ("Commission 4% + $10,000 rebate", "$10,000 rebate"),
    ("$15,000 rebate (agent referral fee $2,000)", "$15,000 rebate"),
    ("$15,000 rebate (agent fee $2,000) plus FIRB covered", "$15,000 rebate plus FIRB covered"),
    ("$10,000 rebate.Agent bonus $5,000", "$10,000 rebate"),
    ("Rebate $15,000 agent fee $2,000", "Rebate $15,000"),
    ("$10,000 rebate plus agent commission 2%", "$10,000 rebate"),
    # an amount that is the agent's goes with the agent's words
    ("$20,000 agent bonus, $10,000 purchaser rebate", "$10,000 purchaser rebate"),
    ("$20,000 bonus for agents", ""),
    ("Agent commission 2%", ""),
    # '$50,000 bonus' beside the purchaser's own offer is the agent's: 12 live rows of
    # 'Pricelist New Agent.pdf' (South Yarra), whose commission column runs '6% plus' into it
    ("$50,000bonus.Purchaserrebate", "Purchaserrebate"),
    ("$50,000 bonus.Purchaserrebate", "Purchaserrebate"),
    ("$50,000bonus.Purchaser rebate", "Purchaser rebate"),
    ("$50,000 bonus.Purchaser rebate", "Purchaser rebate"),
]
# A bonus or an offer with no agent beside it is the buyer's, and stays as it was.
BUYERS_OWN = ["$35,000 Discount, Bonus", "$10,000 bonus",
              "bonus front lot landscaping worth up to $6,500", "$10,000 purchaser rebate",
              "rebate + FIRB fee covered (FIRB buyer) or $65,000 | $34,700 rebate"]


@pytest.mark.parametrize("raw, shown", INCENTIVES)
def test_an_incentive_loses_the_agents_share_and_never_the_buyers_money(raw, shown):
    """Fails before the repair round (8 Oct 2026): an incentive parted only at commas, '|'
    and '/' between words, so '$5,000 cashback + agent bonus $2,000' came out '' -- the card
    kept its incentive amount with no words -- and '$50,000bonus.Purchaserrebate' kept the
    agent's bonus."""
    assert L.shown_incentive(raw) == shown
    assert L.shown_incentive(shown) == shown


@pytest.mark.parametrize("text", BUYERS_OWN)
def test_the_buyers_own_bonus_or_offer_is_not_the_agents(text):
    assert L.shown_incentive(text) is text


def test_money_is_never_cut_at_its_comma():
    """Fails before 8 Oct 2026: Stage B split at every comma, so '$50,000 purchaser
    rebate/agent bonus' (37 live rows) came out '$50'. The amount is one part; the '/' sets
    the agent's bonus apart, and the purchaser's rebate is the offer."""
    raw = "$50,000 purchaser rebate/agent bonus"
    assert L.shown_incentive(raw) == "$50,000 purchaser rebate"
    # as a title or estate the purchaser words go too -- and still nothing reads '$50'
    assert L.without_agent_text(raw, "<place>") == "<place>"
    assert L.without_agent_text("$1,250,000 to $1,300,000, Agent commission 2%") == \
        "$1,250,000 to $1,300,000"
    # a unit address keeps its slash: '3/45' is not a break, and the street before the note
    # stays (repair round, 8 Oct 2026)
    assert L.without_agent_text("3/45 May Street Agent commission, Kew VIC") == \
        "3/45 May Street, Kew VIC"


@pytest.mark.parametrize("raw, _shown", TITLES + ESTATES + LEADS)
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
    for r in rows[8:10]:
        r.update(estate_name="Upfront FIRB Fee payable by", lot_number=None,
                 lot_address="2 Bed 2 Bath 1 Powder purchaser")
    for r in rows[10:12]:            # Aria: the estate column ends in the next one's heading
        r.update(estate_name="2 Bed 2 Bath 1 Powder purchaser", lot_number=None,
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
    # Repair round (8 Oct 2026): the agent's '$50,000 bonus' beside the purchaser's offer
    # ('Pricelist New Agent.pdf'), what a title said before its small print, and an address
    # named with an agent word.
    for r in rows[26:28]:
        r.update(incentive_text="$50,000bonus.Purchaserrebate", incentive_amount=50000.0,
                 suburb="South Yarra", source_doc="doc-south-yarra")
    rows[28].update(lot_number="178", lot_address="Lot 178, Wine cellar lot included. Agent",
                    suburb="South Yarra", estate_name="Wine cellar lot included. Agent",
                    source_doc="doc-south-yarra")
    rows[29].update(lot_number="7", lot_address="Lot 7, 12 Agent Street",
                    estate_name="Commission's Hill Estate", source_doc="doc-commissions-hill")
    # Two stale lots, one price and design, no lot number, on two documents, whose titles
    # differ only in their small print: as shown both read 'South Yarra VIC'. Two lots, so
    # both are offered to confirm -- a copy test on the title as shown would hide one.
    for n, title in enumerate(("Agent commission =6%plus, South Yarra VIC",
                               "Agentcommission=6%plus, South Yarra VIC")):
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
            off.setattr(where, "shown_incentive", lambda value: value, raising=False)
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


def test_the_repair_round_reaches_every_caller(searched):
    """Fails before the repair round (8 Oct 2026), through the whole search, for the Research
    tab (paged) and Clare's hook (unpaged): the agent's '$50,000 bonus' stayed in the
    incentive, 'Lot 178, Wine cellar lot included. Agent' read 'Lot 178', '2 Bed 2 Bath 1
    Powder purchaser' read only its place, and '12 Agent Street' / "Commission's Hill Estate"
    lost their names. The incentive amount is as read."""
    _research, _rows, (unpaged, paged), _before = searched
    for resp in (unpaged, paged):
        by_hash = {e["content_hash"]: e for e in resp["shortlist"]}
        for h in ("v2:fresh-00026", "v2:fresh-00027"):
            assert (by_hash[h]["incentive_text"], by_hash[h]["incentive_amount"]) == \
                ("Purchaserrebate", 50000.0), by_hash[h]
        lot178 = by_hash["v2:fresh-00028"]
        assert "Wine cellar lot included" in lot178["lot_address"], lot178["lot_address"]
        assert not SMALL_PRINT.search(lot178["lot_address"]), lot178["lot_address"]
        named = by_hash["v2:fresh-00029"]
        assert "12 Agent Street" in named["lot_address"], named["lot_address"]
        for h in ("v2:fresh-00010", "v2:fresh-00011"):
            unit = by_hash[h]["lot_address"]
            assert unit.startswith("2 Bed 2 Bath 1 Powder"), unit
            assert not SMALL_PRINT.search(unit), unit
    full = {e["content_hash"]: e for e in paged["shortlist"]}
    assert full["v2:fresh-00028"]["estate_context"] == "Wine cellar lot included"
    assert full["v2:fresh-00029"]["estate_context"] == "Commission's Hill Estate"


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
    card = dict(package, lot_address="Single Car Space & OB Storage, South Yarra VIC")
    assert SR.card_title(package) == "Single Car Space & OB Storage, South Yarra VIC"
    assert P._visible_key(package) == P._visible_key(card) == (
        "single car space & ob storage, south yarra vic", 1425000)
    bare = dict(package, lot_address="Agent commission=6%plus, South Yarra VIC")
    assert P._visible_key(bare) == P._visible_key(dict(bare, lot_address="South Yarra VIC"))
    clean = dict(package, lot_address="Lot 5, Tarneit VIC")
    assert SR.card_title(clean) == "Lot 5, Tarneit VIC"
