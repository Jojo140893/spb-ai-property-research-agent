"""With no state there is nothing to check a stated suburb against.

That branch exists for good reason — 'Mango Hill' x38 and 'Clyde North' x32 are plainly
right and blanking them throws away real stock. But it published whatever the column
held, and OCR feeds it more than a text reader does: reading the 57 image-only stocklists
produced "Price" on 6 listings, "City" on 8 and "Haven" on 5 — a Price column, a City View
aspect and a Haven floorplan.

Haven (VIC) and Vista (SA) are REAL localities, so a gazetteer would not have caught them
even with a state. The only thing separating a place from a column heading here is the
word itself.
"""
import sys

sys.path.insert(0, "api")

import suburb_quality as SQ


def _resolve(value, state=""):
    return SQ.resolve({"suburb": value, "state": state, "suburb_source": ""})


def test_a_column_heading_is_not_a_suburb():
    for junk in ("Price", "City", "Total", "Status", "Level", "Aspect", "Balcony"):
        suburb, why = _resolve(junk)
        assert suburb == "", "%s was published as a suburb" % junk
        assert not SQ.is_searchable(why)


def test_a_floorplan_name_that_is_also_a_town():
    """The ones a gazetteer cannot save you from."""
    assert _resolve("Haven")[0] == ""
    assert _resolve("Vista")[0] == ""


def test_but_with_a_state_the_real_town_still_stands():
    """The guard is for the branch that has nothing to check against. Where the state IS
    known the gazetteer decides, and Haven VIC and Vista SA are real places."""
    assert _resolve("Haven", "VIC")[0] == "Haven"
    assert _resolve("Vista", "SA")[0] == "Vista"


def test_the_names_this_branch_exists_for_survive():
    """Blanking every stateless value would throw away real stock. A multi-word place
    name is untouched."""
    for good in ("Mango Hill", "Clyde North", "Waverley", "Armstrong Creek"):
        suburb, why = _resolve(good)
        assert suburb == good, good
        assert SQ.is_searchable(why)
        assert not SQ.is_located(why), "still never a location without a state"
