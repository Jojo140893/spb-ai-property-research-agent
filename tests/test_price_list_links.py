"""The unreadable price lists on the Settings tab open, without publishing their links.

Ahsan, 24 Oct 2026: every entry in "Price lists we could not read" should be a link he can
open to check the price list himself. They were names only, on purpose (build_web, "THE
NAME, NOT THE LINK"): 56 of 57 were Dropbox "/scl/fo/" folder shares, capability URLs that
hand the whole shared folder to anyone holding the string, and channels.json is a static
file. So:

    harvest   note_unreadable(..., link=) records the file's link, or its folder's;
    build     channels.json gets an opaque id per entry, never the link; the id -> link
              map goes into the function bundle (api/_data/price_list_links.json);
    route     GET /api/price_list?id= -> 302 to the link, behind the sign-in.

What must hold, and is checked here:
  * no '/scl/fo/' string reaches any published static file, and the link still works;
  * a link that is not http(s) never renders, at any of the three layers;
  * an entry without a recorded link still shows its name.
The page side runs under Node: tests/price_list_panel_checks.mjs.
"""
import http.client
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "api"))

import build_web  # noqa: E402
import price_list  # noqa: E402
from sources import spreadsheet_extract as SE  # noqa: E402

PANEL_CHECKS = Path(__file__).resolve().parent / "price_list_panel_checks.mjs"

FILE_LINK = ("https://www.dropbox.com/scl/fo/aaa111zz/bbb222yy/92%20New%20Street%2C%20South"
             "%20Kingsville%20-%20Pricelist.pdf?rlkey=SECRETKEYONE&dl=1")
FOLDER_LINK = "https://www.dropbox.com/scl/fo/ccc333xx/ddd444ww?rlkey=SECRETKEYTWO&dl=0"
LEGACY_LINK = ("https://www.dropbox.com/scl/fo/eee555vv/fff666uu/Atlas%20Price.pdf"
               "?rlkey=SECRETKEYTHREE&dl=1")
SECRETS = ("/scl/fo/", "rlkey", "SECRETKEY", "aaa111zz", "ccc333xx", "eee555vv")

# One of each kind the harvest writes, plus the hostile ones it must never pass on.
ENTRIES = [
    # 0: a downloaded file that read as nothing: the file's own link.
    {"kind": "pdf", "file": FILE_LINK, "why": "no listings could be read from it",
     "link": FILE_LINK},
    # 1: a folder with no price list in it: the folder's link.
    {"kind": "remote", "file": "Victoria (VIC Townhouse) - no price list in the folder.",
     "why": "no price list in this folder (3 other document(s) tried, none is one)",
     "link": FOLDER_LINK},
    # 2: an email attachment: no link was recorded.
    {"kind": "pdf", "file": "email:Koops Price.pdf", "why": "no listings could be read from it"},
    # 3: a list written before 24 Oct 2026: no `link`, but `file` is the link.
    {"kind": "pdf", "file": LEGACY_LINK, "why": "no listings could be read from it"},
    # 4..9: links that are not http(s), or not safe in a header.
    {"kind": "remote", "file": "Evil One - could not be downloaded.", "why": "x",
     "link": "javascript:alert(document.cookie)"},
    {"kind": "remote", "file": "Evil Two - could not be downloaded.", "why": "x",
     "link": "JAVASCRIPT:alert(1)"},
    {"kind": "remote", "file": "Evil Three - could not be downloaded.", "why": "x",
     "link": "data:text/html,<script>alert(1)</script>"},
    {"kind": "remote", "file": "Evil Four - could not be downloaded.", "why": "x",
     "link": "file:///C:/Users/coleen/price.pdf"},
    {"kind": "remote", "file": "Evil Five - could not be downloaded.", "why": "x",
     "link": "//evil.example/price.pdf"},
    {"kind": "remote", "file": "Evil Six - could not be downloaded.", "why": "x",
     "link": "https://example.com/a\r\nSet-Cookie: spb_session=x"},
]
OPENS = {0, 1, 3}


def _write_list(root):
    """The fixture list where build_web looks for it, beside a build_web.py at `root`."""
    path = root / "data" / "unreadable_stocklists.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generated": "24/10/2026 09:40", "files": ENTRIES}),
                    encoding="utf-8")
    return path


@pytest.fixture
def unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(build_web, "__file__", str(tmp_path / "build_web.py"))
    return _write_list(tmp_path)


# ------------------------------------------------------------------ the harvest records it

def test_a_file_that_reads_as_nothing_records_its_own_link(monkeypatch):
    monkeypatch.setattr(SE, "UNREADABLE", [])
    SE._log_yield("pdf", FILE_LINK, [])
    assert SE.UNREADABLE == [{"kind": "pdf", "file": FILE_LINK,
                              "why": "no listings could be read from it", "link": FILE_LINK}]


def test_an_email_attachment_records_no_link_and_keeps_its_name(monkeypatch):
    monkeypatch.setattr(SE, "UNREADABLE", [])
    SE._log_yield("pdf", "email:Koops Price.pdf", [])
    assert SE.UNREADABLE == [{"kind": "pdf", "file": "email:Koops Price.pdf",
                              "why": "no listings could be read from it"}]


@pytest.mark.parametrize("link", [e["link"] for e in ENTRIES[4:]] + ["Koops Price.pdf", ""])
def test_the_harvest_records_no_link_that_is_not_http(monkeypatch, link):
    monkeypatch.setattr(SE, "UNREADABLE", [])
    SE.note_unreadable("remote", "Some Estate - could not be downloaded.", "why", link=link)
    assert "link" not in SE.UNREADABLE[0], SE.UNREADABLE[0]


class _Scraper:
    def throttle(self):
        pass


def _eagent(monkeypatch, pieces):
    import harvest_manifest as HM
    from sources import e_agent as EA
    monkeypatch.setattr(SE, "UNREADABLE", [])
    HM.reset()
    monkeypatch.setattr(EA.remote, "fetch_stocklist", lambda *a, **k: pieces)
    src = EA.EAgentSource.__new__(EA.EAgentSource)
    src._http = None
    link = {"href": FOLDER_LINK, "label": "Access Portal", "heading": "Fraser Rise Hattan"}
    rows = src._parse_stocklist(_Scraper(), link, "", "project", "VIC", "Townhouse",
                                page="townhouses-vic")
    return rows, HM


def test_eagent_records_the_file_where_it_knows_one(monkeypatch):
    """A file in the folder that would not download: that file's link, not the folder's."""
    import sources.remote_stocklist as RS
    piece = RS.Fetched(label="Access Portal / Price List.pdf", url=FILE_LINK,
                       problem="download failed: timed out")
    rows, HM = _eagent(monkeypatch, [piece])
    assert rows == [] and len(SE.UNREADABLE) == 1
    assert SE.UNREADABLE[0]["link"] == FILE_LINK
    assert "dropbox.com" not in SE.UNREADABLE[0]["file"]
    assert "dropbox.com" not in json.dumps(HM.ENTRIES).lower(), "the manifest got the link"


def test_eagent_records_the_folder_when_it_has_no_price_list(monkeypatch):
    """The "no price list in the folder" entries open the folder, so a person can look."""
    import sources.remote_stocklist as RS
    piece = RS.Fetched(label="Access Portal", url=FOLDER_LINK, problem=(
        "no price list in this folder \u2014 it holds only a.pdf. Nothing to harvest"))
    _rows, _HM = _eagent(monkeypatch, [piece])
    assert SE.UNREADABLE[0]["link"] == FOLDER_LINK
    assert SE.UNREADABLE[0]["file"].endswith("no price list in the folder.")


def test_eagent_records_the_folder_when_every_guess_fails(monkeypatch):
    """No file named as a price list, and none of the documents tried was one."""
    import sources.remote_stocklist as RS
    guess = RS.Fetched(label="Access Portal / Brochure.pdf", url=FILE_LINK,
                       problem="no price list: not one by its name, and too big",
                       meta={"guessed": True})
    _rows, _HM = _eagent(monkeypatch, [guess])
    assert len(SE.UNREADABLE) == 1, SE.UNREADABLE
    assert SE.UNREADABLE[0]["link"] == FOLDER_LINK


def test_the_harvest_writes_the_link_down(monkeypatch, tmp_path):
    """harvest_buildings carries the list from the harvest to the build, link and all."""
    import harvest_buildings as HB
    monkeypatch.setattr(HB, "__file__", str(tmp_path / "harvest_buildings.py"))
    monkeypatch.setattr(SE, "UNREADABLE", [])
    SE._log_yield("pdf", FILE_LINK, [])
    HB._record_unreadable_stocklists()
    written = json.loads((tmp_path / "data" / "unreadable_stocklists.json")
                         .read_text(encoding="utf-8"))
    assert written["files"][0]["link"] == FILE_LINK


# ------------------------------------------------------------------ the build publishes ids

def test_channels_json_carries_ids_never_links(unreadable):
    got = build_web._unreadable_stocklists()
    text = json.dumps(got)
    for leak in SECRETS + ("javascript", "JAVASCRIPT", "data:text", "file:///", "evil.example",
                           "Set-Cookie"):
        assert leak not in text, leak
    assert got["count"] == len(ENTRIES)
    assert len(got["open"]) == len(got["files"]) == len(ENTRIES)
    for i, (name, opens) in enumerate(zip(got["files"], got["open"])):
        if i in OPENS:
            assert re.fullmatch(r"[0-9a-f]{16}", opens), (i, opens)
        else:
            assert opens == "", (i, name, opens)


def test_an_entry_without_a_link_still_shows_its_name(unreadable):
    got = build_web._unreadable_stocklists()
    assert got["files"][2] == "Koops Price.pdf" and got["open"][2] == ""
    assert got["files"][1] == "Victoria (VIC Townhouse) - no price list in the folder."
    assert got["files"][4] == "Evil One - could not be downloaded." and got["open"][4] == ""
    assert got["files"][0] == "92 New Street, South Kingsville - Pricelist.pdf (dropbox.com)"


def test_the_map_holds_exactly_the_ids_the_page_shows(unreadable, tmp_path):
    got = build_web._unreadable_stocklists()
    n = build_web._write_price_list_links(tmp_path / "site" / "api")
    links = json.loads((tmp_path / "site" / "api" / "_data" / "price_list_links.json")
                       .read_text(encoding="utf-8"))
    assert n == len(OPENS) == len(links)
    assert set(links) == {i for i in got["open"] if i}
    # A Dropbox file opens in Dropbox's preview (dl=0) rather than downloading (dl=1).
    assert links[got["open"][0]] == FILE_LINK.replace("dl=1", "dl=0")
    assert links[got["open"][1]] == FOLDER_LINK
    assert links[got["open"][3]] == LEGACY_LINK.replace("dl=1", "dl=0")
    assert all(v.startswith("https://") for v in links.values())


def test_no_list_means_no_links_and_no_error(tmp_path, monkeypatch):
    monkeypatch.setattr(build_web, "__file__", str(tmp_path / "build_web.py"))
    assert build_web._unreadable_stocklists()["open"] == []
    assert build_web._write_price_list_links(tmp_path / "api") == 0
    assert price_list.load_links(str(tmp_path / "api" / "_data" / "price_list_links.json")) == {}


# ------------------------------------------------------------------ the published site

_BUILD = r"""
import shutil, sqlite3, sys
from pathlib import Path
import build_web
app, site = Path(sys.argv[1]), Path(sys.argv[2])
# The fixture list sits at <argv[3]>/data/unreadable_stocklists.json. Pointing __file__
# there also leaves the probe's logo check out (no brand/ beside it), which is not tested here.
build_web.__file__ = str(Path(sys.argv[3]) / "build_web.py")
conn = sqlite3.connect(":memory:")
conn.execute("CREATE TABLE buildings (source_channel TEXT, builder_name TEXT, "
             "superseded_by TEXT, date_checked TEXT, checked_at TEXT)")
build_web._export_channel_health(site, conn)
build_web._bundle_research_function(app, site)
build_web._write_auth_middleware(site)
build_web._write_share_page(app, site)
shutil.copyfile(app / "index.html", site / "index.html")
print("site-ok")
"""


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """The parts of the deploy the list touches, built by build_web from this tree: the
    Settings tab's data, the function bundle (import probe included), the gate, the page
    and the share page."""
    base = tmp_path_factory.mktemp("price_list_site")
    _write_list(base)
    out = base / "site"
    out.mkdir()
    done = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", _BUILD, str(APP), str(out), str(base)],
        cwd=str(APP), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert done.returncode == 0 and "site-ok" in done.stdout, (
        done.stdout[-2000:] + done.stderr[-2000:])
    return out


def test_no_folder_link_reaches_any_published_static_file(site):
    static = list(build_web._static_files(site))
    names = {p.name for p in static}
    assert {"channels.json", "index.html", "r.html", "middleware.js"} <= names, names
    for path in static:
        text = path.read_text(encoding="utf-8", errors="replace")
        for leak in SECRETS:
            assert leak not in text, f"{leak!r} published in {path.relative_to(site)}"
        build_web._assert_no_folder_capability_urls(path)        # the nightly's own guard


def test_the_links_are_in_the_bundle_so_they_still_open(site):
    """The map is where the links went, not nowhere."""
    links = site / "api" / "_data" / "price_list_links.json"
    assert "/scl/fo/" in links.read_text(encoding="utf-8")
    channels = json.loads((site / "channels.json").read_text(encoding="utf-8"))
    ids = [i for i in channels["unreadable_stocklists"]["open"] if i]
    assert len(ids) == len(OPENS)
    loaded = price_list.load_links(str(links))
    for key in ids:
        code, location, _msg = price_list.resolve("id=" + key, loaded)
        assert code == 302 and "/scl/fo/" in location, (key, code)


def test_the_bundle_configures_the_route_and_carries_the_map(site):
    src = (APP / "build_web.py").read_text(encoding="utf-8")
    entry = src[src.index('"api/price_list.py":'):]
    assert '"includeFiles": "api/_data/price_list_links.json"' in entry[:entry.index("}")]
    assert (site / "api" / "price_list.py").is_file()


def test_the_nightly_guard_would_stop_a_leak(site, tmp_path):
    """Proved able to fail: the same guard over a static file holding a folder link."""
    leaked = tmp_path / "suburbs.json"
    leaked.write_text(json.dumps({"x": FOLDER_LINK}), encoding="utf-8")
    with pytest.raises(SystemExit, match="folder-level share link"):
        build_web._assert_no_folder_capability_urls(leaked)


# ------------------------------------------------------------------ the route

GOOD = "0123456789abcdef"


def test_a_known_id_redirects_to_its_link():
    assert price_list.resolve("id=" + GOOD, {GOOD: FOLDER_LINK}) == (302, FOLDER_LINK, "")


def test_an_unknown_id_is_a_404_that_says_why():
    code, location, message = price_list.resolve("id=" + GOOD, {})
    assert (code, location) == (404, "") and "nightly harvest" in message


@pytest.mark.parametrize("query", ["", "id=", "id=../../stock.json", "id=" + GOOD.upper(),
                                   "id=" + GOOD + "0", "id=" + GOOD[1:],
                                   "id=" + GOOD + "%0d%0aSet-Cookie:x", "foo=" + GOOD])
def test_an_id_the_build_never_makes_is_refused(query):
    assert price_list.resolve(query, {GOOD: FOLDER_LINK})[:2] == (400, "")


@pytest.mark.parametrize("bad", [e["link"] for e in ENTRIES[4:]] + ["", None, 7, "ftp://x/y"])
def test_a_tampered_map_never_redirects_anywhere_but_http(bad):
    """The build never writes these, and the route refuses them anyway."""
    code, location, _ = price_list.resolve("id=" + GOOD, {GOOD: bad})
    assert (code, location) == (404, "")


def test_a_link_outside_latin1_is_percent_encoded_not_a_crash():
    code, location, _ = price_list.resolve("id=" + GOOD, {GOOD: "https://x.com/Prixé.pdf"})
    assert code == 302 and location == "https://x.com/Prix%C3%A9.pdf", location


@pytest.fixture
def server(tmp_path, monkeypatch):
    links = tmp_path / "price_list_links.json"
    links.write_text(json.dumps({GOOD: FOLDER_LINK}), encoding="utf-8")
    monkeypatch.setattr(price_list, "LINKS", str(links))
    httpd = HTTPServer(("127.0.0.1", 0), price_list.handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _request(port, method, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path)
    res = conn.getresponse()
    body = res.read()
    conn.close()
    return res, body


def test_the_handler_answers_302_no_store_no_referrer(server, capfd):
    res, body = _request(server, "GET", "/api/price_list?id=" + GOOD)
    assert res.status == 302 and res.getheader("Location") == FOLDER_LINK
    assert res.getheader("Cache-Control") == "no-store"
    assert res.getheader("Referrer-Policy") == "no-referrer"
    assert body == b""
    err = capfd.readouterr().err
    assert "[api/price_list]" in err, "the log line was not captured, so the next check is empty"
    assert "rlkey" not in err and "/scl/fo/" not in err, "the link was logged"


def test_the_handler_404s_an_unknown_id_and_405s_a_post(server):
    res, body = _request(server, "GET", "/api/price_list?id=fedcba9876543210")
    assert res.status == 404 and b"nightly harvest" in body
    assert res.getheader("Location") is None
    res, _ = _request(server, "POST", "/api/price_list?id=" + GOOD)
    assert res.status == 405 and res.getheader("Allow") == "GET, HEAD"


# ------------------------------------------------------------------ behind the sign-in

_GATE_CHECKS = r"""
import mw from "./middleware.mjs";
const USER = "digital@smartpropertybuying.com.au", PASS = "test-password-not-the-real-one";
process.env.SITE_PASSWORD = PASS; process.env.SITE_USERNAME = USER;
process.env.SPB_SERVICE_KEY = "service-key-for-tests";
const PATH = "/api/price_list?id=0123456789abcdef";
const req = (path, opts = {}) => new Request("https://app.smartpropertybuying.com.au" + path,
  { method: opts.method || "GET", headers: opts.headers || {}, body: opts.body || null });
const out = {};
let r = await mw(req(PATH));
out.fetch = r ? r.status : "served";
r = await mw(req(PATH, { headers: { accept: "text/html" } }));
out.browse = r ? r.status : "served";
out.browse_to = r ? r.headers.get("location") : "";
r = await mw(req(PATH, { headers: { "x-spb-service-key": "service-key-for-tests" } }));
out.service_key = r ? r.status : "served";
r = await mw(req("/login", { method: "POST",
  headers: { "content-type": "application/x-www-form-urlencoded" },
  body: "email=" + encodeURIComponent(USER) + "&password=" + encodeURIComponent(PASS) +
        "&next=" + encodeURIComponent(PATH) }));
out.signin_to = r.headers.get("location");
const cookie = (r.headers.get("set-cookie") || "").split(";")[0];
r = await mw(req(PATH, { headers: { cookie } }));
out.signed_in = r ? r.status : "served";
console.log(JSON.stringify(out));
"""


def test_the_route_is_behind_the_sign_in(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the edge middleware cannot be exercised here")
    build_web._write_auth_middleware(tmp_path)
    (tmp_path / "middleware.mjs").write_text(
        (tmp_path / "middleware.js").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "gate.mjs").write_text(_GATE_CHECKS, encoding="utf-8")
    p = subprocess.run([node, "gate.mjs"], cwd=str(tmp_path), capture_output=True,
                       text=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    got = json.loads(p.stdout.strip().splitlines()[-1])
    assert got["fetch"] == 401, got                       # signed out: nothing
    assert got["browse"] == 302 and got["browse_to"].startswith("/login?next="), got
    assert got["service_key"] == 401, got                 # Agent 1's key opens only research
    assert got["signin_to"] == "/api/price_list?id=0123456789abcdef", got   # and back again
    assert got["signed_in"] == "served", got              # signed in: the route answers


# ------------------------------------------------------------------ the page

def _node_checks(index=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    env = dict(os.environ, **({"PL_INDEX": str(index)} if index else {}))
    return subprocess.run([node, str(PANEL_CHECKS)], capture_output=True, text=True,
                          timeout=60, env=env)


def test_the_page_links_only_ids_and_always_shows_the_name():
    p = _node_checks()
    assert p.returncode == 0, p.stdout + p.stderr


def test_the_page_checks_can_fail(tmp_path):
    """A page that trusted whatever sat in `open` would pass a raw link into href."""
    src = (APP / "index.html").read_text(encoding="utf-8")
    guard = "/^[0-9a-f]{16}$/.test(String(id || ''))"
    assert guard in src
    bad = tmp_path / "index.html"
    bad.write_text(src.replace(guard, "id"), encoding="utf-8")
    p = _node_checks(bad)
    assert p.returncode == 1 and "FAIL  javascript:" in p.stdout, p.stdout
