"""Exclusive in a status cell, on a list that states Available, is Available (28 Oct 2026).

Ahsan, 28 Oct 2026: "fix the Exclusive issue". Until then Exclusive was left unread as
Coleen's call (7 Oct, tests/test_status_words_7oct.py). Aldrich's Beveridge lots 2243-2245
(ids 30070-30072) print "Exclusive" in the slot where 138 lines of the same list print
Available. Stored with no status on a list that states Available, the search refused them
as document_states_available, and a Beveridge VIC <= $500k search returned nothing.

DECIDED PER DOCUMENT, NOT BY THE PARSER. A line alone cannot tell APLACE's QLD STATUS
column ("Aplace Exclusive" beside Available, Reserved, EOI Received) from its VIC RELEASE
TYPE column ("Aplace Exclusive" or "General", on lists that state nothing), and Silkwood's
remark column opens "EXCLUSIVE - INVESTORS WELCOME". A global rule measured on the 28 Oct
database hid 51 sibling rows that show today. So parse_availability is unchanged, and
repair_rows.find_exclusive_status writes Available onto a stored row only when its own
document has a live Available row and nothing else on the line is a status word: 100 rows
on 10 documents (APLACE 79, Aldrich 11, EVO 5 blank and 5 'Settled').

EOI, RESALE, APLACE "General", "EOI Received" and "8% Leaseback" stay undecided.

The 5 EVO rows stored 'Settled' are a misread: on EVO lists "Settled" is the title column,
and repair_rows.find_sold_word_without_status read it out of lot_address with no
status_previous. They are corrected here, and that pass no longer reaches these rows.
"""
import contextlib
import io
import sqlite3

import pytest

from sources.feature_extract import exclusive_status_cell, parse_availability


# The lines as the 28 Oct 2026 lists print them (ids in the comments).
ALDRICH = ("2243 Ooranya Estate Beveridge Exclusive Bristol 11 $183,000 $305,055 $488,055 "
           "JAN 2027 West")                                                       # 30070
EVO = ("West 777 Harlow Tarneit EVO Exclusive Carlton 18 Elevate $307,500 $358,650 "
       "$666,150 NA $666,150 Titled North")                                       # 29510
EVO_PLAIN = ("Geelong 12031 Warralily Armstrong Creek Exclusive Richmond 13 Elevate "
             "$280,000 $301,200 $581,200 NA $581,200 Titled North")               # 30038
EVO_SETTLED = ("North 60405 Cloverton Kalkallo Exclusive Richmond 13 Bright $320,000 "
               "$295,430 $615,430 NA $615,430 Settled West 220")                  # 30036
APLACE_END = ("Subury 137 Kildare Street Rockcliffe Dec-27 294 10.5 x 28 $326,500 Mackay 05 "
              "167.08 4 2 2 $392,300 $718,800 Aplace Exclusive")                  # 834
APLACE_2K = ("Woodford 11 Park Street Park Rise Jan-27 600 Rectangular $530,000 Nelson A 4 "
             "206.9 5 | 2.5 | 2 $444,610 $974,610 Aplace Exclusive $2k initial deposit and "
             "balance of 5% at contract signing")                                 # 889
APLACE_5K = ("Ripley 21 Amory Street Ripley Jan-27 400 Rectangular $330,000 Duval 05 149.49 "
             "4 | 2 | 2 $414,950 $753,950 Aplace Exclusive $5k initial deposit and balance "
             "of 5% at contract signing")
SILKWOOD = ("EXCLUSIVE - INVESTORS WELCOME PR8623 33 Caboolture Central Springs Estate "
            "October 2026 470 $619,000")                                          # 15860


# ------------------------------------------------------------------ the status cell

@pytest.mark.parametrize("line", [ALDRICH, EVO, EVO_PLAIN, EVO_SETTLED, APLACE_END,
                                  APLACE_2K, APLACE_5K])
def test_exclusive_in_a_status_cell_is_seen(line):
    assert exclusive_status_cell(line), line
    # ...and the parser itself still reads nothing: the document decides, not the line.
    assert parse_availability(line) is None, line


@pytest.mark.parametrize("line", [
    SILKWOOD,                                                       # the first word
    "EXCLUSIVE - Owner Occ PR0122 33 Caboolture Central Springs Estate",
    "EXCLUSIVE OFFER - PRICE NEGOTIABLE PR0111 33 Caboolture Central Springs Estate",
    "EXCLUSIVE PR0001 33 Caboolture Central Springs Estate October 2026 470 $619,000",
    "Caboolture Central Springs EXCLUSIVE OFFER - INVESTORS WELCOME",
    "Caboolture Central Springs Exclusive - Owner Occ",
    "Caboolture Central Springs Exclusive: Owner Occ",
    "Caboolture Central Springs Exclusive \u2013 Owner Occ",
    "SMSF Ready Exclusive Packages",                                # a list title
    "HOUSE AND LAND STOCKLIST - EXCLUSIVE LOTS",                    # the APLACE page title
    "Ripley APLACE EXCLUSIVES",                                     # the APLACE banner
    "Lot 7 is not on hold exclusively $929,000",
    "Clyde North Exclusive Release $709,875",
    "Lot 7 Exclusive to E-Agent $650,000",
    "",
    None,
])
def test_exclusive_that_is_not_a_status_cell_is_not_seen(line):
    assert not exclusive_status_cell(line), line


# ------------------------------------------------------------------ the plan

_DOC = "https://sheets.example.test/aldrich-homes-vic"


def _row(id_, text, status=None, doc=_DOC, channel="E-Agent", previous=None, **kw):
    row = {"id": id_, "superseded_by": None, "source_channel": channel,
           "source_text": text, "lot_address": text[:110], "source_url": doc,
           "availability_status": status, "status_previous": previous,
           "builder_name": "Testco Homes"}
    row.update(kw)
    return row


def _sibling(id_, doc=_DOC, **kw):
    """A line of the same list that states Available."""
    return _row(id_, "2250 Ooranya Estate Beveridge Available Bristol 11 $183,000 $305,055 "
                     "$488,055", status="Available", doc=doc, **kw)


def _plan(rows):
    import repair_rows as RR
    return {r["id"]: (old, new) for r, old, new in RR.find_exclusive_status(rows)}


def test_a_row_on_a_list_that_states_available_is_planned():
    rows = [_sibling(1), _row(2, ALDRICH), _row(3, ALDRICH.replace("2243", "2244"), status="")]
    assert _plan(rows) == {2: (None, "Available"), 3: ("", "Available")}


def test_a_list_that_states_no_available_is_left_alone():
    vic = "https://drive.example.test/aplace-vic-release-type"
    qld = "https://drive.example.test/aplace-qld-9df15fd7"
    rows = [
        # APLACE VIC: a RELEASE TYPE column, "Aplace Exclusive" or "General", no status
        _row(1, APLACE_END, doc=vic),
        _row(2, APLACE_END.replace("Aplace Exclusive", "General").replace("137", "139"),
             doc=vic),
        # APLACE QLD 9df15fd7: Exclusive, Reserved and EOI Received, no Available
        _row(3, APLACE_2K, doc=qld),
        _row(4, APLACE_2K.replace("Aplace Exclusive", "Reserved").replace("11 Park", "13 Park"),
             status="Reserved", doc=qld),
        _row(5, APLACE_2K.replace("Aplace Exclusive", "EOI Received").replace("11 Park",
                                                                              "15 Park"),
             doc=qld),
    ]
    assert _plan(rows) == {}


def test_a_superseded_available_row_does_not_make_the_list_state_it():
    """The search's doc_statements counts LIVE Available rows only."""
    rows = [_sibling(1, superseded_by="v2:newer"), _row(2, ALDRICH)]
    assert _plan(rows) == {}


def test_a_gone_word_on_the_line_wins():
    rows = [_sibling(1), _row(2, ALDRICH.replace("Exclusive", "Exclusive Sold"))]
    assert _plan(rows) == {}


def test_what_this_pass_never_touches():
    rows = [
        _sibling(1),
        _row(2, "Beveridge " + SILKWOOD),          # Silkwood's leading remark, mid-line
        _row(3, SILKWOOD),                          # Silkwood's leading remark
        _row(4, ALDRICH, status="Reserved"),        # a status somebody stated
        # a 'Settled' something else wrote: it has a status_previous
        _row(5, EVO_SETTLED, status="Settled", previous="On Hold"),
        _row(6, ALDRICH, superseded_by="v2:newer"),                      # retired
        _row(7, "Lot 3 Unit 3, Exclusive Ave, Beveridge, VIC, 3753", channel="Proxima"),
        _row(8, ALDRICH, doc=""),                   # no document to judge by
        _row(9, ALDRICH, doc=None),
        _row(10, ""),                               # no line
    ]
    assert _plan(rows) == {}


def test_a_settled_read_off_the_title_column_is_planned_with_its_old_value():
    """EVO 29512, 29513, 29515, 30036, 30037: stored 'Settled' by the sold-word pass, with
    no status_previous. The plan carries 'Settled', so the compare-and-set writes it into
    status_previous and the rollback can put it back."""
    rows = [_sibling(1), _row(2, EVO_SETTLED, status="Settled"),
            _row(3, EVO_SETTLED.replace("60405", "60406"), status="settled", previous="")]
    assert _plan(rows) == {2: ("Settled", "Available"), 3: ("settled", "Available")}


def test_the_switch():
    import repair_rows as RR
    assert RR.exclusive_status_on({})
    assert RR.exclusive_status_on({"SPB_EXCLUSIVE_STATUS": "1"})
    for off in ("0", "off", "OFF", " false ", "no"):
        assert not RR.exclusive_status_on({"SPB_EXCLUSIVE_STATUS": off}), off


# ------------------------------------------------------------------ the nightly repair

_EVO_DOC = "https://drive.example.test/evo-homes-vic"
_EVO_SIBLING = ("West 778 Harlow Tarneit Available Carlton 18 Elevate $307,500 $358,650 "
                "$666,150 NA $666,150 Titled North")


def _db(tmp_path, rows):
    from database import ResearchDatabase
    path = tmp_path / "exclusive.db"
    ResearchDatabase(path)
    conn = sqlite3.connect(str(path))
    for id_, text, status, previous, doc in rows:
        conn.execute("INSERT INTO buildings (id, source_channel, builder_name, lot_address, "
                     "source_text, source_url, availability_status, status_previous, "
                     "price, dedup_key, content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (id_, "E-Agent", "EVO Homes", text[:110], text, doc, status, previous,
                      615430, f"v2:{id_}", f"v2:{id_}"))
    conn.commit()
    conn.close()
    return path


def _evo_rows():
    return [(1, _EVO_SIBLING, "Available", None, _EVO_DOC),
            # tonight's new Exclusive row: blank, "Settled" in its lot_address
            (2, EVO_SETTLED, None, None, _EVO_DOC),
            # an earlier night's: the sold-word pass already wrote 'Settled'
            (3, EVO_SETTLED.replace("60405", "61538"), "Settled", None, _EVO_DOC)]


def _main(path, monkeypatch, apply_changes, **env):
    import config
    import repair_rows as RR
    monkeypatch.setattr(config, "DATABASE_PATH", path)
    monkeypatch.setenv("SPB_SKIP_REREAD", "1")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert RR.main(apply_changes) == 0
    conn = sqlite3.connect(str(path))
    got = {i: (s, p) for i, s, p in conn.execute(
        "SELECT id, availability_status, status_previous FROM buildings")}
    conn.close()
    return got, out.getvalue()


def test_the_nightly_repair_writes_available_not_settled(tmp_path, monkeypatch):
    """Through repair_rows.main, the way run_daily step 4/7 runs it. Without taking these
    rows out of the sold-word pass, its unconditional write stamps EVO's title-column
    'Settled' onto row 2 first, and the compare-and-set then (rightly) leaves it."""
    path = _db(tmp_path, _evo_rows())
    got, report = _main(path, monkeypatch, False)
    assert got == {1: ("Available", None), 2: (None, None), 3: ("Settled", None)}
    assert "18. Exclusive in a status cell" in report and "ids: 2,3" in report, report
    got, report = _main(path, monkeypatch, True)
    assert got == {1: ("Available", None), 2: ("Available", None),
                   3: ("Available", "Settled")}, got
    assert "2 Exclusive status cell(s) read as Available" in report, report


def test_the_rollback_switch_restores_the_old_behaviour(tmp_path, monkeypatch):
    """SPB_EXCLUSIVE_STATUS=0: nothing planned, and the sold-word pass reads row 2 as it
    did before 28 Oct 2026. This is also the proof the test above can fail."""
    path = _db(tmp_path, _evo_rows())
    got, report = _main(path, monkeypatch, True, SPB_EXCLUSIVE_STATUS="0")
    assert got == {1: ("Available", None), 2: ("Settled", None), 3: ("Settled", None)}, got
    assert "OFF (SPB_EXCLUSIVE_STATUS='0')" in report, report


def test_the_repair_is_compare_and_set(tmp_path):
    """A row whose status moved between the plan and the write is not overwritten."""
    import repair_rows as RR
    path = _db(tmp_path, _evo_rows())
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    plan = RR.find_exclusive_status([dict(r) for r in conn.execute("SELECT * FROM buildings")])
    assert sorted(r["id"] for r, _old, _new in plan) == [2, 3]
    # tonight's harvest read the lot as Sold in between
    conn.execute("UPDATE buildings SET availability_status='Sold' WHERE id=2")
    conn.commit()
    written = RR.apply_status_words_reread(conn, plan)
    conn.commit()
    got = dict(conn.execute("SELECT id, availability_status FROM buildings"))
    conn.close()
    assert written == 1
    assert got[2] == "Sold", "a value written after the plan was overwritten"
    assert got[3] == "Available"


def test_identity_is_untouched_and_a_re_read_keeps_available(tmp_path):
    """Only availability_status and status_previous are written: content_hash and
    dedup_key stay what the harvester computes, so the next read finds the same row. That
    read stores no status for the line (the parser reads none), and record_building never
    blanks a stored one, so the row stays Available."""
    import repair_rows as RR
    from database import ResearchDatabase
    path = tmp_path / "identity.db"
    db = ResearchDatabase(path)

    def listing(line, lot):
        return {"source_channel": "E-Agent", "attribution_scope": "builder",
                "builder_name": "Aldrich Homes", "suburb": "Beveridge", "state": "VIC",
                "lot_number": lot, "lot_address": f"Lot {lot}, Ooranya",
                "source_text": line, "advertised_package_price": 488_055,
                "availability_status": parse_availability(line),
                "source_url_or_ref": _DOC, "date_checked": "28/10/2026"}

    sibling = listing(ALDRICH.replace("2243", "2250").replace("Exclusive", "Available"),
                      "2250")
    exclusive = listing(ALDRICH, "2243")
    assert exclusive["availability_status"] is None
    assert db.record_building(sibling) == "new"
    assert db.record_building(exclusive) == "new"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM buildings")]
    before = {r["id"]: (r["content_hash"], r["dedup_key"]) for r in rows}
    plan = RR.find_exclusive_status(rows)
    assert [r["lot_number"] for r, _old, _new in plan] == ["2243"]
    assert RR.apply_status_words_reread(conn, plan) == 1
    conn.commit()
    after = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT id, content_hash, dedup_key FROM buildings")}
    assert after == before
    conn.close()

    # The same line read again the next night: the same identity, the same row.
    assert db.record_building(listing(ALDRICH, "2243")) in ("unchanged", "updated")
    conn = sqlite3.connect(str(path))
    rid = plan[0][0]["id"]
    got = conn.execute("SELECT availability_status, status_previous, content_hash, "
                       "dedup_key FROM buildings WHERE id=?", (rid,)).fetchone()
    count = conn.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
    conn.close()
    assert count == 2, "the re-read inserted a second row"
    assert got == ("Available", None) + before[rid], got
