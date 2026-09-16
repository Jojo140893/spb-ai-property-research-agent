// Exercises the Research & Scoring tab: the brief form's markup, and the code between the
// RESEARCH-TAB-BEGIN and RESEARCH-TAB-END markers in index.html, sliced out and run under
// Node with fetch and the DOM stubbed. Sliced rather than reimplemented, so it cannot drift
// from what ships.
//
// Part C, 16 Sep. Colin searched "QLD under $750k" and got nothing useful. Among the causes
// on the form itself:
//   - a blank Client Name blocked Execute AND the "search without this minimum" button;
//   - a budget that was not a whole $10,000 (765000, or 750) could not be submitted;
//   - nothing said what the price boxes were read as, or what the search did to the brief;
//   - builder and email text went into the shortlist cards unescaped (7 fields ran script).
//
// WHAT THIS CAN AND CANNOT PROVE. There is no browser here, so the browser's own form
// validation (`required`, `step`) does not exist: a behaviour test of "a blank name submits"
// would pass whether or not the fix is in. Those are checked on the MARKUP instead (Part C
// review, correction 15). Behaviour is tested only where it can fail under Node: escaping,
// the budget echo, the search notes, the sheet badge and the minimum re-run.
//
// NOTHING HERE TOUCHES THE NETWORK. fetch inside the sandbox is a stub, and a stub asked
// for anything but /api/research fails the run.
//
//     node tests/research_tab_checks.mjs
//     RT_INDEX=path/to/other/index.html node tests/research_tab_checks.mjs   (mutation runs)
import { readFileSync } from "node:fs";
import vm from "node:vm";

const INDEX = process.env.RT_INDEX || new URL("../index.html", import.meta.url);
const html = readFileSync(INDEX, "utf8");

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  PASS  " + name); }
  else { fail++; console.log("  FAIL  " + name + (detail ? "  -> " + detail : "")); }
}

// ------------------------------------------------------------------ the form's markup
console.log("\n--- the brief form cannot block a search (markup) ---");
const formMatch = html.match(/<form id="propertyForm">([\s\S]*?)<\/form>/);
check("the brief form is found", !!formMatch);
const form = formMatch ? formMatch[1].replace(/<!--[\s\S]*?-->/g, "") : "";
const inputTag = id => (form.match(new RegExp(`<input\\b[^>]*\\bid="${id}"[^>]*>`)) || [""])[0];
// An attribute as the browser reads it: any case, and a value double-quoted, single-quoted,
// bare or absent. The FIRST occurrence wins, as it does in HTML. null when it is not there.
// (A reader that only understood name="value" let `required='required'` and `REQUIRED`
// straight past the checks below -- review, 17 Sep.)
const attrOf = (tag, name) => {
  // Tokenised, so a word inside another attribute's quoted value is never read as a name.
  const body = String(tag || "").replace(/^<[a-zA-Z][\w-]*/, "").replace(/\/?>$/, "");
  const re = /([^\s"'>\/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
  let m;
  while ((m = re.exec(body))) {
    if (m[1].toLowerCase() === name) return m[2] ?? m[3] ?? m[4] ?? "";
  }
  return null;
};
check("the attribute reader sees every spelling of an attribute",
  attrOf("<input id=\"a\" required>", "required") === ""
  && attrOf("<input id=\"a\" REQUIRED>", "required") === ""
  && attrOf("<input id=\"a\" required='required'>", "required") === "required"
  && attrOf("<input id=\"a\" required=\"\">", "required") === ""
  && attrOf("<input id=\"a\" step=10000>", "step") === "10000"
  && attrOf("<input id=\"a\" step='0.5'>", "step") === "0.5"
  && attrOf("<input id=\"a\" step = \"any\">", "step") === "any"
  && attrOf("<input id=\"a\" data-required=\"x\">", "required") === null
  && attrOf("<input id=\"a\" placeholder=\"required\">", "required") === null
  && attrOf("<input id=\"a\" placeholder=\"is required here\">", "required") === null);

const clientName = inputTag("clientName");
check("#clientName is on the form", !!clientName);
check("#clientName is not required (a blank name blocked Execute)",
  clientName && attrOf(clientName, "required") === null, clientName);
const requiredTags = (form.match(/<(input|select|textarea)\b[^>]*>/gi) || [])
  .filter(t => attrOf(t, "required") !== null);
check("no control on the brief form is required", requiredTags.length === 0, requiredTags.join(" "));

for (const id of ["budgetMin", "budgetMax", "budgetPref"]) {
  const tag = inputTag(id);
  check(`#${id} accepts any dollar amount (step="any")`,
    tag && attrOf(tag, "step") === "any", tag || "not found");
}
// Correction 16: the parser and the stock hold bathrooms as whole numbers, so 2.5 would
// silently search for 2. The box must not invite it.
const bathrooms = inputTag("bathrooms");
const bathStep = attrOf(bathrooms, "step");
check("#bathrooms stays whole numbers (no fractional step)",
  bathrooms && (bathStep === null || /^[1-9]\d*$/.test(bathStep)), bathrooms || "not found");
check("the budget echo has a place under Price Range",
  /<div\b[^>]*\bid="budgetEcho"[^>]*>/.test(form) && form.indexOf('id="budgetEcho"') > form.indexOf('id="budgetMax"'));
check("the Preferred Price help no longer names a 'Maximum Budget' box",
  !/Maximum Budget does that/.test(form) && /the maximum price does that/.test(form));

// ------------------------------------------------------------------ the slice
const BEGIN = "    // RESEARCH-TAB-BEGIN";
const END = "    // RESEARCH-TAB-END";
const b = html.indexOf(BEGIN), e = html.indexOf(END);
if (b < 0 || e < 0 || e < b) {
  console.log("  FAIL  RESEARCH-TAB-BEGIN / RESEARCH-TAB-END markers not found in index.html");
  process.exit(1);
}
const block = html.slice(b, e);
// The page's one escaper and its "not stated" word, reused rather than copied: both are
// declared elsewhere in the script.
const escMatch = html.match(/^    const esc = [\s\S]*?\[c\]\)\);$/m);
const notStatedMatch = html.match(/^    const NOT_STATED = .*;$/m);
if (!escMatch || !notStatedMatch) {
  console.log("  FAIL  the page's esc() or NOT_STATED was not found -- renamed?");
  process.exit(1);
}

check("the minimum re-run never goes through form.requestSubmit (validation swallowed it)",
  !/requestSubmit/.test(block));

const EXPORTS = ["runResearchAgent", "budgetEchoText", "paintBudgetEcho", "searchedLineText",
  "adjustmentText", "locationNoteText", "searchNotesHtml", "capturedRow"];

// A tiny element: enough DOM for runResearchAgent to render into and for the checks to
// read back what it wrote. innerHTML replaces children, as it does in a browser.
function makeEl(id = "", tag = "div") {
  let inner = "";
  const el = {
    id, tagName: tag.toUpperCase(), value: "", checked: false, textContent: "", className: "",
    type: "", hidden: false, disabled: false, onclick: null, style: {}, dataset: {}, attrs: {},
    children: [], parent: null, listeners: {}, queried: {},
    get innerHTML() { return inner; },
    set innerHTML(v) { inner = String(v); this.children = []; this.queried = {}; },
    appendChild(c) { if (c.parent) c.remove(); c.parent = this; this.children.push(c); return c; },
    remove() {
      if (this.parent) { this.parent.children = this.parent.children.filter(x => x !== this); }
      this.parent = null;
    },
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    // Class selectors only, read out of this element's own markup. The same objects come
    // back on every call, so a handler the page attaches can be clicked here.
    querySelectorAll(sel) {
      if (this.queried[sel]) return this.queried[sel];
      const found = [];
      const m = /^\.([\w-]+)$/.exec(sel);
      if (m) {
        const tagRe = /<([a-zA-Z][\w-]*)\b([^>]*)>/g;
        let t;
        while ((t = tagRe.exec(inner))) {
          const cls = / class="([^"]*)"/.exec(t[2]);
          if (!cls || !cls[1].split(/\s+/).includes(m[1])) continue;
          const child = makeEl("", t[1]);
          child.className = cls[1];
          for (const d of t[2].matchAll(/ data-([\w-]+)="([^"]*)"/g)) child.dataset[d[1]] = d[2];
          found.push(child);
        }
      }
      return (this.queried[sel] = found);
    },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
  };
  return el;
}

// What an element holds, as markup: its innerHTML, then each appended child wrapped in its own
// tag and class, so the order of what was rendered can be read back.
const dump = el => (el.innerHTML || "") + el.children.map(c => {
  const tag = c.tagName.toLowerCase();
  return `<${tag}${c.className ? ` class="${c.className}"` : ""}>${dump(c)}</${tag}>`;
}).join("");
const FORM_IDS = ["clientName", "budgetMin", "budgetMax", "budgetPref", "buyerType", "stateSelect",
  "suburbs", "bedrooms", "bathrooms", "carSpaces", "storeys", "landSize", "houseSize",
  "searchRadius", "requiresIncentive", "resultsContent", "actionBar", "shortlistBadge", "budgetEcho"];

function sandbox() {
  const els = new Map();
  const calls = { fetch: [], requestSubmit: 0, dispatchEvent: 0 };
  for (const id of FORM_IDS) {
    els.set(id, makeEl(id, ["budgetEcho", "resultsContent", "actionBar", "shortlistBadge"].includes(id)
      ? "div" : "input"));
  }
  const formEl = makeEl("propertyForm", "form");
  formEl.requestSubmit = () => { calls.requestSubmit++; };
  formEl.dispatchEvent = () => { calls.dispatchEvent++; return true; };
  els.set("propertyForm", formEl);
  let responder = async () => { throw new Error("no fetch responder set"); };
  const ctx = vm.createContext({
    URL, URLSearchParams, console, setTimeout, clearTimeout,
    location: { pathname: "/", search: "", origin: "https://example.invalid" },
    navigator: {}, window: {}, STOCK: [],
    document: {
      // Anything the renderers write by id (the report buttons, "show more") exists on
      // demand, as it would once their markup was parsed.
      getElementById: id => {
        if (!els.has(id)) els.set(id, makeEl(id));
        return els.get(id);
      },
      createElement: tag => makeEl("", tag),
      querySelectorAll: () => [],
      addEventListener: () => {},
    },
    // The page's globals this tab reads and writes, declared elsewhere in the script.
    SELECTED: new Map(), LAST_SHORTLIST: [], BENCH_CACHE: null, COMPARISONS: {},
    currentPayload: null,
    paintCompareBar: () => {}, saveSelected: () => {}, openGeneratedReport: () => {},
    comparisonDocument: () => "", checkBenchmark: () => {},
    fetch: async (url, opts) => {
      calls.fetch.push({ url, opts });
      if (url !== "/api/research") throw new Error("BLOCKED: test tried to fetch " + url);
      return responder(url, opts);
    },
  });
  const exportsSrc = EXPORTS.map(n => `${n}: typeof ${n} === 'undefined' ? undefined : ${n}`).join(", ");
  const api = vm.runInContext(
    escMatch[0] + "\n" + notStatedMatch[0] + "\n" + block + "\n;({" + exportsSrc + ", esc })",
    ctx, { filename: "index.html#research-tab" });
  return { api, ctx, calls, el: id => els.get(id), respond: fn => { responder = fn; } };
}

const reply = (status, body) => ({
  status, ok: status >= 200 && status < 300,
  headers: { get: () => "application/json" },
  json: async () => JSON.parse(JSON.stringify(body)),
});
const settle = async () => { for (let i = 0; i < 30; i++) await new Promise(r => setImmediate(r)); };
const fn = (api, name) => (typeof api[name] === "function" ? api[name] : null);

// ------------------------------------------------------------------ markup auditor
// Every tag the renderers emit, against a small allowlist. A listing string that reaches the
// page unescaped shows up here as a tag that should not exist (<img>, <svg>, <script>) or an
// event-handler attribute.
const TAGS = new Set(["div", "span", "a", "strong", "br", "details", "summary", "p", "button", "em",
  "label", "input", "code"]);
const ATTRS = new Set(["class", "id", "href", "target", "rel", "title", "style", "type", "disabled",
  "hidden", "checked", "data-hash", "data-idx", "data-term", "data-field"]);
function markupProblems(out) {
  const problems = [];
  const text = String(out).replace(/<!--[\s\S]*?-->/g, "");
  if (/<\s*script/i.test(text)) problems.push("raw <script");
  const tagRe = /<\/?([a-zA-Z][\w-]*)([^>]*)>/g;
  let m;
  while ((m = tagRe.exec(text))) {
    const name = m[1].toLowerCase();
    if (!TAGS.has(name)) problems.push("tag <" + name + ">");
    const attrRe = /\s+([^\s="'>\/]+)(?:="([^"]*)")?/g;
    const leftover = m[2].replace(attrRe, "").replace(/\s*\/?\s*$/, "");
    if (leftover) problems.push("unparsed attribute text in <" + name + ">: " + leftover.slice(0, 60));
    let a;
    while ((a = attrRe.exec(m[2]))) {
      const attr = a[1].toLowerCase();
      if (!ATTRS.has(attr)) problems.push("attribute " + attr + " on <" + name + ">");
      if (attr === "href" && !/^https?:\/\//i.test(a[2] || "")) problems.push("href " + JSON.stringify(a[2]));
    }
  }
  return problems;
}

console.log("\n--- the markup auditor catches what it exists for ---");
check("auditor flags an img tag", markupProblems('<div><img src=x onerror=alert(1)></div>').length > 0);
check("auditor flags an event-handler attribute", markupProblems('<span onmouseover="x">y</span>').length > 0);
check("auditor flags a raw script tag", markupProblems('<div><script>alert(1)</' + 'script></div>').length > 0);
check("auditor passes ordinary markup",
  markupProblems('<div class="x"><strong>a</strong> &lt;img src=x&gt;</div>').length === 0);

// ------------------------------------------------------------------ fixtures
const payload = field => `<img src=x data-f=${field} onerror=window.__xss.push(1)>`;
const shown = field => `&lt;img src=x data-f=${field} onerror=window.__xss.push(1)&gt;`;

const prop = (over = {}) => ({
  content_hash: "v2:aec54350", lot_address: "Lot 1228 Dawn Estate", suburb: "Walloon", state: "QLD",
  builder_name: "Shape Homes", house_design: "Medium Density", score: 81.5, recommendation: "Recommend",
  bedrooms: 3, bathrooms: 2, car_spaces: 1, land_size_sqm: 300, house_size_sqm: 140,
  advertised_package_price: 721900, turnkey_status: "Full Turnkey", missing_inclusions: [],
  benchmark: { benchmarked: true, basis: "internal", classification: "Fair Market Value",
               variance_pct: 2.1, avg_market_price: 707000, needs_manual_benchmark: false },
  scoring_details: { budget_fit: 18, requirement_match: 16, location_amenity: 10,
                     value_competitiveness: 12, builder_confidence: 8, suitability_score: 8, risk_score: 9 },
  recommendation_reason: "Under budget.", advisories: "", date_checked: "03/09/2026",
  source_link: null, competitive: null,
  ...over,
});
const success = (over = {}) => ({
  status: "success", research_record_id: "r", shortlist_count: 1, rejected_count: 4,
  shortlist: [prop()], rejected_log: [], comparisons: {}, kommo_payload: {},
  ...over,
});

async function render(body, setup) {
  const s = sandbox();
  if (setup) setup(s);
  s.respond(async () => reply(200, body));
  if (fn(s.api, "runResearchAgent")) await s.api.runResearchAgent();
  await settle();
  return { ...s, out: dump(s.el("resultsContent")) };
}

// ------------------------------------------------------------------ escaping
console.log("\n--- the escaper covers single-quoted attributes ---");
{
  const { api } = sandbox();
  check("esc turns ' into &#39;", api.esc("it's") === "it&#39;s", api.esc("it's"));
  check("esc still escapes & < > \"", api.esc(`&<>"`) === "&amp;&lt;&gt;&quot;");
}

console.log("\n--- no listing field on a shortlist card can become markup ---");
// One field hostile at a time, so each line names the esc() it depends on: removing any one
// of them turns exactly that line red.
const CARD_FIELDS = {
  lot_address: p => ({ lot_address: p }),
  suburb: p => ({ suburb: p }),
  state: p => ({ state: p }),
  builder_name: p => ({ builder_name: p }),
  house_design: p => ({ house_design: p }),
  recommendation: p => ({ recommendation: p }),
  turnkey_status: p => ({ turnkey_status: p }),
  "benchmark.classification (benchmarked)": p => ({ benchmark: { ...prop().benchmark, classification: p } }),
  "benchmark.classification (not benchmarked)": p => ({ benchmark: { benchmarked: false, classification: p } }),
  recommendation_reason: p => ({ recommendation_reason: p }),
};
for (const [field, over] of Object.entries(CARD_FIELDS)) {
  const key = field.replace(/[^\w]+/g, "_");
  const { out } = await render(success({ shortlist: [prop(over(payload(key)))] }));
  const problems = markupProblems(out);
  check(`${field}: rendered as text, not markup`,
    problems.length === 0 && out.includes(shown(key)),
    problems.length ? problems.slice(0, 3).join(" | ") : "the field was not rendered at all");
}
{
  const all = {};
  for (const over of Object.values(CARD_FIELDS)) Object.assign(all, over(payload("all")));
  const { out } = await render(success({ shortlist: [prop(all)] }));
  const imgs = (out.match(/<img\b/gi) || []).length;
  check("every field hostile at once: no <img> tag reaches the card", imgs === 0, `${imgs} img tags`);
}

// ------------------------------------------------------------------ budget echo
console.log("\n--- the price boxes say what will be searched ---");
{
  const s = sandbox();
  const echo = s.el("budgetEcho");
  const type = (id, value) => {
    s.el(id).value = value;
    for (const h of s.el(id).listeners.input || []) h({ type: "input", target: s.el(id) });
  };
  check("the echo is wired to the maximum box", (s.el("budgetMax").listeners.input || []).length > 0);
  check("the echo is wired to the minimum box", (s.el("budgetMin").listeners.input || []).length > 0);
  check("nothing typed: the echo is hidden", echo.hidden === true && echo.textContent === "",
    JSON.stringify({ hidden: echo.hidden, text: echo.textContent }));

  type("budgetMax", "750");
  check("750 reads as $750,000 (thousands)",
    echo.textContent.includes("$750,000") && /thousands/.test(echo.textContent) && echo.hidden === false,
    JSON.stringify(echo.textContent));
  type("budgetMax", "750000");
  check("750000 reads = $750,000", echo.textContent.includes("= $750,000") && !/thousands/.test(echo.textContent),
    JSON.stringify(echo.textContent));
  type("budgetMax", "765000");
  check("765000 reads $765,000", echo.textContent.includes("$765,000"), JSON.stringify(echo.textContent));
  type("budgetMax", "");
  type("budgetMin", "750000");
  check("a minimum alone says 'and above'", /showing \$750,000 and above/.test(echo.textContent),
    JSON.stringify(echo.textContent));
  type("budgetMax", "300000");
  check("a minimum above the maximum says they are swapped", /swap/.test(echo.textContent),
    JSON.stringify(echo.textContent));
  type("budgetMin", "");
  type("budgetMax", "");
  check("cleared again: hidden", echo.hidden === true && echo.textContent === "");
  // The edges of the rule, which must match the server's normaliser (1 to 9,999 is thousands).
  type("budgetMax", "9999");
  check("9,999 is still thousands", echo.textContent.includes("$9,999,000") && /thousands/.test(echo.textContent),
    JSON.stringify(echo.textContent));
  type("budgetMax", "10000");
  check("10,000 is dollars", echo.textContent.includes("= $10,000") && !/thousands/.test(echo.textContent),
    JSON.stringify(echo.textContent));
  type("budgetMax", "1");
  check("1 is thousands", echo.textContent.includes("$1,000") && /thousands/.test(echo.textContent),
    JSON.stringify(echo.textContent));
  type("budgetMax", "0.5");
  check("below 1 is not thousands (the server reads 1 to 9,999 only)", !/thousands/.test(echo.textContent),
    JSON.stringify(echo.textContent));
  type("budgetMax", "");
}
{
  // Set the way a restored form, a hot-lead re-run or a one-click relaxation sets it: no
  // input event. The echo must still describe what the search is about to send.
  const s = sandbox();
  s.el("budgetMax").value = "750";
  s.respond(async () => reply(200, success()));
  if (fn(s.api, "runResearchAgent")) await s.api.runResearchAgent();
  await settle();
  const echo = s.el("budgetEcho");
  check("a search repaints the echo from boxes changed without typing",
    echo.hidden === false && echo.textContent.includes("$750,000"), JSON.stringify(echo.textContent));
}

// ------------------------------------------------------------------ Searched line and notes
console.log("\n--- the results say what was searched, and what the search changed ---");
{
  const { out } = await render(success({
    brief_used: { state: "QLD", budget_min: 0, budget_max: 750000, suburbs: [], search_radius_km: 0,
                  bedrooms_min: 0, bathrooms_min: 0, car_spaces_min: 0, product_type: [],
                  requires_incentive: false },
  }));
  check("a Searched: line from brief_used", /Searched:/.test(out) && out.includes("QLD · up to $750,000 · all suburbs"),
    out.slice(0, 300));
  check("the Searched: line comes before the first card",
    out.indexOf("Searched:") >= 0 && out.indexOf("prop-card") > out.indexOf("Searched:"),
    `Searched: at ${out.indexOf("Searched:")}, first card at ${out.indexOf("prop-card")}`);
}
{
  const { out } = await render(success({
    shortlist: [], shortlist_count: 0,
    brief_used: { state: "", budget_min: 300000, budget_max: 9007199254740991,
                  suburbs: ["Tarneit"], search_radius_km: 10, bedrooms_min: 4,
                  product_type: ["House & Land"], requires_incentive: true },
  }));
  check("Any state, a floor with no ceiling, a radius and minimums read plainly",
    out.includes("Any state · $300,000 and above · within 10 km of Tarneit · 4+ bed · House &amp; Land · with a rebate or discount"),
    out.slice(0, 400));
}
{
  // Suburbs in brief_used are typed text, echoed back.
  const { out } = await render(success({
    brief_used: { state: payload("used_state"), budget_max: 750000, suburbs: [payload("used_suburb")],
                  product_type: [payload("used_type")] },
  }));
  const problems = markupProblems(out);
  check("a brief_used carrying <img> renders as text",
    problems.length === 0 && ["used_state", "used_suburb", "used_type"].every(k => out.includes(shown(k))),
    problems.slice(0, 3).join(" | ") || "a hostile brief_used value was not shown");
}
{
  const many = Array.from({ length: 15 }, (_, i) => `Suburb${i + 1}`);
  const { out } = await render(success({ brief_used: { state: "VIC", suburbs: many } }));
  check("a long suburb list on the Searched: line is cut to 12 and counted",
    out.includes("Suburb11, Suburb12 and 3 more") && !out.includes("Suburb13"), out.slice(0, 400));
}
{
  const { out } = await render(success({
    brief_adjustments: [{ field: "budget_max", from: 750, to: 750000, why: "read as thousands" }],
  }));
  check("a brief_adjustments entry is shown as a note",
    out.includes("search-note") && out.includes("Maximum price: $750 → $750,000 (read as thousands)"),
    out.slice(0, 400));
}
{
  const { out } = await render(success({
    brief_adjustments: [{ field: "budget_min/budget_max", from: [750000, 300000], to: [300000, 750000],
                          why: "minimum was above maximum; swapped" }],
  }));
  check("a swapped range reads as money on both ends",
    out.includes("Minimum price and Maximum price: $750,000, $300,000 → $300,000, $750,000 (minimum was above maximum; swapped)"),
    out.slice(0, 400));
}
{
  const { out } = await render(success({
    brief_adjustments: [
      payload("adj_string"),
      { field: "primary_suburbs", from: [payload("adj_from")], to: "QLD", why: payload("adj_why") },
      { field: payload("adj_field"), note: payload("adj_note") },
    ],
  }));
  const problems = markupProblems(out);
  check("brief_adjustments carrying <img> render as text", problems.length === 0,
    problems.slice(0, 3).join(" | "));
  check("... and every one of them is still shown",
    ["adj_string", "adj_from", "adj_why", "adj_field", "adj_note"].every(k => out.includes(shown(k))));
}
{
  const { out } = await render(success({
    location_resolution: [
      { said: "Tarneit", used: ["Tarneit"], method: "exact", note: "a locality in VIC" },
      { said: "Red Bank Plains", used: ["Redbank Plains"], method: "spoken", note: "said differently from the gazetteer's Redbank Plains" },
      { said: "Queensland", used: ["Queensland"], method: "unresolved", note: "not a locality anywhere; searched as said" },
      { said: "Tarn eat", used: ["Tarneit"], method: "groq", note: "read as Tarneit" },
      { said: payload("loc_said"), used: [payload("loc_used")], method: "fuzzy", note: payload("loc_note") },
    ],
  }));
  check("an unresolved location is shown",
    out.includes("&quot;Queensland&quot; — not a locality anywhere; searched as said"), out.slice(0, 600));
  check("a Groq reading is shown", out.includes("&quot;Tarn eat&quot; searched as Tarneit — read as Tarneit"));
  check("an exact locality is not a note", !out.includes("a locality in VIC"));
  check("a spoken respelling is not a note", !out.includes("said differently from the gazetteer"));
  const problems = markupProblems(out);
  check("location notes carrying <img> render as text",
    problems.length === 0 && ["loc_said", "loc_used", "loc_note"].every(k => out.includes(shown(k))),
    problems.slice(0, 3).join(" | ") || "a hostile location note was not shown");
}
{
  const { out } = await render(success({
    region_expansion: { Geelong: ["Armstrong Creek", "Lara"], [payload("region")]: [payload("region_added")] },
  }));
  check("a widened region is shown",
    out.includes("&quot;Geelong&quot; is a region, so the search also covered Armstrong Creek, Lara"), out.slice(0, 400));
  const problems = markupProblems(out);
  check("region names carrying <img> render as text",
    problems.length === 0 && out.includes(shown("region")) && out.includes(shown("region_added")),
    problems.slice(0, 3).join(" | "));
}
{
  const { out } = await render(success({ shortlist: [], shortlist_count: 0,
    brief_adjustments: [{ field: "primary_suburbs", from: ["Queensland"], to: "QLD", why: "a state was typed as a suburb" }] }));
  check("notes are shown when nothing matched, too", out.includes("a state was typed as a suburb"));
}
{
  const { out } = await render(success({ location_resolution: [], region_expansion: {} }));
  check("nothing to say: no notes box", !out.includes("search-notes"));
}

// ------------------------------------------------------------------ sheet badge
console.log("\n--- a list with no status column says so on the card ---");
{
  const BADGE = "Confirm availability: this list has no status column";
  const on = await render(success({ shortlist: [prop({ availability_by_the_sheet: true })] }));
  check("availability_by_the_sheet true shows the badge", on.out.includes(BADGE));
  const off = await render(success({ shortlist: [prop({ availability_by_the_sheet: false })] }));
  check("availability_by_the_sheet false shows no badge", !off.out.includes(BADGE));
  const none = await render(success());
  check("no flag, no badge", !none.out.includes(BADGE));
}

// ------------------------------------------------------------------ minimum re-run
console.log("\n--- 'search without this minimum' re-runs with a blank client name ---");
{
  const s = sandbox();
  s.el("clientName").value = "";
  s.el("bedrooms").value = "4";
  s.respond(async () => reply(200, success({
    rejected_log: [{ property_id: "SNAPSHOT-COVERAGE", kind: "coverage", address: "Snapshot coverage",
                     reason: "counted", missing_fields: { "bedroom count": 7 } }],
  })));
  if (fn(s.api, "runResearchAgent")) await s.api.runResearchAgent();
  await settle();
  const first = s.calls.fetch.length ? JSON.parse(s.calls.fetch[0].opts.body).client_brief : {};
  check("the first search sent bedrooms_min 4 and a named-for-you client",
    first.bedrooms_min === 4 && first.client_name === "Unnamed Client", JSON.stringify(first));
  const box = s.el("resultsContent").children.find(c => c.className === "gap-note");
  const button = box ? box.querySelectorAll(".gap-clear")[0] : null;
  check("the offer to drop the bedroom minimum is shown", !!(button && typeof button.onclick === "function"));
  if (button && typeof button.onclick === "function") button.onclick();
  await settle();
  check("clicking it posts the search again (it posted nothing with a blank name)",
    s.calls.fetch.length === 2, `${s.calls.fetch.length} POST(s)`);
  const second = s.calls.fetch[1] ? JSON.parse(s.calls.fetch[1].opts.body).client_brief : {};
  check("... without the bedroom minimum", second.bedrooms_min === 0, JSON.stringify(second.bedrooms_min));
  check("... and not through the form's submit",
    s.calls.requestSubmit === 0 && s.calls.dispatchEvent === 0,
    `requestSubmit ${s.calls.requestSubmit}, dispatchEvent ${s.calls.dispatchEvent}`);
}

// ------------------------------------------------------------------ the card's own date (guard)
console.log("\n--- the card's date warning reads the row's own date (unchanged, guarded) ---");
{
  const old = await render(success({ shortlist: [prop({ date_checked: "03/09/2026" })] }));
  check("a row checked 03/09/2026 warns 'days ago'", /Last checked at the source 03\/09\/2026 &mdash; \d+ days ago/.test(old.out));
  const now = new Date();
  const today = `${String(now.getDate()).padStart(2, "0")}/${String(now.getMonth() + 1).padStart(2, "0")}/${now.getFullYear()}`;
  const fresh = await render(success({ shortlist: [prop({ date_checked: today })] }));
  check("a row checked today does not warn", fresh.out.includes(`Last checked at the source ${today}.`) && !/days ago/.test(fresh.out));
}

console.log(fail ? `\n${fail} of ${pass + fail} CHECKS FAILED` : `\nALL ${pass} CHECKS PASSED`);
process.exit(fail ? 1 : 0);
