"""A document's own title, and the four ways reading it goes wrong.

Every case here is a document in the live catalogue, and every guard exists because the
version without it produced a wrong suburb rather than no suburb.
"""
import sys

sys.path.insert(0, "api")

import document_suburb as DS
from sources.spreadsheet_extract import (_context_suburb, _is_column_header,
                                         _is_group_header, _strip_validity,
                                         _address_label)


class FakeIndex:
    """Just enough gazetteer for these cases."""

    PLACES = {
        ("dromana", "VIC"): (-38.338, 144.965),
        ("theodore", "QLD"): (-24.945, 150.077),
        ("stafford", "QLD"): (-27.412, 153.010),
        ("hudson", "QLD"): (-17.525, 145.999),
        ("fraser rise", "VIC"): (-37.700, 144.740),
        ("central park", "VIC"): (-37.868, 145.044),
    }

    def locate(self, name, state=""):
        key = str(name or "").strip().lower()
        if state:
            return self.PLACES.get((key, state.upper()))
        for (n, _s), point in self.PLACES.items():
            if n == key:
                return point
        return None


def _document_says(text, state, builder=""):
    """suburb_for_document against literal text.

    front_page_text only reads PDF and xlsx bytes, so handing it a plain string returns
    "" and every refusal test below would pass without its guard ever running. Replacing
    the reader is what makes these tests mean anything.
    """
    real = DS.front_page_text
    DS.front_page_text = lambda _data: text
    try:
        return DS.suburb_for_document("u", state, FakeIndex(),
                                      fetcher=lambda _u: b"x", builder=builder)
    finally:
        DS.front_page_text = real


# ------------------------------------------------------------------ the validity clause


def test_a_print_date_does_not_stop_a_banner_naming_its_town():
    """"Hali Dromana - Current as of 25/02/2026" is the title of a 34-townhouse price
    list. The date carries eight digits, past the digit ceiling in _is_group_header, so
    the line was not read as a section at all and _context_suburb was handed the whole
    string and found nothing. 7 of 34 named -> 34 of 34."""
    line = "Hali Dromana • Current as of 25/02/2026"
    assert _strip_validity(line) == "Hali Dromana"
    assert _is_group_header(line, {})
    assert _context_suburb(line, "VIC") == "Dromana"


def test_only_a_clause_ending_in_a_date_is_stripped():
    """'Current' is an ordinary word and an estate may use it. A trailing DATE is what
    makes the clause a print stamp rather than part of the name."""
    assert _strip_validity("Currumbin Waters") == "Currumbin Waters"
    assert _strip_validity("Current Release Estate") == "Current Release Estate"
    assert _strip_validity("Aurora Updated August 2026") == "Aurora"


# ------------------------------------------------- column header vs section header


def test_a_column_header_naming_no_place_is_not_a_section():
    """The Solara price list stored "Lot Collection Level Bed Bath Car Store Int Ext
    Total Price" as the estate of all 14 of its apartments."""
    line = "Lot Collection Level Bed Bath Car Store Int Ext Total Price"
    assert _is_column_header(line), "the premise: this IS a column row"
    assert not _is_group_header(line, {})


def test_but_a_column_header_that_names_a_place_still_is_one():
    """The counterweight, and the regression this pairs with: "House and Land Packages,
    Adelaide" is made entirely of column vocabulary and locates 44 lots on the Alete
    stocklist. Vetoing on the column test alone broke it."""
    line = "House and Land Packages, Adelaide"
    assert _is_column_header(line), "the premise: this looks like a column row too"
    assert _is_group_header(line, {}), "but it names a locality, so it is a section"


# -------------------------------------------------------------- above the table only


def test_the_head_stops_at_the_first_column_header():
    text = ("Hali Dromana • Current as of 25/02/2026\n"
            "Sales by Product Type\n"
            "Level Stock ID Configuration Type Aspect Extras Internal Area Price\n"
            "Bayview 14 3 BED 2 BATH 2 CAR TH14 E/S 142 139 281 $1,850,000")
    head = DS._above_the_table(text)
    assert "Dromana" in head
    assert "Bayview" not in head


def test_a_file_that_opens_on_its_table_has_no_title():
    """"Price List_ARCA, Stafford.xlsx" is 35 apartments in Stafford, Brisbane. It has no
    title row: it opens on the column header, and the column after Lot is "Building",
    whose value on every row is "Theodore" -- a real QLD town 500 km north west, and the
    only locality on the page. Reading exactly one locality is not test enough when a
    column of the table can supply it."""
    text = ("Lot Building Floor Product Type Bed Car Internal m2 External m2 Aspect\n"
            "114.0 Theodore Ground 3 Bed + MPR / 2 Bath Type A1 3.0 2.0 97.0 29.0\n"
            "115.0 Theodore Ground 3 Bed + MPR / 2 Bath Type A 3.0 2.0 97.0 18.0")
    assert DS._above_the_table(text) == ""
    assert _document_says(text, "QLD") == "", "Theodore is a column, not the title"


# ------------------------------------------------------------- the builder is not a town


def test_the_builders_own_name_is_not_the_suburb():
    """"Hudson Homes Stocklist" is the whole title of a 19-row list covering Bald Hills,
    Bellbird Park and Bethania. Hudson is a QLD locality 1,500 km north."""
    assert DS._names_the_builder("Hudson", "Hudson Homes")


def test_but_a_project_named_after_its_suburb_keeps_it():
    """E-Agent derives a project's name from its own link label, and those labels
    routinely CONTAIN the suburb. Rejecting on containment threw away six correct
    answers to remove one wrong one; the test is what is LEFT OVER."""
    assert not DS._names_the_builder("Wheelers Hill", "Eastbury Wheelers Hill")
    assert not DS._names_the_builder("Palm Beach", "Villea Palm Beach")
    assert not DS._names_the_builder("Collingwood", "Smith by Valli Collingwood")
    assert not DS._names_the_builder("Rockdale", "Rockdale"), "no leftover, no company"


# ------------------------------------------------------- a street address names a place


def test_a_street_address_inside_the_table_still_names_the_place():
    """The Taylors Run price list says where it is exactly once, on a line inside its
    table: "1012 Taylors Rd, Fraser Rise". Its lots carry no address of their own, so
    nothing else in the pipeline can reach it -- 34 rows."""
    line = "1012 Taylors Rd, Fraser Rise 1008 10.5 28 294 $390,000 DEPOSIT East Q1 2027"
    assert _address_label(line, {}, "") == "1012 Taylors Rd, Fraser Rise"
    assert DS._addressed_place(line) == "Fraser Rise"


def test_a_bare_word_in_a_column_is_not_an_addressed_place():
    """The distinction that makes the rule above safe: a Building column or a View
    column gives a bare word, never a number-street-type-place."""
    assert DS._addressed_place("114.0 Theodore Ground 3 Bed + MPR / 2 Bath Type A1") == ""
    assert DS._addressed_place(
        "Available V1602 Leased 2 Bed 1 Study in stage 2 North Flemington Racecourse") == ""


def test_a_document_title_is_still_refused_when_two_places_are_addressed():
    text = ("12 Smith Rd, Dromana 1 $100,000\n"
            "9 Jones St, Fraser Rise 2 $200,000\n")
    assert _document_says(text, "VIC") == "", "two addressed places, so no one answer"


def test_one_addressed_place_is_taken():
    """The positive half of the pair above, so the refusal cannot pass by the function
    simply never answering anything."""
    text = ("Lot Frontage Depth Area Price Status\n"
            "1012 Taylors Rd, Fraser Rise 1008 10.5 28 294 $390,000 DEPOSIT\n")
    assert _document_says(text, "VIC") == "Fraser Rise"


def test_the_builder_guard_is_load_bearing_and_needs_the_builder():
    """Hudson Homes' stocklist title, end to end — and the guard's one dependency.

    Nothing in the TEXT distinguishes "Hudson Homes Stocklist" from a title naming a
    town: without the builder this function answers 'Hudson' and 19 listings move 1,500
    km. `refresh_from_database` passes the group's builder for exactly this reason, and
    this test fails if that argument is ever dropped again.
    """
    text = "Hudson Homes Stocklist\nLee Gilmour - sales@e-agent.com.au\nNSW"
    assert _document_says(text, "QLD") == "Hudson", "the trap, with no builder to check"
    assert _document_says(text, "QLD", builder="Hudson Homes") == ""
