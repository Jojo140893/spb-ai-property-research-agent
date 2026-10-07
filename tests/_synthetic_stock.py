"""Synthetic stored stock for the 7 Oct 2026 search tests (every candidate scored, paging).

Rows clear every gate of _candidates.build_packages unless a test says otherwise: a stated
locality and state, a document, price evidence, Available, read the day before the frozen
today (16/09/2026). One estate is one building on the Research tab.
"""
import contextlib
import io
import json
from datetime import datetime

NO_CEILING = 9007199254740991
TODAY = datetime(2026, 9, 16, 12, 0)

BRIEF = {"client_name": "Synthetic", "state": "VIC", "budget_min": 0,
         "budget_max": NO_CEILING, "preferred_spending_cap": NO_CEILING,
         "primary_suburbs": [], "bedrooms_min": 0, "bathrooms_min": 0, "car_spaces_min": 0,
         "storeys_max": 3, "land_size_min_sqm": 0, "house_size_min_sqm": 0,
         "search_radius_km": 0, "product_type": [], "requires_incentive": False,
         "buyer_type": ""}


class Sep16(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 16, 12, 0, tzinfo=tz)


def row(content_hash, **kw):
    out = {"content_hash": content_hash, "builder_name": "Example Homes",
           "lot_address": "Lot 1, Example Rise", "street_address": None, "lot_number": "1",
           "suburb": "Tarneit", "state": "VIC", "availability_status": "Available",
           "state_source": "listing locality", "price": 600000.0, "land_price": None,
           "build_price": None, "bedrooms": 4, "bathrooms": 2, "car_spaces": 2,
           "land_sqm": 400.0, "house_sqm": 180.0, "storey": None, "title_status": None,
           "estate_name": "Example Rise", "incentive_amount": None, "incentive_text": None,
           "product_type": "House & Land", "source_channel": "E-Agent",
           "attribution_scope": None, "date_checked": "15/09/2026", "listing_url": None,
           "floorplan_url": None, "brochure_url": None, "source_project_id": None,
           "superseded_by": None, "suburb_source": "stated by the source",
           "source_doc": "doc-example", "price_kind": "package", "text_price_max": None,
           "price_in_text": True, "email_list": None, "email_first_seen": None,
           "line_key": None, "line_says_gone": False, "lot_page_gone": "",
           "lot_page_status": ""}
    out.update(kw)
    if out["text_price_max"] is None:
        out["text_price_max"] = out["price"]
    return out


def stock(fresh=1500, estates=30, stale=0, recent_unstated=0):
    """`fresh` lots spread over `estates` estates (one price list each), priced $400,000 up
    in $1,000 steps; `stale` lots read 20/08/2026 (outside FRESH_DAYS, inside the 45-day
    confirm window), so they need confirming; and `recent_unstated` blank lots read
    yesterday on an exceptions-only list (one lot On Hold), which need confirming under
    EXCEPTIONS_LIST_MODE=confirm and were read in the last 3 nights."""
    rows = []
    for i in range(fresh):
        e = i % estates
        rows.append(row("v2:fresh-%05d" % i, lot_number=str(1000 + i),
                        lot_address="Lot %d, Estate %02d" % (1000 + i, e),
                        estate_name="Estate %02d" % e, source_doc="doc-estate-%02d" % e,
                        price=400000.0 + 1000.0 * i, line_key="k-fresh-%05d" % i))
    for i in range(stale):
        # A builder of its own: a builder whose OTHER lists were read since is hidden as
        # builder_documents_reread, which is not what these rows are for.
        rows.append(row("v2:stale-%05d" % i, builder_name="Old Rise Homes",
                        lot_number=str(5000 + i),
                        lot_address="Lot %d, Old Rise" % (5000 + i), estate_name="Old Rise",
                        source_doc="doc-old-rise", price=500000.0 + 997.0 * i,
                        date_checked="20/08/2026", line_key="k-stale-%05d" % i))
    if recent_unstated:
        rows.append(row("v2:held", lot_number="9000", lot_address="Lot 9000, Blank Rise",
                        estate_name="Blank Rise", source_doc="doc-blank-rise",
                        availability_status="On Hold", price=450000.0, line_key="k-held"))
    for i in range(recent_unstated):
        rows.append(row("v2:blank-%05d" % i, lot_number=str(9001 + i),
                        lot_address="Lot %d, Blank Rise" % (9001 + i),
                        estate_name="Blank Rise", source_doc="doc-blank-rise",
                        availability_status=None, price=455000.0 + 1013.0 * i,
                        line_key="k-blank-%05d" % i))
    return rows


def write(tmp_path, rows):
    path = tmp_path / "stock.json"
    path.write_text(json.dumps({"buildings": rows}), encoding="utf-8")
    return path


def run(research, brief=None, **payload):
    body = dict(payload, client_brief=json.loads(json.dumps(brief or BRIEF)))
    with contextlib.redirect_stdout(io.StringIO()):
        return research.run_research(body)


def size(response):
    """Bytes as api/research.py's handler writes a response: compact separators."""
    return len(json.dumps(response, default=str, separators=(",", ":")).encode("utf-8"))
