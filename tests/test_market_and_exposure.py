"""
Benchmark B (market context) and the catalogue exposure rollup.

The rule these hold in place: a weak comparison must never become a confident sentence in
a document a buyer acts on.
"""

import price_kind
from benchmark_competitive import VERDICT_FLAG, VERDICT_NONE, VERDICT_PASS
from benchmark_market import evaluate as market
from catalogue_exposure import report, summarise
from comp_provider import BASIS_EXACT, NullProvider, RawComp


class _Stub:
    name = "stub"

    def __init__(self, comps, stats=None):
        self.comps, self.stats = comps, stats

    def search_comps(self, q):
        return list(self.comps)

    def get_suburb_stats(self, *a, **k):
        return self.stats

    def resolve_suburb(self, *a, **k):
        return None


def _comp(price, new=True, kind=price_kind.PACKAGE):
    return RawComp(price=price, price_basis=BASIS_EXACT, price_kind=kind,
                   suburb="Toowoomba", state="QLD", bedrooms=4, land_sqm=400.0,
                   is_new_build=new, provider_url="https://x/1")


def _row(price=750_000, **kw):
    base = {"suburb": "Toowoomba", "state": "QLD", "product_type": "House & Land",
            "bedrooms": 4, "land_sqm": 400.0, "price": price}
    base.update(kw)
    return base


def test_a_new_build_comp_set_is_preferred_and_says_so():
    ctx = market(_row(750_000), provider=_Stub([_comp(800_000) for _ in range(12)]),
                 as_at="07/08/2026")
    assert ctx.benchmarked and not ctx.established_comps_used
    assert ctx.avg_market_price == 800_000
    assert ctx.classification == "Below Market Value", ctx.classification
    assert ctx.client_safe, "a strong new-build comparison should be presentable"
    assert "07/08/2026" in ctx.data_note, "a benchmark without its date is not a benchmark"


def test_established_comps_are_flagged_and_never_client_safe():
    """A new turnkey package carries a premium over a 1990s house on the same street.

    Benchmarking against established stock makes it look expensive every time — fine for
    the competitive check, misleading in a report that tells a buyer the buy is sound.
    """
    ctx = market(_row(750_000), provider=_Stub([_comp(600_000, new=False) for _ in range(12)]))
    assert ctx.benchmarked and ctx.established_comps_used
    assert not ctx.client_safe, "an established-only comp set must not make a client claim"
    assert "established" in ctx.data_note


def test_a_thin_comp_set_makes_no_market_claim():
    ctx = market(_row(750_000), provider=_Stub([_comp(800_000) for _ in range(3)]))
    assert not ctx.benchmarked
    assert not ctx.client_safe
    assert ctx.avg_market_price is None


def test_no_provider_produces_context_but_never_a_claim():
    ctx = market(_row(750_000), provider=NullProvider())
    assert not ctx.benchmarked and not ctx.client_safe
    assert ctx.value_score_contribution == 7.5, "an unbenchmarked lot scores neutrally"


def test_a_land_comp_never_reaches_the_market_benchmark_either():
    land = [_comp(310_000, kind=price_kind.LAND_ONLY) for _ in range(20)]
    ctx = market(_row(750_000), provider=_Stub(land))
    assert not ctx.benchmarked, "a vacant block priced a house-and-land package"


def test_exposure_rolls_verdicts_up_by_builder():
    cheap = _Stub([_comp(500_000)] + [_comp(900_000) for _ in range(11)])
    rows = [_row(750_000, builder_name="Alpha"), _row(750_000, builder_name="Alpha"),
            _row(760_000, builder_name="Beta")]
    s = summarise(rows, provider=cheap, by="builder")
    assert s["Alpha"]["n"] == 2 and s["Beta"]["n"] == 1
    assert s["Alpha"][VERDICT_FLAG] == 2, s["Alpha"]


def test_exposure_never_reports_no_verdict_as_a_clean_bill():
    """Rolling "not checked" together with "passed" is exactly the laundering this
    codebase keeps refusing."""
    rows = [_row(750_000, builder_name="Alpha") for _ in range(3)]
    s = summarise(rows, provider=NullProvider(), by="builder")
    assert s["Alpha"][VERDICT_NONE] == 3 and s["Alpha"][VERDICT_PASS] == 0
    text = report(s, "builder")
    assert "not a clean bill of health" in text


def run_all():
    tests = [
        ("new-build comps preferred", test_a_new_build_comp_set_is_preferred_and_says_so),
        ("established comps are flagged", test_established_comps_are_flagged_and_never_client_safe),
        ("a thin set makes no claim", test_a_thin_comp_set_makes_no_market_claim),
        ("no provider, no claim", test_no_provider_produces_context_but_never_a_claim),
        ("land never prices a package", test_a_land_comp_never_reaches_the_market_benchmark_either),
        ("exposure rolls up by builder", test_exposure_rolls_verdicts_up_by_builder),
        ("no verdict is not a pass", test_exposure_never_reports_no_verdict_as_a_clean_bill),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] market: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] market: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
