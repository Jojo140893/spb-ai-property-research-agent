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
