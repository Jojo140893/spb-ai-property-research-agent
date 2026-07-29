"""
Tests for resolving a listing's state.

Coleen, 28 July: "we should see the state. That's one of the main things." Only 406 of
4,308 harvested rows had one, because builders' stocklists rarely spell it out — each
file covers one region and assumes you know.

Every fixture below is a real stored row. Two of them are the reason the resolver decides
by agreement rather than by a fixed ranking: cross-checking the signals against each
other on the live data showed each one can be wrong alone.
"""

from geo import SuburbGeoIndex
from state_resolver import (normalise_state, resolve_state, state_from_postcode)

_GEO = SuburbGeoIndex()


def test_postcode_ranges_cover_every_state():
    cases = {
        "3000": "VIC", "3338": "VIC", "8000": "VIC",          # Melbourne + LVR
        "2000": "NSW", "2328": "NSW", "1010": "NSW",
        "4000": "QLD", "4207": "QLD", "9000": "QLD",
        "5000": "SA", "5114": "SA",
        "6000": "WA", "6164": "WA",
        "7000": "TAS",
        "0800": "NT", "0810": "NT",                            # leading zero
        "2600": "ACT", "2617": "ACT", "2900": "ACT", "0200": "ACT",
        # the boundary either side of the ACT block inside NSW's 2xxx range
        "2599": "NSW", "2619": "NSW", "2620": "NSW", "2921": "NSW",
    }
    for pc, want in cases.items():
        assert state_from_postcode(pc) == want, f"{pc} -> {state_from_postcode(pc)}, want {want}"
    # not postcodes
    for junk in ("", None, "12", "12345", "abcd", "0000"):
        assert state_from_postcode(junk) == "", repr(junk)


def test_normalise_state_accepts_what_the_files_actually_say():
    for raw, want in {"vic": "VIC", " NSW ": "NSW", "Victoria": "VIC",
                      "queensland": "QLD", "South Australia": "SA",
                      "BRISBANE NORTH QLD": "QLD", "": "", "Nowhere": ""}.items():
        assert normalise_state(raw) == want, f"{raw!r} -> {normalise_state(raw)!r}"


def test_a_misparsed_postcode_does_not_move_a_listing_interstate():
    """Real rows: lots in Clyde North and Truganina — plainly Victorian, and off E-Agent's
    Victoria page — carry 1028, 1030 and 2209, which are NSW codes. A four-digit number in
    the row was read as a postcode. Ranking postcode first would have exported them as NSW."""
    for bad_pc, suburb in (("1028", "Clyde North"), ("1030", "Clyde North"),
                           ("2209", "Truganina")):
        state, signal = resolve_state(postcode=bad_pc, suburb=suburb,
                                      page_state="VIC", geo=_GEO)
        assert state == "VIC", f"{suburb} with postcode {bad_pc} -> {state} ({signal})"
        assert "suburb" in signal and "page" in signal, signal


def test_a_region_name_in_the_suburb_column_does_not_win_on_its_own():
    """Real rows: the QLD stocklists put "LOGAN" in the suburb column. The locality
    dataset has a Logan in Victoria and nowhere else, so the suburb alone answers VIC for
    a Queensland lot. The postcode and the page both say QLD, and they carry it."""
    assert _GEO.states_for_suburb("LOGAN") == ["VIC"], "fixture no longer reproduces"
    state, signal = resolve_state(postcode="4207", suburb="LOGAN",
                                  page_state="QLD", geo=_GEO)
    assert state == "QLD", f"-> {state} ({signal})"
    assert "postcode" in signal and "page" in signal, signal


def test_an_unambiguous_suburb_resolves_on_its_own():
    assert _GEO.states_for_suburb("Shepparton") == ["VIC"]
    assert resolve_state(suburb="Shepparton", geo=_GEO) == ("VIC", "suburb")
    assert resolve_state(suburb="Winter Valley", geo=_GEO) == ("VIC", "suburb")


def test_a_shared_locality_name_is_decided_by_the_page_it_came_from():
    """"Springfield" exists in six states. On its own it must decide nothing."""
    assert len(_GEO.states_for_suburb("Springfield")) > 1
    assert resolve_state(suburb="Springfield", geo=_GEO) == ("", "")
    state, signal = resolve_state(suburb="Springfield", page_state="QLD", geo=_GEO)
    assert (state, signal) == ("QLD", "suburb + e-agent page")


def test_when_an_unambiguous_suburb_fights_the_page_the_page_wins():
    """Settled from the data, not from first principles. Exactly 8 of 4,308 rows have an
    unambiguous suburb that contradicts the page, and in every one the SUBURB is the wrong
    field:

      * "Jubilee" on the Victoria page, builder Hattan Homes — a Victorian estate name
        stored in the suburb column, which the locality dataset only knows as a QLD place.
      * "Price" on the Queensland page — a column-header word captured as a suburb, which
        happens to be a real locality in South Australia.

    The page state was read off E-Agent's own navigation, so it is the sturdier of the two.
    It is labelled as contested so the export never hides the disagreement.
    """
    state, signal = resolve_state(suburb="Jubilee", page_state="VIC", geo=_GEO)
    assert state == "VIC", f"-> {state} ({signal})"
    assert "conflicting" in signal, signal
    state2, signal2 = resolve_state(suburb="Price", page_state="QLD", geo=_GEO)
    assert state2 == "QLD" and "conflicting" in signal2, (state2, signal2)
    # ...and a real postcode agreeing with the page settles it outright, no longer contested
    state3, signal3 = resolve_state(postcode="3029", suburb="Jubilee", page_state="VIC",
                                    geo=_GEO)
    assert state3 == "VIC", (state3, signal3)
    assert "postcode" in signal3 and "conflicting" not in signal3, signal3


def test_the_page_alone_is_used_but_labelled_as_such():
    assert resolve_state(page_state="NSW", geo=_GEO) == ("NSW", "e-agent page")
    assert resolve_state(suburb="Nowhere At All", page_state="SA", geo=_GEO) == (
        "SA", "e-agent page")


def test_nothing_reliable_leaves_it_blank():
    assert resolve_state(geo=_GEO) == ("", "")
    assert resolve_state(suburb="Nowhere At All", geo=_GEO) == ("", "")
    # postcode and suburb conflict with nothing to break the tie -> refuse to choose
    state, signal = resolve_state(postcode="2000", suburb="Shepparton", geo=_GEO)
    assert state == "" and signal == "", (state, signal)


def test_a_real_postcode_carries_a_row_with_no_other_signal():
    assert resolve_state(postcode="4207", geo=_GEO) == ("QLD", "postcode")
    assert resolve_state(postcode="0810", geo=_GEO) == ("NT", "postcode")


def run_all():
    tests = [
        ("postcode ranges cover every state", test_postcode_ranges_cover_every_state),
        ("state names normalise", test_normalise_state_accepts_what_the_files_actually_say),
        ("misparsed postcode does not move a lot", test_a_misparsed_postcode_does_not_move_a_listing_interstate),
        ("region name in suburb column loses", test_a_region_name_in_the_suburb_column_does_not_win_on_its_own),
        ("unambiguous suburb resolves alone", test_an_unambiguous_suburb_resolves_on_its_own),
        ("shared locality decided by page", test_a_shared_locality_name_is_decided_by_the_page_it_came_from),
        ("page wins a straight conflict", test_when_an_unambiguous_suburb_fights_the_page_the_page_wins),
        ("page alone used but labelled", test_the_page_alone_is_used_but_labelled_as_such),
        ("nothing reliable stays blank", test_nothing_reliable_leaves_it_blank),
        ("real postcode carries a lone row", test_a_real_postcode_carries_a_row_with_no_other_signal),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] state: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] state: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
