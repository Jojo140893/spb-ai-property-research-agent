"""POST /api/auth/reset: spend the emailed code on a new dashboard password.

Ahsan, 23 Oct 2026 (see api/auth/forgot.py). The body is {"code": "123456", "password": "...",
"confirm": "..."} and nothing else. The order of the checks is the design (api/_spb_auth.py,
reset_endpoint and reset_password):

  1. the shape of the request and the rules the password breaks BY ITSELF (length, spaces, the
     username, the two boxes matching). None of these look at a secret, so a wrong answer here is
     no oracle for the code or for the current password.
  2. the code, in constant time. A wrong one is counted in the store BEFORE the answer is sent and
     the fifth burns it. An expired, burned or never-requested code gets the same answer as a wrong
     one.
  3. only with the right code: is the new password the one already in use? (Asked later because
     answering it earlier would let a stranger test guesses at the current password.)
  4. the code is burned (a conditional write that only one request can win), then the password is
     stored as PBKDF2 and a "password changed" notice is emailed to the same address.

Not configured: 503, "not set up yet". 200 {"ok": true} on success; 400 {"error": ...} for a
request, rule or code that is wrong; 503 when the store cannot settle it (nothing changed).
"""
import os
import sys

_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _API not in sys.path:
    sys.path.insert(0, _API)

import _spb_auth  # noqa: E402


class handler(_spb_auth.AuthHandler):
    endpoint = staticmethod(_spb_auth.reset_endpoint)
