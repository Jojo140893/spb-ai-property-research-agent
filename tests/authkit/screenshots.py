"""Headless screenshots of /login, /forgot, /reset, "not set up yet" and an error, at 1440 px and 375 px.

    python -m tests.authkit.screenshots <output folder>

What it drives: tests/authkit/serve.mjs, which serves the middleware.js that build_web.py writes, on
https://app.smartpropertybuying.com.au:<port> locally, in front of the real Python functions and the fake
Blob store, SMTP server and clock. The browser is started with that host name mapped to 127.0.0.1 and with
no proxy, and every request it makes to anything else is aborted, so nothing leaves the machine and the
same-origin check sees what it will see in production (https, Origin == Host).

It also walks the whole thing in a real browser, once per width: ask for a code, read it from the fake
mailbox, choose a password, get sent to the login page with "Password changed", and sign in on it.
"""
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parent.parent.parent
HOST = "app.smartpropertybuying.com.au"
USER = "digital@smartpropertybuying.com.au"
WIDTHS = {"1440": (1440, 900, False), "375": (375, 812, True)}


def _cert(folder):
    key, cert = folder / "key.pem", folder / "cert.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out",
                    str(cert), "-days", "2", "-subj", "/CN=" + HOST, "-addext", "subjectAltName=DNS:" + HOST],
                   check=True, capture_output=True)
    return key, cert


class Server(object):
    def __init__(self, mw, key, cert, env=None):
        e = dict(os.environ, SPB_MW_PATH=str(mw), SPB_SERVE_KEY=str(key), SPB_SERVE_CERT=str(cert),
                 SPB_SERVE_ENV=json.dumps(env or {}), PYTHONIOENCODING="utf-8")
        self.p = subprocess.Popen(["node", str(APP / "tests" / "authkit" / "serve.mjs")], cwd=str(APP), env=e,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError("the demo server did not start: " + self.p.stderr.read())
        self.port = json.loads(line)["port"]
        self.base = "https://%s:%d" % (HOST, self.port)

    def demo(self, path):
        """The demo controls, over a plain socket to 127.0.0.1. NEVER Playwright's page.request or
        context.request: those resolve the real host name and do not honour the browser's host mapping
        (the first run of this script did exactly that and tried to connect to the real site's IP)."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen("https://127.0.0.1:%d%s" % (self.port, path), context=ctx, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))

    def stop(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=15)
        except Exception:                                                # noqa: BLE001
            self.p.kill()


def main(out):
    from playwright.sync_api import sync_playwright

    import build_web

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="spb_shots_"))
    try:
        key, cert = _cert(work)
        mw = work / "middleware.mjs"
        mw.write_bytes(build_web.MIDDLEWARE_JS.encode("utf-8"))
        configured = Server(mw, key, cert)
        unconfigured = Server(mw, key, cert, {"SPB_SMTP_PASSWORD": None})
        shots = []
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=[
                    "--host-resolver-rules=MAP %s 127.0.0.1" % HOST, "--no-proxy-server",
                    "--ignore-certificate-errors"])
                for tag, (w, h, mobile) in WIDTHS.items():
                    ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=mobile,
                                              has_touch=mobile, ignore_https_errors=True)
                    allowed = (configured.base, unconfigured.base)
                    ctx.route("**/*", lambda route: route.continue_() if route.request.url.startswith(allowed)
                              else route.abort())
                    page = ctx.new_page()
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

                    def snap(name, srv=configured):
                        path = out / ("%s-%s.png" % (name, tag))
                        page.screenshot(path=str(path), full_page=True)
                        shots.append(path.name)
                        horiz = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
                        assert not horiz, "horizontal scroll on %s at %s px" % (name, tag)

                    base = configured.base
                    # Time moves between the two widths, so the second run is allowed a new code.
                    configured.demo("/__demo/advance?s=3700")

                    page.goto(base + "/login")
                    snap("01-login")
                    page.click("text=Forgot password?")
                    page.wait_for_url(base + "/forgot")
                    assert page.inner_text("p.lede") == "A 6-digit code will be sent to d***@smartpropertybuying.com.au"
                    snap("02-forgot")
                    page.click("#go")
                    page.wait_for_selector("#out:not([hidden]).note")
                    assert "If this site is set up for it" in page.inner_text("#out")
                    snap("03-forgot-sent")
                    page.click("text=Enter the code")
                    page.wait_for_url(base + "/reset")
                    snap("04-reset")
                    # An error state: the wrong code, with a password that passes the page's own checks.
                    page.fill("#code", "000000")
                    page.fill("#pw", "a long enough password 1")
                    page.fill("#pw2", "a long enough password 1")
                    page.check("#show")
                    page.click("#go")
                    page.wait_for_selector("#out:not([hidden])")
                    assert "not right" in page.inner_text("#out")
                    snap("05-reset-error")
                    page.fill("#pw2", "something else entirely")
                    page.click("#go")
                    page.wait_for_function("document.getElementById('out').textContent.includes('do not match')")
                    snap("06-reset-mismatch")
                    # The real thing: the code from the fake mailbox, a new password, and sign in on it.
                    code = configured.demo("/__demo/code")["code"]
                    password = "Coleen picks this one %s" % tag
                    page.fill("#code", code)
                    page.fill("#pw", password)
                    page.fill("#pw2", password)
                    page.click("#go")
                    page.wait_for_url(base + "/login?reset=1")
                    assert "Password changed" in page.inner_text("p.note")
                    snap("07-login-after-reset")
                    configured.demo("/__demo/advance?s=3")                  # the time it takes a person to type
                    page.fill("#email", USER)
                    page.fill("#password", password)
                    page.click("button[type=submit]")
                    page.wait_for_selector("text=You are signed in")
                    ctx.clear_cookies()

                    page.goto(unconfigured.base + "/forgot")
                    assert "not set up yet" in page.inner_text(".info")
                    snap("08-not-set-up-forgot", unconfigured)
                    page.goto(unconfigured.base + "/reset")
                    snap("09-not-set-up-reset", unconfigured)
                    assert not [e for e in errors if "Failed to load resource" not in e], errors
                    ctx.close()
                browser.close()
        finally:
            configured.stop()
            unconfigured.stop()
        print("\n".join(sorted(shots)))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.path.insert(0, str(APP))
    main(sys.argv[1])
