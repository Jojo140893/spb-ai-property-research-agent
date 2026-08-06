"""
The re-parse rewrites stored rows from their source files, so its guards matter more
than its yield. Each one below was added after a dry run showed what it would otherwise
have written.
"""

from reparse_from_source import (_empty, _is_locality, _key, _postcode_fits,
                                 _replaceable_suburb)


def test_a_suburb_written_must_be_a_real_place():
    """The first dry run would have written 267 suburbs, 115 of them not places.

    "Dual Key" (38 rows), "COAST COUNCIL" (19), "Duplex" (15), "Land" (13). The banner
    reader refuses junk, but parse_fields can still put a product type in that column,
    so the value being WRITTEN is validated and not only the value being replaced.
    Swapping one non-place for another is churn, not a repair.
    """
    assert _is_locality("Wadalba", "NSW")
    assert _is_locality("Toowoomba", "QLD")
    for junk in ("Dual Key", "COAST COUNCIL", "Duplex", "Land", "One Part Contracts", ""):
        assert not _is_locality(junk, "NSW"), f"{junk!r} was accepted as a locality"
    # A real locality in the WRONG state is not a locality for this row.
    assert not _is_locality("Toowoomba", "VIC")


def test_only_a_non_place_may_be_overwritten():
    """Additive everywhere else, because a stored value may have been checked by a human.

    The suburb is the exception: 1,796 rows hold spreadsheet furniture rather than a
    blank, and treating those as values worth protecting protects nothing.
    """
    assert _replaceable_suburb("", "NSW"), "a blank is replaceable"
    assert _replaceable_suburb(None, "NSW")
    assert _replaceable_suburb("7 Star Energy Rating", "VIC"), "furniture is replaceable"
    assert _replaceable_suburb("COAST COUNCIL", "NSW")
    # A value that IS a place is left alone, even if the re-parse disagrees with it.
    assert not _replaceable_suburb("Wadalba", "NSW")
    assert not _replaceable_suburb("Austral", "NSW")


def test_a_postcode_must_belong_to_the_rows_own_state():
    """This column was found populated from lot numbers, unit numbers and floor areas —
    138 live rows carry one nothing supports, and on two the fake postcode had also set
    the wrong state. Writing more of those would deepen the problem."""
    assert _postcode_fits("2259", "NSW")
    assert _postcode_fits("4350", "QLD")
    assert _postcode_fits("2259", ""), "no state to contradict it"
    assert not _postcode_fits("2259", "QLD"), "a NSW postcode on a QLD row"
    assert not _postcode_fits("4350", "NSW")
    for junk in ("12", "202.15", "", None, "abcd"):
        assert not _postcode_fits(junk, "NSW"), f"{junk!r} was accepted as a postcode"


def test_rows_are_matched_on_a_key_that_survives_the_extractor_changes():
    """The join is the row's own line, normalised exactly as supersede_duplicates does —
    money spacing repaired and unit suffixes dropped, because a vendor re-exporting
    "269" as "269 m2" is the same line."""
    a = "Lot 82 Aberdeen 282 142.8 $ 9 32,900 Split"
    b = "Lot 82  Aberdeen  282  142.8 m2  $932,900  Split"
    assert _key(a) == _key(b), (_key(a), _key(b))
    assert _key("") == ""
    assert _key(None) == ""
    # Two genuinely different lines must not collide.
    assert _key("Lot 82 Aberdeen $932,900") != _key("Lot 83 Aberdeen $932,900")


def test_zero_is_missing_not_a_measurement():
    assert _empty(0) and _empty(None) and _empty("")
    assert not _empty(4) and not _empty("Austral")


def run_all():
    tests = [
        ("a written suburb must be a place", test_a_suburb_written_must_be_a_real_place),
        ("only a non-place is overwritten", test_only_a_non_place_may_be_overwritten),
        ("a postcode must fit the state", test_a_postcode_must_belong_to_the_rows_own_state),
        ("the match key survives reformatting",
         test_rows_are_matched_on_a_key_that_survives_the_extractor_changes),
        ("zero means missing", test_zero_is_missing_not_a_measurement),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] reparse: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] reparse: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
