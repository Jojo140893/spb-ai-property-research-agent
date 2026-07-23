"""
Market Benchmarking Engine (SOP Step 7 + 2026-07-22 client requirement).
"Whatever we have in our database, we want to benchmark it on what else they
can find within the market" — every recommended package is compared against
market comparables before it can reach a client.

Comparables are ingested from CSV files the client drops into drive_input/
(CoreLogic / realestate.com.au exports — Coleen supplies the sources).
Expected columns: suburb, state, bedrooms, price, rent_weekly, land_sqm, source, date_checked
A labelled SAMPLE dataset (data/comparables_sample.csv) is used only when no
real comparables exist, and everything it produces is marked as sample data.

No comparables at all -> the package is classified "Unbenchmarked - Pending
Market Data" and flagged for manual benchmarking. The engine never invents
market prices.
"""

import csv
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional, Any

from config import PROJECT_ROOT, DRIVE_INPUT_DIR
from geo import SuburbGeoIndex

SAMPLE_COMPARABLES = PROJECT_ROOT / "data" / "comparables_sample.csv"


class BenchmarkEngine:
    def __init__(self, geo_index: Optional[SuburbGeoIndex] = None):
        self.geo = geo_index or SuburbGeoIndex()
        self.comparables: List[Dict[str, Any]] = []
        self.using_sample_data = False
        self._load_comparables()

    def _load_comparables(self):
        real_files = sorted(DRIVE_INPUT_DIR.glob("comparables*.csv")) if DRIVE_INPUT_DIR.exists() else []
        files = real_files
        if not files and SAMPLE_COMPARABLES.exists():
            files = [SAMPLE_COMPARABLES]
            self.using_sample_data = True
        for path in files:
            with open(path, encoding='utf-8', errors='ignore') as f:
                for row in csv.DictReader(f):
                    try:
                        self.comparables.append({
                            'suburb': (row.get('suburb') or '').strip().title(),
                            'state': (row.get('state') or '').strip().upper(),
                            'bedrooms': int(float(row.get('bedrooms') or 0)),
                            'price': float(row.get('price') or 0),
                            'rent_weekly': float(row.get('rent_weekly') or 0) or None,
                            'land_sqm': float(row.get('land_sqm') or 0) or None,
                            'source': (row.get('source') or path.name).strip(),
                            'date_checked': (row.get('date_checked') or '').strip(),
                        })
                    except (ValueError, TypeError):
                        continue
        self.comparables = [c for c in self.comparables if c['price'] > 0 and c['suburb']]

    def find_comparables(self, suburb: str, state: str, bedrooms: int,
                         radius_km: float = 15.0, max_results: int = 5) -> List[Dict[str, Any]]:
        """Comparables in the same suburb first, then within radius_km, similar bedrooms (+/-1)."""
        hits = []
        for c in self.comparables:
            if c['state'] != state.upper() or abs(c['bedrooms'] - bedrooms) > 1:
                continue
            if c['suburb'].lower() == suburb.strip().lower():
                dist = 0.0
            else:
                dist = self.geo.distance_between(suburb, c['suburb'], state)
                if dist is None or dist > radius_km:
                    continue
            hits.append({**c, 'distance_km': dist})
        hits.sort(key=lambda x: (x['distance_km'], abs(x['bedrooms'] - bedrooms)))
        return hits[:max_results]

    def benchmark_package(self, suburb: str, state: str, bedrooms: int,
                          realistic_total_price: float) -> Dict[str, Any]:
        comps = self.find_comparables(suburb, state, bedrooms)

        if not comps:
            return {
                'benchmarked': False,
                'classification': 'Unbenchmarked - Pending Market Data',
                'value_score_contribution': 7.5,  # neutral — never rewards or punishes missing data
                'comparables': [],
                'avg_market_price': None,
                'variance_pct': None,
                'needs_manual_benchmark': True,
                'data_note': 'No market comparables available for this area. Manual CoreLogic/REA benchmark required before client presentation.',
            }

        avg_price = mean(c['price'] for c in comps)
        variance_pct = ((realistic_total_price - avg_price) / avg_price) * 100.0

        # SOP Step 7 classification bands
        if variance_pct < -5.0:
            classification, score = 'Below Market Value', 15.0
        elif variance_pct <= 5.0:
            classification, score = 'Competitive Market Value', 12.5
        elif variance_pct <= 12.0:
            classification, score = 'Slightly Above Market', 10.0
        else:
            classification, score = 'Poor Value', 5.0

        note = f"Benchmarked against {len(comps)} comparable(s)."
        if self.using_sample_data:
            note += " WARNING: SAMPLE comparables data - replace with CoreLogic/REA exports in drive_input/ before client use."

        return {
            'benchmarked': True,
            'classification': classification,
            'value_score_contribution': score,
            'comparables': comps[:3],
            'avg_market_price': round(avg_price, 0),
            'variance_pct': round(variance_pct, 1),
            'needs_manual_benchmark': self.using_sample_data,
            'data_note': note,
        }
