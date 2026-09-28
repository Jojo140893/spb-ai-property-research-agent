"""The Clare's Leads (Agent 1) tab -- "Hot Leads" until 15 Sep, when it widened to every hot
caller and every warm or nurture caller who gave a state (booking stayed hot-only).

Every string on that tab came from someone else: the name, the reason and the brief are a
phone caller's speech as transcribed, and the buildings are scraped listings. So the thing
that matters most is that none of it can become markup, or a link that runs code, on the
screen Coleen and Colin open with the phone in their hand.

tests/hot_leads_tab_checks.mjs slices the tab's code out of index.html between the
HOT-LEADS-BEGIN / HOT-LEADS-END markers and runs it under Node with fetch and the DOM
stubbed. Nothing touches the network.

A check that cannot fail reads as a pass, so the second half of this file breaks the page
on purpose, in the specific ways that matter, and requires the checks to notice.

    python -m pytest tests/test_hot_leads_tab.py -v
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
INDEX = APP / "index.html"
CHECKS = Path(__file__).resolve().parent / "hot_leads_tab_checks.mjs"
BEGIN = "    // HOT-LEADS-BEGIN"
END = "    // HOT-LEADS-END"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _run(index=None):
    env = dict(os.environ)
    env.pop("HL_INDEX", None)
    if index is not None:
        env["HL_INDEX"] = str(index)
    return subprocess.run(["node", str(CHECKS)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=120)


def test_the_tab_passes_its_checks():
    r = _run()
    assert r.returncode == 0, r.stdout[-6000:] + r.stderr[-2000:]


# name -> (text in the hot-leads block, what to replace it with, a FAIL line that must appear)
MUTANTS = {
    "any URL scheme may become a link": (
        "(parsed.protocol === 'http:' || parsed.protocol === 'https:')", "true", "refused:"),
    "the caller's name is not escaped": (
        '<span class="hl-name">${esc(name)}</span>', '<span class="hl-name">${name}</span>',
        "raw <script"),
    "a 401 is not treated as an expired sign-in": (
        "if (res.status === 401) {", "if (false) {", "sessionExpired()"),
    "external links open without rel=noopener": (
        'target="_blank" rel="noopener noreferrer"', 'target="_blank"', "rel=noopener"),
    "mailto is built from anything": (
        "? 'mailto:' + e : ''", "? 'mailto:' + e : 'mailto:' + e", "mailto:"),
    # The tab once led with the realistic total while Research & Scoring showed the
    # advertised price (the 9 Aug rule), so one building had two prices depending on the tab.
    "the realistic total is shown ahead of the advertised price": (
        "const price = advertised || hlMoney(r.realistic_total_price);",
        "const price = hlMoney(r.realistic_total_price) || advertised;",
        "advertised price is the price"),
    "test records are asked for without ?hl_test=1": (
        "(v.length === 1 && v[0] === '1')", "(true)", "include_test not sent"),
    # 15 Sep: warm and nurture callers are researched and stored, so they are listed.
    # 23 Sep: the same filter, now with the clause that keeps an ungraded WhatsApp lead.
    "warm and nurture callers are dropped from the list": (
        "&& (hlGrade(l) !== '' || hlChannel(l) === 'whatsapp'))",
        "&& hlGrade(l) === 'hot')",
        "warm and nurture kept"),
    # A grade straight from the record would put a closed caller on the tab and the
    # record's own text into the pill's class attribute.
    "any grade the record carries is trusted": (
        "return HL_GRADES.includes(lead.grade) ? lead.grade : '';",
        "return String(lead.grade);",
        "closed and junk dropped"),
    # Booking is hot-only, so a warm card with no call is not a missed booking.
    "a warm caller with no booking is told to follow up": (
        "if (grade === 'warm' || grade === 'nurture') {", "if (false) {",
        "only hot callers are booked"),
    "the filter does not hide the other grades": (
        "if (card) card.hidden = !on;", "if (card) card.hidden = false;",
        "filter Warm hides"),
    # 23 Sep: the cards are printed inside their channel's group, so these two anchors moved
    # from hlRenderLeads into hlGroupBlock and the entries list it is handed. What they
    # protect did not move: a card is hidden by the filter, not dropped, and it carries its
    # index into the FULL list.
    "Refresh forgets the filter": (
        "hlLeadCard(x.lead, x.i, !hlShows(x.lead, f))", "hlLeadCard(x.lead, x.i, false)",
        "kept after Refresh"),
    # Numbering the filtered list would send the wrong caller's brief to Re-run.
    "cards are numbered by their place in the filtered list": (
        "const entries = list.map((lead, i) => ({ lead, i }));",
        "const entries = list.filter(l => hlShows(l, f)).map((lead, i) => ({ lead, i }));",
        "re-run from a filtered list"),
    # --- the WhatsApp / Vapi split (23 Sep) --------------------------------------------
    # EVERY RECORD IN THE STORE PREDATES THE channel FIELD, so a default of anything but
    # 'vapi' moves the entire tab into the WhatsApp group on the day this ships.
    "a record with no channel is grouped as WhatsApp": (
        "return lead.channel === 'whatsapp' ? 'whatsapp' : 'vapi';",
        "return lead.channel === 'vapi' ? 'vapi' : 'whatsapp';",
        "grouped as Vapi"),
    "the two channels are not split, every card is printed in both groups": (
        "entries.filter(x => hlChannel(x.lead) === ch)", "entries",
        "printed once"),
    # A group that says nothing when it is empty is indistinguishable from one that failed
    # to render -- and "is the WhatsApp scan running at all?" is the question it answers.
    "an empty group is silent": (
        "${visible ? ' hidden' : ''}>${hlGroupEmptyText(channel, f)}</div>",
        "${visible ? ' hidden' : ' hidden'}>${hlGroupEmptyText(channel, f)}</div>",
        "empty group says so"),
    # The Kommo tag is written after question 2 of 8, so a fully answered WhatsApp lead can
    # carry no tag at all. Dropping it deletes it from the tab with nothing to say so.
    "an ungraded WhatsApp lead is dropped from the list": (
        "&& (hlGrade(l) !== '' || hlChannel(l) === 'whatsapp'))",
        "&& hlGrade(l) !== '')",
        "not dropped"),
    "a WhatsApp lead with no tag is called hot": (
        "if (lead.grade === undefined) return hlChannel(lead) === 'whatsapp' ? '' : 'hot';",
        "if (lead.grade === undefined) return 'hot';",
        "with no grade is not called hot"),
    # An unreadable free-text suburb still returns stock, for the whole state. The card has
    # to say the search ran on those words, or the buildings read as an answer to the place.
    "the suburb is not said to have been searched as written": (
        "${hlUnresolvedNote(L.research)}", "",
        "searched as written"),
    # Since 15 Sep warm and nurture callers share the hook's answer with hot ones, so a cut-off
    # answer can leave an older hot caller out; the tab then asks for hot callers on their own.
    "hot callers are not looked up when the answer is cut off": (
        "if (all.cut) {", "if (false) {", "every hot caller in the store is on the tab"),
    "a caller in both answers is listed twice": (
        "if (seen.has(id)) return;", "if (false) return;", "no caller listed twice"),
    "a failed hot lookup is not said": (
        "if (c.hotFailed) {", "if (false) {", "hot lookup failed: says a hot caller may be missing"),
    # Part C W14: explain:true adds search_help and needs_confirming, which belong to the
    # Research tab. A re-run from a lead card must not ask for them, or show them.
    "a re-run asks for explain": (
        "body: JSON.stringify({ client_brief: lead.brief })",
        "body: JSON.stringify({ client_brief: lead.brief, explain: true })",
        "re-run never asks for explain"),
    "the live block falls back to the needs-confirming listings": (
        "const shortlist = Array.isArray(d.shortlist) ? d.shortlist : [];",
        "const shortlist = (Array.isArray(d.shortlist) && d.shortlist.length) ? d.shortlist "
        ": (Array.isArray(d.needs_confirming) ? d.needs_confirming : []);",
        "needs_confirming only: none of its listings is shown"),

    # --- Coleen, 28 Sep 2026 (3): contact details only for whoever holds the password ---
    "the contact line is full whatever the view says": (
        "const contact = full\n            ? (email", "const contact = true\n            ? (email",
        "masked by default: no mailto and no tel link"),
    "a card drawn with no view is unlocked": (
        "contact: HL_ACCESS.contact === 'full' ? 'full' : 'masked',", "contact: 'full',",
        "masked by default"),
    "the loader trusts any contact value": (
        "contact: data.contact === 'full' ? 'full' : 'masked',",
        "contact: data.contact ? 'full' : 'masked',",
        "a contact value other than exactly 'full' is masked"),
    "one full read unlocks a half-masked list": (
        "answers.every(a => a.contact === 'full')", "answers.some(a => a.contact === 'full')",
        "half masked is masked"),
    "the masked line shows more than 3 digits": (
        "digits.length >= 6 ? digits.slice(-3) : ''", "digits.length >= 6 ? digits.slice(-6) : ''",
        "shows only its last 3 digits"),
    "phoneLast3 is printed as given": (
        "/^\\d{3}$/.test(given) ? given", "given ? given",
        "a phoneLast3 that is not 3 digits is not shown"),
    "a masked card links the call recording": (
        "full ? hlExtLink(L.recordingUrl, 'Call recording') : ''",
        "hlExtLink(L.recordingUrl, 'Call recording')",
        "masked: no recording link"),
    "Email this client carries the record's text": (
        'href="${esc(mail + HL_MAIL_QUERY)}"',
        "href=\"${esc(mail + '?subject=' + encodeURIComponent(name))}\"",
        "Email this client: subject and body are fixed words"),
    "a wrong password is read as an expired sign-in": (
        "if (res.status === 401) {\n            hlAccessSay(",
        "if (res.status === 401 || res.status === 403) {\n            hlAccessSay(",
        "403: the server's message is shown, and it is not an expired sign-in"),
    "the password is left in the box": (
        "if (box) box.value = '';\n        if (!password) {", "if (!password) {",
        "the password box is emptied"),
    "the unlock form is drawn with no password set": (
        "} else if (HL_ACCESS.unlock === 'password') {", "} else if (true) {",
        "no password set: a note, and no password box"),

    # --- (1)/(2) the Research tab's selection and report, per lead ---
    "a lead's tick writes the Research tab's selection": (
        "if (rec) mine.set(h, slim(rec));",
        "if (rec) { mine.set(h, slim(rec)); SELECTED.set(h, slim(rec)); }",
        "a lead's picks never reach SELECTED"),
    "a pick is stored whole": (
        "if (rec) mine.set(h, slim(rec));", "if (rec) mine.set(h, rec);",
        "a pick is slim()'d"),
    "one lead's picks show on every lead": (
        "const hlPicksFor = id => (id && hlPicksLoad().get(id)) || new Map();",
        "const hlPicksFor = id => [...hlPicksLoad().values()][0] || new Map();",
        "picks are per lead"),
    "a tick for a building not on the card is kept": (
        "else if (!mine.has(h)) return false;", "else mine.set(h, { content_hash: h });",
        "a tick for a building not on the card is refused"),
    "the tick listener reads the Research tab's boxes too": (
        "!box.classList.contains('hl-cmp-box')) return;",
        "!(box.classList.contains('hl-cmp-box') || box.classList.contains('cmp-box'))) return;",
        "a Research tab .cmp-box is not this tab's to read"),
    "the day-of-call picks carry no tick": (
        "list.map(r => hlRecCard(r, lead === null ? undefined : { lead }))",
        "list.map(r => hlRecCard(r))",
        "each saved building carries its own lead's tick"),
    "blocked storage loses a pick without a word": (
        "if (!saved) {\n            hlSay(", "if (false) {\n            hlSay(",
        "blocked storage"),
    "the report is built from the Research tab's form": (
        "comparisonDocument, hlReportBrief(lead));", "comparisonDocument);",
        "the report is for the lead"),
    "the report brief takes the no-ceiling budget": (
        "&& bm > 0 && bm < Number.MAX_SAFE_INTEGER) ? bm : 0,", "&& bm > 0) ? bm : 0,",
        "hlReportBrief: the no-ceiling budget is 0"),
    "a spoken email reaches the report": (
        "client: hlNoContact(hlText(c.name) || hlText(b.client_name), c),",
        "client: hlText(c.name) || hlText(b.client_name),",
        "a name that is an email or phone is taken out"),
    "the live list is the top 3 again": (
        "shortlist.slice(0, shown).map((p, k) => {", "shortlist.slice(0, 3).map((p, k) => {",
        "the whole live shortlist"),
    "the live list is not paged": (
        "(Number.isInteger(o.shown) && o.shown > 0) ? o.shown : HL_LIVE_PAGE);", "shortlist.length);",
        "a live shortlist is painted 25 at a time"),
    "a live card is drawn for the Research tab": (
        "shortlistCardHtml(p, k, { lead, picked: hlPicked(lead, hash) })", "shortlistCardHtml(p, k)",
        "every live card carries this lead's tick"),

    # --- (2)/(5) notes and links per lead ---
    "a note's text is not escaped": (
        '`<div class="hl-note-text">${esc(text)}</div>`', '`<div class="hl-note-text">${text}</div>`',
        "hostile notes render as text"),
    "who wrote a note is not escaped": (
        "(meta ? `<span>${esc(meta)}</span>` : '')", "(meta ? `<span>${meta}</span>` : '')",
        "hostile notes render as text"),
    "a stored link is not checked": (
        "(hlExtLink(url, label, url) || ", '(`<a href="${url}">${esc(label)}</a>` || ',
        "a javascript: link stored in the notes is not a link"),
    "a javascript: link is sent to be kept": (
        "const safe = hlSafeUrl(u);\n        if (!safe) return '';\n        try {",
        "const safe = String(u);\n        try {",
        "a javascript: link is refused before sending"),
    "a link carrying a password is sent to be kept": (
        "if (p.username || p.password) return '';", "",
        "a link carrying a user name or password is refused"),
    "a 409 is read as saved": (
        "if (res.status === 409) {",
        "if (res.status === 409) { HL_NOTE_BUSY.delete(id); return true; }\n            if (false) {",
        "409 keeps the note in the box"),
    "an empty note is sent": (
        "if (action === 'other' && !text) {", "if (false) {",
        "an empty note is not sent"),
    "a second save goes out while the first is out": (
        "if (HL_NOTE_BUSY.has(id)) {", "if (false) {",
        "a second save while the first is out sends nothing"),
    "Remove takes the store's order, not what was drawn": (
        "const x = pick(HL_NOTES.get(id))[k];", "const x = hlNotesOf(HL_NOTES.get(id)).entries[k];",
        "Remove sends the id of the note drawn in that place"),
    "Remove does not ask first": (
        "if (typeof confirm === 'function' && !confirm(", "if (false && !confirm(",
        "Remove asks first"),
    "notes are drawn oldest first": (
        ".sort((a, b) => (at(b.x) - at(a.x)) || (a.k - b.k))",
        ".sort((a, b) => (at(a.x) - at(b.x)) || (a.k - b.k))",
        "newest first"),
    "a removed note is drawn": (
        "&& !Array.isArray(x) && x.removed !== true);", "&& !Array.isArray(x));",
        "a removed note is not drawn"),
    "a save's answer is not drawn": (
        "HL_NOTES.set(id, hlNotesOf(data.notes));", "",
        "the list is redrawn from the answer"),
    "the notes in /api/hot_leads are ignored": (
        "if (!HL_NOTES.has(id)) HL_NOTES.set(id, hlNotesOf(raw));", "",
        "notes from /api/hot_leads are drawn"),
    "unreadable notes are not said": (
        "(v.notesError ? ", "(false ? ",
        "a lead whose notes could not be read says so"),
    "typing is lost to a reload": (
        "        hlCaptureDrafts();\n        el.innerHTML", "        el.innerHTML",
        "a typed note survives a reload"),
    "the consultant's name is not remembered": (
        "try { if (by) localStorage.setItem(HL_BY_STORE, by); }", "try { }",
        "the name is remembered"),

    # --- the 28 Sep review of the above ---
    # Three escapes that no check needed: removing any of them passed every check.
    "the typed link draft is not escaped": (
        'value="${esc(hlText(d.url))}"', 'value="${hlText(d.url)}"',
        "a hostile draft link survives a reload, escaped"),
    "a link with no URL prints its label unescaped": (
        "(hlExtLink(url, label, url) || `<span class=\"hl-nolink\">${esc(label)} (link unavailable)</span>`)",
        "(hlExtLink(url, label, url) || `<span class=\"hl-nolink\">${label} (link unavailable)</span>`)",
        "a link with no URL: its label is text"),
    "the remembered name is not escaped": (
        'value="${esc(hlByStored())}"', 'value="${hlByStored()}"',
        "a remembered name is escaped back into its box"),
    # Two dashboard tabs: writing the page's whole map back dropped the other tab's picks.
    "a pick save writes this page's whole map back": (
        "const stored = hlPicksStored() || [];",
        "const stored = [...hlPicksLoad()].map(([k, m]) => [k, [...m.values()]]);",
        "two tabs: a pick ticked in one tab is not dropped"),
    "a lead written again moves to the end of the store": (
        "pairs.splice(at < 0 ? pairs.length : at, 0,", "pairs.splice(pairs.length, 0,",
        "a lead written again keeps its place"),
    # A save that may have been made was called "Not saved", and was saved twice.
    "a save that may have been made is called not saved": (
        "if (hlMaybeMade(res.status, why)) {", "if (false) {",
        "may have saved, the hook's 502"),
    "a 502 is read as refused": (
        "(status >= 500 && status !== 503)", "(status === 500)",
        "may have saved, a 502 with no message"),
    "a message saying the note was saved is ignored": (
        "if (/\\bwas saved, but\\b/i.test(m)) return true;", "",
        "a message that says the note was saved"),
    "a message saying nothing was changed is ignored": (
        "if (/\\bnothing was changed\\b/i.test(m)) return false;", "",
        "a 502 that says nothing was changed is 'Not saved'"),
    "a dropped connection is said to have changed nothing": (
        "hlNoteSay(hlIndexOf(id), unsure('the dashboard could not be reached'), true);",
        "hlNoteSay(hlIndexOf(id), 'Could not reach the dashboard, so nothing was changed.' + kept, true);",
        "unreachable: could not confirm"),
    # (c) one requestId per press, the same one on the automatic retry.
    "no requestId is sent": (
        "body.requestId = hlRequestId();", "",
        "carries a requestId"),
    "every press sends the same requestId": (
        "return Array.from(bytes, x => x.toString(16).padStart(2, '0')).join('');", "return 'r1';",
        "a fresh requestId for every press of Save"),
    "a dropped connection is not retried": (
        "for (let tries = body.requestId ? 2 : 1;", "for (let tries = 1;",
        "unreachable: sent once more, automatically"),
    "the retry carries a new requestId": (
        "body: sent\n", "body: JSON.stringify({ ...body, requestId: hlRequestId() })\n",
        "with the same requestId"),
    # notes[callId] === null (the hook's word for unreadable before 28 Sep) is not "no notes".
    "a null notes value is drawn as no notes": (
        "if (!raw || typeof raw !== 'object' || Array.isArray(raw)) { HL_NOTES_ERR.add(id); return; }", "",
        "a null notes value is said to be unreadable"),
    "unreadable notes also say there are none": (
        "const unread = !!v.notesError && !v.notes;", "const unread = false;",
        "none says it has no notes"),
    # The server's half of the same finding, had it been the page's: a masked answer with
    # notesErrors taken as a failed answer loses every lead.
    "a masked answer naming unreadable notes is treated as a failure": (
        "if (!res.ok || !data || data.status !== 'success') {\n            return { kind: 'failed'",
        "if (!res.ok || !data || data.status !== 'success' || (data.contact !== 'full'"
        " && Array.isArray(data.notesErrors) && data.notesErrors.length)) {\n            return { kind: 'failed'",
        "masked, with notes that could not be read: every lead is listed"),
}


# THE SAME, FOR WHAT THE TAB NOW SHARES WITH THE REST OF THE PAGE (Coleen, 28 Sep 2026): the
# Research tab's card (shortlistCardHtml, in the SHORTLIST-CARD block) and openGeneratedReport
# are outside the HOT-LEADS markers but are exercised by the same checks. Each anchor must be
# in index.html exactly once, so a mutant cannot land somewhere the checks do not look.
PAGE_MUTANTS = {
    "a lead's card draws the Research tab's .cmp-box": (
        "${leadMode ? leadPickBox(o.lead, prop.content_hash, o.picked === true) : `",
        "${false ? '' : `",
        "a live card's tick is the lead's own"),
    "a lead's card keeps the benchmark button": (
        "${leadMode ? '' : `<div style=\"margin-top:0.9rem;", "${false ? '' : `<div style=\"margin-top:0.9rem;",
        "no benchmark button on a lead's card"),
    "the card throws on a row with no scoring breakdown": (
        "${sd ? `<div class=\"score-section\">", "${true ? `<div class=\"score-section\">",
        "the card renders"),
    "the card claims Full Turnkey Verified when it does not know": (
        "${Array.isArray(prop.missing_inclusions) && prop.missing_inclusions.length === 0 ?",
        "${!(prop.missing_inclusions || []).length ?",
        "no claim of Full Turnkey Verified"),
    "a missing price prints $undefined": (
        "${price === null || price === undefined ? NOT_STATED : '$' + esc(price.toLocaleString())}",
        "$${String(price)}",
        "a missing advertised price says not stated"),
    "a javascript: cheaper comparable becomes a link": (
        "const cheapest = /^https?:\\/\\//i.test(String(c.cheapest_url || '').trim()) ? c.cheapest_url : '';",
        "const cheapest = c.cheapest_url;",
        "a javascript: cheaper comparable is not a link"),
    "bed / bath / car are not escaped on the card": (
        "${esc(spec(prop.bedrooms, 'Bed'))}", "${spec(prop.bedrooms, 'Bed')}",
        "hostile live shortlist: markup"),
    "the report ignores the lead's brief": (
        "const given = (briefIn && typeof briefIn === 'object' && !Array.isArray(briefIn)) ? briefIn : null;",
        "const given = null;",
        "the report is for the lead"),
    "openGeneratedReport prints the no-ceiling budget": (
        "&& givenBudget < Number.MAX_SAFE_INTEGER) ? givenBudget : 0,", ") ? givenBudget : 0,",
        "a no-ceiling budget handed to openGeneratedReport is never printed"),
    "the Research tab's report stops reading its form": (
        "const client = given ? String(given.client ?? '').trim() : _v('clientName');",
        "const client = String((given || {}).client ?? '').trim();",
        "the Research tab's call (no 4th argument) still reads its form"),
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_a_broken_page_fails_the_checks(tmp_path, name):
    old, new, expect = MUTANTS[name]
    src = INDEX.read_text(encoding="utf-8")
    begin, end = src.index(BEGIN), src.index(END)
    block = src[begin:end]
    assert old in block, (
        f"mutation anchor {old!r} is no longer in the hot-leads block; the code changed, so "
        f"update this mutant rather than deleting it")
    broken = tmp_path / "index.html"
    broken.write_text(src[:begin] + block.replace(old, new) + src[end:], encoding="utf-8")
    r = _run(broken)
    assert r.returncode != 0, f"the checks PASSED against a page where {name}"
    fails = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("FAIL")]
    assert any(expect in ln for ln in fails), (
        f"the run failed, but not on the check this mutant targets ({expect!r}). "
        f"FAIL lines: {fails[:6]} stderr: {r.stderr[-800:]}")


@pytest.mark.parametrize("name", sorted(PAGE_MUTANTS))
def test_a_broken_shared_part_fails_the_checks(tmp_path, name):
    old, new, expect = PAGE_MUTANTS[name]
    src = INDEX.read_text(encoding="utf-8")
    begin, end = src.index(BEGIN), src.index(END)
    assert src.count(old) == 1, (
        f"mutation anchor {old!r} is not in index.html exactly once; the code changed, so "
        f"update this mutant rather than deleting it")
    assert not (begin <= src.index(old) < end), f"{old!r} is inside the hot-leads block: put it in MUTANTS"
    broken = tmp_path / "index.html"
    broken.write_text(src.replace(old, new), encoding="utf-8")
    r = _run(broken)
    assert r.returncode != 0, f"the checks PASSED against a page where {name}"
    fails = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("FAIL")]
    assert any(expect in ln for ln in fails), (
        f"the run failed, but not on the check this mutant targets ({expect!r}). "
        f"FAIL lines: {fails[:6]} stderr: {r.stderr[-800:]}")


def test_the_shared_parts_are_required(tmp_path):
    """Without the card block the checks must refuse to run, not quietly check less."""
    src = INDEX.read_text(encoding="utf-8")
    broken = tmp_path / "index.html"
    broken.write_text(src.replace("    // SHORTLIST-CARD-BEGIN", "    // renamed"), encoding="utf-8")
    r = _run(broken)
    assert r.returncode != 0
    assert "the SHORTLIST-CARD block was not found" in r.stdout
