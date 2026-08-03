"""
Suburb Geolocation & Distance Search Module.
Implements the distance-based search requested in the 2026-07-22 client review:
"within N km of a target suburb" when the exact suburb has no stock.

Backed by data/au_suburbs.csv (17,500+ Australian localities with coordinates,
derived from the public australian_postcodes dataset).
"""

import csv
import math
import re
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
        # {suburb_lower: {states}} — the reverse lookup, for answering "which state is
        # this suburb in?" when no state is known yet. Roughly a fifth of Australian
        # locality names exist in more than one state, so the caller has to be told when
        # the answer is ambiguous rather than handed the first match.
        self._states_by_suburb: Dict[str, set] = {}
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
                self._states_by_suburb.setdefault(suburb.lower(), set()).add(state)

    @property
    def loaded(self) -> bool:
        return bool(self._index)

    def states_for_suburb(self, suburb: str) -> List[str]:
        """Every state that has a locality of this name, sorted. Empty if unknown.

        A single element means the suburb identifies its state on its own; several means
        the name is shared and something else has to break the tie.
        """
        if not suburb:
            return []
        return sorted(self._states_by_suburb.get(suburb.strip().lower(), ()))

    def locate(self, suburb: str, state: str) -> Optional[Tuple[float, float]]:
        return self._index.get((suburb.strip().lower(), state.strip().upper()))

    def resolve_locality(self, suburb: str, state: str = "") -> str:
        """The real locality inside a suburb value, or '' if there is not one.

        The suburb column collects whatever landed in that position of a stocklist.
        Some of it is a locality, some is an estate glued to one — "Stage 5A,
        Greenbank", "Waler Heights, Mango Hill", "Walloon (Owner Occupiers Only),
        Walloon" — and 59% is neither: postcodes ("2026"), stray words ("offer"),
        header fragments ("IN TERNAL BALCONY TOTAL"), councils and regions.

        The locality is the LAST comma-separated part. That is a fact about how an
        address is written, not a guess about which word looks like a suburb — which
        matters, because guessing picks the street out of "COLEDALE DRIVE, MELTON".

        Lives here so the scoring pipeline and the benchmark agree. They did not:
        _candidates.py ungluedthe composite and benchmark_buildings.py did not, so a
        lot in "Stage 5A, Greenbank" was shortlisted with no benchmark and its peers
        were never grouped with the other Greenbank stock.
        """
        raw = str(suburb or "").strip()
        if not raw:
            return ""
        if self.locate(raw, state or ""):
            return raw
        # Colon is used the same way as a comma by several stocklists — "Estate : Dream
        # Sebastopol" — and a trailing postcode blocks the match on the part that holds
        # the locality: "Wyndham Gardens, Wyndham Vale 3024". Between them these two
        # shapes cost 43 real VIC lots, each of which was dropped from every search.
        for part in reversed([p.strip(" ()") for p in re.split(r"[,:]", raw) if p.strip(" ()")]):
            part = re.sub(r"\s*\([^)]*\)\s*", " ", part).strip()
            if part and part.lower() != raw.lower() and self.locate(part, state or ""):
                return part
            # Only ever a trailing postcode, and only when what remains resolves in the
            # index. Nothing is inferred: an unmatched remainder is still rejected.
            trimmed = re.sub(r"\s+\d{4}$", "", part).strip()
            if trimmed and trimmed != part and self.locate(trimmed, state or ""):
                return trimmed
            # "Dream Sebastopol" / "Pinnacle Smythes Creek" — an estate name prepended to
            # the locality. Walk the words from the right and keep the longest tail that
            # is a real locality, so "Smythes Creek" wins over "Creek".
            words = part.split()
            for start in range(1, len(words)):
                tail = " ".join(words[start:])
                if self.locate(tail, state or ""):
                    return tail
        return ""

    def find_suburb_in_text(self, text: str, state: str = "") -> Optional[str]:
        """Recover a suburb from a free-text address.

        Stocklist rows often carry the locality only inside the address string
        ("LOT 79 STELLA ST, COLAC 3250"), leaving the suburb field empty. Without
        this the property cannot be geocoded, so a distance filter cannot judge it.
        Matches the longest known locality name present, preferring `state`.
        """
        if not text or not self._index:
            return None
        words = re.findall(r"[A-Za-z][A-Za-z'\-]+", text)
        states = [state.strip().upper()] if state else []
        states += [s for s in self._by_state.keys() if s not in states]
        # try 3-word, then 2-word, then single-word candidates (longest match wins)
        for size in (3, 2, 1):
            for i in range(len(words) - size + 1):
                cand = " ".join(words[i:i + size])
                for st in states:
                    if (cand.lower(), st) in self._index:
                        return cand.title()
        return None

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
