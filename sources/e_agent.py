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
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

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

    # JS, not Python: the builder name is only recoverable from the DOM position of
    # the link relative to its section heading, which Playwright cannot express as a
    # selector. Climbs from each stocklist link to the nearest heading ABOVE it —
    # previous siblings first at each level, then up a level.
    _LINKS_WITH_HEADINGS_JS = """() => {
        const HEAD = 'h1,h2,h3,h4,h5,h6,[role="heading"]';
        const txt = e => ((e.innerText || e.textContent || '').replace(/\\s+/g, ' ')).trim();
        const nearest = (el) => {
            let node = el;
            while (node && node !== document.body) {
                let sib = node.previousElementSibling;
                while (sib) {
                    if (sib.matches && sib.matches(HEAD)) { const t = txt(sib); if (t) return t; }
                    const inner = sib.querySelectorAll ? [...sib.querySelectorAll(HEAD)] : [];
                    for (let i = inner.length - 1; i >= 0; i--) {
                        const t = txt(inner[i]); if (t) return t;
                    }
                    sib = sib.previousElementSibling;
                }
                node = node.parentElement;
            }
            return '';
        };
        return [...document.querySelectorAll('a[href*="_files/ugd"]')].map(a => ({
            href: a.href,
            label: txt(a).slice(0, 60),
            heading: nearest(a).slice(0, 80),
        }));
    }"""

    # Headings that are page furniture, not builders. Without this filter the crawl
    # would attribute stock to "Live Packages" — a wrong builder is worse than a blank.
    _NOT_A_BUILDER = re.compile(
        r"^(live\s*packages?|builder\s*info|request\s*(a\s*)?marketing\s*flyer|"
        r"package\s*request|marketing\s*flyer|price\s*list|stock\s*list|available\s*stock|"
        r"house\s*&?\s*land|townhouses?|apartments?|commercial(\s*propert\w*)?|"
        r"dual\s*(key|occupancy)|projects?|our\s*builders?|new\s*(homes?|stock)|"
        r"home|about|contact|log\s*in|login|sign\s*up|access\s*projects|"
        r"victoria|new\s*south\s*wales|queensland|south\s*australia|"
        r"vic|nsw|qld|sa|wa|nt|act|tas)$", re.I)

    # Not every file under a builder's heading is stock. Goldstate Homes publishes a
    # "Builder Info" company document beside its "Live Packages" stocklist; parsing it
    # as stock would put marketing copy in the client's sheet.
    _NOT_STOCK_LABEL = re.compile(
        r"builder\s*info|marketing\s*flyer|package\s*request|eoi|expression\s*of\s*interest|"
        r"brochure|company\s*profile|inclusion|contract|terms|price\s*guide\s*only", re.I)

    def _is_builder_heading(self, heading: str) -> bool:
        h = (heading or "").strip(" -–—:|")
        if not (2 < len(h) <= 60) or not re.search(r"[A-Za-z]{3}", h):
            return False
        return not self._NOT_A_BUILDER.match(h)

    def _stocklist_links(self, scraper: PlaywrightScraper) -> List[Dict[str, str]]:
        try:
            links = scraper.page.evaluate(self._LINKS_WITH_HEADINGS_JS)
        except Exception as e:
            logger.warning("E-Agent: could not read stocklist links: %s", e)
            return []
        out, seen = [], set()
        for l in links or []:
            href = l.get("href", "")
            if not href or href in seen or not re.search(r"\.(xlsx|xls|pdf|csv)(\?|$)", href, re.I):
                continue
            seen.add(href)
            out.append(l)
        return out

    def _parse_stocklist(self, scraper: PlaywrightScraper, link: Dict[str, str],
                         builder_hint: str, scope: str, state_hint: str = "",
                         product_type: str = "") -> List[Dict[str, Any]]:
        href = link.get("href", "")
        try:
            data = scraper.page.context.request.get(
                href, timeout=config.SCRAPER_NAV_TIMEOUT_MS).body()
        except Exception as e:
            logger.warning("E-Agent: could not download stocklist %s: %s", link.get("label"), e)
            return []
        got = extract_stocklist(data, source_label=href, builder_hint=builder_hint)
        for g in got:
            g["source_channel"] = self.channel_name
            g["date_checked"] = datetime.now().strftime("%d/%m/%Y")
            g["verified"] = True
            g["stocklist"] = link.get("label", "")
            g["stocklist_file"] = href
            g["attribution_scope"] = scope
            if builder_hint:
                g["builder_source"] = "e-agent category heading"
            if state_hint:
                g["source_state_hint"] = state_hint
            if product_type:
                g["product_type"] = product_type
        scraper.throttle()
        return got

    def _scrape_stocklist_files(self, scraper: PlaywrightScraper) -> List[Dict[str, Any]]:
        """The POOLED state stocklists on /access-projects. Kept because they are the
        proven source of the existing rows, but they name no builder, so they are
        tagged `state_pooled` and stay a separate identity from per-builder rows."""
        out: List[Dict[str, Any]] = []
        links = self._stocklist_links(scraper)
        for l in links:
            out.extend(self._parse_stocklist(scraper, l, "", "state_pooled"))
        logger.info("E-Agent: %d pooled listing(s) from %d stocklist file(s).", len(out), len(links))
        return out

    def _scrape_category_pages(self, scraper: PlaywrightScraper,
                               seen_files: Optional[set] = None) -> List[Dict[str, Any]]:
        """Per-builder stock, the fix for Coleen's blank builder column.

        Each category page stacks one section per builder, so the builder is the
        nearest heading above its "Live Packages" link. Never guesses: a link whose
        heading is page furniture is skipped and counted, not attributed.
        """
        page = scraper.page
        out: List[Dict[str, Any]] = []
        seen_files = seen_files if seen_files is not None else set()
        builders, unattributed = {}, 0
        for cat in (self.cfg.category_pages or ()):
            try:
                scraper.goto(cat.url)
            except Exception as e:
                logger.warning("E-Agent: category page %s unreachable: %s", cat.url, e)
                continue
            # Route-change guard. E-Agent is a Wix client-side-routed SPA: a failed
            # navigation leaves the PREVIOUS page rendered, which would attribute the
            # same VIC files to NSW, SA and QLD builders in turn.
            want = urlparse(cat.url).path.rstrip("/").lower()
            got_path = urlparse(page.url or "").path.rstrip("/").lower()
            if want != got_path:
                logger.warning("E-Agent: %s did not load (still on %s) — skipping so its "
                               "files are not attributed to the wrong builders.",
                               cat.url, page.url)
                continue
            # A Wix 404 renders AT the requested path, so the route check above passes.
            # Report it, because a silently-dead route looks like "this state has no
            # stock" — which is how the NSW page went unnoticed.
            try:
                if page.query_selector("text=ERROR: PAGE NOT FOUND"):
                    logger.error("E-Agent: %s is a dead route (404) — the config needs "
                                 "updating from the site navigation.", cat.url)
                    continue
            except Exception:
                pass
            links = self._stocklist_links(scraper)
            fresh = [l for l in links if l["href"] not in seen_files]
            logger.info("E-Agent: %s (%s) -> %d stocklist file(s), %d new",
                        cat.url.rsplit("/", 1)[-1], cat.state or "-", len(links), len(fresh))
            for l in fresh:
                heading = (l.get("heading") or "").strip(" -–—:|")
                if self._NOT_STOCK_LABEL.search(l.get("label") or ""):
                    logger.debug("E-Agent: %r under %r is not a stocklist — skipped.",
                                 l.get("label"), heading)
                    continue
                if not self._is_builder_heading(heading):
                    unattributed += 1
                    logger.debug("E-Agent: no builder heading for %s (saw %r)",
                                 l.get("label"), heading)
                    continue
                seen_files.add(l["href"])
                got = self._parse_stocklist(scraper, l, heading, "builder",
                                            cat.state, cat.product_type)
                if got:
                    builders[heading] = builders.get(heading, 0) + len(got)
                out.extend(got)
        if builders:
            logger.info("E-Agent: per-builder stock: %s",
                        ", ".join(f"{b} ({n})" for b, n in sorted(builders.items())))
        if unattributed:
            logger.warning("E-Agent: %d stocklist file(s) had no recognisable builder heading "
                           "and were skipped rather than guessed.", unattributed)
        logger.info("E-Agent: %d per-builder listing(s) across %d builder(s).",
                    len(out), len(builders))
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
                pooled = self._scrape_stocklist_files(scraper)
                logged_in = bool(pooled)
                if not pooled:
                    logger.info("E-Agent: no stocklists visible anonymously — logging in.")
                    if not self._login(scraper):
                        return []
                    logged_in = True
                    scraper.goto(self.cfg.listings_url)
                    pooled = self._scrape_stocklist_files(scraper)

                # The pooled files reaching us used to END the run, so the per-builder
                # category pages were never visited and every E-Agent row landed with a
                # blank builder. They are now always crawled.
                seen_files = set()
                per_builder = self._scrape_category_pages(scraper, seen_files)
                if not per_builder and not logged_in:
                    if self._login(scraper):
                        per_builder = self._scrape_category_pages(scraper, seen_files)

                listings = per_builder + pooled
                if not listings:
                    listings = self._scrape_listings(scraper)
                logger.info("E-Agent: %d listing(s) total (%d per-builder, %d pooled).",
                            len(listings), len(per_builder), len(pooled))
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
