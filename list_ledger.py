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
     search ending clean, two channels agreeing on the same page, a person's --accept.
  c) HISTORY (E-Agent price lists only). Tonight's read is no smaller than 60% of the
     largest of the list's last 7 WHOLE reads, and no smaller than half the largest WHOLE
     read of the last 28 days (a staircase of drops just under 40% cannot walk it down).
  d) THE ANCHOR, when there is no history and no proof: today's 40% rule, with the
     yardstick fixed -- measured over ACCOUNTABLE rows, the members minus rows whose own
     status says gone and minus older identities matched one-for-one to a row on tonight's
     read. The 10-row floor is tested on the FULL member count.

A PARTIAL read is recorded but never enters any baseline, however many nights it repeats:
repeating a bad read never makes it the yardstick. Proxima, the direct portals and the
E-Agent Stocklist can never be whole on count history at all -- a capped read repeats the
same count night after night -- so they need (b), or fall to (d).

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
# A run that ends before this hour may hold reads stamped the evening before.
MANIFEST_SPANS_MIDNIGHT_BEFORE_HOUR = 6
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
TORSION_AGREE = 2
# list_reads, source_offers and channel_nights older than this are pruned; retirements
# are kept for good.
PRUNE_NIGHTS = 120
# Where Proxima's accordion stops rendering; api/_candidates.PROXIMA_RENDER_CAPS is the
# source of truth and a test pins the two together.
RENDER_CAPS_FALLBACK = (80, 83, 160)

# Channels whose count history can never show a read whole on its own.
PROOF_REQUIRED = frozenset({"Proxima", "Direct Builder Portal (live)", "E-Agent Stocklist"})
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


def _is_hash_mark(mark):
    return str(mark or "").startswith(HASH_RECIPE_VERSION + ":")


def render_caps():
    try:
        import sys
        api = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api")
        if api not in sys.path:
            sys.path.insert(0, api)
        from _candidates import PROXIMA_RENDER_CAPS
        return tuple(PROXIMA_RENDER_CAPS)
    except Exception:                                      # noqa: BLE001
        return RENDER_CAPS_FALLBACK


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
    """(channel, builder, lot_number) or None, and the places that can confirm a match."""
    lot = _norm(_col(row, "lot_number"))
    builder = _norm(_col(row, "builder_name"))
    if not lot or not builder:
        return None, ()
    places = tuple(p for p in (("estate", _norm(_col(row, "estate_name"))),
                               ("suburb", _norm(_col(row, "suburb"))),
                               ("street", _street(_col(row, "lot_address")))) if p[1])
    return (str(_col(row, "source_channel") or ""), builder, lot), places


def same_lot(a, b):
    ka, pa = lot_identity(a)
    kb, pb = lot_identity(b)
    return bool(ka) and ka == kb and bool(set(pa) & set(pb))


def older_identities(missing, on_read):
    """{missing row id: the on-read row it is an older identity of}, matched ONE-FOR-ONE:
    same builder and lot_number, and the same estate, suburb or street. Each row on the read
    absorbs at most one missing row, most recently seen first."""
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
            if r["id"] not in used and same_lot(m, r):
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


class Tonight:
    """What tonight's manifest says about each read, for judge(): vetoes and proofs.

    A list is judged with the manifest only when its newest read is the manifest's own
    night (read_dates): a list read yesterday is judged as it was recorded then."""

    def __init__(self, manifest, rows, ledger=None):
        self.m = manifest or {}
        made = self.m.get("_made") or datetime.now()
        self.night = _iso(made)
        dates = {_ddmmyyyy(made)}
        if made.hour < MANIFEST_SPANS_MIDNIGHT_BEFORE_HOUR:
            dates.add(_ddmmyyyy(made - timedelta(days=1)))
        self.read_dates = dates
        entries = [e for e in (self.m.get("entries") or []) if isinstance(e, dict)]
        self.entries = entries
        self.projects = {str(e.get("pid")): e for e in entries
                         if e.get("kind") == "project" and e.get("channel") == "Proxima"}
        self.links_by_key, self.links_by_unit = {}, {}
        for e in entries:
            if e.get("kind") == "stock link":
                if e.get("stocklist_key"):
                    self.links_by_key[str(e["stocklist_key"])] = e
                if e.get("unit"):
                    self.links_by_unit[str(e["unit"])] = e
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
        # Rows stored from each document tonight, whatever supersede made of them since (a
        # duplicate superseded tonight was still on the read); repair's own marks aside.
        self.stored = collections.Counter()
        # E-Agent links read tonight, by host, for the two-channel agreement.
        self.eagent_by_host = collections.defaultdict(list)
        # Who each stock link belongs to, from the rows it has given (any night).
        self._builders = collections.defaultdict(collections.Counter)
        for r in rows:
            if str(_col(r, "source_channel") or "") == "E-Agent" and _col(r, "stocklist_file"):
                self._builders[sha16(_col(r, "stocklist_file"))][
                    str(_col(r, "builder_name") or "").strip()] += 1
        for r in rows:
            if str(_col(r, "date_checked") or "") not in dates:
                continue
            mark = str(_col(r, "superseded_by") or "")
            if mark and not _is_hash_mark(mark):
                continue
            self.stored[str(_col(r, "source_url") or "")] += 1
            if str(_col(r, "source_channel") or "") == "E-Agent":
                sf = str(_col(r, "stocklist_file") or "")
                for host in {_host(_col(r, "source_url")), _host(sf)} - {""}:
                    self.eagent_by_host[host].append(sf)
        self.proxima_unhealthy = proxima_index_health(self, ledger)

    # --- E-Agent ----------------------------------------------------------------
    def link_builder(self, e):
        """The builder a stock link's rows name most often; blank for a pooled list."""
        c = self._builders.get(str(e.get("stocklist_key") or ""))
        if c:
            name, _n = c.most_common(1)[0]
            return name
        return ""

    def link_entries(self, members):
        ents, fk = [], file_key_fn()
        for sf in sorted({str(_col(r, "stocklist_file") or "") for r in members} - {""}):
            e = self.links_by_key.get(sha16(sf))
            if e is None and fk is not None:
                e = self.links_by_unit.get(sha16(fk(sf)))
            if e is not None:
                ents.append((sf, e))
        return ents

    def _eagent(self, key, members):
        veto = None
        for sf in sorted({str(_col(r, "stocklist_file") or "") for r in members} - {""}):
            note = self.link_notes.get(sha16(sf)) or {}
            if note.get("truncated"):
                return "link truncated (%s)" % (note.get("why") or "a folder budget bound"), \
                    None
        for sf, e in self.link_entries(members):
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

    def _portal(self, key, members, seen):
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
        # same page is read through E-Agent tonight and the two counts agree.
        host = str(e.get("host") or "").lower()
        if host and got == found and host in self.eagent_by_host:
            printed = int(e.get("printed") or got)
            for sf in set(self.eagent_by_host[host]):
                le = self.links_by_key.get(sha16(sf))
                if le is not None and abs(int(le.get("printed") or 0) - printed) <= \
                        TORSION_AGREE:
                    return None, "two channels agree: portal %d, e-agent %d" % (
                        printed, int(le.get("printed") or 0))
        return None, None

    # --- E-Agent Stocklist -------------------------------------------------------
    def _search(self):
        if not self.searches:
            return None, None
        e = self.searches[-1]
        if e.get("state") == "ok" and not e.get("short"):
            return None, "stocklist search ok"
        return "stocklist search %s%s" % (e.get("state") or "?",
                                          ", short" if e.get("short") else ""), None

    def veto_and_proof(self, key, members, seen):
        """(veto text or None, proof text or None) for one list read tonight."""
        ch = str(_col(members[0], "source_channel") or "")
        if ch == "E-Agent":
            return self._eagent(key, members)
        if ch == "Proxima":
            return self._proxima(key, members, seen)
        if ch == "E-Agent Stocklist":
            return self._search()
        if ch in PROOF_REQUIRED:
            return self._portal(key, members, seen)
        return None, None


def proxima_index_health(tonight, ledger):
    """A reason when tonight's Proxima index is narrowed against the recorded nights, else
    None. Unknown (fewer than HEALTH_MIN_NIGHTS recorded) is not unhealthy: it vetoes
    nothing, so the old rule's retirements are never delayed by a young ledger."""
    projects = list(tonight.projects.values())
    hist = ledger.channel_history("Proxima") if ledger is not None else []
    hist = [h for h in hist if h["units"] > 0][-HEALTH_WINDOW_NIGHTS:]
    if len(hist) < HEALTH_MIN_NIGHTS:
        return None
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
                 "label", "recorded")

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
        for d, seen, verdict, basis in self.reads.get(lk, ()):
            if d == newest:
                return verdict, basis
        return None

    def whole_reads_after(self, lk, seen_on):
        start = self.first_night or _day(self.today)
        return sum(1 for d, _n, v, _b in self.reads.get(lk, ())
                   if v == WHOLE and d > seen_on and d >= start)

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
                    if night == self.night:
                        continue
                    try:
                        pages = json.loads(pages or "{}")
                    except ValueError:
                        pages = {}
                    self._channels[ch].append({"night": night, "units": int(units or 0),
                                               "pages": pages, "healthy": bool(healthy)})
        return self._channels.get(channel, [])

    # --- judging ------------------------------------------------------------------
    def judge(self, key, members, newest, tonight=None, is_gone=lambda r: False,
              partial_share=40, min_rows=10):
        """The Verdict on the list's newest read: the first rule that matches decides."""
        lk = list_key(key)
        ch = str(_col(members[0], "source_channel") or "")
        seen = sum(1 for r in members if _as_date(_col(r, "date_checked")) == newest)
        base = dict(key=key, list_key=lk, newest=newest, seen=seen, members=len(members),
                    channel=ch, label=label(key, members))
        recorded = self.recorded(lk, newest)
        judged_tonight = tonight is not None and _ddmmyyyy(newest) in tonight.read_dates
        accepted = bool(recorded) and str(recorded[1]).startswith(ACCEPTED)
        if recorded and not judged_tonight and not accepted:
            v, b = recorded
            return Verdict(**base, verdict=v, basis=b, vetoed=str(b).startswith(VETO),
                           proof=False, recorded=True)
        # a) vetoes, from tonight's own record of the read
        veto = proof = None
        if judged_tonight:
            veto, proof = tonight.veto_and_proof(key, members, seen)
        if veto:
            return Verdict(**base, verdict=PARTIAL, basis=VETO + veto, vetoed=True,
                           proof=False)
        # b) proofs
        if accepted:
            return Verdict(**base, verdict=WHOLE, basis=recorded[1], vetoed=False,
                           proof=True)
        if proof:
            return Verdict(**base, verdict=WHOLE, basis=proof, vetoed=False, proof=True)
        # c) history: E-Agent price lists only
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
        # d) the anchor: today's rule over accountable rows
        acc, gone, older = accountable(members, newest, is_gone)
        miss = sum(1 for r in acc if (_as_date(_col(r, "date_checked")) or newest) < newest)
        basis = "anchor %d/%d accountable missing" % (miss, len(acc))
        if gone or older:
            basis += " (%d gone-status, %d older identities set aside)" % (len(gone),
                                                                          len(older))
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

    def commit(self, tonight=None):
        """Write tonight's verdicts and channel nights (and prune). Nothing in a
        report-only run."""
        if not self.write:
            return 0
        record_channels(self, tonight)
        stamp = self.today.isoformat(timespec="seconds")
        for v in self.tonight.values():
            if v.recorded:
                continue                               # already written on its own night
            self.conn.execute(
                "INSERT OR REPLACE INTO list_reads (list_key, read_date, channel, label, "
                "rows_seen, members, accountable, baseline, verdict, basis, recorded_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (v.list_key, _iso(v.newest), v.channel, v.label, v.seen, v.members,
                 v.accountable, v.baseline, v.verdict, v.basis, stamp))
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


def channel_health(units, pages, hist):
    """(healthy, why) for one channel's night against its recorded nights.

    Healthy when it offered at least HEALTHY_SHARE of its median number of units over the
    last HEALTH_WINDOW_NIGHTS recorded nights, and every page present on 5 of those 7
    nights is present tonight with at least PAGE_LINK_SHARE of its median link count. Fewer
    than HEALTH_MIN_NIGHTS recorded nights is NOT healthy: the rules that need a healthy
    night (rule B) fail closed until the ledger has seen enough of them."""
    hist = [h for h in hist if h["units"] > 0][-HEALTH_WINDOW_NIGHTS:]
    if len(hist) < HEALTH_MIN_NIGHTS:
        return False, "%d recorded night(s), %d needed to judge" % (len(hist),
                                                                   HEALTH_MIN_NIGHTS)
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


def night_units(tonight):
    """{channel: (units, pages, failed builders)} the manifest offered tonight."""
    out = {}
    links = [e for e in tonight.entries if e.get("kind") == "stock link"]
    if links:
        pages = collections.Counter(str(e.get("page") or "") for e in links)
        failed = sorted({tonight.link_builder(e) for e in links if _link_failed(e)} - {""})
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


def prune(conn, today):
    """Drop list_reads, source_offers and channel_nights older than PRUNE_NIGHTS."""
    cut = _iso(today - timedelta(days=PRUNE_NIGHTS))
    conn.execute("DELETE FROM list_reads WHERE read_date < ?", (cut,))
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
    out.append("  whole reads since it was last seen: %d (a retirement needs %d)"
               % (whole_after, MIN_WHOLE_READS_ABSENT))
    for stamp, rule, basis, before, undone_at, undone_by in conn.execute(
            "SELECT stamp, rule, basis, status_before, undone_at, undone_by FROM "
            "retirements WHERE row_id=? ORDER BY stamp", (row["id"],)):
        out.append("  retired %s (%s; %s) status before %r%s" % (
            stamp, rule, basis, before,
            "; undone %s by %s" % (undone_at, undone_by) if undone_at else ""))
    return out
