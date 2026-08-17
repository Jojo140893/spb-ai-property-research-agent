// Behaviour of the N-option comparison document. The generator is extracted from
// index.html by test_comparison_report.py and written beside this file as generator.mjs,
// so these checks always run against the code that actually ships rather than a copy.
import { comparisonDocument } from './generator.mjs';

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
check('no raw <img survives', !body.includes('<img'), 'IMG INJECTED');
check('the payload is present but inert text',
  body.includes('&lt;img src=x onerror=alert(2)&gt;'));
check('a quote cannot break out of an attribute', !/<td[^>]*"><img/.test(body));
check('an ampersand in a builder name survives as text', body.includes('A &amp; B Homes'));

console.log('\n--- the consultant writes the prose, the system does not ---');
check('every option gets an empty notes field',
  (h4.match(/<textarea/g) || []).length === 4);
check('no suburb/infrastructure commentary is pre-filled',
  !/growth area|infrastructure:|investment strengths:/i.test(body.replace(/placeholder="[^"]*"/g, '')));

console.log(bad === 0 ? '\nALL CHECKS PASSED' : '\n' + bad + ' FAILED');
process.exit(bad ? 1 : 0);
