"""
Test the building-stock harvest runner's storage + dedup, without any live login.
The credentialed sources are stubbed to return fixture listings, so this verifies
the new plumbing (buildings table, dedup, channel counts) deterministically.
"""

import tempfile
from pathlib import Path

import config
from database import ResearchDatabase

_EAGENT_LISTINGS = [
    {"lot_address": "Lot 10 Aura Estate", "suburb": "Coomera", "state": "QLD",
     "builder_name": "Avia Homes", "advertised_package_price": 725000, "bedrooms": 4,
     "bathrooms": 2, "car_spaces": 2, "source_url_or_ref": "https://e-agent/x1"},
    {"lot_address": "Lot 22 Orion", "suburb": "Springfield", "state": "QLD",
     "builder_name": "Creation Homes", "advertised_package_price": 690000, "bedrooms": 4,
     "bathrooms": 2, "car_spaces": 2, "source_url_or_ref": "https://e-agent/x2"},
]
_PORTAL_LISTINGS = [
    {"lot_address": "Lot 5 Riverbank", "suburb": "Truganina", "state": "VIC",
     "builder_name": "Paramount Living", "advertised_package_price": 640000, "bedrooms": 3,
     "source_channel": "Direct Builder Portal (live)", "source_url_or_ref": "https://paramount/x"},
]


def test_harvest_stores_and_dedupes(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(config, "DATABASE_PATH", tmp / "harvest_test.db")

    import harvest_buildings as hb

    class FakeEA:
        def __init__(self, *a, **k):
            self.username = "coleenn@spb"; self.password = "pw"
        def search(self, filters):
            return list(_EAGENT_LISTINGS)

    class FakePortals:
        def __init__(self, *a, **k):
            pass
        def search(self, filters):
            # same listing returned for every state -> runner must dedupe to 1
            return list(_PORTAL_LISTINGS)

    class FakeRegistry:
        def __init__(self, *a, **k):
            pass
        def get_all_builders(self):
            return [{"states": ["QLD"]}, {"states": ["VIC"]}]

    monkeypatch.setattr(hb, "EAgentSource", FakeEA)
    monkeypatch.setattr(hb, "BuilderPortalSource", FakePortals)
    monkeypatch.setattr(hb, "BuilderRegistry", FakeRegistry)
    monkeypatch.setattr(hb, "ResearchDatabase", lambda *a, **k: ResearchDatabase(db_path=tmp / "harvest_test.db"))

    # Only EAgentSource and BuilderPortalSource are faked below, so the other two
    # channels are switched OFF explicitly. Without this the runner reaches the real
    # Proxima portal over the network and the row count becomes whatever the client's
    # live stock happens to be that day.
    hb.harvest(eagent=True, portals=True, email=False, proxima=False)

    db = ResearchDatabase(db_path=tmp / "harvest_test.db")
    rows = db.get_buildings()
    assert len(rows) == 3, f"expected 3 unique buildings, got {len(rows)}"
    channels = {c["source_channel"]: c["n"] for c in db.building_counts_by_channel()}
    assert channels.get("E-Agent") == 2
    assert sum(channels.values()) == 3
    # portal listing returned per-state must dedupe to a single row
    assert len([r for r in rows if r["builder_name"] == "Paramount Living"]) == 1

    # re-run must not duplicate
    # Only EAgentSource and BuilderPortalSource are faked below, so the other two
    # channels are switched OFF explicitly. Without this the runner reaches the real
    # Proxima portal over the network and the row count becomes whatever the client's
    # live stock happens to be that day.
    hb.harvest(eagent=True, portals=True, email=False, proxima=False)
    assert len(ResearchDatabase(db_path=tmp / "harvest_test.db").get_buildings()) == 3


def _run_without_pytest():
    """Fallback runner (no pytest) using a tiny monkeypatch shim."""
    class MP:
        def __init__(self): self._undo = []
        def setattr(self, obj, name, val):
            old = getattr(obj, name); self._undo.append((obj, name, old)); setattr(obj, name, val)
        def undo(self):
            for obj, name, old in reversed(self._undo): setattr(obj, name, old)
    mp = MP()
    try:
        test_harvest_stores_and_dedupes(mp)
        test_a_channel_that_reads_nothing_but_holds_stock_is_a_failure()
        return True
    finally:
        mp.undo()



def test_a_channel_that_reads_nothing_but_holds_stock_is_a_failure():
    """Proxima's sign-in expired on 3 Aug and the harvest reported [SUCCESS] every
    night after it.

    It read zero lots, printed a helpful line about running portal_login, and exited 0
    — so run_daily carried on to export, build and deploy, and the dashboard served
    three-day-old prices as current. Nobody found out until Colin opened the portal.

    Reading zero is only a failure when the channel HAS stock. A channel that has never
    returned anything is a configuration state, and one skipped for missing credentials
    never ran at all, so neither can raise a false alarm at 3am.
    """
    from harvest_buildings import _dead_channels

    # The real 6 Aug shape: Proxima dead, everything else healthy.
    dead = _dead_channels({"Proxima": 0, "E-Agent": 4851},
                          {"Proxima": 1212, "E-Agent": 6344})
    assert dead == [("Proxima", 1212)], dead

    assert _dead_channels({"Proxima": 1212}, {"Proxima": 1212}) == [], "healthy run"
    assert _dead_channels({"Brand New": 0}, {}) == [], "never had stock is not a regression"
    assert _dead_channels({}, {"Proxima": 1212}) == [], "a skipped channel never ran"

if __name__ == "__main__":
    import sys
    try:
        ok = _run_without_pytest()
        print(" [PASS] harvest stores + dedupes" if ok else " [FAIL]")
        sys.exit(0 if ok else 1)
    except Exception as e:
        print(f" [FAIL] harvest test: {e}")
        sys.exit(1)
