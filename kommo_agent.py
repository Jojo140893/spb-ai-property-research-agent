"""
Production 15-Step SOP Orchestrator Pipeline for SPB AI Property Research Agent.
Integrates Database Audit, Hybrid Search Sources, Step 11 Scoring Engine,
Step 6 Turnkey Auditor, Step 15 QA Checklist, and Kommo CRM v4 REST Integration.
"""

import json
from datetime import datetime
from typing import List, Dict, Any, Optional

from schema import (
    ClientBrief, CandidateProperty, RecommendationStatus,
    RiskItem, RiskRating, BuyerType, VerificationStatus
)
from brief_parser import ClientBriefParser
from builder_registry import BuilderRegistry
from turnkey_calculator import TurnkeyCalculator
from scoring_engine import ScoringEngine, BuilderConfidenceModel
from report_generator import ReportGenerator
from kommo_client import KommoClient
from database import ResearchDatabase
from qa_checker import Section9QAChecker
from sources.e_agent import EAgentSource
from sources.builder_portals import BuilderPortalSource
from sources.drive_pdf import DrivePdfSource
from sources.dedupe import DedupeEngine
from sources.rea_domain_benchmark import ReaDomainBenchmarkSource
import config


class KommoPropertyResearchAgent:
    def __init__(self, csv_filepath: Optional[str] = None):
        self.builder_registry = BuilderRegistry(csv_filepath or str(config.BUILDER_CSV_PATH))
        self.kommo_client = KommoClient()
        self.db = ResearchDatabase()
        
        # Search Sources
        self.eagent_source = EAgentSource()
        self.portal_source = BuilderPortalSource()
        self.drive_source = DrivePdfSource()

    def run_property_research(self, raw_brief_dict: Dict[str, Any], candidate_packages: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Executes the full 15-step SOP property research workflow.
        """
        # Step 1 & 2: Structure Client Brief & Research Record
        brief: ClientBrief = ClientBriefParser.parse_dict(raw_brief_dict)
        suburb_name = brief.primary_suburbs[0] if brief.primary_suburbs else 'General'
        research_record_id = f"{brief.client_name} - {brief.state}/{suburb_name} - ${brief.budget_max:,.0f} - {datetime.now().strftime('%Y-%m-%d')}"

        # Step 3 & 4: Hybrid Search & Capture across channels if candidate_packages is not manually provided
        if not candidate_packages:
            raw_captured = []
            raw_captured.extend(self.eagent_source.search({'state': brief.state, 'budget_max': brief.budget_max, 'primary_suburbs': brief.primary_suburbs}))
            raw_captured.extend(self.portal_source.search({'state': brief.state, 'budget_max': brief.budget_max, 'primary_suburbs': brief.primary_suburbs}))
            raw_captured.extend(self.drive_source.search({'budget_max': brief.budget_max}))
            candidate_packages = DedupeEngine.deduplicate(raw_captured)

        # Step 5 to 11: Process Each Candidate Package
        processed_candidates: List[CandidateProperty] = []
        rejected_candidates: List[Dict[str, str]] = []

        for idx, raw_pkg in enumerate(candidate_packages):
            builder_name = raw_pkg.get('builder_name', 'Unknown Builder')
            builder_info = self.builder_registry.search_builder_by_name(builder_name)
            
            # Step 8: Builder Quality & Confidence Model (Defect #1 Fix)
            b_rating, b_score, b_reason = BuilderConfidenceModel.evaluate_builder(builder_name, builder_info)

            # Step 5: Three-State Verification (Defect #5 Fix)
            # SOP rule: a package is Pending Confirmation until a source explicitly verifies it.
            is_verified = raw_pkg.get('verified', False)
            verif_status = VerificationStatus.VERIFIED if is_verified else VerificationStatus.PENDING

            # Step 6: Validate Package Price & Turnkey Inclusions
            price_breakdown = TurnkeyCalculator.calculate_price_breakdown(raw_pkg)

            # Step 7: Step 7 Market Benchmarking
            bm_res = ReaDomainBenchmarkSource.evaluate_value_classification(
                price_breakdown.land_price,
                raw_pkg.get('land_size_sqm', 400),
                price_breakdown.realistic_total_price,
                raw_pkg.get('suburb', suburb_name),
                brief.state
            )

            # Step 10: Risks Assessment
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
                builder_confidence_rating=b_rating,
                source_channel=raw_pkg.get('source_channel', 'E-Agent / Builder Portal'),
                source_url_or_ref=raw_pkg.get('source_url_or_ref', builder_info.get('portal_link', '') if builder_info else 'Internal Stock'),
                date_checked=datetime.now().strftime("%d/%m/%Y"),
                verification_status=verif_status,
                consultant_approved=raw_pkg.get('consultant_approved', False),
                risks=risks
            )

            # Step 11: Score Property Matrix
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

        # Select Top 3-5 Shortlisted Properties.
        # SOP Step 5: Pending Confirmation packages stay out of the final list
        # unless a consultant explicitly approves their inclusion.
        final_pool = [
            c for c in processed_candidates
            if c.verification_status == VerificationStatus.VERIFIED or c.consultant_approved
        ]
        shortlist = final_pool[:5]
        pending_awaiting = [c for c in processed_candidates if c not in final_pool]
        for c in pending_awaiting:
            rejected_candidates.append({
                'property_id': c.property_id,
                'address': c.lot_address,
                'reason': 'Pending Confirmation - excluded from final list until verified or consultant-approved'
            })

        # Step 15: Automated Section 9 QA Checklist Validation
        qa_passed, qa_failures = Section9QAChecker.verify_pipeline_compliance(brief, len(candidate_packages), shortlist)

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

        # Step 14: Kommo CRM Update Payload Format & Database Audit Trail
        kommo_update_payload = self._build_kommo_update_payload(brief, research_record_id, shortlist, rejected_candidates)
        self.db.save_research_run(research_record_id, raw_brief_dict, shortlist, rejected_candidates)

        return {
            'research_record_id': research_record_id,
            'client_brief': brief,
            'shortlist_count': len(shortlist),
            'shortlist': shortlist,
            'rejected_count': len(rejected_candidates),
            'rejected_log': rejected_candidates,
            'qa_passed': qa_passed,
            'qa_failures': qa_failures,
            'reports': reports,
            'kommo_payload': kommo_update_payload
        }

    def _build_kommo_update_payload(self, brief: ClientBrief, record_id: str, shortlist: List[CandidateProperty], rejected: List[Dict[str, str]]) -> Dict[str, Any]:
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
