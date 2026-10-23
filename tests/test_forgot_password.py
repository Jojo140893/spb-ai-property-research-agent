"""Forgot password: the Python half, and the runner for the end-to-end suite.

Ahsan, 23 Oct 2026: "add a Forgot password: it sends a code to digital@smartpropertybuying.com.au
and she can reset the password". What is proved where:

  tests/forgot_password_checks.mjs   the REAL generated middleware.js and the REAL api/auth/*.py,
                                     together, against a fake Blob store, fake SMTP and a fake clock
                                     (tests/authkit/). Happy path, wrong codes, expiry, replay, rate
                                     limits, concurrency, outage, tampering, routes, origin, XSS,
                                     email, logs. test_the_end_to_end_suite_passes runs it.
  this file                          what is easier to see from Python: the hash is bound to its nonce,
                                     the code is compared in constant time, a conditional write is the
                                     only kind there is, the mail goes out the way production sends it,
                                     headers cannot be injected, no log line can carry a secret, and the
                                     build ships every file.
  tests/test_forgot_password_mutations.py
                                     removes one safeguard at a time and requires a test above to go red.

NOTHING HERE REACHES A REAL HOST. An autouse fixture makes any socket connection to a non-loopback
address fail the test, the Blob transport refuses any URL that is not a Blob store or vercel.com/api/
blob (and rewrites those to the local fake), and the mail goes to a server this process owns.
"""
import ast
import hashlib
import hmac
import http.client
import http.server
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tests.authkit.backend import (
    FAKE_SERVICE_KEY, FAKE_SITE_PASSWORD, FAKE_SMTP_PASSWORD, FAKE_SMTP_USER, FAKE_TOKEN, HOST, ORIGIN,
    Backend)

APP = Path(__file__).resolve().parent.parent
CHECKS = APP / "tests" / "forgot_password_checks.mjs"


@pytest.fixture(autouse=True)
def _loopback_only(monkeypatch):
    real = socket.socket.connect

    def guard(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError("a test tried to reach a real host: %r" % (address,))
        return real(self, address, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", guard)


@pytest.fixture
def world():
    b = Backend()
    b.configure()
    b.start(serve_functions=False)
    yield b
    b.close()


@pytest.fixture
def served():
    b = Backend()
    b.configure()
    b.start(serve_functions=True)
    yield b
    b.close()


def _issue(world):
    A = world.A
    store = A.default_store()
    assert A.forgot(store) == "sent"
    return store, world.code_in_mail()


def _other(code):
    return "000000" if code != "000000" else "111111"


# ------------------------------------------------------------------ the end-to-end suite

def test_the_end_to_end_suite_passes(tmp_path):
    """Run tests/forgot_password_checks.mjs against middleware.js exactly as the build emits it."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the edge middleware cannot be exercised here")
    import build_web

    mw = tmp_path / "middleware.mjs"
    mw.write_bytes(build_web.MIDDLEWARE_JS.encode("utf-8"))
    env = dict(os.environ, SPB_MW_PATH=str(mw), PYTHONIOENCODING="utf-8")
    env.pop("SPB_ONLY", None)
    p = subprocess.run([node, str(CHECKS)], cwd=str(APP), env=env, capture_output=True, text=True,
                       encoding="utf-8", timeout=900)
    fails = [ln for ln in p.stdout.splitlines() if ln.lstrip().startswith("FAIL")]
    assert p.returncode == 0, "the forgot-password suite failed:\n" + "\n".join(fails) + "\n" + p.stderr[-2000:]
    assert "ALL " in p.stdout and "CHECKS PASSED" in p.stdout


# ------------------------------------------------------------------ configuration

def test_configured_needs_the_whole_feature_and_clean_addresses(world):
    A = world.A
    assert A.configured()
    for label, change in {
        "no token": {"BLOB_READ_WRITE_TOKEN": None},
        "not a blob token": {"BLOB_READ_WRITE_TOKEN": "nope"},
        "no smtp password": {"SPB_SMTP_PASSWORD": None},
        "blank smtp password": {"SPB_SMTP_PASSWORD": "  "},
        "no service key": {"SPB_SERVICE_KEY": None},
        "reset address with a newline": {"SPB_RESET_TO": "a@b.com\nBcc: x@y.com"},
        "reset address that is not one": {"SPB_RESET_TO": "<b>@x"},
        "smtp user with a carriage return": {"SPB_SMTP_USER": "a@b.com\r\nBcc: x@y.com"},
    }.items():
        world.configure(**change)
        assert not A.configured(), label
    world.configure()
    assert A.configured()


def test_the_address_is_masked_to_its_first_letter():
    from tests.authkit.backend import load_auth
    A, _ = load_auth()
    assert A.mask_email("digital@smartpropertybuying.com.au") == "d***@smartpropertybuying.com.au"
    assert A.mask_email("a@b.co") == "a***@b.co"


def test_the_iteration_count_is_in_the_safe_band(world):
    A = world.A
    assert 100000 <= A.MIN_ITER <= A.PBKDF2_ITER <= A.MAX_ITER <= 1000000
    assert len(A.env_fp()) == 16 and re.fullmatch(r"[0-9a-f]{16}", A.env_fp())


# ------------------------------------------------------------------ the code and its hash

def test_the_code_hash_is_bound_to_its_nonce(world):
    """A stored hash is only good under the nonce it was made with, so a hash cannot be carried from one
    request to another and the same code issued twice looks different in the store."""
    A = world.A
    _store, code = _issue(world)
    rec = json.loads(world.blob.text(A.RESET_PATH))
    key = A._reset_key()
    assert A._matches(key, rec, code)
    assert not A._matches(key, dict(rec, nonce=A.b64url(b"some-other-nonce!")), code)
    assert A.code_mac(key, "nonce-one", code) != A.code_mac(key, "nonce-two", code)
    assert code not in json.dumps(rec) and rec["nonce"] and rec["code_mac"]


def test_the_code_hash_is_keyed_by_a_server_secret(world):
    """Someone holding a copy of the store, but not SPB_SERVICE_KEY, cannot test the million codes."""
    A = world.A
    _store, code = _issue(world)
    rec = json.loads(world.blob.text(A.RESET_PATH))
    unkeyed = A.b64url(hashlib.sha256(("reset-code|" + rec["nonce"] + "|" + code).encode()).digest())
    assert unkeyed != rec["code_mac"]
    world.configure(SPB_SERVICE_KEY="a-different-service-key")
    assert not A._matches(A._reset_key(), rec, code)


def test_the_code_is_compared_with_compare_digest(world, monkeypatch):
    """Not a timing measurement (those flake): proof that the one comparison that decides a code goes
    through hmac.compare_digest, with the stored value on one side."""
    A = world.A
    store, code = _issue(world)
    stored = json.loads(world.blob.text(A.RESET_PATH))["code_mac"].encode("ascii")
    seen = []
    real = hmac.compare_digest

    def spy(a, b):
        seen.append((bytes(a), bytes(b)))
        return real(a, b)

    monkeypatch.setattr(A.hmac, "compare_digest", spy)
    assert A.reset_password(_other(code), "a-perfectly-fine-password", store) == "code"
    assert any(stored in pair for pair in seen), "the wrong code was not compared with compare_digest"
    seen.clear()
    assert A.reset_password(code, "a-perfectly-fine-password", store) == "ok"
    assert any(stored in pair and pair[0] == pair[1] for pair in seen), \
        "the right code was not compared with compare_digest"


def test_the_stored_password_checks_use_compare_digest(world, monkeypatch):
    A = world.A
    store, code = _issue(world)
    assert A.reset_password(code, "a-perfectly-fine-password", store) == "ok"
    rec = json.loads(world.blob.text(A.PASSWORD_PATH))
    seen = []
    real = hmac.compare_digest
    monkeypatch.setattr(A.hmac, "compare_digest",
                        lambda a, b: (seen.append((bytes(a), bytes(b))), real(a, b))[1])
    assert A.valid_override(rec) is not None
    flat = [x for pair in seen for x in pair]
    assert rec["env_fp"].encode() in flat and rec["mac"].encode() in flat
    seen.clear()
    assert A.is_current_password(store, "a-perfectly-fine-password")
    assert rec["hash"].encode() in [x for pair in seen for x in pair]


# ------------------------------------------------------------------ the stored password

def _override(world, password="a-perfectly-fine-password"):
    A = world.A
    store, code = _issue(world)
    assert A.reset_password(code, password, store) == "ok"
    return store, json.loads(world.blob.text(A.PASSWORD_PATH))


def test_a_record_is_only_good_for_the_deployment_that_wrote_it(world):
    A = world.A
    _store, rec = _override(world)
    assert A.valid_override(rec) is not None
    world.configure(SITE_PASSWORD="the-owner-changed-it-in-vercel")
    assert A.valid_override(rec) is None, "a changed SITE_PASSWORD must retire the record"
    world.configure()
    assert A.valid_override(rec) is not None
    world.configure(SPB_SERVICE_KEY="rotated-service-key")
    assert A.valid_override(rec) is None, "a rotated SPB_SERVICE_KEY must retire the record"


def test_a_record_with_anything_changed_is_not_valid(world):
    A = world.A
    _store, rec = _override(world)
    for field, value in [("hash", A.b64url(b"x" * 32)), ("salt", A.b64url(b"y" * 16)), ("iter", 100001),
                         ("epoch", 2), ("updated_at", "2030-01-01T00:00:00Z"), ("mac", A.b64url(b"z" * 32)),
                         ("algo", "pbkdf2-sha1"), ("v", 2), ("v", True), ("iter", True), ("epoch", True),
                         ("iter", 50), ("iter", 10 ** 9)]:
        assert A.valid_override(dict(rec, **{field: value})) is None, (field, value)
    missing = dict(rec)
    del missing["mac"]
    assert A.valid_override(missing) is None
    assert A.valid_override("not a dict") is None and A.valid_override(None) is None


def test_the_mac_stops_a_writer_who_copied_the_real_env_fp(world):
    A = world.A
    _store, rec = _override(world)
    salt = os.urandom(16)
    evil = dict(rec, salt=A.b64url(salt), hash=A.b64url(A.pbkdf2("attacker-password-1", salt, rec["iter"])))
    assert A.valid_override(evil) is None


def test_the_password_is_stored_as_pbkdf2_and_never_as_itself(world):
    A = world.A
    password = "correct horse battery staple 9"
    _store, rec = _override(world, password)
    assert rec["algo"] == "pbkdf2-sha256" and rec["iter"] == A.PBKDF2_ITER
    salt = A.b64url_decode(rec["salt"])
    assert len(salt) == 16
    expect = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rec["iter"])
    assert A.b64url_decode(rec["hash"]) == expect
    assert password not in world.blob.text(A.PASSWORD_PATH) and password not in world.stderr_text()


def test_password_rules_name_the_rule_that_failed(world):
    A = world.A
    P = A.password_problem
    assert P("abcdefghij1", "abcdefghij1") and "12" in P("abcdefghij1", "abcdefghij1")
    assert P("a" * 129, "a" * 129) and "128" in P("a" * 129, "a" * 129)
    assert "space" in P(" abcdefghijkl1", " abcdefghijkl1")
    assert "space" in P("abcdefghijkl1 ", "abcdefghijkl1 ")
    assert "control" in P("abcdef\tghijkl1", "abcdef\tghijkl1")
    assert "email" in P(FAKE_SMTP_USER.upper(), FAKE_SMTP_USER.upper())
    assert "match" in P("abcdefghijkl1", "abcdefghijkl2")
    assert P("abcdefghijkl1", "abcdefghijkl1") is None
    assert P("a" * 128, "a" * 128) is None and P("a" * 12, "a" * 12) is None
    assert P(None, None) and P(12345678901234, 12345678901234)
    assert P("Café-au-lait-12", "Café-au-lait-12") is None


# ------------------------------------------------------------------ concurrency and failure order

def test_one_code_is_spent_once_even_in_threads(world):
    A = world.A
    store, code = _issue(world)
    results = []
    gate = threading.Barrier(8)

    def go(i):
        gate.wait()
        try:
            results.append(A.reset_password(code, "password-number-%d-long-enough" % i, store))
        except Exception as e:                                          # noqa: BLE001
            results.append(type(e).__name__)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 1, results
    assert set(results) <= {"ok", "code", "busy"}, results
    assert json.loads(world.blob.text(A.RESET_PATH))["state"] == "burned"


def test_a_wrong_guess_whose_count_cannot_be_stored_is_not_answered(world):
    """The decrement is written BEFORE the answer. If it cannot be, the caller learns nothing."""
    A = world.A
    store, code = _issue(world)
    world.blob.faults.append({"on": "write", "status": 500, "count": 1})
    with pytest.raises(A.StoreError):
        A.reset_password(_other(code), "a-perfectly-fine-password", store)
    assert json.loads(world.blob.text(A.RESET_PATH))["attempts_left"] == 5
    assert A.reset_password(_other(code), "a-perfectly-fine-password", store) == "code"
    assert json.loads(world.blob.text(A.RESET_PATH))["attempts_left"] == 4


def test_the_code_is_burned_before_the_password_is_written(world):
    """If storing the password fails after the burn, nothing has changed and the code is gone."""
    A = world.A
    store, code = _issue(world)
    world.blob.faults.append({"on": "write", "status": 500, "count": 1, "skip": 1})   # the 2nd write
    with pytest.raises(A.StoreError):
        A.reset_password(code, "a-perfectly-fine-password", store)
    assert world.blob.text(A.PASSWORD_PATH) is None
    assert json.loads(world.blob.text(A.RESET_PATH))["state"] == "burned"
    assert A.reset_password(code, "a-perfectly-fine-password", store) == "code"


def test_the_right_code_with_the_current_password_does_not_spend_the_code(world):
    A = world.A
    store, code = _issue(world)
    assert A.reset_password(code, FAKE_SITE_PASSWORD, store) == "same"
    assert json.loads(world.blob.text(A.RESET_PATH))["state"] == "open"
    assert A.reset_password(code, "a-different-password-123", store) == "ok"


def test_a_store_that_cannot_be_read_decides_nothing(world):
    A = world.A
    store, code = _issue(world)
    world.blob.mode = "outage"
    with pytest.raises(A.StoreError):
        A.reset_password(code, "a-perfectly-fine-password", store)
    world.blob.mode = "ok"
    assert json.loads(world.blob.text(A.RESET_PATH))["attempts_left"] == 5
    assert A.reset_password(code, "a-perfectly-fine-password", store) == "ok"


# ------------------------------------------------------------------ the Blob client

class _Script(object):
    """A transport that answers from a list and records what it was asked."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body, timeout))
        return self.answers.pop(0)


def _store(world, *answers):
    world.A.TRANSPORT = _Script(*answers)
    return world.A.BlobStore(FAKE_TOKEN), world.A.TRANSPORT


def test_the_client_refuses_an_unconditional_overwrite(world):
    s, _t = _store(world)
    with pytest.raises(ValueError):
        s.write("site-auth/x.json", "{}")
    with pytest.raises(ValueError):
        s.write("site-auth/x.json", "{}", if_match='"a"', create_only=True)


def test_the_client_maps_the_service_answers(world):
    A = world.A
    ok = json.dumps({"url": "https://fakestore0123.private.blob.vercel-storage.com/site-auth/x.json",
                     "pathname": "site-auth/x.json", "etag": '"e1"'}).encode()
    s, t = _store(world, (200, {}, ok), (412, {}, b"{}"), (400, {}, b"{}"), (409, {}, b"{}"), (403, {}, b"{}"),
                  (500, {}, b""), (400, {}, b"{}"))
    assert s.write("site-auth/x.json", "{}", if_match='"e0"') == '"e1"'
    method, url, headers, body, timeout = t.calls[0]
    assert method == "PUT" and url == "https://vercel.com/api/blob/?pathname=site-auth%2Fx.json"
    assert headers["x-if-match"] == '"e0"' and headers["x-allow-overwrite"] == "1"
    assert headers["x-vercel-blob-access"] == "private" and headers["x-add-random-suffix"] == "0"
    assert headers["x-vercel-blob-store-id"] == "fakestore0123" and headers["authorization"] == "Bearer " + FAKE_TOKEN
    assert timeout == A.BLOB_TIMEOUT == 8
    with pytest.raises(A.Conflict):
        s.write("site-auth/x.json", "{}", if_match='"e0"')                   # 412
    with pytest.raises(A.Conflict):
        s.write("site-auth/x.json", "{}", create_only=True)                  # 400: already exists
    with pytest.raises(A.Conflict):
        s.write("site-auth/x.json", "{}", create_only=True)                  # 409
    with pytest.raises(A.StoreError):
        s.write("site-auth/x.json", "{}", if_match='"e0"')                   # 403
    with pytest.raises(A.StoreError):
        s.write("site-auth/x.json", "{}", if_match='"e0"')                   # 500
    with pytest.raises(A.StoreError):
        s.write("site-auth/x.json", "{}", if_match='"e0"')                   # 400 on a conditional write is a real error


def test_the_client_refuses_a_blob_that_landed_somewhere_unexpected(world):
    A = world.A
    elsewhere = json.dumps({"url": "https://fakestore0123.private.blob.vercel-storage.com/site-auth/x-Ab12.json",
                            "pathname": "site-auth/x-Ab12.json", "etag": '"e1"'}).encode()
    public = json.dumps({"url": "https://fakestore0123.public.blob.vercel-storage.com/site-auth/x.json",
                         "pathname": "site-auth/x.json", "etag": '"e1"'}).encode()
    s, _t = _store(world, (200, {}, elsewhere), (200, {}, public), (200, {}, b"not json"))
    for _ in range(3):
        with pytest.raises(A.StoreError):
            s.write("site-auth/x.json", "{}", create_only=True)


def test_the_client_reads_404_as_nothing_and_everything_else_as_an_error(world):
    A = world.A
    s, t = _store(world, (404, {}, b"{}"), (200, {"etag": '"e1"'}, b'{"a": 1}'), (200, {}, b"{}"),
                  (500, {}, b""), (403, {}, b""), (302, {"location": "https://elsewhere.example/"}, b""),
                  (200, {"etag": '"e"'}, b"x" * (A.MAX_RECORD + 1)))
    assert s.read("site-auth/x.json") == (None, None)
    method, url, headers, _b, timeout = t.calls[0]
    assert method == "GET" and url == "https://fakestore0123.private.blob.vercel-storage.com/site-auth/x.json?cache=0"
    assert headers == {"authorization": "Bearer " + FAKE_TOKEN}
    assert s.read("site-auth/x.json") == ('{"a": 1}', '"e1"')
    for why in ("no ETag", "500", "403", "a redirect", "too large"):
        with pytest.raises(A.StoreError):
            s.read("site-auth/x.json")


def test_the_bearer_token_never_follows_a_redirect(world):
    """urllib follows a 302 and re-sends the headers. The transport must not, or a redirect (or a
    hijacked store host) would carry the token to someone else."""
    A = world.A
    hits = {"second": []}

    class Second(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits["second"].append(dict(self.headers))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    second = http.server.HTTPServer(("127.0.0.1", 0), Second)
    threading.Thread(target=second.serve_forever, daemon=True).start()
    target = "http://127.0.0.1:%d/stolen" % second.server_address[1]

    class First(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()

        def log_message(self, *a):
            pass

    first = http.server.HTTPServer(("127.0.0.1", 0), First)
    threading.Thread(target=first.serve_forever, daemon=True).start()
    try:
        status, _h, _b = A._urllib_transport("GET", "http://127.0.0.1:%d/x" % first.server_address[1],
                                             {"authorization": "Bearer " + FAKE_TOKEN}, None, 5)
    finally:
        first.shutdown()
        second.shutdown()
    assert status == 302 and hits["second"] == [], "the token followed a redirect"


# ------------------------------------------------------------------ the email

def test_the_mail_goes_out_by_smtp_ssl_to_gmail_with_a_15_second_timeout(world, monkeypatch):
    """Production wiring, with smtplib stubbed so nothing connects: SMTP_SSL, smtp.gmail.com:465, a
    verifying TLS context, 15 s, the configured login, one message, then quit."""
    A = world.A
    import smtplib
    A.SMTP_FACTORY = None
    made = []

    class Recorder(object):
        def __init__(self, host, port, local_hostname=None, timeout=None, context=None):
            made.append({"host": host, "port": port, "timeout": timeout, "context": context,
                         "ehlo": local_hostname, "calls": []})

        def login(self, user, password):
            made[-1]["calls"].append(("login", user, password))

        def send_message(self, msg):
            made[-1]["calls"].append(("send", msg["To"], msg["From"], msg["Subject"]))

        def quit(self):
            made[-1]["calls"].append(("quit",))

    monkeypatch.setattr(smtplib, "SMTP_SSL", Recorder)
    A.send_mail(A.SUBJECT_CODE, A.code_mail_body("123456"))
    m = made[0]
    assert (m["host"], m["port"], m["timeout"]) == ("smtp.gmail.com", 465, 15) and A.SMTP_TIMEOUT == 15
    assert m["ehlo"] == A.EHLO_NAME == "app.smartpropertybuying.com.au"     # no DNS lookup for our own name
    assert isinstance(m["context"], ssl.SSLContext) and m["context"].verify_mode == ssl.CERT_REQUIRED
    assert m["context"].check_hostname is True
    assert m["calls"][0] == ("login", FAKE_SMTP_USER, FAKE_SMTP_PASSWORD)
    assert m["calls"][1] == ("send", FAKE_SMTP_USER, FAKE_SMTP_USER, "Smart Property Buying dashboard password code")
    assert m["calls"][-1] == ("quit",)


def test_a_stalled_mail_server_is_cut_off_by_the_timeout(world):
    A = world.A
    world.smtp.delay = 1.5
    A.SMTP_TIMEOUT = 0.3
    store = A.default_store()
    t0 = time.time()
    assert A.forgot(store) == "failed"
    assert time.time() - t0 < 1.4
    assert re.search(r"forgot: mail failed \(", world.stderr_text())
    assert world.mailbox() == []


def test_header_injection_through_the_settings_is_refused_twice(world, monkeypatch):
    A = world.A
    world.configure(SPB_RESET_TO="a@b.com\r\nBcc: evil@example.org")
    assert not A.configured()
    with pytest.raises(ValueError):
        A.build_message("Subject", "body")
    # Second line of defence: even if the address check were loosened, the message class itself
    # refuses a line break in a header value.
    monkeypatch.setattr(A, "_EMAIL_RE", re.compile(r"(?s).*"))
    with pytest.raises(ValueError):
        A.build_message("Subject", "body")
    assert world.mailbox() == []


def test_the_message_has_nothing_from_a_request_in_it(world):
    A = world.A
    msg = A.build_message(A.SUBJECT_CODE, A.code_mail_body("654321"))
    assert sorted(msg.keys()) == ["Content-Transfer-Encoding", "Content-Type", "Date", "From", "MIME-Version",
                                  "Subject", "To"]
    assert msg["To"] == "digital@smartpropertybuying.com.au" and msg["From"] == FAKE_SMTP_USER
    assert msg.get_content_type() == "text/plain" and not msg.is_multipart()


# ------------------------------------------------------------------ same-origin

def test_same_origin_table(world):
    A = world.A
    good = {"Origin": ORIGIN, "Host": HOST}
    assert A.same_origin(good)
    assert A.same_origin({"Origin": ORIGIN, "Host": HOST, "Sec-Fetch-Site": "same-origin"})
    assert A.same_origin({"Origin": ORIGIN, "Host": "x.vercel.app", "X-Forwarded-Host": HOST + ", other"})
    assert A.same_origin({"Origin": ORIGIN.upper().replace("HTTPS", "https"), "Host": HOST})
    for label, h in {
        "no origin": {"Host": HOST},
        "empty": {"Origin": "", "Host": HOST},
        "null": {"Origin": "null", "Host": HOST},
        "foreign": {"Origin": "https://evil.example", "Host": HOST},
        "http": {"Origin": "http://" + HOST, "Host": HOST},
        "port": {"Origin": ORIGIN + ":8443", "Host": HOST},
        "suffix": {"Origin": ORIGIN + ".evil.example", "Host": HOST},
        "userinfo": {"Origin": "https://" + HOST + "@evil.example", "Host": HOST},
        "path": {"Origin": ORIGIN + "/x", "Host": HOST},
        "query": {"Origin": ORIGIN + "?x", "Host": HOST},
        "cross-site": {"Origin": ORIGIN, "Host": HOST, "Sec-Fetch-Site": "cross-site"},
        "same-site": {"Origin": ORIGIN, "Host": HOST, "Sec-Fetch-Site": "same-site"},
        "no host": {"Origin": ORIGIN},
        "scheme only": {"Origin": "https://"},
    }.items():
        assert not A.same_origin(h), label


def _post(world, which, body, headers=None, raw=None):
    c = http.client.HTTPConnection("127.0.0.1", world.ports[which], timeout=10)
    h = {"Origin": ORIGIN, "Host": HOST, "Content-Type": "application/json"}
    h.update(headers or {})
    data = raw if raw is not None else json.dumps(body).encode()
    c.request("POST", "/api/auth/" + which, data, {k: v for k, v in h.items() if v is not None})
    r = c.getresponse()
    out = (r.status, dict((k.lower(), v) for k, v in r.getheaders()), r.read())
    c.close()
    return out


def test_the_function_checks_the_origin_itself(served):
    """The middleware lets the POST through; the function is where cross-site requests stop."""
    b = served
    before = len(b.blob.requests())
    for which, body in (("forgot", {}), ("reset", {"code": "123456", "password": "a" * 14, "confirm": "a" * 14})):
        status, _h, _b = _post(b, which, body, {"Origin": "https://evil.example"})
        assert status == 403
        status, _h, _b = _post(b, which, body, {"Origin": None})
        assert status == 403
    assert len(b.blob.requests()) == before and b.mailbox() == []
    status, headers, body = _post(b, "forgot", {})
    assert status == 200 and json.loads(body)["ok"] is True
    assert headers["cache-control"] == "no-store" and headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["content-type"].startswith("application/json")


def test_the_functions_answer_only_post(served):
    for which in ("forgot", "reset"):
        for method in ("GET", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            c = http.client.HTTPConnection("127.0.0.1", served.ports[which], timeout=10)
            c.request(method, "/api/auth/" + which)
            r = c.getresponse()
            r.read()
            assert r.status == 405 and r.getheader("Allow") == "POST", (which, method, r.status)
            c.close()


def test_forgot_with_no_body_or_with_an_email_is_refused(served):
    b = served
    assert _post(b, "forgot", None, raw=b"")[0] == 400
    assert _post(b, "forgot", {"email": "attacker@example.org"})[0] == 400
    assert _post(b, "forgot", None, raw=b"[]")[0] == 400
    assert b.mailbox() == []


def test_chunked_and_length_less_bodies_are_refused(served):
    b = served
    c = http.client.HTTPConnection("127.0.0.1", b.ports["reset"], timeout=10)
    c.putrequest("POST", "/api/auth/reset", skip_host=True, skip_accept_encoding=True)
    for k, v in (("Origin", ORIGIN), ("Host", HOST), ("Content-Type", "application/json"),
                 ("Transfer-Encoding", "chunked")):
        c.putheader(k, v)
    c.endheaders()
    c.send(b"2\r\n{}\r\n0\r\n\r\n")
    r = c.getresponse()
    assert r.status == 411
    r.read()
    c.close()


# ------------------------------------------------------------------ the log

def _module_source(name):
    """The source of api/<name>. SPB_SOURCE_DIR lets the mutation test show these scans can fail by
    pointing them at a copy with one line changed."""
    override = os.environ.get("SPB_SOURCE_DIR")
    if override and (Path(override) / name).is_file():
        return (Path(override) / name).read_text(encoding="utf-8")
    return (APP / "api" / name).read_text(encoding="utf-8")


def test_no_log_line_can_carry_a_secret():
    """Every log() call is a fixed sentence, optionally with the CLASS NAME of an exception. A new line
    that formats a code, a password, a token or an exception message would fail here."""
    src = _module_source("_spb_auth.py")
    tree = ast.parse(src)
    allowed = (re.compile(r'^log\("[^"%{}]*"\)$'),
               re.compile(r'^log\("[^"%{}]*\(%s\)" % _why\(e\)\)$'),
               re.compile(r'^log\("reset: wrong code" \+ \(", code burned" if burned else ""\)\)$'))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "log"]
    assert len(calls) >= 12
    for call in calls:
        text = ast.get_source_segment(src, call)
        assert any(r.match(text) for r in allowed), "a log call that is not a fixed sentence: " + text


def test_nothing_prints_and_only_log_writes_to_stderr():
    for name in ("_spb_auth.py", "auth/forgot.py", "auth/reset.py"):
        src = _module_source(name)
        assert "print(" not in src, name
        assert "console." not in src
    src = _module_source("_spb_auth.py")
    writes = [ln for ln in src.splitlines() if "stderr.write" in ln]
    assert len(writes) == 2, writes                        # log() and log_message()
    assert "self.headers" not in "".join(writes) and "rfile" not in "".join(writes)


def test_exceptions_are_logged_by_class_name_only(world):
    A = world.A
    class Loud(Exception):
        smtp_code = 535

    assert A._why(Loud("password hunter2 is wrong")) == "Loud 535"
    assert "hunter2" not in A._why(Loud("password hunter2 is wrong"))
    assert A._why(ValueError("x")) == "ValueError"


# ------------------------------------------------------------------ the build ships it

def test_every_function_under_api_is_bundled_and_configured():
    """api/auth/ is new: the existing guard globs api/*.py and would not see it. A function file not
    in _FUNCTION_ENTRY is never copied, and its route 404s on a site that otherwise looks healthy."""
    import build_web

    src = (APP / "build_web.py").read_text(encoding="utf-8")
    nested = sorted(p.relative_to(APP / "api").as_posix() for p in (APP / "api").rglob("*.py")
                    if not p.name.startswith("_") and p.parent != APP / "api")
    assert nested == ["auth/forgot.py", "auth/reset.py"], nested
    for rel in nested:
        assert rel in build_web._FUNCTION_ENTRY, rel
        assert '"api/%s":' % rel in src, "no entry in the vercel.json functions map: " + rel
        entry = src[src.index('"api/%s":' % rel):]
        assert "api/_spb_auth.py" in entry[:entry.index("}") + 1], rel + " is deployed without api/_spb_auth.py"
    assert "_spb_auth.py" in build_web._FUNCTION_ENTRY
    assert "import importlib.util, _spb_auth" in build_web._READONLY_PROBE
    missing = [n for n in build_web._FUNCTION_ENTRY if not (APP / "api" / n).is_file()]
    assert not missing, missing


def test_the_headers_are_set_a_second_time_for_the_four_exact_paths():
    src = (APP / "build_web.py").read_text(encoding="utf-8")
    block = src[src.index("# Forgot password, 23 Oct 2026. The two pages"):]
    block = block[:block.index("        ],")]
    names = re.search(r'for name in \(([^)]*)\)\]', block).group(1)
    assert [n.strip().strip('"') for n in names.split(",")] == ["forgot", "reset", "api/auth/forgot", "api/auth/reset"]
    assert '"source": "/%s" % name' in block, "exact paths, no wildcard"
    assert '"Referrer-Policy", "value": "no-referrer"' in block and '"Cache-Control", "value": "no-store"' in block


def test_the_bundle_contains_the_functions_and_they_import_in_a_clean_interpreter(tmp_path):
    """build_web's own probe imports the built bundle on a read-only copy, the way Vercel will."""
    import build_web

    build_web._bundle_research_function(APP, tmp_path)
    for rel in ("api/_spb_auth.py", "api/auth/forgot.py", "api/auth/reset.py"):
        assert (tmp_path / rel).is_file(), rel + " did not reach the bundle"
    probe = build_web._READONLY_PROBE
    assert "import importlib.util, _spb_auth" in probe and "'forgot', 'reset'" in probe


def test_the_middleware_the_build_writes_has_the_new_routes_and_still_gates_everything(tmp_path):
    import build_web

    build_web._write_auth_middleware(tmp_path)
    body = (tmp_path / "middleware.js").read_text(encoding="utf-8")
    assert 'matcher: "/(.*)"' in body
    for needle in ('request.method === "GET" && path === "/forgot"', 'request.method === "GET" && path === "/reset"',
                   '(path === "/api/auth/forgot" || path === "/api/auth/reset")', 'href="/forgot"'):
        assert needle in body, needle
    assert "startsWith(\"/forgot\")" not in body and "startsWith(\"/reset\")" not in body
    assert "startsWith(\"/api/auth\")" not in body
    # The two Python endpoints must not be reachable by any method but POST.
    assert body.count('path === "/api/auth/forgot"') == 1 and body.count('path === "/api/auth/reset"') == 1
