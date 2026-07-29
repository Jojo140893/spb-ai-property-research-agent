"""
Read-only probe of E-Agent's per-builder category pages. Writes nothing to the DB.

Answers the three questions the per-builder crawl depends on, against the live
authenticated site:

  1. Does each configured category route actually load? (Wix is a client-side-routed
     SPA, so a failed navigation silently leaves the previous page rendered.)
  2. Is the builder recoverable from the heading above each "Live Packages" link?
  3. How many listings does each builder's file yield?

Usage:
    python diagnose_e_agent.py            # headings + file counts only (no downloads)
    python diagnose_e_agent.py --parse    # also download and parse each file
"""

import logging
import sys
from urllib.parse import urlparse

from sources.e_agent import EAgentSource
from sources.scraper_base import PLAYWRIGHT_AVAILABLE, PlaywrightScraper

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main(parse: bool = False) -> int:
    if not PLAYWRIGHT_AVAILABLE:
        print("Playwright not installed.")
        return 1
    src = EAgentSource()
    scraper = PlaywrightScraper(session_name="e_agent")
    with scraper.session():
        scraper.goto(src.cfg.listings_url)
        pooled = src._stocklist_links(scraper)
        print(f"\n/access-projects  -> {len(pooled)} pooled stocklist file(s)")
        if not pooled:
            print("  (not authenticated — logging in)")
            if not src._login(scraper):
                print("  LOGIN FAILED")
                return 1
            scraper.goto(src.cfg.listings_url)
            pooled = src._stocklist_links(scraper)
            print(f"  after login -> {len(pooled)} pooled file(s)")
        for l in pooled:
            print(f"    - {l['label']!r}")

        total_rows = 0
        for cat in src.cfg.category_pages:
            route = cat.url.rsplit("/", 1)[-1]
            try:
                scraper.goto(cat.url)
            except Exception as e:
                print(f"\n{route:<26} UNREACHABLE: {e}")
                continue
            want = urlparse(cat.url).path.rstrip("/").lower()
            got = urlparse(scraper.page.url or "").path.rstrip("/").lower()
            loaded = "OK " if want == got else f"ROUTE MISMATCH (on {got})"
            links = src._stocklist_links(scraper)
            print(f"\n{route:<26} {cat.state or '-':<4} {loaded}  {len(links)} file(s)")
            if want != got:
                continue
            for l in links:
                heading = (l.get("heading") or "").strip(" -–—:|")
                # mirrors _scrape_category_pages: apartments / townhouses / commercial
                # are grouped by development, not by builder
                is_builder = src._is_builder_heading(heading)
                project_page = cat.product_type not in ("House & Land", "")
                skip = src._NOT_STOCK_LABEL.search(l.get("label") or "") or \
                    (not is_builder and not project_page)
                ok = not skip
                scope = "builder" if (is_builder and not project_page) else "project"
                mark = (scope[:7] if ok else "SKIP   ").ljust(7)
                extra = ""
                if parse and ok:
                    rows = src._parse_stocklist(scraper, l, heading if is_builder else "",
                                                scope, cat.state, cat.product_type)
                    total_rows += len(rows)
                    extra = f"  -> {len(rows)} listing(s)"
                    if rows:
                        r = rows[0]
                        extra += (f"  e.g. {r.get('lot_address')!r} "
                                  f"${(r.get('advertised_package_price') or 0):,.0f}")
                print(f"    [{mark}] {heading!r:<42} {l['label']!r}{extra}")
        if parse:
            print(f"\nTOTAL per-builder listings parsed: {total_rows}")
    return 0


if __name__ == "__main__":
    sys.exit(main(parse="--parse" in sys.argv))
