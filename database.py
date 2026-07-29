"""
SQLite Audit Trail & Research Database (SOP Step 1 & 14).
Stores persisted research records, evaluated candidate properties, rejection logs,
and Kommo CRM payload audit trails.
"""

import hashlib
import re
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import config

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
    ("listing_url", "TEXT"),             # human-openable page for this lot
    ("floorplan_url", "TEXT"),
    ("brochure_url", "TEXT"),
    ("incentive_text", "TEXT"),
    ("incentive_amount", "REAL"),
    # provenance / attribution
    ("builder_matched", "TEXT"),         # registry match, kept apart from the display name
    ("builder_source", "TEXT"),          # heading | filename | portal | registry_text
    ("attribution_scope", "TEXT"),       # builder | state_pooled
    ("product_type", "TEXT"),
    ("source_state_hint", "TEXT"),
    ("stocklist_file", "TEXT"),
    ("storey_source", "TEXT"),
    ("incentive_source", "TEXT"),
    # benchmarking (Coleen's 22 July ask)
    ("benchmark_median", "REAL"), ("benchmark_variance_pct", "REAL"),
    ("benchmark_classification", "TEXT"), ("benchmark_basis", "TEXT"),
    # identity + change tracking
    ("content_hash", "TEXT"),
    ("first_seen", "TEXT"), ("last_seen", "TEXT"),
    ("price_previous", "REAL"), ("status_previous", "TEXT"),
)

# Bump when the recipe changes, so old hashes are recognisably stale.
HASH_RECIPE_VERSION = "v1"

# Fields that identify a listing. Deliberately EXCLUDES price, availability,
# title_status and date_checked — those are the values that should update in place
# rather than create a second row. Also excludes state/builder_matched, which the
# enrichment pass writes: hashing them would make enrichment duplicate the table.
_HASH_FIELDS = ("source_channel", "attribution_scope", "builder_key", "suburb_norm",
                "lot_number", "house_design", "land_sqm")


def _norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


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
    }
    basis = "|".join(f"{k}={parts[k]}" for k in _HASH_FIELDS)
    # Degenerate rows (no lot number, no design) would otherwise all collide or go
    # NULL — SQLite treats NULLs as distinct in a unique index, so fall back to the
    # raw row text to guarantee every row gets a value.
    if not (parts["lot_number"] or parts["house_design"]):
        basis += "|raw=" + _norm(b.get("lot_address"))[:200]
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
    def record_building(self, b: Dict[str, Any]) -> bool:
        """Insert a harvested building; returns False if an identical listing already exists."""
        key = "||".join(str(b.get(k, "")).strip().lower() for k in
                        ("builder_name", "lot_address", "suburb", "price"))
        with self._get_connection() as conn:
            try:
                conn.execute("""
                    INSERT INTO buildings
                    (builder_name, source_channel, lot_address, suburb, state, price, bedrooms,
                     bathrooms, car_spaces, land_sqm, house_sqm, title_status, source_url, dedup_key, date_checked,
                     land_price, build_price, extraction_confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    b.get("builder_name", ""), b.get("source_channel", ""), b.get("lot_address", ""),
                    b.get("suburb", ""), b.get("state", ""), float(b.get("advertised_package_price") or b.get("price") or 0),
                    b.get("bedrooms"), b.get("bathrooms"), b.get("car_spaces"),
                    b.get("land_size_sqm"), b.get("house_size_sqm"), b.get("title_status", ""),
                    b.get("source_url_or_ref") or b.get("source_url", ""), key,
                    b.get("date_checked") or datetime.now().strftime("%d/%m/%Y"),
                    b.get("land_price"), b.get("build_price"), b.get("extraction_confidence"),
                ))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def get_buildings(self, builder_name: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            if builder_name:
                rows = conn.execute("SELECT * FROM buildings WHERE builder_name=? ORDER BY price", (builder_name,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM buildings ORDER BY builder_name, price").fetchall()
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
                brief_dict.get('client_name', 'Client'),
                brief_dict.get('state', 'QLD'),
                brief_dict.get('primary_suburbs', ['General'])[0] if brief_dict.get('primary_suburbs') else 'General',
                float(brief_dict.get('budget_max', 0.0)),
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
