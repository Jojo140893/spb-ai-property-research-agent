"""search_regressions fails what Coleen phoned about (7 Oct 2026).

Until 7 Oct the search scored only the 600 cheapest candidates in a state and nothing in the
nightly replay noticed: the obvious-rows check failed a 'truncated' row, but it ran only on
briefs where the cap never bound, and an obvious row past the 50 listed needs-confirming
cards was only a warning. Now:

  * any response saying the candidate cap cut the search fails, whatever its brief expects;
  * any response over 4 MB fails (Vercel refuses a body over 4.5 MB);
  * a dashboard brief is replayed as the Research tab asks -- the first page, then every
    further page of the shortlist and of the needs-confirming group -- and fails when the
    pages do not add up to the counts the first one gave;
  * a hook-shaped brief (no paging, as Clare's hook posts) fails unless its one answer lists
    every shortlisted lot.
The state-wide no-budget briefs Coleen compares with E-Agent (vic_any, nsw_any, qld_any,
sa_any) are in data/colin_searches.json with the obvious-rows check on.
"""
import io
import json
import sys
from datetime import date
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import search_regressions as SR  # noqa: E402

TODAY = date(2026, 9, 16)
SPEC = {"form": {"client_name": "Unit", "state": "QLD", "budget_min": 0, "budget_max": 750000},
        "never_list": {}, "briefs": []}
USED = {"state": "QLD", "budget_min": 0, "budget_max": 750000, "primary_suburbs": []}


def _row(n):
    return {"content_hash": "v2:%08d" % n, "state": "QLD", "price": 600000.0 + n,
            "availability_status": "Available", "date_checked": "15/09/2026",
            "source_doc": "doc-1", "lot_address": "Lot %d Example Rise" % n,
            "superseded_by": None, "estate_name": "Example Rise", "lot_number": str(n),
            "source_channel": "E-Agent", "state_source": "listing locality"}


def _card(row):
    return {"content_hash": row["content_hash"], "state": "QLD",
            "advertised_package_price": row["price"], "date_checked": row["date_checked"],
            "lot_address": row["lot_address"], "estate_context": row["estate_name"],
            "lot_number": row["lot_number"]}


def _answer(shortlist, explain=True, **kw):
    out = {"status": "success", "shortlist_count": len(shortlist), "rejected_count": 0,
           "shortlist": list(shortlist), "research_record_id": "Unit - QLD/General",
           "brief_used": dict(USED), "brief_adjustments": [], "rejected_log": []}
    if explain:
        out.update(search_help={"relaxations": []}, needs_confirming=[],
                   needs_confirming_count=0, needs_confirming_hidden={})
    out.update(kw)
    return out


def _replay(briefs, answers, rows):
    posted = []

    def post(body):
        posted.append(json.loads(json.dumps(body)))
        return 200, json.loads(json.dumps(answers(body))), 0.01
    report = SR.Report(io.StringIO())
    SR.Run(dict(SPEC, briefs=briefs), post, SR.Catalogue(rows), TODAY, report).execute()
    return report, report.out.getvalue(), posted


BRIEF = {"id": "b", "client_brief": {}, "expect": {"brief_used": {"state": "QLD"}}}
HOOK = {"id": "h", "hook_shaped": True, "explain": False,
        "client_brief": {"client_name": "Clare", "state": "QLD", "budget_max": 750000},
        "expect": {"brief_used": {"state": "QLD"}}}


def test_a_search_the_candidate_cap_cut_fails():
    rows = [_row(1)]
    capped = _answer([_card(rows[0])], candidate_cap={"limit": 600, "cut": 2643})
    report, out, _posted = _replay([BRIEF], lambda body: capped, rows)
    assert report.failures >= 1
    assert "the candidate cap cut the search: 2643 candidate(s) were never scored " \
           "(SPB_MAX_CANDIDATES=600)" in out, out
    # A server from before 7 Oct 2026 says so only in the explanation's funnel: fails too.
    old = _answer([_card(rows[0])])
    old["search_help"]["funnel"] = {"capped": True, "gates": [
        {"code": "truncated", "stage": "pool", "count": 2643, "in_price_range": None}]}
    report, out, _posted = _replay([BRIEF], lambda body: old, rows)
    assert "the candidate cap cut the search: 2643 candidate(s) were never scored " \
           "(SPB_MAX_CANDIDATES=unstated)" in out, out


def test_a_response_over_4_mb_fails():
    rows = [_row(1)]
    big = _answer([_card(rows[0])], rejected_log=[{"reason": "x" * 4_100_000}])
    report, out, _posted = _replay([BRIEF], lambda body: big, rows)
    assert report.failures >= 1
    assert "over the 4,000,000-byte limit" in out, out


def test_a_dashboard_brief_is_posted_with_the_first_page():
    rows = [_row(1)]
    report, out, posted = _replay([BRIEF], lambda body: _answer([_card(rows[0])]), rows)
    assert posted[0]["offset"] == 0 and posted[0]["limit"] == 100
    assert posted[0]["needs_confirming_offset"] == 0
    assert posted[0]["needs_confirming_limit"] == 50
    assert posted[0]["explain"] is True


def _paged(rows, nc_rows=(), short_page=None):
    """A server that pages `rows` and `nc_rows` the way api/research.py does."""
    cards, nc = [_card(r) for r in rows], [_card(r) for r in nc_rows]

    def answers(body):
        off, lim = body.get("offset", 0), body.get("limit", 100)
        page = cards[off:off + lim]
        if short_page is not None and off == short_page:
            page = []
        nxt = off + len(page) if off + len(page) < len(cards) else None
        noff, nlim = body.get("needs_confirming_offset", 0), body.get("needs_confirming_limit", 50)
        npage = nc[noff:noff + nlim]
        nnxt = noff + len(npage) if noff + len(npage) < len(nc) else None
        return _answer(page, shortlist_count=len(cards), needs_confirming=npage,
                       needs_confirming_count=len(nc),
                       page={"offset": off, "limit": lim, "returned": len(page),
                             "total": len(cards), "next_offset": nxt},
                       needs_confirming_page={"offset": noff, "limit": nlim,
                                              "returned": len(npage), "total": len(nc),
                                              "next_offset": nnxt})
    return answers


def test_every_page_of_both_lists_is_fetched_and_checked():
    rows = [_row(n) for n in range(1, 731)]
    nc_rows = [_row(n) for n in range(2001, 2121)]
    for r in nc_rows:
        r["date_checked"] = "20/08/2026"
    report, out, posted = _replay([BRIEF], _paged(rows, nc_rows), rows + nc_rows)
    offsets = [(p.get("offset"), p.get("limit")) for p in posted]
    assert offsets[:3] == [(0, 100), (100, 500), (600, 500)], offsets
    nc = [(p.get("needs_confirming_offset"), p.get("needs_confirming_limit"))
          for p in posted if p.get("limit") == 0]
    assert nc == [(50, 500)], nc
    assert report.failures == 0, out


def test_pages_that_do_not_add_up_fail():
    rows = [_row(n) for n in range(1, 731)]
    report, out, _posted = _replay([BRIEF], _paged(rows, short_page=600), rows)
    assert "the shortlist page at offset 600 did not come" in out, out
    assert "the pages hold 600 shortlisted cards, shortlist_count says 730" in out, out


def test_a_hook_brief_is_posted_unpaged_and_must_list_every_lot():
    rows = [_row(n) for n in range(1, 4)]
    short = _answer([_card(rows[0])], explain=False, shortlist_count=3)
    report, out, posted = _replay([HOOK], lambda body: short, rows)
    assert posted == [{"client_brief": HOOK["client_brief"]}]
    assert "the hook's answer lists 1 of 3 shortlisted lots" in out, out
    cut = _answer([_card(r) for r in rows], explain=False,
                  shortlist_detail={"full": 3, "compact": 0, "cut": 2})
    report, out, _posted = _replay([HOOK], lambda body: cut, rows)
    assert "the unpaged answer left 2 shortlisted lot(s) out" in out, out


def test_the_state_wide_briefs_are_replayed_with_the_obvious_rows_check():
    spec = SR.load_spec()
    briefs = {b["id"]: b for b in spec["briefs"]}
    for state in ("vic", "nsw", "qld", "sa"):
        b = briefs["%s_any" % state]
        assert b["client_brief"] == {"state": state.upper()}
        assert set(b["drop"]) == {"budget_max", "preferred_spending_cap"}
        assert b["expect"]["obvious_rows"] is True
        assert b["expect"]["brief_used"]["budget_max"] == 9007199254740991
    hook = briefs["hook_shaped_vic_any"]
    assert hook["hook_shaped"] is True and hook["expect"]["equal_to"] == "vic_any"


def test_a_copy_the_search_reads_as_a_shown_card_is_not_a_lost_lot():
    """CC-0127 read 03/09 as "CC-0127, Bairnsdale Brookfield Lakes, Bairnsdale VIC" and
    06/10 as "CC-0127, Bairnsdale": the search shows the second and leaves the first out of
    the needs-confirming group as its copy (research._same_listing: the same lot number in
    the state, at the price, the land agreeing). The check asks the bundle's own rule, so
    the copy is "collapsed into a shown twin", not "scored, not shown"."""
    sys.path.insert(0, str(APP / "api"))
    import research
    shown = _row(1)
    shown.update(lot_number="CC-0127", lot_address="CC-0127, Bairnsdale", price=870965.0)
    copy = _row(2)
    copy.update(lot_number="CC-0127", price=870965.0,
                lot_address="CC-0127, Bairnsdale Brookfield Lakes, Bairnsdale VIC")
    other = _row(3)
    other.update(lot_number="CC-0128", price=870965.0, lot_address="CC-0128, Bairnsdale")
    card = dict(_card(shown), land_size_sqm=0.0, house_size_sqm=0.0, bedrooms=4,
                suburb="Bairnsdale")
    resp = {"shortlist": [card], "needs_confirming": []}
    find = SR.shown_copy_finder(research, resp, SR.Catalogue([shown, copy, other]))

    def package(row):
        return dict(_card(row), bedrooms=4, suburb="Bairnsdale", land_size_sqm=0.0,
                    house_size_sqm=0.0, confirm=["stale"])
    assert find(package(copy)) is True
    assert find(package(other)) is False, "another lot number is another lot"
    visible = {(card["lot_address"].lower(), round(card["advertised_package_price"]))}
    outcome = SR._obvious_outcome({"gate": "scored", "package": package(copy)}, visible,
                                  research, None, USED, False, find)
    assert outcome == ("collapsed_into_visible", "")
    # A row merged into a capture that is itself hidden, and hidden by its own guard too.
    guarded = dict(package(other), confirm_guard="newer_capture_exists")
    assert SR._obvious_outcome({"gate": "same_listing_collapsed", "package": guarded},
                               visible, research, None, USED, False, find) == \
        ("newer_capture_exists", "")
    # ...and one with no guard of its own still fails as merged into a hidden twin.
    assert SR._obvious_outcome({"gate": "same_listing_collapsed", "package": package(other)},
                               visible, research, None, USED, False, find)[0] == \
        "same_listing_collapsed"


def test_an_obvious_row_past_the_listed_group_and_a_truncated_one_fail():
    """Before 7 Oct 2026 the first only warned ("needs confirming beyond the listed cap") and
    the second failed with no word of the cap. Both now fail, by name."""
    cut, unlisted, listed = _row(1), _row(2), _row(3)
    for i, r in enumerate((cut, unlisted, listed), 1):
        r["id"] = 100 + i
    unlisted["date_checked"] = listed["date_checked"] = "20/08/2026"

    class C:
        ADMITTABLE_GATES = ()

        @staticmethod
        def snapshot_build_cells(rec):
            return {"price_kind": "package", "price_in_text": True, "text_price_max": None}

        @staticmethod
        def prepared_for_snapshot(rows, path):
            return None

        @staticmethod
        def explain_rows(brief, rows, prepared=None, admit=()):
            return [(cut, {"gate": "truncated", "package": dict(_card(cut))}),
                    (unlisted, {"gate": "scored",
                                "package": dict(_card(unlisted), confirm=["stale"])})]

    class Research:
        @staticmethod
        def _normalised_brief(brief):
            return dict(brief), []

    rows = [cut, unlisted, listed]
    catalogue = SR.Catalogue(rows, rows)
    hook = SR.obvious_rows_check(C, Research, None, catalogue, "stock.json")

    class Run:
        report = SR.Report(io.StringIO())
        today = TODAY

    resp = {"brief_used": USED, "shortlist": [], "needs_confirming": [_card(listed)],
            "needs_confirming_count": 2}
    hook(Run, "b", {"id": "b"}, {}, resp)
    out = Run.report.out.getvalue()
    assert "[FAIL] b: obvious row v2:00000001 (id 101) -- QLD, $600,001 -- was cut by the " \
           "candidate cap before it was scored" in out, out
    assert "[FAIL] b: obvious row v2:00000002 (id 102) needs confirming but is past every " \
           "page of the listed group" in out, out
    assert Run.report.failures == 2
