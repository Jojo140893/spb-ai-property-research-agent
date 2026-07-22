"""
Proxima Platform Search Source (Stub).
Logs clear TODO and deferred status until client credentials arrive.
"""

from typing import List, Dict, Any
from sources.base import PropertySource


class ProximaSource(PropertySource):
    @property
    def channel_name(self) -> str:
        return "Proxima Platform"

    def search(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        print("[PROXIMA SOURCE STUB] Search requested. Pending Proxima credentials from client.")
        return []

    def verify(self, package: Dict[str, Any]) -> Dict[str, Any]:
        return {'verified': False, 'status': 'Pending Confirmation', 'price_change': 0.0}
