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
                           # Why the suburb reads the way it does -- "stated by the
                           # source", "read from the composite", or the reason it is
                           # blank. A blank suburb with no reason beside it looks like
                           # data we lost; with the reason it is data the source never
                           # gave. Same contract as state_source.
                           "suburb_source",
                           # "Compare on realestate.com.au" -- a SEARCH URL for this
                           # row's suburb, built by rea_link.py from the RESOLVED suburb
                           # a few lines below. No key, no API, no request from us: the
                           # string sits in the page and Coleen's browser makes the
                           # request when she clicks. Same four-field shape as
                           # source_link_*, so index.html renders it with the same cell.
                           "rea_search_url", "rea_search_opens", "rea_search_label",
                           "rea_search_search"]

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


def _FOLDER_CAPABILITY(url: str) -> bool:
    """A share link that opens a FOLDER rather than one document."""
    return any(m in url for m in ("dropbox.com/scl/fo/", "/:f:/", "sharepoint.com/:f:"))


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
    text = path.read_text(encoding="utf-8")
    folder = sum(text.count(m) for m in
                 ("dropbox.com/scl/fo/", "/:f:/", "sharepoint.com/:f:"))
    if folder:
        raise SystemExit(
            f"[ABORT] {path.name}: {folder} folder-level share link(s) would be "
            f"published. A folder capability grants the whole folder to anyone with the "
            f"URL. Export the per-document link instead, or nothing.")


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


_NOT_FREE_TEXT = {"content_hash", "sha256", "dedup_key", "row_key", "superseded_by"}


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
    _read_fields = BUILDING_FIELDS + ["source_text", "source_url", "stocklist_file",
                                      "source_sheet", "source_row", "source_page"]
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
    _linked = 0
    _rea_linked = 0
    _resuburbed = 0
    _suburb_reasons = {}
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
        b["suburb_source"] = why
        _suburb_reasons[why] = _suburb_reasons.get(why, 0) + 1
        if b["suburb"] != before:
            _resuburbed += 1
        link = _link_for(b)
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
        # The compare-on-REA half, which works with no key at all. Reported so the
        # coverage is a number in the build log rather than something a reader has to
        # infer from how many em-dashes they scroll past.
        "rea_linked": _rea_linked,
        "rea_suburbs": len(_suburb_rows),
        "benchmarks": len(_bench_rows),
        "with_suburb": sum(1 for b in buildings if (b.get("suburb") or "").strip()),
    }
    # Which sources are still producing stock — the Settings tab, and the banner that
    # would have shown E-Agent had signed out three nights before anyone noticed. Before
    # conn.close(), obviously, but worth saying why it is HERE and not beside the other
    # exports further down: those run after the connection is closed.
    meta["channel_health"] = _export_channel_health(out_dir, conn)
    conn.close()

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
            {"source": "/stock.json", "headers": [
                {"key": "Cache-Control", "value": "private, no-cache"}]},
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
# refresh.py is stdlib-only and imports nothing from the pipeline, so it carries no
# includeFiles and cannot drag the harvest's modules into a second function bundle.
_FUNCTION_ENTRY = ("research.py", "_bootstrap.py", "_candidates.py", "_export_builders.py",
                   "refresh.py")


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

  if (path === "/logout") {
    return new Response(null, { status: 303, headers: {
      "location": "/login",
      "set-cookie": COOKIE + "=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax",
      "cache-control": "no-store",
    }});
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
    from datetime import date, datetime as _dt

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

    def _health(name, by_iso, ts_map=None):
        """One row of the Settings table.

        REPORTS VOLUME AS WELL AS RECENCY, and the second one was the gap. The first cut
        asked only "did this source produce a row lately", so on 17 Aug it printed Proxima
        as "ok" while that run had read 16 lots against a stored 1,396 — a 99% collapse,
        shown as healthy, on the very tab built to stop exactly this being invisible. The
        harvest's own partial-read guard caught it; the tab flattered it.

        `refreshed` is how many of the live rows carry the newest date — i.e. how much of
        what we hold was actually seen on the last successful read. A source that returns
        a fraction of its stock is stamping that fraction as today's and letting the rest
        go quietly stale, which is the same harm as not running at all, minus the warning.
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
        refreshed = by_iso.get(newest.strftime("%Y%m%d"), 0) if newest else 0
        pct = round(100.0 * refreshed / live) if live else 0
        if age is None:
            state = "unknown"
        elif age >= CHANNEL_DEAD_DAYS:
            state = "dead"
        elif age > CHANNEL_STALE_DAYS:
            state = "stale"
        elif pct < CHANNEL_PARTIAL_PCT:
            # Recent but thin: it ran, and brought back a fraction.
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

    chan_ts = _newest_ts("source_channel", "")
    channels = [_health(n, t, chan_ts) for n, t in _tally("source_channel", "").items()]
    channels.sort(key=lambda c: (-(c["days_ago"] or 0), -c["live"]))

    # THE DIRECT PORTALS, BY NAME. They all share one source_channel, so Bathla,
    # Hermitage and Torsion appeared as a single row called "Direct Builder Portal
    # (live)" — and when one signs out, that row names the group rather than the portal
    # that broke, which is the one thing a person needs in order to fix it.
    portal_ts = _newest_ts("builder_name", " AND source_channel LIKE ?",
                          ("Direct Builder Portal%",))
    portals = [_health(n, t, portal_ts) for n, t in
               _tally("builder_name", " AND source_channel LIKE ?",
                      ("Direct Builder Portal%",)).items()]
    portals.sort(key=lambda c: (-(c["days_ago"] or 0), -c["live"]))
    unwell = ("stale", "dead", "partial")
    payload = {
        "generated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "stale_after_days": CHANNEL_STALE_DAYS, "dead_after_days": CHANNEL_DEAD_DAYS,
        "partial_below_pct": CHANNEL_PARTIAL_PCT,
        "channels": channels,
        "portals": portals,
        # Portals are named individually here too: "Direct Builder Portal (live)" tells
        # nobody which sign-in to go and fix.
        "needs_signin": [c["channel"] for c in channels + portals if c["state"] in unwell],
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
    dest_api = out_dir / "api"
    lib = dest_api / "_lib"
    lib.mkdir(parents=True, exist_ok=True)
    n = 0
    for name in _FUNCTION_ENTRY:
        if (src_api / name).is_file():
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
assert (not %r) or client_report._logo_html(), 'logo missing from bundle'
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
    if m.get("relabelled"):
        print(f"    {m['relabelled']} row(s) re-labelled to one name per builder "
              f"(the database keeps its spellings — see _display_name_canonicaliser)")
    _assert_no_folder_capability_urls(where / "stock.json")
    _assert_a_benchmark_names_its_place(where / "stock.json")
    _assert_every_benchmark_listing_opens_somewhere(where / "benchmarks.json")
    # benchmarks.json is in this list for the same reason `notes` was the leak that
    # created the check: a listing payload sourced from an agency is the most likely
    # place a direct phone number has ever reached this project's free text.
    for f in ("stock.json", "builders.json", "vendor-assets.json", "benchmarks.json"):
        _assert_no_contact_details(where / f)
        print(f"    {f:<20} {(where / f).stat().st_size:>10,} bytes")
    print("    X-Robots-Tag: noindex set (a Vercel URL is public by default)")
