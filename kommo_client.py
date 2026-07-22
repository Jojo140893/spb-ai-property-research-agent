"""
Kommo CRM REST API v4 Integration Client.
Handles OAuth2 long-lived token authentication, lead field retrieval,
custom field updates, internal research note appending, and consultant task creation.
Supports Dry-Run mode when credentials are pending.
"""

import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional
import config


class KommoClient:
    def __init__(self, subdomain: Optional[str] = None, access_token: Optional[str] = None, dry_run: Optional[bool] = None):
        self.subdomain = subdomain or config.KOMMO_SUBDOMAIN
        self.access_token = access_token or config.KOMMO_ACCESS_TOKEN
        self.dry_run = config.KOMMO_DRY_RUN if dry_run is None else dry_run
        self.base_url = f"https://{self.subdomain}.kommo.com/api/v4"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "User-Agent": "SPB-Property-Research-Agent/1.0"
        }

    def get_lead(self, lead_id: int) -> Dict[str, Any]:
        if self.dry_run or not self.access_token:
            print(f"[KOMMO DRY-RUN] GET Lead ID #{lead_id}")
            return {"id": lead_id, "name": "Dry-Run Client Lead", "custom_fields_values": []}

        url = f"{self.base_url}/leads/{lead_id}?with=contacts"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            print(f"[KOMMO API ERROR] Failed to fetch lead #{lead_id}: {e.code} {e.reason}")
            return {}

    def update_lead_status_and_fields(self, lead_id: int, status_name: str, custom_fields: Dict[str, Any]) -> bool:
        payload = [
            {
                "id": lead_id,
                "custom_fields_values": [
                    {"field_code": k, "values": [{"value": str(v)}]} for k, v in custom_fields.items()
                ]
            }
        ]

        if self.dry_run or not self.access_token:
            print(f"[KOMMO DRY-RUN] Updated Lead #{lead_id} -> Status: '{status_name}' | Fields: {custom_fields}")
            return True

        url = f"{self.base_url}/leads"
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=self._headers(), method="PATCH")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status == 200
        except urllib.error.HTTPError as e:
            print(f"[KOMMO API ERROR] Failed to update lead #{lead_id}: {e.code} {e.reason}")
            return False

    def add_lead_note(self, lead_id: int, note_text: str) -> bool:
        payload = [
            {
                "entity_id": lead_id,
                "note_type": "common",
                "params": {"text": note_text}
            }
        ]

        if self.dry_run or not self.access_token:
            print(f"[KOMMO DRY-RUN] Appended Internal Note to Lead #{lead_id}:\n{note_text[:120]}...")
            return True

        url = f"{self.base_url}/leads/{lead_id}/notes"
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status in (200, 201)
        except urllib.error.HTTPError as e:
            print(f"[KOMMO API ERROR] Failed to add note to lead #{lead_id}: {e.code} {e.reason}")
            return False

    def create_consultant_review_task(self, lead_id: int, task_title: str, due_hours: int = 24) -> bool:
        import time
        due_timestamp = int(time.time()) + (due_hours * 3600)

        payload = [
            {
                "entity_id": lead_id,
                "entity_type": "leads",
                "task_type_id": 1,
                "text": task_title,
                "complete_till": due_timestamp
            }
        ]

        if self.dry_run or not self.access_token:
            print(f"[KOMMO DRY-RUN] Created Task for Lead #{lead_id}: '{task_title}' (Due in {due_hours}h)")
            return True

        url = f"{self.base_url}/tasks"
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status in (200, 201)
        except urllib.error.HTTPError as e:
            print(f"[KOMMO API ERROR] Failed to create task for lead #{lead_id}: {e.code} {e.reason}")
            return False
