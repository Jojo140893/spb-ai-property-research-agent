"""
Mark older captures of a lot that is also stored fresher.

WHY THERE ARE ANY. `content_hash` includes `lot_number` and a key derived from the
source row's text. Both come out of the extractor, so when the extractor's output for
a lot changes between harvests — a field starts or stops being populated, a stocklist
reformats a line — the identity changes with it and the next run INSERTS a second row
instead of updating the first. Nothing is wrong with the upsert; it is doing exactly
what it was asked to. The identity simply is not stable across extractor changes, and
every improvement to extraction is therefore also a duplication event.

Found on 2026-08-01: 777 rows, 12% of the table, are surplus copies of a lot already
stored. One lot was in there three times —

    27 Jul  Direct Portal  lot_number '9'   no source text   $  962,351
    29 Jul  E-Agent        lot_number '9'   text populated   $1,058,877
    03 Aug  Direct Portal  lot_number NULL  text populated   $1,058,877

WHY THIS IS NOT "THE SAME LOT, CHEAPER ELSEWHERE". It looks like a price comparison
and it is not one. Of 225 lots carrying more than one price, 224 conflict WITHIN a
single channel — the same seller quoting two numbers, which cannot be a competing
offer — and in 152 the cheaper row is simply the OLDEST capture. Surfacing "cheaper
elsewhere" from this would recommend a July price that has since risen, which is worse
than saying nothing.

WHAT COUNTS AS THE SAME LOT. Deliberately conservative: same state, same suburb, same
address label, and an IDENTICAL spec (beds, baths, cars, house size, land size). Two
genuinely different packages on one lot differ in spec and are left alone — 36 groups
did, and they are not touched.

AND THE SAME LINE ARRIVING TWICE. Added 8 Aug 2026 while unpicking the Hattan split.
The same builder stocklist reaches us as E-Agent's pooled PDF and as the builder's own
email, and BOTH copies were live: 62 of Hattan's 180 live rows were surplus, $46.1m of
$129.8m. That is not a naming problem — renaming the rows would have left every one of
them standing — so it is fixed here, in the rule, and not by a script writing
superseded_by directly. main() below CLEARS superseded_by table-wide on every nightly
run and re-derives it, so a hand-set mark evaporates at 03:27 with no error.

MARKED, NOT DELETED. The older capture is real history and its price is what was
advertised then. It keeps its row and gains a pointer to the row that replaced it.

AND THE SAME LINE READ AGAIN WITH A DIFFERENT STATUS WORD. Added 17 Sep 2026 (Part C,
W01). Howard lot 21 was stored twice on one Smartsheet: "Howard Available 21 Howard
Crossing ..." read 15/09 and "Howard ON HOLD 21 Howard Crossing ..." read 16/09. Both
stayed live, so Colin's "QLD under $750k" search recommended a lot the builder had put
on hold the next morning. The identity split because database.py cuts the text to 100
characters BEFORE it strips the status word, and none of the three passes below could
join them: text_key compares the text WITH the status word, lot_key needs an identical
lot_address, and flattened rows carry the status word there too. See
find_status_superseded. It is a FOURTH, ADDITIVE pass and it is switchable:

    SPB_STATUS_PASS=0    (or off/false/no) turns it off and restores the three-pass rule
                         exactly. Unset, empty or anything else: ON.

If the fourth pass raises, the run keeps the three existing passes' marks, prints the
traceback with an ERROR line, and exits DEGRADED_EXIT (70). run_daily treats 70 as
"finished, publish, but mark the run DEGRADED", so the nightly still publishes and the
CI job goes red afterwards instead of the dashboard silently keeping yesterday's data.

    python supersede_duplicates.py --dry-run
    python supersede_duplicates.py
"""

import argparse
import hashlib
import os
import re
import shutil
import sqlite3
import sys
import traceback
from collections import Counter, defaultdict
from urllib.parse import urlsplit

from sources.scraper_base import normalise_money_spacing
from datetime import datetime

import config

FIELDS = ("superseded_by", "superseded_at")

# The switch for the fourth pass. ON unless it is explicitly turned off, because the
# harm it removes (a lot recommended as Available the day after its own list said On
# Hold) is live today; OFF exists so a bad night can be undone without a code revert.
STATUS_PASS_ENV = "SPB_STATUS_PASS"
_OFF_WORDS = {"0", "off", "false", "no"}

# The exit code for "the three passes ran and were written, the fourth pass failed and
# was dropped". run_daily._run treats it as success-but-degraded; it must equal
# run_daily.DEGRADED_EXIT (a test pins them). 2 is taken: it means a harvest CHANNEL read
# nothing, and CI tells whoever reads that to refresh a portal login. 3 is unsafe: it is
# what a Windows process exits with when the C runtime aborts, so a crash would read as a
# fallback. 70 is sysexits' EX_SOFTWARE, which no crash produces.
DEGRADED_EXIT = 70

STATUS_PASS_REASON = "status changed on the same line"
KEPT_AVAILABLE_OVER_UNSTATED = "Available, and every later read states no availability (e.g. EOI)"
KEPT_SAME_READ = "written by the survivor's own read of the document: another line of that read"


def _norm(v):
    s = str(v or "").strip().lower()
    return s or None


def _parsed_date(value):
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value or "").strip(), fmt)
        except (ValueError, TypeError):
            continue
    return None


def lot_key(row):
    """Same physical lot AND the same product on it."""
    key = (_norm(row["state"]), _norm(row["suburb"]), _norm(row["lot_address"]),
           row["bedrooms"], row["bathrooms"], row["car_spaces"],
           row["house_sqm"], row["land_sqm"])
    # A row with no locality or no address label cannot be matched to anything with
    # confidence, so it is never superseded — the spec alone is not an identity.
    if key[0] is None or key[1] is None or key[2] is None:
        return None
    return key


def _freshness(row):
    """Newest wins. date_checked first, then last_seen, then insertion order.

    id is the final tie-break rather than anything clever: when two captures carry the
    same date the later INSERT is the later harvest, and picking deterministically
    matters more than picking cleverly — a run that chose differently each time would
    move Colin's selection around underneath him.
    """
    return (_parsed_date(row["date_checked"]) or datetime.min,
            _parsed_date(row["last_seen"]) or datetime.min,
            row["id"])


def text_key(row):
    """The row's own source line — the strongest evidence two captures are one listing.

    lot_key needs an IDENTICAL spec, and the spec is exactly what is unstable: the
    extractor recorded land_sqm as NULL on one harvest and 455.0 on the next, and
    lot_number as NULL then '68', so seven captures of "68 Amory Ripley Tallow 170"
    formed seven groups of one and none was superseded. 1,038 surplus rows survived
    that way and the stock table showed a listing five times over.

    Two captures carrying byte-identical source text from the same channel cannot be two
    different products — the line contains the lot, the address, the spec and the price.
    Money spacing is normalised first, because a stocklist that reformats "$ 9 89,900"
    into "$989,900" is the same line, and that reformatting alone created 46 duplicates.

    The PRICE has to match too. This repo deliberately treats two rows identical in every
    other field but the price as two real packages (see variant_ordinal in
    database.building_content_hash), so those are left alone: 11 groups differ only on
    price and none of them is touched.
    """
    text = normalise_money_spacing(str(row.get("source_text") or ""))
    # Drop the unit suffix before comparing. A vendor re-exporting "269" as "269 m2" is
    # the same line, and that alone left 33 pairs live -- one of them the same Tarneit
    # lot twice, differing only by two characters.
    text = re.sub(r"(?<=\d)\s*(?:m2|m²|sqm|sq\.?m)\b", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip().lower()
    if not text:
        return None
    return (_norm(row.get("source_channel")), text,
            round(float(row.get("price") or 0), 2))


def cross_channel_text_key(row):
    """The same source line, arriving through two different channels.

    text_key deliberately includes source_channel, because source_channel is the first
    element of database._HASH_FIELDS and two channels are two identities by design. That
    is right for identity and wrong for counting stock. Hattan's stocklist reaches us
    twice — once as E-Agent's pooled PDF, once as the builder's own email — and both
    copies stayed live: 46 lots were counted twice under two spellings of one builder,
    and 16 more under the single correct spelling. $46.1m of $129.8m of live Hattan
    stock was the same houses, twice.

    The evidence is stronger here than in text_key, not weaker: the two rows carry
    BYTE-IDENTICAL source text at the SAME price. A line that names the lot, the estate,
    the spec and the price cannot describe two different houses because it was forwarded
    down a different pipe.

    Measured before shipping, over all 6,141 live rows: 122 groups, 122 surplus rows,
    122 of them cross-channel (121 E-Agent vs digital email, 1 Portal vs E-Agent). The
    same-channel pass already catches everything else, so this adds nothing outside the
    gap it exists to close.

    A CONTRADICTION ON STATE STOPS THE MERGE — see find_superseded, not this key. Keyed
    on text and price alone, 13 of 148 cross-channel groups held rows that CONTRADICT
    each other on state: "Jubilee" stored as both QLD and VIC, "Grandview" as both NSW
    and VIC. The survivor was then picked arbitrarily, and for Jubilee it kept the
    VIC-labelled copy — Colin's Queensland brief went from one qualified listing to none,
    because the only lot that matched had been retired in favour of a row claiming to be
    in another state.

    The guard is applied to the GROUP rather than folded into the key here, and the
    difference is worth 370 listings. Putting the state in the key also splits a capture
    that never stated one away from the capture that did, which is not a contradiction —
    it is one row knowing less. That version retired 44 cross-channel duplicates against
    417, losing ~370 correct merges to prevent 13 wrong ones.
    """
    k = text_key(row)
    return None if k is None else k[1:]          # drop the channel, keep text + price


def _detail(row):
    """How much this capture actually knows. The survivor rule for the cross-channel
    pass, and NOT plain freshness.

    Measured: under _freshness alone the sparse digital-email copy wins 68 of the 122
    groups and 60 of those are decided by nothing but the id tie-break, which would
    destroy the suburb, the spec and the benchmark on 56 lots that have them — the two
    copies of a Hattan lot are 'Weir Views, 4/2/2, benchmarked' against a row whose
    suburb is NULL. Freshness is still the last word, so a tie between two equally
    detailed captures resolves exactly as the other two passes resolve it.
    """
    filled = sum(1 for c in ("suburb", "state", "bedrooms", "bathrooms", "car_spaces",
                             "house_sqm", "land_sqm", "postcode", "estate_name",
                             "builder_name")
                 if row.get(c) not in (None, "", 0))
    benchmarked = 1 if row.get("benchmark_median") not in (None, "", 0) else 0
    return (filled, benchmarked) + _freshness(row)


def find_superseded(rows):
    """[(loser_row, winner_row)] for every surplus capture.

    Three independent groupings, because each catches what the others miss: lot_key
    matches the same lot across DIFFERENT source lines, text_key matches the same source
    line whose extracted spec came out differently, cross_channel_text_key matches the
    same source line that arrived down two pipes. A row caught by more than one is
    recorded once, and a row that has already lost is never reconsidered — so the
    cross-channel pass can only ADD retirements, never move an existing one.
    """
    # ONE COMPONENT, ONE SURVIVOR — and why the obvious version of this was wrong.
    #
    # The first version of the three-pass rule assigned a winner per pass and then walked
    # a loser->winner chain to find who finally survived. Three passes disagree far more
    # often than two, so winners became losers elsewhere and the chains closed into
    # cycles. When a cycle sent a winner's chain back to the loser it started from, the
    # pair was dropped and the row simply stayed live. Measured on the 11,257 stored rows:
    # 6,933 duplicates were correctly identified, 3,272 of them were then discarded by the
    # chain walk, and the run retired 3,661 where the two-pass rule had retired 5,116.
    # A pass added to REMOVE double counting would have published 1,455 more duplicates as
    # sellable stock — the precise harm it exists to prevent, and invisible in the output,
    # which reported a smaller number as though it were a cleaner database.
    #
    # Grouping is transitive — if two rows are duplicates under ANY key they belong to one
    # group — so the honest structure is connected components, not per-pass winners. Each
    # component elects exactly one survivor and everything else points straight at it.
    # A cycle cannot form, because no row is ever both a survivor and a loser.
    parent = {r["id"]: r["id"] for r in rows}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]        # path halving, so deep chains stay cheap
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for key_fn in (lot_key, text_key, cross_channel_text_key):
        groups = defaultdict(list)
        for r in rows:
            k = key_fn(r)
            if k is not None:
                groups[k].append(r)
        for members in groups.values():
            # Two captures that disagree about which STATE a house is in are not proven
            # to be one listing — one of them is simply wrong, and merging picks the
            # wrong one half the time. Keeping both duplicates is the cheaper error: a
            # duplicate is visible and countable, a lot filed under the wrong state is
            # neither, and it is the one that reaches a client. A BLANK state is not a
            # contradiction, only a capture that knows less, so it still merges.
            #
            # Applied to the cross-channel pass alone: lot_key already requires the state
            # to match, and text_key compares captures of one channel, where a state
            # flip between harvests is the state resolver improving rather than two
            # sources disagreeing.
            if key_fn is cross_channel_text_key:
                stated = {_norm(m.get("state")) for m in members if _norm(m.get("state"))}
                if len(stated) > 1:
                    continue
            for other in members[1:]:
                union(members[0]["id"], other["id"])

    by_id = {r["id"]: r for r in rows}
    components = defaultdict(list)
    for r in rows:
        components[find(r["id"])].append(r)

    out = []
    for members in components.values():
        if len(members) < 2:
            continue
        # The cross-channel case keeps the better-described capture rather than the newest
        # one (see _detail): the two copies of a Hattan lot are "Weir Views, 4/2/2,
        # benchmarked" against a row whose suburb is NULL, and freshness alone would keep
        # the empty one. Within a single channel, detail is stable and freshness is the
        # whole question, so the original rule is preserved there untouched.
        spans_channels = len({_norm(m.get("source_channel")) for m in members}) > 1
        rank = _detail if spans_channels else _freshness
        ordered = sorted(members, key=rank, reverse=True)
        winner = ordered[0]
        for loser in ordered[1:]:
            out.append((by_id[loser["id"]], winner))
    return out


# ---------------------------------------------------------------------------------------
# THE FOURTH PASS: the same line on the same document, read again with a new status word
# ---------------------------------------------------------------------------------------

def status_pass_enabled(environ=None):
    """SPB_STATUS_PASS: ON unless set to 0/off/false/no (case-insensitive)."""
    env = os.environ if environ is None else environ
    return str(env.get(STATUS_PASS_ENV, "") or "").strip().lower() not in _OFF_WORDS


# The search's own availability vocabulary. These are api/_candidates.NOT_AVAILABLE and
# IS_AVAILABLE, copied rather than imported: this is a required nightly step and
# _candidates drags in the whole search bundle. tests/test_supersede.py pins the copies
# to the originals, so the two cannot drift apart silently.
NOT_AVAILABLE = frozenset({
    "sold", "under offer", "reserved", "leased", "on hold", "withdrawn",
    "not available", "unavailable", "not avail", "no longer available",
    "off market", "settled", "deposit taken", "contract pending"})
IS_AVAILABLE = frozenset({
    "available", "for sale", "in stock", "released", "selling",
    "titled", "now selling", "open"})

# The words stripped from the line before two captures are compared: the search's
# vocabulary plus the spellings price lists use for a status ("HOLD", "EOI", "TBC",
# "Under Contract", "Sold Out").
#
# MINUS words that are as often a fact about the lot as its status. "Titled" is the land
# title ("Titled Available" is two facts); "Released", "Selling" and "Now Selling" name a
# stage. Stripping a fact would join two lines that differ in it, so when in doubt a word
# stays in the line: a missed join leaves today's duplicate, a wrong join hides a lot.
#
# This is exactly the list the Part C simulation measured (17 Sep copy: 121 retirements
# of rows live that day, before the two exceptions in find_status_superseded). The
# plan's shorter list (the search's words plus eoi/hold, minus titled/open/selling/
# released) joins 4 fewer lines, e.g. Creation Homes' "Under Contract 8 LAND ONLY ..."
# (id 9843).
STATUS_WORDS = frozenset(
    (NOT_AVAILABLE | IS_AVAILABLE | {
        "available", "not available", "unavailable", "on hold", "hold", "under offer",
        "sold", "reserved", "leased", "tbc", "eoi", "under contract", "deposit taken",
        "sold out"})
    - {"titled", "open", "selling", "released", "in stock", "for sale", "now selling"})


def _status_rx(words):
    # Longest first, so "on hold" is removed whole rather than leaving "on". A space in a
    # phrase matches any run of whitespace, including none: "ONHOLD" is the same word.
    parts = sorted((re.escape(w).replace(r"\ ", r"\s*") for w in words),
                   key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.I)


_STATUS_RX = _status_rx(STATUS_WORDS)
_URL_RX = re.compile(r"(?:https?://|www\.)\S+", re.I)
_UNIT_RX = re.compile(r"(?<=\d)\s*(?:m2|m²|sqm|sq\.?m)\b", re.I)


def _document(row):
    """The one price list this capture was read from."""
    return str(row.get("source_url") or row.get("stocklist_file") or "").strip()


def status_free_text(text):
    """The line as text_key compares it, with links and status words taken out.

    Links first, so a status word inside a URL ("/sold-out.pdf") is never what changes
    the key, and so a link that moved (a re-shared Drive file) does not split one line.
    """
    t = str(text or "")
    t = _URL_RX.sub(" ", t)
    t = normalise_money_spacing(t)
    t = _UNIT_RX.sub("", t)
    t = _STATUS_RX.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def status_text_key(row):
    """(channel, document, status-free line, price), or None.

    PER DOCUMENT, not per channel. Keyed on the channel alone the simulation retired 167
    rows instead of 121, and the extra 46 join lines from different emails that nobody
    has checked are the same listing. One document re-read on two days is the claim this
    pass can actually prove.

    No document or no text left once the status is removed: None, never grouped. A
    line that is ONLY a status word and a price is not an identity.
    """
    doc = _document(row)
    text = status_free_text(row.get("source_text"))
    if not doc or not text:
        return None
    try:
        price = round(float(row.get("price") or 0), 2)
    except (TypeError, ValueError):
        return None
    return (_norm(row.get("source_channel")), doc, text, price)


def _stated(row):
    """'available', 'not available', or None when the capture states neither."""
    s = str(row.get("availability_status") or "").strip().lower()
    if s in IS_AVAILABLE:
        return "available"
    if s in NOT_AVAILABLE:
        return "not available"
    return None


def _read_rank(row):
    """The most recent READ wins: date_checked, then checked_at (the time of day, which
    date_checked lacks), then last_seen.

    A TIE KEEPS THE LOWER id, the same way api/_candidates._collapse_same_listing keeps
    the first-stored capture. That is deliberately not _freshness's rule (higher id), and
    it does not need to be: this pass never touches a row the other three decided.

    A tie on the SAME READ is a different thing, see _same_read: the lower id is still
    the survivor older captures point at, but the tied capture is not retired.
    """
    try:
        rid = int(row["id"])
    except (TypeError, ValueError):
        rid = 0
    return (_parsed_date(row.get("date_checked")) or datetime.min,
            _read_time(row) or datetime.min,
            _parsed_date(row.get("last_seen")) or datetime.min,
            -rid)


# How far apart two lines of ONE read of one document can be stamped. checked_at is
# written per ROW, to the second (database.record_building: datetime.now()), not once per
# read. Measured on the 17 Sep copy: one read of a document spans at most 16 s (46 of 336
# reads wrote their lines across 2-6 different seconds), and two reads of one document on
# the same day were never less than 41 minutes (2,456 s) apart. Ten minutes sits well
# clear of both.
SAME_READ_WINDOW_S = 600


def _read_time(row):
    """checked_at as a naive datetime, or None. The first 19 characters only, so an
    offset or fraction a future writer adds can neither fail to parse nor raise when
    compared with a naive stamp."""
    s = str(row.get("checked_at") or "").strip()[:19].replace(" ", "T")
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _same_read(a, b):
    """Two captures written by ONE read of the document: both checked_at stamps present
    and no more than SAME_READ_WINDOW_S apart.

    NOT an identical second. The stamp is per row, so one read is a run of seconds, and
    the Merbein pair below is re-stamped every night it is re-read: requiring equality
    would keep both lots the night they land in one second and hide one of them the night
    they land a second apart -- whichever was written first, the Available one included.

    Then they are two lines, not one line whose status changed, and neither may retire
    the other. Both such pairs on the 17 Sep copy are provably two lots:
      - Creation Homes "8 LAND ONLY CNR 290.5 ... $858,500": 16177 Available is sheet
        row 92 and 16180 SOLD is row 95, and the same pair was read on 22/08 (9840/11720)
        and earlier (2594/2597). The lot number is in a column the line does not carry.
      - Merbein 18203 "Hold" and 18204 "Available": the same PDF page lists identical
        4/2/2 463 m2 lots at $691,000.
    Retiring by rank would hide one of each pair; retiring the Available one would hide
    a real lot, which is the harm the Volos exception below also exists to stop.

    A capture with no checked_at (stored before the column existed) is never "the same
    read": a tie between two of those falls to _read_rank's lower id.
    """
    ta, tb = _read_time(a), _read_time(b)
    if ta is None or tb is None:
        return False
    return abs((ta - tb).total_seconds()) <= SAME_READ_WINDOW_S


def _carries_rule_marker(row):
    """superseded_by holding a judgement rather than a content hash.

    repair_rows writes these ("withdrawn: the source stopped listing it", "spreadsheet
    filter row, not a listing", ...) and re-writes them every night after this step. A
    filter row or an email sentence must never be elected the survivor of a real lot.
    """
    mark = str(row.get("superseded_by") or "")
    return bool(mark) and not mark.startswith("v2:")


def find_status_superseded(rows, retired_ids=(), kept_live=None):
    """[(loser_row, winner_row)] for captures of one line that differ only by status.

    ADDITIVE. It runs over the rows find_superseded left live (pass its losers' ids as
    retired_ids) and only ever adds retirements. It is never folded into that union: the
    first simulation that did so re-elected the survivor of 4 existing groups (8948/8950,
    10032/10049, 14881/12408, 14884/12411), moving marks that were already right.

    In each group the most recently read capture survives (_read_rank) and the rest are
    retired with the reason STATUS_PASS_REASON.

    EXCEPT AN AVAILABLE CAPTURE IS NEVER RETIRED IN FAVOUR OF A READ THAT STATES NOTHING.
    AVIA Homes' Volos sheet changed 21 lots from "Available" to "EOI" on 16 Sep. "EOI" is
    not in the search's vocabulary, so the new capture is stored with no status, and
    retiring the Available capture in its favour would drop a real lot out of every
    result and every "needs confirming" list. So when every capture read after an
    Available one states no availability, the Available capture stays live. If any later
    capture DOES state a status (On Hold, or a newer Available), that statement is the
    news and the older Available capture is retired as usual.

    AND A CAPTURE FROM THE SURVIVOR'S OWN READ IS NEVER RETIRED (_same_read): two lines
    on one page at one moment are two lots.

    AN EXISTING SURVIVOR CAN BE RETIRED HERE. Its own losers keep pointing at it (their
    marks do not move), so they reach the live row in two steps instead of one. Every
    reader of superseded_by treats it as a flag, never a lookup.

    Pass a list as kept_live to be told which captures the exceptions kept, as
    [(kept_row, latest_read_row, why)]; it is for the report only and changes nothing.
    """
    retired = set(retired_ids)
    groups = defaultdict(list)
    for r in rows:
        if r["id"] in retired or _carries_rule_marker(r):
            continue
        k = status_text_key(r)
        if k is not None:
            groups[k].append(r)

    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=_read_rank, reverse=True)
        winner = ordered[0]
        for n in range(1, len(ordered)):
            loser = ordered[n]
            why = None
            if _same_read(loser, winner):
                why = KEPT_SAME_READ
            elif (_stated(loser) == "available"
                    and all(_stated(m) is None for m in ordered[:n])):
                why = KEPT_AVAILABLE_OVER_UNSTATED
            if why:
                if kept_live is not None:
                    kept_live.append((loser, winner, why))
                continue
            out.append((loser, winner))
    return out


def document_label(row):
    """A reviewer-readable name for the document, WITHOUT its link.

    The nightly log and the review lists get passed around; the links to builders' price
    lists stay out of them. They name the host (or the email subject) and a short
    fingerprint instead: two rows with the same [doc xxxxxxxx] came from one document.
    """
    doc = _document(row)
    if not doc:
        return "(no document)"
    tag = hashlib.sha1(doc.encode("utf-8")).hexdigest()[:8]
    if doc.lower().startswith("email:"):
        # A subject line can carry a link too ("Fwd: stock list https://...").
        where = "email '" + _URL_RX.sub("<link>", doc[6:]).strip()[:70] + "'"
    elif "://" in doc:
        where = urlsplit(doc).hostname or "link"
    else:
        where = "file " + os.path.basename(_URL_RX.sub("<link>", doc).replace("\\", "/"))[:60]
    # PLAIN ASCII. The report is printed inside the status pass's try block, so a subject
    # carrying a character cp1252 lacks (an emoji; one subject on the 17 Sep copy), printed
    # to a cp1252 pipe (a Windows run with its output redirected), would raise
    # UnicodeEncodeError there and throw away every status retirement of the night as a
    # "failed pass". The [doc] tag still identifies the document.
    return f"{where} [doc {tag}]".encode("ascii", "replace").decode("ascii")


def _status_word(row):
    return str(row.get("availability_status") or "").strip() or "(blank)"


def _state_word(row):
    return str(row.get("state") or "").strip().upper() or "(blank)"


def _report_status_pass(status_pairs, kept_live):
    """Printed separately from the three passes, because these retirements change what a
    lot SAYS, not only how many copies of it there are."""
    print()
    print(f"    {len(status_pairs)} more superseded because the same line was read again with a")
    print(f"    different status word ({STATUS_PASS_REASON}):")
    if status_pairs:
        print("      by state:")
        for st, n in sorted(Counter(_state_word(l) for l, _ in status_pairs).items(),
                            key=lambda kv: (-kv[1], kv[0])):
            print(f"        {n:>5}  {st}")
        print("      by status, retired -> kept:")
        trans = Counter(f"{_status_word(l)} -> {_status_word(w)}" for l, w in status_pairs)
        for t, n in sorted(trans.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"        {n:>5}  {t}")
        qld = [(l, w) for l, w in status_pairs if _state_word(l) == "QLD"]
        print(f"      every QLD one ({len(qld)}):")
        for l, w in sorted(qld, key=lambda p: p[0]["id"]):
            print(f"        {l['id']:>6} -> {w['id']:<6} {_status_word(l)} {l.get('date_checked') or '?'}"
                  f" -> {_status_word(w)} {w.get('date_checked') or '?'}"
                  f"  ${float(l.get('price') or 0):,.0f}  {document_label(l)}")
    if kept_live:
        print(f"    {len(kept_live)} capture(s) in those groups deliberately left live:")
        for why, n in sorted(Counter(k[2] for k in kept_live).items(),
                             key=lambda kv: (-kv[1], kv[0])):
            print(f"        {n:>5}  {why}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = ap.parse_args(argv)

    print("=" * 66)
    print("  Supersede older captures of the same lot")
    print("=" * 66)

    if not args.dry_run:
        dest = f"{config.DATABASE_PATH}.bak-supersede-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy2(str(config.DATABASE_PATH), dest)
        print(f"[+] backup written: {dest}")
    else:
        print("[i] report only — nothing will be written.")

    # Instantiating ResearchDatabase is what runs the additive-column migration, so
    # the superseded_* columns exist before the first raw query touches them.
    from database import ResearchDatabase
    ResearchDatabase()

    conn = sqlite3.connect(str(config.DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM buildings")]
    pairs = find_superseded(rows)

    total = len(rows)
    print(f"    {total} row(s) stored")
    print(f"    {len(pairs)} superseded by a fresher capture of the same lot")

    # THE FOURTH PASS, isolated. Everything above is today's rule and is written whatever
    # happens here: a bug in the new pass must cost tonight's status retirements, not
    # tonight's publish (this is a required step, and a crash aborts the whole chain).
    status_pairs, degraded = [], False
    if not status_pass_enabled():
        print(f"    status pass OFF ({STATUS_PASS_ENV}="
              f"{os.environ.get(STATUS_PASS_ENV, '')!r}) - the three-pass rule only")
    else:
        try:
            kept_live = []
            status_pairs = find_status_superseded(
                rows, {l["id"] for l, _ in pairs}, kept_live=kept_live)
            _report_status_pass(status_pairs, kept_live)
        except Exception:                                              # noqa: BLE001
            degraded = True
            status_pairs = []
            print()
            print("ERROR: the status pass failed; falling back to the three existing passes.")
            print("       Their marks are still written. The run is DEGRADED "
                  f"(exit {DEGRADED_EXIT}).")
            traceback.print_exc(file=sys.stdout)

    live = total - len(pairs) - len(status_pairs)
    print(f"    {live} distinct lot(s) remain live")

    if pairs:
        moved = [(l, w) for l, w in pairs
                 if round(float(l["price"] or 0), 2) != round(float(w["price"] or 0), 2)]
        cheaper_stale = sum(1 for l, w in moved
                            if float(l["price"] or 0) < float(w["price"] or 0))
        print()
        print(f"    of those, {len(moved)} had a price different from the fresher row")
        print(f"    and {cheaper_stale} were CHEAPER — the stale prices that would have been")
        print(f"    surfaced as 'deals' if superseded rows were left in the running")

        # The cross-channel retirements are named, not folded into the total. They are the
        # only ones where the row disappearing is not an older capture of the same feed —
        # it is the same listing arriving down a different pipe, so the per-channel daily
        # figures move ('digital email' drops by 121) and a reader who is not told will
        # reasonably think stock went missing overnight.
        cross = [(l, w) for l, w in pairs
                 if _norm(l["source_channel"]) != _norm(w["source_channel"])]
        if cross:
            seen = {}
            for l, w in cross:
                pair = " vs ".join(sorted({str(l["source_channel"]), str(w["source_channel"])}))
                seen[pair] = seen.get(pair, 0) + 1
            print()
            print(f"    {len(cross)} of them are the SAME source line arriving through two")
            print(f"    channels — one listing counted twice, not an older capture:")
            for pair, n in sorted(seen.items(), key=lambda kv: -kv[1]):
                print(f"        {n:>5}  {pair}")
            by_builder = {}
            for l, _w in cross:
                by_builder[l["builder_name"] or "(blank)"] = \
                    by_builder.get(l["builder_name"] or "(blank)", 0) + 1
            for name, n in sorted(by_builder.items(), key=lambda kv: -kv[1])[:6]:
                print(f"        {n:>5}  retired from {name}")

    all_pairs = list(pairs) + list(status_pairs)
    if not args.dry_run and all_pairs:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Clear first: a row that is no longer a duplicate (because the row that beat
        # it was itself removed, or the spec changed) must not stay flagged.
        conn.execute("UPDATE buildings SET superseded_by=NULL, superseded_at=NULL")
        conn.executemany(
            "UPDATE buildings SET superseded_by=?, superseded_at=? WHERE id=?",
            [(w["content_hash"], stamp, l["id"]) for l, w in all_pairs])
        conn.commit()
        print(f"\n    marked. Superseded rows keep their price and history; they are")
        print(f"    excluded from the benchmark and hidden from the stock table by default.")

    print("=" * 66)
    return DEGRADED_EXIT if degraded else 0


if __name__ == "__main__":
    sys.exit(main())
