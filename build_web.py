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
    python build_web.py [--out vercel_site]
"""

import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import config

# --- allow-lists -------------------------------------------------------------------
# buildings: listing facts only. No dedup keys, no content hashes, no internal ids.
BUILDING_FIELDS = [
    "builder_name", "lot_address", "suburb", "state", "availability_status",
    "price", "land_price", "build_price", "bedrooms", "bathrooms", "car_spaces",
    "land_sqm", "house_sqm", "storey", "title_status", "estate_name",
    "incentive_amount", "incentive_text", "product_type", "source_channel",
    "attribution_scope", "date_checked", "listing_url", "floorplan_url",
    "brochure_url", "benchmark_median", "benchmark_variance_pct",
    "benchmark_classification",
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

FORBIDDEN = ("password", "passwd", "pwd", "secret", "token", "login_email",
             "content_hash", "dedup", "sha256", "local_path", "session")


def _check(name: str, fields) -> None:
    """A field whose name looks like a credential must never be in an allow-list."""
    for f in fields:
        low = f.lower()
        if any(bad in low for bad in FORBIDDEN):
            raise SystemExit(f"[ABORT] {name}: refusing to export field {f!r}")


def _pick(row, fields):
    d = dict(row) if not isinstance(row, dict) else row
    return {f: d.get(f) for f in fields}


def build(out_dir: Path) -> dict:
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
    by_channel = [{"source_channel": r[0], "n": r[1]} for r in conn.execute(
        "SELECT source_channel, COUNT(*) FROM buildings GROUP BY 1 ORDER BY 2 DESC")]
    stamp = datetime.now().strftime("%d %b %Y, %H:%M")
    (out_dir / "stock.json").write_text(json.dumps({
        "status": "success", "total": len(buildings), "by_channel": by_channel,
        "generated": stamp, "buildings": buildings,
    }, separators=(",", ":"), default=str), encoding="utf-8")

    # builder registry, credentials stripped
    try:
        from builder_registry import BuilderRegistry
        regs = [_pick(b, BUILDER_FIELDS) for b in BuilderRegistry().get_all_builders()]
    except Exception as e:                                   # pragma: no cover
        print(f"[!] builder registry unavailable ({e}) — builders.json will be empty")
        regs = []
    (out_dir / "builders.json").write_text(json.dumps(
        {"status": "success", "count": len(regs), "generated": stamp, "builders": regs},
        separators=(",", ":"), default=str), encoding="utf-8")

    # harvested brochures / floorplans
    assets, by_builder = [], []
    try:
        arows = conn.execute(
            f"SELECT {', '.join(ASSET_FIELDS)} FROM builder_assets "
            "ORDER BY builder_name, asset_type").fetchall()
        assets = [_pick(r, ASSET_FIELDS) for r in arows]
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
        "assets": len(assets), "registry": len(regs),
    }
    conn.close()

    # the app's own frontend, unmodified
    shutil.copyfile(app / "index.html", out_dir / "index.html")
    (out_dir / "vercel.json").write_text(json.dumps({
        "$schema": "https://openapi.vercel.sh/vercel.json",
        "headers": [{"source": "/(.*)", "headers": [
            {"key": "X-Robots-Tag", "value": "noindex, nofollow"}]}],
    }, indent=2), encoding="utf-8")
    return meta


if __name__ == "__main__":
    where = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv \
        else Path(__file__).with_name("vercel_site")
    m = build(where)
    print(f"[+] {where}  built from index.html (the app's own frontend)")
    print(f"    {m['total']:,} listings · {m['builders']} builders/developments · "
          f"{m['named']:,} named · {m['with_state']:,} with a state")
    print(f"    {m['registry']} registry builders (credentials excluded) · {m['assets']} assets")
    for f in ("stock.json", "builders.json", "vendor-assets.json"):
        print(f"    {f:<20} {(where / f).stat().st_size:>10,} bytes")
    print("    X-Robots-Tag: noindex set (a Vercel URL is public by default)")
