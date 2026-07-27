"""
Tests for the vendor onboarding pipeline: CSV import + website asset scraping.

The scraper test runs against a LOCAL fixture site (a threaded http.server on
localhost) so it exercises the real crawl -> download -> hash -> dedup -> DB
path with zero external network calls.
"""

import http.server
import socketserver
import tempfile
import threading
import time
from pathlib import Path

import config
from database import ResearchDatabase
from vendor_import import VendorImporter

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"

VENDORS_CSV = config.DRIVE_INPUT_DIR / "vendors.csv"


_FIXTURE_CSV = """NAME,EMAIL,PHONE,BUILDER,STATES,Contract Availble??,Is it available on E Agent?,WEB PORTAL LINK,EMAIL,PASSWORD,NOTES
Alex Q,alex@avia.com.au,0400,Avia Homes,QLD,Yes,YES,https://www.e-agent.com.au,user@spb.com,pw,
,,,Villie Building Group,QLD,Yes,,https://villiebuildinggroup.com.au,,,
,,,Hermitage Homes,VIC,,YES,https://portal.hermitagehomes.com.au/,sales@x.com,pw,
,,,,,,,,,,
NAME,EMAIL,PHONE,BUILDER,STATES,Title,LinkedIn Link,STATUS,,,
Adam Barclay,adam@x.com,,Gallery Group,QLD,CEO,https://linkedin.com/in/adam,Email Sent,,,
Jon Rivera,,,Urbane Homes,VIC,Director,https://linkedin.com/in/jon,Not connected,,,
,,,,,,,,,,
BUILDERS IN PERTH,Email,Phone,Website,,,,,,,
Nulook Homes,info@nulook.com.au,9349,https://www.nulookhomes.com.au/,Perth,,,,,,
"""


def test_importer_parses_messy_multisection_csv():
    # Self-contained fixture so the test doesn't depend on whichever CSV the user
    # currently has in drive_input/.
    tmp = Path(tempfile.mkdtemp())
    fx = tmp / "vendors_fixture.csv"
    fx.write_text(_FIXTURE_CSV, encoding="utf-8")
    db = ResearchDatabase(db_path=tmp / "spb_vendor_test.db")
    summary = VendorImporter(db).import_to_db(fx)
    names = {b["builder_name"].lower() for b in db.get_builders()}
    # Real vendors across sections present
    assert any("avia homes" in n for n in names)
    assert any("villie building group" in n for n in names)
    assert any("nulook homes" in n for n in names)  # Perth section
    assert any("hermitage homes" in n for n in names)
    # LinkedIn prospect *people* must NOT be imported as builders
    assert not any(n == "adam barclay" for n in names)
    assert not any(n == "jon rivera" for n in names)
    # website vs portal disambiguation
    villie = next(b for b in db.get_builders() if "villie" in b["builder_name"].lower())
    assert villie["website"] and villie["has_website"] == 1


def _make_fixture_site(root: Path):
    (root / "index.html").write_text(
        "<html><body><h1>Test Homes</h1>"
        "<a href='/downloads/range-brochure.pdf'>Download our range brochure</a>"
        "<a href='/house-and-land.html'>House and Land packages</a>"
        "<a href='https://external.example.com/other.pdf'>offsite pdf</a>"
        "</body></html>", encoding="utf-8")
    (root / "house-and-land.html").write_text(
        "<html><body><a href='/downloads/floorplan.pdf'>Floor plan PDF</a></body></html>",
        encoding="utf-8")
    (root / "downloads").mkdir(exist_ok=True)
    (root / "downloads" / "range-brochure.pdf").write_bytes(MINIMAL_PDF)
    (root / "downloads" / "floorplan.pdf").write_bytes(MINIMAL_PDF + b" v2")  # different hash


def test_website_scraper_downloads_and_dedupes():
    try:
        from sources.website_scraper import WebsiteAssetScraper, PLAYWRIGHT_AVAILABLE
    except Exception:
        return
    if not PLAYWRIGHT_AVAILABLE:
        return

    tmp = Path(tempfile.mkdtemp())
    site = tmp / "site"
    site.mkdir()
    _make_fixture_site(site)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(site), **k)
        def log_message(self, *a):
            pass

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.3)

    orig_assets = config.ASSETS_DIR
    config.ASSETS_DIR = tmp / "assets"
    config.ASSETS_DIR.mkdir()
    try:
        db = ResearchDatabase(db_path=tmp / "vendor_scrape_test.db")
        scraper = WebsiteAssetScraper(db)
        base = f"http://127.0.0.1:{port}/"

        r1 = scraper.scrape_builder("Test Homes", base)
        assert r1["error"] is None, f"scrape errored: {r1['error']}"
        # crawled home + house-and-land page; found both same-origin PDFs, skipped the offsite one
        assert r1["pages_visited"] >= 2
        assert r1["assets_found"] == 2, f"expected 2 same-origin PDFs, got {r1['assets_found']}"
        assert r1["assets_new"] == 2, f"expected 2 downloads, got {r1['assets_new']}"

        assets = db.get_assets("Test Homes")
        assert len(assets) == 2
        assert all(Path(a["local_path"]).exists() for a in assets)
        assert any(a["asset_type"] == "brochure" for a in assets)
        assert any(a["asset_type"] == "floorplan" for a in assets)

        # Re-run: identical content must dedupe by sha256 (no new rows)
        r2 = scraper.scrape_builder("Test Homes", base)
        assert r2["assets_new"] == 0, "re-scrape must not duplicate identical files"
        assert len(db.get_assets("Test Homes")) == 2
    finally:
        config.ASSETS_DIR = orig_assets
        httpd.shutdown()


def run_all():
    tests = [
        ("Vendor importer parses messy CSV", test_importer_parses_messy_multisection_csv),
        ("Website scraper downloads + dedupes", test_website_scraper_downloads_and_dedupes),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] {name}: {e}")
        except Exception as e:
            failed += 1
            print(f" [ERROR] {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
