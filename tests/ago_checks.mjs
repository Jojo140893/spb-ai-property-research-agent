// Exercises agoText / agoShort, sliced out of index.html by test_channel_age.py.
// The wording on the Settings tab is the only place a consultant learns a source went
// quiet, so "1 day ago" on a source that delivered two hours earlier is a real defect,
// not a cosmetic one.
let failures = 0;
const eq = (got, want, what) => {
  if (got !== want) { failures++; console.error("FAIL " + what + ": got " + JSON.stringify(got) + ", want " + JSON.stringify(want)); }
};

// --- hours, whenever we have a real timestamp -------------------------------
eq(agoText({hours_ago: 0,  days_ago: 0}), 'less than an hour ago', 'zero hours');
eq(agoText({hours_ago: 1,  days_ago: 0}), '1 hour ago',            'singular hour');
eq(agoText({hours_ago: 2,  days_ago: 0}), '2 hours ago',           'the 18 Aug case');
eq(agoText({hours_ago: 25, days_ago: 1}), '25 hours ago',          'past a day, still hours');
eq(agoText({hours_ago: 47, days_ago: 1}), '47 hours ago',          'last hour before the switch');

// --- days once hours stop being a fact anybody acts on ----------------------
eq(agoText({hours_ago: 48,  days_ago: 2}), '2 days ago',  'switches at 48h');
eq(agoText({hours_ago: 173, days_ago: 7}), '7 days ago',  'a dead channel reads in days');

// --- the fallback: rows harvested before checked_at existed -----------------
eq(agoText({hours_ago: null, days_ago: 3}), '3 days ago', 'no timestamp -> days');
eq(agoText({hours_ago: null, days_ago: 1}), '1 day ago',  'singular day');
eq(agoText({hours_ago: undefined, days_ago: 0}), '0 days ago', 'missing field -> days');
eq(agoText({hours_ago: null, days_ago: null}), '', 'nothing known -> say nothing');

// A fabricated hour figure is worse than an honest day figure: with no timestamp the
// hours branch must never be entered.
eq(agoText({days_ago: 5}), '5 days ago', 'absent hours_ago is not treated as 0');

// --- the compressed form used in the warning banner -------------------------
eq(agoShort({hours_ago: 3,   days_ago: 0}), '3h', 'banner hours');
eq(agoShort({hours_ago: 47,  days_ago: 1}), '47h', 'banner still hours at 47');
eq(agoShort({hours_ago: 168, days_ago: 7}), '7d', 'banner days');
eq(agoShort({hours_ago: null, days_ago: 9}), '9d', 'banner fallback');
eq(agoShort({hours_ago: null, days_ago: null}), '?', 'banner knows nothing');

if (failures) { console.error(failures + " check(s) failed"); process.exit(1); }
console.log('ago checks passed');
