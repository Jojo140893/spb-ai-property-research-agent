"""Open every price list we publish from, and check what we say about it is true.

Ahsan, 9 Sep 2026: *"i want you to confirm all the listings are present and when i open
the sheet they are at exact number that is written and if they are not then mark them not
available automatically and the buildings that are no longer in the sheets are marked
sold and the buildings that are sold or hold or not avaliable should not show in the
filter search."*

Everything else in this repo reasons about the database. This script reasons about the
SHEET: it downloads each source document, re-extracts it with today's code, and asks one
question per published listing -- **is it there, and is it on the line we said?**

Six answers, and each is acted on differently, because they are different claims:

  AT_STATED_ROW   the lot is where the app says it is. Nothing to do. A spreadsheet row is
                  found by its LINE and a PDF listing by its PAGE, and both are checked --
                  1,437 live rows claim a line, 4,866 claim a page.
  PRESENT         the lot is in the document, which has no lines to number -- a folder of
                  PDF brochures, say. Confirmed present; there is no line to be wrong
                  about, and the app does not claim one.
  MOVED           the lot is in the sheet, on a different line. The sheet was re-sorted;
                  the listing is fine and the POINTER is what is wrong, so the row number
                  is corrected. Never a reason to touch availability -- a lot that moved
                  from row 24 to row 11 is still for sale on row 11.
  ABSENT          the lot is not in the sheet, AND the sheet came back short by about that
                  many rows. The sheet is the builder's own statement of what is for sale,
                  so a lot missing from it is not for sale: availability is set to Not
                  Available, which is what hides it from the filter. Retirement to "sold"
                  stays with repair_rows, which waits for the absence to repeat -- see WHY
                  TWO STEPS below.
  UNMATCHED       the lot is not in the sheet, but the sheet came back WHOLE. A lot that
                  sold is gone from the file and the file shrinks; a file that did not
                  shrink has lost nothing, so this is a lot we failed to recognise. NO
                  claim is made. See UNEXPLAINED_ABSENCE_SLACK_SHARE -- this is the
                  difference between hiding 636 live properties and hiding 165 sold ones.
  UNREADABLE      the document would not download or would not parse. NO claim is made.
                  Silence from a file we could not open is not evidence about any lot in
                  it, and this is the case that has caused every bad mass-update here.

WHY TWO STEPS, NOT ONE. Marking Not Available is reversible in a day: the next harvest
that finds the lot writes its availability back. Retirement supersedes the row, and until
9 Sep 2026 that was one-way. So the fast, loud, self-correcting action happens here on
first sight, and the permanent one keeps its fortnight of evidence.

A HALF-READ SHEET IS NOT A SOLD-OUT SHEET. If a document parses but yields far fewer rows
than we hold from it, the read failed -- a login expired, a render cap, a changed layout.
PARTIAL_READ_SHARE refuses to mark anything from such a document and says so. One Proxima
project returned 3 of its 85 lots on 9 Sep; without this guard that is 82 lots "sold".

Run it:
    python verify_sheet_positions.py                 # report only, changes nothing
    python verify_sheet_positions.py --apply         # correct rows, mark absent lots
    python verify_sheet_positions.py --limit 5       # first 5 documents, for a quick look
"""

import argparse
import collections
import logging
import re
import sqlite3
import sys
from datetime import datetime

import config

logger = logging.getLogger(__name__)

AT_STATED_ROW, PRESENT, MOVED, ABSENT, UNMATCHED, UNREADABLE = (
    "at_stated_row", "present_no_line", "moved", "absent", "unmatched", "unreadable")

# A document that comes back holding less than this share of what we store from it has not
# been read. Same threshold and same reasoning as repair_rows.PARTIAL_READ_SHARE, kept as
# its own name because this one is measured against a FRESH read rather than against
# date_checked, and the two could reasonably diverge later.
PARTIAL_READ_MIN_SHARE = 60
PARTIAL_READ_MIN_ROWS = 10

# A LOT THAT SOLD IS GONE FROM THE FILE, SO THE FILE COMES BACK SHORTER. That is the whole
# of it. If we hold 118 rows from a document and it returns 118, nothing has been removed
# -- and 110 "absences" from it are 110 lots we failed to RECOGNISE, not 110 sales.
#
# The first full audit reported 681 absent and would have hidden 636 live properties on
# it. 414 of those sat in documents that came back the same size or larger than what we
# store from them. The partial-read guard above could not see it: it counts ROWS RETURNED,
# which was healthy, and the failure was in the MATCHING. Apartment price lists are where
# it bites -- Lindfield House, Live Caringbah, The Macquarie Collection -- because their
# rows carry a unit and a level where _lot_key expects a lot number and a street.
#
# So absences are trusted only up to the number of rows the file actually lost, plus a
# little slack for stock that arrived and left between two reads. Beyond that the document
# is making no statement we can read, and no claim is made about the rows we could not
# find. Applied to the real audit this keeps 165 absences across 27 documents and refuses
# 516 across 22.
UNEXPLAINED_ABSENCE_SLACK_SHARE = 10        # of what we store from that document
UNEXPLAINED_ABSENCE_SLACK_MIN = 3

# The status written to a lot its own sheet no longer carries. Spelled exactly as the
# published filter spells it -- see _AVAILABLE in api/_candidates.py and availPill in
# index.html. A status those two do not recognise would hide the lot from nothing.
ABSENT_STATUS = "Not Available"

# Returned by fetch when the link is a PAGE rather than a file -- a builder portal that
# has to be opened in a browser. Distinct from "nothing came back", because those rows are
# verified by the harvest, which drives Playwright; they are simply not this check's to
# make a claim about. A sentinel rather than a flag so it cannot be mistaken for data.
_A_PAGE_NOT_A_FILE = ("__a_page_not_a_file__",)

# Returned by fetch when SOME of what is behind the link came back and some did not. The
# document cannot be compared against what we store from it, because we cannot tell a lot
# that was removed from a lot that is in the file we failed to download. No claim is made.
_A_PIECE_IS_MISSING = ("__a_piece_is_missing__",)


def _row_key(text) -> str:
    """A stable key for one source row, tolerant of the noise a PDF reader leaves.

    Deliberately the same shape as repair_rows._row_key: the two match rows across a
    re-read of the same file, and a key that differed between them would let one repair
    contradict the other.
    """
    cleaned = re.sub(r"[^A-Za-z0-9$,./ -]", " ", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip().lower()[:120]


def _lot_key(row) -> str:
    """A second, coarser identity: the lot and its street, as the ADDRESS states them.

    source_text is the whole flattened line, so it changes when a PRICE changes -- and a
    price change is the single most common edit a price list gets. Matching on that alone
    reported live, correctly-listed lots as ABSENT the moment their price moved. The
    address label survives a re-price, so this key is what stands between a re-priced lot
    and being hidden from the client.

    It only works if both sides build it the same way. See the note below on lot_number.
    """
    addr = str(_get(row, "lot_address") or "")
    street = re.sub(r"[^a-z0-9 ]", " ", addr.split(",")[0].lower())
    street = " ".join(street.split()[:4])
    lot = re.sub(r"[^a-z0-9]", "", str(_get(row, "lot_number") or "").lower())
    # THE LOT NUMBER ONLY WHEN THE ADDRESS DOES NOT ALREADY CARRY IT, because the two
    # sides of this comparison do not fill lot_number the same way. A STORED row got its
    # lot_number after extraction, from stocklist_reparse's per-cohort rules; a FRESH
    # listing gets it only from feature_extract.parse_lot_number, which needs a literal
    # "Lot 12" or a stock code. So the stored key read "208|west 208 thornhill gardens"
    # and the fresh key read "west 208 thornhill gardens", and they could never match --
    # on 1,637 of 6,452 live rows, a quarter of the stock this script checks.
    #
    # Those rows fell back to matching the whole flattened line, which contains the price,
    # so a single re-price made a lot "absent from its own sheet". Omitting a lot number
    # the address already states makes the key symmetric and takes matching from 74% to
    # 96%; the remaining 4% are rows whose lot number appears nowhere in the address.
    if lot and lot not in street.split():
        return f"{lot} {street}".strip()
    return street


def _get(row, name):
    """row[name] for a sqlite3.Row, a dict, or an extractor listing."""
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return None


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# Words that say a lot's STATUS rather than which lot it is. They change between reads --
# that is their whole job -- so they must not count toward recognising a row.
_STATUS_TOKEN = re.compile(
    r"^(sold|available|hold|on|under|offer|reserved|withdrawn|settled|titled|"
    r"deposit|taken|pending|new|released|na|tbc|tba)$", re.I)

# How much of a stored row's wording must survive in a fresh line before the two are
# treated as the same lot. Below this the row is not recognised; it is NOT declared gone.
RESCUE_OVERLAP = 0.70


def _tokens(text) -> set:
    """The words that identify a row, with the ones that move between reads removed.

    Prices, statuses and small integers are dropped: a price list is re-priced constantly,
    a status column is meant to change, and a bare "3" is a bedroom count that means
    nothing on its own. What is left is the lot label, the street, the estate and the
    house design -- the words that say WHICH property this is.
    """
    out = set()
    for word in re.findall(r"[A-Za-z0-9$.,]+", str(text or "")):
        word = word.strip(".,").lower()
        if not word or "$" in word or _STATUS_TOKEN.match(word):
            continue
        if word.isdigit() and len(word) <= 3:
            continue
        out.add(word)
    return out


def _rescued(stored_row, fresh_tokens) -> bool:
    """True when a fresh line still reads like this row, even though no key matched.

    THE CASE THIS EXISTS FOR. Some price lists are scanned pictures. The nightly harvest
    OCRs them on GitHub's ubuntu runner with unpinned rapidocr-onnxruntime and PyMuPDF
    (requirements.txt); this check re-read them on Windows. Two OCR stacks read the same
    picture into different words -- "LOT408" against "LoT406" -- so neither the row key
    nor the lot key can ever match, and 288 of the first audit's 681 absences were that
    cohort alone. Apartment lists fail the same way for a different reason: their rows
    carry a unit and a level where the lot key expects a lot number and a street.

    A stored row that shares 70% of its identifying words WITH ITS OWN LOT NUMBER IN THEM
    is still on that page. We cannot say which line, so no position is claimed -- but
    "still on the page" is the opposite of "no longer for sale", and it is the difference
    between hiding a property and leaving it alone.

    This is the second of two layers, and the narrower one. A document whose file did not
    shrink has its absences refused wholesale by _drop_unexplained_absences, which is what
    catches the OCR and apartment-list cohorts. This one catches the single re-worded row
    inside a document that is otherwise read perfectly well.
    """
    mine = _tokens(_get(stored_row, "source_text")) or _tokens(
        _get(stored_row, "lot_address"))
    if len(mine) < 3:
        return False                       # too little to recognise anything by
    # THE LOT IDENTITY HAS TO BE PART OF WHAT MATCHES. Neighbouring lots on one street
    # share almost every word they have -- "2102 Racecourse Rd Fraser Rise" against
    # "2103 Racecourse Rd Fraser Rise" is 80% overlap -- so wording alone would rescue
    # every sold lot on a street from its own neighbours and make the whole check inert.
    # Where the row states a lot number, a line that does not carry that number is a
    # DIFFERENT lot, however similar it reads.
    lot = re.sub(r"[^a-z0-9]", "", str(_get(stored_row, "lot_number") or "").lower())
    # Against the RAW line, not the token set: _tokens drops integers of three digits or
    # fewer because a bare "3" is a bedroom count -- and a great many lot numbers are
    # three digits too, so the lot would never be found among the tokens it was filtered
    # out of. Checked as a whole word so lot 408 does not match 4085.
    wanted = re.compile(r"\b%s\b" % re.escape(lot), re.I) if lot else None
    for theirs, raw in fresh_tokens:
        if wanted and not wanted.search(raw):
            continue
        if len(mine & theirs) / len(mine) >= RESCUE_OVERLAP:
            return True
    return False


def check_document(stored, fresh, trust_lines=True):
    """Classify each stored row against a fresh read of its own document.

    `stored` are the rows the app publishes from this document; `fresh` are the listings
    today's extractor finds in it. Returns [(row, verdict, new_row_number)].

    `trust_lines` is False when the link resolved to several files -- a Dropbox folder of
    price lists, say. Each of them has a row 12, the stored row does not record which file
    it came from, so no line can be confirmed or corrected: presence is all that can
    honestly be said.

    Pure, and takes the fresh listings as an argument, so the whole decision table is
    testable without a network.
    """
    # WHICHEVER KIND OF POSITION THE DOCUMENT HAS. A spreadsheet numbers lines and a PDF
    # numbers pages; 1,437 live rows claim a row and 4,866 claim a page. Checking only
    # rows left the larger half of the promise unkept -- a stale page pointer sends Colin
    # to the wrong page of a 40-page price list exactly as a stale row number sent Coleen
    # to the wrong property.
    by_text, by_lot = {}, {}
    for f in fresh:
        where = (_int(_get(f, "source_row")), _int(_get(f, "source_page")))
        key = _row_key(_get(f, "source_text"))
        if key and key not in by_text:
            by_text[key] = where
        lot = _lot_key(f)
        if lot and lot not in by_lot:
            by_lot[lot] = where

    # (identifying tokens, the raw line) per fresh listing. The raw line is kept because
    # the lot number has to be looked for in text _tokens deliberately filters.
    fresh_tokens = []
    for f in fresh:
        tokens = _tokens(_get(f, "source_text"))
        if tokens:
            fresh_tokens.append((tokens, "%s %s" % (_get(f, "source_text") or "",
                                                    _get(f, "lot_address") or "")))

    out = []
    for r in stored:
        # A row is numbered by its LINE, a PDF listing by its PAGE, and a row never holds
        # both. Which one this document speaks is decided by what came back, not assumed.
        stated_row, stated_page = (_int(_get(r, "source_row")),
                                   _int(_get(r, "source_page")))
        by_page = bool(stated_page) and not stated_row
        found_where = by_text.get(_row_key(_get(r, "source_text")))
        if found_where is None:
            found_where = by_lot.get(_lot_key(r))
        found = None if found_where is None else found_where[1 if by_page else 0]
        stated = stated_page if by_page else stated_row
        if found_where is None:
            # NOT RECOGNISED IS NOT GONE. Look for the row's own wording anywhere in the
            # document before saying the builder removed it.
            out.append((r, UNMATCHED if _rescued(r, fresh_tokens) else ABSENT, None))
        elif not trust_lines:
            out.append((r, PRESENT, None))
        elif not found:
            # PRESENT, BUT THE DOCUMENT HAS NO LINES TO NUMBER. A dropbox folder of PDF
            # brochures is a real source and its lots are really in it, but "row 12" is
            # not a thing that exists there. The first run of this script called all 646
            # of them MOVED and offered to write row 0 -- a correction that would have
            # replaced a truthful absence with a meaningless number.
            out.append((r, PRESENT, None))
        elif not stated:
            # In the sheet, and the app never claimed a line for it. Recording the line we
            # can now see is a gain, not a correction.
            out.append((r, MOVED, found))
        elif found == stated:
            out.append((r, AT_STATED_ROW, stated))
        else:
            out.append((r, MOVED, found))
    return out


def _fetchers():
    """The network pieces, imported late so the pure logic above tests without them.

    READ THE DOCUMENT THE WAY THE HARVEST READS IT, or the comparison is meaningless.

    The first version of this script fetched each source_url with document_suburb.fetch --
    one URL, one GET, one file. That is not what these links are. sources/remote_stocklist
    exists because ONE LINK IS NOT ONE FILE: a Dropbox "Access Portal" folder holds a
    dozen price lists, a Google workbook holds one tab per estate, and Smartsheet and
    SharePoint each need their own dance to yield bytes at all. 96 of 142 stock links are
    Dropbox folders.

    Fetched naively, such a link answers with HTML or with one arbitrary file, so the rows
    that live in the OTHER files in that folder match nothing -- and this script would
    have called them "absent from their own sheet" and hidden live stock. That is almost
    certainly a large part of the 666 the first pass reported, and it is the reason 989
    came back unreadable.

    So the fetch here is remote_stocklist.fetch_stocklist, the harvest's own, and the
    "document" compared against is every piece behind the link concatenated -- because
    every piece behind the link is what the row could have come from.
    """
    import document_suburb
    from sources.remote_stocklist import KIND_HTML, fetch_stocklist
    from sources.spreadsheet_extract import extract_stocklist

    def fetch(url):
        """Every downloadable piece behind one link, as a list of (label, bytes).

        BOTH READERS, IN THIS ORDER, because neither is right on its own and the failures
        are silent in opposite directions.

        remote_stocklist picks its handler by HOST, so every dropbox.com link goes to the
        folder handler. Most stored source_urls are not folder roots -- they are the link
        to ONE file inside the folder, already resolved by an earlier harvest
        (".../AKlDGt.../Atlas%20Price%20New.pdf?dl=1"). Asked to list that as a folder,
        Dropbox returns nothing, and a probe of the three largest documents reported all
        677 of their listings unreadable. A plain GET reads every one of them.

        The reverse is just as bad: a plain GET on a folder ROOT answers with one file or
        with HTML, so the rows living in the folder's other price lists match nothing and
        get called "absent from their own sheet" -- live stock, hidden.

        So: the harvest's reader first, and a direct GET when it comes back empty-handed.
        A document neither can read yields no pieces, and no claim is made about it.
        """
        got = fetch_stocklist(url, label=url)
        pieces = [(f.label or f.url or url, f.data) for f in got if f.data]
        # A PIECE THAT DID NOT ARRIVE IS NOT A PIECE THAT IS NOT THERE.
        #
        # remote_stocklist reports a failed piece by setting `problem` and returning
        # normally -- a per-file GET that 404s, a subfolder it cannot list, or one of the
        # hard caps _DBX_MAX_FILES=12 / _DBX_MAX_FOLDERS=20 quietly truncating a large
        # folder. Keeping only the pieces with bytes threw that signal away, and the
        # result is the worst shape this script can be handed: a folder that returned 8
        # of its 12 files looks exactly like a complete list that has shrunk. Its missing
        # listings then supply the absences AND the shrinkage together, so
        # `unexplained = len(absent) - shrank` cancels to zero and every absence is
        # believed. Neither of the other guards sees it -- the 60% partial-read floor is
        # a share of ROWS (last night's report has a list at 42 of 70, one row above the
        # cliff), and a folder short one file hashes differently, so the sha256 guard
        # reads it as an edited price list.
        if any(f.problem for f in got):
            logger.info("%s: %d piece(s) did not arrive (%s) - making no claim",
                        url[:60], sum(1 for f in got if f.problem),
                        next(f.problem for f in got if f.problem)[:70])
            return _A_PIECE_IS_MISSING
        if pieces:
            return pieces
        # A PAGE, NOT A FILE. remote_stocklist answers KIND_HTML with NO bytes on purpose:
        # it is telling the caller to open this one in a browser, which is what
        # sources/e_agent.py does with adaptive_extract. 223 Hermitage package pages and
        # 30 Torsion brochure endpoints are that shape. They ARE verified -- by the
        # harvest, which drives Playwright -- just not here, and reporting them as
        # "unreadable" understates coverage by 254 listings and reads as a failure.
        if got and all(f.kind == KIND_HTML for f in got):
            return _A_PAGE_NOT_A_FILE
        data = document_suburb.fetch(url)
        return [(url, data)] if data else []

    def extract(pieces, source_label="", builder_hint=""):
        """The listings in all of them, each stamped with the piece it came from.

        A row number is only meaningful WITHIN one file, so the piece label travels with
        it: two files in a folder both have a row 12 and they are not the same line.
        """
        out = []
        for label, data in pieces:
            try:
                got = extract_stocklist(data, source_label=label, builder_hint=builder_hint)
            except Exception as exc:
                logger.info("cannot parse piece %s (%s)", str(label)[:60],
                            type(exc).__name__)
                continue
            for listing in got:
                listing["source_piece"] = label
            out.extend(got)
        return out

    return fetch, extract


def _digest(pieces) -> str:
    """A sha256 over everything behind one link, or "" when nothing came back.

    Order-independent, because a Dropbox folder does not promise to list its files the
    same way twice and a reshuffled folder is not a changed price list.
    """
    if not pieces:
        return ""
    import hashlib
    if isinstance(pieces, (bytes, bytearray)):
        return hashlib.sha256(pieces).hexdigest()   # a caller that handed over one blob
    h = hashlib.sha256()
    try:
        parts = sorted(hashlib.sha256(data).digest() for _label, data in pieces)
    except (TypeError, ValueError):
        return ""                                   # an opaque fetch result; claim nothing
    for chunk in parts:
        h.update(chunk)
    return h.hexdigest()


# A document that yields this share or less of what it yielded last time has been
# truncated, not emptied by sales. Deliberately generous -- a list really can lose half
# its stock in a fortnight -- because this is the last guard, not the first.
TRUNCATED_AGAINST_LAST_RUN = 50


def verify(rows, fetch=None, extract=None, limit=0, on_document=None, previous=None,
           previous_rows=None):
    """Open every document behind these rows and check what we say about each listing.

    Returns (results, per_document) where results is [(row, verdict, new_row)] and
    per_document maps url -> {"stored": n, "fresh": n, "sha256": ..., "verdicts": Counter}.

    `previous` maps url -> sha256 from an earlier run's report. A document whose bytes are
    unchanged since then cannot have lost a lot, so none of its absences are believed.
    """
    previous = previous or {}
    previous_rows = previous_rows or {}
    if fetch is None or extract is None:
        _f, _e = _fetchers()
        fetch, extract = fetch or _f, extract or _e

    wanted = collections.defaultdict(list)
    for r in rows:
        if _get(r, "superseded_by"):
            continue
        url = str(_get(r, "source_url") or "").strip()
        # Proxima stores the projects INDEX as every row's url -- it is a portal, not a
        # sheet, and there is no document to open and count lines in. Its stock is
        # verified by the harvest that walks the accordions, not here.
        if not url.startswith("http") or "proxima" in url.lower():
            continue
        wanted[url].append(r)

    order = sorted(wanted.items(), key=lambda kv: -len(kv[1]))
    if limit:
        order = order[:limit]

    results, per_doc = [], {}
    for n, (url, group) in enumerate(order, 1):
        if on_document:
            on_document(n, len(order), url, len(group))
        data = fetch(url)
        digest = _digest(data)
        fresh = []
        if data:
            try:
                fresh = extract(data, source_label="verify", builder_hint="")
            except Exception as exc:                   # a document today's code cannot read
                logger.info("cannot parse %s (%s)", url[:70], type(exc).__name__)
                fresh = []
        if data is _A_PIECE_IS_MISSING:
            per_doc[url] = {"stored": len(group), "fresh": 0, "incomplete_fetch": True,
                            "verdicts": collections.Counter({UNREADABLE: len(group)})}
            results.extend((r, UNREADABLE, None) for r in group)
            continue
        if data is _A_PAGE_NOT_A_FILE:
            per_doc[url] = {"stored": len(group), "fresh": 0, "a_page_not_a_file": True,
                            "verdicts": collections.Counter({UNREADABLE: len(group)})}
            results.extend((r, UNREADABLE, None) for r in group)
            continue
        if not fresh:
            per_doc[url] = {"stored": len(group), "fresh": 0,
                            "verdicts": collections.Counter({UNREADABLE: len(group)})}
            results.extend((r, UNREADABLE, None) for r in group)
            continue
        # AGAINST LAST NIGHT'S OWN READ, not only against what we store. A folder that
        # gave up 40 rows last night and 12 tonight has been truncated, whatever its hash
        # says -- and the hash cannot see it, because a folder short one file hashes
        # differently and so reads as an edited price list.
        was = previous_rows.get(url) or 0
        if was and len(fresh) * 100 // was <= TRUNCATED_AGAINST_LAST_RUN:
            per_doc[url] = {"stored": len(group), "fresh": len(fresh), "partial": True,
                            "read_last_run": was,
                            "verdicts": collections.Counter({UNREADABLE: len(group)})}
            results.extend((r, UNREADABLE, None) for r in group)
            continue
        # THE GUARD. A sheet that comes back far shorter than what we hold from it has not
        # been read, and every "absent" it produces is an artefact of that.
        share = len(fresh) * 100 // max(len(group), 1)
        if len(group) >= PARTIAL_READ_MIN_ROWS and share < PARTIAL_READ_MIN_SHARE:
            per_doc[url] = {"stored": len(group), "fresh": len(fresh), "partial": True,
                            "verdicts": collections.Counter({UNREADABLE: len(group)})}
            results.extend((r, UNREADABLE, None) for r in group)
            continue
        # A ROW NUMBER IS ONLY MEANINGFUL WITHIN ONE FILE. Most of these links are Dropbox
        # folders holding a dozen price lists, and every one of them has a row 12. The
        # stored row does not record which file inside the folder it came from, so when a
        # link resolves to more than one piece there is no line to confirm or correct --
        # only presence. Saying "row 12" from the wrong file is the exact defect this
        # script was written to stop.
        pieces = {_get(f, "source_piece") for f in fresh}
        checked = check_document(group, fresh, trust_lines=len(pieces) <= 1)
        # A FILE THAT DID NOT CHANGE HAS NOT LOST A LOT. The strongest statement available
        # and the cheapest: if the bytes are identical to the last run's, the builder has
        # not touched the list, so nothing on it was sold or withdrawn and every absence
        # is ours. Row counts only approximate this; a hash settles it.
        unchanged = bool(digest) and previous.get(url) == digest
        if unchanged:
            checked = [(r, UNMATCHED if v == ABSENT else v, n) for r, v, n in checked]
        checked, unexplained = _drop_unexplained_absences(checked, len(group), len(fresh))
        per_doc[url] = {"stored": len(group), "fresh": len(fresh), "sha256": digest,
                        "pieces": len(pieces), "unexplained_absence": unexplained,
                        "unchanged_since_last_run": unchanged,
                        "verdicts": collections.Counter(v for _r, v, _n in checked)}
        results.extend(checked)
    return results, per_doc


def _drop_unexplained_absences(checked, stored, fresh):
    """Withdraw a document's ABSENT verdicts when the file did not actually shrink.

    Returns (checked, unexplained_count). A MATCH is positive evidence and is left alone:
    a row recognised by its text or its lot key really is on the line the read found it on,
    so AT_STATED_ROW and MOVED survive. A NON-match is only evidence when the file lost
    rows to account for it.

    See UNEXPLAINED_ABSENCE_SLACK_SHARE for what this cost the first audit.
    """
    absent = [i for i, (_r, v, _n) in enumerate(checked) if v == ABSENT]
    if not absent:
        return checked, 0
    shrank = max(0, stored - fresh)
    # NO FREE ALLOWANCE FOR A DOCUMENT THAT DID NOT SHRINK. The slack exists for churn --
    # stock arriving and leaving between two reads, so the net count barely moves while a
    # few rows really did go. A file that came back the same size or LARGER has lost
    # nothing, and giving it three absences for nothing let 29 rows through in 16 such
    # documents, 22 of them from the OCR cohort where matching is least trustworthy.
    slack = 0 if not shrank else max(
        UNEXPLAINED_ABSENCE_SLACK_MIN,
        stored * UNEXPLAINED_ABSENCE_SLACK_SHARE // 100)
    unexplained = len(absent) - shrank
    if unexplained <= slack:
        return checked, max(0, unexplained)
    # ALL OF THEM, not the excess. Which rows are gone and which are unrecognised cannot
    # be told apart from here, and picking some to hide would be guessing with a client's
    # stock. A document whose matching is this unreliable makes no absence statement.
    out = list(checked)
    for i in absent:
        out[i] = (out[i][0], UNMATCHED, None)
    return out, unexplained


def _rows(conn):
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT * FROM buildings").fetchall()


def _previous_digests(conn) -> dict:
    """What each document looked like the last time this ran, url -> sha256.

    FROM THE DATABASE, NOT FROM THE REPORT FILE. The nightly runs on a fresh GitHub
    runner: data/sheet_check.json is written at step 4/7 and thrown away with the
    workspace at the end of the job, so a report-to-report comparison would never once
    fire in production. The database is the one thing that survives the night, because
    state_sync carries it to the bucket and back.
    """
    try:
        return {r[0]: r[1] for r in
                conn.execute("SELECT url, sha256 FROM document_reads WHERE sha256 <> ''")}
    except sqlite3.Error:
        return {}                          # table not created yet; nothing to compare


def _previous_row_counts(conn) -> dict:
    """How many rows each document yielded last time, url -> count.

    _remember_digests has been writing this every night and nothing read it. It is the
    cheapest possible check on a truncated fetch: a folder that gave up 40 rows last night
    and 12 tonight has not been read, whatever its hash says -- a folder short one file
    hashes differently, so the sha256 guard reads that as an edited price list.
    """
    try:
        return {r[0]: int(r[1] or 0) for r in
                conn.execute("SELECT url, rows_read FROM document_reads "
                             "WHERE COALESCE(rows_read,0) > 0")}
    except sqlite3.Error:
        return {}                          # table not created yet; nothing to compare


def _remember_digests(conn, per_doc) -> int:
    """Record what each document looked like today, for tomorrow to compare against."""
    stamp = datetime.now().isoformat(timespec="seconds")
    rows = [(url, d.get("sha256") or "", int(d.get("fresh") or 0), stamp)
            for url, d in per_doc.items() if d.get("sha256")]
    if not rows:
        return 0
    try:
        conn.executemany(
            "INSERT INTO document_reads (url, sha256, rows_read, seen_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(url) DO UPDATE SET "
            "sha256=excluded.sha256, rows_read=excluded.rows_read, seen_at=excluded.seen_at",
            rows)
        conn.commit()
    except sqlite3.Error as exc:           # a database written before the table existed
        logger.info("could not record document hashes (%s)", exc)
        return 0
    return len(rows)


def _apply_report(conn, rows, path):
    """Write the corrections a saved report describes, changing nothing else."""
    import json
    with open(path, encoding="utf-8") as fh:
        report = json.load(fh)
    by_id = {r["id"]: r for r in rows}
    verdicts = report.get("rows") or []
    print(f"applying {path}, generated {report.get('generated', '?')}")
    print(f"  {len(verdicts)} verdict(s) recorded")

    moved = [(by_id[v["id"]], v["new_row"]) for v in verdicts
             if v.get("verdict") == MOVED and v.get("new_row") and v["id"] in by_id]
    absent = [by_id[v["id"]] for v in verdicts
              if v.get("verdict") == ABSENT and v["id"] in by_id]
    gone = len(verdicts) - len([v for v in verdicts if v["id"] in by_id])
    if gone:
        print(f"  {gone} row(s) in the report are no longer live and were skipped")

    cur = conn.cursor()
    for r, new_position in moved:
        column = ("source_page" if (_int(r["source_page"]) and not _int(r["source_row"]))
                  else "source_row")
        cur.execute(f"UPDATE buildings SET {column}=? WHERE id=?", (new_position, r["id"]))
    marked = 0
    for r in absent:
        if not _is_available(r["availability_status"]):
            continue
        cur.execute("UPDATE buildings SET availability_status=?, status_previous=? "
                    "WHERE id=?",
                    (ABSENT_STATUS, r["availability_status"], r["id"]))
        marked += 1
    conn.commit()
    print(f"applied: {len(moved)} position(s) corrected, "
          f"{marked} listing(s) marked {ABSENT_STATUS} (absent from their own sheet)")
    return 0


def main(args):
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    conn = sqlite3.connect(str(config.DATABASE_PATH))
    rows = _rows(conn)
    live = [r for r in rows if not r["superseded_by"]]
    print(f"live listings: {len(live)}")

    def progress(n, total, url, count):
        print(f"  [{n:>3}/{total}] {count:>4} listing(s)  {url[:64]}", flush=True)

    if args.from_report:
        # APPLY EXACTLY WHAT WAS REVIEWED. A full pass re-downloads 484 documents and
        # takes an hour, and the verdicts it produces are not guaranteed identical to the
        # ones a person just read -- a builder can edit a sheet in between. When the
        # decision is "hide 95 properties", the thing applied has to be the thing that was
        # checked, so this replays a saved report instead of forming new opinions.
        return _apply_report(conn, rows, args.from_report)

    # LAST NIGHT'S HASHES, so a file nobody edited cannot be read as a file that lost
    # lots. run_daily.py passes --save data/sheet_check.json every night, and the run
    # reads back what the previous one wrote before overwriting it.
    previous = _previous_digests(conn)
    if previous:
        print(f"comparing against {len(previous)} document(s) hashed on the last run")

    results, per_doc = verify(rows, limit=args.limit, on_document=progress,
                              previous=previous,
                              previous_rows=_previous_row_counts(conn))
    tally = collections.Counter(v for _r, v, _n in results)
    print()
    # WHAT WAS NOT CHECKED, STATED FIRST. "Confirm all the listings are present" is only
    # answered by a number that adds up: a check that silently covers two thirds of the
    # stock and reports a clean bill reads exactly like one that covers all of it.
    checked = {id(r) for r, _v, _n in results}
    missed = collections.Counter()
    for r in live:
        if id(r) in checked:
            continue
        url = str(_get(r, "source_url") or "").strip()
        if "proxima" in url.lower():
            missed["Proxima - a portal, not a sheet; checked by the nightly harvest "
                   "and repair_rows per project"] += 1
        elif not url.startswith("http"):
            missed["no document to open (emailed or hand-entered)"] += 1
        else:
            missed["not reached in this run"] += 1
    print(f"coverage: {len(results)} of {len(live)} live listing(s) checked against "
          f"their own document")
    for why, n in missed.most_common():
        print(f"   {n:6}  not checked: {why}")
    print()
    print(f"documents opened: {len(per_doc)}")
    for name, label in ((AT_STATED_ROW, "on the exact line the app names"),
                        (PRESENT, "in the document, which has no line numbers"),
                        (MOVED, "in the document, on a different line or page"),
                        (ABSENT, "NOT in the sheet, and the sheet lost that many rows"),
                        (UNMATCHED, "not recognised in a sheet that came back whole - "
                                    "no claim made"),
                        (UNREADABLE, "document unreadable - no claim made")):
        print(f"   {tally[name]:6}  {label}")

    pages = [u for u, d in per_doc.items() if d.get("a_page_not_a_file")]
    if pages:
        rows_behind = sum(per_doc[u]["stored"] for u in pages)
        print(f"\n{len(pages)} link(s) are a PAGE, not a file ({rows_behind} listing(s))"
              f" - opened in a browser by the harvest, not checkable here")
    unchanged = [u for u, d in per_doc.items() if d.get("unchanged_since_last_run")]
    if unchanged:
        print(f"\n{len(unchanged)} document(s) are byte-identical to the last run - the "
              f"builder has not touched them, so no absence from them is believed")
    partial = [u for u, d in per_doc.items() if d.get("partial")]
    if partial:
        print(f"\n{len(partial)} document(s) came back far shorter than what we hold "
              f"from them - treated as unread, nothing marked:")
        for u in partial[:6]:
            d = per_doc[u]
            print(f"     {d['fresh']:>4} read / {d['stored']:>4} stored   {u[:58]}")

    moved = [(r, n) for r, v, n in results if v == MOVED]
    absent = [r for r, v, _n in results if v == ABSENT]
    print(f"\npositions to correct: {len(moved)}")
    for r, n in moved[:5]:
        print(f"     {str(r['builder_name'] or '?')[:20]:22} row {r['source_row']} -> {n}"
              f"   {str(r['lot_address'] or '')[:40]}")
    already = sum(1 for r in absent if not _is_available(r["availability_status"]))
    print(f"\nlistings their own sheet no longer carries: {len(absent)} "
          f"({already} already marked unavailable, {len(absent) - already} to mark)")
    for r in absent[:5]:
        print(f"     {str(r['builder_name'] or '?')[:20]:22} "
              f"{str(r['availability_status'] or '(none)')[:14]:16} "
              f"{str(r['lot_address'] or '')[:40]}")

    _remember_digests(conn, per_doc)

    if args.save:
        # THE EVIDENCE, KEPT. 666 listings came back absent on the first full pass, and
        # "should I hide 628 properties" is not a question to answer from a summary line.
        # The report says which document every verdict came from, so a run can be audited
        # -- and so two runs can be compared, which is the only way to tell a builder
        # selling stock from an extractor quietly getting worse at reading one file.
        import json
        report = {
            "generated": datetime.now().isoformat(timespec="seconds"),
            "live": len(live), "checked": len(results),
            "documents": [dict(d, url=u) for u, d in per_doc.items()],
            "rows": [{"id": r["id"], "verdict": v, "new_row": n,
                      "builder": r["builder_name"], "address": r["lot_address"],
                      "status": r["availability_status"], "url": r["source_url"]}
                     for r, v, n in results],
        }
        with open(args.save, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, default=dict)
        print(f"report written to {args.save}")

    if not args.apply:
        print("\n(dry run - nothing written. Re-run with --apply)")
        return 0

    cur = conn.cursor()
    for r, new_position in moved:
        # Back into the column the row speaks. A PDF listing is found by its PAGE and a
        # spreadsheet row by its LINE; writing a page number into source_row would put a
        # number in the wrong column and make provenance say "row 7" about page 7.
        column = ("source_page" if (_int(r["source_page"]) and not _int(r["source_row"]))
                  else "source_row")
        cur.execute(f"UPDATE buildings SET {column}=? WHERE id=?",
                    (new_position, r["id"]))
    marked = 0
    for r in absent:
        if not _is_available(r["availability_status"]):
            continue                       # already hidden; leave the builder's own word
        # status_previous only. There is no availability_checked_at column -- writing one
        # would fail the transaction and silently roll back every row number corrected in
        # the same run, which is exactly how a suburb repair lost a whole night's work
        # once already (see repair_rows, the note on suburb_source).
        cur.execute("UPDATE buildings SET availability_status=?, status_previous=? "
                    "WHERE id=?",
                    (ABSENT_STATUS, r["availability_status"], r["id"]))
        marked += 1
    conn.commit()
    print(f"\napplied: {len(moved)} row number(s) corrected, "
          f"{marked} listing(s) marked {ABSENT_STATUS} (absent from their own sheet)")
    return 0


def _is_available(status) -> bool:
    """Whether this status would let the listing through the published filter.

    Asks the ONE set the published app asks. Re-stating the list here is how "not
    available" came to be missing from the API's copy for weeks while every other surface
    honoured it, and a sold Rouse Hill lot was recommended to Colin on 5 Aug.
    """
    sys.path.insert(0, "api") if "api" not in sys.path else None
    from _candidates import NOT_AVAILABLE
    return str(status or "").strip().lower() not in NOT_AVAILABLE


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="correct row numbers and mark absent listings unavailable")
    ap.add_argument("--from-report", metavar="PATH",
                    help="apply a saved report instead of re-reading every document")
    ap.add_argument("--save", metavar="PATH",
                    help="write a JSON report of every verdict, for auditing")
    ap.add_argument("--limit", type=int, default=0,
                    help="only open the N largest documents (for a quick look)")
    sys.exit(main(ap.parse_args()))
