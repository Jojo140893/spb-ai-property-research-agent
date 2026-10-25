"""Open one price list from the Settings tab's "Price lists we could not read" panel.

Ahsan, 24 Oct 2026: each entry there should be a link, so he can open the price list and
check it himself. They were names only, on purpose: 56 of the 57 on 27 Aug were Dropbox
"/scl/fo/" folder shares, capability URLs that hand the whole shared folder to anyone
holding the string, and channels.json is a static file. So the page carries an opaque id
and this route turns it into the link on the server:

    browser -> GET /api/price_list?id=3f9c0a1b2d4e5f60      (behind the site login)
            <- 302 Location: <the file, or its folder where the harvest knows no file>

THE LINKS LIVE IN THE BUNDLE. build_web writes api/_data/price_list_links.json (id -> link)
beside this file, from the same data/unreadable_stocklists.json that gave the page its ids.
Vercel does not serve api/ as static content, so a link is reachable only through this
route, and this route only through middleware.js: its matcher is every path, and the one
request under /api it lets through without a signed-in session is Agent 1's POST
/api/research with the service key. A signed-out click goes to the sign-in page and comes
back here afterwards.

THE ANSWERS:
  302  to the link. Only an http(s) link, checked again here whatever the map says.
  400  an id that is not 16 hex characters: not one this dashboard made.
  404  an id the map does not hold. The nightly harvest rebuilt the list after the page
       loaded (the file may have become readable); a reload shows the current list.
  405  anything but GET or HEAD.
Every answer is no-store and sends no referrer, so the dashboard's address is not handed
to Dropbox, and nothing logs the link: the request line carries only the id.
"""
import html
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, quote, urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
LINKS = os.path.join(_HERE, "_data", "price_list_links.json")
_ID = re.compile(r"[0-9a-f]{16}")

BAD_ID = "That link is not one this dashboard made."
GONE = ("This price list is no longer on the list. The nightly harvest rebuilds it every "
        "morning, so the file may have become readable since the page loaded. Reload the "
        "Settings tab to see the current list.")


def load_links(path=None):
    """The id -> link map, or {} when there is none (a build with no unreadable list)."""
    try:
        with open(path or LINKS, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def web_link(value):
    """`value` if it is an http(s) link to somewhere, else "". Never javascript:, file:,
    data: or a bare name, and never a value carrying a space or control character (a CR/LF
    in a Location header would split the response)."""
    text = str(value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return ""
    if re.search(r"[\s\x00-\x1f\x7f]", text):
        return ""
    # A header is Latin-1: percent-encode anything else, leaving existing escapes alone.
    return quote(text, safe=":/?#[]@!$&'()*+,;=%~")


def resolve(query, links=None):
    """(http_status, location or "", message) for one request's query string."""
    key = ((parse_qs(query or "").get("id") or [""])[0]).strip()
    if not _ID.fullmatch(key):
        return 400, "", BAD_ID
    link = web_link((load_links() if links is None else links).get(key))
    if not link:
        return 404, "", GONE
    return 302, link, ""


def _page(message):
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"robots\" content=\"noindex, nofollow\">"
            "<title>Price list - Smart Property Buying</title></head>"
            "<body style=\"font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;"
            "max-width:36rem;margin:3rem auto;padding:0 1rem;color:#334155;\">"
            "<p>%s</p><p><a href=\"/\">Back to the dashboard</a></p></body></html>"
            % html.escape(message)).encode("utf-8")


class handler(BaseHTTPRequestHandler):
    """Vercel Python runtime entrypoint. Behind the site login like every /api/* route."""

    def _reply(self, code, location="", message="", extra=(), body=True):
        payload = b"" if code == 302 else _page(message)
        self.send_response(code)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(payload)

    def _answer(self, body):
        try:
            code, location, message = resolve(urlparse(self.path).query)
        except Exception as e:                                         # noqa: BLE001
            code, location, message = 500, "", f"api/price_list failed ({type(e).__name__})"
        self._reply(code, location, message, body=body)

    def do_GET(self):
        self._answer(body=True)

    def do_HEAD(self):
        self._answer(body=False)

    def _not_allowed(self):
        self._reply(405, message="Method not allowed. Use GET /api/price_list?id=...",
                    extra=(("Allow", "GET, HEAD"),))

    do_POST = do_PUT = do_DELETE = do_PATCH = _not_allowed

    def log_message(self, fmt, *args):
        # The request line and status only: the id, never the link it resolves to.
        sys.stderr.write("[api/price_list] " + (fmt % args) + "\n")
