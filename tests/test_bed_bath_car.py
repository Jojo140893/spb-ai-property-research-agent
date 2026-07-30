"""
Tests for reading bed / bath / car out of a stocklist row.

Why this matters more than it looks: a client brief filters on bed/bath/car, so a blank
here makes a lot invisible to the agents. Only 18% of harvested rows carried a bedroom
count, and of 4,192 rows just 75 had bed+bath+car+house-size together — 9 of them
available. The counts were stated in the rows all along; the parser could not read the
notations the builders use.

Every fixture is a real stored row. The REJECT cases matter as much as the accept cases:
each one is a false positive found in the live data, and a wrong bedroom count sends a
buyer to the wrong house.
"""

from sources.feature_extract import parse_bed_bath_car, parse_listing_features


def _triple(text):
    d = parse_bed_bath_car(text)
    return d.get("bedrooms"), d.get("bathrooms"), d.get("car_spaces")


def test_every_notation_the_builders_actually_use():
    cases = {
        # VIC regional stocklists
        "Lot 82 Aberdeen 282 142.8 12.0 Sep-26 Empley 15 3x2x2 $205,000 $335,220 "
        "$540,220 Available": (3, 2, 2),
        "Lot 320 Atticus 126 139.4 4.5 Titled The Collins D 3x2.5x1 $154,007": (3, 2.5, 1),
        # QLD dual-occupancy, CC- stock codes
        "CC-0122 417 Jul-26 173 5 + 5 + 3 FLINDERS SINGLE $ 2,330 $ 121,160 8.03% TBC "
        "$ 850,000": (5, 5, 3),
        # APLACE by Glenville
        "Woodford 4 Windsor Street Park Rise Jan-27 600 Rectangular $520,000 Malanda A 4 "
        "192.75 4 | 2 | 2 $460,780": (4, 2, 2),
        # Verv Projects / Knew Street / Silkwood
        "Verv 4 / 2 / 2 $796,600": (4, 2, 2),
        # FRD / Hudson / Alete / Land Build Direct — the commonest form, 737 rows
        "5 beds / 3 baths / 2 cars $899,000": (5, 3, 2),
        # spelled out with commas
        "Lot 23 Type B4 Middle Lot South 3 bed, 2.5 bath, 1 car 3 168.44m2 $971,000": (3, 2.5, 1),
        # Murcia / The Albertine code forms
        "Murcia 3B2B $625,000": (3, 2, None),
        "The Albertine 3B2B2C $780,000": (3, 2, 2),
    }
    for text, want in cases.items():
        got = _triple(text)
        assert got == want, f"{text[:56]!r}\n  -> {got}, want {want}"


def test_a_bedroom_only_row_gives_only_a_bedroom():
    """Thomas Paul writes "Custom 3BR". The 3 and 3 that follow are bath and car in that
    file, but nothing in the row says so, so only the labelled count is taken."""
    assert _triple("8 Myrtle Place Custom 3BR 3 3 House 451m2 167.9m2 $450,000")[0] == 3


def test_the_traps_that_produced_wrong_counts():
    """Each of these appeared in the live data and each would yield a bed/bath/car triple
    under a naive pattern. None of them may produce one."""
    traps = {
        # a gross yield, and weekly/annual rent
        "CC-0122 417 Jul-26 173 FLINDERS SINGLE $ 2,330 $ 121,160 8.03% TBC $ 850,000 "
        "$ 659,400 $ 1,509,400": "yield and rent",
        # EVO opens with the frontage aspect then the lot number
        "West 210 Sunrise Deanside Available Paris 22 Elite $412,500 $402,070 "
        "$814,570": "frontage aspect + lot",
        # a frontage x depth pair reads as bed x bath
        "Lot 519 Beaumoor Estate 738 209 10.5 x 30 $929,934": "frontage x depth",
        # AVIA's rows end in a Drive link whose file id contains 1X7 and 0X2
        "https://drive.google.com/file/d/1X7abc0X2def/view": "a URL",
        # a grid of bare figures with no delimiter and no label
        "12 Brittlewood 300 26 CTM 5 Yes 3 2 N/A Double $1,430,000": "unlabelled grid",
        # areas and a percentage
        "Land 451m2 House 167.9m2 $450,000 4.2% yield": "areas and a yield",
    }
    for text, why in traps.items():
        got = _triple(text)
        assert got == (None, None, None), f"false positive from {why}: {got}\n  {text[:70]}"


def test_values_outside_a_plausible_range_are_refused():
    for text in ("99x99x99 $500,000", "Lot 5 0 x 0 x 0 $400,000",
                 "design 12 x 14 x 16 $700,000"):
        got = _triple(text)
        assert got[0] in (None,) or 1 <= got[0] <= 10, f"{text} -> {got}"


def test_it_reaches_the_pipeline_through_parse_listing_features():
    """The parser is only useful if every channel picks it up — the stocklist extractor
    calls parse_listing_features, not parse_bed_bath_car directly."""
    f = parse_listing_features(
        "Lot 82 Aberdeen 282 142.8 12.0 Sep-26 Empley 15 3x2x2 $205,000 $335,220 "
        "$540,220 Available", "", 540220)
    assert f.get("bedrooms") == 3, f
    assert f.get("bathrooms") == 2, f
    assert f.get("car_spaces") == 2, f


def run_all():
    tests = [
        ("every builder notation", test_every_notation_the_builders_actually_use),
        ("bedroom-only row", test_a_bedroom_only_row_gives_only_a_bedroom),
        ("the traps that produced wrong counts", test_the_traps_that_produced_wrong_counts),
        ("implausible values refused", test_values_outside_a_plausible_range_are_refused),
        ("reaches the pipeline", test_it_reaches_the_pipeline_through_parse_listing_features),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] specs: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] specs: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
