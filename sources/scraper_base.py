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

import json
import logging
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import urlsplit

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

    # AN EXPLICIT PIN WINS OVER DERIVATION, and it exists for one specific move: taking
    # the harvest off this Windows laptop. Proxima's "remember this device" trust is a
    # 180-day cookie (otp_authentication) issued against the browser that completed 2FA,
    # and this function derives its string from whatever Chromium is INSTALLED — so the
    # same session file replayed on a Linux runner would present "X11; Linux x86_64"
    # where the trust was granted to "Windows NT 10.0". Whether Proxima binds device
    # trust to the UA is not established; the rest of this file already treats it as a
    # live hazard, and a re-challenge costs a human with Colin's inbox. Pinning is the
    # cheap side of that bet.
    #
    #     setx SPB_USER_AGENT "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ..."
    #
    # Normalised like any other value, so a pin carrying "HeadlessChrome" — the one token
    # Proxima's WAF actually 403s — is corrected rather than obeyed.
    pinned = os.getenv("SPB_USER_AGENT", "").strip()
    if pinned:
        _UA_CACHE = normalize_user_agent(pinned)
        logger.info("user agent pinned by SPB_USER_AGENT")
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

# The same break, but landing on the other side of the comma:
#     "$ 1 ,455,900.00"   ->   $1,455,900.00
#     "$ 1 ,064,230"      ->   $1,064,230
# The leading group is followed by a space and then a COMMA, so _SPLIT_MONEY above (which
# needs digits straight after the space) never matched it. PRICE_RE used to absorb this
# shape by allowing whitespace as a thousands separator, but that same tolerance glued
# adjacent spreadsheet columns into 9-digit numbers and cost 212 rows their package
# price. The repair belongs here, where it is bounded, rather than in the price regex,
# where it was not.
#
# One or more comma groups must follow, so "$ 1 ,455,900" joins while two genuinely
# separate amounts never can: nothing here can bridge "$896,000 202.15", because 202.15
# is not a comma group.
_SPLIT_MONEY_COMMA = re.compile(r"\$\s*(\d{1,3})\s+((?:,\d{3})+)")


def normalise_money_spacing(text: Optional[str]) -> str:
    """Re-join money amounts that a PDF/spreadsheet copy split mid-number."""
    out = _SPLIT_MONEY.sub(lambda m: "$%s%s" % (m.group(1), m.group(2)), str(text or ""))
    return _SPLIT_MONEY_COMMA.sub(lambda m: "$%s%s" % (m.group(1), m.group(2)), out)


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

# A sign-in challenge in the address bar: the same words LOGGED_IN_JS refuses on.
_CHALLENGE_URL = re.compile(r"twofactor|two-factor|2fa|otp|mfa|customerauth|challenge|verify", re.I)


def safe_url(url: Any) -> str:
    """scheme://host/path of a URL -- never its query string or fragment.

    The address a sign-in ended on is worth recording (it is how "redirected to /login"
    is told from "a slow page"), and it is written to files the Settings tab publishes.
    A query string is where a portal puts a return token, a SharePoint folder id or a
    one-time code, so it is dropped before anything is kept.
    """
    try:
        p = urlsplit(str(url or ""))
    except ValueError:
        return ""
    if not p.scheme:
        return ""
    return "%s://%s%s" % (p.scheme, p.netloc, p.path)


def signed_out_reason(page) -> str:
    """Why a page reads as NOT signed in, in words a person can act on.

    "expired" said nothing about what the page showed, so a session the portal had ended,
    a login form that moved and a page that had not finished loading all looked the same
    on the Settings tab (Hermitage, 23 Oct 2026: the harvest read 288 listings on the saved
    session at 23:39 and the check called it expired at 00:40, with nothing to say why).
    """
    try:
        path = urlsplit(page.url or "").path or "/"
    except Exception:                                                 # noqa: BLE001
        path = "?"
    try:
        if page.query_selector("input[type=password]") is not None:
            return "password field at %s" % path
    except Exception:                                                 # noqa: BLE001
        pass
    if _CHALLENGE_URL.search(path):
        return "a sign-in challenge at %s" % path
    return "no signed-in marker at %s" % path


# ---------------------------------------------------------------------------------------
# WHAT THE HARVEST FOUND WHEN IT SIGNED IN (23 Oct 2026).
#
# The Settings tab's Login column was measured by ONE thing: portal_login.py --check
# reopening each saved session after the harvest. That is a second sign-in, made an hour
# later with its own idea of "signed in", and on 23 Oct it was wrong in both directions:
#
#   * E-Agent and Hermitage read RE-LOGIN NEEDED on a night the harvest had signed in to
#     both and read 5,069 and 288 listings. E-Agent's check looked for a stocklist link on
#     a page that has not carried one since 24 Sep (18 of 18 nights "expired").
#   * Proxima read NOT CHECKED through an 11-day sign-in outage (12 Oct 20:51 to
#     23 Oct 11:31). The check never opens Proxima, rightly, and the harvest -- which
#     tried on every run and logged "signed in but still bounced ... the password may have
#     changed" on 11 of the 12 (on 21 Oct the sign-in form timed out instead) -- wrote its
#     answer only into a log nobody reads.
#
# So each harvest now writes down what IT found, per session: the saved session was
# accepted, it signed back in with the stored password, or it was bounced. That is the
# fact the column exists to report -- did tonight's read actually get in -- and
# portal_login.check_logins reads it first (eagent_stocklist.login_row already reported
# the Stocklist this way). logs/ is gitignored and not synced to the bucket, so a record
# can never be another machine's night posing as tonight's; HARVEST_LOGIN_FRESH_HOURS
# bounds the laptop, where the file persists between runs.
#
# outcome -> the Settings tab's login state. "error" is no verdict: a sign-in form that
# timed out says nothing about whether the password works, and calling it "expired" is
# how a crash came to read as a dead login.
HARVEST_LOGIN_STATES = {
    "session": "ok",          # the saved session was accepted
    "signed_in": "ok",        # the saved session had lapsed; signed back in with the password
    "bounced": "expired",     # tried and was not let in: refused, 2FA, still on the login page
    "missing": "missing",     # nothing to sign in with: no saved session, no stored password
    "error": None,            # the attempt itself failed (timeout, crash): no verdict
}
# Within ONE harvest the best attempt stands -- a builder licensed in two states is read
# twice, and one good sign-in is a sign-in (the same rule as the manifest's "one good read
# is a read"). A later harvest replaces an earlier one outright.
_HARVEST_LOGIN_RANK = {"session": 4, "signed_in": 4, "bounced": 2, "missing": 1, "error": 0}
HARVEST_LOGIN_FRESH_HOURS = 36
_HARVEST_RUN = "%d-%d" % (os.getpid(), int(time.time()))


def harvest_logins_path() -> Path:
    """logs/harvest_logins.json, resolved at call time so a test can move PROJECT_ROOT."""
    return Path(config.PROJECT_ROOT) / "logs" / "harvest_logins.json"


def record_harvest_login(session_name: str, outcome: str, reason: str = "", url: str = "",
                         path: Optional[Path] = None) -> None:
    """Write down how the harvest's sign-in to one portal went. Never fatal, never a secret:
    the reason is the harvest's own words and an exception's class, the url has no query."""
    if outcome not in HARVEST_LOGIN_STATES or not session_name:
        logger.warning("harvest login record refused: %r for %r", outcome, session_name)
        return
    target = Path(path) if path else harvest_logins_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    old = data.get(session_name) if isinstance(data.get(session_name), dict) else None
    if old and old.get("run") == _HARVEST_RUN and \
            _HARVEST_LOGIN_RANK.get(old.get("outcome"), 0) > _HARVEST_LOGIN_RANK[outcome]:
        return
    data[session_name] = {"outcome": outcome, "reason": str(reason or "")[:240],
                          "url": safe_url(url),
                          "at": datetime.now().isoformat(timespec="seconds"),
                          "run": _HARVEST_RUN}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError as exc:
        logger.warning("could not record the %s sign-in in %s: %s", session_name, target, exc)


def read_harvest_logins(path: Optional[Path] = None,
                        now: Optional[datetime] = None) -> Dict[str, Dict[str, Any]]:
    """{session: record} for every record young enough to describe the latest harvest."""
    now = now or datetime.now()
    try:
        data = json.loads(Path(path or harvest_logins_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for name, rec in (data.items() if isinstance(data, dict) else ()):
        if not isinstance(rec, dict) or rec.get("outcome") not in HARVEST_LOGIN_STATES:
            continue
        try:
            age_h = (now - datetime.fromisoformat(str(rec.get("at")))).total_seconds() / 3600
        except (ValueError, TypeError):
            # TypeError: an "at" carrying a timezone cannot be taken from a naive now. The
            # harvest never writes one, but a hand-edited file must not stop --check.
            continue
        if -1 <= age_h <= HARVEST_LOGIN_FRESH_HOURS:
            out[name] = rec
    return out


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

        WHAT "PROVE IT" MEANS IS PER-PORTAL. LOGGED_IN_JS looks for a Log Out / My Account
        marker, and E-Agent renders none — signed in or out — so its check was permanently
        False and its session was therefore NEVER refreshed by any run. It aged out in
        mid-August and took the channel down with it: ~4,500 listings went stale while the
        harvest reported success. A portal that knows a better test (E-Agent: are the
        stocklist files reachable?) sets `auth_check` and that is used instead. The guard
        is unchanged for every other portal, deliberately — loosening it globally would
        trade this bug for the Proxima one above.
        """
        if self.read_only or self._using_profile:
            # A persistent profile writes its own cookies to disk; there is nothing to
            # export, and exporting would only risk overwriting a good session file
            # with whatever this run happened to end on.
            return
        try:
            if not (self._context and self.session_file.exists()):
                return
            if not self.page:
                return
            probe = getattr(self, "auth_check", None)
            try:
                authenticated = bool(probe()) if callable(probe) \
                    else bool(self.page.evaluate(LOGGED_IN_JS))
            except Exception as exc:                                  # noqa: BLE001
                # An unusable probe must not silently fall back to the marker check —
                # that is how E-Agent's session quietly stopped being refreshed. Refuse
                # the write-back and say why.
                logger.warning("[%s] auth check failed (%s) — leaving the saved session untouched.",
                               self.session_name, exc)
                return
            if not authenticated:
                logger.info("[%s] not authenticated at close — leaving the saved session untouched.",
                            self.session_name)
                return
            self._context.storage_state(path=str(self.session_file))
        except Exception as e:  # pragma: no cover
            logger.warning("Could not persist session %s: %s", self.session_name, e)

    def throttle(self):
        if self.rate_limit_seconds > 0:
            time.sleep(self.rate_limit_seconds)

    # A first navigation that times out once must not cost the whole channel.
    #
    # The scheduled run of 20 Aug went red having read NOTHING from Proxima — 3,922 lots
    # left to go stale — because the very first page.goto of the run hit
    #     Page.goto: Timeout 30000ms exceeded
    # on https://portal.proxima.com.au/agent/projects/index/. The session was valid (the
    # auth check had passed at 09:26) and a manual re-run three hours later read all
    # 3,922 back. The portal was simply slow for half a minute.
    #
    # The alert made it worse: the guard's advice is "the usual cause is an expired
    # sign-in, run portal_login.py", which sends Ahsan to re-authenticate a session that
    # was never broken. A retry removes the whole class — a portal that is slow once at
    # 09:23 is usually fine at 09:24 — and a portal that is genuinely down still fails,
    # just after three honest attempts instead of one.
    _GOTO_ATTEMPTS = 3

    def goto(self, url: str):
        last = None
        for attempt in range(1, self._GOTO_ATTEMPTS + 1):
            try:
                # Grow the budget: a portal mid-deploy can take well over the 30s default.
                self.page.goto(url, wait_until="domcontentloaded",
                               timeout=30_000 * attempt)
                last = None
                break
            except Exception as exc:  # Playwright TimeoutError and transport resets alike
                last = exc
                if attempt == self._GOTO_ATTEMPTS:
                    break
                logger.warning("goto %s failed (%s) — attempt %d of %d, retrying.",
                               url, type(exc).__name__, attempt, self._GOTO_ATTEMPTS)
                try:
                    self.page.wait_for_timeout(2000 * attempt)
                except Exception:
                    time.sleep(2 * attempt)
        if last is not None:
            raise last
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
