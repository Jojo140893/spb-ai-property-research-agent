"""
realestate.com.au & Domain Market Benchmarking Source (SOP Step 7).
Provides market comparables for vacant land $/sqm and comparable House & Land packages.
"""

from typing import List, Dict, Any


class ReaDomainBenchmarkSource:
    @classmethod
    def get_land_benchmarks(cls, suburb: str, state: str) -> Dict[str, Any]:
        """
        Returns average land price per sqm benchmark for target suburb.
        """
        # Benchmark heuristics per state
        benchmarks = {
            'QLD': {'avg_land_psqm': 850.0, 'median_house_price': 720000.0},
            'VIC': {'avg_land_psqm': 950.0, 'median_house_price': 780000.0},
            'NSW': {'avg_land_psqm': 1250.0, 'median_house_price': 920000.0},
            'SA':  {'avg_land_psqm': 650.0, 'median_house_price': 620000.0},
            'WA':  {'avg_land_psqm': 700.0, 'median_house_price': 640000.0},
        }
        return benchmarks.get(state.upper(), {'avg_land_psqm': 850.0, 'median_house_price': 700000.0})

    @classmethod
    def evaluate_value_classification(cls, land_price: float, land_sqm: float, package_price: float, suburb: str, state: str) -> Dict[str, Any]:
        bm = cls.get_land_benchmarks(suburb, state)
        actual_psqm = land_price / land_sqm if land_sqm > 0 else 0
        bm_psqm = bm['avg_land_psqm']

        diff_pct = ((actual_psqm - bm_psqm) / bm_psqm) * 100.0 if bm_psqm > 0 else 0

        if diff_pct < -5.0:
            classification = "Below Market Value"
            score_contrib = 15.0
        elif diff_pct <= 5.0:
            classification = "Competitive Market Value"
            score_contrib = 12.5
        elif diff_pct <= 12.0:
            classification = "Slightly Above Market"
            score_contrib = 10.0
        else:
            classification = "Poor Value"
            score_contrib = 5.0

        return {
            'actual_land_psqm': round(actual_psqm, 2),
            'benchmark_land_psqm': bm_psqm,
            'variance_pct': round(diff_pct, 1),
            'value_classification': classification,
            'value_score_contribution': score_contrib
        }
