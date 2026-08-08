"""
Builder Registry Module for SPB AI Property Research Agent.
Fixes Defect #2: Rewritten parser to ingest only primary builder table (lines 1-52),
disambiguate duplicate EMAIL columns (contact vs portal login), normalize phone numbers,
and map portal access channels.
"""

import csv
import os
import re
from pathlib import Path
from typing import List, Dict, Optional, Any
from config import BUILDER_CSV_PATH


class BuilderRegistry:
    def __init__(self, csv_filepath: Optional[str] = None):
        self.csv_filepath = Path(csv_filepath) if csv_filepath else BUILDER_CSV_PATH
        self.builders: List[Dict[str, Any]] = []
        self._load_primary_builders()
        self._resolve_domain_named_builders()

    def _load_primary_builders(self):
        if not self.csv_filepath.exists():
            return

        with open(self.csv_filepath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        # Defect #2 Fix: Isolate Primary Builder Section (lines 1 to 52)
        primary_lines = []
        for line in lines:
            # Stop parsing when reaching secondary sections like LinkedIn lists or Perth list
            if 'LinkedIn Link' in line or 'BUILDERS IN PERTH' in line:
                break
            primary_lines.append(line)

        if not primary_lines:
            return

        reader = csv.reader(primary_lines)
        header = next(reader, None)
        if not header:
            return

        for row in reader:
            if not row or not any(row):
                continue
            
            # Primary builder table has columns:
            # 0: NAME, 1: EMAIL (contact), 2: PHONE, 3: BUILDER, 4: STATES,
            # 5: Contract Availble??, 6: Is it available on E Agent?, 7: WEB PORTAL LINK,
            # 8: EMAIL (portal login), 9: PASSWORD, 10: NOTES
            builder_name = (row[3] if len(row) > 3 else '').strip()
            if not builder_name:
                continue

            contact_name = (row[0] if len(row) > 0 else '').strip()
            contact_email = (row[1] if len(row) > 1 else '').strip()
            contact_phone = self._normalize_phone(row[2] if len(row) > 2 else '')
            states_raw = (row[4] if len(row) > 4 else '').strip()
            contract_available = (row[5] if len(row) > 5 else '').strip()
            e_agent_available = (row[6] if len(row) > 6 else '').strip()
            portal_url = (row[7] if len(row) > 7 else '').strip()
            portal_login_email = (row[8] if len(row) > 8 else '').strip()
            portal_login_password = (row[9] if len(row) > 9 else '').strip()
            notes = (row[10] if len(row) > 10 else '').strip()

            states = [s.strip().upper() for s in states_raw.replace('/', ',').split(',') if s.strip()]

            # Determine primary stock channel
            is_on_e_agent = 'YES' in e_agent_available.upper()
            if is_on_e_agent:
                stock_channel = 'e_agent'
            elif portal_url:
                stock_channel = 'portal'
            elif 'email' in notes.lower() or 'pdf' in notes.lower():
                stock_channel = 'email_pdf'
            else:
                stock_channel = 'direct_contact'

            self.builders.append({
                'builder_name': builder_name,
                'contact_name': contact_name,
                'contact_email': contact_email,
                'contact_phone': contact_phone,
                'states': states,
                'contract_available': contract_available,
                'e_agent_available': e_agent_available,
                'is_on_e_agent': is_on_e_agent,
                'portal_url': portal_url,
                'portal_login_email': portal_login_email,
                'portal_login_password': portal_login_password,
                'notes': notes,
                'stock_channel': stock_channel
            })

    def _resolve_domain_named_builders(self):
        """A WEBSITE typed into the BUILDER column is not a second builder.

        The seed of the Hattan split. `Book1(Builders) List.csv` line 21 reads

            Brian Zeeman,BrianZ@hattan.com.au,0437 503 015,hattan.com.au,VIC,Yes,YES,,,,

        — the builder's site in the name column — while line 30 carries the real
        `Hattan Homes`. Everything downstream trusted the registry, so both spellings
        went out as two suppliers: email_inbox's domain_map answered the literal string
        "hattan.com.au" for mail from that domain, and enrich's name-key builder strips
        \\b(au|com)\\b so both rows collapsed onto the key "hattan", where setdefault let
        line 21 win. 192 rows were stored under the domain, 67 of them still live.

        Resolved here, once, at the point the fault enters the system, rather than in
        each consumer that happens to notice. This is builder_names' transformation 1 —
        a bare domain resolved against a name we already hold — and nothing more: a
        domain that matches no registered builder is LEFT AS IT WAS FOUND, because
        inventing "Hattan" out of "hattan.com.au" would be a guess, and the standing
        rule is that a raw value beats a plausible invention.

        The code is the guard, not the cure: line 21 should be corrected in the sheet so
        the client's own contact record names the builder rather than the website.
        """
        try:
            from builder_names import BuilderNameCanonicaliser, looks_like_domain
        except Exception:                                        # pragma: no cover
            return
        # learn() refuses to register a domain as a canonical target, so the resolver is
        # built from the registry's real names and cannot map one domain onto another.
        canon = BuilderNameCanonicaliser(b['builder_name'] for b in self.builders)
        for b in self.builders:
            if not looks_like_domain(b['builder_name']):
                continue
            fixed = canon.canonical(b['builder_name'])
            if fixed and fixed != b['builder_name']:
                b['builder_name_source'] = (
                    f"resolved from the website {b['builder_name']!r} in the BUILDER column")
                b['builder_name'] = fixed

    def _normalize_phone(self, raw_phone: str) -> str:
        cleaned = re.sub(r'[^\d+]', '', raw_phone)
        if len(cleaned) == 9 and not cleaned.startswith('0'):
            cleaned = '0' + cleaned
        return cleaned

    def get_all_builders(self) -> List[Dict[str, Any]]:
        return self.builders

    def get_builders_by_state(self, state: str) -> List[Dict[str, Any]]:
        state_upper = state.strip().upper()
        return [b for b in self.builders if state_upper in b['states'] or not b['states']]

    def search_builder_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        name_lower = name.lower()
        for b in self.builders:
            if name_lower in b['builder_name'].lower():
                return b
        return None
