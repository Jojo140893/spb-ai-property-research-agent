// Forgot password, the sign-in half: the REAL generated middleware.js against records made by the
// REAL reset flow (api/_spb_auth.py, through tests/test_forgot_password.py, which writes them to
// SPB_FIXTURES and the middleware to SPB_MW_PATH, then runs this file).
//
// What a reset changes, 23 Oct 2026: the new password arrives as the env var SPB_PW_RECORD (GitHub
// stores it and redeploys). So each "deploy" below is just a change of process.env. And because the
// whole point of the redesign is that sign-in never touches the network (Vercel Hobby's Blob quota),
// fetch() is replaced with a trap that counts calls, and the last check requires zero.
//
// Check names are matched by tests/test_forgot_password_mutations.py, which removes one safeguard at a
// time and requires the named check to go red, so rename one here and that test says so.
import crypto from "node:crypto";
import fs from "node:fs";
import { pathToFileURL } from "node:url";

const F = JSON.parse(fs.readFileSync(process.env.SPB_FIXTURES, "utf-8"));

let fetches = 0;
globalThis.fetch = async () => { fetches++; throw new Error("the middleware tried to make a network call"); };
// The 700 ms pause after a failed login is not under test here (tests/middleware_checks.mjs keeps it).
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...rest) => realSetTimeout(fn, ms === 700 ? 0 : ms, ...rest);

const mw = (await import(pathToFileURL(process.env.SPB_MW_PATH).href)).default;

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  PASS  " + name); }
  else { fail++; console.log("  FAIL  " + name + (detail !== undefined ? "  -> " + detail : "")); }
}
// SPB_ONLY=word,word runs only the sections whose title contains one of the words (the mutation test
// uses it so each mutant is judged by the section that should catch it).
const ONLY = (process.env.SPB_ONLY || "").split(",").map((x) => x.trim().toLowerCase()).filter(Boolean);
async function section(title, fn) {
  if (ONLY.length && !ONLY.some((k) => title.toLowerCase().includes(k))) return;
  console.log("\n--- " + title + " ---");
  await fn();
}

const ENV_KEYS = ["SITE_PASSWORD", "SITE_USERNAME", "SPB_AUTH_SECRET", "SPB_SMTP_PASSWORD", "SPB_SMTP_USER",
                  "GITHUB_DISPATCH_TOKEN", "SPB_PW_RECORD", "SPB_RESET_TO", "SPB_SITE_URL"];
function deploy(env) {                    // what a redeploy with these env vars amounts to
  for (const k of ENV_KEYS) delete process.env[k];
  Object.assign(process.env, env);
}
const OFF = { SITE_PASSWORD: F.site };
const ON = { SITE_PASSWORD: F.site, SPB_AUTH_SECRET: F.secret, SPB_SMTP_PASSWORD: F.smtp,
             GITHUB_DISPATCH_TOKEN: F.token };

function req(path, { method = "GET", cookie = "", accept = "*/*", body = null } = {}) {
  const headers = { accept };
  if (cookie) headers.cookie = cookie;
  if (body !== null) headers["content-type"] = "application/x-www-form-urlencoded";
  return new Request("https://app.smartpropertybuying.com.au" + path, { method, headers, body });
}
const call = (r) => mw(r);
const html = (path) => call(req(path, { accept: "text/html" }));
async function login(password, user = F.user) {
  return await call(req("/login", { method: "POST",
    body: "email=" + encodeURIComponent(user) + "&password=" + encodeURIComponent(password) }));
}
function cookieOf(res) {
  const sc = (res && res.headers.get("set-cookie")) || "";
  const m = /spb_session=([^;]+)/.exec(sc);
  return m ? "spb_session=" + m[1] : "";
}
async function signsIn(password) {
  const res = await login(password);
  return res.status === 303 && cookieOf(res) !== "";
}
// A cookie signed the way every session was signed before any reset: with SITE_PASSWORD itself.
function legacyCookie(site) {
  const exp = Math.floor(Date.now() / 1000) + 3600;
  const sig = crypto.createHmac("sha256", site).update(F.user + "|" + exp).digest("base64url");
  return "spb_session=" + exp + "." + sig;
}
const served = async (cookie) => (await call(req("/stock.json", { cookie }))) === undefined;
const epochOf = async () => (await html("/login")).headers.get("x-spb-auth-epoch");

await section("OFF: nothing new until the owner sets it up", async () => {
  deploy(OFF);
  const page = await html("/login");
  const text = await page.text();
  check("OFF: the login page is byte-for-byte what origin/main served", text === F.loginOff,
        text.length + " bytes vs " + F.loginOff.length);
  check("OFF: the login page has no Forgot password link", !text.includes("/forgot"));
  check("OFF: no x-spb-auth headers", page.headers.get("x-spb-auth-epoch") === null &&
        page.headers.get("x-spb-auth-id") === null);
  check("OFF: /forgot is gated like any page", (await html("/forgot")).status === 302);
  check("OFF: /reset is gated like any page", (await html("/reset")).status === 302);
  for (const p of ["/api/reset_code", "/api/reset_password", "/api/reset_check"]) {
    const res = await call(req(p, { method: "POST" }));
    check("OFF: POST " + p + " is not passed through", res && res.status === 401, res ? res.status : "PASSED");
  }
  check("OFF: SITE_PASSWORD signs in", await signsIn(F.site));
  check("OFF: a SITE_PASSWORD session works", await served(legacyCookie(F.site)));
});

await section("ON, no reset yet: the link, the pages, the five routes and nothing wider", async () => {
  deploy(ON);
  const page = await html("/login");
  const text = await page.text();
  check("ON: the login page links to /forgot", text.includes('<a href="/forgot">Forgot password?</a>'));
  check("ON: the epoch header says 0, the id none", page.headers.get("x-spb-auth-epoch") === "0" &&
        page.headers.get("x-spb-auth-id") === "none", page.headers.get("x-spb-auth-epoch"));
  check("ON: an ordinary /login says nothing about a new password", !text.includes("new password is live"));
  check("ON: /login?reset=1 says the new password is live",
        (await (await html("/login?reset=1")).text()).includes("Your new password is live"));
  check("ON: SITE_PASSWORD still signs in", await signsIn(F.site));
  check("ON: a SITE_PASSWORD session still works", await served(legacyCookie(F.site)));

  const forgot = await html("/forgot");
  const ft = await forgot.text();
  check("ON: GET /forgot is served without a login", forgot.status === 200, forgot.status);
  check("  ...names the masked address and offers no email box",
        ft.includes("d***@smartpropertybuying.com.au") && !/type="email"/.test(ft) && !ft.includes(F.user));
  const csp = forgot.headers.get("content-security-policy") || "";
  const nonce = (/nonce-([A-Za-z0-9_-]+)/.exec(csp) || [])[1];
  check("  ...script only by nonce, never framed", !!nonce && ft.includes('<script nonce="' + nonce + '">') &&
        csp.includes("frame-ancestors 'none'") && csp.includes("default-src 'none'"), csp);
  check("  ...not cached, no referrer", forgot.headers.get("cache-control") === "no-store" &&
        forgot.headers.get("referrer-policy") === "no-referrer");
  const reset = await html("/reset");
  const rt = await reset.text();
  check("ON: GET /reset is served without a login", reset.status === 200, reset.status);
  check("  ...asks for the code, the password twice, with the right autocomplete",
        rt.includes('autocomplete="one-time-code"') && (rt.match(/autocomplete="new-password"/g) || []).length === 2);
  check("  ...posts to /api/reset_password, and reads the code from the fragment",
        rt.includes('fetch("/api/reset_password"') && rt.includes('"#code="'));
  check("  ...a fresh nonce per page", ((/nonce-([A-Za-z0-9_-]+)/.exec(reset.headers.get("content-security-policy") || "") || [])[1]) !== nonce);

  // /api/reset_check (25 Oct 2026): the GitHub workflow asks it whether the dashboard signed a record,
  // with no session and no Origin. It must reach its function, or no reset could ever be stored.
  for (const p of ["/api/reset_code", "/api/reset_password", "/api/reset_check"]) {
    check("ON: POST " + p + " is passed to its function", (await call(req(p, { method: "POST" }))) === undefined);
    const get = await call(req(p));
    check("ON: GET " + p + " still needs a login", get && get.status === 401, get ? get.status : "SERVED");
  }
  for (const m of ["POST", "PUT", "DELETE", "PATCH"]) {
    for (const p of ["/forgot", "/reset"]) {
      const res = await call(req(p, { method: m }));
      check("ON: " + m + " " + p + " still needs a login", res && res.status === 401, res ? res.status : "SERVED");
    }
  }
  // A prefix test would open every one of these.
  for (const p of ["/forgot/", "/reset/", "/forgot.html", "/reset.html", "/forgotten", "/resets", "/Forgot",
                   "/api/reset_code/", "/api/reset_password/", "/api/reset_codex", "/api/reset_code.py",
                   "/API/reset_code", "/api/reset", "/api/reset_password.py", "/api/reset_check/",
                   "/api/reset_checks", "/api/reset_check.py", "/api/reset_chec"]) {
    const res = await call(req(p, { method: p.startsWith("/api") || p.startsWith("/API") ? "POST" : "GET" }));
    check("ON: " + p + " still needs a login", res && res.status === 401, res ? res.status : "SERVED WITHOUT AUTH");
  }
});

await section("A reset is live: only the new password, only new sessions", async () => {
  deploy({ ...ON, SPB_PW_RECORD: F.rec1 });
  check("after a reset: the epoch header says 1", (await epochOf()) === "1", await epochOf());
  check("after a reset: the id header names exactly this record",
        (await html("/login")).headers.get("x-spb-auth-id") === F.id1);
  const res = await login(F.p1);
  const fresh = cookieOf(res);
  check("after a reset: the new password signs in", res.status === 303 && fresh !== "", res.status);
  check("after a reset: SITE_PASSWORD no longer signs in", (await login(F.site)).status === 401);
  check("after a reset: a session from before it is dead", !(await served(legacyCookie(F.site))));
  check("after a reset: a new session works", await served(fresh));
  check("  ...the new password with a trailing space does not", (await login(F.p1 + " ")).status === 401);
  check("  ...nor an empty one", (await login("")).status === 401);
  check("  ...nor the right one with the wrong email", (await login(F.p1, "someone@example.com")).status === 401);
  const exp = Math.floor(Date.now() / 1000) + 3600;
  check("  ...nor a forged cookie signed with the stored hash",
        !(await served("spb_session=" + exp + "." +
          crypto.createHmac("sha256", F.hash1).update(F.user + "|" + exp).digest("base64url"))));
  check("  ...and the record shows no hash: it travels encrypted", !F.rec1.includes(F.hash1));

  deploy({ ...ON, SPB_PW_RECORD: F.rec2 });
  check("a second reset: the epoch header says 2", (await epochOf()) === "2", await epochOf());
  check("a second reset: the id header moves on", (await html("/login")).headers.get("x-spb-auth-id") === F.id2);
  check("a second reset: the newest password signs in", await signsIn(F.p2));
  check("a second reset: the first new password no longer does", (await login(F.p1)).status === 401);
  check("a second reset: the first reset's sessions are dead", !(await served(fresh)));
});

await section("The owner's SITE_PASSWORD change wins", async () => {
  deploy({ ...ON, SITE_PASSWORD: F.site2, SPB_PW_RECORD: F.rec1 });
  check("owner changed SITE_PASSWORD: the new SITE_PASSWORD signs in", await signsIn(F.site2));
  check("owner changed SITE_PASSWORD: the reset password no longer does", (await login(F.p1)).status === 401);
  check("owner changed SITE_PASSWORD: the epoch header says 0", (await epochOf()) === "0");
  deploy({ ...ON, SPB_PW_RECORD: F.recOtherSite });
  check("a record made under another SITE_PASSWORD is ignored", await signsIn(F.site) && (await login(F.p1)).status === 401);
});

await section("A record that cannot be trusted: 503, never the old password", async () => {
  // "secret changed": the owner rotated SPB_AUTH_SECRET. Before the 23 Oct review the fingerprint was
  // checked first, so this record looked written-against-another-SITE_PASSWORD and was IGNORED, and the
  // old SITE_PASSWORD signed in again. "signed elsewhere": a record made under another secret.
  for (const [label, env] of [["tampered", { ...ON, SPB_PW_RECORD: F.recTampered }],
                              ["malformed", { ...ON, SPB_PW_RECORD: F.recMalformed }],
                              ["no secret", { SITE_PASSWORD: F.site, SPB_PW_RECORD: F.rec1 }],
                              ["secret changed", { ...ON, SPB_AUTH_SECRET: F.otherSecret, SPB_PW_RECORD: F.rec1 }],
                              ["signed elsewhere", { ...ON, SPB_PW_RECORD: F.recOtherSecret }]]) {
    deploy(env);
    const old = await login(F.site);
    check("untrusted record (" + label + "): SITE_PASSWORD gets 503, not a session",
          old.status === 503 && cookieOf(old) === "", old.status);
    check("untrusted record (" + label + "): the reset password gets 503 too", (await login(F.p1)).status === 503);
    const s = await call(req("/stock.json", { cookie: legacyCookie(F.site) }));
    check("untrusted record (" + label + "): an old session gets 503", s && s.status === 503, s ? s.status : "SERVED");
    const body = s ? await s.text() : "";
    check("  ...plain text that names the setting and leaks nothing", body.includes("SPB_PW_RECORD") &&
          !body.includes(F.secret) && !body.includes(F.rec1) && s.headers.get("content-type").startsWith("text/plain"));
    check("untrusted record (" + label + "): /r is still public", (await call(req("/r"))) === undefined);
  }
});

await section("Feature switched off after a reset: the reset still rules", async () => {
  deploy({ SITE_PASSWORD: F.site, SPB_AUTH_SECRET: F.secret, SPB_PW_RECORD: F.rec1 });
  check("switched off: the reset password still signs in", await signsIn(F.p1));
  check("switched off: SITE_PASSWORD does not come back", (await login(F.site)).status === 401);
  check("switched off: the pages are gated again", (await html("/forgot")).status === 302);
  check("switched off: the epoch header is still reported", (await epochOf()) === "1");
});

await section("configured() in Python and resetConfigured() here agree", async () => {
  for (const c of F.combos) {
    deploy(c.env);
    const status = (await html("/forgot")).status;
    check("same answer for " + JSON.stringify(Object.keys(c.env).filter((k) => !["SITE_PASSWORD"].includes(k))) +
          " -> " + c.configured, (status === 200) === c.configured, "status " + status);
  }
});

await section("PBKDF2 cost on a login, and no network at all", async () => {
  deploy({ ...ON, SPB_PW_RECORD: F.rec1 });
  const times = [];
  for (let i = 0; i < 5; i++) {
    const t = performance.now();
    await login(i % 2 ? F.p1 : "wrong-password-attempt");
    times.push(performance.now() - t);
  }
  times.sort((a, b) => a - b);
  console.log("  (login with a stored password, median " + times[2].toFixed(0) + " ms)");
  check("a login with a stored password stays under a second", times[2] < 1000, times[2].toFixed(0) + " ms");
  check("sign-in never made a network call", fetches === 0, fetches + " fetch() calls");
});

console.log("");
// Zero checks is a failure: a filter that matches no section must not read as a pass.
if (pass + fail === 0) { console.log("NO CHECKS RAN (SPB_ONLY=" + process.env.SPB_ONLY + ")"); process.exit(2); }
if (fail === 0) console.log("ALL " + pass + " CHECKS PASSED");
else console.log(pass + " passed, " + fail + " FAILED");
process.exit(fail === 0 ? 0 : 1);
