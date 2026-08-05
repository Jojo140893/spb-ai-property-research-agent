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
    "source_project_id", "stocklist_file", "source_url",
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


def build(out_dir: Path, with_assets: bool = True) -> dict:
    for name, fields in (("buildings", BUILDING_FIELDS), ("builders", BUILDER_FIELDS),
                         ("assets", ASSET_FIELDS)):
        _check(name, fields)

    out_dir.mkdir(parents=True, exist_ok=True)
    app = Path(__file__).parent
    conn = sqlite3.connect(str(config.DATABASE_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        f"SELECT {', '.join(BUILDING_FIELDS)} FROM buildings "
        "ORDER BY builder_name, price").fetchall()
    buildings = [_pick(r, BUILDING_FIELDS) for r in rows]
    # One builder, one name, in the copy that reaches the browser. See
    # _display_name_canonicaliser for why this happens here and not in the database.
    _canon = _display_name_canonicaliser(b.get("builder_name") for b in buildings)
    _relabelled = 0
    for b in buildings:
        fixed = _canon.canonical(b.get("builder_name") or "")
        if fixed and fixed != b.get("builder_name"):
            b["builder_name"] = fixed
            _relabelled += 1
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
        "keys": BUILDING_FIELDS,
        "rows": [[b[f] for f in BUILDING_FIELDS] for b in buildings],
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

    named = sum(1 for b in buildings if (b.get("builder_name") or "").strip())
    meta = {
        "generated": stamp, "total": len(buildings), "named": named,
        "builders": len({(b.get("builder_name") or "").strip() for b in buildings
                         if (b.get("builder_name") or "").strip()}),
        "with_state": sum(1 for b in buildings if (b.get("state") or "").strip()),
        "assets": len(assets), "registry": len(regs), "copied": copied,
        "relabelled": _relabelled,
    }
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
    marker = "<script>window.SPB_STATIC_BUILD = true;</script>\n"
    if "</head>" in html:
        html = html.replace("</head>", marker + "</head>", 1)
    else:                                    # no head: put it before the first script
        html = marker + html
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    fn = _bundle_research_function(app, out_dir)
    meta["function_files"] = fn

    vercel = {
        "$schema": "https://openapi.vercel.sh/vercel.json",
        "headers": [{"source": "/(.*)", "headers": [
            {"key": "X-Robots-Tag", "value": "noindex, nofollow"}]}],
    }
    if fn:
        # Research & Scoring runs the Python pipeline, so on a static host it needs a
        # serverless function. Under api/ because Vercel's static builder excludes that
        # path — the pipeline source is bundled with the function, not served.
        vercel["functions"] = {"api/research.py": {
            "memory": 1024, "maxDuration": 30,
            "includeFiles": "{api/_bootstrap.py,api/_candidates.py,"
                            "api/_export_builders.py,api/_data/**,api/_lib/**,stock.json}",
        }}
    (out_dir / "vercel.json").write_text(json.dumps(vercel, indent=2), encoding="utf-8")
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
    "address_label.py", "benchmark.py", "provenance.py", "brief_parser.py", "builder_registry.py",
    "client_report.py", "config.py", "database.py", "drive_ingest.py", "geo.py",
    "kommo_agent.py", "kommo_client.py", "qa_checker.py", "report_generator.py",
    "schema.py", "scoring_engine.py", "secrets_store.py", "state_resolver.py",
    "turnkey_calculator.py",
)
_FUNCTION_SOURCES = (
    "__init__.py", "adaptive_extract.py", "base.py", "builder_portals.py", "dedupe.py",
    "drive_pdf.py", "e_agent.py", "feature_extract.py", "portal_config.py",
    "remote_stocklist.py", "scraper_base.py", "spreadsheet_extract.py",
)
_FUNCTION_ENTRY = ("research.py", "_bootstrap.py", "_candidates.py", "_export_builders.py")


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
    # the builder directory with every credential column blanked
    try:
        sys.path.insert(0, str(app))
        from api._export_builders import write_public_registry
        path, rows = write_public_registry(out_dir)      # it appends api/_data itself
        print(f"[+] builder directory for the function: {rows} rows, credentials blanked")
        n += 1
    except Exception as e:
        print(f"[!] could not write the public builder registry ({e}) — "
              f"deploying without the research endpoint")
        shutil.rmtree(dest_api, ignore_errors=True)
        return 0
    for junk in dest_api.rglob("__pycache__"):
        shutil.rmtree(junk, ignore_errors=True)
    _verify_function_imports(dest_api)
    return n


def _verify_function_imports(dest_api: Path) -> None:
    """Import the built endpoint the way Vercel will, in a clean interpreter.

    A module added to api/ but not to _FUNCTION_ROOT_MODULES copies nothing, imports
    fine locally — the app directory is on sys.path here — and then raises ImportError
    inside the function, 500ing every request. That shipped once: address_label.py was
    imported by _candidates.py and never bundled, so the whole research endpoint was
    dead while the page, the tests and the deploy all reported success.

    Raising here means a bundle that cannot import never reaches the alias.
    """
    probe = ("import sys; sys.path[:0] = [r'%s', r'%s'];"
             " import research, _candidates, _export_builders;"
             " print('imports-ok')" % (dest_api, dest_api / "_lib"))
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
    if m.get("relabelled"):
        print(f"    {m['relabelled']} row(s) re-labelled to one name per builder "
              f"(the database keeps its spellings — see _display_name_canonicaliser)")
    for f in ("stock.json", "builders.json", "vendor-assets.json"):
        _assert_no_contact_details(where / f)
        print(f"    {f:<20} {(where / f).stat().st_size:>10,} bytes")
    print("    X-Robots-Tag: noindex set (a Vercel URL is public by default)")
