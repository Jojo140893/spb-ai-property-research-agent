"""A long line is judged by its words, not refused for its length (plan rank 7, 7 Oct 2026).

The deployed snapshot carries no source_text: a row's lot_address is its source line cut at
110 characters, and promotion_refusal refused every blank row whose address sat on the cut
("line_truncated"), because a status word might be past the end. On the 6 Oct database that
was 58 Silkwood and Gallery lots, none of whose full lines says sold or contract -- so
Coleen saw them on the builders' lists and not in the app.

build_web now reads the FULL source_text at build time and publishes one word about it,
line_says_gone (True / False / null when there is no text). The search reads that cell:
False promotes, True still refuses, and only a row without the cell is refused for its
length. And a blank row whose whole line says it has gone is refused admission to "Needs
confirming" on the deployed path too, as the SQLite reader (which holds the line) did.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
for _p in (str(APP / "api"), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _candidates as C  # noqa: E402

TODAY = datetime(2026, 9, 16, 12, 0)
# A Silkwood-shaped line: the lot's facts and price fill the first 110 characters, and on
# the gone lot "Under contract" comes after them.
HEAD = ("Lot 1207 Ormeau Vue Estate Ormeau QLD 4208 Stage 12 Double Storey 4 Bed 2 Bath 2 Car "
        "$389,000 $412,400 $801,400 ")
assert len(HEAD) > 110
SHOWN = HEAD[:110]
FULL_GONE = HEAD + "Under contract 02/10"
FULL_OPEN = HEAD + "Titled March 2027"


def _row(content_hash, **kw):
    row = {"content_hash": content_hash, "builder_name": "Silkwood Homes",
           "lot_address": SHOWN, "street_address": None, "lot_number": "1207",
           "suburb": "Ormeau", "state": "QLD", "availability_status": None,
           "state_source": "listing locality", "price": 801400.0, "land_price": None,
           "build_price": None, "bedrooms": 4, "bathrooms": 2, "car_spaces": 2,
           "land_sqm": 400.0, "house_sqm": 220.0, "storey": None, "title_status": None,
           "estate_name": "Ormeau Vue", "incentive_amount": None, "incentive_text": None,
           "product_type": None, "source_channel": "E-Agent", "attribution_scope": None,
           "date_checked": "15/09/2026", "listing_url": None, "floorplan_url": None,
           "brochure_url": None, "source_project_id": None, "superseded_by": None,
           "suburb_source": "stated by the source", "source_doc": "doc-silkwood",
           "price_kind": "package", "text_price_max": 801400.0, "price_in_text": True,
           "email_list": None, "email_first_seen": None, "line_key": None,
           "lot_page_gone": "", "lot_page_status": ""}
    row.update(kw)
    return row


def _list(*blank, read="15/09/2026"):
    """An exceptions-only list, read on `read`: one lot it marks On Hold, the rest blank."""
    held = _row("v2:held", lot_number="1100", lot_address="Lot 1100 Ormeau Vue On Hold",
                availability_status="On Hold", price=700000.0, text_price_max=700000.0,
                line_says_gone=True, date_checked=read)
    return [held] + list(blank)


BRIEF = {"client_name": "Rank 7", "state": "QLD", "budget_min": 0, "budget_max": 900000,
         "preferred_spending_cap": 900000, "primary_suburbs": [], "bedrooms_min": 0,
         "bathrooms_min": 0, "car_spaces_min": 0, "storeys_max": 3, "land_size_min_sqm": 0,
         "house_size_min_sqm": 0, "search_radius_km": 0, "product_type": [],
         "requires_incentive": False, "buyer_type": ""}


@pytest.fixture(autouse=True)
def _available_mode(monkeypatch):
    # The promotion is what line_truncated guards; it exists only in "available" mode (the
    # shipped default since 20 Sep). Pinned so the suite does not depend on the default.
    monkeypatch.setattr(C, "EXCEPTIONS_LIST_MODE", "available")


# ------------------------------------------------------------------ the cell, at build time

def test_the_cell_is_the_whole_lines_verdict():
    assert C.snapshot_line_cells({"source_text": FULL_GONE})["line_says_gone"] is True
    assert C.snapshot_line_cells({"source_text": FULL_OPEN})["line_says_gone"] is False
    # "not sold" is not gone, as text_marks_unavailable reads it.
    assert C.snapshot_line_cells({"source_text": HEAD + "not sold"})["line_says_gone"] is False
    assert C.snapshot_line_cells({"source_text": ""})["line_says_gone"] is None
    assert C.snapshot_line_cells({})["line_says_gone"] is None
    # The word the cut hid is past character 110 -- the premise of the whole fix.
    assert "Under" not in SHOWN and "Under contract" in FULL_GONE


def test_build_web_publishes_it_and_both_readers_carry_it():
    import build_web
    assert "line_says_gone" in build_web.LINE_KEY_FIELDS
    assert "line_says_gone" in build_web.BUILDING_DERIVED_FIELDS
    assert "line_says_gone" not in build_web.BUILDING_FIELDS, "it would reach the SQL SELECT"
    assert "line_says_gone" in C.SNAPSHOT_FIELDS
    # A snapshot DATABASE built before the cell existed computes it from source_text.
    assert "line_says_gone" in C._COMPUTED_SNAPSHOT_FIELDS
    failures = []
    cells = build_web._line_key_cells({"lot_number": "1207", "source_text": FULL_GONE},
                                      failures)
    assert cells["line_says_gone"] is True and failures == []


# ------------------------------------------------------------------ promotion_refusal

@pytest.mark.parametrize("cell, expected", [
    (False, None),                   # the whole line was read and says nothing gone
    (True, "line_truncated"),        # the whole line says Under contract past the cut
    (None, "line_truncated"),        # no cell (an older snapshot): refused for its length
])
def test_a_line_on_the_cut_is_judged_by_its_cell(cell, expected):
    row = _row("v2:blank", line_says_gone=cell)
    if cell is None:
        row.pop("line_says_gone")
    prepared = C.prepare_rows([row])
    assert len(row["lot_address"]) >= C._TRUNCATED_AT
    assert C.promotion_refusal(row, prepared) == expected


def test_a_short_line_with_no_cell_is_promoted_as_before():
    row = _row("v2:short", lot_address="Lot 1207 Ormeau Vue")
    row.pop("line_says_gone", None)
    assert C.promotion_refusal(row, C.prepare_rows([row])) is None


def test_the_full_text_still_wins_where_the_row_has_it():
    # The SQLite reader's rows (and trace_listing's) hold source_text; a gone word in it is
    # read by text_marks_unavailable whatever the cell says.
    row = _row("v2:sqlite", source_text=FULL_GONE, line_says_gone=False)
    assert C.text_marks_unavailable(row) is True
    assert C.text_marks_unavailable(_row("v2:sqlite2", source_text=FULL_OPEN)) is False


# ------------------------------------------------------------------ through the search

def _search(rows, **kw):
    return C.build_packages(dict(BRIEF), rows, today=TODAY, **kw)


def test_a_long_blank_line_that_says_nothing_gone_is_shown():
    """The 58 Silkwood / Gallery lots: promoted to a result, badged as an exceptions-list lot."""
    rows = _list(_row("v2:open", line_says_gone=False))
    packages, counts = _search(rows)
    shown = {p["content_hash"]: p for p in packages}
    assert "v2:open" in shown, counts
    assert shown["v2:open"]["availability_by_exception_list"] is True
    assert not counts.get("not_promoted_line_truncated")


def test_a_long_blank_line_whose_whole_text_says_gone_is_still_refused():
    """The fixture the plan asked for: 'Under contract' after character 110 is refused."""
    rows = _list(_row("v2:gone", line_says_gone=True))
    packages, _counts = _search(rows)
    assert "v2:gone" not in {p["content_hash"] for p in packages}
    # Refused BEFORE the promotion is asked about: its own line says it has gone, which is
    # what the SQLite reader, holding the line, always said of it.
    verdict = dict((r["content_hash"], v) for r, v in C.explain_rows(
        dict(BRIEF), rows, today=TODAY, admit=C.ADMITTABLE_GATES))["v2:gone"]
    assert verdict["detail"]["not_admitted"] == "text_marks_unavailable"
    # ...and it is not offered to confirm either, read a month ago.
    stale = _list(_row("v2:gone", line_says_gone=True, date_checked="20/08/2026"),
                  read="20/08/2026")
    offered, _counts = _search(stale, admit=("availability_unstated",), only_admitted=True)
    assert "v2:gone" not in {p["content_hash"] for p in offered}


def test_without_the_cell_a_long_line_is_refused_as_it_always_was():
    blank = _row("v2:old")
    blank.pop("line_says_gone", None)
    packages, counts = _search(_list(blank))
    assert "v2:old" not in {p["content_hash"] for p in packages}
    assert counts.get("not_promoted_line_truncated") == 1


def test_a_stale_long_line_that_says_nothing_gone_is_offered_to_confirm():
    """Read a month ago: not a result (FRESH_DAYS), but a lot a consultant can ring about."""
    stale = _list(_row("v2:stale", line_says_gone=False, date_checked="20/08/2026"),
                  read="20/08/2026")
    offered, _counts = _search(stale, admit=("availability_unstated",), only_admitted=True)
    assert "v2:stale" in {p["content_hash"] for p in offered}
