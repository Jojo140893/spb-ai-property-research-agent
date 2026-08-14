"""The login gate in front of the deployed dashboard.

WHY THIS TEST EXISTS. The gate is a JavaScript file that build_web.py writes into
vercel_site/ — a gitignored build output. Nothing else imports it, nothing type-checks it,
and if a refactor stops emitting it the build still succeeds, the dashboard still loads,
and the entire catalogue goes back to being readable by anyone with the URL. That failure
is completely silent, which is the same shape as the harvest reporting success on an empty
channel. So the gate gets asserted like any other behaviour.

The behavioural checks live in middleware_checks.mjs and run under Node, because the code
under test is edge JavaScript and re-implementing HMAC session verification in Python to
test it would be testing the re-implementation. This module is the pytest entry point:
it takes MIDDLEWARE_JS exactly as the build would write it, and runs the suite against it.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))

CHECKS = Path(__file__).resolve().parent / "middleware_checks.mjs"


def test_build_writes_the_gate(tmp_path):
    """A build with no middleware.js is a public dashboard, so the build must emit it."""
    import build_web

    build_web._write_auth_middleware(tmp_path)
    written = tmp_path / "middleware.js"
    assert written.is_file(), "build_web wrote no middleware.js — the site would be public"
    body = written.read_text(encoding="utf-8")
    assert "SITE_PASSWORD" in body
    # The matcher is what makes it cover stock.json and the PDFs rather than just the page.
    assert 'matcher: "/(.*)"' in body


def test_the_gate_holds(tmp_path):
    """Run the Node suite against the middleware exactly as the build emits it."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the edge middleware cannot be exercised here")

    import build_web

    build_web._write_auth_middleware(tmp_path)
    (tmp_path / "middleware.mjs").write_text(
        (tmp_path / "middleware.js").read_text(encoding="utf-8"), encoding="utf-8")
    shutil.copy(CHECKS, tmp_path / "checks.mjs")

    p = subprocess.run([node, "checks.mjs"], cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=120)
    # Printed in full on failure: the individual check names are the diagnosis, and
    # "exit code 1" on a security gate is not something anyone should have to re-run to read.
    assert p.returncode == 0, "the login gate failed its checks:\n" + p.stdout + p.stderr
