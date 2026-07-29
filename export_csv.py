"""
Export everything harvested into CSV files.

Writes to output/:
  buildings_<date>.csv        every property scraped (stock)
  brochures_<date>.csv        harvested brochures/floorplans + extracted details
  vendors_<date>.csv          the vendor directory (NO passwords)

Usage:
    python export_csv.py                 # all three
    python export_csv.py buildings       # just one
"""

import csv
import sys
from datetime import datetime

import config
from database import ResearchDatabase

STAMP = datetime.now().strftime("%Y%m%d")

BUILDING_COLS = [
    ("builder_name", "Builder"),
    ("lot_address", "Lot / Address"),
    ("suburb", "Suburb"),
    ("state", "State"),
    ("price", "Package Price"),
    ("land_price", "Land Price"),
    ("build_price", "Build Price"),
    ("bedrooms", "Beds"),
    ("bathrooms", "Baths"),
    ("car_spaces", "Cars"),
    ("land_sqm", "Land m2"),
    ("house_sqm", "House m2"),
    ("title_status", "Title / Registration"),
    ("source_channel", "Source"),
    ("extraction_confidence", "Confidence"),
    ("date_checked", "Date Checked"),
    ("source_url", "Source URL"),
]

BROCHURE_COLS = [
    ("builder_name", "Builder"),
    ("title", "Title"),
    ("asset_type", "Type"),
    ("file_size", "Size (bytes)"),
    ("local_path", "Local File"),
    ("source_url", "Source URL"),
    ("downloaded_at", "Downloaded"),
    ("extracted_text", "Building Details (extracted)"),
]

# Deliberately excludes portal_login_password — never export secrets.
VENDOR_COLS = [
    ("builder_name", "Builder"),
    ("contact_name", "Contact"),
    ("email", "Email"),
    ("phone", "Phone"),
    ("states", "States"),
    ("website", "Website"),
    ("portal_url", "Portal URL"),
    ("is_on_e_agent", "On E-Agent"),
    ("has_website", "Has Website"),
    ("source_section", "CSV Section"),
    ("notes", "Notes"),
]


def _write(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig = clean in Excel
        w = csv.writer(f)
        w.writerow([label for _k, label in cols])
        for r in rows:
            out = []
            for key, _label in cols:
                v = r.get(key)
                if isinstance(v, str):
                    v = " ".join(v.split())      # flatten newlines/tabs so Excel behaves
                out.append("" if v is None else v)
            w.writerow(out)
    print(f"  [OK] {path.name:<34} {len(rows):>5} rows")
    return path


def export_buildings(db):
    rows = db.get_buildings()
    rows.sort(key=lambda r: (r.get("builder_name") or "", r.get("price") or 0))
    return _write(config.OUTPUT_DIR / f"buildings_{STAMP}.csv", BUILDING_COLS, rows)


def export_brochures(db):
    rows = db.get_assets()
    return _write(config.OUTPUT_DIR / f"brochures_{STAMP}.csv", BROCHURE_COLS, rows)


def export_vendors(db):
    rows = db.get_builders()
    return _write(config.OUTPUT_DIR / f"vendors_{STAMP}.csv", VENDOR_COLS, rows)


def main(which: str = ""):
    db = ResearchDatabase()
    config.OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"Exporting to {config.OUTPUT_DIR}")
    jobs = {"buildings": export_buildings, "brochures": export_brochures, "vendors": export_vendors}
    todo = {which: jobs[which]} if which in jobs else jobs
    written = [fn(db) for fn in todo.values()]
    print(f"\n{len(written)} file(s) written.")
    return written


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
