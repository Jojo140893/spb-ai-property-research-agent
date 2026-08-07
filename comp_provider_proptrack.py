"""
realestate.com.au data, through REA Group's own API.

WHY THIS AND NOT A SCRAPER. realestate.com.au is the site Coleen named first, and it
cannot be read directly. Probed 2026-08-08: the FIRST unauthenticated request returns
HTTP 429 carrying a Kasada (KPSDK) bot-detection challenge, and domain.com.au returns
403. Not rate limiting — one request. The only way past either is to defeat a
bot-detection system, and that is not something this project builds.

PropTrack is REA Group's own data product and serves the same listings under licence, so
this is the same data through the door that opens.

A scraper would also fail the way this codebase keeps getting burned: SILENTLY. Kasada
changes, the parse returns nothing, the run reports success with an empty comparable set,
and every listing quietly reads "insufficient" — which is indistinguishable from an
honest no-comps answer. That is the same shape as the three days Proxima published a land
price as a package.

Credentials via secrets_store("proptrack_api"), never committed. Unconfigured it behaves
exactly as NullProvider: no comps, no verdict, and nothing anywhere claiming a market
comparison was made.

    python setup_credentials.py proptrack_api
    setx SPB_COMP_PROVIDER proptrack        (or "realestate" — both resolve here)
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

import price_kind
from comp_provider import (BASIS_EXACT, BASIS_RANGE_MID, CompQuery, RawComp,
                           SuburbStats)

# realestate.com.au property types, by what a price COVERS. Same contract as the Domain
# map: a comp may only ever be matched against its own kind, so a vacant block can never
# price a house-and-land package.
PROPTRACK_TYPES = {
    price_kind.PACKAGE: ["house", "townhouse", "villa"],
    price_kind.DWELLING: ["house", "townhouse", "unit", "apartment", "villa"],
    price_kind.LAND_ONLY: ["land"],
}


class PropTrackProvider:
    """CompProvider over PropTrack. Never raises; a provider being down must not fail a
    consultant's search."""

    name = "proptrack"
    BASE = "https://data.proptrack.com"

    def __init__(self, key: Optional[str] = None, timeout: int = 30,
                 max_retries: int = 3):
        if key is None:
            try:
                from secrets_store import get_credentials
                _user, key, _src = get_credentials("proptrack_api")
            except Exception:                                         # noqa: BLE001
                key = ""
        self.key = key or os.environ.get("PROPTRACK_API_KEY", "")
        self.timeout = timeout
        self.max_retries = max_retries
        self.calls = 0            # logged per run: quota surprises stall projects

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def _get(self, path: str, params: Dict[str, Any]) -> Any:
        if not self.configured:
            return None
        url = self.BASE + path + "?" + urllib.parse.urlencode(params, doseq=True)
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + self.key, "Accept": "application/json"})
        for attempt in range(self.max_retries):
            try:
                self.calls += 1
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                if e.code == 429:                     # respect the rate limit
                    time.sleep(2 ** attempt * 2)
                    continue
                if 500 <= e.code < 600:
                    time.sleep(2 ** attempt)
                    continue
                return None                           # 4xx: retrying will not help
            except Exception:                                         # noqa: BLE001
                time.sleep(2 ** attempt)
        return None

    @staticmethod
    def price_of(listing: Dict[str, Any]):
        """(price, basis), or (None, '') when the listing withholds it.

        Withheld is EXCLUDED and counted by the caller, never read as zero. "Contact
        agent" and an auction with no guide are both real and both unusable, and a zero
        would drag every median it touched.
        """
        for key in ("price", "displayPrice", "priceValue"):
            raw = listing.get(key)
            if isinstance(raw, (int, float)) and raw > 0:
                return float(raw), BASIS_EXACT
            if isinstance(raw, dict):
                if raw.get("value"):
                    return float(raw["value"]), BASIS_EXACT
                lo = raw.get("from") or raw.get("min")
                hi = raw.get("to") or raw.get("max")
                if lo and hi:
                    return (float(lo) + float(hi)) / 2.0, BASIS_RANGE_MID
        return None, ""

    def search_comps(self, q: CompQuery) -> List[RawComp]:
        types = PROPTRACK_TYPES.get(q.price_kind)
        if not types or not self.configured:
            return []
        out: List[RawComp] = []
        for suburb in [q.suburb] + list(q.include_neighbours):
            params: Dict[str, Any] = {
                "suburb": suburb,
                "state": q.state,
                "propertyTypes": ",".join(types),
                "pageSize": min(q.limit, 100),
            }
            if q.bedrooms is not None:
                params["minBedrooms"] = max(0, q.bedrooms - q.bedrooms_tolerance)
                params["maxBedrooms"] = q.bedrooms + q.bedrooms_tolerance
            if q.land_min:
                params["minLandSize"] = q.land_min
            if q.land_max:
                params["maxLandSize"] = q.land_max

            payload = self._get("/api/v2/listings", params)
            listings = payload.get("listings", []) if isinstance(payload, dict) else []
            for listing in listings:
                price, basis = self.price_of(listing)
                feats = listing.get("features") or listing.get("attributes") or {}
                addr = listing.get("address") or {}
                out.append(RawComp(
                    price=price,
                    price_basis=basis,
                    # The kind we ASKED for; the provider filtered on it. Recorded
                    # explicitly so a comp can never be matched against another kind.
                    price_kind=q.price_kind,
                    suburb=str(addr.get("suburb") or suburb),
                    state=str(addr.get("state") or q.state).upper(),
                    bedrooms=feats.get("bedrooms"),
                    land_sqm=feats.get("landSize") or feats.get("landArea"),
                    provider_listing_id=str(listing.get("listingId")
                                            or listing.get("id") or ""),
                    # Attribution and click-through are contractual for REA data, so the
                    # source URL travels from the first line rather than being bolted on.
                    provider_url=str(listing.get("listingUrl")
                                     or listing.get("url") or ""),
                    is_new_build=bool(listing.get("isNewDevelopment")
                                      or listing.get("newDevelopment")),
                ))
            if len(out) >= q.limit:
                break
        return out[:q.limit]

    def get_suburb_stats(self, suburb: str, state: str) -> Optional[SuburbStats]:
        """Deliberately unwired.

        PropTrack exposes market indicators on a separate endpoint, and the response
        shape has to be confirmed against a real key rather than guessed. A guessed parse
        that silently returns nothing is worse than an honest None, which is the whole
        lesson of this file. market_open_data.py supplies suburb medians meanwhile.
        """
        return None

    def resolve_suburb(self, suburb: str, state: str,
                       postcode: str = "") -> Optional[str]:
        if not suburb:
            return None
        return suburb.strip().title() + "|" + state.strip().upper()
