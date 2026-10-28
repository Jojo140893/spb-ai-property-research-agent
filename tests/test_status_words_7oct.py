"""Status words the availability parser never knew (7 Oct 2026, plan rank 9, safe subset).

Coleen, 7 Oct 2026: the app shows fewer lots than E-Agent for the same search. Part of the
gap is a lot whose own line states its status in a word the parser did not know, so the
row was stored with no availability and kept off every recommendation:

  * a one-letter typo of Available -- "311 Avallable Slgnature 2B2BN $1,495,000" (Prince
    Parade, 3 lots), "Avallable Clyde North 2026 Townhouse" (Ceduna Hattan), "Volos
    Availlable Owner Occ only" (AVIA). Measured on the 6 Oct database: 15 live rows, 9 of
    them stored with no status.
  * a word that says the lot has GONE -- "1102 Exchanged Prestige 3B2B2C", "$683,450
    CONTRACT ISSUED Brochure", "4402 DEPOSIT Detached Stanmore Circuit". These were blank
    too, so they sat in "needs confirming" when the line itself says they are gone.

Decisions taken by the owner (7 Oct): typos read as Available; DEPOSIT, CONTRACT ISSUED and
EXCHANGED read as Not Available. EOI and RESALE are Coleen's call and are NOT changed here
-- this file pins that they still read as nothing. So does 'Exclusive', to the parser: since
28 Oct 2026 (Ahsan: "fix the Exclusive issue") it is decided per document, never by the line
alone, by repair_rows.find_exclusive_status (tests/test_exclusive_status_28oct.py).

DEPOSIT IS NARROW ON PURPOSE. The word is on 310 live rows and almost all of them are about
money, not status: "- 5% DEPOSIT", "$5k initial deposit and balance of 5%", "5% LAND
DEPOSIT REQUIRED", "Interest on Deposit." Only an upper-case DEPOSIT standing in a status
cell -- after a lot number or a price, not after a percentage or LAND/BUILD -- is a status.

A parser change reaches stored rows only through repair_rows (memory:
parser-change-runs-through-repair-rows), so the repair is tested here too: it changes a row
only where the stored value is what the OLD table produced, so a status somebody or
something else wrote is never overwritten.
"""
import sqlite3
import sys

import pytest

sys.path.insert(0, "api")

from sources.feature_extract import parse_availability  # noqa: E402


# ------------------------------------------------------------------ the parser

@pytest.mark.parametrize("line, want", [
    # one-letter typos of Available, as the 6 Oct rows print them
    ("311 Avallable Slgnature 2B2BN $1,495,000 $1,158,625 $149,500", "Available"),
    ("Avallable Clyde North 2026 Townhouse 3771 168 6 Embla", "Available"),
    ("Tropea 190 Fortitude 190m2 4 2 2 2 $429,400 $974,400 Volos Availlable Owner Occ only",
     "Available"),
    ("Lot 12 Avaliable $650,000", "Available"),            # transposed letters
    ("Lot 12 Availble $650,000", "Available"),             # a dropped letter
    # ...and a negated typo is still a negation, never "Available"
    ("Lot 12 Not Avallable $650,000", "Not Available"),
    ("Lot 12 Unavallable $650,000", "Not Available"),
    # the gone words
    ("1102 Exchanged Prestige 3B2B2C 162.3m2 13.4m2 175.7m2 $4,150,000", "Not Available"),
    ("213 $337,000 Neo 13 Vogue 3 1 2 1 121.84 $346,450 $683,450 CONTRACT ISSUED Brochure",
     "Not Available"),
    ("Contract Issued 1195 Armoury Rd Double Sara Traditional 10 300 19", "Not Available"),
    ("24 Land Only 9 318.6 Registered $918,000.00 N/A $918,000.00 Contract Issued",
     "Not Available"),
    ("4402 DEPOSIT Detached Stanmore Circuit Burnley 4-2-2-1 168.33", "Not Available"),
    ("704 12.5 28 350 $370,000 DEPOSIT", "Not Available"),
    ("146 DEPOSIT Detached Wanderer", "Not Available"),
])
def test_the_new_words_are_read(line, want):
    assert parse_availability(line) == want, line


@pytest.mark.parametrize("line, want", [
    # DEPOSIT about money is not a status
    ("LOT 1850 COLEDALE DRIVE , MELTON - 5% DEPOSIT", None),
    ("LOT 5532 VERDAY BLVD, TARNEIT - 10% DEPOSIT", None),
    ("LOT 134 MOHIT RISE, CRAIGIEBURN - ONLY 5% LAND DEPOSIT REQUIRED", None),
    # (Exclusive here is find_exclusive_status's to read, per document; not the parser's)
    ("Aplace Exclusive $5k initial deposit and balance of 5% at contract signing", None),
    ("$1k Build Deposit, Balance of Land Deposit at Unconditional Available", "Available"),
    ("Stage 16 - POS Deposit", None),
    ("Park LAND DEPOSIT Initial deposit $2,000 BOD On Hold", "On Hold"),
    ("the $10,000 deposit already paid Available", "Available"),
    # a deposit AMOUNT in capitals is a payment term, not a status cell
    ("Lot 7 $1,000 DEPOSIT HOLDS YOUR LOT $650,000", None),
    ("Lot 7 $5,000 DEPOSIT Secures $650,000", None),
    ("Lot 7 2,000 DEPOSIT $650,000", None),
    ("Lot 7 $650,000 Build 10% DEPOSIT", None),
    # EOI and RESALE are Coleen's call, so unchanged; Exclusive is decided per document by
    # repair_rows.find_exclusive_status (28 Oct 2026). To the parser all still read as nothing
    ("Aplace Exclusive 4 | 2 | 2 $444,610 $974,610", None),
    ("Lot 31 EOI $612,000", None),
    ("Lot 31 RESALE $612,000", None),
    ("Lot 31 Resale $612,000", None),
    # the old table still decides everything it decided before
    ("Deposit Taken 3 Double Dalton 18 Modern", "Reserved"),
    ("Sold Contracts Issued - RT", "Sold"),
    ("Lot 9 Available $500,000", "Available"),
    ("Lot 9 Not Available $500,000", "Not Available"),
    ("Lot 9 On Hold $500,000", "On Hold"),
    ("Lot 9 $500,000", None),
])
def test_what_is_not_a_status_stays_unread(line, want):
    assert parse_availability(line) == want, line


def test_the_old_table_is_kept_for_the_repair():
    """repair_rows needs the answer the parser gave BEFORE today, to touch only rows whose
    stored value came from it."""
    from sources.feature_extract import parse_availability_before_7oct as old
    assert old("311 Avallable Slgnature 2B2BN $1,495,000") is None
    assert old("4402 DEPOSIT Detached Stanmore") is None
    assert old("1102 Exchanged Prestige") is None
    assert old("Lot 9 Available $500,000") == "Available"


# ------------------------------------------------------------------ the repair

def _row(id_, text, status=None, channel="E-Agent", **kw):
    row = {"id": id_, "superseded_by": None, "source_channel": channel,
           "source_text": text, "lot_address": text[:40], "availability_status": status}
    row.update(kw)
    return row


def test_the_repair_reads_the_new_words_off_stored_rows():
    import repair_rows as RR
    rows = [
        _row(1, "311 Avallable Slgnature 2B2BN $1,495,000"),
        _row(2, "4402 DEPOSIT Detached Stanmore Circuit"),
        _row(3, "1102 Exchanged Prestige 3B2B2C $4,150,000", status=""),
        _row(4, "Lot 9 Available $500,000", status="Available"),          # nothing new
        # a status written by something else (the sheet check, a Sold retirement) is not
        # the old parser's answer, so it is left exactly as it is
        _row(5, "STORAGE Cold Shell 255,000.00 Avallable", status="Not Available"),
        # Proxima's status is the portal's own field, never read off the text
        _row(6, "Lot 3 Unit 3, 1 Contract Issued Ave, X, NSW, 2000", channel="Proxima",
             status="For Sale"),
        # a retired row is left alone
        _row(7, "311 Avallable Signature", superseded_by="v2:abc"),
        # not this pass's: Exclusive is find_exclusive_status's, per document (28 Oct 2026)
        _row(8, "Aplace Exclusive 4 | 2 | 2 $444,610"),
    ]
    got = {r["id"]: (old, new) for r, old, new in RR.find_status_words_reread(rows)}
    assert got == {1: (None, "Available"), 2: (None, "Not Available"),
                   3: ("", "Not Available")}, got


def _db(tmp_path):
    from database import ResearchDatabase
    path = tmp_path / "status.db"
    ResearchDatabase(path)
    conn = sqlite3.connect(str(path))
    for id_, text, status in ((1, "311 Avallable Slgnature 2B2BN $1,495,000", None),
                              (2, "4402 DEPOSIT Detached Stanmore Circuit", None),
                              (3, "Lot 9 Available $500,000", "Available")):
        conn.execute("INSERT INTO buildings (id, source_channel, lot_address, source_text, "
                     "availability_status, price, dedup_key, content_hash) "
                     "VALUES (?,?,?,?,?,?,?,?)",
                     (id_, "E-Agent", text[:30], text, status, 500000,
                      f"v2:{id_}", f"v2:{id_}"))
    conn.commit()
    conn.close()
    return path


def test_the_nightly_repair_writes_them(tmp_path, monkeypatch):
    """Through repair_rows.main(True), the way run_daily step 4/7 runs it."""
    import contextlib
    import io
    import config
    import repair_rows as RR
    path = _db(tmp_path)
    monkeypatch.setattr(config, "DATABASE_PATH", path)
    monkeypatch.setenv("SPB_SKIP_REREAD", "1")
    with contextlib.redirect_stdout(io.StringIO()):
        assert RR.main(False) == 0                     # report only: nothing written
    conn = sqlite3.connect(str(path))
    assert dict(conn.execute("SELECT id, availability_status FROM buildings")) == {
        1: None, 2: None, 3: "Available"}
    conn.close()
    with contextlib.redirect_stdout(io.StringIO()):
        assert RR.main(True) == 0
    conn = sqlite3.connect(str(path))
    got = dict(conn.execute("SELECT id, availability_status FROM buildings"))
    prev = dict(conn.execute("SELECT id, status_previous FROM buildings"))
    conn.close()
    assert got == {1: "Available", 2: "Not Available", 3: "Available"}, got
    assert prev[1] is None and prev[2] is None


def test_the_repair_is_compare_and_set(tmp_path):
    """A row whose status moved between the plan and the write is not overwritten."""
    import repair_rows as RR
    path = _db(tmp_path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM buildings")]
    plan = RR.find_status_words_reread(rows)
    assert len(plan) == 2
    # tonight's harvest wrote a status in between
    conn.execute("UPDATE buildings SET availability_status='Sold' WHERE id=1")
    conn.commit()
    written = RR.apply_status_words_reread(conn, plan)
    conn.commit()
    got = dict(conn.execute("SELECT id, availability_status FROM buildings"))
    conn.close()
    assert written == 1
    assert got[1] == "Sold", "a value written after the plan was overwritten"
    assert got[2] == "Not Available"
