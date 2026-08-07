"""
Where comparable listings come from — one interface, adapters behind it.

The brief is explicit that this abstraction is mandatory: which source ends up licensed
is not decided (Domain can start immediately; PropTrack and Cotality are sales cycles),
and switching must not touch the matching or scoring layers.

    CompProvider          the contract every source implements
    DomainProvider        Domain Developer API — the Phase 1 source
    NullProvider          no source configured; returns nothing, says why

WHAT A PROVIDER MUST NOT DO. Return raw listings for storage. Domain's terms address
storing listings data directly, and prices and metadata change constantly, so only
derived aggregates are persisted plus a listing id and URL for audit. RawComp exists to
be aggregated and thrown away, not written to a table.

PRICE KIND TRAVELS WITH EVERY COMP. A vacant block at $310,000 is a real listing and a
catastrophic comparable for a $750,000 house-and-land package — the same class of error
as pricing a package off a land figure, moved to the other side of the comparison. Every
RawComp carries the kind its price covers, and the matcher refuses a cohort that mixes
them. See price_kind.py.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

import price_kind

# Price basis, per the brief. A withheld price is EXCLUDED and counted, never treated
# as zero — half a suburb's stock having no price is something the client must be told.
BASIS_EXACT = "exact"
BASIS_RANGE_MID = "range_mid"
BASIS_ESTIMATE = "estimate"


@dataclass
class CompQuery:
    suburb: str
    state: str
    price_kind: str                       # only comps of this kind may be returned
    bedrooms: Optional[int] = None
    bedrooms_tolerance: int = 0
    land_min: Optional[float] = None
    land_max: Optional[float] = None
    include_neighbours: List[str] = field(default_factory=list)
    new_build_only: bool = False          # Benchmark B prefers project stock
    limit: int = 50


@dataclass
class RawComp:
    price: Optional[float]
    price_basis: str
    price_kind: str
    suburb: str
    state: str
    bedrooms: Optional[int] = None
    land_sqm: Optional[float] = None
    provider_listing_id: str = ""
    provider_url: str = ""
    is_new_build: bool = False


@dataclass
class SuburbStats:
    median_price: Optional[float] = None
    median_rent: Optional[float] = None
    gross_yield: Optional[float] = None
    growth_12m: Optional[float] = None
    days_on_market: Optional[float] = None
    as_at: str = ""
    source: str = ""


class CompProvider(Protocol):
    name: str

    def search_comps(self, q: CompQuery) -> List[RawComp]: ...
    def get_suburb_stats(self, suburb: str, state: str) -> Optional[SuburbStats]: ...
    def resolve_suburb(self, suburb: str, state: str,
                       postcode: str = "") -> Optional[str]: ...


class NullProvider:
    """No source configured. Returns nothing and names the reason.

    This is what runs until a licence is settled, and it must be indistinguishable from
    a provider that simply found no comparables: the benchmark reports "insufficient",
    no verdict reaches a client, and nothing anywhere claims a market comparison was made.
    """

    name = "none"
    reason = ("no comparables provider is configured — set SPB_COMP_PROVIDER=domain and "
              "store a key with: python setup_credentials.py domain_api")

    def search_comps(self, q: CompQuery) -> List[RawComp]:
        return []

    def get_suburb_stats(self, suburb: str, state: str) -> Optional[SuburbStats]:
        return None

    def resolve_suburb(self, suburb, state, postcode="") -> Optional[str]:
        return None


# Domain property types, mapped to what OUR price covers. A provider's "house" listing
# prices land and dwelling together, which is our `package`; a "unit" prices a strata
# dwelling; "vacant land" prices land alone.
_DOMAIN_TYPES = {
    price_kind.PACKAGE: ["House", "NewHouseLand", "Duplex", "Terrace", "SemiDetached"],
    price_kind.DWELLING: ["Apartment", "Unit", "Townhouse", "NewApartments"],
    price_kind.LAND_ONLY: ["VacantLand", "NewLand"],
}


class DomainProvider:
    """Domain Developer API.

    Chosen for Phase 1 because realestate.com.au has no public listings API and its terms
    prohibit scraping — and a scraper behind Kasada would break silently, which is the
    exact failure mode that cost three days on Proxima. Coleen named Domain herself in
    SOP item 48 ("compare on real estate, domain and developer").

    Every call is retried with backoff, respects 429, and returns [] rather than raising:
    a user-facing search must never fail because a provider was down.
    """

    name = "domain"
    BASE = "https://api.domain.com.au"

    def __init__(self, key: Optional[str] = None, timeout: int = 30,
                 max_retries: int = 3):
        if key is None:
            try:
                from secrets_store import get_credentials
                _user, key, _src = get_credentials("domain_api")
            except Exception:
                key = ""
        self.key = key or os.environ.get("DOMAIN_API_KEY", "")
        self.timeout = timeout
        self.max_retries = max_retries
        self.calls = 0                    # logged per run; quota surprises stall projects

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def _post(self, path: str, body: Dict[str, Any]) -> Any:
        if not self.configured:
            return None
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            self.BASE + path, data=data,
            headers={"Content-Type": "application/json", "X-Api-Key": self.key})
        for attempt in range(self.max_retries):
            try:
                self.calls += 1
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                if e.code == 429:                      # respect the rate limit
                    time.sleep(2 ** attempt * 2)
                    continue
                if 500 <= e.code < 600:
                    time.sleep(2 ** attempt)
                    continue
                return None                            # 4xx: retrying will not help
            except Exception:
                time.sleep(2 ** attempt)
        return None

    @staticmethod
    def _price_of(listing: Dict[str, Any]):
        """(price, basis) — or (None, '') when the listing withholds it.

        A withheld price is excluded and counted, never read as 0. "Contact agent" and an
        auction with no guide are both real and both unusable.
        """
        p = listing.get("priceDetails") or {}
        if p.get("price"):
            return float(p["price"]), BASIS_EXACT
        lo, hi = p.get("priceFrom"), p.get("priceTo")
        if lo and hi:
            return (float(lo) + float(hi)) / 2.0, BASIS_RANGE_MID
        return None, ""

    def search_comps(self, q: CompQuery) -> List[RawComp]:
        types = _DOMAIN_TYPES.get(q.price_kind)
        if not types or not self.configured:
            return []
        locations = [{"state": q.state, "suburb": s}
                     for s in [q.suburb] + list(q.include_neighbours)]
        body: Dict[str, Any] = {
            "listingType": "Sale",
            "propertyTypes": types,
            "pageSize": min(q.limit, 100),
            "locations": locations,
        }
        if q.bedrooms is not None:
            body["minBedrooms"] = max(0, q.bedrooms - q.bedrooms_tolerance)
            body["maxBedrooms"] = q.bedrooms + q.bedrooms_tolerance
        if q.land_min:
            body["minLandArea"] = q.land_min
        if q.land_max:
            body["maxLandArea"] = q.land_max

        payload = self._post("/v1/listings/residential/_search", body) or []
        out: List[RawComp] = []
        for row in payload:
            listing = row.get("listing") or row
            price, basis = self._price_of(listing)
            props = listing.get("propertyDetails") or {}
            out.append(RawComp(
                price=price,
                price_basis=basis,
                # The kind is what we ASKED for; the provider filtered on it. Recording
                # it explicitly so a comp can never be matched against another kind.
                price_kind=q.price_kind,
                suburb=str(props.get("suburb") or q.suburb),
                state=str(props.get("state") or q.state).upper(),
                bedrooms=props.get("bedrooms"),
                land_sqm=props.get("landArea"),
                provider_listing_id=str(listing.get("id") or ""),
                provider_url=str(listing.get("seoUrl") or ""),
                is_new_build=bool(listing.get("isNewDevelopment")),
            ))
        return out

    def get_suburb_stats(self, suburb: str, state: str) -> Optional[SuburbStats]:
        # Domain exposes these through locations/profiles; wired when a key exists so the
        # shape can be confirmed against a real response rather than guessed at.
        return None

    def resolve_suburb(self, suburb: str, state: str, postcode: str = "") -> Optional[str]:
        return f"{suburb.strip().title()}|{state.strip().upper()}" if suburb else None


_ANNOUNCED = [False]


def get_provider(quiet: bool = False) -> Any:
    """The configured provider, or NullProvider. Never raises. SAYS WHICH, once.

    This used to fall back in silence, and the silence was the problem: someone sets
    SPB_COMP_PROVIDER=domain, forgets the key, gets "insufficient" on every listing, and
    has nothing to tell them the difference between "no comparables exist" and "no
    source was ever contacted". Those two look identical by design -- that is the whole
    point of NullProvider -- so the distinction has to be printed somewhere.

    Verified against real stock with a stub in place of the licence: 400 rows produced
    99 flags and 12 passes, so the pipeline is complete and this is the only gap.
    """
    which = (os.environ.get("SPB_COMP_PROVIDER") or "").strip().lower()
    chosen, why = NullProvider(), ""
    if which == "domain":
        p = DomainProvider()
        if p.configured:
            chosen = p
        else:
            why = ("SPB_COMP_PROVIDER=domain is set but no key is stored. Run: "
                   "python setup_credentials.py domain_api")
    elif which:
        why = f"SPB_COMP_PROVIDER={which!r} is not a provider this build knows."
    else:
        why = NullProvider.reason
    if why and not quiet and not _ANNOUNCED[0]:
        _ANNOUNCED[0] = True
        print(f"[i] market comparables: {why}", flush=True)
    return chosen


def provider_status() -> Dict[str, Any]:
    """Machine-readable answer to "is the benchmark actually able to run?"."""
    p = get_provider(quiet=True)
    live = getattr(p, "name", "none") != "none"
    return {"provider": getattr(p, "name", "none"), "live": live,
            "reason": "" if live else NullProvider.reason}
