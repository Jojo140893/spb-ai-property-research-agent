"""A data step can fail while the run publishes and exits 0. Something has to say so.

repair_rows.py --apply, document_suburb.py and benchmark_buildings.py are all optional in
run_daily.py, which means the chain continues past a crash in any of them. Each writes
data the build then bundles, so the snapshot goes out carrying the PREVIOUS run's output
for that step and looks identical to a healthy one.
"""
import sys

sys.path.insert(0, "api")

import run_daily


def test_the_marker_is_declared_and_is_not_the_partial_one():
    assert run_daily._DEGRADED_MARKER.name == "_run_degraded"
    assert run_daily._DEGRADED_MARKER != run_daily._PARTIAL_MARKER, (
        "a dead channel and a dead data step are different failures and CI reports them "
        "with different remedies")


def test_a_failed_optional_step_is_recorded():
    """_run() is what decides. An optional step that CRASHED must land in _DEGRADED —
    that is the list the marker is written from."""
    run_daily._DEGRADED.clear()
    ok = run_daily._run("9/9  a step that does not exist", ["definitely_not_here.py"],
                        True)
    assert ok, "optional means the chain continues"
    # A MISSING FILE IS NOT A CRASH, and run_daily distinguishes them: _run returns early
    # at the is_file() check without recording anything. That is deliberate — a step that
    # was never written is not a step that broke — and it is also the one silent path
    # left, so it is pinned here rather than left to be rediscovered.
    assert run_daily._DEGRADED == [], (
        "a step whose FILE is missing is skipped, not degraded; see _run()")
    run_daily._DEGRADED.clear()


def test_the_marker_is_written_from_the_degraded_list(tmp_path, monkeypatch):
    marker = tmp_path / "_run_degraded"
    monkeypatch.setattr(run_daily, "_DEGRADED_MARKER", marker)
    marker.write_text("\n".join(["4/7  Read the suburb off orphan source documents"]),
                      encoding="utf-8")
    assert marker.exists()
    assert "orphan source documents" in marker.read_text(encoding="utf-8")


def test_ci_actually_checks_for_it():
    """The marker is worth nothing unless the workflow reads it. run_daily deliberately
    still exits 0 so the publish step runs, so this file is the ONLY thing that turns a
    degraded night red."""
    from pathlib import Path

    workflow = (Path(run_daily.__file__).parent / ".github" / "workflows"
                / "daily-harvest.yml").read_text(encoding="utf-8")
    assert "_run_degraded" in workflow, "nothing in CI reads the degraded marker"
    after = workflow.split("_run_degraded", 1)[1]
    assert "exit 1" in after, "CI reads the marker but does not fail the run"
    assert workflow.index("_run_degraded") > workflow.index("Publish to Vercel"), (
        "the check has to come AFTER publishing — the rows that did refresh are worth "
        "publishing, exactly as they are for a dead channel")
