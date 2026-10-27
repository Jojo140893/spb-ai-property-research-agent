"""A local SMTP server for the password-reset tests. It only ever listens on 127.0.0.1.

Real SMTP, not a mock of smtplib: the code under test opens a real socket, says EHLO, logs in with
AUTH PLAIN or LOGIN, and sends a message with MAIL FROM / RCPT TO / DATA, and this server records
what arrived. So a test reads the "mailbox" the way Coleen would, and can check that the one thing in
the email is the code and the fixed text, that nothing was sent to anyone but the fixed address, and
that nothing at all was sent when it should not have been.

Not implemented on purpose: TLS. The production code uses SMTP_SSL to smtp.gmail.com:465; the tests
swap in a plain connection to this server through the SMTP_FACTORY seam (api/_spb_auth.py), because
certificates are not what is under test here.

Failure injection (self.mode): "ok", "auth_fail" (535 on login), "down" (421 and hang up at once),
"drop" (hang up after the greeting), "slow" (wait self.delay before every reply).
"""
import base64
import socketserver
import threading
import time


class FakeSMTP:
    def __init__(self, user, password):
        self.user, self.password = user, password
        self.messages = []           # {"from", "to", "data", "user"}
        self.logins = []             # [(user, password_was_correct)]
        self.mode = "ok"
        self.delay = 0.0
        self.lock = threading.Lock()
        self.server = None

    def start(self):
        fake = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                fake._session(self)

        class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = True
            allow_reuse_address = True
            request_queue_size = 64          # default 5 refuses a concurrent burst on Windows

        self.server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        return self.server.server_address[1]

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def reset(self):
        with self.lock:
            self.messages.clear()
            self.logins.clear()
            self.mode = "ok"
            self.delay = 0.0

    # ------------------------------------------------------------------ protocol
    @staticmethod
    def _say(h, line):
        h.wfile.write((line + "\r\n").encode("ascii"))
        h.wfile.flush()

    def _session(self, h):
        say = lambda line: (time.sleep(self.delay) if self.delay else None, self._say(h, line))
        if self.mode == "down":
            return say("421 service not available")
        say("220 fake.local ESMTP ready")
        if self.mode == "drop":
            return
        authed = None
        sender, rcpts = None, []
        while True:
            raw = h.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            verb = line.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                h.wfile.write(b"250-fake.local\r\n250-AUTH PLAIN LOGIN\r\n250 8BITMIME\r\n")
                h.wfile.flush()
            elif verb == "AUTH":
                authed = self._auth(h, line, say)
            elif verb == "MAIL":
                if authed is None:
                    say("530 5.7.0 Authentication required")
                    continue
                sender, rcpts = line.split(":", 1)[1].strip().strip("<>").split(" ")[0].strip("<>"), []
                say("250 ok")
            elif verb == "RCPT":
                rcpts.append(line.split(":", 1)[1].strip().strip("<>"))
                say("250 ok")
            elif verb == "DATA":
                say("354 end with <CRLF>.<CRLF>")
                data = []
                while True:
                    l = h.rfile.readline()
                    if not l or l in (b".\r\n", b".\n"):
                        break
                    data.append(l[1:] if l.startswith(b"..") else l)
                with self.lock:
                    self.messages.append({"from": sender, "to": list(rcpts), "data": b"".join(data),
                                          "user": authed})
                say("250 queued")
            elif verb == "RSET":
                sender, rcpts = None, []
                say("250 ok")
            elif verb == "NOOP":
                say("250 ok")
            elif verb == "QUIT":
                say("221 bye")
                return
            else:
                say("502 not implemented")

    def _auth(self, h, line, say):
        parts = line.split(" ")
        method = parts[1].upper() if len(parts) > 1 else ""
        if method == "PLAIN":
            blob = parts[2] if len(parts) > 2 else None
            if blob is None:
                self._say(h, "334 ")
                blob = h.rfile.readline().decode().strip()
            _authzid, user, password = base64.b64decode(blob).decode().split("\x00")
        elif method == "LOGIN":
            if len(parts) > 2:                       # smtplib sends the user name with the verb
                user = base64.b64decode(parts[2]).decode()
            else:
                self._say(h, "334 " + base64.b64encode(b"Username:").decode())
                user = base64.b64decode(h.rfile.readline().strip()).decode()
            self._say(h, "334 " + base64.b64encode(b"Password:").decode())
            password = base64.b64decode(h.rfile.readline().strip()).decode()
        else:
            say("504 unrecognised authentication type")
            return None
        good = self.mode != "auth_fail" and user == self.user and password == self.password
        with self.lock:
            self.logins.append((user, good))
        if not good:
            say("535 5.7.8 Username and Password not accepted")
            return None
        say("235 2.7.0 Accepted")
        return user
