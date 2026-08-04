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

import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:                       # api/ on sys.path: sibling imports
    sys.path.insert(0, _HERE)

from _bootstrap import HERE, ROOT
from address_label import clean_display_address

# Exactly the fields build_web.py exports to stock.json, so both readers agree and
# neither can reach an internal column (content_hash, dedup_key, source_text).
SNAPSHOT_FIELDS = [
    "builder_name", "lot_address", "street_address", "lot_number",
    "suburb", "state", "availability_status",
    "state_source", "price", "land_price", "build_price", "bedrooms", "bathrooms",
    "car_spaces", "land_sqm", "house_sqm", "storey", "title_status", "estate_name",
    "incentive_amount", "incentive_text", "product_type", "source_channel",
    "attribution_scope", "date_checked", "listing_url", "floorplan_url",
    "brochure_url", "benchmark_median", "benchmark_variance_pct",
    "benchmark_classification", "benchmark_basis",
    # Required, not optional: build_packages skips superseded captures, and without
    # this column the SQLite reader would hand over rows with the flag missing and
    # silently score stale prices while the JSON reader filtered them.
    "superseded_by",
]

# Not purchasable, so not a candidate. Absent/blank is NOT in this set: most stocklists
# have no availability column at all, and treating "unstated" as "sold" would empty
# the pool. Unstated availability is instead why such a row is not marked verified.
NOT_AVAILABLE = {"sold", "under offer", "reserved", "leased", "on hold", "withdrawn"}

# Same headroom the live sources apply before handing a package to scoring
# (sources/e_agent.py:524, sources/builder_portals.py:204).
BUDGET_HEADROOM = 50_000.0

# A stored row was verified by a live scrape on its date_checked. That verification
# goes stale: past this many days the row is passed through as unverified, which the
# pipeline turns into "Pending Confirmation" and keeps out of the final list.
FRESH_DAYS = int(os.environ.get("SPB_SNAPSHOT_FRESH_DAYS", "14"))

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
        cols = ", ".join(SNAPSHOT_FIELDS)
        rows = conn.execute("SELECT %s FROM buildings" % cols).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ------------------------------------------------------------------------ conversion

def _num(value):
    """float or None. Never a default — a missing figure must stay missing."""
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _int(value):
    n = _num(value)
    return int(n) if n is not None else None


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
    return clean_display_address(row.get("lot_address"), row)


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
    """
    raw = str(row.get("date_checked") or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            seen = datetime.strptime(raw[:10], fmt)
        except ValueError:
            continue
        age = ((today or datetime.now()) - seen).days
        return (0 <= age <= FRESH_DAYS), seen, raw
    return False, None, raw


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
    groups = {}
    order = []
    for seen, pkg in entries:
        key = (pkg["builder_name"].lower(), pkg["lot_address"].lower(),
               pkg["suburb"].lower(), pkg["advertised_package_price"],
               pkg["bedrooms"], pkg["bathrooms"], pkg["car_spaces"],
               pkg["house_size_sqm"], pkg["land_size_sqm"])
        rank = (seen or datetime.min,
                1 if pkg["house_design"] else 0,
                1 if pkg["source_url_or_ref"].startswith("http") else 0)
        if key not in groups:
            groups[key] = (rank, pkg)
            order.append(key)
        elif rank > groups[key][0]:
            groups[key] = (rank, pkg)
    kept = [groups[k][1] for k in order]
    return kept, len(entries) - len(kept)


def build_packages(brief_dict, rows, today=None):
    """(packages, coverage) — coverage is a plain dict of counts, all reported.

    `brief_dict` is the raw brief the browser posted; only budget/state/storeys are
    read here. Everything else stays the pipeline's job.
    """
    state = str(brief_dict.get("state") or "").strip().upper()
    budget_max = _num(brief_dict.get("budget_max")) or 0.0
    ceiling = budget_max + BUDGET_HEADROOM if budget_max else None
    # storeys_max < 2 is the only brief that a missing storey figure could slip past,
    # because any real house-and-land storey value (1 or 2) satisfies a max of 2.
    storeys_max = _int(brief_dict.get("storeys_max"))
    storey_is_binding = storeys_max is not None and storeys_max < 2
    # Only demand a fact the client's brief actually depends on. This gate used to
    # require all four of REQUIRED_FACTS from every row regardless of the brief, and
    # house size is recorded on 18% of stock — so 675 of VIC's 2,690 listings were
    # discarded for a missing house size even when the brief set NO house-size minimum.
    # Together with the other gates that left every VIC brief, at every budget and every
    # radius, returning exactly zero results. Same reasoning as storey_is_binding.
    binding_facts = {field for field, _ in REQUIRED_FACTS
                     if (_num(brief_dict.get(BRIEF_MINIMUM_FOR[field])) or 0) > 0}

    counts = {
        "snapshot_rows": len(rows), "other_state": 0, "state_unknown": 0,
        "not_available": 0, "no_price": 0, "over_budget": 0, "no_suburb": 0,
        "incomplete_facts": 0, "storey_unknown_and_binding": 0, "scored": 0,
        "truncated": 0, "stale_unverified": 0, "same_listing_collapsed": 0,
        "suburb_not_a_locality": 0, "superseded": 0,
    }
    missing_field_counts = {}
    unstated_but_scored = {}
    entries = []

    for row in rows:
        # An older capture of a lot we also hold fresher. The dashboard has hidden these
        # by default since they were identified, but the recommendation engine was still
        # scoring all 979 of them — so the one place where a stale price actually reaches
        # a client, the shortlist, was the one place that ignored the flag.
        if row.get("superseded_by"):
            counts["superseded"] += 1
            continue
        row_state = str(row.get("state") or "").strip().upper()
        if not row_state:
            counts["state_unknown"] += 1
            continue
        if state and row_state != state:
            counts["other_state"] += 1
            continue

        avail = str(row.get("availability_status") or "").strip().lower()
        if avail in NOT_AVAILABLE:
            counts["not_available"] += 1
            continue

        price = _num(row.get("price"))
        if not price or price <= 0:
            counts["no_price"] += 1
            continue
        if ceiling is not None and price > ceiling:
            counts["over_budget"] += 1
            continue

        suburb = str(row.get("suburb") or "").strip()
        if not suburb:
            # Without a locality the row cannot be geocoded, so no distance and no
            # benchmark can be established for it. kommo_agent tries to recover one
            # from the address text; if that is all there is, it is not scoreable.
            counts["no_suburb"] += 1
            continue
        cleaned = clean_locality(suburb, row.get("state"))
        if cleaned:
            # Use the locality itself, not the estate glued to the front of it.
            suburb = cleaned
        else:
            # PRESENT is not the same as REAL. The suburb column collects whatever
            # landed in that position of a stocklist, and 59% of it is not a locality:
            # postcodes ("2026"), stray words ("offer"), header fragments
            # ("Street # Type"), states and regions.
            #
            # This gate exists because those rows were not merely stored — they were
            # RECOMMENDED. A QLD search returned "Lot 507, 2026 in 2026, QLD" and
            # "Lot 507, offer in offer, QLD" as four of its five results, which is
            # what made the research tab look broken. A listing whose location cannot
            # be confirmed must not reach a client, and it cannot be geocoded,
            # distance-filtered or benchmarked either.
            counts["suburb_not_a_locality"] += 1
            continue

        facts = {}
        blocking, unstated = [], []
        for field, label in REQUIRED_FACTS:
            value = _int(row.get(field)) if field != "house_sqm" else _num(row.get(field))
            facts[field] = value
            if value is not None:
                continue
            (blocking if field in binding_facts else unstated).append(label)
        if blocking:
            # The client set a minimum for this and the listing does not say — it cannot
            # be judged against their requirement, so it is excluded and named.
            counts["incomplete_facts"] += 1
            for label in blocking:
                missing_field_counts[label] = missing_field_counts.get(label, 0) + 1
            continue
        for label in unstated:
            # Nothing in the brief turns on this one. Scored, with the gap on the record
            # so the report can say "not stated" instead of implying a figure.
            unstated_but_scored[label] = unstated_but_scored.get(label, 0) + 1

        storeys = _storeys(row)
        if storeys is None and storey_is_binding:
            # Counted, but no longer dropped. This exclusion existed because
            # CandidateProperty.storeys was a plain int that defaulted to 1, so an
            # unrecorded storey silently satisfied a "single storey only" brief. storeys is
            # Optional now and the scorer flags None instead of assuming it, so the row can
            # be scored honestly — and it has to be, because storey is stated on 149 of
            # 4,192 rows and dropping the rest made a single-storey brief return NOTHING:
            # all 33 lots that met every other requirement were thrown away here.
            counts["storey_unknown_and_binding"] += 1

        verified, seen_at, seen_on = _fresh(row, today)
        if not verified:
            counts["stale_unverified"] += 1

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
        entries.append((seen_at, {
            "builder_name": builder,
            # The label a client reads. Prefer the street address recovered from the
            # row's own text (stocklist_reparse), because on 47% of stock lot_address is
            # the entire flattened spreadsheet row and stripping the prices out of it
            # still leaves a jumble. Falls back to the cleaned lot_address where no
            # street was stated. Never invents and never empties.
            "lot_address": (_display_address(row) or "(no address in source row)"),
            "suburb": suburb,
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
            "verified": verified,
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
        }))

    packages, counts["same_listing_collapsed"] = _collapse_same_listing(entries)
    packages.sort(key=lambda p: p["advertised_package_price"])
    if len(packages) > MAX_CANDIDATES:
        counts["truncated"] = len(packages) - MAX_CANDIDATES
        packages = packages[:MAX_CANDIDATES]
    counts["scored"] = len(packages)
    counts["missing_fields"] = missing_field_counts
    counts["unstated_but_scored"] = unstated_but_scored
    return packages, counts


def coverage_sentence(counts, source_path, state):
    """One honest paragraph about what the snapshot could and could not judge."""
    missing = counts.get("missing_fields") or {}
    missing_txt = ", ".join("%s missing on %d" % (k, v) for k, v in
                            sorted(missing.items(), key=lambda kv: -kv[1])) or "none"
    parts = [
        "Scored %d of %d stored listing(s) from the deployed snapshot (%s)."
        % (counts["scored"], counts["snapshot_rows"], os.path.basename(source_path)),
        "Not scored: %d superseded by a fresher capture of the same lot, %d outside %s, "
        "%d with no state recorded, %d not available (sold/on hold/under offer), "
        "%d with no price, %d over the budget ceiling, %d with no suburb, "
        "%d whose suburb is not a recognised locality, "
        "%d without the facts the brief's minimums are checked against (%s)."
        % (counts.get("superseded", 0),
           counts["other_state"], state or "the requested state", counts["state_unknown"],
           counts["not_available"], counts["no_price"], counts["over_budget"],
           counts["no_suburb"], counts.get("suburb_not_a_locality", 0),
           counts["incomplete_facts"], missing_txt),
        "Those rows are NOT guessed into the shortlist: the pipeline would otherwise "
        "default a missing bedroom count to 4 and a missing house size to 180 m².",
    ]
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
