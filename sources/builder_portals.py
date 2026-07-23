"""
Direct Builder Portal Search Source — LIVE Playwright scraper.

For builders NOT on E-Agent that have their own credentialed portal (Hermitage,
Bathla, FRD, Torsion, Paramount, ...). Iterates the approved builder directory,
logs into each portal with the credentials stored against that builder, and
scrapes its stock list.

Credentials come from the builder registry (Book1 CSV / env). A builder with no
portal URL or no credentials is skipped with a logged reason. A portal whose
selectors are unverified logs a "needs re-mapping" warning. Nothing is ever
fabricated — a failed portal contributes zero listings, not fake ones.
"""

import logging
from datetime import datetime
from typing import List, Dict, Any

from sources.base import PropertySource
from sources.scraper_base import PlaywrightScraper, parse_price, parse_int, PLAYWRIGHT_AVAILABLE
from sources.portal_config import config_for_url
from builder_registry import BuilderRegistry

logger = logging.getLogger("spb.scraper.portals")


class BuilderPortalSource(PropertySource):
    def __init__(self, registry: BuilderRegistry = None):
        self.registry = registry or BuilderRegistry()

    @property
    def channel_name(self) -> str:
        return "Direct Builder Portal (live)"

    def _login(self, scraper: PlaywrightScraper, cfg, email: str, password: str) -> bool:
        page = scraper.page
        scraper.goto(cfg.login_url)
        if cfg.logged_in_selector and scraper.is_logged_in(cfg.logged_in_selector):
            return True
        try:
            if cfg.open_login_selector:
                try:
                    page.click(cfg.open_login_selector, timeout=6000)
                    scraper.throttle()
                except Exception:
                    pass
            page.fill(cfg.email_selector, email, timeout=10000)
            page.fill(cfg.password_selector, password, timeout=10000)
            page.click(cfg.submit_selector, timeout=10000)
            page.wait_for_load_state("networkidle", timeout=20000)
            scraper.throttle()
        except Exception as e:
            logger.error("[%s] login interaction failed: %s", cfg.name, e)
            return False
        if cfg.logged_in_selector and not scraper.is_logged_in(cfg.logged_in_selector):
            logger.error("[%s] login did not reach an authenticated state.", cfg.name)
            return False
        return True

    def _scrape_builder(self, builder: Dict[str, Any], filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        cfg = config_for_url(builder["portal_url"])
        if not cfg:
            return []
        if not cfg.verified:
            logger.warning("[%s] portal selectors are UNVERIFIED — confirm against live DOM in portal_config.py.", cfg.name)
        email = builder.get("portal_login_email") or ""
        password = builder.get("portal_login_password") or ""
        if not password:
            logger.warning("[%s] skipped: no portal password stored for this builder.", builder["builder_name"])
            return []

        session_name = "portal_" + "".join(c for c in builder["builder_name"].lower() if c.isalnum())
        results: List[Dict[str, Any]] = []
        try:
            scraper = PlaywrightScraper(session_name=session_name)
            with scraper.session():
                if not self._login(scraper, cfg, email, password):
                    return []
                scraper.goto(cfg.listings_url)
                cards = scraper.page.query_selector_all(cfg.listing_card_selector)
                if not cards:
                    logger.warning("[%s] no listing cards matched '%s' — selectors need re-mapping.",
                                   cfg.name, cfg.listing_card_selector)
                    return []
                fs = cfg.field_selectors
                for card in cards:
                    title = scraper.text_or_none(card, fs.get("title", "h3"))
                    price = parse_price(scraper.text_or_none(card, fs.get("price", ".price")))
                    if not title or not price:
                        continue
                    link_el = card.query_selector(cfg.link_selector)
                    href = link_el.get_attribute("href") if link_el else cfg.listings_url
                    results.append({
                        "lot_address": title,
                        "suburb": scraper.text_or_none(card, fs.get("suburb", ".suburb")) or "",
                        "state": builder["states"][0] if builder.get("states") else filters.get("state", ""),
                        "builder_name": builder["builder_name"],
                        "advertised_package_price": price,
                        "bedrooms": parse_int(scraper.text_or_none(card, fs.get("beds", ".beds"))),
                        "bathrooms": parse_int(scraper.text_or_none(card, fs.get("baths", ".baths"))),
                        "car_spaces": parse_int(scraper.text_or_none(card, fs.get("cars", ".cars"))),
                        "source_channel": self.channel_name,
                        "source_url_or_ref": href,
                        "date_checked": datetime.now().strftime("%d/%m/%Y"),
                        "verified": True,
                    })
            logger.info("[%s] captured %d live listing(s).", cfg.name, len(results))
        except Exception as e:
            logger.exception("[%s] scraper crashed: %s", cfg.name, e)
            return []
        return results

    def search(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not PLAYWRIGHT_AVAILABLE:
            logger.error("Builder portal search skipped: Playwright not installed.")
            return []
        state = filters.get("state", "").upper()
        max_budget = float(filters.get("budget_max", 10_000_000))

        # Full coverage: every state-eligible builder with a direct portal (not on E-Agent).
        targets = [
            b for b in self.registry.get_builders_by_state(state)
            if b.get("portal_url") and not b.get("is_on_e_agent")
        ]
        if not targets:
            logger.info("Builder portal search: no direct-portal builders in scope for %s.", state)
            return []

        all_results: List[Dict[str, Any]] = []
        for builder in targets:
            all_results.extend(self._scrape_builder(builder, filters))

        return [r for r in all_results if r["advertised_package_price"] <= max_budget + 50_000]

    def verify(self, package: Dict[str, Any]) -> Dict[str, Any]:
        # Per-builder re-verification would re-login to that builder's portal;
        # until selectors are confirmed we mark for human confirmation.
        return {"verified": False, "status": "Pending Confirmation", "price_change": 0.0}
