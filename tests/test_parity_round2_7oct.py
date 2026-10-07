"""Repair round 2 of the parity release (7 Oct 2026): the parity verifier's findings on the
checks themselves. Each test fails on par-search 068569a and passes after.

  * AN ALLOWED REASON COULD HIDE A WHOLE DEVELOPMENT. A search made to call Fawkner Park
    West's 35 lots "builder_documents_reread" -- read that very night, on a list read that
    very night -- passed parity_check AND search_regressions: the reason was allowed, and
    nothing asked whether the database backs it. Now every guard reason is held to its
    evidence, a whole building hidden for its own list's sake fails, and the excused floor
    is 2 lots, not 10 (WA's 10 of 22 was 45%).
  * PARITY HAD NO PRECISION BAR: a bundle that also returned NSW lots for VIC passed it.
  * COPIES WERE UNDER-COUNTED: the duplicates key carried the card's suburb, and one Hattan
    lot is carded "Springvale" off one list and "Springvale South" off the other.
  * WHAT THE BARS LEAVE OUT IS NAMED: blank lots on a list that states "Available", the
    fresh rows the guard keeps off the shortlist, cards that name no lot and no street.
  * KNOWN DATA ISSUES ARE WRITTEN DOWN WITH A DATE (callers verifier): the run went red every
    night on rows only the client can fix.
  * A CARD'S SUBURB MUST NOT CONTRADICT THE ADDRESS ITS OWN ROW WRITES ("Mt Duneed,Victoria,
    3217" carded Tarneit).
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for _p in (str(APP), str(TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _parity_fixture as F  # noqa: E402
import parity_check as P  # noqa: E402
import search_regressions as SR  # noqa: E402

TODAY = "06/10/2026"


# ------------------------------------------------------------- the evidence an excuse rests on

def _cat(rows):
    return SR.Catalogue(rows, rows)


def _r(h, **kw):
    out = {"content_hash": h, "source_channel": "E-Agent", "builder_name": "Fawkner Homes",
           "state": "VIC", "estate_name": "Fawkner Park West", "lot_number": None,
           "source_doc": "doc-fpw", "date_checked": "06/10/2026", "source_folder": "",
           "availability_status": "Available", "lot_address": "Lot 1, Fawkner Park West"}
    out.update(kw)
    return out


def test_a_list_read_tonight_backs_no_builder_or_folder_excuse():
    rows = [_r("v2:a"), _r("v2:b", lot_number="7")]
    ev = SR.guard_evidence(_cat(rows))
    assert ev.unsupported(rows[0], "builder_documents_reread")
    assert ev.unsupported(rows[0], "newer_upload_in_folder")
    assert ev.unsupported(rows[0], "document_reread_without_row")
    assert ev.unsupported(rows[1], "newer_capture_exists")


def test_the_evidence_that_backs_each_excuse_is_found():
    old = _r("v2:old", date_checked="24/09/2026", source_doc="doc-old", lot_number="7",
             source_folder="f1")
    rows = [old,
            _r("v2:new", source_doc="doc-new", lot_number="7", source_folder="f1"),
            _r("v2:same", date_checked="06/10/2026", source_doc="doc-old2")]
    ev = SR.guard_evidence(_cat(rows))
    assert ev.unsupported(old, "builder_documents_reread") is None
    assert ev.unsupported(old, "newer_upload_in_folder") is None
    assert ev.unsupported(old, "newer_capture_exists") is None
    # a reason that rests on the row's own words is not second-guessed
    assert ev.unsupported(rows[2], "text_marks_unavailable") is None
    # a Proxima project read again, later
    p_old = _r("v2:p1", source_channel="Proxima", source_project_id="785",
               date_checked="25/09/2026")
    p_new = _r("v2:p2", source_channel="Proxima", source_project_id="785")
    ev = SR.guard_evidence(_cat([p_old, p_new]))
    assert ev.unsupported(p_old, "project_reread_without_row") is None
    assert ev.unsupported(p_new, "project_reread_without_row")


# ------------------------------------------------------------- the bars, unit by unit

def _lot(i, building="estate:a", reason=None, allowed=True, group="VIC", e_agent=True):
    return {"hash": "v2:%03d" % i, "id": i, "group": group, "building": building,
            "building_label": "Building %s" % building, "e_agent": e_agent,
            "reason": reason, "allowed": allowed, "channel": "E-Agent", "doc": "doc:x",
            "doc_label": "E-Agent - x"}


def test_a_whole_building_hidden_for_its_own_lists_sake_is_named():
    mine = [_lot(i) for i in range(6)] + [_lot(10 + i, building="estate:b") for i in range(6)]
    hidden = ([dict(l, reason="builder_documents_reread") for l in mine[:6]]
              + [dict(l, reason="newer_capture_exists") for l in mine[6:]])
    whole = P._whole_buildings_excused(mine, hidden)
    assert [w["building"] for w in whole] == ["Building estate:a"], whole
    # Four lots are under the size that fails.
    assert P._whole_buildings_excused(mine[:4], hidden[:4]) == []


def _group(hidden=(), **extra):
    lots = {"e-agent": [100, 100], "all": [100, 100]}
    out = {"searched": True, "error": "", "short": [], "page_bytes": [10],
           "lots": lots, "buildings": {"e-agent": [10, 10], "all": [10, 10]},
           "obvious": {"e-agent": 100, "all": 100}, "excused": {"e-agent": 0, "all": 0},
           "shortlisted": {"e-agent": [100, 100], "all": [100, 100]}, "hidden": list(hidden)}
    out.update(extra)
    return out


def test_an_excuse_without_evidence_and_a_demotion_without_evidence_fail():
    hidden = [_lot(1, reason="unsupported:builder_documents_reread", allowed=False)]
    groups = {"VIC": _group(hidden, demoted_unsupported=[
        {"id": 2, "reason": "builder_documents_reread",
         "lacking": "its builder's lists were not read after its own list (06/10/2026)"}])}
    failures, rows, _notes = P.shown_bar(groups)
    texts = [f["text"] for f in failures]
    assert any("hidden as unsupported:builder_documents_reread" in t
               and "the database does not back" in t for t in texts), texts
    assert any("sent to Needs confirming as builder_documents_reread" in t for t in texts)
    assert rows[0]["pass"] is False


def test_the_excused_floor_is_two_lots():
    groups = {"WA": _group(lots={"e-agent": [0, 0], "all": [19, 19]},
                           obvious={"e-agent": 0, "all": 22},
                           excused={"e-agent": 0, "all": 3},
                           buildings={"e-agent": [0, 0], "all": [1, 1]},
                           shortlisted={"e-agent": [0, 0], "all": [19, 19]})}
    failures, _rows, _notes = P.shown_bar(groups)
    assert any("3 of 22 obvious lots are hidden for an allowed reason" in f["text"]
               for f in failures), failures


def test_a_card_of_another_state_or_gone_or_retired_fails_precision():
    rows = [_r("v2:vic", id=1), _r("v2:nsw", id=2, state="NSW"),
            _r("v2:sold", id=3, availability_status="Sold"),
            _r("v2:old", id=4, superseded_by="v2:vic"), _r("v2:blank", id=5, state="")]
    cat = _cat(rows)
    entries = [{"content_hash": h, "state": "VIC"} for h in
               ("v2:vic", "v2:nsw", "v2:sold", "v2:old", "v2:blank", "v2:nowhere")]
    cell = P._precision(entries, "VIC", cat)
    assert cell == {"wrong_state": [2], "gone": [3], "retired": [4],
                    "missing": ["v2:nowhere"]}, cell
    failures = P.precision_bar({"VIC": {"precision": cell}})
    assert {f["reason"] for f in failures} == {"wrong_state", "gone", "retired", "missing"}
    assert P.precision_bar({"VIC": {"precision": {"wrong_state": [], "gone": [],
                                                   "retired": [], "missing": []}}}) == []


def test_one_lot_carded_in_two_spellings_of_its_suburb_is_one_copy():
    a = _r("v2:sd", id=1, builder_name="Hattan Homes", lot_number="58",
           source_text="Available Titled Townhouse 58 160 8 Nexus | Lot 58 | Roselle $984,950")
    b = _r("v2:ro", id=2, builder_name="Hattan Homes", lot_number="58", source_doc="doc-em",
           source_channel="digital email",
           source_text="Available Springvale South Titled Townhouse 58 160 8 Nexus $984,950")
    cat = _cat([a, b])
    entries = [{"content_hash": "v2:sd", "suburb": "Springvale",
                "advertised_package_price": 984950},
               {"content_hash": "v2:ro", "suburb": "Springvale South",
                "advertised_package_price": 984950}]
    docs = {"v2:sd": "doc:fpw", "v2:ro": "doc:em"}
    assert P._duplicates(entries, cat, docs)["surplus"] == 1
    # two suburbs that do not contain each other are two places
    entries[1]["suburb"] = "Tarneit"
    assert P._duplicates(entries, cat, docs)["surplus"] == 0


def test_what_the_bars_leave_out_is_named_in_the_notes():
    groups = {"QLD": _group(blank_on_stating_list={"count": 35, "docs": {
        "E-Agent - AVIA Homes - stock.pdf": 35}},
        fresh_guard={"hidden": {"document_reread_without_row": 46},
                     "confirm": {"builder_documents_reread": 219}},
        unlabelled={"count": 3, "ids": [12452, 12437, 18916]})}
    _failures, rows, notes = P.shown_bar(groups)
    text = " | ".join(notes)
    assert "35 lot(s) read in the last 3 nights are blank on a list that states" in text
    assert "hidden 46 (document_reread_without_row 46)" in text
    assert "sent to Needs confirming 219" in text
    assert "3 shortlisted card(s) name no lot and no street" in text
    assert rows[0]["blank_on_stating_list"] == 35 and rows[0]["fresh_guard_confirm"] == 219


def test_a_unit_its_own_list_files_commercial_is_no_obvious_home():
    """191 Leakes Rd's and Rockwater's warehouse units, with no state on their lists,
    failed parity every night as 'no state search reaches them'. A home search should not."""
    cells = {"price_kind": "unknown", "price_in_text": True, "text_price_max": 776895.0}
    unit = {"price": 776895.0, "product_type": "Commercial", "bedrooms": None,
            "lot_number": "50", "lot_address": "Lot 50, OCFEE:"}
    assert not SR.priced_as_a_home(unit, cells)
    assert SR.priced_as_a_home(dict(unit, product_type="House & Land"), cells)
    assert SR.priced_as_a_home(dict(unit, bedrooms=2), cells), "a home filed Commercial"


# ------------------------------------------------------------- known issues, with a date

def test_an_acknowledged_row_is_a_note_until_its_date_and_a_failure_after():
    hidden = [_lot(17450, reason="no_suburb", allowed=False),
              _lot(17451, reason="no_suburb", allowed=False),
              _lot(99, reason="no_suburb", allowed=False)]
    groups = {"VIC": _group(hidden)}
    data = {"acknowledged": [{"group": "VIC", "reason": "no_suburb", "ids": [17450, 17451],
                              "why": "the PV list prints no suburb; asked of Coleen",
                              "until": "21/10/2026"}]}
    acks, ack_notes = P._acks_in_force(data, TODAY)
    failures, _rows, notes = P.shown_bar(groups, acks)
    ids = [i for f in failures for i in f.get("ids") or ()]
    assert ids == [99], failures
    assert any("acknowledged until 21/10/2026" in n and "17450" in n for n in notes), notes
    acks, ack_notes = P._acks_in_force(data, "22/10/2026")
    failures, _rows, _notes = P.shown_bar(groups, acks)
    assert sorted(i for f in failures for i in f.get("ids") or ()) == [99, 17450, 17451]
    assert any("expired 21/10/2026" in n for n in ack_notes), ack_notes


def test_an_acknowledged_unread_list_leaves_the_unread_share():
    served = {"shown": 100, "over": 0, "unjudged": 0, "ids": [], "docs": {}, "unread": 30,
              "unread_docs": {"E-Agent - Victoria & Albert - Sales List": 26,
                              "E-Agent - Murcia - report.pdf": 4}}
    data = {"acknowledged": [{"group": "QLD", "bar": "unread",
                              "docs": ["E-Agent - Victoria & Albert"],
                              "why": "its Dropbox folder lists empty", "until": "21/10/2026"}]}
    acks, _ = P._acks_in_force(data, TODAY)
    failures, _cells, notes = P.over_served_bar({"QLD": {"over_served": served}}, acks)
    assert failures == [], failures
    assert any("acknowledged until 21/10/2026" in n for n in notes), notes
    failures, _cells, _notes = P.over_served_bar({"QLD": {"over_served": served}})
    assert failures, "without the acknowledgement 30% fails"


def test_the_shipped_acknowledgements_parse_and_expire():
    data = json.loads((APP / "data" / "parity_acknowledged.json").read_text(encoding="utf-8"))
    acks, notes = P._acks_in_force(data, TODAY)
    assert acks and not notes, notes
    for entry in data["acknowledged"]:
        assert entry.get("why") and SR._day(entry.get("until")), entry
        assert entry.get("ids") or entry.get("docs"), entry
    assert P._acks_in_force(data, "01/01/2027")[0] == []


# ------------------------------------------------------------- a card and its own row

def test_a_card_placed_against_the_address_its_row_writes_is_named():
    rec = {"lot_address": "Mt Duneed,Victoria,3217 $836,000.00 325 m2"}
    assert SR.written_place_contradicts(rec, "Tarneit", "VIC") == "Mt Duneed, VIC"
    assert SR.written_place_contradicts(rec, "Mount Duneed", "VIC") == ""
    assert SR.written_place_contradicts({"lot_address": "Tarniet,Victoria,3029"},
                                        "Tarneit", "VIC") == ""
    assert SR.written_place_contradicts({"lot_address": "Lot 4, Somewhere"}, "Tarneit",
                                        "VIC") == ""


@pytest.mark.parametrize("title, lot, named", [
    ("GOLF $3,497,500", "", False),
    ("PARK RIDGE", "", False),
    ("$620,000.00 $461,312.00 Click here Click here", "", False),
    ("1386 Peter Thomson Circuit", "", True),
    ("Lot 3755, Ceduna", "", True),
    ("Unit 2302, Atlas", "", True),
    ("Altona Drive", "17", True),
])
def test_a_card_that_names_no_lot_and_no_street_is_counted(title, lot, named):
    assert SR.no_lot_and_no_street({"lot_address": title, "lot_number": lot}) is (not named)


# ------------------------------------------------------------- the Settings tab on a phone

def test_the_settings_tables_scroll_inside_their_own_box():
    """Callers verifier, Playwright at 375 px: the Settings tab overflowed by 191 px, from
    its six-column source-health table. Both of its tables now scroll in a box of their own."""
    import re
    page = (APP / "index.html").read_text(encoding="utf-8")
    channels = page[page.index("const table = (rows, firstCol) =>"):]
    channels = channels[:channels.index("</table>")]
    assert re.search(r'overflow-x:\s*auto[^>]*>\s*<table', channels), channels[:300]
    panel = page[page.index("PARITY-PANEL-BEGIN"):page.index("PARITY-PANEL-END")]
    assert re.search(r'overflow-x:\s*auto[^>]*>\s*<table', panel)


# ------------------------------------------------------------- a copy folded into a shown card

def test_a_lot_the_dedupe_folded_into_a_shown_card_is_not_reported_lost(tmp_path, monkeypatch):
    """Hermitage lot 283 on 6 Oct: its 01/10 E-Agent copy, now sent to confirm (its list was
    replaced), is folded by the pipeline's dedupe into the portal's shown 'LOT 283
    RECREATION CIRCUIT, CLYDE 3978' -- the same lot, price and land. No card key and no lot
    number pairs the two spellings, so the obvious-rows check called the lot lost
    (scored_not_shown) until it replayed the dedupe."""
    import io
    from datetime import date
    from types import SimpleNamespace
    sys.path.insert(0, str(APP / "api"))
    import _candidates as C
    import _synthetic_stock as S
    common = dict(builder_name="Hermitage Homes", price=728100.0, land_sqm=290.0,
                  suburb="Clyde", state="VIC")
    rows = [
        S.row("v2:ea283", lot_number="283", estate_name="Fernlea , Clyde 3978",
              lot_address="283 Recreation Circuit, Fernlea , Clyde 3978",
              source_doc="doc-herm-old", date_checked="12/09/2026", line_key="k-ea283",
              source_text="283 Recreation Circuit 290 Drake 19 Duke 4 Single $374,000 "
                          "$354,100 $728,100 Available", **common),
        S.row("v2:po283", lot_number=None, estate_name=None, bedrooms=None, house_sqm=None,
              lot_address="LOT 283 RECREATION CIRCUIT, CLYDE 3978", availability_status=None,
              source_channel="Direct Builder Portal (live)", source_doc="doc-portal",
              date_checked="15/09/2026", line_key="k-po283", **common),
        # the builder's newer E-Agent list, read last night
        S.row("v2:new1", builder_name="Hermitage Homes", lot_number="900",
              lot_address="Lot 900, Fernlea", source_doc="doc-herm-new",
              date_checked="15/09/2026", line_key="k-new1", suburb="Clyde"),
    ]
    for n, r in enumerate(rows):
        r["id"] = 800 + n
    path = S.write(tmp_path, rows)
    monkeypatch.setenv("SPB_SNAPSHOT_JSON", str(path))
    monkeypatch.delenv("SPB_FRESH_GUARD", raising=False)
    monkeypatch.setattr(C, "datetime", S.Sep16)
    import _bootstrap
    import research
    resp = S.run(research, explain=True)
    shown = {e["content_hash"] for e in resp["shortlist"] + (resp.get("needs_confirming") or [])}
    assert "v2:po283" in shown and "v2:ea283" not in shown, shown
    catalogue = SR.Catalogue(rows, rows)
    out = io.StringIO()
    run = SimpleNamespace(report=SR.Report(out), today=date(2026, 9, 16))
    hook = SR.obvious_rows_check(C, research, _bootstrap, catalogue, str(path))
    hook(run, "synthetic", {}, {}, resp)
    text = out.getvalue()
    assert "v2:ea283" not in "".join(l for l in text.splitlines() if "[FAIL]" in l), text
    assert "collapsed_into_visible 1" in text, text


# ------------------------------------------------------------- end to end, made to fail

@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    site = tmp_path_factory.mktemp("parity_round2_bundle")
    done = subprocess.run(
        [sys.executable, "-X", "utf8", "-c",
         "import sys; from pathlib import Path; import build_web; "
         "build_web._bundle_research_function(Path(sys.argv[1]), Path(sys.argv[2]))",
         str(APP), str(site)],
        cwd=str(APP), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    return site / "api"


# The verifier's mutation: the search calls one building's lots builder_documents_reread.
_MUTATION = '''

_round2_original_guard = fresh_row_guard


def fresh_row_guard(row, prepared, today=None):
    if str(row.get("estate_name") or "") == "Parity Rise 0":
        return "builder_documents_reread"
    return _round2_original_guard(row, prepared, today)
'''


def _mutated(bundle, tmp_path):
    copy = tmp_path / "mutated_api"
    shutil.copytree(bundle, copy)
    target = copy / "_candidates.py"
    target.write_text(target.read_text(encoding="utf-8") + _MUTATION, encoding="utf-8")
    return copy


def _parity(api, base, env=None):
    snapshot, db = F.write(base, F.clean_catalogue())
    report = base / "parity_report.json"
    run_env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    run_env.pop("SPB_MAX_CANDIDATES", None)
    run_env.pop("SPB_FRESH_GUARD", None)
    run_env.update(env or {})
    done = subprocess.run(
        [sys.executable, "-X", "utf8", str(APP / "parity_check.py"), "--snapshot",
         str(snapshot), "--db", str(db), "--bundle", str(api), "--today", TODAY,
         "--history", str(base / "h.json"), "--report", str(report),
         "--manifest", str(base / "m.json"), "--acknowledged", str(base / "none.json"),
         "--degraded-marker", str(base / "_deg")],
        cwd=str(APP), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=run_env, timeout=600)
    body = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None
    return done.returncode, done.stdout + done.stderr, body


def test_a_building_hidden_under_an_allowed_reason_it_cannot_back_fails(bundle, tmp_path):
    code, out, body = _parity(_mutated(bundle, tmp_path), tmp_path,
                              env={"SPB_FRESH_GUARD": "builder_documents_reread=hide"})
    assert code == 1, out
    texts = " | ".join(f["text"] for f in body["failures"])
    assert "unsupported:builder_documents_reread" in texts, texts
    assert "all 8 obvious lots of" in texts and "Parity Rise 0" in texts, texts


def test_a_building_demoted_to_confirm_for_a_reason_it_cannot_back_fails(bundle, tmp_path):
    code, out, body = _parity(_mutated(bundle, tmp_path), tmp_path)
    assert code == 1, out
    texts = " | ".join(f["text"] for f in body["failures"])
    assert "was sent to Needs confirming as builder_documents_reread" in texts, texts


def test_the_clean_catalogue_still_passes(bundle, tmp_path):
    code, out, body = _parity(bundle, tmp_path)
    assert code == 0, out
