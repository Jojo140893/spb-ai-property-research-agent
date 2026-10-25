"""Forgot password: the Python half, the hand-off to GitHub, and the runner for the middleware suite.

Colin, 23 Oct 2026: people have left his team and he wants to change the dashboard password himself.
What is proved where:

  this file                          the code (stateless, 50 bits, 15-20 minutes, bound to the live
                                     password), the encrypted record, the email, the GitHub hand-off and
                                     the run it starts (refused over billing, stopped, queued; no answer
                                     promises "saved" or a time), the one deadline (real clock), the
                                     workflow's daily cap (only runs that stored a password), the limits
                                     (per client and one email per window, so a stranger cannot block
                                     Coleen), the request checks, /api/reset_check, the workflow file and
                                     its first step run under bash, and that no log line carries a secret.
  tests/forgot_password_checks.mjs   the REAL generated middleware.js against records made by the REAL
                                     reset flow below: who signs in, which cookies survive, what a bad
                                     or re-keyed record does, and that sign-in never touches the network.
                                     test_the_middleware_signs_in_against_the_flows_records runs it.
  tests/test_forgot_password_mutations.py
                                     removes one safeguard at a time and requires a check here to go red.

NOTHING HERE REACHES A REAL HOST. An autouse fixture makes any socket connection to a non-loopback
address fail the test; GitHub is a recording fake behind the TRANSPORT seam; the mail goes to a local
SMTP server (tests/authkit/fake_smtp.py) through the SMTP_FACTORY seam.
"""
import email
import importlib
import json
import os
import re
import shutil
import smtplib
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from tests.authkit.fake_smtp import FakeSMTP

APP = Path(__file__).resolve().parent.parent
API = APP / "api"
CHECKS = APP / "tests" / "forgot_password_checks.mjs"
WORKFLOW = APP / ".github" / "workflows" / "apply-dashboard-password.yml"
# /login exactly as origin/main e12236b served it (before this feature), with SITE_PASSWORD set and
# no error: with the feature off, the page must still be byte-for-byte this.
LOGIN_OFF_GOLDEN = APP / "tests" / "fixtures" / "login_page_before_reset_feature.html"

FAKE_SITE = "fake-site-password-not-the-real-one"
FAKE_SECRET = "fake-auth-secret-not-the-real-one-0123456789"
OTHER_SECRET = "another-auth-secret-also-long-enough-987654"
FAKE_SMTP_PASSWORD = "fake-app-password-not-real"
FAKE_TOKEN = "fake-dispatch-token-not-real"
USER = "digital@smartpropertybuying.com.au"
HOST = "app.smartpropertybuying.com.au"
ORIGIN = "https://" + HOST
ENV_KEYS = ("SITE_PASSWORD", "SITE_USERNAME", "SPB_AUTH_SECRET", "SPB_SMTP_PASSWORD", "SPB_SMTP_USER",
            "GITHUB_DISPATCH_TOKEN", "SPB_PW_RECORD", "SPB_RESET_TO", "SPB_SITE_URL")


@pytest.fixture(autouse=True)
def _loopback_only(monkeypatch):
    real = socket.socket.connect

    def guard(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError("a test tried to reach a real host: %r" % (address,))
        return real(self, address, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", guard)


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


# What GitHub's API shows for the run a dispatch created, by what happened to it. "refused" is exactly
# what it showed on 30 Sep (run 36666518741) and 25 Oct 2026 (run 40172642421) while billing was failing:
# the dispatch got its 204, the run completed with conclusion failure in about 5 seconds, and its job ran
# 0 steps ("The job was not started because recent account payments have failed").
_STEP = lambda name, status, conclusion: {"name": name, "status": status, "conclusion": conclusion}
_CHECK = "Refuse an old, malformed or one-too-many record"
_SET_UP = _STEP("Set up job", "completed", "success")
RUN_KINDS = {
    # Running, and its check step has not answered yet.
    "started": ({"status": "in_progress", "conclusion": None},
                {"status": "in_progress", "conclusion": None, "steps": [
                    _SET_UP, _STEP(_CHECK, "in_progress", None)]}),
    # Its check step passed and it is waiting for Vercel: nothing stored yet.
    "past the gate": ({"status": "in_progress", "conclusion": None},
                      {"status": "in_progress", "conclusion": None, "steps": [
                          _SET_UP, _STEP(_CHECK, "completed", "success"),
                          _STEP("Install the Vercel CLI", "completed", "success"),
                          _STEP("Wait for any production deploy in progress", "in_progress", None)]}),
    "applied": ({"status": "completed", "conclusion": "success"},
                {"status": "completed", "conclusion": "success", "steps": [
                    _SET_UP, _STEP(_CHECK, "completed", "success"),
                    _STEP("Store it in Vercel", "completed", "success"),
                    _STEP("Redeploy production so it takes effect", "completed", "success"),
                    _STEP("Wait until the new password signs people in", "completed", "success")]}),
    # The check step refused it (the daily cap, a forged record, /api/reset_check not answering); the job
    # has not finished tidying up yet.
    "gate failed, still running": ({"status": "in_progress", "conclusion": None},
                                   {"status": "in_progress", "conclusion": None, "steps": [
                                       _SET_UP, _STEP(_CHECK, "completed", "failure")]}),
    # The dispatch token can cancel any run (second review, 25 Oct 2026).
    "cancelled after start": ({"status": "completed", "conclusion": "cancelled"},
                              {"status": "completed", "conclusion": "cancelled", "steps": [
                                  _SET_UP, _STEP(_CHECK, "completed", "cancelled")]}),
    # Dispatched on another ref: the job's `if:` skips it. Shown both ways GitHub might conclude it.
    "skipped": ({"status": "completed", "conclusion": "skipped"},
                {"status": "completed", "conclusion": "skipped", "steps": []}),
    "skipped, run says success": ({"status": "completed", "conclusion": "success"},
                                  {"status": "completed", "conclusion": "skipped", "steps": []}),
    # Stored and failed on attempt 1, then someone pressed Re-run and the check refused attempt 2.
    "re-run after a store": ({"status": "completed", "conclusion": "failure"}, [
        {"status": "completed", "conclusion": "failure", "steps": [
            _SET_UP, _STEP(_CHECK, "completed", "success"), _STEP("Store it in Vercel", "completed", "success"),
            _STEP("Redeploy production so it takes effect", "completed", "failure")]},
        {"status": "completed", "conclusion": "failure", "steps": [
            _SET_UP, _STEP(_CHECK, "completed", "failure"), _STEP("Store it in Vercel", "completed", "skipped")]}]),
    "refused": ({"status": "completed", "conclusion": "failure"},
                {"status": "completed", "conclusion": "failure", "steps": []}),
    "stopped": ({"status": "completed", "conclusion": "failure"},
                {"status": "completed", "conclusion": "failure", "steps": [
                    _STEP("Set up job", "completed", "success"),
                    _STEP("Refuse an old, malformed or one-too-many record", "completed", "failure"),
                    _STEP("Store it in Vercel", "completed", "skipped")]}),
    "stored, then failed": ({"status": "completed", "conclusion": "failure"},
                            {"status": "completed", "conclusion": "failure", "steps": [
                                _STEP("Set up job", "completed", "success"),
                                _STEP("Store it in Vercel", "completed", "success"),
                                _STEP("Redeploy production so it takes effect", "completed", "failure")]}),
    "queued": ({"status": "queued", "conclusion": None}, {"status": "queued", "conclusion": None, "steps": []}),
    # A newer pending run in the concurrency group cancels an older pending one: not a billing refusal.
    "cancelled": ({"status": "completed", "conclusion": "cancelled"},
                  {"status": "completed", "conclusion": "cancelled", "steps": []}),
}


class FakeGitHub(object):
    """Stands in for api.github.com. Every dispatch is recorded in `calls` and answered `status` (204 =
    accepted); every read in `reads`. An accepted dispatch creates a run, listed newest first, created at
    the dispatch's time, whose state is `run` (a RUN_KINDS key), or "unreadable" (reads answer 403).
    `run` may also be a list of kinds: the state the run shows at each listing in turn, the last one
    staying, so a run can move from queued to started to refused as a real one does.

    The runs listing honours `created=>=`, `per_page` and `page`; the jobs listing shows the latest
    attempt unless asked for `filter=all`, as GitHub's does."""

    def __init__(self, clock):
        self.calls, self.reads, self.runs = [], [], []
        self.status = 204
        self.run = "past the gate"
        self.clock = clock

    def add_run(self, kind, created, run_id=None):
        seq = list(kind) if isinstance(kind, (list, tuple)) else [kind]
        run = {"id": run_id if run_id is not None else 90000 + len(self.runs), "created_at": _iso(created),
               "event": "workflow_dispatch", "seq": seq, "reads": 0, "kind": seq[0]}
        self.runs.insert(0, run)
        self.runs.sort(key=lambda r: r["created_at"], reverse=True)     # stable: newest first
        return run

    def __call__(self, method, url, headers, body, timeout):
        if method == "POST":
            self.calls.append({"method": method, "url": url, "headers": dict(headers),
                               "body": json.loads(body.decode("utf-8")) if body else None})
            if self.status == 204:
                self.add_run(self.run, self.clock(), run_id=4000 + len(self.calls))
            return self.status, b""
        self.reads.append({"method": method, "url": url, "headers": dict(headers), "timeout": timeout})
        if self.run == "unreadable":
            return 403, b""
        path, _, query = url.partition("?")
        q = urllib.parse.parse_qs(query)
        if path.endswith("/actions/workflows/apply-dashboard-password.yml/runs"):
            floor = q.get("created", [""])[0]
            assert floor == "" or floor.startswith(">="), floor
            rows = [r for r in self.runs if r["created_at"] >= floor[2:]]
            per_page, page = int(q.get("per_page", ["30"])[0]), int(q.get("page", ["1"])[0])
            shown = rows[(page - 1) * per_page:page * per_page]
            for r in shown:
                r["kind"] = r["seq"][min(r["reads"], len(r["seq"]) - 1)]
                r["reads"] += 1
            shown = [dict(RUN_KINDS[r["kind"]][0], id=r["id"], created_at=r["created_at"], event=r["event"])
                     for r in shown if r["kind"] in RUN_KINDS]
            return 200, json.dumps({"total_count": len(rows), "workflow_runs": shown}).encode("utf-8")
        m = re.search(r"/actions/runs/(\d+)/jobs$", path)
        run = next((r for r in self.runs if m and r["id"] == int(m.group(1))), None)
        if run is not None and run["kind"] in RUN_KINDS:
            jobs = RUN_KINDS[run["kind"]][1]
            jobs = [dict(j, name="apply") for j in (jobs if isinstance(jobs, list) else [jobs])]
            if q.get("filter", ["latest"])[0] != "all":
                jobs = jobs[-1:]
            return 200, json.dumps({"total_count": len(jobs), "jobs": jobs}).encode("utf-8")
        return 404, b""

    def records(self):
        return [c["body"]["inputs"]["record"] for c in self.calls]


class World(object):
    """The Python half wired to fakes: a clock that only moves when told, a recording GitHub, a real
    local SMTP server, and env vars set the way the owner's runbook sets them."""

    def __init__(self, monkeypatch):
        self.mp = monkeypatch
        sys.path.insert(0, str(API))
        self.A = importlib.import_module("_spb_auth")
        self.now = 1_800_000_000.0
        self.slept = []
        self.github = FakeGitHub(lambda: self.now)
        self.smtp = FakeSMTP("digital@smartpropertybuying.com.au", FAKE_SMTP_PASSWORD)
        self.ips = 0
        port = self.smtp.start()
        A = self.A
        monkeypatch.setattr(A, "clock", lambda: self.now)
        monkeypatch.setattr(A, "sleep", lambda s: self.slept.append(s))
        monkeypatch.setattr(A, "TRANSPORT", self.github)
        monkeypatch.setattr(A, "SMTP_FACTORY",
                            lambda host, p, timeout: smtplib.SMTP("127.0.0.1", port, timeout=timeout,
                                                                  local_hostname="test"))
        A.LIMITS.clear()
        self.configure()

    def configure(self, **changes):
        base = {"SITE_PASSWORD": FAKE_SITE, "SPB_AUTH_SECRET": FAKE_SECRET,
                "SPB_SMTP_PASSWORD": FAKE_SMTP_PASSWORD, "GITHUB_DISPATCH_TOKEN": FAKE_TOKEN}
        base.update(changes)
        for k in ENV_KEYS:
            self.mp.delenv(k, raising=False)
        for k, v in base.items():
            if v is not None:
                self.mp.setenv(k, v)

    def apply(self, record):
        """What the GitHub workflow plus the redeploy amount to: the record is now in the env."""
        self.mp.setenv("SPB_PW_RECORD", record)

    def advance(self, seconds):
        self.now += seconds

    def fresh_ip(self):
        self.ips += 1
        return "198.51.100.%d" % self.ips

    def mails(self):
        out = []
        for m in self.smtp.messages:
            msg = email.message_from_bytes(m["data"])
            out.append({"to": m["to"], "from": m["from"], "subject": msg["Subject"],
                        "From": msg["From"], "To": msg["To"],
                        "body": msg.get_payload(decode=True).decode().replace("\r\n", "\n")})
        return out

    def last_code(self):
        bodies = [m["body"] for m in self.mails() if m["subject"] == self.A.SUBJECT_CODE]
        assert bodies, "no code email arrived"
        return re.search(r"^    ([0-9A-Z]{5}-[0-9A-Z]{5})$", bodies[-1], re.M).group(1)

    def code_mails(self):
        return [m for m in self.mails() if m["subject"] == self.A.SUBJECT_CODE]

    def forgot(self, ip="203.0.113.7"):
        return self.A.forgot_endpoint({}, ip)

    def reset(self, code, password, confirm=None, ip="203.0.113.7"):
        return self.A.reset_endpoint({"code": code, "password": password,
                                      "confirm": password if confirm is None else confirm}, ip)

    def full_reset(self, password):
        """forgot -> code from the mailbox -> reset -> the record GitHub was handed."""
        self.advance(61)
        ip = self.fresh_ip()
        assert self.forgot(ip).status == 200
        r = self.reset(self.last_code(), password, ip=ip)
        assert r.status == 200, r.payload
        return self.github.records()[-1]

    def close(self):
        self.smtp.stop()
        if str(API) in sys.path:
            sys.path.remove(str(API))


@pytest.fixture
def world(monkeypatch):
    w = World(monkeypatch)
    yield w
    w.close()


# ------------------------------------------------------------------ configuration

def test_configured_needs_the_whole_feature_and_clean_addresses(world):
    A = world.A
    assert A.configured()
    for label, change in {
        "no auth secret": {"SPB_AUTH_SECRET": None},
        "auth secret too short": {"SPB_AUTH_SECRET": "x" * 31},
        "no smtp password": {"SPB_SMTP_PASSWORD": None},
        "blank smtp password": {"SPB_SMTP_PASSWORD": "  "},
        "no dispatch token": {"GITHUB_DISPATCH_TOKEN": None},
        "no SITE_PASSWORD": {"SITE_PASSWORD": None},
        "reset address with a newline": {"SPB_RESET_TO": "a@b.com\nBcc: x@y.com"},
        "reset address that is not one": {"SPB_RESET_TO": "<b>@x"},
        "smtp user with a carriage return": {"SPB_SMTP_USER": "a@b.com\r\nBcc: x@y.com"},
        "site url over http": {"SPB_SITE_URL": "http://app.smartpropertybuying.com.au"},
        "site url with a path": {"SPB_SITE_URL": "https://evil.example/x?"},
    }.items():
        world.configure(**change)
        assert not A.configured(), label
    world.configure(SPB_SITE_URL="https://staging.example.com", SPB_RESET_TO="colin@example.com")
    assert A.configured()


def test_the_client_ip_is_the_one_vercel_reports(world):
    A = world.A
    assert A.client_ip({"x-real-ip": "203.0.113.9", "x-forwarded-for": "198.51.100.1"}) == "203.0.113.9"
    assert A.client_ip({"x-forwarded-for": "198.51.100.1, 10.0.0.1"}) == "198.51.100.1"
    assert A.client_ip({"x-forwarded-for": "2001:db8::1"}) == "2001:db8::1"
    for junk in ({}, {"x-real-ip": "not an ip"}, {"x-real-ip": "a" * 60}):
        assert A.client_ip(junk) == "unknown"


# ------------------------------------------------------------------ the code

def test_the_code_is_ten_crockford_characters_and_reads_forgivingly(world):
    A = world.A
    code = A.code_for(123456, "site|abc")
    assert len(code) == 10 and all(c in A.CODE_ALPHABET for c in code)
    assert not set("ILOU") & set(A.CODE_ALPHABET)
    assert A.show_code(code) == code[:5] + "-" + code[5:]
    assert A.normalise_code(A.show_code(code).lower()) == code
    assert A.normalise_code(" " + code[:5] + " - " + code[5:] + " ") == code
    assert A.normalise_code("O1L1I00000") == "0111100000"          # the letters people mistake for digits
    for bad in ("", "ABCDE", "ABCDE-FGHJKM", "UUUUUUUUUU", "ABCDE_FGHJK", "ABCDE.FGHJK", None, 12345, "A" * 41):
        assert A.normalise_code(bad) is None, bad


def test_a_code_lives_fifteen_to_twenty_minutes(world):
    A = world.A
    vid = A.verifier_id(None)
    start = (int(world.now // A.BUCKET) + 1) * A.BUCKET      # the very start of a bucket: the longest life
    code = A.code_for(int(start // A.BUCKET), vid)
    for minutes in (0, 5, 10, 15, 19.9):
        assert A.code_ok(code, start + minutes * 60, vid), minutes
    assert not A.code_ok(code, start + 20 * 60, vid)
    end = start + A.BUCKET - 1                                 # the very end: the shortest life
    code = A.code_for(int(end // A.BUCKET), vid)
    assert A.code_ok(code, end + 15 * 60, vid)
    assert not A.code_ok(code, end + 15 * 60 + 2, vid)
    assert not A.code_ok(code, end - A.BUCKET, vid), "a code is not valid before it was made"


def test_a_code_is_compared_against_every_live_bucket_in_constant_time(world, monkeypatch):
    A = world.A
    seen = []
    real = A.hmac.compare_digest
    monkeypatch.setattr(A.hmac, "compare_digest", lambda a, b: seen.append(1) or real(a, b))
    vid = A.verifier_id(None)
    A.code_ok(A.code_for(int(world.now // A.BUCKET), vid), world.now, vid)    # matches the first bucket
    assert len(seen) == A.BUCKETS
    seen.clear()
    A.code_ok("0000000000", world.now, vid)
    assert len(seen) == A.BUCKETS


def test_a_code_belongs_to_the_live_password_and_the_secret(world):
    A = world.A
    b = int(world.now // A.BUCKET)
    code = A.code_for(b, A.verifier_id(None))
    world.configure(SITE_PASSWORD="owner-changed-it-in-vercel")
    assert A.code_for(b, A.verifier_id(None)) != code, "SITE_PASSWORD changed: old codes must die"
    world.configure(SPB_AUTH_SECRET=OTHER_SECRET)
    assert A.code_for(b, A.verifier_id(None)) != code, "a different secret makes different codes"


def test_after_a_reset_goes_live_the_old_code_is_dead(world):
    A = world.A
    world.advance(61)
    world.forgot()
    code = world.last_code()
    assert world.reset(code, "First-new-passphrase-1").status == 200
    world.apply(world.github.records()[-1])
    # Another instance, which never saw the code spent: only the binding to the live password stops it.
    A.LIMITS.clear()
    r = world.reset(code, "Second-new-passphrase-2")
    assert r.status == 400 and r.payload["error"] == "code_invalid"
    assert len(world.github.calls) == 1
    world.forgot()
    assert world.last_code() != code, "same 5 minutes, new password: a new code"


# ------------------------------------------------------------------ asking for a code

def test_forgot_emails_the_code_and_a_link_to_the_fixed_address_and_stores_nothing(world):
    A = world.A
    r = world.forgot()
    assert (r.status, r.payload) == (200, {"ok": True, "message": A.GENERIC_FORGOT})
    mails = world.mails()
    assert len(mails) == 1
    m = mails[0]
    assert m["to"] == [USER] and m["To"] == USER and m["From"] == USER and m["subject"] == A.SUBJECT_CODE
    code = A.normalise_code(world.last_code())
    assert "%s/reset#code=%s\n" % (A.DEFAULT_SITE_URL, code) in m["body"]
    assert A.code_ok(code, world.now, A.verifier_id(None))
    assert world.github.calls == [], "asking for a code must not reach GitHub"
    assert world.slept and abs(world.slept[-1] - A.MIN_FORGOT_SECONDS) < 0.5


def test_the_email_link_uses_the_configured_site_never_the_request(world):
    world.configure(SPB_SITE_URL="https://staging.example.com", SPB_RESET_TO="colin@example.com")
    world.forgot()
    m = world.mails()[-1]
    assert m["to"] == ["colin@example.com"]
    assert "https://staging.example.com/reset#code=" in m["body"]
    assert "app.smartpropertybuying.com.au" not in m["body"]


def test_forgot_is_limited_per_client_and_always_answers_the_same(world):
    A = world.A
    answers = set()
    for _ in range(2):
        answers.add(json.dumps(world.forgot("203.0.113.1").payload))
    assert len(world.mails()) == 1, "a second press inside a minute sends nothing"
    # Each in a new 5-minute window, so the one-email-per-window rule is not what refuses.
    world.advance(A.BUCKET)
    world.forgot("203.0.113.1")
    world.advance(A.BUCKET)
    world.forgot("203.0.113.1")
    assert len(world.mails()) == 3
    world.advance(A.BUCKET)
    answers.add(json.dumps(world.forgot("203.0.113.1").payload))
    assert len(world.mails()) == 3, "three an hour per client, then nothing"
    world.forgot("203.0.113.2")
    assert len(world.mails()) == 4, "another client is not held back by the first"
    world.advance(3600)
    world.forgot("203.0.113.1")
    assert len(world.mails()) == 5
    assert len(answers) == 1


def _code_good_for_15_minutes(world, when):
    """Is the newest code in the inbox one that works now, and still works 15 minutes from now?"""
    A = world.A
    code = A.normalise_code(world.last_code())
    vid = A.verifier_id(A.active_record())
    return A.code_ok(code, when, vid) and A.code_ok(code, when + 15 * 60 - 1, vid)


def test_one_code_email_per_window_whoever_asks(world):
    A = world.A
    world.forgot("203.0.113.1")
    world.advance(30)
    r = world.forgot("203.0.113.2")
    assert r.payload["message"] == A.GENERIC_FORGOT
    assert len(world.code_mails()) == 1, "the window's code is in the inbox already: it is not sent twice"
    assert _code_good_for_15_minutes(world, world.now), "and it is the code the second client would get"
    world.advance(A.BUCKET)
    world.forgot("203.0.113.2")
    assert len(world.code_mails()) == 2, "the next window has its own email"


def test_a_stranger_cannot_use_up_coleens_code_email(world):
    """Review, 25 Oct 2026. The per-instance ceiling (20 code emails an hour, shared by every client)
    was used up by 21 requests from 7 addresses of one /64 in 3 minutes. Coleen, asking afterwards from
    her own address, was told "a code has been emailed" while nothing was sent, and for most of the
    hour no code in the inbox worked. Now: whenever she asks, a code that works for 15 more minutes is
    in the inbox, and the stranger gets at most one email per window."""
    A = world.A
    stranger = (["2001:db8:66:1::%x" % i for i in range(1, 8)]          # one home IPv6 connection
                + ["192.0.2.%d" % i for i in range(10, 17)])             # and seven cloud machines
    coleen = "203.0.113.50"
    start = world.now
    windows = set()
    for minutes in (0, 5, 25, 50):
        world.now = start + minutes * 60
        for _ in range(3):                    # three rounds, a minute apart, from every address
            for ip in stranger:
                windows.add(int(world.now // A.BUCKET))
                world.forgot(ip)
            world.advance(61)
        windows.add(int(world.now // A.BUCKET))
        assert world.forgot(coleen).payload["message"] == A.GENERIC_FORGOT
        assert _code_good_for_15_minutes(world, world.now), \
            "Coleen asked %d minutes in and no working code is in the inbox" % minutes
    assert len(world.code_mails()) <= len(windows), "at most one code email per window"


def test_a_failed_email_does_not_hold_the_window(world):
    """The window is marked only once the email has gone out, so a failed send (or one a stranger
    started while Gmail was refusing) does not stop the next request sending it."""
    world.smtp.mode = "auth_fail"
    world.forgot("192.0.2.66")
    assert world.code_mails() == []
    world.smtp.mode = "ok"
    world.forgot("203.0.113.50")
    assert len(world.code_mails()) == 1 and _code_good_for_15_minutes(world, world.now)


def test_the_limit_key_is_the_address_or_its_64(world):
    A = world.A
    assert A.limit_key("2001:db8:1:2:aaaa::1") == A.limit_key("2001:db8:1:2:ffff:1:2:3") == "2001:db8:1:2::/64"
    assert A.limit_key("2001:db8:1:3::1") != A.limit_key("2001:db8:1:2::1")
    assert A.limit_key("::ffff:198.51.100.7") == A.limit_key("198.51.100.7") == "198.51.100.7"
    assert A.limit_key("198.51.100.8") != A.limit_key("198.51.100.7")
    assert A.limit_key("unknown") == "unknown" and A.limit_key("everyone") == "everyone"


def test_ipv6_clients_are_counted_by_their_64(world):
    """Review, 25 Oct 2026: each IPv6 /128 was its own client, so four addresses of one /64 got 60 wrong
    codes a minute answered, and seven sent seven times the code emails of one client."""
    A = world.A
    block = ["2001:db8:66:1::%x" % i for i in range(1, 5)]
    answered = sum(world.reset("0000000000", "Brand-new-passphrase-9", ip=block[i % 4]).status == 400
                   for i in range(60))
    assert answered == A.WRONG_PER_MINUTE, "one /64 is one client for wrong codes"
    assert world.reset("0000000000", "Brand-new-passphrase-9", ip="2001:db8:66:2::1").status == 400, \
        "the next /64 is not held back"
    for ip in block:                          # one address of the block per window
        world.advance(A.BUCKET)
        world.forgot(ip)
    assert len(world.code_mails()) == A.IP_PER_HOUR, "one /64 is one client for code emails"
    world.forgot("2001:db8:66:2::1")
    assert len(world.code_mails()) == A.IP_PER_HOUR + 1


def test_forgot_wants_an_empty_body_and_hides_a_mail_failure(world, capsys):
    assert world.A.forgot_endpoint({"email": "attacker@example.com"}).status == 400
    world.smtp.mode = "auth_fail"
    r = world.forgot()
    assert r.status == 200 and r.payload["message"] == world.A.GENERIC_FORGOT
    err = capsys.readouterr().err
    assert "forgot: not sent (SMTPAuthenticationError 535)" in err
    assert FAKE_SMTP_PASSWORD not in err


# ------------------------------------------------------------------ spending a code

def test_the_happy_path_hands_github_a_signed_encrypted_record_and_says_so(world):
    A = world.A
    world.forgot()
    shown = world.last_code()
    r = world.reset(shown.lower(), "Brand-new-passphrase-9")
    assert len(world.github.calls) == 1
    record = world.github.records()[0]
    assert r.status == 200 and r.payload == {"ok": True, "message": A.APPLYING, "epoch": 1,
                                             "id": record.split(".")[-1][:A.ID_LEN]}
    call = world.github.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == ("https://api.github.com/repos/Jojo140893/spb-ai-property-research-agent"
                           "/actions/workflows/apply-dashboard-password.yml/dispatches")
    assert call["headers"]["Authorization"] == "Bearer " + FAKE_TOKEN
    assert set(call["body"]) == {"ref", "inputs"} and call["body"]["ref"] == "main"
    assert set(call["body"]["inputs"]) == {"record"}
    # Then the run it started was looked up, with the same token, before anything was promised.
    assert [x["url"] for x in world.github.reads] == [
        "https://api.github.com/repos/Jojo140893/spb-ai-property-research-agent/actions/workflows"
        "/apply-dashboard-password.yml/runs?event=workflow_dispatch&per_page=5",
        "https://api.github.com/repos/Jojo140893/spb-ai-property-research-agent/actions/runs/4001/jobs"]
    assert all(x["method"] == "GET" and x["headers"]["Authorization"] == "Bearer " + FAKE_TOKEN
               for x in world.github.reads)
    shape = A.parse_record(record)
    assert shape and shape["epoch"] == 1 and shape["fp"] == A.env_fp() and shape["at"] == int(world.now)
    world.apply(record)
    rec = A.active_record()
    assert rec["iter"] == A.PBKDF2_ITER
    assert A.b64url(A.pbkdf2("Brand-new-passphrase-9", rec["salt"], rec["iter"])) == rec["hash"]
    assert rec["hash"] not in record and A.b64url(rec["salt"]) not in record, \
        "the hash and salt travel encrypted: GitHub's logs must not show them"
    subjects = [m["subject"] for m in world.mails()]
    assert subjects == [A.SUBJECT_CODE, A.SUBJECT_CHANGED]
    assert "Brand-new-passphrase-9" not in json.dumps(world.mails())


def test_once_live_the_record_rules_and_the_next_reset_counts_on(world):
    A = world.A
    rec1 = world.full_reset("First-new-passphrase-1")
    world.apply(rec1)
    assert A.active_record()["epoch"] == 1
    rec2 = world.full_reset("Second-new-passphrase-2")
    assert A.parse_record(rec2)["epoch"] == 2


def test_a_wrong_or_expired_code_changes_nothing(world):
    A = world.A
    world.forgot()
    code = A.normalise_code(world.last_code())
    wrong = ("0" if code[0] != "0" else "1") + code[1:]
    for c in (wrong, "ZZZZZ-ZZZZZ"):
        r = world.reset(c, "Brand-new-passphrase-9")
        assert r.status == 400 and r.payload["error"] == "code_invalid", c
    world.advance(21 * 60)
    r = world.reset(code, "Brand-new-passphrase-9")
    assert r.status == 400 and r.payload["error"] == "code_invalid"
    assert world.github.calls == [] and len(world.mails()) == 1


def test_a_password_that_changes_nothing_is_refused_after_the_code(world):
    world.forgot()
    code = world.last_code()
    r = world.reset(code, FAKE_SITE)
    assert r.status == 400 and r.payload["error"] == "weak_password"
    rec = world.full_reset("First-new-passphrase-1")
    world.apply(rec)
    world.advance(61)
    world.forgot()
    code = world.last_code()
    for same in ("First-new-passphrase-1", FAKE_SITE):
        r = world.reset(code, same)
        assert r.status == 400 and r.payload["error"] == "weak_password", same
    assert len(world.github.calls) == 1


@pytest.mark.parametrize("password,confirm,needle", [
    ("short-one", None, "at least 12"),
    ("x" * 129, None, "128 characters or fewer"),
    (" leading-space-passphrase", None, "start or end"),
    ("trailing-space-passphrase ", None, "start or end"),
    (USER.upper(), None, "sign-in email"),
    ("Brand-new-passphrase-9", "Brand-new-passphrase-8", "do not match"),
    ("tab\there-passphrase", None, "control characters"),
])
def test_password_rules(world, password, confirm, needle):
    world.forgot()
    r = world.reset(world.last_code(), password, confirm)
    assert r.status == 400 and needle in r.payload["message"]
    assert world.github.calls == []


def test_the_username_rule_is_asked_only_with_the_right_code(world):
    """Review, 25 Oct 2026: asked before the code, "do not use the sign-in email address" told anyone,
    with no code at all, whether a guess was SITE_USERNAME, which the login gate takes care to hide."""
    A = world.A
    for username in ("colin.private.login@example.com", None):     # a private one, then the default
        world.configure(SITE_USERNAME=username)
        A.LIMITS.clear()
        name = username or USER
        guesses = ("someone.else@example.com", name, name.upper())
        for guess in guesses:
            r = world.reset("0000000000", guess)
            assert (r.status, r.payload["error"], r.payload["message"]) == (400, "code_invalid", A.CODE_INVALID), guess
        assert len(A.LIMITS.wrong[A.limit_key("203.0.113.7")]) == len(guesses), "each counts as a wrong code"
        world.advance(A.BUCKET)
        world.forgot()
        r = world.reset(world.last_code(), name.title())
        assert r.status == 400 and r.payload["message"] == A.USERNAME_RULE, "with the right code it is refused"
    assert world.github.calls == []


def test_when_github_refuses_nothing_is_promised_and_the_code_still_works(world):
    world.forgot()
    code = world.last_code()
    for status in (401, 404, 500, 0):
        world.github.status = status
        r = world.reset(code, "Brand-new-passphrase-9")
        assert r.status == 503 and r.payload["error"] == "unavailable", status
    assert [m["subject"] for m in world.mails()] == [world.A.SUBJECT_CODE], "no 'changed' notice"
    world.github.status = 204
    assert world.reset(code, "Brand-new-passphrase-9").status == 200


def test_a_run_github_refuses_to_start_is_not_called_saved(world):
    """Review, 25 Oct 2026. GitHub answers 204 to a dispatch while it refuses every job over billing
    (30 Sep, run 36666518741; 25 Oct, run 40172642421): the run completes with conclusion failure in
    about 5 seconds, its job runs 0 steps, and it is never retried. Before this, the person was told
    "Saved", emailed that a new password "was set", and the leavers' password stayed in force."""
    A = world.A
    world.github.run = "refused"
    world.forgot()
    code = world.last_code()
    for attempt in range(A.HANDOFFS_PER_HOUR + 1):
        r = world.reset(code, "Brand-new-passphrase-9")
        assert (r.status, r.payload) == (503, {"ok": False, "message": A.NOT_STARTED, "error": "not_started"}), \
            attempt
    assert "Nothing changed" in A.NOT_STARTED and "old password still works" in A.NOT_STARTED
    assert [m["subject"] for m in world.mails()] == [A.SUBJECT_CODE], "no 'change requested' notice"
    assert len(world.github.calls) == A.HANDOFFS_PER_HOUR + 1, "a refused run is not counted as a change"
    world.github.run = "started"
    r = world.reset(code, "Brand-new-passphrase-9")
    assert r.status == 200 and r.payload["message"] == A.APPLYING, "the code still works once GitHub runs jobs"
    assert [m["subject"] for m in world.mails()] == [A.SUBJECT_CODE, A.SUBJECT_CHANGED]


def test_a_run_that_stops_before_storing_is_not_called_saved(world):
    """The workflow's own checks stopped it, or GitHub cancelled it: "stopped", not "refusing jobs"."""
    A = world.A
    for kind in ("stopped", "cancelled"):
        world.advance(A.BUCKET)
        world.forgot(world.fresh_ip())
        code = world.last_code()
        world.github.run = kind
        r = world.reset(code, "Brand-new-passphrase-9")
        assert (r.status, r.payload) == (503, {"ok": False, "message": A.STOPPED, "error": "not_started"}), kind
        assert world.mails()[-1]["subject"] == A.SUBJECT_CODE, "%s: no 'change requested' notice" % kind
    world.github.run = "started"
    assert world.reset(code, "Brand-new-passphrase-9").status == 200, "the code still works"


@pytest.mark.parametrize("kind", ["queued", "unreadable"])
def test_a_run_not_seen_starting_promises_nothing(world, kind):
    """Queued past the wait, or GitHub not saying: it may still go live, so neither "started" nor
    "nothing changed". The page says it is not live yet and keeps watching."""
    A = world.A
    world.github.run = kind
    world.forgot()
    r = world.reset(world.last_code(), "Brand-new-passphrase-9")
    assert r.status == 200 and r.payload["message"] == A.PENDING and r.payload["epoch"] == 1, r.payload
    assert "old password still works" in A.PENDING and "Saved" not in A.PENDING
    _assert_the_notice_promises_nothing(world)
    waited = [s for s in world.slept if s == A.RUN_POLL]
    assert 1 <= len(waited) <= A.RUN_WAIT / A.RUN_POLL + 1, "the wait is bounded"
    assert all(x["timeout"] <= A.GITHUB_TIMEOUT for x in world.github.reads)


# Words that make a promise the 15-second answer cannot keep: the workflow's own checks may still refuse
# the change, and whoever holds the dispatch token can cancel any run (second review, 25 Oct 2026).
_PROMISES = re.compile(r"\bsaved\b|\bminutes?\b|\bseconds?\b|\bwithin\b|\busually\b|\bsoon\b|was set\b"
                       r"|\bstarts working\b|\bnow live\b", re.I)


def _assert_the_answer_promises_nothing(message):
    assert not _PROMISES.search(message), "the answer promises what nobody knows yet: %r" % message
    assert "old password still works until this page says the new one is live" in message, message


def _assert_the_notice_promises_nothing(world):
    notice = world.mails()[-1]
    assert notice["subject"] == world.A.SUBJECT_CHANGED and "was requested at" in notice["body"]
    rest = notice["body"].split("\n", 1)[1]                  # after the line with the time it was asked
    assert not _PROMISES.search(rest), "the notice promises more than the page: %r" % rest
    assert "old\npassword keeps working until the reset page says the new one is live" in rest, rest


def test_no_answer_at_the_wait_says_saved_or_gives_a_time(world):
    """Second review, 25 Oct 2026: a run was "started", so "Saved. Your new password starts working in
    about 2 minutes", and the notice went out, the moment GitHub showed "Set up job", seconds before the
    workflow's own check ran. Ten forged dispatches used up the daily cap, the check then refused every
    real reset for 24 hours, and each was told "Saved". Now a run that started is watched until its check
    step answers: refused inside the wait is the honest 503, and every 200 says only what is true."""
    A = world.A
    cases = (
        # (how the run moves, listing by listing; the answer; the message, by its name in the module)
        (["queued", "started", "gate failed, still running"], 503, "STOPPED"),
        (["queued", "started", "stopped"], 503, "STOPPED"),
        (["queued", "started", "cancelled after start"], 503, "STOPPED"),
        (["queued", "started"], 200, "APPLYING"),                    # its check had not answered in time
        (["queued", "started", "past the gate"], 200, "APPLYING"),
        (["started", "stored, then failed"], 200, "APPLYING"),       # stored: the next deploy puts it live
    )
    for moves, status, message in cases:
        world.advance(3601)                  # past the hour's hand-off limit, which is not what is tested
        ip = world.fresh_ip()
        world.forgot(ip)
        code = world.last_code()
        mails = len(world.mails())
        world.github.run = moves
        r = world.reset(code, "Brand-new-passphrase-9", ip=ip)
        assert (r.status, r.payload["message"]) == (status, getattr(A, message, message)), (moves, r.payload)
        if status == 503:
            assert len(world.mails()) == mails, "%s: a notice went out for a change that was refused" % moves
            assert r.payload["error"] == "not_started"
        else:
            _assert_the_answer_promises_nothing(r.payload["message"])
            _assert_the_notice_promises_nothing(world)
    _assert_the_answer_promises_nothing(A.PENDING)
    for name, value in vars(A).items():
        if isinstance(value, str) and name.isupper():
            assert not re.search(r"\bsaved\b|starts working", value, re.I), "%s still says it: %r" % (name, value)


def test_a_double_click_does_not_hand_over_twice(world):
    world.forgot()
    code = world.last_code()
    assert world.reset(code, "Brand-new-passphrase-9").status == 200
    r = world.reset(code, "Brand-new-passphrase-9")
    assert r.status == 400 and r.payload["error"] == "code_invalid"
    assert len(world.github.calls) == 1


def test_wrong_codes_block_only_the_client_sending_them(world):
    A = world.A
    for _ in range(A.WRONG_PER_MINUTE):
        assert world.reset("0000000000", "Brand-new-passphrase-9", ip="192.0.2.66").status == 400
    assert world.reset("0000000000", "Brand-new-passphrase-9", ip="192.0.2.66").status == 429
    world.forgot("203.0.113.5")
    r = world.reset(world.last_code(), "Brand-new-passphrase-9", ip="203.0.113.5")
    assert r.status == 200, "Coleen, on another IP, is not held back by a stranger's guessing"
    world.advance(61)
    assert world.reset("0000000000", "Brand-new-passphrase-9", ip="192.0.2.66").status == 400


def test_hand_offs_are_capped_so_the_days_deploys_last(world):
    A = world.A
    for i in range(A.HANDOFFS_PER_HOUR):
        world.apply(world.full_reset("New-passphrase-number-%d" % i))
    world.advance(61)
    ip = world.fresh_ip()
    world.forgot(ip)
    r = world.reset(world.last_code(), "One-too-many-passphrase", ip=ip)
    assert r.status == 429 and r.payload["error"] == "busy"
    assert len(world.github.calls) == A.HANDOFFS_PER_HOUR
    world.advance(3600)
    world.forgot(ip)
    assert world.reset(world.last_code(), "One-too-many-passphrase", ip=ip).status == 200


def test_the_request_shape_is_strict(world):
    A = world.A
    assert A.reset_endpoint({"code": "x", "password": "y"}).status == 400
    assert A.reset_endpoint({"code": "x", "password": "y", "confirm": "y", "extra": 1}).status == 400
    r = world.reset("12345", "Brand-new-passphrase-9")
    assert r.status == 400 and r.payload["error"] == "bad_code_format"
    r = world.reset(["ABCDE", "FGHJK"], "Brand-new-passphrase-9")
    assert r.status == 400 and r.payload["error"] == "bad_code_format"


# ------------------------------------------------------------------ the record

def test_the_record_round_trips_and_is_shell_safe(world):
    A = world.A
    rec = A.build_record("Brand-new-passphrase-9", 7, world.now)
    assert A.RECORD_RE.match(rec) and re.fullmatch(r"[A-Za-z0-9._-]+", rec)
    world.apply(rec)
    got = A.active_record()
    assert got["epoch"] == 7 and got["at"] == int(world.now) and got["iter"] == A.PBKDF2_ITER
    assert A.next_epoch() == 8
    assert A.build_record("Brand-new-passphrase-9", 7, world.now) != rec, "a fresh salt and nonce each time"


def test_the_owner_changing_site_password_makes_the_record_ignored(world):
    A = world.A
    world.apply(A.build_record("Brand-new-passphrase-9", 3, world.now))
    world.configure(SITE_PASSWORD="owner-changed-it-in-vercel", SPB_PW_RECORD=os.environ["SPB_PW_RECORD"])
    assert A.active_record() is None
    assert A.next_epoch() == 4, "an ignored record still counts, so epochs never repeat"


def test_a_changed_secret_is_refused_not_ignored(world):
    """Review, 23 Oct 2026: with the fingerprint checked first, a new SPB_AUTH_SECRET made the record
    look written-against-another-SITE_PASSWORD, so it was ignored and the OLD password came back."""
    A = world.A
    rec = A.build_record("Brand-new-passphrase-9", 1, world.now)
    world.configure(SPB_AUTH_SECRET=OTHER_SECRET, SPB_PW_RECORD=rec)
    with pytest.raises(A.ConfigError):
        A.active_record()


def test_a_record_that_cannot_be_trusted_is_an_error_not_a_fallback(world, monkeypatch):
    A = world.A
    rec = A.build_record("Brand-new-passphrase-9", 1, world.now)
    parts = rec.split(".")
    parts[5] = ("A" if parts[5][0] != "A" else "B") + parts[5][1:]       # another box, same shape
    monkeypatch.setattr(A, "PBKDF2_ITER", 50000)
    weak = A.build_record("Brand-new-passphrase-9", 1, world.now)        # signed, but too few rounds
    monkeypatch.undo()
    for label, bad in {"tampered box": ".".join(parts), "malformed": "v1.not-a-record",
                       "iterations out of range": weak}.items():
        world.apply(bad)
        with pytest.raises(A.ConfigError):
            A.active_record()
        r = world.reset("ABCDE-FGHJK", "Brand-new-passphrase-9")
        assert r.status == 503 and r.payload["error"] == "unavailable", label
    world.configure(SPB_AUTH_SECRET=None, SPB_PW_RECORD=rec)
    with pytest.raises(A.ConfigError):
        A.active_record()


def test_check_record_is_the_workflows_gate(world, monkeypatch):
    A = world.A
    real_now = time.time()
    rec = A.build_record("Brand-new-passphrase-9", 12, real_now)

    def run(value, *args):
        env = dict(os.environ, RECORD=value)
        return subprocess.run([sys.executable, str(API / "_spb_auth.py")] + list(args or ["check-record"]),
                              env=env, capture_output=True, text=True, timeout=60)

    p = run(rec)
    assert p.returncode == 0 and p.stdout.strip() == "12 " + rec.split(".")[-1][:A.ID_LEN]
    assert A.parse_record(rec)["box"] not in p.stdout + p.stderr
    for bad in ("", "v1.x", rec + "\n; rm -rf /", rec + "\n", " " + rec, rec.replace(".", "..", 1), "$(id)"):
        p = run(bad)
        assert p.returncode == 1 and p.stdout == "", repr(bad)
    for label, when in {"31 minutes old (a re-run)": real_now - 31 * 60, "from the future": real_now + 600}.items():
        p = run(A.build_record("Brand-new-passphrase-9", 12, when))
        assert p.returncode == 1 and p.stdout == "", label
    assert run(rec, "something-else").returncode == 2


def forged_record(epoch, at):
    """What anyone with the dispatch token can write: the right shape, a mac nobody computed."""
    import base64
    rnd = lambda n: base64.urlsafe_b64encode(os.urandom(n)).rstrip(b"=").decode("ascii")
    return "v1.%d.%d.%s.%s.%s.%s" % (epoch, int(at), os.urandom(8).hex(), rnd(16), rnd(52), rnd(32))


def test_reset_check_vouches_only_for_records_this_dashboard_signed(world):
    """Review, 25 Oct 2026: the workflow could check only a record's shape, so a forged dispatch was
    stored and redeployed, and sign-in answered 503 for everyone. /api/reset_check checks the mac."""
    A = world.A
    good = A.build_record("Brand-new-passphrase-9", 1, world.now)
    assert A.verify_record(good, world.now) is None
    r = A.check_endpoint({"record": good})
    assert (r.status, r.payload) == (204, None)
    forged = forged_record(1, world.now)
    assert A.parse_record(forged) is not None, "the forgery has exactly the right shape"
    world.configure(SPB_AUTH_SECRET=OTHER_SECRET)
    signed_elsewhere = A.build_record("Brand-new-passphrase-9", 1, world.now)
    world.configure(SITE_PASSWORD="a-different-site-password")
    other_site = A.build_record("Brand-new-passphrase-9", 1, world.now)
    world.configure()
    old = A.build_record("Brand-new-passphrase-9", 1, world.now - 31 * 60)
    future = A.build_record("Brand-new-passphrase-9", 1, world.now + 600)
    for label, rec, why in (("forged", forged, "not_signed"), ("signed under another secret", signed_elsewhere,
                             "not_signed"), ("made under another SITE_PASSWORD", other_site, "other_site_password"),
                            ("31 minutes old", old, "stale"), ("from the future", future, "stale"),
                            ("malformed", "v1.not-a-record", "malformed"), ("with a newline", good + "\n", "malformed")):
        assert A.verify_record(rec, world.now) == why, label
        r = A.check_endpoint({"record": rec})
        assert r.status == 422 and r.payload["error"] == why, label
    world.apply(A.build_record("Brand-new-passphrase-8", 3, world.now))
    assert A.verify_record(good, world.now) == "not_newer_than_live"
    assert A.verify_record(A.build_record("Brand-new-passphrase-9", 3, world.now), world.now) == \
        "not_newer_than_live", "the live number is not newer"
    assert A.verify_record(A.build_record("Brand-new-passphrase-9", 4, world.now), world.now) is None
    world.apply(forged)
    assert A.verify_record(A.build_record("Brand-new-passphrase-9", 4, world.now), world.now) == \
        "live_record_broken", "a live record that cannot be trusted vouches for nothing"
    for body in ({}, {"record": good, "extra": 1}, {"record": 5}):
        assert A.check_endpoint(body).status == 400


def test_a_record_is_applied_only_over_an_older_number(world, tmp_path):
    """Second review, 25 Oct 2026. Two resets made before either is live get the same number. With
    "refuse only an older number", the one that lost could be replayed (its record is readable in the
    run's inputs) for up to 30 minutes after the other went live, bringing the older password back. Now
    a record whose number is not above the live one is refused by /api/reset_check AND by the
    workflow's own check, so the second of two same-moment resets fails, and says so."""
    A = world.A
    first = world.full_reset("First-new-passphrase-1")
    world.advance(A.BUCKET)                   # a code is spent once here; the next window's is another
    second = world.full_reset("Second-new-passphrase-2")
    assert A.parse_record(first)["epoch"] == A.parse_record(second)["epoch"] == 1, "made at the same moment"
    assert A.verify_record(first, world.now) is None and A.verify_record(second, world.now) is None
    world.apply(second)                       # the second one went live
    world.advance(60)
    for label, rec in (("the one that lost, replayed", first), ("the live one, again", second)):
        assert A.verify_record(rec, world.now) == "not_newer_than_live", label
        r = A.check_endpoint({"record": rec})
        assert (r.status, r.payload["error"]) == (422, "not_newer_than_live"), label
    assert A.parse_record(world.full_reset("Third-new-passphrase-3"))["epoch"] == 2
    assert A.verify_record(world.github.records()[-1], world.now) is None, "the next reset still works"
    # The workflow's own check, from /login's number alone (SPB_PW_RECORD unset here, so
    # /api/reset_check vouches, and only the step's comparison decides).
    world.configure()
    world.now = time.time()                   # check-record (a subprocess) reads the real clock
    rec = A.build_record("Brand-new-passphrase-9", 3, world.now)
    code, out, outputs, asked = run_check_step(world, tmp_path / "same", rec, live_epoch="3")
    assert code != 0 and "number 3 is already live" in out, "the step applied a record of the live number:\n" + out
    assert outputs == "" and asked == "", "the step went on after refusing it"
    code, out, outputs, asked = run_check_step(world, tmp_path / "newer", rec, live_epoch="2")
    assert code == 0 and outputs.split()[0] == "epoch=3", out


# The reset page's script under Node, with just enough of a browser: the form is submitted, the server
# answers 200 for password number 2 with id "mine", and /login then reports the number and id given.
_RESET_PAGE_HARNESS = r'''
import { readFileSync } from "node:fs";
const [src, seenEpoch, seenId] = process.argv.slice(2);
const els = {};
function el(id) {
  if (!els[id]) els[id] = { id, hidden: id === "wait" || id === "live" || id === "out", value: "",
    textContent: "", type: "password", disabled: false, listeners: {},
    addEventListener(type, fn) { this.listeners[type] = fn; }, focus() {} };
  return els[id];
}
globalThis.document = { getElementById: el };
globalThis.location = { hash: "", pathname: "/reset" };
globalThis.history = { replaceState() {} };
let timers = 0;
globalThis.setTimeout = () => { timers += 1; };
globalThis.fetch = async (url) => {
  if (url === "/api/reset_password") {
    return { status: 200, json: async () => ({ ok: true, epoch: 2, id: "mine", message: "MESSAGE FROM THE SERVER" }) };
  }
  if (url === "/login") {
    return { status: 200, headers: { get: (n) => (n === "x-spb-auth-epoch" ? seenEpoch : n === "x-spb-auth-id" ? seenId : null) } };
  }
  throw new Error("unexpected fetch " + url);
};
new Function(readFileSync(src, "utf8"))();
el("code").value = "ABCDE-FGHJK";
el("pw").value = "Brand-new-passphrase-9";
el("pw2").value = "Brand-new-passphrase-9";
el("f").listeners.submit({ preventDefault() {} });
for (let i = 0; i < 20; i++) await new Promise((resolve) => setImmediate(resolve));
process.stdout.write(JSON.stringify({ text: els.waitText.textContent, live: !els.live.hidden, timers }));
'''


def test_the_reset_page_says_so_when_a_reset_made_at_the_same_moment_went_live(world, tmp_path):
    """Same number, another id on /login: a reset made at the same moment went live. The page used to
    say nothing and wait ("ours is queued behind it"), but the workflow now refuses ours (above), so the
    page says what is true, and still watches in case ours was already past the check."""
    import build_web
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the reset page cannot be run here")
    src = re.search(r"const RESET_JS = `(.*?)`;", build_web.MIDDLEWARE_JS, re.S).group(1)
    (tmp_path / "reset.js").write_text(src, encoding="utf-8")
    (tmp_path / "harness.mjs").write_text(_RESET_PAGE_HARNESS, encoding="utf-8")
    seen = {}
    for label, (epoch, rid) in {"twin": ("2", "theirs"), "newer": ("3", "theirs"), "ours": ("2", "mine"),
                                "older": ("1", "old"), "no record": ("0", "none")}.items():
        p = subprocess.run([node, str(tmp_path / "harness.mjs"), str(tmp_path / "reset.js"), epoch, rid],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert p.returncode == 0, label + ": " + p.stderr[-2000:]
        seen[label] = json.loads(p.stdout)
    assert seen["twin"]["text"].startswith("A password made at the same moment as yours went live instead"), seen
    assert not seen["twin"]["live"] and seen["twin"]["timers"] == 1, "not live, and still watched"
    assert seen["newer"]["text"].startswith("A newer password was set after yours") and seen["newer"]["timers"] == 0
    assert seen["ours"]["text"].startswith("Your new password is live") and seen["ours"]["live"]
    for label in ("older", "no record"):
        assert seen[label] == {"text": "MESSAGE FROM THE SERVER", "live": False, "timers": 1}, (label, seen[label])


def _git_bash():
    """GitHub runs a step with bash. On Windows that is Git's bash, never C:\\Windows\\System32\\bash.exe
    (WSL: another machine, with none of this checkout's tools)."""
    if os.name != "nt":
        return shutil.which("bash")
    for base in (os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles"), r"C:\Program Files"):
        if base and (Path(base) / "Git" / "bin" / "bash.exe").is_file():
            return str(Path(base) / "Git" / "bin" / "bash.exe")
    return None


def _check_step_script():
    data, _ = _workflow()
    return next(s for s in data["jobs"]["apply"]["steps"] if s.get("id") == "check")["run"]


def run_check_step(world, tmp_path, record, script=None, live_epoch="0", site_up=True, github=None):
    """Run the workflow's first step, as written in the .yml, under bash, against a stand-in for the live
    site that answers /login as middleware.js does and /api/reset_check with the REAL handler. The same
    stand-in answers GitHub's API (GITHUB_API_URL points at it) from `github`, a FakeGitHub, by default
    one with no runs: that is what the daily cap reads, so a read there shows the step got that far.
    `gh` is a stub that logs and fails: the step must not need it (nor reach the real GitHub).
    Returns (exit code, output, the step's outputs, what GitHub and gh were asked)."""
    bash = _git_bash()
    if not bash or not shutil.which("curl"):
        pytest.skip("bash and curl are needed to run the workflow step")
    tmp_path.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(API))
    reset_check = importlib.import_module("reset_check")
    github = github if github is not None else FakeGitHub(time.time)
    reads_before = len(github.reads)

    class LiveSite(reset_check.handler):
        def do_GET(self):
            if self.path.startswith("/repos/"):
                status, data = github("GET", "https://api.github.com" + self.path, dict(self.headers), None, 30)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if self.path != "/login":
                return self._not_allowed()
            self.send_response(200)
            self.send_header("x-spb-auth-epoch", live_epoch)
            self.send_header("x-spb-auth-id", "none")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            if self.path != "/api/reset_check":
                return self._not_allowed()
            return super().do_POST()

    server = ThreadingHTTPServer(("127.0.0.1", 0), LiveSite)
    port = server.server_address[1]
    if site_up:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    else:
        server.server_close()                 # nothing listens: "no answer at all"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh_log = tmp_path / "gh.log"
    gh_log.write_text("", encoding="utf-8")
    # python3 is this interpreter. tr: Windows Python writes CRLF, a runner's does not.
    (bin_dir / "python3").write_bytes(('#!/bin/bash\nset -o pipefail\n"%s" "$@" | tr -d "\\r"\n'
                                       % Path(sys.executable).as_posix()).encode("utf-8"))
    (bin_dir / "gh").write_bytes(('#!/bin/sh\necho "gh $*" >> "%s"\necho "gh is not to be used" >&2\nexit 1\n'
                                  % gh_log.as_posix()).encode("utf-8"))
    out_file = tmp_path / "github_output"
    out_file.write_text("", encoding="utf-8")
    step = tmp_path / "step.sh"
    step.write_bytes((script if script is not None else _check_step_script()).encode("utf-8"))
    env = dict(os.environ, RECORD=record, SITE="http://127.0.0.1:%d" % port, MAX_PER_DAY="10",
               GH_TOKEN="fake-github-token", GITHUB_REPOSITORY="Jojo140893/spb-ai-property-research-agent",
               GITHUB_API_URL="http://127.0.0.1:%d" % port,
               GITHUB_RUN_ID="1", GITHUB_OUTPUT=out_file.as_posix(), RUNNER_TEMP=tmp_path.as_posix(),
               PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    try:
        p = subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", step.as_posix()], cwd=str(APP),
                           env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    finally:
        if site_up:
            server.shutdown()
            server.server_close()
    asked = gh_log.read_text(encoding="utf-8") + "".join(
        "GET %s %s\n" % (r["url"], r["headers"].get("Authorization", "")) for r in github.reads[reads_before:])
    return p.returncode, p.stdout + p.stderr, out_file.read_text(encoding="utf-8"), asked


def test_the_workflow_stores_nothing_the_dashboard_did_not_sign(world, tmp_path):
    """Review, 25 Oct 2026: a forged dispatch passed the first step (shape and age only), was stored,
    and sign-in answered 503 for everyone. The step must now fail for it, before the next check (the
    daily cap, which calls gh) runs, and no step after it may run when it fails."""
    A = world.A
    world.now = time.time()                   # check-record (a subprocess) reads the real clock
    good = A.build_record("Brand-new-passphrase-9", 1, world.now)
    code, out, outputs, gh = run_check_step(world, tmp_path / "good", good)
    assert code == 0, "the step refused a record the dashboard made:\n" + out
    assert outputs.split() == ["epoch=1", "id=" + A.parse_record(good)["id"]], out
    assert "/actions/workflows/apply-dashboard-password.yml/runs?" in gh and "gh " not in gh, gh
    assert "Bearer fake-github-token" in gh, "the cap reads GitHub with the run's own token"
    world.configure(SPB_AUTH_SECRET=OTHER_SECRET)
    signed_elsewhere = A.build_record("Brand-new-passphrase-9", 1, world.now)
    world.configure()
    for label, rec, kw in (("forged", forged_record(1, world.now), {}),
                           ("signed under another secret", signed_elsewhere, {}),
                           ("the site does not answer", good, {"site_up": False})):
        code, out, outputs, gh = run_check_step(world, tmp_path / label.replace(" ", "-"), rec, **kw)
        assert code != 0, "%s: the step let it through:\n%s" % (label, out)
        assert outputs == "" and gh == "", "%s: the step went on after refusing it" % label
        assert "did not confirm that the dashboard signed this record" in out, label
        assert rec not in out, "%s: the record was printed" % label
    data, _ = _workflow()
    job = data["jobs"]["apply"]
    assert job.get("if") == "github.ref == 'refs/heads/main'" and "continue-on-error" not in job, \
        "the job's only condition is the main-only guard"
    for s in job["steps"]:
        assert "if" not in s and "continue-on-error" not in s, \
            "%r could run after a failed check" % s.get("name", s.get("uses"))


def test_the_daily_cap_counts_only_runs_that_stored_a_password(world):
    """Second review, 25 Oct 2026: the workflow's cap counted every run in 24 hours. Ten forged
    dispatches (each refused by /api/reset_check), or a day of runs GitHub refused over billing, used it
    up, and every real reset for the next day was refused by the cap. Only a run that got as far as
    storing a password, in any attempt, is a change."""
    A = world.A
    gh, now = world.github, world.now
    for i in range(120):                     # forged: refused at the check. More than one page of runs.
        gh.add_run("stopped", now - 30 * (i + 1))
    for i in range(6):
        gh.add_run("refused", now - 7200 - 60 * i)                  # GitHub refusing jobs over billing
    for kind in ("cancelled", "cancelled after start", "skipped", "skipped, run says success", "queued",
                 "started", "past the gate", "gate failed, still running"):
        gh.add_run(kind, now - 9000)
    for kind in ("applied", "stored, then failed", "re-run after a store"):
        gh.add_run(kind, now - 10000)
    gh.add_run("applied", now - A.APPLIED_WINDOW - 60)             # yesterday's: outside the 24 hours
    gh.add_run("past the gate", now, run_id=777)                   # the run asking: not counted
    repo = "Jojo140893/spb-ai-property-research-agent"
    assert A.count_applied(now, 777, "fake-github-token", repo) == 3
    assert all(r["headers"]["Authorization"] == "Bearer fake-github-token" for r in gh.reads), \
        "read with the run's own token, never the dashboard's"
    assert any("page=2" in r["url"] for r in gh.reads), "past the first page of runs"
    assert all("filter=all" in r["url"] for r in gh.reads if "/jobs" in r["url"]), "every attempt is looked at"
    for i in range(7):
        gh.add_run("applied", now - 100 - i)
    assert A.count_applied(now, 777, "fake-github-token", repo) == 10
    gh.run = "unreadable"
    with pytest.raises(RuntimeError):
        A.count_applied(now, 777, "fake-github-token", repo)


def test_the_workflows_daily_cap_is_not_used_up_by_runs_that_changed_nothing(world, tmp_path):
    """The same, through the workflow's step as written, under bash."""
    A = world.A
    world.now = time.time()                   # check-record (a subprocess) reads the real clock
    good = A.build_record("Brand-new-passphrase-9", 1, world.now)
    gh = FakeGitHub(time.time)
    for i in range(10):
        gh.add_run("stopped", world.now - 60 * (i + 1))             # ten forged dispatches
    for i in range(6):
        gh.add_run("refused", world.now - 3600 - 60 * i)            # a day of billing refusals
    code, out, outputs, asked = run_check_step(world, tmp_path / "nothing-changed", good, github=gh)
    assert code == 0, "runs that changed nothing used up the day's changes:\n" + out
    assert "0 other changes in 24 hours" in out and outputs.startswith("epoch=1"), out
    for i in range(10):
        gh.add_run("applied", world.now - 120 * (i + 1))
    code, out, outputs, asked = run_check_step(world, tmp_path / "ten-changes", good, github=gh)
    assert code != 0 and "10 password changes in the last 24 hours (limit 10)" in out and outputs == "", out
    gh.run = "unreadable"
    code, out, outputs, asked = run_check_step(world, tmp_path / "github-silent", good, github=gh)
    assert code != 0 and "Could not count this workflow's recent password changes" in out, out
    assert outputs == "", "GitHub not saying is a refusal"


# ------------------------------------------------------------------ the request plumbing

def _headers(**over):
    h = {"Origin": ORIGIN, "Host": HOST, "Content-Type": "application/json", "Content-Length": "2"}
    h.update(over)
    return {k: v for k, v in h.items() if v is not None}


class _Body(object):
    def __init__(self, raw):
        self.raw = raw

    def read(self, n):
        return self.raw[:n]


def test_respond_refuses_everything_but_small_same_origin_json(world):
    A = world.A
    seen = []
    ok = lambda body, ip: seen.append(ip) or A.Reply(200, {"ok": True})
    assert A.respond(_headers(**{"x-real-ip": "203.0.113.4"}), _Body(b"{}"), ok).status == 200
    assert seen == ["203.0.113.4"], "the endpoint is told who is asking"
    for label, h in {
        "no origin": _headers(Origin=None), "null origin": _headers(Origin="null"),
        "http origin": _headers(Origin="http://" + HOST), "other site": _headers(Origin="https://evil.example"),
        "origin with a path": _headers(Origin=ORIGIN + "/x"),
        "cross-site fetch": _headers(**{"Sec-Fetch-Site": "cross-site"}),
    }.items():
        assert A.respond(h, _Body(b"{}"), ok).status == 403, label
    assert A.respond(_headers(**{"Content-Type": "text/plain"}), _Body(b"{}"), ok).status == 415
    assert A.respond(_headers(**{"Content-Length": None}), _Body(b"{}"), ok).status == 411
    assert A.respond(_headers(**{"Transfer-Encoding": "chunked"}), _Body(b"{}"), ok).status == 411
    assert A.respond(_headers(**{"Content-Length": "5000"}), _Body(b"{}"), ok).status == 413
    for raw in (b"[]", b'{"a":1,"a":2}', b'{"a":NaN}', b"\xff"):
        assert A.respond(_headers(**{"Content-Length": str(len(raw))}), _Body(raw), ok).status == 400, raw
    # /api/reset_check is called by a server, with no Origin; every other check still applies.
    seen.clear()
    assert A.respond(_headers(Origin=None), _Body(b"{}"), ok, need_origin=False).status == 200 and seen
    assert A.respond(_headers(Origin=None, **{"Content-Type": "text/plain"}), _Body(b"{}"), ok,
                     need_origin=False).status == 415
    world.configure(SPB_SMTP_PASSWORD=None)
    r = A.respond(_headers(), _Body(b"{}"), ok)
    assert r.status == 503 and r.payload == {"ok": False, "message": A.NOT_SET_UP, "configured": False}
    assert A.respond(_headers(Origin=None), _Body(b"{}"), ok, need_origin=False).status == 503, \
        "not configured: reset_check vouches for nothing"


def test_the_route_files_serve_over_real_http(world):
    """The handler classes Vercel will load, behind a real socket: POST works, anything else is 405."""
    import http.client
    sys.path.insert(0, str(API))
    reset_code = importlib.import_module("reset_code")
    reset_password = importlib.import_module("reset_password")
    reset_check = importlib.import_module("reset_check")
    good = world.A.build_record("Brand-new-passphrase-9", 1, world.now)
    for mod, body, expect, origin in ((reset_code, b"{}", 200, True), (reset_password, b'{"code":"x"}', 400, True),
                                      (reset_check, b'{"record":"x"}', 422, False),
                                      (reset_check, json.dumps({"record": good}).encode(), 204, False)):
        server = ThreadingHTTPServer(("127.0.0.1", 0), mod.handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            host = "127.0.0.1:%d" % server.server_address[1]
            c = http.client.HTTPConnection(host, timeout=30)
            headers = {"Host": host, "Content-Type": "application/json"}
            if origin:
                headers["Origin"] = "https://" + host
            c.request("POST", "/", body=body, headers=headers)
            r = c.getresponse()
            assert r.status == expect and r.getheader("Cache-Control") == "no-store", (mod.__name__, r.status)
            raw = r.read()
            if expect == 204:
                assert raw == b"" and r.getheader("Content-Length") is None and r.getheader("Content-Type") is None
                c.close()
                c = http.client.HTTPConnection(host, timeout=30)
            else:
                assert json.loads(raw)["ok"] is (expect == 200)
            c.request("GET", "/")
            r = c.getresponse()
            r.read()
            assert r.status == 405 and r.getheader("Allow") == "POST"
        finally:
            server.shutdown()
            server.server_close()


def test_no_log_line_carries_a_secret(world, capsys):
    A = world.A
    rec1 = world.full_reset("First-new-passphrase-1")
    world.apply(rec1)
    live = A.active_record()
    world.advance(61)
    world.forgot()
    code = world.last_code()
    world.reset(code, "short")
    world.reset("ZZZZZ-ZZZZZ", "Second-new-passphrase-2")
    world.github.status = 500
    world.reset(code, "Second-new-passphrase-2")
    world.github.status = 204
    world.github.run = "refused"
    world.reset(code, "Second-new-passphrase-2")
    forged = forged_record(2, world.now)
    fresh = A.build_record("Third-new-passphrase-3", 2, world.now)
    assert A.check_endpoint({"record": fresh}).status == 204
    assert A.check_endpoint({"record": rec1}).status == 422, "the live one is not newer than itself"
    assert A.check_endpoint({"record": forged}).status == 422
    err = capsys.readouterr().err
    assert "[auth] reset: new password handed to GitHub" in err and "[auth] forgot: code sent" in err
    assert "[auth] reset: GitHub refused the run; nothing changed" in err
    assert "[auth] check: record refused (not_signed)" in err
    for secret in (FAKE_SITE, FAKE_SECRET, FAKE_SMTP_PASSWORD, FAKE_TOKEN, "First-new-passphrase-1",
                   "Second-new-passphrase-2", code, A.normalise_code(code), live["hash"], live["mac"],
                   live["box"], rec1, forged, fresh):
        assert secret not in err, "a log line carries a secret"


def test_the_email_cannot_be_aimed_or_injected(world):
    A = world.A
    for bad in ("a@b.com\nBcc: x@y.com", "a@b.com\r\nSubject: hi", "Colin <colin@example.com>"):
        world.configure(SPB_RESET_TO=bad)
        with pytest.raises(ValueError):
            A.build_message("s", "b")


def test_the_changed_notice_has_a_short_deadline(world):
    """A slow mail server must not push a reset that GitHub already accepted past the function's limit."""
    timeouts = []
    real = world.A.SMTP_FACTORY
    world.mp.setattr(world.A, "SMTP_FACTORY",
                     lambda host, p, timeout: timeouts.append(timeout) or real(host, p, timeout))
    world.forgot()
    assert world.reset(world.last_code(), "Brand-new-passphrase-9").status == 200
    assert timeouts == [world.A.SMTP_TIMEOUT, world.A.NOTICE_TIMEOUT]


# ------------------------------------------------------------------ the build and the workflow

def test_the_build_ships_the_three_routes_and_their_module(world):
    import build_web
    for name in ("_spb_auth.py", "reset_code.py", "reset_password.py", "reset_check.py"):
        assert name in build_web._FUNCTION_ENTRY
        assert (API / name).is_file()
    src = (APP / "build_web.py").read_text(encoding="utf-8")
    assert '"api/reset_code.py": {"memory": 256, "maxDuration": 30,' in src
    assert '"api/reset_password.py": {"memory": 256, "maxDuration": 60,' in src
    assert '"api/reset_check.py": {"memory": 256, "maxDuration": 10,' in src
    assert "import reset_code, reset_password, reset_check, _spb_auth" in build_web._READONLY_PROBE


def test_the_reset_deadline_fits_inside_the_functions_limit(world):
    """The arithmetic the deadline rests on (the tests below prove the code keeps to it). Second review,
    25 Oct 2026: the old check here added the configured timeouts, 48 s, but the code gave two reads per
    poll each "the time left", so the real worst case was about 63 s, past maxDuration."""
    A = world.A
    src = (APP / "build_web.py").read_text(encoding="utf-8")
    max_duration = int(re.search(r'"api/reset_password\.py": \{"memory": 256, "maxDuration": (\d+),', src).group(1))
    assert A.GITHUB_TIMEOUT + A.RUN_WAIT + A.NOTICE_TIMEOUT < A.RESET_DEADLINE, "every stage fits in the deadline"
    assert A.RESET_DEADLINE + 15 <= max_duration, "and the deadline well inside maxDuration (a cold start, PBKDF2)"


class _SlowGitHub(object):
    """FakeGitHub behind a slow network: every read takes `share` of the time it is given, or, with
    share=None, hangs for 5 seconds whatever it is given, as a stuck DNS lookup or a response that drips
    in would (a socket timeout bounds one operation, not a call). `release` lets the hung ones go."""

    def __init__(self, fake, share):
        self.fake, self.share, self.release = fake, share, threading.Event()

    def __call__(self, method, url, headers, body, timeout):
        if method == "GET":
            if self.share is None:
                self.release.wait(5)
                return 0, b""
            time.sleep(timeout * self.share)
        return self.fake(method, url, headers, body, timeout)


def _real_time(world, **limits):
    """The real clock and real sleeps, and the module's time limits scaled down to `limits`."""
    world.mp.setattr(world.A, "sleep", time.sleep)
    for name, value in limits.items():
        world.mp.setattr(world.A, name, value, raising=False)


def test_watching_the_run_keeps_to_its_deadline(world):
    """Second review, 25 Oct 2026: each poll made two reads, each given "the time left", so with a slow
    GitHub the 15-second watch took 29.8 s. Every read now gets what is left of ONE deadline, and is held
    to it in wall time even when the network ignores its timeout. Limits scaled down; real clock."""
    A = world.A
    _real_time(world, RUN_WAIT=1.2, RUN_POLL=0.2, GITHUB_TIMEOUT=1.2, MIN_CALL=0.05)
    world.github.add_run("started", world.now)               # running; its check never answers in time
    for share in (0.9, None):
        slow = _SlowGitHub(world.github, share)
        world.mp.setattr(A, "TRANSPORT", slow)
        began = time.monotonic()
        try:
            state = A.run_state(world.now)
        finally:
            took = time.monotonic() - began
            slow.release.set()
        assert took <= A.RUN_WAIT + 0.4, "share %s: a %.1f s watch took %.2f s" % (share, A.RUN_WAIT, took)
        assert state in (A.STARTED, A.UNKNOWN), state


class _StuckSMTP(object):
    """A mail server that takes the login and then never answers (for 5 seconds)."""

    def __init__(self, release):
        self.release = release

    def login(self, user, password):
        self.release.wait(5)

    def send_message(self, msg):
        pass

    def quit(self):
        pass


def test_a_reset_answers_inside_one_deadline_whatever_github_and_the_mail_do(world, capsys):
    """The whole reset under one deadline: GitHub hanging at the dispatch, or accepting it and then
    hanging on every read, and the mail server hanging on the notice. It answers before RESET_DEADLINE
    with what is true (nothing promised, or nothing handed over), never Vercel's 504 after a dispatch
    GitHub took. Limits scaled down; real clock."""
    A = world.A
    world.forgot()
    code = world.last_code()                  # the code email, through the real local mail server
    _real_time(world, RUN_WAIT=0.8, RUN_POLL=0.2, GITHUB_TIMEOUT=0.8, NOTICE_TIMEOUT=0.5,
               RESET_DEADLINE=2.4, MIN_CALL=0.05)
    release = threading.Event()
    world.mp.setattr(A, "SMTP_FACTORY", lambda host, port, timeout: _StuckSMTP(release))

    def stuck_dispatch(method, url, headers, body, timeout):
        release.wait(5)
        return 204, b""

    slow = _SlowGitHub(world.github, None)
    try:
        for label, transport, status, message in (("reads hang", slow, 200, A.PENDING),
                                                  ("the dispatch hangs", stuck_dispatch, 503, None)):
            world.mp.setattr(A, "TRANSPORT", transport)
            A.LIMITS.clear()
            began = time.monotonic()
            r = world.reset(code, "Brand-new-passphrase-9")
            took = time.monotonic() - began
            assert took <= A.RESET_DEADLINE + 0.4, "%s: the reset took %.2f s, deadline %.1f" % (
                label, took, A.RESET_DEADLINE)
            assert r.status == status and (message is None or r.payload["message"] == message), (label, r.payload)
    finally:
        release.set()
        slow.release.set()
    assert "[auth] reset: change notice timed out" in capsys.readouterr().err


def _workflow():
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data, data.get("on", data.get(True))


def test_the_workflow_takes_the_record_the_reset_page_sends(world):
    data, on = _workflow()
    assert world.A.WORKFLOW == WORKFLOW.name
    assert list(on) == ["workflow_dispatch"], "started only by the reset page (or by hand)"
    inputs = on["workflow_dispatch"]["inputs"]
    assert list(inputs) == ["record"] and inputs["record"]["required"] is True
    assert data["permissions"] == {"contents": "read", "actions": "read"}
    assert data["concurrency"] == {"group": "dashboard-password", "cancel-in-progress": False}


def test_the_workflow_never_puts_the_input_in_a_shell_line(world):
    data, _ = _workflow()
    job = data["jobs"]["apply"]
    assert "RECORD" not in job["env"], "only the steps that need the record get it"
    with_record = [s["name"] for s in job["steps"] if "RECORD" in (s.get("env") or {})]
    assert with_record == ["Refuse an old, malformed or one-too-many record", "Store it in Vercel"]
    for s in job["steps"]:
        if "RECORD" in (s.get("env") or {}):
            assert s["env"]["RECORD"] == "${{ inputs.record }}"
        assert "inputs." not in s.get("run", "") and "github.event" not in s.get("run", "")
    secrets = set(re.findall(r"secrets\.([A-Z_]+)", WORKFLOW.read_text(encoding="utf-8")))
    assert secrets == {"VERCEL_TOKEN", "VERCEL_ORG_ID", "VERCEL_PROJECT_ID"}


def test_vercel_token_reaches_only_the_vercel_steps_and_only_on_main(world):
    """Second review, 25 Oct 2026: workflow_dispatch takes any ref and runs that ref's code, and
    VERCEL_TOKEN sat in the whole job's env. So a branch's copy (an old one skips /api/reset_check, a
    work-in-progress one runs its own api/_spb_auth.py) could write SPB_PW_RECORD with it. The job now
    runs only for main, and only the three steps that call Vercel get the token."""
    data, _ = _workflow()
    job = data["jobs"]["apply"]
    assert job.get("if") == "github.ref == 'refs/heads/main'"
    assert "VERCEL_TOKEN" not in (job.get("env") or {}) and "VERCEL_TOKEN" not in (data.get("env") or {})
    with_token = [s["name"] for s in job["steps"] if "VERCEL_TOKEN" in (s.get("env") or {})]
    assert with_token == ["Wait for any production deploy in progress", "Store it in Vercel",
                          "Redeploy production so it takes effect"]
    for s in job["steps"]:
        calls_vercel = re.search(r"\bvercel (list|env|inspect|redeploy)\b", s.get("run", ""))
        if s.get("name") in with_token:
            assert s["env"]["VERCEL_TOKEN"] == "${{ secrets.VERCEL_TOKEN }}" and calls_vercel, s["name"]
        else:
            assert not calls_vercel, "%s calls Vercel without the token" % s.get("name", s.get("uses"))
    assert world.A.REF == "main", "the dashboard dispatches the one ref the job runs for"


def test_the_workflow_guards_then_waits_then_stores_then_redeploys_then_probes(world):
    data, _ = _workflow()
    runs = [s.get("run", "") for s in data["jobs"]["apply"]["steps"]]
    joined = "\n".join(runs)
    order = [joined.index(x) for x in (
        "python3 api/_spb_auth.py check-record", '"$SITE/api/reset_check"', '[ "$status" != "204" ]',
        '[ "$epoch" -le "$live" ]', "python3 api/_spb_auth.py count-applied", '[ "$recent" -ge "$MAX_PER_DAY" ]',
        "--status BUILDING,QUEUED,INITIALIZING", "vercel env add SPB_PW_RECORD", "vercel redeploy",
        "x-spb-auth-id:", '--data "email=probe')]
    assert order == sorted(order)
    names = [s.get("name") for s in data["jobs"]["apply"]["steps"]]
    assert world.A.STORE_STEP in names and world.A.CHECK_STEP in names, \
        "run_state() and count_applied() look for these steps by name"
    assert names.index(world.A.CHECK_STEP) < names.index(world.A.STORE_STEP)
    assert data["jobs"]["apply"]["env"]["MAX_PER_DAY"] == "10"
    add = next(r for r in runs if "vercel env add" in r)
    assert "printf '%s' \"$RECORD\" | vercel env add SPB_PW_RECORD" in add
    for flag in ("--environment production", "--force", "--sensitive", "--yes"):
        assert flag in add
    assert "vercel@latest" not in joined and "vercel@65.2.0" in joined
    wait = runs[-1]
    assert '"$SITE/stock.json"' in wait and '[ "$probe" != "401" ]' in wait and '[ "$seen_id" = "$ID" ]' in wait


# ------------------------------------------------------------------ the middleware, under Node

def _fixtures(world):
    """Records made by the real flow, plus the bad ones, for tests/forgot_password_checks.mjs."""
    A = world.A
    p1, p2 = "First-new-passphrase-1", "Second-new-passphrase-2"
    rec1 = world.full_reset(p1)
    world.apply(rec1)
    live1 = A.active_record()
    rec2 = world.full_reset(p2)
    parts = rec1.split(".")
    parts[5] = ("A" if parts[5][0] != "A" else "B") + parts[5][1:]
    world.configure(SITE_PASSWORD="a-different-site-password")
    rec_other_site = A.build_record(p1, 1, world.now)
    world.configure(SPB_AUTH_SECRET=OTHER_SECRET)
    rec_other_secret = A.build_record(p1, 1, world.now)
    world.configure()
    combos = []
    for change in ({}, {"SPB_AUTH_SECRET": None}, {"SPB_AUTH_SECRET": "x" * 31}, {"SPB_SMTP_PASSWORD": None},
                   {"GITHUB_DISPATCH_TOKEN": None}, {"SPB_RESET_TO": "nope"}, {"SPB_SMTP_USER": "a@b"},
                   {"SPB_SITE_URL": "http://x.example"}, {"SPB_SITE_URL": "https://staging.example.com"},
                   {"SPB_RESET_TO": "colin@example.com"}):
        world.configure(**change)
        combos.append({"env": {k: os.environ[k] for k in ENV_KEYS if k in os.environ},
                       "configured": world.A.configured()})
    world.configure()
    assert {c["configured"] for c in combos} == {True, False}
    return {"user": USER, "site": FAKE_SITE, "site2": "owner-changed-it-in-vercel", "secret": FAKE_SECRET,
            "otherSecret": OTHER_SECRET, "smtp": FAKE_SMTP_PASSWORD, "token": FAKE_TOKEN, "p1": p1, "p2": p2,
            "rec1": rec1, "rec2": rec2, "hash1": live1["hash"], "id1": rec1.split(".")[-1][:A.ID_LEN],
            "id2": rec2.split(".")[-1][:A.ID_LEN], "recTampered": ".".join(parts),
            "recOtherSite": rec_other_site, "recOtherSecret": rec_other_secret, "recMalformed": "v1.not-a-record",
            "loginOff": LOGIN_OFF_GOLDEN.read_text(encoding="utf-8"), "combos": combos}


def run_checks(tmp_path, fixtures, mw_text=None, only=""):
    """Run tests/forgot_password_checks.mjs against middleware.js as the build emits it (or mw_text)."""
    import build_web
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the middleware cannot be exercised here")
    mw = tmp_path / "middleware.mjs"
    mw.write_bytes((mw_text if mw_text is not None else build_web.MIDDLEWARE_JS).encode("utf-8"))
    fx = tmp_path / "fixtures.json"
    fx.write_text(json.dumps(fixtures), encoding="utf-8")
    env = dict(os.environ, SPB_MW_PATH=str(mw), SPB_FIXTURES=str(fx), SPB_ONLY=only)
    p = subprocess.run([node, str(CHECKS)], cwd=str(APP), env=env, capture_output=True, text=True,
                       encoding="utf-8", timeout=300)
    return p


def test_the_middleware_signs_in_against_the_flows_records(world, tmp_path):
    p = run_checks(tmp_path, _fixtures(world))
    print(p.stdout)                          # shown with -s: every check by name, and the PBKDF2 timing
    fails = [ln for ln in p.stdout.splitlines() if ln.lstrip().startswith("FAIL")]
    assert p.returncode == 0, "the middleware suite failed:\n" + "\n".join(fails) + "\n" + p.stderr[-2000:]
    assert re.search(r"ALL \d+ CHECKS PASSED", p.stdout)
