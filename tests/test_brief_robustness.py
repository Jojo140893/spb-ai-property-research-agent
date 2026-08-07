"""
A client brief arrives over HTTP. It must not be able to crash the pipeline.

Coleen cleared a number field, pressed search, and got HTTP 500:

    brief_parser.py: bedrooms_min=int(raw_data.get('bedrooms_min', 3))
    TypeError: int() argument must be ... not 'NoneType'

A PRESENT-BUT-NULL key is not a missing key, and `dict.get`'s default only covers the
second case. The browser sends null for an empty number input — parseInt('') is NaN
and JSON.stringify writes NaN as null — so emptying any one of seven fields broke the
search. Fixed at both ends; these tests hold the API end, because that is the one that
has to survive input it did not author.
"""

from brief_parser import ClientBriefParser

FULL = dict(client_name="T", budget_max=900000, preferred_spending_cap=850000,
            buyer_type="Investor", state="QLD", primary_suburbs=["Ripley"],
            bedrooms_min=4, bathrooms_min=2, car_spaces_min=2, storeys_max=2,
            land_size_min_sqm=300, house_size_min_sqm=150, search_radius_km=10)

NUMERIC = ("budget_max", "preferred_spending_cap", "bedrooms_min", "bathrooms_min",
           "car_spaces_min", "storeys_max", "land_size_min_sqm", "house_size_min_sqm",
           "search_radius_km")


def test_a_null_in_any_numeric_field_does_not_crash():
    """The actual bug. Every one of these was a 500."""
    for field in NUMERIC:
        brief = {**FULL, field: None}
        b = ClientBriefParser.parse_dict(brief)          # must not raise
        assert b is not None, field


def test_an_empty_string_in_any_numeric_field_does_not_crash():
    for field in NUMERIC:
        b = ClientBriefParser.parse_dict({**FULL, field: ""})
        assert b is not None, field


def test_junk_text_in_a_numeric_field_falls_back_rather_than_crashing():
    for field in NUMERIC:
        b = ClientBriefParser.parse_dict({**FULL, field: "not a number"})
        assert b is not None, field


def test_nan_and_infinity_do_not_survive_into_the_brief():
    """json.loads accepts NaN and Infinity. Both poison every comparison downstream —
    `price > budget_max` is False for NaN, so a hard budget filter silently stops
    filtering."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        b = ClientBriefParser.parse_dict({**FULL, "budget_max": bad})
        assert b.budget_max == b.budget_max, f"NaN reached the brief via {bad}"
        assert b.budget_max not in (float("inf"), float("-inf")), bad


def test_a_completely_empty_brief_still_parses():
    b = ClientBriefParser.parse_dict({})
    assert b is not None
    assert b.budget_max == 0.0
    # A size the client never mentioned must not become a requirement they are held to.
    # House size is a hard rejection and is recorded on 18% of stock, so a default of
    # 150 m² silently emptied whole states.
    assert b.house_size_min_sqm == 0.0, "an unstated house size must impose no minimum"
    assert b.land_size_min_sqm == 0.0, "an unstated land size must impose no minimum"


def test_an_unstated_requirement_is_never_invented():
    """The same rule as the two sizes above, applied to the rest of the criteria.

    This test used to assert `bedrooms_min == 3` — "the documented default should
    apply" — which is exactly the bug. Every one of these is a MANDATORY filter in
    scoring_engine, so a brief silent about bathrooms rejected honest "1 bathroom"
    stock against a requirement nobody gave, while a row that recorded nothing at all
    passed the same filter untouched. A silent brief means no preference.
    """
    b = ClientBriefParser.parse_dict({})
    assert (b.bedrooms_min, b.bathrooms_min, b.car_spaces_min) == (0, 0, 0), (
        b.bedrooms_min, b.bathrooms_min, b.car_spaces_min)
    assert b.storeys_max is None, "an unstated storey cap must not reject a 3-storey home"


def test_an_unstated_storey_cap_rejects_nothing():
    """storeys_max=None must not crash the comparison it used to satisfy.

    `prop.storeys > brief.storeys_max` is a TypeError against None, so removing the
    invented cap without guarding the comparison would trade a silent wrong answer for
    a 500. Both ends are checked here: no crash, and no rejection.
    """
    from schema import CandidateProperty, PriceBreakdown, TurnkeyStatus
    from scoring_engine import ScoringEngine

    brief = ClientBriefParser.parse_dict(
        {**FULL, "storeys_max": None, "primary_suburbs": ["Coomera"],
         "search_radius_km": None})
    pb = PriceBreakdown(
        advertised_package_price=700000, land_price=300000, build_price=400000,
        fixed_site_costs=0, driveway_cost=0, fencing_cost=0, landscaping_cost=0,
        flooring_cost=0, blinds_cost=0, hvac_cost=0, estimated_additional_costs=0,
        realistic_total_price=700000, turnkey_status=TurnkeyStatus.FULL_TURNKEY)
    three_storey = CandidateProperty(
        property_id="P1", lot_address="Lot 1", suburb="Coomera", state="QLD",
        builder_name="Avia Homes", developer_name="Dev", house_design="D",
        bedrooms=4, bathrooms=2, car_spaces=2, storeys=3,
        land_size_sqm=400.0, house_size_sqm=200.0, title_status="Titled",
        expected_title_date="Ready", price_breakdown=pb,
        estimated_rent_weekly_min=600, estimated_rent_weekly_max=650,
        amenities_summary="", builder_confidence_rating="HIGH",
        source_channel="E-Agent", source_url_or_ref="http://x",
        date_checked="07/08/2026")
    result = ScoringEngine.evaluate_property(brief, three_storey)   # must not raise
    assert "Storeys" not in result.rejection_reason, result.rejection_reason
    assert not result.hard_rejection, result.rejection_reason


def test_numbers_sent_as_strings_are_accepted():
    """A form can send "900000" rather than 900000, and money often carries $ and commas."""
    b = ClientBriefParser.parse_dict({**FULL, "budget_max": "$900,000",
                                      "bedrooms_min": "4"})
    assert b.budget_max == 900000.0
    assert b.bedrooms_min == 4


def test_blank_suburbs_are_dropped_not_searched_for():
    """"Coomera, , " used to yield a '' suburb, which matches nothing and quietly
    dragged a distance filter to no results."""
    b = ClientBriefParser.parse_dict({**FULL, "primary_suburbs": ["Coomera", "", "  "]})
    assert b.primary_suburbs == ["Coomera"], b.primary_suburbs
    b2 = ClientBriefParser.parse_dict({**FULL, "primary_suburbs": None})
    assert b2.primary_suburbs == []


def test_a_real_brief_is_unchanged():
    """The hardening must not alter a well-formed brief."""
    b = ClientBriefParser.parse_dict(FULL)
    assert b.budget_max == 900000.0
    assert b.preferred_spending_cap == 850000.0
    assert (b.bedrooms_min, b.bathrooms_min, b.car_spaces_min) == (4, 2, 2)
    assert b.storeys_max == 2
    assert b.land_size_min_sqm == 300.0
    assert b.house_size_min_sqm == 150.0
    assert b.search_radius_km == 10.0
    assert b.primary_suburbs == ["Ripley"]


def test_no_hostile_payload_reaches_a_500():
    """A malformed request must be answered, not crash the function.

    Every one of these returned HTTP 500 — "we broke" — instead of a result or a 400
    naming the bad field:

      * a Python int beyond float range (json.loads turns a bare 1e400-sized literal
        into one) raises OverflowError, not ValueError, so it went past every parser;
      * inf and nan survive json.loads and then blew up inside int();
      * primary_suburbs sent as a bare scalar was iterated in one file and subscripted
        in another, crashing both the scoring path and the zero-results path.

    The zero-results branch was the worst of them, because it returns before the parser
    runs — so the path a caller hits when their search is already going badly was the
    one with no input handling at all.
    """
    import os
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "api"))
    os.environ.setdefault("SPB_SNAPSHOT_JSON", os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "vercel_site", "stock.json"))
    import research
    from research import BadRequest

    huge = 10 ** 400
    hostile = [
        "not-a-dict",
        {"client_brief": "a string"},
        {"client_brief": [1, 2]},
        {"client_brief": None},
        {"client_brief": {}},
        {"client_brief": {"state": "NSW", "budget_max": huge}},
        {"client_brief": {"state": "NSW", "budget_max": float("inf")}},
        {"client_brief": {"state": "NSW", "budget_max": float("nan")}},
        {"client_brief": {"state": "NSW", "budget_max": [1]}},
        {"client_brief": {"state": {"x": 1}, "budget_max": 900000}},
        {"client_brief": {"state": "NSW", "budget_max": 900000, "primary_suburbs": 5}},
        {"client_brief": {"state": "NSW", "budget_max": 900000, "primary_suburbs": True}},
        {"client_brief": {"state": "NSW", "budget_max": 900000,
                          "primary_suburbs": [{"a": 1}]}},
        {"client_brief": {"state": "NSW", "budget_max": 900000, "storeys_max": huge}},
        {"client_brief": {"state": "NSW", "budget_max": 900000, "bedrooms_min": huge}},
        {"client_brief": {"state": "NSW", "budget_max": 900000, "search_radius_km": [5]}},
        # the zero-results branch, reached with junk still in the brief
        {"client_brief": {"state": "ACT", "budget_max": huge, "primary_suburbs": 7}},
    ]
    for payload in hostile:
        try:
            research.run_research(payload)
        except BadRequest:
            pass                      # a 400 naming the problem is the correct answer
        except Exception as exc:      # noqa: BLE001
            raise AssertionError(
                f"{type(exc).__name__} on {str(payload)[:90]}: {exc}") from exc


def test_a_brief_cannot_inject_script_into_the_client_report():
    """index.html renders this report with document.write(), so an unescaped angle
    bracket is script execution, not a formatting glitch.

    The consultant types the client name and the suburbs; the listing text comes out of
    a builder's spreadsheet. Neither is trusted input, and both were interpolated raw.
    """
    from brief_parser import ClientBriefParser
    from client_report import ClientReportGenerator

    payload = "<script>alert(1)</script>"
    brief = ClientBriefParser.parse_dict(
        {"client_name": payload, "state": "NSW", "budget_max": 900_000,
         "primary_suburbs": [payload]})
    out = ClientReportGenerator.generate_html(brief, [])
    assert "<script>alert" not in out, "script tag reached the client report"
    assert "&lt;script&gt;" in out, "the name should still be shown, just inert"

    # Quotes must be escaped too, or the payload breaks out of an attribute instead of
    # sitting inside one. Asserted on the payload itself rather than on a window of
    # surrounding text, which contains the template's own quotes (class="meta").
    attr = '" onerror="alert(1)'
    quoted = ClientBriefParser.parse_dict(
        {"client_name": attr, "state": "NSW", "budget_max": 900_000})
    html_out = ClientReportGenerator.generate_html(quoted, [])
    assert attr not in html_out, "the raw quote survived; it can break out of an attribute"
    assert "&quot; onerror=&quot;alert(1)" in html_out, "the name should still be shown, inert"


def run_all():
    tests = [
        ("no hostile payload reaches a 500", test_no_hostile_payload_reaches_a_500),
        ("a brief cannot inject script", test_a_brief_cannot_inject_script_into_the_client_report),
        ("null in any numeric field", test_a_null_in_any_numeric_field_does_not_crash),
        ("empty string in any numeric field", test_an_empty_string_in_any_numeric_field_does_not_crash),
        ("junk text falls back", test_junk_text_in_a_numeric_field_falls_back_rather_than_crashing),
        ("NaN / infinity never reach the brief", test_nan_and_infinity_do_not_survive_into_the_brief),
        ("a completely empty brief parses", test_a_completely_empty_brief_still_parses),
        ("an unstated requirement is never invented", test_an_unstated_requirement_is_never_invented),
        ("an unstated storey cap rejects nothing", test_an_unstated_storey_cap_rejects_nothing),
        ("numbers as strings accepted", test_numbers_sent_as_strings_are_accepted),
        ("blank suburbs dropped", test_blank_suburbs_are_dropped_not_searched_for),
        ("a real brief is unchanged", test_a_real_brief_is_unchanged),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] brief: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] brief: {name}: {e}")
        except Exception as e:
            failed += 1
            print(f" [FAIL] brief: {name}: {type(e).__name__}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
