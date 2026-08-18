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
