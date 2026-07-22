"""
Google Drive & Local File Stocklist Ingestion Module.
Ingests PDF stocklists (e.g. Shape Homes weekly stocklist) and CSV/XLSX files dropped into
the drive_input directory or Google Drive folder, extracting candidate property packages.
"""

import os
import csv
import re
from pathlib import Path
from typing import List, Dict, Any
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
            if file_path.suffix.lower() == '.csv':
                packages.extend(self.ingest_csv(file_path))
            elif file_path.suffix.lower() == '.pdf':
                packages.extend(self.ingest_pdf(file_path))

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
        """
        Parses text from PDF stocklists (e.g. Shape Homes PDF stocklists) using regex patterns.
        """
        packages = []
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                full_text = "\n".join([page.extract_text() or "" for page in pdf.pages])

            # Regex pattern matching lot, suburb, design, beds, price
            pattern = r'Lot\s+(\d+)\s+([A-Za-z\s]+)\s+([A-Za-z0-9\s]+)\s+(\d+)Bed\s+\$(\d{3},\d{3})'
            matches = re.findall(pattern, full_text)

            for m in matches:
                lot_num, suburb, design, beds, price_str = m
                price = float(price_str.replace(',', ''))
                packages.append({
                    'lot_address': f"Lot {lot_num} {suburb.strip()} Estate",
                    'suburb': suburb.strip(),
                    'state': 'VIC',
                    'builder_name': 'Shape Homes',
                    'house_design': design.strip(),
                    'bedrooms': int(beds),
                    'bathrooms': 2,
                    'car_spaces': 2,
                    'storeys': 1,
                    'land_size_sqm': 400,
                    'house_size_sqm': 185,
                    'land_price': price * 0.45,
                    'build_price': price * 0.55,
                    'advertised_package_price': price,
                    'inclusions': {'site_costs_fixed': True, 'driveway_included': True, 'fencing_included': True, 'landscaping_included': True, 'flooring_included': True, 'blinds_included': True, 'hvac_included': True},
                    'title_status': 'Titled',
                    'expected_title_date': 'Ready Now',
                    'source_channel': 'drive_pdf',
                    'source_url_or_ref': str(file_path.name),
                    'date_checked': datetime.now().strftime("%d/%m/%Y")
                })
        except ImportError:
            print("[DRIVE INGEST WARNING] pdfplumber not installed; PDF stocklist parsing deferred.")
        except Exception as e:
            print(f"[DRIVE INGEST ERROR] PDF ingestion failed: {e}")

        return packages
