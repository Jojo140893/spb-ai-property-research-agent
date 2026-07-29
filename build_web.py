"""
Build a self-contained static dashboard of the harvested stock, for deployment.

Deliberately a STATIC site. The app's own `server.py` cannot be published: it is a
SimpleHTTPRequestHandler rooted at PROJECT_ROOT, so it would serve `.sessions/*.json`
(live authenticated portal cookies — reusable by anyone), `drive_input/vendors.csv`
(plaintext supplier passwords), the builder credential CSV and the SQLite database.

This exports ONLY listing fields. The allow-list below is explicit: a column has to be
named here to reach the browser, so a future schema addition cannot leak by default.

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

# Explicit allow-list: (db column, short key for the browser, label).
# Nothing about credentials, sessions, dedup keys or internal hashes.
COLUMNS = [
    ("builder_name", "b", "Builder / development"),
    ("lot_address", "a", "Address"),
    ("suburb", "su", "Suburb"),
    ("state", "st", "State"),
    ("availability_status", "av", "Availability"),
    ("price", "p", "Package price"),
    ("land_price", "lp", "Land price"),
    ("build_price", "bp", "Build price"),
    ("bedrooms", "bd", "Bed"),
    ("bathrooms", "ba", "Bath"),
    ("car_spaces", "c", "Car"),
    ("land_sqm", "ls", "Land m²"),
    ("house_sqm", "hs", "House m²"),
    ("storey", "sy", "Storey"),
    ("title_status", "ti", "Title"),
    ("estate_name", "es", "Estate"),
    ("incentive_amount", "in", "Incentive"),
    ("product_type", "pt", "Product"),
    ("source_channel", "sc", "Source"),
    ("attribution_scope", "as", "Attribution"),
    ("date_checked", "dc", "Checked"),
    ("listing_url", "u1", "Listing"),
    ("floorplan_url", "u2", "Floorplan"),
    ("brochure_url", "u3", "Brochure"),
]


def build(out_dir: Path) -> dict:
    conn = sqlite3.connect(str(config.DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    cols = ", ".join(c for c, _k, _l in COLUMNS)
    rows = conn.execute(
        f"SELECT {cols} FROM buildings ORDER BY builder_name, price").fetchall()

    keys = [k for _c, k, _l in COLUMNS]
    data = []
    for r in rows:
        data.append([r[c] for c, _k, _l in COLUMNS])

    def tally(col, limit=None):
        q = (f"SELECT COALESCE(NULLIF(TRIM({col}),''),'—') v, COUNT(*) n FROM buildings "
             f"GROUP BY 1 ORDER BY 2 DESC")
        got = [(x["v"], x["n"]) for x in conn.execute(q)]
        return got[:limit] if limit else got

    meta = {
        "generated": datetime.now().strftime("%d %b %Y, %H:%M"),
        "total": len(rows),
        "builders": conn.execute(
            "SELECT COUNT(DISTINCT builder_name) FROM buildings "
            "WHERE TRIM(COALESCE(builder_name,''))<>''").fetchone()[0],
        "with_links": conn.execute(
            "SELECT COUNT(*) FROM buildings WHERE listing_url IS NOT NULL "
            "OR floorplan_url IS NOT NULL OR brochure_url IS NOT NULL").fetchone()[0],
        "with_availability": conn.execute(
            "SELECT COUNT(*) FROM buildings WHERE availability_status IS NOT NULL").fetchone()[0],
        "blank_builder": conn.execute(
            "SELECT COUNT(*) FROM buildings WHERE TRIM(COALESCE(builder_name,''))=''").fetchone()[0],
        "by_state": tally("state"),
        "by_availability": tally("availability_status"),
        "by_channel": tally("source_channel"),
        "by_product": tally("product_type"),
        "by_scope": tally("attribution_scope"),
        "top_builders": tally("builder_name", 30),
    }
    conn.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"keys": keys, "labels": [l for _c, _k, l in COLUMNS], "rows": data,
               "meta": meta}
    (out_dir / "stock.json").write_text(
        json.dumps(payload, separators=(",", ":"), default=str), encoding="utf-8")
    shutil.copyfile(Path(__file__).with_name("web_index.html"), out_dir / "index.html")
    (out_dir / "vercel.json").write_text(json.dumps({
        "$schema": "https://openapi.vercel.sh/vercel.json",
        "headers": [{
            "source": "/(.*)",
            "headers": [{"key": "X-Robots-Tag", "value": "noindex, nofollow"}],
        }],
    }, indent=2), encoding="utf-8")
    return meta


if __name__ == "__main__":
    where = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv \
        else Path("vercel_site")
    m = build(where)
    size = (where / "stock.json").stat().st_size
    print(f"[+] {where}/  built")
    print(f"    {m['total']:,} listings, {m['builders']} builders/developments")
    print(f"    stock.json {size:,} bytes")
    print(f"    X-Robots-Tag: noindex set (a Vercel URL is public by default)")
