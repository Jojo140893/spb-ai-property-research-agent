"""
Tests for the two Proxima defects found on 2026-08-01, before the first harvest.

Neither is visible without a signed-in session, and both are the kind that only
show up after the client has already done the one-time 2FA sign-in — which is
exactly when they are most expensive to discover.

1. USER AGENT. Chromium's headless UA carries a "HeadlessChrome" token, and
   Proxima's WAF answers 403 to it: the login page returns 200 for an ordinary UA
   and even for bare curl, but 403 for that one string. Separately, scraper_base
   pinned "Chrome/120.0" while the installed Chromium was 148, so portal_login.py
   (which set no UA) signed in with a different fingerprint from the one the
   harvest presents. A portal that ties "remember this device" to the UA then
   re-challenges for 2FA on every run and the one-time sign-in never stays done.

2. SOURCE CHANNEL. source_channel is part of the identity hash, so it has to be
   right BEFORE the first Proxima row is stored — renaming the channel afterwards
   re-identifies every Proxima row and the next harvest inserts them all again.
   Colin asked for "Proxima" as its own entry in the dashboard's by-source filter,
   and that list is built from the distinct source_channel values, so the channel
   value IS the filter entry.
"""

from database import building_content_hash
from sources.portal_config import BUILDER_PORTAL_CONFIGS, config_for_url
from sources.scraper_base import normalize_user_agent


# --------------------------------------------------------------- user agent

def test_headless_token_is_never_presented():
    """The literal string Proxima's WAF 403s on must not survive normalisation."""
    headless = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) HeadlessChrome/148.0.7778.96 Safari/537.36")
    out = normalize_user_agent(headless)
    assert "Headless" not in out, out
    assert "Chrome/148" in out, out


def test_headless_and_headed_normalise_to_the_same_string():
    """The sign-in and the harvest must present an identical UA.

    Headless Chromium reports the full build (148.0.7778.96); headed Chromium
    reports the reduced form real Chrome sends (148.0.0.0). Same installation,
    two different strings — so normalising only the "Headless" token is not
    enough to make the two fingerprints match.
    """
    base = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    headless = base + "HeadlessChrome/148.0.7778.96 Safari/537.36"
    headed = base + "Chrome/148.0.0.0 Safari/537.36"
    assert normalize_user_agent(headless) == normalize_user_agent(headed), (
        normalize_user_agent(headless), normalize_user_agent(headed))


def test_version_is_reduced_not_invented():
    """Reduction keeps the real major version — it must not pin to a stale one."""
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/151.0.1234.5 Safari/537.36")
    assert "Chrome/151.0.0.0" in normalize_user_agent(ua)


def test_a_blank_user_agent_does_not_crash():
    assert normalize_user_agent("") == ""
    assert normalize_user_agent(None) == ""


# ------------------------------------------------------------ source channel

def test_proxima_declares_its_own_channel():
    cfg = BUILDER_PORTAL_CONFIGS["proxima.com.au"]
    assert cfg.source_channel == "Proxima", cfg.source_channel


def test_the_other_portals_keep_the_generic_channel():
    """Hermitage/Torsion/Bathla have 361 rows stored under the generic channel.

    Giving them a per-portal channel now would change their content_hash and the
    next harvest would store all 361 a second time. They must stay blank.
    """
    for host, cfg in BUILDER_PORTAL_CONFIGS.items():
        if host == "proxima.com.au":
            continue
        assert not cfg.source_channel, f"{host} would re-identify its stored rows"


def test_channel_reaches_the_config_lookup_by_url():
    """builder_portals resolves the config from the builder's portal_url."""
    for url in ("https://portal.proxima.com.au/customer/account/login/",
                "www.proxima.com.au"):
        cfg = config_for_url(url)
        assert cfg is not None, url
        assert cfg.source_channel == "Proxima", (url, cfg.name, cfg.source_channel)


def test_the_channel_changes_the_identity_hash():
    """The reason the channel must be set before the first harvest, not after."""
    row = {"source_channel": "Direct Builder Portal (live)", "builder_name": "Proxima",
           "suburb": "Schofields", "lot_number": "12", "land_sqm": 300}
    generic = building_content_hash(row)
    named = building_content_hash({**row, "source_channel": "Proxima"})
    assert generic != named, (
        "source_channel no longer affects identity — if that is deliberate, this "
        "test and the ordering warning in portal_config.py are both stale")


# ------------------------------------------------- the 2FA premature-close bug

# Reproduces the page that closed the browser on Coleen mid-sign-in (2026-08-01).
# Magento keeps its customer header on the two-factor page, so "Log Out" and "My
# Account" are both present while authentication is still incomplete.
_MAGENTO_2FA = """<html><body>
  <header><a href="/customer/account/">My Account</a><a href="/customer/account/logout/">Log Out</a></header>
  <h1>Two-Factor Authentication</h1>
  <p>Please enter the verification code from your authenticator app.</p>
  <form><input type="text" name="tfa_code" autocomplete="one-time-code"><button>Confirm</button></form>
</body></html>"""

_SIGNED_IN = """<html><body>
  <header><a href="/customer/account/">My Account</a><a href="/customer/account/logout/">Log Out</a></header>
  <h1>My Dashboard</h1><p>Welcome back. Available stock is listed below.</p>
</body></html>"""

_LOGIN = """<html><body><h1>Customer Login</h1>
  <form><input type="email" id="email"><input type="password" id="pass"><button id="send2">Sign In</button></form>
</body></html>"""


def _judge(html: str):
    """Run the shared detector over a page. Skips cleanly if Playwright is absent."""
    from sources.scraper_base import LOGGED_IN_JS, PLAYWRIGHT_AVAILABLE
    if not PLAYWRIGHT_AVAILABLE:
        return None
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            page = b.new_context().new_page()
            page.set_content(html)
            return page.evaluate(LOGGED_IN_JS)
        finally:
            b.close()


def test_a_2fa_challenge_is_not_signed_in():
    """The regression that cost a real sign-in attempt.

    A half-authenticated session is worse than none: it carries a live PHPSESSID, so
    the harvest skips its login path and scrapes a logged-out page.
    """
    got = _judge(_MAGENTO_2FA)
    if got is None:
        return
    assert got is False, "a 2FA page is being read as signed in — the browser will close mid-OTP again"


def test_a_real_dashboard_is_still_signed_in():
    """The guard must not be so strict that a genuine login never registers."""
    got = _judge(_SIGNED_IN)
    if got is None:
        return
    assert got is True, "a genuine signed-in page is no longer detected — sign-in would never save"


def test_a_login_page_is_not_signed_in():
    got = _judge(_LOGIN)
    if got is None:
        return
    assert got is False, got


def test_a_2fa_settings_link_does_not_look_like_a_challenge():
    """The opposite failure, and just as costly.

    Magento's account menu carries a "Two-Factor Authentication" settings link. If
    the wording alone counted as a challenge, a genuinely signed-in page would read
    as still-authenticating and the sign-in would never save — the operator would sit
    through the full 45-minute wait for nothing.
    """
    html = """<html><body>
      <header><a href="/customer/account/">My Account</a><a href="/customer/account/logout/">Log Out</a></header>
      <nav><a href="/tfa/settings/">Two-Factor Authentication</a><a href="/customer/address/">Addresses</a></nav>
      <h1>My Dashboard</h1><p>Available stock is listed below.</p>
    </body></html>"""
    got = _judge(html)
    if got is None:
        return
    assert got is True, "a 2FA settings link is being mistaken for a 2FA challenge"


def test_there_is_only_one_detector_definition():
    """portal_login.py and scraper_base.py each carried a copy, and they drifted.

    The copy in portal_login.py is the one that had the dead `loginish` guard.
    """
    import portal_login
    from sources.scraper_base import LOGGED_IN_JS, PlaywrightScraper
    assert portal_login._LOGGED_IN_JS is LOGGED_IN_JS, "portal_login has its own copy again"
    assert PlaywrightScraper._LOGGED_IN_JS is LOGGED_IN_JS, "scraper_base has its own copy again"


def run_all():
    tests = [
        ("UA: the headless token is never presented", test_headless_token_is_never_presented),
        ("UA: sign-in and harvest agree", test_headless_and_headed_normalise_to_the_same_string),
        ("UA: version reduced, not pinned", test_version_is_reduced_not_invented),
        ("UA: blank input is safe", test_a_blank_user_agent_does_not_crash),
        ("channel: Proxima declares its own", test_proxima_declares_its_own_channel),
        ("channel: other portals unchanged", test_the_other_portals_keep_the_generic_channel),
        ("channel: resolves from the portal url", test_channel_reaches_the_config_lookup_by_url),
        ("channel: it is part of identity", test_the_channel_changes_the_identity_hash),
        ("2fa: a challenge page is NOT signed in", test_a_2fa_challenge_is_not_signed_in),
        ("2fa: a real dashboard still is", test_a_real_dashboard_is_still_signed_in),
        ("2fa: a login page is not", test_a_login_page_is_not_signed_in),
        ("2fa: a settings link is not a challenge", test_a_2fa_settings_link_does_not_look_like_a_challenge),
        ("2fa: one shared detector, no copies", test_there_is_only_one_detector_definition),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] proxima: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] proxima: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
