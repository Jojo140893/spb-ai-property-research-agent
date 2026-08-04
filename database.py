"""
SQLite Audit Trail & Research Database (SOP Step 1 & 14).
Stores persisted research records, evaluated candidate properties, rejection logs,
and Kommo CRM payload audit trails.
"""

import hashlib
import logging
import re
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import config
from brief_parser import coerce_number

logger = logging.getLogger("spb.db")


def _reparse_recover(building: Dict[str, Any]) -> Dict[str, Any]:
    """Facts recovered from the row's own source_text, or {} if the module is absent.

    Imported lazily and failure-tolerant on purpose: the recovery rules are generated
    data, and a harvest must never stop because a regenerated rule file has a problem.
    A harvest that stores slightly less is recoverable; a harvest that does not run is
    a day of stale prices.
    """
    try:
        import stocklist_reparse
        return stocklist_reparse.recover(building)
    except Exception as exc:                                        # noqa: BLE001
        logger.warning("row re-parse unavailable (%s) — storing the row as captured", exc)
        return {}


def _first_suburb(brief_dict: Dict[str, Any]) -> str:
    """The first suburb named in the brief, or 'General'.

    `primary_suburbs[0]` alone was wrong for a caller that sends the field as a plain
    string — "Tarneit, Truganina" indexed to "T" and that single letter was written to
    the audit trail as the suburb searched.
    """
    raw = brief_dict.get('primary_suburbs')
    if isinstance(raw, str):
        parts = [s.strip() for s in raw.split(',') if s.strip()]
    elif isinstance(raw, (list, tuple)):
        parts = [str(s).strip() for s in raw if str(s).strip()]
    else:
        parts = []
    return parts[0][:120] if parts else 'General'

# Columns added to `buildings` after its original CREATE TABLE, with their types.
# Declared here (not inline) so a new field is one line and the type is explicit.
BUILDINGS_EXTRA_COLUMNS = (
    # price breakdown (SOP Step 6)
    ("land_price", "REAL"), ("build_price", "REAL"), ("extraction_confidence", "REAL"),
    # fields Coleen asked for on 28/29 July
    ("availability_status", "TEXT"),     # Available / On Hold / Under Offer / Sold
    ("storey", "TEXT"),                  # SINGLE / DOUBLE
    ("estate_name", "TEXT"),
    ("lot_number", "TEXT"),              # TEXT: also holds stock codes like "CC-0122"
    ("postcode", "TEXT"),                # TEXT: NT postcodes have a leading zero (0800)
    ("frontage_m", "REAL"),
    # The street part on its own, recovered from source_text by stocklist_reparse.
    # lot_address on 47% of rows is the whole flattened spreadsheet row, and it is what
    # a client reads as the headline of every shortlist card. This is the clean version
    # where the source stated one; lot_address is left exactly as captured.
    ("street_address", "TEXT"),
    ("listing_url", "TEXT"),             # human-openable page for this lot
    ("floorplan_url", "TEXT"),
    ("brochure_url", "TEXT"),
    ("incentive_text", "TEXT"),
    ("incentive_amount", "REAL"),
    # provenance / attribution
    # Which signal decided the state, so a state in front of a buyer is traceable.
    ("state_source", "TEXT"),
    ("builder_matched", "TEXT"),         # registry match, kept apart from the display name
    ("builder_source", "TEXT"),          # heading | filename | portal | registry_text
    ("attribution_scope", "TEXT"),       # builder | state_pooled
    ("product_type", "TEXT"),
    ("source_state_hint", "TEXT"),
    ("stocklist_file", "TEXT"),
    ("storey_source", "TEXT"),
    ("incentive_source", "TEXT"),
    # Full untruncated source row. lot_address is a short, human-readable label for
    # the client's sheet; this keeps everything the parser needs (and an audit
    # trail). Truncating to 110 chars used to cut prices in half mid-number.
    ("source_text", "TEXT"),
    # Best-deals selection — Colin's 30 July ask: filter the database, mark the
    # listings going into the weekly customer promotion, export those.
    #
    # These MUST stay out of building_content_hash and out of record_building's
    # column dict, or a re-harvest would either re-identify every marked row or
    # overwrite the mark. Both exclude by allow-list rather than by denylist, so
    # a new column is excluded automatically — test_best_deals.py pins that,
    # because the failure is silent and only shows up a week later when Colin's
    # selection has quietly emptied itself.
    ("promo_selected", "INTEGER"),        # 1 = in the weekly promotion
    ("promo_selected_at", "TEXT"),        # when it was marked, for "what changed this week"
    # Superseded rows — an OLDER capture of a lot that is also stored fresher.
    #
    # content_hash includes lot_number and a key derived from the source row's text,
    # so when the extractor's output for a lot changes between harvests the identity
    # changes with it and the next run INSERTS instead of updating. One lot was found
    # stored three times: 27 Jul with lot_number '9' and no source text at $962,351,
    # then 3 Aug through the same channel with lot_number NULL and the text populated
    # at $1,058,877. 777 rows — 12% of the table — are surplus copies like that.
    #
    # Marked rather than deleted, per the standing rule: the older capture is real
    # history and its price is what was advertised at the time.
    ("superseded_by", "TEXT"),            # content_hash of the surviving row
    ("superseded_at", "TEXT"),
    # benchmarking (Coleen's 22 July ask)
    ("benchmark_median", "REAL"), ("benchmark_variance_pct", "REAL"),
    ("benchmark_classification", "TEXT"), ("benchmark_basis", "TEXT"),
    # identity + change tracking
    # Persisted so a re-hash can reproduce the identity the extractor computed. Without
    # it, migrating price-only siblings collapses them into a colliding pair and the
    # unique index cannot be created.
    ("variant_ordinal", "INTEGER"),
    ("content_hash", "TEXT"),
    ("first_seen", "TEXT"), ("last_seen", "TEXT"),
    ("price_previous", "REAL"), ("status_previous", "TEXT"),
)

# Bump when the recipe changes, so old hashes are recognisably stale.
# v2: title status (titled / registered / untitled) and spelled-out title quarters no
#     longer enter the key. A lot goes from untitled to titled during its life, so
#     hashing it meant a lot getting titled appeared as a brand new listing. Identity
#     is also now taken from a fixed-length prefix of the row (IDENTITY_TEXT_CHARS)
#     rather than the whole of it. Re-hash with migrate_buildings_identity.py.
HASH_RECIPE_VERSION = "v2"

# How much of a listing's source row identifies it. Must stay BELOW the 110-character
# truncation that rows stored before `source_text` existed were subject to — see
# _variant_key. Raising it would re-identify those rows and duplicate them.
IDENTITY_TEXT_CHARS = 100

# Fields that identify a listing. Deliberately EXCLUDES price, availability,
# title_status and date_checked — those are the values that should update in place
# rather than create a second row. Also excludes state/builder_matched, which the
# enrichment pass writes: hashing them would make enrichment duplicate the table.
_HASH_FIELDS = ("source_channel", "attribution_scope", "builder_key", "suburb_norm",
                "lot_number", "house_design", "land_sqm", "variant_key")


def _norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


# Volatile content stripped out of the variant key, so a price move or a status
# flip does not change a listing's identity.
_STRIP_MONEY = re.compile(r"\$\s?[\d,\.]+\s*k?", re.I)
_STRIP_STATUS = re.compile(r"\b(available|not\s*available|unavailable|on\s*hold|hold|"
                           r"under\s*offer|sold|reserved|leased|tbc|"
                           # title status changes over a lot's life (untitled -> titled),
                           # so it identifies nothing and must not enter the key
                           r"titled|untitled|registered|unregistered)\b", re.I)
_STRIP_DATES = re.compile(r"\b(?:q[1-4][\s-]*\d{2,4}|quarter\s*[1-4][\s,-]*\d{2,4}|"
                          r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
                          r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s-]*\d{2,4})\b", re.I)
_STRIP_PCT = re.compile(r"\d+(?:\.\d+)?\s*%")
# Rows stored before `source_text` existed were cut at 110 characters, which can slice
# the final token in half ("... Available" -> "... avail", "... Sep-26" -> "... Sep-2").
# The whole-word patterns above cannot see a half word, so the debris survives into the
# key and the row re-identifies on the next harvest. Only the LAST token can be
# damaged, so the prefix-tolerant patterns are anchored to end-of-string.
_STATUS_WORDS = ("available", "unavailable", "notavailable", "hold", "onhold",
                 "underoffer", "under", "offer", "sold", "reserved", "leased", "tbc",
                 "titled", "untitled", "registered", "unregistered")
_STRIP_STATUS_TAIL = re.compile(
    r"\s+(?:" + "|".join(sorted({w[:i] for w in _STATUS_WORDS for i in range(3, len(w) + 1)},
                                key=len, reverse=True)) + r")$", re.I)
_STRIP_DATE_TAIL = re.compile(
    r"\s+(?:q(?:u(?:a(?:r(?:t(?:e(?:r)?)?)?)?)?)?[\s,-]*[1-4]?[\s,-]*\d{0,4}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s-]*\d{0,4}"
    r"|\d{1,2}[/-]\d{0,2}(?:[/-]\d{0,4})?)$", re.I)


def _variant_key(b: Dict[str, Any]) -> str:
    """Distinguishes real product variants that share a lot number.

    Verified need: the VIC Regional stocklist lists Lot 414 twice — once as
    "Arklay 17" and once as "Dunestone 22" — and NSW lists Lot 116 as both
    "Vesper SG" and "Vesper DG". Those are different packages at different prices
    and must stay separate rows. Prices, availability, dates and yields are
    stripped so the *same* package keeps its identity when its price moves.

    Reads `source_text` (the full row) in preference to `lot_address`, which is now a
    short client-facing label — "Lot 414, Clearwater" alone cannot tell an Arklay 17
    from a Dunestone 22.

    Only the first IDENTITY_TEXT_CHARS of the row are used. Rows stored before
    `source_text` existed hold the row text cut at 110 characters, so a shorter prefix
    is a prefix of BOTH the old truncated text and the new full text — which makes the
    identity of all 373 rows Coleen has already reviewed survive this change exactly,
    rather than approximately. Verified: 0 of 373 re-identify and 0 collide.
    """
    t = _norm(b.get("source_text") or b.get("lot_address"))[:IDENTITY_TEXT_CHARS]
    for rx in (_STRIP_MONEY, _STRIP_STATUS, _STRIP_DATES, _STRIP_PCT):
        t = rx.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # the prefix cut can leave half a word behind; it lands identically on both sides,
    # but stripping it keeps the key readable in the DB for a human checking a match
    for rx in (_STRIP_STATUS_TAIL, _STRIP_DATE_TAIL):
        t = rx.sub("", t).strip()
    return t


def building_content_hash(b: Dict[str, Any]) -> str:
    """Stable identity for a listing, computed from the extractor's output.

    MUST be computed at insert time from the upstream dict, never recomputed from a
    DB row — enrichment writes non-hashed columns only, so the next harvest derives
    the same hash from the same source file and updates in place.
    """
    parts = {
        "source_channel": _norm(b.get("source_channel")),
        "attribution_scope": _norm(b.get("attribution_scope")),
        "builder_key": _norm(b.get("builder_name")),
        "suburb_norm": _norm(b.get("suburb")),
        "lot_number": _norm(b.get("lot_number")),
        "house_design": _norm(b.get("house_design")),
        "land_sqm": _norm(b.get("land_size_sqm") or b.get("land_sqm")),
        "variant_key": _variant_key(b),
    }
    basis = "|".join(f"{k}={parts[k]}" for k in _HASH_FIELDS)
    # Two rows in one stocklist can be identical in every field above and still be two
    # real packages — verified on the APLACE, Met Invest and Paramount files, where the
    # only difference is the price, which identity deliberately ignores so that a price
    # move updates in place. The extractor numbers such siblings; without this, 10 of
    # 983 per-builder listings silently replaced each other.
    # Appended ONLY when non-zero, so every already-stored row's hash is unchanged.
    ordinal = int(b.get("variant_ordinal") or 0)
    if ordinal:
        basis += f"|variant_ordinal={ordinal}"
    return f"{HASH_RECIPE_VERSION}:{hashlib.sha256(basis.encode('utf-8')).hexdigest()}"


class ResearchDatabase:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or config.DATABASE_PATH
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(str(self.db_path))

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS research_records (
                    record_id TEXT PRIMARY KEY,
                    client_name TEXT,
                    state TEXT,
                    suburb TEXT,
                    budget_max REAL,
                    start_time TEXT,
                    agent_version TEXT,
                    shortlist_count INTEGER,
                    rejected_count INTEGER,
                    created_at TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS candidate_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id TEXT,
                    property_id TEXT,
                    lot_address TEXT,
                    suburb TEXT,
                    builder_name TEXT,
                    price REAL,
                    score REAL,
                    verification_status TEXT,
                    recommendation TEXT,
                    source_ref TEXT,
                    date_checked TEXT,
                    FOREIGN KEY(record_id) REFERENCES research_records(record_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS rejection_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id TEXT,
                    property_id TEXT,
                    address TEXT,
                    reason TEXT,
                    FOREIGN KEY(record_id) REFERENCES research_records(record_id)
                )
            """)

            # Vendor (builder) directory imported from Coleen's vendor CSV.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS builders (
                    builder_name TEXT PRIMARY KEY,
                    contact_name TEXT,
                    email TEXT,
                    phone TEXT,
                    states TEXT,
                    website TEXT,
                    portal_url TEXT,
                    is_on_e_agent INTEGER,
                    source_section TEXT,
                    has_website INTEGER,
                    notes TEXT,
                    updated_at TEXT
                )
            """)

            # Marketing assets (brochures, fliers, floorplans, price lists) harvested
            # from each builder's public website.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS builder_assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    builder_name TEXT,
                    asset_type TEXT,
                    title TEXT,
                    source_url TEXT,
                    local_path TEXT,
                    file_size INTEGER,
                    sha256 TEXT UNIQUE,
                    scraped_from TEXT,
                    extracted_text TEXT,
                    downloaded_at TEXT,
                    FOREIGN KEY(builder_name) REFERENCES builders(builder_name)
                )
            """)
            # Add extracted_text to pre-existing DBs that lack it.
            cols = [r[1] for r in cursor.execute("PRAGMA table_info(builder_assets)").fetchall()]
            if "extracted_text" not in cols:
                cursor.execute("ALTER TABLE builder_assets ADD COLUMN extracted_text TEXT")

            # Building stock harvested from E-Agent + direct builder portals.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS buildings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    builder_name TEXT,
                    source_channel TEXT,
                    lot_address TEXT,
                    suburb TEXT,
                    state TEXT,
                    price REAL,
                    bedrooms INTEGER,
                    bathrooms INTEGER,
                    car_spaces INTEGER,
                    land_sqm REAL,
                    house_sqm REAL,
                    title_status TEXT,
                    source_url TEXT,
                    dedup_key TEXT UNIQUE,
                    date_checked TEXT
                )
            """)
            # Additive columns, declared with their type. The previous version of this
            # loop hard-coded REAL, which silently made every TEXT field numeric.
            bcols = [r[1] for r in cursor.execute("PRAGMA table_info(buildings)").fetchall()]
            for col, coltype in BUILDINGS_EXTRA_COLUMNS:
                if coltype not in ("TEXT", "REAL", "INTEGER"):
                    raise ValueError(f"unsupported column type {coltype!r} for {col!r}")
                if col not in bcols:
                    cursor.execute(f"ALTER TABLE buildings ADD COLUMN {col} {coltype}")

            # Records whether a data backfill has run — PRAGMA tells us a column
            # exists, but nothing about whether its values were populated.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            conn.commit()

    # ---------- identity ----------
    def get_meta(self, key: str) -> Optional[str]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT value FROM schema_meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else None

    def set_meta(self, key: str, value: str):
        with self._get_connection() as conn:
            conn.execute("INSERT INTO schema_meta(key,value) VALUES(?,?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
            conn.commit()

    # ---------- Building stock ----------
    def record_building(self, b: Dict[str, Any]) -> str:
        """Upsert a harvested building. Returns "new" | "updated" | "unchanged".

        Identity is `content_hash` over the listing's stable fields, so a re-harvest
        of the same lot UPDATES its price/availability in place rather than adding a
        second row — and a failed source never deletes anything. Rows not seen in a
        run simply keep an older `last_seen`.

        NOTE: callers that count "new" rows must compare against the string, not
        truthiness — every return value here is truthy.
        """
        now_date = b.get("date_checked") or datetime.now().strftime("%d/%m/%Y")

        # One builder, one name — BEFORE the hash, never after. builder_name feeds
        # builder_key in content_hash, so rewriting it over stored rows would change
        # their identity and make the next harvest insert them all again. That is the
        # mechanism that produced 777 duplicate captures. Resolving it here means
        # "hattan.com.au" and "Hattan Homes" hash to the same listing every run.
        canon = self._builder_canonicaliser()
        if canon is not None and b.get("builder_name"):
            fixed = canon.canonical(b["builder_name"])
            if fixed and fixed != b["builder_name"]:
                b = {**b, "builder_name": fixed}

        # Recover, from the row's own text, the facts its columns lack. Applied on the
        # way in so a re-harvest lands clean rather than needing a backfill afterwards —
        # and BEFORE the hash for the same reason the builder name is: these values feed
        # the identity, so filling them later would change it and re-insert every row.
        # It only ever fills a field that is empty (see stocklist_reparse.recover), so it
        # cannot overwrite anything a source actually stated.
        recovered = _reparse_recover(b)
        if recovered:
            b = {**b, **recovered}

        h = b.get("content_hash") or building_content_hash(b)
        price = float(b.get("advertised_package_price") or b.get("price") or 0)
        status = b.get("availability_status")

        # `dedup_key` carries a UNIQUE constraint from before content_hash existed, and
        # its old basis (builder||lot_address||suburb||price) silently rejected 160 of
        # 943 real listings once lot_address became a short label: "Lot 68" repeats
        # across estates where a whole jammed row never did. Setting it to the identity
        # hash makes the legacy constraint agree with the real one instead of fighting
        # it. (Dropping a UNIQUE column in SQLite means rebuilding the table, which is
        # not worth doing to the client's reviewed data.)
        key = h

        cols = {
            "builder_name": b.get("builder_name", ""),
            "source_channel": b.get("source_channel", ""),
            "lot_address": b.get("lot_address", ""),
            "suburb": b.get("suburb", ""),
            "state": b.get("state", ""),
            "price": price,
            "bedrooms": b.get("bedrooms"),
            "bathrooms": b.get("bathrooms"),
            "car_spaces": b.get("car_spaces"),
            "land_sqm": b.get("land_size_sqm") or b.get("land_sqm"),
            "house_sqm": b.get("house_size_sqm") or b.get("house_sqm"),
            "title_status": b.get("title_status", ""),
            "source_url": b.get("source_url_or_ref") or b.get("source_url", ""),
            "dedup_key": key,
            "date_checked": now_date,
            "land_price": b.get("land_price"),
            "build_price": b.get("build_price"),
            "extraction_confidence": b.get("extraction_confidence"),
            "availability_status": status,
            "storey": b.get("storey"),
            "estate_name": b.get("estate_name"),
            "lot_number": b.get("lot_number"),
            "postcode": b.get("postcode"),
            "frontage_m": b.get("frontage_m"),
            "listing_url": b.get("listing_url"),
            "floorplan_url": b.get("floorplan_url"),
            "brochure_url": b.get("brochure_url"),
            "incentive_text": b.get("incentive_text"),
            "incentive_amount": b.get("incentive_amount"),
            "builder_source": b.get("builder_source"),
            "attribution_scope": b.get("attribution_scope"),
            "product_type": b.get("product_type"),
            "source_state_hint": b.get("source_state_hint"),
            "stocklist_file": b.get("stocklist_file"),
            "storey_source": b.get("storey_source"),
            "incentive_source": b.get("incentive_source"),
            "source_text": b.get("source_text") or b.get("lot_address", ""),
            "variant_ordinal": int(b.get("variant_ordinal") or 0),
            "content_hash": h,
            "first_seen": now_date,
            "last_seen": now_date,
        }

        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT id, price, availability_status FROM buildings WHERE content_hash=?", (h,)
            ).fetchone()

            if existing is None:
                names = ", ".join(cols)
                marks = ", ".join("?" for _ in cols)
                try:
                    conn.execute(f"INSERT INTO buildings ({names}) VALUES ({marks})",
                                 tuple(cols.values()))
                    conn.commit()
                    return "new"
                except sqlite3.IntegrityError as e:
                    # Never silent: swallowing this is how 160 listings went missing
                    # without a trace. A live listing dropped by a constraint is a bug
                    # to see, not a row to quietly discard.
                    logger.warning("buildings: %r rejected by a UNIQUE constraint (%s) — "
                                   "listing NOT stored.", b.get("lot_address"), e)
                    return "unchanged"

            rid, old_price, old_status = existing
            # Only a value this run actually read counts as a change — an absent field
            # is "not observed", not "moved to blank".
            changed = bool(price and (old_price or 0) != price) or \
                bool(status and (old_status or None) != status)

            # Update the volatile fields in place; leave enrichment-owned columns
            # (state, builder_matched, benchmark_*) alone unless this run supplied one.
            upd = {"date_checked": now_date, "last_seen": now_date}
            # A run that could not read a field must not erase what is stored. Writing
            # these unconditionally meant one unparsed row blanked the availability and
            # title Coleen asked for, and zeroed its price.
            for c in ("price", "availability_status", "title_status", "source_url",
                      "extraction_confidence", "land_price", "build_price"):
                v = price if c == "price" else cols[c]
                if v not in (None, "", 0, 0.0):
                    upd[c] = v
            if changed:
                upd["price_previous"] = old_price
                upd["status_previous"] = old_status
            # These two are re-derived from the same source row every run, so this
            # run's version is authoritative — and without them a re-harvest would
            # leave the old jammed-together address and an empty source_text on the
            # rows Coleen is reading.
            for c in ("lot_address", "source_text"):
                if cols.get(c) not in (None, ""):
                    upd[c] = cols[c]
            # fill any detail field that is currently empty
            for c in ("builder_name", "suburb", "storey", "estate_name", "lot_number",
                      "postcode", "frontage_m", "listing_url", "floorplan_url",
                      "brochure_url", "incentive_text", "incentive_amount", "bedrooms",
                      "bathrooms", "car_spaces", "land_sqm", "house_sqm", "state"):
                if cols.get(c) not in (None, ""):
                    upd[c] = f"COALESCE(NULLIF(buildings.{c},''), ?)"
            sets, params = [], []
            for c, v in upd.items():
                if isinstance(v, str) and v.startswith("COALESCE("):
                    sets.append(f"{c}={v}")
                    params.append(cols[c])
                else:
                    sets.append(f"{c}=?")
                    params.append(v)
            conn.execute(f"UPDATE buildings SET {', '.join(sets)} WHERE id=?", (*params, rid))
            conn.commit()
            return "updated" if changed else "unchanged"

    def get_buildings(self, builder_name: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            if builder_name:
                rows = conn.execute("SELECT * FROM buildings WHERE builder_name=? ORDER BY price", (builder_name,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM buildings ORDER BY builder_name, price").fetchall()
            return [dict(r) for r in rows]

    def _builder_canonicaliser(self):
        """Built once per connection from the names already stored.

        Learned from the data rather than a hand-written list, so a builder the app
        has never seen simply passes through unchanged instead of being bent toward
        the nearest name someone once typed.
        """
        if getattr(self, "_canon", None) is None:
            try:
                from builder_names import BuilderNameCanonicaliser
                with self._get_connection() as conn:
                    names = [r[0] for r in conn.execute(
                        "SELECT DISTINCT builder_name FROM buildings "
                        "WHERE TRIM(COALESCE(builder_name,'')) <> ''") if r[0]]
                self._canon = BuilderNameCanonicaliser(names)
            except Exception as e:  # pragma: no cover - never block a harvest
                logger.debug("builder canonicaliser unavailable: %s", e)
                self._canon = False
        return self._canon or None

    # ---------- Best-deals selection (Colin, 30 Jul) ----------
    def set_promo_selection(self, content_hashes: List[str], selected: bool) -> int:
        """Mark or unmark listings for the weekly promotion. Returns rows changed.

        Keyed by content_hash, not by id: id is a local autoincrement that means
        nothing to the deployed snapshot, whereas content_hash is the same listing's
        identity before and after a re-harvest. That is the whole point — a selection
        made on Monday has to still point at the same lots on Friday.
        """
        keys = [k for k in (content_hashes or []) if k]
        if not keys:
            return 0
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if selected else None
        with self._get_connection() as conn:
            changed = 0
            for chunk in (keys[i:i + 400] for i in range(0, len(keys), 400)):
                marks = ",".join("?" * len(chunk))
                cur = conn.execute(
                    f"UPDATE buildings SET promo_selected=?, promo_selected_at=? "
                    f"WHERE content_hash IN ({marks})",
                    [1 if selected else 0, stamp, *chunk])
                changed += cur.rowcount
            conn.commit()
            return changed

    def clear_promo_selection(self) -> int:
        with self._get_connection() as conn:
            cur = conn.execute(
                "UPDATE buildings SET promo_selected=0, promo_selected_at=NULL "
                "WHERE promo_selected=1")
            conn.commit()
            return cur.rowcount

    def get_promo_selected(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM buildings WHERE promo_selected=1 "
                "ORDER BY state, suburb, price").fetchall()
            return [dict(r) for r in rows]

    def building_counts_by_channel(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT source_channel, COUNT(*) AS n FROM buildings GROUP BY source_channel ORDER BY n DESC
            """).fetchall()
            return [dict(r) for r in rows]

    # ---------- Vendor directory ----------
    def upsert_builder(self, b: Dict[str, Any]):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO builders
                (builder_name, contact_name, email, phone, states, website, portal_url,
                 is_on_e_agent, source_section, has_website, notes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(builder_name) DO UPDATE SET
                    contact_name=COALESCE(NULLIF(excluded.contact_name,''), builders.contact_name),
                    email=COALESCE(NULLIF(excluded.email,''), builders.email),
                    phone=COALESCE(NULLIF(excluded.phone,''), builders.phone),
                    states=COALESCE(NULLIF(excluded.states,''), builders.states),
                    website=COALESCE(NULLIF(excluded.website,''), builders.website),
                    portal_url=COALESCE(NULLIF(excluded.portal_url,''), builders.portal_url),
                    is_on_e_agent=MAX(builders.is_on_e_agent, excluded.is_on_e_agent),
                    has_website=MAX(builders.has_website, excluded.has_website),
                    notes=COALESCE(NULLIF(excluded.notes,''), builders.notes),
                    updated_at=excluded.updated_at
            """, (
                b['builder_name'], b.get('contact_name', ''), b.get('email', ''), b.get('phone', ''),
                b.get('states', ''), b.get('website', ''), b.get('portal_url', ''),
                1 if b.get('is_on_e_agent') else 0, b.get('source_section', ''),
                1 if b.get('website') else 0, b.get('notes', ''),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ))
            conn.commit()

    def get_builders(self, only_with_website: bool = False) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            q = "SELECT * FROM builders"
            if only_with_website:
                q += " WHERE has_website=1"
            q += " ORDER BY builder_name"
            return [dict(r) for r in conn.execute(q).fetchall()]

    # ---------- Harvested assets ----------
    def record_asset(self, a: Dict[str, Any]) -> bool:
        """Insert an asset; returns False if this file (by sha256) is already stored."""
        with self._get_connection() as conn:
            try:
                conn.execute("""
                    INSERT INTO builder_assets
                    (builder_name, asset_type, title, source_url, local_path, file_size, sha256, scraped_from, extracted_text, downloaded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    a['builder_name'], a.get('asset_type', 'brochure'), a.get('title', ''),
                    a.get('source_url', ''), a.get('local_path', ''), int(a.get('file_size', 0)),
                    a.get('sha256', ''), a.get('scraped_from', ''), a.get('extracted_text', ''),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False  # duplicate file already stored

    def get_assets(self, builder_name: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            if builder_name:
                rows = conn.execute("SELECT * FROM builder_assets WHERE builder_name=? ORDER BY downloaded_at DESC", (builder_name,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM builder_assets ORDER BY builder_name, downloaded_at DESC").fetchall()
            return [dict(r) for r in rows]

    def asset_counts_by_builder(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT builder_name, COUNT(*) AS asset_count,
                       SUM(CASE WHEN asset_type='brochure' THEN 1 ELSE 0 END) AS brochures
                FROM builder_assets GROUP BY builder_name ORDER BY asset_count DESC
            """).fetchall()
            return [dict(r) for r in rows]

    def save_research_run(self, record_id: str, brief_dict: Dict[str, Any], shortlist: List[Any], rejected_log: List[Dict[str, str]]):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            cursor.execute("""
                INSERT OR REPLACE INTO research_records 
                (record_id, client_name, state, suburb, budget_max, start_time, agent_version, shortlist_count, rejected_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record_id,
                str(brief_dict.get('client_name') or 'Client')[:200],
                brief_dict.get('state', 'QLD'),
                _first_suburb(brief_dict),
                # The browser sends an explicit null for an empty number field, so the key
                # is PRESENT and a get() default never fires — that was the second half of
                # the original 500, which moved here once the parser was hardened. A bare
                # float() then failed again on "abc" and on "750,000", so this shares the
                # parser's coercion rather than inventing a third rule.
                coerce_number(brief_dict.get('budget_max'), 0.0) or 0.0,
                now_str,
                "v3.4-prod",
                len(shortlist),
                len(rejected_log),
                now_str
            ))

            for prop in shortlist:
                cursor.execute("""
                    INSERT INTO candidate_audit 
                    (record_id, property_id, lot_address, suburb, builder_name, price, score, verification_status, recommendation, source_ref, date_checked)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record_id,
                    prop.property_id,
                    prop.lot_address,
                    prop.suburb,
                    prop.builder_name,
                    prop.price_breakdown.realistic_total_price,
                    prop.scoring.total_score if prop.scoring else 0,
                    prop.verification_status.value if hasattr(prop.verification_status, 'value') else str(prop.verification_status),
                    prop.recommendation.value if hasattr(prop.recommendation, 'value') else str(prop.recommendation),
                    prop.source_url_or_ref,
                    prop.date_checked
                ))

            for rej in rejected_log:
                cursor.execute("""
                    INSERT INTO rejection_logs (record_id, property_id, address, reason)
                    VALUES (?, ?, ?, ?)
                """, (record_id, rej.get('property_id', ''), rej.get('address', ''), rej.get('reason', '')))

            conn.commit()

    def get_research_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM research_records ORDER BY created_at DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]
