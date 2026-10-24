"""Retirement over many nights, through the real nightly steps (24 Oct 2026).

Ahsan: "if the stock is retired in the portals it should retire in our agent too". Each
night here runs what run_daily runs, on a scratch database: the harvest's
record_building for every lot the list carries tonight (and a harvest manifest), then
supersede_duplicates.main, then repair_rows.main --apply. Nothing is planted by hand
after night 1.

What a single-night test cannot see, and these do:
  * the nightly reset: a retirement was thrown away whenever its list stopped qualifying
    (Proxima read nothing on 11 runs, 12-23 Oct, and ~280 retirements were undone a night);
  * the ratchet: a list that sells steadily crossed 40% "missing" of everything ever
    stored, and from then on retired nothing, ever;
  * the safety rule over time: a read that comes back with 1 of 29, or stops at the
    render cap, must retire nothing however many nights it repeats.
"""
import contextlib
import io
import json
import sqlite3
from datetime import datetime, timedelta

import pytest

import harvest_manifest
from database import ResearchDatabase, WITHDRAWN_FROM_SOURCE

DAY0 = datetime(2026, 9, 1, 3, 0)
PORTAL = "Direct Builder Portal (live)"


def _night_dt(n):
    return DAY0 + timedelta(days=n)


def _d(n):
    return _night_dt(n).strftime("%d/%m/%Y")


def _lot(n, night, doc="https://docs.example/sheet-a", channel="E-Agent", status="",
         builder="Example Homes", **kw):
    row = {"source_channel": channel, "attribution_scope": "builder",
           "builder_name": builder, "suburb": "", "lot_number": str(n),
           "lot_address": f"Lot {n} Example Street",
           "source_text": f"Lot {n} Example Street 4 bed ${600000 + n * 1000:,} {status}",
           "advertised_package_price": 600000 + n * 1000, "availability_status": status,
           "source_url_or_ref": doc, "stocklist_file": doc, "date_checked": _d(night),
           "checked_at": _night_dt(night).isoformat(timespec="seconds")}
    row.update(kw)
    return row


class _Clock(datetime):
    """repair_rows' datetime, stopped at the night being run (the harvest stamps its own
    dates, as every source does)."""
    current = DAY0

    @classmethod
    def now(cls, tz=None):
        return cls.current


class Nights:
    """A scratch database and the nightly chain, one call per night."""

    def __init__(self, tmp_path, monkeypatch):
        import config
        import repair_rows as RR
        self.path = tmp_path / "nights.db"
        self.db = ResearchDatabase(db_path=self.path)
        con = sqlite3.connect(str(self.path))
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_buildings_content_hash "
                    "ON buildings(content_hash)")
        con.commit()
        con.close()
        self.manifest = tmp_path / "harvest_manifest.json"
        monkeypatch.setattr(config, "DATABASE_PATH", self.path)
        monkeypatch.setattr(RR, "datetime", _Clock)
        # Not a retirement repair, and it loads the whole suburb gazetteer every night.
        monkeypatch.setattr(RR, "find_refuted_states", lambda live: [])
        try:
            import list_ledger
            monkeypatch.setattr(list_ledger, "MANIFEST", self.manifest)
        except ImportError:                    # origin/main: no ledger, no manifest read
            pass
        monkeypatch.setenv("SPB_SKIP_REREAD", "1")
        self.started = False
        self.out = ""

    def run(self, night, lots, entries=(), documents=None, link_notes=None, apply=True,
            manifest=True, runs=None, started=None, generated=None, after_harvest=None,
            read_date=None):
        import repair_rows as RR
        import supersede_duplicates as SD
        # Another list, read every night, whose one line also reached the inbox: the
        # night always has a duplicate pair, as every real night does, so
        # supersede_duplicates really runs its reset (it skips it when it finds none).
        self.db.record_building(_lot(900, night, doc="https://docs.example/other-list"))
        if not self.started:
            self.db.record_building(_lot(900, night, channel="digital email", doc="inbox"))
            self.started = True
        for b in lots:
            self.db.record_building(dict(b))
        if after_harvest is not None:          # what an older harvest would have stored
            after_harvest(self.path)
        if manifest:                           # else last night's: too old to be tonight's
            m = {"schema": 1,
                 "generated": (generated or _night_dt(night)).strftime("%d/%m/%Y %H:%M"),
                 "entries": list(entries), "documents": documents or {},
                 "link_notes": link_notes or {}}
            if runs is not None:               # else a harvest from before "runs" existed
                m["runs"] = runs
            if started is not None:
                m["started"] = started.strftime("%d/%m/%Y %H:%M")
            if read_date is not None:          # the run's one read date (one_read_date)
                m["read_date"] = read_date.strftime("%d/%m/%Y")
            self.manifest.write_text(json.dumps(m), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            SD.main([])
            _Clock.current = _night_dt(night)
            assert RR.main(apply) == 0
        self.out = buf.getvalue()

    def rows(self):
        con = sqlite3.connect(str(self.path))
        con.row_factory = sqlite3.Row
        out = {r["lot_number"]: dict(r) for r in con.execute(
            "SELECT * FROM buildings WHERE lot_number <> '900'")}
        con.close()
        return out

    def retired(self):
        return {k for k, r in self.rows().items()
                if r["superseded_by"] == WITHDRAWN_FROM_SOURCE}

    def hidden(self):
        return {k for k, r in self.rows().items() if r["superseded_by"]}


# ------------------------------------------------------------------ 1. persistence

def test_a_retirement_survives_the_nights_its_list_goes_unread(tmp_path, monkeypatch):
    """Lot 12 leaves its list on night 4. It retires on night 17 (absent 14 days, list read
    whole). Then the list goes unread for a week -- a portal outage. On 24 Oct's rule the
    reset threw the retirement away the night the list fell outside the 3-day window, and
    the lot came back live with "Sold"; the source never listed it again."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 18):
        carried = range(1, 13) if night < 4 else range(1, 12)
        n.run(night, [_lot(i, night, status="Available") for i in carried])
    assert n.retired() == {"12"}, "the lot its list stopped carrying was not retired"
    for night in range(18, 26):                       # the outage: nothing is read
        n.run(night, [])
    rows = n.rows()
    assert n.retired() == {"12"}, "a retirement was undone by a night nobody read the list"
    assert rows["12"]["availability_status"] == "Sold"
    assert rows["12"]["status_previous"] == "Available", \
        "re-retiring overwrote what the lot said before it was retired"


def test_the_old_rule_is_a_whole_rollback(tmp_path, monkeypatch):
    """SPB_RETIREMENT_RULE=old: the nights run exactly as on 24 Oct 2026 -- the outage
    throws the retirement away again -- and the ledger records nothing."""
    monkeypatch.setenv("SPB_RETIREMENT_RULE", "old")
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 18):
        carried = range(1, 13) if night < 4 else range(1, 12)
        n.run(night, [_lot(i, night, status="Available") for i in carried])
    assert n.retired() == {"12"}
    for night in range(18, 22):
        n.run(night, [])
    assert n.retired() == set()
    con = sqlite3.connect(str(n.path))
    names = {t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert not names & {"list_reads", "retirements", "source_offers", "channel_nights"}


def test_supersede_keeps_a_retirement_and_clears_what_it_owns(tmp_path, monkeypatch):
    """The reset clears only the marks supersede re-derives. A withdrawn lot that also
    loses a duplicate group keeps the mark that says why it is gone."""
    import supersede_duplicates as SD
    n = Nights(tmp_path, monkeypatch)
    n.db.record_building(_lot(1, 1))
    n.db.record_building(_lot(2, 1))
    n.db.record_building(_lot(5, 1, source_text="Lot 5 twin $605,000",
                              advertised_package_price=605000))
    con = sqlite3.connect(str(n.path))
    con.execute("UPDATE buildings SET superseded_by=? WHERE lot_number IN ('1', '5')",
                (WITHDRAWN_FROM_SOURCE,))
    con.execute("UPDATE buildings SET superseded_by='spreadsheet filter row, not a listing'"
                " WHERE lot_number='2'")
    con.commit()
    con.close()
    # Lot 5's line also arrives tonight through another list: lot 5 (retired) loses the
    # duplicate group to it on freshness, and must keep its own mark.
    n.db.record_building(_lot(6, 2, source_text="Lot 5 twin $605,000",
                              advertised_package_price=605000,
                              doc="https://docs.example/sheet-b"))
    n.db.record_building(_lot(3, 1, source_text="Lot 3 twin $603,000",
                              advertised_package_price=603000))
    n.db.record_building(_lot(4, 2, source_text="Lot 3 twin $603,000",
                              advertised_package_price=603000))
    with contextlib.redirect_stdout(io.StringIO()):
        SD.main([])
    rows = n.rows()
    assert rows["1"]["superseded_by"] == WITHDRAWN_FROM_SOURCE, "the reset undid a retirement"
    assert rows["5"]["superseded_by"] == WITHDRAWN_FROM_SOURCE, \
        "a duplicate mark was written over a retirement"
    assert rows["2"]["superseded_by"] is None, "a stale repair mark survived the reset"
    assert rows["3"]["superseded_by"] == rows["4"]["content_hash"], \
        "the duplicate found tonight was not retired"


def test_the_old_rule_clears_every_mark_again(tmp_path, monkeypatch):
    """SPB_RETIREMENT_RULE=old is the rollback: the reset is what it was on 24 Oct."""
    import supersede_duplicates as SD
    monkeypatch.setenv("SPB_RETIREMENT_RULE", "old")
    n = Nights(tmp_path, monkeypatch)
    n.db.record_building(_lot(1, 1))
    n.db.record_building(_lot(3, 1, source_text="Lot 3 twin $603,000",
                              advertised_package_price=603000))
    n.db.record_building(_lot(4, 2, source_text="Lot 3 twin $603,000",
                              advertised_package_price=603000))
    con = sqlite3.connect(str(n.path))
    con.execute("UPDATE buildings SET superseded_by=? WHERE lot_number='1'",
                (WITHDRAWN_FROM_SOURCE,))
    con.commit()
    con.close()
    with contextlib.redirect_stdout(io.StringIO()):
        SD.main([])
    assert n.rows()["1"]["superseded_by"] is None


def test_a_lot_read_tonight_is_never_hidden_behind_a_retired_copy(tmp_path, monkeypatch):
    """Across channels supersede keeps the better-described capture (_detail), not the
    newest. A portal lot retired weeks ago -- suburb, state and spec filled -- and the
    same line on an E-Agent list read tonight with none of them: the retired copy wins the
    group, and the lot the source is listing tonight was hidden behind it. With
    retirements kept that would last for good, so the newer capture stays live."""
    import supersede_duplicates as SD
    n = Nights(tmp_path, monkeypatch)
    line = "Lot 7 Banksia Rise 4 bed $707,000"
    n.db.record_building(_lot(7, 1, channel="Direct Builder Portal (live)",
                              builder="Hermitage Homes", doc="https://portal.example/lot/7",
                              source_text=line, suburb="Tarneit", state="VIC",
                              bedrooms=4, bathrooms=2, car_spaces=2, land_sqm=400,
                              house_sqm=200, advertised_package_price=707000))
    con = sqlite3.connect(str(n.path))
    con.execute("UPDATE buildings SET superseded_by=?, availability_status='Sold' "
                "WHERE lot_number='7'", (WITHDRAWN_FROM_SOURCE,))
    con.commit()
    con.close()
    n.db.record_building(_lot(77, 30, source_text=line, advertised_package_price=707000))
    with contextlib.redirect_stdout(io.StringIO()):
        SD.main([])
    rows = n.rows()
    assert rows["7"]["superseded_by"] == WITHDRAWN_FROM_SOURCE
    assert rows["77"]["superseded_by"] is None, \
        "the lot its source listed tonight was hidden behind a retired copy"


def test_an_older_copy_still_loses_to_a_retired_one(tmp_path, monkeypatch):
    """The guard is about a NEWER read only: a copy nobody has read since the retired one
    was last seen is not evidence the lot is back, and stays hidden."""
    import supersede_duplicates as SD
    n = Nights(tmp_path, monkeypatch)
    line = "Lot 8 Banksia Rise 4 bed $708,000"
    n.db.record_building(_lot(8, 10, channel="Direct Builder Portal (live)",
                              builder="Hermitage Homes", doc="https://portal.example/lot/8",
                              source_text=line, suburb="Tarneit", state="VIC",
                              bedrooms=4, advertised_package_price=708000))
    con = sqlite3.connect(str(n.path))
    con.execute("UPDATE buildings SET superseded_by=? WHERE lot_number='8'",
                (WITHDRAWN_FROM_SOURCE,))
    con.commit()
    con.close()
    n.db.record_building(_lot(88, 5, source_text=line, advertised_package_price=708000))
    with contextlib.redirect_stdout(io.StringIO()):
        SD.main([])
    rows = n.rows()
    assert rows["88"]["superseded_by"] == rows["8"]["content_hash"]


# ------------------------------------------------------------------ 2. the status comes back

def test_a_lot_the_source_lists_again_comes_back_saying_what_it_said(tmp_path, monkeypatch):
    """A sheet with no status column. Lot 12 is retired ("Sold"), then the sheet lists it
    again. It is for sale again -- not live and still saying Sold, which kept it out of
    every search while the Settings tab counted it as held."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 18):
        carried = range(1, 13) if night < 4 else range(1, 12)
        n.run(night, [_lot(i, night) for i in carried])
    assert n.retired() == {"12"}
    n.run(18, [_lot(i, 18) for i in range(1, 13)])
    lot = n.rows()["12"]
    assert lot["superseded_by"] is None
    assert not lot["availability_status"], lot["availability_status"]


def _retired_row(tmp_path, status_previous, ledger_status=None, with_table=True):
    """One lot, retired the way repair_rows retires it, with or without a ledger row."""
    path = tmp_path / "one.db"
    db = ResearchDatabase(db_path=path)
    db.record_building(_lot(1, 1, status="Available"))
    con = sqlite3.connect(str(path))
    con.execute("UPDATE buildings SET superseded_by=?, availability_status='Sold', "
                "status_previous=? WHERE id=1", (WITHDRAWN_FROM_SOURCE, status_previous))
    if with_table:
        con.execute("CREATE TABLE retirements (row_id INTEGER, stamp TEXT, rule TEXT, "
                    "list_key TEXT, read_date TEXT, silent_days INTEGER, basis TEXT, "
                    "status_before TEXT, undone_at TEXT, undone_by TEXT)")
        if ledger_status is not False:
            con.execute("INSERT INTO retirements (row_id, stamp, rule, status_before) "
                        "VALUES (1, '2026-09-20T03:00:00', 'list re-read without it', ?)",
                        (ledger_status,))
    con.commit()
    con.close()
    return db, path


def _one(path):
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    r = dict(con.execute("SELECT * FROM buildings WHERE id=1").fetchone())
    log = [dict(x) for x in con.execute("SELECT * FROM retirements")] \
        if con.execute("SELECT name FROM sqlite_master WHERE name='retirements'").fetchone() \
        else []
    con.close()
    return r, log


@pytest.mark.parametrize("ledger_status,want", [("Available", "Available"),
                                                ("Under Offer", "Under Offer"),
                                                (None, None)])
def test_a_relisted_lot_gets_back_the_status_its_retirement_recorded(tmp_path, ledger_status,
                                                                     want):
    db, path = _retired_row(tmp_path, "Sold", ledger_status)
    db.record_building(_lot(1, 30))                    # tonight's read states no status
    r, log = _one(path)
    assert r["superseded_by"] is None
    assert r["availability_status"] == want, "a NULL became a status, or Sold stayed"
    assert r["status_previous"] == "Sold"
    assert log[0]["undone_by"] == "source" and log[0]["undone_at"]


@pytest.mark.parametrize("status_previous,want", [("Available", "Available"),
                                                  ("Sold", None), ("", None)])
def test_a_retirement_older_than_the_ledger_falls_back_to_status_previous(
        tmp_path, status_previous, want):
    """'Sold' over 'Sold' is the old nightly re-retirement, not a status anyone stated."""
    db, path = _retired_row(tmp_path, status_previous, with_table=False)
    db.record_building(_lot(1, 30))
    r, _log = _one(path)
    assert r["superseded_by"] is None
    assert r["availability_status"] == want


def test_a_relisted_lot_whose_read_states_a_status_takes_that_one(tmp_path):
    db, path = _retired_row(tmp_path, "Sold", "Available")
    db.record_building(_lot(1, 30, status="On Hold"))
    r, log = _one(path)
    assert r["availability_status"] == "On Hold"
    assert log[0]["undone_by"] == "source"


# ------------------------------------------------------------------ manifest lines

def _unit(doc):
    from sources.e_agent import stocklist_file_key
    return harvest_manifest.stocklist_key(stocklist_file_key(doc))


def _link(doc, printed, page="house-and-land-1", heading="Example Homes", label="Price List",
          outcome="read", pieces=None, rejected=0):
    """An E-Agent stock-link line as harvest_manifest writes it."""
    return {"channel": "E-Agent", "kind": "stock link", "name": heading, "page": page,
            "heading": heading, "label": label, "outcome": outcome, "printed": printed,
            "stored": {"new": 0, "updated": 0, "unchanged": printed, "rejected": rejected},
            "stocklist_key": harvest_manifest.stocklist_key(doc), "unit": _unit(doc),
            "pieces": pieces if pieces is not None else
            [{"name": "file", "outcome": outcome, "printed": printed, "why": ""}]}


def _proxima(i, night, pid="587", **kw):
    return _lot(i, night, channel="Proxima", builder="Damac Properties",
                doc="https://proxima.example/projects", source_project_id=pid, **kw)


def _project_entry(pid, printed, stated=None, whole="", short=False, on_index=True):
    return {"channel": "Proxima", "kind": "project", "pid": str(pid), "printed": printed,
            "stated": stated, "whole": whole, "short": short, "on_index": on_index,
            "outcome": "read" if printed else "zero-yield",
            "stored": {"new": 0, "updated": 0, "unchanged": printed, "rejected": 0}}


def _px_run(projects, logged=None, errors=0, stats=None):
    """manifest["runs"]["Proxima"] as harvest_buildings writes it: the reader's own skip
    counts ({"project unreadable": 1}, ...) and how many projects it listed and logged."""
    return {"Proxima": {"stats": dict(stats or {}),
                        "projects_seen": projects,
                        "projects_logged": projects if logged is None else logged,
                        "errors": errors}}


def _portal_entry(builder, printed, found=None, read=None, host="", outcome="read"):
    return {"channel": PORTAL, "kind": "portal", "name": builder, "outcome": outcome,
            "printed": printed, "cards_found": printed if found is None else found,
            "cards_read": printed if read is None else read, "host": host,
            "stored": {"new": 0, "updated": 0, "unchanged": printed, "rejected": 0}}


def _ledger_reads(path):
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    out = [dict(r) for r in con.execute("SELECT * FROM list_reads ORDER BY read_date")]
    con.close()
    return out


# ------------------------------------------------------------------ 3. the ratchet

def test_a_list_that_sells_steadily_keeps_retiring_what_it_sold(tmp_path, monkeypatch):
    """THE RATCHET. A 20-lot list sells one lot every second night from night 3 to night
    21 -- half of it. Judged against every row ever stored it crossed 40% "missing" on
    night 18 and retired nothing from then on (and its earlier retirements were reset).
    Judged against its own recent whole reads, every read is whole and every sold lot
    retires a fortnight after it left."""
    n = Nights(tmp_path, monkeypatch)
    gone_on = {i: 1 + 2 * i for i in range(1, 11)}   # lot i leaves on night 2i+1
    for night in range(1, 36):
        n.run(night, [_lot(i, night) for i in range(1, 21)
                      if gone_on.get(i, 99) > night])
        retired = n.retired()
        assert all(gone_on.get(int(k), 99) + 13 <= night for k in retired), \
            f"night {night}: a lot retired before a fortnight's absence: {retired}"
    assert n.retired() == {str(i) for i in range(1, 11)}, \
        f"the sold lots that stayed live: {set(map(str, range(1, 11))) - n.retired()}"


def test_the_accountable_anchor_frees_a_list_held_by_its_own_gone_rows(tmp_path,
                                                                       monkeypatch):
    """Proxima project 53, 24 Oct: 311 of its 312 'missing' rows are 'Not Available'
    captures from one read on 27/09, and tonight's read is 5 of 5. Measured over every
    stored row it is a failed read for good, so a lot that really left never retires.
    The source already said those 311 are gone; they are not evidence of a half read.
    (The captures are from before the ledger's first night, as on 24 Oct: dropped WITH
    lot 6 on one night, on a list main holds partial, they would be a step --
    tests/test_retirement_attack3_25oct.py.)"""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 18):
        carried = range(1, 7) if night == 1 else range(1, 6)
        lots = [_proxima(i, night, pid="53", status="Available") for i in carried]
        if night == 1:
            lots += [_proxima(i, -20, pid="53", status="Not Available")
                     for i in range(100, 120)]
        n.run(night, lots, entries=[_project_entry("53", len(carried), whole="uncapped")])
    assert "6" in n.retired(), "the lot the project stopped listing was never retired"
    assert not {"1", "2", "3", "4", "5"} & n.retired()


def test_a_night_without_a_manifest_holds_the_new_rules(tmp_path, monkeypatch):
    """With no harvest manifest no veto can fire, so that night nothing beyond what the old
    rule retires is written: the anchor's retirement of lot 6 waits for a night that has
    one."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 18):
        carried = range(1, 7) if night == 1 else range(1, 6)
        lots = [_proxima(i, night, pid="53", status="Available") for i in carried]
        if night == 1:
            lots += [_proxima(i, -20, pid="53", status="Not Available")
                     for i in range(100, 120)]
        n.run(night, lots, entries=[_project_entry("53", len(carried), whole="uncapped")],
              manifest=night not in (15, 16))
        if night == 16:
            assert "6" not in n.retired(), "retired on a night with no manifest"
            assert "no harvest manifest tonight" in n.out
    assert "6" in n.retired()


@pytest.mark.parametrize("floor_on_members", [True, False])
def test_rows_set_aside_never_drop_a_big_list_under_the_floor(tmp_path, monkeypatch,
                                                              floor_on_members):
    """THE ANCHOR'S SAFETY, pure. 29 portal lots, 20 of them later marked Sold by a repair,
    then a read that returns 1. Set aside, 9 accountable rows are under the 10-row floor --
    but the floor is tested on all 29, so 8 of 9 missing is still a failed read and nothing
    retires. The twin tests the floor on the accountable rows and retires the 8: the rule
    holding is what keeps them, not the harness. (The twin also switches off the step
    guard, which holds the same 8-of-9 drop on its own.)"""
    import list_ledger
    if not floor_on_members:
        monkeypatch.setattr(list_ledger, "ANCHOR_FLOOR_ON_MEMBERS", False)
        monkeypatch.setattr(list_ledger, "STEP_MIN_ROWS", 10 ** 6)
    n = Nights(tmp_path, monkeypatch)
    kw = {"channel": PORTAL, "builder": "Hermitage Homes"}
    for night in range(1, 21):
        carried = range(1, 30) if night == 1 else [1]
        n.run(night, [_lot(i, night, doc=f"https://portal.example/lot/{i}", **kw)
                      for i in carried])
        if night == 1:
            con = sqlite3.connect(str(n.path))
            con.execute("UPDATE buildings SET availability_status='Sold' WHERE "
                        "source_channel=? AND CAST(lot_number AS INTEGER) >= 10", (PORTAL,))
            con.commit()
            con.close()
    if floor_on_members:
        assert n.retired() == set()
    else:
        assert {str(i) for i in range(2, 10)} <= n.retired()


@pytest.mark.parametrize("floor", [None, 0.0])
def test_a_staircase_of_drops_never_walks_the_baseline_down(tmp_path, monkeypatch, floor):
    """A safety test with its twin. 100 rows read whole, then 62 for a week, then 39: each
    step is under 40% of the last week's whole reads, so the history bar alone would walk
    the baseline down to 39 and retire 61 lots of a list that may simply be breaking. The
    drift floor holds every read below half of the largest whole read of the last 28 days.
    The twin (DRIFT_FLOOR 0) retires all 61. The step guard, which holds each of these
    steps on its own (tests/test_retirement_attack_24oct.py), is off in both: this is the
    drift floor's test."""
    import list_ledger
    monkeypatch.setattr(list_ledger, "STEP_MIN_ROWS", 10 ** 6)
    if floor is not None:
        monkeypatch.setattr(list_ledger, "DRIFT_FLOOR", floor)
    n = Nights(tmp_path, monkeypatch)
    doc = "https://docs.example/staircase"
    for night in range(1, 29):
        top = 100 if night <= 3 else 62 if night <= 10 else 39
        n.run(night, [_lot(i, night, doc=doc) for i in range(1, top + 1)])
    if floor is None:
        assert n.retired() == set()
        last = [r for r in _ledger_reads(n.path) if r["rows_seen"] == 39][-1]
        assert last["verdict"] == "partial" and "drift floor" in last["basis"], last
    else:
        assert len(n.retired()) == 61


# ------------------------------------------------------------------ 4. the safety rule

@pytest.mark.parametrize("channel,builder,doc", [
    ("E-Agent", "Example Homes", lambda i: "https://docs.example/sheet-c"),
    (PORTAL, "Hermitage Homes", lambda i: f"https://portal.example/package/{i}"),
])
def test_a_list_that_comes_back_with_one_of_29_retires_nothing_for_a_month(
        tmp_path, monkeypatch, channel, builder, doc):
    """THE SAFETY RULE, over time. 29 lots read whole for five nights, then one lot a
    night for thirty. A collapse never becomes the yardstick by repeating, so nothing on
    the list ever retires; and the portal, whose count can never prove a read whole,
    stays on the anchor (28 of 29 missing: a failed read)."""
    import repair_rows as RR
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 36):
        carried = range(1, 30) if night <= 5 else [1]
        n.run(night, [_lot(i, night, channel=channel, builder=builder, doc=doc(i))
                      for i in carried])
        assert n.retired() == set(), f"night {night}: {sorted(n.retired())} retired"
    con = sqlite3.connect(str(n.path))
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM buildings")]
    try:
        import list_ledger
        judged = {"ledger": list_ledger.Ledger(con, _night_dt(35))}
    except ImportError:
        judged = {}
    assert RR.partial_reads(rows, today=_night_dt(35), **judged), \
        "the half-read list is no longer reported"


def test_the_harness_can_fail_on_a_collapse(tmp_path, monkeypatch):
    """The test above has teeth: with the history bar, the drift floor and the step guard
    switched off the same nights retire the 28 lots, so 'nothing retired' is the rule
    holding, not the harness idling."""
    import list_ledger
    monkeypatch.setattr(list_ledger, "FULL_READ_SHARE", 0.0)
    monkeypatch.setattr(list_ledger, "DRIFT_FLOOR", 0.0)
    monkeypatch.setattr(list_ledger, "STEP_MIN_ROWS", 10 ** 6)
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 36):
        carried = range(1, 30) if night <= 5 else [1]
        n.run(night, [_lot(i, night, doc="https://docs.example/sheet-c") for i in carried])
    assert len(n.retired()) == 28


def test_a_read_stopped_at_the_render_cap_retires_nothing(tmp_path, monkeypatch):
    """Proxima's accordion serves 80 lots a project, and a read that stops there says so
    (short). 12 lots on the board, 10 read every night after the second: on 24 Oct's rule
    2 of 12 missing is 16%, a 'whole' read, and the 2 lots past the cap retired after a
    fortnight. Here the manifest's own word vetoes the read."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 21):
        carried = range(1, 13) if night <= 2 else range(1, 11)
        n.run(night, [_proxima(i, night, status="Available") for i in carried],
              entries=[_project_entry("587", len(carried), stated=12,
                                      whole="uncapped" if night <= 2 else "",
                                      short=night > 2)])
        assert n.retired() == set(), f"night {night}: lots past the cap retired"


def test_a_night_with_a_failed_fetch_retires_nothing(tmp_path, monkeypatch):
    """A folder link whose fetch failed in part tonight: its lists are not judged whole
    tonight, whatever their counts say -- the old rule retired lot 12 that night."""
    n = Nights(tmp_path, monkeypatch)
    doc = "https://docs.example/folder"
    for night in range(1, 18):
        carried = range(1, 13) if night < 4 else range(1, 12)
        failed = [_link(doc, len(carried), pieces=[
            {"name": "a.pdf", "outcome": "read", "printed": len(carried), "why": ""},
            {"name": "b.pdf", "outcome": "fetch failed", "printed": 0, "why": "HTTP 500"}])]
        n.run(night, [_lot(i, night, doc=doc) for i in carried],
              entries=failed if night == 17 else ())
    assert n.retired() == set(), "lot 12 retired on a night its folder half-failed"


def test_an_inbox_never_retires_anything_over_a_month(tmp_path, monkeypatch):
    """A pure safety test: an inbox does not re-list its stock, so absence from it means
    nothing (RELISTING_CHANNELS), and it gets no ledger rows at all."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 41):
        n.run(night, [_lot(i, night, channel="digital email", doc="inbox")
                      for i in (range(1, 6) if night == 1 else [1])])
    assert n.retired() == set()
    assert all(r["channel"] != "digital email" for r in _ledger_reads(n.path))


# ------------------------------------------------------------------ 5. proofs and vetoes

def _tab_nights(n, doc, documents, entries=lambda night: ()):
    """30 lots read whole for three nights, then 12 a night: 18 left at once."""
    for night in range(1, 4):
        n.run(night, [_lot(i, night, doc=doc) for i in range(1, 31)])
    for night in range(4, 20):
        n.run(night, [_lot(i, night, doc=doc) for i in range(1, 13)])
    assert n.retired() == set(), "a collapse with no proof retired stock"
    for night in (20, 21):
        n.run(night, [_lot(i, night, doc=doc) for i in range(1, 13)], documents=documents,
              entries=entries(night))
        if night == 20:
            assert n.retired() == set(), "one proven read was enough"


def _tab(doc, endpoint="export", rows=13):
    return {harvest_manifest.stocklist_key(doc): {"csv_rows": rows, "endpoint": endpoint}}


def test_a_tab_whose_rows_are_all_accounted_for_retires_its_backlog(tmp_path, monkeypatch):
    """Verv, 24 Oct: the tab's export has 90 rows and the read gives 89 listings, against
    306 rows held. On 24 Oct's rule that list was a failed read for ever (217 missing).
    The tab's own CSV row count proves the read whole, so what it no longer carries
    retires -- after two such reads, never on the first."""
    n = Nights(tmp_path, monkeypatch)
    doc = "https://docs.example/verv-tab"
    _tab_nights(n, doc, _tab(doc))                   # header + 12 listings
    assert n.retired() == {str(i) for i in range(13, 31)}


def test_a_gviz_tab_never_proves_a_read_whole(tmp_path, monkeypatch):
    """A pure safety test (its twin is the export test above). gviz answers 200 with a
    workbook's FIRST tab for a gid that no longer exists, so its row count is no proof."""
    n = Nights(tmp_path, monkeypatch)
    doc = "https://docs.example/verv-tab"
    _tab_nights(n, doc, _tab(doc, endpoint="gviz"))
    assert n.retired() == set()


@pytest.mark.parametrize("rejected", [1, 0])
def test_a_lot_refused_by_a_constraint_voids_the_proof(tmp_path, monkeypatch, rejected):
    """A safety test with its twin. A link whose lots a constraint refused tonight (the
    dedup_key trap: 41 lots a night, Fawkner Park West retired while its list carried
    them) has not been stored whole, whatever its export says."""
    n = Nights(tmp_path, monkeypatch)
    doc = "https://docs.example/verv-tab"
    _tab_nights(n, doc, _tab(doc),
                entries=lambda night: [_link(doc, 12, rejected=rejected)])
    assert n.retired() == (set() if rejected else {str(i) for i in range(13, 31)})


@pytest.mark.parametrize("narrowed", [True, False])
def test_a_narrowed_proxima_index_makes_every_project_partial(tmp_path, monkeypatch,
                                                              narrowed):
    """The session-filter trap: browsing Proxima signed in narrows the next harvest's
    index. On a night the index lists far fewer projects than its recorded nights, every
    Proxima read is partial -- the old rule retired lot 12 that night. Twin: the same night
    with the whole index retires it."""
    n = Nights(tmp_path, monkeypatch)
    others = [_project_entry(p, 0, whole="uncapped") for p in range(200, 209)]
    for night in range(1, 18):
        carried = range(1, 13) if night < 4 else range(1, 12)
        entries = [_project_entry("587", len(carried), stated=len(carried),
                                  whole="uncapped")] + others
        if narrowed and night == 17:
            entries = entries[:4]
        n.run(night, [_proxima(i, night, status="Available") for i in carried],
              entries=entries)
    assert n.retired() == (set() if narrowed else {"12"})


@pytest.mark.parametrize("board,read,retires", [(101, 100, False), (100, 99, True)])
def test_a_portal_read_that_ends_on_its_page_length_proves_nothing(tmp_path, monkeypatch,
                                                                   board, read, retires):
    """The portals' grid select is set to 100 a page and no grid total is read, so a read
    of exactly 100 is what a second page looks like from here. On 24 Oct's rule 1 of 101
    missing was a whole read and lot 101 retired. Twin: 99 of a 100-lot board retires."""
    n = Nights(tmp_path, monkeypatch)
    kw = {"channel": PORTAL, "builder": "Hermitage Homes"}
    for night in range(1, 18):
        carried = range(1, board + 1) if night < 4 else range(1, read + 1)
        n.run(night, [_lot(i, night, doc=f"https://portal.example/lot/{i}", **kw)
                      for i in carried],
              entries=[_portal_entry("Hermitage Homes", len(carried))])
    assert n.retired() == ({str(board)} if retires else set())


@pytest.mark.parametrize("eagent_printed,retires", [(7, True), (20, False)])
def test_two_channels_reading_one_page_prove_the_pooled_list(tmp_path, monkeypatch,
                                                             eagent_printed, retires):
    """TORSION. Its portal used to give each lot a page (pooled by builder) and now serves
    every lot from one Stocklist page, which E-Agent also links. The 29 old per-lot rows
    never looked re-read, so they never retired. Pooled with the new page they are 29 of
    36 missing -- a failed read by count alone -- but the same page read through two
    channels tonight agrees on 7, and that proves it whole. Twin: the counts disagree."""
    n = Nights(tmp_path, monkeypatch)
    host = "referrer.torsion.example"
    page = f"https://{host}/stocklist"
    kw = {"channel": PORTAL, "builder": "Torsion Homes"}
    for night in range(1, 19):
        lots = [_lot(i, night, doc=page, **kw) for i in range(30, 37)]
        if night == 1:
            lots += [_lot(i, 1, doc=f"https://{host}/lot/{i}", **kw) for i in range(1, 30)]
        lots += [_lot(i, night, doc=page, builder="Torsion Homes",
                      source_text=f"E-Agent copy of lot {i}") for i in range(30, 37)]
        n.run(night, lots, entries=[_portal_entry("Torsion Homes", 7, host=host),
                                    _link(page, eagent_printed, heading="Torsion Homes")])
    old = {str(i) for i in range(1, 30)}
    assert (n.retired() & old) == (old if retires else set())


# ------------------------------------------------------------------ 6. how a retirement is made

def test_a_lot_that_moved_to_a_new_identity_is_hidden_not_sold(tmp_path, monkeypatch):
    """Lot 12 is read with its suburb from night 4 on, which is a new identity (suburb is
    part of content_hash). The old row leaves the list -- but the lot did not: it is still
    for sale. The old row is hidden as a duplicate of the new one, its availability kept;
    the old rule retired it as Sold."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 20):
        lots = [_lot(i, night, status="Available") for i in range(1, 12)]
        lots.append(_lot(12, night, status="Available") if night < 4 else
                    _lot(12, night, status="Available", suburb="Tarneit",
                         source_text="Lot 12 Example Street Tarneit 4 bed $612,000"))
        n.run(night, lots)
    con = sqlite3.connect(str(n.path))
    con.row_factory = sqlite3.Row
    old, new = [dict(r) for r in con.execute(
        "SELECT * FROM buildings WHERE lot_number='12' ORDER BY id")]
    con.close()
    assert new["superseded_by"] is None
    assert old["superseded_by"] == new["content_hash"], old["superseded_by"]
    assert old["availability_status"] == "Available"


def test_calibration_holds_the_new_rules_while_the_old_rules_retirements_still_apply(
        tmp_path, monkeypatch):
    """The first CALIBRATION_NIGHTS nights print what the new rules would retire and write
    none of it; what the old rule retires still goes from night 1. List Y: lot 42 has been
    off it for three weeks -- the old rule retires it on night 1. List X: lot 21 likewise,
    but X also holds 10 'Not Available' rows, so the old rule calls X a failed read; the
    anchor sets them aside and retires lot 21 once calibration is over."""
    n = Nights(tmp_path, monkeypatch)
    x, y = "https://docs.example/list-x", "https://docs.example/list-y"
    for night in range(1, 9):
        lots = [_lot(i, night, doc=x, status="Available") for i in range(1, 11)]
        lots += [_lot(i, night, doc=y, status="Available") for i in range(31, 42)]
        if night == 1:
            lots += [_lot(i, -20, doc=x, status="Not Available") for i in range(11, 21)]
            lots += [_lot(21, -20, doc=x, status="Available"),
                     _lot(42, -20, doc=y, status="Available")]
        n.run(night, lots)
        if night <= 7:
            assert "42" in n.retired(), f"night {night}: the old rule's retirement waited"
            assert "21" not in n.retired(), f"night {night}: calibration wrote a retirement"
            if night >= 2:
                assert "would retire" in n.out
    assert "21" in n.retired()


def test_a_retirement_a_person_undoes_stays_undone_until_the_lot_is_seen(tmp_path,
                                                                         monkeypatch):
    import list_ledger
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 18):
        carried = range(1, 13) if night < 4 else range(1, 12)
        n.run(night, [_lot(i, night, status="Available") for i in carried])
    con = sqlite3.connect(str(n.path))
    (stamp,) = con.execute("SELECT stamp FROM retirements").fetchone()
    assert list_ledger.undo_retirements(con, stamp, now=_night_dt(17)) == 1
    con.commit()
    con.close()
    assert n.rows()["12"]["availability_status"] == "Available"
    for night in range(18, 22):
        n.run(night, [_lot(i, night, status="Available") for i in range(1, 12)])
    assert n.retired() == set(), "an undone retirement was made again the next night"


def test_the_nightly_cap_defers_rather_than_drops(monkeypatch):
    import repair_rows as RR
    rows = [{"id": i, "source_channel": "E-Agent", "superseded_by": "",
             "availability_status": "Available"} for i in range(1000)]
    items = [{"row": r, "silent": 20 + r["id"] % 7, "main": False} for r in rows[:120]]
    items += [{"row": {"id": 5000 + i, "source_channel": "E-Agent", "superseded_by": "",
                       "availability_status": "Sold"}, "silent": 30, "main": False}
              for i in range(40)]
    items += [{"row": rows[500 + i], "silent": 15, "main": True} for i in range(30)]
    keep, later = RR.cap_retirements(items, rows)
    shown = [it for it in keep if it["row"]["availability_status"] != "Sold"
             and not it["main"]]
    assert len(shown) == 50                          # 5% of 1,000 live rows
    assert sum(1 for it in keep if it["row"]["availability_status"] == "Sold") == 40
    assert sum(1 for it in keep if it["main"]) == 30, "the old rule's retirements waited"
    assert len(later) == 70
    assert min(it["silent"] for it in shown) >= max(it["silent"] for it in later), \
        "the longest-silent lots must go first"


def test_a_report_only_run_writes_nothing_ledger_included(tmp_path, monkeypatch):
    """A pure safety test. repair_rows without --apply writes nothing: not on a database
    the ledger has never seen (no table is created), and not on one it has."""
    import repair_rows as RR
    n = Nights(tmp_path, monkeypatch)
    n.run(1, [_lot(i, 1) for i in range(1, 13)], apply=False)
    con = sqlite3.connect(str(n.path))
    names = {t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert not names & {"list_reads", "retirements", "source_offers", "channel_nights"}
    for night in range(2, 18):
        n.run(night, [_lot(i, night) for i in range(1, 12)])

    def dump():
        c = sqlite3.connect(str(n.path))
        out = {t: c.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall()
               for t in ("buildings", "list_reads", "retirements", "channel_nights")}
        c.close()
        return out
    before = dump()
    _Clock.current = _night_dt(18)
    with contextlib.redirect_stdout(io.StringIO()):
        assert RR.main(False) == 0
    assert dump() == before


# ------------------------------------------------------------------ 7. rule B: the source stopped offering it

def _page(k, label="Price List"):
    """Link k on one E-Agent category page: its own builder, two lots of its own."""
    return (f"https://files.example/link-{k}", f"Builder {k}", [100 * k + 1, 100 * k + 2],
            label)


def _eagent_night(n, night, links, extra_lots=(), extra_entries=(), **kw):
    """One night of an E-Agent page: every link's lots read, every link in the manifest.
    `links` is [(doc, builder, lot numbers, label)] -- a link with no lot numbers is still
    on the page (a dead link) and reads nothing."""
    lots, entries = list(extra_lots), list(extra_entries)
    for doc, builder, nums, label in links:
        lots += [_lot(i, night, doc=doc, builder=builder) for i in nums]
        entries.append(_link(doc, len(nums), heading=builder, label=label,
                             outcome="read" if nums else "fetch failed"))
    n.run(night, lots, entries=entries, **kw)


def _lots_of(link):
    return {str(i) for i in link[2]}


def test_a_link_taken_down_retires_after_fourteen_healthy_nights(tmp_path, monkeypatch):
    """E-Agent takes a link down (201 + 35 documents never read again, about 4,900 held
    rows). Its list is never re-read, so the absence rule cannot see it: on 24 Oct's rule
    its lots stay live for good. The link gone from a healthy page is the source's own
    statement; 14 such nights retire what it carried."""
    n = Nights(tmp_path, monkeypatch)
    links = [_page(k) for k in range(12)]
    for night in range(1, 20):
        _eagent_night(n, night, links if night <= 5 else links[1:])
        if night == 18:
            assert not _lots_of(links[0]) & n.retired(), "retired before 14 counted nights"
    assert _lots_of(links[0]) <= n.retired()
    assert not (set().union(*map(_lots_of, links[1:])) & n.retired())


def test_a_dead_link_still_on_the_page_never_retires(tmp_path, monkeypatch):
    """A pure safety test (its twin is the one above). A link whose fetch fails is still on
    E-Agent's page: the source still offers it, we simply cannot read it."""
    n = Nights(tmp_path, monkeypatch)
    links = [_page(k) for k in range(12)]
    dead = (links[0][0], links[0][1], [], links[0][3])
    for night in range(1, 26):
        _eagent_night(n, night, links if night <= 5 else [dead] + links[1:])
    assert not _lots_of(links[0]) & n.retired()


def test_outage_nights_do_not_count(tmp_path, monkeypatch):
    """Five nights the page offers two links of twelve -- an outage, not twelve links
    taken down -- count for nothing, so the retirement comes five nights later."""
    n = Nights(tmp_path, monkeypatch)
    links = [_page(k) for k in range(12)]
    for night in range(1, 25):
        tonight = links if night <= 5 else links[1:3] if 8 <= night <= 12 else links[1:]
        _eagent_night(n, night, tonight)
        if night == 23:
            assert not _lots_of(links[0]) & n.retired(), "an outage night was counted"
    assert _lots_of(links[0]) <= n.retired()


@pytest.mark.parametrize("half_read", [True, False])
def test_a_builder_with_a_half_read_list_blocks_it(tmp_path, monkeypatch, half_read):
    """A safety test with its twin. Builder 0's link leaves the page, but its other list
    comes back with 1 row of 30 tonight: whatever is happening to that builder's stock, it
    is not a night to count its absences on. Twin: its other list reads whole."""
    n = Nights(tmp_path, monkeypatch)
    links = [_page(k) for k in range(12)]
    other = ("https://files.example/builder-0-second", "Builder 0",
             list(range(5001, 5031)), "Second List")
    for night in range(1, 21):
        second = other if (night <= 5 or not half_read) else \
            (other[0], other[1], other[2][:1], other[3])
        _eagent_night(n, night, [second] + (links if night <= 5 else links[1:]))
    assert (_lots_of(links[0]) & n.retired()) == (set() if half_read else _lots_of(links[0]))


def test_a_mass_vanish_night_does_not_count(tmp_path, monkeypatch):
    """Seven of twelve links replaced overnight by seven links nobody has seen, under other
    headings: the count is the same but the page is not the page it was. That night counts
    for nothing, so link 0's lots retire a night later than they would."""
    n = Nights(tmp_path, monkeypatch)
    links = [_page(k) for k in range(12)]
    newcomers = [_page(k, label="New Release") for k in range(20, 27)]
    for night in range(1, 21):
        tonight = links if night <= 5 else (links[7:] + newcomers)
        _eagent_night(n, night, tonight)
        if night == 19:
            assert not _lots_of(links[0]) & n.retired(), "the mass-vanish night counted"
    assert _lots_of(links[0]) <= n.retired()


def test_a_reuploaded_list_neither_resurrects_nor_strands_its_rows(tmp_path, monkeypatch):
    """E-Agent re-uploads link 0's file under a new link in the same place (same page,
    heading and label). Two of its three lots are on the new upload and keep being read
    there -- they are never retired; the third is not, and retires 14 counted nights after
    the old link left the page. Nothing retired comes back unless its source lists it."""
    n = Nights(tmp_path, monkeypatch)
    links = [_page(k) for k in range(12)]
    old = (links[0][0], links[0][1], [1, 2, 3], links[0][3])
    new = ("https://files.example/link-0-reuploaded", links[0][1], [1, 2], links[0][3])
    for night in range(1, 21):
        _eagent_night(n, night, ([old] + links[1:]) if night <= 5 else ([new] + links[1:]))
        assert not {"1", "2"} & n.retired(), f"night {night}: a lot on the new upload retired"
    assert "3" in n.retired(), "the lot the re-upload dropped is stranded under the old link"
    for night in range(21, 24):
        _eagent_night(n, night, [new] + links[1:])
    assert "3" in n.retired() and not {"1", "2"} & n.retired()


def test_a_proxima_project_gone_from_the_dropdown_retires(tmp_path, monkeypatch):
    """Projects 821, 192, 637, 172, 119 and 120 left the account's dropdown after 12 Oct
    and were never read again, so their ~100 lots never retired. Project 300 here was
    already gone when the ledger began (never offered since): it counts on healthy nights
    once its developer has another project read whole. Project 301, with no developer
    named, is listed for a person instead -- nothing can prove it."""
    n = Nights(tmp_path, monkeypatch)
    projects = {str(p): f"Developer {p}" for p in range(200, 212)}
    projects["200"] = "Damac Properties"
    for night in range(1, 19):
        lots, entries = [], []
        for p, dev in projects.items():
            lots += [_lot(int(p) * 10 + i, night, channel="Proxima", builder=dev,
                          doc="https://proxima.example/projects", source_project_id=p)
                     for i in (1, 2)]
            entries.append(_project_entry(p, 2, stated=2, whole="uncapped"))
        if night == 1:
            lots += [_lot(3000 + i, -10, channel="Proxima", builder="Damac Properties",
                          doc="https://proxima.example/projects", source_project_id="300")
                     for i in (1, 2)]
            lots += [_lot(3010 + i, -10, channel="Proxima", builder="",
                          doc="https://proxima.example/projects", source_project_id="301")
                     for i in (1, 2)]
        n.run(night, lots, entries=entries, runs=_px_run(len(entries)))
        if night == 16:
            assert not {"3001", "3002"} & n.retired(), "retired before 14 counted nights"
    assert {"3001", "3002"} <= n.retired()
    assert not {"3011", "3012"} & n.retired(), "a project nothing can prove retired"
    assert "listed for a person" in n.out


@pytest.mark.parametrize("budget_bound", [False, True])
def test_a_file_a_clean_folder_no_longer_serves_retires(tmp_path, monkeypatch, budget_bound):
    """A Dropbox folder link read cleanly every night, whose second file is no longer in
    it: its lots retire after 14 such nights. Twin, a safety test: the same folder whose
    file budget bound every night never counts -- the file may simply not have been
    downloaded."""
    n = Nights(tmp_path, monkeypatch)
    links = [_page(k) for k in range(1, 12)]
    folder = "https://files.example/folder"
    for night in range(1, 21):
        files = [("https://files.example/folder/f1", [1, 2]),
                 ("https://files.example/folder/f2", [3, 4])][:2 if night <= 5 else 1]
        lots = [_lot(i, night, doc=url, stocklist_file=folder, builder="Builder F")
                for url, nums in files for i in nums]
        entry = _link(folder, len(lots), heading="Builder F", pieces=[
            {"name": url.rsplit("/", 1)[-1], "outcome": "read", "printed": len(nums),
             "why": ""} for url, nums in files])
        notes = {harvest_manifest.stocklist_key(folder): {"truncated": True,
                                                          "why": "file budget"}} \
            if budget_bound else None
        _eagent_night(n, night, links, extra_lots=lots, extra_entries=[entry],
                      link_notes=notes)
        assert not {"1", "2"} & n.retired()
    assert ({"3", "4"} & n.retired()) == (set() if budget_bound else {"3", "4"})


def test_rule_b_is_off_for_e_agent_without_the_file_key(tmp_path, monkeypatch):
    """A pure safety test. Without sources.e_agent.stocklist_file_key two spellings of one
    link cannot be told apart, so a link might look gone when it is merely spelt anew:
    E-Agent's rule B is off that night, and the taken-down link of the test above stays."""
    import list_ledger
    monkeypatch.setattr(list_ledger, "file_key_fn", lambda: None)
    n = Nights(tmp_path, monkeypatch)
    links = [_page(k) for k in range(12)]
    for night in range(1, 21):
        _eagent_night(n, night, links if night <= 5 else links[1:])
    assert not _lots_of(links[0]) & n.retired()
    assert "stocklist_file_key cannot be imported" in n.out


# ------------------------------------------------------------------ 8. what the log says

def test_the_log_names_every_half_read_list_never_its_link(tmp_path, monkeypatch):
    """Section 12 printed the first 56 characters of each partial list's url -- for a
    Dropbox or SharePoint share, the capability itself, in a public repo's nightly log.
    It now names the list (channel, builder, host, project), its key, what decided it and
    how many nights it has been stalled."""
    n = Nights(tmp_path, monkeypatch)
    doc = "https://www.dropbox.com/scl/fo/abc123/sheet?rlkey=secretkey"
    for night in range(1, 9):
        carried = range(1, 30) if night <= 2 else [1]
        n.run(night, [_lot(i, night, doc=doc) for i in carried])
    section = n.out[n.out.index("\n12. "):n.out.index("\n11. ")]
    assert "rlkey" not in section and "/scl/fo/" not in section, section
    assert "E-Agent | Example Homes | www.dropbox.com" in section
    assert "dropped 28 of 29 since its last whole read" in section and \
        "stalled   6 night(s)" in section, section


def test_the_first_night_lists_inherited_retirements_on_a_cut_read(tmp_path, monkeypatch):
    """The old rule could not see a cut read: it retired lots 81-85 behind Proxima's 80-lot
    cap. Kept now, those are listed for a person on the ledger's first night with a
    manifest -- the night that can tell which reads were cut."""
    n = Nights(tmp_path, monkeypatch)
    for i in range(1, 15):
        n.db.record_building(_proxima(i, 1, status="Available"))
    con = sqlite3.connect(str(n.path))
    con.execute("UPDATE buildings SET superseded_by=?, availability_status='Sold' WHERE "
                "CAST(lot_number AS INTEGER) > 10", (WITHDRAWN_FROM_SOURCE,))
    con.commit()
    con.close()
    n.run(2, [_proxima(i, 2, status="Available") for i in range(1, 11)],
          entries=[_project_entry("587", 10, stated=14, short=True)])
    assert "9c. first night of the ledger: 4 retirement(s)" in n.out, n.out[-2500:]
    assert n.retired() == {"11", "12", "13", "14"}, "the report changed what is retired"
    n.run(3, [_proxima(i, 3, status="Available") for i in range(1, 11)],
          entries=[_project_entry("587", 10, stated=14, short=True)])
    assert "9c." not in n.out, "listed again after the first night"


def test_explain_says_where_a_row_stands(tmp_path, monkeypatch):
    import list_ledger
    import repair_rows as RR
    n = Nights(tmp_path, monkeypatch)
    links = [_page(k) for k in range(12)]
    for night in range(1, 11):
        _eagent_night(n, night, links if night <= 5 else links[1:])
    con = sqlite3.connect(str(n.path))
    con.row_factory = sqlite3.Row
    row = dict(con.execute("SELECT * FROM buildings WHERE lot_number='1'").fetchone())
    text = "\n".join(list_ledger.explain(con, row, RR._doc_key(row, True)))
    con.close()
    assert "5 night(s) counted since" in text, text
    assert "absent from its list for" in text and "whole reads since" in text, text
    assert "https://" not in text and "link-0" not in text, text
