"""The Research & Scoring tab's brief form and results (Part C, 16 Sep).

Colin searched "QLD under $750k" and got nothing useful. On the form itself: a blank Client
Name blocked Execute and the "search without this minimum" button; a budget that was not a
whole $10,000 (765000, or 750) could not be submitted; nothing said what the price boxes
were read as or what the search did to the brief; and builder and email text went into the
shortlist cards unescaped.

tests/research_tab_checks.mjs checks the form's markup and slices the tab's code out of
index.html between the RESEARCH-TAB-BEGIN / RESEARCH-TAB-END markers, running it under Node
with fetch and the DOM stubbed. Nothing touches the network.

A check that cannot fail reads as a pass, so the second half of this file breaks the page
on purpose -- one fix reverted at a time -- and requires the checks to notice.

    python -m pytest tests/test_research_tab.py -v
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
INDEX = APP / "index.html"
CHECKS = Path(__file__).resolve().parent / "research_tab_checks.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _run(index=None):
    env = dict(os.environ)
    env.pop("RT_INDEX", None)
    if index is not None:
        env["RT_INDEX"] = str(index)
    return subprocess.run(["node", str(CHECKS)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=120)


def test_the_tab_passes_its_checks():
    r = _run()
    assert r.returncode == 0, r.stdout[-6000:] + r.stderr[-2000:]


# name -> (text in index.html, what to replace it with, a FAIL line that must appear)
MUTANTS = {
    # --- the form: markup only, because Node has no browser validation to exercise
    "Client Name is required again": (
        '<input type="text" id="clientName" placeholder="Client name">',
        '<input type="text" id="clientName" placeholder="Client name" required>',
        "#clientName is not required"),
    "the maximum only takes whole $10,000s again": (
        'id="budgetMax" placeholder="no maximum" step="any"',
        'id="budgetMax" placeholder="no maximum" step="10000"',
        "#budgetMax accepts any dollar amount"),
    "the minimum only takes whole $10,000s again": (
        'id="budgetMin" placeholder="no minimum" step="any"',
        'id="budgetMin" placeholder="no minimum" step="10000"',
        "#budgetMin accepts any dollar amount"),
    "the preferred price only takes whole $10,000s again": (
        'id="budgetPref" placeholder="optional" step="any"',
        'id="budgetPref" placeholder="optional" step="10000"',
        "#budgetPref accepts any dollar amount"),
    # Correction 16: the parser reads bathrooms as a whole number, so 2.5 would search for 2.
    "bathrooms invite a half": (
        '<input type="number" id="bathrooms" placeholder="any">',
        '<input type="number" id="bathrooms" placeholder="any" step="0.5">',
        "#bathrooms stays whole numbers"),
    # --- the minimum re-run
    "the minimum re-run goes back through the form's submit": (
        "// someone asked for, so it asks for the explanation too.\n"
        "                            runResearchAgent({ explain: true, focusResults: true });",
        "// someone asked for, so it asks for the explanation too.\n                            "
        "const form = document.getElementById('propertyForm'); if (form) form.requestSubmit();",
        "clicking it posts the search again"),
    # --- escaping, one field at a time
    "lot_address is not escaped": (
        "Rank #${idx+1}: ${esc(prop.lot_address)}", "Rank #${idx+1}: ${prop.lot_address}",
        "lot_address: rendered as text"),
    "suburb is not escaped": (
        "${esc(prop.suburb)}, ${esc(prop.state)}", "${prop.suburb}, ${esc(prop.state)}",
        "suburb: rendered as text"),
    "state is not escaped": (
        "${esc(prop.suburb)}, ${esc(prop.state)}", "${esc(prop.suburb)}, ${prop.state}",
        "state: rendered as text"),
    "builder_name is not escaped": (
        "<strong>${esc(prop.builder_name || NOT_STATED)}</strong>",
        "<strong>${prop.builder_name || NOT_STATED}</strong>",
        "builder_name: rendered as text"),
    "house_design is not escaped": (
        " (${esc(prop.house_design)})", " (${prop.house_design})",
        "house_design: rendered as text"),
    "recommendation is not escaped": (
        '<span class="pill ${pillStyle}">${esc(prop.recommendation)}</span>',
        '<span class="pill ${pillStyle}">${prop.recommendation}</span>',
        "recommendation: rendered as text"),
    "turnkey_status is not escaped": (
        "${esc(prop.turnkey_status)}", "${prop.turnkey_status}",
        "turnkey_status: rendered as text"),
    "a benchmarked classification is not escaped": (
        "${esc(prop.benchmark.classification)}</span>", "${prop.benchmark.classification}</span>",
        "benchmark.classification (benchmarked): rendered as text"),
    "an unbenchmarked classification is not escaped": (
        "${esc(prop.benchmark ? prop.benchmark.classification : 'Unbenchmarked')}",
        "${prop.benchmark ? prop.benchmark.classification : 'Unbenchmarked'}",
        "benchmark.classification (not benchmarked): rendered as text"),
    "recommendation_reason is not escaped": (
        "${esc(prop.recommendation_reason)}", "${prop.recommendation_reason}",
        "recommendation_reason: rendered as text"),
    "the escaper leaves apostrophes alone": (
        "/[&<>\"']/g", "/[&<>\"]/g", "esc turns ' into &#39;"),
    # --- what was searched
    "the search notes are not escaped": (
        '<div class="search-note">${esc(n)}</div>', '<div class="search-note">${n}</div>',
        "brief_adjustments carrying <img> render as text"),
    "the Searched: line and notes are never rendered": (
        "const notesHtml = searchNotesHtml(data);", "const notesHtml = '';",
        "a Searched: line from brief_used"),
    "an exact locality is reported as a note": (
        "if (method === 'exact' || method === 'spoken') return '';", "if (false) return '';",
        "an exact locality is not a note"),
    "a swapped range prints raw numbers": (
        "const money = fields.some(f => MONEY_FIELD.has(f));", "const money = MONEY_FIELD.has(field);",
        "a swapped range reads as money on both ends"),
    "a widened region is not reported": (
        "...regionNoteTexts(d.region_expansion),", "",
        "a widened region is shown"),
    # --- the sheet badge
    "the sheet badge is never shown": (
        "prop.availability_by_the_sheet === true", "false",
        "availability_by_the_sheet true shows the badge"),
    "the sheet badge is shown on every card": (
        "prop.availability_by_the_sheet === true", "true",
        "availability_by_the_sheet false shows no badge"),
    # --- the budget echo
    "a budget under 10,000 is not read as thousands": (
        "return v >= 1 && v < THOUSANDS_BELOW", "return false", "750 reads as $750,000 (thousands)"),
    # --- added in review, 17 Sep: each of these passed the checks before the check was added
    "the thousands rule stops at 1,000": (
        "return v >= 1 && v < THOUSANDS_BELOW", "return v >= 1 && v < 1000",
        "9,999 is still thousands"),
    "the thousands rule reaches below 1, unlike the server's": (
        "return v >= 1 && v < THOUSANDS_BELOW", "return v < THOUSANDS_BELOW",
        "below 1 is not thousands"),
    "a search does not repaint the echo": (
        "keep the echo in step with what is about to be sent.\n        paintBudgetEcho();",
        "keep the echo in step with what is about to be sent.\n",
        "a search repaints the echo from boxes changed without typing"),
    "the Searched: line is not escaped": (
        "<strong>Searched:</strong> ${esc(line)}", "<strong>Searched:</strong> ${line}",
        "a brief_used carrying <img> renders as text"),
    "a long suburb list is printed whole": (
        "const subsShown = subs.slice(0, 12).join(', ')", "const subsShown = subs.join(', ')",
        "cut to 12 and counted"),
    "Client Name is required again, single-quoted": (
        '<input type="text" id="clientName" placeholder="Client name">',
        "<input type=\"text\" id=\"clientName\" placeholder=\"Client name\" required='required'>",
        "#clientName is not required"),
    "Client Name is required again, in capitals": (
        '<input type="text" id="clientName" placeholder="Client name">',
        '<input type="text" id="clientName" placeholder="Client name" REQUIRED>',
        "#clientName is not required"),
    "bathrooms invite a half, single-quoted": (
        '<input type="number" id="bathrooms" placeholder="any">',
        "<input type=\"number\" id=\"bathrooms\" placeholder=\"any\" step='0.5'>",
        "#bathrooms stays whole numbers"),
    "the maximum takes whole $10,000s, unquoted": (
        'id="budgetMax" placeholder="no maximum" step="any"',
        'id="budgetMax" placeholder="no maximum" step=10000',
        "#budgetMax accepts any dollar amount"),
    "the echo is not wired to the boxes": (
        "if (el && el.addEventListener) el.addEventListener('input', paintBudgetEcho);",
        "if (false) el.addEventListener('input', paintBudgetEcho);",
        "the echo is wired to the maximum box"),
    "a minimum alone does not say 'and above'": (
        "if (lo && !hi) parts.push(", "if (false) parts.push(",
        "a minimum alone says 'and above'"),

    # --- Part C W14: who asks for the explanation
    "the page-load search asks for explain": (
        "search found what it found.\n    runResearchAgent();",
        "search found what it found.\n    runResearchAgent({ explain: true });",
        "the page-load search does not ask for explain"),
    "a form submit does not ask for explain": (
        "e.preventDefault();\n        runResearchAgent({ explain: true });",
        "e.preventDefault();\n        runResearchAgent();",
        "a form submit asks for explain"),
    "every search asks for explain": (
        "body: JSON.stringify(explain ? { client_brief: brief, explain: true }\n"
        "                                                     : { client_brief: brief })",
        "body: JSON.stringify({ client_brief: brief, explain: true })",
        "a search without explain sends only client_brief"),
    "any truthy explain option counts": (
        "const explain = !!(options && options.explain === true);",
        "const explain = !!(options && options.explain);",
        "only explain === true counts"),
    "the minimum re-run does not ask for explain": (
        "// someone asked for, so it asks for the explanation too.\n"
        "                            runResearchAgent({ explain: true, focusResults: true });",
        "// someone asked for, so it asks for the explanation too.\n"
        "                            runResearchAgent({ focusResults: true });",
        "being a relaxation someone clicked, asks for the explanation"),
    # --- the Why only N matched panel
    "the panel is never rendered": (
        "const helpHtml = renderSearchHelp(data);", "const helpHtml = '';",
        "the panel is open and headed 'Why only 2 matched'"),
    "the panel stays open on a full shortlist": (
        "<details class=\"search-help\"${few ? ' open' : ''}>", "<details class=\"search-help\" open>",
        "folds it away as 'How this search was counted'"),
    "the headline lists gates with no in-range number": (
        "n: wholeCount(g.in_price_range) }))\n            .filter(g => g.n !== null && g.n > 0);",
        "n: wholeCount(g.in_price_range) }))\n            .filter(g => true);",
        "and not a gate with no in-range number"),
    "a change that adds nothing still gets a button": (
        "if (!(r.adds > 0)) { noChange.push(r.label); return; }",
        "if (false) { noChange.push(r.label); return; }",
        "a change that adds nothing is one grey 'No change:' line"),
    "a change not counted in time gets a button": (
        "if (!r.exact) { notCounted.push(r.label); return; }",
        "if (false) { notCounted.push(r.label); return; }",
        "a change not counted in time gets no button"),
    "a change the form cannot express gets a button": (
        "if (!r.fits) { byHand.push(text); return; }", "if (false) { byHand.push(text); return; }",
        "a patch naming a field the form does not have gets no button"),
    "a relaxation button's text is not escaped": (
        "data-relax=\"${i}\">${esc(text)}</button>", "data-relax=\"${i}\">${text}</button>",
        "everything in search_help renders as text"),
    "a funnel gate line is not escaped": (
        "parts.push(`<div class=\"sh-gate\">${esc(`${inThousands(g.n)} ${gateLabel(g.code, g.n)}`)}</div>`);",
        "parts.push(`<div class=\"sh-gate\">${g.n} ${gateLabel(g.code, g.n)}</div>`);",
        "everything in search_help renders as text"),
    "the panel repeats a note already shown above": (
        ".filter(s => s && !above.has(s))", ".filter(s => s)",
        "is not repeated in the panel"),
    # --- the relaxation click
    "a relaxation click goes through the form's submit": (
        "if (applyBriefPatch(r.patch)) runResearchAgent({ explain: true, focusResults: true });",
        "if (applyBriefPatch(r.patch)) { const form = document.getElementById('propertyForm'); "
        "if (form) form.requestSubmit(); }",
        "clicking it with a blank client name calls runResearchAgent once"),
    "a relaxation click does not write the change into the form": (
        "if (applyBriefPatch(r.patch)) runResearchAgent({ explain: true, focusResults: true });",
        "if (patchFits(r.patch)) runResearchAgent({ explain: true, focusResults: true });",
        "with budget_max 825000"),
    "the reveal button does not open the group": (
        "ncGroup.open = true;", "ncGroup.open = false;",
        "the reveal button opens the needs-confirming group"),
    "applyBriefPatch writes a 0 minimum as 0": (
        "|| !Number.isFinite(n) || n <= 0", "|| !Number.isFinite(n) || n < 0",
        "every box takes the patch"),
    "applyBriefPatch leaves the property type boxes alone": (
        ".forEach(c => writes.push([c, 'checked', wanted.has(c.value)]));", ".forEach(c => void c);",
        "the product type boxes and the rebate box follow the patch"),
    "applyBriefPatch writes a state the select cannot show": (
        "if (el.options && ![...el.options].some(o => o.value === text)) return null;",
        "if (false) return false;",
        "a state the select cannot show is refused"),
    "applyBriefPatch accepts a field the form does not have": (
        "&& Object.keys(patch).every(k => Object.prototype.hasOwnProperty.call(BRIEF_PATCH_BOX, k));",
        "&& true;",
        "a field the form does not have is refused"),
    "a blank preferred price is pinned to the new maximum": (
        "if (String(el.value || '').trim() === '' && Number(value) === Number(patch.budget_max)) continue;",
        "",
        "the preferred price, left blank, follows the new maximum"),
    # --- Needs confirming
    "the needs-confirming group opens by default": (
        "<details class=\"nc-group\"><summary class=\"nc-summary\">",
        "<details class=\"nc-group\" open><summary class=\"nc-summary\">",
        "with no open attribute"),
    "a needs-confirming card gets a compare tick (renderShortlistCard reused)": (
        "<div class=\"nc-title\">${esc(text(x.lot_address) || NOT_STATED)}</div>",
        "<div class=\"nc-title\"><input type=\"checkbox\" class=\"cmp-box\" "
        "data-hash=\"${esc(x.content_hash || '')}\"> ${esc(text(x.lot_address) || NOT_STATED)}</div>",
        "it has no .cmp-box"),
    # The plan's own mutation: the amber rows drawn with the shortlist's card renderer.
    "needs-confirming rows are drawn with renderShortlistCard": (
        "const ncHtml = renderNeedsConfirming(data);",
        "(data.needs_confirming || []).forEach((p, i) => renderShortlistCard({ recommendation: '', "
        "missing_inclusions: [], scoring_details: {}, ...p }, data.shortlist.length + i));\n"
        "                const ncHtml = renderNeedsConfirming(data);",
        "needs-confirming rows appear nowhere but the group"),
    "needs-confirming rows are put into LAST_SHORTLIST": (
        "LAST_SHORTLIST = data.shortlist || [];",
        "LAST_SHORTLIST = (data.shortlist || []).concat(data.needs_confirming || []);",
        "LAST_SHORTLIST holds only the shortlist"),
    "a needs-confirming address is not escaped": (
        "<div class=\"nc-title\">${esc(text(x.lot_address) || NOT_STATED)}</div>",
        "<div class=\"nc-title\">${text(x.lot_address) || NOT_STATED}</div>",
        "'<script>' in a needs-confirming address is escaped"),
    # Correction 27: an emailed list's date_checked is the inbox sweep's last read.
    "an emailed list reads as an ordinary check": (
        "if (emailed) {", "if (false) {",
        "an emailed list says 'email last read'"),
    "the card works out its own age instead of the row's age_days": (
        "const n = wholeCount(given);", "const n = null;",
        "a card carries its own date: '20/08/2026' and '27 days'"),
    "the badge does not count what needs confirming": (
        "+ (toConfirm ? ` · ${toConfirm}${confirmCountShort(data) ? '+' : ''} to confirm` : '')", "+ ''",
        "the badge reads '2 Qualified · 3 to confirm (1438 Filtered)'"),
    "a 'Show more' page lands below the needs-confirming group": (
        "cardsBox.appendChild(card);", "resultsDiv.appendChild(card);",
        "after 'Show more' every card is still above the needs-confirming group"),
    "a javascript: source link becomes a link": (
        r"if (!/^https?:\/\//i.test(String(link.url).trim())) return '';", "",
        "a source link that is not http(s) is never a link"),
    "an https source link is refused too": (
        r"if (!/^https?:\/\//i.test(String(link.url).trim())) return '';",
        r"if (!/^http:\/\//i.test(String(link.url).trim())) return '';",
        "while an https link still shows"),

    # --- W14 review of its own checks: one mutant per check that had none, so no check
    # --- here passes only because nothing could make it fail
    "explain is never sent": (
        "body: JSON.stringify(explain ? { client_brief: brief, explain: true }\n"
        "                                                     : { client_brief: brief })",
        "body: JSON.stringify({ client_brief: brief })",
        "a search asked to explain sends {client_brief, explain:true}"),
    "a relaxation click does not ask for explain": (
        "if (applyBriefPatch(r.patch)) runResearchAgent({ explain: true, focusResults: true });",
        "if (applyBriefPatch(r.patch)) runResearchAgent({ focusResults: true });",
        "... asking for the explanation"),
    "the headline drops the in-range total": (
        "const total = wholeCount(funnel.in_price_range_total);", "const total = null;",
        "the panel headline is the funnel's in-range count"),
    "the funnel does not say how many made the shortlist": (
        "made the shortlist", "were looked at",
        "then where they went"),
    "a button does not say what the change adds": (
        " (+${inThousands(r.adds)})", "",
        "the budget button carries its true count"),
    "the reveal offer is never made": (
        "if (amber > 0 && n > 0) {", "if (false) {",
        "the reveal offer shows how many need confirming"),
    "the panel is painted below the cards": (
        "resultsDiv.appendChild(helpBox);", "setTimeout(() => resultsDiv.appendChild(helpBox), 0);",
        "the panel comes before the first card"),
    "no results reads like some results": (
        "found === 0 ? 'Why nothing matched'", "found === -1 ? 'Why nothing matched'",
        "with no results it reads 'Why nothing matched'"),
    "a panel is drawn without search_help": (
        "const help = d.search_help;",
        "const help = d.search_help || { funnel: { in_price_range_total: 1 } };",
        "no search_help (the page-load run): no panel"),
    "adds is not worked out from the count": (
        ": (reveal ? count : (count !== null && base !== null ? count - base : null));", ": null;",
        "an id-keyed relaxation with no adds works out +10"),
    "a capped count reads as exact": (
        "${r.capped ? '+' : ''}", "",
        "a capped count reads 40+"),
    "the panel never shows its own notes": (
        "for (const n of notes) parts.push(", "for (const n of []) parts.push(",
        "the panel shows location notes and brief adjustments the notes above did not"),
    "applyBriefPatch writes suburbs the search cannot read back": (
        "writes.push([el, 'value', textList(value).join(', ')]);",
        "writes.push([el, 'value', textList(value).join('; ')]);",
        "the search that follows sends what the patch said"),
    "'Any state' is written as the words": (
        "text = /^any state$/i.test(want) ? '' : want.toUpperCase();",
        "text = want.toUpperCase();",
        "'Any state' clears the State select"),
    "an empty patch is accepted": (
        "&& Object.keys(patch).length > 0", "",
        "an empty or missing patch is refused"),
    "a patch does not repaint the price echo": (
        "        paintBudgetEcho();\n        return true;", "        return true;",
        "a patch repaints the price echo"),
    "the amber summary counts only what was sent": (
        "const total = Math.max(wholeCount(d.needs_confirming_count) ?? 0, list.length);",
        "const total = list.length;",
        "a capped list says how many of the total it shows"),
    "the amber group draws only its first card": (
        "${list.map(needsConfirmingCard).join('')}", "${list.slice(0, 1).map(needsConfirmingCard).join('')}",
        "it has one card per entry"),
    "needs-confirming rows reach COMPARISONS": (
        "COMPARISONS = data.comparisons || {};",
        "COMPARISONS = Object.assign({}, data.comparisons, { confirming: data.needs_confirming });",
        "nothing was selected for comparison"),
    "an amber card does not say to confirm before sending": (
        "<div class=\"nc-warn\">Confirm price and availability with the builder before sending</div>",
        "<div class=\"nc-warn\"></div>",
        "the stated status, and what to do before sending"),
    "an amber card does not say why it needs confirming": (
        "${why.length ? ", "${false ? ",
        "why it needs confirming"),
    "an amber card's price is not money": (
        "Number.isFinite(price) && price > 0 ? echoMoney(price)",
        "Number.isFinite(price) && price > 0 ? String(price)",
        "the address, place, builder, price and bed / bath / car"),
    "what the guard left out is not said": (
        "${hidden.length ? ", "${false ? ",
        "what the guard left out is said"),
    "the badge says '0 to confirm'": (
        "+ (toConfirm ? ` · ${toConfirm}${confirmCountShort(data) ? '+' : ''} to confirm` : '')", "+ ` · ${toConfirm} to confirm`",
        "no needs_confirming: no group, and the badge is unchanged"),
    "a non-email date does not say how long ago": (
        "${days !== null ? ` — ${daysAgo(days)}` : ''}", "",
        "a list that did not come by email reads 'Last checked <date> — N days ago'"),
    "an unstated amber row reads as stated available": (
        "const stated = typeof x.availability_as_stated === 'string' ? x.availability_as_stated.trim() : '';",
        "const stated = 'Available';",
        "an unstated one says the source doesn't say"),
    "an emailed row with no list date prints an empty one": (
        "if (first) {", "if (true) {",
        "an emailed row with no list date still says 'email last read'"),
    "without age_days no age is worked out": (
        "if (!m) return null;", "return null;",
        "without age_days the age is worked out from the row's own dates"),
    "a row with no date says nothing useful": (
        "if (!date) return 'Last checked: no date recorded';", "if (!date) return 'Last checked ';",
        "no date at all says so"),
    "the badge counts only the rows sent": (
        "return list ? Math.max(wholeCount(d.needs_confirming_count) ?? 0, list) : 0;", "return list;",
        "the badge counts the total"),
    "an amber card's builder is not escaped": (
        "${esc(text(x.builder_name) || NOT_STATED)}", "${text(x.builder_name) || NOT_STATED}",
        "every needs-confirming field renders as text"),

    # --- W14, completed: a patch the form's own validation would refuse (correction 15)
    "applyBriefPatch never asks whether the box would take the figure": (
        "if (!boxHolds(el, text)) return null;", "",
        "a radius past the box's max is refused"),
    "the box check ignores max": (
        "if (hi !== null && Number.isFinite(Number(hi)) && n > Number(hi)) return false;", "",
        "a radius past the box's max is refused"),
    "the box check ignores step": (
        "return Math.abs(q - Math.round(q)) < 1e-9;", "return true;",
        "a fractional minimum a whole-number box would refuse is refused"),
    "the box check reads step=\"any\" as whole numbers": (
        "if (step !== null && step.toLowerCase() === 'any') return true;", "",
        "a price in any whole or part dollar still fits the price boxes"),
    "a patch writes the property types before the rest is checked": (
        ".forEach(c => writes.push([c, 'checked', wanted.has(c.value)]));",
        ".forEach(c => { c.checked = wanted.has(c.value); });",
        "nothing else in the patch is written first"),
    # --- the funnel after the checks
    "the funnel never says what became of the listings that passed": (
        "if (passed !== null && passed > 0) {", "if (false) {",
        "and what became of the ones that passed every check"),
    "a pool or scorer line is read by its (empty) in-range count": (
        ".map(g => ({ code: g.code.trim(), n: wholeCount(g.count) }))",
        ".map(g => ({ code: g.code.trim(), n: wholeCount(g.in_price_range) }))",
        "and what became of the ones that passed every check"),
    "a gate that counted nothing is listed": (
        "n: wholeCount(g.count) }))\n            .filter(g => g.n !== null && g.n > 0);",
        "n: wholeCount(g.count) }))\n            .filter(g => g.n !== null);",
        "nor one that counted nothing"),
    # --- a re-run that failed, and hidden reasons
    "a failed re-run reads as not counted in time": (
        "if (r.failed) { failed.push(r.label); return; }", "",
        "a change whose re-run failed says so"),
    "an entry's first_seen is never read": (
        "const first = emailFirst || (emailed ? String(x.first_seen ?? '').trim() : '');",
        "const first = emailFirst;",
        "an emailed entry that calls it first_seen still shows the list's own age"),
    "any row with a first_seen reads as emailed": (
        "|| !!emailFirst;", "|| !!emailFirst || !!String(x.first_seen ?? '').trim();",
        "a first_seen on a row that did not come by email is not read as a list's age"),
    "hidden reasons with one meaning are not summed": (
        "because.set(why, (because.get(why) || 0) + n);", "because.set(why, n);",
        "reasons that mean the same are summed into one line"),
    "a hidden reason that counted nothing is said": (
        "if (n === null || n <= 0) continue;", "if (n === null) continue;",
        "reasons that mean the same are summed into one line"),

    # --- W14 review, 17 Sep: overlapping searches. An explained search is slower than the
    # --- page-load run, so an older answer arriving last painted over the newer one.
    "an older search still paints over a newer one": (
        "const overtaken = () => run !== RESEARCH_RUN;", "const overtaken = () => false;",
        "a search that answers after a newer one paints nothing"),
    "an overtaken search writes its retry message": (
        "if (attempt === 2) {\n                    if (overtaken()) return;\n",
        "if (attempt === 2) {\n",
        "an older search that fails does not overwrite the newer results with its error"),
    "an overtaken search retries anyway": (
        "await new Promise(r => setTimeout(r, 1200));\n                    if (overtaken()) return;\n",
        "await new Promise(r => setTimeout(r, 1200));\n",
        "an older search waiting to retry does not retry once a newer search has answered"),
    "an overtaken search's late 401 is shown": (
        "            if (overtaken()) return;       // a newer search owns the results now\n", "",
        "late 401 does not replace the newer results"),
    "an overtaken search's late body is painted": (
        "const data = await res.json();\n            if (overtaken()) return;\n",
        "const data = await res.json();\n",
        "whose body is read late paints nothing"),
    "an overtaken search's unreadable body is reported": (
        "} catch (e) {\n            if (overtaken()) return;\n", "} catch (e) {\n",
        "whose body will not parse does not replace the newer results"),
    # --- a relaxation's count is for the brief that was searched
    "a relaxation click runs after the brief was edited": (
        "if (briefKey(readBriefForm()) !== searchedKey) {", "if (false) {",
        "after the brief is edited, a relaxation click searches nothing"),
    "an edited brief is refused without a word": (
        "if (said) said.textContent = 'The brief above has changed since these '",
        "if (false) said.textContent = 'The brief above has changed since these '",
        "and says why, in the panel"),
    "the panel has no line for a refused click to speak on": (
        "            parts.push('<div class=\"sh-said sh-grey\" role=\"status\" aria-live=\"polite\"></div>');\n",
        "",
        "and says why, in the panel"),
    "a new client name refuses the counted change": (
        "const briefKey = brief => JSON.stringify({ ...brief, client_name: '' });",
        "const briefKey = brief => JSON.stringify(brief);",
        "the brief as searched (the client name aside) still runs the counted change"),
    "a patch the form cannot hold is still a button": (
        "fits: briefPatchWrites(patch) !== null,", "fits: patchFits(patch),",
        "a patch the form cannot hold is offered as text"),
    # --- counts as a person reads them (full 17 Sep snapshot, rendered)
    "two codes with one meaning are two lines": (
        "if (said.has(key)) said.get(key).n += g.n;\n            else said.set(key, { code: g.code, n: g.n });",
        "said.set(key + said.size, { code: g.code, n: g.n });",
        "two codes that both mean 'repeat another listing' are one line"),
    "counts in the thousands have no separators": (
        "const inThousands = n => Number(n).toLocaleString('en-AU');", "const inThousands = n => String(n);",
        "counts in the thousands are written with separators"),
    "no price limits reads 'priced at any price'": (
        ": `${where}, ${inThousands(total)} listing${total === 1 ? ' has' : 's have'} a price.`;",
        ": `${where}, ${inThousands(total)} listings are priced at any price.`;",
        "with no price limits the headline says so plainly"),
    "a count that may be short reads as exact in the group": (
        "${confirmCountShort(d) ? 'At least ' : ''}", "",
        "a count that may be short reads 'At least N' in the group"),
    "a count that may be short reads as exact on the badge": (
        "${confirmCountShort(data) ? '+' : ''}", "",
        "and 'N+ to confirm' on the badge"),
    # --- release review, 17 Sep: one mutant per new check
    "the Pending count reads as listings to confirm": (
        "pending_confirmation: [\"couldn't be verified, so it isn't recommended\",\n"
        "                               \"couldn't be verified, so they aren't recommended\"],",
        "pending_confirmation: ['needs confirming with the builder first',\n"
        "                               'need confirming with the builder first'],",
        "the panel never says '4 need confirming'"),
    "the panel does not say how many the group offers": (
        "        if (amber > 0) {\n            const n = Math.max(waiting ?? 0, amber);",
        "        if (false) {\n            const n = Math.max(waiting ?? 0, amber);",
        "and the 3 the group offers"),
    "an empty group says nothing of what was left out": (
        "            if (left.length) {", "            if (false) {",
        "still says what was left out"),
    "the reveal button ignores 'may be short' and says 'need' for one": (
        "${esc(`Show the ${inThousands(n)}${plus} that need${n === 1 && !plus ? 's' : ''} confirming`)}",
        "${esc(`Show the ${n} that need confirming`)}",
        "Show the 1+ that need confirming"),
    "the refused-click line is not a live region": (
        "<div class=\"sh-said sh-grey\" role=\"status\" aria-live=\"polite\"></div>",
        "<div class=\"sh-said sh-grey\"></div>",
        "is a polite live region"),
    "focus is lost after a relaxation's re-run": (
        "                if (refocus) {", "                if (false) {",
        "focus is on the new panel's summary"),
    "the reveal leaves focus on the button": (
        "if (ncSummary && typeof ncSummary.focus === 'function') {", "if (false) {",
        "the reveal moves focus to the group it opened"),
    "a state typed as a suburb is said twice above the results": (
        ".filter(x => !locationNoteRepeats(x, d.brief_adjustments)).map(locationNoteText),", ".map(locationNoteText),",
        "is said once, by the adjustment"),
    "the thin-search threshold ignores the server": (
        "const few = found < (wholeCount(help.explain_below) ?? EXPLAIN_BELOW);",
        "const few = found < EXPLAIN_BELOW;",
        "explain_below 10 from the server opens the panel"),
    "a count is read as short whatever the server says": (
        "return !!(nc && typeof nc === 'object' && nc.count_may_be_short === true);", "return true;",
        "an exact count stays exact"),
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_a_broken_page_fails_the_checks(tmp_path, name):
    old, new, expect = MUTANTS[name]
    src = INDEX.read_text(encoding="utf-8")
    assert src.count(old) == 1, (
        f"mutation anchor {old!r} is not in index.html exactly once; the code changed, so "
        f"update this mutant rather than deleting it")
    broken = tmp_path / "index.html"
    broken.write_text(src.replace(old, new), encoding="utf-8")
    r = _run(broken)
    assert r.returncode != 0, f"the checks PASSED against a page where {name}"
    fails = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("FAIL")]
    assert any(expect in ln for ln in fails), (
        f"the run failed, but not on the check this mutant targets ({expect!r}). "
        f"FAIL lines: {fails[:6]} stderr: {r.stderr[-800:]}")


def test_the_markers_are_required(tmp_path):
    """Without the markers the checks must refuse to run, not quietly check nothing."""
    src = INDEX.read_text(encoding="utf-8")
    broken = tmp_path / "index.html"
    broken.write_text(src.replace("    // RESEARCH-TAB-BEGIN", "    // renamed"), encoding="utf-8")
    r = _run(broken)
    assert r.returncode != 0
    assert "RESEARCH-TAB-BEGIN / RESEARCH-TAB-END markers not found" in r.stdout
