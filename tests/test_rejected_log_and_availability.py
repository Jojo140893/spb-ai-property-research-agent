"""What the research response lists: stale matches are never cut from the log, and every
shortlisted lot says whether its source ever stated it was available.

1. PENDING ENTRIES AHEAD OF THE CAP. rejected_log is capped for the browser
   (SPB_MAX_REJECTED_LOG, 30). It used to be cut in pipeline order, and the pipeline lists
   hard rejections first, so any search with more than 30 of them never showed one stale
   match: Werribee VIC <= $700k, 4 bed, hid its only pending lot behind 933 scorer
   rejections. A stale match is a real listing a consultant can confirm with one call.
   Pending entries are picked by the pipeline's reason text ("Pending Confirmation ..."),
   not by a 'kind' field only some pipeline versions add (plan critique 18).

2. availability_by_the_sheet ON EVERY SHORTLIST ENTRY. True when the lot is on a price
   list with no status column anywhere, so being listed is the only evidence it is for
   sale. The Research tab badges it "Confirm availability"; without the key the badge
   could never appear.

Run on tests/fixtures/colin_17sep_subset.json, today frozen at 16/09/2026.
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
_FULL_SNAPSHOT_ENV = os.environ.get("SPB_SNAPSHOT_JSON")

FORM = {
    "client_name": "Part C log", "budget_min": 0, "budget_max": 750000,
    "preferred_spending_cap": 750000, "buyer_type": "", "state": "QLD",
    "primary_suburbs": [], "bedrooms_min": 0, "bathrooms_min": 0, "car_spaces_min": 0,
    "storeys_max": 3, "land_size_min_sqm": 0, "house_size_min_sqm": 0,
    "search_radius_km": 0, "product_type": [], "requires_incentive": False,
}
PENDING = "Pending Confirmation"


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


def _pending(log):
    return [e for e in log if str(e.get("reason") or "").startswith(PENDING)]


def _listed(log):
    """The pipeline's own entries: no coverage card, no truncation card."""
    return [e for e in log
            if e.get("property_id") not in ("SNAPSHOT-COVERAGE", "REJECTED-TRUNCATED")]


def _uncapped_and_capped(research, monkeypatch, brief):
    monkeypatch.setattr(research, "MAX_REJECTED_LOG", 10 ** 6)
    everything = research.run_research({"client_brief": brief})
    monkeypatch.setattr(research, "MAX_REJECTED_LOG", 30)
    capped = research.run_research({"client_brief": brief})
    return everything, capped


# ------------------------------------------------------------------ 1. the rejected log

def test_pending_matches_are_listed_ahead_of_thirty_hard_rejections(research, monkeypatch):
    """QLD <= $900k, 4 bed: 26 pending lots and more than 30 hard rejections. Cut in
    pipeline order, the log listed 30 rejections and not one pending lot."""
    brief = _brief(budget_max=900000, preferred_spending_cap=900000, bedrooms_min=4)
    everything, capped = _uncapped_and_capped(research, monkeypatch, brief)
    all_pending = _pending(_listed(everything["rejected_log"]))
    all_hard = [e for e in _listed(everything["rejected_log"]) if e not in all_pending]
    # PREMISE: the cap bites, and there is something pending for it to hide.
    assert all_pending and len(all_hard) > 30, (len(all_pending), len(all_hard))

    listed = _listed(capped["rejected_log"])
    assert [e["address"] for e in _pending(listed)] == [
        e["address"] for e in all_pending[:30]]
    # Pending first, then the hard rejections, each group capped at 30.
    assert listed[:len(_pending(listed))] == _pending(listed)
    assert len(listed) == min(len(all_pending), 30) + 30
    (card,) = [e for e in capped["rejected_log"] if e["property_id"] == "REJECTED-TRUNCATED"]
    assert card["address"] == "%d further rejection(s) not listed" % (
        max(0, len(all_pending) - 30) + len(all_hard) - 30)
    # The count is untouched by what is listed.
    assert capped["rejected_count"] == everything["rejected_count"]
    assert capped["rejected_log"][0]["property_id"] == "SNAPSHOT-COVERAGE"


def test_the_cap_applies_to_each_group_and_reads_the_reason_text(research):
    hard = [{"property_id": "H%d" % i, "address": "h%d" % i, "reason": "Bedrooms (3) below",
             "kind": "rejection"} for i in range(40)]
    # No 'kind' on these, exactly as kommo_agent writes them today.
    pending = [{"property_id": "P%d" % i, "address": "p%d" % i,
                "reason": PENDING + " - excluded from final list until verified or "
                                    "consultant-approved"} for i in range(35)]
    kept, pending_left, hard_left = research._capped_rejected_log(hard + pending, 30)
    assert kept == pending[:30] + hard[:30]
    assert (pending_left, hard_left) == (5, 10)
    kept, pending_left, hard_left = research._capped_rejected_log(hard[:3] + pending[:2], 30)
    assert kept == pending[:2] + hard[:3] and (pending_left, hard_left) == (0, 0)


def test_on_the_full_snapshot_werribee_lists_its_pending_lot(monkeypatch):
    """Plan W12 acceptance 3, on whatever full snapshot SPB_SNAPSHOT_JSON names."""
    path = _FULL_SNAPSHOT_ENV
    if not path or not Path(path).exists() or Path(path).resolve() == SUBSET.resolve():
        pytest.skip("SPB_SNAPSHOT_JSON does not point at a full snapshot (local-only check)")
    mp, research = _research_on(path)
    try:
        brief = _brief(state="VIC", primary_suburbs=["Werribee"], budget_max=700000,
                       preferred_spending_cap=700000, bedrooms_min=4)
        everything, capped = _uncapped_and_capped(research, monkeypatch, brief)
    finally:
        mp.undo()
    all_pending = _pending(_listed(everything["rejected_log"]))
    assert all_pending, "premise: the Werribee brief has a pending lot on this snapshot"
    assert len(_listed(everything["rejected_log"])) > 30, "premise: the cap bites"
    assert [e["address"] for e in _pending(capped["rejected_log"])] == [
        e["address"] for e in all_pending[:30]]


# ------------------------------------------------------ 2. availability on the shortlist

@pytest.fixture(scope="module")
def no_budget(research):
    brief = _brief(budget_max=NO_CEILING, preferred_spending_cap=NO_CEILING)
    return brief, research.run_research({"client_brief": brief})


def test_every_shortlist_entry_says_whether_the_sheet_is_the_only_evidence(research,
                                                                           no_budget):
    brief, out = no_budget
    rows, _path = _candidates.load_snapshot()
    packages, _counts = _candidates.build_packages(brief, rows, today=_Sep16.now())
    by_sheet = {p["content_hash"]: p["availability_by_the_sheet"] for p in packages}
    stated = {str(r.get("content_hash") or ""): str(r.get("availability_status") or "").strip()
              for r in rows if not r.get("superseded_by")}

    flags = [entry["availability_by_the_sheet"] for entry in out["shortlist"]]
    # PREMISE: this search shortlists both kinds, so a constant cannot pass.
    assert True in flags and False in flags, "premise: by-the-sheet and stated lots both"
    for entry in out["shortlist"]:
        assert entry["availability_by_the_sheet"] is by_sheet[entry["content_hash"]]
        assert entry["availability_status"] == stated[entry["content_hash"]]
    assert {e["availability_status"] for e in out["shortlist"]
            if not e["availability_by_the_sheet"]} - {""}, "premise: stated statuses too"


def test_a_candidate_that_carries_the_flag_itself_is_believed(research):
    """Once kommo_agent copies the flag onto CandidateProperty (plan W10), that wins."""
    from tests.test_benchmark_buildings import _candidate

    bare = research._shortlist_entry(_candidate())
    assert bare["availability_by_the_sheet"] is False and bare["availability_status"] == ""

    carried = _candidate(content_hash="v2:x")
    carried.availability_by_the_sheet = True
    carried.availability_status = "For Sale"
    entry = research._shortlist_entry(carried, {"v2:x": {
        "availability_by_the_sheet": False, "availability_status": "Available"}})
    assert entry["availability_by_the_sheet"] is True
    assert entry["availability_status"] == "For Sale"
    # A blank carried status is "not carried": the row's own statement fills it.
    carried.availability_status = ""
    entry = research._shortlist_entry(carried, {"v2:x": {
        "availability_by_the_sheet": False, "availability_status": "Available"}})
    assert entry["availability_status"] == "Available"

    looked_up = research._shortlist_entry(_candidate(content_hash="v2:y"), {"v2:y": {
        "availability_by_the_sheet": True, "availability_status": ""}})
    assert looked_up["availability_by_the_sheet"] is True
