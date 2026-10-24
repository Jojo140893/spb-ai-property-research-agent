"""POST /api/reset_password: spend the emailed code on a new dashboard password.

Colin, 23 Oct 2026 (see api/reset_code.py). The body is {"code": "7KQ2M-XR4TD", "password": "...",
"confirm": "..."} and nothing else. The order of the checks is the design (api/_spb_auth.py,
reset_endpoint):

  1. the shape of the request and the rules the password breaks BY ITSELF (length, spaces, the
     username, the two boxes matching). None of these look at a secret.
  2. the code, in constant time against every live 5-minute bucket. Nothing is stored either way: the
     code is 50 bits, so there is no attempt counter to keep (or for a stranger to burn).
  3. only with the right code: would the new password change nothing? (Asked later because answering it
     earlier would let a stranger test guesses at the current password.)
  4. a signed password record is made and handed to GitHub, whose workflow stores it in Vercel as
     SPB_PW_RECORD and redeploys. Only when GitHub accepts it does the answer say "saved", and a "password
     changed" notice goes to the same address.

200 {"ok": true, "epoch": N} when handed over (the page then waits for /login to report epoch N); 400 for
a request, rule or code that is wrong; 429 for too many tries on this instance; 503 when nothing could
be handed over (nothing changed). Not configured: 503, "not set up yet".
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _spb_auth  # noqa: E402


class handler(_spb_auth.AuthHandler):
    endpoint = staticmethod(_spb_auth.reset_endpoint)
