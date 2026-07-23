"""
Vendor onboarding pipeline: upload CSV -> import vendors -> harvest website assets -> DB.

This is the "just upload the file and let it collect everything" runner.

Usage:
    python import_and_scrape.py [path/to/vendors.csv] [--limit N] [--import-only] [--builder "Name"]

Defaults to drive_input/vendors.csv. With no flags it imports every vendor,
then crawls each builder that has a public website and stores their brochures /
fliers / booklets in the DB (sorted into assets/<builder>/).

  --import-only   just load the vendor directory, do not crawl websites
  --limit N       crawl only the first N website-bearing builders (useful for a test run)
  --builder NAME  crawl only the one builder whose name matches

NOTE: crawling downloads files from external builder websites. Run it when you
are ready for that live activity; --limit 1 is a safe first check.
"""

import sys
import argparse
from pathlib import Path

import config
from database import ResearchDatabase
from vendor_import import VendorImporter
from sources.website_scraper import WebsiteAssetScraper


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?", default=str(config.DRIVE_INPUT_DIR / "vendors.csv"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--import-only", action="store_true")
    ap.add_argument("--builder", default="")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"[ERROR] Vendor CSV not found: {csv_path}")
        print(f"        Drop the file at {config.DRIVE_INPUT_DIR / 'vendors.csv'} or pass a path.")
        sys.exit(1)

    db = ResearchDatabase()
    print("=" * 70)
    print("  VENDOR IMPORT + WEBSITE ASSET HARVEST")
    print("=" * 70)

    summary = VendorImporter(db).import_to_db(csv_path)
    print(f"[+] Imported {summary['total_builders']} vendors into the DB")
    print(f"    - {summary['with_website']} have a public website (crawlable)")
    print(f"    - {summary['with_portal']} have a login portal (handled by the portal scraper)")

    if args.import_only:
        print("[i] --import-only: skipping website crawl.")
        return

    targets = db.get_builders(only_with_website=True)
    if args.builder:
        targets = [b for b in targets if args.builder.lower() in b["builder_name"].lower()]
    if args.limit:
        targets = targets[:args.limit]

    if not targets:
        print("[i] No website-bearing builders selected to crawl.")
        return

    print(f"\n[+] Crawling {len(targets)} builder website(s) for brochures / fliers / booklets...")
    scraper = WebsiteAssetScraper(db)
    grand_total = 0
    for b in targets:
        r = scraper.scrape_builder(b["builder_name"], b["website"])
        status = r["error"] or f"{r['pages_visited']} pages, {r['assets_new']} new asset(s) of {r['assets_found']} found"
        print(f"    - {b['builder_name']:<32} {status}")
        grand_total += r["assets_new"]

    print("\n" + "=" * 70)
    print(f"[SUCCESS] Harvested {grand_total} new asset(s) into {config.ASSETS_DIR}")
    print("  Asset counts by builder:")
    for row in db.asset_counts_by_builder():
        print(f"    - {row['builder_name']:<32} {row['asset_count']} file(s)")
    print("=" * 70)


if __name__ == "__main__":
    main()
