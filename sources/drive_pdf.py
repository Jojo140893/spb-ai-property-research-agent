"""
Google Drive & PDF Stocklist Search Source.
Ingests candidate property packages from uploaded PDFs and spreadsheets (e.g. Shape Homes).
"""

from typing import List, Dict, Any
from sources.base import PropertySource
from drive_ingest import DriveStocklistIngestor


class DrivePdfSource(PropertySource):
    def __init__(self):
        self.ingestor = DriveStocklistIngestor()

    @property
    def channel_name(self) -> str:
        return "Google Drive PDF/Spreadsheet Stocklist"

    def search(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        all_packages = self.ingestor.scan_and_ingest_all()
        max_budget = float(filters.get('budget_max', 800000))
        return [p for p in all_packages if p.get('advertised_package_price', 0) <= max_budget]

    def verify(self, package: Dict[str, Any]) -> Dict[str, Any]:
        return {
            'verified': True,
            'status': 'Verified (Document Dated)',
            'date_checked': package.get('date_checked', ''),
            'price_change': 0.0
        }
