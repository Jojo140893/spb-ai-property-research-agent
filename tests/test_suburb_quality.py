"""
A value in the suburb column is not the same thing as a place.

3,854 published rows showed Coleen something that is not a locality — '2026', 'offer',
'GARAGE', 'Logan City Council', 'Rooms Rooms m2 m2 m2' — printed in the suburb column and
offered in the suburb filter as though it were a town. The search path had already been
taught to refuse those rows; the export never was, so a row could be excluded from every
search as "not a recognised locality" and still be published with that value as its suburb.

The rules these tests hold in place:
  * recovery is STRUCTURAL and STATE-CHECKED — the value contains a locality, or the
    row's own text names one immediately before a postcode that belongs to its state,
  * everything else is BLANKED, with the reason recorded,
  * the stored column is never rewritten, because suburb_norm is part of content_hash.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import suburb_quality                                             # noqa: E402
from geo import SuburbGeoIndex                                    # noqa: E402

_GEO = SuburbGeoIndex()


def _r(**kw):
    row = {"suburb": "", "state": "", "lot_address": "", "street_address": "",
           "source_text": ""}
    row.update(kw)
    return row


def test_a_real_locality_in_its_own_state_is_kept():
    got, why = suburb_quality.resolve(_r(suburb="Tarneit", state="VIC"))
    assert (got, why) == ("Tarneit", suburb_quality.STATED), (got, why)


def test_a_locality_that_contradicts_the_rows_state_is_refused():
    """'LOGAN' is a locality in Victoria and nowhere else, and QLD stocklists put it in
    this column. Accepting it on name alone geocodes a Queensland lot to Victoria — 84
    live rows carried a state their own suburb contradicts."""
    assert _GEO.states_for_suburb("LOGAN") == ["VIC"], "fixture no longer reproduces"
    got, why = suburb_quality.resolve(_r(suburb="LOGAN", state="QLD"))
    assert got == "", f"a Victorian locality was accepted for a QLD row: {got!r}"
    assert why == suburb_quality.BLANK_NOT_A_PLACE, why


def test_a_locality_glued_to_an_estate_is_unglued():
    for raw, want in (("Cloverton Estate , Kalkallo 3064", "Kalkallo"),
                      ("Kinship Estate, Tarneit 3029", "Tarneit"),
                      ("Lera, Officer", "Officer")):
        got, why = suburb_quality.resolve(_r(suburb=raw, state="VIC"))
        assert got == want, f"{raw!r} -> {got!r}, want {want!r}"
        assert why == suburb_quality.FROM_COMPOSITE, why


def test_junk_is_blanked_and_says_why():
    """Every one of these is a real stored value."""
    for junk in ("2026", "offer", "GARAGE", "Logan City Council", "COAST COUNCIL",
                 "IN TERNAL BALCONY TOTAL", "Rooms Rooms m2 m2 m2", "Untitled Packages",
                 "Dual Key", "SOUTH EAST", "7 Star Energy Rating"):
        got, why = suburb_quality.resolve(_r(suburb=junk, state="QLD"))
        assert got == "", f"{junk!r} was published as a suburb ({got!r})"
        assert why == suburb_quality.BLANK_NOT_A_PLACE, (junk, why)


def test_a_blank_is_reported_as_never_stated_not_as_rejected():
    """The two blanks mean different things to a consultant chasing the gap: one is a
    value we refused, the other is a value the builder never gave."""
    _, why = suburb_quality.resolve(_r(suburb="", state="QLD"))
    assert why == suburb_quality.BLANK_ABSENT, why
    _, why2 = suburb_quality.resolve(_r(suburb="GARAGE", state="QLD"))
    assert why2 == suburb_quality.BLANK_NOT_A_PLACE, why2


def test_with_no_state_recorded_a_name_is_shown_but_never_located():
    """SHOWN is not LOCATED, and conflating them is how '122 Atlas Crescent' became the
    SA town of Crescent.

    860 live rows name a place with no state beside it. Blanking them all throws away
    'Mango Hill' x38 and 'Clyde North' x32, which the source plainly did state; trusting
    them geocodes a lot to whichever state happens to share the name — and a fifth of
    Australian locality names exist in more than one. So the name is displayed with the
    caveat and refused as a location.
    """
    got, why = suburb_quality.resolve(_r(suburb="Springfield"))
    assert got == "Springfield", got
    assert why == suburb_quality.UNCHECKED_NO_STATE, why
    assert not suburb_quality.is_located(why), "an unchecked name must not be geocoded"

    got2, why2 = suburb_quality.resolve(_r(suburb="Logan City Council"))
    assert got2 == "" and why2 == suburb_quality.BLANK_NO_STATE, (got2, why2)


def test_a_street_is_refused_even_with_no_state_to_check_against():
    """Real rows: '122 Atlas Crescent' published the suburb 'Crescent' (a locality in
    SA), and '132 Paramatta Street' published 'Paramatta'. One check needs no state — a
    street type is a street type."""
    for value, address in (("Crescent", "122 Atlas Crescent"),
                           ("Paramatta", "132 Paramatta Street")):
        got, why = suburb_quality.resolve(_r(suburb=value, lot_address=address))
        assert got == "", f"{value!r} from {address!r} was published as a place"
        assert why == suburb_quality.BLANK_NOT_A_PLACE, why


def test_the_rows_own_address_beats_a_street_name_in_the_suburb_column():
    """'LOT 2950 LOCKINGTON RD, TARNEIT 3029' was published as Lockington — a real
    Victorian town about 200 km from the lot — because the spreadsheet put the street's
    name in the suburb position. An address is stronger evidence than a column."""
    got, why = suburb_quality.resolve(_r(
        suburb="Lockington", state="VIC",
        lot_address="LOT 2950 LOCKINGTON RD, TARNEIT 3029"))
    assert got == "Tarneit", got
    assert why == suburb_quality.FROM_ADDRESS, why
    assert suburb_quality.is_located(why)


def test_a_correct_suburb_is_not_overridden_by_its_own_postcode():
    """Both conditions are required. Either alone would start rewriting values that are
    simply right — here the column and the address agree, so nothing may change."""
    got, why = suburb_quality.resolve(_r(
        suburb="Tarneit", state="VIC",
        lot_address="LOT 2950 SOME ROAD, TARNEIT 3029"))
    assert (got, why) == ("Tarneit", suburb_quality.STATED), (got, why)


def test_a_state_name_in_the_column_is_rebuilt_from_the_rows_own_address():
    """56 Proxima rows hold suburb='New South Wales'; the address names Wilton."""
    got, why = suburb_quality.resolve(_r(
        suburb="New South Wales", state="NSW",
        lot_address="LOT 21, BINGARA DRIVE, WILTON, New South Wales, 2571"))
    assert got == "Wilton", got
    assert why == suburb_quality.STATED, why


def test_a_postcode_anchor_recovers_only_when_the_postcode_fits_the_state():
    """The anchor is what makes this safe. Without the state-range check the same
    pattern reads a LOT NUMBER ('Lot 2427, Newhaven'), a DATE ('Raceview 2026-08-01')
    and a non-postcode ('Fraser Rise 1106') as suburbs — all three are live rows."""
    good = _r(suburb="COAST COUNCIL", state="NSW",
              source_text="Jensen Rise Estate - Wadalba 49 Road #5 Wadalba 2259")
    got, why = suburb_quality.resolve(good)
    assert got == "Wadalba" and why == suburb_quality.FROM_POSTCODE, (got, why)

    # 2026 is a NSW-range code, so it must NOT satisfy a QLD row even though Raceview
    # is a real QLD locality.
    bad = _r(suburb="Dual Key", state="QLD",
             source_text="DUAL PR8756 21 Raceview 2026-08-01 00:00:00 534")
    got2, why2 = suburb_quality.resolve(bad)
    assert got2 == "", f"a date was read as a postcode: {got2!r}"
    assert why2 == suburb_quality.BLANK_NOT_A_PLACE, why2


def test_free_text_alone_never_fills_a_suburb():
    """The rejected technique, kept rejected. Over the 953 live rows where a free-text
    scan finds something it returns the row's own street, its house design or a region
    header often enough that the result cannot be published."""
    row = _r(suburb="BRISBANE NORTH", state="QLD",
             source_text="12 Windsor Street, BRISBANE NORTH 12 Windsor Street Woodford")
    got, why = suburb_quality.resolve(row)
    assert got == "", f"a free-text scan filled the suburb with {got!r}"
    assert why == suburb_quality.BLANK_NOT_A_PLACE, why


def test_one_locality_one_spelling():
    """'GLENVALE' and 'Glenvale' are 27 rows split into two suburbs — two facet entries,
    two benchmark cohorts, two rows in a suburb dropdown. 13 localities across 164 live
    rows are split this way.

    The spelling is taken from the locality dataset rather than .title() so that the one
    authoritative source decides, and an improvement to the dataset propagates instead of
    being overridden here.
    """
    for a_raw, b_raw, state in (("GLENVALE", "Glenvale", "QLD"),
                                ("TOOWOOMBA", "Toowoomba", "QLD"),
                                ("LOGAN RESERVE", "Logan Reserve", "QLD")):
        a, _ = suburb_quality.resolve(_r(suburb=a_raw, state=state))
        b, _ = suburb_quality.resolve(_r(suburb=b_raw, state=state))
        assert a == b != "", f"{a_raw!r} and {b_raw!r} resolved to {a!r} and {b!r}"
        assert a == _GEO.canonical_suburb(a_raw), \
            f"spelling should come from the locality dataset, got {a!r}"


def test_the_stored_column_is_never_modified():
    """The whole reason this is a function and not an UPDATE."""
    row = _r(suburb="Cloverton Estate , Kalkallo 3064", state="VIC")
    before = dict(row)
    suburb_quality.resolve(row)
    assert row == before, "resolve() mutated the row it was given"


def run_all():
    tests = [
        ("a real locality is kept", test_a_real_locality_in_its_own_state_is_kept),
        ("a locality contradicting the state is refused",
         test_a_locality_that_contradicts_the_rows_state_is_refused),
        ("an estate glued to a locality is unglued", test_a_locality_glued_to_an_estate_is_unglued),
        ("junk is blanked and says why", test_junk_is_blanked_and_says_why),
        ("never-stated differs from rejected",
         test_a_blank_is_reported_as_never_stated_not_as_rejected),
        ("no state: shown but never located",
         test_with_no_state_recorded_a_name_is_shown_but_never_located),
        ("a street is refused with no state either",
         test_a_street_is_refused_even_with_no_state_to_check_against),
        ("the address beats a street in the suburb column",
         test_the_rows_own_address_beats_a_street_name_in_the_suburb_column),
        ("a correct suburb is not overridden",
         test_a_correct_suburb_is_not_overridden_by_its_own_postcode),
        ("a state name is rebuilt from the address",
         test_a_state_name_in_the_column_is_rebuilt_from_the_rows_own_address),
        ("postcode anchor needs the state to fit",
         test_a_postcode_anchor_recovers_only_when_the_postcode_fits_the_state),
        ("free text alone never fills a suburb", test_free_text_alone_never_fills_a_suburb),
        ("one locality, one spelling", test_one_locality_one_spelling),
        ("the stored column is never modified", test_the_stored_column_is_never_modified),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] suburb: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] suburb: {name}: {e}")
    return failed


if __name__ == "__main__":
    sys.exit(1 if run_all() else 0)
