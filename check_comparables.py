"""
Will this comparables export actually benchmark our stock?

The market basis needs no API and no purchase: drop a CoreLogic / REA / agent export
into drive_input/ as comparables*.csv and benchmark_buildings.py switches from "other
stock we hold" to a real market comparison, with no code change. That is the cheapest
route to the thing Coleen asked for on 3 Aug -- "benchmarking", not a filter.

What was missing is any way to find out whether an export is USABLE before the nightly
run silently benchmarks nothing with it. A file with the right name and the wrong column
headers loads zero rows, and BenchmarkEngine reports "Unbenchmarked" for every listing,
which looks identical to having no file at all.

    python check_comparables.py                 # check drive_input/
    python check_comparables.py <file-or-dir>   # check a file before you move it
    python check_comparables.py --wanted        # the suburbs to ask the export for

Reports, per file: how many rows parse, why the rest did not, and -- the part that
matters -- how much of OUR live stock the export actually covers. An export of Sydney
comparables cannot benchmark a Melbourne catalogue, and that is worth knowing before it
becomes tonight's market basis rather than after.

Reads only. Never writes, never moves a file, never touches the database.
"""

import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config                                                        # noqa: E402

REQUIRED = ("suburb", "state", "bedrooms", "price")
OPTIONAL = ("rent_weekly", "land_sqm", "source", "date_checked")


def _rows(path: Path):
    """(parsed, rejected-with-reason) exactly as BenchmarkEngine._load_comparables sees it."""
    good, bad = [], Counter()
    with open(path, encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        headers = [h.strip().lower() for h in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED if c not in headers]
        if missing:
            return [], Counter({f"missing required column(s): {', '.join(missing)}": 1}), headers
        for row in reader:
            row = {(k or "").strip().lower(): v for k, v in row.items()}
            try:
                price = float(row.get("price") or 0)
            except (TypeError, ValueError):
                bad["price is not a number"] += 1
                continue
            if price <= 0:
                bad["no price"] += 1
                continue
            suburb = (row.get("suburb") or "").strip()
            if not suburb:
                bad["no suburb"] += 1
                continue
            try:
                beds = int(float(row.get("bedrooms") or 0))
            except (TypeError, ValueError):
                bad["bedrooms is not a number"] += 1
                continue
            if not beds:
                # Peers are matched on bedroom count, so a comparable without one can
                # never be selected. It loads, and it is dead weight.
                bad["no bedroom count (will never match a listing)"] += 1
                continue
            good.append({"suburb": suburb.title(),
                         "state": (row.get("state") or "").strip().upper(),
                         "bedrooms": beds, "price": price})
        return good, bad, headers


def _our_stock():
    """(suburb, state, bedrooms) of live priced stock, so coverage is measurable."""
    import sqlite3
    import suburb_quality
    conn = sqlite3.connect(str(config.DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    out = []
    for r in conn.execute(
            "SELECT suburb, state, bedrooms, lot_address, street_address, source_text "
            "FROM buildings WHERE price > 0 "
            "  AND (superseded_by IS NULL OR superseded_by = '')"):
        row = dict(r)
        name, why = suburb_quality.resolve(row)
        if suburb_quality.is_located(why) and row.get("bedrooms"):
            out.append((name.lower(), (row["state"] or "").upper(), int(row["bedrooms"])))
    return out


def _wanted() -> int:
    """The suburbs to ASK for, ordered by how much of our stock each unlocks.

    "Get me a CoreLogic export" is not an actionable request. This turns it into a list
    with a number beside each line, so whoever pulls the export knows where to stop.
    """
    stock = _our_stock()
    per = Counter((s.title(), st) for s, st, _b in stock)
    beds = {}
    for s, st, b in stock:
        beds.setdefault((s.title(), st), Counter())[b] += 1
    print("=" * 70)
    print("  Suburbs to request comparables for")
    print("=" * 70)
    print(f"  {len(stock):,} live listing(s) can be benchmarked once "
          f"covered, across {len(per)} suburb(s)." + chr(10))
    print(f"  {'suburb':<26} {'state':<6} {'listings':>9}   bedroom counts to cover")
    run = 0
    for (sub, st), n in per.most_common():
        run += n
        b = ", ".join(f"{k}br" for k, _ in sorted(beds[(sub, st)].items()))
        print(f"  {sub[:26]:<26} {st:<6} {n:>9}   {b}")
        if run >= 0.8 * len(stock):
            rank = per.most_common().index(((sub, st), n)) + 1
            print(chr(10) + f"  ^ the {rank} suburbs above cover "
                  f"{100.0 * run / len(stock):.0f}% of benchmarkable stock. "
                  f"The remaining {len(per) - rank} are a long tail.")
            break
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--wanted" in argv:
        return _wanted()
    where = Path(argv[0]) if argv else config.DRIVE_INPUT_DIR
    files = ([where] if where.is_file()
             else sorted(where.glob("comparables*.csv")) if where.is_dir() else [])

    print("=" * 70)
    print("  Comparables export — is it usable as a market basis?")
    print("=" * 70)
    if not files:
        print(f"  no comparables*.csv in {where}")
        print("\n  Until one exists every benchmark is INTERNAL: this lot against other")
        print("  stock we hold, which is not a market comparison and never claims to be.")
        print("  A CoreLogic / REA export with these columns is all it takes:")
        print(f"      {', '.join(REQUIRED)}"
              f"   (optional: {', '.join(OPTIONAL)})")
        print(f"  Save it as comparables_<name>.csv in {config.DRIVE_INPUT_DIR}")
        return 1

    total = []
    for path in files:
        good, bad, headers = _rows(path)
        total.extend(good)
        print(f"\n  {path.name}")
        print(f"    columns : {', '.join(headers) or '(none)'}")
        print(f"    usable  : {len(good):,}")
        for reason, n in bad.most_common():
            print(f"    dropped : {n:,}  {reason}")

    if not total:
        print("\n  Nothing usable. This file would load as ZERO comparables, and every")
        print("  listing would report 'Unbenchmarked' — indistinguishable from having no")
        print("  file at all, which is why this check exists.")
        return 1

    print(f"\n  {len(total):,} comparable(s) loaded from {len(files)} file(s)")
    print(f"  covering {len({(c['suburb'].lower(), c['state']) for c in total})} suburb(s)")

    # The question that actually decides whether this export is worth anything.
    try:
        stock = _our_stock()
    except Exception as exc:                                          # noqa: BLE001
        print(f"\n  (could not read our own stock to measure coverage: {exc})")
        return 0
    have = {(c["suburb"].lower(), c["state"], c["bedrooms"]) for c in total}
    loose = {(s, st) for s, st, _b in stock}
    covered_exact = sum(1 for k in stock if k in have)
    covered_suburb = sum(1 for s, st, _b in stock if (s, st) in {(c["suburb"].lower(), c["state"]) for c in total})
    print(f"\n  our live, located, bedroom-stated stock : {len(stock):,} listing(s) "
          f"across {len(loose)} suburb(s)")
    print(f"    same suburb + bedroom count            : {covered_exact:,} "
          f"({100.0 * covered_exact / max(1, len(stock)):.0f}%)")
    print(f"    same suburb, any bedroom count         : {covered_suburb:,} "
          f"({100.0 * covered_suburb / max(1, len(stock)):.0f}%)")
    if not covered_suburb:
        print("\n  WARNING none of our stock is in a suburb this export covers, so it")
        print("  would become the market basis and benchmark nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
