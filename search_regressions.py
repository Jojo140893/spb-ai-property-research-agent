"""search_regressions.py -- replay Colin's searches and fail loudly when one goes wrong.

Part C, 16 Sep 2026. Colin searched "QLD under $750k" and was shown a Victorian lot filed as
Queensland (14905) and a lot a later read said was On Hold (18491), while three sellable
Shape Homes lots in Walloon sat hidden with nothing on screen. Every one of those was
checkable from data we held. This replays his searches, and the form slips that turned his
retries into nothing, from data/colin_searches.json, and checks what must hold for each.

    python search_regressions.py --snapshot vercel_site/stock.json [--db PATH] [--today 16/09/2026]
    python search_regressions.py --live https://app.example --key-file PATH [--published-snapshot vercel_site/stock.json]

--snapshot  THE NIGHTLY STEP (run_daily.py, after build_web). The DB is copied to a temp file
            through a read-only connection and never opened for writing. A child process,
            with SPB_DATABASE_PATH pointed at the copy, GROQ_API_KEY and SPB_COMP_PROVIDER
            unset and PYTHONDONTWRITEBYTECODE set, imports vercel_site/api/research.py -- the
            bundle that ships, NOT app/api, so a module the bundle forgot is caught here
            (critique #20c) -- with the bundle made read-only the way Vercel runs it, and
            calls run_research exactly as the endpoint does. No network: no Groq, no
            comparables, no portal. The bundle is fingerprinted before and after; a replay
            that wrote anything into what is about to be deployed fails.
--live      W23's replay against the deployed API. The service key is read from a file,
            sent as x-spb-service-key and never printed (every line is scrubbed of it);
            redirects are refused so the key is never forwarded. The middleware lets the
            key through only on POST /api/research, so card dates are compared with the
            published vercel_site/stock.json by content_hash (critique #13).

WHAT IS CHECKED, for every brief:
  * the response is status success, it searched the state and price range the brief says
    (expect.brief_used, required on every brief: an echo is not an oracle), and a body
    with explain got every explain key back with no pass failed (search_help.error, a
    relaxation priced as "error");
  * what goes to Kommo names shortlisted cards only: the lead's top property is one of
    them, and no needs-confirming card is written into the internal note;
  * NEGATIVE INVARIANTS on every shortlisted and needs-confirming card: inside the searched
    state and price range; its date_checked equals the catalogue's (the DB's, in --snapshot);
    its row does not say Sold / On Hold; a shortlisted card is at most 14 days old; and no
    shortlisted row has a FRESHER sibling -- same document, same line once the status word
    is taken out, same price -- that says the lot has gone (18491 vs 15953);
  * WARN only: a shortlisted row filed in its state from the suburb column alone, whose own
    text names a locality of one other state (14905: "Wollert");
  * then the brief's own `expect`: must-not-appear hashes, Dawn needs confirming while its
    rows are unchanged, equal_to another brief, brief_adjustments / brief_used /
    location_resolution / record id rules, minimum counts, the funnel invariant
    (shortlist_count + rejected_count == in_state_rows), no "exceeds max budget" rejection,
    and the independent OBVIOUS-ROWS check (--snapshot only): rows picked straight out of
    the database -- live, in the brief's state, priced in range, stated available, at most
    45 days old, not a Build Only / Land Only price and not one part of a bigger printed
    total -- must each be shortlisted, offered to confirm, collapsed into a twin that is
    shown, or hidden by a named sold-stock guard. Anything else fails and names the id.
    no_suburb, suburb_not_a_locality and incomplete_facts are data-quality outcomes and only
    WARN (critique #20a).
  * RELAXATION SELF-CHECK: every relaxation priced "exact" is re-posted with its brief_patch
    and must return exactly its count. Nightly only for brief #1 (the time budget); every
    brief with --checks full, which --live defaults to (critique #20b).
  * TIMING: brief #1 is posted twice and the second (warm) call timed: over 8 s fails in
    --live and warns in --snapshot, where the machine is not Vercel's (critique #13).

EXIT CODE: 0 when nothing failed (warnings allowed), 1 otherwise -- including a usage error,
which argparse would report as 2: run_daily.py reads a step's 2 as "a harvest channel
returned nothing" and 70 as "fell back", and neither is what a failed replay means. As an
OPTIONAL run_daily step a failure marks the run DEGRADED after it has published, so fresh
stock is never withheld over a regression, and the job still goes red.

NEVER PRINTS A LINK OR THE KEY: hashes, database ids, estates and lot numbers only.
"""
import argparse
import contextlib
import errno
import io
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent
BRIEFS = APP / "data" / "colin_searches.json"
DEFAULT_SNAPSHOT = APP / "vercel_site" / "stock.json"

SERVICE_KEY_HEADER = "x-spb-service-key"
# The only hosts --live may reach over plain http (a stub server in the tests).
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# A shortlisted card must be verified: read within this many days (_candidates.FRESH_DAYS,
# stated here on purpose -- the harness does not borrow the rule it is checking).
FRESH_DAYS = 14
# The obvious-rows check picks rows at most this old (the confirm window, plan section 1).
# Fixed, NOT SPB_CONFIRM_MAX_DAYS: a search that narrowed its window would otherwise
# narrow the check with it and pass.
OBVIOUS_MAX_DAYS = 45
# A stored price is one part of a bigger total when the line prints more than this over it.
COMPONENT_TOLERANCE = 0.02
# The warm call's limit (critique #13: warm calls took 1.7-2.5 s, explain adds up to 4 s).
WARM_CALL_LIMIT_S = 8.0
# Two captures of one document within this many seconds are one read of it, not a later
# read (supersede_duplicates.SAME_READ_WINDOW_S: two lots on one page are stamped apart).
SAME_READ_S = 600
AEST = timezone(timedelta(hours=10))

# The search's availability vocabulary, as the plan states it -- copied, not imported.
IS_AVAILABLE = frozenset({"available", "for sale", "in stock", "released", "selling",
                          "titled", "now selling", "open"})
NOT_AVAILABLE = frozenset({"sold", "under offer", "reserved", "leased", "on hold",
                           "withdrawn", "not available", "unavailable", "not avail",
                           "no longer available", "off market", "settled", "deposit taken",
                           "contract pending"})
# Words taken out of a line before two captures of it are compared. Not "titled",
# "released" or "selling": those are as often facts about the lot as its status.
_STATUS_WORDS = ("no longer available", "not available", "not avail", "unavailable",
                 "available", "on hold", "hold", "sold out", "sold", "under offer",
                 "under contract", "reserved", "leased", "withdrawn", "eoi", "tbc",
                 "deposit taken", "settled", "contract pending", "off market")
_STATUS_RX = re.compile(r"\b(?:%s)\b" % "|".join(
    re.escape(w).replace(r"\ ", r"\s*") for w in _STATUS_WORDS), re.I)
_URLISH = re.compile(r"(?:[a-z][a-z0-9+.-]*:/+|www\.)\S*|\[link removed\]", re.I)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# The shorter of two cut-off lines must be at least this long to be read as the same line.
_SAME_LINE_MIN = 30

# How an obvious row may end up. Anything else fails.
ALLOWED_VERDICTS = ("shortlist", "needs_confirming", "collapsed_into_visible",
                    "newer_capture_disagrees", "newer_capture_exists",
                    # A later capture of the same lot, on the same line, asks another
                    # price (release review: Hattan lot 406 at its 17/08 price).
                    "newer_capture_repriced",
                    "document_reread_without_row", "builder_documents_reread",
                    # W11's email guard (critique #9), the same kind of sold-stock evidence
                    # as builder_documents_reread for a list sent by email.
                    "newer_list_from_sender",
                    "price_unverified",
                    # The lot's own page answered 404/410 when link_check.py asked (26 Sep:
                    # Coleen's SA search opened dead Kenekt pages). Sold-stock evidence the
                    # database does not carry, so an "obvious" row can be hidden by it.
                    "lot_page_gone")
WARN_VERDICTS = ("no_suburb", "suburb_not_a_locality", "incomplete_facts",
                 # An office or a shop filed as a dwelling (release review: "Building A
                 # Ground Level Office", an Apartment row) -- the filing is the problem.
                 "commercial")
EXPLAIN_KEYS = ("search_help", "needs_confirming", "needs_confirming_count",
                "needs_confirming_hidden")
# What every brief in data/colin_searches.json must say it searched (load_spec).
BRIEF_USED_REQUIRED = ("state", "budget_min", "budget_max")
NO_CEILING = 9007199254740991
EMAIL_CHANNEL = "digital email"

# A scheme (even cut short), "www.", an e-mail address, or a bare domain with a path.
_LINK = re.compile(r"(?:[a-z][a-z0-9+.-]*:/+|www\.)\S*|\S+@\S+\.\S+"
                   r"|\b[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}(?::\d+)?/\S*", re.I)


# ----------------------------------------------------------------------------- plumbing

def _plain(text):
    return _LINK.sub("<link>", str(text))


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _money(value):
    n = _num(value)
    return "?" if n is None else "$" + format(int(round(n)), ",")


def _day(value):
    raw = str(value or "").strip()[:10]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _age(value, today):
    seen = _day(value)
    return None if seen is None else (today - seen).days


def _read_time(value):
    raw = str(value or "").strip()[:19].replace(" ", "T")
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _norm(value):
    return " ".join(_NON_ALNUM.sub(" ", str(value or "").lower()).split())


def _hash(entry):
    return str((entry or {}).get("content_hash") or "").strip()


def status_free(text):
    """A line with its links and status words taken out, lower case, words only."""
    text = _URLISH.sub(" ", str(text or ""))
    return _norm(_STATUS_RX.sub(" ", text))


def same_line(a, b):
    """The same line, allowing either to be cut off (a snapshot keeps ~110 characters, and
    a longer status word cuts the rest shorter: "... Due Septe" / "... Due Septemb")."""
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= _SAME_LINE_MIN and long_.startswith(short)


class Report:
    """Lines as they happen (the nightly log shows progress), scrubbed of links and of any
    secret registered with it."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.failures = 0
        self.warnings = 0
        self.secrets = []
        self.failed_where = {}

    def _emit(self, line, scrub=True):
        text = _plain(line) if scrub else str(line)
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, "[redacted]")
        print(text, file=self.out, flush=True)

    def header(self, text):
        self._emit(text, scrub=False)

    def fail(self, where, text):
        self.failures += 1
        self.failed_where[where] = self.failed_where.get(where, 0) + 1
        self._emit("[FAIL] %s: %s" % (where, text))

    def warn(self, where, text):
        self.warnings += 1
        self._emit("[WARN] %s: %s" % (where, text))

    def note(self, where, text):
        self._emit("[note] %s: %s" % (where, text))

    def ok(self, where, text):
        self._emit("[ ok ] %s: %s" % (where, text))


# ------------------------------------------------------------------------- the briefs

def load_spec(path=BRIEFS):
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = [b["id"] for b in spec["briefs"]]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError("brief ids must be unique: %s" % ", ".join(dupes))
    lots = (spec.get("dawn") or {}).get("lots") or {}
    for brief in spec["briefs"]:
        expect = brief.get("expect") or {}
        if expect.get("equal_to") and expect["equal_to"] not in ids:
            raise ValueError("%s: equal_to names no brief %r" % (brief["id"], expect["equal_to"]))
        if expect.get("never") and expect["never"] not in spec:
            raise ValueError("%s: never names no list %r" % (brief["id"], expect["never"]))
        for lot in expect.get("needs_confirming_contains") or ():
            if lot not in lots:
                raise ValueError("%s: Dawn lot %s is not defined" % (brief["id"], lot))
        # THE BRIEF'S OWN STATE AND RANGE, not only the server's echo of them. Every card is
        # checked against brief_used; a normaliser that misread the budget would echo its
        # own misreading and every card inside it would pass (a thousands rule widened to
        # 1,000,000 searched "QLD $100,000" as $100,000,000 and returned 370 green results).
        stated = expect.get("brief_used") or {}
        lacking = [k for k in BRIEF_USED_REQUIRED if k not in stated]
        if lacking:
            raise ValueError("%s: expect.brief_used must state %s" % (
                brief["id"], ", ".join(lacking)))
    return spec


def client_brief_for(spec, brief):
    """The client_brief as sent: the dashboard form with this brief's fields over it, or --
    for Clare's hook -- exactly the brief as the hook builds it."""
    if brief.get("hook_shaped"):
        sent = dict(brief.get("client_brief") or {})
    else:
        sent = dict(spec["form"])
        sent.update(brief.get("client_brief") or {})
    for key in brief.get("drop") or ():
        sent.pop(key, None)
    return json.loads(json.dumps(sent))


def body_for(spec, brief):
    body = {"client_brief": client_brief_for(spec, brief)}
    if brief.get("explain", True):
        body["explain"] = True
    return body


def where_of(brief):
    return ("%s %s" % (brief.get("plan") or "", brief["id"])).strip()


# ----------------------------------------------------------------------- the catalogue

def read_snapshot_rows(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data.get("rows"), list) and isinstance(data.get("keys"), list):
        keys = data["keys"]
        return [dict(zip(keys, row)) for row in data["rows"]]
    return list(data.get("buildings") or [])


def _ro_uri(path):
    return "file:%s?mode=ro" % Path(path).as_posix()


def read_db_rows(path):
    """Every row of buildings, as dicts, through a read-only connection."""
    conn = sqlite3.connect(_ro_uri(path), uri=True)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM buildings")]
    finally:
        conn.close()


def copy_db_read_only(src, dest):
    """A copy of the database taken with sqlite's backup through a mode=ro connection, so
    the database given is never opened for writing."""
    source = sqlite3.connect(_ro_uri(src), uri=True)
    try:
        target = sqlite3.connect(str(dest))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


class Catalogue:
    """The rows a check compares a card with: the snapshot searched, and in --snapshot mode
    the database it was built from, joined by content_hash."""

    def __init__(self, rows, db_rows=None, label=None):
        self.rows = rows
        self.label = label or ("the database" if db_rows is not None else "the snapshot")
        self.by_hash = {}
        self.by_doc = {}
        for row in rows:
            h = _hash(row)
            if not h:
                continue
            # A live row wins over a superseded capture that shares its hash.
            if h not in self.by_hash or (self.by_hash[h].get("superseded_by")
                                         and not row.get("superseded_by")):
                self.by_hash[h] = row
            doc = str(row.get("source_doc") or "").strip()
            if doc:
                self.by_doc.setdefault(doc, []).append(row)
        self.has_db = db_rows is not None
        self.db = {}
        for rec in db_rows or ():
            h = _hash(rec)
            if h and (h not in self.db or (self.db[h].get("superseded_by")
                                           and not rec.get("superseded_by"))):
                self.db[h] = rec

    def row(self, h):
        return self.by_hash.get(h)

    def db_row(self, h):
        return self.db.get(h)

    def name(self, h):
        rec = self.db.get(h)
        if rec and rec.get("id") is not None:
            return "%s (id %s)" % (h[:11], rec["id"])
        return h[:11]

    def date(self, h):
        """The date the catalogue holds for a card: the database's when there is one."""
        if self.has_db:
            rec = self.db.get(h)
            return None if rec is None else str(rec.get("date_checked") or "").strip()
        row = self.by_hash.get(h)
        return None if row is None else str(row.get("date_checked") or "").strip()

    def text(self, h):
        rec = self.db.get(h) or {}
        return rec.get("source_text") or (self.by_hash.get(h) or {}).get("lot_address") or ""

    def checked_at(self, h):
        return _read_time((self.db.get(h) or {}).get("checked_at"))


def gone_sibling(catalogue, h):
    """A capture of the same line on the same document, read LATER, that says the lot has
    gone -- the shape of 18491 (Available, 15/09) and 15953 (ON HOLD, 16/09). None if none."""
    row = catalogue.row(h)
    if row is None:
        return None
    doc = str(row.get("source_doc") or "").strip()
    text = status_free(catalogue.text(h))
    seen = _day(row.get("date_checked"))
    price = _num(row.get("price"))
    if not doc or len(text) < _SAME_LINE_MIN or seen is None or not price:
        return None
    at = catalogue.checked_at(h)
    for other in catalogue.by_doc.get(doc, ()):
        oh = _hash(other)
        if not oh or oh == h:
            continue
        if str(other.get("availability_status") or "").strip().lower() not in NOT_AVAILABLE:
            continue
        other_price = _num(other.get("price"))
        if other_price is None or abs(other_price - price) >= 1.0:
            continue
        other_seen = _day(other.get("date_checked"))
        if other_seen is None or other_seen < seen:
            continue
        if other_seen == seen:
            other_at = catalogue.checked_at(oh)
            if at is None or other_at is None or (other_at - at).total_seconds() < SAME_READ_S:
                continue
        if same_line(text, status_free(catalogue.text(oh))):
            return other
    return None


# ---------------------------------------------------------------------------- the Dawn lots

def dawn_expectation(spec, lots, catalogue, today):
    """(holds, why_not, until): whether the Dawn lots must be offered to confirm today.

    Held while the rows are unchanged: each lot still has a live row at its price, read on
    the recorded date; no later capture of that estate and lot; no later read of the list
    they came on (a newer list from the sender guards them); and today inside the confirm
    window, counted from the earlier of each row's read and -- for an emailed row -- its
    first read (critique #27). Matched by estate, lot and price, so whichever twin a
    supersede keeps is the lot (critique #3).
    """
    dawn = spec["dawn"]
    estate = _norm(dawn["estate"])
    recorded = _day(dawn["date_checked"])
    until = _day(dawn["until"])
    window = int(dawn.get("confirm_max_days") or OBVIOUS_MAX_DAYS)
    same_estate = [r for r in catalogue.rows if _norm(r.get("estate_name")) == estate]
    for lot in lots:
        price = float(dawn["lots"][lot])
        captures = [r for r in same_estate if _norm(r.get("lot_number")) == _norm(lot)]
        live = [r for r in captures if not r.get("superseded_by")
                and abs((_num(r.get("price")) or 0) - price) < 1.0]
        if not live:
            return False, "no live row holds lot %s at %s" % (lot, _money(price)), until
        later = [r for r in captures if (_day(r.get("date_checked")) or recorded) > recorded]
        if later:
            return False, "lot %s was read again on %s" % (
                lot, later[0].get("date_checked")), until
        if any(_day(r.get("date_checked")) != recorded for r in live):
            return False, "lot %s is no longer dated %s" % (lot, dawn["date_checked"]), until
        lists = {str(r.get("email_list") or "") for r in live} - {""}
        newer = [r for r in catalogue.rows if str(r.get("email_list") or "") in lists
                 and (_day(r.get("date_checked")) or recorded) > recorded]
        if newer:
            return False, "a newer list from the sender of lot %s was read on %s" % (
                lot, newer[0].get("date_checked")), until
        for r in live:
            anchors = [recorded]
            if str(r.get("source_channel") or "").strip().lower() == EMAIL_CHANNEL:
                first = _day(r.get("email_first_seen"))
                if first is not None:
                    anchors.append(first)
            ends = min(anchors) + timedelta(days=window)
            until = ends if until is None else min(until, ends)
    if until is not None and today > until:
        return False, "the confirm window ended on %s" % until.strftime("%d/%m/%Y"), until
    return True, "", until


# ---------------------------------------------------------------------------- the run

def _matches(have, want):
    for key, value in want.items():
        got = (have or {}).get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if _num(got) is None or abs(_num(got) - float(value)) > 0.5:
                return False
        elif isinstance(value, str) and key == "state":
            if str(got or "").strip().upper() != value.upper():
                return False
        elif got != value:
            return False
    return True


def _searched_state(used):
    state = str((used or {}).get("state") or "").strip().upper()
    return "" if state in ("", "ANY STATE") else state


class Run:
    """Every brief posted, every rule checked. `post(body)` returns (http_status, payload,
    seconds); it is an in-process call of the bundle's run_research, or an HTTP POST."""

    def __init__(self, spec, post, catalogue, today, report, mode="snapshot",
                 checks="nightly", obvious=None, locality=None, only=None):
        self.spec = spec
        self.post = post
        self.catalogue = catalogue
        self.today = today
        self.report = report
        self.mode = mode
        self.checks = checks
        self.obvious = obvious
        self.locality = locality
        self.only = list(only or ())
        self.responses = {}
        self.warned_locality = set()
        self.briefs = {b["id"]: b for b in spec["briefs"]}
        unknown = [i for i in self.only if i not in self.briefs]
        if unknown:
            raise ValueError("no such brief: %s" % ", ".join(unknown))

    # -- posting

    def _post(self, brief, body):
        status, payload, seconds = self.post(body)
        if not isinstance(payload, dict):
            payload = {"status": "error", "message": "not a JSON object"}
        return status, payload, seconds

    def response(self, brief_id):
        """(body, status, payload, seconds) for a brief, posted on first use."""
        if brief_id not in self.responses:
            brief = self.briefs[brief_id]
            body = body_for(self.spec, brief)
            status, payload, seconds = self._post(brief, body)
            if brief.get("timed"):
                # The first call can pay for a cold start (an import, a new instance); the
                # second is what a consultant waits for.
                status, payload, warm = self._post(brief, body)
                limit = WARM_CALL_LIMIT_S
                where = where_of(brief)
                if warm > limit:
                    say = "the warm call took %.1f s (limit %.0f s)" % (warm, limit)
                    if self.mode == "live":
                        self.report.fail(where, say)
                    else:
                        self.report.warn(where, say + "; timed on this machine, not Vercel")
                seconds = warm
            self.responses[brief_id] = (body, status, payload, seconds)
        return self.responses[brief_id]

    # -- the run

    def execute(self):
        started = time.monotonic()
        selected = [b for b in self.spec["briefs"] if not self.only or b["id"] in self.only]
        for brief in selected:
            where = where_of(brief)
            try:
                self.check(brief)
            except Exception as exc:                                  # noqa: BLE001
                self.report.fail(where, "the check itself raised %s: %s"
                                 % (type(exc).__name__, exc))
                for line in traceback.format_exc().strip().splitlines()[-6:]:
                    self.report.note(where, line)
        return time.monotonic() - started

    def check(self, brief):
        where = where_of(brief)
        before = self.report.failed_where.get(where, 0)
        body, status, resp, seconds = self.response(brief["id"])
        if status != 200 or resp.get("status") != "success":
            self.report.fail(where, "HTTP %s, status %r: %s" % (
                status, resp.get("status"), str(resp.get("message") or "")[:300]))
            return
        expect = brief.get("expect") or {}
        shortlist = [e for e in resp.get("shortlist") or [] if isinstance(e, dict)]
        confirming = [e for e in resp.get("needs_confirming") or [] if isinstance(e, dict)]
        used = resp.get("brief_used") or {}

        self.explain_answered(where, body, resp)
        self.card_invariants(where, used, shortlist, confirming)
        self.kommo(where, resp, shortlist, confirming)
        if expect.get("never"):
            listed = dict(self.spec[expect["never"]])
            listed.update(expect.get("never_also") or {})
            self.never(where, listed, shortlist, confirming)
        if expect.get("needs_confirming_contains"):
            self.dawn(where, expect["needs_confirming_contains"], confirming)
        if expect.get("funnel"):
            self.funnel(where, resp)
        if expect.get("no_over_budget_rejections"):
            over = [e for e in resp.get("rejected_log") or []
                    if "exceeds max budget" in str(e.get("reason") or "").lower()]
            if over:
                self.report.fail(where, "%d rejection(s) say 'exceeds max budget'" % len(over))
        if "min_shortlist" in expect and (resp.get("shortlist_count") or 0) < expect["min_shortlist"]:
            self.report.fail(where, "shortlist_count %s, expected at least %s"
                             % (resp.get("shortlist_count"), expect["min_shortlist"]))
        if expect.get("adjustment"):
            if not any(_matches(a, expect["adjustment"])
                       for a in resp.get("brief_adjustments") or [] if isinstance(a, dict)):
                self.report.fail(where, "no brief_adjustments entry matches %s (got %s)" % (
                    json.dumps(expect["adjustment"]),
                    json.dumps([{k: a.get(k) for k in ("field", "code", "from", "to")}
                                for a in resp.get("brief_adjustments") or []])))
        if expect.get("brief_used") and not _matches(used, expect["brief_used"]):
            self.report.fail(where, "brief_used %s, expected %s" % (
                json.dumps({k: used.get(k) for k in expect["brief_used"]}),
                json.dumps(expect["brief_used"])))
        if expect.get("location_method"):
            methods = [e.get("method") for e in resp.get("location_resolution") or []
                       if isinstance(e, dict)]
            if expect["location_method"] not in methods:
                self.report.fail(where, "location_resolution has no method %r (got %s)"
                                 % (expect["location_method"], methods))
        self.record_id(where, expect, str(resp.get("research_record_id") or ""))
        if expect.get("suburb_searched"):
            searched = [str(s).strip().lower() for s in used.get("primary_suburbs") or []]
            if expect["suburb_searched"].lower() not in searched:
                self.report.fail(where, "searched suburbs %s, expected %s"
                                 % (used.get("primary_suburbs"), expect["suburb_searched"]))
        if expect.get("no_explain_keys"):
            leaked = [k for k in EXPLAIN_KEYS if k in resp]
            if leaked:
                self.report.fail(where, "a body without explain got %s back" % leaked)
        if expect.get("equal_to"):
            self.equal_to(where, brief, resp, expect["equal_to"])
        if self.checks == "full" or brief.get("self_check_nightly"):
            self.relaxations_re_post(where, body, resp)
        if self.checks == "full" and expect.get("relaxation_exact_when_full"):
            self.relaxations_exact(where, resp, expect["relaxation_exact_when_full"])
        if expect.get("obvious_rows"):
            if self.obvious is None:
                self.report.note(where, "obvious-rows check skipped: it reads the database "
                                        "(--snapshot only)")
            else:
                self.obvious(self, where, brief, body, resp)
        if self.report.failed_where.get(where, 0) == before:
            self.report.ok(where, "shortlist %s, needs confirming %s, %.2f s" % (
                resp.get("shortlist_count"),
                resp.get("needs_confirming_count", "-"), seconds))

    # -- the checks

    def explain_answered(self, where, body, resp):
        """A body with explain gets every explain key back, and none of explain's own passes
        failed. _with_explain never raises -- a pass that does is caught, leaves [] behind
        and is named in search_help.error -- so without this a needs-confirming or
        relaxations pass that throws every night reads as "nothing to offer"."""
        if body.get("explain") is not True:
            return
        missing = [k for k in EXPLAIN_KEYS if k not in resp]
        if missing:
            self.report.fail(where, "a body with explain got no %s back" % missing)
        help_ = resp.get("search_help")
        if not isinstance(help_, dict):
            return
        if help_.get("error"):
            self.report.fail(where, "search_help.error: %s" % str(help_["error"])[:300])
        for offer in help_.get("relaxations") or ():
            if isinstance(offer, dict) and offer.get("count_status") == "error":
                # A re-run that raised, not one the time budget skipped: the button has no
                # count, every night, for a reason nobody is told.
                self.report.fail(where, "relaxation %s failed to price: %s"
                                 % (offer.get("id"), offer.get("error") or "error"))

    def kommo(self, where, resp, shortlist, confirming):
        """What the search hands Kommo names shortlisted cards only: the lead's top property
        is one of them (or none when there are none), and no needs-confirming card that is
        not also shortlisted is written into the internal note."""
        payload = resp.get("kommo_payload")
        if not isinstance(payload, dict):
            return
        cat = self.catalogue

        def price(entry):
            return int(round(_num(entry.get("advertised_package_price")) or 0))

        fields = payload.get("custom_fields_update") or {}
        if "top_property_match" in fields:
            top = str(fields.get("top_property_match") or "").strip()
            top_price = int(round(_num(fields.get("top_property_price")) or 0))
            if shortlist:
                prices = {(str(e.get("lot_address") or "").strip(), p)
                          for e in shortlist
                          for p in (price(e), int(round(_num(e.get("realistic_total_price"))
                                                        or 0)))}
                if (top, top_price) not in prices:
                    self.report.fail(where, "kommo_payload's top property %r at %s is not a "
                                            "shortlisted card" % (top, _money(top_price)))
            elif top_price:
                self.report.fail(where, "kommo_payload names a top property %r at %s, but "
                                        "nothing was shortlisted" % (top, _money(top_price)))
        # One line of the note per shortlisted property: "1. <lot_address>, <suburb> - $<total>
        # (Score ...)". A needs-confirming card's address on a line is a leak -- unless that
        # address is also shortlisted (a fresh capture of the lot at a new price), when only
        # a line carrying the stale card's own price is.
        lines = str(payload.get("kommo_internal_note") or "").splitlines()
        listed = {(str(e.get("lot_address") or "").strip(), price(e)) for e in shortlist}
        addresses = {address for address, _price in listed}
        for entry in confirming:
            address = str(entry.get("lot_address") or "").strip()
            if not address or (address, price(entry)) in listed:
                continue
            for line in lines:
                if (address + ",") not in line or (address in addresses
                                                   and _money(price(entry)) not in line):
                    continue
                self.report.fail(where, "needs confirming %s (%s, %s) is written into the "
                                        "Kommo note as if shortlisted"
                                 % (cat.name(_hash(entry)), address, _money(price(entry))))
                break

    def card_invariants(self, where, used, shortlist, confirming):
        state = _searched_state(used)
        low = _num(used.get("budget_min")) or 0.0
        high = _num(used.get("budget_max"))
        cat = self.catalogue
        for kind, entries in (("shortlisted", shortlist), ("needs confirming", confirming)):
            for entry in entries:
                h = _hash(entry)
                if not h:
                    self.report.fail(where, "a %s card has no content_hash" % kind)
                    continue
                name = cat.name(h)
                if state and str(entry.get("state") or "").strip().upper() != state:
                    self.report.fail(where, "%s %s is in %s, not the %s searched" % (
                        kind, name, entry.get("state"), state))
                price = _num(entry.get("advertised_package_price"))
                if price is None or price < low - 0.5 or (high and price > high + 0.5):
                    self.report.fail(where, "%s %s is priced %s, outside %s-%s" % (
                        kind, name, _money(price), _money(low), _money(high)))
                card_date = str(entry.get("date_checked") or "").strip()
                row = cat.row(h)
                held = cat.date(h)
                if row is None or held is None:
                    self.report.fail(where, "%s %s is not in %s" % (kind, name, cat.label))
                else:
                    if card_date != held:
                        self.report.fail(where, "%s %s: the card says checked %r, %s says %r"
                                         % (kind, name, card_date, cat.label, held))
                    said = str(row.get("availability_status") or "").strip()
                    if said.lower() in NOT_AVAILABLE:
                        self.report.fail(where, "%s %s is shown, but its row says %r"
                                         % (kind, name, said))
                if kind != "shortlisted":
                    continue
                age = _age(card_date, self.today)
                if age is None or age > FRESH_DAYS:
                    self.report.fail(where, "shortlisted %s was checked %r, more than %d days "
                                            "ago" % (name, card_date, FRESH_DAYS))
                sibling = gone_sibling(cat, h) if row is not None else None
                if sibling is not None:
                    self.report.fail(where, "sibling: shortlisted %s says %r (%s), but %s, the "
                                            "same line on the same document read %s, says %r"
                                     % (name, row.get("availability_status"),
                                        row.get("date_checked"), cat.name(_hash(sibling)),
                                        sibling.get("date_checked"),
                                        sibling.get("availability_status")))
                # A row filed in the state SEARCHED (the plan: "a QLD shortlist row"); an
                # Any-state search would warn about every state's rows. Once per row a run.
                if (self.locality is not None and row is not None and state
                        and h not in self.warned_locality):
                    self.warned_locality.add(h)
                    hit = self.locality(h)
                    if hit:
                        self.report.warn(where, "text: shortlisted %s is filed %s from its "
                                                "suburb column (%s), but its own text names "
                                                "%s (%s)" % (name, row.get("state"),
                                                             row.get("state_source"), hit[0],
                                                             hit[1]))

    def never(self, where, listed, shortlist, confirming):
        for kind, entries in (("shortlist", shortlist), ("needs_confirming", confirming)):
            for entry in entries:
                h = _hash(entry)
                for prefix, why in listed.items():
                    if h.startswith(prefix):
                        self.report.fail(where, "%s must never appear, and is in %s: %s"
                                         % (self.catalogue.name(h), kind, why))

    def dawn(self, where, lots, confirming):
        """Each lot on its own: lot 1009 was first read on 19/08, two days after 1228-1231,
        so its window runs two days longer -- judged as one group it stopped being checked
        on the day the earliest lot's window closed."""
        dawn = self.spec["dawn"]
        lapsed, changed, required = [], [], []
        for lot in lots:
            holds, why_not, until = dawn_expectation(self.spec, [lot], self.catalogue,
                                                     self.today)
            if holds:
                required.append(lot)
            elif until is not None and self.today > until:
                lapsed.append((lot, why_not))
            else:
                changed.append((lot, why_not))
        for group, is_lapse in ((lapsed, True), (changed, False)):
            if not group:
                continue
            text = "Dawn lots %s not required to need confirming: %s" % (
                ", ".join(lot for lot, _why in group),
                "; ".join(sorted({why for _lot, why in group})))
            if is_lapse:
                self.report.note(where, text)
            else:
                self.report.warn(where, text + " (update data/colin_searches.json)")
        estate = _norm(dawn["estate"])
        for lot in required:
            price = float(dawn["lots"][lot])
            found = [e for e in confirming
                     if _norm(e.get("estate_context")) == estate
                     and _norm(e.get("lot_number")) == _norm(lot)
                     and abs((_num(e.get("advertised_package_price")) or 0) - price) < 1.0]
            if not found:
                self.report.fail(where, "Dawn lot %s (%s, stated Available, read %s) is not in "
                                        "needs_confirming" % (lot, _money(price),
                                                              dawn["date_checked"]))
            elif found[0].get("date_checked") != dawn["date_checked"]:
                self.report.fail(where, "Dawn lot %s needs confirming dated %r, not its own %r"
                                 % (lot, found[0].get("date_checked"), dawn["date_checked"]))

    def funnel(self, where, resp):
        funnel = (resp.get("search_help") or {}).get("funnel")
        if not isinstance(funnel, dict):
            self.report.fail(where, "search_help.funnel is missing")
            return
        total = (resp.get("shortlist_count") or 0) + (resp.get("rejected_count") or 0)
        if total != funnel.get("in_state_rows"):
            self.report.fail(where, "shortlist_count + rejected_count = %d, but "
                                    "search_help.funnel.in_state_rows = %s"
                             % (total, funnel.get("in_state_rows")))

    def record_id(self, where, expect, record):
        start = expect.get("record_id_startswith")
        if start and not record.startswith(start):
            self.report.fail(where, "research_record_id %r does not start %r" % (record, start))
        for text in expect.get("record_id_contains") or ():
            if text not in record:
                self.report.fail(where, "research_record_id %r lacks %r" % (record, text))
        for text in expect.get("record_id_excludes") or ():
            if text in record:
                self.report.fail(where, "research_record_id %r contains %r" % (record, text))

    def equal_to(self, where, brief, resp, other_id):
        _body, status, other, _s = self.response(other_id)
        if status != 200 or other.get("status") != "success":
            self.report.fail(where, "cannot compare: %s did not succeed" % other_id)
            return
        for key in ("shortlist", "needs_confirming"):
            if key not in resp or key not in other:
                continue
            mine = {_hash(e) for e in resp.get(key) or []}
            theirs = {_hash(e) for e in other.get(key) or []}
            if mine != theirs:
                self.report.fail(where, "%s differs from %s: only here %s, only there %s" % (
                    key, other_id, sorted(h[:11] for h in mine - theirs),
                    sorted(h[:11] for h in theirs - mine)))

    def relaxations_re_post(self, where, body, resp):
        help_ = resp.get("search_help")
        if not isinstance(help_, dict):
            return
        offers = help_.get("relaxations") or []
        if not offers:
            self.report.note(where, "no relaxation priced: %s" % (
                help_.get("relaxations_note") or "none offered"))
        for offer in offers:
            if offer.get("kind") != "rerun":
                continue
            rid = offer.get("id")
            if offer.get("count_status") == "error":
                continue                                # explain_answered failed it
            if offer.get("count_status") != "exact":
                self.report.warn(where, "relaxation %s was %s, not re-posted"
                                 % (rid, offer.get("count_status")))
                continue
            patch = offer.get("brief_patch")
            if not isinstance(patch, dict):
                self.report.fail(where, "relaxation %s is exact but has no brief_patch" % rid)
                continue
            again = {"client_brief": dict(body["client_brief"], **patch)}
            status, payload, _seconds = self.post(again)
            if status != 200 or payload.get("status") != "success":
                self.report.fail(where, "re-posting relaxation %s failed: HTTP %s" % (rid, status))
            elif payload.get("shortlist_count") != offer.get("shortlist_count"):
                self.report.fail(where, "relaxation %s promises %s, re-posting its brief_patch "
                                        "returns %s" % (rid, offer.get("shortlist_count"),
                                                        payload.get("shortlist_count")))

    def relaxations_exact(self, where, resp, wanted):
        help_ = resp.get("search_help") or {}
        offers = {o.get("id"): o for o in help_.get("relaxations") or []}
        if not offers and help_.get("relaxations_note"):
            self.report.note(where, "relaxations not priced: %s" % help_["relaxations_note"])
            return
        for rid in wanted:
            offer = offers.get(rid)
            if offer is None:
                self.report.fail(where, "relaxation %s was not offered" % rid)
            elif offer.get("count_status") != "exact":
                self.report.fail(where, "relaxation %s is %s, not exact"
                                 % (rid, offer.get("count_status")))


# ------------------------------------------------------------ inside the bundle (--snapshot)

def _email_lot_key(rec, catalogue):
    """(document, lot, price) for an emailed row: the twins one list gives when it is parsed
    twice (Dawn 11878/14940: the same email re-parsed on 19/08) share it. None without one."""
    row = catalogue.row(_hash(rec)) or {}
    doc = str(row.get("source_doc") or "").strip()
    lot = _norm(rec.get("lot_number") or row.get("lot_number"))
    price = _num(rec.get("price"))
    if not doc or not lot or not price:
        return None
    return doc, lot, int(round(price))


def obvious_candidates(catalogue, state, low, high, today, build_cells):
    """The obvious rows for a state and price range, picked from the DATABASE: live, in
    the state, priced in range, stated available, at most OBVIOUS_MAX_DAYS old, and not a
    Build Only / Land Only price (by its text, or by its own land and build columns), one
    part of a bigger printed total, or a price-list line that does not print its price
    (build_cells is the bundle's snapshot_build_cells).

    An emailed row ages from the list's FIRST read (critique #27), and a lot's first read
    is the earliest of its live twins on that list: a re-parse stamps its rows with a later
    first_seen, but the list is no younger for it. Aged by its own stamp, 14940 (first
    read 19/08) was still obvious on 02/10 while the search -- rightly, by 11878's 17/08 --
    had stopped offering lot 1228, and the nightly went red over a correct search."""
    live_db = [r for r in catalogue.db.values() if not r.get("superseded_by")]
    first_read = {}
    for rec in live_db:
        if str(rec.get("source_channel") or "").strip().lower() != EMAIL_CHANNEL:
            continue
        key, first = _email_lot_key(rec, catalogue), _day(rec.get("first_seen"))
        if key is not None and first is not None and (key not in first_read
                                                      or first < first_read[key]):
            first_read[key] = first
    out = []
    for rec in live_db:
        if str(rec.get("state") or "").strip().upper() != state:
            continue
        price = _num(rec.get("price"))
        if not price or price <= 0 or price < low or (high and price > high):
            continue
        if str(rec.get("availability_status") or "").strip().lower() not in IS_AVAILABLE:
            continue
        age = _age(rec.get("date_checked"), today)
        if age is None or age < 0 or age > OBVIOUS_MAX_DAYS:
            continue
        if str(rec.get("source_channel") or "").strip().lower() == EMAIL_CHANNEL:
            firsts = [d for d in (_day(rec.get("first_seen")),
                                  first_read.get(_email_lot_key(rec, catalogue)))
                      if d is not None]
            if firsts and (today - min(firsts)).days > OBVIOUS_MAX_DAYS:
                continue
        cells = build_cells(dict(rec))
        if str(cells.get("price_kind") or "").lower() in ("build_only", "land_only"):
            continue
        # The row's own columns saying the price is only the land or only the house
        # ("Wollert Land Price $143,389": land_price is the price, no build price).
        land, build = _num(rec.get("land_price")), _num(rec.get("build_price"))
        if bool(land) != bool(build) and abs((land or build) - price) <= 1.0:
            continue
        # A line of a price list that does not print the price it is stored at (an email
        # sentence stored at $553,284) is not an obvious listing.
        if (cells.get("price_in_text") is False
                and str(rec.get("source_channel") or "").strip().lower() in (EMAIL_CHANNEL,
                                                                             "e-agent")):
            continue
        printed = _num(cells.get("text_price_max"))
        if printed is not None and printed > price * (1 + COMPONENT_TOLERANCE):
            continue
        out.append(rec)
    return out


def obvious_rows_check(C, research, bootstrap, catalogue, snapshot_path):
    """The independent obvious-rows check, as a Run hook. See the module docstring."""

    def picked(state, low, high, today):
        return obvious_candidates(catalogue, state, low, high, today, C.snapshot_build_cells)

    def hook(run, where, brief, body, resp):
        used = resp.get("brief_used") or {}
        state = _searched_state(used)
        if not state:
            run.report.note(where, "obvious-rows check needs a state")
            return
        low = _num(used.get("budget_min")) or 0.0
        high = _num(used.get("budget_max"))
        rows_ = picked(state, low, high, run.today)
        shown = {_hash(e): "shortlist" for e in resp.get("shortlist") or []}
        shown.update({_hash(e): "needs_confirming" for e in resp.get("needs_confirming") or []})
        visible = {(str(e.get("lot_address") or "").strip().lower(),
                    round(_num(e.get("advertised_package_price")) or 0))
                   for e in (resp.get("shortlist") or []) + (resp.get("needs_confirming") or [])}
        pending = [r for r in rows_ if _hash(r) not in shown]
        verdicts = {}
        if pending:
            brief_dict = research._normalised_brief(used)[0]
            wanted = {_hash(r) for r in pending}
            for row, verdict in C.explain_rows(
                    brief_dict, catalogue.rows,
                    prepared=C.prepared_for_snapshot(catalogue.rows, snapshot_path),
                    admit=C.ADMITTABLE_GATES):
                h = _hash(row)
                if h in wanted and (h not in verdicts or not row.get("superseded_by")):
                    verdicts[h] = verdict
        tally = {}
        listed_short = ((resp.get("needs_confirming_count") or 0)
                        > len(resp.get("needs_confirming") or []))
        for rec in rows_:
            h = _hash(rec)
            name = catalogue.name(h)
            if h in shown:
                outcome = shown[h]
            elif catalogue.row(h) is None:
                run.report.fail(where, "obvious row %s is in the database but not in the "
                                       "snapshot" % name)
                continue
            else:
                outcome, detail = _obvious_outcome(verdicts.get(h), visible, research,
                                                   bootstrap, used, listed_short)
                if outcome in WARN_VERDICTS:
                    run.report.warn(where, "obvious row %s is hidden as %s (data quality)"
                                    % (name, outcome))
                elif outcome == "needs_confirming_unlisted":
                    run.report.warn(where, "obvious row %s needs confirming beyond the "
                                           "listed cap" % name)
                elif outcome not in ALLOWED_VERDICTS:
                    run.report.fail(where, "obvious row %s -- %s, %s, stated %r, checked %s -- "
                                           "is hidden as %s%s" % (
                                               name, rec.get("state"), _money(rec.get("price")),
                                               rec.get("availability_status"),
                                               rec.get("date_checked"), outcome,
                                               (": " + detail) if detail else ""))
            tally[outcome] = tally.get(outcome, 0) + 1
        run.report.note(where, "obvious rows: %d (%s)" % (
            len(rows_), ", ".join("%s %d" % kv for kv in sorted(tally.items())) or "none"))

    return hook


def _obvious_outcome(verdict, visible, research, bootstrap, used, listed_short=False):
    """(outcome, detail) for an obvious row the response does not show.

    A stale row no guard hides and no twin stands in for belongs in needs_confirming: when
    the list was cut at its cap it may simply be past the cap; otherwise it was lost, and
    the scorer is asked why."""
    if verdict is None:
        return "not_explained", ""
    gate = verdict.get("gate")
    package = verdict.get("package") or {}
    twin = (str(package.get("lot_address") or "").strip().lower(),
            round(_num(package.get("advertised_package_price")) or 0))
    if gate == "same_listing_collapsed":
        if twin in visible:
            return "collapsed_into_visible", ""
        return gate, "merged into a twin that is not shown"
    if gate != "scored":
        return gate, ""
    guard = package.get("confirm_guard")
    if guard:
        return guard, ""
    if twin in visible:
        return "collapsed_into_visible", ""
    if package.get("confirm") and listed_short:
        return "needs_confirming_unlisted", ""
    try:
        agent = bootstrap.get_agent()
        brief_dict = research._normalised_brief(used)[0]
        with contextlib.redirect_stdout(io.StringIO()):
            processed, rejected = agent.score_candidates(brief_dict, [dict(package)])
        if rejected:
            return "rejected_by_the_scorer", str(rejected[0].get("reason") or "")[:200]
        return "scored_not_shown", "passed the scorer and is not on the shortlist"
    except Exception as exc:                                          # noqa: BLE001
        return "scored_not_shown", "scoring it again raised %s" % type(exc).__name__


def locality_check(catalogue, report):
    """14905's shape, as a WARN: filed in a state by its suburb column alone, with its own
    text naming a locality of one other state the catalogue stocks (state_resolver's rule).

    geo imports config, whose module-level mkdir the read-only bundle refuses: the caller
    imports config through _bootstrap first, as the endpoint does."""
    try:
        import state_resolver
        from geo import SuburbGeoIndex
        geo = SuburbGeoIndex()
    except Exception as exc:                                          # noqa: BLE001
        report.warn("setup", "the text-locality warning is off: %s: %s"
                    % (type(exc).__name__, exc))
        return None
    stocked = {(str(r.get("suburb") or "").strip().lower(),
                str(r.get("state") or "").strip().upper())
               for r in catalogue.rows
               if not r.get("superseded_by") and r.get("suburb")
               and not str(r.get("state_source") or "").strip().lower().startswith("suburb")}

    def check(h):
        row = catalogue.row(h) or {}
        if not str(row.get("state_source") or "").strip().lower().startswith("suburb"):
            return None
        try:
            return state_resolver.text_locality_elsewhere(
                catalogue.text(h), row.get("state"), geo, stocked,
                estate=row.get("estate_name") or "")
        except Exception:                                             # noqa: BLE001
            return None
    return check


def _read_only_bundle(bundle):
    """Deny mkdir inside the bundle for the rest of this process, as Vercel's read-only
    /var/task does (the build's own import probe does the same)."""
    root = os.path.abspath(str(bundle))
    real_mkdir = pathlib.Path.mkdir

    def mkdir(self, mode=0o777, parents=False, exist_ok=False):
        path = os.path.abspath(str(self))
        if path == root or path.startswith(root + os.sep):
            raise OSError(errno.EROFS, "Read-only file system (the deployed bundle)", str(self))
        return real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)
    pathlib.Path.mkdir = mkdir


def _freeze_today(C, day):
    base = C.datetime

    class _Frozen(base):
        @classmethod
        def now(cls, tz=None):
            return cls(day.year, day.month, day.day, 12, 0, tzinfo=tz)
    C.datetime = _Frozen


def in_process_post(research):
    def post(body):
        payload = json.loads(json.dumps(body))
        started = time.monotonic()
        captured = io.StringIO()
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(captured):
                out = research.run_research(payload)
            # Serialised as the endpoint serialises it (handler._send).
            return 200, json.loads(json.dumps(out, default=str)), time.monotonic() - started
        except research.BadRequest as exc:
            return 400, {"status": "error", "message": str(exc)}, time.monotonic() - started
        except Exception as exc:                                      # noqa: BLE001
            tail = (captured.getvalue() + traceback.format_exc()).strip().splitlines()[-5:]
            return 500, {"status": "error", "message": "%s: %s | %s" % (
                type(exc).__name__, exc, " / ".join(tail))}, time.monotonic() - started
    return post


def run_in_bundle(args, report):
    """The child process of --snapshot: everything imported from the bundle."""
    started = time.monotonic()
    bundle = Path(args.bundle).resolve()
    snapshot = Path(args.snapshot).resolve()
    app = str(APP).lower()
    sys.path[:] = [str(bundle), str(bundle / "_lib")] + [
        p for p in sys.path
        if p and os.path.abspath(p).lower() != app]
    _read_only_bundle(bundle)
    import _bootstrap
    import _candidates as C
    import research
    # config the way the endpoint imports it: its module-level mkdir tolerated on a
    # read-only bundle, its writable paths pointed at SPB_SCRATCH.
    _bootstrap.get_config()
    import config
    import geo
    for module in (research, C, _bootstrap, config, geo):
        if not os.path.abspath(module.__file__).lower().startswith(str(bundle).lower()):
            report.fail("setup", "%s was imported from outside the bundle" % module.__name__)
            return 1
    today = _day(args.today) if args.today else C._now_aest().date()
    if args.today:
        if today is None:
            report.fail("setup", "--today must be dd/mm/yyyy")
            return 1
        _freeze_today(C, today)
    rows, path = C.load_snapshot()
    if os.path.abspath(path).lower() != str(snapshot).lower():
        report.fail("setup", "the bundle read a different snapshot than --snapshot")
        return 1
    catalogue = Catalogue(rows, read_db_rows(args.db))
    report.header("search_regressions --snapshot: %s rows, bundle %s, today %s, checks %s"
                  % (format(len(rows), ","), bundle.name, today.strftime("%d/%m/%Y"),
                     args.checks))
    run = Run(load_spec(args.briefs), in_process_post(research), catalogue, today, report,
              mode="snapshot", checks=args.checks,
              obvious=obvious_rows_check(C, research, _bootstrap, catalogue, path),
              locality=locality_check(catalogue, report), only=args.only)
    run.execute()
    return _finish(report, started)


def _finish(report, started):
    verdict = "PASS" if not report.failures else "FAIL"
    report.header("RESULT: %s -- %d failure(s), %d warning(s), %.0f s"
                  % (verdict, report.failures, report.warnings, time.monotonic() - started))
    return 0 if not report.failures else 1


# ----------------------------------------------------------------------- the two modes

def _tree(root):
    out = {}
    for base, dirs, files in os.walk(root):
        for name in dirs:
            out[os.path.relpath(os.path.join(base, name), root)] = "dir"
        for name in files:
            full = os.path.join(base, name)
            st = os.stat(full)
            out[os.path.relpath(full, root)] = (st.st_size, st.st_mtime_ns)
    return out


def run_snapshot(args, report):
    """Copy the database, then replay in a child process that imports only the bundle."""
    started = time.monotonic()
    snapshot = Path(args.snapshot).resolve()
    bundle = Path(args.bundle).resolve() if args.bundle else snapshot.parent / "api"
    db = Path(args.db).resolve() if args.db else _default_db()
    for what, path in (("snapshot", snapshot), ("database", db)):
        if not path.is_file():
            report.fail("setup", "no %s at %s" % (what, path))
            return _finish(report, started)
    if not (bundle / "research.py").is_file():
        report.fail("setup", "no research bundle at %s (build_web writes it beside "
                             "stock.json)" % bundle)
        return _finish(report, started)
    before = _tree(bundle)
    with tempfile.TemporaryDirectory(prefix="spb_regressions_",
                                     ignore_cleanup_errors=True) as tmp:
        copy = Path(tmp) / "catalogue-copy.db"
        try:
            copy_db_read_only(db, copy)
        except sqlite3.Error as exc:
            report.fail("setup", "could not copy the database read-only: %s" % exc)
            return _finish(report, started)
        env = dict(os.environ)
        for name in ("GROQ_API_KEY", "SPB_COMP_PROVIDER"):
            env.pop(name, None)
        env.update(SPB_DATABASE_PATH=str(copy), SPB_SNAPSHOT_JSON=str(snapshot),
                   PYTHONDONTWRITEBYTECODE="1", SPB_SCRATCH=str(Path(tmp) / "scratch"),
                   PYTHONIOENCODING="utf-8")
        env.pop("SPB_SNAPSHOT_DB", None)
        cmd = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--in-bundle",
               "--snapshot", str(snapshot), "--db", str(copy), "--bundle", str(bundle),
               "--briefs", str(Path(args.briefs).resolve()), "--checks", args.checks]
        if args.today:
            cmd += ["--today", args.today]
        for brief_id in args.only or ():
            cmd += ["--only", brief_id]
        report.out.flush()
        try:
            report.out.fileno()
            real_file = True
        except (AttributeError, OSError, io.UnsupportedOperation):
            real_file = False
        if real_file:
            done = subprocess.run(cmd, cwd=tmp, env=env, stdout=report.out)
        else:
            done = subprocess.run(cmd, cwd=tmp, env=env, stdout=subprocess.PIPE,
                                  text=True, encoding="utf-8", errors="replace")
            report.out.write(done.stdout)
        code = done.returncode
    after = _tree(bundle)
    if after != before:
        changed = sorted(set(after) ^ set(before)
                         | {k for k in after if k in before and after[k] != before[k]})
        report.fail("setup", "the replay changed the bundle that is about to be deployed: %s"
                    % ", ".join(changed[:5]))
        code = 1
    report.header("search_regressions: exit %d after %.0f s" % (0 if code == 0 else 1,
                                                                 time.monotonic() - started))
    return 0 if code == 0 else 1


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the service key to wherever it points. Refuse it: the 3xx
    comes back as an HTTPError and the brief fails with its status."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_post(base_url, key, timeout=60.0):
    url = base_url.rstrip("/") + "/api/research"
    opener = urllib.request.build_opener(_NoRedirects)

    def post(body):
        request = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", SERVICE_KEY_HEADER: key})
        started = time.monotonic()
        try:
            with opener.open(request, timeout=timeout) as answer:
                status, raw = answer.status, answer.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read() or b""
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return 0, {"status": "error", "message": "no response (%s)" % type(exc).__name__}, \
                time.monotonic() - started
        seconds = time.monotonic() - started
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            payload = {"status": "error",
                       "message": "not JSON: %s" % raw[:200].decode("utf-8", "replace")}
        return status, payload, seconds
    return post


def run_live(args, report):
    started = time.monotonic()
    if not args.key_file:
        report.fail("setup", "--live needs --key-file")
        return _finish(report, started)
    try:
        # utf-8-sig: a key file saved by Notepad starts with a BOM, which would become part
        # of the header (not latin-1: every POST fails) and of the text scrubbed from output.
        key = Path(args.key_file).read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeDecodeError) as exc:
        report.fail("setup", "could not read the key file (%s)" % type(exc).__name__)
        return _finish(report, started)
    report.secrets.append(key)
    if not key:
        report.fail("setup", "the key file is empty")
        return _finish(report, started)
    target = urllib.parse.urlsplit(args.live)
    if target.scheme != "https" and not (target.scheme == "http"
                                         and target.hostname in LOOPBACK_HOSTS):
        # Sent before any redirect could be refused: http://app... would put the key on
        # the wire in clear on the first request, and Vercel's 308 to https comes after.
        report.fail("setup", "--live must be an https URL (plain http only to this machine), "
                             "so the service key is never sent in clear")
        return _finish(report, started)
    published = Path(args.published_snapshot).resolve() if args.published_snapshot \
        else DEFAULT_SNAPSHOT
    if not published.is_file():
        report.fail("setup", "no published snapshot at %s to compare card dates with "
                             "(critique #13: the deployed stock.json is not readable with "
                             "the service key)" % published)
        return _finish(report, started)
    catalogue = Catalogue(read_snapshot_rows(published), None,
                          label="the published stock.json")
    today = _day(args.today) if args.today else datetime.now(AEST).date()
    if today is None:
        report.fail("setup", "--today must be dd/mm/yyyy")
        return _finish(report, started)
    report.header("search_regressions --live: %s rows published, today %s, checks %s"
                  % (format(len(catalogue.rows), ","), today.strftime("%d/%m/%Y"),
                     args.checks))
    run = Run(load_spec(args.briefs), http_post(args.live, key), catalogue, today, report,
              mode="live", checks=args.checks, only=args.only)
    run.execute()
    return _finish(report, started)


def _default_db():
    env = os.environ.get("SPB_DATABASE_PATH")
    return Path(env) if env else APP / "spb_research_audit.db"


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # Not 2: run_daily.py reads a step's exit 2 as "a harvest channel returned nothing".
        self.print_usage(sys.stderr)
        print("search_regressions: error: %s" % message, file=sys.stderr)
        sys.exit(1)


def parser():
    ap = _Parser(description="Replay Colin's searches and check what must hold for each.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshot", help="stock.json built tonight; the bundle is beside it")
    mode.add_argument("--live", help="the deployed site's base URL")
    ap.add_argument("--db", help="--snapshot: the database (default SPB_DATABASE_PATH, then "
                                 "the app's); copied read-only, never written")
    ap.add_argument("--bundle", help="--snapshot: the research bundle (default: api/ beside "
                                     "the snapshot)")
    ap.add_argument("--key-file", help="--live: a file holding the service key")
    ap.add_argument("--published-snapshot",
                    help="--live: the stock.json that was published (default "
                         "vercel_site/stock.json)")
    ap.add_argument("--briefs", default=str(BRIEFS))
    ap.add_argument("--only", action="append", default=[],
                    help="run only these brief ids (repeatable, or comma-separated)")
    ap.add_argument("--checks", choices=("nightly", "full"),
                    help="nightly: re-post relaxations for brief #1 only; full: for every "
                         "brief (default: nightly for --snapshot, full for --live)")
    ap.add_argument("--today", help="dd/mm/yyyy (default: today in AEST)")
    ap.add_argument("--in-bundle", action="store_true", help=argparse.SUPPRESS)
    return ap


def main(argv=None, out=None):
    args = parser().parse_args(argv)
    args.only = [i.strip() for part in args.only for i in part.split(",") if i.strip()]
    if args.checks is None:
        args.checks = "full" if args.live else "nightly"
    report = Report(out)
    try:
        spec = load_spec(args.briefs)
        unknown = [i for i in args.only if i not in {b["id"] for b in spec["briefs"]}]
        if unknown:
            raise ValueError("no such brief: %s" % ", ".join(unknown))
    except (OSError, ValueError, KeyError) as exc:
        report.fail("setup", "briefs %s: %s" % (args.briefs, exc))
        return 1
    if args.live:
        return run_live(args, report)
    if args.in_bundle:
        try:
            return run_in_bundle(args, report)
        except Exception as exc:                                      # noqa: BLE001
            report.fail("setup", "the replay raised %s: %s" % (type(exc).__name__, exc))
            for line in traceback.format_exc().strip().splitlines()[-8:]:
                report.note("setup", line)
            return 1
    return run_snapshot(args, report)


if __name__ == "__main__":
    sys.exit(main())
