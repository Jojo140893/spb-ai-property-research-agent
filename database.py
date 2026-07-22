"""
SQLite Audit Trail & Research Database (SOP Step 1 & 14).
Stores persisted research records, evaluated candidate properties, rejection logs,
and Kommo CRM payload audit trails.
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import config


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

            conn.commit()

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
