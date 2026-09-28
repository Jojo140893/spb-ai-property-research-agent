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
// Coleen, 28 Sep 2026: a lead's live results are the Research tab's own card
// (shortlistCardHtml, in the SHORTLIST-CARD block), its picks are slim()'d like the Research
// tab's selection, and Open Report is the same comparison document through the same
// openGeneratedReport. All of it is sliced from where the page declares it, not copied.
const between = (from, to) => {
  const i = html.indexOf(from), j = html.indexOf(to);
  return (i >= 0 && j > i) ? html.slice(i, j) : null;
};
const OTHER = {
  "NOT_STATED": (html.match(/^    const NOT_STATED = .*;$/m) || [null])[0],
  "SELECT_FIELDS": (html.match(/^    const SELECT_FIELDS = \[[\s\S]*?\];$/m) || [null])[0],
  "slim()": (html.match(/^    const slim = p => [\s\S]*?\)\);$/m) || [null])[0],
  "the SHORTLIST-CARD block": between("    // SHORTLIST-CARD-BEGIN", "    // SHORTLIST-CARD-END"),
  "the report renderers": between("    const SHARE_PATH = ", "    async function openGeneratedReport("),
  "openGeneratedReport .. fetchDrafts": between("    async function openGeneratedReport(", "    function copyPayloadJSON("),
};
for (const [what, src] of Object.entries(OTHER)) {
  if (!src) {
    console.log("  FAIL  " + what + " was not found in index.html -- renamed or moved?");
    process.exit(1);
  }
}

const EXPORTS = ["hlSafeUrl", "hlTelHref", "hlMailHref", "hlMoney", "hlWhen", "hlBriefChips",
  "hlRecCard", "hlRecsBlock", "hlLiveBlock", "hlAppointment", "hlLeadCard", "hlRenderLeads",
  "hlSortNewest", "hlUnavailable", "loadHotLeads", "rerunHotLead", "hlGrade", "hlSetFilter",
  "hlMergeLeads", "hlCoverNote", "hlLocationNote",
  // 23 Sep: WhatsApp leads from the Kommo scan share the tab with Clare's callers, in
  // their own group.
  "hlChannel", "hlUnresolvedNote", "hlGroupEmptyText", "hlGroupBlock",
  // 28 Sep (Coleen): the Research tab's selection and report on every lead, notes and links
  // per lead, and contact details only once unlocked.
  "shortlistCardHtml", "leadPickBox", "hlReportBrief", "hlOpenReport", "hlClearPicks",
  "hlTogglePick", "hlShowMore", "hlPickAction", "hlSaveNote", "hlRemoveNote", "hlAddLink",
  "hlRemoveLink", "hlUnlock", "hlLock", "hlMaskedContact", "hlNotesListHtml",
  "hlLinksListHtml", "hlAccessBar", "hlLinkToKeep", "hlPicksFor",
  "openGeneratedReport", "comparisonDocument", "unpackShare", "slim"];

// The tab's two reads -- every grade, and hot callers on their own -- each with and without
// include_test=1, and the research. The bare "/api/hot_leads" the hot-only tab used is NOT
// here: it got the hook's default 50 of every grade, which since 15 Sep can leave hot
// callers out, so a tab that went back to it fails the run.
//
// STILL ACCURATE AFTER THE WHATSAPP SPLIT (23 Sep). WhatsApp leads arrive through the same
// /api/hot_leads records -- the Kommo scan runs on the hook, not in the browser -- so the
// tab asks for nothing new, and a grouping that started fetching its own list would be
// blocked here rather than shipping.
//
// 28 SEP (Coleen): the unlock (/api/contact_unlock), the notes (/api/lead_notes), and the
// AI draft notes the report asks for (/api/narrative, exactly as the Research tab's report
// does). All three are the dashboard's own endpoints: the hook's /api/lead-notes is reached
// only through the dashboard, which holds the service key, so the tab never names it.
const ALLOWED_FETCH = new Set([
  "/api/hot_leads?limit=200", "/api/hot_leads?limit=200&include_test=1",
  "/api/hot_leads?limit=200&grade=hot", "/api/hot_leads?limit=200&grade=hot&include_test=1",
  "/api/research", "/api/contact_unlock", "/api/lead_notes", "/api/narrative"]);

// A localStorage that behaves like the browser's (strings in, strings out), or one that
// throws on every call, as a private window or blocked storage does.
function memoryStorage(blocked) {
  const m = new Map();
  const no = () => { throw new Error("SecurityError: storage is blocked"); };
  return {
    _map: m,
    getItem: k => (blocked ? no() : (m.has(k) ? m.get(k) : null)),
    setItem: (k, v) => (blocked ? no() : void m.set(k, String(v))),
    removeItem: k => (blocked ? no() : void m.delete(k)),
  };
}

// search: the page's own ?query, as location.search. Left undefined, the sandbox has no
// location at all, which the loader must survive. extra: more globals (localStorage,
// confirm, a window) for the checks that need them.
function sandbox(search, extra = {}) {
  const els = new Map();
  const calls = { fetch: [], expired: 0, listeners: [], windows: [] };
  let responder = async () => { throw new Error("no fetch responder set"); };
  // window.open hands back a window whose document collects what is written into it, so
  // the report a click opens can be read back whole.
  const win = {
    SPB_LOGO: "",
    open: () => {
      const w = { html: "", document: { write(s) { w.html += s; }, open() { w.html = ""; }, close() {} } };
      calls.windows.push(w);
      return w;
    },
  };
  const ctx = vm.createContext({
    URL, URLSearchParams, console, TextEncoder, TextDecoder, Blob, Response,
    CompressionStream, DecompressionStream, btoa, atob, setTimeout, clearTimeout,
    ...(search === undefined ? {} : { location: { search, origin: "https://dashboard.invalid" } }),
    window: win,
    // The Research tab's selection. Nothing on this tab may ever write to it.
    SELECTED: new Map(),
    document: {
      getElementById: id => els.get(id) || null,
      addEventListener: (type, fn) => { calls.listeners.push({ type, fn }); },
    },
    sessionExpired: () => { calls.expired++; },
    fetch: async (url, opts) => {
      calls.fetch.push({ url, opts });
      if (!ALLOWED_FETCH.has(url)) throw new Error("BLOCKED: test tried to fetch " + url);
      return responder(url, opts);
    },
    ...extra,
  });
  const api = vm.runInContext(
    [escMatch[0], ...Object.values(OTHER), block].join("\n") + "\n;({" + EXPORTS.join(", ") +
    ", leads: () => HOT_LEADS, access: () => HL_ACCESS, notesMap: () => HL_NOTES," +
    " picksMap: () => hlPicksLoad(), live: () => HL_LIVE })", ctx, { filename: "index.html#hot-leads" });
  const el = (id, props = {}) => {
    const o = { id, innerHTML: "", textContent: "", className: "", disabled: false, hidden: false,
                attrs: {}, setAttribute(k, v) { this.attrs[k] = String(v); }, ...props };
    els.set(id, o);
    return o;
  };
  // A change event on a compare tick, as the browser would deliver it to the page's one
  // delegated listener.
  const change = target => calls.listeners.filter(l => l.type === "change").forEach(l => l.fn({ target }));
  return { api, calls, el, els, ctx, change, respond: fn => { responder = fn; } };
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
// 28 Sep: the Research tab's card (label, input, code), the notes (textarea, input), and the
// unlock form. Their attributes, and the handlers the new buttons call, are listed below --
// each onclick by the exact shape it may take, numbers and a fixed list of words only.
const TAGS = new Set(["div", "span", "a", "strong", "br", "details", "summary", "p", "button", "em",
  "label", "input", "code", "textarea", "form"]);
const ATTRS = new Set(["class", "id", "href", "target", "rel", "title", "style", "onclick", "type", "disabled",
  "hidden", "aria-pressed", "checked", "data-hash", "data-lead", "data-term", "value", "placeholder",
  "maxlength", "rows", "autocomplete", "aria-label", "role", "aria-live", "onsubmit"]);
const ONCLICK = new RegExp("^(rerunHotLead\\(\\d+\\)|loadHotLeads\\(true\\)|hlSetFilter\\('(all|hot|warm|nurture)'\\)"
  + "|hlOpenReport\\(\\d+\\)|hlClearPicks\\(\\d+\\)|hlShowMore\\(\\d+\\)|hlSaveNote\\(\\d+\\)|hlAddLink\\(\\d+\\)"
  + "|hlRemoveNote\\(\\d+, \\d+\\)|hlRemoveLink\\(\\d+, \\d+\\)|hlLock\\(\\)"
  + "|hlPickAction\\(\\d+, '(package_sent|call_booked|follow_up|drive_link|property_saved)'\\))$");
const INPUT_TYPES = new Set(["checkbox", "text", "url", "password"]);
const decode = s => s.replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">")
  .replace(/&#39;/g, "'").replace(/&amp;/g, "&");

function markupProblems(outRaw) {
  const problems = [];
  // The Research tab's card carries HTML comments about its own history; they are not markup.
  const out = String(outRaw).replace(/<!--[\s\S]*?-->/g, "");
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
      if (attr === "onclick" && !ONCLICK.test(val))
        problems.push("onclick " + JSON.stringify(val));
      if (attr === "onsubmit" && val !== "hlUnlock(); return false;")
        problems.push("onsubmit " + JSON.stringify(val));
      if (attr === "style" && /url\(|expression|javascript/i.test(val))
        problems.push("style " + JSON.stringify(val));
      if (name === "input" && attr === "type" && !INPUT_TYPES.has(val))
        problems.push("input type " + JSON.stringify(val));
    }
    // noopener is what matters (the opened page cannot reach back into this one); the
    // Research tab's source links carry it alone, and are drawn here byte for byte.
    if (name === "a" && /target="_blank"/.test(attrText) && !/rel="noopener( noreferrer)?"/.test(attrText))
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
check("auditor flags a Remove button carrying anything but numbers",
  markupProblems(`<button onclick="hlRemoveNote(1, 'abc')">x</button>`).length > 0
  && markupProblems(`<button onclick="hlRemoveNote(1, 2); alert(1)">x</button>`).length > 0);
check("auditor flags a quick action outside the five",
  markupProblems(`<button onclick="hlPickAction(0, 'other')">x</button>`).length > 0);
check("auditor flags any other onsubmit, and a hidden or file input",
  markupProblems('<form onsubmit="alert(1)"></form>').length > 0
  && markupProblems('<input type="hidden" value="x">').length > 0
  && markupProblems('<input type="file">').length > 0);
check("auditor passes the new handlers in their exact shapes",
  markupProblems(`<button onclick="hlRemoveNote(3, 0)">x</button><button onclick="hlPickAction(2, 'drive_link')">y</button>`
    + '<form class="hl-unlock" onsubmit="hlUnlock(); return false;"><input type="password" id="p"></form>').length === 0);

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
// Unlocked (contact "full"), so the email and phone are drawn and their escaping is checked.
// Masked, they are not drawn at all -- see "contact details are masked" below.
throwsNot("hostile record renders", () => { out = api.hlLeadCard(hostile(), 0, false, { contact: "full" }); });
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
console.log("\n--- a clean record links what it should (contact details unlocked) ---");
out = api.hlLeadCard(clean(), 3, false, { contact: "full" });
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

// ------------------------------------------------------------------ WhatsApp and Vapi
// 23 Sep: a WhatsApp lead the Kommo bot has qualified is written to the same store as
// Clare's callers, carrying channel:"whatsapp". EVERY RECORD WRITTEN BEFORE THAT FIELD
// EXISTED HAS NO channel AT ALL, so a missing one must read as Vapi -- the whole tab is
// those records, and a different default would move all of them into the wrong group on the
// day this ships. The two are worked differently, so they are listed apart.
console.log("\n--- WhatsApp and Vapi are two groups ---");

// The measured shape of a qualified WhatsApp card (23 Sep): no call, so no recording, no
// duration and no summary -- the message bodies are unreachable (leads/{id}/notes answers
// 204), so a summary would have to be invented and is left empty. The name and phone come
// from the CONTACT; lead.name is Kommo's default "Lead #54494433". The suburbs are free
// text the customer typed.
const wa = (over = {}) => clean({
  callId: "wa-54494433", channel: "whatsapp", createdAt: "2026-09-22T23:10:00Z",
  endedReason: "", durationSeconds: 0, recordingUrl: "", summary: "", appointment: null,
  customer: { name: "Bari", email: "", phone: "+61479057492" },
  grade: "warm", gradeSource: "kommo-tag", gradeReason: "Kommo tag WARM LEAD.",
  brief: { ...clean().brief, client_name: "Bari", state: "QLD",
           primary_suburbs: ["COUNTRY NEAR MOUNT VICENT"], budget_max: 900000,
           deposit_amount: 50000, timeframe: "0-3 months", finance_status: "Yes" },
  kommo: { contactId: 51345283, leadId: 54494433,
           leadUrl: "https://spb.kommo.com/leads/detail/54494433" },
  research: { status: "ok", message: "", record_id: "r9", shortlist_count: 3, widened: "",
              location: [{ said: "COUNTRY NEAR MOUNT VICENT", used: [], method: "unresolved" }] },
  ...over,
});

// WhatsApp is drawn first and Vapi last, so a group runs to the next group marker or the end.
const groupHtml = (html, channel) => {
  const i = html.indexOf(`id="hlGroup-${channel}"`);
  if (i < 0) return "";
  const j = html.indexOf('id="hlGroup-', i + 1);
  return j < 0 ? html.slice(i) : html.slice(i, j);
};

{
  check("channel 'whatsapp' -> whatsapp", api.hlChannel({ channel: "whatsapp" }) === "whatsapp");
  check("a record with no channel is grouped as Vapi (hlChannel)",
    api.hlChannel({ callId: "x" }) === "vapi" && api.hlChannel(clean()) === "vapi");
  for (const c of ["vapi", "WhatsApp", "WHATSAPP", "whatsapp ", " whatsapp", "", null, undefined,
                   7, EVIL, ["whatsapp"], { channel: "whatsapp" }])
    check("channel " + JSON.stringify(c) + " -> vapi", api.hlChannel({ channel: c }) === "vapi",
      api.hlChannel({ channel: c }));
  check("a non-object is vapi",
    api.hlChannel(null) === "vapi" && api.hlChannel(undefined) === "vapi"
    && api.hlChannel("whatsapp") === "vapi");

  const mixed = [
    wa({ callId: "wa1", createdAt: "2026-09-22T23:10:00Z" }),
    clean({ callId: "v1", createdAt: "2026-09-22T22:00:00Z",
            customer: { name: "Clare Caller", email: "", phone: "" } }),
    clean({ callId: "v2", channel: "vapi", grade: "warm", appointment: null,
            createdAt: "2026-09-22T21:00:00Z",
            customer: { name: "Second Caller", email: "", phone: "" } }),
  ];
  out = api.hlRenderLeads(mixed, "all");
  auditMarkup("two groups: markup is only the renderer's own", out);
  check("both groups are drawn, WhatsApp first",
    out.indexOf('id="hlGroup-whatsapp"') >= 0
    && out.indexOf('id="hlGroup-whatsapp"') < out.indexOf('id="hlGroup-vapi"'),
    out.slice(0, 200));
  check("each group has a heading and a count",
    out.includes('<span class="hl-group-name">WhatsApp</span><span class="hl-group-count">(1)</span>')
    && out.includes('<span class="hl-group-name">Vapi</span><span class="hl-group-count">(2)</span>'));
  const waHtml = groupHtml(out, "whatsapp"), vapiHtml = groupHtml(out, "vapi");
  check("the WhatsApp lead is in the WhatsApp group",
    waHtml.includes("Bari") && !vapiHtml.includes("Bari"));
  check("a record with no channel is grouped as Vapi, not WhatsApp",
    vapiHtml.includes("Clare Caller") && !waHtml.includes("Clare Caller"));
  check("split into two groups: every card is printed once, in its channel's group",
    [0, 1, 2].every(i => (out.match(new RegExp('id="hlCard-' + i + '"', "g")) || []).length === 1)
    && waHtml.includes('id="hlCard-0"')
    && vapiHtml.includes('id="hlCard-1"') && vapiHtml.includes('id="hlCard-2"'),
    [0, 1, 2].map(i => (out.match(new RegExp('id="hlCard-' + i + '"', "g")) || []).length).join(","));
  // Grouping changes the order cards are PRINTED in, never the index they carry:
  // rerunHotLead(i) reads HOT_LEADS[i].
  check("a card keeps its index into the full list, not its place in its group",
    vapiHtml.includes('onclick="rerunHotLead(1)"') && vapiHtml.includes('onclick="rerunHotLead(2)"')
    && !vapiHtml.includes('onclick="rerunHotLead(0)"')
    && waHtml.includes('onclick="rerunHotLead(0)"'));
  // Nobody CALLED a WhatsApp lead. Its time is when the bot's last question was answered, so
  // it says "Qualified". The only label check before 24 Sep used a Clare card, so the wrong
  // word on every WhatsApp card went unnoticed.
  check("a WhatsApp card says Qualified, never Called",
    /class="hl-when">Qualified /.test(waHtml) && !/class="hl-when">Called /.test(waHtml),
    (waHtml.match(/class="hl-when">[^<]*/) || [""])[0]);
  check("a Vapi card still says Called",
    /class="hl-when">Called /.test(vapiHtml) && !/class="hl-when">Qualified /.test(vapiHtml));

  out = api.hlRenderLeads([clean({ callId: "v1" })], "all");
  auditMarkup("empty WhatsApp group: markup", out);
  check("an empty group says so rather than vanishing",
    out.includes('id="hlGroup-whatsapp"')
    && out.includes('<span class="hl-group-name">WhatsApp</span><span class="hl-group-count">(0)</span>')
    && /id="hlGroupEmpty-whatsapp">No WhatsApp leads yet\.<\/div>/.test(out), out.slice(0, 300));
  check("the group that has leads does not show its empty note",
    /id="hlGroupEmpty-vapi" hidden>/.test(out));

  out = api.hlRenderLeads([wa({ callId: "wa1", grade: "warm" }), clean({ callId: "v1", grade: "hot" })], "hot");
  check("a group emptied by the filter says which filter emptied it",
    /id="hlGroupEmpty-whatsapp">No hot WhatsApp leads in this list\.<\/div>/.test(out)
    && /id="hlGroupEmpty-vapi" hidden>/.test(out), out.slice(0, 400));
  check("group empty text: All says none yet, a grade says none of that grade",
    api.hlGroupEmptyText("whatsapp", "all") === "No WhatsApp leads yet."
    && api.hlGroupEmptyText("vapi", "nurture") === "No nurture Vapi leads in this list.");
}

// The groups survive a real load, and the filter keeps each group's note honest.
{
  const s = sandbox();
  const content = s.el("hotLeadsContent"), badge = s.el("hotLeadsBadge");
  s.el("hotLeadsRefresh");
  s.respond(async () => reply(200, { status: "success", count: 3, leads: [
    wa({ callId: "wa1", grade: "warm", createdAt: "2026-09-22T23:10:00Z" }),
    clean({ callId: "v1", grade: "hot", createdAt: "2026-09-22T22:00:00Z",
            customer: { name: "Clare Caller", email: "", phone: "" } }),
    wa({ callId: "wa2", grade: "hot", createdAt: "2026-09-22T21:00:00Z",
         customer: { name: "Second Chat", email: "", phone: "" } }),
  ] }));
  await s.api.loadHotLeads();
  check("the loaded tab is split into two groups",
    content.innerHTML.includes('id="hlGroup-whatsapp"') && content.innerHTML.includes('id="hlGroup-vapi"'));
  check("the counts are per group", content.innerHTML.includes('>WhatsApp</span><span class="hl-group-count">(2)</span>')
    && content.innerHTML.includes('>Vapi</span><span class="hl-group-count">(1)</span>'));
  check("the badge still counts the whole list", badge.textContent === "3 leads · 2 hot", badge.textContent);
  auditMarkup("loaded two-group list markup", content.innerHTML);

  const cards = [0, 1, 2].map(i => s.el("hlCard-" + i));
  const waNote = s.el("hlGroupEmpty-whatsapp"), vapiNote = s.el("hlGroupEmpty-vapi");
  s.api.hlSetFilter("hot");
  check("filter Hot: the WhatsApp group keeps its hot lead and hides its warm one",
    cards[0].hidden === true && cards[2].hidden === false && waNote.hidden === true,
    cards.map(c => (c.hidden ? "-" : "V")).join(""));
  s.api.hlSetFilter("nurture");
  check("a filter that empties a group updates that group's own note",
    waNote.hidden === false && waNote.textContent === "No nurture WhatsApp leads in this list."
    && vapiNote.hidden === false && vapiNote.textContent === "No nurture Vapi leads in this list.",
    waNote.textContent + " | " + vapiNote.textContent);
  s.api.hlSetFilter("all");
  check("back to All: both group notes are hidden again",
    waNote.hidden === true && vapiNote.hidden === true);
  check("every fetch went to a dashboard endpoint", s.calls.fetch.every(c => ALLOWED_FETCH.has(c.url)));
}

// ------------------------------------------------------------------ nothing vanishes
// hlSortNewest drops a record whose grade is none of hot/warm/nurture, and that is right for
// a CALLER: Clare grades everyone she speaks to, so a grade she did not write is a broken
// record. A WhatsApp lead's grade is a Kommo tag written after question 2 of 8, so a fully
// answered lead can carry no tag at all -- and dropping it would delete it from the tab with
// nothing anywhere saying a lead had been hidden.
console.log("\n--- a WhatsApp lead is never dropped for its grade ---");
{
  const noTag = wa({ callId: "wa-untagged", grade: undefined });
  check("a WhatsApp record with no grade is not called hot", api.hlGrade(noTag) === "");
  check("a Vapi record with no grade is still hot (stored while the tab was hot-only)",
    api.hlGrade(clean({ grade: undefined })) === "hot");
  const ids = api.hlSortNewest([
    noTag,
    wa({ callId: "wa-cold", grade: "cold" }),          // the scan must never write this
    clean({ callId: "v-closed", grade: "closed" }),
    clean({ callId: "v-ok", grade: "hot" }),
  ]).map(l => l.callId);
  check("an ungraded WhatsApp lead is listed, not dropped",
    ids.includes("wa-untagged") && ids.includes("wa-cold") && ids.includes("v-ok")
    && !ids.includes("v-closed"), ids.join(","));
  out = api.hlLeadCard(noTag, 0);
  auditMarkup("ungraded WhatsApp card: markup", out);
  check("an ungraded WhatsApp card says so where the grade pill would be",
    out.includes('<span class="hl-ungraded">Not graded yet</span>') && !/pill-(hot|warm|nurture)/.test(out));
  check("an ungraded card is not called hot",
    !out.includes("Why hot") && out.includes("<strong>Grade reason:</strong>"));
  check("an ungraded Vapi card carries no such marker",
    !api.hlLeadCard(clean({ grade: "closed" }), 0).includes("hl-ungraded"));
  out = api.hlRenderLeads([noTag], "all");
  check("an ungraded WhatsApp lead is shown under All, in the WhatsApp group",
    /id="hlCard-0">/.test(out) && !/id="hlCard-0" hidden/.test(out)
    && groupHtml(out, "whatsapp").includes('id="hlCard-0"'));
}

// ------------------------------------------------------------------ searched as written
// A WhatsApp lead types its suburbs free-hand ("COUNTRY NEAR MOUNT VICENT"). The phrase goes
// to Agent 2 unparsed: api/_location_resolver.py reads it, and lib/research.js widens
// 20 km -> 50 km -> the whole state when nothing resolves. So the buildings below are real
// stock for the state -- but not stock near the place the customer named, and they look like
// an answer to that place until the card says otherwise.
console.log("\n--- the suburb was searched as written ---");
{
  const unres = location => api.hlUnresolvedNote({ status: "ok", location });
  out = unres([{ said: "COUNTRY NEAR MOUNT VICENT", used: [], method: "unresolved" }]);
  check("an unresolved phrase is named, as written",
    out === '<div class="hl-unres">No suburb matched, so this was searched as written: COUNTRY NEAR MOUNT VICENT</div>', out);
  auditMarkup("unresolved line markup", out);
  check("a phrase that WAS read produces no such line (hlLocationNote says that instead)",
    unres([{ said: "Tarn eat", used: ["Tarneit"], method: "groq" }]) === ""
    && unres([{ said: "Tarneit", used: ["Tarneit"], method: "exact" }]) === ""
    && unres([{ said: "Cragieburn", used: ["Craigieburn"], method: "fuzzy" }]) === "");
  check("the same phrase twice is said once",
    unres([{ said: "A", used: [], method: "unresolved" }, { said: "A", used: [], method: "unresolved" }])
      === '<div class="hl-unres">No suburb matched, so this was searched as written: A</div>');
  for (const junk of [undefined, null, "x", 5, { location: "x" }, { location: [] },
                      { location: [null, 3, "unresolved", { method: "unresolved" },
                                   { said: "  ", method: "unresolved" },
                                   { said: "x", method: "UNRESOLVED" }] }])
    check("unresolved note: " + JSON.stringify(junk) + " -> no line",
      api.hlUnresolvedNote(junk) === "", JSON.stringify(api.hlUnresolvedNote(junk)));
  out = api.hlUnresolvedNote({ location: [{ said: EVIL, used: [], method: "unresolved" }] });
  check("a hostile phrase is shown as text",
    out === `<div class="hl-unres">No suburb matched, so this was searched as written: ${EVIL_TEXT}</div>`, out);
  auditMarkup("hostile unresolved line markup", out);

  out = api.hlLeadCard(wa({ callId: "wa1" }), 0);
  auditMarkup("WhatsApp card markup", out);
  check("a WhatsApp card shows its brief chips",
    out.includes('class="hl-chips"') && out.includes("Budget up to $900,000")
    && out.includes("Deposit $50,000") && out.includes("State: QLD")
    && out.includes("Suburbs: COUNTRY NEAR MOUNT VICENT") && out.includes("Timeframe: 0-3 months"));
  check("a WhatsApp card says the suburb was searched as written",
    out.includes("No suburb matched, so this was searched as written: COUNTRY NEAR MOUNT VICENT"));
  check("a WhatsApp card has no call summary and no recording link",
    !out.includes("Call summary") && !out.includes("Call recording"));
  check("a WhatsApp card keeps its Kommo link and a working Re-run",
    />Open in Kommo<\/a>/.test(out) && out.includes('onclick="rerunHotLead(0)"')
    && !/id="hlRerun-0"[^>]*disabled/.test(out));
  check("a WhatsApp lead whose suburb resolved has no 'as written' line",
    !api.hlLeadCard(wa({ research: { status: "ok",
      location: [{ said: "Red Bank Plains", used: ["Redbank Plains"], method: "spoken" }] } }), 0)
      .includes("searched as written"));
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
  // Coleen, 28 Sep 2026: "match and mirror the first page/tab". The live results are the
  // whole shortlist, drawn with the Research tab's card -- no longer the top 3, compact.
  check("the whole live shortlist, as Research tab cards (no longer the top 3)",
    (box.innerHTML.match(/class="prop-card hl-live-card/g) || []).length === 5
    && box.innerHTML.includes("Three") && box.innerHTML.includes("Five")
    && !/class="hl-rec"/.test(box.innerHTML), (box.innerHTML.match(/class="prop-card hl-live-card/g) || []).length);
  check("says how many the live search found", box.innerHTML.includes("Showing 5 of 5"));
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
    !out.includes("Dawn") && !out.includes("Needs Confirming Builder") && !/class="hl-rec"/.test(out)
    && !out.includes("hl-live-card"), out.slice(0, 400));
  auditMarkup("needs_confirming only: markup", out);
}

// ================================================================== Coleen, 28 Sep 2026
// "Each state tab should have the same property selection and reporting functionality as
// Tab 1", "dedicated spaces for Notes and Links", and "only display the last 3 digits of the
// customer's mobile number ... The full customer contact details should remain restricted
// to authorised users only."
const liveProp = (over = {}) => ({
  content_hash: "v2:live1", lot_address: "Lot 7 Example Rd", suburb: "Tarneit", state: "VIC",
  builder_name: "Metricon", house_design: "Aspen 25", score: 83.5, recommendation: "Recommend",
  bedrooms: 4, bathrooms: 2, car_spaces: 2, land_size_sqm: 350, house_size_sqm: 210,
  advertised_package_price: 640000, turnkey_status: "Full Turnkey", missing_inclusions: [],
  benchmark: { benchmarked: true, basis: "internal", classification: "Fair Market Value",
               variance_pct: 1.2, avg_market_price: 632000, needs_manual_benchmark: false },
  scoring_details: { budget_fit: 18, requirement_match: 16, location_amenity: 10,
                     value_competitiveness: 12, builder_confidence: 8, suitability_score: 8, risk_score: 9 },
  recommendation_reason: "Under budget.", advisories: "", date_checked: "27/09/2026",
  source_link: { url: "https://builder.example.com/list.pdf", label: "Price list", opens: "price list" },
  competitive: null,
  ...over,
});
const tickEvent = (lead, hash, checked, cls = "hl-cmp-box") =>
  ({ classList: { contains: c => c === cls }, dataset: { lead, hash }, checked });

console.log("\n--- a lead's live results are the Research tab's card, with the lead's own tick ---");
{
  const card = api.shortlistCardHtml(liveProp(), 0, { lead: "call-1", picked: false });
  auditMarkup("lead-mode card: markup is only the renderer's own", card);
  check("a live card's tick is the lead's own (hl-cmp-box, data-lead), never the Research tab's .cmp-box",
    /<input type="checkbox" class="hl-cmp-box" data-lead="call-1" data-hash="v2:live1">/.test(card)
    && !/class="cmp-box"/.test(card), (card.match(/<input[^>]*>/) || [""])[0]);
  check("no benchmark button on a lead's card (it reads the Research tab's LAST_SHORTLIST)",
    !card.includes("bench-btn") && !card.includes("benchOut"));
  check("the rest is the Research tab's card: rank, scoring matrix, advertised price, source link",
    card.includes("Rank #1: Lot 7 Example Rd") && card.includes("SOP Step 11 Scoring Matrix Breakdown")
    && card.includes("$640,000") && />Price list<\/a>/.test(card) && card.includes("(Full Turnkey Verified)"));
  const research = api.shortlistCardHtml(liveProp(), 0);
  check("with no opts it is still the Research tab's card: .cmp-box and the benchmark button",
    /class="cmp-box" data-hash="v2:live1"/.test(research) && research.includes('class="bench-btn" data-idx="0"')
    && !research.includes("hl-cmp-box"));
  check("a building this lead picked is drawn ticked",
    /class="hl-cmp-box"[^>]* checked>/.test(api.shortlistCardHtml(liveProp(), 1, { lead: "call-1", picked: true })));
  check("a listing with no hash cannot be ticked",
    /class="hl-cmp-box"[^>]* disabled title="This listing has no stable identifier/.test(
      api.shortlistCardHtml(liveProp({ content_hash: "" }), 0, { lead: "call-1" })));
  const noLead = api.shortlistCardHtml(liveProp(), 0, { lead: "" });
  check("a lead with no call id cannot keep a tick, and still gets no .cmp-box",
    /class="hl-cmp-box" data-lead=""[^>]* disabled/.test(noLead) && !/class="cmp-box"/.test(noLead));
  const evil = api.shortlistCardHtml(liveProp({ content_hash: '"><img src=x onerror=alert(1)>' }), 0,
    { lead: '"><script>alert(2)</script>' });
  auditMarkup("a hostile call id and hash in the tick: markup", evil);
  check("a hostile call id and hash stay inside their attributes",
    evil.includes('data-lead="&quot;&gt;&lt;script&gt;alert(2)&lt;/script&gt;"') && !/<img/i.test(evil));
  check("leadPickBox refuses a non-string id or hash rather than printing it",
    /data-lead="" data-hash=""[^>]* disabled/.test(api.leadPickBox({ x: 1 }, 42, true)));
}

console.log("\n--- the card is null-safe for a record that is not a full shortlist row ---");
for (const [label, p] of [
  ["an empty record", {}], ["null", null], ["a stored recommendation", rec()],
  ["no breakdown, no inclusions, no price, no verdict",
   liveProp({ scoring_details: undefined, missing_inclusions: undefined, advertised_package_price: null,
              recommendation: undefined, turnkey_status: undefined })],
]) {
  let got = "";
  throwsNot(label + ": the card renders", () => { got = api.shortlistCardHtml(p, 0, { lead: "c" }); });
  auditMarkup(label + ": markup", got);
  const text = got.replace(/<!--[\s\S]*?-->/g, "");
  check(label + ": no 'undefined' or 'NaN' printed, and no claim of Full Turnkey Verified",
    !/undefined|NaN/.test(text) && !text.includes("Full Turnkey Verified"),
    (text.match(/.{0,40}(undefined|NaN).{0,40}/) || [""])[0]);
}
check("a missing advertised price says not stated",
  /Advertised Price<\/span><strong[^>]*>not stated<\/strong>/.test(api.shortlistCardHtml({ advertised_package_price: null }, 0, { lead: "c" })));
check("no scoring breakdown: no matrix at all, not a matrix of blanks",
  !api.shortlistCardHtml({ score: 80 }, 0, { lead: "c" }).includes("score-section"));
throwsNot("the Research tab's own mode is null-safe too", () => api.shortlistCardHtml({}, 0));

console.log("\n--- a hostile live shortlist renders as text ---");
{
  const hostileLive = liveProp({
    content_hash: "v2:evil", lot_address: EVIL, suburb: EVIL, state: "<b>VIC</b>", builder_name: EVIL,
    house_design: EVIL, recommendation: EVIL, turnkey_status: EVIL, recommendation_reason: EVIL,
    advisories: EVIL, bedrooms: '<img src=x onerror="alert(1)">', bathrooms: EVIL, car_spaces: EVIL,
    advertised_package_price: EVIL, score: EVIL, distance_km_from_target: EVIL, date_checked: EVIL,
    benchmark: { benchmarked: true, basis: EVIL, classification: EVIL, variance_pct: EVIL,
                 avg_market_price: EVIL, needs_manual_benchmark: true, data_note: EVIL },
    competitive: { verdict: "flag", note: EVIL, cheapest_url: " javascript:alert(8)", n_comps: 2,
                   confidence: EVIL, source: EVIL, as_at: EVIL },
    source_link: { url: "javascript:alert(9)", label: EVIL, opens: "lot", search: EVIL },
    scoring_details: { budget_fit: EVIL, requirement_match: 1, location_amenity: 1,
                       value_competitiveness: 1, builder_confidence: 1, suitability_score: 1, risk_score: 1 },
  });
  out = api.hlLiveBlock({ status: "success", shortlist: [hostileLive], shortlist_count: EVIL }, { n: 0, lead: "call-1" });
  auditMarkup("hostile live shortlist: markup is only the renderer's own", out);
  check("hostile live shortlist: no <script, <img or <b from the data", !/<script|<img|<b>/i.test(out));
  check("a javascript: cheaper comparable is not a link",
    !out.includes("View the cheaper comparable") && !/href="\s*javascript/i.test(out));
}

console.log("\n--- a re-run shows the whole shortlist, 25 at a time ---");
{
  const s = sandbox();
  s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  s.respond(async () => reply(200, { status: "success", leads: [clean()] }));
  await s.api.loadHotLeads();
  const box = s.el("hlRecs-0"); s.el("hlRerun-0"); s.el("hlMsg-0");
  const sixty = Array.from({ length: 60 }, (_, k) => liveProp({ content_hash: "v2:p" + k, lot_address: "Card " + k }));
  s.respond(async () => reply(200, { status: "success", shortlist: sixty, shortlist_count: 60 }));
  await s.api.rerunHotLead(0);
  const cards = () => (box.innerHTML.match(/class="prop-card hl-live-card/g) || []).length;
  check("a live shortlist is painted 25 at a time",
    cards() === 25 && box.innerHTML.includes("Card 24") && !box.innerHTML.includes("Card 25<"), String(cards()));
  check("... with Show more saying what is left",
    box.innerHTML.includes('onclick="hlShowMore(0)"') && box.innerHTML.includes("Show 25 more (35 remaining of 60)"));
  check("... and the count of what was found", box.innerHTML.includes("Showing 25 of 60"));
  check("only the first card is rank 1", (box.innerHTML.match(/hl-live-card rank-1/g) || []).length === 1);
  s.api.hlShowMore(0);
  check("Show more adds the next 25", cards() === 50 && box.innerHTML.includes("Show 10 more (10 remaining of 60)"), String(cards()));
  s.api.hlShowMore(0);
  check("the last page leaves no Show more", cards() === 60 && !box.innerHTML.includes("hlShowMore"), String(cards()));
  auditMarkup("paged live results: markup", box.innerHTML);
  check("every live card carries this lead's tick, and none the Research tab's",
    (box.innerHTML.match(/class="hl-cmp-box" data-lead="call-1"/g) || []).length === 60
    && !/class="cmp-box"/.test(box.innerHTML));
  s.api.hlShowMore(5);
  check("Show more on a card with no live results does nothing", cards() === 60);
}

console.log("\n--- each lead keeps its own picks, and none reach the Research tab's selection ---");
{
  const store = memoryStorage();
  const s = sandbox(undefined, { localStorage: store });
  const content = s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  const a = clean({ callId: "call-a", createdAt: "2026-09-20T00:00:00Z", recommendations: [
    rec({ content_hash: "h-a1" }), rec({ content_hash: "h-a2", builder_name: "Porter Davis" }),
    rec({ content_hash: "h-a3", builder_name: "Boutique" }), rec({ content_hash: "h-a4", builder_name: "Fourth" })] });
  const bLead = clean({ callId: "call-b", createdAt: "2026-09-19T00:00:00Z",
    customer: { name: "Other Client", email: "other@example.com", phone: "0400111222" },
    recommendations: [rec({ content_hash: "h-b1" })] });
  s.respond(async () => reply(200, { status: "success", leads: [a, bLead] }));
  await s.api.loadHotLeads();
  const html0 = content.innerHTML;
  auditMarkup("lead cards with ticks: markup", html0);
  check("each saved building carries its own lead's tick",
    html0.includes('data-lead="call-a" data-hash="h-a1"') && html0.includes('data-lead="call-b" data-hash="h-b1"')
    && !/class="cmp-box"/.test(html0));
  check("the saved buildings say they are the day-of-call picks",
    (html0.match(/Picked from the stock on the day of the call/g) || []).length === 2);
  check("Open Report starts on the top 3, with Clear hidden",
    html0.includes('onclick="hlOpenReport(0)"') && html0.includes(">Open Report (top 3)</button>")
    && /id="hlClear-0" onclick="hlClearPicks\(0\)"[^>]* hidden>/.test(html0));
  check("the tab listens for its ticks, once", s.calls.listeners.filter(l => l.type === "change").length === 1);

  const rep = s.el("hlReport-0"), clr = s.el("hlClear-0", { hidden: true }), msg = s.el("hlMsg-0");
  s.change(tickEvent("call-a", "h-a2", true));
  check("a tick is kept against that lead", [...s.api.hlPicksFor("call-a").keys()].join() === "h-a2");
  check("a lead's picks never reach SELECTED (the Research tab's selection)", s.ctx.SELECTED.size === 0);
  check("the report button says what it will cover", rep.textContent === "Open Report (1 selected)" && clr.hidden === false,
    rep.textContent);
  const stored = JSON.parse(store.getItem("spb.leadcompare.v1") || "null");
  check("kept in localStorage spb.leadcompare.v1 as [callId, [record]] pairs",
    Array.isArray(stored) && stored.length === 1 && stored[0][0] === "call-a" && stored[0][1][0].content_hash === "h-a2",
    JSON.stringify(stored).slice(0, 200));
  check("a pick is slim()'d, as the Research tab's selection is",
    JSON.stringify(stored[0][1][0]) === JSON.stringify(api.slim(a.recommendations[1])));
  s.change(tickEvent("call-a", "h-a1", true, "cmp-box"));
  check("a Research tab .cmp-box is not this tab's to read", s.api.hlPicksFor("call-a").size === 1 && s.ctx.SELECTED.size === 0);
  const ghost = tickEvent("call-a", "h-a4", true);
  s.change(ghost);
  check("a tick for a building not on the card is refused, and unticked",
    s.api.hlPicksFor("call-a").size === 1 && ghost.checked === false);
  s.change(tickEvent("call-nobody", "h-a1", true));
  check("a tick naming a lead that is not listed is refused", !s.api.hlPicksFor("call-nobody").size);
  s.change(tickEvent("call-b", "h-b1", true));
  check("picks are per lead: each keeps its own",
    [...s.api.hlPicksFor("call-b").keys()].join() === "h-b1" && [...s.api.hlPicksFor("call-a").keys()].join() === "h-a2");
  s.change(tickEvent("call-a", "h-a2", false));
  check("unticking forgets it, and the button goes back to the top 3",
    s.api.hlPicksFor("call-a").size === 0 && rep.textContent === "Open Report (top 3)" && clr.hidden === true);
  s.change(tickEvent("call-a", "h-a1", true));
  s.change(tickEvent("call-a", "h-a3", true));
  const box0 = s.el("hlRecs-0");
  s.api.hlClearPicks(0);
  check("Clear forgets this lead's picks only, and redraws its ticks unticked",
    s.api.hlPicksFor("call-a").size === 0 && s.api.hlPicksFor("call-b").size === 1
    && box0.innerHTML.includes('data-hash="h-a1"') && !/data-lead="call-a"[^>]*checked/.test(box0.innerHTML));
  const all = JSON.stringify([...store._map]);
  check("nothing about a customer is in localStorage (the picks are listings)",
    !all.includes("jo.citizen@example.com") && !all.includes("412345678") && !all.includes("Jo Citizen")
    && !all.includes("other@example.com") && !all.includes("Other Client"));

  // The next page view, in the same browser: the picks are still there.
  const s2 = sandbox(undefined, { localStorage: store });
  const c2 = s2.el("hotLeadsContent"); s2.el("hotLeadsBadge"); s2.el("hotLeadsRefresh");
  s2.respond(async () => reply(200, { status: "success", leads: [a, bLead] }));
  await s2.api.loadHotLeads();
  check("picks survive a reload, on their own lead", /data-lead="call-b" data-hash="h-b1" checked>/.test(c2.innerHTML)
    && !/data-lead="call-a"[^>]*checked>/.test(c2.innerHTML) && c2.innerHTML.includes(">Open Report (1 selected)</button>"));
  check("every fetch went to a dashboard endpoint", s.calls.fetch.every(c => ALLOWED_FETCH.has(c.url)));
}
{
  const store = memoryStorage();
  store.setItem("spb.leadcompare.v1", JSON.stringify([["__proto__", [{ content_hash: "x" }]], ["call-a", "nope"],
    [7, []], "junk", ["call-a", [null, { content_hash: 5 }, { content_hash: "h-a1", lot_address: "Lot 1" }]]]));
  const s = sandbox(undefined, { localStorage: store });
  let picks = null;
  throwsNot("an odd pick store is read without throwing", () => { picks = s.api.picksMap(); });
  check("... keeping only what is well formed", picks && picks.get("call-a") && picks.get("call-a").size === 1
    && picks.get("__proto__").size === 1 && !picks.has(7));
  const bad = memoryStorage();
  bad.setItem("spb.leadcompare.v1", "{not json");
  check("a store that is not JSON is no picks", sandbox(undefined, { localStorage: bad }).api.picksMap().size === 0);
}
{
  const s = sandbox(undefined, { localStorage: memoryStorage(true) });
  s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  s.respond(async () => reply(200, { status: "success", leads: [clean({ callId: "call-a", recommendations: [rec({ content_hash: "h1" })] })] }));
  await s.api.loadHotLeads();
  const msg = s.el("hlMsg-0");
  s.change(tickEvent("call-a", "h1", true));
  check("blocked storage: the pick still counts for this page view, and the card says it was not kept",
    s.api.hlPicksFor("call-a").size === 1 && /would not keep the pick/.test(msg.textContent));
}
{
  // TWO DASHBOARD TABS IN ONE BROWSER (28 Sep review): both drawn before either ticked
  // anything, so each read the store while it was empty. A save that wrote the page's whole
  // map back made the second tab's tick wipe the first tab's.
  const store = memoryStorage();
  store.setItem("spb.leadcompare.v1", JSON.stringify([["call-old", [{ content_hash: "h-old" }]]]));
  const leads = [clean({ callId: "call-x", recommendations: [rec({ content_hash: "hx" })] }),
                 clean({ callId: "call-y", createdAt: "2026-09-14T00:00:00Z", recommendations: [rec({ content_hash: "hy" })] })];
  const tabs = [sandbox(undefined, { localStorage: store }), sandbox(undefined, { localStorage: store })];
  for (const t of tabs) {
    t.el("hotLeadsContent"); t.el("hotLeadsBadge"); t.el("hotLeadsRefresh");
    t.respond(async () => reply(200, { status: "success", leads }));
    await t.api.loadHotLeads();
  }
  const kept = () => {
    let pairs = [];
    try { pairs = JSON.parse(store.getItem("spb.leadcompare.v1") || "[]"); } catch (e) { pairs = []; }
    return new Map(pairs.map(p => [p[0], p[1].map(r => r.content_hash).join()]));
  };
  tabs[0].change(tickEvent("call-x", "hx", true));
  tabs[1].change(tickEvent("call-y", "hy", true));
  check("two tabs: a pick ticked in one tab is not dropped when the other saves its own",
    kept().get("call-x") === "hx" && kept().get("call-y") === "hy" && kept().get("call-old") === "h-old",
    store.getItem("spb.leadcompare.v1"));
  tabs[1].api.hlClearPicks(1);
  check("two tabs: Clear in one tab forgets only its own lead's picks",
    kept().get("call-x") === "hx" && !kept().has("call-y") && kept().get("call-old") === "h-old",
    store.getItem("spb.leadcompare.v1"));
  tabs[0].change(tickEvent("call-x", "hx", false));
  check("two tabs: unticking a lead's last pick takes only that lead out of the store",
    !kept().has("call-x") && kept().get("call-old") === "h-old" && kept().size === 1, store.getItem("spb.leadcompare.v1"));
  tabs[1].change(tickEvent("call-y", "hy", true));
  tabs[0].change(tickEvent("call-x", "hx", true));
  tabs[1].change(tickEvent("call-y", "hy", true));
  check("two tabs: a lead written again keeps its place in the store, and nothing else moves",
    [...kept().keys()].join() === "call-old,call-y,call-x", store.getItem("spb.leadcompare.v1"));
}

console.log("\n--- Open Report: the Research tab's report, for this lead, with no contact details ---");
{
  const B = over => api.hlReportBrief(clean(over));
  check("hlReportBrief: the customer's name, the suburbs and state, the budget",
    JSON.stringify(B()) === JSON.stringify({ client: "Jo Citizen", pref: "Tarneit, VIC", budget: 700000 }), JSON.stringify(B()));
  check("hlReportBrief: the brief's client_name when no name was captured",
    B({ customer: { name: "", email: "", phone: "" } }).client === "Jo Citizen");
  check("hlReportBrief: no budget stated is 0", B({ brief: { ...clean().brief, budget_max: null } }).budget === 0);
  check("hlReportBrief: the no-ceiling budget is 0, never 9,007,199,254,740,991",
    B({ brief: { ...clean().brief, budget_max: Number.MAX_SAFE_INTEGER } }).budget === 0);
  check("hlReportBrief: a budget written as a string", B({ brief: { ...clean().brief, budget_max: "750000" } }).budget === 750000);
  check("hlReportBrief: suburbs given as one string, and a state alone",
    B({ brief: { primary_suburbs: "Tarneit" } }).pref === "Tarneit" && B({ brief: { state: "QLD" } }).pref === "QLD");
  check("hlReportBrief: nothing at all", JSON.stringify(api.hlReportBrief({})) === JSON.stringify({ client: "", pref: "", budget: 0 })
    && JSON.stringify(api.hlReportBrief(null)) === JSON.stringify({ client: "", pref: "", budget: 0 }));
  const spoken = api.hlReportBrief(clean({
    customer: { name: "Jo jo.citizen@example.com 0412 345 678", email: "jo.citizen@example.com", phone: "0412345678" },
    brief: { ...clean().brief, primary_suburbs: ["Tarneit", "ring +61 412 345 678"] } }));
  check("a name that is an email or phone is taken out of the report brief",
    spoken.client === "Jo" && !/@|412/.test(JSON.stringify(spoken)), JSON.stringify(spoken));
  check("the report brief is only client, pref and budget", Object.keys(api.hlReportBrief(clean())).join() === "client,pref,budget");
}
{
  const s = sandbox("", { localStorage: memoryStorage() });
  const content = s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  // The Research tab's form, holding somebody else entirely: its last search.
  for (const [id, value] of [["clientName", "Form Client"], ["suburbs", "Werribee"], ["stateSelect", "VIC"],
                             ["budgetMax", "999000"]]) s.el(id, { value });
  // Joseph Shahid, the test lead in Coleen's report of 28 Sep: QLD, under $800k.
  const joseph = clean({ callId: "call-r",
    customer: { name: "Joseph Shahid", email: "joseph.shahid@example.com", phone: "+61 412 345 678" },
    brief: { ...clean().brief, client_name: "Joseph S", state: "QLD", primary_suburbs: ["Redbank Plains", "Ripley"],
             budget_max: 800000 },
    recommendations: [rec({ content_hash: "q1", suburb: "Ripley", state: "QLD", lot_address: "Lot 1 Ripley Rd" }),
                      rec({ content_hash: "q2", suburb: "Redbank Plains", state: "QLD", lot_address: "Lot 2 Redbank" }),
                      rec({ content_hash: "q3", state: "QLD", suburb: "Ripley", lot_address: "Lot 3 Ripley Rd" })] });
  s.respond(async url => url === "/api/narrative" ? reply(200, { drafts: [] })
    : reply(200, { status: "success", contact: "full", contactUnlock: "password", leads: [joseph] }));
  await s.api.loadHotLeads();
  check("(the answer said full, so this card is unlocked)", content.innerHTML.includes('href="mailto:joseph.shahid@example.com"'));
  await s.api.hlOpenReport(0);
  const doc = s.calls.windows[0] ? s.calls.windows[0].html : "";
  check("Open Report with nothing ticked: one report over the top 3 the card shows",
    s.calls.windows.length === 1 && (doc.match(/<tr/g) || []).length === 4, String((doc.match(/<tr/g) || []).length));
  check("the report is for the lead, not whoever the Research form holds",
    doc.includes("Prepared for Joseph Shahid") && doc.includes("Client preference: Redbank Plains, Ripley, QLD")
    && doc.includes("Target budget: under $800,000") && !doc.includes("Form Client") && !doc.includes("Werribee")
    && !doc.includes("999,000"), (doc.match(/<div class="sub">[^<]*/) || [""])[0]);
  check("the report carries no email and no phone of the customer",
    !doc.includes("joseph.shahid") && !doc.replace(/\s/g, "").includes("412345678") && !doc.includes("mailto:joseph"));
  const frag = (doc.match(/id="shareUrl" readonly value="[^"#]*#([^"]+)"/) || [])[1];
  const packed = frag ? await s.api.unpackShare(frag) : null;
  check("the client link it offers carries the lead's brief and no contact details",
    !!packed && packed.c === "Joseph Shahid" && packed.b && packed.b.pref === "Redbank Plains, Ripley, QLD"
    && !JSON.stringify(packed).includes("joseph.shahid") && !JSON.stringify(packed).replace(/\s/g, "").includes("412345678"),
    packed ? JSON.stringify(packed).slice(0, 200) : "no link in the report");
  check("the report's draft notes are asked of /api/narrative, the Research tab's endpoint",
    s.calls.fetch.some(c => c.url === "/api/narrative"));

  s.el("hlReport-0"); s.el("hlClear-0");
  s.change(tickEvent("call-r", "q2", true));
  await s.api.hlOpenReport(0);
  const doc2 = s.calls.windows[1] ? s.calls.windows[1].html : "";
  check("with a tick, the report covers exactly what was ticked",
    (doc2.match(/<tr/g) || []).length === 2 && doc2.includes("Lot 2 Redbank") && !doc2.includes("Lot 1 Ripley Rd"));
  check("a lead's report never touched SELECTED", s.ctx.SELECTED.size === 0);

  // The Research tab's own call, unchanged: no fourth argument, so the form is read.
  await s.api.openGeneratedReport("compare", [api.slim(rec({ content_hash: "z" }))], s.api.comparisonDocument);
  const doc3 = s.calls.windows[2] ? s.calls.windows[2].html : "";
  check("the Research tab's call (no 4th argument) still reads its form, exactly as before",
    doc3.includes("Prepared for Form Client") && doc3.includes("Client preference: Werribee, VIC")
    && doc3.includes("Target budget: under $999,000"), (doc3.match(/<div class="sub">[^<]*/) || [""])[0]);
  await s.api.openGeneratedReport("compare", [api.slim(rec({ content_hash: "z" }))], s.api.comparisonDocument,
    { client: "X", pref: "Y", budget: Number.MAX_SAFE_INTEGER });
  const doc4 = s.calls.windows[3] ? s.calls.windows[3].html : "";
  check("a no-ceiling budget handed to openGeneratedReport is never printed",
    doc4.includes("Prepared for X") && !doc4.includes("Target budget") && !doc4.includes("9,007,199"));
  check("every fetch went to a dashboard endpoint", s.calls.fetch.every(c => ALLOWED_FETCH.has(c.url)));
}
{
  const s = sandbox("");
  s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  s.respond(async () => reply(200, { status: "success", leads: [clean({ recommendations: [], research: { message: "none" } })] }));
  await s.api.loadHotLeads();
  const msg = s.el("hlMsg-0");
  s.api.hlOpenReport(0);
  check("nothing to report on: the card says so, and no window opens",
    s.calls.windows.length === 0 && /no buildings to put in a report/.test(msg.textContent));
  s.api.hlOpenReport(7);
  check("Open Report on an index with no lead does nothing", s.calls.windows.length === 0);
}

console.log("\n--- contact details are masked unless unlocked ---");
{
  out = api.hlLeadCard(clean(), 0);
  auditMarkup("masked card: markup", out);
  check("masked by default: no mailto and no tel link", !out.includes("mailto:") && !out.includes("tel:"));
  check("masked: neither the email nor the number is printed",
    !out.includes("jo.citizen@example.com") && !out.includes("412345678") && !out.includes("+614"));
  check("masked: 'Email on file' and 'Mobile ending 678'", out.includes("Email on file") && out.includes("Mobile ending 678"));
  check("masked: no recording link and no Email this client",
    !out.includes("Call recording") && !out.includes("Email this client"));
  check("masked: the Kommo link stays", />Open in Kommo<\/a>/.test(out));
  const fromServer = clean({ recordingUrl: "",
    customer: { name: "Jo Citizen", email: "", phone: "", phoneLast3: "678", hasEmail: true, hasPhone: true } });
  out = api.hlLeadCard(fromServer, 0, false, { contact: "masked" });
  check("the server's masked record: 'Mobile ending 678' and 'Email on file'",
    out.includes("Mobile ending 678") && out.includes("Email on file") && !out.includes("mailto:"));
  out = api.hlLeadCard(clean({ customer: { name: "A", email: "", phone: "", phoneLast3: "", hasEmail: false, hasPhone: true } }), 0);
  check("a number with no last 3 to show is 'Phone on file'", out.includes("Phone on file") && !out.includes("Mobile ending"));
  out = api.hlLeadCard(clean({ customer: { name: "A", email: "", phone: "", phoneLast3: "12345678", hasPhone: true } }), 0);
  check("a phoneLast3 that is not 3 digits is not shown", !out.includes("12345678") && out.includes("Phone on file"));
  out = api.hlLeadCard(clean({ customer: { name: "A", email: "", phone: "+61 412 999 555" } }), 0);
  check("a raw phone (a server that did not mask) shows only its last 3 digits",
    out.includes("Mobile ending 555") && !out.includes("999") && !out.includes("412"));
  out = api.hlLeadCard(clean({ customer: { name: "A", email: "", phone: "" } }), 0);
  check("masked with nothing captured says so", out.includes("No email captured") && out.includes("No phone captured"));
  out = api.hlLeadCard(hostile(), 0);
  auditMarkup("masked hostile card: markup", out);
  check("masked hostile card: no contact text and no link",
    !out.includes("mailto:") && !out.includes("tel:") && !out.includes("onmouseover") && out.includes("Mobile ending 678"));
  for (const v of ["FULL", "yes", true, 1, undefined])
    check("view contact " + JSON.stringify(v) + " is masked",
      !api.hlLeadCard(clean(), 0, false, { contact: v }).includes("mailto:"));
}
{
  out = api.hlLeadCard(clean(), 0, false, { contact: "full" });
  auditMarkup("unlocked card: markup", out);
  const mailtos = [...out.matchAll(/href="(mailto:[^"]*)"/g)].map(m => decode(m[1]));
  check("unlocked: the email is a mailto link, with Email this client beside it",
    mailtos.length === 2 && mailtos[0] === "mailto:jo.citizen@example.com"
    && /<a class="hl-mail-client" href="[^"]*">Email this client<\/a>/.test(out), JSON.stringify(mailtos));
  let u = null;
  try { u = new URL(mailtos[1]); } catch (e) { u = null; }
  check("Email this client: subject and body are fixed words, to the same address",
    !!u && u.pathname === "jo.citizen@example.com"
    && u.searchParams.get("subject") === "Your property options from Smart Property Buying"
    && /^Hi,\n\nAs discussed, here are the property options/.test(u.searchParams.get("body") || "")
    && [...u.searchParams.keys()].join() === "subject,body", mailtos[1]);
  out = api.hlLeadCard(clean({ customer: { name: "Jo&cc=boss@x.com", email: "jo.citizen@example.com", phone: "" } }), 0, false, { contact: "full" });
  check("Email this client never carries the record's text",
    !/href="mailto:[^"]*(Jo|boss)/.test(out.replace('href="mailto:jo.citizen@example.com"', "")));
  check("unlocked: the tel link", api.hlLeadCard(clean(), 0, false, { contact: "full" }).includes('href="tel:+61412345678"'));
  out = api.hlLeadCard(hostile(), 0, false, { contact: "full" });
  check("unlocked, an address hlMailHref refuses gets neither link", !out.includes("mailto:") && !out.includes("Email this client"));
}
{
  const s = sandbox(undefined, { localStorage: memoryStorage() });
  const content = s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  let answer = { status: "success", contact: "masked", contactUnlock: "password", leads: [clean()] };
  let unlockReply = () => reply(403, { status: "error", message: "That contact-details password is not right." });
  const posted = [];
  s.respond(async (url, opts) => {
    if (url === "/api/contact_unlock") { posted.push(JSON.parse(opts.body)); return unlockReply(); }
    return reply(200, answer);
  });
  await s.api.loadHotLeads();
  let h = content.innerHTML;
  auditMarkup("masked list with the unlock form: markup", h);
  check("masked + a password set: the unlock form is drawn",
    h.includes('<form class="hl-unlock" onsubmit="hlUnlock(); return false;">') && h.includes('type="password" id="hlUnlockPass"')
    && h.includes("only the last 3") && !h.includes("hlLock()"));
  check("masked list: no mailto or tel anywhere", !h.includes("mailto:") && !h.includes("tel:"));
  const pass = s.el("hlUnlockPass", { value: "wrong guess" });
  const ubtn = s.el("hlUnlockBtn"), amsg = s.el("hlAccessMsg");
  const n0 = s.calls.fetch.length;
  await s.api.hlUnlock();
  check("Unlock POSTs the password to /api/contact_unlock as JSON",
    s.calls.fetch[n0] && s.calls.fetch[n0].url === "/api/contact_unlock" && s.calls.fetch[n0].opts.method === "POST"
    && JSON.stringify(posted[0]) === JSON.stringify({ password: "wrong guess" }));
  check("the password box is emptied, right or wrong", pass.value === "");
  check("403: the server's message is shown, and it is not an expired sign-in",
    amsg.textContent === "That contact-details password is not right." && /err/.test(amsg.className)
    && s.calls.expired === 0 && ubtn.disabled === false);
  check("403: the list is not reloaded", s.calls.fetch.length === n0 + 1);
  await s.api.hlUnlock();
  check("an empty password sends nothing", s.calls.fetch.length === n0 + 1 && /password first/.test(amsg.textContent));
  unlockReply = () => reply(503, { status: "error", message: "Contact details are locked: no contact-details password is set on this deployment." });
  pass.value = "x";
  await s.api.hlUnlock();
  check("503: the message says no password is set, and it is not an expired sign-in",
    amsg.textContent.includes("no contact-details password is set") && s.calls.expired === 0);
  unlockReply = () => { throw new TypeError("Failed to fetch"); };
  pass.value = "x";
  await s.api.hlUnlock();
  check("unreachable: says so", /Could not reach the dashboard/.test(amsg.textContent) && ubtn.disabled === false);
  unlockReply = () => reply(401, { error: "auth_required" });
  pass.value = "x";
  await s.api.hlUnlock();
  check("401 from unlock is the sign-in expiring", s.calls.expired === 1);

  unlockReply = () => reply(200, { status: "success", contact: "full" });
  answer = { status: "success", contact: "full", contactUnlock: "password", leads: [clean()] };
  pass.value = "right one";
  const n1 = s.calls.fetch.length;
  await s.api.hlUnlock();
  h = content.innerHTML;
  check("the right password reloads the list, unlocked",
    s.calls.fetch.length === n1 + 2 && s.calls.fetch[n1 + 1].url === "/api/hot_leads?limit=200"
    && h.includes('href="mailto:jo.citizen@example.com"') && h.includes('href="tel:+61412345678"'));
  check("unlocked: a Lock button where the form was", h.includes('onclick="hlLock()"') && !h.includes("hlUnlockPass"));
  check("the password is nowhere in the page", !h.includes("right one") && !h.includes("wrong guess"));
  auditMarkup("unlocked list: markup", h);
  unlockReply = () => reply(200, { status: "success", contact: "masked" });
  answer = { status: "success", contact: "masked", contactUnlock: "password", leads: [clean()] };
  s.el("hlLockBtn");
  const n2 = s.calls.fetch.length;
  await s.api.hlLock();
  check("Lock POSTs {lock:true} and reloads, masked again",
    JSON.stringify(posted[posted.length - 1]) === JSON.stringify({ lock: true }) && s.calls.fetch.length === n2 + 2
    && !content.innerHTML.includes("mailto:"));
  answer = { status: "success", contact: "masked", contactUnlock: "not-configured", leads: [clean()] };
  await s.api.loadHotLeads(true);
  check("no password set: a note, and no password box",
    content.innerHTML.includes("No contact-details password is set on this dashboard") && !content.innerHTML.includes("hlUnlockPass"));
  for (const odd of ["yes", "FULL", true, undefined]) {
    answer = { status: "success", contact: odd, contactUnlock: "password", leads: [clean()] };
    await s.api.loadHotLeads(true);
    check("a contact value other than exactly 'full' is masked: " + JSON.stringify(odd),
      !content.innerHTML.includes("mailto:") && s.api.access().contact === "masked");
  }
  check("every fetch went to a dashboard endpoint", s.calls.fetch.every(c => ALLOWED_FETCH.has(c.url)));
}
{
  // Two reads (the answer was cut off): full contact only if BOTH said full.
  const s = sandbox();
  s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  const many = Array.from({ length: 200 }, (_, i) => clean({ callId: "m" + i, grade: "nurture", appointment: null }));
  s.respond(async url => url.includes("grade=hot")
    ? reply(200, { status: "success", contact: "masked", leads: [clean({ callId: "hot-old" })] })
    : reply(200, { status: "success", contact: "full", leads: many }));
  await s.api.loadHotLeads();
  check("two reads, one masked: the list is masked (half masked is masked)", s.api.access().contact === "masked");
}

console.log("\n--- notes and links per lead ---");
{
  const note = (over = {}) => ({ id: "n1", at: "2026-09-27T01:00:00Z", by: "Coleen", action: "package_sent",
                                 text: "Sent the QLD package.", ...over });
  const link = (over = {}) => ({ id: "l1", at: "2026-09-27T02:00:00Z", by: "Colin",
                                 url: "https://drive.google.com/drive/folders/abc", label: "Client folder", ...over });
  const store = memoryStorage();
  const s = sandbox(undefined, { localStorage: store });
  const content = s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  const lead = clean({ callId: "call-n" });
  const errored = clean({ callId: "call-e", createdAt: "2026-09-01T00:00:00Z", customer: { name: "Errored", email: "", phone: "" } });
  const notes = { "call-n": {
    entries: [note({ id: "old", at: "2026-09-20T00:00:00Z", action: "follow_up", text: "Ring back Friday" }), note(),
              note({ id: "gone", removed: true, text: "should not show" }),
              note({ id: "evil", at: "2026-09-10T00:00:00Z", by: EVIL, action: EVIL, text: EVIL })],
    links: [link(), link({ id: "bad", at: "2026-09-26T00:00:00Z", url: "javascript:alert(1)", label: "Evil link" }),
            link({ id: "nolabel", at: "2026-09-25T00:00:00Z", label: "", url: "https://example.com/some/long/path" })],
    rev: "3" } };
  let notesReply = () => reply(500, {});
  const sent = [];
  s.respond(async (url, opts) => {
    if (url === "/api/lead_notes") { const bdy = JSON.parse(opts.body); sent.push(bdy); return notesReply(bdy); }
    return reply(200, { status: "success", contact: "masked", contactUnlock: "password", leads: [lead, errored],
                        notes, notesErrors: ["call-e"] });
  });
  await s.api.loadHotLeads();
  const h = content.innerHTML;
  auditMarkup("notes and links: markup is only the renderer's own", h);
  check("notes from /api/hot_leads are drawn on their lead, newest first",
    h.indexOf("Sent the QLD package.") > 0 && h.indexOf("Sent the QLD package.") < h.indexOf("Ring back Friday"));
  check("a note shows its action, when and who",
    h.includes('<span class="hl-note-action">Package sent</span>') && /Sun 27 Sept?, 11:00 am · by Coleen/.test(h));
  check("a removed note is not drawn", !h.includes("should not show"));
  check("hostile notes render as text: text, author and an unknown action",
    h.includes(`<div class="hl-note-text">${EVIL_TEXT}</div>`) && h.includes("by " + EVIL_TEXT) && !/<script/i.test(h));
  check("a stored link is drawn through the safe link helper",
    /<a href="https:\/\/drive\.google\.com\/drive\/folders\/abc" target="_blank" rel="noopener noreferrer" title="[^"]*">Client folder<\/a>/.test(h));
  check("a javascript: link stored in the notes is not a link",
    !/href="\s*javascript/i.test(h) && h.includes("Evil link (link unavailable)"));
  check("a link with no label shows where it goes", h.includes(">example.com/some/long/path</a>"));
  check("each note and link has its Remove button, by position",
    h.includes('onclick="hlRemoveNote(0, 0)"') && h.includes('onclick="hlRemoveNote(0, 2)"')
    && !h.includes("hlRemoveNote(0, 3)") && h.includes('onclick="hlRemoveLink(0, 2)"'));
  check("a lead whose notes could not be read says so, and the other does not",
    (h.match(/could not be read just now/g) || []).length === 1
    && h.indexOf("could not be read just now") > h.indexOf('id="hlCard-1"'));
  check("the five quick actions are on the card",
    ["Package sent", "Call booked with customer", "Follow-up required", "Google Drive link added",
     "Property saved for the customer"].every(t => h.includes(">" + t + "</button>")));
  check("a note box, a link box and a label box",
    h.includes('<textarea class="hl-note-input" id="hlNoteText-0" rows="2" maxlength="2000"')
    && h.includes('<input type="url" id="hlLinkUrl-0" maxlength="2048"') && h.includes('id="hlLinkLabel-0" maxlength="120"'));
  check("the name the notes are saved under is asked above the list",
    h.includes('id="hlBy" maxlength="40"'));

  const ta = s.el("hlNoteText-0", { value: "" }), msg = s.el("hlNoteMsg-0"), save = s.el("hlNoteSave-0");
  const list = s.el("hlNoteList-0"), links = s.el("hlLinkList-0");
  const chips = Object.fromEntries(["package_sent", "call_booked", "follow_up", "drive_link", "property_saved"]
    .map(a => [a, s.el(`hlAct-0-${a}`)]));
  s.el("hlBy", { value: "  Coleen Smith  " });
  const n0 = s.calls.fetch.length;
  await s.api.hlSaveNote(0);
  check("an empty note is not sent", s.calls.fetch.length === n0 && /Choose what was done/.test(msg.textContent));
  s.api.hlPickAction(0, "call_booked");
  check("a quick action is pressed, and only that one",
    chips.call_booked.attrs["aria-pressed"] === "true" && chips.call_booked.className.includes("is-active")
    && chips.package_sent.attrs["aria-pressed"] === "false");
  s.api.hlPickAction(0, "call_booked");
  check("pressed again, it is let go", chips.call_booked.attrs["aria-pressed"] === "false");
  s.api.hlPickAction(0, "other');alert(1);//");
  check("an action outside the five is ignored", Object.values(chips).every(c => c.attrs["aria-pressed"] === "false"));
  s.api.hlPickAction(0, "follow_up");
  ta.value = "  Wants Ripley options <b>asap</b>  ";
  const fresh = { entries: [note({ id: "new1", at: "2026-09-28T00:00:00Z", action: "follow_up",
                                   text: "Wants Ripley options <b>asap</b>", by: "Coleen Smith" }), ...notes["call-n"].entries],
                  links: notes["call-n"].links, rev: "4" };
  notesReply = () => reply(200, { status: "success", callId: "call-n", notes: fresh });
  await s.api.hlSaveNote(0);
  // 28 Sep: every Save and every Add carries a requestId of its own (the hook's
  // [A-Za-z0-9_-]{1,64}), so an automatic retry of that press can never store it twice.
  const noRid = o => { const { requestId, ...rest } = o || {}; return rest; };
  const RID = /^[A-Za-z0-9_-]{1,64}$/;
  // The requestId of the k-th body sent, or null when there is none in the hook's pattern.
  const ridAt = k => (sent[k] && typeof sent[k].requestId === "string" && RID.test(sent[k].requestId))
    ? sent[k].requestId : null;
  check("Save note POSTs {callId, op:add_entry, action, text, by, requestId} to /api/lead_notes",
    JSON.stringify(noRid(sent[0])) === JSON.stringify({ callId: "call-n", op: "add_entry", action: "follow_up",
                                                        text: "Wants Ripley options <b>asap</b>", by: "Coleen Smith" })
    && s.calls.fetch[s.calls.fetch.length - 1].opts.method === "POST", JSON.stringify(sent[0]));
  check("Save note carries a requestId, last, in the hook's pattern",
    ridAt(0) !== null && Object.keys(sent[0] || {}).pop() === "requestId", JSON.stringify(sent[0]));
  check("the list is redrawn from the answer, newest first, the note escaped",
    list.innerHTML.includes("Wants Ripley options &lt;b&gt;asap&lt;/b&gt;")
    && list.innerHTML.indexOf("Wants Ripley") < list.innerHTML.indexOf("Sent the QLD package."));
  check("after saving: the box emptied, the action let go, 'Saved.'",
    ta.value === "" && chips.follow_up.attrs["aria-pressed"] === "false" && msg.textContent === "Saved.");
  check("the name is remembered in this browser", store.getItem("spb.leadnotes.by.v1") === "Coleen Smith");
  check("the save button is usable again", save.disabled === false);
  ta.value = "Just a note";
  await s.api.hlSaveNote(0);
  check("text alone is saved as action 'other'", sent[1] && sent[1].action === "other" && sent[1].text === "Just a note");
  check("a fresh requestId for every press of Save",
    ridAt(1) !== null && ridAt(1) !== ridAt(0), JSON.stringify([ridAt(0), ridAt(1)]));
  ta.value = "x".repeat(2001);
  const n1 = s.calls.fetch.length;
  await s.api.hlSaveNote(0);
  check("a note over 2,000 characters is not sent", s.calls.fetch.length === n1 && /2,000/.test(msg.textContent));
  ta.value = "Second consultant typed this";
  notesReply = () => reply(409, { status: "error", message: "Another save changed this lead's notes." });
  await s.api.hlSaveNote(0);
  check("409 keeps the note in the box and says someone else saved at the same moment",
    ta.value === "Second consultant typed this" && /at the same moment/.test(msg.textContent) && /err/.test(msg.className));
  notesReply = () => reply(503, { status: "error", message: "SPB_SERVICE_KEY is not set on the dashboard project" });
  await s.api.hlSaveNote(0);
  check("503 shows the server's message and keeps the note",
    msg.textContent.includes("SPB_SERVICE_KEY is not set") && ta.value === "Second consultant typed this" && s.calls.expired === 0);
  check("503 is 'Not saved': a missing setting is refused before anything is written",
    msg.textContent === "Not saved: SPB_SERVICE_KEY is not set on the dashboard project. What you typed is still here.",
    msg.textContent);
  notesReply = () => reply(404, { status: "error", message: "No such lead." });
  await s.api.hlSaveNote(0);
  check("404 shows the server's message", msg.textContent.includes("No such lead."));
  // A DROPPED CONNECTION (28 Sep). fetch throwing does not say the note never arrived, so the
  // page sends it once more by itself -- the same bytes, the same requestId, which the hook
  // adds at most once -- and, if that drops too, says it could not confirm, never "not saved".
  notesReply = () => { throw new TypeError("Failed to fetch"); };
  const n6 = sent.length;
  await s.api.hlSaveNote(0);
  check("unreachable: sent once more, automatically, with the same requestId",
    sent.length === n6 + 2 && ridAt(n6) !== null
    && JSON.stringify(sent[n6]) === JSON.stringify(sent[n6 + 1]), JSON.stringify(sent.slice(n6)));
  check("unreachable: could not confirm, press Save again, and the note is kept",
    msg.textContent === "Could not confirm this was saved: the dashboard could not be reached. Press Save again: "
      + "if it did arrive, it will not be saved twice. What you typed is still here."
    && /err/.test(msg.className) && ta.value === "Second consultant typed this" && save.disabled === false, msg.textContent);
  // 28 Sep review: "press Save again" is only safe if it is the same request. The same words
  // pressed again carry the id of the press that could not be confirmed; changed words do not.
  const n6b = sent.length;
  await s.api.hlSaveNote(0);
  check("Save again after 'could not confirm': the same requestId as the press before",
    sent.length === n6b + 2 && ridAt(n6b) === ridAt(n6) && ridAt(n6b + 1) === ridAt(n6),
    JSON.stringify(sent.slice(n6).map(x => x.requestId)));
  ta.value = "Second consultant typed this, edited";
  const n6c = sent.length;
  await s.api.hlSaveNote(0);
  check("an edited note after 'could not confirm' is a new request",
    ridAt(n6c) !== null && ridAt(n6c) !== ridAt(n6), JSON.stringify([ridAt(n6), ridAt(n6c)]));
  s.api.hlPickAction(0, "package_sent");
  const n6d = sent.length;
  await s.api.hlSaveNote(0);
  check("the same words with another action are a new request too",
    ridAt(n6d) !== null && ridAt(n6d) !== ridAt(n6c), JSON.stringify([ridAt(n6c), ridAt(n6d)]));
  s.api.hlPickAction(0, "package_sent");
  let drops = 1;
  notesReply = () => {
    if (drops-- > 0) throw new TypeError("Failed to fetch");
    return reply(200, { status: "success", notes: fresh });
  };
  ta.value = "Dropped once";
  const n7 = sent.length;
  await s.api.hlSaveNote(0);
  check("a connection that drops once: the retry, with the same requestId, is what saves it",
    sent.length === n7 + 2 && ridAt(n7) !== null && ridAt(n7) === ridAt(n7 + 1) && sent[n7].text === "Dropped once"
    && msg.textContent === "Saved." && ta.value === "", JSON.stringify(sent.slice(n7)) + " " + msg.textContent);
  ta.value = "Next press";
  await s.api.hlSaveNote(0);
  check("the press after it has a requestId of its own",
    sent.length === n7 + 3 && sent[n7 + 2].text === "Next press" && ridAt(n7 + 2) !== null
    && ridAt(n7 + 2) !== ridAt(n7), JSON.stringify(sent.slice(n7)));

  // AN ANSWER THAT LEAVES IT OPEN (28 Sep). "Not saved" here sent the consultant to Save again
  // over a note that was stored, and it was stored twice. None of these is retried: the
  // server answered, and what it said is shown.
  const unsureSaid = t => /^Could not confirm this was saved: .+[^.]\. Press Save again: if it did arrive, it will not be saved twice\. What you typed is still here\.$/.test(t);
  ta.value = "Maybe saved";
  for (const [label, answer] of [
    ["the hook's 502 (reload the lead to see whether it was saved)", () => reply(502, { status: "error",
      message: "lead-notes store answered HTTP 502: the lead-notes store did not answer properly; reload the lead to see whether the change was saved" })],
    ["a 502 with no message", () => reply(502, new Error("not JSON"))],
    ["a 504 from in front of the dashboard", () => reply(504, new Error("not JSON"))],
    ["a 500", () => reply(500, { status: "error", message: "api/lead_notes failed (KeyError)" })],
    ["a 200 that is not the notes", () => reply(200, new Error("not JSON"))],
  ]) {
    notesReply = answer;
    const k = sent.length;
    await s.api.hlSaveNote(0);
    check("may have saved, " + label + ": could not confirm, press Save again -- never 'Not saved' -- and the note is kept",
      unsureSaid(msg.textContent) && !/Not saved/i.test(msg.textContent) && /err/.test(msg.className)
      && ta.value === "Maybe saved" && sent.length === k + 1, msg.textContent);
  }
  // THE DASHBOARD'S 502 FOR AN ANSWER IT DOES NOT KNOW (28 Sep 2026, integration). A masking
  // failure is a 200 notesHidden now; what is left as a 502 is a hook answer of a shape nobody
  // sends, which says nothing about whether the note was stored -- so it is "could not
  // confirm", the words and their requestId kept, and pressing Save again is the same request.
  // (The second review's page read an older dashboard's "the note was saved, but ..." as a
  // save; that 502 is no longer sent by any dashboard this page ships with.)
  const nSaid = sent.length;
  notesReply = () => reply(502, { status: "error",
    message: "the lead-notes store answered in a shape the dashboard does not know (MaskError), so it cannot say whether the change was saved; press Refresh to check" });
  await s.api.hlSaveNote(0);
  check("the dashboard's 502 for an unknown answer: could not confirm, and the note is kept",
    unsureSaid(msg.textContent) && ta.value === "Maybe saved" && sent.length === nSaid + 1, msg.textContent);
  await s.api.hlSaveNote(0);
  check("... and Save again resends the same request",
    ridAt(nSaid + 1) !== null && ridAt(nSaid + 1) === ridAt(nSaid), JSON.stringify([ridAt(nSaid), ridAt(nSaid + 1)]));
  notesReply = () => reply(502, { status: "error",
    message: "the note was saved, but the notes could not be shown with their contact details hidden (MaskError); press Refresh" });
  await s.api.hlSaveNote(0);
  check("an old dashboard's 'was saved, but' 502 is read as could-not-confirm too, never as a save that empties the box",
    unsureSaid(msg.textContent) && ta.value === "Maybe saved", msg.textContent);
  ta.value = "Maybe saved";
  notesReply = () => reply(502, { status: "error",
    message: "lead-notes store answered HTTP 502: the notes stored for this lead cannot be read, so nothing was changed" });
  await s.api.hlSaveNote(0);
  check("a 502 that says nothing was changed is 'Not saved', and the note is kept",
    msg.textContent === "Not saved: lead-notes store answered HTTP 502: the notes stored for this lead cannot be read, "
      + "so nothing was changed. What you typed is still here." && ta.value === "Maybe saved", msg.textContent);
  ta.value = "Second consultant typed this";
  notesReply = () => reply(401, { error: "auth_required" });
  await s.api.hlSaveNote(0);
  check("401 on a note is the sign-in expiring", s.calls.expired === 1);

  // Every answer held until released, so a page that did send the second save cannot hang
  // the run waiting for it.
  const held = [];
  notesReply = () => new Promise(r => { held.push(() => r(reply(200, { status: "success", notes: fresh }))); });
  const first = s.api.hlSaveNote(0);
  await new Promise(r => setImmediate(r));
  const n2 = s.calls.fetch.length;
  const second = s.api.hlSaveNote(0);
  await new Promise(r => setImmediate(r));
  check("a second save while the first is out sends nothing",
    s.calls.fetch.length === n2 && /Still saving/.test(msg.textContent) && save.disabled === true);
  held.forEach(go => go());
  await first;
  await second;
  // From here a note or link that should never be sent is answered as the hook would a bad one.
  notesReply = () => reply(400, { status: "error", message: "That is not a note or link this lead can keep." });

  const url = s.el("hlLinkUrl-0", { value: "javascript:alert(1)" }), lab = s.el("hlLinkLabel-0", { value: "Folder" });
  const n3 = s.calls.fetch.length;
  await s.api.hlAddLink(0);
  check("a javascript: link is refused before sending", s.calls.fetch.length === n3 && /http:\/\/ or https:\/\//.test(msg.textContent));
  url.value = "https://user:secret@drive.google.com/x";
  await s.api.hlAddLink(0);
  check("a link carrying a user name or password is refused", s.calls.fetch.length === n3);
  url.value = "   ";
  await s.api.hlAddLink(0);
  check("no link: nothing sent", s.calls.fetch.length === n3 && /Paste the link first/.test(msg.textContent));
  url.value = " https://drive.google.com/drive/folders/xyz?usp=sharing ";
  notesReply = b => reply(200, { status: "success", notes: { entries: fresh.entries,
    links: [link({ id: "l9", at: "2026-09-28T03:00:00Z", url: b.url, label: b.label }), ...fresh.links] } });
  const nLink = sent.length;
  await s.api.hlAddLink(0);
  check("Add link POSTs {callId, op:add_link, url, label, by, requestId}",
    JSON.stringify(noRid(sent[sent.length - 1])) === JSON.stringify({ callId: "call-n", op: "add_link",
      url: "https://drive.google.com/drive/folders/xyz?usp=sharing", label: "Folder", by: "Coleen Smith" }),
    JSON.stringify(sent[sent.length - 1]));
  check("Add link carries a requestId of its own press",
    sent.length === nLink + 1 && ridAt(nLink) !== null
    && sent.slice(0, nLink).every(x => x.requestId !== ridAt(nLink)), JSON.stringify(sent[nLink]));
  check("the new link is drawn, and the boxes are emptied",
    links.innerHTML.includes('href="https://drive.google.com/drive/folders/xyz?usp=sharing"') && url.value === "" && lab.value === "");
  url.value = "https://drive.google.com/drive/folders/abc";
  notesReply = () => { throw new TypeError("Failed to fetch"); };
  const nL2 = sent.length;
  await s.api.hlAddLink(0);
  check("a link that could not be confirmed: press Add link again, and the link is kept",
    msg.textContent === "Could not confirm this was saved: the dashboard could not be reached. Press Add link again: "
      + "if it did arrive, it will not be saved twice. What you typed is still here."
    && url.value === "https://drive.google.com/drive/folders/abc", msg.textContent);
  await s.api.hlAddLink(0);
  check("Add link again after 'could not confirm': the same requestId",
    sent.length === nL2 + 4 && ridAt(nL2) !== null && ridAt(nL2 + 2) === ridAt(nL2) && ridAt(nL2) !== ridAt(nLink),
    JSON.stringify(sent.slice(nL2).map(x => x.requestId)));
  url.value = "";
  check("hlLinkToKeep: http(s) only, no credentials",
    api.hlLinkToKeep("https://a.example/x") === "https://a.example/x" && api.hlLinkToKeep("data:text/html,x") === ""
    && api.hlLinkToKeep("https://a:b@a.example/") === "" && api.hlLinkToKeep("https://a@a.example/") === "");

  // Out of order on purpose: the page draws newest first whatever order the store sends.
  notesReply = () => reply(200, { status: "success", notes: {
    entries: [note({ id: "old", at: "2026-09-20T00:00:00Z" }), note({ id: "new1", at: "2026-09-28T00:00:00Z" }), note({ id: "n1" })],
    links: [link({ id: "la", at: "2026-09-01T00:00:00Z" }), link({ id: "lb", at: "2026-09-02T00:00:00Z" })] } });
  ta.value = "reorder";
  await s.api.hlSaveNote(0);
  let asked = 0;
  s.ctx.confirm = () => { asked++; return false; };
  const n4 = s.calls.fetch.length;
  await s.api.hlRemoveNote(0, 1);
  check("Remove asks first, and Cancel sends nothing", asked === 1 && s.calls.fetch.length === n4);
  s.ctx.confirm = () => true;
  await s.api.hlRemoveNote(0, 1);
  check("Remove sends the id of the note drawn in that place (newest first)",
    JSON.stringify(sent[sent.length - 1]) === JSON.stringify({ callId: "call-n", op: "remove", id: "n1" }),
    JSON.stringify(sent[sent.length - 1]));
  await s.api.hlRemoveLink(0, 0);
  check("... and of the link drawn there",
    JSON.stringify(sent[sent.length - 1]) === JSON.stringify({ callId: "call-n", op: "remove", id: "lb" }),
    JSON.stringify(sent[sent.length - 1]));
  const n5 = s.calls.fetch.length;
  await s.api.hlRemoveNote(0, 9);
  await s.api.hlRemoveNote(0, -1);
  await s.api.hlRemoveLink(1, 0);
  check("a position with nothing drawn there sends nothing", s.calls.fetch.length === n5);
  notesReply = () => { throw new TypeError("Failed to fetch"); };
  const n8 = sent.length;
  await s.api.hlRemoveNote(0, 0);
  check("a Remove that could not reach the dashboard: sent once, no requestId, and not said to be undone",
    sent.length === n8 + 1 && !("requestId" in sent[n8])
    && msg.textContent === "Could not confirm the removal: the dashboard could not be reached. Press Refresh to check "
      + "whether it is gone.", msg.textContent);
  notesReply = () => reply(404, { status: "error", message: "No such lead." });
  await s.api.hlRemoveNote(0, 0);
  check("a Remove that was refused says 'Not removed'", msg.textContent === "Not removed: No such lead.", msg.textContent);

  // Typed and not saved, then the list reloads (Refresh, or an Unlock): nothing is lost.
  ta.value = "Typed, not saved <i>";
  url.value = "https://example.com/draft";
  lab.value = "Draft label";
  s.api.hlPickAction(0, "drive_link");
  await s.api.loadHotLeads(true);
  const h2 = content.innerHTML;
  check("a typed note survives a reload, escaped",
    h2.includes(">Typed, not saved &lt;i&gt;</textarea>") && h2.includes('value="https://example.com/draft"')
    && h2.includes('value="Draft label"') && /id="hlAct-0-drive_link" aria-pressed="true"/.test(h2));
  auditMarkup("a reload with drafts: markup", h2);
  // The link box's draft goes back into a value="..." attribute, so a quote typed or pasted
  // into it must not end the attribute when the list is drawn again.
  url.value = 'https://example.com/"><script>alert(1)</script>';
  lab.value = '"><b onmouseover="x">label</b>';
  await s.api.loadHotLeads(true);
  const h3 = content.innerHTML;
  check("a hostile draft link survives a reload, escaped",
    h3.includes('value="https://example.com/&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"')
    && h3.includes('value="&quot;&gt;&lt;b onmouseover=&quot;x&quot;&gt;label&lt;/b&gt;"') && !/<script/i.test(h3),
    (h3.match(/id="hlLinkUrl-0"[^>]*>/) || [""])[0]);
  auditMarkup("a reload with a hostile draft link: markup", h3);
  const everything = JSON.stringify([...store._map]);
  check("nothing about the customer is in localStorage",
    !everything.includes("jo.citizen@example.com") && !everything.includes("412345678") && !everything.includes("Jo Citizen")
    && !everything.includes("Typed, not saved"));
  check("every fetch went to a dashboard endpoint", s.calls.fetch.every(c => ALLOWED_FETCH.has(c.url)));

  out = api.hlLeadCard(clean({ callId: undefined }), 0, false, { contact: "masked" });
  check("a record with no call id says notes cannot be kept, and draws no note box",
    out.includes("has no call id") && !out.includes("hlNoteText-0"));

  // A stored link with no address at all: hlExtLink draws nothing, and the fallback beside it
  // prints the label -- which a consultant typed.
  const noUrl = api.hlLinksListHtml({ links: [link({ id: "nourl", url: "", label: EVIL }),
                                              link({ id: "nourl2", at: "2026-09-26T00:00:00Z", url: "", label: "" })] }, 0);
  check("a link with no URL: its label is text, marked unavailable",
    noUrl.includes(`<span class="hl-nolink">${EVIL_TEXT} (link unavailable)</span>`) && !/<script/i.test(noUrl), noUrl.slice(0, 300));
  check("a link with no URL and no label is 'Link (link unavailable)'",
    noUrl.includes('<span class="hl-nolink">Link (link unavailable)</span>'));
  auditMarkup("links with no URL: markup", noUrl);
}
{
  // The name remembered in this browser goes back into the box's value="..." attribute.
  const store = memoryStorage();
  store.setItem("spb.leadnotes.by.v1", '"><script>alert(1)</script>');
  const s = sandbox(undefined, { localStorage: store });
  const content = s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  s.respond(async () => reply(200, { status: "success", contact: "masked", contactUnlock: "password", leads: [clean()] }));
  await s.api.loadHotLeads();
  const h = content.innerHTML;
  check("a remembered name is escaped back into its box",
    h.includes('id="hlBy" maxlength="40" autocomplete="off" value="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"')
    && !/<script/i.test(h), (h.match(/id="hlBy"[^>]*>/) || [""])[0]);
  auditMarkup("a hostile remembered name: markup", h);
}
{
  // FINDING, 28 Sep: one unreadable notes object took the whole tab away from everyone
  // without the password (the server could not mask a null). The page's half: masked, with
  // every lead's notes unreadable -- the shape a failed listing of the notes store sends --
  // every lead is still listed, each says its notes could not be read, and nothing is unmasked.
  const s = sandbox(undefined, { localStorage: memoryStorage() });
  const content = s.el("hotLeadsContent"), badge = s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  const leads = [clean({ callId: "call-p" }),
                 clean({ callId: "call-q", createdAt: "2026-09-14T00:00:00Z", grade: "warm", appointment: null }),
                 clean({ callId: "wa-54494433", channel: "whatsapp", createdAt: "2026-09-13T00:00:00Z" })];
  s.respond(async () => reply(200, { status: "success", contact: "masked", contactUnlock: "password", leads,
                                     notes: { "call-p": null }, notesErrors: ["call-p", "call-q", "wa-54494433"] }));
  let threw = null;
  try { await s.api.loadHotLeads(); } catch (e) { threw = e; }
  const h = content.innerHTML;
  check("masked, with notes that could not be read: every lead is listed",
    !threw && [0, 1, 2].every(i => h.includes(`id="hlCard-${i}"`)) && /^3 leads/.test(badge.textContent),
    threw ? "threw " + threw : badge.textContent + " " + h.slice(0, 200));
  check("... each says its notes could not be read, and none says it has no notes",
    (h.match(/could not be read just now/g) || []).length === 3 && !h.includes("No notes yet."));
  check("... and nothing is unmasked", !h.includes("mailto:") && !h.includes("tel:") && !h.includes("jo.citizen@example.com"));
  auditMarkup("masked list with unreadable notes: markup", h);

  // notes[callId] === null is how the hook said "unreadable" before 28 Sep: it is that notice
  // even with notesErrors missing, never "No notes yet."
  s.respond(async () => reply(200, { status: "success", contact: "masked", leads: [clean({ callId: "call-p" })],
                                     notes: { "call-p": null } }));
  await s.api.loadHotLeads(true);
  check("a null notes value is said to be unreadable, not 'No notes yet.'",
    content.innerHTML.includes("could not be read just now") && !content.innerHTML.includes("No notes yet."));
  s.respond(async () => reply(200, { status: "success", contact: "masked", leads: [clean({ callId: "call-p" })],
                                     notes: { "call-p": [] }, notesErrors: "call-p" }));
  await s.api.loadHotLeads(true);
  check("notes that are not an object, and a notesErrors that is not a list, do not throw or draw an empty list",
    content.innerHTML.includes('id="hlCard-0"') && content.innerHTML.includes("could not be read just now")
    && !content.innerHTML.includes("No notes yet."));
}
{
  // 28 Sep integration review: ONE lead the server could not mask is cut down on its own
  // (api/_contact.py hard_masked: maskFailed) and its notes left out (notesHidden), instead of
  // a 502 that took the whole tab away. The page says what is missing and why, for that lead
  // only; every other lead is drawn as usual.
  const s = sandbox(undefined, { localStorage: memoryStorage() });
  const content = s.el("hotLeadsContent"), badge = s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  const cut = { callId: "call-cut", channel: "vapi", grade: "hot", createdAt: "2026-09-28T01:02:03.000Z",
                kommo: { leadUrl: "https://spb.kommo.com/leads/detail/21345678" },
                customer: { name: "[hidden]", email: "", phone: "", phoneLast3: "678", hasEmail: true, hasPhone: true },
                maskFailed: true };
  const ok = clean({ callId: "call-ok", createdAt: "2026-09-14T00:00:00Z",
                     customer: { name: "Jo Citizen", email: "", phone: "", phoneLast3: "678", hasEmail: true, hasPhone: true } });
  s.respond(async () => reply(200, { status: "success", contact: "masked", contactUnlock: "password",
    leads: [cut, ok], notes: {}, notesErrors: [], notesHidden: ["call-cut"] }));
  let threw = null;
  try { await s.api.loadHotLeads(); } catch (e) { threw = e; }
  const h = content.innerHTML;
  const card0 = h.slice(h.indexOf('id="hlCard-0"'), h.indexOf('id="hlCard-1"'));
  const card1 = h.slice(h.indexOf('id="hlCard-1"'));
  check("a cut-down lead: both leads are listed", !threw && /^2 leads/.test(badge.textContent) && card0 && card1,
    threw ? "threw " + threw : badge.textContent);
  check("... the cut-down one says why most of it is missing, and keeps what it has",
    card0.includes("could not be shown with its contact details hidden") && card0.includes("Mobile ending 678")
    && card0.includes("Open in Kommo") && card0.includes("&#91;hidden&#93;") === false && card0.includes("[hidden]"),
    card0.slice(0, 600));
  check("... its notes are said to need the password, not to be missing or unreadable",
    card0.includes("shown only with the contact-details password") && !card0.includes("No notes yet.")
    && !card0.includes("could not be read just now"));
  check("... and the other lead says neither",
    !card1.includes("could not be shown with its contact details hidden")
    && !card1.includes("shown only with the contact-details password") && card1.includes("No notes yet."));
  auditMarkup("a cut-down lead: markup", h);
  // 28 Sep 2026, second review: the notice has an id, so a save can take it down again.
  const HIDDEN_SAID = "The notes saved for this lead are held back with its contact details, so they are "
    + "shown only with the contact-details password.";
  check("... the hidden-notes notice is drawn with an id of its own",
    card0.includes(`id="hlNotesHidden-0">${HIDDEN_SAID}</div>`), card0.slice(card0.indexOf("hlNotesHidden-0") - 60, card0.indexOf("hlNotesHidden-0") + 200));
  // A save on that lead that comes back masked and whole: its notes are shown from then on.
  s.respond(async (url) => url === "/api/lead_notes"
    ? reply(200, { status: "success", callId: "call-cut", notes: { links: [], entries: [
        { id: "n9", at: "2026-09-28T02:00:00Z", by: "Coleen", action: "package_sent", text: "Package sent" }] } })
    : reply(200, { status: "success", contact: "masked", leads: [cut, ok] }));
  s.el("hlNoteText-0", { value: "Package sent" }); s.el("hlNoteMsg-0");
  const list0 = s.el("hlNoteList-0");
  // As the browser holds it after the card was drawn: showing, with its words.
  const hiddenBox = s.el("hlNotesHidden-0", { textContent: HIDDEN_SAID, hidden: false });
  await s.api.hlSaveNote(0);
  check("a masked answer to a save shows that lead's notes", list0.innerHTML.includes("Package sent"), list0.innerHTML.slice(0, 200));
  check("after a masked save shows a hidden lead's notes, the notice saying they are hidden is gone",
    hiddenBox.hidden === true && hiddenBox.textContent === "", JSON.stringify([hiddenBox.hidden, hiddenBox.textContent]));
  // The notice belongs to the masked view: the full view never says it, whatever a record holds.
  out = api.hlLeadCard(cut, 0, false, { contact: "full" });
  check("the full view never draws the cut-down notice", !out.includes("could not be shown with its contact details hidden"));
}
{
  // The requestId comes from the browser's crypto when it has one (the sandbox above has none,
  // so it used the fallback): the same shape either way.
  const s = sandbox(undefined, { localStorage: memoryStorage(), crypto: globalThis.crypto });
  s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  const sent = [];
  s.respond(async (url, opts) => {
    if (url === "/api/lead_notes") { sent.push(JSON.parse(opts.body)); return reply(200, { status: "success", notes: {} }); }
    return reply(200, { status: "success", contact: "masked", leads: [clean({ callId: "call-c" })] });
  });
  await s.api.loadHotLeads();
  const ta = s.el("hlNoteText-0", { value: "one" });
  s.el("hlNoteMsg-0");
  await s.api.hlSaveNote(0);
  ta.value = "two";
  await s.api.hlSaveNote(0);
  check("with crypto: a 32-hex requestId, fresh for each press",
    sent.length === 2 && sent.every(x => /^[0-9a-f]{32}$/.test(x.requestId)) && sent[0].requestId !== sent[1].requestId,
    JSON.stringify(sent.map(x => x.requestId)));
  // "Package sent" twice, a week apart, is two notes: once one is confirmed its id is dropped,
  // so the same action pressed again is a new request, not a duplicate the hook would skip.
  ta.value = "";
  s.api.hlPickAction(0, "package_sent");
  await s.api.hlSaveNote(0);
  s.api.hlPickAction(0, "package_sent");
  await s.api.hlSaveNote(0);
  check("the same quick action saved twice is two requests",
    sent.length === 4 && sent[2].action === "package_sent" && sent[3].action === "package_sent"
    && sent[2].requestId !== sent[3].requestId, JSON.stringify(sent.slice(2)));
}

// ------------------------------------------------------------------ 28 Sep 2026, second review
// A FAKE HOOK STORE, answering as the contract agreed for this round says the hook and the
// dashboard answer. An add is kept with its requestId; the same requestId again is answered 200
// with the notes as they stand plus duplicate:true, and duplicateRemoved:true as well when the
// item it matched has since been removed (C2) -- a removed item keeps its requestId on the hook.
// The notes view carries each LIVE item's requestId, and never sends a removed item (C1).
function fakeNotesStore() {
  const items = [];
  let n = 0;
  const live = () => items.filter(x => !x.removed);
  const shown = kind => live().filter(x => x.kind === kind).map(({ kind: _k, removed: _r, ...x }) => x);
  const view = () => ({ entries: shown("entry"), links: shown("link"), rev: String(items.length) });
  const post = body => {
    if (body.op === "remove") {
      const x = items.find(y => y.id === body.id);
      if (x) x.removed = true;
      return { status: "success", callId: body.callId, notes: view() };
    }
    const match = items.find(y => y.requestId === body.requestId);
    if (match) {
      return { status: "success", callId: body.callId, notes: view(), duplicate: true,
               ...(match.removed ? { duplicateRemoved: true } : {}) };
    }
    n++;
    const at = new Date(Date.UTC(2026, 8, 28, 0, n)).toISOString();
    items.push(body.op === "add_link"
      ? { kind: "link", id: "l" + n, at, by: body.by || "", url: body.url, label: body.label || "", requestId: body.requestId }
      : { kind: "entry", id: "e" + n, at, by: body.by || "", action: body.action, text: body.text, requestId: body.requestId });
    return { status: "success", callId: body.callId, notes: view() };
  };
  return { items, live, view, post };
}
// The dashboard's own answer when its request to the hook timed out, after the hook wrote.
const TIMED_OUT = () => reply(502, { status: "error",
  message: "could not reach the lead-notes store at hook.invalid (timed out)" });

// One lead on a masked list, its notes on the fake hook. lose(): the hook stores what it is sent
// but the answer never reaches the page (TIMED_OUT). gate(): each answer is held until released.
// 28 Sep repair -- offline: the fetch throws before the hook sees it. drop: that many fetches
// reach the hook, which stores them, and then throw. late: with gate, the hook stores it only
// when the answer is released. echoDup: the hook's retry inside one request, answered duplicate.
function notesRig(over = {}) {
  const s = sandbox(undefined, { localStorage: memoryStorage() });
  const content = s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  const hook = fakeNotesStore();
  const sent = [], held = [];
  const rig = { s, content, hook, sent, held, lose: false, gate: false, unlock: over.unlock || "password",
                offline: false, drop: 0, late: false, echoDup: false };
  s.respond(async (url, opts) => {
    if (url === "/api/lead_notes") {
      const bdy = JSON.parse(opts.body);
      sent.push(bdy);
      if (rig.offline) throw new TypeError("Failed to fetch");
      const answerOf = () => {
        const a = hook.post(bdy);
        return rig.echoDup && !a.duplicate ? { ...a, duplicate: true } : a;
      };
      if (rig.gate && rig.late) {
        const lose = rig.lose;
        return new Promise(r => held.push(() => { const a = answerOf(); r(lose ? TIMED_OUT() : reply(200, a)); }));
      }
      const answer = answerOf();
      if (rig.drop > 0) { rig.drop--; throw new TypeError("Failed to fetch"); }
      const out = rig.lose ? TIMED_OUT() : reply(200, answer);
      return rig.gate ? new Promise(r => held.push(() => r(out))) : out;
    }
    return reply(200, { status: "success", contact: "masked", contactUnlock: rig.unlock,
                        leads: [clean({ callId: "call-s" })], notes: { "call-s": hook.view() } });
  });
  return rig;
}
const rid = x => (x && typeof x.requestId === "string") ? x.requestId : null;

console.log("\n--- an unconfirmed press is settled, and 'it will not be saved twice' holds ---");
{
  const r = notesRig();
  const { s, content, hook, sent } = r;
  await s.api.loadHotLeads();
  const ta = s.el("hlNoteText-0", { value: "" }), msg = s.el("hlNoteMsg-0"), chip = s.el("hlAct-0-follow_up");
  // S1b. "Follow-up required" pressed and saved; stored, but the answer was lost.
  s.api.hlPickAction(0, "follow_up");
  r.lose = true;
  await s.api.hlSaveNote(0);
  const r1 = rid(sent[0]);
  check("S1b: the chip-only note is stored, its answer lost, and the page could not confirm it",
    hook.live().length === 1 && /^Could not confirm this was saved/.test(msg.textContent)
    && chip.attrs["aria-pressed"] === "true", msg.textContent);
  r.lose = false;
  await s.api.loadHotLeads(true);
  check("a Refresh that shows the unconfirmed press arrived: the chip is let go, and the card says it arrived",
    /id="hlAct-0-follow_up" aria-pressed="false"/.test(content.innerHTML)
    && msg.textContent === "The note that could not be confirmed did arrive: it is in the list below.",
    msg.textContent + " | " + (content.innerHTML.match(/id="hlAct-0-follow_up"[^>]*>/) || [""])[0]);
  s.api.hlPickAction(0, "follow_up");
  await s.api.hlSaveNote(0);
  check("... and the same quick action saved later is a new request, stored as a second note",
    sent.length === 2 && rid(sent[1]) !== r1 && hook.live().length === 2 && msg.textContent === "Saved.",
    JSON.stringify(sent.map(rid)) + " " + msg.textContent);

  // S1. Typed and saved, stored but not confirmed; a colleague removes it; Save pressed again, as
  // the page said. The hook matches the removed note's requestId and adds nothing.
  ta.value = "Left a voicemail";
  r.lose = true;
  await s.api.hlSaveNote(0);
  const k = sent.length - 1;
  hook.items.find(y => y.requestId === rid(sent[k])).removed = true;
  r.lose = false;
  await s.api.hlSaveNote(0);
  check("a press matched to a note since removed: says so, keeps the text, and is never called saved",
    rid(sent[k + 1]) === rid(sent[k])
    && msg.textContent === "This note was saved earlier and has since been removed from the lead, so it was not "
      + "added again. It is still here: press Save to add it anew."
    && ta.value === "Left a voicemail" && /err/.test(msg.className), msg.textContent);
  await s.api.hlSaveNote(0);
  check("... and the next press adds it anew, with a requestId of its own",
    rid(sent[k + 2]) !== rid(sent[k]) && hook.live().some(y => y.text === "Left a voicemail")
    && msg.textContent === "Saved." && ta.value === "", msg.textContent);

  // X, then Y, then X again, the first two unconfirmed.
  r.lose = true;
  ta.value = "Package sent";
  await s.api.hlSaveNote(0);
  const rX = rid(sent[sent.length - 1]);
  ta.value = "Package sent to Jo";
  await s.api.hlSaveNote(0);
  r.lose = false;
  ta.value = "Package sent";
  await s.api.hlSaveNote(0);
  check("X, then Y, then X again (both unconfirmed): X goes with X's requestId, and is stored once",
    rid(sent[sent.length - 1]) === rX && hook.live().filter(y => y.text === "Package sent").length === 1,
    JSON.stringify(hook.live().map(y => y.text)));
  // 28 Sep repair: that answer's list also shows Y arrived, so Y is settled by it and the card
  // says so -- the "Press Save again" said for Y is not left standing.
  check("a press the hook already had: 'Already saved.', the box emptied, and Y, which its list shows, said to have arrived",
    msg.textContent === "Already saved. An earlier note that could not be confirmed did arrive: it is in the list below."
    && ta.value === "", msg.textContent);
  ta.value = "Package sent to Jo";
  await s.api.hlSaveNote(0);
  check("... so Y typed again after that is a new note, not a repeat the hook would drop",
    hook.live().filter(y => y.text === "Package sent to Jo").length === 2 && msg.textContent === "Saved.", msg.textContent);
  ta.value = "Package sent";
  await s.api.hlSaveNote(0);
  check("... and once settled, the same words later are a new note",
    hook.live().filter(y => y.text === "Package sent").length === 2 && msg.textContent === "Saved.", msg.textContent);

  // The same for a link: unconfirmed, then seen in the list on a Refresh.
  const url = s.el("hlLinkUrl-0", { value: "https://drive.google.com/drive/folders/jo" });
  const lab = s.el("hlLinkLabel-0", { value: "Jo's folder" });
  r.lose = true;
  await s.api.hlAddLink(0);
  r.lose = false;
  await s.api.loadHotLeads(true);
  check("a Refresh that shows an unconfirmed link arrived: the link boxes are drawn empty, and the card says so",
    content.innerHTML.includes('id="hlLinkUrl-0" maxlength="2048" aria-label="Link to keep for this lead"'
      + ' placeholder="Google Drive or property link (https://...)" value=""')
    && msg.textContent === "The link that could not be confirmed did arrive: it is in the list below.", msg.textContent);
  check("every fetch went to a dashboard endpoint", s.calls.fetch.every(c => ALLOWED_FETCH.has(c.url)));
}

console.log("\n--- a masked save whose notes cannot be shown is a save ---");
{
  // C3: the masked view could not hide the answer's notes, so it sent none: 200, notesHidden.
  const s = sandbox(undefined, { localStorage: memoryStorage() });
  const content = s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  const sent = [];
  let unlock = "password";
  const visible = { entries: [{ id: "v1", at: "2026-09-27T00:00:00Z", by: "Coleen", action: "other", text: "Seen before" }], links: [] };
  s.respond(async (url, opts) => {
    if (url === "/api/lead_notes") {
      sent.push(JSON.parse(opts.body));
      return reply(200, { status: "success", callId: "call-h", notesHidden: true, contact: "masked" });
    }
    return reply(200, { status: "success", contact: "masked", contactUnlock: unlock,
                        leads: [clean({ callId: "call-h" }), clean({ callId: "call-v", createdAt: "2026-09-10T00:00:00Z" })],
                        notes: { "call-v": visible }, notesHidden: ["call-h"] });
  });
  await s.api.loadHotLeads();
  const ta = s.el("hlNoteText-0", { value: "Ring back Friday" }), msg = s.el("hlNoteMsg-0");
  await s.api.hlSaveNote(0);
  check("a masked save whose notes cannot be shown (notesHidden): 'Saved.', where they can be seen, the box emptied",
    msg.textContent === "Saved. This lead's notes are shown only with the contact-details password."
    && ta.value === "" && !/err/.test(msg.className), msg.textContent);
  check("... the lead's notes stay hidden",
    !s.api.notesMap().has("call-h") && s.api.hlLeadCard(s.api.leads()[0], 0).includes("shown only with the contact-details password"));
  ta.value = "Ring back Friday";
  await s.api.hlSaveNote(0);
  check("... and the same words pressed again are a new note, not the settled one's request",
    sent.length === 2 && rid(sent[1]) !== rid(sent[0]), JSON.stringify(sent.map(rid)));
  // A lead whose notes were showing, until a save's answer could not be masked: the lists go and
  // the notice comes, from the same paint.
  const vList = s.el("hlNoteList-1", { innerHTML: "Seen before" }), vHidden = s.el("hlNotesHidden-1", { hidden: true });
  s.el("hlNoteText-1", { value: "Mob 0400 or 111 then 678" }); s.el("hlNoteMsg-1");
  await s.api.hlSaveNote(1);
  check("a save that hides a lead's notes: its lists are emptied and the hidden-notes notice is drawn",
    vList.innerHTML === "" && vHidden.hidden === false
    && vHidden.textContent === "The notes saved for this lead are held back with its contact details, so they "
      + "are shown only with the contact-details password.", JSON.stringify([vList.innerHTML, vHidden.hidden, vHidden.textContent]));
  // No password set (contactUnlock "not-configured"): the same, without sending anyone for one.
  unlock = "not-configured";
  await s.api.loadHotLeads(true);
  ta.value = "Left a voicemail";
  await s.api.hlSaveNote(0);
  check("no password set: the save says the notes cannot be shown on this dashboard",
    msg.textContent === "Saved. This lead's notes cannot be shown on this dashboard: no contact-details password is set.",
    msg.textContent);
}
{
  // A lead whose notes could not be read, until a save's answer brought them: the notice goes.
  const s = sandbox(undefined, { localStorage: memoryStorage() });
  s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  s.respond(async url => url === "/api/lead_notes"
    ? reply(200, { status: "success", callId: "call-e", notes: { entries: [
        { id: "e1", at: "2026-09-28T00:00:00Z", by: "", action: "other", text: "Read now" }], links: [] } })
    : reply(200, { status: "success", contact: "masked", contactUnlock: "password",
                   leads: [clean({ callId: "call-e" })], notesErrors: ["call-e"] }));
  await s.api.loadHotLeads();
  s.el("hlNoteText-0", { value: "Read now" }); s.el("hlNoteMsg-0");
  const errBox = s.el("hlNotesErr-0", { textContent: "The notes saved for this lead could not be read just now.", hidden: false });
  const list = s.el("hlNoteList-0");
  await s.api.hlSaveNote(0);
  check("after a save reads a lead's unreadable notes, the notice saying they could not be read is gone",
    errBox.hidden === true && errBox.textContent === "" && list.innerHTML.includes("Read now"),
    JSON.stringify([errBox.hidden, errBox.textContent]));
}

console.log("\n--- a cut-down lead draws nothing it does not have ---");
{
  const s = sandbox(undefined, { localStorage: memoryStorage() });
  const content = s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  // As api/_contact.py hard_masked sends it: no appointment, brief, recommendations or research.
  const cut = { callId: "call-cut", channel: "vapi", grade: "hot", createdAt: "2026-09-28T01:02:03.000Z",
                kommo: { leadUrl: "https://spb.kommo.com/leads/detail/21345678" },
                customer: { name: "[hidden]", email: "", phone: "", phoneLast3: "678", hasEmail: true, hasPhone: true },
                maskFailed: true };
  let unlock = "password";
  s.respond(async () => reply(200, { status: "success", contact: "masked", contactUnlock: unlock,
                                     leads: [cut], notesHidden: ["call-cut"] }));
  await s.api.loadHotLeads();
  let h = content.innerHTML;
  check("a cut-down card draws no booking line, brief, buildings, Re-run or report bar",
    !h.includes("No discovery call booked") && !h.includes("hl-appt-none") && !h.includes("No buildings were recommended")
    && !h.includes("No brief was captured") && !h.includes('id="hlRerun-0"') && !h.includes('id="hlReport-0"')
    && !h.includes("Recommended buildings"), h.slice(h.indexOf('id="hlCard-0"'), h.indexOf('id="hlCard-0"') + 1500));
  check("... one line says the booking, brief and buildings are hidden with the contact details",
    h.includes('id="hlCut-0">The call booking, the brief and the recommended buildings are hidden with the contact '
      + "details. The contact-details password shows them.</div>"));
  check("... and its notes block stays", h.includes('id="hlNoteText-0"') && h.includes('id="hlNotesHidden-0"'));
  auditMarkup("a cut-down card: markup", h);
  // A record that still carried them is drawn the same way in the masked view: they were not masked.
  const carried = { ...clean({ callId: "call-cut2" }), maskFailed: true };
  const out2 = s.api.hlLeadCard(carried, 0, false, { contact: "masked" });
  check("... even when the record still carries them",
    !out2.includes("Discovery call booked") && !out2.includes('class="hl-chip"') && !out2.includes("Metricon")
    && !out2.includes("Re-run with today") && out2.includes("recommended buildings are hidden"));
  const full = s.api.hlLeadCard(carried, 0, false, { contact: "full" });
  check("the full view of the same record draws them as usual",
    full.includes("Discovery call booked") && full.includes("Recommended buildings") && full.includes('id="hlRerun-0"')
    && !full.includes("recommended buildings are hidden"));

  // No password set on this dashboard: nothing sends anyone for one.
  unlock = "not-configured";
  await s.api.loadHotLeads(true);
  h = content.innerHTML;
  const card = h.slice(h.indexOf('id="hlCard-0"'));
  check("no password set: no notice sends anyone for a password, and each says it cannot be shown here",
    !card.includes("password shows") && !card.includes("shown only with the contact-details password")
    && card.includes("The rest cannot be shown on this dashboard: no contact-details password is set.")
    && card.includes("They cannot be shown on this dashboard: no contact-details password is set.")
    && card.includes("so they cannot be shown on this dashboard: no contact-details password is set."),
    card.slice(0, 1600));
  check("... under the access bar that says the same",
    h.includes("No contact-details password is set on this dashboard, so they cannot be unlocked here."));
}

console.log("\n--- what is typed while a save is out stays ---");
{
  const r = notesRig();
  const { s, hook, sent, held } = r;
  await s.api.loadHotLeads();
  const ta = s.el("hlNoteText-0", { value: "Rang Jo" }), msg = s.el("hlNoteMsg-0");
  r.gate = true;
  const p = s.api.hlSaveNote(0);
  await new Promise(go => setImmediate(go));
  ta.value = "Rang Jo -- she also wants Ripley and Deanside";
  held.shift()();
  await p;
  check("words typed while a save is out stay in the box; 'Saved.' is about the words sent",
    msg.textContent === "Saved." && ta.value === "Rang Jo -- she also wants Ripley and Deanside"
    && sent[0].text === "Rang Jo" && hook.live().length === 1, msg.textContent + " | " + JSON.stringify(ta.value));
  ta.value = "Rang Jo";
  s.api.hlPickAction(0, "call_booked");
  const p2 = s.api.hlSaveNote(0);
  await new Promise(go => setImmediate(go));
  s.api.hlPickAction(0, "package_sent");
  held.shift()();
  await p2;
  check("... and a chip pressed while it saved stays pressed, the box that still held the sent words emptied",
    ta.value === "" && s.api.hlLeadCard(s.api.leads()[0], 0).includes('id="hlAct-0-package_sent" aria-pressed="true"'));
  s.api.hlPickAction(0, "package_sent");
  const url = s.el("hlLinkUrl-0", { value: "https://drive.google.com/drive/folders/a" });
  const lab = s.el("hlLinkLabel-0", { value: "Folder" });
  const p3 = s.api.hlAddLink(0);
  await new Promise(go => setImmediate(go));
  url.value = "https://drive.google.com/drive/folders/b";
  held.shift()();
  await p3;
  check("a link box changed while the link saved keeps the new link; the label box, unchanged, is emptied",
    msg.textContent === "Saved." && url.value === "https://drive.google.com/drive/folders/b" && lab.value === ""
    && sent[sent.length - 1].url === "https://drive.google.com/drive/folders/a", JSON.stringify([url.value, lab.value]));
  r.gate = false;
}

console.log("\n--- a pasted link is sent as pasted, for the hook's own rules ---");
{
  const r = notesRig();
  const { s, sent } = r;
  await s.api.loadHotLeads();
  const url = s.el("hlLinkUrl-0", { value: "" }), msg = s.el("hlNoteMsg-0");
  s.el("hlLinkLabel-0", { value: "" });
  const add = async v => { url.value = v; const k = sent.length; await s.api.hlAddLink(0); return sent.length > k ? sent[k].url : null; };
  let got = await add("https://drive.google.com/file/d/abc\u200B");
  check("a trailing zero-width space (as a chat app pastes it) is dropped before sending",
    got === "https://drive.google.com/file/d/abc" && msg.textContent === "Saved.", JSON.stringify(got));
  got = await add("\u200Ehttps://drive.google.com/file/d/def");
  check("a leading left-to-right mark is dropped, and the link is kept, not refused as 'not a web link'",
    got === "https://drive.google.com/file/d/def" && msg.textContent === "Saved.", JSON.stringify(got) + " " + msg.textContent);
  const k2 = sent.length;
  got = await add("https://drive.google.com/drive/folders/abc https://www.realestate.com.au/property-143456789");
  check("two links pasted together are refused before sending, and the page says why",
    got === null && sent.length === k2
    && msg.textContent === "That link has a space or a hidden character inside it, which usually means two links "
      + "were pasted together. Add one link at a time." && /err/.test(msg.className), msg.textContent);
  check("... and the paste is left in the box to fix",
    url.value === "https://drive.google.com/drive/folders/abc https://www.realestate.com.au/property-143456789");
  got = await add("https://Drive.Google.com/file/d/XyZ");
  check("a link is sent as pasted, not as this browser parses it: the hook's own rules decide",
    got === "https://Drive.Google.com/file/d/XyZ", JSON.stringify(got));
  got = await add("javascript:alert(1)\u200B");
  check("... still http(s) only", got === null && /http:\/\/ or https:\/\//.test(msg.textContent));
}

console.log("\n--- an answer that lands after Lock is not drawn on the locked tab ---");
{
  const FULL = "Rang Jo on 0412 345 678, email jo.citizen@example.com";
  const fullNotes = { entries: [{ id: "f1", at: "2026-09-28T01:00:00Z", by: "Coleen", action: "other", text: FULL }],
                      links: [{ id: "fl1", at: "2026-09-28T01:00:00Z", by: "Coleen", url: "https://drive.google.com/x",
                                label: "Jo 0412 345 678" }] };
  const maskedNotes = { entries: [{ id: "f1", at: "2026-09-28T01:00:00Z", by: "Coleen", action: "other",
                                    text: "Rang Jo on [hidden], email [hidden]" }],
                        links: [{ id: "fl1", at: "2026-09-28T01:00:00Z", by: "Coleen", url: "https://drive.google.com/x",
                                  label: "Jo [hidden]" }] };
  const s = sandbox(undefined, { localStorage: memoryStorage() });
  s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh"); s.el("hlLockBtn"); s.el("hlAccessMsg");
  let view = "full";
  let answer = null;
  const held = [];
  s.respond(async (url, opts) => {
    if (url === "/api/contact_unlock") { view = "masked"; return reply(200, { status: "success", contact: "masked" }); }
    if (url === "/api/lead_notes") return new Promise(r => held.push(() => r(reply(200, answer))));
    return reply(200, { status: "success", contact: view, contactUnlock: "password", leads: [clean({ callId: "call-k" })],
                        notes: { "call-k": view === "full" ? fullNotes : maskedNotes } });
  });
  const leak = () => JSON.stringify([...s.api.notesMap()]).includes("0412")
    || ["hlNoteList-0", "hlLinkList-0"].some(id => s.els.get(id).innerHTML.includes("0412"));
  const presses = {
    note: () => { s.el("hlNoteText-0", { value: "Rang Jo" }); return s.api.hlSaveNote(0); },
    link: () => { s.el("hlLinkUrl-0", { value: "https://drive.google.com/new" }); s.el("hlLinkLabel-0", { value: "" });
                  return s.api.hlAddLink(0); },
    remove: () => s.api.hlRemoveNote(0, 0),
  };
  const race = async (kind, body) => {
    view = "full";
    await s.api.loadHotLeads(true);
    const msg = s.el("hlNoteMsg-0");
    s.el("hlNoteList-0"); s.el("hlLinkList-0");
    answer = body;
    const p = presses[kind]();
    await new Promise(go => setImmediate(go));
    await s.api.hlLock();
    const lockedClean = s.api.access().contact === "masked" && !leak();
    held.shift()();
    await p;
    return { lockedClean, leaked: leak(), said: msg.textContent };
  };
  for (const kind of ["note", "link", "remove"]) {
    const got = await race(kind, { status: "success", callId: "call-k", contact: "full", notes: fullNotes });
    check(`${kind === "note" ? "Save" : kind === "link" ? "Add link" : "Remove"} answered after Lock: the full notes are `
      + "neither drawn nor kept, and the card says to press Refresh",
      got.lockedClean && !got.leaked && s.api.access().contact === "masked"
      && got.said === (kind === "remove" ? "Removed. Press Refresh to see the notes as they are now." : "Saved. Press Refresh to see it."),
      JSON.stringify(got));
  }
  const older = await race("note", { status: "success", callId: "call-k", notes: fullNotes });
  check("a Save answered after Lock, from a dashboard that does not say which view (no contact): not drawn either",
    older.lockedClean && !older.leaked && older.said === "Saved. Press Refresh to see it.", JSON.stringify(older));
  // A masked list, no redraw while the save was out, and an answer that says it is the full view
  // (unlocked in another tab since): still not drawn on this one.
  view = "masked";
  await s.api.loadHotLeads(true);
  s.el("hlNoteList-0"); s.el("hlLinkList-0");
  const msg = s.el("hlNoteMsg-0");
  answer = { status: "success", callId: "call-k", contact: "full", notes: fullNotes };
  const p = presses.note();
  await new Promise(go => setImmediate(go));
  held.shift()();
  await p;
  check("a full answer to a masked list, with no redraw between: not drawn",
    !leak() && msg.textContent === "Saved. Press Refresh to see it.", msg.textContent);
  // The control: a masked answer to a masked list is drawn, as it always was.
  answer = { status: "success", callId: "call-k", contact: "masked", notes: maskedNotes };
  const p2 = presses.note();
  await new Promise(go => setImmediate(go));
  held.shift()();
  await p2;
  check("... while a masked answer to the masked list it was sent from is drawn",
    s.els.get("hlNoteList-0").innerHTML.includes("Rang Jo on [hidden]") && msg.textContent === "Saved.", msg.textContent);
}

// ------------------------------------------------------------------ 28 Sep 2026, repair round
console.log("\n--- a Refresh while a save is out does not settle that save ---");
{
  // The dashboard gives up on the hook at 20 s while the hook may still write, so the consultant
  // is waiting when they press Refresh. Here the hook has written; the GET shows the note.
  const r = notesRig();
  const { s, content, hook, sent, held } = r;
  await s.api.loadHotLeads();
  const ta = s.el("hlNoteText-0", { value: "Sent the Ripley pack" }), msg = s.el("hlNoteMsg-0");
  r.gate = true; r.lose = true;
  const p = s.api.hlSaveNote(0);
  await new Promise(go => setImmediate(go));
  await s.api.loadHotLeads(true);
  check("a Refresh while the save is out: that press is not called 'could not be confirmed', and its words are drawn back",
    !/could not be confirmed/.test(msg.textContent) && content.innerHTML.includes(">Sent the Ripley pack</textarea>"),
    msg.textContent);
  held.shift()();
  await p;
  check("... its answer then lost: the list drawn since shows it, so 'Saved.', the box emptied, stored once",
    msg.textContent === "Saved." && ta.value === "" && hook.live().length === 1,
    msg.textContent + " | " + JSON.stringify(ta.value));
  // The same, with the answer coming back.
  r.lose = false;
  ta.value = "Rang Jo";
  const p2 = s.api.hlSaveNote(0);
  await new Promise(go => setImmediate(go));
  await s.api.loadHotLeads(true);
  held.shift()();
  await p2;
  check("a Refresh while the save is out, then its answer: 'Saved.' -- the list drawn since shows it, so no 'press Refresh'",
    msg.textContent === "Saved." && ta.value === "" && hook.live().length === 2, msg.textContent);
  // The hook writes only after the Refresh has read the list, and the answer is lost.
  r.late = true; r.lose = true;
  ta.value = "Left a voicemail";
  const p3 = s.api.hlSaveNote(0);
  await new Promise(go => setImmediate(go));
  await s.api.loadHotLeads(true);
  held.shift()();
  await p3;
  check("a Refresh that does not show it yet, then its answer lost: could not confirm, and the words are still in the box",
    /^Could not confirm this was saved/.test(msg.textContent) && ta.value === "Left a voicemail"
    && content.innerHTML.includes(">Left a voicemail</textarea>"), msg.textContent);
  r.gate = false; r.late = false; r.lose = false;
  await s.api.hlSaveNote(0);
  check("... and Save pressed again, as it says, is the same request: 'Already saved.', stored once",
    msg.textContent === "Already saved." && hook.live().filter(y => y.text === "Left a voicemail").length === 1
    && rid(sent[sent.length - 1]) === rid(sent[sent.length - 2]), msg.textContent);
}

console.log("\n--- a save's own answer settles the other presses it shows ---");
{
  const r = notesRig();
  const { s, hook, sent } = r;
  await s.api.loadHotLeads();
  const ta = s.el("hlNoteText-0", { value: "" }), msg = s.el("hlNoteMsg-0");
  s.api.hlPickAction(0, "follow_up");
  r.lose = true;
  await s.api.hlSaveNote(0);
  const r1 = rid(sent[sent.length - 1]);
  r.lose = false;
  s.api.hlPickAction(0, "follow_up");
  ta.value = "Rang Jo, no answer";
  await s.api.hlSaveNote(0);
  check("a save whose answer lists an earlier unconfirmed press: 'Saved.', and the card says that one arrived",
    msg.textContent === "Saved. An earlier note that could not be confirmed did arrive: it is in the list below.",
    msg.textContent);
  s.api.hlPickAction(0, "follow_up");
  await s.api.hlSaveNote(0);
  check("... so a deliberate second follow-up later is a new note, not a repeat the hook drops",
    rid(sent[sent.length - 1]) !== r1 && hook.live().filter(y => y.action === "follow_up").length === 2
    && msg.textContent === "Saved.", JSON.stringify(sent.map(rid)) + " " + msg.textContent);
  const url = s.el("hlLinkUrl-0", { value: "https://drive.google.com/drive/folders/a" });
  s.el("hlLinkLabel-0", { value: "" });
  r.lose = true;
  await s.api.hlAddLink(0);
  const ra = rid(sent[sent.length - 1]);
  r.lose = false;
  url.value = "https://drive.google.com/drive/folders/b";
  await s.api.hlAddLink(0);
  check("the same for links: an Add whose answer lists an earlier unconfirmed link says that one arrived",
    msg.textContent === "Saved. An earlier link that could not be confirmed did arrive: it is in the list below.",
    msg.textContent);
  url.value = "https://drive.google.com/drive/folders/a";
  await s.api.hlAddLink(0);
  check("... and that link added again later is a new link",
    rid(sent[sent.length - 1]) !== ra && msg.textContent === "Saved."
    && hook.live().filter(y => y.url === "https://drive.google.com/drive/folders/a").length === 2, msg.textContent);
  // The note still in its box, unconfirmed, when a link's answer shows it arrived.
  ta.value = "Package sent";
  r.lose = true;
  await s.api.hlSaveNote(0);
  r.lose = false;
  url.value = "https://drive.google.com/drive/folders/c";
  await s.api.hlAddLink(0);
  check("a link's answer that shows the note still in its box arrived: the note box is emptied, and the card says why",
    ta.value === "" && msg.textContent === "Saved. The note that could not be confirmed did arrive: it is in the list below.",
    msg.textContent + " | " + JSON.stringify(ta.value));
  ta.value = "Package sent";
  await s.api.hlSaveNote(0);
  check("... and the same words saved after that are a new note",
    hook.live().filter(y => y.text === "Package sent").length === 2 && msg.textContent === "Saved.", msg.textContent);
}

// ------------------------------------------------------------------ 28 Sep 2026, integration
console.log("\n--- a Refresh while another press is out settles the presses it shows ---");
{
  const r = notesRig();
  const { s, hook, sent, held } = r;
  await s.api.loadHotLeads();
  const ta = s.el("hlNoteText-0", { value: "" }), msg = s.el("hlNoteMsg-0");
  // P0: a chip-only "Follow-up required", stored, its answer lost.
  s.api.hlPickAction(0, "follow_up");
  r.lose = true;
  await s.api.hlSaveNote(0);
  const r0 = rid(sent[sent.length - 1]);
  // P1 out when a Refresh shows P0 live; P1's answer is lost too.
  ta.value = "Rang Jo, no answer";
  r.gate = true;
  const p1 = s.api.hlSaveNote(0);
  await new Promise(go => setImmediate(go));
  await s.api.loadHotLeads(true);
  held.shift()();
  await p1;
  r.gate = false; r.lose = false;
  // A deliberate second chip-only follow-up.
  s.api.hlPickAction(0, "follow_up");
  ta.value = "";
  await s.api.hlSaveNote(0);
  const chipOnly = hook.live().filter(y => y.action === "follow_up" && y.text === "").length;
  check("a Refresh while another press is out still settles the presses it shows: a second follow-up is a new note",
    rid(sent[sent.length - 1]) !== r0 && chipOnly === 2 && msg.textContent === "Saved.",
    JSON.stringify({ sentLast: rid(sent[sent.length - 1]) === r0, chipOnly, said: msg.textContent }));
}

console.log("\n--- several earlier presses arrived: said as a count ---");
{
  const r = notesRig();
  const { s, sent } = r;
  await s.api.loadHotLeads();
  const ta = s.el("hlNoteText-0", { value: "" }), msg = s.el("hlNoteMsg-0");
  r.lose = true;
  ta.value = "first";
  await s.api.hlSaveNote(0);
  ta.value = "second";
  await s.api.hlSaveNote(0);
  r.lose = false;
  ta.value = "third";
  await s.api.hlSaveNote(0);
  check("several earlier notes arrived: said as a count, in the plural",
    msg.textContent === "Saved. 2 earlier notes that could not be confirmed did arrive: they are in the list below.",
    msg.textContent + " " + JSON.stringify(sent.length));
}

console.log("\n--- a hidden lead's save answered after a Refresh does not send the consultant to Refresh ---");
{
  const s = sandbox(undefined, { localStorage: memoryStorage() });
  s.el("hotLeadsContent"); s.el("hotLeadsBadge"); s.el("hotLeadsRefresh");
  const held = [];
  s.respond(async url => {
    if (url === "/api/lead_notes") {
      return new Promise(go => held.push(() => go(reply(200, { status: "success", callId: "call-h", notesHidden: true, contact: "masked" }))));
    }
    return reply(200, { status: "success", contact: "masked", contactUnlock: "password",
                        leads: [clean({ callId: "call-h" })], notes: {}, notesHidden: ["call-h"] });
  });
  await s.api.loadHotLeads();
  s.el("hlNoteText-0", { value: "Ring back Friday" });
  const msg = s.el("hlNoteMsg-0");
  const p = s.api.hlSaveNote(0);
  await new Promise(go => setImmediate(go));
  await s.api.loadHotLeads(true);
  held.shift()();
  await p;
  check("a hidden lead's save answered after a Refresh: saved, and never 'Press Refresh to see it' (a Refresh would not show it)",
    /^Saved\./.test(msg.textContent) && !/Press Refresh/.test(msg.textContent)
    && msg.textContent.includes("contact-details password"), msg.textContent);
}

console.log("\n--- one press is 'Saved.', whichever retry the hook answered as a duplicate ---");
{
  const r = notesRig();
  const { s, hook, sent } = r;
  await s.api.loadHotLeads();
  const ta = s.el("hlNoteText-0", { value: "One press" }), msg = s.el("hlNoteMsg-0");
  r.drop = 1;
  await s.api.hlSaveNote(0);
  check("one press whose first fetch dropped after the hook wrote: its retry is answered duplicate, and it is 'Saved.'",
    msg.textContent === "Saved." && sent.length === 2 && rid(sent[0]) === rid(sent[1]) && ta.value === ""
    && hook.live().filter(y => y.text === "One press").length === 1, msg.textContent);
  ta.value = "Another press";
  r.echoDup = true;
  await s.api.hlSaveNote(0);
  r.echoDup = false;
  check("... and one the hook retried inside the request (duplicate, on a fresh requestId): 'Saved.' too",
    msg.textContent === "Saved." && ta.value === "", msg.textContent);
}

console.log("\n--- more unconfirmed presses than are kept ---");
{
  const r = notesRig();
  const { s, sent } = r;
  await s.api.loadHotLeads();
  const ta = s.el("hlNoteText-0", { value: "" }); s.el("hlNoteMsg-0");
  r.offline = true;                     // none of these reaches the hook
  const first = {};
  for (let k = 0; k <= 10; k++) {
    ta.value = "note " + k;
    const n = sent.length;
    await s.api.hlSaveNote(0);
    first[k] = rid(sent[n]);
  }
  r.offline = false;
  ta.value = "note 0";                  // the oldest, past the cap: a new request, and confirmed
  await s.api.hlSaveNote(0);
  r.offline = true;
  ta.value = "note 1";
  const n1 = sent.length;
  await s.api.hlSaveNote(0);
  r.offline = false;
  check("eleven presses unconfirmed, then the oldest confirmed: the next oldest still goes with its own requestId",
    first[1] !== null && rid(sent[n1]) === first[1], JSON.stringify([rid(sent[n1]), first[1]]));
}

console.log("\n--- the link box drops what the hook drops, and keeps what it keeps ---");
{
  // What lib/lead-notes.js cleanLinkUrl parses for each paste (its FORMAT_CHARS, then
  // dropLooseJoiners), taken from the hook itself on 28 Sep 2026. The page must send exactly
  // that string, so the hook, not the page, decides the address that is stored.
  const r = notesRig();
  const { s, sent } = r;
  await s.api.loadHotLeads();
  const url = s.el("hlLinkUrl-0", { value: "" }), msg = s.el("hlNoteMsg-0");
  s.el("hlLinkLabel-0", { value: "" });
  const HOOK = [
    ["a ZWNJ inside a Persian word in the path (kept)",
      "https://fa.wikipedia.org/wiki/\u0645\u06CC\u200C\u062E\u0648\u0627\u0647\u0645", null],
    ["an emoji's variation selector (kept)", "https://example.com/\u2764\uFE0F", null],
    ["a Devanagari half-form's ZWJ (kept)", "https://example.com/\u0915\u094D\u200D", null],
    ["a family emoji's ZWJ (kept)", "https://example.com/\u{1F468}\u200D\u{1F469}", null],
    ["a keycap's selector (kept)", "https://example.com/3\uFE0F\u20E3", null],
    ["a ZWJ after a Latin letter (dropped)", "https://example.com/a\u200D", "https://example.com/a"],
    ["a leading ZWJ (dropped, not refused)", "\u200Dhttps://example.com/x", "https://example.com/x"],
    ["a Hangul filler and a ZWJ (both dropped)", "https://example.com/x\u3164\u200D", "https://example.com/x"],
    ["a run of tag characters (dropped)", "https://example.com/x\u{E0041}\u{E0042}", "https://example.com/x"],
    ["soft hyphens and a BOM (dropped)", "\u00ADhttps://example.com/y\u00AD\uFEFF", "https://example.com/y"],
    // A digit outside the BMP is two UTF-16 units: the joiner after it is judged by the whole
    // character (hlCharBefore), as the hook judges it, not by half of it.
    ["a ZWNJ after a 4-byte digit (dropped, as after any digit)",
      "https://example.com/a\u{1D7CE}\u200Cb", "https://example.com/a\u{1D7CE}b"],
  ];
  for (const [label, paste, want] of HOOK) {
    url.value = paste;
    const k = sent.length;
    await s.api.hlAddLink(0);
    const got = sent.length > k ? sent[k].url : null;
    check("link box, as the hook: " + label, got === (want === null ? paste : want) && msg.textContent === "Saved.",
      JSON.stringify(got) + " " + msg.textContent);
  }
}

console.log("\n--- a Refresh sent before Lock is not drawn after it ---");
{
  const s = sandbox(undefined, { localStorage: memoryStorage() });
  const content = s.el("hotLeadsContent"); s.el("hotLeadsBadge"); const btn = s.el("hotLeadsRefresh");
  s.el("hlLockBtn"); s.el("hlAccessMsg");
  let view = "full", holdGet = false;
  const heldGets = [], heldLock = [];
  s.respond(async url => {
    if (url === "/api/contact_unlock") {
      return new Promise(r => heldLock.push(() => { view = "masked"; r(reply(200, { status: "success", contact: "masked" })); }));
    }
    // The GET's answer is the view the cookie it was sent with gave: fixed when it is sent.
    const ans = reply(200, { status: "success", contact: view, contactUnlock: "password", leads: [clean({ callId: "call-r" })] });
    return holdGet ? new Promise(r => heldGets.push(() => r(ans))) : ans;
  });
  await s.api.loadHotLeads();
  check("the race's control: unlocked, the full number is on the tab", content.innerHTML.includes('href="tel:+61412345678"'));
  const lock = s.api.hlLock();                  // Lock pressed; its answer held
  await new Promise(go => setImmediate(go));
  holdGet = true;
  const refresh = s.api.loadHotLeads(true);     // Refresh pressed while Lock is out: sent unlocked
  await new Promise(go => setImmediate(go));
  holdGet = false;
  heldLock.shift()();                           // Lock lands, and its own reload is answered masked
  const locked = await lock;
  heldGets.shift()();                           // then the Refresh sent before it lands, full
  await refresh;
  const h = content.innerHTML;
  check("a Refresh sent before Lock and answered after it: the locked tab stays masked, no number, email or link",
    locked === true && s.api.access().contact === "masked" && !h.includes("tel:") && !h.includes("mailto:")
    && !h.includes("jo.citizen@example.com") && h.includes("Mobile ending 678"), JSON.stringify([locked, s.api.access()]));
  check("... and the Refresh button is usable again", btn.disabled === false);
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
  check("pane header names both sources", pane.includes("<span>Leads from Clare's calls and WhatsApp</span>"));
  check("pane says WhatsApp leads are listed in their own group",
    /Qualified <strong>WhatsApp<\/strong> leads from Kommo are listed in their own group/.test(pane));
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
