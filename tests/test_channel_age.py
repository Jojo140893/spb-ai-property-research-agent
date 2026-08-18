"""How long ago each source last delivered — in hours, not whole days.

THE DEFECT THIS EXISTS FOR. On the morning of 18 Aug 2026 the Settings tab reported all
four sources as "1 day ago" on a morning every one of them had delivered a couple of
hours earlier. Two separate causes: the runner stamped UTC dates while the cron is set to
land at 09:00 Melbourne, and the only timestamp in the schema was date_checked — a DATE,
which puts a harvest anywhere inside a 24-hour window. The first is fixed in the workflow;
this covers the second.

checked_at is NULL on every row written before that date, so the fallback to whole days is
tested as carefully as the hours path: a fabricated hour figure would be worse than an
honest day figure.
"""
import json
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
INDEX = APP / "index.html"
CHECKS = Path(__file__).resolve().parent / "ago_checks.mjs"


def _db(rows):
    """An in-memory buildings table holding just what the export reads."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE buildings (
        source_channel TEXT, builder_name TEXT, superseded_by TEXT,
        date_checked TEXT, checked_at TEXT)""")
    conn.executemany("INSERT INTO buildings VALUES (?,?,?,?,?)", rows)
    return conn


def _export(tmp_path, rows):
    import build_web
    build_web._export_channel_health(tmp_path, _db(rows))
    return json.loads((tmp_path / "channels.json").read_text(encoding="utf-8"))


def _channel(payload, name):
    return next(c for c in payload["channels"] if c["channel"] == name)


def _ago(hours):
    return (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")


TODAY = datetime.now().strftime("%d/%m/%Y")


# ------------------------------------------------------------------ the export

def test_hours_are_reported_when_the_row_carries_a_timestamp(tmp_path):
    c = _channel(_export(tmp_path, [("E-Agent", "", None, TODAY, _ago(3))] * 4), "E-Agent")
    assert c["hours_ago"] == 3
    assert c["days_ago"] == 0


def test_the_newest_timestamp_wins_not_the_last_row(tmp_path):
    rows = [("E-Agent", "", None, TODAY, _ago(h)) for h in (30, 2, 19)]
    assert _channel(_export(tmp_path, rows), "E-Agent")["hours_ago"] == 2


def test_a_row_with_no_timestamp_reports_none_rather_than_zero(tmp_path):
    """The pre-18-Aug rows. None makes the tab fall back to days; 0 would claim the
    source delivered this minute."""
    c = _channel(_export(tmp_path, [("E-Agent", "", None, TODAY, None)] * 3), "E-Agent")
    assert c["hours_ago"] is None
    assert c["days_ago"] == 0


def test_superseded_rows_do_not_supply_the_timestamp(tmp_path):
    """A superseded row is not stock this source is currently delivering."""
    rows = [("E-Agent", "", None, TODAY, _ago(9)),
            ("E-Agent", "", "somehash", TODAY, _ago(0))]
    assert _channel(_export(tmp_path, rows), "E-Agent")["hours_ago"] == 9


def test_a_clock_ahead_of_the_build_machine_reports_zero_not_a_negative_age(tmp_path):
    stamp = (datetime.now() + timedelta(hours=2)).isoformat(timespec="seconds")
    c = _channel(_export(tmp_path, [("E-Agent", "", None, TODAY, stamp)]), "E-Agent")
    assert c["hours_ago"] == 0


def test_an_unparseable_timestamp_is_not_guessed_at(tmp_path):
    c = _channel(_export(tmp_path, [("E-Agent", "", None, TODAY, "last tuesday")]), "E-Agent")
    assert c["hours_ago"] is None


def test_the_named_portals_get_their_own_hours(tmp_path):
    """Bathla, Hermitage and Torsion share one source_channel and are listed separately."""
    rows = [("Direct Builder Portal (live)", "Hermitage Homes", None, TODAY, _ago(4)),
            ("Direct Builder Portal (live)", "Torsion Homes", None, TODAY, _ago(26))]
    by_name = {p["channel"]: p for p in _export(tmp_path, rows)["portals"]}
    assert by_name["Hermitage Homes"]["hours_ago"] == 4
    assert by_name["Torsion Homes"]["hours_ago"] == 26


def test_days_ago_still_drives_the_state(tmp_path):
    """hours_ago is wording. The stale/dead thresholds stay on whole days."""
    old = (datetime.now() - timedelta(days=9)).strftime("%d/%m/%Y")
    c = _channel(_export(tmp_path, [("E-Agent", "", None, old, _ago(9 * 24))]), "E-Agent")
    assert c["state"] == "dead"
    assert c["days_ago"] == 9


# ------------------------------------------------------------------ the wording

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_wording_the_page_actually_ships(tmp_path):
    """Sliced out of index.html rather than reimplemented, so it cannot drift."""
    html = INDEX.read_text(encoding="utf-8")
    src = ""
    for name in ("agoText", "agoShort"):
        m = re.search(r"^    function " + name + r"\(c\) \{.*?^    \}", html, re.S | re.M)
        assert m, f"{name} was not found in index.html — has it been renamed?"
        src += m.group(0) + chr(10)
    bundle = tmp_path / "ago.mjs"
    bundle.write_text(src + CHECKS.read_text(encoding="utf-8"), encoding="utf-8")
    r = subprocess.run(["node", str(bundle)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout
