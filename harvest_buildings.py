"""
Harvest ALL building stock from the credentialed sources into the DB.

Unlike the client-research pipeline (which filters stock by a single client's
budget/suburb), this pulls the full current stock list from:
  - E-Agent (one login, ~14 builders)
  - E-Agent's Stocklist search (eagent.mekooflow.com; skipped until its company login,
    from E-Agent's BDM, is stored -- see sources/eagent_stocklist.py)
  - the direct builder portals (Paramount, FRD, Torsion, Hermitage, Bathla, Proxima)

  - the digital@ inbox, where the nine builders with no portal send their stock
  - RealNet's "RealNet Stocklist" Google Drive folder, WA and Tasmanian packages, one PDF
    each (sources/realnet_drive.py; shared by link, so no login)

Other Google-Drive PDFs (Shape Homes' weekly list) are still out of scope here.

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
from datetime import datetime
from typing import Dict
import logging

# _record_unreadable_stocklists (27 Aug) called logger.warning and logger.info but this module
# never defined `logger` -- it imported `logging` and stopped. The path only runs when a
# stocklist produces no listings, so it stayed dormant for a month. On 24 Sep E-Agent read 0
# of 13,636 stored listings, the path ran, and its first line raised NameError; the except
# clause then raised the same NameError, so the harvest step CRASHED. A dead channel is meant
# to be a PARTIAL night (run_daily treats exit 2 as "the channels that did read still publish"):
# Proxima's 3,312, the inbox's 443 and the portals' 269 were all read fresh that run, and none
# of it reached the dashboard, because a missing name turned one dead channel into a total one.
logger = logging.getLogger(__name__)

import benchmarks
import harvest_manifest
from builder_registry import BuilderRegistry
from sources.e_agent import EAgentSource
from sources.eagent_stocklist import (EAgentStocklistSource, CHANNEL as STOCKLIST_CHANNEL,
                                      MISSING_TEXT as STOCKLIST_MISSING)
from sources.builder_portals import BuilderPortalSource
from sources.email_inbox import EmailStocklistSource
from sources.proxima import ProximaSource
from sources.realestate import RealestateSource
from sources.realnet_drive import RealNetDriveSource, CHANNEL as REALNET_CHANNEL
from database import ResearchDatabase

# Permissive filters => return the full stock list, not a client-filtered subset.
ALL_STOCK_FILTERS = {"budget_max": 100_000_000, "primary_suburbs": []}


def _require_readers():
    """Stop before harvesting if a reader this run depends on is not installed.

    On 12 Aug the first GitHub Actions run had no openpyxl — it is imported all over this
    codebase and was listed in requirements.txt nowhere, surviving only because Ahsan's
    laptop had it from a forgotten pip install. sources/spreadsheet_extract.py logs
    "openpyxl not installed — cannot read xlsx stocklists" and CARRIES ON, so the harvest
    skipped every .xlsx stocklist, stored almost nothing new, and reported step 1 as done.
    Most of E-Agent's stock is xlsx.

    That is the failure this project keeps meeting: a run that is degraded rather than
    broken, on a machine nobody is watching, publishing a thinner catalogue that looks
    healthy. A missing reader is now fatal at the start, where it costs one clear error
    instead of a quiet 4am shortfall nobody notices for a week.
    """
    missing = []
    try:
        import openpyxl                                              # noqa: F401
    except ImportError:
        missing.append("openpyxl  (.xlsx stocklists — most of E-Agent's stock)")
    try:
        import pdfplumber                                            # noqa: F401
    except ImportError:
        missing.append("pdfplumber  (.pdf price lists — 2,799 live rows come from these)")
    if missing:
        print("=" * 70)
        print("  CANNOT HARVEST — a reader is missing, and without it this run would")
        print("  quietly skip those files and report success:")
        for m in missing:
            print(f"    - {m}")
        print("\n  Fix: pip install -r requirements.txt")
        print("=" * 70)
        return False
    return True


def harvest(eagent=True, portals=True, email=True, proxima=True, rea=True,
            email_days=90, eagent_stocklist=None, realnet=False):
    # realnet defaults OFF here and ON from the command line (__main__, which the nightly
    # runs): several tests call harvest() directly and switch off only the channels that
    # existed when they were written, and a default-on folder read would put Google
    # Drive's answer into every one of them.
    logging.basicConfig(level=logging.INFO, format='    %(levelname)s %(message)s')
    # The Stocklist is E-Agent's own stock, so it runs whenever E-Agent does unless asked
    # for separately (--eagent-stocklist-only).
    if eagent_stocklist is None:
        eagent_stocklist = eagent
    if not _require_readers():
        return 1
    registry = BuilderRegistry()
    db = ResearchDatabase()

    # CAPTURED BEFORE ANYTHING IS STORED. record_building stamps last_seen with today,
    # so the moment the first row lands, "the newest last_seen" is this run and what the
    # channel returned last time is no longer recoverable. See _collapsed_channels.
    baseline = db.building_reads_by_channel()
    harvest_manifest.reset()
    # Lots a constraint refused, per channel. record_building now says "rejected" instead
    # of "unchanged" (7 Oct 2026): 41 lots a night were dropped by the dedup_key trap and
    # every summary line counted them as stored.
    rejected: Dict[str, int] = {}

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
            harvest_manifest.count_outcomes(listings, outcomes)
            new = outcomes.count("new")
            updated = outcomes.count("updated")
            total_new += new
            read["E-Agent"] = len(listings)
            rejected["E-Agent"] = outcomes.count("rejected")
            print(f"    E-Agent: {len(listings)} listing(s) scraped, {new} new, {updated} updated.")

    if eagent_stocklist:
        # E-AGENT'S POOLED HOUSE-AND-LAND SEARCH (7 Oct 2026). On 24 Sep E-Agent moved its
        # pooled H&L lists off the members' pages to "EAgent Stocklist" at
        # eagent.mekooflow.com, behind a company login issued by its BDM, and from that
        # night the log read "0 pooled listing(s)". Coleen compares the app against that
        # search, which is why "E-Agent always shows more". Its own channel, so its rows
        # never share an identity with the price-list rows the E-Agent pages give for the
        # same lots (supersede_duplicates joins the two). No login -> one line and a skip,
        # never a failed run: the login is the client's to obtain.
        sl = EAgentStocklistSource()
        if not sl.configured:
            print(f"[i] E-Agent Stocklist: no company login stored -- skipped. "
                  f"{STOCKLIST_MISSING}, then: python setup_credentials.py eagent_stocklist")
            skipped[STOCKLIST_CHANNEL] = f"no company login ({STOCKLIST_MISSING})"
        else:
            print("[+] E-Agent Stocklist: signing in and reading the H&L search...")
            listings = sl.search(ALL_STOCK_FILTERS)
            # Counted whenever a login is stored, so a refused sign-in on a channel that
            # holds stock is a dead channel like any other (_dead_channels).
            read[STOCKLIST_CHANNEL] = len(listings)
            # One manifest line for the search, and its refused lots counted like every
            # other channel's (7 Oct 2026). This block predates record_building's
            # "rejected", so a lot a constraint refused vanished with no line anywhere --
            # and the parity check's READ bar could not see the source at all.
            sl_state = str(getattr(sl, "state", "") or "")
            sl_entry = harvest_manifest.entry(
                STOCKLIST_CHANNEL, "search", "stocklist", name=STOCKLIST_CHANNEL,
                state=sl_state, printed=len(listings),
                short=bool(getattr(sl, "truncated", False)),
                outcome=("read" if listings else
                         "login failed" if sl_state == "refused" else
                         "fetch failed" if sl_state in ("unreachable", "error") else
                         "zero-yield"))
            harvest_manifest.tag(listings, sl_entry)
            outcomes = [db.record_building(L) for L in listings]
            harvest_manifest.count_outcomes(listings, outcomes)
            rejected[STOCKLIST_CHANNEL] = outcomes.count("rejected")
            new, updated = outcomes.count("new"), outcomes.count("updated")
            total_new += new
            print(f"    E-Agent Stocklist: {len(listings)} listing(s), {new} new, "
                  f"{updated} updated -- {getattr(sl, 'state', '?')}: "
                  f"{getattr(sl, 'why', '')}")
            if getattr(sl, "truncated", False):
                print("    E-Agent Stocklist: the read STOPPED AT ITS CAP -- the list is "
                      "longer than what was stored tonight.")

    if realnet:
        # RealNet's WA packages (Coleen, 24 Oct 2026: "can we add these WA properties from
        # E-Agent. They are all in the drive here"). E-Agent publishes no WA page, so the
        # folder is the only copy. Shared by link: no login, nothing to skip for lack of
        # one. Counted in `read` every night, so a folder whose sharing is tightened is a
        # dead channel like any other (_dead_channels).
        rn = RealNetDriveSource()
        if rn.off:
            skipped[REALNET_CHANNEL] = rn.why or "switched off (SPB_REALNET_FOLDER=off)"
        else:
            print("[+] RealNet Stocklist: reading the Google Drive folder...")
            listings = rn.search(ALL_STOCK_FILTERS)
            read[REALNET_CHANNEL] = len(listings)
            rn_entry = harvest_manifest.entry(
                REALNET_CHANNEL, "stock link", "folder", name="RealNet Stocklist (Google Drive)",
                state=rn.state, stocklist_key=harvest_manifest.stocklist_key(rn.folder_url))
            for name, outcome, printed, why in rn.files:
                harvest_manifest.add_piece(rn_entry, name, outcome, printed, why)
            if not rn.files:
                rn_entry["outcome"] = ("fetch failed" if rn.state in ("unreachable", "not_shared",
                                                                     "error") else "zero-yield")
            harvest_manifest.tag(listings, rn_entry)
            outcomes = [db.record_building(L) for L in listings]
            harvest_manifest.count_outcomes(listings, outcomes)
            rejected[REALNET_CHANNEL] = outcomes.count("rejected")
            new, updated = outcomes.count("new"), outcomes.count("updated")
            total_new += new
            print(f"    RealNet Stocklist: {len(listings)} package(s), {new} new, {updated} "
                  f"updated -- {rn.state}: {rn.why}")
            for name, outcome, _printed, why in rn.files:
                if outcome != "read" or why:
                    print(f"      {outcome}: {name} -- {why}")

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
                outcome = db.record_building(
                    {**L, "source_channel": L.get("source_channel", "Direct Portal")})
                harvest_manifest.count_outcomes([L], [outcome])
                if outcome == "new":
                    total_new += 1
                elif outcome == "rejected":
                    ch = L.get("source_channel", "Direct Portal")
                    rejected[ch] = rejected.get(ch, 0) + 1
        read["Direct Builder Portal (live)"] = len(seen_portals)
        print(f"    Direct portals: {len(seen_portals)} unique listing(s) seen.")

    if rea:
        # realestate.com.au, through PropTrack (REA Group's own API). The site itself
        # cannot be read: measured 2026-08-08, a plain request AND headless Chromium both
        # get HTTP 429 with a Kasada challenge on the first hit. Scraping it would mean
        # defeating bot detection, so this uses the licensed channel instead.
        #
        # Suburb-scoped because PropTrack bills per call: the suburbs we actually hold
        # stock in are the ones a comparison needs, and asking for the rest is someone
        # else's money.
        #
        # THE ROUTING LINE IS BELOW, AND IT IS THE WHOLE SEPARATION. These rows go to
        # benchmarks.record_listing, NOT db.record_building. Coleen's instruction is that
        # realestate.com.au listings are "a totally another category named BENCHMARKS" —
        # market comparables, not stock she can sell. Every consumer of `buildings`
        # selects it with no source_channel predicate (measured: api/_candidates.py:213,
        # build_web.py:339 and :410, benchmark_buildings.py:326, supersede_duplicates
        # lot_key, database.get_buildings feeding the CSV and the Excel workbook,
        # cohorts, catalogue_exposure, recover_pooled_builders, server.py, the promo
        # selection). Putting a resale with a blank builder into that table would put it
        # in the client shortlist as a purchasable package, into the "By source"
        # dropdown beside E-Agent, and into the internal peer median that builder stock
        # is scored against. A row that is not in the table cannot reach any of them.
        # See benchmarks.py; tests/test_benchmarks.py pins this at zero.
        rea_src = RealestateSource()
        rea_read = None                   # deliberately NOT in `read` — see below
        if not rea_src.configured:
            print("[i] realestate.com.au: no PropTrack key stored — skipped. "
                  "Run: python setup_credentials.py proptrack_api")
        else:
            suburbs = db.distinct_suburbs() if hasattr(db, "distinct_suburbs") else []
            print(f"[+] realestate.com.au: reading listings for {len(suburbs)} suburb(s)...")
            # PER STATE, not one stateless sweep. distinct_suburbs returns (suburb,
            # state) pairs and the state used to be thrown away, so every PropTrack query
            # went out with state="" — and 'Logan' is a locality in two states, so two
            # different places collapsed into one query whose answer belonged to neither.
            by_state: Dict[str, list] = {}
            for _sub, _st in suburbs:
                by_state.setdefault(str(_st or "").strip().upper(), []).append(_sub)
            listings = []
            for _st, _subs in sorted(by_state.items()):
                listings.extend(rea_src.search({"primary_suburbs": _subs, "state": _st}))
            rea_read = len(listings)
            if not listings:
                print("    realestate.com.au: nothing read.")
                for why, n in sorted(rea_src.skipped.items()):
                    print(f"      {n} x {why}")
            else:
                outcomes = [benchmarks.record_listing(L) for L in listings]
                new = outcomes.count("new")
                updated = outcomes.count("updated")
                refused = outcomes.count("refused")
                # NOT added to total_new: that number is "new building stock stored",
                # and a comparable is not stock. Conflating them is how the headline
                # count starts drifting away from what Coleen can actually sell.
                print(f"    realestate.com.au -> BENCHMARKS: {len(listings)} listing(s), "
                      f"{new} new, {updated} updated, {rea_src.calls} API call(s).")
                if refused:
                    print(f"      {refused} refused: no listing id and no URL, so there "
                          f"would be nothing to identify them by on the next run.")
            benchmarks.set_meta("last_read", datetime.now().isoformat(timespec="seconds"))
            benchmarks.set_meta("last_read_count", rea_read)
            benchmarks.set_meta("last_read_skipped",
                                "; ".join(f"{n} x {why}"
                                          for why, n in sorted(rea_src.skipped.items())))
        _report_benchmark_health(rea_src.configured, rea_read)

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
            # One manifest line per project, and each lot counted under its project.
            by_pid = {}
            for proj in getattr(px, "project_log", []) or []:
                by_pid[proj["pid"]] = harvest_manifest.entry(
                    "Proxima", "project", proj["pid"],
                    outcome="read" if proj.get("printed") else "zero-yield", **proj)
            for L in listings:
                e = by_pid.get(str(L.get("source_project_id") or ""))
                if e is not None:
                    harvest_manifest.tag([L], e)
            outcomes = [db.record_building(L) for L in listings]
            harvest_manifest.count_outcomes(listings, outcomes)
            rejected["Proxima"] = outcomes.count("rejected")
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
            rejected["digital email"] = outcomes.count("rejected")
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
    refused = {c: n for c, n in sorted(rejected.items()) if n}
    if refused:
        # Loud, and named by channel. Not a failed run on its own -- the rows that did
        # store are good -- but every one of these is a lot missing from tonight's stock.
        print("[!] listings REJECTED by a database constraint and NOT stored: "
              + ", ".join(f"{c} {n}" for c, n in refused.items())
              + " -- see the ERROR lines above for which.")
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
    # EVERY NIGHT, NOT ONLY A FAILED ONE. This sat below the `return 0`, so a good night
    # never wrote the unreadable list and the Settings tab kept showing whatever a failed
    # night had last left there (27 Aug's 57 files on 6 Oct).
    _record_unreadable_stocklists()
    _write_manifest()
    if not (dead or collapsed):
        return 0
    # 2 = PARTIAL: something failed, but other channels read normally this run.
    # 1 = nothing came back at all.
    #
    # The distinction exists because "abort the whole chain" was costing more than it
    # saved. Proxima's sign-in expires every few days; when it does, this returned 1,
    # run_daily stopped at step 1/7, and E-Agent's 4,261 freshly-read rows never reached
    # the dashboard either. Refusing to deploy does not make the stale Proxima rows
    # fresher — they are ALREADY on the live site from the previous deploy. It only
    # withholds the channels that worked. Strictly worse, in every direction.
    #
    # The guard that matters is unchanged: a run where nothing was read still aborts, and
    # a partial run is reported loudly rather than passed off as success.
    any_read = any(v for v in read.values())
    return 2 if any_read else 1


def _record_unreadable_stocklists() -> None:
    """Write down the stocklists this run read nothing out of.

    The harvest and the build are separate processes, so an in-memory list dies with the
    run. This is what carries it across, and build_web publishes it to the Settings tab.

    On 27 Aug E-Agent served 143 stocklists and 57 of them extracted zero listings --
    whole projects absent from the catalogue with nothing anywhere saying so. Coleen
    found one by hand. See sources.spreadsheet_extract.note_unreadable.
    """
    import json
    from pathlib import Path

    try:
        from sources.spreadsheet_extract import UNREADABLE
        out = Path(__file__).resolve().parent / "data" / "unreadable_stocklists.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {"generated": datetime.now().strftime("%d/%m/%Y %H:%M"),
             "files": UNREADABLE}, indent=1), encoding="utf-8")
        if UNREADABLE:
            logger.warning("%d stocklist(s) produced no listings at all; "
                           "their projects are missing from the catalogue.", len(UNREADABLE))
    except Exception as exc:                                          # noqa: BLE001
        logger.info("could not record unreadable stocklists: %s", exc)


def _write_manifest() -> None:
    """data/harvest_manifest.json: one line per source link, project and portal. Never
    fatal -- a manifest that cannot be written must not cost the night's stock."""
    try:
        path = harvest_manifest.write()
        snap = harvest_manifest.snapshot()["totals"]["by_outcome"]
        print(f"    manifest: {len(harvest_manifest.ENTRIES)} source(s) -> {path.name} "
              f"({', '.join(f'{k} {v}' for k, v in sorted(snap.items()))})")
    except Exception as exc:                                          # noqa: BLE001
        logger.warning("could not write the harvest manifest: %s", exc)


def _report_benchmark_health(configured: bool, read) -> None:
    """Say how the comparables are doing, and NEVER change the exit code.

    realestate.com.au is deliberately absent from the `read` dict above, and that is not
    an oversight. _dead_channels and _collapsed_channels compare `read` against
    db.building_counts_by_channel(), which reads `buildings` — a table benchmark rows are
    never in. So if the channel were left in `read` the guard would either be silent
    forever (stored is 0, and _dead_channels requires stored > 0) or, wired the other
    way, would abort the whole nightly run at step 1/7 the first time a PropTrack key
    lapsed. Freezing the dashboard — E-Agent, Proxima, the portals, the client
    shortlist — over a comparables outage is the wrong trade by a wide margin.

    The cost of taking it out of the guard is that nothing watches it, and an unwatched
    channel going quietly stale is exactly the 3 Aug Proxima incident. So it is watched
    HERE and reported out loud, and benchmarks.status() carries the same facts into
    benchmarks.json for the tab's stale banner. Visible, never blocking.
    """
    try:
        stored = benchmarks.count()
    except Exception as exc:                                          # noqa: BLE001
        print(f"    Benchmarks (realestate.com.au): unreadable ({exc})")
        return
    if not configured:
        print(f"    Benchmarks (realestate.com.au): {stored} stored, nothing read — "
              f"no PropTrack key. Expected, not a fault; the dashboard says so too.")
        return
    if read == 0 and stored > 0:
        print(f"    Benchmarks (realestate.com.au): READ 0 against {stored} stored. "
              f"The comparables on the Benchmarks tab are going stale. This does NOT "
              f"stop the run — building stock is unaffected — but it needs looking at.")
        return
    print(f"    Benchmarks (realestate.com.au): {read} read, {stored} stored.")


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
# Channels that DELIVER rather than re-list. See _collapsed_channels.
PUSH_CHANNELS = frozenset({"digital email"})

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
        if channel in PUSH_CHANNELS:
            # A SOURCE THAT PUSHES CANNOT COLLAPSE, because it never re-lists. This test
            # compares what a channel handed back against what it holds, which means
            # something only for a channel that re-lists everything each run. A builder
            # emails a price list when they have one; on a quiet day the inbox yields a
            # handful and that is a normal day, not a failure.
            #
            # IT WAS FATAL. "digital email: read 9 this run, against 170 stored (expected
            # at least 42)" failed the harvest step every night from 27 to 31 Aug. The
            # database was still pushed each time -- 16,908 rows on the 31st -- but the
            # workflow gates "Publish to Vercel" on success(), so the site went four days
            # without being republished while the data underneath it was fresh. The same
            # false positive was fixed on the Settings tab (build_web.CHANNEL_PUSH_SOURCES)
            # and this copy of the reasoning was missed.
            #
            # Silence is still caught: _dead_channels handles a channel that reads NOTHING,
            # and that test is untouched.
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
    ap.add_argument("--eagent-only", action="store_true",
                    help="E-Agent's pages and its Stocklist search")
    ap.add_argument("--eagent-stocklist-only", action="store_true",
                    help="only E-Agent's Stocklist search (eagent.mekooflow.com)")
    ap.add_argument("--portals-only", action="store_true")
    ap.add_argument("--email-only", action="store_true")
    ap.add_argument("--proxima-only", action="store_true")
    ap.add_argument("--rea-only", action="store_true",
                    help="only the realestate.com.au comparables (Benchmarks)")
    ap.add_argument("--realnet-only", action="store_true",
                    help="only RealNet's Google Drive folder (WA packages)")
    ap.add_argument("--email-days", type=int, default=90,
                    help="how far back to sweep the inbox (default 90)")
    args = ap.parse_args()
    only = (args.eagent_only or args.portals_only or args.email_only
            or args.proxima_only or args.rea_only or args.eagent_stocklist_only
            or args.realnet_only)
    # Non-zero when a channel that holds stock read nothing, OR read so much less than
    # usual that the run is a partial, so run_daily stops and the operator is told,
    # instead of publishing a stale snapshot as if it were fresh.
    #
    # `rea=` was declared on harvest() and never passed from here, so --proxima-only and
    # every other single-channel run still executed the PropTrack step. Harmless while
    # there is no key; the day one is stored it means a targeted Proxima re-run quietly
    # spends metered quota on somebody else's question.
    sys.exit(harvest(eagent=args.eagent_only or not only,
                     portals=args.portals_only or not only,
                     email=args.email_only or not only,
                     proxima=args.proxima_only or not only,
                     rea=args.rea_only or not only,
                     email_days=args.email_days,
                     eagent_stocklist=(args.eagent_stocklist_only or args.eagent_only
                                       or not only),
                     realnet=args.realnet_only or not only))
