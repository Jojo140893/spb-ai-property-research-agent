"""A stand-in for the Vercel Blob service (a PRIVATE store), for tests of the password reset only.

It speaks the wire protocol that @vercel/blob 2.x speaks and that Vercel documents at
vercel.com/docs/vercel-blob (private storage, conditional writes, consistent reads), and it is
deliberately UNFRIENDLY in the same ways the real service is, because a fake that is kinder than
the service would let a wrong client pass:

  CONTENT READS   GET https://<storeId>.private.blob.vercel-storage.com/<pathname>[?cache=0]
      Authorization: Bearer <token> or 403. 404 for a pathname with no blob. 200 + ETag + body.
      Reads go through a CDN: without ?cache=0 an overwritten blob can still be served in its
      PREVIOUS version for up to 60 seconds ("Consistent reads", private-storage docs). ?cache=0
      always reads the current version.
  WRITES          PUT https://vercel.com/api/blob/?pathname=<pathname>
      Authorization (403 when wrong), x-api-version, x-vercel-blob-store-id, x-vercel-blob-access
      (must be "private" for this private store), x-add-random-suffix, x-content-type,
      x-allow-overwrite ("1" to replace an existing blob, otherwise an existing pathname is an
      error), x-if-match (the write succeeds only if the blob's current ETag is exactly this;
      otherwise 412 {"error":{"code":"precondition_failed"}}). 200 + JSON
      {url, downloadUrl, pathname, contentType, contentDisposition, etag}.
  HEAD            GET https://vercel.com/api/blob/?url=<url or pathname> -> metadata JSON or 404.

ASSUMED, because the public docs do not give the wire text: the error body for a create-only write
to a pathname that already exists (here 400 {"error":{"code":"bad_request","message":"This blob
already exists..."}}), and the ETag format (here the quoted MD5 of the body, as S3 does). The client
under test accepts 400, 409 and 412 as "lost the race" on a create-only write and re-reads, so it
does not depend on the guess.

Requests reach it as http://127.0.0.1:<port>/fake/<original host>/<original path and query>, the
harness having rewritten the real URL; nothing here ever touches a network. Control calls
(/__ctl/...) are for the tests: failure injection, a clock, corrupting a record, and a log of every
request the client under test made (headers included, tokens redacted), so a test can assert that
a call WAS or WAS NOT made.
"""
import hashlib
import http.server
import json
import re
import secrets
import socketserver
import threading
import time
import urllib.parse

HOST_RE = re.compile(r"^([a-z0-9]+)\.private\.blob\.vercel-storage\.com$")
CDN_TTL = 60.0


class FakeBlob:
    def __init__(self, token, now=time.time):
        self.token = token
        m = re.match(r"^vercel_blob_rw_([A-Za-z0-9]+)_", token)
        self.store_id = m.group(1).lower()
        self.now = now
        self.lock = threading.Lock()
        self.blobs = {}              # pathname -> {"body": bytes, "etag": str, "type": str, "at": float}
        self.cdn = {}                # pathname -> {"body", "etag", "type", "at"} what a CDN node holds
        self.log = []                # one dict per request
        self.mode = "ok"             # ok | outage (503) | forbidden (403) | garbage (200 + junk)
        self.faults = []             # [{"on": "read"|"write"|"any", "status": 503, "count": 1, "delay": 0}]
        self.server = None
        self.thread = None

    # ---------------------------------------------------------------- lifecycle
    def start(self):
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _go(self):
                fake.handle(self)

            do_GET = do_PUT = do_POST = do_DELETE = _go

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        return self.server.server_address[1]

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    # ---------------------------------------------------------------- test controls
    def reset(self):
        with self.lock:
            self.blobs.clear()
            self.cdn.clear()
            self.log.clear()
            self.faults.clear()
            self.mode = "ok"

    def put_raw(self, pathname, body, content_type="application/json"):
        """Plant a blob without going through the API (a record somebody tampered with)."""
        with self.lock:
            self._store(pathname, body if isinstance(body, bytes) else body.encode("utf-8"),
                        content_type)

    def delete_raw(self, pathname):
        with self.lock:
            self.blobs.pop(pathname, None)

    def text(self, pathname):
        with self.lock:
            b = self.blobs.get(pathname)
            return b["body"].decode("utf-8") if b else None

    def requests(self, method=None, kind=None):
        with self.lock:
            return [r for r in self.log
                    if (method is None or r["method"] == method) and (kind is None or r["kind"] == kind)]

    # ---------------------------------------------------------------- internals
    def _store(self, pathname, body, content_type):
        self.blobs[pathname] = {"body": body, "etag": '"%s"' % hashlib.md5(body).hexdigest(),
                                "type": content_type, "at": self.now()}

    @staticmethod
    def _json(h, status, obj, extra=()):
        data = json.dumps(obj).encode("utf-8")
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        for k, v in extra:
            h.send_header(k, v)
        h.end_headers()
        h.wfile.write(data)

    @staticmethod
    def _error(h, status, code, message):
        FakeBlob._json(h, status, {"error": {"code": code, "message": message}})

    def _take_fault(self, op):
        """The next injected failure for this kind of request, if any. "skip": n lets the first n
        matching requests through untouched (to fail the SECOND write of a flow, say)."""
        with self.lock:
            for f in self.faults:
                if f["on"] in (op, "any") and f.get("count", 1) > 0:
                    if f.get("skip", 0) > 0:
                        f["skip"] -= 1
                        return None
                    f["count"] = f.get("count", 1) - 1
                    return dict(f)
        return None

    def handle(self, h):
        parts = urllib.parse.urlsplit(h.path)
        length = int(h.headers.get("Content-Length") or 0)
        body = h.rfile.read(length) if length else b""
        if parts.path.startswith("/__ctl/"):
            return self._control(h, parts, body)
        segs = parts.path.split("/", 3)               # ["", "fake", host, rest]
        if len(segs) < 4 or segs[1] != "fake":
            return self._error(h, 404, "not_found", "no such route")
        host, path = segs[2], "/" + segs[3]
        query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
        headers = {k.lower(): v for k, v in h.headers.items()}
        auth = headers.get("authorization", "")
        entry = {"method": h.command, "host": host, "path": path, "query": {k: v[0] for k, v in query.items()},
                 "headers": {k: ("<redacted>" if k == "authorization" else v) for k, v in headers.items()},
                 "authorized": auth == "Bearer " + self.token, "time": self.now()}
        content = HOST_RE.match(host)
        entry["kind"] = "read" if content else ("write" if h.command == "PUT" else "api")
        with self.lock:
            self.log.append(entry)
        op = "read" if content else "write"
        fault = self._take_fault(op)
        if fault:
            if fault.get("delay"):
                time.sleep(fault["delay"])
            if fault.get("drop"):
                h.close_connection = True
                h.wfile.flush()
                return
            if fault.get("status"):
                return self._error(h, fault["status"], "internal_server_error", "injected failure")
        if self.mode == "outage":
            return self._error(h, 503, "service_unavailable", "injected outage")
        if self.mode == "forbidden":
            return self._error(h, 403, "forbidden", "injected: token rejected")
        if self.mode == "garbage":
            data = b"<html>not a blob</html>"
            h.send_response(200)
            h.send_header("Content-Length", str(len(data)))
            h.send_header("ETag", '"junk"')
            h.end_headers()
            h.wfile.write(data)
            return
        if content:
            return self._content_read(h, content.group(1), path, query, auth)
        if host == "vercel.com" and path.rstrip("/") == "/api/blob":
            if h.command == "PUT":
                return self._put(h, query, headers, body, auth)
            if h.command == "GET":
                return self._head(h, query, auth)
        return self._error(h, 404, "not_found", "no such route")

    # ---------------------------------------------------------------- reads
    def _content_read(self, h, store, path, query, auth):
        if auth != "Bearer " + self.token:
            return self._error(h, 403, "forbidden", "This request is not authorised")
        if store != self.store_id:
            return self._error(h, 404, "store_not_found", "This store does not exist")
        pathname = urllib.parse.unquote(path[1:])
        fresh = "cache" in query and query["cache"][0] == "0"
        with self.lock:
            now = self.now()
            hit = self.cdn.get(pathname)
            if not fresh and hit and now - hit["at"] < CDN_TTL:
                served = hit                                   # possibly the previous version
            else:
                blob = self.blobs.get(pathname)
                if blob is None:
                    served = None                              # a 404 is not cached
                else:
                    served = {"body": blob["body"], "etag": blob["etag"], "type": blob["type"],
                              "at": now}
                    if not fresh:
                        self.cdn[pathname] = served
        if served is None:
            return self._error(h, 404, "not_found", "The requested blob does not exist")
        h.send_response(200)
        h.send_header("Content-Type", served["type"])
        h.send_header("Content-Length", str(len(served["body"])))
        h.send_header("ETag", served["etag"])
        h.send_header("Cache-Control", "public, max-age=2592000")
        h.end_headers()
        h.wfile.write(served["body"])

    def _head(self, h, query, auth):
        if auth != "Bearer " + self.token:
            return self._error(h, 403, "forbidden", "This request is not authorised")
        target = (query.get("url") or [""])[0]
        pathname = urllib.parse.unquote(urllib.parse.urlsplit(target).path.lstrip("/")) \
            if "://" in target else target
        with self.lock:
            blob = self.blobs.get(pathname)
        if blob is None:
            return self._error(h, 404, "not_found", "The requested blob does not exist")
        url = "https://%s.private.blob.vercel-storage.com/%s" % (self.store_id, pathname)
        self._json(h, 200, {"url": url, "downloadUrl": url + "?download=1", "pathname": pathname,
                            "size": len(blob["body"]), "contentType": blob["type"],
                            "contentDisposition": "inline", "cacheControl": "public, max-age=2592000",
                            "uploadedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(blob["at"])),
                            "etag": blob["etag"]})

    # ---------------------------------------------------------------- writes
    def _put(self, h, query, headers, body, auth):
        if auth != "Bearer " + self.token:
            return self._error(h, 403, "forbidden", "This request is not authorised")
        if headers.get("x-vercel-blob-store-id", "").lower() != self.store_id:
            return self._error(h, 403, "forbidden", "The store id does not match the token")
        if not headers.get("x-api-version", "").isdigit():
            return self._error(h, 400, "bad_request", "Missing or invalid x-api-version header")
        if headers.get("x-vercel-blob-access") != "private":
            return self._error(h, 400, "bad_request",
                               "The access mode does not match this store (it is private)")
        pathname = (query.get("pathname") or [""])[0]
        if (not pathname or len(pathname) > 950 or "//" in pathname
                or re.search(r"[\x00-\x1f]", pathname) or pathname.startswith("/")):
            return self._error(h, 400, "bad_request", "Invalid pathname")
        if len(body) > 1024 * 1024:
            return self._error(h, 400, "bad_request", "The file is too large")
        if_match = headers.get("x-if-match")
        overwrite = headers.get("x-allow-overwrite") == "1"
        suffix = headers.get("x-add-random-suffix") == "1"
        if if_match and not overwrite:
            return self._error(h, 400, "bad_request", "x-if-match requires x-allow-overwrite: 1")
        if suffix:
            stem, dot, ext = pathname.rpartition(".")
            pathname = (stem + "-" + secrets.token_hex(15) + dot + ext) if dot else pathname + "-" + secrets.token_hex(15)
        with self.lock:                       # check and write are one atomic step: that is the CAS
            existing = self.blobs.get(pathname)
            if if_match is not None:
                if existing is None or existing["etag"] != if_match:
                    return self._error(h, 412, "precondition_failed", "Precondition failed: ETag mismatch.")
            elif existing is not None and not overwrite:
                return self._error(h, 400, "bad_request",
                                   "This blob already exists, use `allowOverwrite: true` to overwrite it")
            self._store(pathname, body, headers.get("x-content-type", "application/octet-stream"))
            etag = self.blobs[pathname]["etag"]
        url = "https://%s.private.blob.vercel-storage.com/%s" % (self.store_id, urllib.parse.quote(pathname))
        self._json(h, 200, {"url": url, "downloadUrl": url + "?download=1", "pathname": pathname,
                            "contentType": headers.get("x-content-type", "application/octet-stream"),
                            "contentDisposition": 'inline; filename="%s"' % pathname.rsplit("/", 1)[-1],
                            "etag": etag})

    # ---------------------------------------------------------------- control
    def _control(self, h, parts, body):
        cmd = parts.path[len("/__ctl/"):]
        arg = json.loads(body.decode("utf-8")) if body else {}
        out = {"ok": True}
        if cmd == "mode":
            self.mode = arg["mode"]
        elif cmd == "fault":
            with self.lock:
                self.faults.append(arg)
        elif cmd == "clear_faults":
            with self.lock:
                self.faults.clear()
        elif cmd == "reset":
            self.reset()
        elif cmd == "put":
            self.put_raw(arg["pathname"], arg["text"])
        elif cmd == "delete":
            self.delete_raw(arg["pathname"])
        elif cmd == "text":
            out["text"] = self.text(arg["pathname"])
        elif cmd == "paths":
            with self.lock:
                out["paths"] = sorted(self.blobs)
        elif cmd == "log":
            out["log"] = self.requests(arg.get("method"), arg.get("kind"))
        elif cmd == "clear_log":
            with self.lock:
                self.log.clear()
        else:
            return self._error(h, 404, "not_found", "no such control")
        self._json(h, 200, out)
