"""The project's own name, when the price list behind it has gone.

E-Agent derives a project's name from its own link label, and those labels routinely carry
the suburb: "Eastbury Wheelers Hill", "Villea Palm Beach", "Arca, Stafford". That is
normally redundant — the document says where it is. It stops being redundant when the
document goes: Eastbury's Dropbox link now answers with an HTML page instead of the PDF,
the document map lost the entry, and 16 live rows became unnameable overnight.

Measured against every row that already has a LOCATED suburb: 94% agreement with the
guards below, 83% without them. That is why the answer is INFERRED and never located.
"""
import sys

sys.path.insert(0, "api")

import suburb_quality as SQ


def _resolve(builder, state="VIC", **kw):
    row = {"builder_name": builder, "state": state, "suburb": "", "suburb_source": ""}
    row.update(kw)
    return SQ.resolve(row)


def test_a_project_named_after_its_suburb_is_findable():
    suburb, why = _resolve("Eastbury Wheelers Hill")
    assert suburb == "Wheelers Hill"
    assert SQ.is_searchable(why)


def test_and_it_is_inferred_not_located():
    """It is right 94% of the time, which is fine for deciding what to show a consultant
    and not fine for geocoding, distance-filtering or benchmarking."""
    _suburb, why = _resolve("Eastbury Wheelers Hill")
    assert not SQ.is_located(why), (
        "a 94% rule must never reach the code that puts a pin on a map")


def test_the_builder_is_not_a_town():
    """"Hudson Homes" carries lots in Bald Hills and Bethania. Hudson is a real QLD
    locality 1,500 km north, near Innisfail. Reading the project name reintroduced that
    exact error until the leftover test was applied here too."""
    suburb, why = _resolve("Hudson Homes", state="QLD")
    assert suburb == ""
    assert not SQ.is_searchable(why)


def test_but_a_project_that_merely_contains_its_suburb_keeps_it():
    """The counterweight. Rejecting on containment would throw away the whole rule."""
    assert _resolve("Villea Palm Beach", state="QLD")[0] == "Palm Beach"
    assert _resolve("Arca, Stafford", state="QLD")[0] == "Stafford"


def test_a_park_is_not_a_suburb():
    """"Fawkner Park West" is a development beside Fawkner PARK, on St Kilda Road.
    Reading "Fawkner" out of it moves the listing 15 km — and that single class was 11
    points of the rule's accuracy."""
    from geo import SuburbGeoIndex

    index = SuburbGeoIndex()
    assert index.locate("Fawkner", "VIC"), "the premise: Fawkner IS a VIC locality"
    got = SQ._longest_locality_in_text(
        {"builder_name": "Fawkner Park West", "suburb": "", "lot_address": ""},
        "VIC", index)
    assert got != "Fawkner", "the feature guard has to stop this"


def test_the_feature_guard_does_not_eat_real_suburbs():
    """Plenty of real localities END in a feature word. The guard only fires when the
    feature word FOLLOWS a complete locality match."""
    assert _resolve("Villea Palm Beach", state="QLD")[0] == "Palm Beach"
    assert _resolve("Eastbury Wheelers Hill")[0] == "Wheelers Hill"


def test_a_row_with_no_state_is_not_guessed_at():
    """A fifth of Australian locality names exist in more than one state."""
    suburb, why = _resolve("Eastbury Wheelers Hill", state="")
    assert not SQ.is_located(why)
