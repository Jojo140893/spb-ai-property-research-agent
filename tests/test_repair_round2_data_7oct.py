"""Repair round 2 of the parity release (7 Oct 2026), the data verifier's findings on the
harvest and nightly side -- each a test that fails on par-search 068569a and passes after:

  * the EAgent Stocklist capture (a CI artifact) kept a next-link token in a payload's label
    and an auth token handed to localStorage.setItem;
  * a Stocklist row was not joined to its price-list twin when the lot's OLDER, already
    retired captures sat in other groups (about 5% of keys on the 6 Oct database);
  * Proxima's band walk called a read whole when the portal ignored the sort, and said
    nothing when it ran out of replays;
  * "$500,000 DEPOSIT 10% Available" read Not Available;
  * repair_rows re-attached a trap row the harvest had read AFTER the 30 Jul migration
    (row 2568), and record_building re-attached a SUPERSEDED twin;
  * a guessed Dropbox file passed as a price list on a brochure's residence-type table.
"""
import json
import logging
import sqlite3

import pytest

from database import WITHDRAWN_FROM_SOURCE, ResearchDatabase
from sources import eagent_stocklist as es
from supersede_duplicates import find_superseded
from tests.test_eagent_stocklist import (COOKIE, COMPANY, PASSWORD, FakeSite,  # noqa: F401
                                         _source, creds, make_records)


# ------------------------------------------------------------------ the Stocklist capture

class _LeakySite(FakeSite):
    """A page that keeps its session in browser storage, and an API whose next link
    carries the session as a query parameter."""

    def home_page(self):
        return ("<html><head><title>EAgent Stocklist - Search</title>"
                "<script src='/assets/app.js'></script>"
                "<script>localStorage.setItem(\"authToken\",\"opaque-LS-secret-AAA\");"
                "sessionStorage.setItem('k','sess-val-GGG');"
                "headers.set('Authorization', 'hdr-secret-HHH');</script></head><body>"
                "<p>Signed in as %s</p></body></html>" % COMPANY)

    def page(self, q, path):
        p = int(q.get("p", 1))
        size = 25
        nxt = ("%s?p=%d&access_token=NEXTSECRET123" % (path, p + 1)
               if p * size < len(self.records) else None)
        return {"packages": self.records[(p - 1) * size: p * size], "links": {"next": nxt}}


def test_the_capture_keeps_no_token_from_a_label_or_browser_storage(creds, tmp_path):  # noqa: F811
    site = _LeakySite(make_records(60), paging="next")
    try:
        src = _source(site, tmp_path)
        src.search({})
    finally:
        site.close()
    assert src.state == "ok", src.why
    blobs = {f.name: f.read_text(encoding="utf-8") for f in (tmp_path / "capture").iterdir()}
    blobs["status.json"] = (tmp_path / "status.json").read_text(encoding="utf-8")
    leaks = {s: [n for n, b in blobs.items() if s in b]
             for s in ("NEXTSECRET123", "opaque-LS-secret-AAA", "sess-val-GGG",
                       "hdr-secret-HHH", PASSWORD, COOKIE.split("=", 1)[1])}
    assert not {s: n for s, n in leaks.items() if n}, leaks
    # The label still says what was fetched, so the capture stays useful.
    assert any("access_token=[redacted]" in b for b in blobs.values())


# ------------------------------------------------------------------ the Stocklist twin

def test_a_stocklist_row_joins_its_live_twin_past_a_retired_capture():
    """The price-list lot (live), an older capture of it the status pass already retired
    (another line, so another group), and the Stocklist row: one lot, shown once."""
    common = dict(lot_number="242", estate_name="Hargrove", price=650000.0, state="VIC",
                  suburb="Wollert", bedrooms=4, bathrooms=2, car_spaces=2, house_sqm=None,
                  land_sqm=None, postcode=None, builder_name="Hudson Homes",
                  benchmark_median=None, date_checked="06/10/2026", last_seen="06/10/2026")
    live = dict(common, id=1, source_channel="E-Agent", content_hash="v2:h1",
                lot_address="Lot 242, Hargrove (live)", source_text="242 Hargrove line A",
                source_url="https://e-agent.example/a.pdf", superseded_by=None)
    retired = dict(common, id=2, source_channel="E-Agent", content_hash="v2:h2",
                   lot_address="Lot 242, Hargrove (old)", source_text="242 Hargrove line B",
                   source_url="https://e-agent.example/b.pdf", superseded_by="v2:h9",
                   date_checked="20/09/2026", last_seen="20/09/2026")
    sl = dict(common, id=3, source_channel="E-Agent Stocklist", content_hash="v2:h3",
              lot_address="Lot 242, Hargrove (stocklist)", source_text="Lot 242 | Hargrove",
              source_url="https://eagent.mekooflow.com/api/packages", superseded_by=None)
    pairs = {(l["id"], w["id"]) for l, w in find_superseded([live, retired, sl])}
    assert pairs & {(1, 3), (3, 1)}, pairs


# ------------------------------------------------------------------ the Proxima band walk

from tests.test_proxima_capped_replay import (_Page, _Server, _by_project,  # noqa: E402
                                              _harvest, _inventory, _tile)


def _run(pid, prices, honour_sort=True):
    tiles = [_tile(pid, i + 1, p) for i, p in enumerate(prices)]
    server = _Server({"761": _inventory("761", 3), pid: tiles}, honour_sort=honour_sort)
    page = _Page(server, index=["761"], dropdown=[(pid, "Probe Tower")])
    src, rows = _harvest(page)
    return src, _by_project(rows).get(pid, [])


def test_a_portal_that_ignores_the_sort_is_never_called_whole(caplog):
    with caplog.at_level(logging.WARNING, logger="spb.scraper.proxima"):
        src, mine = _run("912", [400000 + i * 1000 for i in range(300)], honour_sort=False)
    assert len(mine) == 300 or any(s["pid"] == "912" for s in src.short_projects), (
        len(mine), src.short_projects)


def test_a_project_that_spends_every_replay_is_named_short(caplog):
    with caplog.at_level(logging.WARNING, logger="spb.scraper.proxima"):
        src, mine = _run("913", [400000 + i * 100 for i in range(2600)])
    assert len(mine) == 2600 or any(s["pid"] == "913" for s in src.short_projects), (
        len(mine), src.short_projects)


def test_an_honoured_sort_still_reads_a_big_project_whole():
    src, mine = _run("914", [400000 + i * 1000 for i in range(300)])
    assert len(mine) == 300 and not src.short_projects


# ------------------------------------------------------------------ a deposit's size

@pytest.mark.parametrize("line, want", [
    ("Lot 12 $500,000 DEPOSIT 10% Available", "Available"),
    ("Lot 12 $500,000 DEPOSIT 5 % Available", "Available"),
    ("Lot 12 $500,000 DEPOSIT $5,000 Available", "Available"),
    # the status cell itself, as before
    ("4402 DEPOSIT Detached Stanmore", "Not Available"),
    ("4402 DEPOSIT 4 2 2 $650,000", "Not Available"),
    ("704 12.5 28 350 $370,000 DEPOSIT", "Not Available"),
])
def test_a_deposits_size_is_a_payment_term_not_a_status(line, want):
    from sources.feature_extract import parse_availability
    assert parse_availability(line) == want


# ------------------------------------------------------------------ identity

def _db(tmp_path):
    path = tmp_path / "trap.db"
    ResearchDatabase(db_path=path)
    return path


def _plant(path, rows):
    con = sqlite3.connect(str(path))
    for r in rows:
        cols = list(r)
        con.execute("INSERT INTO buildings (%s) VALUES (%s)" % (
            ", ".join(cols), ", ".join("?" for _ in cols)), tuple(r[c] for c in cols))
    con.commit()
    con.close()


def _rows(path):
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    out = [dict(r) for r in con.execute("SELECT * FROM buildings ORDER BY id")]
    con.close()
    return out


def test_a_trap_row_read_after_the_migration_is_aligned_not_re_attached(tmp_path):
    """Row 2568 (Creation Homes, last read 04/08): the harvest found it by its MIGRATED
    content_hash after 30 Jul, so that is the hash its line computes."""
    import repair_rows as RR
    path = _db(tmp_path)
    _plant(path, [
        {"id": 1, "source_channel": "E-Agent", "lot_address": "Lot 1", "price": 1,
         "content_hash": "v2:mig1", "dedup_key": "v2:harv1", "last_seen": "29/07/2026"},
        {"id": 2, "source_channel": "E-Agent", "lot_address": "Lot 2", "price": 1,
         "content_hash": "v2:mig2", "dedup_key": "v2:harv2", "last_seen": "04/08/2026"},
        {"id": 3, "source_channel": "E-Agent", "lot_address": "Lot 3", "price": 1,
         "content_hash": "v2:mig3", "dedup_key": "v2:harv3", "last_seen": "05/08/2026",
         "superseded_by": WITHDRAWN_FROM_SOURCE}])
    plan, skipped = RR.find_identity_traps(_rows(path))
    by_id = {p["id"]: p["set"] for p in plan}
    assert by_id[1] == {"content_hash": "v2:harv1"}, "read before the migration: re-attached"
    assert by_id[2] == {"dedup_key": "v2:mig2"}, by_id[2]
    assert by_id[3] == {"dedup_key": "v2:mig3"}, by_id[3]
    assert not skipped


def test_a_superseded_twin_hit_by_its_dedup_key_keeps_its_identity(tmp_path):
    from tests.test_dedup_key_trap import _db as trap_db
    from tests.test_dedup_key_trap import _listing, _migrate_the_30_jul_way
    from tests.test_dedup_key_trap import _rows as trap_rows
    db, path = trap_db(tmp_path)
    db.record_building(_listing(9))
    migrated = _migrate_the_30_jul_way(path, 1, suburb="Mernda")
    con = sqlite3.connect(str(path))
    con.execute("UPDATE buildings SET superseded_by='v2:the-keeper' WHERE id=1")
    con.commit()
    con.close()
    assert db.record_building(_listing(9, 799000)) == "updated"
    row = trap_rows(path)[0]
    assert row["content_hash"] == migrated, "a superseded twin was re-attached"
    assert row["superseded_by"] == "v2:the-keeper" and row["price"] == 799000
    con = sqlite3.connect(str(path))
    logged = con.execute("SELECT count(*) FROM sqlite_master WHERE name='identity_repair_log'"
                         ).fetchone()[0] and con.execute(
        "SELECT count(*) FROM identity_repair_log").fetchone()[0]
    con.close()
    assert not logged, "nothing was re-attached, so nothing is logged"


# ------------------------------------------------------------------ a guessed file

@pytest.mark.parametrize("lines, keep", [
    (["Residence A 2 Bed 2 Bath 1 Car 85 sqm $650,000",
      "Residence B 3 Bed 2 Bath 2 Car 110 sqm $890,000"], False),
    (["Level 3 | 2 Bed | 2 Bath | 1 Car | $650,000",
      "Level 4 | 3 Bed | 2 Bath | 2 Car | $890,000"], False),
    # the same unit twice is one lot, not a list
    (["A-201 1 1 - 60 13 73 $850,000", "A-201 1 1 - 60 13 73 $850,000"], False),
    (["A-201 1 1 - 60 13 73 $850,000", "A-202 2 1 - 82 13 95 $1,125,000"], True),
    (["Lot 12 Smith St 448 4 2 2 $650,000", "Lot 14 Smith St 512 4 2 2 $690,000"], True),
])
def test_a_guessed_file_must_name_a_lot_on_each_line(lines, keep):
    import sources.remote_stocklist as RS
    rows = [{"source_text": t} for t in lines]
    assert bool(RS.guessed_price_list(rows)) == keep, lines
