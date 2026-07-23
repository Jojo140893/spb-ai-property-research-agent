"""
Client Brief Parser Module for SPB AI Property Research Agent.
Extracts mandatory vs. preferred requirements, budget limits, buyer classification,
location filters, and structural criteria.
"""

from typing import Dict, Any, List
from schema import ClientBrief, BuyerType


class ClientBriefParser:
    @staticmethod
    def parse_dict(raw_data: Dict[str, Any]) -> ClientBrief:
        """
        Parses raw client dictionary or Kommo custom field mapping into a structured ClientBrief.
        """
        # Determine Buyer Type
        buyer_str = str(raw_data.get('buyer_type', '')).lower()
        if 'first' in buyer_str:
            buyer_type = BuyerType.FIRST_HOME_BUYER
        elif 'invest' in buyer_str:
            buyer_type = BuyerType.INVESTOR
        elif 'smsf' in buyer_str:
            buyer_type = BuyerType.SMSF
        else:
            buyer_type = BuyerType.OWNER_OCCUPIER

        # Parse Suburbs
        suburbs_raw = raw_data.get('primary_suburbs', [])
        if isinstance(suburbs_raw, str):
            suburbs = [s.strip() for s in suburbs_raw.split(',') if s.strip()]
        else:
            suburbs = list(suburbs_raw)

        budget_max = float(raw_data.get('budget_max', 0.0))
        preferred_cap = float(raw_data.get('preferred_spending_cap', budget_max))

        return ClientBrief(
            client_name=raw_data.get('client_name', 'Unnamed Client'),
            budget_max=budget_max,
            preferred_spending_cap=preferred_cap,
            deposit_amount=float(raw_data.get('deposit_amount', 0.0)),
            finance_status=raw_data.get('finance_status', 'Pre-approved'),
            buyer_type=buyer_type,
            state=str(raw_data.get('state', 'QLD')).upper(),
            primary_suburbs=suburbs,
            bedrooms_min=int(raw_data.get('bedrooms_min', 3)),
            bathrooms_min=int(raw_data.get('bathrooms_min', 2)),
            car_spaces_min=int(raw_data.get('car_spaces_min', 1)),
            storeys_max=int(raw_data.get('storeys_max', 2)),
            land_size_min_sqm=float(raw_data.get('land_size_min_sqm', 300.0)),
            house_size_min_sqm=float(raw_data.get('house_size_min_sqm', 150.0)),
            target_rent_weekly=float(raw_data['target_rent_weekly']) if raw_data.get('target_rent_weekly') else None,
            target_gross_yield_pct=float(raw_data['target_gross_yield_pct']) if raw_data.get('target_gross_yield_pct') else None,
            build_timeframe_months=int(raw_data['build_timeframe_months']) if raw_data.get('build_timeframe_months') else None,
            search_radius_km=float(raw_data['search_radius_km']) if raw_data.get('search_radius_km') else None,
            additional_notes=raw_data.get('additional_notes', '')
        )
