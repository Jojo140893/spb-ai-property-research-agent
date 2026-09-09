"""The portal counts its own stock, and nothing ever read the count back.

Every Proxima project title carries a live counter -- "275 Twelfth Ave Austral (6/6)" --
which sources/proxima.py stores as project_available / project_total. 49 of the 123
projects with live stock state it.

Damac Properties, project 587: 231 lots on the board, all shown as available; the
project's own header says 129 are. A hundred and two properties were being offered to
clients that the portal itself says are gone.

TWO INDEPENDENT SIGNALS AGREE. The header gives a NUMBER; the harvest gives IDENTITIES
(rows the project went on re-listing without). Across the seven projects showing more than
they state, the excess is 130 rows and 128 of them are also rows the latest read missed --
on five of the seven the counts match exactly.
"""
from datetime import datetime, timedelta
import sys

sys.path.insert(0, "api")

import repair_rows as RR

TODAY = datetime(2026, 9, 9)


def _row(days_ago, pid="587", available=129, status="Available", **kw):
    seen = TODAY - timedelta(days=days_ago)
    row = {"id": kw.get("id", 0), "superseded_by": "", "source_channel": "Proxima",
           "source_project_id": pid, "project_available": available,
           "project_total": 267, "builder_name": "Damac Properties Services",
           "availability_status": status, "lot_address": "Unit 1",
           "date_checked": seen.strftime("%d/%m/%Y")}
    row.update(kw)
    return row


def _found(rows):
    return {r["id"] for r in RR.find_project_oversupply(rows, today=TODAY)}


def test_lots_the_project_says_are_gone_and_stopped_listing_are_caught():
    """Three shown, the header says two are available, one was not in today's read."""
    rows = [_row(0, id=1, available=2), _row(0, id=2, available=2),
            _row(6, id=3, available=2)]
    assert _found(rows) == {3}


def test_the_header_alone_is_not_enough():
    """A stale header must not hide stock the project is still listing. Every row here was
    in today's read, so none of them is the one that went."""
    rows = [_row(0, id=1, available=2), _row(0, id=2, available=2),
            _row(0, id=3, available=2)]
    assert _found(rows) == set()


def test_a_missed_read_alone_is_not_enough():
    """Without the header confirming a shortfall, one missed read is not a sale -- that
    judgement belongs to find_withdrawn_from_source and its fortnight of evidence."""
    rows = [_row(0, id=1, available=3), _row(0, id=2, available=3),
            _row(6, id=3, available=3)]
    assert _found(rows) == set()


def test_it_never_hides_more_than_the_portal_says_is_gone():
    """Project 587 has 135 rows the latest read missed but states only 102 too many.
    Marking all 135 would hide 33 lots that are for sale. The portal's number is the
    ceiling, never the floor."""
    rows = [_row(0, id=1, available=2)] + [_row(n, id=10 + n, available=2)
                                           for n in range(1, 6)]
    # 6 shown, header says 2 available -> 4 too many, 5 rows missed the latest read
    assert len(_found(rows)) == 4


def test_the_least_recently_seen_go_first():
    """When the cap bites, the rows to hide are the ones the project has ignored longest.

    Four shown and one stated available means three to hide, and the three chosen are the
    three whose last sighting is oldest -- not an arbitrary three.
    """
    rows = [_row(0, id=1, available=1), _row(2, id=2, available=1),
            _row(9, id=3, available=1), _row(20, id=4, available=1)]
    assert _found(rows) == {2, 3, 4}
    # and with room for only one, it is the oldest of them
    rows = [_row(0, id=1, available=3), _row(2, id=2, available=3),
            _row(9, id=3, available=3), _row(20, id=4, available=3)]
    assert _found(rows) == {4}


def test_a_project_that_states_nothing_is_left_alone():
    """49 of 123 projects state a count. The other 74 are not evidence of anything."""
    rows = [_row(0, id=1, available=None), _row(9, id=2, available=None)]
    assert _found(rows) == set()


def test_a_project_nobody_has_read_lately_is_left_alone():
    rows = [_row(30, id=1, available=1), _row(40, id=2, available=1)]
    assert _found(rows) == set()


def test_rows_already_hidden_do_not_count_toward_the_excess():
    """The comparison is against what the CLIENT sees, not what is stored. Project 161
    holds 305 lots and states 5 available -- and is absent from the finding, because the
    other 300 already carry a sold status."""
    rows = [_row(0, id=1, available=1)] + \
           [_row(9, id=10 + n, available=1, status="Sold") for n in range(5)]
    assert _found(rows) == set()


def test_another_channel_is_untouched():
    """E-Agent price lists have no project counter; this rule is Proxima's alone."""
    rows = [_row(0, id=1, available=1, source_channel="E-Agent"),
            _row(9, id=2, available=1, source_channel="E-Agent")]
    assert _found(rows) == set()


def test_a_superseded_row_is_left_alone():
    rows = [_row(0, id=1, available=1), _row(9, id=2, available=1, superseded_by="gone")]
    assert _found(rows) == set()
