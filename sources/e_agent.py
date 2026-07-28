"""
E-Agent Portal Search Source — LIVE Playwright scraper.

Primary source for approved builders listed on E-Agent (e-agent.com.au, a Wix
members site). Logs in with credentials from the environment, scrapes the
Access Projects stock list, and returns real candidate packages.

No credentials, Playwright missing, or login/DOM failure -> logs the reason and
returns [] (never fabricated data). Selectors live in portal_config.EAGENT_CONFIG.
"""

import logging
import re
from datetime import datetime
from typing import List, Dict, Any

import config
from sources.base import PropertySource
from sources.scraper_base import PlaywrightScraper, ScraperError, parse_price, parse_int, PLAYWRIGHT_AVAILABLE, SESSION_DIR
from sources.portal_config import EAGENT_CONFIG
from sources.adaptive_extract import extract_listings
from sources.spreadsheet_extract import extract_stocklist
from secrets_store import get_credentials

logger = logging.getLogger("spb.scraper.eagent")


class EAgentSource(PropertySource):
    def __init__(self, registry=None):
        self.cfg = EAGENT_CONFIG
        # Credentials resolve at RUN TIME: OS vault -> env/.env -> vendor CSV.
        # Nothing is hardcoded and the plaintext never has to live in the repo.
        csv_user = csv_pass = ""
        if registry is not None:
            for b in registry.get_all_builders():
                if b.get("is_on_e_agent") and b.get("portal_login_email") and b.get("portal_login_password") \
                        and "e-agent" in (b.get("portal_url") or "").lower():
                    csv_user, csv_pass = b["portal_login_email"], b["portal_login_password"]
                    break
        csv_user = csv_user or config.E_AGENT_USERNAME
        csv_pass = csv_pass or config.E_AGENT_PASSWORD
        self.username, self.password, self.cred_source = get_credentials("e_agent", (csv_user, csv_pass))
        if self.username:
            logger.info("E-Agent credentials resolved from %s", self.cred_source)

    @property
    def channel_name(self) -> str:
        return "E-Agent Portal (live)"

    def _login(self, scraper: PlaywrightScraper) -> bool:
        page = scraper.page
        scraper.goto(self.cfg.login_url)
        if scraper.is_logged_in(self.cfg.logged_in_selector):
            return True  # reused a saved session (created by portal_login.py)
        if not (self.username and self.password):
            logger.error("E-Agent: no saved session and no credentials. "
                         "Run: python portal_login.py e_agent")
            return False
        try:
            # E-Agent is a Wix members site: the page opens on a SIGN-UP panel and the
            # email/password fields only exist after clicking through
            # "Log In" -> "Log in with Email". The modal is slow, so click, wait, and
            # re-check for the password field rather than assuming one click is enough.
            for sel in ("button:has-text('Log In')", "button:has-text('Already a member')",
                        "a:has-text('Log In')", "button:has-text('Log in with Email')",
                        "button:has-text('Sign in with email')"):
                if page.query_selector(self.cfg.password_selector):
                    break
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        el.click()
                        page.wait_for_timeout(3000)
                except Exception:
                    continue
            try:
                page.wait_for_selector(self.cfg.password_selector, timeout=12000)
            except Exception:
                logger.error("E-Agent: login form never appeared (site layout may have changed).")
                return False
            page.fill(self.cfg.email_selector, self.username, timeout=10000)
            page.fill(self.cfg.password_selector, self.password, timeout=10000)
            page.click(self.cfg.submit_selector, timeout=10000)
            page.wait_for_load_state("networkidle", timeout=20000)
            scraper.throttle()
        except Exception as e:
            logger.error("E-Agent login interaction failed: %s", e)
            return False
        if not scraper.is_logged_in(self.cfg.logged_in_selector):
            logger.error("E-Agent login did not reach an authenticated state (check credentials/selectors).")
            return False
        return True

    def _scrape_stocklist_files(self, scraper: PlaywrightScraper) -> List[Dict[str, Any]]:
        """E-Agent publishes stock as downloadable XLSX/PDF stocklists per state and
        builder rather than as HTML listings — download and parse them."""
        page = scraper.page
        try:
            links = page.evaluate(
                """() => [...document.querySelectorAll('a[href*="_files/ugd"]')]
                       .map(a => ({label: (a.innerText||'').trim().slice(0,40), href: a.href}))"""
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        seen = set()
        for l in links or []:
            href = l.get("href", "")
            if not href or href in seen or not re.search(r"\.(xlsx|xls|pdf|csv)(\?|$)", href, re.I):
                continue
            seen.add(href)
            try:
                data = page.context.request.get(href, timeout=config.SCRAPER_NAV_TIMEOUT_MS).body()
            except Exception as e:
                logger.warning("E-Agent: could not download stocklist %s: %s", l.get("label"), e)
                continue
            got = extract_stocklist(data, source_label=href, builder_hint="")
            for g in got:
                g["source_channel"] = self.channel_name
                g["date_checked"] = datetime.now().strftime("%d/%m/%Y")
                g["verified"] = True
                g["stocklist"] = l.get("label", "")
            out.extend(got)
            scraper.throttle()
        logger.info("E-Agent: %d listing(s) from %d stocklist file(s).", len(out), len(seen))
        return out

    def _scrape_listings(self, scraper: PlaywrightScraper) -> List[Dict[str, Any]]:
        page = scraper.page
        scraper.goto(self.cfg.listings_url)
        # Stocklist FILES are E-Agent's real inventory channel — try them first.
        from_files = self._scrape_stocklist_files(scraper)
        if from_files:
            return from_files
        cards = page.query_selector_all(self.cfg.listing_card_selector)
        if not cards:
            # No hand-mapped selector matched — infer the listing structure from the
            # page itself instead of failing (no per-portal mapping required).
            adaptive = extract_listings(page, builder_hint="", state_hint="")
            if adaptive:
                logger.info("E-Agent: adaptive extractor found %d listing(s).", len(adaptive))
                for a in adaptive:
                    a["source_channel"] = self.channel_name
                    a["date_checked"] = datetime.now().strftime("%d/%m/%Y")
                    a["verified"] = True
                return adaptive
            logger.warning("E-Agent: no listings found on %s (page may need a different stock-list URL).",
                           self.cfg.listings_url)
            return []
        results = []
        fs = self.cfg.field_selectors
        for card in cards:
            title = scraper.text_or_none(card, fs.get("title", "h2"))
            price = parse_price(scraper.text_or_none(card, fs.get("price", ".price")))
            if not title or not price:
                continue
            link_el = card.query_selector(self.cfg.link_selector)
            href = link_el.get_attribute("href") if link_el else self.cfg.listings_url
            results.append({
                "lot_address": title,
                "suburb": scraper.text_or_none(card, fs.get("suburb", ".suburb")) or "",
                "builder_name": "",  # E-Agent aggregates many builders; resolved downstream by registry
                "advertised_package_price": price,
                "bedrooms": parse_int(scraper.text_or_none(card, fs.get("beds", ".beds"))),
                "bathrooms": parse_int(scraper.text_or_none(card, fs.get("baths", ".baths"))),
                "car_spaces": parse_int(scraper.text_or_none(card, fs.get("cars", ".cars"))),
                "source_channel": self.channel_name,
                "source_url_or_ref": href,
                "date_checked": datetime.now().strftime("%d/%m/%Y"),
                "verified": True,  # scraped live from the portal this run
            })
        logger.info("E-Agent: captured %d live listing(s).", len(results))
        return results

    def search(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not PLAYWRIGHT_AVAILABLE:
            logger.error("E-Agent search skipped: Playwright not installed.")
            return []
        # A saved session (from portal_login.py) is enough — credentials optional.
        has_session = (SESSION_DIR / "e_agent.json").exists()
        if not has_session and not (self.username and self.password):
            logger.warning("E-Agent skipped: no saved session and no credentials. "
                           "Run: python portal_login.py e_agent")
            return []
        try:
            scraper = PlaywrightScraper(session_name="e_agent")
            with scraper.session():
                # E-Agent shows no reliable "Log Out" marker, so authentication is
                # judged FUNCTIONALLY: if the stocklist files are reachable we are in.
                # Only log in when they are not.
                scraper.goto(self.cfg.listings_url)
                listings = self._scrape_stocklist_files(scraper)
                if not listings:
                    logger.info("E-Agent: no stocklists visible anonymously — logging in.")
                    if not self._login(scraper):
                        return []
                    listings = self._scrape_listings(scraper)
        except ScraperError as e:
            logger.error("E-Agent scraper error: %s", e)
            return []
        except Exception as e:
            logger.exception("E-Agent scraper crashed: %s", e)
            return []

        max_budget = float(filters.get("budget_max", 10_000_000))
        suburbs = [s.lower() for s in filters.get("primary_suburbs", [])]
        out = []
        for r in listings:
            if r["advertised_package_price"] > max_budget + 50_000:
                continue
            if suburbs and r.get("suburb") and r["suburb"].lower() not in suburbs:
                continue
            out.append(r)
        return out

    def verify(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """Re-open the listing URL and confirm it still exists / same price."""
        url = package.get("source_url_or_ref")
        if not (PLAYWRIGHT_AVAILABLE and self.username and url and url.startswith("http")):
            return {"verified": False, "status": "Pending Confirmation", "price_change": 0.0}
        try:
            scraper = PlaywrightScraper(session_name="e_agent")
            with scraper.session():
                if not self._login(scraper):
                    return {"verified": False, "status": "Pending Confirmation", "price_change": 0.0}
                scraper.goto(url)
                live_price = parse_price(scraper.text_or_none(scraper.page, self.cfg.field_selectors.get("price", ".price")))
            old = float(package.get("advertised_package_price", 0) or 0)
            change = (live_price - old) if (live_price and old) else 0.0
            return {
                "verified": live_price is not None,
                "status": "Verified" if live_price is not None else "Unavailable",
                "date_checked": datetime.now().strftime("%d/%m/%Y"),
                "price_change": change,
            }
        except Exception as e:
            logger.error("E-Agent verify failed: %s", e)
            return {"verified": False, "status": "Pending Confirmation", "price_change": 0.0}
