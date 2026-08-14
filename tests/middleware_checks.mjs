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

function req(path, { method = "GET", cookie = "", accept = "*/*", body = null } = {}) {
  const headers = { accept };
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
