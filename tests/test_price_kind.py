"""
A price may only be compared to a price of the same kind.

This is the guard against repeating the mistake that published $675,000 against a
$1,127,000 property. Benchmarking gives that error a second home — on the COMPARABLE
side, where a vacant block listed at $310,000 would otherwise sit in a house-and-land
cohort and produce "you can find comparable stock cheaper here", linking a buyer to dirt.

Our own medians are 15% apart (packages $847,195, land $719,990), so a land price in a
package cohort does not look wrong on inspection. It has to be impossible instead.
"""

from price_kind import (BUILD_ONLY, DWELLING, LAND_ONLY, PACKAGE, UNKNOWN,
                        derive, is_comparable)


def test_the_source_saying_land_only_beats_everything_else():
    """A land listing carries a land price legitimately — the component IS the product.

    verify_against_source.py already had to learn this: it flagged "LAND ONLY ...
    $918,000" as understated and would have inflated a correct listing to a package
    price. The vendor's own words settle it.
    """
    kind, why = derive({"product_type": "House & Land",
                        "source_text": "Available 23 LAND ONLY 9 318.6 Registered Land $918,000"})
    assert kind == LAND_ONLY, (kind, why)
    assert "land only" in why

    build, _ = derive({"source_text": "PR8831 8 Heathwood The Crest Estate 400 Build Only $390,639"})
    assert build == BUILD_ONLY


def test_a_row_can_prove_what_its_own_price_covers():
    """382 live rows have no product_type but state land + build adding to their price.

    That is the source describing its own price, not us inferring one from the number —
    which is the reasoning that caused the original bug and stays banned.
    """
    kind, why = derive({"product_type": "", "land_price": 665_000,
                        "build_price": 786_000, "price": 1_451_000})
    assert kind == PACKAGE, (kind, why)
    assert "land + build" in why

    # Components that do NOT add up prove nothing, so the row stays unknown.
    assert derive({"product_type": "", "land_price": 665_000,
                   "build_price": 786_000, "price": 786_000})[0] == UNKNOWN


def test_a_dwelling_without_proof_of_land_stays_unknown():
    """170 live rows record a house area and bedrooms and nothing else.

    Those prove a dwelling exists. They do not prove the price covers the land under it,
    which is the only question here — so they get no verdict rather than a guessed one.
    """
    kind, _ = derive({"product_type": "", "house_sqm": 210.0, "bedrooms": 4,
                      "price": 850_000})
    assert kind == UNKNOWN


def test_a_cohort_never_mixes_kinds():
    assert is_comparable(PACKAGE, PACKAGE)
    assert is_comparable(LAND_ONLY, LAND_ONLY)
    assert is_comparable(DWELLING, DWELLING)

    # The failure this whole module exists to prevent, in both directions.
    assert not is_comparable(PACKAGE, LAND_ONLY), "a block of dirt is not a comparable house"
    assert not is_comparable(LAND_ONLY, PACKAGE)
    assert not is_comparable(PACKAGE, DWELLING), "a strata unit is not a house-and-land package"

    # Two prices that each cover something unrecorded are not covering the same thing.
    assert not is_comparable(UNKNOWN, UNKNOWN)
    # A build contract is not benchmarkable at all — there is no portal comparable for it.
    assert not is_comparable(BUILD_ONLY, BUILD_ONLY)


def test_every_live_row_gets_a_kind_and_a_reason():
    """No row may fall through without a recorded justification — the verdict on a
    client's screen has to be traceable to the evidence behind it."""
    for row in ({"product_type": "Apartment"},
                {"product_type": "Land"},
                {"product_type": None, "source_text": ""},
                {}):
        kind, why = derive(row)
        assert kind, row
        assert why and isinstance(why, str), row


def run_all():
    tests = [
        ("the source's own words win", test_the_source_saying_land_only_beats_everything_else),
        ("a row can prove its price cover", test_a_row_can_prove_what_its_own_price_covers),
        ("a dwelling without land proof is unknown", test_a_dwelling_without_proof_of_land_stays_unknown),
        ("a cohort never mixes kinds", test_a_cohort_never_mixes_kinds),
        ("every row gets a kind and a reason", test_every_live_row_gets_a_kind_and_a_reason),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] price-kind: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] price-kind: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
