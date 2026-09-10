"""The build refuses to publish a catalogue that shows sold stock.

Ahsan, 7 Sep 2026: "I want you to see that there are not any buildings that sold or not
available. Show up in the the filter because I see". Twice I answered by measuring
availability_status, found it clean, and told him it was fine. He was looking at the word
SOLD in the ADDRESS column, which no status check would ever have found. Repeated on
9 Sep as part of the standing goal.

Every other guard in build_web asks whether the DATA is right. This one asks what the
SCREEN says, which is the thing he was actually complaining about.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "api")

import build_web


def _snapshot(rows, keys=("availability_status", "lot_address", "estate_name",
                          "superseded_by")):
    path = Path(tempfile.mkdtemp()) / "stock.json"
    path.write_text(json.dumps({"keys": list(keys), "rows": rows}), encoding="utf-8")
    return path


def _row(status="Available", address="Lot 7 Racecourse Rd", estate="Westwood Walk",
         superseded=""):
    return [status, address, estate, superseded]


def test_a_clean_catalogue_passes():
    assert build_web._assert_no_sold_stock_on_show(_snapshot([_row(), _row()])) == 0


def test_the_word_in_the_address_is_counted():
    """'Lot 7, 2 14.6 SOLD' with a blank status -- one of the three that were live."""
    n = build_web._assert_no_sold_stock_on_show(
        _snapshot([_row(status="", address="Lot 7, 2 14.6 SOLD")]))
    assert n == 1


def test_the_word_in_the_estate_is_counted():
    """'1 Reserved Bby/5old To SEY' -- an OCR garble that reached the client."""
    n = build_web._assert_no_sold_stock_on_show(
        _snapshot([_row(status="", estate="1 Reserved Bby/5old To SEY")]))
    assert n == 1


def test_a_row_the_filter_already_hides_is_not_counted():
    """The complaint is about what SHOWS. A lot correctly marked Sold is doing its job,
    and its address is expected to say so."""
    n = build_web._assert_no_sold_stock_on_show(
        _snapshot([_row(status="Sold", address="West 9 Darling Darley Sold Barcelona")]))
    assert n == 0


def test_a_superseded_row_is_not_counted():
    n = build_web._assert_no_sold_stock_on_show(
        _snapshot([_row(status="", address="Lot 7 SOLD", superseded="withdrawn")]))
    assert n == 0


def test_a_real_place_name_is_not_a_sold_word():
    """"The Reserve" is an estate and "Settlers Run" is a street. A guard that failed the
    build on those would be worse than no guard: it would train everyone to ignore it."""
    for estate in ("The Reserve Estate", "Settlers Run", "Reserve Road Precinct",
                   "Holden Park"):
        assert build_web._assert_no_sold_stock_on_show(
            _snapshot([_row(status="", estate=estate)])) == 0, estate


def test_a_systemic_break_stops_the_build():
    """One OCR garble must not cost a day of fresh stock. Two dozen means the repair that
    fixes these stopped running, and that is worth stopping for."""
    rows = [_row(status="", address=f"Lot {n} SOLD")
            for n in range(build_web._SOLD_ON_SHOW_LIMIT + 5)]
    try:
        build_web._assert_no_sold_stock_on_show(_snapshot(rows))
    except SystemExit as exit_:
        assert "sold/on-hold word" in str(exit_)
        assert "find_sold_word_without_status" in str(exit_), \
            "the abort message must name the step that fixes this"
    else:
        raise AssertionError("the build did not stop on a systemic break")


def test_one_survivor_does_not_stop_the_build():
    rows = [_row(status="", address="Lot 7 SOLD")] + [_row() for _ in range(50)]
    assert build_web._assert_no_sold_stock_on_show(_snapshot(rows)) == 1


def test_a_snapshot_without_the_column_is_not_judged():
    path = Path(tempfile.mkdtemp()) / "stock.json"
    path.write_text(json.dumps({"keys": ["lot_address"], "rows": [["Lot 7 SOLD"]]}),
                    encoding="utf-8")
    assert build_web._assert_no_sold_stock_on_show(path) == 0


def test_every_excluded_word_is_actually_detected():
    """The coverage gap that let a broken guard ship.

    The pattern carried a literal backspace where a whitespace class was meant, so
    "on hold", "under offer" and "deposit taken" were silently missed while "sold"
    matched and every test passed. Three of the seven words the filter excludes were
    invisible to the guard that exists to catch them.

    One assertion per word, so a regex edit cannot quietly drop one again.
    """
    for word in ("sold", "on hold", "onhold", "under offer", "underoffer",
                 "reserved", "withdrawn", "deposit taken", "deposittaken", "settled"):
        assert build_web._SOLD_WORD_ON_SHOW.search(f"Lot 7 {word} Estate"), \
            f"{word!r} is not detected by the build-time guard"


def test_a_word_merely_containing_one_is_not_flagged():
    """Whole words only. "Soldiers Point" is a real place and "Reserve Road" is a street;
    a guard that failed the build on those would be trained away within a week."""
    for text in ("Soldiers Point Rd", "The Reserve Estate", "Settlers Run",
                 "Holden Park", "Resold Court"):
        assert not build_web._SOLD_WORD_ON_SHOW.search(text), text


def test_the_pattern_carries_no_mangled_escapes():
    """A literal 0x08 compiles fine and matches nothing -- the exact failure above. Any
    control character in this file is a mangled escape, never intentional."""
    from pathlib import Path
    src = Path(build_web.__file__).read_text(encoding="utf-8", errors="replace")
    assert chr(8) not in src, "build_web.py contains a literal backspace (a mangled \b)"


# --------------------------------------- capability URLs must not reach the browser

def test_a_folder_share_link_is_never_published_as_a_name():
    """56 of the 57 unreadable price lists are Dropbox "/scl/fo/" folder shares --
    capability URLs that hand over a whole shared folder to anyone holding the string,
    with no sign-in. channels.json was publishing all of them, because the assertion that
    exists to stop exactly this only ever read stock.json.

    The panel exists so somebody can go and ask that builder for a readable price list.
    A file name does that; the capability URL only adds risk.
    """
    name = build_web._readable_name(
        "https://www.dropbox.com/scl/fo/nsvrshjtdqt0jdp82is29/AL7lkoSPC/"
        "92%20New%20Street%20Price.pdf?rlkey=secrettoken&dl=1")
    assert name == "92 New Street Price.pdf (dropbox.com)"
    assert "scl/fo" not in name and "rlkey" not in name and "secrettoken" not in name


def test_the_name_is_readable_and_decoded():
    assert build_web._readable_name(
        "https://www.e-agent.com.au/_files/ugd/069fe0_x.pdf") == "069fe0_x.pdf (e-agent.com.au)"
    assert "%20" not in build_web._readable_name("https://x.com/a/My%20File.xlsx")


def test_a_link_with_no_filename_still_says_something_useful():
    got = build_web._readable_name("https://docs.google.com/spreadsheets/d/1abcDEF/edit")
    assert got and "1abcDEF" not in got


def test_the_guard_reads_every_published_file():
    """It was written for stock.json and only ever read stock.json. A guard that checks
    one of five files reads, from its passing, as though it checked all five."""
    import inspect
    src = inspect.getsource(build_web)
    call = src[src.index("for _published in ("):]
    call = call[:call.index("_assert_no_folder_capability_urls(where / _published)") + 60]
    for name in ("stock.json", "channels.json", "builders.json", "vendor-assets.json",
                 "benchmarks.json"):
        assert name in call, f"{name} is published but not checked for capability URLs"


def test_no_module_in_the_request_path_carries_a_mangled_escape():
    """A literal 0x08 compiles fine and matches nothing, so the guard it lives in goes
    quietly dead. api/_candidates.py carried THIRTEEN of them on main: _HOUSE_WORDS
    matched neither "Detached" nor "Duplex" nor "House & Land", so 1,269 house-and-land
    lots were served as apartments and a client filtering House & Land could reach 4,735
    listings instead of 6,034.

    Checked across every module the deployed function imports, not just this one.
    """
    from pathlib import Path
    root = Path(build_web.__file__).resolve().parent
    checked = 0
    for path in list((root / "api").glob("*.py")) + list(root.glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        checked += 1
        assert chr(8) not in text, f"{path.name} contains a literal backspace (a mangled escape)"
        assert chr(1) not in text, f"{path.name} contains a literal SOH (a mangled backreference)"
    assert checked > 20, f"only scanned {checked} modules; the glob is not finding them"
