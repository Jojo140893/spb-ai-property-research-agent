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
def coerce_number(value: Any, default=None):
    """Best-effort number out of anything a form or an API caller might send.

    Public because the raw brief dict is read in more than one place — the parser here
    and the research record written in database.py — and each one that reimplemented
    `float(...)` became its own HTTP 500. "abc" and "750,000" both crashed the request
    at database.py even after this module learned to handle them.
    """
    if value is None:
        return default
    if isinstance(value, bool):          # bool is an int subclass; not a quantity
        return default
    if isinstance(value, str):
        value = value.strip().replace(",", "").replace("$", "").replace(" ", "")
        if not value:
            return default
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        # OverflowError, not ValueError, is what a Python int beyond float range raises.
        # json.loads turns a bare 1e400-sized integer literal into exactly that, so it
        # reached here as an int and crashed the request with a 500.
        return default
    # NaN and infinities survive json.loads and poison every comparison after them.
    if out != out or out in (float("inf"), float("-inf")):
        return default
    return out


def _num(raw: Dict[str, Any], key: str, default):
    if key not in raw:
        return default
    return coerce_number(raw[key], default)


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
            # NO MINIMUM UNLESS THE CLIENT STATES ONE — the same rule already applied
            # to land and house size below, and for the same reason.
            #
            # These defaulted to 3 bedrooms, 2 bathrooms, 1 car space and a 2-storey cap.
            # Every one of those is a MANDATORY filter, so a brief that simply did not
            # mention bathrooms silently discarded stock honestly recording "1 bathroom"
            # against a requirement the client never gave — while a listing that recorded
            # nothing sailed through the same filter untouched. Inventing a minimum is the
            # same class of error as inventing a price.
            #
            # storeys_max defaulted to 2, which hard-rejected every three-storey home
            # nobody had objected to. None means no cap; scoring_engine guards for it.
            bedrooms_min=_i(raw_data, 'bedrooms_min', 0),
            bathrooms_min=_i(raw_data, 'bathrooms_min', 0),
            car_spaces_min=_i(raw_data, 'car_spaces_min', 0),
            storeys_max=_i(raw_data, 'storeys_max', None),
            # No minimum unless the client states one. These used to default to 300 and
            # 150, which invented a requirement nobody asked for — and house size is a
            # HARD rejection, recorded on only 18% of stock, so the invented 150 m² wiped
            # out every VIC result at every budget and every radius. A brief that is
            # silent about house size means "no preference", not "at least 150 m²".
            land_size_min_sqm=_f(raw_data, 'land_size_min_sqm', 0.0),
            house_size_min_sqm=_f(raw_data, 'house_size_min_sqm', 0.0),
            target_rent_weekly=_f(raw_data, 'target_rent_weekly', None),
            target_gross_yield_pct=_f(raw_data, 'target_gross_yield_pct', None),
            build_timeframe_months=_i(raw_data, 'build_timeframe_months', None),
            search_radius_km=_f(raw_data, 'search_radius_km', None),
            additional_notes=raw_data.get('additional_notes') or ''
        )
