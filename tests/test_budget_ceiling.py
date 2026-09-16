"""The budget the client gave is the ceiling. Nothing over it takes a scoring slot.

Colin's QLD <= $750,000 search scored 16 listings and spent 8 of them on rows priced
$752,400 to $799,995, which scoring_engine then rejected on arrival as "exceeds max
budget": a $50,000 headroom copied from the live sources, invisible to him, buying red
cards. The headroom is gone; the rows it used to let through are still counted
(counts['just_over']) so a thin search can say what a slightly higher figure would add.

Replayed through the real run_research on the Colin fixture, today frozen at 16/09/2026
(the day the offline replay matched the live API). The data is not fixed here, so the two
wrong shortlist rows are still the shortlist -- that is W01/W02's job.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
for _p in (str(APP / "api"), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _candidates as C  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "make_colin_subset", APP / "tests" / "fixtures" / "make_colin_subset.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

QLD_750K = dict(gen.BRIEFS[0][1])


@pytest.fixture(scope="module")
def colin_750k(tmp_path_factory):
    """(run_research response, build_packages counts) for QLD <= $750k on the fixture."""
    import research
    captured = {}
    real = C.build_packages

    def frozen(brief_dict, rows, today=None, **kw):
        packages, counts = real(brief_dict, rows, today=gen.TODAY, **kw)
        captured["counts"] = dict(counts)
        return packages, counts

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SPB_SNAPSHOT_JSON", str(gen.FIXTURE))
        mp.setenv("SPB_SCRATCH", str(tmp_path_factory.mktemp("scratch")))
        mp.delenv("SPB_SNAPSHOT_DB", raising=False)
        mp.delenv("SPB_COMP_PROVIDER", raising=False)
        mp.delenv("GROQ_API_KEY", raising=False)
        mp.setattr(C, "build_packages", frozen)
        response = research.run_research({"client_brief": dict(QLD_750K)})
    return response, captured["counts"]


def test_no_listing_is_rejected_for_exceeding_the_budget(colin_750k):
    response, _counts = colin_750k
    reasons = [str(entry.get("reason") or "") for entry in response["rejected_log"]]
    assert reasons, "the search must log something for this to mean anything"
    assert not [r for r in reasons if "exceeds max budget" in r]


def test_the_shortlist_is_what_the_data_gives_until_the_data_is_fixed(colin_750k):
    """v2:1c5ca48e is the Wollert VIC lot filed QLD and v2:43802e2d the Howard lot now On
    Hold -- both wrong, both W01/W02's to remove. Removing the headroom moved neither."""
    response, _counts = colin_750k
    assert sorted(s["content_hash"][:11] for s in response["shortlist"]) == [
        "v2:1c5ca48e", "v2:43802e2d"]
    assert response["shortlist_count"] == 2


def test_the_rows_just_over_are_counted_not_scored(colin_750k):
    _response, counts = colin_750k
    assert counts["just_over"] >= 8
    assert counts["scored"] == 8, "16 before: 8 slots went to rows over $750,000"


def test_the_ceiling_is_the_budget_itself():
    assert C.BUDGET_HEADROOM == 0
    rows = [{"content_hash": str(price), "state": "QLD", "suburb": "Walloon",
             "price": price, "availability_status": "Available", "source_doc": "d",
             "product_type": "House & Land", "lot_address": "Lot %d" % price,
             "date_checked": "15/09/2026"}
            for price in (750000, 750001, 800000, 800001)]
    verdicts = {r["content_hash"]: v for r, v in
                C.explain_rows({"state": "QLD", "budget_max": 750000}, rows, today=gen.TODAY)}
    assert verdicts["750000"]["gate"] == "scored"
    for price in ("750001", "800000"):
        assert verdicts[price]["gate"] == "over_budget"
        assert verdicts[price]["detail"]["just_over"] is True
    assert verdicts["800001"]["gate"] == "over_budget"
    assert "just_over" not in verdicts["800001"]["detail"]
    _packages, counts = C.build_packages({"state": "QLD", "budget_max": 750000}, rows,
                                         today=gen.TODAY)
    assert (counts["over_budget"], counts["just_over"]) == (3, 2)
    # No budget, no band.
    _packages, counts = C.build_packages({"state": "QLD"}, rows, today=gen.TODAY)
    assert (counts["over_budget"], counts["just_over"]) == (0, 0)
