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


def test_the_nightly_step_is_report_only_and_bounded():
    """Two restraints the pre-merge review required, pinned so neither is lost.

    NO --apply: this script's docstring justifies acting on a single sighting because
    "the next harvest that finds the lot writes its availability back" -- but
    record_building writes availability only when the fresh read SUPPLIED one, and 1,951
    of the 6,730 rows in scope come from sources that never state it. On the last real run
    94 rows were marked Not Available and 21 had no previous status to restore.

    --limit: unbounded, the step opens 491 documents serially, about an hour on top of a
    harvest that took 65, 65, 66 and 69 minutes on the last four successful runs -- past
    the job's cap, which cancels the run and discards the night's harvest with it.
    """
    import re
    src = (APP / "run_daily.py").read_text(encoding="utf-8", errors="replace")
    step = re.search(r'"verify_sheet_positions\.py"[^)]*', src)
    assert step, "the sheet-check step has been removed from the nightly"
    args = step.group(0)
    assert '"--apply"' not in args, \
        "the nightly applies sheet-check writes again; they are not reversible for " \
        "sources that never state availability"
    assert '"--limit"' in args, "the sheet-check step is unbounded again"


def test_the_job_cap_leaves_room_for_the_harvest_and_the_check():
    """The harvest alone has taken 65-69 minutes on every recent run. A cancellation skips
    "Publish to Vercel" (if: success()) and kills the 8/8 state_sync push inside a
    finally, so the whole night is discarded -- an expense out of all proportion to the
    step that causes it."""
    import re
    yml = (APP / ".github" / "workflows" / "daily-harvest.yml").read_text(
        encoding="utf-8", errors="replace")
    caps = [int(m) for m in re.findall(r"^\s*timeout-minutes:\s*(\d+)", yml, re.M)]
    assert caps, "no job timeout found"
    assert max(caps) >= 150, f"the harvest job cap is {max(caps)}m; 65-69m of harvest " \
                             f"plus the sheet check does not fit"
