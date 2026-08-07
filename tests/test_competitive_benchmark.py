"""
Benchmark A — the competitive check Coleen actually asked for.

Every test here encodes a way the naive version is wrong, because each one would produce
a confident claim in front of a buyer.
"""

import price_kind
from benchmark_competitive import (CONF_HIGH, CONF_LOW, MIN_COMPS, VERDICT_FLAG,
                                   VERDICT_NONE, VERDICT_PASS, evaluate)
from comp_provider import BASIS_EXACT, NullProvider, RawComp


class _Stub:
    """A provider returning a fixed set, so the ladder can be tested without a licence."""

    name = "stub"

    def __init__(self, by_tier=None, always=None):
        self.by_tier, self.always, self.queries = by_tier or {}, always, []

    def search_comps(self, q):
        self.queries.append(q)
        if self.always is not None:
            return list(self.always)
        return list(self.by_tier.get(len(self.queries), []))

    def get_suburb_stats(self, *a, **k):
        return None

    def resolve_suburb(self, *a, **k):
        return None


def _comp(price, kind=price_kind.PACKAGE, beds=4, land=400.0, url="https://x/1"):
    return RawComp(price=price, price_basis=BASIS_EXACT, price_kind=kind,
                   suburb="Toowoomba", state="QLD", bedrooms=beds, land_sqm=land,
                   provider_url=url)


def _row(price=750_000, **kw):
    base = {"suburb": "Toowoomba", "state": "QLD", "product_type": "House & Land",
            "bedrooms": 4, "land_sqm": 400.0, "price": price}
    base.update(kw)
    return base


def test_a_cheaper_comparable_flags_and_shows_the_buyer_where():
    """The client's stated logic: if comparable stock is cheaper, say so and link to it."""
    comps = [_comp(700_000, url="https://domain/cheap")] + [_comp(800_000) for _ in range(11)]
    r = evaluate(_row(750_000), provider=_Stub(always=comps))
    assert r.verdict == VERDICT_FLAG, r
    assert r.cheapest == 700_000 and r.cheapest_url == "https://domain/cheap"
    assert r.confidence == CONF_HIGH and r.tier == 1


def test_nothing_cheaper_passes_and_shows_the_reference_price():
    comps = [_comp(820_000) for _ in range(12)]
    r = evaluate(_row(750_000), provider=_Stub(always=comps))
    assert r.verdict == VERDICT_PASS, r
    assert "competitively priced" in r.note
    assert r.delta_abs < 0, "a cheaper package should show a negative delta"


def test_a_vacant_block_is_never_a_comparable_for_a_package():
    """The mistake that put $675,000 on a $1,127,000 property, moved to the comp side.

    Land and package medians in our own stock sit 15% apart, so a land price in a package
    cohort does not look wrong to anyone reading the output.
    """
    land = [_comp(310_000, kind=price_kind.LAND_ONLY) for _ in range(20)]
    r = evaluate(_row(750_000), provider=_Stub(always=land))
    assert r.verdict == VERDICT_NONE, "a block of dirt was accepted as a comparable house"
    assert r.n_comps == 0


def test_a_loose_tier_never_drives_the_flag():
    """In any suburb something is always cheaper — a smaller block, a worse pocket, a
    distressed sale. Flagging on a loose-tier cheapest marks ~100% of the catalogue and
    the signal becomes noise."""
    # Tiers 1-3 return nothing; tier 4 finds plenty, one of them cheap.
    stub = _Stub(by_tier={4: [_comp(400_000)] + [_comp(900_000) for _ in range(11)]})
    r = evaluate(_row(750_000), provider=stub)
    assert r.tier == 4
    assert r.verdict == VERDICT_NONE, "a loose-tier cheapest must not flag"
    assert "no claim" in r.note


def test_three_listings_produce_no_verdict_at_all():
    """A wrong confident number is worse than an honest gap."""
    r = evaluate(_row(750_000), provider=_Stub(always=[_comp(700_000) for _ in range(3)]))
    assert r.verdict == VERDICT_NONE
    assert r.confidence == "insufficient"
    assert not r.client_safe


def test_a_withheld_price_is_excluded_and_counted_never_read_as_zero():
    comps = [_comp(800_000) for _ in range(10)]
    comps += [RawComp(price=None, price_basis="", price_kind=price_kind.PACKAGE,
                      suburb="Toowoomba", state="QLD") for _ in range(4)]
    r = evaluate(_row(750_000), provider=_Stub(always=comps))
    assert r.n_comps == 10 and r.n_excluded_no_price == 4
    assert r.median == 800_000, "a withheld price was averaged in as zero"


def test_low_confidence_never_reaches_a_client():
    r = evaluate(_row(750_000), provider=_Stub(by_tier={5: [_comp(900_000) for _ in range(5)]}))
    assert r.confidence == CONF_LOW
    assert not r.client_safe, "a low-confidence comparison must stay internal"


def test_no_provider_configured_is_silent_not_wrong():
    """Until a licence is settled this is what runs, and it must be indistinguishable
    from a provider that found nothing — never a claim."""
    r = evaluate(_row(750_000), provider=NullProvider())
    assert r.verdict == VERDICT_NONE and not r.client_safe
    assert r.median is None


def test_a_provider_outage_degrades_rather_than_failing_the_search():
    class _Broken:
        name = "broken"

        def search_comps(self, q):
            raise RuntimeError("provider down")

    r = evaluate(_row(750_000), provider=_Broken())
    assert r.verdict == VERDICT_NONE, "an outage must not raise into a user's search"


def test_an_unknown_price_kind_is_never_benchmarked():
    row = _row(750_000, product_type="", land_price=0, build_price=0)
    r = evaluate(row, provider=_Stub(always=[_comp(700_000) for _ in range(20)]))
    assert r.verdict == VERDICT_NONE
    assert "not recorded" in r.note


def run_all():
    tests = [
        ("a cheaper comparable flags", test_a_cheaper_comparable_flags_and_shows_the_buyer_where),
        ("nothing cheaper passes", test_nothing_cheaper_passes_and_shows_the_reference_price),
        ("vacant land is never a comparable", test_a_vacant_block_is_never_a_comparable_for_a_package),
        ("a loose tier never flags", test_a_loose_tier_never_drives_the_flag),
        ("three listings give no verdict", test_three_listings_produce_no_verdict_at_all),
        ("a withheld price is excluded", test_a_withheld_price_is_excluded_and_counted_never_read_as_zero),
        ("low confidence stays internal", test_low_confidence_never_reaches_a_client),
        ("no provider is silent, not wrong", test_no_provider_configured_is_silent_not_wrong),
        ("an outage degrades", test_a_provider_outage_degrades_rather_than_failing_the_search),
        ("unknown price kind is not benchmarked", test_an_unknown_price_kind_is_never_benchmarked),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] competitive: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] competitive: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
