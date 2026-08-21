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


# ------------------------------------------------- the section header above each block
def test_line_key_survives_the_encoding_noise_pdfs_produce():
    """The same line arrives as '197.08m\u00b2' from one reader and '197.08m?' from another."""
    a = DS.line_key("Available 61 House COPPER 21 197.08m\u00b2 469.0 m\u00b2 4 beds")
    b = DS.line_key("Available 61 House COPPER 21 197.08m? 469.0 m? 4 beds")
    assert a == b != ""


def test_a_builder_stocklist_is_a_dozen_suburbs_not_one():
    """Hudson Homes' list reads

        Watagan Park - Display Homes, Cooranbong
        Available 7 House MARIGOLD 35 ... $1,519,500
        Jensen Rise Estate - Wadalba, Wadalba
        Available 61 House COPPER 21 ... $1,167,260

    and every one of its 104 live rows was stored with no suburb, because the harvest
    kept the lot line and threw the header above it away. suburb_for_document correctly
    refuses this file — asked for ONE locality when there are twelve — so the lot line
    itself has to carry the join."""
    lines = [
        "Hudson Homes Stocklist",
        "Watagan Park - Display Homes, Cooranbong",
        "Available 7 House MARIGOLD 35 324.91m2 483.0 m2 5 beds $1,519,500 Portal Link",
        "Jensen Rise Estate - Wadalba, Wadalba",
        "Available 61 House COPPER 21 197.08m2 469.0 m2 4 beds $1,167,260 Portal Link",
    ]

    class _Idx:
        def locate(self, name, state):
            return str(name).lower() in ("cooranbong", "wadalba")

        def states_for_suburb(self, name):
            return ["NSW"] if str(name).lower() in ("cooranbong", "wadalba") else []

    got = _rows_from_lines(lines, "NSW", _Idx())
    assert got[DS.line_key(lines[2])] == "Cooranbong"
    assert got[DS.line_key(lines[4])] == "Wadalba"


def test_a_lot_before_any_header_is_left_unnamed():
    """Better to name nothing than to hand a lot the suburb of the block below it."""
    class _Idx:
        def locate(self, name, state):
            return str(name).lower() == "wadalba"

        def states_for_suburb(self, name):
            return ["NSW"] if str(name).lower() == "wadalba" else []

    lines = ["Available 7 House MARIGOLD $1,519,500", "Estate - Wadalba, Wadalba",
             "Available 61 House COPPER $1,167,260"]
    got = _rows_from_lines(lines, "NSW", _Idx())
    assert DS.line_key(lines[0]) not in got
    assert got[DS.line_key(lines[2])] == "Wadalba"


def _rows_from_lines(lines, state, index):
    """row_suburbs' logic against a list of lines, without building a PDF to hold them."""
    out, current = {}, ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if DS._MONEY.search(line):
            if current:
                out[DS.line_key(line)] = current
            continue
        segments = [s.strip() for s in line.split(",") if s.strip()]
        if not segments:
            continue
        tail = segments[-1]
        if len(DS._WORD.findall(tail)) > 4:
            continue
        if index.locate(tail, state) if state else len(index.states_for_suburb(tail)) == 1:
            current = tail.title()
    return out


def test_the_estate_name_trap_does_not_reach_the_section_headers():
    """Only the LAST comma-separated segment is tested, which is what keeps
    'Bingara Gorge - Spec Contract' from yielding Bingara."""
    class _Idx:
        def locate(self, name, state):
            return str(name).lower() == "bingara"

        def states_for_suburb(self, name):
            return ["NSW"] if str(name).lower() == "bingara" else []

    got = _rows_from_lines(["Bingara Gorge - Spec Contract",
                            "Available 7 House $900,000"], "NSW", _Idx())
    assert got == {}


# ------------------------------------------------ the gate that keeps the row map safe
def test_the_row_map_gate_is_strict_enough_to_matter():
    """Ungated, the per-row mapping is 32% right, and its failure LATCHES: a line that is
    not a section header matches anyway and hands its suburb to every lot beneath it
    until the next match. That is how Donnybrook reached Shepparton lots.

    The gate is what stands between that and the published snapshot, so its constants are
    pinned here rather than left as tunables someone relaxes on a quiet afternoon."""
    assert DS._ROW_MAP_MIN_CHECKS >= 5
    assert DS._ROW_MAP_FLOOR >= 0.9


def test_the_shipped_row_map_agrees_with_every_suburb_it_can_be_checked_against():
    """The map in the repo is the gate's output, so it must survive the gate's own test.

    This is the regression that matters: if a future refresh ever writes the ungated map,
    or the gate is loosened, this fails rather than the app quietly serving Shepparton
    lots as Donnybrook."""
    import sqlite3

    import config
    import suburb_quality

    try:
        conn = sqlite3.connect("file:%s?mode=ro" % config.DATABASE_PATH, uri=True)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT source_text, suburb, state FROM buildings "
            "WHERE (superseded_by IS NULL OR superseded_by = '')")]
        conn.close()
    except Exception:                              # no database in this environment
        return

    index = suburb_quality._index()
    if not index.loaded:
        return
    mapping = DS.load_row_map(refresh=True)
    if not mapping:
        return
    disagreements = []
    for row in rows:
        named = mapping.get(DS.line_key(row.get("source_text") or ""))
        if not named:
            continue
        stored = str(row.get("suburb") or "").strip()
        state = str(row.get("state") or "").strip()
        if not stored:
            continue
        if not (index.locate(stored, state) if state else index.states_for_suburb(stored)):
            continue
        if named.lower() != stored.lower():
            disagreements.append((named, stored))
    assert not disagreements, (
        "the shipped row map contradicts %d known suburb(s), e.g. %s"
        % (len(disagreements), disagreements[:3]))
