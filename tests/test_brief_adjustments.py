"""A form slip is read the way the consultant meant it, and the response says what changed.

Part C, 16 Sep 2026. Colin searched "QLD under $750k" and got nothing useful. Replayed
against the deployed snapshot, each of these slips on its own turns that search into 0
results, with nothing on screen saying why:

    budget_max 750 or "750k"            a $750 (or $0) ceiling
    no budget_max at all                a $0 ceiling
    min 750000, max 300000              above $750k and below $300k at once
    "Queensland" / "QLD" as a suburb    a suburb called Queensland, in every state
    client name left blank              "Unnamed Client" never reached the empty branch

api/research.py _normalised_brief reads each one as meant and lists the change in
brief_adjustments; brief_used says what was actually searched. These run the real endpoint
on tests/fixtures/colin_17sep_subset.json with today frozen at 16/09/2026 (the day the live
replays matched), and every slip must return EXACTLY the shortlist of the brief it was
meant to be. Revert the normaliser and the slips go back to 0 results.
"""
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

# Every field the Research form posts, as it posts them for Colin's search.
FORM = {
    "client_name": "Part C slips", "budget_min": 0, "budget_max": 750000,
    "preferred_spending_cap": 750000, "buyer_type": "", "state": "QLD",
    "primary_suburbs": [], "bedrooms_min": 0, "bathrooms_min": 0, "car_spaces_min": 0,
    "storeys_max": 3, "land_size_min_sqm": 0, "house_size_min_sqm": 0,
    "search_radius_km": 0, "product_type": [], "requires_incentive": False,
}


class _Sep16(datetime):
    """16/09/2026 for build_packages' freshness rule. Accepts a tz, as datetime.now does."""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 16, 12, 0, tzinfo=tz)


@pytest.fixture(scope="module")
def research():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SPB_SNAPSHOT_JSON", str(SUBSET))
        mp.delenv("GROQ_API_KEY", raising=False)
        mp.setattr(_candidates, "datetime", _Sep16)
        import research as module
        yield module


def _brief(drop=(), **changes):
    brief = json.loads(json.dumps(FORM))
    brief.update(changes)
    for key in drop:
        brief.pop(key, None)
    return brief


def _run(research, brief):
    return research.run_research({"client_brief": brief})


def _hashes(out):
    return sorted(p["content_hash"] for p in out["shortlist"])


def _fields(out):
    return {a.get("field") for a in out.get("brief_adjustments") or []}


@pytest.fixture(scope="module")
def qld_750k(research):
    out = _run(research, _brief())
    # PREMISE, asserted rather than assumed: the brief every slip is compared with finds
    # something, so "the same shortlist" cannot be two empty lists agreeing.
    assert out["shortlist_count"] > 0 and _hashes(out), "premise: QLD <= $750k shortlists"
    return out


# ------------------------------------------------------------------ no slip, no note

def test_a_brief_with_no_slip_is_searched_as_sent_and_says_so(qld_750k):
    assert qld_750k["brief_adjustments"] == []
    used = qld_750k["brief_used"]
    assert used["state"] == "QLD"
    assert (used["budget_min"], used["budget_max"]) == (0, 750000)
    assert used["primary_suburbs"] == [] and used["search_radius_km"] == 0
    assert "client_name" not in used


# ------------------------------------------------------------------------- the budget

@pytest.mark.parametrize("typed", [750, "750", 750.0, "750k", "750K", "$750k", "$750,000",
                                   "750 000", "0.75m"])
def test_a_budget_typed_in_thousands_or_as_text_is_750000(research, qld_750k, typed):
    out = _run(research, _brief(budget_max=typed, preferred_spending_cap=typed))
    assert _hashes(out) == _hashes(qld_750k), typed
    assert out["brief_used"]["budget_max"] == 750000
    assert out["brief_used"]["preferred_spending_cap"] == 750000
    moved = {a["field"]: a for a in out["brief_adjustments"]}
    assert set(moved) == {"budget_max", "preferred_spending_cap"}, out["brief_adjustments"]
    assert moved["budget_max"]["from"] == typed and moved["budget_max"]["to"] == 750000


@pytest.mark.parametrize("drop,value", [(("budget_max",), None), ((), None), ((), 0),
                                        ((), ""), ((), "not a number")])
def test_a_missing_budget_is_no_ceiling_not_a_0_ceiling(research, drop, value):
    """Measured on the full snapshot: QLD with no budget_max key returned 0; with the form's
    own blank-Max value (Number.MAX_SAFE_INTEGER) it returned 378."""
    brief = _brief(drop=drop) if drop else _brief(budget_max=value)
    out = _run(research, brief)
    blank_max = _run(research, _brief(budget_max=NO_CEILING))
    assert blank_max["shortlist_count"] > 0, "premise: a QLD search with no ceiling finds stock"
    assert _hashes(out) == _hashes(blank_max)
    assert out["brief_used"]["budget_max"] == NO_CEILING
    (note,) = [a for a in out["brief_adjustments"] if a["field"] == "budget_max"]
    assert note["to"] == NO_CEILING and "no price ceiling" in note["why"]
    # And the form's own "no maximum" is not itself a slip.
    assert blank_max["brief_adjustments"] == []


def test_a_minimum_above_the_maximum_is_swapped_and_said(research):
    typed = _run(research, _brief(budget_min=750000, budget_max=300000))
    meant = _run(research, _brief(budget_min=300000, budget_max=750000))
    assert meant["shortlist_count"] > 0, "premise: QLD $300k-$750k shortlists"
    assert _hashes(typed) == _hashes(meant)
    assert (typed["brief_used"]["budget_min"], typed["brief_used"]["budget_max"]) == (
        300000, 750000)
    (swap,) = typed["brief_adjustments"]
    assert swap["field"] == "budget_min/budget_max"
    assert swap["from"] == [750000, 300000] and swap["to"] == [300000, 750000]
    assert meant["brief_adjustments"] == []


# ------------------------------------------------------------ a state typed as a suburb

@pytest.mark.parametrize("state,said", [("", "Queensland"), ("", "QLD"), ("", "queensland"),
                                        ("QLD", "Queensland"), ("QLD", " qld ")])
def test_a_state_typed_as_a_suburb_searches_the_state(research, qld_750k, state, said):
    out = _run(research, _brief(state=state, primary_suburbs=[said]))
    assert _hashes(out) == _hashes(qld_750k), (state, said)
    assert out["brief_used"]["state"] == "QLD"
    assert out["brief_used"]["primary_suburbs"] == []
    # Named in both places the Research tab reads: the brief change and the location note.
    assert ("state" if not state else "primary_suburbs") in _fields(out), out[
        "brief_adjustments"]
    (entry,) = [x for x in out["location_resolution"] if x["method"] == "state"]
    assert entry["said"] == said.strip() and entry["used"] == []


def test_a_state_name_that_conflicts_with_the_selected_state_is_kept_and_named(research):
    out = _run(research, _brief(state="VIC", primary_suburbs=["Queensland"]))
    assert out["brief_used"]["state"] == "VIC"
    assert out["brief_used"]["primary_suburbs"] == ["Queensland"]
    (note,) = [a for a in out["brief_adjustments"] if a["field"] == "primary_suburbs"]
    assert "QLD" in note["note"] and "VIC" in note["note"]
    assert not [x for x in out["location_resolution"] if x["method"] == "state"]


def test_a_real_suburb_beside_a_typed_state_is_still_searched(research):
    out = _run(research, _brief(state="", primary_suburbs=["Queensland", "Walloon"]))
    assert out["brief_used"]["state"] == "QLD"
    assert out["brief_used"]["primary_suburbs"] == ["Walloon"]


def test_a_state_spelt_out_in_the_state_field_is_its_code(research, qld_750k):
    out = _run(research, _brief(state="Queensland"))
    assert _hashes(out) == _hashes(qld_750k)
    assert out["brief_used"]["state"] == "QLD"
    assert "state" in _fields(out)


# --------------------------------------------------------------------- where to search

def test_a_radius_with_no_suburb_is_noted_and_changes_nothing(research, qld_750k):
    out = _run(research, _brief(search_radius_km=20))
    assert _hashes(out) == _hashes(qld_750k)
    assert out["shortlist_count"] == qld_750k["shortlist_count"]
    (note,) = out["brief_adjustments"]
    assert note["field"] == "search_radius_km"
    assert note["why"] == "radius ignored: no suburb to measure from"
    assert out["brief_used"]["search_radius_km"] == 0


def test_a_suburb_in_several_states_with_any_state_names_them(research):
    out = _run(research, _brief(state="", primary_suburbs=["Springfield"],
                                budget_max=NO_CEILING))
    (note,) = [a for a in out["brief_adjustments"]
               if a.get("code") == "suburb_in_several_states"]
    for state in ("NSW", "QLD", "VIC"):
        assert state in note["note"], note
    assert out["brief_used"]["state"] == "Any state"
    # With a state chosen there is nothing to say.
    chosen = _run(research, _brief(state="QLD", primary_suburbs=["Springfield"]))
    assert not [a for a in chosen["brief_adjustments"]
                if a.get("code") == "suburb_in_several_states"]


# -------------------------------------------------------------------- the empty branch

def test_a_blank_client_name_on_a_search_with_nothing_to_score_is_unnamed_client(research):
    """CRITIQUE 14: the name fix is on the ZERO-PACKAGE branch, so the brief must reach it.
    QLD <= $100,000 has no packages at all; QLD <= $750k never takes that branch, and
    brief_parser already names the client on the main path, so a test there cannot fail."""
    for blank in ("", None):
        out = _run(research, _brief(client_name=blank, budget_max=100000,
                                    preferred_spending_cap=100000))
        # Premise: this really is the branch that returns before the pipeline runs.
        assert out["shortlist_count"] == 0
        assert out["rejected_log"][0]["property_id"] == "SNAPSHOT-COVERAGE"
        assert len(out["rejected_log"]) == 1
        assert out["qa_failures"] == ["QA Item 2 Failed: No candidate packages evaluated."]
        assert out["research_record_id"].startswith("Unnamed Client - QLD/"), out[
            "research_record_id"]
        assert "Client: Unnamed Client" in out["kommo_payload"]["kommo_internal_note"]


def test_any_state_on_a_search_with_nothing_to_score_is_not_labelled_qld(research):
    out = _run(research, _brief(state="", budget_max=10000, preferred_spending_cap=10000))
    assert out["shortlist_count"] == 0 and len(out["rejected_log"]) == 1, "premise: empty"
    assert " - Any state/General - " in out["research_record_id"]
    assert "QLD/" not in out["research_record_id"]
    assert out["brief_used"]["state"] == "Any state"


def test_both_returns_carry_the_same_keys(research, qld_750k):
    empty = _run(research, _brief(budget_max=100000))
    for out in (qld_750k, empty):
        for key in ("brief_adjustments", "brief_used", "in_state_rows"):
            assert key in out, key
    assert set(empty) == set(qld_750k)


# ----------------------------------------------------------------- the normaliser alone

@pytest.mark.parametrize("typed,amount", [
    ("1.2m", 1200000), ("1.2 million", 1200000), ("$1,200k", 1200000), (".5m", 500000),
    (1200000, 1200000), (9999, 9999000), (1, 1000), (10000, 10000), (750000.0, 750000),
])
def test_money_is_read_as_meant(research, typed, amount):
    brief, _adjustments = research._normalised_brief({"budget_max": typed})
    assert brief["budget_max"] == amount, typed


def test_the_normaliser_returns_the_brief_and_its_adjustments(research):
    brief, adjustments = research._normalised_brief(dict(FORM))
    assert brief == FORM and adjustments == []
    # The list coercion it always did is unchanged.
    brief, _ = research._normalised_brief({**FORM, "primary_suburbs": "Walloon"})
    assert brief["primary_suburbs"] == ["Walloon"]


def test_a_minimum_typed_in_thousands_is_read_as_thousands(research):
    brief, adjustments = research._normalised_brief({**FORM, "budget_min": 300})
    assert brief["budget_min"] == 300000
    assert [a["field"] for a in adjustments] == ["budget_min"]


# --------------------------------------------------------------- the replayable log line

def test_one_log_line_per_search_replays_it(research, capsys):
    """A search that returned nothing on Vercel has to be replayable from its log: the
    logged brief_used, posted back, is the same search."""
    capsys.readouterr()
    slip = _brief(state="", primary_suburbs=["Queensland"], budget_max="750k",
                  client_name="Somebody Private")
    out = _run(research, slip)
    lines = [line for line in capsys.readouterr().out.splitlines()
             if line.startswith('{"event":"spb_research"')]
    assert len(lines) == 1, lines
    logged = json.loads(lines[0])
    assert "Somebody Private" not in lines[0]
    assert logged["brief_used"] == out["brief_used"]
    assert (logged["shortlist_count"], logged["rejected_count"]) == (
        out["shortlist_count"], out["rejected_count"])
    assert logged["counts"]["snapshot_rows"] > 0
    assert set(logged["brief_adjustments"]) == {"state_typed_as_suburb", "budget_from_text"}

    # Posted back exactly as logged: no mapping, no editing.
    again = _run(research, logged["brief_used"])
    assert _hashes(again) == _hashes(out) and again["shortlist_count"] > 0
    assert again["brief_adjustments"] == []


def test_an_any_state_search_replays_from_its_own_log_line(research, capsys):
    capsys.readouterr()
    out = _run(research, _brief(state="", budget_max=500000, preferred_spending_cap=500000))
    (line,) = [x for x in capsys.readouterr().out.splitlines()
               if x.startswith('{"event":"spb_research"')]
    logged = json.loads(line)["brief_used"]
    assert logged["state"] == "Any state"
    again = _run(research, logged)
    assert out["shortlist_count"] > 0, "premise: Any state <= $500k finds stock"
    assert _hashes(again) == _hashes(out)
    assert again["brief_adjustments"] == [] and again["in_state_rows"] == out["in_state_rows"]


def test_the_thousands_rule_and_no_maximum_match_the_research_form(research):
    """index.html echoes "750 reads as $750,000" before the search is sent. If the two
    rules drift, the echo promises a search the server does not run."""
    import re
    page = (APP / "index.html").read_text(encoding="utf-8")
    (below,) = re.findall(r"const THOUSANDS_BELOW = (\d+);", page)
    assert int(below) == research.THOUSANDS_BELOW
    assert "const RESEARCH_NO_CEILING = Number.MAX_SAFE_INTEGER;" in page
    assert research.NO_CEILING == 2 ** 53 - 1
