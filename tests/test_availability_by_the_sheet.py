"""A price list with no status column is itself the availability statement.

Coleen searched Box Hill under $500,000 on 26 Aug and got nothing, while six Irving
Domain apartments at $450k-$499k sat in the catalogue, priced, current, and sourced from
E-Agent. The price list prints Residence, Bedrooms, Bathrooms, Internal, External, Total,
Car, Price, Carpark, OC — and no status column anywhere, because the vendor lists what is
for sale and deletes a unit when it sells.

Blank meant two different things and the filter told them apart the wrong way.
"""
import sys

sys.path.insert(0, "api")

import _candidates


def _row(**kw):
    row = {"state": "VIC", "suburb": "Box Hill", "price": "460000",
           "availability_status": "", "source_doc": "doc-a", "product_type": "Apartment",
           "superseded_by": "", "date_checked": "26/08/2026", "content_hash": "h"}
    row.update(kw)
    return row


def _packages(rows, **brief):
    spec = {"state": "VIC", "budget_max": 500000}
    spec.update(brief)
    return _candidates.build_packages(spec, rows)


def test_a_silent_sheet_is_recommended():
    """The Irving Domain shape: no row on the document says anything about availability."""
    rows = [_row(content_hash="a"), _row(content_hash="b", price="465000")]
    packs, counts = _packages(rows)
    assert len(packs) == 2
    assert counts["available_by_the_sheet"] == 2
    assert counts["availability_unstated"] == 0


def test_a_blank_cell_on_a_sheet_that_does_state_it_is_still_excluded():
    """The other half, and the reason this is not just "include everything". If the
    document says "Available" on its other rows, a blank cell is a real gap — the vendor
    had somewhere to say it and did not."""
    rows = [_row(content_hash="a", availability_status="Available"),
            _row(content_hash="b", availability_status="")]
    packs, counts = _packages(rows)
    assert len(packs) == 1, "only the row that states it"
    assert counts["availability_unstated"] == 1
    assert counts["available_by_the_sheet"] == 0


def test_sold_is_still_sold_on_a_silent_sheet():
    """A document that marks its sold units is not silent, so nothing on it is inferred
    — and the sold row is excluded by the rule that was already there."""
    rows = [_row(content_hash="a", availability_status="Sold"),
            _row(content_hash="b", availability_status="")]
    packs, counts = _packages(rows)
    assert counts["not_available"] == 1
    assert counts["availability_unstated"] == 1, "its sibling's blank is a gap, not a list"
    assert len(packs) == 0


def test_a_row_with_no_document_is_left_out():
    """It cannot be told apart either way, so it keeps the conservative answer."""
    rows = [_row(source_doc="")]
    packs, counts = _packages(rows)
    assert len(packs) == 0
    assert counts["availability_unstated"] == 1


def test_the_entry_says_the_source_never_stated_it():
    """Recommended, but flagged. A consultant quoting this to a buyer should know the
    only evidence it is still for sale is that it is on the list."""
    packs, _ = _packages([_row()])
    assert packs[0]["availability_by_the_sheet"] is True

    packs, _ = _packages([_row(availability_status="Available")])
    assert packs[0]["availability_by_the_sheet"] is False


def test_one_silent_document_does_not_vouch_for_another():
    """The set is keyed per document. A second sheet that DOES state availability must
    not make the first one's blanks look like gaps, or vice versa."""
    # Distinct prices, or _collapse_same_listing folds them into one card — they would
    # otherwise be the same suburb at the same price with no address to tell them apart.
    rows = [_row(content_hash="a", source_doc="silent", price="460000"),
            _row(content_hash="b", source_doc="states-it", price="470000",
                 availability_status="Available"),
            _row(content_hash="c", source_doc="states-it", price="480000",
                 availability_status="")]
    packs, counts = _packages(rows)
    assert counts["available_by_the_sheet"] == 1, "the silent document's row"
    assert counts["availability_unstated"] == 1, "the blank cell on the other document"
    assert len(packs) == 2
