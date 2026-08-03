"""
Benchmark every harvested listing against comparable stock.

Step 4 of run_daily.py, and the last unbuilt piece of Coleen's 22 July ask:
"whatever we have in our database, we want to benchmark it on what else they can
find within the market".

TWO BASES, AND THE DIFFERENCE IS NOT COSMETIC
---------------------------------------------
1. REAL MARKET COMPARABLES — CoreLogic / realestate.com.au exports dropped into
   drive_input/ as comparables*.csv. This is a true market benchmark and it earns
   the SOP Step 7 wording ("Below Market Value", ...). Colin has not supplied an
   export yet, so this path is dormant; the moment a file appears it takes over
   with no code change.

2. INTERNAL PEER MEDIAN — until then, each listing is compared against the OTHER
   listings we hold that are genuinely like it. That is a real, useful signal: it
   answers "is this lot cheap for its suburb and product type", which is exactly
   the question behind Colin's weekly best-deals promotion.

   What it is NOT is a market benchmark. Nothing here is evidence that a package
   beats realestate.com.au — only that it is priced below other stock on OUR books.
   So the internal path never uses the SOP market wording. It says "Below
   comparable stock", and benchmark_basis records precisely what the comparison
   was against, because a number in front of a buyer has to be traceable to the
   thing it actually came from.

PEER GROUPS, TIGHTEST FIRST
---------------------------
A row takes the first tier where it has at least MIN_PEERS others to compare with:

    suburb + product type + bedrooms     the real comparable
    suburb + product type                a 3-bed and a 4-bed house in one suburb
    suburb + bedrooms                    where product type was never recorded

The row itself is always excluded from its own median — including it drags the
median toward the row and makes everything look average. A group smaller than
MIN_PEERS is left blank with a reason rather than given a median of two.

Bare run = apply (run_daily.py calls it with no arguments). --dry-run to report only.
"""

import argparse
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from statistics import median

import config

# A median over four listings is noise dressed as a number. Five others is the
# smallest group where "typical" means anything; measured against the live data it
# still benchmarks ~49% of stock, against ~53% at three peers — a poor trade for
# how much shakier the low end gets.
MIN_PEERS = 5

# A peer group whose 90th percentile is more than this times its 10th is not a
# comparable set — the price is being driven by something the key does not match on.
# Measured on the live data: median group dispersion is x1.22 and p90 is x1.78, so
# the typical group is genuinely tight; the tail is not. The worst offenders were a
# Commercial group spanning x4.9 ($216k to $1.27M) and an Apartment group spanning
# x4.0 ($450k to $1.9M) — a penthouse and a studio in one building, where the price
# gap is floor area, not value. Calling the penthouse "well above comparable stock"
# would be telling Colin something false about a lot that is simply bigger.
# Refusing above x2.5 costs 57 of 3,145 rows (1.8%) and removes precisely those.
MAX_DISPERSION = 2.5

BENCH_FIELDS = ("benchmark_median", "benchmark_variance_pct",
                "benchmark_classification", "benchmark_basis")

# Internal wording, deliberately distinct from benchmark.py's SOP market bands so
# the two can never be confused on a client card.
INTERNAL_BANDS = (
    (-10.0, "Below comparable stock"),
    (-2.5, "Slightly below comparable stock"),
    (2.5, "In line with comparable stock"),
    (10.0, "Above comparable stock"),
)
INTERNAL_TOP = "Well above comparable stock"


def _norm(v):
    s = str(v or "").strip().lower()
    return s or None


class SuburbCheck:
    """Is this actually a suburb?

    59% of the suburb values in the table are not (3,063 of 5,214 on 2026-08-01).
    The column collects whatever landed in that position of a stocklist, so besides
    real localities it holds spreadsheet header fragments — 'Rooms Rooms m2 m2 m2',
    'Street # Type', '7 Star Energy Rating', 'IN TERNAL BALCONY TOTAL' — plus states
    ('New South Wales'), regions ('BRISBANE WEST', 'LAKE MACQUARIE') and development
    names ('[Haven]').

    That is fatal for a peer group specifically, because the group IS the suburb. A
    median over everything labelled 'BRISBANE WEST' spans a whole region, and a
    median over 'Rooms Rooms m2 m2 m2' is a median over a parsing accident. Both
    produce a confident-looking percentage with nothing behind it, and the worst of
    them sort straight to the top of a best-deals list.

    So a row is benchmarked only where its suburb is in the 17,537-suburb AU index
    the app already carries for distance search. Not a guess and not a repair —
    a row that fails is simply left unbenchmarked, with the reason counted.
    """

    def __init__(self):
        from geo import SuburbGeoIndex
        self.geo = SuburbGeoIndex()
        self._cache = {}

    @property
    def available(self):
        return bool(self.geo.loaded)

    def is_real(self, suburb, state):
        return bool(self.resolve(suburb, state))

    def resolve(self, suburb, state):
        """The locality to group on, or '' if the value is not one.

        Uses the SAME resolver the scoring pipeline uses. They used to differ: this
        checked the raw value while _candidates.py unglued composites, so a lot in
        "Stage 5A, Greenbank" was shortlisted with no benchmark and never grouped
        with the other Greenbank stock. Peer groups are only as good as the key, and
        two components disagreeing about the key is the worst version of that.
        """
        if not self.available:
            return str(suburb or "").strip()    # no index: behave as before
        key = (_norm(suburb), _norm(state))
        if key not in self._cache:
            try:
                self._cache[key] = self.geo.resolve_locality(
                    str(suburb or ""), str(state or ""))
            except Exception:
                self._cache[key] = str(suburb or "").strip()
        return self._cache[key]


TIERS = (
    ("suburb + product type + bedrooms",
     lambda r: (_norm(r["state"]), _norm(r["suburb"]), _norm(r["product_type"]), r["bedrooms"])),
    ("suburb + product type",
     lambda r: (_norm(r["state"]), _norm(r["suburb"]), _norm(r["product_type"]))),
    ("suburb + bedrooms",
     lambda r: (_norm(r["state"]), _norm(r["suburb"]), r["bedrooms"])),
)


def dispersion(prices):
    """p90 / p10 of a peer group. Percentiles rather than min/max so one oddly
    cheap or dear listing does not disqualify an otherwise tight group."""
    s = sorted(prices)
    if not s:
        return 0.0
    lo = s[max(0, int(len(s) * 0.10))]
    hi = s[min(len(s) - 1, int(len(s) * 0.90))]
    return hi / max(1.0, lo)


def classify_internal(variance_pct):
    for edge, label in INTERNAL_BANDS:
        if variance_pct < edge:
            return label
    return INTERNAL_TOP


def assign_peer_groups(rows):
    """(row_id -> (tier_label, [peer prices])) using the tightest tier that qualifies."""
    out = {}
    remaining = list(rows)
    for label, keyfn in TIERS:
        groups = defaultdict(list)
        for r in remaining:
            key = keyfn(r)
            if any(part is None for part in key):
                continue
            groups[key].append(r)
        placed = set()
        for _key, members in groups.items():
            if len(members) < MIN_PEERS + 1:      # +1 so each row still has MIN_PEERS others
                continue
            prices = [float(m["price"]) for m in members]
            for m in members:
                out[m["id"]] = (label, prices)
                placed.add(m["id"])
        if placed:
            remaining = [r for r in remaining if r["id"] not in placed]
    return out


def benchmark_internal(rows, suburb_check=None):
    """Compare each listing against its peer group. Returns id -> field dict."""
    check = suburb_check if suburb_check is not None else SuburbCheck()
    results, skipped = {}, defaultdict(int)

    # Filter BEFORE grouping, not after: a junk suburb must not even form a group,
    # or real listings that happen to share it get benchmarked against nonsense.
    usable = []
    for r in rows:
        locality = check.resolve(r["suburb"], r["state"])
        if locality:
            # Group on the RESOLVED locality, so every "Stage N, Greenbank" lot is a
            # peer of every other Greenbank lot instead of forming its own island.
            usable.append({**r, "suburb": locality})
        else:
            skipped["suburb is not a recognised locality"] += 1
    rows = usable
    groups = assign_peer_groups(rows)

    for r in rows:
        got = groups.get(r["id"])
        if not got:
            skipped["no comparable peer group"] += 1
            continue
        label, prices = got
        price = float(r["price"])
        # Dispersion is a property of the COHORT, so it is measured over the whole
        # group and disqualifies all of it. Measuring it over peers-excluding-self
        # judged the same cohort comparable or not depending on which member you
        # were pricing: the $5M penthouse got a clean read against tight studios
        # (its own removal made them tight) while every studio was refused for
        # sitting next to it. Either the set is comparable or it is not.
        if dispersion(prices) > MAX_DISPERSION:
            skipped["peer group too spread out to be comparable"] += 1
            continue
        # Exclude this row from its own median. Removing by value (not identity)
        # is correct here: two identical prices are interchangeable for a median.
        peers = list(prices)
        peers.remove(price)
        if len(peers) < MIN_PEERS:
            skipped["peer group too small once self excluded"] += 1
            continue
        med = median(peers)
        if med <= 0:
            skipped["peer median not usable"] += 1
            continue
        variance = ((price - med) / med) * 100.0
        results[r["id"]] = {
            "benchmark_median": round(med, 2),
            "benchmark_variance_pct": round(variance, 1),
            "benchmark_classification": classify_internal(variance),
            "benchmark_basis": f"internal peer median, {label} ({len(peers)} peers)",
        }
    return results, skipped


def benchmark_against_market(rows, engine):
    """The real thing, once Colin supplies a CoreLogic / REA export."""
    results, skipped = {}, defaultdict(int)
    for r in rows:
        suburb, state, beds = r["suburb"], r["state"], r["bedrooms"]
        if not (suburb and state and beds is not None):
            skipped["needs suburb + state + bedrooms for a market comparable"] += 1
            continue
        out = engine.benchmark_package(suburb, state, int(beds), float(r["price"]))
        if not out.get("benchmarked"):
            skipped["no market comparable within range"] += 1
            continue
        results[r["id"]] = {
            "benchmark_median": out["avg_market_price"],
            "benchmark_variance_pct": out["variance_pct"],
            "benchmark_classification": out["classification"],
            "benchmark_basis": out["data_note"],
        }
    return results, skipped


def main(argv=None):
    global MIN_PEERS
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; write nothing")
    ap.add_argument("--min-peers", type=int, default=MIN_PEERS)
    args = ap.parse_args(argv)
    MIN_PEERS = max(1, args.min_peers)

    comparables = sorted(config.DRIVE_INPUT_DIR.glob("comparables*.csv")) \
        if config.DRIVE_INPUT_DIR.exists() else []

    print("=" * 66)
    print("  Benchmark harvested stock")
    print("=" * 66)

    if not args.dry_run:
        dest = f"{config.DATABASE_PATH}.bak-benchmark-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy2(str(config.DATABASE_PATH), dest)
        print(f"[+] backup written: {dest}")
    else:
        print("[i] report only — nothing will be written. Drop --dry-run to commit.")

    conn = sqlite3.connect(str(config.DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    # Superseded rows are older captures of a lot that is also stored fresher. They
    # carry the price that was advertised at the time, so leaving them in the peer
    # pool drags medians toward stale numbers AND lets a stale row be benchmarked as
    # though it were on the market. 777 of them, 199 cheaper than the row that
    # replaced them. Excluded from both sides.
    rows = [dict(r) for r in conn.execute(
        "SELECT id, state, suburb, product_type, bedrooms, price FROM buildings "
        "WHERE price IS NOT NULL AND price > 0 AND superseded_by IS NULL")]
    total = conn.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
    stale = conn.execute(
        "SELECT COUNT(*) FROM buildings WHERE superseded_by IS NOT NULL").fetchone()[0]
    print(f"    {total} listing(s), {len(rows)} live with a usable price "
          f"({stale} superseded, excluded)")

    if comparables:
        from benchmark import BenchmarkEngine
        engine = BenchmarkEngine()
        print(f"[+] MARKET basis: {len(engine.comparables)} comparable(s) from "
              f"{', '.join(p.name for p in comparables)}")
        results, skipped = benchmark_against_market(rows, engine)
    else:
        print("[i] INTERNAL basis: no comparables*.csv in drive_input/, so each listing is")
        print("    compared against other stock we hold that is like it. This is NOT a")
        print("    market benchmark — it says a lot is cheap for its suburb and product")
        print("    type, not that it beats realestate.com.au. Drop a CoreLogic / REA")
        print("    export into drive_input/ and this switches basis with no code change.")
        results, skipped = benchmark_internal(rows)

    # Clear first, then write. A row that has LOST its peer group since the last run
    # must not keep last week's median sitting on it looking current.
    if not args.dry_run:
        conn.execute("UPDATE buildings SET " +
                     ", ".join(f"{f}=NULL" for f in BENCH_FIELDS))
        conn.executemany(
            "UPDATE buildings SET benchmark_median=?, benchmark_variance_pct=?, "
            "benchmark_classification=?, benchmark_basis=? WHERE id=?",
            [(v["benchmark_median"], v["benchmark_variance_pct"],
              v["benchmark_classification"], v["benchmark_basis"], rid)
             for rid, v in results.items()])
        conn.commit()

    pct = (100.0 * len(results) / len(rows)) if rows else 0.0
    print()
    print(f"    benchmarked : {len(results)} of {len(rows)} priced listing(s) ({pct:.1f}%)")
    for reason, n in sorted(skipped.items(), key=lambda kv: -kv[1]):
        print(f"    not scored  : {n:5d}  {reason}")

    if results:
        bands = defaultdict(int)
        for v in results.values():
            bands[v["benchmark_classification"]] += 1
        print()
        for label, n in sorted(bands.items(), key=lambda kv: -kv[1]):
            print(f"      {label:34s} {n}")
        tiers = defaultdict(int)
        for v in results.values():
            tiers[v["benchmark_basis"].split("(")[0].strip().rstrip(",")] += 1
        print()
        for label, n in sorted(tiers.items(), key=lambda kv: -kv[1]):
            print(f"      {label:52s} {n}")

    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
