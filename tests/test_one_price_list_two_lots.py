"""Two lines of one price list are two lots (plan rank 4, 7 Oct 2026).

The same-listing collapse (_candidates._collapse_kept) and the DedupeEngine pass the search
runs after it (api/research.py) both keyed on the CARD -- the address a client reads, the
price and the facts. A line with no street and no lot number is carded "Atlas, Melbourne
VIC", so units 2302 and 1712 of Atlas, on one price list at one price, became one card and
the other unit was never shown; Balmain lots 827 and 822 at $350,000 the same. On the 6 Oct
database 226 different lots were folded into a twin this way, 214 of them two lines of one
document. Coleen counts lots on E-Agent and in the app; these were lots she could see there
and not here.

The rule (_candidates.same_lot_key): two lines of one document that do not name the same lot
are never merged; rows that name a lot merge only when the lot and the estate match, whatever
document or channel read them, so the same lot read twice -- on E-Agent and on Proxima, or one
list re-read -- still collapses; rows that name no lot merge only as the same line of the same
document.
"""
import contextlib
import importlib.util
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
for _p in (str(APP / "api"), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _candidates as C  # noqa: E402

TODAY = datetime(2026, 9, 16, 12, 0)
BRIEF = {"client_name": "Rank 4", "state": "VIC", "budget_min": 0, "budget_max": 900000,
         "preferred_spending_cap": 900000, "primary_suburbs": [], "bedrooms_min": 0,
         "bathrooms_min": 0, "car_spaces_min": 0, "storeys_max": 3, "land_size_min_sqm": 0,
         "house_size_min_sqm": 0, "search_radius_km": 0, "product_type": [],
         "requires_incentive": False, "buyer_type": ""}


def _row(content_hash, **kw):
    """An apartment on a VIC price list that clears every gate. Two rows built with the same
    keyword arguments read alike on a card: same address, suburb, price and facts."""
    row = {"content_hash": content_hash, "builder_name": "Example Developments",
           "lot_address": "Atlas, Melbourne", "street_address": None, "lot_number": None,
           "suburb": "Melbourne", "state": "VIC", "availability_status": "Available",
           "state_source": "listing locality", "price": 650000.0, "land_price": None,
           "build_price": None, "bedrooms": 2, "bathrooms": 2, "car_spaces": 1,
           "land_sqm": None, "house_sqm": 78.0, "storey": None, "title_status": None,
           "estate_name": "Atlas", "incentive_amount": None, "incentive_text": None,
           "product_type": "Apartment", "source_channel": "E-Agent",
           "attribution_scope": None, "date_checked": "15/09/2026", "listing_url": None,
           "floorplan_url": None, "brochure_url": None, "source_project_id": None,
           "superseded_by": None, "suburb_source": "stated by the source",
           "source_doc": "doc-atlas", "price_kind": "package", "text_price_max": 650000.0,
           "price_in_text": True, "email_list": None, "email_first_seen": None,
           "line_key": None, "line_says_gone": False, "lot_page_gone": "",
           "lot_page_status": ""}
    row.update(kw)
    return row


def _kept(rows):
    """The hashes the search scores: the same-listing collapse, then the pipeline's dedupe
    (the card-only DedupeEngine where the lot-aware one does not exist, so this file can be
    run against the code before 7 Oct 2026 and fail there by its assertions)."""
    from sources.dedupe import DedupeEngine
    packages, counts = C.build_packages(dict(BRIEF), rows, today=TODAY)
    dedupe = getattr(C, "dedupe_packages", lambda p, _rows: DedupeEngine.deduplicate(p))
    return sorted(p["content_hash"] for p in dedupe(packages, rows)), counts


# ------------------------------------------------------------------ must stay apart

def test_two_lines_of_one_price_list_are_two_lots():
    """Atlas: two unit lines on one list, neither naming its unit in a lot column, one card."""
    rows = [_row("v2:atlas-2302", line_key="k-2302"), _row("v2:atlas-1712", line_key="k-1712")]
    kept, counts = _kept(rows)
    assert kept == ["v2:atlas-1712", "v2:atlas-2302"], counts
    assert counts["same_listing_collapsed"] == 0


def test_two_lot_numbers_on_one_card_are_two_lots():
    """Balmain lots 827 and 822 at $350,000; '1 Potter Walk' lots 40 to 44."""
    rows = [_row("v2:lot-827", lot_number="827", line_key="k-827"),
            _row("v2:lot-822", lot_number="822", line_key="k-822")]
    assert _kept(rows)[0] == ["v2:lot-822", "v2:lot-827"]
    # The same with no line keys at all (a row whose text left nothing to compare).
    rows = [_row("v2:lot-40", lot_number="40"), _row("v2:lot-41", lot_number="41")]
    assert _kept(rows)[0] == ["v2:lot-40", "v2:lot-41"]


def test_two_lists_that_name_no_lot_are_not_one_lot_by_their_card():
    """'45 Pulchella Crescent' on two documents with different lines: kept apart -- the
    price of never hiding a different lot behind a twin."""
    rows = [_row("v2:a", source_doc="doc-1", line_key="k-a"),
            _row("v2:b", source_doc="doc-2", line_key="k-b")]
    assert _kept(rows)[0] == ["v2:a", "v2:b"]


def test_the_same_lot_number_in_two_estates_is_two_lots():
    rows = [_row("v2:x", lot_number="12", estate_name="Raceview Rise", source_doc="doc-1"),
            _row("v2:y", lot_number="12", estate_name="Ormeau Vue", source_doc="doc-2")]
    assert _kept(rows)[0] == ["v2:x", "v2:y"]


# ------------------------------------------------------------------ must still collapse

def test_the_same_unit_on_e_agent_and_proxima_is_one_card():
    """A true cross-channel duplicate: E-Agent's list and Proxima's project name the same
    unit of the same building. One card, as before 7 Oct 2026."""
    rows = [_row("v2:eagent-2302", lot_number="2302", line_key="k-2302"),
            _row("v2:proxima-2302", lot_number="2302", source_channel="Proxima",
                 source_project_id="761", source_doc="proxima-761", line_key="p-2302")]
    kept, counts = _kept(rows)
    assert len(kept) == 1, kept
    assert counts["same_listing_collapsed"] == 1


def test_the_same_lot_on_two_documents_is_one_card():
    """'Lot 156, Ausbuild Stock List.' read from two documents; and "Lot 12" in one lot
    column is lot 12 in another."""
    rows = [_row("v2:one", lot_number="156", source_doc="doc-1", line_key="k-1"),
            _row("v2:two", lot_number="156", source_doc="doc-2", line_key="k-2")]
    assert len(_kept(rows)[0]) == 1
    rows = [_row("v2:one", lot_number="Lot 12", source_doc="doc-1"),
            _row("v2:two", lot_number="12", source_doc="doc-2")]
    assert len(_kept(rows)[0]) == 1


def test_a_lot_named_only_on_its_card_is_matched_by_that_name():
    """Creation Homes 'Lot 100' in Gatton on two documents, no lot column on either."""
    rows = [_row("v2:one", lot_address="Lot 100", source_doc="doc-1", line_key="k-1"),
            _row("v2:two", lot_address="Lot 100", source_doc="doc-2", line_key="k-2")]
    assert len(_kept(rows)[0]) == 1


def test_the_same_line_of_one_list_read_twice_is_one_card():
    rows = [_row("v2:read-1", line_key="k-same"), _row("v2:read-2", line_key="k-same")]
    kept, counts = _kept(rows)
    assert len(kept) == 1 and counts["same_listing_collapsed"] == 1


def test_the_pipeline_dedupe_alone_keeps_the_same_rule():
    """DedupeEngine's own key (address, design, price) with the lot key beside it: what
    api/research.py runs after the collapse, and what a live crawl runs without it."""
    from sources.dedupe import DedupeEngine
    rows = [_row("v2:p1", lot_number="827"), _row("v2:p2", lot_number="822"),
            _row("v2:p3", lot_number="827", source_doc="doc-2")]
    pkgs = [{"content_hash": r["content_hash"], "lot_address": "Balmain NSW",
             "house_design": "", "advertised_package_price": 350000.0,
             "lot_number": r["lot_number"], "estate_context": "Atlas"} for r in rows]
    assert len(DedupeEngine.deduplicate([dict(p) for p in pkgs])) == 1
    kept = C.dedupe_packages([dict(p) for p in pkgs], rows)
    assert sorted(p["content_hash"] for p in kept) == ["v2:p1", "v2:p2"]


def test_the_dedupe_keeps_the_capture_read_last_not_the_first_in_line():
    """Hermitage Springridge lot 1307 (6 Oct): read that night, and merged into its own
    capture from August -- which a guard then hid as superseded by the newer one, so the lot
    was shown nowhere. Two captures of one lot: the fresher, verified one is kept, wherever
    the older one sat in the list."""
    from sources.dedupe import DedupeEngine
    old = {"content_hash": "v2:aug", "lot_address": "Lot 1307, Springridge",
           "house_design": "", "advertised_package_price": 752850.0, "lot_number": "1307",
           "estate_context": "Springridge", "verified": False, "age_days": 55}
    new = dict(old, content_hash="v2:oct", verified=True, age_days=0)
    rows = [{"content_hash": "v2:aug", "lot_number": "1307", "estate_name": "Springridge"},
            {"content_hash": "v2:oct", "lot_number": "1307", "estate_name": "Springridge"}]
    assert [p["content_hash"] for p in DedupeEngine.deduplicate([old, new])] == ["v2:aug"], \
        "premise: the engine alone keeps the first"
    dedupe = getattr(C, "dedupe_packages", lambda p, _rows: DedupeEngine.deduplicate(p))
    assert [p["content_hash"] for p in dedupe([dict(old), dict(new)], rows)] == ["v2:oct"]
    assert [p["content_hash"] for p in dedupe([dict(new), dict(old)], rows)] == ["v2:oct"]
    # Of two unverified captures, the one read more recently.
    older = dict(old, content_hash="v2:jul", age_days=80)
    rows.append({"content_hash": "v2:jul", "lot_number": "1307", "estate_name": "Springridge"})
    assert [p["content_hash"] for p in dedupe([dict(older), dict(old)], rows)] == ["v2:aug"]


def test_a_lot_read_tonight_is_shown_when_an_old_capture_of_it_comes_first(tmp_path,
                                                                           monkeypatch):
    """End to end: the old capture sorts first (its document is listed first in the
    snapshot) and records the land a little differently, so the same-listing collapse
    (which compares every fact) leaves both for the pipeline's dedupe (which compares the
    address, design and price). The lot is still on the shortlist, as tonight's capture."""
    old = _row("v2:aug-1307", lot_number="1307", lot_address="Lot 1307, Springridge",
               estate_name="Springridge", date_checked="20/08/2026", source_doc="doc-aug",
               land_sqm=448.0)
    new = _row("v2:oct-1307", lot_number="1307", lot_address="Lot 1307, Springridge",
               estate_name="Springridge", source_doc="doc-sep")
    path = tmp_path / "stock.json"
    path.write_text(json.dumps({"buildings": [old, new]}), encoding="utf-8")
    monkeypatch.setenv("SPB_SNAPSHOT_JSON", str(path))
    monkeypatch.setattr(C, "datetime", _Sep16)
    import research
    with contextlib.redirect_stdout(io.StringIO()):
        out = research.run_research({"client_brief": dict(BRIEF)})
    assert [e["content_hash"] for e in out["shortlist"]] == ["v2:oct-1307"], out["shortlist"]


def test_a_price_list_line_is_not_a_fragment_of_another_lot_on_it(tmp_path, monkeypatch):
    """The needs-confirming pass reads a line that names no lot and no bedrooms, at a shown
    card's price and house size on the same document, as a FRAGMENT of that card. That is
    how an email breaks one lot over several lines -- and how it hid lot 406 Sama Promenade
    behind lot 408 on a price list (6 Oct): both read on 03/09, so both need confirming, and
    lot 408, offered first, stood for 406. On a price list each line is a lot."""
    stale = dict(source_doc="doc-sama", estate_name="Sama", date_checked="20/08/2026",
                 builder_name="Another Builder")
    lot_408 = _row("v2:sama-408", lot_number="408", lot_address="Lot 408 Sama Promenade",
                   bedrooms=4, line_key="k-408", **stale)
    lot_406 = _row("v2:sama-406", lot_number=None, lot_address="LOT406SamaPromenade",
                   bedrooms=None, line_key="k-406", **stale)
    path = tmp_path / "stock.json"
    path.write_text(json.dumps({"buildings": [lot_408, lot_406]}), encoding="utf-8")
    monkeypatch.setenv("SPB_SNAPSHOT_JSON", str(path))
    monkeypatch.setattr(C, "datetime", _Sep16)
    import research
    monkeypatch.setattr(research, "EXPLAIN_BUDGET_MS", 60000)
    with contextlib.redirect_stdout(io.StringIO()):
        out = research.run_research({"client_brief": dict(BRIEF), "explain": True})
    assert out["shortlist"] == []
    assert sorted(e["content_hash"] for e in out["needs_confirming"]) == [
        "v2:sama-406", "v2:sama-408"], (out["needs_confirming"],
                                        out.get("needs_confirming_hidden"))
    # The same two lines on an EMAIL are one lot broken over two lines: one card.
    email = dict(source_channel="digital email")
    path.write_text(json.dumps({"buildings": [dict(lot_408, **email), dict(lot_406, **email)]}),
                    encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        out = research.run_research({"client_brief": dict(BRIEF), "explain": True})
    assert [e["content_hash"] for e in out["needs_confirming"]] == ["v2:sama-408"]


# ------------------------------------------------------------------ the search, end to end

class _Sep16(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 16, 12, 0, tzinfo=tz)


def test_the_search_shows_both_units(tmp_path, monkeypatch):
    rows = [_row("v2:atlas-2302", line_key="k-2302"), _row("v2:atlas-1712", line_key="k-1712"),
            _row("v2:eagent-501", lot_number="501", line_key="k-501", price=700000.0,
                 text_price_max=700000.0),
            _row("v2:proxima-501", lot_number="501", source_channel="Proxima",
                 source_project_id="761", source_doc="proxima-761", price=700000.0,
                 text_price_max=700000.0)]
    path = tmp_path / "stock.json"
    path.write_text(json.dumps({"buildings": rows}), encoding="utf-8")
    monkeypatch.setenv("SPB_SNAPSHOT_JSON", str(path))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(C, "datetime", _Sep16)
    import research
    with contextlib.redirect_stdout(io.StringIO()):
        out = research.run_research({"client_brief": dict(BRIEF)})
    shown = sorted(e["content_hash"] for e in out["shortlist"])
    assert "v2:atlas-2302" in shown and "v2:atlas-1712" in shown, shown
    assert len([h for h in shown if h.endswith("-501")]) == 1, shown
    assert out["shortlist_count"] == 3


# ------------------------------------------------------------------ only ever keeps apart

_spec = importlib.util.spec_from_file_location(
    "make_colin_subset", APP / "tests" / "fixtures" / "make_colin_subset.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)
GOLDEN = json.loads(gen.GOLDEN.read_text(encoding="utf-8"))
SUBSET_BRIEFS = [n for n, e in GOLDEN["briefs"].items() if "subset" in e]


@pytest.fixture(scope="module")
def subset_rows():
    return gen.load_rows(gen.FIXTURE)


@pytest.mark.parametrize("name", SUBSET_BRIEFS)
def test_on_colins_subset_the_lot_rule_only_keeps_apart_what_the_card_rule_merged(
        name, subset_rows, monkeypatch):
    """Every row the card-only rule scored is still scored; the rows added are exactly rows
    the card-only rule merged into a twin. Nothing is lost to the new rule."""
    brief = dict(GOLDEN["briefs"][name]["brief"])
    monkeypatch.setattr(C, "EXCEPTIONS_LIST_MODE", "confirm")
    new, new_counts = C.build_packages(dict(brief), subset_rows, today=gen.TODAY)
    monkeypatch.setattr(C, "same_lot_key", lambda package, row: None, raising=False)
    old, old_counts = C.build_packages(dict(brief), subset_rows, today=gen.TODAY)
    old_h, new_h = {p["content_hash"] for p in old}, {p["content_hash"] for p in new}
    assert old_h <= new_h
    assert len(new_h - old_h) == (old_counts["same_listing_collapsed"]
                                  - new_counts["same_listing_collapsed"])
