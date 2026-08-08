"""Fill the Benchmarks tab from a realestate.com.au export Coleen drops in a file.

WHY THIS EXISTS. The Benchmarks tab reads `benchmark_listings`, and the only writer was
sources/realestate.py, which needs a PropTrack subscription. That subscription is not
being bought, so on 8 Aug 2026 the tab's ceiling was zero rows forever. This is the other
door, and it needs no licence and no automated access to realestate.com.au: Coleen exports
from whatever REA access she already has — an agent account, a saved search, a report REA
emails her — and drops the file into `drive_input/`. Her browser, her account, her data.

The same folder and the same `comparables*.csv` pattern that benchmark.py has always
read, so one dropped file now feeds BOTH the SOP-step-7 scoring benchmark and the tab.
Expected columns (benchmark.py's set, all optional except suburb/state/price):

    suburb, state, bedrooms, price, land_sqm, source, date_checked
    address / address_label, listing_url, product_type       <- also read if present

REALESTATE.COM.AU ONLY, and rows from anywhere else are REFUSED rather than mixed in.
The standing instruction is that a benchmark stitched from several sources is not the
thing Coleen asked for, and is harder to defend when a buyer asks where a figure came
from. A CoreLogic or Domain row is not silently dropped either — it is counted and named
in the report, because a quiet drop is how a benchmark goes half-empty without anyone
noticing. If `source` is blank the row is TAKEN: an export Coleen made from REA that
simply lacks the column is the expected case, and the file's provenance is stated on the
command line by the person who dropped it.

IDENTITY, AND WHY RE-DROPPING THE SAME FILE IS SAFE. record_listing keys on
(provider, provider_listing_id). An export usually has no listing id, so one is derived
from the row's own content — suburb, state, beds, price, address, land — and prefixed
`csv:`. Re-importing the same export therefore UPDATES those rows rather than inserting a
second copy, and a genuinely new listing gets a new key. This is the duplicate-capture
mechanism the rest of this codebase has paid for twice; it is designed out here rather
than discovered later.

    python import_comparables_csv.py                  # dry run, says what it would store
    python import_comparables_csv.py --apply
"""
import argparse
import csv
import hashlib
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import benchmarks                                                   # noqa: E402
from config import DRIVE_INPUT_DIR                                  # noqa: E402

PROVIDER = "manual export"
# Said on every row, because attribution is a condition of using REA data at all — and
# because a row that came from a person's export must never be mistaken in SQL for one a
# licensed API returned.
SOURCE_NOTE = "realestate.com.au, exported manually by Smart Property Buying"

# What counts as realestate.com.au in the `source` column. Deliberately short: anything
# not recognisably REA is reported, not guessed at.
_REA = ("realestate.com.au", "realestate", "rea", "rea group", "proptrack")


def _looks_rea(value: str) -> bool:
    v = str(value or "").strip().lower()
    return (not v) or any(t in v for t in _REA)


def _row_identity(row: dict) -> str:
    """Stable across re-imports, so dropping the same export twice updates in place."""
    basis = "|".join(str(row.get(k) or "").strip().lower() for k in
                     ("suburb", "state", "bedrooms", "price", "address_label", "land_sqm"))
    return "csv:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def _read(path: Path):
    with open(path, encoding="utf-8-sig", errors="ignore") as f:
        for raw in csv.DictReader(f):
            row = {(k or "").strip().lower(): (v or "").strip()
                   for k, v in raw.items() if k}
            yield row


def collect(files):
    """-> (importable, refused_counter). Nothing is written here."""
    out, refused = [], Counter()
    for path in files:
        for row in _read(path):
            if not _looks_rea(row.get("source")):
                refused[f"not realestate.com.au: {row.get('source')!r}"] += 1
                continue
            suburb = row.get("suburb") or ""
            state = row.get("state") or ""
            price = row.get("price") or ""
            if not suburb or not state:
                refused["no suburb or no state — cannot be located or linked"] += 1
                continue
            try:
                if float(str(price).replace("$", "").replace(",", "")) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                refused["no usable price — it would drag any median it reached"] += 1
                continue

            listing = {
                "provider": PROVIDER,
                "source_channel": benchmarks.PORTAL,
                "source_note": SOURCE_NOTE,
                "suburb": suburb,
                "state": state,
                "price": str(price).replace("$", "").replace(",", ""),
                "bedrooms": row.get("bedrooms"),
                "land_sqm": row.get("land_sqm"),
                "lot_address": row.get("address_label") or row.get("address") or "",
                "product_type": row.get("product_type") or "",
                "listing_url": row.get("listing_url") or row.get("url") or "",
                "price_basis": "advertised",
                "price_kind": "asking",
                "source_file": path.name,
            }
            # Only when the export carries no URL of its own. record_listing derives a
            # suburb search link in that case, so the row still opens somewhere.
            if not listing["listing_url"]:
                listing["provider_listing_id"] = _row_identity(
                    {**listing, "address_label": listing["lot_address"]})
            out.append(listing)
    return out, refused


def main(argv=None):
    ap = argparse.ArgumentParser(description="Import realestate.com.au comparables from CSV.")
    ap.add_argument("--apply", action="store_true", help="write them; otherwise report only")
    ap.add_argument("--dir", default=None, help="folder to read (default: drive_input/)")
    args = ap.parse_args(argv)

    folder = Path(args.dir) if args.dir else DRIVE_INPUT_DIR
    files = sorted(folder.glob("comparables*.csv")) if folder.exists() else []

    print(f"  folder : {folder}")
    if not files:
        print("  no comparables*.csv found — nothing to import.")
        print("  Drop a realestate.com.au export there named e.g. comparables_aug.csv")
        return 0
    for f in files:
        print(f"  file   : {f.name}")

    listings, refused = collect(files)
    print()
    print(f"  importable rows : {len(listings)}")
    if refused:
        print("  refused:")
        for why, n in refused.most_common():
            print(f"      {n:>5}  {why}")

    if not listings:
        return 0
    if not args.apply:
        print("\n  Re-run with --apply to store them.")
        for l in listings[:3]:
            print(f"      e.g. {l['suburb']}, {l['state']}  ${l['price']}  {l.get('bedrooms') or '?'} bed")
        return 0

    before = benchmarks.count()
    outcome = Counter(benchmarks.record_listing(l) for l in listings)
    after = benchmarks.count()
    print()
    for k, n in outcome.most_common():
        print(f"      {n:>5}  {k}")
    print(f"  benchmark_listings: {before} -> {after}")
    print("  Rebuild and publish with: python build_web.py   (then deploy.ps1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
