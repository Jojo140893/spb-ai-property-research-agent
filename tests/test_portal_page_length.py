"""A paged table shows ten rows and calls it the stock list.

Hermitage's /allpackages is a DataTables grid with a "1 2 3 4 5 ... 10" pager and a
page-length select defaulting to 10. Nothing in the portal scraper ever touched either,
so the harvest read the first page and reported it as the whole portal — 92 listings
against the 171 the portal's own config records, and "ONLY PART OF ITS STOCK" on the
dashboard every day.

Scrolling does not help: the pager is JavaScript, not lazy loading. Measured live after
signing in: 92 -> 182.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, "api")

from sources.builder_portals import _EXPAND_PAGE_LENGTH_JS

playwright = pytest.importorskip("playwright.sync_api")


def _run(html, tmp_path):
    page_file = tmp_path / "t.html"
    page_file.write_text(html, encoding="utf-8")
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(pathlib.Path(page_file).as_uri())
        chosen = page.evaluate(_EXPAND_PAGE_LENGTH_JS)
        shown = page.evaluate("() => document.querySelector('select').value")
        browser.close()
    return chosen, shown


def test_it_picks_the_largest_page_length(tmp_path):
    html = """<html><body><select name="listtable_length">
      <option value="10" selected>10</option><option value="25">25</option>
      <option value="50">50</option><option value="100">100</option>
      </select></body></html>"""
    chosen, shown = _run(html, tmp_path)
    assert chosen == "100" and shown == "100"


def test_it_leaves_a_non_numeric_select_alone(tmp_path):
    """A sort or a suburb filter is not a page length. Changing one would silently
    re-filter the stock we then read — the opposite of reading all of it."""
    html = """<html><body><select name="order">
      <option value="">Sort</option><option value="total_price">Price</option>
      <option value="beds">Beds</option></select></body></html>"""
    chosen, _shown = _run(html, tmp_path)
    assert chosen == ""


def test_it_does_nothing_when_already_at_the_maximum(tmp_path):
    """Returning "" means "no change", so the caller does not wait for a re-render that
    is never coming."""
    html = """<html><body><select><option value="10">10</option>
      <option value="100" selected>100</option></select></body></html>"""
    chosen, _shown = _run(html, tmp_path)
    assert chosen == ""


def test_the_scraper_actually_calls_it():
    import inspect
    from sources import builder_portals

    src = inspect.getsource(builder_portals._load_every_card)
    assert "_EXPAND_PAGE_LENGTH_JS" in src, (
        "widening the table is the fix; the loop must run it before counting")
