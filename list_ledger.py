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
PORTAL = "Direct Builder Portal (live)"
PROOF_REQUIRED = frozenset({"Proxima", PORTAL, "E-Agent Stocklist"})
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
        if ledger is not None and self.night not in ledger.exclude_nights:
            ledger.exclude_nights.add(self.night)
            ledger._channels = None
            ledger.__dict__.pop("_offers", None)
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
        c = collections.Counter(self._builders.get(str(e.get("stocklist_key") or "")) or {})
        c.pop("", None)
        return c.most_common(1)[0][0] if c else ""

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
                 "label", "recorded", "builder")

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
              partial_share=40, min_rows=10):
        """The Verdict on the list's newest read: the first rule that matches decides."""
        lk = list_key(key)
        ch = str(_col(members[0], "source_channel") or "")
        seen = sum(1 for r in members if _as_date(_col(r, "date_checked")) == newest)
        base = dict(key=key, list_key=lk, newest=newest, seen=seen, members=len(members),
                    channel=ch, label=label(key, members), builder=main_builder(members))
        recorded = self.recorded(lk, newest)
        judged_tonight = tonight is not None and _ddmmyyyy(newest) in tonight.read_dates
        accepted = bool(recorded) and str(recorded[1]).startswith(ACCEPTED)
        if recorded and not judged_tonight and not accepted:
            v, b = recorded
            return Verdict(**base, verdict=v, basis=b, vetoed=str(b).startswith(VETO),
                           proof=False, recorded=True)
        # a) vetoes. First the count itself, manifest or not: a Proxima read ending on a
        #    render cap, or a portal read ending on its page length, was cut -- that is
        #    main's hole (lots 81-85 behind an 80 cap retired after a fortnight), closed
        #    even on a night the manifest is missing. Then tonight's own record of the read.
        veto = proof = None
        if ch == "Proxima" and seen in render_caps():
            veto = "proxima read ends on a render cap (%d)" % seen
        elif ch == PORTAL and seen and seen % PORTAL_PAGE_LENGTH == 0:
            veto = "portal read ends on its page length (%d)" % seen
        elif judged_tonight:
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
# unless they were read within WITHDRAWN_AFTER_DAYS: a lot carried onto a new upload is
# read nightly (record_building moves its source_url, never its stocklist_file), so
# presence always wins. Direct portals are recorded but never retired this way: a portal
# drops out of the manifest when OUR registry drops it, which says nothing about its stock.
UNOFFERED_NIGHTS = 14
NOT_SERVED_NIGHTS = 14
MASS_VANISH_MIN = 5
MASS_VANISH_SHARE = 0.05
RULE_B_CHANNELS = ("E-Agent", "Proxima")
FILE_CHANNEL = "E-Agent file"
UNOFFERED, SERVED, NOT_SERVED = "unoffered", "served", "not served"
_ABSENT_DAYS = 14                                  # repair_rows.WITHDRAWN_AFTER_DAYS


def row_unit(row, fk=None):
    """The unit rule B counts a row under: an E-Agent stock link (by its spelling-
    normalised file key) or a Proxima project, or None."""
    ch = str(_col(row, "source_channel") or "")
    if ch == "E-Agent":
        sf = str(_col(row, "stocklist_file") or _col(row, "source_url") or "").strip()
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


def plan_rule_b(ledger, tonight, rows, verdicts, today):
    """RULE B for tonight (see the comment above). Pure: record_rule_b writes it."""
    out = RuleB()
    if tonight is None:
        out.off = {ch: "no manifest tonight" for ch in RULE_B_CHANNELS}
        return out
    fk = file_key_fn()
    day = _day(today)
    links = [e for e in tonight.entries if e.get("kind") == "stock link"]
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
        if ch in live and not _col(r, "superseded_by"):
            u = row_unit(r, fk)
            if u:
                live[ch][u].append(r)
    partial_b, whole_b = collections.defaultdict(set), collections.defaultdict(set)
    for v in verdicts.values():
        if _ddmmyyyy(v.newest) in tonight.read_dates and v.builder:
            (whole_b if v.whole else partial_b)[v.channel].add(v.builder)
    units_now = night_units(tonight)

    for ch in RULE_B_CHANNELS:
        if ch in out.off:
            continue
        n_units, pages, failed = units_now.get(ch, (0, {}, []))
        healthy, why = channel_health(n_units, pages, ledger.channel_history(ch))
        if not healthy:
            out.off[ch] = "night not counted: " + why
            continue
        history = ledger.offers(ch)
        offered_before = {u: [x for x in recs if x[5] != UNOFFERED]
                          for u, recs in history.items()}
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
            builder = main_builder(members)
            if not builder:
                out.person.append((ch, u, len(members)))
                continue
            last = offered_before.get(u, [])
            last = last[-1] if last else None
            if last is not None and last[1] not in tonight_pages:
                out.blocked["its page was not crawled tonight"] += 1
                continue
            if last is None and builder not in whole_b[ch]:
                out.blocked["never offered since the ledger began, and no list of its "
                            "builder read whole tonight"] += 1
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

    if "E-Agent" not in out.off:
        _plan_files(out, ledger, tonight, rows, links, day)
    return out


def _plan_files(out, ledger, tonight, rows, links, day):
    """File level: a file a cleanly read folder no longer serves. Counted only under a
    link read clean tonight (every piece read, no budget bound, nothing refused) that
    still yielded rows from its other files."""
    by_link = collections.defaultdict(list)
    for r in rows:
        if str(_col(r, "source_channel") or "") == "E-Agent" and _col(r, "stocklist_file"):
            by_link[sha16(_col(r, "stocklist_file"))].append(r)
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
        for s, rs in files.items():
            fu = sha16(s)
            if s in served:
                out.files[fu] = (SERVED, 0, 0)
                continue
            liv = [r for r in rs if not _col(r, "superseded_by")]
            if not liv:
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
