"""Every candidate is scored (plan rank 1, 7 Oct 2026).

Coleen phoned on 7 Oct: when she runs the same search in E-Agent and in the app, E-Agent
always shows more. The search-funnel audit found why. api/_candidates.py sorted a state's
packages by price and kept the cheapest 600 (MAX_CANDIDATES) BEFORE anything was scored, so
a state-wide search showed 684 of the 4,252 E-Agent lots the app held, and in VIC nothing
above $645,500 could ever appear. The cap existed to protect the function's 30 s limit.

Now MAX_CANDIDATES is an emergency valve, 0 (off) by default; when it is set and binds, the
response says so (candidate_cap) and so does the explanation. The pipeline's Pending split
compares candidates by identity, not by dataclass equality (2.64 s -> 0.60 s on VIC); the
Kommo note lists the top 10 and says how many more; and the endpoint no longer renders a
markdown report per shortlisted lot that no caller read.
"""
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
for _p in (str(APP / "api"), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _candidates as C  # noqa: E402
from tests import _synthetic_stock as S  # noqa: E402


@pytest.fixture(scope="module")
def research(tmp_path_factory):
    path = S.write(tmp_path_factory.mktemp("every"), S.stock(fresh=1500, estates=30))
    mp = pytest.MonkeyPatch()
    mp.setenv("SPB_SNAPSHOT_JSON", str(path))
    mp.delenv("SPB_SHORTLIST_MAX", raising=False)
    mp.delenv("SPB_MAX_CANDIDATES", raising=False)
    mp.setattr(C, "datetime", S.Sep16)
    import research as module
    yield module
    mp.undo()


def test_the_cap_is_off_unless_someone_sets_it():
    import os
    assert os.environ.get("SPB_MAX_CANDIDATES") in (None, ""), "the suite must not set it"
    assert C.MAX_CANDIDATES == 0


def test_build_packages_scores_all_of_5000_rows():
    rows = S.stock(fresh=5000, estates=50)
    packages, counts = C.build_packages(dict(S.BRIEF), rows, today=S.TODAY)
    assert len(packages) == 5000
    assert not counts.get("truncated")
    # The dearest lot is there: the cap kept only the cheapest 600 ($400,000-$999,000).
    assert max(p["advertised_package_price"] for p in packages) == 400000.0 + 1000.0 * 4999


def test_a_state_wide_search_shortlists_every_lot(research):
    out = S.run(research)
    assert out["shortlist_count"] == 1500
    hashes = [e["content_hash"] for e in out["shortlist"]]
    assert len(hashes) == len(set(hashes)) == 1500
    assert "candidate_cap" not in out


def test_a_valve_that_binds_is_never_silent(research, monkeypatch):
    monkeypatch.setattr(C, "MAX_CANDIDATES", 50)
    out = S.run(research, explain=True)
    assert out["shortlist_count"] == 50
    assert out["candidate_cap"]["limit"] == 50 and out["candidate_cap"]["cut"] == 1450
    assert "SPB_MAX_CANDIDATES" in out["candidate_cap"]["why"]
    assert out["search_help"]["funnel"]["capped"] is True
    assert out["search_help"]["candidate_cap"] == {"limit": 50, "cut": 1450}


def test_the_endpoint_renders_no_per_lot_reports(research):
    """'reports' was 43% of an uncapped VIC response and nothing read it: not index.html,
    not the vapi-kommo-hook (checked 7 Oct 2026)."""
    out = S.run(research)
    assert "reports" not in out
    page = (APP / "index.html").read_text(encoding="utf-8")
    assert ".reports" not in page and "['reports']" not in page


def test_the_kommo_note_lists_the_top_10_and_says_how_many_more(research):
    note = S.run(research)["kommo_payload"]["kommo_internal_note"]
    numbered = [line for line in note.splitlines() if line[:1].isdigit() and ". " in line]
    assert len(numbered) == 10
    assert numbered[0].startswith("1. ") and numbered[-1].startswith("10. ")
    assert "... and 1,490 more on the shortlist." in note


def test_the_pending_split_compares_by_identity(research, monkeypatch):
    """run_property_research split Pending from the final pool with `c not in final_pool`:
    the dataclass's field-by-field __eq__ against every pool member, 7.4 million calls on
    a VIC search once every candidate is scored. Identity answers the same question."""
    from schema import CandidateProperty
    calls = {"n": 0}
    real = CandidateProperty.__eq__

    def counting(self, other):
        calls["n"] += 1
        return real(self, other)

    monkeypatch.setattr(CandidateProperty, "__eq__", counting)
    # A valve far above the pool, so this test asks only about the split, cap or no cap.
    monkeypatch.setattr(C, "MAX_CANDIDATES", 100000)
    out = S.run(research)
    assert out["shortlist_count"] == 1500
    assert calls["n"] < 1500, "%d dataclass comparisons" % calls["n"]


def test_the_pending_split_still_logs_every_unverified_candidate(research, tmp_path,
                                                                 monkeypatch):
    """Identity, not equality, but the same answer: a stale (unverified) candidate is still
    logged as pending, and a verified one never is."""
    rows = S.stock(fresh=20, estates=2, stale=7)
    monkeypatch.setenv("SPB_SNAPSHOT_JSON", str(S.write(tmp_path, rows)))
    out = S.run(research)
    assert out["shortlist_count"] == 20
    pending = [e for e in out["rejected_log"] if e.get("kind") == "pending_confirmation"]
    assert sorted(e["address"] for e in pending) == sorted(
        "Lot %d, Old Rise" % (5000 + i) for i in range(7)), [e.get("address") for e in pending]
