"""The client report is a document a buyer reads. It must not show them the working.

Coleen, 19 Aug 2026: prefilling this box "put the internal score and the invented
allowance straight into a document a buyer reads" -- she saw it live.

Half of that was fixed at the time: turnkey_calculator stopped inventing a $15,000
allowance, and the web editor stopped prefilling the box. The RENDERER was never changed,
so every client report generated since -- including the two dated 21 Aug, two days after
she reported it -- still opens "Why we recommend it" with "Strong match (92.5/100)".

Found on 10 Sep by reading 26 real reports in output/.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, "api")

import client_report

APP = Path(__file__).resolve().parents[1]


class _P:
    def __init__(self, reason):
        self.recommendation_reason = reason


def test_the_match_score_never_reaches_the_client():
    """A score out of 100 is how the tool ranked the stock. The buyer is reading the
    answer, not the working."""
    for reason in ("Strong match (92.5/100). Fully satisfies budget, location, and "
                   "structural requirements.",
                   "Satisfactory match (70.0/100). Requires consultant confirmation.",
                   "Low suitability score (40.0/100).",
                   "Good match (81/100). Note: something real."):
        out = client_report._client_reason(_P(reason))
        assert "/100" not in out, f"the score survived: {out!r}"
        assert "match (" not in out.lower()


def test_the_property_facts_survive():
    """Stripping must not empty the section of the things that are about the PROPERTY."""
    out = client_report._client_reason(_P(
        "Strong match (92.5/100). Fully satisfies budget, location, and structural "
        "requirements."))
    assert out == "Fully satisfies budget, location, and structural requirements."


def test_a_real_caveat_survives():
    """"inclusions are not stated by the source, confirm with the builder" is exactly
    what a buyer needs to know before they sign."""
    out = client_report._client_reason(_P(
        "Strong match (88.5/100). Note: Unclear - inclusions are not stated by the "
        "source, confirm with the builder."))
    assert "inclusions are not stated" in out and "confirm with the builder" in out


def test_consultant_only_sentences_are_dropped():
    """"see risk register" and "requires consultant confirmation" address the consultant.
    The report already has a "Things to be aware of" section built from the risks."""
    out = client_report._client_reason(_P(
        "Strong match (90.1/100). Note: Partial Turnkey - allow $15,000 in additional "
        "costs; 2 open risk item(s) - see risk register."))
    assert "risk register" not in out and "open risk item" not in out
    assert "Partial Turnkey" in out, "the real caveat was thrown out with the internal one"


def test_a_wholly_internal_reason_yields_no_section():
    """A heading over an empty paragraph is worse than no heading."""
    for reason in ("Satisfactory match (70.0/100). Requires consultant confirmation on "
                   "turnkey allowances or risk mitigation.",
                   "Verification Status: Pending Confirmation. Requires consultant "
                   "approval before client presentation.",
                   "Awaiting scoring evaluation.",
                   "Low suitability score (40.0/100).",
                   "", None):
        assert client_report._client_reason(_P(reason)) == ""


def test_no_stray_punctuation_is_left_behind():
    """Removing a caveat from the middle of a list must not leave "Note: ;" on the page."""
    for reason in ("Strong match (90/100). Note: 2 open risk item(s) - see risk register.",
                   "Strong match (90/100). Note: ; see risk register."):
        out = client_report._client_reason(_P(reason))
        assert not re.search(r"(Note:\s*[;.]|;\s*[;.]|^\s*[;.])", out), repr(out)


def test_the_renderer_uses_it_rather_than_the_raw_reason():
    """The regression that let this ship: the helper existed nowhere and the template
    interpolated recommendation_reason directly."""
    src = (APP / "client_report.py").read_text(encoding="utf-8", errors="replace")
    assert "_esc(p.recommendation_reason)" not in src, \
        "the client report still renders the scorer's raw reason"
    assert "_client_reason(p)" in src


def test_every_report_shipped_before_today_carried_the_score():
    """Evidence, kept. If output/ still holds them, they all show what was wrong -- this
    is the fixture that proves the defect was real and not theoretical."""
    reports = sorted((APP / "output").glob("client_report_*.html"))
    if not reports:
        return                                   # nothing archived on this machine
    with_score = [f for f in reports
                  if re.search(r"Why we recommend it</h3>\s*<p>[^<]*\d+(?:\.\d)?/100",
                               f.read_text(encoding="utf-8", errors="replace"))]
    assert with_score, "expected the archived reports to demonstrate the old behaviour"
