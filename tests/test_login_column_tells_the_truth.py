"""The Settings tab's Login column says what the nightly actually did.

THE NIGHT THIS EXISTS FOR, 23 Oct 2026 (run 39950641008). The Login column read:

    E-Agent    RE-LOGIN NEEDED   -- the harvest had reused that session and read 142
                                    stocklist files from 12 members-only pages
    Hermitage  RE-LOGIN NEEDED   -- the harvest's gate passed at 23:39 and read 288
                                    listings; the check reopened the file at 00:40
    Proxima    NOT CHECKED       -- as it had through a real 11-day sign-in outage
                                    (12 Oct 20:51 to 23 Oct 11:31), while the harvest
                                    logged "still bounced ... the password may have
                                    changed" every night

The owner and the client would chase two logins that were fine and miss the one that was
broken. Three causes, each pinned here:

  * portal_login judged E-Agent with its own copy of "signed in" -- a stocklist link on
    /access-projects, which since 24-26 Sep is a hub of three buttons with no such link
    whether signed in or not (18 of 18 nights "expired"). It now asks the harvest's own
    EAgentSource._authenticated, through a read-only PlaywrightScraper.
  * Every other portal was loaded its own way and a crash returned False, so "expired"
    could mean a dead session, a different page load or an exception. The row now records
    the reason and the address the page ended on, and a crash is "unknown".
  * Nothing recorded the harvest's own sign-in. It now does (scraper_base.
    record_harvest_login), and that is what decides the row; the check is the fallback.

NO NETWORK AND NO CHROMIUM. `_Web` below is a fake Playwright serving fixture pages: a
context is signed in when its storage_state carries a live token, exactly the property a
saved session has. It replaces only the browser -- PlaywrightScraper, EAgentSource,
BuilderPortalSource, ProximaSource and the checker all run their real code on it. A fake
browser PROFILE launch fails the test outright: nothing here may ever open one.
"""
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

import config  # noqa: E402
import portal_login  # noqa: E402
from sources import scraper_base  # noqa: E402
from sources import e_agent  # noqa: E402
from sources import builder_portals  # noqa: E402
from sources import proxima  # noqa: E402
from sources.e_agent import EAgentSource  # noqa: E402

TODAY = datetime.now().strftime("%d/%m/%Y")
EA = "https://www.e-agent.com.au"
HERMITAGE = "https://portal.hermitagehomes.com.au"
PX = "https://portal.proxima.com.au"
PX_LOGIN = PX + "/customer/account/login/"
HEADLESS_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
               "HeadlessChrome/148.0.7778.96 Safari/537.36")


# ------------------------------------------------------------------ the fixture pages

# E-Agent, as measured 26 Sep: /access-projects is a members hub of three buttons with NO
# stocklist link; ACCESS LISTINGS leads to /copy-of-home, which holds none either; the
# stocklist files are on the per-builder category pages. Signed out, every one of these
# is Wix's sign-up wall.
SIGN_UP_WALL = """<html><body><h1>Sign Up</h1>
<p>Already a member? <button>Log In</button></p>
<a href="https://www.e-agent.com.au/">Home</a></body></html>"""

EA_HUB = """<html><body>
<h2>Property Portal</h2><a href="https://www.e-agent.com.au/copy-of-home">ACCESS LISTINGS</a>
<h2>Training &amp; Development</h2><a href="https://www.e-agent.com.au/training">VIEW TRAINING</a>
<h2>Professional Referrers</h2><a href="https://www.e-agent.com.au/referrers">REFER A CLIENT</a>
</body></html>"""

EA_LISTINGS = """<html><body>
<h2>H&amp;L SEARCH</h2><a href="https://eagent.mekooflow.com/">OPEN THE SEARCH</a>
<h2>Builders</h2>
<a href="https://www.e-agent.com.au/house-and-land-1">VIC</a>
<a href="https://www.e-agent.com.au/copy-of-house-and-land">QLD</a>
</body></html>"""

EA_CATEGORY = """<html><body>
<h1>Builders</h1>
<h3>Aldrich Homes</h3>
<a href="https://www.e-agent.com.au/_files/ugd/0a1b2c_3d4e5f6a7b8c.pdf">Live Packages</a>
<h3>Hattan Homes</h3>
<a href="https://www.e-agent.com.au/_files/ugd/9f8e7d_6c5b4a3f2e1d.pdf">Price List</a>
</body></html>"""

HERMITAGE_STOCK = """<html><body><span>Welcome, Lee</span>
<a href="https://portal.hermitagehomes.com.au/logout">Logout</a>
<table><tr class="stock-row"><td class="address">Lot 1 Example St</td>
<td class="price">$650,000</td></tr></table></body></html>"""

HERMITAGE_LOGIN = """<html><body><form>
<input name="username"><input type="password" name="password">
<button type="submit">LOG IN</button></form></body></html>"""

PX_LOGIN_PAGE = """<html><body><form>
<input id="email" type="email" name="login[username]">
<input id="pass" type="password" name="login[password]">
<button id="send2" class="action login" type="submit">Sign In</button>
</form></body></html>"""

PX_PROJECTS = """<html><body><a href="https://portal.proxima.com.au/customer/account/logout/">Sign Out</a>
<h1>PROJECTS</h1></body></html>"""

PX_ACCOUNT = """<html><body><a href="https://portal.proxima.com.au/customer/account/logout/">Sign Out</a>
<h1>My Account</h1></body></html>"""


class _Doc(HTMLParser):
    """Elements in document order, each with its attributes and inner text."""
    VOID = {"input", "br", "img", "meta", "link", "hr"}

    def __init__(self, html):
        super().__init__()
        self.els, self._open, self._text = [], [], []
        self.feed(html)
        self.text = " ".join(" ".join(self._text).split())

    def handle_starttag(self, tag, attrs):
        el = {"tag": tag, "attrs": {k: (v or "") for k, v in attrs}, "text": ""}
        self.els.append(el)
        if tag not in self.VOID:
            self._open.append(el)

    def handle_endtag(self, tag):
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i]["tag"] == tag:
                del self._open[i:]
                break

    def handle_data(self, data):
        self._text.append(data)
        for el in self._open:
            el["text"] += data


_ATTR = re.compile(r"\[([\w-]+)=(?:(['\"])(.*?)\2|([^\]'\"]+))\]$")


def _matches(el, part):
    """A small CSS subset: tag, #id, .class, [attr=value], :has-text('...'). Anything else
    matches nothing."""
    part = part.strip()
    has_text = re.search(r":has-text\((['\"])(.*?)\1\)$", part)
    if has_text:
        if has_text.group(2).lower() not in " ".join(el["text"].split()).lower():
            return False
        part = part[:has_text.start()]
    attr = _ATTR.search(part)
    if attr:
        name, value = attr.group(1), attr.group(3) if attr.group(2) else attr.group(4)
        if el["attrs"].get(name) != value:
            return False
        part = part[:attr.start()]
    m = re.fullmatch(r"([a-z0-9]*)((?:#[\w-]+)?)((?:\.[\w-]+)*)", part)
    if not m:
        return False
    tag, ident, classes = m.groups()
    if tag and el["tag"] != tag:
        return False
    if ident and el["attrs"].get("id") != ident[1:]:
        return False
    have = set(el["attrs"].get("class", "").split())
    return all(c in have for c in classes.split(".")[1:])


class _El:
    def __init__(self, page, el):
        self.page, self.el = page, el

    def get_attribute(self, name):
        return self.el["attrs"].get(name)

    def inner_text(self):
        return self.el["text"]

    def is_visible(self):
        return True


class _Locator:
    def __init__(self, els):
        self.els = els

    def count(self):
        return len(self.els)

    @property
    def first(self):
        return self.els[0]


class _Page:
    def __init__(self, ctx):
        self.ctx, self.web = ctx, ctx.web
        self.url, self.doc, self.fields = "about:blank", _Doc(""), {}

    # -- navigation
    def goto(self, url, wait_until=None, timeout=None):
        self.web.loads.append((url, wait_until))
        if self.web.fail_goto:
            raise self.web.fail_goto("Timeout %sms exceeded." % timeout)
        self._land(url)

    def _land(self, url):
        for _ in range(5):
            page = self.web.pages.get(url)
            if page is None:
                self.url, self.doc = url, _Doc("<html><body>Not found</body></html>")
                return
            inside, outside = page
            html = inside if self.ctx.signed_in() else outside
            if html.startswith("->"):
                url = html[2:]
                continue
            self.url, self.doc, self.fields = url, _Doc(html), {}
            return

    def wait_for_load_state(self, state=None, timeout=None):
        self.web.load_states.append(state)

    def wait_for_timeout(self, ms):
        return None

    # -- reading
    def _all(self, selector):
        if selector.startswith("text="):
            want = selector[5:].strip().lower()
            return [e for e in self.doc.els
                    if e["tag"] in ("a", "button", "span") and want in e["text"].lower()]
        return [e for e in self.doc.els
                if any(_matches(e, part) for part in selector.split(","))]

    def query_selector(self, selector):
        hits = self._all(selector)
        return _El(self, hits[0]) if hits else None

    def query_selector_all(self, selector):
        return [_El(self, e) for e in self._all(selector)]

    def wait_for_selector(self, selector, timeout=None):
        el = self.query_selector(selector)
        if el is None:
            raise TimeoutError("waiting for %s" % selector)
        return el

    def get_by_role(self, role, name=None, exact=False):
        links = [e for e in self.doc.els if e["tag"] == "a"]
        if hasattr(name, "search"):
            links = [e for e in links if name.search(e["text"])]
        return _Locator([_El(self, e) for e in links])

    def locator(self, selector):
        return _Locator(self.query_selector_all(selector))

    def evaluate(self, script, arg=None):
        if script == "navigator.userAgent":
            return HEADLESS_UA
        if script == scraper_base.LOGGED_IN_JS:
            if re.search(r"twofactor|customerauth|otp|verify", self.url, re.I):
                return False
            if any(e["attrs"].get("type") == "password" for e in self.doc.els):
                return False
            markers = ("log out", "logout", "sign out", "my account", "dashboard")
            return any(m in self.doc.text.lower() for m in markers)
        if script == EAgentSource._LINKS_WITH_HEADINGS_JS:
            out, heads = [], []
            for e in self.doc.els:
                if re.fullmatch(r"h[1-6]", e["tag"]):
                    heads.insert(0, " ".join(e["text"].split()))
                elif e["tag"] == "a" and e["attrs"].get("href"):
                    out.append({"href": e["attrs"]["href"],
                                "label": " ".join(e["text"].split())[:60],
                                "heading": heads[0] if heads else "", "chain": list(heads)})
            return out
        if "live packages" in script:
            # The rule portal_login carried before 23 Oct, so the same fixture can be put
            # to it: a link that says "live packages" or points at a file.
            return any(re.search(r"live packages", e["text"], re.I)
                       or re.search(r"\.(pdf|xlsx|xls|csv)(\?|$)|docs\.google|onedrive|"
                                    r"smartsheet", e["attrs"].get("href", ""), re.I)
                       for e in self.doc.els if e["tag"] == "a")
        raise NotImplementedError("the fake page cannot run: " + script[:60])

    # -- typing
    def fill(self, selector, value, timeout=None):
        el = self.query_selector(selector)
        if el is None:
            raise TimeoutError("no field %s" % selector)
        self.fields[el.el["attrs"].get("type") or el.el["attrs"].get("name")] = value

    def click(self, selector, timeout=None):
        el = self.query_selector(selector)
        if el is None:
            raise TimeoutError("nothing to click at %s" % selector)
        if el.el["attrs"].get("type") == "submit":
            self.web.submit(self)


class _Ctx:
    def __init__(self, web, storage_state=None):
        self.web, self.token = web, None
        if storage_state:
            cookies = json.loads(Path(storage_state).read_text(encoding="utf-8"))["cookies"]
            self.token = next((c["value"] for c in cookies if c["name"] == "member"), None)

    def signed_in(self):
        return self.token in self.web.live

    def new_page(self):
        return _Page(self)

    def set_default_timeout(self, ms):
        return None

    def storage_state(self, path=None):
        self.web.writes.append(path)
        Path(path).write_text(json.dumps({"cookies": [
            {"name": "member", "value": self.token or "anonymous"}], "origins": []}))

    def close(self):
        return None


class _Browser:
    def __init__(self, web):
        self.web = web

    def new_context(self, storage_state=None, **kw):
        self.web.contexts.append(dict(kw, storage_state=storage_state))
        return _Ctx(self.web, storage_state)

    def close(self):
        return None


class _Web:
    """A fake sync_playwright() over the fixture pages above."""

    def __init__(self):
        self.pages = {}
        self.live = set()           # tokens a portal still honours
        self.passwords = {}         # login page url -> the password that signs in
        self.after_login = {}       # login page url -> where a good sign-in lands
        self.loads, self.load_states, self.contexts, self.writes = [], [], [], []
        self.launches = 0
        self.fail_goto = None
        self.chromium = self

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def start(self):
        return self

    def stop(self):
        return None

    def launch(self, headless=True, **kw):
        self.launches += 1
        return _Browser(self)

    def launch_persistent_context(self, *a, **kw):
        raise AssertionError("a saved browser PROFILE was launched -- never allowed here")

    def submit(self, page):
        want = self.passwords.get(page.url)
        if want is not None and page.fields.get("password") == want:
            page.ctx.token = "fresh-" + page.url
            self.live.add(page.ctx.token)
            page._land(self.after_login[page.url])


def _session(path, token):
    path.parent.mkdir(parents=True, exist_ok=True)
    cookies = [{"name": "member", "value": token}] if token else []
    path.write_text(json.dumps({"cookies": cookies, "origins": []}), encoding="utf-8")
    return path


class _NoBuilders:
    def get_all_builders(self):
        return []

    def get_builders_by_state(self, state):
        return []


TARGETS = {
    "e_agent": ("e_agent", "E-Agent (covers all E-Agent builders)", EA + "/access-projects"),
    "hermitage": ("portal_hermitage_homes", "Hermitage Homes", HERMITAGE + "/login"),
    "proxima": ("portal_proxima", "Proxima", PX_LOGIN),
    "torsion": ("portal_torsion_homes", "Torsion Homes",
                "https://referrer.torsionhomes.au/Stocklist.aspx"),
}


@pytest.fixture
def web(monkeypatch, tmp_path):
    """Every browser in the process is the fake; every store is under tmp_path."""
    import secrets_store
    w = _Web()
    for path, inside, outside in (
            ("/access-projects", EA_HUB, SIGN_UP_WALL),
            ("/copy-of-home", EA_LISTINGS, SIGN_UP_WALL),
            ("/house-and-land-1", EA_CATEGORY, SIGN_UP_WALL),
            ("/copy-of-house-and-land", EA_CATEGORY, SIGN_UP_WALL)):
        w.pages[EA + path] = (inside, outside)
    w.pages[HERMITAGE + "/allpackages"] = (HERMITAGE_STOCK, "->" + HERMITAGE + "/login")
    w.pages[HERMITAGE + "/login"] = (HERMITAGE_LOGIN, HERMITAGE_LOGIN)
    w.pages[PX + "/agent/projects/index/"] = (PX_PROJECTS, "->" + PX_LOGIN)
    w.pages[PX_LOGIN] = (PX_LOGIN_PAGE, PX_LOGIN_PAGE)
    w.pages[PX + "/customer/account/"] = (PX_ACCOUNT, "->" + PX_LOGIN)
    w.after_login[PX_LOGIN] = PX + "/customer/account/"

    sessions = tmp_path / ".sessions"
    sessions.mkdir()
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config, "SCRAPER_RATE_LIMIT_S", 0.0)
    for mod in (scraper_base, portal_login, e_agent, builder_portals):
        monkeypatch.setattr(mod, "SESSION_DIR", sessions, raising=False)
    monkeypatch.setattr(scraper_base, "PROFILE_DIR", tmp_path / ".browser_profiles")
    monkeypatch.setattr(portal_login, "LOGIN_STATUS_PATH", sessions / "login_status.json")
    monkeypatch.setattr(scraper_base, "sync_playwright", w, raising=False)
    monkeypatch.setattr(portal_login, "sync_playwright", w, raising=False)
    monkeypatch.setattr(scraper_base, "PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(portal_login, "PLAYWRIGHT_AVAILABLE", True)
    for mod in (e_agent, builder_portals, proxima):
        monkeypatch.setattr(mod, "PLAYWRIGHT_AVAILABLE", True, raising=False)
    monkeypatch.setattr(scraper_base, "_UA_CACHE", None)
    monkeypatch.delenv("SPB_USER_AGENT", raising=False)
    # Never the owner's vault: every credential in here is a test value from the env.
    monkeypatch.setattr(secrets_store, "KEYRING_AVAILABLE", False)
    for k in ("E_AGENT_USERNAME", "E_AGENT_PASSWORD", "PORTAL_HERMITAGE_HOMES_USERNAME",
              "PORTAL_HERMITAGE_HOMES_PASSWORD", "EAGENT_STOCKLIST_USERNAME",
              "EAGENT_STOCKLIST_PASSWORD", "PORTAL_PROXIMA_USERNAME",
              "PORTAL_PROXIMA_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(config, "E_AGENT_USERNAME", "", raising=False)
    monkeypatch.setattr(config, "E_AGENT_PASSWORD", "", raising=False)
    monkeypatch.setattr(portal_login, "BuilderRegistry", _NoBuilders)
    w.sessions = sessions
    return w


def _check(monkeypatch, *names, write=False):
    monkeypatch.setattr(portal_login, "_targets", lambda: [TARGETS[n] for n in names])
    rows = portal_login.check_logins(write=write)
    return {r["session"]: r for r in rows if r.get("session")}


# ------------------------------------------------------------------ E-Agent

def test_the_e_agent_members_hub_and_a_category_page_of_files_read_signed_in(web, monkeypatch):
    """The 23 Oct page shape, signed in: no stocklist link on the hub or behind ACCESS
    LISTINGS, PDFs under /_files/ugd/ on the category pages. The harvest calls that signed
    in; the check called it expired on 18 of 18 nights."""
    session = _session(web.sessions / "e_agent.json", "t-eagent")
    web.live.add("t-eagent")
    before = session.read_bytes()

    row = _check(monkeypatch, "e_agent")["e_agent"]

    assert row["state"] == "ok", row
    assert row.get("check", {}).get("signed_in") is True, row
    assert row.get("check", {}).get("url") == EA + "/house-and-land-1", row
    # It looked where the harvest looks: the hub, ACCESS LISTINGS, then a category page.
    urls = [u for u, _ in web.loads]
    assert urls[:3] == [EA + "/access-projects", EA + "/copy-of-home", EA + "/house-and-land-1"]
    # READ-ONLY: the session it was checking is exactly as it was.
    assert session.read_bytes() == before and web.writes == []


def test_the_anonymous_e_agent_page_reads_not_signed_in(web, monkeypatch):
    """The other half, without which a check that always says "ok" would pass the test
    above: a session FILE written by an anonymous visit (no member cookie) must read
    expired, with the reason -- and the same predicate with no saved state at all must say
    False, having looked at the very pages that carry files when signed in."""
    _session(web.sessions / "e_agent.json", None)

    row = _check(monkeypatch, "e_agent")["e_agent"]

    assert row["state"] == "expired", row
    assert row.get("basis") == "check", row
    assert "no stocklist file" in row.get("reason", ""), row
    assert row.get("url", "").startswith(EA), row
    category = [c.url for c in e_agent.EAGENT_CONFIG.category_pages[:e_agent.AUTH_CATEGORY_PAGES]]
    assert all(u in [x for x, _ in web.loads] for u in category), \
        "it must have looked at the category pages before saying no"

    (web.sessions / "e_agent.json").unlink()
    probe = portal_login._probe_session("e_agent", EA + "/access-projects")
    assert probe["signed_in"] is False, probe
    assert web.contexts[-1]["storage_state"] is None, "no saved state: anonymous"


# ------------------------------------------------------------------ the other portals

def test_a_portal_row_records_its_reason_and_final_url(web, monkeypatch):
    """Hermitage with a session the portal no longer honours: /allpackages redirects to
    /login. The row says so -- the reason and the address -- so it cannot be mistaken for a
    crash or a slow page. And the page was loaded the harvest's way: its user agent (never
    "HeadlessChrome"), its viewport, and a networkidle wait."""
    _session(web.sessions / "portal_hermitage_homes.json", "t-ended")

    row = _check(monkeypatch, "hermitage")["portal_hermitage_homes"]

    assert row["state"] == "expired", row
    assert row.get("reason") == "password field at /login", row
    assert row.get("url") == HERMITAGE + "/login", row
    opened = [c for c in web.contexts if c.get("storage_state")]
    assert opened, "the saved session was never opened"
    assert opened[-1]["storage_state"].endswith("portal_hermitage_homes.json")
    assert "Headless" not in opened[-1].get("user_agent", "Headless"), opened[-1]
    assert opened[-1].get("viewport") == {"width": 1440, "height": 900}
    assert "networkidle" in web.load_states


def test_a_signed_in_portal_still_reads_signed_in(web, monkeypatch):
    _session(web.sessions / "portal_hermitage_homes.json", "t-live")
    web.live.add("t-live")
    row = _check(monkeypatch, "hermitage")["portal_hermitage_homes"]
    assert row["state"] == "ok", row
    assert row.get("check", {}).get("url") == HERMITAGE + "/allpackages", row


def test_a_crash_is_recorded_as_a_crash_not_expired(web, monkeypatch):
    """Before 23 Oct any exception in the check returned False, and False was written as
    "expired": the row sent a person to re-authenticate a portal nobody had looked at."""
    _session(web.sessions / "portal_hermitage_homes.json", "t-live")
    web.live.add("t-live")
    web.fail_goto = TimeoutError

    row = _check(monkeypatch, "hermitage")["portal_hermitage_homes"]

    assert row["state"] == "unknown", row
    assert row.get("reason") == "the check could not run: TimeoutError", row
    assert "exceeded" not in json.dumps(row), \
        "only the class is kept: a Playwright message carries the URL it was loading"


def test_verify_asks_the_same_question(web, monkeypatch, capsys):
    """--verify was a third copy of "signed in" (the marker selector alone: no password
    veto, no E-Agent rule), so it too called a working E-Agent session NOT LOGGED IN."""
    _session(web.sessions / "e_agent.json", "t-eagent")
    web.live.add("t-eagent")
    monkeypatch.setattr(portal_login, "_targets", lambda: [TARGETS["e_agent"]])
    portal_login.status(verify=True)
    out = capsys.readouterr().out
    assert "[LOGGED IN]" in out and "NOT LOGGED IN" not in out, out


def test_proxima_and_a_browser_profile_are_never_opened_by_the_check(web, monkeypatch):
    """Proxima's saved session lapses between runs by design, and a browser profile holds
    the device trust that keeps its 2FA quiet. With no harvest record tonight, the row says
    nobody looked -- and no browser was launched for it."""
    _session(web.sessions / "portal_proxima.json", "t-old")
    profile = Path(config.PROJECT_ROOT) / ".browser_profiles" / "portal_hermitage_homes"
    profile.mkdir(parents=True)
    _session(web.sessions / "portal_hermitage_homes.json", "t-live")

    rows = _check(monkeypatch, "proxima", "hermitage")

    assert web.launches == 0, "nothing may be launched for Proxima or a profile"
    assert rows["portal_proxima"]["state"] == "unknown"
    assert "harvest" in rows["portal_proxima"]["note"]
    assert rows["portal_hermitage_homes"]["state"] == "unknown"
    assert "profile" in rows["portal_hermitage_homes"].get("reason", "")
    assert list(profile.iterdir()) == [], "the profile was not touched"


# ------------------------------------------------------------------ the harvest's own word

def _harvest_e_agent():
    src = EAgentSource()
    src._scrape_stocklist_files = lambda scraper: []
    src._scrape_category_pages = lambda scraper, seen: []
    src._scrape_listings = lambda scraper: []
    return src.search({})


def _harvest_hermitage(monkeypatch):
    def read(page, builder_hint="", state_hint=""):
        if "Logout" not in page.doc.text:
            return []
        return [{"lot_address": "Lot 1 Example St", "suburb": "", "builder_name": builder_hint,
                 "advertised_package_price": 650000.0, "state": state_hint}]
    monkeypatch.setattr(builder_portals, "extract_listings", read)
    monkeypatch.setattr(builder_portals, "_load_every_card", lambda scraper, cfg: None)
    bp = builder_portals.BuilderPortalSource(registry=_NoBuilders())
    builder = {"builder_name": "Hermitage Homes", "portal_url": HERMITAGE + "/login",
               "states": ["VIC"]}
    return bp._scrape_builder_pages(builder, {}, builder_portals.config_for_url(
        builder["portal_url"]))


def _harvest_proxima():
    src = proxima.ProximaSource(stored_counters={})
    src.stored_row_projects = {}
    # Stop right after the sign-in: everything past it reads project pages.
    src._clear_filters = lambda scraper: False
    return src.search({})


def _channels(tmp_path):
    import build_web
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE buildings (source_channel TEXT, builder_name TEXT, "
                 "superseded_by TEXT, date_checked TEXT, checked_at TEXT)")
    conn.executemany("INSERT INTO buildings VALUES (?,?,?,?,?)",
                     [("E-Agent", "Aldrich Homes", None, TODAY, None)] * 30
                     + [("Proxima", "Proxima", None, TODAY, None)] * 30
                     + [("Direct Builder Portal (live)", "Hermitage Homes", None, TODAY,
                         None)] * 30
                     + [("Direct Builder Portal (live)", "Torsion Homes", None, TODAY,
                         None)] * 30)
    out = tmp_path / "site"
    out.mkdir(exist_ok=True)
    build_web._export_channel_health(out, conn)
    payload = json.loads((out / "channels.json").read_text(encoding="utf-8"))
    rows = {c["channel"]: c for c in payload["channels"] + payload["portals"]}
    return payload, rows


def test_the_harvests_own_sign_in_reaches_the_settings_payload(web, monkeypatch, tmp_path):
    """One night of the 12-23 Oct outage, end to end, through the real sources:

      E-Agent    the harvest reuses its saved session
      Hermitage  the harvest's gate passes on its saved session; by the time the check
                 reopens the file the portal has ended it
      Proxima    the saved sign-in has lapsed and the stored password has been changed
                 at the portal, so the harvest is bounced

    channels.json must say E-Agent and Hermitage are signed in -- the nightly got in -- and
    Proxima needs a re-login, from what the harvest did and without the check opening it.
    Then the password is fixed, and the next night Proxima reads signed in."""
    monkeypatch.setenv("PORTAL_PROXIMA_USERNAME", "agent@example.test")
    monkeypatch.setenv("PORTAL_PROXIMA_PASSWORD", "the-old-password")
    web.passwords[PX_LOGIN] = "the-new-password"
    _session(web.sessions / "e_agent.json", "t-eagent")
    _session(web.sessions / "portal_hermitage_homes.json", "t-hermitage")
    _session(web.sessions / "portal_proxima.json", "t-lapsed")
    web.live.update({"t-eagent", "t-hermitage"})

    _harvest_e_agent()
    assert _harvest_hermitage(monkeypatch), "premise: the harvest read Hermitage"
    assert _harvest_proxima() == [], "premise: Proxima bounced"
    launched_by_harvest = web.launches

    web.live.discard("t-hermitage")       # the portal ends the session after the read
    loads_before = len(web.loads)
    _check(monkeypatch, "e_agent", "hermitage", "proxima", "torsion", write=True)
    assert not any(PX in u for u, _ in web.loads[loads_before:]), "the check opened Proxima"
    assert web.launches == launched_by_harvest + 2, "one look each at E-Agent and Hermitage"

    payload, rows = _channels(tmp_path)
    assert rows["E-Agent"]["login"]["state"] == "ok", rows["E-Agent"]["login"]
    assert rows["Hermitage Homes"]["login"]["state"] == "ok", rows["Hermitage Homes"]["login"]
    assert "signed in by the harvest" in rows["Hermitage Homes"]["login"]["note"]
    assert "signed out (password field at /login)" in rows["Hermitage Homes"]["login"]["note"]
    px = rows["Proxima"]["login"]
    assert px["state"] == "expired", px
    assert px.get("basis") == "harvest", px
    assert "may have changed" in px["note"], px
    assert "Proxima" in payload["needs_login"]
    assert "E-Agent" not in payload["needs_login"]
    assert "Hermitage Homes" not in payload["needs_login"]
    # The group row is its worst child, and a public stocklist is never the worst.
    assert rows["Direct Builder Portal (live)"]["login"]["state"] == "ok"

    # The next night: the new password is stored, and the harvest signs back in.
    monkeypatch.setenv("PORTAL_PROXIMA_PASSWORD", "the-new-password")
    monkeypatch.setattr(scraper_base, "_HARVEST_RUN", "the-next-night")
    _harvest_proxima()
    _check(monkeypatch, "e_agent", "hermitage", "proxima", "torsion", write=True)
    _payload, rows = _channels(tmp_path)
    assert rows["Proxima"]["login"]["state"] == "ok", rows["Proxima"]["login"]
    assert "with the stored password" in rows["Proxima"]["login"]["note"]
    assert rows["Proxima"]["login"]["basis"] == "harvest"


@pytest.mark.parametrize("night, outcome, words", [
    ("the saved sign-in is accepted", "session", "accepted"),
    ("lapsed, and the stored password works", "signed_in", "signed back in"),
    ("lapsed, and the password was changed", "bounced", "may have changed"),
    ("lapsed, and Proxima asks for a code", "bounced", "two-factor"),
    ("lapsed, and the sign-in form times out", "error", "TimeoutError"),
])
def test_every_proxima_sign_in_ending_is_written_down(web, monkeypatch, night, outcome, words):
    monkeypatch.setenv("PORTAL_PROXIMA_USERNAME", "agent@example.test")
    monkeypatch.setenv("PORTAL_PROXIMA_PASSWORD", "pw")
    _session(web.sessions / "portal_proxima.json", "t-px")
    if outcome == "session":
        web.live.add("t-px")
    web.passwords[PX_LOGIN] = "pw" if outcome == "signed_in" else "something-else"
    if "code" in night:
        web.passwords[PX_LOGIN] = "pw"
        web.after_login[PX_LOGIN] = PX + "/customerauth/twofactor/authentication/"
        web.pages[web.after_login[PX_LOGIN]] = ("<html><body>Enter the code</body></html>",) * 2
    if "times out" in night:
        web.pages[PX_LOGIN] = ("<html><body>maintenance</body></html>",) * 2

    _harvest_proxima()

    rec = scraper_base.read_harvest_logins()["portal_proxima"]
    assert rec["outcome"] == outcome, rec
    assert words in rec["reason"] or words in rec["outcome"], rec
    assert "?" not in rec["url"]


def test_a_hermitage_read_that_has_to_sign_in_says_so(web, monkeypatch):
    """A session the portal no longer honours, and a stored password that works: the harvest
    signs back in, and says it did."""
    monkeypatch.setenv("PORTAL_HERMITAGE_HOMES_USERNAME", "agent@example.test")
    monkeypatch.setenv("PORTAL_HERMITAGE_HOMES_PASSWORD", "pw")
    web.passwords[HERMITAGE + "/login"] = "pw"
    web.after_login[HERMITAGE + "/login"] = HERMITAGE + "/allpackages"
    _session(web.sessions / "portal_hermitage_homes.json", "t-ended")
    assert _harvest_hermitage(monkeypatch)
    rec = scraper_base.read_harvest_logins()["portal_hermitage_homes"]
    assert rec["outcome"] == "signed_in", rec


# ------------------------------------------------------------------ the record itself

def test_one_good_sign_in_in_a_run_stands_and_the_next_run_replaces_it(tmp_path, monkeypatch):
    path = tmp_path / "h.json"
    scraper_base.record_harvest_login("portal_x", "session", "ok", "https://h/a?token=SECRET",
                                      path=path)
    # A builder licensed in two states is read twice; the second, worse attempt in the
    # SAME run does not undo the first.
    scraper_base.record_harvest_login("portal_x", "error", "the harvest crashed: X", path=path)
    rec = scraper_base.read_harvest_logins(path)["portal_x"]
    assert rec["outcome"] == "session"
    assert rec["url"] == "https://h/a", "never the query string"
    monkeypatch.setattr(scraper_base, "_HARVEST_RUN", "a-later-run")
    scraper_base.record_harvest_login("portal_x", "bounced", "refused", path=path)
    assert scraper_base.read_harvest_logins(path)["portal_x"]["outcome"] == "bounced"


def test_an_old_record_is_not_tonights(tmp_path):
    """On the laptop logs/ persists between runs: the night before last is not tonight.
    Fixed ages, not the constant, so a bound that stopped bounding would be caught."""
    path = tmp_path / "h.json"
    scraper_base.record_harvest_login("portal_x", "session", path=path)
    now = datetime.now()
    assert "portal_x" in scraper_base.read_harvest_logins(path, now=now + timedelta(hours=12))
    assert scraper_base.read_harvest_logins(path, now=now + timedelta(days=2)) == {}


def test_an_error_is_no_verdict_and_the_check_decides(web, monkeypatch):
    """A harvest whose sign-in form timed out said nothing about the password. With a
    saved session the check still decides; for Proxima, which is never probed, the row
    says what happened instead of guessing either way."""
    _session(web.sessions / "portal_hermitage_homes.json", "t-live")
    web.live.add("t-live")
    scraper_base.record_harvest_login("portal_hermitage_homes", "error",
                                      "the sign-in form failed: TimeoutError")
    scraper_base.record_harvest_login("portal_proxima", "error",
                                      "the sign-in form failed: TimeoutError")
    rows = _check(monkeypatch, "hermitage", "proxima")
    assert rows["portal_hermitage_homes"]["state"] == "ok"
    assert rows["portal_hermitage_homes"]["basis"] == "check"
    assert "did not finish" in rows["portal_hermitage_homes"]["note"]
    assert rows["portal_proxima"]["state"] == "unknown"
    assert "TimeoutError" in rows["portal_proxima"]["note"]
    assert "did not try" not in rows["portal_proxima"]["note"]


def test_a_public_stocklist_never_speaks_for_the_portal_group():
    """_LOGIN_RANK had no "no_login_needed", so it took the default 2 and beat "ok": the
    moment Hermitage read signed in, the "Direct Builder Portal" group row would have said
    "no sign-in needed" over a portal that has one."""
    import build_web
    group = ["ok", "no_login_needed"]
    assert min(group, key=lambda s: build_web._LOGIN_RANK.get(s, 2)) == "ok"
    group = ["expired", "no_login_needed"]
    assert min(group, key=lambda s: build_web._LOGIN_RANK.get(s, 2)) == "expired"


# ------------------------------------------------------------------ the page

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_login_cell_shows_who_said_so(tmp_path):
    """Sliced out of index.html, so the test runs what ships. "saved today" is the time the
    runner COPIED the session file in from the bucket, so it read "today" under every row;
    a row the harvest decided says when and how it got in, and a red row says why."""
    html = (APP / "index.html").read_text(encoding="utf-8")
    m = re.search(r"^        const loginPill = c => \{.*?^        \};", html, re.S | re.M)
    assert m, "loginPill was not found in index.html -- has it been renamed?"
    checks = r"""
const esc = s => String(s ?? '').replace(/[&<>"']/g,
    ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
%s
let failures = 0;
const has = (got, want, what) => { if (!got.includes(want)) { failures++;
    console.error('FAIL ' + what + ': ' + JSON.stringify(want) + ' not in ' + got); } };
const hasnt = (got, bad, what) => { if (got.includes(bad)) { failures++;
    console.error('FAIL ' + what + ': ' + JSON.stringify(bad) + ' in ' + got); } };
const byHarvest = loginPill({login: {state: 'ok', age_days: 0, basis: 'harvest',
    note: 'signed in by the harvest at 23/10 23:39 (its saved session was accepted)'}});
has(byHarvest, 'signed in by the harvest at 23/10 23:39', 'a harvest ok says who');
hasnt(byHarvest, 'saved today', 'and not the copy time');
has(loginPill({login: {state: 'ok', age_days: 2}}), 'saved 2d ago', 'a plain ok as before');
const red = loginPill({login: {state: 'expired', self_service: false, session: 'portal_proxima',
    blocked_by: 'a changed password must be stored again',
    note: 'the harvest at 23/10 23:40 could not sign in: it may have changed'}});
has(red, 're-login needed', 'red');
has(red, 'could not sign in: it may have changed', 'red says why');
const crash = loginPill({login: {state: 'unknown', reason: 'the check could not run: TimeoutError',
    note: 'the check could not run: TimeoutError'}});
has(crash, 'not checked', 'a crash is not a dead login');
has(crash, 'TimeoutError', 'and says so');
if (failures) { console.error(failures + ' check(s) failed'); process.exit(1); }
console.log('login cell checks passed');
""" % m.group(0)
    bundle = tmp_path / "login_pill.mjs"
    bundle.write_text(checks, encoding="utf-8")
    r = subprocess.run(["node", str(bundle)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout
