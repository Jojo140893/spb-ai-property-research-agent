"""
Custom Test Runner executing Phase 0 defect tests.
"""

import sys
from tests.test_defects import (
    test_defect_1_builder_confidence,
    test_defect_2_csv_parsing,
    test_defect_3_config_paths,
    test_defect_4_house_size_minimum,
    test_defect_5_verification_defaults
)
from tests.test_intended_features import (
    test_geo_index_loads_and_locates,
    test_distance_search_expands_suburbs,
    test_brief_parser_reads_radius,
    test_benchmark_classifies_against_comparables,
    test_no_sample_data_ships,
    test_benchmark_never_invents_data,
    test_pipeline_end_to_end_with_radius_and_report,
    test_live_sources_return_nothing_without_credentials
)
from tests.test_vendor_pipeline import (
    test_importer_parses_messy_multisection_csv,
    test_website_scraper_downloads_and_dedupes
)
from tests.test_harvest_buildings import _run_without_pytest as test_harvest_buildings
from tests.test_identity import run_all as _identity_suite
from tests.test_feature_extract import run_all as _features_suite
from tests.test_upsert import run_all as _upsert_suite
from tests.test_price_parsing import run_all as _prices_suite
from tests.test_row_links import run_all as _links_suite
from tests.test_remote_stocklist import run_all as _remote_suite
from tests.test_builder_attribution import run_all as _attribution_suite
from tests.test_state_resolver import run_all as _state_suite
from tests.test_bed_bath_car import run_all as _specs_suite
from tests.test_adaptive_extract import test_adaptive_extracts_from_unknown_layouts


def run_all_tests():
    print("=" * 60)
    print("      RUNNING PHASE 0 DEFECT REMEDIATION TEST SUITE")
    print("=" * 60)

    tests = [
        ("Defect #1: Builder Confidence Model", test_defect_1_builder_confidence),
        ("Defect #2: Primary Builder CSV Isolation", test_defect_2_csv_parsing),
        ("Defect #3: Relative Config Paths", test_defect_3_config_paths),
        ("Defect #4: Mandatory House Size Check", test_defect_4_house_size_minimum),
        ("Defect #5: Pending Verification Defaults", test_defect_5_verification_defaults),
        ("Feature: Geo index loads & locates", test_geo_index_loads_and_locates),
        ("Feature: Distance search expands suburbs", test_distance_search_expands_suburbs),
        ("Feature: Brief parser reads radius", test_brief_parser_reads_radius),
        ("Feature: Benchmark classifies vs comparables", test_benchmark_classifies_against_comparables),
        ("Feature: No sample market data ships", test_no_sample_data_ships),
        ("Feature: Benchmark never invents data", test_benchmark_never_invents_data),
        ("Feature: Pipeline E2E radius + report + coverage", test_pipeline_end_to_end_with_radius_and_report),
        ("Feature: Live sources return [] without creds", test_live_sources_return_nothing_without_credentials),
        ("Vendor: importer parses messy multi-section CSV", test_importer_parses_messy_multisection_csv),
        ("Vendor: website scraper downloads + dedupes", test_website_scraper_downloads_and_dedupes),
        ("Buildings: harvest runner stores + dedupes", test_harvest_buildings),
        ("Adaptive: extracts listings from unknown layouts", test_adaptive_extracts_from_unknown_layouts),
        ("Identity: content_hash + column spec", lambda: (_ for _ in ()).throw(AssertionError("identity suite failed")) if _identity_suite() else None),
        ("Features: availability/storey/lot/incentives", lambda: (_ for _ in ()).throw(AssertionError("features suite failed")) if _features_suite() else None),
        ("Upsert: idempotent re-harvest, no data loss", lambda: (_ for _ in ()).throw(AssertionError("upsert suite failed")) if _upsert_suite() else None),
        ("Prices: rent/yield, spaced numbers, title dates", lambda: (_ for _ in ()).throw(AssertionError("prices suite failed")) if _prices_suite() else None),
        ("Links: per-lot pdf/floorplan + address label", lambda: (_ for _ in ()).throw(AssertionError("links suite failed")) if _links_suite() else None),
        ("Remote: off-site stocklist hosts", lambda: (_ for _ in ()).throw(AssertionError("remote suite failed")) if _remote_suite() else None),
        ("Attribution: builder vs estate, one tab one file", lambda: (_ for _ in ()).throw(AssertionError("attribution suite failed")) if _attribution_suite() else None),
        ("State: postcode / suburb / page resolution", lambda: (_ for _ in ()).throw(AssertionError("state suite failed")) if _state_suite() else None),
        ("Specs: bed / bath / car notations", lambda: (_ for _ in ()).throw(AssertionError("specs suite failed")) if _specs_suite() else None),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        try:
            test_fn()
            print(f" [PASS] {name}")
            passed += 1
        except Exception as e:
            print(f" [FAIL] {name}: {e}")
            failed += 1

    print("=" * 60)
    print(f"RESULT: {passed} PASSED, {failed} FAILED.")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
