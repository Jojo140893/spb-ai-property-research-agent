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
  "adjustmentText", "locationNoteText", "searchNotesHtml", "capturedRow",
  "renderSearchHelp", "relaxationsOf", "applyBriefPatch", "renderNeedsConfirming",
  "needsConfirmingCard", "confirmDateText"];

// The State select's options, read from the form's markup: applyBriefPatch refuses a state
// the select cannot show, so the stub must offer exactly what the page offers.
const STATE_OPTIONS = [...((form.match(/<select id="stateSelect">([\s\S]*?)<\/select>/) || [, ""])[1])
  .matchAll(/<option\b[^>]*\bvalue="([^"]*)"/g)].map(m => ({ value: m[1] }));
const PRODUCT_TYPES = [...((form.match(/<div id="productType"[^>]*>([\s\S]*?)<\/div>/) || [, ""])[1])
  .matchAll(/<input type="checkbox" value="([^"]*)"/g)].map(m => m[1].replace(/&amp;/g, "&"));
check("the State select's options are read from the form", STATE_OPTIONS.some(o => o.value === "QLD")
  && STATE_OPTIONS.some(o => o.value === ""), JSON.stringify(STATE_OPTIONS));
check("the Property Type boxes are read from the form", PRODUCT_TYPES.includes("House & Land")
  && PRODUCT_TYPES.includes("Townhouse"), JSON.stringify(PRODUCT_TYPES));

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
  els.get("stateSelect").options = STATE_OPTIONS;
  const productBoxes = PRODUCT_TYPES.map(v => Object.assign(makeEl("", "input"), { value: v }));
  const formEl = makeEl("propertyForm", "form");
  formEl.requestSubmit = () => { calls.requestSubmit++; };
  formEl.dispatchEvent = () => { calls.dispatchEvent++; return true; };
  els.set("propertyForm", formEl);
  // An element the page created and gave an id ("show more"), found where it was appended.
  const inTree = (node, id) => {
    for (const c of node.children) {
      if (c.id === id) return c;
      const deeper = inTree(c, id);
      if (deeper) return deeper;
    }
    return null;
  };
  let responder = async () => { throw new Error("no fetch responder set"); };
  const ctx = vm.createContext({
    URL, URLSearchParams, console, setTimeout, clearTimeout,
    location: { pathname: "/", search: "", origin: "https://example.invalid" },
    navigator: {}, window: {}, STOCK: [],
    document: {
      // Anything the renderers write by id (the report buttons, "show more") exists on
      // demand, as it would once their markup was parsed.
      getElementById: id => {
        const appended = inTree(els.get("resultsContent"), id);
        if (appended) return appended;
        if (!els.has(id)) els.set(id, makeEl(id));
        return els.get(id);
      },
      createElement: tag => makeEl("", tag),
      querySelectorAll: sel => sel === "#productType input" ? productBoxes
        : sel === "#productType input:checked" ? productBoxes.filter(b => b.checked) : [],
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
  return { api, ctx, calls, productBoxes, el: id => els.get(id), respond: fn => { responder = fn; } };
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
  "hidden", "checked", "data-hash", "data-idx", "data-term", "data-field", "open", "data-relax"]);
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
  const secondBody = s.calls.fetch[1] ? JSON.parse(s.calls.fetch[1].opts.body) : {};
  check("... and, being a relaxation someone clicked, asks for the explanation",
    secondBody.explain === true, JSON.stringify(Object.keys(secondBody)));
}

// ------------------------------------------------------------------ explain: who asks for it
console.log("\n--- only a search someone asked for sends explain:true ---");
{
  // Outside the slice, so read from the page: the form's submit and the page-load run are the
  // only places a search starts. Comments are stripped so a mention in prose is not a call.
  const outside = (html.slice(0, b) + html.slice(e))
    .replace(/<!--[\s\S]*?-->/g, "").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  const starts = [...outside.matchAll(/runResearchAgent\(([^)]*)\)/g)].map(m => m[1].trim());
  const submit = outside.match(
    /getElementById\('propertyForm'\)\.addEventListener\('submit', function\(e\) \{\s*e\.preventDefault\(\);\s*runResearchAgent\(([^)]*)\);\s*\}\);/);
  check("a form submit asks for explain", !!submit && /^\{\s*explain:\s*true\s*\}$/.test(submit[1]),
    submit ? submit[1] : "the submit listener was not found");
  const onLoad = outside.match(/loadBuilderDirectory\(\);\s*runResearchAgent\(([^)]*)\);/);
  check("the page-load search does not ask for explain", !!onLoad && onLoad[1].trim() === "",
    onLoad ? onLoad[1] : "the page-load run was not found");
  check("outside the tab a search starts only there (submit explained, page load not)",
    starts.length === 2 && starts.filter(a => /explain:\s*true/.test(a)).length === 1 && starts.includes(""),
    JSON.stringify(starts));
}
{
  const s = sandbox();
  s.respond(async () => reply(200, success()));
  await s.api.runResearchAgent();
  await settle();
  const plain = JSON.parse(s.calls.fetch[0].opts.body);
  check("a search without explain sends only client_brief",
    JSON.stringify(Object.keys(plain)) === '["client_brief"]', JSON.stringify(Object.keys(plain)));
  await s.api.runResearchAgent({ explain: true });
  await settle();
  const asked = JSON.parse(s.calls.fetch[1].opts.body);
  check("a search asked to explain sends {client_brief, explain:true}",
    JSON.stringify(Object.keys(asked)) === '["client_brief","explain"]' && asked.explain === true,
    JSON.stringify(asked).slice(0, 120));
  await s.api.runResearchAgent({ explain: "yes" });
  await settle();
  const loose = JSON.parse(s.calls.fetch[2].opts.body);
  check("only explain === true counts", !("explain" in loose), JSON.stringify(Object.keys(loose)));
}

// ------------------------------------------------------------------ fixtures: an explained search
// Colin's QLD <= $750k on the subset fixture, today 16/09/2026: two confirmed, the three
// Shape Homes Dawn lots from the emailed list waiting to be confirmed.
const dawn = (lot, over = {}) => ({
  content_hash: `v2:dawn${lot}`, lot_address: `Lot ${lot}, Dawn, Walloon`, lot_number: String(lot),
  suburb: "Walloon", state: "QLD", builder_name: "Builder not identified in source", estate_context: "Dawn",
  advertised_package_price: lot === 1231 ? 725900 : 721900, bedrooms: 4, bathrooms: 2, car_spaces: null,
  land_size_sqm: 0, house_size_sqm: null, score: 84, advisories: "", source_link: null, confirm: ["stale"],
  availability_as_stated: "Available", date_checked: "20/08/2026", age_days: 27, fresh_days: 14,
  source_channel: "digital email", email_first_seen: "17/08/2026", list_age_days: 30,
  ...over,
});
const RELAX = {
  reveal: { kind: "include_needs_confirming", label: "Show 3 matches that need confirming", count: 3 },
  plus50k: { kind: "budget_plus_50k", label: "Budget up to $800,000",
             brief_patch: { budget_max: 800000, preferred_spending_cap: 800000 },
             shortlist_count: 2, adds: 0, count_status: "exact", capped: false },
  plus10pct: { kind: "budget_plus_10pct", label: "Budget up to $825,000",
               brief_patch: { budget_max: 825000, preferred_spending_cap: 825000 },
               shortlist_count: 12, adds: 10, count_status: "exact", capped: false },
  radius: { kind: "radius_20", label: "Within 20 km", brief_patch: { search_radius_km: 20 },
            shortlist_count: null, count_status: "not_computed", capped: false },
};
const searchHelp = (over = {}) => ({
  version: 1,
  brief_as_searched: { state: "QLD", budget_min: 0, budget_max: 750000 },
  funnel: {
    snapshot_rows: 19800, out_of_scope: { superseded: 7646, other_state: 9659, state_unknown: 437 },
    gates: [
      { code: "not_available", count: 178, in_price_range: 5 },
      { code: "availability_unstated", count: 632, in_price_range: 6 },
      { code: "price_unverified", count: 1, in_price_range: 1 },
      { code: "over_budget", count: 1228, in_price_range: null },
      { code: "pending_confirmation", count: 6, in_price_range: 3 },
      { code: "bedrooms_below_min", count: 4 },
    ],
    in_price_range_total: 17, in_state_rows: 1440, scored: 8, shortlisted: 2, capped: false,
  },
  relaxations: [RELAX.reveal, RELAX.plus50k, RELAX.plus10pct, RELAX.radius],
  location_notes: [], brief_adjustments: [], timing_ms: 1840,
  ...over,
});
const explained = (over = {}) => success({
  shortlist: [prop({ content_hash: "v2:one" }), prop({ content_hash: "v2:two", lot_address: "Lot 2" })],
  shortlist_count: 2, rejected_count: 1438,
  brief_used: { state: "QLD", budget_min: 0, budget_max: 750000, primary_suburbs: [] },
  search_help: searchHelp(),
  needs_confirming: [dawn(1228), dawn(1229), dawn(1231)], needs_confirming_count: 3,
  needs_confirming_hidden: { newer_capture_disagrees: 3 },
  ...over,
});
async function renderExplained(body, setup) {
  const s = sandbox();
  if (setup) setup(s);
  s.respond(async () => reply(200, body));
  await s.api.runResearchAgent({ explain: true });
  await settle();
  return { ...s, out: dump(s.el("resultsContent")) };
}
const child = (s, cls) => {
  const walk = node => {
    for (const c of node.children) {
      if (String(c.innerHTML).includes(`class="${cls}`)) return c;
      const deeper = walk(c);
      if (deeper) return deeper;
    }
    return null;
  };
  return walk(s.el("resultsContent"));
};

// ------------------------------------------------------------------ Why only N matched
console.log("\n--- 'Why only N matched': the counts, and a button per change that finds more ---");
{
  const { api, out } = await renderExplained(explained());
  const e = t => api.esc(t);
  check("the panel is open and headed 'Why only 2 matched'",
    /<details class="search-help" open><summary>Why only 2 matched<\/summary>/.test(out), out.slice(0, 300));
  check("the panel headline is the funnel's in-range count",
    out.includes(e("In QLD, 17 listings are priced at or under $750,000.")), out.slice(0, 600));
  check("... then where they went: the shortlist, and each gate with an in-range count",
    out.includes(e("2 made the shortlist"))
    && out.includes(e("5 are marked Sold, On Hold or otherwise not available"))
    && out.includes(e("6 are on lists that don't say whether they're available"))
    && out.includes(e("1 has a price we couldn't verify"))
    && out.includes(e("3 weren't checked at the source in the last 14 days")), out.slice(0, 900));
  check("... and not a gate with no in-range number (over budget, scorer codes)",
    !out.includes(e("are over the maximum price")) && !out.includes("fewer bedrooms"));
  check("the budget button carries its true count: 'Budget up to $825,000 → 12 matches (+10)'",
    /<button type="button" class="relax-btn" data-relax="2">Budget up to \$825,000 → 12 matches \(\+10\)<\/button>/.test(out),
    (out.match(/<button[^>]*relax-btn[\s\S]*?<\/button>/g) || []).join(" | "));
  check("a change that adds nothing is one grey 'No change:' line, not a button",
    out.includes("No change: Budget up to $800,000") && !/relax-btn[^>]*>Budget up to \$800,000/.test(out));
  check("a change not counted in time gets no button",
    out.includes("Not counted in time: Within 20 km") && !/relax-btn[^>]*>Within 20 km/.test(out));
  check("the reveal offer shows how many need confirming",
    /class="relax-btn relax-reveal" data-relax="0">Show the 3 that need confirming<\/button>/.test(out));
  check("the panel comes before the first card",
    out.indexOf("search-help") >= 0 && out.indexOf("prop-card") > out.indexOf("search-help"));
}
{
  const nothing = await renderExplained(explained({ shortlist: [], shortlist_count: 0,
    search_help: searchHelp({ relaxations: [RELAX.reveal, { ...RELAX.plus10pct, shortlist_count: 12, adds: 12 }] }) }));
  check("with no results it reads 'Why nothing matched'",
    /<details class="search-help" open><summary>Why nothing matched<\/summary>/.test(nothing.out));
  const many = Array.from({ length: 6 }, (_, i) => prop({ content_hash: `v2:m${i}` }));
  const full = await renderExplained(explained({ shortlist: many, shortlist_count: 6, search_help: searchHelp({ relaxations: [] }) }));
  check("a shortlist of 5 or more folds it away as 'How this search was counted'",
    /<details class="search-help"><summary>How this search was counted<\/summary>/.test(full.out), full.out.slice(0, 300));
  const plain = await render(success());
  check("no search_help (the page-load run): no panel", !plain.out.includes("search-help"));
}
{
  // A relaxation keyed by id with a separate kind, counted as 'count' (the explain design's
  // first spelling), with no adds: adds is the count less this search's own shortlist.
  const { out } = await renderExplained(explained({ search_help: searchHelp({ relaxations: [
    { id: "budget_plus_10", kind: "rerun", label: "Budget up to $825,000 (+10%)",
      brief_patch: { budget_max: 825000 }, count: 12, count_status: "exact" },
    { id: "whole_state", kind: "rerun", brief_patch: { primary_suburbs: [] }, shortlist_count: 40,
      count_status: "exact", capped: true },
  ] }) }));
  check("an id-keyed relaxation with no adds works out +10 from its count",
    out.includes("Budget up to $825,000 (+10%) → 12 matches (+10)"), out.slice(0, 1200));
  check("a relaxation with no label is named from its kind, and a capped count reads 40+",
    out.includes("The whole state, not just these suburbs → 40+ matches (+38)"), out.slice(0, 1200));
}
{
  // A change this form cannot express is never a button: its click would run a different
  // search from the one that was counted.
  const { out } = await renderExplained(explained({ search_help: searchHelp({ relaxations: [
    { kind: "drop_garage_type", label: "Any garage", brief_patch: { garage_type: "" },
      shortlist_count: 5, adds: 3, count_status: "exact" },
  ] }) }));
  check("a patch naming a field the form does not have gets no button, only the count in text",
    !/relax-btn[^>]*>Any garage/.test(out) && out.includes("Change the brief above to try: Any garage → 5 matches (+3)"),
    out.slice(0, 1200));
}
{
  const note = { said: "Queensland", used: [], method: "state", note: "a state, not a suburb, so it was not searched as a suburb" };
  const once = await renderExplained(explained({ location_resolution: [note],
    search_help: searchHelp({ location_notes: [note] }) }));
  const text = "&quot;Queensland&quot; — a state, not a suburb";
  check("a location note already shown above the results is not repeated in the panel",
    once.out.split(text).length - 1 === 1, `${once.out.split(text).length - 1} times`);
  const only = await renderExplained(explained({ location_resolution: [],
    search_help: searchHelp({ location_notes: [note],
      brief_adjustments: [{ field: "budget_max", from: 750, to: 750000, why: "read as thousands" }] }) }));
  check("the panel shows location notes and brief adjustments the notes above did not",
    only.out.includes(`class="sh-note">${text}`) && only.out.includes("Maximum price: $750 → $750,000 (read as thousands)"),
    only.out.slice(0, 900));
}
{
  const { out } = await renderExplained(explained({ search_help: searchHelp({
    funnel: { in_price_range_total: 3, gates: [{ code: payload("gatecode"), in_price_range: 2 }] },
    relaxations: [
      { kind: "budget_plus_10pct", label: payload("relax_label"), brief_patch: { budget_max: 825000 },
        shortlist_count: 12, adds: 10, count_status: "exact" },
      { kind: "budget_plus_50k", label: payload("nochange_label"), brief_patch: { budget_max: 800000 },
        shortlist_count: 2, adds: 0, count_status: "exact" },
      { kind: "radius_20", label: payload("uncounted_label"), brief_patch: { search_radius_km: 20 },
        count_status: "not_computed" },
    ],
    location_notes: [{ said: payload("sh_said"), used: [], method: "unresolved", note: payload("sh_note") }],
    brief_adjustments: [{ field: payload("sh_field"), note: payload("sh_adj") }],
    error: payload("sh_error"),
  }), brief_used: { state: payload("sh_state"), budget_max: 750000 } }));
  const problems = markupProblems(out);
  check("everything in search_help renders as text (labels, gates, notes, error, state)",
    // An unknown gate code is shown with its underscores as spaces, so its payload is too.
    problems.length === 0 && out.includes(shown("gatecode").replace(/_/g, " "))
    && ["relax_label", "nochange_label", "uncounted_label", "sh_said",
      "sh_note", "sh_field", "sh_adj", "sh_error", "sh_state"].every(k => out.includes(shown(k))),
    problems.slice(0, 3).join(" | ") || ["relax_label", "nochange_label", "uncounted_label", "sh_said",
      "sh_note", "sh_field", "sh_adj", "sh_error", "sh_state"].filter(k => !out.includes(shown(k))).join(", ")
      || "gatecode");
}

// ------------------------------------------------------------------ a relaxation click
console.log("\n--- a relaxation click writes the change into the form and searches straight away ---");
{
  const s = sandbox();
  s.el("clientName").value = "";
  s.el("budgetMax").value = "750000";
  s.respond(async () => reply(200, explained()));
  await s.api.runResearchAgent({ explain: true });
  await settle();
  const helpBox = child(s, "search-help");
  const button = helpBox ? helpBox.querySelectorAll(".relax-btn").find(x => x.dataset.relax === "2") : null;
  check("the +10% button is wired", !!(button && typeof button.onclick === "function"));
  // A spy in front of the real function: what it was asked, and what the form held then.
  const real = s.ctx.runResearchAgent;
  const spied = [];
  s.ctx.runResearchAgent = (opts) => {
    spied.push({ opts, budgetMax: s.el("budgetMax").value, clientName: s.el("clientName").value });
    return real(opts);
  };
  if (button && typeof button.onclick === "function") button.onclick();
  await settle();
  check("clicking it with a blank client name calls runResearchAgent once, with budget_max 825000 in the form",
    spied.length === 1 && spied[0].budgetMax === "825000" && spied[0].clientName === "",
    JSON.stringify(spied));
  check("... asking for the explanation", spied.length === 1 && spied[0].opts && spied[0].opts.explain === true);
  const body = s.calls.fetch[1] ? JSON.parse(s.calls.fetch[1].opts.body) : {};
  check("... which posts budget_max 825000 with explain:true",
    s.calls.fetch.length === 2 && body.client_brief && body.client_brief.budget_max === 825000 && body.explain === true,
    `${s.calls.fetch.length} POST(s) ${JSON.stringify(body).slice(0, 200)}`);
  check("... the preferred price, left blank, follows the new maximum without being pinned in the box",
    s.el("budgetPref").value === "" && body.client_brief && body.client_brief.preferred_spending_cap === 825000,
    JSON.stringify({ box: s.el("budgetPref").value, sent: body.client_brief && body.client_brief.preferred_spending_cap }));
  check("... and never through the form's submit",
    s.calls.requestSubmit === 0 && s.calls.dispatchEvent === 0,
    `requestSubmit ${s.calls.requestSubmit}, dispatchEvent ${s.calls.dispatchEvent}`);
}
{
  const s = sandbox();
  s.respond(async () => reply(200, explained()));
  await s.api.runResearchAgent({ explain: true });
  await settle();
  const helpBox = child(s, "search-help");
  const reveal = helpBox ? helpBox.querySelectorAll(".relax-btn").find(x => x.dataset.relax === "0") : null;
  const ncBox = child(s, "nc-group");
  const group = ncBox ? ncBox.querySelector(".nc-group") : null;
  check("the needs-confirming group starts closed", !!group && group.open !== true);
  if (reveal && typeof reveal.onclick === "function") reveal.onclick();
  await settle();
  check("the reveal button opens the needs-confirming group and searches nothing",
    !!group && group.open === true && s.calls.fetch.length === 1, `open ${group && group.open}, ${s.calls.fetch.length} POST(s)`);
}

// ------------------------------------------------------------------ applyBriefPatch
console.log("\n--- applyBriefPatch writes every field a relaxation can change ---");
{
  const s = sandbox();
  for (const [id, v] of Object.entries({ budgetMin: "", budgetMax: "750000", budgetPref: "740000",
    stateSelect: "QLD", suburbs: "Ripley", searchRadius: "0", bedrooms: "4", bathrooms: "", carSpaces: "2",
    storeys: "1", landSize: "", houseSize: "180" })) s.el(id).value = v;
  s.el("requiresIncentive").checked = true;
  s.productBoxes.find(x => x.value === "House & Land").checked = true;
  const ok = s.api.applyBriefPatch({
    budget_min: 300000, budget_max: 9007199254740991, preferred_spending_cap: 700000, state: "VIC",
    primary_suburbs: ["Tarneit", "Werribee"], search_radius_km: 20, bedrooms_min: 0, bathrooms_min: 2,
    car_spaces_min: 0, storeys_max: null, house_size_min_sqm: 0, land_size_min_sqm: 350,
    product_type: ["Townhouse"], requires_incentive: false,
  });
  const got = Object.fromEntries(["budgetMin", "budgetMax", "budgetPref", "stateSelect", "suburbs",
    "searchRadius", "bedrooms", "bathrooms", "carSpaces", "storeys", "landSize", "houseSize"].map(id => [id, s.el(id).value]));
  const want = { budgetMin: "300000", budgetMax: "", budgetPref: "700000", stateSelect: "VIC",
    suburbs: "Tarneit, Werribee", searchRadius: "20", bedrooms: "", bathrooms: "2", carSpaces: "",
    storeys: "", landSize: "350", houseSize: "" };
  check("every box takes the patch (a 0 minimum and no ceiling read as blank)",
    ok === true && JSON.stringify(got) === JSON.stringify(want), JSON.stringify(got));
  check("the product type boxes and the rebate box follow the patch",
    s.productBoxes.filter(x => x.checked).map(x => x.value).join() === "Townhouse"
    && s.el("requiresIncentive").checked === false,
    JSON.stringify(s.productBoxes.filter(x => x.checked).map(x => x.value)));
  s.respond(async () => reply(200, success()));
  await s.api.runResearchAgent({ explain: true });
  await settle();
  const sent = JSON.parse(s.calls.fetch[0].opts.body).client_brief;
  check("the search that follows sends what the patch said",
    sent.budget_min === 300000 && sent.budget_max === Number.MAX_SAFE_INTEGER && sent.preferred_spending_cap === 700000
    && sent.state === "VIC" && sent.primary_suburbs.join() === "Tarneit,Werribee" && sent.search_radius_km === 20
    && sent.bedrooms_min === 0 && sent.bathrooms_min === 2 && sent.car_spaces_min === 0 && sent.storeys_max === 3
    && sent.land_size_min_sqm === 350 && sent.house_size_min_sqm === 0 && sent.product_type.join() === "Townhouse"
    && sent.requires_incentive === false, JSON.stringify(sent));
}
{
  const s = sandbox();
  s.el("stateSelect").value = "QLD";
  check("'Any state' clears the State select", s.api.applyBriefPatch({ state: "Any state" }) === true
    && s.el("stateSelect").value === "", JSON.stringify(s.el("stateSelect").value));
  s.el("stateSelect").value = "QLD";
  s.el("budgetMax").value = "750000";
  check("a state the select cannot show is refused, and nothing is written",
    s.api.applyBriefPatch({ state: "TAS", budget_max: 825000 }) === false
    && s.el("stateSelect").value === "QLD" && s.el("budgetMax").value === "750000");
  check("a field the form does not have is refused, and nothing is written",
    s.api.applyBriefPatch({ budget_max: 825000, garage_type: "double" }) === false && s.el("budgetMax").value === "750000");
  check("an empty or missing patch is refused",
    s.api.applyBriefPatch({}) === false && s.api.applyBriefPatch(null) === false && s.api.applyBriefPatch([1]) === false);
}
{
  // The boxes change without an input event, so the price echo is repainted by the patch
  // itself -- not only by a search that may or may not follow.
  const s = sandbox();
  s.el("budgetMax").value = "750000";
  s.api.applyBriefPatch({ budget_max: 825000 });
  check("a patch repaints the price echo", s.el("budgetEcho").textContent === "Max = $825,000"
    && s.el("budgetEcho").hidden === false, JSON.stringify(s.el("budgetEcho").textContent));
}

// ------------------------------------------------------------------ Needs confirming
console.log("\n--- Needs confirming: amber, closed, dated, and never in the shortlist ---");
{
  const s = sandbox();
  s.respond(async () => reply(200, explained()));
  await s.api.runResearchAgent({ explain: true });
  await settle();
  const out = dump(s.el("resultsContent"));
  const e = t => s.api.esc(t);
  const groupTag = (out.match(/<details\b[^>]*class="nc-group"[^>]*>/) || [""])[0];
  check("the group is a <details class=\"nc-group\"> with no open attribute",
    groupTag === '<details class="nc-group">', groupTag || "no .nc-group rendered");
  const group = out.slice(out.indexOf('<details class="nc-group"'), out.indexOf("</details>", out.indexOf('<details class="nc-group"')));
  check("its summary counts them", group.includes("<summary>3 more listings match but need confirming before you send them</summary>"),
    group.slice(0, 200));
  check("it has no .cmp-box and no benchmark button", group.length > 0 && !group.includes("cmp-box") && !group.includes("bench-btn"));
  const outsideGroup = out.replace(group, "");
  check("needs-confirming rows appear nowhere but the group (one compare tick and card per shortlist row)",
    (out.match(/class="cmp-box"/g) || []).length === 2 && (out.match(/class="prop-card/g) || []).length === 2
    && (out.match(/class="bench-btn"/g) || []).length === 2 && !outsideGroup.includes("Dawn, Walloon"),
    `${(out.match(/class="cmp-box"/g) || []).length} ticks, ${(out.match(/class="prop-card/g) || []).length} cards`);
  check("it has one card per entry", (group.match(/class="nc-card"/g) || []).length === 3);
  check("LAST_SHORTLIST holds only the shortlist",
    s.ctx.LAST_SHORTLIST.length === 2 && s.ctx.LAST_SHORTLIST.every(p => !String(p.content_hash).startsWith("v2:dawn")),
    JSON.stringify(s.ctx.LAST_SHORTLIST.map(p => p.content_hash)));
  check("nothing was selected for comparison, and COMPARISONS is the server's",
    s.ctx.SELECTED.size === 0 && JSON.stringify(s.ctx.COMPARISONS) === "{}");
  check("a card carries its own date: '20/08/2026' and '27 days'", group.includes("20/08/2026") && group.includes("27 days"));
  check("an emailed list says 'email last read', with the list's own age beside it",
    group.includes(e("Email last read 20/08/2026, 27 days ago · list first read 17/08/2026, 30 days ago")),
    (group.match(/class="nc-date">[^<]*/) || [""])[0]);
  check("... the stated status, and what to do before sending",
    group.includes("Stated: Available") && group.includes("Confirm price and availability with the builder before sending"));
  check("... why it needs confirming", group.includes(e("Needs confirming: not checked at the source in the last 14 days")));
  check("... the address, place, builder, price and bed / bath / car",
    group.includes("Lot 1228, Dawn, Walloon") && group.includes("Walloon, QLD") && group.includes("Builder not identified in source")
    && group.includes("$721,900") && group.includes("$725,900") && group.includes("4 Bed | 2 Bath | Car: not stated"),
    group.slice(0, 900));
  check("what the guard left out is said", group.includes(e("Also left out: 3 because a newer capture of the same lot says otherwise.")));
  check("the badge reads '2 Qualified · 3 to confirm (1438 Filtered)'",
    s.el("shortlistBadge").textContent === "2 Qualified · 3 to confirm (1438 Filtered)", s.el("shortlistBadge").textContent);
  const last = out.lastIndexOf('class="prop-card');
  check("the group comes after the last shortlist card", last >= 0 && out.indexOf('class="nc-group"') > last);
}
{
  const { out } = await render(success());
  const s = sandbox();
  s.respond(async () => reply(200, success()));
  await s.api.runResearchAgent();
  await settle();
  check("no needs_confirming: no group, and the badge is unchanged ('1 Qualified (4 Filtered)')",
    !out.includes("nc-group") && s.el("shortlistBadge").textContent === "1 Qualified (4 Filtered)",
    s.el("shortlistBadge").textContent);
}
{
  const s = sandbox();
  const a = dawn(1, { source_channel: "E-Agent", email_first_seen: null, list_age_days: null, date_checked: "03/09/2026",
                      age_days: 13, availability_as_stated: null, confirm: ["availability_unstated"] });
  const text = s.api.confirmDateText(a);
  check("a list that did not come by email reads 'Last checked <date> — N days ago'",
    text === "Last checked 03/09/2026 — 13 days ago", JSON.stringify(text));
  const card = s.api.needsConfirmingCard(a);
  check("... and an unstated one says the source doesn't say", card.includes("Source doesn't say whether it's available")
    && !card.includes("Stated:"));
  const channelOnly = s.api.confirmDateText(dawn(2, { email_first_seen: "", list_age_days: null }));
  check("an emailed row with no list date still says 'email last read'",
    channelOnly === "Email last read 20/08/2026, 27 days ago", JSON.stringify(channelOnly));
  const computed = s.api.confirmDateText(dawn(3, { age_days: null, list_age_days: null }));
  check("without age_days the age is worked out from the row's own dates",
    /^Email last read 20\/08\/2026, \d+ days ago · list first read 17\/08\/2026, \d+ days ago$/.test(computed), JSON.stringify(computed));
  check("no date at all says so", s.api.confirmDateText(dawn(4, { date_checked: "", source_channel: "Proxima", email_first_seen: null }))
    === "Last checked: no date recorded");
}
{
  // Capped at 50 by the server, with the exact count beside it.
  const { out, el } = await renderExplained(explained({ needs_confirming: [dawn(1228), dawn(1229)], needs_confirming_count: 60 }));
  check("a capped list says how many of the total it shows", out.includes("Showing the 2 best matches of 60."));
  check("... and the badge counts the total", el("shortlistBadge").textContent === "2 Qualified · 60 to confirm (1438 Filtered)",
    el("shortlistBadge").textContent);
}
{
  const hostile = dawn(1228, {
    lot_address: "<script>window.__xss.push(1)</script>", suburb: payload("nc_suburb"), state: payload("nc_state"),
    builder_name: payload("nc_builder"), availability_as_stated: payload("nc_stated"), date_checked: payload("nc_date"),
    email_first_seen: payload("nc_first"), advertised_package_price: payload("nc_price"), bedrooms: payload("nc_bed"),
    bathrooms: payload("nc_bath"), car_spaces: payload("nc_car"),
    source_link: { url: "https://builder.example.com/list.pdf?a=1&b=2", label: payload("nc_link"), opens: "document" },
  });
  const { out } = await renderExplained(explained({ needs_confirming: [hostile],
    needs_confirming_hidden: { [payload("nchidden")]: 2 } }));
  const group = out.slice(out.indexOf('<details class="nc-group"'));
  const problems = markupProblems(group);
  check("'<script>' in a needs-confirming address is escaped",
    group.includes("&lt;script&gt;window.__xss.push(1)&lt;/script&gt;") && !/<script/i.test(group));
  check("every needs-confirming field renders as text",
    // An unknown hidden reason is shown with its underscores as spaces, so its payload is too.
    problems.length === 0 && group.includes(shown("nchidden").replace(/_/g, " "))
    && ["nc_suburb", "nc_state", "nc_builder", "nc_stated", "nc_date", "nc_first", "nc_bed",
      "nc_bath", "nc_car", "nc_link"].every(k => group.includes(shown(k))),
    problems.slice(0, 3).join(" | ") || ["nc_suburb", "nc_state", "nc_builder", "nc_stated", "nc_date", "nc_first",
      "nc_bed", "nc_bath", "nc_car", "nc_link"].filter(k => !group.includes(shown(k))).join(", ") || "nchidden");
}
{
  // esc() keeps a URL inside href="", but a javascript: URL still runs when clicked.
  const bad = { url: " javascript:window.__xss.push(1)", label: "Price list", opens: "price list" };
  const { out } = await renderExplained(explained({
    shortlist: [prop({ source_link: bad })], shortlist_count: 1,
    needs_confirming: [dawn(1228, { source_link: bad }), dawn(1229, { source_link: { ...bad, url: "data:text/html,x" } })],
  }));
  check("a source link that is not http(s) is never a link, on a card or a needs-confirming card",
    !/href="\s*(javascript|data):/i.test(out) && !out.includes("javascript:"), (out.match(/href="[^"]*"/g) || []).join(" "));
  const good = await renderExplained(explained({
    needs_confirming: [dawn(1228, { source_link: { url: "https://builder.example.com/list.pdf", label: "Price list", opens: "price list" } })],
  }));
  check("... while an https link still shows, saying what it opens",
    good.out.includes('href="https://builder.example.com/list.pdf"') && good.out.includes("opens the price list it came from"));
}
{
  // Painted in pages of 25: a later page must land with the cards, above the amber group.
  const many = Array.from({ length: 30 }, (_, i) => prop({ content_hash: `v2:p${i}`, lot_address: `Card ${i}` }));
  const s = sandbox();
  s.respond(async () => reply(200, explained({ shortlist: many, shortlist_count: 30 })));
  await s.api.runResearchAgent({ explain: true });
  await settle();
  const more = s.ctx.document.getElementById("shortlistMore");
  check("'Show more' is offered for the last 5", more && typeof more.onclick === "function" && /Show 5 more/.test(more.textContent),
    more ? more.textContent : "no button");
  if (more && typeof more.onclick === "function") more.onclick();
  const out = dump(s.el("resultsContent"));
  check("after 'Show more' every card is still above the needs-confirming group",
    (out.match(/class="prop-card/g) || []).length === 30
    && out.lastIndexOf('class="prop-card') < out.indexOf('class="nc-group"')
    && out.includes("Card 29"), `${(out.match(/class="prop-card/g) || []).length} cards`);
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
