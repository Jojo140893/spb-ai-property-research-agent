"""One gate path for build_packages and explain_row, proven against saved outputs.

Part C starts by asking of every listing Colin cannot see: WHICH rule hid it? The answer
has to come from the rules the search actually ran, not from a second copy that drifts,
so build_packages's per-row body became _gate and explain_row calls the same function.
That split had to change nothing, and the goldens were saved before it was made
(tests/fixtures/golden_build_packages.json, today frozen at 16/09/2026).

  * 'subset' goldens run on tests/fixtures/colin_17sep_subset.json, everywhere.
  * 'full_snapshot' goldens need the 17 Sep snapshot itself, which cannot be committed.
    They run when SPB_SNAPSHOT_JSON points at it (matched by fingerprint), and skip with
    the reason otherwise. SPB_REQUIRE_FULL_SNAPSHOT_GOLDENS=1 turns that skip into a
    failure, so a run that must check them cannot pass by not looking.
"""
import importlib.util
import inspect
import json
import os
import re
import sqlite3
import sys
from collections import Counter
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

GOLDEN = json.loads(gen.GOLDEN.read_text(encoding="utf-8"))
TODAY = gen.TODAY
SUBSET_BRIEFS = [n for n, e in GOLDEN["briefs"].items() if "subset" in e]
ALL_BRIEFS = list(GOLDEN["briefs"])
TAGS = ("state_borrowed", "estate_only_place", "available_by_the_sheet",
        "storey_unknown_and_binding", "stale_unverified")


def _brief(name):
    return dict(GOLDEN["briefs"][name]["brief"])


@pytest.fixture(scope="module")
def subset_rows():
    return gen.load_rows(gen.FIXTURE)


@pytest.fixture(scope="module")
def subset_prepared(subset_rows):
    return C.prepare_rows(subset_rows)


@pytest.fixture(scope="module")
def full_rows():
    """The 17 Sep snapshot, or a skip that says exactly why it is not being checked."""
    must = os.environ.get("SPB_REQUIRE_FULL_SNAPSHOT_GOLDENS") == "1"
    path = os.environ.get("SPB_SNAPSHOT_JSON")
    why = None
    rows = None
    if not path or not Path(path).exists():
        why = "SPB_SNAPSHOT_JSON does not point at a snapshot file"
    else:
        rows = gen.load_rows(path)
        if gen.fingerprint(rows) != GOLDEN["full_snapshot_fingerprint"]:
            why = "SPB_SNAPSHOT_JSON is not the 17 Sep snapshot the goldens were saved from"
    if why:
        if must:
            pytest.fail("full-snapshot goldens required but not checkable: " + why)
        pytest.skip("full-snapshot goldens are local-only: " + why)
    return rows


@pytest.fixture(scope="module")
def full_prepared(full_rows):
    return C.prepare_rows(full_rows)


def _pin(monkeypatch, mode):
    """Pin the exceptions-only rule to `mode` for one test.

    _candidates reads SPB_EXCEPTIONS_LIST_MODE once, at import, into EXCEPTIONS_LIST_MODE,
    but _gate reads that module constant on every row, so setting the constant is enough
    and no reload is needed. Every test that depends on the rule pins the mode it is
    about: "confirm" is the shipped behaviour and what the goldens were saved from,
    "available" is the 20/09/2026 promotion, built and shipped OFF. Neither behaviour is
    then left to whatever default the environment running the suite happens to carry.

    AND THE POOL THE GOLDENS WERE SAVED UNDER (7 Oct 2026). Two pool rules changed on
    purpose that day: MAX_CANDIDATES was 600 when every golden here was written and is now
    an emergency valve, off by default; and the same-listing collapse keyed on the card
    alone, where it now also asks whether two rows are the same LOT (same_lot_key). These
    goldens pin that _gate and the pool behave as they did, so they are compared under the
    rules they were saved with. The new rules are asserted by
    tests/test_every_candidate_is_scored.py and tests/test_one_price_list_two_lots.py,
    which also proves on this subset that the lot rule only ever keeps apart rows the old
    rule merged.
    """
    monkeypatch.setattr(C, "EXCEPTIONS_LIST_MODE", mode)
    monkeypatch.setattr(C, "MAX_CANDIDATES", 600)
    monkeypatch.setattr(C, "same_lot_key", lambda package, row: None)
    # And repair round 1's (7 Oct 2026): a fresh row its own list no longer carries, or that
    # is not a home at all, is no longer verified. Asserted by
    # tests/test_fresh_rows_and_copies_7oct.py; pinned off here for the same reason.
    monkeypatch.setattr(C, "fresh_row_guard", lambda *a, **k: None)
    monkeypatch.setattr(C, "not_a_home", lambda *a, **k: None)


# 20/09/2026 added one more always-present package field, availability_by_exception_list
# (see _candidates.EXCEPTIONS_LIST_MODE), written on EVERY package in BOTH modes and False
# unless the row was promoted. make_colin_subset's NEW_PACKAGE_KEYS predates it, so the
# goldens -- saved before the field existed -- are compared with it removed here, exactly
# as that module removes W04's and W11's, and the assertions below still say these are the
# ONLY differences from the saved output.
W18_PACKAGE_KEYS = ("availability_by_exception_list",)
ADDITIVE_PACKAGE_KEYS = set(gen.NEW_PACKAGE_KEYS) | set(W18_PACKAGE_KEYS)

# On every package from W04/W11/W18 on; confirm and confirm_guard only on unverified ones.
ALWAYS_ADDED = {"age_days", "email_first_seen", "list_age_days",
                "availability_by_exception_list"}


def _assert_matches_golden(packages, counts, golden, keep_packages_up_to=60):
    """Legacy output identical, and the W04/W11/W18 additions the only difference.

    Called only under a pinned "confirm": the goldens are the shipped mode's output.
    """
    legacy = set(golden["packages"][0]) if golden.get("packages") else None
    for p in packages:
        added = set(p) & ADDITIVE_PACKAGE_KEYS
        if legacy is not None:
            assert set(p) - legacy == added, set(p) - legacy
        # A default search admits nothing: confirm appears exactly on the packages it
        # could not verify (stale, or demoted by a later capture), never as admitted.
        unverified = p["verified"] is False
        assert added == ALWAYS_ADDED | ({"confirm", "confirm_guard"} if unverified else set())
        assert "availability_unstated" not in p.get("confirm", ()), (
            "a default search must admit nothing")
        # The shipped mode promotes nothing off an exceptions-only price list, so the new
        # field is False on every saved package. This is what pins the goldens to
        # "confirm": with the rule on, the 20/09 promotion puts extra rows in here.
        assert p["availability_by_exception_list"] is False
    extra_counts = {k for k in counts if k in gen.NEW_COUNT_KEYS}
    assert extra_counts == {"counts_in_range", "just_over", "stale_over_limit",
                            "newer_capture_demoted", "confirm_guarded",
                            "lot_page_gone"}, extra_counts
    assert counts["lot_page_gone"] == 0, "the fixture carries no lot_page_gone"
    assert "available_by_exception_list" not in counts, "confirm mode promotes nothing"
    got = gen.golden_record(
        [{k: v for k, v in p.items() if k not in W18_PACKAGE_KEYS} for p in packages],
        counts, keep_packages_up_to)
    assert got["counts"] == golden["counts"], "counts (values or key order) moved"
    assert got["content_hashes"] == golden["content_hashes"], "package set or order moved"
    if "packages" in golden and "packages" in got:
        for mine, saved in zip(got["packages"], golden["packages"]):
            assert mine == saved, "package %s changed" % saved.get("content_hash")
    assert got["packages_digest"] == golden["packages_digest"], "a package field changed"


# ------------------------------------------------------------------------- 1. goldens

def test_the_goldens_are_frozen_to_the_day_the_live_replays_matched(subset_rows):
    assert GOLDEN["today"] == "16/09/2026" == gen.TODAY.strftime("%d/%m/%Y")
    assert GOLDEN["subset_fingerprint"] == gen.fingerprint(subset_rows), (
        "the fixture changed without regenerating the goldens")
    assert len(ALL_BRIEFS) >= 12
    for name in ("qld_750k", "qld_760k", "qld_770k", "variant_A_budget_typed_750",
                 "variant_B_qld_plus_suburb_queensland",
                 "variant_C_anystate_suburb_queensland", "variant_D_anystate_no_suburb",
                 "variant_E_max_blank_pref_750k", "werribee_vic_700k_4bed",
                 "vic_900k_4bed", "nsw_750k"):
        brief = _brief(name)
        # Literal briefs: every field the Research form posts, spelled out.
        assert set(gen._FORM) <= set(brief), name
    assert _brief("variant_A_budget_typed_750")["budget_max"] == 750
    assert _brief("variant_C_anystate_suburb_queensland")["state"] == ""
    assert _brief("variant_E_max_blank_pref_750k")["budget_max"] == 9007199254740991


@pytest.mark.parametrize("name", SUBSET_BRIEFS)
def test_default_output_equals_the_golden_on_the_subset(name, subset_rows, subset_prepared,
                                                        monkeypatch):
    _pin(monkeypatch, "confirm")           # the mode the goldens were saved from
    golden = GOLDEN["briefs"][name]["subset"]
    packages, counts = C.build_packages(_brief(name), subset_rows, today=TODAY)
    _assert_matches_golden(packages, counts, golden)
    again, again_counts = C.build_packages(_brief(name), subset_rows, today=TODAY,
                                           prepared=subset_prepared)
    _assert_matches_golden(again, again_counts, golden)
    assert again == packages and again_counts == counts, "prepared= changed the output"


@pytest.mark.parametrize("name", ALL_BRIEFS)
def test_default_output_equals_the_golden_on_the_full_snapshot(name, full_rows,
                                                                full_prepared, monkeypatch):
    _pin(monkeypatch, "confirm")           # the mode the goldens were saved from
    golden = GOLDEN["briefs"][name]["full_snapshot"]
    packages, counts = C.build_packages(_brief(name), full_rows, today=TODAY)
    _assert_matches_golden(packages, counts, golden, keep_packages_up_to=0)
    again, again_counts = C.build_packages(_brief(name), full_rows, today=TODAY,
                                           prepared=full_prepared)
    assert again == packages and again_counts == counts, "prepared= changed the output"


def test_the_gates_run_in_the_documented_order():
    """GATE_ORDER is what explain_row reports against; it must be the order _gate asks.

    8 Oct 2026: _gate's first, brief-independent stage (superseded, state_unknown) lives in
    _state_stage, which _gate calls before any other gate, so the order is read from both,
    the stage first; the stage returns its code in a tuple, not a GateResult."""
    src = inspect.getsource(C._state_stage) + inspect.getsource(C._gate)
    codes = []
    for code in re.findall(r'return (?:GateResult\(|\()"(\w+)"', src):
        if code not in codes:
            codes.append(code)
    assert tuple(codes) == C.GATE_ORDER


# --------------------------------------------------------------- 2. explain_row adds up

@pytest.mark.parametrize("name", ["qld_750k", "variant_D_anystate_no_suburb",
                                  "qld_600k_800k_4bed_2bath_single_storey",
                                  "anystate_house_land_requires_incentive"])
def test_explain_row_verdicts_add_up_to_the_counts(name, subset_rows, subset_prepared):
    brief = _brief(name)
    _packages, counts = C.build_packages(brief, subset_rows, today=TODAY,
                                         prepared=subset_prepared)
    verdicts = C.explain_rows(brief, subset_rows, prepared=subset_prepared, today=TODAY)
    assert len(verdicts) == counts["snapshot_rows"]
    by_gate = Counter(v["gate"] for _row, v in verdicts)
    assert set(by_gate) <= set(C.GATE_ORDER) | set(C.POOL_OUTCOMES), by_gate
    for code in C.GATE_ORDER + C.POOL_OUTCOMES:
        assert by_gate.get(code, 0) == counts[code], code
    tags = Counter(t for _row, v in verdicts for t in v["detail"]["tags"])
    for tag in TAGS:
        assert tags.get(tag, 0) == counts.get(tag, 0), tag
    missing = Counter(label for _row, v in verdicts
                      for label in v["detail"].get("missing", ()))
    assert dict(missing) == counts["missing_fields"]
    scored = [v["package"]["content_hash"] for _row, v in verdicts if v["gate"] == "scored"]
    assert sorted(scored) == sorted(p["content_hash"] for p in _packages)


def test_explain_row_on_one_row_agrees_with_the_whole_pass(subset_rows, subset_prepared):
    brief = _brief("qld_750k")
    verdicts = C.explain_rows(brief, subset_rows, prepared=subset_prepared, today=TODAY)
    first = {}
    for row, verdict in verdicts:
        first.setdefault(verdict["gate"], (row, verdict))
    assert {"scored", "same_listing_collapsed", "not_available", "superseded",
            "other_state", "availability_unstated", "over_budget"} <= set(first)
    for gate, (row, verdict) in first.items():
        alone = C.explain_row(brief, row, subset_rows, prepared=subset_prepared, today=TODAY)
        assert alone == verdict, gate


def test_the_dawn_lots_carry_their_own_date_and_age(subset_rows, subset_prepared):
    packages, _counts = C.build_packages(_brief("qld_750k"), subset_rows, today=TODAY,
                                         prepared=subset_prepared)
    dawn = {p["content_hash"][:11]: p for p in packages
            if p["content_hash"][:11] in ("v2:aec54350", "v2:30440e59", "v2:325f8457")}
    assert len(dawn) == 3
    for p in dawn.values():
        assert p["date_checked"] == "20/08/2026"
        assert p["age_days"] == 27
        assert p["verified"] is False
    for p in packages:
        seen = C._fresh({"date_checked": p["date_checked"]})[1]
        assert p["age_days"] == (TODAY - seen).days


def test_fresh_keeps_its_three_tuple_and_age_days_is_separate():
    verified, seen, raw = C._fresh({"date_checked": "20/08/2026"}, TODAY)
    assert (verified, seen.day, raw) == (False, 20, "20/08/2026")
    assert C._age_days({"date_checked": "20/08/2026"}, TODAY) == 27
    assert C._age_days({"date_checked": "2026-09-16"}, TODAY) == 0
    assert C._age_days({"date_checked": "last week"}, TODAY) is None
    assert C._age_days({}, TODAY) is None


# ------------------------------------------------------------------------ 3. admit mode

@pytest.mark.parametrize("name", ["qld_750k", "variant_E_max_blank_pref_750k"])
def test_admit_returns_exactly_the_unstated_rows_that_pass_every_later_gate(
        name, subset_rows, subset_prepared, monkeypatch):
    # THE SHIPPED RULE, PINNED. Every blank row goes to "Needs confirming" and none is
    # promoted, so this pool is the whole of what an unstated row can become. What
    # EXCEPTIONS_LIST_MODE=available does with the same rows is asserted in
    # test_available_mode_promotes_the_fresh_blank_rows_it_can_read_in_full below.
    _pin(monkeypatch, "confirm")
    brief = _brief(name)
    default = C.explain_rows(brief, subset_rows, prepared=subset_prepared, today=TODAY)
    unstated = [row for row, v in default if v["gate"] == "availability_unstated"]
    assert unstated

    # INDEPENDENTLY: the same rows with availability stated. Their documents already
    # state availability (or they have none), so no brief-independent map moves, and a
    # plain default search then shows which of them clear every later gate.
    marked = {id(r) for r in unstated}
    restated = [dict(r, availability_status="Available") if id(r) in marked else r
                for r in subset_rows]
    restated_prepared = C.prepare_rows(restated)
    assert restated_prepared.states_availability == subset_prepared.states_availability
    unstated_hashes = {u["content_hash"] for u in unstated}
    later = {}
    for row, v in C.explain_rows(brief, restated, prepared=restated_prepared, today=TODAY):
        if row["content_hash"] in unstated_hashes and v["gate"] in C.POOL_OUTCOMES:
            later[row["content_hash"]] = v["package"]
    passing = set(later)
    assert passing, "the brief must reach a later gate for this test to mean anything"
    # W11: of those, only the rows the exceptions-only rule allows are admitted. Asked of
    # admission_refusal row by row, outside any pass.
    by_hash = {u["content_hash"]: u for u in unstated}
    refused = {h: C.admission_refusal(by_hash[h], subset_prepared, TODAY)[0]
               for h in passing}
    eligible = {h for h, why in refused.items() if why is None}

    packages, counts, outcome, _results = C._pass(
        brief, subset_rows, today=TODAY, prepared=subset_prepared,
        admit=("availability_unstated",), only_admitted=True)
    pool = {r["content_hash"] for r in subset_rows
            if outcome.get(id(r)) in C.POOL_OUTCOMES}
    assert pool == eligible
    assert counts["admitted"] == {"availability_unstated": len(eligible)}
    # A refused row that clears every later gate stops where the default search stops it,
    # named; one a later gate stops is counted there, exactly as without admit.
    assert counts["availability_unstated"] == len(passing) - len(eligible)
    assert sum(counts["unstated_not_admitted"].values()) == len(passing) - len(eligible)
    assert counts["unstated_not_admitted"] == dict(
        Counter(why for why in refused.values() if why is not None))

    got, got_counts = C.build_packages(brief, subset_rows, today=TODAY,
                                       prepared=subset_prepared,
                                       admit=("availability_unstated",), only_admitted=True)
    assert got == packages and got_counts == counts
    assert {p["content_hash"] for p in got} == {
        r["content_hash"] for r in subset_rows if outcome.get(id(r)) == "scored"}
    if name == "qld_750k":
        # Colin's search: every in-range unstated row is refused (two Build Only prices,
        # duplex halves, a garbled line, the Wacol lot its sheet was re-read without).
        assert not got
    else:
        # Max blank / $750k preferred admits 48 lots, every one of them off a single
        # document -- Silkwood Homes' E-Agent sheet aa285df384618124, 431 rows in this
        # fixture of which 426 are blank and 5 are On Hold, re-read on 16/09/2026 so
        # age_days is 0 (Walloon Taylors Ridge lot 35, Burpengary Sage lot 534,
        # Rosewood lot 146 v2:d26d5470). Under the shipped rule a list that marks only
        # what has gone has still not said the word "available", so all 48 are offered to
        # confirm and none is shortlisted.
        assert got, "Max blank / $750k preferred admits exceptions-only stock"
        assert len(got) == 48
        shortlist, plain_counts = C.build_packages(brief, subset_rows, today=TODAY,
                                                   prepared=subset_prepared)
        assert not [p for p in shortlist if p["availability_by_exception_list"]]
        assert "available_by_exception_list" not in plain_counts
        assert not ({p["content_hash"] for p in shortlist} &
                    {p["content_hash"] for p in got}), "no lot may sit in both pools"
    for p in got:
        assert p["availability_by_exception_list"] is False
        assert "availability_unstated" in p["confirm"]
        assert p["confirm_guard"] is None
        assert p["verified"] is False
        twin = later[p["content_hash"]]
        strip = ("confirm", "confirm_guard", "verified")
        assert {k: v for k, v in p.items() if k not in strip} == \
            {k: v for k, v in twin.items() if k not in strip}


def test_admitted_rows_never_share_the_confirmed_pool(subset_rows, subset_prepared):
    """Plan section 1: a separate admit pass with its own cap. Sharing the pool cut
    confirmed stock the real search scores, so it is refused, not offered."""
    brief = _brief("variant_E_max_blank_pref_750k")
    with pytest.raises(ValueError, match="pool of their own"):
        C.build_packages(brief, subset_rows, today=TODAY, prepared=subset_prepared,
                         admit=("availability_unstated",))
    # only_admitted with nothing admitted would be an empty pool that reads as
    # "nothing needs confirming".
    with pytest.raises(ValueError, match="pool of their own"):
        C.build_packages(brief, subset_rows, today=TODAY, prepared=subset_prepared,
                         only_admitted=True)


@pytest.mark.parametrize("name", ["variant_E_max_blank_pref_750k", "qld_750k"])
def test_explain_with_admit_reports_each_row_from_the_pool_it_is_really_in(
        name, subset_rows, subset_prepared, monkeypatch):
    """A confirmed row's fate comes from the confirmed search, an admitted row's from the
    admitted pass. At Max blank / $750k preferred a shared pool reported 181 rows the
    search scores as truncated.

    The shipped rule, pinned: nothing is promoted, so every blank row's fate comes from
    the admitted pass and seen_promoted stays at zero.
    """
    _pin(monkeypatch, "confirm")
    brief = _brief(name)
    admit = ("availability_unstated",)
    default = C.explain_rows(brief, subset_rows, prepared=subset_prepared, today=TODAY)
    with_admit = C.explain_rows(brief, subset_rows, prepared=subset_prepared, today=TODAY,
                                admit=admit)
    _p, _c, admitted_fate, _r = C._pass(brief, subset_rows, today=TODAY,
                                        prepared=subset_prepared, admit=admit,
                                        only_admitted=True)
    seen_admitted = Counter()
    seen_promoted = 0
    for (row, plain), (_same, verdict) in zip(default, with_admit):
        if plain["gate"] != "availability_unstated":
            assert verdict == plain, (row["content_hash"], plain["gate"], verdict["gate"])
            # A promoted row comes through HERE, from the confirmed search, because the
            # exceptions-only rule is decided in _gate and admit= cannot move it.
            if (plain["package"] or {}).get("availability_by_exception_list"):
                assert plain["gate"] == "scored"
                seen_promoted += 1
            continue
        if verdict["package"] is None:
            continue                     # stopped by a later gate, or refused admission
        assert "availability_unstated" in verdict["package"]["confirm"]
        assert verdict["gate"] == admitted_fate[id(row)]
        seen_admitted[verdict["gate"]] += 1
    if name == "qld_750k":
        assert not seen_admitted, "Colin's six in-range unstated rows are all refused"
    else:
        assert seen_admitted["scored"] > 0
    assert not seen_promoted, (
        "the shipped rule promotes nothing, so no package carries the badge")
    # One row at a time says the same, for every kind of verdict the pass produced.
    first = {}
    for row, verdict in with_admit:
        first.setdefault((verdict["gate"], "confirm" in (verdict["package"] or {})),
                         (row, verdict))
    for (_gate, _adm), (row, verdict) in first.items():
        assert C.explain_row(brief, row, subset_rows, prepared=subset_prepared,
                             today=TODAY, admit=admit) == verdict


def test_only_the_unstated_gate_can_be_admitted(subset_rows, subset_prepared):
    with pytest.raises(ValueError):
        C.build_packages(_brief("qld_750k"), subset_rows, today=TODAY,
                         prepared=subset_prepared, admit=("not_available",))


# ------------------------------------------- 3b. the exceptions-only rule, mode by mode
#
# SPB_EXCEPTIONS_LIST_MODE=available is the 20/09/2026 behaviour: a blank row on a price
# list that marks only the lots that have GONE, read inside FRESH_DAYS and past both
# admission_refusal and promotion_refusal, is shown as a RESULT carrying the badge instead
# of being offered under "Needs confirming". Every test here pins the mode it exercises
# rather than trusting the default, so the pair keeps passing whichever way the switch
# ships: these two pin it on and everything above pins it off.

def test_available_mode_promotes_the_fresh_blank_rows_it_can_read_in_full(
        subset_rows, subset_prepared, monkeypatch):
    """Max blank / $750k preferred, the brief this rule was written for.

    The shipped rule offers 48 lots under "Needs confirming", all off one document --
    Silkwood Homes' E-Agent sheet aa285df384618124, 431 rows in this fixture of which 426
    are blank and 5 are On Hold, re-read on 16/09/2026 so age_days is 0. FOUR of the 48 are
    promoted to the shortlist (badged, verified, no confirm). The other 44 are refused by
    promotion_refusal -- their stored line is cut at 110 characters, so a status word could
    be past the end, and a row we cannot read in full is not recommended.

    A REFUSED ROW MUST LEAVE BY THE availability_unstated GATE, which is what the last
    assertion here is for. It used to fall through and be ADMITTED into the confirmed pool
    instead, carrying confirm=['availability_unstated'] on a pass whose ctx.admit is empty.
    On the live snapshot that put 89 such rows into the pool priced under the 600th, which
    dropped the cut from $1,171,900 to $1,113,900 and pushed 42 verified lots off the end.
    Nothing the shipped rule shortlists is lost: all 501 stay.
    """
    brief = _brief("variant_E_max_blank_pref_750k")

    _pin(monkeypatch, "confirm")
    shipped, shipped_counts = C.build_packages(brief, subset_rows, today=TODAY,
                                               prepared=subset_prepared)
    offered, _offered_counts = C.build_packages(
        brief, subset_rows, today=TODAY, prepared=subset_prepared,
        admit=("availability_unstated",), only_admitted=True)
    assert len(shipped) == 501 and len(offered) == 48

    _pin(monkeypatch, "available")
    shortlist, counts = C.build_packages(brief, subset_rows, today=TODAY,
                                         prepared=subset_prepared)
    assert len(shortlist) == 505
    assert {p["content_hash"] for p in shipped} <= {p["content_hash"] for p in shortlist}
    added = {p["content_hash"] for p in shortlist} - {p["content_hash"] for p in shipped}
    assert added <= {p["content_hash"] for p in offered}, (
        "the rule may only move rows the shipped rule offers to confirm")

    badged = [p for p in shortlist if p["availability_by_exception_list"]]
    assert len(badged) == 4 == counts["available_by_exception_list"]
    assert {p["content_hash"] for p in badged} == added, (
        "the only rows added to the shortlist are the promoted ones")
    for p in badged:
        # Promoted, not admitted: confirm and confirm_guard are what route a package into
        # "Needs confirming", and a promoted row carries neither.
        assert p["verified"] is True
        assert "confirm" not in p and "confirm_guard" not in p
        assert p["date_checked"] == "16/09/2026" and p["age_days"] == 0
    assert "available_by_exception_list" not in shipped_counts

    # THE 44 REFUSED ROWS ARE NOT IN THE CONFIRMED POOL AT ALL. This is the assertion the
    # 42-lot eviction was hiding behind: they must leave _gate by the availability_unstated
    # gate, exactly as they do with the mode off, and reach the consultant only through the
    # admit pass -- which still offers all 48, refused and promoted alike.
    assert counts["not_promoted_line_truncated"] == 44
    refused = {p["content_hash"] for p in offered} - added
    assert len(refused) == 44
    assert not (refused & {p["content_hash"] for p in shortlist}), (
        "a promotion-refused row was admitted into the confirmed pool; the main pass "
        "admits nothing, so it must return the availability_unstated gate result")
    assert not [p for p in shortlist if p.get("confirm") == ["availability_unstated"]], (
        "no package on this pass may carry availability_unstated: ctx.admit is empty")


def test_available_mode_leaves_colins_own_search_exactly_as_it_ships(
        subset_rows, subset_prepared, monkeypatch):
    """QLD <= $750k: no lot in range sits on an exceptions-only list that can be read in
    full, so turning the rule on returns the same packages, in the same order.

    The first-fail tally does move, and that is the tally doing its job: availability is
    gated before price, so 254 fresh blank rows that used to stop at availability_unstated
    now walk on and are stopped by the budget instead. Every row still lands in exactly
    one bucket.
    """
    brief = _brief("qld_750k")

    _pin(monkeypatch, "confirm")
    shipped, shipped_counts = C.build_packages(brief, subset_rows, today=TODAY,
                                               prepared=subset_prepared)
    _pin(monkeypatch, "available")
    shortlist, counts = C.build_packages(brief, subset_rows, today=TODAY,
                                         prepared=subset_prepared)

    assert shortlist == shipped, "nothing is promoted, so nothing Colin sees may move"
    assert not [p for p in shortlist if p["availability_by_exception_list"]]
    assert "available_by_exception_list" not in counts

    moved = shipped_counts["availability_unstated"] - counts["availability_unstated"]
    assert moved == 254
    assert counts["over_budget"] - shipped_counts["over_budget"] == moved
    buckets = C.GATE_ORDER + C.POOL_OUTCOMES
    assert sum(counts[b] for b in buckets) == counts["snapshot_rows"]
    assert sum(shipped_counts[b] for b in buckets) == shipped_counts["snapshot_rows"]


# -------------------------------------------------------------------- 4. in-range tally

EXPECTED_IN_RANGE_750K = {"available": 11, "availability_unstated": 6,
                          "not_available": 5, "price_unverified": 1}


def test_in_range_tally_for_qld_750k_on_the_subset(subset_rows, subset_prepared):
    """Critique correction 11: not_available is 5, not 3 -- Howard e7012b88, 370ead99,
    fc7cd55f and 4276a713 are Sold, and d9553ad6 is On Hold."""
    brief = _brief("qld_750k")
    _packages, counts = C.build_packages(brief, subset_rows, today=TODAY,
                                         prepared=subset_prepared)
    assert counts["counts_in_range"] == EXPECTED_IN_RANGE_750K
    ctx = C._BriefContext(brief)
    sold = sorted(row["content_hash"][:11] for row, v in
                  C.explain_rows(brief, subset_rows, prepared=subset_prepared, today=TODAY)
                  if v["gate"] == "not_available" and ctx.in_range(C._num(row.get("price"))))
    assert sold == ["v2:370ead99", "v2:4276a713", "v2:d9553ad6", "v2:e7012b88",
                    "v2:fc7cd55f"]


def test_in_range_tally_for_qld_750k_on_the_full_snapshot(full_rows, full_prepared):
    _packages, counts = C.build_packages(_brief("qld_750k"), full_rows, today=TODAY,
                                         prepared=full_prepared)
    assert counts["counts_in_range"] == EXPECTED_IN_RANGE_750K


def test_in_range_uses_the_brief_range_without_headroom():
    ctx = C._BriefContext({"budget_min": 300000, "budget_max": 750000})
    assert ctx.in_range(750000) and ctx.in_range(300000)
    assert not ctx.in_range(750001) and not ctx.in_range(299999) and not ctx.in_range(None)
    assert C._BriefContext({}).in_range(5_000_000)


# ---------------------------------------------------------------------- prepared cache

def test_prepared_rows_are_cached_per_snapshot_file(tmp_path, subset_rows):
    rows = subset_rows[:300]
    path = tmp_path / "stock.json"
    path.write_text(json.dumps({"keys": [], "rows": []}), encoding="utf-8")
    first = C.prepared_for_snapshot(rows, path)
    assert C.prepared_for_snapshot(rows, path) is first
    path.write_text(json.dumps({"keys": [], "rows": [], "x": 1}), encoding="utf-8")
    rewritten = C.prepared_for_snapshot(rows, path)
    assert rewritten is not first                                       # file rewritten
    assert C.prepared_for_snapshot(rows, path) is rewritten
    assert C.prepared_for_snapshot(rows[:200], path) is not rewritten   # other rows


def test_prepared_rows_from_another_snapshot_are_refused(subset_rows, subset_prepared):
    with pytest.raises(ValueError):
        C.build_packages(_brief("qld_750k"), subset_rows[:100], today=TODAY,
                         prepared=subset_prepared)
    # explain_row too, before it gates the row against another catalogue's maps.
    with pytest.raises(ValueError):
        C.explain_row(_brief("qld_750k"), subset_rows[0], subset_rows[:100], today=TODAY,
                      prepared=subset_prepared)
    with pytest.raises(ValueError):
        C.explain_rows(_brief("qld_750k"), subset_rows[:100], today=TODAY,
                       prepared=subset_prepared)


# ------------------------------------------------- snapshot rows joined to database-only

def _raw_database(path, rows):
    """A database shaped like the harvest's: no build-time columns at all."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE buildings (id INTEGER PRIMARY KEY, content_hash TEXT, "
                 "builder_name TEXT, price REAL, state TEXT, source_text TEXT, "
                 "checked_at TEXT, first_seen TEXT)")
    for n, r in enumerate(rows, start=1):
        conn.execute("INSERT INTO buildings VALUES (?,?,?,?,?,?,?,?)",
                     (n, r["content_hash"], r.get("builder_name"), r.get("price"),
                      r.get("state"),
                      # Text naming another state's locality, so a flat merge would give
                      # suburb_quality something to read.
                      "Available Wollert Titled Townhouse %s Riverhills VIC 3750" % n,
                      "2026-08-20T09:46:43", "17/08/2026"))
    conn.commit()
    conn.close()


def test_explain_row_works_from_snapshot_rows_joined_to_database_fields(
        tmp_path, monkeypatch, subset_rows, subset_prepared):
    """Critique correction 19: _read_sqlite cannot read the harvest database, so tools
    load the snapshot and join what only the database has, by content_hash."""
    brief = _brief("qld_750k")
    in_state = [r for r in subset_rows if str(r.get("state") or "").upper() in ("QLD", "")]
    stored = {r["content_hash"]: r for r in in_state[:400]}
    stored.update({r["content_hash"]: r for r in subset_rows
                   if r["content_hash"].startswith("v2:aec54350")})
    db = tmp_path / "raw.db"
    _raw_database(db, list(stored.values()))
    before = db.stat().st_mtime_ns

    with pytest.raises(sqlite3.OperationalError, match="not a snapshot database"):
        C._read_sqlite(db)

    # READ-ONLY BY THE OPEN MODE, not by the absence of a write: a SELECT leaves the file
    # untouched in any mode, so the mtime check alone could never catch a writable open.
    opened = []
    real_connect = sqlite3.connect

    def spy(database, *args, **kwargs):
        opened.append((str(database), kwargs.get("uri")))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(C.sqlite3, "connect", spy)
    joined = C.join_db_fields(subset_rows, db)
    monkeypatch.setattr(C.sqlite3, "connect", real_connect)
    assert len(opened) == 1 and opened[0][1] is True, opened
    assert re.search(r"[?&]mode=ro(&|$)", opened[0][0]), opened
    assert db.stat().st_mtime_ns == before, "the database must be opened read-only"
    dawn = [r for r in joined if r["content_hash"].startswith("v2:aec54350")]
    assert len(dawn) == 1 and dawn[0]["db"]["first_seen"] == "17/08/2026"
    assert dawn[0]["db"]["id"] and "Wollert" in dawn[0]["db"]["source_text"]
    for row in joined:
        assert not {"source_text", "checked_at", "first_seen"} & set(row), (
            "database fields leaked onto the row, where suburb_quality can read them")
    assert {r["content_hash"] for r in joined if r["db"] is not None} == set(stored)

    plain = C.explain_rows(brief, subset_rows, prepared=subset_prepared, today=TODAY)
    through_join = C.explain_rows(brief, joined, prepared=C.prepare_rows(joined), today=TODAY)
    assert [v for _r, v in plain] == [v for _r, v in through_join]

    # One joined row, explained against the plain snapshot it was copied from.
    verdict = C.explain_row(brief, dawn[0], subset_rows, prepared=subset_prepared, today=TODAY)
    assert verdict["gate"] == "scored"
    assert verdict["detail"]["date_checked"] == "20/08/2026"
    assert verdict["detail"]["age_days"] == 27

    with pytest.raises(ValueError, match="no column"):
        C.join_db_fields(subset_rows[:5], db, fields=("id", "suburb_source"))


# ------------------------------------------------------------------- fixture hygiene

def test_committed_fixtures_carry_no_links():
    for path in (gen.FIXTURE, gen.GOLDEN):
        text = path.read_text(encoding="utf-8").lower()
        for bad in ("://", "www.", "dropbox", "rlkey", "onedrive", "sharepoint"):
            assert bad not in text, "%s carries %r" % (path.name, bad)
    keys = json.loads(gen.FIXTURE.read_text(encoding="utf-8"))["keys"]
    for key in keys:
        assert key not in ("source_url", "stocklist_file", "listing_url", "floorplan_url",
                           "brochure_url"), key
        assert not key.startswith(("source_link_", "rea_")), key
