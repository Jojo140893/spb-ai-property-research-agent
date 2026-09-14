// Exercises the Hot Leads (Agent 1) tab: the code between the HOT-LEADS-BEGIN and
// HOT-LEADS-END markers in index.html, sliced out and run under Node with fetch, the DOM
// and sessionExpired stubbed. Sliced rather than reimplemented, so it cannot drift from
// what ships.
//
// NOTHING HERE TOUCHES THE NETWORK. fetch inside the sandbox is a stub from the start, and
// a stub asked for anything other than the two dashboard endpoints fails the run.
//
// Every string on that tab came from someone else -- a phone caller's transcribed speech
// or a scraped listing -- so most of what is checked is that none of it can become markup,
// or a link that runs code.
//
//     node tests/hot_leads_tab_checks.mjs
//     HL_INDEX=path/to/other/index.html node tests/hot_leads_tab_checks.mjs   (mutation runs)
import { readFileSync } from "node:fs";
import vm from "node:vm";

const INDEX = process.env.HL_INDEX || new URL("../index.html", import.meta.url);
const html = readFileSync(INDEX, "utf8");

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  PASS  " + name); }
  else { fail++; console.log("  FAIL  " + name + (detail ? "  -> " + detail : "")); }
}
function throwsNot(name, fn) {
  try { fn(); check(name, true); } catch (e) { check(name, false, "threw " + e); }
}

// ------------------------------------------------------------------ the slice
const BEGIN = "    // HOT-LEADS-BEGIN";
const END = "    // HOT-LEADS-END";
const b = html.indexOf(BEGIN), e = html.indexOf(END);
if (b < 0 || e < 0 || e < b) {
  console.log("  FAIL  HOT-LEADS-BEGIN / HOT-LEADS-END markers not found in index.html");
  process.exit(1);
}
const block = html.slice(b, e);
// The page's one escaper, reused rather than copied: it is declared elsewhere in the script.
const escMatch = html.match(/^    const esc = [\s\S]*?\[c\]\)\);$/m);
if (!escMatch) {
  console.log("  FAIL  the page's esc() helper was not found -- renamed?");
  process.exit(1);
}

const EXPORTS = ["hlSafeUrl", "hlTelHref", "hlMailHref", "hlMoney", "hlWhen", "hlBriefChips",
  "hlRecCard", "hlRecsBlock", "hlLiveBlock", "hlAppointment", "hlLeadCard", "hlRenderLeads",
  "hlSortNewest", "hlUnavailable", "loadHotLeads", "rerunHotLead"];

const ALLOWED_FETCH = new Set(["/api/hot_leads", "/api/research"]);

function sandbox() {
  const els = new Map();
  const calls = { fetch: [], expired: 0 };
  let responder = async () => { throw new Error("no fetch responder set"); };
  const ctx = vm.createContext({
    URL, console,
    document: { getElementById: id => els.get(id) || null },
    sessionExpired: () => { calls.expired++; },
    fetch: async (url, opts) => {
      calls.fetch.push({ url, opts });
      if (!ALLOWED_FETCH.has(url)) throw new Error("BLOCKED: test tried to fetch " + url);
      return responder(url, opts);
    },
  });
  const api = vm.runInContext(
    escMatch[0] + "\n" + block + "\n;({" + EXPORTS.join(", ") +
    ", leads: () => HOT_LEADS })", ctx, { filename: "index.html#hot-leads" });
  const el = id => {
    const o = { id, innerHTML: "", textContent: "", className: "", disabled: false };
    els.set(id, o);
    return o;
  };
  return { api, calls, el, respond: fn => { responder = fn; } };
}

const reply = (status, body) => ({
  status, ok: status >= 200 && status < 300,
  headers: { get: () => "application/json" },
  json: async () => {
    if (body instanceof Error) throw body;
    return JSON.parse(JSON.stringify(body));
  },
});

// ------------------------------------------------------------------ markup auditor
// Parses every tag the renderers emit and rejects anything outside a small allowlist. A
// caller's name that reaches the page unescaped shows up here as a tag that should not
// exist; a listing's javascript: URL shows up as an href that is not http(s)/mailto/tel.
const TAGS = new Set(["div", "span", "a", "strong", "br", "details", "summary", "p", "button", "em"]);
const ATTRS = new Set(["class", "id", "href", "target", "rel", "title", "style", "onclick", "type", "disabled"]);
const decode = s => s.replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");

function markupProblems(out) {
  const problems = [];
  if (/<\s*script/i.test(out)) problems.push("raw <script");
  const tagRe = /<\/?([a-zA-Z][\w-]*)([^>]*)>/g;
  let m;
  while ((m = tagRe.exec(out))) {
    const name = m[1].toLowerCase();
    if (!TAGS.has(name)) problems.push("tag <" + name + ">");
    const attrText = m[2];
    const attrRe = /\s+([^\s="'>\/]+)(?:="([^"]*)")?/g;
    const leftover = attrText.replace(attrRe, "").replace(/\s*\/?\s*$/, "");
    if (leftover) problems.push("unparsed attribute text in <" + name + ">: " + leftover.slice(0, 60));
    let a;
    while ((a = attrRe.exec(attrText))) {
      const attr = a[1].toLowerCase(), val = decode(a[2] || "");
      if (!ATTRS.has(attr)) problems.push("attribute " + attr + " on <" + name + ">");
      if (attr === "href" && !/^(https?:\/\/|mailto:[^:]+@|tel:\+?\d{6,}$)/i.test(val))
        problems.push("href " + JSON.stringify(val));
      if (attr === "onclick" && !/^(rerunHotLead\(\d+\)|loadHotLeads\(true\))$/.test(val))
        problems.push("onclick " + JSON.stringify(val));
      if (attr === "style" && /url\(|expression|javascript/i.test(val))
        problems.push("style " + JSON.stringify(val));
    }
    if (name === "a" && /target="_blank"/.test(attrText) && !/rel="noopener noreferrer"/.test(attrText))
      problems.push("target=_blank without rel=noopener noreferrer");
  }
  return problems;
}
function auditMarkup(name, out) {
  const p = markupProblems(out);
  check(name, p.length === 0, p.slice(0, 5).join(" | "));
}

// The auditor itself must be able to fail, or every "clean markup" line below is empty.
console.log("\n--- the markup auditor catches what it exists for ---");
check("auditor flags a raw script tag", markupProblems('<div><script>alert(1)</' + 'script></div>').length > 0);
check("auditor flags a javascript: href", markupProblems('<a href="javascript:alert(1)">x</a>').length > 0);
check("auditor flags an event-handler attribute", markupProblems('<span onmouseover="x">y</span>').length > 0);
check("auditor flags an unlisted tag", markupProblems('<img src="x">').length > 0);
check("auditor flags a blank target without rel", markupProblems('<a href="https://a.b/" target="_blank">x</a>').length > 0);
check("auditor passes ordinary markup",
  markupProblems('<div class="x"><a href="https://a.b/?q=1&amp;r=2" target="_blank" rel="noopener noreferrer">x</a></div>').length === 0);

// ------------------------------------------------------------------ fixtures
const EVIL = "<script>alert(1)</script>";
const EVIL_TEXT = "&lt;script&gt;alert(1)&lt;/script&gt;";

const hostile = () => ({
  version: 1, callId: "call-evil", createdAt: "2026-09-16T04:30:00Z",
  endedReason: "customer-ended-call", durationSeconds: 312,
  recordingUrl: "data:text/html,<script>alert(2)</script>",
  customer: { name: EVIL, email: 'x@y.com" onmouseover="alert(1)', phone: "+61 4<script>12 345 678" },
  grade: "hot", gradeSource: "agent",
  gradeReason: EVIL + ' <img src=x onerror="alert(3)">',
  summary: "Wants to move fast " + EVIL,
  brief: {
    client_name: EVIL, budget_max: 650000, deposit_amount: 50000, state: "VIC<b>",
    primary_suburbs: ["Tarneit", EVIL], bedrooms_min: 4, product_type: ["House & Land", "<i>x</i>"],
    buyer_type: "First home buyer", finance_status: EVIL, timeframe: "3 months", search_radius_km: null,
  },
  appointment: { start: "2026-09-18T10:00:00+10:00", meetingUrl: "javascript:alert(document.cookie)",
                 uid: "u1", source: "auto" },
  kommo: { contactId: 1, leadId: 2, leadUrl: "  JAVASCRIPT:alert(4)" },
  recommendations: [{
    builder_name: EVIL, house_design: '"><svg onload=alert(5)>', lot_address: "Lot 12 <u>Evil</u> St",
    suburb: "Tarneit", state: "VIC", realistic_total_price: 650200, advertised_package_price: 600000,
    bedrooms: 4, bathrooms: null, car_spaces: null, land_size_sqm: 350, house_size_sqm: null,
    score: 87.84, recommendation: "Recommend", recommendation_reason: EVIL, content_hash: "h1",
    date_checked: "16/09/2026", source_link: { url: "java\tscript:alert(6)", label: EVIL },
  }],
  research: { status: "ok", message: EVIL, record_id: "r1", shortlist_count: 1, widened: EVIL },
});

const rec = (over = {}) => ({
  builder_name: "Metricon", house_design: "Aspen 25", lot_address: "Lot 101 Example St",
  suburb: "Tarneit", state: "VIC", realistic_total_price: null, advertised_package_price: 540000,
  bedrooms: 4, bathrooms: 2, car_spaces: 0, land_size_sqm: null, house_size_sqm: 210,
  score: 81, recommendation: "Recommend with Conditions", recommendation_reason: "Under budget.",
  content_hash: "h", date_checked: "16/09/2026",
  source_link: { url: "https://builder.example.com/stock.pdf?row=24&v=2", label: "Price list - row 24" },
  ...over,
});

const clean = (over = {}) => ({
  version: 1, callId: "call-1", createdAt: "2026-09-15T00:10:00Z", endedReason: "hangup",
  durationSeconds: 400, recordingUrl: "https://storage.vapi.example/rec/1.wav",
  customer: { name: "Jo Citizen", email: "jo.citizen@example.com", phone: "+61412345678" },
  grade: "hot", gradeSource: "rules", gradeReason: "Pre-approved, wants to buy within 3 months.",
  summary: "Jo wants a 4 bed in Tarneit.",
  brief: { client_name: "Jo Citizen", budget_max: 700000, deposit_amount: 0, state: "VIC",
           primary_suburbs: ["Tarneit"], bedrooms_min: 4, product_type: ["House & Land"],
           buyer_type: "Owner Occupier", finance_status: "pre-approved", timeframe: "3 months",
           search_radius_km: 10 },
  appointment: { start: "2026-12-01T00:00:00Z", meetingUrl: "https://meet.google.com/abc-defg-hij",
                 uid: "u2", source: "call" },
  kommo: { contactId: 9, leadId: 10, leadUrl: "https://spb.kommo.com/leads/detail/10" },
  recommendations: [rec(), rec({ builder_name: "Porter Davis" }), rec({ builder_name: "Boutique" })],
  research: { status: "ok", message: "3 buildings shortlisted", record_id: "r2", shortlist_count: 12, widened: "" },
  ...over,
});

const { api } = sandbox();

// ------------------------------------------------------------------ URL / contact safety
console.log("\n--- only http(s) becomes a link ---");
check("https kept", api.hlSafeUrl("https://meet.google.com/abc") === "https://meet.google.com/abc");
check("http kept", api.hlSafeUrl("http://example.com/x") === "http://example.com/x");
for (const bad of ["javascript:alert(1)", "  JAVASCRIPT:alert(1)", "java\tscript:alert(1)",
                   "java\nscript:alert(1)", "data:text/html,<b>x</b>", "vbscript:msgbox",
                   "/api/hot_leads", "//evil.example.com", "mailto:a@b.com", "file:///C:/x",
                   "", "   ", null, undefined, 42, { url: "https://x.y" }]) {
  check("refused: " + JSON.stringify(bad), api.hlSafeUrl(bad) === "", JSON.stringify(api.hlSafeUrl(bad)));
}
check("a quote in an https URL cannot close the attribute",
  !api.hlSafeUrl('https://x.example/a"onmouseover="b').includes('"'));

check("tel: digits and a leading + only", api.hlTelHref("+61 412 345 678") === "tel:+61412345678",
  api.hlTelHref("+61 412 345 678"));
check("tel: markup in the number is dropped", api.hlTelHref("0412<script>345678") === "tel:0412345678");
check("tel: no link for a non-number", api.hlTelHref("call me maybe") === "");
check("tel: javascript: yields no link", api.hlTelHref("javascript:alert(1)") === "");
check("tel: null", api.hlTelHref(null) === "");

check("mailto: plain address", api.hlMailHref("jo.citizen@example.com") === "mailto:jo.citizen@example.com");
check("mailto: O'Brien is an address", api.hlMailHref("sean.o'brien@example.com.au") !== "");
check("mailto: attribute breakout refused", api.hlMailHref('x@y.com" onmouseover="alert(1)') === "");
check("mailto: javascript refused", api.hlMailHref("javascript:alert(1)//@x.com") === "");
check("mailto: header injection refused", api.hlMailHref("a?cc=boss@x.com") === "");
check("mailto: not an email", api.hlMailHref("no email given") === "");
check("mailto: null", api.hlMailHref(null) === "");

// ------------------------------------------------------------------ money and time
console.log("\n--- money and Melbourne time ---");
check("money $650,200", api.hlMoney(650200) === "$650,200", api.hlMoney(650200));
check("money rounds", api.hlMoney("650200.4") === "$650,200");
check("money null -> blank", api.hlMoney(null) === "");
check("money 0 -> blank", api.hlMoney(0) === "");
check("money junk -> blank", api.hlMoney("about six hundred") === "");

const WEDS = api.hlWhen("2026-09-16T04:30:00Z");
check("UTC input shown in Melbourne time (AEST)", /^Wed 16 Sept?, 2:30 pm$/.test(WEDS), WEDS);
const DST = api.hlWhen("2026-12-01T00:00:00Z");
check("daylight saving applied (AEDT)", /^Tue 1 Dec, 11:00 am$/.test(DST), DST);
const OFFS = api.hlWhen("2026-09-18T10:00:00+10:00");
check("an offset timestamp stays put", /^Fri 18 Sept?, 10:00 am$/.test(OFFS), OFFS);
check("unparseable time -> blank, not a guess", api.hlWhen("next tuesday") === "");
check("null time -> blank", api.hlWhen(null) === "");

// ------------------------------------------------------------------ the hostile record
console.log("\n--- a hostile record renders as text ---");
let out = "";
throwsNot("hostile record renders", () => { out = api.hlLeadCard(hostile(), 0); });
auditMarkup("hostile card: markup is only the renderer's own", out);
check("no raw <script anywhere", !/<script/i.test(out));
check("no <img / <svg / <u / <i / <b from the data", !/<(img|svg|u|i|b)[\s>]/i.test(out));
check("no javascript: or data: href", !/href="\s*(javascript|data|vbscript)/i.test(out));
check("the name is still SHOWN, as text", out.includes(EVIL_TEXT));
check("the grade reason is shown as text", out.includes("&lt;img src=x onerror=&quot;alert(3)&quot;&gt;"));
check("an unusable email is shown but not linked", !out.includes("mailto:") && out.includes("x@y.com&quot; onmouseover"));
check("the phone becomes a clean tel: link", out.includes('href="tel:+61412345678"'));
check("unsafe source link is not a link", !/>View source<\/a>/.test(out));
check("unsafe Kommo link is not a link", !/>Open in Kommo<\/a>/.test(out));
check("unsafe recording link is not a link", !/>Call recording<\/a>/.test(out));
check("unsafe Meet link is not a link", !/>Google Meet link<\/a>/.test(out));
check("the booking still shows, in Melbourne time", /Discovery call booked:<\/strong> Fri 18 Sept?, 10:00 am Melbourne time/.test(out));
check("an automatic booking says to confirm it", out.includes("confirm with the customer"));
check("realistic total price preferred", out.includes("$650,200") && !out.includes("$600,000"));
check("bed/bath/car nulls are '-', never invented", out.includes("4 / - / -"));
check("land m2 shown when present", /Land 350 m&sup2;/.test(out));
check("house m2 absent when null", !/House [\d-]+ m&sup2;/.test(out));
check("score /100", out.includes("87.8 / 100"));
check("widened note shown when there are results", /widened/i.test(out) && out.includes(EVIL_TEXT));

// ------------------------------------------------------------------ a clean record
console.log("\n--- a clean record links what it should ---");
out = api.hlLeadCard(clean(), 3);
auditMarkup("clean card: markup is only the renderer's own", out);
check("mailto link", out.includes('href="mailto:jo.citizen@example.com"'));
check("tel link", out.includes('href="tel:+61412345678"'));
check("Google Meet link", /<a href="https:\/\/meet\.google\.com\/abc-defg-hij" target="_blank" rel="noopener noreferrer">Google Meet link<\/a>/.test(out));
check("Kommo link", /href="https:\/\/spb\.kommo\.com\/leads\/detail\/10"[^>]*>Open in Kommo<\/a>/.test(out));
check("recording link", />Call recording<\/a>/.test(out));
check("View source link, & escaped in the href", out.includes('href="https://builder.example.com/stock.pdf?row=24&amp;v=2"') && />View source<\/a>/.test(out));
check("a booking from the call has no 'confirm' note", !out.includes("confirm with the customer"));
check("booking in AEDT", /Tue 1 Dec, 11:00 am Melbourne time/.test(out));
check("HOT pill", /class="pill pill-hot">Hot</.test(out));
check("call time shown", /Called Tue 15 Sept?, 10:10 am/.test(out), (out.match(/Called[^<]*/) || [""])[0]);
check("advertised price used when no realistic total", out.includes("$540,000"));
check("car spaces 0 is shown as 0, not '-'", out.includes("4 / 2 / 0"));
check("brief chips: budget", out.includes("Budget up to $700,000"));
check("brief chips: deposit of 0 is stated, not dropped", out.includes("Deposit $0"));
check("brief chips: suburbs, bedrooms, type, timeframe, finance",
  ["Tarneit", "4+ bed", "House &amp; Land", "3 months", "pre-approved"].every(s => out.includes(s)));
check("re-run button wired to this card's index", out.includes('onclick="rerunHotLead(3)"') && out.includes('id="hlRerun-3"'));
check("three recommendation cards", (out.match(/class="hl-rec"/g) || []).length === 3);

// ------------------------------------------------------------------ nulls and gaps
console.log("\n--- missing fields ---");
throwsNot("an empty object renders", () => { out = api.hlLeadCard({}, 0); });
auditMarkup("empty card markup", out);
check("name fallback", out.includes("Name not captured"));
check("no booking -> follow up", out.includes("No discovery call booked"));
check("no recommendations -> says so", out.includes("No buildings were recommended"));
check("no email / phone stated", out.includes("No email captured") && out.includes("No phone captured"));
check("no brief -> re-run disabled", /id="hlRerun-0"[^>]*disabled/.test(out));
throwsNot("null lead renders", () => api.hlLeadCard(null, 0));
throwsNot("wrong-typed fields render", () => api.hlLeadCard({ customer: "x", brief: "y",
  recommendations: "z", research: 5, appointment: "soon", kommo: [] }, 0));
check("an appointment with no parseable start does not invent a time",
  api.hlAppointment({ start: "whenever", meetingUrl: "", source: "call" }).includes("time not recorded"));

out = api.hlRecCard({});
auditMarkup("all-null recommendation markup", out);
check("all-null rec: builder fallback", out.includes("Builder not stated"));
check("all-null rec: price not stated", out.includes("Price not stated"));
check("all-null rec: - / - / -", out.includes("- / - / -"));
check("all-null rec: score -", out.includes("- / 100"));
check("all-null rec: no m2, no source link", !out.includes("m&sup2;") && !out.includes("View source"));

out = api.hlRecsBlock([], { status: "empty", message: "Nothing under $500,000 in Tarneit", widened: "radius 10 km to 25 km" });
check("empty recs show research.message", out.includes("Nothing under $500,000 in Tarneit"));
check("empty recs show what was widened", out.includes("radius 10 km to 25 km"));
out = api.hlRecsBlock(undefined, undefined);
check("no recs, no research -> default line", out.includes("No buildings were recommended"));
out = api.hlRecsBlock([rec(), rec(), rec(), rec(), rec()], {});
check("never more than 3 buildings", (out.match(/class="hl-rec"/g) || []).length === 3);

out = api.hlRenderLeads([]);
check("empty list explains when a lead appears", out.includes("No hot leads yet") && out.includes("Clare finishes a call"));
out = api.hlRenderLeads(undefined);
check("undefined list -> empty state", out.includes("No hot leads yet"));

const sorted = api.hlSortNewest([
  { callId: "old", grade: "hot", createdAt: "2026-09-01T00:00:00Z" },
  null,
  { callId: "warm", grade: "warm", createdAt: "2026-09-20T00:00:00Z" },
  { callId: "nodate", grade: "hot" },
  { callId: "new", grade: "hot", createdAt: "2026-09-14T00:00:00Z" },
]).map(l => l.callId).join(",");
check("newest first, non-hot and junk dropped, undated last", sorted === "new,old,nodate", sorted);
check("sort of a non-array -> []", Array.isArray(api.hlSortNewest({ leads: 1 })) && api.hlSortNewest({ leads: 1 }).length === 0);

out = api.hlUnavailable(503, "SPB_SERVICE_KEY is not set on the dashboard project <b>x</b>");
auditMarkup("unavailable notice markup", out);
check("unavailable notice carries the server's message, escaped",
  out.includes("SPB_SERVICE_KEY is not set on the dashboard project &lt;b&gt;x&lt;/b&gt;"));

// ------------------------------------------------------------------ loader
console.log("\n--- loadHotLeads ---");
{
  const s = sandbox();
  const content = s.el("hotLeadsContent"), badge = s.el("hotLeadsBadge"), btn = s.el("hotLeadsRefresh");
  s.respond(async () => reply(401, { error: "auth_required", login: "/login" }));
  await s.api.loadHotLeads();
  check("401 -> sessionExpired()", s.calls.expired === 1, "expired " + s.calls.expired);
  check("401 -> no leads rendered", !content.innerHTML.includes("hl-card"));
  check("fetches /api/hot_leads with cache no-store",
    s.calls.fetch[0] && s.calls.fetch[0].url === "/api/hot_leads" && s.calls.fetch[0].opts
    && s.calls.fetch[0].opts.cache === "no-store");
  check("refresh button re-enabled after 401", btn.disabled === false);

  s.respond(async () => reply(503, { status: "error", message: "SPB_SERVICE_KEY is not set on the dashboard project" }));
  await s.api.loadHotLeads();
  check("after a 401 the next open tries again", s.calls.fetch.length === 2);
  check("503 -> the message is shown", content.innerHTML.includes("SPB_SERVICE_KEY is not set on the dashboard project"));
  check("503 -> badge says unavailable", /unavailable/i.test(badge.textContent), badge.textContent);
  auditMarkup("503 notice markup", content.innerHTML);

  s.respond(async () => reply(502, new Error("Unexpected token < in JSON")));
  await s.api.loadHotLeads(true);
  check("non-JSON error body -> status stated", content.innerHTML.includes("502"));

  s.respond(async () => { throw new TypeError("Failed to fetch"); });
  await s.api.loadHotLeads(true);
  check("network failure -> calm notice", /press Refresh/i.test(content.innerHTML));
  check("refresh re-enabled after a network failure", btn.disabled === false);

  const n = s.calls.fetch.length;
  let sawDisabled = null;
  s.respond(async () => {
    sawDisabled = btn.disabled;
    return reply(200, { status: "success", count: 2, leads: [
      clean({ callId: "a", createdAt: "2026-09-10T00:00:00Z", customer: { name: "Older Lead", email: "", phone: "" } }),
      hostile(),
    ] });
  });
  await s.api.loadHotLeads();
  check("after a network failure the next open tries again", s.calls.fetch.length === n + 1);
  check("refresh disabled while loading", sawDisabled === true);
  check("200 -> badge counts leads", badge.textContent === "2 hot leads", badge.textContent);
  check("200 -> newest first", content.innerHTML.indexOf(EVIL_TEXT) < content.innerHTML.indexOf("Older Lead")
    && content.innerHTML.indexOf(EVIL_TEXT) >= 0);
  auditMarkup("rendered list markup", content.innerHTML);
  check("200 -> no raw <script in the page", !/<script/i.test(content.innerHTML));

  await s.api.loadHotLeads();
  await s.api.loadHotLeads();
  check("loaded once per page view", s.calls.fetch.length === n + 1, "fetches " + s.calls.fetch.length);
  await s.api.loadHotLeads(true);
  check("Refresh loads again", s.calls.fetch.length === n + 2);

  s.respond(async () => reply(200, { status: "success", count: 0, leads: [] }));
  await s.api.loadHotLeads(true);
  check("200 with no leads -> empty state", content.innerHTML.includes("No hot leads yet"));
  check("badge 0 hot leads", badge.textContent === "0 hot leads", badge.textContent);
  check("every fetch went to a dashboard endpoint", s.calls.fetch.every(c => ALLOWED_FETCH.has(c.url)));
}

// ------------------------------------------------------------------ re-run
console.log("\n--- Re-run with today's stock ---");
{
  const s = sandbox();
  s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  const lead = clean();
  s.respond(async () => reply(200, { status: "success", count: 2, leads: [lead, clean({ callId: "nobrief", brief: null, createdAt: "2026-01-01T00:00:00Z" })] }));
  await s.api.loadHotLeads();
  const btn = s.el("hlRerun-0"), msg = s.el("hlMsg-0"), box = s.el("hlRecs-0");
  btn.textContent = "Re-run with today's stock";
  box.innerHTML = "SAVED";

  let seen = null;
  s.respond(async (url, opts) => {
    seen = { url, opts, disabled: btn.disabled };
    return reply(200, { status: "success", shortlist_count: 5, research_record_id: "x",
      shortlist: [rec({ builder_name: EVIL, source_link: { url: "javascript:alert(7)", label: "x" } }),
                  rec({ builder_name: "Two" }), rec({ builder_name: "Three" }),
                  rec({ builder_name: "Four" }), rec({ builder_name: "Five" })] });
  });
  await s.api.rerunHotLead(0);
  check("POSTs /api/research", seen && seen.url === "/api/research" && seen.opts.method === "POST");
  check("body is exactly {client_brief: lead.brief}",
    seen && JSON.stringify(JSON.parse(seen.opts.body)) === JSON.stringify({ client_brief: lead.brief }));
  check("button disabled while running", seen && seen.disabled === true);
  check("button re-enabled after", btn.disabled === false && btn.textContent === "Re-run with today's stock");
  check("live results replace the saved ones", !box.innerHTML.includes("SAVED"));
  check("labelled live and not saved", /Live results &mdash; not saved/.test(box.innerHTML));
  check("top 3 of the live shortlist", (box.innerHTML.match(/class="hl-rec"/g) || []).length === 3
    && box.innerHTML.includes("Three") && !box.innerHTML.includes("Four"));
  check("says how many the live search found", box.innerHTML.includes("of 5"));
  auditMarkup("live results markup", box.innerHTML);
  check("live results: no raw <script", !/<script/i.test(box.innerHTML));

  box.innerHTML = "SAVED";
  s.respond(async () => reply(500, { status: "error", message: "pipeline <b>broke</b>" }));
  await s.api.rerunHotLead(0);
  check("error -> saved results left alone", box.innerHTML === "SAVED");
  check("error -> shown inline on the card", msg.textContent.includes("pipeline <b>broke</b>") && /err/.test(msg.className));
  check("error message set as text, not markup", msg.innerHTML === "");
  check("button re-enabled after an error", btn.disabled === false);

  s.respond(async () => { throw new TypeError("Failed to fetch"); });
  await s.api.rerunHotLead(0);
  check("network error -> shown inline", /Failed to fetch/.test(msg.textContent));

  const before = s.calls.expired;
  s.respond(async () => reply(401, { error: "auth_required" }));
  await s.api.rerunHotLead(0);
  check("401 -> sessionExpired()", s.calls.expired === before + 1);
  check("401 -> button re-enabled", btn.disabled === false);

  const n = s.calls.fetch.length;
  s.el("hlRerun-1"); s.el("hlMsg-1"); s.el("hlRecs-1");
  await s.api.rerunHotLead(1);
  check("a lead with no brief never calls the research", s.calls.fetch.length === n);
  await s.api.rerunHotLead(99);
  check("an index with no lead does nothing", s.calls.fetch.length === n);
  check("every fetch went to a dashboard endpoint", s.calls.fetch.every(c => ALLOWED_FETCH.has(c.url)));
}

// ------------------------------------------------------------------ the page itself
console.log("\n--- the tab is wired into the page ---");
{
  const nav = (html.match(/<nav class="nav-tabs">([\s\S]*?)<\/nav>/) || [, ""])[1];
  const buttons = [...nav.matchAll(/<button[^>]*onclick="([^"]*)"[^>]*>/g)].map(m => m[1]);
  const research = buttons.findIndex(o => o.includes("'researchTab'"));
  const hot = buttons.findIndex(o => o.includes("'hotLeadsTab'"));
  check("nav button directly after Research & Scoring", research >= 0 && hot === research + 1,
    JSON.stringify(buttons));
  check("nav button calls showTab then loadHotLeads()",
    hot >= 0 && /^showTab\('hotLeadsTab', this\); loadHotLeads\(\);$/.test(buttons[hot]), buttons[hot]);
  check("nav label reads Hot Leads (Agent 1)",
    /Hot Leads<span class="nav-tab-label-full"> \(Agent 1\)<\/span><\/button>/.test(nav));
  const pane = (html.match(/<div id="hotLeadsTab" class="view-pane">([\s\S]*?)\n<\/div>/) || [, ""])[1];
  check("pane exists", pane.length > 0);
  check("pane header text", pane.includes("Hot leads from Clare's calls"));
  for (const id of ["hotLeadsBadge", "hotLeadsRefresh", "hotLeadsContent"])
    check("pane has #" + id, pane.includes('id="' + id + '"'));
  check("Refresh forces a reload", /id="hotLeadsRefresh"[^>]*onclick="loadHotLeads\(true\)"/.test(pane));
}

console.log("\n" + (fail === 0 ? "ALL " + pass + " CHECKS PASSED" : pass + " passed, " + fail + " FAILED"));
process.exit(fail === 0 ? 0 : 1);
