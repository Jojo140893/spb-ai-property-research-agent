"""
Interactive CLI Launcher for SPB AI Property Research Agent.
Demonstrates end-to-end execution of property search, scoring, report generation, and Kommo CRM integration.
"""

import sys
import os
import json
from kommo_agent import KommoPropertyResearchAgent


def run_sample_demo():
    print("=" * 70)
    print("      SMART PROPERTY BUYING (SPB) AI PROPERTY RESEARCH AGENT")
    print("=" * 70)

    # Initialize Agent
    agent = KommoPropertyResearchAgent()
    print(f"[+] Builder Registry Loaded: {len(agent.builder_registry.get_all_builders())} builders indexed from CSV.")

    # 1. Define Sample Client Brief (Queensland First Home Buyer / Owner Occupier)
    sample_client_brief = {
        'client_name': 'Sarah & Mark Johnson',
        'budget_max': 780000.0,
        'preferred_spending_cap': 740000.0,
        'deposit_amount': 80000.0,
        'finance_status': 'Pre-approved (ANZ)',
        'buyer_type': 'Owner Occupier',
        'state': 'QLD',
        'primary_suburbs': ['Springfield', 'Coomera', 'Logan Reserve'],
        'bedrooms_min': 4,
        'bathrooms_min': 2,
        'car_spaces_min': 2,
        'storeys_max': 1,
        'land_size_min_sqm': 350.0,
        'house_size_min_sqm': 175.0,
        'additional_notes': 'Need 4 bedrooms, single storey, close to primary school and train station.'
    }

    # 2. Define Sample Candidate Packages (Simulated Inventory from Builder Portals / E-Agent)
    sample_candidate_packages = [
        {
            'lot_address': 'Lot 104 Willow Rise Estate',
            'suburb': 'Coomera',
            'state': 'QLD',
            'builder_name': 'Avia Homes',
            'house_design': 'Aura 185',
            'bedrooms': 4,
            'bathrooms': 2,
            'car_spaces': 2,
            'storeys': 1,
            'land_size_sqm': 400,
            'house_size_sqm': 185,
            'land_price': 340000,
            'build_price': 385000,
            'advertised_package_price': 725000,
            'inclusions': {
                'site_costs_fixed': True,
                'site_costs_val': 15000,
                'driveway_included': True,
                'fencing_included': True,
                'landscaping_included': True,
                'flooring_included': True,
                'blinds_included': True,
                'hvac_included': True
            },
            'title_status': 'Titled',
            'expected_title_date': 'Ready Now',
            'estimated_rent_weekly_min': 620,
            'estimated_rent_weekly_max': 660,
            'amenities_summary': 'Walk to Coomera Rivers State School, 3 mins to Coomera Train Station.',
            'verified': True,  # simulated portal-verified sample
            'risks': []
        },
        {
            'lot_address': 'Lot 42 Riverstone Estate',
            'suburb': 'Springfield',
            'state': 'QLD',
            'builder_name': 'Silkwood Homes',
            'house_design': 'Monaco 200',
            'bedrooms': 4,
            'bathrooms': 2,
            'car_spaces': 2,
            'storeys': 1,
            'land_size_sqm': 450,
            'house_size_sqm': 200,
            'land_price': 370000,
            'build_price': 380000,
            'advertised_package_price': 750000,
            'inclusions': {
                'site_costs_fixed': False,
                'driveway_included': True,
                'fencing_included': False,  # Missing Fencing ($4,000 allowance)
                'landscaping_included': True,
                'flooring_included': True,
                'blinds_included': True,
                'hvac_included': True
            },
            'title_status': 'Expected Q4 2026',
            'expected_title_date': 'November 2026',
            'estimated_rent_weekly_min': 640,
            'estimated_rent_weekly_max': 680,
            'amenities_summary': 'Close to Springfield Central Shopping Centre and Orion Lagoon.',
            'verified': True,  # simulated portal-verified sample
            'risks': [
                {
                    'name': 'Title Delay',
                    'rating': 'Medium',
                    'description': 'Developer target registration Q4 2026 could push back build start',
                    'mitigation': 'Ensure 12-month sunset clause in land contract'
                }
            ]
        },
        {
            'lot_address': 'Lot 18 Highgrove Heights',
            'suburb': 'Logan Reserve',
            'state': 'QLD',
            'builder_name': 'Choice Homes',
            'house_design': 'Haven 220',
            'bedrooms': 5,
            'bathrooms': 3,
            'car_spaces': 2,
            'storeys': 2,  # Violates 1 Storey requirement -> HARD REJECTION
            'land_size_sqm': 500,
            'house_size_sqm': 220,
            'land_price': 390000,
            'build_price': 430000,
            'advertised_package_price': 820000,  # Exceeds max budget -> HARD REJECTION
            'inclusions': {
                'site_costs_fixed': True,
                'driveway_included': True,
                'fencing_included': True,
                'landscaping_included': True,
                'flooring_included': True,
                'blinds_included': True,
                'hvac_included': True
            },
            'title_status': 'Titled',
            'expected_title_date': 'Immediate',
            'estimated_rent_weekly_min': 700,
            'estimated_rent_weekly_max': 740,
            'amenities_summary': 'Parkland views, close to Logan Hospital.',
            'verified': True,  # simulated portal-verified sample
            'risks': []
        }
    ]

    print("\n[+] Running AI Property Research for Client:", sample_client_brief['client_name'])
    result = agent.run_property_research(sample_client_brief, sample_candidate_packages)

    print(f"\n[+] Research Record Created: {result['research_record_id']}")
    print(f"[+] Candidate Packages Processed: {len(sample_candidate_packages)}")
    print(f"[+] Shortlisted (Qualified): {result['shortlist_count']}")
    print(f"[+] Rejected: {result['rejected_count']}")

    print("\n" + "=" * 70)
    print("                    SHORTLISTED PROPERTIES RANKING")
    print("=" * 70)
    for idx, prop in enumerate(result['shortlist'], 1):
        print(f"\nRANK #{idx}: {prop.lot_address}, {prop.suburb} ({prop.builder_name})")
        print(f"  - Total Evaluated Price: ${prop.price_breakdown.realistic_total_price:,.2f}")
        print(f"  - Turnkey Status: {prop.price_breakdown.turnkey_status.value}")
        print(f"  - AI Suitability Score: {prop.scoring.total_score} / 100")
        print(f"  - Recommendation: {prop.recommendation.value}")
        print(f"  - Reason: {prop.recommendation_reason}")

    if result['rejected_log']:
        print("\n" + "=" * 70)
        print("                      REJECTED PACKAGES LOG")
        print("=" * 70)
        for r in result['rejected_log']:
            print(f"  - [{r['property_id']}] {r['address']}: {r['reason']}")

    # Save output reports and Kommo Payload
    from config import OUTPUT_DIR
    output_dir = str(OUTPUT_DIR)
    os.makedirs(output_dir, exist_ok=True)

    summary_file = os.path.join(output_dir, "property_summary_report.md")
    with open(summary_file, 'w', encoding='utf-8') as f:
        for r in result['reports']:
            f.write(r['summary_markdown'] + "\n\n" + "="*80 + "\n\n")

    payload_file = os.path.join(output_dir, "kommo_crm_payload.json")
    with open(payload_file, 'w', encoding='utf-8') as f:
        json.dump(result['kommo_payload'], f, indent=2)

    print("\n" + "=" * 70)
    print(f"[SUCCESS] Reports generated at: {summary_file}")
    print(f"[SUCCESS] Kommo CRM payload generated at: {payload_file}")
    print("=" * 70)


if __name__ == '__main__':
    run_sample_demo()
