"""Helpers index.html calls across functions must not be nested inside another one.

THE BUG THIS EXISTS FOR. agoText and agoShort were first inserted inside
paintChannelBanner, after its early return. Function declarations hoist to the enclosing
FUNCTION, not the file, so the channels table — rendered elsewhere — hit
"ReferenceError: agoText is not defined", and the Settings tab sat on "Loading..."
forever. It only showed when NO source was stale, because the early return is the path
that reaches the misplaced declarations. So it shipped, and Ahsan found it on the live
site.

tests/ago_checks.mjs could not have caught it: it slices the two functions out of the page
and runs them alone, which is exactly the arrangement that hides a scoping mistake.

HOW THIS CHECKS IT. Not by parsing JavaScript — a first attempt counted braces and had to
be taught about strings, template literals and regex literals in turn, and was still wrong
about index.html's own escaper, /[&<>"]/g. The file has one consistent shape instead:
every top-level function sits at four spaces and is closed by a line that is exactly
"    }". That makes "is this declaration inside another function's body" a question about
line ranges, which is answerable exactly.
"""
import re
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parent.parent / "index.html"
LINES = INDEX.read_text(encoding="utf-8").split("\n")

_TOP_LEVEL = re.compile(r"^    (?:async )?function ([A-Za-z_$][\w$]*)\s*\(")
_ANY_DECL = re.compile(r"^(\s*)(?:async )?function ([A-Za-z_$][\w$]*)\s*\(")


def _extent(lines, i):
    """Line of the "    }" that closes a function declared at line i."""
    return next((j for j in range(i + 1, len(lines)) if lines[j] == "    }"), len(lines) - 1)


def _top_level_functions(lines):
    """{name: (first line, last line)} for every function declared at four spaces."""
    out = {}
    for i, line in enumerate(lines):
        m = _TOP_LEVEL.match(line)
        if m:
            out[m.group(1)] = (i, _extent(lines, i))
    return out


def _nested_declarations(lines):
    """{name: line} for every function declared deeper than four spaces."""
    out = {}
    for i, line in enumerate(lines):
        m = _ANY_DECL.match(line)
        if m and len(m.group(1)) > 4:
            out.setdefault(m.group(2), i)
    return out


TOP = _top_level_functions(LINES)
NESTED = _nested_declarations(LINES)

# Declared in one place and called from another. A nested declaration is invisible to the
# caller, and the page dies at runtime rather than at build time.
SHARED = [
    "agoText",                # the channels table AND the warning banner
    "agoShort",
    "paintChannelBanner",
    "packageReportDocument",  # both report entry points
    "comparisonDocument",     # r.html dispatches share links onto these two by name
    "clientReportDocument",
    "openGeneratedReport",
    "fetchDrafts",            # called by openGeneratedReport
    "withTimeout",
    "shareBar",
    "buildShareLink",
    "loadHotLeads",           # inline onclick on the nav button and Refresh: globals only
    "rerunHotLead",           # inline onclick on every hot-lead card
]


def test_every_shared_helper_is_declared_at_the_top_level():
    missing = [n for n in SHARED if n not in TOP]
    assert not missing, (
        f"not declared at four spaces in index.html: {missing}. Either renamed, or "
        f"nested inside another function — found nested at line(s) "
        f"{ {n: NESTED.get(n) for n in missing} }")


@pytest.mark.parametrize("name", SHARED)
def test_no_shared_helper_is_buried_inside_another_function(name):
    """The exact shape of the agoText bug: indented like a top-level function, but
    sitting between the first and last line of one that was already open."""
    if name not in TOP:
        pytest.skip("covered by test_every_shared_helper_is_declared_at_the_top_level")
    start, _ = TOP[name]
    for owner, (o_start, o_end) in TOP.items():
        if owner == name:
            continue
        assert not (o_start < start < o_end), (
            f"{name} (line {start + 1}) sits inside {owner} "
            f"(lines {o_start + 1}-{o_end + 1}), so callers outside it get ReferenceError")


def test_the_report_renderers_are_reachable_from_the_share_page():
    """build_web.py slices these out of index.html into r.html between two markers. A
    declaration nested in a parent would be sliced out with it, and every share link
    already in a client's inbox would render a blank page."""
    for name in ("comparisonDocument", "clientReportDocument", "packageReportDocument"):
        assert name in TOP, f"{name} is not a top-level declaration"


# ------------------------------------------------------------------ the scanner itself

def test_the_scanner_finds_a_functions_extent():
    start, end = TOP["paintChannelBanner"]
    assert LINES[start].strip().startswith("function paintChannelBanner")
    assert LINES[end] == "    }"
    assert end > start


def test_the_scanner_catches_the_broken_shape():
    """Proves the check can FAIL, using the real shape of the bug rather than trusting it."""
    broken = [
        "    function outer() {",
        "        if (x) return;",
        "    function buried(c) {",
        "        return c;",
        "    }",
        "    }",
    ]
    tops = _top_level_functions(broken)
    b_start = tops["buried"][0]
    o_start, o_end = tops["outer"]
    assert o_start < b_start < o_end, "the scanner must see buried inside outer"


def test_the_scanner_accepts_siblings():
    fine = [
        "    function first() {",
        "        return 1;",
        "    }",
        "",
        "    function second() {",
        "        return first();",
        "    }",
    ]
    tops = _top_level_functions(fine)
    s_start = tops["second"][0]
    f_start, f_end = tops["first"]
    assert not (f_start < s_start < f_end)


# ------------------------------------------------------------------ the script element

def test_nothing_closes_the_page_script_early():
    """A literal end tag anywhere inside index.html's script — in a string, in a template
    literal, or in a COMMENT — ends the element there and dumps the rest of the
    application onto the page as visible text.

    This shipped on 19 Aug. The report generator needs a real <script> in the document it
    writes, so the close tag was written plainly; the outer element ended at that point
    and the live site showed raw source under the tabs. The fix escapes it — and then the
    COMMENT explaining the fix quoted the tag literally and broke it a second time, at
    exactly the word "literal".

    So the rule is mechanical: between index.html's opening script tag and its last
    closing one, the sequence must not appear unescaped even once.
    """
    text = INDEX.read_text(encoding="utf-8")
    lower = text.lower()
    start = lower.index("<script>") + len("<script>")
    end = lower.rindex("</script>")
    inner = text[start:end]
    close = "</" + "script"
    bad = [i for i in range(len(inner)) if inner[i:i + len(close)].lower() == close]
    assert not bad, (
        f"{len(bad)} literal end tag(s) inside the page script — the browser stops there. "
        f"First at offset {bad[0]}: ...{inner[max(0, bad[0] - 70):bad[0] + 20]!r}. "
        f"Write it split or escaped.")


def test_the_report_still_ships_its_fitter_script():
    """The escape must not have removed the feature it protects."""
    text = INDEX.read_text(encoding="utf-8")
    assert 'data-spb="fit"' in text, "the one-page fitter is gone"
    assert "<" + chr(92) + "/script>" in text, "its close tag must be escaped, not deleted"
