"""
Test suite for the 2026-07-22 client-review features:
distance search, market benchmarking, client report, builder coverage.
Zero-dependency standard assertions (run via run_tests.py or pytest).
"""

from geo import SuburbGeoIndex, haversine_km
from benchmark import BenchmarkEngine
from brief_parser import ClientBriefParser
from client_report import ClientReportGenerator
from kommo_agent import KommoPropertyResearchAgent

_GEO = SuburbGeoIndex()
_BRIEF = {
    'client_name': 'Test Client', 'budget_max': 780000, 'preferred_spending_cap': 740000,
    'buyer_type': 'Owner Occupier', 'state': 'QLD',
    'primary_suburbs': ['Coomera'], 'bedrooms_min': 4, 'bathrooms_min': 2,
    'car_spaces_min': 2, 'storeys_max': 1, 'land_size_min_sqm': 350,
    'house_size_min_sqm': 175, 'search_radius_km': 15,
}


def test_geo_index_loads_and_locates():
    assert _GEO.loaded, "au_suburbs.csv must load"
    loc = _GEO.locate('Springfield', 'QLD')
    assert loc is not None, "Springfield QLD must geocode"
    assert -29 < loc[0] < -26 and 151 < loc[1] < 154, f"Springfield coords implausible: {loc}"


def test_distance_search_expands_suburbs():
    area = _GEO.expand_search_suburbs(['Coomera'], 'QLD', 10)
    names = [a['suburb'].lower() for a in area]
    assert 'coomera' in names, "origin suburb must be in the search area"
    assert len(area) > 3, f"10km around Coomera must include neighbours, got {len(area)}"
    assert 'pimpama' in names, "Pimpama is ~6km from Coomera and must be inside a 10km radius"
    assert all(a['distance_km'] <= 10 for a in area), "no suburb may exceed the radius"
    # radius 0 / None -> only the primary suburbs
    exact = _GEO.expand_search_suburbs(['Coomera'], 'QLD', None)
    assert len(exact) == 1


def test_brief_parser_reads_radius():
    brief = ClientBriefParser.parse_dict(_BRIEF)
    assert brief.search_radius_km == 15.0


def test_benchmark_classifies_against_comparables():
    eng = BenchmarkEngine(_GEO)
    assert eng.comparables, "sample comparables must load when drive_input has none"
    # Coomera sample comps average ~$743k; $725k realistic is competitive/below
    res = eng.benchmark_package('Coomera', 'QLD', 4, 725000)
    assert res['benchmarked'] is True
    assert res['avg_market_price'] and res['variance_pct'] is not None
    assert res['classification'] in ('Below Market Value', 'Competitive Market Value')
    # A wildly overpriced package must classify as Poor Value
    poor = eng.benchmark_package('Coomera', 'QLD', 4, 900000)
    assert poor['classification'] == 'Poor Value'
    # Sample data must be flagged for manual re-benchmark
    assert res['needs_manual_benchmark'] is True and 'SAMPLE' in res['data_note']


def test_benchmark_never_invents_data():
    eng = BenchmarkEngine(_GEO)
    res = eng.benchmark_package('Broome', 'WA', 4, 700000)
    assert res['benchmarked'] is False
    assert res['classification'] == 'Unbenchmarked - Pending Market Data'
    assert res['avg_market_price'] is None and res['needs_manual_benchmark'] is True


def test_pipeline_end_to_end_with_radius_and_report():
    agent = KommoPropertyResearchAgent()
    result = agent.run_property_research(dict(_BRIEF))
    assert result['search_area'], "search area must be populated"
    assert len(result['search_area']) > 1, "15km radius must expand beyond Coomera"
    cov = result['builder_coverage']
    assert cov['total_in_directory'] >= 25 and cov['in_scope_for_state'] > 0
    assert result['shortlist_count'] > 0, "simulated sources must yield a shortlist"
    top = result['shortlist'][0]
    assert top.benchmark is not None, "every candidate must carry a Step 7 benchmark"
    assert top.distance_km_from_target is not None
    # Client report requirements: price vs market average, rent, yield, disclaimer
    html = result['client_report_html']
    assert 'average market price' in html or 'market benchmark pending' in html
    assert 'Estimated rent' in html and 'gross yield' in html
    assert 'independent verification' in html
    assert result['client_report_path'], "report must be written to output/"


def run_all():
    tests = [
        ('Geo index loads & locates', test_geo_index_loads_and_locates),
        ('Distance search expands suburbs', test_distance_search_expands_suburbs),
        ('Brief parser reads radius', test_brief_parser_reads_radius),
        ('Benchmark classifies vs comparables', test_benchmark_classifies_against_comparables),
        ('Benchmark never invents data', test_benchmark_never_invents_data),
        ('Pipeline E2E: radius + report + coverage', test_pipeline_end_to_end_with_radius_and_report),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] {name}: {e}")
    return failed


if __name__ == '__main__':
    import sys
    sys.exit(1 if run_all() else 0)
