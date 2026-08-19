"""The silent-undercount fixes: a partial read must never look like a complete one.

Every case here was found by auditing the whole pipeline for the failure that hid 78 of
Proxima's 118 projects — the code decides how much stock exists from what it managed to
parse, never from what the source said it had. Each one was reproduced before it was
fixed, and the numbers in the docstrings are the measured ones.
"""
import io
import logging

import pytest

import sources.spreadsheet_extract as SE


HEADER = "Lot,Status,Title,Type,Land,Bed,Bath,Car,Price\n"
ROWS = ("45,Available,Titled,House & Land,375,4,2,2,705000\n"
        "46,Available,Titled,House & Land,392,4,2,2,725000\n"
        "47,Available,Registered,House & Land,401,4,2,2,742000\n"
        "48,Available,Titled,House & Land,410,5,3,2,801000\n"
        "49,Available,Titled,House & Land,420,5,3,2,822000\n")


# ------------------------------------------------ the money-column map wipe

def test_a_numeric_price_sheet_keeps_every_row():
    """MEASURED BEFORE THE FIX: 5 rows in, 2 out.

    _is_column_header refuses to call a row "column names" once it sees money — but a
    numeric sheet keeps the dollar sign in the cell FORMAT, so the raw text has none and
    the guard could not fire on precisely the sheets _MoneyColumns exists to rescue. The
    row then read as a header, and feed_header rebuilt the column map out of the lot's
    own data before the `continue`, emptying it. Every row below lost its price.
    """
    rows = SE.extract_from_csv((HEADER + ROWS).encode(), source_label="t.csv")
    assert len(rows) == 5
    prices = sorted(r["advertised_package_price"] for r in rows)
    assert prices == [705000, 725000, 742000, 801000, 822000]


def test_the_same_holds_when_every_text_column_is_header_vocabulary():
    """The worst shape: Status and Title are the only words, and both are header words.
    Measured before the fix: 0 of 3."""
    csv = ("Lot,Status,Title,Price\n"
           "45,Available,Titled,705000\n"
           "46,Available,Titled,725000\n"
           "47,Available,Registered,742000\n")
    rows = SE.extract_from_csv(csv.encode(), source_label="t.csv")
    assert len(rows) == 3


def test_a_real_header_row_is_still_recognised():
    """The fix must not make headers invisible — that would put column names in the
    catalogue as listings."""
    rows = SE.extract_from_csv((HEADER + ROWS).encode(), source_label="t.csv")
    texts = " ".join((r.get("source_text") or "") for r in rows).lower()
    assert "bed,bath,car" not in texts
    assert not any("status" in (r.get("lot_address") or "").lower()
                   and "title" in (r.get("lot_address") or "").lower() for r in rows)


def test_a_repeated_header_partway_down_still_resets_the_map():
    """Stocklists repeat their header per printed page; that must keep working."""
    csv = HEADER + ROWS + HEADER + "50,Available,Titled,House & Land,430,5,3,2,845000\n"
    rows = SE.extract_from_csv(csv.encode(), source_label="t.csv")
    assert len(rows) == 6
    assert max(r["advertised_package_price"] for r in rows) == 845000


def test_a_money_formatted_sheet_was_never_broken_and_still_is_not():
    csv = ("Lot,Status,Price\n"
           "45,Available,\"$705,000\"\n"
           "46,Available,\"$725,000\"\n")
    assert len(SE.extract_from_csv(csv.encode(), source_label="t.csv")) == 2


# ------------------------------------------------ one bad PDF page costs one page

class _Page:
    def __init__(self, number, rows=(), poison=False):
        self.page_number = number
        self._rows = rows
        self._poison = poison

    def extract_text_lines(self):
        if self._poison:
            raise RuntimeError("no glyphs on this page")
        return [{"text": t, "top": 1.0, "bottom": 2.0} for t in self._rows]

    def extract_text(self):
        if self._poison:
            raise RuntimeError("no glyphs on this page")
        return "\n".join(self._rows)

    def find_tables(self):
        if self._poison:
            raise RuntimeError("no glyphs on this page")
        return []

    @property
    def annots(self):
        return []


class _Pdf:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


LOT = "Lot {n} Wattle Street Example Estate Titled 400 4 2 2 $705,000"


def _pdf(monkeypatch, pages):
    monkeypatch.setattr(SE, "PDF_AVAILABLE", True)
    monkeypatch.setattr(SE, "pdfplumber", type("P", (), {
        "open": staticmethod(lambda *_a, **_k: _Pdf(pages))})())
    monkeypatch.setattr(SE, "_annot_links", lambda page: [])


def test_a_poisoned_page_costs_only_that_page(monkeypatch, caplog):
    """MEASURED BEFORE THE FIX: a 10-page list publishing 50 lots returned 15 rows, from
    pages 1-3, logged as an ordinary success. The try wrapped the whole loop."""
    pages = [_Page(i, rows=[LOT.format(n=i)]) for i in range(1, 6)]
    pages[3] = _Page(4, poison=True)
    _pdf(monkeypatch, pages)
    with caplog.at_level(logging.WARNING):
        rows = SE.extract_from_pdf(b"x", source_label="t.pdf")
    assert len(rows) == 4, "four good pages must survive one bad one"
    assert {r["source_page"] for r in rows} == {1, 2, 3, 5}
    assert any("page 4" in m for m in caplog.messages), "the lost page must be named"


def test_the_run_says_how_many_pages_it_lost(monkeypatch, caplog):
    """A page is only "unreadable" when EVERY strategy failed on it."""
    pages = [_Page(1, poison=True), _Page(2, poison=True),
             _Page(3, rows=[LOT.format(n=3)])]
    _pdf(monkeypatch, pages)
    with caplog.at_level(logging.WARNING):
        SE.extract_from_pdf(b"x", source_label="t.pdf")
    assert any("2 of 3 page(s) could not be read" in m for m in caplog.messages)


def test_one_strategy_failing_does_not_condemn_a_page_that_another_could_read(
        monkeypatch, caplog):
    """`lines` failing on a broken text layer must not cost the `tables` read of the
    same page — and the page is not counted as lost, because it was read."""
    class _TextBroken(_Page):
        def extract_text_lines(self):
            raise RuntimeError("no text layer")

        def extract_text(self):
            raise RuntimeError("no text layer")

        def find_tables(self):
            return []

    _pdf(monkeypatch, [_TextBroken(1)])
    monkeypatch.setattr(SE, "_PDF_STRATEGIES", (
        ("tables", lambda p, a: [(LOT.format(n=1), [], {})]),
        ("lines", SE._pdf_rows_from_lines)))
    with caplog.at_level(logging.WARNING):
        rows = SE.extract_from_pdf(b"x", source_label="t.pdf")
    assert len(rows) == 1, "the tables strategy still read the page"
    assert any("failed the lines strategy" in m for m in caplog.messages)
    assert not any("could not be read" in m and "page(s)" in m for m in caplog.messages)


def test_a_file_that_cannot_be_opened_is_still_reported(monkeypatch, caplog):
    monkeypatch.setattr(SE, "PDF_AVAILABLE", True)

    def boom(*_a, **_k):
        raise RuntimeError("not a pdf")

    monkeypatch.setattr(SE, "pdfplumber", type("P", (), {"open": staticmethod(boom)})())
    with caplog.at_level(logging.WARNING):
        assert SE.extract_from_pdf(b"x", source_label="t.pdf") == []
    assert any("could not read pdf" in m for m in caplog.messages)


def test_the_page_cap_is_announced_when_it_binds(monkeypatch, caplog):
    """Reading 25 of 40 pages silently is the same shape of loss as everything else."""
    pages = [_Page(i, rows=[LOT.format(n=i)]) for i in range(1, SE.PDF_PAGE_CAP + 6)]
    _pdf(monkeypatch, pages)
    with caplog.at_level(logging.WARNING):
        rows = SE.extract_from_pdf(b"x", source_label="big.pdf")
    assert len(rows) == SE.PDF_PAGE_CAP
    assert any("only the first" in m for m in caplog.messages)


def test_no_cap_warning_for_a_short_file(monkeypatch, caplog):
    _pdf(monkeypatch, [_Page(1, rows=[LOT.format(n=1)])])
    with caplog.at_level(logging.WARNING):
        SE.extract_from_pdf(b"x", source_label="small.pdf")
    assert not any("only the first" in m for m in caplog.messages)


# ------------------------------------------------ the two strategies union

def test_a_row_only_one_strategy_can_read_is_not_discarded(monkeypatch):
    """The strict `>` tie-break to `tables` is correct — it carries the columns map —
    but the loser used to be dropped whole, taking any row only it could read."""
    _pdf(monkeypatch, [_Page(1)])

    def tables(page, annots):
        return [(LOT.format(n=1), [], {}), (LOT.format(n=2), [], {})]

    def lines(page, annots):
        return [(LOT.format(n=1), [], {}), (LOT.format(n=9), [], {})]

    monkeypatch.setattr(SE, "_PDF_STRATEGIES", (("tables", tables), ("lines", lines)))
    rows = SE.extract_from_pdf(b"x", source_label="t.pdf")
    texts = " ".join(r.get("source_text") or "" for r in rows)
    assert "Lot 9 " in texts, "the lines-only row must survive"
    assert "Lot 2 " in texts


def test_rows_both_strategies_read_are_not_counted_twice(monkeypatch):
    _pdf(monkeypatch, [_Page(1)])
    same = [(LOT.format(n=1), [], {}), (LOT.format(n=2), [], {})]
    monkeypatch.setattr(SE, "_PDF_STRATEGIES",
                        (("tables", lambda p, a: list(same)),
                         ("lines", lambda p, a: list(same))))
    rows = SE.extract_from_pdf(b"x", source_label="t.pdf")
    assert len(rows) == 2, "a naive concat would return four"
