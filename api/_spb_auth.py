"""Forgot password for the dashboard: the code, the new password record, the email, the hand-off.

Colin, 23 Oct 2026 call: people have left his team and he wants to change the dashboard password
himself, without a developer. Ahsan: "it sends a code to digital@smartpropertybuying.com.au and she
can reset the password". This module is the server half; middleware.js (build_web.py, MIDDLEWARE_JS)
is the sign-in half. The owner's runbook is docs/password-reset-runbook.md.

    GET  /forgot                    middleware.js: one button, no email box
    POST /api/reset_code    {}      api/reset_code.py -> forgot_endpoint(): emails a code + link
    GET  /reset                     middleware.js: code + new password + confirm
    POST /api/reset_password        api/reset_password.py -> reset_endpoint(): checks the code, makes
         {code,password,confirm}    a signed, encrypted password record, hands it to GitHub (below)
    GitHub Actions                  .github/workflows/apply-dashboard-password.yml stores the record
                                    as the Vercel env var SPB_PW_RECORD and redeploys production
    POST /login                     middleware.js: signs in against SPB_PW_RECORD when it is set

NO STORAGE AT ALL, ON PURPOSE (23 Oct 2026). The first build kept the code and the new password in
Vercel Blob, and the sign-in check re-read Blob every 30 seconds. The team (digitalx-solutions-projects)
is on Vercel Hobby: 10,000 Blob operations a month, shared with the hook's hot-leads store, and a
30-day Blob lock-out when they run out. Normal use alone would have run them out, a stranger could
have done it in minutes, and the lock-out would have taken sign-in down for everyone. So:
  * the new password lives in an env var, which the middleware reads from memory: sign-in makes no
    network call of any kind, exactly as before this feature;
  * the code is STATELESS: HMAC(SPB_AUTH_SECRET, 5-minute time bucket + what signs in today). Nothing
    is written when a code is asked for or tried, so nothing here can be exhausted or raced.

THE CODE. 10 characters of Crockford base32 (50 bits), e.g. "7KQ2M-XR4TD". Valid for the bucket it
was made in and the three before the current one: 15 to 20 minutes. Bound to the current password
(the record's mac, or a fingerprint of SITE_PASSWORD), so the moment a new password goes live, or the
owner changes SITE_PASSWORD, every outstanding code dies. With 50 bits there is no attempt counter to
keep: four live codes in 2^50 is a 1-in-280-trillion chance per guess. (The first build used 6 digits
plus a counter; an independent review worked out that a patient stranger had about 1% a month, and the
shared counter let anyone burn Coleen's code. Both are gone.)

THE RECORD (SPB_PW_RECORD). One shell-safe line:
    v1.<epoch>.<unix time>.<fp>.<nonce>.<box>.<mac>
  epoch   one more than the last record's, so the reset page and the workflow can tell when it is live,
          and the workflow can refuse an older one (a re-run of an old run must not bring it back)
  fp      16 hex of HMAC(K_fp, SITE_PASSWORD): change SITE_PASSWORD in Vercel and the record is
          ignored on the spot, so the owner's value always wins (the break-glass)
  box     the PBKDF2-HMAC-SHA256 iterations, salt and hash of the new password, ENCRYPTED: XOR with an
          HMAC-SHA256 keystream (K_box, nonce). The record is a GitHub workflow input, and GitHub prints
          inputs into its logs, readable by anyone who can read the repo for 90 days. Encrypted, they
          see nothing to crack. (The stdlib has no AES, and this is the standard HMAC-in-counter-mode
          construction, authenticated by the mac below: encrypt-then-MAC.)
  mac     HMAC(K_record, everything before it). Checked FIRST, before fp: a record that does not verify
          is refused, never "ignored". (Ignoring it would mean a changed SPB_AUTH_SECRET quietly brings
          back the old SITE_PASSWORD, the one the people who left know. Review, 23 Oct 2026.)
All keys come from SPB_AUTH_SECRET, a secret set only on this Vercel project. Not SPB_SERVICE_KEY: the
hook holds that one and sends it on every research call.

NO VERCEL TOKEN HERE. This function holds GITHUB_DISPATCH_TOKEN, already on the project for the
Refresh button, which can start workflows and nothing else on Vercel. The token that writes the env var
and redeploys is the VERCEL_TOKEN GitHub secret the nightly already uses; it never enters this runtime.

LIMITS, AND WHY THEY ARE PER CLIENT. Each Vercel instance keeps them in memory. By the client's IP
(Vercel sets x-real-ip), so a stranger hammering the endpoints blocks only themselves, never Coleen:
  * code emails: 1 a minute and 3 an hour per IP, and 20 an hour per instance in all (Gmail's sending
    limit is the digital@ account's, which also sends the nightly's alerts);
  * wrong codes: 20 a minute per IP (a right code is never refused by this);
  * hand-offs to GitHub: 3 an hour per instance. Each one is a redeploy, Vercel Hobby allows 100 a day,
    and running out would stop the nightly publishing. The workflow also refuses more than 10 a day,
    counted from GitHub's own run history, which no instance can get around.

FAIL-SAFE: without SPB_AUTH_SECRET, SPB_SMTP_PASSWORD and GITHUB_DISPATCH_TOKEN, configured() is False,
the endpoints answer 503 "not set up yet", and middleware.js serves the sign-in page exactly as it was.

NO SECRET IS LOGGED. Log lines are fixed event names; an exception is logged by CLASS NAME only (an
SMTP or HTTP error can quote the request). The code, the password, the record, the SMTP password, the
dispatch token, SPB_AUTH_SECRET and SITE_PASSWORD never reach a log, a response or an email.
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
import threading
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
DEFAULT_TO = DEFAULT_USER                    # where codes go; SPB_RESET_TO overrides
DEFAULT_SITE_URL = "https://app.smartpropertybuying.com.au"   # for the link in the email; never the Host header

# Where the record is handed over. Hard-coded like api/refresh.py: a caller-supplied repo or workflow
# would make this a "dispatch anything as us" endpoint.
GITHUB_API = "https://api.github.com"
OWNER = os.environ.get("GITHUB_OWNER", "Jojo140893")
REPO = os.environ.get("GITHUB_REPO", "spb-ai-property-research-agent")
REF = os.environ.get("GITHUB_REF_NAME", "main")
WORKFLOW = "apply-dashboard-password.yml"

CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford base32: no I, L, O, U
CODE_LEN = 10
BUCKET = 300                                 # seconds per code bucket
BUCKETS = 4                                  # the current bucket and the three before it: 15-20 min

IP_GAP = 60                                  # per IP: seconds between two code emails
IP_PER_HOUR = 3                              # per IP: code emails in any rolling hour
SENDS_PER_HOUR = 20                          # per instance: code emails in all, any rolling hour
WRONG_PER_MINUTE = 20                        # per IP: wrong codes before that IP gets a 429
HANDOFFS_PER_HOUR = 3                        # per instance: records handed to GitHub (each is a redeploy)
MIN_FORGOT_SECONDS = 2.0                     # floor on the forgot answer, see forgot_endpoint
RECORD_MAX_AGE = 30 * 60                     # check-record refuses a record older than this

# 210,000 rounds of PBKDF2-HMAC-SHA256 measured 76 ms in Node's WebCrypto (the call middleware.js makes
# on every login POST, right or wrong) and 92 ms here, 23 Oct 2026. The middleware runs on Vercel's
# Node.js runtime (the live deployment's lambdaRuntimeStats say nodejs:1 beside the Python functions),
# not an edge isolate that caps PBKDF2 at 100,000, and the workflow proves it after every reset by
# POSTing a wrong password to /login and requiring 401. OWASP's figure for this PRF is 600,000 (243 ms);
# a login near a tenth of a second was preferred. Stored in the record, so raising it is a one-line
# change here that takes effect at the next reset. middleware.js refuses a record outside MIN..MAX.
PBKDF2_ITER = 210000
MIN_ITER = 100000
MAX_ITER = 1000000

MIN_PASSWORD = 12
MAX_PASSWORD = 128
MIN_SECRET = 32                              # SPB_AUTH_SECRET shorter than this is treated as unset

MAX_BODY = 4096                              # a code and a password, not a document
SMTP_TIMEOUT = 15
NOTICE_TIMEOUT = 6                           # the "changed" notice must not push a done reset past maxDuration
GITHUB_TIMEOUT = 15
# What we call ourselves in EHLO. smtplib otherwise asks the network for this machine's fully qualified
# name (a reverse DNS lookup the socket timeout does not cover); Gmail does not care what it is.
EHLO_NAME = "app.smartpropertybuying.com.au"

SUBJECT_CODE = "Smart Property Buying dashboard reset code"
SUBJECT_CHANGED = "Smart Property Buying dashboard password changed"

GENERIC_FORGOT = "If this site is set up for it, a code has been emailed."
NOT_SET_UP = "Password reset is not set up yet - ask the person who runs the dashboard."
CODE_INVALID = ("That code is not right, or it has expired. "
                "Ask for a new code if you are not sure.")
SAVED = ("Saved. Your new password starts working in about 2 minutes. "
         "Until then, keep using the old one.")

# Seams for the tests. Production leaves all of them alone; the tests set them so the clock, GitHub and
# the mail server are fakes and nothing real is ever called.
clock = time.time
sleep = time.sleep
TRANSPORT = None                             # (method, url, headers, body, timeout) -> HTTP status
SMTP_FACTORY = None                          # (host, port, timeout) -> an smtplib.SMTP-like object

RECORD_RE = re.compile(
    r"^v1\.([0-9]{1,9})\.([0-9]{10})\.([0-9a-f]{16})\.([A-Za-z0-9_-]{22})\.([A-Za-z0-9_-]{70})"
    r"\.([A-Za-z0-9_-]{43})$")
BOX_LEN = 4 + 16 + 32                        # iterations (big-endian), salt, hash
ID_LEN = 12                                  # how much of the mac names a record in x-spb-auth-id
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
_SITE_URL_RE = re.compile(r"^https://[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_IP_RE = re.compile(r"^[0-9A-Fa-f:.]{2,45}$")


def log(event):
    """One fixed line. Never an exception message, never a value that came from a secret."""
    sys.stderr.write("[auth] " + event + "\n")
    sys.stderr.flush()


class ConfigError(Exception):
    """SPB_PW_RECORD is set but cannot be trusted. middleware.js answers 503 for the same record."""


# ------------------------------------------------------------------ configuration

def _env(name):
    return (os.environ.get(name) or "").strip()


def site_username():
    return _env("SITE_USERNAME") or DEFAULT_USER


def smtp_user():
    return _env("SPB_SMTP_USER") or DEFAULT_USER


def reset_to():
    return _env("SPB_RESET_TO") or DEFAULT_TO


def site_url():
    return _env("SPB_SITE_URL") or DEFAULT_SITE_URL


def configured():
    """True only when the whole feature can work. Mirrors resetConfigured() in middleware.js; a test
    compares the two. Anything short of this and the dashboard behaves as it always did."""
    return bool(
        len(_env("SPB_AUTH_SECRET")) >= MIN_SECRET and _env("SPB_SMTP_PASSWORD")
        and _env("GITHUB_DISPATCH_TOKEN") and _env("SITE_PASSWORD")
        and _EMAIL_RE.match(reset_to()) and _EMAIL_RE.match(smtp_user())
        and _SITE_URL_RE.match(site_url()))


def mask_email(address):
    """d***@smartpropertybuying.com.au: enough for Coleen to recognise it, no more."""
    local, _, domain = address.partition("@")
    return (local[:1] or "*") + "***@" + domain


# ------------------------------------------------------------------ crypto

def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hmac(key, data):
    return hmac.new(key, data.encode("utf-8"), hashlib.sha256).digest()


def _key(label):
    """One key per purpose, all from SPB_AUTH_SECRET. Same derivation as authKey() in middleware.js."""
    return _hmac(_env("SPB_AUTH_SECRET").encode("utf-8"), "spb-auth|" + label + "|v1")


def env_fp(site_password=None):
    """What a record was written against. 64 bits of a keyed hash of SITE_PASSWORD."""
    pw = _env("SITE_PASSWORD") if site_password is None else site_password
    return _hmac(_key("env-fp"), pw).hex()[:16]


def normalise(password):
    """NFC, so a phone keyboard and a desktop one that type the same visible password agree."""
    return unicodedata.normalize("NFC", password)


def pbkdf2(password, salt, iterations):
    return hashlib.pbkdf2_hmac("sha256", normalise(password).encode("utf-8"), salt, iterations)


def _keystream(nonce):
    """64 bytes of HMAC-SHA256(K_box, "spb-pw-box|" + nonce + "|" + n) for n = 0, 1. Same as boxStream()."""
    key = _key("box")
    return _hmac(key, "spb-pw-box|%s|0" % nonce) + _hmac(key, "spb-pw-box|%s|1" % nonce)


def _xor(data, stream):
    return bytes(a ^ b for a, b in zip(data, stream))


# ------------------------------------------------------------------ the record

def parse_record(text):
    """The record's visible fields by shape alone (no secret needed), or None. Used by the GitHub
    workflow before it writes anything to Vercel, so a mangled value can never reach the env."""
    m = RECORD_RE.fullmatch(text or "")      # fullmatch: "$" alone would let a trailing newline in
    if not m:
        return None
    epoch, at, fp, nonce, box, mac = m.groups()
    if int(epoch) < 1:
        return None
    return {"epoch": int(epoch), "at": int(at), "fp": fp, "nonce": nonce, "box": box, "mac": mac,
            "id": mac[:ID_LEN], "body": text[:text.rindex(".")]}


def record_mac(body):
    return b64url(_hmac(_key("record"), body))


def active_record():
    """The record that signs people in right now, or None for "SITE_PASSWORD, as before".

    Same decision, in the same order, as evaluateRecord() in middleware.js:
      unset                         -> None
      malformed, or no secret       -> ConfigError (the middleware answers 503: it will not guess)
      mac fails                     -> ConfigError (so a changed SPB_AUTH_SECRET is refused, not ignored)
      fp is not today's             -> None (the owner changed SITE_PASSWORD; the owner wins)
      iterations out of range       -> ConfigError
    """
    raw = _env("SPB_PW_RECORD")
    if not raw:
        return None
    rec = parse_record(raw)
    if rec is None:
        raise ConfigError("SPB_PW_RECORD is not a record this version understands")
    if not _env("SPB_AUTH_SECRET"):
        raise ConfigError("SPB_PW_RECORD is set but SPB_AUTH_SECRET is not")
    if not hmac.compare_digest(rec["mac"], record_mac(rec["body"])):
        raise ConfigError("SPB_PW_RECORD failed its check")
    if not hmac.compare_digest(rec["fp"], env_fp()):
        return None
    plain = _xor(b64url_decode(rec["box"]), _keystream(rec["nonce"]))
    rec["iter"] = int.from_bytes(plain[:4], "big")
    if not (MIN_ITER <= rec["iter"] <= MAX_ITER):
        raise ConfigError("SPB_PW_RECORD is out of range")
    rec["salt"], rec["hash"] = plain[4:20], b64url(plain[20:52])
    return rec


def next_epoch():
    """One more than the last record's epoch, even an ignored one, so epochs never repeat."""
    rec = parse_record(_env("SPB_PW_RECORD"))
    return (rec["epoch"] if rec else 0) + 1


def build_record(password, epoch, now):
    salt = secrets.token_bytes(16)
    nonce = b64url(secrets.token_bytes(16))
    plain = PBKDF2_ITER.to_bytes(4, "big") + salt + pbkdf2(password, salt, PBKDF2_ITER)
    body = "v1.%d.%d.%s.%s.%s" % (epoch, int(now), env_fp(), nonce, b64url(_xor(plain, _keystream(nonce))))
    return body + "." + record_mac(body)


def is_current_password(password, rec):
    """Would this password change nothing? True for the password that signs in now, and for
    SITE_PASSWORD even after a reset: going back to the password the leavers knew is no change."""
    same = hmac.compare_digest(normalise(password).encode("utf-8"), _env("SITE_PASSWORD").encode("utf-8"))
    if rec is not None:
        got = b64url(pbkdf2(password, rec["salt"], rec["iter"]))
        same = hmac.compare_digest(got, rec["hash"]) or same
    return same


# ------------------------------------------------------------------ the code

def verifier_id(rec):
    """What the code is bound to: the live record, else SITE_PASSWORD's fingerprint."""
    return ("rec|" + rec["mac"]) if rec is not None else ("site|" + env_fp())


def code_for(bucket, vid):
    digest = _hmac(_key("code"), "code|%d|%s" % (bucket, vid))
    n = int.from_bytes(digest[:8], "big") >> (64 - 5 * CODE_LEN)
    return "".join(CODE_ALPHABET[(n >> (5 * (CODE_LEN - 1 - i))) & 31] for i in range(CODE_LEN))


def show_code(code):
    return code[:5] + "-" + code[5:]


def normalise_code(text):
    """What the person typed, as the 10 canonical characters, or None. Case, spaces and the hyphen do
    not matter, and the letters people mistake for digits (O, I, L) are read as the digits."""
    if not isinstance(text, str) or len(text) > 40:
        return None
    t = text.upper().replace(" ", "").replace("-", "")
    t = t.replace("O", "0").replace("I", "1").replace("L", "1")
    if len(t) != CODE_LEN or any(c not in CODE_ALPHABET for c in t):
        return None
    return t


def code_ok(code, now, vid):
    """Every live bucket is compared, in constant time, whatever matched first."""
    bucket = int(now // BUCKET)
    ok = False
    for k in range(BUCKETS):
        ok = hmac.compare_digest(code_for(bucket - k, vid), code) | ok
    return ok


class _Limiter(object):
    """Per instance, in memory, keyed by client IP where it matters (module header: LIMITS). NOT what
    makes the code safe (50 bits does that): it stops one client flooding the mailbox, running up
    function time, or spending the day's Vercel deploys."""

    def __init__(self):
        self.lock = threading.Lock()
        self.clear()

    def clear(self):
        self.sends = []                      # (time, ip) of every code email this instance sent
        self.wrong = {}                      # ip -> times of wrong codes
        self.handoffs = []                   # times of records handed to GitHub
        self.used = {}                       # code -> when it was spent here, so a double-click is refused

    def may_send(self, ip, now):
        with self.lock:
            self.sends = [(t, i) for t, i in self.sends if now - t < 3600]
            mine = [t for t, i in self.sends if i == ip]
            if (mine and now - mine[-1] < IP_GAP) or len(mine) >= IP_PER_HOUR \
                    or len(self.sends) >= SENDS_PER_HOUR:
                return False
            self.sends.append((now, ip))
            return True

    def too_many_wrong(self, ip, now):
        with self.lock:
            times = [t for t in self.wrong.get(ip, []) if now - t < 60]
            self.wrong[ip] = times
            return len(times) >= WRONG_PER_MINUTE

    def wrong_code(self, ip, now):
        with self.lock:
            self.wrong.setdefault(ip, []).append(now)

    def may_hand_off(self, now):
        with self.lock:
            self.handoffs = [t for t in self.handoffs if now - t < 3600]
            return len(self.handoffs) < HANDOFFS_PER_HOUR

    def handed_off(self, code, now):
        with self.lock:
            self.handoffs.append(now)
            self.used[code] = now

    def spent(self, code, now):
        with self.lock:
            self.used = {c: t for c, t in self.used.items() if now - t < BUCKET * (BUCKETS + 1)}
            return code in self.used


LIMITS = _Limiter()


def client_ip(headers):
    """The caller's IP as Vercel reports it (it sets x-real-ip and overwrites x-forwarded-for, so a
    client cannot choose its own). Anything that does not look like an IP becomes one shared bucket."""
    for name in ("x-real-ip", "x-forwarded-for"):
        value = (headers.get(name) or "").split(",")[0].strip()
        if _IP_RE.match(value):
            return value
    return "unknown"


# ------------------------------------------------------------------ the email

def code_mail_body(code):
    return ("Your Smart Property Buying dashboard reset code is:\n\n"
            "    %s\n\n"
            "Or open this link to choose a new password:\n"
            "%s/reset#code=%s\n\n"
            "It works for 15 minutes. If you did not ask for it, ignore this email and "
            "nothing changes.\n" % (show_code(code), site_url(), code))


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
    return ("A new password for the Smart Property Buying dashboard was set at %s.\n"
            "It starts working within a few minutes; until then the old password still works,\n"
            "and once it does, everyone who was signed in is signed out.\n\n"
            "If that was you there is nothing to do. If it was not, tell the person who runs the\n"
            "dashboard straight away (docs/password-reset-runbook.md, section 5).\n" % when)


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


def send_mail(subject, body, timeout=SMTP_TIMEOUT):
    password = _env("SPB_SMTP_PASSWORD")
    if not password:
        raise RuntimeError("no SMTP password")
    msg = build_message(subject, body)
    host, port = "smtp.gmail.com", 465
    if SMTP_FACTORY is not None:
        server = SMTP_FACTORY(host, port, timeout)
    else:
        server = smtplib.SMTP_SSL(host, port, local_hostname=EHLO_NAME, timeout=timeout,
                                  context=ssl.create_default_context())
    try:
        server.login(smtp_user(), password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:                                              # noqa: BLE001
            pass


def _why(exc):
    """The class name and, for SMTP, the numeric reply code. Never the message: it can quote input."""
    code = getattr(exc, "smtp_code", None)
    return type(exc).__name__ + (" %s" % code if isinstance(code, int) else "")


# ------------------------------------------------------------------ the hand-off to GitHub

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None                          # the token must never follow a redirect


_OPENER = urllib.request.build_opener(_NoRedirect)


def _urllib_transport(method, url, headers, body, timeout):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:      # 3xx (not followed), 4xx, 5xx: the status is enough
        return e.code
    except Exception:                                                  # noqa: BLE001
        return 0


def dispatch(record):
    """Start apply-dashboard-password.yml with the record. True when GitHub accepted it (204)."""
    url = "%s/repos/%s/%s/actions/workflows/%s/dispatches" % (GITHUB_API, OWNER, REPO, WORKFLOW)
    body = json.dumps({"ref": REF, "inputs": {"record": record}}).encode("utf-8")
    headers = {
        "Authorization": "Bearer " + _env("GITHUB_DISPATCH_TOKEN"),
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "spb-dashboard",
    }
    status = (TRANSPORT or _urllib_transport)("POST", url, headers, body, GITHUB_TIMEOUT)
    if status != 204:
        log("reset: GitHub did not accept the hand-off (%d)" % status)
    return status == 204


# ------------------------------------------------------------------ the request

def same_origin(headers):
    """A browser POST from our own page carries Origin: https://<our host>. Refuse anything else,
    including no Origin at all: both endpoints are only ever called by fetch() from /forgot and
    /reset, and a cross-site page must not be able to send a code or spend one."""
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


def forgot_endpoint(body, ip="unknown"):
    """POST /api/reset_code after the request checks. Always the same answer once configured."""
    started = time.monotonic()
    if body:
        return _refuse(400, "Send an empty JSON object: {}.")
    try:
        now = clock()
        if LIMITS.may_send(ip, now):
            code = code_for(int(now // BUCKET), verifier_id(active_record()))
            send_mail(SUBJECT_CODE, code_mail_body(code))
            log("forgot: code sent")
        else:
            log("forgot: limited")
    except Exception as e:                                             # noqa: BLE001
        log("forgot: not sent (%s)" % _why(e))
    # A floor on the time taken, so "limited" (instant) and "sent" (a Gmail session, usually under
    # two seconds) are hard to tell apart by stopwatch. The answer is the same sentence either way.
    sleep(max(0.0, MIN_FORGOT_SECONDS - (time.monotonic() - started)))
    return Reply(200, {"ok": True, "message": GENERIC_FORGOT})


def reset_endpoint(body, ip="unknown"):
    """POST /api/reset_password after the request checks. The order is the design:
      1. the request's shape and the rules the password breaks BY ITSELF (no secret involved);
      2. the code, in constant time against every live bucket (a wrong one counts against this IP);
      3. only with the right code: is the new password one that changes nothing?
      4. the record is made and handed to GitHub; only when GitHub takes it is anything promised."""
    if set(body) != {"code", "password", "confirm"}:
        return _refuse(400, "Send the code, the new password and its confirmation.")
    now = clock()
    if LIMITS.too_many_wrong(ip, now):
        return _refuse(429, "Too many wrong codes. Wait a minute and try again.", error="busy")
    code = normalise_code(body["code"])
    if code is None:
        LIMITS.wrong_code(ip, now)
        return _refuse(400, "The code is the 10 letters and digits from the email.",
                       error="bad_code_format")
    problem = password_problem(body["password"], body["confirm"])
    if problem:
        return _refuse(400, problem, error="weak_password")
    try:
        rec = active_record()
    except ConfigError:
        log("reset: SPB_PW_RECORD cannot be trusted")
        return _refuse(503, "Password reset is not working right now. Tell the person who runs the "
                            "dashboard.", error="unavailable")
    if not code_ok(code, now, verifier_id(rec)) or LIMITS.spent(code, now):
        LIMITS.wrong_code(ip, now)
        log("reset: wrong or expired code")
        return _refuse(400, CODE_INVALID, error="code_invalid")
    if is_current_password(body["password"], rec):
        return _refuse(400, "Use a password that is different from the current one.",
                       error="weak_password")
    if not LIMITS.may_hand_off(now):
        log("reset: hand-off limit reached")
        return _refuse(429, "The password has been changed several times in the last hour. "
                            "Wait an hour and try again.", error="busy")
    epoch = next_epoch()
    record = build_record(body["password"], epoch, now)
    if not dispatch(record):
        return _refuse(503, "Could not save that just now. Wait a minute and try again.",
                       error="unavailable")
    LIMITS.handed_off(code, now)
    log("reset: new password handed to GitHub")
    try:
        send_mail(SUBJECT_CHANGED, changed_mail_body(now), timeout=NOTICE_TIMEOUT)
    except Exception as e:                                             # noqa: BLE001
        log("reset: change notice not sent (%s)" % _why(e))
    return Reply(200, {"ok": True, "message": SAVED, "epoch": epoch, "id": parse_record(record)["id"]})


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
    return endpoint(body, client_ip(headers))


class AuthHandler(BaseHTTPRequestHandler):
    """Vercel Python runtime entrypoint base. Subclasses set `endpoint`.

    Reachable without a session ONLY because middleware.js lets exactly POST /api/reset_code and
    POST /api/reset_password through, and only while the feature is configured."""

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


def _main(argv):
    """`python api/_spb_auth.py check-record`: the GitHub workflow's gate before it writes anything to
    Vercel. Reads the record from the RECORD env var (not argv, which the process list shows) and checks:
      * its SHAPE (the mac needs SPB_AUTH_SECRET, which is deliberately not on GitHub; the middleware
        checks it, and answers 503 rather than accept a record it cannot verify);
      * its AGE: made in the last 30 minutes. A re-run of an old workflow run, or a record copied out of
        an old log, must not be able to put an older password back. (The workflow also refuses an epoch
        below the live one.)
    Prints "<epoch> <id>" for the workflow."""
    if argv[1:] != ["check-record"]:
        sys.stderr.write("usage: RECORD=... python api/_spb_auth.py check-record\n")
        return 2
    # Not stripped: the workflow stores RECORD exactly as given, so a value with any whitespace in it
    # is refused here rather than written to Vercel.
    rec = parse_record(os.environ.get("RECORD", ""))
    if rec is None:
        sys.stderr.write("not a password record: refusing to apply it\n")
        return 1
    age = time.time() - rec["at"]
    if not (-300 <= age <= RECORD_MAX_AGE):
        sys.stderr.write("this record was made %d minutes ago, not just now: refusing to apply it. A new "
                         "password needs a new reset from the dashboard, not a re-run.\n" % (age // 60))
        return 1
    print("%d %s" % (rec["epoch"], rec["id"]))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
