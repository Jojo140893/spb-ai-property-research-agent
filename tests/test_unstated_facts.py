"""
A listing must not be discarded for a fact the client's brief never asked about.

The gate that enforced this discarded 675 of VIC's 2,690 rows for a missing house size
even when the brief set no house-size minimum. Combined with the other exclusions, every
VIC brief — at every budget, every radius, every bedroom count — returned exactly zero
results, which is what the client hit when she tried to run research in a demo.

The rule these tests pin down:
  * the brief states a minimum for a field -> a listing that is silent about it is
    excluded, and the exclusion is counted and named,
  * the brief is silent about a field    -> a listing that is silent about it is scored,
    and the gap is reported rather than filled in with a plausible figure.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api"))

import _candidates                                                    # noqa: E402
from brief_parser import ClientBriefParser, coerce_number             # noqa: E402
from client_report import ClientReportGenerator                       # noqa: E402
from report_generator import ReportGenerator                          # noqa: E402
from scoring_engine import ScoringEngine                              # noqa: E402
from schema import (CandidateProperty, PriceBreakdown, TurnkeyStatus,  # noqa: E402
                    VerificationStatus)


def _row(**over):
    row = {
        "builder_name": "Testco Homes", "lot_address": "Lot 9", "suburb": "Tarneit",
        "state": "VIC", "postcode": "3029", "availability_status": "Available",
        "price": 700000.0, "bedrooms": 4, "bathrooms": 2,
        "car_spaces": 2, "house_sqm": 180.0, "land_size_sqm": 400.0,
        "house_design": "Aspen 22", "source_url_or_ref": "https://example.test/lot9",
        "source_channel": "Test",
    }
    row.update(over)
    return row


def _brief(**over):
    brief = {"state": "VIC", "budget_max": 800000, "preferred_spending_cap": 780000,
             "bedrooms_min": 4, "bathrooms_min": 2, "car_spaces_min": 2}
    brief.update(over)
    return brief


# ------------------------------------------------------- the candidate gate

def test_missing_house_size_is_scored_when_the_brief_sets_no_minimum():
    rows = [_row(house_sqm=None)]
    pkgs, counts = _candidates.build_packages(_brief(house_size_min_sqm=0), rows)
    assert len(pkgs) == 1, "a brief with no house-size minimum must still see this lot"
    assert counts["incomplete_facts"] == 0
    assert counts["unstated_but_scored"] == {"house size": 1}
    assert pkgs[0]["house_size_sqm"] is None, "the gap must stay a gap, not become a number"


def test_missing_house_size_is_excluded_when_the_brief_sets_a_minimum():
    rows = [_row(house_sqm=None)]
    pkgs, counts = _candidates.build_packages(_brief(house_size_min_sqm=175), rows)
    assert pkgs == [], "it cannot be judged against a minimum it does not state"
    assert counts["incomplete_facts"] == 1
    assert counts["missing_fields"] == {"house size": 1}


def test_missing_bedrooms_follows_the_same_rule():
    rows = [_row(bedrooms=None)]
    assert _candidates.build_packages(_brief(bedrooms_min=4), rows)[0] == []
    pkgs, counts = _candidates.build_packages(_brief(bedrooms_min=0), rows)
    assert len(pkgs) == 1
    assert counts["unstated_but_scored"] == {"bedroom count": 1}


def test_a_stated_fact_below_the_minimum_is_still_rejected():
    """Loosening the missing-fact gate must not loosen the actual requirement."""
    rows = [_row(house_sqm=120.0)]
    pkgs, _ = _candidates.build_packages(_brief(house_size_min_sqm=175), rows)
    assert len(pkgs) == 1, "it reaches the scorer — the scorer is what rejects it"
    brief = ClientBriefParser.parse_dict(_brief(house_size_min_sqm=175))
    scored = ScoringEngine.evaluate_property(brief, _prop(house_size_sqm=120.0))
    assert scored.hard_rejection is True
    assert "House size" in scored.rejection_reason


def test_the_coverage_sentence_reports_what_it_scored_with_a_gap():
    _, counts = _candidates.build_packages(_brief(house_size_min_sqm=0),
                                           [_row(house_sqm=None)])
    text = _candidates.coverage_sentence(counts, "stock.json", "VIC")
    assert "house size unknown on 1" in text
    assert "never filled in with an assumed figure" in text


# ------------------------------------------------------- the scorer and reports

def _prop(**over):
    pb = PriceBreakdown(
        advertised_package_price=700000, land_price=300000, build_price=400000,
        fixed_site_costs=0, driveway_cost=0, fencing_cost=0, landscaping_cost=0,
        flooring_cost=0, blinds_cost=0, hvac_cost=0, estimated_additional_costs=0,
        realistic_total_price=700000, turnkey_status=TurnkeyStatus.FULL_TURNKEY)
    fields = dict(
        property_id="P1", lot_address="Lot 9", suburb="Tarneit", state="VIC",
        builder_name="Testco Homes", developer_name="Dev", house_design="Aspen 22",
        bedrooms=4, bathrooms=2, car_spaces=2, storeys=None, land_size_sqm=400.0,
        house_size_sqm=180.0, title_status="Titled", expected_title_date="Q4 2026",
        price_breakdown=pb, estimated_rent_weekly_min=600, estimated_rent_weekly_max=650,
        amenities_summary="Close to shops", builder_confidence_rating="HIGH",
        source_channel="Test", source_url_or_ref="https://example.test/lot9",
        date_checked="04/08/2026", verification_status=VerificationStatus.VERIFIED,
    )
    fields.update(over)
    return CandidateProperty(**fields)


def test_the_scorer_does_not_crash_on_an_unstated_fact():
    brief = ClientBriefParser.parse_dict(_brief(bedrooms_min=0, bathrooms_min=0,
                                                car_spaces_min=0, house_size_min_sqm=0))
    scored = ScoringEngine.evaluate_property(
        brief, _prop(bedrooms=None, bathrooms=None, car_spaces=None, house_size_sqm=None))
    assert scored.hard_rejection is False, "nothing was violated, so nothing is rejected"
    assert scored.total_score > 0


def test_an_unstated_fact_is_named_as_an_advisory_not_assumed():
    brief = ClientBriefParser.parse_dict(_brief(bedrooms_min=0, house_size_min_sqm=0))
    scored = ScoringEngine.evaluate_property(brief, _prop(bedrooms=None, house_size_sqm=None))
    joined = scored.advisories.lower()
    assert "bedroom count not stated" in joined
    assert "house size not stated" in joined


def test_the_client_report_omits_an_unstated_spec_rather_than_printing_none():
    brief = ClientBriefParser.parse_dict(_brief(bedrooms_min=0, house_size_min_sqm=0))
    prop = _prop(bedrooms=None, house_size_sqm=None)
    prop.scoring = ScoringEngine.evaluate_property(brief, prop)
    html = ClientReportGenerator.generate_html(brief, [prop])
    assert "None bed" not in html and "None m" not in html
    assert ">None<" not in html
    assert "2 bath" in html, "the specs that ARE stated must still be shown"


def test_the_markdown_summary_says_not_stated_rather_than_crashing():
    brief = ClientBriefParser.parse_dict(_brief(bedrooms_min=0, house_size_min_sqm=0))
    prop = _prop(bedrooms=None, house_size_sqm=None)
    prop.scoring = ScoringEngine.evaluate_property(brief, prop)
    md = ReportGenerator.generate_property_summary_markdown(brief, prop)
    assert "Not stated in the builder's stocklist" in md
    assert "None" not in md.replace("None (Full Turnkey)", "")


# ------------------------------------------------------- number coercion

def test_coerce_number_survives_everything_a_form_can_send():
    cases = [("750,000", 750000.0), ("$720,000", 720000.0), ("", None), ("abc", None),
             (None, None), (True, None), (float("nan"), None), (float("inf"), None),
             ("  4  ", 4.0), (7, 7.0), ("1 200", 1200.0)]
    for raw, want in cases:
        got = coerce_number(raw, None)
        assert got == want, f"coerce_number({raw!r}) -> {got!r}, expected {want!r}"


def test_an_unstated_size_imposes_no_minimum():
    brief = ClientBriefParser.parse_dict({"budget_max": 700000})
    assert brief.house_size_min_sqm == 0.0
    assert brief.land_size_min_sqm == 0.0


def run_all():
    tests = [
        ("missing house size scored when unconstrained",
         test_missing_house_size_is_scored_when_the_brief_sets_no_minimum),
        ("missing house size excluded when constrained",
         test_missing_house_size_is_excluded_when_the_brief_sets_a_minimum),
        ("missing bedrooms follows the same rule", test_missing_bedrooms_follows_the_same_rule),
        ("a stated fact below the minimum is still rejected",
         test_a_stated_fact_below_the_minimum_is_still_rejected),
        ("coverage reports what it scored with a gap",
         test_the_coverage_sentence_reports_what_it_scored_with_a_gap),
        ("scorer survives an unstated fact", test_the_scorer_does_not_crash_on_an_unstated_fact),
        ("unstated fact is an advisory, not an assumption",
         test_an_unstated_fact_is_named_as_an_advisory_not_assumed),
        ("client report omits an unstated spec",
         test_the_client_report_omits_an_unstated_spec_rather_than_printing_none),
        ("markdown says not stated", test_the_markdown_summary_says_not_stated_rather_than_crashing),
        ("coerce_number survives form input", test_coerce_number_survives_everything_a_form_can_send),
        ("an unstated size imposes no minimum", test_an_unstated_size_imposes_no_minimum),
        ("locality recovered from real stocklist shapes",
         test_a_locality_is_recovered_from_the_shapes_stocklists_actually_use),
        ("junk in the suburb column still refused", test_junk_in_the_suburb_column_is_still_refused),
        ("address label drops prices/dates/specs", test_the_address_label_drops_prices_dates_and_specs),
        ("address label leaves bed/bath alone", test_the_address_label_leaves_bed_and_bath_counts_alone),
        ("address label strips title dates", test_the_address_label_strips_title_dates_and_quarters),
        ("address label keeps a plain address", test_the_address_label_keeps_an_ordinary_address_untouched),
        ("address label never returns empty", test_the_address_label_never_returns_empty),
        ("recovery only fills an empty field", test_recovery_only_ever_fills_an_empty_field),
        ("recovery drops implausible values", test_recovery_drops_a_value_outside_the_plausible_range),
        ("recovery survives a row with no text", test_recovery_survives_a_row_with_no_source_text),
        ("every shipped rule compiles", test_every_shipped_rule_compiles_and_captures_a_named_group),
        ("display address prefers the street", test_the_display_address_prefers_the_recovered_street),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  [PASS] unstated: {name}")
        except Exception as exc:                                        # noqa: BLE001
            print(f"  [FAIL] unstated: {name}: {exc}")
            failed += 1
    return failed



# ------------------------------------------------------- locality recovery

def test_a_locality_is_recovered_from_the_shapes_stocklists_actually_use():
    """Only ever accepted when the recovered token resolves in the geo index."""
    from geo import SuburbGeoIndex
    idx = SuburbGeoIndex()
    cases = [
        ("Wyndham Gardens, Wyndham Vale 3024", "Wyndham Vale"),   # trailing postcode
        ("Estate : Dream Sebastopol", "Sebastopol"),               # colon separator
        ("Estate : Pinnacle Smythes Creek", "Smythes Creek"),      # estate prefix
        ("COLEDALE DRIVE, MELTON", "MELTON"),                      # street, not the suburb
    ]
    for raw, want in cases:
        got = idx.resolve_locality(raw, "VIC")
        assert got == want, f"resolve_locality({raw!r}) -> {got!r}, expected {want!r}"


def test_junk_in_the_suburb_column_is_still_refused():
    """The recovery must not become a licence to guess. These reached clients once."""
    from geo import SuburbGeoIndex
    idx = SuburbGeoIndex()
    for raw in ("Untitled Packages", "Titled Packages", "7 Star Energy Rating",
                "External Agent Price List", "One Part Contracts", "Fully Refundable",
                "purchaser", "27 RESERVED", "Price List as at 17th July 26",
                "Double Story", "North", "WEST", "SOUTH EAST", "STAGE 3", "2026"):
        got = idx.resolve_locality(raw, "VIC")
        assert got == "", f"{raw!r} is not a locality but resolved to {got!r}"


# ------------------------------------------------------- address labels

def test_the_address_label_drops_prices_dates_and_specs():
    from address_label import clean_display_address
    got = clean_display_address(
        "DUPLEX PR8735 106 Redbank Plains Sienna Eden Estate 2026-09-01 00:00:00 505 "
        "$595,000 $732,285 732285")
    for gone in ("$595,000", "$732,285", "732285", "2026-09-01", "00:00:00"):
        assert gone not in got, f"{gone!r} should have been removed, got {got!r}"
    assert "Redbank Plains" in got and "Sienna Eden Estate" in got
    assert "505" in got, "a small number may be part of the address and must survive"


def test_the_address_label_leaves_bed_and_bath_counts_alone():
    """Matching against sibling columns turned '2 Bed 2 Bath' into 'Bed Bath'."""
    from address_label import clean_display_address
    row = {"bedrooms": 2, "bathrooms": 2, "car_spaces": 1, "land_size_sqm": 2}
    got = clean_display_address(
        "Available V1509 2 Bed 2 Bath 1 in stage 2 South West Sky Garden $ 671,000", row)
    assert "2 Bed 2 Bath" in got, got
    assert "in stage 2" in got, got
    assert "$" not in got


def test_the_address_label_strips_title_dates_and_quarters():
    from address_label import clean_display_address
    got = clean_display_address("2-Part Townhome North 25 Havenwood Mernda Q4 2026 $307,000")
    assert "Q4 2026" not in got and "$307,000" not in got
    assert "Havenwood Mernda" in got
    got2 = clean_display_address("SS West 2103 Seventh Bend Weir Views Sep-26 4 / 2 / 2")
    assert "Sep-26" not in got2 and "4 / 2 / 2" not in got2
    assert "Seventh Bend" in got2


def test_the_address_label_keeps_an_ordinary_address_untouched():
    from address_label import clean_display_address
    for plain in ("Lot 1408, Regent Quarter", "12 Coledale Drive, Melton",
                  "Lot 97 Sunnyvue Estate", "Unit 3/45 May Street"):
        assert clean_display_address(plain) == plain, plain


def test_the_address_label_never_returns_empty():
    """A cluttered address beats a blank one on a client-facing card."""
    from address_label import clean_display_address
    for hopeless in ("$595,000 2026-09-01 00:00:00 732285", "4 / 2 / 2", "$1,000"):
        assert clean_display_address(hopeless) == hopeless.strip(), hopeless
    assert clean_display_address("") == ""
    assert clean_display_address(None) == ""


# ------------------------------------------------------- recovered stocklist facts

def test_recovery_only_ever_fills_an_empty_field():
    """Strictly additive. A rule that is wrong can turn a blank into a wrong value; it
    must never turn a right value into a wrong one."""
    import stocklist_reparse
    cohort = stocklist_reparse.RULES[0]["cohort"]
    channel, builder = cohort.split("|", 1)
    row = {"source_channel": channel, "builder_name": None if builder == "?" else builder,
           "source_text": "Lot 82 Aberdeen 282 142.8 12.0 Sep-26 Empley 15 3x2x2 "
                          "$205,000 $335,220 $540,220 Available",
           "house_sqm": 999.0, "land_sqm": 111.0, "bedrooms": 9}
    got = stocklist_reparse.recover(row)
    for field in ("house_sqm", "land_sqm", "bedrooms"):
        assert field not in got, f"{field} was already stored and must not be recovered"


def test_recovery_drops_a_value_outside_the_plausible_range():
    """A parse error wearing a number is still a parse error."""
    import stocklist_reparse
    assert stocklist_reparse.in_range("house_sqm", 180) is True
    assert stocklist_reparse.in_range("house_sqm", 3) is False
    assert stocklist_reparse.in_range("house_sqm", 5000) is False
    assert stocklist_reparse.in_range("bedrooms", 4) is True
    assert stocklist_reparse.in_range("bedrooms", 44) is False
    assert stocklist_reparse.in_range("land_sqm", 400) is True
    assert stocklist_reparse.in_range("land_sqm", 12) is False


def test_recovery_survives_a_row_with_no_source_text():
    import stocklist_reparse
    assert stocklist_reparse.recover({"source_channel": "E-Agent",
                                      "builder_name": "Hudson Homes"}) == {}
    assert stocklist_reparse.recover({}) == {}


def test_every_shipped_rule_compiles_and_captures_a_named_group():
    """A rule that cannot compile is silently skipped at runtime, so catch it here."""
    import re
    import stocklist_reparse
    for rule in stocklist_reparse.RULES:
        pattern = re.compile(rule["pattern"], re.IGNORECASE)   # raises if malformed
        assert "v" in pattern.groupindex, f"{rule['cohort']}/{rule['field']} has no (?P<v>)"
        assert rule["transform"] in stocklist_reparse._TRANSFORMS


def test_the_display_address_prefers_the_recovered_street():
    import os, sys
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path in (here, os.path.join(here, "api")):
        if path not in sys.path:
            sys.path.insert(0, path)
    import _candidates
    jumbled = ("DUPLEX PR8735 106 Redbank Plains Sienna Eden Estate 2026-09-01 00:00:00 "
               "505 $595,000 $732,285 732285")
    assert _candidates._display_address(
        {"street_address": "12 Coledale Drive", "lot_number": "97",
         "lot_address": jumbled}) == "Lot 97, 12 Coledale Drive"
    # already carries its own number — do not prepend a second one
    assert _candidates._display_address(
        {"street_address": "Lot 5, 12 Coledale Drive", "lot_number": "5",
         "lot_address": jumbled}) == "Lot 5, 12 Coledale Drive"
    # nothing recovered: fall back to the cleaned raw address, never to nothing
    fallback = _candidates._display_address({"lot_address": jumbled})
    assert "$595,000" not in fallback and "Redbank Plains" in fallback

if __name__ == "__main__":
    sys.exit(1 if run_all() else 0)
