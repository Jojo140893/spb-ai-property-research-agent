"""The shortlist could not return half the catalogue, and the half it dropped was the
half the client asked for.

MAX_CANDIDATES bounds the scoring matrix. It used to take the 600 CHEAPEST rows in the
state, and the suburb filter ran afterwards -- so a search could only ever see the cheap
end. That was fixed for a brief that NAMES suburbs (Coleen's $845,000 Geelong townhouse).

A state-wide brief has no suburbs, so nothing was reordered and the cheapest still won.
Measured on the live snapshot on 10 Sep 2026: "VIC, four bedrooms, under $900,000" has
1,336 candidates, 784 of which genuinely have four bedrooms -- and the cheapest 600
contained just 271 of them. The scorer never saw a four-bedroom home above $714,190 from
a client who said they would pay $900,000. 511 matching homes could not be returned by
any search.
"""
import sys

import pytest

sys.path.insert(0, "api")

import _candidates as C


@pytest.fixture(autouse=True)
def _the_valve_is_set(monkeypatch):
    """7 Oct 2026: the cap is an emergency valve, off by default (every candidate is scored).
    What these tests pin is how it spends its slots WHEN it is set, so they set the 600."""
    monkeypatch.setattr(C, "MAX_CANDIDATES", 600)


def _rows(n, beds=4, first=400000, step=1000, state="VIC", suburb="Tarneit", tag="a"):
    # `price` is the column the pipeline reads (api/_candidates.py:1148); the package it
    # builds exposes the same figure as advertised_package_price.
    # A REAL locality: the pipeline drops a row whose suburb is not one
    # (suburb_not_a_locality), which silently emptied this fixture at first.
    return [{"state": state, "suburb": suburb, "bedrooms": beds,
             "price": first + i * step,
             "availability_status": "Available", "superseded_by": "",
             "lot_address": f"{tag} Lot {i}", "builder_name": "A Builder",
             "source_channel": "E-Agent", "date_checked": "09/09/2026"}
            for i in range(n)]


def _scored(brief, rows):
    picks, counts = C.build_packages(brief, rows)
    return picks, counts


def test_matching_stock_is_kept_ahead_of_cheaper_stock_that_cannot_match():
    """A row that already fails a minimum the consultant typed is the cheapest thing to
    give up -- scoring_engine hard-rejects it on arrival anyway."""
    cheap_but_wrong = _rows(C.MAX_CANDIDATES, beds=1, first=300000, tag="cheap")
    dear_but_right = _rows(200, beds=4, first=800000, tag="dear")
    picks, _ = _scored({"state": "VIC", "budget_max": 1500000, "bedrooms_min": 4,
                        "search_radius_km": 0}, cheap_but_wrong + dear_but_right)
    kept4 = sum(1 for p in picks if (p.get("bedrooms") or 0) >= 4)
    assert kept4 == 200, f"only {kept4} of the 200 matching homes survived the cap"


def test_the_kept_slots_cover_the_whole_budget_not_just_its_cheap_end():
    """784 matching homes and 600 slots: cheapest-first still ends far below what the
    client said they would pay. A consultant is choosing between options."""
    rows = _rows(900, beds=4, first=500000, step=500)     # $500,000 - $949,500
    picks, _ = _scored({"state": "VIC", "budget_max": 1000000, "bedrooms_min": 4,
                        "search_radius_km": 0}, rows)
    prices = sorted(float(p["advertised_package_price"]) for p in picks)
    assert len(prices) == C.MAX_CANDIDATES
    dearest_available = 500000 + 899 * 500
    assert prices[-1] > dearest_available * 0.95, (
        f"the dearest reaching the scorer is ${prices[-1]:,.0f}; the client's budget "
        f"reaches ${dearest_available:,.0f}")


def test_nothing_is_reordered_when_the_matching_stock_already_fits():
    """No cap, no behaviour change -- the common case must be untouched."""
    rows = _rows(50, beds=4, first=500000)
    picks, counts = _scored({"state": "VIC", "budget_max": 1000000, "bedrooms_min": 4,
                             "search_radius_km": 0}, rows)
    assert counts["truncated"] == 0
    prices = [float(p["advertised_package_price"]) for p in picks]
    assert prices == sorted(prices), "price order was disturbed without need"


def test_a_brief_with_no_minimums_is_left_alone():
    """Nothing was asked for, so nothing can fail to match; the old behaviour stands."""
    rows = _rows(C.MAX_CANDIDATES + 100, beds=4, first=400000)
    picks, counts = _scored({"state": "VIC", "budget_max": 2000000,
                             "search_radius_km": 0}, rows)
    assert counts["truncated"] == 100
    prices = [float(p["advertised_package_price"]) for p in picks]
    assert prices == sorted(prices)


def test_a_named_suburb_still_wins_over_the_new_rule():
    """The earlier fix is the more specific one and must keep precedence: a consultant who
    names a suburb has said where, and that is a stronger statement than a bedroom count."""
    here = _rows(100, beds=1, first=300000, suburb="Geelong", tag="here")
    elsewhere = _rows(C.MAX_CANDIDATES + 200, beds=4, first=250000, tag="else")
    picks, _ = _scored({"state": "VIC", "budget_max": 2000000, "bedrooms_min": 4,
                        "primary_suburbs": ["Geelong"], "search_radius_km": 0},
                       here + elsewhere)
    kept_geelong = sum(1 for p in picks if str(p.get("suburb")).lower() == "geelong")
    assert kept_geelong == 100, f"only {kept_geelong} of 100 Geelong rows survived"


def test_a_row_missing_the_fact_is_not_counted_as_meeting_it():
    """An unknown bedroom count is not a four-bedroom home."""
    assert C._meets_binding({"bedrooms": None}, {"bedrooms_min": 4}, {"bedrooms"}) == 0
    assert C._meets_binding({"bedrooms": 3}, {"bedrooms_min": 4}, {"bedrooms"}) == 0
    assert C._meets_binding({"bedrooms": 4}, {"bedrooms_min": 4}, {"bedrooms"}) == 1


def test_a_house_size_minimum_is_actually_read_off_the_package():
    """The bug every other fixture in this file was blind to.

    REQUIRED_FACTS calls the fact "house_sqm"; a built package stores the figure as
    "house_size_sqm". _meets_binding read the fact name straight off the package, found
    nothing, and scored every row 0 -- so a row meeting the client's minimum sorted
    identically to one failing it, and the cap collapsed back to the 600 cheapest. It
    failed silently, and the other six tests here all set ONLY bedrooms_min, so every one
    of them passed with the bug present.
    """
    big = _rows(200, beds=4, first=800000, tag="big")
    for r in big:
        r["house_sqm"] = 200                       # the column the snapshot carries
    small = _rows(C.MAX_CANDIDATES, beds=4, first=300000, tag="small")
    for r in small:
        r["house_sqm"] = 90
    picks, _ = _scored({"state": "VIC", "budget_max": 1500000, "bedrooms_min": 4,
                        "house_size_min_sqm": 150, "search_radius_km": 0}, big + small)
    kept = sum(1 for p in picks if (p.get("house_size_sqm") or 0) >= 150)
    assert kept == 200, (
        f"only {kept} of the 200 homes meeting the house-size minimum survived the cap; "
        f"the minimum is not being read off the package")


def test_every_required_fact_can_be_found_on_a_package():
    """The general form of it: a fact with no key on the package scores every row 0 and
    silently disables that half of the ordering."""
    rows = _rows(20, beds=4, first=500000)
    for r in rows:
        r.update(house_sqm=200, bathrooms=2, car_spaces=2)
    picks, _ = _scored({"state": "VIC", "budget_max": 1000000, "search_radius_km": 0}, rows)
    assert picks
    for fact, _label in C.REQUIRED_FACTS:
        key = C.PACKAGE_KEY_FOR[fact]
        assert key in picks[0], f"{fact!r} maps to {key!r}, which no package carries"
