"""Use the app the way Coleen does, and check every error she has reported is still gone.

Every other test in this suite reasons about functions. This one opens the published
dashboard in a real browser, clicks what she clicks, and reads what she reads -- because
the failures she reports are things she SEES, and twice this project answered a report by
measuring a column that was not the one on her screen.

  Colin, 5 Aug:   "it's not available, but it came as available from your end"
  Coleen, 9 Sep:  she clicked "Price list - row 24" and a different property opened
  Coleen, 28 Jul: "we should see the state. That's one of the main things."
  Colin, 5 Aug:   "This one is a lot. It's land. It's not a house... how come they have
                   bedrooms and baths?"
  Coleen:         "the realestate.com.au ones are a totally another category named
                   BENCHMARKS"
  Coleen:         "if someone wants to click, they can click here and see the PDF."
  Coleen, 14 Aug: "make the filter part sticky"

Runs against vercel_site/ -- the exact bundle that gets deployed -- served locally, so the
static fallbacks (stock.json, channels.json) stand in for the API. SKIPPED, never failed,
when there is no build to open or no browser to open it with: a missing local artefact is
not a regression in the product.

    python -m pytest tests/test_client_journey_ui.py -v
"""
import functools
import http.server
import re
import socket
import socketserver
import threading
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
SITE = APP / "vercel_site"

pytestmark = pytest.mark.skipif(
    not (SITE / "index.html").is_file() or not (SITE / "stock.json").is_file(),
    reason="no built bundle in vercel_site/ - run build_web.py first")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def site_url():
    """The published bundle on a local port, for the life of the module."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(SITE))

    class _Quiet(socketserver.TCPServer):
        allow_reuse_address = True
        def handle_error(self, *_a):       # a browser closing a socket is not news
            pass

    port = _free_port()
    server = _Quiet(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def page(site_url):
    """The dashboard, loaded once, with its stock table rendered."""
    playwright = pytest.importorskip("playwright.sync_api",
                                     reason="playwright is not installed")
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:           # no browser binary on this machine
            pytest.skip(f"chromium will not launch here: {type(exc).__name__}")
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(site_url, wait_until="networkidle")
        pg.get_by_role("button", name="Building Stock").click()
        pg.wait_for_function(
            "() => document.body.innerText.includes('shown')", timeout=30000)
        pg.console_errors = errors
        try:
            yield pg
        finally:
            browser.close()


@pytest.fixture(autouse=True)
def _fresh_view(page):
    """Every journey starts where Coleen starts: Building Stock, nothing filtered.

    The page is shared for speed, so without this the tests are not independent -- a
    search term left in the box by one test showed the next one two rows, and the
    source-link check quietly SKIPPED itself for want of anything to look at. A skip is
    not a pass, and that one was hiding the very bug she reported.
    """
    page.get_by_role("button", name="Building Stock").click()
    box = page.get_by_placeholder(re.compile("Search builder", re.I))
    if box.count():
        box.fill("")
    page.wait_for_timeout(400)
    yield


def _count_line(page) -> str:
    """The line under the table: "9,371 of 10,836 shown - 1,465 sold/unavailable hidden"."""
    text = page.inner_text("body")
    match = re.search(r"[\d,]+ of [\d,]+ shown[^\n]*", text)
    assert match, "the stock table never rendered its count line"
    return match.group(0)


def _search(page, term) -> int:
    """Type a term into the table's search box and return how many rows it shows."""
    box = page.get_by_placeholder(re.compile("Search builder", re.I))
    box.fill("")
    box.fill(term)
    page.wait_for_timeout(600)
    shown = re.search(r"([\d,]+) of", _count_line(page))
    return int(shown.group(1).replace(",", ""))


# ----------------------------------------------------------------- what she must not see

def test_the_table_says_it_is_hiding_sold_stock(page):
    """Colin, 5 Aug: "it's not available, but it came as available from your end ... you
    should only see what's available and nothing else."

    The count line is the app's own statement about this, on screen, unprompted.
    """
    line = _count_line(page)
    assert "sold/unavailable hidden" in line, \
        f"the table no longer reports hiding unavailable stock: {line!r}"
    hidden = int(re.search(r"([\d,]+) sold/unavailable hidden", line)
                 .group(1).replace(",", ""))
    assert hidden > 0, "nothing is being hidden; the availability filter is off"


def test_no_row_on_screen_reads_as_sold(page):
    """Ahsan, 7 Sep: "I want you to see that there are not any buildings that sold or not
    available. Show up in the the filter because I see".

    Twice this was answered by measuring availability_status, finding it clean, and saying
    so -- while the word SOLD sat in the ADDRESS column. This reads the rendered table.
    """
    rows = page.eval_on_selector_all(
        "table tbody tr",
        "els => els.slice(0, 150).map(e => e.innerText)")
    assert rows, "no rows rendered in the stock table"
    sold = re.compile(r"\b(sold|on\s*hold|under\s*offer|withdrawn|not available)\b", re.I)
    offenders = [r for r in rows if sold.search(r)]
    assert not offenders, (
        f"{len(offenders)} visible row(s) read as sold, e.g. "
        f"{offenders[0][:120]!r}")


def test_land_is_not_shown_with_bedrooms(page):
    """Colin, 5 Aug: "This one is a lot. It's land. It's not a house... how come they have
    bedrooms and baths?" """
    import json
    data = json.loads((SITE / "stock.json").read_text(encoding="utf-8"))
    at = {k: i for i, k in enumerate(data["keys"])}
    if "product_type" not in at or "bedrooms" not in at:
        pytest.skip("the snapshot does not carry product_type/bedrooms")
    bad = [r for r in data["rows"]
           if not r[at["superseded_by"]]
           and str(r[at["product_type"]] or "") == "Land" and (r[at["bedrooms"]] or 0)]
    assert not bad, f"{len(bad)} land listings carry a bedroom count"


# --------------------------------------------------------------- what she must be able to find

@pytest.mark.parametrize("term, why", [
    ("Geelong", "Coleen searched Geelong and the app returned nothing"),
    ("Box Hill", "the Box Hill / Irving Domain townhouse was reported missing"),
    ("Koop", "Coleen found the Koop Cls building in E-Agent and not in the app"),
])
def test_a_search_she_reported_as_empty_now_returns_stock(page, term, why):
    found = _search(page, term)
    assert found > 0, f"{term!r} still returns nothing - {why}"


def test_the_search_box_actually_filters(page):
    """A search that returns EVERYTHING is as broken as one that returns nothing, and
    looks fine in a count."""
    everything = _search(page, "")
    narrowed = _search(page, "Koop")
    assert narrowed < everything, "the search box does not narrow the table"


# ------------------------------------------------------------------ what she must be told

def test_the_state_is_on_the_row(page):
    """Coleen, 28 July: "we should see the state. That's one of the main things." """
    headers = page.eval_on_selector_all("table thead th", "els => els.map(e => e.innerText)")
    assert any(re.search(r"\bstate\b", h, re.I) for h in headers), \
        f"no State column in the stock table: {headers}"


def test_a_source_link_says_where_in_the_document_and_when(page):
    """Coleen, 9 Sep: she clicked "Price list - row 24" and a different property opened.

    A row number is a position in a file the builder rewrites, so the label has to date
    itself. Colin, 9 Aug, asked for the position in the first place: "if it is in the
    sheet I need the row number of that sheet where that building is."
    """
    labels = page.eval_on_selector_all(
        "a[title]", "els => els.slice(0, 400).map(e => e.getAttribute('title'))")
    positioned = [t for t in labels if t and re.search(r"\b(row|page) \d+", t)]
    assert positioned, (
        "no source link on screen names a line or page. Colin asked for the position "
        "precisely so he would not have to scan 400 rows for a lot number; if none "
        "renders, that feature is gone.")
    dated = [t for t in positioned if re.search(r"as at \d{1,2} [A-Z][a-z]{2}", t)]
    assert dated, (
        "source links name a line but never say which day it was true: "
        f"{positioned[0][:120]!r}")


def test_the_settings_tab_opens_and_reports_each_source(page):
    """Coleen reported this tab stuck on "Loading..." and, separately, asked what
    "ONLY PART OF ITS STOCK" meant. Both require it to actually render."""
    page.get_by_role("button", name="Settings").click()
    page.wait_for_function(
        "() => document.body.innerText.includes('Last sent stock')", timeout=30000)
    text = page.inner_text("body")
    assert "Loading" not in text.split("Last sent stock")[0][-200:], \
        "the Settings tab is still showing a loading state"
    for source in ("E-Agent", "Proxima"):
        assert source in text, f"{source} is missing from the sources table"


def test_the_sheet_check_result_is_on_the_settings_tab(page):
    """The nightly opens each builder's price list and checks every listing against it.
    A confirmation nobody can see is not a confirmation."""
    page.get_by_role("button", name="Settings").click()
    page.wait_for_function(
        "() => document.body.innerText.includes('Last sent stock')", timeout=30000)
    text = page.inner_text("body")
    if "Checked against the builders" not in text:
        pytest.skip("no sheet-check report in this build (the step is optional)")
    assert "could not be judged" in text, \
        "the panel reports only what it confirmed, not what it refused to judge"


def test_the_benchmarks_tab_is_a_separate_category(page):
    """Coleen: "the realestate.com.au ones are a totally another category named
    BENCHMARKS" -- they are the market, not stock she can sell."""
    page.get_by_role("button", name=re.compile("Benchmarks")).click()
    page.wait_for_function(
        "() => document.body.innerText.includes('realestate.com.au')", timeout=30000)
    text = page.inner_text("body")
    assert "not stock you can sell" in text or "not counted in Building Stock" in text, \
        "the Benchmarks tab no longer distinguishes the market from her own stock"


def test_the_brochures_tab_opens_the_file(page):
    """Coleen: "if someone wants to click, they can click here and see the PDF." """
    page.get_by_role("button", name=re.compile("Vendor Brochures")).click()
    page.wait_for_function(
        "() => document.body.innerText.includes('ASSETS')", timeout=30000)
    links = page.eval_on_selector_all(
        "a[href]", "els => els.map(e => e.getAttribute('href')).filter(Boolean)")
    assert any(h.lower().endswith(".pdf") or "/assets/" in h.lower() for h in links), \
        "no brochure on the tab opens a file"


# ------------------------------------------------------------------------- and nothing broke

def test_the_page_raises_no_javascript_errors(page):
    """A tab that throws renders empty, which is how "stuck on Loading" looked."""
    assert not page.console_errors, \
        f"the page raised {len(page.console_errors)} JS error(s): {page.console_errors[:2]}"
