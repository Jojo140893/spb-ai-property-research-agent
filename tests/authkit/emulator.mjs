// A small stand-in for Vercel, for the password-reset tests only.
//
// What it does: runs the REAL generated middleware.js on a request exactly as Vercel does, and when
// the middleware lets a request through (returns undefined) it hands POST /api/auth/forgot and
// POST /api/auth/reset to the REAL Python handlers (api/auth/*.py) over a socket, and answers
// anything else with a stand-in "static file" so a test can tell "served" from "refused".
//
// What it fakes, and how it is made impossible to reach anything real:
//   * fetch(): replaced. A call to a Vercel Blob store host or vercel.com/api/blob is rewritten to
//     the local fake Blob service (tests/authkit/fake_blob.py); ANY other URL throws. The middleware
//     therefore cannot contact a real host whatever it tries.
//   * Date.now(): shifted by advance(), so "15 minutes later" costs nothing. The Python side's clock
//     is shifted by the same amount through the backend's control API.
//   * the 700 ms pause after a failed login is shortened to nothing: it is not under test here
//     (tests/middleware_checks.mjs keeps it) and 40 failed logins would otherwise cost 28 seconds.
//   * the Python side runs in a subprocess (tests/authkit/backend.py): fake Blob, fake SMTP on
//     127.0.0.1, and the handlers. Its stderr is kept so the tests can read what it logged.
import { spawn } from "node:child_process";
import http from "node:http";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const ORIGIN = "https://app.smartpropertybuying.com.au";
export const HOST = "app.smartpropertybuying.com.au";
export const FAKE = {
  token: "vercel_blob_rw_fakestore0123_NOTAREALSECRETnotarealsecret9876",
  site: "fake-site-password-not-the-real-one",
  service: "fake-service-key-not-the-real-one-0001",
  smtpPassword: "fake-app-password-not-real",
  contact: "fake-contact-password-not-real",
  user: "digital@smartpropertybuying.com.au",
};

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..", "..");

// ---------------------------------------------------------------------- clock and timers
const realNow = Date.now.bind(Date);
const realSetTimeout = globalThis.setTimeout;
let clockOffsetMs = 0;
Date.now = () => realNow() + clockOffsetMs;
globalThis.setTimeout = (fn, ms, ...rest) => realSetTimeout(fn, ms === 700 ? 0 : ms, ...rest);

// ---------------------------------------------------------------------- http helper (not fetch)
function httpJson(port, method, urlPath, body) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : Buffer.from(JSON.stringify(body));
    const req = http.request({ host: "127.0.0.1", port, method, path: urlPath,
      headers: data ? { "content-type": "application/json", "content-length": data.length } : {} },
      (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf-8");
          try { resolve(JSON.parse(text)); } catch (e) { resolve({ raw: text }); }
        });
      });
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}

export class Backend {
  static async start({ apiDir } = {}) {
    const python = process.env.SPB_PYTHON || "python";
    const args = ["-u", "-m", "tests.authkit.backend"];
    const dir = apiDir || process.env.SPB_AUTH_API_DIR;
    if (dir) args.push("--api-dir", dir);
    const child = spawn(python, args, {
      cwd: repoRoot, stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8" } });
    const b = new Backend(child);
    child.stderr.on("data", (c) => { b.stderr += c.toString("utf-8"); });
    b.ports = await new Promise((resolve, reject) => {
      let buf = "";
      child.stdout.on("data", (c) => {
        buf += c.toString("utf-8");
        const nl = buf.indexOf("\n");
        if (nl >= 0) { try { resolve(JSON.parse(buf.slice(0, nl))); } catch (e) { reject(e); } }
      });
      child.on("exit", (code) => reject(new Error("backend exited early (" + code + "): " + b.stderr)));
      realSetTimeout(() => reject(new Error("backend did not start: " + b.stderr)), 20000);
    });
    return b;
  }

  constructor(child) { this.child = child; this.stderr = ""; this.ports = null; }

  ctl(cmd, arg) { return httpJson(this.ports.ctl, "POST", cmd, arg || {}); }
  blobCtl(cmd, arg) { return httpJson(this.ports.blob, "POST", "/__ctl/" + cmd, arg || {}); }

  async text(pathname) { return (await this.blobCtl("text", { pathname })).text; }
  async plant(pathname, text) { await this.blobCtl("put", { pathname, text }); }
  async remove(pathname) { await this.blobCtl("delete", { pathname }); }
  async blobLog(kind, method) { return (await this.blobCtl("log", { kind, method })).log; }
  async clearBlobLog() { await this.blobCtl("clear_log"); }
  async blobMode(mode) { await this.blobCtl("mode", { mode }); }
  async blobFault(fault) { await this.blobCtl("fault", fault); }
  async mailbox() { return (await this.ctl("/mailbox")).messages; }
  async logs() { return (await this.ctl("/logs")).text; }
  async smtp(mode, delay) { await this.ctl("/smtp", { mode, delay }); }
  async pythonConfigured() { return (await this.ctl("/configured")).configured; }

  async stop() {
    this.child.stdin.end();
    await new Promise((resolve) => { this.child.on("exit", resolve); realSetTimeout(resolve, 5000); });
  }
}

// ---------------------------------------------------------------------- the world
export async function startWorld({ mwPath, apiDir } = {}) {
  const backend = await Backend.start({ apiDir });
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const u = new URL(typeof input === "string" ? input : input.url);
    if (u.protocol === "https:" && (u.hostname === "vercel.com" ||
        u.hostname.endsWith(".private.blob.vercel-storage.com"))) {
      return realFetch("http://127.0.0.1:" + backend.ports.blob + "/fake/" + u.host + u.pathname + u.search, init);
    }
    throw new Error("test harness: refusing a real network call to " + u.href);
  };
  const mod = await import(pathToFileURL(mwPath || process.env.SPB_MW_PATH).href + "?v=" + realNow());
  const mw = mod.default;

  const world = {
    backend, mw, config: mod.config,

    // Environment, for the middleware (this process) and the functions (the subprocess) alike.
    async env(values) {
      for (const [k, v] of Object.entries(values)) {
        if (v === null || v === undefined) delete process.env[k]; else process.env[k] = v;
      }
      await backend.ctl("/env", values);
    },
    async configure(overrides = {}) {
      const base = { SITE_PASSWORD: FAKE.site, SITE_USERNAME: null, SPB_SERVICE_KEY: FAKE.service,
        BLOB_READ_WRITE_TOKEN: FAKE.token, SPB_SMTP_USER: null, SPB_SMTP_PASSWORD: FAKE.smtpPassword,
        SPB_RESET_TO: null, SPB_CONTACT_PASSWORD: FAKE.contact, ...overrides };
      await world.env(base);
    },
    // A clean slate: no records, no mail, no logs, real time, and the middleware's own caches
    // dropped by moving past their longest lifetime (a new "instance").
    async fresh(overrides = {}) {
      await backend.ctl("/wipe");
      await backend.blobCtl("reset");
      clockOffsetMs = 0;
      await backend.ctl("/advance", { seconds: 0 });
      await world.configure(overrides);
      await world.coldStart();
    },
    // A new edge instance: its in-memory cache is empty. The module keeps the cache in module
    // scope, so the way to get a cold one is to import the file again.
    async coldStart() {
      const again = await import(pathToFileURL(mwPath || process.env.SPB_MW_PATH).href + "?v=" + realNow() + Math.random());
      world.mw = again.default;
    },
    async advance(seconds) {
      clockOffsetMs += seconds * 1000;
      await backend.ctl("/advance", { seconds });
    },

    // ---- requests
    req(p, { method = "GET", cookie = "", accept = "*/*", body = null, headers = {}, ctype = null } = {}) {
      const h = { accept, host: HOST, ...headers };
      if (cookie) h.cookie = cookie;
      if (body !== null && ctype) h["content-type"] = ctype;
      return new Request(ORIGIN + p, { method, headers: h, body });
    },
    form(p, fields, opts = {}) {
      return world.req(p, { method: "POST", body: new URLSearchParams(fields).toString(),
                            ctype: "application/x-www-form-urlencoded", ...opts });
    },
    json(p, obj, { origin = ORIGIN, ctype = "application/json", raw = null, headers = {}, cookie = "" } = {}) {
      const h = { origin, "sec-fetch-site": "same-origin", ...headers };
      return world.req(p, { method: "POST", body: raw !== null ? raw : JSON.stringify(obj),
                            ctype, headers: h, cookie });
    },

    // What Vercel does: the middleware first, then (if it lets the request through) the route.
    async call(request) {
      const res = await world.mw(request);
      if (res !== undefined) return res;
      const url = new URL(request.url);
      if (request.method === "POST" && (url.pathname === "/api/auth/forgot" || url.pathname === "/api/auth/reset")) {
        return forward(backend, request, url);
      }
      return new Response("static file", { status: 200, headers: { "x-served-by": "static" } });
    },
    served(res) { return res.headers.get("x-served-by") === "static"; },

    // ---- conveniences
    async login(password, { email = FAKE.user, next = null } = {}) {
      const f = { email, password };
      if (next !== null) f.next = next;
      return world.call(world.form("/login", f));
    },
    async cookieFor(password) {
      const res = await world.login(password);
      if (res.status !== 303) return null;
      return (res.headers.get("set-cookie") || "").split(";")[0];
    },
    async sessionWorks(cookie) {
      const res = await world.call(world.req("/stock.json", { cookie }));
      return world.served(res);
    },
    async forgot(opts) { return world.call(world.json("/api/auth/forgot", {}, opts)); },
    async reset(code, password, confirm, opts) {
      return world.call(world.json("/api/auth/reset",
        { code, password, confirm: confirm === undefined ? password : confirm }, opts));
    },
    async lastCode() {
      const box = await backend.mailbox();
      const m = /\b(\d{6})\b/.exec(box[box.length - 1].text);
      return m && m[1];
    },

    async stop() {
      globalThis.fetch = realFetch;
      await backend.stop();
    },
  };
  return world;
}

function forward(backend, request, url) {
  return new Promise(async (resolve, reject) => {
    const port = url.pathname.endsWith("/forgot") ? backend.ports.forgot : backend.ports.reset;
    const data = Buffer.from(await request.arrayBuffer());
    const headers = {};
    request.headers.forEach((v, k) => { headers[k] = v; });
    headers.host = url.host;
    headers["content-length"] = String(data.length);
    const req = http.request({ host: "127.0.0.1", port, method: request.method, path: url.pathname, headers }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        const h = new Headers();
        for (const [k, v] of Object.entries(res.headers)) h.set(k, Array.isArray(v) ? v.join(", ") : v);
        resolve(new Response(Buffer.concat(chunks), { status: res.statusCode, headers: h }));
      });
    });
    req.on("error", reject);
    req.write(data);
    req.end();
  });
}
