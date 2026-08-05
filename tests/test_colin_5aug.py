"""
The defects Colin raised on the call of 5 August 2026, each pinned so it cannot return.

The one that mattered most he found by knowing his own market: a Rouse Hill lot priced
at about $500,000, which he said was impossible. It was — the package cost $932,900 and
we were publishing a component of it.
"""

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(_HERE)
for _p in (_APP, os.path.join(_APP, "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from builder_names import (BuilderNameCanonicaliser, CLIENT_CONFIRMED_ALIASES,  # noqa: E402
                          is_not_a_builder_name)
from provenance import primary_source_link, source_links                        # noqa: E402
from sources.scraper_base import normalise_money_spacing, parse_price           # noqa: E402


# ------------------------------------------------- understated package prices

REAL_ROW = ("5 Arbourwood Morayfield Cherry 149 - Coastal Available 4 2 1 272 "
            "$ 397,900 $ 535,000 $ 9 32,900 Split Registered")


def test_a_money_amount_split_by_a_space_is_rejoined():
    """The bug that understated 113 listings by roughly $400,000 each."""
    fixed = normalise_money_spacing(REAL_ROW)
    assert "$932,900" in fixed, fixed
    # and the two intact amounts beside it are untouched
    assert "$ 397,900" in fixed and "$ 535,000" in fixed


def test_the_package_total_is_the_largest_amount_once_rejoined():
    """Before the fix the broken total parsed as $9, was dropped as implausible, and
    max() of the survivors returned the BUILD component as the package price."""
    amounts = [float(a.replace("$", "").replace(",", "").strip())
               for a in re.findall(r"\$\s?\d[\d,]*", normalise_money_spacing(REAL_ROW))]
    plausible = [a for a in amounts if a >= 50_000]
    assert max(plausible) == 932900.0, plausible
    assert 535000.0 in plausible, "the component must still be visible, just not the total"


def test_two_separate_amounts_are_never_joined_together():
    """The guard that makes this safe: the second half must carry a thousands comma, so
    "$ 397,900 $ 535,000" cannot collapse into one number."""
    assert normalise_money_spacing("$ 397,900 $ 535,000") == "$ 397,900 $ 535,000"
    assert normalise_money_spacing("Available 4 2 1 272 $ 397,900") == \
        "Available 4 2 1 272 $ 397,900"


def test_a_price_below_the_floor_is_still_refused():
    """Unchanged by the rejoin work: $7 from a marketing paragraph is not a price."""
    assert parse_price("$7") is None
    assert parse_price("$149") is None
    assert parse_price("$535,000") == 535000.0


# ------------------------------------------------- availability

def test_not_available_is_excluded_from_recommendations():
    """Colin: "it's not available, but it came as available from your end". The literal
    string "Not Available" sits on 581 rows and was missing from the exclusion set."""
    import _candidates
    assert "not available" in _candidates.NOT_AVAILABLE
    for value in ("Not Available", "NOT AVAILABLE", "  not available  "):
        assert value.strip().lower() in _candidates.NOT_AVAILABLE, value


def test_a_recommendation_requires_a_positive_availability_signal():
    """"Unstated" is not "available" — 1,462 rows record nothing, and recommending one
    risks putting a sold house in front of a buyer."""
    import _candidates

    def row(lot, **over):
        # Distinct lots, or the same-listing collapse merges them and the count under
        # test becomes the dedupe's count instead of the availability gate's.
        base = {"builder_name": "Testco", "lot_address": "Lot %s" % lot,
                "lot_number": str(lot), "suburb": "Tarneit",
                "state": "VIC", "price": 700000.0, "bedrooms": 4, "bathrooms": 2,
                "car_spaces": 2, "house_sqm": 180.0, "land_sqm": 400.0,
                "source_channel": "Test", "availability_status": "Available"}
        base.update(over)
        return base

    brief = {"state": "VIC", "budget_max": 800000, "preferred_spending_cap": 780000,
             "bedrooms_min": 4, "bathrooms_min": 2, "car_spaces_min": 2}
    rows = [row(1), row(2, availability_status="Not Available"),
            row(3, availability_status=""), row(4, availability_status="For Sale")]
    packages, counts = _candidates.build_packages(brief, rows)
    assert len(packages) == 2, "only Available and For Sale may be recommended"
    assert counts["not_available"] == 1
    assert counts["availability_unstated"] == 1
    text = _candidates.coverage_sentence(counts, "stock.json", "VIC")
    assert "1 whose availability the source never stated" in text, text


# ------------------------------------------------- Level 33 / builder names

def test_a_floor_is_not_a_builder():
    """Proxima's project header put "Level 33" in the developer field and 318 listings
    were stored under a builder that does not exist."""
    for place in ("Level 33", "Level 4", "LVL 2", "Floor 12", "Stage 2", "Tower A",
                  "Building B", "Unit 5", "Precinct 3", "Release 1"):
        assert is_not_a_builder_name(place), place


def test_a_company_whose_name_contains_such_a_word_is_kept():
    """The guard must not eat real builders."""
    for company in ("Level Homes", "Stage Constructions", "Tower Living Group",
                    "Atchison and Kenny", "Buildcorp", "Unity Homes"):
        assert not is_not_a_builder_name(company), company


def test_level_33_resolves_to_the_builder_the_client_named():
    """Colin: "the builder is called Atchison and Kenny" — corroborated by the project
    title on all 318 rows, so this is a recorded client decision, not an inference."""
    canon = BuilderNameCanonicaliser(["Atchison and Kenny"])
    assert canon.canonical("Level 33") == "Atchison and Kenny"
    assert CLIENT_CONFIRMED_ALIASES["level33"] == "Atchison and Kenny"


# ------------------------------------------------- provenance links

def test_proxima_links_to_its_project_page_when_the_id_is_known():
    link = primary_source_link({"source_channel": "Proxima", "source_project_id": "305",
                                "source_url": "https://portal.proxima.com.au/agent/projects/index/"})
    assert link and link["opens"] == "project"
    assert link["url"].endswith("/305"), link


def test_proxima_without_a_project_id_says_so_rather_than_promising_the_lot():
    link = primary_source_link({"source_channel": "Proxima", "source_project_id": "",
                                "source_url": "https://portal.proxima.com.au/agent/projects/index/"})
    assert link and link["opens"] == "project"
    assert "projects list" in link["label"].lower(), link


def test_e_agent_offers_the_price_list_because_no_lot_page_exists():
    link = primary_source_link({
        "source_channel": "E-Agent",
        "stocklist_file": "https://www.e-agent.com.au/_files/ugd/069fe0_x.xlsx?dn=VIC.xlsx",
        "source_url": "https://www.e-agent.com.au/_files/ugd/069fe0_x.xlsx?dn=VIC.xlsx"})
    assert link and link["opens"] == "price list", link


def test_a_per_lot_document_outranks_the_price_list():
    link = primary_source_link({
        "source_channel": "E-Agent",
        "listing_url": "https://www.dropbox.com/scl/fi/x/CC-0122.pdf",
        "stocklist_file": "https://www.e-agent.com.au/_files/ugd/069fe0_x.xlsx"})
    assert link["opens"] == "lot", link


def test_an_emailed_price_list_becomes_a_search_that_finds_the_email():
    link = primary_source_link({"source_channel": "digital email",
                                "source_url": "email:Fw: H&L Current Availability - 28th of July"})
    assert link and link["opens"] == "email"
    assert link["url"].startswith("https://mail.google.com/"), link


def test_a_row_with_nothing_to_point_at_gets_no_link_rather_than_a_guess():
    assert primary_source_link({"source_channel": "E-Agent"}) is None
    assert source_links({}) == []


def test_a_non_http_reference_is_never_offered_as_a_link():
    """source_url on stored rows can be prose — "stored stock, captured 04/08/2026"."""
    assert primary_source_link({"source_channel": "E-Agent",
                                "source_url": "stored stock, captured 04/08/2026"}) is None


def run_all():
    tests = [
        ("split money amount rejoined", test_a_money_amount_split_by_a_space_is_rejoined),
        ("package total wins once rejoined",
         test_the_package_total_is_the_largest_amount_once_rejoined),
        ("two amounts never joined", test_two_separate_amounts_are_never_joined_together),
        ("price floor still refused", test_a_price_below_the_floor_is_still_refused),
        ("Not Available excluded", test_not_available_is_excluded_from_recommendations),
        ("recommendation needs a positive signal",
         test_a_recommendation_requires_a_positive_availability_signal),
        ("a floor is not a builder", test_a_floor_is_not_a_builder),
        ("real company with such a word kept",
         test_a_company_whose_name_contains_such_a_word_is_kept),
        ("Level 33 -> Atchison and Kenny", test_level_33_resolves_to_the_builder_the_client_named),
        ("Proxima links to its project", test_proxima_links_to_its_project_page_when_the_id_is_known),
        ("Proxima without an id is honest",
         test_proxima_without_a_project_id_says_so_rather_than_promising_the_lot),
        ("E-Agent offers the price list", test_e_agent_offers_the_price_list_because_no_lot_page_exists),
        ("a lot document outranks the price list", test_a_per_lot_document_outranks_the_price_list),
        ("an email becomes a findable search",
         test_an_emailed_price_list_becomes_a_search_that_finds_the_email),
        ("nothing to point at -> no link",
         test_a_row_with_nothing_to_point_at_gets_no_link_rather_than_a_guess),
        ("prose is never a link", test_a_non_http_reference_is_never_offered_as_a_link),
        ("comparison has every section", test_the_comparison_has_every_section_colins_report_has),
        ("cheaper quote != cheaper package", test_the_cheaper_quote_is_not_called_the_cheaper_package),
        ("no invented lead on equal scores", test_it_refuses_to_invent_a_lead_when_the_scores_are_equal),
        ("same builder still tellable apart", test_two_lots_from_one_builder_are_still_tellable_apart),
        ("an unstated row is omitted", test_a_field_neither_side_states_is_omitted_not_shown_blank),
        ("one or three properties refused", test_a_comparison_of_one_or_three_is_refused),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] 5-aug: {name}")
        except Exception as exc:                                        # noqa: BLE001
            failed += 1
            print(f" [FAIL] 5-aug: {name}: {exc}")
    return failed



# ------------------------------------------------- the two-option comparison report

def _pair():
    """Two candidates that differ in the ways the report is supposed to surface."""
    from schema import (CandidateProperty, PriceBreakdown, TurnkeyStatus,
                        VerificationStatus, ScoringBreakdown)

    def prop(pid, builder, pkg, extra, house, missing, score):
        pb = PriceBreakdown(
            advertised_package_price=pkg, land_price=0, build_price=0, fixed_site_costs=0,
            driveway_cost=0, fencing_cost=0, landscaping_cost=0, flooring_cost=0,
            blinds_cost=0, hvac_cost=0, estimated_additional_costs=extra,
            realistic_total_price=pkg + extra,
            turnkey_status=TurnkeyStatus.PARTIAL_TURNKEY if extra else TurnkeyStatus.FULL_TURNKEY,
            missing_inclusions=missing)
        p = CandidateProperty(
            property_id=pid, lot_address=f"Lot {pid}", suburb="Tarneit", state="VIC",
            builder_name=builder, developer_name="", house_design="Aspen 22",
            bedrooms=4, bathrooms=2, car_spaces=2, storeys=None, land_size_sqm=400.0,
            house_size_sqm=house, title_status="Titled", expected_title_date="",
            price_breakdown=pb, estimated_rent_weekly_min=0, estimated_rent_weekly_max=0,
            amenities_summary="", builder_confidence_rating="HIGH",
            source_channel="Test", source_url_or_ref="https://example.test/1",
            date_checked="06/08/2026", verification_status=VerificationStatus.VERIFIED)
        p.scoring = ScoringBreakdown(20, 20, 15, 15, 10, 10, 10, score)
        return p

    a = prop("A", "Alpha Homes", 700000, 0, 180.0, [], 92.0)
    b = prop("B", "Beta Homes", 690000, 35000, 165.0, ["Fencing", "Landscaping"], 88.0)
    return a, b


def _brief():
    from brief_parser import ClientBriefParser
    return ClientBriefParser.parse_dict({"client_name": "Test Buyer", "state": "VIC",
                                         "budget_max": 800000})


def test_the_comparison_has_every_section_colins_report_has():
    from comparison_report import ComparisonReportGenerator
    a, b = _pair()
    html = ComparisonReportGenerator.generate_html(_brief(), [a, b])
    for section in ("Builder comparison report", "1. Headline comparison",
                    "2. What each one gives you", "3. Inclusions still to be arranged",
                    "4. Cost and completion", "5. Things to be aware of"):
        assert section in html, section
    assert "Alpha Homes vs Beta Homes" in html


def test_the_cheaper_quote_is_not_called_the_cheaper_package():
    """The whole point of Colin's section 4: Beta's quote is $10,000 lower but its
    COMPLETED position is $25,000 higher, and the report must lead with the latter."""
    from comparison_report import ComparisonReportGenerator
    a, b = _pair()
    html = ComparisonReportGenerator.generate_html(_brief(), [a, b])
    assert "$700,000" in html and "$690,000" in html      # both quotes shown
    assert "$725,000" in html                              # Beta's completed position
    assert "Alpha Homes</strong> lands $25,000 lower" in html, (
        "the verdict must compare completed positions, not quoted prices")


def test_it_refuses_to_invent_a_lead_when_the_scores_are_equal():
    from comparison_report import ComparisonReportGenerator
    a, b = _pair()
    b.scoring.total_score = a.scoring.total_score
    html = ComparisonReportGenerator.generate_html(_brief(), [a, b])
    assert "scores higher" not in html
    assert "both score" in html


def test_two_lots_from_one_builder_are_still_tellable_apart():
    """The first pair the tool ever produced was two lots from one builder, and it
    rendered as "Verv Projects vs Verv Projects" with two identical column heads."""
    from comparison_report import ComparisonReportGenerator
    a, b = _pair()
    b.builder_name = a.builder_name
    html = ComparisonReportGenerator.generate_html(_brief(), [a, b])
    assert "Alpha Homes vs Alpha Homes" not in html
    assert "two options compared" in html
    assert "Option 1" in html and "Option 2" in html


def test_a_field_neither_side_states_is_omitted_not_shown_blank():
    from comparison_report import ComparisonReportGenerator
    a, b = _pair()
    a.storeys = b.storeys = None
    html = ComparisonReportGenerator.generate_html(_brief(), [a, b])
    assert "<th>Storeys</th>" not in html, "a row neither side states adds nothing"


def test_a_comparison_of_one_or_three_is_refused():
    from comparison_report import ComparisonReportGenerator
    a, b = _pair()
    for bad in ([a], [a, b, a], []):
        try:
            ComparisonReportGenerator.generate_html(_brief(), bad)
        except ValueError:
            continue
        raise AssertionError(f"should have refused {len(bad)} properties")

if __name__ == "__main__":
    sys.exit(1 if run_all() else 0)
