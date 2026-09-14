"""The shortlist carries the lot number the source stated, and never one it did not.

Clare's verify_property_availability tool (vapi-kommo-hook, lib/tools/stock-match.js) may
only tell a caller a home is available when it can pin the listing to one lot. It reads
Agent 2's shortlist, and until 14 Sep that shortlist had lot_address and nothing else: on
thousands of rows the address is only a street address ("5532 Verday Blvd, Jubilee"), so a
caller asking about lot 5532 could never be matched. SNAPSHOT_FIELDS already selected
lot_number; build_packages, CandidateProperty and both serialisations dropped it.

These pin the carry-through, and that it is the stored value only: a blank stays blank and
is never read out of the address, because a street number is not a lot.
"""
import json
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "api"))

import _candidates  # noqa: E402


def _row(**kw):
    row = {"state": "VIC", "suburb": "Box Hill", "price": "460000",
           "availability_status": "Available", "source_doc": "doc-a",
           "product_type": "House & Land", "builder_name": "Hermitage Homes",
           "superseded_by": "", "date_checked": "13/09/2026", "content_hash": "h"}
    row.update(kw)
    return row


def _by_hash(rows):
    packs, _counts = _candidates.build_packages({"state": "VIC", "budget_max": 900000}, rows)
    return {p["content_hash"]: p for p in packs}


def test_the_stated_lot_travels_beside_a_bare_street_address():
    got = _by_hash([
        _row(content_hash="a", lot_number=" 5532 ", lot_address="5532 Verday Blvd, Box Hill"),
        _row(content_hash="b", lot_number="CC-0122", lot_address="CC-0122, Box Hill",
             price="470000"),
    ])
    assert got["a"]["lot_number"] == "5532"
    # A stock code is what the source stated; deciding it is not a lot is the reader's job.
    assert got["b"]["lot_number"] == "CC-0122"


def test_no_stated_lot_is_blank_never_read_out_of_the_address():
    got = _by_hash([
        _row(content_hash="a", lot_number=None, lot_address="12 Hollins Street, Box Hill"),
        _row(content_hash="b", lot_address="Lot 413, Mambourin Green, Box Hill",
             price="470000"),
    ])
    assert got["a"]["lot_number"] == ""
    # Even an address that names a lot does not fill the column: it carries the source's
    # lot_number field and nothing else, so the two can be checked against each other.
    assert got["b"]["lot_number"] == ""


def test_candidate_property_and_the_deployed_serialisation_carry_it():
    from schema import CandidateProperty
    from tests.test_benchmark_buildings import _candidate
    import research

    assert CandidateProperty.__dataclass_fields__["lot_number"].default == ""
    assert research._shortlist_entry(_candidate(lot_number="5532"))["lot_number"] == "5532"
    assert research._shortlist_entry(_candidate())["lot_number"] == ""


def test_a_wyndham_vale_search_returns_the_snapshots_own_lot_numbers():
    """End to end over the deployed snapshot, read-only: every shortlist row says its lot,
    and what it says is exactly the stored value for that row, never something composed."""
    import research

    snapshot = APP / "vercel_site" / "stock.json"
    if not snapshot.exists():
        pytest.skip("no local stock.json snapshot")
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    stored = {}
    for values in data["rows"]:
        row = dict(zip(data["keys"], values))
        stored[row.get("content_hash")] = str(row.get("lot_number") or "").strip()

    out = research.run_research({"client_brief": {
        "client_name": "T", "state": "VIC", "primary_suburbs": ["Wyndham Vale"],
        "budget_max": 850000, "search_radius_km": 15}})
    shortlist = out.get("shortlist") or []
    if not shortlist:
        pytest.skip("snapshot has no Wyndham Vale stock to exercise this")
    for entry in shortlist:
        assert isinstance(entry.get("lot_number"), str), entry.get("lot_address")
        assert entry["lot_number"] == stored.get(entry["content_hash"], ""), entry["lot_address"]
    assert any(e["lot_number"] for e in shortlist), "no shortlist row carried a lot number"
