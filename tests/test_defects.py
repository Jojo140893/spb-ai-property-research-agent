"""
Standard assertion test suite verifying remediation of all Phase 0 defects.
Runs zero-dependency with standard Python.
"""

from pathlib import Path
from schema import (
    ClientBrief, CandidateProperty, VerificationStatus,
    RecommendationStatus, BuyerType, PriceBreakdown, TurnkeyStatus
)
from builder_registry import BuilderRegistry
from scoring_engine import ScoringEngine, BuilderConfidenceModel
import config


def test_defect_1_builder_confidence():
    """
    Defect #1 Fix Verification: Approved CSV builders score HIGH (10 pts) or MEDIUM (7 pts),
    not Low (4 pts).
    """
    registry = BuilderRegistry()
    avia_info = registry.search_builder_by_name("Avia Homes")
    assert avia_info is not None, "Avia Homes must exist in primary builder list"

    rating, score, reason = BuilderConfidenceModel.evaluate_builder("Avia Homes", avia_info)
    assert rating in ["HIGH", "MEDIUM"], f"Expected HIGH or MEDIUM, got {rating}"
    assert score >= 7.0, f"Expected score >= 7.0, got {score}"
    assert "Avia Homes" in reason


def test_defect_2_csv_parsing():
    """
    Defect #2 Fix Verification: Parser reads primary builder section (lines 1-52 only),
    isolates contact email vs portal login email, and normalizes phone numbers.
    """
    registry = BuilderRegistry()
    builders = registry.get_all_builders()

    assert 25 <= len(builders) <= 40, f"Expected 25-40 primary builders, got {len(builders)}"

    for b in builders:
        assert "LinkedIn Link" not in b['notes']
        assert b['builder_name'] != "BUILDERS IN PERTH"

    avia = registry.search_builder_by_name("Avia Homes")
    assert avia['contact_email'] == "alex@aviahomes.com.au"
    assert avia['contact_phone'] == "0400027420"
    assert avia['is_on_e_agent'] is True


def test_defect_3_config_paths():
    """
    Defect #3 Fix Verification: Config uses relative Path objects without hardcoded d:/kommo paths.
    """
    assert isinstance(config.PROJECT_ROOT, Path)
    assert config.BUILDER_CSV_PATH.exists()
    assert config.OUTPUT_DIR.exists()


def test_defect_4_house_size_minimum():
    """
    Defect #4 Fix Verification: Hard rejection triggered when house_size_sqm < house_size_min_sqm.
    """
    brief = ClientBrief(
        client_name="Test Client",
        budget_max=800000.0,
        preferred_spending_cap=750000.0,
        deposit_amount=50000.0,
        finance_status="Approved",
        buyer_type=BuyerType.OWNER_OCCUPIER,
        state="QLD",
        primary_suburbs=["Coomera"],
        bedrooms_min=4,
        bathrooms_min=2,
        car_spaces_min=2,
        storeys_max=1,
        land_size_min_sqm=350.0,
        house_size_min_sqm=190.0,  # Mandatory 190 m² minimum
    )

    pb = PriceBreakdown(
        advertised_package_price=700000, land_price=300000, build_price=400000,
        fixed_site_costs=15000, driveway_cost=0, fencing_cost=0, landscaping_cost=0,
        flooring_cost=0, blinds_cost=0, hvac_cost=0, estimated_additional_costs=0,
        realistic_total_price=700000, turnkey_status=TurnkeyStatus.FULL_TURNKEY
    )

    small_house_prop = CandidateProperty(
        property_id="PROP-SMALL",
        lot_address="Lot 1 Small House",
        suburb="Coomera",
        state="QLD",
        builder_name="Avia Homes",
        developer_name="Dev",
        house_design="Design 170",
        bedrooms=4,
        bathrooms=2,
        car_spaces=2,
        storeys=1,
        land_size_sqm=400.0,
        house_size_sqm=170.0,  # 170 m² < 190 m² mandatory minimum -> HARD REJECTION
        title_status="Titled",
        expected_title_date="Ready",
        price_breakdown=pb,
        estimated_rent_weekly_min=600,
        estimated_rent_weekly_max=650,
        amenities_summary="Close to shops",
        builder_confidence_rating="HIGH",
        source_channel="E-Agent",
        source_url_or_ref="http://portal.com",
        date_checked="22/07/2026",
        verification_status=VerificationStatus.VERIFIED
    )

    scoring = ScoringEngine.evaluate_property(brief, small_house_prop)
    assert scoring.hard_rejection is True
    assert "House size (170 m²) below minimum mandatory requirement (190 m²)" in scoring.rejection_reason


def test_defect_5_verification_defaults():
    """
    Defect #5 Fix Verification: Default verification status is PENDING,
    and unapproved pending items are assigned HOLD recommendation status.
    """
    pb = PriceBreakdown(
        advertised_package_price=700000, land_price=300000, build_price=400000,
        fixed_site_costs=15000, driveway_cost=0, fencing_cost=0, landscaping_cost=0,
        flooring_cost=0, blinds_cost=0, hvac_cost=0, estimated_additional_costs=0,
        realistic_total_price=700000, turnkey_status=TurnkeyStatus.FULL_TURNKEY
    )

    pending_prop = CandidateProperty(
        property_id="PROP-PENDING",
        lot_address="Lot 2 Pending Verification",
        suburb="Coomera",
        state="QLD",
        builder_name="Avia Homes",
        developer_name="Dev",
        house_design="Aura 185",
        bedrooms=4,
        bathrooms=2,
        car_spaces=2,
        storeys=1,
        land_size_sqm=400.0,
        house_size_sqm=185.0,
        title_status="Titled",
        expected_title_date="Ready",
        price_breakdown=pb,
        estimated_rent_weekly_min=600,
        estimated_rent_weekly_max=650,
        amenities_summary="Close to shops",
        builder_confidence_rating="HIGH",
        source_channel="E-Agent",
        source_url_or_ref="http://portal.com",
        date_checked="22/07/2026"
    )

    assert pending_prop.verification_status == VerificationStatus.PENDING

    rec, reason = ScoringEngine.assign_recommendation(pending_prop)
    assert rec == RecommendationStatus.HOLD
    assert "Pending Confirmation" in reason


def test_a_stated_land_minimum_actually_rejects():
    """Coleen types a number into "Min Land Size (m2)" and it was silently ignored.

    House size hard-rejects below its minimum; land size only ever deducted up to 5
    points and rejected nothing, so a buyer asking for 800 m2 was shown 296 m2 lots as a
    "Strong match". A stated minimum the tool declines to apply is worse than no field.

    A land size of 0 means the stocklist never said (api/_candidates passes a missing
    size through as 0.0, and a 0 m2 lot does not exist), so it raises an advisory rather
    than a rejection -- the same choice house size already makes when it is missing.
    """
    from scoring_engine import ScoringEngine

    def _prop(pid, land):
        pb = PriceBreakdown(
            advertised_package_price=700000, land_price=300000, build_price=400000,
            fixed_site_costs=0, driveway_cost=0, fencing_cost=0, landscaping_cost=0,
            flooring_cost=0, blinds_cost=0, hvac_cost=0, estimated_additional_costs=0,
            realistic_total_price=700000, turnkey_status=TurnkeyStatus.FULL_TURNKEY)
        return CandidateProperty(
            property_id=pid, lot_address=f"Lot {pid}", suburb="Coomera", state="QLD",
            builder_name="Avia Homes", developer_name="Dev", house_design="D",
            bedrooms=4, bathrooms=2, car_spaces=2, storeys=1,
            land_size_sqm=land, house_size_sqm=200.0, title_status="Titled",
            expected_title_date="Ready", price_breakdown=pb,
            estimated_rent_weekly_min=600, estimated_rent_weekly_max=650,
            amenities_summary="", builder_confidence_rating="HIGH",
            source_channel="E-Agent", source_url_or_ref="http://x", date_checked="06/08/2026")

    brief = ClientBrief(
        client_name="T", budget_max=800000.0, preferred_spending_cap=800000.0,
        deposit_amount=50000.0, finance_status="Approved",
        buyer_type=BuyerType.OWNER_OCCUPIER, state="QLD", primary_suburbs=["Coomera"],
        bedrooms_min=4, bathrooms_min=2, car_spaces_min=2, storeys_max=1,
        land_size_min_sqm=800.0, house_size_min_sqm=150.0)

    small = ScoringEngine.evaluate_property(brief, _prop("SMALL", 296.0))
    assert small.hard_rejection, "296 m2 must not pass an 800 m2 minimum"
    assert "Land size" in small.rejection_reason, small.rejection_reason

    big = ScoringEngine.evaluate_property(brief, _prop("BIG", 850.0))
    assert not big.hard_rejection, "850 m2 satisfies the minimum"

    unstated = ScoringEngine.evaluate_property(brief, _prop("UNSTATED", 0.0))
    assert not unstated.hard_rejection, "an unrecorded size is not a small size"
    assert "Land size not stated" in unstated.advisories, unstated.advisories
