"""Start a harvest from the app, and report how it went.

Coleen, 17 Aug 2026, twice: a button in the dashboard that signs the sources back in and
fetches stock, usable "anywhere, on any laptop", without the developer's machine.

WHY IT DELEGATES. A browser cannot drive a login against E-Agent or a builder portal —
that needs a real automated browser, cookies for a third-party domain, and a Python
pipeline. The GitHub Actions runner already does exactly that every night. So this endpoint
starts THAT, and the whole job is to do it without ever putting a CI credential in a
browser.

    browser -> POST /api/refresh   (this file, behind the site login)
            -> GitHub workflow_dispatch
            -> the same nightly harvest, on demand

A GitHub token with actions:write is the only way to dispatch a workflow, and there is no
dispatch-only scope. It lives in the Vercel project environment and is read here; it is
never returned in a response, never logged, and never reaches the page. The browser only
ever sees "started" or "failed".

WHY THE FULL HARVEST AND NOT A LOGIN-ONLY JOB. Signing in is not the thing anyone wants —
fresh stock is. And the harvest already re-authenticates any source whose session has
lapsed: that is precisely how E-Agent recovered on 17 Aug after four nights at zero. A
separate login-only workflow would be a second thing to maintain that stops one step short
of the point.

WHAT IT CANNOT DO, and the UI says so rather than pretending: Proxima needs a 2FA code from
a human's inbox, and Paramount has an invisible reCAPTCHA. No button signs those in.
"""
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

# Hard-coded, not taken from the request. A caller-supplied repo or workflow would turn a
# gated button into a general-purpose "dispatch anything as us" endpoint.
OWNER = os.environ.get("GITHUB_OWNER", "Jojo140893")
REPO = os.environ.get("GITHUB_REPO", "spb-ai-property-research-agent")
WORKFLOW = "daily-harvest.yml"
REF = os.environ.get("GITHUB_REF_NAME", "main")
API = "https://api.github.com"


def _token():
    return (os.environ.get("GITHUB_DISPATCH_TOKEN") or "").strip()


def _gh(path, method="GET", body=None):
    """One GitHub API call. Returns (status, parsed-json-or-None)."""
    req = urllib.request.Request(
        f"{API}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "spb-dashboard",
        })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        # The BODY of a GitHub error can quote the request, so it is not echoed onward —
        # only the status, which is what tells the two real failures apart (401 bad token,
        # 404 wrong repo/workflow or a token without actions:write).
        return e.code, None
    except Exception:                                                  # noqa: BLE001
        return 0, None


def _latest_run():
    """The most recent harvest run, as the UI needs to describe it."""
    status, data = _gh(f"/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW}/runs?per_page=1")
    if status != 200 or not data:
        return {"ok": False, "http": status}
    runs = data.get("workflow_runs") or []
    if not runs:
        return {"ok": True, "run": None}
    r = runs[0]
    return {"ok": True, "run": {
        "id": r.get("id"),
        # queued | in_progress | completed
        "status": r.get("status"),
        # success | failure | cancelled | None while running
        "conclusion": r.get("conclusion"),
        "started": r.get("run_started_at"),
        "url": r.get("html_url"),
    }}


class handler(BaseHTTPRequestHandler):
    """Vercel Python runtime entrypoint (a BaseHTTPRequestHandler named `handler`).

    Reachable only through the site login: middleware.js matches "/(.*)" and serves exactly
    two things without a session -- the exact path /r (the client share page), and a POST to
    exactly /api/research carrying an x-spb-service-key header equal to SPB_SERVICE_KEY
    (Agent 1's webhook starting a research on a hot, warm or nurture caller who gave a state;
    hot-only until 15 Sep 2026). Everything else, this included,
    answers 401 without a session — verified against the deployed host.
    """

    def _reply(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        """How the last run went. Polled by the button while one is in flight."""
        if not _token():
            return self._reply(200, {"configured": False})
        out = _latest_run()
        out["configured"] = True
        self._reply(200, out)

    def do_POST(self):
        """Start a harvest."""
        if not _token():
            # Says which knob is missing, without hinting at its value.
            return self._reply(503, {
                "configured": False,
                "error": "This button is not connected yet. Set GITHUB_DISPATCH_TOKEN on "
                         "the Vercel project (a GitHub token with actions:write on "
                         f"{OWNER}/{REPO})."})

        # REFUSE IF ONE IS ALREADY GOING. Two harvests at once is not a slow day, it is
        # data loss: both pull the same database, both write, and whoever pushes last
        # silently discards the other's entire run. The bucket lock in state_sync.py is
        # the real guard and the workflow has a concurrency group, but neither gives the
        # person clicking an answer — so check first and say so.
        latest = _latest_run()
        run = (latest or {}).get("run") or {}
        if run.get("status") in ("queued", "in_progress"):
            return self._reply(409, {
                "configured": True, "already_running": True, "run": run,
                "error": "A harvest is already running. It takes about 20 minutes."})

        status, _ = _gh(f"/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW}/dispatches",
                        method="POST", body={"ref": REF})
        if status == 204:
            return self._reply(202, {"configured": True, "started": True})
        self._reply(502, {
            "configured": True, "started": False, "http": status,
            "error": {
                401: "GitHub rejected the token — it is invalid or expired.",
                403: "The token is not allowed to start workflows on this repository.",
                404: "Workflow or repository not found, or the token lacks actions:write.",
            }.get(status, f"GitHub returned {status} when starting the harvest.")})
