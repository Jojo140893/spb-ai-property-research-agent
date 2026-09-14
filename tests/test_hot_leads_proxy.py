"""GET /api/hot_leads -- the Hot Leads tab's server-side read of Agent 1's hot leads.

The hook that stores hot leads answers only a caller holding SPB_SERVICE_KEY, and a hot
lead is a person's name, phone number, email and appointment. So the proxy has two jobs
that fail silently if they are wrong: it must send the key upstream and NEVER send it
anywhere else (the page, a log, an error message, a redirect target), and it must make a
failed read look like a failure rather than like an empty list of leads.

NOTHING HERE TOUCHES THE NETWORK. urllib.request.urlopen is stubbed in every test, and an
autouse fixture makes any real socket connection raise: a proxy test that quietly reached
the real hook would pass while proving nothing, and would spend the real key doing it.
"""
import email.message
import io
import json
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


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """No network, and no key or URL leaking in from the shell that runs the suite."""
    def refuse(*a, **k):
        raise AssertionError("a test tried to open a real network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.delenv("SPB_SERVICE_KEY", raising=False)
    monkeypatch.delenv("SPB_HOT_LEADS_URL", raising=False)


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


def _call(method, path="/api/hot_leads"):
    """Drive the handler as Vercel does, with no socket: (status, headers, body)."""
    h = H.handler.__new__(H.handler)
    h.command, h.path, h.request_version = method, path, "HTTP/1.1"
    h.requestline = f"{method} {path} HTTP/1.1"
    h.client_address = ("127.0.0.1", 0)
    h.headers = email.message.Message()
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

def test_success_passes_the_hooks_json_through_byte_for_byte(monkeypatch):
    monkeypatch.setenv("SPB_SERVICE_KEY", KEY)
    up, raw = _ok(monkeypatch)
    status, headers, body = _call("GET")
    assert status == 200
    assert body == raw, "the tab and the hook share one format; nothing is rewritten here"
    assert headers["content-type"] == "application/json"
    assert headers["cache-control"] == "no-store"
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
    assert '"api/hot_leads.py": {"memory": 256, "maxDuration": 30}' in src


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
