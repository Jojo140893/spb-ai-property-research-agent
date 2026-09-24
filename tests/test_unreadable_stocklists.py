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


def test_the_harvest_can_actually_record_them(monkeypatch, caplog):
    """CALLED, not grepped for. test_the_harvest_writes_it_down above checks only that the
    function's NAME appears in harvest_buildings.py, and it passed for a month while the
    function could not run one line: it called logger.warning in a module that never defined
    `logger`. The path only runs when a stocklist yields nothing, so it stayed dormant until
    24 Sep, when E-Agent read 0 of 13,636 listings and the NameError crashed the whole
    harvest -- publishing none of the 4,000 rows the other channels HAD read that night.
    This drives the exact path that crashed: a non-empty UNREADABLE list."""
    import json
    import logging
    from pathlib import Path

    import harvest_buildings as HB
    import sources.spreadsheet_extract as SX

    target = Path(HB.__file__).resolve().parent / "data" / "unreadable_stocklists.json"
    saved = target.read_bytes() if target.exists() else None
    monkeypatch.setattr(SX, "UNREADABLE", [{"file": "x.pdf", "source": "E-Agent"}])
    try:
        with caplog.at_level(logging.WARNING):
            HB._record_unreadable_stocklists()          # raised NameError before 24 Sep
        assert json.loads(target.read_text(encoding="utf-8"))["files"] == [
            {"file": "x.pdf", "source": "E-Agent"}]
        assert any("produced no listings" in r.getMessage() for r in caplog.records), \
            "the warning that names the missing projects was never logged"
    finally:
        if saved is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(saved)                   # never leave the real file changed
