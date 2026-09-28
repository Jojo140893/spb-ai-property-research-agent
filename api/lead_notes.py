"""What the team has done for a Clare lead, and the links they saved for it.

Coleen, 28 Sep 2026: "Please add a free-text area where we can record what action has been
taken on each lead, for example: Package sent, Call booked with customer, Follow-up required,
Google Drive link added, Property saved for the customer. It would also be useful to have a
field where we can paste a Google Drive link or other relevant property link for future
reference."

    browser -> POST /api/lead_notes {callId, op, ...}      (this file, behind the login)
            -> POST vapi-kommo-hook/api/lead-notes         (x-spb-service-key: SPB_SERVICE_KEY)

The body is the hook's, unchanged in meaning: op "add_entry" {action, text, by?, requestId?},
op "add_link" {url, label?, by?, requestId?}, op "remove" {id}. The hook validates it, stores
it beside the lead (lead-notes/<callId>.json in its private Blob store) and answers with the
lead's notes as they now stand; a requestId it has already stored for that lead is a retried
click, answered with the notes and nothing written. GET /api/hot_leads returns the same notes
with every lead, so the tab shows them without a second request.
The upstream is SPB_LEAD_NOTES_URL, else the host of SPB_HOT_LEADS_URL, else production.

WHY THE HOOK STORES THEM. This dashboard is a static site whose functions have a read-only
disk and no store of their own; the hook already keeps each lead in a private Blob store and
holds its token. The notes go where the lead is, through the one key the two projects share.

THE SAME SAFEGUARDS AS api/hot_leads.py, and from the same code: the key comes from the
environment and travels as an unredirected header (never onto a redirect's host), only to an
https:// upstream, and is never returned or logged. The request body is checked here before
anything is sent: JSON (Content-Type application/json, which a cross-site form cannot send),
an object, at most 16 KB -- the hook's own cap.

WHAT COMES BACK. The hook's JSON without its "contact" block -- the lead's own email and phone,
which the hook adds for the masking below and which the browser never gets from here, in
either view. Masked -- no valid spb_contact cookie, see api/_contact.py -- means every note's
text, label and by, and every saved link, are scrubbed with that lead's own rules and the
generic ones, exactly as GET /api/hot_leads scrubs them, so saving a note never shows a
masked viewer more than reading one does. (A hook deployed before it sent "contact" gets the
generic rules only: api/_contact.py mask_notes_answer.) Since 28 Sep 2026 the top-level
"contact" is instead the view it was built in, "full" or "masked", as GET /api/hot_leads says
it; notes a masked view cannot make safe come back as notesHidden: true with no notes; and the
hook's "duplicate" / "duplicateRemoved" (a retried click it had already stored) are passed
on in both views. Errors:
  400 404 409 413 415  the hook's own answer and message, passed on: a bad request, no such
                       lead, a conflict it could not retry past, over a cap, not JSON.
  503  a setting is missing (either project's SPB_SERVICE_KEY, the hook's
       BLOB_READ_WRITE_TOKEN) or the hook has no /api/lead-notes route yet. The message says
       which, and refreshing will not fix it.
  502  the hook could not be reached, timed out, or failed some other way that may pass.
NEVER 401: the tab reads a 401 from /api/* as "your sign-in expired".
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _contact  # noqa: E402
import hot_leads as _hl  # noqa: E402  (the key, the error body, the hook's message)

DEFAULT_URL = "https://vapi-kommo-hook.vercel.app/api/lead-notes"
MAX_BODY = 16 * 1024
# The hook's answers that are about the request, not about a setting: passed on as they are.
PASSED_ON = (400, 404, 409, 413, 415)


def _upstream():
    """(url, the setting it came from).

    28 Sep 2026 (review): the notes follow the leads. With SPB_HOT_LEADS_URL pointed at a
    staging hook and SPB_LEAD_NOTES_URL left unset, the leads were read from staging and their
    notes written to production, which answered "no such lead". So an unset
    SPB_LEAD_NOTES_URL means the same host as SPB_HOT_LEADS_URL, at /api/lead-notes (its query
    and fragment are the leads' business, not the notes'), and only with neither set is it
    the production hook.
    """
    own = (os.environ.get("SPB_LEAD_NOTES_URL") or "").strip()
    if own:
        return own, "SPB_LEAD_NOTES_URL"
    leads = (os.environ.get("SPB_HOT_LEADS_URL") or "").strip()
    if leads:
        parts = urllib.parse.urlsplit(leads)
        return (urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/api/lead-notes", "", "")),
                "SPB_HOT_LEADS_URL (which SPB_LEAD_NOTES_URL follows when it is unset)")
    return DEFAULT_URL, "SPB_LEAD_NOTES_URL"


def _failure(code, detail):
    """(dashboard status, message) for a hook answer outside 2xx. Never a 401."""
    # A hook message could quote what was sent; it never needs to carry a phone or email.
    detail = _contact.generic(detail)
    said = f"lead-notes store answered HTTP {code}" + (f": {detail}" if detail else "")
    low = detail.lower()
    if code == 404 and not detail:
        # The hook's own 404 says "no such lead". A bare 404 is Vercel's NOT_FOUND page: the
        # hook deployed without this route, which no amount of retrying will change.
        return 503, (f"the {_hl.HOOK_PROJECT} project has no /api/lead-notes route ({said}); "
                     f"deploy the hook with lead notes, or check SPB_LEAD_NOTES_URL")
    if code in PASSED_ON:
        return code, detail or said
    if code == 401:
        if "key" in low:
            return 503, (f"the dashboard's SPB_SERVICE_KEY does not match the one on the "
                         f"{_hl.HOOK_PROJECT} project ({said})")
        return 503, (f"the {_hl.HOOK_PROJECT} project refused the dashboard ({said}); check "
                     f"that its Vercel deployment protection is off and SPB_SERVICE_KEY is the "
                     f"same on both projects")
    if code in (500, 503):
        if "blob" in low or "store" in low or "token" in low:
            what = "the lead-notes store is not configured (BLOB_READ_WRITE_TOKEN)"
        elif "key" in low:
            what = "SPB_SERVICE_KEY is not configured"
        else:
            what = "a setting is not configured (SPB_SERVICE_KEY or BLOB_READ_WRITE_TOKEN)"
        return 503, f"{what} on the {_hl.HOOK_PROJECT} project ({said})"
    return 502, said


def send(body):
    """(http_status, body_bytes) for one validated request body. Never raises, never leaks the key."""
    key = _hl._key()
    if not key:
        return 503, _hl._error("SPB_SERVICE_KEY is not set on the dashboard project")
    upstream, setting = _upstream()
    parts = urllib.parse.urlsplit(upstream)
    if parts.scheme != "https" or not parts.hostname:
        # Refused BEFORE the key is attached, as in api/hot_leads.py.
        return 503, _hl._error(f"{setting} must be an https:// URL")
    url = urllib.parse.urlunsplit(parts._replace(fragment=""))
    host = parts.hostname                    # never netloc, which could carry user:password@
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Accept": "application/json", "Content-Type": "application/json",
        "User-Agent": "spb-dashboard"})
    req.add_unredirected_header(_hl.HEADER, key)     # see api/hot_leads.py: never on a redirect
    try:
        with urllib.request.urlopen(req, timeout=_hl.TIMEOUT) as r:
            status = getattr(r, "status", None) or r.getcode()
            raw = r.read()
    except urllib.error.HTTPError as e:
        try:
            detail = _hl._upstream_message(e.read())
        except Exception:                                              # noqa: BLE001
            detail = ""
        code, msg = _failure(e.code, detail)
        return code, _hl._error(msg)
    except urllib.error.URLError as e:
        return 502, _hl._error(f"could not reach the lead-notes store at {host} "
                               f"({_hl._why(e.reason)})")
    except OSError as e:
        return 502, _hl._error(f"could not reach the lead-notes store at {host} ({_hl._why(e)})")
    except Exception as e:                                             # noqa: BLE001
        return 502, _hl._error(f"writing the lead notes failed ({type(e).__name__})")
    if not 200 <= int(status or 0) < 300:
        code, msg = _failure(int(status or 0), _hl._upstream_message(raw))
        return code, _hl._error(msg)
    try:
        json.loads(raw.decode("utf-8"))
    except ValueError:
        return 502, _hl._error(f"lead-notes store answered HTTP {status} with a body that is "
                               "not JSON")
    except RecursionError:                   # nested past json.loads: see api/hot_leads.py
        return 502, _hl._error(f"lead-notes store answered HTTP {status} with JSON nested too "
                               "deep to read")
    return 200, raw


def view(raw, cookie_header):
    """The hook's 200 answer in the view this request may see. Fails closed, as hot_leads.view.

    28 Sep 2026 (second review):
      * "contact" at the top is the VIEW, "full" or "masked", as GET /api/hot_leads says it,
        so the tab knows which notes it was handed. The hook's "contact" block -- the lead's
        own email and phone, there for the masking -- is never passed on, in either view:
        the page has the lead from GET /api/hot_leads.
      * Notes the masking cannot make safe, for any reason, are no longer a 502 for a note
        the hook had saved: the answer is a 200 that says so, notesHidden: true and no notes
        (api/_contact.py notes_hidden_answer).
      * An answer that is not an object, or has no status, is not a shape the hook sends:
        still a 502, never the body.
    """
    try:
        doc = json.loads(raw.decode("utf-8"))
        if not isinstance(doc, dict) or "status" not in doc:
            raise _contact.MaskError("the answer is not a lead-notes answer")
        if _contact.authorised(cookie_header):
            doc.pop("contact", None)
            doc["contact"] = "full"
        else:
            try:
                doc = _contact.mask_notes_answer(doc)   # uses "contact", and drops it
            except Exception:                                          # noqa: BLE001
                doc = _contact.notes_hidden_answer(doc)
            doc["contact"] = "masked"
        return 200, _hl._body(doc)
    except Exception as e:                                             # noqa: BLE001
        return 502, _hl._error("the note was saved, but the notes could not be shown with "
                               f"their contact details hidden ({type(e).__name__}); press "
                               "Refresh")


def _request_body(headers, rfile):
    """(None, body_bytes) for a request worth forwarding, or (status, error_bytes)."""
    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return 415, _hl._error("Send the note as JSON (Content-Type: application/json).")
    try:
        length = int(headers.get("Content-Length") or 0)
    except ValueError:
        length = -1
    if length > MAX_BODY:
        return 413, _hl._error(f"That note is too large (the limit is {MAX_BODY // 1024} KB).")
    if length <= 0:
        return 400, _hl._error("The request had no body.")
    try:
        payload = json.loads(rfile.read(length).decode("utf-8"))
    except ValueError:
        return 400, _hl._error("That request is not JSON.")
    except RecursionError:
        # 16 KB of "[" is nested some 16,000 deep, past what json.loads can read: a 500 before
        # the third review (28 Sep 2026). It is not a note, whatever else it is.
        return 400, _hl._error('Send a JSON object: {"callId": ..., "op": ...}.')
    if not isinstance(payload, dict):
        return 400, _hl._error('Send a JSON object: {"callId": ..., "op": ...}.')
    # Re-serialised, so what reaches the hook is JSON this function parsed, never the bytes
    # as they arrived. Every field goes on, requestId included (28 Sep 2026: the tab's random
    # id per Save or Add click, so the hook can tell a retried click from a second note): the
    # hook is what checks each field, and a list kept here too would be a second thing to
    # keep in step. Written by _hl._body (28 Sep 2026, third review): a note whose text holds
    # half a surrogate pair -- a browser's JSON.stringify writes one as "\\ud83d" when a
    # .slice() cuts an emoji in half -- failed the UTF-8 encode here, a 500 with the note
    # unsaved; it goes to the hook as U+FFFD, what the tab draws for it anyway.
    try:
        return None, _hl._body(payload)
    except RecursionError:
        return 400, _hl._error('Send a JSON object: {"callId": ..., "op": ...}.')


class handler(BaseHTTPRequestHandler):
    """Vercel Python runtime entrypoint. Behind the site login like every /api/* route."""

    def _reply(self, code, body, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Cookie")               # masked or full: see hot_leads.py
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            code, body = _request_body(self.headers, self.rfile)
            if code is None:
                code, body = send(body)
                if code == 200:
                    code, body = view(body, _hl._cookie_header(self.headers))
        except Exception as e:                                         # noqa: BLE001
            code, body = 500, _hl._error(f"api/lead_notes failed ({type(e).__name__})")
        self._reply(code, body)

    def _not_allowed(self):
        # The notes arrive with the leads (GET /api/hot_leads); this route only writes.
        self._reply(405, _hl._error("Method not allowed. Use POST /api/lead_notes."),
                    (("Allow", "POST"),))

    do_GET = do_PUT = do_DELETE = do_PATCH = _not_allowed

    def log_message(self, fmt, *args):
        # The request line and status only: no body (notes can name a customer), no key.
        sys.stderr.write("[api/lead_notes] " + (fmt % args) + "\n")
