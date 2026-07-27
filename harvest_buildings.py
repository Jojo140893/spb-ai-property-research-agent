"""
Harvest ALL building stock from the credentialed sources into the DB.

Unlike the client-research pipeline (which filters stock by a single client's
budget/suburb), this pulls the full current stock list from:
  - E-Agent (one login, ~14 builders)
  - the direct builder portals (Paramount, FRD, Torsion, Hermitage, Bathla, Proxima)

Email-only builders and Google-Drive weekly PDFs are intentionally skipped here
(no website stock list to scrape).

Credentials are read from the vendor CSV in drive_input/ (gitignored) via the
builder registry. Nothing is fabricated: a source that returns nothing records
nothing. Results go to the `buildings` table, deduped by builder+lot+suburb+price.

Usage:
    python harvest_buildings.py [--eagent-only] [--portals-only]

NOTE: this performs live authenticated logins to third-party portals using the
credentials in your CSV, so run it in your own environment. On the first run,
if a source reports "no listing cards matched", its listing selectors in
sources/portal_config.py need one confirmation pass against the live logged-in
page (save that page's HTML and they can be mapped exactly).
"""

import argparse

from builder_registry import BuilderRegistry
from sources.e_agent import EAgentSource
from sources.builder_portals import BuilderPortalSource
from database import ResearchDatabase

# Permissive filters => return the full stock list, not a client-filtered subset.
ALL_STOCK_FILTERS = {"budget_max": 100_000_000, "primary_suburbs": []}


def harvest(eagent=True, portals=True):
    registry = BuilderRegistry()
    db = ResearchDatabase()

    print("=" * 70)
    print("  HARVEST ALL BUILDING STOCK (E-Agent + direct portals)")
    print("=" * 70)

    total_new = 0

    if eagent:
        ea = EAgentSource(registry=registry)
        if not (ea.username and ea.password):
            print("[!] E-Agent credentials not found in the vendor CSV — skipping E-Agent.")
        else:
            print(f"[+] E-Agent: logging in as {ea.username} and pulling all stock...")
            # E-Agent is state-agnostic here; pull everything the account can see.
            listings = ea.search({**ALL_STOCK_FILTERS, "state": ""})
            new = sum(1 for L in listings if db.record_building({**L, "source_channel": "E-Agent"}))
            total_new += new
            print(f"    E-Agent: {len(listings)} listing(s) scraped, {new} new stored.")

    if portals:
        bp = BuilderPortalSource(registry)
        # union of states present in the directory so every portal builder is in scope
        states = sorted({s for b in registry.get_all_builders() for s in (b.get("states") or [])})
        seen_portals = set()
        for st in (states or [""]):
            listings = bp.search({**ALL_STOCK_FILTERS, "state": st})
            for L in listings:
                tag = (L.get("builder_name"), L.get("lot_address"), L.get("suburb"), L.get("advertised_package_price"))
                if tag in seen_portals:
                    continue
                seen_portals.add(tag)
                if db.record_building({**L, "source_channel": L.get("source_channel", "Direct Portal")}):
                    total_new += 1
        print(f"    Direct portals: {len(seen_portals)} unique listing(s) seen.")

    print("\n" + "=" * 70)
    print(f"[SUCCESS] {total_new} new building(s) stored. Totals by channel:")
    counts = db.building_counts_by_channel()
    if not counts:
        print("    (none yet — see the note about first-run selector confirmation)")
    for row in counts:
        print(f"    - {row['source_channel']}: {row['n']}")
    print("=" * 70)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--eagent-only", action="store_true")
    ap.add_argument("--portals-only", action="store_true")
    args = ap.parse_args()
    harvest(eagent=not args.portals_only, portals=not args.eagent_only)
