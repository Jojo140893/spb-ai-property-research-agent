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


def test_a_junk_suburb_is_never_priced_against_its_suburb():
    """The provider must not be asked for the median price of 'GARAGE'.

    This gate tested only that the suburb value was non-empty, and the answer is passed
    to provider.get_suburb_stats and rendered in the client report under "How this
    compares to the suburb". Asking a provider about a parsing accident and printing
    whatever comes back is the worst version of the suburb-column bug.
    """
    class _Counting(_Stub):
        def __init__(self, comps):
            super().__init__(comps)
            self.asked = []

        def search_comps(self, q):
            self.asked.append(q.suburb)
            return list(self.comps)

        def get_suburb_stats(self, suburb, *a, **k):
            self.asked.append(suburb)
            return None

    for junk in ("GARAGE", "IN TERNAL BALCONY TOTAL", "Logan City Council", "2026"):
        prov = _Counting([_comp(800_000) for _ in range(12)])
        ctx = market(_row(750_000, suburb=junk), provider=prov)
        assert prov.asked == [], f"{junk!r} was sent to the provider: {prov.asked}"
        assert not ctx.benchmarked and not ctx.client_safe, junk

    # A real suburb still gets through, and an estate glued to one is unglued first.
    prov = _Counting([_comp(800_000) for _ in range(12)])
    ctx = market(_row(750_000, suburb="Stage 5A, Toowoomba"), provider=prov)
    assert ctx.benchmarked, "a recoverable composite was refused"
    assert prov.asked and all(a == "Toowoomba" for a in prov.asked), prov.asked


def test_exposure_groups_on_the_locality_not_the_raw_column():
    """`--by suburb` printed 'IN TERNAL BALCONY TOTAL' and 'GARAGE' as headings in a
    report about which places our stock is overpriced in."""
    rows = [_row(750_000, suburb="Stage 5A, Toowoomba"),
            _row(750_000, suburb="TOOWOOMBA"),
            _row(750_000, suburb="GARAGE")]
    s = summarise(rows, provider=NullProvider(), by="suburb")
    assert "Toowoomba" in s, s
    assert s["Toowoomba"]["n"] == 2, "case and composite variants must be one group"
    assert not any(k in s for k in ("GARAGE", "Stage 5A, Toowoomba", "TOOWOOMBA")), s


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


def test_the_command_we_tell_the_client_to_run_actually_exists():
    """The single item blocking the whole benchmark is a licence key, and the one
    instruction printed for it was a dead end.

    NullProvider.reason says `python setup_credentials.py domain_api`. That script
    builds its target list from the builder REGISTRY, so domain_api was not in it and
    the command answered "no portal matches 'domain_api'" with a list of seven builder
    logins. A blocking step with no working command behind it stays blocked forever.
    """
    import setup_credentials
    from comp_provider import NullProvider, provider_status

    keys = [k for k, _label, _u, _p in setup_credentials._portals()]
    assert "domain_api" in keys, f"the documented target is missing: {keys}"

    # ...and the reason string still names that exact command.
    assert "setup_credentials.py domain_api" in NullProvider.reason, NullProvider.reason

    # provider_status must answer honestly rather than look configured.
    st = provider_status()
    assert set(st) == {"provider", "live", "reason"}, st
    assert st["live"] is (st["provider"] != "none")


def test_realestate_resolves_to_the_licensed_channel_not_a_scraper():
    """realestate.com.au cannot be read directly and must never be attempted.

    Probed 2026-08-08: the FIRST unauthenticated request returns HTTP 429 carrying a
    Kasada (KPSDK) bot-detection challenge; domain.com.au returns 403. PropTrack is REA
    Group's own API over the same listings, so "realestate" resolves there.
    """
    import os

    import comp_provider
    from comp_provider_proptrack import PropTrackProvider

    for alias in ("realestate", "proptrack"):
        os.environ["SPB_COMP_PROVIDER"] = alias
        comp_provider._ANNOUNCED[0] = False
        # No key stored, so it must degrade to NullProvider rather than half-work.
        assert comp_provider.get_provider(quiet=True).name == "none", alias
    os.environ.pop("SPB_COMP_PROVIDER", None)
    comp_provider._ANNOUNCED[0] = False

    # And the command the message tells you to run has to exist -- the domain_api one
    # did not, and that is how a blocking step stays blocked.
    import setup_credentials
    keys = [k for k, _l, _u, _p in setup_credentials._portals()]
    assert "proptrack_api" in keys, keys

    # Unconfigured, it returns nothing rather than raising or inventing.
    p = PropTrackProvider(key="")
    assert p.configured is False
    assert p.search_comps(comp_provider.CompQuery(
        suburb="Tarneit", state="VIC", price_kind=price_kind.PACKAGE)) == []
    assert p.get_suburb_stats("Tarneit", "VIC") is None


def test_a_withheld_proptrack_price_is_never_read_as_zero():
    """"Contact agent" and an auction with no guide are real and unusable. A zero would
    drag every median it touched."""
    from comp_provider_proptrack import PropTrackProvider as P

    assert P.price_of({"price": 0}) == (None, "")
    assert P.price_of({"displayPrice": "Contact Agent"}) == (None, "")
    assert P.price_of({}) == (None, "")
    assert P.price_of({"price": 812000})[0] == 812000
    mid, basis = P.price_of({"price": {"from": 700000, "to": 800000}})
    assert mid == 750000 and basis, (mid, basis)


def run_all():
    tests = [
        ("new-build comps preferred", test_a_new_build_comp_set_is_preferred_and_says_so),
        ("established comps are flagged", test_established_comps_are_flagged_and_never_client_safe),
        ("a thin set makes no claim", test_a_thin_comp_set_makes_no_market_claim),
        ("no provider, no claim", test_no_provider_produces_context_but_never_a_claim),
        ("land never prices a package", test_a_land_comp_never_reaches_the_market_benchmark_either),
        ("a junk suburb is never priced", test_a_junk_suburb_is_never_priced_against_its_suburb),
        ("the documented setup command exists",
         test_the_command_we_tell_the_client_to_run_actually_exists),
        ("realestate resolves to the licensed channel",
         test_realestate_resolves_to_the_licensed_channel_not_a_scraper),
        ("a withheld price is never zero",
         test_a_withheld_proptrack_price_is_never_read_as_zero),
        ("exposure groups on the locality", test_exposure_groups_on_the_locality_not_the_raw_column),
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
