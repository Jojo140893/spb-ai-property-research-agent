"""trace_listing.py -- explain any listing Colin names against the catalogue. Read-only.

Part C, C0. Colin knows QLD listings under $770k the Research tab never showed him. For
each one the next piece of work depends on WHERE it was lost:

  intake gap         no row in the catalogue holds it (harvest or email never read it)
  parse gap          a row holds it, but a field is wrong or missing (state, suburb,
                     price, facts), or the row is a fragment of a list, not a listing
  hidden by a rule   the row is right and a search rule hid it (stale, sold, the list
                     does not say it is available, a sold-stock guard)
  outside the brief  over the budget, another state, a scorer rejection
  needs confirming   shown to a consultant only to confirm
  shortlisted        shown

    python trace_listing.py --builder "Shape Homes" --estate Dawn --lot 1228 \
        --price 721900 --state QLD --budget 750000 [--today 16/09/2026]
    python trace_listing.py --id 15899 --state QLD --budget 750k
    python trace_listing.py --hash v2:1c5ca48e --state QLD --budget 750000 --build
    python trace_listing.py --listings colin.csv --state QLD --budget 770000

--listings traces several listings under one search (a CSV with the header builder, estate,
lot, price and an optional label saying how each reached Colin, or a JSON list of the same)
and ends with one verdict line per listing -- the line that decides whether the next work
is harvest, email intake or the parser.

WHAT IT READS. The rows a search sees are the SNAPSHOT's (states borrowed, suburbs
resolved, source_doc derived at build time), not the database table's, so a listing is
explained from snapshot rows: vercel_site/stock.json (or SPB_SNAPSHOT_JSON, or --snapshot),
or with --build a snapshot built by build_web from a copy of the database. Database-only
facts (id, capture times, the document a row came from, source_text) are joined by
content_hash under row['db'] with _candidates.join_db_fields. The database itself is only
ever opened with sqlite's mode=ro; --build copies it first, through a mode=ro connection.

WHAT IT RUNS. The search's own code, never a copy of its rules: the brief goes through
research._normalised_brief; every row's verdict comes from _candidates.explain_rows (the
same _gate build_packages runs, with the needs-confirming admission pass); rows that reach
scoring are scored by the deployed pipeline object (build_packages -> DedupeEngine ->
score_candidates), exactly as api/research.py does. Superseded rows are traced to what
superseded them. Nothing matching prints "not in the catalogue" and the nearest rows.

NEVER PRINTS A URL. A document is named by its kind, file name, sheet position, e-mail
subject and its short source_doc hash -- and not by a file name that is itself a key (an
E-Agent file is "069fe0_<32 hex>.pdf", the secret half of its link). Every printed value
is passed through _no_links.
"""
import argparse
import difflib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

APP = Path(__file__).resolve().parent
for _p in (str(APP / "api"), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _candidates as C  # noqa: E402

# ------------------------------------------------------------------------ constants

# Database columns a trace shows beside a snapshot row. Joined read-only by content_hash,
# under row['db'], so no gate can read them (see _candidates.join_db_fields).
TRACE_DB_FIELDS = ("id", "source_text", "checked_at", "first_seen", "last_seen",
                   "source_url", "stocklist_file", "source_sheet", "source_row",
                   "source_page", "superseded_at")
# What a database row the snapshot does not hold is traced with.
DB_ONLY_FIELDS = ("id", "content_hash", "builder_name", "estate_name", "lot_number",
                  "lot_address", "street_address", "suburb", "state", "state_source",
                  "price", "availability_status", "date_checked", "source_channel",
                  "superseded_by", "source_url", "stocklist_file", "source_sheet",
                  "source_row", "source_page", "first_seen", "checked_at")

# A price Colin quotes matches a row within this share; "near" within NEAR_PRICE_SHARE.
PRICE_MATCH_SHARE = 0.01
NEAR_PRICE_SHARE = 0.05
NEAR_LOT = 2
ESTATE_RATIO = 0.8

KIND_LABELS = {
    "shown": "shortlisted",
    "confirm": "needs confirming",
    "gate": "hidden by a rule",
    "brief": "outside the brief",
    "parse": "parse gap",
    "snapshot": "not in the snapshot",
    "intake": "intake gap",
}

# Why a sold-stock guard or the exceptions-only admission keeps a row hidden.
HIDDEN_BECAUSE = {
    "newer_capture_disagrees": "a later capture of the same lot says it has gone",
    "newer_capture_exists": "a later capture of the same lot at the same price exists",
    "newer_capture_repriced": ("a later capture of the same lot, on the same line, asks "
                               "another price"),
    "document_reread_without_row": "document re-read without this row",
    "builder_documents_reread": ("the builder's other documents were re-read and this "
                                 "row's document was not"),
    "newer_list_from_sender": "a newer list from the same sender was read after it",
    "over_confirm_limit": ("older than %d days, the needs-confirming limit"
                           % C.CONFIRM_MAX_DAYS),
    "no_capture_date": "no capture date recorded",
    "no_document": "availability not stated, and the row names no document",
    "document_states_available": ("availability not stated on a list that says "
                                  "'Available' beside other lots"),
    "document_marks_no_exceptions": ("availability not stated, and its list never marks "
                                     "a lot as gone"),
    "text_marks_unavailable": "its own line says it has gone",
    "no_price_evidence": "availability not stated, and the snapshot has no price evidence",
    "price_not_in_text": "availability not stated, and its price is not printed on its line",
    "larger_total_in_text": ("availability not stated, and its line prints a larger total "
                             "(the price is one part of it)"),
    "build_only": "availability not stated, and the price is build only",
    "land_only": "availability not stated, and the price is land only",
    "commercial": "availability not stated, and the price is commercial",
}
# The same price refusals on a stale row: availability was stated, the price is the problem.
HIDDEN_BECAUSE_STALE = {
    "price_not_in_text": "its price is not printed on its own line",
    "larger_total_in_text": "its line prints a larger total (the price is one part of it)",
    "build_only": "the price is build only",
    "land_only": "the price is land only",
    "commercial": "the price is commercial (an office, shop or warehouse)",
}

def _hidden_because(guard, confirm):
    """The words for a guard: a price refusal on a row whose availability WAS stated (a
    stale row) is about the price alone."""
    if "availability_unstated" not in (confirm or ()) and guard in HIDDEN_BECAUSE_STALE:
        return HIDDEN_BECAUSE_STALE[guard]
    return HIDDEN_BECAUSE.get(guard, guard)


# A scheme ("https:/", even cut short), "www.", an e-mail address, or a bare domain with a
# path ("docs.google.com/spreadsheets/d/...", "dropbox.com/scl/fi/..."), which a row's text
# can carry without its scheme. A bare domain alone ("hattan.com.au", a builder_name) is a
# name, not a link, and stays.
_LINK = re.compile(r"(?:[a-z][a-z0-9+.-]*:/+|www\.)\S*|\S+@\S+\.\S+"
                   r"|\b[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}(?::\d+)?/\S*", re.I)
# A file name that is a random key, not a title: E-Agent's files are named
# "<6 hex>_<32 hex>.xlsx", and that name is the unguessable part of the link it came from.
# Printed, it is the link less its host. A run of 16 hex digits, or of 24 letters and
# digits mixing both, is a key; "Price List 24.06.26" is a title. See _key_like.
_HEX_RUN = re.compile(r"[0-9a-f]{16,}", re.I)
_ALNUM_RUN = re.compile(r"[A-Za-z0-9]{24,}")
_WORD = re.compile(r"[a-z0-9]+")
_LOT_IN_TEXT = re.compile(r"\blot\s*([a-z]?\d+[a-z]?)\b", re.I)
_ESTATE_STOPWORDS = {"estate", "the", "stage", "at", "of", "and", "release", "precinct"}
_GENERIC_BUILDER_WORDS = {"homes", "home", "group", "builders", "builder", "building",
                          "buildings", "construction", "constructions", "developments",
                          "development", "pty", "ltd", "the", "and", "australia", "qld",
                          "nsw", "vic", "properties", "property", "living", "residential"}
_DOC_EXTENSIONS = (".pdf", ".xlsx", ".xls", ".xlsm", ".csv", ".docx", ".doc")
# How a warning says the row's text names a place in another state (see Tracer.warnings).
_NAMES_ELSEWHERE = "; its text names "


def _no_links(value):
    """Text with every link and e-mail address replaced. The last thing before print."""
    return _LINK.sub("[link removed]", str("" if value is None else value))


def _key_like(stem):
    """Whether a file name's stem is a key rather than a title (see _HEX_RUN)."""
    return bool(_HEX_RUN.search(stem)) or any(
        re.search(r"\d", run) and re.search(r"[A-Za-z]", run)
        for run in _ALNUM_RUN.findall(stem))


def _money(value):
    number = C._num(value)
    return "$%s" % format(number, ",.0f") if number else "no price"


def _squash(value):
    return "".join(_WORD.findall(str(value or "").lower()))


def _norm_lot(value):
    text = str(value or "").strip().lower()
    text = re.sub(r"^lot\s*", "", text)
    return "".join(text.split())


def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------- the catalogue

class Catalogue:
    """Snapshot rows joined to database-only fields, plus database rows the snapshot lacks."""

    def __init__(self, rows, db_only, notes, snapshot_label, db_label):
        self.rows = rows
        self.db_only = db_only
        self.notes = notes
        self.snapshot_label = snapshot_label
        self.db_label = db_label
        self.by_hash = {}
        for row in rows:
            self.by_hash.setdefault(str(row.get("content_hash") or ""), row)
        self._prepared = None

    @property
    def prepared(self):
        if self._prepared is None:
            self._prepared = C.prepare_rows(self.rows)
        return self._prepared


def _ro_uri(db_path):
    return "file:%s?mode=ro" % Path(db_path).as_posix()


def _db_columns(db_path):
    conn = sqlite3.connect(_ro_uri(db_path), uri=True)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(buildings)")}
    finally:
        conn.close()


def _db_only_rows(db_path, have, known_hashes):
    cols = [c for c in DB_ONLY_FIELDS if c in have]
    if "content_hash" not in cols:
        return []
    conn = sqlite3.connect(_ro_uri(db_path), uri=True)
    try:
        conn.row_factory = sqlite3.Row
        out = []
        for rec in conn.execute("SELECT %s FROM buildings" % ", ".join(cols)):
            if str(rec["content_hash"] or "").strip() not in known_hashes:
                row = dict(rec)
                row["db"] = {k: row.get(k) for k in TRACE_DB_FIELDS if k in row}
                out.append(row)
        return out
    finally:
        conn.close()


def load_catalogue(snapshot_path, db_path=None):
    """Catalogue from a stock.json and (optionally) the database, opened mode=ro."""
    snapshot_path = Path(snapshot_path)
    rows = C._read_json(snapshot_path)
    notes = []
    db_only = []
    db_label = "none"
    if db_path and Path(db_path).is_file():
        have = _db_columns(db_path)
        fields = tuple(f for f in TRACE_DB_FIELDS if f in have)
        rows = C.join_db_fields(rows, db_path, fields=fields)
        known = {str(r.get("content_hash") or "").strip() for r in rows}
        db_only = _db_only_rows(db_path, have, known)
        unjoined = sum(1 for r in rows if r.get("db") is None)
        db_label = "%s (opened read-only)" % Path(db_path).name
        if unjoined or db_only:
            notes.append("the snapshot and the database disagree: %d snapshot row(s) are "
                         "not in the database and %d database row(s) are not in the "
                         "snapshot -- one is older than the other; --build traces a "
                         "snapshot built from this database" % (unjoined, len(db_only)))
        filled = _fill_build_time_cells(rows)
        if filled:
            notes.append("this snapshot predates the price-evidence, emailed-list or line "
                         "cells; computed for %d row(s) from the database, exactly as "
                         "build_web computes them" % filled)
    else:
        rows = [dict(r, db=None) for r in rows]
        notes.append("no database: ids, documents and database-only rows are not shown"
                     + (" (%s not found)" % Path(db_path).name if db_path else ""))
    return Catalogue(rows, db_only, notes, "%s (%s rows)" % (snapshot_path.name,
                                                             format(len(rows), ",")),
                     db_label)


def _fill_build_time_cells(rows):
    """Compute build_web's price-evidence, emailed-list and line cells for a snapshot built
    before they existed, from the joined database columns, with the function build_web and
    the SQLite reader share (snapshot_build_cells). Only the cells the snapshot lacks are
    filled: one built before line_key keeps the price evidence it was published with.
    Returns how many rows were filled."""
    missing = [f for f in C._COMPUTED_SNAPSHOT_FIELDS if not rows or f not in rows[0]]
    if not rows or not missing:
        return 0
    filled = 0
    for row in rows:
        db = row.get("db")
        if not db:
            row.update(dict.fromkeys(missing))
            continue
        inputs = {k: db.get(k) for k in ("source_text", "source_url", "first_seen")}
        cells = C.snapshot_build_cells({k: v for k, v in row.items() if k != "db"}
                                       | inputs)
        row.update({f: cells.get(f) for f in missing})
        filled += 1
    return filled


def build_snapshot(db_path, work_dir):
    """stock.json built by build_web from a COPY of the database, in work_dir.

    The copy is taken through a mode=ro connection (sqlite's backup), so the database
    given is never opened for writing, and build_web runs in a child process pointed at
    the copy by SPB_DATABASE_PATH. Returns (stock.json path, copy path).
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    copy = work_dir / "catalogue-copy.db"
    src = sqlite3.connect(_ro_uri(db_path), uri=True)
    try:
        dst = sqlite3.connect(str(copy))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    out = work_dir / "site"
    env = dict(os.environ, SPB_DATABASE_PATH=str(copy), PYTHONDONTWRITEBYTECODE="1")
    code = ("import sys; from pathlib import Path; import build_web; "
            "build_web.build(Path(sys.argv[1]), with_assets=False)")
    done = subprocess.run([sys.executable, "-c", code, str(out)], cwd=str(APP), env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if done.returncode != 0:
        tail = "\n".join((done.stderr or done.stdout or "").strip().splitlines()[-8:])
        raise RuntimeError("build_web failed on the database copy:\n" + _no_links(tail))
    return out / "stock.json", copy


# ------------------------------------------------------------------------- the query

class Query:
    def __init__(self, builder=None, estate=None, lot=None, price=None, ids=(), hashes=()):
        self.builder = (builder or "").strip()
        self.estate = (estate or "").strip()
        self.lot = _norm_lot(lot)
        self.price = price
        self.ids = [int(i) for i in ids or ()]
        self.hashes = [h.strip() for h in hashes or () if h.strip()]

    def criteria(self):
        return [c for c in ("builder", "estate", "lot", "price") if getattr(self, c)]

    def describe(self):
        parts = []
        if self.builder:
            parts.append('builder "%s"' % self.builder)
        if self.estate:
            parts.append('estate "%s"' % self.estate)
        if self.lot:
            parts.append("lot %s" % self.lot)
        if self.price:
            parts.append("price %s" % _money(self.price))
        parts += ["id %d" % i for i in self.ids]
        parts += ["hash %s" % h for h in self.hashes]
        return ", ".join(parts)


def _db(row):
    return row.get("db") or {}


def document_label(row):
    """The document a row came from, named without its URL."""
    db = _db(row)
    ref = str(db.get("source_url") or "").strip()
    parts = []
    if ref and not re.match(r"^[a-z][a-z0-9+.-]*:/", ref, re.I):
        subject = re.sub(r"^\s*email\s*:\s*", "", ref, flags=re.I)
        parts.append('email "%s"' % subject if str(row.get("source_channel") or "")
                     .strip().lower() == C.EMAIL_CHANNEL or ref.lower().startswith("email:")
                     else '"%s"' % subject)
    elif ref:
        parts.append(_web_document_kind(ref))
    name = _file_name(db.get("stocklist_file")) or _file_name(ref)
    if name:
        parts.append('file "%s"' % name)
    if db.get("source_sheet"):
        parts.append('sheet "%s"' % db.get("source_sheet"))
    if db.get("source_row") not in (None, ""):
        parts.append("row %s" % db.get("source_row"))
    if db.get("source_page") not in (None, ""):
        parts.append("page %s" % db.get("source_page"))
    doc = str(row.get(C._DOC_KEY) or "").strip()
    if doc:
        parts.append("[doc %s]" % doc)
    return _no_links(", ".join(parts) or "no document recorded")


def _web_document_kind(url):
    try:
        parts = urlsplit(url)
        host, path = (parts.hostname or "").lower(), parts.path.lower()
    except ValueError:
        return "web document"
    if "docs.google.com" in host:
        return "Google Sheet" if "/spreadsheets/" in path else "Google document"
    for needle, kind in (("drive.google.com", "Google Drive file"),
                         ("dropbox", "Dropbox file"), ("smartsheet", "Smartsheet"),
                         ("e-agent", "E-Agent file"), ("wixstatic", "E-Agent file"),
                         ("proxima", "Proxima portal"), ("sharepoint", "SharePoint file"),
                         ("onedrive", "OneDrive file"), ("1drv", "OneDrive file")):
        if needle in host:
            return kind
    return "web document"


def _file_name(ref):
    ref = str(ref or "").strip()
    if not ref:
        return ""
    try:
        path = urlsplit(ref).path if re.match(r"^[a-z][a-z0-9+.-]*:/", ref, re.I) else ref
    except ValueError:
        return ""
    name = unquote(re.split(r"[\\/]", path)[-1]).strip()
    if not name.lower().endswith(_DOC_EXTENSIONS):
        return ""
    return "" if _key_like(name.rsplit(".", 1)[0]) else name


def _builder_names(query_builder, text):
    """Whether `text` names the builder: every distinctive word of the name ("Shape" of
    "Shape Homes", "Hattan" of "Hattan Homes", which also finds hattan.com.au), or the
    whole squashed name when it has no distinctive word ("G Developments")."""
    have = _squash(text)
    if not have:
        return False
    core = [w for w in _WORD.findall(query_builder.lower())
            if len(w) >= 3 and w not in _GENERIC_BUILDER_WORDS]
    if core:
        return all(w in have for w in core)
    want = _squash(query_builder)
    return len(want) >= 3 and (want in have or have in want)


def _builder_status(query, row):
    if not query.builder:
        return None
    if str(row.get("builder_name") or "").strip():
        return "yes" if _builder_names(query.builder, row.get("builder_name")) else "no"
    # A blank builder is not a different builder. The document may still name it: the
    # Shape Homes list reached us as an e-mail whose sheet has no builder column.
    if _builder_names(query.builder, document_label(row)):
        return "yes"
    return "unknown"


def _estate_status(query, row):
    if not query.estate:
        return None
    wanted = [w for w in _WORD.findall(query.estate.lower()) if w not in _ESTATE_STOPWORDS]
    estate = str(row.get("estate_name") or "")
    hay = set()
    for field in ("estate_name", "suburb", "street_address", "lot_address"):
        hay.update(_WORD.findall(str(row.get(field) or "").lower()))
    # "Highlands" said, "Highland" filed: a plural is the same word.
    singular = {h.rstrip("s") for h in hay if len(h) >= 5}
    if wanted and all(w in hay or (len(w) >= 5 and w.rstrip("s") in singular)
                      for w in wanted):
        return "yes"
    if estate.strip():
        ratio = difflib.SequenceMatcher(None, " ".join(wanted),
                                        " ".join(w for w in _WORD.findall(estate.lower())
                                                 if w not in _ESTATE_STOPWORDS)).ratio()
        return "yes" if ratio >= ESTATE_RATIO else "no"
    return "unknown"


def _row_lots(row):
    """The lot numbers a row states: its lot column, or else every "Lot N" in its address.

    No lot column does not mean no lot. "Lot 4402, Jubilee" is not lot 1229, and without
    reading the address a Shape Homes lot 1229 at $721,900 "matched" that Wyndham Vale lot
    at $716,900 on builder and price alone."""
    have = _norm_lot(row.get("lot_number"))
    if have:
        return {have}
    text = " ".join(str(row.get(f) or "") for f in ("street_address", "lot_address"))
    return {_norm_lot(m) for m in _LOT_IN_TEXT.findall(text)}


def _lot_status(query, row):
    if not query.lot:
        return None
    stated = _row_lots(row)
    if query.lot in stated:
        return "yes"
    return "no" if stated else "unknown"


def _price_status(query, row, share=PRICE_MATCH_SHARE):
    if not query.price:
        return None
    price = C._num(row.get("price"))
    if not price:
        return "unknown"
    return "yes" if abs(price - query.price) <= query.price * share else "no"


def match_statuses(query, row):
    return {"builder": _builder_status(query, row), "estate": _estate_status(query, row),
            "lot": _lot_status(query, row), "price": _price_status(query, row)}


def differing_fact(query, statuses):
    """'price' or 'builder' when the estate AND the lot named both agree and that one fact
    is all that disagrees, else None.

    The estate and the lot name one piece of land. Dawn lot 1228 quoted at $735,000 is
    the catalogue's Dawn lot 1228 at $721,900, repriced since the list was read -- not a
    listing the catalogue never received. Before this, a 1.8% price change sent the lot
    to "not in the catalogue (intake gap)", and that one line is the one that decides
    between harvest, email intake and the parser. A price that differs is forgiven only
    when the builder agrees or was not named (another builder's package on the same lot
    is another listing); a builder that differs only when the price agrees (the lot filed
    under its developer). Never both."""
    if statuses.get("estate") != "yes" or statuses.get("lot") != "yes":
        return None
    against = [c for c in query.criteria() if statuses.get(c) == "no"]
    if against == ["price"] and statuses.get("builder") in (None, "yes"):
        return "price"
    if against == ["builder"] and statuses.get("price") == "yes":
        return "builder"
    return None


def differs_note(query, row):
    """What the catalogue records differently for a row matched by differing_fact, or None."""
    if not query.criteria():
        return None
    fact = differing_fact(query, match_statuses(query, row))
    if fact == "price":
        price = C._num(row.get("price"))
        return ("the catalogue holds it at %s, not the %s named (%+.1f%%)"
                % (_money(price), _money(query.price),
                   100.0 * (price - query.price) / query.price))
    if fact == "builder":
        return 'filed under builder "%s", not "%s"' % (row.get("builder_name"),
                                                       query.builder)
    return None


def is_match(query, statuses):
    """The row is the listing named: no stated fact contradicts it, the lot or the price
    (when given) agrees, and two facts agree when two or more were given -- a lot number
    counts as two. A price alone is not a listing: 1% of $721,900 takes in a Mernda
    townhouse with no estate, builder or lot on its row. The one exception to "no fact
    contradicts it" is differing_fact: the same estate and lot, repriced or filed under
    another name."""
    given = query.criteria()
    forgiven = differing_fact(query, statuses)
    if not given or any(statuses[c] == "no" for c in given if c != forgiven):
        return False
    # Named by where it is, it must be found there. A row stating neither the estate nor
    # the lot named is not the listing on builder and price alone: Torsion's filter-row
    # fragment "LAND SIZE : 1463 m2 LAND PRICE" at $798,000 (no estate, no lot) matched
    # "Torsion, Walloon Highlands, lot 224, $799,995", and for a lot the catalogue does
    # not hold, that fragment's "parse gap" would have been the listing's one-line verdict.
    placed = [c for c in ("estate", "lot") if c in given]
    if placed and not any(statuses[c] == "yes" for c in placed):
        return False
    identifiers = [c for c in ("lot", "price") if c in given]
    if identifiers and not any(statuses[c] == "yes" for c in identifiers):
        return False
    strength = sum(2 if c == "lot" else 1 for c in given if statuses[c] == "yes")
    return strength >= min(2, len(given))


def nearness(query, row):
    """(score, reasons) for the nearest-rows list: same builder, fuzzy estate, lot within
    NEAR_LOT, price within NEAR_PRICE_SHARE. Score 0 when nothing is near.

    Points: estate 2; lot 2 exact, 1 within NEAR_LOT; price 2 within 1%, 1 within 5%. The
    same builder needs 1 of them, a row naming no builder 2, and a row filed under another
    builder name 3 -- a lot filed under its developer is still the same lot. Without the
    same builder, the estate or the lot has to be among them."""
    score, reasons = 0, []
    builder = _builder_status(query, row)
    near = False
    if query.estate and _estate_status(query, row) == "yes":
        score += 2
        near = True
        reasons.append("estate")
    if query.lot:
        a = _as_int(query.lot)
        lots = sorted(n for n in map(_as_int, _row_lots(row)) if n is not None)
        b = min(lots, key=lambda n: abs(n - a)) if a is not None and lots else None
        if b is not None and abs(a - b) <= NEAR_LOT:
            score += 2 if a == b else 1
            near = True
            reasons.append("lot %s" % b if a == b else "lot %s (%+d)" % (b, b - a))
    if query.price:
        price = C._num(row.get("price"))
        if price and abs(price - query.price) <= query.price * NEAR_PRICE_SHARE:
            score += 2 if abs(price - query.price) <= query.price * PRICE_MATCH_SHARE else 1
            near = True
            reasons.append("price %s (%+.1f%%)" % (_money(price),
                                                    100.0 * (price - query.price)
                                                    / query.price))
    # A price is not an identity: without the builder, the estate or the lot must be near.
    placed = any(r == "estate" or r.startswith("lot ") for r in reasons)
    if not near or score < {"yes": 1, "no": 3}.get(builder, 2) or (
            builder != "yes" and not placed):
        return 0, []
    if builder == "yes":
        score += 2
        reasons.insert(0, "same builder")
    elif builder == "no":
        reasons.insert(0, "another builder (%s)" % row.get("builder_name"))
    return score, reasons


# ------------------------------------------------------------------------- the brief

def search_brief(state=None, budget=None):
    """(brief, adjustments) as api/research.py reads it: the same normaliser."""
    import research
    raw = {"client_name": "trace_listing", "state": state or ""}
    if budget not in (None, ""):
        # A plain number typed on the command line is a number, as the form sends it; only
        # a figure the form would also have had to read ("750", "750k") earns a note.
        try:
            raw["budget_max"] = float(str(budget).strip())
        except ValueError:
            raw["budget_max"] = budget
    return research._normalised_brief(raw)


def parse_today(text):
    if not text:
        return datetime.combine(C._now_aest().date(), datetime.min.time())
    return datetime.strptime(text.strip(), "%d/%m/%Y")


# ------------------------------------------------------------------------- scoring

def pipeline_agent(rows):
    """The deployed research endpoint's pipeline object, taught this snapshot's estates.

    _bootstrap.get_agent points config's writable paths (its audit DB, reports) at a
    scratch directory, reads the committed public builder registry and cannot scrape.
    """
    import _bootstrap
    agent = _bootstrap.get_agent()
    agent.geo.know_estates([(r.get("estate_name"), r.get("suburb"), r.get("state"))
                            for r in rows
                            if r.get("estate_name") and not r.get("superseded_by")])
    return agent


def scored_outcomes(brief, catalogue, today, agent):
    """{content_hash: (outcome, detail)} for every package the search scores.

    outcome is 'shortlisted', 'pending' (passed the scorer, not verified), 'rejected'
    (detail: the scorer's reason) or 'deduped' (detail: the package kept). Two pools, as
    a search runs them: the confirmed search, and the needs-confirming admission pass.
    """
    from sources.dedupe import DedupeEngine
    out = {}
    for admitted in (False, True):
        packages, _counts = C.build_packages(
            dict(brief), catalogue.rows, today=today, prepared=catalogue.prepared,
            admit=C.ADMITTABLE_GATES if admitted else (), only_admitted=admitted)
        deduped = DedupeEngine.deduplicate(packages)
        kept = {id(p) for p in deduped}
        for p in packages:
            if id(p) in kept:
                continue
            twin = next((k for k in deduped
                         if k["advertised_package_price"] == p["advertised_package_price"]
                         and len(DedupeEngine.deduplicate([p, k])) == 1), None)
            out[p["content_hash"]] = ("deduped", (twin or {}).get("content_hash"))
        if not deduped:
            continue
        processed, rejected = agent.score_candidates(dict(brief),
                                                     [dict(p) for p in deduped])
        from schema import VerificationStatus
        for cand in processed:
            # run_property_research's final_pool rule, word for word.
            verified = (cand.verification_status == VerificationStatus.VERIFIED
                        or cand.consultant_approved)
            out[cand.content_hash] = ("shortlisted" if verified else "pending",
                                      cand.scoring.total_score if cand.scoring else None)
        for entry in rejected:
            index = int(str(entry["property_id"]).rsplit("-", 1)[1]) - 1
            out[deduped[index]["content_hash"]] = ("rejected", entry.get("reason") or "")
    return out


# ------------------------------------------------------------------------- verdicts

class Tracer:
    """Verdicts for rows of one catalogue under one brief on one day."""

    def __init__(self, catalogue, brief, today, agent_factory=pipeline_agent):
        self.catalogue = catalogue
        self.brief = brief
        self.today = today
        self._agent_factory = agent_factory
        self._agent = None
        self._explained = None
        self._scored = None
        self._scorer_error = None
        self._stocked = None
        self._geo = None

    def agent(self):
        """The pipeline object, built once and only when a row reaches scoring."""
        if self._agent is None:
            self._agent = self._agent_factory(self.catalogue.rows)
        return self._agent

    # -- the search's own answers, computed once
    @property
    def explained(self):
        if self._explained is None:
            pairs = C.explain_rows(dict(self.brief), self.catalogue.rows,
                                   prepared=self.catalogue.prepared, today=self.today,
                                   admit=C.ADMITTABLE_GATES)
            self._explained = {id(row): verdict for row, verdict in pairs}
        return self._explained

    def scored(self):
        if self._scored is None and self._scorer_error is None:
            try:
                self._scored = scored_outcomes(self.brief, self.catalogue, self.today,
                                               self.agent())
            except Exception as exc:                                  # noqa: BLE001
                self._scorer_error = "%s: %s" % (type(exc).__name__, exc)
        return self._scored

    # -- one row
    def trace(self, row, depth=0):
        """{row, id, content_hash, in_snapshot, gate, verdict, kind, confirm_guard,
        warnings} for one catalogue row."""
        in_snapshot = id(row) in self.explained
        out = {"row": row, "id": _row_id(row),
               "content_hash": str(row.get("content_hash") or ""),
               "in_snapshot": in_snapshot, "warnings": self.warnings(row)}
        if not in_snapshot:
            out.update(gate=None, confirm_guard=None, kind="snapshot",
                       verdict=("in the database (checked %s) but not in the snapshot "
                                "searched: the snapshot is older than the database or "
                                "build_web left the row out; --build traces it"
                                % (row.get("date_checked") or "never")))
            if row.get("superseded_by"):
                out["verdict"] += "; the database marks it superseded"
            return out
        explained = self.explained[id(row)]
        detail, package = explained["detail"], explained["package"]
        out["gate"] = explained["gate"]
        out["confirm_guard"] = C.confirm_guard(row, self.catalogue.prepared, self.today)
        if package is not None and package.get("verified"):
            out["guard_note"] = "not applied: the row is fresh, so it counts as verified"
        elif package is None and explained["gate"] != "availability_unstated":
            out["guard_note"] = "not reached: the row stops at %s" % explained["gate"]
        tags = detail.get("tags") or ()
        if "state_borrowed" in tags:
            out["state_note"] = "the search borrows %s from its %s" % (
                detail.get("state"), detail.get("state_borrowed_from"))
        elif "estate_only_place" in tags:
            out["state_note"] = "the search places it in %s by its estate" % ", ".join(
                str(p) for p in reversed(detail.get("estate_only_place") or ()))
        text, kind = self._verdict(row, explained, depth)
        out["verdict"], out["kind"] = text, kind
        return out

    def _verdict(self, row, explained, depth):
        gate, detail, package = explained["gate"], explained["detail"], explained["package"]
        unstated = "document_states_availability" in detail
        also = "; its list also does not say whether it is available" if unstated else ""
        if gate == "superseded":
            return self._superseded(detail.get("superseded_by"), depth)
        if gate == "state_unknown":
            return ("hidden: no state recorded, and none could be borrowed from its "
                    "estate, document or builder", "parse")
        if gate == "other_state":
            # A parse gap only when the row's own text places it in the brief's state and
            # its filed state rests on a suburb match: Hattan's Wollert lot filed QLD is
            # lost to a VIC client. Its correct VIC twin is simply outside a QLD brief.
            wanted = str(self.brief.get("state") or "").strip().upper()
            elsewhere = (self._text_names_elsewhere(row)
                         if self._state_rests_on_suburb(row) else None)
            text = "hidden: filed %s, the brief asks for %s" % (detail.get("state"), wanted)
            if elsewhere and elsewhere[1] == wanted:
                return (text + " (its text names %s, a %s locality)" % elsewhere, "parse")
            return text, "brief"
        if gate == "other_product_type":
            return ("hidden: product type %r is not one the brief asks for"
                    % (detail.get("product_type") or "unknown"), "brief")
        if gate == "no_incentive":
            return "hidden: no incentive, and the brief requires one", "brief"
        if gate == "price_unverified":
            return ("hidden: price not trusted (a %s price read before the %s fix, "
                    "checked %s)" % (detail.get("source_channel"),
                                     C.PROXIMA_PRICE_FIX_DATE.strftime("%d/%m/%Y"),
                                     detail.get("date_checked") or "never"), "parse")
        if gate == "not_available":
            return ("hidden: the source marks it %s (checked %s)"
                    % (detail.get("availability_status"),
                       row.get("date_checked") or "never"), "gate")
        if gate == "lot_page_gone":
            status = detail.get("lot_page_status") or "gone"
            said = ("answered 'not found'" if status == "gone"
                    else "says '%s'" % status)
            return ("hidden: the builder's page for this lot %s (link_check.py, since %s; "
                    "last read from the source %s)"
                    % (said, detail.get("lot_page_gone") or "?",
                       row.get("date_checked") or "never"), "gate")
        if gate == "availability_unstated":
            reason = detail.get("not_admitted")
            text = HIDDEN_BECAUSE.get(reason, reason or "availability not stated")
            return ("hidden: %s (checked %s)" % (text, row.get("date_checked") or "never"),
                    "gate")
        if gate == "no_price":
            return "hidden: no price recorded" + also, "parse"
        if gate == "over_budget":
            just = " (just over)" if detail.get("just_over") else ""
            return ("hidden: %s is over the %s budget%s%s"
                    % (_money(detail.get("price")), _money(detail.get("ceiling")), just,
                       also), "brief")
        if gate == "under_minimum":
            return ("hidden: %s is under the %s minimum%s"
                    % (_money(detail.get("price")), _money(detail.get("floor")), also),
                    "brief")
        if gate == "no_suburb":
            return "hidden: no suburb recorded, and none could be borrowed" + also, "parse"
        if gate == "suburb_not_a_locality":
            return ("hidden: suburb %r is not a locality (%s)%s"
                    % (detail.get("suburb"), detail.get("suburb_source"), also), "parse")
        if gate == "incomplete_facts":
            return ("hidden: missing %s, which the brief requires%s"
                    % (", ".join(detail.get("missing") or ()), also), "parse")
        if gate == "truncated":
            return ("hidden: cut by the candidate cap (SPB_MAX_CANDIDATES=%d) before "
                    "scoring" % C.MAX_CANDIDATES, "gate")
        if gate == "same_listing_collapsed":
            return self._merged_into(row, package, ("scored", "truncated"),
                                     C._collapse_kept, "the same lot, price and facts "
                                     "from another capture", depth)
        if gate == "scored":
            return self._scored_verdict(row, package, depth)
        return "gate %s" % gate, "gate"

    def _superseded(self, target, depth):
        target = str(target or "").strip()
        if not target.startswith("v2:"):
            kind = "parse" if "not a listing" in target else "gate"
            return "superseded: %s" % target, kind
        winner = self.catalogue.by_hash.get(target)
        if winner is None:
            return ("superseded by %s, which the snapshot does not hold" % target[:11],
                    "gate")
        name = ("id %s (%s)" % (_db(winner).get("id"), target[:11])
                if _db(winner).get("id") is not None else target[:11])
        text = ("superseded by %s, checked %s, %s"
                % (name, winner.get("date_checked") or "never",
                   winner.get("availability_status") or "availability not stated"))
        if depth < 4:
            inner = self.trace(winner, depth + 1)
            return "%s -- which is: %s" % (text, inner["verdict"]), inner["kind"]
        return text, "gate"

    def _merged_into(self, row, package, gates, same, why, depth):
        admitted = C._is_admitted(package)
        for other in self.catalogue.rows:
            if other is row:
                continue
            verdict = self.explained.get(id(other))
            if (not verdict or verdict["gate"] not in gates
                    or C._is_admitted(verdict["package"]) != admitted
                    or verdict["package"]["advertised_package_price"]
                    != package["advertised_package_price"]):
                continue
            if len(same([(None, package), (None, verdict["package"])])) == 1:
                inner = self.trace(other, depth + 1) if depth < 4 else None
                name = _row_name(other)
                if inner is None:
                    return "merged into %s (%s)" % (name, why), "gate"
                return ("merged into %s (%s), which is: %s"
                        % (name, why, inner["verdict"]), inner["kind"])
        return "merged into another capture of the same listing (%s)" % why, "gate"

    def _scored_verdict(self, row, package, depth):
        confirm = list(package.get("confirm") or ())
        guard = package.get("confirm_guard")
        checked = package.get("date_checked") or "never"
        notes = ("; note: its document was only partly re-read"
                 if "document_partially_read" in confirm else "")
        scored = self.scored()
        if scored is None:
            base = ("passes every gate and reaches scoring (scorer not run: %s)"
                    % self._scorer_error)
            if package.get("verified"):
                return base, "shown"
            if guard:
                return "hidden: %s (checked %s)" % (_hidden_because(guard, confirm),
                                                   checked), "gate"
            return base + "; unverified, so at most needs confirming", "confirm"
        outcome, detail = scored.get(package["content_hash"], (None, None))
        if outcome == "deduped":
            twin = self.catalogue.by_hash.get(detail or "")
            if twin is not None and depth < 4:
                inner = self.trace(twin, depth + 1)
                return ("merged into %s by the pipeline's dedupe (same address, design and "
                        "price), which is: %s" % (_row_name(twin), inner["verdict"]),
                        inner["kind"])
            return "merged into another package by the pipeline's dedupe", "gate"
        if outcome == "rejected":
            return "rejected by the scorer: %s" % detail, "brief"
        if outcome == "shortlisted":
            return "shortlisted" + notes, "shown"
        if outcome != "pending":
            return "passes every gate, but the scorer did not return it", "gate"
        if guard:
            return ("hidden: %s (checked %s)" % (_hidden_because(guard, confirm), checked)
                    + notes, "gate")
        why = []
        if "availability_unstated" in confirm:
            why.append("availability not stated")
        if "stale" in confirm:
            why.append("stale")
        if "newer_capture" in confirm:
            why.append("a later capture disagrees")
        return ("%s -> needs confirming (checked %s)%s"
                % (", ".join(why) or "unverified", checked, notes), "confirm")

    # -- warnings
    def _state_rests_on_suburb(self, row):
        return str(row.get("state_source") or "").strip().lower().startswith("suburb")

    def warnings(self, row):
        out = []
        if self._state_rests_on_suburb(row):
            source = str(row.get("state_source") or "").strip()
            text = ("the state %s rests on the suburb column%s (state_source %r), not on "
                    "the row's own text" % (row.get("state") or "(none)",
                                             "" if source.lower() == "suburb"
                                             else " and its document", source))
            elsewhere = self._text_names_elsewhere(row)
            if elsewhere:
                text += _NAMES_ELSEWHERE + "%s, a %s locality" % elsewhere
            out.append(text)
        if row.get("db") is None and "db" in row and self.catalogue.db_label != "none":
            out.append("the database has no row with this content_hash")
        return out

    def _text_names_elsewhere(self, row):
        """(locality, state) when the row's text names a place in one other state that
        the catalogue stocks -- W02's evidence, asked as enrich_buildings asks it
        (state_resolver.text_locality_elsewhere over source_text and lot_address, against
        a plain gazetteer) -- or None. Advisory only, and never fatal."""
        try:
            from geo import SuburbGeoIndex
            from state_resolver import text_locality_elsewhere
            if self._stocked is None:
                self._geo = SuburbGeoIndex()
                # As enrich's overrule_evidence: live rows whose suburb is a locality of
                # their state, unless that state rests on the suburb column alone.
                self._stocked = {
                    (str(r.get("suburb") or "").strip().lower(),
                     str(r.get("state") or "").strip().upper())
                    for r in self.catalogue.rows
                    if not r.get("superseded_by") and r.get("suburb") and r.get("state")
                    and str(r.get("state_source") or "").strip() != "suburb"
                    and self._geo.locate(str(r.get("suburb")).strip(),
                                         str(r.get("state")).strip().upper())}
            text = " ".join(str(v or "") for v in (_db(row).get("source_text"),
                                                   row.get("lot_address")))
            return text_locality_elsewhere(text, row.get("state"), self._geo,
                                           self._stocked, estate=row.get("estate_name"))
        except Exception:                                             # noqa: BLE001
            return None


def _row_id(row):
    ident = _db(row).get("id")
    return ident if ident is not None else row.get("id")


def _row_name(row):
    ident = _row_id(row)
    short = str(row.get("content_hash") or "")[:11]
    return "id %s (%s)" % (ident, short) if ident is not None else short


# ------------------------------------------------------------------------- the report

def find_rows(catalogue, query):
    """(matches, nearest-candidates) -- rows in catalogue order, database-only rows last."""
    everything = list(catalogue.rows) + list(catalogue.db_only)
    matches, seen = [], set()
    for row in everything:
        ident = _row_id(row)
        chash = str(row.get("content_hash") or "")
        direct = ((ident is not None and ident in query.ids)
                  or any(chash.startswith(h) for h in query.hashes))
        if direct or (query.criteria() and is_match(query, match_statuses(query, row))):
            if id(row) not in seen:
                seen.add(id(row))
                matches.append(row)
    return matches, everything


def nearest_rows(everything, query, exclude, limit, state=""):
    """The `limit` nearest rows: closest first, the brief's state before others, live
    captures before superseded ones, then the smallest price gap."""
    state = str(state or "").strip().upper()
    scored = []
    for index, row in enumerate(everything):
        if id(row) in exclude:
            continue
        score, reasons = nearness(query, row)
        if score:
            price = C._num(row.get("price")) or 0.0
            gap = abs(price - query.price) if query.price else 0.0
            elsewhere = 1 if state and str(row.get("state") or "").upper() != state else 0
            scored.append((-score, elsewhere, 1 if row.get("superseded_by") else 0, gap,
                           index, row, reasons))
    scored.sort(key=lambda t: t[:5])
    return [(row, reasons) for *_rank, row, reasons in scored[:limit]]


def run_trace(catalogue, query, brief, today, adjustments=(), agent_factory=pipeline_agent,
              nearest=5, limit=20, tracer=None):
    """The whole trace as data: {header, matches, more, nearest, not_found}.

    `tracer` is a Tracer for this catalogue, brief and day to reuse: tracing several
    listings against one search explains and scores the catalogue once."""
    tracer = tracer or Tracer(catalogue, brief, today, agent_factory=agent_factory)
    matches, everything = find_rows(catalogue, query)
    traced = []
    for row in matches[:limit]:
        item = tracer.trace(row)
        item["differs"] = differs_note(query, row)
        traced.append(item)
    report = {
        "query": query.describe(),
        "brief": brief, "adjustments": list(adjustments),
        "today": today.strftime("%d/%m/%Y"),
        "snapshot": catalogue.snapshot_label, "database": catalogue.db_label,
        "notes": list(catalogue.notes),
        "matches": traced,
        "more": max(0, len(matches) - limit),
        "not_found": not matches,
        "nearest": [],
        "searched_nearest": bool(not matches and query.criteria()),
    }
    if report["searched_nearest"]:
        for row, reasons in nearest_rows(everything, query, set(), nearest,
                                         state=brief.get("state")):
            item = tracer.trace(row)
            item["near"] = reasons
            report["nearest"].append(item)
    if tracer._scorer_error:
        report["notes"].append("the scorer could not run: " + tracer._scorer_error)
    return report


# The order a listing's verdict is taken from when several rows hold it: if any capture
# is shown, the listing is shown; a live capture speaks before a superseded one.
_KIND_RANK = ("shown", "confirm", "gate", "brief", "parse", "snapshot")


def summarise(report):
    """(kind, text): the one-line verdict for the listing a report traced."""
    if report["not_found"]:
        text = "not in the catalogue"
        if report["nearest"]:
            item = report["nearest"][0]
            text += "; nearest is %s (%s): [%s] %s" % (
                _row_name(item["row"]), ", ".join(item.get("near") or ()),
                KIND_LABELS[item["kind"]], item["verdict"])
        return "intake", text
    best = min(report["matches"],
               key=lambda i: (_KIND_RANK.index(i["kind"]),
                              1 if i["row"].get("superseded_by") else 0))
    text = "%s: %s" % (_row_name(best["row"]), best["verdict"])
    rows = len(report["matches"]) + report["more"]
    if rows > 1:
        text += " (%d rows hold it)" % rows
    if best.get("differs"):
        text += "; " + best["differs"]
    # 14905 is "shortlisted" to a QLD client and is VIC stock: the warning is half the
    # verdict, so the one line carries it too.
    elsewhere = next((w.split(_NAMES_ELSEWHERE, 1)[1] for w in best["warnings"]
                      if _NAMES_ELSEWHERE in w), None)
    if elsewhere:
        text += "; its state rests on the suburb column, and its text names " + elsewhere
    gone = _later_capture_gone(report["matches"], best)
    if gone is not None:
        text += ("; but a later capture, %s checked %s, says %s"
                 % (_row_name(gone["row"]), gone["row"].get("date_checked"),
                    str(gone["row"].get("availability_status")).strip()))
    return best["kind"], text


def _later_capture_gone(items, best):
    """The newest live row among `items`, when it was read after `best` and its source
    marks the lot gone -- and `best` is shown or offered to confirm. Else None.

    "Shortlisted" alone is the search's answer, and a true one; it is not the whole
    answer when the same lot was read again a day later as On Hold (Howard lot 21: id
    18491 Available 15/09 shortlisted, id 15953 On Hold 16/09). The one-line verdict must
    not read as "this listing is fine"."""
    if best["kind"] not in ("shown", "confirm"):
        return None
    shown_day = C._parse_day(best["row"].get("date_checked"))
    shown_lots = _row_lots(best["row"])
    shown_price = C._num(best["row"].get("price"))
    newest, newest_day = None, None
    for item in items:
        row = item["row"]
        day = C._parse_day(row.get("date_checked"))
        if item is best or row.get("superseded_by") or day is None:
            continue
        # Only a capture of the SAME lot speaks for it: both state the lot, or neither
        # does and the price is the same to the dollar. A row matched loosely (no lot on
        # it, a price within 1%) may be the lot next door -- Howard's lot 13 at $745,857,
        # Sold, is not lot 21 at $746,632.
        lots = _row_lots(row)
        if shown_lots and lots:
            if not shown_lots & lots:
                continue
        elif round(C._num(row.get("price")) or 0) != round(shown_price or 0):
            continue
        if newest_day is None or day > newest_day:
            newest, newest_day = item, day
    if newest is None or (shown_day is not None and newest_day <= shown_day):
        return None
    status = str(newest["row"].get("availability_status") or "").strip().lower()
    return newest if status in C.NOT_AVAILABLE else None


def read_listings(path):
    """[(label, Query)] from Colin's listings: a CSV with a header row, or a JSON list of
    objects. Columns builder, estate, lot, price, and optionally label (how it reached
    him: portal, email, E-Agent, a builder's rep). Prices are read as the brief reads
    money: 721900, $721,900, 722k."""
    import csv
    import research
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        records = json.loads(text)
        if not isinstance(records, list):
            raise ValueError("%s: expected a JSON list of listings" % path.name)
    else:
        records = list(csv.DictReader(text.splitlines()))
    listings = []
    for number, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise ValueError("%s: listing %d is not an object" % (path.name, number))
        record = {str(k or "").strip().lower(): v for k, v in record.items()}
        price = None
        if str(record.get("price") or "").strip():
            price, _how = research._read_money(record.get("price"))
            if price is None:
                raise ValueError("%s: listing %d has price %r, which is not a price"
                                 % (path.name, number, record.get("price")))
        query = Query(record.get("builder"), record.get("estate"),
                      None if record.get("lot") is None else str(record.get("lot")), price)
        if not query.criteria():
            raise ValueError("%s: listing %d names no builder, estate, lot or price"
                             % (path.name, number))
        listings.append((str(record.get("label") or "").strip(), query))
    if not listings:
        raise ValueError("%s holds no listings" % path.name)
    return listings


def _row_title(row):
    address = C._display_address(row) if row.get("lot_address") is not None else ""
    place = " ".join(p for p in (str(row.get("suburb") or "").strip(),
                                 str(row.get("state") or "").strip()) if p)
    return "%s | %s | %s" % (address or "(no address)", place or "(no place)",
                             _money(row.get("price")))


def _describe_brief(brief):
    state = str(brief.get("state") or "").strip().upper() or "Any state"
    ceiling = C._num(brief.get("budget_max"))
    budget = ("no price ceiling" if not ceiling or ceiling >= 9007199254740991
              else "budget up to %s" % _money(ceiling))
    return "%s, %s" % (state, budget)


def render(report):
    """The report as printable lines. Every line has been through _no_links."""
    lines = _render_header(report, report["query"]) + [""] + _render_body(report)
    return [_no_links(line) for line in lines]


def render_many(reports):
    """[(label, report)] -- several listings under one search -- as printable lines: each
    listing's rows, then one verdict line per listing. Every line has been through
    _no_links."""
    notes = []
    for _label, report in reports:
        notes += [n for n in report["notes"] if n not in notes]
    lines = _render_header(reports[0][1], "%d listings" % len(reports), notes)
    for number, (label, report) in enumerate(reports, 1):
        lines += ["", "=" * 78,
                  "LISTING %d: %s%s" % (number, report["query"],
                                        " (%s)" % label if label else ""), ""]
        lines += _render_body(report)
    lines += ["", "=" * 78, "One line per listing:"]
    for number, (label, report) in enumerate(reports, 1):
        kind, text = summarise(report)
        lines.append("  %d. %s%s -> [%s] %s" % (number, report["query"],
                                               " (%s)" % label if label else "",
                                               KIND_LABELS[kind], text))
    return [_no_links(line) for line in lines]


def _render_header(report, looking_for, notes=None):
    lines = [
        "trace_listing (read-only)",
        "  looking for : %s" % looking_for,
        "  brief       : %s, as the Research tab searches it" % _describe_brief(
            report["brief"]),
        "  today       : %s (AEST)" % report["today"],
        "  snapshot    : %s" % report["snapshot"],
        "  database    : %s" % report["database"],
    ]
    for adj in report["adjustments"]:
        lines.append("  adjusted    : %s %s (%s)" % (adj.get("field"), adj.get("code"),
                                                     adj.get("why") or adj.get("note")))
    for note in report["notes"] if notes is None else notes:
        lines.append("  note        : %s" % note)
    return lines


def _render_body(report):
    lines = []
    if report["not_found"]:
        lines.append("NOT IN THE CATALOGUE (intake gap): no row matches %s." % report["query"])
        if report["nearest"]:
            lines.append("")
            lines.append("Nearest rows:")
            for item in report["nearest"]:
                lines.extend(_render_item(item, near=True))
        elif report["searched_nearest"]:
            lines.append("No row is near it either: none from the same builder, estate or "
                         "lot (within %d) at a price within %d%%."
                         % (NEAR_LOT, round(NEAR_PRICE_SHARE * 100)))
    else:
        lines.append("%d row(s) match:" % (len(report["matches"]) + report["more"]))
        for item in report["matches"]:
            lines.extend(_render_item(item))
        if report["more"]:
            lines.append("  ... and %d more (narrow the search, or --limit)" % report["more"])
    lines.append("")
    lines.append("Verdicts:")
    if report["not_found"]:
        lines.append("  intake gap: %s is not in the catalogue" % report["query"])
    for item in report["matches"]:
        lines.append("  %-18s [%s] %s" % (_row_name(item["row"]), KIND_LABELS[item["kind"]],
                                          item["verdict"]))
    return lines


def _scrubbed(value):
    """A JSON-ready value with _no_links applied to every string inside it -- before
    json.dumps, since scrubbing the dumped text would eat the quotes after a link."""
    if isinstance(value, str):
        return _no_links(value)
    if isinstance(value, dict):
        return {k: _scrubbed(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrubbed(v) for v in value]
    return value


def json_lines(report, **extra):
    """The verdicts as JSON objects: a row each, and an intake line when nothing matched."""
    out = []
    for item in report["matches"] or report["nearest"]:
        out.append(dict(extra, id=_row_id(item["row"]), content_hash=item["content_hash"],
                        kind=item["kind"], gate=item.get("gate"), verdict=item["verdict"],
                        differs=item.get("differs"),
                        confirm_guard=item.get("confirm_guard"),
                        warnings=item["warnings"], nearest=bool(item.get("near"))))
    if report["not_found"]:
        out.append(dict(extra, kind="intake", verdict="not in the catalogue",
                        query=report["query"]))
    return [json.dumps(_scrubbed(line)) for line in out]


def _render_item(item, near=False):
    row = item["row"]
    db = _db(row)
    out = ["", "  %s  %s" % (_row_name(row), _row_title(row))]
    if near:
        out.append("    near        : %s" % ", ".join(item.get("near") or ()))
    out.append("    builder     : %s" % (row.get("builder_name") or "(not named on the row)"))
    out.append("    estate / lot: %s / %s" % (row.get("estate_name") or "(none)",
                                             row.get("lot_number") or "(none)"))
    out.append("    channel     : %s" % (row.get("source_channel") or "(none)"))
    out.append("    document    : %s" % document_label(row))
    first = row.get("email_first_seen") or db.get("first_seen")
    out.append("    checked     : %s%s" % (row.get("date_checked") or "never",
                                           ", first read %s" % first if first else ""))
    out.append("    availability: %s" % (row.get("availability_status")
                                         or "not stated"))
    out.append("    state       : %s (state_source: %s)%s" % (
        row.get("state") or "(none)", row.get("state_source") or "(none)",
        "; %s" % item["state_note"] if item.get("state_note") else ""))
    superseded = row.get("superseded_by")
    out.append("    superseded  : %s" % (("yes, " + str(superseded)[:11]
                                          if str(superseded).startswith("v2:")
                                          else "yes: %s" % superseded)
                                         if superseded else "no"))
    out.append("    confirm_guard: %s%s" % (item.get("confirm_guard") or "none",
                                         " (%s)" % item["guard_note"]
                                         if item.get("guard_note") else ""))
    if item.get("differs"):
        out.append("    differs     : %s" % item["differs"])
    for warning in item["warnings"]:
        out.append("    WARNING     : %s" % warning)
    out.append("    verdict     : [%s] %s" % (KIND_LABELS[item["kind"]], item["verdict"]))
    return out


# ------------------------------------------------------------------------- the CLI

def _parser():
    ap = argparse.ArgumentParser(
        description="Explain a listing against the catalogue, read-only.",
        epilog="Name at least one of --builder --estate --lot --price --id --hash, or "
               "pass --listings.")
    ap.add_argument("--builder")
    ap.add_argument("--estate")
    ap.add_argument("--lot")
    ap.add_argument("--price", help="the listing's price: 721900, $721,900, 722k")
    ap.add_argument("--id", action="append", default=[], type=int,
                    help="a database id (repeatable)")
    ap.add_argument("--hash", action="append", default=[],
                    help="a content_hash or its prefix, e.g. v2:1c5ca48e (repeatable)")
    ap.add_argument("--listings",
                    help="several listings under one search: a CSV (header builder, estate, "
                         "lot, price[, label]) or a JSON list; ends with one verdict line "
                         "per listing")
    ap.add_argument("--state", help="the brief's state; blank is Any state")
    ap.add_argument("--budget", help="the brief's budget_max: 750000, 750k, $750,000")
    ap.add_argument("--db", help="the database (default SPB_DATABASE_PATH, then the app's)")
    ap.add_argument("--snapshot",
                    help="stock.json (default SPB_SNAPSHOT_JSON, then vercel_site/stock.json)")
    ap.add_argument("--build", action="store_true",
                    help="build the snapshot with build_web from a copy of --db")
    ap.add_argument("--today", help="dd/mm/yyyy (default today, AEST)")
    ap.add_argument("--nearest", type=int, default=5)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true",
                    help="print the verdicts as JSON lines instead of the report")
    return ap


def _default_db():
    env = os.environ.get("SPB_DATABASE_PATH")
    return Path(env) if env else APP / "spb_research_audit.db"


def _default_snapshot():
    env = os.environ.get("SPB_SNAPSHOT_JSON")
    return Path(env) if env else APP / "vercel_site" / "stock.json"


def main(argv=None, out=None, agent_factory=pipeline_agent):
    out = out or sys.stdout
    try:
        out.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = _parser()
    args = ap.parse_args(argv)
    # --limit 0 left a found listing with no rows to take its one-line verdict from.
    if args.limit < 1:
        ap.error("--limit must be 1 or more")
    if args.nearest < 0:
        ap.error("--nearest must be 0 or more")
    price = None
    if args.price:
        import research
        price, how = research._read_money(args.price)
        if price is None:
            ap.error("--price %r is not a price" % args.price)
    query = Query(args.builder, args.estate, args.lot, price, args.id, args.hash)
    named = bool(query.criteria() or query.ids or query.hashes)
    listings = None
    if args.listings:
        if named:
            ap.error("--listings replaces --builder --estate --lot --price --id --hash")
        try:
            listings = read_listings(args.listings)
        except (OSError, ValueError) as exc:
            ap.error("--listings: %s" % exc)
    elif not named:
        ap.error("name at least one of --builder --estate --lot --price --id --hash, or "
                 "pass --listings")
    try:
        today = parse_today(args.today)
    except ValueError:
        ap.error("--today must be dd/mm/yyyy")
    db_path = Path(args.db) if args.db else _default_db()
    brief, adjustments = search_brief(args.state, args.budget)

    with tempfile.TemporaryDirectory(prefix="spb_trace_", ignore_cleanup_errors=True) as tmp:
        if args.build:
            if not db_path.is_file():
                ap.error("--build needs the database: %s not found" % db_path)
            snapshot, db_for_join = build_snapshot(db_path, tmp)
            label = "built by build_web from a copy of %s" % db_path.name
        else:
            snapshot = Path(args.snapshot) if args.snapshot else _default_snapshot()
            if not snapshot.is_file():
                ap.error("no snapshot at %s: pass --snapshot, or --build from the database"
                         % snapshot)
            db_for_join, label = db_path, None
        catalogue = load_catalogue(snapshot, db_for_join)
        if label:
            catalogue.snapshot_label += ", " + label
            catalogue.db_label = "%s (copied read-only)" % db_path.name
        tracer = Tracer(catalogue, brief, today, agent_factory=agent_factory)
        reports = [(label, run_trace(catalogue, q, brief, today, adjustments,
                                     nearest=args.nearest, limit=args.limit, tracer=tracer))
                   for label, q in (listings or [("", query)])]
    if listings is None:
        report = reports[0][1]
        lines = json_lines(report) if args.json else render(report)
    elif args.json:
        lines = []
        for number, (label, report) in enumerate(reports, 1):
            lines += json_lines(report, listing=number)
            kind, text = summarise(report)
            lines.append(json.dumps(_scrubbed({
                "listing": number, "label": label, "query": report["query"],
                "summary": True, "kind": kind, "verdict": text})))
    else:
        lines = render_many(reports)
    for line in lines:
        print(line, file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
