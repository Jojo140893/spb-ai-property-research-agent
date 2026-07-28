"""
Google Drive & Local File Stocklist Ingestion Module.
Ingests PDF stocklists (e.g. Shape Homes weekly stocklist) and CSV/XLSX files dropped into
the drive_input directory or Google Drive folder, extracting candidate property packages.
"""

import os
import csv
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import config


class DriveStocklistIngestor:
    def __init__(self, watch_dir: Optional[Path] = None):
        self.watch_dir = watch_dir or config.DRIVE_INPUT_DIR

    def scan_and_ingest_all(self) -> List[Dict[str, Any]]:
        packages: List[Dict[str, Any]] = []
        if not self.watch_dir.exists():
            return packages

        for file_path in self.watch_dir.iterdir():
            # comparables*.csv files are market benchmark data (BenchmarkEngine), not stock lists
            if file_path.name.lower().startswith('comparables'):
                continue
            if file_path.suffix.lower() == '.csv':
                packages.extend(self.ingest_csv(file_path))
            elif file_path.suffix.lower() == '.pdf':
                packages.extend(self.ingest_pdf(file_path))
            elif file_path.suffix.lower() in ('.xlsx', '.xls'):
                packages.extend(self.ingest_spreadsheet(file_path))

        return packages

    def ingest_csv(self, file_path: Path) -> List[Dict[str, Any]]:
        packages = []
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    lot_address = row.get('Address') or row.get('Lot') or row.get('lot_address')
                    if not lot_address:
                        continue
                    packages.append({
                        'lot_address': lot_address,
                        'suburb': row.get('Suburb', 'Target Suburb'),
                        'state': row.get('State', 'QLD'),
                        'builder_name': row.get('Builder', 'Shape Homes'),
                        'house_design': row.get('Design', 'Standard Design'),
                        'bedrooms': int(row.get('Beds', 4)),
                        'bathrooms': int(row.get('Baths', 2)),
                        'car_spaces': int(row.get('Cars', 2)),
                        'storeys': int(row.get('Storeys', 1)),
                        'land_size_sqm': float(row.get('LandSQM', 400)),
                        'house_size_sqm': float(row.get('HouseSQM', 185)),
                        'land_price': float(row.get('LandPrice', 300000)),
                        'build_price': float(row.get('BuildPrice', 380000)),
                        'advertised_package_price': float(row.get('TotalPrice', 680000)),
                        'inclusions': {'site_costs_fixed': True, 'driveway_included': True, 'fencing_included': True, 'landscaping_included': True, 'flooring_included': True, 'blinds_included': True, 'hvac_included': True},
                        'title_status': row.get('TitleStatus', 'Titled'),
                        'expected_title_date': 'Ready Now',
                        'source_channel': 'drive_pdf',
                        'source_url_or_ref': str(file_path.name),
                        'date_checked': datetime.now().strftime("%d/%m/%Y")
                    })
        except Exception as e:
            print(f"[DRIVE INGEST ERROR] Failed to parse CSV {file_path.name}: {e}")

        return packages

    def ingest_pdf(self, file_path: Path) -> List[Dict[str, Any]]:
        """Parse a PDF stocklist (e.g. Shape Homes' weekly PDF).

        Uses the adaptive stocklist extractor rather than a fixed regex: real
        builder PDFs vary wildly in layout, and a rigid pattern silently returned
        nothing for anything but one exact format.
        """
        try:
            from sources.spreadsheet_extract import extract_stocklist
        except Exception as e:
            print(f"[DRIVE] cannot load stocklist extractor: {e}")
            return []
        try:
            data = file_path.read_bytes()
        except Exception as e:
            print(f"[DRIVE] cannot read {file_path.name}: {e}")
            return []
        got = extract_stocklist(data, source_label=str(file_path.name), builder_hint="")
        for g in got:
            g.setdefault("source_channel", "Drive stocklist")
            g["verified"] = False   # a document is a snapshot; confirm before presenting
        return got

    def ingest_spreadsheet(self, file_path: Path) -> List[Dict[str, Any]]:
        """Parse an XLSX/XLS stocklist dropped into drive_input/."""
        try:
            from sources.spreadsheet_extract import extract_stocklist
        except Exception:
            return []
        try:
            data = file_path.read_bytes()
        except Exception:
            return []
        got = extract_stocklist(data, source_label=str(file_path.name), builder_hint="")
        for g in got:
            g.setdefault("source_channel", "Drive stocklist")
            g["verified"] = False
        return got

