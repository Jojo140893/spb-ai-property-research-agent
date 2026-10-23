"""Everything the password-reset code talks to, faked, in one object. Tests only.

    Backend(auth_module)     the fake Blob store, the fake SMTP server, a clock the tests can move,
                             and the real api/auth/* handlers served on 127.0.0.1.

`auth_module` is api/_spb_auth.py (or a mutated copy of it: tests/test_forgot_password_mutations.py
runs the suite against copies with one safeguard removed). The module's seams are pointed at the
fakes (TRANSPORT, SMTP_FACTORY, clock, sleep), so the production code runs unchanged and cannot reach
a real host: the Blob transport REFUSES any URL that is not a Blob store host or vercel.com/api/blob
and rewrites those to the local fake.

Two ways to use it:
  * in-process, from pytest: Backend(module).start(); call module functions or POST to .urls
  * as a subprocess, for the Node suite (tests/forgot_password_checks.mjs): `python -m
    tests.authkit.backend --api-dir <dir>` prints one JSON line of ports and then answers a control
    API at /__ctl/* (environment, clock, mailbox, Blob state, logs) until stdin closes.

Everything the function writes to stderr is captured (self.logs) so a test can prove no secret
reached a log.
"""
import argparse
import http.server
import importlib
import json
import os
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email import message_from_bytes, policy

from tests.authkit.fake_blob import FakeBlob
from tests.authkit.fake_smtp import FakeSMTP

FAKE_TOKEN = "vercel_blob_rw_fakestore0123_NOTAREALSECRETnotarealsecret9876"
FAKE_SMTP_USER = "digital@smartpropertybuying.com.au"
FAKE_SMTP_PASSWORD = "fake-app-password-not-real"
FAKE_SERVICE_KEY = "fake-service-key-not-the-real-one-0001"
FAKE_SITE_PASSWORD = "fake-site-password-not-the-real-one"
ORIGIN = "https://app.smartpropertybuying.com.au"
HOST = "app.smartpropertybuying.com.au"

# Every variable the feature reads. Backend.env() sets and restores exactly these.
ENV_NAMES = ("SITE_PASSWORD", "SITE_USERNAME", "SPB_SERVICE_KEY", "BLOB_READ_WRITE_TOKEN",
             "SPB_SMTP_USER", "SPB_SMTP_PASSWORD", "SPB_RESET_TO", "SPB_CONTACT_PASSWORD")


class _Tee(object):
    """sys.stderr, plus a copy kept for the tests."""

    def __init__(self, real, sink):
        self.real, self.sink, self.lock = real, sink, threading.Lock()

    def write(self, text):
        with self.lock:
            self.sink.append(text)
        return self.real.write(text)

    def flush(self):
        self.real.flush()

    def __getattr__(self, name):
        return getattr(self.real, name)


def load_auth(api_dir=None):
    """Import the functions' shared module from `api_dir` (default: this repo's api/) under its
    production name, `_spb_auth`, exactly as the handlers in api/auth/ import it."""
    api_dir = os.path.abspath(api_dir or os.environ.get("SPB_AUTH_API_DIR") or os.path.join(
        os.path.dirname(__file__), "..", "..", "api"))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p) != api_dir]
    sys.path.insert(0, api_dir)
    for name in [n for n in sys.modules if n in ("_spb_auth", "forgot", "reset")]:
        del sys.modules[name]
    return importlib.import_module("_spb_auth"), api_dir


def load_handler(api_dir, name):
    """The handler class from api/auth/<name>.py, imported the way the Python runtime would."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("auth_" + name, os.path.join(api_dir, "auth", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.handler


class Backend(object):
    def __init__(self, module=None, api_dir=None):
        if module is None:
            module, api_dir = load_auth(api_dir)
        self.A, self.api_dir = module, api_dir
        self.offset = 0.0
        self.logs = []
        self._saved_env = None
        self._real_stderr = None
        self._orig_log = None
        self.blob = FakeBlob(FAKE_TOKEN, now=self.now)
        self.smtp = FakeSMTP(FAKE_SMTP_USER, FAKE_SMTP_PASSWORD)
        self.ports = {}
        self.servers = []

    # ---------------------------------------------------------------- time
    def now(self):
        return time.time() + self.offset

    def advance(self, seconds):
        self.offset += seconds

    # ---------------------------------------------------------------- environment
    def env(self, **values):
        """Set (str) or remove (None) variables from ENV_NAMES. The first call remembers the
        originals so close() can put them back."""
        if self._saved_env is None:
            self._saved_env = {k: os.environ.get(k) for k in ENV_NAMES}
        for k, v in values.items():
            assert k in ENV_NAMES, k
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def configure(self, **overrides):
        """The fully configured deployment, with fakes, then any overrides (None removes one)."""
        base = {"SITE_PASSWORD": FAKE_SITE_PASSWORD, "SITE_USERNAME": None,
                "SPB_SERVICE_KEY": FAKE_SERVICE_KEY, "BLOB_READ_WRITE_TOKEN": FAKE_TOKEN,
                "SPB_SMTP_USER": None, "SPB_SMTP_PASSWORD": FAKE_SMTP_PASSWORD,
                "SPB_RESET_TO": None, "SPB_CONTACT_PASSWORD": "fake-contact-password-not-real"}
        base.update(overrides)
        self.env(**base)

    # ---------------------------------------------------------------- start / stop
    def start(self, serve_functions=True, capture="hook"):
        """capture="hook" (pytest, which swaps sys.stderr between phases) wraps the module's log();
        capture="tee" (the subprocess the Node suite drives) copies sys.stderr."""
        A = self.A
        blob_port = self.blob.start()
        smtp_port = self.smtp.start()
        self.ports.update(blob=blob_port, smtp=smtp_port)
        A.clock = self.now
        A.sleep = lambda s: None            # the floor on /forgot is tested on its own
        A.MIN_FORGOT_SECONDS = 0
        A.TRANSPORT = self._blob_transport(blob_port)
        A.SMTP_FACTORY = self._smtp_factory(smtp_port)
        self._orig_log = None
        if capture == "tee":
            self._real_stderr = sys.stderr
            sys.stderr = _Tee(self._real_stderr, self.logs)
        else:
            self._orig_log = A.log

            def hooked(event, _orig=self._orig_log):
                self.logs.append("[auth] " + event + "\n")
                _orig(event)

            A.log = hooked
        if serve_functions:
            for name in ("forgot", "reset"):
                cls = load_handler(self.api_dir, name)
                srv = _Server(("127.0.0.1", 0), cls)
                threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
                self.servers.append(srv)
                self.ports[name] = srv.server_address[1]
        return self

    def close(self):
        for srv in self.servers:
            srv.shutdown()
            srv.server_close()
        self.blob.stop()
        self.smtp.stop()
        if self._real_stderr is not None:
            sys.stderr = self._real_stderr
        if self._orig_log is not None:
            self.A.log = self._orig_log
        if self._saved_env is not None:
            for k, v in self._saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    # ---------------------------------------------------------------- the seams
    def _blob_transport(self, port):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        store_error = self.A.StoreError

        def transport(method, url, headers, body, timeout):
            parts = urllib.parse.urlsplit(url)
            host = parts.netloc
            if parts.scheme != "https" or not (host == "vercel.com" and parts.path.startswith("/api/blob")
                                               or host.endswith(".private.blob.vercel-storage.com")):
                raise AssertionError("test harness: refusing a call to %s" % url)
            fake = "http://127.0.0.1:%d/fake/%s%s%s" % (
                port, host, parts.path, "?" + parts.query if parts.query else "")
            req = urllib.request.Request(fake, data=body, method=method, headers=headers)
            try:
                with opener.open(req, timeout=timeout) as r:
                    return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
            except urllib.error.HTTPError as e:
                return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
            except Exception as e:                                       # noqa: BLE001
                raise store_error(type(e).__name__)

        return transport

    @staticmethod
    def _smtp_factory(port):
        import smtplib

        def factory(host, real_port, timeout):
            assert (host, real_port) == ("smtp.gmail.com", 465), (host, real_port)
            return smtplib.SMTP("127.0.0.1", port, local_hostname="localhost", timeout=timeout)

        return factory

    # ---------------------------------------------------------------- reading the world
    def mailbox(self):
        """Parsed messages: subject, from, to, text, headers (a list of names), raw bytes."""
        out = []
        with self.smtp.lock:
            msgs = list(self.smtp.messages)
        for m in msgs:
            parsed = message_from_bytes(m["data"], policy=policy.default)
            out.append({"subject": parsed["Subject"], "from": parsed["From"], "to": parsed["To"],
                        "envelope_to": m["to"], "envelope_from": m["from"],
                        "text": parsed.get_content().replace("\r\n", "\n"), "header_names": [k for k, _ in parsed.items()],
                        "multipart": parsed.is_multipart(), "content_type": parsed.get_content_type(),
                        "raw": m["data"].decode("utf-8", "replace")})
        return out

    def code_in_mail(self, index=-1):
        import re
        return re.search(r"\b(\d{6})\b", self.mailbox()[index]["text"]).group(1)

    def stderr_text(self):
        return "".join(self.logs)

    def wipe(self):
        """Back to an empty world: no blobs, no mail, no logs, clock at real time."""
        self.blob.reset()
        self.smtp.reset()
        self.logs.clear()
        self.offset = 0.0


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------- subprocess mode

def _control_server(backend):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, obj, status=200):
            data = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _go(self):
            n = int(self.headers.get("Content-Length") or 0)
            arg = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            cmd = self.path.split("?")[0]
            A = backend.A
            if cmd == "/env":
                backend.env(**arg)
                return self._reply({"ok": True})
            if cmd == "/configure":
                backend.configure(**arg)
                return self._reply({"ok": True})
            if cmd == "/advance":
                backend.advance(arg["seconds"])
                return self._reply({"ok": True, "offset": backend.offset})
            if cmd == "/mailbox":
                return self._reply({"messages": backend.mailbox()})
            if cmd == "/logs":
                return self._reply({"text": backend.stderr_text()})
            if cmd == "/wipe":
                backend.wipe()
                return self._reply({"ok": True})
            if cmd == "/configured":
                return self._reply({"configured": bool(A.configured())})
            if cmd == "/smtp":
                backend.smtp.mode = arg.get("mode", backend.smtp.mode)
                backend.smtp.delay = arg.get("delay", backend.smtp.delay)
                return self._reply({"ok": True})
            if cmd == "/smtp_logins":
                return self._reply({"logins": backend.smtp.logins})
            if cmd == "/floor":
                A.MIN_FORGOT_SECONDS = arg["seconds"]
                A.sleep = time.sleep if arg["seconds"] else (lambda s: None)
                return self._reply({"ok": True})
            return self._reply({"error": "no such control"}, 404)

        do_GET = do_POST = _go

    srv = _Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    return srv.server_address[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-dir", default=None)
    args = ap.parse_args()
    backend = Backend(api_dir=args.api_dir)
    backend.configure()
    backend.start(capture="tee")
    ctl = _control_server(backend)
    ports = dict(backend.ports, ctl=ctl)
    sys.stdout.write(json.dumps(ports) + "\n")
    sys.stdout.flush()
    sys.stdin.read()                         # the parent closing our stdin is the stop signal
    backend.close()


if __name__ == "__main__":
    main()
