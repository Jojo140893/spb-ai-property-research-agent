"""The CSV reader must read a fact out of the column its header names.

The xlsx reader and the PDF-table reader both build a column map. This one passed rows as
`(text, links)` -- two members, no columns -- so _column_suburb could never fire, and a
sheet whose header literally reads

    ESTATE,  REGION,LOT No,  SUBURB,TITLES,LAND M2,LAND $,  HOUSE,  FACADE,Bed,...
    Grange,  West,56,  Werribee,Titled,400,"$400,000",  Province 22,  Aspen,4,...

produced no locations at all while printing Werribee, Tarneit, Thornhill Park, Beveridge,
Clyde North and Manor Lakes down the page.
"""
from sources.spreadsheet_extract import extract_from_csv

SHEET = (
    "House & Land Packages,,,,,,,,\n"
    "ESTATE,  REGION,LOT No,  SUBURB,TITLES,LAND M2,LAND $,  HOUSE,HOUSE m2\n"
    'Grange,  West,56,  Werribee,Titled,400,"$400,000",  Province 22,201.10\n'
    'Solara Rise,  West,114,  Tarneit,Titled,365,"$400,000",  Custom,186.66\n'
    'Thornhill Gardens,  West,313,  Thornhill Park,Titled,271,"$313,500",  LX 18E,172.84\n'
)


def test_the_suburb_column_is_read():
    out = extract_from_csv(SHEET.encode(), source_label="t", builder_hint="Luxton")
    got = {str(l.get("suburb") or "") for l in out}
    assert "Werribee" in got, got
    assert "Tarneit" in got, got
    assert "Thornhill Park" in got, got


def test_a_sheet_with_no_suburb_column_still_extracts():
    """The map is an addition, not a requirement — a sheet without one must not break."""
    plain = (
        "ESTATE,LOT No,LAND M2,LAND $,HOUSE m2\n"
        'Grange,56,400,"$400,000",201.10\n'
    )
    out = extract_from_csv(plain.encode(), source_label="t", builder_hint="X")
    assert out, "a sheet with no suburb column produced nothing at all"


def test_a_header_repeated_further_down_restates_the_columns():
    """Stocklists repeat their header for each block; the later one must not be ignored."""
    two_blocks = SHEET + (
        "LOT No,  SUBURB,LAND $\n"
        '99,  Beveridge,"$350,000"\n'
    )
    out = extract_from_csv(two_blocks.encode(), source_label="t", builder_hint="X")
    assert "Beveridge" in {str(l.get("suburb") or "") for l in out}


# ------------------------------------- a misspelling is still the source naming a suburb
def test_a_misspelled_suburb_column_value_is_kept_for_later_correction():
    """The Luxton sheet writes "Michleham" in a column headed SUBURB for nineteen lots. It
    is Mickleham with one letter changed, and suburb_quality corrects exactly that class —
    but it never got the chance, because _column_suburb required a locality match before
    the value was stored. The column was discarded at extraction and the row reached the
    database with no suburb at all.

    Storing it is safe because suburb_quality.resolve blanks anything it cannot verify.
    That is what keeps GARAGE and TBA out of the published column regardless of what the
    extractor kept."""
    from sources.spreadsheet_extract import _column_suburb

    assert _column_suburb("Michleham") == "Michleham"
    assert _column_suburb("Soutbank") == "Soutbank"


def test_the_placeholders_that_column_actually_holds_are_still_refused():
    """"TBA" and "Various" are one alphabetic word each and pass any test based on the
    SHAPE of a place name, so they are named outright."""
    from sources.spreadsheet_extract import _column_suburb

    for junk in ("TBA", "TBC", "Various", "N/A", "Unknown", "Pending", "None"):
        assert _column_suburb(junk) is None, junk


def test_a_figure_or_a_code_is_still_refused():
    from sources.spreadsheet_extract import _column_suburb

    for junk in ("$450,000", "Stage 2", "House & Land", "NSW", "Lot 12", "2026"):
        assert _column_suburb(junk) is None, junk


def test_the_misspelling_resolves_once_the_row_has_its_state():
    """End to end: the extractor keeps it, resolve corrects it."""
    import suburb_quality

    kept = "Michleham"
    assert suburb_quality.resolve({"suburb": kept, "state": "VIC"}) == (
        "Mickleham", suburb_quality.FROM_TYPO)
