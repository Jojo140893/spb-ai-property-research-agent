// Behaviour of the N-option comparison document. The generator is extracted from
// index.html by test_comparison_report.py and written beside this file as generator.mjs,
// so these checks always run against the code that actually ships rather than a copy.
import { comparisonDocument, clientReportDocument, buildShareLink,
         unpackShare, shareBar } from './generator.mjs';

const full = {
  content_hash: 'h1', lot_address: '12 Banksia Way', suburb: 'Armstrong Creek', state: 'VIC',
  builder_name: 'Banksia Homes', house_design: 'Aspen 22', bedrooms: 4, bathrooms: 2,
  car_spaces: 2, land_size_sqm: 350, house_size_sqm: 167.22,
  advertised_package_price: 728500, incentive_amount: 10000,
  incentive_text: '$10,000 rebate', score: 88, title_status: 'Titled',
  date_checked: '14/08/2026',
};
// The common case, not the exception: only 39% of live stock states a dwelling area and
// 49% a suburb, so a document that renders these as "0" or "undefined" is the normal one.
const sparse = {
  content_hash: 'h2', lot_address: '5 Warralily Blvd', suburb: '', state: 'VIC',
  builder_name: '', land_size_sqm: 398, house_size_sqm: null,
  advertised_package_price: 775160, incentive_amount: 0, incentive_text: '', score: 81,
};
// Builder spreadsheets are not trusted input, and this document is opened with
// document.write into a new window — an unescaped bracket is script execution.
const hostile = {
  content_hash: 'h3', lot_address: '<scr' + 'ipt>alert(1)</scr' + 'ipt>',
  suburb: '"><img src=x onerror=alert(2)>', state: 'VIC', builder_name: 'A & B Homes',
  advertised_package_price: 720600, incentive_amount: 0, incentive_text: '', score: 95,
  land_size_sqm: 427, house_size_sqm: 175.28,
};

let bad = 0;
const check = (name, cond, extra) => {
  console.log((cond ? '  PASS  ' : '  FAIL  ') + name + (cond ? '' : '  -> ' + extra));
  if (!cond) bad++;
};

for (const [name, props] of [
  ['N=2', [full, sparse]],
  ['N=4', [full, sparse, hostile, full]],
  ['N=8', new Array(8).fill(full)],          // identical records: see the bestIdx note
  ['N=12', Array.from({ length: 12 }, (_, i) => ({ ...full, content_hash: 'h' + i })) ],
]) {
  const h = comparisonDocument(props);
  check(name + ' renders a document', h.length > 500, h.length);
  check(name + ' one table row per option',
    (h.match(/<tr/g) || []).length === props.length + 1, (h.match(/<tr/g) || []).length);
  check(name + ' one detail block per option',
    (h.match(/class="opt[ "]/g) || []).length === props.length);
  check(name + ' exactly one preferred tag',
    (h.match(/class="tag"/g) || []).length === 1, (h.match(/class="tag"/g) || []).length);
  check(name + ' exactly one highlighted row',
    (h.match(/tr class="best"/g) || []).length === 1);
}

const h4 = comparisonDocument([full, sparse, hostile, full]);
const body = h4.slice(h4.indexOf('<body>'));

console.log('\n--- the preferred option is the engine\'s ranking, not a new judgement ---');
check('banner names the highest-scoring option (3, score 95)', h4.includes('Option 3 &mdash;'));

console.log('\n--- nothing is invented ---');
check('a missing figure is a dash', body.includes('class="na"'));
check('no undefined / NaN reaches the page', !/undefined|NaN/.test(h4));
check('a missing price is never rendered as $0', !/\$0\b/.test(body));

console.log('\n--- rebate and effective price ---');
check('effective price = package - rebate ($718,500)', h4.includes('$718,500'));
check('rebate columns appear when any option has one', h4.includes('<th>Rebate</th>'));
const noReb = comparisonDocument([{ ...full, incentive_amount: 0, incentive_text: '' }, sparse]);
check('rebate columns are omitted when none does',
  !noReb.includes('<th>Rebate</th>') && !noReb.includes('Effective Price'));

console.log('\n--- untrusted vendor text cannot execute ---');
check('no raw <script survives', !body.includes('<scr' + 'ipt'), 'SCRIPT INJECTED');
// The letterhead is a legitimate <img> (our logo, inlined as a data URI), so a blanket
// "no <img>" test would fail on our own branding. Assert the INJECTED shape instead:
// no payload-sourced attributes, and every img on the page is the logo.
// A raw <img> tag, EXCLUDING our own logo. Matching on 'onerror=' alone fails here:
// the escaped payload (&lt;img src=x onerror=...&gt;) contains that text as inert
// characters, which is exactly what correct escaping looks like.
check('no <img> tag other than our logo', !/<img(?![^>]*class="logo")/i.test(body),
  'IMG INJECTED');
check('the payload is present but inert text',
  body.includes('&lt;img src=x onerror=alert(2)&gt;'));
check('a quote cannot break out of an attribute', !/<td[^>]*"><img/.test(body));
check('an ampersand in vendor text survives as escaped text', body.includes('&amp;'));

console.log('\n--- the consultant writes the prose, the system does not ---');
check('every option gets an empty notes field',
  (h4.match(/<textarea/g) || []).length === 4);
check('no suburb/infrastructure commentary is pre-filled',
  !/growth area|infrastructure:|investment strengths:/i.test(body.replace(/placeholder="[^"]*"/g, '')));

// ---------------------------------------------------------------- the client report
//
// Coleen, 14 Aug: "I have selected three different properties from research scoring, and
// it's not implementing in open client report ... I'm still seeing first three ranks, not
// the ones that I selected." The button opened the document the SERVER renders at search
// time, which covers one search's top 3 and cannot see a selection — still less one made
// across several searches. These pin that the selection drives it.
const withMarket = {
  content_hash: 'c1', lot_address: '12 Banksia Way', suburb: 'Armstrong Creek', state: 'VIC',
  builder_name: 'Banksia Homes', bedrooms: 4, bathrooms: 2, car_spaces: 2,
  land_size_sqm: 350, house_size_sqm: 167.22, advertised_package_price: 728500,
  realistic_total_price: 728500, incentive_amount: 10000, score: 88,
  estimated_rent_min: 520, estimated_rent_max: 560,
  recommendation_reason: 'Best balance of price and land in the target suburb.',
  benchmark: {
    avg_market_price: 790000, basis: 'market', classification: 'below market',
    comparables: [{ suburb: 'Armstrong Creek', state: 'VIC', bedrooms: 4,
                    price: 795000, source: 'REA' }],
  },
};
// Stored stock usually carries no rent and no benchmark. This is the normal case.
const bare = {
  content_hash: 'c2', lot_address: '5 Warralily Blvd', suburb: '', state: 'VIC',
  builder_name: '', land_size_sqm: 398, house_size_sqm: null,
  advertised_package_price: 775160, realistic_total_price: 775160, score: 81,
  estimated_rent_min: 0, estimated_rent_max: 0,
  amenities_summary: 'No amenity assessment captured for this listing.',
};

console.log('\n--- the client report follows the selection, not the ranking ---');
for (const n of [1, 3, 7]) {
  const props = Array.from({ length: n }, (_, i) => ({ ...bare, content_hash: 'c' + i }));
  const h = clientReportDocument(props);
  check(`${n} selected -> ${n} cards`,
    (h.match(/class="opt"/g) || []).length === n, (h.match(/class="opt"/g) || []).length);
  check(`${n} selected -> the document says ${n}`, h.includes(`following ${n} option(s)`));
}
const cr = clientReportDocument([withMarket, bare]);
check('no rank wording leaks into a selection-driven report', !/Rank #/.test(cr));

console.log('\n--- the market check (Coleen, 22 Jul) ---');
check('purchase price against the market', cr.includes('average market price'));
check('the saving is computed ($61,500)', cr.includes('$61,500'));
check('comparables are tabulated', cr.includes('<table class="comps"'));
check('absent comparables are stated, not faked',
  cr.includes('Market comparables to be confirmed'));

console.log('\n--- the client report invents nothing ---');
check('no $0-$0 rent row for stock with no rent', !/\$0&ndash;\$0/.test(cr));
check('no 0.00% yield', !cr.includes('0.00%'));
check('a yield IS shown when rent is known', /\d\.\d\d%/.test(cr));
check('boilerplate amenity line suppressed', !cr.includes('No amenity assessment'));
check('no undefined / NaN', !/undefined|NaN/.test(cr));

console.log('\n--- the client report escapes untrusted text ---');
const nasty = clientReportDocument([{
  ...bare, lot_address: '<scr' + 'ipt>alert(1)</scr' + 'ipt>',
  suburb: '"><img src=x onerror=alert(2)>', builder_name: 'A & B Homes',
  recommendation_reason: '<scr' + 'ipt>alert(3)</scr' + 'ipt>',
}]);
const nb = nasty.slice(nasty.indexOf('<body>'));
check('no raw <script survives', !nb.includes('<scr' + 'ipt'), 'SCRIPT INJECTED');
// The letterhead is a legitimate <img> (our logo, inlined as a data URI), so a blanket
// "no <img>" test would fail on our own branding. Assert the INJECTED shape instead:
// no payload-sourced attributes, and every img on the page is the logo.
// A raw <img> tag, EXCLUDING our own logo. Matching on 'onerror=' alone fails here:
// the escaped payload (&lt;img src=x onerror=...&gt;) contains that text as inert
// characters, which is exactly what correct escaping looks like.
check('no <img> tag other than our logo', !/<img(?![^>]*class="logo")/i.test(nb),
  'IMG INJECTED');
check('an ampersand in vendor text survives as escaped text', nb.includes('&amp;'));

// ------------------------------------------------------- no internal scoring leaks
//
// These documents go to a buyer. The SPB score ranks stock for a consultant; in a client's
// hands "88/100" invites a negotiation about our scoring rather than about the property,
// and Coleen's own example one-pager carries none. Greps the OUTPUT, not the source —
// the first attempt at removing it left an HTML comment explaining the scoring inside the
// document, readable in View Source.
console.log('\n--- no internal scoring reaches a client document ---');
for (const [label, d] of [['comparison', comparisonDocument([withMarket, bare], null)],
                          ['client report', clientReportDocument([withMarket, bare], null)]]) {
  check(label + ': no "/ 100"', !d.includes('/ 100'));
  check(label + ': no "SPB score"', !d.includes('SPB score'), 'leaked into the document');
  check(label + ': no HTML comment about scoring', !/<!--[^>]*[Ss]core/.test(d), 'comment leaked');
}
check('the comparison keeps its SPB PREFERRED banner',
  comparisonDocument([withMarket, bare], null).includes('SPB PREFERRED'));

// ------------------------------------------------------------- the shareable link
//
// The report travels in the URL fragment, which browsers never transmit, so a client can
// open it with no login and nothing about them or our stock is stored or sent anywhere.
console.log('\n--- shareable client links ---');
{
  const P = n => Array.from({ length: n }, (_, i) => ({ ...withMarket, content_hash: 'k' + i }));
  for (const n of [2, 3, 8]) {
    const url = await buildShareLink('client', P(n), 'Jane Buyer');
    check(`${n} options: a link is produced`, url.startsWith('https://') && url.includes('/r#'));
    const back = await unpackShare(url.slice(url.indexOf('#') + 1));
    check(`${n} options: round-trips unchanged`,
      back.p.length === n && back.c === 'Jane Buyer' && back.k === 'client');
  }
  check('beyond the limit it refuses rather than truncating',
    (await buildShareLink('client', P(9), 'x')) === '');
  check('and the document says why', shareBar('').includes('too many to fit'));
  check('the shared copy carries no share bar of its own', shareBar(null) === '');

  const url = await buildShareLink('client', P(3), 'Jane Buyer');
  const back = await unpackShare(url.slice(url.indexOf('#') + 1));
  const shared = clientReportDocument(back.p, null, back.c);
  check('the shared document renders every option', (shared.match(/class="opt"/g) || []).length === 3);
  check('the shared document names the client', shared.includes('Jane Buyer'));
  check('the shared document has no Copy-link bar', !shared.includes('Client link'));

  // A recipient can edit the fragment. It must fail visibly, not render nonsense.
  let threw = false;
  try { await unpackShare('1.not-valid-base64!!'); } catch (e) { threw = true; }
  check('a corrupted link throws so the page can say so', threw);
  threw = false;
  try { await unpackShare('nodot'); } catch (e) { threw = true; }
  check('a malformed fragment throws', threw);
}

console.log(bad === 0 ? '\nALL CHECKS PASSED' : '\n' + bad + ' FAILED');
process.exit(bad ? 1 : 0);
