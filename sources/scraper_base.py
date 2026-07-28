"""
Playwright scraper base for live portal scraping.

Design rules (from the code review + client requirements):
- NEVER return fabricated/sample data. If login fails, credentials are missing,
  or the DOM cannot be parsed, log the problem and return [] — the pipeline then
  honestly reports "no listings from this source" rather than inventing stock.
- Fail loudly: raise ScraperError on unexpected states so problems surface.
- Be polite: rate-limit between actions, reuse a saved session (storage_state)
  so we are not logging in on every run.
- Be resilient: selectors live in portal_config.py so a DOM change is a config
  edit, not a code change; a missing selector logs a "needs re-mapping" warning.

Requires: playwright (pip install playwright && playwright install chromium).
"""

import logging
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List, Dict, Any

import config

logger = logging.getLogger("spb.scraper")

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover - environment without playwright
    PLAYWRIGHT_AVAILABLE = False
    PWTimeout = Exception


class ScraperError(RuntimeError):
    """Raised when a scraper hits an unrecoverable, unexpected state."""


SESSION_DIR = config.PROJECT_ROOT / ".sessions"


def parse_price(text: Optional[str]) -> Optional[float]:
    """'$725,000' / 'From $725k' / '725000' -> 725000.0 ; None if not parseable."""
    if not text:
        return None
    t = text.replace(",", "").strip().lower()
    m = re.search(r"\$?\s*([\d]+(?:\.\d+)?)\s*(k|m)?", t)
    if not m:
        return None
    val = float(m.group(1))
    if m.group(2) == "k":
        val *= 1_000
    elif m.group(2) == "m":
        val *= 1_000_000
    return val if val > 0 else None


def parse_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"\d+", text.replace(",", ""))
    return int(m.group(0)) if m else None


class PlaywrightScraper:
    """Manages a Chromium browser/context/page with an optional saved session."""

    def __init__(self, session_name: str, headless: Optional[bool] = None,
                 rate_limit_seconds: Optional[float] = None,
                 nav_timeout_ms: Optional[int] = None):
        self.session_name = session_name
        self.headless = config.SCRAPER_HEADLESS if headless is None else headless
        self.rate_limit_seconds = config.SCRAPER_RATE_LIMIT_S if rate_limit_seconds is None else rate_limit_seconds
        self.nav_timeout_ms = config.SCRAPER_NAV_TIMEOUT_MS if nav_timeout_ms is None else nav_timeout_ms
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None

    @property
    def session_file(self) -> Path:
        return SESSION_DIR / f"{self.session_name}.json"

    @contextmanager
    def session(self):
        if not PLAYWRIGHT_AVAILABLE:
            raise ScraperError(
                "Playwright is not installed. Run: pip install playwright && playwright install chromium"
            )
        SESSION_DIR.mkdir(exist_ok=True)
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=self.headless)
            storage = str(self.session_file) if self.session_file.exists() else None
            self._context = self._browser.new_context(
                storage_state=storage,
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
                viewport={"width": 1440, "height": 900},
            )
            self._context.set_default_timeout(self.nav_timeout_ms)
            self.page = self._context.new_page()
            yield self
        finally:
            self.save_session()
            for closer in (self._context, self._browser):
                try:
                    if closer:
                        closer.close()
                except Exception:
                    pass
            if self._pw:
                self._pw.stop()
            self._pw = self._browser = self._context = self.page = None

    def save_session(self):
        """Refresh an EXISTING session file only.

        Scrape runs must never create a new session file: an anonymous visit would
        leave a cookie jar that later looks like 'already logged in' and suppresses
        the real login path. Only portal_login.py creates sessions.
        """
        try:
            if self._context and self.session_file.exists():
                self._context.storage_state(path=str(self.session_file))
        except Exception as e:  # pragma: no cover
            logger.warning("Could not persist session %s: %s", self.session_name, e)

    def throttle(self):
        if self.rate_limit_seconds > 0:
            time.sleep(self.rate_limit_seconds)

    def goto(self, url: str):
        self.page.goto(url, wait_until="domcontentloaded")
        self.throttle()

    def text_or_none(self, scope, selector: str) -> Optional[str]:
        """First matching element's inner_text, or None. Never raises on miss."""
        try:
            el = scope.query_selector(selector)
            return el.inner_text().strip() if el else None
        except Exception:
            return None

    # Robust authenticated-state check: look for logout/account markers in the DOM.
    _LOGGED_IN_JS = r"""
    () => {
      const t = (document.body ? document.body.innerText : '').toLowerCase();
      const ctrls = [...document.querySelectorAll('a,button')].map(e => (e.innerText||'').toLowerCase());
      const markers = ['log out','logout','sign out','signout','my account','my profile','dashboard'];
      const hasMarker = ctrls.some(l => markers.some(m => l.includes(m))) || markers.some(m => t.includes(m));
      const loginish = /sign in|log in|login|password/.test(t) && !hasMarker;
      return hasMarker && !loginish;
    }
    """

    def is_logged_in(self, logged_in_selector: str) -> bool:
        """True if the page looks authenticated.

        NOTE: a config value like "text=Log Out, text=My Account" is NOT a valid
        single Playwright selector — passing it whole makes the text engine search
        for the literal string "Log Out, text=My Account", which never matches and
        makes every login look like a failure. Split it and try each alternative,
        then fall back to a DOM heuristic.
        """
        for sel in [s.strip() for s in (logged_in_selector or "").split(",") if s.strip()]:
            try:
                if self.page.query_selector(sel) is not None:
                    return True
            except Exception:
                continue
        try:
            return bool(self.page.evaluate(self._LOGGED_IN_JS))
        except Exception:
            return False
