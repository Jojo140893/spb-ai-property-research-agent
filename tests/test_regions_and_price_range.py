"""A region name means the area, and a search has two ends.

Coleen searched "Geelong" for a $845,000 townhouse and got nothing, then diagnosed it
herself: "Geelong shows region here not the suburb maybe its not showing in the app?".
Of the 60 Geelong-area listings the catalogue holds, 24 are filed under Armstrong Creek
and the rest under Lara, Leopold and Kensington — only 4 say "Geelong".
"""
import sys

sys.path.insert(0, "api")

import _candidates
import _regions


class FakeIndex:
    loaded = True
    NEAR = {"geelong": [("Armstrong Creek", 10.0), ("Lara", 15.0), ("Leopold", 9.0),
                        ("Ballan", 45.0)]}

    def suburbs_within_radius(self, origin, state, radius_km):
        return [{"suburb": s, "distance_km": d}
                for s, d in self.NEAR.get(str(origin).lower(), []) if d <= radius_km]


def test_a_region_widens_to_its_suburbs():
    out, expanded = _regions.expand(["Geelong"], "VIC", FakeIndex())
    assert "Armstrong Creek" in out and "Lara" in out and "Leopold" in out
    assert out[0] == "Geelong", "the name typed stays first"
    assert expanded["Geelong"] == ["Armstrong Creek", "Lara", "Leopold"]


def test_it_stops_at_the_region_edge():
    """A region search should widen to the towns that trade as part of that market, not
    to everything within an hour's drive. Ballan is 45 km out and Geelong reaches 25."""
    out, _ = _regions.expand(["Geelong"], "VIC", FakeIndex())
    assert "Ballan" not in out


def test_a_suburb_that_is_not_a_region_is_untouched():
    """Box Hill must keep returning Box Hill. This changes every search if it leaks."""
    out, expanded = _regions.expand(["Box Hill"], "VIC", FakeIndex())
    assert out == ["Box Hill"]
    assert expanded == {}


def test_it_only_adds_suburbs_the_catalogue_holds():
    """A region can otherwise add two hundred localities to a search with stock in five
    of them, and every one becomes a line in the rejection log."""
    out, _ = _regions.expand(["Geelong"], "VIC", FakeIndex(),
                             known={"armstrong creek"})
    assert out == ["Geelong", "Armstrong Creek"]


def test_the_expansion_is_reported_not_silent():
    """Answering a wider question than the one typed has to be visible."""
    _out, expanded = _regions.expand(["Geelong"], "VIC", FakeIndex())
    assert expanded, "the caller must be able to say what the search actually covered"


# ------------------------------------------------------------------ the price range


def _row(price, i):
    return {"state": "VIC", "suburb": "Tarneit", "price": str(price),
            "availability_status": "Available", "product_type": "House & Land",
            "source_doc": "doc", "superseded_by": "", "date_checked": "26/08/2026",
            "content_hash": "h%d" % i, "lot_address": "Lot %d" % i, "bedrooms": "4"}


def test_a_minimum_excludes():
    rows = [_row(400000, 1), _row(750000, 2), _row(850000, 3)]
    packs, counts = _candidates.build_packages(
        {"state": "VIC", "budget_min": 700000, "budget_max": 900000}, rows)
    prices = sorted(float(p["advertised_package_price"]) for p in packs)
    assert prices == [750000.0, 850000.0]
    assert counts["under_minimum"] == 1


def test_the_floor_is_exact_with_no_headroom():
    """The ceiling carries BUDGET_HEADROOM because a package a little over is worth
    showing. A consultant who types a minimum has ruled that price out, not nearly."""
    rows = [_row(699999, 1), _row(700000, 2)]
    packs, _counts = _candidates.build_packages(
        {"state": "VIC", "budget_min": 700000, "budget_max": 900000}, rows)
    assert [float(p["advertised_package_price"]) for p in packs] == [700000.0]


def test_no_minimum_behaves_exactly_as_before():
    rows = [_row(60000, 1), _row(850000, 2)]
    packs, counts = _candidates.build_packages({"state": "VIC", "budget_max": 900000}, rows)
    assert len(packs) == 2
    assert counts["under_minimum"] == 0


def test_the_page_sends_both_ends():
    from pathlib import Path

    page = Path(_candidates.__file__).resolve().parents[1].joinpath("index.html").read_text(
        encoding="utf-8")
    assert 'id="budgetMin"' in page and 'id="budgetMax"' in page
    assert "budget_min:" in page, "the page must actually send the floor"
