"""
BENCHMARKS — market comparables from realestate.com.au. Not building stock.

Coleen, this hour: *"the realestate.com.au ones are a totally another category named
BENCHMARKS and the listings will be stored in there with the link that opens that
building inside the realestate.com.au website so Coleen can compare them"*.

So these are not lots she can sell. They are what the market is asking, stored so her
builders' stock can be measured against something real. They live in their OWN table,
`benchmark_listings`, and that separation is the entire feature.

WHY A SEPARATE TABLE AND NOT A FLAG ON `buildings`
--------------------------------------------------
harvest_buildings.py already called db.record_building() on realestate.com.au listings.
The wiring was live; only the PropTrack key was missing. The moment one is stored, those
rows become source_channel #5 in `buildings` — and every consumer of that table selects
it with NO channel predicate. Measured, all of them: api/_candidates.py:213 (the client
shortlist, which would recommend an established resale with a blank builder as a package
a buyer can purchase, printed as "realestate.com.au - database snapshot"), build_web.py
:339 and :410 (stock.json and the "By source" dropdown, beside E-Agent), benchmark_
buildings.py:326 (the INTERNAL peer median — REA rows carry product_type 'Established'
and peer tier 3 is suburb+bedrooms with no product type, so resales would be averaged
into the median new packages are scored against; benchmark_market.py's own docstring
says that "makes it look expensive every single time"), supersede_duplicates.lot_key
(which omits source_channel, so a comparable could supersede real sellable stock and
remove it from the default view), database.get_buildings feeding export_csv.py:117 and
export_excel.py:67 (the CSV and the Excel workbook Coleen actually reads), the promo
selection at database.py:631/650, cohorts.py, catalogue_exposure.py, recover_pooled_
builders.py:961 (which WRITES builder_name, i.e. would attach one of Coleen's approved
builders to a stranger's resale), server.py:56 and database.distinct_suburbs.

A discriminator column would need a filter added in every one of those, in Python,
JavaScript AND PowerShell, and stay right forever. This repo has already drifted twice
on the one flag it has: benchmark_buildings.py:333-335 documents `superseded_by IS NULL`
vs `= ''`, and api/_candidates.py:449-452 documents the shortlist scoring 979 superseded
rows the dashboard had been hiding. database.py:119-124 states the house rule outright —
exclude by allow-list, not denylist, "because the failure is silent and only shows up a
week later".

A row that is not in `buildings` cannot leak into any of them, with zero edits to any of
them. The whole separation is ONE routing line at harvest_buildings.py:126. That line is
also the realistic failure mode — a future commit "unifying the harvest" reverts it — so
tests/test_benchmarks.py pins `SELECT COUNT(*) FROM buildings WHERE
source_channel='realestate.com.au'` at zero.

WHY A NEW MODULE AND NOT database.record_building
-------------------------------------------------
Two reasons, one structural and one about identity.

record_building's column dict has no `source_project_id` key, so it DROPS it silently.
That is measured, not theoretical: 0 of 11,257 stored rows carry one even though
sources/proxima.py sets it, which is why backfill_proxima_projects.py had to be written.
The provider's listing id is the only durable handle an REA listing has; losing it on
the way in loses the row's identity and any hope of its own URL.

And `buildings` identity is wrong for a listing that already HAS one. content_hash is a
sha256 over builder_key plus a variant key flattened out of a stocklist LINE, with money
and status stripping, a 110-character legacy truncation tolerance, variant ordinals for
price-only siblings and a legacy UNIQUE dedup_key bolted on. Every part of that machinery
exists because a spreadsheet row has no id. A PropTrack listing does.
UNIQUE(provider, provider_listing_id) is one line and strictly more correct.

DELIBERATELY ABSENT COLUMNS
---------------------------
This list is the anti-duplication argument made structural, so a later maintainer cannot
"complete" the schema back into a second buildings table:

  builder_name / builder_matched / builder_source / attribution_scope
      A market listing has no builder. sources/realestate.py:158-161 already refuses to
      guess one ("the mistake that put 319 lots under a builder called Proxima"). With
      no column, recover_pooled_builders.py:961 can never write one.
  content_hash / dedup_key / variant_ordinal      identity is the provider's id.
  superseded_by
      Supersession means "the same lot captured twice from a spreadsheet". Re-reading a
      stable id UPDATES. A listing that stops coming back is a different fact and gets
      withdrawn_at — marked, never deleted.
  promo_selected / promo_selected_at              a comparable can never be sold.
  benchmark_median / _variance_pct / _classification / _basis
      A benchmark row is not benchmarked against itself.
  availability_status / title_status / lot_number / estate_name / source_text /
  stocklist_file                                  builder-stock vocabulary, meaningless
                                                  on somebody else's resale.
  bathrooms / car_spaces / house_sqm / listed_date
      comp_provider.RawComp (comp_provider.py:56-68) carries price, price_basis,
      price_kind, suburb, state, bedrooms, land_sqm, provider_listing_id, provider_url
      and is_new_build. Nothing else. Columns the pipeline cannot fill would ship a
      table that is empty by construction.
  postcode
      Same reason: RawComp has no postcode, data/au_suburbs.csv has no postcode column,
      and rea_link.py deliberately keeps it out of the search slug.

TODAY THIS TABLE IS EMPTY, AND THAT IS THE EXPECTED STATE. PropTrack is sales-gated and
paid, and the key is a commercial decision the client has not made. The site itself
cannot be read — measured 2026-08-08, plain HTTP and headless Chromium both get HTTP 429
with a Kasada challenge on the FIRST request — and defeating that is out of scope. So
the category ships honest-and-empty and fills itself the day a key is stored, with no
schema change and no new source_channel value.
"""

import hashlib
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import config

TABLE = "benchmark_listings"
META_TABLE = "benchmark_meta"

PORTAL = "realestate.com.au"
PROVIDER = "proptrack"
ATTRIBUTION = "realestate.com.au via PropTrack (REA Group)"

# What a row's click-through actually opens. Stored rather than derived, so the honesty
# is auditable in SQL and the tab can count the two populations without re-deriving
# them: "388 open the exact listing, 24 open a suburb search" is a very different
# sentence from a blank column.
URL_FROM_PROVIDER = "listing url returned by the provider"
URL_FROM_SEARCH = "search built from suburb and state"
URL_NONE = "no destination could be built"

# Exported to benchmarks.json. Everything here is a listing fact or its provenance;
# `id`, `provider` and `provider_listing_id` are internal plumbing and stay behind.
EXPORT_FIELDS = [
    "portal", "suburb", "state", "bedrooms", "land_sqm", "price", "price_basis",
    "price_kind", "product_type", "is_new_build", "address_label",
    "listing_url", "search_url", "url_source",
    # Contractual, not decoration: attribution and click-through are conditions of
    # using REA data at all.
    "attribution", "first_seen", "last_seen", "withdrawn_at",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS benchmark_listings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    provider            TEXT NOT NULL,
    portal              TEXT NOT NULL,
    provider_listing_id TEXT NOT NULL,
    listing_url         TEXT,
    search_url          TEXT,
    url_source          TEXT NOT NULL,
    suburb              TEXT,
    state               TEXT,
    address_label       TEXT,
    price               REAL NOT NULL,
    price_basis         TEXT,
    price_kind          TEXT,
    bedrooms            INTEGER,
    land_sqm            REAL,
    product_type        TEXT,
    is_new_build        INTEGER,
    attribution         TEXT NOT NULL,
    first_seen          TEXT,
    last_seen           TEXT,
    withdrawn_at        TEXT,
    UNIQUE(provider, provider_listing_id)
)
"""
_INDEX = ("CREATE INDEX IF NOT EXISTS idx_benchmark_place "
          "ON benchmark_listings(state, suburb)")
_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS benchmark_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
)
"""


def _connect(path=None):
    return sqlite3.connect(str(path or config.DATABASE_PATH))


def init(path=None) -> None:
    """Idempotent. Called on every read and write, exactly like database._init_db.

    Deliberately NOT part of database._init_db: the builder-name work is editing that
    file right now, and a new table has no business in a diff next to record_building.
    """
    with _connect(path) as conn:
        conn.execute(_SCHEMA)
        conn.execute(_INDEX)
        conn.execute(_META_SCHEMA)
        conn.commit()


# ------------------------------------------------------------------------- writing

def record_listing(raw: Dict[str, Any], path=None) -> str:
    """Store one realestate.com.au listing. -> "new" | "updated" | "refused".

    `raw` is the dict sources/realestate.py already produces, key names UNCHANGED. That
    matters in two places and both were live traps: the extractor emits `land_size_sqm`
    (database.py:474 is what maps it to land_sqm, and this writer does not go through
    there), and it emits the provider id as `source_project_id`, which record_building
    would have dropped on the floor.

    A listing with neither a provider id nor a URL is REFUSED, not stored. There would
    be nothing to identify it by, so every night's harvest would insert it again — the
    duplicate-capture mechanism this codebase has already paid for twice.
    """
    init(path)
    listing_id, url = _identity(raw)
    if not listing_id:
        return "refused"

    suburb = str(raw.get("suburb") or "").strip()
    state = str(raw.get("state") or "").strip().upper()
    price = _num(raw.get("price"))
    if not price or price <= 0:
        # sources/realestate.py already drops these and counts why. Held here too,
        # because a zero price would drag any median it reached and this table is
        # explicitly the thing other prices get measured against.
        return "refused"

    search_url = _search_url(suburb, state, raw.get("bedrooms"))
    if url:
        url_source = URL_FROM_PROVIDER
    elif search_url:
        url_source = URL_FROM_SEARCH
    else:
        url_source = URL_NONE

    now = datetime.now().isoformat(timespec="seconds")
    row = {
        "provider": str(raw.get("provider") or PROVIDER),
        "portal": str(raw.get("source_channel") or PORTAL),
        "provider_listing_id": listing_id,
        # VERBATIM from the API, never constructed. The site's per-property path is
        # property-{type}-{state}-{suburb}-{id}; an id alone cannot build it, and it is
        # not established that a PropTrack listingId equals the id in that path.
        "listing_url": url,
        "search_url": search_url,
        "url_source": url_source,
        "suburb": suburb,
        "state": state,
        "address_label": str(raw.get("lot_address") or "").strip(),
        "price": price,
        "price_basis": str(raw.get("price_basis") or ""),
        "price_kind": str(raw.get("price_kind") or ""),
        "bedrooms": _int(raw.get("bedrooms")),
        # The extractor's own key. See the docstring above.
        "land_sqm": _num(raw.get("land_size_sqm", raw.get("land_sqm"))),
        "product_type": str(raw.get("product_type") or ""),
        "is_new_build": 1 if str(raw.get("product_type") or "").strip() == "New" else 0,
        "attribution": str(raw.get("source_note") or ATTRIBUTION),
        "last_seen": now,
        # A listing that came back is not withdrawn, whatever we thought yesterday.
        "withdrawn_at": None,
    }

    with _connect(path) as conn:
        cur = conn.execute(
            "SELECT id FROM benchmark_listings WHERE provider=? AND provider_listing_id=?",
            (row["provider"], row["provider_listing_id"]))
        found = cur.fetchone()
        if found:
            sets = ", ".join("%s=?" % k for k in row if k != "provider_listing_id")
            conn.execute("UPDATE benchmark_listings SET %s WHERE id=?" % sets,
                         [v for k, v in row.items() if k != "provider_listing_id"]
                         + [found[0]])
            conn.commit()
            return "updated"
        row["first_seen"] = now
        cols = list(row)
        conn.execute("INSERT INTO benchmark_listings (%s) VALUES (%s)"
                     % (", ".join(cols), ", ".join("?" * len(cols))),
                     [row[c] for c in cols])
        conn.commit()
        return "new"


def mark_withdrawn(seen_ids, provider: str = PROVIDER, path=None) -> int:
    """Anything we hold for this provider that did NOT come back is marked, not deleted.

    A comparable vanishing from the market is a fact about the market. Deleting the row
    would make the disappearance itself disappear, and Coleen would have no way to see
    that the thing she compared against last month is gone.
    """
    init(path)
    ids = {str(i) for i in (seen_ids or []) if str(i)}
    now = datetime.now().isoformat(timespec="seconds")
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT id, provider_listing_id FROM benchmark_listings "
            "WHERE provider=? AND (withdrawn_at IS NULL OR withdrawn_at='')",
            (provider,)).fetchall()
        gone = [r[0] for r in rows if str(r[1]) not in ids]
        if gone:
            conn.executemany("UPDATE benchmark_listings SET withdrawn_at=? WHERE id=?",
                             [(now, i) for i in gone])
            conn.commit()
        return len(gone)


def set_meta(key: str, value: str, path=None) -> None:
    init(path)
    with _connect(path) as conn:
        conn.execute("INSERT INTO benchmark_meta(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, "" if value is None else str(value)))
        conn.commit()


def get_meta(key: str, path=None) -> Optional[str]:
    init(path)
    with _connect(path) as conn:
        row = conn.execute("SELECT value FROM benchmark_meta WHERE key=?",
                           (key,)).fetchone()
    return row[0] if row else None


# ------------------------------------------------------------------------- reading

def count(path=None) -> int:
    init(path)
    with _connect(path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM benchmark_listings")
                   .fetchone()[0])


def rows(path=None) -> List[Dict[str, Any]]:
    init(path)
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        out = conn.execute(
            "SELECT %s FROM benchmark_listings ORDER BY state, suburb, price"
            % ", ".join(EXPORT_FIELDS)).fetchall()
    return [dict(r) for r in out]


def counts_by_place(path=None) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """{(suburb_lower, STATE): {"n": .., "median": ..}} over live (not withdrawn) rows.

    Keyed on the lowercased suburb because our own stored spellings are not consistent
    ('GLENVALE' and 'Glenvale' are 27 rows split in two) and the whole point of this map
    is to join it to the suburbs Coleen actually holds stock in.
    """
    init(path)
    with _connect(path) as conn:
        got = conn.execute(
            "SELECT suburb, state, price FROM benchmark_listings "
            "WHERE (withdrawn_at IS NULL OR withdrawn_at='') AND price > 0").fetchall()
    buckets: Dict[Tuple[str, str], List[float]] = {}
    for suburb, state, price in got:
        key = (str(suburb or "").strip().lower(), str(state or "").strip().upper())
        if not key[0]:
            continue
        buckets.setdefault(key, []).append(float(price))
    return {k: {"n": len(v), "median": _median(v)} for k, v in buckets.items()}


def status(path=None) -> Dict[str, Any]:
    """Everything the Benchmarks tab needs to say why it looks the way it does.

    This REPLACES a health guard rather than adding one. Because benchmark rows are not
    in `buildings`, harvest_buildings' _dead_channels can never fire for realestate.com.au
    (it requires stored > 0 from building_counts_by_channel). That is the RIGHT blocking
    behaviour — a comparables outage must never abort run_daily at step 1/7 and freeze
    the whole dashboard over data Coleen uses for context. But an unwatched channel going
    quietly stale is exactly the 3 Aug Proxima incident, so the facts are reported here
    and rendered as a banner instead: visible, never a non-zero exit.
    """
    init(path)
    stored = count(path)
    with _connect(path) as conn:
        live = int(conn.execute(
            "SELECT COUNT(*) FROM benchmark_listings "
            "WHERE withdrawn_at IS NULL OR withdrawn_at=''").fetchone()[0])
        with_url = int(conn.execute(
            "SELECT COUNT(*) FROM benchmark_listings WHERE url_source=?",
            (URL_FROM_PROVIDER,)).fetchone()[0])
        oldest = conn.execute(
            "SELECT MIN(last_seen) FROM benchmark_listings "
            "WHERE withdrawn_at IS NULL OR withdrawn_at=''").fetchone()[0]
    configured, reason = _provider_state()
    return {
        "configured": configured,
        "reason": reason,
        "stored": stored,
        "live": live,
        "withdrawn": stored - live,
        # The gap Coleen would otherwise only see as a blank column.
        "open_the_listing": with_url,
        "open_a_suburb_search": live - with_url if live >= with_url else 0,
        "oldest_last_seen": oldest,
        "last_read": get_meta("last_read", path),
        "last_read_count": get_meta("last_read_count", path),
        "last_read_skipped": get_meta("last_read_skipped", path),
        "portal": PORTAL,
        "attribution": ATTRIBUTION,
    }


def _provider_state() -> Tuple[bool, str]:
    """Is a PropTrack key stored, and if not, the sentence that says so.

    Asked of the extractor itself rather than restated here, so the tab and the harvest
    can never report different statuses for the same fact.
    """
    try:
        from sources.realestate import RealestateSource
        if RealestateSource().configured:
            return True, ""
    except Exception as exc:                                          # noqa: BLE001
        return False, ("the realestate.com.au extractor could not be loaded (%s), so "
                       "nothing has been read" % exc)
    return False, ("no PropTrack key is stored, so nothing has been read. "
                   "realestate.com.au listings come through PropTrack, REA Group's own "
                   "licensed API — it is a paid commercial subscription and none is "
                   "active. The site itself cannot be read directly: a single automated "
                   "request is answered with a bot-detection challenge. This is a "
                   "commercial decision, not a fault.")


# ------------------------------------------------------------------------- helpers

def _identity(raw: Dict[str, Any]) -> Tuple[str, str]:
    """(listing_id, listing_url). The id is the provider's own, or a digest of the URL.

    sources/realestate.py puts the provider's listing id in `source_project_id` — the
    same key sources/proxima.py uses, and the same key record_building silently drops.
    """
    url = str(raw.get("listing_url") or raw.get("source_url") or "").strip()
    listing_id = str(raw.get("source_project_id")
                     or raw.get("provider_listing_id") or "").strip()
    if listing_id and listing_id.lower() not in ("none", "null"):
        return listing_id, url
    if url:
        # No id came back under any name the adapter's `or` chain covers. The URL is
        # then the only stable thing about the listing, so it becomes the identity —
        # prefixed so it can never be confused with a real PropTrack id.
        return "url:" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16], url
    return "", url


def _search_url(suburb: str, state: str, bedrooms) -> str:
    try:
        import rea_link
        return rea_link.search_url(suburb, state, bedrooms)
    except Exception:                                                 # noqa: BLE001
        return ""


def _num(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0
