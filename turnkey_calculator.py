"""
Turnkey Price & Inclusion Calculator for SPB AI Property Research Agent.
Implements SOP Step 6: Validate Full Package Price and Turnkey Status.
"""

from typing import Dict, Any, List
from schema import PriceBreakdown, TurnkeyStatus


class TurnkeyCalculator:
    # Typical allowances if items are excluded or omitted
    DEFAULT_ESTIMATES = {
        'site_costs': 15000.0,
        'driveway': 5000.0,
        'fencing': 4000.0,
        'landscaping': 5000.0,
        'flooring': 6000.0,
        'blinds': 2500.0,
        'hvac': 4500.0,
    }

    @classmethod
    def calculate_price_breakdown(cls, raw_pkg: Dict[str, Any]) -> PriceBreakdown:
        land_price = float(raw_pkg.get('land_price', 0.0))
        build_price = float(raw_pkg.get('build_price', 0.0))
        advertised = float(raw_pkg.get('advertised_package_price', land_price + build_price))

        inclusions = raw_pkg.get('inclusions') or {}
        missing_inclusions: List[str] = []
        est_add_cost = 0.0

        # NOTHING RECORDED is not the same as AN ITEM EXCLUDED, and the two must not
        # produce the same sentence. No extractor populates `inclusions` — 0 of the
        # packages we hold carry one — so this branch is every package, and the answer
        # below has to say "the source is silent", not assert what the builder includes.
        nothing_stated = not inclusions

        # Check Site Costs
        site_costs_fixed = inclusions.get('site_costs_fixed', False)
        site_cost_val = float(inclusions.get('site_costs_val', 0.0))
        if not site_costs_fixed and site_cost_val == 0:
            missing_inclusions.append(
                'Site costs are not stated by the source — $15,000 allowed as an '
                'ESTIMATE, not a builder figure'
                if nothing_stated else
                'Fixed site costs (estimated $15,000 allowance needed)')
            est_add_cost += cls.DEFAULT_ESTIMATES['site_costs']

        # Driveway
        if not inclusions.get('driveway_included', True):
            missing_inclusions.append('Exposed aggregate / Concrete Driveway')
            est_add_cost += cls.DEFAULT_ESTIMATES['driveway']

        # Fencing
        if not inclusions.get('fencing_included', True):
            missing_inclusions.append('Perimeter Fencing & Gate')
            est_add_cost += cls.DEFAULT_ESTIMATES['fencing']

        # Landscaping
        if not inclusions.get('landscaping_included', True):
            missing_inclusions.append('Front & Rear Landscaping')
            est_add_cost += cls.DEFAULT_ESTIMATES['landscaping']

        # Flooring
        if not inclusions.get('flooring_included', True):
            missing_inclusions.append('Complete Flooring (Tiles & Carpet)')
            est_add_cost += cls.DEFAULT_ESTIMATES['flooring']

        # Blinds
        if not inclusions.get('blinds_included', True):
            missing_inclusions.append('Window Blinds / Coverings')
            est_add_cost += cls.DEFAULT_ESTIMATES['blinds']

        # HVAC
        if not inclusions.get('hvac_included', True):
            missing_inclusions.append('Air Conditioning / Climate Control')
            est_add_cost += cls.DEFAULT_ESTIMATES['hvac']

        # Classification
        if nothing_stated:
            # "Partial Turnkey" is a claim about what the package CONTAINS, and every
            # package was being given it — on the strength of a source that said nothing
            # about inclusions either way. What we actually know is that it is unclear,
            # and that is a status this enum already has.
            status = TurnkeyStatus.UNCLEAR
        elif not missing_inclusions and site_costs_fixed:
            status = TurnkeyStatus.FULL_TURNKEY
        elif len(missing_inclusions) <= 2:
            status = TurnkeyStatus.PARTIAL_TURNKEY
        elif len(missing_inclusions) > 2:
            status = TurnkeyStatus.BASE_PACKAGE
        else:
            status = TurnkeyStatus.UNCLEAR

        realistic_total = advertised + est_add_cost

        return PriceBreakdown(
            advertised_package_price=advertised,
            land_price=land_price,
            build_price=build_price,
            fixed_site_costs=site_cost_val,
            driveway_cost=cls.DEFAULT_ESTIMATES['driveway'] if 'Driveway' in str(missing_inclusions) else 0.0,
            fencing_cost=cls.DEFAULT_ESTIMATES['fencing'] if 'Fencing' in str(missing_inclusions) else 0.0,
            landscaping_cost=cls.DEFAULT_ESTIMATES['landscaping'] if 'Landscaping' in str(missing_inclusions) else 0.0,
            flooring_cost=cls.DEFAULT_ESTIMATES['flooring'] if 'Flooring' in str(missing_inclusions) else 0.0,
            blinds_cost=cls.DEFAULT_ESTIMATES['blinds'] if 'Blinds' in str(missing_inclusions) else 0.0,
            hvac_cost=cls.DEFAULT_ESTIMATES['hvac'] if 'Air Conditioning' in str(missing_inclusions) else 0.0,
            estimated_additional_costs=est_add_cost,
            realistic_total_price=realistic_total,
            turnkey_status=status,
            missing_inclusions=missing_inclusions
        )
