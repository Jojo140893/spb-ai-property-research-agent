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


def test_a_header_may_carry_street_and_stage_numbers():
    """"152-156 Bancroft Terrace - Stage 1, Deception Bay" has seven digits, and the digit
    ceiling that keeps lot rows out was throwing it away with them — so every lot beneath
    it kept the previous section's suburb."""
    assert _is_group_header("152-156 Bancroft Terrace - Stage 1, Deception Bay", {})
    assert _is_group_header("Park Lane - Stage 1, Joyner", {})
    assert _is_group_header("Paradise in Parkinson - Stage 2, Parkinson", {})


def test_a_wrapped_word_or_a_bare_year_is_not_a_section():
    """"Under offer" and "January 2026" wrap, dropping "offer" and "2026" onto lines of
    their own. On the FRD Homes stocklist those two took the estate of 35 rows."""
    for fragment in ("offer", "2026", "2026 RH", "hold", "pending"):
        assert not _is_group_header(fragment, {}), fragment


def test_the_page_disclaimer_is_not_a_section():
    """Short, digit-free, and at the foot of every page — it passed every other test and
    claimed 6 rows."""
    for line in ("For further details or clarification, please contact us.",
                 "All images, renders and floor plans are conceptual, indicative only",
                 "Prices subject to change without notice. E&OE"):
        assert not _is_group_header(line, {}), line


def test_a_lot_row_is_still_never_a_section():
    """The relaxations above must not start swallowing stock."""
    for row in ("Available Lot 20 388.0 m2 $630,000 January 2027 House Baron LH",
                "Available 61 House COPPER 21 197.08m2 469.0 m2 4 beds $1,167,260"):
        assert not _is_group_header(row, {}), row


def test_the_banners_own_postcode_supplies_the_missing_state():
    """The rule that a locality needs a state is what keeps 'Springs' from landing in WA.
    It also refused whole documents that state their suburb perfectly well.

    The Goldstate list has no state column at all and names its section as
    "Toolern Waters , Melton South 3338" — and 3338 is a Victorian postcode, so the
    locality beside it is the Victorian one and the ambiguity the rule guards against is
    not present. 11 of 89 rows carried a suburb before this; 73 after."""
    from sources.spreadsheet_extract import _context_suburb
    assert _context_suburb("Toolern Waters , Melton South 3338", "") == "Melton South"
    assert _context_suburb("Lyra Estate, Beveridge 3753", "") == "Beveridge"
    assert _context_suburb("Mambourin Green Estate , Mambourin 3024", "") == "Mambourin"


def test_a_stated_state_still_wins_over_the_postcode():
    from sources.spreadsheet_extract import _context_suburb
    assert _context_suburb("Toolern Waters , Melton South 3338", "VIC") == "Melton South"
    # A banner whose postcode belongs to another state cannot name a locality for THIS
    # row's state -- the row's own state is the stronger claim and is checked first.
    assert _context_suburb("Somewhere, Melton South 3338", "QLD") != "Melton South"


def test_two_postcodes_in_one_banner_prove_nothing():
    """A range, or two projects on one line, is not evidence of a single state."""
    from sources.spreadsheet_extract import _state_from_banner_postcode
    assert _state_from_banner_postcode("Melton South 3338") == "VIC"
    assert _state_from_banner_postcode("Stage 3338 and 4000") == ""
    assert _state_from_banner_postcode("no postcode here") == ""


def test_the_paperwork_between_a_section_and_its_lots_is_not_a_section():
    """The AVIA sheet prints its deposit terms and an EOI link under every estate banner:

        KINMA VALLEY - MORAYFIELD
        Land:  Conditional Offer: 3% purchase price deposit ...
        EOI Form
        Lot No.,Stage,Street,Land Size,...
        573,Stage 20,,412m2,"$470,000",...

    Each of those middle lines was read as a NEW section, so the estate above them was
    thrown away and the lots below inherited "EOI Form". 59 rows on one sheet."""
    for line in ("EOI Form", "EOI Form Link", "Unconditional Contracts Only",
                 "Land:  5% Deposit at Contract Signing",
                 "Expression of Interest", "5% balance of deposit payable within 14 days"):
        assert not _is_group_header(line, {}), line


def test_the_estate_banner_around_them_still_is_one():
    for line in ("KINMA VALLEY - MORAYFIELD", "AIRE RIVERSIDE ESTATE - LILYWOOD",
                 "RIVERRBANK ESTATE - CABOOLTURE SOUTH"):
        assert _is_group_header(line, {}), line


def test_a_line_that_begins_mid_sentence_is_a_continuation():
    """PDF text comes out wrapped, and the tail of a paragraph lands on its own line.
    "subject to final positioning on the site and may" is short, digit-free and has none
    of the words the prose test looks for — it read as a section and took the estate of
    27 rows. A banner starts with a capital, a digit or a symbol; never a lowercase word.
    """
    for tail in ("subject to final positioning on the site and may",
                 "and may vary from those shown",
                 "or as otherwise agreed in writing"):
        assert not _is_group_header(tail, {}), tail


def test_a_wrapped_column_header_is_not_a_section():
    """These lists print their column row over two lines — "Status Lot/ Building Design
    Name ... Package Price" then "Street # Type" — and only the first is recognised as a
    column header. The orphan is short, capitalised and digit-free."""
    for line in ("Street # Type", "Lot No Stage Street Land Size",
                 "Design Facade Beds Storeys"):
        assert not _is_group_header(line, {}), line


def test_a_real_banner_made_of_ordinary_words_survives_both_rules():
    """The vocabulary test must key on the WHOLE line being column words, not on any of
    them appearing — plenty of estates are named after streets and parks."""
    for line in ("Millfield Rise, Millfield NSW", "Park Lane - Stage 1, Joyner",
                 "House and Land Packages, Adelaide", "Southern Suburbs, Seaford"):
        assert _is_group_header(line, {}), line
