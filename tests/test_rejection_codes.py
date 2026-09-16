"""
Every hard rejection carries a structured code beside its sentence.

The Research tab can only say "8 over budget, 3 not in the suburbs you named" if it can
count rejections by cause. Parsing the sentences for that is fragile (the budget sentence
embeds dollar figures, the suburb sentence embeds the brief's own suburb list), so
ScoringEngine.evaluate_property records a code next to every rejection reason it appends,
in the same order.

Two rules these tests hold in place:

  * one gate, one code: a package failing exactly one gate carries exactly that code,
    and the number of codes always equals the number of '; '-separated reasons;
  * a high risk rejects nothing, so it is an advisory. It used to be appended to
    rejection_reason on a package that was NOT rejected, which left a reason with no
    code and a shortlisted lot whose rejection_reason was non-empty.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schema import (BuyerType, CandidateProperty, ClientBrief,  # noqa: E402
                    PriceBreakdown, RiskItem, RiskRating, TurnkeyStatus)
from scoring_engine import REJECTION_CODES, ScoringEngine        # noqa: E402


def _brief(**over):
    fields = dict(
        client_name="T", budget_max=900_000.0, preferred_spending_cap=900_000.0,
        deposit_amount=50_000.0, finance_status="Approved",
        buyer_type=BuyerType.OWNER_OCCUPIER, state="QLD", primary_suburbs=["Coomera"],
        bedrooms_min=3, bathrooms_min=2, car_spaces_min=1, storeys_max=None,
        land_size_min_sqm=0.0, house_size_min_sqm=0.0, search_radius_km=None)
    fields.update(over)
    return ClientBrief(**fields)


def _prop(price=850_000.0, **over):
    pb = PriceBreakdown(
        advertised_package_price=price, land_price=0.0, build_price=0.0,
        fixed_site_costs=0.0, driveway_cost=0.0, fencing_cost=0.0,
        landscaping_cost=0.0, flooring_cost=0.0, blinds_cost=0.0, hvac_cost=0.0,
        estimated_additional_costs=0.0, realistic_total_price=price,
        turnkey_status=TurnkeyStatus.UNCLEAR)
    fields = dict(
        property_id="P1", lot_address="Lot 1", suburb="Coomera", state="QLD",
        builder_name="Avia Homes", developer_name="Dev", house_design="D",
        bedrooms=4, bathrooms=2, car_spaces=2, storeys=1,
        land_size_sqm=400.0, house_size_sqm=200.0, title_status="Titled",
        expected_title_date="Ready", price_breakdown=pb,
        estimated_rent_weekly_min=600, estimated_rent_weekly_max=650,
        amenities_summary="", builder_confidence_rating="HIGH",
        source_channel="E-Agent", source_url_or_ref="http://x",
        date_checked="07/08/2026")
    fields.update(over)
    return CandidateProperty(**fields)


def _reasons(result):
    return [r for r in result.rejection_reason.split("; ") if r]


# One package per gate: (code, brief overrides, property overrides, price).
GATES = [
    ("over_budget", {}, {}, 950_000.0),
    ("bedrooms_below_min", {"bedrooms_min": 5}, {}, None),
    ("bathrooms_below_min", {"bathrooms_min": 3}, {}, None),
    ("car_spaces_below_min", {"car_spaces_min": 3}, {}, None),
    ("storeys_above_max", {"storeys_max": 1}, {"storeys": 2}, None),
    ("not_in_requested_suburbs", {"search_radius_km": 0}, {"suburb": "Pimpama"}, None),
    # Reachable only when every requested suburb is blank text: with a real suburb in
    # the brief, a blank listing suburb fails the "not one of the requested" test first.
    ("no_suburb", {"search_radius_km": 0, "primary_suburbs": ["  "]}, {"suburb": ""}, None),
    ("distance_unverified", {"search_radius_km": 20}, {"suburb": "Pimpama",
                                                       "distance_km_from_target": None}, None),
    ("outside_radius", {"search_radius_km": 20}, {"suburb": "Pimpama",
                                                  "distance_km_from_target": 31.5}, None),
    ("house_size_below_min", {"house_size_min_sqm": 250.0}, {}, None),
    ("land_size_below_min", {"land_size_min_sqm": 600.0}, {}, None),
]


def test_the_baseline_package_passes_every_gate():
    """Otherwise a per-gate fixture could carry codes from gates it was not built for."""
    result = ScoringEngine.evaluate_property(_brief(), _prop())
    assert not result.hard_rejection, result.rejection_reason
    assert result.rejection_codes == []
    assert result.rejection_reason == ""


def test_every_gate_has_a_fixture():
    assert [g[0] for g in GATES] == list(REJECTION_CODES)


@pytest.mark.parametrize("code,brief_over,prop_over,price", GATES, ids=[g[0] for g in GATES])
def test_one_gate_gives_exactly_its_code(code, brief_over, prop_over, price):
    prop = _prop(**prop_over) if price is None else _prop(price, **prop_over)
    result = ScoringEngine.evaluate_property(_brief(**brief_over), prop)
    assert result.hard_rejection, code
    assert result.rejection_codes == [code], (result.rejection_codes, result.rejection_reason)
    assert len(result.rejection_codes) == len(_reasons(result)), result.rejection_reason


def test_several_failed_gates_keep_codes_and_reasons_in_step():
    brief = _brief(bedrooms_min=5, bathrooms_min=3, house_size_min_sqm=250.0,
                   land_size_min_sqm=600.0, storeys_max=1, search_radius_km=0)
    prop = _prop(990_000.0, suburb="Pimpama", storeys=2, car_spaces=0)
    result = ScoringEngine.evaluate_property(brief, prop)
    assert result.rejection_codes == [
        "over_budget", "bedrooms_below_min", "bathrooms_below_min",
        "car_spaces_below_min", "storeys_above_max", "not_in_requested_suburbs",
        "house_size_below_min", "land_size_below_min"]
    reasons = _reasons(result)
    assert len(reasons) == len(result.rejection_codes)
    # Order matters: the i-th code names the i-th sentence.
    assert reasons[0].startswith("Advertised package price")
    assert reasons[5].startswith("Pimpama is not one of the requested suburbs")
    assert reasons[7].startswith("Land size")


def test_codes_are_a_fresh_list_per_result():
    """A shared default would let one package's codes leak into the next."""
    first = ScoringEngine.evaluate_property(_brief(), _prop(950_000.0))
    second = ScoringEngine.evaluate_property(_brief(), _prop())
    assert first.rejection_codes == ["over_budget"]
    assert second.rejection_codes == []


def _high_risk():
    return RiskItem(risk_name="Flood overlay", rating=RiskRating.HIGH,
                    description="Lot sits inside the 1% AEP flood extent",
                    proposed_mitigation="Check the council flood map")


def test_a_high_risk_that_rejects_nothing_is_an_advisory_not_a_reason():
    result = ScoringEngine.evaluate_property(_brief(), _prop(risks=[_high_risk()]))
    assert not result.hard_rejection
    assert result.rejection_codes == []
    assert result.rejection_reason == "", result.rejection_reason
    assert "High risk identified" not in result.rejection_reason
    assert "High risk identified: Flood overlay" in result.advisories, result.advisories
    # It still costs its points.
    assert result.risk_score == 5.0


def test_a_rejected_high_risk_package_keeps_its_reason_text_unchanged():
    result = ScoringEngine.evaluate_property(_brief(), _prop(950_000.0, risks=[_high_risk()]))
    assert result.hard_rejection
    assert result.rejection_codes == ["over_budget"]
    assert result.rejection_reason == (
        "Advertised package price ($950,000) exceeds max budget ($900,000) by $50,000")
    assert "High risk identified" not in result.advisories
