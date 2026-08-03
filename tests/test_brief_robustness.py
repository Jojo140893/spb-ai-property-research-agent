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
    assert b.bedrooms_min == 3, "the documented default should apply"
    assert b.budget_max == 0.0
    # A size the client never mentioned must not become a requirement they are held to.
    # House size is a hard rejection and is recorded on 18% of stock, so a default of
    # 150 m² silently emptied whole states.
    assert b.house_size_min_sqm == 0.0, "an unstated house size must impose no minimum"
    assert b.land_size_min_sqm == 0.0, "an unstated land size must impose no minimum"


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


def run_all():
    tests = [
        ("null in any numeric field", test_a_null_in_any_numeric_field_does_not_crash),
        ("empty string in any numeric field", test_an_empty_string_in_any_numeric_field_does_not_crash),
        ("junk text falls back", test_junk_text_in_a_numeric_field_falls_back_rather_than_crashing),
        ("NaN / infinity never reach the brief", test_nan_and_infinity_do_not_survive_into_the_brief),
        ("a completely empty brief parses", test_a_completely_empty_brief_still_parses),
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
