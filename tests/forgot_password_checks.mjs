// The forgot-password feature, end to end, the way Vercel will run it.
//
//   the REAL generated middleware.js  (build_web.py MIDDLEWARE_JS)
//   the REAL Python functions         (api/auth/forgot.py, api/auth/reset.py, api/_spb_auth.py)
//   a FAKE Blob store, a FAKE SMTP server and a FAKE clock, all on 127.0.0.1 (tests/authkit/)
//
// Nothing real is called: the harness replaces fetch() so the middleware can reach only the local
// fake, the Python side's transport refuses any other host, and the mail goes to a socket this
// process owns. Run by tests/test_forgot_password.py, which builds the middleware first and sets
// SPB_MW_PATH; run it by hand the same way. Check names are matched by
// tests/test_forgot_password_mutations.py, which removes one safeguard at a time and requires the
// named check to go red, so rename one here and that test says so.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { startWorld, FAKE, ORIGIN, HOST } from "./authkit/emulator.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..");

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  PASS  " + name); }
  else { fail++; console.log("  FAIL  " + name + (detail !== undefined ? "  -> " + detail : "")); }
}
// SPB_ONLY=word,word runs only the sections whose title contains one of the words (the mutation
// test uses it so each mutant is judged by the section that should catch it, in seconds).
const ONLY = (process.env.SPB_ONLY || "").split(",").map((x) => x.trim().toLowerCase()).filter(Boolean);
async function section(title, fn) {
  if (ONLY.length && !ONLY.some((k) => title.toLowerCase().includes(k))) return;
  console.log("\n--- " + title + " ---");
  await fn();
}

// Everything the middleware logs is kept, so a secret in it can be found.
const nodeLogs = [];
for (const m of ["log", "info", "warn", "error", "debug"]) {
  const real = console[m].bind(console);
  console[m] = (...a) => {
    const line = a.map(String).join(" ");
    // Check lines are ours; anything else is the code under test talking.
    if (!/^\s*(PASS|FAIL)\b|^\n?---|^\n?(ALL \d+|\d+ passed)/.test(line)) nodeLogs.push(line);
    real(...a);
  };
}

const w = await startWorld();
const B = w.backend;
const USER = FAKE.user;
const SEEN_CODES = new Set();
const SEEN_PASSWORDS = new Set([FAKE.site]);
let counter = 0;
const newPassword = (tag) => { const p = "Fresh-" + tag + "-" + (++counter) + "-passphrase"; SEEN_PASSWORDS.add(p); return p; };

const legacySign = (secret, data) => crypto.createHmac("sha256", secret).update(data).digest("base64url");
const body = async (res) => await res.text();
const jbody = async (res) => { try { return JSON.parse(await res.text()); } catch (e) { return {}; } };
const mailCount = async () => (await B.mailbox()).length;
const rec = async (name) => { const t = await B.text("site-auth/" + name + ".json"); return t === null ? null : JSON.parse(t); };

async function issue() {                  // ask for a code; returns it, or null if no mail arrived
  const before = await mailCount();
  await w.forgot();
  if ((await mailCount()) !== before + 1) return null;
  const code = await w.lastCode();
  SEEN_CODES.add(code);
  return code;
}
const notCode = (code) => (code === "000000" ? "111111" : "000000");
// A person is sent to /login after a reset and types a password: a few seconds. The middleware
// re-reads the store at a login only when its copy is more than 2 s old (a guard against a flood of
// logins becoming a flood of reads: see "LOGIN FLOODS" below), so the tests wait the way a person does.
const REDIRECT_AND_TYPING = 3;
async function makeOverride(password, { gap = 61 } = {}) {   // forgot + reset; returns the code used
  await w.advance(gap);
  const code = await issue();
  const res = await w.reset(code, password);
  if (res.status !== 200) throw new Error("setup reset failed: " + res.status + " " + (await body(res)));
  await w.advance(REDIRECT_AND_TYPING);
  return code;
}

await section("NOT CONFIGURED: sign-in is exactly what it was, and nothing reaches the store", async () => {
{
  // Every way the feature can be unconfigured. In each, the old gate must be untouched.
  const variants = {
    "no BLOB_READ_WRITE_TOKEN": { BLOB_READ_WRITE_TOKEN: null },
    "BLOB_READ_WRITE_TOKEN is not a blob token": { BLOB_READ_WRITE_TOKEN: "not-a-blob-token" },
    "no SPB_SMTP_PASSWORD": { SPB_SMTP_PASSWORD: null },
    "blank SPB_SMTP_PASSWORD": { SPB_SMTP_PASSWORD: "   " },
    "no SPB_SERVICE_KEY": { SPB_SERVICE_KEY: null },
    "SPB_RESET_TO is not an address": { SPB_RESET_TO: "<script>alert(1)</script>@x.com" },
    "SPB_RESET_TO carries a header injection": { SPB_RESET_TO: "a@b.com\r\nBcc: evil@example.org" },
    "SPB_SMTP_USER carries a header injection": { SPB_SMTP_USER: "a@b.com\nBcc: evil@example.org" },
  };
  for (const [label, override] of Object.entries(variants)) {
    await w.fresh(override);
    const tag = "[" + label + "] ";
    const login = await w.login(FAKE.site);
    check(tag + "login still works (303)", login.status === 303, "status " + login.status);
    const sc = login.headers.get("set-cookie") || "";
    const [name, token] = sc.split(";")[0].split("=");
    const [exp, sig] = [token.slice(0, token.lastIndexOf(".")), token.slice(token.lastIndexOf(".") + 1)];
    check(tag + "  ...the cookie is the legacy one, byte for byte: HMAC(SITE_PASSWORD, user|exp)",
          name === "spb_session" && sig === legacySign(FAKE.site, USER + "|" + exp), sc);
    check(tag + "  ...same attributes as ever",
          sc.includes("HttpOnly") && sc.includes("Secure") && sc.includes("SameSite=Lax") &&
          sc.includes("Max-Age=43200") && login.headers.get("location") === "/");
    const legacyCookie = "spb_session=" + (Math.floor(Date.now() / 1000) + 600) + "." +
      legacySign(FAKE.site, USER + "|" + (Math.floor(Date.now() / 1000) + 600));
    check(tag + "a cookie minted the old way is accepted", await w.sessionWorks(legacyCookie));
    check(tag + "wrong password -> the same 401", (await w.login("nope")).status === 401);
    check(tag + "no cookie -> the same 401", (await w.call(w.req("/stock.json"))).status === 401);
    const reads = await B.blobLog();
    check(tag + "the store was never contacted: no extra fetch, no latency", reads.length === 0,
          JSON.stringify(reads.map((r) => r.method + " " + r.path)));

    const page = await w.call(w.req("/forgot", { accept: "text/html" }));
    const html = await body(page);
    check(tag + "/forgot says it is not set up yet",
          page.status === 200 && html.includes("Password reset is not set up yet") &&
          html.includes("ask the person who runs the dashboard") && !html.includes('id="go"'));
    const page2 = await w.call(w.req("/reset", { accept: "text/html" }));
    const html2 = await body(page2);
    check(tag + "/reset says it is not set up yet",
          page2.status === 200 && html2.includes("Password reset is not set up yet") && !html2.includes('id="pw"'));
    for (const ep of ["forgot", "reset"]) {
      const r = await w.call(w.json("/api/auth/" + ep, ep === "forgot" ? {} : { code: "123456", password: "x".repeat(14), confirm: "x".repeat(14) }));
      const j = await jbody(r);
      check(tag + "POST /api/auth/" + ep + " -> 503 with the not-set-up message",
            r.status === 503 && j.ok === false && /not set up yet/.test(j.message || ""), r.status + " " + JSON.stringify(j));
    }
    check(tag + "no email was sent and the store still untouched",
          (await mailCount()) === 0 && (await B.blobLog()).length === 0);
    check(tag + "Python agrees it is not configured", (await B.pythonConfigured()) === false);
  }
}
});

await section("CONFIGURED, NOTHING RESET YET: SITE_PASSWORD rules, the store says 404", async () => {
{
  await w.fresh();
  check("Python and JavaScript agree it is configured", (await B.pythonConfigured()) === true);
  const login = await w.login(FAKE.site);
  const token = (login.headers.get("set-cookie") || "").split(";")[0].split("=")[1];
  const exp = token.slice(0, token.lastIndexOf("."));
  check("login with SITE_PASSWORD works and the cookie is still the legacy HMAC",
        login.status === 303 && token.endsWith(legacySign(FAKE.site, USER + "|" + exp)));
  const c = "spb_session=" + token;
  check("the session works", await w.sessionWorks(c));
  await B.clearBlobLog();
  for (let i = 0; i < 6; i++) await w.sessionWorks(c);
  check("30 s cache: six session checks cost at most one read of the store",
        (await B.blobLog("read")).length <= 1, String((await B.blobLog("read")).length));
  await w.advance(31);
  await B.clearBlobLog();
  await w.sessionWorks(c);
  await w.sessionWorks(c);
  const reads = await B.blobLog("read");
  check("after 30 s it reads again, once", reads.length === 1, String(reads.length));
  const r0 = reads[0] || { headers: {}, query: {}, host: "", path: "" };
  check("  ...GET <store>.private.blob.vercel-storage.com/site-auth/password.json",
        r0.method === "GET" && r0.host === "fakestore0123.private.blob.vercel-storage.com" &&
        r0.path === "/site-auth/password.json");
  check("  ...with the bearer token (the harness saw it was right) and ?cache=0",
        r0.authorized === true && r0.query.cache === "0", JSON.stringify(r0.query));
  check("  ...and never writes", (await B.blobLog("write")).length === 0);
  const home = await w.call(w.req("/login", { accept: "text/html" }));
  const html = await body(home);
  check("the login page carries the Forgot password link to /forgot", html.includes('<a href="/forgot">Forgot password?</a>'));
  check("  ...and still has the email and password boxes", html.includes('name="email"') && html.includes('name="password"'));
  const note = await body(await w.call(w.req("/login?reset=1", { accept: "text/html" })));
  check("/login?reset=1 shows 'Password changed - sign in with the new password'",
        /Password changed[^<]*sign in with the new password/.test(note) && !/Password changed/.test(html));
}
});

await section("THE HAPPY PATH", async () => {
let FIRST_PASSWORD, FIRST_CODE, FIRST_COOKIE;
{
  await w.fresh();
  const oldCookie = await w.cookieFor(FAKE.site);
  check("before: the old password signs in and its session works", !!oldCookie && await w.sessionWorks(oldCookie));
  const page = await w.call(w.req("/forgot", { accept: "text/html" }));
  const html = await body(page);
  check("/forgot (no session) shows the masked address and one button",
        page.status === 200 && html.includes("A 6-digit code will be sent to d***@smartpropertybuying.com.au") &&
        html.includes("Email me a code") && !/type="email"/.test(html) && !html.includes(USER));

  const r = await w.forgot();
  const j = await jbody(r);
  check("POST /api/auth/forgot -> 200 and the generic sentence",
        r.status === 200 && j.ok === true && j.message === "If this site is set up for it, a code has been emailed.", JSON.stringify(j));
  const box = await B.mailbox();
  check("exactly one email, to the fixed address", box.length === 1 && box[0].envelope_to.length === 1 &&
        box[0].envelope_to[0] === USER && box[0].to === USER, JSON.stringify(box.map((m) => m.envelope_to)));
  const code = await w.lastCode();
  SEEN_CODES.add(code);
  check("the code is 6 digits", /^\d{6}$/.test(code));
  const stored = await rec("reset");
  const storedText = await B.text("site-auth/reset.json");
  check("the store holds a keyed hash, a nonce and an expiry, and NOT the code",
        stored && stored.state === "open" && stored.code_mac && stored.nonce && stored.attempts_left === 5 &&
        stored.expires_at - stored.issued_at === 900 && !storedText.includes(code), storedText);

  FIRST_PASSWORD = newPassword("happy");
  FIRST_CODE = code;
  const rr = await w.reset(code, FIRST_PASSWORD);
  await w.advance(REDIRECT_AND_TYPING);
  const rj = await jbody(rr);
  check("POST /api/auth/reset with the right code -> 200", rr.status === 200 && rj.ok === true, JSON.stringify(rj));
  const pw = await rec("password");
  check("password.json: pbkdf2-sha256, iterations >= 100000, salt, hash, env_fp, epoch, updated_at, mac",
        pw && pw.v === 1 && pw.algo === "pbkdf2-sha256" && pw.iter >= 100000 && pw.iter <= 1000000 &&
        pw.salt && pw.hash && pw.env_fp && pw.epoch === 1 && /^\d{4}-\d\d-\d\dT[\d:]+Z$/.test(pw.updated_at) && pw.mac,
        JSON.stringify(pw));
  const all = (await B.text("site-auth/password.json")) + (await B.text("site-auth/reset.json"));
  check("the new password is nowhere in the store, and neither is the code", !all.includes(FIRST_PASSWORD) && !all.includes(code));
  const after = await rec("reset");
  check("the code is burned in the store", after.state === "burned" && !("code_mac" in after) && !("nonce" in after), JSON.stringify(after));
  const box2 = await B.mailbox();
  check("a 'password was changed at' notice went to the same address",
        box2.length === 2 && box2[1].envelope_to[0] === USER && /password was changed at/.test(box2[1].text) &&
        /password changed/.test(box2[1].subject), box2[1] && box2[1].subject);

  check("the old password is refused at once", (await w.login(FAKE.site)).status === 401);
  const fresh = await w.cookieFor(FIRST_PASSWORD);
  FIRST_COOKIE = fresh;
  check("the new password is accepted at once", !!fresh);
  check("the new session works", await w.sessionWorks(fresh));
  await w.advance(31);
  check("the old session cookie is rejected once the instance has re-read the store (within 30 s)",
        !(await w.sessionWorks(oldCookie)));
  check("the new session still works", await w.sessionWorks(fresh));
  check("the old password is still refused", (await w.login(FAKE.site)).status === 401);
  const ln = await w.login(FIRST_PASSWORD, { email: USER.toUpperCase() });
  check("email stays case-insensitive and next= is still honoured",
        ln.status === 303 && (await w.login(FIRST_PASSWORD, { next: "/search?q=1" })).headers.get("location") === "/search?q=1");
  const sc = fresh || "";
  const [expv, sigv] = [sc.split("=")[1].slice(0, sc.split("=")[1].lastIndexOf(".")), sc.slice(sc.lastIndexOf(".") + 1)];
  check("the new cookie is NOT signed with SITE_PASSWORD (old key gone)", sigv !== legacySign(FAKE.site, USER + "|" + expv));
  check("  ...nor with the stored hash itself (a reader of the store cannot mint a session)",
        sigv !== legacySign(pw.hash, USER + "|" + expv));
  const forged = "spb_session=" + (Math.floor(Date.now() / 1000) + 600) + "." +
    legacySign(pw.hash, USER + "|" + (Math.floor(Date.now() / 1000) + 600));
  check("  ...and a cookie forged from the stored hash is rejected", !(await w.sessionWorks(forged)));
  // The generic '503 plain message' must not appear anywhere on the happy path.
  const login = await w.call(w.req("/login?reset=1", { accept: "text/html" }));
  check("the login page shows the changed-password note", /Password changed/.test(await body(login)));
}
});

await section("WRONG CODES: five and it is burned", async () => {
{
  await w.fresh();
  const code = await issue();
  const pw1 = newPassword("burn");
  let statuses = [];
  for (let i = 0; i < 5; i++) {
    const r = await w.reset(notCode(code), pw1);
    statuses.push(r.status + ":" + (await jbody(r)).error);
  }
  check("five wrong codes -> five 400 code_invalid", statuses.every((s) => s === "400:code_invalid"), statuses.join(" "));
  const burned = await rec("reset");
  check("the fifth wrong try burned the code in the store", burned.state === "burned" && burned.attempts_left === 0, JSON.stringify(burned));
  const r6 = await w.reset(code, pw1);
  check("the RIGHT code is refused after five wrong tries", r6.status === 400 && (await jbody(r6)).error === "code_invalid", String(r6.status));
  check("nothing changed: the old password still signs in, no new password exists",
        !!(await w.cookieFor(FAKE.site)) && (await rec("password")) === null);
  check("only the code email was sent", (await mailCount()) === 1);
}
{
  await w.fresh();
  const code = await issue();
  const pw1 = newPassword("four");
  for (let i = 0; i < 4; i++) await w.reset(notCode(code), pw1);
  const mid = await rec("reset");
  check("four wrong tries leave one attempt (the decrement is stored before the answer)", mid.state === "open" && mid.attempts_left === 1, JSON.stringify(mid));
  const ok = await w.reset(code, pw1);
  check("the right code still works on the fifth try", ok.status === 200, String(ok.status));
}
{
  await w.fresh();
  const code = await issue();
  const r = await w.reset("12345", newPassword("fmt"));
  const r2 = await w.reset("abcdef", newPassword("fmt"));
  check("a malformed code is refused (400) without costing an attempt",
        r.status === 400 && r2.status === 400 && (await rec("reset")).attempts_left === 5);
  await w.fresh();
  const r3 = await w.reset("123456", newPassword("none"));
  check("a code nobody asked for gets the same answer as a wrong one",
        r3.status === 400 && (await jbody(r3)).error === "code_invalid");
}
});

await section("EXPIRY (fake clock): 15 minutes", async () => {
{
  await w.fresh();
  const code = await issue();
  await w.advance(14 * 60 + 55);
  const ok = await w.reset(code, newPassword("late"));
  check("five seconds short of 15 minutes: the code still works", ok.status === 200, String(ok.status));
}
{
  await w.fresh();
  const code = await issue();
  await w.advance(15 * 60);
  const r = await w.reset(code, newPassword("expired"));
  check("at 15 minutes the code has expired", r.status === 400 && (await jbody(r)).error === "code_invalid", String(r.status));
  check("an expired code changes nothing", (await rec("password")) === null && !!(await w.cookieFor(FAKE.site)));
}
});

await section("REPLAY: one success and the code is gone", async () => {
{
  await w.fresh();
  const code = await issue();
  const a = newPassword("first"), b = newPassword("second");
  const r1 = await w.reset(code, a);
  const before = await B.text("site-auth/password.json");
  const r2 = await w.reset(code, b);
  const r3 = await w.reset(code, a);
  check("the first use succeeds", r1.status === 200);
  check("the same code used again is refused, with another password or the same one",
        r2.status === 400 && r3.status === 400 && (await jbody(r2)).error === "code_invalid", r2.status + " " + r3.status);
  check("the stored password record is byte for byte unchanged by the replay", (await B.text("site-auth/password.json")) === before);
  check("the first password still works, the replayed one does not", !!(await w.cookieFor(a)) && !(await w.cookieFor(b)));
}
});

await section("RATE LIMITS: 1 per 60 s, 3 per hour, and the answer never changes", async () => {
{
  await w.fresh();
  const texts = [];
  const ask = async () => { const r = await w.forgot(); texts.push(r.status + " " + (await r.text())); return r; };
  await ask();                                           // t = 0      sent
  check("first request: one email", (await mailCount()) === 1);
  await ask();                                           // t = 0      too soon
  await w.advance(30); await ask();                      // t = 30     too soon
  check("two requests inside 60 s: still one email", (await mailCount()) === 1, String(await mailCount()));
  await w.advance(31); await ask();                      // t = 61     sent
  check("at 61 s a second email goes out", (await mailCount()) === 2, String(await mailCount()));
  await w.advance(61); await ask();                      // t = 122    sent
  check("at 122 s a third email goes out", (await mailCount()) === 3, String(await mailCount()));
  await w.advance(61); await ask();                      // t = 183    over 3 in the hour
  check("a fourth inside the hour is held back", (await mailCount()) === 3, String(await mailCount()));
  await w.advance(3601 - 183); await ask();              // t = 3601   the first has left the hour
  check("once the first request is over an hour old another is allowed", (await mailCount()) === 4, String(await mailCount()));
  check("every answer was the identical sentence, sent or not (nothing to learn from wording)",
        new Set(texts).size === 1, [...new Set(texts)].join(" | "));
  check("the store kept the history: the limit survives in the store, not in memory",
        (await rec("reset")).issued.length >= 2);
}
{
  await w.fresh();
  const code = await issue();
  await w.reset(code, newPassword("rl"));
  const before = await mailCount();                      // code + notice
  await w.forgot();
  check("a successful reset does not clear the rate limit", (await mailCount()) === before, String(await mailCount()));
}
});

await section("CONCURRENCY: one code is spent once, one request starts one code", async () => {
{
  await w.fresh();
  const code = await issue();
  const pws = Array.from({ length: 8 }, (_, i) => newPassword("race" + i));
  const results = await Promise.all(pws.map((p) => w.reset(code, p)));
  const statuses = results.map((r) => r.status);
  const wins = statuses.filter((s) => s === 200).length;
  check("eight concurrent resets with the SAME right code: exactly one wins",
        wins === 1, statuses.join(","));
  check("  ...the rest are refused or told to retry, never accepted", statuses.every((s) => [200, 400, 503].includes(s)), statuses.join(","));
  const winnerIdx = statuses.indexOf(200);
  await w.advance(31);
  let accepted = [];
  for (let i = 0; i < pws.length; i++) if (await w.cookieFor(pws[i])) accepted.push(i);
  check("  ...and exactly the winner's password signs in", accepted.length === 1 && accepted[0] === winnerIdx, JSON.stringify(accepted) + " winner " + winnerIdx);
  check("  ...one 'password changed' notice, not eight", (await B.mailbox()).filter((m) => /changed/.test(m.subject)).length === 1);
}
{
  await w.fresh();
  const code = await issue();
  const pw1 = newPassword("hammer");
  const results = await Promise.all(Array.from({ length: 12 }, () => w.reset(notCode(code), pw1)));
  const statuses = results.map((r) => r.status);
  const stored = await rec("reset");
  const wrong = statuses.filter((s) => s === 400).length;
  const busy = statuses.filter((s) => s === 503).length;
  const counted = ((await B.logs()).match(/reset: wrong code/g) || []).length;
  check("twelve concurrent wrong guesses: every one is answered 400 or told it is busy (503), none accepted",
        wrong + busy === 12, statuses.join(","));
  check("  ...never more than five guesses are counted, and the store agrees with the log on how many",
        counted <= 5 && (stored.state === "burned" ? counted === 5 : stored.attempts_left === 5 - counted),
        JSON.stringify(stored) + " counted=" + counted);
  check("  ...a guess answered 400 was counted, or the code was already burned (the 400s are not free guesses)",
        wrong >= counted && (counted === 5 || wrong === counted), "wrong=" + wrong + " counted=" + counted);
  const late = await w.reset(code, pw1);
  check("  ...and the right code is dead afterwards if five were counted", counted < 5 || late.status === 400);
}
{
  await w.fresh();
  const results = await Promise.all(Array.from({ length: 6 }, () => w.forgot()));
  check("six concurrent requests for a code: every answer identical", new Set(await Promise.all(results.map(async (r) => r.status + await r.text()))).size === 1);
  check("  ...and exactly one email was sent", (await mailCount()) === 1, String(await mailCount()));
}
});

await section("STORAGE OUTAGE: stale-if-error with a cache, fail closed without one", async () => {
{
  await w.fresh();
  const P = newPassword("outage");
  await makeOverride(P);
  await w.advance(31);
  const good = await w.cookieFor(P);
  check("setup: signed in on the stored password, instance cache warm", !!good && await w.sessionWorks(good));
  await B.blobMode("outage");
  await w.advance(31);                                     // cache is now stale, but within 15 minutes
  check("store down, cache 31 s old: the session still works (stale-if-error)", await w.sessionWorks(good));
  check("store down: the stored password still signs in", !!(await w.cookieFor(P)));
  check("store down: the OLD password is NOT let back in", !(await w.cookieFor(FAKE.site)));
  await B.clearBlobLog();
  for (let i = 0; i < 5; i++) await w.sessionWorks(good);
  check("a store that has just failed is not hammered: at most one read in the next burst",
        (await B.blobLog("read")).length <= 1, String((await B.blobLog("read")).length));
  await w.advance(15 * 60);                                // more than 15 minutes since the last good read
  const gone = await w.call(w.req("/stock.json", { cookie: good }));
  check("a stand-in older than 15 minutes is not used: 503", gone.status === 503, String(gone.status));
  const gtext = await body(gone);
  check("  ...a plain-text message, no secrets, no stack", /temporarily unavailable/.test(gtext) &&
        !gtext.includes(FAKE.token) && gone.headers.get("content-type").startsWith("text/plain"));
  await B.blobMode("ok");
  await w.advance(6);
  check("the store is back: service resumes with no one signed out", await w.sessionWorks(good));
}
{
  await w.fresh();
  await B.blobMode("outage");
  const login = await w.login(FAKE.site);
  check("cold instance + store down: the old password is NOT accepted, and the answer is 503",
        login.status === 503 && !login.headers.get("set-cookie"), String(login.status));
  const wrong = await w.login("wrong-password");
  check("  ...a wrong password also gets 503, not 401 (nothing is decided)", wrong.status === 503);
  const stock = await w.call(w.req("/stock.json", { cookie: "spb_session=" + (Math.floor(Date.now() / 1000) + 600) + ".abc" }));
  check("  ...a cookie cannot be judged either: 503", stock.status === 503);
  check("  ...no cookie at all is still the plain 401 (no store needed)", (await w.call(w.req("/stock.json"))).status === 401);
  check("  ...the login page itself still loads", (await w.call(w.req("/login", { accept: "text/html" }))).status === 200);
  const r = await w.call(w.req("/r", { accept: "text/html" }));
  check("  ...the client share page /r is served", w.served(r));
  check("  ...and /forgot loads (it needs no store)", (await w.call(w.req("/forgot", { accept: "text/html" }))).status === 200);
  await B.blobMode("ok");
  await w.advance(6);
  check("store back: the next sign-in works", !!(await w.cookieFor(FAKE.site)));
}
for (const [label, mode] of [["a rejected token (403)", "forbidden"], ["a 200 that is not the record", "garbage"]]) {
  await w.fresh();
  await B.blobMode(mode);
  const login = await w.login(FAKE.site);
  check("cold + " + label + ": fail closed, 503, no cookie", login.status === 503 && !login.headers.get("set-cookie"), String(login.status));
  await B.blobMode("ok");
}
{
  await w.fresh();
  await B.blobFault({ on: "read", delay: 3.6, count: 1 });
  const t0 = Date.now();
  const t0real = performance.now();
  const login = await w.login(FAKE.site);
  const took = performance.now() - t0real;
  check("a store that stalls is cut off after about 3 s and fails closed (503)",
        login.status === 503 && took >= 2800 && took < 3600, login.status + " after " + Math.round(took) + " ms");
}
{
  await w.fresh();
  await B.blobMode("ok");
  const login = await w.login(FAKE.site);
  check("a 404 from the store is NOT an error: no record means SITE_PASSWORD", login.status === 303);
}
{
  await w.fresh();
  await B.blobFault({ on: "read", status: 500, count: 1 });
  const first = await w.login(FAKE.site);
  check("a single 500 on a cold instance fails closed", first.status === 503, String(first.status));
  await w.advance(6);
  check("and the next attempt, after the 5 s hold, succeeds", (await w.login(FAKE.site)).status === 303);
}
});

await section("SITE_PASSWORD CHANGED IN VERCEL AFTER A RESET: the stored password is ignored", async () => {
{
  await w.fresh();
  const P = newPassword("resetpw");
  await makeOverride(P);
  const sessionOnOverride = await w.cookieFor(P);
  check("setup: override active (reset password signs in, SITE_PASSWORD does not)",
        !!sessionOnOverride && !(await w.cookieFor(FAKE.site)));
  const OWNER = "owner-break-glass-password-9";
  await w.env({ SITE_PASSWORD: OWNER });
  check("the owner's new SITE_PASSWORD signs in at once", !!(await w.cookieFor(OWNER)));
  check("  ...the reset password no longer does", !(await w.cookieFor(P)));
  check("  ...nor does the old SITE_PASSWORD", !(await w.cookieFor(FAKE.site)));
  check("  ...and the session made on the reset password is dead", !(await w.sessionWorks(sessionOnOverride)));
  const ownerCookie = await w.cookieFor(OWNER);
  const e = ownerCookie.split("=")[1];
  check("  ...the new session is signed with SITE_PASSWORD, exactly as before this feature",
        e.endsWith(legacySign(OWNER, USER + "|" + e.slice(0, e.lastIndexOf(".")))));
  check("the record was left in the store (ignored, not deleted)", (await rec("password")) !== null);
  // Rotating the service key does the same.
  await w.env({ SITE_PASSWORD: FAKE.site, SPB_SERVICE_KEY: "rotated-service-key-0002" });
  check("rotating SPB_SERVICE_KEY also retires the stored password (SITE_PASSWORD rules again)",
        !!(await w.cookieFor(FAKE.site)) && !(await w.cookieFor(P)));
  // Coleen can do it all again after the owner has changed the value.
  await w.env({ SITE_PASSWORD: OWNER, SPB_SERVICE_KEY: FAKE.service });
  const P2 = newPassword("again");
  await makeOverride(P2, { gap: 3700 });
  check("a fresh reset after the owner's change writes a record for the NEW value and signs in",
        !!(await w.cookieFor(P2)) && !(await w.cookieFor(OWNER)));
  await w.env({ SITE_PASSWORD: FAKE.site });
}
});

await section("TAMPERED, GARBLED AND FOREIGN RECORDS: fail closed, never silently the old password", async () => {
{
  await w.fresh();
  const P = newPassword("tamper");
  await makeOverride(P);
  const good = await B.text("site-auth/password.json");
  const goodRec = JSON.parse(good);
  const plant = async (text) => { await B.plant("site-auth/password.json", text); await w.coldStart(); };
  const gate = async () => {
    const siteLogin = await w.login(FAKE.site);
    const newLogin = await w.login(P);
    return { site: siteLogin.status, fresh: newLogin.status, sc: siteLogin.headers.get("set-cookie") };
  };

  await plant(good);
  let g = await gate();
  check("control: the genuine record -> reset password in, SITE_PASSWORD out", g.fresh === 303 && g.site === 401, JSON.stringify(g));

  const cases = {
    "garbled (not JSON)": "{not json at all",
    "empty file": "",
    "JSON but not an object": "[1,2,3]",
    "unknown version": JSON.stringify({ ...goodRec, v: 2 }),
    "a field changed (hash) with the real env_fp and the old mac": JSON.stringify({ ...goodRec, hash: goodRec.hash.slice(0, -2) + (goodRec.hash.endsWith("A") ? "BB" : "AA") }),
    "mac removed": JSON.stringify({ ...goodRec, mac: undefined }),
    "iteration count lowered to 1": JSON.stringify({ ...goodRec, iter: 1 }),
    "iteration count raised past the limit": JSON.stringify({ ...goodRec, iter: 99999999 }),
    "salt cut short": JSON.stringify({ ...goodRec, salt: "AAAA" }),
    "epoch changed": JSON.stringify({ ...goodRec, epoch: 2 }),
    "algo changed": JSON.stringify({ ...goodRec, algo: "sha1" }),
  };
  for (const [label, text] of Object.entries(cases)) {
    await plant(text);
    g = await gate();
    check("record " + label + " -> 503 for everyone (fail closed), no cookie issued",
          g.site === 503 && g.fresh === 503 && !g.sc, JSON.stringify(g));
  }

  // The attack the mac exists for: the real env_fp copied out of the store beside the attacker's own hash.
  const ATTACKER = "attacker-chosen-password-1";
  const salt = crypto.randomBytes(16);
  const hash = crypto.pbkdf2Sync(ATTACKER, salt, goodRec.iter, 32, "sha256").toString("base64url");
  await plant(JSON.stringify({ ...goodRec, salt: salt.toString("base64url"), hash }));
  const atk = await w.login(ATTACKER);
  check("a store writer who copies the real env_fp and adds their own hash does NOT get in (the mac)",
        atk.status !== 303 && !atk.headers.get("set-cookie"), String(atk.status));

  // A record from another SITE_PASSWORD is not 'garbled': it is somebody else's, and ignored.
  const foreign = { ...goodRec, env_fp: "0000000000000000" };
  await plant(JSON.stringify(foreign));
  g = await gate();
  check("a record written against a different SITE_PASSWORD (stale env_fp) is IGNORED: SITE_PASSWORD rules",
        g.site === 303 && g.fresh === 401, JSON.stringify(g));

  // Recovery paths.
  await plant("{not json at all");
  g = await gate();
  check("setup: garbled again -> 503", g.site === 503);
  await B.remove("site-auth/password.json");
  await w.coldStart();
  check("recovery 1: the owner deletes the record -> SITE_PASSWORD signs in again", (await w.login(FAKE.site)).status === 303);

  await plant("{not json at all");
  const P3 = newPassword("repair");
  await w.advance(3700);
  const code = await issue();
  const rr = await w.reset(code, P3);
  check("recovery 2: a reset overwrites a garbled record (conditional write on its ETag)", rr.status === 200, String(rr.status));
  await w.coldStart();
  check("  ...and the reset password signs in", (await w.login(P3)).status === 303);
}
{
  // The code record.
  await w.fresh();
  await B.plant("site-auth/reset.json", "{garbage");
  const r = await w.reset("123456", newPassword("rg"));
  check("a garbled reset.json holds no usable code: 400, same as any wrong code", r.status === 400 && (await jbody(r)).error === "code_invalid");
  await w.advance(61);
  const code = await issue();
  check("  ...and asking for a new code overwrites it and works", !!code && (await w.reset(code, newPassword("rg2"))).status === 200);
}
});

await section("THE ROUTE TABLE: /forgot and /reset reach no session, and nothing else opens", async () => {
{
  await w.fresh();
  check("the matcher is still every path", w.config.matcher === "/(.*)");
  // Every route the deployment has: the static files, every function under api/, and the variants
  // a sloppy prefix or case-insensitive match would let through.
  const api = [];
  const walk = (dir, rel) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (e.isDirectory()) walk(path.join(dir, e.name), rel + e.name + "/");
      else if (e.name.endsWith(".py") && !e.name.startsWith("_")) api.push("/api/" + rel + e.name.slice(0, -3));
    }
  };
  walk(path.join(repoRoot, "api"), "");
  const statics = ["/", "/index.html", "/stock.json", "/channels.json", "/builders.json", "/benchmarks.json",
    "/vendor-assets.json", "/suburbs.json", "/parity_report.json", "/vercel.json", "/middleware.js",
    "/reports/client-report.pdf", "/r", "/r.html", "/login", "/logout", "/forgot", "/reset", "/robots.txt", "/favicon.ico"];
  const variants = [];
  for (const base of ["/forgot", "/reset", "/api/auth/forgot", "/api/auth/reset", "/r"]) {
    // (A query string and dot segments are not different paths: URL parsing removes them before any
    // middleware sees the request, so /r?x=1 and /x/../r are /r.)
    variants.push(base + "/", base + "//", base + ".html", base + ".json", base + "x", base + "/x",
      base.toUpperCase(), base[0] + base.slice(1, 3).toUpperCase() + base.slice(3), base + "%2f",
      "/%66" + base.slice(2), base + "%00", base + ";x", base + "/%2e%2e/stock.json");
  }
  variants.push("/api/auth", "/api/auth/", "/api/auth/forgot.py", "/api/auth/reset.py", "/api/auth/_spb_auth", "/api/_spb_auth", "/api/auth_forgot", "/api/auth_reset", "/api/auth/other", "/api//auth/forgot", "/api/Auth/forgot", "/forgot-password", "/reset-password", "/forgotten", "/resets", "/api/auth/forgot/reset");
  const paths = [...new Set([...statics, ...api, ...variants])];
  const methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];
  const open = [];
  for (const p of paths) {
    for (const m of methods) {
      const opts = { method: m, accept: "text/html" };
      if (m === "POST" || m === "PUT" || m === "PATCH") { opts.body = "{}"; opts.ctype = "application/json"; }
      let res;
      try { res = await w.mw(w.req(p, opts)); } catch (e) { res = new Response("threw " + e.message, { status: 500 }); }
      const gated = res instanceof Response && (res.status === 401 || (res.status === 302 && (res.headers.get("location") || "").startsWith("/login?next=")));
      if (!gated) open.push(m + " " + p + (res === undefined ? " (passes through)" : " (" + res.status + ")"));
    }
  }
  const expected = [
    "GET /r (passes through)", "POST /r (passes through)", "PUT /r (passes through)", "PATCH /r (passes through)",
    "DELETE /r (passes through)", "HEAD /r (passes through)", "OPTIONS /r (passes through)",
    "GET /r.html (passes through)", "POST /r.html (passes through)", "PUT /r.html (passes through)", "PATCH /r.html (passes through)",
    "DELETE /r.html (passes through)", "HEAD /r.html (passes through)", "OPTIONS /r.html (passes through)",
    "GET /forgot (200)", "GET /reset (200)",
    "POST /api/auth/forgot (passes through)", "POST /api/auth/reset (passes through)",
    "GET /login (200)", "PUT /login (200)", "PATCH /login (200)", "DELETE /login (200)", "HEAD /login (200)", "OPTIONS /login (200)",
    "GET /logout (303)", "POST /logout (303)", "PUT /logout (303)", "PATCH /logout (303)", "DELETE /logout (303)", "HEAD /logout (303)", "OPTIONS /logout (303)",
  ].sort();
  const got = open.sort();
  const extra = got.filter((x) => !expected.includes(x));
  const missing = expected.filter((x) => !got.includes(x));
  check("probed " + paths.length + " paths x " + methods.length + " methods: nothing is open that should not be",
        extra.length === 0, "OPEN BUT NOT EXPECTED: " + extra.join(" | "));
  check("  ...and exactly the intended routes are open (/r, /r.html, GET /forgot, GET /reset, the two POSTs, /login, /logout)",
        missing.length === 0, "MISSING: " + missing.join(" | "));
  check("the function routes under api/ were all among the probes", api.includes("/api/auth/forgot") && api.includes("/api/auth/reset") && api.length >= 8, api.join(","));
  check("the machine route is unchanged: POST /api/research with the right service key still passes",
        (await w.mw(w.req("/api/research", { method: "POST", headers: { "x-spb-service-key": FAKE.service } }))) === undefined);
  check("  ...GET /api/auth/forgot with the service key is still refused",
        (await w.mw(w.req("/api/auth/forgot", { headers: { "x-spb-service-key": FAKE.service } }))).status === 401);
}
});

await section("SAME-ORIGIN, CONTENT TYPE, SIZE AND STRICT JSON on both POSTs", async () => {
{
  await w.fresh();
  const code = await issue();
  const good = { code, password: newPassword("origin"), confirm: null };
  good.confirm = good.password;
  await B.clearBlobLog();
  const mails = await mailCount();
  const origins = {
    "a foreign Origin": "https://evil.example",
    "no Origin at all": undefined,
    "Origin: null": "null",
    "http instead of https": "http://app.smartpropertybuying.com.au",
    "the right host on another port": "https://app.smartpropertybuying.com.au:8443",
    "a look-alike suffix": "https://app.smartpropertybuying.com.au.evil.example",
    "userinfo trick": "https://app.smartpropertybuying.com.au@evil.example",
    "a path on the right host": "https://app.smartpropertybuying.com.au/x",
    "empty Origin": "",
  };
  for (const ep of ["forgot", "reset"]) {
    for (const [label, origin] of Object.entries(origins)) {
      const headers = { "sec-fetch-site": "same-origin" };
      const r = await w.call(w.req("/api/auth/" + ep, { method: "POST", ctype: "application/json",
        body: JSON.stringify(ep === "forgot" ? {} : good), headers: origin === undefined ? headers : { ...headers, origin } }));
      check("POST /api/auth/" + ep + " with " + label + " -> 403", r.status === 403, String(r.status));
    }
    const cross = await w.call(w.req("/api/auth/" + ep, { method: "POST", ctype: "application/json",
      body: JSON.stringify(ep === "forgot" ? {} : good), headers: { origin: ORIGIN, "sec-fetch-site": "cross-site" } }));
    check("POST /api/auth/" + ep + " the right Origin but Sec-Fetch-Site: cross-site -> 403", cross.status === 403, String(cross.status));
  }
  check("a refused cross-site request changed nothing: no store call, no email",
        (await B.blobLog()).length === 0 && (await mailCount()) === mails);
  const viaForwarded = await w.call(w.req("/api/auth/forgot", { method: "POST", ctype: "application/json", body: "{}",
    headers: { origin: ORIGIN, host: "something.vercel.app", "x-forwarded-host": HOST } }));
  check("the public host is accepted when it arrives as X-Forwarded-Host", viaForwarded.status === 200, String(viaForwarded.status));

  const bad = {
    "Content-Type text/plain": () => w.json("/api/auth/reset", good, { ctype: "text/plain" }),
    "no Content-Type": () => w.req("/api/auth/reset", { method: "POST", body: JSON.stringify(good), headers: { origin: ORIGIN, "content-type": "" } }),
    "a form post": () => w.json("/api/auth/reset", good, { ctype: "application/x-www-form-urlencoded" }),
  };
  for (const [label, make] of Object.entries(bad)) {
    const r = await w.call(make());
    check("reset with " + label + " -> 415", r.status === 415, String(r.status));
  }
  const big = await w.call(w.json("/api/auth/reset", null, { raw: JSON.stringify({ ...good, password: "a".repeat(5000), confirm: "a".repeat(5000) }) }));
  check("a body over 4 KB -> 413", big.status === 413, String(big.status));
  const strict = {
    "invalid JSON": "{nope",
    "a JSON array": "[]",
    "a JSON string": '"x"',
    "duplicate keys": '{"code":"123456","code":"654321","password":"aaaaaaaaaaaaaa","confirm":"aaaaaaaaaaaaaa"}',
    "NaN": '{"code":NaN,"password":"a","confirm":"a"}',
    "an extra key": JSON.stringify({ ...good, extra: 1 }),
    "a missing key": JSON.stringify({ code, password: good.password }),
    "a numeric code": JSON.stringify({ ...good, code: 123456 }),
    "an object as the password": JSON.stringify({ ...good, password: { a: 1 } }),
    "CR LF in the code": JSON.stringify({ ...good, code: "123\r\n456" }),
    "a null byte": JSON.stringify({ ...good, password: "abc\u0000def-ghijkl", confirm: "abc\u0000def-ghijkl" }),
  };
  await B.clearBlobLog();
  for (const [label, raw] of Object.entries(strict)) {
    const r = await w.call(w.json("/api/auth/reset", null, { raw }));
    check("reset with " + label + " -> 400", r.status === 400, String(r.status));
  }
  check("none of those touched the store (a malformed request is no oracle for the code)", (await B.blobLog()).length === 0,
        JSON.stringify((await B.blobLog()).map((r) => r.method + r.path)));
  const f1 = await w.call(w.json("/api/auth/forgot", { email: "attacker@example.org" }));
  check("forgot with a body is refused: there is no email field to aim the code at", f1.status === 400 && (await mailCount()) === mails);
  const f2 = await w.call(w.json("/api/auth/forgot", null, { raw: "" }));
  check("forgot with an empty body is refused as invalid JSON", f2.status === 400);
  const okForgot = await w.call(w.json("/api/auth/forgot", {}));
  check("forgot with {} from our own page is accepted", okForgot.status === 200);
  const hdr = okForgot.headers;
  check("API responses: JSON, no-store, no-referrer, frame-ancestors 'none', nosniff, DENY",
        hdr.get("content-type").startsWith("application/json") && hdr.get("cache-control") === "no-store" &&
        hdr.get("referrer-policy") === "no-referrer" && /frame-ancestors 'none'/.test(hdr.get("content-security-policy") || "") &&
        hdr.get("x-content-type-options") === "nosniff" && hdr.get("x-frame-options") === "DENY",
        JSON.stringify([...hdr.entries()]));
  const fin = await w.reset(code, good.password);
  check("and the genuine request still works (the refusals did not burn the code)", fin.status === 200, String(fin.status));
}
});

await section("PASSWORD RULES: checked before the code, and the code is kept", async () => {
{
  await w.fresh();
  const code = await issue();
  await B.clearBlobLog();
  const U = USER;
  const rules = {
    "11 characters": ["abcdefghij1", "abcdefghij1", /at least 12/i],
    "129 characters": ["a".repeat(129), "a".repeat(129), /128 characters or fewer/i],
    "a leading space": [" abcdefghijkl1", " abcdefghijkl1", /start or end/i],
    "a trailing space": ["abcdefghijkl1 ", "abcdefghijkl1 ", /start or end/i],
    "a trailing line break": ["abcdefghijkl1\n", "abcdefghijkl1\n", /control characters|start or end/i],
    "a tab inside": ["abcdef\tghijkl1", "abcdef\tghijkl1", /control characters/i],
    "the sign-in email": [U, U, /email address/i],
    "the sign-in email in capitals": [U.toUpperCase(), U.toUpperCase(), /email address/i],
    "two different boxes": ["abcdefghijkl1", "abcdefghijkl2", /do not match/i],
    "an empty password": ["", "", /at least 12/i],
  };
  for (const [label, [p, c, re]] of Object.entries(rules)) {
    const r = await w.reset(code, p, c);
    const j = await jbody(r);
    check("password rule: " + label + " -> 400 weak_password", r.status === 400 && j.error === "weak_password" && re.test(j.message || ""), r.status + " " + JSON.stringify(j));
  }
  check("none of the rule failures read the store: they cannot be used to probe the code or the current password",
        (await B.blobLog()).length === 0, String((await B.blobLog()).length));
  const same = await w.reset(code, FAKE.site);
  check("with the RIGHT code, the current SITE_PASSWORD is refused as 'different from the current one' (400)",
        same.status === 400 && /different from the current/.test((await jbody(same)).message || ""), String(same.status));
  check("  ...and the code was not spent by that", (await rec("reset")).state === "open");
  const P = newPassword("rules-ok");
  check("  ...so the same code then accepts a different password", (await w.reset(code, P)).status === 200);
  await w.advance(61);
  const code2 = await issue();
  const again = await w.reset(code2, P);
  check("later, with a fresh code, the password in use now is refused too", again.status === 400 && /different from the current/.test((await jbody(again)).message || ""));
  const wrongAndSame = await w.reset(notCode(code2), P);
  check("a WRONG code with the current password gets the plain wrong-code answer (no oracle for the password)",
        (await jbody(wrongAndSame)).error === "code_invalid");
  // Unicode: the same visible password typed on two keyboards.
  await w.advance(61);
  const code3 = await issue();
  const nfd = "Café-au-lait-1234";
  const nfc = "Café-au-lait-1234";
  SEEN_PASSWORDS.add(nfd); SEEN_PASSWORDS.add(nfc);
  check("set with a decomposed accent", (await w.reset(code3, nfd)).status === 200);
  await w.advance(REDIRECT_AND_TYPING);
  check("  ...signs in when typed with the composed accent (NFC on both sides)", (await w.login(nfc)).status === 303);
  check("an empty password at the login form is a plain 401 under the stored password", (await w.login("")).status === 401);
  check("a 5,000-character password at the login form is a plain 401, not an error", (await w.login("a".repeat(5000))).status === 401);
  check("wrong email with the right password is still 401", (await w.login(nfc, { email: "someone@else.com" })).status === 401);
}
});

await section("TWO RESETS IN A ROW: new epoch, new key, and reads that are never stale", async () => {
{
  await w.fresh();
  const A = newPassword("A"), Bp = newPassword("B");
  const sessionBeforeAny = await w.cookieFor(FAKE.site);
  const code1 = await issue();                              // t = 0
  await w.advance(1);
  await w.reset(code1, A);                                  // t = 1
  await w.advance(57);                                      // t = 58
  const cookieA = await w.cookieFor(A);                     // the store is read now, and a CDN would keep it
  check("first reset: A signs in", !!cookieA);
  const epoch1 = (await rec("password")).epoch;
  await w.advance(3);                                       // t = 61
  const code2 = await issue();
  await w.advance(1);
  const r2 = await w.reset(code2, Bp);                      // t = 62
  check("second reset succeeds", r2.status === 200);
  const epoch2 = (await rec("password")).epoch;
  check("the epoch counts up", epoch1 === 1 && epoch2 === 2, epoch1 + "," + epoch2);
  await w.advance(1);                                       // t = 63: five seconds after the CDN-able read
  const loginB = await w.login(Bp);
  check("the second password signs in straight away: reads ask for the CURRENT version (?cache=0)",
        loginB.status === 303, String(loginB.status));
  check("  ...and the first no longer does", (await w.login(A)).status === 401);
  await w.advance(31);
  check("  ...and the session made on the first is dead", !(await w.sessionWorks(cookieA)));
  check("  ...as is the one made before any reset", !(await w.sessionWorks(sessionBeforeAny)));
  check("  ...and the second's session works", await w.sessionWorks((loginB.headers.get("set-cookie") || "").split(";")[0]));
  const reads = await B.blobLog("read");
  check("every read the middleware and the functions made asked for ?cache=0",
        reads.length > 5 && reads.every((r) => r.query.cache === "0"), reads.filter((r) => r.query.cache !== "0").map((r) => r.path).join(","));
  const writes = await B.blobLog("write");
  check("every write was conditional: x-if-match, or create-only (x-allow-overwrite: 0)",
        writes.length > 3 && writes.every((r) => r.headers["x-if-match"] || r.headers["x-allow-overwrite"] === "0"),
        writes.filter((r) => !(r.headers["x-if-match"] || r.headers["x-allow-overwrite"] === "0")).map((r) => r.query.pathname).join(","));
  check("  ...to a private store, no random suffix, with the store id", writes.every((r) =>
        r.headers["x-vercel-blob-access"] === "private" && r.headers["x-add-random-suffix"] === "0" &&
        r.headers["x-vercel-blob-store-id"] === "fakestore0123" && r.authorized), "");
}
});

await section("EMAIL: only the code and the fixed text, only to the fixed address", async () => {
{
  await w.fresh();
  const code = await issue();
  const [m] = await B.mailbox();
  check("From is the sending mailbox and To is the fixed address",
        m.from === FAKE.user && m.to === FAKE.user && m.envelope_from === FAKE.user && m.envelope_to.join() === FAKE.user);
  check("the subject is exactly 'Smart Property Buying dashboard password code'", m.subject === "Smart Property Buying dashboard password code", m.subject);
  check("plain text, one part, no HTML", m.content_type === "text/plain" && !m.multipart);
  const allowed = new Set(["Subject", "From", "To", "Date", "Content-Type", "MIME-Version", "Content-Transfer-Encoding"]);
  check("no Cc, Bcc, Reply-To or other header", m.header_names.every((h) => allowed.has(h)), m.header_names.join(","));
  check("the body is the fixed text with the code in it, and nothing else",
        m.text === "Your Smart Property Buying dashboard password code is " + code + ".\n\nIt works for 15 minutes. If you did not ask for it, ignore this email and nothing changes.\n", JSON.stringify(m.text));
  const store = (await B.text("site-auth/reset.json"));
  const rec1 = JSON.parse(store);
  check("the email carries no hash, nonce, token or key", ![rec1.nonce, rec1.code_mac, FAKE.token, FAKE.service, FAKE.smtpPassword, FAKE.site].some((s) => m.raw.includes(s)));
  const logins = (await B.ctl("/smtp_logins")).logins;
  check("the mailbox was logged into with the configured user and password", logins.length === 1 && logins[0][0] === FAKE.user && logins[0][1] === true, JSON.stringify(logins));
  await w.reset(code, newPassword("mail"));
  const notice = (await B.mailbox())[1];
  check("the change notice: fixed subject, one address, a time, and no code or password",
        notice.subject === "Smart Property Buying dashboard password changed" && /was changed at \d{2} \w{3} \d{4} \d{2}:\d{2}/.test(notice.text) &&
        !notice.text.includes(code) && ![...SEEN_PASSWORDS].some((p) => p !== FAKE.site && notice.text.includes(p)) &&
        notice.envelope_to.join() === FAKE.user, JSON.stringify(notice.text));
}
{
  await w.fresh({ SPB_RESET_TO: "owner@example.org", SPB_SMTP_USER: "sender@example.org" });
  await B.ctl("/smtp", { mode: "ok" });
  const page = await body(await w.call(w.req("/forgot", { accept: "text/html" })));
  check("SPB_RESET_TO changes the address shown (masked): o***@example.org", page.includes("A 6-digit code will be sent to o***@example.org"));
  const before = await mailCount();
  await w.forgot();
  const logins = (await B.ctl("/smtp_logins")).logins;
  check("a different SPB_SMTP_USER is the login name (the fake only accepts the configured one, so it is refused and no mail is sent)",
        logins.length >= 1 && logins.every((l) => l[0] === "sender@example.org" && l[1] === false) && (await mailCount()) === before,
        JSON.stringify(logins));
}
for (const mode of ["auth_fail", "down", "drop"]) {
  await w.fresh();
  await B.smtp(mode, 0);
  const r = await w.forgot();
  const j = await jbody(r);
  check("SMTP " + mode + ": the browser still gets the generic 200, no error leaks",
        r.status === 200 && j.message === "If this site is set up for it, a code has been emailed." && !/smtp|535|auth|refused/i.test(JSON.stringify(j)), JSON.stringify(j));
  check("  ...and nothing was delivered", (await mailCount()) === 0);
  await B.smtp("ok", 0);
}
{
  await w.fresh();
  const code = await issue();
  await B.smtp("auth_fail", 0);
  const r = await w.reset(code, newPassword("nomail"));
  check("if the change notice cannot be sent the reset still succeeds", r.status === 200, String(r.status));
  await B.smtp("ok", 0);
}
});

await section("PAGES: escaping, redirects, CSP, nothing reflected", async () => {
{
  await w.fresh();
  const XSS = ['"><script>alert(1)</script>', "'><img src=x onerror=alert(1)>", "javascript:alert(1)", "</script><script>alert(2)</script>", "{{7*7}}", "${7*7}", "%3Cscript%3Ealert(3)%3C/script%3E"];
  for (const page of ["/forgot", "/reset", "/login"]) {
    for (const x of XSS) {
      const q = encodeURIComponent(x);
      const res = await w.call(w.req(page + "?next=" + q + "&reset=" + q + "&email=" + q + "&code=" + q + "&error=" + q + "&" + q + "=1", { accept: "text/html" }));
      const html = await body(res);
      const loc = res.headers.get("location");
      check("GET " + page + " with " + JSON.stringify(x).slice(0, 34) + " in every query parameter: nothing reflected",
            res.status === 200 && !html.includes(x) && !html.includes("alert(1)") && !html.includes("alert(2)") && !html.includes("onerror") && !loc && !html.includes("49"),
            res.status + " " + loc);
    }
  }
  for (const page of ["/forgot", "/reset"]) {
    const res = await w.call(w.req(page + "?next=https%3A%2F%2Fevil.example.com&redirect=//evil.example", { accept: "text/html" }));
    const html = await body(res);
    check("GET " + page + "?next=https://evil... -> a page, no Location header, no mention of the host",
          res.status === 200 && !res.headers.get("location") && !html.includes("evil.example"));
  }
  const login = await w.call(w.req("/login?next=%2F%2Fevil.example&reset=1", { accept: "text/html" }));
  const lh = await body(login);
  check("/login?next=//evil keeps next out of reach: the hidden field is '/'", /name="next" value="\/"/.test(lh));
  const posted = await w.login(FAKE.site, { next: "https://evil.example.com/x" });
  check("a successful login with next=https://evil goes to '/'", posted.headers.get("location") === "/");

  // Headers and CSP on both pages.
  const seenNonces = new Set();
  for (const page of ["/forgot", "/reset"]) {
    const res = await w.call(w.req(page, { accept: "text/html" }));
    const html = await body(res);
    const h = res.headers;
    const csp = h.get("content-security-policy") || "";
    const nonce = (/script-src 'nonce-([A-Za-z0-9_-]+)'/.exec(csp) || [])[1];
    seenNonces.add(nonce);
    check(page + ": no-store, no-referrer, DENY, nosniff, html, noindex",
          h.get("cache-control") === "no-store" && h.get("referrer-policy") === "no-referrer" && h.get("x-frame-options") === "DENY" &&
          h.get("x-content-type-options") === "nosniff" && h.get("content-type").startsWith("text/html") && html.includes("noindex"),
          JSON.stringify([...h.entries()]));
    check(page + ": CSP forbids framing, other hosts and inline script without the nonce",
          /frame-ancestors 'none'/.test(csp) && /default-src 'none'/.test(csp) && /connect-src 'self'/.test(csp) && !/script-src[^;]*unsafe-inline/.test(csp) && !/https?:/.test(csp), csp);
    check(page + ": the one script carries the matching nonce", nonce && html.split("<script").length === 2 && html.includes('<script nonce="' + nonce + '">'));
    const script = (/<script[^>]*>([\s\S]*?)<\/script>/.exec(html) || [])[1] || "";
    check(page + ": the script never writes HTML (no innerHTML, document.write, eval, new Function), only textContent",
          script.includes("textContent") && !/innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function|location\.href\s*=|\.src\s*=/.test(script));
    check(page + ": the only navigation is to the fixed /login?reset=1", !/location\.(replace|assign)\((?!"\/login\?reset=1")/.test(script));
    check(page + ": no password or code in the URL: forms post, and nothing uses GET with fields",
          !/method="get"/i.test(html) && (page !== "/reset" || /<form id="f" method="post" action="\/reset"/.test(html)));
  }
  const n2 = /nonce-([A-Za-z0-9_-]+)/.exec((await w.call(w.req("/forgot"))).headers.get("content-security-policy"))[1];
  check("the nonce is different on every response", !seenNonces.has(n2));
  const lp = await w.call(w.req("/login", { accept: "text/html" }));
  check("/login keeps its headers (DENY, no-store, nosniff, noindex)",
        lp.headers.get("x-frame-options") === "DENY" && lp.headers.get("cache-control") === "no-store" && (await body(lp)).includes("noindex"));
  const rp = await body(await w.call(w.req("/reset", { accept: "text/html" })));
  check("/reset: code, new password and confirm boxes, the rules, a show-passwords box",
        ["id=\"code\"", "id=\"pw\"", "id=\"pw2\"", "At least 12 characters", "Show passwords", "autocomplete=\"new-password\"", "one-time-code"].every((s) => rp.includes(s)));
  check("/reset: nothing pre-filled and no field takes the code or password from the URL", !/value="/.test(rp.replace(/name="next" value=""/g, "")));
}
});

await section("THE DIRECT FUNCTION ROUTES THAT ARE NOT PUBLIC, AND CLEAN RESPONSES", async () => {
{
  await w.fresh();
  const secrets = [FAKE.token, FAKE.site, FAKE.service, FAKE.smtpPassword, FAKE.contact];
  const sample = [
    await w.forgot(), await w.reset("123456", newPassword("leak")), await w.reset("12", "x"),
    await w.call(w.json("/api/auth/reset", null, { origin: "https://evil.example", raw: "{}" })),
    await w.call(w.json("/api/auth/reset", null, { raw: "{" })),
  ];
  let leaked = false;
  for (const r of sample) { const t = await body(r); if (secrets.some((s) => t.includes(s))) leaked = true; }
  check("no response from the functions contains a token, key or password", !leaked);
}
});

await section("TIMING: the answer to /forgot takes at least the floor, whatever happened", async () => {
{
  await w.fresh();
  await B.ctl("/floor", { seconds: 0.5 });
  const t1 = performance.now(); await w.forgot(); const sent = performance.now() - t1;
  const t2 = performance.now(); await w.forgot(); const limited = performance.now() - t2;
  await B.ctl("/floor", { seconds: 0 });
  check("sent and rate-limited answers both take at least the floor (500 ms here), so a stopwatch tells nothing",
        sent >= 480 && limited >= 480, Math.round(sent) + " ms / " + Math.round(limited) + " ms");
}
});

await section("CONSTANT-TIME COMPARES in the middleware", async () => {
  const src = fs.readFileSync(process.env.SPB_MW_PATH, "utf-8");
  const fn = /function timingSafeEqual\(a, b\) \{[\s\S]*?\r?\n\}\r?\n/.exec(src);
  check("the compare helper is there", !!fn);
  // Run the helper itself with every byte read counted: an early exit reads fewer bytes when the
  // difference is at the front than when it is at the back, and this is how that would show.
  let reads = 0;
  const spy = { encode: (s) => new Proxy(new TextEncoder().encode(s), {
    get(t, k) { if (typeof k === "string" && /^[0-9]+$/.test(k)) reads++; return Reflect.get(t, k, t); } }) };
  const tse = new Function("enc", fn[0] + "; return timingSafeEqual;")(spy);
  reads = 0; const front = tse("abcdefghijklmnop", "Xbcdefghijklmnop"); const readsFront = reads;
  reads = 0; const back = tse("abcdefghijklmnop", "abcdefghijklmnoX"); const readsBack = reads;
  reads = 0; const same = tse("abcdefghijklmnop", "abcdefghijklmnop");
  reads = 0; const shorter = tse("abcdefghijklmnop", "abc");
  check("the helper is right: equal strings equal, a different first byte, a different last byte or a different length not",
        same === true && front === false && back === false && shorter === false);
  check("the helper reads every byte whether the difference is first or last (no early exit)",
        readsFront > 0 && readsFront === readsBack, readsFront + " vs " + readsBack);
  // Every secret the new code compares goes through it, at each place it is compared.
  for (const site of [
    "timingSafeEqual(rec.env_fp, fp)",
    "timingSafeEqual(rec.mac, b64url(await hmacRaw(macKey, canon)))",
    "timingSafeEqual(b64url(bits), auth.rec.hash)",
    "timingSafeEqual(given, await sign(auth.key, user + \"|\" + exp))",
    "timingSafeEqual(given, await sign(again.key, user + \"|\" + exp))",
    "timingSafeEqual(again.key, auth.key)",
    "timingSafeEqual(given, password)",
  ]) check("compared in constant time: " + site, src.includes(site));
  check("and nowhere is a stored hash, mac, fingerprint or session key compared with == or ===",
        !/(?<!typeof\s)(rec\.(mac|hash|env_fp)|auth\.rec\.hash|auth\.key|again\.key)\s*[!=]==?\s*[a-zA-Z"]/.test(src));
});

await section("LOGIN FLOODS: bad logins and forged cookies are not a flood of store reads", async () => {
  await w.fresh();
  await B.clearBlobLog();
  const statuses = [];
  for (let i = 0; i < 12; i++) statuses.push((await w.login("wrong-password-" + i)).status);
  check("12 wrong logins one after another: all plain 401s", statuses.every((s) => s === 401), statuses.join(","));
  const reads = await B.blobLog("read");
  check("  ...at most 2 reads of the store (a login may re-read, but not more than once every 2 s)", reads.length <= 2, String(reads.length));
  await B.clearBlobLog();
  const exp = Math.floor(Date.now() / 1000) + 600;
  const forged = [];
  for (let i = 0; i < 12; i++) forged.push((await w.call(w.req("/stock.json", { cookie: "spb_session=" + exp + ".forged" + i }))).status);
  check("12 forged cookies one after another: all plain 401s", forged.every((s) => s === 401), forged.join(","));
  check("  ...and they cause no further reads inside the 2 s floor", (await B.blobLog("read")).length === 0, String((await B.blobLog("read")).length));
  await w.advance(3);
  await B.clearBlobLog();
  for (let i = 0; i < 12; i++) await w.login("wrong-password-" + i);
  check("three seconds later the next burst reads once, not twelve times", (await B.blobLog("read")).length === 1, String((await B.blobLog("read")).length));
});

await section("LOGIN COST: PBKDF2 at login only, a session check is one HMAC", async () => {
{
  await w.fresh();
  await makeOverride(newPassword("cost"));
  const P = [...SEEN_PASSWORDS].pop();
  const t0 = performance.now();
  const c = await w.cookieFor(P);
  const loginMs = performance.now() - t0;
  const t1 = performance.now();
  for (let i = 0; i < 20; i++) await w.sessionWorks(c);
  const sessionMs = (performance.now() - t1) / 20;
  const iter = (await rec("password")).iter;
  console.log("  INFO  stored record: " + iter + " PBKDF2-SHA256 iterations; login " + Math.round(loginMs) + " ms, session check " + sessionMs.toFixed(2) + " ms (includes the emulator)");
  check("a session check costs a small fraction of a login (no PBKDF2 per request)", sessionMs < loginMs / 3 || sessionMs < 15, sessionMs + " vs " + loginMs);
  check("the iteration count is in the safe band and is what the writer was built with", iter >= 100000 && iter <= 1000000, String(iter));
}
});

await section("LOGS: no code, password, token or key in anything the functions or the middleware wrote", async () => {
{
  // B.stderr is everything the Python side wrote since it started (B.logs() is only since the last wipe).
  const pyLogs = B.stderr;
  const everything = pyLogs + "\n" + nodeLogs.join("\n");
  const secrets = [FAKE.token, FAKE.site, FAKE.service, FAKE.smtpPassword, FAKE.contact, ...SEEN_CODES, ...[...SEEN_PASSWORDS].filter((p) => p !== FAKE.site)];
  const found = secrets.filter((s) => s && everything.includes(s));
  check("none of " + secrets.length + " secrets (codes, passwords, token, keys) appears in " + everything.length + " bytes of logs", found.length === 0,
        found.map((s) => s.slice(0, 4) + "...").join(","));
  check("the functions did log (so the absence above means something)", /\[auth\] forgot: code sent/.test(pyLogs) && /\[auth\] reset: password changed/.test(pyLogs) && /\[auth\] reset: wrong code/.test(pyLogs));
  check("  ...including failures, by class name only", /mail failed \(SMTPAuthenticationError 535\)|mail failed \(SMTPServerDisconnected\)|mail failed \(/.test(pyLogs));
  check("the middleware logged store failures by status or class only", nodeLogs.some((l) => /password store read failed/.test(l)) &&
        nodeLogs.filter((l) => /\[auth\]/.test(l)).every((l) => !/vercel_blob_rw|Bearer|password":/i.test(l)));
}
});

console.log("\n" + (fail === 0 ? "ALL " + pass + " CHECKS PASSED" : pass + " passed, " + fail + " FAILED"));
await w.stop();
process.exit(fail === 0 ? 0 : 1);
