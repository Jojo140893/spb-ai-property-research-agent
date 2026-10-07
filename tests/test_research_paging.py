"""The shortlist and the needs-confirming group are paged, and no response is over 4 MB
(plan ranks 1 and 5, 7 Oct 2026).

Once every candidate is scored (test_every_candidate_is_scored.py) a state-wide VIC search
shortlists about 2,900 lots, and its response measured 14.9 MB against Vercel's 4.5 MB
limit. So api/research.py pages:

  * a caller that sends offset/limit (the Research tab, the Hot Leads re-run) gets that page
    as full cards, each with its rank and building, plus the totals: shortlist_count, every
    building on the shortlist (lot count, price range) and buildings_count, and page;
    "building" narrows the page to one building; with explain, needs_confirming_offset /
    needs_confirming_limit page the amber group, which used to stop at 50;
  * a caller that sends none of them -- Clare's hook, which matches a caller's lot against
    the WHOLE json.shortlist -- still gets every shortlisted lot: the first 600 as full cards
    exactly as before, the rest as compact cards carrying every field the hook reads;
  * whatever the caller sends, the body stays under RESPONSE_BYTES_MAX, and a response that
    had to list fewer cards says so (page.next_offset, shortlist_detail.cut).
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
from tests import _synthetic_stock as S  # noqa: E402

HOOK = Path(r"D:\Coleen\vapi-kommo-hook")
# Every shortlist field the vapi-kommo-hook reads to match a caller's lot (lib/tools/
# stock-match.js: decideMatch, matchListing, describeListing, tellApart, formatAddress) and
# to answer (lib/tools/verify.js: the availability flags, the date said aloud), checked
# against its source on 7 Oct 2026. Agent 2's hand-off (lib/research.js mapRecommendation)
# reads more, but only of the top 3, which are always full cards.
HOOK_MATCH_FIELDS = {
    "content_hash", "builder_name", "house_design", "lot_address", "lot_number",
    "estate_context", "suburb", "state", "advertised_package_price", "bedrooms",
    "bathrooms", "land_size_sqm", "house_size_sqm", "score", "date_checked",
    "availability_by_the_sheet", "availability_by_exception_list",
}


@pytest.fixture(scope="module")
def research(tmp_path_factory):
    rows = S.stock(fresh=1500, estates=30, stale=120, recent_unstated=6)
    path = S.write(tmp_path_factory.mktemp("paging"), rows)
    mp = pytest.MonkeyPatch()
    mp.setenv("SPB_SNAPSHOT_JSON", str(path))
    mp.delenv("SPB_SHORTLIST_MAX", raising=False)
    mp.delenv("SPB_MAX_CANDIDATES", raising=False)
    mp.setattr(C, "datetime", S.Sep16)
    # "confirm": the six blank lots on the exceptions-only list need confirming (the
    # shipped "available" would promote them, read yesterday, to results).
    mp.setattr(C, "EXCEPTIONS_LIST_MODE", "confirm")
    import research as module
    mp.setattr(module, "EXPLAIN_BUDGET_MS", 120000)
    mp.setattr(module, "MAX_NEEDS_CONFIRMING", 50)
    yield module
    mp.undo()


@pytest.fixture(scope="module")
def unpaged(research):
    return S.run(research)


def _hashes(entries):
    return [e["content_hash"] for e in entries]


# ------------------------------------------------------------------ a caller that does not page

def test_an_unpaged_answer_lists_every_shortlisted_lot(research, unpaged):
    """Clare's hook sends {client_brief} only, and matches against the whole shortlist."""
    assert unpaged["shortlist_count"] == 1500
    assert len(unpaged["shortlist"]) == 1500
    assert len(set(_hashes(unpaged["shortlist"]))) == 1500
    assert "page" not in unpaged and "buildings" not in unpaged


def test_the_first_600_are_full_cards_and_the_rest_compact(research, unpaged):
    full, rest = unpaged["shortlist"][:600], unpaged["shortlist"][600:]
    assert all("scoring_details" in e and "compact" not in e for e in full)
    assert all(e.get("compact") is True for e in rest)
    assert all(set(e) <= set(research.COMPACT_FIELDS) | {"compact"} for e in rest)
    detail = unpaged["shortlist_detail"]
    assert (detail["full"], detail["compact"], detail["cut"]) == (600, 900, 0)


def test_a_compact_card_carries_every_field_the_hook_reads(research, unpaged):
    assert HOOK_MATCH_FIELDS <= set(research.COMPACT_FIELDS)
    rest = unpaged["shortlist"][600:]
    for field in HOOK_MATCH_FIELDS:
        assert all(field in e for e in rest), field
    # The values are the full card's, not a summary of them.
    full_again = S.run(research, offset=600, limit=5)["shortlist"]
    for compact, full in zip(rest[:5], full_again):
        assert compact["content_hash"] == full["content_hash"]
        for field in HOOK_MATCH_FIELDS & set(full):
            assert compact[field] == full[field], field


@pytest.mark.skipif(not (HOOK / "lib" / "tools" / "stock-match.js").is_file(),
                    reason="the vapi-kommo-hook is not checked out beside the app")
def test_the_hook_reads_no_shortlist_field_a_compact_card_lacks(research):
    """Read the hook's own matcher: every `item.<field>` it reads off a listing is kept."""
    read = set()
    for name in ("stock-match.js", "verify.js"):
        text = (HOOK / "lib" / "tools" / name).read_text(encoding="utf-8")
        read |= set(re.findall(r"\bitem\??\.([a-z_]+)\b", text))
    text = (HOOK / "lib" / "kommo-lead.js").read_text(encoding="utf-8")
    body = text[text.index("export function formatAddress"):]
    read |= set(re.findall(r"\br\??\.([a-z_]+)\b", body[:body.index("\n}\n")]))
    # source_address arrives with the Stage B row labels; kept whenever an entry has it.
    assert read - {"source_address"} <= set(research.COMPACT_FIELDS), read - set(
        research.COMPACT_FIELDS)


def test_the_top_3_agent_2_hands_to_kommo_are_full_cards(unpaged):
    for e in unpaged["shortlist"][:3]:
        for field in ("realistic_total_price", "car_spaces", "recommendation",
                      "recommendation_reason", "source_link"):
            assert field in e, field


# ------------------------------------------------------------------ a caller that pages

def test_the_pages_add_up_to_the_whole_ranking(research, unpaged):
    pages, offset = [], 0
    while offset is not None:
        out = S.run(research, offset=offset, limit=400)
        assert out["shortlist_count"] == 1500 and out["page"]["total"] == 1500
        assert out["page"]["offset"] == offset and out["page"]["returned"] == len(out["shortlist"])
        pages.append(out)
        offset = out["page"]["next_offset"]
    assert [len(p["shortlist"]) for p in pages] == [400, 400, 400, 300]
    every = [e for p in pages for e in p["shortlist"]]
    assert _hashes(every) == _hashes(unpaged["shortlist"])
    assert [e["rank"] for e in every] == list(range(1, 1501))
    assert all("scoring_details" in e and "compact" not in e for e in every)
    assert pages[-1]["page"]["next_offset"] is None


def test_the_totals_count_buildings(research):
    out = S.run(research, offset=0, limit=10)
    assert out["buildings_count"] == 30 == len(out["buildings"])
    assert sum(b["lots"] for b in out["buildings"]) == out["shortlist_count"] == 1500
    by_key = {b["key"]: b for b in out["buildings"]}
    estate_07 = [b for b in out["buildings"] if b["name"] == "Estate 07"]
    assert len(estate_07) == 1
    b = estate_07[0]
    # Estate 07 holds lots 7, 37, 67, ... -- 50 of them, $407,000 to $1,877,000.
    assert (b["lots"], b["price_min"], b["price_max"]) == (50, 407000.0, 1877000.0)
    assert b["suburb"] == "Tarneit" and b["state"] == "VIC"
    for e in out["shortlist"]:
        assert e["building_key"] in by_key and e["building_name"] == by_key[e["building_key"]]["name"]
    # Best first: the building of the top-ranked lot leads.
    assert out["buildings"][0]["key"] == out["shortlist"][0]["building_key"]


@pytest.mark.parametrize("estate, suburb, name", [
    ("Olivine", "Donnybrook", "Olivine"),
    ("Estate : Olivine Donnybrook", "Donnybrook", "Olivine"),
    ("Estate: Mandalay Resort Beveridge", "Beveridge", "Mandalay Resort"),
    ("Lindfield House - Lindfield", "Lindfield", "Lindfield House"),
    ("The Fields 7 20 Pm", "Bellfield", "The Fields"),
    ("Donnybrook", "Donnybrook", "Donnybrook"),
    ("Q3 Lot Single", "Truganina", ""),
    ("Stage 12", "Ormeau", ""),
])
def test_one_estate_under_two_spellings_is_one_building(research, estate, suburb, name):
    assert research._building_name(estate, suburb) == name


def test_a_release_heading_is_not_a_building_and_falls_back_to_the_price_list(research):
    from types import SimpleNamespace
    cand = SimpleNamespace(state="VIC", suburb="Truganina", builder_name="Ceduna Hattan",
                           estate_context="Q3 Lot Single")
    row = {"state": "VIC", "source_channel": "E-Agent", "estate_name": "Q3 Lot Single",
           "builder_name": "Ceduna Hattan", "source_doc": "doc-ceduna"}
    assert research._building_of(cand, row, set()) == ("VIC|doc:doc-ceduna",
                                                       "Ceduna Hattan price list")
    row = dict(row, estate_name="Estate : Olivine Donnybrook")
    olivine = research._building_of(SimpleNamespace(**dict(vars(cand), suburb="Donnybrook")),
                                    dict(row, suburb="Donnybrook"), set())
    assert olivine == ("VIC|olivine", "Olivine")


def test_a_building_page_holds_only_that_buildings_lots(research):
    first = S.run(research, offset=0, limit=1)
    key = [b for b in first["buildings"] if b["name"] == "Estate 07"][0]["key"]
    out = S.run(research, offset=0, limit=500, building=key)
    assert out["page"]["total"] == 50 == len(out["shortlist"])
    assert out["page"]["building"] == key
    assert {e["building_key"] for e in out["shortlist"]} == {key}
    assert out["shortlist_count"] == 1500, "the totals stay the whole search's"


@pytest.mark.parametrize("payload, message", [
    ({"offset": -1}, "'offset' must be a whole number"),
    ({"limit": "ten"}, "'limit' must be a whole number"),
    ({"limit": True}, "'limit' must be a whole number"),
    ({"offset": 1.5}, "'offset' must be a whole number"),
    ({"building": 7}, "'building' must be a building key"),
])
def test_paging_values_are_checked(research, payload, message):
    with pytest.raises(research.BadRequest, match=message):
        S.run(research, **payload)


def test_a_limit_past_the_maximum_is_lowered(research):
    out = S.run(research, offset=0, limit=100000)
    assert out["page"]["limit"] == research.PAGE_LIMIT_MAX
    assert len(out["shortlist"]) == research.PAGE_LIMIT_MAX


# ------------------------------------------------------------------ never over the limit

def test_no_response_is_over_the_byte_limit(research, monkeypatch, unpaged):
    assert S.size(unpaged) <= research.RESPONSE_BYTES_MAX < 4_000_000
    monkeypatch.setattr(research, "RESPONSE_BYTES_MAX", 600_000)
    small = S.run(research)
    assert S.size(small) <= 600_000
    detail = small["shortlist_detail"]
    assert detail["cut"] > 0 and len(small["shortlist"]) == 1500 - detail["cut"]
    assert _hashes(small["shortlist"]) == _hashes(unpaged["shortlist"])[:len(small["shortlist"])]
    paged = S.run(research, offset=100, limit=500)
    assert S.size(paged) <= 600_000
    page = paged["page"]
    assert 0 < page["returned"] < 500 and page["next_offset"] == 100 + page["returned"]


# ------------------------------------------------------------------ needs confirming, paged

def test_the_needs_confirming_group_is_paged_to_its_end(research):
    first = S.run(research, explain=True, offset=0, limit=10, needs_confirming_offset=0,
                  needs_confirming_limit=50)
    total = first["needs_confirming_count"]
    assert total == 126 == first["needs_confirming_page"]["total"]
    listed, nxt = list(first["needs_confirming"]), first["needs_confirming_page"]["next_offset"]
    assert len(listed) == 50 and nxt == 50
    while nxt is not None:
        more = S.run(research, explain=True, offset=0, limit=0, needs_confirming_offset=nxt,
                     needs_confirming_limit=50)
        assert more["shortlist"] == []
        listed += more["needs_confirming"]
        nxt = more["needs_confirming_page"]["next_offset"]
    assert len(listed) == len({e["content_hash"] for e in listed}) == 126
    # None of them is on the shortlist, and the shortlist's totals are unchanged by them.
    assert not {e["content_hash"] for e in listed} & {
        e["content_hash"] for e in S.run(research)["shortlist"]}


def test_the_group_says_how_many_were_read_in_the_last_3_nights(research):
    out = S.run(research, explain=True, offset=0, limit=10)
    help_ = out["search_help"]["needs_confirming"]
    assert help_["recent"] == 6 and help_["recent_nights"] == 3
    assert help_["count"] == 126


def test_an_unpaged_explanation_lists_the_first_50_as_before(research):
    out = S.run(research, explain=True)
    assert out["needs_confirming_count"] == 126 and len(out["needs_confirming"]) == 50
    assert "needs_confirming_page" not in out
