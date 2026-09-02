"""Colin, 31 Aug: reports need an edit function "specifically for the bedroom
configuration field (e.g. 1/1), to correct data gaps from source portals".

A stocklist that never states a bed count is the commonest gap in the catalogue, and the
report simply omitted the figure — so a consultant had no way to put the number in short
of editing the PDF afterwards.
"""
import sys

sys.path.insert(0, "api")

from client_report import _fillable


def test_a_stated_figure_is_editable():
    got = _fillable(4, "bed")
    assert 'contenteditable="true"' in got
    assert ">4<" in got and "bed" in got
    assert "empty" not in got, "a stated value is not a gap"


def test_an_unstated_figure_becomes_a_blank_to_fill():
    got = _fillable(None, "bed")
    assert 'contenteditable="true"' in got
    assert "spec empty" in got, "the gap has to be visible and clickable"
    assert "not stated by the source" in got


def test_the_unit_travels_inside_the_wrapper():
    """Print hides an unfilled blank. If the unit sat outside the wrapper the report
    would print a stranded "bed" and read as broken rather than incomplete."""
    got = _fillable(None, "bath")
    inner = got[got.index('<span class="spec empty">'):]
    assert inner.rstrip().endswith("</span>")
    assert "bath</span>" in inner


def test_zero_is_a_figure_not_a_gap():
    """0 car spaces is a fact the source stated. `if not value` would have made it a
    blank."""
    got = _fillable(0, "car")
    assert "empty" not in got
    assert ">0<" in got


def test_the_report_hides_an_unfilled_blank_when_printed_and_prints_a_filled_one():
    import client_report

    src = open(client_report.__file__, encoding="utf-8").read()
    assert ".spec.empty {{ display: none; }}" in src, (
        "an unfilled blank must not reach the client as a dotted placeholder")
    assert "classList.toggle('empty'" in src, (
        "a blank the consultant FILLS must print — without this their correction is "
        "hidden on paper, which is the opposite of the ask")
