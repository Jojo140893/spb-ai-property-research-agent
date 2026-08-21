"""Section headers in a multi-page stocklist, and the two lines that used to destroy them.

A section header sets the suburb for every lot BENEATH it, so a line wrongly accepted as
one does not lose a suburb — it steals one, from every row until the next real header.

Measured on the Hudson Homes stocklist, 104 lots across twelve suburbs: 57 of 104 rows
had a suburb before these two fixes and 101 after. The document reads

    Hudson Homes Stocklist                      <- title, repeated on every page
    21/08/2026
    NSW
    Status Lot/ Building Design Name ...        <- column header
    Watagan Park - Display Homes, Cooranbong    <- section
    Available 7 House MARIGOLD 35 ... $1,519,500
    Jensen Rise Estate - Wadalba, Wadalba       <- section
    Available 61 House COPPER 21 ... $1,167,260
"""
from sources.spreadsheet_extract import _is_group_header, _rows_to_listings


def test_a_bare_state_is_not_a_section():
    """"NSW" sits three lines under the title of every stocklist this builder publishes.
    Accepted as a section it took the estate of 26 rows."""
    assert not _is_group_header("NSW", {})
    assert not _is_group_header("VIC", {})


def test_a_wrapped_cell_is_not_a_section():
    """"MAGNESIUM 37 - DUPLEX" breaks across two lines and DUPLEX lands on its own. It
    was recorded as the estate of 18 rows."""
    for fragment in ("DUPLEX", "Dual Key", "House", "Townhouse", "Available", "Total"):
        assert not _is_group_header(fragment, {}), fragment


def test_an_encoding_fragment_is_not_a_section():
    """A square-metre glyph comes through a PDF as junk and was stored as an estate."""
    for fragment in ("m", "m2", "-", ":"):
        assert not _is_group_header(fragment, {}), fragment


def test_a_real_section_header_still_is_one():
    for header in ("Jensen Rise Estate - Wadalba, Wadalba",
                   "Watagan Park - Display Homes, Cooranbong",
                   "Mt View Grange - Bellbird, Bellbird",
                   "Millfield Rise, Millfield NSW"):
        assert _is_group_header(header, {}), header


def test_the_running_title_does_not_reset_the_section():
    """THE ONE THAT SCALED WITH PAGE COUNT.

    A multi-page stocklist prints its title as a running header. Each repeat reset the
    context, so every lot after a page break inherited "Hudson Homes Stocklist" instead
    of the estate it sits in — 39 of 104 rows on one document, and the longer the price
    list the more of it loses its suburb.
    """
    rows = [
        ("Hudson Homes Stocklist", []),
        ("Jensen Rise Estate - Wadalba, Wadalba", []),
        ("Available 61 House COPPER 21 469.0 m2 4 beds $1,167,260", []),
        ("Hudson Homes Stocklist", []),                       # page 2's running header
        ("Available 66 House Gallium 20 450.0 m2 4 beds $1,151,300", []),
    ]
    out, context, banner = _rows_to_listings(rows, "", "test", "")
    assert len(out) == 2, out
    assert "Wadalba" in context, context
    assert all("Wadalba" in str(listing.get("estate_context") or listing.get("suburb") or
                                context) for listing in out)


def test_a_genuinely_new_section_still_replaces_the_old_one():
    """The guard is against the TITLE repeating, not against sections changing."""
    rows = [
        ("Hudson Homes Stocklist", []),
        ("Jensen Rise Estate - Wadalba, Wadalba", []),
        ("Available 61 House COPPER 21 469.0 m2 $1,167,260", []),
        ("Mt View Grange - Bellbird, Bellbird", []),
        ("Available 527 House COPPER 19A 753.0 m2 $941,230", []),
    ]
    _out, context, _banner = _rows_to_listings(rows, "", "test", "")
    assert "Bellbird" in context, context
