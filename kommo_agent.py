"""
Kommo AI Property Research Agent Core Orchestrator.
Implements the 15-step SOP workflow, connecting Client Briefs, Builder Registry,
Turnkey Auditor, 100-Point Scoring Engine, Report Generator, and Kommo CRM payload formatter.
"""

import json
from datetime import datetime
from typing import List, Dict, Any, Optional

from schema import (
    ClientBrief, CandidateProperty, RecommendationStatus,
    RiskItem, RiskRating, BuyerType
)
from brief_parser import ClientBriefParser
from builder_registry import BuilderRegistry
from turnkey_calculator import TurnkeyCalculator
from scoring_engine import ScoringEngine
from report_generator import ReportGenerator


class KommoPropertyResearchAgent:
    def __init__(self, csv_filepath: str = "d:/kommo/Book1(Builders) List.csv"):
        self.builder_registry = BuilderRegistry(csv_filepath)

    def run_property_research(self, raw_brief_dict: Dict[str, Any], candidate_packages: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Executes the 15-step SOP property research workflow.
        """
        if not candidate_packages:
            candidate_packages = self._get_default_candidate_packages()

        # Step 1 & 2: Structure Client Brief
        brief: ClientBrief = ClientBriefParser.parse_dict(raw_brief_dict)
        suburb_name = brief.primary_suburbs[0] if brief.primary_suburbs else 'General'
        research_record_id = f"{brief.client_name} - {brief.state}/{suburb_name} - ${brief.budget_max:,.0f} - {datetime.now().strftime('%Y-%m-%d')}"

        # Step 3 & 4: Filter Builders & Process Candidates
        processed_candidates: List[CandidateProperty] = []
        rejected_candidates: List[Dict[str, str]] = []

        for idx, raw_pkg in enumerate(candidate_packages):
            builder_name = raw_pkg.get('builder_name', 'Unknown Builder')
            builder_info = self.builder_registry.search_builder_by_name(builder_name)
            
            # Step 5: Verify Availability & Pricing
            verified = raw_pkg.get('verified', True)
            if not verified:
                rejected_candidates.append({
                    'property_id': f"PROP-{idx+1}",
                    'address': raw_pkg.get('lot_address', f"Lot {idx+1}"),
                    'reason': 'Package marked unverified or unavailable'
                })
                continue

            # Step 6: Validate Package Price & Turnkey Inclusions
            price_breakdown = TurnkeyCalculator.calculate_price_breakdown(raw_pkg)

            # Step 8, 9, 10: Risks & Quality Assessment
            risks: List[RiskItem] = []
            for r in raw_pkg.get('risks', []):
                risks.append(RiskItem(
                    risk_name=r['name'],
                    rating=RiskRating(r['rating']),
                    description=r['description'],
                    proposed_mitigation=r.get('mitigation', 'Standard due diligence')
                ))

            cand = CandidateProperty(
                property_id=f"PROP-{idx+1}",
                lot_address=raw_pkg.get('lot_address', f"Lot {idx+1}"),
                suburb=raw_pkg.get('suburb', brief.primary_suburbs[0] if brief.primary_suburbs else 'Target Suburb'),
                state=raw_pkg.get('state', brief.state),
                builder_name=builder_name,
                developer_name=raw_pkg.get('developer_name', 'Developer'),
                house_design=raw_pkg.get('house_design', 'Standard Design'),
                bedrooms=int(raw_pkg.get('bedrooms', 4)),
                bathrooms=int(raw_pkg.get('bathrooms', 2)),
                car_spaces=int(raw_pkg.get('car_spaces', 2)),
                storeys=int(raw_pkg.get('storeys', 1)),
                land_size_sqm=float(raw_pkg.get('land_size_sqm', 400)),
                house_size_sqm=float(raw_pkg.get('house_size_sqm', 180)),
                title_status=raw_pkg.get('title_status', 'Expected Q4 2026'),
                expected_title_date=raw_pkg.get('expected_title_date', 'Q4 2026'),
                price_breakdown=price_breakdown,
                estimated_rent_weekly_min=float(raw_pkg.get('estimated_rent_weekly_min', 550)),
                estimated_rent_weekly_max=float(raw_pkg.get('estimated_rent_weekly_max', 600)),
                amenities_summary=raw_pkg.get('amenities_summary', 'Close to schools, train station & town centre.'),
                builder_confidence_rating=builder_info.get('contract_available', 'HIGH') if builder_info else 'MEDIUM',
                source_channel=raw_pkg.get('source_channel', 'E-Agent / Builder Portal'),
                source_url_or_ref=raw_pkg.get('source_url_or_ref', builder_info.get('portal_link', '') if builder_info else 'Internal Stock'),
                date_checked=datetime.now().strftime("%d/%m/%Y"),
                risks=risks
            )

            # Step 11: Score Property
            cand.scoring = ScoringEngine.evaluate_property(brief, cand)
            status, reason = ScoringEngine.assign_recommendation(cand)
            cand.recommendation = status
            cand.recommendation_reason = reason

            if cand.scoring.hard_rejection:
                rejected_candidates.append({
                    'property_id': cand.property_id,
                    'address': cand.lot_address,
                    'reason': cand.scoring.rejection_reason
                })
            else:
                processed_candidates.append(cand)

        # Rank candidates by Total Score descending
        processed_candidates.sort(key=lambda x: x.scoring.total_score if x.scoring else 0, reverse=True)

        # Select Top 3-5 Shortlisted Properties
        shortlist = processed_candidates[:5]

        # Step 12: Generate Reports
        reports = []
        for prop in shortlist:
            summary_md = ReportGenerator.generate_property_summary_markdown(brief, prop)
            reports.append({
                'property_id': prop.property_id,
                'address': prop.lot_address,
                'suburb': prop.suburb,
                'score': prop.scoring.total_score if prop.scoring else 0,
                'recommendation': prop.recommendation.value,
                'summary_markdown': summary_md
            })

        # Step 14: Kommo CRM Update Payload Format
        kommo_update_payload = self._build_kommo_update_payload(brief, research_record_id, shortlist, rejected_candidates)

        return {
            'research_record_id': research_record_id,
            'client_brief': brief,
            'shortlist_count': len(shortlist),
            'shortlist': shortlist,
            'rejected_count': len(rejected_candidates),
            'rejected_log': rejected_candidates,
            'reports': reports,
            'kommo_payload': kommo_update_payload
        }

    def _build_kommo_update_payload(self, brief: ClientBrief, record_id: str, shortlist: List[CandidateProperty], rejected: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Formats JSON object for updating Kommo lead, custom fields, internal note, and review task.
        """
        shortlist_summary = []
        for idx, p in enumerate(shortlist, 1):
            shortlist_summary.append(f"{idx}. {p.lot_address}, {p.suburb} - ${p.price_breakdown.realistic_total_price:,.0f} (Score: {p.scoring.total_score}/100 - {p.recommendation.value})")

        top_match = shortlist[0].lot_address if shortlist else "No qualifying properties found"
        top_price = shortlist[0].price_breakdown.realistic_total_price if shortlist else 0
        top_score = shortlist[0].scoring.total_score if (shortlist and shortlist[0].scoring) else 0

        note_text = f"SPB Property Research Completed\n" \
                    f"Record ID: {record_id}\n" \
                    f"Client: {brief.client_name} | Budget Ceiling: ${brief.budget_max:,.0f}\n\n" \
                    f"Top Shortlisted Properties:\n" + ("\n".join(shortlist_summary) if shortlist_summary else "None") + "\n\n" \
                    f"Rejected Options: {len(rejected)} candidate(s) filtered out.\n" \
                    f"Status: Awaiting Consultant Review"

        return {
            'research_record_id': record_id,
            'kommo_lead_status': 'Awaiting Consultant Review',
            'custom_fields_update': {
                'research_completed_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'top_property_match': top_match,
                'top_property_price': top_price,
                'top_property_score': top_score,
                'candidates_reviewed': len(shortlist) + len(rejected),
            },
            'kommo_internal_note': note_text,
            'consultant_review_task': {
                'task_type': 'Consultant Review & Approval',
                'title': f"Review Property Research for {brief.client_name}",
                'due_date_hours': 24,
                'status': 'Pending Review'
            }
        }

    def _get_default_candidate_packages(self) -> List[Dict[str, Any]]:
        return [
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
                    'fencing_included': False,
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
                'risks': [
                    {
                        'name': 'Title Registration Delay',
                        'rating': 'Medium',
                        'description': 'Developer target registration Q4 2026 could push back build start date',
                        'mitigation': 'Ensure 12-month sunset clause is inserted into land contract'
                    }
                ]
            },
            {
                'lot_address': 'Lot 88 Sanctuary Cove',
                'suburb': 'Hope Island',
                'state': 'QLD',
                'builder_name': 'Creation Homes',
                'house_design': 'Pacific 210',
                'bedrooms': 4,
                'bathrooms': 2.5,
                'car_spaces': 2,
                'storeys': 1,
                'land_size_sqm': 420,
                'house_size_sqm': 210,
                'land_price': 360000,
                'build_price': 390000,
                'advertised_package_price': 750000,
                'inclusions': {
                    'site_costs_fixed': True,
                    'driveway_included': True,
                    'fencing_included': True,
                    'landscaping_included': False,
                    'flooring_included': True,
                    'blinds_included': True,
                    'hvac_included': True
                },
                'title_status': 'Titled',
                'expected_title_date': 'Ready Now',
                'estimated_rent_weekly_min': 650,
                'estimated_rent_weekly_max': 700,
                'amenities_summary': 'Direct access to marina precinct, golf course, and waterfront dining.',
                'risks': []
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
                'storeys': 2,
                'land_size_sqm': 500,
                'house_size_sqm': 220,
                'land_price': 390000,
                'build_price': 430000,
                'advertised_package_price': 820000,
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
                'risks': []
            }
        ]
