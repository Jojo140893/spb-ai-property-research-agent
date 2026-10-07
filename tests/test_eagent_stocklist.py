"""The EAgent Stocklist reader (sources/eagent_stocklist.py), against a fake site.

WHY THIS EXISTS (7 Oct 2026). Coleen phoned: E-Agent always shows more than the app. E-Agent
moved its pooled house-and-land lists to a separate site, eagent.mekooflow.com ("EAgent
Stocklist"), on 24 Sep, and nothing read it. Nobody has seen it signed in yet -- the login
comes from E-Agent's BDM -- so the reader DISCOVERS the site's JSON API and maps fields by
key name. These tests pin that behaviour against a local Express-like fake on 127.0.0.1:
the sign-in (accepted and refused), discovery from the page's scripts, every paging style,
the export path, the key-name mapping, the plausibility gate, the capture (which must hold
nothing secret) and the harvest's skip when no login is stored. No real network.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import secrets_store
from sources import eagent_stocklist as es

COMPANY = "Smart Property Buying Pty Ltd"
PASSWORD = "pw-Correct-Horse-91"
COOKIE = "sid=s%3AzZ9yX8wV7uT6"

_SUBURBS = (("Wollert", "VIC", "3750", "Aurora"), ("Tarneit", "VIC", "3029", "Riverdale"),
            ("Box Hill", "NSW", "2765", "The Gables"), ("Ripley", "QLD", "4306", "Ecco"),
            ("Clyde North", "VIC", "3978", "Smiths Lane"))
_BUILDERS = ("Hudson Homes", "Creation Homes", "AVIA Homes", "Hallmark Homes")


def make_records(n, style="camel"):
    """n realistic packages, keyed the way an Express/Mongo API might key them."""
    out = []
    for i in range(n):
        suburb, state, pc, estate = _SUBURBS[i % len(_SUBURBS)]
        builder = _BUILDERS[i % len(_BUILDERS)]
        land, build = 300_000 + 1_000 * i, 350_000 + 500 * i
        lot = str(100 + i)
        if style == "camel":
            out.append({"_id": "65f%05d" % i, "lotNumber": lot, "estateName": estate,
                        "suburb": suburb, "state": state, "postcode": pc,
                        "builderName": builder, "designName": "Ascot %d" % (20 + i % 9),
                        "bedrooms": 4, "bathrooms": 2, "carSpaces": 2,
                        "landSize": 448 + i, "houseSize": 210.5, "frontage": 12.5,
                        "landPrice": land, "buildPrice": build,
                        "packagePrice": land + build, "status": "Available",
                        "titled": "Q2 2027", "agentEmail": "bdm.jane@eagent.example",
                        "updatedAt": "2026-10-06T10:00:00Z"})
        elif style == "snake":
            out.append({"id": i + 1, "lot_no": "Lot %s" % lot, "project": estate,
                        "locality": suburb.upper(), "state_code": state.lower(),
                        "post_code": pc, "builder": builder,
                        "house_design": "Ascot %d" % (20 + i % 9), "beds": "4",
                        "baths": "2", "garage": "2", "land_area": "%dm2" % (448 + i),
                        "total_price": "$%s" % format(land + build, ","),
                        "availability": "Avallable" if i == 0 else "available"})
        elif style == "nested":
            out.append({"id": "pkg-%d" % i, "lot": {"number": lot, "size": 448 + i},
                        "estate": {"name": estate, "suburb": suburb, "state": state,
                                   "postcode": pc},
                        "builder": {"name": builder, "phone": "0412 345 678"},
                        "house": {"name": "Ascot 25", "size": 210},
                        "price": {"land": land, "build": build, "total": land + build},
                        "specs": {"bed": 4, "bath": 2, "car": 2},
                        "status": "AVAILABLE"})
    return out


class FakeSite:
    """An Express-like EAgent Stocklist: session cookie, login form, JSON API, paging."""

    def __init__(self, records, paging="page1", page_size=25, js=None, home_extra="",
                 export_csv=None, api_path="/api/v2/lots", ignore_cap=False):
        self.records = records
        self.paging = paging
        self.page_size = page_size
        self.api_path = api_path
        self.js = js if js is not None else (
            'import("./chunk-a1.js");\nconst API = "%s";\n'
            'fetch(API + "?page=" + p).then(r => r.json());\n'
            'fetch("/api/auth/me");\n' % api_path)
        self.chunk = 'axios.get(`/api/estates/${id}`); axios.post("/api/logout");'
        self.home_extra = home_extra
        self.export_csv = export_csv
        self.ignore_cap = ignore_cap
        self.requests = []
        self.posts = []
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="text/html", headers=None):
                data = body if isinstance(body, bytes) else body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _redirect(self, where, headers=None):
                self.send_response(302)
                self.send_header("Location", where)
                self.send_header("Content-Length", "0")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()

            def _authed(self):
                return COOKIE in (self.headers.get("Cookie") or "")

            def do_GET(self):
                u = urlparse(self.path)
                site.requests.append(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if u.path == "/login":
                    return self._send(200, site.login_page())
                if u.path.startswith("/api/"):
                    if not self._authed():
                        return self._send(401, '{"ok":false,"error":"Authentication required"}',
                                          "application/json")
                    if u.path == site.api_path:
                        return self._send(200, json.dumps(site.page(q, u.path)),
                                          "application/json")
                    return self._send(404, '{"ok":false,"error":"Not found"}',
                                      "application/json")
                if not self._authed():
                    return self._redirect("/login")
                if u.path == "/":
                    return self._send(200, site.home_page())
                if u.path == "/assets/app.js":
                    return self._send(200, site.js, "application/javascript")
                if u.path == "/assets/chunk-a1.js":
                    return self._send(200, site.chunk, "application/javascript")
                if u.path == "/export/stock.csv" and site.export_csv is not None:
                    return self._send(200, site.export_csv, "text/csv")
                return self._send(404, "<html>not found</html>")

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
                site.posts.append(sorted(form))          # field NAMES only
                if (self.path == "/login" and form.get("company") == COMPANY
                        and form.get("password") == PASSWORD and form.get("_csrf") == "tok123"):
                    return self._redirect("/", {"Set-Cookie": COOKIE + "; Path=/; HttpOnly"})
                return self._redirect("/login?error=1")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def login_page(self):
        return ("<html><head><title>EAgent Stocklist</title></head><body>"
                "<form method='post' action='/login'>"
                "<input type='hidden' name='_csrf' value='tok123'>"
                "<input type='text' name='company' placeholder='Company'>"
                "<input type='password' name='password'>"
                "<button type='submit'>Sign in</button></form></body></html>")

    def home_page(self):
        return ("<html><head><title>EAgent Stocklist - Search</title>"
                "<meta name='csrf-token' content='tok-secret-777'>"
                "<script src='/assets/app.js'></script></head><body>"
                "<p>Signed in as %s</p>"
                "<form><input name='q' value='Wollert'></form>%s</body></html>"
                % (COMPANY, self.home_extra))

    def page(self, q, path):
        recs, size = self.records, self.page_size
        if self.paging == "all":
            return {"ok": True, "data": recs}
        if self.paging == "page1":       # pages count from 1, total stated
            p = int(q.get("page", 1))
            chunk = recs[(p - 1) * size: p * size]
            return {"ok": True, "data": chunk, "total": len(recs), "page": p, "limit": size}
        if self.paging == "page0":       # pages count from 0, no total
            p = int(q.get("page", 0))
            return {"items": recs[p * size:(p + 1) * size]}
        if self.paging == "offset":      # page= ignored; offset/limit work
            o = int(q.get("offset", 0))
            return {"results": recs[o:o + size], "count": len(recs)}
        if self.paging == "next":        # a next link
            p = int(q.get("p", 1))
            nxt = "%s?p=%d" % (path, p + 1) if p * size < len(recs) else None
            return {"packages": recs[(p - 1) * size: p * size],
                    "links": {"next": nxt}}
        if self.paging == "cursor":
            start = int(q.get("cursor", "0") or 0)
            end = start + size
            return {"rows": recs[start:end],
                    "meta": {"nextCursor": str(end) if end < len(recs) else None}}
        if self.paging == "endless":     # never ends, new ids every page
            p = int(q.get("page", 1))
            base = make_records(size)
            for i, r in enumerate(base):
                r["_id"] = "endless-%d-%d" % (p, i)
            return {"data": base, "total": 10 ** 6}
        raise AssertionError(self.paging)


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setattr(secrets_store, "_from_vault", lambda portal, field: None)
    monkeypatch.setenv("EAGENT_STOCKLIST_USERNAME", COMPANY)
    monkeypatch.setenv("EAGENT_STOCKLIST_PASSWORD", PASSWORD)


@pytest.fixture
def no_creds(monkeypatch):
    monkeypatch.setattr(secrets_store, "_from_vault", lambda portal, field: None)
    monkeypatch.delenv("EAGENT_STOCKLIST_USERNAME", raising=False)
    monkeypatch.delenv("EAGENT_STOCKLIST_PASSWORD", raising=False)


@pytest.fixture
def site_factory():
    made = []

    def make(*a, **k):
        s = FakeSite(*a, **k)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


def _source(site, tmp_path, **kw):
    return es.EAgentStocklistSource(base_url=site.base, capture_dir=tmp_path / "capture",
                                    status_path=tmp_path / "status.json", throttle=0, **kw)


# ------------------------------------------------------------------- credentials

def test_no_credentials_skips_without_touching_the_network(no_creds, tmp_path):
    class Boom:
        def get(self, *a, **k):
            raise AssertionError("no request may be made without a login")
        post = get

    src = es.EAgentStocklistSource(base_url="http://127.0.0.1:9", session=Boom(),
                                   capture_dir=tmp_path / "c", status_path=tmp_path / "s.json")
    assert not src.configured
    assert src.search({}) == []
    assert src.state == "skipped" and es.MISSING_TEXT in src.why
    assert json.loads((tmp_path / "s.json").read_text())["state"] == "skipped"


def test_credentials_come_from_the_documented_env_names(creds):
    u, p, where = secrets_store.get_credentials(es.PORTAL_KEY)
    assert (u, p, where) == (COMPANY, PASSWORD, "env")


# ------------------------------------------------------------------- sign-in

def test_login_success_then_discovery_from_the_scripts(creds, site_factory, tmp_path):
    site = site_factory(make_records(60), paging="page1", page_size=25)
    src = _source(site, tmp_path)
    rows = src.search({})
    assert src.signed_in and src.state == "ok", src.why
    # The list endpoint is NOT one of the defaults: it was read out of /assets/app.js, and
    # it was the first thing asked -- the site's own code outranks a guessed name.
    assert src.endpoint == "/api/v2/lots"
    assert [r for r in site.requests if r.startswith("/api/")][0].startswith("/api/v2/lots")
    assert len(rows) == 60
    # The hidden field went with the form, under the form's own names, and nothing else.
    assert site.posts == [["_csrf", "company", "password"]]
    # The auth and logout literals in the bundle were never called.
    assert not any("/api/auth" in r or "/api/logout" in r for r in site.requests)
    assert all(r["source_channel"] == "E-Agent Stocklist" for r in rows)


def test_a_refused_login_reads_nothing_and_says_so(no_creds, monkeypatch, site_factory,
                                                    tmp_path):
    monkeypatch.setenv("EAGENT_STOCKLIST_USERNAME", COMPANY)
    monkeypatch.setenv("EAGENT_STOCKLIST_PASSWORD", "wrong-password")
    site = site_factory(make_records(60))
    src = _source(site, tmp_path)
    assert src.search({}) == []
    assert src.state == "refused" and not src.signed_in
    assert not any(r.startswith("/api/") for r in site.requests)
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["state"] == "refused"
    row = es.login_row(status, configured=True)
    assert row["state"] == "expired" and "BDM" in row["blocked_by"]


# ------------------------------------------------------------------- paging

@pytest.mark.parametrize("paging", ["page1", "page0", "offset", "next", "cursor", "all"])
def test_every_paging_style_is_read_to_the_end(creds, site_factory, tmp_path, paging):
    recs = make_records(83)
    site = site_factory(recs, paging=paging, page_size=20)
    src = _source(site, tmp_path)
    rows = src.search({})
    assert src.state == "ok", src.why
    assert len(rows) == 83, (paging, src.capture.discovery.get("paging"))
    assert len({r["lot_number"] for r in rows}) == 83
    assert not src.truncated


def test_an_api_that_never_ends_stops_at_the_cap_and_says_it_is_truncated(
        creds, monkeypatch, site_factory, tmp_path):
    monkeypatch.setattr(es, "MAX_PAGES", 6)
    site = site_factory([], paging="endless", page_size=30)
    src = _source(site, tmp_path)
    rows = src.search({})
    assert src.truncated
    assert len(site.requests) < 30
    # Six requests to the list: the first answer, page=1 (which repeats it, so pages count
    # from 1) and pages 2-5. Five pages of 30 new records each, then the cap.
    assert len(rows) == 5 * 30
    assert "READ CAP" in src.why


# ------------------------------------------------------------------- export

def test_an_offered_export_file_is_preferred_and_read_by_its_header(creds, site_factory,
                                                                     tmp_path):
    lines = ["Lot,Estate,Suburb,State,Postcode,Builder,Design,Beds,Baths,Car,"
             "Land Size,Package Price,Status"]
    for i in range(40):
        suburb, state, pc, estate = _SUBURBS[i % len(_SUBURBS)]
        lines.append('%d,%s,%s,%s,%s,Hudson Homes,Ascot 25,4,2,2,%d,"$%s",Available'
                     % (200 + i, estate, suburb, state, pc, 400 + i,
                        format(600_000 + 1_000 * i, ",")))
    site = site_factory(make_records(60), export_csv="\n".join(lines),
                        home_extra="<a href='/export/stock.csv'>Export CSV</a>")
    src = _source(site, tmp_path)
    rows = src.search({})
    assert src.state == "ok", src.why
    assert src.endpoint == "/export/stock.csv"
    assert len(rows) == 40
    assert not any(r.startswith("/api/v2/lots") for r in site.requests)
    r0 = next(r for r in rows if r["lot_number"] == "200")
    assert (r0["suburb"], r0["state"], r0["postcode"]) == ("Wollert", "VIC", "3750")
    assert r0["advertised_package_price"] == 600_000
    assert r0["builder_name"] == "Hudson Homes" and r0["availability_status"] == "Available"


# ------------------------------------------------------------------- mapping

def test_three_key_styles_map_to_the_same_fields():
    base = "https://eagent.mekooflow.com"
    camel, _ = es.map_record(make_records(1, "camel")[0], base, base + "/api/packages")
    snake, _ = es.map_record(make_records(1, "snake")[0], base, base + "/api/packages")
    nested, _ = es.map_record(make_records(1, "nested")[0], base, base + "/api/packages")
    for row in (camel, snake, nested):
        assert row["lot_number"] == "100"
        assert row["suburb"] == "Wollert" and row["state"] == "VIC"
        assert row["postcode"] == "3750"
        assert row["estate_name"] == "Aurora"
        assert row["builder_name"] == "Hudson Homes"
        assert row["advertised_package_price"] == 650_000
        assert (row["bedrooms"], row["bathrooms"], row["car_spaces"]) == (4, 2, 2)
        assert row["land_size_sqm"] == 448
        assert row["availability_status"] == "Available"   # incl. the 'Avallable' typo
        assert row["source_url_or_ref"] == base + "/api/packages"
        assert row["attribution_scope"] == "builder"
    assert camel["land_price"] == 300_000 and camel["build_price"] == 350_000
    assert nested["land_price"] == 300_000 and nested["build_price"] == 350_000
    assert camel["house_design"] == "Ascot 20" and camel["title_status"] == "Q2 2027"


def test_a_nested_land_price_is_never_read_as_the_package_price():
    rec = {"id": 1, "lot": "7", "estate": "Aurora", "suburb": "Wollert", "state": "VIC",
           "land": {"price": 300_000, "size": 400}, "house": {"price": 350_000}}
    row, _ = es.map_record(rec, "https://x", "https://x/api/p")
    assert row["advertised_package_price"] == 650_000
    assert row["land_price"] == 300_000 and row["build_price"] == 350_000


@pytest.mark.parametrize("raw,want", [
    ("Available", "Available"), ("Avallable", "Available"), ("AVAILBLE", "Available"),
    ("Sold", "Sold"), ("DEPOSIT", "Not Available"), ("Contract Issued", "Not Available"),
    ("EXCHANGED", "Not Available"), ("On Hold", "On Hold"),
    # Coleen's call (7 Oct 2026): these read as no stated availability, as everywhere else.
    ("EOI", None), ("RESALE", None), ("Exclusive", None)])
def test_status_words(raw, want):
    assert es.normalise_status(raw) == want


def test_the_line_prints_its_price_and_keeps_its_postcode_last():
    """The search refuses a price-list line that does not print its own price, and the
    nightly repair clears a postcode the line cannot reproduce. Both read source_text."""
    from price_kind import price_evidence
    from sources.feature_extract import parse_postcode
    rec = make_records(1, "camel")[0]
    rec["designName"] = "Hamilton 2400"          # a design with four digits in it
    row, _ = es.map_record(rec, "https://x", "https://x/api/p")
    ev = price_evidence({**row, "price": row["advertised_package_price"]})
    assert ev["price_in_text"] is True
    assert ev["text_price_max"] == 650_000
    text = " ".join(str(row[k] or "") for k in ("lot_address", "suburb", "source_text"))
    assert parse_postcode(text) == "3750"


def test_identity_is_stable_when_only_price_and_status_move():
    from database import building_content_hash
    a = make_records(1, "camel")[0]
    b = dict(a, packagePrice=699_000, landPrice=320_000, status="Sold")
    ra, _ = es.map_record(a, "https://x", "https://x/api/p")
    rb, _ = es.map_record(b, "https://x", "https://x/api/p")
    assert building_content_hash(ra) == building_content_hash(rb)
    c, _ = es.map_record(dict(a, lotNumber="101"), "https://x", "https://x/api/p")
    assert building_content_hash(c) != building_content_hash(ra)


def test_links_behind_the_stocklist_login_are_not_stored_as_lot_pages():
    rec = dict(make_records(1)[0], url="/packages/65f00000",
               brochureUrl="https://cdn.example.com/b/aurora-100.pdf")
    row, _ = es.map_record(rec, "https://eagent.mekooflow.com",
                           "https://eagent.mekooflow.com/api/packages")
    assert row["listing_url"] is None
    assert row["brochure_url"] == "https://cdn.example.com/b/aurora-100.pdf"


# ------------------------------------------------------------------- the gate

def test_the_gate_stores_nothing_from_a_read_that_does_not_look_like_stock(
        creds, site_factory, tmp_path):
    junk = [{"id": i, "name": "Filter option %d" % i, "price": 95_000 + i, "state": "Active"}
            for i in range(50)]
    site = site_factory(junk, paging="all")
    src = _source(site, tmp_path)
    assert src.search({}) == []
    assert src.state == "gate_refused"
    mapping = json.loads((tmp_path / "capture" / "mapping.json").read_text())
    assert mapping["gate"]["store"] is False
    assert "README.txt" in {p.name for p in (tmp_path / "capture").iterdir()}


def test_the_gate_refuses_too_few_rows_and_non_australian_states():
    base = "https://x"
    rows = [es.map_record(r, base, base)[0] for r in make_records(10)]
    ok, why, _ = es.plausibility(rows, 10)
    assert not ok and "at least 20" in why
    rows = [es.map_record(dict(r, state="Dubai"), base, base)[0] for r in make_records(30)]
    ok, why, _ = es.plausibility(rows, 30)
    assert not ok and "not Australian" in why
    rows = [es.map_record(r, base, base)[0] for r in make_records(30)]
    assert es.plausibility(rows, 30)[0] is True
    # Thirty Australian rows, but every "price" is a deposit or a weekly figure.
    rows = [es.map_record(dict(r, packagePrice=95_000, landPrice=None, buildPrice=None),
                          base, base)[0] for r in make_records(30)]
    ok, why, stats = es.plausibility(rows, 30)
    assert not ok and "mapping is probably wrong" in why
    assert stats["priced_and_placed"] == 0


# ------------------------------------------------------------------- the capture

def test_the_capture_holds_nothing_secret(creds, site_factory, tmp_path):
    site = site_factory(make_records(60), paging="page1")
    src = _source(site, tmp_path)
    assert src.search({})
    folder = tmp_path / "capture"
    names = {p.name for p in folder.iterdir()}
    assert {"README.txt", "discovery.json", "mapping.json", "home.html", "login.html",
            "payload_1.json"} <= names
    assert len([n for n in names if n.startswith("payload_")]) <= 3
    blob = "\n".join(p.read_text(encoding="utf-8") for p in folder.iterdir())
    for secret in (PASSWORD, COMPANY, "zZ9yX8wV7uT6", "tok-secret-777", "tok123",
                   "bdm.jane@eagent.example", "Set-Cookie", "Cookie:"):
        assert secret not in blob, secret
    payload = json.loads((folder / "payload_1.json").read_text())
    data = payload["body"]["data"]
    assert len(data) <= es.CAPTURE_RECORDS + 1
    mapping = json.loads((folder / "mapping.json").read_text())
    assert mapping["fields"]["price"]["key"] == "packageprice"
    assert mapping["fields"]["suburb"]["key"] == "suburb"
    disc = json.loads((folder / "discovery.json").read_text())
    assert "/api/v2/lots" in disc["api_paths_found"]
    assert disc["paging"]["records"] == 60


def test_a_capture_from_an_earlier_night_is_not_left_behind(creds, site_factory, tmp_path):
    folder = tmp_path / "capture"
    folder.mkdir()
    (folder / "payload_3.json").write_text("{\"from\": \"last week\"}")
    site = site_factory(make_records(30), paging="all")
    _source(site, tmp_path).search({})
    assert "last week" not in "".join(p.read_text() for p in folder.iterdir())


# ------------------------------------------------------------------- the Settings tab

def test_the_login_row_says_where_the_login_comes_from(no_creds):
    row = es.login_row(None, configured=False)
    assert row["state"] == "missing"
    assert row["blocked_by"] == es.MISSING_TEXT
    assert "Needs the EAgent Stocklist company login from your E-Agent BDM" == es.MISSING_TEXT
    assert row["channel"] == "E-Agent Stocklist" and row["self_service"] is False
    from datetime import datetime
    now = datetime.now()
    ok = es.login_row({"state": "ok", "signed_in": True,
                       "checked_at": now.isoformat(timespec="seconds")}, configured=True)
    assert ok["state"] == "ok"
    old = es.login_row({"state": "ok", "signed_in": True,
                        "checked_at": "2026-01-01T09:00:00"}, configured=True, now=now)
    assert old["state"] == "unknown"


def test_portal_login_check_lists_the_stocklist_login(no_creds, monkeypatch, tmp_path):
    import portal_login
    monkeypatch.setattr(portal_login, "_targets", lambda: [])
    monkeypatch.setattr(es, "STATUS_PATH", tmp_path / "none.json")
    rows = portal_login.check_logins(write=False)
    row = next(r for r in rows if r.get("channel") == "E-Agent Stocklist")
    assert row["state"] == "missing" and row["blocked_by"] == es.MISSING_TEXT


def test_setup_credentials_offers_the_stocklist_key():
    import setup_credentials
    assert "eagent_stocklist" in dict(setup_credentials._API_KEYS)


# ------------------------------------------------------------------- the harvest

def test_the_harvest_skips_the_stocklist_without_a_login(no_creds, monkeypatch, tmp_path,
                                                         capsys):
    import config
    import harvest_buildings as hb
    from database import ResearchDatabase
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "h.db")
    monkeypatch.setattr(hb, "ResearchDatabase",
                        lambda *a, **k: ResearchDatabase(db_path=tmp_path / "h.db"))
    monkeypatch.setattr(es, "STATUS_PATH", tmp_path / "status.json")
    code = hb.harvest(eagent=False, portals=False, email=False, proxima=False, rea=False,
                      eagent_stocklist=True)
    out = capsys.readouterr().out
    assert code == 0
    assert "E-Agent Stocklist: SKIPPED" in out and "BDM" in out
    assert ResearchDatabase(db_path=tmp_path / "h.db").get_buildings() == []


def test_the_harvest_stores_stocklist_rows_under_their_own_channel(monkeypatch, tmp_path):
    import config
    import harvest_buildings as hb
    from database import ResearchDatabase
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "h.db")
    monkeypatch.setattr(hb, "ResearchDatabase",
                        lambda *a, **k: ResearchDatabase(db_path=tmp_path / "h.db"))
    rows = [es.map_record(r, "https://x", "https://x/api/p")[0] for r in make_records(25)]
    for r in rows:
        r.pop("_state_stated", None)

    class Fake:
        channel_name = es.CHANNEL
        configured = True
        signed_in = True
        state, why, truncated = "ok", "25 of 25 rows priced and placed", False

        def __init__(self, *a, **k):
            pass

        def search(self, filters):
            return [dict(r) for r in rows]

    monkeypatch.setattr(hb, "EAgentStocklistSource", Fake)
    assert hb.harvest(eagent=False, portals=False, email=False, proxima=False, rea=False,
                      eagent_stocklist=True) == 0
    db = ResearchDatabase(db_path=tmp_path / "h.db")
    counts = {c["source_channel"]: c["n"] for c in db.building_counts_by_channel()}
    assert counts == {"E-Agent Stocklist": 25}
    # A second night updates in place.
    hb.harvest(eagent=False, portals=False, email=False, proxima=False, rea=False,
               eagent_stocklist=True)
    assert len(db.get_buildings()) == 25


def test_a_stocklist_lot_a_constraint_refuses_is_named_and_counted(monkeypatch, tmp_path,
                                                                    capsys):
    """7 Oct 2026, integration of the Stocklist and harvest lanes. record_building now says
    "rejected" when a constraint refuses a lot, and every other channel's block counts it
    -- the summary names the channel, and data/harvest_manifest.json records what each
    source printed and stored, which the parity check's READ bar reads. The Stocklist block
    was written before that and counted neither, so a refused Stocklist lot vanished with no
    line anywhere: the silent loss the dedup_key trap fix was made to end."""
    import config
    import harvest_buildings as hb
    import harvest_manifest
    from database import ResearchDatabase
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "h.db")
    monkeypatch.setattr(hb, "ResearchDatabase",
                        lambda *a, **k: ResearchDatabase(db_path=tmp_path / "h.db"))
    rows = [es.map_record(r, "https://x", "https://x/api/p")[0] for r in make_records(25)]
    for r in rows:
        r.pop("_state_stated", None)
    refused = {rows[i]["lot_address"] for i in (2, 7, 11)}
    real = ResearchDatabase.record_building

    def record(self, listing):
        if listing.get("lot_address") in refused:
            return "rejected"
        return real(self, listing)

    monkeypatch.setattr(ResearchDatabase, "record_building", record)

    class Fake:
        channel_name = es.CHANNEL
        configured = True
        signed_in = True
        state, why, truncated = "ok", "25 of 25 rows priced and placed", False

        def __init__(self, *a, **k):
            pass

        def search(self, filters):
            return [dict(r) for r in rows]

    monkeypatch.setattr(hb, "EAgentStocklistSource", Fake)
    hb.harvest(eagent=False, portals=False, email=False, proxima=False, rea=False,
               eagent_stocklist=True)
    out = capsys.readouterr().out
    assert "REJECTED" in out and "E-Agent Stocklist 3" in out
    mine = [e for e in harvest_manifest.ENTRIES if e["channel"] == es.CHANNEL]
    assert len(mine) == 1, harvest_manifest.ENTRIES
    assert mine[0]["outcome"] == "read" and mine[0]["printed"] == 25
    assert mine[0]["stored"]["rejected"] == 3 and mine[0]["stored"]["new"] == 22
    assert "http" not in json.dumps(mine[0])


def test_a_stocklist_night_that_reads_nothing_is_named_in_the_manifest(monkeypatch,
                                                                       tmp_path):
    """A stored login that is refused reads 0 lots. The manifest says so in its own words, so
    the READ bar can name a source that yielded last week and nothing tonight."""
    import config
    import harvest_buildings as hb
    import harvest_manifest
    from database import ResearchDatabase
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "h.db")
    monkeypatch.setattr(hb, "ResearchDatabase",
                        lambda *a, **k: ResearchDatabase(db_path=tmp_path / "h.db"))

    class Refused:
        channel_name = es.CHANNEL
        configured = True
        signed_in = False
        state, why, truncated = "refused", "the site did not accept the stored login", False

        def __init__(self, *a, **k):
            pass

        def search(self, filters):
            return []

    monkeypatch.setattr(hb, "EAgentStocklistSource", Refused)
    hb.harvest(eagent=False, portals=False, email=False, proxima=False, rea=False,
               eagent_stocklist=True)
    mine = [e for e in harvest_manifest.ENTRIES if e["channel"] == es.CHANNEL]
    assert [(e["outcome"], e["printed"]) for e in mine] == [("login failed", 0)]
