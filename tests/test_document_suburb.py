"""Reading a stock document for the suburb it is selling, and not being fooled by it."""
import io

import document_suburb as DS


class _Index:
    """The two localities these tests need, in one state each."""
    _PLACES = {("murrumbeena", "VIC"), ("broadbeach", "QLD"), ("bourke", "NSW"),
               ("page", "ACT"), ("sale", "VIC"), ("richmond", "VIC")}

    def locate(self, name, state):
        return (str(name).lower(), str(state).upper()) in self._PLACES

    def states_for_suburb(self, name):
        return [s for n, s in self._PLACES if n == str(name).lower()]


def _doc(text):
    return lambda url, **kw: text.encode()


def _suburb(text, state="VIC"):
    """front_page_text only reads PDFs and workbooks, so bypass it for these."""
    names = DS.localities_in(text, state, _Index())
    return names[0] if len(names) == 1 else ""


def test_it_reads_the_locality_off_a_front_page():
    assert _suburb("Murrumbeena Pricelist - Current as of 30/03/2026\n"
                   "Sales by Product Type") == "Murrumbeena"


def test_the_street_guard_also_costs_a_project_called_X_Place():
    """The known price of the street guard, recorded rather than rediscovered later.

    'Place' is a street type, so a project named "Murrumbeena Place" is declined even
    though Murrumbeena is its suburb. Keeping the guard is still right: it is what stops
    "Level 3, 200 Bourke Street" from naming a price list Bourke, and a decline costs one
    document while a wrong answer costs every lot on it. Such a document is picked up by
    its filename or by a manual entry instead."""
    assert _suburb("Murrumbeena Place - Current as of 30/03/2026") == ""


def test_a_street_named_after_a_town_is_not_the_answer():
    """'Bourke' named a price list because the document mentions Bourke Street. Bourke is
    a town 760 km from it."""
    assert _suburb("Level 3, 200 Bourke Street\nPrice List", "NSW") == ""


def test_a_page_footer_is_not_a_suburb():
    """'Page' is a suburb of Canberra and the footer of every document ever printed."""
    assert _suburb("WE Commercial Price List\nPage 1 of 4", "ACT") == ""


def test_the_word_sales_is_not_the_town_of_sale():
    assert _suburb("Warehouse Price List\nSales by Product Type", "VIC") == ""


def test_two_localities_means_no_answer():
    """A price list covering several projects cannot be collapsed to one suburb, and
    taking the first would quietly mislabel every lot in the others."""
    assert _suburb("Richmond and Murrumbeena releases", "VIC") == ""


def test_a_workbook_is_read_as_well_as_a_pdf():
    """A third of these documents are .xlsx; skipping them cost 10 documents for no
    reason better than the file extension."""
    openpyxl = __import__("openpyxl")
    book = openpyxl.Workbook()
    book.active["A1"] = "Murrumbeena Place Pricelist"
    buffer = io.BytesIO()
    book.save(buffer)
    assert "Murrumbeena" in DS.front_page_text(buffer.getvalue())


def test_neither_a_pdf_nor_a_workbook_is_simply_declined():
    assert DS.front_page_text(b"<html>Murrumbeena</html>") == ""


# ------------------------------------------------------------------- being a good guest
def test_an_empty_body_is_retried_rather_than_believed():
    """Fetching ~70 documents back-to-back got a host returning empty bodies partway
    through — not an error, just nothing, which reads exactly like "no text in this
    document" and would quietly empty the map on the next nightly run."""
    calls = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            calls.append(1)
            return b"" if len(calls) < 3 else b"%PDF-1.4 ok"

    got = DS.fetch("https://example.test/x.pdf", opener=lambda *a, **k: _Resp(),
                   sleep=lambda s: None)
    assert got == b"%PDF-1.4 ok"
    assert len(calls) == 3


def test_it_gives_up_rather_than_hammering_a_host():
    calls = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            calls.append(1)
            return b""

    assert DS.fetch("https://example.test/x.pdf", opener=lambda *a, **k: _Resp(),
                    sleep=lambda s: None) == b""
    assert len(calls) == DS._EMPTY_RETRIES


# ------------------------------------------------------------------------------ the map
def test_a_manual_answer_beats_the_automatic_one(tmp_path, monkeypatch):
    """Someone who checked the developer's own listing knows more than a front-page scan."""
    auto = tmp_path / "a.json"
    by_hand = tmp_path / "m.json"
    auto.write_text('{"http://x": ["Wrongtown", "VIC"]}', encoding="utf-8")
    by_hand.write_text('{"http://x": {"suburb": "Broadbeach", "state": "QLD",'
                       ' "evidence": "20 Albert Ave, Broadbeach QLD 4218"}}',
                       encoding="utf-8")
    monkeypatch.setattr(DS, "map_path",
                        lambda manual=False: str(by_hand if manual else auto))
    monkeypatch.setattr(DS, "_MAP_CACHE", None)
    got = DS.load_map(refresh=True)
    assert got["http://x"] == ["Broadbeach", "QLD"]


def test_a_row_from_another_state_does_not_inherit_the_answer(monkeypatch):
    monkeypatch.setattr(DS, "_MAP_CACHE", {"http://x": ["Broadbeach", "QLD"]})
    assert DS.suburb_for_row({"source_url": "http://x", "state": "QLD"}) == "Broadbeach"
    assert DS.suburb_for_row({"source_url": "http://x", "state": "VIC"}) == ""
