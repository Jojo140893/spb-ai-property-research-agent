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
