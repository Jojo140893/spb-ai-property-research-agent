"""parity_check.py -- does the app show what the sources list? Nightly source-to-search parity.

7 Oct 2026. Coleen phoned the owner, angry: she runs a search in E-Agent or a builder's
portal, runs the same search in the Research tab, and the app shows far fewer -- E-Agent
always shows more. The audit of the 6 Oct database measured why. A state-wide search scored
only the 600 cheapest lots in the state (api/_candidates.py MAX_CANDIDATES) before anything
else happened, so VIC showed 322 of the 2,837 lots read in the last three nights and nothing
above $645,500 at all. Nothing caught it: search_regressions' obvious-rows check would have
failed a cut row, but it runs on two QLD briefs whose budgets the cap never reaches. This
step asks Coleen's question every night, for every state, of the bundle that is about to
ship, and goes red with the lots named when the answer is "fewer".

    python parity_check.py --snapshot vercel_site/stock.json [--db PATH] [--today dd/mm/yyyy]
                           [--nightly]   # run_daily: keep history, publish, mark DEGRADED

THE OBVIOUS SET -- what the sources list tonight -- picked from the DATABASE, never from what
the search says: every live row, in Australia, stamped in the last RECENT_NIGHTS nights,
priced as a whole home and land (search_regressions.priced_as_a_home, the obvious-rows
check's own price rule), and stated Available / For Sale -- or blank on a list that never
states a lot Available (a list with no status column, or one that only marks the lots that
have gone: being on the list is the statement). Counted per state (VIC, NSW, QLD, SA, WA,
and since 8 Oct 2026 ACT, TAS and NT, which the form now offers), for E-Agent and for every
channel, in lots and in buildings. A building is the estate, else the project (a Proxima
project, a project-scope stocklist), else the stocklist. Rows with no state are counted on a
row of their own.

THE BARS. Any miss exits 1.
  SHOWN       the shipped bundle's run_research, once per state (state-wide, no budget,
              explain), every page fetched when the response is paged (see "every page"
              below: both the paged and the unpaged shapes are read). At least 97% of
              obvious lots are on the shortlist or the needs-confirming list, and at least
              99% of obvious buildings have a lot there -- for E-Agent and for all channels.
              Every obvious lot NOT shown must carry an allowed reason: a sold-stock guard,
              its own lot page gone, a price that is not a home-and-land price, or a twin
              that IS shown and is the same lot (another document's copy, or the same line
              read again -- never another line of the same list). Anything else -- cut by
              the candidate cap, past the needs-confirming list, no suburb, no state, merged
              into a twin nobody sees, two lots of one list merged -- fails, named by id.
              A page over Vercel's 4.5 MB response limit fails too: on the live site that
              search would not answer at all.
  NOT OVER-SERVED  shown lots whose own document was re-read in the last 3 nights without
              them: at most 2% of what a state shows.
  DRIFT       tonight's obvious lots and buildings per state against the median of the
              previous 7 nights in data/parity_history.json; a drop of more than 15% fails,
              naming the channels and the documents that fell. With no earlier night it
              passes, and says so.
  READ        from data/harvest_manifest.json when the harvest wrote one: every link has an
              outcome; each document stored at least 98% of the lots it printed; no document
              that yielded rows in the last week yields 0 tonight; no Proxima project ends at
              exactly 80, 83 or 160 lots (its render cap, unless its own counter says that
              many) or below its own available counter; every portal read the cards its page
              shows. Without a manifest the bar is skipped, and says so.
              8 Oct 2026, from the first live night's 13 false alarms: a Proxima lot printed
              under a second project and stored once under the first (cross_listed_into a
              project read tonight) counts as stored -- since the second repair round only
              on its row's evidence: the manifest names the lot (cross_listed_lots) and its
              row is live under that project, read tonight, at the price and status printed
              (and shipped, if for sale); a named lot the database contradicts fails
              whatever the share; a project read WHOLE (`whole`) below an
              old counter -- the database's, from the last night it was on the index -- by
              no more than harvest_manifest.old_counter_gap_ok allows (3 lots or 10%, and at
              least one lot read) is noted, not failed (tonight's own header, or a bigger
              gap, still fails); and an overseas project
              (Proxima lists SMDC's Philippine towers) is named, not judged.

HOW IT RUNS. As search_regressions --snapshot does: the database is copied through a
read-only connection; a child process imports vercel_site/api -- the bundle that ships, made
read-only the way Vercel runs it, no network -- and calls run_research exactly as the
endpoint does. The bundle is fingerprinted before and after. Five to eight searches and
the explanation of every hidden lot; measured 7 Oct 2026 on the 6 Oct database: 39 s on the
origin/main bundle, 40 s on the search lane's paged bundle, 55 s with the cap lifted and no
paging (the slowest shape: one 14.9 MB VIC answer).

OUTPUT. A compact table on stdout; data/parity_report.json, which the Settings tab reads
(with --nightly also copied beside the snapshot, so tonight's publish carries tonight's
table); with --nightly, tonight's counts appended to data/parity_history.json and a one-line
summary added to the DEGRADED marker when a bar fails.

EXIT CODE. 0 when every bar passes, 1 otherwise -- including a setup error. Never 2
(run_daily: a dead harvest channel) or 70 (a step that fell back). As an OPTIONAL run_daily
step a failure marks the night DEGRADED after the stock has published: fresh stock is never
withheld over parity, and CI still goes red.

NEVER PRINTS A LINK: database ids, hash prefixes, builder, estate and file names only.
"""
import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# Harness code from the app, imported before the child narrows sys.path to the bundle.
# search_regressions imports the standard library only, so nothing of the app's pipeline
# can shadow a module of the bundle being checked.
import search_regressions as SR
# The manifest's schema module (standard library only, like SR): the READ bar's bound on an
# old Proxima counter is the harvest's own (old_counter_gap_ok), so the two never differ.
import harvest_manifest as HM

APP = Path(__file__).resolve().parent
DEFAULT_SNAPSHOT = APP / "vercel_site" / "stock.json"
HISTORY = APP / "data" / "parity_history.json"
REPORT = APP / "data" / "parity_report.json"
MANIFEST = APP / "data" / "harvest_manifest.json"
PUBLISHED_NAME = "parity_report.json"
DEGRADED_MARKER = APP / "_run_degraded"

# The states the Research tab's form offers (index.html stateSelect). ACT, TAS and NT since
# 8 Oct 2026: the first live night counted 207 obvious ACT lots (Proxima: The Grande,
# Canberra; Ahlei and Cento, Braddon) that no state search could ask for by name.
SEARCHED_STATES = ("VIC", "NSW", "QLD", "SA", "WA", "ACT", "TAS", "NT")
# The states a working harvest reads stock in every night (6-8 Oct 2026: VIC 2,786 obvious
# lots, NSW 2,852, QLD 813, SA 117, WA 22), where a state with no obvious lot at all means a
# dead source. ACT is Proxima's alone and TAS and NT hold none (8 Oct: TAS no row, NT one
# misfiled Victorian row), so an empty one there is said, not failed -- and a state that had
# obvious lots and loses them still fails the DRIFT bar.
CORE_STATES = ("VIC", "NSW", "QLD", "SA", "WA")
AU_STATES = frozenset(("NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"))
BLANK = "blank"
OTHER = "other"
# Tonight and the two nights before: a lot E-Agent lists is re-read every night, so three
# nights covers a night the harvest ran late or a channel missed, and no more.
RECENT_NIGHTS = 3
SHOWN_LOTS_MIN = 0.97
SHOWN_BUILDINGS_MIN = 0.99
OVER_SERVED_MAX = 0.02
# REPAIR ROUND 1 (7 Oct 2026): bars that could not fail when they should have.
# Lots hidden for an allowed reason, of a state's obvious lots: an excuse leaves the
# denominator, so it needs a ceiling of its own (6 Oct: VIC 6, NSW 11, QLD 2 excused).
EXCUSED_MAX = 0.03
# REPAIR ROUND 2: 10 let a small state hide most of itself under an excuse (WA 10 of 22 is
# 45%, SA 10 of 113). Two is a lot or two; the evidence check below does the rest.
EXCUSED_FLOOR = 2
# An excuse is allowed only on its evidence (search_regressions.guard_evidence), and an
# obvious building of this many lots or more hidden ENTIRELY for its own list's sake (the
# document and project reasons) fails outright: on 6 Oct no such building existed, and a
# search made to call Fawkner Park West's 35 lots "builder_documents_reread" passed.
WHOLE_BUILDING_MIN = 5
WHOLE_BUILDING_REASONS = ("document_reread_without_row", "builder_documents_reread",
                          "newer_upload_in_folder", "project_reread_without_row",
                          "project_reread_uncounted")
# KNOWN DATA ISSUES, written down with a reason and a date they stop being excused (repair
# round 2, callers verifier): bars that fail every night for a row the client or owner
# must fix trained everyone to ignore the alert. data/parity_acknowledged.json
ACKNOWLEDGED = APP / "data" / "parity_acknowledged.json"
# Of the obvious lots a state shows or should, the share on the SHORTLIST (6 Oct, branch:
# every state over 99%).
SHORTLISTED_MIN = 0.95
# A working harvest reads thousands of E-Agent lots a night (6 Oct: 4,026 obvious).
OBVIOUS_E_AGENT_MIN = 50
# Shown cards that are a second copy of a lot already shown.
DUPLICATES_MAX = 0.005
DUPLICATES_FLOOR = 10
# Shown cards on a list not read for RECENT_NIGHTS or more: nobody can say whether the
# list still carries them. Measured after the fresh-row guards on 6 Oct (see the report).
UNREAD_MAX = float(os.environ.get("SPB_PARITY_UNREAD_MAX", "0.15"))
DRIFT_NIGHTS = 7
DRIFT_DROP_MAX = 0.15
# Nights kept in the history: the drift median needs 7, "yielded rows last week" needs 7.
HISTORY_KEEP = 14
READ_STORED_MIN = 0.98
# Where Proxima's accordion stops rendering (memory: proxima-render-cap): a project that
# ends at exactly one of these was cut, not read to its end.
PROXIMA_RENDER_CAPS = (80, 83, 160)
NAME_FIRST = 20
# Cards asked for per page when the server pages. The dashboard's pages are 100 cards and
# every page is a whole search again (VIC ~3-6 s), so the check asks for as many as the
# server allows: the search lane's server clamps a limit to 500 (SPB_PAGE_LIMIT_MAX), and a
# server that clamps is simply paged further.
PAGE_LIMIT = int(os.environ.get("SPB_PARITY_PAGE_LIMIT", "1000"))
MAX_PAGES = 200
# Vercel's documented ceiling on a function's response body. A page over it is not shown
# to anyone: the live endpoint fails instead of answering.
RESPONSE_LIMIT_BYTES = int(os.environ.get("SPB_PARITY_RESPONSE_LIMIT", "4500000"))
CHANNEL_GROUPS = ("e-agent", "all")

# What the dashboard form sends for a state-wide search with the Max budget left blank
# (data/colin_searches.json's form, budget keys dropped as the form drops a blank field).
FORM = {"client_name": "Parity check", "budget_min": 0, "buyer_type": "",
        "primary_suburbs": [], "bedrooms_min": 0, "bathrooms_min": 0, "car_spaces_min": 0,
        "storeys_max": 3, "land_size_min_sqm": 0, "house_size_min_sqm": 0,
        "search_radius_km": 0, "product_type": [], "requires_incentive": False}

# ALLOWED REASONS for an obvious lot the search does not show.
# Sold-stock evidence the database holds (confirm_guard, admission and promotion refusals).
SOLD_STOCK_GUARDS = ("newer_capture_disagrees", "newer_capture_exists",
                     "newer_capture_repriced", "document_reread_without_row",
                     "builder_documents_reread", "newer_list_from_sender",
                     "text_marks_unavailable", "gone_at_any_date",
                     # A re-upload replaced the row's file in its shared folder (the search's
                     # fresh_row_guard, repair round 1, 7 Oct 2026).
                     "newer_upload_in_folder",
                     # Its Proxima project was read again, whole, without it (round 2).
                     "project_reread_without_row")
# The lot's own page answered 404/410 or names it gone (link_check.py).
DEAD_LOT_PAGE = ("lot_page_gone",)
# The stored price is not a home and land at that price. The obvious set already leaves
# these out by the row's own columns and text; the bundle's reading is allowed to agree.
# price_not_a_home and fragment (7 Oct 2026): a fee, a commission or a deposit under
# $100,000, and a line that is only a price -- _candidates.not_a_home.
NOT_A_HOME_PRICE = ("price_not_in_text", "larger_total_in_text", "build_only", "land_only",
                    "commercial", "price_not_a_home", "fragment")
SHOWN_TWIN = "collapsed_into_shown_twin"
ALLOWED = frozenset(SOLD_STOCK_GUARDS + DEAD_LOT_PAGE + NOT_A_HOME_PRICE + (SHOWN_TWIN,))
# Outcomes the pipeline's own dedupe is replayed for, to name the twin it actually kept.
REPLAYED = ("scored_not_shown", "merged_with_another_lot")

REASON_TEXT = {
    "truncated": "cut by the candidate cap before scoring (SPB_MAX_CANDIDATES)",
    "needs_confirming_unlisted": "needs confirming, but past the list the response carries",
    "no_suburb": "no suburb the search can place",
    "suburb_not_a_locality": "a suburb that is not a locality",
    "state_unknown": "no state, so no state search reaches it",
    "merged_into_unshown_twin": "merged into a twin that is not shown either",
    "merged_with_another_lot": "merged with a DIFFERENT lot that is shown "
                               "(another line of the same list, or another lot number)",
    "not_in_snapshot": "in the database but not in the published snapshot",
    "not_explained": "the search's own explanation does not name it",
    "rejected_by_the_scorer": "rejected by the scorer",
    "scored_not_shown": "scored, and still not on any list",
    "incomplete_facts": "a fact the brief needs is missing",
    "search_failed": "the search itself failed",
    SHOWN_TWIN: "a copy of a lot that is shown",
    "unsupported": "hidden for a sold-stock reason the database does not back",
}


# ------------------------------------------------------------------------- small helpers

def _plain(text):
    """Text with every link taken out (search_regressions' rule)."""
    return SR._plain(text)


def _norm(value):
    return SR._norm(value)


def _channel(rec):
    return str((rec or {}).get("source_channel") or "").strip()


def _is_e_agent(rec):
    return _channel(rec).lower().startswith("e-agent")


def _short_hash(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


# A file name that is a random key, not a title (trace_listing's rule, stated here because
# importing trace_listing would import the app's pipeline over the bundle's): E-Agent's files
# are "<6 hex>_<32 hex>.xlsx", and that name is the unguessable half of the link.
_HEX_RUN = re.compile(r"[0-9a-f]{16,}", re.I)
_ALNUM_RUN = re.compile(r"[A-Za-z0-9]{24,}")
_DOC_EXTENSIONS = (".pdf", ".xlsx", ".xls", ".xlsm", ".csv", ".docx", ".doc")


def _key_like(stem):
    return bool(_HEX_RUN.search(stem)) or any(
        re.search(r"\d", run) and re.search(r"[A-Za-z]", run)
        for run in _ALNUM_RUN.findall(stem))


def _file_name(link):
    """The file a link names ('Price List Oct.pdf'), never the link and never a file name
    that is itself a key: '' for a folder share, a sheet id, a page or a keyed name."""
    ref = str(link or "").strip()
    try:
        path = urllib.parse.urlsplit(ref).path if re.match(r"^[a-z][a-z0-9+.-]*:/", ref,
                                                           re.I) else ref
    except ValueError:
        return ""
    name = urllib.parse.unquote(re.split(r"[\\/]", path)[-1]).strip()
    if not name.lower().endswith(_DOC_EXTENSIONS) or len(name) > 120:
        return ""
    return "" if _key_like(name.rsplit(".", 1)[0]) else name


def _pct(shown, total):
    return 1.0 if not total else shown / total


def _fmt_pct(shown, total):
    return "-" if not total else "%.1f%%" % (100.0 * shown / total)


def _n(value):
    return format(int(value or 0), ",")


# ------------------------------------------------------------- the obvious set (child)

def doc_key(rec, snap):
    """The document a row was read from: a Proxima project, else the snapshot's source_doc
    (a short hash of the source URL, what the search groups by), else a hash of the
    stocklist link. A row naming none gets a builder-wide group, which no read can be judged
    against (over-served skips it).

    The project first: every Proxima row carries the portal's index page as its source URL,
    so on 6 Oct all 5,231 live Proxima rows shared ONE source_doc -- 133 projects read as one
    document, and a lot read on 27/09 in a project not re-read since looked "re-read
    without" because some other project was read on 06/10."""
    channel = _channel(rec).lower()
    pid = str(rec.get("source_project_id") or "").strip()
    if channel.startswith("proxima") and pid:
        return "proxima:" + pid
    doc = str((snap or {}).get("source_doc") or "").strip()
    if doc:
        return "doc:" + doc
    src = str(rec.get("stocklist_file") or rec.get("source_url") or "").strip()
    if src:
        return "src:" + _short_hash(src)
    return "group:%s|%s" % (channel, _norm(rec.get("builder_name")))


def doc_label(rec):
    """A document's name for a person: the channel, the builder, the file -- never a link."""
    channel = _channel(rec) or "?"
    pid = str(rec.get("source_project_id") or "").strip()
    who = str(rec.get("builder_name") or "").strip() or str(rec.get("estate_name") or "").strip()
    if channel.lower().startswith("proxima") and pid:
        name = str(rec.get("estate_name") or "").strip() or who or "?"
        return _plain("Proxima project %s (%s)" % (pid, name))[:90]
    tail = _file_name(rec.get("stocklist_file")) or _file_name(rec.get("source_url"))
    return _plain(" - ".join(p for p in (channel, who, tail) if p))[:90]


def building_of(rec, group, doc, label):
    """(key, label): the estate, else the project, else the stocklist."""
    estate = _norm(rec.get("estate_name"))
    channel = _channel(rec).lower()
    pid = str(rec.get("source_project_id") or "").strip()
    builder = str(rec.get("builder_name") or "").strip()
    if estate:
        key, name = "estate:" + estate, str(rec.get("estate_name")).strip()
    elif channel.startswith("proxima") and pid:
        key, name = "project:proxima:" + pid, builder or "Proxima project %s" % pid
    elif str(rec.get("attribution_scope") or "").strip().lower() == "project" and builder:
        key, name = "project:" + _norm(builder), builder
    else:
        key, name = "stocklist:" + doc, label
    return "%s|%s" % (group, key), _plain(name)[:80]


def obvious_lots(catalogue, today, build_cells, overseas, left_out=None, blank_stating=None):
    """[lot] for every obvious lot, picked from the database (see the module docstring).
    Each lot is a dict: hash, id, group (a searched state, BLANK or OTHER), channel,
    e_agent, building, building_label, doc, doc_label, price.

    left_out(rec, doc) -> True drops a row its own document's latest full read did not
    carry (see read_left_it_out): read two nights ago, then the list was read again in full
    last night without it -- not something the sources list tonight.

    blank_stating, when given a dict, is filled {group: Counter(doc label)} with the rows
    left out ONLY for being blank on a list that states "Available" beside them (repair
    round 2, 7 Oct 2026, parity verifier): E-Agent's PDF still shows those lots -- AVIA's 35
    'EOI' lines, a status column the parser missed -- and a 100% read against a set that
    leaves them out says nothing about them, so the report names how many and where."""
    nights = {today - timedelta(days=i) for i in range(RECENT_NIGHTS)}
    live = [r for r in catalogue.db.values() if not r.get("superseded_by")]
    docs = {}
    # A LIST THAT SAYS "AVAILABLE" BESIDE ITS OTHER LOTS: a blank there is a gap, not a
    # statement, and the search rightly refuses it. Any other list's blank is obvious.
    states_available = set()
    for rec in live:
        h = SR._hash(rec)
        docs[h] = doc_key(rec, catalogue.row(h))
        if str(rec.get("availability_status") or "").strip().lower() in SR.IS_AVAILABLE:
            states_available.add(docs[h])
    out = []
    for rec in live:
        if SR._day(rec.get("date_checked")) not in nights:
            continue
        status = str(rec.get("availability_status") or "").strip().lower()
        if status and status not in SR.IS_AVAILABLE:
            continue
        h = SR._hash(rec)
        doc = docs[h]
        blank_beside_stated = not status and doc in states_available
        if blank_beside_stated and blank_stating is None:
            continue
        if left_out is not None and left_out(rec, doc):
            continue
        if not SR.priced_as_a_home(rec, build_cells(dict(rec))):
            continue
        state = str(rec.get("state") or "").strip().upper()
        if state and state not in AU_STATES:
            continue
        if overseas(rec, catalogue.row(h)):
            continue
        group = state if state in SEARCHED_STATES else (BLANK if not state else OTHER)
        label = doc_label(rec)
        if blank_beside_stated:
            blank_stating.setdefault(group, Counter())[label] += 1
            continue
        building, building_label = building_of(rec, group, doc, label)
        out.append({"hash": h, "id": rec.get("id"), "group": group, "state": state,
                    "channel": _channel(rec) or "?", "e_agent": _is_e_agent(rec),
                    "building": building, "building_label": building_label,
                    "doc": doc, "doc_label": label, "price": SR._num(rec.get("price"))})
    return out


# ------------------------------------------------------------------ every page (child)
#
# THE FIRST CALL IS THE ONE CLARE'S HOOK MAKES: the brief and nothing else (plus explain),
# so what an unpaged caller is handed is checked as it stands. A response that declares
# paging is then walked to its end. A response that declares none but hands over fewer
# cards than it counts is asked ONCE for the rest with offset/limit: a server that pages
# answers with its paging and is walked; one that does not answers the same cards, and
# the list is reported short. Measured 7 Oct 2026 on the search lane's paged server and
# the 6 Oct database: VIC unpaged with explain, every one of 3,089 cards in 3.18 MB, 6.2 s;
# the needs-confirming rest (102 past the first 50) one more call, 5.4 s -- where paging
# from the start at 500 cards a page costs seven searches.
#
# The shapes read (detected by their fields, never assumed):
#   per-list page objects -- the search lane's contract: "page" (the shortlist) and
#       "needs_confirming_page", each {offset, limit, returned, total, next_offset},
#       asked for with offset/limit and needs_confirming_offset/needs_confirming_limit;
#   one flat offset over both lists -- offset/limit with next_offset and/or has_more;
#   page numbers -- page (a number), page_size, total_pages.
PAGING_FIELDS = ("page", "needs_confirming_page", "next_offset", "has_more", "total_pages")
LISTS = ("shortlist", "needs_confirming")


def is_paged(resp):
    """Whether a response declares paging (the unpaged shape has none of these fields)."""
    return isinstance(resp, dict) and any(key in resp for key in PAGING_FIELDS)


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _onward(page_info):
    """The next offset a page object names, or None at the end. A next offset that does
    not move past the page's own (a page of limit 0 answers next_offset == offset) is the
    end too, or the walk would ask for the same page forever."""
    if not isinstance(page_info, dict):
        return None
    nxt, at = _int(page_info.get("next_offset")), _int(page_info.get("offset")) or 0
    return nxt if nxt is not None and nxt > at else None


def _next_request(body, resp, sent, held):
    """The request for the cards after `resp` (the answer to `sent`), or None when the
    response says nothing is left. body: the first request; held: {list: cards held}."""
    nxt = json.loads(json.dumps(body))
    sl, nc = resp.get("page"), resp.get("needs_confirming_page")
    if isinstance(sl, dict) or isinstance(nc, dict):
        sl_next, nc_next = _onward(sl), _onward(nc)
        if sl_next is None and nc_next is None:
            return None
        # A list with nothing left is asked from past its end, so the page is empty (and
        # cheap) rather than the first page again.
        end = _int((sl or {}).get("total")) if isinstance(sl, dict) else None
        nxt["offset"] = sl_next if sl_next is not None else max(end or 0, held["shortlist"])
        nxt["limit"] = PAGE_LIMIT
        if nc_next is not None:
            nxt.update(needs_confirming_offset=nc_next, needs_confirming_limit=PAGE_LIMIT)
        else:
            # NOTHING LEFT TO CONFIRM: the rest of the shortlist without the explanation,
            # which costs a page about half again (VIC on the search lane: 3.3 s, not 5.8).
            nxt.pop("explain", None)
        return nxt
    if _int(resp.get("page")) is not None:
        total = _int(resp.get("total_pages"))
        if total is not None and resp["page"] >= total:
            return None
        nxt["page"] = resp["page"] + 1
        if _int(resp.get("page_size")) is not None:
            nxt["page_size"] = resp["page_size"]
        return nxt
    more, onward = resp.get("has_more"), _int(resp.get("next_offset"))
    if more is False or (more is None and onward is None):
        return None
    if onward is None:
        at = _int(resp.get("offset"))
        onward = (sent.get("offset", 0) if at is None else at) + max(
            len(resp.get("shortlist") or ()), len(resp.get("needs_confirming") or ()), 1)
    nxt.update(offset=onward, limit=PAGE_LIMIT)
    return nxt


def fetch_every_page(post, body):
    """Every card a search returns, across all its pages.

    {status, response (the first answer), shortlist, needs_confirming, shortlist_count,
     needs_confirming_count, pages, paged, short (a list the response counts more of than
     it handed over), page_bytes, seconds, error}. See the section comment for the walk."""
    started = time.monotonic()
    status, resp, _seconds = post(body)
    out = {"status": status, "response": resp, "shortlist": [], "needs_confirming": [],
           "shortlist_count": None, "needs_confirming_count": None, "pages": 1,
           "paged": False, "short": [], "page_bytes": [], "error": ""}
    if status != 200 or not isinstance(resp, dict) or resp.get("status") != "success":
        out["error"] = "HTTP %s, status %r: %s" % (
            status, (resp or {}).get("status") if isinstance(resp, dict) else None,
            _plain(str((resp or {}).get("message") if isinstance(resp, dict) else resp))[:200])
        out["seconds"] = time.monotonic() - started
        return out
    seen = {key: {} for key in LISTS}

    def take(page):
        out["page_bytes"].append(len(json.dumps(page, default=str)))
        added = 0
        for key in LISTS:
            for entry in page.get(key) or ():
                if isinstance(entry, dict):
                    h = SR._hash(entry)
                    if h and h not in seen[key]:
                        seen[key][h] = entry
                        added += 1
        return added

    def count(key):
        return _int(out[key + "_count"])

    def wanting():
        return [key for key in LISTS if count(key) is not None and len(seen[key]) < count(key)]

    take(resp)
    out["shortlist_count"] = resp.get("shortlist_count")
    out["needs_confirming_count"] = resp.get("needs_confirming_count")
    out["paged"] = is_paged(resp)
    page, sent, asked = resp, body, False
    while out["pages"] < MAX_PAGES:
        if is_paged(page):
            nxt = _next_request(body, page, sent, {k: len(v) for k, v in seen.items()})
            # Counts the first answer states are the target: every one met, the walk is
            # done whatever a page's own flags say.
            if nxt is not None and not wanting() and all(
                    count(k) is not None for k in LISTS if k + "_count" in resp):
                nxt = None
        elif wanting() and not asked:
            nxt = json.loads(json.dumps(body))
            nxt.update(offset=len(seen["shortlist"]), limit=PAGE_LIMIT,
                       needs_confirming_offset=len(seen["needs_confirming"]),
                       needs_confirming_limit=PAGE_LIMIT)
            asked = True
        else:
            nxt = None
        if nxt is None:
            break
        status, page, _seconds = post(nxt)
        out["pages"] += 1
        if status != 200 or not isinstance(page, dict) or page.get("status") != "success":
            out["error"] = "page %d: HTTP %s" % (out["pages"], status)
            break
        out["paged"] = out["paged"] or is_paged(page)
        sent = nxt
        if not take(page):
            break
    for key in LISTS:
        out[key] = list(seen[key].values())
        if count(key) is not None and len(out[key]) < count(key):
            out["short"].append("%s: %d counted, %d handed over%s" % (
                key, count(key), len(out[key]),
                " across %d page(s)" % out["pages"] if out["paged"]
                else " and the response offers no next page"))
    out["seconds"] = time.monotonic() - started
    return out


# --------------------------------------------------------- why a lot is hidden (child)

def _latest_read_is_full(reads_by_day, share=0.60, runs=7):
    """_candidates.latest_read_is_full, for a bundle that predates it."""
    if not reads_by_day:
        return False
    days = sorted(reads_by_day, reverse=True)[:max(1, runs)]
    return reads_by_day[days[0]] >= share * max(reads_by_day[d] for d in days)


def read_left_it_out(rec, doc, latest_read, reads, cut_short, full):
    """True when a row's own document was read again IN FULL on a later day than the row
    (latest_read_is_full over the rows last read each day) and the row was not on that read.

    Repair round 1, 7 Oct 2026. Such a row is not something the sources list tonight, so it
    is not obvious: a lot E-Agent's list dropped two nights ago is not one Coleen sees on
    E-Agent. Before this the bar counted it obvious and the search's own guard that hides
    it (document_reread_without_row) as an excuse -- and an excuse no longer props a state
    up (see EXCUSED_MAX). A read cut short, a builder-wide group and a row with no date are
    never judged."""
    day = SR._day(rec.get("date_checked"))
    latest = latest_read.get(doc)
    if (day is None or latest is None or latest <= day or doc in cut_short
            or not doc or doc.startswith("group:")):
        return False
    return bool(full(dict(reads.get(doc) or {})))


def _visible_key(entry_or_package):
    return (str(entry_or_package.get("lot_address") or "").strip().lower(),
            round(SR._num(entry_or_package.get("advertised_package_price")) or 0))


def _lot_number(rec):
    return _norm((rec or {}).get("lot_number"))


def same_lot(catalogue, docs, h, twin):
    """Whether shown card `twin` is the same lot as obvious row `h`: the same line read
    again (allowing either capture cut short or missing a column, see recaptured), or a
    copy on another document whose lot number does not say otherwise. Two different lines
    of one list are two lots (the audit's false merges: Atlas units 2302 and 1712, both
    carded 'Melbourne VIC'; plan rank 4, "never merge two different lines of the same
    document")."""
    a, b = catalogue.db_row(h), catalogue.db_row(twin)
    if a is None or b is None:
        return False
    line_a = SR.status_free(a.get("source_text") or a.get("lot_address"))
    line_b = SR.status_free(b.get("source_text") or b.get("lot_address"))
    if SR.same_line(line_a, line_b):
        return True
    na, nb = _lot_number(a), _lot_number(b)
    if na and nb and na != nb:
        return False
    if docs.get(h) and docs.get(h) == docs.get(twin):
        return recaptured(line_a, line_b)
    return True


def recaptured(line_a, line_b):
    """Whether two lines of ONE document are two captures of the same line, one missing a
    column the other read: every number the shorter prints is on the longer, and at most
    one of its words is not (a run of glyphs read glued, "EmblaF" / "Embla D").

    7 Oct 2026, measured on the 6 Oct database: Hattan 9088 and 19329 are both live, both
    read on 06/10, "... Lot 720 263 12.5 Denver 17 Single Storey 4 2 2 $361,000 $395,650
    $756,650" and the same line without "Single Storey" -- one lot, and the search rightly
    shows it once. Two PACKAGES of one lot differ in a number or in their design's words
    (Goldstate lot 5710: "Atlanta 23 Panorama 4 Single 206" / "Trinidad 23 Sovereign 4
    Single 210", both $712,000), and two units of one tower in their unit numbers (Atlas
    2302 and 1712): those stay two lots, and merging them stays a failure."""
    words_a, words_b = str(line_a or "").split(), str(line_b or "").split()
    short, long_ = (words_a, words_b) if len(words_a) <= len(words_b) else (words_b, words_a)
    numbers = Counter(w for w in short if w.isdigit())
    if sum(numbers.values()) < 3 or numbers - Counter(w for w in long_ if w.isdigit()):
        return False
    others = set(w for w in long_ if not w.isdigit())
    return sum(1 for w in short if not w.isdigit() and w not in others) <= 1


def classify(verdict, h, catalogue, docs, visible, shown_by_key, research, bootstrap, used,
             listed_short):
    """(reason, twin hash or None) for an obvious lot the search does not show, from the
    bundle's own explanation of it (explain_rows) read through search_regressions'
    _obvious_outcome."""
    outcome, _detail = SR._obvious_outcome(verdict, visible, research, bootstrap, used,
                                           listed_short)
    if outcome == "collapsed_into_visible":
        twins = shown_by_key.get(_visible_key((verdict or {}).get("package") or {}), [])
        same = [t for t in twins if t != h and same_lot(catalogue, docs, h, t)]
        if same:
            return SHOWN_TWIN, same[0]
        return "merged_with_another_lot", (twins[0] if twins else None)
    if outcome == "same_listing_collapsed":
        return "merged_into_unshown_twin", None
    if outcome == "availability_unstated":
        detail = (verdict or {}).get("detail") or {}
        why = detail.get("not_admitted") or detail.get("not_promoted") or "refused"
        return (why if why in ALLOWED else "availability_unstated:" + str(why)), None
    return outcome, None


def _name_twin(lot, dropped, shown, catalogue, docs):
    """Re-read a hidden lot the scorer passed (scored_not_shown) through the pipeline's own
    dedupe: merged into a twin that is shown and is the same lot (allowed), into a twin
    that is a different lot, or into a twin nobody sees. Updates `lot` in place."""
    twin = dropped.get(lot["hash"])
    if not twin:
        return
    if twin in shown:
        reason = SHOWN_TWIN if same_lot(catalogue, docs, lot["hash"], twin) \
            else "merged_with_another_lot"
    else:
        reason = "merged_into_unshown_twin"
    lot.update(reason=reason, allowed=allowed(reason), twin=twin[:11])


def allowed(reason):
    return reason in ALLOWED


def _dedupe_twins(C, brief_dict, rows, prepared):
    """{hash of a package the pipeline's dedupe dropped: hash of the package it kept}, by
    the search's own path: build_packages, then the bundle's dedupe as api/research.py runs
    it -- _candidates.dedupe_packages where the bundle has one (the search lane keys it on
    the lot as well as the card, 7 Oct 2026), else sources.dedupe's engine as origin/main
    calls it. Of several kept cards on one key, the one with the same lot number."""
    packages, _counts = C.build_packages(brief_dict, rows, prepared=prepared)
    merged = {}
    if callable(getattr(C, "dedupe_packages", None)):
        try:
            # The bundle names the twin of every lot its same-lot collapse folded (repair
            # round 1, 7 Oct 2026: one lot under two card spellings -- Hattan lot 3208 on
            # E-Agent and in an email). Those twins carry another card, so the card key
            # below cannot find them.
            kept = C.dedupe_packages(list(packages), rows, merged_into=merged)
        except TypeError:
            kept = C.dedupe_packages(list(packages), rows)
    else:
        from sources.dedupe import DedupeEngine
        kept = DedupeEngine.deduplicate(list(packages))

    def key(p):
        return "%s||%s||%s" % (str(p.get("lot_address", "")).strip().lower(),
                               str(p.get("house_design", "")).strip().lower(),
                               float(p.get("advertised_package_price", 0) or 0))
    kept_ids = {id(p) for p in kept}
    kept_by_key = defaultdict(list)
    for p in kept:
        kept_by_key[key(p)].append(p)
    out = {}
    for p in packages:
        if id(p) in kept_ids:
            continue
        if merged.get(SR._hash(p)):
            out[SR._hash(p)] = merged[SR._hash(p)]
            continue
        twins = kept_by_key.get(key(p)) or []
        same = [t for t in twins if _lot_number(t) and _lot_number(t) == _lot_number(p)]
        out[SR._hash(p)] = SR._hash((same or twins)[0]) if twins else None
    return out


# ------------------------------------------------------------------- the child process

def _searched(post, state):
    brief = dict(FORM, state=state)
    return fetch_every_page(post, {"client_brief": brief, "explain": True})


def measure(args):
    """Everything that needs the bundle, written to args.out as JSON."""
    started = time.monotonic()
    bundle = Path(args.bundle).resolve()
    snapshot = Path(args.snapshot).resolve()
    app = str(APP).lower()
    sys.path[:] = [str(bundle), str(bundle / "_lib")] + [
        p for p in sys.path if p and os.path.abspath(p).lower() != app]
    SR._read_only_bundle(bundle)
    import _bootstrap
    import _candidates as C
    import research
    _bootstrap.get_config()
    import config
    import geo
    import suburb_quality
    for module in (research, C, _bootstrap, config, geo, suburb_quality):
        if not os.path.abspath(module.__file__).lower().startswith(str(bundle).lower()):
            raise RuntimeError("%s was imported from outside the bundle" % module.__name__)
    if args.today:
        today = SR._day(args.today)
        if today is None:
            raise RuntimeError("--today must be dd/mm/yyyy")
        SR._freeze_today(C, today)
    else:
        today = C._now_aest().date()
    rows, path = C.load_snapshot()
    if os.path.abspath(path).lower() != str(snapshot).lower():
        raise RuntimeError("the bundle read a different snapshot than --snapshot")
    db_rows = SR.read_db_rows(args.db)
    catalogue = SR.Catalogue(rows, db_rows)
    prepared = C.prepared_for_snapshot(rows, path)
    post = SR.in_process_post(research)

    def overseas(rec, snap):
        if (snap or {}).get("suburb_source") == suburb_quality.BLANK_OVERSEAS:
            return True
        try:
            return bool(suburb_quality.is_overseas(dict(rec)))
        except Exception:                                             # noqa: BLE001
            return False

    docs = {}
    reads = defaultdict(Counter)        # doc -> {day: rows last read that day}
    counters = {}                       # doc -> the largest available counter it stated
    proxima_docs = set()
    for rec in db_rows:
        h = SR._hash(rec)
        doc = doc_key(rec, catalogue.row(h))
        if h and (h not in docs or not rec.get("superseded_by")):
            docs[h] = doc
        day = SR._day(rec.get("date_checked"))
        if day is not None:
            reads[doc][day] += 1
        counter = SR._num(rec.get("project_available"))
        if counter:
            counters[doc] = max(counters.get(doc, 0), counter)
        if _channel(rec).lower().startswith("proxima"):
            proxima_docs.add(doc)
    latest_read = {doc: max(days) for doc, days in reads.items()}
    # A READ CUT SHORT IS NOT A RE-READ. A Proxima project that stopped at its render cap
    # (THE MAYBELLE: 80 read on 06/10 against its 141 available) did not leave its other
    # lots out -- it never reached them. Those are the READ bar's to name, not over-served.
    cut_short = {doc for doc in proxima_docs if doc in latest_read and (
        reads[doc][latest_read[doc]] in PROXIMA_RENDER_CAPS
        or reads[doc][latest_read[doc]] < counters.get(doc, 0))}
    # AND ANY LIST WHOSE NEWEST READ DROPPED MORE THAN THE SEARCH TRUSTS night over night
    # (repair round 1, 7 Oct 2026; _candidates.night_drop_share): the search keeps showing
    # what such a read left out -- Verv Projects' sheet, 19% on 06/10, whose two readers
    # disagree -- so neither the obvious set nor the over-served bar may judge it either.
    # NOT a Proxima project: the search never hides a Proxima lot on a re-read (every row
    # shares the portal's index page), so a project that dropped lots without reaching its
    # cap or its counter is exactly what the over-served bar is for.
    drop_share = getattr(C, "night_drop_share", None)
    drop_max = getattr(C, "FRESH_REREAD_DROP_MAX", None)
    if drop_share is not None and drop_max is not None:
        live_days = defaultdict(Counter)
        for rec in db_rows:
            day = SR._day(rec.get("date_checked"))
            if day is not None and not rec.get("superseded_by"):
                live_days[docs.get(SR._hash(rec)) or ""][day] += 1
        cut_short |= {doc for doc, days in live_days.items()
                      if doc and not doc.startswith("group:") and doc not in proxima_docs
                      and drop_share(dict(days)) > drop_max}
    full = getattr(C, "latest_read_is_full", None) or _latest_read_is_full

    def left_out(rec, doc):
        return read_left_it_out(rec, doc, latest_read, reads, cut_short, full)

    blank_stating = {}
    lots = obvious_lots(catalogue, today, C.snapshot_build_cells, overseas, left_out,
                        blank_stating=blank_stating)
    by_group = defaultdict(list)
    for lot in lots:
        by_group[lot["group"]].append(lot)
    evidence = SR.guard_evidence(catalogue)
    fresh_guard = _fresh_guard_counts(C, rows, prepared, suburb_quality)
    hand_checked = getattr(suburb_quality, "FROM_VERIFIED", None)
    newest = max(latest_read.values()) if latest_read else None
    print("parity: %s obvious lots from the database (%s), today %s; ready in %.1f s" % (
        _n(len(lots)), ", ".join("%s %s" % (g, _n(len(v))) for g, v in sorted(by_group.items())),
        today.strftime("%d/%m/%Y"), time.monotonic() - started), flush=True)

    shown_anywhere = set()
    groups = {}
    verdict_sources = []          # (used brief, verdicts) per state, for the blank-state lots
    blank_wanted = {lot["hash"] for lot in by_group.get(BLANK, [])}
    for state in args.states:
        got = _searched(post, state)
        result = {"searched": True, "seconds": round(got["seconds"], 2),
                  "pages": got["pages"], "paged": got["paged"], "short": got["short"],
                  "error": got["error"], "page_bytes": got["page_bytes"],
                  "shortlist_count": got["shortlist_count"],
                  "needs_confirming_count": got["needs_confirming_count"],
                  "shortlist_listed": len(got["shortlist"]),
                  "needs_confirming_listed": len(got["needs_confirming"])}
        resp = got["response"] if isinstance(got["response"], dict) else {}
        help_ = resp.get("search_help") if isinstance(resp.get("search_help"), dict) else {}
        if help_.get("error"):
            result["error"] = (result["error"] + "; " if result["error"] else "") + \
                "search_help.error: " + _plain(help_["error"])[:200]
        entries = got["shortlist"] + got["needs_confirming"]
        shown = {SR._hash(e) for e in entries} - {""}
        shown_anywhere |= shown
        visible = {_visible_key(e) for e in entries}
        shown_by_key = defaultdict(list)
        for e in entries:
            shown_by_key[_visible_key(e)].append(SR._hash(e))
        mine = by_group.get(state, [])
        hidden = [lot for lot in mine if lot["hash"] not in shown]
        # The rows with no state ride along on a state's explanation only when that state
        # needs one anyway: a blank-state row is reached by a state search only when the
        # search borrowed it a state, and then that state's pass says what became of it.
        wanted = ({lot["hash"] for lot in hidden} | blank_wanted) if hidden else set()
        explaining = time.monotonic()
        verdicts = {}
        used = resp.get("brief_used") or dict(FORM, state=state)
        if wanted and not got["error"]:
            brief_dict = research._normalised_brief(used)[0]
            for row, verdict in C.explain_rows(brief_dict, catalogue.rows, prepared=prepared,
                                               admit=C.ADMITTABLE_GATES):
                h = SR._hash(row)
                if h in wanted and (h not in verdicts or not row.get("superseded_by")):
                    verdicts[h] = verdict
        # The pipeline's dedupe, replayed at most once per state and only when a hidden lot
        # needs it (see below) -- a row with no state included, when this state's search
        # borrowed it one (Hattan's emailed lots carry no state).
        replay = {}

        def dropped_twins(used=used):
            if "map" not in replay:
                replay["map"] = _dedupe_twins(C, research._normalised_brief(used)[0], rows,
                                              prepared)
            return replay["map"]
        if wanted and not got["error"]:
            verdict_sources.append((used, verdicts, visible, shown_by_key, dropped_twins,
                                    shown))
        listed_short = any(s.startswith("needs_confirming") for s in got["short"])
        hidden_out = []
        for lot in hidden:
            if got["error"]:
                reason, twin = "search_failed", None
            elif catalogue.row(lot["hash"]) is None:
                reason, twin = "not_in_snapshot", None
            else:
                reason, twin = classify(verdicts.get(lot["hash"]), lot["hash"], catalogue,
                                        docs, visible, shown_by_key, research, _bootstrap,
                                        used, listed_short)
            hidden_out.append(dict(lot, reason=reason, allowed=allowed(reason),
                                   twin=(twin or "")[:11]))
        if any(lot["reason"] in REPLAYED for lot in hidden_out):
            # SCORED AND ON NO LIST: usually the pipeline's dedupe (address, design and
            # price) kept a twin that is itself hidden -- Creation Homes 8470 into 7904,
            # whose price its own line does not print. _obvious_outcome re-scores the lot
            # alone and cannot see that, so the dedupe is replayed once to name the twin.
            # And MERGED WITH ANOTHER LOT is asked again (repair round 1): the card-key
            # match names whichever shown card reads alike, and Celia St's list carries
            # two lines carded "30 Celia St, G-Floor room Updated" at one price -- the
            # 4-bed copy was merged into its own other capture, not into the 5-bed line.
            for lot in hidden_out:
                if lot["reason"] in REPLAYED:
                    _name_twin(lot, dropped_twins(), shown, catalogue, docs)
        # AN EXCUSE ONLY ON ITS EVIDENCE (repair round 2): a sold-stock reason the
        # database does not back is a lot hidden for nothing.
        for lot in hidden_out:
            if lot["allowed"] and lot["reason"] in SR.GUARD_EVIDENCE_REASONS:
                lacking = evidence.unsupported(catalogue.row(lot["hash"]) or {}, lot["reason"])
                if lacking:
                    lot.update(reason="unsupported:" + lot["reason"], allowed=False,
                               lacking=lacking)
        result["demoted_unsupported"] = _demoted_without_evidence(
            got["needs_confirming"], {lot["hash"] for lot in mine}, catalogue, evidence)
        result["whole_buildings_excused"] = _whole_buildings_excused(mine, hidden_out)
        result["precision"] = _precision(entries, state, catalogue, hand_checked)
        result["unlabelled"] = _unlabelled(got["shortlist"], catalogue)
        result["blank_on_stating_list"] = {
            "count": sum((blank_stating.get(state) or Counter()).values()),
            "docs": dict((blank_stating.get(state) or Counter()).most_common(6))}
        result["fresh_guard"] = fresh_guard.get(state) or {}
        result.update(_tally(mine, shown, {lot["hash"] for lot in hidden_out if lot["allowed"]},
                             shortlisted={SR._hash(e) for e in got["shortlist"]} - {""},
                             twins=_twins_of(hidden_out, got["shortlist"])))
        result["hidden"] = hidden_out
        # THE SHORTLIST ONLY (repair round 2): a card under "Needs confirming" already says
        # its list may have moved on -- that is why it is there. Counted over both lists,
        # QLD read 46.6% "unread" once the fresh guard sent old lists' lots to confirm
        # instead of hiding them.
        result["over_served"] = _over_served(got["shortlist"], catalogue, docs, latest_read,
                                             today, cut_short)
        result["duplicates"] = _duplicates(entries, catalogue, docs)
        result["drift"] = _drift_counts(mine)
        # SHOWN LOTS, beside the obvious ones, for the drift bar: a night the search shows
        # far fewer of the same obvious lots falls on this number, not on the obvious count.
        result["drift"]["shown"] = result["lots"]["all"][1]
        result["explain_seconds"] = round(time.monotonic() - explaining, 2)
        groups[state] = result
        print("parity: %-3s searched in %.1f s (%d call%s), hidden lots explained in %.1f s: "
              "%s shortlisted, %s to confirm; %s of %s obvious lots shown" % (
                  state, got["seconds"], got["pages"], "" if got["pages"] == 1 else "s",
                  result["explain_seconds"],
                  _n(len(got["shortlist"])), _n(len(got["needs_confirming"])),
                  _n(result["lots"]["all"][1]), _n(result["lots"]["all"][0])), flush=True)

    for group in (BLANK, OTHER):
        mine = by_group.get(group, [])
        result = {"searched": False, "hidden": []}
        result["drift"] = _drift_counts(mine)
        if group == BLANK:
            hidden_out = []
            for lot in mine:
                if lot["hash"] in shown_anywhere:
                    continue
                reason, twin, source = "state_unknown", None, None
                for source in verdict_sources:
                    used, verdicts, visible, shown_by_key = source[:4]
                    verdict = verdicts.get(lot["hash"])
                    if verdict and verdict.get("gate") not in ("state_unknown", "other_state"):
                        reason, twin = classify(verdict, lot["hash"], catalogue, docs,
                                                visible, shown_by_key, research, _bootstrap,
                                                used, False)
                        break
                out_lot = dict(lot, reason=reason, allowed=allowed(reason),
                               twin=(twin or "")[:11])
                if reason in REPLAYED and source is not None:
                    _name_twin(out_lot, source[4](), source[5], catalogue, docs)
                hidden_out.append(out_lot)
            result["hidden"] = hidden_out
        result.update(_tally(mine, shown_anywhere,
                             {lot["hash"] for lot in result["hidden"] if lot["allowed"]},
                             twins=_twins_of(result["hidden"], ())))
        groups[group] = result
    # THE ROWS BEHIND THE MANIFEST'S "STORED ONCE SOMEWHERE ELSE" (8 Oct 2026, second
    # repair round): the READ bar counts such a lot as stored only on its row's evidence.
    manifest = (load_json(args.manifest)
                if getattr(args, "manifest", None) and Path(args.manifest).is_file() else None)
    out = {"today": today.strftime("%d/%m/%Y"), "snapshot_rows": len(rows),
           "cross_listed_rows": cross_listed_row_facts(
               db_rows, manifest, {SR._hash(r) for r in rows} - {""}),
           "obvious": len(lots), "groups": groups,
           "newest_read": newest.strftime("%d/%m/%Y") if newest else "",
           "seconds": round(time.monotonic() - started, 1)}
    Path(args.out).write_text(json.dumps(out, default=str), encoding="utf-8")
    return 0


def _fresh_guard_counts(C, rows, prepared, suburb_quality):
    """{state: {"hidden": {reason: n}, "confirm": {reason: n}}}: live rows read inside the
    search's FRESH_DAYS that its fresh-row guard keeps off the shortlist -- hidden, or sent to
    "Needs confirming" (repair round 2, callers verifier: on 6 Oct the guard hid about 1,050
    such rows with no flag, and the obvious set, three nights deep, never saw them). {} for a
    bundle without the guard."""
    guard = getattr(C, "fresh_row_guard", None)
    fresh = getattr(C, "_fresh", None)
    if guard is None or fresh is None:
        return {}
    actions = C.fresh_guard_actions() if callable(getattr(C, "fresh_guard_actions", None)) \
        else {r: "hide" for r in getattr(C, "FRESH_GUARD_HIDES", ())}
    now = C._now_aest()
    out = {}
    for row in rows:
        if row.get("superseded_by"):
            continue
        verified, _seen, _raw = fresh(row, now)
        if not verified:
            continue
        why = guard(row, prepared, now)
        if not why:
            continue
        state = str(row.get("state") or "").strip().upper() or BLANK
        cell = out.setdefault(state, {"hidden": Counter(), "confirm": Counter()})
        cell["confirm" if actions.get(why) == "confirm" else "hidden"][why] += 1
    return {s: {k: dict(v) for k, v in cell.items()} for s, cell in out.items()}


def _demoted_without_evidence(confirming, obvious, catalogue, evidence):
    """[{id, reason, lacking}] for obvious lots sent to "Needs confirming" for a guard reason
    the database does not back (repair round 2): off the shortlist Clare reads, for nothing."""
    out = []
    for e in confirming:
        h = SR._hash(e)
        if h not in obvious:
            continue
        for reason in e.get("confirm") or ():
            if reason not in SR.GUARD_EVIDENCE_REASONS:
                continue
            lacking = evidence.unsupported(catalogue.row(h) or {}, reason)
            if lacking:
                out.append({"id": (catalogue.db_row(h) or {}).get("id"), "reason": reason,
                            "lacking": lacking})
    return out


def _whole_buildings_excused(mine, hidden):
    """[{building, lots, ids}] for obvious buildings of WHOLE_BUILDING_MIN lots or more whose
    every lot is hidden for a document or project reason (see WHOLE_BUILDING_REASONS)."""
    by_hash = {lot["hash"]: lot for lot in hidden}
    buildings = defaultdict(list)
    for lot in mine:
        buildings[lot["building"]].append(lot)
    out = []
    for lots_ in buildings.values():
        if len(lots_) < WHOLE_BUILDING_MIN:
            continue
        reasons = [str((by_hash.get(lot["hash"]) or {}).get("reason") or "").split(":")[-1]
                   for lot in lots_]
        if all(r in WHOLE_BUILDING_REASONS for r in reasons):
            out.append({"building": lots_[0]["building_label"], "lots": len(lots_),
                        "ids": [lot["id"] for lot in lots_[:NAME_FIRST]],
                        "reasons": dict(Counter(reasons))})
    return out


def _precision(entries, state, catalogue, hand_checked=None):
    """{wrong_state, gone, retired, missing}: ids of shown cards that are not what the search
    was asked for (repair round 2, parity verifier): a card in another state than the one
    searched -- by the card or by its database row, unless the row is blank or a hand-checked
    rule moved it -- one whose row says it has gone, one whose row is superseded, or one the
    database does not hold. Every one of these must be 0. The other bars count what is
    MISSING; without this one a search that also returned NSW lots for VIC passed them."""
    out = {"wrong_state": [], "gone": [], "retired": [], "missing": []}
    for e in entries:
        h = SR._hash(e)
        rec = catalogue.db_row(h)
        if rec is None:
            out["missing"].append(h[:11])
            continue
        ident = rec.get("id")
        stored = str(rec.get("state") or "").strip().upper()
        snap = catalogue.row(h) or {}
        moved_by_hand = hand_checked and snap.get("suburb_source") == hand_checked
        if (str(e.get("state") or "").strip().upper() != state
                or (stored and stored != state and not moved_by_hand)):
            out["wrong_state"].append(ident)
        if str(rec.get("availability_status") or "").strip().lower() in SR.NOT_AVAILABLE:
            out["gone"].append(ident)
        if rec.get("superseded_by"):
            out["retired"].append(ident)
    return out


def _unlabelled(shortlist, catalogue):
    """{count, ids}: shortlisted cards whose title names no lot and no street (repair round
    2): 'GOLF $3,497,500', 'PARK RIDGE'. Reported, not failed -- the row labels are their
    own piece of work -- so how many reach Clare's list is in front of the owner nightly."""
    ids = [(catalogue.db_row(SR._hash(e)) or {}).get("id") for e in shortlist
           if SR.no_lot_and_no_street(e)]
    return {"count": len(ids), "ids": ids[:NAME_FIRST]}


def _twins_of(hidden, shortlist):
    """{hash of a lot hidden as a copy of a shown card: True when that card is on the
    shortlist} -- the lots _tally counts as shown through their twin."""
    on_list = {SR._hash(e)[:11] for e in shortlist}
    return {lot["hash"]: lot.get("twin", "") in on_list
            for lot in hidden if lot.get("reason") == SHOWN_TWIN}


def _tally(lots, shown, excused=frozenset(), shortlisted=None, twins=None):
    """{lots: {group: [expected, shown]}, buildings: {...}, obvious: {group: n},
    excused: {group: n}, shortlisted: {group: [expected, on the shortlist]}} for E-Agent
    and all channels.

    A lot hidden for an ALLOWED reason (sold-stock evidence, a dead page, a price that is
    not a home's) is excused: it is out of what the search should show, so it neither
    counts against the bar nor props it up. A building whose every obvious lot is excused
    is out with them. HOW MANY are excused is a bar of its own (EXCUSED_MAX): an excuse
    that grows without limit hides a loss as surely as no excuse at all.

    twins ({hash: twin on the shortlist?}, repair round 1): a lot hidden only because the
    SAME lot is shown under another card is shown -- Coleen sees it -- and counts as shown,
    not as excused.

    shortlisted: of the expected lots, those on the SHORTLIST rather than "Needs
    confirming" -- what Clare and Agent 2 read (shortlisted None: not counted)."""
    twins = twins or {}
    seen = set(shown) | set(twins)
    out = {"lots": {}, "buildings": {}, "obvious": {}, "excused": {}}
    if shortlisted is not None:
        out["shortlisted"] = {}
        listed = set(shortlisted) | {h for h, on in twins.items() if on}
    for name in CHANNEL_GROUPS:
        mine = [lot for lot in lots if name == "all" or lot["e_agent"]]
        expected = [lot for lot in mine
                    if lot["hash"] in seen or lot["hash"] not in excused]
        buildings = defaultdict(bool)
        for lot in expected:
            buildings[lot["building"]] |= lot["hash"] in seen
        out["lots"][name] = [len(expected), sum(1 for lot in expected if lot["hash"] in seen)]
        out["buildings"][name] = [len(buildings), sum(1 for v in buildings.values() if v)]
        out["obvious"][name] = len(mine)
        out["excused"][name] = len(mine) - len(expected)
        if shortlisted is not None:
            out["shortlisted"][name] = [len(expected), sum(1 for lot in expected
                                                           if lot["hash"] in listed)]
    return out


def _duplicates(entries, catalogue, docs):
    """{shown, surplus, groups, examples}: cards that are a second copy of a lot already
    shown -- the same builder's same lot number at the same price in the same suburb, or
    the same line of one document at the same price -- judged pairwise by same_lot (two
    packages of one lot, or two units of one tower, are not copies).

    Repair round 1, 7 Oct 2026: about 2% of a state-wide answer was such copies (Hattan lot
    3208 on E-Agent and in an email; the Celia St list read with and without a heading);
    nothing counted them."""
    by_key = defaultdict(list)
    shown = 0
    for e in entries:
        h = SR._hash(e)
        rec = catalogue.db_row(h)
        if rec is None:
            continue
        shown += 1
        price = round(SR._num(e.get("advertised_package_price")) or SR._num(rec.get("price"))
                      or 0)
        if not price:
            continue
        builder, lot = _norm(rec.get("builder_name")), _lot_number(rec)
        if not lot:
            m = re.match(r"^\s*(?:lot|unit)\s*([a-z]?\d+[a-z]?)\b",
                         str(e.get("lot_address") or ""), re.I)
            lot = m.group(1).lower() if m else ""
        if builder and lot:
            # NOT KEYED BY THE CARD'S SUBURB (repair round 2, parity verifier): the same
            # Hattan lot 58 is carded "Springvale" off one list's "Springvale Double" heading
            # and "Springvale South" off the other, so 11 copies went uncounted. The suburbs
            # are compared loosely, pair by pair, below.
            by_key[("lot", builder, lot, price)].append(h)
        line = SR.status_free(rec.get("source_text") or "")
        if len(line) >= SR._SAME_LINE_MIN and docs.get(h):
            by_key[("line", docs[h], line, price)].append(h)
    suburb_of = {SR._hash(e): set(_norm(e.get("suburb")).split()) for e in entries}

    def places_agree(a, b):
        """One card's suburb words inside the other's ('springvale' in 'springvale
        south'), or either blank."""
        x, y = suburb_of.get(a) or set(), suburb_of.get(b) or set()
        return not x or not y or x <= y or y <= x
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def facts_differ(a, b):
        ra, rb = catalogue.db_row(a) or {}, catalogue.db_row(b) or {}
        for field, tolerance in (("bedrooms", 0), ("house_sqm", 1.0), ("land_sqm", 1.0)):
            x, y = SR._num(ra.get(field)), SR._num(rb.get(field))
            if x and y and abs(x - y) > tolerance:
                return True                  # two packages of one lot, not two copies
        return False
    for members in by_key.values():
        if len(members) < 2:
            continue
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if (a != b and find(a) != find(b) and not facts_differ(a, b)
                        and places_agree(a, b) and same_lot(catalogue, docs, a, b)):
                    parent[find(b)] = find(a)
    sets = defaultdict(list)
    for h in parent:
        sets[find(h)].append(h)
    copies = [sorted(v, key=lambda h: (catalogue.db_row(h) or {}).get("id") or 0)
              for v in sets.values() if len(v) > 1]
    copies.sort(key=lambda v: -len(v))
    return {"shown": shown, "surplus": sum(len(v) - 1 for v in copies), "groups": len(copies),
            "examples": [[(catalogue.db_row(h) or {}).get("id") for h in v]
                         for v in copies[:NAME_FIRST]]}


def _over_served(entries, catalogue, docs, latest_read, today, cut_short=()):
    """Shown cards whose own document was read again in the last RECENT_NIGHTS nights, on a
    later night than the card's own row: the document no longer carries the lot. A card on
    a document whose latest read was cut short is not judged (counted as unjudged)."""
    shown, over, unjudged, examples, by_doc = 0, 0, 0, [], Counter()
    unread, unread_docs = 0, Counter()
    for entry in entries:
        h = SR._hash(entry)
        rec = catalogue.db_row(h)
        if rec is None:
            continue
        shown += 1
        doc = docs.get(h) or ""
        day = SR._day(rec.get("date_checked"))
        latest = latest_read.get(doc)
        if (doc and not doc.startswith("group:") and latest is not None
                and (today - latest).days >= RECENT_NIGHTS):
            # ON A LIST NOBODY HAS READ FOR 3 NIGHTS OR MORE (repair round 1, 7 Oct 2026):
            # nothing can say whether that list still carries the lot, so it is counted
            # and named -- not judged over-served, and not silently left out either.
            unread += 1
            unread_docs[doc_label(rec)] += 1
            continue
        if (not doc or doc.startswith("group:") or day is None or latest is None
                or latest <= day):
            continue
        if doc in cut_short:
            unjudged += 1
            continue
        over += 1
        by_doc[doc_label(rec)] += 1
        if len(examples) < NAME_FIRST:
            examples.append(rec.get("id"))
    # EVERY unread list, not the first ten (repair round 2): an acknowledged list is taken
    # out of the share by name (see acknowledged).
    return {"shown": shown, "over": over, "unjudged": unjudged, "ids": examples,
            "docs": dict(by_doc.most_common(10)), "unread": unread,
            "unread_docs": dict(unread_docs.most_common())}


def _drift_counts(lots):
    channels, docs, labels, buildings = Counter(), Counter(), {}, set()
    for lot in lots:
        channels[lot["channel"]] += 1
        docs[lot["doc"]] += 1
        labels[lot["doc"]] = lot["doc_label"]
        buildings.add(lot["building"])
    return {"lots": len(lots), "buildings": len(buildings), "channels": dict(channels),
            "docs": dict(docs), "doc_labels": labels}


# ------------------------------------------------------------------- the bars (parent)

def load_acknowledged(path, today):
    """([acknowledgement in force tonight], [note]).

    Repair round 2 (7 Oct 2026, callers verifier): the bars failed every night on rows only
    the client or the owner can fix (a price list with no suburb on it, lots filed with no
    state), so CI went red and the DEGRADED alert fired daily -- which trains people to stop
    reading it. data/parity_acknowledged.json names them, each with why and an `until`
    date: {"acknowledged": [{"group": "VIC", "reason": "no_suburb", "ids": [17450, ...],
    "why": "...", "until": "21/10/2026"}, {"group": "QLD", "bar": "unread", "docs":
    ["E-Agent - Victoria & Albert"], ...}]}. An acknowledged row or list is reported as a
    note, not a failure, until that date; after it the failure is back and a note says the
    acknowledgement expired. Anything not named still fails."""
    return _acks_in_force(load_json(path) if path and Path(path).is_file() else None, today)


def _acks_in_force(data, today):
    """load_acknowledged over an already-parsed file."""
    entries = data.get("acknowledged") if isinstance(data, dict) else None
    live, notes = [], []
    tonight = SR._day(today)
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        until = SR._day(entry.get("until"))
        what = "%s %s" % (entry.get("group") or "any state",
                          entry.get("reason") or entry.get("bar") or "")
        if until is None or tonight is None or until < tonight:
            notes.append("acknowledgement %s (%s) expired %s -- it fails again until fixed "
                         "or renewed" % (what.strip(), _plain(entry.get("why") or "")[:80],
                                         entry.get("until") or "(no date)"))
            continue
        live.append(entry)
    return live, notes


def _acked(acks, group, reason=None, bar=None):
    """The acknowledgements in force for this group and reason (or bar)."""
    out = []
    for entry in acks or ():
        if entry.get("group") not in (None, "", group):
            continue
        if reason is not None and entry.get("reason") not in (None, "", reason):
            continue
        if bar is not None and entry.get("bar") != bar:
            continue
        out.append(entry)
    return out


def shown_bar(groups, acks=None):
    """([failure], [table row], [note]) for the SHOWN bar."""
    failures, rows, notes = [], [], []
    for state in [g for g in SEARCHED_STATES if g in groups] + [BLANK]:
        result = groups.get(state)
        if not result or (state == BLANK and not result["lots"]["all"][0]):
            continue
        row = {"group": state, "lots": result["lots"], "buildings": result["buildings"],
               "obvious": result.get("obvious") or {}, "excused": result.get("excused") or {},
               "shortlisted": result.get("shortlisted") or {}, "pass": True}
        if result.get("searched"):
            if result.get("error"):
                failures.append({"bar": "shown", "group": state,
                                 "text": "the search failed: %s" % result["error"]})
                row["pass"] = False
            for short in result.get("short") or ():
                # A shortlist that counts lots it will not hand over says "N results" and
                # shows fewer: a failure whoever the lots are. A needs-confirming list cut
                # short is a failure only for the obvious lots past its end, which are
                # named below as needs_confirming_unlisted.
                if short.startswith("shortlist"):
                    failures.append({"bar": "shown", "group": state,
                                     "text": "the response does not hand over every lot it "
                                             "counts -- %s" % short})
                    row["pass"] = False
                else:
                    notes.append("%s: %s" % (state, short))
            heavy = [b for b in result.get("page_bytes") or () if b > RESPONSE_LIMIT_BYTES]
            if heavy:
                failures.append({"bar": "shown", "group": state,
                                 "text": "a page of %.1f MB is over Vercel's %.1f MB response "
                                         "limit: on the live site this search does not answer"
                                         % (max(heavy) / 1e6, RESPONSE_LIMIT_BYTES / 1e6)})
                row["pass"] = False
            for name in CHANNEL_GROUPS:
                total, shown = result["lots"][name]
                excused = (result.get("excused") or {}).get(name) or 0
                if total and _pct(shown, total) < SHOWN_LOTS_MIN:
                    failures.append({"bar": "shown", "group": state,
                                     "text": "%s: %s of %s obvious lots shown (%s, needs %.0f%%)%s"
                                             % (_label(name), _n(shown), _n(total),
                                                _fmt_pct(shown, total), SHOWN_LOTS_MIN * 100,
                                                "; %s more hidden for an allowed reason"
                                                % _n(excused) if excused else "")})
                    row["pass"] = False
                total, shown = result["buildings"][name]
                if total and _pct(shown, total) < SHOWN_BUILDINGS_MIN:
                    failures.append({"bar": "shown", "group": state,
                                     "text": "%s: %s of %s obvious buildings have a lot shown "
                                             "(%s, needs %.0f%%)"
                                             % (_label(name), _n(shown), _n(total),
                                                _fmt_pct(shown, total),
                                                SHOWN_BUILDINGS_MIN * 100)})
                    row["pass"] = False
                # AN EXCUSE HAS A CEILING (repair round 1). Excused lots leave the
                # denominator, so without one a 404 storm that hid 74 of SA's 113 lots read
                # "39/39 100.0%" and passed.
                obvious = (result.get("obvious") or {}).get(name) or 0
                if excused > max(EXCUSED_FLOOR, obvious * EXCUSED_MAX):
                    failures.append({"bar": "shown", "group": state,
                                     "text": "%s: %s of %s obvious lots are hidden for an allowed "
                                             "reason (%s, at most %.0f%% or %d) -- %s" % (
                                                 _label(name), _n(excused), _n(obvious),
                                                 _fmt_pct(excused, obvious), EXCUSED_MAX * 100,
                                                 EXCUSED_FLOOR, _excused_by_reason(result, name))})
                    row["pass"] = False
                # ON THE SHORTLIST, not only somewhere on the page (repair round 1). Clare and
                # Agent 2 read the shortlist alone; a night that sends every fresh lot to
                # "Needs confirming" showed SA 113/113 and passed.
                total, listed = (result.get("shortlisted") or {}).get(name) or (0, 0)
                if total and _pct(listed, total) < SHORTLISTED_MIN:
                    failures.append({"bar": "shown", "group": state,
                                     "text": "%s: %s of %s obvious lots are on the shortlist (%s, "
                                             "needs %.0f%%) -- the rest only under Needs "
                                             "confirming, which Clare and Agent 2 never read"
                                             % (_label(name), _n(listed), _n(total),
                                                _fmt_pct(listed, total),
                                                SHORTLISTED_MIN * 100)})
                    row["pass"] = False
        by_reason = defaultdict(list)
        allowed_tally = Counter()
        acked = defaultdict(list)
        for lot in result.get("hidden") or ():
            if lot["allowed"]:
                allowed_tally[lot["reason"]] += 1
                continue
            known = [e for e in _acked(acks, state, reason=lot["reason"])
                     if lot["id"] in set(e.get("ids") or ())]
            if known:
                acked[(lot["reason"], known[0].get("until"),
                       _plain(known[0].get("why") or "")[:120])].append(lot["id"])
            else:
                by_reason[lot["reason"]].append(lot)
        for (reason, until, why), ids in sorted(acked.items()):
            notes.append("%s: %s obvious lot(s) hidden as %s, acknowledged until %s (%s): "
                         "ids %s" % (state if state != BLANK else "no state", _n(len(ids)),
                                     reason, until, why,
                                     ", ".join(str(i) for i in ids[:NAME_FIRST])))
        # SOLD ON NOTHING, AND WHOLE BUILDINGS (repair round 2).
        for item in result.get("demoted_unsupported") or ():
            failures.append({"bar": "shown", "group": state, "reason": "demoted_unsupported",
                             "ids": [item["id"]],
                             "text": "obvious lot %s was sent to Needs confirming as %s, but %s"
                                     % (item["id"], item["reason"], item["lacking"])})
            row["pass"] = False
        for item in result.get("whole_buildings_excused") or ():
            failures.append({"bar": "shown", "group": state, "reason": "whole_building",
                             "ids": item["ids"],
                             "text": "all %s obvious lots of %s are hidden for their own "
                                     "list's sake (%s) -- a whole building is not sold in a "
                                     "night: ids %s" % (
                                         _n(item["lots"]), item["building"],
                                         ", ".join("%s %s" % kv for kv in item["reasons"].items()),
                                         ", ".join(str(i) for i in item["ids"][:10]))})
            row["pass"] = False
        for reason, lots in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
            ids = ", ".join(str(lot["id"]) for lot in lots[:NAME_FIRST])
            e_agent = sum(1 for lot in lots if lot["e_agent"])
            failures.append({"bar": "shown", "group": state, "reason": reason,
                             "count": len(lots), "ids": [lot["id"] for lot in lots[:NAME_FIRST]],
                             "text": "%s obvious lot%s (%s E-Agent) hidden as %s -- %s: ids %s%s"
                                     % (_n(len(lots)), "" if len(lots) == 1 else "s",
                                        _n(e_agent), reason, REASON_TEXT.get(
                                            reason.split(":")[0], "not an allowed reason"),
                                        ids, " ..." if len(lots) > NAME_FIRST else "")})
            row["pass"] = False
        row["hidden_allowed"] = dict(allowed_tally)
        row["hidden_failing"] = {r: len(v) for r, v in by_reason.items()}
        if allowed_tally:
            notes.append("%s: hidden for an allowed reason -- %s" % (
                state if state != BLANK else "no state",
                ", ".join("%s %s" % (r, _n(n)) for r, n in allowed_tally.most_common())))
        # WHAT THE BARS DO NOT COUNT, named every night (repair round 2).
        blank = result.get("blank_on_stating_list") or {}
        if blank.get("count"):
            row["blank_on_stating_list"] = blank["count"]
            notes.append("%s: %s lot(s) read in the last %d nights are blank on a list that "
                         "states 'Available' beside them, so they are not counted obvious -- "
                         "E-Agent still shows them (a status the parser does not read, or "
                         "'EOI'): %s" % (state, _n(blank["count"]), RECENT_NIGHTS,
                                         "; ".join("%s (%d)" % kv
                                                   for kv in list(blank["docs"].items())[:5])))
        guard = result.get("fresh_guard") or {}
        if guard.get("hidden") or guard.get("confirm"):
            row["fresh_guard_hidden"] = sum((guard.get("hidden") or {}).values())
            row["fresh_guard_confirm"] = sum((guard.get("confirm") or {}).values())
            notes.append("%s: read in the last 14 days and kept off the shortlist by the fresh-"
                         "row guard -- hidden %s (%s); sent to Needs confirming %s (%s)" % (
                             state, _n(row["fresh_guard_hidden"]),
                             ", ".join("%s %s" % kv for kv in sorted(
                                 (guard.get("hidden") or {}).items())) or "none",
                             _n(row["fresh_guard_confirm"]),
                             ", ".join("%s %s" % kv for kv in sorted(
                                 (guard.get("confirm") or {}).items())) or "none"))
        unlabelled = result.get("unlabelled") or {}
        if unlabelled.get("count"):
            notes.append("%s: %s shortlisted card(s) name no lot and no street (row labels): "
                         "ids %s" % (state, _n(unlabelled["count"]),
                                     ", ".join(str(i) for i in unlabelled["ids"][:10])))
        rows.append(row)
    return failures, rows, notes


def precision_bar(groups):
    """[failure]: shown cards that are not what was searched for (_precision). Every one
    fails: a card in another state, gone by its own row, superseded, or not in the
    database. Repair round 2, parity verifier: a bundle that put NSW lots into the VIC
    answer (VIC 2,738 -> 5,533 cards) passed every bar before this one."""
    failures = []
    for state in SEARCHED_STATES:
        cell = (groups.get(state) or {}).get("precision")
        if not cell:
            continue
        for kind, text in (("wrong_state", "in another state than %s" % state),
                           ("gone", "whose own row says it has gone"),
                           ("retired", "whose row is superseded"),
                           ("missing", "the database does not hold")):
            ids = cell.get(kind) or []
            if ids:
                failures.append({"bar": "precision", "group": state, "reason": kind,
                                 "ids": ids[:NAME_FIRST],
                                 "text": "%s shown card(s) %s: ids %s%s" % (
                                     _n(len(ids)), text,
                                     ", ".join(str(i) for i in ids[:NAME_FIRST]),
                                     " ..." if len(ids) > NAME_FIRST else "")})
    return failures


def _label(name):
    return "E-Agent" if name == "e-agent" else "all channels"


def _excused_by_reason(result, name):
    tally = Counter(lot["reason"] for lot in result.get("hidden") or ()
                    if lot.get("allowed") and lot.get("reason") != SHOWN_TWIN
                    and (name == "all" or lot.get("e_agent")))
    return ", ".join("%s %s" % (r, _n(n)) for r, n in tally.most_common(6)) or "none named"


def coverage_bar(measured, groups):
    """[failure], [note]: is there anything to compare at all? (repair round 1)

    A bar over nothing passes: _pct(0, 0) is 100%. Run against a database six days stale,
    the check found 0 obvious lots, read every state as 0/0, and passed -- while the search
    shortlisted 2,944 VIC lots from that same stale stock. So: the database's newest read
    must be inside RECENT_NIGHTS, every state searched must hold obvious lots, and E-Agent
    must hold at least OBVIOUS_E_AGENT_MIN of them."""
    failures, notes = [], []
    today, newest = SR._day(measured.get("today")), SR._day(measured.get("newest_read"))
    if today is not None and (newest is None or (today - newest).days >= RECENT_NIGHTS):
        failures.append({"bar": "coverage", "group": "database",
                         "text": "the newest read in the database is %s, %s night(s) before %s: "
                                 "no harvest landed in the last %d nights, so there is nothing "
                                 "fresh to compare the search with"
                                 % (measured.get("newest_read") or "none",
                                    "?" if newest is None else (today - newest).days,
                                    measured.get("today"), RECENT_NIGHTS)})
    e_agent = 0
    for state in SEARCHED_STATES:
        result = groups.get(state)
        if not result or not result.get("searched"):
            continue
        obvious = (result.get("obvious") or {}).get("all") or 0
        e_agent += (result.get("obvious") or {}).get("e-agent") or 0
        if not obvious and state not in CORE_STATES:
            notes.append("coverage: %s holds no obvious lot tonight -- nothing read in the "
                         "last %d nights is stated for sale there" % (state, RECENT_NIGHTS))
        elif not obvious:
            failures.append({"bar": "coverage", "group": state,
                             "text": "no obvious lot at all -- nothing read in the last %d "
                                     "nights is stated for sale in %s, so the bars there "
                                     "compare nothing" % (RECENT_NIGHTS, state)})
    if any((groups.get(s) or {}).get("searched") for s in SEARCHED_STATES) \
            and e_agent < OBVIOUS_E_AGENT_MIN:
        failures.append({"bar": "coverage", "group": "E-Agent",
                         "text": "%s obvious E-Agent lots across the states searched, under the "
                                 "%d a working harvest always reads" % (_n(e_agent),
                                                                        OBVIOUS_E_AGENT_MIN)})
    return failures, notes


def duplicates_bar(groups):
    """[failure], {state: cell}, [note]: shown cards that are a second copy of a lot
    already shown (_duplicates). At most DUPLICATES_MAX of a state's cards, or
    DUPLICATES_FLOOR, whichever is more."""
    failures, cells, notes = [], {}, []
    for state in SEARCHED_STATES:
        dup = (groups.get(state) or {}).get("duplicates")
        if not dup:
            continue
        cells[state] = dup
        if dup["surplus"] > max(DUPLICATES_FLOOR, dup["shown"] * DUPLICATES_MAX):
            failures.append({"bar": "duplicates", "group": state,
                             "text": "%s of %s shown cards (%s, at most %.1f%% or %d) are a second "
                                     "copy of a lot already shown, in %s group(s); ids %s"
                                     % (_n(dup["surplus"]), _n(dup["shown"]),
                                        _fmt_pct(dup["surplus"], dup["shown"]),
                                        DUPLICATES_MAX * 100, DUPLICATES_FLOOR,
                                        _n(dup["groups"]),
                                        "; ".join("/".join(str(i) for i in ids)
                                                  for ids in dup["examples"][:10]))})
        elif dup["surplus"]:
            notes.append("%s: %s shown card(s) are a second copy of a lot already shown "
                         "(within the bar): ids %s" % (state, _n(dup["surplus"]), "; ".join(
                             "/".join(str(i) for i in ids) for ids in dup["examples"][:5])))
    return failures, cells, notes


def over_served_bar(groups, acks=None):
    """([failure], {state: cell}, [note]) for the NOT OVER-SERVED bar."""
    failures, cells, notes = [], {}, []
    for state in SEARCHED_STATES:
        result = groups.get(state) or {}
        served = result.get("over_served")
        if not served:
            continue
        cells[state] = served
        if served.get("unjudged"):
            notes.append("%s: %s shortlisted lot(s) sit on a list whose latest read stopped short "
                         "(a Proxima project at its render cap or under its own counter, or a "
                         "list that read more than 15%% fewer lots than the night before) -- "
                         "not judged as over-served" % (state, _n(served["unjudged"])))
        unread = served.get("unread") or 0
        # AN ACKNOWLEDGED LIST IS TAKEN OUT OF THE SHARE BY NAME (repair round 2).
        known = [_norm(d) for e in _acked(acks, state, bar="unread") for d in e.get("docs") or ()]
        if unread and known:
            taken = {d: n for d, n in (served.get("unread_docs") or {}).items()
                     if any(_norm(d).startswith(k) for k in known if k)}
            if taken:
                unread -= sum(taken.values())
                entry = _acked(acks, state, bar="unread")[0]
                notes.append("%s: %s shortlisted lot(s) on %d list(s) not read for %d nights are "
                             "acknowledged until %s (%s): %s" % (
                                 state, _n(sum(taken.values())), len(taken), RECENT_NIGHTS,
                                 entry.get("until"), _plain(entry.get("why") or "")[:120],
                                 "; ".join("%s (%d)" % kv for kv in list(taken.items())[:6])))
                served = dict(served, unread_docs={d: n for d, n in
                                                   (served.get("unread_docs") or {}).items()
                                                   if d not in taken})
        if unread:
            docs = "; ".join("%s (%d)" % (d, n)
                             for d, n in list((served.get("unread_docs") or {}).items())[:6])
            text = ("%s of %s shortlisted lots (%s) sit on a list not read for %d nights or "
                    "more -- nobody can say whether it still carries them: %s"
                    % (_n(unread), _n(served["shown"]), _fmt_pct(unread, served["shown"]),
                       RECENT_NIGHTS, docs))
            if served["shown"] and unread / served["shown"] > UNREAD_MAX:
                failures.append({"bar": "over-served", "group": state,
                                 "text": text + " (at most %.0f%%)" % (UNREAD_MAX * 100)})
            else:
                notes.append("%s: %s" % (state, text))
        if served["shown"] and served["over"] / served["shown"] > OVER_SERVED_MAX:
            docs = "; ".join("%s (%d)" % (d, n) for d, n in list(served["docs"].items())[:5])
            failures.append({"bar": "over-served", "group": state,
                             "text": "%s of %s shortlisted lots (%s, at most %.0f%%) are lots "
                                     "their own document was re-read without in the last %d "
                                     "nights:"
                                     " ids %s; documents: %s"
                                     % (_n(served["over"]), _n(served["shown"]),
                                        _fmt_pct(served["over"], served["shown"]),
                                        OVER_SERVED_MAX * 100, RECENT_NIGHTS,
                                        ", ".join(str(i) for i in served["ids"]), docs)})
    return failures, cells, notes


def _earlier_nights(history, night):
    today = SR._day(night)
    nights = [n for n in history if isinstance(n, dict) and SR._day(n.get("night")) is not None
              and today is not None and SR._day(n.get("night")) < today]
    nights.sort(key=lambda n: SR._day(n["night"]), reverse=True)
    return nights


def drift_bar(groups, history, night):
    """[failure], {state: cell}, [note]: tonight against the median of up to DRIFT_NIGHTS
    earlier nights."""
    failures, cells, notes = [], {}, []
    earlier = _earlier_nights(history, night)[:DRIFT_NIGHTS]
    if not earlier:
        notes.append("drift: no earlier night is recorded in data/parity_history.json, so "
                     "there is nothing to compare with -- passes tonight; the bar compares "
                     "from the next night on")
        return failures, {s: "no history" for s in SEARCHED_STATES if s in groups}, notes
    for state in SEARCHED_STATES:
        tonight = (groups.get(state) or {}).get("drift")
        if tonight is None:
            continue
        past = [n["states"][state] for n in earlier
                if isinstance(n.get("states"), dict) and isinstance(n["states"].get(state), dict)]
        if not past:
            cells[state] = "no history"
            continue
        texts = []
        # "shown" (repair round 1): obvious lots the search showed. A night whose obvious set
        # holds steady while the search shows far fewer of it falls here; a night with no
        # "shown" on record (before 7 Oct 2026) has a median of 0 and is not judged.
        for metric in ("lots", "buildings", "shown"):
            median = statistics.median(p.get(metric) or 0 for p in past)
            now = tonight.get(metric) or 0
            if median > 0 and now < median * (1 - DRIFT_DROP_MAX):
                texts.append("%s %s against a %d-night median of %s (%+.0f%%)"
                             % (metric, _n(now), len(past), _n(median),
                                100.0 * (now - median) / median))
        lots_median = statistics.median(p.get("lots") or 0 for p in past)
        cells[state] = "%+.0f%%" % (100.0 * ((tonight.get("lots") or 0) - lots_median)
                                    / lots_median) if lots_median else "-"
        if not texts:
            continue
        fell_channels = []
        for channel in sorted({c for p in past for c in (p.get("channels") or {})}
                              | set(tonight.get("channels") or {})):
            median = statistics.median((p.get("channels") or {}).get(channel, 0) for p in past)
            now = (tonight.get("channels") or {}).get(channel, 0)
            if median > 0 and now < median * (1 - DRIFT_DROP_MAX):
                fell_channels.append("%s %s -> %s" % (channel, _n(median), _n(now)))
        labels = {}
        for p in past:
            labels.update(p.get("doc_labels") or {})
        labels.update(tonight.get("doc_labels") or {})
        drops = []
        for doc in {d for p in past for d in (p.get("docs") or {})}:
            median = statistics.median((p.get("docs") or {}).get(doc, 0) for p in past)
            now = (tonight.get("docs") or {}).get(doc, 0)
            if median - now > 0 and now < median * (1 - DRIFT_DROP_MAX):
                drops.append((median - now, labels.get(doc, doc), median, now))
        drops.sort(key=lambda d: -d[0])
        failures.append({"bar": "drift", "group": state,
                         "text": "%s; channels that fell: %s; documents that fell: %s"
                                 % ("; ".join(texts), ", ".join(fell_channels) or "none by more "
                                    "than 15%", "; ".join("%s %s -> %s" % (
                                        name, _n(m), _n(now)) for _d, name, m, now in drops[:10])
                                    or "none")})
    return failures, cells, notes


def manifest_entries(manifest):
    if isinstance(manifest, list):
        return [e for e in manifest if isinstance(e, dict)]
    if isinstance(manifest, dict):
        for key in ("entries", "links", "documents"):
            if isinstance(manifest.get(key), list):
                return [e for e in manifest[key] if isinstance(e, dict)]
    return []


# THE MANIFEST'S FIELDS, in both spellings. The plan's: url, page_state, heading, outcome,
# printed, stored (a number) and stated_counter. The harvest lane's harvest_manifest.py
# (schema 1, 7 Oct 2026), which never holds a link: channel, kind ("stock link" | "project"
# | "portal"), name, heading/label, stocklist_key or pid, printed, stored as {new, updated,
# unchanged, rejected}, a Proxima project's `stated` (or header_counter [available, total])
# and its own `short` verdict, a portal's cards_found and cards_read.

def _stored(entry):
    """(lots stored tonight, lots a constraint refused) for one entry."""
    stored = entry.get("stored")
    if isinstance(stored, dict):
        kept = sum(SR._num(stored.get(k)) or 0 for k in ("new", "updated", "unchanged"))
        return kept, SR._num(stored.get("rejected")) or 0
    return SR._num(stored), 0


def _counter(entry):
    """The available count a Proxima project states, or None."""
    for key in ("stated_counter", "stated"):
        value = SR._num(entry.get(key))
        if value:
            return value
    header = entry.get("header_counter")
    if isinstance(header, (list, tuple)) and header:
        return SR._num(header[0]) or None
    return None


def _tonights_header(entry):
    """The available count tonight's index header stated for a Proxima project, or None."""
    header = entry.get("header_counter")
    if isinstance(header, (list, tuple)) and header:
        return SR._num(header[0]) or None
    return None


def _named_lots(entry):
    """The lots a Proxima manifest line says were stored once somewhere else, by name
    (cross_listed_lots and duplicate_lots, 8 Oct 2026, second repair round)."""
    out = []
    for key in ("cross_listed_lots", "duplicate_lots"):
        for lot in entry.get(key) or ():
            if isinstance(lot, dict) and str(lot.get("hash") or "").strip():
                out.append(lot)
    return out


def cross_listed_row_facts(db_rows, manifest, snapshot_hashes=()):
    """{content_hash: [what each database row with that identity says]} for every lot a
    manifest line names as stored once somewhere else -- the row-level evidence the READ
    bar's excuse needs (8 Oct 2026, second repair round). Each fact: the row's project,
    price, status, the day it was last read, whether a capture superseded it, and whether
    the snapshot that ships holds it."""
    wanted = {str(lot["hash"]).strip() for e in manifest_entries(manifest)
              for lot in _named_lots(e)}
    facts = defaultdict(list)
    if not wanted:
        return {}
    shipped = set(snapshot_hashes or ())
    for rec in db_rows:
        h = SR._hash(rec)
        if h not in wanted:
            continue
        facts[h].append({"id": rec.get("id"),
                         "pid": str(rec.get("source_project_id") or ""),
                         "price": SR._num(rec.get("price")),
                         "status": str(rec.get("availability_status") or ""),
                         "read": str(rec.get("date_checked") or ""),
                         "superseded": bool(rec.get("superseded_by")),
                         "in_snapshot": h in shipped})
    return dict(facts)


def _lot_proven(lot, facts, night):
    """'' when a row proves the named lot stored, else why not. PROOF is a live row with the
    lot's identity, under the project the harvest says kept it, read tonight, at the price
    and with the status this project printed -- and, for a lot printed for sale, one the
    snapshot ships (shown-eligible). Atchison and Kenny Building B's B-1301 and B-1803 (kept
    as Building A's Not Available rows) and Lancelin's land-only lots 71/129/130 (kept at
    the house-and-land price) are what this refuses."""
    if facts is None:
        return "no row evidence was measured"
    rows = [f for f in facts.get(str(lot.get("hash") or "").strip()) or ()
            if not f.get("superseded")]
    if not rows:
        return "no live row holds it"
    into = str(lot.get("into") or "")
    mine = [f for f in rows if f.get("pid") == into]
    if not mine:
        return "its row is under project %s, not %s" % (rows[0].get("pid") or "(none)", into)
    tonight = SR._day(night)
    mine = [f for f in mine if SR._day(f.get("read")) == tonight] or mine
    row = mine[0]
    if SR._day(row.get("read")) != tonight:
        return "its row was last read %s" % (row.get("read") or "never")
    # By class: an accordion's "For Sale" and an availability view's "Available" say the same.
    status, kept = _status_class(lot.get("status")), _status_class(row.get("status"))
    if status != kept:
        return "its row says %s, not %s" % (row.get("status") or "nothing",
                                            lot.get("status") or "nothing")
    price, kept_price = SR._num(lot.get("price")), SR._num(row.get("price"))
    if price is None or kept_price is None or abs(price - kept_price) >= 1:
        return "its row is priced %s, not %s" % (SR._money(kept_price), SR._money(price))
    if status == "available" and not row.get("in_snapshot"):
        return "its row is not in the snapshot that ships"
    return ""


def _status_class(status):
    """'available', '' (none stated) or 'not available' -- sources/proxima._status_class."""
    said = re.sub(r"\s+", " ", str(status or "")).strip().lower()
    if not said:
        return ""
    return "available" if said in SR.IS_AVAILABLE else "not available"


def _stored_elsewhere(entry, projects_read, facts=None, night=None):
    """(lots stored once somewhere else ON ROW-LEVEL PROOF, [the named others, each with
    why], how many more were claimed with no lot named) for a Proxima manifest line:
    cross-listed under another project read tonight, or printed twice in this project's own
    list.

    8 Oct 2026: Ascenta Living Traditional printed 80 and stored 76, its other 4 under
    Coliving (785). A claim into a project nobody read tonight counts for nothing.

    ONLY ON PROOF (second repair round, correctness verifier): the first cut counted every
    claimed lot as stored, and so passed Atchison and Kenny Building B's 2 For Sale units --
    kept as Building A's Not Available rows, hidden from every search -- and Lancelin's 3
    land-only lots, whose rows hold the house-and-land price. A lot now counts only when the
    manifest names it (cross_listed_lots / duplicate_lots) and `facts`
    (cross_listed_row_facts) show its row live under the kept project, read tonight, at the
    printed price and status (_lot_proven). A count with no names -- an older manifest -- is
    no proof at all."""
    named = _named_lots(entry)
    proven, unproven = 0, []
    for lot in named:
        into = str(lot.get("into") or "")
        why = ("project %s was not read tonight" % into
               if into != str(entry.get("pid") or "") and into not in projects_read
               else _lot_proven(lot, facts, night))
        if why:
            unproven.append("%s (%s)" % (_plain(lot.get("address") or lot.get("lot")
                                                or "a lot")[:48], why))
        else:
            proven += 1
    claimed = sum(int(SR._num(n) or 0) for n in (entry.get("cross_listed_into") or {}).values()) \
        if isinstance(entry.get("cross_listed_into"), dict) else 0
    claimed += int(SR._num(entry.get("duplicates")) or 0)
    return proven, unproven, max(0, claimed - len(named))


def _manifest_key(entry):
    for key in ("stocklist_key", "pid"):
        if str(entry.get(key) or "").strip():
            return "%s:%s:%s" % (entry.get("channel") or "", key, entry[key])
    return _short_hash(entry.get("url") or "%s|%s|%s" % (
        entry.get("channel") or "", entry.get("kind") or "",
        entry.get("name") or entry.get("heading") or json.dumps(entry, sort_keys=True)))


def _manifest_label(entry):
    """What a person calls an entry -- channel, page, heading, name, file -- never a link."""
    parts = [str(entry.get(k) or "").strip() for k in ("channel", "page", "page_state",
                                                        "heading", "name", "label")]
    if str(entry.get("pid") or "").strip():
        parts.insert(1, "project %s" % entry["pid"])
    parts.append(_file_name(entry.get("url")))
    seen, out = set(), []
    for part in parts:
        part = _plain(part)
        if part and part.lower() not in seen and not part.lower().startswith(("http", "www.")):
            seen.add(part.lower())
            out.append(part)
    return (" - ".join(out) or "(unnamed link)")[:90]


def _is_proxima(entry):
    text = " ".join(str(entry.get(k) or "") for k in ("channel", "source", "kind", "url"))
    return "proxima" in text.lower()


def read_bar(manifest, history, night, required=False, facts=None):
    """[failure], {summary}, [note] from data/harvest_manifest.json (None: none written).

    facts: cross_listed_row_facts for the lots the manifest names as stored once somewhere
    else (measured from the database with the snapshot); None when none were measured, and
    then no such lot counts as stored (8 Oct 2026, second repair round).

    required (--nightly): the harvest writes the manifest every night, so a nightly run
    without one -- or with one from an earlier night -- FAILS rather than skipping (repair
    round 1, 7 Oct 2026: skipped, the bar said nothing on every run it was never fed). A
    manual run without a manifest still only says so."""
    if manifest is None:
        text = ("no data/harvest_manifest.json -- the harvest writes it every night, so "
                "tonight's reads cannot be judged")
        if required:
            return ([{"bar": "read", "group": "harvest", "text": text}],
                    {"skipped": True, "missing": True}, [])
        return [], {"skipped": True}, ["read: %s (the read bars were skipped)" % text]
    entries = manifest_entries(manifest)
    failures, notes = [], []
    written = SR._day(str((manifest or {}).get("generated") or "")[:10]) \
        if isinstance(manifest, dict) else None
    tonight_day = SR._day(night)
    if written is not None and tonight_day is not None and (tonight_day - written).days > 1:
        text = ("data/harvest_manifest.json was written %s, not tonight -- it describes an "
                "earlier harvest" % written.strftime("%d/%m/%Y"))
        if required:
            failures.append({"bar": "read", "group": "harvest", "text": text})
        else:
            notes.append("read: " + text)
    if not entries:
        failures.append({"bar": "read", "group": "harvest",
                         "text": "data/harvest_manifest.json lists no link at all"})
    no_outcome = [e for e in entries if not str(e.get("outcome") or "").strip()]
    if no_outcome:
        failures.append({"bar": "read", "group": "harvest",
                         "text": "%d link(s) have no outcome: %s" % (
                             len(no_outcome), "; ".join(_manifest_label(e)
                                                        for e in no_outcome[:NAME_FIRST]))})
    short = []
    elsewhere_notes = []
    projects_read = {str(e.get("pid")) for e in entries
                     if _is_proxima(e) and str(e.get("pid") or "").strip()
                     and str(e.get("outcome") or "").strip().lower() == "read"}
    for e in entries:
        printed = SR._num(e.get("printed"))
        stored, refused = _stored(e)
        elsewhere, unproven, unnamed = _stored_elsewhere(e, projects_read, facts, night)
        why_not = "; ".join(unproven[:4] + (
            ["%s claimed with no lot named, so no row can prove them" % _n(unnamed)]
            if unnamed else []))
        # A NAMED LOT THE DATABASE CONTRADICTS FAILS WHATEVER THE SHARE (second repair
        # round): 2 For Sale units kept as another project's Not Available rows are 2 lots
        # hidden, in a list of 19 or of 305 alike. A bare count still goes through the share.
        if (printed and printed > 0 and (stored or 0) + elsewhere < printed * READ_STORED_MIN) \
                or unproven:
            short.append("%s (stored %s of %s printed%s%s%s)" % (
                _manifest_label(e), _n(stored), _n(printed),
                ", %s refused by a constraint" % _n(refused) if refused else "",
                ", %s stored once under another project" % _n(elsewhere) if elsewhere else "",
                "; not proven stored elsewhere: %s" % why_not if why_not else ""))
        elif elsewhere:
            elsewhere_notes.append("%s %s of %s (%s)" % (
                _manifest_label(e), _n(elsewhere), _n(printed), ", ".join(
                    "%s into project %s" % (_n(n), pid)
                    for pid, n in sorted((e.get("cross_listed_into") or {}).items()))
                or "printed twice"))
    if short:
        failures.append({"bar": "read", "group": "harvest",
                         "text": "%d document(s) stored under %.0f%% of the lots they printed: %s"
                                 % (len(short), READ_STORED_MIN * 100,
                                    "; ".join(short[:NAME_FIRST]))})
    if elsewhere_notes:
        notes.append("read: %d Proxima project(s) printed lots stored once under another "
                     "project, or twice in their own list -- counted as stored: %s"
                     % (len(elsewhere_notes), "; ".join(elsewhere_notes[:NAME_FIRST])))
    capped, stale, overseas = [], [], []
    for e in entries:
        if not _is_proxima(e):
            continue
        count = SR._num(e.get("printed"))
        if count is None:
            count = _stored(e)[0]
        counter = _counter(e)
        if count is None:
            continue
        # HOW THE READ IS KNOWN WHOLE, AND HOW OLD THE COUNTER IS (8 Oct 2026). A manifest
        # from the harvest since then says both: `whole` (the list ended under its render
        # cap, the price sorts and bands met, or the availability view) and stated_source
        # ("header" = tonight's index, "stored" = the last night the project was on it). An
        # entry without them -- an older manifest -- is judged exactly as before.
        whole = str(e.get("whole") or "").strip()
        old_counter = (str(e.get("stated_source") or "") == "stored"
                       and not _tonights_header(e))
        cut = int(count) in PROXIMA_RENDER_CAPS and counter != count and not whole
        below = bool(counter) and count < counter
        if e.get("overseas") is True:
            # Not Australian stock, which is what this check is about: named, never passed
            # over in silence (SMDC's Philippine projects, 8 Oct 2026).
            if cut or below or e.get("short") is True:
                overseas.append("%s read %s%s" % (_manifest_label(e), _n(count),
                                                  " (marked short)" if e.get("short") else ""))
            continue
        # A project that ends exactly where the accordion stops rendering was cut there --
        # unless its own counter says it has exactly that many available, or the read was
        # proven whole.
        # ...and an old counter excuses only a small gap (repair round, 8 Oct 2026): "whole"
        # for a read under the render cap says only that the list ended there, and a
        # narrowed list ends there too -- 2 against an old 79 is short, as it always was.
        excused = bool(old_counter and whole and HM.old_counter_gap_ok(count, counter))
        if cut:
            capped.append("%s ends at exactly %d%s" % (
                _manifest_label(e), int(count),
                " of its %s available" % _n(counter) if counter else ""))
        elif below and not excused:
            why = ""
            if old_counter and whole:
                why = (" (nothing read, which an old counter never excuses)" if count <= 0
                       else " (read whole, but more under its old counter than lots leave a "
                            "list: at most %d or %d%% may)"
                       % (HM.OLD_COUNTER_GAP_LOTS, round(HM.OLD_COUNTER_GAP_SHARE * 100)))
            capped.append("%s read %s of its %s available%s" % (
                _manifest_label(e), _n(count), _n(counter), why))
        elif below:
            # Read whole tonight, against the number the index printed on an earlier night:
            # the difference sold or left the For Sale list since. Said, never failed.
            stale.append("%s read %s, whole (%s), against the %s its counter stated%s" % (
                _manifest_label(e), _n(count), whole, _n(counter),
                " on %s" % e["stated_on"] if e.get("stated_on") else " on an earlier night"))
        elif e.get("short") is True:
            capped.append("%s: the harvest marked its read short" % _manifest_label(e))
    if capped:
        failures.append({"bar": "read", "group": "Proxima",
                         "text": "%d project(s) were not read to their end: %s"
                                 % (len(capped), "; ".join(capped[:NAME_FIRST]))})
    if stale:
        notes.append("read: %d Proxima project(s) hold fewer lots than an old counter, each "
                     "read whole tonight: %s" % (len(stale), "; ".join(stale[:NAME_FIRST])))
    if overseas:
        notes.append("read: %d overseas Proxima project(s) read short -- not Australian stock, "
                     "not judged: %s" % (len(overseas), "; ".join(overseas[:NAME_FIRST])))
    cards = []
    for e in entries:
        found, read = SR._num(e.get("cards_found")), SR._num(e.get("cards_read"))
        if found and (read or 0) < found:
            cards.append("%s (read %s of %s cards)" % (_manifest_label(e), _n(read), _n(found)))
    if cards:
        failures.append({"bar": "read", "group": "portals",
                         "text": "%d portal(s) read fewer cards than their page shows: %s"
                                 % (len(cards), "; ".join(cards[:NAME_FIRST]))})
    tonight = {_manifest_key(e): int(_stored(e)[0] or 0) for e in entries}
    labels = {_manifest_key(e): _manifest_label(e) for e in entries}
    earlier = _earlier_nights(history, night)[:DRIFT_NIGHTS]
    vanished = []
    # TONIGHT'S LINKS AND THE WEEK'S (repair round 1): a link that left E-Agent's page
    # entirely has no entry tonight at all, and only looping over tonight's entries never
    # saw it go. A link absent tonight stored 0 tonight.
    for n in earlier:
        for key, label in (n.get("manifest_labels") or {}).items():
            labels.setdefault(key, label)
    for key in sorted(set(tonight) | {k for n in earlier for k in (n.get("manifest") or {})}):
        if tonight.get(key):
            continue
        before = [(n.get("manifest") or {}).get(key) for n in earlier]
        if any(b for b in before if isinstance(b, (int, float))):
            vanished.append(labels.get(key) or key)
    if vanished:
        failures.append({"bar": "read", "group": "harvest",
                         "text": "%d document(s) yielded lots in the last week and 0 tonight "
                                 "(read empty, or no longer on the source's page): %s"
                                 % (len(vanished), "; ".join(vanished[:NAME_FIRST]))})
    named = [_manifest_label(e) + ": " + str(e.get("outcome"))
             for e in entries if str(e.get("outcome") or "").strip().lower()
             not in ("", "read")]
    if named:
        notes.append("read: %d link(s) not read as a price list tonight -- %s" % (
            len(named), "; ".join(named[:NAME_FIRST])))
    summary = {"links": len(entries), "no_outcome": len(no_outcome), "short": len(short),
               "proxima_cut": len(capped), "portals_short": len(cards),
               "vanished": len(vanished), "manifest": tonight, "labels": labels,
               # 8 Oct 2026: what the two Proxima rules above now say instead of failing.
               "proxima_old_counter": len(stale), "proxima_overseas_short": len(overseas),
               "stored_elsewhere": len(elsewhere_notes)}
    return failures, summary, notes


# ------------------------------------------------------------------ report and history

def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_history(path):
    data = load_json(path)
    nights = (data or {}).get("nights") if isinstance(data, dict) else None
    return [n for n in nights or () if isinstance(n, dict)]


def history_entry(measured, read_summary):
    states = {}
    for group in SEARCHED_STATES + (BLANK,):
        drift = (measured["groups"].get(group) or {}).get("drift")
        if drift is not None:
            states[group] = drift
    entry = {"night": measured["today"], "recorded": datetime.now().isoformat(timespec="seconds"),
             "states": states}
    if read_summary and not read_summary.get("skipped"):
        entry["manifest"] = read_summary.get("manifest") or {}
        entry["manifest_labels"] = read_summary.get("labels") or {}
    return entry


def save_history(path, history, entry):
    nights = [n for n in history if n.get("night") != entry["night"]] + [entry]
    nights.sort(key=lambda n: SR._day(n.get("night")) or datetime.min.date())
    nights = nights[-HISTORY_KEEP:]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps({"version": 1, "nights": nights}), encoding="utf-8")
    os.replace(tmp, path)


def table(measured, shown_rows, over_cells, drift_cells, verdict, failures, notes,
          dup_cells=None):
    """The compact text table: stdout, the alert, and the Settings tab's fallback.

    Repair round 1 (7 Oct 2026) adds what a ratio alone hid: how many obvious lots were
    excused (they leave the denominator), how many of the shown ones are on the
    SHORTLIST, and how many cards are a second copy of a lot already shown."""
    dup_cells = dup_cells or {}
    lines = ["Source-to-search parity, %s: %s (newest read %s)" % (
                 measured["today"], verdict, measured.get("newest_read") or "?"),
             "%-6s %-22s %-22s %-18s %-9s %-16s %-11s %-7s %-12s %-6s %s" % (
                 "state", "E-Agent lots shown", "all lots shown", "buildings shown",
                 "excused", "on shortlist", "over-served", "copies", "drift",
                 # Repair round 2: blank on a list stating Available (not counted
                 # obvious), and fresh rows the guard hid / sent to confirm.
                 "blank", "guard hid/confirm")]
    for row in shown_rows:
        g = row["group"]
        ea, al = row["lots"]["e-agent"], row["lots"]["all"]
        bl = row["buildings"]["all"]
        sl = (row.get("shortlisted") or {}).get("all")
        served = over_cells.get(g)
        dup = dup_cells.get(g)
        lines.append("%-6s %-22s %-22s %-18s %-9s %-16s %-11s %-7s %-12s %-6s %s" % (
            g if g != BLANK else "none",
            "%s/%s %s" % (_n(ea[1]), _n(ea[0]), _fmt_pct(ea[1], ea[0])),
            "%s/%s %s" % (_n(al[1]), _n(al[0]), _fmt_pct(al[1], al[0])),
            "%s/%s %s" % (_n(bl[1]), _n(bl[0]), _fmt_pct(bl[1], bl[0])),
            "%s/%s" % (_n((row.get("excused") or {}).get("all")),
                       _n((row.get("obvious") or {}).get("all"))),
            "%s/%s %s" % (_n(sl[1]), _n(sl[0]), _fmt_pct(sl[1], sl[0])) if sl else "-",
            _fmt_pct(served["over"], served["shown"]) if served else "-",
            _n(dup["surplus"]) if dup else "-",
            drift_cells.get(g, "-") if g != BLANK else "-",
            _n(row.get("blank_on_stating_list")) if g != BLANK else "-",
            "%s/%s" % (_n(row.get("fresh_guard_hidden")), _n(row.get("fresh_guard_confirm")))
            if g != BLANK else "-"))
    other = (measured["groups"].get(OTHER) or {}).get("lots", {}).get("all", [0, 0])
    if other[0]:
        lines.append("(ACT/TAS/NT: %s obvious lots, %s shown by a state search -- the form "
                     "cannot search those states by name)" % (_n(other[0]), _n(other[1])))
    for f in failures:
        lines.append("FAIL %s %s: %s" % (f["bar"], f["group"], f["text"]))
    for note in notes:
        lines.append("note: %s" % note)
    return "\n".join(_plain(line) for line in lines)


def evaluate(measured, history, manifest, nightly=False, acknowledged=None):
    """(verdict, failures, notes, table rows...) -- every bar over one measurement.
    nightly: the run_daily step, where a missing or stale harvest manifest fails.
    acknowledged: the parsed data/parity_acknowledged.json (load_json), or None."""
    groups = measured["groups"]
    acks, ack_notes = load_acknowledged(None, measured["today"]) if acknowledged is None \
        else _acks_in_force(acknowledged, measured["today"])
    cover_failures, cover_notes = coverage_bar(measured, groups)
    shown_failures, shown_rows, shown_notes = shown_bar(groups, acks)
    precision_failures = precision_bar(groups)
    over_failures, over_cells, over_notes = over_served_bar(groups, acks)
    dup_failures, dup_cells, dup_notes = duplicates_bar(groups)
    drift_failures, drift_cells, drift_notes = drift_bar(groups, history, measured["today"])
    read_failures, read_summary, read_notes = read_bar(manifest, history, measured["today"],
                                                       required=nightly,
                                                       facts=measured.get("cross_listed_rows"))
    failures = (cover_failures + shown_failures + precision_failures + over_failures
                + dup_failures + drift_failures + read_failures)
    notes = (cover_notes + shown_notes + over_notes + dup_notes + drift_notes + read_notes
             + ack_notes)
    verdict = "FAIL" if failures else "PASS"
    return {"verdict": verdict, "failures": failures, "notes": notes,
            "shown_rows": shown_rows, "over_cells": over_cells, "drift_cells": drift_cells,
            "dup_cells": dup_cells,
            "read": {k: v for k, v in read_summary.items() if k not in ("manifest", "labels")},
            "read_summary": read_summary}


def report_body(measured, result):
    rows = []
    for row in result["shown_rows"]:
        g = row["group"]
        served = result["over_cells"].get(g)
        dup = (result.get("dup_cells") or {}).get(g)
        rows.append({"state": g if g != BLANK else "no state",
                     "e_agent_lots": row["lots"]["e-agent"], "lots": row["lots"]["all"],
                     "e_agent_buildings": row["buildings"]["e-agent"],
                     "buildings": row["buildings"]["all"],
                     "obvious_lots": row["obvious"].get("all"),
                     "excused_lots": row["excused"].get("all"),
                     "shortlisted": (row.get("shortlisted") or {}).get("all"),
                     "over_served": [served["over"], served["shown"]] if served else None,
                     "unread": [served.get("unread", 0), served["shown"]] if served else None,
                     "copies": [dup["surplus"], dup["shown"]] if dup else None,
                     "drift": result["drift_cells"].get(g) if g != BLANK else None,
                     # Repair round 2: what the ratios above leave out.
                     "blank_on_stating_list": row.get("blank_on_stating_list", 0),
                     "fresh_guard_hidden": row.get("fresh_guard_hidden", 0),
                     "fresh_guard_confirm": row.get("fresh_guard_confirm", 0),
                     "pass": row["pass"]})
    searches = {g: {k: r.get(k) for k in ("seconds", "pages", "paged", "shortlist_count",
                                          "needs_confirming_count", "page_bytes")}
                for g, r in measured["groups"].items() if r.get("searched")}
    # The report is published (the Settings tab). It names lots and documents, never a
    # link: every sentence in it goes through the same scrub as stdout.
    return {"version": 1, "night": measured["today"],
            "generated": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "verdict": result["verdict"], "rows": rows,
            "failures": [dict(f, text=_plain(f["text"])) for f in result["failures"]],
            "notes": [_plain(n) for n in result["notes"]], "read": result["read"],
            "searches": searches,
            "newest_read": measured.get("newest_read") or "",
            "bars": {"shown_lots_min": SHOWN_LOTS_MIN,
                     "shown_buildings_min": SHOWN_BUILDINGS_MIN,
                     "over_served_max": OVER_SERVED_MAX, "drift_drop_max": DRIFT_DROP_MAX,
                     "recent_nights": RECENT_NIGHTS, "excused_max": EXCUSED_MAX,
                     "shortlisted_min": SHORTLISTED_MIN, "duplicates_max": DUPLICATES_MAX,
                     "unread_max": UNREAD_MAX, "excused_floor": EXCUSED_FLOOR,
                     "whole_building_min": WHOLE_BUILDING_MIN},
            "table": table(measured, result["shown_rows"], result["over_cells"],
                           result["drift_cells"], result["verdict"], result["failures"],
                           result["notes"], result.get("dup_cells"))}


def _write(path, body):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(body, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _mark_degraded(marker, result):
    first = "; ".join("%s %s: %s" % (f["bar"], f["group"], f["text"][:140])
                      for f in result["failures"][:2])
    line = _plain("7/7  Source-to-search parity: %d bar failure(s) -- %s" % (
        len(result["failures"]), first))
    try:
        with Path(marker).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        print("  (could not write the degraded marker: %s)" % exc)


# ---------------------------------------------------------------------- the parent run

def run(args, out=None):
    out = out or sys.stdout
    started = time.monotonic()
    snapshot = Path(args.snapshot).resolve()
    bundle = Path(args.bundle).resolve() if args.bundle else snapshot.parent / "api"
    db = Path(args.db).resolve() if args.db else SR._default_db()

    def say(text):
        print(_plain(text), file=out, flush=True)

    for what, path in (("snapshot", snapshot), ("database", db)):
        if not path.is_file():
            say("[FAIL] setup: no %s at %s" % (what, path))
            return 1
    if not (bundle / "research.py").is_file():
        say("[FAIL] setup: no research bundle at %s (build_web writes it beside stock.json)"
            % bundle)
        return 1
    before = SR._tree(bundle)
    with tempfile.TemporaryDirectory(prefix="spb_parity_", ignore_cleanup_errors=True) as tmp:
        copy = Path(tmp) / "catalogue-copy.db"
        try:
            SR.copy_db_read_only(db, copy)
        except Exception as exc:                                      # noqa: BLE001
            say("[FAIL] setup: could not copy the database read-only: %s" % exc)
            return 1
        env = dict(os.environ)
        for name in ("GROQ_API_KEY", "SPB_COMP_PROVIDER", "SPB_SNAPSHOT_DB"):
            env.pop(name, None)
        env.update(SPB_DATABASE_PATH=str(copy), SPB_SNAPSHOT_JSON=str(snapshot),
                   PYTHONDONTWRITEBYTECODE="1", SPB_SCRATCH=str(Path(tmp) / "scratch"),
                   PYTHONIOENCODING="utf-8",
                   # NO CREDENTIAL VAULT, as on Vercel. The bundle's agent asks the OS
                   # keyring for portal logins when it starts (secrets_store); a search
                   # needs none, and on a laptop that is a read of the owner's vault for
                   # nothing -- and ~3 s of backend detection (measured 7 Oct 2026).
                   PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring")
        measured_path = Path(tmp) / "measured.json"
        cmd = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--in-bundle",
               "--snapshot", str(snapshot), "--db", str(copy), "--bundle", str(bundle),
               "--out", str(measured_path), "--states", ",".join(args.states),
               "--manifest", str(Path(args.manifest).resolve())]
        if args.today:
            cmd += ["--today", args.today]
        done = subprocess.run(cmd, cwd=tmp, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, encoding="utf-8",
                              errors="replace")
        for line in (done.stdout or "").splitlines():
            say(line)
        measured = load_json(measured_path) if done.returncode == 0 else None
        if measured is None:
            say("[FAIL] setup: the measurement did not finish (exit %s)" % done.returncode)
            for line in (done.stderr or "").strip().splitlines()[-8:]:
                say("  " + line)
            return 1
    if SR._tree(bundle) != before:
        say("[FAIL] setup: the check changed the bundle that is about to be deployed")
        return 1
    history = load_history(args.history)
    manifest = load_json(args.manifest) if Path(args.manifest).is_file() else None
    acknowledged = (load_json(args.acknowledged)
                    if args.acknowledged and Path(args.acknowledged).is_file() else {})
    result = evaluate(measured, history, manifest, nightly=args.nightly,
                      acknowledged=acknowledged or {})
    body = report_body(measured, result)
    body["seconds"] = round(time.monotonic() - started, 1)
    say(body["table"])
    _write(args.report, body)
    if args.nightly:
        published = snapshot.parent / PUBLISHED_NAME
        try:
            _write(published, body)
        except OSError as exc:
            say("  (could not publish the report beside the snapshot: %s)" % exc)
        save_history(args.history, history,
                     history_entry(measured, result["read_summary"]))
        if result["verdict"] == "FAIL":
            _mark_degraded(args.degraded_marker, result)
    say("parity_check: %s, exit %d after %.0f s" % (
        result["verdict"], 0 if result["verdict"] == "PASS" else 1,
        time.monotonic() - started))
    return 0 if result["verdict"] == "PASS" else 1


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # Not 2: run_daily.py reads a step's exit 2 as "a harvest channel returned nothing".
        self.print_usage(sys.stderr)
        print("parity_check: error: %s" % message, file=sys.stderr)
        sys.exit(1)


def parser():
    ap = _Parser(description="Source-to-search parity: does the app show what the sources "
                             "list?")
    ap.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT),
                    help="stock.json built tonight; the bundle is beside it")
    ap.add_argument("--db", help="the database (default SPB_DATABASE_PATH, then the app's); "
                                 "copied read-only, never written")
    ap.add_argument("--bundle", help="the research bundle (default: api/ beside the snapshot)")
    ap.add_argument("--today", help="dd/mm/yyyy (default: today in AEST)")
    ap.add_argument("--states", default=",".join(SEARCHED_STATES),
                    help="states to search (default %s)" % ",".join(SEARCHED_STATES))
    ap.add_argument("--history", default=str(HISTORY))
    ap.add_argument("--report", default=str(REPORT))
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--acknowledged", default=str(ACKNOWLEDGED),
                    help="known data issues reported as notes until a date "
                         "(data/parity_acknowledged.json)")
    ap.add_argument("--nightly", action="store_true",
                    help="run_daily: append tonight to the history, publish the report "
                         "beside the snapshot, and add a line to the DEGRADED marker on a "
                         "failure")
    ap.add_argument("--degraded-marker", default=str(DEGRADED_MARKER), help=argparse.SUPPRESS)
    ap.add_argument("--in-bundle", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--out", help=argparse.SUPPRESS)
    return ap


def main(argv=None, out=None):
    args = parser().parse_args(argv)
    args.states = [s.strip().upper() for s in str(args.states).split(",") if s.strip()]
    unknown = [s for s in args.states if s not in SEARCHED_STATES]
    if unknown:
        print("parity_check: error: not a searched state: %s" % ", ".join(unknown),
              file=sys.stderr)
        return 1
    if args.in_bundle:
        try:
            return measure(args)
        except Exception as exc:                                      # noqa: BLE001
            print("parity: the measurement raised %s: %s" % (type(exc).__name__, exc))
            for line in traceback.format_exc().strip().splitlines()[-8:]:
                print("  " + line)
            return 1
    try:
        return run(args, out)
    except Exception as exc:                                          # noqa: BLE001
        print("[FAIL] setup: parity_check raised %s: %s" % (type(exc).__name__, exc),
              file=out or sys.stdout)
        return 1


if __name__ == "__main__":
    sys.exit(main())
