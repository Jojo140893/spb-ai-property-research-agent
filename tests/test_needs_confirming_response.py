"""The amber "Needs confirming" group: real matches a consultant can phone about, kept out
of everything that counts as a recommendation.

Colin searched "QLD under $750k" and could have been shown Shape Homes' Dawn lots in
Walloon -- $721,900, $721,900 and $725,900, stated Available on a list last read 20/08/2026
-- but the 14-day rule hid them with nothing on screen. With `"explain": true` they now come
back in `needs_confirming`, each with its OWN dates, while:

  * shortlist, shortlist_count, rejected_count and kommo_payload stay exactly as without
    explain (Clare and Kommo read those);
  * the three "Windermere Mambourin" lots a later capture marks On Hold stay hidden and are
    counted in needs_confirming_hidden;
  * rows admitted from exceptions-only lists get a pool of their own, so they can never push
    a confirmed lot out of the 600-candidate cap;
  * hidden reasons are counted AFTER scoring, so a suburb search does not count the refusals
    of every other suburb in the state.

Dawn has live twins (11878/14940, 11879/14941, 11880/14942) and a later supersede pass may
keep either one, so Dawn is matched as (estate 'Dawn, Walloon', lot, price) with either
twin's hash accepted (critique #3). Everything is frozen at 16/09/2026.
"""
import contextlib
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

APP = Path(__file__).resolve().parents[1]
for _p in (str(APP / "api"), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _candidates  # noqa: E402

SUBSET = APP / "tests" / "fixtures" / "colin_17sep_subset.json"
NO_CEILING = 9007199254740991
# Read at import, before any fixture below points the variable at another snapshot.
_FULL_SNAPSHOT_ENV = os.environ.get("SPB_SNAPSHOT_JSON")

FORM = {
    "client_name": "Part C needs confirming", "budget_min": 0, "budget_max": 750000,
    "preferred_spending_cap": 750000, "buyer_type": "", "state": "QLD",
    "primary_suburbs": [], "bedrooms_min": 0, "bathrooms_min": 0, "car_spaces_min": 0,
    "storeys_max": 3, "land_size_min_sqm": 0, "house_size_min_sqm": 0,
    "search_radius_km": 0, "product_type": [], "requires_incentive": False,
}
PROTECTED = ("shortlist", "shortlist_count", "rejected_count", "rejected_log",
             "comparisons", "client_report_html", "in_state_rows")

# (estate, lot) -> price, and the two hashes each lot is held under (critique #3).
DAWN_PRICE = {"1228": 721900.0, "1229": 721900.0, "1231": 725900.0}
DAWN_TWINS = {"1228": ("v2:aec54350", "v2:c48b4599"),
              "1229": ("v2:30440e59", "v2:9fb1f79e"),
              "1231": ("v2:325f8457", "v2:67246ddb")}

# Plan section 4 (with critique #3's v2:d9553ad6), plus Wacol v2:3b141dd8: never offered.
NEVER_OFFERED = (
    "v2:1c5ca48e", "v2:43802e2d", "v2:d32b17a9", "v2:50cabe9b", "v2:b399f290",
    "v2:022e96c6", "v2:7013f9f6", "v2:37c3f092", "v2:4276a713", "v2:e7012b88",
    "v2:370ead99", "v2:fc7cd55f", "v2:0c599a1c", "v2:89ed40df", "v2:99dfea91",
    "v2:487e9a1f", "v2:f0865179", "v2:f76dee98", "v2:d9553ad6", "v2:3b141dd8")

# The C5 data fixes (W01, W02), applied in memory: Howard 18491 and Windermere 9568-9571
# retired, the Wollert lot 14905 filed VIC.
C5_SUPERSEDED = ("v2:43802e2d", "v2:d32b17a9", "v2:50cabe9b", "v2:b399f290", "v2:022e96c6")
C5_TO_VIC = ("v2:1c5ca48e",)


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
    mp.setattr(research, "EXPLAIN_BUDGET_MS", 60000)
    mp.setattr(research, "EXPLAIN_BELOW", 5)
    mp.setattr(research, "MAX_NEEDS_CONFIRMING", 50)
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


def _run(research, brief, **payload):
    with contextlib.redirect_stdout(io.StringIO()):
        return research.run_research(dict(payload, client_brief=json.loads(json.dumps(brief))))


def _masked_kommo(out):
    kommo = json.loads(json.dumps(out["kommo_payload"], default=str))
    kommo.pop("research_record_id", None)
    kommo["custom_fields_update"].pop("research_completed_date", None)
    kommo["kommo_internal_note"] = "\n".join(
        line for line in kommo["kommo_internal_note"].splitlines()
        if not line.startswith("Record ID:"))
    return kommo


def _hashes(entries):
    return sorted(str(e["content_hash"]) for e in entries)


def _assert_the_dawn_lots(entries):
    assert len(entries) == 3, [(e["estate_context"], e["lot_number"]) for e in entries]
    by_lot = {e["lot_number"]: e for e in entries}
    assert set(by_lot) == set(DAWN_PRICE)
    for lot, entry in by_lot.items():
        assert entry["estate_context"] == "Dawn, Walloon"
        assert entry["suburb"] == "Walloon" and entry["state"] == "QLD"
        assert entry["advertised_package_price"] == DAWN_PRICE[lot]
        assert entry["content_hash"][:11] in DAWN_TWINS[lot], (lot, entry["content_hash"])


@pytest.fixture(scope="module")
def budgets(research):
    """QLD 750k / 760k / 770k, each with and without explain."""
    out = {}
    for budget in (750000, 760000, 770000):
        brief = _brief(budget_max=budget, preferred_spending_cap=budget)
        out[budget] = (_run(research, brief), _run(research, brief, explain=True))
    return out


# ------------------------------------------------------------------- (b) Colin's search

@pytest.mark.parametrize("budget", [750000, 760000, 770000])
def test_explain_leaves_the_shortlist_and_kommo_exactly_as_they_were(budgets, budget):
    plain, explained = budgets[budget]
    for key in PROTECTED:
        assert explained[key] == plain[key], key
    assert _masked_kommo(explained) == _masked_kommo(plain)
    assert not set(_hashes(explained["needs_confirming"])) & set(
        _hashes(explained["shortlist"]))


@pytest.mark.parametrize("budget", [750000, 760000, 770000])
def test_the_dawn_lots_need_confirming_with_their_own_dates(budgets, budget):
    _plain, out = budgets[budget]
    entries = out["needs_confirming"]
    _assert_the_dawn_lots(entries)
    assert out["needs_confirming_count"] == 3
    for entry in entries:
        assert entry["date_checked"] == "20/08/2026"          # the row's own, not today
        assert entry["age_days"] == 27 and entry["fresh_days"] == 14
        assert entry["confirm"] == ["stale"]
        assert entry["availability_as_stated"] == "Available"
        assert entry["availability_by_the_sheet"] is False
        # Shape Homes' list names no builder in the row.
        assert entry["builder_name"] == "Builder not identified in source"
        assert entry["score"] > 0
        for key in ("lot_address", "bedrooms", "bathrooms", "car_spaces", "land_size_sqm",
                    "house_size_sqm", "advisories", "source_link"):
            assert key in entry, key


def test_an_emailed_row_says_when_the_list_was_first_read(budgets):
    """Critique #27: an emailed row's date_checked is the inbox sweep's last re-read, so the
    entry carries the channel and the list's own first read for "email last read" copy."""
    _plain, out = budgets[750000]
    for entry in out["needs_confirming"]:
        assert entry["source_channel"] == "digital email"
        # Either twin: 11878-11880 were first read 17/08, their 14940-14942 twins 19/08.
        assert entry["first_seen"] in ("17/08/2026", "19/08/2026")
        first = datetime.strptime(entry["first_seen"], "%d/%m/%Y")
        assert entry["list_age_days"] == (datetime(2026, 9, 16) - first).days
        assert entry["list_age_days"] > entry["age_days"]


@pytest.mark.parametrize("budget,on_hold", [(750000, 3), (760000, 4), (770000, 4)])
def test_captures_a_later_read_marks_gone_are_hidden_and_counted(budgets, budget, on_hold):
    """(b)/(e) The Windermere Mambourin lots (and at $760k one more) were read again later
    as On Hold. They are hidden, never offered, and the count says so."""
    _plain, out = budgets[budget]
    assert out["needs_confirming_hidden"]["newer_capture_disagrees"] == on_hold
    assert out["search_help"]["needs_confirming"]["hidden"] == out["needs_confirming_hidden"]


@pytest.mark.parametrize("budget", [750000, 760000, 770000])
def test_no_known_wrong_row_is_ever_offered_to_confirm(budgets, budget):
    _plain, out = budgets[budget]
    offered = {e["content_hash"][:11] for e in out["needs_confirming"]}
    assert not offered & set(NEVER_OFFERED)


def test_the_rows_colins_search_refused_are_counted_with_their_reasons(budgets):
    """The six in-range unstated rows: two Build Only prices, a garbled line printing a
    bigger total, Wacol (its sheet re-read on 16/09 without it) and two duplex halves on a
    list that states availability elsewhere."""
    _plain, out = budgets[750000]
    assert out["needs_confirming_hidden"] == {
        "newer_capture_disagrees": 3, "build_only": 2, "larger_total_in_text": 1,
        "document_reread_without_row": 1, "document_states_available": 2}


def test_queensland_typed_as_a_suburb_needs_the_same_confirming(research, budgets):
    """Acceptance 4: "Queensland" typed into Target Suburbs with Any state selected."""
    out = _run(research, _brief(state="", primary_suburbs=["Queensland"]), explain=True)
    _plain, colin = budgets[750000]
    assert out["needs_confirming"] == colin["needs_confirming"]
    assert out["needs_confirming_hidden"] == colin["needs_confirming_hidden"]


def test_the_list_is_capped_and_the_count_stays_exact(research, monkeypatch):
    monkeypatch.setattr(research, "MAX_NEEDS_CONFIRMING", 2)
    out = _run(research, _brief(), explain=True)
    assert len(out["needs_confirming"]) == 2
    assert out["needs_confirming_count"] == 3
    assert out["search_help"]["needs_confirming"]["listed"] == 2


def test_the_count_says_when_the_candidate_cap_may_have_cut_it_short(research, budgets):
    """Pending matches come from the search's own pool. When the 600-candidate cap cut that
    pool, stale matches beyond it were never scored, so the count is exact only over what
    was scored -- and search_help says so rather than implying a complete count."""
    _plain, colin = budgets[750000]
    assert colin["search_help"]["funnel"]["capped"] is False
    assert colin["search_help"]["needs_confirming"]["count_may_be_short"] is False
    assert colin["search_help"]["needs_confirming"]["hidden_may_be_short"] is False
    wide = _run(research, _brief(state="", budget_max=NO_CEILING,
                                 preferred_spending_cap=NO_CEILING), explain=True)
    assert wide["search_help"]["funnel"]["capped"] is True, "premise: the cap bites"
    assert wide["search_help"]["needs_confirming"]["count_may_be_short"] is True
    assert wide["search_help"]["needs_confirming"]["hidden_may_be_short"] is True


def test_without_its_row_an_entry_still_knows_it_came_by_email(research):
    """Critique #27's "email last read" turns on source_channel. A candidate with no live
    row behind it (no content_hash) takes the channel from its package's label."""
    cand = SimpleNamespace(
        content_hash="", lot_address="Lot 1228, Dawn, Walloon", lot_number="1228",
        suburb="Walloon", state="QLD", builder_name="", estate_context="Dawn, Walloon",
        house_design="", price_breakdown=SimpleNamespace(advertised_package_price=721900.0),
        bedrooms=4, bathrooms=2, car_spaces=2, storeys=None, land_size_sqm=0.0,
        house_size_sqm=None, scoring=None, source_link=None)
    pkg = {"source_channel": "digital email - database snapshot", "confirm": ["stale"],
           "date_checked": "20/08/2026", "age_days": 27, "email_first_seen": "17/08/2026",
           "list_age_days": 30}
    entry = research._needs_confirming_entry(cand, pkg, None)
    assert entry["source_channel"] == "digital email"
    assert (entry["date_checked"], entry["first_seen"], entry["list_age_days"]) == (
        "20/08/2026", "17/08/2026", 30)
    assert entry["builder_name"] == "Builder not identified in source"
    assert entry["availability_as_stated"] is None
    unnamed = dict(pkg, source_channel="stored stock - database snapshot")
    assert research._needs_confirming_entry(cand, unnamed, None)["source_channel"] == ""
    # The row's own channel wins over the package's label.
    assert research._needs_confirming_entry(
        cand, pkg, {"source_channel": "E-Agent"})["source_channel"] == "E-Agent"


def test_the_list_is_ranked_by_score(research):
    out = _run(research, _brief(budget_max=NO_CEILING, preferred_spending_cap=750000),
               explain=True)
    scores = [e["score"] for e in out["needs_confirming"]]
    assert len(set(scores)) > 1, "premise: the scores differ"
    assert scores == sorted(scores, reverse=True)


# -------------------------------------------------------- (c) once the data is fixed

@pytest.fixture(scope="module")
def c5_snapshot(tmp_path_factory):
    rows = _candidates._read_json(SUBSET)
    patched = []
    touched = 0
    for row in rows:
        h = str(row.get("content_hash") or "")[:11]
        if h in C5_SUPERSEDED and not row.get("superseded_by"):
            row = dict(row, superseded_by="C5 simulated: a later capture of this lot")
            touched += 1
        elif h in C5_TO_VIC:
            row = dict(row, state="VIC", state_source="row text locality (over suburb column)")
            touched += 1
        patched.append(row)
    assert touched == len(C5_SUPERSEDED) + len(C5_TO_VIC), "premise: every C5 row is here"
    path = tmp_path_factory.mktemp("c5") / "stock.json"
    path.write_text(json.dumps({"buildings": patched}), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def c5(c5_snapshot, research):
    mp, module = _research_on(c5_snapshot)
    try:
        brief = _brief()
        yield module, brief, _run(module, brief), _run(module, brief, explain=True)
    finally:
        mp.undo()


def test_after_the_data_fixes_colin_gets_nothing_confirmed_and_three_to_confirm(c5):
    _module, _brief_, plain, out = c5
    assert plain["shortlist_count"] == out["shortlist_count"] == 0
    assert out["shortlist"] == [] and plain["shortlist"] == []
    _assert_the_dawn_lots(out["needs_confirming"])
    assert "newer_capture_disagrees" not in out["needs_confirming_hidden"]
    rel = {r["id"]: r for r in out["search_help"]["relaxations"]}
    assert rel["include_needs_confirming"]["count"] == 3
    assert rel["include_needs_confirming"]["count_status"] == "exact"
    assert rel["budget_plus_10pct"]["count_status"] == "exact"
    assert rel["budget_plus_10pct"]["shortlist_count"] >= 1


def test_after_the_data_fixes_every_exact_relaxation_re_posts_to_its_count(c5):
    module, brief, _plain, out = c5
    reruns = [r for r in out["search_help"]["relaxations"] if r["kind"] == "rerun"]
    assert reruns
    for r in reruns:
        assert r["count_status"] == "exact"
        assert _run(module, dict(brief, **r["brief_patch"]))["shortlist_count"] == \
            r["shortlist_count"], r["id"]


# --------------------------------------- critique #3: whichever Dawn twin survives

# W18's identity-twin pass keeps the fresher row, and on a tie falls back to the higher id:
# it would retire 11878-11880 in favour of 14940-14942. Simulated here in memory.
W18_RETIRES = {"v2:aec54350": "v2:c48b4599", "v2:30440e59": "v2:9fb1f79e",
               "v2:325f8457": "v2:67246ddb"}


@pytest.fixture(scope="module")
def w18_snapshot(tmp_path_factory):
    rows = _candidates._read_json(SUBSET)
    full = {str(r.get("content_hash") or "")[:11]: r["content_hash"] for r in rows
            if r.get("content_hash")}
    patched = []
    for row in rows:
        h = str(row.get("content_hash") or "")[:11]
        if h in W18_RETIRES and not row.get("superseded_by"):
            row = dict(row, superseded_by=full[W18_RETIRES[h]])
        patched.append(row)
    assert sum(1 for r in patched if str(r.get("content_hash") or "")[:11] in W18_RETIRES
               and r.get("superseded_by")) == 3, "premise: all three were live"
    path = tmp_path_factory.mktemp("w18") / "stock.json"
    path.write_text(json.dumps({"buildings": patched}), encoding="utf-8")
    return path


def test_the_dawn_lots_need_confirming_whichever_twin_survives(w18_snapshot, research,
                                                                budgets):
    mp, module = _research_on(w18_snapshot)
    try:
        out = _run(module, _brief(), explain=True)
    finally:
        mp.undo()
    _assert_the_dawn_lots(out["needs_confirming"])
    # PREMISE: the other twin of each lot is the one offered now, and today it is not.
    assert {e["content_hash"][:11] for e in out["needs_confirming"]} == set(
        W18_RETIRES.values())
    _plain, colin = budgets[750000]
    assert {e["content_hash"][:11] for e in colin["needs_confirming"]} == set(W18_RETIRES)
    assert out["needs_confirming_hidden"] == colin["needs_confirming_hidden"]
    for entry in out["needs_confirming"]:
        assert (entry["date_checked"], entry["first_seen"]) == ("20/08/2026", "19/08/2026")


# ------------------------------------------------ (f) never the confirmed search's pool

@pytest.mark.parametrize("changes", [
    # Max blank, $750k preferred: the admit pass admits rows here.
    {"budget_max": NO_CEILING, "preferred_spending_cap": 750000},
    # Every state, no ceiling: the 600-candidate cap truncates the confirmed pool.
    {"state": "", "budget_max": NO_CEILING, "preferred_spending_cap": NO_CEILING},
])
def test_admitted_rows_never_change_the_confirmed_shortlist(research, changes):
    brief = _brief(**changes)
    plain = _run(research, brief)
    out = _run(research, brief, explain=True)
    assert [e["content_hash"] for e in out["shortlist"]] == [
        e["content_hash"] for e in plain["shortlist"]]
    assert out["shortlist_count"] == plain["shortlist_count"]
    admitted = [e for e in out["needs_confirming"] if "availability_unstated" in e["confirm"]]
    assert admitted, "premise: this brief admits unstated rows to confirm"


def test_on_the_full_snapshot_vic_900k_4bed_shortlist_is_unchanged(monkeypatch):
    """(f) on the 17 Sep snapshot, where the confirmed pool is capped (local-only).

    A regression check only. That snapshot was built before W07 published price evidence,
    so its admit pass admits nothing (every unstated row is refused no_price_evidence) and
    a pool shared with admitted rows could not change it. The red proof for (f) is
    test_an_admitted_lot_never_takes_a_confirmed_lots_place_under_the_cap."""
    path = _FULL_SNAPSHOT_ENV
    if not path or not Path(path).exists() or Path(path).resolve() == SUBSET.resolve():
        pytest.skip("SPB_SNAPSHOT_JSON does not point at a full snapshot (local-only check)")
    mp, module = _research_on(path)
    try:
        brief = _brief(state="VIC", budget_max=900000, preferred_spending_cap=900000,
                       bedrooms_min=4)
        plain = _run(module, brief)
        out = _run(module, brief, explain=True)
        colin = _run(module, _brief(), explain=True)
    finally:
        mp.undo()
    full_rows = _candidates._read_json(Path(path))
    if len(full_rows) < 15000:
        pytest.skip("SPB_SNAPSHOT_JSON is not a full snapshot")
    assert out["search_help"]["funnel"]["capped"], "premise: the cap bites"
    assert [e["content_hash"] for e in out["shortlist"]] == [
        e["content_hash"] for e in plain["shortlist"]]
    # The numbers below belong to the 17 Sep snapshot, not to any full snapshot: a later
    # one (C5 applied, new stock) moves them. Same gate as W04's full-snapshot goldens.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "make_colin_subset", APP / "tests" / "fixtures" / "make_colin_subset.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    golden = json.loads(gen.GOLDEN.read_text(encoding="utf-8"))
    if gen.fingerprint(full_rows) != golden["full_snapshot_fingerprint"]:
        pytest.skip("the unchanged VIC shortlist held; the acceptance numbers are pinned to "
                    "the 17 Sep snapshot and SPB_SNAPSHOT_JSON is another one")
    # Acceptance 2 (pre-C5, 16/09/2026): $825,000 finds 12.
    rel = {r["id"]: r for r in colin["search_help"]["relaxations"]}
    assert rel["budget_plus_10pct"]["shortlist_count"] == 12
    _assert_the_dawn_lots(colin["needs_confirming"])


# ------------------------------------------------------------- synthetic, rule by rule

def _listing(content_hash, **kw):
    """A Walloon listing that clears every gate but the ones a test is about."""
    row = {"content_hash": content_hash, "builder_name": "Example Homes",
           "lot_address": "12 Example Street", "street_address": None, "lot_number": "",
           "suburb": "Walloon", "state": "QLD", "postcode": None,
           "availability_status": "Available", "state_source": "listing locality",
           "price": 600000.0, "land_price": None, "build_price": None, "bedrooms": 4,
           "bathrooms": 2, "car_spaces": 2, "land_sqm": 400.0, "house_sqm": 180.0,
           "frontage_m": None, "storey": None, "title_status": None,
           "estate_name": "Example Rise", "incentive_amount": None, "incentive_text": None,
           "product_type": None, "source_channel": "digital email",
           "attribution_scope": None, "date_checked": "15/09/2026",
           "source_project_id": None, "benchmark_median": None,
           "benchmark_variance_pct": None, "benchmark_classification": None,
           "benchmark_basis": None, "promo_selected": None, "superseded_by": None,
           "suburb_source": "stated by the source", "source_doc": "doc-states",
           "price_kind": "package", "text_price_max": None, "price_in_text": True,
           "email_list": None, "email_first_seen": None}
    row.update(kw)
    if row["text_price_max"] is None:
        row["text_price_max"] = row["price"]
    return row


EXCEPTIONS = dict(source_doc="doc-exceptions", availability_status=None)


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    rows = [
        # A confirmed lot, on a list that says "Available".
        _listing("v2:syn-confirmed"),
        # An exceptions-only list: one lot it marked Sold (since retired), the rest blank.
        _listing("v2:syn-sold", lot_address="99 Example Street", availability_status="Sold",
                 source_doc="doc-exceptions", superseded_by="v2:syn-sold-later",
                 date_checked="01/09/2026"),
        # ...the SAME lot, design and price as the confirmed one, under another hash.
        _listing("v2:syn-same-lot", **EXCEPTIONS),
        # ...a different lot, admitted to confirm.
        _listing("v2:syn-admitted", lot_address="14 Example Street", price=590000.0,
                 **EXCEPTIONS),
        # ...two Build Only prices: one in Walloon, one in Redbank Plains.
        _listing("v2:syn-build-walloon", lot_address="16 Example Street", price=580000.0,
                 price_kind="build_only", **EXCEPTIONS),
        _listing("v2:syn-build-redbank", lot_address="3 Other Road", price=570000.0,
                 suburb="Redbank Plains", price_kind="build_only", **EXCEPTIONS),
    ]
    path = tmp_path_factory.mktemp("synthetic") / "stock.json"
    path.write_text(json.dumps({"buildings": rows}), encoding="utf-8")
    mp, module = _research_on(path)
    yield module
    mp.undo()


def test_a_lot_already_shortlisted_is_not_offered_again_under_another_hash(synthetic):
    brief = _brief(budget_max=650000, preferred_spending_cap=650000)
    rows, _path = _candidates.load_snapshot()
    admitted, _counts = _candidates.build_packages(
        brief, rows, admit=("availability_unstated",), only_admitted=True)
    # PREMISE: the twin IS admitted, so only the shortlist check can keep it out.
    assert sorted(p["content_hash"] for p in admitted) == ["v2:syn-admitted",
                                                           "v2:syn-same-lot"]
    out = _run(synthetic, brief, explain=True)
    assert _hashes(out["shortlist"]) == ["v2:syn-confirmed"]
    assert _hashes(out["needs_confirming"]) == ["v2:syn-admitted"]
    (entry,) = out["needs_confirming"]
    assert entry["confirm"] == ["availability_unstated"]
    assert entry["availability_as_stated"] is None
    assert entry["date_checked"] == "15/09/2026" and entry["age_days"] == 1
    assert entry["first_seen"] is None and entry["list_age_days"] is None


def test_an_admitted_lot_never_takes_a_confirmed_lots_place_under_the_cap(synthetic,
                                                                         monkeypatch):
    """(f) where the cap bites on a fixture: one candidate slot. The admitted lot at $590,000
    is cheaper than the confirmed one at $600,000, so a pool shared and cut by price would
    give it the only slot and the confirmed lot would never be scored."""
    monkeypatch.setattr(_candidates, "MAX_CANDIDATES", 1)
    brief = _brief(budget_max=650000, preferred_spending_cap=650000)
    plain = _run(synthetic, brief)
    out = _run(synthetic, brief, explain=True)
    assert _hashes(plain["shortlist"]) == _hashes(out["shortlist"]) == ["v2:syn-confirmed"]
    assert out["shortlist_count"] == plain["shortlist_count"] == 1
    (entry,) = out["needs_confirming"]
    assert entry["content_hash"] == "v2:syn-admitted"
    # PREMISE: cheaper than the confirmed lot, so a shared price-ordered cut keeps it.
    assert entry["advertised_package_price"] < out["shortlist"][0]["advertised_package_price"]
    # Its own pool was cut too (2 admitted, 1 slot), and the count says it may be short.
    assert out["search_help"]["needs_confirming"]["count_may_be_short"] is True


def test_refusals_are_counted_only_for_lots_that_match_the_brief(synthetic):
    """Build Only is refused in both suburbs. Searching Walloon, the Redbank Plains one is a
    lot the scorer rejects anyway, so it is not "hidden" from this search."""
    anywhere = _run(synthetic, _brief(budget_max=650000, preferred_spending_cap=650000),
                    explain=True)
    assert anywhere["needs_confirming_hidden"] == {"build_only": 2}
    walloon = _run(synthetic, _brief(budget_max=650000, preferred_spending_cap=650000,
                                     primary_suburbs=["Walloon"]), explain=True)
    assert walloon["needs_confirming_hidden"] == {"build_only": 1}
    assert _hashes(walloon["needs_confirming"]) == ["v2:syn-admitted"]


@pytest.mark.parametrize("slots,count_short,hidden_short", [
    # Room for everything: nothing may be short.
    (600, False, False),
    # 3 slots: the search (1 row) and the admit pass (2) fit, but the refusal pass -- every
    # unstated row searched as though stated, beside the confirmed lot, 5 rows -- is cut.
    # Only the hidden count may be short, and only it says so.
    (3, False, True),
    # 1 slot: the admit pass is cut too, so the count and the reveal say so as well.
    (1, True, True),
])
def test_each_pass_the_cap_cuts_says_which_number_may_be_short(synthetic, monkeypatch, slots,
                                                                count_short, hidden_short):
    monkeypatch.setattr(_candidates, "MAX_CANDIDATES", slots)
    out = _run(synthetic, _brief(budget_max=650000, preferred_spending_cap=650000),
               explain=True)
    assert out["shortlist_count"] == 1 and out["search_help"]["funnel"]["capped"] is False
    flags = out["search_help"]["needs_confirming"]
    assert (flags["count_may_be_short"], flags["hidden_may_be_short"]) == (count_short,
                                                                           hidden_short)
    (reveal,) = [r for r in out["search_help"]["relaxations"] if r["kind"] == "reveal"]
    assert reveal["count"] == out["needs_confirming_count"] == 1
    assert reveal["count_status"] == "exact" and reveal["capped"] is count_short


def test_with_nothing_to_score_an_admitted_lot_is_still_offered(synthetic):
    """$595,000: the confirmed lot is over budget, so the search takes the zero-packages
    branch -- and the exceptions-only lot at $590,000 still needs confirming."""
    out = _run(synthetic, _brief(budget_max=595000, preferred_spending_cap=595000),
               explain=True)
    assert out["shortlist_count"] == 0 and out["rejected_log"][0]["kind"] == "coverage"
    assert _hashes(out["needs_confirming"]) == ["v2:syn-admitted"]
    assert out["needs_confirming_hidden"] == {"build_only": 2}
    rel = {r["id"]: r for r in out["search_help"]["relaxations"]}
    assert rel["include_needs_confirming"]["count"] == 1
    assert rel["budget_plus_50k"]["shortlist_count"] == 1        # the confirmed lot
