"""
"Any state" is a national search, and nothing may relabel it as Queensland.

The Research tab's State select defaults to "Any state" and sends state ''. The brief
parser turned that into 'QLD', so a national search (live, 16 Sep: 510 results, VIC 321,
WA 100, ACT 45, NSW 41, SA 2, QLD 1) was recorded as "QLD/General", builder coverage was
counted against QLD builders, and both client documents printed QLD in their header.
build_packages already searched every state; only the labels lied.

Fixing the parser alone is not enough: get_builders_by_state('') then returned only the
builders with no state listed (`'' in ['QLD']` is False), and the report headers printed
an empty gap. These tests hold the parser, the registry and both report headers.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brief_parser import ClientBriefParser  # noqa: E402


# ------------------------------------------------------------------ the parser

@pytest.mark.parametrize("raw", [
    {"state": ""},
    {"state": None},
    {"state": "   "},
    {},
])
def test_a_blank_state_is_any_state_not_qld(raw):
    brief = ClientBriefParser.parse_dict(dict(raw, client_name="T", budget_max=750000))
    assert brief.state == "", brief.state


@pytest.mark.parametrize("given,expected", [("QLD", "QLD"), ("qld", "QLD"), (" vic ", "VIC")])
def test_a_stated_state_is_kept(given, expected):
    assert ClientBriefParser.parse_dict({"state": given}).state == expected


# ------------------------------------------------------------------ builder coverage

_CSV = (
    "NAME,EMAIL,PHONE,BUILDER,STATES,Contract Availble??,Is it available on E Agent?,"
    "WEB PORTAL LINK,EMAIL,PASSWORD,NOTES\n"
    "A,a@x.test,0400000001,Queensland Homes,QLD,Yes,YES,,,,\n"
    "B,b@x.test,0400000002,Victoria Homes,VIC,Yes,YES,,,,\n"
    "C,c@x.test,0400000003,Two State Homes,QLD/NSW,Yes,YES,,,,\n"
    "D,d@x.test,0400000004,Nowhere Listed Homes,,Yes,YES,,,,\n"
)


@pytest.fixture
def registry(tmp_path):
    from builder_registry import BuilderRegistry
    path = tmp_path / "builders.csv"
    path.write_text(_CSV, encoding="utf-8")
    reg = BuilderRegistry(str(path))
    assert len(reg.get_all_builders()) == 4
    return reg


def _names(builders):
    return sorted(b["builder_name"] for b in builders)


@pytest.mark.parametrize("blank", ["", "  ", None])
def test_any_state_puts_every_builder_in_scope(registry, blank):
    assert _names(registry.get_builders_by_state(blank)) == _names(registry.get_all_builders())


def test_a_named_state_still_scopes_the_builders(registry):
    assert _names(registry.get_builders_by_state("qld")) == [
        "Nowhere Listed Homes", "Queensland Homes", "Two State Homes"]
    assert _names(registry.get_builders_by_state("VIC")) == [
        "Nowhere Listed Homes", "Victoria Homes"]


def test_any_state_returns_a_copy_not_the_registry_itself(registry):
    """A caller that filters its result in place must not empty the registry."""
    got = registry.get_builders_by_state("")
    got.clear()
    assert len(registry.get_all_builders()) == 4


# ------------------------------------------------------------------ report headers

def _brief(state):
    return ClientBriefParser.parse_dict({"client_name": "Test Buyer", "state": state,
                                         "budget_max": 750000})


def _pair():
    from schema import ScoringBreakdown
    from tests.test_benchmark_buildings import _candidate
    a = _candidate(property_id="A", lot_address="Lot 1", suburb="Tarneit", state="VIC",
                   builder_name="Alpha Homes", content_hash="v2:a")
    b = _candidate(property_id="B", lot_address="Lot 2", suburb="Walloon", state="QLD",
                   builder_name="Beta Homes", content_hash="v2:b", price=690_000.0)
    for p, score in ((a, 90.0), (b, 85.0)):
        p.scoring = ScoringBreakdown(20, 20, 15, 15, 10, 10, 10, score)
    return a, b


def _meta_line(html):
    lines = re.findall(r'<p class="meta">.*?</p>', html, flags=re.S)
    assert len(lines) == 1, lines
    return lines[0]


def test_the_client_report_header_says_any_state():
    from client_report import ClientReportGenerator
    meta = _meta_line(ClientReportGenerator.generate_html(_brief(""), list(_pair())))
    assert "Any state" in meta, meta
    assert "QLD" not in meta, meta


def test_a_search_with_no_ceiling_prints_no_budget_figure():
    """A brief with no Max budget reaches the report as api/research's NO_CEILING
    (9,007,199,254,740,991, JavaScript's largest safe integer). The server's client report
    printed "Budget up to $9,007,199,254,740,991" (callers verification, 7 Oct 2026)."""
    from client_report import ClientReportGenerator
    brief = ClientBriefParser.parse_dict({"client_name": "Test Buyer", "state": "VIC",
                                          "budget_max": 9007199254740991})
    meta = _meta_line(ClientReportGenerator.generate_html(brief, list(_pair())))
    assert "9,007,199" not in meta, meta
    assert "No budget limit" in meta, meta
    # A real ceiling still prints.
    meta = _meta_line(ClientReportGenerator.generate_html(_brief("VIC"), list(_pair())))
    assert "Budget up to $750,000" in meta, meta


def test_the_comparison_report_header_says_any_state():
    from comparison_report import ComparisonReportGenerator
    meta = _meta_line(ComparisonReportGenerator.generate_html(_brief(""), list(_pair())))
    assert "Any state" in meta, meta
    assert "QLD" not in meta, meta


@pytest.mark.parametrize("module,cls", [("client_report", "ClientReportGenerator"),
                                        ("comparison_report", "ComparisonReportGenerator")])
def test_a_named_state_is_still_printed(module, cls):
    gen = getattr(__import__(module), cls)
    meta = _meta_line(gen.generate_html(_brief("NSW"), list(_pair())))
    assert "NSW" in meta, meta
    assert "Any state" not in meta, meta


def test_the_comparison_header_escapes_the_state():
    """The state is brief input; the comparison header printed it raw."""
    from comparison_report import ComparisonReportGenerator
    from schema import ClientBrief
    brief = _brief("NSW")
    brief = ClientBrief(**dict(brief.__dict__, state="<img src=x onerror=alert(1)>"))
    meta = _meta_line(ComparisonReportGenerator.generate_html(brief, list(_pair())))
    assert "<IMG" not in meta.upper(), meta
