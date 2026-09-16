"""
Tests for supersede_duplicates.py.

12% of the table (777 rows) turned out to be older captures of a lot already stored
fresher, because content_hash includes fields the extractor can change between runs.
The guards here are about not over-reaching: collapsing two genuinely different
packages, or picking a different winner on each run, would both be worse than the
duplicates.
"""

from supersede_duplicates import find_status_superseded, find_superseded, lot_key


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


def test_every_duplicate_found_is_actually_retired():
    """A duplicate that is IDENTIFIED and then dropped is worse than one never found.

    The three-pass rule first shipped as one winner per pass plus a loser->winner chain
    walk. Three passes disagree far more than two, the chains closed into cycles, and
    when a cycle led back to the row it started from the pair was silently discarded.
    Measured on the real 11,257 rows: 6,933 duplicates identified, 3,272 thrown away,
    5,116 retirements down to 3,661 -- a pass added to REMOVE double counting published
    1,455 MORE duplicates as sellable stock, and the run printed a smaller superseded
    figure as though the database had got cleaner.

    The suite did not catch it, because every existing test asserts on small hand-built
    groups where no cycle can form. This one asserts the invariant instead: whatever is
    grouped together must leave exactly one row live, and every other row must be
    retired against a survivor that is NOT itself retired.
    """
    # The construction matters, so it is spelled out. lot_key compares state, suburb,
    # address and the five spec fields; _detail ALSO counts postcode, estate_name and
    # builder_name, which lot_key ignores. So two captures can be identical to lot_key
    # while _detail ranks them differently -- and that is precisely when the freshness
    # pass and the cross-channel pass elect OPPOSITE winners and point at each other.
    rows = [
        # older, but the better-described capture: knows the postcode, estate and builder
        dict(_row(1, 989_900, "29/07/2026"), source_text=_LINE,
             postcode="4306", estate_name="Tallow", builder_name="Hattan Homes"),
        # newer, same lot and same line, arrived by email knowing much less
        dict(_row(2, 989_900, "06/08/2026", channel="digital email"), source_text=_LINE,
             postcode=None, estate_name=None, builder_name=None),
    ]
    pairs = find_superseded(rows)

    retired = {loser["id"] for loser, _ in pairs}
    survivors = {w["id"] for _, w in pairs}
    live = {r["id"] for r in rows} - retired

    assert len(live) == 1, f"two captures of one listing must leave one live, left {sorted(live)}"
    assert len(retired) == 1, f"expected 1 retirement, got {sorted(retired)}"
    # No row may be retired against a row that is itself retired -- that is the orphan
    # a chain walk creates, and it leaves the group with no readable survivor.
    assert not (survivors & retired), (
        f"a survivor is itself superseded: {sorted(survivors & retired)}")


def test_a_row_is_never_superseded_against_itself():
    """The cycle, reduced to its smallest form.

    Two captures that each 'win' under a different pass pointed at each other; the chain
    walk then resolved both back to themselves and dropped both pairs, so a plain
    duplicate stayed live. Cheap to assert, and it is the shape that cost 1,455 rows.
    """
    rows = [
        dict(_row(1, 989_900, "29/07/2026"), source_text=_LINE,
             postcode="4306", estate_name="Tallow", builder_name="Hattan Homes"),
        dict(_row(2, 989_900, "06/08/2026", channel="digital email"), source_text=_LINE,
             postcode=None, estate_name=None, builder_name=None),
    ]
    pairs = find_superseded(rows)
    assert len(pairs) == 1, (
        f"one duplicate must be retired, got {len(pairs)} -- 0 means both pairs were "
        f"dropped by a cycle and BOTH copies stayed live")
    loser, winner = pairs[0]
    assert loser["id"] != winner["id"], "a row was superseded against itself"
    # The better-described capture survives, not merely the newest one.
    assert winner["id"] == 1, f"the row that knows postcode/estate/builder must win, got {winner['id']}"


# ---------------------------------------------------------------------------------------
# THE FOURTH PASS: the same line on one document, read again with a new status word
# (Part C W01). Row shapes are the real 17 Sep 2026 captures; the documents are stand-ins,
# never the builders' real links.
# ---------------------------------------------------------------------------------------

_HOWARD_DOC = "https://sheets.example.test/g-developments-brisbane-east"
_HOWARD = ("Howard {} 21 Howard Crossing Kinchant Coastal 4 2 2 350 $224,000 180.58 "
           "$522,632 $746,632 675 Due September 2026")


def _capture(i, text, status, date, checked_at, doc=_HOWARD_DOC, price=746_632.0,
             channel="E-Agent", state="QLD", suburb="Howard", **over):
    """A stored row the way a flattened price-list line arrives: lot_address carries the
    whole line, status word included, which is why lot_key cannot join two reads."""
    row = _row(i, price, date, suburb=suburb, addr=text, channel=channel, state=state,
               beds=4, baths=2, cars=2, house=None, land=350.0)
    row.update(source_text=text, availability_status=status, checked_at=checked_at,
               source_url=doc, stocklist_file=doc, builder_name="Testco Homes",
               superseded_by=None)
    row.update(over)
    return row


def _retirements(rows, kept=None):
    """({loser: winner} from the three passes, {loser: winner} from the status pass),
    run in the order main() runs them."""
    base = find_superseded(rows)
    status = find_status_superseded(rows, {l["id"] for l, _ in base}, kept_live=kept)
    return ({l["id"]: w["id"] for l, w in base},
            {l["id"]: w["id"] for l, w in status})


def test_a_status_flip_is_one_listing_and_the_latest_read_wins():
    """Howard lot 21: 'Available' read 15/09 11:40 (id 18491), 'ON HOLD' read 16/09 11:41
    (id 15953). Both stayed live and Colin's QLD <= $750k search recommended the lot the
    builder had put on hold. The newer read has the LOWER id, so this also proves the read
    time decides, not the insert order."""
    rows = [
        _capture(18491, _HOWARD.format("Available"), "Available", "15/09/2026",
                 "2026-09-15T11:40:07"),
        _capture(15953, _HOWARD.format("ON HOLD"), "On Hold", "16/09/2026",
                 "2026-09-16T11:41:43"),
    ]
    base, status = _retirements(rows)
    assert base == {}, f"the three passes were never able to join these: {base}"
    assert status == {18491: 15953}, status


def test_the_status_pass_never_moves_an_existing_winner():
    """Additive, never folded into the union. The Tomorrow Homes Strathtulloh group is
    six captures joined by lot_key and text_key, survivor 8950; its older copies 1961/8948
    are the same '219 ... HOLD' line read on 04/08 and 27/08. A pass that re-grouped rows
    the three passes had already retired would elect 8948 over 1961 and move 1961's mark.
    14881 (digital email) -> 12408 (E-Agent, better described) is the cross-channel shape."""
    doc = "https://sheets.example.test/tomorrow-homes-west"
    line = ("Q3 26 Tulloh St {lot} {street} Strathtulloh 7.5 210 MARTO 1 3 $242,500 {build} "
            "{total} TH BROCHURE - {lot} {status} {tail}")
    l219 = line.format(lot=219, street="Craven Way", build="$355,600", total="$598,100",
                       status="HOLD", tail="Upgrades List")
    l220 = line.format(lot=220, street="Craven Way", build="$355,600", total="$598,100",
                       status="AVAILABLE", tail="Internal colour selection")
    l210 = line.format(lot=210, street="Hydepark Cres", build="$358,600", total="$601,100",
                       status="AVAILABLE", tail="External facade selection")

    def tomorrow(i, text, status, price, date, checked_at, newer):
        over = dict(state="VIC", suburb="Strathtulloh", lot_address="26 Tulloh St",
                    bedrooms=3, land_sqm=210.0) if newer else dict(
                    state=None, suburb="WEST Draft HIA", lot_address="26 Tulloh St, WEST Draft HIA",
                    bedrooms=None, land_sqm=None)
        return _capture(i, text, status, date, checked_at, doc=doc, price=price,
                        bathrooms=None, car_spaces=None, house_sqm=None, **over)

    clyde = "Available Clyde North House 332 10.46 Langdon 16 3 2 2 $423,400 $355,550 $778,950"
    rows = [
        tomorrow(1961, l219, "On Hold", 598_100.0, "04/08/2026", None, False),
        tomorrow(1962, l220, "Available", 598_100.0, "04/08/2026", None, False),
        tomorrow(1963, l210, "Available", 601_100.0, "04/08/2026", None, False),
        tomorrow(8948, l219, "On Hold", 598_100.0, "27/08/2026", "2026-08-27T14:34:15", True),
        tomorrow(8949, l220, "Available", 598_100.0, "27/08/2026", "2026-08-27T14:34:15", True),
        tomorrow(8950, l210, "Available", 601_100.0, "27/08/2026", "2026-08-27T14:34:15", True),
        _capture(12408, clyde, "Sold", "22/08/2026", "2026-08-22T03:08:51",
                 doc="https://pdf.example.test/hattan-q4", price=778_950.0, state="VIC",
                 suburb=None, bedrooms=3, bathrooms=2, car_spaces=2, land_sqm=None,
                 estate_name="Q4 Lot Single", builder_name="Hattan Homes"),
        _capture(14881, clyde, "Available", "22/08/2026", "2026-08-22T03:22:58",
                 doc="email:Hattan Homes Q4 stock", price=778_950.0, state=None,
                 suburb=None, channel="digital email", bedrooms=None, bathrooms=None,
                 car_spaces=None, land_sqm=None, estate_name="Q4 Lot Single",
                 builder_name="Hattan Homes"),
    ]
    base, status = _retirements(rows)
    assert base == {1961: 8950, 1962: 8950, 1963: 8950, 8948: 8950, 8949: 8950,
                    14881: 12408}, base
    assert not set(status) & set(base), (
        f"the status pass re-decided rows the three passes had already retired: "
        f"{ {i: (base[i], status[i]) for i in set(status) & set(base)} }")


def test_a_tie_on_the_latest_read_keeps_the_lower_id():
    """Two captures read on the same day with no time of day recorded: the LOWER id
    survives, the way api/_candidates._collapse_same_listing keeps the first-stored row,
    whatever either capture says and whatever order the rows arrive in."""
    for first, second in (("Sold", "Available"), ("Available", "Sold")):
        rows = [
            _capture(501, _HOWARD.format(first), first, "03/09/2026", None),
            _capture(502, _HOWARD.format(second), second, "03/09/2026", None),
        ]
        for order in (rows, list(reversed(rows))):
            _base, status = _retirements(order)
            assert status == {502: 501}, (first, second, status)


def test_two_lines_of_one_read_are_two_lots_not_a_status_change():
    """Merbein 18203 'Hold' and 18204 'Available' were written by ONE read of one PDF page
    (checked_at 2026-09-03T11:32:01, which all 72 rows of that PDF share). One read cannot
    show one line twice, so they are two lots with identical specs and neither may retire
    the other. An older capture of the line is still retired, onto the lower id."""
    doc = "https://pdf.example.test/e-agent-pooled"
    merbein = "Merbein,Victoria,3505 $691,000.00 463 m² 179 m² 4 2 2 $700.00 November 2026 {}"
    same_read = [
        _capture(18203, merbein.format("Hold"), "On Hold", "03/09/2026", "2026-09-03T11:32:01",
                 doc=doc, price=691_000.0, state="VIC", suburb=None),
        _capture(18204, merbein.format("Available"), "Available", "03/09/2026",
                 "2026-09-03T11:32:01", doc=doc, price=691_000.0, state="VIC", suburb=None),
    ]
    kept = []
    _base, status = _retirements(same_read, kept)
    assert status == {}, f"a line of the same read was retired: {status}"
    assert [(k[0]["id"], k[1]["id"]) for k in kept] == [(18204, 18203)], kept

    # A different word from both, so text_key (identical lines) cannot take it first.
    older = _capture(17000, merbein.format("Under Contract"), "Sold", "27/08/2026",
                     "2026-08-27T10:00:00", doc=doc, price=691_000.0, state="VIC", suburb=None)
    base, status = _retirements(same_read + [older])
    assert base == {} and status == {17000: 18203}, (base, status)


def test_lines_of_one_read_stamped_seconds_apart_are_still_one_read():
    """checked_at is stamped per ROW, to the second (database.record_building), so one read
    is not one timestamp: on the 17 Sep copy 46 of 336 document reads wrote their lines
    across 2-6 different seconds (up to 16 s apart), while two reads of one document on
    the same day were never less than 41 minutes apart. The Merbein pair is re-read, and
    re-stamped, every night; the night its two lines land a second apart must not retire
    either lot, in either order. A second read 41 minutes later is a different read, and
    its newer status still wins."""
    doc = "https://pdf.example.test/e-agent-pooled"
    merbein = "Merbein,Victoria,3505 $691,000.00 463 m² 179 m² 4 2 2 $700.00 November 2026 {}"
    for hold_at, avail_at in (("2026-09-03T11:32:01", "2026-09-03T11:32:02"),
                              ("2026-09-03T11:32:02", "2026-09-03T11:32:01"),
                              ("2026-09-03T11:31:50", "2026-09-03T11:32:06")):
        rows = [
            _capture(18203, merbein.format("Hold"), "On Hold", "03/09/2026", hold_at,
                     doc=doc, price=691_000.0, state="VIC", suburb=None),
            _capture(18204, merbein.format("Available"), "Available", "03/09/2026", avail_at,
                     doc=doc, price=691_000.0, state="VIC", suburb=None),
        ]
        _base, status = _retirements(rows)
        assert status == {}, (hold_at, avail_at, status)

    two_reads = [
        _capture(18204, merbein.format("Available"), "Available", "03/09/2026",
                 "2026-09-03T11:32:01", doc=doc, price=691_000.0, state="VIC", suburb=None),
        _capture(18203, merbein.format("Hold"), "On Hold", "03/09/2026",
                 "2026-09-03T12:13:17", doc=doc, price=691_000.0, state="VIC", suburb=None),
    ]
    _base, status = _retirements(two_reads)
    assert status == {18204: 18203}, status


def test_same_text_on_different_documents_is_not_merged():
    """Scope is ONE document. The same line on two price lists is two claims nobody has
    checked are one listing (per channel the simulation retired 167 rows, not 121)."""
    rows = [
        _capture(18491, _HOWARD.format("Available"), "Available", "15/09/2026",
                 "2026-09-15T11:40:07", doc="https://sheets.example.test/list-a"),
        _capture(15953, _HOWARD.format("ON HOLD"), "On Hold", "16/09/2026",
                 "2026-09-16T11:41:43", doc="https://sheets.example.test/list-b"),
    ]
    base, status = _retirements(rows)
    assert (base, status) == ({}, {}), (base, status)


_VOLOS_DOC = "https://sheets.example.test/avia-homes-stock"
_VOLOS_45 = ("45 Stage 5 581m2 $415,000 Sept-2026 Valletta Paddington 180m2 4 2 2 0 $491,600 "
             "$906,600 Volos {} Owner OCC & Investor 04.09.2026 "
             "https://drive.example.test/file/volos-45/view")
_VOLOS_74 = ("74 Stage 2 480m2 $610,000 Registered Tropea Yamba 190m2 4 2 2 1 $432,400 "
             "$1,042,400 Volos {} Owner Occ & Investor 04.09.2026 "
             "https://drive.example.test/file/volos-74/view")


def _volos_rows():
    """AVIA Homes' Volos sheet, 16 Sep: 21 lots went from 'Available' to 'EOI'. EOI is
    not in the search's vocabulary, so the new captures are stored with no status."""
    return [
        _capture(7805, _VOLOS_45.format("Available"), "Available", "10/09/2026",
                 "2026-09-10T11:25:26", doc=_VOLOS_DOC, price=906_600.0, suburb=None,
                 lot_number="45"),
        _capture(18990, _VOLOS_45.format("EOI"), None, "16/09/2026", "2026-09-16T11:41:42",
                 doc=_VOLOS_DOC, price=906_600.0, state=None, suburb=None, lot_number="45"),
        _capture(7791, _VOLOS_74.format("Available"), "Available", "08/09/2026",
                 "2026-09-08T11:24:34", doc=_VOLOS_DOC, price=1_042_400.0, suburb=None,
                 lot_number="74"),
        _capture(18989, _VOLOS_74.format("EOI"), None, "16/09/2026", "2026-09-16T11:41:42",
                 doc=_VOLOS_DOC, price=1_042_400.0, suburb=None, lot_number="74"),
    ]


def test_an_available_capture_is_never_retired_for_a_read_that_states_nothing():
    """Critique #2. Retiring 7805 and 7791 in favour of their blank 'EOI' captures would
    drop 21 real lots out of every result and every needs-confirming list."""
    import supersede_duplicates as sd
    kept = []
    base, status = _retirements(_volos_rows(), kept)
    assert base == {} and status == {}, (base, status)
    assert sorted((k[0]["id"], k[1]["id"]) for k in kept) == [(7791, 18989), (7805, 18990)]
    assert {k[2] for k in kept} == {sd.KEPT_AVAILABLE_OVER_UNSTATED}


def test_a_later_stated_status_still_retires_the_available_capture():
    """The exception is for a read that says NOTHING. If On Hold was read in between, that
    is the news, and the Available capture goes as usual even though the newest read is
    blank: otherwise a held lot would come back as Available."""
    held = _capture(9000, _VOLOS_45.format("On Hold"), "On Hold", "12/09/2026",
                    "2026-09-12T11:00:00", doc=_VOLOS_DOC, price=906_600.0, suburb=None,
                    lot_number="45")
    _base, status = _retirements(_volos_rows()[:2] + [held])
    assert status == {7805: 18990, 9000: 18990}, status


def test_the_volos_lot_still_reaches_the_search_after_the_pass():
    """End to end through the search's own gate: after the pass, and its marks applied,
    the Available Volos capture is packaged (read 10/09, fresh on 16/09) and nothing is
    counted as superseded. W11/W13 then decide whether it is shortlisted or needs
    confirming; either way it must not have vanished here."""
    import os, sys
    from datetime import datetime
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path in (here, os.path.join(here, "api")):
        if path not in sys.path:
            sys.path.insert(0, path)
    import _candidates

    rows = _volos_rows()
    base, status = _retirements(rows)
    by_id = {r["id"]: r for r in rows}
    for loser, winner in list(base.items()) + list(status.items()):
        by_id[loser]["superseded_by"] = by_id[winner]["content_hash"]
    snapshot = [dict(r, builder_name="AVIA Homes", suburb="Park Ridge", land_size_sqm=581.0,
                     house_sqm=180.0, source_url_or_ref=_VOLOS_DOC, source_doc=_VOLOS_DOC)
                for r in rows]
    brief = {"state": "QLD", "budget_max": 1_100_000, "preferred_spending_cap": 1_100_000,
             "bedrooms_min": 0, "bathrooms_min": 0, "car_spaces_min": 0}
    packages, counts = _candidates.build_packages(brief, snapshot,
                                                  today=datetime(2026, 9, 16))
    assert counts["superseded"] == 0, counts
    hashes = {p["content_hash"] for p in packages}
    assert by_id[7805]["content_hash"] in hashes, (sorted(hashes), counts)


def test_status_words_are_the_searchs_own_vocabulary():
    """The copies in supersede_duplicates must be the sets the search filters on; a word
    added to one and not the other would split or join lines the search reads differently."""
    import os, sys
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path in (here, os.path.join(here, "api")):
        if path not in sys.path:
            sys.path.insert(0, path)
    import _candidates
    import supersede_duplicates as sd
    assert set(sd.NOT_AVAILABLE) == set(_candidates.NOT_AVAILABLE)
    assert set(sd.IS_AVAILABLE) == set(_candidates.IS_AVAILABLE)
    # The stage/title words are part of the line, never stripped as a status.
    for w in ("titled", "released", "selling", "open"):
        assert w not in sd.STATUS_WORDS, w
    assert sd.status_free_text("Lot 7 Titled ON HOLD $500,000") == \
        sd.status_free_text("Lot 7 Titled Available $500,000") == "lot 7 titled $500,000"


def test_the_review_label_never_carries_the_link():
    import supersede_duplicates as sd
    label = sd.document_label({"source_url": "https://docs.example.test/spreadsheets/d/SECRET123/edit?usp=sharing"})
    assert "docs.example.test" in label and "SECRET123" not in label and "://" not in label
    assert sd.document_label({"source_url": "email:Fwd: Shape Homes - Stock List 13.08.2026"}) \
        .startswith("email 'Fwd: Shape Homes - Stock List 13.08.2026'")


def test_a_subject_line_with_an_emoji_or_a_link_cannot_sink_the_status_pass(
        tmp_path, monkeypatch):
    """The QLD review list names each document, and emailed lists are named by their
    subject. The 17 Sep copy holds a subject that is not even latin-1. Printed to a cp1252
    pipe (a Windows run with its output redirected), that label raised inside the status
    pass's try block, and the night's status retirements were dropped as a failed pass.
    A subject carrying a link must not put the link in the log either."""
    import io
    import sqlite3
    import sys
    import config
    import supersede_duplicates as sd
    from database import ResearchDatabase

    doc = "email:Fwd: \U0001f3e0 Howard stock – see https://drive.example.test/f/SECRET9"
    label = sd.document_label({"source_url": doc})
    assert label.isascii() and "Howard stock" in label, label
    assert "SECRET9" not in label and "://" not in label, label

    db = tmp_path / "subject.db"
    ResearchDatabase(db)
    rows = [
        _capture(18491, _HOWARD.format("Available"), "Available", "15/09/2026",
                 "2026-09-15T11:40:07", doc=doc, channel="digital email"),
        _capture(15953, _HOWARD.format("ON HOLD"), "On Hold", "16/09/2026",
                 "2026-09-16T11:41:43", doc=doc, channel="digital email"),
    ]
    cols = ("id", "source_channel", "lot_address", "suburb", "state", "price", "source_url",
            "stocklist_file", "date_checked", "last_seen", "checked_at",
            "availability_status", "source_text", "content_hash")
    conn = sqlite3.connect(str(db))
    conn.executemany(f"INSERT INTO buildings ({', '.join(cols)}) VALUES "
                     f"({', '.join('?' for _ in cols)})",
                     [tuple(r.get(c) for c in cols) for r in rows])
    conn.commit()
    conn.close()

    pipe = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.delenv("SPB_STATUS_PASS", raising=False)
    monkeypatch.setattr(config, "DATABASE_PATH", db)
    monkeypatch.setattr(sys, "stdout", pipe)
    code = sd.main([])
    monkeypatch.undo()
    pipe.flush()
    out = pipe.buffer.getvalue().decode("cp1252")
    assert code == 0, out
    assert _marks(db) == {18491: "v2:hash15953", 15953: None}, out
    assert "SECRET9" not in out and "://" not in out, out


def test_the_status_pass_switch_reads_off_words_only():
    import supersede_duplicates as sd
    for on in ({}, {"SPB_STATUS_PASS": ""}, {"SPB_STATUS_PASS": "1"},
               {"SPB_STATUS_PASS": "on"}, {"SPB_STATUS_PASS": "yes"}):
        assert sd.status_pass_enabled(on), on
    for off in ("0", "off", "OFF ", "false", "No"):
        assert not sd.status_pass_enabled({"SPB_STATUS_PASS": off}), off


def _seed_db(path):
    """A real buildings table (ResearchDatabase's own migration) holding one ordinary
    duplicate the three passes retire, and the Howard status flip."""
    import sqlite3
    from database import ResearchDatabase
    ResearchDatabase(path)
    rows = [
        # an ordinary duplicate: the same line read twice, three passes' business
        _capture(1, _LINE, "Available", "29/07/2026", None, doc="https://s.example.test/a",
                 price=989_900.0, suburb="Ripley", land_sqm=455.0),
        _capture(2, _LINE, "Available", "06/08/2026", None, doc="https://s.example.test/a",
                 price=989_900.0, suburb="Ripley", land_sqm=455.0),
        _capture(18491, _HOWARD.format("Available"), "Available", "15/09/2026",
                 "2026-09-15T11:40:07"),
        _capture(15953, _HOWARD.format("ON HOLD"), "On Hold", "16/09/2026",
                 "2026-09-16T11:41:43"),
    ]
    cols = ("id", "builder_name", "source_channel", "lot_address", "suburb", "state", "price",
            "bedrooms", "bathrooms", "car_spaces", "land_sqm", "house_sqm", "source_url",
            "stocklist_file", "date_checked", "last_seen", "checked_at",
            "availability_status", "source_text", "content_hash")
    conn = sqlite3.connect(str(path))
    conn.executemany(
        f"INSERT INTO buildings ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
        [tuple(r.get(c) for c in cols) for r in rows])
    conn.commit()
    conn.close()


def _marks(path):
    import sqlite3
    conn = sqlite3.connect(str(path))
    out = dict(conn.execute("SELECT id, superseded_by FROM buildings"))
    conn.close()
    return out


def test_main_applies_the_status_pass_by_default_and_off_restores_today(
        tmp_path, monkeypatch, capsys):
    """Critique #4: SPB_STATUS_PASS defaults ON; turning it off writes exactly what the
    three-pass rule writes today."""
    import config
    import supersede_duplicates as sd

    on_db, off_db = tmp_path / "on.db", tmp_path / "off.db"
    for p in (on_db, off_db):
        _seed_db(p)

    monkeypatch.delenv("SPB_STATUS_PASS", raising=False)
    monkeypatch.setattr(config, "DATABASE_PATH", on_db)
    assert sd.main([]) == 0
    on = _marks(on_db)
    assert on[1] == "v2:hash2" and on[18491] == "v2:hash15953", on
    assert on[2] is None and on[15953] is None, on
    out = capsys.readouterr().out
    assert "1 more superseded because the same line was read again" in out, out
    assert "18491 -> 15953" in out, out

    monkeypatch.setenv("SPB_STATUS_PASS", "0")
    monkeypatch.setattr(config, "DATABASE_PATH", off_db)
    assert sd.main([]) == 0
    off = _marks(off_db)
    assert off == {1: "v2:hash2", 2: None, 18491: None, 15953: None}, off
    assert "status pass OFF" in capsys.readouterr().out


def test_a_failing_status_pass_falls_back_and_marks_the_run_degraded(
        tmp_path, monkeypatch, capsys):
    """Critique #5: supersede_duplicates.py is a REQUIRED nightly step. An exception in the
    new pass must cost tonight's status retirements, not tonight's publish: the three
    passes' marks are still written and the exit code tells run_daily to mark the run
    DEGRADED."""
    import config
    import supersede_duplicates as sd

    db = tmp_path / "boom.db"
    _seed_db(db)

    def boom(*a, **k):
        raise RuntimeError("status pass exploded")

    monkeypatch.delenv("SPB_STATUS_PASS", raising=False)
    monkeypatch.setattr(config, "DATABASE_PATH", db)
    monkeypatch.setattr(sd, "find_status_superseded", boom)
    assert sd.main([]) == sd.DEGRADED_EXIT
    assert _marks(db) == {1: "v2:hash2", 2: None, 18491: None, 15953: None}
    out = capsys.readouterr().out
    assert "ERROR: the status pass failed" in out and "Traceback" in out, out
    assert "status pass exploded" in out, out


def test_run_daily_publishes_a_degraded_step_and_records_it(tmp_path, monkeypatch):
    """DEGRADED_EXIT from a step: the chain continues (supersede is REQUIRED, so a non-ok
    here would abort the night) and the label lands in _DEGRADED, which writes the marker
    CI fails on after publishing. Not 2, which CI reports as a dead harvest channel."""
    import run_daily
    import supersede_duplicates as sd

    assert run_daily.DEGRADED_EXIT == sd.DEGRADED_EXIT, "the step and the runner must agree"
    (tmp_path / "degraded_step.py").write_text(
        f"import sys\nsys.exit({sd.DEGRADED_EXIT})\n", encoding="utf-8")
    monkeypatch.setattr(run_daily, "APP", tmp_path)
    run_daily._DEGRADED.clear()
    run_daily._PARTIAL.clear()
    try:
        assert run_daily._run("4/7  Supersede older captures of the same lot",
                              ["degraded_step.py"], False) is True
        assert len(run_daily._DEGRADED) == 1, run_daily._DEGRADED
        assert run_daily._DEGRADED[0].startswith("4/7  Supersede older captures")
        assert run_daily._PARTIAL == [], "a fallback is not a dead channel"
    finally:
        run_daily._DEGRADED.clear()
        run_daily._PARTIAL.clear()


def test_a_crashed_required_step_is_never_read_as_degraded(tmp_path, monkeypatch):
    """The degraded code must be one no crash produces. 3 is not: it is what a Windows
    process returns when the C runtime aborts, which is how a "Fatal Python error" in a
    native module ends, and run_daily.cmd runs this chain on Windows. Had 3 meant
    "finished on the fallback", a REQUIRED step that crashed that way would read as ok,
    and the chain would carry on to export, build and publish over it."""
    import run_daily

    monkeypatch.setattr(run_daily, "APP", tmp_path)
    for code in (1, 3):
        (tmp_path / "crashed_step.py").write_text(f"import sys\nsys.exit({code})\n",
                                                  encoding="utf-8")
        run_daily._DEGRADED.clear()
        run_daily._PARTIAL.clear()
        try:
            assert run_daily._run("2/7  Enrich: state, suburb, builder",
                                  ["crashed_step.py"], False) is False, code
            assert run_daily._DEGRADED == [] and run_daily._PARTIAL == [], code
        finally:
            run_daily._DEGRADED.clear()
            run_daily._PARTIAL.clear()


def run_all():
    tests = [
        ("every duplicate found is retired", test_every_duplicate_found_is_actually_retired),
        ("no row supersedes itself", test_a_row_is_never_superseded_against_itself),
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
        ("a status flip is one listing", test_a_status_flip_is_one_listing_and_the_latest_read_wins),
        ("status pass moves no winner", test_the_status_pass_never_moves_an_existing_winner),
        ("a read tie keeps the lower id", test_a_tie_on_the_latest_read_keeps_the_lower_id),
        ("one read, two lines", test_two_lines_of_one_read_are_two_lots_not_a_status_change),
        ("one read across seconds", test_lines_of_one_read_stamped_seconds_apart_are_still_one_read),
        ("other document not merged", test_same_text_on_different_documents_is_not_merged),
        ("Available kept over EOI", test_an_available_capture_is_never_retired_for_a_read_that_states_nothing),
        ("a later stated status wins", test_a_later_stated_status_still_retires_the_available_capture),
        ("Volos lot reaches the search", test_the_volos_lot_still_reaches_the_search_after_the_pass),
        ("status words are the search's", test_status_words_are_the_searchs_own_vocabulary),
        ("review label has no link", test_the_review_label_never_carries_the_link),
        ("status pass switch", test_the_status_pass_switch_reads_off_words_only),
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
