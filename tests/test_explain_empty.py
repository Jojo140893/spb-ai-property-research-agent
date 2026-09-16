"""Why a search found only N, and what each change to the brief would really find.

Part C, 16 Sep 2026: Colin searched "QLD under $750k" and the Research tab showed two cards
and "2 Qualified (1005 Filtered)". Nothing on screen said that 23 in-state listings sit in
his price range, that 3 match but were last read 27 days ago, or that $825,000 finds 12.

With `"explain": true` (and only then) /api/research adds `search_help`: the funnel gate
by gate with its in-range tally, the location notes, and -- for a thin search -- every
relaxation priced by re-running the real search. These tests pin the contract:

  * without the literal flag the response is exactly W12's, so Clare's hook and the Hot
    Leads re-run can never see an explanation (a truthy "true" is not the flag);
  * with it, only new keys are added, and none of the search's own keys changes;
  * every relaxation reported 'exact' re-POSTs to exactly its shortlist_count;
  * relaxations are priced in priority order, so a slow search runs out of time on the
    least likely ones, never on the budget steps (critique #24);
  * an explanation that breaks never costs the consultant the search.

Run on tests/fixtures/colin_17sep_subset.json, today frozen at 16/09/2026.
"""
import contextlib
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
for _p in (str(APP / "api"), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _candidates  # noqa: E402

SUBSET = APP / "tests" / "fixtures" / "colin_17sep_subset.json"
NO_CEILING = 9007199254740991

FORM = {
    "client_name": "Part C explain", "budget_min": 0, "budget_max": 750000,
    "preferred_spending_cap": 750000, "buyer_type": "", "state": "QLD",
    "primary_suburbs": [], "bedrooms_min": 0, "bathrooms_min": 0, "car_spaces_min": 0,
    "storeys_max": 3, "land_size_min_sqm": 0, "house_size_min_sqm": 0,
    "search_radius_km": 0, "product_type": [], "requires_incentive": False,
}

# The response W12 shipped, on both returns. Nothing may be added to it without the flag.
W12_KEYS = {
    "status", "research_record_id", "shortlist_count", "rejected_count", "in_state_rows",
    "brief_used", "brief_adjustments", "rejected_log", "shortlist", "reports",
    "search_area", "region_expansion", "location_resolution", "builder_coverage",
    "client_report_html", "comparisons", "client_report_path", "qa_passed", "qa_failures",
    "kommo_payload",
}
EXPLAIN_KEYS = {"search_help", "needs_confirming", "needs_confirming_count",
                "needs_confirming_hidden"}

# The order relaxations are priced in (critique #24): the reveal, the budget steps, the
# location, then the minimums and the filters.
PRIORITY = ["include_needs_confirming", "budget_plus_50k", "budget_plus_10pct",
            "whole_state", "radius_20", "radius_50", "drop_bedrooms_min",
            "drop_bathrooms_min", "drop_car_spaces_min", "drop_house_size_min_sqm",
            "drop_land_size_min_sqm", "drop_all_minimums", "drop_product_type",
            "drop_incentive", "drop_price_floor"]

BRIEFS = {
    "qld_750k": {},
    # Zero packages: the early-return branch.
    "qld_100k": {"budget_max": 100000, "preferred_spending_cap": 100000},
    "qld_plus_suburb_queensland": {"primary_suburbs": ["Queensland"]},
    "anystate_suburb_queensland": {"state": "", "primary_suburbs": ["Queensland"]},
    # The state comes from the normaliser, not the form: whole_state must carry it.
    "anystate_queensland_and_walloon": {"state": "", "primary_suburbs": ["Queensland",
                                                                         "Walloon"]},
    "springfield_lakes_townhouse_3bed_2bath_from_400k": {
        "primary_suburbs": ["Springfield Lakes"], "product_type": ["Townhouse"],
        "budget_min": 400000, "bedrooms_min": 3, "bathrooms_min": 2},
    "ripley_10km_4bed": {"primary_suburbs": ["Ripley"], "bedrooms_min": 4,
                         "search_radius_km": 10},
    "qld_incentive_from_500k": {"requires_incentive": True, "budget_min": 500000},
    "anystate_house_land_incentive_650k_700k_4bed_2bath": {
        "state": "", "requires_incentive": True, "product_type": ["House & Land"],
        "budget_min": 650000, "budget_max": 700000, "preferred_spending_cap": 700000,
        "bedrooms_min": 4, "bathrooms_min": 2},
    "qld_townhouse_from_550k": {"product_type": ["Townhouse"], "budget_min": 550000},
    "qld_every_minimum": {"bedrooms_min": 3, "bathrooms_min": 2, "car_spaces_min": 2,
                          "house_size_min_sqm": 150, "land_size_min_sqm": 300},
    # Max left blank, $750k preferred: 402 shortlisted, so nothing is priced.
    "variant_E_max_blank_pref_750k": {"budget_max": NO_CEILING,
                                      "preferred_spending_cap": 750000},
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
    mp.setattr(_candidates, "datetime", _Sep16)
    import research as module
    mp.setattr(module, "EXPLAIN_BUDGET_MS", 60000)
    mp.setattr(module, "EXPLAIN_BELOW", 5)
    mp.setattr(module, "MAX_NEEDS_CONFIRMING", 50)
    yield module
    mp.undo()


def _brief(name, **more):
    brief = json.loads(json.dumps(FORM))
    brief.update(BRIEFS[name])
    brief.update(more)
    return brief


def _run(research, brief, **payload):
    with contextlib.redirect_stdout(io.StringIO()):
        return research.run_research(dict(payload, client_brief=json.loads(json.dumps(brief))))


def _comparable(out):
    """The response minus what legitimately differs between two runs of one search: the
    clock inside the Kommo payload and the record id's date."""
    out = json.loads(json.dumps(out, default=str))
    out.pop("research_record_id", None)
    kommo = out.get("kommo_payload") or {}
    kommo.pop("research_record_id", None)
    (kommo.get("custom_fields_update") or {}).pop("research_completed_date", None)
    note = kommo.get("kommo_internal_note") or ""
    kommo["kommo_internal_note"] = "\n".join(
        line for line in note.splitlines() if not line.startswith("Record ID:"))
    return out


@pytest.fixture(scope="module")
def plain(research):
    return {name: _run(research, _brief(name)) for name in BRIEFS}


@pytest.fixture(scope="module")
def explained(research):
    return {name: _run(research, _brief(name), explain=True) for name in BRIEFS}


# ----------------------------------------------------------------- (a) only on request

@pytest.mark.parametrize("name", ["qld_750k", "qld_100k"])
@pytest.mark.parametrize("flag", ["absent", False, None, "true", "True", 1, "yes", {}, [True]])
def test_without_a_literal_true_the_response_is_w12s(research, plain, name, flag):
    """Clare's hook posts {client_brief} and nothing else; anything but JSON true -- a
    string, a 1 -- is not the flag either."""
    out = (_run(research, _brief(name)) if flag == "absent"
           else _run(research, _brief(name), explain=flag))
    assert set(out) == W12_KEYS
    assert _comparable(out) == _comparable(plain[name])


@pytest.mark.parametrize("name", list(BRIEFS))
def test_explain_adds_its_own_keys_and_changes_none_of_the_searchs(plain, explained, name):
    out, base = explained[name], plain[name]
    assert set(base) == W12_KEYS
    assert set(out) == W12_KEYS | EXPLAIN_KEYS
    assert _comparable({k: out[k] for k in W12_KEYS}) == _comparable(base)
    assert out["search_help"]["version"] == 1
    assert "error" not in out["search_help"], out["search_help"].get("error")
    assert out["search_help"]["brief_as_searched"] == out["brief_used"]
    assert out["search_help"]["brief_adjustments"] == out["brief_adjustments"]
    json.dumps(out, allow_nan=False, default=str)       # a browser can parse it


# ------------------------------------------------------------------------- the funnel

def test_the_funnel_accounts_for_colins_search(explained):
    out = explained["qld_750k"]
    funnel = out["search_help"]["funnel"]
    assert funnel["snapshot_rows"] == 8694
    assert (funnel["snapshot_rows"] - sum(funnel["out_of_scope"].values())
            == funnel["in_state_rows"] == out["in_state_rows"])
    assert funnel["shortlisted"] == out["shortlist_count"] == 2
    assert funnel["scored"] == 8 and funnel["pending_confirmation"] == 6
    assert funnel["just_over"] == 8 and funnel["capped"] is False
    # Critique #11's in-range tally: 11 passed every gate, 6 unstated, 5 sold or on hold.
    assert funnel["in_price_range"] == {"available": 11, "availability_unstated": 6,
                                        "not_available": 5, "price_unverified": 1}
    assert funnel["in_price_range_total"] == 23
    before = [g for g in funnel["gates"] if g["stage"] == "before_scoring"]
    assert [g["code"] for g in before] == [
        c for c in _candidates.GATE_ORDER if c not in ("superseded", "state_unknown",
                                                         "other_state")]
    by_code = {g["code"]: g for g in funnel["gates"]}
    assert by_code["availability_unstated"]["count"] == 403
    assert by_code["availability_unstated"]["in_price_range"] == 6
    assert by_code["not_available"]["in_price_range"] == 5
    assert by_code["over_budget"]["in_price_range"] is None
    assert by_code["pending_confirmation"] == {"code": "pending_confirmation",
                                               "stage": "scoring", "count": 6,
                                               "in_price_range": None}


def test_the_funnel_names_the_scorers_own_rejections(explained):
    funnel = explained["ripley_10km_4bed"]["search_help"]["funnel"]
    scoring = {g["code"]: g["count"] for g in funnel["gates"] if g["stage"] == "scoring"}
    assert funnel["rejected_by_scorer"] > 0, "premise: the scorer rejects something here"
    assert sum(n for code, n in scoring.items() if code != "pending_confirmation") >= \
        funnel["rejected_by_scorer"]
    assert set(scoring) - {"pending_confirmation"} <= set(
        __import__("scoring_engine").REJECTION_CODES)


def test_the_zero_packages_branch_is_explained_too(explained):
    out = explained["qld_100k"]
    assert out["shortlist_count"] == 0 and out["rejected_log"][0]["kind"] == "coverage"
    funnel = out["search_help"]["funnel"]
    assert funnel["scored"] == 0 and funnel["pending_confirmation"] == 0
    ids = [r["id"] for r in out["search_help"]["relaxations"]]
    assert ids[:3] == ["include_needs_confirming", "budget_plus_50k", "budget_plus_10pct"]


# ----------------------------------------------------------------- location notes (#24)

@pytest.mark.parametrize("name", ["qld_plus_suburb_queensland", "anystate_suburb_queensland"])
def test_a_state_typed_as_a_suburb_is_in_the_location_notes(explained, name):
    """The normaliser's 'state' entry survives the resolver replacing location_resolution,
    and reaches search_help.location_notes."""
    out = explained[name]
    notes = out["search_help"]["location_notes"]
    assert [(n["said"], n["method"]) for n in notes] == [("Queensland", "state")]
    assert notes == [e for e in out["location_resolution"] if e["method"] == "state"]


def test_an_exact_suburb_needs_no_note_and_an_unplaceable_one_gets_one(research):
    exact = _run(research, _brief("qld_750k", primary_suburbs=["Walloon"]), explain=True)
    assert exact["search_help"]["location_notes"] == []
    nowhere = _run(research, _brief("qld_750k", primary_suburbs=["Qwzxv Flats"]),
                   explain=True)
    assert [n["method"] for n in nowhere["search_help"]["location_notes"]] == ["unresolved"]


# ------------------------------------------------------------------------ relaxations

def _relaxations(out):
    return out["search_help"]["relaxations"]


def test_relaxations_are_priced_in_priority_order(explained):
    seen = set()
    for name, out in explained.items():
        ids = [r["id"] for r in _relaxations(out)]
        assert all(i in PRIORITY for i in ids), (name, ids)
        assert ids == sorted(ids, key=PRIORITY.index), (name, ids)
        seen.update(ids)
    # PREMISE: the briefs above offer every kind, so a reordering cannot hide.
    assert seen == set(PRIORITY), set(PRIORITY) - seen


def test_the_time_budget_is_spent_on_the_budget_steps_first(research, monkeypatch):
    """Out of time after two re-runs: the budget steps are priced, the rest are said to be
    not computed -- never silently dropped, never guessed."""
    calls = []

    def two_then_out(started):
        calls.append(started)
        return len(calls) > 2

    monkeypatch.setattr(research, "_out_of_time", two_then_out)
    out = _run(research, _brief("springfield_lakes_townhouse_3bed_2bath_from_400k"),
               explain=True)
    rel = _relaxations(out)
    assert len(rel) >= 6, "premise: many kinds are offered"
    assert rel[0]["id"] == "include_needs_confirming" and rel[0]["count_status"] == "exact"
    assert [(r["id"], r["count_status"]) for r in rel[1:3]] == [
        ("budget_plus_50k", "exact"), ("budget_plus_10pct", "exact")]
    for r in rel[3:]:
        assert r["count_status"] == "not_computed"
        assert r["shortlist_count"] is None and r["adds"] is None, r


def test_with_no_time_budget_nothing_is_re_run(research, monkeypatch):
    monkeypatch.setattr(research, "EXPLAIN_BUDGET_MS", 0)
    out = _run(research, _brief("qld_750k"), explain=True)
    rel = _relaxations(out)
    assert [r["count_status"] for r in rel] == ["exact"] + ["not_computed"] * (len(rel) - 1)
    assert rel[0]["count"] == out["needs_confirming_count"]


def test_colins_search_prices_both_budget_steps(explained):
    rel = {r["id"]: r for r in _relaxations(explained["qld_750k"])}
    assert rel["include_needs_confirming"]["count"] == 3
    assert rel["include_needs_confirming"]["brief_patch"] is None
    assert rel["budget_plus_50k"]["brief_patch"] == {"budget_max": 800000,
                                                     "preferred_spending_cap": 800000}
    assert rel["budget_plus_10pct"]["brief_patch"] == {"budget_max": 825000,
                                                       "preferred_spending_cap": 825000}
    assert (rel["budget_plus_10pct"]["shortlist_count"], rel["budget_plus_10pct"]["adds"]) \
        == (12, 10)
    assert rel["budget_plus_10pct"]["label"] == "Budget up to $825,000 (+10%)"
    assert rel["budget_plus_10pct"]["capped"] is False


def test_equal_budget_steps_are_offered_once_and_a_typed_preferred_price_stays(research):
    out = _run(research, _brief("qld_750k", budget_max=500000,
                                preferred_spending_cap=450000), explain=True)
    budget = [r for r in _relaxations(out) if r["id"].startswith("budget_")]
    # +$50,000 and +10% of $500,000 are the same $550,000.
    assert [(r["id"], r["brief_patch"]) for r in budget] == [
        ("budget_plus_50k", {"budget_max": 550000})]


@pytest.mark.parametrize("name", [n for n in BRIEFS if n != "variant_E_max_blank_pref_750k"])
def test_every_exact_relaxation_re_posts_to_its_count(research, explained, name):
    """(d) The count on each button is what clicking it returns: the brief as sent, with
    brief_patch applied, posted again. The reveal has no patch and is not a re-run."""
    brief = _brief(name)
    checked = 0
    for r in _relaxations(explained[name]):
        if r["kind"] == "reveal":
            assert r["brief_patch"] is None
            continue
        assert r["count_status"] == "exact", r
        again = _run(research, dict(brief, **r["brief_patch"]))
        assert again["shortlist_count"] == r["shortlist_count"], (name, r["id"])
        assert r["adds"] == r["shortlist_count"] - explained[name]["shortlist_count"]
        checked += 1
    assert checked, "premise: something was re-run"


def test_the_relaxations_can_change_the_answer(explained):
    """PREMISE for the self-check: a count that never moves could not be wrong."""
    adds = {(n, r["id"]): r["adds"] for n, out in explained.items()
            for r in _relaxations(out) if r["kind"] == "rerun"}
    assert sum(1 for v in adds.values() if v) >= 5, adds


def test_whole_state_carries_the_state_the_search_used(research, explained):
    """"Queensland" beside "Walloon" with Any state selected searched QLD. Re-posted without
    the state, dropping the suburbs would search every state instead."""
    name = "anystate_queensland_and_walloon"
    rel = {r["id"]: r for r in _relaxations(explained[name])}
    patch = rel["whole_state"]["brief_patch"]
    assert patch == {"state": "QLD", "primary_suburbs": [], "search_radius_km": 0}
    without_state = {k: v for k, v in patch.items() if k != "state"}
    assert (_run(research, dict(_brief(name), **without_state))["shortlist_count"]
            != rel["whole_state"]["shortlist_count"]), "premise: the state matters here"


def test_a_long_shortlist_prices_no_relaxations(explained):
    out = explained["variant_E_max_blank_pref_750k"]
    assert out["shortlist_count"] >= 5
    assert _relaxations(out) == []
    assert "SPB_EXPLAIN_BELOW" in out["search_help"]["relaxations_note"]
    assert out["needs_confirming_count"] > 0, "needs confirming is still worked out"


# ------------------------------------------------------------------------ never fatal

def test_a_broken_needs_confirming_pass_costs_nothing_else(research, plain, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(research, "_needs_confirming", broken)
    out = _run(research, _brief("qld_750k"), explain=True)
    assert out["status"] == "success"
    assert _comparable({k: out[k] for k in W12_KEYS}) == _comparable(plain["qld_750k"])
    assert "needs_confirming: RuntimeError" in out["search_help"]["error"]
    assert out["needs_confirming"] == [] and out["needs_confirming_count"] is None
    rel = {r["id"]: r for r in _relaxations(out)}
    assert rel["include_needs_confirming"]["count_status"] == "not_computed"
    assert rel["budget_plus_10pct"]["shortlist_count"] == 12


def test_an_explanation_that_raises_still_returns_the_search(research, plain, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(research, "_with_explain", broken)
    out = _run(research, _brief("qld_750k"), explain=True)
    assert _comparable({k: out[k] for k in W12_KEYS}) == _comparable(plain["qld_750k"])
    assert out["search_help"] == {"version": 1, "error": "explain: RuntimeError"}


def test_a_relaxation_that_raises_is_named_and_the_rest_are_priced(research, monkeypatch):
    real = research._shortlist_count_for

    def fails_on_50k(agent, brief, rows, prepared):
        if brief.get("budget_max") == 800000:
            raise ValueError("boom")
        return real(agent, brief, rows, prepared)

    monkeypatch.setattr(research, "_shortlist_count_for", fails_on_50k)
    rel = {r["id"]: r for r in _relaxations(_run(research, _brief("qld_750k"),
                                                 explain=True))}
    assert rel["budget_plus_50k"]["count_status"] == "error"
    assert rel["budget_plus_50k"]["error"] == "ValueError"
    assert rel["budget_plus_10pct"]["count_status"] == "exact"


def test_explain_never_replaces_a_key_the_search_already_wrote(research, monkeypatch):
    real = research._log_search

    def plant(response, counts):
        response["needs_confirming"] = "written by the search"
        return real(response, counts)

    monkeypatch.setattr(research, "_log_search", plant)
    out = _run(research, _brief("qld_750k"), explain=True)
    assert out["needs_confirming"] == "written by the search"
    assert out["needs_confirming_count"] == 3
