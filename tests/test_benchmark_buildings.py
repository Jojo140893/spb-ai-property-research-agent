"""
Tests for benchmark_buildings.py — step 4 of the daily run.

Every guard here exists because the live data broke it. A benchmark is a number
that ends up in front of a buyer, so the failures worth testing are the ones that
produce a confident-looking percentage with nothing behind it.
"""

import benchmark_buildings as bb


class _FakeCheck:
    """Stands in for the AU suburb index. Anything in `real` is a locality."""

    def __init__(self, real):
        self.real = {s.lower() for s in real}
        self.available = True

    def is_real(self, row):
        return bool(self.resolve(row))

    def resolve(self, row):
        """Mirrors suburb_quality.resolve: the locality to group on, or '' if none.

        Includes the unglue step, because that is what the real resolver does and a
        stub that skipped it would let a regression through unnoticed. Takes the whole
        ROW, matching the real signature — a stub with the old (suburb, state) shape
        would keep passing against an interface that no longer exists.
        """
        raw = str(row.get("suburb") or "").strip()
        if raw.lower() in self.real:
            return raw
        for part in reversed([p.strip(" ()") for p in raw.split(",") if p.strip(" ()")]):
            if part.lower() in self.real:
                return part
        return ""


def _row(i, price, suburb="Sampleton", state="NSW", product="House & Land", beds=4):
    return {"id": i, "price": price, "suburb": suburb, "state": state,
            "product_type": product, "bedrooms": beds}


def _group(prices, **kw):
    return [_row(i, p, **kw) for i, p in enumerate(prices, start=1)]


# ------------------------------------------------------------------ the median

def test_a_row_is_excluded_from_its_own_median():
    """Including the row drags the median toward it and flattens every variance.

    Prices chosen so the answer differs either way AND the group stays inside the
    dispersion guard: whole group median is 150, but a 100-priced row sees peers
    [100,100,200,200,200] -> 200, and a 200-priced row sees [100,100,100,200,200]
    -> 100. If self were included both would read 150 and both variances would
    shrink toward zero.
    """
    rows = _group([100.0, 100.0, 100.0, 200.0, 200.0, 200.0])
    res, _ = bb.benchmark_internal(rows, _FakeCheck(["Sampleton"]))
    assert len(res) == 6, res

    cheap = res[1]                       # priced 100
    assert cheap["benchmark_median"] == 200.0, cheap
    assert cheap["benchmark_variance_pct"] == -50.0, cheap

    dear = res[6]                        # priced 200
    assert dear["benchmark_median"] == 100.0, dear
    assert dear["benchmark_variance_pct"] == 100.0, dear


def test_variance_is_signed_the_way_a_reader_expects():
    rows = _group([100.0] * 6 + [50.0])
    res, _ = bb.benchmark_internal(rows, _FakeCheck(["Sampleton"]))
    cheap = res[7]
    assert cheap["benchmark_variance_pct"] < 0, "a cheaper listing must read negative"
    assert "Below" in cheap["benchmark_classification"], cheap


def test_a_group_below_the_floor_is_refused():
    """Four listings do not establish a typical price."""
    res, skipped = bb.benchmark_internal(_group([1.0, 2.0, 3.0, 4.0]),
                                         _FakeCheck(["Sampleton"]))
    assert res == {}
    assert sum(skipped.values()) == 4


# ------------------------------------------------- the guards the data forced

def test_a_junk_suburb_never_forms_a_peer_group():
    """59% of suburb values are not localities — header fragments, states, regions.

    Filtering has to happen BEFORE grouping. If a junk value can form a group, the
    real listings that happen to share it get benchmarked against nonsense, and the
    worst of those sort straight to the top of a best-deals list.
    """
    rows = _group([100.0] * 6, suburb="Rooms Rooms m2 m2 m2")
    res, skipped = bb.benchmark_internal(rows, _FakeCheck(["Sampleton"]))
    assert res == {}, "a parsing accident was used as a peer group"
    assert skipped["suburb is not a recognised locality"] == 6


def test_the_market_path_refuses_a_junk_suburb_too():
    """The dormant path is not the safe path.

    benchmark_against_market tested only that the suburb was NON-EMPTY, so 'Rooms
    Rooms m2 m2 m2' and 'Logan City Council' would have been sent to the provider as
    places to price against. It takes over from the internal path automatically the
    moment a comparables*.csv lands in drive_input/ — no code change, no review — so
    the gate has to be there before that day, not after it.
    """
    class _Engine:
        def __init__(self):
            self.asked = []

        def benchmark_package(self, suburb, state, beds, price):
            self.asked.append(suburb)
            return {"benchmarked": True, "avg_market_price": 500.0,
                    "variance_pct": -1.0, "classification": "Below Market Value",
                    "data_note": "stub"}

    engine = _Engine()
    rows = _group([100.0] * 3, suburb="Rooms Rooms m2 m2 m2")
    res, skipped = bb.benchmark_against_market(rows, engine, _FakeCheck(["Sampleton"]))
    assert engine.asked == [], f"a parsing accident was priced as a suburb: {engine.asked}"
    assert res == {} and skipped["suburb is not a recognised locality"] == 3, skipped

    # ...and a real one still gets through, grouped on the RESOLVED locality.
    engine2 = _Engine()
    good = _group([100.0] * 2, suburb="Stage 5A, Sampleton")
    res2, _ = bb.benchmark_against_market(good, engine2, _FakeCheck(["Sampleton"]))
    assert engine2.asked == ["Sampleton", "Sampleton"], engine2.asked
    assert len(res2) == 2


def test_both_paths_ask_the_same_question_about_a_suburb():
    """One resolver, four consumers. This class used to keep its own copy and the
    copies drifted twice — once against the scoring pipeline, once against the
    published snapshot."""
    import suburb_quality
    from inspect import signature
    assert list(signature(bb.SuburbCheck.resolve).parameters)[1:] == ["row"],         "SuburbCheck.resolve must take the whole row, as suburb_quality does"
    check = bb.SuburbCheck()
    if not check.available:
        return
    row = {"suburb": "Cloverton Estate , Kalkallo 3064", "state": "VIC",
           "lot_address": "", "street_address": "", "source_text": ""}
    assert check.resolve(row) == suburb_quality.resolve(row)[0] == "Kalkallo"
    # LOCATED, not merely non-empty: no state means no peer group.
    assert check.resolve({"suburb": "Springfield", "state": ""}) == ""


def test_a_real_suburb_still_benchmarks_alongside_junk_ones():
    rows = _group([100.0] * 6) + _group([900.0] * 6, suburb="Street # Type")
    for i, r in enumerate(rows):
        r["id"] = i + 1
    res, _ = bb.benchmark_internal(rows, _FakeCheck(["Sampleton"]))
    assert len(res) == 6, "the valid suburb should still be benchmarked"
    assert all(v["benchmark_median"] == 100.0 for v in res.values())


def test_a_group_that_is_too_spread_out_is_refused():
    """A penthouse and a studio in one building are not comparables.

    The price gap is floor area, and calling the penthouse "well above comparable
    stock" would say something false about a lot that is simply bigger.
    """
    rows = _group([100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 5000.0],
                  product="Apartment", beds=None)
    res, skipped = bb.benchmark_internal(rows, _FakeCheck(["Sampleton"]))
    assert res == {}, "a x50 spread was treated as a comparable set"
    assert skipped["peer group too spread out to be comparable"] == 7


def test_a_tight_group_is_not_refused():
    rows = _group([500.0, 520.0, 540.0, 560.0, 580.0, 600.0])
    res, _ = bb.benchmark_internal(rows, _FakeCheck(["Sampleton"]))
    assert len(res) == 6, "a tight group should benchmark"


def test_dispersion_uses_percentiles_not_extremes():
    """One oddly cheap listing must not disqualify an otherwise tight group."""
    tight = [500.0] * 20
    assert bb.dispersion(tight) == 1.0
    assert bb.dispersion(tight + [1.0]) < bb.MAX_DISPERSION


# ------------------------------------------------------- honesty of the label

def test_the_internal_wording_never_claims_the_market():
    """benchmark.py's SOP bands mean a real market comparison. These must not
    borrow that wording, or a client card could claim "Below Market Value" off the
    back of a comparison against our own stock."""
    for _edge, label in bb.INTERNAL_BANDS:
        assert "market" not in label.lower(), label
    assert "market" not in bb.INTERNAL_TOP.lower()


def test_every_result_records_what_it_was_compared_against():
    rows = _group([500.0, 520.0, 540.0, 560.0, 580.0, 600.0])
    res, _ = bb.benchmark_internal(rows, _FakeCheck(["Sampleton"]))
    for v in res.values():
        basis = v["benchmark_basis"]
        assert basis.startswith("internal peer median"), basis
        assert "peers)" in basis, "the peer count has to be on the row"


def test_the_tightest_available_tier_wins():
    """suburb + product + bedrooms beats suburb + product where both qualify."""
    rows = _group([500.0] * 6, beds=4) + _group([900.0] * 6, beds=3)
    for i, r in enumerate(rows):
        r["id"] = i + 1
    res, _ = bb.benchmark_internal(rows, _FakeCheck(["Sampleton"]))
    assert all("bedrooms" in v["benchmark_basis"] for v in res.values()), \
        "a coarser tier was used where a tighter one qualified"
    assert res[1]["benchmark_median"] == 500.0
    assert res[7]["benchmark_median"] == 900.0


def run_all():
    tests = [
        ("row excluded from its own median", test_a_row_is_excluded_from_its_own_median),
        ("variance signed as expected", test_variance_is_signed_the_way_a_reader_expects),
        ("group below the floor refused", test_a_group_below_the_floor_is_refused),
        ("junk suburb forms no group", test_a_junk_suburb_never_forms_a_peer_group),
        ("real suburb unaffected by junk", test_a_real_suburb_still_benchmarks_alongside_junk_ones),
        ("market path refuses junk too", test_the_market_path_refuses_a_junk_suburb_too),
        ("both paths ask the same question", test_both_paths_ask_the_same_question_about_a_suburb),
        ("over-spread group refused", test_a_group_that_is_too_spread_out_is_refused),
        ("tight group accepted", test_a_tight_group_is_not_refused),
        ("dispersion uses percentiles", test_dispersion_uses_percentiles_not_extremes),
        ("internal wording avoids 'market'", test_the_internal_wording_never_claims_the_market),
        ("basis recorded on every row", test_every_result_records_what_it_was_compared_against),
        ("tightest tier wins", test_the_tightest_available_tier_wins),
        ("advisory is not a rejection cause", test_a_non_rejecting_advisory_is_not_reported_as_a_rejection_cause),
        ("unstated storey never rejects", test_an_unstated_storey_alone_never_rejects),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] benchmark: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] benchmark: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)

def _brief(**over):
    """A ClientBrief with every mandatory field, so a test states only what it varies."""
    from schema import ClientBrief, BuyerType
    kw = dict(client_name="t", budget_max=900_000.0, preferred_spending_cap=850_000.0,
              deposit_amount=100_000.0, finance_status="Pre-approved",
              buyer_type=BuyerType.INVESTOR, state="QLD", primary_suburbs=[],
              bedrooms_min=4, bathrooms_min=2, car_spaces_min=1, storeys_max=1,
              land_size_min_sqm=0.0, house_size_min_sqm=175.0)
    kw.update(over)
    return ClientBrief(**kw)


def _candidate(**over):
    from schema import CandidateProperty, PriceBreakdown, TurnkeyStatus
    price = over.pop("price", 700_000.0)
    kw = dict(property_id="P1", lot_address="Lot 1", suburb="Sampleton", state="QLD",
              builder_name="Placeholder", developer_name="", house_design="",
              bedrooms=3, bathrooms=2, car_spaces=1, storeys=None,
              land_size_sqm=300.0, house_size_sqm=133.0, title_status="Registered",
              expected_title_date="", estimated_rent_weekly_min=0.0,
              estimated_rent_weekly_max=0.0, amenities_summary="",
              builder_confidence_rating="MEDIUM", source_channel="test",
              source_url_or_ref="", date_checked="01/08/2026",
              price_breakdown=PriceBreakdown(
                  advertised_package_price=price, land_price=0.0, build_price=price,
                  fixed_site_costs=0.0, driveway_cost=0.0, fencing_cost=0.0,
                  landscaping_cost=0.0, flooring_cost=0.0, blinds_cost=0.0,
                  hvac_cost=0.0, estimated_additional_costs=0.0,
                  realistic_total_price=price,
                  turnkey_status=TurnkeyStatus.FULL_TURNKEY))
    kw.update(over)
    return CandidateProperty(**kw)


# ------------------------------------------- advisories are not rejection causes

def test_a_non_rejecting_advisory_is_not_reported_as_a_rejection_cause():
    """An unrecorded storey count flags, it does not reject.

    Both used to be pooled into one string, so "Storeys not stated ... confirm with
    the builder" printed on every rejection card under a "Hard Rejection" heading as
    though it had caused the rejection. It reads as an extra failure on a lot that
    failed for one reason, and it made a deliberate decision — flag, never fail,
    because rejecting on it made a single-storey brief return nothing — look like
    its opposite.
    """
    from scoring_engine import ScoringEngine
    s = ScoringEngine.evaluate_property(_brief(), _candidate())
    assert s.hard_rejection is True, "3 beds against a 4-bed minimum must still reject"
    assert "Storeys not stated" not in s.rejection_reason,         "a non-rejecting advisory is being reported as a cause of rejection"
    assert "Bedrooms" in s.rejection_reason, s.rejection_reason
    assert "Storeys not stated" in s.advisories,         "the advisory must still be surfaced, just not as a cause"


def test_an_unstated_storey_alone_never_rejects():
    from scoring_engine import ScoringEngine
    s = ScoringEngine.evaluate_property(
        _brief(bedrooms_min=1, bathrooms_min=1, car_spaces_min=0, house_size_min_sqm=0.0),
        _candidate(bedrooms=4, bathrooms=2, car_spaces=2, house_size_sqm=200.0))
    assert s.hard_rejection is False, "an unrecorded storey count must never reject"
    assert "Storeys not stated" in s.advisories


def run_all():
    tests = [
        ("row excluded from its own median", test_a_row_is_excluded_from_its_own_median),
        ("variance signed as expected", test_variance_is_signed_the_way_a_reader_expects),
        ("group below the floor refused", test_a_group_below_the_floor_is_refused),
        ("junk suburb forms no group", test_a_junk_suburb_never_forms_a_peer_group),
        ("real suburb unaffected by junk", test_a_real_suburb_still_benchmarks_alongside_junk_ones),
        ("market path refuses junk too", test_the_market_path_refuses_a_junk_suburb_too),
        ("both paths ask the same question", test_both_paths_ask_the_same_question_about_a_suburb),
        ("over-spread group refused", test_a_group_that_is_too_spread_out_is_refused),
        ("tight group accepted", test_a_tight_group_is_not_refused),
        ("dispersion uses percentiles", test_dispersion_uses_percentiles_not_extremes),
        ("internal wording avoids 'market'", test_the_internal_wording_never_claims_the_market),
        ("basis recorded on every row", test_every_result_records_what_it_was_compared_against),
        ("tightest tier wins", test_the_tightest_available_tier_wins),
        ("advisory is not a rejection cause", test_a_non_rejecting_advisory_is_not_reported_as_a_rejection_cause),
        ("unstated storey never rejects", test_an_unstated_storey_alone_never_rejects),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] benchmark: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] benchmark: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
