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

let failures = 0;

// The document carries exactly ONE script of our own — the one-page fitter, tagged
// data-spb="fit". The injection checks below strip it first and then assert that NOTHING
// else script-shaped survives, so untrusted vendor text still cannot introduce one. A
// blanket "no <script>" would have been satisfied by deleting our own feature.
const OURS = /<script data-spb="fit">[\s\S]*?<\/script>/g;
const stripOurs = h => {
  const n = (h.match(OURS) || []).length;
  if (n !== 1) { failures++; console.error(`FAIL exactly one SPB script -> found ${n}`); }
  return h.replace(OURS, '');
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
  // Options as ROWS, matching the one-pager Coleen sent as the target: it fits four
  // options on one page, which a feature-per-row table does not. +1 for the header.
  check(name + ' one table row per option',
    (h.match(/<tr/g) || []).length === props.length + 1, (h.match(/<tr/g) || []).length);
  check(name + ' one detail block per option',
    (h.match(/class="opt[ "]/g) || []).length === props.length);
  // Several badges now (BEST OVERALL / LARGEST HOME / MOST LAND / LOWEST EFFECTIVE
  // PRICE), all computed from the selected set — but exactly one option is preferred.
  check(name + ' exactly one BEST OVERALL',
    (h.match(/BEST OVERALL/g) || []).length === 1, (h.match(/BEST OVERALL/g) || []).length);
  check(name + ' no option wears two badges',
    (h.match(/class="tag"/g) || []).length <= props.length);
  check(name + ' exactly one highlighted row',
    (h.match(/tr class="best"/g) || []).length === 1);
}

const h4 = comparisonDocument([full, sparse, hostile, full]);
const body = h4.slice(h4.indexOf('<body>'));

console.log('\n--- the preferred option is the engine\'s ranking, not a new judgement ---');
check('banner names the highest-scoring option (3, score 95)',
  /SPB PREFERRED OPTION: OPTION 3/.test(h4), 'banner names the wrong option');

console.log('\n--- nothing is invented ---');
check('a missing figure is a dash', body.includes('class="na"'));
check('no undefined / NaN reaches the page', !/undefined|NaN/.test(h4));
check('a missing price is never rendered as $0', !/\$0\b/.test(body));

console.log('\n--- rebate and effective price ---');
check('effective price = package - rebate ($718,500)', h4.includes('$718,500'));
check('a Rebate column appears when any option has one', h4.includes('>Rebate<'));
const noReb = comparisonDocument([{ ...full, incentive_amount: 0, incentive_text: '' }, sparse]);
check('the Rebate column is omitted when none does',
  !noReb.includes('>Rebate<') && !noReb.includes('>Effective Price<'));

console.log('\n--- untrusted vendor text cannot execute ---');
check('no raw <script survives', !stripOurs(body).includes('<scr' + 'ipt'), 'SCRIPT INJECTED');
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
// Three per option (strengths / infrastructure / trade-off) plus one recommendation.
// contenteditable spans since 19 Aug, not textareas: a label above a bordered box cost
// three lines of chrome per note, and four options would not fit one page. Still one
// editable field each — the count is the point, not the element.
const fields = h => (h.match(/class="ed"/g) || []).length;
check('each option gets its own notes fields', fields(h4) === 4 * 3 + 1, fields(h4));
check('every field is editable',
  (h4.match(/contenteditable="true"/g) || []).length === 13,
  (h4.match(/contenteditable="true"/g) || []).length);
// The LABELS are ours and always present; what must never be pre-filled is commentary
// about the AREA, which no source gives us. Placeholders are stripped first — they are
// guidance for the consultant, not content, and they never print.
check('no suburb/infrastructure commentary is pre-filled',
  !/growth area|planned town centres|major activity centre|rail connectivity/i
     .test(body.replace(/data-ph="[^"]*"/g, '')));

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
// class="opt" OR class="opt opt-best": since the two documents merged (18 Aug) the
// client-facing render marks its preferred block, which the strict pattern missed —
// it counted n-1 and reported a defect that was not there.
const WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight',
               'nine', 'ten'];
const blocks = h => (h.match(/class="opt[ "]/g) || []).length;
for (const n of [1, 3, 7]) {
  const props = Array.from({ length: n }, (_, i) => ({ ...bare, content_hash: 'c' + i }));
  const h = clientReportDocument(props);
  check(`${n} selected -> ${n} cards`, blocks(h) === n, blocks(h));
  // The merged lede states the count in words: "reviewed three packages across ..."
  check(`${n} selected -> the document says ${n}`,
    new RegExp('reviewed\\s+' + (WORDS[n] || n) + '\\s*\\n?\\s*package').test(h));
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
check('no raw <script survives', !stripOurs(nb).includes('<scr' + 'ipt'), 'SCRIPT INJECTED');
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
  check('the shared document renders every option',
    (shared.match(/class="opt[ "]/g) || []).length === 3);
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


console.log('\n--- one document, one page (Coleen and Colin, 18 Aug) ---');
{
  // "Consolidate into a single, one-page PDF by eliminating redundant information."
  // Both entry points survive because r.html dispatches share links on k, so a link
  // already in a client's inbox still renders — but they must now produce the SAME
  // document, or a client still receives one of two.
  const viaCompare = comparisonDocument([full, sparse], null, {});
  const viaClient = clientReportDocument([full, sparse], null, '');
  check('both entry points render one and the same document', viaCompare === viaClient);

  const h = comparisonDocument([full, hostile], null, {});
  // THE COLUMNS OF COLEEN'S OWN ONE-PAGER, in her order. The 18 Aug asks are folded INTO
  // these columns rather than added beside them: a column costs width, and one page is
  // the requirement.
  const headings = [...h.matchAll(/<th>([^<]*)<\/th>/g)].map(m => m[1]);
  check('the seven target columns, in order',
    headings.join('|') === 'Option|Location|Land|House|Package|Rebate|Effective Price',
    headings.join('|'));
  check('no Title column — the target has none', !h.includes('<th>Title</th>'));
  check('no Bed / Bath / Car column either', !h.includes('<th>Bed / Bath / Car</th>'));

  // ...but both asks still land, inside the cells they belong to.
  check('bed / bath / car rides in the House cell', /class="sub">\s*4 \/ 2 \/ 2/.test(h));
  check('and is gone from the assessment block', !h.includes('<b>Bed / Bath / Car</b>'));
  check("the street address rides in the Location cell", /class="sub">[^<]*Banksia Way/.test(h));

  check('the address is not repeated in the block', !h.includes('<b>Address</b>'));
  check('land and house are not restated in the block', !h.includes('<b>Land / House</b>'));
  check('package price is not restated in the block', !h.includes('<b>Package</b>'));

  // The assessment grid reads best-first, as the target's does (OPTION 4, 1, 3, 2).
  const order = [...h.matchAll(/OPTION (\d+) &mdash;/g)].map(m => m[1]);
  check('the preferred option is assessed first', order[0] === '2', order.join(','));

  // Contact details on the document itself: a forwarded PDF has to stay traceable, and
  // the letterhead is lost the moment somebody screenshots the table.
  check('the footer carries the SPB email',
    h.includes('info@smartpropertybuying.com.au'));
  check('and the mobile Coleen named, ending 212', h.includes('0411 502 212'));
  // Off the site's contact page, not typed from memory — it also publishes a landline
  // and only one of the two is the number the meeting asked for.
  check('not the landline', !h.includes('7056 7191'));
  check('the page is set up to print as one A4 sheet', h.includes('@page'));

  // The assessment textareas open PREFILLED from what we actually hold, not empty and
  // not invented.
  // recommendation_reason is CONSULTANT text, and it must never reach a client.
  // scoring_engine builds it as "Strong match (92.5/100). Note: Unclear - allow $15,000
  // in additional costs." — an internal score and an allowance no builder quoted. It was
  // prefilled into Investment strengths and shipped, and Coleen saw both on screen.
  const leaky = comparisonDocument([Object.assign({}, full, {
    recommendation_reason: 'Strong match (92.5/100). Note: Unclear - allow $15,000 in '
      + 'additional costs.',
    amenities_summary: 'Primary school 900 m; rail 4 km.' })], null, {});
  check('the internal score never reaches the document', !/92\.5|\/100/.test(leaky));
  check('nor the turnkey allowance', !/15,000|additional costs/.test(leaky));
  check('nor the phrase the engine writes for consultants', !/Strong match/.test(leaky));
  check('infrastructure still prefills from the stored amenity text',
    leaky.includes('Primary school 900 m; rail 4 km.'));
  // The AI draft IS client copy — api/narrative.py checks every one before returning it.
  const drafted = comparisonDocument([full], null,
    { drafts: [{ index: 0, text: 'Titled block, 350 m2, ready to build on now.' }] });
  check('an AI draft does prefill the box',
    drafted.includes('Titled block, 350 m2, ready to build on now.'));
  const dull = comparisonDocument([Object.assign({}, full, {
    amenities_summary: 'No amenity assessment captured for this listing.' })], null, {});
  check('boilerplate amenity text stays suppressed', !dull.includes('No amenity assessment'));

  // The client's name reaches the merged document from either entry point.
  check('a shared comparison still names the client',
    comparisonDocument([full], null, { client: 'Jane Buyer' }).includes('Jane Buyer'));
  check('a shared client report still names the client',
    clientReportDocument([full], null, 'Jane Buyer').includes('Jane Buyer'));

  // The banner uppercases the estate label, and must do it BEFORE escaping — otherwise
  // an ampersand in a vendor's estate name ships to a client as "&AMP;".
  check('no broken entity from uppercasing',
    !comparisonDocument([Object.assign({}, full, { estate_context: 'Smith & Co Estate' })],
      null, {}).includes('&AMP;'));
}

console.log('\n--- a price list\'s own heading is not an estate a client reads (7 Oct 2026) ---');
{
  // Repair round 2, callers verifier: the report and its /r link headlined "SPB PREFERRED
  // OPTION: OPTION 1 - STOCK HIGHLIGHTED IN ORANGE BELOW IS ELIGIBLE FOR THE 100%
  // COMMISSION OFFER., COLAC". An agent's commission is internal; so is a table's header.
  const heading = Object.assign({}, full, { suburb: 'Colac', score: 99,
    estate_context: 'Stock highlighted in orange below is eligible for the 100% commission offer.' });
  for (const [name, doc] of [['comparison', comparisonDocument([heading, sparse], null, {})],
                             ['client report', clientReportDocument([heading, sparse], null, '')]]) {
    check(name + ': no commission heading reaches the client', !/commission|highlighted/i.test(doc));
    check(name + ': the label falls back to the suburb', /PREFERRED OPTION: OPTION 1[^<]*COLAC/.test(doc));
  }
  const header = Object.assign({}, full, {
    estate_context: 'Lot Type Frontage(m) Land Size (m2) Land Rego Land Price Status' });
  check('a table header row is no estate either',
    !comparisonDocument([header], null, {}).includes('Frontage(m)'));
  const agent = Object.assign({}, full, { estate_context: 'Single Car Space & OB Storage Agent commission=6%plus' });
  check('an agent commission line is no estate',
    !/commission/i.test(clientReportDocument([agent], null, '')));
  const real = Object.assign({}, full, { estate_context: 'Estate : Alluvium $25k Land Discount Winter Valley' });
  check('a real estate name still reaches the client',
    comparisonDocument([real], null, {}).includes('ALLUVIUM'));
  // The card's title can carry it too (a flattened line): "Agent commission=6%plus, South
  // Yarra VIC" was a shortlisted title on 6 Oct.
  const titled = Object.assign({}, full, { suburb: 'South Yarra',
    lot_address: 'Single Car Space & OB Storage Agent commission=6%plus, 12 Toorak Rd' });
  for (const [name, doc] of [['comparison', comparisonDocument([titled], null, {})],
                             ['client report', clientReportDocument([titled], null, '')]]) {
    check(name + ': no agent commission in the title', !/commission|agent/i.test(doc));
    check(name + ': the rest of the address stays', doc.includes('12 Toorak Rd'));
  }
}

console.log(bad === 0 ? '\nALL CHECKS PASSED' : '\n' + bad + ' FAILED');
process.exit(bad ? 1 : 0);
