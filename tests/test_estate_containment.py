"""Borrowing a state from an estate spelled longer here than where its state is known.

The same project is written several ways across a catalogue: "Central Springs" on one
builder's sheet, "Caboolture Central Springs" on another's. Exact-match state borrowing
sees those as different estates and lends nothing, so the longer spelling stays stateless
— and without a state no suburb rule runs on it at all.
"""
import time

from api._candidates import _containment_candidates, _state_by_estate_containment

KNOWN = {
    ("estate_name", "central springs"): "QLD",
    ("estate_name", "rosewood green"): "QLD",
    ("estate_name", "toolern waters"): "VIC",
    ("estate_name", "south east"): "VIC",       # generic, must never lend
    ("estate_name", "the rise"): "NSW",         # too short, must never lend
    ("builder_name", "someone"): "WA",          # wrong field, must never lend
}


def _state(estate):
    return _state_by_estate_containment({"estate_name": estate},
                                        _containment_candidates(KNOWN))


def test_a_longer_spelling_borrows_from_the_shorter_one():
    assert _state("Caboolture Central Springs") == "QLD"
    assert _state("Rosewood Rosewood Green") == "QLD"
    assert _state("Toolern Waters Melton") == "VIC"


def test_a_generic_region_word_lends_nothing():
    """"south east" appears inside plenty of estate names and is evidence of nothing. It
    lent VIC to a Queensland row when this was first measured."""
    assert _state("South East Queensland Estate") == ""


def test_a_short_estate_name_lends_nothing():
    assert _state("The Rise At Somewhere") == ""


def test_only_the_estate_field_lends():
    assert _state("Someone Estate") == ""


def test_a_partial_word_is_not_a_match():
    """'rosewood green' must not match inside 'rosewoodgreenery'."""
    assert _state("Rosewoodgreenery Park") == ""


def test_two_states_among_the_matches_lends_neither():
    known = dict(KNOWN)
    known[("estate_name", "melton waters")] = "SA"
    got = _state_by_estate_containment(
        {"estate_name": "Toolern Waters Melton Waters"}, _containment_candidates(known))
    assert got == ""


def test_it_does_not_scan_the_whole_catalogue_per_row():
    """THE FIRST VERSION TURNED A SEARCH INTO A TEN-MINUTE WAIT.

    It compiled a fresh regex for every known estate for every stateless row — roughly a
    million pattern compiles on the live catalogue — on the per-request path. The
    filtering is now hoisted out of the row loop and the inner test is a plain substring
    scan."""
    big = {("estate_name", f"estate number {i:05d}"): "QLD" for i in range(2000)}
    candidates = _containment_candidates(big)
    start = time.time()
    for _ in range(2000):
        _state_by_estate_containment({"estate_name": "somewhere else entirely"},
                                     candidates)
    assert time.time() - start < 5.0, "containment lookup is too slow for a request path"
