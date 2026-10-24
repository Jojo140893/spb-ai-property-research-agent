"""POST /api/reset_code: email a dashboard reset code to the fixed address.

Colin, 23 Oct 2026: people have left his team and he wants to change the dashboard password himself.
All the logic is in api/_spb_auth.py (read its header first); this file only names the route, because
Vercel makes one function per file under api/.

middleware.js lets exactly POST /api/reset_code through without a session (the person asking has lost
the password), and only while the feature is configured, so everything a public endpoint needs is
checked in _spb_auth.respond(): configured, same-origin, JSON, small. The body must be {}: there is
deliberately NO email field, so nobody can aim the code at a different address.

THE ANSWER IS ALWAYS THE SAME SENTENCE once configured: "If this site is set up for it, a code has been
emailed." A request made too soon and a failed email read exactly like success, and the answer takes at
least two seconds whichever it was. What happened is in the function log, as fixed event names that
never carry the code. Nothing is stored. Not configured: 503, "not set up yet".
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _spb_auth  # noqa: E402


class handler(_spb_auth.AuthHandler):
    endpoint = staticmethod(_spb_auth.forgot_endpoint)
