"""A price list exported as a picture.

57 of the 143 stocklists E-Agent served on 27 Aug extracted zero listings — 39%, whole
projects missing. Thirteen of fourteen sampled were PDFs holding ONE IMAGE and no text:
pdfplumber reads nothing and PyMuPDF agrees. Coleen found one by hand, "Koops Price.pdf",
whose own first line reads "HIGHTON (GEELONG)- 12 KOOP CLS".
"""
import sys

sys.path.insert(0, "api")

from sources import spreadsheet_extract as SE


def test_ocr_is_a_last_resort_not_a_strategy():
    """It must never compete with a text read. Measured at 92% price recall, OCR is
    strictly worse than reading real text — its only virtue is that it beats nothing."""
    src = open(SE.__file__, encoding="utf-8").read()
    assert "_ocr_rows" not in str(SE._PDF_STRATEGIES), (
        "OCR must not be one of the competing strategies")
    assert "if not best and not others:" in src, (
        "OCR must run only where both text strategies found nothing")


def test_a_missing_engine_is_not_an_error():
    """The dependency is optional in effect: a machine without it must still build."""
    class Page:
        page_number = 1
    assert SE._ocr_rows(Page()) == []


def test_the_runner_installs_everything_ocr_imports():
    """EVERY import inside _ocr_rows, not just the one I remembered.

    This asserted only that rapidocr was declared. PyMuPDF was not, so on the 31 Aug run
    `import fitz` raised inside the lazy import, _ocr_rows returned no rows, and
    "Koops Price.pdf -> 0 listing(s)" came back exactly as before — OCR working on my
    machine and doing nothing in production. Deriving the list from the function source is
    what stops the next dependency being forgotten the same way.
    """
    import re
    from pathlib import Path

    root = Path(SE.__file__).resolve().parents[1]
    req = root.joinpath("requirements.txt").read_text(encoding="utf-8").lower()
    src = Path(SE.__file__).read_text(encoding="utf-8")
    block = src[src.index("def _ocr_rows"):src.index("def _pdf_rows_from_lines")]
    modules = set(re.findall(r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", block, re.M))
    # The distribution name differs from the import name for these.
    package_for = {"fitz": "pymupdf", "rapidocr_onnxruntime": "rapidocr"}
    for mod in modules:
        name = package_for.get(mod, mod)
        assert name in req, (
            "_ocr_rows imports %r but %r is not in requirements.txt — it will work here "
            "and do nothing on the runner" % (mod, name))


def test_rows_are_grouped_into_bands_not_detection_order():
    """OCR returns boxes in detection order. A stocklist row is a horizontal band, so
    boxes are grouped by vertical position and sorted left to right — without that the
    price of one lot lands in the row of another."""
    src = open(SE.__file__, encoding="utf-8").read()
    block = src[src.index("def _ocr_rows"):src.index("def _pdf_rows_from_lines")]
    assert "bands" in block and "sorted(" in block
