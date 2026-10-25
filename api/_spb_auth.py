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
    POST /api/reset_check {record}  api/reset_check.py -> check_endpoint(): 204 only for a record this
                                    dashboard signed. The workflow asks it before storing anything
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

ONLY A RECORD THIS DASHBOARD SIGNED IS EVER STORED (review, 25 Oct 2026). Anyone who can start the
workflow (GITHUB_DISPATCH_TOKEN, which every function on the project can read, or write access to the
repo) can hand it any record of the right shape. GitHub cannot check the mac: SPB_AUTH_SECRET is
deliberately not there. So the workflow's first step asks the live site, POST /api/reset_check, and
stops unless it answers 204: mac, fingerprint, iterations, age (now an authenticated field) and epoch,
by verify_record() below. Before this, one forged dispatch was stored and redeployed, and since the mac
is checked before the fingerprint, sign-in answered 503 for everyone, and a new SITE_PASSWORD did not
bring it back.

NOTHING SAID AT 15 SECONDS IS A PROMISE (reviews, 25 Oct 2026). GitHub accepts a dispatch (204) even
while it is refusing every job over billing, as it did on 30 Sep and 25 Oct: the run then fails in about
five seconds with no step run, and is never retried. So after the 204 reset_endpoint watches the run for
up to RUN_WAIT seconds (run_state()), until the workflow's own check step has answered. Refused, or
stopped before "Store it in Vercel": 503, "nothing changed", no notice, the code still works. Anything
else is a 200 that says only what is true at that moment: GitHub has started applying it (APPLYING), or
has not started yet (PENDING), and either way the old password works until the page says the new one
is live. Never "saved" and never a time: the check step can still refuse it after the answer, and
whoever holds the dispatch token can cancel any run (the second review: "Saved, starts working in about
2 minutes" went out for runs the daily cap then refused). Only the page's watch of /login says "live".
The "change requested" notice says no more than the page does.

ONE DEADLINE (review, 25 Oct 2026). reset_endpoint answers within RESET_DEADLINE (40 s, inside
maxDuration 60). The dispatch, every GitHub read and the notice are each held to what is left of it in
wall time (_bounded): a socket timeout bounds one operation, not a call, and two reads that were each
given "the time left" once took 29.8 s of a 15 s wait.

NO VERCEL TOKEN HERE. This function holds GITHUB_DISPATCH_TOKEN, already on the project for the
Refresh button, which can start workflows and nothing else on Vercel. The token that writes the env var
and redeploys is the VERCEL_TOKEN GitHub secret the nightly already uses; it never enters this runtime.

LIMITS, AND WHY NONE OF THEM LETS A STRANGER BLOCK COLEEN. Each Vercel instance keeps them in memory,
per client: the IP Vercel reports (x-real-ip), and for IPv6 the whole /64, the block one home
connection or one cloud machine is handed (limit_key()).
  * code emails: ONE per 5-minute window per instance, and only once it has actually gone out. Every
    request in a window would get that window's code anyway, so a request that sends nothing means the
    window's code reached the inbox in the last 5 minutes and works for at least 15 more, whoever asked
    for it. That also caps the mailbox at 12 code emails an hour per instance (Gmail's sending limit is
    the digital@ account's, which also sends the nightly's alerts) with no shared counter to use up.
    Per client on top: 1 a minute and 3 an hour. (Review, 25 Oct 2026: there was a shared ceiling of 20
    an hour; 21 requests from 7 addresses of one /64 used it up in 3 minutes, and Coleen's request was
    then told "emailed" while nothing was sent for the rest of the hour.)
  * wrong codes: 20 a minute per client (a right code is never refused by this);
  * hand-offs to GitHub: 3 an hour per instance. Each one is a redeploy, Vercel Hobby allows 100 a day,
    and running out would stop the nightly publishing. The workflow also refuses more than 10 a day,
    counted from GitHub's own run history, which no instance can get around (count_applied()). It counts
    only runs that got as far as storing a password: a run GitHub refused (billing) or that the check
    step refused (a forged record) changed nothing, and counting those let a forger, or a day of billing
    failures, use up the day's changes (review, 25 Oct 2026). A run GitHub refused to start is not
    counted here either (nothing ran); one that ran and stopped is.

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
import ipaddress
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
REF = "main"                                 # the workflow's job runs only on main (its `if:`)
WORKFLOW = "apply-dashboard-password.yml"

CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford base32: no I, L, O, U
CODE_LEN = 10
BUCKET = 300                                 # seconds per code bucket
BUCKETS = 4                                  # the current bucket and the three before it: 15-20 min

IP_GAP = 60                                  # per client: seconds between two code emails
IP_PER_HOUR = 3                              # per client: code emails in any rolling hour
# (No instance-wide count of code emails: at most one per window per instance, see _Limiter.)
WRONG_PER_MINUTE = 20                        # per client: wrong codes before that client gets a 429
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
# Watching the run a dispatch created (run_state). A run GitHub refuses over billing completes in about
# 5 s (run 36666518741, 30 Sep 2026).
RUN_WAIT = 15.0
RUN_POLL = 2.5
RUN_SKEW = 10                                # GitHub's created_at may read this much before our clock
MIN_CALL = 1.0                               # a GitHub read with less time than this left is not made
# One deadline for everything reset_endpoint does (module header: ONE DEADLINE). The dispatch
# (GITHUB_TIMEOUT), the watch (RUN_WAIT) and the notice (NOTICE_TIMEOUT) are 36 s at most, each held to
# it in wall time; 40 leaves 20 of reset_password.py's maxDuration of 60 (build_web.py) for a cold start.
RESET_DEADLINE = 40.0
MAX_GITHUB_BODY = 1 << 20                    # a runs or jobs listing, not a download
CHECK_STEP = "Refuse an old, malformed or one-too-many record"   # the workflow's own checks
STORE_STEP = "Store it in Vercel"            # the workflow step after which a record may be stored
APPLIED_WINDOW = 24 * 3600                   # the workflow's daily cap counts changes in this window
MAX_RUN_PAGES = 10                           # GitHub lists at most 1,000 runs for a `created` filter
# What we call ourselves in EHLO. smtplib otherwise asks the network for this machine's fully qualified
# name (a reverse DNS lookup the socket timeout does not cover); Gmail does not care what it is.
EHLO_NAME = "app.smartpropertybuying.com.au"

SUBJECT_CODE = "Smart Property Buying dashboard reset code"
# "requested", not "changed": it is sent once GitHub has the change, which is before it is live.
SUBJECT_CHANGED = "Smart Property Buying dashboard password change requested"

GENERIC_FORGOT = "If this site is set up for it, a code has been emailed."
NOT_SET_UP = "Password reset is not set up yet - ask the person who runs the dashboard."
CODE_INVALID = ("That code is not right, or it has expired. "
                "Ask for a new code if you are not sure.")
# GitHub has started the run (or it is past its checks, or has stored the record). Still not "saved" and
# no time: the check step may yet refuse it, and the dispatch token can cancel any run (review, 25 Oct
# 2026). Only the page's watch of /login says "live".
APPLYING = ("GitHub has started applying it. Your old password still works until this page says the "
            "new one is live.")
# GitHub took the change but had not started it within RUN_WAIT: nothing is promised.
PENDING = ("Sent to GitHub, which has not started applying it yet. Your old password still works "
           "until this page says the new one is live.")
# GitHub refused to start the run (billing, 30 Sep and 25 Oct 2026): it is dropped, never retried.
NOT_STARTED = ("GitHub could not run the change just now (it is refusing jobs). Nothing changed: "
               "your old password still works. Try again later.")
# The run started and stopped before it stored anything (the workflow's own checks refused it).
STOPPED = ("GitHub stopped the change before saving it. Nothing changed: your old password still "
           "works. If it happens again, tell the person who runs the dashboard.")
BUSY_HANDOFFS = ("Several password changes were started in the last hour. "
                 "Wait an hour and try again.")
USERNAME_RULE = "Do not use the sign-in email address as the password."

# Seams for the tests. Production leaves all of them alone; the tests set them so the clock, GitHub and
# the mail server are fakes and nothing real is ever called.
clock = time.time
sleep = time.sleep
TRANSPORT = None                             # (method, url, headers, body, timeout) -> (status, bytes)
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
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
# Where count-applied reads GitHub: the runner's GITHUB_API_URL (https), or a loopback stand-in in tests.
_API_RE = re.compile(r"^(https://[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+|http://127\.0\.0\.1:[0-9]{1,5})$")


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


def verify_record(text, now):
    """None when `text` is a record THIS dashboard signed, for today's SITE_PASSWORD, made in the last
    30 minutes and NEWER than the one in SPB_PW_RECORD; otherwise a fixed word saying why not. What
    POST /api/reset_check answers the workflow before it stores anything (review, 25 Oct 2026: the
    workflow could check only the shape, so a forged record was stored and sign-in answered 503).

    Newer, not "not older" (second review, 25 Oct 2026): two resets made before either is live share a
    number, so with "<" the one that lost could be replayed for up to 30 minutes after the other went
    live, bringing the older password back. Now, once a record is live, one of the same number is
    refused, and of two resets made at the same moment the second to arrive fails, and says so.

    The mac is checked first, so every answer after it is about a record only this dashboard could
    have made: someone without SPB_AUTH_SECRET learns "malformed" or "not_signed" and nothing else."""
    rec = parse_record(text)
    if rec is None:
        return "malformed"
    if len(_env("SPB_AUTH_SECRET")) < MIN_SECRET:
        return "unavailable"
    if not hmac.compare_digest(rec["mac"], record_mac(rec["body"])):
        return "not_signed"
    if not hmac.compare_digest(rec["fp"], env_fp()):
        return "other_site_password"          # it would only be ignored; the owner changed SITE_PASSWORD
    plain = _xor(b64url_decode(rec["box"]), _keystream(rec["nonce"]))
    if not (MIN_ITER <= int.from_bytes(plain[:4], "big") <= MAX_ITER):
        return "out_of_range"
    age = now - rec["at"]                     # `at` is covered by the mac, so this age is real
    if not (-300 <= age <= RECORD_MAX_AGE):
        return "stale"
    try:
        active_record()
    except ConfigError:
        return "live_record_broken"
    live = parse_record(_env("SPB_PW_RECORD"))
    if live is not None and rec["epoch"] <= live["epoch"]:
        return "not_newer_than_live"
    return None


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


SEND, LIMITED, ALREADY = "send", "limited", "already"     # _Limiter.send_decision


class _Limiter(object):
    """Per instance, in memory, keyed by client (limit_key) where it matters (module header: LIMITS).
    NOT what makes the code safe (50 bits does that): it stops one client flooding the mailbox, running
    up function time, or spending the day's Vercel deploys. Nothing in it is shared between clients in a
    way one client can use up for another."""

    def __init__(self):
        self.lock = threading.Lock()
        self.clear()

    def clear(self):
        self.sends = {}                      # client key -> times this client had a code email sent
        self.windows = {}                    # (bucket, verifier id) -> "sending" | "sent", this instance
        self.wrong = {}                      # client key -> times of wrong codes
        self.handoffs = []                   # times of records handed to GitHub
        self.used = {}                       # code -> when it was spent here, so a double-click is refused

    def send_decision(self, ip, now, window):
        """SEND (and the window is held for this request), LIMITED (this client asked too often) or
        ALREADY (this window's code went out, or is going out, from this instance).

        One email per (5-minute bucket, verifier id) per instance: everyone asking in a window would get
        the same code, so ALREADY always means that code reached the inbox in the last 5 minutes and is
        good for at least 15 more. Review, 25 Oct 2026: a shared 20-an-hour ceiling here let a stranger
        use up the hour, after which Coleen was told "emailed" and nothing was sent."""
        key = limit_key(ip)
        with self.lock:
            self.windows = {w: s for w, s in self.windows.items() if w[0] > window[0] - BUCKETS}
            self.sends = {k: ts for k, ts in ((k, [t for t in ts if now - t < 3600])
                                               for k, ts in self.sends.items()) if ts}
            mine = self.sends.get(key, [])
            if (mine and now - mine[-1] < IP_GAP) or len(mine) >= IP_PER_HOUR:
                return LIMITED
            if window in self.windows:
                return ALREADY
            self.windows[window] = "sending"
            self.sends[key] = mine + [now]
            return SEND

    def window_sent(self, window):
        with self.lock:
            self.windows[window] = "sent"

    def window_failed(self, window):
        """The email did not go out: free the window, so the next request (Coleen's) sends it."""
        with self.lock:
            if self.windows.get(window) == "sending":
                del self.windows[window]

    def too_many_wrong(self, ip, now):
        key = limit_key(ip)
        with self.lock:
            times = [t for t in self.wrong.get(key, []) if now - t < 60]
            self.wrong[key] = times
            return len(times) >= WRONG_PER_MINUTE

    def wrong_code(self, ip, now):
        with self.lock:
            self.wrong.setdefault(limit_key(ip), []).append(now)

    def may_hand_off(self, now):
        with self.lock:
            self.handoffs = [t for t in self.handoffs if now - t < 3600]
            return len(self.handoffs) < HANDOFFS_PER_HOUR

    def handed_off(self, now):
        with self.lock:
            self.handoffs.append(now)

    def spent(self, code, now):
        with self.lock:
            self.used = {c: t for c, t in self.used.items() if now - t < BUCKET * (BUCKETS + 1)}
            return code in self.used

    def use(self, code, now):
        """Held from before the dispatch, so a second click while this one waits on GitHub is refused."""
        with self.lock:
            self.used[code] = now

    def unuse(self, code):
        """Nothing was changed after all: the same code may try again."""
        with self.lock:
            self.used.pop(code, None)


LIMITS = _Limiter()


def client_ip(headers):
    """The caller's IP as Vercel reports it (it sets x-real-ip and overwrites x-forwarded-for, so a
    client cannot choose its own). Anything that does not look like an IP becomes one shared bucket."""
    for name in ("x-real-ip", "x-forwarded-for"):
        value = (headers.get(name) or "").split(",")[0].strip()
        if _IP_RE.match(value):
            return value
    return "unknown"


def limit_key(ip):
    """What the per-client limits count by. An IPv4 address is one client. An IPv6 address counts as its
    /64: that is the block one home connection or one cloud machine is handed, so stepping through it
    (review, 25 Oct 2026: 4 addresses of one /64 got 60 wrong codes a minute answered) is one client.
    Anything that is not an address is its own key, unchanged."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network((addr, 64), strict=False))
    return str(addr)


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
    # "requested": this goes out once GitHub has the change, before it is live (review, 25 Oct 2026:
    # "was set" went out for changes GitHub then refused to run, and nothing was ever set). No more than
    # the page says, and no time (second review, 25 Oct 2026): the workflow's checks may still refuse it.
    return ("A new password for the Smart Property Buying dashboard was requested at %s.\n"
            "It is not in use yet, and it may not be: GitHub can still refuse the change. The old\n"
            "password keeps working until the reset page says the new one is live. If it goes live,\n"
            "everyone who was signed in is signed out.\n\n"
            "If that was not you, tell the person who runs the dashboard straight away\n"
            "(docs/password-reset-runbook.md, section 5).\n" % when)


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
            return resp.status, resp.read(MAX_GITHUB_BODY)
    except urllib.error.HTTPError as e:      # 3xx (not followed), 4xx, 5xx: the status is enough
        return e.code, b""
    except Exception:                                                  # noqa: BLE001
        return 0, b""


def _bounded(seconds, fn, *args, **kwargs):
    """fn(*args, **kwargs) given at most `seconds` of wall time: ("ok", its value), ("error", what it
    raised) or ("late", None). A socket timeout bounds one operation, not a call (DNS has none, and an
    SMTP session is a dozen operations), so it cannot by itself keep a reset inside its deadline; this
    can. A call cut off here goes on in a daemon thread, and whatever it returns is dropped."""
    if seconds <= 0:
        return "late", None
    box = []

    def run():
        try:
            box.append(("ok", fn(*args, **kwargs)))
        except BaseException as e:                                     # noqa: BLE001
            box.append(("error", e))

    worker = threading.Thread(target=run, name="spb-bounded", daemon=True)
    worker.start()
    worker.join(seconds)
    return box[0] if box else ("late", None)


def _github(method, path, body=None, timeout=GITHUB_TIMEOUT, token=None, base=GITHUB_API):
    """One call to the GitHub API, with the dispatch token unless another is given: (status, bytes),
    within `timeout` seconds of wall time. Never raises."""
    headers = {
        "Authorization": "Bearer " + (_env("GITHUB_DISPATCH_TOKEN") if token is None else token),
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "spb-dashboard",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    kind, got = _bounded(timeout, TRANSPORT or _urllib_transport, method, base + path, headers, body, timeout)
    if kind != "ok" or not isinstance(got, tuple) or len(got) != 2:
        return 0, b""
    status, data = got
    return (status if isinstance(status, int) and not isinstance(status, bool) else 0), \
        (data if isinstance(data, bytes) else b"")


def dispatch(record, timeout=GITHUB_TIMEOUT):
    """Start apply-dashboard-password.yml with the record. True when GitHub accepted it (204). That is
    NOT the same as the run starting: see run_state()."""
    path = "/repos/%s/%s/actions/workflows/%s/dispatches" % (OWNER, REPO, WORKFLOW)
    body = json.dumps({"ref": REF, "inputs": {"record": record}}).encode("utf-8")
    status, _ = _github("POST", path, body, timeout=timeout)
    if status != 204:
        log("reset: GitHub did not accept the hand-off (%d)" % status)
    return status == 204


def _github_json(path, end, **kw):
    """GET `path` as a JSON object, or None. `end` is a time.monotonic() deadline: the call gets what is
    left of it (at most GITHUB_TIMEOUT), and is not made at all with less than MIN_CALL left."""
    left = min(GITHUB_TIMEOUT, end - time.monotonic())
    if left < MIN_CALL:
        return None
    status, data = _github("GET", path, timeout=left, **kw)
    if status != 200:
        return None
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    return obj if isinstance(obj, dict) else None


def _gh_time(text):
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


STARTED, REFUSED, STOPPED_EARLY, UNKNOWN = "started", "refused", "stopped", "unknown"   # run_state
RUNNING = "running"          # _run_state_once only: a step has run, the check step has not answered yet


def _steps_run(jobs):
    """{step name: (status, conclusion)} for every step that has run or is running, in every job
    listed. A skipped step did not run."""
    ran = {}
    for job in jobs:
        for step in ((job.get("steps") if isinstance(job, dict) else None) or []):
            if isinstance(step, dict) and isinstance(step.get("name"), str) \
                    and step.get("status") in ("in_progress", "completed") and step.get("conclusion") != "skipped":
                ran[step["name"]] = (step.get("status"), step.get("conclusion"))
    return ran


def _run_state_once(since, end):
    runs = _github_json("/repos/%s/%s/actions/workflows/%s/runs?event=workflow_dispatch&per_page=5"
                        % (OWNER, REPO, WORKFLOW), end)
    if runs is None or not isinstance(runs.get("workflow_runs"), list):
        return UNKNOWN
    ours = []
    for r in runs["workflow_runs"]:
        created = _gh_time(r.get("created_at")) if isinstance(r, dict) else None
        if created is not None and created >= since - RUN_SKEW and isinstance(r.get("id"), int) \
                and not isinstance(r.get("id"), bool):
            ours.append((abs(created - since), r))
    if not ours:
        return UNKNOWN                       # not listed yet
    run = min(ours, key=lambda c: c[0])[1]   # the one made closest to our dispatch
    status, conclusion = run.get("status"), run.get("conclusion")
    if status not in ("in_progress", "completed"):
        return UNKNOWN                       # queued, waiting, pending, requested: not started yet
    jobs = _github_json("/repos/%s/%s/actions/runs/%d/jobs" % (OWNER, REPO, run["id"]), end)
    if jobs is None or not isinstance(jobs.get("jobs"), list):
        return UNKNOWN
    ran = _steps_run(jobs["jobs"])
    if STORE_STEP in ran:
        return STARTED                       # stored, or storing: from here it can go live
    gate = ran.get(CHECK_STEP)
    if gate is not None and gate[0] == "completed":
        # The workflow's own checks have answered. Failed: no step after it runs (none has an `if:`),
        # so nothing is stored. Passed: GitHub is applying it, which is still no promise (the dispatch
        # token can cancel the run), so it is reported as started, never as saved.
        return STARTED if gate[1] == "success" else STOPPED_EARLY
    if status == "completed":
        # Nothing stored. Failed with no step run: GitHub never started it (billing: "The job was not
        # started because recent account payments have failed"). Anything else (steps run, so it stopped
        # on the way; cancelled, which a newer pending run in the concurrency group does to an older one,
        # or the token holder does to any run; or skipped, on a ref other than main): stopped.
        if not ran and conclusion in ("failure", "startup_failure"):
            return REFUSED
        return STOPPED_EARLY
    return RUNNING if ran else UNKNOWN


def run_state(since, end=None):
    """What GitHub did with the run our dispatch created, watched for at most RUN_WAIT seconds and never
    past `end` (a time.monotonic() deadline): STARTED (a step has run, or the run is past its checks),
    REFUSED (completed with nothing run: billing), STOPPED_EARLY (it ran and stopped before storing
    anything) or UNKNOWN (not started yet, or GitHub would not say).

    A run that has started is watched on, until its check step answers or the time is up, so that a run
    the workflow's own checks refuse inside the wait gets the honest "nothing changed" (second review,
    25 Oct 2026: it used to be called started, and so "saved", the moment "Set up job" ran).

    Review, 25 Oct 2026: GitHub answers 204 to a dispatch while it refuses every job over billing, and
    the run then dies in about 5 seconds and is never retried, so a 204 alone promised a password
    change that never happened. The token already reads runs for the Refresh button (api/refresh.py)."""
    stop = time.monotonic() + RUN_WAIT
    if end is not None:
        stop = min(stop, end)
    seen = UNKNOWN
    for _ in range(int(RUN_WAIT / RUN_POLL) + 1):   # a backstop: never more polls than this
        left = stop - time.monotonic()
        if left <= 0:
            break
        sleep(min(RUN_POLL, left))
        state = _run_state_once(since, stop)
        if state == RUNNING:
            seen = STARTED
        elif state != UNKNOWN:
            return state
    return seen


def count_applied(now, exclude_id, token, repo, base=GITHUB_API):
    """How many runs of this workflow created in the APPLIED_WINDOW before `now`, other than
    `exclude_id` (the run asking), got as far as storing a password: the store step ran in some attempt,
    or the run succeeded. The workflow's daily cap counts these and nothing else (review, 25 Oct 2026: it
    counted every run, so ten forged dispatches that /api/reset_check refused, or a day of runs GitHub
    refused over billing, used up the day's changes, and Colin was then refused by the cap).
    Raises RuntimeError whenever GitHub does not say, so the cap fails closed."""
    since = datetime.fromtimestamp(int(now) - APPLIED_WINDOW, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = {}
    for page in range(1, MAX_RUN_PAGES + 1):
        got = _github_json("/repos/%s/actions/workflows/%s/runs?created=%%3E%%3D%s&per_page=100&page=%d"
                           % (repo, WORKFLOW, since, page), time.monotonic() + GITHUB_TIMEOUT,
                           token=token, base=base)
        if got is None or not isinstance(got.get("workflow_runs"), list):
            raise RuntimeError("GitHub did not list this workflow's runs")
        for r in got["workflow_runs"]:
            rid = r.get("id") if isinstance(r, dict) else None
            if not isinstance(rid, int) or isinstance(rid, bool):
                raise RuntimeError("a run without an id")
            runs[rid] = r
        if len(got["workflow_runs"]) < 100:
            break
    else:
        raise RuntimeError("more runs than GitHub will list in a day")
    runs.pop(exclude_id, None)
    applied = 0
    for rid in sorted(runs):
        jobs = _github_json("/repos/%s/actions/runs/%d/jobs?filter=all&per_page=100" % (repo, rid),
                            time.monotonic() + GITHUB_TIMEOUT, token=token, base=base)
        if jobs is None or not isinstance(jobs.get("jobs"), list):
            raise RuntimeError("GitHub did not list a run's jobs")
        finished = any(isinstance(j, dict) and j.get("conclusion") == "success" for j in jobs["jobs"])
        if STORE_STEP in _steps_run(jobs["jobs"]) or (runs[rid].get("conclusion") == "success" and finished):
            applied += 1
    return applied


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
    """The first rule the password breaks BY ITSELF, as a sentence for the person; None when it is fine.

    Asked before the code, so nothing here may look at any configured value, secret or not: a wrong
    answer must be no oracle for anything. The username rule is NOT here (review, 25 Oct 2026: asked
    before the code, "do not use the sign-in email address" told anyone without a code whether a guess
    was SITE_USERNAME, which the login gate takes care to hide). reset_endpoint asks it after the code."""
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
    if pw != normalise(confirm):
        return "The two passwords do not match."
    return None


def is_username(password):
    """Asked only after the code is right: it compares against SITE_USERNAME."""
    return hmac.compare_digest(normalise(password).lower().encode("utf-8"),
                               site_username().lower().encode("utf-8"))


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
        window = (int(now // BUCKET), verifier_id(active_record()))
        decision = LIMITS.send_decision(ip, now, window)
        if decision == SEND:
            try:
                send_mail(SUBJECT_CODE, code_mail_body(code_for(*window)))
            except Exception:                                          # noqa: BLE001
                LIMITS.window_failed(window)  # not marked: the next request sends it
                raise
            LIMITS.window_sent(window)
            log("forgot: code sent")
        elif decision == ALREADY:
            log("forgot: this window's code was already sent")
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
      1. the request's shape and the rules the password breaks BY ITSELF (no configured value involved);
      2. the code, in constant time against every live bucket (a wrong one counts against this client);
      3. only with the right code: is the new password the sign-in email, or one that changes nothing?
      4. the record is made and handed to GitHub, and the run it starts is watched (run_state). One
         GitHub refused, or that stopped before storing, is "nothing changed"; anything else is a 200
         that promises nothing: started, or not started yet, and the old password works until the page
         says the new one is live.
    All of it inside one deadline, RESET_DEADLINE (module header: ONE DEADLINE)."""
    deadline = time.monotonic() + RESET_DEADLINE
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
    if is_username(body["password"]):
        return _refuse(400, USERNAME_RULE, error="weak_password")
    if is_current_password(body["password"], rec):
        return _refuse(400, "Use a password that is different from the current one.",
                       error="weak_password")
    if not LIMITS.may_hand_off(now):
        log("reset: hand-off limit reached")
        return _refuse(429, BUSY_HANDOFFS, error="busy")
    epoch = next_epoch()
    record = build_record(body["password"], epoch, now)
    LIMITS.use(code, now)
    # The notice's time is kept back from the two GitHub stages, so it always has its NOTICE_TIMEOUT.
    if not dispatch(record, timeout=min(GITHUB_TIMEOUT, deadline - NOTICE_TIMEOUT - time.monotonic())):
        LIMITS.unuse(code)
        return _refuse(503, "Could not save that just now. Wait a minute and try again.",
                       error="unavailable")
    state = run_state(now, deadline - NOTICE_TIMEOUT)
    if state in (REFUSED, STOPPED_EARLY):
        # Nothing was stored: no notice, and the code still works. A refused run cost nothing and is
        # not counted against the hand-offs; one that ran and stopped used a runner, so it is.
        LIMITS.unuse(code)
        if state == STOPPED_EARLY:
            LIMITS.handed_off(now)
        log("reset: GitHub %s the run; nothing changed" % ("refused" if state == REFUSED else "stopped"))
        return _refuse(503, NOT_STARTED if state == REFUSED else STOPPED, error="not_started")
    LIMITS.handed_off(now)
    log("reset: new password handed to GitHub (%s)" % ("run started" if state == STARTED
                                                       else "not started yet"))
    left = min(NOTICE_TIMEOUT, deadline - time.monotonic())
    kind, err = _bounded(left, send_mail, SUBJECT_CHANGED, changed_mail_body(now), timeout=max(0.1, left))
    if kind == "error":
        log("reset: change notice not sent (%s)" % _why(err))
    elif kind == "late":
        log("reset: change notice timed out")
    return Reply(200, {"ok": True, "message": APPLYING if state == STARTED else PENDING, "epoch": epoch,
                       "id": parse_record(record)["id"]})


def check_endpoint(body, ip="unknown"):
    """POST /api/reset_check {"record": ...}: 204 only when verify_record() passes. Called by the GitHub
    workflow (a server: no Origin), before it stores anything. It changes nothing and answers only with
    a fixed word, so it needs no session and no limit beyond the request checks."""
    if set(body) != {"record"} or not isinstance(body["record"], str):
        return _refuse(400, "Send the record.", error="malformed")
    why = verify_record(body["record"], clock())
    if why:
        log("check: record refused (%s)" % why)
        return _refuse(422, "That is not a record this dashboard can apply.", error=why)
    log("check: record confirmed")
    return Reply(204, None)


def respond(headers, rfile, endpoint, need_origin=True):
    """Everything between the socket and the endpoint: configured? same origin? JSON? small?
    need_origin is False only for /api/reset_check, which a server calls and which changes nothing."""
    if not configured():
        return _refuse(503, NOT_SET_UP, configured=False)
    if need_origin and not same_origin(headers):
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
    """Vercel Python runtime entrypoint base. Subclasses set `endpoint` (and `need_origin`).

    Reachable without a session ONLY because middleware.js lets exactly POST /api/reset_code,
    POST /api/reset_password and POST /api/reset_check through, and only while the feature is
    configured."""

    endpoint = None
    need_origin = True
    server_version = "spb"
    sys_version = ""

    def _reply(self, reply, extra=()):
        body = b"" if reply.status == 204 else json.dumps(reply.payload).encode("utf-8")
        self.send_response(reply.status)
        if reply.status != 204:              # a 204 carries neither (RFC 9110 15.3.5, 8.6)
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
            reply = respond(self.headers, self.rfile, type(self).endpoint, type(self).need_origin)
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
    """`python api/_spb_auth.py check-record`: the GitHub workflow's FIRST gate, before it asks anyone
    anything. Reads the record from the RECORD env var (not argv, which the process list shows) and checks:
      * its SHAPE, so nothing malformed is ever sent on, let alone written;
      * its AGE by the claimed `at`: made in the last 30 minutes. A re-run of an old workflow run, or a
        record copied out of an old log, must not be able to put an older password back.
    Neither proves the dashboard made it: the mac needs SPB_AUTH_SECRET, which is deliberately not on
    GitHub. So the same step then asks the live site (POST /api/reset_check, verify_record above), which
    checks the mac and, under it, the age and the epoch again, and stops unless the answer is 204.
    Prints "<epoch> <id>" for the workflow.

    `python api/_spb_auth.py count-applied`: the same step's daily cap. Prints how many other runs of the
    workflow in the last 24 hours got as far as storing a password (count_applied), reading GitHub with
    GH_TOKEN at GITHUB_API_URL for GITHUB_REPOSITORY, leaving out GITHUB_RUN_ID (all set by the runner).
    Exits 1 whenever GitHub does not say, so the cap fails closed."""
    if argv[1:] == ["count-applied"]:
        return _count_applied_main()
    if argv[1:] != ["check-record"]:
        sys.stderr.write("usage: RECORD=... python api/_spb_auth.py check-record | count-applied\n")
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


def _count_applied_main():
    token = os.environ.get("GH_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    base = (os.environ.get("GITHUB_API_URL") or GITHUB_API).rstrip("/")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not token or not _REPO_RE.match(repo) or not _API_RE.match(base) or not re.fullmatch(r"[0-9]{1,20}", run_id):
        sys.stderr.write("count-applied needs GH_TOKEN, GITHUB_REPOSITORY, GITHUB_API_URL and GITHUB_RUN_ID\n")
        return 1
    try:
        n = count_applied(time.time(), int(run_id), token, repo, base)
    except RuntimeError as e:
        sys.stderr.write("could not count this workflow's recent changes (%s)\n" % e)
        return 1
    print(n)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
