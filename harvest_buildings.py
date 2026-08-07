"""
Harvest ALL building stock from the credentialed sources into the DB.

Unlike the client-research pipeline (which filters stock by a single client's
budget/suburb), this pulls the full current stock list from:
  - E-Agent (one login, ~14 builders)
  - the direct builder portals (Paramount, FRD, Torsion, Hermitage, Bathla, Proxima)

  - the digital@ inbox, where the nine builders with no portal send their stock

Google-Drive weekly PDFs are still out of scope here (no website stock list to scrape).

Credentials are read from the vendor CSV in drive_input/ (gitignored) via the
builder registry. Nothing is fabricated: a source that returns nothing records
nothing. Results go to the `buildings` table, deduped by builder+lot+suburb+price.

Usage:
    python harvest_buildings.py [--eagent-only] [--portals-only] [--email-only]
                                [--proxima-only] [--email-days N]

NOTE: this performs live authenticated logins to third-party portals using the
credentials in your CSV, so run it in your own environment. On the first run,
if a source reports "no listing cards matched", its listing selectors in
sources/portal_config.py need one confirmation pass against the live logged-in
page (save that page's HTML and they can be mapped exactly).
"""

import argparse
import sys
from typing import Dict
import logging

from builder_registry import BuilderRegistry
from sources.e_agent import EAgentSource
from sources.builder_portals import BuilderPortalSource
from sources.email_inbox import EmailStocklistSource
from sources.proxima import ProximaSource
from database import ResearchDatabase

# Permissive filters => return the full stock list, not a client-filtered subset.
ALL_STOCK_FILTERS = {"budget_max": 100_000_000, "primary_suburbs": []}


def harvest(eagent=True, portals=True, email=True, proxima=True, email_days=90):
    logging.basicConfig(level=logging.INFO, format='    %(levelname)s %(message)s')
    registry = BuilderRegistry()
    db = ResearchDatabase()

    # CAPTURED BEFORE ANYTHING IS STORED. record_building stamps last_seen with today,
    # so the moment the first row lands, "the newest last_seen" is this run and what the
    # channel returned last time is no longer recoverable. See _collapsed_channels.
    baseline = db.building_reads_by_channel()

    print("=" * 70)
    print("  HARVEST ALL BUILDING STOCK (E-Agent + direct portals)")
    print("=" * 70)

    total_new = 0
    # What each channel actually READ this run, against what it has stored. A channel
    # that has stock in the table and returns nothing tonight has failed, however
    # calmly it said so. See _report_channel_health.
    read: Dict[str, int] = {}
    skipped: Dict[str, str] = {}

    if eagent:
        ea = EAgentSource(registry=registry)
        if not (ea.username and ea.password):
            print("[!] E-Agent credentials not found in the vendor CSV — skipping E-Agent.")
            skipped["E-Agent"] = "no credentials in the vendor CSV"
        else:
            print(f"[+] E-Agent: logging in as {ea.username} and pulling all stock...")
            # E-Agent is state-agnostic here; pull everything the account can see.
            listings = ea.search({**ALL_STOCK_FILTERS, "state": ""})
            # record_building returns "new" | "updated" | "unchanged" — all truthy,
            # so count by value, never by truthiness.
            outcomes = [db.record_building({**L, "source_channel": "E-Agent"}) for L in listings]
            new = outcomes.count("new")
            updated = outcomes.count("updated")
            total_new += new
            read["E-Agent"] = len(listings)
            print(f"    E-Agent: {len(listings)} listing(s) scraped, {new} new, {updated} updated.")

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
                if db.record_building({**L, "source_channel": L.get("source_channel", "Direct Portal")}) == "new":
                    total_new += 1
        read["Direct Builder Portal (live)"] = len(seen_portals)
        print(f"    Direct portals: {len(seen_portals)} unique listing(s) seen.")

    if proxima:
        # Colin's #1 ask from 30 July. Not part of the BuilderPortalSource loop: the
        # stock is not on a page there — it is in data-* attributes inside 40 project
        # accordions — and it needs the persistent browser profile, because Proxima
        # re-challenges 2FA for every new browser context.
        px = ProximaSource(registry=registry)
        print("[+] Proxima: reading the agent portal's project stock...")
        listings = px.search(ALL_STOCK_FILTERS)
        read["Proxima"] = len(listings)
        if not listings:
            print("    Proxima: nothing read. If this says the sign-in expired, run: "
                  "python portal_login.py portal_proxima --profile")
        else:
            outcomes = [db.record_building(L) for L in listings]
            new, updated = outcomes.count("new"), outcomes.count("updated")
            total_new += new
            print(f"    Proxima: {len(listings)} lot(s) from {px.projects_with_stock}"
                  f"/{px.projects_seen} project(s), {new} new, {updated} updated.")
            if px.cross_listed:
                print(f"    ({len(px.cross_listed)} lot(s) cross-listed under a second "
                      f"project, stored once)")
        if px.filter_found:
            # Said out loud even on a good run: the harvest walked into a filtered page,
            # and the only reason the count is right is that it cleared it first.
            was = ", ".join(f"{k}={v}" for k, v in sorted(px.filter_found.items()))
            print(f"    (the projects page came up filtered on {was} — "
                  + ("cleared before counting)" if listings else
                     "see the log above for whether it cleared)"))

    if email:
        # The nine approved builders with no portal email their stock to
        # digital@smartpropertybuying.com.au, and Coleen asked on 30 July for the agent to
        # sweep it daily. The reader existed and worked but was never called from here, so
        # not one row had ever come from email.
        em = EmailStocklistSource(registry=registry, days_back=email_days)
        if not getattr(em, "username", ""):
            print("[!] No inbox credentials — skipping the email sweep. "
                  "Run: python setup_credentials.py email_inbox")
            skipped["digital email"] = "no inbox credentials"
        else:
            print(f"[+] digital email: sweeping the last {email_days} days (READ ONLY)...")
            listings = em.search({**ALL_STOCK_FILTERS, "state": ""})
            read["digital email"] = len(listings)
            outcomes = [db.record_building({**L, "source_channel": em.channel_name})
                        for L in listings]
            new, updated = outcomes.count("new"), outcomes.count("updated")
            total_new += new
            print(f"    digital email: {len(listings)} listing(s) read, {new} new, "
                  f"{updated} updated, {len(em.brochures)} brochure(s) seen.")
            if em.brochures:
                print(f"    -> file and link them with: python attach_email_brochures.py --apply")

    counts = db.building_counts_by_channel()
    # `n` for "has this channel ever produced anything" (_dead_channels asks a yes/no
    # question, so every capture counts). `n_live` for the collapse FLOOR, which is a
    # magnitude: superseded rows are never deleted, so `n` only grows while a healthy
    # read stays flat, and a floor derived from it eventually exceeds what a good run
    # returns. E-Agent stores 8,538 against 4,351 live already. Once it crossed, every
    # night would abort at step 1/7 -- and each aborted run still stores its rows first,
    # pushing the floor further out of reach.
    stored = {row["source_channel"]: row["n"] for row in counts}
    stored_live = {row["source_channel"]: (row.get("n_live") or 0) for row in counts}
    dead = _dead_channels(read, stored)
    collapsed = _collapsed_channels(read, stored_live, previous=baseline)

    print("\n" + "=" * 70)
    if dead:
        print("[FAILED] a channel that has stock returned nothing this run:")
        for channel, held in dead:
            print(f"    - {channel}: read 0 tonight, {held} listing(s) already stored")
        print("\n    Those rows are now going stale while the dashboard keeps serving")
        print("    them as current. The usual cause is an expired sign-in:")
        print("      python portal_login.py portal_proxima --profile")
    if collapsed:
        print("[FAILED] a channel read far less than it normally does:")
        for channel, n, floor, basis in collapsed:
            print(f"    - {channel}: read {n} this run, against {basis} "
                  f"(expected at least {floor})")
        print("\n    A partial read is not a small success: the rows that DID come back")
        print("    are stamped as today's stock and the rest go stale with nothing on")
        print("    screen to say so. For Proxima the usual cause is a filter left set on")
        print("    the projects page — the portal remembers it per session, and a filtered")
        print("    page reads as a smaller portfolio, not as an error. A healthy Proxima")
        print("    run logs: 40 project(s) listed, 9 with an availability view.")
    if not (dead or collapsed):
        print(f"[SUCCESS] {total_new} new building(s) stored.")
    for channel, why in sorted(skipped.items()):
        print(f"    - {channel}: SKIPPED, {why}")
    print("\n  Totals by channel:")
    if not counts:
        print("    (none yet — see the note about first-run selector confirmation)")
    for row in counts:
        n_read = read.get(row["source_channel"])
        got = "" if n_read is None else f"   (read {n_read} this run)"
        print(f"    - {row['source_channel']}: {row['n']}{got}")
    print("=" * 70)
    return 1 if (dead or collapsed) else 0


def _dead_channels(read: Dict[str, int], stored: Dict[str, int]):
    """Channels that were attempted, read nothing, and yet hold stock.

    Proxima's sign-in expired on 3 Aug. Every night after that the harvest read zero
    lots, printed a helpful line about running portal_login, and then printed [SUCCESS]
    and exited 0 — so run_daily carried on to export, build and deploy, and the
    dashboard served three-day-old Proxima prices as current with nothing on screen or
    in the log to say otherwise. Nobody found out until Colin opened the portal himself.

    Reading zero is only a failure when the channel HAS stock: a channel that has never
    returned anything is a configuration state, not a regression, and a channel skipped
    for missing credentials never ran at all.
    """
    return [(channel, stored.get(channel, 0))
            for channel, n in sorted(read.items())
            if n == 0 and stored.get(channel, 0) > 0]


# A channel that half-fails returns a number, and a number is what every check above was
# looking for. On 2026-08-07 a filter left set in Proxima's portal session made the
# projects page render 8 projects instead of 40: the harvest read 52 lots against 1,212
# stored, printed [SUCCESS], exited 0, and run_daily went on to export, benchmark and
# publish — the same shape as the 3 Aug incident _dead_channels was written for, one step
# short of invisible.
#
# Two floors, because each covers the other's blind spot:
#   * HALF of what the channel read last run. Tight, current, and it moves with the
#     stock — but a partial run WRITES its rows, so on its own it would ratchet down and
#     wave the same fault through tomorrow.
#   * A QUARTER of everything the channel has ever stored. Loose, but monotonic: no bad
#     run can lower it, so a second consecutive collapse is still caught.
# The higher of the two applies. Both are deliberately generous — this exists to catch a
# collapse, not ordinary movement, and a false alarm at 3am stops the whole daily run.
_COLLAPSE_FLOOR = 0.5      # of the previous run's read
_STORED_FLOOR = 0.25       # of the channel's stored rows
# Below this, a day's ordinary churn is indistinguishable from a fault, so no floor is
# applied at all: a channel that sends 8 listings and today sends 3 has not failed.
_MIN_BASELINE = 20


def _collapsed_channels(read: Dict[str, int], stored: Dict[str, int],
                        previous: Dict[str, int] = None):
    """Channels that read far less than usual — a partial read wearing success.

    Returns (channel, read_this_run, floor, basis) per collapsed channel.

    Reading NOTHING is left to _dead_channels, so a dead channel is reported once, with
    the message that names its actual cause.
    """
    previous = previous or {}
    out = []
    for channel, n in sorted(read.items()):
        if n <= 0:
            continue
        floors = []
        prev = int(previous.get(channel) or 0)
        if prev >= _MIN_BASELINE:
            floors.append((int(prev * _COLLAPSE_FLOOR), f"{prev} read last run"))
        held = int(stored.get(channel) or 0)
        if held >= _MIN_BASELINE:
            floors.append((int(held * _STORED_FLOOR), f"{held} stored"))
        if not floors:
            continue                      # nothing to compare against yet: a first run
        floor, basis = max(floors)
        if n < floor:
            out.append((channel, n, floor, basis))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--eagent-only", action="store_true")
    ap.add_argument("--portals-only", action="store_true")
    ap.add_argument("--email-only", action="store_true")
    ap.add_argument("--proxima-only", action="store_true")
    ap.add_argument("--email-days", type=int, default=90,
                    help="how far back to sweep the inbox (default 90)")
    args = ap.parse_args()
    only = (args.eagent_only or args.portals_only or args.email_only
            or args.proxima_only)
    # Non-zero when a channel that holds stock read nothing, OR read so much less than
    # usual that the run is a partial, so run_daily stops and the operator is told,
    # instead of publishing a stale snapshot as if it were fresh.
    sys.exit(harvest(eagent=args.eagent_only or not only,
                     portals=args.portals_only or not only,
                     email=args.email_only or not only,
                     proxima=args.proxima_only or not only,
                     email_days=args.email_days))
