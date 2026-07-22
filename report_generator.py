"""
Property Summary Report Generator for SPB AI Property Research Agent.
Implements SOP Step 12 & Section 7 (Property Summary Template).
"""

from schema import CandidateProperty, ClientBrief
from datetime import datetime


class ReportGenerator:
    @staticmethod
    def generate_property_summary_markdown(brief: ClientBrief, prop: CandidateProperty) -> str:
        sc = prop.scoring
        pb = prop.price_breakdown
        date_str = prop.date_checked or datetime.now().strftime("%d/%m/%Y")

        missing_str = ", ".join(pb.missing_inclusions) if pb.missing_inclusions else "None (Full Turnkey)"

        md = f"""# SMART PROPERTY BUYING – PROPERTY SUMMARY

**Client Name:** {brief.client_name}  
**Date Checked:** {date_str}  
**Property Lot/Address:** {prop.lot_address}  
**Suburb/State:** {prop.suburb}, {prop.state}  
**Property Type:** House & Land Package  
**Title Status:** {prop.title_status} (Expected: {prop.expected_title_date})  

---

### Key Specifications
| Parameter | Value |
| :--- | :--- |
| **Bedrooms / Bathrooms / Cars** | {prop.bedrooms} Bed | {prop.bathrooms} Bath | {prop.car_spaces} Car |
| **Storeys** | {prop.storeys} |
| **Land Size** | {prop.land_size_sqm:,.0f} m² |
| **House Size** | {prop.house_size_sqm:,.0f} m² |
| **Builder / Design** | {prop.builder_name} ({prop.house_design}) |
| **Builder Confidence** | {prop.builder_confidence_rating} |

---

### Pricing & Turnkey Inclusions Audit
| Item | Cost / Details | Notes |
| :--- | :--- | :--- |
| **Land Price** | ${pb.land_price:,.2f} | Confirmed |
| **Build Price** | ${pb.build_price:,.2f} | Standard Specification |
| **Advertised Package Price** | ${pb.advertised_package_price:,.2f} | Headline Price |
| **Estimated Additional Costs** | ${pb.estimated_additional_costs:,.2f} | Inclusions Audit Allowance |
| **REALISTIC TOTAL PRICE** | **${pb.realistic_total_price:,.2f}** | **Evaluated Budget Cost** |
| **Turnkey Classification** | **{pb.turnkey_status.value}** | Missing: {missing_str} |
| **Estimated Rental Income** | ${prop.estimated_rent_weekly_min:,.0f} – ${prop.estimated_rent_weekly_max:,.0f} / week | Estimated Yield: {((((prop.estimated_rent_weekly_min + prop.estimated_rent_weekly_max)/2)*52)/pb.realistic_total_price)*100:.2f}% |

---

### Executive Summary & Recommendation
**Benchmark Score:** `{sc.total_score if sc else 'N/A'} / 100`  
**Recommendation:** **{prop.recommendation.value}**  
**Reasoning:** {prop.recommendation_reason}  

**Location & Amenities:** {prop.amenities_summary}  

---

### Risk Register & Risk Rating
"""
        if prop.risks:
            md += "| Risk Factor | Rating | Description & Proposed Mitigation |\n| :--- | :--- | :--- |\n"
            for r in prop.risks:
                md += f"| {r.risk_name} | **{r.rating.value}** | {r.description}. *Mitigation:* {r.proposed_mitigation} |\n"
        else:
            md += "*No high or medium risks identified for this property.*\n"

        md += f"""
---
*Disclaimer: Information checked on {date_str} and subject to availability and independent verification. This summary does not constitute legal, financial, building, tax or valuation advice.*
"""
        return md
