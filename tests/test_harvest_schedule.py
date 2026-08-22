"""The harvest has to land at 09:00 Melbourne every day of the year.

GitHub evaluates cron in UTC and Melbourne is UTC+10 for half the year and UTC+11 for the
other half, so one cron line cannot do it. The `TZ: Australia/Melbourne` env in the
workflow is process scope — it retimes the Python inside the job and cannot retime the
trigger, which fires before any of it exists.

This is why the drift was invisible: TZ keeps every date_checked correct straight through
it, so the staleness reporting, _fresh() in api/_candidates.py and deploy.ps1's guard all
stay satisfied while the run is an hour late.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

MELBOURNE = ZoneInfo("Australia/Melbourne")
WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-harvest.yml"


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _crons():
    # PyYAML parses a bare `on:` key as the boolean True, which is the one genuinely
    # surprising thing about reading a GitHub workflow from Python.
    spec = _workflow()
    triggers = spec.get("on", spec.get(True))
    return [c["cron"] for c in triggers["schedule"]]


def _fires(cron, moment):
    """Whether the gate lets this cron through at this instant. Mirrors the shell."""
    offset = moment.astimezone(MELBOURNE).utcoffset()
    if cron == "0 22 * * *":
        return offset == timedelta(hours=11)
    if cron == "0 23 * * *":
        return offset == timedelta(hours=10)
    raise AssertionError("unrecognised cron %r — update this test with the schedule" % cron)


def test_exactly_one_run_a_day_at_nine_am_for_a_full_year():
    """The invariant, checked against the real tz database rather than against arithmetic
    I did in my head. 400 days covers both transitions with room either side."""
    crons = _crons()
    start = datetime(2026, 8, 22, tzinfo=timezone.utc)
    for day in range(400):
        midnight = start + timedelta(days=day)
        fired = []
        for cron in crons:
            hour = int(cron.split()[1])
            moment = midnight.replace(hour=hour)
            if _fires(cron, moment):
                fired.append(moment.astimezone(MELBOURNE))
        assert len(fired) == 1, (
            "%s: %d runs, expected exactly 1 — a second run would double-harvest and a "
            "zero would skip the day" % (midnight.date(), len(fired)))
        assert fired[0].strftime("%H:%M") == "09:00", (
            "%s fired at %s, not 09:00 Melbourne" % (midnight.date(), fired[0]))


def test_the_single_cron_this_replaced_really_did_drift():
    """The premise. If this ever stops being true the whole gate is dead weight."""
    summer = datetime(2027, 1, 15, 23, tzinfo=timezone.utc).astimezone(MELBOURNE)
    winter = datetime(2026, 8, 22, 23, tzinfo=timezone.utc).astimezone(MELBOURNE)
    assert winter.strftime("%H:%M") == "09:00"
    assert summer.strftime("%H:%M") == "10:00", "23:00 UTC is 10am Melbourne under AEDT"


def test_the_gate_actually_guards_the_harvest():
    """Two crons with nothing discarding one of them is a double harvest every night."""
    jobs = _workflow()["jobs"]
    assert "gate" in jobs, "no gate job"
    assert jobs["harvest"].get("needs") == "gate"
    assert jobs["harvest"].get("if") == "needs.gate.outputs.go == 'true'"
    assert "go" in (jobs["gate"].get("outputs") or {})


def test_a_manual_run_is_never_gated():
    """workflow_dispatch exists so a failed night can be re-run at any hour. Gating it on
    the schedule would make the re-run button work for one hour a day."""
    script = _workflow()["jobs"]["gate"]["steps"][0]["run"]
    assert "github.event_name" in script and "schedule" in script
    assert "go=true" in script.split("github.event_name")[1].split("offset=")[0], (
        "a non-schedule trigger has to short-circuit to go=true before the offset check")


def test_the_gate_decides_on_the_offset_not_the_local_hour():
    """GitHub's scheduler runs late. A run that starts at 09:20 still has the right
    offset and must proceed; gating on the hour would cancel it and lose the day."""
    script = _workflow()["jobs"]["gate"]["steps"][0]["run"]
    assert "date +%z" in script
    assert "+1000" in script and "+1100" in script
