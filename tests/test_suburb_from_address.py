"""The suburb read from the row's own address, and the traps that rule must survive.

Coleen, 19 Aug: a Donnybrook lot she was looking at in E-Agent could not be found in the
app. It was in there, priced and bedroomed, with no suburb. 4,230 live rows were in that
state; 2,873 of them had nothing in the suburb column at all, so no search could reach
them and the Building Stock table showed them with no location.

Every rule here was scored on a holdout of 4,678 rows that already carry a checked
suburb — hide it, recover it, compare — before it was allowed to run.
"""
import pytest

import suburb_quality as SQ


def resolve(addr, state="VIC", **extra):
    row = {"lot_address": addr, "state": state}
    row.update(extra)
    return SQ.resolve(row)


# --------------------------------------------------------------- what it must recover
@pytest.mark.parametrize("addr, state, expected", [
    ("1015 Bristly Way, Lera, Officer", "VIC", "Officer"),
    ("2212 Skippers Street, Estate : Ooranya Beveridge", "VIC", "Beveridge"),
    # Marketing tacked on after the locality — 2,000+ Direct Builder Portal rows.
    ("LOT 60913 BASALT ST, KALKALLO - 7 STAR ENERGY RATING", "VIC", "Kalkallo"),
    ("LOT 1312 BASIL DR, WALLAN - 7 STAR ENERGY RATING", "VIC", "Wallan"),
    # The suburb, then the REGION it is marketed under. The suburb wins.
    ("LOT 12015 ESPERANCE AVE, ARMSTRONG CREEK (GEELONG)", "VIC", "Armstrong Creek"),
])
def test_it_reads_the_locality_out_of_the_address(addr, state, expected):
    suburb, why = resolve(addr, state)
    assert suburb == expected
    assert why == SQ.FROM_ADDRESS_TAIL
    assert SQ.is_located(why)


# ------------------------------------------------------------------------- the traps
def test_a_street_named_after_a_town_is_not_the_suburb():
    """'614 Buckley Avenue' is ON Buckley Avenue, not IN Buckley (a real VIC locality)."""
    suburb, why = resolve("614 Buckley Avenue, Alluvium", "VIC")
    assert suburb == "", f"published {suburb!r} as the suburb of a lot on that street"
    assert not SQ.is_located(why)


def test_a_flattened_table_row_is_never_read_as_an_ADDRESS():
    """Half of lot_address is not an address but a row of table cells, and its tail is a
    HOUSE DESIGN. London, Madrid, Paddington, Ormond, Belmont and Bright are all builder
    design names AND real Australian localities.

    The address rule must never claim one of these as a located suburb. A later,
    explicitly-inferred pass may still offer a best reading of the same text — that is a
    different promise, and it is the one that is allowed to be wrong."""
    for junk in ("West 316 Sunrise Deanside Available London 22 Bright $365,000",
                 "2120 Stage 21 400 $627,000 July 2026 Madrid 190 Paddington",
                 "20 Land Only CNR 344.3 Q1 2026 $1,037,820 $997,820 AVAILABLE"):
        suburb, why = resolve(junk, "VIC")
        assert why != SQ.FROM_ADDRESS_TAIL, f"{junk[:40]!r} -> {suburb!r} as an address"
        assert not SQ.is_located(why), f"{junk[:40]!r} -> {suburb!r} treated as located"


def test_it_will_not_answer_without_a_state():
    """'Caboolture Central Springs' tails to Springs, a locality in Western Australia and
    nowhere else, for a Queensland lot. Requiring the row's own state removes the class."""
    assert resolve("12 Smith St, Caboolture Central Springs", "")[0] == ""


def test_a_segment_that_calls_itself_an_estate_is_not_a_located_locality():
    """'Rangewood Estate, Lot 109' is in Lowood; Rangewood is the development.

    As above, the address rule must not claim it. The inferred pass may still surface it
    as a best reading, marked as such."""
    suburb, why = resolve("Rangewood Estate, Lot 109", "QLD")
    assert not (suburb == "Rangewood" and why == SQ.FROM_ADDRESS_TAIL)
    assert not (suburb == "Rangewood" and SQ.is_located(why))


def test_the_longer_locality_wins():
    """'Armstrong Creek' must beat 'Creek', 'Melton South' must beat 'Melton'."""
    assert resolve("11946 Caspian Crescent, Armstrong Creek", "VIC")[0] == "Armstrong Creek"


# ---------------------------------------------------------- it never overrides better
def test_a_stated_suburb_still_wins():
    """This rule runs LAST. A suburb the source actually stated is stronger evidence."""
    suburb, why = resolve("LOT 60913 BASALT ST, KALKALLO", "VIC", suburb="Wallan")
    assert (suburb, why) == ("Wallan", SQ.STATED)


@pytest.mark.parametrize("addr, expected", [
    # These two resolve through the POSTCODE anchor, which is stronger and runs first.
    # They are here because both were unfindable before any of this work, and the point
    # is the suburb, not which rule got there.
    ("23 Bottlebrush Circuit, Orchardfield, Kyabram 3620", "Kyabram"),
    ("1368 Margery St, Toolern Waters , Melton South 3338", "Melton South"),
])
def test_a_composite_address_still_yields_its_suburb(addr, expected):
    suburb, why = resolve(addr, "VIC")
    assert suburb == expected
    assert SQ.is_located(why)


def test_a_postcode_anchored_read_still_wins():
    suburb, why = resolve("15 Example St, Officer 3809", "VIC")
    assert why in (SQ.FROM_POSTCODE, SQ.STATED, SQ.FROM_ADDRESS_TAIL)
    assert suburb == "Officer"


# -------------------------------------------------------------------------- overseas
@pytest.mark.parametrize("addr, estate", [
    ("Lot 189/A075X06 Unit 189/A075X06, DAMAC ISLANDS 2, Dubai, DXB, 0", "DAMAC Islands Stage 2"),
    ("Lot B070206 Unit B070206, Circumferential Road 1, Brgy. Balabago, Jaro", "SMDC Glade Residences ILOILO"),
])
def test_an_overseas_listing_is_labelled_not_counted_as_a_gap(addr, estate):
    """633 live listings are Dubai and Manila off-plan apartments sitting in the same
    pool as Melbourne house-and-land. They will never have an Australian suburb, and
    reporting them as "the source did not state one" reads as a scraping bug to chase."""
    row = {"lot_address": addr, "estate_name": estate, "suburb": "0", "state": ""}
    suburb, why = SQ.resolve(row)
    assert suburb == ""
    assert why == SQ.BLANK_OVERSEAS
    assert not SQ.is_located(why)


def test_an_australian_listing_is_not_called_overseas():
    row = {"lot_address": "LOT 1312 BASIL DR, WALLAN", "state": "VIC"}
    assert SQ.resolve(row)[1] != SQ.BLANK_OVERSEAS


# ----------------------------------------------------------- inferred, not believed
def test_an_inferred_suburb_is_searchable_but_never_located():
    """The whole safety of the last-resort rule is this split.

    Ahsan, 21 Aug: fill the remainder from their own text, he would rather listings be
    findable than invisible. On holdout that rule is 83.8%-86.8% right, so the value is
    produced and marked, and everything that needs the position to be TRUE keeps
    refusing it: distance filtering, benchmarking and comparables all gate on
    is_located. A wrong suburb costs a noisier search; a wrong comparable costs a client
    a wrong valuation."""
    row = {"lot_address": "2120 Stage 21 400 $627,000 July 2026 Madrid 190 Paddington",
           "state": "VIC"}
    suburb, why = SQ.resolve(row)
    if not suburb:
        return                                  # declining is always allowed
    assert SQ.is_inferred(why), why
    assert SQ.is_searchable(why), "an inferred suburb must still be findable"
    assert not SQ.is_located(why), "an inferred suburb must never be benchmarked"


def test_the_reason_says_it_may_be_wrong():
    """It is shown to a consultant who forwards it to a buyer. It has to say so itself."""
    text = SQ.INFERRED_FROM_TEXT.lower()
    assert "may be wrong" in text and "did not state" in text


def test_a_stated_suburb_is_never_downgraded_to_inferred():
    suburb, why = SQ.resolve({"lot_address": "anything at all", "suburb": "Officer",
                              "state": "VIC"})
    assert (suburb, why) == ("Officer", SQ.STATED)
