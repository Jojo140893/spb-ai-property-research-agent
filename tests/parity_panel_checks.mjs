// Exercises the Settings tab's source-to-search parity panel: the code between the
// PARITY-PANEL-BEGIN and PARITY-PANEL-END markers in index.html, sliced out and run under
// Node with fetch stubbed. Sliced rather than reimplemented, so it cannot drift from what
// ships.
//
// 7 Oct 2026. Coleen ran the same search in E-Agent and in the Research tab and E-Agent
// always showed more; nothing on the dashboard said so. parity_check.py now measures it every
// night and writes parity_report.json beside stock.json, and this panel is where she and
// Ahsan read it. What must hold:
//   - no report (a build the check has not run against) draws NOTHING -- never "all clear";
//   - a failing night says how many things there are to fix, row by row and failure by
//     failure, every value escaped (a builder or file name is text from a price list);
//   - a passing night says so, with no failure list;
//   - the report is fetched without the browser cache, and a signed-out answer (401) goes
//     to the sign-in screen like every other file on the tab.
//
// NOTHING HERE TOUCHES THE NETWORK: fetch is a stub.
//
//     node tests/parity_panel_checks.mjs
//     PP_INDEX=path/to/other/index.html node tests/parity_panel_checks.mjs   (mutation runs)
import { readFileSync } from "node:fs";
import vm from "node:vm";

const INDEX = process.env.PP_INDEX || new URL("../index.html", import.meta.url);
const html = readFileSync(INDEX, "utf8");

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  PASS  " + name); }
  else { fail++; console.log("  FAIL  " + name + (detail ? "  -> " + detail : "")); }
}

const slice = html.match(/\/\* PARITY-PANEL-BEGIN[\s\S]*?\/\* PARITY-PANEL-END \*\//);
check("the panel's code is found between its markers", !!slice);

const calls = { fetch: [], expired: 0 };
let answer = { status: 404, body: null };
const sandbox = {
  console: { log() {}, error() {} },
  esc: s => String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
  sessionExpired: () => { calls.expired++; },
  fetch: async (url, opts) => {
    calls.fetch.push([String(url), opts || {}]);
    if (answer.throws) throw new Error("offline");
    return { status: answer.status, ok: answer.status >= 200 && answer.status < 300,
             json: async () => JSON.parse(JSON.stringify(answer.body)) };
  },
};
vm.createContext(sandbox);
vm.runInContext((slice ? slice[0] : "") +
  "\nthis.parityPanel = parityPanel; this.loadParity = loadParity;" +
  "\nthis.resetParity = () => { PARITY = null; };", sandbox);

const FAILING = {
  version: 1, night: "06/10/2026", verdict: "FAIL",
  bars: { recent_nights: 3 },
  rows: [
    { state: "VIC", e_agent_lots: [2343, 256], lots: [2738, 306], buildings: [546, 84],
      shortlisted: [2738, 290], obvious_lots: 2779, excused_lots: 41, pass: false },
    { state: "WA", e_agent_lots: [0, 0], lots: [22, 22], buildings: [1, 1], pass: true },
  ],
  failures: [
    { bar: "shown", group: "VIC",
      text: "2,160 obvious lots hidden as truncated -- ids 432, 447" },
    { bar: "shown", group: "VIC", text: "Builder <img src=x onerror=alert(1)> & Sons" },
    { bar: "over-served", group: "WA", text: "11 of 113 shown lots" },
  ],
};

console.log("\n--- nothing to show draws nothing ---");
check("no report: no panel", sandbox.parityPanel(null) === "");
check("a report with no rows: no panel", sandbox.parityPanel({ verdict: "PASS", rows: [] }) === "");

console.log("\n--- a failing night ---");
const out = sandbox.parityPanel(FAILING);
check("the panel is drawn", out.includes('id="parityPanel"'), out.slice(0, 200));
check("it says how many things there are to fix", out.includes("3 to fix"));
check("the failing pill is red", /pill-reject/.test(out) && !/pill-recommend/.test(out));
check("it names the night", out.includes("06/10/2026"));
check("every state row is drawn", out.includes("<strong>VIC</strong>") && out.includes("<strong>WA</strong>"));
check("a lot share reads 'shown of obvious'", out.includes("306 of 2,738") && out.includes("(11.2%)"),
  out.match(/306[^<]*/)?.[0]);
check("a building share is drawn", out.includes("84 of 546"));
// Repair round 1, 7 Oct 2026: "shown" counts Needs confirming too, which Clare and Agent 2
// never read, and an excused lot leaves the count -- both said, not hidden in a ratio.
check("the shortlist share is drawn", out.includes("290 of 2,738") && out.includes("On the shortlist"));
check("the lots hidden for a reason are counted", out.includes("41 of 2,779") && out.includes("Hidden for a reason"));
check("a report from before the columns draws a dash", /<strong>WA<\/strong>[\s\S]*?&mdash;/.test(out));
check("an empty E-Agent share reads as a dash, not 0 of 0", !out.includes("0 of 0"));
check("a failing row is marked", /VIC<\/strong> <span[^>]*>&#10007;/.test(out));
check("every failure is listed", out.includes("2,160 obvious lots hidden as truncated")
  && out.includes("11 of 113 shown lots"));
check("a failure's text is escaped",
  out.includes("Builder &lt;img src=x onerror=alert(1)&gt; &amp; Sons") && !out.includes("<img src=x"));

console.log("\n--- a passing night ---");
const passing = sandbox.parityPanel(Object.assign({}, FAILING, {
  verdict: "PASS", failures: [], rows: [FAILING.rows[1]] }));
check("it says everything is shown", passing.includes("everything shown") && /pill-recommend/.test(passing));
check("no failure list", !passing.includes("&#10007;"));

console.log("\n--- loading the report ---");
const run = async () => {
  answer = { status: 404, body: null };
  sandbox.resetParity();
  check("a build with no report reads as none", (await sandbox.loadParity()) === null);
  check("it is fetched from parity_report.json without the cache",
    calls.fetch.length === 1 && calls.fetch[0][0] === "parity_report.json"
    && calls.fetch[0][1].cache === "no-store", JSON.stringify(calls.fetch));
  answer = { status: 200, body: FAILING };
  sandbox.resetParity();
  const got = await sandbox.loadParity();
  check("a report is read", got && got.verdict === "FAIL" && got.rows.length === 2);
  answer = { status: 401, body: {} };
  sandbox.resetParity();
  check("signed out reads as none", (await sandbox.loadParity()) === null);
  check("signed out goes to the sign-in screen", calls.expired === 1);
  answer = { throws: true };
  sandbox.resetParity();
  check("a network error reads as none", (await sandbox.loadParity()) === null);
};

console.log("\n--- the Settings tab draws it ---");
// \r?: a Windows checkout (core.autocrlf) carries CRLF; the repository stores LF.
const settings = html.match(/async function loadSettings\(\) \{[\s\S]*?\r?\n    \}\r?\n/);
check("loadSettings is found", !!settings);
check("loadSettings loads the report", settings && settings[0].includes("await loadParity();"));
check("loadSettings draws the panel", settings && settings[0].includes("${parityPanel(PARITY)}"));

await run();
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
