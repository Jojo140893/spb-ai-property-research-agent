"""search_regressions.py: Colin's searches replayed nightly, and loudly red when one is wrong. (W17)

Part C, 16 Sep 2026: Colin searched "QLD under $750k" and was shown a Victorian lot filed as
Queensland (14905) and a lot a later read said was On Hold (18491), while three sellable Shape
Homes lots in Walloon were hidden. The harness must see all of that on the data of that day,
and pass once the data fixes (W01, W02) are applied.

The replays run the real thing: a research bundle built by build_web from this tree, a
snapshot (tests/fixtures/colin_17sep_subset.json, as it was and with the C5 fixes applied in
memory), and a small database made from the same rows -- the named rows under their real
17 Sep ids. Today is frozen at 16/09/2026, the day the live replays matched.

The rest are unit checks on the rules with a stubbed search, the --live mode against a
stubbed server (the service key must never be printed, nor carried through a redirect), and
the run_daily step.
"""
import http.server
import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import date
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import search_regressions as SR  # noqa: E402

SUBSET = APP / "tests" / "fixtures" / "colin_17sep_subset.json"
TODAY = "16/09/2026"

# The rows the tests name, under their ids in the 17 Sep database.
IDS = {"v2:aec54350": 11878, "v2:30440e59": 11879, "v2:325f8457": 11880,
       "v2:c48b4599": 14940, "v2:9fb1f79e": 14941, "v2:67246ddb": 14942,
       "v2:11ad45df": 14943, "v2:43802e2d": 18491, "v2:d9553ad6": 15953,
       "v2:1c5ca48e": 14905}
# The C5 data fixes (W01, W02), applied in memory as tests/test_needs_confirming_response.py
# applies them: Howard 18491 and Windermere 9568-9571 retired, Wollert lot 618 filed VIC.
C5_SUPERSEDED = ("v2:43802e2d", "v2:d32b17a9", "v2:50cabe9b", "v2:b399f290", "v2:022e96c6")
C5_TO_VIC = ("v2:1c5ca48e",)
# The one brief the subset cannot answer. It holds every document with a QLD or blank-state
# row at or under $850,000, which carries no Werribee stock; Werribee runs against the full
# snapshot in the acceptance replays.
NOT_ON_THE_SUBSET = ("werribee_vic_700k_4bed",)


# ------------------------------------------------------------------------- fixtures

def _subset_rows():
    data = json.loads(SUBSET.read_text(encoding="utf-8"))
    return [dict(zip(data["keys"], row)) for row in data["rows"]], data["keys"]


def _c5(rows):
    out = []
    for row in rows:
        h = str(row.get("content_hash") or "")[:11]
        if h in C5_SUPERSEDED and not row.get("superseded_by"):
            row = dict(row, superseded_by="C5 simulated: a later capture of this lot")
        elif h in C5_TO_VIC:
            row = dict(row, state="VIC",
                       state_source="row text locality (over suburb column)")
        out.append(row)
    return out


def _write_snapshot(path, rows, keys):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"keys": list(keys),
                                "rows": [[r.get(k) for k in keys] for r in rows]}),
                    encoding="utf-8")
    return path


def _checked_at(day):
    parsed = SR._day(day)
    return None if parsed is None else parsed.strftime("%Y-%m-%dT11:00:00")


def _write_db(path, rows, keys):
    """A buildings table holding the snapshot's rows: the named ones under their real ids,
    source_text the line the snapshot keeps, first_seen the emailed list's first read."""
    cols = ["id"] + [k for k in keys if k != "id"] + ["source_text", "first_seen",
                                                       "checked_at"]
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE buildings (%s)" % ", ".join('"%s"' % c for c in cols))
        records = []
        for n, row in enumerate(rows):
            h = str(row.get("content_hash") or "")
            rec = dict(row, id=IDS.get(h[:11], 900000 + n),
                       source_text=row.get("lot_address"),
                       first_seen=row.get("email_first_seen") or row.get("date_checked"),
                       checked_at=_checked_at(row.get("date_checked")))
            records.append(tuple(
                json.dumps(rec.get(c)) if isinstance(rec.get(c), (list, dict)) else rec.get(c)
                for c in cols))
        conn.executemany("INSERT INTO buildings VALUES (%s)" % ", ".join("?" * len(cols)),
                         records)
        conn.commit()
    finally:
        conn.close()
    return path


class Site:
    """base/site/stock.json (pre-C5) with the bundle beside it in base/site/api;
    base/c5/stock.json (C5 applied, no bundle beside it); a database for each."""

    def __init__(self, base):
        self.base = base
        self.site = base / "site"
        self.bundle = self.site / "api"
        self.pre_c5 = self.site / "stock.json"
        self.pre_c5_db = base / "pre_c5.db"
        self.c5 = base / "c5" / "stock.json"
        self.c5_db = base / "c5.db"


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """A research bundle built by build_web from this tree, and the two catalogues."""
    paths = Site(tmp_path_factory.mktemp("regressions"))
    done = subprocess.run(
        [sys.executable, "-X", "utf8", "-c",
         "import sys; from pathlib import Path; import build_web; "
         "build_web._bundle_research_function(Path(sys.argv[1]), Path(sys.argv[2]))",
         str(APP), str(paths.site)],
        cwd=str(APP), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert done.returncode == 0 and (paths.bundle / "research.py").is_file(), (
        done.stdout[-2000:] + done.stderr[-2000:])
    rows, keys = _subset_rows()
    _write_snapshot(paths.pre_c5, rows, keys)
    _write_db(paths.pre_c5_db, rows, keys)
    fixed = _c5(rows)
    _write_snapshot(paths.c5, fixed, keys)
    _write_db(paths.c5_db, fixed, keys)
    return paths


def _harness(*args, env=None, timeout=600):
    done = subprocess.run(
        [sys.executable, "-X", "utf8", str(APP / "search_regressions.py"), *map(str, args)],
        cwd=str(APP), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, **(env or {})), timeout=timeout)
    return done.returncode, done.stdout, done.stderr


def _fingerprint(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest(), Path(path).stat().st_mtime_ns


# ------------------------------------------------------ the plan's tests, on the subset

def test_on_the_data_colin_searched_it_fails_naming_the_on_hold_lot(site):
    """Plan test 1: the pre-C5 subset. Howard 18491 is shortlisted as Available on 15/09
    while 15953, the same line of the same document read on 16/09, says On Hold: the sibling
    invariant names it. 14905 is filed QLD from its suburb column while its own line says
    Wollert: a warning, not a failure, until it has been clean for 7 nights."""
    code, out, err = _harness("--snapshot", site.pre_c5, "--db", site.pre_c5_db,
                              "--today", TODAY, "--only", "qld_750k")
    assert code == 1, out + err
    assert ("[FAIL] #1 qld_750k: sibling: shortlisted v2:43802e2d (id 18491) says 'Available' "
            "(15/09/2026), but v2:d9553ad6 (id 15953)") in out, out + err
    for h, rid in (("v2:1c5ca48e", 14905), ("v2:43802e2d", 18491)):
        assert "[FAIL] #1 qld_750k: %s (id %d) must never appear, and is in shortlist" % (
            h, rid) in out, out
    warnings = [line for line in out.splitlines() if line.startswith("[WARN]")]
    assert any("v2:1c5ca48e (id 14905)" in w and "Wollert (VIC)" in w for w in warnings), out
    assert not any("v2:1c5ca48e" in line and "sibling" in line for line in out.splitlines())
    assert "RESULT: FAIL" in out


def test_a_confirm_window_narrowed_to_14_days_fails_naming_the_dawn_lots(site):
    """Plan test 2: SPB_CONFIRM_MAX_DAYS=14 hides the Dawn lots as over_confirm_limit. The
    obvious-rows check picks its rows with its own fixed 45 days, so it sees three sellable
    lots hidden and names each by its database id."""
    code, out, err = _harness("--snapshot", site.c5, "--bundle", site.bundle,
                              "--db", site.c5_db, "--today", TODAY,
                              "--only", "qld_750k", env={"SPB_CONFIRM_MAX_DAYS": "14"})
    assert code == 1, out + err
    obvious = [line for line in out.splitlines() if "[FAIL]" in line and "obvious row" in line]
    for rid in (11878, 11879, 11880):
        assert any("(id %d)" % rid in line and "over_confirm_limit" in line
                   for line in obvious), (rid, out)


def test_once_the_data_is_fixed_every_search_passes(site):
    """Plan test 3: the same subset with the C5 outcome applied. Every brief the subset can
    answer passes; the database given is not written and the bundle is not changed."""
    db = site.c5_db
    before_db = _fingerprint(db)
    before_bundle = SR._tree(site.bundle)
    only = [b["id"] for b in SR.load_spec()["briefs"] if b["id"] not in NOT_ON_THE_SUBSET]
    code, out, err = _harness("--snapshot", site.c5, "--bundle", site.bundle, "--db", db,
                              "--today", TODAY, "--only", ",".join(only))
    assert code == 0, out + err
    assert "RESULT: PASS" in out
    for brief_id in only:
        assert " %s: shortlist" % brief_id in out, (brief_id, out)
    # The Dawn lots were offered, and the obvious-rows check ran and saw them.
    assert "obvious rows: 7 (collapsed_into_visible 3, needs_confirming 3, " \
           "price_unverified 1)" in out, out
    assert _fingerprint(db) == before_db
    assert SR._tree(site.bundle) == before_bundle


def test_run_daily_lists_the_replay_as_an_optional_step_after_the_build():
    """Plan test 4."""
    done = subprocess.run([sys.executable, "-X", "utf8", str(APP / "run_daily.py"),
                           "--dry-run", "--no-deploy"], cwd=str(APP), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stderr
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    build = next(i for i, line in enumerate(lines) if "Build the deployed snapshot" in line)
    replay = next(i for i, line in enumerate(lines) if "Replay Colin's searches" in line)
    assert replay == build + 1, lines
    assert lines[replay].endswith(
        "python search_regressions.py --snapshot vercel_site/stock.json  (optional)"), lines


class _Stub(http.server.BaseHTTPRequestHandler):
    """A deployed API that misbehaves: echoes the key it was sent in its refusals and its
    record id, and answers Clare's body with a redirect to another server."""
    seen_keys = []
    redirect_to = ""

    def log_message(self, *args):
        pass

    def _send(self, code, body, headers=()):
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        # Where urllib's default handling of a 302 would land: the POST re-sent as a GET,
        # every header but the content ones copied -- the service key included.
        type(self).seen_keys.append((self.server.server_address[1],
                                     self.headers.get(SR.SERVICE_KEY_HEADER) or ""))
        self._send(200, {"status": "success", "shortlist": []})

    def do_POST(self):
        key = self.headers.get(SR.SERVICE_KEY_HEADER) or ""
        type(self).seen_keys.append((self.server.server_address[1], key))
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        brief = body.get("client_brief") or {}
        if self.path != "/api/research":
            self._send(404, {"status": "error", "message": "no route"})
        elif "explain" not in body and brief.get("finance_status"):
            self._send(302, {"status": "moved"}, [("Location", type(self).redirect_to)])
        elif brief.get("client_name") == "":
            self._send(200, {"status": "success", "research_record_id": "Echo %s" % key,
                             "shortlist_count": 0, "rejected_count": 0, "shortlist": [],
                             "brief_used": {"state": "QLD", "budget_min": 0,
                                            "budget_max": 100000}})
        else:
            self._send(401, {"status": "error", "message": "refused key %s" % key})


def test_live_mode_never_prints_the_key_nor_carries_it_through_a_redirect(tmp_path):
    """Plan test 5: stdout and stderr never hold the key, even when the server echoes it
    back; the header reaches the API; a redirect is refused, so the key never reaches
    whatever the redirect points at."""
    key = "spb-test-key-7d1c0b5e0f4a4bd2"
    key_file = tmp_path / "key.txt"
    # Saved the way Notepad saves UTF-8: with a byte-order mark, which is not the key.
    key_file.write_text("﻿" + key + "\n", encoding="utf-8")
    rows, keys = _subset_rows()
    published = _write_snapshot(tmp_path / "published" / "stock.json", rows[:50], keys)
    _Stub.seen_keys = []
    api = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    elsewhere = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    _Stub.redirect_to = "http://127.0.0.1:%d/api/research" % elsewhere.server_address[1]
    for server in (api, elsewhere):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        code, out, err = _harness(
            "--live", "http://127.0.0.1:%d" % api.server_address[1], "--key-file", key_file,
            "--published-snapshot", published, "--today", TODAY,
            "--only", "qld_750k,qld_blank_name_nothing_to_score,hook_shaped_qld_750k",
            timeout=120)
    finally:
        api.shutdown()
        elsewhere.shutdown()
    assert code == 1, out + err
    # PREMISE: the server really echoed the key into what the harness prints.
    assert "refused key [redacted]" in out and "Echo [redacted]" in out, out
    assert key not in out and key not in err
    assert {port for port, sent in _Stub.seen_keys} == {api.server_address[1]}, \
        "the redirect was followed: the key reached another server"
    assert all(sent == key for _port, sent in _Stub.seen_keys)
    assert "HTTP 302" in out, out


def test_live_mode_never_sends_the_key_in_clear_to_a_remote_host(tmp_path):
    """The key goes out with the first request, before any redirect to https could be
    refused: plain http is for a stub on this machine only."""
    key_file = tmp_path / "key.txt"
    key_file.write_text("spb-test-key-0b5e\n", encoding="utf-8")
    rows, keys = _subset_rows()
    published = _write_snapshot(tmp_path / "stock.json", rows[:5], keys)
    code, out, err = _harness("--live", "http://app.example.invalid", "--key-file", key_file,
                              "--published-snapshot", published, "--only", "qld_750k",
                              timeout=120)
    assert code == 1
    assert "--live must be an https URL" in out, out + err
    assert "spb-test-key-0b5e" not in out + err


# ------------------------------------------------------------------- exit codes and steps

def test_a_failure_or_a_usage_error_exits_1_never_what_run_daily_reads_otherwise(site):
    """run_daily.py reads a step's 2 as a dead harvest channel and DEGRADED_EXIT as a
    fallback; a failed replay is neither."""
    import run_daily
    assert run_daily.DEGRADED_EXIT not in (0, 1)
    assert _harness("--snapshot", site.pre_c5, "--only", "no_such_brief")[0] == 1
    assert _harness()[0] == 1
    code, out, _err = _harness("--snapshot", site.base / "nowhere" / "stock.json")
    assert code == 1 and "no snapshot at" in out


def test_without_a_bundle_beside_the_snapshot_it_refuses_to_replay(site):
    """The replay goes through the bundle that ships, never app/api (critique #20c)."""
    code, out, _err = _harness("--snapshot", site.c5, "--db", site.c5_db, "--today", TODAY,
                               "--only", "qld_750k")
    assert code == 1
    assert "no research bundle at" in out, out


def test_a_failed_replay_marks_the_night_degraded_and_the_chain_carries_on(tmp_path,
                                                                            monkeypatch):
    """The step is optional: _run returns True (the chain continues to the end and main()
    writes the DEGRADED marker CI fails on after publishing) and records the label; a failed
    replay is not a dead channel."""
    import run_daily
    (tmp_path / "search_regressions.py").write_text("import sys\nsys.exit(1)\n",
                                                   encoding="utf-8")
    monkeypatch.setattr(run_daily, "APP", tmp_path)
    run_daily._DEGRADED.clear()
    run_daily._PARTIAL.clear()
    try:
        label = "7/7  Replay Colin's searches"
        assert run_daily._run(label, ["search_regressions.py", "--snapshot",
                                      "vercel_site/stock.json"], True) is True
        assert run_daily._DEGRADED == [label]
        assert run_daily._PARTIAL == []
    finally:
        run_daily._DEGRADED.clear()
        run_daily._PARTIAL.clear()


# ------------------------------------------------------------------- the rules, unit by unit

SPEC = {
    "form": {"client_name": "Unit", "state": "QLD", "budget_min": 0, "budget_max": 750000},
    "never_list": {"v2:bad00000": "a row that must never appear"},
    "dawn": {"estate": "Dawn, Walloon", "date_checked": "20/08/2026", "until": "04/10/2026",
             "confirm_max_days": 45, "lots": {"1228": 721900}},
    "briefs": [],
}


def _row(h, **kw):
    row = {"content_hash": h, "state": "QLD", "price": 700000.0,
           "availability_status": "Available", "date_checked": "15/09/2026",
           "source_doc": "doc-1", "lot_address": "Lot 1 Example Street Example Rise 4 2 2",
           "superseded_by": None, "estate_name": "Example Rise", "lot_number": "1",
           "source_channel": "E-Agent", "state_source": "listing locality"}
    row.update(kw)
    return row


def _card(row, **kw):
    card = {"content_hash": row["content_hash"], "state": row["state"],
            "advertised_package_price": row["price"], "date_checked": row["date_checked"],
            "lot_address": row["lot_address"], "estate_context": row.get("estate_name"),
            "lot_number": row.get("lot_number")}
    card.update(kw)
    return card


def _response(shortlist=(), confirming=None, explain=True, **kw):
    """A search's answer. explain=True (what body_for sends unless a brief says otherwise)
    carries every explain key, as research._with_explain always adds them."""
    out = {"status": "success", "shortlist_count": len(shortlist), "rejected_count": 0,
           "shortlist": list(shortlist), "research_record_id": "Unit - QLD/General",
           "brief_used": {"state": "QLD", "budget_min": 0, "budget_max": 750000,
                          "primary_suburbs": []},
           "brief_adjustments": [], "rejected_log": []}
    if explain:
        out.update(search_help={"relaxations": []}, needs_confirming=[],
                   needs_confirming_count=0, needs_confirming_hidden={})
    if confirming is not None:
        out["needs_confirming"] = list(confirming)
        out["needs_confirming_count"] = len(confirming)
    out.update(kw)
    return out


def _run(briefs, answers, rows, today=date(2026, 9, 16), checks="nightly", db=None):
    """Run the briefs against canned answers: answers(body) -> response."""
    spec = dict(SPEC, briefs=briefs)
    posted = []

    def post(body):
        posted.append(body)
        return 200, json.loads(json.dumps(answers(body))), 0.01
    report = SR.Report(io.StringIO())
    SR.Run(spec, post, SR.Catalogue(rows, db), today, report, checks=checks).execute()
    return report, report.out.getvalue(), posted


def test_a_relaxation_whose_count_a_re_post_does_not_return_fails():
    rows = [_row("v2:ok000000")]
    relaxations = [{"id": "include_needs_confirming", "kind": "reveal", "count": 0},
                   {"id": "budget_plus_50k", "kind": "rerun", "count_status": "exact",
                    "shortlist_count": 4, "brief_patch": {"budget_max": 800000}},
                   {"id": "whole_state", "kind": "rerun", "count_status": "not_computed",
                    "brief_patch": {"state": "QLD"}}]

    def answers(body):
        if body["client_brief"]["budget_max"] == 800000:
            return _response([_card(rows[0])] * returned)
        return _response(search_help={"relaxations": relaxations})

    brief = {"id": "b", "self_check_nightly": True, "client_brief": {}}
    for returned, failures in ((4, 0), (3, 1)):
        report, out, posted = _run([brief], answers, rows)
        assert report.failures == failures, out
        # PREMISE: the patch was posted over the brief as sent, without explain.
        assert posted[-1] == {"client_brief": dict(SPEC["form"], budget_max=800000)}
    assert "budget_plus_50k promises 4, re-posting its brief_patch returns 3" in out
    assert "whole_state was not_computed, not re-posted" in out
    # Nightly, only the flagged brief is re-posted; full re-posts every brief.
    _report, _out, posted = _run([dict(brief, self_check_nightly=False)], answers, rows)
    assert len(posted) == 1
    _report, _out, posted = _run([dict(brief, self_check_nightly=False)], answers, rows,
                                 checks="full")
    assert len(posted) == 2


def test_each_rule_a_brief_states_fails_when_the_response_breaks_it():
    rows = [_row("v2:bad00000")]
    card = _card(rows[0])
    broken = _response(
        [card], confirming=[card], shortlist_count=1, rejected_count=5,
        research_record_id="Colin - QLD/General - $750,000",
        brief_used={"state": "VIC", "budget_min": 0, "budget_max": 750000,
                    "primary_suburbs": ["Red Bank Plains"]},
        brief_adjustments=[{"field": "state", "code": "state_full_name"}],
        location_resolution=[{"said": "Red Bank Plains", "method": "exact"}],
        rejected_log=[{"reason": "Hard Rejection: Price $760,000 exceeds max budget"}],
        search_help={"funnel": {"in_state_rows": 9},
                     "relaxations": [{"id": "budget_plus_50k", "kind": "rerun",
                                      "count_status": "exact", "shortlist_count": 1,
                                      "brief_patch": {"budget_max": 800000}},
                                     {"id": "radius_20", "kind": "rerun",
                                      "count_status": "not_computed"}]})
    brief = {"id": "b", "client_brief": {}, "expect": {
        "never": "never_list", "funnel": True, "no_over_budget_rejections": True,
        "min_shortlist": 2, "adjustment": {"field": "budget_max", "code": "no_budget_max"},
        "brief_used": {"state": "QLD"}, "location_method": "state",
        "record_id_startswith": "Unnamed Client", "record_id_contains": ["Any state"],
        "record_id_excludes": ["QLD/"], "suburb_searched": "Redbank Plains",
        "relaxation_exact_when_full": ["whole_state", "radius_20"]}}
    spec = dict(SPEC, briefs=[brief])
    report = SR.Report(io.StringIO())
    SR.Run(spec, lambda body: (200, broken, 9.5), SR.Catalogue(rows), date(2026, 9, 16),
           report, mode="live", checks="full").execute()
    out = report.out.getvalue()
    for expected in (
            "v2:bad00000 must never appear, and is in shortlist: a row that must never appear",
            "v2:bad00000 must never appear, and is in needs_confirming",
            "shortlist_count + rejected_count = 6, but search_help.funnel.in_state_rows = 9",
            "1 rejection(s) say 'exceeds max budget'",
            "shortlist_count 1, expected at least 2",
            "no brief_adjustments entry matches",
            'brief_used {"state": "VIC"}, expected {"state": "QLD"}',
            "location_resolution has no method 'state'",
            "does not start 'Unnamed Client'",
            "lacks 'Any state'",
            "contains 'QLD/'",
            "searched suburbs ['Red Bank Plains'], expected Redbank Plains",
            "relaxation whole_state was not offered",
            "relaxation radius_20 is not_computed, not exact",
            "shortlisted v2:bad00000 is in QLD, not the VIC searched"):
        assert expected in out, (expected, out)
    # Live: the warm call over 8 s fails; replayed on this machine it only warns.
    assert "[FAIL] b: the warm call took 9.5 s (limit 8 s)" not in out, "premise: not timed"
    timed = dict(brief, timed=True, expect={})
    for mode, line in (("live", "[FAIL] b: the warm call took 9.5 s (limit 8 s)"),
                       ("snapshot", "[WARN] b: the warm call took 9.5 s (limit 8 s); timed "
                                    "on this machine, not Vercel")):
        report = SR.Report(io.StringIO())
        SR.Run(dict(SPEC, briefs=[timed]), lambda body: (200, _response(), 9.5),
               SR.Catalogue(rows), date(2026, 9, 16), report, mode=mode).execute()
        assert line in report.out.getvalue(), report.out.getvalue()


def test_equal_to_compares_the_shortlist_and_the_needs_confirming_hashes():
    rows = [_row("v2:aaaa0000"), _row("v2:bbbb0000", lot_number="2",
                                      lot_address="Lot 2 Example Street Example Rise")]

    def answers(body):
        if body["client_brief"].get("budget_max") == 750:
            return _response([_card(rows[1])], confirming=[])
        return _response([_card(rows[0])], confirming=[])

    briefs = [{"id": "base", "client_brief": {}},
              {"id": "slip", "client_brief": {"budget_max": 750},
               "expect": {"equal_to": "base"}}]
    report, out, _posted = _run(briefs, answers, rows)
    assert report.failures == 1
    assert "shortlist differs from base: only here ['v2:bbbb0000'], only there " \
           "['v2:aaaa0000']" in out


def test_every_card_must_match_the_catalogue_and_the_brief():
    fresh = _row("v2:fresh000")
    stale = _row("v2:stale000", date_checked="01/09/2026", lot_number="3",
                 lot_address="Lot 3 Other Street Example Rise 4 2 2")
    gone = _row("v2:gone0000", availability_status="On Hold", lot_number="4",
                lot_address="Lot 4 Third Street Example Rise 3 2 1")
    rows = [fresh, stale, gone]
    db = [{"id": 1, "content_hash": "v2:fresh000", "date_checked": "14/09/2026"},
          {"id": 2, "content_hash": "v2:stale000", "date_checked": "01/09/2026"},
          {"id": 3, "content_hash": "v2:gone0000", "date_checked": "15/09/2026"}]

    def answers(body):
        return _response([_card(fresh), _card(stale), _card(gone, state="VIC"),
                          _card(fresh, content_hash="v2:nowhere0",
                                advertised_package_price=760000)])

    report, out, _posted = _run([{"id": "b", "client_brief": {}}], answers, rows, db=db)
    assert "shortlisted v2:fresh000 (id 1): the card says checked '15/09/2026', the " \
           "database says '14/09/2026'" in out
    assert "shortlisted v2:stale000 (id 2) was checked '01/09/2026', more than 14 days ago" \
        in out
    assert "shortlisted v2:gone0000 (id 3) is shown, but its row says 'On Hold'" in out
    assert "shortlisted v2:gone0000 (id 3) is in VIC, not the QLD searched" in out
    assert "shortlisted v2:nowhere0 is priced $760,000, outside $0-$750,000" in out
    assert "shortlisted v2:nowhere0 is not in the database" in out
    assert report.failures == 6, out


def test_a_fresher_capture_of_the_same_line_that_says_gone_fails_the_older_one():
    """The sibling invariant, both ways round, and not for two lines of ONE read."""
    older = _row("v2:older000", lot_address="Howard Available 21 Howard Crossing Kinchant "
                                            "Coastal 4 2 2 350 $224,000 $746,632 Due Septe",
                 date_checked="15/09/2026", price=746632.0)
    newer = _row("v2:newer000", lot_address="Howard ON HOLD 21 Howard Crossing Kinchant "
                                            "Coastal 4 2 2 350 $224,000 $746,632 Due Septemb",
                 date_checked="16/09/2026", availability_status="On Hold", price=746632.0)
    catalogue = SR.Catalogue([older, newer])
    assert SR.gone_sibling(catalogue, "v2:older000") is newer
    # The later capture is the Available one: the lot came back, nothing to flag.
    flipped = SR.Catalogue([dict(older, date_checked="17/09/2026"), newer])
    assert SR.gone_sibling(flipped, "v2:older000") is None
    # Another document, or another price, is another listing.
    assert SR.gone_sibling(SR.Catalogue([older, dict(newer, source_doc="doc-2")]),
                           "v2:older000") is None
    assert SR.gone_sibling(SR.Catalogue([older, dict(newer, price=750000.0)]),
                           "v2:older000") is None
    # Two lines of one read (Merbein: identical lots, one Hold, one Available) are two lots.
    same_day = dict(newer, date_checked="15/09/2026")
    db = [{"content_hash": "v2:older000", "checked_at": "2026-09-15T11:32:01"},
          {"content_hash": "v2:newer000", "checked_at": "2026-09-15T11:32:02"}]
    assert SR.gone_sibling(SR.Catalogue([older, same_day], db), "v2:older000") is None
    db[1]["checked_at"] = "2026-09-15T13:40:00"
    assert SR.gone_sibling(SR.Catalogue([older, same_day], db), "v2:older000") is same_day


def _dawn_rows(first_hash="v2:dawn0001", twin_hash="v2:dawn0002", **kw):
    base = dict(estate_name="Dawn, Walloon", lot_number="1228", price=721900.0,
                date_checked="20/08/2026", source_channel="digital email",
                email_list="list-1", email_first_seen="17/08/2026", source_doc="doc-dawn",
                lot_address="1228 Available Medium Density Bates Street")
    base.update(kw)
    return [_row(first_hash, **base), _row(twin_hash, **dict(base, email_first_seen="19/08/2026"))]


def _dawn_answer(rows, hash_offered):
    offered = next(r for r in rows if r["content_hash"] == hash_offered)
    return lambda body: _response(confirming=[_card(offered)])


BRIEF_DAWN = {"id": "b", "client_brief": {}, "expect": {"needs_confirming_contains": ["1228"]}}


def test_dawn_is_matched_by_estate_lot_and_price_whichever_twin_is_offered():
    rows = _dawn_rows()
    for offered in ("v2:dawn0001", "v2:dawn0002"):
        report, out, _posted = _run([BRIEF_DAWN], _dawn_answer(rows, offered), rows)
        assert report.failures == 0, out
    report, out, _posted = _run([BRIEF_DAWN], lambda body: _response(confirming=[]), rows)
    assert report.failures == 1
    assert "Dawn lot 1228 ($721,900, stated Available, read 20/08/2026) is not in " \
           "needs_confirming" in out


def test_dawn_is_required_only_while_its_rows_are_unchanged():
    rows = _dawn_rows()
    missing = lambda body: _response(confirming=[])                      # noqa: E731
    # The window runs from the earlier first read (17/08 + 45 days = 01/10), not 04/10.
    holds, _why, until = SR.dawn_expectation(dict(SPEC), ["1228"], SR.Catalogue(rows),
                                             date(2026, 10, 1))
    assert holds and until == date(2026, 10, 1)
    report, out, _posted = _run([BRIEF_DAWN], missing, rows, today=date(2026, 10, 2))
    assert report.failures == 0 and report.warnings == 0
    assert "the confirm window ended on 01/10/2026" in out
    # A later read of the lot, or a newer list from the sender: a warning to update the
    # expectation, never a failure.
    for changed in (rows + [_row("v2:dawn0003", estate_name="Dawn, Walloon",
                                 lot_number="1228", price=721900.0,
                                 date_checked="10/09/2026", availability_status="Sold")],
                    rows + [_row("v2:other001", email_list="list-1",
                                 date_checked="05/09/2026", source_channel="digital email")]):
        report, out, _posted = _run([BRIEF_DAWN], missing, changed)
        assert report.failures == 0 and report.warnings == 1, out


def test_an_obvious_row_passes_only_as_shown_collapsed_into_a_shown_twin_or_guarded():
    """critique #20(a) and #3: the verdicts an obvious row may end with, and the ones that
    name it as lost."""
    shown = {("lot 1228, dawn, walloon", 721900)}
    twin = {"lot_address": "Lot 1228, Dawn, Walloon", "advertised_package_price": 721900.0}
    other = {"lot_address": "Lot 7, Nowhere", "advertised_package_price": 700000.0}

    class Agent:
        def score_candidates(self, brief, packages):
            return [], [{"reason": "Hard Rejection: not one of the requested suburbs"}]

    class Bootstrap:
        @staticmethod
        def get_agent():
            return Agent()

    class Research:
        @staticmethod
        def _normalised_brief(brief):
            return dict(brief), []

    def outcome(verdict, listed_short=False):
        return SR._obvious_outcome(verdict, shown, Research, Bootstrap, {"state": "QLD"},
                                   listed_short)

    assert outcome({"gate": "same_listing_collapsed", "package": twin})[0] == \
        "collapsed_into_visible"
    assert outcome({"gate": "same_listing_collapsed", "package": other})[0] not in \
        SR.ALLOWED_VERDICTS
    assert outcome({"gate": "scored", "package": dict(
        other, confirm=["stale"], confirm_guard="newer_capture_disagrees")})[0] == \
        "newer_capture_disagrees"
    assert outcome({"gate": "scored", "package": dict(
        other, confirm=["stale"], confirm_guard="over_confirm_limit")})[0] not in \
        SR.ALLOWED_VERDICTS
    assert outcome({"gate": "incomplete_facts", "package": None})[0] in SR.WARN_VERDICTS
    assert outcome({"gate": "truncated", "package": other})[0] not in SR.ALLOWED_VERDICTS
    lost = outcome({"gate": "scored", "package": dict(other, confirm=["stale"])})
    assert lost == ("rejected_by_the_scorer",
                    "Hard Rejection: not one of the requested suburbs")
    assert outcome({"gate": "scored", "package": dict(other, confirm=["stale"])},
                   listed_short=True)[0] == "needs_confirming_unlisted"


def test_a_hook_body_gets_no_explain_keys_back_and_is_sent_without_explain():
    rows = [_row("v2:ok000000")]
    brief = {"id": "hook", "hook_shaped": True, "explain": False,
             "client_brief": {"client_name": "Clare", "state": "QLD", "budget_max": 750000},
             "expect": {"no_explain_keys": True}}
    report, out, posted = _run([brief], lambda body: _response(explain=False,
                                                               needs_confirming=[]), rows)
    assert posted == [{"client_brief": {"client_name": "Clare", "state": "QLD",
                                        "budget_max": 750000}}]
    assert report.failures == 1 and "a body without explain got ['needs_confirming']" in out


def test_the_replay_cannot_create_a_directory_inside_the_bundle(tmp_path, monkeypatch):
    import errno
    import pathlib
    monkeypatch.setattr(pathlib.Path, "mkdir", pathlib.Path.mkdir)
    bundle = tmp_path / "api"
    bundle.mkdir()
    SR._read_only_bundle(bundle)
    with pytest.raises(OSError) as refused:
        (bundle / "_lib" / "output").mkdir(parents=True)
    assert refused.value.errno == errno.EROFS
    (tmp_path / "scratch").mkdir()


def test_the_briefs_file_holds_no_link_and_every_plan_brief():
    raw = (APP / "data" / "colin_searches.json").read_text(encoding="utf-8")
    assert not SR._LINK.search(raw), SR._LINK.search(raw)
    spec = SR.load_spec()
    plans = {b.get("plan") for b in spec["briefs"]}
    assert {"#%d" % n for n in range(1, 15)} <= plans
    assert "critique #20(d)" in plans
    # Critique #3: Dawn is named by estate, lot and price -- no hash to go stale.
    assert "aec54350" not in raw and "c48b4599" not in raw


# ------------------------------------------------------------------ review of W17 (17 Sep)

def test_every_brief_must_say_what_it_searched_and_a_misread_echo_fails(tmp_path):
    """Cards are checked against brief_used, the server's own echo. A normaliser that misread
    the budget echoes its misreading, and every card inside it passes: with the thousands
    rule widened to 1,000,000, "QLD $100,000" searched $100,000,000 and returned 370 green
    results. So every brief states its state and range, and the echo is checked."""
    spec = json.loads((APP / "data" / "colin_searches.json").read_text(encoding="utf-8"))
    del spec["briefs"][0]["expect"]["brief_used"]
    broken = tmp_path / "briefs.json"
    broken.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ValueError, match="qld_750k: expect.brief_used must state state, "
                                         "budget_min, budget_max"):
        SR.load_spec(broken)
    rows = [_row("v2:ok000000", price=790000.0)]
    brief = {"id": "b", "client_brief": {}, "expect": {
        "brief_used": {"state": "QLD", "budget_min": 0, "budget_max": 100000}}}
    misread = _response([_card(rows[0])], brief_used={
        "state": "QLD", "budget_min": 0, "budget_max": 100000000, "primary_suburbs": []})
    report, out, _posted = _run([brief], lambda body: misread, rows)
    # PREMISE: nothing else about the cards is wrong -- inside the range the echo claims.
    assert "outside" not in out, out
    assert report.failures == 1, out
    assert 'brief_used {"state": "QLD", "budget_min": 0, "budget_max": 100000000}, expected' \
        in out, out


def test_an_explain_pass_that_raised_or_an_explain_key_missing_fails():
    """_with_explain never raises: a pass that throws leaves [] and says so only in
    search_help.error, and a relaxation that throws is priced "error". Neither may read
    as "nothing to offer". A relaxation the time budget skipped is still only a warning."""
    rows = [_row("v2:ok000000")]
    brief = {"id": "b", "client_brief": {}}
    for response, expected in (
            (_response(search_help={"relaxations": [], "error": "relaxations: RuntimeError"}),
             "search_help.error: relaxations: RuntimeError"),
            (_response(search_help={"relaxations": [
                {"id": "budget_plus_50k", "kind": "rerun", "count_status": "error",
                 "error": "ValueError", "brief_patch": {"budget_max": 800000}}]}),
             "relaxation budget_plus_50k failed to price: ValueError"),
            ({k: v for k, v in _response().items() if k != "needs_confirming_count"},
             "a body with explain got no ['needs_confirming_count'] back")):
        report, out, _posted = _run([brief], lambda body, r=response: r, rows)
        assert report.failures == 1 and expected in out, (expected, out)
    skipped = _response(search_help={"relaxations": [
        {"id": "radius_50", "kind": "rerun", "count_status": "not_computed"}]})
    report, out, _posted = _run([dict(brief, self_check_nightly=True)],
                                lambda body: skipped, rows)
    assert report.failures == 0 and report.warnings == 1, out
    # A body without explain is not asked for the keys.
    hook = {"id": "h", "explain": False, "client_brief": {}}
    report, out, _posted = _run([hook], lambda body: _response(explain=False), rows)
    assert report.failures == 0, out


def test_kommo_is_handed_shortlisted_cards_only():
    """The lead's top property and the internal note come from the shortlist; a
    needs-confirming card reaching either is a stale lot sold to Kommo as verified."""
    fresh = _row("v2:fresh000", lot_address="Lot 162, Rosewood Green", price=749900.0)
    stale = _row("v2:stale000", lot_address="Lot 1228, Dawn, Walloon, Walloon QLD",
                 price=721900.0, date_checked="20/08/2026", lot_number="1228")
    repriced = _row("v2:fresh1228", lot_address=stale["lot_address"], price=731900.0,
                    lot_number="1228")
    rows = [fresh, stale, repriced]

    def payload(top, top_price, lines):
        return {"custom_fields_update": {"top_property_match": top,
                                         "top_property_price": top_price},
                "kommo_internal_note": "SPB Property Research Completed\n\nTop Shortlisted "
                                       "Properties:\n" + ("\n".join(lines) or "None")}

    good_line = "1. Lot 162, Rosewood Green, Rosewood - $749,900 (Score: 93.5/100 - Recommend)"
    leaked_line = "2. Lot 1228, Dawn, Walloon, Walloon QLD, Walloon - $721,900 (Score: 84/100)"
    brief = {"id": "b", "client_brief": {}}
    cases = (
        (_response([_card(fresh)], confirming=[_card(stale)], kommo_payload=payload(
            "Lot 162, Rosewood Green", 749900.0, [good_line])), None),
        (_response([], confirming=[_card(stale)], kommo_payload=payload(
            "No qualifying properties found", 0, [])), None),
        (_response([_card(fresh)], confirming=[_card(stale)], kommo_payload=payload(
            "Lot 162, Rosewood Green", 749900.0, [good_line, leaked_line])),
         "needs confirming v2:stale000 (Lot 1228, Dawn, Walloon, Walloon QLD, $721,900) is "
         "written into the Kommo note as if shortlisted"),
        (_response([_card(fresh)], confirming=[_card(stale)], kommo_payload=payload(
            "Lot 1228, Dawn, Walloon, Walloon QLD", 721900.0, [good_line])),
         "kommo_payload's top property 'Lot 1228, Dawn, Walloon, Walloon QLD' at $721,900 is "
         "not a shortlisted card"),
        (_response([], confirming=[_card(stale)], kommo_payload=payload(
            "Lot 1228, Dawn, Walloon, Walloon QLD", 721900.0, [])),
         "kommo_payload names a top property 'Lot 1228, Dawn, Walloon, Walloon QLD' at "
         "$721,900, but nothing was shortlisted"),
        # The note prints the realistic total, which can differ from the card's price.
        (_response([_card(fresh)], confirming=[_card(stale)], kommo_payload=payload(
            "Lot 162, Rosewood Green", 749900.0,
            [good_line, leaked_line.replace("$721,900", "$736,900")])),
         "needs confirming v2:stale000 (Lot 1228, Dawn, Walloon, Walloon QLD, $721,900) is "
         "written into the Kommo note as if shortlisted"),
        # A fresh capture of the SAME lot at a new price is shortlisted and in the note;
        # the stale capture beside it in needs_confirming is not a leak.
        (_response([_card(fresh), _card(repriced)], confirming=[_card(stale)],
                   kommo_payload=payload(
            "Lot 162, Rosewood Green", 749900.0,
            [good_line, leaked_line.replace("$721,900", "$731,900")])), None))
    for response, expected in cases:
        report, out, _posted = _run([brief], lambda body, r=response: r, rows)
        if expected is None:
            assert report.failures == 0, out
        else:
            assert report.failures == 1 and expected in out, (expected, out)


def test_an_emailed_lot_ages_from_the_earliest_first_read_of_its_twins():
    """Dawn 11878 (first read 17/08) and 14940 (the same email re-parsed, first_seen 19/08)
    are one lot. The search ages it by 17/08 and stops offering it after 01/10; picked by
    14940's own stamp, the obvious-rows check still wanted it on 02/10 and 03/10 and turned
    the nightly red over a correct search. Lot 1009, read only on 19/08, keeps its own."""
    def dawn(h, lot, price, first, doc="doc-dawn"):
        return _row(h, estate_name="Dawn, Walloon", lot_number=lot, price=price,
                    date_checked="20/08/2026", source_channel="digital email",
                    source_doc=doc, lot_address="Lot %s, Dawn, Walloon" % lot), \
            {"id": int(h[-4:], 16), "content_hash": h, "state": "QLD", "price": price,
             "availability_status": "Available", "date_checked": "20/08/2026",
             "source_channel": "digital email", "first_seen": first, "lot_number": lot,
             "superseded_by": None}

    pairs = [dawn("v2:dawn0001", "1228", 721900.0, "17/08/2026"),
             dawn("v2:dawn0002", "1228", 721900.0, "19/08/2026"),
             dawn("v2:dawn0003", "1009", 791404.0, "19/08/2026"),
             # the same lot and price on ANOTHER list is another listing: its own first read
             dawn("v2:dawn0004", "1228", 721900.0, "19/08/2026", doc="doc-other")]
    catalogue = SR.Catalogue([p[0] for p in pairs], [p[1] for p in pairs])

    def picked(day):
        return sorted(r["content_hash"] for r in SR.obvious_candidates(
            catalogue, "QLD", 0, 800000, day, lambda rec: {}))

    everything = ["v2:dawn0001", "v2:dawn0002", "v2:dawn0003", "v2:dawn0004"]
    assert picked(date(2026, 10, 1)) == everything
    assert picked(date(2026, 10, 2)) == ["v2:dawn0003", "v2:dawn0004"]
    assert picked(date(2026, 10, 3)) == ["v2:dawn0003", "v2:dawn0004"]
    assert picked(date(2026, 10, 4)) == []


def test_dawn_lots_are_judged_one_by_one():
    """Lot 1009's window (first read 19/08) runs to 03/10, two days past 1228's (17/08):
    on 02/10 a missing 1009 fails, while 1228's lapse is only a note."""
    rows = _dawn_rows() + [_row("v2:dawn1009", estate_name="Dawn, Walloon", lot_number="1009",
                                price=791404.0, date_checked="20/08/2026",
                                source_channel="digital email", email_list="list-1",
                                email_first_seen="19/08/2026", source_doc="doc-dawn",
                                lot_address="1009 Available Medium Bates Street")]
    spec_lots = dict(SPEC["dawn"], lots={"1228": 721900, "1009": 791404})
    brief = {"id": "b", "client_brief": {}, "expect": {"needs_confirming_contains":
                                                       ["1228", "1009"]}}
    report = SR.Report(io.StringIO())
    SR.Run(dict(SPEC, dawn=spec_lots, briefs=[brief]), lambda body: (200, _response(), 0.01),
           SR.Catalogue(rows), date(2026, 10, 2), report).execute()
    out = report.out.getvalue()
    assert report.failures == 1, out
    assert "Dawn lot 1009 ($791,404, stated Available, read 20/08/2026) is not in " \
           "needs_confirming" in out
    assert "Dawn lots 1228 not required to need confirming: the confirm window ended on " \
           "01/10/2026" in out
