"""list_ledger, unit by unit (24 Oct 2026): the gate table judge() applies to a list's
newest read, the one-for-one matching the anchor sets older identities aside with, and the
constants pinned to the rules they mirror. The multi-night behaviour is in
tests/test_retirement_multinight.py."""
import json
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, "api")

import harvest_manifest
import list_ledger as LL
import repair_rows as RR

TODAY = datetime(2026, 10, 24)
DOC = "https://docs.example/sheet"


def _d(days_ago):
    return (TODAY - timedelta(days=days_ago)).strftime("%d/%m/%Y")


def _row(i, days_ago=0, channel="E-Agent", status="", lot=None, builder="Example Homes",
         url=DOC, pid="", **kw):
    r = {"id": i, "source_channel": channel, "builder_name": builder, "source_url": url,
         "stocklist_file": url, "source_project_id": pid, "superseded_by": None,
         "availability_status": status, "lot_number": str(lot if lot is not None else i),
         "lot_address": f"Lot {lot if lot is not None else i} Example Street",
         "estate_name": "", "suburb": "", "date_checked": _d(days_ago),
         "content_hash": f"v2:h{i}"}
    r.update(kw)
    return r


def _list(n_seen, n_missing, **kw):
    return [_row(i, 0, **kw) for i in range(1, n_seen + 1)] + \
        [_row(100 + i, 20, **kw) for i in range(n_missing)]


def _key(members):
    return RR._doc_key(members[0], True)


def _ledger(history=()):
    """A ledger holding `history`: [(days_ago, rows_seen, verdict)] for the list keyed DOC."""
    led = LL.Ledger.empty(TODAY)
    led.nights = {LL._iso(TODAY - timedelta(days=n)) for n in range(1, 9)}
    return led, list(history)


def _judge(members, history=(), manifest=None, recorded_basis=None):
    led, hist = _ledger(history)
    key = _key(members)
    lk = LL.list_key(key)
    for days_ago, seen, verdict in hist:
        led.reads[lk].append((datetime(TODAY.year, TODAY.month, TODAY.day)
                              - timedelta(days=days_ago), seen, verdict, ""))
    if recorded_basis:
        led.reads[lk].append((datetime(TODAY.year, TODAY.month, TODAY.day), 0, LL.WHOLE,
                              recorded_basis))
    led.reads[lk].sort()
    tonight = None
    if manifest is not None:
        m = dict({"schema": 1, "entries": [], "documents": {}, "link_notes": {}}, **manifest)
        m["_made"] = TODAY.replace(hour=21)
        tonight = LL.Tonight(m, members, led)
    newest = max(LL._as_date(r["date_checked"]) for r in members)
    return led.judge(key, members, newest, tonight, RR._gone_status, RR.PARTIAL_READ_SHARE,
                     RR.PARTIAL_READ_MIN_ROWS)


def _link(doc, printed, outcome="read", pieces=None, rejected=0):
    return {"channel": "E-Agent", "kind": "stock link", "outcome": outcome,
            "printed": printed, "stocklist_key": harvest_manifest.stocklist_key(doc),
            "stored": {"rejected": rejected},
            "pieces": pieces or [{"name": "f", "outcome": outcome, "printed": printed}]}


def _project(pid, printed, stated=None, whole="", short=False):
    return {"channel": "Proxima", "kind": "project", "pid": pid, "printed": printed,
            "stated": stated, "whole": whole, "short": short, "on_index": True}


def _portal(builder, printed, outcome="read", found=None):
    return {"channel": "Direct Builder Portal (live)", "kind": "portal", "name": builder,
            "outcome": outcome, "printed": printed,
            "cards_found": printed if found is None else found, "cards_read": printed}


PORTAL = dict(channel="Direct Builder Portal (live)", builder="Hermitage Homes")

# (case, members, history, manifest, verdict, basis prefix)
GATES = [
    ("E-Agent 1 of 29, nothing known: the anchor holds it",
     _list(1, 28), (), None, "partial", "anchor 28/29"),
    ("E-Agent 1 of 29 after whole reads of 29: a collapse",
     _list(1, 28), [(2, 29, "whole"), (1, 29, "whole")], None, "partial", "collapsed 1 of 29"),
    ("...and a collapse recorded as partial never becomes the baseline",
     _list(1, 28), [(3, 29, "whole"), (2, 1, "partial"), (1, 1, "partial")], None,
     "partial", "collapsed 1 of 29"),
    ("E-Agent 20 of 29 after whole reads of 29: a whole read on history",
     _list(20, 9), [(1, 29, "whole")], None, "whole", "history 20 of 29"),
    ("E-Agent 12 of 30 with its export's rows accounted for: proven",
     _list(12, 18), (), {"documents": {harvest_manifest.stocklist_key(DOC):
                                       {"csv_rows": 13, "endpoint": "export"}}},
     "whole", "export csv"),
    ("...the same count from gviz proves nothing: the anchor decides",
     _list(12, 18), (), {"documents": {harvest_manifest.stocklist_key(DOC):
                                       {"csv_rows": 13, "endpoint": "gviz"}}},
     "partial", "anchor"),
    ("...and a failed piece vetoes the proof",
     _list(12, 18), (), {"entries": [_link(DOC, 12, pieces=[
         {"name": "a", "outcome": "read", "printed": 12},
         {"name": "b", "outcome": "fetch failed", "printed": 0}])],
         "documents": {harvest_manifest.stocklist_key(DOC):
                       {"csv_rows": 13, "endpoint": "export"}}},
     "partial", "veto: link fetch failed"),
    ("...as does a lot a constraint refused",
     _list(29, 0), (), {"entries": [_link(DOC, 29, rejected=2)]}, "partial",
     "veto: link had 2"),
    ("...and a folder whose budget bound",
     _list(29, 0), (), {"link_notes": {harvest_manifest.stocklist_key(DOC):
                                       {"truncated": True, "why": "file budget"}}},
     "partial", "veto: link truncated"),
    ("Proxima, read short: vetoed whatever the count",
     _list(79, 6, channel="Proxima", pid="587"), [(1, 85, "whole")],
     {"entries": [_project("587", 79, stated=85, short=True)]}, "partial",
     "veto: proxima short 79/85"),
    ("Proxima 80 lots 'proven whole' is still a render cap",
     _list(80, 0, channel="Proxima", pid="587"), (),
     {"entries": [_project("587", 80, stated=80, whole="price sorts met")]}, "partial",
     "veto: proxima read ends on a render cap"),
    ("...and so is 83 with no manifest at all: the count itself is the signature",
     _list(83, 2, channel="Proxima", pid="587"), (), None, "partial",
     "veto: proxima read ends on a render cap (83)"),
    ("...as a portal read of 200 with no manifest is its page length",
     _list(200, 3, url="", **PORTAL), (), None, "partial",
     "veto: portal read ends on its page length (200)"),
    ("Proxima capped and not proven whole",
     _list(79, 1, channel="Proxima", pid="587"), (),
     {"entries": [_project("587", 79, whole="")]}, "partial", "veto: proxima capped"),
    ("Proxima uncapped against its counter: proven",
     _list(5, 1, channel="Proxima", pid="53"), (),
     {"entries": [_project("53", 5, stated=5, whole="uncapped")]}, "whole",
     "proxima uncapped 5/5"),
    ("Proxima uncapped with no counter proves nothing: the anchor decides",
     _list(5, 1, channel="Proxima", pid="53"), (),
     {"entries": [_project("53", 5, whole="uncapped")]}, "whole", "anchor 1/6"),
    ("Proxima is never whole on history: 1 of 29 after whole reads of 29 -> anchor",
     _list(1, 28, channel="Proxima", pid="587"), [(1, 29, "whole")], None, "partial",
     "anchor 28/29"),
    ("A portal is never whole on history either",
     _list(25, 4, url="", **PORTAL), [(1, 2, "whole")], None, "whole", "anchor 4/29"),
    ("A portal read of exactly 100 is a page-length signature",
     _list(100, 1, url="", **PORTAL), (), {"entries": [_portal("Hermitage Homes", 100)]},
     "partial", "veto: portal read ends on its page length"),
    ("A portal whose sign-in failed",
     _list(10, 0, url="", **PORTAL), (),
     {"entries": [_portal("Hermitage Homes", 10, outcome="login failed")]}, "partial",
     "veto: portal login failed"),
    ("A portal that read fewer cards than it found",
     _list(10, 0, url="", **PORTAL), (),
     {"entries": [_portal("Hermitage Homes", 10, found=12)]}, "partial",
     "veto: portal read 10 of 12"),
    ("The Stocklist search ending clean proves its read",
     _list(5, 1, channel="E-Agent Stocklist", url="https://api.example/s"), (),
     {"entries": [{"channel": "E-Agent Stocklist", "kind": "search", "state": "ok",
                   "short": False}]}, "whole", "stocklist search ok"),
    ("...and stopping at its cap vetoes it",
     _list(5, 1, channel="E-Agent Stocklist", url="https://api.example/s"), (),
     {"entries": [{"channel": "E-Agent Stocklist", "kind": "search", "state": "ok",
                   "short": True}]}, "partial", "veto: stocklist search ok, short"),
    ("Gone-status rows are set aside from the anchor's yardstick",
     _list(5, 0, channel="Proxima", pid="53") +
     [_row(200 + i, 20, channel="Proxima", pid="53", status="Not Available")
      for i in range(20)], (), None, "whole", "anchor 0/5 accountable missing (20"),
    ("...but the floor counts every member: 1 of 9 blank read, 20 Sold aside",
     [_row(1, 0, **PORTAL, url="")] + [_row(i, 20, **PORTAL, url="") for i in range(2, 10)]
     + [_row(i, 20, status="Sold", **PORTAL, url="") for i in range(10, 30)], (), None,
     "partial", "anchor 8/9"),
]


@pytest.mark.parametrize("case,members,history,manifest,verdict,basis", GATES,
                         ids=[g[0][:60] for g in GATES])
def test_the_gate_table(case, members, history, manifest, verdict, basis):
    v = _judge(members, history, manifest)
    assert (v.verdict, v.basis[:len(basis)]) == (verdict, basis), (case, v.basis)


def test_a_person_can_accept_a_read_and_a_veto_still_wins():
    members = _list(1, 28, **dict(PORTAL, url=""))
    accepted = LL.ACCEPTED + " at 2026-10-24T21:00:00"
    assert _judge(members, recorded_basis=accepted).verdict == "whole"
    vetoed = _judge(members, recorded_basis=accepted,
                    manifest={"entries": [_portal("Hermitage Homes", 1,
                                                  outcome="login failed")]})
    assert vetoed.verdict == "partial" and vetoed.vetoed


def test_older_identities_are_matched_one_for_one():
    """Two older identities of lot 7 and one row of lot 7 on the read: one is set aside,
    the other still counts as missing. A different estate is a different lot."""
    on_read = [_row(1, 0, lot=7, estate_name="Banksia Rise")]
    missing = [_row(2, 20, lot=7, estate_name="Banksia Rise"),
               _row(3, 30, lot=7, estate_name="Banksia Rise"),
               _row(4, 20, lot=7, estate_name="Other Estate", lot_address="")]
    got = LL.older_identities(missing, on_read)
    assert set(got) == {2}, "matched more than one, or not the most recently seen"
    assert LL.same_lot(missing[0], on_read[0])
    assert not LL.same_lot(missing[2], on_read[0])
    assert not LL.same_lot(_row(5, 0, lot=7, builder="Other Homes"), on_read[0])


def test_the_street_is_the_address_without_its_numbers():
    assert LL._street("Lot 12 Example Street") == "example street"
    assert LL._street("LOT 1203A, 14 Banksia Rise Road") == "banksia rise road"
    assert LL._street("") == ""


def test_the_bars_are_pinned_to_the_rules_they_mirror():
    import _candidates
    from sources import proxima
    assert LL.FULL_READ_SHARE == 1 - RR.PARTIAL_READ_SHARE / 100 == _candidates.FULL_READ_SHARE
    assert LL.FULL_READ_RUNS == _candidates.FULL_READ_RUNS
    assert LL.RENDER_CAPS_FALLBACK == tuple(_candidates.PROXIMA_RENDER_CAPS) == \
        tuple(proxima._CAP_SIGNATURES) == LL.render_caps()
    assert LL.old_counter_gap_ok is harvest_manifest.old_counter_gap_ok
    assert LL.PROOF_REQUIRED <= RR.RELISTING_CHANNELS
    assert "digital email" not in RR.RELISTING_CHANNELS
    assert (RR.WITHDRAWN_AFTER_DAYS, RR.SOURCE_READ_WITHIN_DAYS) == (14, 3)
    assert set(LL.FAILED_OUTCOMES) <= set(harvest_manifest.OUTCOMES)


def _db(tmp_path):
    con = sqlite3.connect(str(tmp_path / "l.db"))
    con.execute("CREATE TABLE buildings (id INTEGER PRIMARY KEY, superseded_by TEXT, "
                "superseded_at TEXT, availability_status TEXT, status_previous TEXT)")
    LL.ensure_tables(con)
    return con


def test_pruning_drops_old_nights_and_keeps_every_retirement(tmp_path):
    """(Every read whole: the read a step is measured from -- the latest whole one -- is
    kept whatever its age, tests/test_retirement_attack_24oct.py; here it is 10 days old.)"""
    con = _db(tmp_path)
    for days in (10, 119, 121, 400):
        night = LL._iso(TODAY - timedelta(days=days))
        con.execute("INSERT INTO list_reads (list_key, read_date, verdict) VALUES ('k', ?, "
                    "'whole')", (night,))
        con.execute("INSERT INTO source_offers (night, channel, unit) VALUES (?, 'E-Agent', "
                    "'u')", (night,))
        con.execute("INSERT INTO channel_nights (night, channel) VALUES (?, 'E-Agent')",
                    (night,))
        con.execute("INSERT INTO retirements (row_id, stamp) VALUES (1, ?)", (night,))
    LL.prune(con, TODAY)
    for t in ("list_reads", "source_offers", "channel_nights"):
        assert con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 2, t
    assert con.execute("SELECT COUNT(*) FROM retirements").fetchone()[0] == 4


def test_undo_puts_back_the_status_and_holds_until_the_lot_is_seen(tmp_path):
    con = _db(tmp_path)
    con.execute("INSERT INTO buildings VALUES (1, ?, 'x', 'Sold', 'Available')",
                (LL.WITHDRAWN_FROM_SOURCE,))
    con.execute("INSERT INTO retirements (row_id, stamp, status_before) VALUES "
                "(1, 's1', 'Available')")
    assert LL.undo_retirements(con, "s1", now=TODAY) == 1
    assert con.execute("SELECT superseded_by, availability_status FROM buildings").fetchone() \
        == (None, "Available")
    led = LL.Ledger(con, TODAY + timedelta(days=1))
    assert led.undone_since(1, TODAY - timedelta(days=30)), "the undo did not hold"
    assert not led.undone_since(1, TODAY + timedelta(days=1)), "a later sighting is new"
    assert LL.undo_retirements(con, "s1") == 0, "an undo is not repeated"


def test_a_manifest_from_another_night_is_not_tonights(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"schema": 1, "generated": "24/10/2026 02:10", "entries": []}))
    m = LL.read_manifest(p, now=datetime(2026, 10, 24, 3, 0))
    assert m is not None
    # The run's dates come from when it began, never from the hour it ended (review, 24
    # Oct 2026): a manifest written at 02:10 by a run that began at 01:00 holds one date.
    assert LL.Tonight(m, []).read_dates == {"24/10/2026"}
    late = {"checked_at": "2026-10-23T23:40:00", "date_checked": "23/10/2026"}
    assert LL.Tonight(m, [late]).read_dates == {"24/10/2026", "23/10/2026"}, \
        "a run whose first read was stamped before midnight holds reads from both dates"
    yesterday = {"checked_at": "2026-10-23T13:00:00", "date_checked": "23/10/2026"}
    assert LL.Tonight(m, [yesterday]).read_dates == {"24/10/2026"}, \
        "a read from 13 hours before the manifest was taken for tonight's run"
    said = dict(m, started="23/10/2026 23:30")
    assert LL.Tonight(said, [yesterday]).read_dates == {"24/10/2026", "23/10/2026"}, \
        "the manifest's own start was not believed"
    assert LL.Tonight(dict(m, started="24/10/2026 01:00"), [late]).read_dates == \
        {"24/10/2026"}, "the rows overrode the manifest's own start"
    assert LL.read_manifest(p, now=datetime(2026, 10, 25, 3, 0)) is None
    assert LL.read_manifest(tmp_path / "absent.json") is None


def _tonight_says(entries, rows, started="24/10/2026 20:00"):
    m = {"schema": 1, "entries": entries, "documents": {}, "link_notes": {},
         "started": started, "_made": TODAY.replace(hour=21)}
    return LL.Tonight(m, rows)


def test_a_recorded_veto_is_never_rejudged():
    """Review, 24 Oct 2026. A read the channel's own record called cut stays cut: not on a
    night whose manifest has no line for it, not on a night that reads it again (a
    midnight run), and not when that line would prove it whole."""
    members = _list(15, 5, channel="Proxima", pid="53")
    for r in members:
        r["checked_at"] = "2026-10-24T20:30:00"
    key = _key(members)
    lk = LL.list_key(key)
    newest = max(LL._as_date(r["date_checked"]) for r in members)
    whole_line = [_project("53", 15, stated=15, whole="uncapped")]
    for entries in ([], whole_line):
        led, _h = _ledger()
        led.reads[lk].append((newest, 15, LL.PARTIAL, "veto: proxima short 15/20"))
        v = led.judge(key, members, newest, _tonight_says(entries, members),
                      RR._gone_status, RR.PARTIAL_READ_SHARE, RR.PARTIAL_READ_MIN_ROWS)
        assert (v.verdict, v.basis, v.recorded) == (
            LL.PARTIAL, "veto: proxima short 15/20", True), (entries, v.basis)
    # The twin: a recorded verdict that is not a veto IS re-judged by tonight's own line
    # for a read tonight's run made...
    led, _h = _ledger()
    led.reads[lk].append((newest, 15, LL.PARTIAL, "anchor 5/20 accountable missing"))
    v = led.judge(key, members, newest, _tonight_says(whole_line, members),
                  RR._gone_status, RR.PARTIAL_READ_SHARE, RR.PARTIAL_READ_MIN_ROWS)
    assert (v.verdict, v.recorded) == (LL.WHOLE, None), v.basis
    # ...but not when the run began after the read was stamped: that read is yesterday's.
    led, _h = _ledger()
    led.reads[lk].append((newest, 15, LL.PARTIAL, "anchor 5/20 accountable missing"))
    v = led.judge(key, members, newest, _tonight_says(whole_line, members,
                                                      started="24/10/2026 20:45"),
                  RR._gone_status, RR.PARTIAL_READ_SHARE, RR.PARTIAL_READ_MIN_ROWS)
    assert (v.verdict, v.recorded) == (LL.PARTIAL, True), v.basis


def test_the_table_never_writes_over_a_recorded_veto(tmp_path):
    """commit()'s own guard, beneath judge's: a verdict for a read whose row already says
    veto leaves the row as it is."""
    con = sqlite3.connect(str(tmp_path / "l.db"))
    led = LL.Ledger(con, TODAY, write=True)
    con.execute("INSERT INTO list_reads (list_key, read_date, verdict, basis, recorded_at) "
                "VALUES ('k1', '2026-10-24', 'partial', 'veto: proxima short 15/20', 'x')")
    v = LL.Verdict(key=("u", "53"), list_key="k1", newest=TODAY, verdict=LL.WHOLE,
                   basis="anchor 5/20", seen=15, members=20, channel="Proxima", label="l",
                   recorded=None)
    led.note(v)
    led.commit()
    assert con.execute("SELECT verdict, basis FROM list_reads").fetchall() == \
        [("partial", "veto: proxima short 15/20")]
    con.close()


def test_a_row_belongs_to_the_link_its_document_was_last_read_through():
    """Review, 24 Oct 2026: rows stored before record_building moved stocklist_file with
    the read still name the link they were first stored under. A document's rows belong to
    the link its most recently read row came through -- for rule B's unit and for every
    veto and note on that link."""
    old, new = "https://files.example/old", "https://files.example/new"
    doc = "https://files.example/doc"
    rows = [_row(1, 20, url=doc, stocklist_file=old),           # sold, never re-read
            _row(2, 0, url=doc, stocklist_file=new),            # read tonight via new
            _row(3, 0, url="https://files.example/other", stocklist_file=old)]
    links = LL.current_links(rows)
    assert links == {doc: new, "https://files.example/other": old}
    fk = LL.file_key_fn()
    assert LL.row_unit(rows[0], fk, links) == LL.row_unit(rows[1], fk, links) == \
        LL.sha16(fk(new))
    assert LL.row_unit(rows[0], fk) == LL.sha16(fk(old)), "the row's own link changed"
    # ...and the document's own url, in case it is its own link (merged with main 1858d55).
    assert LL.links_of(rows[:1], links) == sorted({old, new, doc})
    tonight = _tonight_says([_link(new, 1, rejected=1)], rows)
    assert tonight.veto_and_proof((doc, ""), rows[:1], 0)[0].startswith(
        "link had 1 lot(s) refused"), "a veto on the new link missed its document"


@pytest.mark.parametrize("note,why", [
    (None, "does not say whether every listed project was read"),
    ({"stats": {"project unreadable": 2}, "projects_seen": 9, "projects_logged": 7,
      "errors": 0}, "2 listed project(s) could not be read (project unreadable 2)"),
    ({"stats": {}, "projects_seen": 9, "projects_logged": 8, "errors": 0},
     "8 of 9 listed project(s) have a manifest line"),
    ({"stats": {}, "projects_seen": 9, "projects_logged": 9, "errors": 1},
     "logged 1 error(s)"),
    ({"stats": {}, "projects_seen": 9}, "run note is incomplete"),
    ({"stats": {"developer field was not a company name": 335},
      "projects_seen": 9, "projects_logged": 9, "errors": 0}, None),
])
def test_proxima_says_which_nights_can_count_a_project_gone(note, why):
    tonight = LL.Tonight({"runs": {"Proxima": note} if note else {},
                          "_made": TODAY.replace(hour=21)}, [])
    got = LL.proxima_run_problem(tonight)
    assert (got is None) if why is None else (why in str(got)), got


def test_a_young_ledger_judges_no_night_healthy():
    hist = [{"units": 100, "pages": {"p": 10}}] * 2
    assert LL.channel_health(100, {"p": 10}, hist)[0] is False
    hist = [{"units": 100, "pages": {"p": 10, "q": 5}}] * 7
    assert LL.channel_health(95, {"p": 10, "q": 5}, hist) == (True, "")
    assert LL.channel_health(80, {"p": 10, "q": 5}, hist)[0] is False
    assert LL.channel_health(100, {"p": 10, "q": 3}, hist)[0] is False, \
        "a page that lost its links was called healthy"


def test_explain_names_the_list_never_its_link(tmp_path):
    con = _db(tmp_path)
    secret = "https://www.dropbox.com/scl/fo/abc123?rlkey=secret"
    row = _row(1, 0, url=secret)
    lines = "\n".join(LL.explain(con, row, RR._doc_key(row, True)))
    assert "rlkey" not in lines and "dropbox.com/scl" not in lines
    assert LL.list_key(RR._doc_key(row, True)) in lines


def test_the_command_line_accepts_a_read_and_explains_a_row(tmp_path):
    """repair_rows.py --accept LIST_KEY records a person's proof; --explain ROW_ID prints how
    the ledger sees one row. Both run against the database config points at."""
    import os
    import subprocess
    from pathlib import Path
    from database import ResearchDatabase
    path = tmp_path / "cli.db"
    ResearchDatabase(db_path=path).record_building(
        {"source_channel": "Direct Builder Portal (live)", "builder_name": "Hermitage Homes",
         "lot_number": "1", "lot_address": "Lot 1 Example Street", "source_text": "Lot 1",
         "advertised_package_price": 600000, "source_url_or_ref": "https://p.example/lot/1",
         "date_checked": "24/10/2026"})
    con = sqlite3.connect(str(path))
    LL.ensure_tables(con)
    lk = LL.list_key(("Direct Builder Portal (live)|Hermitage Homes", ""))
    con.execute("INSERT INTO list_reads (list_key, read_date, rows_seen, members, verdict, "
                "basis, recorded_at) VALUES (?, '2026-10-24', 1, 29, 'partial', 'anchor', "
                "'2026-10-24T21:00:00')", (lk,))
    con.commit()
    con.close()
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ, SPB_DATABASE_PATH=str(path), PYTHONDONTWRITEBYTECODE="1")

    def cli(*args):
        return subprocess.run([sys.executable, "repair_rows.py", *args], cwd=str(root),
                              env=env, capture_output=True, text=True, timeout=120)
    got = cli("--accept", lk)
    assert got.returncode == 0 and "accepted as whole" in got.stdout, got.stdout + got.stderr
    con = sqlite3.connect(str(path))
    verdict, basis = con.execute("SELECT verdict, basis FROM list_reads").fetchone()
    con.close()
    assert verdict == "whole" and basis.startswith(LL.ACCEPTED)
    got = cli("--explain", "1")
    assert got.returncode == 0 and lk in got.stdout and "p.example" not in got.stdout, \
        got.stdout + got.stderr


def test_a_report_only_ledger_creates_nothing(tmp_path):
    con = sqlite3.connect(str(tmp_path / "r.db"))
    led = LL.Ledger(con, TODAY, write=False)
    led.note(_judge(_list(3, 0)))
    assert led.commit() == 0
    assert not LL.tables_exist(con)
