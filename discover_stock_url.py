"""
Find a portal's real stock-list URL after logging in.

Hermitage/Bathla authenticate fine but their root page is a search form or
marketing landing, not inventory. This logs in, enumerates the authenticated
navigation, then visits the most stock-like destinations and reports which one
the adaptive extractor can actually read listings from.

Prints no secret material.

Usage: python discover_stock_url.py hermitage|bathla|paramount|e_agent
"""

import logging
import sys
from urllib.parse import urljoin, urlparse

logging.basicConfig(level=logging.INFO, format="    %(levelname)s %(message)s")

from sources.scraper_base import PlaywrightScraper          # noqa: E402
from sources.adaptive_extract import extract_listings       # noqa: E402
from diagnose_portal import resolve                          # noqa: E402
from secrets_store import get_credentials                    # noqa: E402

# nav text/href that plausibly leads to inventory
STOCK_HINTS = ("package", "stock", "listing", "property", "properties", "display",
               "house", "land", "granny", "promotion", "inventory", "search",
               "available", "view all", "estate", "home")

LINKS_JS = """
() => [...document.querySelectorAll('a[href]')].map(a => ({
  text: (a.innerText||'').trim().slice(0,60), href: a.href
})).filter(l => l.href && !l.href.startsWith('mailto') && !l.href.startsWith('tel'))
"""


def main(which: str):
    key, label, cfg, csv_fb = resolve(which)
    if not cfg:
        print(f"[ERROR] unknown portal '{which}'")
        return 1
    user, pw, src = get_credentials(key, csv_fb)
    print(f"=== {label} — discovering the real stock URL (credentials: {src}) ===")

    scraper = PlaywrightScraper(session_name=key)
    with scraper.session():
        page = scraper.page
        scraper.goto(cfg.login_url)
        page.wait_for_timeout(2000)

        if not scraper.is_logged_in(cfg.logged_in_selector) and user and pw:
            try:
                if cfg.open_login_selector:
                    for sel in cfg.open_login_selector.split(","):
                        try:
                            page.click(sel.strip(), timeout=4000); page.wait_for_timeout(1000)
                        except Exception:
                            pass
                page.fill(cfg.email_selector, user, timeout=10000)
                if cfg.continue_selector and not page.query_selector(cfg.password_selector):
                    page.click(cfg.continue_selector, timeout=8000)
                    page.wait_for_selector(cfg.password_selector, timeout=15000)
                page.fill(cfg.password_selector, pw, timeout=10000)
                page.click(cfg.submit_selector, timeout=10000)
                page.wait_for_load_state("networkidle", timeout=25000)
                page.wait_for_timeout(2500)
            except Exception as e:
                print(f"  login FAILED: {str(e).splitlines()[0][:110]}")
                return 1
        print(f"  authenticated: {scraper.is_logged_in(cfg.logged_in_selector)}  at {page.url[:70]}")

        origin = f"{urlparse(page.url).scheme}://{urlparse(page.url).netloc}"
        links = page.evaluate(LINKS_JS)
        same = [l for l in links if urlparse(l["href"]).netloc == urlparse(origin).netloc]
        cands, seen = [], set()
        for l in same:
            blob = (l["text"] + " " + l["href"]).lower()
            if any(h in blob for h in STOCK_HINTS) and l["href"] not in seen:
                seen.add(l["href"]); cands.append(l)
        print(f"  {len(links)} links, {len(cands)} stock-like candidates:")
        for l in cands[:14]:
            print(f"     - {l['text'][:34]:<34} {l['href'][:64]}")

        print("\n  --- testing candidates for readable listings ---")
        best = []
        for l in cands[:10]:
            try:
                scraper.goto(l["href"])
                page.wait_for_timeout(2500)
                got = extract_listings(page, builder_hint=label, state_hint="")
                marker = "  <== LISTINGS" if got else ""
                print(f"     {len(got):>3} listings | {l['text'][:26]:<26} {l['href'][:52]}{marker}")
                if got:
                    best.append((len(got), l["href"], got[:2]))
            except Exception as e:
                print(f"     ERR {l['text'][:26]:<26} {str(e).splitlines()[0][:44]}")
        if best:
            best.sort(reverse=True)
            n, url, sample = best[0]
            print(f"\n  >>> BEST STOCK URL: {url}  ({n} listings)")
            for s in sample:
                print(f"      e.g. {str(s['lot_address'])[:44]} | ${s['advertised_package_price']:,.0f}")
            print(f"\n  Set listings_url to this in sources/portal_config.py")
        else:
            print("\n  No candidate yielded listings — inventory may need a search form submitted.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "hermitage"))
