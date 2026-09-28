"""POST /api/contact_unlock -- the second password that shows a Clare caller's full details.

Coleen, 28 Sep 2026: "Not everyone on the team should have access to the customer's full
email address or phone number." The owner chose the rule: whoever knows SPB_CONTACT_PASSWORD.
Each rule here is one a careless change would break silently: a wrong password answered 401
sends Coleen to the login page as though her session had ended; a missing delay makes the
password guessable at the speed of the network; an unset password that validated would make
the masking decoration.

Nothing here touches the network (the endpoint makes no upstream call, and an autouse fixture
refuses any socket), and the 0.7 s delay is recorded rather than slept.
"""
import email.message
import io
import json
import socket
import time

import pytest

from api import contact_unlock as U
from api import hot_leads as H

PASSWORD = "contact-test-password-not-the-real-one"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a test tried to open a real network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    for name in ("SPB_CONTACT_PASSWORD", "SPB_SERVICE_KEY", "SPB_HOT_LEADS_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def slept(monkeypatch):
    """Every time.sleep the endpoint asks for, recorded instead of waited."""
    calls = []
    monkeypatch.setattr(U.time, "sleep", calls.append)
    return calls


def _call(method, body=None, raw=None, cookie=None, length=None):
    """Drive the handler as Vercel does, with no socket: (status, headers, set_cookies, body)."""
    data = raw if raw is not None else (b"" if body is None else json.dumps(body).encode())
    h = U.handler.__new__(U.handler)
    h.command, h.path, h.request_version = method, "/api/contact_unlock", "HTTP/1.1"
    h.requestline = f"{method} /api/contact_unlock HTTP/1.1"
    h.client_address = ("127.0.0.1", 0)
    h.headers = email.message.Message()
    h.headers["Content-Type"] = "application/json"
    h.headers["Content-Length"] = str(len(data) if length is None else length)
    if cookie:
        h.headers["Cookie"] = cookie
    h.rfile, h.wfile = io.BytesIO(data), io.BytesIO()
    getattr(h, "do_" + method)()
    head, _, out = h.wfile.getvalue().partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    pairs = [line.split(":", 1) for line in lines[1:] if ":" in line]
    headers = {k.strip().lower(): v.strip() for k, v in pairs}
    cookies = [v.strip() for k, v in pairs if k.strip().lower() == "set-cookie"]
    return int(lines[0].split()[1]), headers, cookies, out


def test_the_right_password_sets_a_signed_four_hour_cookie_for_the_api_only(monkeypatch, slept):
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", PASSWORD)
    status, headers, cookies, body = _call("POST", {"password": PASSWORD})
    assert status == 200
    assert json.loads(body) == {"status": "success", "contact": "full"}
    assert len(cookies) == 1
    cookie = cookies[0]
    attrs = [a.strip() for a in cookie.split(";")]
    assert attrs[0].startswith("spb_contact=")
    assert set(attrs[1:]) == {"Path=/api", "HttpOnly", "Secure", "SameSite=Strict",
                              "Max-Age=14400"}
    exp, sig = attrs[0].split("=", 1)[1].split(".")
    assert abs(int(exp) - (time.time() + 14400)) < 5
    assert sig == U._contact.sign(PASSWORD, exp)
    assert PASSWORD not in cookie
    assert U._contact.authorised(attrs[0])
    assert headers["cache-control"] == "no-store"
    assert slept == [], "a right password is not slowed down"


def test_the_cookie_it_sets_opens_the_full_view_of_the_leads(monkeypatch, slept):
    """End to end across the two functions: unlock, then read the leads with that cookie."""
    import urllib.request

    monkeypatch.setenv("SPB_CONTACT_PASSWORD", PASSWORD)
    monkeypatch.setenv("SPB_SERVICE_KEY", "svc-test-key")
    leads = {"status": "success", "count": 1, "leads": [{
        "callId": "c1", "customer": {"name": "N", "email": "n@example.com",
                                     "phone": "+61 400 111 678"}}]}

    class Resp(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: Resp(json.dumps(leads).encode()))
    cookie = _call("POST", {"password": PASSWORD})[2][0].split(";")[0]

    def read(c):
        h = H.handler.__new__(H.handler)
        h.command, h.path, h.request_version = "GET", "/api/hot_leads", "HTTP/1.1"
        h.requestline, h.client_address = "GET /api/hot_leads HTTP/1.1", ("127.0.0.1", 0)
        h.headers = email.message.Message()
        if c:
            h.headers["Cookie"] = c
        h.rfile, h.wfile = io.BytesIO(), io.BytesIO()
        h.do_GET()
        return json.loads(h.wfile.getvalue().partition(b"\r\n\r\n")[2])
    assert read(cookie)["leads"][0]["customer"]["phone"] == "+61 400 111 678"
    masked = read(None)["leads"][0]["customer"]
    assert masked["phone"] == "" and masked["phoneLast3"] == "678"


def test_the_password_is_trimmed_like_the_setting(monkeypatch, slept):
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", f"  {PASSWORD}\n")
    status, _, cookies, _ = _call("POST", {"password": f"{PASSWORD} "})
    assert status == 200 and cookies


@pytest.mark.parametrize("body", [
    {"password": "wrong"},
    {"password": PASSWORD[:-1]},
    {"password": PASSWORD + "x"},
    {"password": PASSWORD.upper()},
    {"password": ""},
    {"password": None},
    {"password": 12345},
    {"password": [PASSWORD]},
    {},
    {"lock": "true", "password": "wrong"},       # only lock: true (the boolean) locks
])
def test_a_wrong_or_missing_password_is_a_403_after_the_delay(monkeypatch, slept, body):
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", PASSWORD)
    status, headers, cookies, out = _call("POST", body)
    assert status == 403
    assert json.loads(out) == {"status": "error",
                               "message": "That contact-details password is not right."}
    assert cookies == [], "a wrong password sets nothing"
    assert slept == [0.7]
    assert headers["cache-control"] == "no-store"


@pytest.mark.parametrize("blank", [None, "", "   ", "\t\n"])
@pytest.mark.parametrize("given", ["", "   ", "anything"])
def test_with_no_password_set_nothing_unlocks(monkeypatch, slept, blank, given):
    """An empty password must never match an empty answer: that would unlock everyone."""
    if blank is not None:
        monkeypatch.setenv("SPB_CONTACT_PASSWORD", blank)
    status, _, cookies, out = _call("POST", {"password": given})
    assert status == 503
    assert json.loads(out) == {
        "status": "error",
        "message": ("Contact details are locked: no contact-details password is set on "
                    "this deployment.")}
    assert cookies == []


@pytest.mark.parametrize("configured", [True, False])
def test_lock_clears_the_cookie_at_the_same_path(monkeypatch, slept, configured):
    if configured:
        monkeypatch.setenv("SPB_CONTACT_PASSWORD", PASSWORD)
    status, _, cookies, out = _call("POST", {"lock": True})
    assert status == 200
    assert json.loads(out) == {"status": "success", "contact": "masked"}
    assert len(cookies) == 1
    attrs = [a.strip() for a in cookies[0].split(";")]
    assert attrs[0] == "spb_contact="
    assert {"Path=/api", "Max-Age=0", "HttpOnly", "Secure", "SameSite=Strict"} <= set(attrs[1:])
    assert slept == []


@pytest.mark.parametrize("raw,length,code", [
    (b"", 0, 400),
    (b"not json", None, 400),
    (b"[1, 2]", None, 400),
    (b'"password"', None, 400),
    (b"{}", "abc", 400),
    (b"x" * 5000, None, 413),
])
def test_a_request_it_does_not_take_is_refused_by_name(monkeypatch, slept, raw, length, code):
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", PASSWORD)
    status, _, cookies, out = _call("POST", raw=raw, length=length)
    assert status == code
    assert json.loads(out)["status"] == "error" and cookies == []


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "PATCH"])
def test_only_POST_is_served(monkeypatch, method):
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", PASSWORD)
    status, headers, cookies, _ = _call(method, {"password": PASSWORD})
    assert status == 405 and headers.get("allow") == "POST" and cookies == []


@pytest.mark.parametrize("configured", [True, False])
@pytest.mark.parametrize("body", [{"password": PASSWORD}, {"password": "wrong"}, {"lock": True},
                                  {}, None, "x"])
def test_no_answer_is_ever_a_401(monkeypatch, slept, configured, body):
    """The tab reads a 401 from /api/* as "your sign-in expired" and sends Coleen to /login."""
    if configured:
        monkeypatch.setenv("SPB_CONTACT_PASSWORD", PASSWORD)
    raw = b"" if body is None else json.dumps(body).encode()
    assert _call("POST", raw=raw)[0] != 401


def test_nothing_logged_carries_the_password(monkeypatch, capsys, slept):
    monkeypatch.setenv("SPB_CONTACT_PASSWORD", PASSWORD)
    _call("POST", {"password": PASSWORD})
    _call("POST", {"password": "wrong-guess-xyz"})
    out = capsys.readouterr()
    assert "[api/contact_unlock]" in out.err, "the capture saw the handler's log at all"
    assert PASSWORD not in out.err + out.out and "wrong-guess-xyz" not in out.err + out.out
