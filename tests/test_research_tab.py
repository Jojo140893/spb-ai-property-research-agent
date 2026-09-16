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
        "never be able to swallow a re-run.\n                            runResearchAgent();",
        "never be able to swallow a re-run.\n                            "
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
