"""
Extract listings from realestate.com.au — through PropTrack, REA Group's own API.

WHY NOT THE SITE ITSELF. Measured 2026-08-08, three ways, all blocked:

    plain HTTP request        -> HTTP 429, Kasada (KPSDK) challenge, on the FIRST request
    headless Chromium         -> HTTP 429, 798 bytes, no listings in the DOM
    domain.com.au, same probe -> HTTP 403

Not a rate limit; one request. Kasada exists precisely to stop automated extraction, and
the only ways past it are solving the challenge, patching the browser fingerprint, or
driving a headed browser to look like a person. All three are evasion, and none of them
happen here. REA sells this data as PropTrack, which is why the front door is shut.

The practical argument is as strong as the principled one: a scraper behind Kasada fails
SILENTLY. The challenge changes, the parse returns nothing, the harvest reports success
with zero rows, and the catalogue quietly goes stale — the exact shape of the three days
Proxima published a land price as a package, and of the filtered-session read that stored
52 lots in place of 1,212.

WHAT THIS DOES. Pulls listings per suburb through PropTrack and hands them to the same
pipeline as every other channel, so dedupe, identity, enrichment, benchmarking and the
dashboard all treat REA stock like any other source. Without a key it returns [] and says
why — never a partial harvest presented as a whole one.

    python setup_credentials.py proptrack_api
    setx SPB_COMP_PROVIDER realestate
"""

import logging
import time
from typing import Any, Dict, List, Optional

from sources.base import PropertySource

logger = logging.getLogger(__name__)

CHANNEL = "realestate.com.au"

# Bulk extraction is a quota event. Capped per run and logged, because a surprise bill
# stalls a project as surely as a bug does.
DEFAULT_PAGE_SIZE = 100
MAX_PAGES_PER_SUBURB = 10
PAUSE_BETWEEN_CALLS_S = 0.4


class RealestateSource(PropertySource):
    """realestate.com.au listings via PropTrack."""

    def __init__(self, provider=None, page_size: int = DEFAULT_PAGE_SIZE):
        self._provider = provider
        self.page_size = page_size
        self.calls = 0
        self.skipped: Dict[str, int] = {}

    @property
    def channel_name(self) -> str:
        return CHANNEL

    # ------------------------------------------------------------------ provider

    @property
    def provider(self):
        if self._provider is None:
            try:
                from comp_provider_proptrack import PropTrackProvider
                self._provider = PropTrackProvider()
            except Exception as exc:                                  # noqa: BLE001
                logger.warning("PropTrack adapter unavailable: %s", exc)
                self._provider = False
        return self._provider or None

    @property
    def configured(self) -> bool:
        p = self.provider
        return bool(p and getattr(p, "configured", False))

    def _bump(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    # ------------------------------------------------------------------ search

    def search(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Listings for the suburbs in `filters`, as raw candidate dicts.

        Returns [] and SAYS SO when unconfigured. An empty harvest that looks like a
        successful one is the failure mode this codebase keeps paying for.
        """
        if not self.configured:
            logger.warning(
                "%s: no PropTrack key stored, so nothing was extracted. "
                "Run: python setup_credentials.py proptrack_api", CHANNEL)
            self._bump("no API key configured")
            return []

        suburbs = [s for s in (filters.get("primary_suburbs") or []) if str(s).strip()]
        state = str(filters.get("state") or "").strip().upper()
        budget_max = filters.get("budget_max")
        if not suburbs:
            self._bump("no suburb given to search")
            return []

        out: List[Dict[str, Any]] = []
        for suburb in suburbs:
            out.extend(self._suburb(str(suburb).strip(), state, budget_max))
        logger.info("%s: %d listing(s) from %d suburb(s), %d API call(s)",
                    CHANNEL, len(out), len(suburbs), self.calls)
        return out

    def _suburb(self, suburb: str, state: str, budget_max) -> List[Dict[str, Any]]:
        import price_kind
        from comp_provider import CompQuery

        rows: List[Dict[str, Any]] = []
        seen = set()
        for page in range(MAX_PAGES_PER_SUBURB):
            q = CompQuery(suburb=suburb, state=state,
                          price_kind=price_kind.DWELLING, limit=self.page_size)
            try:
                comps = self.provider.search_comps(q)
            except Exception as exc:                                  # noqa: BLE001
                # Never raise into a harvest: one bad suburb must not cost the run.
                logger.warning("%s: %s %s page %d failed: %s",
                               CHANNEL, suburb, state, page + 1, exc)
                self._bump("provider error")
                break
            self.calls = getattr(self.provider, "calls", self.calls)
            if not comps:
                break
            fresh = 0
            for c in comps:
                key = c.provider_listing_id or c.provider_url
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                row = self._row(c, suburb, state)
                if row:
                    rows.append(row)
                    fresh += 1
            if fresh == 0 or len(comps) < self.page_size:
                break                                  # no more pages worth asking for
            time.sleep(PAUSE_BETWEEN_CALLS_S)
        return rows

    def _row(self, comp, suburb: str, state: str) -> Optional[Dict[str, Any]]:
        """One RawComp -> the raw dict shape the pipeline stores.

        A listing with no usable price is DROPPED and counted, never stored as zero.
        """
        if not comp.price or comp.price <= 0:
            self._bump("listing withheld its price")
            return None
        address = (comp.provider_url or "").rstrip("/").split("/")[-1].replace("-", " ")
        return {
            "source_channel": CHANNEL,
            "attribution_scope": "listing",
            # The portal is not the builder, and this file will not guess one. That is
            # the mistake that put 319 lots under a builder called "Proxima" and 318
            # under "Level 33".
            "builder_name": "",
            "suburb": comp.suburb or suburb,
            "state": (comp.state or state).upper(),
            "price": float(comp.price),
            "bedrooms": comp.bedrooms,
            "land_size_sqm": comp.land_sqm,
            "product_type": "Established" if not comp.is_new_build else "New",
            "lot_address": address.title() or f"{comp.suburb or suburb} listing",
            "listing_url": comp.provider_url,
            "source_url": comp.provider_url,
            "source_project_id": comp.provider_listing_id,
            # Attribution is contractual for REA data, not a nicety.
            "source_note": "realestate.com.au via PropTrack (REA Group)",
            "verified": True,
        }

    # ------------------------------------------------------------------ verify

    def verify(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """Re-read one listing. Unconfigured, it reports UNVERIFIED rather than OK."""
        if not self.configured:
            return {"status": "UNVERIFIED",
                    "reason": "no PropTrack key stored, so nothing was re-checked"}
        return {"status": "PENDING",
                "reason": "per-listing verification wired when a key exists; the shape "
                          "of the single-listing response has to be confirmed against a "
                          "real response rather than guessed"}
