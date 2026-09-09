"""The nightly sheet check has to be visible to the person who asked for it.

Ahsan, 9 Sep 2026: "i want you to confirm all the listings are present ... i wnat you to
do full testing and verify that". A confirmation nobody can see is not a confirmation --
it is a log line in a file on a CI runner that is deleted every morning.

So the counts reach the Settings tab, stated as the check states them, INCLUDING what it
refused to judge. "Could not open the file" and "opened it and could not recognise the
row" are different admissions, and one 'unverified' number would hide both.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "api")

import build_web

APP = Path(__file__).resolve().parents[1]


def _write_report(tmp_path, rows, documents=None):
    payload = {"generated": "2026-09-09T18:30:00", "live": 10872, "checked": len(rows),
               "documents": documents or [], "rows": rows}
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "sheet_check.json").write_text(json.dumps(payload), encoding="utf-8")


def test_the_counts_reach_the_settings_tab(monkeypatch, tmp_path):
    rows = ([{"verdict": "at_stated_row"}] * 3 + [{"verdict": "present_no_line"}] * 5
            + [{"verdict": "moved"}] * 2 + [{"verdict": "absent"}]
            + [{"verdict": "unmatched"}] * 4 + [{"verdict": "unreadable"}] * 6)
    _write_report(tmp_path, rows, documents=[{"a_page_not_a_file": True, "stored": 4}])
    monkeypatch.setattr(build_web, "__file__", str(tmp_path / "build_web.py"))
    got = build_web._sheet_check()
    assert got["at_stated_row"] == 3 and got["present"] == 5 and got["moved"] == 2
    assert got["absent"] == 1 and got["unmatched"] == 4 and got["unreadable"] == 6
    assert got["checked"] == 21 and got["live"] == 10872
    assert got["pages_not_files"] == 4
    assert got["generated"] == "2026-09-09 18:30"


def test_an_absent_report_is_not_an_error(monkeypatch, tmp_path):
    """The check is optional in run_daily.py, so a night it did not run must not break
    the build or the tab."""
    monkeypatch.setattr(build_web, "__file__", str(tmp_path / "build_web.py"))
    assert build_web._sheet_check() == {}


def test_a_corrupt_report_is_not_an_error(monkeypatch, tmp_path):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "sheet_check.json").write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(build_web, "__file__", str(tmp_path / "build_web.py"))
    assert build_web._sheet_check() == {}


def test_the_panel_renders_every_count_the_check_produces():
    """A verdict added to verify_sheet_positions and not surfaced here would silently
    stop being reported, which is how the check quietly becomes decorative."""
    page = (APP / "index.html").read_text(encoding="utf-8", errors="replace")
    for field in ("checked", "live", "documents", "at_stated_row", "present", "moved",
                  "absent", "unmatched", "unreadable", "pages_not_files"):
        assert f"CHANNELS.sheet_check.{field}" in page, f"{field} is not on the tab"


def test_the_panel_says_what_it_refused_to_judge():
    """The honest half. A panel that showed only what it confirmed would read as a clean
    bill of health over a check that covered two thirds of the stock."""
    page = (APP / "index.html").read_text(encoding="utf-8", errors="replace")
    assert "could not be judged" in page
    assert "not recognising a property is not the same as it being sold" in page


def test_the_export_carries_it(monkeypatch, tmp_path):
    """_export_channel_health is what writes channels.json; the key has to be in it."""
    import inspect
    assert '"sheet_check": _sheet_check()' in inspect.getsource(
        build_web._export_channel_health)
