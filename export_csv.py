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

# Coleen's read order, taken from how he went down the sheet on 29 July: builder, address,
# suburb, state, availability, storey, price, land size, house price, bathrooms, incentives,
# benchmark, then the links, then the provenance columns at the far right where they do not
# get in the way of reading the stock.
BUILDING_COLS = [
    ("builder_name", "Builder / Development"),
    ("lot_address", "Address"),
    # Lot and postcode sit next to the address because that is how a lot gets quoted
    # back to a builder. Both were stored and never exported; Proxima put a postcode on
    # 1,212 more rows (336 -> 1,548 across the database) and it is the field that
    # settles an ambiguous suburb — "LOGAN" is a locality in two states.
    ("lot_number", "Lot"),
    ("suburb", "Suburb"),
    ("state", "State"),
    ("postcode", "Postcode"),
    ("availability_status", "Availability"),
    ("storey", "Storey"),
    ("price", "Package Price"),
    ("land_sqm", "Land Size m2"),
    ("land_price", "Land Price"),
    ("house_sqm", "House Size m2"),
    ("build_price", "House Price"),
    ("bedrooms", "Beds"),
    ("bathrooms", "Baths"),
    ("car_spaces", "Cars"),
    ("frontage_m", "Frontage m"),
    ("estate_name", "Estate"),
    ("title_status", "Title / Registration"),
    ("incentive_amount", "Incentive $"),
    ("incentive_text", "Incentive"),
    ("benchmark_median", "Market Median"),
    ("benchmark_variance_pct", "Variance vs Market %"),
    ("benchmark_classification", "Vs Market"),
    ("listing_url", "PDF / Listing"),
    ("floorplan_url", "Floor Plan"),
    ("brochure_url", "Brochure"),
    ("date_checked", "Date Checked"),
    ("source_channel", "Source"),
    # provenance, so any cell in front of a buyer can be traced back
    ("attribution_scope", "Attribution"),
    ("state_source", "State From"),
    ("builder_source", "Builder From"),
    ("product_type", "Product"),
    ("extraction_confidence", "Confidence"),
    ("source_url", "Source File"),
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
    # State, then builder, then suburb, then price. Rows whose builder could not be
    # established sort LAST rather than first — an empty string sorts before every name, so
    # the old order opened the sheet on the one thing Coleen complained about.
    rows.sort(key=lambda r: (
        r.get("state") or "zz",
        not (r.get("builder_name") or "").strip(),
        (r.get("builder_name") or "").lower(),
        (r.get("suburb") or "").lower(),
        r.get("price") or 0,
    ))
    return _write(config.OUTPUT_DIR / f"buildings_{STAMP}.csv", BUILDING_COLS, rows)


def export_best_deals(db):
    """Just the listings marked for the weekly promotion (Colin, 30 Jul).

    Same columns and same read order as the full sheet, so the file he sends out is
    the sheet he already knows with everything else taken away. Always written, even
    empty — a missing file looks like the export broke, whereas a header-only file
    says plainly that nothing is marked this week.
    """
    rows = db.get_promo_selected()
    rows.sort(key=lambda r: (
        r.get("state") or "zz",
        not (r.get("builder_name") or "").strip(),
        (r.get("builder_name") or "").lower(),
        (r.get("suburb") or "").lower(),
        r.get("price") or 0,
    ))
    return _write(config.OUTPUT_DIR / f"best_deals_{STAMP}.csv", BUILDING_COLS, rows)


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
    jobs = {"buildings": export_buildings, "best_deals": export_best_deals,
            "brochures": export_brochures, "vendors": export_vendors}
    todo = {which: jobs[which]} if which in jobs else jobs
    written = [fn(db) for fn in todo.values()]
    print(f"\n{len(written)} file(s) written.")
    return written


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
