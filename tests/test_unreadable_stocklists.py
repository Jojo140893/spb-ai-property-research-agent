"""A stocklist that reads as nothing has to be named, not just logged.

On 27 Aug E-Agent served 143 stocklists and 57 of them — 39% — extracted zero listings.
Whole projects are absent from the catalogue: Koops, Eastbury, Rockbank, Tunstall,
Rosebud, Westwood, Ceduna, Aurora Wollert, Oakleigh, Yarra Park. Coleen found one of them
by hand — "i can see there is a townhouse in Geelong ... tried search in the app result
not found" — because nothing anywhere said the file had been fetched and discarded.

Sampled, the cause is nearly uniform: the PDF holds ONE IMAGE and no text at all.
pdfplumber and PyMuPDF both return zero characters, so no text reader can recover them.
"""
import sys

sys.path.insert(0, "api")

from sources import spreadsheet_extract as SE


def test_a_file_that_yields_nothing_is_recorded():
    before = len(SE.UNREADABLE)
    SE._log_yield("pdf", "Koops Price.pdf", [])
    assert len(SE.UNREADABLE) == before + 1
    assert SE.UNREADABLE[-1]["file"] == "Koops Price.pdf"


def test_a_file_that_yields_listings_is_not():
    before = len(SE.UNREADABLE)
    SE._log_yield("pdf", "good.pdf", [{"suburb": "Geelong"}])
    assert len(SE.UNREADABLE) == before, "a working file must not be flagged"


def test_the_harvest_writes_it_down():
    """The harvest and the build are separate processes; an in-memory list dies with the
    run. This is what carries it across."""
    from pathlib import Path

    src = Path(SE.__file__).resolve().parents[1].joinpath(
        "harvest_buildings.py").read_text(encoding="utf-8")
    assert "_record_unreadable_stocklists" in src
    assert "unreadable_stocklists.json" in src


def test_the_build_publishes_it_and_the_page_shows_it():
    from pathlib import Path

    root = Path(SE.__file__).resolve().parents[1]
    build = root.joinpath("build_web.py").read_text(encoding="utf-8")
    page = root.joinpath("index.html").read_text(encoding="utf-8")
    assert '"unreadable_stocklists"' in build, "the build must publish the list"
    assert "unreadable_stocklists" in page, "the Settings tab must render it"


def test_an_absent_file_is_not_an_error():
    """A machine that has never harvested still has to build."""
    import build_web

    got = build_web._unreadable_stocklists()
    assert "count" in got and isinstance(got["files"], list)
