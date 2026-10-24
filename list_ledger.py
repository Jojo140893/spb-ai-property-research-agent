"""
list_ledger.py -- what each re-listing list carried on the nights it was read, how each
read was judged, and why each lot was retired: KEPT, so a retirement is a decision made
once on evidence instead of a guess re-made every night.

WHY (24 Oct 2026). Ahsan: "if the stock is retired in the portals it should retire in our
agent too". A lot that leaves a builder's list must stop being live: not searched, not
recommended by Clare or Agent 2, not counted as held on the Settings tab. Two things stopped
it, and both were about memory, not evidence:

  * THE NIGHTLY RESET. supersede_duplicates cleared every superseded_by mark before
    repair_rows ran, so a retirement held only while its list still qualified that night.
    Proxima read nothing on 11 runs (12-23 Oct) and about 280 proven retirements were
    undone on each of them. The mark now persists (supersede_duplicates.PERSISTENT_MARKS)
    and the evidence each retirement was made on is kept here (table `retirements`).

  * THE RATCHET. repair_rows.partial_reads calls a list a failed read when 40% of its
    stored rows were not on its newest read, measured over EVERY row ever stored for it.
    Rows the source itself says are gone, older identities of lots tonight's read still
    carries, and genuinely sold lots all count as "missing", so once a list drifts past
    40% nothing on it retires again and the share only rises (Hermitage 50-56% on every
    run since 9 Oct; Verv 217 of 306 while its tab has 90 rows and the read gives 89).

THE SAFETY RULE IS UNCHANGED, AND IT IS THE POINT. A failed, cut-short or half-rendered
read must never be taken as "everything sold": a portal returning 1 row of 29 is
structurally identical to one whose stock genuinely sold, and the data alone cannot tell
them apart (tests/test_withdrawn_from_source.py:194; WORKLOG section 4, "Fix the read, not
the guard"). So a read counts as WHOLE only on evidence, judged in this order -- the first
rule that matches decides (judge()):

  a) VETOES. The channel's own record of tonight's read (data/harvest_manifest.json) says
     it was short, capped, half-fetched or refused: PARTIAL, whatever the counts say.
  b) PROOFS. The channel's own count says the read was whole: Proxima's whole/stated, a
     Google tab's export CSV row count with its rows accounted for, the E-Agent Stocklist
     search reading every record the site states, two channels agreeing on the same page
     (on a count that is the page's, leaning on an E-Agent read judged whole tonight:
     TORSION_AGREE, Tonight.leaning_doubt), a person's --accept. A proof that tolerates
     unaccounted rows (an export's slack, an old Proxima counter, the Stocklist's mapping)
     stands past a step (c) only while what it leaves unaccounted for grew by less than a
     step (slack_short).
  c) THE STEP GUARD. Tonight's accountable rows (see e) fell, since the list's last WHOLE
     read (its first recorded read, when none was whole; never pruned), by at least
     max(2, 10%) of what that read carried -- 2 rows, counting rows that say gone when a
     lot for sale dropped with them, on a list the old rule holds partial (THE STRICT
     BAR) -- PARTIAL until a proof (STEP_MIN_ROWS). A cut the manifest says nothing about
     cannot walk a baseline down. A two-channel agreement whose agreed count fell by such
     a step proves nothing (TORSION_AGREE).
  d) HISTORY (E-Agent price lists only). Tonight's read is no smaller than 60% of the
     largest of the list's last 7 WHOLE reads, and no smaller than half the largest WHOLE
     read of the last 28 days (a staircase of drops just under 40% cannot walk it down).
  e) THE ANCHOR, when there is no history and no proof: today's 40% rule, with the
     yardstick fixed -- measured over ACCOUNTABLE rows, the members minus rows whose own
     status says gone and minus older identities matched one-for-one to a row on tonight's
     read. The 10-row floor is tested on the FULL member count.

A PARTIAL read is recorded but never enters any baseline, however many nights it repeats:
repeating a bad read never makes it the yardstick. A RECORDED verdict stands: it is judged
again only when tonight's run made the read and tonight's manifest has a line for its list,
and a recorded veto is never judged again at all (review, 24 Oct 2026: a project unreadable
the next night had no line, and the bare anchor rewrote its veto as whole). Proxima, the
direct portals and the E-Agent Stocklist can never be whole on count history at all -- a
capped read repeats the same count night after night -- so they need (b), or fall to (e).

What retires a lot is unchanged: absence from its own list for WITHDRAWN_AFTER_DAYS (14) on
a list read within SOURCE_READ_WITHIN_DAYS (3) and judged whole. On top of that a lot needs
MIN_WHOLE_READS_ABSENT whole reads after its last sighting, and a retirement the old rule
would not have made waits out CALIBRATION_NIGHTS and a nightly cap (repair_rows).

Never holds a link: lists are keyed by sha16 of their grouping key and labelled by channel,
builder, host and project. The tables live in spb_research_audit.db, so state_sync's
whole-file push carries them between runners, and they are created only under --apply.
"""

import collections
import hashlib
import json
import os
import re
import sqlite3
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from database import HASH_RECIPE_VERSION, WITHDRAWN_FROM_SOURCE
from harvest_manifest import old_counter_gap_ok

# ------------------------------------------------------------------ the constants

# The same bar as api/_candidates.FULL_READ_SHARE (0.60 over the last 7 read days), which
# is 1 - repair_rows.PARTIAL_READ_SHARE / 100: tonight's read must carry at least 60% of
# the largest of the list's last FULL_READ_RUNS whole reads. Over the 19 nightly logs of
# 9-24 Oct every whole document read sat above it; it trips on failures.
FULL_READ_SHARE = 0.60
FULL_READ_RUNS = 7
# The drift floor: never below half the largest whole read of the last DRIFT_WINDOW_DAYS,
# so 100, then 62, then 39 does not walk the baseline down by steps just under 40%.
DRIFT_FLOOR = 0.50
DRIFT_WINDOW_DAYS = 28
# THE STEP GUARD (second review, 24 Oct 2026). The ledger trusts lists the old rule gave up
# on -- by history, or by the anchor over accountable rows -- and on those a cut that the
# manifest says nothing about walked straight through: a silent cut keeping 70% of a
# 114-row list read "history 81 of 114", and FULL_READ_RUNS nights later "81 of 81"; a
# 10-row list cut to 7 fell under the 10-row floor and was "not judged by a share"; a
# Proxima project with no counter was 30% missing, under the anchor's 40%. On the 30 Sep
# copy that retired 46-68 lots still for sale, and main, which holds those lists partial,
# none. So a read whose accountable rows fell, since the list's last WHOLE read, by at
# least max(STEP_MIN_ROWS, STEP_SHARE of what that read and tonight's carried) is PARTIAL
# ("dropped N ... since its last whole read") and stays so -- it never enters a baseline,
# and the lots it dropped are counted every night -- until a proof or a person's --accept,
# the lots coming back, or the old rule retiring them. A lot leaving a list on its own is
# the sale signal; two or more of its for-sale lots at once (Luxton's 10-row tab cut to 7,
# one of the three "Not Available": 2 of 9), or a tenth of a big list, waits for evidence.
# Measured on 30 Sep (40 days, 439 lists): 139 drop events, 38 of them on 29 lists at
# max(2, 10%), 33 on 25 at max(3, 10%); over the nightly logs of 9-24 Oct, 3 net drops of
# that size in 2,322 night-over-night reads. It is no veto: what the old rule retires on
# such a list still goes (main parity), so it holds only what the ledger would retire
# beyond main.
STEP_MIN_ROWS = 2
STEP_SHARE = 0.10
# THE STRICT BAR ON A LIST MAIN HOLDS PARTIAL (third review, 25 Oct 2026). On a list the old
# rule holds partial tonight -- the ratchet: 40%+ of everything stored missing, or (a pooled
# portal list) rows of a group whose newest read is stale -- main retires NOTHING, so every
# retirement rule A makes there is beyond main, and max(2, 10%) still let a silent cut
# through: a 90% cut drops floor(10% of n) rows, one short of ceil(10% of n) unless n is a
# multiple of ten (30 Sep: 42 lots still for sale retired on night 17, Tomorrow Homes 10 of
# 114, Proxima 724 8), and a cut row whose own status says gone was not counted at all
# (Luxton's 10-row tab cut to 8: one Available lot and one "not available", 1 of 9). There
# (Ledger.judge's `strict`) a step is STEP_MIN_ROWS rows, whatever the list's size, and a
# row whose status says gone counts toward it whenever a row for sale dropped with it. One
# lot leaving still retires -- the sale signal the ratchet fix exists for -- and a read
# that drops only rows the source itself already called gone is no step (nothing for sale
# is at stake; Proxima stops showing "Not Available" lots). The same bar holds a
# two-channel agreement's count, and an export CSV's slack, on such a list.
# The cost, measured on the 30 Sep copy over its last 40 days: 31 of the 235 lists judged
# are lists main holds partial; of their 144 drop events 28 were already a step, 33 were
# one lot for sale leaving (they still retire), and 57 more (367 rows: AVIA 13, Hermitage's
# pooled portal 10, Silkwood 10, Tomorrow 7, Verv 5, G Developments 5, ...) now wait for a
# proof or a person's --accept -- a Google tab's export still proves a drop its own rows
# fell with. Main retires none of them. (26 Sep copy: 20 such lists, 73 events, 31 more.)
# A retirement needs this many WHOLE reads, on distinct nights after the lot's last
# sighting, that did not carry it: one fluke whole read is never enough.
MIN_WHOLE_READS_ABSENT = 2
# A Google tab is read whole when the listings stored from it tonight account for its
# export's CSV rows to within max(3, 15%) -- the header, notes and section titles. On the
# runs of 9-24 Oct Verv 89/90, Tomorrow 117/123, Silkwood 70/80, Havenwood 22/25 and Luxton
# 10/12 pass; AVIA 153/240 and Invision 15/29 do not.
CSV_SLACK_ROWS = 3
CSV_SLACK_SHARE = 0.15
# A manifest older than this did not describe tonight's harvest.
MANIFEST_MAX_AGE_HOURS = 20
# WHEN TONIGHT'S RUN BEGAN (review, 24 Oct 2026). The dates tonight's reads carry run from
# the run's start to the manifest's writing, so a run that crosses midnight holds reads of
# two dates and every other run holds one. It used to be guessed from the hour the manifest
# was written (before 06:00 meant "yesterday's date too"), which put yesterday's reads in
# front of tonight's manifest: a project unreadable tonight has no line in it, so its
# recorded veto ("proxima short 15/20") was re-judged by the bare anchor and overwritten as
# whole. The start is the manifest's own "started" (harvest_manifest.reset), else the
# earliest checked_at stamped within RUN_MAX_HOURS before the manifest was written (a
# harvest takes about an hour; the job's timeout is 150 minutes), else the manifest's own
# midnight. Never later than the run's first stamp: a read of tonight's taken for
# yesterday's would be judged without its manifest line.
RUN_MAX_HOURS = 12
RUN_SLACK_MINUTES = 5
# ONE READ DATE PER RUN (main's fix-proxima-day, merged 24 Oct 2026). Every row a harvest
# reads now carries the day the run STARTED (sources.base.one_read_date), whatever the
# clock says when the row is read, and the manifest names that day ("read_date", written by
# harvest_manifest.reset inside the same block). That date is always one of tonight's read
# dates, beside the span from the run's start to the manifest's writing: reset() runs a
# moment after the block opens, so a run whose block opened at 23:59:59 has started=00:00
# the next day while every row it reads carries the day before -- without the stated date
# those reads would be taken for yesterday's and judged without their manifest lines. The
# run is also ONE NIGHT to the ledger, named by that date (Tonight.night), and rule B
# measures absence from it, as rule A always has from the list's own read date. Only a
# stated date within RUN_DATE_MAX_DAYS of the manifest's writing names the night.
RUN_DATE_MAX_DAYS = 1
# Retirements the old rule would not have made are printed as "would retire", and not
# written, until the ledger holds this many recorded nights.
CALIBRATION_NIGHTS = 7
# How many shown lots one channel may retire in a night beyond what the old rule would:
# max(NIGHTLY_CAP_MIN, NIGHTLY_CAP_SHARE of the channel's live rows). A retirement now
# persists, so a fault that retired too much would no longer undo itself the next night.
NIGHTLY_CAP_MIN = 25
NIGHTLY_CAP_SHARE = 0.05
# The direct portals' grid page length (sources/builder_portals sets the select to 100): a
# read that ends exactly on a multiple of it is a page-length signature, like Proxima's
# render cap, and proves nothing.
PORTAL_PAGE_LENGTH = 100
# Proxima's index health: on-index project entries below (the 7-night median - this), or
# all project entries below PROXIMA_INDEX_SHARE of their median, is a narrowed index (the
# session-filter trap) and makes every Proxima read that night partial.
PROXIMA_INDEX_SLACK = 2
PROXIMA_INDEX_SHARE = 0.90
HEALTH_WINDOW_NIGHTS = 7
HEALTH_MIN_NIGHTS = 3
# A channel's night is healthy (channel_health) when it offered at least this share of its
# median number of units, and every page present on 5 of the last 7 nights is there
# tonight with at least PAGE_LINK_SHARE of its median link count.
HEALTHY_SHARE = 0.90
PAGE_PRESENT_NIGHTS = 5
PAGE_LINK_SHARE = 0.80
# The anchor's 10-row floor (repair_rows.PARTIAL_READ_MIN_ROWS) is tested on the FULL
# member count, so setting rows aside can never drop a big list under it: 20 repair-written
# "Sold" rows plus 9 blank ones, 1 of them read, is still 8 of 9 missing, a failed read.
ANCHOR_FLOOR_ON_MEMBERS = True
# Two reads of the same page through two channels agree when their counts are this close.
# AGREEMENT IS NOT WHOLENESS (second review, 24 Oct 2026): when the page paginates or
# lazy-loads and neither reader follows it, both read page 1 and agree exactly (24 of
# 120). So an agreed count proves a read whole only when it carries FULL_READ_SHARE of the
# lots stored from that page through either channel (live, or hidden as a duplicate of
# another channel's copy -- Torsion's E-Agent copies mostly are), and of the list's own
# recent whole reads (each counted by its agreed portal count where it was an agreement),
# and DRIFT_FLOOR of its whole reads of the last DRIFT_WINDOW_DAYS, and it is no STEP down
# (STEP_MIN_ROWS) from the list's last whole read's count (repair, 25 Oct 2026: a page that
# stops loading at 84 of 120 passes every share above); else it proves nothing
# and the step guard and the anchor decide. On 30 Sep the portal stored each Torsion card
# under its own brochure link, so no portal row names the page: the page's E-Agent rows and
# the list's history are what hold an agreed count to account.
TORSION_AGREE = 2
_AGREED = re.compile(r"two channels agree: portal (\d+)")
# list_reads, source_offers and channel_nights older than this are pruned; retirements
# are kept for good.
PRUNE_NIGHTS = 120
# Where Proxima's accordion stops rendering; api/_candidates.PROXIMA_RENDER_CAPS is the
# source of truth and a test pins the two together.
RENDER_CAPS_FALLBACK = (80, 83, 160)

# Channels whose count history can never show a read whole on its own.
PORTAL = "Direct Builder Portal (live)"
PROOF_REQUIRED = frozenset({"Proxima", PORTAL, "E-Agent Stocklist"})
# RealNet's Google Drive folder (main's realnet-wa-drive, merged 24 Oct 2026) joined
# repair_rows.RELISTING_CHANNELS: the folder is one list (every row's source_url is the
# folder), and a package whose file leaves it retires after WITHDRAWN_AFTER_DAYS. But a
# package whose file is STILL in the folder drops out of the read in ways that are not a
# sale: its download failed (HTTP error, Drive answering with a page, over 30 MB: "fetch
# failed"), its brochure does not print the price its name states, so the reader withholds
# it rather than list a price a buyer may be held to ("zero-yield" -- The Spires Estate,
# Baldivis, on 24 Oct), the PDF would not open, a constraint refused it, or a rename left a
# name the reader cannot parse ("no price list"). Under the 40% rule alone one package
# failing that way every night retires after a fortnight: one file in 42 is far under 40%.
#
# The folder's own listing says which files are still there, so the guard is PER PACKAGE
# (Tonight.realnet_hold, applied by repair_rows to the old rule and to rule A alike): a
# RealNet row retires only on a night whose manifest line for its folder was read clean
# (outcome read, state ok) and lists the folder's files ("files_listed", each Drive id
# hashed), and its own file -- the id in its listing_url -- is not among them. Every other
# night holds every package: no manifest, no line for the folder, a listing that failed, a
# line from before files_listed. A file that failed holds its own package and not the
# folder, because the real folder fails a file every night (24 Oct: the Echo Beach brochure,
# which prints no price beside the price list that does, and The Spires) and a folder held
# for it would never retire a package that really left. A read tonight's manifest says
# nothing of is vetoed (judge), and so is a folder read that was not clean (_realnet).
# There is no PROOF: a clean read is whole only against the listing Drive returned, so the
# 40% anchor stays the backstop. Rule B never counts it: the folder is one unit, and a
# package that leaves it is rule A's.
REALNET = "RealNet Stocklist"
CLEAN_READ_REQUIRED = frozenset({REALNET})
# Drive's id of a package's file inside its row's listing_url (sources/realnet_drive
# FILE_URL, ".../file/d/<id>/view"; a test pins the two together).
_DRIVE_FILE = re.compile(r"/file/d/([\w-]+)")
# A link or piece outcome that means the read of that link failed, in part or in whole.
FAILED_OUTCOMES = frozenset({"fetch failed", "empty folder", "private link", "crashed",
                             "login failed", "no credentials"})

WHOLE, PARTIAL = "whole", "partial"
ACCEPTED = "accepted by a person"
VETO = "veto: "

RULE_ENV = "SPB_RETIREMENT_RULE"
RULE_ABSENT = "list re-read without it"
RULE_PARITY = "list re-read without it (the old rule)"
RULE_UNOFFERED = "source stopped offering it"
RULE_NOT_SERVED = "file no longer served"

TABLES = ("list_reads", "retirements", "source_offers", "channel_nights")
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS list_reads (
        list_key TEXT, read_date TEXT, channel TEXT, label TEXT,
        rows_seen INTEGER, members INTEGER, accountable INTEGER, baseline INTEGER,
        verdict TEXT, basis TEXT, recorded_at TEXT,
        PRIMARY KEY (list_key, read_date))""",
    """CREATE TABLE IF NOT EXISTS retirements (
        row_id INTEGER, stamp TEXT, rule TEXT, list_key TEXT, read_date TEXT,
        silent_days INTEGER, basis TEXT, status_before TEXT, undone_at TEXT,
        undone_by TEXT)""",
    """CREATE TABLE IF NOT EXISTS source_offers (
        night TEXT, channel TEXT, unit TEXT, page TEXT, heading TEXT, label TEXT,
        builder TEXT, outcome TEXT,
        PRIMARY KEY (night, channel, unit))""",
    """CREATE TABLE IF NOT EXISTS channel_nights (
        night TEXT, channel TEXT, units INTEGER, pages_json TEXT,
        failed_builders_json TEXT, healthy INTEGER, why TEXT,
        PRIMARY KEY (night, channel))""",
)


def step_need(size, strict=False):
    """The fewest rows a drop must lose to be a step (STEP_MIN_ROWS): max(STEP_MIN_ROWS,
    STEP_SHARE of `size`, rounded up -- 10% of 30 is 3, not 3.0000000000000004); on a list
    the old rule holds partial tonight (`strict`, THE STRICT BAR), STEP_MIN_ROWS."""
    if strict:
        return STEP_MIN_ROWS
    return max(STEP_MIN_ROWS, -(-int(size) * round(STEP_SHARE * 100) // 100))


# A proof that tolerates unaccounted rows -- a Google tab's export CSV (CSV_SLACK_ROWS), the
# E-Agent Stocklist search's stated total against the listings mapped from it, a Proxima
# read under an old counter -- writes its two counts in its basis: "N listing(s) for M
# row(s)" / "record(s)", "proxima uncapped N/M". Past a step (judge),
# such a proof stands only while the rows it leaves unaccounted for grew by less than a step
# since the read the step is measured from (third review, 25 Oct 2026: a parser dropping
# 12 of Tomorrow Homes' 117 lots read "export csv: 105 listing(s) for 123 row(s)", inside
# max(3, 15%), and the 12 retired while still for sale).
_SLACK = re.compile(r"(\d+) listing\(s\) for (\d+) (?:row|record)\(s\)")
# Proxima's own counter tolerates a gap too: a read proven whole under an OLD stored
# counter (harvest_manifest.old_counter_gap_ok: up to max(3, 10%) under it) is
# "proxima uncapped READ/COUNTER".
_COUNTER = re.compile(r"proxima uncapped (\d+)/(\d+)")


def _slack_counts(text):
    """(listings, rows the proof says it accounts for) from a proof's basis, or None."""
    text = str(text or "")
    m = _SLACK.search(text) or _COUNTER.search(text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def slack_short(proof, step):
    """Why a proof that tolerates unaccounted rows does not cover tonight's step, or None
    (None also for every other proof, and when there is no step)."""
    got = _slack_counts(proof)
    if got is None or not step:
        return None
    grew = got[1] - got[0]
    ref = _slack_counts(step.get("basis"))
    if ref:
        grew -= max(0, ref[1] - ref[0])
    if grew < step["need"]:
        return None
    return ("%s, %d more unaccounted for than on %s: it does not account for the drop"
            % (proof, grew, _iso(step["last"])))


def rule_is_old(environ=None):
    """SPB_RETIREMENT_RULE=old: the rule as it stood on 24 Oct 2026, and nothing recorded."""
    env = os.environ if environ is None else environ
    return str(env.get(RULE_ENV, "") or "").strip().lower() == "old"


# ------------------------------------------------------------------ small helpers

def _as_date(value):
    """dd/mm/YYYY (the buildings table) as a datetime at midnight, or None."""
    try:
        d, m, y = str(value or "").split("/")
        return datetime(int(y), int(m), int(d))
    except Exception:                                      # noqa: BLE001
        return None


def _iso(d):
    return d.strftime("%Y-%m-%d")


def _from_iso(value):
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _ddmmyyyy(d):
    return d.strftime("%d/%m/%Y")


def _day(d):
    return datetime(d.year, d.month, d.day)


def _checked(row):
    """The row's checked_at (record_building stamps it on every read) as a naive datetime,
    or None."""
    s = str(_col(row, "checked_at") or "").strip()[:19].replace(" ", "T")
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def sha16(text):
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:16]


def _col(row, name):
    try:
        return row[name]
    except (KeyError, IndexError):
        return None


def list_key(key):
    """The ledger's name for a repair_rows._by_document key: never the link itself."""
    return sha16(json.dumps(list(key)))


def _host(text):
    text = str(text or "")
    if "://" not in text:
        return ""
    try:
        return (urlsplit(text).netloc or "").lower()
    except ValueError:
        return ""


def label(key, members):
    """What a person calls a list in the log: channel, builder, host, project. No link."""
    r = members[0] if members else {}
    host = _host(key[0]) or ("pooled" if "|" in str(key[0]) else "")
    return " | ".join(x for x in (str(_col(r, "source_channel") or ""),
                                  str(_col(r, "builder_name") or "(no builder)"), host,
                                  ("project %s" % key[1]) if key[1] else "") if x)


def main_builder(rows):
    """The builder most of these rows name (normalised), or '' when none does."""
    c = collections.Counter(_norm(_col(r, "builder_name")) for r in rows)
    c.pop("", None)
    return c.most_common(1)[0][0] if c else ""


def _is_hash_mark(mark):
    return str(mark or "").startswith(HASH_RECIPE_VERSION + ":")


_CAPS = []


def render_caps():
    """api/_candidates.PROXIMA_RENDER_CAPS (read once), or the pinned fallback."""
    if not _CAPS:
        try:
            import sys
            api = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api")
            if api not in sys.path:
                sys.path.insert(0, api)
            from _candidates import PROXIMA_RENDER_CAPS
            _CAPS.append(tuple(PROXIMA_RENDER_CAPS))
        except Exception:                                  # noqa: BLE001
            _CAPS.append(RENDER_CAPS_FALLBACK)
    return _CAPS[0]


_FILE_KEY = {}


def file_key_fn():
    """sources.e_agent.stocklist_file_key (a busy file: imported, never edited), or None.
    Without it two spellings of one link cannot be told apart, so the rules that need it
    (rule B on E-Agent) are off for the night: they fail closed."""
    if "fn" not in _FILE_KEY:
        try:
            from sources.e_agent import stocklist_file_key
            _FILE_KEY["fn"] = stocklist_file_key
        except Exception:                                  # noqa: BLE001
            _FILE_KEY["fn"] = None
    return _FILE_KEY["fn"]


# ------------------------------------------------------------------ the tables

def tables_exist(conn):
    names = {n for (n,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    return all(t in names for t in TABLES)


def ensure_tables(conn):
    for sql in _SCHEMA:
        conn.execute(sql)


# ------------------------------------------------------------------ one lot, its street

_LOT_WORD = re.compile(r"\blot\s*[\w/-]*\d[\w/-]*", re.I)
_NOT_LETTERS = re.compile(r"[^a-z]+")


def _street(addr):
    """'Lot 12 Example Street' -> 'example street'. Numbers and the lot word go."""
    s = _LOT_WORD.sub(" ", str(addr or "").lower())
    return " ".join(_NOT_LETTERS.sub(" ", s).split())


def _norm(v):
    return " ".join(str(v or "").lower().split())


def lot_identity(row):
    """(channel, builder, project, lot_number) or None, and the places that can confirm a
    match. A row naming neither a builder nor a portal project is too weak to match: a
    Proxima project whose developer the index never stated (off-index projects name none)
    still scopes its own lots, but a pooled E-Agent list names nothing at all."""
    lot = _norm(_col(row, "lot_number"))
    builder = _norm(_col(row, "builder_name"))
    project = _norm(_col(row, "source_project_id"))
    if not lot or not (builder or project):
        return None, ()
    places = tuple(p for p in (("estate", _norm(_col(row, "estate_name"))),
                               ("suburb", _norm(_col(row, "suburb"))),
                               ("street", _street(_col(row, "lot_address")))) if p[1])
    return (str(_col(row, "source_channel") or ""), builder, project, lot), places


def same_lot(a, b):
    ka, pa = lot_identity(a)
    kb, pb = lot_identity(b)
    return bool(ka) and ka == kb and bool(set(pa) & set(pb))


def newer_identity(old, new):
    """True when `new` can be `old` under a new identity: the same lot (same_lot), first
    seen AFTER `old` was last seen. A row listed beside it on the same reads is another
    package of the same lot -- a second design, say -- not its new identity (third review,
    25 Oct 2026: a read that silently dropped design B of lots 11-20 had those 10 rows
    matched to their own design-A siblings, set aside from every count and hidden as
    "older identity, not sold", while still for sale). A row whose dates cannot be read
    is no match: it fails closed."""
    if not same_lot(old, new):
        return False
    first = _as_date(_col(new, "first_seen"))
    last = _as_date(_col(old, "date_checked"))
    return first is not None and last is not None and first > last


def older_identities(missing, on_read):
    """{missing row id: the on-read row it is an older identity of}, matched ONE-FOR-ONE:
    same builder and lot_number, and the same estate, suburb or street, first seen after
    the missing row was last seen (newer_identity). Each row on the read absorbs at most
    one missing row, most recently seen first."""
    pool = collections.defaultdict(list)
    for r in on_read:
        k, _p = lot_identity(r)
        if k:
            pool[k].append(r)
    used, out = set(), {}
    for m in sorted(missing, key=lambda r: _as_date(_col(r, "date_checked")) or datetime.min,
                    reverse=True):
        k, _p = lot_identity(m)
        for r in pool.get(k, ()):
            if r["id"] not in used and newer_identity(m, r):
                used.add(r["id"])
                out[m["id"]] = r
                break
    return out


def accountable(members, newest, is_gone):
    """(accountable rows, gone-status ids, older-identity ids): the anchor's yardstick."""
    on_read = [r for r in members if _as_date(_col(r, "date_checked")) == newest]
    gone = {r["id"] for r in members if is_gone(r)}
    missing = [r for r in members if r["id"] not in gone
               and (_as_date(_col(r, "date_checked")) or newest) < newest]
    older = set(older_identities(missing, on_read))
    return [r for r in members if r["id"] not in gone and r["id"] not in older], gone, older


# ------------------------------------------------------------------ the manifest

MANIFEST = Path(__file__).resolve().parent / "data" / "harvest_manifest.json"


def read_manifest(path=None, now=None):
    """Tonight's harvest manifest as a dict, or None when there is none, it cannot be read,
    or it was not written within MANIFEST_MAX_AGE_HOURS of `now`. Never raises."""
    path = Path(path or MANIFEST)
    now = now or datetime.now()
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
        made = datetime.strptime(str(m.get("generated", "")), "%d/%m/%Y %H:%M")
    except Exception:                                      # noqa: BLE001
        return None
    if not isinstance(m, dict) or abs((now - made).total_seconds()) > \
            MANIFEST_MAX_AGE_HOURS * 3600:
        return None
    m["_made"] = made
    return m


def run_started(manifest, rows, made):
    """When tonight's harvest began (see RUN_MAX_HOURS): the manifest's own "started",
    else the earliest checked_at stamped within RUN_MAX_HOURS before it was written, else
    its own midnight."""
    hi = made + timedelta(minutes=RUN_SLACK_MINUTES)
    try:
        said = datetime.strptime(str((manifest or {}).get("started") or ""), "%d/%m/%Y %H:%M")
    except ValueError:
        said = None
    if said is not None and said <= hi:
        return said
    lo, first = made - timedelta(hours=RUN_MAX_HOURS), None
    for r in rows:
        t = _checked(r)
        if t is not None and lo <= t <= hi and (first is None or t < first):
            first = t
    return first or _day(made)


# A ROW'S LINK IS THE ONE ITS DOCUMENT WAS LAST READ THROUGH (review, 24 Oct 2026). E-Agent
# gives a file a new link when it is re-uploaded, and until this branch record_building
# kept the link a row was first stored under: on the 30 Sep database 111 documents were fed
# by more than one link, and 1,666 live E-Agent rows named a link other than the one their
# document was last read through. Keyed on that, a lot carried onto a re-upload whose new
# link then failed looked "no longer offered" and retired, and a veto on the new link (a
# lot refused) never reached its document. A row keeps its FIRST stocklist_file (merged with
# main 1858d55: main's per-file state votes rest on it), and the link its latest read came
# through travels beside it in last_stocklist_file (database.BUILDINGS_EXTRA_COLUMNS) --
# read_link. A document's rows are resolved through the link its most recently read row
# came through; rows stored before last_stocklist_file existed fall back to their first
# link, to the document's own url where that is the link, and to tonight's manifest, which
# names the documents each link served ("docs", Tonight.link_entries).
def read_link(row):
    """The link a row's latest read came through: last_stocklist_file, else the link it was
    first stored under (a row stored before the column existed)."""
    return (str(_col(row, "last_stocklist_file") or "").strip()
            or str(_col(row, "stocklist_file") or "").strip())


def current_links(rows):
    """{document url: the stock link its most recently read E-Agent row came through}."""
    best = {}
    for r in rows:
        if str(_col(r, "source_channel") or "") != "E-Agent":
            continue
        doc = str(_col(r, "source_url") or "").strip()
        sf = read_link(r)
        if not doc or not sf:
            continue
        rank = (_as_date(_col(r, "date_checked")) or datetime.min,
                _checked(r) or datetime.min, int(_col(r, "id") or 0))
        if doc not in best or rank > best[doc][0]:
            best[doc] = (rank, sf)
    return {d: sf for d, (_rank, sf) in best.items()}


def row_link(row, doc_link=None):
    """The stock link a row belongs to: its document's (current_links), else its own."""
    doc = str(_col(row, "source_url") or "").strip()
    return (doc_link or {}).get(doc) or read_link(row) or doc


def links_of(rows, doc_link=None):
    """Every link these rows could have been read through: each row's latest and first,
    its document's, and the document itself, where it is its own link (a direct PDF or
    sheet link). A veto, note or failure on any of them reaches the list.
    Tonight.link_entries adds the links tonight's manifest says served the rows' documents
    ("docs")."""
    out = set()
    for r in rows:
        out.add(read_link(r))
        out.add(str(_col(r, "stocklist_file") or "").strip())
        out.add(row_link(r, doc_link))
        out.add(str(_col(r, "source_url") or "").strip())
    return sorted(out - {""})


class Tonight:
    """What tonight's manifest says about each read, for judge(): vetoes and proofs.

    A list is judged with the manifest only when its newest read was made by tonight's run
    (read_tonight: on one of the run's dates, stamped at or after it began), and a recorded
    verdict is re-judged only when the manifest has a line for that list (has_line)."""

    def __init__(self, manifest, rows, ledger=None):
        self.m = manifest or {}
        self.ledger = ledger
        # Why tonight's two-channel agreement on a list proved nothing, by list key.
        self.doubts = {}
        made = self.m.get("_made") or datetime.now()
        # The run's own read date (RUN_DATE_MAX_DAYS): the date its rows carry.
        stated = _as_date(self.m.get("read_date"))
        self.run_date = (stated if stated is not None
                         and 0 <= (_day(made) - stated).days <= RUN_DATE_MAX_DAYS else None)
        self.night = _iso(self.run_date or made)
        self.started = run_started(self.m, rows, made)
        dates, d = set(), _day(self.started)
        while d <= made:
            dates.add(_ddmmyyyy(d))
            d += timedelta(days=1)
        if self.run_date is not None:
            dates.add(_ddmmyyyy(self.run_date))
        self.read_dates = dates or {_ddmmyyyy(made)}
        runs = self.m.get("runs")
        self.runs = runs if isinstance(runs, dict) else {}
        self.doc_link = current_links(rows)
        entries = [e for e in (self.m.get("entries") or []) if isinstance(e, dict)]
        self.entries = entries
        self.projects = {str(e.get("pid")): e for e in entries
                         if e.get("kind") == "project" and e.get("channel") == "Proxima"}
        # E-Agent's stock links only: RealNet's folder is a "stock link" line too, and it is
        # neither an E-Agent link (no vetoes, units or page health of E-Agent's) nor keyed
        # like one.
        self.links_by_key, self.links_by_unit = {}, {}
        # And by the documents each link served tonight (harvest_manifest "docs"): a row
        # keeps the link it was first stored under, so this is how a re-upload's own line
        # -- a lot refused, a piece failed -- reaches the rows carried onto it.
        self.links_by_doc = collections.defaultdict(list)
        for e in eagent_links(entries):
            if e.get("stocklist_key"):
                self.links_by_key[str(e["stocklist_key"])] = e
            if e.get("unit"):
                self.links_by_unit[str(e["unit"])] = e
            for d in (e.get("docs") or []):
                self.links_by_doc[str(d)].append(e)
        self.folders = [e for e in entries
                        if e.get("kind") == "stock link" and e.get("channel") == REALNET]
        self.portals = {}
        for e in entries:
            if e.get("kind") == "portal":
                self.portals[(str(e.get("channel") or ""), _norm(e.get("name")))] = e
        self.searches = [e for e in entries if e.get("kind") == "search"]
        docs = self.m.get("documents")
        self.documents = docs if isinstance(docs, dict) else {}
        notes = self.m.get("link_notes")
        self.link_notes = notes if isinstance(notes, dict) else {}
        self.caps = render_caps()
        if ledger is not None and self.night not in ledger.exclude_nights:
            ledger.exclude_nights.add(self.night)
            ledger._channels = None
            ledger.__dict__.pop("_offers", None)
        # Rows stored from each document tonight, whatever supersede made of them since (a
        # duplicate superseded tonight was still on the read); repair's own marks aside.
        self.stored = collections.Counter()
        # E-Agent links read tonight, by host, for the two-channel agreement, and the
        # documents each served tonight (whose lists' verdicts the agreement leans on).
        self.eagent_by_host = collections.defaultdict(list)
        self.eagent_docs = collections.defaultdict(set)
        # Who each stock link belongs to, from the rows it has given (any night): by the
        # link's exact spelling and by its file key, through each row's own link and its
        # document's.
        self._builders = collections.defaultdict(collections.Counter)
        # The lots stored from each page, by the url they were stored from and the link
        # they were read through, per channel: a two-channel agreement is held to its
        # page's own stock. A capture hidden as a duplicate still says the page carried
        # its lot (counted once, by the hash of the copy that won); a retired row does not.
        self.page_rows = collections.defaultdict(lambda: collections.defaultdict(set))
        for r in rows:
            mark = str(_col(r, "superseded_by") or "")
            if mark and not _is_hash_mark(mark):
                continue
            lot = mark or str(_col(r, "content_hash") or "") or "row %s" % _col(r, "id")
            ch = str(_col(r, "source_channel") or "")
            for u in {str(_col(r, "source_url") or "").strip(), read_link(r)} - {""}:
                self.page_rows[u][ch].add(lot)
        fk = file_key_fn()
        for r in rows:
            if str(_col(r, "source_channel") or "") != "E-Agent":
                continue
            for sf in links_of([r], self.doc_link):
                name = str(_col(r, "builder_name") or "").strip()
                self._builders[sha16(sf)][name] += 1
                if fk is not None:
                    self._builders["unit:" + sha16(fk(sf))][name] += 1
        for r in rows:
            if str(_col(r, "date_checked") or "") not in dates:
                continue
            mark = str(_col(r, "superseded_by") or "")
            if mark and not _is_hash_mark(mark):
                continue
            self.stored[str(_col(r, "source_url") or "")] += 1
            if str(_col(r, "source_channel") or "") == "E-Agent":
                sf = read_link(r)
                for host in {_host(_col(r, "source_url")), _host(sf)} - {""}:
                    self.eagent_by_host[host].append(sf)
                doc = (str(_col(r, "source_url") or "").strip()
                       or str(_col(r, "stocklist_file") or "").strip())
                if sf and doc:
                    self.eagent_docs[sf].add(doc)
        self.proxima_unhealthy = proxima_index_health(self, ledger)

    # --- whose read is tonight's ---------------------------------------------------
    def read_tonight(self, members, newest):
        """True when a list's newest read was made by tonight's run: dated on one of the
        run's dates and, where its rows carry checked_at, stamped at or after the run began
        (a run that crossed midnight shares a date with the run before it)."""
        if _ddmmyyyy(newest) not in self.read_dates:
            return False
        stamps = [t for t in (_checked(r) for r in members
                              if _as_date(_col(r, "date_checked")) == newest) if t]
        return not stamps or max(stamps) >= self.started

    def has_line(self, key, members):
        """True when tonight's manifest says anything about this list. A recorded verdict
        is never re-judged on a night that says nothing of its list: without the line no
        veto can fire, and the bare anchor would overwrite one."""
        ch = str(_col(members[0], "source_channel") or "")
        if ch == "E-Agent":
            links = links_of(members, self.doc_link)
            return bool(self.link_entries(members)) or any(
                sha16(sf) in self.link_notes for sf in links) or (
                bool(key[0]) and sha16(key[0]) in self.documents)
        if ch == "Proxima":
            return str(key[1] or "") in self.projects
        if ch == "E-Agent Stocklist":
            return bool(self.searches)
        if ch == REALNET:
            return bool(self.folder_lines(key[0]))
        if ch in PROOF_REQUIRED:
            return self.portal_entry(ch, _col(members[0], "builder_name")) is not None
        return False

    # --- E-Agent ----------------------------------------------------------------
    def link_builder(self, e):
        """The builder a stock link's rows name most often; blank for a pooled list."""
        c = collections.Counter(self._builders.get(str(e.get("stocklist_key") or "")) or {})
        if not c and e.get("unit"):
            c = collections.Counter(self._builders.get("unit:" + str(e["unit"])) or {})
        c.pop("", None)
        return c.most_common(1)[0][0] if c else ""

    def link_entries(self, members):
        """[(link, its line tonight)] for every link these rows could have come through
        (links_of), and every link tonight's manifest says served one of their documents."""
        ents, fk, seen = [], file_key_fn(), set()
        for sf in links_of(members, self.doc_link):
            e = self.links_by_key.get(sha16(sf))
            if e is None and fk is not None:
                e = self.links_by_unit.get(sha16(fk(sf)))
            if e is not None and id(e) not in seen:
                seen.add(id(e))
                ents.append((sf, e))
        for doc in sorted({str(_col(r, "source_url") or "").strip() for r in members} - {""}):
            for e in self.links_by_doc.get(sha16(doc), ()):
                if id(e) not in seen:
                    seen.add(id(e))
                    ents.append(("", e))
        return ents

    def _eagent(self, key, members):
        veto = None
        ents = self.link_entries(members)
        keys = {sha16(sf) for sf in links_of(members, self.doc_link)} | {
            str(e.get("stocklist_key") or "") for _sf, e in ents}
        for k in sorted(keys - {""}):
            note = self.link_notes.get(k) or {}
            if note.get("truncated"):
                return "link truncated (%s)" % (note.get("why") or "a folder budget bound"), \
                    None
        for sf, e in ents:
            failed = [p.get("outcome") for p in (e.get("pieces") or [])
                      if p.get("outcome") in FAILED_OUTCOMES]
            if e.get("outcome") in FAILED_OUTCOMES or failed:
                veto = "link %s" % (failed[0] if failed else e.get("outcome"))
            elif e.get("truncated"):
                veto = "link truncated (a folder budget bound)"
            elif int((e.get("stored") or {}).get("rejected") or 0) > 0:
                veto = "link had %s lot(s) refused by a constraint" % (
                    e["stored"]["rejected"])
            if veto:
                return veto, None
        note = self.documents.get(sha16(key[0])) if key[0] else None
        if isinstance(note, dict) and int(note.get("csv_rows") or 0) > 0:
            rows = int(note["csv_rows"])
            if str(note.get("endpoint") or "") != "export":
                return None, None                  # gviz never proves (see the docstring)
            got = self.stored.get(str(key[0]), 0)
            slack = max(CSV_SLACK_ROWS, int(rows * CSV_SLACK_SHARE))
            if got >= rows - slack:
                return None, "export csv: %d listing(s) for %d row(s)" % (got, rows)
        return None, None

    # --- Proxima ----------------------------------------------------------------
    def _proxima(self, key, members, seen):
        if self.proxima_unhealthy:
            return "proxima index unhealthy (%s)" % self.proxima_unhealthy, None
        e = self.projects.get(str(key[1] or ""))
        if e is None:
            return None, None
        printed = int(e.get("printed") or 0)
        stated = e.get("stated")
        if e.get("short"):
            return "proxima short %s/%s" % (printed, stated if stated is not None else "?"), \
                None
        if not e.get("whole"):
            return "proxima capped, not proven whole (%d)" % printed, None
        if printed in self.caps or seen in self.caps:
            return "proxima read ends on a render cap (%d)" % (printed if printed in self.caps
                                                                else seen), None
        whole = str(e.get("whole"))
        if whole in ("availability view", "price sorts met"):
            return None, "proxima %s %d/%s" % (whole, printed,
                                               stated if stated is not None else "-")
        if whole == "uncapped" and stated is not None:
            try:
                ok = printed >= int(stated) or old_counter_gap_ok(printed, stated)
            except (TypeError, ValueError):
                ok = False
            if ok:
                return None, "proxima uncapped %d/%s" % (printed, stated)
        return None, None                          # "uncapped" with no counter proves nothing

    # --- direct portals ---------------------------------------------------------
    def portal_entry(self, channel, builder):
        return self.portals.get((channel, _norm(builder)))

    def _portal(self, key, members, seen, strict=False):
        ch = str(_col(members[0], "source_channel") or "")
        e = self.portal_entry(ch, _col(members[0], "builder_name"))
        if e is None:
            return None, None
        if str(e.get("outcome") or "") != "read":
            return "portal %s" % (e.get("outcome") or "no outcome"), None
        found, got = int(e.get("cards_found") or 0), int(e.get("cards_read") or 0)
        if got < found:
            return "portal read %d of %d cards" % (got, found), None
        for n in (int(e.get("printed") or 0), got, seen):
            if n and n % PORTAL_PAGE_LENGTH == 0:
                return "portal read ends on its page length (%d)" % n, None
        # No grid total is read, so a clean portal proves nothing by itself -- unless the
        # same page is read through E-Agent tonight, the two counts agree, and the agreed
        # count is the page's (see TORSION_AGREE: both readers can stop at page 1).
        host = str(e.get("host") or "").lower()
        if host and got == found and host in self.eagent_by_host:
            printed = int(e.get("printed") or got)
            doubt = None
            for sf in sorted(set(self.eagent_by_host[host])):
                le = self.links_by_key.get(sha16(sf))
                if le is None or abs(int(le.get("printed") or 0) - printed) > TORSION_AGREE:
                    continue
                doubt = (self.agreement_doubt(key, members, sf, printed, strict)
                         or self.leaning_doubt(sf, printed, strict))
                if doubt is None:
                    return None, "two channels agree: portal %d, e-agent %d" % (
                        printed, int(le.get("printed") or 0))
            if doubt:
                self.doubts[list_key(key)] = doubt
        return None, None

    def leaning_doubt(self, sf, n, strict=False):
        """Why the E-Agent read an agreement on `n` leans on is no evidence tonight, or None.

        AN AGREEMENT IS ONLY AS WHOLE AS THE E-AGENT READ IT LEANS ON (third review, 25 Oct
        2026). The portal stored one link per card and read page 1 (24 of 120) from before
        the ledger began, so the pooled list had no whole read to hold an agreed count to;
        E-Agent's link to the same page read 30 for three nights and then stopped at 24 too.
        The ledger recorded E-Agent's copy PARTIAL ("dropped 6 of 30") and, the same night,
        the portal list WHOLE ("two channels agree: portal 24, e-agent 24"), and 96 lots
        still for sale retired. So an agreement proves nothing unless every E-Agent list read
        through that link tonight is judged WHOLE tonight (repair_rows.list_verdicts judges
        E-Agent lists first) -- and its count is no step (step_need; `strict`, THE STRICT
        BAR, when the old rule holds the pooled list partial) under the most that E-Agent
        list ever read whole since a person last accepted a read of it. Without that, the
        old rule laundered the cut: it retired E-Agent's 6 dropped copies after a fortnight
        (6 of 30 is under its 40%), E-Agent's list read "history 24 of 30" again, and the
        agreement on 24 proved the portal list and retired its 96. A link whose lists were
        not judged -- every copy hidden as a duplicate of the portal's, say -- proves
        nothing either: it fails closed."""
        docs = self.eagent_docs.get(str(sf or "").strip()) or set()
        if not docs or self.ledger is None:
            return ("two channels agree on %d, but no E-Agent list read through that link "
                    "tonight was judged" % n)
        judged = collections.defaultdict(list)
        for v in self.ledger.tonight.values():
            if v.channel == "E-Agent" and v.tonight and str(v.key[0]) in docs:
                judged[str(v.key[0])].append(v)
        for doc in sorted(docs):
            if not judged.get(doc):
                return ("two channels agree on %d, but an E-Agent list it leans on was not "
                        "judged tonight" % n)
            for v in judged[doc]:
                if not v.whole:
                    return ("two channels agree on %d, but the E-Agent list it leans on is "
                            "partial tonight (%s)" % (n, str(v.basis or "")[:80]))
                reads = self.ledger.reads.get(v.list_key, ())
                accepted = [d for d, _s, verdict, b in reads
                            if verdict == WHOLE and str(b).startswith(ACCEPTED)]
                since = max(accepted) if accepted else None
                top = max((s for d, s, verdict, _b in reads if verdict == WHOLE
                           and (since is None or d >= since)), default=0)
                if top - n >= step_need(top, strict):
                    return ("two channels agree on %d, a step of %d or more under the %d "
                            "the E-Agent list it leans on has read whole" % (
                                n, step_need(top, strict), top))
        return None

    def agreement_doubt(self, key, members, sf, n, strict=False):
        """Why `n` lots agreed on by both channels for the page at `sf` are not the page's
        whole stock (see TORSION_AGREE), or None. `strict`: the old rule holds the list
        partial tonight, so a step of the agreed count is STEP_MIN_ROWS (THE STRICT BAR)."""
        page = max((len(lots) for lots in
                    self.page_rows.get(str(sf or "").strip(), {}).values()), default=0)
        if page and n * 100 < round(FULL_READ_SHARE * 100) * page:
            return "two channels agree on %d, under %d%% of the %d lot(s) stored from that " \
                   "page" % (n, round(FULL_READ_SHARE * 100), page)
        dates = [d for d in (_as_date(_col(r, "date_checked")) for r in members) if d]
        if self.ledger is None or not dates:
            return None
        newest = max(dates)
        counts, first = [], None
        for d, seen, verdict, basis in self.ledger.reads.get(list_key(key), ()):
            if d >= newest:
                continue
            m = _AGREED.match(str(basis or ""))
            c = int(m.group(1)) if m else int(seen or 0)
            if verdict == WHOLE:
                counts.append((d, c))
            elif first is None:
                first = (d, c)
        # THE STEP GUARD ON THE AGREED COUNT (repair, 25 Oct 2026). Two readers that both
        # stop where a page stops loading agree on 84 of 120 as readily as on 24, and 84 is
        # over every share below: the page's count fell by a step since its last whole read
        # (its first recorded one, if none was whole), so the agreement proves nothing and
        # the step guard over the list's rows decides. Measured on counts, not rows: the
        # rows a pooled list holds from links the portal no longer uses (Torsion's old
        # per-lot pages) are what an agreement is there to prove gone.
        ref = counts[-1] if counts else first
        if ref is not None:
            need = step_need(ref[1], strict)
            if ref[1] - n >= need:
                return "two channels agree on %d, a step of %d or more down from %d on %s" % (
                    n, need, ref[1], _iso(ref[0]))
        if not counts:
            return None
        base = max(c for _d, c in counts[-FULL_READ_RUNS:])
        window = [c for d, c in counts if (newest - d).days <= DRIFT_WINDOW_DAYS]
        floor = max(window) if window else base
        if n < FULL_READ_SHARE * base:
            return "two channels agree on %d, under %d%% of the list's last whole reads " \
                   "(%d)" % (n, round(FULL_READ_SHARE * 100), base)
        if n < DRIFT_FLOOR * floor:
            return "two channels agree on %d, below the drift floor (the %d-day max %d)" % (
                n, DRIFT_WINDOW_DAYS, floor)
        return None

    # --- E-Agent Stocklist -------------------------------------------------------
    def _search(self):
        """The E-Agent Stocklist search's line: a veto when the reader says it did not end
        clean, a PROOF only when the site stated how many records it holds and the walk
        read them all.

        "OK" IS NOT A COUNT (third review, 25 Oct 2026). sources/eagent_stocklist set
        `truncated` only at its record and page caps, so a walk that ended early -- a page
        fetch that failed, a server that ignored page= and every offset, "the API says 120
        record(s), 60 were read" -- still wrote state ok, short false, and that proved the
        read whole: the 60 lots past the stopping point retired while still for sale. The
        reader now writes the site's stated total ("stated"), the records it read
        ("records") and short whenever it can tell the walk stopped early; a line with no
        stated total proves nothing, and the step guard and the anchor decide. The proof
        carries the listings mapped against the records read, so past a step it is held to
        its own slack like an export CSV (slack_short)."""
        if not self.searches:
            return None, None
        e = self.searches[-1]
        if e.get("state") != "ok" or e.get("short"):
            return "stocklist search %s%s%s" % (
                e.get("state") or "?", ", short" if e.get("short") else "",
                (" (%s)" % e["short_why"]) if e.get("short_why") else ""), None
        try:
            stated = int(e.get("stated") or 0)
            records = int(e.get("records") if e.get("records") is not None
                          else e.get("printed") or 0)
            printed = int(e.get("printed") or 0)
        except (TypeError, ValueError):
            return None, None
        if stated > 0 and records < stated:
            return "stocklist search read %d of the %d record(s) the site states" % (
                records, stated), None
        if stated > 0:
            return None, "stocklist search ok: %d listing(s) for %d record(s), %d stated" % (
                printed, records, stated)
        return None, None

    # --- RealNet's Drive folder (see REALNET) ------------------------------------------
    def folder_lines(self, url):
        """Tonight's RealNet lines for the folder at `url` (by its stocklist_key)."""
        want = sha16(str(url or "").strip()) if str(url or "").strip() else ""
        return [e for e in self.folders if want and str(e.get("stocklist_key") or "") == want]

    def _realnet(self, key):
        """A veto unless tonight's line for this folder says Drive listed it and the reader
        read it (outcome read, state ok). Never a proof. A file that failed is no veto of the
        folder: it holds its own package (realnet_hold)."""
        ents = self.folder_lines(key[0])
        if not ents:
            return "realnet: tonight's manifest has no line for its folder", None
        for e in ents:
            if str(e.get("outcome") or "") != "read" or str(e.get("state") or "") != "ok":
                return "realnet folder %s (%s)" % (e.get("outcome") or "not read",
                                                   e.get("state") or "?"), None
        return None, None

    def realnet_hold(self, row):
        """Why this RealNet package must not retire tonight, or None when tonight's line for
        its folder proves its file has left the folder (see REALNET): read clean, the
        folder's files listed, and this row's own file not among them."""
        ents = self.folder_lines(_col(row, "source_url"))
        if not ents:
            return "tonight's manifest has no line for its folder"
        m = _DRIVE_FILE.search(str(_col(row, "listing_url") or ""))
        if not m:
            return "its file cannot be named"
        mine = sha16(m.group(1))
        for e in ents:
            if str(e.get("outcome") or "") != "read" or str(e.get("state") or "") != "ok":
                return "its folder was not read clean tonight"
            listed = e.get("files_listed")
            if not isinstance(listed, list) or not listed:
                return "tonight's line for its folder does not list the folder's files"
            if mine in {str(x) for x in listed}:
                return "its file is still in the folder"
        return None

    def veto_and_proof(self, key, members, seen, strict=False):
        """(veto text or None, proof text or None) for one list read tonight. `strict`: the
        old rule holds the list partial tonight (THE STRICT BAR)."""
        ch = str(_col(members[0], "source_channel") or "")
        if ch == "E-Agent":
            return self._eagent(key, members)
        if ch == "Proxima":
            return self._proxima(key, members, seen)
        if ch == "E-Agent Stocklist":
            return self._search()
        if ch == REALNET:
            return self._realnet(key)
        if ch in PROOF_REQUIRED:
            return self._portal(key, members, seen, strict)
        return None, None


def proxima_index_health(tonight, ledger):
    """A reason when tonight's Proxima index is narrowed against the recorded nights, else
    None. Unknown (fewer than HEALTH_MIN_NIGHTS recorded) is not unhealthy: it vetoes
    nothing, so the old rule's retirements are never delayed by a young ledger."""
    projects = list(tonight.projects.values())
    hist = ledger.channel_history("Proxima") if ledger is not None else []
    if len([h for h in hist if h["units"] > 0]) < HEALTH_MIN_NIGHTS:
        return None
    hist = _baseline_nights(hist)
    on_now = sum(1 for e in projects if e.get("on_index"))
    on_med = statistics.median(int(h["pages"].get("on_index", 0)) for h in hist)
    all_med = statistics.median(h["units"] for h in hist)
    if on_now < on_med - PROXIMA_INDEX_SLACK:
        return "%d project(s) on the index against a median of %g" % (on_now, on_med)
    if len(projects) < PROXIMA_INDEX_SHARE * all_med:
        return "%d project(s) read against a median of %g" % (len(projects), all_med)
    return None


# ------------------------------------------------------------------ one verdict

class Verdict:
    """How one list's newest read was judged."""

    __slots__ = ("key", "list_key", "newest", "verdict", "basis", "baseline", "seen",
                 "members", "accountable", "missing", "vetoed", "proof", "channel",
                 "label", "recorded", "builder", "tonight", "withdrawn")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def whole(self):
        return self.verdict == WHOLE


# ------------------------------------------------------------------ the ledger

class Ledger:
    """The recorded reads and retirements, plus tonight's verdicts (written by commit()).

    write=False (a report-only run) reads the tables if they exist and creates, writes and
    commits nothing."""

    def __init__(self, conn, today=None, write=False):
        self.conn = conn
        self.today = today or datetime.now()
        self.night = _iso(self.today)
        self.write = bool(write and conn is not None)
        if self.write:
            ensure_tables(conn)
        self.present = conn is not None and tables_exist(conn)
        self.reads = collections.defaultdict(list)   # list_key -> [(date, seen, verdict, basis)]
        self.undone = {}                               # row_id -> iso of the latest person undo
        self.nights = set()                            # recorded nights before tonight (iso)
        self.first_night = None
        self._channels = None
        # Nights whose channel and offer records are tonight's own, never history: the
        # repair's date and, once known, the manifest's (a run can cross midnight).
        self.exclude_nights = {self.night}
        self.tonight = {}
        if self.present:
            self._load()

    @classmethod
    def empty(cls, today=None):
        return cls(None, today)

    def _load(self):
        first = None
        for lk, rd, seen, verdict, basis, rec in self.conn.execute(
                "SELECT list_key, read_date, rows_seen, verdict, basis, recorded_at "
                "FROM list_reads"):
            d = _from_iso(rd)
            if d is None:
                continue
            self.reads[lk].append((d, int(seen or 0), verdict, basis or ""))
            n = str(rec or "")[:10]
            if n:
                first = n if first is None or n < first else first
                if n != self.night:
                    self.nights.add(n)
        for lk in self.reads:
            self.reads[lk].sort()
        self.first_night = _from_iso(first) if first else None
        for rid, at in self.conn.execute(
                "SELECT row_id, MAX(undone_at) FROM retirements WHERE undone_by='person' "
                "GROUP BY row_id"):
            self.undone[rid] = str(at or "")

    # --- what the ledger knows ----------------------------------------------------
    @property
    def nights_recorded(self):
        """Nights recorded before tonight."""
        return len(self.nights)

    @property
    def calibrating(self):
        return self.nights_recorded < CALIBRATION_NIGHTS

    def recorded(self, lk, newest):
        """(verdict, basis) recorded for this list's read of `newest`, or None. A verdict
        noted tonight and not yet written is not "recorded": judging it again judges it
        afresh, the same way, and commit() still writes it."""
        mine = self.tonight.get(lk)
        if mine is not None and mine.newest == newest and not mine.recorded:
            return None
        for d, seen, verdict, basis in self.reads.get(lk, ()):
            if d == newest:
                return verdict, basis
        return None

    def whole_reads_after(self, lk, seen_on):
        start = self.first_night or _day(self.today)
        return sum(1 for d, _n, v, _b in self.reads.get(lk, ())
                   if v == WHOLE and d > seen_on and d >= start)

    def step_reference(self, lk, newest):
        """(date, rows seen, what it is) the step guard measures tonight's read of a list
        against: its last WHOLE read before `newest`, else -- a list never read whole since
        the ledger began, every read of it vetoed or partial -- its FIRST recorded read
        (repair, 25 Oct 2026: a list vetoed on its first nights, then cut silently, had no
        whole read to step down from, and the bare anchor called the cut whole); None when
        the ledger holds no earlier read of it at all."""
        prior = [(d, n) for d, n, _v, _b in self.reads.get(lk, ()) if d < newest]
        whole = [(d, n) for d, n, v, _b in self.reads.get(lk, ()) if v == WHOLE and d < newest]
        if whole:
            return max(whole) + ("its last whole read",)
        if prior:
            return min(prior) + ("its first recorded read (none whole)",)
        return None

    def _basis_of(self, lk, d):
        for rd, _n, _v, b in self.reads.get(lk, ()):
            if rd == d:
                return b
        return ""

    def step(self, lk, acc, newest, gone_rows=(), strict=False):
        """Tonight's step down from the list's last WHOLE read (see STEP_MIN_ROWS;
        step_reference when it has none) as {dropped, live, gone, size, need, last, what,
        basis}, or None. `acc` are the list's accountable rows: an older identity of a lot
        tonight's read carries never counts as dropped, and neither does a row whose own
        status says gone -- except on a list the old rule holds partial (`strict`, THE
        STRICT BAR), where each of `gone_rows` dropped counts whenever an accountable row
        dropped with it, and a step is STEP_MIN_ROWS. Dropped: rows last seen on or after
        that read and not on tonight's."""
        ref = self.step_reference(lk, newest)
        if ref is None:
            return None
        last, _n, what = ref
        on = dropped = gone = 0
        for r in acc:
            d = _as_date(_col(r, "date_checked"))
            if d is None:
                continue
            if d == newest:
                on += 1
            elif last <= d < newest:
                dropped += 1
        if strict and dropped:
            for r in gone_rows:
                d = _as_date(_col(r, "date_checked"))
                if d is not None and last <= d < newest:
                    gone += 1
        need = step_need(on + dropped, strict)
        if dropped + gone < need:
            return None
        return {"dropped": dropped + gone, "live": dropped, "gone": gone,
                "size": on + dropped + gone, "need": need, "last": last, "what": what,
                "strict": strict, "basis": self._basis_of(lk, last)}

    @staticmethod
    def step_text(s):
        if not s:
            return None
        return ("dropped %d of %d since %s (%s)%s: a step of %d or more waits for a proof or "
                "a person's --accept%s" % (
                    s["dropped"], s["size"], s["what"], _iso(s["last"]),
                    (" (%d of them say gone)" % s["gone"]) if s["gone"] else "", s["need"],
                    " (the old rule holds this list partial)" if s["strict"] else ""))

    def step_down(self, lk, acc, newest, gone_rows=(), strict=False):
        """Why tonight's read of a list is a step down from its last WHOLE read (step), or
        None."""
        return self.step_text(self.step(lk, acc, newest, gone_rows, strict))

    def first_read(self, lk):
        """The date of the list's earliest recorded read (tonight's included), or None."""
        reads = self.reads.get(lk, ())
        return min(d for d, _n, _v, _b in reads) if reads else None

    def accepted_after(self, lk, seen_on):
        """True when a person accepted a read of this list dated after `seen_on`."""
        return any(v == WHOLE and str(b).startswith(ACCEPTED) and d > seen_on
                   for d, _n, v, b in self.reads.get(lk, ()))

    def undone_since(self, row_id, seen_on):
        """True when a person undid this row's retirement on or after its last sighting."""
        at = self.undone.get(row_id)
        return bool(at) and seen_on is not None and at[:10] >= _iso(seen_on)

    def stalled(self, lk):
        """Recorded PARTIAL reads in a row, up to the newest."""
        n = 0
        for _d, _s, v, _b in reversed(self.reads.get(lk, ())):
            if v == WHOLE:
                break
            n += 1
        return n

    def channel_history(self, channel):
        """[{night, units, pages, healthy}] for nights before tonight, oldest first."""
        if self._channels is None:
            self._channels = collections.defaultdict(list)
            if self.present:
                for night, ch, units, pages, healthy in self.conn.execute(
                        "SELECT night, channel, units, pages_json, healthy FROM "
                        "channel_nights ORDER BY night"):
                    if night in self.exclude_nights:
                        continue
                    try:
                        pages = json.loads(pages or "{}")
                    except ValueError:
                        pages = {}
                    self._channels[ch].append({"night": night, "units": int(units or 0),
                                               "pages": pages, "healthy": bool(healthy)})
        return self._channels.get(channel, [])

    def offers(self, channel):
        """{unit: [(night, page, heading, label, builder, outcome)]} recorded before
        tonight, oldest first (rule B)."""
        if not hasattr(self, "_offers"):
            self._offers = collections.defaultdict(lambda: collections.defaultdict(list))
            if self.present:
                for night, ch, unit, page, heading, lab, builder, outcome in self.conn.execute(
                        "SELECT night, channel, unit, page, heading, label, builder, outcome "
                        "FROM source_offers ORDER BY night"):
                    if night not in self.exclude_nights:
                        self._offers[ch][unit].append((night, page or "", heading or "",
                                                       lab or "", builder or "",
                                                       outcome or ""))
        return self._offers[channel]

    # --- judging ------------------------------------------------------------------
    def judge(self, key, members, newest, tonight=None, is_gone=lambda r: False,
              partial_share=40, min_rows=10, withdrawn=frozenset(), strict=False):
        """The Verdict on the list's newest read: the first rule that matches decides.

        `strict` (repair_rows.list_verdicts): the old rule retires nothing from this list
        tonight -- it holds it partial, or a group pooled into it is stale -- so a step is
        STEP_MIN_ROWS rows (THE STRICT BAR), on the rows, on a two-channel agreement's count
        and on a proof's slack.

        `withdrawn` (merged 24 Oct 2026 with main's fix-bathla-projects): ids of rows of a
        builder the client withdrew -- Bathla by name, by project or by capture
        (builder_names.is_withdrawn_row). Proxima still reads Bathla's projects whole, so
        its lots sit on lists beside everyone else's. They are SET ASIDE from every count
        that measures the read -- members, the rows on it, the missing, the history -- so
        they can neither tip a list into a failed read nor carry a cut one over the line
        (12 other lots and 10 of Bathla's, 7 of the 12 cut: 7 of 22 is "whole", 7 of 12 is
        not). What the read itself shows is kept whole: its date and stamps, its links, and
        the count a cut signature is read off (a Proxima read that ends on its render cap
        ended there whoever's lots were in it). And they still count toward the list's
        size for the 10-row floor, as gone-status rows do: setting rows aside never makes a
        big list a small one."""
        lk = list_key(key)
        ch = str(_col(members[0], "source_channel") or "")
        read = [r for r in members if _as_date(_col(r, "date_checked")) == newest]
        mine = [r for r in members if _col(r, "id") not in withdrawn]
        seen = sum(1 for r in read if _col(r, "id") not in withdrawn)
        recorded = self.recorded(lk, newest)
        judged_tonight = tonight is not None and tonight.read_tonight(members, newest)
        base = dict(key=key, list_key=lk, newest=newest, seen=seen, members=len(mine),
                    channel=ch, label=label(key, mine or members),
                    builder=main_builder(mine), tonight=judged_tonight,
                    withdrawn=len(members) - len(mine))
        if not mine:
            return Verdict(**base, verdict=PARTIAL, basis="every row on it is a withdrawn "
                           "builder's", vetoed=False, proof=False)
        accepted = bool(recorded) and str(recorded[1]).startswith(ACCEPTED)
        # A RECORDED VERDICT STANDS (review, 24 Oct 2026) unless tonight's run made the read
        # AND tonight's manifest has a line for its list -- and a recorded VETO stands for
        # good: a read the channel's own record called cut is never re-judged whole by a
        # later night that knows less about it.
        if recorded and not accepted and (
                str(recorded[1]).startswith(VETO) or not judged_tonight
                or not tonight.has_line(key, members)):
            v, b = recorded
            return Verdict(**base, verdict=v, basis=b, vetoed=str(b).startswith(VETO),
                           proof=False, recorded=True)
        # a) vetoes. First the count itself, manifest or not: a Proxima read ending on a
        #    render cap, or a portal read ending on its page length, was cut -- that is
        #    main's hole (lots 81-85 behind an 80 cap retired after a fortnight), closed
        #    even on a night the manifest is missing. Then tonight's own record of the read.
        #    A channel that retires only on a clean read (CLEAN_READ_REQUIRED: RealNet's
        #    folder) is vetoed on a read tonight's manifest says nothing of, unless a person
        #    accepted it.
        veto = proof = None
        printed = len(read)                    # the read's own size, withdrawn rows and all
        if ch == "Proxima" and printed in render_caps():
            veto = "proxima read ends on a render cap (%d)" % printed
        elif ch == PORTAL and printed and printed % PORTAL_PAGE_LENGTH == 0:
            veto = "portal read ends on its page length (%d)" % printed
        elif judged_tonight:
            veto, proof = tonight.veto_and_proof(key, members, printed, strict=strict)
        elif ch in CLEAN_READ_REQUIRED and not accepted:
            veto = "%s: no manifest line proves the read clean" % ch.lower()
        if veto:
            return Verdict(**base, verdict=PARTIAL, basis=VETO + veto, vetoed=True,
                           proof=False)
        # b) proofs
        if accepted:
            return Verdict(**base, verdict=WHOLE, basis=recorded[1], vetoed=False,
                           proof=True)
        # The step guard's measure (c), taken before a proof is trusted: a proof that
        # tolerates unaccounted rows (an export CSV's slack, the Stocklist's mapping) stands
        # past a step only while what it leaves unaccounted for did not grow by a step
        # (slack_short).
        acc, gone, older = accountable(mine, newest, is_gone)
        miss = sum(1 for r in acc if (_as_date(_col(r, "date_checked")) or newest) < newest)
        step = self.step(lk, acc, newest, [r for r in mine if _col(r, "id") in gone], strict)
        short = slack_short(proof, step)
        if proof and not short:
            return Verdict(**base, verdict=WHOLE, basis=proof, vetoed=False, proof=True)
        # Tonight's two channels agreed on a count that is not the page's (TORSION_AGREE):
        # it proved nothing, and every basis below says so. Likewise a proof whose slack
        # does not account for tonight's step.
        doubt = tonight.doubts.get(lk) if judged_tonight else None
        pre = "".join(x + "; " for x in (short, doubt) if x)
        # c) the step guard (STEP_MIN_ROWS): measured over accountable rows, before any
        #    baseline is consulted -- a step of a tenth (of 2 rows, on a list the old rule
        #    holds partial) needs a proof, whatever the history or the anchor would make of
        #    what is left.
        if step:
            return Verdict(**base, verdict=PARTIAL, basis=pre + self.step_text(step),
                           accountable=len(acc), missing=miss, vetoed=False, proof=False)
        # d) history: E-Agent price lists only
        prior = [(d, n) for d, n, v, _b in self.reads.get(lk, ()) if v == WHOLE and d < newest]
        if ch not in PROOF_REQUIRED and ch == "E-Agent" and prior:
            last = [n for _d, n in prior][-FULL_READ_RUNS:]
            baseline = max(last)
            window = [n for d, n in prior if (newest - d).days <= DRIFT_WINDOW_DAYS]
            floor = max(window) if window else baseline
            if baseline < min_rows:
                return Verdict(**base, verdict=WHOLE, basis="history: a %d-row list is not "
                               "judged by a share" % baseline, baseline=baseline,
                               vetoed=False, proof=False)
            if seen < FULL_READ_SHARE * baseline:
                return Verdict(**base, verdict=PARTIAL, basis="collapsed %d of %d" % (
                    seen, baseline), baseline=baseline, vetoed=False, proof=False)
            if seen < DRIFT_FLOOR * floor:
                return Verdict(**base, verdict=PARTIAL, basis="below the drift floor: %d of "
                               "the %d-day max %d" % (seen, DRIFT_WINDOW_DAYS, floor),
                               baseline=baseline, vetoed=False, proof=False)
            return Verdict(**base, verdict=WHOLE, basis="history %d of %d" % (seen, baseline),
                           baseline=baseline, vetoed=False, proof=False)
        # e) the anchor: today's rule over accountable rows
        basis = pre + "anchor %d/%d accountable missing" % (miss, len(acc))
        if gone or older:
            basis += " (%d gone-status, %d older identities set aside)" % (len(gone),
                                                                          len(older))
        if len(mine) < len(members):
            basis += " (%d withdrawn builder's set aside)" % (len(members) - len(mine))
        # The floor on every row the list holds, withdrawn ones included (see above).
        floor = len(members) if ANCHOR_FLOOR_ON_MEMBERS else len(acc)
        partial = floor >= min_rows and acc and miss * 100 // len(acc) >= partial_share
        if partial and ch in PROOF_REQUIRED:
            basis += ", no proof"
        return Verdict(**base, verdict=PARTIAL if partial else WHOLE, basis=basis,
                       accountable=len(acc), missing=miss, vetoed=False, proof=False)

    def note(self, v):
        """Tonight's verdict, kept for commit() and for the counts below."""
        self.tonight[v.list_key] = v
        reads = [e for e in self.reads.get(v.list_key, ()) if e[0] != v.newest]
        reads.append((v.newest, v.seen, v.verdict, v.basis))
        self.reads[v.list_key] = sorted(reads)
        if self.first_night is None:
            self.first_night = _day(self.today)

    def commit(self, tonight=None, rule_b=None):
        """Write tonight's verdicts, channel nights and offers (and prune). Nothing in a
        report-only run."""
        if not self.write:
            return 0
        record_channels(self, tonight)
        if rule_b is not None:
            record_rule_b(self, tonight, rule_b)
        stamp = self.today.isoformat(timespec="seconds")
        for v in self.tonight.values():
            if v.recorded:
                continue                               # already written on its own night
            # Never over a recorded veto (judge keeps one; this is the table's own guard).
            self.conn.execute(
                "INSERT INTO list_reads (list_key, read_date, channel, label, "
                "rows_seen, members, accountable, baseline, verdict, basis, recorded_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT (list_key, read_date) DO UPDATE SET channel=excluded.channel, "
                "label=excluded.label, rows_seen=excluded.rows_seen, "
                "members=excluded.members, accountable=excluded.accountable, "
                "baseline=excluded.baseline, verdict=excluded.verdict, "
                "basis=excluded.basis, recorded_at=excluded.recorded_at "
                "WHERE COALESCE(list_reads.basis, '') NOT LIKE ?",
                (v.list_key, _iso(v.newest), v.channel, v.label, v.seen, v.members,
                 v.accountable, v.baseline, v.verdict, v.basis, stamp, VETO + "%"))
        prune(self.conn, self.today)
        return len(self.tonight)

    def log_retirement(self, row, stamp, rule, v, silent, basis=None, status_before=None):
        """One retirement, with the evidence it was made on and the status that comes back
        if the source lists the lot again (database.record_building reads it)."""
        if not self.write:
            return
        self.conn.execute(
            "INSERT INTO retirements (row_id, stamp, rule, list_key, read_date, silent_days, "
            "basis, status_before) VALUES (?,?,?,?,?,?,?,?)",
            (row["id"], stamp, rule, v.list_key if v else "",
             _iso(v.newest) if v else "", silent, basis or (v.basis if v else ""),
             status_before))


def _baseline_nights(hist):
    """The recorded nights a channel's tonight is measured against: the last
    HEALTH_WINDOW_NIGHTS HEALTHY ones, so a long partial outage never becomes the median
    (5 nights of 2 links out of 12 would otherwise be "normal" by the fifth); while fewer
    than HEALTH_MIN_NIGHTS are healthy -- the ledger's first nights -- every night that
    offered anything. A real, lasting drop therefore stops rule B for that channel until a
    person looks: it says so in the log every night, and it fails closed."""
    hist = [h for h in hist if h["units"] > 0]
    healthy = [h for h in hist if h.get("healthy")]
    return (healthy if len(healthy) >= HEALTH_MIN_NIGHTS else hist)[-HEALTH_WINDOW_NIGHTS:]


def channel_health(units, pages, hist):
    """(healthy, why) for one channel's night against its recorded nights.

    Healthy when it offered at least HEALTHY_SHARE of its median number of units over the
    last HEALTH_WINDOW_NIGHTS recorded nights, and every page present on 5 of those 7
    nights is present tonight with at least PAGE_LINK_SHARE of its median link count. Fewer
    than HEALTH_MIN_NIGHTS recorded nights is NOT healthy: the rules that need a healthy
    night (rule B) fail closed until the ledger has seen enough of them."""
    seen = [h for h in hist if h["units"] > 0]
    if len(seen) < HEALTH_MIN_NIGHTS:
        return False, "%d recorded night(s), %d needed to judge" % (len(seen),
                                                                   HEALTH_MIN_NIGHTS)
    hist = _baseline_nights(hist)
    med = statistics.median(h["units"] for h in hist)
    if units < HEALTHY_SHARE * med:
        return False, "%d unit(s) against a median of %g" % (units, med)
    need = -(-len(hist) * PAGE_PRESENT_NIGHTS // HEALTH_WINDOW_NIGHTS)
    for page in sorted(set().union(*[set(h["pages"]) for h in hist])):
        present = [int(h["pages"][page]) for h in hist if int(h["pages"].get(page) or 0) > 0]
        if len(present) >= need and int(pages.get(page) or 0) < \
                PAGE_LINK_SHARE * statistics.median(present):
            return False, "page %s has %d against a median of %g" % (
                page, int(pages.get(page) or 0), statistics.median(present))
    return True, ""


def eagent_links(entries):
    """E-Agent's stock-link lines. RealNet's folder line is a "stock link" too (merged 24 Oct
    2026): counted as an E-Agent unit it would move E-Agent's page health whenever the folder
    is switched off, and its failures would hold E-Agent slots. A line naming no channel is
    from a manifest written before every line named one: E-Agent's."""
    return [e for e in entries if e.get("kind") == "stock link"
            and str(e.get("channel") or "E-Agent") == "E-Agent"]


def realnet_hold(tonight, row):
    """Why a RealNet package must not retire tonight (Tonight.realnet_hold), or None; None
    for every other channel's row. A night with no manifest holds every package: nothing
    says which files are still in the folder."""
    if str(_col(row, "source_channel") or "") != REALNET:
        return None
    if tonight is None:
        return "no harvest manifest tonight"
    return tonight.realnet_hold(row)


def night_units(tonight):
    """{channel: (units, pages, failed builders)} the manifest offered tonight."""
    out = {}
    links = eagent_links(tonight.entries)
    if links:
        pages = collections.Counter(str(e.get("page") or "") for e in links)
        # A failed link whose rows name no builder yet -- a re-upload none of whose rows
        # has been read through it -- is its heading's: on a builder page, the builder.
        failed = sorted({tonight.link_builder(e) or str(e.get("heading") or "").strip()
                         for e in links if _link_failed(e)} - {""})
        out["E-Agent"] = (len(links), dict(pages), failed)
    projects = list(tonight.projects.values())
    if projects:
        on = sum(1 for e in projects if e.get("on_index"))
        out["Proxima"] = (len(projects), {"on_index": on, "off_index": len(projects) - on},
                          [])
    return out


def _link_failed(e):
    return (e.get("outcome") in FAILED_OUTCOMES
            or any(p.get("outcome") in FAILED_OUTCOMES for p in (e.get("pieces") or [])))


def _link_read_whole(tonight, e):
    """A stock link read tonight with nothing failed, cut or refused (pieces that are
    simply not price lists aside -- that is _clean_link's stricter bar)."""
    note = tonight.link_notes.get(str(e.get("stocklist_key") or "")) or {}
    return (e.get("outcome") == "read" and not _link_failed(e) and not e.get("truncated")
            and not note.get("truncated")
            and int((e.get("stored") or {}).get("rejected") or 0) == 0)


# PROXIMA SAYS WHICH PROJECTS IT COULD NOT READ (review, 24 Oct 2026). sources/proxima.py
# skips a project whose accordion will not open ("project unreadable") or that raised
# ("project errored") before logging its manifest line, so tonight's manifest says of it
# exactly what it says of a project that left the dropdown: nothing. Rule B cannot tell the
# two apart, so it does not count a Proxima night on which any listed project failed to
# read, nor one whose manifest does not say (harvest_buildings writes the reader's own
# counts as manifest["runs"]["Proxima"]; a manifest without them is a harvest from before
# this, or a Proxima run that never finished). It fails closed: one project that cannot be
# read stops Proxima's rule B for the night, and the log says which counts stopped it.
PROXIMA_FAILED_STATS = ("project unreadable", "project errored", "off-index project errored",
                        "off-index project published no lots")


def proxima_run_problem(tonight):
    """Why tonight's Proxima run cannot say a project left the dropdown, or None."""
    run = tonight.runs.get("Proxima") if tonight is not None else None
    if not isinstance(run, dict):
        return "tonight's manifest does not say whether every listed project was read"
    stats = run.get("stats") if isinstance(run.get("stats"), dict) else {}
    failed = {}
    for k in PROXIMA_FAILED_STATS:
        try:
            n = int(stats.get(k) or 0)
        except (TypeError, ValueError):
            n = 1
        if n:
            failed[k] = n
    if failed:
        return "%d listed project(s) could not be read (%s)" % (
            sum(failed.values()), ", ".join("%s %d" % kv for kv in sorted(failed.items())))
    try:
        errors = int(run.get("errors") or 0)
        seen, logged = int(run["projects_seen"]), int(run["projects_logged"])
    except (KeyError, TypeError, ValueError):
        return "tonight's Proxima run note is incomplete"
    if errors:
        return "the Proxima reader logged %d error(s)" % errors
    if logged < seen:
        return "%d of %d listed project(s) have a manifest line" % (logged, seen)
    return None


def record_channels(ledger, tonight):
    """channel_nights for tonight: what each channel offered and whether the night was
    healthy. Written only under --apply."""
    if not ledger.write or tonight is None:
        return {}
    out = {}
    for ch, (units, pages, failed) in night_units(tonight).items():
        healthy, why = channel_health(units, pages, ledger.channel_history(ch))
        ledger.conn.execute(
            "INSERT OR REPLACE INTO channel_nights (night, channel, units, pages_json, "
            "failed_builders_json, healthy, why) VALUES (?,?,?,?,?,?,?)",
            (tonight.night, ch, units, json.dumps(pages, sort_keys=True), json.dumps(failed),
             1 if healthy else 0, why))
        out[ch] = (healthy, why)
    return out


# ------------------------------------------------------------------ rule B

# RULE B: THE SOURCE STOPPED OFFERING THE LIST (24 Oct 2026). A list nobody reads any more
# retires nothing by absence -- its own newest read is old, so the 3-day window skips it --
# and three things leave lists unread for good: E-Agent re-uploading a file under a new
# link or taking a link down (201 + 35 documents, about 4,900 held rows, never read again),
# Proxima projects leaving the account's dropdown (821, 192, 637, 172, 119, 120: about
# 100 lots after 12 Oct), and a file no longer served inside a folder that is read cleanly.
# Rule B counts the nights the SOURCE ITSELF stopped offering the unit -- a link gone from
# its E-Agent page, a project gone from the dropdown -- and only on nights that can say so:
# the channel healthy, no mass vanishing, the unit's page crawled, nothing of its builder
# failed or half-read tonight. A dead or private link still on the page is still offered,
# so it never counts. After UNOFFERED_NIGHTS counted nights the unit's live rows retire,
# unless they were read within WITHDRAWN_AFTER_DAYS: presence always wins. Direct portals
# are recorded but never retired this way: a portal drops out of the manifest when OUR
# registry drops it, which says nothing about its stock.
#
# A ROW'S UNIT IS ITS CURRENT LINK (review, 24 Oct 2026). A lot carried onto a re-upload
# is read through the new link. A row keeps the link it was first stored under (merged with
# main 1858d55, see links_of), so a row is resolved through its document (current_links),
# and a unit any of whose rows' links -- its own, its document's, the document's own url --
# or whose documents tonight's manifest says were served by a link on the page tonight
# ("docs") is still offered and never counts. And a unit whose place on the page (page,
# heading, label) is held tonight by a link that did not read whole -- a re-upload none of
# whose rows we have ever read -- is not counted either: what the old link carried may be
# on the new one.
# Proxima is not counted on a night any listed project failed to read
# (proxima_run_problem).
#
# THE 40% RULE CARRIED ACROSS A RE-UPLOAD (second review, 24 Oct 2026). A re-upload whose
# read is cut silently -- outcome read, nothing failed, 10 of the old link's 30 lots -- is a
# whole read of its own (10 of 10), so its builder was "read whole", its place on the page
# was not held, and the old link's 20 stranded lots, still for sale, retired after 14
# nights: the very cut the 40% rule holds on one link. A row keeps the link it was first
# stored under (stocklist_file), so the lots a successor carries say which unit they came
# from. A vanished E-Agent unit with a SUCCESSOR -- tonight's read carried some of its rows
# through another link, another link holds its place (page, heading, label), or a link of
# its builder first offered since it was last offered is on the page -- is counted only
# when what tonight's read carried of its rows through other links is at least
# SUCCESSOR_SHARE of that plus its stranded live rows ("its successor on the page carries
# under 60% of it"). 2 of 3, or 28 of 30, still retire the lots the re-upload dropped; a
# successor that carries none of its rows (its rows were first stored under a link before
# it, say) carries 0% of it, and nothing is counted. The same holds for a file a cleanly
# read folder stops serving while a file new to that folder appears (_plan_files): the new
# file's rows stored before it first appeared were carried from the folder's other files.
#
# A UNIT NEVER OFFERED SINCE THE LEDGER BEGAN has no place to check, and "its builder has a
# list read whole tonight" was all that gated it. It is counted only while under
# STRANDED_SHARE of its builder's live rows sit on units no longer offered (30 Sep: Creation
# Homes 116 against 51 read, EVO Homes 26 against 36), or when every list of its builder
# judged tonight was PROVEN whole (a counter, an export, two channels), not merely whole.
#
# A SUCCESSOR THAT LEFT LOTS BEHIND IS NO PROOF THEY SOLD (third review, 25 Oct 2026). The
# 60% bar still let a silently cut re-upload retire up to 40% of its list -- lots still for
# sale, which main never retires (the old document simply goes stale): a re-upload carrying
# 20 of 30, 27 of 30, 36 of 39 (Albertine's External Agent folder on the 26 Sep copy: the
# three lots it left behind, all Available, retired on night 18), a list split into two
# links one of them cut, a renamed file in a clean folder cut to 20. The same cut on an
# unchanged link is held by the step guard. So a vanished unit WITH A SUCCESSOR that leaves
# SUCCESSOR_HOLD_ROWS or more rows behind whose own status does not say gone is held ("its
# successor dropped ...") until the successor's list is PROVEN whole tonight (an export, a
# counter) or a person accepted a read of it since the unit was last offered (repair_rows
# --accept LIST_KEY, the keys printed in section 9b); one lot left behind, or rows that
# already say gone, may count as before. A file new to a cleanly read folder is held the
# same way (_folder_successor_hold). And the never-offered bars are the step guard's:
# max(2, 10%), not 40%.
SUCCESSOR_SHARE = 60                               # 100 * FULL_READ_SHARE
SUCCESSOR_HOLD_ROWS = 2                            # STEP_MIN_ROWS
STRANDED_SHARE = 40                                # repair_rows.PARTIAL_READ_SHARE (history)
UNOFFERED_NIGHTS = 14
NOT_SERVED_NIGHTS = 14
MASS_VANISH_MIN = 5
MASS_VANISH_SHARE = 0.05
RULE_B_CHANNELS = ("E-Agent", "Proxima")
FILE_CHANNEL = "E-Agent file"
UNOFFERED, SERVED, NOT_SERVED = "unoffered", "served", "not served"
_ABSENT_DAYS = 14                                  # repair_rows.WITHDRAWN_AFTER_DAYS


def row_unit(row, fk=None, doc_link=None):
    """The unit rule B counts a row under: an E-Agent stock link (its current one, by its
    spelling-normalised file key) or a Proxima project, or None."""
    ch = str(_col(row, "source_channel") or "")
    if ch == "E-Agent":
        sf = row_link(row, doc_link)
        return sha16(fk(sf)) if (fk is not None and sf) else None
    if ch == "Proxima":
        pid = str(_col(row, "source_project_id") or "").strip()
        return pid or None
    return None


def _clean_link(tonight, e):
    """A stock link read cleanly tonight: every piece read, nothing truncated or refused."""
    pieces = e.get("pieces") or []
    note = tonight.link_notes.get(str(e.get("stocklist_key") or "")) or {}
    return (e.get("outcome") == "read" and pieces
            and all(p.get("outcome") == "read" for p in pieces)
            and not e.get("truncated") and not note.get("truncated")
            and int((e.get("stored") or {}).get("rejected") or 0) == 0)


class RuleB:
    """Tonight's rule B, decided but not written: the offers to record, the units counted,
    the files counted, what retires, and why anything is held."""

    def __init__(self):
        self.offers = []          # (channel, unit, page, heading, label, builder, outcome)
        self.counted = {}         # (channel, unit) -> (nights, page, builder, rows)
        self.blocked = collections.Counter()
        self.person = []          # (channel, unit, rows): a blank builder, nothing proves it
        self.files = {}           # file unit -> (outcome, nights, rows)
        self.retire = []          # (row, silent, rule, basis)
        self.off = {}             # channel -> why rule B did not count tonight
        # (channel, unit or file, rows left behind, builder, successor list keys): held
        # until a successor's list is proven or a person accepts a read of it.
        self.waiting = []


def successor_proven(ledger, verdicts_by_lk, lks, since):
    """True when every one of a successor's lists (`lks`, list keys) is PROVEN whole
    tonight, or a person accepted a read of it on or after `since` (the night, iso, the
    vanished unit was last offered; None: any). No list known: False -- it fails closed."""
    if not lks:
        return False
    for lk in lks:
        v = verdicts_by_lk.get(lk)
        if v is not None and v.tonight and v.whole and v.proof:
            continue
        if any(verdict == WHOLE and str(b).startswith(ACCEPTED)
               and (since is None or _iso(d) >= since)
               for d, _n, verdict, b in ledger.reads.get(lk, ())):
            continue
        return False
    return True


def plan_rule_b(ledger, tonight, rows, verdicts, today, withdrawn=frozenset(), is_gone=None,
                doc_key=None):
    """RULE B for tonight (see the comment above). Pure: record_rule_b writes it.

    `withdrawn`: ids of rows of a builder the client withdrew (builder_names, Bathla). They
    are no unit's members -- never retired here, never naming a unit's builder -- but they
    still show that a file was served tonight. `is_gone(row)`: the row's own status says it
    cannot be bought (repair_rows._gone_status; without it every row counts as for sale).
    `doc_key(row)`: the key of the list a row is judged in (repair_rows._doc_key, pooled),
    how a successor's verdict is found; without it no successor is ever proven."""
    out = RuleB()
    if tonight is None:
        out.off = {ch: "no manifest tonight" for ch in RULE_B_CHANNELS}
        return out
    is_gone = is_gone or (lambda r: False)
    fk = file_key_fn()
    # Absence is measured from the run's own read date where the manifest states it (one
    # read date per run), never from a later clock: a run that crossed midnight is the day
    # its rows carry.
    day = _day(today)
    if tonight.run_date is not None:
        day = min(day, tonight.run_date)
    links = eagent_links(tonight.entries)
    if fk is None:
        out.off["E-Agent"] = "sources.e_agent.stocklist_file_key cannot be imported"
    elif links and not all(e.get("unit") for e in links):
        out.off["E-Agent"] = "tonight's manifest names no unit for its links"
    proj_builders = collections.defaultdict(collections.Counter)
    for r in rows:
        if str(_col(r, "source_channel") or "") == "Proxima":
            proj_builders[str(_col(r, "source_project_id") or "")][
                _norm(_col(r, "builder_name"))] += 1
    offered = {"E-Agent": {}, "Proxima": {}}
    for e in links:
        if e.get("unit"):
            offered["E-Agent"][str(e["unit"])] = (
                str(e.get("page") or ""), str(e.get("heading") or ""),
                str(e.get("label") or ""), _norm(tonight.link_builder(e)),
                str(e.get("outcome") or ""))
    for pid, e in tonight.projects.items():
        c = proj_builders.get(pid) or collections.Counter()
        c.pop("", None)
        offered["Proxima"][pid] = ("on_index" if e.get("on_index") else "off_index",
                                   str(e.get("name") or ""), "",
                                   c.most_common(1)[0][0] if c else "",
                                   str(e.get("outcome") or ""))
    for ch, units in offered.items():
        out.offers.extend((ch, u) + v for u, v in units.items())
    for (ch, name), e in tonight.portals.items():
        out.offers.append((ch, name, str(e.get("host") or ""), str(e.get("name") or ""), "",
                           name, str(e.get("outcome") or "")))

    live = {ch: collections.defaultdict(list) for ch in RULE_B_CHANNELS}
    for r in rows:
        ch = str(_col(r, "source_channel") or "")
        if ch in live and not _col(r, "superseded_by") and _col(r, "id") not in withdrawn:
            u = row_unit(r, fk, tonight.doc_link)
            if u:
                live[ch][u].append(r)
    partial_b, whole_b = collections.defaultdict(set), collections.defaultdict(set)
    proven, unproven = collections.defaultdict(set), collections.defaultdict(set)
    for v in verdicts.values():
        if v.tonight and v.builder:
            (whole_b if v.whole else partial_b)[v.channel].add(v.builder)
            (proven if v.whole and v.proof else unproven)[v.channel].add(v.builder)
    # Builders every one of whose lists judged tonight was PROVEN whole.
    proven_b = {ch: proven[ch] - unproven[ch] for ch in RULE_B_CHANNELS}
    # Each builder's live rows on units offered tonight and on units no longer offered.
    fresh_rows = {ch: collections.Counter() for ch in RULE_B_CHANNELS}
    stale_rows = {ch: collections.Counter() for ch in RULE_B_CHANNELS}
    for ch, units in live.items():
        for u, members in units.items():
            (fresh_rows if u in offered[ch] else stale_rows)[ch][main_builder(members)] += \
                len(members)
    # What each E-Agent unit's rows were carried through tonight by ANOTHER unit: a row read
    # tonight whose first link (stocklist_file) is that unit's and whose current link is not
    # -- a lot a re-upload carried on. Read tonight: one of the run's dates, and stamped at
    # or after the run began where it carries a stamp.
    moved = collections.Counter()
    moved_to = collections.defaultdict(set)
    # The lists (by list key) each E-Agent unit's rows read tonight are judged in: how a
    # successor's verdict is found (successor_proven).
    lists_by_unit = collections.defaultdict(set)
    verdicts_by_lk = {v.list_key: v for v in verdicts.values()}
    if fk is not None:
        for r in rows:
            if str(_col(r, "source_channel") or "") != "E-Agent" or \
                    _col(r, "id") in withdrawn:
                continue
            mark = str(_col(r, "superseded_by") or "")
            if (mark and not _is_hash_mark(mark)) or not _read_tonight(tonight, r):
                continue
            first = str(_col(r, "stocklist_file") or "").strip()
            cur = row_unit(r, fk, tonight.doc_link)
            if first and cur and sha16(fk(first)) != cur:
                moved[sha16(fk(first))] += 1
                moved_to[sha16(fk(first))].add(cur)
            if cur and not mark and doc_key is not None:
                v = verdicts.get(doc_key(r))
                if v is not None:
                    lists_by_unit[cur].add(v.list_key)
    units_now = night_units(tonight)
    # Places on an E-Agent page held tonight by a link that did not read whole.
    held_slots = {(str(e.get("page") or ""), str(e.get("heading") or ""),
                   str(e.get("label") or "")) for e in links
                  if not _link_read_whole(tonight, e)}

    for ch in RULE_B_CHANNELS:
        if ch in out.off:
            continue
        if ch == "Proxima":
            why = proxima_run_problem(tonight)
            if why:
                out.off[ch] = "night not counted: " + why
                continue
        n_units, pages, failed = units_now.get(ch, (0, {}, []))
        healthy, why = channel_health(n_units, pages, ledger.channel_history(ch))
        if not healthy:
            out.off[ch] = "night not counted: " + why
            continue
        history = ledger.offers(ch)
        offered_before = {u: [x for x in recs if x[5] != UNOFFERED]
                          for u, recs in history.items()}
        # The night each unit was first offered (a unit new tonight is tonight's).
        first_offered = {u: recs[0][0] for u, recs in offered_before.items() if recs}
        nights = sorted({x[0] for recs in offered_before.values() for x in recs})
        prev = {u for u, recs in offered_before.items() if nights and recs
                and recs[-1][0] == nights[-1]}
        now = offered[ch]
        new_slots = {now[u][:3] for u in set(now) - prev}
        vanished = [u for u in prev - set(now)
                    if offered_before[u][-1][1:4] not in new_slots]
        limit = max(MASS_VANISH_MIN, int(MASS_VANISH_SHARE * n_units))
        if len(vanished) > limit:
            out.off[ch] = "mass vanish: %d unit(s) gone unexplained (limit %d)" % (
                len(vanished), limit)
            continue
        tonight_pages = {v[0] for v in now.values()}
        failed_b = {_norm(b) for b in failed}
        for u, members in live[ch].items():
            if u in now:
                continue
            if ch == "E-Agent" and (
                    {sha16(fk(sf)) for sf in links_of(members, tonight.doc_link)} & set(now)
                    or any(sha16(str(_col(r, "source_url") or "").strip())
                           in tonight.links_by_doc for r in members)):
                # Resolved through its document to a link gone tonight, but a row's own
                # link, its document's own url, or a link tonight's manifest says served
                # its document is still on the page: any may lead to it, so it is offered.
                out.blocked["a link its rows were read through is still offered"] += 1
                continue
            builder = main_builder(members)
            if not builder:
                out.person.append((ch, u, len(members)))
                continue
            last = offered_before.get(u, [])
            last = last[-1] if last else None
            if last is not None and last[1] not in tonight_pages:
                out.blocked["its page was not crawled tonight"] += 1
                continue
            if ch == "E-Agent" and last is not None and tuple(last[1:4]) in held_slots:
                out.blocked["its place on the page is held tonight by a link that did not "
                            "read whole"] += 1
                continue
            if ch == "E-Agent":
                carried = moved.get(u, 0)
                succ = set(moved_to.get(u, ()))
                if last is not None:
                    succ |= {s for s, v in now.items() if v[:3] == tuple(last[1:4])
                             or (v[3] == builder
                                 and first_offered.get(s, tonight.night) >= last[0])}
                succeeded = bool(succ)
                if succeeded and carried * 100 < SUCCESSOR_SHARE * (carried + len(members)):
                    out.blocked["its successor on the page carries under 60% of it"] += 1
                    continue
                left = sum(1 for r in members if not is_gone(r))
                lks = sorted({lk for s in succ for lk in lists_by_unit.get(s, ())})
                if succeeded and left >= SUCCESSOR_HOLD_ROWS and not successor_proven(
                        ledger, verdicts_by_lk, lks, last[0] if last else None):
                    out.blocked["its successor dropped %d or more of its lots still listed "
                                "for sale: waits for a proof or a person's --accept of the "
                                "successor's list" % SUCCESSOR_HOLD_ROWS] += 1
                    out.waiting.append((ch, u, left, builder, lks))
                    continue
            if last is None and builder not in whole_b[ch]:
                out.blocked["never offered since the ledger began, and no list of its "
                            "builder read whole tonight"] += 1
                continue
            if last is None and builder not in proven_b[ch]:
                st, fr = stale_rows[ch][builder], fresh_rows[ch][builder]
                if st >= step_need(st + fr):
                    out.blocked["never offered since the ledger began, and a step (max(%d, "
                                "%d%%)) of its builder's live rows are on units no longer "
                                "offered" % (STEP_MIN_ROWS, round(STEP_SHARE * 100))] += 1
                    continue
            if builder in failed_b:
                out.blocked["its builder had a failed read tonight"] += 1
                continue
            if builder in partial_b[ch]:
                out.blocked["its builder has a half-read list tonight"] += 1
                continue
            since = last[0] if last else ""
            counted = 1 + sum(1 for x in history.get(u, ())
                              if x[5] == UNOFFERED and x[0] > since)
            out.counted[(ch, u)] = (counted, last[1] if last else "", builder, len(members))
            if counted < UNOFFERED_NIGHTS:
                continue
            for r in members:
                seen = _as_date(_col(r, "date_checked"))
                if seen and (day - seen).days >= _ABSENT_DAYS:
                    out.retire.append((r, (day - seen).days, RULE_UNOFFERED,
                                       "%s offered no more for %d counted night(s)" % (
                                           "link" if ch == "E-Agent" else "project",
                                           counted)))

    def files_proven(rs, since):
        """(the lists the rows `rs` read tonight are judged in are a proven successor,
        those lists' keys)."""
        lks = set()
        for r in rs:
            if doc_key is None or _col(r, "superseded_by") or not _read_tonight(tonight, r):
                continue
            v = verdicts.get(doc_key(r))
            if v is not None:
                lks.add(v.list_key)
        lks = sorted(lks)
        return successor_proven(ledger, verdicts_by_lk, lks, since), lks

    if "E-Agent" not in out.off:
        _plan_files(out, ledger, tonight, rows, links, day, withdrawn, is_gone, files_proven)
    return out


def _plan_files(out, ledger, tonight, rows, links, day, withdrawn=frozenset(), is_gone=None,
                proven=None):
    """File level: a file a cleanly read folder no longer serves. Counted only under a
    link read clean tonight (every piece read, no budget bound, nothing refused) that
    still yielded rows from its other files. `is_gone` and `proven(rows, since)` as for
    _folder_successor_hold."""
    by_link = collections.defaultdict(list)
    for r in rows:
        if str(_col(r, "source_channel") or "") == "E-Agent" and read_link(r):
            by_link[sha16(row_link(r, tonight.doc_link))].append(r)
    history = ledger.offers(FILE_CHANNEL)
    for e in links:
        if not _clean_link(tonight, e):
            continue
        under = by_link.get(str(e.get("stocklist_key") or ""), [])
        files = collections.defaultdict(list)
        for r in under:
            mark = str(_col(r, "superseded_by") or "")
            if mark and not _is_hash_mark(mark) and mark != WITHDRAWN_FROM_SOURCE:
                continue
            files[str(_col(r, "source_url") or "")].append(r)
        files.pop("", None)
        served = {s for s, rs in files.items()
                  if any(str(_col(r, "date_checked") or "") in tonight.read_dates for r in rs)}
        if not served:
            continue
        hold, left, lks = _folder_successor_hold(tonight, history, files, served, withdrawn,
                                                 is_gone, proven)
        if left:
            out.waiting.append((FILE_CHANNEL, str(e.get("unit") or ""), left,
                                tonight.link_builder(e), lks))
        for s, rs in files.items():
            fu = sha16(s)
            if s in served:
                out.files[fu] = (SERVED, 0, 0)
                continue
            liv = [r for r in rs if not _col(r, "superseded_by")
                   and _col(r, "id") not in withdrawn]
            if not liv:
                continue
            if hold:
                out.blocked[hold] += 1
                continue
            recs = history.get(fu, [])
            since = max((x[0] for x in recs if x[5] == SERVED), default="")
            counted = 1 + sum(1 for x in recs if x[5] == NOT_SERVED and x[0] > since)
            out.files[fu] = (NOT_SERVED, counted, len(liv))
            if counted < NOT_SERVED_NIGHTS:
                continue
            for r in liv:
                seen = _as_date(_col(r, "date_checked"))
                if seen and (day - seen).days >= _ABSENT_DAYS:
                    out.retire.append((r, (day - seen).days, RULE_NOT_SERVED,
                                       "file not served by its cleanly read folder for %d "
                                       "night(s)" % counted))


def _read_tonight(tonight, r):
    """A row tonight's run read: one of the run's dates, and stamped at or after the run
    began where it carries a stamp."""
    if str(_col(r, "date_checked") or "") not in tonight.read_dates:
        return False
    t = _checked(r)
    return t is None or t >= tonight.started


def _folder_successor_hold(tonight, history, files, served, withdrawn, is_gone=None,
                           proven=None):
    """(why no file this cleanly read folder no longer serves may be counted tonight, or
    None; the rows left behind a held successor waits on, else 0; that successor's list
    keys, for a person's --accept) -- see THE 40% RULE
    CARRIED ACROSS A RE-UPLOAD and A SUCCESSOR THAT LEFT LOTS BEHIND. `files` is
    {document: rows} under the folder's link, `served` the documents it served tonight.

    A file served tonight that the folder first served on or after the night a file it no
    longer serves was last served may be that file renamed or re-uploaded: its rows read
    tonight that were stored before it first appeared were carried from elsewhere, and
    they must be at least SUCCESSOR_SHARE of themselves plus every live row the folder no
    longer serves; and when SUCCESSOR_HOLD_ROWS or more of those rows say nothing of being
    gone (`is_gone`), the new files must be a proven successor (`proven(rows, since)` ->
    (proven, list keys): successor_proven over their lists). Files never served since the ledger began have no
    night to compare: their live rows must be under a step (step_need) of themselves plus
    the rows read tonight."""
    is_gone = is_gone or (lambda r: False)
    nights = {s: sorted(x[0] for x in history.get(sha16(s), []) if x[5] == SERVED)
              for s in files}
    gone, left = {}, 0
    for s, rs in files.items():
        if s in served:
            continue
        liv = [r for r in rs if not _col(r, "superseded_by") and _col(r, "id") not in withdrawn]
        if liv:
            gone[s] = len(liv)
            left += sum(1 for r in liv if not is_gone(r))
    if not gone:
        return None, 0, []
    stranded = sum(gone.values())
    lasts = [nights[s][-1] for s in gone if nights[s]]
    if lasts:
        since = min(lasts)
        carried = new = 0
        new_rows = []
        for s in served:
            first = nights[s][0] if nights[s] else tonight.night
            if first < since:
                continue
            new += 1
            new_rows.extend(files[s])
            first_d = _from_iso(first)
            for r in files[s]:
                fs = _as_date(_col(r, "first_seen"))
                if _read_tonight(tonight, r) and fs is not None and first_d is not None \
                        and fs < first_d:
                    carried += 1
        if new and carried * 100 < SUCCESSOR_SHARE * (carried + stranded):
            return ("a file new to its folder carries under 60% of what the folder no "
                    "longer serves"), 0, []
        if new and left >= SUCCESSOR_HOLD_ROWS:
            ok, lks = proven(new_rows, since) if proven is not None else (False, [])
            if not ok:
                return ("a file new to its folder dropped %d or more lots still listed for "
                        "sale: waits for a proof or a person's --accept of its list"
                        % SUCCESSOR_HOLD_ROWS), left, lks
    never = sum(n for s, n in gone.items() if not nights[s])
    if never:
        read_now = sum(1 for s in served for r in files[s] if _read_tonight(tonight, r))
        if never >= step_need(never + read_now):
            return ("a step (max(%d, %d%%)) of its folder's rows are on files not served "
                    "since the ledger began" % (STEP_MIN_ROWS, round(STEP_SHARE * 100))), \
                0, []
    return None, 0, []


def record_rule_b(ledger, tonight, plan):
    """source_offers for tonight: every unit offered, every counted absence, every file
    served or not. Written only under --apply."""
    if not ledger.write or tonight is None:
        return
    rows = [(tonight.night,) + tuple(o) for o in plan.offers]
    rows += [(tonight.night, ch, u, page, "", "", builder, UNOFFERED)
             for (ch, u), (_n, page, builder, _r) in plan.counted.items()]
    rows += [(tonight.night, FILE_CHANNEL, fu, "", "", "", "", outcome)
             for fu, (outcome, _n, _r) in plan.files.items()]
    ledger.conn.executemany(
        "INSERT OR REPLACE INTO source_offers (night, channel, unit, page, heading, label, "
        "builder, outcome) VALUES (?,?,?,?,?,?,?,?)", rows)


def prune(conn, today):
    """Drop list_reads, source_offers and channel_nights older than PRUNE_NIGHTS -- except
    the read each list's step guard is measured against (Ledger.step_reference), whatever
    its age (repair, 25 Oct 2026): its latest WHOLE read, or, for a list never read whole,
    its first recorded read. A list held partial by the step guard is measured against
    that read for as long as the cut lasts; pruned after PRUNE_NIGHTS, the list's first
    remaining read would be the cut itself, and the bare anchor would call the cut whole.
    One row per list, kept for good, like the retirements."""
    cut = _iso(today - timedelta(days=PRUNE_NIGHTS))
    # Null-safe throughout (IS, COALESCE): a NULL verdict or an empty MAX must never turn
    # the keep-test into NULL, which NOT leaves NULL and the DELETE then skips.
    conn.execute(
        "DELETE FROM list_reads WHERE read_date < ? AND NOT COALESCE(("
        "(verdict IS ? AND read_date IS (SELECT MAX(w.read_date) FROM list_reads w "
        "WHERE w.list_key IS list_reads.list_key AND w.verdict IS ?)) OR "
        "(NOT EXISTS (SELECT 1 FROM list_reads w WHERE w.list_key IS list_reads.list_key "
        "AND w.verdict IS ?) AND read_date IS (SELECT MIN(f.read_date) FROM list_reads f "
        "WHERE f.list_key IS list_reads.list_key))), 0)", (cut, WHOLE, WHOLE, WHOLE))
    conn.execute("DELETE FROM source_offers WHERE night < ?", (cut,))
    conn.execute("DELETE FROM channel_nights WHERE night < ?", (cut,))


# ------------------------------------------------------------------ a person's tools

def undo_retirements(conn, stamp, now=None):
    """Put back every retirement made at `stamp` that is still in place: the mark goes and
    the status it had before comes back. The undo holds until the lot is seen again
    (repair_rows skips a row whose undo is newer than its last sighting)."""
    ensure_tables(conn)
    now = (now or datetime.now()).isoformat(timespec="seconds")
    n = 0
    for rid, before in conn.execute(
            "SELECT row_id, status_before FROM retirements WHERE stamp=? AND undone_at IS "
            "NULL", (stamp,)).fetchall():
        cur = conn.execute("UPDATE buildings SET superseded_by=NULL, superseded_at=NULL, "
                           "availability_status=?, status_previous='Sold' "
                           "WHERE id=? AND superseded_by=?",
                           (before, rid, WITHDRAWN_FROM_SOURCE))
        if cur.rowcount:
            conn.execute("UPDATE retirements SET undone_at=?, undone_by='person' "
                         "WHERE row_id=? AND stamp=?", (now, rid, stamp))
            n += 1
    return n


def accept(conn, lk, read_date=None, now=None):
    """A person's proof that a list's read was whole: its newest recorded read (or the one
    on `read_date`, dd/mm/YYYY) is recorded WHOLE, basis 'accepted by a person ...'. A veto
    from that night's manifest still wins (judge). Returns the read date, or None."""
    ensure_tables(conn)
    now = now or datetime.now()
    if read_date:
        d = _as_date(read_date)
        rd = _iso(d) if d else None
    else:
        got = conn.execute("SELECT MAX(read_date) FROM list_reads WHERE list_key=?",
                           (lk,)).fetchone()
        rd = got[0] if got else None
    if not rd:
        return None
    basis = "%s at %s" % (ACCEPTED, now.isoformat(timespec="seconds"))
    cur = conn.execute("UPDATE list_reads SET verdict=?, basis=? WHERE list_key=? AND "
                       "read_date=?", (WHOLE, basis, lk, rd))
    if not cur.rowcount:
        conn.execute("INSERT INTO list_reads (list_key, read_date, verdict, basis, "
                     "recorded_at) VALUES (?,?,?,?,?)",
                     (lk, rd, WHOLE, basis, now.isoformat(timespec="seconds")))
    return rd


def explain(conn, row, key):
    """Lines saying how the ledger sees one row: its list, that list's reads and the
    row's retirement history. Never a link."""
    lk = list_key(key)
    out = ["row %s: list %s (%s)" % (row["id"], lk, label(key, [row])),
           "  last seen %s, superseded_by %r, availability %r" % (
               _col(row, "date_checked"), _col(row, "superseded_by") or "",
               _col(row, "availability_status") or "")]
    if not tables_exist(conn):
        out.append("  the ledger has no tables yet (no --apply run since it was added)")
        return out
    seen = _as_date(_col(row, "date_checked"))
    whole_after = 0
    for rd, n, members, verdict, basis in conn.execute(
            "SELECT read_date, rows_seen, members, verdict, basis FROM list_reads WHERE "
            "list_key=? ORDER BY read_date", (lk,)):
        d = _from_iso(rd)
        if verdict == WHOLE and seen and d and d > seen:
            whole_after += 1
        out.append("  %s  %-7s %4s of %-4s  %s" % (rd, verdict, n, members, basis))
    newest = conn.execute("SELECT MAX(read_date) FROM list_reads WHERE list_key=?",
                          (lk,)).fetchone()[0]
    nd = _from_iso(newest)
    if seen and nd:
        out.append("  absent from its list for %d day(s) at its newest recorded read "
                   "(rule A needs %d)" % ((nd - seen).days, _ABSENT_DAYS))
    out.append("  whole reads since it was last seen: %d (rule A needs %d)"
               % (whole_after, MIN_WHOLE_READS_ABSENT))
    unit = row_unit(row, file_key_fn())
    if unit:
        ch = str(_col(row, "source_channel") or "")
        recs = conn.execute("SELECT night, outcome FROM source_offers WHERE channel=? AND "
                            "unit=? ORDER BY night", (ch, unit)).fetchall()
        offered = [n for n, o in recs if o != UNOFFERED]
        counted = [n for n, o in recs if o == UNOFFERED and (not offered or n > offered[-1])]
        out.append("  rule B: unit [%s] last offered %s; %d night(s) counted since (it "
                   "retires at %d)" % (unit, offered[-1] if offered else "never (since the "
                                       "ledger began)", len(counted), UNOFFERED_NIGHTS))
    for stamp, rule, basis, before, undone_at, undone_by in conn.execute(
            "SELECT stamp, rule, basis, status_before, undone_at, undone_by FROM "
            "retirements WHERE row_id=? ORDER BY stamp", (row["id"],)):
        out.append("  retired %s (%s; %s) status before %r%s" % (
            stamp, rule, basis, before,
            "; undone %s by %s" % (undone_at, undone_by) if undone_at else ""))
    return out
