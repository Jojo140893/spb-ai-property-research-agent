"""
Automated Section 9 QA Checklist Guard (SOP Step 15).
Blocks Kommo submission until every mandatory SOP compliance check passes.
"""

from typing import List, Dict, Any, Tuple
from schema import ClientBrief, CandidateProperty, VerificationStatus, RecommendationStatus


class Section9QAChecker:
    @classmethod
    def verify_pipeline_compliance(cls, brief: ClientBrief, candidate_count: int, shortlist: List[CandidateProperty]) -> Tuple[bool, List[str]]:
        checklist_failures: List[str] = []

        # 1. Brief Completeness Check
        if not brief.client_name or brief.budget_max <= 0:
            checklist_failures.append("QA Item 1 Failed: Client Brief incomplete or budget max missing.")

        # 2. Candidate Volume Check
        if candidate_count < 1:
            checklist_failures.append("QA Item 2 Failed: No candidate packages evaluated.")

        # 3. Verification Check (No unverified property in final list without consultant approval)
        for p in shortlist:
            if p.verification_status == VerificationStatus.PENDING and not p.consultant_approved:
                checklist_failures.append(f"QA Item 5 Failed: Property {p.lot_address} is Pending Confirmation without explicit consultant waiver.")
            elif p.verification_status == VerificationStatus.UNAVAILABLE:
                checklist_failures.append(f"QA Item 5 Failed: Unavailable property {p.lot_address} included in shortlist.")

        # 4. Turnkey Audit Check
        for p in shortlist:
            if not hasattr(p, 'price_breakdown') or p.price_breakdown is None:
                checklist_failures.append(f"QA Item 6 Failed: Price breakdown missing for {p.lot_address}.")

        # 5. Scoring Matrix Check
        for p in shortlist:
            if not p.scoring:
                checklist_failures.append(f"QA Item 11 Failed: 100-point scoring matrix not calculated for {p.lot_address}.")

        is_passed = len(checklist_failures) == 0
        return is_passed, checklist_failures
