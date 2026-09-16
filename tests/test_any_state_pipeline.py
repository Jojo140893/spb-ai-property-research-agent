"""Any state reaches the pipeline as Any state: labelled, covered and measured per lot.

W06 stopped the brief parser turning a blank state into 'QLD'. The pipeline still read
brief.state in five places, and '' is not a state:

  * the research record said " - /General";
  * builder coverage relied on the registry's reading of '';
  * the search area was drawn "inside" a state called '';
  * the benchmark matched comparables on state '' -- nothing ever matches;
  * every lot was measured from the brief's suburbs in state '', so geo placed nothing,
    and Any state + Springfield + 20 km rejected all 600 candidates as "Location could
    not be verified" (0 results; 1 QLD result while the parser still said QLD).

Each lot is now benchmarked and measured in its OWN state, and a live crawl gets no state
filter at all.
"""
import copy
import sys
from datetime import datetime
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
for _p in (str(APP / "api"), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.test_score_candidates_golden import (  # noqa: E402
    fresh_agent, gen, packages_for, scratch_config)


def _springfield(state):
    return gen._brief(state=state, primary_suburbs=["Springfield"], search_radius_km=20)


@pytest.fixture(scope="module")
def subset_rows():
    return gen.load_rows(gen.FIXTURE)


@pytest.fixture(scope="module")
def agent(subset_rows):
    with scratch_config():
        yield fresh_agent(subset_rows)


def _run(agent, brief, rows):
    packages = packages_for(brief, rows)
    assert packages
    return agent.run_property_research(dict(brief), copy.deepcopy(packages))


def test_any_state_radius_search_finds_lots_in_every_state_named_springfield(
        agent, subset_rows):
    any_state = _run(agent, _springfield(""), subset_rows)
    qld = _run(agent, _springfield("QLD"), subset_rows)

    states = {p.state for p in any_state["shortlist"]}
    assert states - {"QLD"}, "no lot outside QLD within 20 km of any Springfield"
    for p in any_state["shortlist"]:
        assert p.distance_km_from_target is not None and p.distance_km_from_target <= 20, (
            p.content_hash, p.state, p.suburb, p.distance_km_from_target)
    # And nothing the QLD search finds is lost by widening it to every state.
    assert qld["shortlist"], "the QLD search must find something, or this proves nothing"
    assert ({p.content_hash for p in qld["shortlist"]}
            <= {p.content_hash for p in any_state["shortlist"]})


def test_the_record_and_builder_coverage_say_any_state(agent, subset_rows):
    result = _run(agent, _springfield(""), subset_rows)
    assert " - Any state/Springfield - " in result["research_record_id"]
    assert "QLD/" not in result["research_record_id"]
    assert " - /" not in result["research_record_id"]
    cov = result["builder_coverage"]
    assert cov["in_scope_for_state"] == cov["total_in_directory"] > 0
    assert result["kommo_payload"]["research_record_id"] == result["research_record_id"]


def test_a_named_state_is_still_the_record_label(agent, subset_rows):
    result = _run(agent, gen._brief(), subset_rows)
    assert " - QLD/General - " in result["research_record_id"]


# The research record id is written in two places: kommo_agent when there are packages,
# and api/research.py's zero-packages branch, which returns before the pipeline (and the
# brief parser) ever runs. A blank-name or Any-state test that finds packages only ever
# reaches the first, where the parser already says 'Unnamed Client' -- so it could never
# fail. These briefs are chosen to find NOTHING on the fixture, and the test proves it.
@pytest.mark.parametrize("brief,starts,label", [
    (gen._brief(client_name="", budget_max=100000, preferred_spending_cap=100000),
     "Unnamed Client - ", " - QLD/General - "),
    (gen._brief(client_name="", state="", budget_max=40000, preferred_spending_cap=40000),
     "Unnamed Client - ", " - Any state/General - "),
])
def test_the_zero_packages_record_id_names_the_client_and_the_state(
        brief, starts, label, monkeypatch):
    import _bootstrap
    import _candidates
    import research
    monkeypatch.setenv("SPB_SNAPSHOT_JSON", str(gen.FIXTURE))
    packages, _counts = _candidates.build_packages(dict(brief), gen.load_rows(gen.FIXTURE))
    assert packages == [], "the premise: this brief must find no packages"

    def not_called(*_a, **_k):
        raise AssertionError("the pipeline ran, so this is not the zero-packages branch")
    monkeypatch.setattr(_bootstrap.get_agent(), "run_property_research", not_called)

    out = research.run_research({"client_brief": dict(brief)})
    assert out["shortlist_count"] == 0
    assert out["research_record_id"].startswith(starts), out["research_record_id"]
    assert label in out["research_record_id"], out["research_record_id"]
    assert out["kommo_payload"]["research_record_id"] == out["research_record_id"]


class _FixtureToday(datetime):
    """build_packages' today, frozen on the day the fixture was cut (16/09/2026)."""
    @classmethod
    def now(cls, tz=None):
        return gen.TODAY if tz is None else gen.TODAY.replace(tzinfo=tz)


def test_the_pipeline_record_id_for_a_blank_name_and_any_state(monkeypatch):
    """The same brief shape WITH packages goes through kommo_agent instead."""
    import _bootstrap
    import _candidates
    import research
    monkeypatch.setenv("SPB_SNAPSHOT_JSON", str(gen.FIXTURE))
    # Today frozen, or this ages with the fixture: run_research reads the real clock, and
    # from about 30/09/2026 every fixture row is past the 14-day rule, the shortlist is
    # empty, and "the pipeline ran" could no longer be read off shortlist_count.
    monkeypatch.setattr(_candidates, "datetime", _FixtureToday)
    agent = _bootstrap.get_agent()
    calls = []
    real = agent.run_property_research

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)
    monkeypatch.setattr(agent, "run_property_research", spy)

    out = research.run_research({"client_brief": gen._brief(client_name="", state="")})
    assert calls == [1], "the zero-packages branch answered, so kommo_agent's id is untested"
    assert out["shortlist_count"] > 0
    assert out["research_record_id"].startswith("Unnamed Client - Any state/General - "), (
        out["research_record_id"])


def test_a_lot_is_measured_from_the_named_suburb_in_its_own_state(agent):
    """Hand-built, no fixture: Tarneit VIC is about 9 km from Werribee VIC."""
    brief = gen._brief(state="", primary_suburbs=["Werribee"], search_radius_km=20)
    package = {
        "lot_address": "Lot 5 Test Street", "suburb": "Tarneit", "state": "VIC",
        "builder_name": "Hattan Homes", "bedrooms": 4, "bathrooms": 2, "car_spaces": 2,
        "storeys": 1, "land_size_sqm": 350, "house_size_sqm": 180,
        "land_price": 250000, "build_price": 400000, "advertised_package_price": 650000,
        "verified": True, "risks": [], "date_checked": "15/09/2026",
    }
    processed, rejected = agent.score_candidates(brief, [package])
    assert not rejected, rejected
    assert len(processed) == 1
    assert 0 < processed[0].distance_km_from_target <= 20


def test_the_benchmark_matches_comparables_in_the_lots_own_state(agent, monkeypatch):
    """Critique #17: kommo_agent.py:172 benchmarked every Any-state lot in state ''."""
    comparables = [
        {"suburb": "Tarneit", "state": "VIC", "bedrooms": 4, "price": price,
         "rent_weekly": 600, "land_sqm": 350, "source": "test", "date_checked": "2026-09-01"}
        for price in (640000, 655000, 670000)]
    monkeypatch.setattr(agent.benchmark_engine, "comparables", comparables)
    package = {
        "lot_address": "Lot 6 Test Street", "suburb": "Tarneit", "state": "VIC",
        "builder_name": "Hattan Homes", "bedrooms": 4, "bathrooms": 2, "car_spaces": 2,
        "storeys": 1, "land_size_sqm": 350, "house_size_sqm": 180,
        "land_price": 250000, "build_price": 400000, "advertised_package_price": 650000,
        "verified": True, "risks": [], "date_checked": "15/09/2026",
    }
    for state in ("", "VIC"):
        processed, _rejected = agent.score_candidates(gen._brief(state=state),
                                                      [dict(package)])
        benchmark = processed[0].benchmark
        assert benchmark["benchmarked"] is True, (state, benchmark)
        assert benchmark.get("basis") != "internal", (state, benchmark)
        assert benchmark["avg_market_price"], (state, benchmark)


class _Capture:
    def __init__(self):
        self.filters = []

    def __call__(self, filters):
        self.filters.append(dict(filters))
        return []


@pytest.mark.parametrize("radius", [0, 20])
def test_a_live_crawl_for_any_state_sends_no_state_filter(agent, monkeypatch, radius):
    """server.py's path: no packages, so the sources are searched. Stubbed, never real."""
    eagent, portal, drive = _Capture(), _Capture(), _Capture()
    monkeypatch.setattr(agent.eagent_source, "search", eagent)
    monkeypatch.setattr(agent.portal_source, "search", portal)
    monkeypatch.setattr(agent.drive_source, "search", drive)

    brief = gen._brief(state="", primary_suburbs=["Springfield"], search_radius_km=radius)
    agent.run_property_research(dict(brief), None)
    for sent in (eagent.filters[0], portal.filters[0]):
        assert "state" not in sent, sent
        # With a radius and no state no area is drawn, so no suburb name may narrow the
        # crawl: the scorer applies the radius to each lot in its own state.
        assert sent["primary_suburbs"] == ([] if radius else ["Springfield"]), sent

    brief = gen._brief(state="QLD", primary_suburbs=["Springfield"], search_radius_km=radius)
    agent.run_property_research(dict(brief), None)
    for sent in (eagent.filters[1], portal.filters[1]):
        assert sent["state"] == "QLD"
        assert "Springfield" in sent["primary_suburbs"]
        if radius:
            assert len(sent["primary_suburbs"]) > 1, "a QLD radius still draws its area"
