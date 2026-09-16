"""Every in-state row a search looked at is either shortlisted or in the Filtered count.

The Research tab's badge reads "N Qualified (M Filtered)". Colin's QLD <= $750k search said
"2 Qualified (1420 Filtered)" while 632 rows whose availability the source never stated were
left out of M, and so were price_unverified, under_minimum, other_product_type and
no_incentive -- and storey_unknown_and_binding, a tag on rows that are STILL SCORED, was
added, counting those rows twice. The number could not be checked against anything.

Now it can: the response carries in_state_rows (every row not superseded, not in another
state and not stateless) and

    shortlist_count + rejected_count == in_state_rows

Run on tests/fixtures/colin_17sep_subset.json, today frozen at 16/09/2026. Restore the old
sum (api/research.py pre_filtered) and every brief here goes red.
"""
import json
import os
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
# Read at import, before the module fixture below points the variable at the subset.
_FULL_SNAPSHOT_ENV = os.environ.get("SPB_SNAPSHOT_JSON")

FORM = {
    "client_name": "Part C filtered", "budget_min": 0, "budget_max": 750000,
    "preferred_spending_cap": 750000, "buyer_type": "", "state": "QLD",
    "primary_suburbs": [], "bedrooms_min": 0, "bathrooms_min": 0, "car_spaces_min": 0,
    "storeys_max": 3, "land_size_min_sqm": 0, "house_size_min_sqm": 0,
    "search_radius_km": 0, "product_type": [], "requires_incentive": False,
}

BRIEFS = {
    # The five the plan names.
    "qld_750k": {},
    "qld_750k_single_storey": {"storeys_max": 1},
    "qld_no_budget": {"budget_max": None, "preferred_spending_cap": None},
    "vic_700k_4bed_house_and_land_rebate": {
        "state": "VIC", "budget_max": 700000, "preferred_spending_cap": 700000,
        "bedrooms_min": 4, "product_type": ["House & Land"], "requires_incentive": True},
    "nsw_400k_600k": {"state": "NSW", "budget_min": 400000, "budget_max": 600000,
                      "preferred_spending_cap": 600000},
    # And two the subset needs to reach the rest of the counters: the 600-candidate cap
    # (truncated) and a price floor (under_minimum), neither of which the five above hit
    # on QLD-sized stock.
    "any_state_no_budget": {"state": "", "budget_max": NO_CEILING,
                            "preferred_spending_cap": NO_CEILING},
    "any_state_400k_600k": {"state": "", "budget_min": 400000, "budget_max": 600000,
                            "preferred_spending_cap": 600000},
}


class _Sep16(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 16, 12, 0, tzinfo=tz)


def _research_on(snapshot):
    mp = pytest.MonkeyPatch()
    mp.setenv("SPB_SNAPSHOT_JSON", str(snapshot))
    mp.delenv("GROQ_API_KEY", raising=False)
    mp.delenv("SPB_SHORTLIST_MAX", raising=False)
    mp.setattr(_candidates, "datetime", _Sep16)
    import research
    return mp, research


@pytest.fixture(scope="module")
def research():
    mp, module = _research_on(SUBSET)
    yield module
    mp.undo()


def _brief(**changes):
    brief = json.loads(json.dumps(FORM))
    brief.update(changes)
    return brief


def _search(research, capsys, brief):
    """(response, counts) -- counts read off the search's own log line."""
    capsys.readouterr()
    out = research.run_research({"client_brief": brief})
    (line,) = [x for x in capsys.readouterr().out.splitlines()
               if x.startswith('{"event":"spb_research"')]
    return out, json.loads(line)["counts"]


@pytest.fixture(scope="module")
def measured(research):
    """Every brief once, for the tests that look across all of them."""
    import io
    import contextlib
    got = {}
    for name, changes in BRIEFS.items():
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out = research.run_research({"client_brief": _brief(**changes)})
        (line,) = [x for x in buf.getvalue().splitlines()
                   if x.startswith('{"event":"spb_research"')]
        got[name] = (out, json.loads(line)["counts"])
    return got


@pytest.mark.parametrize("name", list(BRIEFS))
def test_shortlisted_plus_filtered_is_every_in_state_row(measured, name):
    out, counts = measured[name]
    out_of_scope = counts["superseded"] + counts["other_state"] + counts["state_unknown"]
    assert out["in_state_rows"] == counts["snapshot_rows"] - out_of_scope
    assert out["in_state_rows"] > 0, "premise: the brief's state has stock in the fixture"
    assert out["shortlist_count"] + out["rejected_count"] == out["in_state_rows"], (
        name, out["shortlist_count"], out["rejected_count"], out["in_state_rows"])


def test_the_briefs_reach_every_counter_the_old_sum_got_wrong(measured):
    """PREMISE for the invariant above: it can only catch a counter the briefs exercise.
    Each of these was missing from the old sum (or, the last, added to it twice)."""
    reached = {code for _out, counts in measured.values()
               for code, n in counts.items() if isinstance(n, int) and n > 0}
    for code in ("availability_unstated", "price_unverified", "under_minimum",
                 "other_product_type", "no_incentive", "same_listing_collapsed",
                 "duplicates_merged", "truncated", "storey_unknown_and_binding"):
        assert code in reached, code


def test_a_row_scored_with_its_storeys_unknown_is_not_filtered_as_well(measured):
    plain, _ = measured["qld_750k"]
    single, counts = measured["qld_750k_single_storey"]
    assert counts["storey_unknown_and_binding"] > 0, "premise: the tag fires"
    # Tagged rows are still scored: the same rows are in scope, so the same total.
    assert single["in_state_rows"] == plain["in_state_rows"]
    assert single["shortlist_count"] + single["rejected_count"] == single["in_state_rows"]


def test_the_empty_branch_filters_every_in_state_row(research, capsys):
    out, counts = _search(research, capsys, _brief(budget_max=100000,
                                                   preferred_spending_cap=100000))
    assert counts["scored"] == 0 and out["shortlist_count"] == 0, "premise: nothing scored"
    assert out["rejected_count"] == out["in_state_rows"] > 0


def test_a_shortlist_cut_by_spb_shortlist_max_is_still_counted(research, capsys, monkeypatch):
    """SPB_SHORTLIST_MAX (unset in production) drops qualifying lots from the end of the
    list without logging them. They were in-state rows, so the count must not lose them."""
    uncapped, _ = _search(research, capsys, _brief())
    assert uncapped["shortlist_count"] >= 2, "premise: more than one lot qualifies"
    monkeypatch.setenv("SPB_SHORTLIST_MAX", "1")
    capped, _ = _search(research, capsys, _brief())
    assert capped["shortlist_count"] == 1
    assert capped["shortlist_count"] + capped["rejected_count"] == capped["in_state_rows"]
    assert capped["in_state_rows"] == uncapped["in_state_rows"]


def test_the_filtered_codes_are_exactly_the_gates_that_stop_an_in_state_row():
    """The sum is written against _candidates' own gate list, so a gate added there
    without being counted here fails loudly rather than leaking out of the badge."""
    import research
    in_scope_gates = [g for g in _candidates.GATE_ORDER if g not in research.OUT_OF_SCOPE_COUNTS]
    pool = [o for o in _candidates.POOL_OUTCOMES if o != "scored"]
    assert set(research.FILTERED_COUNTS) == set(in_scope_gates) | set(pool) | {
        "duplicates_merged"}
    assert set(research.OUT_OF_SCOPE_COUNTS) == set(_candidates._OUT_OF_SCOPE)
    assert "storey_unknown_and_binding" not in research.FILTERED_COUNTS


# ------------------------------------------------------- the full 17 Sep snapshot, locally

def _full_snapshot():
    path = _FULL_SNAPSHOT_ENV
    if not path or not Path(path).exists():
        pytest.skip("SPB_SNAPSHOT_JSON does not point at a snapshot (local-only check)")
    if Path(path).resolve() == SUBSET.resolve():
        pytest.skip("SPB_SNAPSHOT_JSON is the subset fixture, already checked above")
    return path


def test_on_the_full_snapshot_qld_750k_filtered_is_in_state_less_shortlisted(capsys):
    """Plan W12 acceptance 2, on whatever full snapshot SPB_SNAPSHOT_JSON names."""
    mp, research = _research_on(_full_snapshot())
    try:
        out, counts = _search(research, capsys, _brief())
    finally:
        mp.undo()
    assert counts["availability_unstated"] > 0, "premise: unstated rows are in scope"
    assert out["rejected_count"] == out["in_state_rows"] - out["shortlist_count"]
