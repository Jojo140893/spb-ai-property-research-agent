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
# Persistent browser profiles written by portal_login.py --profile. Preferred over the
# exported storage_state whenever one exists: on Proxima the storage_state is bounced
# straight to /customerauth/twofactor/ in a fresh context (Magento re-challenges 2FA
# per browser context), while the profile loads the signed-in stock page directly.
# portal_login.py has always claimed "the scrapers can reuse the same profile
# directory" — until 2026-08-01 nothing actually did.
PROFILE_DIR = config.PROJECT_ROOT / ".browser_profiles"

# The UA every automated context presents. Derived from the installed Chromium at
# run time rather than hardcoded, for two reasons found on Proxima (2026-08-01):
#
#  1. Chromium's own headless UA says "HeadlessChrome/<v>", and Proxima's WAF 403s
#     on it — the login page returns 200 for any ordinary UA, including bare curl,
#     but 403 for that one string. Stripping "Headless" is what makes it readable.
#  2. A hardcoded UA goes stale. This module pinned "Chrome/120.0" while the
#     installed Chromium was 148, so portal_login.py (which sets no UA, and so
#     presents the real 148) signed in with a different fingerprint from the one
#     the harvest later presents. A site that binds "remember this device" to the
#     UA re-challenges for 2FA on every run, which for Proxima means the one-time
#     interactive sign-in never actually stays done.
#
# Both the sign-in and the harvest now call this, so the two always agree.
_UA_CACHE: Optional[str] = None
_UA_FALLBACK = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")


def normalize_user_agent(ua: str) -> str:
    """'HeadlessChrome/148.0.7778.96' -> 'Chrome/148.0.0.0'.

    Two separate normalisations, and both are load-bearing:
      * "HeadlessChrome" -> "Chrome"  — the token Proxima's WAF 403s on.
      * full build -> <major>.0.0.0   — headless Chromium reports the full build
        while headed Chromium sends the reduced form real Chrome uses. Left alone,
        the sign-in and the harvest would present different strings even from the
        same installation, which is the mismatch this whole helper exists to remove.
    """
    ua = (ua or "").replace("HeadlessChrome/", "Chrome/")
    return re.sub(r"Chrome/(\d+)(?:\.[\d.]+)?", r"Chrome/\1.0.0.0", ua)


def browser_user_agent(browser=None, pw=None) -> str:
    """The UA every automated context presents, derived once and cached.

    Pass a launched `browser`, or a `sync_playwright()` instance to derive one from
    a throwaway. With neither, falls back to a pinned string — correct today, and
    normalised the same way, so a stale pin can only ever be a version behind rather
    than the "HeadlessChrome" string that actually gets blocked.
    """
    global _UA_CACHE
    if _UA_CACHE:
        return _UA_CACHE
    ua, throwaway, probe = "", None, None
    try:
        if browser is None and pw is not None:
            browser = throwaway = pw.chromium.launch(headless=True)
        if browser is not None:
            probe = browser.new_context()
            ua = probe.new_page().evaluate("navigator.userAgent") or ""
    except Exception:  # pragma: no cover - probing must never break a scrape
        ua = ""
    finally:
        for closer in (probe, throwaway):
            try:
                if closer:
                    closer.close()
            except Exception:
                pass
    _UA_CACHE = normalize_user_agent(ua or _UA_FALLBACK)
    return _UA_CACHE


# No Australian property package sells for less than this. A "price" below it is the
# parser having picked up something that is not a price at all — the stored examples are
# $7 from a marketing paragraph ("OUR PROMISE At Strike Developments...") and $149 from
# an internal-area table ("Total 149 149 58 30 77 77"). Only 13 rows, but the stock table
# sorts by price ascending, so they were the FIRST four listings a client ever saw.
MIN_PLAUSIBLE_PRICE = 50_000.0

# A money amount broken by a space inside the digits — "$ 9 32,900" for $932,900 — which
# is how several price lists come out of PDF and spreadsheet copy.
#
# THIS ONE UNDERSTATED PRICES TO CLIENTS. The row reads
#     ... Available 4 2 1 272  $ 397,900  $ 535,000  $ 9 32,900  Split Registered
# i.e. land $397,900, build $535,000, package $932,900. The broken total parsed as $9,
# fell under MIN_PLAUSIBLE_PRICE, was discarded, and max() of what survived picked a
# COMPONENT: the listing was published at $535,000 for a $932,900 package. 113 of the
# 114 affected rows were understated, most by about $400,000. Colin caught it on the
# call by knowing what Rouse Hill costs.
#
# The second group must carry the thousands comma, so two genuinely separate amounts
# ("$ 397,900 $ 535,000") can never be joined: after "$ 397" the next character is a
# comma, not whitespace.
_SPLIT_MONEY = re.compile(r"\$\s*(\d{1,3})\s+(\d{2,3},\d{3})\b")


def normalise_money_spacing(text: Optional[str]) -> str:
    """Re-join money amounts that a PDF/spreadsheet copy split mid-number."""
    return _SPLIT_MONEY.sub(lambda m: "$%s%s" % (m.group(1), m.group(2)), str(text or ""))


def parse_price(text: Optional[str]) -> Optional[float]:
    """'$725,000' / 'From $725k' / '725000' -> 725000.0 ; None if not parseable.

    Returns None rather than an implausibly small number: a wrong price is worse than
    no price, because no price is visibly missing and excluded from every shortlist,
    while $149 looks like the bargain of the century and sorts straight to the top.
    """
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
    return val if val >= MIN_PLAUSIBLE_PRICE else None


def parse_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"\d+", text.replace(",", ""))
    return int(m.group(0)) if m else None


# The single definition of "is this page authenticated?", shared by the scrapers and
# by portal_login.py. It used to exist as two near-identical copies that drifted.
#
# Both clauses below were written the hard way, on Proxima (2026-08-01):
#
#  * The old version computed `loginish = /sign in|login|password/.test(t) && !hasMarker`
#    and returned `hasMarker && !loginish`. Because loginish can only be true when
#    hasMarker is false, that whole expression collapses to plain `hasMarker` — the
#    "am I still on a login page?" guard was dead code from the start.
#
#  * A 2FA challenge is the case that actually matters. Magento keeps its customer
#    header ("Log Out", "My Account") on the two-factor page, so the moment Coleen's
#    password was accepted the marker appeared, this returned true, portal_login saved
#    a half-session and CLOSED THE BROWSER before the OTP could be typed. A partially
#    authenticated session is worse than none: it carries a real PHPSESSID, so the
#    harvest would have skipped the login path and scraped a logged-out page.
#
# So a challenge page is now an explicit NOT-logged-in state, checked before markers.
LOGGED_IN_JS = r"""
() => {
  const url = (location.href || '').toLowerCase();
  const t = (document.body ? document.body.innerText : '').toLowerCase();

  // --- still mid-authentication? then we are NOT in, whatever the header says ---
  const challengeUrl = /twofactor|two-factor|2fa|otp|mfa|customerauth|challenge|verify/.test(url);
  const challengeField = !!document.querySelector(
    "input[autocomplete='one-time-code'], input[name*='otp' i], input[id*='otp' i], " +
    "input[name*='tfa' i], input[id*='tfa' i], input[name*='authcode' i], " +
    "input[name*='verification' i], input[id*='verification' i], input[name*='2fa' i]");
  // Wording alone is NOT enough. Magento's account menu carries a "Two-Factor
  // Authentication" settings link, so treating the words as proof would make a
  // genuinely signed-in page read as a challenge and the sign-in would never save
  // — the opposite failure, and just as costly. Require something to type into.
  const typeable = [...document.querySelectorAll('input')].some(i => {
    const ty = (i.getAttribute('type') || 'text').toLowerCase();
    return ['text', 'tel', 'number', 'password'].includes(ty)
           && !/search/i.test((i.name || '') + (i.id || '') + ty)
           && i.offsetParent !== null;
  });
  const challengeText = /verification code|authentication code|one-time code|one time code|enter the code|security code/.test(t);
  if (challengeUrl || challengeField || (challengeText && typeable)) return false;

  // --- a visible password box means the credential step is not done either ---
  if (document.querySelector("input[type=password]")) return false;

  const ctrls = [...document.querySelectorAll('a,button')].map(e => (e.innerText||'').toLowerCase());
  const markers = ['log out','logout','sign out','signout','my account','my profile','dashboard'];
  return ctrls.some(l => markers.some(m => l.includes(m))) || markers.some(m => t.includes(m));
}
"""


class PlaywrightScraper:
    """Manages a Chromium browser/context/page with an optional saved session."""

    def __init__(self, session_name: str, headless: Optional[bool] = None,
                 rate_limit_seconds: Optional[float] = None,
                 nav_timeout_ms: Optional[int] = None,
                 read_only: bool = False):
        # read_only: never write the session file back. For diagnostics that only
        # want to LOOK at whether a saved session still works.
        self.read_only = read_only
        self.session_name = session_name
        self.headless = config.SCRAPER_HEADLESS if headless is None else headless
        self.rate_limit_seconds = config.SCRAPER_RATE_LIMIT_S if rate_limit_seconds is None else rate_limit_seconds
        self.nav_timeout_ms = config.SCRAPER_NAV_TIMEOUT_MS if nav_timeout_ms is None else nav_timeout_ms
        self._pw = None
        self._browser = None
        self._context = None
        self._using_profile = False
        self.page = None

    @property
    def session_file(self) -> Path:
        return SESSION_DIR / f"{self.session_name}.json"

    @property
    def profile_dir(self) -> Path:
        return PROFILE_DIR / self.session_name

    @contextmanager
    def session(self):
        if not PLAYWRIGHT_AVAILABLE:
            raise ScraperError(
                "Playwright is not installed. Run: pip install playwright && playwright install chromium"
            )
        SESSION_DIR.mkdir(exist_ok=True)
        self._pw = sync_playwright().start()
        try:
            # A persistent profile beats the exported storage_state wherever one
            # exists — it is the only thing that survives Proxima's per-context 2FA
            # re-challenge. Falls back to the old path for every portal without one,
            # so nothing that works today changes.
            if self.profile_dir.exists():
                self._using_profile = True
                self._context = self._pw.chromium.launch_persistent_context(
                    str(self.profile_dir),
                    headless=self.headless,
                    user_agent=browser_user_agent(pw=self._pw),
                    viewport={"width": 1440, "height": 900},
                )
                self._context.set_default_timeout(self.nav_timeout_ms)
                pages = self._context.pages
                self.page = pages[0] if pages else self._context.new_page()
                logger.info("[%s] using the saved browser profile.", self.session_name)
                yield self
                return
            self._browser = self._pw.chromium.launch(headless=self.headless)
            storage = str(self.session_file) if self.session_file.exists() else None
            self._context = self._browser.new_context(
                storage_state=storage,
                user_agent=browser_user_agent(self._browser),
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
            self._using_profile = False

    def save_session(self):
        """Refresh an EXISTING session file, but ONLY if we are still authenticated.

        Scrape runs must never create a new session file: an anonymous visit would
        leave a cookie jar that later looks like 'already logged in' and suppresses
        the real login path. Only portal_login.py creates sessions.

        The authenticated check is not optional, and it was learned expensively
        (Proxima, 2026-08-01). Loading a 2FA-gated session in a fresh context makes
        Magento bounce to /customerauth/twofactor/ and issue a NEW, unverified
        PHPSESSID. Writing that back over the good one destroys a session that cost a
        human an interactive sign-in — which is exactly what happened, to a session
        Coleen had just created. A degraded write-back is strictly worse than no
        write-back, so a context that cannot prove it is signed in leaves the file
        alone.
        """
        if self.read_only or self._using_profile:
            # A persistent profile writes its own cookies to disk; there is nothing to
            # export, and exporting would only risk overwriting a good session file
            # with whatever this run happened to end on.
            return
        try:
            if not (self._context and self.session_file.exists()):
                return
            if not self.page or not self.page.evaluate(LOGGED_IN_JS):
                logger.info("[%s] not authenticated at close — leaving the saved session untouched.",
                            self.session_name)
                return
            self._context.storage_state(path=str(self.session_file))
        except Exception as e:  # pragma: no cover
            logger.warning("Could not persist session %s: %s", self.session_name, e)

    def throttle(self):
        if self.rate_limit_seconds > 0:
            time.sleep(self.rate_limit_seconds)

    def goto(self, url: str):
        self.page.goto(url, wait_until="domcontentloaded")
        # Let client-rendered inventory hydrate before anything reads the DOM.
        # (Bathla's /display renders stock via JS; without this the page is still
        # empty at extraction time and the portal looks like it has no listings.)
        for wait in (lambda: self.page.wait_for_load_state("networkidle", timeout=8000),
                     lambda: self.page.wait_for_timeout(1500)):
            try:
                wait()
            except Exception:
                pass
        self.throttle()

    def text_or_none(self, scope, selector: str) -> Optional[str]:
        """First matching element's inner_text, or None. Never raises on miss."""
        try:
            el = scope.query_selector(selector)
            return el.inner_text().strip() if el else None
        except Exception:
            return None

    _LOGGED_IN_JS = LOGGED_IN_JS

    def is_logged_in(self, logged_in_selector: str) -> bool:
        """True if the page looks authenticated.

        NOTE: a config value like "text=Log Out, text=My Account" is NOT a valid
        single Playwright selector — passing it whole makes the text engine search
        for the literal string "Log Out, text=My Account", which never matches and
        makes every login look like a failure. Split it and try each alternative,
        then fall back to a DOM heuristic.
        """
        # A visible password field means we are still on a login screen, whatever
        # markers the page shows. (Paramount's PUBLIC nav contains "AGENT PORTAL",
        # which otherwise matched and made the logged-out homepage look authenticated,
        # causing the login step to be skipped entirely.)
        try:
            if self.page.query_selector("input[type=password]") is not None:
                return False
        except Exception:
            pass
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
