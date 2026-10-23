"""POST /api/auth/forgot: email a 6-digit code to the dashboard's fixed address.

Ahsan, 23 Oct 2026: "add a Forgot password: it sends a code to digital@smartpropertybuying.com.au
and she can reset the password". All the logic is in api/_spb_auth.py (read its header first);
this file only names the route, because Vercel makes one function per file under api/.

middleware.js lets exactly POST /api/auth/forgot through without a session (the person asking has
lost the password), so everything a public endpoint needs is checked in _spb_auth.respond():
the feature is configured, the request is same-origin JSON and small. The body must be {}: there
is deliberately NO email field, so nobody can aim the code at a different address.

THE ANSWER IS ALWAYS THE SAME SENTENCE once the feature is configured: "If this site is set up
for it, a code has been emailed." A request made too soon, a failed email and a store outage all
read exactly like success, and the answer takes at least a second whichever it was, so neither the
wording nor a stopwatch tells a stranger anything. What happened is in the function log, as fixed
event names that never carry the code. Not configured: 503, "not set up yet".
"""
import os
import sys

_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _API not in sys.path:
    sys.path.insert(0, _API)

import _spb_auth  # noqa: E402


class handler(_spb_auth.AuthHandler):
    endpoint = staticmethod(_spb_auth.forgot_endpoint)
