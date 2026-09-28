"""The Clare's Leads tab's feed: every caller Agent 1 graded on a call and stored.

Agent 1 is the voice agent. When a call ends, its webhook (vapi-kommo-hook) grades the
caller and writes every hot caller, and every warm or nurture caller who gave a state, to
that project's private Blob store together with the brief, the Kommo lead, the shortlist
this dashboard ran for them and, for a hot caller only, the booked appointment. A hot
caller with no state is still stored, for the booking, but has no shortlist: the research
needs a state. Until 15 Sep only hot callers were stored (Ahsan widened it so Agent 2 runs
for every caller with a state). This endpoint and the hook's hot-leads/ records keep their
old names so nothing stored moves. The tab reads them back through here.

    browser -> GET /api/hot_leads[?limit=N][&include_test=1][&grade=hot|warm|nurture]
                                                            (this file, behind the login)
            -> GET vapi-kommo-hook/api/hot-leads       (x-spb-service-key: SPB_SERVICE_KEY)

WHY A PROXY AND NOT A FETCH FROM THE PAGE. The hook answers only a caller holding
SPB_SERVICE_KEY. Anything index.html holds, every viewer can read -- so a key placed in the
page would be published, and with it every hot lead's name, phone number and email. This
endpoint holds the key in the Vercel project environment, is reachable only through the
site login, and sends it on one server-to-server request. The key is never returned, never
logged, and never reaches the page.

WHAT COMES BACK. On success, the hook's JSON -- the tab and the hook share one record format,
and a second copy of that shape here would be a second thing to keep in step -- in one of two
views, and the top level says which:
  contact: "full"    the request carries a valid spb_contact cookie (api/contact_unlock.py):
                     the hook's JSON untouched, with contact and contactUnlock added (and a
                     lead's notes the hook sent as null -- could not be read -- left out and
                     named in notesErrors, as in the masked view).
  contact: "masked"  everyone else. Coleen, 28 Sep 2026: "Please only display the last 3
                     digits of the customer's mobile number ... Not everyone on the team
                     should have access to the customer's full email address or phone
                     number." Each lead's customer.email and customer.phone are "", with
                     phoneLast3, hasEmail and hasPhone instead; recordingUrl is ""; and every
                     free-text string, notes included, is scrubbed of email addresses and phone
                     numbers. The rules are in api/_contact.py. A body that cannot be masked
                     is a 502, never the raw leads.
  contactUnlock: "password" when SPB_CONTACT_PASSWORD is set here, "not-configured" when not,
  so the tab can say which of "enter the password" and "ask for one to be set" applies.
Both views send Vary: Cookie beside Cache-Control: no-store, so no cache can hand one person's
full view to another.

On a failure, a message that says what went wrong, under one of two statuses:
  503  a setting is missing, and refreshing will not fix it: no SPB_SERVICE_KEY here, or
       the hook answered that it lacks one of its own settings or that the keys disagree.
       The message names the project to fix.
  502  the hook could not be reached, timed out, or failed some other way that may pass.
"No leads" and "could not ask" must never look the same on the tab: an empty list is
a fact about the leads, and a failed read is not.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

# The contact-details rules live beside this file (build_web.py bundles api/_contact.py with
# every function that imports it). The directory goes on sys.path the way research.py puts
# it there for _bootstrap: on Vercel the function's folder is not guaranteed to be.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _contact  # noqa: E402

DEFAULT_URL = "https://vapi-kommo-hook.vercel.app/api/hot-leads"
TIMEOUT = 20
HEADER = "x-spb-service-key"

# ?limit= is forwarded only as a plain whole number. Anything else -- "abc", "5.5", "-1",
# a second parameter smuggled inside it -- is dropped, and the hook applies its own default
# (50, at most 200). The value is rebuilt from its digits rather than copied, so nothing the
# browser sends is spliced into the upstream URL. ASCII digits only: \d would also accept
# Arabic-Indic and other Unicode digits.
_LIMIT = re.compile(r"[0-9]{1,6}")


def _key():
    return (os.environ.get("SPB_SERVICE_KEY") or "").strip()


def _upstream():
    return (os.environ.get("SPB_HOT_LEADS_URL") or "").strip() or DEFAULT_URL


def _error(message):
    """The body of every failure. Scrubbed of the key last, whatever built the message."""
    key = _key()
    text = str(message)
    if key and key in text:                                           # never, but cheap
        text = text.replace(key, "[redacted]")
    return json.dumps({"status": "error", "message": text}).encode("utf-8")


# Half a surrogate pair: JSON.stringify writes one as "\ud83d" when a .slice() cuts an emoji
# in half, json.loads reads it back, and UTF-8 has no way to write it.
_LONE_SURROGATE = re.compile("[" + chr(0xD800) + "-" + chr(0xDFFF) + "]")


def _body(doc):
    """`doc` as the UTF-8 JSON of a 200, in either view (this file's and api/lead_notes.py's),
    and of the note api/lead_notes.py forwards to the hook.

    28 Sep 2026 (second review): one lone surrogate anywhere in the hook's answer -- a
    summary, a note, a callId -- failed the encode, and the whole answer was a 502 in both
    views, with a message that blamed the contact details. It is sent as U+FFFD, which is
    what a browser draws for it anyway (api/_contact.py plain() does the same in the masked
    view's text)."""
    text = json.dumps(doc, ensure_ascii=False)
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        return _LONE_SURROGATE.sub(chr(0xFFFD), text).encode("utf-8")


def limit_from(path):
    """The ?limit= to forward, as a canonical integer string, or None."""
    query = urllib.parse.urlsplit(path or "").query
    values = urllib.parse.parse_qs(query).get("limit") or []
    if values and _LIMIT.fullmatch(values[0]):
        return str(int(values[0]))
    return None


def include_test_from(path):
    """True only for exactly one ?include_test= whose value is exactly "1".

    The hook keeps the records it marks as tests off the list unless include_test=1 is
    sent, so a test call never sits on Coleen's tab looking like a buyer to ring. Turning them back on is a deliberate act from a page opened with
    ?hl_test=1, so nothing looser counts: not "true", "01" or "1.0", and not a repeated
    parameter whose values disagree. Like limit, it is forwarded as a literal written
    here, never as the browser's text.
    """
    query = urllib.parse.urlsplit(path or "").query
    return urllib.parse.parse_qs(query).get("include_test") == ["1"]


# The grades the hook stores a record for since 15 Sep (Agent 2 runs for every caller who
# gives a state, not only hot ones). A closed caller is not researched or stored, so there
# is no "closed" to ask for.
GRADES = ("hot", "warm", "nurture")


def grade_from(path):
    """The ?grade= to forward -- "hot", "warm" or "nurture" -- or None.

    The tab filters the list it already holds and sends no grade; this is for a reader that
    wants one grade from the hook. Like include_test, only an exact answer counts: one grade
    parameter whose value is exactly one of the three, in lower case. "HOT", "closed",
    "hot,warm", " hot" or a repeated parameter forward nothing, and the hook answers with
    every grade. What is forwarded is the literal from GRADES, never the browser's text.
    """
    query = urllib.parse.urlsplit(path or "").query
    values = urllib.parse.parse_qs(query).get("grade") or []
    if len(values) == 1 and values[0] in GRADES:
        return GRADES[GRADES.index(values[0])]
    return None


def _upstream_message(raw):
    """The hook's own `message` field, which names the failure ("bad key", "hot-lead store
    not configured"). Only that field: the rest of an error body is not ours to repeat."""
    try:
        body = json.loads((raw or b"").decode("utf-8", "replace"))
    except ValueError:
        return ""
    msg = body.get("message") if isinstance(body, dict) else ""
    if not isinstance(msg, str):
        return ""
    key = _key()
    if key and key in msg:
        return ""
    return " ".join(msg.split())[:160]


HOOK_PROJECT = "vapi-kommo-hook"


def _failure(code, detail):
    """(dashboard status, message) for a hook answer outside 2xx.

    TWO KINDS OF FAILURE, AND THE TAB MUST TELL THEM APART. A hook that is down or slow
    comes right on its own, so it is a 502 and the tab says "press Refresh in a minute". A
    hook that is MISSING A SETTING never comes right on its own: this is the state straight
    after a deploy that set SPB_SERVICE_KEY on the dashboard and not yet on the hook. As a
    502 it read as "try again", someone kept refreshing, and whoever looked checked the
    dashboard project, which already had the key. So a settings failure is a 503, the
    status the tab already reads as "needs a setting", and the message names the project
    the setting is missing FROM.

    Never 401: the tab reads a 401 from this endpoint as "your sign-in expired".
    """
    said = f"hot-lead store answered HTTP {code}" + (f": {detail}" if detail else "")
    low = (detail or "").lower()
    if code == 401:
        # Two projects, one secret. The hook says "bad key" when the two disagree; a 401
        # with no JSON reason is most likely Vercel deployment protection in front of it.
        if "key" in low:
            return 503, (f"the dashboard's SPB_SERVICE_KEY does not match the one on the "
                         f"{HOOK_PROJECT} project ({said})")
        return 503, (f"the {HOOK_PROJECT} project refused the dashboard ({said}); check that "
                     f"its Vercel deployment protection is off and SPB_SERVICE_KEY is the "
                     f"same on both projects")
    if code in (500, 503) and "not configured" in low:
        if "spb_service_key" in low:
            what = "SPB_SERVICE_KEY is not configured"
        elif "store" in low:
            what = "the hot-lead store is not configured (BLOB_READ_WRITE_TOKEN)"
        else:
            what = "a setting is not configured"
        return 503, f"{what} on the {HOOK_PROJECT} project ({said})"
    return 502, said


def _why(reason):
    """A network failure, described without echoing anything it might carry."""
    if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
        return f"timed out after {TIMEOUT}s"
    if isinstance(reason, BaseException):
        return type(reason).__name__
    return " ".join(str(reason).split())[:80]


def fetch(limit=None, include_test=False, grade=None):
    """(http_status, body_bytes) for the tab. Never raises, and never puts the key in a body."""
    key = _key()
    if not key:
        # Says which knob is missing, without hinting at its value.
        return 503, _error("SPB_SERVICE_KEY is not set on the dashboard project")

    parts = urllib.parse.urlsplit(_upstream())
    if parts.scheme != "https" or not parts.hostname:
        # Refused BEFORE the key is attached. A plain-http override would send the secret
        # in the clear, and urllib would happily read a file:// one off the function's disk.
        return 503, _error("SPB_HOT_LEADS_URL must be an https:// URL")
    query = parts.query
    extra = []
    if limit is not None:
        extra.append(("limit", limit))
    if include_test:
        extra.append(("include_test", "1"))
    # Checked again here, not only in grade_from: whatever calls fetch, only one of the
    # three literals can reach the upstream URL.
    if grade in GRADES:
        extra.append(("grade", grade))
    if extra:
        query = (query + "&" if query else "") + urllib.parse.urlencode(extra)
    url = urllib.parse.urlunsplit(parts._replace(query=query, fragment=""))
    # hostname, not netloc: netloc would carry any user:password@ written into the URL.
    host = parts.hostname

    req = urllib.request.Request(url, method="GET", headers={
        "Accept": "application/json", "User-Agent": "spb-dashboard"})
    # UNREDIRECTED, deliberately. urllib copies ordinary headers onto a redirect, to
    # whichever host the redirect names -- so a hook that ever answered 30x (a Vercel
    # protection page, a moved domain) would have the key handed on by the default opener.
    # An unredirected header goes to this URL and nowhere else; a redirect arrives without
    # it and fails like any other bad answer.
    req.add_unredirected_header(HEADER, key)

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            status = getattr(r, "status", None) or r.getcode()
            raw = r.read()
    except urllib.error.HTTPError as e:
        try:
            detail = _upstream_message(e.read())
        except Exception:                                              # noqa: BLE001
            detail = ""
        code, msg = _failure(e.code, detail)
        return code, _error(msg)
    except urllib.error.URLError as e:
        return 502, _error(f"could not reach the hot-lead store at {host} ({_why(e.reason)})")
    except OSError as e:
        # A read that stalls past TIMEOUT raises TimeoutError here rather than URLError.
        return 502, _error(f"could not reach the hot-lead store at {host} ({_why(e)})")
    except Exception as e:                                             # noqa: BLE001
        return 502, _error(f"reading the hot-lead store failed ({type(e).__name__})")

    if not 200 <= int(status or 0) < 300:
        code, msg = _failure(int(status or 0), _upstream_message(raw))
        return code, _error(msg)
    try:
        json.loads(raw.decode("utf-8"))
    except ValueError:
        # A 200 that is not JSON is a login or error page served in the hook's place, and
        # passing it on would hand the tab HTML where it expected leads.
        return 502, _error(f"hot-lead store answered HTTP {status} with a body that is not JSON")
    except RecursionError:
        # 28 Sep 2026 (third review): a value nested some 3,000 deep (a hand-edited record) is
        # more than json.loads can read, and this raised past every except here: a 500 for
        # everyone, in both views. Nothing of such an answer can be shown, so it is a 502 like
        # any other answer that cannot be read.
        return 502, _error(f"hot-lead store answered HTTP {status} with JSON nested too deep "
                           "to read")
    return 200, raw


def view(raw, cookie_header):
    """(http_status, body_bytes): the hook's 200 answer in the view this request may see.

    FAILS CLOSED. An answer whose shape the masking does not know -- a body that is not an
    object, no list of leads, a lead that is not an object, notes that are not an object --
    is a 502 that says the leads were not shown, never the raw body. ONE lead whose own
    number is still found after the masking, or that the masking fails on in any other way,
    is cut down on its own (api/_contact.py hard_masked: grade, date and Kommo link,
    maskFailed) and its notes left out (notesHidden), so it cannot take the others with it
    (28 Sep 2026, review; any exception since the second review). The full view needs only
    the cookie.
    """
    try:
        doc = json.loads(raw.decode("utf-8"))
        if not isinstance(doc, dict):
            raise _contact.MaskError("the answer is not an object")
        # 28 Sep 2026: an older hook marks a lead's unreadable notes with notes[callId] = null.
        # Read as absent (and named in notesErrors) in both views, so the tab gets one shape.
        doc = _contact.unreadable_notes_absent(doc)
        if _contact.authorised(cookie_header):
            doc["contact"] = "full"
            doc["contactUnlock"] = "password"
        else:
            doc = _contact.mask_hot_leads(doc)
            doc["contact"] = "masked"
            doc["contactUnlock"] = "password" if _contact.configured() else "not-configured"
        return 200, _body(doc)
    except Exception as e:                                             # noqa: BLE001
        return 502, _error("the leads were not shown: their contact details could not be "
                           f"hidden ({type(e).__name__})")


def _cookie_header(headers):
    """Every Cookie header the request carried, as one (HTTP/2 may send several)."""
    got = headers.get_all("Cookie") if hasattr(headers, "get_all") else None
    return "; ".join(got or [])


class handler(BaseHTTPRequestHandler):
    """Vercel Python runtime entrypoint (a BaseHTTPRequestHandler named `handler`).

    Reachable only through the site login. middleware.js serves exactly two things without
    a session -- /r, the client share page, and POST /api/research carrying the service
    key -- and this is neither.
    """

    def _reply(self, code, body, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # Leads are names, phone numbers and appointments: never a CDN or browser copy.
        self.send_header("Cache-Control", "no-store")
        # The answer depends on the spb_contact cookie (masked or full), so nothing that
        # keeps copies may ever answer one request with another's.
        self.send_header("Vary", "Cookie")
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            code, body = fetch(limit_from(self.path), include_test_from(self.path),
                               grade_from(self.path))
            if code == 200:
                code, body = view(body, _cookie_header(self.headers))
        except Exception as e:                                         # noqa: BLE001
            code, body = 500, _error(f"api/hot_leads failed ({type(e).__name__})")
        self._reply(code, body)

    def _not_allowed(self):
        # Read-only by design. The hook is the only writer of a hot-lead record.
        self._reply(405, _error("Method not allowed. Use GET /api/hot_leads."),
                    (("Allow", "GET"),))

    do_POST = do_PUT = do_DELETE = do_PATCH = _not_allowed

    def log_message(self, fmt, *args):       # keep the function log readable
        # The request line and status only. The key rides on the upstream request, which
        # is never logged.
        sys.stderr.write("[api/hot_leads] " + (fmt % args) + "\n")
