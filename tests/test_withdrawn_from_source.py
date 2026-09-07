"""The app was offering properties that had already sold.

Colin, 7 Sep: he searched Geelong at $650-750k, the app offered a DBN Homes lot at
$668,000, and the builder's live list had a different property on that row at $722,000
marked sold. The $668,000 lot was not on the list at all. It had gone, and we were still
advertising it.

These portals re-list their whole stock every run, so a listing DISAPPEARING is the only
"sold" signal they ever send. Nothing acted on it, so a sold lot stayed live forever.
"""
from datetime import datetime, timedelta
import sys

sys.path.insert(0, "api")

import repair_rows as RR

TODAY = datetime(2026, 9, 7)


def _row(days_ago, doc="doc-a", channel="E-Agent", **kw):
    seen = TODAY - timedelta(days=days_ago)
    row = {"id": kw.get("id", days_ago), "superseded_by": "", "source_channel": channel,
           "source_url": doc, "stocklist_file": "", "builder_name": "DBN Homes",
           "lot_address": "Lot 1", "price": 668000, "date_checked": seen.strftime("%d/%m/%Y")}
    row.update(kw)
    return row


def _found(rows):
    return {r["id"] for r, _silent in RR.find_withdrawn_from_source(rows, today=TODAY)}


def test_a_listing_its_own_list_has_stopped_carrying_is_retired():
    """The list was read today and did not include this one, and has not for a fortnight."""
    rows = [_row(0, id=1), _row(0, id=2), _row(20, id=3)]
    assert _found(rows) == {3}


def test_a_recent_absence_is_not_evidence():
    """A lot can be missing from one run for a hundred reasons. Fourteen days of a list
    being read without it is the bar."""
    rows = [_row(0, id=1), _row(5, id=2), _row(13, id=3)]
    assert _found(rows) == set()


def test_a_list_that_has_gone_quiet_retires_nothing():
    """THE GUARD THAT MATTERS. If the source itself has not been read, its silence says
    nothing about any listing on it — and without this a broken scrape would retire the
    builder's entire stock."""
    rows = [_row(30, id=1), _row(60, id=2)]
    assert _found(rows) == set(), "a stale list must never retire its own rows"


def test_one_builders_silence_says_nothing_about_another():
    """The gate is the row's own DOCUMENT, not its channel. E-Agent carries a hundred
    documents and one going quiet is not evidence about the rest."""
    rows = [_row(0, id=1, doc="live-list"), _row(20, id=2, doc="live-list"),
            _row(40, id=3, doc="abandoned-list")]
    assert _found(rows) == {2}, "only the row on the list that IS being read"


def test_an_inbox_never_retires_anything():
    """A builder emails a price list when they have one. Silence from an inbox is a quiet
    week, not a sold property — the same distinction the harvest and the dashboard make."""
    rows = [_row(0, id=1, channel="digital email"),
            _row(40, id=2, channel="digital email")]
    assert _found(rows) == set()


def test_a_row_already_superseded_is_left_alone():
    rows = [_row(0, id=1), _row(30, id=2, superseded_by="something")]
    assert _found(rows) == set()


def test_the_thresholds_are_where_the_docstring_says():
    assert RR.WITHDRAWN_AFTER_DAYS == 14
    assert RR.SOURCE_READ_WITHIN_DAYS == 3
    assert "digital email" not in RR.RELISTING_CHANNELS
