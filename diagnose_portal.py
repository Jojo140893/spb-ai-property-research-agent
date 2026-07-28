"""
Diagnose one portal's authenticated state — reports WHAT ACTUALLY HAPPENS.

Runs the real login path (credentials from the OS vault) and then reports the
landing URL, whether the page looks authenticated, which logout/account markers
are present, and how many listings the adaptive extractor can see.

Prints NO secret material — only the credential source and a masked identifier.

Usage: python diagnose_portal.py e_agent|paramount|hermitage|bathla|proxima|frd|torsion
"""

import logging
import sys

logging.basicConfig(level=logging.INFO, format="    %(levelname)s %(message)s")

from builder_registry import BuilderRegistry              # noqa: E402
from sources.scraper_base import PlaywrightScraper        # noqa: E402
from sources.portal_config import EAGENT_CONFIG, config_for_url  # noqa: E402
from sources.adaptive_extract import extract_listings     # noqa: E402
from secrets_store import get_credentials                 # noqa: E402

PAGE_PROBE = r"""
() => {
  const ctrls = [...document.querySelectorAll('a,button')].map(e => (e.innerText||'').trim()).filter(Boolean);
  const markers = ['log out','logout','sign out','my account','my profile','dashboard'];
  return {
    url: location.href,
    title: document.title.slice(0,80),
    logoutMarkers: ctrls.filter(c => markers.some(m => c.toLowerCase().includes(m))).slice(0,5),
    hasPasswordField: !!document.querySelector('input[type=password]'),
    inputs: [...document.querySelectorAll('input')].filter(i=>!['hidden','submit'].includes(i.type))
              .map(i=>`${i.type}|${i.name||i.id||i.placeholder||''}`).slice(0,6),
    bodyStart: (document.body?document.body.innerText:'').replace(/\s+/g,' ').slice(0,220),
    linkCount: document.querySelectorAll('a').length,
  };
}
"""


def resolve(which: str):
    reg = BuilderRegistry()
    if which == "e_agent":
        return "e_agent", "E-Agent", EAGENT_CONFIG, ("", "")
    for b in reg.get_all_builders():
        url = (b.get("portal_url") or "").strip()
        if not url or "e-agent" in url.lower():
            continue
        if which.lower() in b["builder_name"].lower().replace(" ", ""):
            key = "portal_" + b["builder_name"].lower().replace(" ", "_").replace("/", "_")
            return key, b["builder_name"], config_for_url(url), (
                b.get("portal_login_email", ""), b.get("portal_login_password", ""))
    return None, None, None, ("", "")


def main(which: str):
    key, label, cfg, csv_fb = resolve(which)
    if not cfg:
        print(f"[ERROR] unknown portal '{which}'")
        return 1
    user, pw, src = get_credentials(key, csv_fb)
    masked = (user[:2] + "***") if user else "(none)"
    print(f"=== {label} ===")
    print(f"  credentials: {src} ({masked})")
    print(f"  login_url:   {cfg.login_url[:80]}")
    print(f"  listings_url:{cfg.listings_url[:80]}")

    scraper = PlaywrightScraper(session_name=key)
    with scraper.session():
        page = scraper.page
        # 1) state BEFORE any login
        scraper.goto(cfg.login_url)
        page.wait_for_timeout(2500)
        before = page.evaluate(PAGE_PROBE)
        print(f"  [before] url={before['url'][:70]}")
        print(f"           title={before['title']}")
        print(f"           password field present: {before['hasPasswordField']}  inputs={before['inputs']}")

        # 2) attempt login if we have credentials and are not already in
        already = scraper.is_logged_in(cfg.logged_in_selector)
        print(f"  already authenticated (fixed check): {already}")
        if not already and user and pw:
            try:
                if cfg.open_login_selector:
                    for sel in cfg.open_login_selector.split(","):
                        try:
                            page.click(sel.strip(), timeout=5000); page.wait_for_timeout(1200)
                        except Exception:
                            pass
                    for extra in ("button:has-text('Log in with Email')",):
                        try:
                            page.click(extra, timeout=4000); page.wait_for_timeout(1200)
                        except Exception:
                            pass
                if not page.query_selector(cfg.email_selector):
                    raise RuntimeError('login form not found on page (email field missing)')
                page.fill(cfg.email_selector, user, timeout=10000)
                if cfg.continue_selector and not page.query_selector(cfg.password_selector):
                    page.click(cfg.continue_selector, timeout=8000)
                    page.wait_for_selector(cfg.password_selector, timeout=15000)
                page.fill(cfg.password_selector, pw, timeout=10000)
                page.click(cfg.submit_selector, timeout=10000)
                page.wait_for_load_state("networkidle", timeout=25000)
                page.wait_for_timeout(2500)
                print("  login submitted OK")
            except Exception as e:
                print(f"  login interaction FAILED: {str(e).splitlines()[0][:110]}")

        after = page.evaluate(PAGE_PROBE)
        print(f"  [after]  url={after['url'][:70]}")
        print(f"           logout/account markers: {after['logoutMarkers']}")
        print(f"           still shows password field: {after['hasPasswordField']}")
        print(f"           authenticated (fixed check): {scraper.is_logged_in(cfg.logged_in_selector)}")
        print(f"           body: {after['bodyStart'][:170]}")

        # 3) can we read listings from the stock page?
        scraper.goto(cfg.listings_url)
        page.wait_for_timeout(3000)
        got = extract_listings(page, builder_hint=label, state_hint="")
        print(f"  LISTINGS READABLE: {len(got)}")
        for g in got[:3]:
            print(f"     * {str(g['lot_address'])[:40]} | ${g['advertised_package_price']:,.0f} | {g.get('suburb')}")
        if not got:
            probe = page.evaluate(PAGE_PROBE)
            print(f"     stock page url={probe['url'][:70]} links={probe['linkCount']}")
            print(f"     stock body: {probe['bodyStart'][:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "e_agent"))
