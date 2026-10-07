"""parity_check.py: does the app show what the sources list? (7 Oct 2026)

Coleen phoned on 7 Oct 2026: the same search in E-Agent and in the Research tab, and E-Agent
always showed more. The audit of the 6 Oct database found a state-wide search scored only the
600 cheapest lots in the state (SPB_MAX_CANDIDATES), so VIC showed about one lot in nine of
what its sources listed -- and nothing checked, because the nightly replay's obvious-rows
check runs on two QLD briefs the cap never reaches.

A check that cannot fail reads as a pass, so every bar is made to fail here first:
  * SHOWN: the real bundle (built by build_web from this tree) over a clean synthetic
    catalogue passes; the same catalogue with SPB_MAX_CANDIDATES=50 exits 1 naming the cut
    lots; lots with no suburb, no state, or two lots of one list merged into one card fail
    by id; a sold-stock guard, a dead lot page and the same lot on another channel do not;
  * NOT OVER-SERVED: lots a re-read document no longer carries, over 2% of a state;
  * DRIFT: a night that falls more than 15% under its 7-night median, naming the channel and
    the document that fell;
  * READ: each manifest rule on its own;
  * paging: both response shapes, and a server that stops paging short;
  * and the plumbing: exit codes, nothing printed or published carries a link, the database
    and the bundle are untouched, run_daily runs it last and optional, the alert carries the
    table, the build publishes it, the runner keeps its history.
"""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for _p in (str(APP), str(TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _parity_fixture as F  # noqa: E402
import parity_check as P  # noqa: E402
import search_regressions as SR  # noqa: E402

TODAY = "06/10/2026"


# ------------------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    """The research bundle build_web writes, built from this tree."""
    site = tmp_path_factory.mktemp("parity_bundle")
    done = subprocess.run(
        [sys.executable, "-X", "utf8", "-c",
         "import sys; from pathlib import Path; import build_web; "
         "build_web._bundle_research_function(Path(sys.argv[1]), Path(sys.argv[2]))",
         str(APP), str(site)],
        cwd=str(APP), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert done.returncode == 0 and (site / "api" / "research.py").is_file(), (
        done.stdout[-2000:] + done.stderr[-2000:])
    return site / "api"


def _check(bundle, base, rows, *extra, env=None, history=None, manifest=None):
    """Run parity_check.py over `rows`; (exit code, stdout, report dict or None)."""
    snapshot, db = F.write(base, rows)
    return _run_check(bundle, base, snapshot, db, *extra, env=env, history=history,
                      manifest=manifest)


def _run_check(bundle, base, snapshot, db, *extra, env=None, history=None, manifest=None):
    report = base / "parity_report.json"
    hist = base / "parity_history.json"
    if history is not None:
        hist.write_text(json.dumps({"version": 1, "nights": history}), encoding="utf-8")
    man = base / "harvest_manifest.json"
    if manifest is not None:
        man.write_text(json.dumps(manifest), encoding="utf-8")
    run_env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    run_env.pop("SPB_MAX_CANDIDATES", None)
    run_env.update(env or {})
    done = subprocess.run(
        [sys.executable, "-X", "utf8", str(APP / "parity_check.py"), "--snapshot", str(snapshot),
         "--db", str(db), "--bundle", str(bundle), "--today", TODAY, "--history", str(hist),
         "--report", str(report), "--manifest", str(man),
         "--degraded-marker", str(base / "_run_degraded"), *extra],
        cwd=str(APP), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=run_env, timeout=600)
    body = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None
    return done.returncode, done.stdout + done.stderr, body


def _night(day, vic_lots=80, buildings=10, channel_lots=None, docs=None):
    """One earlier night in the history file, VIC counts as given."""
    return {"night": day, "states": {"VIC": {
        "lots": vic_lots, "buildings": buildings,
        "channels": channel_lots or {"E-Agent": vic_lots},
        "docs": docs or {"doc:doc-parity-rise-0": 8},
        "doc_labels": {"doc:doc-parity-rise-0": "E-Agent - Parity Homes - Rise 0 Price List.pdf"}}}}


EARLIER = ("29/09/2026", "30/09/2026", "01/10/2026", "02/10/2026", "03/10/2026",
           "04/10/2026", "05/10/2026")


def _tonights_manifest():
    """A clean data/harvest_manifest.json written tonight: a --nightly run without one
    fails the READ bar since repair round 1 (7 Oct 2026)."""
    return {"schema": 1, "generated": "06/10/2026 21:00", "entries": [_lane_entry()]}


# ------------------------------------------------------------- the SHOWN bar, for real

def test_a_catalogue_the_search_shows_whole_passes_every_bar(bundle, tmp_path):
    """The pass path: every obvious lot shown, in every state, by the bundle that ships."""
    code, out, body = _check(bundle, tmp_path, F.clean_catalogue())
    assert code == 0, out
    assert "Source-to-search parity, 06/10/2026: PASS" in out, out
    assert body["verdict"] == "PASS" and not body["failures"]
    rows = {r["state"]: r for r in body["rows"]}
    assert set(rows) == {"VIC", "NSW", "QLD", "SA", "WA"}
    assert rows["VIC"]["lots"] == [80, 80] and rows["VIC"]["e_agent_lots"] == [80, 80]
    assert rows["VIC"]["buildings"] == [10, 10]
    # The bars that had nothing to compare with say so, rather than reading as a pass.
    assert any("no earlier night" in n for n in body["notes"]), body["notes"]
    assert any("harvest_manifest.json" in n for n in body["notes"]), body["notes"]


def test_the_candidate_cap_at_50_fails_naming_the_cut_lots(bundle, tmp_path):
    """The audit's root cause, at a scale a test can afford: 80 VIC lots, 50 scored."""
    code, out, body = _check(bundle, tmp_path, F.clean_catalogue(),
                             env={"SPB_MAX_CANDIDATES": "50"})
    assert code == 1, out
    assert "VIC: E-Agent: 50 of 80 obvious lots shown (62.5%, needs 97%)" in out, out
    assert "VIC: all channels: 7 of 10 obvious buildings have a lot shown" in out, out
    cut = [f for f in body["failures"] if f.get("reason") == "truncated"]
    assert len(cut) == 1 and cut[0]["count"] == 30 and cut[0]["group"] == "VIC", cut
    # Named by id, the first 20: the dearest 30 were cut, ids 70050-70079.
    assert cut[0]["ids"] == list(range(70050, 70070)), cut[0]["ids"]
    assert "hidden as truncated -- cut by the candidate cap" in out
    assert {r["state"]: r["pass"] for r in body["rows"]} == {
        "VIC": False, "NSW": True, "QLD": True, "SA": True, "WA": True}


def _defects():
    """Three obvious lots hidden for reasons that are NOT allowed."""
    no_suburb = F.lot(500, estate="Parity Nowhere", doc="doc-nowhere", suburb="",
                      suburb_source="", postcode=None, lot_address="Lot 600 Parity Way",
                      price=650000, source_text="Lot 600 Parity Way Available 4 2 2 $650,000")
    no_state = dict(F.lot(501, estate="Parity Lost", doc="doc-lost"), state="",
                    state_source=None, postcode=None, lot_address="Lot 601 Parity Lost Way")
    # Two different units of one price list, carded with the same address and price: the
    # audit's false merge (Atlas units 2302 and 1712, both "Melbourne VIC"). The search no
    # longer merges them (rank 4, same_lot_key: two lines of one list naming no lot are two
    # lots), so on the integrated tree they must both be SHOWN.
    unit_a = F.lot(502, estate="Parity Tower", doc="doc-tower", lot_number="",
                   lot_address="Parity Tower, Tarneit VIC 3029", price=700000,
                   line_key="unit-2302", source_text="Unit 2302 Parity Tower 2 1 1 $700,000")
    unit_b = F.lot(503, estate="Parity Tower", doc="doc-tower", lot_number="",
                   lot_address="Parity Tower, Tarneit VIC 3029", price=700000,
                   line_key="unit-1712", source_text="Unit 1712 Parity Tower 2 1 1 $700,000",
                   suburb="Tarneit", postcode="3029")
    unit_a.update(suburb="Tarneit", postcode="3029")
    # Two packages on ONE lot at one price, two lines of one list (Goldstate lot 5710 on 6 Oct:
    # Atlanta 206 m2 and Trinidad 210 m2, both $712,000). Since repair round 1 the search
    # keys its dedupe on the house size and bedrooms as well, so those two are two cards.
    # Two designs that state the SAME facts at one price are still folded into one -- the
    # merge the search still makes, and which the bar must keep naming.
    pair = dict(estate="Parity Pair", doc="doc-pair", lot_number="5710",
                lot_address="5710 Piebald St, Tarneit VIC 3029", price=712000,
                suburb="Tarneit", postcode="3029")
    pkg_a = F.lot(504, line_key="pair-atlanta", house_sqm=206.0,
                  source_text="Mar-27 5710 Piebald St North West 12.5 28 350 $340,000 Atlanta "
                              "23 Panorama 4 Single 206 $372,000 $712,000 Available", **pair)
    pkg_b = F.lot(505, line_key="pair-trinidad", house_sqm=206.0,
                  source_text="Mar-27 5710 Piebald St North West 12.5 28 350 $340,000 Trinidad "
                              "23 Sovereign 4 Single 206 $372,000 $712,000 Available", **pair)
    # ...and the 6 Oct pair itself, 206 m2 against 210 m2: two cards, named by nobody.
    real = dict(pair, lot_number="5711", lot_address="5711 Piebald St, Tarneit VIC 3029",
                price=713000)
    gold_a = F.lot(506, line_key="gold-atlanta", house_sqm=206.0,
                   source_text="Mar-27 5711 Piebald St North West 12.5 28 350 $341,000 Atlanta "
                               "23 Panorama 4 Single 206 $372,000 $713,000 Available", **real)
    gold_b = F.lot(507, line_key="gold-trinidad", house_sqm=210.0,
                   source_text="Mar-27 5711 Piebald St North West 12.5 28 350 $341,000 "
                               "Trinidad 23 Sovereign 4 Single 210 $372,000 $713,000 Available",
                   **real)
    return [no_suburb, no_state, unit_a, unit_b, pkg_a, pkg_b, gold_a, gold_b]


def test_lots_hidden_without_an_allowed_reason_are_named_by_id(bundle, tmp_path):
    code, out, body = _check(bundle, tmp_path, F.clean_catalogue() + _defects())
    assert code == 1, out
    reasons = {f.get("reason"): f for f in body["failures"] if f.get("reason")}
    assert reasons["no_suburb"]["ids"] == [70500], out
    assert reasons["state_unknown"]["ids"] == [70501], out
    assert reasons["state_unknown"]["group"] == "blank"
    merged = reasons["merged_with_another_lot"]
    assert merged["count"] == 1 and merged["ids"][0] in (70504, 70505), out
    # The two units of one tower are both shown now, and so are the 206 / 210 m2 packages
    # of one lot (repair round 1): no failure names any of them.
    named = {i for f in body["failures"] for i in (f.get("ids") or [])}
    assert not named & {70502, 70503, 70506, 70507}, out


def test_two_captures_of_one_line_are_one_lot_and_two_packages_are_two():
    """Lines of one document, from the 6 Oct database (7 Oct 2026). A capture that missed a
    column is the same lot, which the search rightly shows once; two packages on one lot,
    or two units of one tower, are two lots, and merging them is a failure."""
    def same(a, b):
        return P.recaptured(SR.status_free(a), SR.status_free(b))
    # Hattan 19329 / 9088 and 17405 / 17404: one lot, read twice.
    assert same("Available Clyde North Titled House Lot 720 263 12.5 Denver 17 4 2 2 "
                "$361,000 $395,650 $756,650",
                "Available Clyde North Titled House Lot 720 263 12.5 Denver 17 Single Storey "
                "4 2 2 $361,000 $395,650 $756,650")
    assert same("Available Q3 Lot 186 EmblaF|Ceduna Single $317,129 $287,800 $604,929",
                "Available Clyde North Q3 Lot 186 6 Embla D|Ceduna 9.66 Single 1 $317,129 "
                "$287,800 $604,929")
    # Goldstate 15146 / 15145: two designs on lot 5710 at one price.
    assert not same("Mar-27 5710 Piebald St North West 12.5 28 350 $340,000 Atlanta 23 "
                    "Panorama 4 Single 206 $372,000 $712,000",
                    "Mar-27 5710 Piebald St North West 12.5 28 350 $340,000 Trinidad 23 "
                    "Sovereign 4 Single 210 $372,000 $712,000")
    # Same numbers, another design: still two packages.
    assert not same("Lot 12 Atlanta 23 Panorama 4 2 2 $700,000",
                    "Lot 12 Georgia 23 Sovereign 4 2 2 $700,000")
    # Atlas units 2302 and 1712: two units of one tower.
    assert not same("Unit 2302 Atlas 2 1 1 $700,000", "Unit 1712 Atlas 2 1 1 $700,000")
    assert not same("Lot 5", "Lot 5 Available")                  # too little to tell


def _allowed():
    """Three obvious lots the search hides for a reason the bar allows."""
    sold_later = F.lot(510, estate="Parity Sold", doc="doc-sold", day="05/10/2026",
                       lot_number="777")
    # On ANOTHER list: a capture on the row's own list, read after it, would make the row
    # one its list no longer carries, which is not obvious at all (repair round 1).
    sold_capture = F.lot(511, estate="Parity Sold", doc="doc-sold-elsewhere",
                         lot_number="777", status="Sold", line_key="line-510-sold")
    dead_page = F.lot(512, estate="Parity Gone", doc="doc-gone", lot_page_gone="05/10/2026",
                      lot_page_status="gone")
    # The same lot on two channels: identical facts, two documents.
    on_e_agent = F.lot(513, estate="Parity Twin", doc="doc-twin-e", lot_number="913",
                       line_key="twin")
    on_proxima = dict(on_e_agent, content_hash="v2:%064x" % 0xB0B0, id=70999,
                      source_channel="Proxima", source_doc="doc-twin-p", source_project_id="913",
                      source_url="https://example.invalid/proxima/913")
    return [sold_later, sold_capture, dead_page, on_e_agent, on_proxima]


def test_a_sold_stock_guard_a_dead_page_and_a_shown_twin_are_allowed(bundle, tmp_path):
    code, out, body = _check(bundle, tmp_path, F.clean_catalogue() + _allowed())
    assert code == 0, out
    allowed = " ".join(n for n in body["notes"] if "allowed reason" in n)
    for reason in ("newer_capture_disagrees", "lot_page_gone", "collapsed_into_shown_twin"):
        assert reason in allowed, (reason, body["notes"])


# ------------------------------------------------------------------ NOT OVER-SERVED

def _over_served():
    """Three VIC lots read on 03/10 from a Proxima project the harvest read again on 06/10
    without them -- still fresh enough to be shortlisted.

    PROXIMA, since repair round 1 (7 Oct 2026): the search no longer shows an E-Agent lot
    its own list was re-read without (fresh_row_guard), so only a channel it cannot judge
    that way -- every Proxima row shares the portal's index page as its document -- still
    provokes the bar."""
    common = dict(estate="Parity Project", channel="Proxima", doc="doc-proxima-index",
                  source_project_id="900")
    return ([F.lot(530 + n, **common) for n in range(10)]
            + [F.lot(520 + n, day="03/10/2026", **common) for n in range(3)])


def test_lots_a_re_read_list_no_longer_carries_fail_over_two_percent(bundle, tmp_path):
    code, out, body = _check(bundle, tmp_path, F.clean_catalogue() + _over_served())
    assert code == 1, out
    over = [f for f in body["failures"] if f["bar"] == "over-served"]
    assert len(over) == 1 and over[0]["group"] == "VIC", body["failures"]
    assert "3 of 93 shown lots (3.2%, at most 2%)" in over[0]["text"], over[0]["text"]
    assert "70520, 70521, 70522" in over[0]["text"]
    # Everything else is clean: the three are not obvious (read 3 nights ago), so the
    # SHOWN bar is untouched by them.
    assert {f["bar"] for f in body["failures"]} == {"over-served"}


def test_an_e_agent_list_re_read_without_its_lots_no_longer_shows_them(bundle, tmp_path):
    """The same shape on an E-Agent price list: the search hides the three itself now
    (document_reread_without_row), so nothing is over-served (repair round 1)."""
    gone = [F.lot(520 + n, estate="Parity Rise 0", doc="doc-parity-rise-0", day="03/10/2026")
            for n in range(3)]
    code, out, body = _check(bundle, tmp_path, F.clean_catalogue() + gone)
    assert code == 0, out
    assert not [f for f in body["failures"] if f["bar"] == "over-served"], body["failures"]


def test_a_lot_its_list_dropped_since_is_not_obvious():
    """Read two nights ago, and the list read IN FULL last night without it: not something
    the sources list tonight (repair round 1). A read cut short proves nothing."""
    day = SR._day
    reads = {"doc:a": {day("06/10/2026"): 40, day("04/10/2026"): 1},
             "doc:b": {day("06/10/2026"): 5, day("04/10/2026"): 40}}
    latest = {d: max(v) for d, v in reads.items()}
    rec = {"date_checked": "04/10/2026"}
    assert P.read_left_it_out(rec, "doc:a", latest, reads, set(), P._latest_read_is_full)
    assert not P.read_left_it_out(rec, "doc:b", latest, reads, set(), P._latest_read_is_full)
    assert not P.read_left_it_out(rec, "doc:a", latest, reads, {"doc:a"},
                                  P._latest_read_is_full)
    assert not P.read_left_it_out({"date_checked": "06/10/2026"}, "doc:a", latest, reads,
                                  set(), P._latest_read_is_full)


# ------------------------------------------------------- bars that could not fail (round 1)

def test_an_excuse_has_a_ceiling(bundle, tmp_path):
    """Repair round 1 (parity verifier, probe S4): a lot-page 404 storm hid 74 of SA's 113
    lots and SA read '39/39 100.0%' -- excused lots leave the denominator. More than 3% of
    a state's obvious lots (and more than 10) hidden for an allowed reason fails."""
    dead = [F.lot(600 + n, estate="Parity Gone %d" % (n // 4), doc="doc-gone-%d" % (n // 4),
                  lot_page_gone="05/10/2026", lot_page_status="gone") for n in range(20)]
    code, out, body = _check(bundle, tmp_path, F.clean_catalogue() + dead)
    assert code == 1, out
    texts = [f["text"] for f in body["failures"]]
    assert any("20 of 100 obvious lots are hidden for an allowed reason" in t
               and "lot_page_gone 20" in t for t in texts), texts
    row = {r["state"]: r for r in body["rows"]}["VIC"]
    assert row["excused_lots"] == 20 and row["lots"] == [80, 80]
    assert " excused " in out and "20/100" in out, out


def test_a_stale_database_or_a_state_with_nothing_obvious_fails(bundle, tmp_path):
    """Probe S1: today six nights after the newest read, no history -- 0 obvious lots, every
    state 0/0, PASS. A bar over nothing is not a pass."""
    code, out, body = _check(bundle, tmp_path, F.clean_catalogue(), "--today", "12/10/2026")
    assert code == 1, out
    cover = [f for f in body["failures"] if f["bar"] == "coverage"]
    assert any("newest read in the database is 06/10/2026" in f["text"] for f in cover), cover
    assert {f["group"] for f in cover} >= {"database", "VIC", "NSW", "QLD", "SA", "WA",
                                           "E-Agent"}, cover


def test_a_search_that_sends_every_lot_to_confirm_fails(bundle, tmp_path):
    """Probe S2: SPB_SNAPSHOT_FRESH_DAYS=-1 sends every lot to Needs confirming -- which
    Clare and Agent 2 never read -- and SA, WA and NSW still passed their lot bars."""
    code, out, body = _check(bundle, tmp_path, F.clean_catalogue(),
                             env={"SPB_SNAPSHOT_FRESH_DAYS": "-1"})
    assert code == 1, out
    on_list = [f for f in body["failures"] if "on the shortlist" in f["text"]]
    assert {f["group"] for f in on_list} == {"VIC", "NSW", "QLD", "SA", "WA"}, body["failures"]
    assert any("0 of 4 obvious lots are on the shortlist" in f["text"] for f in on_list)


def test_copies_of_one_lot_on_the_page_fail_the_duplicates_bar(bundle, tmp_path):
    """Parity verifier: about 2% of a state's cards were a second copy of a lot already
    shown, and nothing counted them. Eleven lots each carded twice -- the same builder's
    lot, price and suburb on two lists, too thinly described for the search to prove them
    one -- is over the bar."""
    copies = []
    for n in range(11):
        a = F.lot(700 + n, estate="Copy Rise A", doc="doc-copy-a", lot_number=str(7000 + n),
                  price=800000 + n * 1000, builder_name="Copy Homes", land_sqm=None,
                  bedrooms=None, suburb="Tarneit", postcode="3029")
        b = dict(F.lot(750 + n, estate="Copy Rise B", doc="doc-copy-b",
                       lot_number=str(7000 + n), price=800000 + n * 1000,
                       builder_name="Copy Homes", land_sqm=None, bedrooms=None,
                       suburb="Tarneit", postcode="3029"))
        copies += [a, b]
    code, out, body = _check(bundle, tmp_path, F.clean_catalogue() + copies)
    assert code == 1, out
    dup = [f for f in body["failures"] if f["bar"] == "duplicates"]
    assert len(dup) == 1 and dup[0]["group"] == "VIC", body["failures"]
    assert "11 of 102 shown cards" in dup[0]["text"], dup[0]["text"]


def test_a_nightly_run_needs_tonights_manifest():
    """Without one the READ bar said nothing on every run it was never fed."""
    failures, summary, _notes = P.read_bar(None, [], TODAY, required=True)
    assert len(failures) == 1 and "harvest_manifest.json" in failures[0]["text"]
    stale = {"schema": 1, "generated": "01/10/2026 21:14", "entries": [_lane_entry()]}
    failures, _s, _n = P.read_bar(stale, [], TODAY, required=True)
    assert any("written 01/10/2026, not tonight" in f["text"] for f in failures), failures
    # A manual run only says so.
    assert P.read_bar(None, [], TODAY)[0] == []
    assert P.read_bar(stale, [], TODAY)[0] == []


def test_a_link_gone_from_the_page_is_named():
    """A link that left E-Agent's page has no entry tonight at all; looping over tonight's
    entries never saw it go."""
    history = [{"night": "04/10/2026", "states": {},
                "manifest": {"E-Agent:stocklist_key:feedfacefeedface": 120},
                "manifest_labels": {"E-Agent:stocklist_key:feedfacefeedface":
                                    "E-Agent - house-and-land-vic - Gone Homes"}}]
    failures, summary, _notes = P.read_bar({"entries": [_lane_entry()]}, history, TODAY)
    assert len(failures) == 1 and "Gone Homes" in failures[0]["text"], failures
    assert summary["vanished"] == 1


def test_a_shown_count_that_falls_under_its_median_fails_the_drift_bar():
    """Repair round 1: the drift bar measured the obvious set only, so a search that
    showed far fewer of a steady obvious set could not trip it."""
    past = [{"night": d, "states": {"VIC": {"lots": 100, "buildings": 10, "shown": 100,
                                            "channels": {}, "docs": {}}}} for d in EARLIER]
    tonight = {"VIC": {"drift": {"lots": 100, "buildings": 10, "shown": 60, "channels": {},
                                 "docs": {}, "doc_labels": {}}}}
    failures, _cells, _notes = P.drift_bar(tonight, past, TODAY)
    assert len(failures) == 1 and "shown 60 against a 7-night median of 100" in \
        failures[0]["text"], failures


def test_a_state_mostly_shown_off_unread_lists_fails():
    served = {"shown": 100, "over": 0, "unjudged": 0, "ids": [], "docs": {}, "unread": 30,
              "unread_docs": {"E-Agent - Gone Folder": 30}}
    failures, _cells, notes = P.over_served_bar({"QLD": {"over_served": served}})
    assert len(failures) == 1 and "30 of 100 shown lots (30.0%) sit on a list not read" in \
        failures[0]["text"], failures
    served = dict(served, unread=5)
    failures, _cells, notes = P.over_served_bar({"QLD": {"over_served": served}})
    assert failures == [] and any("5 of 100" in n for n in notes)


def test_a_proxima_project_is_its_own_document():
    """Every Proxima row carries the portal's index page as its source URL, so all 5,231 live
    Proxima rows shared one source_doc on 6 Oct. Judged as one document, a lot in a project
    nobody re-read looked dropped whenever any other project was read."""
    snap = {"source_doc": "539808aa85affd55"}
    assert P.doc_key({"source_channel": "Proxima", "source_project_id": "814"}, snap) == \
        "proxima:814"
    assert P.doc_key({"source_channel": "Proxima", "source_project_id": "802"}, snap) == \
        "proxima:802"
    assert P.doc_key({"source_channel": "E-Agent"}, {"source_doc": "abc"}) == "doc:abc"


def test_a_proxima_project_read_to_its_render_cap_is_not_judged():
    """THE MAYBELLE read 80 lots on 06/10 against 141 available: its other lots were never
    reached, not left out. Unjudged, and said so -- the READ bar names the cut read."""
    rows = [{"content_hash": "v2:%d" % n, "source_doc": "p814", "date_checked": "06/10/2026",
             "source_channel": "Proxima"} for n in range(3)]
    old = {"content_hash": "v2:old", "source_doc": "p814", "date_checked": "27/09/2026",
           "source_channel": "Proxima", "id": 5}
    catalogue = SR.Catalogue(rows + [old], [dict(r, id=i) for i, r in enumerate(rows)] + [old])
    docs = {r["content_hash"]: "doc:p814" for r in rows + [old]}
    latest = {"doc:p814": SR._day(TODAY)}
    today = SR._day(TODAY)
    judged = P._over_served([{"content_hash": "v2:old"}], catalogue, docs, latest, today)
    assert judged["over"] == 1 and judged["ids"] == [5]
    cut = P._over_served([{"content_hash": "v2:old"}], catalogue, docs, latest, today,
                         cut_short={"doc:p814"})
    assert cut["over"] == 0 and cut["unjudged"] == 1
    _f, _cells, notes = P.over_served_bar({"VIC": {"over_served": cut}})
    assert any("not judged" in n for n in notes), notes


# ------------------------------------------------------------------------ DRIFT

def test_a_night_that_falls_15_percent_under_its_median_fails_naming_what_fell(bundle,
                                                                               tmp_path):
    history = [_night(d, vic_lots=200, buildings=25,
                      docs={"doc:doc-parity-rise-0": 128})
               for d in EARLIER]
    code, out, body = _check(bundle, tmp_path, F.clean_catalogue(), history=history)
    assert code == 1, out
    drift = [f for f in body["failures"] if f["bar"] == "drift"]
    assert len(drift) == 1 and drift[0]["group"] == "VIC", body["failures"]
    text = drift[0]["text"]
    assert "lots 80 against a 7-night median of 200 (-60%)" in text, text
    assert "buildings 10 against a 7-night median of 25" in text, text
    assert "E-Agent 200 -> 80" in text, text
    assert "E-Agent - Parity Homes - Parity Rise 0 Price List.pdf 128 -> 8" in text, text


def test_a_steady_night_passes_and_nightly_keeps_the_history(bundle, tmp_path):
    history = [_night(d) for d in EARLIER]
    code, out, body = _check(bundle, tmp_path, F.clean_catalogue(), "--nightly",
                             history=history, manifest=_tonights_manifest())
    assert code == 0, out
    assert {r["state"]: r["drift"] for r in body["rows"]}["VIC"] == "+0%"
    saved = json.loads((tmp_path / "parity_history.json").read_text(encoding="utf-8"))
    nights = [n["night"] for n in saved["nights"]]
    assert nights == list(EARLIER) + [TODAY], nights
    assert saved["nights"][-1]["states"]["VIC"]["lots"] == 80
    # --nightly publishes the report beside the snapshot, for tonight's deploy.
    published = tmp_path / "site" / "parity_report.json"
    assert json.loads(published.read_text(encoding="utf-8"))["verdict"] == "PASS"
    assert not (tmp_path / "_run_degraded").exists()


def test_the_history_keeps_two_weeks_and_one_entry_a_night(tmp_path):
    path = tmp_path / "h.json"
    history = [{"night": "%02d/09/2026" % d, "states": {}} for d in range(1, 21)]
    P.save_history(path, history, {"night": "20/09/2026", "states": {"VIC": {"lots": 1}}})
    nights = json.loads(path.read_text(encoding="utf-8"))["nights"]
    assert len(nights) == P.HISTORY_KEEP
    assert nights[-1] == {"night": "20/09/2026", "states": {"VIC": {"lots": 1}}}
    assert [n["night"] for n in nights].count("20/09/2026") == 1


def test_a_failing_nightly_run_marks_the_night_degraded(bundle, tmp_path):
    code, out, _body = _check(bundle, tmp_path, F.clean_catalogue(), "--nightly",
                              env={"SPB_MAX_CANDIDATES": "50"})
    assert code == 1, out
    marker = (tmp_path / "_run_degraded").read_text(encoding="utf-8")
    assert marker.startswith("7/7  Source-to-search parity:"), marker
    assert "50 of 80 obvious lots shown" in marker


# ------------------------------------------------------------------------- READ

def _entry(**kw):
    out = {"url": "https://www.e-agent.com.au/_files/ugd/069fe0_0123456789abcdef0123456789abcdef.pdf",
           "page_state": "VIC", "heading": "Parity Rise", "outcome": "read", "printed": 40,
           "stored": 40, "stated_counter": None}
    out.update(kw)
    return out


def test_every_read_rule_fails_on_its_own():
    cases = {
        "have no outcome": [_entry(outcome="")],
        "stored under 98% of the lots they printed": [_entry(printed=100, stored=97)],
        "ends at exactly 80": [_entry(url="https://agents.proxima.example/p/814", printed=80,
                                      stored=80, heading="THE MAYBELLE")],
        "read 70 of its 141 available": [_entry(url="https://agents.proxima.example/p/815",
                                                printed=70, stored=70, stated_counter=141,
                                                heading="The Grande")],
    }
    for words, entries in cases.items():
        failures, _summary, _notes = P.read_bar({"entries": entries}, [], TODAY)
        assert len(failures) == 1 and words in failures[0]["text"], (words, failures)
    # A clean manifest passes, and its non-price-list links are named, not failed.
    failures, summary, notes = P.read_bar(
        {"entries": [_entry(), _entry(url="https://x.example/a.pdf", heading="Brochure",
                                      outcome="not a price list", printed=0, stored=0)]},
        [], TODAY)
    assert failures == [] and summary["links"] == 2
    assert any("Brochure" in n and "not a price list" in n for n in notes), notes


def _lane_entry(**kw):
    """One entry as the harvest lane's harvest_manifest.py writes it (schema 1): no link,
    stored as what record_building did with the lots."""
    out = {"id": 1, "channel": "E-Agent", "kind": "stock link", "name": "Parity Rise",
           "page": "house-and-land-vic", "state": "VIC", "heading": "Parity Homes",
           "label": "Price List", "stocklist_key": "0123456789abcdef", "outcome": "read",
           "printed": 40, "stored": {"new": 2, "updated": 3, "unchanged": 35, "rejected": 0},
           "pieces": [{"name": "Parity Rise Price List.pdf", "outcome": "read",
                       "printed": 40, "why": ""}]}
    out.update(kw)
    return out


def test_the_harvest_lanes_manifest_is_read_in_its_own_terms():
    """data/harvest_manifest.json as par-harvest writes it (7 Oct 2026): `stored` is a
    breakdown, a Proxima project states `stated` and its own `short`, a portal its cards."""
    project = {"id": 2, "channel": "Proxima", "kind": "project", "pid": "814",
               "name": "THE MAYBELLE", "on_index": True, "via": "accordion",
               "outcome": "read"}
    cases = {
        "stored 95 of 100 printed, 5 refused by a constraint": [_lane_entry(
            printed=100, stored={"new": 0, "updated": 0, "unchanged": 95, "rejected": 5})],
        "THE MAYBELLE ends at exactly 80 of its 141 available": [dict(
            project, printed=80, stored={"new": 0, "updated": 0, "unchanged": 80,
                                         "rejected": 0}, stated=141)],
        "read 70 of its 141 available": [dict(project, printed=70, stored={
            "new": 0, "updated": 0, "unchanged": 70, "rejected": 0},
            header_counter=[141, 300])],
        "the harvest marked its read short": [dict(project, printed=50, stored={
            "new": 0, "updated": 0, "unchanged": 50, "rejected": 0}, short=True)],
        "read 30 of 40 cards": [dict(_lane_entry(), kind="portal", channel="Direct Builder "
                                     "Portal (live)", name="Parity Homes", cards_found=40,
                                     cards_read=30, stocklist_key=None)],
    }
    for words, entries in cases.items():
        failures, _summary, _notes = P.read_bar({"schema": 1, "entries": entries}, [], TODAY)
        assert len(failures) == 1 and words in failures[0]["text"], (words, failures)
    # Read whole, and a project whose own counter says exactly 80: no failure.
    whole = [_lane_entry(), dict(project, printed=80, stated=80, stored={
        "new": 0, "updated": 0, "unchanged": 80, "rejected": 0})]
    failures, summary, _notes = P.read_bar({"schema": 1, "entries": whole}, [], TODAY)
    assert failures == [] and summary["manifest"] == {
        "E-Agent:stocklist_key:0123456789abcdef": 40, "Proxima:pid:814": 80}, failures
    # Named for a person, never by a link.
    assert P._manifest_label(_lane_entry()) == \
        "E-Agent - house-and-land-vic - Parity Homes - Parity Rise - Price List"
    # The week before, it stored 40; tonight nothing: named.
    gone = _lane_entry(outcome="zero-yield", printed=0, stored={
        "new": 0, "updated": 0, "unchanged": 0, "rejected": 0})
    history = [{"night": "03/10/2026", "states": {},
                "manifest": {"E-Agent:stocklist_key:0123456789abcdef": 40}}]
    failures, _s, notes = P.read_bar({"entries": [gone]}, history, TODAY)
    assert len(failures) == 1 and "0 tonight (read empty, or no longer on the source's " \
        "page): E-Agent - house-and-land-vic" in failures[0]["text"], failures
    assert any("zero-yield" in n for n in notes), notes


def test_a_document_that_yielded_last_week_and_nothing_tonight_fails():
    entry = _entry(stored=0, printed=0, outcome="zero yield")
    key = P._manifest_key(entry)
    history = [{"night": "02/10/2026", "states": {}, "manifest": {key: 25}}]
    failures, _s, _n = P.read_bar({"entries": [entry]}, history, TODAY)
    assert len(failures) == 1 and "yielded lots in the last week and 0 tonight" in \
        failures[0]["text"], failures
    assert P.read_bar({"entries": [entry]}, [], TODAY)[0] == []


def test_without_a_manifest_the_read_bars_are_skipped_and_say_so():
    failures, summary, notes = P.read_bar(None, [], TODAY)
    assert failures == [] and summary == {"skipped": True}
    assert "skipped" in notes[0]


def test_a_bad_manifest_fails_the_run(bundle, tmp_path):
    code, out, body = _check(bundle, tmp_path, F.clean_catalogue(),
                             manifest={"entries": [_entry(printed=100, stored=50)]})
    assert code == 1, out
    assert [f["bar"] for f in body["failures"]] == ["read"], body["failures"]


# ----------------------------------------------------------------------- paging

def _cards(prefix, n):
    return [{"content_hash": "%s%03d" % (prefix, i), "lot_address": "Lot %d" % i,
             "advertised_package_price": 500000 + i} for i in range(n)]


def _server(shortlist, confirming, page=None, honours_offset=True, extra=None,
            names_next=True, by_page=False):
    """A research endpoint: unpaged when page is None, else pages of `page` cards with
    offset/limit and has_more (and next_offset when names_next), as the plan's rank 1
    describes -- one offset over both lists. by_page: page/page_size instead."""
    posted = []

    def post(body):
        posted.append(body)
        resp = {"status": "success", "shortlist_count": len(shortlist),
                "needs_confirming_count": len(confirming)}
        if page is None:
            resp.update(shortlist=list(shortlist), needs_confirming=list(confirming))
        elif by_page:
            number = int(body.get("page") or 1)
            off = (number - 1) * page
            resp.update(shortlist=shortlist[off:off + page],
                        needs_confirming=confirming[off:off + page],
                        page=number, page_size=page,
                        total_pages=-(-max(len(shortlist), len(confirming)) // page))
        else:
            off = int(body.get("offset") or 0) if honours_offset else 0
            size = min(page, int(body.get("limit") or page))
            resp.update(shortlist=shortlist[off:off + size],
                        needs_confirming=confirming[off:off + size],
                        offset=off, limit=size,
                        has_more=off + size < max(len(shortlist), len(confirming)))
            if names_next:
                resp["next_offset"] = off + size
        resp.update(extra or {})
        return 200, json.loads(json.dumps(resp)), 0.01
    return post, posted


def test_an_unpaged_response_is_taken_whole_in_one_call():
    post, posted = _server(_cards("s", 30), _cards("c", 5))
    got = P.fetch_every_page(post, {"client_brief": {"state": "VIC"}, "explain": True})
    assert len(posted) == 1 and not got["paged"] and got["short"] == []
    assert len(got["shortlist"]) == 30 and len(got["needs_confirming"]) == 5


def test_a_paged_response_is_fetched_to_its_last_page():
    post, posted = _server(_cards("s", 250), _cards("c", 120), page=100)
    got = P.fetch_every_page(post, {"client_brief": {"state": "VIC"}, "explain": True})
    assert got["paged"] and got["pages"] == 3 and got["short"] == []
    assert len(got["shortlist"]) == 250 and len(got["needs_confirming"]) == 120
    assert [b.get("offset") for b in posted] == [None, 100, 200]
    assert all(b["client_brief"] == {"state": "VIC"} and b["explain"] is True for b in posted)


def test_one_offset_over_both_lists_walks_the_longer_list_to_its_end():
    """QLD on 6 Oct: 366 shortlisted, 176 to confirm. With pages of 100, no next_offset
    named and one offset for both lists, the needs-confirming list must still be walked."""
    post, posted = _server(_cards("s", 3), _cards("c", 176), page=100, names_next=False)
    got = P.fetch_every_page(post, {"client_brief": {"state": "QLD"}, "explain": True})
    assert got["short"] == [] and len(got["needs_confirming"]) == 176, got["short"]
    assert [b.get("offset") for b in posted] == [None, 100]


def test_a_paged_response_by_page_number_is_followed():
    post, posted = _server(_cards("s", 250), [], page=100, by_page=True)
    got = P.fetch_every_page(post, {"client_brief": {"state": "VIC"}, "explain": True})
    assert got["short"] == [] and len(got["shortlist"]) == 250
    assert [b.get("page") for b in posted] == [None, 2, 3]


def test_a_server_that_stops_paging_short_is_named():
    """It answers every page with the first page: the loop stops, and says the shortlist
    handed over 100 of the 250 it counted -- a SHOWN failure."""
    post, posted = _server(_cards("s", 250), [], page=100, honours_offset=False)
    got = P.fetch_every_page(post, {"client_brief": {"state": "VIC"}, "explain": True})
    assert len(posted) == 2 and len(got["shortlist"]) == 100
    assert got["short"] == ["shortlist: 250 counted, 100 handed over across 2 page(s)"]
    result = {"searched": True, "short": got["short"], "page_bytes": [], "error": "",
              "lots": {"all": [10, 10], "e-agent": [10, 10]},
              "buildings": {"all": [1, 1], "e-agent": [1, 1]}, "hidden": []}
    failures, _rows, _notes = P.shown_bar({"VIC": result})
    assert len(failures) == 1 and "does not hand over every lot" in failures[0]["text"]


def test_an_unpaged_list_cut_at_its_cap_is_a_note_and_its_lots_fail_by_name():
    post, posted = _server(_cards("s", 3), _cards("c", 50),
                           extra={"needs_confirming_count": 176})
    got = P.fetch_every_page(post, {"client_brief": {}, "explain": True})
    assert got["short"] == ["needs_confirming: 176 counted, 50 handed over and the response "
                            "offers no next page"]
    # Asked once for the rest, in the terms a paging server reads; this one answered the
    # same cards, so the walk stopped there.
    assert len(posted) == 2 and posted[1]["needs_confirming_offset"] == 50 \
        and posted[1]["offset"] == 3, posted
    result = {"searched": True, "short": got["short"], "page_bytes": [], "error": "",
              "lots": {"all": [10, 10], "e-agent": [10, 10]},
              "buildings": {"all": [1, 1], "e-agent": [1, 1]},
              "hidden": [{"id": 19473, "reason": "needs_confirming_unlisted",
                          "allowed": False, "e_agent": True}]}
    failures, _rows, notes = P.shown_bar({"QLD": result})
    assert [f.get("reason") for f in failures] == ["needs_confirming_unlisted"], failures
    assert "ids 19473" in failures[0]["text"]
    assert any("176 counted" in n for n in notes)


def _lane_server(shortlist, confirming, page_max=500, unpaged_full=None, unpaged_nc=50):
    """The search lane's /api/research contract (api/research.py on par-search, 7 Oct 2026):
    a caller that sends no paging key gets the whole shortlist (cut to unpaged_full when
    given, as its RESPONSE_BYTES_MAX would cut it) and the first 50 to confirm; offset/limit
    and needs_confirming_offset/needs_confirming_limit (each clamped to page_max) page both
    lists and answer "page" and "needs_confirming_page" objects."""
    posted = []
    keys = ("offset", "limit", "needs_confirming_offset", "needs_confirming_limit", "building")

    def info(offset, limit, returned, total):
        nxt = offset + returned
        return {"offset": offset, "limit": limit, "returned": returned, "total": total,
                "next_offset": nxt if nxt < total else None}

    def post(body):
        posted.append(body)
        explain = body.get("explain") is True
        resp = {"status": "success", "shortlist_count": len(shortlist)}
        if explain:
            resp["needs_confirming_count"] = len(confirming)
        if not any(body.get(k) is not None for k in keys):
            resp["shortlist"] = list(shortlist[:unpaged_full] if unpaged_full else shortlist)
            if explain:
                resp["needs_confirming"] = list(confirming[:unpaged_nc])
        else:
            off = body.get("offset") or 0
            lim = min(100 if body.get("limit") is None else body["limit"], page_max)
            resp["shortlist"] = shortlist[off:off + lim]
            resp["page"] = dict(info(off, lim, len(resp["shortlist"]), len(shortlist)),
                                building=None)
            if explain:
                noff = body.get("needs_confirming_offset") or 0
                nlim = min(unpaged_nc if body.get("needs_confirming_limit") is None
                           else body["needs_confirming_limit"], page_max)
                resp["needs_confirming"] = confirming[noff:noff + nlim]
                resp["needs_confirming_page"] = info(noff, nlim, len(resp["needs_confirming"]),
                                                     len(confirming))
        return 200, json.loads(json.dumps(resp)), 0.01
    return post, posted


def test_the_search_lanes_server_whole_shortlist_and_the_rest_to_confirm_in_one_more_call():
    """VIC on the search lane's bundle, 6 Oct data: 3,089 shortlisted, handed over whole
    unpaged; 152 to confirm, 50 unpaged. One more call fetches the other 102 -- asking for
    the shortlist from past its end, so that page is empty."""
    post, posted = _lane_server(_cards("s", 1200), _cards("c", 152))
    got = P.fetch_every_page(post, {"client_brief": {"state": "VIC"}, "explain": True})
    assert got["short"] == [] and got["paged"], got["short"]
    assert len(got["shortlist"]) == 1200 and len(got["needs_confirming"]) == 152
    assert len(posted) == 2, posted
    assert "offset" not in posted[0] and "limit" not in posted[0]      # Clare's request
    assert posted[1]["offset"] == 1200 and posted[1]["needs_confirming_offset"] == 50
    assert posted[1]["explain"] is True
    # More to confirm than one page holds: the needs-confirming page is followed on.
    post, posted = _lane_server(_cards("s", 1200), _cards("c", 700))
    got = P.fetch_every_page(post, {"client_brief": {"state": "VIC"}, "explain": True})
    assert got["short"] == [] and len(got["needs_confirming"]) == 700, got["short"]
    assert [b.get("needs_confirming_offset") for b in posted] == [None, 50, 550], posted


def test_the_search_lanes_server_cut_unpaged_is_walked_page_by_page_to_its_end():
    """An unpaged answer cut from the bottom (over its byte budget): the rest is paged at
    the most the server allows, and once nothing is left to confirm the explanation -- half
    a page's cost -- is no longer asked for."""
    post, posted = _lane_server(_cards("s", 1200), _cards("c", 10), unpaged_full=600)
    got = P.fetch_every_page(post, {"client_brief": {"state": "VIC"}, "explain": True})
    assert got["short"] == [] and len(got["shortlist"]) == 1200, got["short"]
    assert [b.get("offset") for b in posted] == [None, 600, 1100], posted
    assert [b.get("explain") for b in posted] == [True, True, None], posted
    # A page that asks for nothing answers next_offset == offset: never followed.
    assert P._onward({"offset": 0, "limit": 0, "returned": 0, "total": 5,
                      "next_offset": 0}) is None


def test_the_search_lanes_server_that_will_not_page_past_a_cut_is_short():
    """The same cut answer from a server that ignores the paging keys: one call more, the
    same cards, and the shortlist named short -- "N results" that shows fewer."""
    post, posted = _server(_cards("s", 600), [], extra={"shortlist_count": 1200})
    got = P.fetch_every_page(post, {"client_brief": {"state": "VIC"}, "explain": True})
    assert len(posted) == 2
    assert got["short"] == ["shortlist: 1200 counted, 600 handed over and the response "
                            "offers no next page"]


def test_a_page_over_the_response_limit_fails_the_shown_bar():
    """An uncapped, unpaged VIC response was 14.9 MB on the 6 Oct data: Vercel's 4.5 MB
    ceiling means the live search fails outright, however many lots it 'shows' here."""
    result = {"searched": True, "short": [], "page_bytes": [14_937_945], "error": "",
              "lots": {"all": [10, 10], "e-agent": [10, 10]},
              "buildings": {"all": [1, 1], "e-agent": [1, 1]}, "hidden": []}
    failures, _rows, _notes = P.shown_bar({"VIC": result})
    assert len(failures) == 1 and "14.9 MB is over Vercel's 4.5 MB" in failures[0]["text"]


# --------------------------------------------------------------------- plumbing

def test_nothing_printed_or_published_carries_a_link_or_a_keyed_file_name(bundle, tmp_path):
    """E-Agent's file names are the unguessable half of their links, and a Dropbox folder
    link hands over the whole folder: neither may reach the log or the Settings tab."""
    keyed = "https://www.e-agent.com.au/_files/ugd/069fe0_0123456789abcdef0123456789abcdef.pdf"
    folder = "https://www.dropbox.com/scl/fo/abcdef123456/h?rlkey=zzz&dl=0"
    rows = F.clean_catalogue() + _over_served()
    for row in rows:
        if row["source_doc"] == "doc-parity-rise-0":
            row.update(source_url=keyed, stocklist_file=folder)
    code, out, body = _check(bundle, tmp_path, rows, "--nightly")
    assert code == 1, out
    published = (tmp_path / "site" / "parity_report.json").read_text(encoding="utf-8")
    for text in (out, json.dumps(body), published):
        for leak in ("http", "069fe0_", "dropbox", "rlkey", "0123456789abcdef"):
            assert leak not in text, (leak, text[:3000])
    assert P._file_name("https://x.example/Rise%200%20Price%20List.pdf") == "Rise 0 Price List.pdf"
    assert P._file_name(keyed) == ""


def test_the_database_and_the_bundle_are_left_as_they_were(bundle, tmp_path):
    snapshot, db = F.write(tmp_path, F.clean_catalogue())
    before = (SR._tree(bundle), db.read_bytes(), snapshot.read_bytes())
    code, out, _body = _run_check(bundle, tmp_path, snapshot, db, "--nightly",
                                  manifest=_tonights_manifest())
    assert code == 0, out
    assert (SR._tree(bundle), db.read_bytes(), snapshot.read_bytes()) == before


def test_the_measurement_runs_with_no_credential_vault(tmp_path, monkeypatch):
    """The bundle's agent asks the OS keyring for portal logins as it starts; a search needs
    none, Vercel has none, and on the owner's laptop it is a read of his vault for nothing."""
    snapshot, db = F.write(tmp_path, F.clean_catalogue())
    bundle = tmp_path / "site" / "api"
    bundle.mkdir()
    (bundle / "research.py").write_text("", encoding="utf-8")
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw.get("env") or {})
        return subprocess.CompletedProcess(cmd, 1, "", "")
    monkeypatch.setattr(P.subprocess, "run", fake_run)
    args = P.parser().parse_args(["--snapshot", str(snapshot), "--db", str(db),
                                  "--report", str(tmp_path / "r.json")])
    args.states = list(P.SEARCHED_STATES)
    assert P.run(args, out=io.StringIO()) == 1
    assert seen["PYTHON_KEYRING_BACKEND"] == "keyring.backends.null.Keyring"
    assert seen["SPB_DATABASE_PATH"] != str(db)          # a read-only copy, never the file


def test_the_dedupe_replay_uses_the_bundles_own_dedupe_and_names_the_same_lot():
    """The search lane keys its dedupe on the lot (_candidates.dedupe_packages, 7 Oct 2026):
    the replay must run the bundle's dedupe, not origin/main's engine, and of two kept cards
    on one key name the one with the same lot number."""
    def pkg(h, lot):
        return {"content_hash": h, "lot_address": "Parity Tower, Tarneit VIC 3029",
                "house_design": "", "advertised_package_price": 700000.0, "lot_number": lot}
    packages = [pkg("v2:a", "2302"), pkg("v2:b", "1712"), pkg("v2:c", "1712")]

    class Bundle:
        calls = []

        @staticmethod
        def build_packages(brief, rows, prepared=None):
            return list(packages), {}

        @staticmethod
        def dedupe_packages(found, rows):
            Bundle.calls.append(rows)
            return [p for p in found if p["content_hash"] in ("v2:a", "v2:b")]
    dropped = P._dedupe_twins(Bundle, {}, ["rows"], None)
    assert Bundle.calls == [["rows"]]
    assert dropped == {"v2:c": "v2:b"}, dropped


def test_a_setup_failure_or_a_usage_error_exits_1_never_what_run_daily_reads_otherwise(
        tmp_path, bundle):
    import run_daily
    assert run_daily.DEGRADED_EXIT not in (0, 1)

    def run(*args):
        return subprocess.run([sys.executable, "-X", "utf8", str(APP / "parity_check.py"),
                               *map(str, args)], cwd=str(APP), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=120)
    done = run("--snapshot", tmp_path / "nowhere" / "stock.json")
    assert done.returncode == 1 and "no snapshot at" in done.stdout
    snapshot, db = F.write(tmp_path, F.clean_catalogue())
    done = run("--snapshot", snapshot, "--db", db)            # no bundle beside it
    assert done.returncode == 1 and "no research bundle at" in done.stdout
    assert run("--states", "ACT", "--snapshot", snapshot).returncode == 1
    assert run("--no-such-flag").returncode == 1


def test_run_daily_runs_it_last_and_optional_after_the_replay():
    done = subprocess.run([sys.executable, "-X", "utf8", str(APP / "run_daily.py"),
                           "--dry-run", "--no-deploy"], cwd=str(APP), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stderr
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    replay = next(i for i, line in enumerate(lines) if "Replay Colin's searches" in line)
    parity = next(i for i, line in enumerate(lines) if "Source-to-search parity" in line)
    assert parity == replay + 1, lines
    assert lines[parity].endswith("python parity_check.py --snapshot vercel_site/stock.json "
                                  "--nightly  (optional)"), lines[parity]


def test_a_failed_parity_step_marks_the_night_degraded_and_the_alert_carries_its_table(
        tmp_path, monkeypatch):
    import time

    import run_daily
    (tmp_path / "data").mkdir()
    (tmp_path / "parity_check.py").write_text(
        "import json, sys\n"
        "json.dump({'verdict': 'FAIL', 'table': 'VIC  306/2,738 11.2%'},\n"
        "          open('data/parity_report.json', 'w'))\n"
        "sys.exit(1)\n", encoding="utf-8")
    monkeypatch.setattr(run_daily, "APP", tmp_path)
    monkeypatch.setattr(run_daily, "_RUN_STARTED", [time.time() - 1])
    run_daily._DEGRADED.clear()
    run_daily._PARTIAL.clear()
    try:
        label = "7/7  Source-to-search parity"
        assert run_daily._run(label, ["parity_check.py", "--snapshot",
                                      "vercel_site/stock.json", "--nightly"], True) is True
        assert run_daily._DEGRADED == [label] and run_daily._PARTIAL == []
        table = run_daily._parity_table()
        assert "Source-to-search parity:" in table and "VIC  306/2,738 11.2%" in table
        # Last night's report is not tonight's: a step that died before writing leaves it.
        monkeypatch.setattr(run_daily, "_RUN_STARTED", [time.time() + 60])
        assert run_daily._parity_table() == ""
    finally:
        run_daily._DEGRADED.clear()
        run_daily._PARTIAL.clear()
    assert "_parity_table()" in Path(run_daily.__file__).read_text(encoding="utf-8")


def test_the_build_publishes_the_last_report_with_no_cache(tmp_path, monkeypatch):
    import inspect

    import build_web
    out = tmp_path / "site"
    out.mkdir()
    monkeypatch.setattr(build_web, "__file__", str(tmp_path / "build_web.py"))
    assert build_web._publish_parity_report(out) is False          # none yet: no report
    # ...but a file all the same (repair round 1, 7 Oct 2026): a build with no report --
    # deploy.ps1 on a laptop that never ran the check -- published nothing, Vercel answered
    # the Settings tab's fetch with a 404, and the browser logged it as a console error.
    # An empty report is a 200 the panel reads as "no panel".
    placeholder = json.loads((out / "parity_report.json").read_text(encoding="utf-8"))
    assert placeholder["rows"] == [] and not placeholder.get("failures"), placeholder
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "parity_report.json").write_text(
        json.dumps({"verdict": "PASS", "rows": [{"state": "VIC"}]}), encoding="utf-8")
    assert build_web._publish_parity_report(out) is True
    assert json.loads((out / "parity_report.json").read_text(encoding="utf-8"))["rows"]
    src = inspect.getsource(build_web)
    assert '"parity_report.json")]' in src, "parity_report.json has no Cache-Control"
    assert 'meta["parity_report"] = _publish_parity_report(out_dir)' in src
    assert '"benchmarks.json", "parity_report.json"):' in src, \
        "the folder-link guard does not read the published report"


def test_the_runner_keeps_the_history_between_nights():
    """Without it the drift bar would say "no earlier night" on every run: the runner is
    thrown away each morning and state_sync carries only the database and the sessions."""
    import yaml
    jobs = yaml.safe_load((APP / ".github" / "workflows" / "daily-harvest.yml").read_text(
        encoding="utf-8"))["jobs"]["harvest"]["steps"]
    names = [s.get("name") for s in jobs]
    restore = jobs[names.index("Restore the parity history")]
    save = jobs[names.index("Keep the parity history for tomorrow")]
    assert names.index("Restore the parity history") < names.index("Harvest") \
        < names.index("Keep the parity history for tomorrow") < names.index("Publish to Vercel")
    assert restore["uses"].startswith("actions/cache/restore@")
    assert save["uses"].startswith("actions/cache/save@")
    assert restore["with"]["path"] == save["with"]["path"] == "data/parity_history.json"
    assert restore["with"]["restore-keys"] == "parity-history-"
    assert save["with"]["key"].startswith("parity-history-")
    # A cache hiccup must never cost the night's publish, which runs only on success().
    assert restore["continue-on-error"] is True and save["continue-on-error"] is True
    assert "always()" in save["if"]


# --------------------------------------------------------------- the Settings panel

NODE = __import__("shutil").which("node")


def _panel(index=None):
    env = dict(os.environ)
    env.pop("PP_INDEX", None)
    if index is not None:
        env["PP_INDEX"] = str(index)
    return subprocess.run(["node", str(TESTS / "parity_panel_checks.mjs")], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env, timeout=120)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_settings_panel_passes_its_checks():
    done = _panel()
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]


PANEL_MUTANTS = {
    "the tab never loads the report": (
        "        await loadParity();\n", "", "loadSettings loads the report"),
    "the tab never draws the panel": (
        "          ${parityPanel(PARITY)}\n", "", "loadSettings draws the panel"),
    "a failure's text is not escaped": (
        "${esc(f.group)}: ${esc(f.text)}", "${esc(f.group)}: ${f.text}",
        "a failure's text is escaped"),
    "no report draws an all-clear": (
        "if (!p || !Array.isArray(p.rows) || !p.rows.length) return '';",
        "if (!p || !Array.isArray(p.rows) || !p.rows.length) p = {verdict: 'PASS', rows: [{state: '-'}], failures: []};",
        "no report: no panel"),
}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("name", sorted(PANEL_MUTANTS))
def test_the_panel_checks_see_each_fix_reverted(name, tmp_path):
    old, new, expect = PANEL_MUTANTS[name]
    page = (APP / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert page.count(old) == 1, (name, page.count(old))
    mutant = tmp_path / "index.html"
    mutant.write_text(page.replace(old, new), encoding="utf-8")
    done = _panel(mutant)
    assert done.returncode == 1, (name, done.stdout[-3000:])
    assert ("FAIL  " + expect) in done.stdout, (name, done.stdout[-3000:])
