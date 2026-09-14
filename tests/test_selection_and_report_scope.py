"""A consultant's selection, and how much of it a report is allowed to show.

Coleen, 14 Aug 2026: "when i filter buildings then i select the building i like and then i
reset the filter and apply another filter and select the building both buildings should
compare ... also i can select more then 2 buildings to compare not just 2", and separately
"the reporting feature is restricted to the 'top 3' identified properties. We require the
ability to generate reports for any specific property selected."

Both need something the pipeline did not previously carry: a listing identity that means
the same building tomorrow. `property_id` is "PROP-{n}" assigned per run over the candidate
list, so it is stable only while the snapshot is — and the snapshot is rebuilt by the
harvest every night. Measured on 14 Aug against a simulated re-harvest (50 rows dropped,
20 appended): all 40 shortlisted property_ids shifted and 6 of them came to name a
DIFFERENT building. A selection keyed on it would silently compare the wrong properties
the morning after it was made, which is worse than failing outright.

content_hash does survive that, which is why the Building Stock tab has keyed its marked
set on it since 30 Jul. These tests pin it end to end.
"""
import os
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "api"))


# --------------------------------------------------------------- identity threading

def test_snapshot_reader_selects_content_hash():
    """The SQLite reader must ask for it, or only the JSON path would have it."""
    import _candidates
    assert "content_hash" in _candidates.SNAPSHOT_FIELDS, (
        "content_hash dropped from SNAPSHOT_FIELDS — the SQLite reader would return rows "
        "without it and every selection made against a local run would be unkeyable")


def test_candidate_property_carries_identity_and_rebate():
    from schema import CandidateProperty
    ann = CandidateProperty.__annotations__
    for f in ("content_hash", "incentive_amount", "incentive_text"):
        assert f in ann, f"CandidateProperty lost {f}"


def test_both_serialisations_expose_the_same_new_keys():
    """server.py and api/research.py must not drift.

    They already had: server.py's shortlist entry is missing source_link, date_checked and
    competitive, which is why the local app and the deployed one show different cards. A
    selection key going the same way would mean a pick made locally cannot be compared on
    the deployed site.
    """
    research_src = (APP / "api" / "research.py").read_text(encoding="utf-8")
    server_src = (APP / "server.py").read_text(encoding="utf-8")
    for key in ("'content_hash'", "'incentive_amount'", "'incentive_text'", "'lot_number'"):
        assert key in research_src, f"api/research.py stopped emitting {key}"
        assert key in server_src, f"server.py stopped emitting {key} — local/deployed drift"


@pytest.mark.parametrize("second_brief", [
    {"budget_max": 760000, "search_radius_km": 25},
    {"min_bedrooms": 2},
])
def test_identity_survives_a_second_search(second_brief):
    """The actual workflow: search, pick, search again, still holding the same building."""
    import research

    def run(**over):
        brief = {"client_name": "T", "state": "VIC",
                 "primary_suburbs": ["Armstrong Creek"], "budget_max": 800000,
                 "min_bedrooms": 3, "search_radius_km": 40}
        brief.update(over)
        return research.run_research({"client_brief": brief}).get("shortlist") or []

    a = run()
    if len(a) < 2:
        pytest.skip("snapshot has too little Armstrong Creek stock to exercise this")
    assert all(e.get("content_hash") for e in a), "a shortlist row has no content_hash"
    assert len({e["content_hash"] for e in a}) == len(a), "content_hash is not unique per row"

    b = run(**second_brief)
    shared = {e["content_hash"] for e in a} & {e["content_hash"] for e in b}
    assert shared, "no overlap between the two searches — cannot test carry-over"
    # The point: a hash picked in search A still identifies one row in search B.
    by_hash = {e["content_hash"]: e for e in b}
    for h in shared:
        assert by_hash[h]["lot_address"], "carried hash resolves to an empty row"


# ------------------------------------------------------------------ report scope

def _prop(n, **over):
    """Reuses the established factory rather than restating PriceBreakdown's 13 fields —
    those signatures move, and a second hand-rolled copy is a second thing to fix."""
    from tests.test_benchmark_buildings import _candidate
    kw = dict(property_id=f"PROP-{n}", lot_address=f"{n} Test St", suburb="Testville",
              state="VIC", content_hash=f"v2:hash{n}")
    kw.update(over)
    return _candidate(**kw)


def _brief(**over):
    """Every mandatory field, so a test states only what it varies — same shape as the
    helper in test_benchmark_buildings.py."""
    from schema import ClientBrief, BuyerType
    kw = dict(client_name="Test Client", budget_max=800_000.0,
              preferred_spending_cap=780_000.0, deposit_amount=100_000.0,
              finance_status="Pre-approved", buyer_type=BuyerType.INVESTOR,
              state="VIC", primary_suburbs=["Testville"], bedrooms_min=4,
              bathrooms_min=2, car_spaces_min=1, storeys_max=2,
              land_size_min_sqm=0.0, house_size_min_sqm=0.0)
    kw.update(over)
    return ClientBrief(**kw)


def test_report_defaults_to_three_so_the_existing_document_is_unchanged():
    from client_report import ClientReportGenerator
    html = ClientReportGenerator.generate_html(_brief(), [_prop(i) for i in range(1, 9)])
    assert html.count("Option 1") >= 1
    assert "Option 4" not in html, "the default automatic report grew past 3 options"
    assert "following 3 option(s)" in html


def test_the_stated_count_matches_what_is_rendered():
    """The sentence used to print the TRUNCATED count against an untruncated shortlist,
    telling a client we reviewed 3 when hundreds were ranked."""
    from client_report import ClientReportGenerator
    for n in (1, 2, 3):
        html = ClientReportGenerator.generate_html(
            _brief(), [_prop(i) for i in range(1, n + 1)])
        assert f"following {n} option(s)" in html


@pytest.mark.parametrize("limit,expected", [(0, 8), (5, 5), (3, 3), (1, 1)])
def test_caller_decides_how_many_options_the_report_shows(limit, expected):
    """Coleen's #3: a report over exactly what was selected, not a fixed top 3."""
    from client_report import ClientReportGenerator
    html = ClientReportGenerator.generate_html(
        _brief(), [_prop(i) for i in range(1, 9)], limit=limit)
    assert f"following {expected} option(s)" in html
    assert f"Option {expected}" in html
    assert f"Option {expected + 1}" not in html


def test_report_renders_every_selected_option_without_a_hidden_cap():
    from client_report import ClientReportGenerator
    html = ClientReportGenerator.generate_html(
        _brief(), [_prop(i) for i in range(1, 13)], limit=0)
    assert "Option 12" in html, "a 12-option selection lost options to an internal cap"
