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

from database import ResearchDatabase, WITHDRAWN_FROM_SOURCE

DAY0 = datetime(2026, 9, 1, 3, 0)


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

    def run(self, night, lots, entries=(), documents=None, link_notes=None, apply=True):
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
        self.manifest.write_text(json.dumps({
            "schema": 1, "generated": _night_dt(night).strftime("%d/%m/%Y %H:%M"),
            "entries": list(entries), "documents": documents or {},
            "link_notes": link_notes or {}}), encoding="utf-8")
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
