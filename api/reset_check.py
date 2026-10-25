"""POST /api/reset_check: does this dashboard vouch for this password record? 204, or a refusal.

Review, 25 Oct 2026. The GitHub workflow (.github/workflows/apply-dashboard-password.yml) stores whatever
record it is started with as SPB_PW_RECORD and redeploys. Anyone who can start it (the dispatch token,
which every function on this Vercel project can read, or write access to the repo) could hand it a record
of the right shape that this dashboard never signed. middleware.js checks the mac before anything else,
so sign-in then answered 503 for everyone, and a new SITE_PASSWORD did not bring it back. GitHub cannot
check the mac itself: SPB_AUTH_SECRET is deliberately only here. So the workflow's first step asks this
endpoint and stops unless it answers 204.

All the logic is in api/_spb_auth.py (verify_record, check_endpoint); this file only names the route.
The body is {"record": "v1...."} and nothing else. It changes nothing and needs no Origin (a server calls
it). middleware.js lets exactly POST /api/reset_check through without a session, and only while the
feature is configured; otherwise it is gated like any path, and the workflow, getting no 204, stores
nothing.

204 for a record this dashboard signed, for today's SITE_PASSWORD, made in the last 30 minutes and newer
than the live one (the same number is not newer: review, 25 Oct 2026, so a reset made at the same moment
as the live one cannot replace it). 422 {"error": <why>} otherwise: "malformed" or "not_signed" for
anything this dashboard did not make, and only past the mac anything more specific. Not configured: 503.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _spb_auth  # noqa: E402


class handler(_spb_auth.AuthHandler):
    endpoint = staticmethod(_spb_auth.check_endpoint)
    need_origin = False
