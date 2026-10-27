"""Every stdlib mail connection checks the server's certificate and the server's name.

25 Oct 2026. sources/email_inbox.py opened the digital@ inbox with
imaplib.IMAP4_SSL(host, port), and run_daily._alert sent through
smtplib.SMTP_SSL(host, port). With no context, both fall back to
ssl._create_stdlib_context() on Python 3.11 and 3.12 -- CERT_NONE, no hostname check -- so
anything that answered on the port finished the handshake with any certificate at all, and
LOGIN then handed it the Gmail app password.

The impostor here is a real TLS server on 127.0.0.1 with a throwaway self-signed
certificate made for the test. The control test proves it is a working server -- a client
built the old way gets all the way to the greeting -- so a refusal in the other tests is
the certificate check and nothing else.

27 Oct 2026, after an adversarial review:
  * the context carries Google's own roots (mail_tls), because the laptop's Windows store
    had none and Gmail verified only through a cross-signature that ends in Jan 2028;
  * the impostor answers EVERY connection, so a refused client that retries unchecked is
    caught rather than left waiting in the backlog;
  * a stalled read costs one message, not every message after it;
  * the third mail login in the repo, api/_spb_auth.send_mail, is held to checking too.
"""
import email
import imaplib
import importlib
import logging
import smtplib
import socket
import ssl
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
# Not in requirements.txt by name, but always installed: pdfplumber -> pdfminer.six
# depends on it. Imported at the top so that if that ever changes the file fails to
# collect, loudly, rather than skipping and reading as a pass.
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

import mail_tls
import run_daily
from sources import email_inbox

API = Path(__file__).resolve().parents[1] / "api"
# Two of the Google Trust Services roots imap.gmail.com and smtp.gmail.com chain to.
GOOGLE_ROOTS = ("GTS Root R1", "GTS Root R4")

IMAP_GREETING = b"* OK evil.example ready\r\n"
SMTP_GREETING = b"220 evil.example ESMTP ready\r\n"


def _imap_reply(line: bytes) -> bytes:
    """Says yes to every command. imaplib sends CAPABILITY straight after the greeting
    and waits for the tagged answer, so the control needs at least this much."""
    tag = line.split(b" ", 1)[0]
    return b"* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n" + tag + b" OK done\r\n"
NOT_A_PASSWORD = "not-a-real-password"

# OpenSSL's X509_V_ERR_DEPTH_ZERO_SELF_SIGNED_CERT: refused because nobody vouches for the
# certificate, not because the connection broke some other way.
SELF_SIGNED = 18


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    """A self-signed certificate for evil.example, made fresh and thrown away."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "evil.example")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("evil.example")]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    folder = tmp_path_factory.mktemp("impostor")
    cert_path, key_path = folder / "cert.pem", folder / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return str(cert_path), str(key_path)


class _Impostor:
    """TLS on 127.0.0.1, answered with a certificate nobody vouches for.

    EVERY connection is served, not only the first. Serving one let a client that was
    refused and then retried without the check pass every test here: the retry sat in the
    backlog unanswered and timed out, where a real impostor would have taken it and the
    password with it. Counts connections and handshakes and keeps every byte sent after a
    handshake, across all of them, so a test can say not only that the client gave up but
    that it gave up before LOGIN.
    """

    def __init__(self, cert_path: str, key_path: str, greeting: bytes, reply=None):
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.load_cert_chain(cert_path, key_path)
        self._greeting = greeting
        self._reply = reply
        self._listener = socket.create_server(("127.0.0.1", 0))
        # Short, so finish() can tell an empty backlog from a connection still to come.
        self._listener.settimeout(0.2)
        self.port = self._listener.getsockname()[1]
        self.connections = 0
        self.handshakes = 0
        self.received = b""
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._served = []
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self):
        while True:
            try:
                raw, _ = self._listener.accept()
            except socket.timeout:
                if self._stopping.is_set():
                    return              # finish() was called and nothing is waiting
                continue
            except OSError:
                return                  # close(): the listener is gone
            with self._lock:
                self.connections += 1
            served = threading.Thread(target=self._serve, args=(raw,), daemon=True)
            self._served.append(served)
            served.start()

    def _serve(self, raw):
        raw.settimeout(15)
        try:
            with self._ctx.wrap_socket(raw, server_side=True) as tls:
                with self._lock:
                    self.handshakes += 1
                tls.sendall(self._greeting)
                pending = b""
                while True:
                    chunk = tls.recv(4096)
                    if not chunk:
                        break
                    with self._lock:
                        self.received += chunk
                    pending += chunk
                    while self._reply and b"\r\n" in pending:
                        line, pending = pending.split(b"\r\n", 1)
                        tls.sendall(self._reply(line))
        except (ssl.SSLError, OSError):
            pass            # the client walked away: what a refusal looks like from here
        finally:
            raw.close()

    def finish(self):
        """Take every connection already made and wait for each to end, so the counters
        are final. Called once the code under test has returned."""
        self._stopping.set()
        self._thread.join(timeout=20)
        assert not self._thread.is_alive(), "the impostor never stopped accepting"
        for served in self._served:
            served.join(timeout=20)
            assert not served.is_alive(), "the impostor never saw a connection end"

    def close(self):
        self._listener.close()


@pytest.fixture
def impostor(certificate):
    started = []

    def start(greeting: bytes) -> _Impostor:
        imp = _Impostor(*certificate, greeting,
                        reply=_imap_reply if greeting is IMAP_GREETING else None)
        started.append(imp)
        return imp

    yield start
    for imp in started:
        imp.close()


def _inbox_source():
    """The real source with a made-up login, skipping the vault lookup in __init__."""
    src = email_inbox.EmailStocklistSource.__new__(email_inbox.EmailStocklistSource)
    src.username, src.password, src.cred_source = "digital@example.com", NOT_A_PASSWORD, "test"
    return src


def _alert_env(monkeypatch, port):
    monkeypatch.setenv("SPB_ALERT_TO", "someone@example.com")
    monkeypatch.setenv("SPB_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SPB_SMTP_PORT", str(port))
    monkeypatch.setattr("secrets_store.get_credentials",
                        lambda portal, csv_fallback=("", ""): ("inbox@example.com",
                                                               NOT_A_PASSWORD, "env"))


# --- the impostor is a real server ----------------------------------------------------

@pytest.mark.parametrize("protocol", ["imap", "smtp"])
def test_the_impostor_answers_a_client_that_does_not_check(impostor, protocol):
    """The control. ssl._create_stdlib_context() is exactly what both calls fell back to
    before this fix; it completes the handshake and reads the greeting. Were the listener
    broken, every refusal below would pass for the wrong reason."""
    old_default = ssl._create_stdlib_context()
    if protocol == "imap":
        imp = impostor(IMAP_GREETING)
        box = imaplib.IMAP4_SSL("127.0.0.1", imp.port, ssl_context=old_default, timeout=10)
        assert box.welcome.startswith(b"* OK")
        box.shutdown()
    else:
        imp = impostor(SMTP_GREETING)
        smtp = smtplib.SMTP_SSL("127.0.0.1", imp.port, timeout=10, context=old_default)
        smtp.close()
    imp.finish()
    assert imp.handshakes == 1


def test_the_impostor_sees_a_retry_that_does_not_check(impostor):
    """The second connection is served and its bytes are counted. A client refused by the
    check that then retries the old way must show up here with the password, or every
    'never reaches an impostor' test below would pass while it went out."""
    imp = impostor(IMAP_GREETING)
    with pytest.raises(ssl.SSLCertVerificationError):
        email_inbox._open_imap("127.0.0.1", imp.port)
    box = imaplib.IMAP4_SSL("127.0.0.1", imp.port, ssl_context=ssl._create_stdlib_context(),
                            timeout=10)
    box.login("digital@example.com", NOT_A_PASSWORD)
    box.shutdown()
    imp.finish()
    assert imp.connections == 2
    assert imp.handshakes == 1
    assert NOT_A_PASSWORD.encode() in imp.received


# --- the roots the check trusts (mail_tls) ---------------------------------------------

def _root_names(ctx):
    return {value for cert in ctx.get_ca_certs()
            for rdn in cert.get("subject", ()) for key, value in rdn if key == "commonName"}


def test_the_mail_context_carries_google_s_roots_whatever_the_machine_holds(monkeypatch):
    """27 Oct 2026: the laptop's Windows store listed 31 roots and no Google Trust Services
    root; Gmail verified only through a GlobalSign cross-signature that ends on 28 Jan 2028.
    With the machine's own store emptied, the context must still trust Google's roots --
    and still require a certificate and the name."""
    monkeypatch.setattr(ssl.SSLContext, "load_default_certs", lambda self, purpose=None: None)
    ctx = mail_tls.verified_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    missing = [root for root in GOOGLE_ROOTS if root not in _root_names(ctx)]
    assert not missing, "not trusted without the machine's store: %s" % missing


# --- the inbox (sources/email_inbox.py) -----------------------------------------------

def test_the_inbox_refuses_a_self_signed_impostor(impostor):
    imp = impostor(IMAP_GREETING)
    with pytest.raises(ssl.SSLCertVerificationError) as refused:
        email_inbox._open_imap("127.0.0.1", imp.port)
    assert refused.value.verify_code == SELF_SIGNED, refused.value.verify_message
    imp.finish()
    assert imp.handshakes == 0


def test_the_inbox_password_never_reaches_an_impostor(impostor, monkeypatch, caplog):
    """The whole path the harvest takes: _connect() gives up, says why, and LOGIN is never
    sent."""
    imp = impostor(IMAP_GREETING)
    monkeypatch.setattr(email_inbox, "IMAP_HOST", "127.0.0.1")
    monkeypatch.setattr(email_inbox, "IMAP_PORT", imp.port)
    with caplog.at_level(logging.ERROR, logger="spb.source.email"):
        assert _inbox_source()._connect() is None
    assert "CERTIFICATE_VERIFY_FAILED" in caplog.text
    imp.finish()
    assert imp.received == b"", "the impostor was sent something after the handshake"
    assert NOT_A_PASSWORD.encode() not in imp.received


def test_the_inbox_refuses_a_trusted_certificate_for_another_name(impostor, monkeypatch,
                                                                    certificate):
    """The hostname half. Here the impostor's certificate IS trusted, so only the name check
    stands between it and the password: a certificate for evil.example must not be accepted
    for the address that was dialled."""
    real = ssl.create_default_context

    def trusting_the_impostor(*a, **k):
        ctx = real(*a, **k)
        ctx.load_verify_locations(cafile=certificate[0])
        return ctx

    monkeypatch.setattr(ssl, "create_default_context", trusting_the_impostor)
    imp = impostor(IMAP_GREETING)
    with pytest.raises(ssl.SSLCertVerificationError) as refused:
        email_inbox._open_imap("127.0.0.1", imp.port)
    assert "mismatch" in refused.value.verify_message, refused.value.verify_message
    imp.finish()
    assert imp.handshakes == 0


def test_the_inbox_context_requires_a_certificate_and_the_name(monkeypatch):
    seen = {}

    class _Capture:
        def __init__(self, host, port, ssl_context=None, timeout=None):
            seen.update(ssl_context=ssl_context, timeout=timeout)

        def login(self, user, password):
            pass

    monkeypatch.setattr(imaplib, "IMAP4_SSL", _Capture)
    assert _inbox_source()._connect() is not None
    ctx = seen["ssl_context"]
    assert isinstance(ctx, ssl.SSLContext), "no context: imaplib's unverified default"
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    assert set(GOOGLE_ROOTS) <= _root_names(ctx), "not mail_tls.verified_context()"
    assert seen["timeout"], "no timeout: a server that stops answering hangs the run"


def test_the_inbox_is_gmail_unless_told_otherwise(monkeypatch):
    """The default host is where the app password goes when nothing sets IMAP_HOST -- every
    hand-run entry point. It was Hostinger's, the provider the mailbox left on 12 Aug."""
    monkeypatch.delenv("IMAP_HOST", raising=False)
    fresh = importlib.reload(email_inbox)
    try:
        assert fresh.IMAP_HOST == "imap.gmail.com"
    finally:
        importlib.reload(email_inbox)


# --- a stalled read mid-sweep (EmailStocklistSource.search) -------------------------------

def _plain_message(n: int) -> bytes:
    """Not builder stock: _relevant turns it away, so only the sweep itself is exercised."""
    return (b"From: someone@unrelated.example\r\nSubject: note %d\r\n"
            b"Date: Mon, 26 Oct 2026 09:00:00 +1100\r\n\r\nhello\r\n" % n)


class _Box:
    """An inbox connection that stalls on one message and then behaves as a timed-out
    socket does: imaplib's file object refuses every later read."""

    def __init__(self, name, ids, fetched, stall_at=None):
        self.name, self.ids, self.fetched, self.stall_at = name, ids, fetched, stall_at
        self.dead = self.shut = False

    def select(self, mailbox, readonly=False):
        assert readonly, "the inbox must only ever be opened read-only"
        return "OK", [str(len(self.ids)).encode()]

    def search(self, charset, criteria):
        return "OK", [b" ".join(self.ids)]

    def fetch(self, mid, parts):
        self.fetched.append((self.name, mid.decode()))
        if self.dead:
            raise OSError("cannot read from timed out object")
        if mid == self.stall_at:
            self.dead = True
            raise TimeoutError("The read operation timed out")
        return "OK", [(b"%s (RFC822 {1}" % mid, _plain_message(int(mid))), b")"]

    def shutdown(self):
        self.shut = True

    def close(self):
        pass

    def logout(self):
        pass


def _sweep(monkeypatch, boxes):
    """Run the real search() over the given connections, one per _connect() call."""
    src = _inbox_source()
    src.days_back, src.brochures, src.domain_map = 7, [], {}
    queue = list(boxes)
    calls = []

    def connect():
        calls.append(1)
        return queue.pop(0) if queue else None

    monkeypatch.setattr(src, "_connect", connect)
    return src.search({}), len(calls)


IDS = [b"1", b"2", b"3", b"4", b"5"]


def test_a_stalled_read_costs_that_message_not_the_rest(monkeypatch, caplog):
    fetched = []
    first = _Box("first", IDS, fetched, stall_at=b"4")
    second = _Box("second", IDS, fetched)
    with caplog.at_level(logging.INFO, logger="spb.source.email"):
        _rows, connects = _sweep(monkeypatch, [first, second])
    assert connects == 2
    assert fetched == [("first", "5"), ("first", "4"),
                       ("second", "3"), ("second", "2"), ("second", "1")]
    assert first.shut, "the dead connection was not let go"
    assert "message 4 could not be read" in caplog.text
    assert "scanned 5 message(s), 0 relevant, 1 unreadable" in caplog.text


def test_a_connection_that_will_not_come_back_says_how_much_was_not_read(monkeypatch, caplog):
    fetched = []
    first = _Box("first", IDS, fetched, stall_at=b"4")
    with caplog.at_level(logging.INFO, logger="spb.source.email"):
        _rows, connects = _sweep(monkeypatch, [first])
    assert connects == 2
    # Nothing more is sent down a connection that can no longer be read.
    assert fetched == [("first", "5"), ("first", "4")]
    assert "after 2 of 5 message(s)" in caplog.text and "the 3 not read" in caplog.text
    assert "scanned 5 message(s), 0 relevant, 4 unreadable" in caplog.text


def test_reconnects_are_capped(monkeypatch, caplog):
    fetched = []
    boxes = [_Box("b%d" % i, IDS, fetched, stall_at=mid)
             for i, mid in enumerate([b"5", b"4", b"3", b"2"])]
    with caplog.at_level(logging.INFO, logger="spb.source.email"):
        _rows, connects = _sweep(monkeypatch, boxes)
    assert connects == 1 + email_inbox.IMAP_RECONNECTS
    assert fetched == [("b0", "5"), ("b1", "4"), ("b2", "3")]
    assert "scanned 5 message(s), 0 relevant, 5 unreadable" in caplog.text


# --- the failure alert (run_daily._alert) ---------------------------------------------

def test_the_alert_refuses_a_self_signed_impostor(impostor):
    imp = impostor(SMTP_GREETING)
    with pytest.raises(ssl.SSLCertVerificationError) as refused:
        run_daily._open_smtp("127.0.0.1", imp.port)
    assert refused.value.verify_code == SELF_SIGNED, refused.value.verify_message
    imp.finish()
    assert imp.handshakes == 0


def test_the_alert_password_never_reaches_an_impostor(impostor, monkeypatch, capsys):
    imp = impostor(SMTP_GREETING)
    _alert_env(monkeypatch, imp.port)
    run_daily._alert("subject", "body")
    out = capsys.readouterr().out
    assert "COULD NOT NOTIFY" in out and "CERTIFICATE_VERIFY_FAILED" in out, out
    imp.finish()
    assert imp.received == b"", "the impostor was sent something after the handshake"


def test_the_alert_context_requires_a_certificate_and_the_name(monkeypatch, capsys):
    seen = {}

    class _Capture:
        def __init__(self, host, port, timeout=None, context=None):
            seen.update(context=context, timeout=timeout)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            pass

        def send_message(self, msg):
            pass

    monkeypatch.setattr(smtplib, "SMTP_SSL", _Capture)
    _alert_env(monkeypatch, 465)
    run_daily._alert("subject", "body")
    assert "sent to someone@example.com" in capsys.readouterr().out
    ctx = seen["context"]
    assert isinstance(ctx, ssl.SSLContext), "no context: smtplib's unverified default"
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    assert set(GOOGLE_ROOTS) <= _root_names(ctx), "not mail_tls.verified_context()"
    assert seen["timeout"]


# --- the forgot-password mail (api/_spb_auth.send_mail) ------------------------------------

def test_the_forgot_password_mail_checks_the_server_too(monkeypatch):
    """The third stdlib mail login in the repo. It runs on Vercel, whose Linux store has
    Google's roots, and api/ is bundled on its own, so it keeps create_default_context()
    rather than mail_tls; this holds it to checking at all. The forgot-password tests swap
    the whole connection for a plain-socket fake, so nothing else looks at this line."""
    monkeypatch.syspath_prepend(str(API))
    auth = importlib.import_module("_spb_auth")
    monkeypatch.setattr(auth, "SMTP_FACTORY", None)
    monkeypatch.setenv("SPB_SMTP_PASSWORD", NOT_A_PASSWORD)
    seen = {}

    class _Capture:
        def __init__(self, host, port, local_hostname=None, timeout=None, context=None):
            seen.update(host=host, context=context)

        def login(self, user, password):
            pass

        def send_message(self, msg):
            pass

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP_SSL", _Capture)
    auth.send_mail("subject", "body")
    assert seen["host"] == "smtp.gmail.com"
    ctx = seen["context"]
    assert isinstance(ctx, ssl.SSLContext), "no context: smtplib's unverified default"
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
