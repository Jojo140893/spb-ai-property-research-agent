"""The comparison document Coleen builds from her own selection.

WHY THIS RUNS NODE. The generator lives in index.html, because the deployed site is a
static snapshot and the selection spans searches: no single request holds all the data, so
the document is assembled in the browser from what it already has. That makes it
JavaScript, and re-implementing it in Python to test it would be testing the
re-implementation. So the function is sliced out of index.html and exercised under Node —
against the code that actually ships, not a copy that can drift.

The checks themselves are in comparison_report_checks.mjs.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
INDEX = APP / "index.html"
CHECKS = Path(__file__).resolve().parent / "comparison_report_checks.mjs"

START = "const EFFECTIVE = p =>"
END = "    function openComparisonReport()"

# index.html's own escaper, copied verbatim so the harness escapes exactly as the page
# does. If these ever diverge the injection checks would pass here and fail in a browser.
ESC = ("const esc = s => String(s ?? '').replace(/[&<>\"]/g, c => "
       "({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));\n")

# clientReportDocument reads the client's name off the brief form. Under Node there is no
# DOM, so stub the one call it makes — the name still flows through esc(), which is what
# the escaping check below actually exercises.
STUB_DOC = ("globalThis.document = { getElementById: () => "
            "({ value: 'Test Client' }) };\n")


def _generator_source():
    src = INDEX.read_text(encoding="utf-8")
    assert START in src, "the comparison generator moved — update START"
    assert END in src, "openComparisonReport moved — update END"
    return src[src.index(START):src.index(END)]


def test_the_generator_is_still_in_index_html():
    """A rename that silently skipped the checks would be worse than a failing test."""
    body = _generator_source()
    assert "function comparisonDocument(" in body
    assert "function clientReportDocument(" in body
    assert "SPB PREFERRED" in body


def test_selection_is_keyed_on_content_hash_not_row_position():
    """The bug this feature replaced: `data-idx` meant a different building after a new
    search, so a pick made under one brief compared something else under the next."""
    src = INDEX.read_text(encoding="utf-8")
    assert 'class="cmp-box" data-hash=' in src, "compare boxes are no longer hash-keyed"
    assert not re.search(r'class="cmp-box"\s+data-idx=', src), \
        "compare boxes went back to row-position keying"


def test_the_pair_only_api_is_gone():
    """Pre-rendering one document per pair capped at 10 is what made the button dead: on a
    37-option search that is 10 of 666 pairings, all of them 'rank 1 vs something'."""
    src = INDEX.read_text(encoding="utf-8")
    assert "function comparisonKey(" not in src, "the pair-key helper is back"
    assert "COMPARISONS[" not in src, "the frontend depends on pre-rendered pairs again"


def test_more_than_two_can_be_selected():
    src = INDEX.read_text(encoding="utf-8")
    assert "if (boxes.length > 2) boxes[0].checked = false" not in src, \
        "a third selection is being silently un-ticked again"


def test_the_comparison_document(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the browser-side generator cannot be run here")
    (tmp_path / "generator.mjs").write_text(
        ESC + STUB_DOC + _generator_source()
        + "\nexport { comparisonDocument, clientReportDocument };\n", encoding="utf-8")
    shutil.copy(CHECKS, tmp_path / "checks.mjs")
    p = subprocess.run([node, "checks.mjs"], cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, "the comparison document failed its checks:\n" + p.stdout + p.stderr
