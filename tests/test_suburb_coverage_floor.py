"""The guard that would have caught a suburb-blind harvest.

Every other health check in this pipeline counts ROWS. This is the only one that looks at
whether the rows still know where they are.
"""
import sys

import pytest

sys.path.insert(0, "api")

import build_web


def test_it_aborts_below_the_floor():
    with pytest.raises(SystemExit) as caught:
        build_web._assert_suburb_coverage(
            {"australian": 8795, "findable": 6000, "located": 5000, "share": 0.68})
    assert "found by suburb" in str(caught.value)
    assert "document_suburb.py or repair_rows.py" in str(caught.value), (
        "the message has to name the two steps that fail silently, or the person "
        "reading it at 9am has nowhere to start")


def test_it_passes_a_healthy_night():
    build_web._assert_suburb_coverage(
        {"australian": 8795, "findable": 8795, "located": 7682, "share": 1.0})


def test_the_floor_is_not_so_low_it_never_fires():
    """A floor of 0.5 would pass the exact catastrophe it exists for: every row inserted
    fresh with a blank suburb still leaves yesterday's correctly-named rows in place, so
    coverage halves rather than collapsing."""
    assert 0.90 <= build_web.SUBURB_COVERAGE_FLOOR < 1.0


def test_an_empty_catalogue_does_not_abort():
    """A build with nothing in it is a different failure, and the row-count guards in
    deploy.ps1 own it. This one must not turn a division by zero into a crash."""
    build_web._assert_suburb_coverage({})
    build_web._assert_suburb_coverage(
        {"australian": 0, "findable": 0, "located": 0, "share": 0.0})


def test_the_build_actually_calls_it():
    """The guard is only worth having if it runs. This asserts the wiring, because a
    refactor that drops the call leaves every test above still passing."""
    import inspect
    from pathlib import Path

    source = Path(build_web.__file__).read_text(encoding="utf-8")
    calls = [ln for ln in source.splitlines() if "_assert_suburb_coverage(" in ln
             and not ln.strip().startswith("def ")]
    assert calls, "nothing calls _assert_suburb_coverage"
    assert any("suburb_coverage" in ln or "_cov" in ln for ln in calls)
    assert "suburb_coverage" in inspect.getsource(build_web.build), (
        "build() must put the measurement in its meta, or the caller has nothing to check")
