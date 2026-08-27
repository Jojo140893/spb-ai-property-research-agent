"""A row's own postcode can supply the state its address needs.

Proxima publishes a complete address and no state column:

    Lot 297 Unit 1406, 50CourtRoad, FAIRFIELD, NewSouthWales, 2165

Fairfield is right there and 2165 is NSW beyond argument, but with the state field empty
there was nothing to check "Fairfield" against, so the row resolved to "the source did not
state one" and was invisible to every suburb search.

A postcode names its state deterministically, so this is evidence and not inference: the
suburbs it unlocks come back LOCATED.
"""
import sys

sys.path.insert(0, "api")

import suburb_quality as SQ


def _row(address, postcode, **kw):
    row = {"lot_address": address, "postcode": postcode, "state": "",
           "suburb": "", "suburb_source": ""}
    row.update(kw)
    return row


def test_the_address_names_the_suburb_once_the_postcode_names_the_state():
    row = _row("Lot 297 Unit 1406, 50CourtRoad, FAIRFIELD, NewSouthWales, 2165", "2165")
    suburb, why = SQ.resolve(row)
    assert suburb == "Fairfield"
    assert SQ.is_located(why), "a postcode is evidence, so this must not be downgraded"


def test_a_lot_number_that_looks_like_a_postcode_supplies_nothing():
    """THE CASE THIS GUARD EXISTS FOR. "2226 Whiterock White Rock ..." opens with its lot
    number, 2226 is a real NSW postcode, and the property is in White Rock, QUEENSLAND.
    That is the precise error repair_rows.find_fabricated_postcodes was written to undo,
    and a contains-test walks straight back into it."""
    row = _row("2226 Whiterock White Rock White Rock 156 - Facade A", "2226")
    assert SQ._state_from_the_rows_own_postcode(row) == ""


def test_a_postcode_must_be_where_a_postcode_goes():
    """An Australian address ends with its postcode. A number matching one somewhere in
    the middle is a lot, a size or a design code."""
    assert SQ._state_from_the_rows_own_postcode(
        _row("Lot 3029 Something Road, Nowhere", "3029")) == ""
    assert SQ._state_from_the_rows_own_postcode(
        _row("12 Smith St, Tarneit 3029", "3029")) == "VIC"


def test_a_recorded_state_is_never_overridden():
    """This only fills a gap. A row that HAS a state keeps it, even if its postcode
    disagrees -- that disagreement is repair_rows' business, not this function's."""
    row = _row("12 Smith St, Tarneit 3029", "3029", state="QLD")
    assert str(row["state"]) == "QLD"
    suburb, _why = SQ.resolve(row)
    assert suburb != "Tarneit", "Tarneit is VIC; a QLD row must not take it"


def test_junk_postcodes_are_ignored():
    for bad in ("", "21", "21655", "abcd", "0000"):
        assert SQ._state_from_the_rows_own_postcode(
            _row("Somewhere, %s" % bad, bad)) in ("", None), bad
