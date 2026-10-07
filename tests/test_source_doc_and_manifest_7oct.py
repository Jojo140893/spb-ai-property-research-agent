"""Every portal / HTML-page row names its page; every source gets a manifest line (7 Oct 2026).

RANK 11. A row with no source_url has no document for the search to group it with, so
api/_candidates refuses it as "no_document" and it can never be offered to confirm. On
6 Oct: 32 live direct-portal rows (Torsion 17, Bathla 14, Hermitage 1) and the E-Agent
Torsion HTML-stocklist rows whose cards carry no link -- trace_listing --id 12437 "PARK
RIDGE" says "the row names no document". The harvest now stamps the page each row was
read from; repair_rows fills existing rows from the link they were read from.

RANK 19. One Google sheet gave the harvest 22 rows and the sheet check 53 the same night,
and nothing said which endpoint served which read. Now both readers log it.

NEW. data/harvest_manifest.json: one line per E-Agent link, Proxima project and portal,
with what the extractor printed and what record_building stored -- "rejected" included.
"""
import json
import logging

import pytest

import harvest_manifest as HM


@pytest.fixture(autouse=True)
def _fresh_manifest():
    HM.reset()
    yield
    HM.reset()


# ------------------------------------------------------------------ the page stamp

def test_a_portal_row_with_no_card_link_names_its_page():
    from sources.builder_portals import _stamp_page
    rows = [{"lot_address": "LOT 5 X ST", "source_url_or_ref": ""},
            {"lot_address": "LOT 6 X ST", "source_url_or_ref": "https://p/package?id=6"}]
    _stamp_page(rows, "https://portal.example/display")
    assert rows[0]["source_url_or_ref"] == "https://portal.example/display"
    assert rows[1]["source_url_or_ref"] == "https://p/package?id=6", "a card's own link wins"


def test_an_e_agent_html_stocklist_row_names_its_page(monkeypatch):
    from sources import e_agent as EA

    class _Scraper:
        page = object()

        def goto(self, url):
            self.url = url

    monkeypatch.setattr(EA, "extract_listings", lambda page, **k: [
        {"lot_address": "PARK RIDGE", "advertised_package_price": 650000,
         "source_url_or_ref": ""}])
    src = EA.EAgentSource.__new__(EA.EAgentSource)
    page = "https://referrer.torsionhomes.au/Stocklist.aspx?DriveID=d&FolderID=f"
    rows = src._scrape_html_stocklist(_Scraper(), page, "Torsion Homes", "QLD")
    assert rows[0]["source_url_or_ref"] == page


def test_the_page_reaches_the_stored_row(tmp_path):
    from database import ResearchDatabase
    import sqlite3
    db = ResearchDatabase(db_path=tmp_path / "x.db")
    db.record_building({"source_channel": "E-Agent", "builder_name": "Torsion Homes",
                        "lot_address": "PARK RIDGE", "advertised_package_price": 650000,
                        "source_url_or_ref": "https://referrer.torsionhomes.au/Stocklist.aspx"})
    got = sqlite3.connect(str(tmp_path / "x.db")).execute(
        "SELECT source_url FROM buildings").fetchone()[0]
    assert got == "https://referrer.torsionhomes.au/Stocklist.aspx"


def test_the_repair_fills_stored_rows_from_the_link_they_were_read_from():
    import repair_rows as RR
    rows = [
        {"id": 1, "superseded_by": None, "source_url": "",
         "stocklist_file": "https://referrer.torsionhomes.au/Stocklist.aspx?DriveID=d"},
        {"id": 2, "superseded_by": None, "source_url": "https://keep/me",
         "stocklist_file": "https://other"},                       # names one already
        {"id": 3, "superseded_by": None, "source_url": None, "stocklist_file": None},
        {"id": 4, "superseded_by": "v2:x", "source_url": "", "stocklist_file": "https://a"},
    ]
    got = {r["id"]: url for r, url in RR.find_source_url_backfill(rows)}
    assert got == {1: "https://referrer.torsionhomes.au/Stocklist.aspx?DriveID=d"}


# ------------------------------------------------------------------ Google endpoint

def test_the_google_endpoint_and_row_count_are_logged(caplog):
    from sources.remote_stocklist import _fetch_google

    class _Resp:
        def __init__(self, body, status, ctype):
            self.content = body.encode()
            self.status_code, self.ok = status, status == 200
            self.headers = {"Content-Type": ctype}
            self.text = body

    class _Sess:
        def get(self, url, **kw):
            if "/htmlview" in url:
                return _Resp('items.push({name: "Verv", pageUrl: "x", gid: "11"});', 200,
                              "text/html")
            if "/export" in url:
                return _Resp("", 401, "text/html")
            return _Resp("Lot,Price\nT1,$1\nT2,$2\n", 200, "text/csv")

    with caplog.at_level(logging.INFO, logger="spb.remote"):
        got = _fetch_google("https://docs.google.com/spreadsheets/d/" + "C" * 30 +
                            "/edit#gid=11", "Havenwood", _Sess())
    assert got[0].meta["endpoint"] == "gviz" and got[0].meta["csv_rows"] == 3
    assert any("served by gviz, 3 CSV row(s)" in r.getMessage() for r in caplog.records)


def test_the_row_count_is_csv_rows_and_the_sheet_check_never_logs_the_link(caplog):
    """The sheet check passes the URL as the label. A quoted cell may hold a line break."""
    from sources.remote_stocklist import _fetch_google

    class _Resp:
        def __init__(self, body, status, ctype):
            self.content = body.encode()
            self.status_code, self.ok = status, status == 200
            self.headers = {"Content-Type": ctype}
            self.text = body

    class _Sess:
        def get(self, url, **kw):
            if "/htmlview" in url:
                return _Resp('items.push({name: "Verv", pageUrl: "x", gid: "11"});', 200,
                              "text/html")
            return _Resp('Lot,Note\nT1,"two\nlines"\n\nT2,x\n', 200, "text/csv")

    link = "https://docs.google.com/spreadsheets/d/" + "D" * 30 + "/edit#gid=11"
    with caplog.at_level(logging.INFO, logger="spb.remote"):
        got = _fetch_google(link, link, _Sess())
    assert got[0].meta["endpoint"] == "export" and got[0].meta["csv_rows"] == 3
    said = [r.getMessage() for r in caplog.records if "served by" in r.getMessage()]
    assert said and "docs.google.com" not in said[0] and "D" * 30 not in said[0], said


# ------------------------------------------------------------------ the manifest

def test_the_manifest_counts_what_was_stored_per_source(tmp_path, monkeypatch):
    """A whole harvest() with every channel faked: each source's line carries what its
    extractor printed and what record_building did with it, rejections included."""
    import config
    import harvest_buildings as HB
    from database import ResearchDatabase
    import sqlite3

    path = tmp_path / "m.db"
    monkeypatch.setattr(config, "DATABASE_PATH", path)
    ResearchDatabase(db_path=path)
    con = sqlite3.connect(str(path))
    # a constraint the lookup cannot see, so one E-Agent lot is rejected
    con.execute("CREATE UNIQUE INDEX t_addr ON buildings(lot_address)")
    con.execute("INSERT INTO buildings (lot_address, dedup_key, content_hash) "
                "VALUES ('Lot 2 Taken', 'legacy', 'v2:legacy')")
    con.commit()
    con.close()

    href = "https://www.dropbox.com/scl/fo/abc/def?rlkey=secret&dl=0"

    class FakeEA:
        def __init__(self, *a, **k):
            self.username, self.password = "u", "p"

        def search(self, filters):
            e = HM.entry("E-Agent", "stock link", href, name="Suncourt Residences",
                         heading="Suncourt Residences", page="apartments-qld", state="QLD",
                         stocklist_key=HM.stocklist_key(href))
            HM.add_piece(e, "Access Portal / Suncourt - Sales List.xlsx", "read", 2)
            rows = [{"lot_address": "Lot 1 Suncourt", "advertised_package_price": 500000,
                     "stocklist_file": href},
                    {"lot_address": "Lot 2 Taken", "advertised_package_price": 510000,
                     "stocklist_file": href}]
            HM.tag(rows, e)
            return rows

    class FakePx:
        def __init__(self, *a, **k):
            self.projects_with_stock = self.projects_seen = 1
            self.cross_listed, self.filter_found, self.short_projects = [], {}, []
            self.project_log = [{"pid": "724", "name": "Lindfield House", "on_index": False,
                                 "via": "accordion", "header_counter": None, "stated": 107,
                                 "printed": 107, "replays": 2, "bands": 0, "short": False}]

        def search(self, filters):
            return [{"lot_address": f"Lot {i}, 1 X St, LINDFIELD, NSW, 2070",
                     "source_channel": "Proxima", "source_project_id": "724",
                     "advertised_package_price": 900000 + i} for i in range(3)]

    monkeypatch.setattr(HB, "EAgentSource", FakeEA)
    monkeypatch.setattr(HB, "ProximaSource", FakePx)
    monkeypatch.setattr(HB, "_require_readers", lambda: True)
    monkeypatch.setattr(HB, "BuilderRegistry", lambda: type(
        "R", (), {"get_all_builders": lambda s: []})())
    out = tmp_path / "harvest_manifest.json"
    monkeypatch.setattr(HM, "write", lambda path=None, _w=HM.write: _w(out))
    monkeypatch.setattr(HB, "_record_unreadable_stocklists", lambda: None)
    HB.harvest(eagent=True, portals=False, email=False, proxima=True, rea=False)

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema"] == 1
    by_name = {e["name"]: e for e in data["entries"]}
    ea = by_name["Suncourt Residences"]
    assert ea["printed"] == 2 and ea["stored"] == {"new": 1, "updated": 0, "unchanged": 0,
                                                   "rejected": 1}, ea
    px = by_name["Lindfield House"]
    assert px["stored"]["new"] == 3 and px["stated"] == 107 and px["kind"] == "project"
    assert data["totals"]["by_channel"]["E-Agent"]["rejected"] == 1
    text = out.read_text(encoding="utf-8")
    assert "dropbox.com" not in text and "rlkey" not in text, "a share link was written"


def test_a_link_inside_an_error_message_never_reaches_the_manifest(tmp_path):
    """requests puts the URL in its exceptions; "download failed: {e}" carried it in."""
    e = HM.entry("E-Agent", "stock link", "k", name="Murcia Residences")
    HM.add_piece(e, "Access Portal / Price List/report_murcia.pdf", "fetch failed", 0,
                 "download failed: HTTPSConnectionPool(host='www.dropbox.com', port=443): "
                 "Max retries exceeded with url: /scl/fo/abc123/AAx?rlkey=secret9&dl=1")
    HM.entry("E-Agent", "stock link", "k2", name="https://www.dropbox.com/scl/fo/z?rlkey=q")
    text = HM.write(tmp_path / "m.json").read_text(encoding="utf-8")
    for leak in ("rlkey", "dropbox.com", "/scl/fo/", "secret9"):
        assert leak not in text, leak
    piece = json.loads(text)["entries"][0]["pieces"][0]
    assert piece["name"] == "Access Portal / Price List/report_murcia.pdf"
    assert piece["why"].startswith("download failed")


def test_the_schema_is_documented_where_it_is_written():
    doc = HM.__doc__
    for word in ("stocklist_key", "pieces", "header_counter", "cards_found", "rejected",
                 "skipped by label filter", "zero-yield", "private link", "no price list"):
        assert word in doc, word
    for outcome in HM.OUTCOMES:
        assert outcome in doc, outcome


def test_a_stock_host_link_the_label_filter_skips_is_set_aside_not_lost():
    """A link whose text is not a stock label was dropped without a log line."""
    from sources import e_agent as EA

    links = [
        {"href": "https://www.dropbox.com/scl/fo/a/b?rlkey=k&dl=0", "label": "Click here",
         "heading": "Regatta"},
        {"href": "https://www.dropbox.com/scl/fo/c/d?rlkey=k&dl=0", "label": "Live Packages",
         "heading": "Babylon"},
        {"href": "https://www.dropbox.com/scl/fo/e/f?rlkey=k&dl=0", "label": "Brochure",
         "heading": "Babylon"},
        {"href": "https://www.e-agent.com.au/contact", "label": "Contact us", "heading": ""},
    ]

    class _Page:
        def evaluate(self, js):
            return links

    scraper = type("S", (), {"page": _Page()})()
    src = EA.EAgentSource.__new__(EA.EAgentSource)
    kept = src._stocklist_links(scraper)
    assert [l["heading"] for l in kept] == ["Babylon"]
    aside = {l["heading"]: why for l, why in src._set_aside}
    assert aside == {"Regatta": "skipped by label filter", "Babylon": "not a price list"}, \
        "navigation links are not stock and must not be listed"


def test_a_portal_gets_one_manifest_line_with_its_card_counts(monkeypatch):
    from sources import builder_portals as BP

    def pages(self, builder, filters, cfg):
        self._page_stats = {"found": 263, "read": 263}
        return [{"lot_address": "LOT 1 X ST"}, {"lot_address": "LOT 2 X ST"}]

    monkeypatch.setattr(BP.BuilderPortalSource, "_scrape_builder_pages", pages)
    src = BP.BuilderPortalSource.__new__(BP.BuilderPortalSource)
    builder = {"builder_name": "Hermitage Homes",
               "portal_url": "https://portal.hermitagehomes.com.au/"}
    rows = src._scrape_builder(builder, {})
    rows += src._scrape_builder(builder, {})          # licensed in a second state
    assert len(HM.ENTRIES) == 1, "one builder, one line"
    e = HM.ENTRIES[0]
    assert (e["kind"], e["outcome"], e["printed"], e["cards_found"], e["cards_read"]) == \
        ("portal", "read", 2, 263, 263)
    assert e["host"] == "portal.hermitagehomes.com.au"
    assert all(r["_manifest_id"] == e["id"] for r in rows)


def test_the_research_bundle_still_imports_the_harvest_sources():
    """build_web ships sources/e_agent.py and sources/builder_portals.py inside the research
    function (kommo_agent imports both) but not harvest_manifest.py. A hard import there
    would fail the bundle's import probe and the whole nightly publish with it."""
    import subprocess
    import sys
    import textwrap
    from pathlib import Path
    code = textwrap.dedent("""
        import importlib.abc, sys
        class _NotInTheBundle(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path, target=None):
                if name == "harvest_manifest":
                    raise ImportError("harvest_manifest is not in the function bundle")
        sys.meta_path.insert(0, _NotInTheBundle())
        import sources.e_agent, sources.builder_portals, kommo_agent
        print("imports-ok")
    """)
    app = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, "-c", code], cwd=str(app), capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0 and "imports-ok" in r.stdout, r.stderr[-1200:]
