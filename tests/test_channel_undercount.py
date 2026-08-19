"""Portals, email and drive drops: read all of it, or say what was skipped.

The remaining findings from the pipeline-wide audit for the failure that hid 78 of
Proxima's 118 projects. Each of these loses stock in a way no existing guard can see,
because every guard downstream is calibrated on a whole CHANNEL: the five direct portals
pool into one "Direct Builder Portal (live)" whose floor is disabled below 20 rows, and
one lost email is ~100 listings against a channel of a few hundred.
"""
import re

import pytest

import sources.email_inbox as EI
import sources.builder_portals as BP
import drive_ingest


# ------------------------------------------------------------------ attachments

def test_a_stocklist_followed_by_a_signature_image_is_not_dropped():
    """DATA_EXT is anchored on "$", and it was tested against the JOINED filenames — so
    only the LAST attachment could ever satisfy it. A message whose stocklist was
    followed by a .png signature read as having no data at all.

    The live log said "scanned 20 message(s), 8 relevant" and gave no reason for any of
    the 12 rejected.
    """
    names = ["NSW Stocklist.xlsx", "signature.png"]
    assert not EI.DATA_EXT.search(" ".join(names)), "the old joined test"
    assert any(EI.DATA_EXT.search(n) for n in names), "the per-name test"


@pytest.mark.parametrize("name", [
    "Stock.xlsx", "Stock.xlsm", "Stock.xls", "Prices.csv", "Availability.pdf"])
def test_every_stocklist_format_is_accepted(name):
    """xlsm was missing. Every sibling module accepts it and extract_stocklist
    dispatches on the PK magic bytes, so it parses identically — without it, a macro
    workbook from an unknown-domain sender dropped the whole message."""
    assert EI.DATA_EXT.search(name)


@pytest.mark.parametrize("name", ["photo.png", "cover.docx", "archive.zip", "note.txt"])
def test_things_that_are_not_stocklists_are_still_ignored(name):
    assert not EI.DATA_EXT.search(name)


def test_the_summary_line_reports_unreadable_messages():
    """One lost message is about a hundred listings, comfortably inside the collapse
    floor — so if the summary does not carry it, nothing anywhere does."""
    src = (EI.__file__ or "")
    text = open(src, encoding="utf-8").read()
    assert "unreadable" in text
    assert re.search(r"scanned %d message\(s\), %d relevant, %d unreadable", text)
    assert "scanned = matched = failed = 0" in text, (
        "the counter must be initialised outside the try, or the summary NameErrors "
        "on the path where the scan aborts")


def test_an_imap_not_ok_is_no_longer_silent():
    text = open(EI.__file__, encoding="utf-8").read()
    assert 'typ != "OK"' in text, (
        "typ was assigned and never checked, so an imaplib 'NO' fell into the bare "
        "except as a TypeError and vanished")


# ------------------------------------------------------------------ portals

def test_a_saved_session_has_to_prove_itself():
    """A signed-out portal that still renders a few public cards satisfied "did anything
    come back" and was harvested as a success, with every row stamped verified: True.
    has_session was computed and then never referenced again."""
    text = open(BP.__file__, encoding="utf-8").read()
    assert "signed_in" in text
    assert "cfg.logged_in_selector" in text
    assert "or not signed_in" in text, "a session that cannot be proved must re-login"


def test_a_failed_re_login_refuses_the_public_view():
    text = open(BP.__file__, encoding="utf-8").read()
    assert "could not sign in; refusing the" in text


def test_extra_stock_pages_survive_a_mapped_selector_win():
    """/display + /granny are pages the main grid does not contain, so they are additive
    to whichever path wins — but they were appended only to `adaptive` and discarded
    wholesale when the mapped selectors read more. The log had already claimed them:
    "[Bathla] +4 listing(s) from .../granny"."""
    text = open(BP.__file__, encoding="utf-8").read()
    assert "results = (results or []) + more" in text
    assert "adaptive = (adaptive or []) + more" in text


def test_an_empty_extra_page_says_so():
    """/promotions has produced zero log lines in ten runs, and nobody can tell "this
    page is empty" from "this page 404s"."""
    text = open(BP.__file__, encoding="utf-8").read()
    assert "returned nothing" in text


# ------------------------------------------------------------------ drive drops

def test_a_dropped_csv_goes_through_the_real_extractor():
    """ingest_csv wraps its ENTIRE row loop in one try and returns the rows read so far,
    so the first row that raises ends the file and the partial list is returned as the
    whole thing. Its only signal is a print() that never says it is truncated."""
    text = open(drive_ingest.__file__, encoding="utf-8").read()
    disp = text[text.index("for file_path in self.watch_dir.iterdir()"):]
    disp = disp[:disp.index("return packages")]
    assert "'.csv'" in disp
    csv_branch = disp[disp.index("'.csv'"):disp.index("elif")]
    assert "ingest_spreadsheet" in csv_branch, "the csv branch must not use ingest_csv"


def test_the_drive_whitelist_accepts_xlsm():
    text = open(drive_ingest.__file__, encoding="utf-8").read()
    assert "'.xlsm'" in text


def test_comparables_are_still_excluded():
    """comparables*.csv is market benchmark data for BenchmarkEngine, not stock."""
    text = open(drive_ingest.__file__, encoding="utf-8").read()
    assert "startswith('comparables')" in text
