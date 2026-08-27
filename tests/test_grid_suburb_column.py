"""A column its own author headed "Suburb" beats a value that is not a place.

The Eternal Homes stock list published 97 listings as "Dual Key", "Duplex" and "Land" —
the TYPE column. Its header reads

    TYPE STATE SUBURB LOTS FLOORPLAN PROJECT ESTATE LAND SIZE BUILD SIZE ...

so the grid reader had the right answer for every row and was skipped, because it only
ever filled a BLANK suburb and a wrong value is not a blank one. Those 97 rows are seven
different NSW estates hundreds of kilometres apart — Hunterview near Singleton, Dora
Creek on Lake Macquarie, Millfield near Cessnock, Grantham Farm in north-west Sydney.
"""
import sys

sys.path.insert(0, "api")

from sources.spreadsheet_extract import _looks_like_one_locality


def test_the_type_column_values_are_not_places():
    """The premise. If these ever read as localities the override cannot help."""
    for junk in ("Dual Key", "Duplex", "Land"):
        assert not _looks_like_one_locality(junk), junk


def test_the_real_suburbs_are_places():
    for real in ("Millfield", "Hunterview", "Dora Creek", "Grantham Farm", "Nulkaba"):
        assert _looks_like_one_locality(real), real


def test_the_override_is_wired_and_narrow():
    """It must replace a non-place and must NOT overwrite a real suburb: where a grid and
    a stored locality disagree, that is a data question, not one to settle silently."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1].joinpath(
        "sources", "spreadsheet_extract.py").read_text(encoding="utf-8")
    assert "_needs_a_place" in src, "the grid override is not wired in"
    block = src[src.index("def _needs_a_place"):src.index("def _needs_a_place") + 400]
    assert "_looks_like_one_locality" in block, (
        "the override must be gated on the stored value not being a place")
