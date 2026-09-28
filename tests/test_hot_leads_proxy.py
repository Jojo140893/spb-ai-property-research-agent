"""GET /api/hot_leads -- the Clare's Leads tab's server-side read of the callers Agent 1
stored (hot only until 15 Sep; hot, warm and nurture since).

The hook that stores them answers only a caller holding SPB_SERVICE_KEY, and a lead is a
person's name, phone number, email and appointment. So the proxy has two jobs
that fail silently if they are wrong: it must send the key upstream and NEVER send it
anywhere else (the page, a log, an error message, a redirect target), and it must make a
failed read look like a failure rather than like an empty list of leads.

NOTHING HERE TOUCHES THE NETWORK. urllib.request.urlopen is stubbed in every test, and an
autouse fixture makes any real socket connection raise: a proxy test that quietly reached
the real hook would pass while proving nothing, and would spend the real key doing it.

Since 28 Sep 2026 (Coleen: "only display the last 3 digits of the customer's mobile number
... the full customer contact details should remain restricted to authorised users only") a
third job: without a valid spb_contact cookie the answer is MASKED, and the section "contact
details" below reads the raw response bytes for every spelling of a caller's number and email.
"""
import copy
import email.message
import io
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from api import hot_leads as H

APP = Path(__file__).resolve().parent.parent
KEY = "svc-test-key-not-the-real-one-7f3a"
LEADS = {"status": "success", "count": 1, "leads": [{
    "version": 1, "callId": "call-123", "createdAt": "2026-09-14T09:30:00.000Z",
    "customer": {"name": "Test Buyer", "email": "buyer@example.com", "phone": "+61400000000"},
    "grade": "hot", "gradeSource": "agent", "gradeReason": "pre-approved, 3 months",
    "recommendations": [], "appointment": None,
    "research": {"status": "empty", "message": "", "record_id": "", "shortlist_count": 0,
                 "widened": ""},
}]}
CONTACT_PASSWORD = "contact-test-password-not-the-real-one"


def _unlocked(now=None):
    """The Cookie header a browser sends after a correct contact-details password."""
    from api import _contact
    return _contact.unlock_cookie(now).split(";")[0]


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """No network, and no key or URL leaking in from the shell that runs the suite."""
    def refuse(*a, **k):
        raise AssertionError("a test tried to open a real network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.delenv("SPB_SERVICE_KEY", raising=False)
    monkeypatch.delenv("SPB_HOT_LEADS_URL", raising=False)
    monkeypatch.delenv("SPB_CONTACT_PASSWORD", raising=False)
    monkeypatch.delenv("SPB_LEAD_NOTES_URL", raising=False)


class _Resp:
    """What urlopen returns: a context manager with a status and a body."""

    def __init__(self, body=b"", status=200):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Upstream:
    """Stands in for urlopen, and records every request it was handed."""

    def __init__(self, respond):
        self.respond, self.calls = respond, []

    def __call__(self, req, timeout=None):
        self.calls.append((req, timeout))
        return self.respond(req)


def _install(monkeypatch, respond):
    up = _Upstream(respond)
    monkeypatch.setattr(urllib.request, "urlopen", up)
    return up


def _ok(monkeypatch, body=None):
    raw = json.dumps(LEADS if body is None else body, indent=1).encode()
    return _install(monkeypatch, lambda req: _Resp(raw)), raw


def _call(method, path="/api/hot_leads", cookie=None):
    """Drive the handler as Vercel does, with no socket: (status, headers, body)."""
    h = H.handler.__new__(H.handler)
    h.command, h.path, h.request_version = method, path, "HTTP/1.1"
    h.requestline = f"{method} {path} HTTP/1.1"
    h.client_address = ("127.0.0.1", 0)
    h.headers = email.message.Message()
    if cookie is not None:
        h.headers["Cookie"] = cookie
    h.rfile, h.wfile = io.BytesIO(), io.BytesIO()
    getattr(h, "do_" + method)()
    head, _, body = h.wfile.getvalue().partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {k.strip().lower(): v.strip()
               for k, v in (line.split(":", 1) for line in lines[1:] if ":" in line)}
    return int(lines[0].split()[1]), headers, body


def _message(body):
    parsed = json.loads(body)
    assert parsed["status"] == "error"
    return parsed["message"]


# ------------------------------------------------------------------ not configured

def test_no_key_on_the_dashboard_is_a_503_that_names_it(monkeypatch):
    up, _ = _ok(monkeypatch)
    status, headers, body = _call("GET")
    assert status == 503
    assert json.loads(body) == {"status": "error",
                                "message": "SPB_SERVICE_KEY is not set on the dashboard project"}
    assert headers["cache-control"] == "no-store"
    assert up.calls == [], "with no key there is nothing to send, so nothing is sent"


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_a_blank_key_is_no_key(monkeypatch, blank):
    monkeypatch.setenv("SPB_SERVICE_KEY", blank)
    up, _ = _ok(monkeypatch)
    status, _, body = _call("GET")
    assert status == 503 and "SPB_SERVICE_KEY" in _message(body)
    assert up.calls == []


@pytest.mark.parametrize("url", ["http://vapi-kommo-hook.vercel.app/api/hot-leads",
                                 "file:///etc/passwd", "hot-leads", "https://"])
def test_a_non_https_upstream_is_refused_before_the_key_is_attached(monkeypatch, url):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_HOT_LEADS_URL", url)
    up, _ = _ok(monkeypatch)
    status, _, body = _call("GET")
    assert status == 503 and "https" in _message(body)
    assert up.calls == [], "the key must not be sent in the clear"


# ------------------------------------------------------------------ the happy path

def test_an_authorised_request_gets_the_hooks_json_untouched_plus_the_view(monkeypatch):
    """Until 28 Sep this was a byte-for-byte pass-through. With the contact-details password
    given (a valid spb_contact cookie) it is still the hook's JSON, every value as the hook
    wrote it -- customer included -- with only the two view keys added at the top level."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", CONTACT_PASSWORD)
    up, raw = _ok(monkeypatch)
    status, headers, body = _call("GET", cookie=_unlocked())
    assert status == 200
    assert json.loads(body) == {**LEADS, "contact": "full", "contactUnlock": "password"}, \
        "the tab and the hook share one format; nothing but the view is added here"
    assert json.loads(body)["leads"][0]["customer"] == LEADS["leads"][0]["customer"]
    assert headers["content-type"] == "application/json"
    assert headers["cache-control"] == "no-store"
    assert headers["vary"] == "Cookie"
    assert KEY not in body.decode()


def test_the_key_is_actually_sent_upstream(monkeypatch):
    """The one header the hook checks, with the environment's value, on a GET to the hook."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up, _ = _ok(monkeypatch)
    _call("GET")
    assert len(up.calls) == 1
    req, timeout = up.calls[0]
    assert req.full_url == H.DEFAULT_URL == "https://vapi-kommo-hook.vercel.app/api/hot-leads"
    assert req.get_method() == "GET"
    assert timeout == 20
    sent = {k.lower(): v for k, v in req.header_items()}
    assert sent.get("x-spb-service-key") == KEY


def test_the_configured_key_is_trimmed_before_it_is_sent(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", f"  {KEY}\n")
    up, _ = _ok(monkeypatch)
    assert _call("GET")[0] == 200
    sent = {k.lower(): v for k, v in up.calls[0][0].header_items()}
    assert sent.get("x-spb-service-key") == KEY


def test_the_key_is_not_carried_onto_a_redirect(monkeypatch):
    """urllib copies ordinary headers onto a redirect to whatever host it names. The key is
    attached as an unredirected header, so a 302 to elsewhere arrives without it."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up, _ = _ok(monkeypatch)
    _call("GET")
    req = up.calls[0][0]
    followed = urllib.request.HTTPRedirectHandler().redirect_request(
        req, io.BytesIO(), 302, "Found", email.message.Message(),
        "https://elsewhere.example/collect")
    assert followed is not None, "the stdlib would follow this redirect"
    assert all(k.lower() != "x-spb-service-key" for k, _ in followed.header_items()), \
        "the service key would be handed to the redirect's host"


def test_SPB_HOT_LEADS_URL_overrides_the_default(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_HOT_LEADS_URL", "https://hook.example/api/hot-leads")
    up, _ = _ok(monkeypatch)
    assert _call("GET")[0] == 200
    assert up.calls[0][0].full_url == "https://hook.example/api/hot-leads"


# ------------------------------------------------------------------ ?limit=

@pytest.mark.parametrize("path,forwarded", [
    ("/api/hot_leads?limit=5", "5"),
    ("/api/hot_leads?limit=200", "200"),
    ("/api/hot_leads?limit=1000", "1000"),        # the hook caps it; not our call
    ("/api/hot_leads?limit=007", "7"),
    ("/api/hot_leads?x=1&limit=12", "12"),
])
def test_an_integer_limit_is_forwarded(monkeypatch, path, forwarded):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up, _ = _ok(monkeypatch)
    assert _call("GET", path)[0] == 200
    url = up.calls[0][0].full_url
    assert url.startswith(H.DEFAULT_URL + "?")
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(url).query) == {"limit": [forwarded]}


@pytest.mark.parametrize("path", [
    "/api/hot_leads",
    "/api/hot_leads?limit=",
    "/api/hot_leads?limit=abc",
    "/api/hot_leads?limit=5.5",
    "/api/hot_leads?limit=-1",
    "/api/hot_leads?limit=1e3",
    "/api/hot_leads?limit=+5",
    "/api/hot_leads?limit=%D9%A5",                  # an Arabic-Indic five: a digit, not ASCII
    "/api/hot_leads?limit=5%26key%3Dx",             # "5&key=x", smuggling a parameter
    "/api/hot_leads?limit=1234567",
    "/api/hot_leads?other=5",
])
def test_anything_but_an_integer_limit_is_not_forwarded(monkeypatch, path):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up, _ = _ok(monkeypatch)
    assert _call("GET", path)[0] == 200
    assert up.calls[0][0].full_url == H.DEFAULT_URL, up.calls[0][0].full_url


def test_a_limit_joins_a_query_the_override_already_has(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_HOT_LEADS_URL", "https://hook.example/api/hot-leads?region=vic")
    up, _ = _ok(monkeypatch)
    _call("GET", "/api/hot_leads?limit=3")
    assert up.calls[0][0].full_url == "https://hook.example/api/hot-leads?region=vic&limit=3"


# ------------------------------------------------------------------ ?include_test=
# The hook leaves test records out unless it is sent include_test=1, so a test call never
# sits on Coleen's tab as a buyer to ring. The switch is passed on only as exactly "1".

def test_include_test_1_is_forwarded(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up, _ = _ok(monkeypatch)
    assert _call("GET", "/api/hot_leads?include_test=1")[0] == 200
    url = up.calls[0][0].full_url
    assert url == H.DEFAULT_URL + "?include_test=1", url


def test_include_test_travels_with_a_limit(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up, _ = _ok(monkeypatch)
    _call("GET", "/api/hot_leads?include_test=1&limit=5")
    url = up.calls[0][0].full_url
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(url).query) == {
        "limit": ["5"], "include_test": ["1"]}, url
    assert url.startswith(H.DEFAULT_URL + "?")


def test_include_test_joins_a_query_the_override_already_has(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_HOT_LEADS_URL", "https://hook.example/api/hot-leads?region=vic")
    up, _ = _ok(monkeypatch)
    _call("GET", "/api/hot_leads?include_test=1")
    assert up.calls[0][0].full_url == \
        "https://hook.example/api/hot-leads?region=vic&include_test=1"


@pytest.mark.parametrize("path", [
    "/api/hot_leads",
    "/api/hot_leads?include_test=",
    "/api/hot_leads?include_test=0",
    "/api/hot_leads?include_test=true",
    "/api/hot_leads?include_test=yes",
    "/api/hot_leads?include_test=01",
    "/api/hot_leads?include_test=1.0",
    "/api/hot_leads?include_test=%201",            # " 1"
    "/api/hot_leads?include_test=1%26limit%3D999",  # "1&limit=999", smuggling a parameter
    "/api/hot_leads?include_test=1&include_test=0",  # two answers is not a yes
    "/api/hot_leads?include_test=0&include_test=1",
    "/api/hot_leads?INCLUDE_TEST=1",
    "/api/hot_leads?hl_test=1",                     # the PAGE's switch is not the hook's
])
def test_anything_but_exactly_include_test_1_is_not_forwarded(monkeypatch, path):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up, _ = _ok(monkeypatch)
    assert _call("GET", path)[0] == 200
    url = up.calls[0][0].full_url
    assert url == H.DEFAULT_URL, url
    assert "include_test" not in url


# ------------------------------------------------------------------ ?grade=
# Since 15 Sep the hook stores hot, warm and nurture callers. The tab filters the list it
# already has, but a reader may ask the hook for one grade; like include_test, only an exact
# answer is passed on, and only as a literal written in hot_leads.py.

@pytest.mark.parametrize("grade", ["hot", "warm", "nurture"])
def test_an_exact_grade_is_forwarded(monkeypatch, grade):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up, _ = _ok(monkeypatch)
    assert _call("GET", f"/api/hot_leads?grade={grade}")[0] == 200
    url = up.calls[0][0].full_url
    assert url == H.DEFAULT_URL + "?grade=" + grade, url


def test_grade_travels_with_include_test_and_a_limit(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up, _ = _ok(monkeypatch)
    _call("GET", "/api/hot_leads?grade=warm&include_test=1&limit=5")
    url = up.calls[0][0].full_url
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(url).query) == {
        "limit": ["5"], "include_test": ["1"], "grade": ["warm"]}, url
    assert url.startswith(H.DEFAULT_URL + "?")


def test_grade_joins_a_query_the_override_already_has(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    monkeypatch.setenv("SPB_HOT_LEADS_URL", "https://hook.example/api/hot-leads?region=vic")
    up, _ = _ok(monkeypatch)
    _call("GET", "/api/hot_leads?grade=nurture")
    assert up.calls[0][0].full_url == "https://hook.example/api/hot-leads?region=vic&grade=nurture"


@pytest.mark.parametrize("path", [
    "/api/hot_leads?grade=",
    "/api/hot_leads?grade=closed",                  # not researched, so never stored
    "/api/hot_leads?grade=all",
    "/api/hot_leads?grade=HOT",
    "/api/hot_leads?grade=Warm",
    "/api/hot_leads?grade=nurturing",
    "/api/hot_leads?grade=hot,warm",
    "/api/hot_leads?grade=%20hot",                  # " hot"
    "/api/hot_leads?grade=hot%26include_test%3D1",  # "hot&include_test=1", smuggling
    "/api/hot_leads?grade=hot&grade=warm",          # two answers is not one
    "/api/hot_leads?grade=hot&grade=hot",
    "/api/hot_leads?GRADE=hot",
    "/api/hot_leads?grades=hot",
])
def test_anything_but_exactly_one_known_grade_is_not_forwarded(monkeypatch, path):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up, _ = _ok(monkeypatch)
    assert _call("GET", path)[0] == 200
    url = up.calls[0][0].full_url
    assert url == H.DEFAULT_URL, url
    assert "grade" not in url and "include_test" not in url


@pytest.mark.parametrize("grade", ["closed", "HOT", "hot&include_test=1", "", None, 1])
def test_fetch_itself_refuses_a_grade_outside_the_three(monkeypatch, grade):
    """A second guard behind grade_from: a future caller of fetch cannot splice text in."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up, _ = _ok(monkeypatch)
    assert H.fetch(grade=grade)[0] == 200
    assert up.calls[0][0].full_url == H.DEFAULT_URL


# ------------------------------------------------------------------ upstream failures

def _http_error(code, body):
    def respond(req):
        raise urllib.error.HTTPError(req.full_url, code, "upstream", {},
                                     io.BytesIO(json.dumps(body).encode()))
    return respond


@pytest.mark.parametrize("code,upstream", [
    (404, {"status": "error"}),
    (500, {"status": "error", "message": "boom"}),
    # Vercel's own 503 in front of a struggling function passes on its own: still a 502.
    (503, {"status": "error", "message": "service unavailable"}),
    (504, {"status": "error", "message": "FUNCTION_INVOCATION_TIMEOUT"}),
])
def test_an_upstream_failure_that_may_pass_is_a_502_naming_its_status(monkeypatch, code,
                                                                       upstream):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, _http_error(code, upstream))
    status, headers, body = _call("GET")
    assert status == 502
    msg = _message(body)
    assert str(code) in msg
    if upstream.get("message"):
        assert upstream["message"] in msg, "the hook's own reason is the useful part"
    assert "not configured" not in msg and "vapi-kommo-hook project" not in msg
    assert KEY not in body.decode()
    assert headers["cache-control"] == "no-store"


# The state straight after a deploy that set SPB_SERVICE_KEY on the dashboard but not yet on
# the hook. As a 502 the tab said "press Refresh in a minute", and the message sent whoever
# read it to the dashboard project, which already had the key. A setting missing on the hook
# is a 503 (the tab's "needs a setting" status) naming the hook project.
@pytest.mark.parametrize("code,upstream,names", [
    (500, {"status": "error", "message": "SPB_SERVICE_KEY not configured"}, "SPB_SERVICE_KEY"),
    (503, {"status": "error", "message": "hot-lead store not configured"},
     "BLOB_READ_WRITE_TOKEN"),
    # The hook may start naming itself in its own messages; the mapping must survive that.
    (500, {"status": "error", "message": "SPB_SERVICE_KEY not configured on vapi-kommo-hook"},
     "SPB_SERVICE_KEY"),
    (401, {"status": "error", "message": "bad key"}, "does not match"),
])
def test_a_setting_missing_on_the_hook_is_a_503_naming_the_hook_project(monkeypatch, code,
                                                                         upstream, names):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, _http_error(code, upstream))
    status, headers, body = _call("GET")
    assert status == 503, "refreshing will not fix a missing setting"
    msg = _message(body)
    assert "on the vapi-kommo-hook project" in msg, msg
    assert "dashboard project" not in msg, "that points at the project that is already right"
    assert msg != "SPB_SERVICE_KEY is not set on the dashboard project"
    assert names in msg and str(code) in msg and upstream["message"] in msg, msg
    # Words a message-based check on the tab can key on, whatever the status.
    assert "not configured" in msg or "does not match" in msg
    assert KEY not in body.decode()
    assert headers["cache-control"] == "no-store"


def test_a_401_with_no_reason_is_still_a_setting_not_a_retry(monkeypatch):
    """A 401 page with no JSON reason: Vercel deployment protection in front of the hook,
    most likely. Not a key mismatch the hook reported, so it is not called one."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)

    def respond(req):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                     io.BytesIO(b"<!doctype html><title>Log in</title>"))
    _install(monkeypatch, respond)
    status, _, body = _call("GET")
    msg = _message(body)
    assert status == 503 and "401" in msg and "vapi-kommo-hook project" in msg
    assert "does not match" not in msg and "deployment protection" in msg


@pytest.mark.parametrize("code", [400, 401, 403, 404, 429, 500, 502, 503, 504])
@pytest.mark.parametrize("message", [None, "bad key", "SPB_SERVICE_KEY not configured"])
def test_no_upstream_answer_becomes_a_401_from_this_endpoint(monkeypatch, code, message):
    """The tab reads a 401 from /api/hot_leads as "your sign-in expired" and sends Coleen to
    the login page. Nothing the hook says may come back as one."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, _http_error(code, {"status": "error", "message": message}))
    status, _, body = _call("GET")
    assert status in (502, 503), status
    assert str(code) in _message(body)


@pytest.mark.parametrize("code", [401, 500])
def test_an_upstream_error_that_quotes_the_key_does_not_pass_it_on(monkeypatch, capsys, code):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, _http_error(code, {"status": "error",
                                             "message": f"bad key {KEY} for this project"}))
    status, _, body = _call("GET")
    assert status in (502, 503) and str(code) in _message(body)
    assert KEY not in body.decode()
    out = capsys.readouterr()
    assert KEY not in out.out and KEY not in out.err


@pytest.mark.parametrize("code,raw", [
    (302, b"<html>moved</html>"),
    # A JSON body, so only the STATUS can catch it: a check that leaned on "is it JSON"
    # would pass an error payload straight through to the tab as though it were leads.
    (302, b'{"status": "success", "count": 0, "leads": []}'),
    (500, b'{"status": "error", "message": "boom"}'),
])
def test_a_non_2xx_that_urlopen_returns_rather_than_raises_is_a_502(monkeypatch, code, raw):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, lambda req: _Resp(raw, status=code))
    status, _, body = _call("GET")
    msg = _message(body)
    assert status == 502 and str(code) in msg
    if raw.startswith(b"{"):
        assert "not JSON" not in msg, "caught by the JSON check, not the status check"


def test_a_settings_failure_urlopen_returns_rather_than_raises_is_also_a_503(monkeypatch):
    """The same mapping on the other path: an opener that hands back a non-2xx response."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, lambda req: _Resp(
        b'{"status": "error", "message": "hot-lead store not configured"}', status=503))
    status, _, body = _call("GET")
    msg = _message(body)
    assert status == 503 and "vapi-kommo-hook project" in msg and "BLOB_READ_WRITE_TOKEN" in msg


def test_a_200_that_is_not_json_is_a_502_not_an_empty_tab(monkeypatch):
    """A login or error page served in the hook's place."""
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _install(monkeypatch, lambda req: _Resp(b"<!doctype html><title>Log in</title>"))
    status, _, body = _call("GET")
    assert status == 502 and "not JSON" in _message(body)


@pytest.mark.parametrize("exc,says", [
    (TimeoutError("timed out"), "timed out"),
    (socket.timeout("The read operation timed out"), "timed out"),
    (urllib.error.URLError(TimeoutError("timed out")), "timed out"),
    (urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed")), "gaierror"),
    (urllib.error.URLError("connection refused"), "connection refused"),
    (ConnectionResetError(104, "reset"), "ConnectionResetError"),
])
def test_a_network_failure_or_timeout_is_a_502(monkeypatch, exc, says):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)

    def respond(req):
        raise exc
    _install(monkeypatch, respond)
    status, _, body = _call("GET")
    assert status == 502
    msg = _message(body)
    assert "could not reach" in msg and says in msg, msg
    assert KEY not in body.decode()


# ------------------------------------------------------------------ contact details
# Coleen, 28 Sep 2026: "Please only display the last 3 digits of the customer's mobile number
# for identification purposes. Not everyone on the team should have access to the customer's
# full email address or phone number." Without a valid spb_contact cookie the answer is
# masked on the server, so a masked viewer's browser never receives the number at all. The
# fixture's number is distinctive on purpose: every spelling of it below is searched for in
# the RAW response bytes, where a leak would actually be.

PHONE = "+61 400 111 678"
EMAIL = "joseph.test@example.com"
PHONE_SPELLINGS = ["+61 400 111 678", "+61400111678", "61400111678", "0400111678",
                   "0400 111 678", "0400-111-678", "400111678", "400 111 678",
                   "(0)400 111 678", "0400 111678"]
EMAIL_SPELLINGS = ["joseph.test@example.com", "joseph dot test at example"]
# Someone else's details inside a lead's free text: a partner, a second address.
OTHER_PEOPLE = ["0411 222 333", "0411222333", "partner@elsewhere.net"]
WA_PHONE = "+61 400 999 321"
RECORDING = "https://storage.vapi.ai/call-678-recording.wav"
REA = "https://www.realestate.com.au/property-house-qld-jubilee-143456789"
DRIVE = "https://drive.google.com/drive/folders/1AbCdEf12345678"

CONTACT_LEAD = {
    "version": 1, "callId": "call-678", "channel": "vapi", "fieldsFingerprint": "",
    "createdAt": "2026-09-28T01:02:03.000Z", "endedReason": "customer-ended-call",
    "durationSeconds": 312, "recordingUrl": RECORDING,
    "customer": {"name": "Joseph Test", "email": EMAIL, "phone": PHONE},
    "grade": "hot", "gradeSource": "agent",
    "gradeReason": "pre-approved; said to call him on 0400 111 678",
    "summary": ("Joseph wants a QLD house under $800,000. Email joseph.test@example.com or "
                "ring +61400111678. His partner is on 0411 222 333 and "
                "partner@elsewhere.net. Spelled out: joseph dot test at example dot com."),
    "brief": {"client_name": "Joseph Test", "budget_max": 800000, "deposit_amount": 80000,
              "state": "QLD", "primary_suburbs": ["Jubilee"], "bedrooms_min": 4,
              "product_type": ["House & Land"], "timeframe": "3 months, text 0400-111-678"},
    "appointment": {"start": "2026-09-30T00:00:00.000Z", "meetingUrl": "https://meet.example/abc",
                    "uid": "u-1", "source": "agent"},
    "kommo": {"contactId": 123, "leadId": 54494433,
              "leadUrl": "https://spb.kommo.com/leads/detail/54494433"},
    "recommendations": [{"title": "Lot 12 Jubilee", "price": 779000,
                         "source_link": {"url": REA, "label": ""}}],
    "research": {"status": "ok", "message": "Agent 2 shortlisted 3; top 3 attached",
                 "record_id": "r-20260928-1", "shortlist_count": 3, "widened": "",
                 "location": [{"said": "Jubilee (0400111678)", "used": ["Jubilee"],
                               "method": "unresolved",
                               "note": "sent by joseph.test@example.com"}]},
}
# A WhatsApp lead: Kommo often names a WhatsApp contact by their number.
WA_LEAD = {
    "version": 1, "callId": "wa-54494433", "channel": "whatsapp", "createdAt": "2026-09-27T01:00:00.000Z",
    "recordingUrl": "", "customer": {"name": WA_PHONE, "email": "", "phone": "+61400999321"},
    "grade": "warm", "summary": "", "brief": {"client_name": WA_PHONE, "state": "QLD"},
    "kommo": {"contactId": 9, "leadId": 54494433,
              "leadUrl": "https://spb.kommo.com/leads/detail/54494433"},
    "recommendations": [], "research": {"status": "ok", "message": "", "location": []},
}
NOTES = {"entries": [
    {"id": "e1", "at": "2026-09-28T02:00:00.000Z", "by": "Coleen", "action": "call_booked",
     "text": ("Booked for 2026-10-02; rang 0400 111 678, emailed JOSEPH.TEST@example.com and "
              "cc'd sam@spb.example")},
    {"id": "e2", "at": "2026-09-28T03:00:00.000Z", "by": "0400111678", "action": "other",
     "text": "Package sent"}],
    "links": [
        {"id": "l1", "at": "2026-09-28T02:05:00.000Z", "by": "Coleen", "url": DRIVE,
         "label": "Pack for 0400111678"},
        {"id": "l2", "at": "2026-09-28T02:06:00.000Z", "by": "Coleen", "url": REA,
         "label": "Lot 12 Jubilee"},
        {"id": "l3", "at": "2026-09-28T02:07:00.000Z", "by": "Coleen",
         "url": "https://wa.me/61400111678", "label": "WhatsApp"},
        {"id": "l4", "at": "2026-09-28T02:08:00.000Z", "by": "Coleen",
         "url": "https://docs.example/share?to=joseph.test%40example.com", "label": "Doc"}],
    "rev": 4}
CONTACT_LEADS = {"status": "success", "count": 2, "leads": [CONTACT_LEAD, WA_LEAD],
                 "notes": {"call-678": NOTES}, "notesErrors": ["wa-54494433"]}


def _leaks(body):
    """Every spelling of a caller's number or email found in the raw bytes, lower-cased."""
    text = body.decode("utf-8").lower()
    spellings = PHONE_SPELLINGS + EMAIL_SPELLINGS + OTHER_PEOPLE + [
        WA_PHONE, "61400999321", "400999321", RECORDING]
    return [s for s in spellings if s.lower() in text]


def _masked(monkeypatch, upstream=None, cookie=None):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _ok(monkeypatch, copy.deepcopy(CONTACT_LEADS if upstream is None else upstream))
    return _call("GET", cookie=cookie)


def test_the_masked_view_carries_no_spelling_of_any_callers_number_or_email(monkeypatch):
    status, headers, body = _masked(monkeypatch)
    assert status == 200
    assert _leaks(body) == [], "contact details in the masked answer"
    doc = json.loads(body)
    assert doc["contact"] == "masked"
    assert doc["contactUnlock"] == "not-configured"          # no SPB_CONTACT_PASSWORD here
    lead, wa = doc["leads"]
    assert lead["customer"] == {"name": "Joseph Test", "email": "", "phone": "",
                                "phoneLast3": "678", "hasEmail": True, "hasPhone": True}
    assert lead["recordingUrl"] == "", "the recording is the caller saying their number"
    assert wa["customer"] == {"name": "[hidden]", "email": "", "phone": "",
                              "phoneLast3": "321", "hasEmail": False, "hasPhone": True}
    assert wa["brief"]["client_name"] == "[hidden]"
    assert "[hidden]" in lead["summary"] and "[hidden]" in lead["gradeReason"]
    assert "[hidden]" in lead["brief"]["timeframe"]
    assert lead["research"]["location"][0]["note"] == "sent by [hidden]"
    assert headers["cache-control"] == "no-store"
    assert headers["vary"] == "Cookie"


def test_the_masked_view_keeps_everything_the_team_works_from(monkeypatch):
    """Hiding the number must not cost the name, the brief, the Kommo link, the shortlist,
    the booking or a saved property link."""
    doc = json.loads(_masked(monkeypatch)[2])
    lead, wa = doc["leads"]
    assert lead["summary"].startswith("Joseph wants a QLD house under $800,000.")
    for key in ("callId", "createdAt", "appointment", "kommo", "recommendations", "grade"):
        assert lead[key] == CONTACT_LEAD[key], key
    assert wa["callId"] == "wa-54494433" and wa["kommo"]["leadUrl"].endswith("/54494433")
    assert lead["brief"]["budget_max"] == 800000 and lead["brief"]["primary_suburbs"] == ["Jubilee"]
    assert lead["research"]["record_id"] == "r-20260928-1"
    assert lead["research"]["location"][0]["used"] == ["Jubilee"]
    assert doc["count"] == 2 and doc["notesErrors"] == ["wa-54494433"]

    notes = doc["notes"]["call-678"]
    booked, sent = notes["entries"]
    assert booked["text"] == "Booked for 2026-10-02; rang [hidden], emailed [hidden] and cc'd [hidden]"
    assert booked["at"] == "2026-09-28T02:00:00.000Z" and booked["action"] == "call_booked"
    assert sent["by"] == "[hidden]" and sent["text"] == "Package sent"
    drive, rea, wa_me, doc_link = notes["links"]
    assert drive["url"] == DRIVE and drive["label"] == "Pack for [hidden]"
    assert rea["url"] == REA, "a listing id is not a phone number"
    assert wa_me["url"] == "[hidden]", "a link that carries the number is hidden whole"
    assert doc_link["url"] == "[hidden]", "a percent-encoded email is still an email"
    assert notes["rev"] == 4


@pytest.mark.parametrize("text", [
    "call 0400 111 678", "+61 400 111 678", "+61400111678", "0400-111-678", "(0)400 111 678",
    "0061 400 111 678", "400.111.678",
    "0400" + chr(0xA0) + "111" + chr(0xA0) + "678",           # a no-break space
    "0400" + chr(0x2013) + "111" + chr(0x2013) + "678",       # an en dash (phone autocorrect)
    "joseph.test@example.com", "Joseph.Test@Example.COM", "joseph dot test at example dot com",
    "josephtest at example", "joseph_test at example",
    # Anyone's, not only the lead's.
    "someone@else.org", "0411 222 333", "+61 411 222 333", "(03) 9123 4567", "1300 123 456",
])
def test_every_spelling_of_a_number_or_address_is_hidden_in_free_text(text):
    out = H._contact.Scrubber([(EMAIL, PHONE)]).free(text)
    rest = out.replace("[hidden]", "")
    assert "[hidden]" in out, out
    assert not re.search(r"\d{3}", rest) and "@" not in rest, out
    assert "joseph" not in rest.lower() and "example" not in rest.lower(), out


@pytest.mark.parametrize("text", [
    "under $800,000", "$750 000 - 800 000", "booked for 2026-10-02",
    "at 2026-09-28T02:00:00.000Z", "Jubilee QLD 4000", "4 bed, 2 bath, 2 car",
    "from 14/09/2026", "Joseph Test", "joseph test", "Joseph at the example home",
    "Lot 12, stage 3", "ring after 5.30",
])
def test_what_is_not_a_number_or_address_is_left_alone(text):
    """Masking that ate budgets, dates and the caller's name would make the tab useless to the
    people it is masked for."""
    assert H._contact.Scrubber([(EMAIL, PHONE)]).free(text) == text


@pytest.mark.parametrize("url,kept", [
    (REA, True),                                             # a 9-digit listing id
    (DRIVE, True),
    ("https://www.domain.com.au/12-smith-st-jubilee-qld-4000-2019123456", True),
    ("https://wa.me/61400111678", False),
    ("https://x.example/?to=0411222333", False),
    ("https://x.example/p?tel=%2B61411222333", False),       # "+61..." percent-encoded
    ("https://x.example/?e=a%40b.com", False),                # "a@b.com" percent-encoded
    ("https://x.example/joseph.test@example.com", False),
])
def test_a_saved_link_is_kept_whole_or_hidden_whole(url, kept):
    out = H._contact._scrub_url(url, H._contact.Scrubber([(EMAIL, PHONE)]))
    assert out == (url if kept else "[hidden]")


def test_the_absence_check_sees_a_leak_when_the_masking_is_switched_off(monkeypatch):
    """A silent check reads as a pass. With the masking replaced by the identity, the same
    search over the same bytes must find the number -- or the test above proves nothing."""
    monkeypatch.setattr(H._contact, "mask_hot_leads", lambda doc: doc)
    status, _, body = _masked(monkeypatch)
    assert status == 200
    found = _leaks(body)
    assert "0400 111 678" in found and "joseph.test@example.com" in found and RECORDING in found


def test_a_number_the_masking_walk_misses_is_a_502_not_a_leak(monkeypatch):
    """The second look (Scrubber.leaked): if a future change stops the walk scrubbing a
    field, the answer fails closed instead of publishing the number."""
    monkeypatch.setattr(H._contact.Scrubber, "own", lambda self, text: text)
    monkeypatch.setattr(H._contact.Scrubber, "free", lambda self, text: text)
    status, headers, body = _masked(monkeypatch)
    assert status == 502
    assert "not shown" in _message(body)
    assert _leaks(body) == []
    assert headers["cache-control"] == "no-store"


@pytest.mark.parametrize("upstream", [
    [CONTACT_LEAD],                                             # a list, not an object
    {"status": "success", "leads": "call-678 0400 111 678"},
    {"status": "success"},                                      # no leads at all
    {"status": "success", "leads": ["0400 111 678"]},
    {"status": "success", "leads": [{"customer": "0400 111 678"}]},
    {"status": "success", "leads": [CONTACT_LEAD], "notes": "0400 111 678"},
    {"status": "success", "leads": [CONTACT_LEAD], "notes": {"call-678": {"entries": "x"}}},
    {"status": "success", "leads": [CONTACT_LEAD],
     "notes": {"call-678": {"entries": ["0400 111 678"], "links": []}}},
])
def test_an_answer_that_cannot_be_masked_is_a_502_never_the_raw_body(monkeypatch, upstream):
    status, _, body = _masked(monkeypatch, upstream)
    assert status == 502, body[:200]
    assert "could not be hidden" in _message(body)
    assert _leaks(body) == []


def test_the_callers_own_number_is_hidden_even_in_machine_written_fields(monkeypatch):
    """The generic 8-digit rule stays out of ids and links (it would break the Kommo link),
    so the lead's OWN number is the rule that guards them -- in its shorter spellings too."""
    lead = copy.deepcopy(CONTACT_LEAD)
    lead["appointment"]["meetingUrl"] = "https://meet.example/join?ph=0400111678"
    lead["recommendations"][0]["note"] = "held for 400 111 678"
    status, _, body = _masked(monkeypatch, {"status": "success", "count": 1, "leads": [lead]})
    assert status == 200 and _leaks(body) == []
    got = json.loads(body)["leads"][0]
    assert got["appointment"]["meetingUrl"] == "https://meet.example/join?ph=[hidden]"
    assert got["recommendations"][0]["note"] == "held for [hidden]"
    assert got["kommo"] == CONTACT_LEAD["kommo"], "an 8-digit Kommo id is not a phone number"


def test_a_number_stored_as_a_number_is_hidden_too(monkeypatch):
    lead = copy.deepcopy(CONTACT_LEAD)
    lead["customer"]["phone"] = 61400111678
    lead["brief"]["alt"] = 61400111678
    status, _, body = _masked(monkeypatch, {"status": "success", "count": 1, "leads": [lead]})
    assert status == 200 and _leaks(body) == []
    got = json.loads(body)["leads"][0]
    assert got["customer"]["phoneLast3"] == "678" and got["brief"]["alt"] == "[hidden]"


@pytest.mark.parametrize("customer,last3,has_email,has_phone", [
    ({"name": "A", "email": "", "phone": ""}, "", False, False),
    ({"name": "A", "email": " ", "phone": "12345"}, "", False, True),   # too short to show any
    ({"name": "A", "email": "a@b.co", "phone": "123456"}, "456", True, True),
    (None, "", False, False),
])
def test_phone_last3_needs_a_number_of_six_digits_or_more(monkeypatch, customer, last3,
                                                          has_email, has_phone):
    lead = copy.deepcopy(LEADS["leads"][0])
    lead["customer"] = customer
    status, _, body = _masked(monkeypatch, {"status": "success", "count": 1, "leads": [lead]})
    assert status == 200
    got = json.loads(body)["leads"][0]["customer"]
    assert (got["email"], got["phone"]) == ("", "")
    assert (got["phoneLast3"], got["hasEmail"], got["hasPhone"]) == (last3, has_email, has_phone)


def test_a_valid_cookie_gets_the_full_view(monkeypatch):
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", CONTACT_PASSWORD)
    status, headers, body = _masked(monkeypatch, cookie=_unlocked())
    assert status == 200
    doc = json.loads(body)
    assert doc == {**CONTACT_LEADS, "contact": "full", "contactUnlock": "password"}
    assert PHONE in body.decode() and EMAIL in body.decode()
    assert headers["vary"] == "Cookie"


def test_the_cookie_is_found_among_the_others_a_browser_sends(monkeypatch):
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", CONTACT_PASSWORD)
    cookie = f"spb_session=123.abc; {_unlocked()}; theme=dark"
    assert json.loads(_masked(monkeypatch, cookie=cookie)[2])["contact"] == "full"


def test_with_a_password_set_and_no_cookie_the_view_says_it_can_be_unlocked(monkeypatch):
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", CONTACT_PASSWORD)
    status, _, body = _masked(monkeypatch)
    doc = json.loads(body)
    assert status == 200 and _leaks(body) == []
    assert (doc["contact"], doc["contactUnlock"]) == ("masked", "password")


def _forged():
    """Cookies that must never unlock: (label, cookie)."""
    import time as _t
    from api import _contact as C
    now = int(_t.time())
    good = _unlocked()
    exp, sig = good.split("=", 1)[1].split(".")
    return [
        ("signed with another password",
         f"spb_contact={exp}.{C.sign('some-other-password', exp)}"),
        ("expired, correctly signed",
         f"spb_contact={now - 60}.{C.sign(CONTACT_PASSWORD, str(now - 60))}"),
        ("tampered signature", f"spb_contact={exp}.{sig[:-2]}{'AA' if sig[-2:] != 'AA' else 'BB'}"),
        ("expiry extended, signature kept", f"spb_contact={int(exp) + 3600}.{sig}"),
        ("signed, but claims to last a day",
         f"spb_contact={now + 86400}.{C.sign(CONTACT_PASSWORD, str(now + 86400))}"),
        ("signed with an empty key", f"spb_contact={exp}.{C.sign('', exp)}"),
        ("the password itself", f"spb_contact={CONTACT_PASSWORD}"),
        ("no signature", f"spb_contact={exp}."),
        ("no expiry", f"spb_contact=.{sig}"),
        ("an empty value", "spb_contact="),
        ("a non-ASCII signature", f"spb_contact={exp}.{chr(0xE9)}{sig[1:]}"),
        ("the site session's name", f"spb_session={exp}.{sig}"),
        ("a signature for the site session's message",
         f"spb_contact={exp}.{C._b64url(__import__('hmac').new(CONTACT_PASSWORD.encode(), ('digital@smartpropertybuying.com.au|' + exp).encode(), 'sha256').digest())}"),
    ]


_FORGED = ["signed with another password", "expired, correctly signed", "tampered signature",
           "expiry extended, signature kept", "signed, but claims to last a day",
           "signed with an empty key", "the password itself", "no signature", "no expiry",
           "an empty value", "a non-ASCII signature", "the site session's name",
           "a signature for the site session's message"]


@pytest.mark.parametrize("label", _FORGED)
def test_a_wrong_expired_or_tampered_cookie_gets_the_masked_view(monkeypatch, label):
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", CONTACT_PASSWORD)
    forged = dict(_forged())
    assert sorted(forged) == sorted(_FORGED)
    cookie = forged[label]
    status, _, body = _masked(monkeypatch, cookie=cookie)
    assert status == 200
    assert json.loads(body)["contact"] == "masked", label
    assert _leaks(body) == [], label


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_an_unset_or_blank_contact_password_never_unlocks(monkeypatch, blank):
    """Anyone can sign with an empty key, so with no password there is nothing to unlock:
    not even a cookie signed exactly as a blank password would sign it."""
    from api import _contact as C
    exp = str(int(__import__("time").time()) + 600)
    for key in (blank, blank.strip()):
        monkeypatch.setenv("SPB_CONTACT_PASSWORD", blank)
        status, _, body = _masked(monkeypatch, cookie=f"spb_contact={exp}.{C.sign(key, exp)}")
        doc = json.loads(body)
        assert status == 200 and _leaks(body) == []
        assert (doc["contact"], doc["contactUnlock"]) == ("masked", "not-configured")


def test_changing_the_contact_password_ends_every_unlock(monkeypatch):
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", CONTACT_PASSWORD)
    cookie = _unlocked()
    assert json.loads(_masked(monkeypatch, cookie=cookie)[2])["contact"] == "full"
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", "a-brand-new-contact-password")
    status, _, body = _masked(monkeypatch, cookie=cookie)
    assert json.loads(body)["contact"] == "masked" and _leaks(body) == []


def test_a_cookie_minted_four_hours_ago_has_run_out(monkeypatch):
    import time as _t
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", CONTACT_PASSWORD)
    old = _unlocked(now=_t.time() - 4 * 60 * 60 - 1)
    assert json.loads(_masked(monkeypatch, cookie=old)[2])["contact"] == "masked"
    fresh = _unlocked(now=_t.time() - 4 * 60 * 60 + 60)
    assert json.loads(_masked(monkeypatch, cookie=fresh)[2])["contact"] == "full"


# ------------------------------------------------------------------ review fixes, 28 Sep 2026
# Each test below fails with the fix it names taken out (checked by breaking each one on
# purpose, one at a time).

def _one(lead, notes=None):
    """The hook's answer for one lead, with `notes` as that lead's notes when given."""
    doc = {"status": "success", "count": 1, "leads": [lead]}
    if notes is not None:
        doc.update(notes={lead["callId"]: notes}, notesErrors=[])
    return doc


def _note(text, **extra):
    return {"entries": [{"id": "e1", "at": "2026-09-28T02:00:00.000Z", "by": "Coleen",
                         "action": "other", "text": text, **extra}], "links": [], "rev": 1}


def _note_text(body, cid="call-678"):
    return json.loads(body)["notes"][cid]["entries"][0]["text"]


def test_one_unreadable_notes_object_is_absent_not_a_502_for_the_whole_tab(monkeypatch):
    """The hook's own shape for a notes file it could not read (lib/hot-leads.js before the
    fix on its side): null beside notesErrors. Masking the null raised, and ONE broken note
    blanked every lead for everyone without the password."""
    lead = dict(copy.deepcopy(CONTACT_LEAD), callId="c1")
    status, _, body = _masked(monkeypatch, {"status": "success", "count": 1, "leads": [lead],
                                            "notes": {"c1": None}, "notesErrors": ["c1"]})
    assert status == 200, body[:300]
    assert _leaks(body) == []
    doc = json.loads(body)
    got = doc["leads"][0]
    assert doc["contact"] == "masked" and got["callId"] == "c1"
    assert (got["customer"]["phone"], got["customer"]["email"]) == ("", "")
    assert got["customer"]["phoneLast3"] == "678" and got["recordingUrl"] == ""
    assert doc["notes"] == {} and doc["notesErrors"] == ["c1"], "absent, and named"


def test_mask_hot_leads_itself_reads_a_null_as_absent():
    """Not only through view(): the rule belongs to mask_hot_leads, whoever calls it."""
    lead = dict(copy.deepcopy(CONTACT_LEAD), callId="c1")
    out = H._contact.mask_hot_leads({"status": "success", "count": 1, "leads": [lead],
                                     "notes": {"c1": None}, "notesErrors": ["c1"]})
    assert out["notes"] == {} and out["notesErrors"] == ["c1"]
    assert out["leads"][0]["customer"]["phone"] == ""


def test_a_null_the_hook_did_not_name_is_named_and_the_full_view_drops_it_too(monkeypatch):
    """Treated as absent in both views, so the tab gets one shape; and named in notesErrors,
    because "could not read" and "no notes yet" must not look the same."""
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", CONTACT_PASSWORD)
    upstream = {"status": "success", "count": 2, "leads": [CONTACT_LEAD, WA_LEAD],
                "notes": {"call-678": NOTES, "wa-54494433": None}}
    for cookie, view in ((None, "masked"), (_unlocked(), "full")):
        status, _, body = _masked(monkeypatch, upstream, cookie=cookie)
        doc = json.loads(body)
        assert status == 200 and doc["contact"] == view
        assert list(doc["notes"]) == ["call-678"], view
        assert doc["notesErrors"] == ["wa-54494433"], view


@pytest.mark.parametrize("text", [
    "0400/111/678", "0400,111,678", "0400\n111\n678", "0400\r\n111\r\n678", "0400\t111\t678",
    "0400    111    678", "0400 / 111 / 678", "+61/400/111/678", "0400_111_678",
    "0400,\n111,\n678",
])
def test_the_leads_own_number_is_hidden_whatever_punctuation_is_between_its_digits(
        monkeypatch, text):
    """Slashes, commas, line breaks (the hook keeps a note's), tabs and runs of spaces: the
    generic rule leaves those out for dates and prices, but these are the lead's own 9 digits
    in order, which are the number whatever sits between them."""
    assert H._contact.Scrubber([(EMAIL, PHONE)]).free(text) == "[hidden]"
    status, _, body = _masked(monkeypatch, _one(copy.deepcopy(CONTACT_LEAD),
                                                _note("Texted " + text + " at 5")))
    assert status == 200, body[:300]
    assert _note_text(body) == "Texted [hidden] at 5"


def test_the_same_punctuation_leaves_other_numbers_alone():
    scrub = H._contact.Scrubber([(EMAIL, PHONE)])
    for text in ("from 14/09/2026, 10:30", "prices 400,000 to 450,000", "Lot 400, 111 Smith St",
                 "0400/111/679"):
        assert scrub.free(text) == text, text


# The lead's digits in order, with words between them that no pattern can list. The masking
# cannot hide this; the second look must see it and fail closed.
EVADED = "ring 0400 or 111 then 678"


def test_the_number_written_past_every_pattern_in_a_note_is_a_502(monkeypatch):
    """The NOTES-level second look (mask_notes), by its digit half: every non-digit removed,
    "0400 or 111 then 678" is 0400111678."""
    status, _, body = _masked(monkeypatch, _one(copy.deepcopy(CONTACT_LEAD), _note(EVADED)))
    assert status == 502 and "could not be hidden" in _message(body)
    assert "then 678" not in body.decode()


def test_the_number_written_past_every_pattern_in_a_lead_is_a_502(monkeypatch):
    """The LEAD-level second look (mask_lead), by the same digit half."""
    lead = copy.deepcopy(CONTACT_LEAD)
    lead["summary"] = "Joseph said to " + EVADED
    status, _, body = _masked(monkeypatch, _one(lead))
    assert status == 502 and "could not be hidden" in _message(body)
    assert "then 678" not in body.decode()


def test_a_short_stored_number_is_not_found_in_a_note_of_dates_and_prices(monkeypatch):
    """A number stored cut short (7 digits) is looked for only as an unbroken run: its digits
    in order across words turn up by chance, and a false match is a 502 for the whole tab."""
    lead = copy.deepcopy(CONTACT_LEAD)
    lead["customer"]["phone"] = "4567890"
    status, _, body = _masked(monkeypatch, _one(lead, _note("Lot 456 on 78-90 Smith St")))
    assert status == 200 and _note_text(body) == "Lot 456 on 78-90 Smith St"


def test_notes_are_masked_with_their_own_leads_spellings(monkeypatch):
    """The lead-specific rules reach the notes, not only the generic ones: the address said
    aloud and the number written with slashes are nobody else's pattern."""
    notes = _note("Emailed joseph dot test at example dot com")
    notes["entries"].append(dict(notes["entries"][0], id="e2", text="Texted 0400/111/678"))
    status, _, body = _masked(monkeypatch, _one(copy.deepcopy(CONTACT_LEAD), notes))
    assert status == 200, body[:300]
    texts = [e["text"] for e in json.loads(body)["notes"]["call-678"]["entries"]]
    assert texts == ["Emailed [hidden]", "Texted [hidden]"]


@pytest.mark.parametrize("email,said", [
    ("joseph@shahid.com.au", "joseph at shahid dot com dot au"),
    ("joseph.shahid@gmail.com", "joseph dot shahid at gmail dot com"),
])
def test_a_callers_name_is_not_read_as_their_address_said_aloud(monkeypatch, email, said):
    """joseph@shahid.com.au with the "at" optional read "Joseph Shahid" -- the name -- as the
    address, and hid it from the one identifier a masked viewer has. Said aloud WITH its "at"
    and domain, the address still goes."""
    lead = copy.deepcopy(CONTACT_LEAD)
    lead["customer"].update(name="Joseph Shahid", email=email)
    lead["brief"]["client_name"] = "Joseph Shahid"
    lead["summary"] = f"Joseph Shahid wants a QLD house. Email {said}."
    status, _, body = _masked(monkeypatch, _one(lead))
    assert status == 200, body[:300]
    got = json.loads(body)["leads"][0]
    assert got["customer"]["name"] == "Joseph Shahid"
    assert got["brief"]["client_name"] == "Joseph Shahid"
    assert got["summary"] == "Joseph Shahid wants a QLD house. Email [hidden]."


def test_the_name_fields_skip_only_the_address_said_aloud(monkeypatch):
    """A name kept on purpose: "Joseph at Shahid Realty" is the address said aloud to the
    summary's rule and the caller's name to the two name fields -- which still lose the
    address as written and the lead's number."""
    lead = copy.deepcopy(CONTACT_LEAD)
    lead["customer"].update(name="Joseph at Shahid Realty", email="joseph@shahid.com.au")
    lead["brief"]["client_name"] = "Joseph at Shahid Realty"
    lead["summary"] = "Joseph at Shahid Realty rang."
    status, _, body = _masked(monkeypatch, _one(lead))
    assert status == 200, body[:300]
    got = json.loads(body)["leads"][0]
    assert got["customer"]["name"] == got["brief"]["client_name"] == "Joseph at Shahid Realty"
    assert got["summary"] == "[hidden] Realty rang."

    lead["customer"]["name"] = "joseph@shahid.com.au"
    lead["brief"]["client_name"] = "Joseph 0400/111/678"
    status, _, body = _masked(monkeypatch, _one(lead))
    got = json.loads(body)["leads"][0]
    assert status == 200 and got["customer"]["name"] == "[hidden]"
    assert got["brief"]["client_name"] == "Joseph [hidden]"


ZWSP, WJ, LRM, ZWJ = chr(0x200B), chr(0x2060), chr(0x200E), chr(0x200D)


@pytest.mark.parametrize("text", [
    "0400" + ZWSP + "111" + ZWSP + "678",
    "04" + WJ + "00 111 6" + WJ + "78",
    LRM + "+61 400 111 678" + LRM,
    "0400" + chr(0xAD) + "111" + chr(0x202E) + "678",       # a soft hyphen, a bidi override
    "0411" + ZWSP + "222" + WJ + "333",                      # someone else's: the generic rule
    "03" + LRM + " 9123 " + chr(0xFEFF) + "4567",           # a byte-order mark too
    "joseph" + ZWSP + ".test@example.com",
    "joseph.test@exa" + WJ + "mple.com",
    "joseph.test" + LRM + "@example.com",
    "joseph.test@exam" + ZWJ + "ple.com",                    # a joiner between two letters
    "joseph dot te" + ZWSP + "st at example dot com",
    "sam@spb" + ZWSP + ".example",                           # someone else's address
])
def test_an_invisible_character_does_not_walk_a_number_or_address_past_the_masking(
        monkeypatch, text):
    """Pasted out of WhatsApp or Word, a number or an address can carry characters that do
    not show. They go before any pattern runs (the rule the hook applies to what it stores)."""
    assert H._contact.Scrubber([(EMAIL, PHONE)]).free(text) == "[hidden]", repr(text)
    status, _, body = _masked(monkeypatch, _one(copy.deepcopy(CONTACT_LEAD),
                                                _note("Sent " + text)))
    assert status == 200, body[:300]
    assert _note_text(body) == "Sent [hidden]"


def test_the_second_look_sees_through_an_invisible_character_too(monkeypatch):
    """If the walk ever stopped stripping them, the check behind it still would: an address
    with a zero-width space in it is found, and the answer fails closed."""
    monkeypatch.setattr(H._contact.Scrubber, "own", lambda self, text: text)
    monkeypatch.setattr(H._contact.Scrubber, "free", lambda self, text: text)
    lead = copy.deepcopy(LEADS["leads"][0])
    lead.update(callId="call-678", customer={"name": "J", "email": EMAIL, "phone": ""})
    status, _, body = _masked(monkeypatch, _one(lead, _note("joseph.test@exa" + ZWSP
                                                            + "mple.com")))
    assert status == 502 and "exa" + ZWSP not in body.decode()


def test_a_stored_address_with_an_invisible_character_is_still_the_leads_address(monkeypatch):
    """The lead record itself can carry one (an address pasted into Kommo). It is the address
    without it that a machine-written field would show, and that must go."""
    lead = copy.deepcopy(CONTACT_LEAD)
    lead["customer"]["email"] = "jo" + ZWSP + "@ab.co"       # too short for a said-aloud rule
    lead["appointment"]["meetingUrl"] = "https://meet.example/join?e=jo@ab.co"
    status, _, body = _masked(monkeypatch, _one(lead))
    assert status == 200, body[:300]
    got = json.loads(body)["leads"][0]
    assert got["appointment"]["meetingUrl"] == "https://meet.example/join?e=[hidden]"


def test_a_saved_link_with_an_invisible_character_is_kept_as_the_plain_link():
    """A zero-width space pasted into a listing link is not a reason to hide the link."""
    scrub = H._contact.Scrubber([(EMAIL, PHONE)])
    assert H._contact._scrub_url(REA[:30] + ZWSP + REA[30:], scrub) == REA
    assert H._contact._scrub_url("https://wa.me/614001" + ZWSP + "11678", scrub) == "[hidden]"


def test_an_email_stored_as_a_word_is_not_looked_for_as_one(monkeypatch):
    """A lead whose email was stored as "none" must not make every "none" in the answer look
    like a leak: only an address is looked for as one."""
    lead = copy.deepcopy(CONTACT_LEAD)
    lead["customer"]["email"] = "none"
    lead["summary"] = "Has none of the paperwork yet."
    status, _, body = _masked(monkeypatch, _one(lead))
    assert status == 200 and json.loads(body)["leads"][0]["summary"] == lead["summary"]


def test_an_emojis_joiner_is_kept(monkeypatch):
    """The one format character kept: U+200D holds an emoji sequence together."""
    family = chr(0x1F468) + ZWJ + chr(0x1F469) + ZWJ + chr(0x1F467)
    text = f"Whole family {family} keen, call after 5"
    assert H._contact.Scrubber([(EMAIL, PHONE)]).free(text) == text
    status, _, body = _masked(monkeypatch, _one(copy.deepcopy(CONTACT_LEAD), _note(text)))
    assert status == 200 and _note_text(body) == text


def test_a_request_id_is_kept_as_the_hook_stored_it(monkeypatch):
    """The tab's random id per Save click, stored on the item: machine-written, so the
    generic 8-digit rule does not turn it into [hidden]."""
    rid = "r-20260928-12345678-ab"
    status, _, body = _masked(monkeypatch, _one(copy.deepcopy(CONTACT_LEAD),
                                                _note("Package sent", requestId=rid)))
    assert status == 200
    assert json.loads(body)["notes"]["call-678"]["entries"][0]["requestId"] == rid


@pytest.mark.parametrize("stale_before", [1, 3])
def test_every_spb_contact_cookie_is_looked_at_not_only_the_first(monkeypatch, stale_before):
    """A browser can hold two cookies of one name -- a stale one from another path, or one an
    older version of the site set -- and sends them all. The valid one unlocks wherever it
    comes in the header."""
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", CONTACT_PASSWORD)
    stale = ["spb_contact=1.stale-and-long-expired"] * stale_before
    cookie = "; ".join(stale + [_unlocked()])
    assert json.loads(_masked(monkeypatch, cookie=cookie)[2])["contact"] == "full"


# ------------------------------------------------------------------ methods and logs

@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_only_GET_is_served(monkeypatch, method):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up, _ = _ok(monkeypatch)
    status, headers, body = _call(method)
    assert status == 405
    assert headers.get("allow") == "GET"
    assert _message(body)
    assert up.calls == [], "a refused method must not reach the hook"


def test_nothing_logged_carries_the_key(monkeypatch, capsys):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    _ok(monkeypatch)
    _call("GET", "/api/hot_leads?limit=5")
    _install(monkeypatch, _http_error(401, {"status": "error", "message": "bad key"}))
    _call("GET")

    def boom(req):
        raise TimeoutError("timed out")
    _install(monkeypatch, boom)
    _call("GET")
    out = capsys.readouterr()
    # Proves the capture saw the handler's log at all, so the absence below means something.
    assert "[api/hot_leads]" in out.err
    assert KEY not in out.err and KEY not in out.out


# ------------------------------------------------------------------ the build ships it

def test_every_public_function_is_bundled_and_configured():
    """A function file not named in _FUNCTION_ENTRY is never copied into the deploy, and the
    tab's fetch 404s on a site that otherwise looks healthy."""
    import build_web

    public = sorted(p.name for p in (APP / "api").glob("*.py") if not p.name.startswith("_"))
    assert "hot_leads.py" in public
    missing = [n for n in public if n not in build_web._FUNCTION_ENTRY]
    assert not missing, f"not bundled by _bundle_research_function: {missing}"
    src = (APP / "build_web.py").read_text(encoding="utf-8")
    unconfigured = [n for n in public if f'"api/{n}":' not in src]
    assert not unconfigured, f"no entry in the vercel.json functions map: {unconfigured}"
    assert '"api/hot_leads.py": {"memory": 256, "maxDuration": 30,' in src


def test_the_contact_rules_travel_with_every_function_that_imports_them():
    """api/_contact.py is not a route, so nothing above notices it missing -- but hot_leads,
    contact_unlock and lead_notes import it at module level, and without it all three 500.
    It must be copied (_FUNCTION_ENTRY), shipped with each function (includeFiles) and
    imported by the build's probe."""
    import build_web

    assert "_contact.py" in build_web._FUNCTION_ENTRY
    src = (APP / "build_web.py").read_text(encoding="utf-8")
    for name in ("hot_leads.py", "contact_unlock.py", "lead_notes.py"):
        entry = src[src.index(f'"api/{name}":'):]
        entry = entry[:entry.index("}") + 1]
        assert "api/_contact.py" in entry, f"api/{name} is deployed without api/_contact.py"
    notes = src[src.index('"api/lead_notes.py":'):]
    assert "api/hot_leads.py" in notes[:notes.index("}") + 1], \
        "api/lead_notes.py imports hot_leads.py and must carry it"
    assert "import _contact" in build_web._READONLY_PROBE
    assert "contact_unlock, lead_notes" in build_web._READONLY_PROBE


def test_every_bundled_function_file_exists_in_source():
    import build_web

    missing = [n for n in build_web._FUNCTION_ENTRY if not (APP / "api" / n).is_file()]
    assert not missing, f"listed in _FUNCTION_ENTRY but not in api/: {missing}"


def test_a_function_file_missing_from_source_stops_the_bundle_and_names_it(monkeypatch,
                                                                            tmp_path):
    """build_web.py tracked and api/hot_leads.py not: a `git commit -am` ships the first
    without the second. The build must refuse by NAME, before copying anything, rather than
    skip the file and fail later in the import probe with advice about the wrong list."""
    import sys
    import types

    import build_web

    # Should the check ever be skipped, stop at the next step with a message that does not
    # match: no registry is written, no probe interpreter starts, sys.path is restored.
    stub = types.ModuleType("api._export_builders")

    def got_past(*a, **k):
        raise AssertionError("got past the entry check")
    stub.write_public_registry = got_past
    monkeypatch.setitem(sys.modules, "api._export_builders", stub)
    monkeypatch.setattr(sys, "path", list(sys.path))

    app = tmp_path / "app"
    (app / "api").mkdir(parents=True)
    for name in build_web._FUNCTION_ENTRY:
        if name != "hot_leads.py":
            (app / "api" / name).write_text("", encoding="utf-8")
    out = tmp_path / "site"
    out.mkdir()
    with pytest.raises(RuntimeError, match=r"api/hot_leads\.py is listed in _FUNCTION_ENTRY"):
        build_web._bundle_research_function(app, out)
    assert not (out / "api").exists(), "refused before anything was copied"
