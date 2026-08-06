"""
Regression tests for the price/date bugs found in the CSV Coleen reviewed.

All three fixtures are real rows from the live database. Each one was producing a
wrong number that would have gone in front of a buyer.
"""

from sources.adaptive_extract import (PRICE_RE, TITLE_RE, _money,
                                      _ordered_package_prices, parse_fields,
                                      parse_price)

# Real row. Column order: code | land m2 | title | house m2 | bed+bath+car | design |
# storey | WEEKLY RENT | ANNUAL RENT | yield | title | land $ | build $ | total $
QLD_DUAL = ("CC-0122 417 Jul-26 173 5 + 5 + 3 FLINDERS SINGLE $ 2,330 $ 121,160 "
            "8.03% TBC $ 850,000 $ 659,400 $ 1,509,400")
VIC_REGIONAL = ("Lot 82 Aberdeen 282 142.8 12.0 Sep-26 Empley 15 3x2x2 "
                "$205,000 $335,220 $540,220 Available")


def test_rent_and_yield_are_not_treated_as_package_prices():
    """Was storing the $121,160 ANNUAL RENT as the land price of an $850,000 lot."""
    ordered = _ordered_package_prices(QLD_DUAL)
    assert 121_160 not in ordered, "annual rent leaked into the price list"
    assert 2_330 not in ordered, "weekly rent leaked into the price list"
    assert ordered == [850_000.0, 659_400.0, 1_509_400.0], ordered


def test_qld_row_splits_land_and_build_correctly():
    f = parse_fields(QLD_DUAL)
    assert f["advertised_package_price"] == 1_509_400
    assert f["land_price"] == 850_000, f"land price wrong: {f['land_price']}"
    assert f["build_price"] == 659_400, f"build price wrong: {f['build_price']}"
    # the split must add up to the package
    assert f["land_price"] + f["build_price"] == f["advertised_package_price"]


def test_vic_row_still_splits_correctly():
    """Guard against fixing QLD by breaking VIC."""
    f = parse_fields(VIC_REGIONAL)
    assert f["advertised_package_price"] == 540_220
    assert f["land_price"] == 205_000
    assert f["build_price"] == 335_220


def test_price_with_space_inside_the_number():
    """pdfplumber emits "$ 1 ,757,400"; this row's package read as $1,035,000.

    The repair now happens in normalise_money_spacing rather than by letting PRICE_RE
    accept a space as a thousands separator. That tolerance also glued two adjacent
    spreadsheet columns into one 9-digit number (see the test below), which cost 212
    rows their package price — so the split is rejoined first and PRICE_RE stays strict.
    """
    from sources.scraper_base import normalise_money_spacing

    joined = normalise_money_spacing("$ 1 ,757,400")
    assert [_money(m) for m in PRICE_RE.findall(joined)] == [1_757_400.0]
    # The older split shape, on the other side of the comma, still works.
    assert [_money(m) for m in
            PRICE_RE.findall(normalise_money_spacing("$ 9 32,900"))] == [932_900.0]
    assert _money("$1,035,000".lstrip("$")) == 1_035_000.0
    # Callers that hand parse_price raw text must get the same repair.
    assert parse_price("$ 1 ,757,400") == 1_757_400.0


def test_two_columns_are_never_glued_into_one_number():
    """The defect that understated 212 live rows by a median of $424,900.

    A stocklist row runs "... 450.1 $896,000 202.15 $550,411 $1,446,411 900 Due ...":
    land, house size, build, TOTAL, then a weekly rent. When a space counted as a
    thousands separator, "$896,000 202" and "$1,446,411 900" became 896000202 and
    1446411900, both blew past the $5M ceiling and were discarded, and max() of what
    was left published the BUILD component of $550,411 as the package price.
    """
    from sources.scraper_base import normalise_money_spacing

    row = ("Rooty Hill Available 6 Gardner Road Riverton MOD Coastal 4 2 2 450.1 "
           "$896,000 202.15 $550,411 $1,446,411 900 Due Oct 26")
    found = [_money(m) for m in PRICE_RE.findall(normalise_money_spacing(row))]
    assert found == [896_000.0, 550_411.0, 1_446_411.0], found
    assert parse_fields(row)["advertised_package_price"] == 1_446_411.0, (
        "the stated total must win, not the largest surviving component")


def test_title_dates_that_previously_never_matched():
    for text, expected in (("Q1-2026", "Q1-2026"), ("Sep-26", "Sep-26"),
                           ("Q2 2026", "Q2 2026"), ("Nov-27", "Nov-27"),
                           ("Titled", "Titled"), ("Registered", "Registered"),
                           ("TBC", "TBC")):
        m = TITLE_RE.search(text)
        assert m and m.group(1) == expected, f"{text!r} -> {m.group(1) if m else None}"


def test_title_status_populated_on_real_rows():
    assert parse_fields(VIC_REGIONAL)["title_status"] == "Sep-26"
    assert parse_fields(QLD_DUAL)["title_status"] is not None


def test_rent_words_must_be_bounded_not_matched_inside_words():
    """The rent filter's "pa" abbreviation was unbounded, so it matched inside
    "2-part Contract", "Package Price" and "Park" — and every price on such a row was
    discarded as rental income. Five builders' entire stocklists were empty because of
    it (FRD, Hudson QLD, Hudson NSW, Alete, Land Build Direct).

    Real row from the Land Build Direct PDF:
    """
    row = ("Available Lot 404 House ZARA - 23 NORTH 136.0m2 300.0 m2 "
           "4 beds / 2 baths / 1 car 2-part Contract $779,613 Portal Link")
    f = parse_fields(row)
    assert f["advertised_package_price"] == 779_613, \
        f"'2-part Contract' still suppresses the price: {f['advertised_package_price']}"
    for phrase in ("Package Price $650,000", "12 Park Street $650,000",
                   "Comparable $650,000", "Departure $650,000"):
        assert parse_fields(phrase)["advertised_package_price"] == 650_000, phrase
    # ...while genuine rent figures are still excluded
    assert 2_330 not in _ordered_package_prices("$ 2,330 pw $ 121,160 pa $ 850,000")
    assert 121_160 not in _ordered_package_prices("$ 2,330 pw $ 121,160 pa $ 850,000")


def test_mixed_money_rows_are_flagged_low_confidence():
    """A row where rent, yield and price are jumbled must not look authoritative."""
    f = parse_fields("Lot 9 $1,500 pw $78,000 pa 7.78% $960,000")
    assert f.get("price_confidence", 1.0) <= 0.6, f.get("price_confidence")


def run_all():
    tests = [
        ("rent/yield excluded from prices", test_rent_and_yield_are_not_treated_as_package_prices),
        ("QLD land/build split correct", test_qld_row_splits_land_and_build_correctly),
        ("VIC split still correct", test_vic_row_still_splits_correctly),
        ("price with internal space", test_price_with_space_inside_the_number),
        ("two columns are never glued", test_two_columns_are_never_glued_into_one_number),
        ("title dates now match", test_title_dates_that_previously_never_matched),
        ("title_status on real rows", test_title_status_populated_on_real_rows),
        ("rent words are word-bounded", test_rent_words_must_be_bounded_not_matched_inside_words),
        ("mixed-money rows flagged", test_mixed_money_rows_are_flagged_low_confidence),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] prices: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] prices: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
