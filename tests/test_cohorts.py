"""
Cohorts are the unit a benchmark is computed for, so a wrong key is a wrong benchmark.

Two things are being held in place here: that spreadsheet debris never becomes a suburb
we query a provider about, and that the tempting-but-wrong suburb recovery stays out.
"""

from cohorts import (SKIP_KIND, SKIP_NO_BEDROOMS, SKIP_NO_SUBURB, build, key_for,
                     land_band)


def _row(**kw):
    base = {"suburb": "Toowoomba", "state": "QLD", "product_type": "House & Land",
            "bedrooms": 4, "land_sqm": 400.0, "price": 750_000}
    base.update(kw)
    return base


def test_spreadsheet_debris_never_becomes_a_suburb():
    """Ungrouped, the five largest cohorts in the whole catalogue were column headers.

        44  "In Ternal Balcony Total" QLD
        39  "One Part Contracts" VIC
        34  "7 Star Energy Rating" VIC

    Those would have become the highest-quota provider queries in the nightly run, asked
    about places that do not exist.
    """
    for junk in ("In Ternal Balcony Total", "7 Star Energy Rating", "One Part Contracts",
                 "Untitled Packages", "External Agent Price List", "Dual Key"):
        key, why = key_for(_row(suburb=junk, state="VIC"))
        assert key is None, f"{junk!r} was accepted as a suburb"
        assert why == SKIP_NO_SUBURB

    # ...while a real locality buried in a composite is still recovered.
    key, _ = key_for(_row(suburb="Kemps Estate | 155 Boyd Avenue, Austral", state="NSW"))
    assert key and key.suburb == "Austral", key


def test_a_locality_is_recovered_only_when_a_postcode_anchors_it():
    """The estate-name trap, held open deliberately.

    Scanning a row for any known suburb name "recovers" 27% of the blocked rows and the
    recoveries are wrong — an estate name is not a suburb, and many estate names are real
    localities somewhere else:

        estate "Jensen Rise" -> "Jensen"   (a QLD suburb; the lot is in Wadalba NSW)
        estate "Warner Park" -> "Warner"   (the lot is in Warnervale NSW)

    Only the postcode-anchored form is accepted, because that is address grammar rather
    than a guess about which of several names is the locality.
    """
    anchored = _row(suburb="COAST COUNCIL", state="NSW",
                    source_text="Jensen Rise Estate - Wadalba 49 Road #5 Wadalba 2259 NSW")
    key, _ = key_for(anchored)
    assert key and key.suburb == "Wadalba", key

    # The same row WITHOUT the postcode must not fall back to the estate name.
    unanchored = _row(suburb="COAST COUNCIL", state="NSW",
                      estate_name="Jensen Rise", source_text="Jensen Rise Estate lot 49")
    key, why = key_for(unanchored)
    assert key is None, f"an estate name was accepted as a suburb: {key}"
    assert why == SKIP_NO_SUBURB


def test_a_cohort_is_never_shared_across_price_kinds():
    """The whole point of the key. A land price and a package price for the same suburb,
    bedroom count and land band must not land in one bucket."""
    package = key_for(_row(product_type="House & Land"))[0]
    land = key_for(_row(product_type="Land"))[0]
    assert package and land
    assert package != land, "a land listing shared a cohort with a package"
    assert package.price_kind != land.price_kind


def test_a_row_that_cannot_be_keyed_is_counted_not_dropped():
    rows = [_row(),                                   # keyable
            _row(bedrooms=None),                      # no bedrooms
            _row(product_type="", land_price=0, build_price=0),   # unknown kind
            _row(suburb="7 Star Energy Rating")]      # not a place
    cohorts, skipped = build(rows)
    assert sum(len(v) for v in cohorts.values()) == 1
    assert skipped[SKIP_NO_BEDROOMS] == 1
    assert skipped[SKIP_KIND] == 1
    assert skipped[SKIP_NO_SUBURB] == 1
    assert sum(skipped.values()) == 3, "a row went missing instead of being counted"


def test_land_bands_and_the_absence_of_one():
    assert land_band(400) == "300-450"
    assert land_band(299.9) == "0-300"
    assert land_band(1200) == "1000+"
    # Not a bucket: a listing with no land size cannot be matched on land size, which is
    # one of the three things the client named.
    assert land_band(0) is None and land_band(None) is None


def run_all():
    tests = [
        ("debris never becomes a suburb", test_spreadsheet_debris_never_becomes_a_suburb),
        ("recovery needs a postcode anchor", test_a_locality_is_recovered_only_when_a_postcode_anchors_it),
        ("cohorts never mix price kinds", test_a_cohort_is_never_shared_across_price_kinds),
        ("unkeyable rows are counted", test_a_row_that_cannot_be_keyed_is_counted_not_dropped),
        ("land bands, and having none", test_land_bands_and_the_absence_of_one),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] cohorts: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] cohorts: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
