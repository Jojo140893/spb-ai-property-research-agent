"""One read is one day: a run that crosses Melbourne's midnight (23 Oct 2026).

Run 39950641008 was dispatched by hand at 23:04 Melbourne, so its Proxima read ran from
23:40 to 00:00:44 AEDT. Every lot was stamped date_checked from the clock as its row was
built, so the last six projects -- 288 lots -- carried 24/10 and the other ~8,075 carried
23/10. Three readers believed the split:

  - the Settings tab counted "Refreshed" on the channel's newest DAY: 288 of 10,032 is 3%,
    "ONLY PART OF ITS STOCK", for a read that was whole (8,363 lots, 109 of 109 projects);
  - the search ages rows on a fixed UTC+10 clock, so until 14:00 UTC a 24/10 row was a day
    in the future, age -1, and went to Needs confirming;
  - parity (13:46 UTC) and the replay (13:44 UTC), on the same clock, left those lots out
    of their obvious sets -- NSW fell by 250 -- and no bar moved.

Each test whose docstring says what happened "before the fix" fails against main e12236b
and passes after it; one that says "before review" failed against this fix's first cut
(4762906, a fixed 12-hour window for the Settings tab) and passes on main and after it; the
others pin what the fix must not weaken.
"""
import ast
import sqlite3
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

APP = Path(__file__).resolve().parents[1]
for _p in (str(APP / "api"), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _candidates as C  # noqa: E402
import search_regressions as SR  # noqa: E402
from sources import proxima as px  # noqa: E402
from tests import test_proxima_capped_replay as CR  # noqa: E402

# 13:46 UTC on 23 Oct: 23:46 on the search's AEST clock, 00:46 in Melbourne (AEDT).
UTC_1346 = datetime(2026, 10, 23, 13, 46, tzinfo=timezone.utc)
BEFORE_MIDNIGHT = datetime(2026, 10, 23, 23, 59, 50)
AFTER_MIDNIGHT = datetime(2026, 10, 24, 0, 0, 5)


# ------------------------------------------------------------------- a clock to move

class _Clock:
    def __init__(self, start):
        self.now = start


def _clock(monkeypatch, start):
    """One clock behind every module that stamps a date or a moment while a harvest
    stores rows. Set with raising=False so the same test runs against code where a module
    does not import datetime (sources.base before the fix, sources.proxima after it)."""
    import database
    from sources import base
    clock = _Clock(start)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now if tz is None else clock.now.replace(tzinfo=tz)

    for module in (base, px, database):
        monkeypatch.setattr(module, "datetime", Frozen, raising=False)
    return clock


class _MidnightPage(CR._Page):
    """The test_proxima_capped_replay portal, whose clock passes midnight as one project
    is opened: every project before it is read on the 23rd, it and every one after it on
    the 24th -- the shape of 23 Oct, where projects 64-69 of 69 were read after 13:00 UTC."""

    def __init__(self, clock, *a, after_midnight="102", **k):
        super().__init__(*a, **k)
        self.clock, self.after = clock, after_midnight

    def evaluate(self, js, *args):
        if js is px._COLLECT_JS and args and args[0] == self.after:
            self.clock.now = AFTER_MIDNIGHT
        return super().evaluate(js, *args)


def _portal(clock):
    server = CR._Server({"101": CR._inventory("101", 6), "102": CR._inventory("102", 4)})
    return _MidnightPage(clock, server, index=["101", "102"], dropdown=[])


def _stored(path, *columns):
    con = sqlite3.connect(str(path))
    try:
        return con.execute("SELECT %s FROM buildings" % ", ".join(columns)).fetchall()
    finally:
        con.close()


# ------------------------------------------------------- the harvest: one day per read

def test_a_proxima_read_across_midnight_stores_one_date(monkeypatch, tmp_path):
    """Project 101 is opened at 23:59:50 and project 102 at 00:00:05. Before the fix 101's
    lots were stored under 23/10 and 102's under 24/10."""
    import config
    from database import ResearchDatabase

    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "none-yet.db")
    clock = _clock(monkeypatch, BEFORE_MIDNIGHT)
    page = _portal(clock)
    monkeypatch.setattr(px, "PlaywrightScraper", lambda **k: CR._Scraper(page))
    monkeypatch.setattr(px, "PLAYWRIGHT_AVAILABLE", True)
    src = px.ProximaSource()
    src.stored_counters = {}
    rows = src.search({})

    assert sorted({r["source_project_id"] for r in rows}) == ["101", "102"], \
        "premise: both projects were read"
    assert clock.now == AFTER_MIDNIGHT, "premise: the read crossed midnight"
    assert {r["date_checked"] for r in rows} == {"23/10/2026"}, \
        sorted({(r["source_project_id"], r["date_checked"]) for r in rows})

    db = ResearchDatabase(db_path=tmp_path / "px.db")
    for r in rows:
        db.record_building(r)
    assert set(_stored(tmp_path / "px.db", "date_checked", "last_seen")) == \
        {("23/10/2026", "23/10/2026")}


def test_a_harvest_across_midnight_stores_every_channel_under_the_day_it_started(
        monkeypatch, tmp_path):
    """23 Oct in miniature: E-Agent read at 23:58, Proxima across midnight, then the inbox
    swept after it. The E-Agent and inbox fakes give no date_checked, so
    database.record_building stamps them -- the path any source that omits the date takes.
    Before the fix the run stored E-Agent on 23/10, Proxima on both days and the whole
    inbox on 24/10; after it, one day, while checked_at still says when each row was read.

    Proxima pinning its own read is not enough for this: the inbox starts after midnight,
    so only a date fixed when the HARVEST starts keeps it on the run's day."""
    import config
    import harvest_buildings as HB
    import harvest_manifest as HM
    from database import ResearchDatabase
    from sources import base

    path = tmp_path / "h.db"
    monkeypatch.setattr(config, "DATABASE_PATH", path)
    ResearchDatabase(db_path=path)
    clock = _clock(monkeypatch, datetime(2026, 10, 23, 23, 58, 0))

    class FakeEA:
        def __init__(self, *a, **k):
            self.username, self.password = "u", "p"

        def search(self, filters):
            return [{"lot_address": "Lot %d Aura Estate" % i, "suburb": "Coomera",
                     "state": "QLD", "builder_name": "Avia Homes",
                     "advertised_package_price": 700000 + i,
                     "source_url_or_ref": "https://e-agent/x%d" % i} for i in range(3)]

    class FakeInbox:
        channel_name = "digital email"

        def __init__(self, *a, **k):
            self.username, self.brochures = "digital@", []

        def search(self, filters):
            return [{"lot_address": "Lot %d Dawn" % i, "suburb": "Walloon", "state": "QLD",
                     "builder_name": "Shape Homes", "advertised_package_price": 720000 + i,
                     "source_url_or_ref": "email:Dawn price list"} for i in range(2)]

    page = _portal(clock)
    monkeypatch.setattr(px, "PlaywrightScraper", lambda **k: CR._Scraper(page))
    monkeypatch.setattr(px, "PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(HB, "EAgentSource", FakeEA)
    monkeypatch.setattr(HB, "EmailStocklistSource", FakeInbox)
    monkeypatch.setattr(HB, "_require_readers", lambda: True)
    monkeypatch.setattr(HB, "BuilderRegistry", lambda: type(
        "R", (), {"get_all_builders": lambda s: []})())
    monkeypatch.setattr(HM, "write", lambda path=None, _w=HM.write: _w(tmp_path / "m.json"))
    monkeypatch.setattr(HB, "_record_unreadable_stocklists", lambda: None)
    HB.harvest(eagent=True, portals=False, email=True, proxima=True, rea=False,
               eagent_stocklist=False)

    rows = _stored(path, "source_channel", "date_checked", "last_seen", "checked_at")
    assert {r[0] for r in rows} == {"E-Agent", "Proxima", "digital email"}, \
        "premise: every channel stored"
    assert len(rows) == 15, "premise: 3 E-Agent lots, 6 + 4 Proxima lots, 2 emailed"
    assert {(r[1], r[2]) for r in rows} == {("23/10/2026", "23/10/2026")}, \
        sorted({(r[0], r[1]) for r in rows})
    # The moment is still the moment: the day is the run's, checked_at is the row's own.
    by_channel = {}
    for channel, _day, _seen, moment in rows:
        by_channel.setdefault(channel, set()).add(moment[:13])
    assert by_channel == {"E-Agent": {"2026-10-23T23"}, "Proxima": {"2026-10-24T00"},
                          "digital email": {"2026-10-24T00"}}
    # ...and the run's date does not outlive the run.
    assert base.read_date() == "24/10/2026"


def test_no_harvested_source_stamps_the_day_from_its_own_clock():
    """The two tests above drive Proxima and the database's own fallback. The other
    channels build their rows deep inside a signed-in session, so this reads them: a
    date_checked set from datetime.now() in any of them re-opens the split for that
    channel. Before the fix nine places did, in all five channels. A live re-verification
    of one package (EAgentSource.verify) is not a harvested row."""
    found = []
    for name in ("e_agent", "eagent_stocklist", "builder_portals", "email_inbox",
                 "proxima"):
        path = APP / "sources" / (name + ".py")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = set()
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == "verify":
                skip.update(id(n) for n in ast.walk(fn))
        for node in ast.walk(tree):
            if id(node) in skip:
                continue
            values = []
            if isinstance(node, ast.Dict):
                values = [v for k, v in zip(node.keys, node.values)
                          if isinstance(k, ast.Constant) and k.value == "date_checked"]
            elif isinstance(node, ast.Assign):
                values = [node.value for t in node.targets
                          if isinstance(t, ast.Subscript)
                          and isinstance(t.slice, ast.Constant)
                          and t.slice.value == "date_checked"]
            for v in values:
                if "datetime.now" in ast.unparse(v):
                    found.append("sources/%s.py:%d" % (name, v.lineno))
    assert found == [], "stamped from the clock, per row: " + ", ".join(found)


# ------------------------------------------------------ the Settings tab: one run

def _channels_db(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE buildings (
        source_channel TEXT, builder_name TEXT, superseded_by TEXT,
        date_checked TEXT, checked_at TEXT)""")
    conn.executemany("INSERT INTO buildings VALUES (?,?,?,?,?)", rows)
    return conn


def _health(tmp_path, monkeypatch, rows):
    import build_web
    monkeypatch.setattr(build_web, "_login_status", lambda: {})
    payload = build_web._export_channel_health(tmp_path, _channels_db(rows))
    return ({c["channel"]: c for c in payload["channels"]},
            {p["channel"]: p for p in payload["portals"]})


def _read(channel, builder, start, n, step_s=10):
    """n live rows read one after another from `start`, each dated the day it was read --
    exactly how the rows of 23 Oct were stored."""
    out = []
    for i in range(n):
        moment = start + timedelta(seconds=i * step_s)
        out.append((channel, builder, None, moment.strftime("%d/%m/%Y"),
                    moment.isoformat(timespec="seconds")))
    return out


def test_a_channel_read_across_midnight_reads_as_fully_refreshed(tmp_path, monkeypatch):
    """80 lots read before midnight, 20 after, nothing else held: the whole of what the
    channel holds came back in one read. Before the fix: 20 of 100, "partial"."""
    midnight = datetime.combine(date.today(), time())
    rows = (_read("Proxima", "", midnight - timedelta(minutes=20), 80)
            + _read("Proxima", "", midnight + timedelta(seconds=3), 20)
            + _read("Direct Builder Portal (live)", "Hermitage Homes",
                    midnight - timedelta(minutes=2), 30))
    channels, portals = _health(tmp_path, monkeypatch, rows)
    proxima = channels["Proxima"]
    assert (proxima["live"], proxima["refreshed"], proxima["refreshed_pct"]) == (100, 100, 100)
    assert proxima["state"] == "ok"
    assert proxima["last_stock"] == midnight.strftime("%d/%m/%Y")
    hermitage = portals["Hermitage Homes"]
    assert (hermitage["refreshed"], hermitage["state"]) == (30, "ok"), hermitage


def test_last_nights_rows_are_not_tonights(tmp_path, monkeypatch):
    """The rule the tab exists for still fires: a run that hands back 40 of 100, the other
    60 last seen by the previous night's run, is partial -- the count is one run wide,
    not one day plus a run."""
    midnight = datetime.combine(date.today(), time())
    rows = (_read("Proxima", "", midnight + timedelta(hours=9), 40)
            + _read("Proxima", "", midnight - timedelta(hours=15), 60))
    proxima = _health(tmp_path, monkeypatch, rows)[0]["Proxima"]
    assert (proxima["refreshed"], proxima["state"]) == (40, "partial")


def _summary(c):
    return (c["live"], c["refreshed"], c["refreshed_pct"], c["state"])


def test_a_whole_late_read_then_a_thin_morning_read_is_partial(tmp_path, monkeypatch):
    """A manual run dispatched late the evening before reads all 1,000 lots from 23:40 to
    00:00:48, dated the day it started; the scheduled run nine hours later hands back 16 of
    them. The morning read is the one to judge, and 16 of 1,000 is the 17 Aug collapse
    (16 lots against a stored 1,396). Before review, the first cut of the run rule (every
    stamp within 12 hours of the newest) counted both reads and said 100% "ok". Main's
    day rule says "partial" here, and the run rule must not lose what it caught."""
    midnight = datetime.combine(date.today(), time())
    evening = (midnight - timedelta(days=1)).strftime("%d/%m/%Y")
    start = midnight - timedelta(minutes=20)
    night = [("Proxima", "", None, evening,
              (start + timedelta(seconds=1.25 * i)).isoformat(timespec="seconds"))
             for i in range(1000)]
    morning = _read("Proxima", "", midnight + timedelta(hours=9, minutes=40), 16, step_s=5)
    proxima = _health(tmp_path, monkeypatch, morning + night[16:])[0]["Proxima"]
    assert _summary(proxima) == (1000, 16, 2, "partial")


def test_a_thin_rerun_the_same_afternoon_is_partial(tmp_path, monkeypatch):
    """Two runs on one date: a manual run stores all 1,000 lots from 14:03, and a re-run
    two and a half hours later hands back 16. Both carry today's date, so before the fix
    the day rule counted 1,000 and said "ok", as does any fixed window of three hours or more --
    the length of a run is no help when two runs fit inside it. The pause between the
    two bursts is what says they are two runs. The long burst (1,000 rows a second apart,
    16 minutes in all) is one run, not cut by its own length."""
    midnight = datetime.combine(date.today(), time())
    whole = _read("Proxima", "", midnight + timedelta(hours=14, minutes=3), 1000, step_s=1)
    thin = _read("Proxima", "", midnight + timedelta(hours=16, minutes=33), 16, step_s=1)
    proxima = _health(tmp_path, monkeypatch, thin + whole[16:])[0]["Proxima"]
    assert _summary(proxima) == (1000, 16, 2, "partial")


def test_a_run_is_never_longer_than_the_job(tmp_path, monkeypatch):
    """Reads repeated by hand every 11 minutes for over five hours never pause long enough
    to part, but the harvest job is killed at 150 minutes, so nothing more than three hours
    older than the newest stamp is the same run: of 30 bursts of 10 lots, the 17 within
    three hours count (the 17th is 176 minutes back, the 18th 187). Before the fix every
    burst counted, on the day rule and on the 12-hour window alike."""
    newest = datetime.combine(date.today(), time(18, 0))
    rows = []
    for k in range(30):
        rows += _read("Proxima", "", newest - timedelta(minutes=11 * k), 10, step_s=0)
    proxima = _health(tmp_path, monkeypatch, rows)[0]["Proxima"]
    assert (proxima["live"], proxima["refreshed"]) == (300, 170)


def test_a_channel_with_no_timestamps_keeps_the_day_rule(tmp_path, monkeypatch):
    """Rows stored before checked_at existed (18 Aug 2026) are counted on the newest day,
    as they always were."""
    today = date.today().strftime("%d/%m/%Y")
    yesterday = (date.today() - timedelta(days=1)).strftime("%d/%m/%Y")
    rows = ([("E-Agent", "", None, today, None)] * 30
            + [("E-Agent", "", None, yesterday, None)] * 70)
    e_agent = _health(tmp_path, monkeypatch, rows)[0]["E-Agent"]
    assert (e_agent["refreshed"], e_agent["refreshed_pct"], e_agent["state"]) == \
        (30, 30, "partial")


# --------------------------------------------- the search, parity and the replay

def _lot(content_hash, day, **kw):
    """A Proxima lot that clears every gate of the search, parity and the replay."""
    row = {"content_hash": content_hash, "id": sum(map(ord, content_hash)),
           "builder_name": "Prosperity Residential", "estate_name": "Windermere Mambourin",
           "lot_number": content_hash[-2:], "lot_address": "Lot %s Windermere"
           % content_hash[-2:], "suburb": "Walloon", "state": "QLD",
           "availability_status": "Available", "price": 741900.0, "bedrooms": 4,
           "bathrooms": 2, "car_spaces": 2, "house_sqm": 180.0,
           "product_type": "House & Land", "source_channel": "Proxima",
           "source_project_id": "448", "source_doc": "doc-448", "superseded_by": None,
           "date_checked": day, "checked_at": None, "price_kind": "package",
           "text_price_max": 741900.0, "price_in_text": True, "email_list": None,
           "email_first_seen": None, "first_seen": day, "land_price": None,
           "build_price": None}
    row.update(kw)
    return row


def test_a_lot_stamped_after_melbournes_midnight_is_fresh_on_the_search_clock(monkeypatch):
    """Read at 00:30 AEDT on the 24th, searched at 23:46 AEST on the 23rd (13:46 UTC).
    Before the fix its age was -1, _fresh said unverified, and it went to Needs
    confirming for the hour."""
    row = _lot("v2:after00", "24/10/2026", checked_at="2026-10-24T00:30:00")
    today = C._now_aest(UTC_1346)
    assert today == datetime(2026, 10, 23, 23, 46), "premise: the search's day is the 23rd"
    verified, seen, _raw = C._fresh(row, today)
    assert verified and seen == datetime(2026, 10, 24)
    assert C._age_days(row, today) == 0

    monkeypatch.setattr(C, "_utc_now", lambda: UTC_1346)
    assert C._fresh(row)[0], "the default clock disagrees"
    packages, _counts = C.build_packages(
        {"state": "QLD", "budget_max": 750000, "preferred_spending_cap": 750000}, [row])
    (pkg,) = packages
    assert pkg["verified"] is True, pkg.get("confirm")
    assert pkg["age_days"] == 0
    assert "stale" not in (pkg.get("confirm") or [])


def _after_and_before():
    return _lot("v2:after01", "24/10/2026"), _lot("v2:before2", "23/10/2026")


def test_a_lot_stamped_after_melbournes_midnight_is_in_paritys_obvious_set():
    """The same lot, judged by parity at 23:46 AEST. Before the fix it was left out of the
    obvious set -- tonight's parity never judged the 288 lots read after midnight."""
    import parity_check as P
    after, before = _after_and_before()
    catalogue = SimpleNamespace(db={r["content_hash"]: r for r in (after, before)},
                                row=lambda h: None)
    obvious = P.obvious_lots(catalogue, C._now_aest(UTC_1346).date(),
                             lambda rec: {"price_in_text": True}, lambda rec, snap: False)
    assert sorted(lot["hash"] for lot in obvious) == ["v2:after01", "v2:before2"]


def test_a_lot_stamped_after_melbournes_midnight_is_in_the_replays_obvious_set():
    """And by the replay of Colin's searches, which ran at 13:44 UTC that night. Before
    the fix its "age < 0" dropped the lot."""
    after, before = _after_and_before()
    picked = SR.obvious_candidates(SR.Catalogue([], [after, before]), "QLD", 0, 750000,
                                   C._now_aest(UTC_1346).date(),
                                   lambda rec: {"price_in_text": True})
    assert sorted(r["content_hash"] for r in picked) == ["v2:after01", "v2:before2"]


def test_two_days_ahead_is_still_never_fresh():
    """Only the daylight-saving hour is forgiven. A date two days ahead is a wrong date,
    and nobody may call it fresh."""
    import parity_check as P
    ahead = _lot("v2:ahead03", "25/10/2026")
    now = C._now_aest(UTC_1346)
    assert C._fresh(ahead, now)[0] is False
    catalogue = SimpleNamespace(db={"v2:ahead03": ahead}, row=lambda h: None)
    assert P.obvious_lots(catalogue, now.date(), lambda rec: {"price_in_text": True},
                          lambda rec, snap: False) == []
    assert SR.obvious_candidates(SR.Catalogue([], [ahead]), "QLD", 0, 750000, now.date(),
                                 lambda rec: {"price_in_text": True}) == []


@pytest.mark.parametrize("offset", [2, 1, 0, -1, -2, -3, -14, -15])
def test_the_search_and_the_checks_age_a_row_alike(offset):
    """One clock: the search's age and the replay's (which parity counts its nights with)
    agree on every day around today, the day ahead included."""
    now = C._now_aest(UTC_1346)
    day = (now.date() + timedelta(days=offset)).strftime("%d/%m/%Y")
    assert C._age_days({"date_checked": day}, now) == SR._age(day, now.date())
