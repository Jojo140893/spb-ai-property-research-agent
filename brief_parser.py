"""
Client Brief Parser Module for SPB AI Property Research Agent.
Extracts mandatory vs. preferred requirements, budget limits, buyer classification,
location filters, and structural criteria.
"""

from typing import Dict, Any, List, Optional
from schema import ClientBrief, BuyerType


# A PRESENT-BUT-NULL key is not a missing key, and dict.get's default only covers the
# second case. `int(raw.get('bedrooms_min', 3))` therefore crashed on None with
#
#     TypeError: int() argument must be ... not 'NoneType'
#
# and every clear-a-field-and-search returned HTTP 500. The browser sends null for an
# empty number input, because parseInt('') is NaN and JSON.stringify writes NaN as
# null — so emptying "Min Bedrooms" in the form was enough to break the search.
# Coleen hit exactly that. Fixed at BOTH ends: the page no longer sends NaN, and this
# parser no longer trusts it not to. A brief is user input arriving over HTTP; it has
# to survive anything, not just what our own form happens to send.
def _num(raw: Dict[str, Any], key: str, default):
    if key not in raw:
        return default
    value = raw[key]
    if value is None:
        return default
    if isinstance(value, str):
        value = value.strip().replace(",", "").replace("$", "")
        if not value:
            return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    # NaN and infinities survive json.loads and poison every comparison after them.
    if out != out or out in (float("inf"), float("-inf")):
        return default
    return out


def _f(raw: Dict[str, Any], key: str, default) -> Optional[float]:
    out = _num(raw, key, default)
    return None if out is None else float(out)


def _i(raw: Dict[str, Any], key: str, default) -> Optional[int]:
    out = _num(raw, key, default)
    return None if out is None else int(out)


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
        suburbs_raw = raw_data.get('primary_suburbs', []) or []
        if isinstance(suburbs_raw, str):
            suburbs = [s.strip() for s in suburbs_raw.split(',') if s.strip()]
        else:
            suburbs = [str(s).strip() for s in suburbs_raw if str(s).strip()]

        budget_max = _f(raw_data, 'budget_max', 0.0)
        preferred_cap = _f(raw_data, 'preferred_spending_cap', budget_max)

        return ClientBrief(
            client_name=raw_data.get('client_name') or 'Unnamed Client',
            budget_max=budget_max,
            preferred_spending_cap=preferred_cap,
            deposit_amount=_f(raw_data, 'deposit_amount', 0.0),
            finance_status=raw_data.get('finance_status') or 'Pre-approved',
            buyer_type=buyer_type,
            state=str(raw_data.get('state') or 'QLD').upper(),
            primary_suburbs=suburbs,
            bedrooms_min=_i(raw_data, 'bedrooms_min', 3),
            bathrooms_min=_i(raw_data, 'bathrooms_min', 2),
            car_spaces_min=_i(raw_data, 'car_spaces_min', 1),
            storeys_max=_i(raw_data, 'storeys_max', 2),
            land_size_min_sqm=_f(raw_data, 'land_size_min_sqm', 300.0),
            house_size_min_sqm=_f(raw_data, 'house_size_min_sqm', 150.0),
            target_rent_weekly=_f(raw_data, 'target_rent_weekly', None),
            target_gross_yield_pct=_f(raw_data, 'target_gross_yield_pct', None),
            build_timeframe_months=_i(raw_data, 'build_timeframe_months', None),
            search_radius_km=_f(raw_data, 'search_radius_km', None),
            additional_notes=raw_data.get('additional_notes') or ''
        )
