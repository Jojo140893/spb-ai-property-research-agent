// Exercises the Clare's Leads (Agent 1) tab -- "Hot Leads" until 15 Sep, when it widened to
// every hot caller and every warm or nurture caller who gave a state: the code between the HOT-LEADS-BEGIN and
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
  "hlSortNewest", "hlUnavailable", "loadHotLeads", "rerunHotLead", "hlGrade", "hlSetFilter",
  "hlMergeLeads", "hlCoverNote", "hlLocationNote"];

// The tab's two reads -- every grade, and hot callers on their own -- each with and without
// include_test=1, and the research. The bare "/api/hot_leads" the hot-only tab used is NOT
// here: it got the hook's default 50 of every grade, which since 15 Sep can leave hot
// callers out, so a tab that went back to it fails the run.
const ALLOWED_FETCH = new Set([
  "/api/hot_leads?limit=200", "/api/hot_leads?limit=200&include_test=1",
  "/api/hot_leads?limit=200&grade=hot", "/api/hot_leads?limit=200&grade=hot&include_test=1",
  "/api/research"]);

// search: the page's own ?query, as location.search. Left undefined, the sandbox has no
// location at all, which the loader must survive.
function sandbox(search) {
  const els = new Map();
  const calls = { fetch: [], expired: 0 };
  let responder = async () => { throw new Error("no fetch responder set"); };
  const ctx = vm.createContext({
    URL, URLSearchParams, console,
    ...(search === undefined ? {} : { location: { search } }),
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
    const o = { id, innerHTML: "", textContent: "", className: "", disabled: false, hidden: false,
                attrs: {}, setAttribute(k, v) { this.attrs[k] = String(v); } };
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
const ATTRS = new Set(["class", "id", "href", "target", "rel", "title", "style", "onclick", "type", "disabled",
  "hidden", "aria-pressed"]);
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
      if (attr === "onclick" && !/^(rerunHotLead\(\d+\)|loadHotLeads\(true\)|hlSetFilter\('(all|hot|warm|nurture)'\))$/.test(val))
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
check("auditor flags a filter onclick outside the four grades",
  markupProblems(`<button onclick="hlSetFilter('closed')">x</button>`).length > 0);
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
  research: { status: "ok", message: EVIL, record_id: "r1", shortlist_count: 1, widened: EVIL,
              location: [{ said: EVIL, used: ["Tarneit", EVIL], method: "groq" }] },
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

// ------------------------------------------------------------------ the address line
// Seen live on 14 Sep: "Lot 413, Mambourin Green, Wyndham Vale VIC · Wyndham Vale VIC".
console.log("\n--- the address line names the suburb once ---");
const subLine = rec => {
  const m = api.hlRecCard(rec).match(/<div class="hl-rec-sub">([\s\S]*?)<\/div>/);
  return m ? m[1] : "";
};
const REPEAT = subLine({ lot_address: "Lot 413, Mambourin Green, Wyndham Vale VIC", suburb: "Wyndham Vale", state: "VIC" });
check("suburb + state already in the address are not repeated",
  REPEAT === "Lot 413, Mambourin Green, Wyndham Vale VIC", REPEAT);
const ESTATE = subLine({ lot_address: "Lot 406, Mambourin Green", suburb: "Mambourin", state: "VIC" });
check("a suburb word inside an estate name still gets the suburb",
  ESTATE === "Lot 406, Mambourin Green &middot; Mambourin VIC", ESTATE);
const STREET = subLine({ lot_address: "1815 Torenia Dr, Verdant Hill", suburb: "Tarneit", state: "VIC" });
check("a street address gets its suburb and state",
  STREET === "1815 Torenia Dr, Verdant Hill &middot; Tarneit VIC", STREET);
const BARE = subLine({ lot_address: "Lot 5, Lara", suburb: "Lara", state: "VIC" });
check("suburb alone in the address is enough", BARE === "Lot 5, Lara", BARE);

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
check("advertised price is the price, even beside a realistic total",
  out.includes("$600,000") && !out.includes("$650,200") && /\$600,000<\/span> <span[^>]*>advertised</.test(out));
check("bed/bath/car nulls are '-', never invented", out.includes("4 / - / -"));
check("land m2 shown when present", /Land 350 m&sup2;/.test(out));
check("house m2 absent when null", !/House [\d-]+ m&sup2;/.test(out));
check("score /100", out.includes("87.8 / 100"));
check("widened note shown when there are results", /widened/i.test(out) && out.includes(EVIL_TEXT));
check("a hostile location reading is shown as text",
  out.includes(`<div class="hl-loc">Location read as: ${EVIL_TEXT} -&gt; Tarneit, ${EVIL_TEXT}</div>`));

// ------------------------------------------------------------------ where the search looked
// 15 Sep: the research reads a location Vapi mistranscribed ("Tarn eat") or an area ("western
// suburbs of Melbourne") through Groq, keeps only names the suburb index confirms, and the hook
// copies those phrases into research.location. The card says which suburbs were searched.
console.log("\n--- Location read as ---");
const loc = (...entries) => ({ status: "ok", location: entries });
out = api.hlLocationNote(loc({ said: "Tarn eat", used: ["Tarneit"], method: "groq" }));
check("one groq phrase -> one muted line", out === '<div class="hl-loc">Location read as: Tarn eat -&gt; Tarneit</div>', out);
out = api.hlLocationNote(loc(
  { said: "western suburbs of Melbourne", used: ["Tarneit", " Truganina ", "", 7, null, "Werribee"], method: "groq" },
  { said: "Tarneit", used: ["Tarneit"], method: "exact" },
  { said: "Red Bank Plains", used: ["Redbank Plains"], method: "spoken" },
  { said: "Newpark", used: ["Newpark"], method: "unresolved" },
  { said: "Wyndum Vale", used: ["Wyndham Vale"], method: "groq" },
  { said: "Cragieburn", used: ["Craigieburn"], method: "fuzzy" }));
check("groq and fuzzy phrases share the one line; exact, spoken and unresolved are left out",
  out === '<div class="hl-loc">Location read as: western suburbs of Melbourne -&gt; Tarneit, Truganina, Werribee; Wyndum Vale -&gt; Wyndham Vale; Cragieburn -&gt; Craigieburn (matched by spelling)</div>', out);
auditMarkup("location line markup", out);
// Live, 15 Sep 2026: Groq answered "Wyndum Vale" unknown, and the resolver now reads such a
// phrase as the one locality spelt that closely (method "fuzzy"). That is a reading too.
// Review, 15 Sep 2026: it looked exactly like a Groq reading here while the Kommo note marks
// it "(matched by spelling)" -- the card now says so as well, and a Groq reading does not.
out = api.hlLocationNote(loc({ said: "Wyndum Vale", used: ["Wyndham Vale"], method: "fuzzy" }));
check("a fuzzy phrase alone -> the line, marked as a spelling match", out === '<div class="hl-loc">Location read as: Wyndum Vale -&gt; Wyndham Vale (matched by spelling)</div>', out);
out = api.hlLocationNote(loc({ said: "Wyndum Vale", used: ["Wyndham Vale"], method: "groq" }));
check("a groq reading carries no spelling mark", !out.includes("matched by spelling") && out.includes("Wyndum Vale -&gt; Wyndham Vale</div>"), out);
out = api.hlLocationNote(loc({ said: "<script>alert(1)</script>", used: ["<script>alert(1)</script>"], method: "fuzzy" }));
check("a hostile fuzzy reading is shown as text", out === `<div class="hl-loc">Location read as: ${EVIL_TEXT} -&gt; ${EVIL_TEXT} (matched by spelling)</div>`, out);
for (const [label, research] of [
  ["no research", undefined], ["null research", null], ["research a string", "x"],
  ["no location", { status: "ok" }], ["location empty", loc()], ["location not an array", { location: { said: "a", used: ["b"], method: "groq" } }],
  ["only unresolved", loc({ said: "Tarn eat", used: ["Tarn eat"], method: "unresolved" })],
  ["method in capitals", loc({ said: "Tarn eat", used: ["Tarneit"], method: "GROQ" })],
  ["fuzzy in capitals", loc({ said: "Wyndum Vale", used: ["Wyndham Vale"], method: "FUZZY" })],
  ["a method that only contains fuzzy", loc({ said: "Wyndum Vale", used: ["Wyndham Vale"], method: "fuzzy,groq" })],
  ["junk entries", loc(null, "groq", 5, [], { method: "groq" }, { said: "x", method: "groq" },
                       { said: "x", used: "Tarneit", method: "groq" }, { said: "  ", used: ["Tarneit"], method: "groq" },
                       { said: "x", used: ["", null, 3], method: "groq" })],
]) {
  let got = null;
  throwsNot(label + " renders", () => { got = api.hlLocationNote(research); });
  check(label + " -> no line", got === "", JSON.stringify(got));
}
out = api.hlLeadCard(clean(), 0);
check("a card with no location reading has no line", !out.includes("Location read as") && !out.includes("hl-loc"));
out = api.hlLeadCard(clean({ research: { ...clean().research, location: [{ said: "Tarn eat", used: ["Tarneit"], method: "groq" }] } }), 4);
auditMarkup("card with a location line: markup", out);
check("the card shows the line once", (out.match(/Location read as:/g) || []).length === 1 && out.includes("Tarn eat -&gt; Tarneit"));
check("the line sits with the brief, outside the box a Re-run replaces",
  out.indexOf("Location read as") > out.indexOf('class="hl-chips"') && out.indexOf("Location read as") < out.indexOf('id="hlRecs-4"'));

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
check("advertised price used when no realistic total, labelled advertised",
  /\$540,000<\/span> <span[^>]*>advertised</.test(out) && !out.includes("estimated total"));
check("car spaces 0 is shown as 0, not '-'", out.includes("4 / 2 / 0"));
check("brief chips: budget", out.includes("Budget up to $700,000"));
check("brief chips: deposit of 0 is stated, not dropped", out.includes("Deposit $0"));
check("brief chips: suburbs, bedrooms, type, timeframe, finance",
  ["Tarneit", "4+ bed", "House &amp; Land", "3 months", "pre-approved"].every(s => out.includes(s)));
check("re-run button wired to this card's index", out.includes('onclick="rerunHotLead(3)"') && out.includes('id="hlRerun-3"'));
check("three recommendation cards", (out.match(/class="hl-rec"/g) || []).length === 3);
check("Why hot: on a hot card", out.includes("<strong>Why hot:</strong>"));

// ------------------------------------------------------------------ every grade
// 15 Sep (Ahsan): Agent 2 runs for every caller who gives a state, so warm and nurture
// callers are stored and listed too. Booking stayed hot-only.
console.log("\n--- warm and nurture callers have their own cards ---");
out = api.hlLeadCard(clean({ grade: "warm", appointment: null, gradeReason: "Within 6 months, no pre-approval." }), 1);
auditMarkup("warm card markup", out);
check("WARM pill, and no HOT pill", /class="pill pill-warm">Warm</.test(out) && !out.includes("pill-hot"));
check("Why warm:", out.includes("<strong>Why warm:</strong> Within 6 months") && !out.includes("Why hot"));
check("warm with no booking: only hot callers are booked automatically",
  out.includes("No discovery call (only hot callers are booked automatically)") && !out.includes("follow up"));
check("warm card keeps its buildings and its Re-run",
  (out.match(/class="hl-rec"/g) || []).length === 3 && out.includes('onclick="rerunHotLead(1)"'));

out = api.hlLeadCard(clean({ grade: "nurture", appointment: null, gradeReason: "No pre-approval, investor." }), 2);
auditMarkup("nurture card markup", out);
check("NURTURE pill", /class="pill pill-nurture">Nurture</.test(out) && !/pill-(hot|warm)"/.test(out));
check("Why nurture:", out.includes("<strong>Why nurture:</strong> No pre-approval"));
check("nurture with no booking: only hot callers are booked automatically",
  out.includes("only hot callers are booked automatically") && !out.includes("follow up"));

out = api.hlLeadCard(clean({ grade: "warm" }), 0);
check("a warm caller who did book still shows the booking",
  /Discovery call booked:<\/strong> Tue 1 Dec/.test(out) && !out.includes("only hot callers"));
out = api.hlLeadCard(clean({ appointment: null }), 0);
check("hot with no booking still says follow up",
  out.includes("No discovery call booked &mdash; follow up") && !out.includes("only hot callers"));
out = api.hlLeadCard(clean({ grade: undefined }), 0);
check("a record with no grade (stored while the tab was hot-only) is hot",
  /class="pill pill-hot">Hot</.test(out) && out.includes("Why hot:"));

for (const g of ["closed", "HOT", 'hot" onclick="alert(1)', EVIL, 7, null]) {
  const label = "grade " + JSON.stringify(g);
  throwsNot(label + " renders", () => { out = api.hlLeadCard(clean({ grade: g }), 0); });
  auditMarkup(label + ": markup is only the renderer's own", out);
  check(label + ": no grade pill, and the reason is not called hot",
    !/pill-(hot|warm|nurture)/.test(out) && !out.includes("Why hot") && out.includes("<strong>Grade reason:</strong>"));
  check(label + ": hlGrade is ''", api.hlGrade({ grade: g }) === "");
}

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

// The realistic total is only a fallback, and says it is an estimate.
out = api.hlRecCard(rec({ advertised_package_price: null, realistic_total_price: 712000 }));
auditMarkup("estimate-only recommendation markup", out);
check("no advertised price -> realistic total shown, labelled an estimate",
  /\$712,000<\/span> <span[^>]*>estimated total \(no advertised price\)</.test(out));
check("an estimate is never labelled advertised", !/>advertised</.test(out));
out = api.hlRecCard(rec({ advertised_package_price: 0, realistic_total_price: 712000 }));
check("an advertised price of 0 is no price -> estimate", out.includes("$712,000") && out.includes("estimated total"));
out = api.hlRecCard(rec({ advertised_package_price: 540000, realistic_total_price: 555000 }));
check("one price per card: advertised shown, realistic total not beside it",
  out.includes("$540,000") && !out.includes("$555,000") && (out.match(/\$\d/g) || []).length === 1);

out = api.hlRecsBlock([], { status: "empty", message: "Nothing under $500,000 in Tarneit", widened: "radius 10 km to 25 km" });
check("empty recs show research.message", out.includes("Nothing under $500,000 in Tarneit"));
check("empty recs show what was widened", out.includes("radius 10 km to 25 km"));
out = api.hlRecsBlock(undefined, undefined);
check("no recs, no research -> default line", out.includes("No buildings were recommended"));
out = api.hlRecsBlock([rec(), rec(), rec(), rec(), rec()], {});
check("never more than 3 buildings", (out.match(/class="hl-rec"/g) || []).length === 3);

out = api.hlRenderLeads([]);
// A hot caller is stored with or without a state (vapi-kommo-hook api/webhook.js keeps the
// record for the booking), so the empty state must not say every listed caller gave one.
check("empty list explains when a lead appears: every hot caller, warm or nurture with a state",
  out.includes("No leads yet") && /A hot caller appears\s+here after Clare finishes their call/.test(out)
  && /warm or nurture caller who gave\s+a state/.test(out)
  && out.includes("Hot callers also get a") && !out.includes("hl-filters") && !out.includes("hlCover"));
check("empty list does not claim every caller gave a state",
  !/A caller appears here[\s\S]*where they gave a state/.test(out));
out = api.hlRenderLeads(undefined);
check("undefined list -> empty state", out.includes("No leads yet"));

const sorted = api.hlSortNewest([
  { callId: "old", grade: "hot", createdAt: "2026-09-01T00:00:00Z" },
  null,
  { callId: "warm", grade: "warm", createdAt: "2026-09-20T00:00:00Z" },
  { callId: "closed", grade: "closed", createdAt: "2026-09-21T00:00:00Z" },
  { callId: "junk", grade: "<b>hot</b>", createdAt: "2026-09-22T00:00:00Z" },
  { callId: "shouty", grade: "HOT", createdAt: "2026-09-23T00:00:00Z" },
  { callId: "nurture", grade: "nurture", createdAt: "2026-09-02T00:00:00Z" },
  { callId: "nodate", grade: "hot" },
  { callId: "legacy", createdAt: "2026-08-01T00:00:00Z" },
  { callId: "new", grade: "hot", createdAt: "2026-09-14T00:00:00Z" },
]).map(l => l.callId).join(",");
check("newest first, warm and nurture kept, closed and junk dropped, undated last",
  sorted === "warm,new,nurture,old,legacy,nodate", sorted);
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
  check("fetches /api/hot_leads?limit=200 with cache no-store",
    s.calls.fetch[0] && s.calls.fetch[0].url === "/api/hot_leads?limit=200" && s.calls.fetch[0].opts
    && s.calls.fetch[0].opts.cache === "no-store", s.calls.fetch[0] && s.calls.fetch[0].url);
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
  check("200 -> badge counts leads, and the hot ones", badge.textContent === "2 leads · 2 hot", badge.textContent);
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
  check("200 with no leads -> empty state", content.innerHTML.includes("No leads yet"));
  check("badge 0 leads", badge.textContent === "0 leads", badge.textContent);
  check("every fetch went to a dashboard endpoint", s.calls.fetch.every(c => ALLOWED_FETCH.has(c.url)));
}

// ------------------------------------------------------------------ test records
// The hook leaves test records out unless asked with include_test=1. Only a page opened with
// exactly one ?hl_test=1 asks; anything else must get the ordinary list.
console.log("\n--- test records only with ?hl_test=1 ---");
{
  const firstUrl = async search => {
    const s = sandbox(search);
    s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
    s.respond(async () => reply(200, { status: "success", count: 0, leads: [] }));
    await s.api.loadHotLeads();
    return s.calls.fetch[0];
  };
  for (const search of ["?hl_test=1", "?tab=hot&hl_test=1", "?hl_test=%31"]) {
    const c = await firstUrl(search);
    check(search + " -> include_test=1 sent, still no-store",
      c && c.url === "/api/hot_leads?limit=200&include_test=1" && c.opts && c.opts.cache === "no-store",
      c && c.url);
  }
  for (const search of [undefined, "", "?", "?hl_test=0", "?hl_test=true", "?hl_test=", "?hl_test=01",
                        "?hl_test=1&hl_test=0", "?hl_test=0&hl_test=1", "?HL_TEST=1",
                        "?include_test=1", "?hl_test= 1"]) {
    const c = await firstUrl(search);
    check(JSON.stringify(search ?? "no location") + " -> include_test not sent",
      c && c.url === "/api/hot_leads?limit=200", c && c.url);
  }
}

// ------------------------------------------------------------------ filters
// All / Hot / Warm / Nurture work over the list already loaded: no request, cards hidden
// rather than re-rendered, and every card keeps its index into HOT_LEADS for Re-run.
console.log("\n--- All / Hot / Warm / Nurture ---");
{
  const s = sandbox();
  const content = s.el("hotLeadsContent"), badge = s.el("hotLeadsBadge");
  s.el("hotLeadsRefresh");
  const who = (name, over) => clean({ customer: { name, email: "", phone: "" }, ...over });
  const warmBrief = { ...clean().brief, client_name: "Warm Caller", state: "QLD",
                      primary_suburbs: ["Red Bank Plains"], budget_max: 750000 };
  s.respond(async () => reply(200, { status: "success", count: 6, leads: [
    who("Nurture Two", { callId: "n2", grade: "nurture", appointment: null, createdAt: "2026-09-15T01:00:00Z" }),
    who("Hot One", { callId: "h1", grade: "hot", createdAt: "2026-09-15T05:00:00Z" }),
    who("Closed Caller", { callId: "c1", grade: "closed", createdAt: "2026-09-15T06:00:00Z" }),
    who("Warm Caller", { callId: "w1", grade: "warm", appointment: null, brief: warmBrief, createdAt: "2026-09-15T04:00:00Z" }),
    who("Nurture One", { callId: "n1", grade: "nurture", appointment: null, createdAt: "2026-09-15T03:00:00Z" }),
    who("Hot Two", { callId: "h2", grade: "hot", createdAt: "2026-09-15T02:00:00Z" }),
  ] }));
  await s.api.loadHotLeads();
  const html0 = content.innerHTML;
  // Newest first: 0 Hot One, 1 Warm Caller, 2 Nurture One, 3 Hot Two, 4 Nurture Two.
  check("a closed caller is not on the tab", s.api.leads().length === 5 && !html0.includes("Closed Caller"));
  check("badge: 5 leads · 2 hot", badge.textContent === "5 leads · 2 hot", badge.textContent);
  auditMarkup("filter bar and mixed list markup", html0);
  check("filter buttons carry their counts",
    ["All (5)", "Hot (2)", "Warm (1)", "Nurture (2)"].every(t => html0.includes(">" + t + "</button>")));
  check("All starts pressed",
    /class="btn-ghost hl-filter is-active" id="hlFilter-all" aria-pressed="true"/.test(html0)
    && (html0.match(/aria-pressed="true"/g) || []).length === 1);
  check("under All no card is hidden, and the empty note is", !/id="hlCard-\d+" hidden/.test(html0)
    && /id="hlFilterEmpty" hidden/.test(html0));
  check("each card has its grade pill", (html0.match(/pill pill-hot"/g) || []).length === 2
    && (html0.match(/pill pill-warm"/g) || []).length === 1 && (html0.match(/pill pill-nurture"/g) || []).length === 2);

  const cards = [0, 1, 2, 3, 4].map(i => s.el("hlCard-" + i));
  const fb = Object.fromEntries(["all", "hot", "warm", "nurture"].map(k => [k, s.el("hlFilter-" + k)]));
  const note = s.el("hlFilterEmpty");
  const visible = () => cards.map(c => (c.hidden ? "-" : "V")).join("");
  const pressed = key => fb[key].className.includes("is-active") && fb[key].attrs["aria-pressed"] === "true"
    && Object.keys(fb).filter(k => k !== key).every(k => !fb[k].className.includes("is-active")
                                                        && fb[k].attrs["aria-pressed"] === "false");
  const n = s.calls.fetch.length;

  s.api.hlSetFilter("warm");
  check("filter Warm hides every card but the warm one", visible() === "-V---", visible());
  check("filter Warm presses Warm, and only Warm", pressed("warm"));
  check("filter Warm: empty note stays hidden", note.hidden === true);
  s.api.hlSetFilter("hot");
  check("filter Hot shows the two hot cards", visible() === "V--V-", visible());
  s.api.hlSetFilter("nurture");
  check("filter Nurture shows the two nurture cards", visible() === "--V-V", visible());
  s.api.hlSetFilter("all");
  check("filter All shows them all again", visible() === "VVVVV" && pressed("all"), visible());
  for (const bad of ["closed", "HOT", "x'); alert(1); //", undefined]) {
    s.api.hlSetFilter("hot");
    s.api.hlSetFilter(bad);
    check("filter " + JSON.stringify(bad) + " -> All", visible() === "VVVVV" && pressed("all"), visible());
  }
  check("changing the filter asks the server nothing", s.calls.fetch.length === n);

  // Refresh keeps the filter, and a card keeps its index into the full list.
  s.api.hlSetFilter("warm");
  await s.api.loadHotLeads(true);
  const html1 = content.innerHTML;
  check("the filter is kept after Refresh",
    /id="hlFilter-warm" aria-pressed="true"/.test(html1) && /id="hlCard-0" hidden/.test(html1)
    && /id="hlCard-1">/.test(html1) && /id="hlCard-2" hidden/.test(html1), html1.slice(0, 400));
  auditMarkup("filtered list markup", html1);
  const idx = (html1.match(/Warm Caller[\s\S]*?onclick="rerunHotLead\((\d+)\)"/) || [])[1];
  s.el("hlRerun-" + idx); s.el("hlMsg-" + idx); s.el("hlRecs-" + idx);
  let posted = null;
  s.respond(async (url, opts) => { posted = JSON.parse(opts.body); return reply(200, { status: "success", shortlist: [] }); });
  await s.api.rerunHotLead(Number(idx));
  check("re-run from a filtered list sends that caller's brief",
    posted && JSON.stringify(posted) === JSON.stringify({ client_brief: warmBrief }), JSON.stringify(posted));

  // A grade with nobody in it says so.
  s.respond(async () => reply(200, { status: "success", count: 1, leads: [who("Only Hot", { grade: "hot" })] }));
  s.api.hlSetFilter("all");
  await s.api.loadHotLeads(true);
  check("badge: 1 lead · 1 hot", badge.textContent === "1 lead · 1 hot", badge.textContent);
  const only = s.el("hlCard-0"), note2 = s.el("hlFilterEmpty");
  s.api.hlSetFilter("nurture");
  check("a filter with no leads shows its empty note and hides the rest",
    note2.hidden === false && note2.textContent === "No nurture leads in this list." && only.hidden === true);
  s.api.hlSetFilter("hot");
  check("back to a filter with leads hides the note", note2.hidden === true && only.hidden === false);
  const rendered = api.hlRenderLeads([clean()], "warm");
  check("rendered straight into an empty filter, the note shows",
    /id="hlFilterEmpty">No warm leads in this list\.<\/div>/.test(rendered) && /id="hlCard-0" hidden/.test(rendered));
  check("every fetch went to a dashboard endpoint", s.calls.fetch.every(c => ALLOWED_FETCH.has(c.url)));
}

// ------------------------------------------------------------------ a busy store
// 15 Sep: warm and nurture callers share the hook's answer with hot ones. The hook sends at
// most `limit` records (50 when none is asked for, 200 at most), newest first, and applies
// ?grade= BEFORE that limit (vapi-kommo-hook lib/hot-leads.js). The stub below answers the
// same way, so a tab that asks with no limit, or never asks for hot callers on their own,
// loses the older hot callers here exactly as it would live.
console.log("\n--- a busy store: warm and nurture callers do not push hot callers off ---");
{
  const hookAnswer = (store, url) => {
    const q = new URL(url, "https://dashboard.invalid").searchParams;
    const raw = q.get("limit") || "";
    const limit = /^\d+$/.test(raw) ? Math.min(200, Math.max(1, parseInt(raw, 10))) : 50;
    const grade = ["hot", "warm", "nurture"].includes(q.get("grade")) ? q.get("grade") : null;
    const rows = store.filter(l => !grade || l.grade === grade).slice(0, limit);
    return reply(200, { status: "success", count: rows.length, leads: rows });
  };
  const at = i => new Date(Date.UTC(2026, 8, 15, 12, 0) - i * 60000).toISOString();
  const person = (id, grade, i) => clean({ callId: id, grade, createdAt: at(i),
    appointment: grade === "hot" ? clean().appointment : null,
    customer: { name: "Caller " + id, email: "", phone: "" } });
  // Newest first: nNurture nurture callers, then nHot hot callers older than all of them.
  const busy = (nNurture, nHot) => [
    ...Array.from({ length: nNurture }, (_, i) => person("n" + i, "nurture", i)),
    ...Array.from({ length: nHot }, (_, i) => person("h" + i, "hot", nNurture + i)),
  ];
  const run = async (store, respond, search) => {
    const s = sandbox(search);
    const content = s.el("hotLeadsContent"), badge = s.el("hotLeadsBadge");
    s.el("hotLeadsRefresh");
    s.respond(respond || (async url => hookAnswer(store, url)));
    await s.api.loadHotLeads();
    return { s, content, badge, urls: s.calls.fetch.map(c => c.url), ids: s.api.leads().map(l => l.callId) };
  };
  const ALL = "/api/hot_leads?limit=200", HOT = "/api/hot_leads?limit=200&grade=hot";
  const hotIds = n => Array.from({ length: n }, (_, i) => "h" + i);

  // 197 nurture callers after 10 hot ones: the first answer holds 3 of the hot callers.
  {
    const { s, content, badge, urls, ids } = await run(busy(197, 10));
    check("cut-off answer: the tab asks again for hot callers only",
      JSON.stringify(urls) === JSON.stringify([ALL, HOT]), JSON.stringify(urls));
    check("every hot caller in the store is on the tab",
      hotIds(10).every(id => ids.includes(id)), ids.filter(id => id.startsWith("h")).join(","));
    check("no caller listed twice (3 hot callers were in both answers)",
      new Set(ids).size === ids.length && ids.length === 207, String(ids.length));
    check("busy store: Hot (10)", content.innerHTML.includes(">Hot (10)</button>"));
    check("busy store badge: 207+ leads · 10 hot", badge.textContent === "207+ leads · 10 hot", badge.textContent);
    check("busy store: says only the newest 200 are listed, and hot callers were looked up on their own",
      /id="hlCover">Only the newest 200 callers are listed, so older warm and nurture callers are not shown here\. Hot callers were looked up on their own, so an older hot caller is still listed\.</.test(content.innerHTML)
      && !content.innerHTML.includes("hl-cover-warn"));
    auditMarkup("busy store list markup", content.innerHTML);
    const cards = ids.map((_, i) => s.el("hlCard-" + i));
    s.api.hlSetFilter("hot");
    const shown = cards.map((c, i) => (c.hidden ? null : ids[i])).filter(Boolean).sort();
    check("busy store: the Hot filter shows exactly the ten hot callers",
      JSON.stringify(shown) === JSON.stringify(hotIds(10).sort()), shown.join(","));
  }

  // Under the limit the first answer is the whole store: one request, nothing to say.
  {
    const { content, badge, urls, ids } = await run(busy(189, 10));
    check("199 records: one request, no second lookup", JSON.stringify(urls) === JSON.stringify([ALL]), JSON.stringify(urls));
    check("199 records: every hot caller listed, no cut-off note",
      hotIds(10).every(id => ids.includes(id)) && !content.innerHTML.includes("hlCover"));
    check("199 records badge: 199 leads · 10 hot", badge.textContent === "199 leads · 10 hot", badge.textContent);
  }
  {
    const { badge, urls } = await run(busy(190, 10));
    check("an answer of exactly 200 counts as cut off", JSON.stringify(urls) === JSON.stringify([ALL, HOT])
      && badge.textContent === "200+ leads · 10 hot", JSON.stringify(urls) + " " + badge.textContent);
  }

  // More hot callers than even the hot lookup sends.
  {
    const { content, badge, ids } = await run(busy(200, 250));
    check("hot lookup cut off too: its newest 200 are listed", ids.length === 400
      && hotIds(200).every(id => ids.includes(id)) && !ids.includes("h200"), String(ids.length));
    check("hot lookup cut off too: badge 400+ leads · 200+ hot", badge.textContent === "400+ leads · 200+ hot", badge.textContent);
    check("hot lookup cut off too: the note says the newest 200 hot callers are listed",
      content.innerHTML.includes("Hot callers were looked up on their own, so the newest 200 of them are listed."));
  }

  // The hot lookup fails: show what came back, and say a hot caller may be missing.
  {
    const store = busy(197, 10);
    const { content, badge, ids } = await run(store, async url => url === HOT
      ? reply(502, { status: "error", message: "hot-lead store answered HTTP 500 <b>x</b>" })
      : hookAnswer(store, url));
    check("hot lookup failed: the first answer is still listed", ids.length === 200);
    check("hot lookup failed: says a hot caller may be missing",
      /class="hl-notice hl-cover hl-cover-warn" id="hlCover"><strong>A hot caller may be missing from this list\.<\/strong>/.test(content.innerHTML)
      && content.innerHTML.includes("that lookup failed (hot-lead store answered HTTP 500 &lt;b&gt;x&lt;/b&gt;). Press Refresh to try again."));
    check("hot lookup failed: badge 200+ leads · 3+ hot", badge.textContent === "200+ leads · 3+ hot", badge.textContent);
    auditMarkup("hot lookup failed markup", content.innerHTML);
  }
  {
    const store = busy(197, 10);
    const { content } = await run(store, async url => {
      if (url === HOT) throw new TypeError("Failed to fetch");
      return hookAnswer(store, url);
    });
    check("hot lookup unreachable: says so", content.innerHTML.includes("hl-cover-warn")
      && content.innerHTML.includes("that lookup failed (the dashboard could not be reached)"));
  }
  {
    const store = busy(197, 10);
    const { content } = await run(store, async url => url === HOT ? reply(502, new Error("not JSON")) : hookAnswer(store, url));
    check("hot lookup answered non-JSON: the status is stated", content.innerHTML.includes("that lookup failed (the server answered 502)"));
  }
  {
    const store = busy(197, 10);
    const { s, content } = await run(store, async url => url === HOT ? reply(401, { error: "auth_required" }) : hookAnswer(store, url));
    check("hot lookup 401: sign-in expired, and no half list left on the page",
      s.calls.expired === 1 && !content.innerHTML.includes("hl-card"));
  }

  // ?hl_test=1 carries through to both reads.
  {
    const { urls } = await run(busy(197, 10), null, "?hl_test=1");
    check("?hl_test=1 on a cut-off answer: both reads include test records",
      JSON.stringify(urls) === JSON.stringify([ALL + "&include_test=1", HOT + "&include_test=1"]), JSON.stringify(urls));
  }

  out = api.hlMergeLeads([{ callId: "a" }, { callId: "b" }, {}], [{ callId: "b", later: 1 }, { callId: "c" }, {}]);
  check("merge: by callId, first answer's copy kept, records with no callId kept",
    JSON.stringify(out) === JSON.stringify([{ callId: "a" }, { callId: "b" }, {}, { callId: "c" }, {}]), JSON.stringify(out));
  check("merge: non-arrays are empty", JSON.stringify(api.hlMergeLeads(null, "x")) === "[]");
  check("cover note: nothing when the answer was whole",
    api.hlCoverNote({ cut: false, hotFailed: "x" }) === "" && api.hlCoverNote(undefined) === "");
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
  // Part C W14: explain:true makes the research add search_help and needs_confirming, which
  // are for the Research tab only. A re-run here must never ask for them.
  check("re-run never asks for explain",
    seen && !Object.prototype.hasOwnProperty.call(JSON.parse(seen.opts.body), "explain"),
    seen ? seen.opts.body.slice(0, 160) : "no request");
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

// ------------------------------------------------------------------ needs confirming stays out
console.log("\n--- Needs confirming never reaches a lead card ---");
{
  // Part C W14. The research can return listings that need confirming beside an empty
  // shortlist. They are not recommendations: the live block must read as nothing found,
  // and must not show them, whatever else the response carries.
  const s = sandbox();
  const waiting = rec({ lot_address: "Lot 1228, Dawn, Walloon", builder_name: "Needs Confirming Builder" });
  const out = s.api.hlLiveBlock({ status: "success", shortlist: [], shortlist_count: 0,
    needs_confirming: [waiting], needs_confirming_count: 1,
    search_help: { relaxations: [{ kind: "include_needs_confirming", count: 1 }] } });
  check("needs_confirming only: \"Nothing in today's stock matches this brief.\"",
    out.includes(s.api.hlRecsBlock([], { message: "Nothing in today's stock matches this brief." }).trim())
    && out.includes("Nothing in today&#39;s stock matches this brief."), out.slice(0, 400));
  check("needs_confirming only: none of its listings is shown",
    !out.includes("Dawn") && !out.includes("Needs Confirming Builder") && !/class="hl-rec"/.test(out), out.slice(0, 400));
  auditMarkup("needs_confirming only: markup", out);
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
  check("nav label reads Clare's Leads (Agent 1)",
    /Clare's Leads<span class="nav-tab-label-full"> \(Agent 1\)<\/span><\/button>/.test(nav));
  check("the old Hot Leads label is gone from the nav", !nav.includes("Hot Leads"));
  const pane = (html.match(/<div id="hotLeadsTab" class="view-pane">([\s\S]*?)\n<\/div>/) || [, ""])[1];
  check("pane exists", pane.length > 0);
  check("pane header text", pane.includes("<span>Leads from Clare's calls</span>"));
  // Every hot caller is stored, with or without a state; warm and nurture only with one.
  check("explanation: every hot caller, and warm or nurture who gave a state; hot callers get a call booked",
    /Every hot caller from Clare's calls, and every warm or nurture caller who gave a\s+state/.test(pane)
    && /<strong>Hot<\/strong> callers also get a discovery call booked/.test(pane)
    && !pane.includes("graded <strong>hot</strong>"));
  check("explanation does not claim every caller gave a state",
    !/Every caller Clare graded after a call where they gave a state/.test(pane));
  for (const id of ["hotLeadsBadge", "hotLeadsRefresh", "hotLeadsContent"])
    check("pane has #" + id, pane.includes('id="' + id + '"'));
  check("Refresh forces a reload", /id="hotLeadsRefresh"[^>]*onclick="loadHotLeads\(true\)"/.test(pane));
}

console.log("\n" + (fail === 0 ? "ALL " + pass + " CHECKS PASSED" : pass + " passed, " + fail + " FAILED"));
process.exit(fail === 0 ? 0 : 1);
