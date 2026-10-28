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
        "+ hlUnresolvedNote(L.research)}", "}",
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
        "body: JSON.stringify({ client_brief: lead.brief, offset: 0, limit: HL_LIVE_LIMIT })",
        "body: JSON.stringify({ client_brief: lead.brief, offset: 0, limit: HL_LIVE_LIMIT, explain: true })",
        "re-run never asks for explain"),
    # 7 Oct 2026: a re-run asks for a page of 500 full cards; unpaged, a lead's state-wide
    # brief would bring cards past the 600th in their compact form.
    "a re-run sends no page": (
        "body: JSON.stringify({ client_brief: lead.brief, offset: 0, limit: HL_LIVE_LIMIT })",
        "body: JSON.stringify({ client_brief: lead.brief })",
        "body is exactly {client_brief: lead.brief, offset: 0, limit: 500}"),
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
        "err: v.notesError ? ", "err: false ? ",
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
        "if (hlMaybeMade(res.status, why)) return open(why);", "if (false) return open(why);",
        "may have saved, the hook's 502"),
    "a 502 is read as refused": (
        "(status >= 500 && status !== 503)", "(status === 500)",
        "may have saved, a 502 with no message"),
    # Integration, 28 Sep 2026: the second review's "was saved, but" shortcut is gone, and these
    # guard what replaced it and the verifiers' last findings.
    "a Refresh leaves a lead with a press out wholly unsettled": (
        "if (!id || !HL_NOTES.has(id)) return;\n",
        "if (!id || HL_NOTE_BUSY.has(id) || !HL_NOTES.has(id)) return;\n",
        "a Refresh while another press is out still settles the presses it shows"),
    "a Refresh settles the press still out": (
        "HL_NOTE_OUT.get(id) || '', -1)", "'', -1)",
        "a Refresh while the save is out: that press is not called 'could not be confirmed'"),
    "the character before a joiner is read as half a surrogate pair": (
        "const pair = k >= 2 && low >= 0xDC00", "const pair = false && low >= 0xDC00",
        "a ZWNJ after a 4-byte digit"),
    "a hidden lead's late save sends the consultant to Refresh": (
        "stale && !shown && !hidden)", "stale && !shown)",
        "a hidden lead's save answered after a Refresh"),
    "several arrivals are said as one": (
        "if (kinds.some(k => settled.filter(x => x.what === k).length > 1)) {", "if (false) {",
        "several earlier notes arrived: said as a count"),
    "a message saying nothing was changed is ignored": (
        "if (/\\bnothing was changed\\b/i.test(m)) return false;", "",
        "a 502 that says nothing was changed is 'Not saved'"),
    "a dropped connection is said to have changed nothing": (
        "if (!res) return open('the dashboard could not be reached');",
        "if (!res) { hlNoteSay(hlIndexOf(id), 'Could not reach the dashboard, so nothing was changed.' + kept, true);"
        " return ''; }",
        "unreachable: could not confirm"),
    # (c) one requestId per press, the same one on the automatic retry.
    "no requestId is sent": (
        "body.requestId = hlDraftRequestId(d, 'noteReqs', same);", "",
        "carries a requestId"),
    # 28 Sep review: "press Save again" after "could not confirm" must be the same request.
    "Save again after an unconfirmed save is a new request": (
        "const rid = kept.get(sig) || hlRequestId();", "const rid = hlRequestId();",
        "Save again after 'could not confirm': the same requestId"),
    "an edited note keeps the unconfirmed press's requestId": (
        "const sig = JSON.stringify(same);\n        const kept = d[key];\n        const rid",
        "const sig = '';\n        const kept = d[key];\n        const rid",
        "an edited note after 'could not confirm' is a new request"),
    "a confirmed note's requestId is kept for the next one": (
        "hlDraftSettled(d, 'noteReqs', same, !!outcome);", "hlDraftSettled(d, 'noteReqs', same, false);",
        "the same quick action saved twice is two requests"),
    # One lead the server could not mask is cut down alone (maskFailed, notesHidden).
    "a cut-down lead does not say why": (
        "${cut ? '<div class=\"hl-notice hl-mask-failed\">", "${false ? '<div class=\"hl-notice hl-mask-failed\">",
        "the cut-down one says why most of it is missing"),
    "notes left out are drawn as no notes": (
        "const hlNotesUnread = v => (!!v.notesError || !!v.notesHidden) && !v.notes;",
        "const hlNotesUnread = v => !!v.notesError && !v.notes;",
        "its notes are said to need the password"),
    "notesHidden is not read from the answer": (
        "a.notesHidden.forEach(id => HL_NOTES_HIDDEN.add(id));", "",
        "its notes are said to need the password"),
    "the unconfirmed wording sends the consultant to Refresh": (
        "Press ${kind === 'link' ? 'Add link' : 'Save'} again:`", "Press Refresh to check before saving it again:`",
        "unreachable: could not confirm, press Save again"),
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
        "const hlNotesUnread = v => (!!v.notesError || !!v.notesHidden) && !v.notes;",
        "const hlNotesUnread = v => false;",
        "none says it has no notes"),
    # The server's half of the same finding, had it been the page's: a masked answer with
    # notesErrors taken as a failed answer loses every lead.
    "a masked answer naming unreadable notes is treated as a failure": (
        "if (!res.ok || !data || data.status !== 'success') {\n            return { kind: 'failed'",
        "if (!res.ok || !data || data.status !== 'success' || (data.contact !== 'full'"
        " && Array.isArray(data.notesErrors) && data.notesErrors.length)) {\n            return { kind: 'failed'",
        "masked, with notes that could not be read: every lead is listed"),

    # --- the second 28 Sep review: settling a press, and the masked view's edges ---------
    # A kept requestId was dropped only by a confirmed save, so a Refresh that showed the note
    # changed nothing, and the next identical note was a "duplicate" the hook skipped.
    "a press the list shows arrived is kept for later": (
        "const arrived = hlSettleDrafts();", "const arrived = new Map();",
        "a Refresh that shows the unconfirmed press arrived"),
    "a press the hook already had is called Saved.": (
        "(data.duplicate === true && again ? 'Already saved.' : 'Saved.')", "'Saved.'",
        "a press the hook already had: 'Already saved.'"),
    "a press matched to a removed note is called saved": (
        "if (kind !== 'remove' && data.duplicateRemoved === true) {", "if (false) {",
        "a press matched to a note since removed"),
    "a press matched to a removed note sends the same requestId again": (
        "hlDraftSettled(d, 'noteReqs', same, !!outcome);",
        "hlDraftSettled(d, 'noteReqs', same, outcome === 'made');",
        "the next press adds it anew"),
    # One slot per draft: X, then Y, then X again stored X twice.
    "one unconfirmed press is kept per draft": (
        "const HL_REQ_KEEP = 10;", "const HL_REQ_KEEP = 1;",
        "X, then Y, then X again"),
    # C3: a masked answer whose notes cannot be masked is a save, with the notes left hidden.
    "a notesHidden answer is read as could not confirm": (
        "&& (hidden || (data.notes && typeof data.notes === 'object'))) {",
        "&& (data.notes && typeof data.notes === 'object')) {",
        "a masked save whose notes cannot be shown (notesHidden)"),
    "a notesHidden answer leaves the lead's notes showing": (
        "                        HL_NOTES.delete(id);\n                        HL_NOTES_HIDDEN.add(id);\n", "",
        "a save that hides a lead's notes"),
    # A cut-down (maskFailed) lead drew "No discovery call booked -- follow up" over a booked call.
    "a cut-down card draws what it does not have": (
        "const cut = !full && L.maskFailed === true;", "const cut = false;",
        "a cut-down card draws no booking line"),
    "a cut-down card still draws its buildings, Re-run and report bar": (
        "${cut ? '' : `<div class=\"hl-recs-head\">", "${false ? '' : `<div class=\"hl-recs-head\">",
        "a cut-down card draws no booking line"),
    # The notices had no id, so a save that changed what was known left them standing.
    "a save leaves the notices over a lead's notes as they were": (
        "[['hlNotesErr-', notice.err], ['hlNotesHidden-', notice.hidden]].forEach(", "[].forEach(",
        "the notice saying they are hidden is gone"),
    "the notices name a password when none is set": (
        "const hlCanUnlock = () => HL_ACCESS.unlock === 'password';", "const hlCanUnlock = () => true;",
        "no password set: no notice sends anyone for a password"),
    # Words typed while a save was out were wiped by its success.
    "a save empties the box whatever was typed since": (
        "if (String(typed ?? '').trim() === text) {", "if (true) {",
        "words typed while a save is out stay in the box"),
    "a save lets go of a chip pressed since": (
        "if ((HL_NOTE_ACTIONS.includes(d.action) ? d.action : 'other') === action) {", "if (true) {",
        "a chip pressed while it saved stays pressed"),
    "an Add link empties both boxes whatever was typed since": (
        "if (read((b && typeof b.value === 'string') ? b.value : d[field]) !== sentAs) return;", "",
        "a link box changed while the link saved keeps the new link"),
    # The tab sent URL.href, so the hook's own link rules never ran on a link from it.
    "invisible characters are sent in a link": (
        "return String(v ?? '').replace(HL_LINK_DROP, '')", "return String(v ?? '')",
        "a trailing zero-width space"),
    "two links pasted together are sent as one": (
        "if (/[\\s\\u0000-\\u001F\\u007F]/.test(raw)) {", "if (false) {",
        "two links pasted together are refused before sending"),
    "the parsed href is sent, not the link as pasted": (
        "const body = { callId: id, op: 'add_link', url: raw };", "const body = { callId: id, op: 'add_link', url };",
        "a link is sent as pasted"),
    # Save, then Lock before the answer: the full notes were drawn onto the locked tab.
    "a late answer is drawn onto a list drawn after it was sent": (
        "const stale = HL_DRAWN !== drawnAt ||", "const stale = false ||",
        "from a dashboard that does not say which view"),
    "the list's redraws are not counted": (
        "            HL_DRAWN++;\n", "",
        "from a dashboard that does not say which view"),
    "a full answer is drawn onto a masked list": (
        " || (data.contact === 'full' && HL_ACCESS.contact !== 'full');", ";",
        "a full answer to a masked list, with no redraw between: not drawn"),

    # --- 28 Sep repair round: what two verifiers found in the second review's fixes --------
    # A Refresh while a slow Save was out settled that very press: the box emptied, then its lost
    # answer said "What you typed is still here", and Save pressed again stored it twice.
    "a lost answer over a list that shows it is 'could not confirm'": (
        "if ((body.op !== 'remove' || HL_DRAWN !== drawnAt) && hlListShows(id, body)) {", "if (false) {",
        "its answer then lost: the list drawn since shows it"),
    "an answer the list drawn since already shows says to press Refresh": (
        "const shown = stale && hlListShows(id, body);", "const shown = false;",
        "then its answer: 'Saved.' -- the list drawn since shows it"),
    # Settled on a Refresh alone, a press a save's own answer listed stayed kept, and a deliberate
    # second follow-up went out with its requestId and was dropped as a repeat.
    "a save's answer does not settle the other presses it shows": (
        "const arrived = hidden ? [] : hlSettleDraft(id, hlNotesOf(data.notes), body.requestId || '', j);",
        "const arrived = [];",
        "a deliberate second follow-up later is a new note"),
    "a press settled that is not the draft's words is not said": (
        "settled.push({ what, current: kept === sig });", "if (kept === sig) settled.push({ what, current: true });",
        "a save whose answer lists an earlier unconfirmed press"),
    "a settled press's words are left in the box": (
        "if (kept === sig) hlDraftClear(d, j, what);", "",
        "a link's answer that shows the note still in its box arrived"),
    # One press whose retry the hook answered duplicate read "Already saved.".
    "a single press answered duplicate is called Already saved.": (
        "(data.duplicate === true && again ? 'Already saved.' : 'Saved.')",
        "(data.duplicate === true ? 'Already saved.' : 'Saved.')",
        "one press whose first fetch dropped after the hook wrote"),
    # The cap forgot the oldest as a press was added, before its own id was settled.
    "the cap forgets an unconfirmed press before the answer": (
        "        kept.set(sig, rid);\n        return rid;",
        "        kept.set(sig, rid);\n        while (kept.size > HL_REQ_KEEP) kept.delete(kept.keys().next().value);\n"
        "        return rid;",
        "eleven presses unconfirmed, then the oldest confirmed"),
    # The link box dropped every joiner and selector, where the hook keeps them after a letter.
    "the link box drops a joiner the hook keeps": (
        "if (before !== '' && !HL_LINK_PLAIN.test(before)) return run;", "",
        "link box, as the hook: a ZWNJ inside a Persian word"),
    "the link box keeps a joiner the hook drops": (
        "return whole.codePointAt(at + run.length) === 0x20E3 && /[\\u{FE00}-\\u{FE0F}]/u.test(last) ? last : '';",
        "return run;",
        "link box, as the hook: a ZWJ after a Latin letter"),
    # A Refresh sent while Lock was out was drawn in full after Lock.
    "an older load draws over the newer one": (
        "            const all = await hlAsk(hlLeadsUrl());\n            if (gen !== HL_LOAD_GEN) return;\n",
        "            const all = await hlAsk(hlLeadsUrl());\n",
        "a Refresh sent before Lock and answered after it"),
    "a forced load while one is out is skipped": (
        "if (HOT_LEADS_STATE === 'loading' && !force) return;", "if (HOT_LEADS_STATE === 'loading') return;",
        "a Refresh sent before Lock and answered after it"),

    # --- Coleen, 26 Oct 2026: Both / Clare calls (VAPI) / WhatsApp, above the grades ----------
    # Every record stored before 23 Sep is a Clare call with NO channel key, so reading the key
    # itself puts most of Clare's calls under neither button.
    "a Clare call with no channel key is not under Clare calls (VAPI)": (
        "const hlFromSource = (lead, s) => hlSourceHas(hlSourceKey(s), hlChannel(lead));",
        "const hlFromSource = (lead, s) => hlSourceHas(hlSourceKey(s), lead && lead.channel);",
        "a record with no channel is a Clare call"),
    "any source string is trusted": (
        "const hlSourceKey = s => HL_SOURCES.includes(s) ? s : 'all';", "const hlSourceKey = s => String(s);",
        "-> Both (hlSourceKey)"),
    "the label is not Clare calls (VAPI)": (
        "vapi: 'Clare calls (VAPI)' }", "vapi: 'Vapi' }",
        "the source buttons are labelled exactly"),
    "a source button does not hide the other group": (
        "if (group) group.hidden = !hlSourceHas(s, ch);", "if (group) group.hidden = false;",
        "Clare calls (VAPI) hides the WhatsApp group"),
    "a list drawn under a source draws the other group": (
        "${hlSourceHas(hlSourceKey(s), channel) ? '' : ' hidden'}>`", "${''}>`",
        "rendered under Clare calls (VAPI), the WhatsApp group is drawn hidden"),
    "the pressed source button is not marked": (
        "b.className = 'btn-ghost hl-filter' + (key === s ? ' is-active' : '');", "b.className = 'btn-ghost hl-filter';",
        "Clare calls (VAPI) presses that button, and only that one"),
    "Refresh forgets the source": (
        "el.innerHTML = hlRenderLeads(HOT_LEADS, HOT_LEADS_FILTER, cover, HOT_LEADS_SOURCE);",
        "el.innerHTML = hlRenderLeads(HOT_LEADS, HOT_LEADS_FILTER, cover);",
        "the source is kept after Refresh"),
    "the grade counts ignore a source pressed": (
        "b.textContent = hlFilterLabel(inSource, key);", "b.textContent = hlFilterLabel(HOT_LEADS, key);",
        "grade counts are within the source"),
    "the grade counts drawn ignore the source": (
        "hlFilterBar(list.filter(l => hlFromSource(l, s)), f)", "hlFilterBar(list, f)",
        "rendered under a source, the grade counts are that source's"),
    # A hot Clare call kept "No hot leads" hidden while WhatsApp alone was shown, with no
    # card visible under it.
    "the empty note counts leads the source hides": (
        "if (on) { inGroup[hlChannel(lead)]++; if (hlFromSource(lead, s)) shown++; }",
        "if (on) { inGroup[hlChannel(lead)]++; shown++; }",
        "Warm under Clare calls: nothing visible"),
    "the empty note drawn counts leads the source hides": (
        "const shown = list.filter(l => hlShows(l, f) && hlFromSource(l, s)).length;",
        "const shown = list.filter(l => hlShows(l, f)).length;",
        "rendered: nothing of that grade in the source shown"),
    "the empty note does not name the source": (
        "return `No ${f === 'all' ? '' : f + ' '}${HL_CHANNEL_NOUN[src]} in this list.`;",
        "return `No ${f} leads in this list.`;",
        "the note names the source"),
    # --- All Kommo leads (28 Oct 2026) ---------------------------------------------------
    # Every lead in Kommo, through /api/kommo_leads: a card's name is whatever someone typed
    # on it in Kommo, and its link is Kommo's.
    "the Kommo card's name is not escaped": (
        '<span class="hl-name kl-name">${esc(name)}</span>', '<span class="hl-name kl-name">${name}</span>',
        "hostile Kommo lead: markup"),
    "the Kommo card's own name is not escaped on its second line": (
        '<div class="kl-cardname">Card: ${esc(cardName)}</div>', '<div class="kl-cardname">Card: ${cardName}</div>',
        "hostile Kommo lead: markup"),
    "a masked Kommo card shows the contact details": (
        "const full = KL_VIEW === 'full';", "const full = true;", "masked Kommo view"),
    "the Kommo link skips hlSafeUrl": (
        "const link = hlExtLink(k.leadUrl, 'Open in Kommo', 'Opens this lead in Kommo');",
        'const link = `<a href="${esc(k.leadUrl)}" target="_blank" rel="noopener noreferrer">Open in Kommo</a>`;',
        "hostile Kommo lead: markup"),
    # hlApplyFilters hides hlCard-<i> for every HOT_LEADS index: under that prefix the list
    # above's buttons would hide and show the Kommo cards.
    "Kommo cards use the hot-leads card ids": (
        '<div class="prop-card kl-card" id="klCard-${n}">', '<div class="prop-card kl-card" id="hlCard-${n}">',
        "never hlCard-"),
    "the Kommo card opens on every column": (
        "let KL_SCOPE = 'incoming';", "let KL_SCOPE = 'all';", "scope=incoming&page=1"),
    # Pages are ordered by lead id, so a lead that arrives mid-read is read twice.
    "the Kommo pages are not de-duplicated": (
        "if (ids.has(id)) return;", "if (false) return;", "duplicates removed by lead id"),
    # hlSortNewest drops a record with no grade: most Kommo leads have none.
    "the Kommo list is sorted by hlSortNewest": (
        "KL_LEADS = klSortNewest(klMerge(KL_LEADS, a.leads));",
        "KL_LEADS = hlSortNewest(klMerge(KL_LEADS, a.leads));",
        "ungraded ones too"),
    "Unlock leaves the Kommo list in the view it had": (
        "if (KL_STATE !== 'idle') await loadKommoLeads(true);", "", "Unlock reloads the Kommo list"),
    "a Kommo list half masked is drawn full": (
        "KL_VIEW = ((KL_PAGE === 0 || KL_VIEW === 'full') && a.contact === 'full') ? 'full' : 'masked';",
        "KL_VIEW = a.contact === 'full' ? 'full' : KL_VIEW;",
        "a list half masked is a masked list"),
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
    # 28 Oct 2026: opening the tab reads the Kommo card too; without it the card sits on
    # "Loading Kommo leads…" until someone finds its Refresh.
    "the nav button does not read the Kommo list": (
        "onclick=\"showTab('hotLeadsTab', this); loadHotLeads(); loadKommoLeads();\"",
        "onclick=\"showTab('hotLeadsTab', this); loadHotLeads();\"",
        "loadKommoLeads()"),
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
