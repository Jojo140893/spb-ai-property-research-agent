"""Reading a column grid that the page never drew, without displacing what already works.

Some stocklists lay their columns out with whitespace and draw no ruling lines, so
pdfplumber finds no table and the row arrives as one flat string:

    Lot Project Name Address Suburb Type Status Bed Bath Car Land Size Build Price ...
    4A 4 Pilny Street Camira Banksia 183 - Urban Available 4 2 2 501 $ 430,900 ...

The suburb is printed on every row and was unreachable — 0 of 63 lots located.

THE SHAPE OF THIS MATTERS MORE THAN THE PARSING. Written first as a third entry in
_PDF_STRATEGIES it competed for the row-count tie-break, won pages it read worse than the
existing readers, and cost two documents everything they had (82 of 82 rows down to 0 of
86) for a net gain of 5. As an enrichment it cannot displace anything.
"""
from sources.spreadsheet_extract import _grid_suburbs, _looks_like_one_locality


class _Word(dict):
    pass


def _page(lines, xs):
    """A stand-in page whose extract_words returns positioned words."""
    words = []
    for row, (cells, top) in enumerate(lines):
        for cell, x in zip(cells, xs):
            if cell:
                words.append({"text": cell, "x0": float(x), "top": float(top)})

    class _P:
        def extract_words(self, **kw):
            return words
    return _P()


HEADER = (["Lot", "Project", "Address", "Suburb", "Type", "Status"], 0)
XS = [40, 65, 134, 165, 204, 318]


def test_it_reads_the_suburb_out_of_an_undrawn_grid():
    page = _page([HEADER,
                  (["4A", "Pilny", "", "Camira", "Banksia", "Available"], 12)], XS)
    got = _grid_suburbs(page)
    assert "Camira" in got.values(), got


def test_a_design_name_two_columns_over_is_not_taken():
    """Banksia is a real NSW suburb and it is the house design. Only the locality column
    and its immediate neighbours are searched — a wider search finds the design."""
    page = _page([HEADER,
                  (["4A", "Pilny", "", "", "Banksia", "Available"], 12)], XS)
    assert _grid_suburbs(page) == {}


def test_a_page_with_no_locality_column_yields_nothing():
    page = _page([(["Lot", "Design", "Price", "Status", "Bed", "Bath"], 0),
                  (["4A", "Banksia", "$500,000", "Available", "4", "2"], 12)], XS)
    assert _grid_suburbs(page) == {}


def test_an_unreadable_page_is_survivable():
    class _P:
        def extract_words(self, **kw):
            raise RuntimeError("no text layer")
    assert _grid_suburbs(_P()) == {}


def test_only_a_real_locality_is_accepted():
    assert _looks_like_one_locality("Camira")
    assert _looks_like_one_locality("White Rock")
    assert not _looks_like_one_locality("")
    assert not _looks_like_one_locality("$ 430,900")
    assert not _looks_like_one_locality("Banksia 183 - Urban")
    assert not _looks_like_one_locality("a b c d e")
