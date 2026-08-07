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


def test_the_collapse_floor_is_measured_against_live_stock_not_every_capture():
    """The floor must not climb past what a healthy run actually reads.

    It was derived from building_counts_by_channel()['n'], which is COUNT(*) over the
    whole table. Superseded rows are never deleted, so that number only ever grows while
    a good nightly read stays flat -- E-Agent already stores 8,538 against 4,351 live.
    Once the floor crossed the real read, run_daily would abort at step 1/7 EVERY night,
    permanently: each aborted run still stores its rows first, pushing the floor further
    out of reach. The guard would have become the outage.
    """
    from harvest_buildings import _collapsed_channels

    # A channel reading its full live stock, with three times as many superseded
    # captures behind it. Judged on live rows this is healthy; judged on every capture
    # ever stored it trips.
    read = {"E-Agent": 4351}
    live = {"E-Agent": 4351}
    # 5x live: floor = 21755 * 0.25 = 5438, comfortably above the 4351 actually read.
    # (4x lands exactly ON the boundary, and the check is `n < floor`, so it would not
    # trip -- a fixture sitting on the edge proves nothing.)
    every_capture = {"E-Agent": 21755}

    assert _collapsed_channels(read, live, previous=read) == [],         "a full read of live stock was reported as a collapse"
    assert _collapsed_channels(read, every_capture, previous=read),         "fixture no longer reproduces: the old denominator must trip here"


def test_the_live_count_is_actually_exposed_by_the_query():
    """The floor can only use live rows if the query returns them."""
    import os
    import pathlib
    import tempfile

    from database import ResearchDatabase
    tmp = os.path.join(tempfile.mkdtemp(), "probe.db")
    os.environ["SPB_DATABASE_PATH"] = tmp     # never the live database
    try:
        rows = ResearchDatabase(pathlib.Path(tmp)).building_counts_by_channel()
    finally:
        os.environ.pop("SPB_DATABASE_PATH", None)
    # Empty table, but the SHAPE is the contract.
    probe = ResearchDatabase(pathlib.Path(tmp))
    with probe._get_connection() as conn:
        cols = [d[0] for d in conn.execute(
            "SELECT source_channel, COUNT(*) AS n, "
            "SUM(CASE WHEN superseded_by IS NULL OR superseded_by='' THEN 1 ELSE 0 END) "
            "AS n_live FROM buildings GROUP BY source_channel").description]
    assert "n_live" in cols and "n" in cols, cols


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
        test_a_partial_read_does_not_pass_as_success()
        test_a_second_partial_run_is_still_caught()
        test_ordinary_movement_is_not_a_failure()
        test_zero_is_left_to_the_dead_channel_check()
        test_a_small_channel_is_not_judged_on_noise()
        test_a_first_run_has_nothing_to_be_measured_against()
        test_the_previous_read_is_the_newest_run_not_the_newest_string()
        test_the_collapse_floor_is_measured_against_live_stock_not_every_capture()
        test_the_live_count_is_actually_exposed_by_the_query()
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


def test_a_partial_read_does_not_pass_as_success():
    """The 7 Aug incident: a number came back, so every check was satisfied.

    A filter left set in Proxima's portal session made the projects page render 8
    projects instead of 40. The harvest read 52 lots against 1,212 stored, printed
    [SUCCESS] and exited 0 — so run_daily went on to export, benchmark and publish. Same
    shape as the 3 Aug outage _dead_channels was written for, one step short of invisible.
    """
    from harvest_buildings import _collapsed_channels

    got = _collapsed_channels({"Proxima": 52, "E-Agent": 4851},
                              {"Proxima": 1212, "E-Agent": 6344},
                              previous={"Proxima": 1293, "E-Agent": 4900})
    assert [c for c, *_ in got] == ["Proxima"], got
    channel, n, floor, basis = got[0]
    assert (n, basis) == (52, "1293 read last run"), got
    assert floor > 52


def test_a_second_partial_run_is_still_caught():
    """The ratchet, and why the stored count is a floor as well as the last read.

    A partial run WRITES its rows, so tomorrow "what it read last run" is 52. Comparing
    against that alone would wave the identical fault straight through on night two —
    which is the version of this bug nobody would ever find.
    """
    from harvest_buildings import _collapsed_channels

    got = _collapsed_channels({"Proxima": 52}, {"Proxima": 1264}, previous={"Proxima": 52})
    assert len(got) == 1, "the same collapse passed on the second night"
    assert got[0][3] == "1264 stored", got


def test_ordinary_movement_is_not_a_failure():
    """This exists to catch a collapse. A false alarm at 3am stops the whole daily run."""
    from harvest_buildings import _collapsed_channels

    assert _collapsed_channels({"Proxima": 1293}, {"Proxima": 1212},
                               previous={"Proxima": 1293}) == [], "a healthy run"
    assert _collapsed_channels({"Proxima": 1100}, {"Proxima": 1212},
                               previous={"Proxima": 1293}) == [], "lots sell; 15% is a Tuesday"
    assert _collapsed_channels({"Proxima": 1400}, {"Proxima": 1212},
                               previous={"Proxima": 1293}) == [], "reading MORE is not a fault"


def test_zero_is_left_to_the_dead_channel_check():
    """One failure, reported once, with the message that names its actual cause."""
    from harvest_buildings import _collapsed_channels

    assert _collapsed_channels({"Proxima": 0}, {"Proxima": 1212},
                               previous={"Proxima": 1293}) == []


def test_a_small_channel_is_not_judged_on_noise():
    """The email sweep returns single figures. 8 listings then 3 is not a regression."""
    from harvest_buildings import _collapsed_channels

    assert _collapsed_channels({"digital email": 3}, {"digital email": 12},
                               previous={"digital email": 8}) == []


def test_a_first_run_has_nothing_to_be_measured_against():
    from harvest_buildings import _collapsed_channels

    assert _collapsed_channels({"Brand New": 5}, {}, previous={}) == []
    assert _collapsed_channels({"Brand New": 5}, {"Brand New": 5}) == [], "no baseline arg"


def test_the_previous_read_is_the_newest_run_not_the_newest_string():
    """last_seen is written "%d/%m/%Y", so ordering it as text reads the wrong run.

    "30/08/2026" sorts above "01/09/2026" as a string, which would take a month-old
    run as the baseline — and the baseline is the whole basis of the partial-read guard.
    """
    tmp = Path(tempfile.mkdtemp())
    db = ResearchDatabase(db_path=tmp / "reads.db")

    def store(n, date, suburb):
        for i in range(n):
            db.record_building({
                "source_channel": "Proxima", "builder_name": "Placeholder Developments",
                "lot_address": f"Lot {i} {suburb}", "suburb": suburb, "state": "NSW",
                "lot_number": str(i), "land_size_sqm": 300 + i,
                "advertised_package_price": 800000 + i, "date_checked": date,
            })

    store(5, "30/08/2026", "Olderton")     # the older run, and the larger string
    store(3, "01/09/2026", "Newville")     # the most recent run

    assert db.building_reads_by_channel() == {"Proxima": 3}, db.building_reads_by_channel()

if __name__ == "__main__":
    import sys
    try:
        ok = _run_without_pytest()
        print(" [PASS] harvest stores + dedupes" if ok else " [FAIL]")
        sys.exit(0 if ok else 1)
    except Exception as e:
        print(f" [FAIL] harvest test: {e}")
        sys.exit(1)
