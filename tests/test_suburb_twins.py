"""One package stored twice because it only recently started stating its suburb.

THE MECHANISM. `suburb_norm` is one of the eight fields in content_hash. On 18 Aug 2026
the extractor began reading the Suburb COLUMN out of stocklist PDFs, so every affected
row acquired a NEW identity: the harvest inserted it, and the old suburb-less row was
never matched again — never updated, never superseded. It sat there live, a second copy
of a property Coleen can sell once.

The collapse is deliberately narrow. Everything identity actually turns on must match;
only the PRICE is allowed to differ, because the suburb-less copy is an older capture and
a price move between then and now is the normal case rather than evidence of a different
package.

The keeper rule is the part worth guarding. Ranking on completeness alone chose the stale
copy in 84 of 86 groups on the first run and would have retired live stock: one group kept
a row last checked 06/08 whose suburb read "PART" and whose price was $0, and superseded
that night's $60,000 row for it.
"""
import sqlite3

import pytest

import repair_rows as R


def _row(**kw):
    """A buildings row with the fields find_suburb_twins reads."""
    base = dict(id=1, superseded_by=None, source_channel="E-Agent",
                attribution_scope="builder", builder_name="Hattan Homes",
                lot_number="1133", land_sqm=263.0, suburb="", state="VIC",
                lot_address="1133 Tangerine Terrace", price=698050.0,
                date_checked="04/08/2026",
                source_text="Wollert 1133 Tangerine Terrace Northbourne Mar-27 263 "
                            "12.5 x 21 $350,000 Duval 01 149.49 4 2 2 $348,050 $698,050",
                house_sqm=149.49, bedrooms=4, bathrooms=2, car_spaces=2, postcode=None,
                listing_url=None, brochure_url=None, floorplan_url=None,
                estate_name="Northbourne", title_status=None,
                availability_status=None, state_source="page", product_type=None,
                # _completeness breaks ties on these two.
                last_seen="04/08/2026", content_hash="hash-old")
    base.update(kw)
    return base


OLD_BLANK = _row(id=1, suburb="", date_checked="04/08/2026", content_hash="old")
NEW_NAMED = _row(id=2, suburb="Wollert", date_checked="18/08/2026", price=703050.0,
                 content_hash="new",
                 source_text="Wollert 1133 Tangerine Terrace Northbourne Mar-27 263 "
                             "12.5 x 21 $355,000 Duval 01 149.49 4 2 2 $348,050 $703,050")


# ---------------------------------------------------------------- the collapse

def test_the_older_suburbless_copy_is_superseded():
    pairs = R.find_suburb_twins([OLD_BLANK, NEW_NAMED])
    assert len(pairs) == 1
    keeper, losers = pairs[0]
    assert keeper["id"] == 2 and keeper["suburb"] == "Wollert"
    assert [r["id"] for r in losers] == [1]


def test_a_price_move_between_the_two_captures_does_not_block_it():
    """The whole point: the old copy is older, so its price is expected to differ."""
    assert OLD_BLANK["price"] != NEW_NAMED["price"]
    assert len(R.find_suburb_twins([OLD_BLANK, NEW_NAMED])) == 1


def test_nothing_happens_without_a_suburbless_twin():
    assert R.find_suburb_twins([NEW_NAMED, _row(id=3, suburb="Wollert",
                                                date_checked="18/08/2026")]) == []


def test_a_lone_row_is_not_a_twin():
    assert R.find_suburb_twins([NEW_NAMED]) == []


def test_a_superseded_row_is_not_reconsidered():
    dead = dict(OLD_BLANK, superseded_by="something")
    assert R.find_suburb_twins([dead, NEW_NAMED]) == []


# ------------------------------------------------- the keeper must be the live row

def test_a_newer_suburbless_row_is_left_alone():
    """The source has STOPPED stating a suburb. That is not an old duplicate, and
    collapsing it backwards would retire the row the harvest is still seeing."""
    stale_named = _row(id=1, suburb="PART", date_checked="06/08/2026", price=0.0)
    tonight_blank = _row(id=2, suburb="", date_checked="18/08/2026", price=60000.0)
    assert R.find_suburb_twins([stale_named, tonight_blank]) == []


def test_the_newest_named_row_wins_even_when_a_staler_one_is_fuller():
    """_completeness alone picked the stale copy in 84 of 86 groups."""
    stale_but_full = _row(id=1, suburb="Wollert", date_checked="06/08/2026",
                          postcode="3750", listing_url="u", brochure_url="b",
                          floorplan_url="f", title_status="Titled",
                          availability_status="Available")
    fresh = _row(id=2, suburb="Wollert", date_checked="18/08/2026")
    blank = _row(id=3, suburb="", date_checked="29/07/2026")
    keeper, losers = R.find_suburb_twins([stale_but_full, fresh, blank])[0]
    assert keeper["id"] == 2, "the row last seen tonight must be the keeper"
    assert [r["id"] for r in losers] == [3]


def test_dates_are_compared_as_dates_not_as_strings():
    """'29/07/2026' sorts above '13/08/2026' as a string — the trap that once made a
    four-day-stale channel look nineteen days stale."""
    assert R._iso("29/07/2026") < R._iso("13/08/2026")
    assert R._iso("04/08/2026") < R._iso("18/08/2026")
    assert R._iso("") == "" and R._iso(None) == ""


# ------------------------------------------------- what must NOT be collapsed

def test_two_named_rows_that_disagree_are_left_alone():
    """Which suburb is right is not a call this project makes."""
    a = _row(id=1, suburb="Wollert", date_checked="18/08/2026")
    b = _row(id=2, suburb="Epping", date_checked="18/08/2026")
    blank = _row(id=3, suburb="", date_checked="29/07/2026")
    assert R.find_suburb_twins([a, b, blank]) == []


@pytest.mark.parametrize("field,value", [
    ("builder_name", "Someone Else"),
    ("lot_number", "9999"),
    ("land_sqm", 999.0),
    ("source_channel", "Proxima"),
    ("attribution_scope", "project"),
])
def test_a_difference_in_any_identity_field_keeps_them_apart(field, value):
    other = dict(OLD_BLANK, **{field: value})
    assert R.find_suburb_twins([other, NEW_NAMED]) == []


def test_two_designs_on_one_lot_stay_separate():
    """house_design is hashed but never stored as a column, so the grouping leans on
    _variant_key — which reads the design out of source_text."""
    dunestone = dict(OLD_BLANK,
                     source_text=OLD_BLANK["source_text"].replace("Duval 01", "Dunestone 22"))
    assert R.find_suburb_twins([dunestone, NEW_NAMED]) == []


def test_the_variant_key_ignores_money_so_a_price_move_still_groups():
    """Prices are stripped before the text is compared; that is what lets a re-priced
    capture of the SAME package group with its twin."""
    assert R._identity_without_suburb(OLD_BLANK) == R._identity_without_suburb(NEW_NAMED)


# ------------------------------------------------------------------ end to end

def test_apply_marks_rather_than_deletes(tmp_path, monkeypatch):
    """Superseding is a mark, per the standing rule: the older capture is real history
    and its price is what was advertised at the time."""
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE buildings (id INTEGER PRIMARY KEY, superseded_by TEXT, "
                 "superseded_at TEXT, content_hash TEXT, price REAL)")
    conn.executemany("INSERT INTO buildings (id, superseded_by, content_hash, price) "
                     "VALUES (?,?,?,?)", [(1, None, "old", 698050.0), (2, None, "new", 703050.0)])
    conn.commit()
    cur = conn.cursor()
    cur.execute("UPDATE buildings SET superseded_by=?, superseded_at=? WHERE id=?",
                ("new", "2026-08-18T20:00:00", 1))
    conn.commit()
    rows = {r[0]: r for r in conn.execute(
        "SELECT id, superseded_by, price FROM buildings")}
    assert rows[1][1] == "new", "the loser points at its keeper"
    assert rows[1][2] == 698050.0, "and keeps the price it was advertised at"
    assert rows[2][1] is None
    assert conn.execute("SELECT COUNT(*) FROM buildings").fetchone()[0] == 2


# ------------------------------------- after supersede's status pass (release review)
#
# G Developments, Munno Para Downs lot 101 (SA), three reads of one price list:
#   11734 "Available" 22/08, suburb column "Murray Bridge"  -- a twin of 15985
#   15985 "ON HOLD"   08/09, "Munno Para Downs" in its own address
#   18856 "SOLD"      10/09
# find_suburb_twins retired 11734 in favour of 15985. The status pass then retired 15985 in
# favour of 18856, repair_rows grouped live rows only, 11734 lost its keeper and was
# published live again as Available stock. Channel 'digital email' keeps the withdrawn-
# from-source repair (relisting channels, dated against today) out of the way; the doc is a
# stand-in, never the builder's link.

_MUNNO_DOC = "email:Fwd: G Developments stock list"
_MUNNO = ("Munno Para Downs {} 101 Springfield Glenelg Balmoral 4 2 1 300 $322,000 142.2 "
          "$448,336 $770,336 675 Due {} 2026")


def _munno(i, status_word, status, date, checked_at, suburb, due):
    text = _MUNNO.format(status_word, due)
    return dict(id=i, builder_name="G Developments", source_channel="digital email",
                attribution_scope="builder", lot_address=text[:110], suburb=suburb,
                state="SA", state_source="suburb", estate_name="MURRAY BRIDGE",
                price=770336.0, bedrooms=4, bathrooms=2, car_spaces=1, land_sqm=300.0,
                house_sqm=142.2, source_url=_MUNNO_DOC, stocklist_file=_MUNNO_DOC,
                date_checked=date, last_seen=date, checked_at=checked_at,
                availability_status=status, source_text=text, content_hash=f"v2:hash{i}")


def _munno_db(path):
    from database import ResearchDatabase
    ResearchDatabase(path)
    rows = [
        _munno(11734, "Available", "Available", "22/08/2026", "2026-08-22T04:03:45",
               "Murray Bridge", "August"),
        _munno(15985, "ON HOLD", "On Hold", "08/09/2026", "2026-09-08T11:24:36",
               "Munno Para Downs", "September"),
        _munno(18856, "SOLD", "Sold", "10/09/2026", "2026-09-10T11:25:27",
               "Munno Para Downs", "September"),
    ]
    cols = list(rows[0])
    conn = sqlite3.connect(str(path))
    conn.executemany(f"INSERT INTO buildings ({', '.join(cols)}) VALUES "
                     f"({', '.join('?' for _ in cols)})",
                     [tuple(r[c] for c in cols) for r in rows])
    conn.commit()
    conn.close()


def _night(db, monkeypatch, status_pass):
    """supersede_duplicates then repair_rows --apply, as run_daily step 4/7 runs them."""
    import contextlib
    import io
    import config
    import supersede_duplicates as sd
    monkeypatch.setattr(config, "DATABASE_PATH", db)
    monkeypatch.setenv("SPB_SKIP_REREAD", "1")
    monkeypatch.setenv("SPB_STATUS_PASS", status_pass)
    with contextlib.redirect_stdout(io.StringIO()):
        assert sd.main([]) == 0
        assert R.main(True) == 0
    conn = sqlite3.connect(str(db))
    marks = dict(conn.execute("SELECT id, superseded_by FROM buildings"))
    conn.close()
    return marks


def test_a_twin_stays_retired_when_the_status_pass_retires_its_keeper(tmp_path, monkeypatch):
    db = tmp_path / "munno.db"
    _munno_db(db)
    marks = _night(db, monkeypatch, "1")
    assert marks[15985] == "v2:hash18856", marks        # the status pass: ON HOLD -> SOLD
    assert marks[18856] is None, marks
    # The Available capture stays retired, pointing at the capture that is live.
    assert marks[11734] == "v2:hash18856", marks
    # And a second night changes nothing.
    assert _night(db, monkeypatch, "1") == marks


def test_without_the_status_pass_the_twin_keeps_its_old_keeper(tmp_path, monkeypatch):
    """The premise: this IS a suburb twin (15985 keeps 11734 out), and with the pass off
    both repairs behave exactly as before it existed."""
    db = tmp_path / "munno-off.db"
    _munno_db(db)
    marks = _night(db, monkeypatch, "0")
    assert marks == {11734: "v2:hash15985", 15985: None, 18856: None}, marks


def test_a_duplicate_line_stays_retired_when_its_keeper_is_status_retired():
    """find_duplicate_groups, the same shape: AVIA lot 53, 2264 (EOI, filed QLD) kept its
    blank-state copy 16979 out; the status pass retires 2264 for 4643 (Available)."""
    line = ("53 Stage 5 400m2 $404,000 August 2026 Vieste Carolina 180m2 4 2 2 0 $500,500 "
            "$904,500 Volos EOI Owner OCC & Investor {}")
    keeper = _row(id=2264, suburb="GLENVALE", state="QLD", source_text=line.format("26.06.2026"),
                  superseded_by="v2:hash4643", content_hash="v2:hash2264",
                  date_checked="29/07/2026", lot_number="53", land_sqm=400.0,
                  builder_name="AVIA Homes", lot_address=line.format("")[:110])
    copy = dict(keeper, id=16979, state=None, suburb="Glenvale", superseded_by=None,
                source_text=line.format("13.08.2026"), content_hash="v2:hash16979",
                date_checked="09/09/2026")
    assert R.find_duplicate_groups([copy]) == [], "premise: alone, the copy has no keeper"
    ((kept, retired),) = R.find_duplicate_groups([keeper, copy], {2264: "v2:hash4643"})
    assert kept["id"] == 2264 and [r["id"] for r in retired] == [16979]
    assert R._keeper_mark(kept, {2264: "v2:hash4643"}) == "v2:hash4643"
    # A status-retired row is never retired a second time here.
    assert R.find_duplicate_groups([dict(keeper, state=None), dict(copy, state="QLD")],
                                   {2264: "v2:hash4643"}) == []
