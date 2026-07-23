"""
Live CLI runner for the SPB AI Property Research Agent.

Runs the real 15-step pipeline end to end: hybrid live search (E-Agent +
direct builder portals via Playwright, plus Drive stock lists) -> verify ->
benchmark -> score -> rank -> client report -> Kommo payload.

There is NO sample/fabricated inventory. If no listings come back, the run
reports exactly why (missing portal credentials, no drive stock lists, or
selectors needing confirmation) instead of inventing data.

Configure before running for real:
  - E_AGENT_USERNAME / E_AGENT_PASSWORD           (E-Agent portal login)
  - builder portal logins in Book1(Builders) List.csv
  - drive_input/comparables*.csv                  (CoreLogic/REA market data)
  - drive_input/*.csv|*.pdf                        (builder stock lists)
"""

import json
import os

from kommo_agent import KommoPropertyResearchAgent
from config import OUTPUT_DIR


def run_live(client_brief: dict):
    print("=" * 70)
    print("      SMART PROPERTY BUYING (SPB) AI PROPERTY RESEARCH AGENT")
    print("=" * 70)

    agent = KommoPropertyResearchAgent()
    print(f"[+] Builder registry: {len(agent.builder_registry.get_all_builders())} approved builders")
    print(f"[+] Suburb geo index: {'loaded' if agent.geo.loaded else 'MISSING'}")
    print(f"[+] Market comparables loaded: {len(agent.benchmark_engine.comparables)}")

    print(f"\n[+] Running LIVE property research for: {client_brief['client_name']}")
    # No candidate_packages passed -> the agent runs the live hybrid search.
    result = agent.run_property_research(client_brief)

    print(f"\n[+] Research Record: {result['research_record_id']}")
    cov = result['builder_coverage']
    print(f"[+] Builder coverage: {cov['in_scope_for_state']}/{cov['total_in_directory']} in scope "
          f"({len(cov['e_agent_channel'])} E-Agent, {len(cov['direct_portal_channel'])} portals, "
          f"{len(cov['email_or_drive_channel'])} email/drive)")
    print(f"[+] Search area: {len(result['search_area'])} suburb(s)")
    print(f"[+] Shortlisted: {result['shortlist_count']} | Rejected/Pending: {result['rejected_count']}")

    if not result['shortlist'] and result['rejected_count'] == 0:
        print("\n" + "!" * 70)
        print("  NO LISTINGS RETURNED FROM ANY LIVE SOURCE.")
        print("  This is expected until the data layer is configured:")
        print("   - Set E_AGENT_USERNAME / E_AGENT_PASSWORD for the E-Agent scraper")
        print("   - Add portal logins to Book1(Builders) List.csv for direct portals")
        print("   - Drop builder stock lists into drive_input/ (*.csv / *.pdf)")
        print("   - Confirm portal selectors in sources/portal_config.py on first run")
        print("!" * 70)
        return

    print("\n" + "=" * 70)
    print("                    SHORTLISTED PROPERTIES RANKING")
    print("=" * 70)
    for idx, prop in enumerate(result['shortlist'], 1):
        bm = prop.benchmark or {}
        print(f"\nRANK #{idx}: {prop.lot_address}, {prop.suburb} ({prop.builder_name})")
        print(f"  - Evaluated Price: ${prop.price_breakdown.realistic_total_price:,.2f}")
        print(f"  - Turnkey: {prop.price_breakdown.turnkey_status.value}")
        print(f"  - Market benchmark: {bm.get('classification', 'n/a')}")
        print(f"  - Score: {prop.scoring.total_score}/100 | {prop.recommendation.value}")

    if result['rejected_log']:
        print("\n" + "=" * 70)
        print("                      REJECTED / PENDING LOG")
        print("=" * 70)
        for r in result['rejected_log']:
            print(f"  - [{r['property_id']}] {r['address']}: {r['reason']}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    summary_file = OUTPUT_DIR / "property_summary_report.md"
    with open(summary_file, 'w', encoding='utf-8') as f:
        for r in result['reports']:
            f.write(r['summary_markdown'] + "\n\n" + "=" * 80 + "\n\n")
    payload_file = OUTPUT_DIR / "kommo_crm_payload.json"
    with open(payload_file, 'w', encoding='utf-8') as f:
        json.dump(result['kommo_payload'], f, indent=2)

    print("\n" + "=" * 70)
    print(f"[SUCCESS] Property summaries: {summary_file}")
    print(f"[SUCCESS] Kommo payload:      {payload_file}")
    if result.get('client_report_path'):
        print(f"[SUCCESS] Client report:     {result['client_report_path']}")
    print("=" * 70)


if __name__ == '__main__':
    # Example brief — the pipeline searches live sources for matching stock.
    example_brief = {
        'client_name': 'Sample Client',
        'budget_max': 780000.0,
        'preferred_spending_cap': 740000.0,
        'deposit_amount': 80000.0,
        'finance_status': 'Pre-approved',
        'buyer_type': 'Owner Occupier',
        'state': 'QLD',
        'primary_suburbs': ['Coomera', 'Springfield'],
        'search_radius_km': 15,
        'bedrooms_min': 4,
        'bathrooms_min': 2,
        'car_spaces_min': 2,
        'storeys_max': 1,
        'land_size_min_sqm': 350.0,
        'house_size_min_sqm': 175.0,
    }
    run_live(example_brief)
