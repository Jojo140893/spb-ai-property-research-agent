"""Hermitage: every priced card on the page, not the first 200 (plan rank 6, 7 Oct 2026).

The adaptive reader capped a page at CARD_CAP = 200 cards. Hermitage's /allpackages grid,
widened to 100 rows a page and fully loaded, renders 240-263 priced cards a night; the log
said so every night ("adaptive: 263 priced card(s) on this page, only 200 read" on 6 Oct)
and the last 40-63 packages were never read. Raised to 2,000. The 1,200-character card
limit and the "only N read" warning stay: a page bigger than that is still announced.

Runs the real JS in real headless Chromium on an in-memory page (no network, no browser
profile), as tests/test_adaptive_groups.py does.
"""
import logging
import re

import pytest

from sources.adaptive_extract import FIND_CARDS_JS, extract_listings

sync_playwright = pytest.importorskip(
    "playwright.sync_api", reason="playwright is not installed").sync_playwright


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        yield pg
        browser.close()


def _grid(n):
    rows = "".join(
        f"<tr class=pkg><td><a href='/package?id={30000 + i}'>LOT {1000 + i} "
        f"EXAMPLE DRIVE, TARNEIT</a></td><td>4 bed 2 bath 2 car</td>"
        f"<td>${500000 + i * 1000:,}</td></tr>" for i in range(n))
    return f"<html><body><table><tbody>{rows}</tbody></table></body></html>"


def test_all_263_cards_are_read(page):
    page.set_content(_grid(263))
    out = page.evaluate(FIND_CARDS_JS)
    assert out["found"] == 263
    assert len(out["cards"]) == 263, f"read {len(out['cards'])} of 263"


def test_every_package_becomes_a_listing(page):
    page.set_content(_grid(263))
    rows = extract_listings(page, builder_hint="Hermitage Homes", state_hint="VIC")
    assert len(rows) == 263, len(rows)


def test_a_page_beyond_the_cap_is_still_announced(page, caplog):
    m = re.search(r"CARD_CAP\s*=\s*(\d+)", FIND_CARDS_JS)
    cap = int(m.group(1))
    assert cap >= 2000
    page.set_content(_grid(cap + 5))
    with caplog.at_level(logging.WARNING):
        rows = extract_listings(page, builder_hint="Hermitage Homes", state_hint="VIC")
    assert len(rows) == cap
    assert any(f"{cap + 5} priced card(s) on this page, only {cap} read" in r.getMessage()
               for r in caplog.records)
