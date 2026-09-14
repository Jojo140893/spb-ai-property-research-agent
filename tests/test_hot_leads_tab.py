"""The Hot Leads (Agent 1) tab.

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
