"""
Suburb Geolocation & Distance Search Module.
Implements the distance-based search requested in the 2026-07-22 client review:
"within N km of a target suburb" when the exact suburb has no stock.

Backed by data/au_suburbs.csv (17,500+ Australian localities with coordinates,
derived from the public australian_postcodes dataset).
"""

import csv
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import PROJECT_ROOT

SUBURBS_CSV = PROJECT_ROOT / "data" / "au_suburbs.csv"


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    p = math.radians
    a = math.sin(p(lat2 - lat1) / 2) ** 2 + \
        math.cos(p(lat1)) * math.cos(p(lat2)) * math.sin(p(lng2 - lng1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


class SuburbGeoIndex:
    def __init__(self, csv_path: Optional[Path] = None):
        self.csv_path = csv_path or SUBURBS_CSV
        # {(suburb_lower, state): (lat, lng)}
        self._index: Dict[Tuple[str, str], Tuple[float, float]] = {}
        self._by_state: Dict[str, List[Tuple[str, float, float]]] = {}
        self._load()

    def _load(self):
        if not self.csv_path.exists():
            return
        with open(self.csv_path, encoding='utf-8') as f:
            for row in csv.DictReader(f):
                suburb = row['suburb'].strip()
                state = row['state'].strip().upper()
                lat, lng = float(row['lat']), float(row['lng'])
                self._index[(suburb.lower(), state)] = (lat, lng)
                self._by_state.setdefault(state, []).append((suburb, lat, lng))

    @property
    def loaded(self) -> bool:
        return bool(self._index)

    def locate(self, suburb: str, state: str) -> Optional[Tuple[float, float]]:
        return self._index.get((suburb.strip().lower(), state.strip().upper()))

    def distance_between(self, suburb_a: str, suburb_b: str, state: str) -> Optional[float]:
        a = self.locate(suburb_a, state)
        b = self.locate(suburb_b, state)
        if not a or not b:
            return None
        return round(haversine_km(a[0], a[1], b[0], b[1]), 1)

    def suburbs_within_radius(self, origin_suburb: str, state: str, radius_km: float) -> List[Dict]:
        """
        All suburbs in `state` within radius_km of origin_suburb,
        sorted nearest-first. Origin itself is included at 0.0 km.
        """
        origin = self.locate(origin_suburb, state)
        if not origin or radius_km <= 0:
            return []
        out = []
        for suburb, lat, lng in self._by_state.get(state.strip().upper(), []):
            d = haversine_km(origin[0], origin[1], lat, lng)
            if d <= radius_km:
                out.append({'suburb': suburb, 'state': state.upper(), 'distance_km': round(d, 1)})
        out.sort(key=lambda x: x['distance_km'])
        return out

    def expand_search_suburbs(self, primary_suburbs: List[str], state: str, radius_km: Optional[float]) -> List[Dict]:
        """
        Builds the full search area: every primary suburb plus, when a radius is
        given, every suburb within radius_km of each primary suburb.
        Returns [{'suburb', 'state', 'distance_km', 'origin'}] nearest-first, deduped.
        """
        seen: Dict[str, Dict] = {}
        for origin in primary_suburbs:
            seen.setdefault(origin.strip().lower(), {
                'suburb': origin.strip().title(), 'state': state.upper(),
                'distance_km': 0.0, 'origin': origin.strip().title()
            })
        if radius_km and radius_km > 0:
            for origin in primary_suburbs:
                for hit in self.suburbs_within_radius(origin, state, radius_km):
                    key = hit['suburb'].lower()
                    if key not in seen or hit['distance_km'] < seen[key]['distance_km']:
                        seen[key] = {**hit, 'origin': origin.strip().title()}
        return sorted(seen.values(), key=lambda x: x['distance_km'])
