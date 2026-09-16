"""run_property_research's per-package loop, moved into score_candidates and nothing else.

Part C's explain pass (W13) has to score the packages a search scored again -- for the
needs-confirming rows and for every "what if" relaxation -- without writing an audit row,
a client report or a Kommo payload each time. So the loop became
KommoPropertyResearchAgent.score_candidates(brief, packages) -> (processed, rejected), and
run_property_research calls it.

That move had to change nothing. tests/fixtures/golden_score_candidates.json was written
by THIS file's __main__ from the code BEFORE the extraction, with today frozen at
16/09/2026, for three briefs:

  * qld_750k                 Colin's search: 16 packages, shortlisted, pending, rejected
  * werribee_vic_700k_4bed   a suburb brief, so the requested-suburb and bedroom gates run
  * qld_no_budget            the form's blank maximum: every QLD package, 501 of them

Compared: shortlist ids, scores and order; the rejected_log text; and kommo_payload with
its dates masked. The changes W10 makes ON PURPOSE are excluded here and pinned
separately: a card carries its row's own date_checked (tests/test_candidate_dates.py),
and rejected entries gain `codes`, with `kind` on pending ones.

Never regenerate the golden to make a failure go away. A failure here means scoring
moved, and that is the thing this file exists to catch.

    python tests/test_score_candidates_golden.py --write [--snapshot <17 Sep stock.json>]
"""
import argparse
import copy
import importlib.util
import json
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
for _p in (str(APP / "api"), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_spec = importlib.util.spec_from_file_location(
    "make_colin_subset", APP / "tests" / "fixtures" / "make_colin_subset.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

GOLDEN_PATH = APP / "tests" / "fixtures" / "golden_score_candidates.json"
REGISTRY_CSV = APP / "api" / "_data" / "builders_public.csv"

# Literal briefs, every field the Research form posts (gen._FORM), as W04's goldens are.
BRIEFS = {
    "qld_750k": gen._brief(),
    "werribee_vic_700k_4bed": gen._brief(
        state="VIC", primary_suburbs=["Werribee"], budget_max=700000,
        preferred_spending_cap=700000, bedrooms_min=4),
    "qld_no_budget": gen._brief(budget_max=gen.MAX_SAFE,
                                preferred_spending_cap=gen.MAX_SAFE),
}

# The keys every rejected entry had before W10. W10 adds `codes` to every entry and
# `kind` to the pending ones; nothing else may appear.
LEGACY_REJECTED_KEYS = ("property_id", "address", "reason", "advisories", "kind")
W10_REJECTED_KEYS = {"codes", "kind"}
PENDING_REASON = ("Pending Confirmation - excluded from final list until verified or "
                  "consultant-approved")

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2})?")


def _mask_dates(value):
    if isinstance(value, str):
        return _DATE.sub("<date>", value)
    if isinstance(value, dict):
        return {k: _mask_dates(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask_dates(v) for v in value]
    return value


# ------------------------------------------------------------------ the pipeline

@contextmanager
def scratch_config():
    """Audit DB and client reports into a temp dir: never a *.db inside the repo."""
    import config
    saved = (config.DATABASE_PATH, config.OUTPUT_DIR)
    # ignore_cleanup_errors: on Windows the audit DB's handle outlives the run.
    with tempfile.TemporaryDirectory(prefix="spb_w10_golden_",
                                     ignore_cleanup_errors=True) as tmp:
        config.DATABASE_PATH = Path(tmp) / "audit.db"
        config.OUTPUT_DIR = Path(tmp)
        try:
            yield
        finally:
            config.DATABASE_PATH, config.OUTPUT_DIR = saved


def fresh_agent(rows):
    """A pipeline object no other test has taught anything.

    The api's get_agent() is one per process, and run_research teaches its geo index the
    estates of whatever snapshot it last read -- so a golden run through it would depend
    on which tests ran first. This one learns this snapshot's estates, reads the committed
    public registry, has no market comparables, and cannot scrape.
    """
    import _bootstrap
    from kommo_agent import KommoPropertyResearchAgent
    agent = KommoPropertyResearchAgent(csv_filepath=str(REGISTRY_CSV))
    _bootstrap._disarm_sources(agent)
    agent.benchmark_engine.comparables = []
    agent.geo.know_estates([(r.get("estate_name"), r.get("suburb"), r.get("state"))
                            for r in rows
                            if r.get("estate_name") and not r.get("superseded_by")])
    return agent


def packages_for(brief, rows):
    """What api/research.py hands the pipeline: build_packages, then the same dedupe."""
    import _candidates
    from sources.dedupe import DedupeEngine
    packages, _counts = _candidates.build_packages(dict(brief), rows, today=gen.TODAY)
    return DedupeEngine.deduplicate(packages)


def run(agent, brief, rows):
    packages = packages_for(brief, rows)
    assert packages, "a golden brief must reach the pipeline, or it proves nothing"
    return agent.run_property_research(dict(brief), copy.deepcopy(packages)), packages


def legacy_kind(entry):
    """`kind` as the pre-W10 code wrote it: pending entries had none."""
    kind = entry.get("kind")
    return None if kind == "pending_confirmation" else kind


def record(result):
    rejected = []
    for e in result["rejected_log"]:
        row = [e.get(k) for k in LEGACY_REJECTED_KEYS]
        row[LEGACY_REJECTED_KEYS.index("kind")] = legacy_kind(e)
        rejected.append(row)
    return gen.scrub_urls({
        "shortlist": [[p.property_id, p.content_hash,
                       p.scoring.total_score if p.scoring else 0,
                       p.recommendation.value, p.verification_status.value]
                      for p in result["shortlist"]],
        "pending": [e["property_id"] for e in result["rejected_log"]
                    if e.get("reason") == PENDING_REASON],
        "rejected_log": rejected,
        "shortlist_count": result["shortlist_count"],
        "rejected_count": result["rejected_count"],
        "kommo_payload": _mask_dates(result["kommo_payload"]),
    })


def full_record(result):
    """Local-only: a digest, so no full-snapshot catalogue text is committed."""
    rec = record(result)
    return {"digest": gen.digest(rec),
            "shortlist_hashes": [s[1] for s in rec["shortlist"]],
            "pending_count": len(rec["pending"]),
            "rejected_count": rec["rejected_count"]}


# ------------------------------------------------------------------ fixtures

GOLDEN = json.loads(GOLDEN_PATH.read_text(encoding="utf-8")) if GOLDEN_PATH.exists() else {}


@pytest.fixture(scope="module")
def subset_rows():
    return gen.load_rows(gen.FIXTURE)


@pytest.fixture(scope="module")
def subset_agent(subset_rows):
    with scratch_config():
        yield fresh_agent(subset_rows)


@pytest.fixture(scope="module")
def full_rows():
    import os
    must = os.environ.get("SPB_REQUIRE_FULL_SNAPSHOT_GOLDENS") == "1"
    path = os.environ.get("SPB_SNAPSHOT_JSON")
    why = None
    rows = None
    if not path or not Path(path).exists():
        why = "SPB_SNAPSHOT_JSON does not point at a snapshot file"
    else:
        rows = gen.load_rows(path)
        if gen.fingerprint(rows) != GOLDEN.get("full_snapshot_fingerprint"):
            why = "SPB_SNAPSHOT_JSON is not the 17 Sep snapshot the golden was saved from"
    if why:
        if must:
            pytest.fail("full-snapshot golden required but not checkable: " + why)
        pytest.skip("full-snapshot golden is local-only: " + why)
    return rows


@pytest.fixture(scope="module")
def full_agent(full_rows):
    with scratch_config():
        yield fresh_agent(full_rows)


# ------------------------------------------------------------------ the golden

def test_the_golden_is_frozen_and_matches_the_fixture(subset_rows):
    assert GOLDEN, "tests/fixtures/golden_score_candidates.json is missing"
    assert GOLDEN["today"] == "16/09/2026" == gen.TODAY.strftime("%d/%m/%Y")
    assert GOLDEN["subset_fingerprint"] == gen.fingerprint(subset_rows), (
        "the subset fixture changed without regenerating this golden")
    assert set(GOLDEN["briefs"]) == set(BRIEFS)
    for name, brief in BRIEFS.items():
        assert GOLDEN["briefs"][name]["brief"] == brief, name
    # The golden must exercise all three outcomes, or "identical" is a weak claim.
    qld = GOLDEN["briefs"]["qld_750k"]["subset"]
    assert qld["shortlist"] and qld["pending"]
    assert any(r[LEGACY_REJECTED_KEYS.index("kind")] == "rejection"
               for r in qld["rejected_log"])
    assert len(GOLDEN["briefs"]["qld_no_budget"]["subset"]["shortlist"]) > 100


def _first_difference(mine, saved):
    for i, (a, b) in enumerate(zip(mine, saved)):
        if a != b:
            return i, a, b
    return len(saved), mine[len(saved):len(saved) + 1], saved[len(mine):len(mine) + 1]


@pytest.mark.parametrize("name", list(BRIEFS))
def test_the_pipeline_output_equals_the_pre_extraction_golden(name, subset_rows,
                                                              subset_agent):
    saved = GOLDEN["briefs"][name]["subset"]
    result, _packages = run(subset_agent, BRIEFS[name], subset_rows)
    mine = record(result)
    for key in ("shortlist", "pending", "rejected_log"):
        if mine[key] != saved[key]:
            pytest.fail("%s: %s moved (length %d vs golden %d), first difference: %r"
                        % (name, key, len(mine[key]), len(saved[key]),
                           _first_difference(mine[key], saved[key])))
    assert mine["shortlist_count"] == saved["shortlist_count"]
    assert mine["rejected_count"] == saved["rejected_count"]
    assert mine["kommo_payload"] == saved["kommo_payload"]
    assert mine == saved


@pytest.mark.parametrize("name", list(BRIEFS))
def test_the_pipeline_output_equals_the_golden_on_the_full_snapshot(name, full_rows,
                                                                    full_agent):
    saved = GOLDEN["briefs"][name]["full_snapshot"]
    result, _packages = run(full_agent, BRIEFS[name], full_rows)
    assert full_record(result) == saved


# ------------------------------------------------------------------ what W10 adds

@pytest.mark.parametrize("name", list(BRIEFS))
def test_pending_confirmation_is_the_processed_candidates_that_are_not_verified(
        name, subset_rows, subset_agent):
    result, packages = run(subset_agent, BRIEFS[name], subset_rows)
    processed, _rejected = subset_agent.score_candidates(dict(BRIEFS[name]),
                                                         copy.deepcopy(packages))
    unconfirmed = [c.property_id for c in processed
                   if c.verification_status.value != "Verified" and not c.consultant_approved]
    assert [c.property_id for c in result["pending_confirmation"]] == unconfirmed
    assert unconfirmed == GOLDEN["briefs"][name]["subset"]["pending"]
    shortlisted = {c.property_id for c in result["shortlist"]}
    assert not shortlisted & set(unconfirmed), "a pending candidate reached the shortlist"


@pytest.mark.parametrize("name", list(BRIEFS))
def test_score_candidates_is_the_loop_and_writes_nothing(name, subset_rows, subset_agent,
                                                         monkeypatch):
    result, packages = run(subset_agent, BRIEFS[name], subset_rows)

    def refuse(*_a, **_k):
        raise AssertionError("score_candidates wrote an audit row or built a payload")
    monkeypatch.setattr(subset_agent.db, "save_research_run", refuse)
    monkeypatch.setattr(subset_agent, "_build_kommo_update_payload", refuse)
    import client_report
    monkeypatch.setattr(client_report.ClientReportGenerator, "generate_html", refuse)

    processed, rejected = subset_agent.score_candidates(dict(BRIEFS[name]),
                                                        copy.deepcopy(packages))
    # Ranked, and exactly the shortlist plus the pending candidates, in that ranking.
    scores = [c.scoring.total_score for c in processed]
    assert scores == sorted(scores, reverse=True)
    ranked = {c.property_id: i for i, c in enumerate(processed)}
    assert sorted(ranked) == sorted([c.property_id for c in result["shortlist"]]
                                    + [c.property_id for c in result["pending_confirmation"]])
    assert [c.property_id for c in result["shortlist"]] == sorted(
        (c.property_id for c in result["shortlist"]), key=ranked.get)
    # The hard rejections, as run_property_research logged them, and nothing else.
    assert rejected == [e for e in result["rejected_log"] if e.get("kind") == "rejection"]
    # A ClientBrief is accepted as well as the raw dict.
    from brief_parser import ClientBriefParser
    again, again_rejected = subset_agent.score_candidates(
        ClientBriefParser.parse_dict(dict(BRIEFS[name])), copy.deepcopy(packages))
    assert [c.property_id for c in again] == [c.property_id for c in processed]
    assert again_rejected == rejected


@pytest.mark.parametrize("name", list(BRIEFS))
def test_every_rejected_entry_carries_codes(name, subset_rows, subset_agent):
    from scoring_engine import REJECTION_CODES
    result, _packages = run(subset_agent, BRIEFS[name], subset_rows)
    assert result["rejected_log"]
    for entry in result["rejected_log"]:
        assert set(entry) - set(LEGACY_REJECTED_KEYS) <= W10_REJECTED_KEYS, entry
        if entry.get("reason") == PENDING_REASON:
            assert entry["kind"] == "pending_confirmation"
            assert entry["codes"] == ["pending_confirmation"]
        else:
            assert entry["kind"] == "rejection"
            assert entry["codes"] and set(entry["codes"]) <= set(REJECTION_CODES), entry
            assert len(entry["codes"]) == len(entry["reason"].split("; ")), entry


# ------------------------------------------------------------------ writing it

def _write(snapshot):
    subset = gen.load_rows(gen.FIXTURE)
    out = {
        "note": ("run_property_research output saved BEFORE score_candidates was "
                 "extracted (W10). Card dates and the W10 keys codes/kind are excluded "
                 "by record(); never regenerate to hide a change."),
        "today": gen.TODAY.strftime("%d/%m/%Y"),
        "subset_fingerprint": gen.fingerprint(subset),
        "briefs": {n: {"brief": b} for n, b in BRIEFS.items()},
    }
    with scratch_config():
        agent = fresh_agent(subset)
        for name, brief in BRIEFS.items():
            out["briefs"][name]["subset"] = record(run(agent, brief, subset)[0])
    if snapshot:
        full = gen.load_rows(snapshot)
        out["full_snapshot_fingerprint"] = gen.fingerprint(full)
        with scratch_config():
            agent = fresh_agent(full)
            for name, brief in BRIEFS.items():
                out["briefs"][name]["full_snapshot"] = full_record(run(agent, brief, full)[0])
    GOLDEN_PATH.write_text(json.dumps(out, indent=1, ensure_ascii=True) + "\n",
                           encoding="utf-8")
    print("wrote", GOLDEN_PATH)


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", required=True)
    parser.add_argument("--snapshot")
    args = parser.parse_args()
    _write(args.snapshot)
