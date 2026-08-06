"""
Tests for supersede_duplicates.py.

12% of the table (777 rows) turned out to be older captures of a lot already stored
fresher, because content_hash includes fields the extractor can change between runs.
The guards here are about not over-reaching: collapsing two genuinely different
packages, or picking a different winner on each run, would both be worse than the
duplicates.
"""

from supersede_duplicates import find_superseded, lot_key


def _row(i, price, date, suburb="Sampleton", addr="lot 9, example road",
         channel="E-Agent", beds=4, baths=2, cars=2, house=170.0, land=400.0,
         state="QLD", last_seen=None):
    return {"id": i, "price": price, "date_checked": date, "suburb": suburb,
            "lot_address": addr, "source_channel": channel, "bedrooms": beds,
            "bathrooms": baths, "car_spaces": cars, "house_sqm": house,
            "land_sqm": land, "state": state, "last_seen": last_seen or date,
            "content_hash": f"v2:hash{i}"}


_LINE = ("68 Amory Ripley Tallow 170 - Urban Available 4 2 2 455 "
         "$ 407,900 $ 582,000 $ 9 89,900 Split Registered")


def test_the_same_source_line_is_one_listing():
    """1,038 surplus rows survived because the SPEC was unstable, not the listing.

    lot_key needs an identical spec, and the extractor recorded land_sqm as NULL on one
    harvest and 455.0 on the next, and lot_number as NULL then '68'. Seven captures of
    this one line therefore formed seven groups of one, and the stock table showed the
    listing five times. The line itself never changed.
    """
    rows = [
        # Same line, but each capture disagrees about what it managed to extract.
        dict(_row(1, 989_900, "29/07/2026", land=None, house=None), source_text=_LINE),
        dict(_row(2, 989_900, "04/08/2026", land=455.0, house=None), source_text=_LINE),
        dict(_row(3, 989_900, "05/08/2026", land=455.0, house=170.0), source_text=_LINE),
        # The stocklist reformatted the split money; it is still the same line.
        dict(_row(4, 989_900, "06/08/2026", land=455.0, house=170.0),
             source_text=_LINE.replace("$ 9 89,900", "$989,900")),
    ]
    pairs = find_superseded(rows)
    assert len(pairs) == 3, [(l["id"], w["id"]) for l, w in pairs]
    assert {l["id"] for l, _ in pairs} == {1, 2, 3}
    assert {w["id"] for _, w in pairs} == {4}, "the freshest capture is the survivor"


def test_two_captures_that_disagree_on_price_are_left_alone():
    """Identical in every field but the price is TWO PACKAGES, by this repo's own rule.

    database.building_content_hash appends variant_ordinal precisely so that such
    siblings stop replacing each other. Collapsing them here would undo that, so the
    price is part of the key: 11 real groups differ only on price and none is touched.
    """
    rows = [
        dict(_row(1, 730_000, "03/08/2026"), source_text="lot 243 unit b-1602, 30 ellen st"),
        dict(_row(2, 745_000, "03/08/2026"), source_text="lot 243 unit b-1602, 30 ellen st"),
    ]
    # Differing land/house keeps lot_key apart too, so only text_key could merge them.
    rows[0]["land_sqm"], rows[1]["land_sqm"] = 400.0, 410.0
    assert find_superseded(rows) == []


def test_the_freshest_capture_wins():
    rows = [_row(1, 962_351, "27/07/2026"),
            _row(2, 1_058_877, "03/08/2026")]
    pairs = find_superseded(rows)
    assert len(pairs) == 1
    loser, winner = pairs[0]
    assert loser["id"] == 1 and winner["id"] == 2
    assert winner["price"] == 1_058_877, "the current price must be the surviving one"


def test_the_stale_cheaper_row_is_the_one_superseded():
    """The whole point. 199 rows were cheaper than the capture that replaced them,
    and every one would have read as a deal."""
    rows = [_row(1, 500_000, "27/07/2026"), _row(2, 600_000, "03/08/2026")]
    (loser, winner), = find_superseded(rows)
    assert loser["price"] < winner["price"]
    assert loser["id"] == 1, "the older row is superseded even though it is cheaper"


def test_three_captures_leave_exactly_one_live():
    rows = [_row(1, 962_351, "27/07/2026", channel="Direct"),
            _row(2, 1_058_877, "29/07/2026", channel="E-Agent"),
            _row(3, 1_058_877, "03/08/2026", channel="Direct")]
    pairs = find_superseded(rows)
    assert len(pairs) == 2
    assert {l["id"] for l, _w in pairs} == {1, 2}
    assert {w["id"] for _l, w in pairs} == {3}


def test_a_different_spec_is_a_different_package_and_is_left_alone():
    """Two packages on one lot are not duplicates — 36 groups looked like this."""
    rows = [_row(1, 1_194_221, "27/07/2026", house=174.0),
            _row(2, 1_287_901, "03/08/2026", house=232.0)]
    assert find_superseded(rows) == []


def test_different_lots_are_never_collapsed():
    rows = [_row(1, 500_000, "01/08/2026", addr="lot 9, example road"),
            _row(2, 500_000, "01/08/2026", addr="lot 10, example road")]
    assert find_superseded(rows) == []


def test_the_same_address_in_a_different_suburb_is_a_different_lot():
    rows = [_row(1, 500_000, "01/08/2026", suburb="Sampleton"),
            _row(2, 500_000, "01/08/2026", suburb="Testvale")]
    assert find_superseded(rows) == []


def test_a_row_with_no_locality_is_never_superseded():
    """Spec alone is not an identity: without a suburb or an address label two rows
    cannot be shown to be the same lot, so neither is touched."""
    assert lot_key(_row(1, 1.0, "01/08/2026", suburb="")) is None
    assert lot_key(_row(2, 1.0, "01/08/2026", addr="")) is None
    rows = [_row(1, 500_000, "01/08/2026", suburb=""),
            _row(2, 600_000, "02/08/2026", suburb="")]
    assert find_superseded(rows) == []


def test_the_winner_is_deterministic_when_dates_tie():
    """A run that picked differently each time would move Colin's marked selection
    around underneath him."""
    rows = [_row(1, 500_000, "01/08/2026"), _row(2, 600_000, "01/08/2026")]
    first = find_superseded(rows)
    second = find_superseded(list(reversed(rows)))
    assert first[0][1]["id"] == second[0][1]["id"] == 2


def test_an_unparseable_date_does_not_win_by_accident():
    """A row whose date cannot be read must not outrank a row with a real one."""
    rows = [_row(1, 500_000, "not a date"), _row(2, 600_000, "01/08/2026")]
    (loser, winner), = find_superseded(rows)
    assert winner["id"] == 2, "a dated capture should beat an undated one"


def test_a_superseded_capture_is_never_shortlisted():
    """The one place a stale price actually reaches a client is the shortlist.

    Superseded rows were hidden in the dashboard from the day they were identified, but
    the recommendation engine kept scoring all 979 of them — so the flag was honoured
    everywhere it was cosmetic and ignored in the one place it mattered.
    """
    import os, sys
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path in (here, os.path.join(here, "api")):
        if path not in sys.path:
            sys.path.insert(0, path)
    import _candidates

    def row(**over):
        base = {"builder_name": "Testco Homes", "lot_address": "Lot 9", "suburb": "Tarneit",
                "state": "VIC", "availability_status": "Available", "price": 700000.0,
                "bedrooms": 4, "bathrooms": 2, "car_spaces": 2, "house_sqm": 180.0,
                "land_size_sqm": 400.0, "source_channel": "Test",
                "source_url_or_ref": "https://example.test/9", "superseded_by": None}
        base.update(over)
        return base

    brief = {"state": "VIC", "budget_max": 800000, "preferred_spending_cap": 780000,
             "bedrooms_min": 4, "bathrooms_min": 2, "car_spaces_min": 2}

    fresh, stale = row(price=700000.0), row(price=640000.0, superseded_by="v2:whatever")
    packages, counts = _candidates.build_packages(brief, [fresh, stale])
    assert counts["superseded"] == 1, "the stale capture must be counted as skipped"
    assert len(packages) == 1, f"only the fresh capture may be scored, got {len(packages)}"
    assert packages[0]["advertised_package_price"] == 700000.0, (
        "the cheaper price came from the stale capture and must not win the shortlist")
    text = _candidates.coverage_sentence(counts, "stock.json", "VIC")
    assert "1 superseded by a fresher capture" in text, text


def test_the_superseded_flag_is_read_from_both_snapshot_readers():
    """SNAPSHOT_FIELDS drives the SQLite reader; omitting the flag there would mean the
    JSON reader filtered stale rows and the database reader silently did not."""
    import os, sys
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path in (here, os.path.join(here, "api")):
        if path not in sys.path:
            sys.path.insert(0, path)
    import _candidates
    assert "superseded_by" in _candidates.SNAPSHOT_FIELDS


def run_all():
    tests = [
        ("identical source line collapses", test_the_same_source_line_is_one_listing),
        ("a price difference is left alone", test_two_captures_that_disagree_on_price_are_left_alone),
        ("freshest capture wins", test_the_freshest_capture_wins),
        ("stale cheaper row is superseded", test_the_stale_cheaper_row_is_the_one_superseded),
        ("three captures leave one live", test_three_captures_leave_exactly_one_live),
        ("different spec left alone", test_a_different_spec_is_a_different_package_and_is_left_alone),
        ("different lots not collapsed", test_different_lots_are_never_collapsed),
        ("same address, other suburb", test_the_same_address_in_a_different_suburb_is_a_different_lot),
        ("no locality means no supersede", test_a_row_with_no_locality_is_never_superseded),
        ("winner is deterministic", test_the_winner_is_deterministic_when_dates_tie),
        ("unparseable date does not win", test_an_unparseable_date_does_not_win_by_accident),
        ("a superseded capture is never shortlisted", test_a_superseded_capture_is_never_shortlisted),
        ("both readers see the flag", test_the_superseded_flag_is_read_from_both_snapshot_readers),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] supersede: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] supersede: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
