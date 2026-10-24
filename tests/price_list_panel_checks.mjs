// Exercises the Settings tab's "Price lists we could not read" lines: the code between the
// PRICE-LIST-LINES-BEGIN and PRICE-LIST-LINES-END markers in index.html, sliced out and run
// under Node. Sliced rather than reimplemented, so it cannot drift from what ships.
//
// Ahsan, 24 Oct 2026: each entry should open, so he can check the price list himself. Most
// of the links are Dropbox folder shares, so the page never carries one: channels.json
// gives each entry an opaque 16-hex id and /api/price_list resolves it behind the sign-in.
// What must hold:
//   - an id the build makes becomes a link to /api/price_list?id=..., in a new tab, with
//     no opener and no referrer;
//   - no id, or anything else in its place (a raw link, javascript:, an id with markup
//     after it), and the entry shows its NAME, with no link at all;
//   - the name is escaped either way (it is text from a builder's file or folder).
//
// NOTHING HERE TOUCHES THE NETWORK.
//
//     node tests/price_list_panel_checks.mjs
//     PL_INDEX=path/to/other/index.html node tests/price_list_panel_checks.mjs   (mutation runs)
import { readFileSync } from "node:fs";
import vm from "node:vm";

const INDEX = process.env.PL_INDEX || new URL("../index.html", import.meta.url);
const html = readFileSync(INDEX, "utf8");

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  PASS  " + name); }
  else { fail++; console.log("  FAIL  " + name + (detail ? "  -> " + detail : "")); }
}

const slice = html.match(/\/\* PRICE-LIST-LINES-BEGIN \*\/[\s\S]*?\/\* PRICE-LIST-LINES-END \*\//);
check("the lines' code is found between its markers", !!slice);
// The page's one escaper, reused rather than copied.
const escMatch = html.match(/^    const esc = [\s\S]*?\[c\]\)\);$/m);
check("the page's esc() helper is found", !!escMatch);
if (!slice || !escMatch) { console.log(`\n${pass} passed, ${fail} failed`); process.exit(1); }

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(escMatch[0].replace(/^\s*const /, "var ") + "\n" +
                slice[0].replace(/const (prettyFile|priceListLine) =/g, "var $1 ="), sandbox);
const line = sandbox.priceListLine;
check("priceListLine is defined", typeof line === "function");

const ID = "0123456789abcdef";
const NAME = "92 New Street, South Kingsville - Pricelist.pdf (dropbox.com)";

{
  const out = line(NAME, ID);
  check("an id opens through /api/price_list", out.includes(`href="/api/price_list?id=${ID}"`), out);
  check("  ...in a new tab", out.includes('target="_blank"'), out);
  check("  ...with no opener and no referrer", out.includes('rel="noopener noreferrer"'), out);
  check("  ...showing the name", out.includes(NAME), out);
  check("  ...and exactly one link", (out.match(/<a /g) || []).length === 1, out);
}

const NO_LINK = [
  ["no id at all", undefined],
  ["an empty id", ""],
  ["a raw folder link", "https://www.dropbox.com/scl/fo/abc/def?rlkey=k&dl=0"],
  ["javascript:", "javascript:alert(document.cookie)"],
  ["an id with markup after it", ID + '" onmouseover="alert(1)'],
  ["an id with a path after it", ID + "/../../stock.json"],
  ["an id one character short", ID.slice(1)],
  ["upper-case hex (the build writes lower)", ID.toUpperCase()],
  ["a number", 12345678901234567],
];
for (const [why, id] of NO_LINK) {
  const out = line("Victoria (VIC Townhouse) - no price list in the folder.", id);
  check(`${why}: the name, and no link`,
        out === "<div>Victoria (VIC Townhouse) - no price list in the folder.</div>", out);
}

{
  const out = line("Koops Price.pdf", "");
  check("an entry with no recorded link still shows its name", out === "<div>Koops Price.pdf</div>", out);
}
{
  const evil = '<img src=x onerror="alert(1)">.pdf';
  for (const id of ["", ID]) {
    const out = line(evil, id);
    check(`the name is escaped (${id ? "with" : "without"} a link)`,
          !out.includes("<img") && out.includes("&lt;img"), out);
  }
}
{
  // The panel draws every entry through priceListLine, pairing name i with id i.
  check("the panel renders each entry through priceListLine with its own id",
        /\.files\.map\(\(f, i\) =>\s*priceListLine\(f, \(CHANNELS\.unreadable_stocklists\.open \|\| \[\]\)\[i\]\)\)/
          .test(html));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
