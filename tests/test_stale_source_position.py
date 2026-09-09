"""Coleen clicked "Price list - row 24" and a different property opened.

Third report of the same thing. The listing was genuine and the pointer had been right
when it was written on 2 Sep; Tomorrow Homes re-sorts that workbook as stock sells, so by
the 9th row 24 held a Woodlea lot at Aintree instead.

Ahsan, 9 Sep: "you should verify the row every day and if it is removed from that row it
means it is sold or removed."

Two guarantees, tested here and in test_source_position_is_reread.py:
  a row the harvest keeps finding has its number rewritten by every read, and
  a row the harvest stopped finding stops naming a line at all.
"""
from datetime import datetime, timedelta
import sys

sys.path.insert(0, "api")

import repair_rows as RR

TODAY = datetime(2026, 9, 9)


def _row(days_ago, doc="doc-a", channel="E-Agent", **kw):
    seen = TODAY - timedelta(days=days_ago)
    row = {"id": kw.get("id", days_ago), "superseded_by": "", "source_channel": channel,
           "source_url": doc, "stocklist_file": "", "builder_name": "Tomorrow Homes",
           "lot_address": "2102 Racecourse Rd, Fraser Rise", "price": 724841,
           "source_row": 24, "source_page": None,
           "date_checked": seen.strftime("%d/%m/%Y")}
    row.update(kw)
    return row


def _found(rows):
    return {r["id"] for r in RR.find_stale_source_position(rows, today=TODAY)}


def test_a_row_number_todays_read_did_not_confirm_is_cleared():
    """The sheet was re-read today; this lot was not in it, so row 24 is now a stranger."""
    rows = [_row(0, id=1), _row(0, id=2), _row(7, id=3)]
    assert _found(rows) == {3}


def test_a_row_the_read_still_finds_keeps_its_number():
    """Every row re-read on the same pass is exactly where it says it is."""
    assert _found([_row(0, id=1), _row(0, id=2)]) == set()


def test_one_missed_read_is_enough_to_stop_pointing():
    """Unlike retirement, which waits a fortnight. Saying "row 24" when today's read did
    not put it there is simply false, and costs nothing to stop saying."""
    assert _found([_row(0, id=1), _row(1, id=2)]) == {2}


def test_a_list_nobody_has_read_lately_keeps_its_numbers():
    """If the file itself has not been opened, its silence proves nothing about any row."""
    assert _found([_row(9, id=1), _row(30, id=2)]) == set()


def test_a_row_with_no_position_has_nothing_to_clear():
    rows = [_row(0, id=1), _row(7, id=2, source_row=None, source_page=None)]
    assert _found(rows) == set()


def test_a_page_number_is_a_position_too():
    """PDF price lists point at a page; it rots the same way a row does."""
    rows = [_row(0, id=1), _row(7, id=2, source_row=None, source_page=3)]
    assert _found(rows) == {2}


def test_another_builders_sheet_says_nothing_about_this_one():
    rows = [_row(0, id=1, doc="doc-b"), _row(7, id=2, doc="doc-a")]
    assert _found(rows) == set()


def test_an_inbox_is_left_alone():
    """A quiet inbox is not a re-listing. Same gate as retirement, one rule for both."""
    rows = [_row(0, id=1, channel="digital email"),
            _row(7, id=2, channel="digital email")]
    assert _found(rows) == set()


def test_a_superseded_row_is_left_alone():
    rows = [_row(0, id=1), _row(7, id=2, superseded_by="gone")]
    assert _found(rows) == set()
