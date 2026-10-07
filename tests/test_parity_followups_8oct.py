"""The first live night of search parity (8 Oct 2026), and the four things it found.

Run 37652011840 published PR #27 and its parity step went red; the live replay of Colin's
searches failed once. Each section below is one finding, fixed at its root, with the test
that fails on origin/main (1bbed94) and passes now:

  A. RELAXATION HINTS NOT COMPUTED ON LIVE. #12 Werribee and #13 Redbank Plains priced no
     relaxation: the 4 s budget was counted from the start of the explanation, and the
     needs-confirming passes before it cost 4-5 s once every candidate was scored. The
     budget now starts with the relaxations, a whole-request deadline bounds them, every
     pass of a request shares a GateCache, and a relaxation that leaves the pool alone
     re-scores the search's own pool -- with the same counts as before. Repair round: the
     budget and minimum steps are cut from one WidePool pass, every step is estimated by
     its size before it starts (scoring in chunks), and the deadline is 18 s, not 22.
  B. ACT, TAS AND NT WERE NOT SEARCHABLE BY STATE. 207 obvious ACT lots, none reachable by
     a state search; the form, the parity table and the replay briefs now cover them.
  C. ONE LOT WRONGLY MERGED. Docklands townhomes T20 (id 3578) and T21 (id 3579), both read
     with lot number "0" off their level column, both $3,655,000: T21 hidden behind T20.
     A lot "number" of only zeros names no lot.
  D. PROXIMA READ BARS. 13 alarms, none a lot the reader missed: 4 "stored under 98%" were
     lots cross-listed under a second project and stored once; 7 "not read to the end"
     were projects read WHOLE against counters from 9 Sep - 1 Oct; 2 were SMDC's
     Philippine towers. The manifest now says each of those things, and the bar still
     fails a real short read -- repair round: including a whole read far below an old
     counter (harvest_manifest.old_counter_gap_ok bounds what an old counter excuses).
"""
import contextlib
import io
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for _p in (str(APP / "api"), str(APP), str(TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _candidates as C  # noqa: E402

SUBSET = APP / "tests" / "fixtures" / "colin_17sep_subset.json"
FORM = {
    "client_name": "Parity follow-ups", "budget_min": 0, "budget_max": 750000,
    "preferred_spending_cap": 750000, "buyer_type": "", "state": "QLD",
    "primary_suburbs": [], "bedrooms_min": 0, "bathrooms_min": 0, "car_spaces_min": 0,
    "storeys_max": 3, "land_size_min_sqm": 0, "house_size_min_sqm": 0,
    "search_radius_km": 0, "product_type": [], "requires_incentive": False,
}


class _Sep16(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 16, 12, 0, tzinfo=tz)


@pytest.fixture(scope="module")
def research():
    mp = pytest.MonkeyPatch()
    mp.setenv("SPB_SNAPSHOT_JSON", str(SUBSET))
    mp.delenv("GROQ_API_KEY", raising=False)
    mp.delenv("SPB_SHORTLIST_MAX", raising=False)
    mp.delenv("SPB_MAX_CANDIDATES", raising=False)
    mp.setattr(C, "datetime", _Sep16)
    mp.setattr(C, "EXCEPTIONS_LIST_MODE", "confirm")
    mp.setattr(C, "MAX_CANDIDATES", 0)
    import research as module
    mp.setattr(module, "EXPLAIN_BELOW", 5)
    yield module
    mp.undo()


def _brief(**more):
    brief = json.loads(json.dumps(FORM))
    brief.update(more)
    return brief


def _run(research, brief, **payload):
    with contextlib.redirect_stdout(io.StringIO()):
        return research.run_research(dict(payload, client_brief=brief))


def _rel(out):
    return {r["id"]: r for r in out["search_help"]["relaxations"]}


def _snapshot(research):
    rows, path = C.load_snapshot()
    return rows, C.prepared_for_snapshot(rows, path)


# ======================================================================= A. relaxations

def test_a_the_relaxation_budget_starts_when_the_relaxations_start(research, monkeypatch):
    """The live shape, made small: the needs-confirming passes take longer than the whole
    relaxation budget. Before the fix every re-run came back not_computed (the budget was
    already spent when the first one was due); now each is priced exactly."""
    monkeypatch.setattr(research, "EXPLAIN_BUDGET_MS", 3000)
    real = research._needs_confirming

    def slow(*args, **kwargs):
        out = real(*args, **kwargs)
        time.sleep(3.5)                     # the 4-5 s the live passes took, scaled
        return out

    monkeypatch.setattr(research, "_needs_confirming", slow)
    rel = _rel(_run(research, _brief(), explain=True))
    reruns = [r for r in rel.values() if r["kind"] == "rerun"]
    assert reruns, "premise: the thin search offers re-runs"
    assert all(r["count_status"] == "exact" for r in reruns), reruns


def test_a_past_the_request_deadline_nothing_more_starts(research, monkeypatch):
    """A deadline of 0 ms: the search and its needs-confirming list still come back whole,
    no relaxation is started and each says why, and the refusal pass (hidden counts only)
    is skipped and said to be possibly short."""
    plain = _run(research, _brief(), explain=True)
    monkeypatch.setattr(research, "REQUEST_DEADLINE_MS", 0)
    out = _run(research, _brief(), explain=True)
    assert out["status"] == "success"
    assert out["shortlist_count"] == plain["shortlist_count"]
    assert out["needs_confirming_count"] == plain["needs_confirming_count"]
    rel = _rel(out)
    assert rel["include_needs_confirming"]["count_status"] == "exact"
    reruns = [r for r in rel.values() if r["kind"] == "rerun"]
    assert reruns and all(r["count_status"] == "not_computed" and r["skipped"] == "deadline"
                          for r in reruns), reruns
    help_nc = out["search_help"]["needs_confirming"]
    if help_nc.get("refusal_pass_skipped"):
        assert help_nc["hidden_may_be_short"] is True
    assert out["search_help"]["deadline_ms"] == 0


def test_a_outside_a_request_there_is_no_deadline(research):
    assert research._past_deadline(10 ** 6) is False
    assert research._gate_cache() is None


def _old_count(research, agent, brief, rows, prepared):
    """The count exactly as origin/main priced it: the full gate pass, the dedupe, EVERY
    package scored, then the verified-or-approved final pool."""
    from schema import VerificationStatus
    packages, counts = C.build_packages(brief, rows, prepared=prepared)
    processed, _ = research._score_packages(agent, brief, packages, rows)
    final = [c for c in processed
             if c.verification_status == VerificationStatus.VERIFIED or c.consultant_approved]
    return len(final), bool(counts.get("truncated"))


PATCHES = [
    {}, {"budget_max": 800000, "preferred_spending_cap": 800000},
    {"state": "QLD", "primary_suburbs": [], "search_radius_km": 0},
    {"primary_suburbs": ["Walloon"]}, {"primary_suburbs": ["Walloon"], "search_radius_km": 20},
    {"primary_suburbs": ["Walloon"], "search_radius_km": 50}, {"bedrooms_min": 4},
    {"bedrooms_min": 3, "bathrooms_min": 2}, {"product_type": ["Townhouse"]},
    {"state": "", "budget_max": 650000}, {"budget_min": 500000},
]


@pytest.mark.parametrize("patch", PATCHES, ids=lambda p: json.dumps(p, sort_keys=True))
def test_a_the_cheaper_count_is_the_old_count(research, patch):
    """Scoring only what can be shortlisted, and re-using a pool by pool_key, gives the
    number the full re-run gave -- with no request context, and inside one whose pools were
    filled by an earlier brief of the same key (the search's own)."""
    import _bootstrap
    agent = _bootstrap.get_agent()
    rows, prepared = _snapshot(research)
    brief = research._normalised_brief(_brief(**patch))[0]
    want = _old_count(research, agent, brief, rows, prepared)
    assert research._shortlist_count_for(agent, brief, rows, prepared) == want
    token = research._REQUEST.set(research._Request(time.monotonic()))
    try:
        research._REQUEST.get().gate_cache = C.GateCache(rows, prepared)
        base = research._normalised_brief(_brief())[0]
        research._shortlist_count_for(agent, base, rows, prepared)       # fills a pool
        assert research._shortlist_count_for(agent, brief, rows, prepared) == want
        assert research._shortlist_count_for(agent, brief, rows, prepared) == want
    finally:
        research._REQUEST.reset(token)


def test_a_pool_key_names_what_the_gates_read(research):
    base = _brief(primary_suburbs=["Walloon"])
    same = [dict(base, state="QLD", primary_suburbs=[], search_radius_km=0),
            dict(base, search_radius_km=20), dict(base, search_radius_km=50)]
    differ = [dict(base, budget_max=800000), dict(base, bedrooms_min=4),
              dict(base, product_type=["Townhouse"]), dict(base, state="NSW"),
              dict(base, budget_min=1), dict(base, requires_incentive=True),
              dict(base, storeys_max=1)]
    key = C.pool_key(base)
    assert all(C.pool_key(b) == key for b in same)
    assert all(C.pool_key(b) != key for b in differ)
    assert C.pool_key(base, admit=C.ADMITTABLE_GATES) != key


def test_a_with_the_emergency_cap_the_suburbs_are_part_of_the_pool(research, monkeypatch):
    """SPB_MAX_CANDIDATES orders its cut by the brief's suburbs, radius and minimums, so with
    it set those are read and the pool cannot be shared."""
    monkeypatch.setattr(C, "MAX_CANDIDATES", 50)
    base = _brief(primary_suburbs=["Walloon"])
    assert C.pool_key(dict(base, primary_suburbs=[])) != C.pool_key(base)
    assert C.pool_key(dict(base, search_radius_km=20)) != C.pool_key(base)


@pytest.mark.parametrize("more", [{}, {"state": ""}, {"bedrooms_min": 4},
                                  {"state": "ACT", "budget_max": 9007199254740991}])
def test_a_the_gate_cache_changes_no_package_and_no_count(research, more):
    """The cached stages are the ones _gate ran inline: every pass of a request -- the
    search, the admit pass and a second search on the same cache -- gives exactly what it
    gives without one."""
    rows, prepared = _snapshot(research)
    brief = research._normalised_brief(_brief(**more))[0]
    cache = C.GateCache(rows, prepared)
    for admit in ((), C.ADMITTABLE_GATES):
        kw = dict(admit=admit, only_admitted=bool(admit), prepared=prepared)
        want = C.build_packages(brief, rows, **kw)
        assert C.build_packages(brief, rows, gate_cache=cache, **kw) == want
        assert C.build_packages(brief, rows, gate_cache=cache, **kw) == want
    assert cache.states and cache.places, "premise: the cache was filled and then read"


def test_a_the_gate_cache_serves_only_the_snapshots_own_rows(research):
    """A row built during the request (the refusal pass's stand-ins) is never served from
    the cache, and neither is another prepared's -- each is computed as without one."""
    rows, prepared = _snapshot(research)
    cache = C.GateCache(rows, prepared)
    copy = dict(rows[0])
    assert cache.state_stage(copy, prepared) is None
    assert cache.state_stage(rows[0], C.prepare_rows(rows)) is None
    assert cache.state_stage(rows[0], prepared) == cache.state_stage(rows[0], prepared)


def test_a_whole_state_and_radius_are_priced_and_re_post_to_their_counts(research):
    """#13's shape on the 17 Sep subset: a suburb, a 4-bed minimum, a thin answer. Every
    location relaxation (served from the search's own pool) is exact, and posting its patch
    back returns exactly that count."""
    brief = _brief(primary_suburbs=["Walloon"], bedrooms_min=4)
    out = _run(research, brief, explain=True)
    rel = _rel(out)
    assert out["shortlist_count"] < 5, "premise: a thin search"
    for rid in ("whole_state", "radius_20", "radius_50", "drop_bedrooms_min",
                "budget_plus_50k", "budget_plus_10pct"):
        assert rel[rid]["count_status"] == "exact", rel[rid]
        again = _run(research, dict(brief, **rel[rid]["brief_patch"]))
        assert again["shortlist_count"] == rel[rid]["shortlist_count"], rid
    assert rel["whole_state"]["shortlist_count"] > out["shortlist_count"]


# --- A, repair round (8 Oct 2026): one wide pass for the budget and minimum steps, and every
# step of a relaxation estimated by its size before it starts (callers verifier: an Any-state
# search with five minimums took 20-25 s on the 2x-Vercel model, and "No minimums at all"
# started inside the 22 s deadline and ended 2.3 s past it).

MANY_MINIMUMS = dict(primary_suburbs=["Walloon"], bedrooms_min=4, bathrooms_min=2,
                     car_spaces_min=1, house_size_min_sqm=150)


def _offers(research, brief, rows, prepared):
    _packages, counts = C.build_packages(brief, rows, prepared=prepared)
    return research._relaxation_offers(brief, counts, 0)


@pytest.mark.parametrize("more", [
    MANY_MINIMUMS, dict(MANY_MINIMUMS, state="", budget_max=650000),
    dict(bedrooms_min=3, bathrooms_min=2, budget_max=9007199254740991,
         preferred_spending_cap=9007199254740991),
    dict(state="ACT", bedrooms_min=2, bathrooms_min=1, budget_max=600000),
    dict(MANY_MINIMUMS, budget_min=400000, product_type=["House & Land"]),
], ids=["qld_walloon", "any_state", "no_budget", "act", "floor_and_product"])
def test_a_the_wide_pool_gives_each_covered_brief_build_packages_own_packages(research,
                                                                              more):
    """WidePool(loosest).packages_for(brief) IS build_packages(brief)'s packages -- the same
    dicts in the same order -- for every budget and minimum step it covers, and the dedupe
    after it keeps the same ones. The steps it does not cover are left to the full path."""
    rows, prepared = _snapshot(research)
    base = research._normalised_brief(_brief(**more))[0]
    offers = _offers(research, base, rows, prepared)
    wide_brief = research._wide_brief(base, offers)
    wide = C.WidePool(wide_brief, rows, prepared=prepared,
                      gate_cache=C.GateCache(rows, prepared))
    covered = 0
    for offer in offers:
        if offer["kind"] != "rerun":
            continue
        brief = dict(base, **offer["brief_patch"])
        if not wide.serves(brief):
            assert offer["id"] in ("drop_product_type", "drop_incentive",
                                   "drop_price_floor"), offer["id"]
            continue
        covered += 1
        want, _counts = C.build_packages(brief, rows, prepared=prepared)
        got = wide.packages_for(brief)
        assert got == want, offer["id"]
        assert C.dedupe_packages(got, rows) == C.dedupe_packages(want, rows), offer["id"]
    assert covered >= 3, "premise: the budget and minimum steps are covered"


def test_a_the_wide_pool_covers_only_a_higher_ceiling_and_fewer_binding_facts(research,
                                                                               monkeypatch):
    base = _brief(bedrooms_min=4, bathrooms_min=2)
    wide = dict(base, budget_max=825000, bedrooms_min=0)
    assert C.pool_covers(wide, base)
    assert C.pool_covers(wide, dict(base, budget_max=800000))
    assert C.pool_covers(wide, dict(base, bedrooms_min=0))
    for differs in (dict(base, budget_max=900000), dict(base, bathrooms_min=0),
                    dict(base, state="NSW"), dict(base, product_type=["Townhouse"]),
                    dict(base, requires_incentive=True), dict(base, budget_min=1),
                    dict(base, storeys_max=1), dict(base, budget_max=0)):
        assert not C.pool_covers(wide, differs), differs
    assert not C.pool_covers(wide, base, admit=C.ADMITTABLE_GATES)
    monkeypatch.setattr(C, "MAX_CANDIDATES", 50)
    assert not C.pool_covers(wide, base)


@pytest.mark.parametrize("chunk", [500, 3])
def test_a_every_relaxation_count_re_posts_to_itself(research, monkeypatch, chunk):
    """The counts an explained search prices -- cut from the wide pool, scored in chunks --
    are what posting each patch back over the form returns (the self-check the live
    replay makes), for a five-minimum search in QLD and in every state."""
    monkeypatch.setattr(research, "SCORE_CHUNK", chunk, raising=False)
    for more in (MANY_MINIMUMS, dict(MANY_MINIMUMS, state="", budget_max=650000)):
        brief = _brief(**more)
        out = _run(research, brief, explain=True)
        priced = [r for r in out["search_help"]["relaxations"] if r["kind"] == "rerun"]
        assert len(priced) >= 6 and all(r["count_status"] == "exact" for r in priced), priced
        for r in priced:
            again = _run(research, dict(brief, **r["brief_patch"]))
            assert again["shortlist_count"] == r["shortlist_count"], r["id"]


class _Clock:
    """research's clock, moved only by what a test says a step costs."""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t


def _minimum(brief, name):
    return (brief.get(name) if isinstance(brief, dict) else getattr(brief, name, 0)) or 0


@pytest.fixture
def clock(research, monkeypatch):
    import types
    import _bootstrap
    c = _Clock()
    monkeypatch.setattr(research, "time", types.SimpleNamespace(monotonic=c.monotonic))
    agent = _bootstrap.get_agent()
    real = agent.score_candidates
    # Scoring costs 10 ms a package -- and 400 ms for the brief with no minimums at all, the
    # widest pool and the last relaxation priced, as on the live Any-state search.
    c.per_package = lambda brief: (0.4 if not (_minimum(brief, "bedrooms_min")
                                               or _minimum(brief, "bathrooms_min"))
                                   else 0.01)

    def costly(brief, packages, search_area=None):
        c.t += c.per_package(brief) * len(packages)
        return real(brief, packages, search_area=search_area)

    monkeypatch.setattr(agent, "score_candidates", costly)
    monkeypatch.setattr(research, "_RATE_SAMPLE", 1, raising=False)
    monkeypatch.setattr(research, "SCORE_CHUNK", 5, raising=False)
    monkeypatch.setattr(research, "EXPLAIN_BUDGET_MS", 10 ** 9)
    return c


def test_a_a_last_relaxation_slower_than_every_earlier_one_stops_at_the_deadline(
        research, clock, monkeypatch):
    """THE VERIFIER'S CASE, on a clock only scoring moves. "No minimums at all" is priced
    last and costs far more than any relaxation before it. With the deadline a third of the
    way into it, it is stopped by the deadline -- not_computed, `skipped` "deadline" --
    within one chunk of scoring of the deadline, and every relaxation before it is priced
    exactly as without a deadline. Before: it started (the slowest relaxation so far was
    cheap) and ran its whole length past the deadline. An Any-state search with five
    minimums, as the verifier's adv3_any_suburbs_5min."""
    brief = _brief(**dict(MANY_MINIMUMS, state="", budget_max=650000))
    monkeypatch.setattr(research, "REQUEST_DEADLINE_MS", 10 ** 12)
    start = clock.t
    free = _run(research, brief, explain=True)
    rel = free["search_help"]["relaxations"]
    assert free["shortlist_count"] < 5 and rel[-1]["id"] == "drop_all_minimums", rel
    assert all(r["count_status"] == "exact" for r in rel), rel
    last_ms = rel[-1]["ms"]
    assert last_ms > 5 * max(r.get("ms") or 0 for r in rel[:-1]), \
        "premise: the last relaxation is much the slowest"
    last_started_ms = (clock.t - start) * 1000 - last_ms

    deadline_ms = int(last_started_ms + last_ms / 3)
    monkeypatch.setattr(research, "REQUEST_DEADLINE_MS", deadline_ms)
    start = clock.t
    out = _run(research, brief, explain=True)
    took_ms = (clock.t - start) * 1000
    got = out["search_help"]["relaxations"]
    assert [(r["id"], r["count_status"], r.get("shortlist_count")) for r in got[:-1]] == \
        [(r["id"], r["count_status"], r.get("shortlist_count")) for r in rel[:-1]]
    assert (got[-1]["count_status"], got[-1].get("skipped")) == ("not_computed", "deadline")
    one_chunk_ms = 1000 * 0.4 * research.SCORE_CHUNK
    assert took_ms <= deadline_ms + one_chunk_ms, (took_ms, deadline_ms)


def test_a_the_refusal_pass_is_estimated_as_the_gate_pass_it_is(research, clock,
                                                                 monkeypatch):
    """The refusal pass (hidden tallies only) is one more gate pass. It no longer starts
    when twice the search's own pass would end past the deadline -- before, it started
    whenever the deadline had not yet passed."""
    real_build = C.build_packages
    pass_s = 2.0

    def slow_build(*args, **kwargs):
        clock.t += pass_s
        return real_build(*args, **kwargs)

    calls = []
    real_refused = research._refused_matches

    def counted(*args, **kwargs):
        calls.append(1)
        return real_refused(*args, **kwargs)

    monkeypatch.setattr(research._candidates, "build_packages", slow_build)
    monkeypatch.setattr(research, "_refused_matches", counted)
    clock.per_package = lambda brief: 0.0
    brief = _brief()
    monkeypatch.setattr(research, "REQUEST_DEADLINE_MS", 10 ** 12)
    free = _run(research, brief, explain=True)
    assert calls, "premise: this search runs the refusal pass"
    assert not free["search_help"]["needs_confirming"].get("refusal_pass_skipped")
    # The search's pass and the admit pass: 2 x 2 s. The refusal pass would start at 4 s.
    monkeypatch.setattr(research, "REQUEST_DEADLINE_MS", 6000)
    calls.clear()
    out = _run(research, brief, explain=True)
    assert not calls
    help_nc = out["search_help"]["needs_confirming"]
    assert help_nc["refusal_pass_skipped"] and help_nc["hidden_may_be_short"] is True
    assert out["needs_confirming_count"] == free["needs_confirming_count"]


# ======================================================================= B. ACT, TAS, NT

def test_b_the_form_offers_act_tas_and_nt():
    html = (APP / "index.html").read_text(encoding="utf-8")
    select = re.search(r'<select id="stateSelect">(.*?)</select>', html, re.S).group(1)
    values = re.findall(r'<option\b[^>]*\bvalue="([^"]*)"', select)
    assert values == ["", "QLD", "NSW", "VIC", "SA", "WA", "ACT", "TAS", "NT"], values
    # Repair round (callers verifier): the closed select showed "ACT (Australian Capit". No
    # new label is longer than the longest the form already showed whole.
    labels = dict(re.findall(r'<option\b[^>]*\bvalue="([^"]*)"[^>]*>([^<]*)</option>', select))
    widest = max(len(labels[s]) for s in ("QLD", "NSW", "VIC", "SA", "WA"))
    assert all(len(labels[s]) <= widest for s in ("ACT", "TAS", "NT")), labels


def test_b_an_act_search_finds_the_act_lots(research):
    """The 17 Sep subset holds 234 ACT rows (Proxima: The Grande, Ahlei). A state search for
    ACT reaches them, and the normaliser reads the territory written out."""
    out = _run(research, _brief(state="ACT", budget_max=9007199254740991,
                                preferred_spending_cap=9007199254740991))
    assert out["status"] == "success" and out["shortlist_count"] > 0
    assert {e.get("state") for e in out["shortlist"]} == {"ACT"}
    named = _run(research, _brief(state="", primary_suburbs=["Australian Capital Territory"],
                                  budget_max=9007199254740991,
                                  preferred_spending_cap=9007199254740991))
    assert named["brief_used"]["state"] == "ACT"
    assert named["shortlist_count"] == out["shortlist_count"]


def test_b_parity_and_the_replay_search_every_state_the_form_offers():
    import parity_check as P
    import search_regressions as SR
    html = (APP / "index.html").read_text(encoding="utf-8")
    select = re.search(r'<select id="stateSelect">(.*?)</select>', html, re.S).group(1)
    offered = [v for v in re.findall(r'<option\b[^>]*\bvalue="([^"]*)"', select) if v]
    assert sorted(P.SEARCHED_STATES) == sorted(offered)
    briefs = {b["id"]: b for b in SR.load_spec()["briefs"]}
    for state in offered:
        b = briefs["%s_any" % state.lower()]
        assert b["client_brief"] == {"state": state}
        assert b["expect"]["obvious_rows"] is True and b["expect"]["funnel"] is True
        assert b["expect"]["brief_used"]["state"] == state


# ======================================================================= C. lot "0"

def _townhome(content_hash, line, beds=4, **kw):
    """One of the Docklands tower's ground-floor townhomes as stored on 8 Oct: the lot
    number is the LEVEL column's 0, the card is "Lot 0", the price $3,655,000."""
    row = {"content_hash": content_hash, "builder_name": "", "lot_address": "Lot 0",
           "street_address": None, "lot_number": "0", "suburb": "Docklands", "state": "VIC",
           "availability_status": "Available", "state_source": "e-agent page",
           "price": 3655000.0, "land_price": None, "build_price": None, "bedrooms": beds,
           "bathrooms": 3, "car_spaces": 2, "land_sqm": None, "house_sqm": None,
           "storey": None, "title_status": None, "estate_name": None,
           "incentive_amount": None, "incentive_text": None, "product_type": "Apartment",
           "source_channel": "E-Agent", "attribution_scope": "project",
           "date_checked": "15/09/2026", "listing_url": None, "floorplan_url": None,
           "brochure_url": None, "source_project_id": None, "superseded_by": None,
           "suburb_source": "stated by the source", "source_doc": "doc-docklands",
           "price_kind": "package", "text_price_max": 3655000.0, "price_in_text": True,
           "email_list": None, "email_first_seen": None, "line_key": line,
           "line_says_gone": False, "lot_page_gone": "", "lot_page_status": ""}
    row.update(kw)
    return row


VIC_ANY = dict(FORM, state="VIC", budget_max=9007199254740991,
               preferred_spending_cap=9007199254740991)
# ids 3578 and 3579: "0 T20 4 Bed 3.5 Bath + Study + 2 Car T20 West ... 223 60 283
# $3,655,000" and "0 T21 4 Bed 3.5 Bath + Study + 2 Car T21 West ... 222 60 282 $3,655,000".
T20 = _townhome("v2:4b67bd11-t20", "k-0-t20-west-223-60-283")
T21 = _townhome("v2:4440217f-t21", "k-0-t21-west-222-60-282")


def _kept(rows, today=datetime(2026, 9, 16, 12, 0)):
    packages, counts = C.build_packages(dict(VIC_ANY), rows, today=today)
    return sorted(p["content_hash"] for p in C.dedupe_packages(packages, rows)), counts


def test_c_a_lot_number_of_zero_names_no_lot():
    for zero in ("0", "00", "000", "Lot 0", " 0 "):
        assert C.lot_identity({"lot_number": zero}) == "", zero
    assert C.lot_identity({"lot_address": "Lot 0, Car Spaces: 2"}) == ""
    assert C.lot_identity({"lot_number": "10"}) == "lot:10"
    assert C.lot_identity({"lot_number": "02"}) == "lot:02"
    assert C.lot_identity({"lot_address": "Lot 100, Gatton"}) == "lot:100"


def test_c_the_two_docklands_townhomes_are_two_lots():
    """Before: one card -- the same-listing collapse keyed both on ('lot', 'lot:0'), and the
    parity check found id 3579 hidden behind a DIFFERENT lot that is shown."""
    kept, counts = _kept([T20, T21])
    assert kept == sorted([T20["content_hash"], T21["content_hash"]]), counts
    assert counts["same_listing_collapsed"] == 0


def test_c_the_same_townhome_line_read_twice_is_still_one_card():
    """A true copy -- one line of one list read twice -- still collapses, lot "0" or not."""
    again = dict(T21, content_hash="v2:t21-again")
    kept, counts = _kept([T21, again])
    assert len(kept) == 1 and counts["same_listing_collapsed"] == 1


def test_c_a_real_lot_number_on_two_channels_is_still_one_card():
    eagent = _townhome("v2:lot-2302-eagent", "k-2302", lot_number="2302",
                       lot_address="Lot 2302, Docklands", estate_name="Atlas")
    proxima = _townhome("v2:lot-2302-proxima", "p-2302", lot_number="2302",
                        lot_address="Lot 2302, Docklands", estate_name="Atlas",
                        source_channel="Proxima", source_doc="proxima-761",
                        source_project_id="761")
    assert len(_kept([eagent, proxima])[0]) == 1


def test_c_needs_confirming_never_reads_one_zero_lot_as_a_copy_of_another(research):
    """research._same_listing compares a needs-confirming candidate with every card shown:
    the same "lot" in the same state with the same bedrooms and suburb was a copy. With
    lot "0" that hid T21 behind T20 under Needs confirming as well."""
    class _Price:
        advertised_package_price = 3655000.0

    class _Cand:
        state, suburb, lot_number, bedrooms = "VIC", "Docklands", "0", 4
        land_size_sqm = house_size_sqm = 0
        lot_address = "Lot 0"
        price_breakdown = _Price()

    x = research._card_identity(_Cand(), T21)
    y = research._card_identity(_Cand(), T20)
    assert x["lot"] == "" and y["lot"] == ""
    assert research._same_listing(x, y) is False
    assert research._same_listing(x, research._card_identity(_Cand(), dict(T21))) is True


# ======================================================================= D. Proxima

import test_proxima_capped_replay as R  # noqa: E402
from sources import proxima as px  # noqa: E402


def _harvest(page, stored=None, dates=None):
    saved = (px.PlaywrightScraper, px.PLAYWRIGHT_AVAILABLE)
    px.PlaywrightScraper = lambda **k: R._Scraper(page)
    px.PLAYWRIGHT_AVAILABLE = True
    try:
        src = px.ProximaSource()
        src.stored_counters = stored or {}
        src.stored_counter_dates = dates or {}
        return src, src.search({})
    finally:
        px.PlaywrightScraper, px.PLAYWRIGHT_AVAILABLE = saved


def _log(src, pid):
    return next(e for e in src.project_log if e["pid"] == pid)


def test_d_a_project_read_whole_below_an_old_counter_is_not_short():
    """The CARLING Carlingford (762): off the index on 8 Oct, its stored counter 79 from
    27 Sep, its list 72 lots -- under the render cap, so the portal's own list ended there.
    Before: named short, and the parity READ bar failed on it."""
    server = R._Server({"761": R._inventory("761", 3), "762": R._inventory("762", 72)})
    page = R._Page(server, index=["761"], dropdown=[("762", "The CARLING Carlingford")])
    src, rows = _harvest(page, stored={"762": 79}, dates={"762": "27/09/2026"})
    assert len(R._by_project(rows)["762"]) == 72
    assert not any(s["pid"] == "762" for s in src.short_projects), src.short_projects
    entry = _log(src, "762")
    assert (entry["whole"], entry["stated_source"], entry["stated_on"],
            entry["stale_counter"], entry["short"]) == (
        "uncapped", "stored", "27/09/2026", True, False), entry


def test_d_an_old_counter_excuses_only_the_gaps_lots_leave_a_list_by():
    """The bound, on the seven whole reads of 8 Oct (all excused) and on reads it must not
    excuse: a narrowed list, an empty one, a gap past 3 lots and 15%."""
    import harvest_manifest as HM
    for read, stated in ((2, 3), (8, 11), (27, 28), (138, 140), (72, 79), (108, 117)):
        assert HM.old_counter_gap_ok(read, stated), (read, stated)
    assert HM.old_counter_gap_ok(68, 79)                 # 11 = int(15% of 79)
    for read, stated in ((2, 79), (0, 40), (0, 3), (67, 79), (99, 117), (5, 10)):
        assert not HM.old_counter_gap_ok(read, stated), (read, stated)
    assert not HM.old_counter_gap_ok(None, 3) and not HM.old_counter_gap_ok(3, None)


def test_d_a_whole_read_far_below_an_old_counter_is_still_short():
    """MUTATION (repair round): The CARLING's 79 from 27 Sep, and tonight a list of 2 that
    ends under the render cap -- "whole" by the cap alone. Before the repair it was a stale
    counter; it is a short read, named, as it was before 8 Oct."""
    server = R._Server({"761": R._inventory("761", 3), "762": R._inventory("762", 2)})
    page = R._Page(server, index=["761"], dropdown=[("762", "The CARLING Carlingford")])
    src, _rows = _harvest(page, stored={"762": 79}, dates={"762": "27/09/2026"})
    assert any(s["pid"] == "762" for s in src.short_projects), src.short_projects
    entry = _log(src, "762")
    assert (entry["whole"], entry["stale_counter"], entry["short"]) == (
        "uncapped", False, True), entry


def test_d_a_stored_counter_carries_the_night_it_was_stated(tmp_path):
    import sqlite3
    from database import ResearchDatabase
    path = tmp_path / "px.db"
    ResearchDatabase(path)
    con = sqlite3.connect(str(path))
    for i, (pid, avail, when) in enumerate([("762", 79, "2026-09-27T11:51:07"),
                                            ("762", 52, "2026-08-26T11:00:00"),
                                            ("762", None, "2026-10-08T03:30:00")]):
        con.execute("INSERT INTO buildings (source_channel, source_project_id, "
                    "project_available, checked_at, dedup_key, content_hash) "
                    "VALUES ('Proxima', ?, ?, ?, ?, ?)", (pid, avail, when, f"k{i}", f"h{i}"))
    con.commit()
    con.close()
    assert px.stored_project_counter_details(path) == {"762": (79, "27/09/2026")}
    assert px.stored_project_counters(path) == {"762": 79}


def test_d_tonights_own_header_counter_still_makes_a_read_short():
    """MUTATION: the same 72 lots under tonight's header "(79/119)": the portal says 79 are
    available NOW, so 72 is short whatever else is true."""
    server = R._Server({"762": R._inventory("762", 72)})
    page = R._Page(server, index=["762"], dropdown=[],
                   headers={"762": "The CARLING Carlingford (79/119)"})
    src, _rows = _harvest(page)
    assert any(s["pid"] == "762" for s in src.short_projects)
    entry = _log(src, "762")
    assert entry["stated_source"] == "header" and entry["short"] is True


def test_d_a_capped_read_not_proven_whole_is_still_short_of_an_old_counter():
    """MUTATION: THE MAYBELLE's shape -- the portal ignores the sort and the body has no
    price ceiling, so 80 of 141 is all that can be read: still short, stored counter or not."""
    server = R._Server({"761": R._inventory("761", 3), "720": R._inventory("720", 141)},
                       honour_sort=False)
    page = R._Page(server, index=["761"], dropdown=[("720", "THE MAYBELLE")])
    old = R.BODY
    R.BODY = R.BODY.replace("&property_range_filter%5Brop%5D%5Brange_two%5D=26000000", "")
    try:
        src, _rows = _harvest(page, stored={"720": 141}, dates={"720": "26/09/2026"})
    finally:
        R.BODY = old
    entry = _log(src, "720")
    assert entry["whole"] == "" and entry["short"] is True
    assert any(s["pid"] == "720" for s in src.short_projects)


def test_d_a_topped_up_project_whose_bands_met_is_whole():
    """The Grande (529): 80 tiles, topped up by both price sorts and a band to 108, against
    the 117 its counter stated on 27 Sep. Whole: not short."""
    server = R._Server({"761": R._inventory("761", 3), "529": R._inventory("529", 108)})
    page = R._Page(server, index=["761"], dropdown=[("529", "The Grande")])
    src, rows = _harvest(page, stored={"529": 117}, dates={"529": "27/09/2026"})
    assert len(R._by_project(rows)["529"]) == 108
    entry = _log(src, "529")
    assert entry["whole"] == "price sorts met" and entry["short"] is False, entry


def test_d_a_lot_cross_listed_under_a_second_project_is_counted_where_it_went():
    """Ascenta Living: one lot printed under Coliving (785) and Traditional (776), kept once
    under Coliving. Traditional's line says where its missing lot went."""
    shared = R._tile("785", 1, 610800)
    shared["name"] = "Lot 1212 Pinnacle Unit 1212 Pinnacle, Vespa Grange, SMYTHES CREEK, VIC, 3351"
    coliving = [shared] + R._inventory("785", 4, start=500000)
    traditional = [dict(shared)] + R._inventory("776", 5, start=700000)
    server = R._Server({"785": coliving, "776": traditional})
    page = R._Page(server, index=["785", "776"], dropdown=[])
    src, rows = _harvest(page)
    assert len(rows) == 10
    trad, coli = _log(src, "776"), _log(src, "785")
    assert (trad["printed"], trad["cross_listed"], trad["cross_listed_into"]) == (
        6, 1, {"785": 1}), trad
    assert coli["cross_listed"] == 0


def test_d_an_overseas_project_is_marked():
    """SMDC Glade Residences ILOILO (696): Philippine addresses, no Australian state."""
    tiles = R._inventory("696", 5, start=3189000)
    for i, t in enumerate(tiles):
        t["name"] = ("Lot B0702%02d Unit B0702%02d, Circumferential Road 1, Brgy. Balabago,, "
                     "Jaro, Iloilo, 5034" % (i, i))
    server = R._Server({"761": R._inventory("761", 3), "696": tiles})
    page = R._Page(server, index=["761"], dropdown=[("696", "SMDC Glade Residences ILOILO")])
    src, _rows = _harvest(page)
    assert _log(src, "696")["overseas"] is True
    assert _log(src, "761")["overseas"] is False


# --- the READ bar over the manifest the harvest now writes

def _entry(pid, name, printed, stored, **more):
    e = {"id": int(pid), "channel": "Proxima", "kind": "project", "pid": pid, "name": name,
         "outcome": "read", "printed": printed,
         "stored": {"new": 0, "updated": 0, "unchanged": stored, "rejected": 0},
         "on_index": False, "via": "accordion", "header_counter": None, "stated": None,
         "replays": 0, "bands": 0, "short": False}
    e.update(more)
    return e


# The 13 alarms of 8 Oct 2026, as the harvest now writes them (numbers from run 37652011840
# and the database it left; ids and dates in the notes of sources/proxima.py).
LIVE_8OCT = [
    _entry("785", "Ascenta Living Coliving", 5, 5, on_index=True, header_counter=[5, 17],
           stated=5, stated_source="header", whole="uncapped"),
    _entry("776", "Ascenta Living Traditional", 80, 76, on_index=True,
           header_counter=[80, 172], stated=80, stated_source="header",
           whole="price sorts met", replays=3, bands=1, cross_listed=4,
           cross_listed_into={"785": 4}),
    _entry("161", "Atchison and Kenny Wollongong Building A", 305, 305, on_index=True,
           via="availability view", header_counter=[7, 305], stated=7,
           stated_source="header", whole="availability view"),
    _entry("793", "Atchison and Kenny Wollongong Building B", 19, 17, on_index=True,
           header_counter=[19, 160], stated=19, stated_source="header", whole="uncapped",
           cross_listed=2, cross_listed_into={"161": 2}),
    _entry("802", "Lancelin South Stage C", 25, 25, whole="uncapped"),
    _entry("767", "Lancelin South Stage C Land Only", 24, 21, whole="uncapped",
           cross_listed=3, cross_listed_into={"802": 3}),
    _entry("758", "Park Avenue II Stage 1 Rouse Hill", 4, 4, whole="uncapped"),
    _entry("689", "Park Avenue II Stage 3 Rouse Hill", 4, 3, whole="uncapped",
           cross_listed=1, cross_listed_into={"758": 1}),
    _entry("197", "Jasmine Schofields", 2, 2, stated=3, stated_source="stored",
           stated_on="01/10/2026", whole="uncapped", stale_counter=True),
    _entry("499", "Kensington Lot1 Townhouse", 8, 8, stated=11, stated_source="stored",
           stated_on="09/09/2026", whole="uncapped", stale_counter=True),
    _entry("726", "KIARA NORTH MASCOT", 27, 27, stated=28, stated_source="stored",
           stated_on="27/09/2026", whole="uncapped", stale_counter=True),
    _entry("283", "Live Caringbah", 138, 138, stated=140, stated_source="stored",
           stated_on="27/09/2026", whole="price sorts met", replays=3, bands=1,
           stale_counter=True),
    _entry("762", "The CARLING Carlingford", 72, 72, stated=79, stated_source="stored",
           stated_on="27/09/2026", whole="uncapped", stale_counter=True),
    _entry("529", "The Grande", 108, 108, stated=117, stated_source="stored",
           stated_on="27/09/2026", whole="price sorts met", replays=3, bands=1,
           stale_counter=True),
    _entry("810", "VERTEX Zetland", 27, 27, stated=28, stated_source="stored",
           stated_on="27/09/2026", whole="uncapped", stale_counter=True),
    _entry("696", "SMDC Glade Residences ILOILO", 334, 334, whole="", replays=5, bands=3,
           short=True, overseas=True),
    _entry("693", "SMDC Gold Residences", 1849, 1849, whole="", replays=24, bands=22,
           short=True, overseas=True),
]


def _read(entries):
    import parity_check as P
    return P.read_bar({"schema": 1, "generated": "08/10/2026 03:30", "entries": entries},
                      [], "08/10/2026", required=True)


def test_d_the_eighth_of_october_reads_pass_and_are_named():
    failures, summary, notes = _read([dict(e) for e in LIVE_8OCT])
    assert failures == [], failures
    said = " ".join(notes)
    for name in ("Ascenta Living Traditional", "Building B", "Land Only", "Stage 3 Rouse Hill",
                 "Jasmine Schofields", "The Grande", "VERTEX Zetland", "SMDC Gold Residences",
                 "SMDC Glade Residences ILOILO"):
        assert name in said, name
    assert (summary["proxima_old_counter"], summary["proxima_overseas_short"],
            summary["stored_elsewhere"]) == (7, 2, 4)


def _without(entries, pid, *keys, **more):
    out = []
    for e in entries:
        e = dict(e)
        if e["pid"] == pid:
            for k in keys:
                e.pop(k, None)
            e.update(more)
        out.append(e)
    return out


@pytest.mark.parametrize("pid,keys,more,why", [
    # A real short read: nothing stored the 4 elsewhere.
    ("776", ("cross_listed", "cross_listed_into"), {}, "stored 76 of 80 printed"),
    # The project it names was not read tonight: a lot cannot vanish into a name.
    ("776", (), {"cross_listed_into": {"999": 4}}, "stored 76 of 80 printed"),
    # The counter is tonight's own header.
    ("762", (), {"stated_source": "header", "header_counter": [79, 119]}, "read 72 of its 79"),
    # Not proven whole.
    ("762", ("whole",), {}, "read 72 of its 79"),
    # An older manifest that does not say where its counter came from.
    ("529", ("stated_source",), {}, "read 108 of its 117"),
    # An Australian project with SMDC's read.
    ("693", ("overseas",), {}, "SMDC Gold Residences: the harvest marked its read short"),
    # A cap signature not proven whole.
    ("726", ("whole",), {"printed": 80, "stated": None, "stated_source": None},
     "ends at exactly 80"),
    # REPAIR ROUND (correctness verifier): read whole under the cap, off the index, but far
    # below the old counter -- a narrowed list ends under the cap too. Each passed before.
    ("762", (), {"printed": 2}, "read 2 of its 79 available (read whole, but more"),
    ("762", (), {"printed": 0}, "read 0 of its 79 available"),
    ("197", (), {"printed": 0}, "read 0 of its 3 available (nothing read"),
    ("529", (), {"printed": 99}, "read 99 of its 117 available"),
    ("762", (), {"printed": 67}, "read 67 of its 79 available"),
])
def test_d_the_bar_still_fails_a_real_short_read(pid, keys, more, why):
    """MUTATIONS: take away the one fact that excuses each 8 Oct alarm and it fails again."""
    failures, _summary, _notes = _read(_without(LIVE_8OCT, pid, *keys, **more))
    assert failures and any(why in f["text"] for f in failures), failures
