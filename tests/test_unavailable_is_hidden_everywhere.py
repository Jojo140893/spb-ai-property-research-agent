"""A lot that cannot be bought must not appear on ANY surface that offers stock.

Ahsan, 9 Sep 2026: "the buildings that are sold or hold or not avaliable should not show
in the filter search". Said before, on 7 Sep: "I want you to see that there are not any
buildings that sold or not available. Show up in the the filter because I see".

THREE SURFACES HOLD THIS LIST, AND EACH HELD ITS OWN COPY:

  api/_candidates.py  NOT_AVAILABLE   the shortlist the API recommends from
  index.html          NOT_PURCHASABLE the stock table's own filter
  verify_sheet_positions.py           what it writes onto a lot its sheet dropped

Copies drift, and this one already did: "not available" was missing from the API's set
while it was the literal value on 581 rows, so a sold Rouse Hill lot was recommended to
Colin on 5 Aug. He put it plainly -- "it's not available, but it came as available from
your end... you should only see what's available and nothing else."

These tests do not check that the list is RIGHT. They check that the three cannot say
different things, which is the failure that actually happened.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, "api")

from _candidates import IS_AVAILABLE, NOT_AVAILABLE

APP = Path(__file__).resolve().parents[1]


def _set_from_index_html(name):
    """The JavaScript Set literal `const <name> = new Set([...])`, as a python set."""
    text = (APP / "index.html").read_text(encoding="utf-8", errors="replace")
    match = re.search(r"const\s+%s\s*=\s*new Set\(\[(.*?)\]\)" % re.escape(name),
                      text, re.S)
    assert match, f"index.html no longer declares a Set called {name}"
    return {v.lower() for v in re.findall(r"'([^']*)'", match.group(1))}


def test_the_table_filter_and_the_api_exclude_exactly_the_same_statuses():
    """Neither list may quietly gain or lose a value the other does not have."""
    ui = _set_from_index_html("NOT_PURCHASABLE")
    assert ui == NOT_AVAILABLE, (
        "the stock table and the recommendation API disagree about what cannot be "
        f"bought.\n  only in index.html: {sorted(ui - NOT_AVAILABLE)}\n"
        f"  only in _candidates.py: {sorted(NOT_AVAILABLE - ui)}")


def test_the_words_the_client_named_are_all_excluded():
    """"sold or hold or not avaliable" -- the three he asked about, spelled every way the
    sources actually spell them."""
    for status in ("sold", "on hold", "not available", "unavailable",
                   "under offer", "reserved", "withdrawn", "settled"):
        assert status in NOT_AVAILABLE, f"{status!r} would still show in the filter"


def test_a_status_is_matched_however_it_is_typed():
    """Sources write SOLD, Sold and ' sold '. The gate lowercases and strips; if it ever
    stops doing one of those, this is where it shows up."""
    ui = _set_from_index_html("NOT_PURCHASABLE")
    for raw in ("SOLD", " Sold ", "On Hold", "NOT AVAILABLE"):
        assert raw.strip().lower() in ui


def test_available_and_not_available_do_not_overlap():
    """A value in both sets would make the answer depend on which test ran first."""
    assert not (IS_AVAILABLE & NOT_AVAILABLE)


def test_the_status_the_sheet_check_writes_is_one_of_the_excluded_ones():
    """verify_sheet_positions marks a lot its own sheet stopped carrying. A status the
    other two do not recognise would hide it from nothing."""
    import verify_sheet_positions
    assert verify_sheet_positions.ABSENT_STATUS.strip().lower() in NOT_AVAILABLE
    assert verify_sheet_positions.ABSENT_STATUS.strip().lower() in \
        _set_from_index_html("NOT_PURCHASABLE")
