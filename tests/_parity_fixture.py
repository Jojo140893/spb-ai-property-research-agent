"""A small clean catalogue for parity_check.py's tests: a snapshot (stock.json shape) and
the database it was "built" from, with every lot plainly for sale.

7 Oct 2026. The parity check compares what the sources list with what the search shows,
so its tests need stock the search has every reason to show: real localities in the state
filed, a whole house-and-land price printed on the row's own line, read tonight, stated
Available. Anything the search then hides was hidden by the search itself -- which is the
thing a test of the check has to be able to provoke (SPB_MAX_CANDIDATES=50) and then see.
"""
import json
import sqlite3
from pathlib import Path

# Real localities, so suburb_quality locates every row and no lot is hidden as no_suburb.
PLACES = {
    "VIC": (("Tarneit", "3029"), ("Truganina", "3029"), ("Werribee", "3030"),
            ("Point Cook", "3030"), ("Wyndham Vale", "3024"), ("Clyde North", "3978"),
            ("Craigieburn", "3064"), ("Mickleham", "3064"), ("Donnybrook", "3064"),
            ("Kalkallo", "3064")),
    "NSW": (("Marsden Park", "2765"), ("Box Hill", "2765"), ("Oran Park", "2570")),
    "QLD": (("Ripley", "4306"), ("Springfield", "4300"), ("Yarrabilba", "4207")),
    "SA": (("Mount Barker", "5251"),),
    "WA": (("Baldivis", "6171"),),
}

KEYS = ("content_hash", "builder_name", "lot_address", "street_address", "lot_number",
        "suburb", "suburb_source", "state", "postcode", "availability_status",
        "state_source", "price", "land_price", "build_price", "bedrooms", "bathrooms",
        "car_spaces", "land_sqm", "house_sqm", "storey", "title_status", "estate_name",
        "incentive_amount", "incentive_text", "product_type", "source_channel",
        "attribution_scope", "date_checked", "source_project_id", "superseded_by",
        "source_doc", "price_kind", "text_price_max", "price_in_text", "email_list",
        "email_first_seen", "line_key", "lot_page_gone", "lot_page_status")
DB_ONLY = ("id", "source_text", "first_seen", "checked_at", "source_url", "stocklist_file")


def _iso(day):
    d, m, y = day.split("/")
    return "%s-%s-%sT11:00:00" % (y, m, d)


def lot(n, state="VIC", estate=None, day="06/10/2026", status="Available",
        channel="E-Agent", price=None, doc=None, **over):
    """One clean lot. n makes it unique: its own lot number, address, price and hash."""
    suburb, postcode = PLACES[state][n % len(PLACES[state])]
    estate = estate or "Parity Rise %d" % (n // 8)
    price = float(price if price is not None else 450000 + n * 1000)
    address = "Lot %d %s Way, %s %s %s" % (100 + n, estate.split()[-1], suburb, state, postcode)
    row = {
        "content_hash": "v2:%064x" % (0xA11CE000 + n),
        "builder_name": "Parity Homes", "lot_address": address, "street_address": None,
        "lot_number": str(100 + n), "suburb": suburb, "suburb_source": "stated by the source",
        "state": state, "postcode": postcode, "availability_status": status,
        "state_source": "listing locality", "price": price, "land_price": None,
        "build_price": None, "bedrooms": 4, "bathrooms": 2, "car_spaces": 2,
        "land_sqm": 400.0, "house_sqm": 200.0, "storey": 1, "title_status": "Titled",
        "estate_name": estate, "incentive_amount": None, "incentive_text": None,
        "product_type": "House & Land", "source_channel": channel,
        "attribution_scope": "builder", "date_checked": day, "source_project_id": None,
        "superseded_by": None, "source_doc": doc or ("doc-" + estate.lower().replace(" ", "-")),
        "price_kind": "house_and_land", "text_price_max": price, "price_in_text": True,
        "email_list": None, "email_first_seen": None, "line_key": "line-%d" % n,
        "lot_page_gone": "", "lot_page_status": "",
        # database-only columns
        "id": 70000 + n,
        "source_text": "%s %s 4 2 2 $%s" % (address, status, format(int(price), ",")),
        "first_seen": day, "checked_at": _iso(day),
        "source_url": "https://example.invalid/%s%%20Price%%20List.pdf"
                      % (doc or estate).replace(" ", "%20"),
        "stocklist_file": None,
    }
    row.update(over)
    return row


def clean_catalogue():
    """Every state the form searches, every lot plainly for sale: 80 VIC lots in ten
    estates (more than the 50 a capped search can score), and a handful elsewhere."""
    rows = [lot(n) for n in range(80)]
    rows += [lot(100 + n, state="NSW", estate="Parity Park %d" % (n // 4)) for n in range(12)]
    rows += [lot(200 + n, state="QLD", estate="Parity Heights %d" % (n // 4))
             for n in range(12)]
    rows += [lot(300 + n, state="SA", estate="Parity Vale") for n in range(4)]
    rows += [lot(400 + n, state="WA", estate="Parity Shores") for n in range(4)]
    return rows


def write(base, rows):
    """(snapshot path, database path) under base, the bundle's site layout."""
    base = Path(base)
    site = base / "site"
    site.mkdir(parents=True, exist_ok=True)
    snapshot = site / "stock.json"
    snapshot.write_text(json.dumps({"keys": list(KEYS),
                                    "rows": [[r.get(k) for k in KEYS] for r in rows]}),
                        encoding="utf-8")
    db = base / "catalogue.db"
    if db.exists():
        db.unlink()
    cols = list(DB_ONLY) + [k for k in KEYS if k not in DB_ONLY]
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE buildings (%s)" % ", ".join('"%s"' % c for c in cols))
        conn.executemany("INSERT INTO buildings VALUES (%s)" % ", ".join("?" * len(cols)),
                         [tuple(r.get(c) for c in cols) for r in rows])
        conn.commit()
    finally:
        conn.close()
    return snapshot, db
