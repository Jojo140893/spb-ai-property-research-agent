"""A spreadsheet's filter row is furniture, not stock.

These were the last rows in the catalogue no suburb rule could reach, and none should
have tried: "All" is not a place. Removing them is what took coverage to 100%, and it is
the right resolution rather than a convenient one — assigning a suburb to a dropdown
control would have been the wrong kind of 100%.
"""
import sys

sys.path.insert(0, "api")

from sources.spreadsheet_extract import _is_a_control_row, _listing_from_row

ARROW = ""          # a dropdown arrow, drawn from an icon font
CARET = ""

REAL_FILTER_ROWS = [
    "All {a} Storage {a} All {a} All {a} $225,500.00 $1,298,154.00 All {a}".format(a=ARROW),
    "The Precinct {a} All {a} All {a} All {a} $225,500.00 $1,298,154.00 All {a}".format(a=ARROW),
    "Multiple selections {a} All {a} {c} Yes All {a} All {a} $506,480 $1,715,000".format(
        a=ARROW, c=CARET),
]

# Every one of these is a real stored row from the live catalogue.
REAL_LISTINGS = [
    "501 2 1 1 70 27 97 SE $913,000",
    "110 1 1 51.1 9.8 60.9 $460,000 S-B4 $2,701.82",
    "1012 Taylors Rd, Fraser Rise 1008 10.5 28 294 $390,000 DEPOSIT East Q1 2027",
    "Bayview 14 3 BED 2 BATH 2 CAR TH14 E/S 142 139 281 $1,850,000 available",
    "321 Stage 3 317m2 $515,000 August 2026 Faro Yamba 170m2 4 2 2 1 $410,100",
]


def test_it_catches_every_filter_row_in_the_catalogue():
    for text in REAL_FILTER_ROWS:
        assert _is_a_control_row(text), text


def test_it_catches_no_real_listing():
    """The cautionary tale is one function above it in the source: a first attempt at
    _is_price_fragment deleted 112 rows and most of them were real."""
    for text in REAL_LISTINGS:
        assert not _is_a_control_row(text), text


def test_a_single_stray_glyph_is_not_enough():
    """The floor is two on purpose. One icon in an otherwise real row is a bullet, not a
    control strip — measured identical on today's data, so the looser bar costs nothing
    and protects a row this has never seen."""
    assert not _is_a_control_row("Lot 42, Werribee %s $610,000" % ARROW)
    assert _is_a_control_row("All %s All %s" % (ARROW, ARROW))


def test_plain_text_is_never_a_control_row():
    assert not _is_a_control_row("")
    assert not _is_a_control_row(None)
    assert not _is_a_control_row("EDEN'S CROSSING ESTATE - Redbank Plains")


def test_the_extractor_refuses_to_build_a_listing_from_one():
    """The guard is only worth having if it runs where rows are made. Two prices was
    exactly what made these look like listings, so the row is passed with its prices."""
    for text in REAL_FILTER_ROWS:
        assert _listing_from_row(text, [], "", "t", "") is None, text


def test_a_real_row_still_becomes_a_listing():
    """The other half, so the test above cannot pass by _listing_from_row rejecting
    everything put in front of it."""
    assert _listing_from_row("110 1 1 51.1 9.8 60.9 $460,000 S-B4", [], "", "t", "")


def test_the_repair_finds_them_in_stored_rows():
    """The extractor guard only helps rows harvested after it; these were already stored."""
    import repair_rows

    rows = [{"source_text": t, "id": i} for i, t in enumerate(REAL_FILTER_ROWS)]
    rows += [{"source_text": t, "id": 100 + i} for i, t in enumerate(REAL_LISTINGS)]
    found = repair_rows.find_control_rows(rows)
    assert len(found) == len(REAL_FILTER_ROWS)
    assert {r["id"] for r in found} == set(range(len(REAL_FILTER_ROWS)))


# ---------------------------------------------------------------- a sentence, not a lot

PRICE_RANGE_SENTENCES = [
    "Price ranges from $288,000- $719,800 (ex g.s.t)",
    "Price ranges from $725,000 to $795,000(excl. GST)",
]


def test_a_price_range_sentence_is_not_a_listing():
    """Both are live rows from one email. They carry two prices and no property at all,
    and the higher figure was stored as the asking price -- so the catalogue held a
    $795,000 listing that is not a property."""
    from sources.spreadsheet_extract import _is_a_price_range_sentence

    for text in PRICE_RANGE_SENTENCES:
        assert _is_a_price_range_sentence(text, {}), text
        assert _listing_from_row(text, [], "", "t", "") is None, text


def test_a_real_listing_quoting_a_range_survives():
    """The lot-identity guard. _is_price_fragment deliberately lets a sentence through
    because it has words of its own -- that test is what stops it deleting the apartment
    rows that are almost entirely numeric -- so this one needs its own narrow guard."""
    from sources.spreadsheet_extract import _is_a_price_range_sentence

    assert not _is_a_price_range_sentence("Lot 42 ranges from $500,000",
                                          {"lot_number": "42"})
    assert not _is_a_price_range_sentence("501 2 1 1 70 27 97 SE $913,000",
                                          {"bedrooms": 2})
    for text in REAL_LISTINGS:
        assert not _is_a_price_range_sentence(text, {}), text


def test_a_totals_line_carrying_its_column_labels_is_not_a_listing():
    """"PACKAGE PRICE HOUSE PRICE A$803,364 A$803,364 Disclaimer: Price based on home
    type and floor plan" — a totals line lifted out of a brochure, with the column
    headings still attached.

    The damage was not only that these are not properties. The extractor took "PRICE" out
    of that header as the SUBURB, and Price is a real town on the Yorke Peninsula, so with
    the row's SA state it passed the gazetteer, was graded LOCATED, and put two phantom
    listings on the map there.
    """
    from sources.spreadsheet_extract import _is_a_price_range_sentence

    line = ("PACKAGE PRICE HOUSE PRICE A$803,364 A$803,364 "
            "Disclaimer: Price based on home type and floor plan")
    assert _is_a_price_range_sentence(line, {})
    assert _listing_from_row(line, [], "", "t", "") is None


def test_a_real_listing_that_merely_quotes_a_price_label_survives():
    """14 live rows carry the word "Disclaimer" and 12 of them are real listings. The lot
    identity is what separates them, and without that guard this rule would delete stock."""
    from sources.spreadsheet_extract import _is_a_price_range_sentence

    assert not _is_a_price_range_sentence("Lot 42 Package Price $650,000",
                                          {"lot_number": "42"})
    assert not _is_a_price_range_sentence(
        "4 Bed 2 Bath Total Price $780,000 Disclaimer applies", {"bedrooms": 4})
    for text in REAL_LISTINGS:
        assert not _is_a_price_range_sentence(text, {}), text
