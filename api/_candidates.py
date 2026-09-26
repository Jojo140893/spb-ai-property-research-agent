"""
Turn the deployed stock snapshot into candidate_packages for the existing pipeline.

The function must not scrape, so it feeds run_property_research() the stock that is
ALREADY in the database instead. Two readers, same output shape:

  * stock.json  — the columnar export build_web.py already writes into vercel_site/.
                  This is the default: it is field-allow-listed, contains no
                  credentials and no vendor PII, and is already public on the
                  deployment, so scoring it adds no new disclosure surface.
  * SQLite      — opened read-only (`file:...?mode=ro&immutable=1`), for local runs
                  or if a purpose-built snapshot DB is deliberately deployed.

THE RULE THIS FILE EXISTS TO ENFORCE: never guess a fact.

run_property_research fills missing package fields with plausible-looking defaults —
`bedrooms=4`, `land 400 m²`, `house 180 m²`, rent `$550-600/wk`, title "Expected Q4
2026", "Close to schools, train station & town centre". Those are fine for a live
scrape that captured the real numbers and merely omitted one. They are NOT fine
here: 3,543 of 4,308 stored rows have no bedroom count, and a fabricated 4 would sail
straight through a "minimum 4 bedrooms" brief and reach a buyer as a recommendation.

So every field that a mandatory filter reads must be PRESENT IN THE ROW or the row is
not scored at all — it is reported, with the missing field named, in the coverage
summary. Every descriptive field the client report prints is passed as an explicit
blank rather than left to the default. A blank is visibly a blank.
"""

import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:                       # api/ on sys.path: sibling imports
    sys.path.insert(0, _HERE)

from _bootstrap import HERE, ROOT
import suburb_quality
from address_label import clean_display_address, display_suburb
from provenance import primary_source_link

# Exactly the fields build_web.py exports to stock.json, so both readers agree and
# neither can reach an internal column (content_hash, dedup_key, source_text).
SNAPSHOT_FIELDS = [
    # THE ONE IDENTIFIER A SELECTION CAN BE KEYED ON. Everything else about a listing
    # moves: price is repriced, availability flips, the address is re-parsed, and the
    # shortlist's own property_id is "PROP-1, PROP-2, ..." assigned per search
    # (kommo_agent.py), so PROP-1 in one search and PROP-1 in the next are DIFFERENT
    # buildings. Coleen's workflow is to filter, mark, change the filter, mark again, then
    # compare across both — which silently compares the wrong properties on any per-run id.
    # content_hash survives a re-harvest, which is exactly why the Building Stock tab has
    # keyed its marked-listings set on it since 30 Jul. Selected here so the SQLite reader
    # returns it too; stock.json has always carried it.
    "content_hash",
    "builder_name", "lot_address", "street_address", "lot_number",
    # suburb_source travels with the blank so the coverage sentence can still separate
    # "the source never gave one" from "the source gave something that is not a place".
    "suburb", "suburb_source", "state", "availability_status",
    "state_source", "price", "land_price", "build_price", "bedrooms", "bathrooms",
    "car_spaces", "land_sqm", "house_sqm", "storey", "title_status", "estate_name",
    "incentive_amount", "incentive_text", "product_type", "source_channel",
    "attribution_scope", "date_checked", "listing_url", "floorplan_url",
    "brochure_url", "source_project_id",
    # The link is PRECOMPUTED at build time and shipped, so the snapshot no longer has to
    # carry stocklist_file / source_url -- both are capability URLs and this deployment
    # is public. The SQLite reader still has the raw columns and recomputes.
    "source_link_url", "source_link_label", "source_link_opens", "source_link_search",
    # "Compare on realestate.com.au" — same derived-at-build-time shape as
    # source_link_*, built by rea_link.py from the RESOLVED suburb. Listed here because
    # this list claims above to mirror stock.json exactly; leaving it stale is precisely
    # the silent divergence between the JSON reader and the SQLite reader that the
    # comment exists to prevent.
    "rea_search_url", "rea_search_label", "rea_search_opens", "rea_search_search",
    "benchmark_median", "benchmark_variance_pct",
    "benchmark_classification", "benchmark_basis",
    # Required, not optional: build_packages skips superseded captures, and without
    # this column the SQLite reader would hand over rows with the flag missing and
    # silently score stale prices while the JSON reader filtered them.
    "superseded_by",
    # WHICH DOCUMENT A ROW CAME FROM, without publishing the document's URL.
    #
    # Rows on one price list are each other's best evidence: a lot with no locality is
    # nearly always sitting next to lots that have one. The grouping key for that was
    # source_url — which is NOT exported, deliberately, because a Dropbox folder link is
    # a capability and this deployment is public. So the page-sibling suburb map and the
    # state borrow were both permanently EMPTY on the deployed path while working
    # perfectly against the local database, which is the worst possible split: measured
    # correct in development, silently absent in production.
    #
    # A short hash of the URL groups the rows identically and discloses nothing.
    "source_doc",
    # WHAT THE ROW'S OWN TEXT SAYS ABOUT ITS PRICE (build_web PRICE_EVIDENCE_FIELDS). The
    # deployed snapshot carries no source_text, so "does this price list print this price,
    # and is it the whole price?" is answered at build time. The exceptions-only admission
    # of unstated rows into Needs confirming reads all three -- see admission_refusal.
    "price_kind", "text_price_max", "price_in_text",
    # AN EMAILED LIST'S OWN IDENTITY AND AGE, for 'digital email' rows only (null on
    # every other row). date_checked on an emailed row is the last night the message sat
    # in the 7-day inbox sweep, not the list's date: the Shape Homes list dated 13 Aug
    # reads "checked 20/08". email_first_seen is the first night it was read, which is
    # what the 45-day confirm limit ages an email from; email_list is a short hash of the
    # subject with dates and Fwd: removed ("shape home stock list"), so a newer list from
    # the same sender can be recognised without publishing the subject line.
    "email_list", "email_first_seen",
    # A LOT'S LINE WITH ITS PRICES AND STATUS WORDS TAKEN OUT, hashed (snapshot_line_cells).
    # Two captures of one lot at different prices are the same listing repriced when their
    # lines agree, and two designs when they do not; the same line of one document read
    # later with a status saying gone is the lot gone -- see _newer_capture. The text
    # itself is not published.
    "line_key",
    # THE DATE THE LOT'S OWN PAGE ANSWERED 404/410 (link_check.py via build_web), else "".
    # The gate refuses such a row as lot_page_gone.
    "lot_page_gone",
    # Why: "gone" (404/410) or the status word the builder's own page shows ("Under offer").
    "lot_page_status",
]

# Build-time cells a snapshot DATABASE may predate and nothing in it can recompute: the
# SQLite reader gives them their "not known" value instead of failing the read. A snapshot
# built before link_check.py existed has no lot_page_gone, which means no page was found
# gone -- exactly how the search behaved before the check.
_OPTIONAL_SNAPSHOT_FIELDS = {"lot_page_gone": "", "lot_page_status": ""}

# Build-time cells a purpose-built snapshot DATABASE may predate. The SQLite reader
# computes them from the columns they derive from, exactly as build_web does, instead of
# failing the read over a column the snapshot could not have had.
_COMPUTED_SNAPSHOT_FIELDS = ("price_kind", "text_price_max", "price_in_text",
                             "email_list", "email_first_seen", "line_key")
# What those computations read. Selected when the table has them, then dropped before a
# row is returned: source_text on a row changes what suburb_quality resolves, and the
# JSON reader's rows never carry it.
_COMPUTE_INPUTS = ("source_text", "source_url", "first_seen")

# The field rows are grouped by when asking "what do this document's other rows say?".
_DOC_KEY = "source_doc"

# Not purchasable, so not a candidate.
#
# "not available" was missing from this set and it is the literal value on 581 rows —
# so a sold Rouse Hill lot was RECOMMENDED to Colin on 5 Aug. He put it plainly: "it's
# not available, but it came as available from your end... you should only see what's
# available and nothing else."
NOT_AVAILABLE = {"sold", "under offer", "reserved", "leased", "on hold", "withdrawn",
                 "not available", "unavailable", "not avail", "no longer available",
                 "off market", "settled", "deposit taken", "contract pending"}

# Values that positively assert the lot can be bought today. A recommendation now needs
# one of these rather than merely failing to be on the excluded list.
#
# 1,462 live rows (22%) record no availability at all, and "unstated" is not "available":
# recommending one risks putting a sold house in front of a buyer, which is the outcome
# Colin was objecting to. Excluding them leaves 4,135 priced candidates (VIC 1,970,
# NSW 795, QLD 667), so this costs coverage we can afford. They stay visible in the
# Building Stock table and the count is reported in the coverage line — hidden from
# recommendations, not hidden from view.
IS_AVAILABLE = {"available", "for sale", "in stock", "released", "selling",
                "titled", "now selling", "open"}

# THE ROW, NOT THE CHANNEL. This was `PRICES_UNVERIFIED_CHANNELS = {"proxima"}` — every
# Proxima row excluded from every recommendation, because on 6 Aug the harvest had stored
# the LAND component as the package price (Lot 2, 275 Twelfth Ave Austral published at
# $675,000 against a real package of $1,127,000).
#
# sources/proxima.py was fixed the same day, and the note left here said the block was "a
# one-word change once a harvest confirms the prices". The harvest confirmed them and
# nobody came back to it. Measured 12 Aug: that same Austral lot now reads $1,127,000 with
# land_price $675,000 stored separately, 1,294 of 1,390 live Proxima rows were re-read
# today, and ZERO still show the bug's signature.
#
# Meanwhile the blanket block was hiding 1,084 rows from every NSW search — which is the
# bug Colin and Sel hit on 12 Aug: Bathla units in Box Hill at $719,990-$729,990, sitting
# in the database, "For Sale", and invisible to a $900k Box Hill search.
#
# So the test is now per row, and it is the bug's own fingerprint: a stored price that IS
# the land figure. Plus anything captured before the fix landed, which cannot be vouched
# for either way.
# The day AFTER the fix, not the day of it. sources/proxima.py was corrected on 6 Aug, so
# a row captured at some point during the 6th cannot be attributed to before or after the
# change — and the pre-fix shape stored the land figure in `price` with NO land_price
# column, so the fingerprint check above cannot catch it either. Anything captured on or
# before the 6th is therefore treated as unverified. That is 15 live rows.
PROXIMA_PRICE_FIX_DATE = datetime(2026, 8, 7)


# The five types Proxima's own filter offers, which Colin read out on 17 Aug as the list
# ours should match: "apartment, land, townhouse, villas, house and land". Commercial is
# kept because 40 stored rows say it.
#
# WHY THIS IS SIX AND PROXIMA'S DROPDOWN IS TWELVE. Asked on 18 Aug 2026 and measured
# rather than assumed. Proxima offers Apartment, Land Only, Townhouse, Villa, House and
# Land, Townhouse (Single Contract), Flat, Unit, Granny Flat, Commercial, House and
# Granny and Co-Living. Six of those are ours exactly; Flat and Unit are our Apartment;
# Townhouse (Single Contract) is a contract term, not a building.
#
# The remaining three cannot be filled, for two separate reasons:
#
#  * PROXIMA DOES NOT PUBLISH A PER-LOT TYPE. It is in neither the accordion's data-*
#    attributes nor the availability API's property records, and the project header does
#    not carry one either. It exists only as `property[property_types]`, a server-side
#    search facet — and that facet is nearly empty in their own data. Filtering DAMAC
#    Islands Stage 2 returned 10 of its 129 lots as "Apartment" and nothing for the other
#    eleven types; Ahlei, 110 real apartments, returned NOTHING for all twelve.
#
#  * OUR CATALOGUE HAS ALMOST NONE OF THEM. Granny Flat is 3 live rows and House and
#    Granny is 0. Co-Living is 12.
#
# Dual Key (86 rows) and Duplex (44) were considered as additions and rejected: 77 of the
# dual-key rows and 16 of the duplex rows already carry a STATED "House & Land", which is
# what they are — a package with two dwellings on it. Promoting them to types would filter
# 8 of 86 dual-key rows under "Dual Key" and leave the other 77 under House & Land, which
# is the "same options regardless of what you write" failure of 17 Aug in a new shape.
PRODUCT_TYPES = ("House & Land", "Apartment", "Townhouse", "Villa", "Land", "Commercial")

# A LOT NUMBER IS NOT A HOUSE. Colin, 17 Aug, reading a result aloud: "that's a lot unit
# number ... the lot is a unit, it's not a house and land, it's not doing the right thing."
# Stocklists write apartments as "Lot 31 Unit 31, 148-154 Bridge Street", so the word "Lot"
# says nothing about the product and "Unit" says everything. Checked in this order for that
# reason: the unit/townhouse/villa words WIN over the presence of a land size, because an
# apartment row can carry a land figure (its share of the site) and 1,319 of them do.
_UNIT_WORDS = re.compile(r"\bunit\b|\bapt\b|\bapartment\b|\blevel\s*\d|\blvl\s*\d|\bdual\s*key\b", re.I)
# Words the stocklists use for a HOUSE that the first pass never read, so 20 of the 22
# type-unknown rows in a scored pool were describing themselves and being ignored:
#   "1054 Available Detached Wildflower Circuit Regent 4-2-1"
#   "836 Available Medium Density Rotunno Drive Poinciana"
_HOUSE_WORDS = re.compile(r"\bdetached\b|\bmedium\s*density\b|\bduplex\b|"
                          r"\bdual\s*occ(upancy)?\b|\bhouse\s*&?\s*land\b", re.I)
_TOWNHOUSE_WORDS = re.compile(r"\btown\s*house(s)?\b|\bth\b", re.I)
_VILLA_WORDS = re.compile(r"\bvilla(s)?\b", re.I)


# "Lot 103 Unit 103, 3 Turffontein Avenue" — the LOT NUMBER REPEATED after the word Unit.
# That is a stocklist column artefact, not a product: the sibling stated row for the same
# lot says "House & Land" or "Land", and the lot carries 301 m2 of dirt. 619 Proxima rows
# are shaped like this, and treating the word "Unit" as decisive classified 360 house lots
# as apartments — so a search for Apartment returned 8 greenfield packages and 0 real
# apartments, while House & Land silently LOST those same 8. Found by the 17 Aug filter
# audit, which is exactly the failure the earlier fix was meant to end.
_LOT_UNIT_REPEAT = re.compile(r"\blot\s+(\S+).{0,40}?\bunit\s+\1\b", re.I)

# A dwelling with real land is not an apartment, whatever the text says. Real apartments in
# this catalogue carry NO land figure at all; the artefact rows carry 260-720 m2. Set above
# a townhouse/villa footprint so those still classify on their own words.
_HOUSE_LOT_SQM = 200.0


def _UNIT_IS_AN_ARTEFACT(text, row) -> bool:
    if _LOT_UNIT_REPEAT.search(text):
        return True
    return (_num(row.get("land_sqm")) or 0) >= _HOUSE_LOT_SQM


def product_type_of(row):
    """The row's product type — stated if the source stated one, inferred if not.

    WHY INFER AT ALL. 1,853 of 6,127 live rows record no product_type, and the filter
    passed anything unstated (deliberately: rejecting them discarded a third of the
    catalogue). The result was that every choice returned the same rows — measured on
    17 Aug, "House & Land" returned 55 of which 53 were blank, "Townhouse" returned 50 of
    which 50 were blank, "Apartment" the same 50. Colin: "it's still giving us the same
    options regardless of what you write."

    Inference settles 92% of the blanks from the row's own words and numbers. It never
    overrides a source that stated a type, and it returns "" rather than guessing when the
    evidence is thin — 8% stay unknown, and those are still passed by the filter, because
    the old asymmetry was right about the cost of inventing a requirement.
    """
    stated = str(row.get("product_type") or "").strip()
    if stated:
        return stated
    text = " ".join(str(row.get(k) or "") for k in
                    ("lot_address", "street_address", "estate_name"))
    if _VILLA_WORDS.search(text):
        return "Villa"
    if _TOWNHOUSE_WORDS.search(text):
        return "Townhouse"
    if _UNIT_WORDS.search(text) and not _UNIT_IS_AN_ARTEFACT(text, row):
        return "Apartment"
    if _HOUSE_WORDS.search(text):
        return "House & Land"
    # Land with a build price or a dwelling size is a package; land alone is land. Neither
    # is asserted without a land figure, which is the one number that separates them.
    land = _num(row.get("land_sqm")) or 0
    if land > 0:
        if (_num(row.get("build_price")) or 0) > 0 or (_num(row.get("house_sqm")) or 0) > 0:
            return "House & Land"
        return "House & Land" if re.search(r"\blot\b", text, re.I) else "Land"
    return ""


def _price_unverified(row):
    """True when this row's price cannot be trusted — not "came from Proxima"."""
    if str(row.get("source_channel") or "").strip().lower() != "proxima":
        return False
    price = _num(row.get("price"))
    land = _num(row.get("land_price"))
    # The signature: the package price and the land price are the same number.
    if price and land and abs(price - land) < 1:
        return True
    # _fresh already parses this module's two date formats and returns the datetime;
    # reusing it means one place understands "12/08/2026" rather than two that can drift.
    _verified, seen, _raw = _fresh(row)
    return bool(seen and seen < PROXIMA_PRICE_FIX_DATE)

# NO HEADROOM. This was $50,000, copied from the live sources (sources/e_agent.py:524,
# sources/builder_portals.py:204), and it bought nothing: scoring_engine hard-rejects
# anything advertised above budget_max, so every row in the band took a scoring slot to
# come back as a red "exceeds max budget" card. Colin's QLD <= $750k search scored 16 and
# spent 8 of them that way ($752,400 to $799,995). A client who says $750,000 has said it;
# "what would $50k more buy" is a relaxation the explain panel prices honestly, not a
# silent widening.
BUDGET_HEADROOM = 0.0
# Rows priced within this much OVER the budget are still counted (counts['just_over']),
# so the headroom removed above stays visible as a number. Informational, never a gate.
JUST_OVER_BAND = 50_000.0

# A stored row was verified by a live scrape on its date_checked. That verification
# goes stale: past this many days the row is passed through as unverified, which the
# pipeline turns into "Pending Confirmation" and keeps out of the final list.
FRESH_DAYS = int(os.environ.get("SPB_SNAPSHOT_FRESH_DAYS", "14"))

# HOW OLD A ROW MAY BE AND STILL BE SHOWN TO CONFIRM. Between FRESH_DAYS and this, an
# unverified row carries confirm=['stale'] and may appear in "Needs confirming" when no
# sold-stock guard hides it (confirm_guard). Older, it is hidden as over_confirm_limit.
# 45, not 30: the only real target, Shape Homes' Dawn lots, crosses 30 days on 19 Sep, and
# with the guards on, the extra days added nothing wrong to Colin's search. FRESH_DAYS is
# NOT widened: at 30 or 45 days the VIC On-Hold Windermere rows become "Available" QLD.
CONFIRM_MAX_DAYS = int(os.environ.get("SPB_CONFIRM_MAX_DAYS", "45"))

# DOES A BLANK LOT ON AN EXCEPTIONS-ONLY LIST COUNT AS AVAILABLE?
#
# Some builders mark only the lots that have GONE and leave the rest blank. Silkwood's
# QLD sheet is the standing example: 245 blank rows and 5 On Hold. Until 20 Sep such a
# blank was admitted to "Needs confirming" and never to the shortlist, on the view that
# the source had not said the word "available".
#
# Measured against the live E-Agent portal on 20 Sep 2026, that cost Colin almost
# everything he has: of 42 Silkwood lots published on E-Agent that day, 42 were in the
# catalogue and 2 reached the shortlist. The other 40 were real, current, in-budget lots
# sitting behind a collapsed panel, and 36 of them were blank purely because the sheet
# marks exceptions only.
#
#   "available"  a blank row on an exceptions-only list, READ WITHIN FRESH_DAYS and
#                past every admission_refusal check, is shown as a result and carries
#                "Confirm availability: this list marks only sold lots".
#   "confirm"    the behaviour before 20 Sep: shown only under "Needs confirming".
#
# FRESH_DAYS still applies: a stale blank row goes to "Needs confirming" exactly as
# before, so this widens what is SHOWN, never how old a row may be. admission_refusal is
# unchanged and still rejects build-only prices, land-only prices, commercial tenancies,
# price components of a larger printed total, and anything its own text marks as gone.
#
# This answers the open client question "do exceptions-only price lists count as
# available" in the affirmative. Roll back with:  SPB_EXCEPTIONS_LIST_MODE=confirm
#
# THREE DEFECTS WERE FOUND BY REPLAYING THIS OVER THE LIVE SNAPSHOT BEFORE IT SHIPPED, and
# all three are fixed above. Two are in promotion_refusal (gone_at_any_date, line_truncated)
# and one was in the fall-through when promotion is refused, which admitted rows into the
# confirmed pool that no caller had asked for and cost 42 verified lots on a no-ceiling
# brief -- see the comment at that return. Measured on the live snapshot, both modes, five
# brief shapes: not one row that was shown before is hidden after, nothing falls out of
# both pools, and no verified lot is evicted by the cap.
#
#   Rollback:  SPB_EXCEPTIONS_LIST_MODE=confirm  (restores the behaviour before 20 Sep)
#
EXCEPTIONS_LIST_MODE = os.environ.get("SPB_EXCEPTIONS_LIST_MODE", "available").strip().lower()

# THE DAY IS BRISBANE'S. date_checked is written in AEST by the nightly runner (checked_at
# 11:41 on a run that started 01:07Z), and Vercel's clock is UTC, so between midnight and
# 10am AEST a search aged every row one day younger than it is. Queensland keeps no
# daylight saving, and neither does the runner's date.
AEST = timezone(timedelta(hours=10))


def _utc_now():
    """The clock, in one place a test can stop."""
    return datetime.now(timezone.utc)


def _now_aest(utc_now=None):
    """Now on the AEST wall clock, naive, so it subtracts from a parsed date_checked.

    A naive `utc_now` is taken to be UTC, as datetime.utcnow() returns it.
    """
    now = utc_now or _utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(AEST).replace(tzinfo=None)

# Upper bound on rows handed to the pipeline, so one enormous state cannot blow the
# function's time limit. Truncation is reported, never silent.
MAX_CANDIDATES = int(os.environ.get("SPB_MAX_CANDIDATES", "600"))

# Facts a mandatory filter reads. Missing -> the row is not scored.
# (bedrooms_min, bathrooms_min, car_spaces_min, house_size_min_sqm in ScoringEngine.)
REQUIRED_FACTS = (
    ("bedrooms", "bedroom count"),
    ("bathrooms", "bathroom count"),
    ("car_spaces", "car space count"),
    ("house_sqm", "house size"),
)

# Which of the client's own minimums each fact is checked against. A fact is only
# REQUIRED when the brief states the minimum that needs it — see _binding_facts.
BRIEF_MINIMUM_FOR = {
    "bedrooms": "bedrooms_min",
    "bathrooms": "bathrooms_min",
    "car_spaces": "car_spaces_min",
    "house_sqm": "house_size_min_sqm",
}

# And what each fact is CALLED on a built package, which is not always what the fact is
# called. REQUIRED_FACTS says "house_sqm"; a package stores the figure as
# "house_size_sqm". _meets_binding read the fact name straight off the package, found
# nothing, and scored every row 0 on house size -- so a row meeting the client's minimum
# sorted identically to one failing it and the cap collapsed back to the 600 cheapest,
# which is the exact defect that ordering exists to prevent. It failed silently: no
# exception, no counter, and every test fixture set only bedrooms_min, so all six passed.
PACKAGE_KEY_FOR = {
    "bedrooms": "bedrooms",
    "bathrooms": "bathrooms",
    "car_spaces": "car_spaces",
    "house_sqm": "house_size_sqm",
}


def _source_link(row, suburb):
    """The "where this came from" link, preferring the one the build already computed.

    stock.json no longer carries stocklist_file or source_url -- both are capability
    URLs (a Dropbox rlkey grants read access to anyone holding the link, no sign-in) and
    the deployment is public, so recomputing the link here would mean shipping the very
    thing that was removed. build_web writes source_link_* into the snapshot instead.

    The SQLite reader still has the raw columns, so it recomputes as before.
    """
    if row.get("source_link_url"):
        return {"url": row.get("source_link_url") or "",
                "label": row.get("source_link_label") or "",
                "opens": row.get("source_link_opens") or "",
                "search": row.get("source_link_search") or ""}
    return primary_source_link({**row, "suburb": suburb})


# --------------------------------------------------------------------------- readers

def _snapshot_paths():
    json_env = os.environ.get("SPB_SNAPSHOT_JSON")
    db_env = os.environ.get("SPB_SNAPSHOT_DB")
    candidates = []
    if json_env:
        candidates.append(("json", Path(json_env)))
    if db_env:
        candidates.append(("db", Path(db_env)))
    if not candidates:
        candidates = [
            ("json", ROOT / "stock.json"),                  # deployed layout
            ("json", HERE / "_data" / "stock.json"),         # deployed, not public
            ("json", ROOT / "vercel_site" / "stock.json"),   # local repo layout
        ]
    return candidates


def load_snapshot():
    """[{field: value}] for every stored listing. Raises if no snapshot is deployed."""
    tried = []
    for kind, path in _snapshot_paths():
        tried.append(str(path))
        if not path.exists():
            continue
        return (_read_json(path) if kind == "json" else _read_sqlite(path)), str(path)
    raise FileNotFoundError(
        "no stock snapshot found (looked at: %s). Deploy stock.json alongside the "
        "function, or set SPB_SNAPSHOT_JSON / SPB_SNAPSHOT_DB." % ", ".join(tried))


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # build_web.py writes a columnar payload; index.html also accepts a row-of-objects
    # shape from the live API, so accept both here too.
    if isinstance(data.get("rows"), list) and isinstance(data.get("keys"), list):
        keys = data["keys"]
        return [dict(zip(keys, row)) for row in data["rows"]]
    return list(data.get("buildings") or [])


def _read_sqlite(path):
    # immutable=1: a deployed snapshot has no writers, and it stops SQLite wanting to
    # create -wal/-shm files next to a read-only file.
    uri = "file:%s?mode=ro&immutable=1" % path.as_posix()
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        have = {r[1] for r in conn.execute("PRAGMA table_info(buildings)")}
        # Price evidence and the emailed list's identity are build-time cells. A snapshot
        # database written before they existed still reads; they are computed below from
        # the columns build_web computes them from.
        computed = [f for f in _COMPUTED_SNAPSHOT_FIELDS if f not in have]
        absent = {f: v for f, v in _OPTIONAL_SNAPSHOT_FIELDS.items() if f not in have}
        cols = [f for f in SNAPSHOT_FIELDS if f not in computed and f not in absent]
        inputs = ([f for f in _COMPUTE_INPUTS if f in have and f not in cols]
                  if computed else [])
        try:
            rows = conn.execute(
                "SELECT %s FROM buildings" % ", ".join(cols + inputs)).fetchall()
        except sqlite3.OperationalError as exc:
            # The harvest database is NOT a snapshot: source_doc, source_link_*, rea_* and
            # suburb_source are written by build_web, so this read fails on it. Say so,
            # instead of leaving "no such column" for the next tool author to decode.
            raise sqlite3.OperationalError(
                "%s -- %s is not a snapshot database. Build one with "
                "build_web.build(out, with_assets=False) on a scratch copy and read its "
                "stock.json; join database-only fields with join_db_fields()."
                % (exc, path)) from exc
        out = [dict(r) for r in rows]
    finally:
        conn.close()
    for row in out:
        if computed:
            cells = snapshot_build_cells(row)
            row.update({f: cells[f] for f in computed})
        for f in inputs:
            row.pop(f, None)
        row.update(absent)
    return out


def _is_email(row):
    return str(row.get("source_channel") or "").strip().lower() == EMAIL_CHANNEL


def snapshot_build_cells(row):
    """{price_kind, text_price_max, price_in_text, email_list, email_first_seen, line_key}
    for a row that still has its database columns (source_text, source_url, first_seen).

    build_web publishes these on every stock.json row and the SQLite reader computes them
    for a snapshot database that predates them -- one function, so the two readers cannot
    disagree about a row. Never raises: a row whose evidence cannot be computed gets
    nulls, which the admission rule reads as "no evidence" and refuses.
    """
    cells = dict.fromkeys(("price_kind", "text_price_max", "price_in_text"))
    try:
        import price_kind
        evidence = price_kind.price_evidence(row)
        cells.update({f: evidence[f] for f in cells})
    except Exception:                                                   # noqa: BLE001
        pass
    cells.update(snapshot_email_cells(row))
    cells.update(snapshot_line_cells(row))
    return cells


# ------------------------------------------------------------------ one line, repriced
#
# A LOT'S LINE WITH ITS PRICES TAKEN OUT, for a deployed search that never sees the text.
# Hattan lot 406, Mambourin Green was read from an emailed list on 17/08 at $588,950 and
# from E-Agent on 16/09 at $604,776 -- the same line, "Available Mambourin Q3 2026
# Townhouse Lot 406 156 6 Westgarth F | Mambourin Green 16.8 Double Storey 4 2 2", apart
# from its house and total prices. The 17/08 capture was offered to confirm, in budget,
# at a price the builder no longer asks. Two captures of one lot at prices more than 2%
# apart were deliberately NOT treated as one listing (critique #8): the same lot can carry
# two house designs. The line tells them apart -- "Enzo 10.5" against "VGU19" on Lumina lot
# 58 -- where lot, land price and bed/bath/car cannot.
#
# Taken out before hashing: links, money ($ amounts, and thousands-grouped or 6+ digit
# bare numbers, which is how price lists print totals), dates, percentages (a yield moves
# with the price), unit suffixes, and status words (Available / On Hold is the status
# pass's business, not a new design). A line with little left says nothing about a design
# and gets no key.
_LINE_LINK = re.compile(r"(?:[a-z][a-z0-9+.-]*:/+|www\.)\S*|\b[\w-]+(?:\.[\w-]+)+/\S*", re.I)
_LINE_MONEY = re.compile(
    r"[A-Za-z]?\$\s?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[kKmM](?![A-Za-z0-9]))?")
_LINE_GROUPED = re.compile(r"(?<![\d.])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d])")
_LINE_BARE_BIG = re.compile(r"(?<![\w.])\d{6,}(?:\.\d+)?(?![\w])")
_LINE_DATE = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b"
                        r"|\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b")
_LINE_PERCENT = re.compile(r"\d+(?:\.\d+)?\s?%")
_LINE_UNITS = re.compile(r"(?<=\d)\s*(?:m2|m²|sqm|sq\.?m)\b", re.I)
_LINE_STATUS = re.compile(
    r"\b(?:no\s*longer\s*available|not\s*available|not\s*avail|unavailable|available|"
    r"on\s*hold|hold|sold\s*out|sold|under\s*offer|under\s*contract|reserved|leased|"
    r"withdrawn|eoi|tbc|deposit\s*taken|settled|contract\s*pending|off\s*market)\b", re.I)
_LINE_MIN_CHARS = 12


def line_text_key(text):
    """A short hash of a source line with prices, dates and status words removed, or None
    when too little is left to tell one design from another."""
    t = str(text or "")
    if not t.strip():
        return None
    t = _LINE_LINK.sub(" ", t)
    for pattern in (_LINE_MONEY, _LINE_GROUPED, _LINE_DATE, _LINE_PERCENT, _LINE_BARE_BIG):
        t = pattern.sub(" ", t)
    t = _LINE_UNITS.sub(" ", t)
    t = _LINE_STATUS.sub(" ", t)
    t = " ".join(_NON_ALNUM.sub(" ", t.lower()).split())
    if len(t) < _LINE_MIN_CHARS:
        return None
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:12]


def snapshot_line_cells(row):
    """{line_key} from a row's source_text; null when the text leaves too little to compare.
    On every row, lot number or not: the same line on the same document read later is
    evidence about a lot the line names without a lot column (Howard lot 21)."""
    try:
        return {"line_key": line_text_key(row.get("source_text"))}
    except Exception:                                                   # noqa: BLE001
        return {"line_key": None}


def snapshot_email_cells(row):
    """{email_list, email_first_seen} from a row's source_url and first_seen columns.

    Both null unless the row came by email (source_channel 'digital email'): no other
    channel's guard or card reads them, and a null cell costs the snapshot least.
    """
    if not _is_email(row):
        return {"email_list": None, "email_first_seen": None}
    return {"email_list": email_list_key(row.get("source_url")) or None,
            "email_first_seen": str(row.get("first_seen") or "").strip() or None}


# --------------------------------------------------------------------- emailed lists

# The subject of an emailed list, minus what changes from one issue to the next.
_SUBJECT_PREFIX = re.compile(r"^\s*email\s*:\s*", re.I)
# Forwards only. "RE:" stays in the stem: a reply about an old list is not a new list.
_SUBJECT_FORWARD = re.compile(r"^\s*(?:fwd?|fw)\s*:\s*", re.I)
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
          r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_SUBJECT_DATES = (
    re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b"),               # 13.08.2026
    re.compile(r"\b\d{4}[./-]\d{1,2}[./-]\d{1,2}\b"),                 # 2026-08-13
    re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MONTH
               + r"\b(?:,?\s+\d{2,4}\b)?", re.I),                     # 28th of July 2026
    re.compile(r"\b" + _MONTH + r"\s+\d{1,2}(?:st|nd|rd|th)?\b(?:,?\s+\d{2,4}\b)?",
               re.I),                                                 # August 13, 2026
    re.compile(r"\b" + _MONTH + r"\s+\d{4}\b", re.I),                 # August 2026
    re.compile(r"\b\d{6,8}\b"),                                       # 13082026
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# "Stock Lists" one week and "Stock List" the next.
_PLURAL = re.compile(r"(?<=[a-z]{3})s\b")
_MIN_STEM = 6


def email_list_stem(label):
    """The part of an emailed list's subject that stays the same from issue to issue.

        "email:Fwd: Shape Homes - Stock List 13.08.2026"  ->  "shape home stock list"
        "Hattan Update - 31 August 2026"                  ->  "hattan update"

    '' for a link (a document, not an email) or a subject with nothing left to match.
    """
    text = str(label or "").strip()
    if not text or text.lower().startswith(("http://", "https://")):
        return ""
    text = _SUBJECT_PREFIX.sub("", text)
    while True:
        shorter = _SUBJECT_FORWARD.sub("", text)
        if shorter == text:
            break
        text = shorter
    for pattern in _SUBJECT_DATES:
        text = pattern.sub(" ", text)
    stem = " ".join(_NON_ALNUM.sub(" ", text.lower()).split())
    stem = _PLURAL.sub("", stem)
    return stem if len(stem) >= _MIN_STEM else ""


def email_list_key(label):
    """A short hash of email_list_stem, or ''. Published instead of the subject."""
    stem = email_list_stem(label)
    return hashlib.sha256(stem.encode("utf-8")).hexdigest()[:16] if stem else ""


# ------------------------------------------------------------------------ conversion

def _num(value):
    """float or None. Never a default — a missing figure must stay missing.

    Anything can arrive over HTTP, and a value this cannot represent must come back as
    "no number", never as an exception: an unparseable field is a 400 naming the field,
    and an uncaught raise here is a 500 that says only "we broke".

      * a Python int beyond float range ("budget_max": 1e400 as a bare integer literal,
        which json.loads returns as an int) raises OverflowError, not ValueError, so it
        went straight past the old handler on all 13 numeric brief fields;
      * JSON has no infinity, but 1e400 parses to inf and "nan" to nan, and both then
        blew up in int() further down instead of here.
    """
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    # inf and nan are not quantities: they compare false against every budget bound and
    # would silently pass a filter rather than fail it.
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _int(value):
    n = _num(value)
    if n is None:
        return None
    try:
        return int(n)
    except (ValueError, OverflowError):     # belt and braces; _num already excludes these
        return None


def _display_address(row):
    """The address a client should read, best available.

    Order matters. street_address is the street on its own, recovered from the row's
    own source text, and is the only version that is reliably an address rather than a
    price list. The lot number is put back in front of it because that is how Coleen
    refers to a listing when she calls a builder — but only when the street does not
    already carry it.
    """
    street = str(row.get("street_address") or "").strip()
    if street:
        lot = str(row.get("lot_number") or "").strip()
        # Only skip when the street already NAMES a lot or unit. A leading digit is the
        # street number — "12 Coledale Drive" — and suppressing the prefix for those
        # dropped the lot number from most addresses, which is the part Coleen quotes.
        if lot and not re.match(r"^\s*(?:lot|unit)\b", street, re.I):
            return "Lot %s, %s" % (lot, street)
        return street
    cleaned = clean_display_address(row.get("lot_address"), row)
    if not _is_a_flattened_row(cleaned):
        return cleaned

    # STILL A SPREADSHEET ROW. clean_display_address never returns empty — "a cluttered
    # address beats a blank one" — which is right for a card and wrong for a document a
    # buyer reads. On the 17 Aug comparison the Denman options came out as
    #     "Available 105 House COPPER 19A 177.08m2 820.0 m2 4 beds / 2 baths / 2 cars
    #      2-part Contract $792,070 Portal Lin"
    # which is not an address, is not client-ready, and cannot be searched for.
    #
    # No street name exists anywhere in those rows — the source never published one — so
    # nothing can recover "106 Almond Street". What the row DOES carry is a lot number, an
    # estate and a suburb, and "Lot 105, Highfields, Denman NSW" is how a consultant names
    # it on the phone. Composed only from stated fields; falls back to the mess when there
    # is nothing to compose from, because a cluttered address still beats a blank one.
    composed = _compose_address(row)
    return composed or cleaned


# Tokens that only appear once a whole stocklist line has been stored in the address
# column: money, areas, bed/bath/car counts, contract and portal wording.
_FLATTENED_ROW = re.compile(
    r"\$\s*[\d,]|m²|\bsqm\b|\bbeds?\b|\bbaths?\b|\bcars?\b|\bgarage\b|"
    r"\bcontract\b|\bavailable\b|\bportal\b", re.I)


def _is_a_flattened_row(text) -> bool:
    # Length is not the test: "Lot 2 Unit 2, 275 Twelfth Avenue, AUSTRAL, NSW, 2179" is
    # long and is a perfectly good address.
    return bool(_FLATTENED_ROW.search(str(text or "")))


def _compose_address(row) -> str:
    """"Lot 105, Highfields, Denman NSW" from the fields the source did state."""
    lot = str(row.get("lot_number") or "").strip()
    estate = str(row.get("estate_name") or "").strip()
    suburb = str(row.get("suburb") or "").strip()
    state = str(row.get("state") or "").strip().upper()
    # The stored suburb is sometimes "Highfields Estate, Denman" — the estate glued to the
    # locality. Keep the last comma-separated part, which is the locality.
    if "," in suburb:
        suburb = suburb.rsplit(",", 1)[-1].strip()
    # THE SUBURB HAS TO BE A PLACE. 115 rows carry suburb "GARAGE" — a spreadsheet column
    # header read as a locality — and composing faithfully produced "Lot 800, GARAGE SA",
    # which asserts a place that does not exist. Naming nowhere is worse than showing the
    # raw row, so an unvalidated locality composes nothing and the mess stands.
    if suburb and not _is_real_locality(suburb, state):
        suburb = ""
    if estate and suburb and estate.lower() in suburb.lower():
        estate = ""                      # an estate repeating the suburb adds nothing
    # Without a real locality the rest names nothing: "Lot 800, Highfields" could be any
    # Highfields in the country.
    if not suburb:
        return ""
    parts = []
    if lot:
        parts.append(lot if re.match(r"^\s*(?:lot|unit)\b", lot, re.I) else "Lot %s" % lot)
    if estate:
        parts.append(estate)
    if suburb:
        parts.append(suburb + (" " + state if state else ""))
    elif state:
        parts.append(state)
    # A lone lot number names nothing: "Lot 105" could be any estate in the country.
    return ", ".join(parts) if (parts and (estate or suburb)) else ""


_GEO = None
_LOCALITY_CACHE = {}


def _is_real_locality(suburb, state):
    """Is this value actually an Australian locality?

    Checked against the 17,537-suburb index the app already ships for distance
    search — the same one that made the benchmark trustworthy. Not a guess and not
    a repair: a value that fails is simply not treated as a location.

    If the index cannot be loaded the answer is True. Degrading to "accept
    everything" keeps the pipeline working exactly as it did before this gate
    existed; degrading to "reject everything" would empty every shortlist, and a
    missing data file should not be able to do that.
    """
    global _GEO
    key = (str(suburb or "").strip().lower(), str(state or "").strip().upper())
    if key in _LOCALITY_CACHE:
        return _LOCALITY_CACHE[key]
    if _GEO is None:
        try:
            from geo import SuburbGeoIndex
        except ImportError:                              # deployed layout: api/_lib
            try:
                from _lib.geo import SuburbGeoIndex      # type: ignore
            except Exception:
                _GEO = False
                return True
        except Exception:
            _GEO = False
            return True
        try:
            idx = SuburbGeoIndex()
            _GEO = idx if getattr(idx, "loaded", False) else False
        except Exception:
            _GEO = False
    if _GEO is False:
        return True
    try:
        ok = bool(_GEO.locate(str(suburb), str(state or "")))
    except Exception:
        ok = True
    _LOCALITY_CACHE[key] = ok
    return ok


def clean_locality(suburb, state):
    """The real locality inside a suburb value, or '' if there is not one.

    Delegates to SuburbGeoIndex.resolve_locality so the scoring pipeline and
    benchmark_buildings.py cannot drift apart on what counts as a place — they did,
    and a lot in "Stage 5A, Greenbank" was shortlisted with no benchmark because one
    unglued the composite and the other did not.
    """
    raw = str(suburb or "").strip()
    if not raw:
        return ""
    if _GEO is None:
        _is_real_locality(raw, state)          # force the index to load
    if _GEO is False:
        return raw                              # no index: behave as before
    try:
        return _GEO.resolve_locality(raw, str(state or ""))
    except Exception:
        return raw


def _storeys(row):
    """1 / 2 from the stored storey text, else None. 'SINGLE'/'DOUBLE' or a digit."""
    raw = str(row.get("storey") or "").strip().lower()
    if not raw:
        return None
    if raw.startswith("sing") or raw == "1":
        return 1
    if raw.startswith("doub") or raw.startswith("two") or raw == "2":
        return 2
    digits = "".join(c for c in raw if c.isdigit())
    return int(digits) if digits and int(digits) in (1, 2, 3) else None


def _fresh(row, today=None):
    """(verified, seen_datetime_or_None, raw_date_text).

    A stored row WAS verified — by the live scrape that captured it on date_checked
    (sources/e_agent.py:467 sets verified=True for exactly that reason). What the
    deployment cannot do is re-verify it now. So the recorded verification is honoured
    while it is fresh and allowed to lapse afterwards, at which point the pipeline's
    own three-state rule keeps the row out of the final list as Pending Confirmation.
    An unparseable or absent capture date is never treated as verified.

    THE SHAPE STAYS. _price_unverified reads `seen` and the same-listing collapse ranks
    on it; the age a caller wants comes from _age_days. `today` defaults to the AEST
    wall clock (see AEST), not the server's.
    """
    raw = str(row.get("date_checked") or "").strip()
    seen = _parse_day(raw)
    if seen is None:
        return False, None, raw
    age = ((today or _now_aest()) - seen).days
    return (0 <= age <= FRESH_DAYS), seen, raw


_DAY_FORMATS = ("%d/%m/%Y", "%Y-%m-%d")
# A snapshot has a few hundred distinct dates across twenty thousand rows, and preparing
# the guards parses every row's; strptime is the expensive part.
_DAY_CACHE = {}


def _parse_day(value):
    """The date a dd/mm/YYYY or ISO value names (first ten characters), or None."""
    raw = str(value or "").strip()
    if raw in _DAY_CACHE:
        return _DAY_CACHE[raw]
    seen = None
    for fmt in _DAY_FORMATS:
        try:
            seen = datetime.strptime(raw[:10], fmt)
            break
        except ValueError:
            continue
    if len(_DAY_CACHE) < 20_000:
        _DAY_CACHE[raw] = seen
    return seen


def _collapse_same_listing(entries):
    """Merge rows that describe the SAME lot at the SAME price with the same facts.

    The same lot legitimately appears twice in the table when two channels captured it
    (a builder portal on the 27th, E-Agent on the 29th) — different rows by design,
    because identity includes the channel. For a client shortlist they are one
    property, and the pipeline's DedupeEngine will not merge them: its key includes
    house_design, and one channel recorded a product type while the other did not.

    Merging is not a judgement call here: builder, lot, suburb, price, bedrooms,
    bathrooms, car spaces, house and land size are all identical. The freshest
    capture wins, then the one carrying more detail.

    `entries` is [(seen_date_or_None, package)]. Returns (packages, merged_count).
    """
    kept = _collapse_kept(entries)
    return [entries[i][1] for i in kept], len(entries) - len(kept)


def _collapse_kept(entries):
    """Indexes into `entries` of the packages _collapse_same_listing keeps, in its order.

    Indexes rather than packages so a caller can also say WHICH rows were merged away --
    explain_row needs that to tell a consultant their lot was folded into its twin.
    """
    groups = {}
    order = []
    for index, (seen, pkg) in enumerate(entries):
        key = (pkg["builder_name"].lower(), pkg["lot_address"].lower(),
               pkg["suburb"].lower(), pkg["advertised_package_price"],
               pkg["bedrooms"], pkg["bathrooms"], pkg["car_spaces"],
               pkg["house_size_sqm"], pkg["land_size_sqm"])
        rank = (seen or datetime.min,
                1 if pkg["house_design"] else 0,
                1 if pkg["source_url_or_ref"].startswith("http") else 0)
        if key not in groups:
            groups[key] = (rank, index)
            order.append(key)
        elif rank > groups[key][0]:
            groups[key] = (rank, index)
    return [groups[k][1] for k in order]


# A group must have more than one located row before it can name the rest. See the note
# at the ratio test: 80% of one voter is 100%, and an estate should not be named by an
# echo of a single recovery.
_SIBLING_MIN_VOTERS = 2


def _sibling_suburb_map(rows, field):
    """{(group, state): suburb} where one group's located rows agree on a suburb.

    `field` is what makes two rows siblings — the estate they are in, or the page they
    were read from. Both are used, estate first, because a lot with no locality of its
    own is nearly always sitting next to lots that have one.

    THE VOTES ARE RESOLVED SUBURBS, not raw column values. This used to tally
    row["suburb"] directly, which meant a group whose rows all carry a street name in
    that column ("Coledale", "Lockington") voted the street in unanimously, and a group
    whose suburbs were only recoverable from their addresses had no votes at all and
    named nothing. Measured on the live snapshot, voting on resolved values instead
    reaches 99.0% on estate siblings and 95.5% on page siblings, scored by hiding a
    row's own suburb and asking its siblings to name it.
    """
    seen = {}
    for r in rows:
        if r.get("superseded_by"):
            continue
        group = str(r.get(field) or "").strip().lower()
        state = str(r.get("state") or "").strip().upper()
        if not (group and state):
            continue
        sub, why = suburb_quality.resolve(r)
        if not (sub and suburb_quality.is_located(why)):
            continue
        seen.setdefault((group, state), {})
        seen[(group, state)][sub] = seen[(group, state)].get(sub, 0) + 1
    out = {}
    for key, counts in seen.items():
        total = sum(counts.values())
        top, n = max(counts.items(), key=lambda kv: kv[1])
        # A split group names nothing: two suburbs at 50/50 is not evidence.
        #
        # AND NEITHER IS ONE ROW. 80% of a single voter is 100%, so a group whose only
        # located row was itself a recovery could name an entire estate — a 400-lot
        # project taking its suburb from one lot, with the ratio test passing trivially
        # because there was nothing to disagree with. Two agreeing rows is still a low
        # bar; it is the difference between a consensus and an echo.
        if total >= _SIBLING_MIN_VOTERS and n / total >= 0.8:
            out[key] = top
    return out


def _estate_suburb_map(rows):
    """Kept as the name the rest of the app and its tests already call."""
    return _sibling_suburb_map(rows, "estate_name")


def _unambiguous_estate_map(rows):
    """{(builder, estate): (state, suburb)} where the estate sits in exactly one place.

    THE SIBLING MAP IS KEYED ON (estate, STATE), which is what makes it safe -- estate
    names repeat across the country. That key is also unreachable for a row whose state
    is missing or wrong, and those are exactly the rows with nothing else left:

      32 rows carry no state at all, while their estate's located siblings agree on one.
      10 rows carry a state that CONTRADICTS their estate's, all of them Hattan's
         "Penrose Place" lots, which say NSW where the estate's other ten say VIC and
         name Officer. Their state_source is 'suburb' -- the state was derived from the
         suburb column, and that column holds 'Penrose Place', which is the estate, not
         a locality. The state is downstream of a value this module already refuses, so
         it is not independent evidence of anything.

    Stricter than the sibling map in every other respect: keyed on builder AND estate,
    and the located siblings must agree on ONE state and ONE suburb, not merely 80%.
    """
    seen = {}
    for r in rows:
        if r.get("superseded_by"):
            continue
        estate = str(r.get("estate_name") or "").strip().lower()
        if not estate:
            continue
        sub, why = suburb_quality.resolve(r)
        if not (sub and suburb_quality.is_located(why)):
            continue
        key = (str(r.get("builder_name") or "").strip().lower(), estate)
        seen.setdefault(key, set()).add((str(r.get("state") or "").strip().upper(), sub))
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def last_resort_map(rows):
    """{(field, value): suburb} -- the majority suburb of a row's siblings.

    THE FLOOR, and it is a low one. Every rule above this needs the row, its document or
    its estate to actually name a place; these are the rows where none of them does --
    "$620,000.00 $461,312.00 $0.00 Click here Click here", or a Hudson lot whose only
    word is its builder's name, which is also a town 1,500 km away.

    Asked for on the standing instruction to fill every listing. Holdout across the whole
    catalogue, hiding each located row's suburb and asking its siblings:

        builder_name   50%
        source_doc     37%

    Ordered by that measurement, best first. Roughly one row in two gets the wrong town,
    which is why the answer is graded INFERRED_FROM_SIBLINGS and never located -- it is
    searchable and badged, and no benchmark, distance filter or client comparable will
    touch it.
    """
    # TWO TIERS. Located siblings vote first; inferred ones are counted only where a key
    # has no located voter at all.
    #
    # Counting located rows alone left two documents unable to answer themselves. All
    # seven rows of the VIC SQ price list are one building, three of them read Flemington
    # out of their own text, and the other four got nothing -- the three that had an
    # answer were inferred, so they were not allowed to speak for their own siblings.
    # Fifteen of sixteen Woodchester rows read Gatton the same way, and the sixteenth,
    # which differs from them only in having no state recorded, got nothing.
    #
    # An inferred voter is weak evidence. It is also the only evidence these rows have,
    # and the answer it produces is graded INFERRED_FROM_SIBLINGS either way -- badged,
    # never located, never benchmarked.
    strong, weak = {}, {}
    for r in rows:
        if r.get("superseded_by"):
            continue
        sub, why = suburb_quality.resolve(r)
        if not sub:
            continue
        into = strong if suburb_quality.is_located(why) else (
            weak if suburb_quality.is_inferred(why) else None)
        if into is None:
            continue
        for field in ("builder_name", _DOC_KEY, "source_url"):
            value = str(r.get(field) or "").strip().lower()
            if value:
                counts = into.setdefault((field, value), {})
                counts[sub] = counts.get(sub, 0) + 1
    out = {k: max(v.items(), key=lambda kv: kv[1])[0] for k, v in strong.items()}
    for k, v in weak.items():
        out.setdefault(k, max(v.items(), key=lambda kv: kv[1])[0])
    return out


def last_resort_suburb(row, table):
    """The best guess this row's siblings can offer, or "". Ordered by measured accuracy."""
    for field in ("builder_name", _DOC_KEY, "source_url"):
        value = str(row.get(field) or "").strip().lower()
        if not value:
            continue
        got = table.get((field, value))
        if got:
            return got
    return ""


def _estate_is_the_only_evidence(row):
    """True when this row's own state cannot be trusted over its estate's.

    Either it has none, or the only thing it rests on is the suburb column -- and this
    function is reached only for rows whose suburb column has already been refused.
    """
    if not str(row.get("state") or "").strip():
        return True
    return str(row.get("state_source") or "").strip().lower() == "suburb"


def _sibling_state_map(rows, floor=0.95):
    """{(field, group): state} where a group's rows agree overwhelmingly on one state.

    A stateless row is invisible to every other rule here, because both sibling maps are
    keyed by state and suburb_quality will not check a locality without one. Borrowing
    the state first is what lets those rules run at all.
    """
    seen = {}
    for row in rows:
        if row.get("superseded_by"):
            continue
        state = str(row.get("state") or "").strip().upper()
        if not state:
            continue
        # BUILDER LAST, and only because 95% makes it safe.
        #
        # A builder that sells in one state is strong evidence for a row of theirs that
        # forgot to say so -- 287 stateless listings. A builder that sells in several is
        # no evidence at all, and the same 95% floor that protects the estate and
        # document groups refuses them by construction: Creation Homes (QLD 138 / NSW
        # 122), Hudson Homes, G Developments, Hattan Homes and four others never qualify.
        # It is tried after estate and document because those are narrower claims.
        for field in ("estate_name", _DOC_KEY, "source_url", "builder_name"):
            group = str(row.get(field) or "").strip().lower()
            if not group:
                continue
            seen.setdefault((field, group), {})
            seen[(field, group)][state] = seen[(field, group)].get(state, 0) + 1
    out = {}
    for key, counts in seen.items():
        total = sum(counts.values())
        top, n = max(counts.items(), key=lambda kv: kv[1])
        if total and n / total >= floor:
            out[key] = top
    return out


# An estate string that CONTAINS a known one, and how long it has to be to count.
#
# The same project is written several ways across a catalogue: "Central Springs" on one
# builder's sheet and "Caboolture Central Springs" on another's, "Rosewood Green" and
# "Rosewood Rosewood Green". Exact-match state borrowing sees those as different estates
# and lends nothing, so the longer spelling stays stateless -- and without a state no
# suburb rule will run on it at all.
#
# ONLY THE INFORMATIVE DIRECTION. The row's estate must contain a KNOWN one, never the
# reverse: "south east" appears inside plenty of estate names, is evidence of nothing, and
# lent VIC to a Queensland row when this was first measured the other way round.
#
# Ten characters, on a word boundary, and exactly one state among the matches. Measured
# across the catalogue: 47 rows gain a state and NOT ONE is ambiguous. At eight characters
# it is 79 rows and one ambiguous, which is the wrong side of the line for a fact every
# other rule here depends on.
_ESTATE_CONTAINS_MIN = 10
_GENERIC_ESTATE = re.compile(
    r"^(north|south|east|west|central|region|regional|metro|city|inner|outer|land|house|"
    r"south east|south west|north east|north west|stage)$", re.I)


def _containment_candidates(state_map):
    """[(estate string, state)] worth testing for containment, longest first.

    Built ONCE per request. The first version of this scanned every known estate for
    every stateless row with an uncompiled regex -- roughly a million pattern compiles on
    the live catalogue -- and turned a search into a ten-minute wait. This is the same
    filtering hoisted out of the row loop.
    """
    out = [(group, state) for (field, group), state in state_map.items()
           if field == "estate_name"
           and len(group) >= _ESTATE_CONTAINS_MIN
           and not _GENERIC_ESTATE.match(group)]
    out.sort(key=lambda pair: -len(pair[0]))
    return out


def _state_by_estate_containment(row, candidates):
    """A state from a KNOWN estate name spelled out inside this row's own, or ''."""
    estate = str(row.get("estate_name") or "").strip().lower()
    if not estate:
        return ""
    found = set()
    for group, state in candidates:
        # Plain `in` first: it is a C-speed scan and rejects almost everything. Only a
        # hit pays for the boundary check, which is what stops "rosewood" matching
        # inside "rosewoodlands".
        start = estate.find(group)
        if start < 0:
            continue
        before = estate[start - 1] if start else " "
        after_index = start + len(group)
        after = estate[after_index] if after_index < len(estate) else " "
        if not before.isalpha() and not after.isalpha():
            found.add(state)
            if len(found) > 1:
                return ""
    return next(iter(found)) if len(found) == 1 else ""


class PreparedRows:
    """The brief-independent half of build_packages, computed once per snapshot.

    Every map below reads the whole catalogue and none of them reads the brief, yet they
    were rebuilt on every request -- about 0.35 s of the 0.53-0.70 s build_packages took
    on the live snapshot. That was merely slow while a search ran build_packages once.
    Explaining a thin search re-runs it for every relaxation it prices, and a
    needs-confirming pass runs it again, so the cost has to be paid once.

    Built by prepare_rows(rows) and valid ONLY for those rows: row_count is checked on
    every use, so a prepared set from another snapshot fails loudly instead of quietly
    borrowing states and suburbs from a catalogue that is no longer deployed.
    """

    __slots__ = ("row_count", "estate_suburb", "page_suburb", "state_of", "containment",
                 "unambiguous_estate", "states_availability",
                 # The sold-stock guards' evidence (confirm_guard), over EVERY row --
                 # superseded ones and other states included, because the capture that
                 # says a lot is On Hold is often exactly the row a search would skip.
                 "captures", "doc_reads", "builder_reads", "email_lists",
                 "doc_statements", "doc_lines")

    def __init__(self, **fields):
        for name in self.__slots__:
            setattr(self, name, fields[name])


def prepare_rows(rows):
    """PreparedRows for this snapshot. Pure: the same rows always give the same maps."""
    # A suburb the SAME ESTATE states on its other rows. 3,292 live rows record no suburb
    # at all and are therefore unlocatable — dropped before scoring, so a third of the
    # apartment stock can never be recommended. The suburb is inside the source PDF the
    # extractor never read, and E-Agent's file names are Wix hashes, so nothing outside
    # the row can supply it — except the estate's own siblings. "Uptown" appears on 18
    # rows that DO say Shepparton; the ones that do not are the same estate in the same
    # state.
    #
    # Only when the estate is unambiguous (>=80% of its located siblings agree) and only
    # within one state, because a fifth of Australian locality names exist in more than
    # one. It recovers ~222 rows of the 3,292 — 7%. The rest need the extractor to read
    # the suburb out of the file, which is harvest-side work, not a filter fix.
    estate_suburb = _sibling_suburb_map(rows, "estate_name")
    # SECOND, and only where the estate has nothing to say. A source page is a weaker
    # claim than an estate — one page can list several suburbs — so it is checked after,
    # and the same 80% agreement rule applies. It is worth having: 496 live rows have no
    # estate name at all but sit on a page whose other rows agree on one suburb.
    page_suburb = _sibling_suburb_map(rows, _DOC_KEY)
    # THE STATE, BORROWED, BEFORE ANY OF THE ABOVE CAN BE ASKED.
    #
    # Both maps above are keyed by state, and every suburb rule in suburb_quality
    # requires one, so a row with no state recorded cannot be reached by any of them.
    # That is not a small population: "CC-0114, Bald Hills" and "CC-0108, One Mile" name
    # real Queensland suburbs and were unreachable purely because their own row does not
    # say QLD, while every other row on the same price list does.
    #
    # 95%, not 80%. A state is a stronger claim than a suburb — one estate sits in one
    # state, and a page mixing two is a page whose state cannot be borrowed at all.
    state_of = _sibling_state_map(rows)
    containment = _containment_candidates(state_of)
    # LAST RESORT, for rows the two maps above cannot key at all — see _gate.
    unambiguous_estate = _unambiguous_estate_map(rows)
    # WHICH DOCUMENTS SAY ANYTHING ABOUT AVAILABILITY AT ALL. See the filter in _gate.
    # Built once, outside the row loop: the estate-containment lookup was written inside
    # one and took ten minutes a request before it was hoisted.
    #
    # SUPERSEDED ROWS STILL COUNT, and must. Counting live rows only would call 17
    # documents silent and promote their 53 blank live rows to "available by the sheet" --
    # 40 of them fresh, straight into the shortlist and to Clare: 34 'Gardner Rd Stage 3'
    # lots, 3 Gallery Group lots, Hattan and Hermitage rows. Their superseded captures said
    # On Hold or Sold. A list that marks only what has gone is an exceptions-only list, and
    # its blank rows are for a consultant to confirm (the admit pass), never a
    # recommendation by default.
    states_availability = set()
    for _r in rows:
        _a = str(_r.get("availability_status") or "").strip().lower()
        if _a in IS_AVAILABLE or _a in NOT_AVAILABLE:
            states_availability.add(str(_r.get(_DOC_KEY) or ""))
    states_availability.discard("")
    guards = _guard_indexes(rows)
    return PreparedRows(row_count=len(rows), estate_suburb=estate_suburb,
                        page_suburb=page_suburb, state_of=state_of, containment=containment,
                        unambiguous_estate=unambiguous_estate,
                        states_availability=states_availability, **guards)


# One entry: a warm instance serves one deployed snapshot, and a stale entry must never
# outlive the file it was built from.
_PREPARED_CACHE = {}


def prepared_for_snapshot(rows, source_path):
    """prepare_rows(rows), cached per warm instance on the snapshot's path + mtime.

    `rows` must be what load_snapshot() just read from `source_path`. The key also carries
    the file size, and a hit is used only when its row count matches, so a snapshot
    rewritten in place is rebuilt rather than served from the old maps.

    NOT FOR ROWS CHANGED IN MEMORY. The key is the file, so rows patched after loading (a
    test that supersedes a lot or moves a state without rewriting the file) would be served
    the maps of the unpatched file: states borrowed and suburbs voted from data that is no
    longer the data searched. Call prepare_rows(patched_rows) for those.
    """
    try:
        st = os.stat(str(source_path))
        key = (os.path.abspath(str(source_path)), st.st_mtime_ns, st.st_size)
    except OSError:
        return prepare_rows(rows)
    hit = _PREPARED_CACHE.get(key)
    if hit is not None and hit.row_count == len(rows):
        return hit
    prepared = prepare_rows(rows)
    _PREPARED_CACHE.clear()
    _PREPARED_CACHE[key] = prepared
    return prepared


def _checked_prepared(rows, prepared):
    """prepared, built now when absent, refused when it came from other rows."""
    if prepared is None:
        return prepare_rows(rows)
    if prepared.row_count != len(rows):
        raise ValueError("prepared rows were built from %d rows, not these %d; call "
                         "prepare_rows on the snapshot being searched"
                         % (prepared.row_count, len(rows)))
    return prepared


# ------------------------------------------------------------- confirming, and not
#
# A row the search cannot verify -- last read more than FRESH_DAYS ago, or on a list that
# never said it was for sale -- is not thereby worth a consultant's phone call. Some of
# them are simply sold, and the catalogue says so somewhere else. Measured on the 17 Sep
# copy: the three "Windermere Mambourin" lots filed QLD "Available" on 21 Aug are the same
# builder, estate and lot 27102 the 16 Sep read files VIC and On Hold. confirm_guard names
# that evidence; a guarded row is hidden and counted, never shown to confirm.

# The channels that re-list a document's whole stock every time they read it, so a row the
# latest read left out is evidence of a sale. An emailed list is sent once and never
# re-listed; a missing row there proves nothing.
RELISTING_CHANNEL_PREFIXES = ("e-agent", "proxima", "direct builder portal", "direct portal")
EMAIL_CHANNEL = "digital email"
# How recently a document must have been read for its silence about a row to count.
REREAD_WITHIN_DAYS = 3
# A PARTIAL READ IS NOT A RE-READ. The latest read must be at least this share of the
# largest read of that document in its last FULL_READ_RUNS reads. Shared with the
# withdrawal repair (repair_rows, W19) through latest_read_is_full.
FULL_READ_SHARE = 0.60
FULL_READ_RUNS = 7
# Two captures of one lot are the same listing only when their prices agree this closely.
NEWER_CAPTURE_PRICE_TOLERANCE = 0.02
# The largest amount a row's text prints may exceed its stored price by this much before
# the stored price is read as one component of a bigger total (a duplex half).
TEXT_PRICE_TOLERANCE = 0.02
# A price that is not the price of a home and land a buyer can purchase as listed.
NOT_ADMITTABLE_PRICE_KINDS = ("build_only", "land_only", "commercial")
# A stored land or build price this close to the stored price IS the stored price.
COMPONENT_PRICE_TOLERANCE = 1.0
# The channels whose row text is the price list's own line (an E-Agent sheet row, a line
# of an emailed list), so a price the text does not print was not read off that line. A
# portal row's text is its address card and never prints a price: Proxima's 4,251 live
# rows all have price_in_text false, and that says nothing against any of them.
PRICE_LINE_CHANNELS = ("e-agent", EMAIL_CHANNEL)
# An office, a shop or a warehouse. Only on a row that states no bedrooms, and never
# "home office" or "study/office": those are rooms in a house.
_TEXT_COMMERCIAL = re.compile(
    r"(?<!home )(?<!study )(?<!study/)(?<!sales )(?<!post )(?<!display )"
    r"\b(?:office|retail|shop|warehouse|tenancy)\b", re.I)


def _norm(value):
    return " ".join(_NON_ALNUM.sub(" ", str(value or "").lower()).split())


def _channel(row):
    return str(row.get("source_channel") or "").strip().lower()


def _is_relisting(channel):
    return channel.startswith(RELISTING_CHANNEL_PREFIXES)


def capture_key(row):
    """(who, estate, lot) naming one lot across captures, or None without a lot number.

    who is the builder when the row names one, else its document: a blank builder is not
    a builder, and keying "" would make every nameless lot 624 in the country one lot.
    The estate is part of the key for the same reason -- (builder, lot) alone guarded 505
    of 925 stale Available rows where (builder-or-document, estate, lot) guards 292; the
    213 difference were other estates' lots with the same number, e.g. a VIC lot 624
    "guarded" by NSW 'The CARLING' lot 624. The STATE is deliberately not in the key: the
    Windermere lots are filed QLD on one capture and VIC on the next.
    """
    lot = _norm(row.get("lot_number"))
    if not lot:
        return None
    builder = _norm(row.get("builder_name"))
    if builder:
        who = "builder:" + builder
    else:
        doc = str(row.get(_DOC_KEY) or "").strip()
        if not doc:
            return None
        who = "doc:" + doc
    return (who, _norm(row.get("estate_name")), lot)


def latest_read_is_full(reads_by_day, share=FULL_READ_SHARE, runs=FULL_READ_RUNS):
    """Whether a document's latest read took in most of what it usually does.

    reads_by_day is {day: rows read that day} for one document. True when the newest
    day's count is at least `share` of the largest count among its newest `runs` days.
    A snapshot cannot see a night's true read count -- a row re-read the next night
    carries the later date -- so from stock.json this is rows LAST read each day. The
    shape it exists to catch survives that: 80 rows read yesterday and 20 tonight is 20
    against 60 left behind, a partial read.
    """
    if not reads_by_day:
        return False
    days = sorted(reads_by_day, reverse=True)[:max(1, runs)]
    largest = max(reads_by_day[d] for d in days)
    return reads_by_day[days[0]] >= share * largest


def _guard_indexes(rows):
    """The PreparedRows fields confirm_guard reads. See PreparedRows.__slots__."""
    captures = {}          # capture_key -> [(day, availability, price, line_key)]
    doc_lines = {}         # (source_doc, line_key) -> [(day, availability, price)]
    reads = {}             # source_doc -> {day: rows}
    builder_reads = {}     # (relisting channel, builder) -> newest day
    email_lists = {}       # email_list -> {source_doc: newest day}
    doc_statements = {}    # source_doc -> [live rows stated available, rows marked gone]
    for row in rows:
        avail = str(row.get("availability_status") or "").strip().lower()
        doc = str(row.get(_DOC_KEY) or "").strip()
        if doc:
            stated = doc_statements.setdefault(doc, [0, 0])
            if avail in IS_AVAILABLE and not row.get("superseded_by"):
                stated[0] += 1
            # Superseded captures included: that is how an exceptions-only list whose
            # marked lots have since dropped off is still known to be one.
            if avail in NOT_AVAILABLE:
                stated[1] += 1
        seen = _parse_day(row.get("date_checked"))
        if seen is None:
            continue
        key = capture_key(row)
        if key is not None:
            captures.setdefault(key, []).append(
                (seen, avail, _num(row.get("price")), str(row.get("line_key") or "")))
        line = str(row.get("line_key") or "")
        if doc and line:
            doc_lines.setdefault((doc, line), []).append((seen, avail, _num(row.get("price"))))
        if doc:
            days = reads.setdefault(doc, {})
            days[seen] = days.get(seen, 0) + 1
        channel = _channel(row)
        if _is_relisting(channel):
            builder = _norm(row.get("builder_name"))
            if builder and seen > builder_reads.get((channel, builder), datetime.min):
                builder_reads[(channel, builder)] = seen
        elif channel == EMAIL_CHANNEL and doc:
            listed = str(row.get("email_list") or "").strip()
            if listed:
                docs = email_lists.setdefault(listed, {})
                if seen > docs.get(doc, datetime.min):
                    docs[doc] = seen
    doc_reads = {doc: (max(days), latest_read_is_full(days)) for doc, days in reads.items()}
    return {"captures": captures, "doc_reads": doc_reads, "builder_reads": builder_reads,
            "email_lists": email_lists, "doc_statements": doc_statements,
            "doc_lines": doc_lines}


def _confirm_age_days(row, today):
    """How old a row is for the CONFIRM_MAX_DAYS limit, or None without a readable date.

    An emailed row ages from email_first_seen when the snapshot has it: its date_checked
    is the last night the message was still inside the inbox sweep, up to a week after the
    list was sent (Dawn: list 13 Aug, first read 17 Aug, "checked" 20 Aug).
    """
    ages = []
    seen = _parse_day(row.get("date_checked"))
    if seen is not None:
        ages.append((today - seen).days)
    if _is_email(row):
        first = _parse_day(row.get("email_first_seen"))
        if first is not None:
            ages.append((today - first).days)
    return max(ages) if ages else None


def _newer_capture(row, prepared, seen):
    """'newer_capture_disagrees', 'newer_capture_exists', 'newer_capture_repriced' or None,
    from capture_key twins read on a strictly later day.

    repriced: a later capture at a price more than NEWER_CAPTURE_PRICE_TOLERANCE away whose
    line, prices taken out, is this row's line (line_key). Hattan lot 406, Mambourin Green
    was offered to confirm at its 17/08 price, $588,950, while the 16/09 read of the same
    line asked $604,776 -- over the brief's $600,000. A later capture at another price
    whose line differs is another design on the lot and hides nothing.

    disagrees, ALSO WITHOUT A LOT NUMBER: the same line of the same document (source_doc,
    line_key) read on a later day at the same price (within 2%) says the lot has gone.
    Howard lot 21 was read "Howard Available 21 Howard Crossing ..." on 15/09 and "Howard ON
    HOLD 21 ..." on 16/09; its rows carry no lot number, so capture_key could never pair
    them, and while supersede's status pass is off (SPB_STATUS_PASS) both stay live. Same
    day never counts: two lots printed identically on one page are read together."""
    if seen is None:
        return None
    price = _num(row.get("price"))
    line = str(row.get("line_key") or "")
    doc = str(row.get(_DOC_KEY) or "").strip()
    if doc and line and price:
        for when, avail, other_price in prepared.doc_lines.get((doc, line), ()):
            if (when > seen and avail in NOT_AVAILABLE and other_price
                    and abs(other_price - price) <= price * NEWER_CAPTURE_PRICE_TOLERANCE):
                return "newer_capture_disagrees"
    key = capture_key(row)
    if key is None:
        return None
    exists = repriced = False
    for when, avail, other_price, other_line in prepared.captures.get(key, ()):
        if when <= seen:
            continue
        # Any later capture marked gone, at any price: a lot re-priced and then put on
        # hold is still on hold.
        if avail in NOT_AVAILABLE:
            return "newer_capture_disagrees"
        if (price and other_price
                and abs(other_price - price) <= price * NEWER_CAPTURE_PRICE_TOLERANCE):
            exists = True
        elif price and other_price and line and other_line == line:
            repriced = True
    if exists:
        return "newer_capture_exists"
    return "newer_capture_repriced" if repriced else None


def price_covers(row):
    """What the stored price is for, as the search reads it.

      land_only / build_only  the row's OWN land and build columns say the price is one of
                              them: land_price is the price and no build price is stated
                              ("Wollert Land Price $143,389"), or the other way round
                              ("FLOORPLAN Victoria 180 HOUSE PRICE $413,000", the house
                              half of 18 Wiltipoll Road's $617,000)
      commercial              the line names an office, shop or warehouse and the row
                              states no bedrooms ("Building A Ground Level Office 81 0 81"
                              $740,000, filed as an Apartment)
      otherwise               the snapshot's price_kind ('' when it has none)

    price_kind itself calls the first two 'unknown' (build_web computes it from what the
    text says, and benchmarks read it); the search reads the columns as well."""
    price = _num(row.get("price")) or 0.0
    land = _num(row.get("land_price")) or 0.0
    build = _num(row.get("build_price")) or 0.0
    if price > 0:
        if land and not build and abs(land - price) <= COMPONENT_PRICE_TOLERANCE:
            return "land_only"
        if build and not land and abs(build - price) <= COMPONENT_PRICE_TOLERANCE:
            return "build_only"
    kind = str(row.get("price_kind") or "").strip().lower()
    if kind in NOT_ADMITTABLE_PRICE_KINDS:
        return kind
    if not _num(row.get("bedrooms")) and any(
            _TEXT_COMMERCIAL.search(str(row.get(f) or ""))
            for f in ("lot_address", "title_status", "source_text")):
        return "commercial"
    return kind


def price_evidence_refusal(row):
    """Why this row's price is not a home and land at that price, or None -- from what the
    row POSITIVELY says, so a snapshot without price evidence refuses nothing here.

      price_not_in_text     the row is a line of a price list (PRICE_LINE_CHANNELS) whose
                            evidence says the stored price is not printed on it: an email
                            sentence stored at $553,284
      larger_total_in_text  the line prints an amount above price + 2%: one component
      build_only / land_only / commercial   price_covers

    For EVERY row offered to confirm (release review). The exceptions-only admission ran
    these checks, stricter, on unstated rows only; a stale "Available" row reached the amber
    group through the Pending Confirmation list without them, and Colin's Any state <=
    $750k search offered three "Land Price" lines, two "HOUSE PRICE" lines and an email
    sentence as house-and-land listings."""
    price = _num(row.get("price")) or 0.0
    stated = row.get("price_in_text")
    if (stated is not None and not _truthy(stated)
            and _channel(row).startswith(PRICE_LINE_CHANNELS)):
        return "price_not_in_text"
    printed = _num(row.get("text_price_max"))
    if printed is not None and printed > price * (1 + TEXT_PRICE_TOLERANCE):
        return "larger_total_in_text"
    kind = price_covers(row)
    return kind if kind in NOT_ADMITTABLE_PRICE_KINDS else None


def confirm_verdict(row, prepared, today=None):
    """(reason or None, notes) for a row that cannot be verified.

    reason is why the row must stay HIDDEN rather than be shown to confirm, first match:

      price_not_in_text, larger_total_in_text, build_only, land_only, commercial
                                   what the row's own price evidence says: its price is
                                   not a home and land at that price
                                   (price_evidence_refusal)

      newer_capture_disagrees      a later capture of the same lot says it is gone
      newer_capture_exists         a later capture of the same lot at the same price
                                   (within 2%) exists, so this row is its old copy
      newer_capture_repriced       a later capture of the same lot, on the same line once
                                   prices are taken out (line_key), asks another price:
                                   this row's price is out of date
      document_reread_without_row  relisting channel; the row's document was read after
                                   the row, within REREAD_WITHIN_DAYS, and that read was
                                   a full one (latest_read_is_full)
      builder_documents_reread     relisting channel, named builder; the builder's other
                                   documents in the channel were read within
                                   REREAD_WITHIN_DAYS and this row's were not
      newer_list_from_sender       emailed; a different email with the same subject stem
                                   was read after the row
      over_confirm_limit           older than CONFIRM_MAX_DAYS (an email by first read)
      no_capture_date              no readable date_checked at all

    notes are things to confirm that hide nothing: 'document_partially_read' when the
    document was re-read after the row but only partly, which is not evidence the row
    was dropped.
    """
    today = today or _now_aest()
    notes = []
    refused = price_evidence_refusal(row)
    if refused:
        return refused, notes
    seen = _parse_day(row.get("date_checked"))
    if seen is None:
        return "no_capture_date", notes
    newer = _newer_capture(row, prepared, seen)
    if newer:
        return newer, notes
    channel = _channel(row)
    doc = str(row.get(_DOC_KEY) or "").strip()
    if _is_relisting(channel):
        read = prepared.doc_reads.get(doc) if doc else None
        doc_recent = bool(read and (today - read[0]).days <= REREAD_WITHIN_DAYS)
        if doc_recent and read[0] > seen:
            if read[1]:
                return "document_reread_without_row", notes
            notes.append("document_partially_read")
        builder = _norm(row.get("builder_name"))
        if builder and not doc_recent:
            newest = prepared.builder_reads.get((channel, builder))
            if newest is not None and (today - newest).days <= REREAD_WITHIN_DAYS:
                return "builder_documents_reread", notes
    elif channel == EMAIL_CHANNEL:
        listed = str(row.get("email_list") or "").strip()
        for other_doc, newest in (prepared.email_lists.get(listed) or {}).items():
            if other_doc != doc and newest > seen:
                return "newer_list_from_sender", notes
    age = _confirm_age_days(row, today)
    if age is not None and age > CONFIRM_MAX_DAYS:
        return "over_confirm_limit", notes
    return None, notes


def confirm_guard(row, prepared, today=None):
    """None when this unverified row may be shown to confirm, else why it stays hidden.

    See confirm_verdict for the reasons. `prepared` is prepare_rows over the whole
    snapshot the row belongs to.
    """
    return confirm_verdict(row, prepared, today)[0]


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


# A BLANK STATUS CELL IS NOT A BLANK LINE. The status column is filled by a vocabulary
# (NOT_AVAILABLE, repair_rows' sold words) that does not know every phrase a price list
# uses. Measured on the 17 Sep copy: the NSW 'Building B' apartment list (E-Agent) marks
# 7 lots "Sold", which the parser read, and 10 more "Under contract" / "Under contact",
# which it did not -- so those 10 are blank on a list that marks lots gone, and 6 of them
# passed every other admission rule and would have been offered to confirm while their
# own line says they are under contract. Read on the text the snapshot carries
# (lot_address, often the whole flattened line; title_status) and on source_text when a
# row still has it. "Under construction" is a build stage, not a sale, and must not match.
_TEXT_UNAVAILABLE = re.compile(
    r"\b(?:sold|on\s*hold|under\s*offer|under\s*(?:contract|contrac|contra|contact)|"
    r"reserved|withdrawn|deposit\s*taken|settled|contract\s*pending|not\s+available|"
    r"unavailable|no\s+longer\s+available|off\s*market)\b", re.I)
# "it is not on hold exclusively or at this stage sold" (repair_rows._NEGATED): a sentence
# saying the lot is NOT gone.
_TEXT_UNAVAILABLE_NEGATED = re.compile(
    r"\b(?:not|isn'?t|nor|never)\s+(?:\w+\s+){0,2}?"
    r"(?:sold|on\s*hold|under\s*offer|under\s*contract|reserved|withdrawn)\b", re.I)
_TEXT_STATUS_COLUMNS = ("lot_address", "title_status", "source_text")


def text_marks_unavailable(row):
    """True when the row's own text says the lot has gone, whatever its status cell says."""
    for field in _TEXT_STATUS_COLUMNS:
        text = str(row.get(field) or "")
        if _TEXT_UNAVAILABLE.search(text) and not _TEXT_UNAVAILABLE_NEGATED.search(text):
            return True
    return False


# The lengths the extractors cut a source line to: sources/spreadsheet_extract.py and
# sources/email_inbox.py write lot_address as line[:110], sources/adaptive_extract.py as
# ln[:120]. A status word past the cut is simply not in the text we hold.
_TRUNCATED_AT = 110


def promotion_refusal(row, prepared):
    """(reason or None): may this UNSTATED row be shown as a RESULT, not only to confirm?

    admission_refusal decides whether a consultant could ring the builder about a blank
    row. This decides the stronger thing -- whether the row may be RECOMMENDED -- and asks
    two questions that only start to matter once a blank row is put in front of a buyer.
    Added 20 Sep 2026 with EXCEPTIONS_LIST_MODE; both were found by replaying the promotion
    over the live snapshot before it shipped.

      gone_at_any_date  _newer_capture demotes a row only on a STRICTLY LATER gone reading,
                        which was sound while a blank row could never be more than "please
                        confirm". Gallery Group's Lot 12 Wildey Street, Raceview QLD
                        ($1,074,966) is read "On Hold" on 09/09 and blank on 17/09, so the
                        blank is the LATER capture and nothing demotes it. Offered to
                        confirm that was survivable; recommended it is not. So for
                        promotion only, a live capture of the same lot that says it has
                        gone stops it whichever day it was read.
      line_truncated    text_marks_unavailable reads the text we stored, and lot_address is
                        cut at 110/120 characters. A Rockdale NSW line ends
                        "... $1,250,000 Under c" -- "Under contract", cut mid-word, so the
                        check saw nothing. When the untruncated source_text is missing and
                        lot_address sits on the cut, the row's own status may be past the
                        end, and a row we cannot fully read is not recommended.

    Neither changes the confirm path: a row refused here falls back to "Needs confirming"
    exactly as it did before, so this can only narrow what is newly shown.
    """
    key = capture_key(row)
    if key is not None:
        for _when, avail, _price, _line in prepared.captures.get(key, ()):
            if avail in NOT_AVAILABLE:
                return "gone_at_any_date"
    price = _num(row.get("price"))
    line = str(row.get("line_key") or "")
    doc = str(row.get(_DOC_KEY) or "").strip()
    if doc and line and price:
        for _when, avail, other_price in prepared.doc_lines.get((doc, line), ()):
            if (avail in NOT_AVAILABLE and other_price
                    and abs(other_price - price) <= price * NEWER_CAPTURE_PRICE_TOLERANCE):
                return "gone_at_any_date"
    if not str(row.get("source_text") or "").strip():
        if len(str(row.get("lot_address") or "")) >= _TRUNCATED_AT:
            return "line_truncated"
    return None


def admission_refusal(row, prepared, today=None):
    """(reason or None, notes): may this UNSTATED row be admitted to confirm?

    EXCEPTIONS-ONLY LISTS ONLY. Some price lists mark the lots that have gone and leave the
    rest blank -- Silkwood's Google Sheet is 245 blank and 5 On Hold. A blank there may be
    for sale; a blank on a list that says "Available" beside its other lots is a real gap.
    Until Colin decides whether such blanks count as available (plan DECISIONS), they are
    shown only to confirm, and only when every one of these holds:

      no_document                  the row names a document
      document_states_available    ...which has no live row stated available
      document_marks_no_exceptions ...and marks at least one row gone (a superseded capture
                                   counts: the Gallery list's 11 On Hold rows dropped off)
      text_marks_unavailable       the row's own text does not say it has gone ("Under
                                   contract" in a cell the status vocabulary missed)
      no_price_evidence            the snapshot carries price evidence for the row
      price_not_in_text            the stored price is printed on the row's own line
      larger_total_in_text         nothing larger than price + 2% is printed on it (the
                                   South Ripley $725,000 is half of $1,505,615)
      build_only / land_only / commercial  the price is for a home and land (price_covers:
                                   the snapshot's price_kind, and the row's own land and
                                   build columns)
      ...then every confirm_verdict reason, including the 45-day limit.
    """
    doc = str(row.get(_DOC_KEY) or "").strip()
    if not doc:
        return "no_document", []
    live_available, marked_gone = prepared.doc_statements.get(doc, (0, 0))
    if live_available:
        return "document_states_available", []
    if not marked_gone:
        return "document_marks_no_exceptions", []
    if text_marks_unavailable(row):
        return "text_marks_unavailable", []
    if all(row.get(f) is None for f in ("price_kind", "text_price_max", "price_in_text")):
        return "no_price_evidence", []
    if not _truthy(row.get("price_in_text")):
        return "price_not_in_text", []
    price = _num(row.get("price")) or 0.0
    printed = _num(row.get("text_price_max"))
    if printed is None or printed > price * (1 + TEXT_PRICE_TOLERANCE):
        return "larger_total_in_text", []
    kind = price_covers(row)
    if kind in NOT_ADMITTABLE_PRICE_KINDS:
        return kind, []
    return confirm_verdict(row, prepared, today)


def _is_admitted(package):
    return "availability_unstated" in ((package or {}).get("confirm") or ())


def _keeps_confirm_slot(package):
    """Which packages the only_admitted pool keeps: the admitted ones, and the PROMOTED
    ones as a safety net.

    The two pools have separate MAX_CANDIDATES caps, and promotion moves a row out of a
    pool with room into the confirmed pool, which on the live snapshot is already full.
    Measured before this line existed: an any-state search lost 10 lots to nowhere -- not
    shortlisted because the cap truncated them, and not offered either, because promotion
    had taken them out of the confirm pool. Keeping them here means the worst the cap can
    do is put a lot back under "Needs confirming", where it used to be.

    Nothing is shown twice: research.py's needs-confirming merge drops everything already
    on the shortlist, by content_hash, by candidate key and by _same_listing.
    """
    return _is_admitted(package) or bool(
        (package or {}).get("availability_by_exception_list"))


class _BriefContext:
    """What build_packages reads from a brief, parsed once per brief rather than per row."""

    __slots__ = ("state", "want_product", "want_incentive", "budget_max", "ceiling",
                 "floor", "storeys_max", "storey_is_binding", "binding_facts", "admit")

    def __init__(self, brief_dict, admit=()):
        self.state = str(brief_dict.get("state") or "").strip().upper()
        # What the client is buying, as a SET — Colin, 17 Aug: "if I choose townhouse and
        # house both as a property type and then let's search". A single value could not
        # express the brief he actually had. Accepts a list or one string so an older
        # caller (server.py, a saved brief) keeps working.
        _raw_product = brief_dict.get("product_type")
        if isinstance(_raw_product, (list, tuple, set)):
            self.want_product = {str(p).strip().lower() for p in _raw_product
                                 if str(p).strip()}
        else:
            _one = str(_raw_product or "").strip().lower()
            self.want_product = {_one} if _one else set()
        self.want_incentive = bool(brief_dict.get("requires_incentive"))
        self.budget_max = _num(brief_dict.get("budget_max")) or 0.0
        self.ceiling = self.budget_max + BUDGET_HEADROOM if self.budget_max else None
        self.floor = _num(brief_dict.get("budget_min")) or 0.0
        # storeys_max < 2 is the only brief that a missing storey figure could slip past,
        # because any real house-and-land storey value (1 or 2) satisfies a max of 2.
        self.storeys_max = _int(brief_dict.get("storeys_max"))
        self.storey_is_binding = self.storeys_max is not None and self.storeys_max < 2
        # Only demand a fact the client's brief actually depends on. This gate used to
        # require all four of REQUIRED_FACTS from every row regardless of the brief, and
        # house size is recorded on 18% of stock — so 675 of VIC's 2,690 listings were
        # discarded for a missing house size even when the brief set NO house-size
        # minimum. Together with the other gates that left every VIC brief, at every
        # budget and every radius, returning exactly zero results. Same reasoning as
        # storey_is_binding.
        self.binding_facts = {field for field, _ in REQUIRED_FACTS
                              if (_num(brief_dict.get(BRIEF_MINIMUM_FOR[field])) or 0) > 0}
        unknown = set(admit or ()) - set(ADMITTABLE_GATES)
        if unknown:
            raise ValueError("cannot admit past %s; admittable: %s"
                             % (sorted(unknown), list(ADMITTABLE_GATES)))
        self.admit = frozenset(admit or ())

    def in_range(self, price):
        """Priced inside the brief's own range: no headroom, both ends inclusive."""
        if not price or price <= 0:
            return False
        if self.floor and price < self.floor:
            return False
        return not self.budget_max or price <= self.budget_max


# Every gate a row can stop at, in the order _gate asks. A row that passes all of them
# becomes a package (PASSED); what happens to it after that -- merged with the same lot
# from another channel, cut by the candidate cap, or scored -- is decided over the whole
# pool, so explain_row reports it from the same pass build_packages makes.
GATE_ORDER = (
    "superseded", "state_unknown", "other_state", "other_product_type", "no_incentive",
    "price_unverified", "not_available", "lot_page_gone", "availability_unstated",
    "no_price", "over_budget", "under_minimum", "no_suburb", "suburb_not_a_locality",
    "incomplete_facts",
)
PASSED = "package"
POOL_OUTCOMES = ("same_listing_collapsed", "truncated", "scored")

# Gates that decide scope rather than suitability. Rows stopped here are not "in the
# requested state", so they are left out of the in-range tally.
_OUT_OF_SCOPE = frozenset(("superseded", "state_unknown", "other_state"))

# The in-range tally's name for a row that passed every gate before scoring. It includes
# stale rows (sent on as unverified) and rows available by the sheet: the name answers
# "was it stopped before scoring", not "is it confirmed available".
IN_RANGE_PASSED = "available"

# Gates a caller may ask to see past. Only availability_unstated: a row whose source did
# not say whether it is for sale can be shown to a consultant to confirm. A SOLD row
# cannot, and a price that cannot be trusted cannot be confirmed by phoning anyone.
ADMITTABLE_GATES = ("availability_unstated",)


# (code, package, tags, detail, seen_at) for one row -- see _gate. A plain namedtuple
# holding the lists _gate built, uncopied: it is made once per row of every search.
GateResult = namedtuple("GateResult", ("code", "package", "tags", "detail", "seen_at"),
                        defaults=(None,))


def _age_days(row, today=None):
    """Whole days since the row's date_checked, or None when it has no readable date.

    Separate from _fresh on purpose: _fresh's (verified, seen, raw) is read by
    _price_unverified and by the same-listing collapse's ranking, and changing its shape
    breaks both. Reuses its parser so one place understands the two date formats.
    """
    today = today or _now_aest()
    _verified, seen, _raw = _fresh(row, today)
    if seen is None:
        return None
    return (today - seen).days


def _gate(ctx, row, prepared, today):
    """Every gate build_packages applies to one row, in the order it has always applied
    them. Returns GateResult(code, package or None, tags, detail, seen_at):

      code     the first gate that stopped the row (GATE_ORDER), or PASSED;
      package  the candidate package when code is PASSED, else None;
      tags     counters the row increments WITHOUT being stopped (state_borrowed,
               estate_only_place, available_by_the_sheet, storey_unknown_and_binding,
               stale_unverified, stale_over_limit, newer_capture_demoted), in the order
               the loop always incremented them;
      detail   what the deciding gate saw -- the only per-row record of a rejection;
      seen_at  the parsed capture date, which the same-listing collapse ranks on.

    Under admit=('availability_unstated',) an unstated row goes on through every later
    gate and is admitted only if admission_refusal allows it; a refused row stops as
    availability_unstated with detail['not_admitted'] naming why.

    THE ONE PATH. build_packages and explain_row both call this, so the answer to "why
    was this listing not shown" can never come from a second copy of the rules that has
    drifted from the first.
    """
    tags = []
    detail = {}
    # An older capture of a lot we also hold fresher. The dashboard has hidden these
    # by default since they were identified, but the recommendation engine was still
    # scoring all 979 of them — so the one place where a stale price actually reaches
    # a client, the shortlist, was the one place that ignored the flag.
    if row.get("superseded_by"):
        return GateResult("superseded", None, tags,
                          {"superseded_by": row.get("superseded_by")})
    row_state = str(row.get("state") or "").strip().upper()
    if not row_state:
        # BORROW THE STATE BEFORE DROPPING THE ROW FOR NOT HAVING ONE.
        #
        # This borrow was written 70 lines further down, next to the suburb borrows,
        # where it could never run: every row it targets had already been dropped
        # here. "CC-0114, Bald Hills" and "CC-0108, One Mile" name real Queensland
        # suburbs and were invisible to every search purely because their own row
        # does not say QLD, while every other row on the same price list does.
        #
        # It has to be here, before the drop, and before the suburb rules further
        # down — all of which are keyed by state and none of which can run without
        # one.
        # None is the containment pass — an estate spelled longer here than where its
        # state is known. Last, because every named field above is an exact claim.
        for _field in ("estate_name", "source_doc", "source_url", "builder_name", None):
            _borrowed = (
                _state_by_estate_containment(row, prepared.containment) if _field is None
                else prepared.state_of.get(
                    (_field, str(row.get(_field) or "").strip().lower())))
            if _borrowed:
                # A BORROWED STATE MUST NOT COST THE ROW ITS OWN SUBURB.
                #
                # This borrow exists for rows that have nothing; it must not take
                # from rows that have something. The eight West End Commercial
                # tenancies store the suburb "West End" and no state, which resolves
                # on its own as unchecked-but-usable. One of the eight also carries
                # NSW -- wrongly; there is no West End in New South Wales -- and that
                # single row was enough to map the whole document to NSW, after which
                # "West End" failed the locality check and seven rows that had an
                # answer had none. A state is only worth borrowing if the row is no
                # worse for it.
                _before = suburb_quality.resolve(dict(row, suburb_source=""))[1]
                _after = suburb_quality.resolve(
                    {**row, "state": _borrowed, "suburb_source": ""})[1]
                if (suburb_quality.is_searchable(_before)
                        and not suburb_quality.is_searchable(_after)):
                    continue                  # try the next field, or borrow nothing
                # AND DROP THE GRADE THE ROW ARRIVED WITH. suburb_source was decided
                # at build time under the facts of that moment — for these rows,
                # "no state recorded, so the value cannot be checked". resolve()
                # honours a carried reason, and would keep honouring that one after
                # the state it complains about has just been supplied, so the borrow
                # would buy nothing. Cleared, the row is graded again from scratch.
                row = {**row, "state": _borrowed, "suburb_source": ""}
                row_state = _borrowed
                tags.append("state_borrowed")
                detail["state_borrowed_from"] = _field or "estate containment"
                break
    # AN ESTATE THAT SITS IN EXACTLY ONE PLACE, for the rows the maps above cannot
    # key. Both suburb maps need (estate, STATE), so a row whose state is missing or
    # wrong is unreachable by either -- and 42 rows are in that position with nothing
    # else left. Hattan's ten "Penrose Place" lots say NSW where the estate's other
    # ten say VIC and name Officer; their state_source is 'suburb', so the state was
    # derived from the suburb column, and that column holds the estate name. Runs
    # only where the row's own state is absent or rests on that refused value.
    if _estate_is_the_only_evidence(row):
        _sub, _why = suburb_quality.resolve(row)
        if not suburb_quality.is_searchable(_why):
            _only = prepared.unambiguous_estate.get(
                (str(row.get("builder_name") or "").strip().lower(),
                 str(row.get("estate_name") or "").strip().lower()))
            if _only and _only[0]:
                row = {**row, "state": _only[0], "suburb": _only[1],
                       "suburb_source": ""}
                row_state = _only[0]
                tags.append("estate_only_place")
                detail["estate_only_place"] = [_only[0], _only[1]]
    if not row_state:
        return GateResult("state_unknown", None, tags, detail)
    detail["state"] = row_state
    if ctx.state and row_state != ctx.state:
        return GateResult("other_state", None, tags, detail)

    # A row that STATES a different product is rejected; a row that states none is
    # not. That asymmetry is the same rule the bedroom, bathroom and house-size gates
    # already follow, and it exists because inventing a requirement is the more
    # expensive error here: 1,826 of 5,879 live rows record no product_type at all,
    # so rejecting the unstated ones would discard a third of the catalogue to
    # enforce a preference their source never contradicted.
    #
    # It still does the job Colin needed on 5 Aug — "Land" is stated on the 145 rows
    # that are vacant blocks, so a house brief no longer ranks one of them first.
    if ctx.want_product:
        row_product = product_type_of(row).lower()
        # AN UNKNOWN TYPE NO LONGER PASSES. It used to, deliberately: 1,853 of 6,127
        # rows stated no type, and rejecting them discarded a third of the catalogue to
        # enforce a preference their source never contradicted.
        #
        # Inference has since settled all but ~2%, and the old rule is now the bug
        # rather than the guard: measured 17 Aug, Apartment / Townhouse / Villa / Land
        # each returned the SAME 19 rows, every one type-unknown and none of them the
        # requested type. That is Colin's complaint word for word — "it's still giving
        # us the same options regardless of what you write" — surviving inside the fix
        # meant to end it. A filter that cannot exclude is not a filter.
        if not row_product or row_product not in ctx.want_product:
            detail["product_type"] = row_product
            return GateResult("other_product_type", None, tags, detail)

    # UNLIKE every other filter here, an unstated value is EXCLUDED — and the
    # asymmetry is deliberate. "Show me packages with a rebate" is a request for rows
    # that positively carry one; a row whose source never mentioned an incentive is
    # not a maybe, it is a no. Including the silent ones would return the whole
    # catalogue and answer nothing.
    #
    # Only 80 of 5,879 live rows carry one, so this narrows hard on purpose. That is
    # the point — Colin was hunting value-driven packages for the Farirais and had no
    # way to find them.
    if ctx.want_incentive:
        has_inc = ((_num(row.get("incentive_amount")) or 0) > 0
                   or str(row.get("incentive_text") or "").strip())
        if not has_inc:
            return GateResult("no_incentive", None, tags, detail)

    if _price_unverified(row):
        detail["source_channel"] = str(row.get("source_channel") or "")
        detail["date_checked"] = str(row.get("date_checked") or "")
        return GateResult("price_unverified", None, tags, detail)
    avail = str(row.get("availability_status") or "").strip().lower()
    if avail in NOT_AVAILABLE:
        detail["availability_status"] = str(row.get("availability_status") or "")
        return GateResult("not_available", None, tags, detail)
    # THE BUILDER HAS TAKEN THE LOT'S OWN PAGE DOWN. Coleen, 26 Sep 2026: the #1, #2 and #6
    # results of "SA, any price" opened Kenekt's "unable to find the page" -- lots read from
    # E-Agent on 24 Sep that had gone since. link_check.py asks every lot page each night;
    # a 404/410 is the site saying the page no longer exists, so the lot is not offered,
    # not even to confirm: a card whose own link is dead is not something to send a client.
    # Never raised on a timeout or a 403 -- those are "could not tell", not "gone".
    if str(row.get("lot_page_gone") or "").strip():
        detail["lot_page_gone"] = str(row.get("lot_page_gone"))
        detail["lot_page_status"] = str(row.get("lot_page_status") or "gone")
        return GateResult("lot_page_gone", None, tags, detail)
    by_the_sheet = False
    by_exception_list = False
    admitting = False
    if avail not in IS_AVAILABLE:
        # A SHEET WITH NO STATUS COLUMN IS ITSELF THE AVAILABILITY STATEMENT.
        #
        # Blank means two different things and this told them apart the wrong way.
        # The Irving Domain price list — Box Hill, 6 apartments at $450k-$499k —
        # prints Residence, Bedrooms, Bathrooms, Internal, External, Total, Car,
        # Price, Carpark, OC. There is no status column anywhere on it, because the
        # vendor lists what is for sale and deletes a unit when it sells. Being on
        # the list IS the signal. Coleen searched Box Hill under $500k on 26 Aug and
        # got nothing while all six sat in the catalogue, priced and current.
        #
        # So the question is not "is this cell blank" but "does this document ever
        # state availability". If it does and THIS row is blank, the cell is a real
        # gap and the row stays out — 569 rows, unchanged. If the document is silent
        # throughout, the sheet is the list — 1,068 rows, recoverable. A row with no
        # document key cannot be told apart either way and stays out (57).
        #
        # Counted separately and flagged on the entry rather than quietly folded in
        # with the stated ones, because a consultant putting this in front of a buyer
        # should know the source never said the word.
        doc = str(row.get(_DOC_KEY) or "")
        if doc and doc not in prepared.states_availability:
            by_the_sheet = True
            tags.append("available_by_the_sheet")
        else:
            # Unstated on a document that states it elsewhere, or unattributable.
            # See IS_AVAILABLE for why "unstated" is not "available".
            detail["availability_status"] = str(row.get("availability_status") or "")
            detail["document_states_availability"] = bool(doc)
            # EXCEPTIONS-ONLY LIST, READ RECENTLY: a result, not a footnote. Decided here
            # so BOTH passes agree and no lot is offered twice -- the main pass shows it
            # and the shortlist shows it. The only_admitted pass KEEPS it as well -- see
            # _keeps_confirm_slot, added after the two pools' separate caps were measured
            # -- and research.py drops whatever is already on the shortlist, so it is
            # never shown twice and the cap can only send it back to "Needs confirming".
            # Stale rows fall through to the old path and still go to "Needs confirming".
            if EXCEPTIONS_LIST_MODE == "available" and _fresh(row, today or _now_aest())[0]:
                admitting = True
                by_exception_list = True
            elif "availability_unstated" not in ctx.admit:
                return GateResult("availability_unstated", None, tags, detail)
            else:
                # ASKED FOR, NOT YET ADMITTED, AND NEVER APPROVED. The caller wants rows a
                # consultant could confirm. Whether this is one (admission_refusal) is
                # decided once it has passed every later gate, so the refusals counted are
                # refusals of rows that match the brief -- not of the over-budget majority.
                admitting = True

    price = _num(row.get("price"))
    if not price or price <= 0:
        detail["price"] = row.get("price")
        return GateResult("no_price", None, tags, detail)
    if ctx.ceiling is not None and price > ctx.ceiling:
        detail["price"] = price
        detail["ceiling"] = ctx.ceiling
        if ctx.budget_max and price <= ctx.budget_max + JUST_OVER_BAND:
            # Within $50k of the budget: still out, but counted, so a thin search can
            # say how much stock the client's own figure is costing them.
            detail["just_over"] = True
        return GateResult("over_budget", None, tags, detail)
    # A PRICE RANGE, NOT JUST A CEILING. Colin and Coleen search a bracket -- "three
    # to four hundred" -- and with only a maximum the cheap end of the state filled
    # the answer every time. A minimum is a real exclusion, unlike the preferred
    # price, which only ranks; the floor is exact, with no headroom, because a
    # consultant who types a minimum has ruled that price out rather than nearly
    # ruled it out.
    if ctx.floor and price < ctx.floor:
        detail["price"] = price
        detail["floor"] = ctx.floor
        return GateResult("under_minimum", None, tags, detail)

    # ONE ANSWER ABOUT WHAT COUNTS AS A PLACE, shared with the published snapshot.
    #
    # This gate used to be three steps written out here — display_suburb, then
    # clean_locality, then a bare-else that dropped the row — while build_web.py ran
    # only the first of them. So a row could be excluded from every search as "not a
    # recognised locality" and still be published to the dashboard with that same
    # value printed as its suburb. suburb_quality.resolve is now the single answer
    # both paths call, for the reason clean_locality was centralised in the first
    # place: two copies of this judgement drift, and did.
    #
    # PRESENT is not the same as REAL. Those rows were not merely stored, they were
    # RECOMMENDED: a QLD search returned "Lot 507, 2026 in 2026, QLD" and "Lot 507,
    # offer in offer, QLD" as four of its five results. A listing whose location
    # cannot be confirmed must not reach a client, and cannot be geocoded,
    # distance-filtered or benchmarked either.
    suburb, suburb_why = suburb_quality.resolve(row)
    if not suburb_quality.is_located(suburb_why):
        state_key = str(row.get("state") or "").strip().upper()
        for group_map, field in ((prepared.estate_suburb, "estate_name"),
                                 (prepared.page_suburb, _DOC_KEY)):
            borrowed = group_map.get(
                (str(row.get(field) or "").strip().lower(), state_key))
            if borrowed:
                row = {**row, "suburb": borrowed}
                suburb, suburb_why = suburb_quality.resolve(row)
                if suburb_quality.is_located(suburb_why):
                    break
    # SEARCHABLE, not LOCATED. A listing whose suburb we inferred from its own text
    # is real stock at a real price, and dropping it here is exactly what made Coleen
    # report properties as missing that the app had. It comes through, carrying
    # suburb_source so the caveat travels with it.
    #
    # is_located is still the gate everywhere the position has to be TRUE — distance
    # filtering, benchmarking and comparables all ask that question separately and
    # still refuse these rows. A wrong suburb costs a noisier search; a wrong
    # comparable costs a client a wrong valuation.
    if not suburb_quality.is_searchable(suburb_why):
        # SHOWN is not LOCATED. A value can be a real locality name and still be
        # unusable here: with no state recorded there is nothing to check it against,
        # geo.locate needs both halves, and a fifth of Australian locality names
        # exist in more than one state. Those rows stay visible in the Building Stock
        # table with the caveat; they must not be scored, distance-filtered or
        # benchmarked.
        detail["suburb"] = str(row.get("suburb") or "")
        detail["suburb_source"] = suburb_why
        if suburb_why == suburb_quality.BLANK_ABSENT:
            # Without a locality the row cannot be geocoded, so no distance and no
            # benchmark can be established for it.
            return GateResult("no_suburb", None, tags, detail)
        return GateResult("suburb_not_a_locality", None, tags, detail)

    facts = {}
    blocking, unstated = [], []
    for field, label in REQUIRED_FACTS:
        value = _int(row.get(field)) if field != "house_sqm" else _num(row.get(field))
        facts[field] = value
        if value is not None:
            continue
        (blocking if field in ctx.binding_facts else unstated).append(label)
    if blocking:
        # The client set a minimum for this and the listing does not say — it cannot
        # be judged against their requirement, so it is excluded and named.
        detail["missing"] = blocking
        return GateResult("incomplete_facts", None, tags, detail)
    # Nothing in the brief turns on these. Scored, with the gap on the record so the
    # report can say "not stated" instead of implying a figure.
    detail["unstated"] = unstated

    today = today or _now_aest()
    admitted = []
    guard, notes = None, []
    if admitting:
        # EXCEPTIONS-ONLY LISTS, PRICES THE LINE ITSELF PRINTS, NO SOLD-STOCK EVIDENCE.
        # Colin's QLD <= $750k search has six in-range unstated rows and admits none of
        # them: two Build Only prices, a garbled merged line printing 45000000, a Wacol lot
        # its own sheet was re-read without on 16 Sep, and two duplex halves of $1.3m-$1.5m
        # totals on a list that states "Available" beside its other lots.
        refusal, notes = admission_refusal(row, prepared, today)
        if refusal:
            detail["not_admitted"] = refusal
            return GateResult("availability_unstated", None, tags, detail)
        promo = promotion_refusal(row, prepared) if by_exception_list else None
        if promo:
            # Good enough to confirm, not good enough to recommend.
            by_exception_list = False
            detail["not_promoted"] = promo
            tags.append("not_promoted_" + promo)
            if "availability_unstated" not in ctx.admit:
                # AND THE MAIN PASS ADMITS NOTHING -- so it leaves by the same door it
                # would have used with the mode off. Without this return it fell through
                # to the `else` below and was ADMITTED into the confirmed pool, which no
                # caller asked for: the main pass runs with ctx.admit empty, and _pass
                # raises ValueError elsewhere precisely to keep the two pools apart.
                #
                # It cost 42 lots. On a brief with no ceiling, 89 promotion-refused rows
                # (88 line_truncated, 1 gone_at_any_date) entered the confirmed pool
                # carrying availability_by_exception_list=False, so the promoted hold-out
                # below -- which tests that flag -- did not hold them out. Priced
                # $832,201-$1,105,539 they sat under the 600th price, dropping the cut
                # from $1,171,900 to $1,113,900 and pushing 60 stated rows off the end,
                # 42 of them verified. Every slot stolen was taken by a REFUSED row;
                # not one promoted row was involved, which is why sorting promoted rows
                # to the back never moved the number.
                return GateResult("availability_unstated", None, tags, detail)
        if by_exception_list:
            # PROMOTED. It passed every check admission_refusal makes, cleared
            # promotion_refusal, and was read inside FRESH_DAYS, so it is shown as a
            # result. `admitted` is left empty on purpose -- _is_admitted is what makes a
            # package unverified -- but the confirm pool still KEEPS it (see
            # _keeps_confirm_slot): the two pools have separate 600-row caps, and without
            # that a promoted row the main cap truncated would appear nowhere at all.
            # research.py drops anything already on the shortlist from "Needs confirming",
            # by content_hash, candidate key and _same_listing, so it is never shown twice.
            tags.append("available_by_exception_list")
        else:
            admitted.append("availability_unstated")
            detail["admitted"] = list(admitted)

    storeys = _storeys(row)
    if storeys is None and ctx.storey_is_binding:
        # Counted, but no longer dropped. This exclusion existed because
        # CandidateProperty.storeys was a plain int that defaulted to 1, so an
        # unrecorded storey silently satisfied a "single storey only" brief. storeys is
        # Optional now and the scorer flags None instead of assuming it, so the row can
        # be scored honestly — and it has to be, because storey is stated on 149 of
        # 4,192 rows and dropping the rest made a single-storey brief return NOTHING:
        # all 33 lots that met every other requirement were thrown away here.
        tags.append("storey_unknown_and_binding")

    verified, seen_at, seen_on = _fresh(row, today)
    # _age_days, without parsing the date a second time for every package.
    age_days = (today - seen_at).days if seen_at is not None else None
    # WHAT A CONSULTANT WOULD HAVE TO CONFIRM, and whether the catalogue already says the
    # answer is no. The main path is unchanged by any of this except the last branch: a
    # stale row is still sent unverified and becomes Pending, and confirm/confirm_guard
    # only say whether "Needs confirming" may show it.
    confirm = list(admitted)
    if not verified:
        tags.append("stale_unverified")
        confirm.append("stale")
        if not admitted:              # an admitted row's verdict was taken on admission
            guard, notes = confirm_verdict(row, prepared, today)
        confirm_age = _confirm_age_days(row, today)
        if confirm_age is not None and confirm_age > CONFIRM_MAX_DAYS:
            tags.append("stale_over_limit")
    elif not admitted and _newer_capture(row, prepared, seen_at) == "newer_capture_disagrees":
        # DEFENCE IN DEPTH, ON THE MAIN SHORTLIST TOO. A fresh "Available" row whose own
        # lot was read again LATER as On Hold or Sold is not verified by being fresh: Avia
        # lot 57, The Hills Collection, read Available on 03/09 and On Hold on 16/09. The
        # supersede pass should retire these where they are written; until it catches
        # every shape, the search does not recommend them.
        verified = False
        confirm.append("newer_capture")
        guard = "newer_capture_disagrees"
        tags.append("newer_capture_demoted")
    confirm.extend(n for n in notes if n not in confirm)
    email = _is_email(row)
    email_first_seen = str(row.get("email_first_seen") or "").strip() if email else ""
    first_seen_on = _parse_day(email_first_seen) if email_first_seen else None

    builder = str(row.get("builder_name") or "").strip()
    # A project-scope row's name is a DEVELOPMENT, not a builder — E-Agent lists
    # apartments and townhouses by development, and for those rows the name came off a
    # price-list file name or a project card, never from a builder. Passing it through
    # as the builder made the scorer hand "Waler Heights" a builder_confidence_rating
    # of MEDIUM and printed "Builder: Waler Heights" on a client card. Of the 75 rows
    # in the database that are scoreable at all, 43 are project-scope, so this was the
    # majority of the shortlist, not an edge case.
    scope = str(row.get("attribution_scope") or "").strip().lower()
    development = builder if scope == "project" else ""
    if scope == "project":
        builder = ""
    if not builder:
        # NOT a guess and NOT an empty string: BuilderRegistry.search_builder_by_name
        # does `query in registry_name`, so an empty query matches the FIRST builder
        # in the directory and would attribute the lot to them.
        builder = "Builder not identified in source"

    listing_ref = (str(row.get("listing_url") or "").strip()
                   or str(row.get("brochure_url") or "").strip())
    package = {
        # Carried so a consultant's selection can outlive the search that produced it
        # — see the note on SNAPSHOT_FIELDS. Never rendered; it is an identity, not a
        # fact about the property.
        "content_hash": str(row.get("content_hash") or "").strip(),
        # True when this row is on a price list that never states availability, so
        # its being listed is the only evidence it is still for sale. The row is
        # recommended — see the filter above for why — but a consultant should know
        # the source did not say the word before quoting it to a buyer.
        "availability_by_the_sheet": by_the_sheet,
        # True when this row was blank on a price list that marks ONLY the lots that have
        # gone (Silkwood's 245 blank / 5 On Hold sheet). Being on the list is the evidence
        # it is still for sale, and the card says so. See EXCEPTIONS_LIST_MODE.
        "availability_by_exception_list": by_exception_list,
        # The rebate, as the source stated it. Coleen's target one-pager has a "Rebate"
        # and an "Effective Price" column ($728,500 package - $10,000 rebate =
        # $718,500), and neither could be filled because these two never left the
        # snapshot reader — the pipeline used incentives only to FILTER
        # (want_incentive), never to report. Effective price is derived at render time
        # rather than stored, so it cannot drift from the two numbers behind it.
        "incentive_amount": _num(row.get("incentive_amount")) or 0.0,
        "incentive_text": str(row.get("incentive_text") or "").strip(),
        "builder_name": builder,
        # The label a client reads. Prefer the street address recovered from the
        # row's own text (stocklist_reparse), because on 47% of stock lot_address is
        # the entire flattened spreadsheet row and stripping the prices out of it
        # still leaves a jumble. Falls back to the cleaned lot_address where no
        # street was stated. Never invents and never empties.
        "lot_address": (_display_address(row) or "(no address in source row)"),
        # THE LOT AS THE SOURCE STATED IT, beside the address rather than inside it. On
        # thousands of rows the address a client reads is only a street address -
        # "5532 Verday Blvd, Jubilee" - and Clare's availability check
        # (vapi-kommo-hook, lib/tools/stock-match.js) may only confirm a lot it can see,
        # so without this a caller asking about lot 5532 could never be matched. The raw
        # value, stripped: a stock code stays a stock code, a blank stays blank, and it
        # is never read out of the address, because a street number is not a lot.
        "lot_number": str(row.get("lot_number") or "").strip(),
        "suburb": suburb,
        # HOW WE KNOW IT. Carried so the grade cannot die at this boundary: without
        # it api/research.py rebuilds a bare {suburb, state, ...} dict for the
        # comparables gate, suburb_quality.resolve() on that dict has only a locality
        # name to go on and returns STATED, and the gate meant to keep GUESSED
        # locations out of a client's comparables can never reject anything.
        "suburb_source": suburb_why,
        "state": row_state,
        # The development keeps its name here, where it is true, instead of standing in
        # for a builder. Falls back to the estate for builder-scope rows.
        "developer_name": development or str(row.get("estate_name") or "").strip(),
        "house_design": str(row.get("product_type") or "").strip(),
        "estate_context": str(row.get("estate_name") or "").strip(),
        "bedrooms": facts["bedrooms"],
        "bathrooms": facts["bathrooms"],
        "car_spaces": facts["car_spaces"],
        # None, not 0 and not the pipeline's 1. A max-storeys filter must not be
        # satisfied by a number nobody recorded — but 0 satisfied it silently just as 1
        # would have, so the "confirm the storey count" flag never appeared on the card.
        # CandidateProperty.storeys is Optional now and the scorer names the gap.
        "storeys": storeys,
        # 0 costs land points; the pipeline's 400 m² default would invent them.
        "land_size_sqm": _num(row.get("land_sqm")) or 0.0,
        "house_size_sqm": facts["house_sqm"],
        "advertised_package_price": price,
        "land_price": _num(row.get("land_price")) or 0.0,
        "build_price": _num(row.get("build_price")) or 0.0,
        "title_status": str(row.get("title_status") or "").strip() or "Not stated in source",
        "expected_title_date": "",          # never invented ("Expected Q4 2026")
        "estimated_rent_weekly_min": 0.0,   # no rent is captured for stored stock
        "estimated_rent_weekly_max": 0.0,
        "amenities_summary": "No amenity assessment captured for this listing.",
        "source_channel": "%s - database snapshot" % (
            str(row.get("source_channel") or "stored stock").strip()),
        "source_url_or_ref": listing_ref or ("stored stock, captured %s" % (seen_on or "date unknown")),
        # Where a consultant can go to see this listing at its source, labelled with
        # what the link actually opens. See provenance.py.
        # The RESOLVED suburb, not the stored one. This builds the string a
        # consultant pastes into the builder's own search box, and off the raw
        # column it produced "122 Crescent" and "132 Paramatta" -- a street number
        # against a street name. A term that returns an error is worse than no term,
        # which is the complaint this function exists to answer.
        "source_link": _source_link(row, suburb),
        # The day this listing was last actually seen at its source, exactly as the row
        # recorded it -- never the day of the search. The card turns this into a
        # staleness warning, which is the only thing that would have made Proxima's
        # expired sign-in visible: its rows sat three days old while every other channel
        # refreshed nightly, and nothing on screen looked wrong.
        "date_checked": seen_on or "",
        # An admitted row is never verified, however fresh: its source did not say the
        # lot is for sale, and "verified" is what lets a row into the final list.
        "verified": verified and not admitted,
        "consultant_approved": False,
        "risks": [],
        # The benchmark already computed over the whole table by
        # benchmark_buildings.py. Passed through so the client card can fall back
        # to it: BenchmarkEngine needs a CoreLogic/REA export Colin has not
        # supplied, so without this EVERY card reads "Unbenchmarked - Pending
        # Market Data" in red even though 957 listings do have a defensible
        # comparison against stock we hold.
        "stored_benchmark_median": _num(row.get("benchmark_median")),
        "stored_benchmark_variance_pct": _num(row.get("benchmark_variance_pct")),
        "stored_benchmark_classification": str(
            row.get("benchmark_classification") or "").strip(),
        "stored_benchmark_basis": str(row.get("benchmark_basis") or "").strip(),
        # Whole days since date_checked on the day of the search (None when the row has
        # no readable date), so "checked 27 days ago" is computed once, here, from the
        # same today the freshness rule used.
        "age_days": age_days,
        # AN EMAILED LIST'S OWN AGE, beside date_checked rather than instead of it. On an
        # emailed row date_checked is when the inbox sweep last re-read the message, so a
        # card should say "email last read <date_checked>" and can say the list was first
        # read <email_first_seen>, <list_age_days> days ago. Blank / None on every row
        # that did not arrive by email.
        "email_first_seen": email_first_seen,
        "list_age_days": (today - first_seen_on).days if first_seen_on is not None else None,
    }
    if confirm:
        # What a consultant must confirm before this can be sent: 'stale' (not read within
        # FRESH_DAYS), 'availability_unstated' (admitted from an exceptions-only list),
        # 'newer_capture' (demoted, see above), 'document_partially_read' (a note). Absent
        # on a verified row. confirm_guard is None when "Needs confirming" may show the
        # row, else the confirm_verdict reason it stays hidden.
        package["confirm"] = confirm
        package["confirm_guard"] = guard
    detail["verified"] = package["verified"]
    detail["date_checked"] = seen_on or ""
    detail["age_days"] = age_days
    detail["availability_by_the_sheet"] = by_the_sheet
    detail["availability_by_exception_list"] = by_exception_list
    detail["confirm"] = confirm
    detail["confirm_guard"] = guard
    return GateResult(PASSED, package, tags, detail, seen_at)


def _pass(brief_dict, rows, today=None, prepared=None, admit=(), only_admitted=False,
          keep_results=False):
    """The whole of build_packages: (packages, counts, outcome, results).

    outcome maps id(row) -> what became of a row that passed every gate
    ("same_listing_collapsed", "truncated", "scored", or "not_admitted" when
    only_admitted left it out). results is [(row, GateResult)] when keep_results.
    """
    # One today for the whole pass. It used to be read per row inside _fresh, so a search
    # running across midnight aged its first and last rows against different days.
    today = today or _now_aest()
    prepared = _checked_prepared(rows, prepared)
    ctx = _BriefContext(brief_dict, admit)
    if bool(ctx.admit) != bool(only_admitted):
        # NEVER ONE POOL. Admitted rows sharing the confirmed search's collapse and 600-row
        # cap push confirmed stock out of it: measured on the 17 Sep subset, Max blank /
        # $750k preferred, 181 rows the real search scores were cut as truncated and one
        # was merged into an unconfirmed twin. The plan's answer is a separate pass, so a
        # shared pool is refused rather than offered. And only_admitted with nothing
        # admitted would return an empty pool that reads as "nothing to confirm".
        raise ValueError("admit and only_admitted go together: admitted rows get a pool "
                         "of their own (admit=%r, only_admitted=%r)"
                         % (tuple(admit or ()), only_admitted))

    counts = {
        "snapshot_rows": len(rows), "other_state": 0, "state_unknown": 0,
        "not_available": 0, "lot_page_gone": 0, "no_price": 0, "over_budget": 0,
        "under_minimum": 0,
        "no_suburb": 0,
        "incomplete_facts": 0, "storey_unknown_and_binding": 0, "scored": 0,
        "truncated": 0, "stale_unverified": 0, "same_listing_collapsed": 0,
        "suburb_not_a_locality": 0, "superseded": 0, "availability_unstated": 0,
        "price_unverified": 0, "other_product_type": 0, "no_incentive": 0,
        "available_by_the_sheet": 0,
        # Informational, never gates (W11): over-budget rows within JUST_OVER_BAND of the
        # budget; unverified packages older than CONFIRM_MAX_DAYS; fresh packages demoted
        # because a later capture of their lot says it is gone.
        "just_over": 0, "stale_over_limit": 0, "newer_capture_demoted": 0,
    }
    missing_field_counts = {}
    unstated_but_scored = {}
    confirm_guarded = {}      # confirm_guard reason -> packages hidden from confirming
    not_admitted = {}         # admission_refusal reason -> unstated rows refused
    # THE SAME FIRST-FAIL TALLY, FOR THE ROWS THE CLIENT COULD ACTUALLY BUY.
    #
    # Availability is checked before price, so "632 whose availability the source never
    # stated" in a QLD <= $750k search is almost entirely stock over the budget: 6 of
    # those 632 are in range. Only rows in the requested state whose price sits inside
    # budget_min..budget_max (no headroom) are tallied, under the gate that stopped them
    # or IN_RANGE_PASSED. The gate order is untouched; this only counts.
    in_range = {}
    admitted_counts = {code: 0 for code in ADMITTABLE_GATES if code in ctx.admit}
    entries = []          # (seen_at, package), in row order, as the collapse expects
    entry_rows = []       # the original row object behind each entry
    entry_admitted = []
    results = [] if keep_results else None

    # Unpacked, and each lookup guarded by the only case that needs it: this runs once per
    # row of the snapshot on every search.
    admitting = bool(ctx.admit)
    for row in rows:
        result = _gate(ctx, row, prepared, today)
        code, package, tags, detail, seen_at = result
        if keep_results:
            results.append((row, result))
        for tag in tags:
            counts[tag] = counts.get(tag, 0) + 1
        if admitting:
            for admitted in detail.get("admitted") or ():
                admitted_counts[admitted] += 1
        if code == PASSED:
            for label in detail["unstated"]:
                unstated_but_scored[label] = unstated_but_scored.get(label, 0) + 1
            entries.append((seen_at, package))
            entry_rows.append(row)
            # ADMITTED, not merely carrying confirm: stale rows carry confirm=['stale'] and
            # belong to the confirmed search's pool, as they always have.
            entry_admitted.append(_is_admitted(package))
            reason = package.get("confirm_guard")
            if reason:
                confirm_guarded[reason] = confirm_guarded.get(reason, 0) + 1
        else:
            counts[code] += 1
            if code == "incomplete_facts":
                for label in detail["missing"]:
                    missing_field_counts[label] = missing_field_counts.get(label, 0) + 1
            elif code == "over_budget" and detail.get("just_over"):
                counts["just_over"] += 1
            elif code == "availability_unstated" and "not_admitted" in detail:
                reason = detail["not_admitted"]
                not_admitted[reason] = not_admitted.get(reason, 0) + 1
            if code in _OUT_OF_SCOPE:
                continue
        if ctx.in_range(_num(row.get("price"))):
            if code != PASSED:
                bucket = code
            elif _is_admitted(package):
                bucket = "admitted:availability_unstated"
            else:
                bucket = IN_RANGE_PASSED
            in_range[bucket] = in_range.get(bucket, 0) + 1

    outcome = {}
    if only_admitted:
        # The admitted rows get their OWN pool. Sharing the confirmed search's 600-row cap
        # cost 143 of 379 confirmed VIC <= $900k results when it was measured.
        entry_kept = [_keeps_confirm_slot(p) for _seen, p in entries]
        left_out = [row for row, adm in zip(entry_rows, entry_kept) if not adm]
        for row in left_out:
            outcome[id(row)] = "not_admitted"
        counts["not_admitted"] = len(left_out)
        keep = [i for i, adm in enumerate(entry_kept) if adm]
        entries = [entries[i] for i in keep]
        entry_rows = [entry_rows[i] for i in keep]

    kept = _collapse_kept(entries)
    counts["same_listing_collapsed"] = len(entries) - len(kept)
    kept_set = set(kept)
    for i, row in enumerate(entry_rows):
        if i not in kept_set:
            outcome[id(row)] = "same_listing_collapsed"
    packages = [entries[i][1] for i in kept]
    row_of_package = {id(entries[i][1]): entry_rows[i] for i in kept}
    packages.sort(key=lambda p: p["advertised_package_price"])
    if len(packages) > MAX_CANDIDATES:
        # A PROMOTED ROW NEVER EVICTS A STATED ONE, AND NEVER REORDERS THEM EITHER.
        # Held out of everything below and appended at the end, because the reorderings
        # are SELECTIONS over the whole list -- _spread_across_the_budget especially --
        # so merely sorting promoted rows to the back afterwards is not enough: their
        # presence in the input already changed which stated rows came forward. Measured
        # on the live snapshot: with them in the spread, 42 stated, verified lots dropped
        # out of one brief's 600 even though every promoted row was then cut. Held out
        # here, the stated ordering is identical to the mode being off.
        # Nothing is lost by being cut: _keeps_confirm_slot leaves a promoted row in the
        # confirm pool, so the cap can only send it back to "Needs confirming".
        promoted_tail = []
        if not only_admitted:
            promoted_tail = [p for p in packages if p.get("availability_by_exception_list")]
            if promoted_tail:
                packages = [p for p in packages
                            if not p.get("availability_by_exception_list")]
        # THE CAP USED TO CUT THE BRIEF'S OWN STOCK BEFORE THE BRIEF WAS APPLIED.
        #
        # Sorted by price ascending and truncated to the 600 cheapest -- and the suburb
        # filter runs AFTERWARDS, in scoring_engine. So the cheapest 600 listings in the
        # whole state were all a search could ever see, whatever was asked for. Measured
        # on the live snapshot: in VIC the dearest listing that reached scoring was
        # $666,800, and 2,254 were unreachable; NSW $886,500 and 2,060; QLD $1,216,360
        # and 478. About half the catalogue could not be returned by any search.
        #
        # Coleen hit it on a $845,000 Geelong townhouse and reported the symptom exactly:
        # "tried search in the app result not found, change the price to 900000, still
        # not showing". Raising the ceiling made it WORSE -- a higher budget admits more
        # cheap stock, which pushes the expensive listings further past 600.
        #
        # So the requested suburbs are kept first. The cap stays, because it is what
        # bounds the scoring matrix, but it now spends its budget on stock that can
        # actually match rather than on the cheapest thing in the state.
        wanted = {str(s).strip().lower()
                  for s in (brief_dict.get("primary_suburbs") or [])
                  if str(s).strip()}
        if wanted and not _num(brief_dict.get("search_radius_km")):
            # Same test as scoring_engine's, and deliberately so: at radius 0 it keeps
            # exactly the rows that will survive the suburb filter, and a radius search
            # is left alone because the suburb is then not the thing being matched on.
            asked = [p for p in packages if str(p.get("suburb") or "").strip().lower() in wanted]
            rest = [p for p in packages if str(p.get("suburb") or "").strip().lower() not in wanted]
            packages = asked + rest
        elif ctx.binding_facts:
            # AND WHEN NO SUBURB WAS NAMED, THE SAME IDEA APPLIED TO THE REST OF THE BRIEF.
            #
            # The block above rescued a search that names its suburbs. A state-wide search
            # -- "anything in VIC under $900,000 with four bedrooms" -- has no suburbs, so
            # `wanted` is empty, nothing is reordered, and the 600 CHEAPEST win again.
            #
            # Measured on the live snapshot: that brief has 1,336 candidates, 784 of which
            # genuinely have four bedrooms. The cheapest 600 contain just 271 of them, so
            # 511 matching homes cannot be returned by any search and the scorer never
            # sees a four-bedroom home above $714,190. The client said they would pay
            # $900,000.
            #
            # A row that already fails a minimum the consultant typed is the cheapest
            # thing to give up: scoring_engine will hard-reject it on arrival. So the
            # budget is spent on rows that satisfy the stated minimums first. The sort is
            # STABLE, so price-ascending order survives inside each group.
            packages.sort(key=lambda p: -_meets_binding(p, brief_dict, ctx.binding_facts))
            packages = _spread_across_the_budget(packages, brief_dict, ctx.binding_facts)
        packages = packages + promoted_tail
        counts["truncated"] = len(packages) - MAX_CANDIDATES
        for p in packages[MAX_CANDIDATES:]:
            outcome[id(row_of_package[id(p)])] = "truncated"
        packages = packages[:MAX_CANDIDATES]
        packages.sort(key=lambda p: p["advertised_package_price"])
    for p in packages:
        outcome[id(row_of_package[id(p)])] = "scored"
    counts["scored"] = len(packages)
    counts["missing_fields"] = missing_field_counts
    counts["unstated_but_scored"] = unstated_but_scored
    counts["counts_in_range"] = in_range
    counts["confirm_guarded"] = confirm_guarded
    if ctx.admit:
        counts["admitted"] = admitted_counts
        counts["unstated_not_admitted"] = not_admitted
    return packages, counts, outcome, results


def build_packages(brief_dict, rows, today=None, prepared=None, admit=(),
                   only_admitted=False):
    """(packages, coverage) — coverage is a plain dict of counts, all reported.

    `brief_dict` is the raw brief the browser posted; only budget/state/storeys are
    read here. Everything else stays the pipeline's job.

    today          the day rows are aged against (default: now on the AEST clock).
    prepared       prepare_rows(rows), to skip the brief-independent work.
    admit          gates to see past, from ADMITTABLE_GATES. An unstated row goes on
                   through every later gate and is admitted only when admission_refusal
                   allows it (exceptions-only list, price printed and whole, no sold-stock
                   guard, within CONFIRM_MAX_DAYS); it leaves with confirm including the
                   gate and verified=False. counts['admitted'][gate] counts the admitted,
                   counts['unstated_not_admitted'][reason] the refused.
    only_admitted  return only admitted rows, in a pool (collapse and cap) of their own.
                   Required with admit, and only with admit: a confirmed search and its
                   admitted rows are two passes, never one shared pool.

    counts['counts_in_range'] repeats the first-fail tally for in-state rows priced
    inside the brief's own range. Every package carries age_days, email_first_seen and
    list_age_days; an unverified one also carries confirm and confirm_guard.
    counts['just_over'] counts over-budget rows within JUST_OVER_BAND of the budget.
    """
    packages, counts, _outcome, _results = _pass(
        brief_dict, rows, today=today, prepared=prepared, admit=admit,
        only_admitted=only_admitted)
    return packages, counts


def _row_in(row, rows):
    """The object in `rows` that IS this row: itself, or its unique content_hash twin
    (a row carried through a join is a copy)."""
    for r in rows:
        if r is row:
            return r
    h = str(row.get("content_hash") or "").strip()
    if not h:
        return None
    matches = [r for r in rows if str(r.get("content_hash") or "").strip() == h]
    return matches[0] if len(matches) == 1 else None


def _verdict(result, outcome_of_row):
    detail = dict(result.detail)
    detail["tags"] = list(result.tags)
    gate = result.code
    if gate == PASSED:
        gate = outcome_of_row or "passes_every_gate"
        if outcome_of_row is None:
            detail["note"] = ("the row is not in the rows searched, so the same-listing "
                              "merge and the candidate cap were not applied to it")
    return {"gate": gate, "detail": detail, "package": result.package}


def explain_row(brief_dict, row, rows, prepared=None, today=None, admit=()):
    """{gate, detail, package}: what build_packages did with ONE row, and why.

    gate is the first gate in GATE_ORDER that stopped the row, or -- for a row that
    passed them all -- what happened to its package in the pool: "scored",
    "same_listing_collapsed" or "truncated". Computed by the same _gate build_packages
    runs, so the explanation and the search cannot disagree.

    `row` is a snapshot row (stock.json shape). A row joined to database-only fields
    (join_db_fields) works too: those ride under 'db' and no gate reads them.
    """
    today = today or _now_aest()
    prepared = _checked_prepared(rows, prepared)
    ctx = _BriefContext(brief_dict, admit)
    result = _gate(ctx, row, prepared, today)
    if result.code != PASSED:
        return _verdict(result, None)
    target = _row_in(row, rows)
    if target is None:
        return _verdict(result, None)
    admitted = _is_admitted(result.package)
    # The pool the row really lands in: the confirmed search for a confirmed row, the
    # separate admitted pass for an admitted one -- the two passes a search runs.
    _packages, _counts, outcome, _results = _pass(
        brief_dict, rows, today=today, prepared=prepared,
        admit=admit if admitted else (), only_admitted=admitted)
    return _verdict(result, outcome.get(id(target)))


def explain_rows(brief_dict, rows, prepared=None, today=None, admit=()):
    """explain_row for every row. [(row, verdict)] in row order.

    One pass without admit. With admit, two -- the confirmed search and the admitted pass
    -- because that is how a search runs them, and a confirmed row's fate (scored,
    collapsed, truncated) must be reported from the pool it is really in.
    """
    today = today or _now_aest()
    prepared = _checked_prepared(rows, prepared)
    _packages, _counts, confirmed, results = _pass(
        brief_dict, rows, today=today, prepared=prepared, keep_results=not admit)
    admitted = {}
    if admit:
        _packages, _counts, admitted, results = _pass(
            brief_dict, rows, today=today, prepared=prepared, admit=admit,
            only_admitted=True, keep_results=True)
    out = []
    for row, result in results:
        fate = None
        if result.code == PASSED:
            fate = (admitted if _is_admitted(result.package) else confirmed).get(id(row))
        out.append((row, _verdict(result, fate)))
    return out


# Database columns worth having beside a snapshot row that the snapshot does not carry.
DB_ONLY_FIELDS = ("id", "source_text", "checked_at", "first_seen")


def join_db_fields(rows, db_path, fields=DB_ONLY_FIELDS):
    """Snapshot rows, each with the database's own columns for it under row['db'].

    WHY NOT READ THE DATABASE DIRECTLY. SNAPSHOT_FIELDS includes columns build_web
    derives at build time (source_doc, source_link_*, rea_*, suburb_source), so
    _read_sqlite on the raw database fails with "no such column: suburb_source", and the
    rows a search sees are the SNAPSHOT's -- states borrowed, suburbs resolved -- not the
    table's. A tool that explains a listing must explain the row the search saw. So it
    loads the snapshot (stock.json, or build_web.build on a scratch copy) and joins the
    few database-only facts it needs by content_hash.

    NAMESPACED, NOT MERGED. suburb_quality.resolve reads source_text when a row carries
    it, so a flat merge could move a suburb and make explain_row disagree with the
    search. Under 'db', no gate can see them.

    The database is opened read-only. Rows with no database match get db = None.
    """
    fields = tuple(fields)
    uri = "file:%s?mode=ro" % Path(db_path).as_posix()
    conn = sqlite3.connect(uri, uri=True)
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(buildings)")}
        missing = [f for f in fields if f not in have]
        if missing:
            raise ValueError("buildings has no column(s) %s" % ", ".join(missing))
        wanted = {str(r.get("content_hash") or "").strip() for r in rows}
        wanted.discard("")
        found = {}
        cols = ", ".join(("content_hash",) + fields)
        for rec in conn.execute("SELECT %s FROM buildings" % cols):
            if rec[0] in wanted:
                found[rec[0]] = dict(zip(fields, rec[1:]))
    finally:
        conn.close()
    return [dict(r, db=found.get(str(r.get("content_hash") or "").strip())) for r in rows]


def _spread_across_the_budget(packages, brief_dict, binding_facts):
    """Reorder so the kept slots cover the whole price range, not just its cheap end.

    Sorting matching stock to the front fixes WHICH rows are given up, but not WHERE. VIC
    has 784 four-bedroom homes under $900,000 and 600 slots, so cheapest-first still ends
    at $798,225 -- and a client who said they would pay $900,000 is shown nothing in the
    top hundred thousand of their own budget.

    A consultant is choosing between options, so the useful 600 are spread over the range
    rather than crowded at the bottom of it. Every price band keeps representation, and
    the scorer still ranks on suitability afterwards -- this only decides what it gets to
    look at.

    Untouched when the matching stock already fits: no reordering, no behaviour change.
    """
    matching = [p for p in packages if _meets_binding(p, brief_dict, binding_facts)
                == len(binding_facts)]
    if len(matching) <= MAX_CANDIDATES:
        return packages
    # An even stride through the price-ascending matching stock, then everything else
    # behind it so the cap falls where it should.
    step = len(matching) / float(MAX_CANDIDATES)
    kept, taken = [], set()
    for n in range(MAX_CANDIDATES):
        idx = int(n * step)
        if idx not in taken:
            taken.add(idx)
            kept.append(matching[idx])
    others = [p for n, p in enumerate(matching) if n not in taken]
    tail = [p for p in packages if _meets_binding(p, brief_dict, binding_facts)
            != len(binding_facts)]
    return kept + others + tail


def _meets_binding(package, brief_dict, binding_facts) -> int:
    """How many of the minimums the consultant actually typed this row satisfies.

    Only the fields the brief set a minimum for -- an unstated minimum is not a
    requirement, and a row is never penalised for a question nobody asked. A row whose
    value is unknown counts as not meeting it, which is the same judgement the pipeline
    already makes by requiring those fields to be stated at all (see binding_facts).
    """
    met = 0
    for field in binding_facts:
        floor = _num(brief_dict.get(BRIEF_MINIMUM_FOR[field])) or 0
        value = _num(package.get(PACKAGE_KEY_FOR[field], package.get(field)))
        if value is not None and value >= floor:
            met += 1
    return met


def coverage_sentence(counts, source_path, state):
    """One honest paragraph about what the snapshot could and could not judge."""
    missing = counts.get("missing_fields") or {}
    missing_txt = ", ".join("%s missing on %d" % (k, v) for k, v in
                            sorted(missing.items(), key=lambda kv: -kv[1])) or "none"
    parts = [
        "Scored %d of %d stored listing(s) from the deployed snapshot (%s)."
        % (counts["scored"], counts["snapshot_rows"], os.path.basename(source_path)),
        "Not scored: %d superseded by a fresher capture of the same lot, %d outside %s, "
        "%d with no state recorded, %d not available (sold/on hold/under offer/not "
        "available), %d whose availability the source never stated, "
        "%d whose channel's prices are being re-verified, "
        "%d with no price, %d over the budget ceiling, %d with no suburb, "
        "%d whose suburb is not a recognised locality, "
        "%d without the facts the brief's minimums are checked against (%s)."
        % (counts.get("superseded", 0),
           counts["other_state"], state or "the requested state", counts["state_unknown"],
           counts["not_available"], counts.get("availability_unstated", 0),
           counts.get("price_unverified", 0),
           counts["no_price"], counts["over_budget"],
           counts["no_suburb"], counts.get("suburb_not_a_locality", 0),
           counts["incomplete_facts"], missing_txt),
        "Those rows are NOT guessed into the shortlist: the pipeline would otherwise "
        "default a missing bedroom count to 4 and a missing house size to 180 m².",
    ]
    # Said separately and only when it applies, because it is the one filter the client
    # sets that can silently remove a large slice — and a consultant who narrowed to
    # "Apartment" should be told how many House & Land lots that cost, not left wondering
    # why the shortlist shrank.
    if counts.get("no_incentive"):
        parts.append(
            "%d listing(s) were set aside because their source states no rebate, "
            "discount or bonus. Only 80 of the catalogue state one at all, so this "
            "filter narrows hard — untick it to search the full stock."
            % counts["no_incentive"])
    if counts.get("other_product_type"):
        parts.append(
            "%d listing(s) were a different property type from the one requested. "
            "Listings whose source never stated a type were still scored — 1,826 of the "
            "catalogue record none, and excluding them would drop a third of the stock "
            "over a preference their source never contradicted."
            % counts["other_product_type"])
    if counts.get("lot_page_gone"):
        parts.append(
            "%d listing(s) were left out because the builder's own page for the lot has "
            "been taken down (\"page not found\") or marks it sold, under offer or on hold "
            "when checked, so they are not offered even to confirm."
            % counts["lot_page_gone"])
    unstated = counts.get("unstated_but_scored") or {}
    if unstated:
        parts.append(
            "Scored with a gap the brief does not turn on (%s) — shown as not stated, "
            "never filled in with an assumed figure."
            % ", ".join("%s unknown on %d" % (k, v) for k, v in
                        sorted(unstated.items(), key=lambda kv: -kv[1])))
    if counts.get("storey_unknown_and_binding"):
        parts.append("%d single-storey-only candidate(s) skipped because the source "
                     "row does not record the storey count."
                     % counts["storey_unknown_and_binding"])
    if counts.get("same_listing_collapsed"):
        parts.append("%d row(s) were the same lot at the same price captured through a "
                     "second channel, and were merged (freshest capture kept)."
                     % counts["same_listing_collapsed"])
    if counts.get("duplicates_merged"):
        parts.append("%d duplicate row(s) (same lot, design and price) were merged by "
                     "the pipeline's own DedupeEngine." % counts["duplicates_merged"])
    if counts.get("stale_unverified"):
        parts.append("%d row(s) were last seen by a source more than %d days ago, so "
                     "they were passed through as Pending Confirmation rather than "
                     "verified." % (counts["stale_unverified"], FRESH_DAYS))
    if counts.get("truncated"):
        parts.append("%d further in-budget row(s) were not scored: the per-request cap "
                     "is %d (SPB_MAX_CANDIDATES)." % (counts["truncated"], MAX_CANDIDATES))
    parts.append("Scoring ran over stored stock only — this endpoint never scrapes, so "
                 "prices and availability are as at each row's capture date, not live.")
    return " ".join(parts)
