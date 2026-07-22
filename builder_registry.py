"""
Builder Registry Module for SPB AI Property Research Agent.
Parses Book1(Builders) List.csv and provides builder & portal querying functions.
"""

import csv
import os
from typing import List, Dict, Optional


class BuilderRegistry:
    def __init__(self, csv_filepath: str = "d:/kommo/Book1(Builders) List.csv"):
        self.csv_filepath = csv_filepath
        self.builders: List[Dict[str, str]] = []
        self._load_builders()

    def _load_builders(self):
        if not os.path.exists(self.csv_filepath):
            return
        with open(self.csv_filepath, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                builder_name = (row.get('BUILDER') or '').strip()
                if not builder_name:
                    continue
                states_raw = (row.get('STATES') or '').strip()
                states = [s.strip().upper() for s in states_raw.replace('/', ',').split(',') if s.strip()]
                
                self.builders.append({
                    'contact_name': (row.get('NAME') or '').strip(),
                    'contact_email': (row.get('EMAIL') or '').strip(),
                    'contact_phone': (row.get('PHONE') or '').strip(),
                    'builder_name': builder_name,
                    'states': states,
                    'contract_available': (row.get('Contract Availble??') or '').strip(),
                    'e_agent_available': (row.get('Is it available on E Agent?') or '').strip(),
                    'portal_link': (row.get('WEB PORTAL LINK') or '').strip(),
                    'portal_email': (row.get('EMAIL_1') or row.get('EMAIL') or '').strip(),
                    'portal_notes': (row.get('NOTES') or '').strip(),
                })

    def get_all_builders(self) -> List[Dict[str, str]]:
        return self.builders

    def get_builders_by_state(self, state: str) -> List[Dict[str, str]]:
        state_upper = state.strip().upper()
        return [b for b in self.builders if state_upper in b['states'] or not b['states']]

    def get_e_agent_builders(self) -> List[Dict[str, str]]:
        return [b for b in self.builders if 'YES' in b['e_agent_available'].upper()]

    def search_builder_by_name(self, name: str) -> Optional[Dict[str, str]]:
        name_lower = name.lower()
        for b in self.builders:
            if name_lower in b['builder_name'].lower():
                return b
        return None
