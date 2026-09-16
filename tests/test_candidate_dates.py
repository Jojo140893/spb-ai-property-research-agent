"""A card says when its lot was last checked, never the day somebody searched.

kommo_agent built every CandidateProperty with date_checked = datetime.now(), although
build_packages hands it the row's own date. So on 16 Sep both of Colin's QLD cards read
"16/09/2026" for rows stored on 15/09, a Walloon lot last seen 20/08 would have read the
same, the card's "N days ago -- confirm with the builder" warning (index.html capturedRow)
could never fire, and Clare told callers a lot was "available as at" today.

The rule now: a package that carries date_checked keeps it (a stored row with no readable
date stays blank); only a package with no such key -- a live crawl that set none -- is
dated today, in AEST, the time zone the stock is checked in.
"""
import copy
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
for _p in (str(APP / "api"), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import kommo_agent  # noqa: E402
from tests.test_score_candidates_golden import (  # noqa: E402
    BRIEFS, fresh_agent, gen, packages_for, scratch_config)

TODAY = gen.TODAY                      # 16/09/2026, the day the live replays matched


def _row(**changes):
    """A stored QLD row that passes every gate of a QLD <= $750k brief."""
    row = {
        "builder_name": "Hattan Homes", "lot_address": "Lot 101 Example Street",
        "street_address": None, "lot_number": "101", "suburb": "Riverhills",
        "state": "QLD", "postcode": None, "availability_status": "Available",
        "state_source": "suburb", "price": 553284.0, "land_price": 143389.0,
        "build_price": 409895.0, "bedrooms": 4, "bathrooms": 2, "car_spaces": 2,
        "land_sqm": 350.0, "house_sqm": 180.0, "storey": 1, "title_status": "Titled",
        "estate_name": None, "incentive_amount": None, "incentive_text": None,
        "product_type": "House & Land", "source_channel": "digital email",
        "attribution_scope": None, "date_checked": "10/09/2026",
        "content_hash": "v2:w10-fresh", "superseded_by": None,
        "suburb_source": "stated by the source", "source_doc": "w10-test-document",
    }
    row.update(changes)
    return row


FRESH = _row()
STALE = _row(lot_address="Lot 102 Example Street", lot_number="102", price=563284.0,
             land_price=153389.0, date_checked="20/08/2026",
             content_hash="v2:w10-stale")


@pytest.fixture(scope="module")
def agent():
    with scratch_config():
        yield fresh_agent([FRESH, STALE])


def test_the_shortlist_and_the_pending_entry_keep_their_rows_own_dates(agent):
    import _candidates
    import research
    packages, _counts = _candidates.build_packages(dict(BRIEFS["qld_750k"]),
                                                   [FRESH, STALE], today=TODAY)
    by_hash = {p["content_hash"]: p for p in packages}
    # The premise: one fresh (6 days) and one stale (27 days), as build_packages saw them.
    assert by_hash["v2:w10-fresh"]["verified"] is True
    assert by_hash["v2:w10-stale"]["verified"] is False
    assert (by_hash["v2:w10-fresh"]["age_days"], by_hash["v2:w10-stale"]["age_days"]) == (6, 27)

    result = agent.run_property_research(dict(BRIEFS["qld_750k"]), packages)

    assert [p.content_hash for p in result["shortlist"]] == ["v2:w10-fresh"]
    assert result["shortlist"][0].date_checked == "10/09/2026"
    assert [p.content_hash for p in result["pending_confirmation"]] == ["v2:w10-stale"]
    assert result["pending_confirmation"][0].date_checked == "20/08/2026"
    # What the card and Clare read.
    assert research._shortlist_entry(result["shortlist"][0])["date_checked"] == "10/09/2026"
    # The rest of what the row knew rides along, read with getattr.
    assert result["shortlist"][0].age_days == 6
    assert result["pending_confirmation"][0].age_days == 27
    assert result["shortlist"][0].availability_by_the_sheet is False


class _FrozenUTC(datetime):
    """16/09/2026 23:30 UTC, which is already 17/09/2026 09:30 in AEST."""
    INSTANT = datetime(2026, 9, 16, 23, 30, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.INSTANT.replace(tzinfo=None)      # the function's clock is UTC
        return cls.INSTANT.astimezone(tz)


def _hand_package(**changes):
    package = {
        "lot_address": "Lot 7 Crawled Street", "suburb": "Riverhills", "state": "QLD",
        "builder_name": "Hattan Homes", "bedrooms": 4, "bathrooms": 2, "car_spaces": 2,
        "storeys": 1, "land_size_sqm": 350, "house_size_sqm": 180,
        "land_price": 150000, "build_price": 400000, "advertised_package_price": 550000,
        "verified": True, "risks": [],
    }
    package.update(changes)
    return package


def test_only_a_package_with_no_date_at_all_is_dated_today_in_aest(agent, monkeypatch):
    monkeypatch.setattr(kommo_agent, "datetime", _FrozenUTC)
    packages = [
        _hand_package(lot_address="Lot 1 Crawled Street"),                    # no key
        _hand_package(lot_address="Lot 2 Stored Street", date_checked=""),     # blank row
        _hand_package(lot_address="Lot 3 Stored Street", date_checked=None),
        _hand_package(lot_address="Lot 4 Stored Street", date_checked="03/09/2026"),
    ]
    result = agent.run_property_research(dict(BRIEFS["qld_750k"]), packages)
    dates = {p.lot_address: p.date_checked for p in result["shortlist"]}
    assert dates == {
        "Lot 1 Crawled Street": "17/09/2026",    # AEST, not the UTC 16/09
        "Lot 2 Stored Street": "",               # blank stays blank, never "today"
        "Lot 3 Stored Street": "",
        "Lot 4 Stored Street": "03/09/2026",
    }
    # The property summary each shortlisted lot gets (result["reports"]) says the same.
    # Its own fallback was today, so a row with no readable date still read as checked
    # on the day of the search there.
    summary = {r["address"]: r["summary_markdown"] for r in result["reports"]}
    assert "**Date Checked:** 03/09/2026" in summary["Lot 4 Stored Street"]
    for blank in ("Lot 2 Stored Street", "Lot 3 Stored Street"):
        assert "**Date Checked:** Not recorded at the source" in summary[blank], summary[blank]
        assert "17/09/2026" not in summary[blank] and "16/09/2026" not in summary[blank]


def test_every_card_in_colins_search_carries_its_rows_date():
    """On the subset fixture, QLD <= $750k: shortlist and pending, every one."""
    rows = gen.load_rows(gen.FIXTURE)
    date_of = {r["content_hash"]: r.get("date_checked") for r in rows}
    with scratch_config():
        agent = fresh_agent(rows)
        packages = packages_for(BRIEFS["qld_750k"], rows)
        result = agent.run_property_research(dict(BRIEFS["qld_750k"]),
                                             copy.deepcopy(packages))
    cards = list(result["shortlist"]) + list(result["pending_confirmation"])
    assert result["shortlist"] and result["pending_confirmation"]
    for card in cards:
        assert card.date_checked == date_of[card.content_hash], card.content_hash
    # The stale Walloon lots Colin could not see are dated when they were read.
    assert "20/08/2026" in {c.date_checked for c in result["pending_confirmation"]}
    assert {c.date_checked for c in cards} <= {r.get("date_checked") for r in rows}
