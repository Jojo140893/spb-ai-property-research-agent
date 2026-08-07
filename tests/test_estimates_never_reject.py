"""
An ESTIMATE may inform a ranking. Only EVIDENCE may reject.

Every package this system holds was being charged an identical, invented $15,000
site-cost allowance, because no stocklist we read records inclusions at all — so the
"are site costs fixed?" test failed for 100% of them and the same constant was added to
every price. That number was then used as the budget filter, so a $900,000 package on a
$900,000 budget was discarded as "$915,000, over by $15,000" — a figure no builder ever
quoted, applied to a lot the client can afford.

It is the same error as pricing a package off its land component and the same error as
inventing a 3-bedroom minimum: acting on a number nobody published. The rule these tests
hold in place:

  * the ADVERTISED price — what the builder actually put in writing — decides whether a
    lot is in budget,
  * the allowance still costs points and is stated as a caveat, because unconfirmed site
    costs are a real thing for a buyer to know,
  * a source that says NOTHING about inclusions is reported as unclear, never as a
    positive claim about what the package contains.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schema import (CandidateProperty, ClientBrief, BuyerType,  # noqa: E402
                    PriceBreakdown, TurnkeyStatus)
from scoring_engine import ScoringEngine                        # noqa: E402
from turnkey_calculator import TurnkeyCalculator                # noqa: E402


def _brief(budget=900_000.0, cap=None):
    return ClientBrief(
        client_name="T", budget_max=budget,
        preferred_spending_cap=budget if cap is None else cap,
        deposit_amount=50_000.0, finance_status="Approved",
        buyer_type=BuyerType.OWNER_OCCUPIER, state="QLD", primary_suburbs=["Coomera"],
        bedrooms_min=0, bathrooms_min=0, car_spaces_min=0, storeys_max=None,
        land_size_min_sqm=0.0, house_size_min_sqm=0.0)


def _prop(advertised, allowance=15_000.0):
    pb = PriceBreakdown(
        advertised_package_price=advertised, land_price=0.0, build_price=0.0,
        fixed_site_costs=0.0, driveway_cost=0.0, fencing_cost=0.0,
        landscaping_cost=0.0, flooring_cost=0.0, blinds_cost=0.0, hvac_cost=0.0,
        estimated_additional_costs=allowance,
        realistic_total_price=advertised + allowance,
        turnkey_status=TurnkeyStatus.UNCLEAR)
    return CandidateProperty(
        property_id="P1", lot_address="Lot 1", suburb="Coomera", state="QLD",
        builder_name="Avia Homes", developer_name="Dev", house_design="D",
        bedrooms=4, bathrooms=2, car_spaces=2, storeys=1,
        land_size_sqm=400.0, house_size_sqm=200.0, title_status="Titled",
        expected_title_date="Ready", price_breakdown=pb,
        estimated_rent_weekly_min=600, estimated_rent_weekly_max=650,
        amenities_summary="", builder_confidence_rating="HIGH",
        source_channel="E-Agent", source_url_or_ref="http://x",
        date_checked="07/08/2026")


def test_an_invented_allowance_never_rejects_an_affordable_lot():
    """The actual bug: advertised AT the budget, rejected by our own $15,000."""
    result = ScoringEngine.evaluate_property(_brief(900_000), _prop(900_000))
    assert not result.hard_rejection, result.rejection_reason
    assert "915,000" not in result.rejection_reason, result.rejection_reason


def test_the_allowance_is_still_disclosed_rather_than_quietly_dropped():
    """Not rejecting is not the same as not telling anyone."""
    result = ScoringEngine.evaluate_property(_brief(900_000), _prop(900_000))
    assert "15,000" in result.advisories, result.advisories
    assert "estimated" in result.advisories.lower(), result.advisories
    assert "Confirm site costs" in result.advisories, result.advisories


def test_a_genuinely_over_budget_lot_is_still_rejected():
    """The filter must still filter — on the builder's own number."""
    result = ScoringEngine.evaluate_property(_brief(900_000), _prop(950_000))
    assert result.hard_rejection
    assert "950,000" in result.rejection_reason, result.rejection_reason
    assert "Advertised" in result.rejection_reason, result.rejection_reason


def test_an_equal_budget_and_cap_does_not_divide_by_zero():
    """Newly reachable: a lot at the cap with an allowance on top now survives the
    rejection branch and lands in the grading band, which is zero-wide here."""
    result = ScoringEngine.evaluate_property(_brief(900_000, cap=900_000),
                                             _prop(900_000))
    assert 0.0 <= result.budget_fit <= 20.0, result.budget_fit


def test_a_lot_under_the_cap_still_scores_full_budget_points():
    result = ScoringEngine.evaluate_property(_brief(900_000, cap=800_000),
                                             _prop(700_000))
    assert result.budget_fit == 20.0, result.budget_fit
    assert not result.hard_rejection


def test_a_source_that_states_nothing_is_reported_unclear_not_partial_turnkey():
    """"Partial Turnkey" asserts what the package contains. The source said nothing."""
    pb = TurnkeyCalculator.calculate_price_breakdown(
        {"advertised_package_price": 700_000, "land_price": 300_000,
         "build_price": 400_000})
    assert pb.turnkey_status == TurnkeyStatus.UNCLEAR, pb.turnkey_status
    assert pb.estimated_additional_costs == 15_000.0
    assert any("not stated" in m for m in pb.missing_inclusions), pb.missing_inclusions
    assert any("ESTIMATE" in m for m in pb.missing_inclusions), pb.missing_inclusions


def test_a_source_that_does_state_its_inclusions_is_read_not_second_guessed():
    """The honest-source path must be unchanged — this is not a blanket downgrade."""
    pb = TurnkeyCalculator.calculate_price_breakdown(
        {"advertised_package_price": 700_000,
         "inclusions": {"site_costs_fixed": True, "site_costs_val": 22_000}})
    assert pb.turnkey_status == TurnkeyStatus.FULL_TURNKEY, pb.turnkey_status
    assert pb.estimated_additional_costs == 0.0
    assert pb.realistic_total_price == 700_000

    excluded = TurnkeyCalculator.calculate_price_breakdown(
        {"advertised_package_price": 700_000,
         "inclusions": {"site_costs_fixed": True, "site_costs_val": 22_000,
                        "fencing_included": False}})
    assert excluded.estimated_additional_costs == 4_000.0
    assert excluded.turnkey_status == TurnkeyStatus.PARTIAL_TURNKEY


def run_all():
    tests = [
        ("an invented allowance never rejects", test_an_invented_allowance_never_rejects_an_affordable_lot),
        ("the allowance is still disclosed", test_the_allowance_is_still_disclosed_rather_than_quietly_dropped),
        ("a genuinely over-budget lot is rejected", test_a_genuinely_over_budget_lot_is_still_rejected),
        ("equal budget and cap does not divide by zero", test_an_equal_budget_and_cap_does_not_divide_by_zero),
        ("under the cap still scores full points", test_a_lot_under_the_cap_still_scores_full_budget_points),
        ("silent source is unclear, not partial", test_a_source_that_states_nothing_is_reported_unclear_not_partial_turnkey),
        ("a stated inclusion is read as stated", test_a_source_that_does_state_its_inclusions_is_read_not_second_guessed),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] estimates: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] estimates: {name}: {e}")
    return failed


if __name__ == "__main__":
    sys.exit(1 if run_all() else 0)
