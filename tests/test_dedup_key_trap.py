"""The stale dedup_key trap: 41 lots dropped every night since 28 Sep (plan rank 8, 7 Oct 2026).

`buildings.dedup_key` carries a UNIQUE constraint and record_building stores the identity
hash in it. On 30 Jul migrate_buildings_identity.py recomputed content_hash for the rows
the 29 Jul harvest had inserted -- from database columns that enrichment had since filled
(suburb 'Mernda', lot_number '0') -- and left dedup_key holding the hash the HARVESTER
computes. Every night after that:

    harvester computes H        (the same hash it computed on 29 Jul)
    lookup  content_hash = H    miss: the migration moved the row's content_hash
    INSERT  dedup_key = H       IntegrityError: the old row still holds H as its dedup_key
    -> "rejected by a UNIQUE constraint -- listing NOT stored", returned as "unchanged"

Measured on the 6 Oct database and nightly log: 41 rejections a night (14 Havenwood rows
frozen at their 29 Jul price, North 45 stored $841,000 against $811,000 that night; 27
Fawkner Park West / Pricelist.pdf lots wrongly retired on 6 Oct as "withdrawn: the source
stopped listing it" although that night's read carried them). 645 rows hold a v2 dedup_key
different from their content_hash; 95 live, 33 withdrawn, the rest superseded.

The fix, and what is pinned here:
  1. record_building looks a listing up by content_hash OR dedup_key. On a dedup_key hit
     it re-attaches that row (content_hash := the harvester's hash) and updates it in
     place -- the same row, the same treatment as a content_hash hit, so a WITHDRAWN
     marker clears and any other supersession stands.
  2. An IntegrityError is never "unchanged": it is "rejected", logged as an ERROR.
  3. repair_rows carries a one-time, idempotent, compare-and-set repair with an undo log:
     live and withdrawn trap rows are re-attached (content_hash := dedup_key), the rest
     aligned (dedup_key := content_hash), and nothing is written that would collide.
  4. migrate_buildings_identity writes dedup_key with content_hash, and never recomputes
     the hash of a row the current harvester inserted (its dedup_key IS that hash).
"""
import contextlib
import io
import logging
import sqlite3

import pytest

from database import (HASH_RECIPE_VERSION, WITHDRAWN_FROM_SOURCE, ResearchDatabase,
                      building_content_hash)


def _listing(n, price=811000, status="Available", **kw):
    row = {"source_channel": "E-Agent", "attribution_scope": "project",
           "builder_name": "", "suburb": "", "lot_number": "",
           "lot_address": f"North {n}",
           "source_text": f"North {n} 3 bed townhouse ${price:,} {status}",
           "advertised_package_price": price, "availability_status": status,
           "source_url_or_ref": "https://docs.google.com/spreadsheets/d/X/gviz/tq?gid=1",
           "stocklist_file": "https://docs.google.com/spreadsheets/d/X/edit#gid=1"}
    row.update(kw)
    return row


def _db(tmp_path):
    path = tmp_path / "trap.db"
    db = ResearchDatabase(db_path=path)
    con = sqlite3.connect(str(path))
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_buildings_content_hash "
                "ON buildings(content_hash)")
    con.commit()
    con.close()
    return db, path


def _rows(path):
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    out = [dict(r) for r in con.execute("SELECT * FROM buildings ORDER BY id")]
    con.close()
    return out


def _migrate_the_30_jul_way(path, row_id, **filled):
    """What the 30 Jul re-run did: fill columns, then hash the DB row, not the listing."""
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    r = dict(con.execute("SELECT * FROM buildings WHERE id=?", (row_id,)).fetchone())
    r.update(filled)
    h = building_content_hash({
        "source_channel": r["source_channel"], "attribution_scope": r["attribution_scope"],
        "builder_name": r["builder_name"], "suburb": r["suburb"],
        "lot_number": r["lot_number"], "house_design": None,
        "land_size_sqm": r["land_sqm"], "lot_address": r["lot_address"],
        "source_text": r["source_text"]})
    sets = ", ".join(f"{k}=?" for k in filled)
    con.execute(f"UPDATE buildings SET {sets}, content_hash=? WHERE id=?",
                (*filled.values(), h, row_id))
    con.commit()
    con.close()
    return h


# ------------------------------------------------------------------ record_building

def test_a_row_whose_hash_was_rewritten_is_updated_not_dropped(tmp_path):
    db, path = _db(tmp_path)
    assert db.record_building(_listing(45, 841000)) == "new"
    harvester_hash = _rows(path)[0]["content_hash"]
    moved = _migrate_the_30_jul_way(path, 1, suburb="Mernda", lot_number="0")
    assert moved != harvester_hash

    got = db.record_building(_listing(45, 811000))
    rows = _rows(path)
    assert got == "updated", f"the listing was {got!r} -- dropped by the UNIQUE trap"
    assert len(rows) == 1, "a second copy of the lot was inserted"
    assert rows[0]["price"] == 811000, "the price stayed frozen at the 29 Jul read"
    assert rows[0]["price_previous"] == 841000
    assert rows[0]["content_hash"] == harvester_hash == rows[0]["dedup_key"], \
        "the row was not re-attached to the hash the harvester computes"
    # a second night is an ordinary content_hash hit
    assert db.record_building(_listing(45, 811000)) == "unchanged"
    assert len(_rows(path)) == 1


def test_a_withdrawn_trap_row_comes_back_when_its_list_carries_it(tmp_path):
    """27 of the 41 were retired on 6 Oct because the trap kept them from being re-read."""
    db, path = _db(tmp_path)
    db.record_building(_listing(7))
    _migrate_the_30_jul_way(path, 1, suburb="St Kilda Road")
    con = sqlite3.connect(str(path))
    con.execute("UPDATE buildings SET superseded_by=?, availability_status='Sold' WHERE id=1",
                (WITHDRAWN_FROM_SOURCE,))
    con.commit()
    con.close()
    assert db.record_building(_listing(7)) == "updated"
    r = _rows(path)[0]
    assert r["superseded_by"] is None, "a lot its list carries again stayed withdrawn"
    assert r["availability_status"] == "Available"


def test_a_superseded_twin_is_re_attached_but_never_revived(tmp_path):
    """Reviving a duplicate would double-count stock: only the WITHDRAWN marker clears,
    exactly as on a content_hash hit."""
    db, path = _db(tmp_path)
    db.record_building(_listing(9))
    _migrate_the_30_jul_way(path, 1, suburb="Mernda")
    con = sqlite3.connect(str(path))
    con.execute("UPDATE buildings SET superseded_by='v2:the-keeper' WHERE id=1")
    con.commit()
    con.close()
    assert db.record_building(_listing(9, 799000)) == "updated"
    rows = _rows(path)
    assert len(rows) == 1
    assert rows[0]["superseded_by"] == "v2:the-keeper"


def test_a_content_hash_hit_beats_a_dedup_key_hit(tmp_path):
    """If one row holds H as its content_hash and another as a stale dedup_key, the
    content_hash row IS the listing; the other must not be rewritten onto it."""
    db, path = _db(tmp_path)
    db.record_building(_listing(1))
    h = _rows(path)[0]["content_hash"]
    con = sqlite3.connect(str(path))
    # row 2 is unrelated and happens to carry H as a stale dedup_key (planted by hand;
    # the UNIQUE constraint is on dedup_key, so row 1's dedup_key is moved first)
    con.execute("UPDATE buildings SET dedup_key='v2:row-one-elsewhere' WHERE id=1")
    con.execute("INSERT INTO buildings (source_channel, lot_address, dedup_key, content_hash,"
                " price) VALUES ('E-Agent', 'Other', ?, 'v2:other', 1)", (h,))
    con.commit()
    con.close()
    assert db.record_building(_listing(1, 700000)) == "updated"
    rows = {r["id"]: r for r in _rows(path)}
    assert rows[1]["price"] == 700000
    assert rows[2]["content_hash"] == "v2:other" and rows[2]["price"] == 1


def test_an_integrity_error_is_never_reported_as_unchanged(tmp_path, caplog):
    """'unchanged' told every caller the listing was safely stored. It was not."""
    db, path = _db(tmp_path)
    con = sqlite3.connect(str(path))
    # Any UNIQUE constraint the lookup cannot see stands in for whatever the next one is.
    con.execute("CREATE UNIQUE INDEX t_one_address ON buildings(lot_address)")
    con.commit()
    con.close()
    assert db.record_building(_listing(1)) == "new"
    clash = _listing(1, lot_number="99")              # a different identity, same address
    with caplog.at_level(logging.WARNING, logger="spb.db"):
        got = db.record_building(clash)
    assert got == "rejected", got
    assert any(r.levelno >= logging.ERROR and "NOT stored" in r.getMessage()
               for r in caplog.records), "the rejection was not logged as an error"


# ------------------------------------------------------------------ the one-time repair

def _plant(path, rows):
    con = sqlite3.connect(str(path))
    for r in rows:
        cols = list(r)
        con.execute(f"INSERT INTO buildings ({', '.join(cols)}) VALUES "
                    f"({', '.join('?' for _ in cols)})", tuple(r[c] for c in cols))
    con.commit()
    con.close()


def _trap_rows():
    def row(i, ch, dk, sup=None, **kw):
        r = {"id": i, "source_channel": "E-Agent", "lot_address": f"Lot {i}",
             "price": 500000, "content_hash": ch, "dedup_key": dk, "superseded_by": sup}
        r.update(kw)
        return r
    return [
        row(1, "v2:mig1", "v2:harv1"),                               # live trap
        row(2, "v2:mig2", "v2:harv2", WITHDRAWN_FROM_SOURCE),        # withdrawn trap
        row(3, "v2:mig3", "v2:harv3", "v2:keep4"),                   # superseded trap
        row(4, "v2:keep4", "v2:keep4"),                              # healthy keeper
        row(5, "v2:ok5", "evo||lot 5||colac||500000"),               # legacy key: not a trap
        row(6, "v2:ok6", "v2:ok6", "v2:mig1"),                       # points AT trap 1
        # a trap whose re-attach would collide with another row's content_hash
        row(7, "v2:mig7", "v2:taken8"),
        row(8, "v2:taken8", "evo||lot 8||colac||500000"),   # legacy key, holds taken8
    ]


def test_the_repair_plan_reattaches_live_and_withdrawn_and_aligns_the_rest(tmp_path):
    import repair_rows as RR
    _db_, path = _db(tmp_path)
    _plant(path, _trap_rows())
    plan, skipped = RR.find_identity_traps(_rows(path))
    by_id = {p["id"]: p for p in plan}
    assert by_id[1]["set"] == {"content_hash": "v2:harv1"}
    assert by_id[2]["set"] == {"content_hash": "v2:harv2"}
    assert by_id[3]["set"] == {"dedup_key": "v2:mig3"}
    assert set(by_id) == {1, 2, 3}, "a healthy or legacy-keyed row was planned"
    assert [s["id"] for s in skipped] == [7], skipped
    # the pointer at trap 1 follows it
    assert by_id[1]["repoint"] == ("v2:mig1", "v2:harv1")


def test_the_repair_applies_compare_and_set_is_idempotent_and_undoable(tmp_path):
    import repair_rows as RR
    _db_, path = _db(tmp_path)
    _plant(path, _trap_rows())
    before = {r["id"]: (r["content_hash"], r["dedup_key"], r["superseded_by"])
              for r in _rows(path)}

    con = sqlite3.connect(str(path))
    plan, _skipped = RR.find_identity_traps(_rows(path))
    # something moved row 3 between the plan and the write: it must be left alone
    con.execute("UPDATE buildings SET content_hash='v2:moved3' WHERE id=3")
    con.commit()
    undo = RR.apply_identity_repair(con, plan, stamp="2026-10-07T20:00:00")
    con.commit()
    after = {r["id"]: (r["content_hash"], r["dedup_key"], r["superseded_by"])
             for r in _rows(path)}
    assert after[1][:2] == ("v2:harv1", "v2:harv1")
    assert after[2][:2] == ("v2:harv2", "v2:harv2")
    assert after[3][:2] == ("v2:moved3", "v2:harv3"), "a row that moved was overwritten"
    assert after[6][2] == "v2:harv1", "the supersede pointer was left dangling"
    assert [u["id"] for u in undo["rows"]] == [1, 2]

    # idempotent: a second night plans nothing for the rows already repaired
    plan2, _ = RR.find_identity_traps(_rows(path))
    assert {p["id"] for p in plan2} <= {3}, plan2

    # the undo log is kept in the database (it survives the runner) and reverses it
    logged = con.execute("SELECT COUNT(*) FROM identity_repair_log").fetchone()[0]
    assert logged == 2
    RR.undo_identity_repair(con, "2026-10-07T20:00:00")
    con.commit()
    con.close()
    restored = {r["id"]: (r["content_hash"], r["dedup_key"], r["superseded_by"])
                for r in _rows(path)}
    for i in (1, 2, 6):
        assert restored[i] == before[i], (i, restored[i], before[i])


def test_the_nightly_report_changes_nothing_and_apply_writes(tmp_path, monkeypatch):
    import config
    import repair_rows as RR
    _db_, path = _db(tmp_path)
    _plant(path, _trap_rows())
    monkeypatch.setattr(config, "DATABASE_PATH", path)
    monkeypatch.setenv("SPB_SKIP_REREAD", "1")
    with contextlib.redirect_stdout(io.StringIO()) as out:
        assert RR.main(False) == 0
    assert "identity" in out.getvalue().lower()
    assert _rows(path)[0]["content_hash"] == "v2:mig1", "a report-only run wrote"
    with contextlib.redirect_stdout(io.StringIO()):
        assert RR.main(True) == 0
    rows = {r["id"]: r for r in _rows(path)}
    assert rows[1]["content_hash"] == "v2:harv1"
    assert rows[3]["dedup_key"] == "v2:mig3"
    with contextlib.redirect_stdout(io.StringIO()):
        assert RR.main(True) == 0                       # a second night: nothing to do
    assert {r["id"]: r["content_hash"] for r in _rows(path)} == \
        {i: r["content_hash"] for i, r in rows.items()}


# ------------------------------------------------------------------ several nights

def test_five_nights_store_every_lot_and_never_duplicate(tmp_path, monkeypatch):
    """Night 1 inserts; the 30 Jul migration rewrites two rows' hashes; nights 2-5 each
    harvest the same list (prices move on night 4) with repair_rows --apply after the
    harvest, as run_daily runs them. Every night: every listing is stored, the table never
    grows, and the prices are tonight's."""
    import config
    import repair_rows as RR
    db, path = _db(tmp_path)
    monkeypatch.setattr(config, "DATABASE_PATH", path)
    monkeypatch.setenv("SPB_SKIP_REREAD", "1")
    lots = [21, 23, 25, 27]
    for n in lots:
        assert db.record_building(_listing(n)) == "new"
    _migrate_the_30_jul_way(path, 1, suburb="Mernda")
    _migrate_the_30_jul_way(path, 2, suburb="Mernda", lot_number="0")
    for night in (2, 3, 4, 5):
        price = 811000 if night < 4 else 799000
        outcomes = [db.record_building(_listing(n, price)) for n in lots]
        assert outcomes == (["updated"] * 4 if night == 4 else ["unchanged"] * 4), \
            f"night {night}: {outcomes}"
        with contextlib.redirect_stdout(io.StringIO()):
            assert RR.main(True) == 0
        rows = _rows(path)
        assert len(rows) == len(lots), f"night {night}: {len(rows)} rows"
        assert all(r["price"] == price for r in rows), \
            f"night {night}: {[r['price'] for r in rows]}"
        assert all(r["content_hash"] == r["dedup_key"] for r in rows)
        assert all(r["superseded_by"] is None for r in rows)


# ------------------------------------------------------------------ the migration

def test_the_migration_writes_dedup_key_and_keeps_the_harvesters_hash(tmp_path, monkeypatch):
    """Re-running the migration must not re-create the trap -- or its mirror image, a
    second copy of every row whose columns enrichment has filled."""
    import config
    import migrate_buildings_identity as M
    db, path = _db(tmp_path)
    monkeypatch.setattr(config, "DATABASE_PATH", path)
    for n in (1, 2):
        db.record_building(_listing(n))
    harvester = {r["id"]: r["content_hash"] for r in _rows(path)}
    con = sqlite3.connect(str(path))
    # enrichment fills a suburb on row 1; row 2 is a pre-hash legacy row
    con.execute("UPDATE buildings SET suburb='Mernda' WHERE id=1")
    con.execute("UPDATE buildings SET dedup_key='legacy||north 2||||811000', "
                "content_hash=NULL WHERE id=2")
    con.commit()
    con.close()
    with contextlib.redirect_stdout(io.StringIO()):
        assert M.main(force=True) == 0
    rows = {r["id"]: r for r in _rows(path)}
    assert all(r["content_hash"] == r["dedup_key"] for r in rows.values()), \
        [(r["content_hash"], r["dedup_key"]) for r in rows.values()]
    assert rows[1]["content_hash"] == harvester[1], \
        "the migration re-hashed a row the current harvester inserted"
    assert rows[2]["content_hash"].startswith(HASH_RECIPE_VERSION + ":")
    # and the next harvest updates both in place
    assert db.record_building(_listing(1, 700000)) == "updated"
    assert len(_rows(path)) == 2


def test_the_migration_on_the_trap_follows_the_repairs_rule(tmp_path, monkeypatch):
    """Run on the database as it stands (before repair_rows has put it straight): a live
    trap row is re-attached and the twin pointing at it follows; a superseded trap twin
    keeps its content_hash and only its dedup_key is aligned -- the same split as
    repair_rows.find_identity_traps, so the two tools never disagree about a row."""
    import config
    import migrate_buildings_identity as M
    _db_, path = _db(tmp_path)
    monkeypatch.setattr(config, "DATABASE_PATH", path)
    _plant(path, _trap_rows()[:4] + [_trap_rows()[5]])   # ids 1, 2, 3, 4 and 6
    with contextlib.redirect_stdout(io.StringIO()):
        assert M.main(force=True) == 0
    rows = {r["id"]: r for r in _rows(path)}
    assert (rows[1]["content_hash"], rows[1]["dedup_key"]) == ("v2:harv1", "v2:harv1")
    assert (rows[2]["content_hash"], rows[2]["dedup_key"]) == ("v2:harv2", "v2:harv2")
    assert (rows[3]["content_hash"], rows[3]["dedup_key"]) == ("v2:mig3", "v2:mig3"), \
        "a superseded twin was re-attached to the harvester's hash"
    assert rows[6]["superseded_by"] == "v2:harv1", "the pointer was left dangling"
    assert rows[3]["superseded_by"] == "v2:keep4"
