// Exercise the generated edge middleware the way Vercel will call it, before it is
// deployed in front of Coleen's dashboard. A gate that is wrong is either a locked-out
// consultant or a public catalogue, and both are found here for free.
import mw from "./middleware.mjs";

const USER = "digital@smartpropertybuying.com.au";
const PASS = "test-password-not-the-real-one";

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  PASS  " + name); }
  else { fail++; console.log("  FAIL  " + name + (detail ? "  -> " + detail : "")); }
}

function req(path, { method = "GET", cookie = "", accept = "*/*", body = null, extra = {} } = {}) {
  const headers = { accept, ...extra };
  if (cookie) headers.cookie = cookie;
  if (body !== null) headers["content-type"] = "application/x-www-form-urlencoded";
  return new Request("https://app.smartpropertybuying.com.au" + path, { method, headers, body });
}

const call = (r) => mw(r);

console.log("\n--- fails closed when unconfigured ---");
delete process.env.SITE_PASSWORD;
{
  const res = await call(req("/"));
  check("no SITE_PASSWORD -> 503, not public", res && res.status === 503,
        res ? "status " + res.status : "PASSED THE REQUEST THROUGH — SITE WOULD BE PUBLIC");
  const body = res ? await res.text() : "";
  check("503 explains why", body.includes("SITE_PASSWORD is not set"));
}

process.env.SITE_PASSWORD = PASS;
process.env.SITE_USERNAME = USER;

console.log("\n--- unauthenticated ---");
{
  const res = await call(req("/", { accept: "text/html,application/xhtml+xml" }));
  check("browsing / -> redirect to login", res && res.status === 302);
  check("  ...carrying the destination",
        res && res.headers.get("location").startsWith("/login?next="),
        res && res.headers.get("location"));
}
{
  const res = await call(req("/stock.json"));
  check("stock.json -> 401, NOT served", res && res.status === 401,
        res ? "status " + res.status : "CATALOGUE SERVED WITHOUT LOGIN");
  const body = res ? await res.json() : {};
  check("  ...as JSON the page can detect", body.error === "auth_required");
}
{
  const res = await call(req("/api/research", { method: "POST" }));
  check("api/research -> 401", res && res.status === 401);
}
{
  const res = await call(req("/reports/client-report.pdf"));
  check("a PDF -> 401", res && res.status === 401);
}

console.log("\n--- the login page ---");
{
  const res = await call(req("/login", { accept: "text/html" }));
  const html = await res.text();
  check("GET /login -> 200 page", res.status === 200);
  check("  ...has an email + password form",
        html.includes('name="email"') && html.includes('name="password"') &&
        html.includes('method="POST"'));
  check("  ...is not cached", (res.headers.get("cache-control") || "").includes("no-store"));
  check("  ...cannot be framed", res.headers.get("x-frame-options") === "DENY");
  check("  ...is not indexed", html.includes("noindex"));
  check("  ...never contains the password", !html.includes(PASS));
}

console.log("\n--- signing in ---");
let cookie = "";
{
  const res = await call(req("/login", { method: "POST",
    body: "email=" + encodeURIComponent(USER) + "&password=" + encodeURIComponent(PASS) }));
  check("correct credentials -> 303", res.status === 303, "status " + res.status);
  const sc = res.headers.get("set-cookie") || "";
  check("  ...sets a session cookie", sc.startsWith("spb_session="));
  check("  ...HttpOnly", sc.includes("HttpOnly"));
  check("  ...Secure", sc.includes("Secure"));
  check("  ...SameSite", sc.includes("SameSite=Lax"));
  check("  ...cookie does not contain the password", !sc.includes(PASS));
  check("  ...lands on the dashboard", res.headers.get("location") === "/");
  cookie = sc.split(";")[0];
}
{
  const res = await call(req("/login", { method: "POST",
    body: "email=" + encodeURIComponent(USER) + "&password=wrong" }));
  check("wrong password -> 401", res.status === 401);
  const html = await res.text();
  check("  ...says so on the page", html.includes("not right"));
  check("  ...sets no cookie", !res.headers.get("set-cookie"));
}
{
  const res = await call(req("/login", { method: "POST",
    body: "email=someone@else.com&password=" + encodeURIComponent(PASS) }));
  check("wrong email -> 401", res.status === 401);
}
{
  const res = await call(req("/login", { method: "POST",
    body: "email=" + encodeURIComponent(USER.toUpperCase()) +
          "&password=" + encodeURIComponent(PASS) }));
  check("email is case-insensitive (Coleen's phone will capitalise it)", res.status === 303);
}

console.log("\n--- signed in ---");
{
  const res = await call(req("/", { cookie, accept: "text/html" }));
  check("dashboard is served", res === undefined, res && "status " + res.status);
}
{
  const res = await call(req("/stock.json", { cookie }));
  check("stock.json is served", res === undefined, res && "status " + res.status);
}
{
  const res = await call(req("/login", { cookie, accept: "text/html" }));
  check("/login bounces to the app when already signed in",
        res && res.status === 302 && res.headers.get("location") === "/");
}

console.log("\n--- Agent 1's service key: POST /api/research, and nothing else ---");
{
  // Agent 1's webhook runs a research on a graded caller from a server, with no browser and so
  // no session cookie. With the key it may do exactly that one thing. Every check after
  // the first is a way the hole could be wider than that, and each one is a line a
  // careless refactor would change.
  const KEY = "svc-key-for-tests-not-the-real-one";
  const keyed = (path, method, key) =>
    req(path, { method, extra: key === undefined ? {} : { "x-spb-service-key": key } });

  // Anything the middleware logs while a key is in play is captured, so a stray
  // console.log(request.headers) fails here rather than printing a secret into Vercel's logs.
  const logged = [];
  const quiet = async (r) => {
    const saved = {};
    for (const m of ["log", "info", "warn", "error", "debug"]) {
      saved[m] = console[m];
      console[m] = (...a) => logged.push(a.map(String).join(" "));
    }
    try { return await call(r); } finally { Object.assign(console, saved); }
  };
  const served = (res) => res === undefined;
  const refused = (res) => res && res.status === 401;

  delete process.env.SPB_SERVICE_KEY;
  {
    const res = await quiet(keyed("/api/research", "POST", ""));
    check("env unset + empty header -> not bypassed", refused(res),
          res ? "status " + res.status : "SERVED: an empty key matched an empty header");
    const res2 = await quiet(keyed("/api/research", "POST", KEY));
    check("env unset + any header -> not bypassed", refused(res2),
          res2 ? "status " + res2.status : "SERVED WITH NO KEY CONFIGURED");
  }

  process.env.SPB_SERVICE_KEY = "   ";
  {
    const res = await quiet(keyed("/api/research", "POST", "   "));
    check("env whitespace + header whitespace -> not bypassed", refused(res),
          res ? "status " + res.status : "SERVED: a blank key was trusted");
    const res2 = await quiet(keyed("/api/research", "POST", ""));
    check("env whitespace + empty header -> not bypassed", refused(res2),
          res2 ? "status " + res2.status : "SERVED: a blank key was trusted");
  }

  process.env.SPB_SERVICE_KEY = KEY;
  {
    const res = await quiet(keyed("/api/research", "POST", KEY));
    check("correct key + POST /api/research -> passes through", served(res),
          res && "status " + res.status);
  }
  {
    process.env.SPB_SERVICE_KEY = "  " + KEY + "\n";
    const res = await quiet(keyed("/api/research", "POST", KEY));
    check("  ...the configured key is trimmed, as SITE_PASSWORD is", served(res),
          res && "status " + res.status);
    process.env.SPB_SERVICE_KEY = KEY;
  }
  {
    const res = await quiet(keyed("/api/research", "POST", "wrong-key"));
    check("wrong key -> 401, not served", refused(res),
          res ? "status " + res.status : "SERVED WITH A WRONG KEY");
    const body = res ? await res.text() : "";
    let parsed = {};
    try { parsed = JSON.parse(body); } catch (e) { /* checked below */ }
    check("  ...as the JSON 401 a fetch can detect", parsed.error === "auth_required", body);
    check("  ...and the 401 echoes neither key", !body.includes(KEY) && !body.includes("wrong-key"));
  }
  {
    // Same length as the real key, one character different: catches a length-only compare.
    const near = KEY.slice(0, -1) + (KEY.endsWith("e") ? "f" : "e");
    const res = await quiet(keyed("/api/research", "POST", near));
    check("same-length wrong key -> not bypassed", refused(res),
          res ? "status " + res.status : "SERVED: the key is compared by length only");
  }
  for (const k of [KEY + "x", "x" + KEY, KEY.slice(0, -1), undefined]) {
    const res = await quiet(keyed("/api/research", "POST", k));
    check(`key ${k === undefined ? "header missing" : "of length " + k.length} -> not bypassed`,
          refused(res), res ? "status " + res.status : "SERVED WITHOUT THE EXACT KEY");
  }
  for (const m of ["GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"]) {
    const res = await quiet(keyed("/api/research", m, KEY));
    check(`correct key + ${m} /api/research -> not bypassed`, refused(res),
          res ? "status " + res.status : m + " SERVED ON THE SERVICE KEY");
  }
  // A prefix or loose match would have opened all of these.
  for (const p of ["/api/research/extra", "/api/researchX", "/api/research/",
                   "/api/research.py", "/API/research", "/api/Research", "/api//research",
                   "/stock.json", "/", "/api/hot_leads", "/api/refresh", "/api/narrative",
                   "/reports/client-report.pdf"]) {
    const res = await quiet(keyed(p, "POST", KEY));
    check(`correct key + POST ${p} -> not bypassed`, refused(res),
          res ? "status " + res.status : "SERVED ON THE SERVICE KEY");
  }
  {
    const res = await quiet(keyed("/stock.json", "GET", KEY));
    check("correct key on GET /stock.json -> not bypassed", refused(res),
          res ? "status " + res.status : "CATALOGUE SERVED ON THE SERVICE KEY");
  }
  {
    // The key is a way past the session check, never past the fail-closed one.
    delete process.env.SITE_PASSWORD;
    const res = await quiet(keyed("/api/research", "POST", KEY));
    check("correct key with SITE_PASSWORD unset -> still 503", res && res.status === 503,
          res ? "status " + res.status : "SERVED ON AN UNCONFIGURED SITE");
    process.env.SITE_PASSWORD = PASS;
  }
  // A person's session is untouched by the key being configured.
  {
    const res = await quiet(req("/stock.json", { cookie }));
    check("with the key configured, a cookie session still serves stock.json", served(res),
          res && "status " + res.status);
    const res2 = await quiet(req("/api/research", { method: "POST", cookie }));
    check("  ...and POST /api/research from the browser, with no key", served(res2),
          res2 && "status " + res2.status);
    const res3 = await quiet(req("/api/research", { method: "POST", cookie,
                                                    extra: { "x-spb-service-key": "wrong" } }));
    check("  ...and a stray wrong key does not lock a signed-in person out", served(res3),
          res3 && "status " + res3.status);
    const res4 = await quiet(req("/api/research", { method: "POST" }));
    check("  ...while no cookie and no key is still 401", refused(res4),
          res4 ? "status " + res4.status : "SERVED WITHOUT KEY OR COOKIE");
  }
  check("nothing logged while a key was in play carries it",
        !logged.some((line) => line.includes(KEY)), logged.join(" | "));

  delete process.env.SPB_SERVICE_KEY;
}

console.log("\n--- forging the cookie ---");
{
  const res = await call(req("/stock.json", { cookie: cookie.slice(0, -3) + "aaa" }));
  check("tampered signature rejected", res && res.status === 401);
}
{
  const [, tok] = cookie.split("=");
  const far = Math.floor(Date.now() / 1000) + 999999;
  const res = await call(req("/stock.json",
    { cookie: "spb_session=" + far + "." + tok.split(".")[1] }));
  check("extending the expiry breaks the signature", res && res.status === 401);
}
{
  const expired = "spb_session=" + (Math.floor(Date.now() / 1000) - 60) + ".whatever";
  const res = await call(req("/stock.json", { cookie: expired }));
  check("expired session rejected", res && res.status === 401);
}
{
  const res = await call(req("/stock.json", { cookie: "spb_session=" }));
  check("empty cookie rejected", res && res.status === 401);
}
{
  process.env.SITE_PASSWORD = "a-brand-new-password";
  const res = await call(req("/stock.json", { cookie }));
  check("changing the password revokes live sessions", res && res.status === 401);
  process.env.SITE_PASSWORD = PASS;
}

console.log("\n--- open redirect / injection ---");
{
  const res = await call(req("/login", { method: "POST",
    body: "email=" + encodeURIComponent(USER) + "&password=" + encodeURIComponent(PASS) +
          "&next=https%3A%2F%2Fevil.example.com" }));
  check("next=https://evil is refused", res.headers.get("location") === "/",
        res.headers.get("location"));
}
{
  const res = await call(req("/login", { method: "POST",
    body: "email=" + encodeURIComponent(USER) + "&password=" + encodeURIComponent(PASS) +
          "&next=%2F%2Fevil.example.com" }));
  check("next=//evil is refused", res.headers.get("location") === "/",
        res.headers.get("location"));
}
{
  const res = await call(req("/login", { method: "POST",
    body: "email=" + encodeURIComponent(USER) + "&password=" + encodeURIComponent(PASS) +
          "&next=%2Flogin" }));
  check("next=/login does not loop", res.headers.get("location") === "/");
}
{
  const res = await call(req("/login", { method: "POST",
    body: "email=" + encodeURIComponent(USER) + "&password=" + encodeURIComponent(PASS) +
          "&next=%2Fsearch%3Fq%3D1" }));
  check("a real path is honoured", res.headers.get("location") === "/search?q=1");
}
{
  const res = await call(req('/login?next=%2F%22%3E%3Cscript%3Ealert(1)%3C%2Fscript%3E',
    { accept: "text/html" }));
  const html = await res.text();
  check("next is HTML-escaped in the form", !html.includes("<script>alert(1)"),
        "SCRIPT INJECTED INTO LOGIN PAGE");
}

console.log("\n--- /r is public, and NOTHING else is ---");
{
  // Coleen has to send a buyer their report without handing them this app, which holds
  // every builder and every wholesale price. /r is a static shell that renders a report
  // out of the URL FRAGMENT — never transmitted — so it can serve the same bytes to
  // everyone and leak nothing. That makes it the one deliberate hole in the gate, and its
  // scope has to stay exactly this size.
  for (const p of ["/r", "/r.html"]) {
    const res = await call(req(p, { accept: "text/html" }));
    check(`${p} is served without a login`, res === undefined,
          res && "status " + res.status);
  }
  // A prefix test would have opened all of these. This is the check that catches a
  // startsWith("/r") refactor.
  for (const p of ["/research", "/reports", "/r/../stock.json", "/rstock.json",
                   "/r.html.json", "/reports/client.pdf"]) {
    const res = await call(req(p));
    check(`${p} still requires a login`, res && res.status === 401,
          res ? "status " + res.status : "SERVED WITHOUT AUTH");
  }
  // The gate's whole purpose.
  const stock = await call(req("/stock.json"));
  check("stock.json is still gated alongside the public /r",
        stock && stock.status === 401, "CATALOGUE EXPOSED");
}

console.log("\n--- signing out ---");
{
  const res = await call(req("/logout", { cookie, accept: "text/html" }));
  check("/logout redirects to login", res.status === 303);
  const sc = res.headers.get("set-cookie") || "";
  check("  ...clears the cookie", sc.includes("Max-Age=0"));
}

console.log("\n" + (fail === 0 ? "ALL " + pass + " CHECKS PASSED"
                               : pass + " passed, " + fail + " FAILED"));
process.exit(fail === 0 ? 0 : 1);
