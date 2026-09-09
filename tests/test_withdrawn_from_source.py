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


def test_one_portal_project_says_nothing_about_another():
    """Every Proxima row stores the same source_url -- the projects index -- so without
    the project id in the key all 3,928 of them were treated as ONE list, and one project
    going quiet was read as evidence about the other thirty-nine. That is the "a channel
    is far too broad" error this repair says it avoids, and it had already retired 151
    Proxima listings on it.
    """
    rows = [_row(0, id=1, channel="Proxima", doc="index", source_project_id="776"),
            _row(0, id=2, channel="Proxima", doc="index", source_project_id="776"),
            _row(20, id=3, channel="Proxima", doc="index", source_project_id="901")]
    assert _found(rows) == set(), "a busy project retired a quiet project's stock"


def test_a_quiet_lot_inside_a_busy_project_is_still_retired():
    """The fix narrows the evidence; it does not switch retirement off."""
    rows = [_row(0, id=1, channel="Proxima", doc="index", source_project_id="776"),
            _row(0, id=2, channel="Proxima", doc="index", source_project_id="776"),
            _row(20, id=3, channel="Proxima", doc="index", source_project_id="776")]
    assert _found(rows) == {3}


def test_a_row_with_no_project_id_still_groups_by_its_document():
    rows = [_row(0, id=1), _row(0, id=2), _row(20, id=3)]
    assert _found(rows) == {3}


def test_a_list_that_came_back_missing_most_of_itself_retires_nothing():
    """One Proxima project returned 3 of its 85 lots on 9 Sep -- the accordion's 80-lot
    render cap, or a dropped session. The fortnight rule would have retired the other 82
    as sold. A half-read list is not a sale."""
    rows = [_row(0, id=0)] + [_row(20, id=n) for n in range(1, 30)]
    assert _found(rows) == set()


def test_a_list_that_came_back_whole_still_retires_what_it_dropped():
    """The guard must not switch retirement off: a list that returned nearly all of
    itself and left one lot out is exactly the evidence this repair exists for."""
    rows = [_row(0, id=n) for n in range(1, 30)] + [_row(20, id=99)]
    assert _found(rows) == {99}


def test_a_short_list_is_not_judged_by_a_percentage():
    """A 3-row list losing one lot is 33% "missing" and is simply a lot that sold."""
    rows = [_row(0, id=1), _row(0, id=2), _row(20, id=3)]
    assert _found(rows) == {3}


def test_the_half_read_lists_are_reported_not_just_skipped():
    """A source that has quietly stopped returning its stock is a thing to go and fix."""
    rows = [_row(0, id=0)] + [_row(20, id=n) for n in range(1, 30)]
    found = RR.partial_reads(rows, today=TODAY)
    assert len(found) == 1
    _key, missing, total = found[0]
    assert (missing, total) == (29, 30)


def test_a_retired_lot_says_sold_in_the_column_a_person_reads():
    """Ahsan, 9 Sep: "the buildings that are no longer in the sheets are marked sold".

    Superseding already hides the row from the filter, which is most of what he asked for.
    But anyone who ticks "show superseded", opens the export, or reads the row through the
    API still saw it carrying "Available" -- the one thing it is not. Pinned here because
    the write lives in main() and is easy to lose in a refactor.
    """
    import inspect
    import repair_rows
    body = inspect.getsource(repair_rows.main)
    retire = body[body.index("for r, _silent in withdrawn:"):]
    retire = retire[:retire.index("for r, suburb in reread:")]
    assert "availability_status=?" in retire, "retirement no longer states a status"
    assert '"Sold"' in retire, "retirement no longer says Sold"
    assert "status_previous=?" in retire, "the previous status is no longer kept"
