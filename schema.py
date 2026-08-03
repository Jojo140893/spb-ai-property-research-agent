"""
Data models and schemas for Smart Property Buying (SPB) AI Property Research Agent.
Aligned with SPB SOP v1.0.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from enum import Enum


class BuyerType(Enum):
    FIRST_HOME_BUYER = "First Home Buyer"
    OWNER_OCCUPIER = "Owner Occupier"
    INVESTOR = "Investor"
    SMSF = "SMSF Buyer"


class RequirementPriority(Enum):
    MANDATORY = "Mandatory"
    PREFERRED = "Preferred"
    OPTIONAL = "Optional"


class TurnkeyStatus(Enum):
    FULL_TURNKEY = "Full Turnkey"
    PARTIAL_TURNKEY = "Partial Turnkey"
    BASE_PACKAGE = "Base Package"
    UNCLEAR = "Unclear"


class VerificationStatus(Enum):
    VERIFIED = "Verified"
    PENDING = "Pending Confirmation"
    UNAVAILABLE = "Unavailable"


class RiskRating(Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class RecommendationStatus(Enum):
    RECOMMEND = "Recommend"
    RECOMMEND_WITH_CONDITIONS = "Recommend with Conditions"
    HOLD = "Hold"
    DO_NOT_RECOMMEND = "Do Not Recommend"


@dataclass
class ClientRequirement:
    field_name: str
    value: Any
    priority: RequirementPriority = RequirementPriority.MANDATORY


@dataclass
class ClientBrief:
    client_name: str
    budget_max: float
    preferred_spending_cap: float
    deposit_amount: float
    finance_status: str
    buyer_type: BuyerType
    state: str
    primary_suburbs: List[str]
    bedrooms_min: int
    bathrooms_min: int
    car_spaces_min: int
    storeys_max: int
    land_size_min_sqm: float
    house_size_min_sqm: float
    target_rent_weekly: Optional[float] = None
    target_gross_yield_pct: Optional[float] = None
    build_timeframe_months: Optional[int] = None
    search_radius_km: Optional[float] = None  # distance search: include suburbs within N km of primary suburbs
    additional_notes: str = ""


@dataclass
class PriceBreakdown:
    advertised_package_price: float
    land_price: float
    build_price: float
    fixed_site_costs: float
    driveway_cost: float
    fencing_cost: float
    landscaping_cost: float
    flooring_cost: float
    blinds_cost: float
    hvac_cost: float
    estimated_additional_costs: float
    realistic_total_price: float
    turnkey_status: TurnkeyStatus
    missing_inclusions: List[str] = field(default_factory=list)


@dataclass
class RiskItem:
    risk_name: str
    rating: RiskRating
    description: str
    proposed_mitigation: str


@dataclass
class ScoringBreakdown:
    budget_fit: float  # max 20
    requirement_match: float  # max 20
    value_competitiveness: float  # max 15
    location_amenity: float  # max 15
    builder_confidence: float  # max 10
    suitability_score: float  # max 10 (Rental/Lifestyle)
    risk_score: float  # max 10
    total_score: float  # max 100
    hard_rejection: bool = False
    # Only reasons that actually caused the rejection. An advisory that costs points
    # but does NOT reject belongs in `advisories`, not here — the two were pooled in
    # one string, so "Storeys not stated ... confirm with the builder" was printed on
    # every rejection card under a "Hard Rejection" heading as though it were a cause.
    # It never rejects anything: an unrecorded storey count was deliberately made a
    # flag rather than a failure, because rejecting on it made a single-storey brief
    # return nothing at all.
    rejection_reason: str = ""
    advisories: str = ""


@dataclass
class CandidateProperty:
    property_id: str
    lot_address: str
    suburb: str
    state: str
    builder_name: str
    developer_name: str
    house_design: str
    # None means "the stocklist does not say", for the same reason as storeys below: a
    # brief that sets no minimum for a field should not lose good stock just because the
    # field is blank, and it must not be back-filled with a plausible number either.
    # A row IS still excluded upstream when the brief states a minimum this would be
    # checked against — see _candidates.BRIEF_MINIMUM_FOR.
    bedrooms: Optional[int]
    bathrooms: Optional[int]
    car_spaces: Optional[int]
    # Optional because most stocklists do not record it: storey is stated on 149 of 4,192
    # harvested rows. As a plain int it defaulted to 1, which silently satisfied a
    # "single storey only" brief for every row that never said — so the candidate feed had
    # to drop those rows instead, and a single-storey brief returned nothing at all.
    # None means "not stated", and the scorer flags it rather than assuming either way.
    storeys: Optional[int]
    land_size_sqm: float
    house_size_sqm: Optional[float]
    title_status: str
    expected_title_date: str
    price_breakdown: PriceBreakdown
    estimated_rent_weekly_min: float
    estimated_rent_weekly_max: float
    amenities_summary: str
    builder_confidence_rating: str  # HIGH, MEDIUM, LOW
    source_channel: str
    source_url_or_ref: str
    date_checked: str
    verification_status: VerificationStatus = VerificationStatus.PENDING  # Defect #5 Fix: Defaults to PENDING
    consultant_approved: bool = False
    risks: List[RiskItem] = field(default_factory=list)
    scoring: Optional[ScoringBreakdown] = None
    recommendation: RecommendationStatus = RecommendationStatus.HOLD
    recommendation_reason: str = ""
    benchmark: Optional[Dict[str, Any]] = None  # SOP Step 7 market benchmark result
    distance_km_from_target: Optional[float] = None  # km from nearest primary suburb
