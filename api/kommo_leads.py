"""The Leads tab's "All Kommo leads" card: every lead in Kommo, read live, page by page.

Coleen's tab lists the leads the app has a record for (api/hot_leads.py): the hot callers,
the warm or nurture callers who gave a state, and the WhatsApp chats that answered the
questions. Everything else that lands in Kommo -- a WhatsApp chat that stopped before the
questions, a RingCentral SMS, a Clare call with no state or no answer, a Facebook comment, a
lead added by hand -- was on no screen of the dashboard. The hook reads them from Kommo on
request (GET only; a PATCH would move a card out of Incoming leads), and this is the
dashboard's side of that read.

    browser -> GET /api/kommo_leads?scope=incoming|all&page=N   (this file, behind the login)
            -> GET vapi-kommo-hook/api/kommo-leads?scope=..&page=..
                                                       (x-spb-service-key: SPB_SERVICE_KEY)

The upstream is SPB_KOMMO_LEADS_URL, else the host of SPB_HOT_LEADS_URL at /api/kommo-leads,
else production: a staging hook's leads and its Kommo list stay together, as the notes do
(api/lead_notes.py).

ONLY LITERALS ARE FORWARDED. scope is exactly "incoming" or "all" and page a whole number
from 1 to MAX_PAGE, each rebuilt here; anything else -- "ALL", two scopes, "2.0", "41", a
parameter smuggled inside a value -- is scope=incoming&page=1. None of the browser's text
reaches the upstream URL.

THE SAME SAFEGUARDS AS api/hot_leads.py, and from the same code: the key comes from the
environment, travels as an unredirected header (never onto a redirect's host), only to an
https:// upstream checked before it is attached, and is never returned or logged.

WHAT COMES BACK. The hook's JSON through hot_leads.view, UNCHANGED: a lead here is the shape
api/_contact.py already masks (customer, kommo, createdAt, callId; the hook writes it that
way on purpose), so the masked view, the full view behind the spb_contact cookie, a lead cut
down on its own (maskFailed) and a 502 for an answer of a shape the masking does not know
are the Leads tab's own rules, not a second copy of them. Errors:
  503  a setting is missing -- either project's SPB_SERVICE_KEY, the hook's KOMMO_DOMAIN or
       KOMMO_TOKEN -- or the hook has no /api/kommo-leads route yet. The message says which,
       and refreshing will not fix it.
  502  the hook could not be reached, timed out, could not read Kommo, or failed some other
       way that may pass.
NEVER 401: the tab reads a 401 from /api/* as "your sign-in expired".
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _contact  # noqa: E402
import hot_leads as _hl  # noqa: E402  (the key, the error body, the view, the hook's message)

DEFAULT_URL = "https://vapi-kommo-hook.vercel.app/api/kommo-leads"
# One page is one Kommo leads read and up to five contact reads, in sequence, on the hook
# (maxDuration 30 there): longer than the hot-leads list, so a little more than its 20 s,
# and still inside this function's own 30.
TIMEOUT = 25
SCOPES = ("incoming", "all")
# The hook's own cap (lib/kommo-leads.js MAX_PAGE): 40 pages of 250.
MAX_PAGE = 40
HOOK_ROUTE = "/api/kommo-leads"
# ASCII digits only, as api/hot_leads.py limit: \d would also accept Arabic-Indic digits.
_PAGE = re.compile(r"[0-9]{1,3}")


def _upstream():
    """(url, the setting it came from), as api/lead_notes.py: the Kommo list follows the leads."""
    own = (os.environ.get("SPB_KOMMO_LEADS_URL") or "").strip()
    if own:
        return own, "SPB_KOMMO_LEADS_URL"
    leads = (os.environ.get("SPB_HOT_LEADS_URL") or "").strip()
    if leads:
        parts = urllib.parse.urlsplit(leads)
        return (urllib.parse.urlunsplit((parts.scheme, parts.netloc, HOOK_ROUTE, "", "")),
                "SPB_HOT_LEADS_URL (which SPB_KOMMO_LEADS_URL follows when it is unset)")
    return DEFAULT_URL, "SPB_KOMMO_LEADS_URL"


def _values(path, name):
    return urllib.parse.parse_qs(urllib.parse.urlsplit(path or "").query).get(name) or []


def scope_from(path):
    """"incoming" or "all": the literal from SCOPES for exactly one exact ?scope=, else
    "incoming". "ALL", "pipeline", "" or a repeated parameter are the default, never passed on."""
    values = _values(path, "scope")
    if len(values) == 1 and values[0] in SCOPES:
        return SCOPES[SCOPES.index(values[0])]
    return "incoming"


def page_from(path):
    """The ?page= as an int from 1 to MAX_PAGE, else 1. One parameter, ASCII digits only."""
    values = _values(path, "page")
    if len(values) == 1 and _PAGE.fullmatch(values[0]) and 1 <= int(values[0]) <= MAX_PAGE:
        return int(values[0])
    return 1


def _failure(code, detail):
    """(dashboard status, message) for a hook answer outside 2xx. Never a 401."""
    # The hook's messages carry statuses and counts only; a phone or email never needs to.
    detail = _contact.generic(detail)
    said = f"kommo-leads answered HTTP {code}" + (f": {detail}" if detail else "")
    low = detail.lower()
    if code == 404 and not detail:
        # Vercel's own NOT_FOUND page: the hook is deployed without this route. The hook goes
        # first (section 3 of the build contract), so this is the state in between.
        return 503, (f"the {_hl.HOOK_PROJECT} project has no {HOOK_ROUTE} route; deploy the "
                     f"hook ({said})")
    if code == 401:
        if "key" in low:
            return 503, (f"the dashboard's SPB_SERVICE_KEY does not match the one on the "
                         f"{_hl.HOOK_PROJECT} project ({said})")
        return 503, (f"the {_hl.HOOK_PROJECT} project refused the dashboard ({said}); check "
                     f"that its Vercel deployment protection is off and SPB_SERVICE_KEY is the "
                     f"same on both projects")
    if code in (500, 503) and "not configured" in low:
        # The key first: a hook that names itself ("... on vapi-kommo-hook") says "kommo" too.
        if "spb_service_key" in low:
            what = "SPB_SERVICE_KEY not configured"
        elif "kommo" in low:
            what = "KOMMO_DOMAIN/KOMMO_TOKEN not configured"
        else:
            what = "a setting is not configured"
        return 503, f"{what} on the {_hl.HOOK_PROJECT} project ({said})"
    return 502, said


def _why(reason):
    """A network failure, described without echoing anything it might carry."""
    if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
        return f"timed out after {TIMEOUT}s"           # this file's TIMEOUT, not the list's
    return _hl._why(reason)


def fetch(scope="incoming", page=1):
    """(http_status, body_bytes) for the tab. Never raises, and never puts the key in a body."""
    key = _hl._key()
    if not key:
        return 503, _hl._error("SPB_SERVICE_KEY is not set on the dashboard project")
    upstream, setting = _upstream()
    parts = urllib.parse.urlsplit(upstream)
    if parts.scheme != "https" or not parts.hostname:
        # Refused BEFORE the key is attached, as in api/hot_leads.py.
        return 503, _hl._error(f"{setting} must be an https:// URL")
    # Checked again here, not only in scope_from and page_from: whatever calls fetch, only
    # a literal from SCOPES and a whole number in range reach the upstream URL.
    scope = SCOPES[SCOPES.index(scope)] if scope in SCOPES else "incoming"
    page = page if type(page) is int and 1 <= page <= MAX_PAGE else 1
    extra = urllib.parse.urlencode((("scope", scope), ("page", str(page))))
    query = (parts.query + "&" if parts.query else "") + extra
    url = urllib.parse.urlunsplit(parts._replace(query=query, fragment=""))
    host = parts.hostname                    # never netloc, which could carry user:password@
    req = urllib.request.Request(url, method="GET", headers={
        "Accept": "application/json", "User-Agent": "spb-dashboard"})
    req.add_unredirected_header(_hl.HEADER, key)     # see api/hot_leads.py: never on a redirect
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
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
        return 502, _hl._error(f"could not reach the Kommo leads list at {host} "
                               f"({_why(e.reason)})")
    except OSError as e:
        # A read that stalls past TIMEOUT raises TimeoutError here rather than URLError.
        return 502, _hl._error(f"could not reach the Kommo leads list at {host} ({_why(e)})")
    except Exception as e:                                             # noqa: BLE001
        return 502, _hl._error(f"reading the Kommo leads list failed ({type(e).__name__})")
    if not 200 <= int(status or 0) < 300:
        code, msg = _failure(int(status or 0), _hl._upstream_message(raw))
        return code, _hl._error(msg)
    try:
        json.loads(raw.decode("utf-8"))
    except ValueError:
        # A login or error page served in the hook's place.
        return 502, _hl._error(f"kommo-leads answered HTTP {status} with a body that is not JSON")
    except RecursionError:                   # nested past json.loads: see api/hot_leads.py
        return 502, _hl._error(f"kommo-leads answered HTTP {status} with JSON nested too deep "
                               "to read")
    return 200, raw


class handler(BaseHTTPRequestHandler):
    """Vercel Python runtime entrypoint. Behind the site login like every /api/* route."""

    def _reply(self, code, body, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # Leads are names and phone numbers: never a CDN or browser copy.
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Cookie")               # masked or full: see hot_leads.py
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            code, body = fetch(scope_from(self.path), page_from(self.path))
            if code == 200:
                code, body = _hl.view(body, _hl._cookie_header(self.headers))
        except Exception as e:                                         # noqa: BLE001
            code, body = 500, _hl._error(f"api/kommo_leads failed ({type(e).__name__})")
        self._reply(code, body)

    def _not_allowed(self):
        # Read-only by design, here and on the hook: nothing on this card writes to Kommo.
        self._reply(405, _hl._error("Method not allowed. Use GET /api/kommo_leads."),
                    (("Allow", "GET"),))

    do_POST = do_PUT = do_DELETE = do_PATCH = _not_allowed

    def log_message(self, fmt, *args):
        # The request line and status only: no names, no numbers, no key.
        sys.stderr.write("[api/kommo_leads] " + (fmt % args) + "\n")
