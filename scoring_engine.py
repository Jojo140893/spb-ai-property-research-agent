"""
100-Point Scoring Engine, Builder Confidence Model, & Risk Evaluator.
Implements SOP Step 11 (100-Point Scoring Matrix), Step 8 (Builder Quality), & Step 10 (Risk Checks).
Fixes Defect #1 (Builder Confidence Rating Model) & Defect #4 (House Size Minimum Check).
"""

from typing import Tuple, List, Dict, Any
from schema import (
    ClientBrief, CandidateProperty, ScoringBreakdown,
    RecommendationStatus, BuyerType, RiskRating, VerificationStatus, TurnkeyStatus
)


class BuilderConfidenceModel:
    """
    Defect #1 Fix: Evaluates builder confidence rating (HIGH, MEDIUM, LOW)
    based on portal verification, contract availability, E-Agent status, and delivery history.
    """
    @classmethod
    def evaluate_builder(cls, builder_name: str, registry_info: Dict[str, Any]) -> Tuple[str, float, str]:
        if not registry_info:
            # An UNIDENTIFIED builder cannot be a confident one.
            #
            # This returned MEDIUM / 7.0, three points ABOVE the 4.0 given to a builder
            # who IS in the registry but has no contract and no portal — so the absence
            # of information outscored known-bad information, and an anonymous listing
            # was ranked over a named one Coleen has actually assessed. Nothing about
            # "we have never heard of this builder" supports medium confidence.
            #
            # Scored below the lowest KNOWN rating rather than merely equal to it: a
            # registry builder awaiting review is at least identified, and a consultant
            # can chase it.
            return ("LOW", 3.0,
                    f"Builder '{builder_name}' is not in the SPB registry at all — "
                    f"identity and contract terms must be confirmed before this is "
                    f"presented to a client.")

        is_eagent = registry_info.get('is_on_e_agent', False)
        contract_avail = 'YES' in str(registry_info.get('contract_available', '')).upper()
        has_portal = bool(registry_info.get('portal_url'))

        if is_eagent or (contract_avail and has_portal):
            rating = "HIGH"
            score = 10.0
            reason = f"Approved builder '{builder_name}' with verified portal access & contract availability."
        elif contract_avail or has_portal or registry_info.get('stock_channel') == 'email_pdf':
            rating = "MEDIUM"
            score = 7.0
            reason = f"Approved builder '{builder_name}' with direct email/stocklist workflow."
        else:
            rating = "LOW"
            score = 4.0
            reason = f"Builder '{builder_name}' requires manual license & contract review."

        return rating, score, reason


class ScoringEngine:
    @classmethod
    def evaluate_property(cls, brief: ClientBrief, prop: CandidateProperty) -> ScoringBreakdown:
        # Kept apart on purpose. `rejection_reasons` is why a lot CANNOT be shown;
        # `advisories` is what a consultant should check before showing it. Pooling
        # them printed advisories on the rejection card as if they were causes.
        rejection_reasons: List[str] = []
        advisories: List[str] = []
        hard_rejection = False

        # --- 1. Budget Fit (Max 20 pts) ---
        realistic_price = prop.price_breakdown.realistic_total_price
        if realistic_price > brief.budget_max:
            hard_rejection = True
            over_amount = realistic_price - brief.budget_max
            rejection_reasons.append(f"Realistic price (${realistic_price:,.0f}) exceeds max budget (${brief.budget_max:,.0f}) by ${over_amount:,.0f}")
            budget_pts = 0.0
        elif realistic_price <= brief.preferred_spending_cap:
            budget_pts = 20.0
        else:
            ratio = (brief.budget_max - realistic_price) / (brief.budget_max - brief.preferred_spending_cap)
            budget_pts = 10.0 + (10.0 * max(0.0, min(1.0, ratio)))

        # --- 2. Requirement Match (Max 20 pts) ---
        req_pts = 20.0
        # A None here means the stocklist never stated it AND the brief set no minimum
        # that needs it — a row whose missing fact the client's own requirement turns on
        # never reaches the scorer (_candidates drops and names it). So "not stated" is
        # recorded as a gap to confirm, not treated as a pass or a failure.
        for value, minimum, label in ((prop.bedrooms, brief.bedrooms_min, "Bedroom count"),
                                      (prop.bathrooms, brief.bathrooms_min, "Bathroom count"),
                                      (prop.car_spaces, brief.car_spaces_min, "Car space count")):
            if value is None:
                advisories.append(f"{label} not stated in the builder's stocklist — "
                                  "confirm with the builder before presenting to the client")
                req_pts -= 1.0

        if prop.bedrooms is not None and prop.bedrooms < brief.bedrooms_min:
            hard_rejection = True
            rejection_reasons.append(f"Bedrooms ({prop.bedrooms}) below minimum mandatory requirement ({brief.bedrooms_min})")
            req_pts -= 10.0

        if prop.bathrooms is not None and prop.bathrooms < brief.bathrooms_min:
            hard_rejection = True
            rejection_reasons.append(f"Bathrooms ({prop.bathrooms}) below minimum mandatory requirement ({brief.bathrooms_min})")
            req_pts -= 5.0

        if prop.car_spaces is not None and prop.car_spaces < brief.car_spaces_min:
            hard_rejection = True
            rejection_reasons.append(f"Car spaces ({prop.car_spaces}) below minimum mandatory requirement ({brief.car_spaces_min})")
            req_pts -= 5.0

        if prop.storeys is None:
            # Not stated in the source — true of all but 149 of 4,192 harvested rows.
            # Neither rejected nor waved through. Rejecting meant a "single storey only"
            # brief returned NOTHING: every lot that satisfied it on every other count
            # failed to record its storeys. Assuming single storey would put a two-storey
            # house in front of a buyer who asked not to see one. So it is shortlisted with
            # the gap named and two points off, ranking below any lot that does state it.
            if brief.storeys_max is not None and brief.storeys_max < 2:
                advisories.append(
                    "Storeys not stated in the builder's stocklist — confirm with the "
                    "builder before presenting to the client")
                req_pts -= 2.0
        elif prop.storeys > brief.storeys_max:
            hard_rejection = True
            rejection_reasons.append(f"Storeys ({prop.storeys}) exceeds maximum allowed ({brief.storeys_max})")

        # Distance search is a mandatory boundary, not just a scoring nudge: a client
        # asking for "within N km" must not be shown stock beyond it (SOP Step 3
        # predefined rejection rules).
        if brief.search_radius_km:
            target = brief.primary_suburbs[0] if brief.primary_suburbs else 'target'
            if prop.distance_km_from_target is None:
                # Unknown distance must NOT be a free pass through a distance filter —
                # that let stock ~200 km away be shortlisted for a 40 km brief.
                hard_rejection = True
                rejection_reasons.append(
                    f"Location could not be verified (suburb not recognised), so the "
                    f"{brief.search_radius_km:.0f} km radius from {target} cannot be confirmed")
            elif prop.distance_km_from_target > brief.search_radius_km:
                hard_rejection = True
                rejection_reasons.append(
                    f"{prop.distance_km_from_target:.1f} km from {target} "
                    f"exceeds the {brief.search_radius_km:.0f} km search radius")

        # Defect #4 Fix: Mandatory House Size Minimum Check
        if prop.house_size_sqm is None:
            advisories.append("House size not stated in the builder's stocklist — "
                              "confirm with the builder before presenting to the client")
            req_pts -= 1.0
        elif prop.house_size_sqm < brief.house_size_min_sqm:
            hard_rejection = True
            rejection_reasons.append(f"House size ({prop.house_size_sqm:,.0f} m²) below minimum mandatory requirement ({brief.house_size_min_sqm:,.0f} m²)")
            req_pts -= 5.0

        # Land size is a MANDATORY minimum, exactly like house size above it.
        #
        # It used to cost at most 5 points and reject nothing, so a buyer who asked for
        # 800 m² was shown 296 m² lots as a "Strong match" — Coleen types the number into
        # "Min Land Size (m²)" on the Research tab and the system quietly ignored it.
        # A stated minimum the tool declines to apply is worse than no field at all.
        if brief.land_size_min_sqm:
            # 0 means "the stocklist never said", not "a lot with no land": a 0 m² lot
            # does not exist, and api/_candidates passes a missing size through as 0.0.
            # Treated as unstated so a data gap is flagged rather than silently rejected —
            # the same choice house size makes when it is missing.
            if not prop.land_size_sqm:
                advisories.append("Land size not stated in the builder's stocklist — "
                                  "confirm it meets the client's minimum before presenting")
                req_pts -= 1.0
            elif prop.land_size_sqm < brief.land_size_min_sqm:
                hard_rejection = True
                rejection_reasons.append(
                    f"Land size ({prop.land_size_sqm:,.0f} m²) below minimum mandatory "
                    f"requirement ({brief.land_size_min_sqm:,.0f} m²)")
                req_pts -= 5.0

        req_pts = max(0.0, req_pts)

        # --- 3. Value & Competitiveness (Max 15 pts) ---
        # SOP Step 7: driven by the market benchmark classification when available
        # (Below Market 15 / Competitive 12.5 / Slightly Above 10 / Poor 5, neutral
        # 7.5 when unbenchmarked), with a penalty for heavy missing inclusions.
        if prop.benchmark:
            value_pts = float(prop.benchmark.get('value_score_contribution', 7.5))
        else:
            value_pts = 7.5
        if prop.price_breakdown.estimated_additional_costs > 15000:
            value_pts -= 2.5
        value_pts = max(0.0, min(15.0, value_pts))

        # --- 4. Location & Amenity (Max 15 pts) ---
        # Distance search: properties in a primary suburb keep full points;
        # radius matches lose points with distance (-0.3/km, capped at -6).
        location_pts = 15.0
        in_primary = brief.primary_suburbs and prop.suburb.lower() in [s.lower() for s in brief.primary_suburbs]
        if not in_primary:
            dist = prop.distance_km_from_target
            if dist is not None:
                location_pts -= min(6.0, round(dist * 0.3, 1))
            else:
                location_pts -= 4.0
        location_pts = max(0.0, location_pts)

        # --- 5. Builder Confidence & Quality (Max 10 pts) --- Defect #1 Fix Applied
        builder_rating = prop.builder_confidence_rating.upper()
        if 'HIGH' in builder_rating:
            builder_pts = 10.0
        elif 'MEDIUM' in builder_rating:
            builder_pts = 7.0
        else:
            builder_pts = 4.0

        # --- 6. Rental / Lifestyle Fit (Max 10 pts) ---
        suitability_pts = 10.0
        if brief.buyer_type == BuyerType.INVESTOR and brief.target_gross_yield_pct:
            avg_rent = (prop.estimated_rent_weekly_min + prop.estimated_rent_weekly_max) / 2.0
            actual_yield = ((avg_rent * 52) / realistic_price) * 100.0 if realistic_price > 0 else 0
            if actual_yield < brief.target_gross_yield_pct:
                deficit = brief.target_gross_yield_pct - actual_yield
                suitability_pts -= min(5.0, deficit * 2.5)
        suitability_pts = max(0.0, suitability_pts)

        # --- 7. Risk Rating (Max 10 pts) ---
        high_risks = [r for r in prop.risks if r.rating == RiskRating.HIGH]
        medium_risks = [r for r in prop.risks if r.rating == RiskRating.MEDIUM]

        risk_pts = 10.0 - (len(high_risks) * 5.0 + len(medium_risks) * 2.0)
        risk_pts = max(0.0, risk_pts)

        if high_risks and not hard_rejection:
            rejection_reasons.append(f"High risk identified: {high_risks[0].risk_name} ({high_risks[0].description})")

        total = budget_pts + req_pts + value_pts + location_pts + builder_pts + suitability_pts + risk_pts

        return ScoringBreakdown(
            budget_fit=round(budget_pts, 1),
            requirement_match=round(req_pts, 1),
            value_competitiveness=round(value_pts, 1),
            location_amenity=round(location_pts, 1),
            builder_confidence=round(builder_pts, 1),
            suitability_score=round(suitability_pts, 1),
            risk_score=round(risk_pts, 1),
            total_score=round(total, 1),
            hard_rejection=hard_rejection,
            rejection_reason="; ".join(rejection_reasons),
            advisories="; ".join(advisories),
        )

    @classmethod
    def assign_recommendation(cls, prop: CandidateProperty) -> Tuple[RecommendationStatus, str]:
        # Defect #5 Fix: Items with VerificationStatus.PENDING are flagged as HOLD unless consultant-approved
        if prop.verification_status == VerificationStatus.PENDING and not prop.consultant_approved:
            return RecommendationStatus.HOLD, "Verification Status: Pending Confirmation. Requires consultant approval before client presentation."

        if prop.verification_status == VerificationStatus.UNAVAILABLE:
            return RecommendationStatus.DO_NOT_RECOMMEND, "REJECTED: Property is unavailable or sold."

        if not prop.scoring:
            return RecommendationStatus.HOLD, "Awaiting scoring evaluation."

        if prop.scoring.hard_rejection:
            return RecommendationStatus.DO_NOT_RECOMMEND, f"REJECTED: {prop.scoring.rejection_reason}"

        score = prop.scoring.total_score
        has_high_risk = any(r.rating == RiskRating.HIGH for r in prop.risks)

        if score >= 80.0 and not has_high_risk:
            caveats = []
            if prop.price_breakdown.turnkey_status != TurnkeyStatus.FULL_TURNKEY:
                caveats.append(f"{prop.price_breakdown.turnkey_status.value} - allow ${prop.price_breakdown.estimated_additional_costs:,.0f} in additional costs")
            if prop.risks:
                caveats.append(f"{len(prop.risks)} open risk item(s) - see risk register")
            caveat_str = f" Note: {'; '.join(caveats)}." if caveats else " Fully satisfies budget, location, and structural requirements."
            return RecommendationStatus.RECOMMEND, f"Strong match ({score}/100).{caveat_str}"
        elif score >= 65.0:
            return RecommendationStatus.RECOMMEND_WITH_CONDITIONS, f"Satisfactory match ({score}/100). Requires consultant confirmation on turnkey allowances or risk mitigation."
        else:
            return RecommendationStatus.HOLD, f"Low suitability score ({score}/100)."
