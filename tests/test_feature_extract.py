"""
Tests for sources/feature_extract.py — the fields Coleen asked for on 28/29 July.

Every fixture string below is a REAL row taken from the live database, so these
tests pin behaviour against the data the client actually reviewed.
"""

from sources.feature_extract import (parse_availability, parse_estate, parse_frontage,
                                     parse_incentive, parse_listing_features,
                                     parse_lot_number, parse_postcode, parse_storey)

# Real rows from spb_research_audit.db
VIC_REGIONAL = ("Lot 82 Aberdeen 282 142.8 12.0 Sep-26 Empley 15 3x2x2 "
                "$205,000 $335,220 $540,220 Available")
QLD_DUAL = ("CC-0122 417 Jul-26 173 5 + 5 + 3 FLINDERS SINGLE $ 2,330 $ 121,160 "
            "8.03% TBC $ 850,000 $ 659,400 $ 1,509,400")
NSW_EMAIL = ("Available 25/03/2026 Denman - Highfields Estate 105 Almond Street "
             "Denman 2328 MUSWELLBROOK NSW")
HERMITAGE = "LOT 5532 VERDAY BLVD, TARNEIT - 10% DEPOSIT"
PARAMOUNT = "Single Contract – Fraser Rise $660,000"


def test_availability():
    assert parse_availability(VIC_REGIONAL) == "Available"
    assert parse_availability(NSW_EMAIL) == "Available"
    assert parse_availability("On hold 10/03/2026 Denman") == "On Hold"
    assert parse_availability("Under offer 23/04/2026 152-156 Bancroft") == "Under Offer"
    assert parse_availability("Lot 5 SOLD") == "Sold"
    assert parse_availability("Not available this release") == "Not Available"
    # negated form must not read as plain "Available"
    assert parse_availability("unavailable") == "Not Available"
    assert parse_availability("Lot 12 Rosewood $620,000") is None


def test_storey_reads_bare_tokens_from_real_stocklists():
    assert parse_storey(QLD_DUAL) == "SINGLE"
    assert parse_storey("GLEBE DOUBLE 5 + 5 + 2 $1,035,000") == "DOUBLE"
    assert parse_storey("Aurora 24 double storey") == "DOUBLE"
    assert parse_storey("single-storey home") == "SINGLE"
    assert parse_storey("2 storey townhome") == "DOUBLE"


def test_storey_rejects_false_friends():
    """The critical one: Paramount labels every package 'Single Contract'."""
    assert parse_storey(PARAMOUNT) is None
    assert parse_storey("ONE PART CONTRACT") is None
    assert parse_storey("Split Contract - To be built") is None
    assert parse_storey("Double lock-up garage") is None
    assert parse_storey("double glazed windows") is None
    assert parse_storey("Dual Key single title") is None


def test_lot_number_handles_lots_and_stock_codes():
    assert parse_lot_number(VIC_REGIONAL) == "82"
    assert parse_lot_number(QLD_DUAL) == "CC-0122"      # stock code, not "Lot N"
    assert parse_lot_number(HERMITAGE) == "5532"
    assert parse_lot_number("LOT 1329 ONE PART CONTRACT, WALLAN") == "1329"
    assert parse_lot_number("Unit B109/143 South Street") is None


def test_postcode_excludes_lookalikes():
    assert parse_postcode(NSW_EMAIL) == "2328"           # not 2026 from the date
    assert parse_postcode("LOT 79 STELLA ST, COLAC 3250") == "3250"
    assert parse_postcode(HERMITAGE) is None             # 5532 is the lot, not a postcode
    assert parse_postcode(VIC_REGIONAL) is None          # no postcode present
    assert parse_postcode("Lot 9 Darwin NT 0800") == "0800"   # leading zero preserved
    assert parse_postcode("Q1-2026 titled 400 m2 Craigieburn 3064") == "3064"


def test_estate():
    assert parse_estate(NSW_EMAIL) == "Highfields"
    assert parse_estate("Lot 12 Rosewood Estate $620,000") == "Rosewood"
    # from a banner/context row
    assert parse_estate("Lot 318", context="ʊ  Atticus - Woodstock - VIC - Terrace") == "Atticus"


def test_frontage_only_when_unambiguous():
    assert parse_frontage("Frontage (m) 12.5") == 12.5
    assert parse_frontage("10.5m frontage") == 10.5
    # a bare decimal in a flattened row is NOT guessed at
    assert parse_frontage(VIC_REGIONAL) is None


def test_incentive_captures_rebates():
    r = parse_incentive("Lot 12 Rosewood Estate $620,000 includes $15,000 settlement rebate")
    assert r["incentive_amount"] == 15000
    assert "rebate" in r["incentive_text"].lower()
    assert parse_incentive("$7,500 cashback on titled lots")["incentive_amount"] == 7500
    assert parse_incentive("bonus $30,000 towards upgrades")["incentive_amount"] == 30000
    assert parse_incentive("$15k builder bonus")["incentive_amount"] == 15000


def test_incentive_rejects_rent_and_yield():
    """The QLD dual-occ rows carry weekly rent, annual rent and a yield — none are incentives."""
    r = parse_incentive(QLD_DUAL, package_price=1_509_400)
    assert r["incentive_amount"] is None, f"phantom incentive from rent/yield: {r}"
    assert parse_incentive("$2,330 per week rent")["incentive_amount"] is None
    assert parse_incentive("rental yield 8.03% on $121,160 pa")["incentive_amount"] is None
    # a figure too large relative to the package is not a rebate
    assert parse_incentive("saving $140,000", package_price=400_000)["incentive_amount"] is None


def test_incentive_absent_from_plain_rows():
    for t in (VIC_REGIONAL, NSW_EMAIL, HERMITAGE, PARAMOUNT):
        assert parse_incentive(t)["incentive_amount"] is None


def test_combined_pass():
    f = parse_listing_features(VIC_REGIONAL)
    assert f["availability_status"] == "Available"
    assert f["lot_number"] == "82"
    assert f["incentive_amount"] is None
    f2 = parse_listing_features(QLD_DUAL, package_price=1_509_400)
    assert f2["storey"] == "SINGLE" and f2["lot_number"] == "CC-0122"


def run_all():
    tests = [
        ("availability", test_availability),
        ("storey from real stocklists", test_storey_reads_bare_tokens_from_real_stocklists),
        ("storey rejects false friends", test_storey_rejects_false_friends),
        ("lot number + stock codes", test_lot_number_handles_lots_and_stock_codes),
        ("postcode excludes lookalikes", test_postcode_excludes_lookalikes),
        ("estate", test_estate),
        ("frontage only when unambiguous", test_frontage_only_when_unambiguous),
        ("incentive captures rebates", test_incentive_captures_rebates),
        ("incentive rejects rent/yield", test_incentive_rejects_rent_and_yield),
        ("incentive absent from plain rows", test_incentive_absent_from_plain_rows),
        ("combined pass", test_combined_pass),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] features: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] features: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
