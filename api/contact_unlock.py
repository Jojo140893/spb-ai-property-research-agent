"""The contact-details password: who may see a Clare caller's full email and phone number.

Coleen, 28 Sep 2026: "Not everyone on the team should have access to the customer's full
email address or phone number. The full customer contact details should remain restricted to
authorised users only." The owner chose what AUTHORISED means: whoever knows a second
password, SPB_CONTACT_PASSWORD, set on the dashboard's Vercel project. The site login lets
the whole team in; this lets one person, for 4 hours, see who to ring and email.

    browser -> POST /api/contact_unlock {"password": "..."}   (this file, behind the login)
            <- 200 {"status": "success", "contact": "full"}
               Set-Cookie: spb_contact=...; Path=/api; HttpOnly; Secure; SameSite=Strict;
                           Max-Age=14400
    browser -> POST /api/contact_unlock {"lock": true}
            <- 200 {"status": "success", "contact": "masked"}, and the cookie cleared

After either, the tab reads GET /api/hot_leads again, which answers in the matching view. The
cookie's format and the masking rules are in api/_contact.py.

THE ANSWERS, and why none of them is a 401:
  200  unlocked (or locked).
  403  "That contact-details password is not right." after a fixed 0.7 s delay: the same
       brake middleware.js puts on a wrong site password. The function is stateless across
       instances, so there is nowhere to count attempts; the delay caps one guesser at about
       a guess a second. It is not a substitute for a strong password.
  503  SPB_CONTACT_PASSWORD is not set here: there is nothing to unlock with, and an empty
       password must never unlock anything (anyone can sign with an empty key).
  400 / 405 / 413  a request this endpoint does not take.
NEVER 401: the tab reads a 401 from /api/* as "your sign-in expired" and sends the person to
the login page -- a wrong contact-details password is not a lost session.
"""
import hmac
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _contact  # noqa: E402

WRONG_DELAY = 0.7
MAX_BODY = 4096                              # a password, not a document
WRONG = "That contact-details password is not right."
NOT_SET = ("Contact details are locked: no contact-details password is set on this "
           "deployment.")


def _json(obj):
    return json.dumps(obj).encode("utf-8")


def _error(message):
    return _json({"status": "error", "message": message})


def answer(payload):
    """(http_status, body_bytes, set_cookie or None) for one parsed request body."""
    if not isinstance(payload, dict):
        return 400, _error('Send a JSON object: {"password": "..."} or {"lock": true}.'), None
    if payload.get("lock") is True:
        # Locking needs no password and works with none configured: ending your own view
        # of the numbers is never something to refuse.
        return 200, _json({"status": "success", "contact": "masked"}), _contact.lock_cookie()
    key = _contact.secret()
    if not key:
        return 503, _error(NOT_SET), None
    given = payload.get("password")
    # Trimmed, because the configured value is: a password can never start or end with a
    # space here, and a phone keyboard's trailing one should not cost Coleen a retry.
    given = given.strip() if isinstance(given, str) else ""
    # Bytes, and always compared: a missing password takes the same path and the same time
    # as a wrong one, so the delay below cannot be skipped by sending nothing.
    if not hmac.compare_digest(given.encode("utf-8"), key.encode("utf-8")):
        time.sleep(WRONG_DELAY)
        return 403, _error(WRONG), None
    return 200, _json({"status": "success", "contact": "full"}), _contact.unlock_cookie()


class handler(BaseHTTPRequestHandler):
    """Vercel Python runtime entrypoint. Behind the site login like every /api/* route."""

    def _reply(self, code, body, cookie=None, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length > MAX_BODY:
                return self._reply(413, _error("That request is too large."))
            if length <= 0:
                return self._reply(400, _error('Send a JSON object: {"password": "..."}.'))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                return self._reply(400, _error("That request is not JSON."))
            code, body, cookie = answer(payload)
        except Exception as e:                                         # noqa: BLE001
            code, body, cookie = 500, _error(
                f"api/contact_unlock failed ({type(e).__name__})"), None
        self._reply(code, body, cookie)

    def _not_allowed(self):
        self._reply(405, _error("Method not allowed. Use POST /api/contact_unlock."),
                    extra=(("Allow", "POST"),))

    do_GET = do_PUT = do_DELETE = do_PATCH = _not_allowed

    def log_message(self, fmt, *args):
        # The request line and status only. The password rides in the body, never logged.
        sys.stderr.write("[api/contact_unlock] " + (fmt % args) + "\n")
