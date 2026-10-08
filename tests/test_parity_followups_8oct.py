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


def _cold_then_warm(research, clock, monkeypatch, cold=3.0, warm=0.5, wide=2.0):
    """build_packages costs `cold` seconds the first time (the search's own pass, which fills
    the GateCache) and `warm` every time after; the WidePool costs `wide`; scoring nothing."""
    calls = []
    real_build = C.build_packages

    def timed_build(*args, **kwargs):
        clock.t += cold if not calls else warm
        calls.append(1)
        return real_build(*args, **kwargs)

    class SlowWide(C.WidePool):
        def __init__(self, *args, **kwargs):
            clock.t += wide
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(research._candidates, "build_packages", timed_build)
    monkeypatch.setattr(research._candidates, "WidePool", SlowWide)
    clock.per_package = lambda brief: 0.0
    return calls


def test_a_the_refusal_pass_is_estimated_as_the_warm_pass_it_is(research, clock,
                                                                monkeypatch):
    """SECOND REPAIR ROUND (callers verifier). The refusal pass reads the GateCache the
    search's own pass filled, like the admit pass just before it -- measured at 0.15-0.4
    times the cold pass. Estimated at twice the COLD pass it was skipped on a cold page load
    with time to spare, and the tally said nothing of it. A cold pass of 3 s, warm passes of
    0.5 s, 2.5 s left after the admit pass -- the two budget steps' own passes, then the
    refusal pass (it waits for the hints, see below): it runs."""
    calls = _cold_then_warm(research, clock, monkeypatch)
    refusal = []
    real_refused = research._refused_matches

    def counted(*args, **kwargs):
        refusal.append(1)
        return real_refused(*args, **kwargs)

    monkeypatch.setattr(research, "_refused_matches", counted)
    monkeypatch.setattr(research, "REQUEST_DEADLINE_MS", 6000)
    out = _run(research, _brief(), explain=True)
    assert refusal, "premise and fix: the refusal pass runs"
    assert len(calls) >= 3
    help_nc = out["search_help"]["needs_confirming"]
    assert not help_nc.get("refusal_pass_skipped"), help_nc


def test_a_the_refusal_tally_waits_for_the_hints(research, clock, monkeypatch):
    """SECOND REPAIR ROUND, from this round's 2x emulation: given the warm estimate, the
    refusal pass ran BEFORE the relaxations and took the time a budget step needed (Any
    state, one suburb, big pool: both budget steps skipped, the radius steps priced). It
    only counts what was left out, so on a thin search it now runs after the hints, when
    there is time; and when there is none the tally says it may be short. 1.7 s left after
    the admit pass: both budget steps (0.5 s each), not the refusal pass too."""
    _cold_then_warm(research, clock, monkeypatch)
    refusal = []
    real_refused = research._refused_matches

    def counted(*args, **kwargs):
        refusal.append(1)
        return real_refused(*args, **kwargs)

    monkeypatch.setattr(research, "_refused_matches", counted)
    monkeypatch.setattr(research, "REQUEST_DEADLINE_MS", 5200)
    out = _run(research, _brief(), explain=True)
    got = {r["id"]: r["count_status"] for r in out["search_help"]["relaxations"]
           if r["kind"] == "rerun"}
    assert got.get("budget_plus_50k") == "exact" and got.get("budget_plus_10pct") == "exact", \
        got
    help_nc = out["search_help"]["needs_confirming"]
    assert not refusal and help_nc["refusal_pass_skipped"] and \
        help_nc["hidden_may_be_short"] is True, help_nc
    # ... and a search with a full shortlist (no hints to price) runs it where it always did
    monkeypatch.setattr(research, "EXPLAIN_BELOW", 0)
    monkeypatch.setattr(research, "REQUEST_DEADLINE_MS", 10 ** 9)
    out = _run(research, _brief(), explain=True)
    assert refusal and not out["search_help"]["needs_confirming"].get("refusal_pass_skipped")


def test_a_with_no_time_for_the_wide_pool_the_budget_steps_are_still_priced(
        research, clock, monkeypatch):
    """SECOND REPAIR ROUND (callers verifier). The WidePool serves every budget and minimum
    step and is estimated at twice the cold pass; when that did not fit, the first budget
    step raised and both budget steps -- the first hints a consultant tries -- came back
    not_computed while cheaper, later steps were priced. Now each covered step runs its own
    warm pass, estimated from the warm passes before it: with 4 s left, the WidePool's 6 s
    does not fit and each budget step's 1 s does, and each is priced exactly."""
    calls = _cold_then_warm(research, clock, monkeypatch)
    started = {}
    real_relax = research._relaxations

    def relax(*args, **kwargs):
        started["t"] = clock.t
        return real_relax(*args, **kwargs)

    monkeypatch.setattr(research, "_relaxations", relax)
    monkeypatch.setattr(research, "REQUEST_DEADLINE_MS", 10 ** 12)
    t0 = clock.t
    free = _run(research, _brief(), explain=True)
    want = {r["id"]: r.get("shortlist_count") for r in free["search_help"]["relaxations"]
            if r["kind"] == "rerun"}
    assert {"budget_plus_50k", "budget_plus_10pct"} <= set(want), want
    before_ms = (started["t"] - t0) * 1000
    assert before_ms >= 3000, "premise: the search's own pass was the cold one"
    monkeypatch.setattr(research, "REQUEST_DEADLINE_MS", int(before_ms + 4000))
    calls.clear()                          # a new request: its own first pass is cold again
    out = _run(research, _brief(), explain=True)
    got ={r["id"]: (r["count_status"], r.get("shortlist_count"), r.get("skipped"))
           for r in out["search_help"]["relaxations"] if r["kind"] == "rerun"}
    for rid in ("budget_plus_50k", "budget_plus_10pct"):
        assert got[rid] == ("exact", want[rid], None), got


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


# --- B, second repair round: the NT search's only lot was a Victorian one

# E-Agent's pooled "3HairpinDrive" list as stored (ids 18641-18644 on 8 Oct): Tarneit lines
# with their locality written, and 18644 whose locality line went to the row beside it.
LINE_18644 = "46Archer Drive $699,000.00 201 m2 148 m2 2 $580.00 July 2026 Available"


@pytest.fixture(scope="module")
def geo():
    from geo import SuburbGeoIndex
    return SuburbGeoIndex()


def test_b_a_house_numbered_street_is_no_suburb(geo):
    """The street of a written address is not the lot's suburb, not even as a last resort.
    Before: Archer (Palmerston, NT) -- the row was filed NT."""
    import state_resolver as sr
    for text in (LINE_18644, "46 Archer Drive $699,000.00 Available"):
        assert sr.suburb_in_row_text(text, geo, "", "3HairpinDrive") is None, text
        assert sr.reading_refuses(text, "Archer", geo), text
    assert geo.find_suburb_in_text(LINE_18644, "") == "Archer"          # PREMISE
    # a LOT number before a road named for its suburb still reads the suburb
    assert sr.suburb_in_row_text("Lot 12 Point Cook Rd", geo, "", "") == "Point Cook"
    # and a locality written after the street is read as always
    assert sr.suburb_in_row_text("46 Archer Drive, Tarneit VIC 3029", geo, "", "") == "Tarneit"


def test_b_the_misfiled_victorian_lot_is_no_longer_nt(tmp_path, monkeypatch):
    """Through the real enrich on a database holding the list's lines as stored on 8 Oct:
    18644 was NT (state_source 'suburb', on Archer); it is left blank -- never another
    state origin/main would not give -- and its Victorian neighbours keep VIC. A second
    night changes nothing."""
    import sqlite3
    import config
    import enrich_buildings
    from database import ResearchDatabase
    cols = ("id", "source_channel", "lot_address", "source_text", "suburb", "state",
            "state_source", "postcode", "estate_name", "stocklist_file", "source_url",
            "attribution_scope", "price", "availability_status", "date_checked", "last_seen",
            "content_hash", "dedup_key")
    doc = "https://example.invalid/pooled-3hairpindrive.pdf"
    lines = [(18641, "Tarneit,Victoria,3029 $799,000.00 350 m2 210 m2 2 $650.00 July 2026 "
                     "Available", "VIC", "listing text", "3029"),
             (18643, "Tarneit,Victoria,3029 $766,000.00 293 m2 168 m2 2 2 $600.00 July 2026 "
                     "Available", "VIC", "listing text", "3029"),
             (18644, LINE_18644, "NT", "suburb", None)]
    db = tmp_path / "nt.db"
    ResearchDatabase(db)
    con = sqlite3.connect(str(db))
    for rid, text, state, how, pc in lines:
        con.execute("INSERT INTO buildings (%s) VALUES (%s)" % (", ".join(cols),
                                                              ", ".join("?" * len(cols))),
                    (rid, "E-Agent", text, text, None, state, how, pc, "3HairpinDrive", doc,
                     doc, "state_pooled", 699000.0, "Available", "24/09/2026", "24/09/2026",
                     "v2:nt%d" % rid, "v2:nt%d" % rid))
    con.commit()
    con.close()
    monkeypatch.setattr(config, "DATABASE_PATH", db)
    for night in (1, 2):
        with contextlib.redirect_stdout(io.StringIO()):
            assert enrich_buildings.main([]) == 0
        con = sqlite3.connect(str(db))
        got = dict(con.execute("SELECT id, state FROM buildings"))
        con.close()
        assert got == {18641: "VIC", 18643: "VIC", 18644: None}, (night, got)


def test_b_the_replay_fails_a_card_shown_in_a_state_its_suburb_is_not_in():
    """The verifier: no parity bar or replay check saw a state search answer with another
    state's lot ("Tarneit, NT"). card_invariants now does, from the bundle's gazetteer: a
    failure where the brief expects it (nt_any and the other clean state-wide briefs), a
    WARN elsewhere (the older Kings Forest, QLD / Donnybrook, SA cards), nothing live."""
    import search_regressions as SR
    import test_search_regressions as T
    row = T._row("v2:nt184644", state="NT", suburb="Tarneit", date_checked="08/10/2026")
    def places(suburb):                     # geo.states_for_suburb's shape
        return {"tarneit": ["VIC"], "darwin": ["NT"]}.get(suburb.strip().lower(), [])

    def answers(_body, suburb="Tarneit"):
        return T._response([T._card(row, suburb=suburb)],
                           brief_used={"state": "NT", "budget_min": 0,
                                       "budget_max": 9007199254740991, "primary_suburbs": []})

    def run(brief, answer=answers, with_places=True):
        report = SR.Report(io.StringIO())
        SR.Run(dict(T.SPEC, briefs=[brief]), lambda body: (200, answer(body), 0.01),
               SR.Catalogue([row]), datetime(2026, 10, 8).date(), report,
               places=places if with_places else None).execute()
        return report, report.out.getvalue()

    strict = {"id": "nt_any", "client_brief": {"state": "NT"},
              "expect": {"cards_in_their_places": True}}
    report, out = run(strict)
    assert report.failures == 1 and "is shown as Tarneit, NT, but Tarneit is a locality of " \
        "VIC only" in out, out
    report, out = run(dict(strict, expect={}))
    assert report.failures == 0 and "place: shortlisted" in out, out
    report, out = run(strict, with_places=False)
    assert report.failures == 0, out
    report, out = run(strict, answer=lambda b: answers(b, suburb="Darwin"))
    assert report.failures == 0, out


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
    excuse: a narrowed list, an empty one, a gap past 3 lots and 10%.

    SECOND REPAIR ROUND (8 Oct 2026, correctness verifier): at 15% CARLING 68 of 79, The
    Grande 100 of 117 and Live Caringbah 120 of 140 passed as stale counters -- 1.7 times
    the largest gap seen (CARLING 7 of 79, 8.9%). Each fails now; the seven real ones hold."""
    import harvest_manifest as HM
    for read, stated in ((2, 3), (8, 11), (27, 28), (138, 140), (72, 79), (108, 117)):
        assert HM.old_counter_gap_ok(read, stated), (read, stated)
    for read, stated in ((2, 79), (0, 40), (0, 3), (67, 79), (99, 117), (5, 10),
                         (68, 79), (100, 117), (120, 140)):
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


def _read(entries, facts=None, night="08/10/2026"):
    import parity_check as P
    return P.read_bar({"schema": 1, "generated": night + " 03:30", "entries": entries},
                      [], night, required=True, facts=facts)


def _fact(rid, pid, price, status="For Sale", read="08/10/2026", superseded=False,
          shipped=True):
    return {"id": rid, "pid": pid, "price": price, "status": status, "read": read,
            "superseded": superseded, "in_snapshot": shipped}


def _lot(h, lot, address, price, into, status="For Sale"):
    return {"hash": h, "lot": lot, "address": address, "price": price, "status": status,
            "into": into}


# The same night as the fixed harvest writes it (8 Oct 2026, second repair round): the
# copies that agree (Ascenta's 4, Park Avenue's 1) named lot by lot, with the rows that
# prove them; the copies that disagree (Atchison B's 2, Lancelin's 3) stored under their
# own projects, so nothing is cross-listed there. Row ids and prices from the 8 Oct database.
ASCENTA = [("h1212", "1212 Pinnacle", "Lot 1212 Pinnacle Unit 1212 Pinnacle, Vespa Grange",
            610800.0, 5630),
           ("h55", "55 Woodlands", "Lot 55 Woodlands Unit 55 Woodlands, Hillier Circuit",
            654500.0, 20591),
           ("h61", "61 Woodlands", "Lot 61 Woodlands Unit 61 Woodlands, Hillier Circuit",
            653500.0, 20592),
           ("h64", "64 Woodlands", "Lot 64 Woodlands Unit 64 Woodlands, Hillier Circuit",
            654800.0, 20593)]
FACTS_FIXED = {h: [_fact(rid, "785", price)] for h, _l, _a, price, rid in ASCENTA}
FACTS_FIXED["h26"] = [_fact(13699, "758", 1484000.0)]


def _fixed(entries):
    out = []
    for e in entries:
        e = dict(e)
        if e["pid"] == "776":
            e.update(stored={"new": 0, "updated": 0, "unchanged": 76, "rejected": 0},
                     cross_listed_lots=[_lot(h, lot, a, p, "785") for h, lot, a, p, _r
                                        in ASCENTA])
        elif e["pid"] == "689":
            e["cross_listed_lots"] = [_lot("h26", "26", "Lot 26 Unit , 74 Tallawong Road",
                                           1484000.0, "758")]
        elif e["pid"] in ("793", "767"):
            for k in ("cross_listed", "cross_listed_into"):
                e.pop(k, None)
            e["stored"] = {"new": 0, "updated": 0, "unchanged": e["printed"], "rejected": 0}
            e["cross_listed_offers"] = 2 if e["pid"] == "793" else 3
        out.append(e)
    return out


LIVE_FIXED = _fixed(LIVE_8OCT)


def test_d_the_eighth_of_october_cross_listed_counts_are_no_proof():
    """SECOND REPAIR ROUND (correctness verifier). The 8 Oct manifest gave counts only --
    "2 into project 161", "3 into project 802" -- and the first cut counted them as stored.
    Two of them were real losses (Atchison B's For Sale units kept as Building A's Not
    Available rows; Lancelin's land-only offer kept at the house-and-land price) and two
    could not be checked. A count names no lot, so no row can prove it: all four fail, as
    on origin/main. The old counters and the overseas projects are still named, not failed."""
    failures, summary, notes = _read([dict(e) for e in LIVE_8OCT], facts={})
    assert len(failures) == 1, failures
    text = failures[0]["text"]
    assert text.startswith("4 document(s) stored under 98%"), text
    for name in ("Ascenta Living Traditional", "Building B", "Land Only", "Stage 3 Rouse Hill"):
        assert name in text, name
    assert "claimed with no lot named" in text
    said = " ".join(notes)
    for name in ("Jasmine Schofields", "The Grande", "VERTEX Zetland", "SMDC Gold Residences",
                 "SMDC Glade Residences ILOILO"):
        assert name in said, name
    assert (summary["proxima_old_counter"], summary["proxima_overseas_short"],
            summary["stored_elsewhere"]) == (7, 2, 0)


def test_d_the_fixed_harvest_of_the_same_night_passes_on_its_rows():
    failures, summary, notes = _read([dict(e) for e in LIVE_FIXED], facts=FACTS_FIXED)
    assert failures == [], failures
    said = " ".join(notes)
    assert "Ascenta Living Traditional" in said and "Stage 3 Rouse Hill" in said
    assert summary["stored_elsewhere"] == 2


ATCHISON_B = _entry("793", "Atchison and Kenny Wollongong Building B", 19, 17,
                    on_index=True, header_counter=[19, 160], stated=19,
                    stated_source="header", whole="uncapped", cross_listed=2,
                    cross_listed_into={"161": 2}, cross_listed_lots=[
                        _lot("h194", "194", "Lot 194 Unit B-1301, 30 Ellen Street, WOLLONGONG",
                             770000.0, "161"),
                        _lot("h276", "276", "Lot 276 Unit B-1803, 30 Ellen Street, WOLLONGONG",
                             790000.0, "161")])
# What the database holds for them on 8 Oct: Building A's availability-view rows.
ATCHISON_ROWS = {"h194": [_fact(10949, "161", 790000.0, "Not Available")],
                 "h276": [_fact(11009, "161", 790000.0, "Not Available")]}
LANCELIN_LAND = _entry("767", "Lancelin South Stage C Land Only", 24, 21, whole="uncapped",
                       cross_listed=3, cross_listed_into={"802": 3}, cross_listed_lots=[
                           _lot("h71", "71", "Lot 71 Unit 71, Lancelin Road, Lancelin South",
                                358600.0, "802"),
                           _lot("h129", "129", "Lot 129 Unit 129, Lancelin Road",
                                390900.0, "802"),
                           _lot("h130", "130", "Lot 130 Unit 130, Lancelin Road",
                                390900.0, "802")])
# Rows 19965-19967: still project 767's, at Stage C's house-and-land price.
LANCELIN_ROWS = {"h71": [_fact(19965, "767", 701500.0)],
                 "h129": [_fact(19966, "767", 733800.0)],
                 "h130": [_fact(19967, "767", 733800.0)]}


def _with(entries, entry):
    return [entry if e["pid"] == entry["pid"] else dict(e) for e in entries]


def test_d_mutation_atchison_b_units_hidden_as_another_projects_rows_fail():
    """MUTATION: the old reader's Atchison B, had it named its lots. Both units were printed
    For Sale under Building B and are held only as Building A's Not Available rows -- hidden
    from every search. Before this round the bar counted them as stored and passed."""
    facts = dict(FACTS_FIXED, **ATCHISON_ROWS)
    failures, _s, _n = _read(_with(LIVE_FIXED, ATCHISON_B), facts=facts)
    assert len(failures) == 1, failures
    text = failures[0]["text"]
    assert "Building B (stored 17 of 19 printed" in text, text
    assert "B-1301" in text and "says Not Available, not For Sale" in text, text
    # ... and once Building A's rows say For Sale at B's prices, the same lots are proven.
    good = {"h194": [_fact(10949, "161", 770000.0)], "h276": [_fact(11009, "161", 790000.0)]}
    failures, _s, _n = _read(_with(LIVE_FIXED, ATCHISON_B), facts=dict(FACTS_FIXED, **good))
    assert failures == [], failures


def test_d_mutation_a_land_only_offer_lost_in_the_collapse_fails():
    """MUTATION: Lancelin's 3 land-only lots, cross-listed into Stage C (802), whose rows are
    still Land Only's (767) at Stage C's house-and-land price. Fails on the project; and
    with the project put right, on the price -- the land offer at $358,600 is not stored by
    a row carding $701,500."""
    facts = dict(FACTS_FIXED, **LANCELIN_ROWS)
    failures, _s, _n = _read(_with(LIVE_FIXED, LANCELIN_LAND), facts=facts)
    assert len(failures) == 1, failures
    assert "its row is under project 767, not 802" in failures[0]["text"], failures
    relabelled = {h: [dict(f, pid="802") for f in rows] for h, rows in LANCELIN_ROWS.items()}
    failures, _s, _n = _read(_with(LIVE_FIXED, LANCELIN_LAND),
                             facts=dict(FACTS_FIXED, **relabelled))
    assert len(failures) == 1, failures
    assert "its row is priced $701,500, not $358,600" in failures[0]["text"], failures


def test_d_a_named_lot_the_database_contradicts_fails_whatever_the_share():
    """One hidden lot in a list of 305 is 99.7% stored -- and still a lot hidden."""
    big = _entry("161", "Atchison and Kenny Wollongong Building A", 305, 304,
                 on_index=True, via="availability view", header_counter=[7, 305], stated=7,
                 stated_source="header", whole="availability view", cross_listed=1,
                 cross_listed_into={"793": 1},
                 cross_listed_lots=[_lot("h194", "194", "Lot 194 Unit B-1301", 770000.0,
                                         "793")])
    facts = dict(FACTS_FIXED, h194=[_fact(30001, "793", 770000.0, "Not Available")])
    failures, _s, _n = _read(_with(LIVE_FIXED, big), facts=facts)
    assert failures and "stored 304 of 305 printed" in failures[0]["text"], failures


@pytest.mark.parametrize("change,why", [
    (dict(superseded=True), "no live row holds it"),
    (dict(read="07/10/2026"), "its row was last read 07/10/2026"),
    (dict(status="Sold"), "its row says Sold, not For Sale"),
    (dict(price=600000.0), "its row is priced $600,000, not $610,800"),
    (dict(shipped=False), "not in the snapshot that ships"),
    (dict(pid="776"), "its row is under project 776, not 785"),
])
def test_d_mutation_each_fact_of_the_proof_is_needed(change, why):
    """Take one fact away from the row that proves Ascenta's 1212 Pinnacle and it fails."""
    base = dict(id=5630, pid="785", price=610800.0, status="For Sale", read="08/10/2026",
                superseded=False, shipped=True)
    base.update(change)
    rid = base.pop("id")
    facts = dict(FACTS_FIXED, h1212=[_fact(rid, **base)])
    failures, _s, _n = _read([dict(e) for e in LIVE_FIXED], facts=facts)
    assert failures and why in failures[0]["text"], failures
    # No facts measured at all is no proof either.
    failures, _s, _n = _read([dict(e) for e in LIVE_FIXED], facts=None)
    assert failures and "no row evidence was measured" in failures[0]["text"], failures


# --- the reader: only copies are collapsed, and a copy is named where it went

def _px_row(src, pid, project, name, lot, price, status="For Sale"):
    tile = {"name": name, "lot": "%08d/%08d" % (lot, lot), "rop": str(price),
            "packageprice": "0", "room": "4", "bathroom": "2", "carspace": "2",
            "landsize": "645", "_status": status, "_titled": "", "_floorplan": "",
            "_reserved": "no"}
    return src._row(tile, {"project": project, "developer": "", "location": ""}, pid)


def _src(*pids, owners=None):
    src = px.ProximaSource()
    src.stored_counters, src.stored_row_projects = {}, dict(owners or {})
    src.project_log = [{"pid": p, "name": p, "printed": 1} for p in pids]
    return src


B1301 = "Lot 194 Unit B-1301, 30 Ellen Street, WOLLONGONG, NSW, 2500"
LOT71 = "Lot 71 Unit 71, Lancelin Road, Lancelin South, WA, 6044"


def test_d_reader_a_for_sale_unit_is_not_dropped_for_another_projects_not_available():
    """Atchison and Kenny: Building A's availability view (161, first) says B-1301 is Not
    Available at $790,000; Building B's own list (793) prints it For Sale at $770,000.
    Before: one row, A's Not Available -- hidden everywhere. Now both are stored, the For
    Sale one under its own project as a variant, and nothing is called cross-listed."""
    from database import building_content_hash
    src = _src("161", "793")
    a = _px_row(src, "161", "Atchison and Kenny Wollongong Building A", B1301, 194, 790000,
                "Not Available")
    b = _px_row(src, "793", "Atchison and Kenny Wollongong Building B", B1301, 194, 770000)
    h = building_content_hash(a)
    assert building_content_hash(b) == h, "premise: the two copies share one identity"
    src.stored_row_projects = {h: "161"}
    out = src._collapse_cross_listed([a, b])
    assert [(r["source_project_id"], r["availability_status"]) for r in out] == [
        ("161", "Not Available"), ("793", "For Sale")]
    assert building_content_hash(out[0]) == h and building_content_hash(out[1]) != h
    assert out[1]["variant_ordinal"] == 793
    logs = {e["pid"]: e for e in src.project_log}
    assert "cross_listed" not in logs["793"] and logs["793"]["cross_listed_offers"] == 1


def test_d_reader_a_land_only_offer_and_its_house_and_land_package_are_two_rows(tmp_path):
    """Lancelin South: Stage C (802, first in page order) prints lot 71 as house-and-land
    at $701,500, Stage C Land Only (767) as land at $358,600. The stored row (19965's shape)
    is 767's. Before: one row, 767's label at 802's price. Now 767's land offer keeps the
    row and its price is the land price again; 802's package is a row of its own -- in
    either page order."""
    import sqlite3
    from database import ResearchDatabase, building_content_hash
    for order in ((0, 1), (1, 0)):
        src = _src("802", "767")
        hl = _px_row(src, "802", "Lancelin South Stage C", LOT71, 71, 701500)
        land = _px_row(src, "767", "Lancelin South Stage C Land Only", LOT71, 71, 358600)
        h = building_content_hash(land)
        path = tmp_path / ("lancelin%d%d.db" % order)
        db = ResearchDatabase(path)
        first = dict(land, advertised_package_price=701500)     # the 8 Oct row: 767 at H&L
        assert db.record_building(first) == "new"
        src.stored_row_projects = px.stored_row_projects(path)
        assert src.stored_row_projects == {h: "767"}
        rows = [hl, land]
        out = src._collapse_cross_listed([rows[i] for i in order])
        assert sorted((r["source_project_id"], r.get("variant_ordinal", 0)) for r in out) == [
            ("767", 0), ("802", 802)], out
        for r in out:
            db.record_building(r)
        con = sqlite3.connect(str(path))
        got = sorted(con.execute("SELECT source_project_id, estate_name, price FROM buildings "
                                 "WHERE superseded_by IS NULL"))
        con.close()
        assert got == [("767", "Lancelin South Stage C Land Only", 358600.0),
                       ("802", "Lancelin South Stage C", 701500.0)], got
    # With no stored row the lowest project id keeps the identity, whatever the order.
    for order in ((0, 1), (1, 0)):
        src = _src("802", "767")
        rows = [_px_row(src, "802", "Lancelin South Stage C", LOT71, 71, 701500),
                _px_row(src, "767", "Lancelin South Stage C Land Only", LOT71, 71, 358600)]
        out = src._collapse_cross_listed([rows[i] for i in order])
        assert {r["source_project_id"]: r.get("variant_ordinal", 0) for r in out} == {
            "767": 0, "802": 802}


def test_d_reader_for_sale_and_available_are_one_offer():
    """An availability view says "Available" where the accordion says "For Sale", and a view
    may say "Sold" where another says "Not Available": the same offer either way, kept once
    -- told apart by the word, one lot would be stored, and shown, twice. And the READ bar
    takes the kept "Available" row as proof of a "For Sale" copy."""
    import parity_check as P
    from database import building_content_hash
    src = _src("161", "793")
    rows = [_px_row(src, "161", "Atchison and Kenny Wollongong Building A", B1301, 194, 790000,
                    "Available"),
            _px_row(src, "793", "Atchison and Kenny Wollongong Building B", B1301, 194, 790000)]
    out = src._collapse_cross_listed(rows)
    assert [r["source_project_id"] for r in out] == ["161"], out
    lot = next(e for e in src.project_log if e["pid"] == "793")["cross_listed_lots"][0]
    assert lot["status"] == "For Sale" and lot["into"] == "161"
    facts = {lot["hash"]: [_fact(10949, "161", 790000.0, "Available")]}
    assert P._lot_proven(lot, facts, "08/10/2026") == ""
    src = _src("161", "162")
    rows = [_px_row(src, "161", "A", B1301, 194, 790000, "Sold"),
            _px_row(src, "162", "B", B1301, 194, 790000, "Not Available")]
    assert len(src._collapse_cross_listed(rows)) == 1
    assert building_content_hash(rows[0]) == building_content_hash(rows[1])


def test_d_reader_true_copies_collapse_and_name_the_lot_and_its_row(tmp_path):
    """Ascenta Living: the same lot at the same price and status under Coliving (785) and
    Traditional (776) is one offer, kept once -- under the project its stored row has, else
    the first printed -- and the dropped copy's line names it: identity, price, status and
    where it went. Recorded, its row proves it to the READ bar; hidden, it does not."""
    import sqlite3
    import parity_check as P
    import search_regressions as SR
    from database import ResearchDatabase, building_content_hash
    name = "Lot 1212 Pinnacle Unit 1212 Pinnacle, Vespa Grange, SMYTHES CREEK, VIC, 3351"
    src = _src("785", "776")
    rows = [_px_row(src, "785", "Ascenta Living Coliving", name, 1212, 610800),
            _px_row(src, "776", "Ascenta Living Traditional", name, 1212, 610800)]
    out = src._collapse_cross_listed(rows)
    assert [r["source_project_id"] for r in out] == ["785"]
    h = building_content_hash(out[0])
    trad = next(e for e in src.project_log if e["pid"] == "776")
    assert trad["cross_listed_into"] == {"785": 1}
    assert trad["cross_listed_lots"] == [{"hash": h, "lot": "1212", "address": name[:90],
                                          "price": 610800.0, "status": "For Sale",
                                          "into": "785"}]
    # the stored row's project wins over page order
    src2 = _src("785", "776", owners={h: "776"})
    rows2 = [_px_row(src2, "785", "Ascenta Living Coliving", name, 1212, 610800),
             _px_row(src2, "776", "Ascenta Living Traditional", name, 1212, 610800)]
    assert [r["source_project_id"] for r in src2._collapse_cross_listed(rows2)] == ["776"]
    # end to end: stored, then measured the way parity_check measures it
    path = tmp_path / "ascenta.db"
    db = ResearchDatabase(path)
    assert db.record_building(out[0]) == "new"
    night = datetime.now().strftime("%d/%m/%Y")
    entries = [_entry("785", "Ascenta Living Coliving", 1, 1, on_index=True, whole="uncapped"),
               dict(_entry("776", "Ascenta Living Traditional", 2, 1, on_index=True,
                           whole="uncapped"), **{k: trad[k] for k in (
                               "cross_listed", "cross_listed_into", "cross_listed_lots")})]
    manifest = {"entries": entries}
    db_rows = SR.read_db_rows(path)
    facts = P.cross_listed_row_facts(db_rows, manifest, {SR._hash(r) for r in db_rows})
    assert _read(entries, facts=facts, night=night)[0] == []
    con = sqlite3.connect(str(path))
    con.execute("UPDATE buildings SET availability_status='Sold'")
    con.commit()
    con.close()
    db_rows = SR.read_db_rows(path)
    facts = P.cross_listed_row_facts(db_rows, manifest, {SR._hash(r) for r in db_rows})
    failures = _read(entries, facts=facts, night=night)[0]
    assert failures and "its row says Sold, not For Sale" in failures[0]["text"], failures


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
    ("776", ("cross_listed", "cross_listed_into", "cross_listed_lots"), {},
     "stored 76 of 80 printed"),
    # The project it names was not read tonight: a lot cannot vanish into a name.
    ("776", (), {"cross_listed_lots": [_lot(h, lot, a, p, "999") for h, lot, a, p, _r
                                       in ASCENTA]},
     "project 999 was not read tonight"),
    # A count with no lot named (the 8 Oct manifest's own shape) is no proof.
    ("776", ("cross_listed_lots",), {}, "4 claimed with no lot named"),
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
    """MUTATIONS: take away the one fact that excuses each 8 Oct alarm and it fails again
    (over the night as the fixed harvest writes it, which passes untouched)."""
    assert _read([dict(e) for e in LIVE_FIXED], facts=FACTS_FIXED)[0] == []
    failures, _summary, _notes = _read(_without(LIVE_FIXED, pid, *keys, **more),
                                       facts=FACTS_FIXED)
    assert failures and any(why in f["text"] for f in failures), failures
