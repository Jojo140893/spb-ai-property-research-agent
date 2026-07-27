"""
Prove the adaptive extractor pulls listings from an UNKNOWN portal layout with no
hand-mapped selectors — this is what removes the per-portal mapping step.

Two fixture pages with deliberately different markup (semantic cards vs. a table)
and no classes the code knows about, served from a local http.server.
"""

import http.server
import socketserver
import tempfile
import threading
import time
from pathlib import Path

# Layout A: div cards with arbitrary class names
PAGE_CARDS = """<html><body><main>
<section class="xq7-grid">
  <article class="zz-item">
     <h3>Lot 214 Highland Rise</h3>
     <p>Coomera, QLD</p>
     <p>4 bed | 2 bath | 2 car</p>
     <p>Land 400m2 &middot; Home 185m2</p>
     <p>Titled</p>
     <span>$725,000</span>
     <a href="/stock/214">View</a>
  </article>
  <article class="zz-item">
     <h3>Lot 88 Parkview Estate</h3>
     <p>Springfield, QLD</p>
     <p>3 bed | 2 bath | 1 car</p>
     <p>Land 350m2 &middot; Home 160m2</p>
     <span>$639,500</span>
     <a href="/stock/88">View</a>
  </article>
  <article class="zz-item">
     <h3>Lot 9 Riverstone</h3>
     <p>Pimpama, QLD</p>
     <p>5 bed | 3 bath | 2 car</p>
     <p>Land 512m2 &middot; Home 240m2</p>
     <span>$899,000</span>
     <a href="/stock/9">View</a>
  </article>
</section></main></body></html>"""

# Layout B: a plain table, totally different structure
PAGE_TABLE = """<html><body>
<table><tbody>
  <tr><td>Lot 31 Marina Views</td><td>Hope Island QLD</td><td>4 bed 2 bath 2 car</td><td>430m2</td><td>$812,000</td></tr>
  <tr><td>Lot 32 Marina Views</td><td>Hope Island QLD</td><td>3 bed 2 bath 2 car</td><td>375m2</td><td>$688,000</td></tr>
</tbody></table></body></html>"""


def _serve(files: dict):
    tmp = Path(tempfile.mkdtemp())
    for name, html in files.items():
        (tmp / name).write_text(html, encoding="utf-8")

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(tmp), **k)
        def log_message(self, *a):
            pass

    httpd = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.25)
    return httpd, httpd.server_address[1]


def test_adaptive_extracts_from_unknown_layouts():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return
    from sources.adaptive_extract import extract_listings

    httpd, port = _serve({"cards.html": PAGE_CARDS, "table.html": PAGE_TABLE})
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_context().new_page()

            # --- Layout A: unknown div/article cards ---
            page.goto(f"http://127.0.0.1:{port}/cards.html", wait_until="domcontentloaded")
            got = extract_listings(page, builder_hint="Test Builder", state_hint="QLD")
            assert len(got) == 3, f"expected 3 listings from card layout, got {len(got)}"
            by_price = {int(g["advertised_package_price"]): g for g in got}
            assert set(by_price) == {725000, 639500, 899000}, f"prices wrong: {sorted(by_price)}"
            top = by_price[725000]
            assert "Lot 214" in top["lot_address"]
            assert top["bedrooms"] == 4 and top["bathrooms"] == 2 and top["car_spaces"] == 2
            assert top["land_size_sqm"] == 400 and top["house_size_sqm"] == 185
            assert top["suburb"] == "Coomera" and top["state"] == "QLD"
            assert top["builder_name"] == "Test Builder"
            assert top["source_url_or_ref"].endswith("/stock/214")

            # --- Layout B: a table, no shared markup with layout A ---
            page.goto(f"http://127.0.0.1:{port}/table.html", wait_until="domcontentloaded")
            got2 = extract_listings(page, builder_hint="Table Builder", state_hint="QLD")
            assert len(got2) == 2, f"expected 2 listings from table layout, got {len(got2)}"
            prices2 = sorted(int(g["advertised_package_price"]) for g in got2)
            assert prices2 == [688000, 812000], f"table prices wrong: {prices2}"
            assert any("Lot 31" in g["lot_address"] for g in got2)
            assert all(g["bedrooms"] in (3, 4) for g in got2)
            browser.close()
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    import sys
    try:
        test_adaptive_extracts_from_unknown_layouts()
        print(" [PASS] adaptive extractor handles unknown layouts")
    except AssertionError as e:
        print(f" [FAIL] {e}")
        sys.exit(1)
