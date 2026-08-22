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
    out, _, _, _, _ = SE._rows_to_listings([row], "Palomino Estate - Wyee - NSW", "f.pdf", "")
    assert len(out) == 1
    assert out[0]["suburb"] == "Armstrong Creek"


def test_a_row_with_no_stated_column_still_falls_back_to_the_banner():
    if not _geo_loaded():
        pytest.skip("suburb index not available in this environment")
    row = ("Lot 12 Palomino Titled 350 4 2 2 $820,000", [], {})
    out, _, _, _, _ = SE._rows_to_listings([row], "Palomino Estate - Wyee - NSW", "f.pdf", "")
    assert len(out) == 1
    assert (out[0]["suburb"] or "").lower() == "wyee"


def test_two_member_rows_are_still_accepted():
    """The spreadsheet readers state no columns and were never asked to."""
    row = ("Lot 12 Palomino Titled 350 4 2 2 $820,000", [])
    out, _, _, _, _ = SE._rows_to_listings([row], "", "f.pdf", "")
    assert len(out) == 1


# ------------------------------------------- the locality at the END of a banner

def test_a_banner_whose_whole_segment_is_not_a_locality_still_yields_one():
    """Coleen, 19 Aug: a Donnybrook lot she could see in E-Agent was missing from the app.

    It was there — priced, 3 bedrooms, 238 m2 — with NO SUBURB. Its banner reads
    "Estate : Balmain Donnybrook", which is a single segment, and _NOT_A_LOCALITY matches
    "Estate" inside it, so the whole thing was discarded. "Donnybrook" sat at the end of
    it the entire time.
    """
    if not _geo_loaded():
        pytest.skip("suburb index not available in this environment")
    assert SE._context_suburb("Estate : Balmain Donnybrook", "VIC") == "Donnybrook"
    assert SE._context_suburb("Estate : Banksia Armstrong Creek", "VIC") == "Armstrong Creek"


def test_the_estate_name_trap_still_holds():
    """This is NOT a substring search. Searching anywhere inside a segment yields
    "Bingara" from "Bingara Gorge" — a real town 500 km from those lots. An Australian
    banner ENDS with its locality, so only the tail is read, and the trap has none."""
    if not _geo_loaded():
        pytest.skip("suburb index not available in this environment")
    assert SE._context_suburb("Bingara Gorge - Spec Contract", "NSW") is None
    assert SE._context_suburb("Bingara Gorge", "NSW") is None


@pytest.mark.parametrize("banner", [
    "One Part Contracts", "7 Star Energy Rating",
    "2-Part Detached SS North 704 Balmain Estate", "External Agent Price List",
])
def test_a_banner_that_names_no_place_still_yields_nothing(banner):
    """These were the largest fake-suburb cohorts in the whole database once."""
    if not _geo_loaded():
        pytest.skip("suburb index not available in this environment")
    assert SE._context_suburb(banner, "VIC") is None


def test_the_row_state_is_required_and_is_load_bearing():
    """Measured across the catalogue: a stateless tail is unsafe even when unambiguous.
    "Caboolture Central Springs" tails to "Springs", which exists in exactly ONE state —
    Western Australia — while Caboolture is in Queensland. And "Donnybrook" itself exists
    in QLD, VIC and WA, so the state is what makes the answer a fact rather than a guess.
    """
    if not _geo_loaded():
        pytest.skip("suburb index not available in this environment")
    assert SE._context_suburb("Caboolture Central Springs", "") is None
    assert SE._context_suburb("Caboolture Central Springs", "QLD") is None
    assert SE._context_suburb("Estate : Balmain Donnybrook", "NSW") is None, \
        "Donnybrook is not in NSW; the wrong state must not resolve"


def test_the_longest_tail_wins():
    if not _geo_loaded():
        pytest.skip("suburb index not available in this environment")
    assert SE._context_suburb("Estate : Palomino Armstrong Creek", "VIC") == "Armstrong Creek"


def test_the_whole_segment_rule_still_runs_first():
    """The existing behaviour is untouched — the tail is only a fallback."""
    if not _geo_loaded():
        pytest.skip("suburb index not available in this environment")
    assert (SE._context_suburb("Harvest Hill | 1377 Hue Hue Road, Wyee", "NSW") or "").lower() \
        == "wyee"
