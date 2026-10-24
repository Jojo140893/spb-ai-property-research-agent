"""
Build a deployable snapshot of the real app frontend (index.html).

`index.html` is the app's own UI and normally talks to `server.py`. For deployment it
reads the same shapes from static JSON instead — the page tries `/api/...` first and
falls back to these files, so ONE frontend serves both local and deployed use.

`server.py` itself is deliberately NOT deployed: it is a SimpleHTTPRequestHandler rooted
at PROJECT_ROOT, so publishing it would serve `.sessions/*.json` (live authenticated
portal cookies, reusable by anyone), `drive_input/vendors.csv` (plaintext supplier
passwords), the builder credential CSV and the SQLite database.

Every export goes through an explicit allow-list, so a field has to be named here to
reach the browser. That matters most for the builder registry: `/api/builders` serves
`portal_login_email` and `portal_login_password` to the local page, and those must never
leave the machine.

Usage:
    python build_web.py [--out vercel_site] [--no-assets]

`--no-assets` skips bundling the harvested PDFs (76 MB), which makes the deploy much
faster but leaves the brochure links pointing at the builders' own sites, where several
answer a direct request with HTML rather than the file.
"""

import collections
import hashlib
import json
import re
import shutil
import subprocess
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import benchmarks
import config
from address_label import clean_asset_title

# --- allow-lists -------------------------------------------------------------------
# buildings: listing facts only. No dedup keys, no content hashes, no internal ids.
BUILDING_FIELDS = [
    # postcode / lot_number / frontage_m were stored but never exported, so they were
    # queryable in the database and invisible in the app. Adding Proxima made that
    # worth fixing rather than noting: it carries a postcode on 100% of its 1,212 rows
    # and took the database from 336 to 1,548: postcode is the field that makes a
    # suburb match unambiguous ("LOGAN" is a locality in two states), and lot_number is
    # how Coleen refers to a listing when she talks to a builder.
    "builder_name", "lot_address", "street_address", "lot_number", "suburb",
    "state", "postcode",
    "availability_status",
    "state_source", "price", "land_price", "build_price", "bedrooms", "bathrooms", "car_spaces",
    "land_sqm", "house_sqm", "frontage_m", "storey", "title_status", "estate_name",
    "incentive_amount", "incentive_text", "product_type", "source_channel",
    "attribution_scope", "date_checked", "listing_url", "floorplan_url",
    "source_project_id",
    # A Proxima project's own "N available" counter on the night the row was read (7 Oct
    # 2026, repair round 2): the search asks whether a project's newest read was whole
    # before it calls a lot that read left out gone (api/_candidates.fresh_row_guard).
    "project_available",
    # stocklist_file and source_url are NOT exported. Both are capability URLs -- a
    # Dropbox rlkey grants read access to whoever holds the link, with no sign-in -- and
    # this deployment is public. stocklist_file is the worst of them: 2,753 rows carried
    # a FOLDER link (scl/fo/...), which hands over the whole shared folder rather than
    # one document, and index.html never rendered either column. The per-document links
    # below (listing_url / floorplan_url / brochure_url) and the derived source_link_*
    # ARE the "show me where this came from" feature Coleen asked for on 5 Aug, so they
    # stay; these two were pure exposure with nothing reading them.
    "brochure_url", "benchmark_median", "benchmark_variance_pct",
    # benchmark_basis states what each median was computed against — internal peer
    # stock or a real CoreLogic/REA comparable set. Without it on the row, a reader
    # cannot tell which, and the two mean very different things.
    "benchmark_classification", "benchmark_basis",
    # Best-deals selection (Colin, 30 Jul).
    #
    # `row_key` is content_hash, and it is the one deliberate exception to "no
    # dedup keys, no content hashes" above. The selection has to survive a
    # re-harvest, which means it needs the one field that identifies the SAME
    # listing before and after — that is precisely what content_hash is. Building a
    # key out of visible fields instead would break the moment a price moved, which
    # is the most common thing to change between harvests. It leaks nothing: it is a
    # digest of listing attributes already published in the row beside it.
    "content_hash", "promo_selected",
    # An older capture of a lot that is also stored fresher. Exported rather than
    # dropped so the dashboard can hide them by default AND say how many it hid —
    # silently serving 5,703 rows from a 6,480-row table would be its own small lie.
    "superseded_by",
]
# Derived at build time, NOT database columns — so they are exported and allow-listed
# but must never reach the SELECT. Keeping them in one list made the build fail with
# "no such column: source_link_url", which is the build guard doing its job.
BUILDING_DERIVED_FIELDS = ["source_link_url", "source_link_opens", "source_link_label",
                           "source_link_search",
                           # The date the lot's own page (listing_url) answered 404/410
                           # (link_check.py), else "". The search refuses such a row
                           # (_candidates, lot_page_gone) and the stock table says so.
                           "lot_page_gone",
                           # Why: "gone" (404/410), or the status word the builder's own
                           # page shows ("Under offer", "Sold").
                           "lot_page_status",
                           # Why the suburb reads the way it does -- "stated by the
                           # source", "read from the composite", or the reason it is
                           # blank. A blank suburb with no reason beside it looks like
                           # data we lost; with the reason it is data the source never
                           # gave. Same contract as state_source.
                           "suburb_source",
                           # WHICH DOCUMENT THIS ROW CAME FROM, as a hash. The deployed
                           # search groups rows by their source document so a lot with no
                           # locality can borrow one from the lots beside it on the same
                           # price list. That grouping was keyed on source_url, which is
                           # deliberately NOT exported (a Dropbox folder link is a
                           # capability and this deployment is public) -- so the page
                           # sibling map and the state borrow were both permanently empty
                           # in production while measuring perfectly against the local
                           # database. The hash groups identically and discloses nothing.
                           "source_doc",
                           # "Compare on realestate.com.au" -- a SEARCH URL for this
                           # row's suburb, built by rea_link.py from the RESOLVED suburb
                           # a few lines below. No key, no API, no request from us: the
                           # string sits in the page and Coleen's browser makes the
                           # request when she clicks. Same four-field shape as
                           # source_link_*, so index.html renders it with the same cell.
                           "rea_search_url", "rea_search_opens", "rea_search_label",
                           "rea_search_search"]
# WHAT THE ROW'S OWN TEXT SAYS ABOUT ITS PRICE, for a deployed search that never sees the
# text. source_text is read here and dropped (see build()), so "does this price list
# print this price, and is it the whole price?" can only be answered at build time. Three
# small cells from price_kind.price_evidence, computed while the text is still in hand:
#   price_kind      build_only / land_only / package / dwelling / commercial / unknown
#   text_price_max  the largest amount the text prints (a $725,000 duplex line that also
#                   prints its 1505615 total), or null when it prints none
#   price_in_text   whether the stored price itself is printed on the line
# On EVERY row, not only the unstated-availability rows that use them today, so the
# snapshot and the SQLite reader (which computes the same three with the same function)
# never disagree about a row. Measured on the 17 Sep copy: ~3% on stock.json.
# Null in all three means the computation failed for that row (logged, run DEGRADED).
PRICE_EVIDENCE_FIELDS = ["price_kind", "text_price_max", "price_in_text"]
BUILDING_DERIVED_FIELDS += PRICE_EVIDENCE_FIELDS
# AN EMAILED LIST'S IDENTITY AND FIRST READ, on 'digital email' rows only (null elsewhere),
# from api/_candidates.snapshot_email_cells. An emailed row's date_checked is the last
# night its message was still inside the 7-day inbox sweep -- Shape Homes' list dated
# 13 Aug reads "checked 20/08" -- so the search ages it for the 45-day confirm limit from
# first_seen, and recognises a newer list from the same sender by a hash of the subject
# with its dates removed. The subject itself is not published.
EMAIL_LIST_FIELDS = ["email_list", "email_first_seen"]
BUILDING_DERIVED_FIELDS += EMAIL_LIST_FIELDS
# A LOT'S LINE WITH ITS PRICES AND STATUS WORDS TAKEN OUT, as a 12-character hash
# (api/_candidates.snapshot_line_cells). The search hides a stale capture offered to
# confirm when a later capture of the same lot asks another price ON THE SAME LINE --
# Hattan lot 406 at its 17/08 $588,950 against the 16/09 read's $604,776 -- and keeps it
# when the later line differs (another design on the lot); and it does not recommend a
# row whose own document's same line, read on a later day, says the lot has gone (Howard
# lot 21: Available 15/09, On Hold 16/09). The line is not published.
# line_says_gone (7 Oct 2026): whether that WHOLE line says the lot has gone -- True, False,
# or null with no text. The search reads it instead of refusing every blank row whose
# 110-character lot_address might hide a status word past the cut (promotion_refusal's
# line_truncated: 58 Silkwood / Gallery lots on 6 Oct, none of them gone). One word, not
# the line, is published.
# source_folder (7 Oct 2026): a hash of the Dropbox shared folder the row's list sits in,
# "" otherwise, so the search can tell a vendor's old upload from the file that replaced it
# in the same folder (api/_candidates.fresh_row_guard). The folder id is never published.
LINE_KEY_FIELDS = ["line_key", "line_says_gone", "source_folder"]
BUILDING_DERIVED_FIELDS += LINE_KEY_FIELDS

# benchmarks.json -- market comparables from realestate.com.au, which are NOT stock.
# Its own payload, not a section of stock.json, for three reasons: stock.json is already
# 6.98 MB and this is fetched only when the tab is opened; deploy.ps1:39 counts
# len(stock.json['rows']) against .last_deploy_rows (11,257) and comparables must never
# inflate that floor, or the day a PropTrack key lapses the honest smaller snapshot is
# refused and publishing stops; and index.html's headline count is
# STOCK.filter(b => !b.superseded_by), which must keep meaning "lots Coleen can sell".
BENCHMARK_FIELDS = list(benchmarks.EXPORT_FIELDS)
# One row per (suburb, state) we hold LOCATED live stock in. This is the half that works
# with no key at all: our lots, our median, our bedroom mix and a working realestate.com.au
# search per suburb, so the tab is a usable comparison tool tonight rather than an empty
# grid promising one later. The market columns fill when a key arrives.
# Suburb rows in benchmarks.json. `our_lots`, `our_median` and `our_beds` were HERE and
# have been removed: they are Coleen's own builder stock, and this payload feeds a tab
# defined as realestate.com.au listings and nothing else (Ahsan, 9 Aug). Carrying her
# stock in it — even unrendered — is how the 223 rows came to be read as market
# properties. What remains is the suburb, whatever REA data exists for it, and the
# search link, which is used by the "Check the benchmark" button.
BENCHMARK_SUBURB_FIELDS = ["suburb", "state",
                           "benchmarks_stored", "market_median", "search_url"]

# builders: NOTE the deliberate omission of portal_login_email / portal_login_password.
BUILDER_FIELDS = [
    "builder_name", "states", "portal_url", "stock_channel", "is_on_e_agent",
    "e_agent_available", "contract_available", "notes",
]
# assets: the document and where it came from. `extracted_text` is megabytes of brochure
# prose and `local_path` leaks the machine's directory layout, so both are left out.
ASSET_FIELDS = ["builder_name", "asset_type", "title", "source_url", "file_size",
                "downloaded_at"]

# This guard was catching two different things under one name. Splitting them, because
# the difference matters: one class must never ship at any price, the other is a
# tidiness rule that a feature can have a reviewed exception to.
#
# Secrets. No exception, ever, for any reason.
FORBIDDEN_SECRETS = ("password", "passwd", "pwd", "secret", "token", "login_email",
                     "session")
# Internal plumbing. Not sensitive — it just has no business on a public page unless
# something genuinely needs it, so it stays out by default and an exception is named.
FORBIDDEN_INTERNAL = ("content_hash", "dedup", "sha256", "local_path")

# Reviewed exceptions to FORBIDDEN_INTERNAL, and why.
#
#   content_hash — the best-deals selection (Colin, 30 Jul) has to survive a
#   re-harvest, so the browser needs the one identifier that names the SAME listing
#   before and after one. A key built from visible fields would break the first time
#   a price moved, which is the most common thing to change between harvests. The
#   value is a digest of listing attributes that are published in the row beside it,
#   so it discloses nothing new and grants no access.
ALLOWED_INTERNAL = {"content_hash"}

FORBIDDEN = FORBIDDEN_SECRETS + FORBIDDEN_INTERNAL   # kept for anything reading it


def _check(name: str, fields) -> None:
    """No credential ever ships; internal fields ship only by named exception."""
    for f in fields:
        low = f.lower()
        if any(bad in low for bad in FORBIDDEN_SECRETS):
            raise SystemExit(f"[ABORT] {name}: refusing to export credential field {f!r}")
        if f in ALLOWED_INTERNAL:
            continue
        if any(bad in low for bad in FORBIDDEN_INTERNAL):
            raise SystemExit(
                f"[ABORT] {name}: refusing to export internal field {f!r}. "
                f"If it is genuinely needed, add it to ALLOWED_INTERNAL with a reason.")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "builder").lower()).strip("_") or "builder"


# contact_name / contact_email / contact_phone are deliberately absent from
# BUILDER_FIELDS: a builder rep's direct line has no business on a public URL. But the
# free-text `notes` column carries the same details in prose — "Email comes from
# Neha@dreamscopehomes.com.au" — and shipped them anyway, which the allow-list cannot
# catch because it filters field names, not values.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
# Conventional Australian formats only, with separators where people actually put them.
# A looser pattern flagged "04-15-03-55-18" and "03 4 3 3 230 24" in the stock export —
# lot codes, not numbers — and a guard that cries wolf gets switched off.
# The digit after the leading 0 must be a real AU area or mobile prefix (2,3,4,7,8).
# Without that, ten-digit internal codes in the stock export — 0011091284, 0092469904 —
# read as phone numbers and blocked a publish that was perfectly safe.
_PHONE = re.compile(
    r"(?<![\d-])(?:\+?61[ -]?)?(?:\(0[2-478]\)[ -]?|0[2-478][ -]?)\d{4}[ -]?\d{4}(?![\d-])"
    r"|(?<![\d-])04\d{2}[ -]?\d{3}[ -]?\d{3}(?![\d-])")


def _display_name_canonicaliser(builder_names):
    """One builder, one name — for the exported copy only.

    builder_names.py already does this at WRITE time and deliberately never as an UPDATE
    over stored rows: builder_name feeds content_hash, so rewriting it changes the
    identity of every row it touches and the next harvest re-inserts them all. That is
    the mechanism that produced 777 duplicate captures.

    The consequence is that rows harvested before a spelling was known keep it, and the
    dashboard still listed one company several times: 89 rows displayed the bare domain
    "hattan.com.au" as their builder, 103 said "AVIA Homes" against the registry's "Avia
    Homes", and "G Developments" appeared three ways. Canonicalising the EXPORT fixes the
    builder filter, the directory and the count with no identity risk at all, and a
    re-harvest converges on the same answer by itself.

    Seeding order is what makes it work: the registry spelling is authoritative, then the
    most common spelling in stock. A name that already resolves to a known one is NOT
    registered as a target of its own — without that, both "Strike Development" and
    "Strike Developments" got learned and neither resolved to the other.
    """
    from builder_names import BuilderNameCanonicaliser
    canon = BuilderNameCanonicaliser()
    try:
        from builder_registry import BuilderRegistry
        for b in BuilderRegistry().get_all_builders():
            canon.learn(b.get("builder_name", ""))
    except Exception as exc:                                      # pragma: no cover
        print(f"[!] registry unavailable for name canonicalisation ({exc})")
    for name, _ in collections.Counter(
            n for n in builder_names if str(n or "").strip()).most_common():
        if canon.canonical(name) == name:
            canon.learn(name)
    return canon


def _redact_contacts(text) -> str:
    """Strip direct contact details out of free text bound for the public build."""
    out = _EMAIL.sub("[email withheld]", str(text or ""))
    out = _PHONE.sub("[phone withheld]", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def _price_evidence_cells(row, failures: list) -> dict:
    """The PRICE_EVIDENCE_FIELDS cells for one row. Never raises.

    build_web.py is a REQUIRED nightly step: an exception here aborts the chain and the
    dashboard keeps yesterday's snapshot, withholding a whole day of fresh stock over one
    odd line of vendor text. So a row whose evidence cannot be computed publishes nulls
    -- which the search reads as "no evidence", the same as a row the rule does not
    admit -- and the failure is recorded for the caller to report.
    """
    try:
        import price_kind
        evidence = price_kind.price_evidence(row)
        return {f: evidence[f] for f in PRICE_EVIDENCE_FIELDS}
    except Exception as exc:                                            # noqa: BLE001
        failures.append((row.get("content_hash") or "", f"{type(exc).__name__}: {exc}"))
        return dict.fromkeys(PRICE_EVIDENCE_FIELDS)


def _email_list_cells(row, failures: list) -> dict:
    """The EMAIL_LIST_FIELDS cells for one row. Never raises, for the same reason as
    _price_evidence_cells: nulls, which the search reads as "no emailed-list evidence"
    (no newer-list guard, and the age limit falls back to date_checked)."""
    try:
        from api._candidates import snapshot_email_cells
        cells = snapshot_email_cells(row)
        return {f: cells[f] for f in EMAIL_LIST_FIELDS}
    except Exception as exc:                                            # noqa: BLE001
        failures.append((row.get("content_hash") or "", f"{type(exc).__name__}: {exc}"))
        return dict.fromkeys(EMAIL_LIST_FIELDS)


def _line_key_cells(row, failures: list) -> dict:
    """The LINE_KEY_FIELDS cells for one row. Never raises, for the same reason as
    _price_evidence_cells: a null, which the search reads as "no line to compare" (the
    repriced guard then hides nothing)."""
    try:
        from api._candidates import snapshot_line_cells
        cells = snapshot_line_cells(row)
        return {f: cells[f] for f in LINE_KEY_FIELDS}
    except Exception as exc:                                            # noqa: BLE001
        failures.append((row.get("content_hash") or "", f"{type(exc).__name__}: {exc}"))
        return dict.fromkeys(LINE_KEY_FIELDS)


# Read by .github/workflows/daily-harvest.yml ("Fail the run if a data step died quietly")
# after publishing. run_daily.py clears it before the run.
_DEGRADED_MARKER = Path(__file__).with_name("_run_degraded")


def _report_price_evidence_failures(failures, marker: Path = None) -> None:
    """Say so, loudly, and mark the run DEGRADED -- but publish.

    Exit status stays 0: deploy.ps1 refuses to publish on any non-zero exit from this
    script, and the rows that did compute are worth publishing.
    """
    if not failures:
        return
    marker = marker or _DEGRADED_MARKER
    first = "; ".join(f"{h or '(no hash)'} {err}" for h, err in failures[:3])
    line = (f"7/7  Build the deployed snapshot: price evidence failed on "
            f"{len(failures)} row(s), published without it ({first})")
    print(f"    ERROR {line}")
    try:
        with marker.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception as exc:                                            # noqa: BLE001
        print(f"  (could not write the degraded marker: {exc})")


def _FOLDER_CAPABILITY(url: str) -> bool:
    """A share link that opens a FOLDER rather than one document."""
    return any(m in url for m in ("dropbox.com/scl/fo/", "/:f:/", "sharepoint.com/:f:"))


# A row the filter shows may not display a word that says it cannot be bought. Whole
# words only -- "The Reserve" is an estate and "Settlers Run" is a street.
_SOLD_WORD_ON_SHOW = re.compile(
    r"\b(sold|on\s*hold|under\s*offer|reserved|withdrawn|deposit\s*taken|settled)\b",
    re.I)
# The columns index.html renders as text in the stock table. A sold word anywhere in
# these is a sold word the client can see.
_VISIBLE_TEXT_COLUMNS = ("estate_name", "lot_address", "title_status", "incentive_text")
# Above this the build stops. Below it, the build says so and carries on: one OCR-garbled
# line should not cost Coleen a whole day of fresh data, but a systemic break should.
_SOLD_ON_SHOW_LIMIT = 20


def _assert_no_sold_stock_on_show(path: Path) -> int:
    """Rows the filter SHOWS that nonetheless read as sold, on screen, to a person.

    Ahsan, 7 Sep: "I want you to see that there are not any buildings that sold or not
    available. Show up in the the filter because I see". Twice I answered by measuring
    availability_status, found it clean, and told him it was fine. He was looking at the
    word SOLD in the ADDRESS column, which no status check would ever have found.

    So this checks what he checks: the text the table actually renders. Every other guard
    in this file asks whether the DATA is right; this one asks what the screen says.

    NOT A HARD ZERO. repair_rows.find_sold_word_without_status runs earlier in the same
    nightly and turns these into a real status, so the expected count here is nought. One
    survivor is an OCR garble and is not worth withholding a day of fresh stock over; a
    couple of dozen means that repair stopped running, which is worth stopping for.
    """
    with path.open(encoding="utf-8") as fh:
        snap = json.load(fh)
    keys, rows = snap.get("keys") or [], snap.get("rows") or []
    at = {k: i for i, k in enumerate(keys)}
    if "availability_status" not in at:
        return 0
    try:
        from api._candidates import NOT_AVAILABLE
    except ImportError:                                # pragma: no cover - api not on path
        return 0
    columns = [at[c] for c in _VISIBLE_TEXT_COLUMNS if c in at]
    sup = at.get("superseded_by")
    offenders = []
    for row in rows:
        if sup is not None and row[sup]:
            continue                                   # hidden from the table already
        if str(row[at["availability_status"]] or "").strip().lower() in NOT_AVAILABLE:
            continue                                   # correctly filtered out
        for i in columns:
            if _SOLD_WORD_ON_SHOW.search(str(row[i] or "")):
                offenders.append(row)
                break
    if len(offenders) > _SOLD_ON_SHOW_LIMIT:
        raise SystemExit(
            "[abort] %d listing(s) the filter SHOWS display a sold/on-hold word where a "
            "person reads. The limit is %d. repair_rows.find_sold_word_without_status "
            "turns these into a real status and runs earlier in run_daily.py -- check "
            "whether that step failed tonight."
            % (len(offenders), _SOLD_ON_SHOW_LIMIT))
    return len(offenders)


def _assert_no_folder_capability_urls(path: Path) -> None:
    """No FOLDER share link may reach the public snapshot.

    A per-document link exposes one PDF and is the feature Coleen asked for. A folder
    link -- Dropbox 'scl/fo/...', SharePoint/OneDrive folder shares -- hands over
    everything in the folder to anyone holding the URL, with no sign-in. 2,753 rows
    carried one in stocklist_file, a column index.html never even rendered.

    The allow-list alone could not catch this: it filters field NAMES, and the field was
    legitimately named. This checks the values.
    """
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    folder = sum(text.count(m) for m in
                 ("dropbox.com/scl/fo/", "/:f:/", "sharepoint.com/:f:"))
    if folder:
        raise SystemExit(
            f"[ABORT] {path.name}: {folder} folder-level share link(s) would be "
            f"published. A folder capability grants the whole folder to anyone with the "
            f"URL. Export the per-document link instead, or nothing.")


_STATIC_TEXT = (".json", ".html", ".js", ".css", ".svg", ".xml", ".txt", ".csv")


def _static_files(site: Path):
    """Every text file the deploy serves as-is: all of `site` except api/, the function
    bundle, which Vercel runs and never serves. api/_data/price_list_links.json holds the
    folder links on purpose and is reachable only through api/price_list.py."""
    for path in sorted(site.rglob("*")):
        rel = path.relative_to(site)
        if path.is_file() and rel.parts[0] != "api" and path.suffix.lower() in _STATIC_TEXT:
            yield path


# The floor a suburb-blind harvest would fall through. See _assert_suburb_coverage.
SUBURB_COVERAGE_FLOOR = 0.95


def _assert_suburb_coverage(coverage):
    """Refuse to publish a catalogue that has lost its suburbs.

    THE FAILURE THIS CATCHES IS INVISIBLE EVERYWHERE ELSE, and it is the one this module
    spent a day earning. Every other health check in the pipeline counts ROWS:
    harvest_buildings scores a channel on how many listings it read, deploy.ps1 guards a
    row-count floor and a 20% drop. A night where every row arrives with a blank suburb
    scores identically to a perfect one -- and worse than identically, because suburb_norm
    is one of the eight inputs to content_hash, so blank-suburb rows take a NEW identity
    and are inserted alongside the old ones. The row count RISES. Every existing guard
    passes. The dashboard quietly loses its suburb filter and nothing anywhere says so.

    suburb_quality.summarise() was written for exactly this -- "Counts by reason, for the
    daily run and the build to report" -- and had no caller anywhere in the tree.

    A FLOOR, NOT A COMPARISON WITH YESTERDAY, because the runner is fresh every night: CI
    tees _daily.log into a new workspace and uploads nothing, so there is no previous run
    to compare against. An absolute floor needs no history, and it is the same shape as
    the two guards this codebase already trusts -- document_suburb._COLLAPSE_FLOOR and
    deploy.ps1's row-count floor.
    """
    if not coverage or not coverage.get("australian"):
        return
    share = coverage["share"]
    if share < SUBURB_COVERAGE_FLOOR:
        raise SystemExit(
            "[abort] only %d of %d Australian listings (%.1f%%) can be found by suburb; "
            "the floor is %.0f%%. Something upstream stopped reading suburbs. Check "
            "whether document_suburb.py or repair_rows.py failed tonight -- both are "
            "optional steps in run_daily.py and neither of them fails the run."
            % (coverage["findable"], coverage["australian"], share * 100,
               SUBURB_COVERAGE_FLOOR * 100))


def _assert_a_benchmark_names_its_place(path: Path) -> None:
    """A published benchmark must say which suburb it is a benchmark FOR.

    benchmark_buildings.py resolves the suburb with source_text in hand; the export
    does not publish that column. When the export could not also READ it, 19 rows went
    out carrying a median computed for Wadalba with the suburb cell empty — a number in
    front of a buyer with nothing naming what it was compared against, which is exactly
    what benchmark_basis exists to prevent.

    NOT about the Benchmarks tab, despite how the name reads. This checks a
    benchmark_median on a BUILDING row in stock.json. The market comparables Coleen calls
    "Benchmarks" live in benchmarks.json and are policed by
    _assert_every_benchmark_listing_opens_somewhere.

    Cheap to check and easy to break again, since the two resolve in different files.
    """
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    keys, rows = data.get("keys") or [], data.get("rows") or []
    if not ({"suburb", "benchmark_median"} <= set(keys)):
        return
    si, bi = keys.index("suburb"), keys.index("benchmark_median")
    orphans = sum(1 for r in rows
                  if r[bi] not in (None, "") and not str(r[si] or "").strip())
    if orphans:
        raise SystemExit(
            f"[ABORT] {path.name}: {orphans} row(s) publish a benchmark with no suburb. "
            f"The export and benchmark_buildings.py must resolve the suburb from the "
            f"same fields — check that source_text is READ (not exported) in build().")


def _assert_no_contact_details(path: Path) -> None:
    """Refuse to publish a payload containing a direct email address or phone number.

    The field allow-list checks NAMES; this checks VALUES. `notes` is free text and was
    carrying what the allow-list had deliberately excluded — "Email comes from
    Neha@dreamscopehomes.com.au" — so a rep's address reached a public URL through a
    field nobody thought of as a contact field.
    """
    found = set()
    for field, value in _walk_strings(json.loads(path.read_text(encoding="utf-8"))):
        # Only free text can carry a contact detail. Digests and URLs cannot, and
        # scanning them matched digit runs inside content_hash hex and inside the
        # base64 token of a Hudson Homes listing URL — blocking a clean publish, which
        # is how a guard gets disabled.
        if field in _NOT_FREE_TEXT or field.endswith("_url") or value.startswith("http"):
            continue
        found.update(_EMAIL.findall(value))
        found.update(m.group(0) for m in _PHONE.finditer(value))
    if found:
        raise SystemExit(
            f"[ABORT] {path.name} contains direct contact details and was not published: "
            f"{sorted(found)[:8]}. Redact them at the export (see _redact_contacts) — "
            f"they cannot go on a public URL.")


# Digests, never text. line_key is a 12-hex-character hash on ~13,000 rows, and hex runs
# of ten digits turn up in that many: '0447270629' matched as a mobile number and aborted
# the first build that published it (release review). source_doc and email_list are
# digests of the same kind.
_NOT_FREE_TEXT = {"content_hash", "sha256", "dedup_key", "row_key", "superseded_by",
                  "line_key", "source_doc", "email_list", "source_folder"}


def _walk_strings(node, field=""):
    """(field_name, string) for every string in a payload, columnar shape included."""
    if isinstance(node, dict):
        if isinstance(node.get("keys"), list) and isinstance(node.get("rows"), list):
            cols = node["keys"]
            for row in node["rows"]:
                for name, cell in zip(cols, row):
                    if isinstance(cell, str):
                        yield name, cell
            rest = {k: v for k, v in node.items() if k not in ("keys", "rows")}
            yield from _walk_strings(rest, field)
            return
        for key, value in node.items():
            yield from _walk_strings(value, key)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_strings(item, field)
    elif isinstance(node, str):
        yield field, node


def _pick(row, fields):
    d = dict(row) if not isinstance(row, dict) else row
    return {f: d.get(f) for f in fields}


def _median(values):
    """Median, or None. None where there is nothing to compute, never 0 — a zero median
    would read on the Benchmarks tab as "this suburb is free"."""
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return None
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _assert_every_benchmark_listing_opens_somewhere(path: Path) -> None:
    """A published comparable must open SOMETHING on realestate.com.au.

    The client's ask was "the link that opens that building inside the realestate.com.au
    website". A benchmark row with neither the provider's own listing URL nor a suburb
    search is a price with no way to check it — the exact thing this category exists to
    avoid — and url_source is stored per row precisely so the distinction is auditable
    rather than inferred from a blank cell.

    NOT the same check as _assert_a_benchmark_names_its_place, which is stock.json-only
    and is about a benchmark_median on a BUILDING row.
    """
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    keys, rows = data.get("keys") or [], data.get("rows") or []
    if not ({"listing_url", "search_url"} <= set(keys)):
        return
    li, si = keys.index("listing_url"), keys.index("search_url")
    orphans = sum(1 for r in rows
                  if not str(r[li] or "").strip() and not str(r[si] or "").strip())
    if orphans:
        raise SystemExit(
            f"[ABORT] {path.name}: {orphans} comparable(s) publish a price with no way "
            f"to open it on realestate.com.au. A benchmark Coleen cannot click is a "
            f"number she has to take on trust — check benchmarks.record_listing's "
            f"url_source branch.")


def build(out_dir: Path, with_assets: bool = True) -> dict:
    for name, fields in (("buildings", BUILDING_FIELDS + BUILDING_DERIVED_FIELDS),
                         ("builders", BUILDER_FIELDS),
                         ("assets", ASSET_FIELDS),
                         # A new payload not named here ships with the credential and
                         # internal-field allow-list never having run on it at all.
                         ("benchmarks", BENCHMARK_FIELDS),
                         ("benchmark suburbs", BENCHMARK_SUBURB_FIELDS)):
        _check(name, fields)

    out_dir.mkdir(parents=True, exist_ok=True)
    app = Path(__file__).parent
    conn = sqlite3.connect(str(config.DATABASE_PATH))
    conn.row_factory = sqlite3.Row

    # source_text is READ but never EXPORTED. suburb_quality's postcode anchor scans it,
    # and benchmark_buildings.py selects it, so without it here the two disagreed: 19
    # published rows carried a benchmark computed for Wadalba while showing no suburb at
    # all, because the export could not see the text the benchmark had resolved from.
    # It stays out of BUILDING_FIELDS deliberately — it is the whole flattened
    # spreadsheet row, megabytes of it, and _pick drops it again immediately below.
    # READ but never EXPORTED. provenance.primary_source_link needs source_url and
    # stocklist_file to BUILD the per-document link; only the built link ships. Dropping
    # them from the SELECT as well as the export cost 6,242 rows their source link --
    # the feature stayed, but most rows lost it silently.
    # source_sheet / source_row / source_page are read for the same reason and fell into
    # the same trap: they only feed the LABEL that provenance builds, so they are not in
    # BUILDING_FIELDS — and being absent from the SELECT too meant every one of the 2,350
    # positions just backfilled reached the browser as a plain "Source file". Verified by
    # fetching the deployed stock.json and counting labels naming a row or page: 0 of
    # 5,859 before this line, which is precisely the silent-loss the comment above
    # describes happening a second time.
    # first_seen is read for the same reason: it becomes email_first_seen on emailed rows
    # (EMAIL_LIST_FIELDS) and is dropped from every row inside the loop.
    _read_fields = BUILDING_FIELDS + ["source_text", "source_url", "stocklist_file",
                                      "source_sheet", "source_row", "source_page",
                                      "first_seen"]
    rows = conn.execute(
        f"SELECT {', '.join(_read_fields)} FROM buildings "
        "ORDER BY builder_name, price").fetchall()
    buildings = [_pick(r, _read_fields) for r in rows]
    # One builder, one name, in the copy that reaches the browser. See
    # _display_name_canonicaliser for why this happens here and not in the database.
    _canon = _display_name_canonicaliser(b.get("builder_name") for b in buildings)
    _relabelled = 0
    for b in buildings:
        fixed = _canon.canonical(b.get("builder_name") or "")
        if fixed and fixed != b.get("builder_name"):
            b["builder_name"] = fixed
            _relabelled += 1

    # "Where this came from", precomputed per row so the Building Stock table can offer
    # it too. The recommendation cards already had it; the table only showed a link where
    # a per-lot URL happened to exist, so 3,776 E-Agent rows — the ones Colin was
    # scrolling through on 5 Aug hunting a lot — showed nothing at all.
    #
    # Computed HERE rather than mirrored in JavaScript: the ranking of lot > project >
    # price list > email is a judgement about what helps a consultant, and having it in
    # two places guarantees the two drift. Two short strings per row against a 5 MB
    # payload is the cheaper trade.
    from provenance import primary_source_link as _link_for
    import rea_link
    from rea_link import compare_link as _rea_link_for
    import suburb_quality
    _gone_pages, _gone_date = _lot_pages_gone()
    _gone_bp = _bp_ids(_gone_pages)
    _lot_gone = 0
    _linked = 0
    _rea_linked = 0
    _resuburbed = 0
    _suburb_reasons = {}
    _emails = {}          # email subject -> its rows; see the stash inside the loop
    # Built ONCE, before the loop, and keyed on source_url because source_doc is only
    # assigned inside it. See the UNCHECKED_NO_STATE branch below for what it is for.
    try:
        from api._candidates import _sibling_state_map as _state_map_of
        _state_by_document = _state_map_of([b for b in buildings if not b.get("superseded_by")])
    except Exception:                                  # pragma: no cover - import guard
        _state_by_document = {}

    try:
        from geo import SuburbGeoIndex as _SGI
        _index_for_names = _SGI()
    except Exception:                                  # pragma: no cover - import guard
        _index_for_names = None

    # WHAT THE LOCATED ROWS ON EACH DOCUMENT SAY, for the corroboration pass at the end.
    _located_says = collections.defaultdict(collections.Counter)
    for _b in buildings:
        if _b.get("superseded_by"):
            continue
        _s, _w = suburb_quality.resolve(dict(_b, suburb_source=""))
        if _s and suburb_quality.is_located(_w):
            for _f in ("source_url", "estate_name"):
                _k = str(_b.get(_f) or "").strip().lower()
                if _k:
                    _located_says[(_f, _k)][_s.strip().lower()] += 1

    _texts = {}           # id(row) -> its source_text, stashed before it is popped
    _evidence_failures = []
    for b in buildings:
        # IS THIS A PLACE? 3,854 published rows answered no and were shown as suburbs
        # anyway — '2026', 'offer', 'GARAGE', 'Logan City Council', 'Rooms Rooms m2 m2
        # m2' — each one sitting in the suburb column and in the suburb filter as though
        # it were a town.
        #
        # suburb_quality.resolve rebuilds the suburb where the row's own address states
        # one (the 56 Proxima "New South Wales" rows), reads it out of a composite where
        # the source glued an estate to it, and BLANKS it where there is no locality to
        # be had — recording which of those happened in suburb_source. The stored column
        # is untouched: suburb_norm is part of content_hash, so writing it back would
        # re-identify the row and the next harvest would insert a duplicate.
        before = (b.get("suburb") or "").strip()
        b["suburb"], why = suburb_quality.resolve(b)
        # A SUBURB THE SOURCE STATED, HELD BACK ONLY FOR WANT OF A STATE.
        #
        # 318 rows carry a locality the source plainly wrote and no state column to check
        # it against, so they published as "stated by the source; no state recorded" --
        # searchable, but refused for geocoding, distance and benchmarking. That refusal
        # is right in the abstract: 'Lucknow' is a real place in VIC and another in NSW.
        #
        # It is not right when the row's OWN DOCUMENT answers the question. Every one of
        # the 318 sits on a price list shared with other rows, and where those agree on a
        # state to within _sibling_state_map's 95% floor, that is evidence about this row
        # -- the same evidence api/_candidates already trusts at request time, which is
        # why these rows resolve for a search and were still published unlocated. 218 of
        # the 318 are settled this way; the other 100 keep the caveat.
        # ANY row the state is the only thing missing from — not just the one reason.
        #
        # This first fired only on UNCHECKED_NO_STATE and caught 218. But a stateless row
        # whose suburb column DOES hold a locality never reaches that reason: with no
        # state to check against, resolve falls through to reading the row's own text and
        # answers INFERRED_FROM_TEXT instead. Same missing state, same document able to
        # settle it, a different label — and 787 more rows sitting one fact away from
        # being located. "1-Part (SMFS) North 10 Havenwood Mernda Q4 2026" comes back
        # 'Mernda', STATED, the moment VIC is supplied.
        #
        # It can only ever upgrade: the borrowed state is kept only if the row then
        # resolves to a LOCATED reason, so a suburb that is not in that state changes
        # nothing and keeps the answer it had.
        if not str(b.get("state") or "").strip() and not suburb_quality.is_located(why):
            _borrowed = None
            for _f in ("source_url", "estate_name", "builder_name"):
                _borrowed = _state_by_document.get((_f, str(b.get(_f) or "").strip().lower()))
                if _borrowed:
                    break
            if _borrowed:
                _sub, _why = suburb_quality.resolve({**b, "state": _borrowed,
                                                     "suburb_source": ""})
                if suburb_quality.is_located(_why):
                    b["suburb"], why = _sub, _why
        # TWO READINGS THAT AGREE IS EVIDENCE; EITHER ONE ALONE IS NOT.
        #
        # A row whose suburb was read out of its own text sits at 83-94% and is refused
        # for geocoding, distance and benchmarking. Where the OTHER rows on its document
        # have a suburb established independently and they all name the SAME place, and
        # that place is the one this row read for itself, two separate routes have
        # arrived at one answer and it is no longer a lone guess.
        #
        # ONLY WHERE THEY AGREE. Measured across the catalogue, 199 inferred rows have a
        # located sibling to check against and 95 of them are CONTRADICTED by it -- and
        # when they disagree the winner varies: the 26 "Arca, Stafford" rows read Stafford
        # and their document says Theodore, where Stafford is right and Theodore is that
        # sheet's Building column; the Warralily rows read Elwood where the document says
        # Armstrong Creek and the document is right. There is no rule in the disagreement,
        # which is the whole reason the inferred grade exists. So a contradiction changes
        # nothing here and the row keeps its caveat.
        if suburb_quality.is_searchable(why) and not suburb_quality.is_located(why):
            _mine = str(b.get("suburb") or "").strip().lower()
            for _f in ("source_url", "estate_name"):
                _says = _located_says.get((_f, str(b.get(_f) or "").strip().lower()))
                if _says and len(_says) == 1:
                    # ...AND THE PROJECT'S OWN NAME MUST NOT NAME SOMEWHERE ELSE.
                    # "Price List_ARCA, Stafford.xlsx" is 35 apartments in Stafford whose
                    # suburb column holds "Theodore" -- the Building column of that sheet,
                    # which is also a real QLD town 500 km away. Those rows agree with
                    # each other because they share one mistake, so agreement alone
                    # promoted five of them. The project name is an independent third
                    # reading and it says Stafford, so it breaks the tie by refusing it.
                    _project = suburb_quality._longest_locality_in_text(
                        {"builder_name": b.get("builder_name"), "suburb": "",
                         "lot_address": ""}, str(b.get("state") or ""), _index_for_names)
                    _clash = _project and _project.strip().lower() != _mine
                    if _mine and next(iter(_says)) == _mine and not _clash:
                        why = suburb_quality.FROM_SIBLING_CONFIRMED
                    break
        b["suburb_source"] = why
        # WHICH DOCUMENT THIS ROW CAME FROM, as a hash rather than the URL.
        #
        # The deployed search groups rows by their source document to let a lot with no
        # locality borrow one from the lots beside it on the same price list. It was
        # keyed on source_url, which this build deliberately does NOT export — a Dropbox
        # folder link is a capability and the deployment is public — so that whole layer
        # was empty in production while measuring perfectly against the local database.
        # A hash groups the rows identically and discloses nothing about the link.
        _doc = str(b.get("source_url") or "").strip()
        # STASHED HERE BECAUSE source_url IS POPPED further down this same loop. An
        # emailed stocklist has no document to read, and its subject line is the only
        # thing the sender wrote about it; the pass that uses it runs after every row is
        # graded, by which time this field is gone.
        # NOT ONLY THE ONES PREFIXED "email:". Some arrive as the bare subject line --
        # "Fw: $989K - REGISTERED SECONDARY DWELLING - COLLINGWOOD PARK" -- with no
        # scheme at all, and that row went unlocated while its subject named the suburb
        # in capitals. Anything that is not an http(s) link is a label, not a document.
        if (_doc and not _doc.startswith("http") and not b.get("superseded_by")):
            _emails.setdefault(_doc, []).append(b)
        # Same reason as the subject above: source_text is popped further down this loop,
        # and the pass that reads a locality out of it runs after every row is graded.
        if not b.get("superseded_by"):
            _texts[id(b)] = str(b.get("source_text") or "")
        b["source_doc"] = (hashlib.sha256(_doc.encode("utf-8")).hexdigest()[:16]
                           if _doc else "")
        _suburb_reasons[why] = _suburb_reasons.get(why, 0) + 1
        if b["suburb"] != before:
            _resuburbed += 1
        # THE LOT'S OWN PAGE IS GONE (link_check.py, run just before this build). The row
        # stays in the stock table, marked, and the search refuses it (_candidates,
        # lot_page_gone). Its card and link fall back to the next thing that exists -- the
        # price list, the portal -- instead of "Lot PDF -- opens this lot" opening a 404.
        _page = str(b.get("listing_url") or "").strip()
        _hit = _gone_pages.get(_page)
        if not _hit and not _page.startswith("http"):
            # THE SAME PACKAGE ON A PRICE LIST WITH NO LINK OF ITS OWN. Review, 26 Sep: 22
            # rows from Hudson's "NSW Dual SEP NEW" stock list carry no listing_url but
            # print the package's BP id, whose page answers 404 -- the lot refused through
            # its E-Agent row was still offered through this one.
            _hit = _bp_twin(b.get("source_text"), _gone_bp)
        _gone = bool(_hit)
        b["lot_page_gone"] = _hit[0] if _hit else ""
        b["lot_page_status"] = _hit[1] if _hit else ""
        if _gone and not b.get("superseded_by"):
            _lot_gone += 1
        link = _published_link(b, _gone, _link_for)
        # A FOLDER capability is not publishable, even as the derived link. 2,027 rows
        # had primary_source_link hand back the shared-folder URL, because a folder is
        # all the provenance those rows have -- and that URL opens the whole folder to
        # anyone holding it, with no sign-in.
        #
        # The URL is dropped; the LABEL and the search term are kept. A consultant still
        # reads "VIC price list -- search there for Lot 623, Uptown", which is the part
        # that actually helps, and index.html renders it as text when there is no URL.
        if link and _FOLDER_CAPABILITY(str(link.get("url") or "")):
            link = {**link, "url": ""}
        # Price evidence reads source_text and lot_address, so it is taken BEFORE the text
        # is popped just below. Per row and guarded: see _price_evidence_cells.
        b.update(_price_evidence_cells(b, _evidence_failures))
        # Also before source_url is popped: the emailed list's key is its subject's stem.
        b.update(_email_list_cells(b, _evidence_failures))
        # And the line's key, before source_text is popped.
        b.update(_line_key_cells(b, _evidence_failures))
        b.pop("first_seen", None)
        # AFTER the link is built, not before: primary_source_link reads source_url and
        # stocklist_file to construct it, and popping them first cost 6,242 rows their
        # source link while leaving the feature apparently intact.
        for _internal in ("source_text", "source_url", "stocklist_file"):
            b.pop(_internal, None)          # read to build the link, never published
        b["source_link_url"] = (link or {}).get("url") or ""
        b["source_link_opens"] = (link or {}).get("opens") or ""
        b["source_link_label"] = (link or {}).get("label") or ""
        # The exact string to paste into that source's own search box. Colin searched
        # our headline address on Proxima and got an error: the address names the LOT,
        # while the portal's search indexes PROJECTS. See provenance._kind.
        b["source_link_search"] = (link or {}).get("search") or ""
        if link:
            _linked += 1
        # "Compare on realestate.com.au". Built AFTER suburb_quality.resolve above,
        # from b["suburb"] — never the stored column, which still holds 'GARAGE',
        # '2026' and 'Logan City Council'. rea_link gates on is_located(), so a row
        # whose suburb we cannot vouch for gets no URL and index.html prints the reason
        # instead. Measured on this database: 2,927 of 6,141 live rows (47.7%) get a
        # link — Proxima 1,274/1,368, Direct Portal 207/215, E-Agent 1,425/4,363,
        # digital email 21/195.
        rea = _rea_link_for(b)
        b["rea_search_url"] = rea.get("url") or ""
        b["rea_search_opens"] = rea.get("opens") or ""
        b["rea_search_label"] = rea.get("label") if rea.get("url") else ""
        b["rea_search_search"] = rea.get("search") or ""
        if b["rea_search_url"]:
            _rea_linked += 1
    # THE LAST RESORT, a second pass because it needs every row graded first.
    #
    # Asked for on the standing instruction to leave no listing unfindable. Everything
    # above needs the row, its document or its estate to name a place; these are the rows
    # where none of them does -- a Gallery Group line that is four prices and two "Click
    # here" links, a Hudson lot whose only word is its builder's name. They take the
    # majority suburb of their builder's or their document's other lots, which holdout
    # says is right 50% and 37% of the time.
    #
    # Graded INFERRED_FROM_SIBLINGS, never located. The row becomes searchable and shows
    # the inferred badge; is_located() stays false, so it is not geocoded, not distance
    # filtered, not benchmarked, and cannot enter a client's comparables. That grade
    # travels in suburb_source and survives the snapshot round trip -- see resolve().
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import document_suburb
    from geo import SuburbGeoIndex
    _geo = SuburbGeoIndex()
    from api._candidates import (last_resort_map, last_resort_suburb,
                                 _sibling_suburb_map, _sibling_state_map,
                                 _containment_candidates, _state_by_estate_containment,
                                 _unambiguous_estate_map, _estate_is_the_only_evidence)
    # THE SIBLING CASCADE RUNS AT SEARCH TIME, NOT HERE, and that is why this pass has to
    # repeat it. Applied straight to the snapshot, the last resort claimed 1,865 rows --
    # ten times its real population -- because every row waiting on an estate sibling
    # still looked unanswered. It would have overwritten a 92% rule with a 50% one.
    _est = _sibling_suburb_map(buildings, "estate_name")
    _page = _sibling_suburb_map(buildings, "source_doc")
    _states = _sibling_state_map(buildings)
    _contain = _containment_candidates(_states)
    _unambiguous = _unambiguous_estate_map(buildings)

    def _siblings_answer(row):
        """What the search-time rules would make of this row. "" if they decline."""
        probe = dict(row)
        if not str(probe.get("state") or "").strip():
            got = None
            for field in ("estate_name", "source_doc", "builder_name"):
                got = _states.get((field, str(probe.get(field) or "").strip().lower()))
                if got:
                    break
            if not got:
                got = _state_by_estate_containment(probe, _contain)
            if got:
                probe = {**probe, "state": got, "suburb_source": ""}
        key_state = str(probe.get("state") or "").strip().upper()
        for table, field in ((_est, "estate_name"), (_page, "source_doc")):
            found = table.get((str(probe.get(field) or "").strip().lower(), key_state))
            if found and suburb_quality.is_searchable(
                    suburb_quality.resolve({**probe, "suburb": found,
                                            "suburb_source": ""})[1]):
                return found
        if _estate_is_the_only_evidence(probe):
            only = _unambiguous.get(
                (str(probe.get("builder_name") or "").strip().lower(),
                 str(probe.get("estate_name") or "").strip().lower()))
            if only and only[0]:
                return only[1]
        return ""

    # THE EMAIL'S OWN SUBJECT LINE, before any guessing. A stocklist that arrives by mail
    # has no document to read, but the subject is the sender's own words about what is
    # in it: "Lakeside Living! $590k in Colac" is four Clearwater Estate lots in Colac.
    # Held to the same bar as a document title -- exactly ONE locality in the subject,
    # and only for an email where nothing else is located, so a round-up covering several
    # projects cannot hand its headline town to all of them.
    _by_email = _emails
    _from_subject = 0
    for _src, _group in _by_email.items():
        # WHAT THE EMAIL'S OWN ROWS ALREADY SAY. This started as "skip any email that has
        # a located row", meant to stop a round-up spreading its headline town -- and it
        # blocked the one case worth having: of the twelve rows on "Lakeside Living! $590k
        # in Colac", four resolve to Colac on their own. Agreement is the strongest
        # evidence in the group, not a reason to walk away from it. Disagreement still is.
        _settled = {suburb_quality.resolve(dict(g, suburb_source=""))[0]
                    for g in _group
                    if suburb_quality.is_located(
                        suburb_quality.resolve(dict(g, suburb_source=""))[1])}
        if len(_settled) > 1:
            continue                       # several towns in one email: it is a round-up
        _subject = _src[6:] if _src.startswith("email:") else _src
        _state = next((str(g.get("state") or "").strip() for g in _group
                       if str(g.get("state") or "").strip()), "")
        if not _state and _settled:
            _state = next((str(g.get("state") or "").strip() for g in _group
                           if suburb_quality.is_located(
                               suburb_quality.resolve(dict(g, suburb_source=""))[1])
                           and str(g.get("state") or "").strip()), "")
        # NO STATE IS THE NORMAL CASE HERE, not the exception: an emailed stocklist has
        # no page to read one off. So when the rows do not say, every state is tried and
        # the subject has to name the SAME one place in only one of them -- "Colac" is a
        # Victorian town and nothing else, which is what makes it usable.
        if _state:
            _named = document_suburb.localities_in(_subject, _state, _geo)
            _subject_states = {_state.strip().upper()} if _named else set()
        else:
            _named, _subject_states = [], set()
            for _try in ("VIC", "NSW", "QLD", "SA", "WA", "NT", "ACT", "TAS"):
                _found = document_suburb.localities_in(_subject, _try, _geo)
                if _found:
                    _named += _found
                    _subject_states.add(_try)
        if _settled:
            # The group already names a town. The subject only has to agree with it --
            # "Lakeside" is a QLD locality and "Colac" a Victorian one, so a blind
            # multi-state read of this subject finds two names and refuses; the rows
            # settle which one the email is about.
            _only = next(iter(_settled))
            if _only not in set(_named):
                continue
            _named, _subject_states = [_only], {"-"}
        # LONGEST NAME WINS ACROSS STATES, as it already does within one. The subject
        # "Fw: $989K - REGISTERED SECONDARY DWELLING - COLLINGWOOD PARK" finds
        # Collingwood Park in Queensland and Collingwood in Victoria, and refusing on
        # that is refusing to read a subject that could not be clearer. One is a prefix
        # of the other, so it is the same word read short.
        if len(set(_named)) > 1:
            _longest = max(_named, key=len)
            if all(_longest.lower().startswith(n.lower()) for n in set(_named)):
                _named = [_longest]
                _subject_states = {st for st in _subject_states
                                   if _geo.locate(_longest, st)}
        if len(set(_named)) != 1 or len(_subject_states) != 1:
            continue
        # AND THE STATE THE SUBJECT WAS READ IN (24 Oct 2026). Where the rows hold no state,
        # the place was accepted above only because it is a place of exactly one state --
        # but the row took the suburb and not the state, and a suburb with no state is
        # reached by no state search. "Fwd: 1x House and Land package at Novella Waters
        # Estate, Coomera priced at $1,582,900" (Creation Homes, lot 114): its brochure
        # names no place, Coomera is a Queensland locality and nowhere else, and the lot
        # was published as Coomera with no state. "-" (the rows' own town) brings none.
        _subject_state = next(iter(_subject_states))
        _subject_state = _subject_state if _subject_state in (
            "VIC", "NSW", "QLD", "SA", "WA", "NT", "ACT", "TAS") else ""
        for g in _group:
            if suburb_quality.is_searchable(g.get("suburb_source") or ""):
                continue
            g["suburb"] = _named[0]
            g["suburb_source"] = suburb_quality.FROM_DOCUMENT
            if _subject_state and not str(g.get("state") or "").strip():
                g["state"] = _subject_state
                g["state_source"] = "email subject"
            _from_subject += 1
    if _from_subject:
        print(f"    {_from_subject} listing(s) took the suburb their email subject names")

    # THE ROW'S OWN TEXT, WHEN IT HAS NO STATE TO READ IT AGAINST.
    #
    # _longest_locality_in_text needs a state -- deliberately, because 'Springs' is a
    # real place in WA and turns up inside Queensland estate names. A row with no state
    # can never reach it. Trying all eight and taking the answer only when exactly one
    # locality is found in exactly one state restores it for those rows.
    #
    # Measured by holdout on located Australian rows, hiding the suburb and reading it
    # back: 2,490 of 2,775, 89.7%. My first run of that same measurement said 33% and I
    # nearly threw the rule away on it -- the comparison was case-sensitive, so 'Wallan'
    # against 'WALLAN' counted as an error. The failures that are real are the family
    # this module already knows: a house design named Magnolia or Verona, a village
    # called March. That is why the answer is graded INFERRED_FROM_TEXT, the same reason
    # and the same badge as the stated-state version of this rule.
    _AU_STATES = ("VIC", "NSW", "QLD", "SA", "WA", "NT", "ACT", "TAS")
    # ...AND A ROW resolve() ALREADY READ THE SAME PLACE OUT OF (24 Oct 2026). With no
    # state, resolve() falls through to this very reading and grades the row
    # INFERRED_FROM_TEXT -- searchable -- so the pass below skipped it and its state stayed
    # blank, where no state search reaches it. APLACE's 16 Oct list is national (QLD and
    # VIC sections), so enrich voids its page state, and seven new lots whose lines open
    # "Joyner 7 New Road Park Lane Estate ..." and "Coomera 117 New Road Novella Waters
    # ..." (its SUBURB column; Joyner and Coomera are Queensland places and nowhere else)
    # were published as Joyner and Coomera with no state. Such a row takes the state only
    # where two more things hold: the place OPENS its line, as a suburb column does
    # (state_resolver.opens_with: not a design or an estate further in), and its own
    # document already holds that state on other rows -- the reading picks between the
    # document's states, never adds one. Measured on the 30 Sep copy plus tonight's
    # lists: exactly those seven rows.
    _doc_states = collections.defaultdict(set)
    for b in buildings:
        _held = str(b.get("state") or "").strip().upper()
        if _held and b.get("source_doc") and not b.get("superseded_by"):
            _doc_states[b["source_doc"]].add(_held)
    from state_resolver import opens_with as _opens_with
    _from_text = 0
    _text_state = 0
    for b in buildings:
        if b.get("superseded_by") or str(b.get("state") or "").strip():
            continue
        why = b.get("suburb_source") or ""
        _read_already = why == suburb_quality.INFERRED_FROM_TEXT
        if (suburb_quality.is_searchable(why) and not _read_already) \
                or why == suburb_quality.BLANK_OVERSEAS:
            continue
        _found = set()
        for _try in _AU_STATES:
            _got = suburb_quality._longest_locality_in_text(
                {**b, "source_text": _texts.get(id(b), ""), "suburb": "", "state": _try},
                _try, _geo)
            if _got:
                _found.add((_got, _try))
        if len({n.lower() for n, _s in _found}) != 1 or len({s for _n, s in _found}) != 1:
            continue
        _name, _state_of = next(iter(_found))
        if _read_already:
            if (_name.lower() == str(b.get("suburb") or "").strip().lower()
                    and _state_of in _doc_states.get(b.get("source_doc") or "", ())
                    and _opens_with(_texts.get(id(b), ""), _name, _geo)):
                b["state"] = _state_of
                b["state_source"] = "row text locality + document"
                _text_state += 1
            continue
        b["suburb"] = _name
        b["state"] = _state_of
        b["suburb_source"] = suburb_quality.INFERRED_FROM_TEXT
        _from_text += 1
    if _from_text:
        print(f"    {_from_text} listing(s) read a suburb out of their own text "
              f"(no state recorded; inferred, badged)")
    if _text_state:
        print(f"    {_text_state} listing(s) took the state of the place their line opens "
              f"with, a state their own document holds")

    _last_resort = last_resort_map(buildings)
    _guessed = 0
    for b in buildings:
        if suburb_quality.is_searchable(b.get("suburb_source") or ""):
            continue
        # NOT A SUPERSEDED ROW, and above all NOT AN OVERSEAS ONE. The ten Sanaya
        # Residences villas in Uluwatu are blank because they are in Bali, and this pass
        # would have handed each of them an Australian suburb off a sibling -- turning a
        # correct blank into a listing that claims to be somewhere it is not.
        if b.get("superseded_by"):
            continue
        if (b.get("suburb_source") or "") == suburb_quality.BLANK_OVERSEAS:
            continue
        if _siblings_answer(b):
            continue                    # a better rule already has this row at search time
        _fallback = last_resort_suburb(b, _last_resort)
        if not _fallback:
            continue
        # NOT AGAINST THE ROW'S OWN WRITTEN ADDRESS (7 Oct 2026, repair round 2). Nine lots
        # of one list wrote "Mt Duneed,Victoria,3217" or "Bonniebrook,Victoria,3335" and
        # were handed their builder's commonest suburb, Tarneit, here -- and shortlisted
        # in a Tarneit search. A row that names its own place keeps no guess at all.
        if not suburb_quality.written_address_agrees(
                {**b, "source_text": _texts.get(id(b), "")}, _fallback, b.get("state")):
            continue
        b["suburb"] = _fallback
        b["suburb_source"] = suburb_quality.INFERRED_FROM_SIBLINGS
        _suburb_reasons[suburb_quality.INFERRED_FROM_SIBLINGS] = _suburb_reasons.get(
            suburb_quality.INFERRED_FROM_SIBLINGS, 0) + 1
        _guessed += 1
    if _guessed:
        print(f"    {_guessed} listing(s) took a suburb from their builder's or "
              f"document's other lots (inferred, badged, never benchmarked)")
    # A BUILDER THE CLIENT HAS WITHDRAWN (builder_names.WITHDRAWN_BUILDERS) leaves here,
    # after every pass above has read its rows as evidence for their neighbours, so no
    # other row's suburb or state differs from a build without this. stock.json, the
    # coverage floor and our side of the benchmarks below then never see it.
    #
    # AND SO DOES A BLANK-BUILDER LOT IN ONE OF ITS PROJECTS (builder_names.is_withdrawn_row):
    # Proxima named Bathla on project 499 one night and nobody on the next, and the rule
    # keyed on the name let the blank nights through to the live NSW answer. The projects
    # are found here, from every row the database holds, live or superseded, because this
    # is the last point that still holds the rows naming the builder. They are written
    # into stock.json beside the rows for _candidates.load_snapshot, which no longer has
    # those rows to find them by.
    #
    # AND SO DOES A BLANK-BUILDER CAPTURE OF ONE OF ITS LOTS (builder_names.withdrawn_captures):
    # Bathla's Lot 128 and Lot 119 at Box Hill came back blank under project 724, which no
    # Bathla row is filed under, and only superseded_by still said they were the same lots.
    # Found here from every row for the same reason, and published beside the rows too.
    from builder_names import (is_withdrawn_builder, is_withdrawn_row, withdrawn_captures,
                               withdrawn_projects)
    _withdrawn_projects = withdrawn_projects(buildings)
    _withdrawn_captures = withdrawn_captures(buildings, _withdrawn_projects)
    _kept = [b for b in buildings
             if not is_withdrawn_row(b, _withdrawn_projects, _withdrawn_captures)]
    if len(_kept) != len(buildings):
        _by_name = sum(1 for b in buildings if is_withdrawn_builder(b.get("builder_name")))
        _by_project = sum(1 for b in buildings
                          if is_withdrawn_row(b, _withdrawn_projects)) - _by_name
        print(f"    {len(buildings) - len(_kept)} listing(s) left out: {_by_name} name a "
              f"withdrawn builder, {_by_project} name no builder in one of its "
              f"{len(_withdrawn_projects)} project(s), "
              f"{len(buildings) - len(_kept) - _by_name - _by_project} name no builder "
              f"and are another capture of one of its lots")
    buildings = _kept
    # WHAT THE APP WILL ACTUALLY RESOLVE, measured here because this is the only place
    # that holds both the graded rows and the sibling maps at once. The stored grade
    # alone understates it -- the estate and page maps run per REQUEST in
    # api/_candidates.py, so a row that says nothing in the snapshot may still be found
    # by its siblings. Counting the snapshot alone reported 98.99% for a catalogue that
    # answers 100%, and a floor set against the wrong number is a floor that fires on a
    # healthy night or misses a sick one.
    _au = _findable = _located = 0
    for b in buildings:
        if b.get("superseded_by"):
            continue
        _why = b.get("suburb_source") or ""
        if _why == suburb_quality.BLANK_OVERSEAS:
            continue
        _au += 1
        if suburb_quality.is_located(_why):
            _located += 1
        if suburb_quality.is_searchable(_why) or _siblings_answer(b):
            _findable += 1
    _coverage = {"australian": _au, "findable": _findable, "located": _located,
                 "share": (_findable / _au) if _au else 0.0}
    by_channel = [{"source_channel": r[0], "n": r[1]} for r in conn.execute(
        "SELECT source_channel, COUNT(*) FROM buildings GROUP BY 1 ORDER BY 2 DESC")]
    stamp = datetime.now().strftime("%d %b %Y, %H:%M")
    # Columnar, not a list of objects. Repeating 28 field names across 4,300 rows costs
    # 3 MB against 1.2 MB, and that difference was 18 seconds of blank table on the
    # deployed site. index.html accepts either shape (see asBuildings there), so the
    # live API can keep returning objects.
    (out_dir / "stock.json").write_text(json.dumps({
        "status": "success", "total": len(buildings), "by_channel": by_channel,
        "generated": stamp,
        # Stored columns plus the links derived above, so the table can trace a listing
        # back without the browser re-deriving the ranking.
        "keys": BUILDING_FIELDS + BUILDING_DERIVED_FIELDS,
        "rows": [[b.get(f) for f in BUILDING_FIELDS + BUILDING_DERIVED_FIELDS]
                 for b in buildings],
        # [source_channel, source_project_id] of each withdrawn project (see above), so
        # the search drops a blank-builder lot in one even from a snapshot whose rows no
        # longer show who the project belongs to.
        "withdrawn_projects": [list(k) for k in sorted(_withdrawn_projects)],
        # content_hash of each blank-builder capture joined to a withdrawn row (see above),
        # for the same reason: the rows that joined them are no longer here.
        "withdrawn_captures": sorted(_withdrawn_captures),
    }, separators=(",", ":"), default=str), encoding="utf-8")

    # BENCHMARKS — market comparables, written UNCONDITIONALLY, including with zero rows.
    #
    # Unconditional is what makes the tab EMPTY rather than BROKEN. getJSON races the API
    # against the static file; if neither exists it throws and the tab renders its error
    # branch, which reads as a fault. It is not a fault — no PropTrack key is stored, and
    # that is a commercial decision the client has not made. The file says so in its own
    # words, taken from the extractor rather than restated, so the page and the pipeline
    # can never report different statuses.
    _bench_rows = benchmarks.rows()
    _bench_status = benchmarks.status()
    _bench_by_place = benchmarks.counts_by_place()
    # Our own side of the comparison, which needs no key: every LOCATED (suburb, state)
    # we hold live stock in. Built from the rows already in hand, after suburb_quality
    # has resolved them, so nothing here can be a suburb we would refuse to link.
    _places = {}
    for b in buildings:
        if b.get("superseded_by"):
            continue
        if not b.get("rea_search_url"):      # not located; a link would be a broken search
            continue
        key = (str(b.get("suburb") or "").strip(), str(b.get("state") or "").strip().upper())
        slot = _places.setdefault(key, {"prices": [], "beds": collections.Counter(),
                                        "url": b.get("rea_search_url")})
        price = b.get("price")
        try:
            if price and float(price) > 0:
                slot["prices"].append(float(price))
        except (TypeError, ValueError):
            pass
        if b.get("bedrooms"):
            slot["beds"][str(b["bedrooms"])] += 1
    _suburb_rows = []
    for (suburb, state), slot in sorted(_places.items(),
                                        key=lambda kv: (-len(kv[1]["prices"]), kv[0])):
        market = _bench_by_place.get((suburb.lower(), state)) or {}
        _suburb_rows.append({
            "suburb": suburb, "state": state,
            "benchmarks_stored": market.get("n") or 0,
            "market_median": market.get("median"),
            # The suburb's own search link, from the same builder as the row-level one,
            # so the two can never disagree about what "compare this suburb" opens.
            "search_url": rea_link.search_url(suburb, state),
        })
    (out_dir / "benchmarks.json").write_text(json.dumps({
        "status": "success", "generated": stamp,
        "total": len(_bench_rows),
        "portal": benchmarks.PORTAL, "attribution": benchmarks.ATTRIBUTION,
        # configured / reason / stored / open_the_listing / open_a_suburb_search /
        # oldest_last_seen / last_read. The comparables channel is deliberately outside
        # harvest_buildings' blocking guard (a comparables outage must never freeze the
        # dashboard), so this meta block IS its health check — see benchmarks.status.
        "provider": _bench_status,
        "keys": BENCHMARK_FIELDS,
        "rows": [[r.get(f) for f in BENCHMARK_FIELDS] for r in _bench_rows],
        "suburbs": {"count": len(_suburb_rows), "keys": BENCHMARK_SUBURB_FIELDS,
                    "rows": [[r.get(f) for f in BENCHMARK_SUBURB_FIELDS]
                             for r in _suburb_rows]},
    }, separators=(",", ":"), default=str), encoding="utf-8")

    # builder registry, credentials stripped
    try:
        from builder_registry import BuilderRegistry
        regs = [_pick(b, BUILDER_FIELDS) for b in BuilderRegistry().get_all_builders()]
        for reg in regs:
            reg["notes"] = _redact_contacts(reg.get("notes"))
    except Exception as e:                                   # pragma: no cover
        print(f"[!] builder registry unavailable ({e}) — builders.json will be empty")
        regs = []
    (out_dir / "builders.json").write_text(json.dumps(
        {"status": "success", "count": len(regs), "generated": stamp, "builders": regs},
        separators=(",", ":"), default=str), encoding="utf-8")

    # harvested brochures / floorplans
    assets, by_builder, copied = [], [], 0
    try:
        arows = conn.execute(
            f"SELECT {', '.join(ASSET_FIELDS)}, local_path FROM builder_assets "
            "ORDER BY builder_name, asset_type").fetchall()
        for r in arows:
            a = _pick(r, ASSET_FIELDS)
            # The scraped title is the text of the builder's download button, identical
            # on every document they publish: 26 of these 56 files shared a title with
            # another, 16 of them reading "Icon to represent a home design brochure
            # Download Brochure". A consultant could not tell which file to send. The
            # file's own name in the URL distinguishes them and is the vendor's own
            # wording, not a guess.
            a["title"] = clean_asset_title(a.get("title"), a.get("source_url"))
            # Ship the PDF itself. Linking to the builder's own source_url is not
            # reliable — 3 of 5 sampled builder sites answer a direct request with HTML
            # rather than the file — and before this every brochure title on the
            # deployed site pointed at "#" and opened nothing.
            src = Path(r["local_path"] or "")
            if src.is_file() and with_assets:
                rel = f"assets/{_slug(a['builder_name'])}/{src.name}"
                dest = out_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists() or dest.stat().st_size != src.stat().st_size:
                    shutil.copyfile(src, dest)
                a["web_path"] = rel                  # relative: works at any mount point
                copied += 1
            assets.append(a)
        by_builder = [{"builder_name": r[0], "n": r[1]} for r in conn.execute(
            "SELECT builder_name, COUNT(*) FROM builder_assets GROUP BY 1 ORDER BY 2 DESC")]
    except sqlite3.OperationalError:
        pass
    (out_dir / "vendor-assets.json").write_text(json.dumps({
        "status": "success", "total_assets": len(assets), "by_builder": by_builder,
        "generated": stamp, "assets": assets,
    }, separators=(",", ":"), default=str), encoding="utf-8")

    # Every Australian locality, for the Research tab's suburb autocomplete.
    #
    # Coleen asked for this twice on 3 Aug and tied it to correctness rather than
    # convenience: "it has to compare the exact SEBAB name ... if it's not great, then
    # it's hard." A mistyped or ambiguous suburb produces a silently wrong benchmark,
    # which is worse than no benchmark. data/au_suburbs.csv already ships 17,537 of them
    # but reaches the deployment only inside the function bundle (see
    # _bundle_research_function), so the browser cannot read it.
    #
    # Parallel arrays, not objects: 17,537 {"suburb":…,"state":…} records is roughly
    # three times the bytes for the same information, and this is fetched per page load.
    try:
        import csv as _csv
        # How many LIVE listings we hold per locality, so the autocomplete can offer the
        # places a search can actually return something for. Computed here rather than in
        # the browser: STOCK is only fetched when the Building Stock tab is opened, so on
        # the Research tab the ranking would have no data and typing "Too" in QLD offered
        # Toobeah, Toogoom and Toolara while never reaching Toowoomba.
        _held = collections.Counter()
        for _b in buildings:
            if _b.get("superseded_by"):
                continue
            _sub = str(_b.get("suburb") or "").strip().lower()
            if _sub:
                _held[(_sub, str(_b.get("state") or "").strip().upper())] += 1
        _names, _states, _counts = [], [], []
        with open(app / "data" / "au_suburbs.csv", encoding="utf-8") as _fh:
            for _row in _csv.DictReader(_fh):
                _n = _row["suburb"].strip()
                _st = _row["state"].strip().upper()
                _names.append(_n)
                _states.append(_st)
                _counts.append(_held.get((_n.lower(), _st), 0))
        (out_dir / "suburbs.json").write_text(json.dumps(
            {"generated": stamp, "count": len(_names),
             "suburb": _names, "state": _states, "listings": _counts},
            separators=(",", ":")), encoding="utf-8")
        print(f"[+] suburbs.json: {len(_names):,} localities for the suburb autocomplete")
    except Exception as _e:                                           # noqa: BLE001
        # Not fatal — the field reverts to free text, which is how it has always worked.
        print(f"[!] could not write suburbs.json ({_e}) — autocomplete unavailable")

    named = sum(1 for b in buildings if (b.get("builder_name") or "").strip())
    meta = {
        "generated": stamp, "total": len(buildings), "named": named,
        "builders": len({(b.get("builder_name") or "").strip() for b in buildings
                         if (b.get("builder_name") or "").strip()}),
        "with_state": sum(1 for b in buildings if (b.get("state") or "").strip()),
        "assets": len(assets), "registry": len(regs), "copied": copied,
        "relabelled": _relabelled,
        "suburb_reasons": _suburb_reasons,
        "suburb_coverage": _coverage,
        # The compare-on-REA half, which works with no key at all. Reported so the
        # coverage is a number in the build log rather than something a reader has to
        # infer from how many em-dashes they scroll past.
        "rea_linked": _rea_linked,
        # Current rows whose own lot page answered 404/410 tonight (link_check.py), and
        # the date it was checked -- None when the check did not run, which is not 0.
        "lot_pages_gone": _lot_gone if _gone_date else None,
        "lot_pages_checked": _gone_date or None,
        "rea_suburbs": len(_suburb_rows),
        "benchmarks": len(_bench_rows),
        "with_suburb": sum(1 for b in buildings if (b.get("suburb") or "").strip()),
        # (content_hash, error) per row whose price evidence could not be computed. Those
        # rows published nulls; __main__ reports them and marks the run DEGRADED.
        "price_evidence_failures": _evidence_failures,
    }
    # Which sources are still producing stock — the Settings tab, and the banner that
    # would have shown E-Agent had signed out three nights before anyone noticed. Before
    # conn.close(), obviously, but worth saying why it is HERE and not beside the other
    # exports further down: those run after the connection is closed.
    meta["channel_health"] = _export_channel_health(out_dir, conn)
    conn.close()
    # The last source-to-search parity table, for the same tab (see _publish_parity_report).
    meta["parity_report"] = _publish_parity_report(out_dir)

    # the app's own frontend, unmodified
    # Stamp the copy as the static build.
    #
    # getJSON races /api/... against the static JSON and takes whichever answers
    # first — deliberately, because waiting on the API cost 10 seconds of dead time
    # on every load. On the DEPLOYED site those three endpoints can never exist
    # (server.py is not published, and for good reason), so the race guarantees three
    # 404s in the console on every visit. The page works, but anyone who opens devtools
    # sees a healthy site reporting errors, and it spends three round-trips finding out
    # something the build already knows.
    #
    # A marker rather than a separate deployed index.html: one frontend still serves
    # both, and locally the flag is simply absent so the live API is used as before.
    html = (app / "index.html").read_text(encoding="utf-8")
    # THE LOGO TRAVELS WITH THE DOCUMENT. Colin, 17 Aug: "every report by default should
    # add the logo and our details to show that it's us who have created that report."
    # The browser builds those reports and writes them into a blank window — which has no
    # base URL, so a relative <img src> resolves to nothing — and they are then emailed on
    # or opened from a share link. Inlined as a data URI, reusing client_report's own
    # resolution so the Python and browser documents cannot end up wearing different marks.
    import client_report as _cr
    _logo = _cr._logo_html()
    marker = ("<script>window.SPB_STATIC_BUILD = true;"
              "window.SPB_LOGO = %s;</script>\n" % json.dumps(_logo))
    if "</head>" in html:
        html = html.replace("</head>", marker + "</head>", 1)
    else:                                    # no head: put it before the first script
        html = marker + html
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    fn = _bundle_research_function(app, out_dir)
    meta["function_files"] = fn

    vercel = {
        "$schema": "https://openapi.vercel.sh/vercel.json",
        # /r is the client-facing share page. A rewrite rather than relying on Vercel's
        # extension stripping, so the link Coleen sends is a fixed, documented path.
        "rewrites": [{"source": "/r", "destination": "/r.html"}],
        "headers": [
            {"source": "/(.*)", "headers": [
                {"key": "X-Robots-Tag", "value": "noindex, nofollow"},
                # Clickjacking: without DENY, a third-party page can frame the signed-in
                # dashboard and read a consultant's clicks through it.
                {"key": "X-Frame-Options", "value": "DENY"},
                {"key": "X-Content-Type-Options", "value": "nosniff"},
                # Same-origin, so the full URL of a search is never handed to whichever
                # site a consultant clicks through to — realestate.com.au included.
                {"key": "Referrer-Policy", "value": "same-origin"},
            ]},
            # `private` keeps the catalogue out of shared proxy caches, where it would sit
            # readable by anyone the proxy serves and outlive the login entirely.
            # `no-cache` still allows the browser's own copy with an ETag revalidation, so
            # a repeat visit is a 304 rather than 7 MB again.
            # EVERY DATA FILE, not just stock.json.
            #
            # The Settings tab fetches channels.json with cache:'no-store', which bypasses
            # the BROWSER and not Vercel's edge. With no Cache-Control on that path the CDN
            # kept serving an older copy: on 3 Sep the page showed "Checked 12:04" and a
            # Hermitage figure from before the harvest, hours after a snapshot generated at
            # 12:53 had been deployed. It reads exactly like a fix that did not work.
            #
            # stock.json was given this header and its siblings were forgotten, so each new
            # published file has quietly inherited the wrong default.
            *[{"source": "/%s" % name,
               "headers": [{"key": "Cache-Control", "value": "private, no-cache"}]}
              for name in ("stock.json", "channels.json", "builders.json",
                           "benchmarks.json", "vendor-assets.json", "suburbs.json",
                           "parity_report.json")],
        ],
    }
    if fn:
        # Research & Scoring runs the Python pipeline, so on a static host it needs a
        # serverless function. Under api/ because Vercel's static builder excludes that
        # path — the pipeline source is bundled with the function, not served.
        vercel["functions"] = {
            "api/research.py": {
                "memory": 1024, "maxDuration": 30,
                "includeFiles": "{api/_bootstrap.py,api/_candidates.py,"
                                "api/_export_builders.py,api/_data/**,api/_lib/**,stock.json}",
            },
            # Starts the nightly harvest on demand. Small and stdlib-only: it holds a
            # GitHub token and talks to one API, and deliberately shares nothing with the
            # pipeline bundle above — a credential in the same process as the whole
            # harvest is a wider blast radius than this feature is worth.
            "api/refresh.py": {"memory": 256, "maxDuration": 30},
            # One Groq call per selected option, up to eight, serially. 25s each at the
            # far end would blow a 30s ceiling, so this one gets longer.
            "api/narrative.py": {"memory": 256, "maxDuration": 60},
            # Clare's Leads tab: a stdlib proxy to Agent 1's hook (20s upstream timeout, so 30).
            # Coleen, 28 Sep 2026: it now masks each caller's email and phone unless the
            # contact-details password was given, and the rules for that live in
            # api/_contact.py -- so that file travels with every function that imports it.
            # Without it the import fails and the whole tab 500s on a green deploy.
            "api/hot_leads.py": {"memory": 256, "maxDuration": 30,
                                 "includeFiles": "api/_contact.py"},
            # The contact-details password (sets or clears the spb_contact cookie). No
            # upstream call; 0.7 s is the longest it ever takes.
            "api/contact_unlock.py": {"memory": 256, "maxDuration": 10,
                                      "includeFiles": "api/_contact.py"},
            # Notes and links per lead: a stdlib proxy to the hook, built on hot_leads.py's
            # key handling, so it carries that file as well as the masking rules.
            "api/lead_notes.py": {"memory": 256, "maxDuration": 30,
                                  "includeFiles": "{api/_contact.py,api/hot_leads.py}"},
            # Opens one entry of the Settings tab's "Price lists we could not read" by id
            # (24 Oct 2026). Stdlib only; the id -> link map travels with it and is never a
            # static file, because most of those links are Dropbox folder shares.
            "api/price_list.py": {"memory": 256, "maxDuration": 10,
                                  "includeFiles": "api/_data/price_list_links.json"},
        }
    (out_dir / "vercel.json").write_text(json.dumps(vercel, indent=2), encoding="utf-8")
    _write_auth_middleware(out_dir)
    meta["share_page_js"] = _write_share_page(app, out_dir)
    # Belt and braces if anyone ever deploys from the repo root instead of vercel_site.
    (out_dir / ".vercelignore").write_text("\n".join((
        "requirements.txt", "Book1(Builders) List.csv", "drive_input/", ".sessions/",
        ".env", "credentials.json", "spb_research_audit.db", "spb_research_audit.db.bak-*",
        "output/", "server.py", "build_web.py", "harvest_buildings.py", "portal_login.py",
        "enrich_buildings.py", "migrate_buildings_identity.py", "tests/", "__pycache__/",
        "_harvest.log", "_server.log", "",
    )), encoding="utf-8")
    return meta


# The transitive import closure of kommo_agent — nothing else is needed, and nothing
# else should be shipped.
# Every module the deployed function imports, directly or transitively. A module missing
# from this list is not a build warning — it is an ImportError inside the serverless
# function, so EVERY request 500s while the page and the tests look perfectly healthy.
# _verify_function_imports below fails the build rather than shipping that again.
_FUNCTION_ROOT_MODULES = (
    "address_label.py", "benchmark.py", "provenance.py", "comparison_report.py", "brief_parser.py", "builder_registry.py",
    "client_report.py", "config.py", "database.py", "drive_ingest.py", "geo.py",
    "kommo_agent.py", "kommo_client.py", "qa_checker.py", "report_generator.py",
    "schema.py", "scoring_engine.py", "secrets_store.py", "state_resolver.py",
    "turnkey_calculator.py",
    # _candidates.py imports this to decide whether a row's suburb is a place at all.
    # Omitting it would not fail the build — it would fail every deployed search.
    "suburb_quality.py",
    # _candidates.py and sources/builder_portals.py import is_withdrawn_builder from it at
    # module level. Standard library only.
    "builder_names.py",
    # THE DOCUMENT MAP HAD NEVER SHIPPED. suburb_quality imports this lazily inside a
    # try/except for both FROM_DOCUMENT and FROM_VERIFIED, so its absence in the bundle
    # was not an error anywhere: the deployed API simply answered "" to both and 941
    # listings that resolve locally had no suburb on the live site, along with all 17
    # hand-verified rules. Exactly the shape the note above this one warns about — it
    # worked on my machine because my machine has the file.
    "document_suburb.py",
    # Benchmark A. Omitting these did not break the build — api/research.py catches the
    # ImportError so a benchmark failure cannot cost a consultant their search — so the
    # deployed endpoint simply returned no verdict at all while it worked locally. That
    # is the same silent-degradation shape as the harvest reporting success on an empty
    # channel, and it is why _verify_function_imports asserts on the bundle below.
    "benchmark_competitive.py", "benchmark_market.py", "comp_provider.py",
    "comp_provider_proptrack.py",
    "price_kind.py",
)
_FUNCTION_SOURCES = (
    "__init__.py", "adaptive_extract.py", "base.py", "builder_portals.py", "dedupe.py",
    "drive_pdf.py", "e_agent.py", "feature_extract.py", "portal_config.py",
    "remote_stocklist.py", "scraper_base.py", "spreadsheet_extract.py",
    "realestate.py",
)
# refresh.py, narrative.py, hot_leads.py, contact_unlock.py and lead_notes.py are stdlib-only
# and import nothing from the pipeline, so they cannot drag the harvest's modules into another
# bundle; the last three carry api/_contact.py as includeFiles, and lead_notes.py hot_leads.py
# as well. A public api/*.py missing from here is never copied, and its route 404s on a site
# that otherwise looks healthy -- tests/test_hot_leads_proxy.py asserts every one is listed.
_FUNCTION_ENTRY = ("research.py", "_bootstrap.py", "_candidates.py", "_export_builders.py",
                   "refresh.py", "narrative.py", "hot_leads.py",
                   # Coleen, 28 Sep 2026: the contact-details password and the notes per
                   # lead. _contact.py is not a route; hot_leads.py, contact_unlock.py and
                   # lead_notes.py all import it, so a bundle without it is three dead
                   # routes. Probed below.
                   "_contact.py", "contact_unlock.py", "lead_notes.py",
                   # research.py imports this inside a try/except, so leaving it out does
                   # not break the build or the endpoint -- it just quietly stops every
                   # region search widening, exactly as document_suburb.py did for the
                   # document map. The probe below asserts it loads.
                   "_regions.py",
                   # The same shape, 15 Sep 2026: research.py imports this inside a
                   # try/except too, so a bundle without it would quietly stop reading
                   # "Tarn eat" as Tarneit and nobody would see an error. Probed below.
                   "_location_resolver.py",
                   # Ahsan, 24 Oct 2026: the unreadable price lists open from the Settings
                   # tab through this. Stdlib only, like refresh.py; it carries
                   # api/_data/price_list_links.json, probed below.
                   "price_list.py")


MIDDLEWARE_JS = r"""// Server-side gate for the whole dashboard. Generated by build_web.py — do not edit here.
//
// WHY THE CHECK IS NOT IN index.html. Colin, 12 Aug: "the app is publicly accessible,
// posing a security risk". A login screen written into the page would not fix it, because
// this is a STATIC site — the browser fetches stock.json directly, and so can anyone:
//
//     curl https://app.smartpropertybuying.com.au/stock.json   ->  200, 7,918,574 bytes
//
// That is the whole catalogue, every builder, every price, with no page and no login
// involved. A JavaScript check also ships the password to the client, where View Source
// reads it. Both are theatre. Edge middleware runs on Vercel's side BEFORE any file is
// served, so the JSON and the PDFs are gated exactly as the page is.
//
// WHY A COOKIE SESSION AND NOT HTTP BASIC AUTH. Basic was the first cut and it works, but
// it is the browser's grey popup with no branding, no way to sign out short of closing
// every window, and it re-sends the password on every one of the ~40 requests a page load
// makes. A form posts it once.
//
// THE SESSION IS SIGNED WITH THE PASSWORD ITSELF, not a second secret. That means one
// value to configure rather than two — and changing the password instantly invalidates
// every outstanding session, which is the behaviour you want on the day it needs changing.
export const config = { matcher: "/(.*)" };

const COOKIE = "spb_session";
const CONTACT_COOKIE = "spb_contact";      // the contact-details unlock; see /logout below
const SESSION_SECONDS = 12 * 60 * 60;      // a working day; a shared laptop should re-ask
const DEFAULT_USER = "digital@smartpropertybuying.com.au";
const enc = new TextEncoder();

export default async function middleware(request) {
  const url = new URL(request.url);
  const path = url.pathname;
  const password = (process.env.SITE_PASSWORD || "").trim();
  const user = (process.env.SITE_USERNAME || DEFAULT_USER).trim();

  // FAILS CLOSED. With no password configured the site does not quietly go public — that
  // is the silent-degradation failure this project keeps meeting, and it would be
  // invisible precisely because everything still works. It says what is wrong instead.
  if (!password) {
    return new Response(
      "This dashboard is not configured for access yet.\n\n" +
      "SITE_PASSWORD is not set on the Vercel project, so it is refusing to serve\n" +
      "rather than serving to everyone.\n",
      { status: 503, headers: { "content-type": "text/plain; charset=utf-8" } });
  }

  // THE ONE PUBLIC ROUTE, and the only one there should ever be.
  //
  // Coleen has to send a buyer their report, and she must not send them this app: it
  // holds every builder, every wholesale price and the log of what was rejected, so
  // sharing the login would expose all 11,474 listings to a client. /r is a static shell
  // that renders a report out of the URL FRAGMENT — and a fragment is never transmitted
  // in an HTTP request, so this route serves the same bytes to everyone and can leak
  // nothing. It reads no database, calls no API, and has no access to stock.json, which
  // the check below still gates.
  //
  // Exact matches only. A prefix test (startsWith("/r")) would have opened /research,
  // /reports and anything else beginning with those characters.
  if (path === "/r" || path === "/r.html") return;

  // ONE MACHINE CALLER: AGENT 1 STARTING A RESEARCH ON A CALLER IT GRADED.
  //
  // When Agent 1's voice call grades a caller hot, warm or nurture and they gave a state,
  // its webhook (vapi-kommo-hook) asks this dashboard for a shortlist straight away, so the
  // lead reaches Coleen with options already found. Hot-only until 15 Sep 2026, when Ahsan
  // widened Agent 2 to every caller with a state: so this route now carries most calls, not
  // only the hot ones.
  //
  // That webhook is a server. It has no browser, so it can never hold the session
  // cookie below: it proves itself with a shared secret instead, SPB_SERVICE_KEY, sent as
  // the x-spb-service-key header and compared in constant time just as the password is.
  //
  // EXACTLY POST /api/research, and nothing wider. Running a search is the whole of what the
  // webhook needs. A key that also opened GET, /stock.json or a prefix match would turn one
  // leaked webhook secret into the whole catalogue, which is the exposure this gate exists
  // to stop. And with the variable unset or blank there is no bypass at all: an empty key
  // must never match an empty header. The header is never logged or echoed.
  if (request.method === "POST" && path === "/api/research") {
    const serviceKey = (process.env.SPB_SERVICE_KEY || "").trim();
    if (serviceKey && timingSafeEqual(request.headers.get("x-spb-service-key") || "", serviceKey)) {
      return;                                  // Agent 1's webhook, not a person: serve it
    }
  }

  if (path === "/logout") {
    // TWO COOKIES END HERE. Coleen, 28 Sep 2026: a caller's full phone and email show only
    // to someone who entered the contact-details password, which sets spb_contact
    // (api/_contact.py) at Path=/api. A browser clears a cookie only for the Path it was set
    // with, so that one needs its own Set-Cookie at Path=/api -- clearing it at Path=/ would
    // leave it in place, and the next person to sign in on a shared laptop would see every
    // number in full for the rest of its 4 hours.
    const headers = new Headers({ "location": "/login", "cache-control": "no-store" });
    headers.append("set-cookie", COOKIE + "=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax");
    headers.append("set-cookie",
                   CONTACT_COOKIE + "=; Path=/api; Max-Age=0; HttpOnly; Secure; SameSite=Strict");
    return new Response(null, { status: 303, headers });
  }

  if (path === "/login") {
    if (request.method === "POST") return signIn(request, user, password);
    if (await validSession(request, user, password)) return redirect("/");
    return htmlResponse(loginPage(safeNext(url.searchParams.get("next")), ""), 200);
  }

  if (await validSession(request, user, password)) return;   // authorised — serve it
  return challenge(request, path + url.search);
}

async function signIn(request, user, password) {
  const body = new URLSearchParams(await request.text());
  const next = safeNext(body.get("next"));

  // Both comparisons ALWAYS run. `okUser && okPass` would short-circuit, so a wrong email
  // would answer measurably faster than a right email with a wrong password — which tells
  // an attacker when they have found the username.
  const okUser = timingSafeEqual((body.get("email") || "").trim().toLowerCase(),
                                 user.toLowerCase()) ? 1 : 0;
  const okPass = timingSafeEqual(body.get("password") || "", password) ? 1 : 0;

  if ((okUser & okPass) === 1) {
    const exp = Math.floor(Date.now() / 1000) + SESSION_SECONDS;
    const token = exp + "." + (await sign(password, user + "|" + exp));
    return new Response(null, { status: 303, headers: {
      "location": next,
      // HttpOnly so script cannot read it, Secure so it never crosses plain HTTP, Lax so
      // it survives this redirect but is not sent on cross-site POSTs.
      "set-cookie": COOKIE + "=" + token + "; Path=/; Max-Age=" + SESSION_SECONDS +
                    "; HttpOnly; Secure; SameSite=Lax",
      "cache-control": "no-store",
    }});
  }

  // A fixed delay on failure. The edge runs stateless across regions, so there is nowhere
  // to count attempts without adding a store; this at least caps a single attacker at a
  // couple of guesses a second instead of hundreds. NOT a substitute for a strong
  // password — it is the reason the password has to be one.
  await new Promise(function (r) { setTimeout(r, 700); });
  return htmlResponse(loginPage(next, "That email or password is not right."), 401);
}

function challenge(request, next) {
  // A person browsing gets the login page. A fetch() for stock.json gets a 401 it can
  // detect — redirecting that one would hand the page 40 KB of HTML where it expected
  // JSON, and it would fail with a parse error that says nothing about signing in.
  const wantsHtml = (request.headers.get("sec-fetch-dest") || "") === "document" ||
                    (request.headers.get("accept") || "").includes("text/html");
  if (wantsHtml) return redirect("/login?next=" + encodeURIComponent(next));
  return new Response(JSON.stringify({ error: "auth_required", login: "/login" }), {
    status: 401,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

async function validSession(request, user, password) {
  const raw = readCookie(request, COOKIE);
  const dot = raw.lastIndexOf(".");
  if (dot < 1) return false;
  const exp = raw.slice(0, dot);
  const seconds = Number(exp);
  if (!Number.isFinite(seconds) || seconds * 1000 <= Date.now()) return false;
  return timingSafeEqual(raw.slice(dot + 1), await sign(password, user + "|" + exp));
}

async function sign(secret, data) {
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(data)));
  let s = "";
  for (let i = 0; i < mac.length; i++) s += String.fromCharCode(mac[i]);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function timingSafeEqual(a, b) {
  const A = enc.encode(a), B = enc.encode(b);
  let diff = A.length ^ B.length;
  const n = Math.max(A.length, B.length);
  for (let i = 0; i < n; i++) diff |= (A[i] || 0) ^ (B[i] || 0);
  return diff === 0;
}

function readCookie(request, name) {
  const raw = request.headers.get("cookie") || "";
  const parts = raw.split(";");
  for (let i = 0; i < parts.length; i++) {
    const eq = parts[i].indexOf("=");
    if (eq > -1 && parts[i].slice(0, eq).trim() === name) return parts[i].slice(eq + 1).trim();
  }
  return "";
}

function safeNext(value) {
  // An open redirect turns the login page into a phishing tool: /login?next=https://evil
  // would sign Coleen in and then hand her to someone else's copy of this screen. Only
  // same-site paths, and never back to /login itself or the form loops forever.
  const v = String(value || "");
  if (!v.startsWith("/") || v.startsWith("//") || v.startsWith("/\\")) return "/";
  if (v === "/login" || v.startsWith("/login?") || v.startsWith("/logout")) return "/";
  if (/[\x00-\x1f]/.test(v)) return "/";
  return v;
}

function redirect(location) {
  return new Response(null, { status: 302,
    headers: { "location": location, "cache-control": "no-store" } });
}

function htmlResponse(html, status) {
  return new Response(html, { status, headers: {
    "content-type": "text/html; charset=utf-8",
    "cache-control": "no-store",
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
  }});
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
                  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function loginPage(next, error) {
  // Palette lifted from index.html so this does not read as a different product. Inline,
  // because a stylesheet request would itself be gated by the middleware above.
  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Sign in - Smart Property Buying</title>
<style>
  :root { --bg:#f8fafc; --card:#fff; --line:#e2e8f0; --ink:#0f172a; --body:#334155;
          --muted:#64748b; --accent:#1e40af; --rose:#b91c1c; --rose-bg:#fef2f2; }
  * { box-sizing:border-box; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:var(--bg); color:var(--body); padding:24px;
         font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:36px 32px; width:100%; max-width:390px;
          box-shadow:0 1px 3px rgba(15,23,42,.08); }
  h1 { margin:0; font-size:20px; color:var(--ink); letter-spacing:-.01em; }
  .sub { margin:4px 0 26px; color:var(--muted); font-size:13px; }
  label { display:block; font-size:13px; font-weight:600; color:var(--ink);
          margin:0 0 6px; }
  input[type=email], input[type=password] {
          width:100%; padding:10px 12px; margin:0 0 18px; font-size:15px;
          border:1px solid #cbd5e1; border-radius:7px; background:#fff; color:var(--ink); }
  input:focus { outline:2px solid var(--accent); outline-offset:-1px; border-color:var(--accent); }
  button { width:100%; padding:11px; font-size:15px; font-weight:600; cursor:pointer;
           border:0; border-radius:7px; background:var(--accent); color:#fff; }
  button:hover { background:#1d4ed8; }
  .err { background:var(--rose-bg); border:1px solid #fecaca; color:var(--rose);
         border-radius:7px; padding:10px 12px; margin:0 0 20px; font-size:13.5px; }
  .foot { margin:22px 0 0; font-size:12px; color:var(--muted); text-align:center; }
</style>
</head><body>
<main class="card">
  <h1>Smart Property Buying</h1>
  <p class="sub">Property Research Dashboard</p>
  ${error ? '<p class="err">' + esc(error) + "</p>" : ""}
  <form method="POST" action="/login">
    <input type="hidden" name="next" value="${esc(next)}">
    <label for="email">Email</label>
    <input id="email" name="email" type="email" autocomplete="username" required autofocus>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    <button type="submit">Sign in</button>
  </form>
  <p class="foot">Authorised users only. This dashboard contains confidential stock and pricing.</p>
</main>
</body></html>`;
}
"""


# The slice of index.html that renders a report. Kept as constants because
# tests/test_comparison_report.py extracts the same range to exercise it under Node — the
# share page and the tests must both be looking at the code that actually ships.
_REPORT_JS_START = "const SHARE_PATH = "
_REPORT_JS_END = "    async function openGeneratedReport("

SHARE_PAGE_SHELL = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Smart Property Buying &mdash; Property Report</title>
</head><body style="margin:0;font:14px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#334155">
<div id="msg" style="padding:2.5rem;max-width:34rem">Loading report&hellip;</div>
<script>
const esc = s => String(s ?? '').replace(/[&<>"]/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
__REPORT_JS__
(async function () {
  const fail = m => { document.getElementById('msg').textContent = m; };
  try {
    const frag = decodeURIComponent(location.hash.slice(1));
    if (!frag) return fail('This link has no report in it. Ask for a new link.');
    // A fragment never reaches a server, so this cap is only about not handing the
    // decompressor an unbounded input from whoever holds the link.
    if (frag.length > 400000) return fail('This report link is too large to open.');
    const data = await unpackShare(frag);
    if (!data || !Array.isArray(data.p) || !data.p.length) {
      return fail('This report link is not valid. Ask for a new link.');
    }
    // Every value below is rendered through esc() by the generators, which is what makes
    // it safe to render a document out of whatever the URL happened to contain.
    // The client's name travels in `c` and the brief in `b`; the merged document shows
    // "Prepared for <name>" from either kind, so `c` is folded in rather than dropped.
    // Object.assign order matters only if a future brief ever carries its own client.
    const html = data.k === 'compare'
      ? comparisonDocument(data.p, null, Object.assign({ client: data.c || '' }, data.b || {}))
      : clientReportDocument(data.p, null, data.c || '');
    document.open(); document.write(html); document.close();
  } catch (e) {
    fail('This report link could not be opened \\u2014 it may have been cut short when it '
       + 'was copied or forwarded. Ask for a fresh link.');
  }
})();
</script>
</body></html>
"""


# A channel that reads nightly and has not produced a row in this many days is not
# "quiet", it is broken. E-Agent, Proxima and the direct portals all run every night, so
# two days covers one missed run plus the clock. Beyond a week it is not a blip.
CHANNEL_STALE_DAYS = 2
CHANNEL_DEAD_DAYS = 7
# Below this share of a source's stored listings refreshed on its last read, the run
# brought back a fraction and stamped it as current. Proxima read 16 against 1,396 stored
# on 17 Aug — 1% — and the first version of this tab called that "ok" because something
# arrived. 50% is deliberately loose: a source legitimately shrinks, and a bar set tight
# would cry wolf until nobody read the tab at all.
CHANNEL_PARTIAL_PCT = 50

# SOURCES THAT PUSH RATHER THAN RE-LIST. The "partial" test asks what fraction of a
# source's stored stock it handed back on its newest day, which only means anything for a
# source that re-lists everything it holds each run. An inbox does not: a builder emails a
# price list when they have one and does not re-send it daily, so its rows keep the date
# the email arrived — 73 rows stamped 24 Aug, 34 stamped 20 Aug, 10 today. Judged that way
# "digital email" reads 6% and is flagged as failing on a day it worked perfectly.
#
# Being quiet is still caught: the age tests below are untouched, so an inbox that stops
# delivering goes stale after CHANNEL_STALE_DAYS exactly as any other source does.
CHANNEL_PUSH_SOURCES = frozenset({"digital email"})

# A FRACTION OF A HANDFUL IS NOISE. Bathla Development is 14 live rows on the direct
# portal and one of them refreshed, which reads 7% and flags as "only part of its stock" —
# while 168 of Bathla's other 182 rows come through Proxima and 135 of those refreshed
# today. The stock is fine; the alarm is arithmetic on a rump.
#
# The failure this rule was written for had 1,396 rows behind it (Proxima returning 16 of
# them on 17 Aug), so a floor costs nothing there and removes a standing false alarm here.
# A small source that dies outright still goes stale on age.
CHANNEL_PARTIAL_MIN_LIVE = 25

# "REFRESHED" IS ONE RUN, NOT ONE CALENDAR DAY (23 Oct 2026). It counted the live rows
# dated a channel's newest day, so a read that crossed midnight was cut in two: a run
# dispatched at 23:04 read Proxima from 23:40 to 00:00:44, the 288 lots read after midnight
# made up the newest day on their own, and this tab said 3% -- "ONLY PART OF ITS STOCK" --
# of a read that was whole (8,363 lots, 109 of 109 projects). The harvest now stamps one
# date per run (sources.base.read_date), but a date is still the wrong unit for "what did
# the last read see", so the tab measures the run itself: the live rows whose checked_at
# (the moment, see _newest_ts) belongs to the same run as the channel's newest one.
#
# A RUN IS ONE BURST OF WRITES, found by walking back from the newest checked_at until the
# stamps stop. record_building is the only writer of checked_at, and the harvest stores a
# channel's rows straight after that channel's read returns -- one burst each for E-Agent,
# the Stocklist, Proxima and the inbox, one per state for the direct portals -- so the
# stamps of one run sit seconds apart and a pause of CHANNEL_RUN_GAP_MINUTES is the end
# of an earlier run. Measured on run 39950641008 (23 Oct): Proxima stored its 8,363 rows
# in 13 seconds (13:00:44Z, when its read returned, to 13:00:57Z, when the inbox sweep
# connected), and the direct portals read state after state 10 to 40 seconds apart.
# Against that, two nightly runs leave the same channel at least 33 minutes apart: runs
# are queued one at a time (the spb-daily-harvest concurrency group), and a re-run
# dispatched the moment a job fails only reaches its first store, E-Agent's, 33 minutes in
# (12:04:47Z to 12:37:45Z); two runs that both finish are over an hour and a half apart
# (Proxima stored 56 minutes after dispatch, and the run went on 49 minutes after that).
# Two Proxima reads by hand (harvest_buildings.py --proxima-only) cannot store closer
# than the 20 minutes one read takes (12:40:16Z to 13:00:44Z). Fifteen minutes sits
# between: twenty times the longest pause inside a run, short of the quickest gap between
# two. Only a quick channel read twice by hand -- the portals or the inbox, a minute or
# two each -- still blends, as two runs on one date always did. The other way round, a
# state's portal read that hung for a quarter of an hour would cut that night's portal
# rows in two and could flag them "partial": a loud error to look at, not a quiet "ok".
#
# And no stamp more than CHANNEL_RUN_SPAN_HOURS older than the newest is the same run,
# however the bursts chain: the harvest job is killed at 150 minutes (daily-harvest.yml),
# so the cap never cuts a real run and only bites on reads repeated by hand.
#
# WHY NOT A FIXED WINDOW. The first cut of this (same day, before review) took every stamp
# within 12 hours of the newest, on the reasoning that 12 is half the gap between two
# scheduled nights, so last night's rows never count as tonight's. Runs are not only
# scheduled: 23 Oct had the 09:00 run and manual ones dispatched at 13:08 and 23:04, and
# that window took the 13:08 run's Proxima read (stored at 14:03) into the 23:04 run's
# (stored from 00:00:44). Worse, a full read by a late manual run followed by a 09:00 run
# that hands back 16 of 1,000 read as 100% "ok" -- the 17 Aug failure (16 lots against a
# stored 1,396) this tab exists to catch, and one the day rule did catch. The day rule
# had its own version: two runs on one date blend, so a thin re-run the same afternoon
# read as whole. The gap separates both.
#
# A channel whose rows carry no checked_at (stored before 18 Aug 2026) keeps the day rule.
CHANNEL_RUN_GAP_MINUTES = 15
CHANNEL_RUN_SPAN_HOURS = 3


def _trimmed_url(value, limit=120):
    """A URL cut to `limit` characters without leaving a half-written %-escape behind."""
    text = str(value or "")[:limit]
    return re.sub(r"%[0-9A-Fa-f]?$", "", text)


def _days_since(stamp) -> int:
    """Whole days between a "dd/mm/YYYY HH:MM" stamp and now, or -1 if it cannot be read."""
    match = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", str(stamp or ""))
    if not match:
        return -1
    try:
        day, month, year = (int(g) for g in match.groups())
        return max(0, (datetime.now() - datetime(year, month, day)).days)
    except ValueError:
        return -1


def _readable_name(url) -> str:
    """"Atlas Price New.pdf (dropbox.com)" -- enough to identify the file, no capability.

    Never the query string: on Dropbox and SharePoint the secret IS the path and the
    token, so the whole URL is the credential. Percent-escapes are decoded for reading
    and any half-written one is dropped rather than published broken.
    """
    from urllib.parse import unquote, urlparse
    parsed = urlparse(str(url or ""))
    tail = unquote((parsed.path or "").rstrip("/").rsplit("/", 1)[-1])
    tail = re.sub(r"%[0-9A-Fa-f]?$", "", tail).strip()
    host = (parsed.netloc or "").replace("www.", "")
    if not tail or len(tail) < 4 or "." not in tail:
        return host or "(a file with no name in its link)"
    return f"{tail[:80]} ({host})" if host else tail[:80]


UNREADABLE_SHOWN = 80
# id -> link for the panel's entries. Written INTO THE FUNCTION BUNDLE (api/_data), which
# Vercel does not serve as a static file, and read only by api/price_list.py.
PRICE_LIST_LINKS = "price_list_links.json"


def _price_list_link(entry) -> str:
    """The http(s) link one unreadable entry opens, or "" if it opens nothing.

    The harvest records it as `link` since 24 Oct 2026. Older lists carried only `file`,
    which for a downloaded file IS its link. Anything that is not http(s) -- a file name,
    javascript:, file:, data: -- opens nothing, and the entry keeps its name.
    """
    from urllib.parse import urlparse
    for value in (entry.get("link"), entry.get("file")):
        text = str(value or "").strip()
        parsed = urlparse(text)
        if (parsed.scheme.lower() not in ("http", "https") or not parsed.netloc
                or re.search(r"[\s\x00-\x1f\x7f]", text)):
            continue
        # A Dropbox file is fetched with dl=1, which downloads it. A person checking it
        # wants Dropbox's own preview.
        if parsed.netloc.lower().endswith("dropbox.com"):
            text = re.sub(r"([?&])dl=1(?=&|#|$)", r"\1dl=0", text)
        return text
    return ""


def _price_list_id(link: str) -> str:
    """An opaque name for one link: 16 hex characters of its SHA-256. A share link holds a
    long random key, so the id cannot be turned back into it."""
    return hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]


def _unreadable_entries():
    """(payload, the entries the panel shows), or (None, []) when there is no list."""
    path = Path(__file__).resolve().parent / "data" / "unreadable_stocklists.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                  # pragma: no cover - absent is fine
        return None, []
    files = [f for f in payload.get("files") or [] if isinstance(f, dict)]
    return payload, files[:UNREADABLE_SHOWN]


def _write_price_list_links(dest_api: Path) -> int:
    """api/_data/price_list_links.json: the id -> link map api/price_list.py resolves.

    Built from the same list as the ids in channels.json, so every id the page shows is
    here. Returns how many links it holds.
    """
    _payload, shown = _unreadable_entries()
    links = {}
    for entry in shown:
        link = _price_list_link(entry)
        if link:
            links[_price_list_id(link)] = link
    out = dest_api / "_data" / PRICE_LIST_LINKS
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(links, indent=1, sort_keys=True), encoding="utf-8")
    return len(links)


def _unreadable_stocklists():
    """What the last harvest fetched and could read nothing out of."""
    payload, shown = _unreadable_entries()
    if payload is None:
        return {"generated": "", "count": 0, "files": [], "open": []}
    files = payload.get("files") or []
    # HOW OLD IT IS, ALONGSIDE WHAT IT SAYS. The harvest rewrites this file on the runner
    # and the runner is thrown away, so a deploy from a laptop republishes whatever copy
    # that laptop happens to hold. On 10 Sep the published panel was showing a list
    # generated on 27 August -- a fortnight stale, undated on the page, and contradicted
    # by the app beside it: it named Koops Price.pdf as unreadable while Koop listings
    # were live from that very file. A panel Coleen acts on has to say when it was true.
    return {"generated": payload.get("generated", ""),
            "days_old": _days_since(payload.get("generated", "")),
            "count": len(files),
            # THE NAME, NOT THE LINK. 56 of these 57 are Dropbox "/scl/fo/" folder
            # shares -- capability URLs that hand over a whole shared folder to anyone
            # holding the string, with no sign-in. build_web has a whole assertion
            # devoted to keeping those out of stock.json, and channels.json was quietly
            # publishing them anyway, because the guard only ever read one file.
            #
            # The panel exists so somebody can go and ask that builder for a readable
            # price list. A file name does that; the capability URL only adds risk.
            "files": [_readable_name(f.get("file")) for f in shown],
            # ...AND A WAY TO OPEN IT, STILL WITHOUT THE LINK (Ahsan, 24 Oct 2026: he
            # wants to check the price list himself). One opaque id per entry, "" where
            # there is nothing to open. /api/price_list?id= turns it into the link on the
            # server, behind the sign-in; the links stay in the function bundle.
            "open": [_price_list_id(link) if link else ""
                     for link in map(_price_list_link, shown)]}


def _sheet_check():
    """Last night's line-by-line check of every listing against its own price list.

    Ahsan, 9 Sep 2026: "i want you to confirm all the listings are present and when i open
    the sheet they are at exact number that is written". The check runs nightly
    (run_daily.py step 4/7, verify_sheet_positions.py) and writes its report here. This
    puts the ANSWER on the Settings tab, because a confirmation nobody can see is not a
    confirmation -- it is a log line in a file on a runner that is deleted every morning.

    The counts are stated as the check states them, including what it refused to judge.
    "Could not open the file" and "opened it and could not recognise the row" are
    different admissions and a single 'unverified' number would hide both.
    """
    path = Path(__file__).resolve().parent / "data" / "sheet_check.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                  # pragma: no cover - absent is fine
        return {}
    tally = collections.Counter(r.get("verdict") for r in payload.get("rows") or [])
    docs = payload.get("documents") or []
    return {
        "generated": str(payload.get("generated", ""))[:16].replace("T", " "),
        "checked": int(payload.get("checked") or 0),
        "live": int(payload.get("live") or 0),
        "documents": len(docs),
        "at_stated_row": tally.get("at_stated_row", 0),
        "present": tally.get("present_no_line", 0),
        "moved": tally.get("moved", 0),
        "absent": tally.get("absent", 0),
        "unmatched": tally.get("unmatched", 0),
        "unreadable": tally.get("unreadable", 0),
        "pages_not_files": sum(d.get("stored", 0) for d in docs
                               if d.get("a_page_not_a_file")),
    }


def _publish_parity_report(out_dir: Path) -> bool:
    """Put the last source-to-search parity report beside stock.json, for the Settings tab.

    7 Oct 2026. parity_check.py runs AFTER this build (it searches the bundle this writes)
    and drops tonight's report into the build itself. This copy is for every other build --
    deploy.ps1 rebuilds before it publishes -- so a rebuild carries the latest report rather
    than quietly losing the panel. The report says which night it describes, so an old one
    reads as old. Absent or unreadable: an EMPTY report is published (no rows), which the
    panel reads as "no panel" -- never no file at all (repair round 1, 7 Oct 2026: with no
    file Vercel answered the Settings tab's fetch with a 404, which the browser logs as a
    console error on every visit to the tab).
    """
    path = Path(__file__).resolve().parent / "data" / "parity_report.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                  # pragma: no cover - absent is fine
        payload = None
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        (out_dir / "parity_report.json").write_text(
            json.dumps({"version": 1, "rows": [], "failures": [], "notes": [],
                        "verdict": "", "night": ""}), encoding="utf-8")
        return False
    (out_dir / "parity_report.json").write_text(json.dumps(payload), encoding="utf-8")
    return True


def _norm_source(name) -> str:
    """'Bathla Development' and 'Bathla' collapse to the same key."""
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _published_link(row, page_gone, link_for):
    """The one link a card and the stock table carry: the lot's own page, unless
    link_check.py found it gone -- then the next thing that exists (the price list, the
    email, the portal), so "opens this lot" never opens a 404."""
    return link_for(dict(row, listing_url="") if page_gone else row)


def _lot_pages_gone():
    """({listing_url: (since 'dd/mm/yyyy', why)}, 'dd/mm/yyyy' checked) from
    .sessions/link_status.json, written by link_check.py just before this build.

    `why` is "gone" for a page that answered 404/410, or the status word the builder's own
    page shows ("Under offer", "Sold") when it names the lot not available. `since` is the
    night the page was first found so, carried forward by link_check.

    Absent or unreadable -> ({}, ''): nothing is marked and nothing is left out, which is
    the build as it was before the check existed. A page the check could not judge (a
    timeout, a 403, a host held back for going dark all at once) is not in the map.
    """
    path = config.PROJECT_ROOT / ".sessions" / "link_status.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, ""
    urls = data.get("urls") if isinstance(data, dict) else None
    if not isinstance(urls, dict):
        return {}, ""
    checked = str(data.get("date") or "").strip()
    pages = {}
    for u, r in urls.items():
        if not isinstance(r, dict) or r.get("verdict") not in ("gone", "unavailable"):
            continue
        why = ("gone" if r.get("verdict") == "gone"
               else str(r.get("page_status") or "not available").strip())
        pages[str(u).strip()] = (str(r.get("since") or checked).strip(), why)
    return pages, checked


_BP_IN_URL = re.compile(r"/property/(BP-\d+)/", re.I)
_BP_IN_TEXT = re.compile(r"\bBP-\d{4,}\b", re.I)


def _bp_ids(pages):
    """{BP id: (since, why)} for the Kenekt-family package pages in `pages`."""
    out = {}
    for url, hit in pages.items():
        m = _BP_IN_URL.search(url)
        if m:
            out.setdefault(m.group(1).upper(), hit)
    return out


def _bp_twin(text, bps):
    """(since, why) when a row's own text names exactly one package id and that package's
    page is gone or unavailable; None otherwise (a line naming several is not one lot)."""
    named = {m.upper() for m in _BP_IN_TEXT.findall(str(text or ""))}
    if len(named) != 1:
        return None
    return bps.get(next(iter(named)))


def _login_status():
    """{normalised source name: login row} from .sessions/login_status.json.

    WRITTEN BY portal_login.py --check, WHICH THE NIGHTLY RUNS. A MEASURED sign-in, not
    one inferred from how much stock came back -- those are different facts and on
    20 Sep they disagreed both ways at once: Hermitage read 43% of its stock on a
    PERFECTLY GOOD login, while E-Agent read 65% on a session that was not signed in at
    all and had failed 111 stocklist fetches that night. Inferring one from the other
    sends a person to re-authenticate a portal that is fine and leaves the broken one
    alone. Absent file -> every row reads 'not checked', which is honest; it never
    guesses.
    """
    path = config.PROJECT_ROOT / ".sessions" / "login_status.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for r in rows:
        if isinstance(r, dict) and r.get("label"):
            # "E-Agent (covers all E-Agent builders)" is the channel E-Agent.
            out[_norm_source(str(r["label"]).split("(")[0])] = r
    return out


def _login_for(name, logins):
    """The login row for a channel or portal, or a 'not checked' stand-in.

    Matched on a normalised prefix because the two lists are named by different things:
    the health table by source_channel / builder_name ('Bathla Development'), the
    sessions by the login they belong to ('Bathla').
    """
    key = _norm_source(name)
    if not key:
        return {"state": "unknown"}
    hit = logins.get(key)
    if hit is None:
        for k, row in logins.items():
            # A row that names its channel (E-Agent's Stocklist search, 7 Oct 2026) belongs
            # to that channel alone. "eagent" is a prefix of "eagentstocklist", so on a night
            # with no E-Agent session row the E-Agent line took the Stocklist's and told the
            # owner E-Agent needed "the EAgent Stocklist company login from your E-Agent BDM".
            if row.get("channel") and _norm_source(row["channel"]) != key:
                continue
            if k and (key.startswith(k) or k.startswith(key)):
                hit = row
                break
    if hit is None:
        return {"state": "unknown"}
    out = {"state": hit.get("state") or "unknown", "session": hit.get("session"),
           "age_days": hit.get("age_days"), "checked_at": hit.get("checked_at")}
    # Whether "Fetch stock now" can fix this row, and when it cannot, why. Carried
    # through rather than recomputed in the browser: the answer depends on whether a
    # password is stored in the vendor sheet, which the page must never see.
    #
    # And who measured it and what the page showed (23 Oct 2026): "basis" is "harvest"
    # when tonight's own sign-in decided the row and "check" when the saved session was
    # reopened afterwards; "reason" tells a dead session from a crash. The row's "url"
    # stays in login_status.json and is not published; a reason or note names a PATH at
    # most ("password field at /login"), never a host or a query string.
    for k in ("self_service", "blocked_by", "note", "basis", "reason"):
        if k in hit:
            out[k] = hit[k]
    return out


# Worst first: one dead sign-in inside a group is what the group should report.
# "no_login_needed" sits ABOVE ok: a public stocklist can never be the group's problem.
# Missing from this map it took the default 2, so once Hermitage read ok the "Direct
# Builder Portal" group would have said "no sign-in needed" over a portal that has one.
_LOGIN_RANK = {"missing": 0, "expired": 1, "unknown": 2, "not_applicable": 3, "ok": 4,
               "no_login_needed": 5}


def _export_channel_health(out_dir: Path, conn) -> dict:
    """When each source last actually produced stock, for the Settings tab.

    WHY THIS EXISTS. E-Agent's sign-in expired on 13 Aug 2026. The harvest kept running,
    logged "[FAILED] ... E-Agent: read 0 tonight" for three consecutive nights, reported
    SUCCESS to GitHub, and published a dashboard that went on serving ~4,500 E-Agent
    listings as current. Nothing on screen said so. It surfaced only when a consultant
    could not find, in the app, stock he was looking straight at in E-Agent.

    The run-side hole is closed separately (run_daily now alerts and CI fails the run),
    but that tells ME, not Coleen — and she is the one who notices a suburb has gone
    quiet. So the same fact is published: per channel, the day it last produced a row.

    DERIVED FROM THE STOCK ITSELF, not from a session file's timestamp. A saved session
    can look perfectly healthy while the portal has quietly stopped honouring it, which is
    exactly what E-Agent did — .sessions/e_agent.json still sat there, 4 KB, dated 31 Jul.
    "Rows arrived" is the only claim worth making, and it is the one a consultant cares
    about. Session files are never published: they are live credentials.
    """
    from datetime import date, datetime as _dt, timedelta

    today = date.today()
    now_ts = _dt.now()
    # REORDERED TO YYYYMMDD IN SQL, and this is the point of doing it here rather than
    # with MAX(date_checked). date_checked is stored DD/MM/YYYY, so a plain MAX() is a
    # STRING comparison that puts '29/07/2026' above '13/08/2026' — it reads the leading
    # digit and stops. That mistake made a channel four days stale look nineteen days
    # stale during the 17 Aug investigation and pointed it at the wrong incident.
    ISO = ("substr(date_checked, 7, 4) || substr(date_checked, 4, 2) "
           "|| substr(date_checked, 1, 2)")
    LIVE = "(superseded_by IS NULL OR superseded_by = '')"
    # NOT A WITHDRAWN BUILDER'S ROWS, by name, by project or by capture
    # (builder_names.is_withdrawn_row, the rule stock.json is built with). They stay in the
    # database but the app cannot offer them, so counting them told Settings on 23 Oct
    # that "Direct Builder Portal (live)" held 453 lots and refreshed 46% of them, when 14
    # of the 453 were Bathla's and its portal is no longer read at all. The same Python
    # rule runs inside the query as a function, so Held and Refreshed below are counted
    # from the rows the app can offer and no second, SQL spelling of the rule can drift
    # from the first. A table without a project or hash column passes NULL, which names
    # no project and joins nothing.
    from builder_names import is_withdrawn_row, withdrawn_captures, withdrawn_projects
    _have = {r[1] for r in conn.execute("PRAGMA table_info(buildings)")}
    _project = "source_project_id" if "source_project_id" in _have else "NULL"
    _hash = "content_hash" if "content_hash" in _have else "NULL"
    _rows = [{"builder_name": n, "source_channel": c, "source_project_id": p,
              "content_hash": h, "superseded_by": s}
             for n, c, p, h, s in conn.execute(
                 f"SELECT builder_name, source_channel, {_project}, {_hash}, superseded_by "
                 f"FROM buildings")]
    _projects = withdrawn_projects(_rows)
    _captures = withdrawn_captures(_rows, _projects)
    conn.create_function(
        "spb_withdrawn", 4, lambda n, c, p, h: int(is_withdrawn_row(
            {"builder_name": n, "source_channel": c, "source_project_id": p,
             "content_hash": h}, _projects, _captures)))
    LIVE += f" AND NOT spb_withdrawn(builder_name, source_channel, {_project}, {_hash})"

    def _tally(group_sql, where_sql, params=()):
        """{name: {iso: live_rows}} for one grouping."""
        out = {}
        for name, iso, n in conn.execute(
                f"SELECT {group_sql} AS g, {ISO} AS iso, COUNT(*) FROM buildings "
                f"WHERE {LIVE}{where_sql} GROUP BY g, iso", params):
            out.setdefault(str(name or "").strip() or "(unnamed)", {})[iso or ""] = n
        return out

    def _newest_ts(group_sql, where_sql, params=()):
        """{name: newest checked_at} — a real timestamp, so recency can be said in hours.

        SEPARATE FROM _tally because it answers a different question. date_checked is a
        DAY and drives every staleness threshold; checked_at is the MOMENT and drives
        nothing but the wording on screen. MAX() is safe here where it is not on
        date_checked: ISO 8601 sorts correctly as a string, which is the whole reason it
        was stored that way.

        NULL on every row harvested before 18 Aug 2026, so this is empty until a run has
        been through — and _health falls back to whole days when it is.
        """
        out = {}
        for name, ts in conn.execute(
                f"SELECT {group_sql} AS g, MAX(checked_at) FROM buildings "
                f"WHERE {LIVE} AND checked_at IS NOT NULL{where_sql} GROUP BY g", params):
            if ts:
                out[str(name or "").strip() or "(unnamed)"] = ts
        return out

    def _moment(ts):
        """checked_at as a naive datetime, or None. The first 19 characters only, as
        supersede_duplicates._read_time reads it, so an offset or a fraction a future
        writer adds can neither fail to parse nor refuse to compare with a naive stamp."""
        try:
            return _dt.strptime(str(ts or "").strip()[:19].replace(" ", "T"),
                                "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None

    def _run_counts(group_sql, where_sql, params=()):
        """{name: live rows read by its LATEST RUN} -- walking back from that name's newest
        checked_at for as long as the stamps keep coming (no pause of
        CHANNEL_RUN_GAP_MINUTES) and no further than CHANNEL_RUN_SPAN_HOURS. A name none
        of whose rows carries a readable checked_at is absent, and _health keeps the day
        rule."""
        stamps = {}
        for name, ts, n in conn.execute(
                f"SELECT {group_sql} AS g, checked_at, COUNT(*) FROM buildings "
                f"WHERE {LIVE} AND checked_at IS NOT NULL{where_sql} "
                f"GROUP BY g, checked_at", params):
            moment = _moment(ts)
            if moment is not None:
                key = str(name or "").strip() or "(unnamed)"
                stamps.setdefault(key, []).append((moment, n))
        gap = timedelta(minutes=CHANNEL_RUN_GAP_MINUTES)
        span = timedelta(hours=CHANNEL_RUN_SPAN_HOURS)
        out = {}
        for key, seen in stamps.items():
            seen.sort(key=lambda s: s[0], reverse=True)
            newest = later = seen[0][0]
            total = 0
            for moment, n in seen:
                if later - moment > gap or newest - moment > span:
                    break
                total += n
                later = moment
            out[key] = total
        return out

    def _hours_since(ts):
        if not ts:
            return None
        try:
            delta = now_ts - _dt.fromisoformat(str(ts))
        except ValueError:
            return None
        # A clock that disagrees with the build machine's can put this slightly in the
        # future; report 0 rather than a negative age.
        return max(0, round(delta.total_seconds() / 3600))

    def _health(name, by_iso, ts_map=None, runs=None):
        """One row of the Settings table.

        REPORTS VOLUME AS WELL AS RECENCY, and the second one was the gap. The first cut
        asked only "did this source produce a row lately", so on 17 Aug it printed Proxima
        as "ok" while that run had read 16 lots against a stored 1,396 — a 99% collapse,
        shown as healthy, on the very tab built to stop exactly this being invisible. The
        harvest's own partial-read guard caught it; the tab flattered it.

        `refreshed` is how many of the live rows the last successful READ saw — i.e. how
        much of what we hold it actually handed back. A source that returns a fraction of
        its stock is stamping that fraction as today's and letting the rest go quietly
        stale, which is the same harm as not running at all, minus the warning. Counted
        per run from checked_at (`runs`, see CHANNEL_RUN_GAP_MINUTES), and from the
        newest date only where no row of the source carries one.
        """
        newest, live = None, sum(by_iso.values())
        for iso in by_iso:
            if len(iso) == 8 and iso.isdigit():
                try:
                    d = date(int(iso[0:4]), int(iso[4:6]), int(iso[6:8]))
                except ValueError:
                    continue
                if newest is None or d > newest:
                    newest = d
        age = (today - newest).days if newest else None
        if name in (runs or {}):
            # Never more than is held: _tally keys a NULL and a blank name alike, and the
            # later of the two overwrites the earlier's counts where this one adds them.
            refreshed = min(runs[name], live)
        else:
            refreshed = by_iso.get(newest.strftime("%Y%m%d"), 0) if newest else 0
        pct = round(100.0 * refreshed / live) if live else 0
        if age is None:
            state = "unknown"
        elif age >= CHANNEL_DEAD_DAYS:
            state = "dead"
        elif age > CHANNEL_STALE_DAYS:
            state = "stale"
        elif (pct < CHANNEL_PARTIAL_PCT
              and name not in CHANNEL_PUSH_SOURCES
              and live >= CHANNEL_PARTIAL_MIN_LIVE):
            # Recent but thin: it ran, and brought back a fraction. Only asked of a
            # source that re-lists its whole stock each run, and only where there is
            # enough of it for the fraction to mean anything — see the two constants.
            state = "partial"
        else:
            state = "ok"
        return {"channel": name, "live": live,
                "last_stock": newest.strftime("%d/%m/%Y") if newest else "",
                "days_ago": age,
                # None until a harvest has written checked_at; the tab says days then.
                "hours_ago": _hours_since((ts_map or {}).get(name)),
                "refreshed": refreshed, "refreshed_pct": pct,
                "state": state}

    logins = _login_status()
    chan_ts = _newest_ts("source_channel", "")
    chan_runs = _run_counts("source_channel", "")
    channels = [_health(n, t, chan_ts, chan_runs)
                for n, t in _tally("source_channel", "").items()]
    channels.sort(key=lambda c: (-(c["days_ago"] or 0), -c["live"]))
    for c in channels:
        # An inbox has no sign-in to break, so it says so rather than reading as a
        # portal nobody has checked -- the same reason it is exempt from the partial
        # test (CHANNEL_PUSH_SOURCES).
        c["login"] = ({"state": "not_applicable"} if c["channel"] in CHANNEL_PUSH_SOURCES
                      else _login_for(c["channel"], logins))
    # A SOURCE WITH A LOGIN AND NO STOCK YET (7 Oct 2026). The table above is built from the
    # stock, so a source that has never delivered a row has no line in it -- and E-Agent's
    # Stocklist search cannot deliver one until the client gets its company login from her
    # E-Agent BDM. The row that says so is the one thing that unblocks it, so a login row
    # naming a channel (portal_login.py --check) gets a line of its own until stock arrives.
    named = {_norm_source(c["channel"]) for c in channels}
    for login in logins.values():
        name = str(login.get("channel") or "").strip()
        if name and _norm_source(name) not in named:
            named.add(_norm_source(name))
            channels.append({"channel": name, "live": 0, "last_stock": "",
                             "days_ago": None, "hours_ago": None, "refreshed": 0,
                             "refreshed_pct": 0, "state": "unknown",
                             "login": _login_for(name, logins)})

    # THE DIRECT PORTALS, BY NAME. They all share one source_channel, so Bathla,
    # Hermitage and Torsion appeared as a single row called "Direct Builder Portal
    # (live)" — and when one signs out, that row names the group rather than the portal
    # that broke, which is the one thing a person needs in order to fix it.
    portal_ts = _newest_ts("builder_name", " AND source_channel LIKE ?",
                          ("Direct Builder Portal%",))
    portal_runs = _run_counts("builder_name", " AND source_channel LIKE ?",
                              ("Direct Builder Portal%",))
    portals = [_health(n, t, portal_ts, portal_runs) for n, t in
               _tally("builder_name", " AND source_channel LIKE ?",
                      ("Direct Builder Portal%",)).items()]
    # A withdrawn builder's portal is no longer read, so its row would only age into a
    # stale alarm naming a supplier the client has dropped.
    from builder_names import is_withdrawn_builder
    portals = [p for p in portals if not is_withdrawn_builder(p["channel"])]
    portals.sort(key=lambda c: (-(c["days_ago"] or 0), -c["live"]))
    for p in portals:
        p["login"] = _login_for(p["channel"], logins)
    # The group row reports its worst child: "Direct Builder Portal (live)" has no
    # sign-in of its own, and a green group over a dead portal is the exact thing the
    # per-portal table below it was added to stop.
    group = [p["login"].get("state", "unknown") for p in portals]
    for c in channels:
        if group and str(c["channel"] or "").startswith("Direct Builder Portal"):
            c["login"] = {"state": min(group, key=lambda s: _LOGIN_RANK.get(s, 2)),
                          "from_portals": True}
    unwell = ("stale", "dead", "partial")
    payload = {
        "generated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "stale_after_days": CHANNEL_STALE_DAYS, "dead_after_days": CHANNEL_DEAD_DAYS,
        "partial_below_pct": CHANNEL_PARTIAL_PCT,
        "partial_min_live": CHANNEL_PARTIAL_MIN_LIVE,
        # STOCKLISTS THAT WERE FETCHED AND YIELDED NOTHING. Written by the harvest; see
        # sources.spreadsheet_extract.note_unreadable for why a file that reads as zero
        # has to be named rather than logged. 57 of 143 on 27 Aug -- whole projects
        # missing, and Coleen found one of them by hand before anything said so.
        "unreadable_stocklists": _unreadable_stocklists(),
        "sheet_check": _sheet_check(),
        "push_sources": sorted(CHANNEL_PUSH_SOURCES),
        "channels": channels,
        "portals": portals,
        # Portals are named individually here too: "Direct Builder Portal (live)" tells
        # nobody which sign-in to go and fix.
        "needs_signin": [c["channel"] for c in channels + portals if c["state"] in unwell],
        # MEASURED, and deliberately not the same list as needs_signin above. That one
        # is "this source's stock has gone quiet or thin", which is a symptom; this one
        # is "we opened the portal with the saved session and it was not signed in",
        # which is the cause and names the command that fixes it.
        "needs_login": [p["channel"] for p in portals + channels
                        if p.get("login", {}).get("state") in ("expired", "missing")],
        # A source nobody could speak for tonight: a public stocklist with no sign-in, a
        # check that could not run (its reason says why), or Proxima on a night the harvest
        # did not try it -- the check never opens Proxima, and its cell otherwise comes from
        # the harvest's own sign-in. Kept out of needs_login so nobody is sent to fix
        # something that is not broken.
        "login_not_assessed": [p["channel"] for p in portals + channels
                               if p.get("login", {}).get("state")
                               in ("no_login_needed", "unknown")],
        "logins_checked_at": next((r.get("checked_at") for r in _login_status().values()
                                   if r.get("checked_at")), ""),
    }
    (out_dir / "channels.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _write_share_page(app: Path, out_dir: Path):
    """Emit /r, the page a client's link opens.

    WHY A SEPARATE PAGE, AND WHY IT IS PUBLIC. Coleen has to send a buyer their report.
    She must not send them the app: it holds every builder, every wholesale price and the
    log of what was rejected, and one shared password would expose all of it. So this page
    is exempt from the login gate — and it can be, because it contains no data. The report
    travels in the URL FRAGMENT, which browsers never transmit, so nothing about a client
    or our stock is stored here, sent here, or written to any log.

    BUILT FROM index.html rather than hand-written, because a second copy of the renderers
    is a second thing to keep in step, and this repo has already been bitten by exactly
    that (server.py's shortlist entry silently lost three fields api/research.py sends).
    """
    src = (app / "index.html").read_text(encoding="utf-8")
    if _REPORT_JS_START not in src or _REPORT_JS_END not in src:
        # Loud, because the alternative is publishing a share page whose links all fail
        # on the client's phone with nothing on our side saying so.
        raise SystemExit(
            "[abort] cannot find the report renderers in index.html "
            f"({_REPORT_JS_START!r} .. {_REPORT_JS_END!r}) — /r would be broken.")
    js = src[src.index(_REPORT_JS_START):src.index(_REPORT_JS_END)]
    (out_dir / "r.html").write_text(
        SHARE_PAGE_SHELL.replace("__REPORT_JS__", js), encoding="utf-8")
    return len(js)


def _write_auth_middleware(out_dir):
    """Emit middleware.js into the build, because vercel_site/ is regenerated every run.

    Anything hand-placed in that folder is destroyed by the next build, so an access
    control that lives there has to be written BY the build or it silently disappears —
    and a security gate that vanishes without anyone noticing is worse than never having
    had one.
    """
    (out_dir / "middleware.js").write_text(MIDDLEWARE_JS, encoding="utf-8")


def _bundle_research_function(app: Path, out_dir: Path) -> int:
    """Ship the Research & Scoring endpoint, or nothing at all.

    Deliberately explicit rather than a directory sweep: `requirements.txt` alone would
    make Vercel pip-install Playwright and pdfminer into the function — hundreds of MB,
    and cold start measured at 3.8s instead of 0.6s — and a sweep of the repo root would
    carry the credential CSV, the vendor CSV and the saved portal sessions with it.
    """
    src_api = app / "api"
    if not (src_api / "research.py").is_file():
        print("[i] api/research.py absent — deploying without the research endpoint")
        return 0
    # Every entry file, or no bundle. This loop used to skip a missing one quietly, so an
    # api/*.py left out of a commit (`git commit -am` does not pick up a new file) surfaced
    # only as the import probe's "cannot import itself", which points at
    # _FUNCTION_ROOT_MODULES, the wrong list, and fails the whole nightly publish. Refuse
    # here instead, before anything is copied, and name the file.
    missing = [name for name in _FUNCTION_ENTRY if not (src_api / name).is_file()]
    if missing:
        raise RuntimeError(
            ", ".join(f"api/{name}" for name in missing)
            + f" is listed in _FUNCTION_ENTRY but missing from {src_api}. Was it left out "
            "of the commit? Nothing was deployed.")
    dest_api = out_dir / "api"
    lib = dest_api / "_lib"
    lib.mkdir(parents=True, exist_ok=True)
    n = 0
    for name in _FUNCTION_ENTRY:
        shutil.copyfile(src_api / name, dest_api / name)
        n += 1
    for name in _FUNCTION_ROOT_MODULES:
        if (app / name).is_file():
            shutil.copyfile(app / name, lib / name)
            n += 1
    (lib / "sources").mkdir(exist_ok=True)
    for name in _FUNCTION_SOURCES:
        if (app / "sources" / name).is_file():
            shutil.copyfile(app / "sources" / name, lib / "sources" / name)
            n += 1
    # geo.py resolves the suburb index as PROJECT_ROOT/data/au_suburbs.csv
    (lib / "data").mkdir(exist_ok=True)
    if (app / "data" / "au_suburbs.csv").is_file():
        shutil.copyfile(app / "data" / "au_suburbs.csv", lib / "data" / "au_suburbs.csv")
        n += 1
    # document_suburb.map_path() resolves these beside its own file, which in the bundle
    # is api/_lib/. The module without its maps is the same silent "" as no module.
    for _map in ("document_suburbs.json", "document_suburbs_manual.json"):
        if (app / "data" / _map).is_file():
            shutil.copyfile(app / "data" / _map, lib / "data" / _map)
            n += 1
    # The report logo. client_report.py resolves it relative to its own file, which in
    # the bundle is api/_lib/, so the brand folder has to travel with it. Without this
    # the report generated locally carried the logo and the DEPLOYED one silently did
    # not — the function imported cleanly, so the import guard could not see it.
    if (app / "brand").is_dir():
        (lib / "brand").mkdir(exist_ok=True)
        for logo in sorted((app / "brand").iterdir()):
            if logo.is_file() and logo.suffix.lower() in (".svg", ".png", ".jpg", ".jpeg"):
                shutil.copyfile(logo, lib / "brand" / logo.name)
                n += 1
    # the builder directory with every credential column blanked
    try:
        sys.path.insert(0, str(app))
        from api._export_builders import write_public_registry
        path, rows = write_public_registry(out_dir)      # it appends api/_data itself
        print(f"[+] builder directory for the function: {rows} rows, credentials blanked")
        n += 1
    except Exception as e:
        # FATAL, not a warning. This used to delete the bundle and carry on, so a build
        # that failed here still "succeeded": the site shipped with no api/research at
        # all, every POST to it 404'd, and the Research & Scoring tab — the whole
        # client-facing pipeline — was dead on a deployment that reported success.
        # _verify_function_imports, which exists to catch exactly this, was never
        # reached because the function returned first.
        shutil.rmtree(dest_api, ignore_errors=True)
        raise RuntimeError(
            f"could not write the public builder registry ({e}); refusing to publish a "
            f"site with no research endpoint") from e
    # The links behind the Settings tab's unreadable price lists, for api/price_list.py.
    # Here, inside the bundle, and nowhere a browser can fetch as a file.
    print(f"[+] unreadable price lists that open: {_write_price_list_links(dest_api)}")
    n += 1
    for junk in dest_api.rglob("__pycache__"):
        shutil.rmtree(junk, ignore_errors=True)
    _verify_function_imports(dest_api)
    return n


_READONLY_PROBE = """
import errno, os, pathlib, sys
sys.path[:0] = [r'%s', r'%s']
_BUNDLE = os.path.abspath(r'%s')
_real_mkdir = pathlib.Path.mkdir


def _readonly_inside_bundle(self, mode=0o777, parents=False, exist_ok=False):
    if os.path.abspath(str(self)).startswith(_BUNDLE):
        raise OSError(errno.EROFS, 'Read-only file system', str(self))
    return _real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)


pathlib.Path.mkdir = _readonly_inside_bundle
try:
    import research, _candidates, _export_builders
    import benchmark_competitive, benchmark_market, comp_provider, price_kind
    import client_report
finally:
    pathlib.Path.mkdir = _real_mkdir
# The stdlib-only routes. They import nothing from the pipeline, so this proves only that
# each was copied into the bundle at all -- and a missing one is a 404 on a green deploy.
import refresh, narrative, hot_leads, contact_unlock, lead_notes, price_list
# The map is a data file, so an import cannot miss it: without it every link 404s.
assert os.path.isfile(price_list.LINKS), 'price_list_links.json missing from bundle'
# hot_leads, contact_unlock and lead_notes import this at module level, so the line above
# already fails without it; asserted by name so the failure says which file is missing.
import _contact as _ct
assert _ct.COOKIE == 'spb_contact' and callable(_ct.mask_hot_leads), 'api/_contact.py missing from bundle'
assert (not %r) or client_report._logo_html(), 'logo missing from bundle'
# THE MAP, NOT JUST THE MODULE. suburb_quality imports document_suburb inside a
# try/except, so neither a missing module nor a missing data file is an error -- both
# just answer "" forever. Assert the bundle can actually name a document's suburb.
import _regions as _rg
assert _rg.radius_for('geelong'), 'region list missing from bundle'
# Imported the same quiet way, so assert it is there rather than trusting the try/except.
import _location_resolver as _lr
assert _lr.MAX_PRIMARY_SUBURBS and callable(_lr.resolve), 'location resolver missing from bundle'
import document_suburb as _ds
assert _ds.load_map(refresh=True), 'document_suburbs.json missing from bundle'
assert _ds.manual_rules(refresh=True), 'document_suburbs_manual.json missing from bundle'
print('imports-ok')
"""


def _verify_function_imports(dest_api: Path) -> None:
    """Import the built endpoint the way Vercel will, in a clean interpreter.

    A module added to api/ but not to _FUNCTION_ROOT_MODULES copies nothing, imports
    fine locally — the app directory is on sys.path here — and then raises ImportError
    inside the function, 500ing every request. That shipped once: address_label.py was
    imported by _candidates.py and never bundled, so the whole research endpoint was
    dead while the page, the tests and the deploy all reported success.

    Raising here means a bundle that cannot import never reaches the alias.
    """
    # Also assert the report can still find its logo INSIDE the bundle. A missing data
    # file is invisible to an import check — the deployed report simply came out
    # unbranded while the local one looked perfect.
    want_logo = (Path(__file__).parent / "brand").is_dir() and any(
        p.suffix.lower() in (".svg", ".png", ".jpg", ".jpeg")
        for p in (Path(__file__).parent / "brand").iterdir() if p.is_file())
    # ...and import it the way Vercel will: on a READ-ONLY bundle.
    #
    # /var/task is read-only there, and config.py runs OUTPUT_DIR.mkdir() at module
    # level. Any module that reaches config before api/_bootstrap has wrapped that one
    # import dies with EROFS and takes the whole endpoint down — HTTP 500 on every
    # search. Locally the same import succeeds, because here the directory can simply be
    # created, so the probe passed while the deployment was dead. That shipped: a
    # module-level `import geo` inside suburb_quality.py, one line, green build, 500s
    # live. _candidates.py imports geo lazily for precisely this reason.
    #
    # mkdir is denied only INSIDE the bundle. _bootstrap's own redirect to /tmp is meant
    # to work and must keep working, so anything outside is left alone.
    probe = _READONLY_PROBE % (dest_api, dest_api / "_lib", dest_api, want_logo)
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                          cwd=str(dest_api))
    if done.returncode != 0 or "imports-ok" not in done.stdout:
        raise RuntimeError(
            "the built function cannot import itself — nothing was deployed.\n"
            "Add the missing module to _FUNCTION_ROOT_MODULES (or _FUNCTION_SOURCES).\n"
            + (done.stderr or done.stdout).strip()[-900:])
    print("[+] function bundle imports cleanly")


if __name__ == "__main__":
    where = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv \
        else Path(__file__).with_name("vercel_site")
    m = build(where, with_assets="--no-assets" not in sys.argv)
    print(f"[+] {where}  built from index.html (the app's own frontend)")
    print(f"    {m['total']:,} listings · {m['builders']} builders/developments · "
          f"{m['named']:,} named · {m['with_state']:,} with a state")
    print(f"    {m['registry']} registry builders (credentials excluded) · "
          f"{m['assets']} assets, {m['copied']} PDF(s) bundled so they actually open")
    print(f"    Benchmarks: {m['benchmarks']:,} market comparable(s) stored · "
          f"{m['rea_linked']:,} listing(s) carry a realestate.com.au compare link · "
          f"{m['rea_suburbs']} suburb(s) on the Benchmarks tab")
    if m.get("lot_pages_checked"):
        print(f"    Lot pages checked {m['lot_pages_checked']}: {m['lot_pages_gone']:,} current "
              "listing(s) whose own page is gone -- marked, and left out of every search")
        # AN EARLIER NIGHT'S VERDICTS ARE STILL APPLIED -- dropping them would put the dead
        # links back -- but a build using them says so (review, 26 Sep: state_sync carries
        # .sessions/*.json between runs, and deploy.ps1 builds without running the check).
        from link_check import _today_melbourne
        if m["lot_pages_checked"] != _today_melbourne():
            print(f"    WARNING: those lot-page verdicts are from {m['lot_pages_checked']}; "
                  "tonight's link_check.py did not run")
    else:
        print("    Lot pages NOT checked (no .sessions/link_status.json): links shown as "
              "stored, none left out")
    if m.get("relabelled"):
        print(f"    {m['relabelled']} row(s) re-labelled to one name per builder "
              f"(the database keeps its spellings — see _display_name_canonicaliser)")
    _report_price_evidence_failures(m.get("price_evidence_failures"))
    _cov = m.get("suburb_coverage") or {}
    _assert_suburb_coverage(_cov)
    if _cov:
        print("    Suburbs: %(findable)s of %(australian)s Australian listing(s) findable "
              "(%(pct).2f%%) · %(located)s located, the rest inferred and badged"
              % {"findable": f"{_cov['findable']:,}", "australian": f"{_cov['australian']:,}",
                 "pct": _cov["share"] * 100, "located": f"{_cov['located']:,}"})
    _showing_sold = _assert_no_sold_stock_on_show(where / "stock.json")
    print("    Sold stock on show: %d listing(s) the filter shows read as sold to a "
          "person%s" % (_showing_sold, "" if _showing_sold else " — none"))
    # EVERY PUBLISHED FILE, not just the big one. This guard was written for stock.json
    # and only ever read stock.json, so channels.json went on publishing 56 Dropbox
    # folder capability URLs -- the exact thing it exists to stop -- for as long as the
    # unreadable-stocklists panel had existed. A guard that checks one of five files
    # reads, from its passing, as though it checked all five. So it reads every static
    # file (24 Oct 2026): a named list had already missed suburbs.json.
    for _published in _static_files(where):
        _assert_no_folder_capability_urls(_published)
    _assert_a_benchmark_names_its_place(where / "stock.json")
    _assert_every_benchmark_listing_opens_somewhere(where / "benchmarks.json")
    # benchmarks.json is in this list for the same reason `notes` was the leak that
    # created the check: a listing payload sourced from an agency is the most likely
    # place a direct phone number has ever reached this project's free text.
    for f in ("stock.json", "builders.json", "vendor-assets.json", "benchmarks.json"):
        _assert_no_contact_details(where / f)
        print(f"    {f:<20} {(where / f).stat().st_size:>10,} bytes")
    print("    X-Robots-Tag: noindex set (a Vercel URL is public by default)")
