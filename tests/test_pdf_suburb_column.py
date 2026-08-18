"""The Suburb COLUMN of a stocklist PDF, which used to be flattened away.

_pdf_rows_from_tables called table.extract() and then _row_to_text() on the result, so
every cell boundary was gone before anything read it. That is fine for price, bed and
bath — those parse out of prose — but a locality does not: once "Armstrong" is one word
in a row of fourteen, nothing can tell it from the estate name beside it. 3,070 live rows
carried no suburb while the PDF they were read from stated one in its own column.

The header names in these fixtures are the real ones an E-Agent stocklist publishes.
The lots, estates and prices are invented; the localities are real because the whole
point of the guard is that a value is checked against the suburb index.
"""

import pytest

import sources.spreadsheet_extract as SE


HEADER = ["Lot", "Suburb", "Estate", "Title Status", "Land Size", "Land Price",
          "Contract Type", "Bed", "Bath", "Car", "House Size", "House Price",
          "Package Price", "Rebate"]


def _table(*data_rows):
    return [HEADER, *data_rows]


# ------------------------------------------------------------ finding the column

def test_the_suburb_column_is_found_by_its_own_header():
    cm = SE._column_map(_table(["Lot 1", "Armstrong", "Palomino", "", "", "", "",
                                "4", "2", "2", "", "", "$780,000", ""]))
    assert cm == {1: "suburb"}


def test_a_header_further_down_the_page_is_not_hunted_for():
    """A stocklist repeats its header per page, never halfway down one.

    Scanning on would start matching wordy lot rows.
    """
    filler = [["Lot %d" % i, "Armstrong", "", "", "", "", "", "4", "2", "2",
               "", "", "$700,000", ""] for i in range(6)]
    assert SE._column_map([*filler, HEADER]) == {}


def test_a_table_with_no_locality_column_states_nothing():
    assert SE._column_map([["Lot", "Estate", "Bed", "Bath", "Package Price"],
                           ["Lot 3", "Palomino", "4", "2", "$800,000"]]) == {}


@pytest.mark.parametrize("name", ["Suburb", "SUBURB", "suburbs", "Locality", "Town"])
def test_the_spellings_builders_actually_use(name):
    hdr = ["Lot", name, "Bed", "Bath", "Car", "Package Price"]
    assert SE._column_map([hdr, ["Lot 1", "Armstrong", "4", "2", "2", "$1"]]) == {1: "suburb"}


@pytest.mark.parametrize("name", ["City", "Area", "Region", "State"])
def test_headers_that_are_not_a_locality_column(name):
    """"City" is the nearest capital in these files, and "Area" is a land size."""
    hdr = ["Lot", name, "Bed", "Bath", "Car", "Package Price"]
    assert SE._column_map([hdr, ["Lot 1", "Sydney", "4", "2", "2", "$1"]]) == {}


# --------------------------------------------------- the value is still checked

def test_a_stated_suburb_must_still_be_a_real_locality():
    """The column is better evidence than a banner, not self-certifying evidence.

    These are the values that actually appear in that column on live stocklists.
    """
    for junk in ("", None, "TBA", "Various", "House & Land", "NSW", "Stage 2"):
        assert SE._column_suburb(junk) is None


def test_a_real_locality_is_returned_title_cased():
    if not _geo_loaded():
        pytest.skip("suburb index not available in this environment")
    assert SE._column_suburb("ARMSTRONG CREEK") == "Armstrong Creek"


def _geo_loaded() -> bool:
    SE._column_suburb("x")            # forces the lazy index load
    return bool(SE._BANNER_GEO and SE._BANNER_GEO.loaded)


# ------------------------------------------------------- end to end through the parser

def test_the_stated_column_beats_the_banner():
    """An estate can span two towns; the banner names only one of them."""
    if not _geo_loaded():
        pytest.skip("suburb index not available in this environment")
    row = ("Lot 12 Armstrong Creek Palomino Titled 350 4 2 2 $820,000",
           [], {"suburb": "Armstrong Creek"})
    out, _, _ = SE._rows_to_listings([row], "Palomino Estate - Wyee - NSW", "f.pdf", "")
    assert len(out) == 1
    assert out[0]["suburb"] == "Armstrong Creek"


def test_a_row_with_no_stated_column_still_falls_back_to_the_banner():
    if not _geo_loaded():
        pytest.skip("suburb index not available in this environment")
    row = ("Lot 12 Palomino Titled 350 4 2 2 $820,000", [], {})
    out, _, _ = SE._rows_to_listings([row], "Palomino Estate - Wyee - NSW", "f.pdf", "")
    assert len(out) == 1
    assert (out[0]["suburb"] or "").lower() == "wyee"


def test_two_member_rows_are_still_accepted():
    """The spreadsheet readers state no columns and were never asked to."""
    row = ("Lot 12 Palomino Titled 350 4 2 2 $820,000", [])
    out, _, _ = SE._rows_to_listings([row], "", "f.pdf", "")
    assert len(out) == 1
