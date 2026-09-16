"""
The published snapshot carries what each row's own text says about its price.

The deployed search reads stock.json, which never contains source_text (build_web reads
it and drops it). So a rule that has to ask "does this price list actually print this
price, and is it the whole price?" -- the exceptions-only admission of unstated rows into
"Needs confirming" -- can only work if the answer is computed at build time and shipped
as three small cells: price_kind, text_price_max and price_in_text.

The texts below are the real lines behind Colin's QLD <= $750k search (17 Sep DB copy),
quoted as stored. The one exception is the Dawn lot's Adobe share link, which the stored
text splits across a space: the real link is a capability URL and no part of it is
committed, so a made-up id of the same shape stands in, with digit runs in it on purpose
(real share ids carry them) so the test can tell whether links are stripped.
"""

import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))

import price_kind  # noqa: E402
from price_kind import price_evidence  # noqa: E402


# id 11878: Shape Homes, Dawn estate, Walloon QLD, lot 1228. Real stock that must stay
# eligible: the text prints $721,900 and nothing larger.
DAWN_1228 = {
    "price": 721900.0, "land_price": 270000.0, "build_price": 451900.0,
    "product_type": None, "estate_name": "Dawn, Walloon",
    "source_text": ("1228 Available Medium Density Bates Street Tulipwood 4-2-1-1 150.02 "
                    "210 7.5 $270,000 $451,900 $721,900 "
                    "https://acrobat.adobe.com/id/urn:aaid:sc:AP:88412345- "
                    "b1c2-4d3e-a5f6-12a9876543b0 Sep-26 16/09/2026 2 Part"),
    # lot_address is the first 120 characters, so it ends in a truncated link.
    "lot_address": ("1228 Available Medium Density Bates Street Tulipwood 4-2-1-1 150.02 "
                    "210 7.5 $270,000 $451,900 $721,900 https:/"),
}

# id 330: South Ripley duplex. $725,000 is one half of a $1,505,615 total.
SOUTH_RIPLEY_330 = {
    "price": 725000.0, "product_type": None, "estate_name": "South Ripley Providence",
    "source_text": ("DUPLEX PR8638 545 South Ripley Providence Estate Registered 534 "
                    "$725,000 Offers Over 780615 1505615 1240 0.0439 Custom 297 3 + 3 "
                    "Finance Considered"),
    "lot_address": ("DUPLEX PR8638 545 South Ripley Providence Estate Registered 534 "
                    "$725,000 Offers Over 780615 1505615 1240 0.043"),
}

# id 329: Redbank Plains duplex. $732,285 is one component of $1,327,285.
REDBANK_329 = {
    "price": 732285.0, "land_price": 595000.0, "product_type": None,
    "estate_name": "Redbank Plains Sienna Eden",
    "source_text": ("DUPLEX PR8735 106 Redbank Plains Sienna Eden Estate 2026-09-01 "
                    "00:00:00 505 $595,000 $732,285 732285 1327285 1400 0.0548 Glenyoy "
                    "261 3 + 3 Finance Considered"),
    "lot_address": ("DUPLEX PR8735 106 Redbank Plains Sienna Eden Estate 2026-09-01 "
                    "00:00:00 505 $595,000 $732,285 732285 1327285 1"),
}

# id 15897: Heathwood. A build contract price, filed with product type House & Land.
HEATHWOOD_15897 = {
    "price": 390639.0, "product_type": "House & Land", "estate_name": "Heathwood The Crest",
    "source_text": ("PR8831 8 Heathwood The Crest Estate September 2026 400 Build Only "
                    "$390,639 $820 Brighton 191 4/2/2/2"),
    "lot_address": ("PR8831 8 Heathwood The Crest Estate September 2026 400 Build Only "
                    "$390,639 $820 Brighton 191 4/2/2/2"),
}

# id 19812: Caboolture. Two estates merged into one garbled line.
CABOOLTURE_19812 = {
    "price": 382201.0, "product_type": "House & Land",
    "estate_name": "Caboolture Central Springs",
    "source_text": ("PR0022 33 Caboolture Central Springs Estate PR8949 $534 Burpengary "
                    "Sage Estate Registered $262 45000000.00% $382,201.00 30/09/2025"),
    "lot_address": ("PR0022 33 Caboolture Central Springs Estate PR8949 $534 Burpengary "
                    "Sage Estate Registered $262 45000000.00% $3"),
}

# id 15899: Silkwood, Wacol. The text prints the price and nothing larger.
WACOL_15899 = {
    "price": 595000.0, "product_type": "House & Land", "estate_name": "Wacol Maplewood",
    "source_text": "ENQUIRE NOW 5 Wacol Maplewood Estate Registered $515.00 $595,000",
    "lot_address": "ENQUIRE NOW 5 Wacol Maplewood Estate Registered $515.00 $595,000",
}


# ------------------------------------------------------------------ the real rows

def test_dawn_lot_prints_its_own_price_and_its_share_link_is_not_a_price():
    ev = price_evidence(DAWN_1228)
    assert ev["price_in_text"] is True
    # Not 88,412,345 (the share id), not 9,876,543 (glued inside the id's hex tail), not 2026.
    assert ev["text_price_max"] == 721900, ev
    assert ev["price_kind"] == price_kind.PACKAGE


def test_south_ripley_duplex_text_prints_the_larger_total():
    ev = price_evidence(SOUTH_RIPLEY_330)
    assert ev["text_price_max"] == 1505615, ev
    assert ev["price_in_text"] is True


def test_redbank_plains_duplex_text_prints_the_larger_total():
    ev = price_evidence(REDBANK_329)
    assert ev["text_price_max"] == 1327285, ev
    assert ev["price_in_text"] is True


def test_heathwood_is_a_build_only_price():
    assert price_evidence(HEATHWOOD_15897)["price_kind"] == price_kind.BUILD_ONLY


def test_caboolture_garbled_percentage_counts_as_a_number_on_the_line():
    ev = price_evidence(CABOOLTURE_19812)
    assert ev["text_price_max"] >= 45_000_000, ev
    assert ev["price_in_text"] is True


def test_wacol_prints_its_own_price_and_nothing_larger():
    ev = price_evidence(WACOL_15899)
    assert ev["price_in_text"] is True
    assert ev["text_price_max"] == 595000, ev


def test_an_ocr_line_whose_dollar_signs_read_as_s_still_shows_its_larger_price():
    """id 17691 (QLD, stored $2,085,000, no status): the scan reads each "$" as "S", so
    the larger price is glued to a letter. Refusing it as "glued" left text_price_max
    equal to the stored price, and the row looked like a whole price that it is not.
    A thousands separator is what tells a price from a share id; letters do not."""
    row = {"price": 2085000.0, "product_type": "Apartment",
           "source_text": "Price $ 事 3,425,000.00S $ 2,085,000.00$ S 1,180,000.00$",
           "lot_address": "Price $ 事 3,425,000.00S $ 2,085,000.00$ S 1,180,000.00$"}
    ev = price_evidence(row)
    assert ev["text_price_max"] == 3425000, ev
    assert ev["price_in_text"] is True
    # ...and a hex share id still is not a price, grouped or not.
    assert price_evidence({"price": 500000.0, "source_text": "$500,000 651a087190a4"})[
        "text_price_max"] == 500000


# ------------------------------------------------------------------ what is stripped

def test_a_price_glued_to_a_link_is_kept_and_links_without_a_scheme_are_dropped():
    row = {"price": 721900.0,
           "source_text": ("$721,900https://acrobat.adobe.com/id/urn:aaid:sc:AP:88412345- "
                           "acrobat.adobe.com/id/99999999 (www.example.com.au/p/12345678)")}
    ev = price_evidence(row)
    assert ev["text_price_max"] == 721900, (ev, price_kind.evidence_text(row))
    assert ev["price_in_text"] is True


def test_a_long_line_with_no_spaces_is_read_in_linear_time():
    """build_web runs this on every row of a required nightly step: a hang there is worse
    than an exception. A whole-text pattern shaped like \\S*\\.com/\\S* took 9 s on a
    20,000-character token and 35 s on 40,000; per-token matching takes milliseconds."""
    import time
    junk = "x" + "a1." * 15_000 + " " + "9" * 20_000 + " " + "1,000" * 5_000
    started = time.perf_counter()
    price_evidence({"price": 500000.0, "source_text": junk + " $500,000", "lot_address": junk})
    assert time.perf_counter() - started < 2.0


def test_a_giant_garbled_number_stays_valid_json():
    """float('9' * 400) is infinity, and json.dumps writes infinity as `Infinity`, which
    no browser will parse: one garbled cell would break the whole published snapshot."""
    import json
    ev = price_evidence({"price": 500000.0, "source_text": "$500,000 " + "9" * 400})
    assert ev["text_price_max"] == price_kind._MAX_AMOUNT, ev
    json.dumps(ev, allow_nan=False)


def test_phone_numbers_are_not_prices():
    """A sales contact run together is a ten-digit number bigger than any price."""
    row = dict(DAWN_1228, source_text=DAWN_1228["source_text"]
               + " Call Sam 0412345678 or the office +61731234567 or 1300 123 456",
               lot_address="")
    ev = price_evidence(row)
    assert ev["text_price_max"] == 721900, ev
    assert ev["price_in_text"] is True


def test_product_codes_are_not_prices():
    """E-Agent codes come as "PR8638" and spaced, "PR 45" (id 11765). A spaced six-digit
    code would otherwise read as a bigger total and hide a real lot as a component."""
    row = {"price": 725000.0,
           "source_text": "DUPLEX PR 845120 45 Ripley Sunnyvue Estate Registered $725,000"}
    ev = price_evidence(row)
    assert ev["text_price_max"] == 725000, ev


def test_a_bare_total_beside_a_lot_size_is_not_mistaken_for_a_phone_number():
    """"1300000 250" is a $1.3m total next to a 250 m2 lot, not 1300 000 250."""
    ev = price_evidence({"price": 650000.0, "source_text": "Lot 12 650000 1300000 250 Duplex"})
    assert ev["text_price_max"] == 1300000, ev


def test_dates_are_removed_before_numbers_are_read():
    """dd/mm/yyyy and ISO dates (id 329 prints "2026-09-01 00:00:00") are not amounts.
    Checked on the cleaned text itself: under the six-digit rule no date can currently
    outgrow a price, so the maximum alone could not show the strip had stopped working."""
    row = {"price": 612000.0,
           "source_text": "Available 16/09/2026 titled 2026-09-01 00:00:00 due 1.10.2026 $612,000"}
    cleaned = price_kind.evidence_text(row)
    for date in ("16/09/2026", "2026-09-01", "00:00:00", "1.10.2026"):
        assert date not in cleaned, cleaned
    assert "$612,000" in cleaned
    assert price_evidence(row)["text_price_max"] == 612000


def test_money_in_thousands_and_millions():
    ev = price_evidence({"price": 989000.0,
                         "source_text": "Fw: $989K - SECONDARY DWELLING, total $1.51m, 450 m2"})
    assert ev["price_in_text"] is True
    assert ev["text_price_max"] == 1510000, ev


def test_small_bare_numbers_are_not_prices():
    """Lot numbers, sizes, postcodes and years never count towards the largest price."""
    ev = price_evidence({"price": 0, "source_text": "Lot 1228 4305 QLD 2026 150.02 m2 4-2-1-1"})
    assert ev["text_price_max"] is None, ev
    assert ev["price_in_text"] is False


def test_a_price_the_text_does_not_print_is_not_in_text():
    """A portal row whose price came from structured data (id 5132 shape)."""
    ev = price_evidence({"price": 1312319.0, "source_text": "Alambie Estate, Lot 11",
                         "lot_address": "Alambie Estate, Lot 11"})
    assert ev == {"price_kind": price_kind.UNKNOWN, "text_price_max": None,
                  "price_in_text": False}


@pytest.mark.parametrize("row", [{}, {"price": None, "source_text": None},
                                 {"price": "abc", "source_text": "$500,000"}])
def test_missing_or_junk_values_do_not_raise(row):
    ev = price_evidence(row)
    assert set(ev) == {"price_kind", "text_price_max", "price_in_text"}
    assert ev["price_in_text"] is False


def test_values_are_compact_for_the_snapshot():
    """721900, not 721900.0: the cell is paid for on every published row."""
    import json
    ev = price_evidence(WACOL_15899)
    assert json.dumps(ev["text_price_max"]) == "595000"


# ------------------------------------------------------------------ build_web

def test_the_three_fields_are_exported_but_never_selected():
    import build_web
    for f in build_web.PRICE_EVIDENCE_FIELDS:
        assert f in build_web.BUILDING_DERIVED_FIELDS, f
        assert f not in build_web.BUILDING_FIELDS, f"{f} would reach the SQL SELECT"
    assert build_web.PRICE_EVIDENCE_FIELDS == ["price_kind", "text_price_max", "price_in_text"]
    build_web._check("buildings", build_web.BUILDING_FIELDS + build_web.BUILDING_DERIVED_FIELDS)


def test_build_web_cells_match_price_evidence():
    import build_web
    failures = []
    assert build_web._price_evidence_cells(dict(SOUTH_RIPLEY_330), failures) == {
        "price_kind": "unknown", "text_price_max": 1505615, "price_in_text": True}
    assert failures == []


def test_a_row_that_breaks_the_evidence_does_not_stop_the_build(monkeypatch):
    """build_web.py is a required nightly step; an exception there withholds a whole day
    of fresh stock. The row publishes nulls and the failure is recorded instead."""
    import build_web

    def boom(row):
        raise ValueError("unparseable vendor line")

    monkeypatch.setattr(price_kind, "price_evidence", boom)
    failures = []
    cells = build_web._price_evidence_cells({"content_hash": "v2:bad", "price": 1}, failures)
    assert cells == {"price_kind": None, "text_price_max": None, "price_in_text": None}
    assert failures == [("v2:bad", "ValueError: unparseable vendor line")]


def test_failures_mark_the_run_degraded_without_exiting(tmp_path, capsys):
    import build_web
    marker = tmp_path / "_run_degraded"
    build_web._report_price_evidence_failures([], marker)
    assert not marker.exists(), "a clean build must not mark the run degraded"
    build_web._report_price_evidence_failures(
        [("v2:bad", "ValueError: x"), ("v2:worse", "TypeError: y")], marker)
    text = marker.read_text(encoding="utf-8")
    assert "price evidence failed on 2 row(s)" in text
    assert "v2:bad" in text
    assert "ERROR" in capsys.readouterr().out


def test_run_daily_keeps_the_build_web_line_when_another_step_also_failed(tmp_path, monkeypatch):
    """build_web appends to the marker during the run; run_daily used to write_text the
    marker from its own list at the end, which erased that line on any night another
    optional step failed too, and its DEGRADED email never named it."""
    import inspect
    import run_daily
    marker = tmp_path / "_run_degraded"
    monkeypatch.setattr(run_daily, "_DEGRADED_MARKER", marker)
    monkeypatch.setattr(run_daily, "_DEGRADED", ["5/7  Benchmark each listing"])
    assert run_daily._lines_already_in_degraded_marker() == []
    marker.write_text("7/7  Build the deployed snapshot: price evidence failed on 2 row(s)\n"
                      "5/7  Benchmark each listing\n", encoding="utf-8")
    assert run_daily._lines_already_in_degraded_marker() == [
        "7/7  Build the deployed snapshot: price evidence failed on 2 row(s)"]
    # The chain lives in _chain(), called from main(). Read from the file rather than
    # inspect.getsource, which stops early inside these functions.
    src = Path(inspect.getsourcefile(run_daily)).read_text(encoding="utf-8")
    main = src[src.index("def _chain("):]
    fold = main.index("_DEGRADED.extend(_lines_already_in_degraded_marker())")
    assert fold < main.index("if _DEGRADED:") < main.index("_DEGRADED_MARKER.write_text(")
    assert main.index("_DEGRADED_MARKER.unlink(") < fold, "the marker must be cleared first"


def test_evidence_is_taken_before_the_text_is_dropped():
    """source_text is popped inside build()'s row loop. Evidence computed after that
    line would see no text and publish text_price_max null on every row."""
    src = (APP / "build_web.py").read_text(encoding="utf-8")
    body = src[src.index("def build(out_dir"):]
    call = body.index("_price_evidence_cells(b, _evidence_failures)")
    drop = body.index('for _internal in ("source_text", "source_url", "stocklist_file")')
    assert call < drop, "price evidence is computed after source_text is popped"
    assert '"price_evidence_failures": _evidence_failures' in body
    main = src[src.index('if __name__ == "__main__":'):]
    assert "_report_price_evidence_failures(" in main
