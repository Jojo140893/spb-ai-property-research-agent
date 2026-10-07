"""run_property_research's per-package loop, moved into score_candidates and nothing else.

Part C's explain pass (W13) has to score the packages a search scored again -- for the
needs-confirming rows and for every "what if" relaxation -- without writing an audit row,
a client report or a Kommo payload each time. So the loop became
KommoPropertyResearchAgent.score_candidates(brief, packages) -> (processed, rejected), and
run_property_research calls it.

That move had to change nothing. tests/fixtures/golden_score_candidates.json was written
by THIS file's __main__ from the code BEFORE the extraction, with today frozen at
16/09/2026, for three briefs:

  * qld_750k                 Colin's search: shortlisted, pending, rejected (16 packages
                             when saved; 8 since W11 dropped the $50k budget headroom)
  * werribee_vic_700k_4bed   a suburb brief, so the requested-suburb and bedroom gates run
  * qld_no_budget            the form's blank maximum: every QLD package. 501 when this
                             line was written; 497 in the golden as it now stands, which
                             is what W11's enrichment left and what its 402 shortlisted
                             plus 95 pending add back up to.

Compared: shortlist ids, scores and order; the rejected_log text; and kommo_payload with
its dates masked. The changes W10 makes ON PURPOSE are excluded here and pinned
separately: a card carries its row's own date_checked (tests/test_candidate_dates.py),
and rejected entries gain `codes`, with `kind` on pending ones.

Never regenerate the golden to make a failure go away. A failure here means scoring
moved, and that is the thing this file exists to catch. When the PACKAGES this pipeline
is fed change on purpose (W11 did), regenerate with the pre-extraction kommo_agent.py
(merge-base 8d86f05) against the new api/_candidates.py, and record the proof in the
golden's "history".

    python tests/test_score_candidates_golden.py --write [--snapshot <17 Sep stock.json>]

MODE-PINNED, 20 Sep 2026 -- and why the golden was NOT regenerated. This is the W11 case
again, a PACKAGES change and not a scoring one: SPB_EXCEPTIONS_LIST_MODE now defaults to
"available", so a blank row on an exceptions-only price list (the vendor marks only what
has gone -- Silkwood's QLD sheet, 245 blank rows and 5 On Hold) that was read inside
FRESH_DAYS is promoted in _gate instead of being held back as availability_unstated. On
the subset that moves qld_no_budget, and only qld_no_budget: 4 rows are promoted and the
other 44 of the 48 are refused by promotion_refusal for a line cut at 110 characters.

The golden records CONFIRM-mode output and the snapshot behind it is not on this machine,
so packages_for pins the mode rather than re-recording something it cannot verify. See
its docstring. Regenerating for "available" needs the snapshot whose fingerprint is
4d07b8ef..., and must add a GOLDEN_HISTORY entry with its proof.

Measured before and after by content_hash, never by property_id, because the answer turns
on that distinction:

  * all 48 new rows are the promoted ones, one for one -- every one Verified, Recommend,
    carrying availability_by_exception_list, and every one read 16/09/2026, i.e. today,
    so none of them is a stale row sneaking past _fresh. Glenvale 5, Walloon 4, Lilywood
    4, Caboolture 4, Yarrabilba 3, Morayfield 3, then a long tail of ones and twos.
  * not one of the 402 rows already shortlisted was lost, none changed score,
    recommendation or verification, and the 402 keep their order -- the old shortlist is
    a subsequence of the new one.
  * `pending` and `rejected_log` are the same 95 entries in the same order. Nothing was
    promoted out of the confirm pool here and nothing fell into it, so no lot is offered
    twice.

So both failures are renumbering, not movement: PROP-n is positional, assigned per search
in kommo_agent.py, and 48 more packages shift every id after the first promotion --
shortlist PROP-31 -> PROP-32 at index 2, pending PROP-281 -> PROP-311 at index 3, with
identical content_hashes behind both. qld_750k and werribee_vic_700k_4bed do not move at
all: QLD <= $750k has no exceptions-list stock in range and the suburb brief is VIC.

Regenerate with the command above, and only with someone holding the 17 Sep stock.json
the full_snapshot half was saved from (fingerprint 4d07b8ef...). The vercel_site/stock.json
in this worktree is a later harvest and does not match it, which is why the three
full-snapshot cases skip; running --write WITHOUT --snapshot would drop
full_snapshot_fingerprint and those goldens with it.
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
        ours = (Path(tmp) / "audit.db", Path(tmp))
        config.DATABASE_PATH, config.OUTPUT_DIR = ours
        try:
            yield
        finally:
            # Only what is still ours. If api/_bootstrap.get_config() redirected these
            # meanwhile (a test called run_research), putting the old values back would
            # undo that for the rest of the process -- and a later search would write
            # its client report into the repo's output/.
            if config.DATABASE_PATH == ours[0]:
                config.DATABASE_PATH = saved[0]
            if config.OUTPUT_DIR == ours[1]:
                config.OUTPUT_DIR = saved[1]


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
    """What api/research.py hands the pipeline: build_packages, then the same dedupe.

    PINNED TO CONFIRM MODE, because that is the mode the golden records. It was written
    before SPB_EXCEPTIONS_LIST_MODE existed, and it cannot be re-recorded on this machine:
    the snapshot behind it (full_snapshot_fingerprint 4d07b8ef...) is not here -- neither
    worktree's stock.json matches -- and `--write` without `--snapshot` would drop that
    fingerprint and the three full-snapshot goldens with it.

    Pinning keeps this file testing the one thing it was built to test: that moving the
    per-package loop into score_candidates changed nothing. The available-mode packages
    are asserted directly, on the same subset, by
    tests/test_gate_single_path.py::test_available_mode_promotes_the_fresh_blank_rows_it_can_read_in_full.
    """
    import _candidates
    from sources.dedupe import DedupeEngine
    was = (_candidates.EXCEPTIONS_LIST_MODE, _candidates.MAX_CANDIDATES,
           _candidates.same_lot_key)
    _candidates.EXCEPTIONS_LIST_MODE = "confirm"
    # THE POOL THE GOLDEN WAS SAVED UNDER (7 Oct 2026), pinned the way the mode is: the
    # 600-row cap (an emergency valve now, off by default) and the card-only merge keys
    # (the collapse and api/research.py's dedupe now also ask whether two rows are the same
    # lot). This file pins that moving the loop changed no SCORING; the new pool rules are
    # asserted, on this same subset, by tests/test_one_price_list_two_lots.py.
    _candidates.MAX_CANDIDATES = 600
    _candidates.same_lot_key = lambda package, row: None
    try:
        packages, _counts = _candidates.build_packages(dict(brief), rows, today=gen.TODAY)
    finally:
        (_candidates.EXCEPTIONS_LIST_MODE, _candidates.MAX_CANDIDATES,
         _candidates.same_lot_key) = was
    return DedupeEngine.deduplicate(packages)


def _note_top(note, top=10):
    """A golden Kommo note as kommo_agent writes it since 7 Oct 2026: the first `top`
    numbered shortlist lines, then "... and N more on the shortlist." when there were more.
    The golden was saved when the note listed every shortlisted lot (402 on qld_no_budget);
    the shortlist itself is still compared in full, line for line, above."""
    lines = note.split("\n")
    numbered = [i for i, line in enumerate(lines) if re.match(r"^\d+\. ", line)]
    if len(numbered) <= top:
        return note
    cut = len(numbered) - top
    keep = lines[:numbered[top]] + ["... and {:,} more on the shortlist.".format(cut)]
    return "\n".join(keep + lines[numbered[-1] + 1:])


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
    # Hard rejections come from the suburb brief: since W11 (budget headroom 0) every
    # package Colin's brief scores is in budget, and its only rejections are pending lots.
    werribee = GOLDEN["briefs"]["werribee_vic_700k_4bed"]["subset"]
    assert sum(r[LEGACY_REJECTED_KEYS.index("kind")] == "rejection"
               for r in werribee["rejected_log"]) > 100
    assert len(GOLDEN["briefs"]["qld_no_budget"]["subset"]["shortlist"]) > 100
    assert GOLDEN["briefs"]["qld_no_budget"]["subset"]["pending"]


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
    saved = copy.deepcopy(saved)
    saved["kommo_payload"]["kommo_internal_note"] = _note_top(
        saved["kommo_payload"]["kommo_internal_note"])
    assert mine["kommo_payload"] == saved["kommo_payload"]
    assert mine == saved


@pytest.mark.parametrize("name", list(BRIEFS))
def test_the_pipeline_output_equals_the_golden_on_the_full_snapshot(name, full_rows,
                                                                    full_agent):
    saved = GOLDEN["briefs"][name]["full_snapshot"]
    result, _packages = run(full_agent, BRIEFS[name], full_rows)
    mine = full_record(result)
    # The digest also covers the Kommo note, which lists the top 10 since 7 Oct 2026 where
    # the golden's listed every lot, and only the 17 Sep snapshot could re-record it. The
    # scoring it exists to pin is compared here hash by hash.
    for key in ("shortlist_hashes", "pending_count", "rejected_count"):
        assert mine[key] == saved[key], key


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
        # Why and how each regeneration was proven; a rewrite keeps it.
        "history": GOLDEN.get("history", []),
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
