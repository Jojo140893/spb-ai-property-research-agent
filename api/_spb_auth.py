"""Forgot password for the dashboard: the code, the store, the email and the reset.

Ahsan, 23 Oct 2026: "add a Forgot password: it sends a code to digital@smartpropertybuying.com.au
and she (Coleen) can reset the password". This module is the server half. The sign-in half is
middleware.js (build_web.py, MIDDLEWARE_JS), which reads what this module writes.

    browser -> GET  /forgot                       middleware.js: a page with one button, no email box
    browser -> POST /api/auth/forgot   {}         api/auth/forgot.py -> forgot(): a 6-digit code is made,
                                                  only a KEYED HASH of it is stored, the code is emailed
    browser -> GET  /reset                        middleware.js: code + new password + confirm
    browser -> POST /api/auth/reset {code,password,confirm}
                                                  api/auth/reset.py -> reset_password(): code checked in
                                                  constant time, burned, the new password stored as PBKDF2
    browser -> POST /login                        middleware.js: checks the stored password when one exists

THE ONE ADDRESS IS FIXED. There is no email field anywhere. The code goes to
digital@smartpropertybuying.com.au (SPB_RESET_TO overrides it), so nobody can aim the email at
themselves and nobody can learn which addresses exist.

TWO RECORDS in a PRIVATE Vercel Blob store (Blob is the only storage this project has; see the
runbook, docs/password-reset-runbook.md):

  site-auth/reset.json     the code in flight and the rate-limit history. Written by forgot() and
                           reset_password(), read by nobody else.
      {"v":1, "state":"open"|"burned", "nonce":..., "code_mac":..., "issued_at":..., "expires_at":...,
       "attempts_left":5, "issued":[recent request times, for the rate limit]}
      code_mac = HMAC(key derived from SPB_SERVICE_KEY, "reset-code|" + nonce + "|" + code). The code
      itself is never stored, and the hash is KEYED and bound to a random NONCE, so a copy of the
      store alone cannot be ground through the million possible codes, and a hash lifted from one
      request cannot be replayed against another.

  site-auth/password.json  the password Coleen chose. Written by reset_password(), read by this module
                           and by middleware.js.
      {"v":1, "algo":"pbkdf2-sha256", "iter":N, "salt":..., "hash":..., "env_fp":..., "epoch":N,
       "updated_at":"...Z", "mac":...}
      env_fp = first 16 hex of HMAC(SPB_SERVICE_KEY, "sitepw|" + SITE_PASSWORD). If the owner later
      changes SITE_PASSWORD (or rotates SPB_SERVICE_KEY) in Vercel, env_fp stops matching and the
      stored password is IGNORED, so the owner's value in Vercel always wins: that is the break-glass.
      mac covers every other field (key derived from SPB_SERVICE_KEY). Not in the brief; added because
      without it anybody holding the Blob token could copy the real env_fp out of the record and write
      their own hash next to it. With it they also need SPB_SERVICE_KEY.

EVERYTHING IS A COMPARE-AND-SWAP. Vercel Blob offers conditional writes (x-if-match with the ETag
of what was read; 412 when somebody else wrote first) and create-only writes (x-allow-overwrite: 0).
Every read-modify-write below uses them and retries on a conflict, so:
  * two requests cannot both spend one code: the "burn" is the conditional write, and only one can
    win it. A code is used at most once, whatever the interleaving.
  * a wrong guess is counted BEFORE the answer is sent. If its decrement cannot be stored the
    caller is told "busy", never "wrong", so a guess is never evaluated for free.
  * the code is burned BEFORE the password is written. If the second write then fails the code is
    gone and the person asks for another: the failure direction is always "nothing changed".
Reads use ?cache=0: Blob serves reads through a CDN that may return the previous version of an
overwritten blob for up to 60 seconds, and a stale read of reset.json would hand out a burned code.

FAIL-SAFE: with BLOB_READ_WRITE_TOKEN, SPB_SMTP_PASSWORD or SPB_SERVICE_KEY missing, configured() is
False, the endpoints answer 503 "not set up yet" and middleware.js behaves exactly as it did before
this feature existed.

NO SECRET IS LOGGED. Log lines are fixed event names; an exception is logged by CLASS NAME only (an
SMTP or HTTP error message can quote the request). The code, the password, the SMTP password, the Blob
token, SPB_SERVICE_KEY and SITE_PASSWORD never reach a log, a response or an email.
"""
import base64
import email.utils
import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler

# ------------------------------------------------------------------ settings

DEFAULT_USER = "digital@smartpropertybuying.com.au"
DEFAULT_TO = DEFAULT_USER                    # where the code goes; SPB_RESET_TO overrides
RESET_PATH = "site-auth/reset.json"
PASSWORD_PATH = "site-auth/password.json"

CODE_TTL = 15 * 60                           # seconds a code works
ATTEMPTS = 5                                 # wrong tries before a code is burned
MIN_GAP = 60                                 # seconds between two code requests
PER_HOUR = 3                                 # code requests in any rolling hour
MIN_FORGOT_SECONDS = 1.2                     # floor on the forgot answer, see forgot_endpoint
CAS_TRIES = 6                                # re-reads after a conflicting write

# 210,000 rounds of PBKDF2-HMAC-SHA256 measured 76 ms in Node's WebCrypto (the call middleware.js
# makes on a login) and 92 ms here, 23 Oct 2026. OWASP's current figure for this PRF is 600,000
# (243 ms in Node); the cost lands on every login POST, right or wrong, so it was set where one
# login stays near a tenth of a second on a slow vCPU. Stored per record, so raising it later is a
# one-line change here and the next reset. middleware.js refuses a record outside MIN..MAX.
PBKDF2_ITER = 210000
MIN_ITER = 100000
MAX_ITER = 1000000

MIN_PASSWORD = 12
MAX_PASSWORD = 128

MAX_BODY = 4096                              # a code and a password, not a document
MAX_RECORD = 16384
BLOB_TIMEOUT = 8
SMTP_TIMEOUT = 15
# What we call ourselves in EHLO. smtplib otherwise asks the network for this machine's fully
# qualified name (a reverse DNS lookup that the socket timeout does not cover and that took 2 s on
# the test laptop); a fixed name costs nothing and Gmail does not care what it is.
EHLO_NAME = "app.smartpropertybuying.com.au"

SUBJECT_CODE = "Smart Property Buying dashboard password code"
SUBJECT_CHANGED = "Smart Property Buying dashboard password changed"

GENERIC_FORGOT = "If this site is set up for it, a code has been emailed."
NOT_SET_UP = "Password reset is not set up yet - ask the person who runs the dashboard."
CODE_INVALID = ("That code is not right, or it has expired or already been used. "
                "Ask for a new code if you are not sure.")

# Seams for the tests. Production leaves all of them alone; tests/authkit sets them so the clock,
# the network and the mail server are fakes and nothing real is ever called.
clock = time.time
sleep = time.sleep
TRANSPORT = None                             # (method, url, headers, body, timeout) -> (status, headers, body)
SMTP_FACTORY = None                          # (host, port, timeout) -> an smtplib.SMTP-like object

_TOKEN_RE = re.compile(r"^vercel_blob_rw_([A-Za-z0-9]+)_\S+$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
_B64_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_CODE_RE = re.compile(r"^[0-9]{6}$")


def log(event):
    """One fixed line. Never an exception message, never a value that came from a secret."""
    sys.stderr.write("[auth] " + event + "\n")
    sys.stderr.flush()


class StoreError(Exception):
    """The store could not be read or written. The text is a status or a class name only."""


class Conflict(Exception):
    """A conditional write lost to somebody else's write."""


# ------------------------------------------------------------------ configuration

def _env(name):
    return (os.environ.get(name) or "").strip()


def site_username():
    return _env("SITE_USERNAME") or DEFAULT_USER


def blob_token():
    """BLOB_READ_WRITE_TOKEN, or "" when it is missing or cannot be a Blob token."""
    token = _env("BLOB_READ_WRITE_TOKEN")
    return token if _TOKEN_RE.match(token) else ""


def smtp_user():
    return _env("SPB_SMTP_USER") or DEFAULT_USER


def reset_to():
    return _env("SPB_RESET_TO") or DEFAULT_TO


def configured():
    """True only when the whole feature can work. Mirrors resetConfigured() in middleware.js; a
    test compares the two. Anything short of this and the dashboard behaves as it always did."""
    return bool(
        blob_token() and _env("SPB_SMTP_PASSWORD") and _env("SPB_SERVICE_KEY")
        and _EMAIL_RE.match(reset_to()) and _EMAIL_RE.match(smtp_user()))


def mask_email(address):
    """d***@smartpropertybuying.com.au: enough for Coleen to recognise it, no more."""
    local, _, domain = address.partition("@")
    return (local[:1] or "*") + "***@" + domain


# ------------------------------------------------------------------ crypto

def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(text):
    if not isinstance(text, str) or not _B64_RE.match(text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hmac(key, data):
    return hmac.new(key, data.encode("utf-8"), hashlib.sha256).digest()


def _service_key():
    return _env("SPB_SERVICE_KEY").encode("utf-8")


def _reset_key():
    return _hmac(_service_key(), "spb-auth|reset-code-key|v1")


def _record_key():
    return _hmac(_service_key(), "spb-auth|record-mac|v1")


def env_fp():
    """What the stored password was written against. 64 bits of a keyed hash: enough that a changed
    SITE_PASSWORD never collides, and too little to be worth grinding for the password."""
    return _hmac(_service_key(), "sitepw|" + _env("SITE_PASSWORD")).hex()[:16]


def code_mac(key, nonce, code):
    return b64url(_hmac(key, "reset-code|" + nonce + "|" + code))


def pbkdf2(password, salt, iterations):
    return hashlib.pbkdf2_hmac("sha256", normalise(password).encode("utf-8"), salt, iterations)


def normalise(password):
    """NFC, so a phone keyboard and a desktop one that type the same visible password agree."""
    return unicodedata.normalize("NFC", password)


def record_mac(rec):
    """Covers every field but itself. Same canonical string as middleware.js."""
    canon = "|".join(str(rec[k]) for k in
                     ("v", "algo", "iter", "salt", "hash", "env_fp", "epoch", "updated_at"))
    return b64url(_hmac(_record_key(), "spb-pw-record|" + canon))


def iso(now):
    return datetime.fromtimestamp(int(now), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ the Blob store

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None                          # the Authorization header must never follow a redirect


_OPENER = urllib.request.build_opener(_NoRedirect)


def _urllib_transport(method, url, headers, body, timeout):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read(MAX_RECORD + 1)
    except urllib.error.HTTPError as e:      # 3xx (not followed), 4xx, 5xx
        try:
            data = e.read(MAX_RECORD + 1)
        except Exception:                                              # noqa: BLE001
            data = b""
        return e.code, {k.lower(): v for k, v in e.headers.items()}, data
    except Exception as e:                                             # noqa: BLE001
        raise StoreError(type(e).__name__)


class BlobStore:
    """The two calls this feature needs against a PRIVATE Vercel Blob store, over the REST API the
    official SDK (@vercel/blob 2.x) speaks:

      read  GET https://<storeId>.private.blob.vercel-storage.com/<pathname>?cache=0
            Authorization: Bearer <token>.  200 + ETag header, or 404 when there is no such blob.
      write PUT https://vercel.com/api/blob/?pathname=<pathname>
            Authorization, x-api-version, x-vercel-blob-store-id, x-vercel-blob-access: private,
            x-add-random-suffix: 0, x-content-type, and one of
              x-if-match: <etag>      overwrite only if the blob still has this ETag (412 if not)
              x-allow-overwrite: 0    create only (an error if the blob already exists)
            200 + JSON {url, downloadUrl, pathname, contentType, contentDisposition, etag}.
    """

    API = "https://vercel.com/api/blob"
    API_VERSION = "12"

    def __init__(self, token=None):
        self.token = token or blob_token()
        m = _TOKEN_RE.match(self.token)
        if not m:
            raise StoreError("no usable Blob token")
        self.store_id = m.group(1)

    def _send(self, method, url, headers, body=None):
        send = TRANSPORT or _urllib_transport
        return send(method, url, headers, body, BLOB_TIMEOUT)

    def read(self, pathname):
        """(text, etag), or (None, None) when the blob does not exist. StoreError on anything else."""
        url = "https://%s.private.blob.vercel-storage.com/%s?cache=0" % (
            self.store_id.lower(), urllib.parse.quote(pathname, safe="/"))
        status, headers, body = self._send("GET", url, {"authorization": "Bearer " + self.token})
        if status == 404:
            return None, None
        if status != 200:
            raise StoreError("read answered %d" % status)
        if len(body) > MAX_RECORD:
            raise StoreError("record too large")
        etag = headers.get("etag") or ""
        if not etag:
            raise StoreError("read carried no ETag")
        return body.decode("utf-8", "replace"), etag

    def write(self, pathname, text, if_match=None, create_only=False):
        """Store `text` and return its ETag. Exactly one of if_match / create_only must be given:
        an unconditional overwrite is something this feature never does."""
        if bool(if_match) == bool(create_only):
            raise ValueError("a write is either conditional on an ETag or create-only")
        headers = {
            "authorization": "Bearer " + self.token,
            "x-api-version": self.API_VERSION,
            "x-vercel-blob-store-id": self.store_id,
            "x-vercel-blob-access": "private",
            "x-add-random-suffix": "0",
            "x-content-type": "application/json",
            "x-allow-overwrite": "1" if if_match else "0",
        }
        if if_match:
            headers["x-if-match"] = if_match
        status, _hdrs, body = self._send(
            "PUT", self.API + "/?" + urllib.parse.urlencode({"pathname": pathname}),
            headers, text.encode("utf-8"))
        if status == 412 or (create_only and status in (400, 409)):
            raise Conflict()
        if status != 200:
            raise StoreError("write answered %d" % status)
        try:
            meta = json.loads(body.decode("utf-8"))
            etag, url, name = meta["etag"], meta["url"], meta["pathname"]
        except Exception:                                              # noqa: BLE001
            raise StoreError("write answer was not understood")
        # A record that landed anywhere but this exact private pathname would be unreadable or,
        # worse, public. Refuse to carry on as if it had worked.
        if name != pathname or ".private.blob." not in str(url):
            raise StoreError("blob landed somewhere unexpected")
        return etag


def default_store():
    return BlobStore()


def _load(text):
    if text is None:
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


# ------------------------------------------------------------------ the stored password

def valid_override(rec):
    """The password record, if it is one this deployment wrote and still stands behind; else None.

    None for: a record written against another SITE_PASSWORD / SPB_SERVICE_KEY (the owner changed
    one in Vercel, so the owner's value wins), and for anything malformed or whose mac fails."""
    if not isinstance(rec, dict) or type(rec.get("v")) is not int or rec["v"] != 1:
        return None
    if not isinstance(rec.get("env_fp"), str):
        return None
    if not hmac.compare_digest(rec["env_fp"].encode("utf-8"), env_fp().encode("ascii")):
        return None
    try:
        if rec.get("algo") != "pbkdf2-sha256":
            return None
        # type() is int, not isinstance: True is an int in Python and must not pass for 1.
        if not (type(rec.get("iter")) is int and MIN_ITER <= rec["iter"] <= MAX_ITER):
            return None
        if not (type(rec.get("epoch")) is int and rec["epoch"] >= 1):
            return None
        if len(b64url_decode(rec.get("salt"))) < 8 or len(b64url_decode(rec.get("hash"))) != 32:
            return None
        if not (isinstance(rec.get("updated_at"), str) and _ISO_RE.match(rec["updated_at"])):
            return None
        mac = rec.get("mac")
        if not isinstance(mac, str) or not _B64_RE.match(mac):
            return None
        if not hmac.compare_digest(mac.encode("ascii"), record_mac(rec).encode("ascii")):
            return None
    except (ValueError, KeyError, TypeError):
        return None
    return rec


def is_current_password(store, password):
    """Is `password` what signs Coleen in right now? Either the value in Vercel or the stored one."""
    site = _env("SITE_PASSWORD")
    same = hmac.compare_digest(normalise(password).encode("utf-8"), site.encode("utf-8"))
    text, _etag = store.read(PASSWORD_PATH)
    rec = valid_override(_load(text))
    if rec is not None:
        got = b64url(pbkdf2(password, b64url_decode(rec["salt"]), rec["iter"]))
        same = hmac.compare_digest(got.encode("ascii"), rec["hash"].encode("ascii")) or same
    return same


def _new_password_record(password, epoch, now):
    salt = secrets.token_bytes(16)
    rec = {
        "v": 1, "algo": "pbkdf2-sha256", "iter": PBKDF2_ITER,
        "salt": b64url(salt), "hash": b64url(pbkdf2(password, salt, PBKDF2_ITER)),
        "env_fp": env_fp(), "epoch": epoch, "updated_at": iso(now),
    }
    rec["mac"] = record_mac(rec)
    return rec


# ------------------------------------------------------------------ the code

def _active(rec, now):
    try:
        return (rec.get("v") == 1 and rec.get("state") == "open"
                and isinstance(rec.get("nonce"), str) and isinstance(rec.get("code_mac"), str)
                and rec["expires_at"] > now and int(rec["attempts_left"]) > 0)
    except (KeyError, TypeError, ValueError):
        return False


def _burned(rec, now):
    """The same record with no code left in it. `issued` stays, or a success would reset the rate limit."""
    return {"v": 1, "state": "burned", "burned_at": int(now), "attempts_left": 0,
            "issued": list(rec.get("issued") or [])}


def _recent(issued, now):
    out = []
    for t in issued if isinstance(issued, list) else []:
        if isinstance(t, (int, float)) and not isinstance(t, bool) and now - 3600 < t <= now + 300:
            out.append(t)
    return out


def rate_limited(issued, now):
    """At most 1 code per 60 s and 3 per rolling hour."""
    recent = _recent(issued, now)
    return any(now - t < MIN_GAP for t in recent) or len(recent) >= PER_HOUR


def forgot(store=None):
    """Make a code, store its keyed hash, email it. Returns "sent", "limited" or "failed".

    The HTTP answer is the same generic sentence for all three: what happened is for the log."""
    store = store or default_store()
    key = _reset_key()
    for _ in range(CAS_TRIES):
        text, etag = store.read(RESET_PATH)
        old = _load(text)
        now = clock()
        issued = _recent(old.get("issued") if old else [], now)
        if rate_limited(issued, now):
            log("forgot: limited")
            return "limited"
        code = "%06d" % secrets.randbelow(1000000)
        nonce = b64url(secrets.token_bytes(16))
        rec = {"v": 1, "state": "open", "nonce": nonce, "code_mac": code_mac(key, nonce, code),
               "issued_at": int(now), "expires_at": int(now) + CODE_TTL, "attempts_left": ATTEMPTS,
               "issued": (issued + [int(now)])[-10:]}
        try:
            if etag:
                store.write(RESET_PATH, json.dumps(rec), if_match=etag)
            else:
                store.write(RESET_PATH, json.dumps(rec), create_only=True)
        except Conflict:
            continue
        # Stored first: a code that is emailed but cannot be checked is worse than one that is
        # stored and lost. If the mail fails the person asks again after a minute.
        try:
            send_mail(SUBJECT_CODE, code_mail_body(code))
        except Exception as e:                                         # noqa: BLE001
            log("forgot: mail failed (%s)" % _why(e))
            return "failed"
        log("forgot: code sent")
        return "sent"
    log("forgot: store was busy")
    return "failed"


def reset_password(code, password, store=None):
    """Spend a code on a new password. Returns one of:
        "ok"       changed
        "code"     the code is wrong, expired, burned or was never asked for (all look the same)
        "same"     the code was right but the password is what already signs people in (code kept)
        "busy"     could not settle it against concurrent writers; nothing was decided
    Raises StoreError when the store cannot be read or written (nothing was decided)."""
    store = store or default_store()
    key = _reset_key()
    for _ in range(CAS_TRIES):
        text, etag = store.read(RESET_PATH)
        rec = _load(text)
        now = clock()
        if rec is None or not _active(rec, now):
            return "code"
        if not _matches(key, rec, code):
            nxt = dict(rec)
            nxt["attempts_left"] = int(rec["attempts_left"]) - 1
            burned = nxt["attempts_left"] <= 0
            if burned:
                nxt = _burned(rec, now)
            try:
                store.write(RESET_PATH, json.dumps(nxt), if_match=etag)
            except Conflict:
                continue
            log("reset: wrong code" + (", code burned" if burned else ""))
            return "code"
        # Right code. Only now is it safe to say anything about the password.
        if is_current_password(store, password):
            return "same"
        try:
            store.write(RESET_PATH, json.dumps(_burned(rec, now)), if_match=etag)
        except Conflict:
            continue                         # somebody changed the record under us: judge it afresh
        break
    else:
        log("reset: store was busy")
        return "busy"
    # The code is spent and is ours alone. Store the password.
    for _ in range(CAS_TRIES):
        text, etag = store.read(PASSWORD_PATH)
        prev = _load(text)
        epoch = prev["epoch"] + 1 if prev and isinstance(prev.get("epoch"), int) and prev["epoch"] >= 1 else 1
        rec = _new_password_record(password, epoch, clock())
        try:
            if etag:
                store.write(PASSWORD_PATH, json.dumps(rec), if_match=etag)
            else:
                store.write(PASSWORD_PATH, json.dumps(rec), create_only=True)
        except Conflict:
            continue
        log("reset: password changed")
        try:
            send_mail(SUBJECT_CHANGED, changed_mail_body(clock()))
        except Exception as e:                                         # noqa: BLE001
            log("reset: change notice not sent (%s)" % _why(e))
        return "ok"
    log("reset: could not store the password")
    raise StoreError("password record stayed contended")


def _matches(key, rec, code):
    expect = code_mac(key, rec["nonce"], code)
    try:
        return hmac.compare_digest(expect.encode("ascii"), rec["code_mac"].encode("ascii"))
    except (UnicodeEncodeError, AttributeError):
        return False


def _why(exc):
    """The class name and, for SMTP, the numeric reply code. Never the message: it can quote input."""
    code = getattr(exc, "smtp_code", None)
    return type(exc).__name__ + (" %s" % code if isinstance(code, int) else "")


# ------------------------------------------------------------------ the email

def code_mail_body(code):
    return ("Your Smart Property Buying dashboard password code is %s.\n\n"
            "It works for 15 minutes. If you did not ask for it, ignore this email and "
            "nothing changes.\n" % code)


def changed_mail_body(now):
    stamp = datetime.fromtimestamp(int(now), timezone.utc)
    when = stamp.strftime("%d %b %Y %H:%M UTC")
    try:
        from zoneinfo import ZoneInfo
        local = stamp.astimezone(ZoneInfo("Australia/Melbourne"))
        when = "%s %s (%s)" % (local.strftime("%d %b %Y %H:%M"), local.strftime("%Z"),
                                stamp.strftime("%H:%M UTC"))
    except Exception:                                                  # noqa: BLE001
        pass                                 # no tz database on this host: UTC alone is still right
    return ("Your Smart Property Buying dashboard password was changed at %s.\n\n"
            "If that was you there is nothing to do. If it was not, ask the person who runs the "
            "dashboard to change SITE_PASSWORD in Vercel: that cancels this change and signs "
            "everyone out.\n" % when)


def build_message(subject, body):
    """Plain text, From the sending mailbox, To the fixed address. Nothing from a request is in a
    header. EmailMessage would also refuse a CR or LF in a header value; the address check is the
    first line, that is the second."""
    sender, to = smtp_user(), reset_to()
    if not (_EMAIL_RE.match(sender) and _EMAIL_RE.match(to)):
        raise ValueError("not a plain email address")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg["Date"] = email.utils.formatdate(clock(), usegmt=True)
    msg.set_content(body)
    return msg


def send_mail(subject, body):
    password = _env("SPB_SMTP_PASSWORD")
    if not password:
        raise RuntimeError("no SMTP password")
    msg = build_message(subject, body)
    host, port = "smtp.gmail.com", 465
    if SMTP_FACTORY is not None:
        server = SMTP_FACTORY(host, port, SMTP_TIMEOUT)
    else:
        server = smtplib.SMTP_SSL(host, port, local_hostname=EHLO_NAME, timeout=SMTP_TIMEOUT,
                                  context=ssl.create_default_context())
    try:
        server.login(smtp_user(), password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:                                              # noqa: BLE001
            pass


# ------------------------------------------------------------------ the request

def same_origin(headers):
    """A browser POST from our own page carries Origin: https://<our host>. Refuse anything else,
    including no Origin at all: both endpoints are only ever called by fetch() from /forgot and
    /reset, and a cross-site page must not be able to start a code or spend one."""
    origin = (headers.get("Origin") or "").strip()
    if not origin or origin == "null":
        return False
    try:
        parts = urllib.parse.urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme != "https" or not parts.netloc or parts.path not in ("", "/") \
            or parts.query or parts.fragment or parts.username is not None:
        return False
    hosts = set()
    for name in ("Host", "X-Forwarded-Host"):
        value = (headers.get(name) or "").split(",")[0].strip().lower()
        if value:
            hosts.add(value)
    fetch_site = (headers.get("Sec-Fetch-Site") or "").strip().lower()
    if fetch_site and fetch_site not in ("same-origin", "none"):
        return False
    return parts.netloc.lower() in hosts


def parse_json(raw):
    """A JSON object, strictly: UTF-8, no duplicate keys, no NaN/Infinity. ValueError otherwise."""
    def _no_dupes(pairs):
        seen = {}
        for k, v in pairs:
            if k in seen:
                raise ValueError("duplicate key")
            seen[k] = v
        return seen

    def _no_constant(name):
        raise ValueError("bad constant")

    obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_dupes, parse_constant=_no_constant)
    if not isinstance(obj, dict):
        raise ValueError("not an object")
    return obj


def password_problem(password, confirm):
    """The first rule the password breaks, as a sentence for the person; None when it is fine.

    Nothing here looks at any secret, so a wrong answer is no oracle for anything."""
    if not isinstance(password, str) or not isinstance(confirm, str):
        return "Send a password and its confirmation."
    pw = normalise(password)
    if _CONTROL_RE.search(pw):
        return "The password cannot contain control characters such as tabs or line breaks."
    if pw != pw.strip():
        return "Do not start or end the password with a space."
    if len(pw) < MIN_PASSWORD:
        return "Use at least %d characters." % MIN_PASSWORD
    if len(pw) > MAX_PASSWORD:
        return "Use %d characters or fewer." % MAX_PASSWORD
    if pw.lower() == site_username().lower():
        return "Do not use the sign-in email address as the password."
    if pw != normalise(confirm):
        return "The two passwords do not match."
    return None


class Reply(object):
    def __init__(self, status, payload):
        self.status, self.payload = status, payload


def _refuse(status, message, **extra):
    payload = {"ok": False, "message": message}
    payload.update(extra)
    return Reply(status, payload)


def forgot_endpoint(body):
    """POST /api/auth/forgot after the request checks. Always the same answer once configured."""
    started = time.monotonic()
    if body:
        return _refuse(400, "Send an empty JSON object: {}.")
    try:
        forgot()
    except Exception as e:                                             # noqa: BLE001
        log("forgot: store failed (%s)" % _why(e))
    # A floor on the time taken, so "limited" (instant) cannot be told from "sent" (a second or two
    # of SMTP) by a stopwatch.
    sleep(max(0.0, MIN_FORGOT_SECONDS - (time.monotonic() - started)))
    return Reply(200, {"ok": True, "message": GENERIC_FORGOT})


def reset_endpoint(body):
    """POST /api/auth/reset after the request checks."""
    if set(body) != {"code", "password", "confirm"}:
        return _refuse(400, "Send the code, the new password and its confirmation.")
    code = body["code"]
    if not (isinstance(code, str) and _CODE_RE.match(code)):
        return _refuse(400, "The code is 6 digits.", error="bad_code_format")
    problem = password_problem(body["password"], body["confirm"])
    if problem:
        return _refuse(400, problem, error="weak_password")
    try:
        verdict = reset_password(code, body["password"])
    except Exception as e:                                             # noqa: BLE001
        log("reset: store failed (%s)" % _why(e))
        return _refuse(503, "Could not save that just now. Wait a minute and try again.",
                       error="unavailable")
    if verdict == "ok":
        return Reply(200, {"ok": True, "message": "Password changed."})
    if verdict == "same":
        return _refuse(400, "Use a password that is different from the current one.",
                       error="weak_password")
    if verdict == "busy":
        return _refuse(503, "That is busy right now. Wait a few seconds and try again.",
                       error="busy")
    return _refuse(400, CODE_INVALID, error="code_invalid")


def respond(headers, rfile, endpoint):
    """Everything between the socket and the endpoint: configured? same origin? JSON? small?"""
    if not configured():
        return _refuse(503, NOT_SET_UP, configured=False)
    if not same_origin(headers):
        return _refuse(403, "That request was not accepted.")
    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return _refuse(415, "Send JSON.")
    if headers.get("Transfer-Encoding"):
        return _refuse(411, "Send a Content-Length.")
    try:
        length = int(headers.get("Content-Length") or "")
    except ValueError:
        return _refuse(411, "Send a Content-Length.")
    if length < 0:
        return _refuse(400, "That request is not valid.")
    if length > MAX_BODY:
        return _refuse(413, "That request is too large.")
    try:
        body = parse_json(rfile.read(length))
    except (ValueError, RecursionError):
        return _refuse(400, "That request is not valid JSON.")
    return endpoint(body)


class AuthHandler(BaseHTTPRequestHandler):
    """Vercel Python runtime entrypoint base. Subclasses set `endpoint`.

    Reachable without a session ONLY because middleware.js lets exactly POST /api/auth/forgot and
    POST /api/auth/reset through; everything else under /api answers 401 there."""

    endpoint = None
    server_version = "spb"
    sys_version = ""

    def _reply(self, reply, extra=()):
        body = json.dumps(reply.payload).encode("utf-8")
        self.send_response(reply.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            reply = respond(self.headers, self.rfile, type(self).endpoint)
        except Exception as e:                                         # noqa: BLE001
            log("endpoint crashed (%s)" % _why(e))
            reply = _refuse(500, "Something went wrong. Try again in a minute.")
        self._reply(reply)

    def _not_allowed(self):
        self._reply(_refuse(405, "Method not allowed."), extra=(("Allow", "POST"),))

    do_GET = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _not_allowed

    def log_message(self, fmt, *args):
        # Request line and status: no body, no header. The code and the password ride in the body.
        sys.stderr.write("[auth] " + (fmt % args) + "\n")
