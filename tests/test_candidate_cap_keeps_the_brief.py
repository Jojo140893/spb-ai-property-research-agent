"""The cap must not throw away the stock the search asked for.

Candidates are sorted by price ascending and cut to MAX_CANDIDATES, and the suburb filter
runs afterwards in scoring_engine. So the cheapest 600 listings in the whole state were
all a search could ever see, whatever suburb was typed.

Measured on the live snapshot before the fix: in VIC the dearest listing that reached
scoring was $666,800 and 2,254 were unreachable; NSW $886,500 and 2,060; QLD $1,216,360
and 478. About half the catalogue could not be returned by any search.

Coleen reported the symptom exactly, on a $845,000 Geelong townhouse: "tried search in the
app result not found, change the price to 900000, still not showing". Raising the ceiling
made it worse, because a higher budget admits more cheap stock.
"""
import sys

sys.path.insert(0, "api")

import _candidates


def _row(suburb, price, i):
    return {"state": "VIC", "suburb": suburb, "price": str(price),
            "availability_status": "Available", "product_type": "House & Land",
            "source_doc": "doc", "superseded_by": "", "date_checked": "26/08/2026",
            "content_hash": "h%d" % i, "lot_address": "Lot %d" % i, "bedrooms": "4"}


def _catalogue():
    """One dear listing in the wanted suburb, buried under more cheap ones than the cap."""
    # A REAL locality, or suburb_quality refuses the filler rows as not-a-place and
    # the cap never bites. Tarneit is VIC and nowhere near Geelong.
    rows = [_row("Tarneit", 400000 + i, i) for i in range(_candidates.MAX_CANDIDATES + 50)]
    rows.append(_row("Geelong", 845000, 999999))
    return rows


def test_the_wanted_suburb_survives_the_cap():
    packs, counts = _candidates.build_packages(
        {"state": "VIC", "budget_max": 900000, "primary_suburbs": ["Geelong"]}, _catalogue())
    assert any(str(p.get("suburb")) == "Geelong" for p in packs), (
        "the one listing in the requested suburb was cut by the price cap")
    assert counts["truncated"] > 0, "the premise: this catalogue is bigger than the cap"


def test_without_the_fix_it_would_have_been_cut():
    """The premise, stated as a test: sorted by price, the wanted listing is past the cap."""
    rows = _catalogue()
    dear = [r for r in rows if r["suburb"] == "Geelong"][0]
    cheaper = sum(1 for r in rows if float(r["price"]) < float(dear["price"]))
    assert cheaper > _candidates.MAX_CANDIDATES


def test_the_cap_still_bounds_the_matrix():
    """It exists for a reason — the scoring matrix is what it protects."""
    packs, _counts = _candidates.build_packages(
        {"state": "VIC", "budget_max": 900000, "primary_suburbs": ["Geelong"]}, _catalogue())
    assert len(packs) == _candidates.MAX_CANDIDATES


def test_a_search_with_no_suburb_is_unchanged():
    """Nothing was asked for, so nothing is preferred — the old behaviour exactly."""
    packs, _counts = _candidates.build_packages(
        {"state": "VIC", "budget_max": 900000}, _catalogue())
    assert len(packs) == _candidates.MAX_CANDIDATES
    assert all(str(p.get("suburb")) == "Tarneit" for p in packs)


def test_a_radius_search_is_left_alone():
    """With a radius the suburb is not what the match is made on, so preferring exact
    suburb names would quietly bias a search that deliberately asked to look wider."""
    packs, _counts = _candidates.build_packages(
        {"state": "VIC", "budget_max": 900000, "primary_suburbs": ["Geelong"],
         "search_radius_km": 25}, _catalogue())
    assert len(packs) == _candidates.MAX_CANDIDATES
    assert all(str(p.get("suburb")) == "Tarneit" for p in packs)


def test_results_are_still_returned_cheapest_first():
    packs, _counts = _candidates.build_packages(
        {"state": "VIC", "budget_max": 900000, "primary_suburbs": ["Geelong"]}, _catalogue())
    prices = [float(p["advertised_package_price"]) for p in packs
              if p.get("advertised_package_price")]
    assert prices == sorted(prices), "the shortlist must still read cheapest first"
