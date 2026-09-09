"""Open the sheet and check what the app says about it is true.

Ahsan, 9 Sep 2026: "i want you to confirm all the listings are present and when i open the
sheet they are at exact number that is written and if they are not then mark them not
available automatically and the buildings that are no longer in the sheets are marked
sold".

The whole decision table is exercised here without a network: check_document takes the
fresh listings as an argument precisely so the judgement can be tested apart from the
downloading.
"""
import collections
import sys

sys.path.insert(0, "api")

import verify_sheet_positions as V


def _stored(row_no, lot="2102", street="2102 Racecourse Rd, Fraser Rise",
            text=None, **kw):
    row = {"id": kw.get("id", row_no or 0), "superseded_by": "",
           "source_url": "https://x/y.xlsx", "builder_name": "Tomorrow Homes",
           "lot_number": lot, "lot_address": street, "source_row": row_no,
           "availability_status": "Available",
           "source_text": text if text is not None else f"TITLED {lot} {street} $724,841"}
    row.update(kw)
    return row


def _fresh(row_no, lot="2102", street="2102 Racecourse Rd, Fraser Rise", text=None):
    return {"source_row": row_no, "lot_number": lot, "lot_address": street,
            "source_text": text if text is not None else f"TITLED {lot} {street} $724,841"}


def _verdicts(stored, fresh):
    return {r["id"]: (v, n) for r, v, n in V.check_document(stored, fresh)}


def test_a_lot_on_the_line_we_name_is_confirmed():
    got = _verdicts([_stored(24, id=1)], [_fresh(24)])
    assert got[1] == (V.AT_STATED_ROW, 24)


def test_a_lot_the_sheet_moved_is_corrected_not_hidden():
    """Coleen clicked "row 24" and a different property opened, because Tomorrow Homes
    re-sorts the sheet as stock sells. The lot was still for sale on another line. A move
    is a POINTER problem; it must never touch availability."""
    got = _verdicts([_stored(24, id=1)], [_fresh(11)])
    assert got[1] == (V.MOVED, 11)


def test_a_lot_the_sheet_no_longer_carries_is_absent():
    got = _verdicts([_stored(24, id=1)], [_fresh(7, lot="9001", street="9001 Other Rd")])
    assert got[1] == (V.ABSENT, None)


def test_a_re_priced_lot_is_still_the_same_lot():
    """source_text is the whole flattened line, so it changes when the PRICE changes --
    the most common edit a price list ever gets. Matching on that alone reported live,
    correctly-listed stock as ABSENT the moment a builder moved a price."""
    stored = [_stored(24, id=1, text="TITLED 2102 2102 Racecourse Rd, Fraser Rise $724,841")]
    fresh = [_fresh(24, text="TITLED 2102 2102 Racecourse Rd, Fraser Rise $739,900")]
    assert _verdicts(stored, fresh)[1] == (V.AT_STATED_ROW, 24)


def test_a_lot_with_no_stated_line_gains_one():
    """Recording a line we can now see is a gain, not a correction."""
    got = _verdicts([_stored(None, id=1)], [_fresh(31)])
    assert got[1] == (V.MOVED, 31)


def test_two_lots_are_judged_independently():
    stored = [_stored(24, id=1),
              _stored(25, id=2, lot="2103", street="2103 Racecourse Rd, Fraser Rise")]
    fresh = [_fresh(24)]
    got = _verdicts(stored, fresh)
    assert got[1][0] == V.AT_STATED_ROW
    assert got[2][0] == V.ABSENT


# ------------------------------------------------------------------ the network wrapper

def test_a_document_that_will_not_download_makes_no_claim():
    """Silence from a file we could not open is not evidence about any lot in it. This is
    the case that has caused every bad mass-update in this repo."""
    rows = [_stored(24, id=1)]
    results, per_doc = V.verify(rows, fetch=lambda url: [], extract=lambda d, **k: [])
    assert [v for _r, v, _n in results] == [V.UNREADABLE]
    assert per_doc["https://x/y.xlsx"]["fresh"] == 0


def test_a_document_that_will_not_parse_makes_no_claim():
    def _boom(data, **kw):
        raise ValueError("not a zip file")
    rows = [_stored(24, id=1)]
    results, _ = V.verify(rows, fetch=lambda url: [("a", b"x")], extract=_boom)
    assert [v for _r, v, _n in results] == [V.UNREADABLE]


def test_a_sheet_that_comes_back_far_short_marks_nothing():
    """One Proxima project returned 3 of its 85 lots on 9 Sep. Without this guard that
    reads as 82 lots sold."""
    rows = [_stored(n, id=n, lot=str(n), street=f"{n} Some Rd") for n in range(1, 31)]
    fresh = [_fresh(1, lot="1", street="1 Some Rd")]
    results, per_doc = V.verify(rows, fetch=lambda url: [("a", b"x")],
                                extract=lambda d, **k: fresh)
    assert {v for _r, v, _n in results} == {V.UNREADABLE}
    assert per_doc["https://x/y.xlsx"]["partial"] is True


def test_a_sheet_that_came_back_whole_still_reports_what_it_dropped():
    """The guard must not switch the check off: a sheet that returned nearly all of
    itself and left one lot out is exactly what this script exists to catch."""
    rows = [_stored(n, id=n, lot=str(n), street=f"{n} Some Rd") for n in range(1, 31)]
    fresh = [_fresh(n, lot=str(n), street=f"{n} Some Rd") for n in range(1, 30)]
    results, _ = V.verify(rows, fetch=lambda url: [("a", b"x")], extract=lambda d, **k: fresh)
    verdicts = {r["id"]: v for r, v, _n in results}
    assert verdicts[30] == V.ABSENT
    assert verdicts[1] == V.AT_STATED_ROW


def test_a_short_sheet_is_not_judged_by_a_percentage():
    """A 3-row list losing one lot is 33% "missing" and is simply a lot that sold."""
    rows = [_stored(n, id=n, lot=str(n), street=f"{n} Some Rd") for n in range(1, 4)]
    fresh = [_fresh(1, lot="1", street="1 Some Rd"), _fresh(2, lot="2", street="2 Some Rd")]
    results, _ = V.verify(rows, fetch=lambda url: [("a", b"x")], extract=lambda d, **k: fresh)
    assert {r["id"]: v for r, v, _n in results}[3] == V.ABSENT


def test_the_proxima_index_is_not_a_sheet():
    """Proxima stores the projects index as every row's url. It is a portal, not a
    document with lines in it, and 3,928 rows share that one url."""
    rows = [_stored(24, id=1, source_url="https://portal.proxima.com.au/agent/projects/index/")]
    results, per_doc = V.verify(rows, fetch=lambda url: [("a", b"x")], extract=lambda d, **k: [])
    assert results == [] and per_doc == {}


def test_a_superseded_row_is_not_checked():
    rows = [_stored(24, id=1, superseded_by="gone")]
    results, per_doc = V.verify(rows, fetch=lambda url: [("a", b"x")], extract=lambda d, **k: [])
    assert results == [] and per_doc == {}


# --------------------------------------------------------- the status that hides a lot

def test_the_absent_status_is_one_the_published_filter_excludes():
    """A status the filter does not recognise would hide the lot from nothing. "not
    available" was missing from the API's copy of that set for weeks, and a sold Rouse
    Hill lot was recommended to Colin on 5 Aug."""
    assert not V._is_available(V.ABSENT_STATUS)


def test_the_statuses_the_client_complained_about_are_all_excluded():
    for status in ("Sold", "SOLD", "sold", "On Hold", "on hold", "Not Available",
                   "Under Offer", "Reserved", "Withdrawn"):
        assert not V._is_available(status), f"{status!r} would still show in the filter"


def test_a_lot_actually_for_sale_is_not_excluded():
    for status in ("Available", "AVAILABLE", "Titled", "Now Selling", "For Sale"):
        assert V._is_available(status), f"{status!r} was wrongly hidden"


def test_a_document_with_no_line_numbers_confirms_presence_without_inventing_one():
    """A dropbox folder of PDF brochures is a real source and its lots really are in it,
    but "row 12" is not a thing that exists there. The first run of this script called
    646 such rows MOVED and offered to write row 0 -- replacing a truthful absence with
    a meaningless number."""
    stored = [_stored(None, id=1)]
    fresh = [_fresh(None)]
    got = _verdicts(stored, fresh)
    assert got[1] == (V.PRESENT, None)


def test_a_line_less_document_never_erases_a_line_we_already_hold():
    """If the app names row 24 and today's read of that file yields no line numbers at
    all, the read cannot speak to the line. Confirm the lot, leave the number alone."""
    got = _verdicts([_stored(24, id=1)], [_fresh(None)])
    assert got[1] == (V.PRESENT, None)


def test_every_column_the_apply_writes_actually_exists():
    """A write to a column that is not there fails the transaction and silently rolls back
    every row number corrected in the same run. That is how a suburb repair lost a whole
    night's work once already -- it wrote to suburb_source, which build_web computes and
    the table does not hold.

    Reads the UPDATE statements out of the source rather than running them, so the check
    costs nothing and cannot itself write.
    """
    import inspect
    import re as _re
    import sqlite3 as _sq
    import textwrap as _textwrap
    from pathlib import Path

    import database

    import tempfile
    # A throwaway database rather than the real one: the schema is what is being asked
    # about, and PRAGMA on the live file would need it not to be mid-harvest. Left in the
    # OS temp directory rather than unlinked -- on Windows the sqlite handle outlives the
    # connection object and deleting it raises WinError 32, failing a test that has
    # already passed.
    tmp = Path(tempfile.mkdtemp()) / "schema_probe.db"
    database.ResearchDatabase(db_path=tmp)
    con = _sq.connect(str(tmp))
    columns = {r[1] for r in con.execute("PRAGMA table_info(buildings)")}
    con.close()

    # Adjacent string literals are joined first ("...SET a=?, b=? " "WHERE id=?"), and
    # the column chosen at runtime by an f-string is picked up from the literals it
    # selects between. Without both, this check quietly finds nothing and passes.
    body = inspect.getsource(V.main)
    joined = _re.sub(r'"\s+"', "", body)
    written = set()
    for stmt in _re.findall(r"UPDATE buildings SET (.*?)WHERE", joined, _re.S):
        written.update(_re.findall(r"(\w+)\s*=\s*\?", stmt))
    # The column chosen at runtime -- column = ("source_page" if ... else "source_row").
    # Parsed rather than regexed: a regex stopped at the paren inside the condition and
    # silently checked only half of the choice.
    import ast as _ast
    tree = _ast.parse(_textwrap.dedent(body))
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Assign):
            continue
        if not any(isinstance(t, _ast.Name) and t.id == "column" for t in node.targets):
            continue
        written.update(n.value for n in _ast.walk(node.value)
                       if isinstance(n, _ast.Constant) and isinstance(n.value, str))
    assert len(written) >= 3, (
        f"only found {sorted(written)} - has main() been restructured? This check is "
        "worthless if it silently matches nothing.")
    missing = sorted(written - columns)
    assert not missing, f"verify_sheet_positions writes columns that do not exist: {missing}"


def test_marking_a_lot_unavailable_is_undone_by_the_next_read_that_finds_it():
    """The property the whole ABSENT action rests on.

    Hiding a lot its sheet dropped is only safe because it is reversible in a day: if the
    lot is in tomorrow's read of that sheet, the harvest writes its availability back and
    it returns to the filter by itself. If record_building ever stopped overwriting
    availability_status, this script would start hiding live stock permanently -- and
    nothing else in the suite would notice.
    """
    import sqlite3 as _sq
    import tempfile
    from pathlib import Path

    from database import ResearchDatabase

    row = {"source_channel": "E-Agent", "attribution_scope": "builder",
           "builder_name": "Tomorrow Homes", "suburb": "Fraser Rise",
           "lot_number": "2102", "lot_address": "2102 Racecourse Rd, Fraser Rise",
           "advertised_package_price": 724841, "availability_status": "Available"}
    tmp = Path(tempfile.mkdtemp()) / "reversible.db"
    db = ResearchDatabase(db_path=tmp)
    con = _sq.connect(str(tmp))
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_buildings_content_hash "
                "ON buildings(content_hash)")
    con.commit()
    con.close()

    db.record_building(dict(row))
    rid = db.get_buildings()[0]["id"]

    # what verify_sheet_positions --apply does to a lot its sheet no longer carries
    con = _sq.connect(str(tmp))
    con.execute("UPDATE buildings SET availability_status=?, status_previous=? WHERE id=?",
                (V.ABSENT_STATUS, "Available", rid))
    con.commit()
    con.close()
    assert not V._is_available(db.get_buildings()[0]["availability_status"])

    db.record_building(dict(row))          # the sheet carries it again tomorrow
    assert db.get_buildings()[0]["availability_status"] == "Available"
    assert V._is_available(db.get_buildings()[0]["availability_status"])


# ------------------------------------------------- one link is not one file

def test_a_folder_of_price_lists_confirms_presence_but_names_no_line():
    """96 of 142 stock links are Dropbox folders holding a dozen price lists each, and
    every one of them has a row 12. The stored row does not record which file inside the
    folder it came from, so no line can be confirmed or corrected.

    The first version of this script fetched one URL and got one file, so rows living in
    the folder's OTHER files matched nothing and were called absent. That is live stock,
    hidden.
    """
    stored = [_stored(24, id=1), _stored(12, id=2, lot="9", street="9 Other Rd")]
    fresh = [dict(_fresh(24), source_piece="a.xlsx"),
             dict(_fresh(12, lot="9", street="9 Other Rd"), source_piece="b.xlsx")]
    got = {r["id"]: (v, n) for r, v, n in V.check_document(stored, fresh, trust_lines=False)}
    assert got[1] == (V.PRESENT, None)
    assert got[2] == (V.PRESENT, None)


def test_a_lot_missing_from_every_file_in_the_folder_is_still_absent():
    """Presence-only must not become blindness: a lot in none of the pieces is gone."""
    stored = [_stored(24, id=1, lot="777", street="777 Nowhere Rd")]
    fresh = [dict(_fresh(24), source_piece="a.xlsx")]
    got = {r["id"]: v for r, v, _n in V.check_document(stored, fresh, trust_lines=False)}
    assert got[1] == V.ABSENT


def test_a_single_file_link_still_checks_the_line():
    """The row-number check is the point of the script; it must survive this."""
    stored = [_stored(24, id=1)]
    fresh = [dict(_fresh(11), source_piece="only.xlsx")]
    got = {r["id"]: (v, n) for r, v, n in V.check_document(stored, fresh, trust_lines=True)}
    assert got[1] == (V.MOVED, 11)


def test_verify_stops_trusting_lines_when_a_link_yields_several_files():
    """The decision is made by verify() from what came back, not configured per host."""
    rows = [_stored(24, id=1)]
    fresh = [dict(_fresh(11), source_piece="a.xlsx"), dict(_fresh(3, lot="9",
             street="9 Other Rd"), source_piece="b.xlsx")]
    results, per_doc = V.verify(rows, fetch=lambda u: [("a", b"x"), ("b", b"y")],
                                extract=lambda p, **k: fresh)
    assert [v for _r, v, _n in results] == [V.PRESENT]
    assert per_doc["https://x/y.xlsx"]["pieces"] == 2


def test_the_real_fetcher_is_the_one_the_harvest_uses():
    """Both readers, because neither is right alone and each fails silently.

    remote_stocklist picks its handler by host, so every dropbox link goes to the FOLDER
    handler -- and most stored urls are the link to one file inside a folder, which lists
    as empty. A probe of the three largest documents called all 677 of their listings
    unreadable on that alone. A plain GET reads them.

    The reverse is worse: a plain GET on a folder ROOT returns one file, so rows in the
    folder's other price lists match nothing and are called absent -- live stock, hidden.

    Pinned by inspecting the imports so a refactor that drops either cannot pass silently.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(V._fetchers).strip())
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                 for a in n.names}
    assert "sources.remote_stocklist" in imported, \
        "the verifier no longer fetches the way the harvest does"
    assert "document_suburb" in imported,         "the direct-GET fallback is gone; per-file dropbox links will read as unreadable"


# --------------------------------------------------- a PDF is numbered by its page

def _stored_pdf(page, lot="2102", street="2102 Racecourse Rd, Fraser Rise", **kw):
    row = _stored(None, lot=lot, street=street, **kw)
    row["source_page"] = page
    row["source_row"] = None
    return row


def _fresh_pdf(page, lot="2102", street="2102 Racecourse Rd, Fraser Rise"):
    f = _fresh(None, lot=lot, street=street)
    f["source_page"] = page
    f["source_row"] = None
    return f


def test_a_pdf_listing_is_checked_by_its_page():
    """4,866 live rows claim a page against 1,437 claiming a line. Checking only lines
    left the larger half of the promise unkept: a stale page pointer sends Colin to the
    wrong page of a 40-page price list exactly as a stale row sent Coleen to the wrong
    property."""
    assert _verdicts([_stored_pdf(7, id=1)], [_fresh_pdf(7)])[1] == (V.AT_STATED_ROW, 7)


def test_a_listing_that_moved_to_another_page_is_corrected():
    assert _verdicts([_stored_pdf(7, id=1)], [_fresh_pdf(3)])[1] == (V.MOVED, 3)


def test_a_listing_gone_from_the_pdf_is_absent():
    got = _verdicts([_stored_pdf(7, id=1)],
                    [_fresh_pdf(3, lot="9001", street="9001 Other Rd")])
    assert got[1] == (V.ABSENT, None)


def test_a_line_numbered_row_is_never_judged_by_a_page():
    """A row holds one kind of position or the other, never both. Reading the wrong one
    would report every spreadsheet row as moved to page 0."""
    stored = [_stored(24, id=1)]
    fresh = [dict(_fresh(24), source_page=9)]
    assert _verdicts(stored, fresh)[1] == (V.AT_STATED_ROW, 24)


def test_the_corrected_position_goes_back_into_the_column_it_came_from():
    """Writing a page number into source_row would make provenance say "row 7" about
    page 7. Pinned by reading the apply, which chooses the column per row."""
    import inspect
    body = inspect.getsource(V.main)
    assert "source_page" in body and "source_row" in body
    assert 'column = ("source_page"' in body, \
        "the apply no longer chooses which position column to write"


def test_a_row_that_claims_no_position_gains_only_a_line_it_can_store():
    """A row stating neither a line nor a page may gain a LINE (source_row is where the
    apply would put it) but must not be handed a PAGE, which would be written into the
    wrong column and make provenance say "row 7" about page 7."""
    from_page_only = [dict(_fresh(None), source_page=7)]
    stateless = _stored(None, id=1)
    stateless["source_page"] = None
    assert _verdicts([stateless], from_page_only)[1] == (V.PRESENT, None)
    # but a line it can store is taken
    assert _verdicts([stateless], [_fresh(31)])[1] == (V.MOVED, 31)


# ------------------------- an absence is only real if the file actually lost rows

def test_a_file_that_came_back_whole_has_lost_nothing():
    """The finding that stopped 636 live properties being hidden.

    One document: we hold 118 rows from it, it returns 118, and 110 of ours match none of
    them. Nothing was removed from that file. Those 110 are lots we failed to RECOGNISE --
    apartment rows carrying a unit and a level where _lot_key expects a lot and a street.
    The partial-read guard cannot see this: it counts rows RETURNED, which was healthy.
    """
    stored = [_stored(n, id=n, lot=str(n), street=f"{n} Some Rd") for n in range(1, 21)]
    fresh = [_fresh(n, lot=f"U{n}", street=f"Level {n}") for n in range(1, 21)]
    results, per_doc = V.verify(stored, fetch=lambda u: [("a", b"x")],
                                extract=lambda p, **k: fresh)
    assert {v for _r, v, _n in results} == {V.UNMATCHED}
    assert per_doc["https://x/y.xlsx"]["unexplained_absence"] == 20


def test_an_absence_the_file_actually_lost_is_believed():
    """20 stored, 15 come back, 5 unmatched -- the file shrank by exactly 5. Those sold."""
    stored = [_stored(n, id=n, lot=str(n), street=f"{n} Some Rd") for n in range(1, 21)]
    fresh = [_fresh(n, lot=str(n), street=f"{n} Some Rd") for n in range(1, 16)]
    results, _ = V.verify(stored, fetch=lambda u: [("a", b"x")],
                          extract=lambda p, **k: fresh)
    verdicts = collections.Counter(v for _r, v, _n in results)
    assert verdicts[V.ABSENT] == 5
    assert verdicts[V.UNMATCHED] == 0


def test_a_little_churn_is_allowed_for():
    """Stock arrives and leaves between two reads, so an absence one or two over what the
    file lost is still believed. Only a gulf is treated as a matching failure."""
    stored = [_stored(n, id=n, lot=str(n), street=f"{n} Some Rd") for n in range(1, 21)]
    # 18 come back, but two of them are NEW lots, so only 16 of ours match: 4 absent
    # against a file that shrank by 2.
    fresh = ([_fresh(n, lot=str(n), street=f"{n} Some Rd") for n in range(1, 17)]
             + [_fresh(90, lot="90", street="90 New Rd"),
                _fresh(91, lot="91", street="91 New Rd")])
    results, _ = V.verify(stored, fetch=lambda u: [("a", b"x")],
                          extract=lambda p, **k: fresh)
    assert collections.Counter(v for _r, v, _n in results)[V.ABSENT] == 4


def test_when_the_matching_fails_no_absence_from_that_document_is_believed():
    """Not just the excess. Which rows are gone and which are unrecognised cannot be told
    apart from here, and picking some to hide would be guessing with a client's stock."""
    stored = [_stored(n, id=n, lot=str(n), street=f"{n} Some Rd") for n in range(1, 21)]
    fresh = [_fresh(1, lot="1", street="1 Some Rd")] + \
            [_fresh(n, lot=f"U{n}", street=f"Level {n}") for n in range(2, 20)]
    results, _ = V.verify(stored, fetch=lambda u: [("a", b"x")],
                          extract=lambda p, **k: fresh)
    verdicts = collections.Counter(v for _r, v, _n in results)
    assert verdicts[V.ABSENT] == 0, "a document with unreliable matching still hid stock"
    assert verdicts[V.UNMATCHED] == 19


def test_a_match_is_still_believed_when_absences_are_not():
    """A row recognised by its text or lot key really is where the read found it. A NON
    match is what needs corroboration, so corrections survive."""
    stored = ([_stored(99, id=1)]
              + [_stored(n, id=n, lot=str(n), street=f"{n} Some Rd") for n in range(2, 21)])
    fresh = ([_fresh(11)]
             + [_fresh(n, lot=f"U{n}", street=f"Level {n}") for n in range(2, 21)])
    results, _ = V.verify(stored, fetch=lambda u: [("a", b"x")],
                          extract=lambda p, **k: fresh)
    got = {r["id"]: (v, n) for r, v, n in results}
    assert got[1] == (V.MOVED, 11)
    assert got[2][0] == V.UNMATCHED


# ------------------- the two sides must build the identity the same way

def test_a_lot_number_the_address_already_states_is_not_added_twice():
    """The single largest source of false absences, found by auditing the audit.

    A STORED row got its lot_number after extraction, from stocklist_reparse's per-cohort
    rules. A FRESH listing gets it only from parse_lot_number, which needs a literal
    "Lot 12" or a stock code. So the stored key read "208|west 208 thornhill gardens" and
    the fresh key read "west 208 thornhill gardens" -- never equal, on 1,637 of 6,452 live
    rows. Those rows then hung on an exact match of the whole flattened line INCLUDING THE
    PRICE, so one re-price hid a live property.
    """
    stored = {"lot_number": "208", "lot_address": "West 208 Thornhill Gardens"}
    fresh = {"lot_number": None, "lot_address": "West 208 Thornhill Gardens"}
    assert V._lot_key(stored) == V._lot_key(fresh)


def test_a_lot_number_the_address_does_not_state_is_still_used():
    """Where the number is real extra information, it stays in the key: two units at the
    same street address are not the same lot."""
    a = V._lot_key({"lot_number": "210", "lot_address": "26 Tulloh St"})
    b = V._lot_key({"lot_number": "211", "lot_address": "26 Tulloh St"})
    assert a != b and a and b


def test_the_key_survives_a_re_price():
    """The whole point. A price list gets re-priced constantly; the address label does
    not move, so the lot is still recognised."""
    stored = _stored(24, id=1, text="TITLED 208 West 208 Thornhill Gardens $724,841")
    fresh = _fresh(24, text="TITLED 208 West 208 Thornhill Gardens $801,000")
    stored["lot_number"], stored["lot_address"] = "208", "West 208 Thornhill Gardens"
    fresh["lot_number"], fresh["lot_address"] = None, "West 208 Thornhill Gardens"
    assert _verdicts([stored], [fresh])[1] == (V.AT_STATED_ROW, 24)


def test_two_different_streets_never_share_a_key():
    assert V._lot_key({"lot_number": None, "lot_address": "12 Alpha Rd"}) != \
challenge_b if False else True
    assert V._lot_key({"lot_number": None, "lot_address": "12 Alpha Rd"}) != \
        V._lot_key({"lot_number": None, "lot_address": "12 Beta Rd"})


# --------------------------------- not recognised is not gone (the per-row rescue)

def test_the_ocr_cohort_is_protected_by_the_document_layer_not_the_row_one():
    """Some price lists are scanned pictures. The nightly OCRs them on GitHub's ubuntu
    runner with unpinned rapidocr/PyMuPDF; this check re-read them on Windows. Two OCR
    stacks read the same picture into different words -- "LOT408" against "LoT406" -- so
    no key matches, and 288 of the first audit's 681 absences were that cohort.

    The ROW rescue deliberately cannot save these: the lot number itself is garbled, and a
    rescue that ignored the lot number would let every sold lot on a street be rescued by
    its neighbours. What saves them is the DOCUMENT layer -- the file came back the same
    size, so it lost nothing, so no absence from it is believed.
    """
    stored = _stored(24, id=1, lot="408",
                     text="LOT408 Aspen 22 Sandhurst Estate 4 2 2 512 sqm $712,000")
    fresh = [_fresh(9, lot="406", street="LoT406 Aspen 22 Sandhurst",
                    text="LoT406 Aspen 22 Sandhurst Estate 4 2 2 512 sqm $718,000")]
    # the row layer alone says gone...
    assert _verdicts([stored], fresh)[1] == (V.ABSENT, None)
    # ...and the document layer overrules it, because the file did not shrink
    results, _ = V.verify([stored], fetch=lambda u: [("a", b"x")],
                          extract=lambda p, **k: fresh)
    assert [v for _r, v, _n in results] == [V.UNMATCHED]


def test_a_re_worded_row_inside_a_shrinking_document_is_still_rescued():
    """The row layer's own case: a document that genuinely lost rows, containing one row
    whose wording changed. Without the rescue that row is collateral damage."""
    stored = ([_stored(1, id=1, lot="408", text="LOT 408 Aspen 22 Sandhurst Estate 512")]
              + [_stored(n, id=n, lot=str(n), street=f"{n} Some Rd") for n in range(2, 21)])
    # 15 of the 20 come back; lot 408 among them but re-worded, so no key matches
    fresh = ([_fresh(1, lot="408", street="Lot 408 Aspen",
                     text="LOT 408 Aspen 22 Sandhurst Estate 512 sqm NOW $718,000")]
             + [_fresh(n, lot=str(n), street=f"{n} Some Rd") for n in range(2, 15)])
    results, _ = V.verify(stored, fetch=lambda u: [("a", b"x")],
                          extract=lambda p, **k: fresh)
    got = {r["id"]: v for r, v, _n in results}
    assert got[1] in (V.UNMATCHED, V.AT_STATED_ROW, V.MOVED, V.PRESENT),         "a re-worded row still on the page was called gone"
    assert got[1] != V.ABSENT


def test_a_genuinely_different_lot_is_not_rescued():
    """The rescue must not swallow the signal it exists to protect: a lot that really is
    off the list shares almost none of its wording with what remains."""
    stored = _stored(24, id=1, lot="408",
                     text="LOT408 Aspen 22 Sandhurst Estate 4 2 2 512 sqm $712,000")
    fresh = [_fresh(9, lot="9001", street="9001 Distant Rd",
                    text="LOT9001 Bellevue 31 Faraway Park 5 3 2 640 sqm $980,000")]
    assert _verdicts([stored], fresh)[1] == (V.ABSENT, None)


def test_a_re_priced_and_re_statused_row_is_recognised():
    """Price and status are exactly the words that move between reads, so they are not
    counted toward recognising a row."""
    a = V._tokens("TITLED Westwood Walk 2102 Racecourse Rd 10.5 294 SAVANA $369,200 Available")
    b = V._tokens("SOLD Westwood Walk 2102 Racecourse Rd 10.5 294 SAVANA $401,000")
    assert len(a & b) / len(a) >= V.RESCUE_OVERLAP


def test_a_row_with_almost_no_words_is_not_rescued_by_accident():
    """Three identifying tokens is the floor. Below it, a 70% overlap is two words in
    common, which is not recognition."""
    assert not V._rescued({"source_text": "12 4", "lot_address": "12 4"},
                          [{"a", "b", "c", "12"}])


def test_a_document_that_did_not_shrink_gets_no_free_allowance():
    """The slack exists for churn. A file that came back the same size or larger has lost
    nothing, and a free allowance of three let 29 rows through in 16 such documents."""
    stored = [_stored(n, id=n, lot=str(n), street=f"{n} Some Rd") for n in range(1, 21)]
    # all 20 come back, but three are unrecognisable AND share no wording
    fresh = ([_fresh(n, lot=str(n), street=f"{n} Some Rd") for n in range(4, 21)]
             + [_fresh(n, lot=f"Z{n}", street=f"Zed {n} Far Rd",
                       text=f"totally different wording {n} zebra quantum") for n in (1, 2, 3)])
    results, _ = V.verify(stored, fetch=lambda u: [("a", b"x")],
                          extract=lambda p, **k: fresh)
    verdicts = collections.Counter(v for _r, v, _n in results)
    assert verdicts[V.ABSENT] == 0, "a document that lost nothing still hid stock"


# ------------------------------ a file nobody edited cannot have lost a lot

def test_a_byte_identical_file_proves_nothing_was_removed():
    """The strongest statement available, and the cheapest. Row counts only approximate
    "did this list lose anything"; a hash settles it. Without this we cannot tell "the
    builder replaced the file" from "the builder sold these lots" -- the synthesis called
    that out as indistinguishable in principle."""
    # 13 of 20 come back -- above the partial-read floor, so the document IS judged, and
    # the shortfall of 7 exactly accounts for 7 absences.
    stored = [_stored(n, id=n, lot=str(n), street=f"{n} Some Rd") for n in range(1, 21)]
    fresh = [_fresh(n, lot=str(n), street=f"{n} Some Rd") for n in range(1, 14)]
    pieces = [("a", b"the same bytes as yesterday")]
    digest = V._digest(pieces)
    # without the hash, 7 absences look real (the read came back 7 rows shorter)
    results, _ = V.verify(stored, fetch=lambda u: pieces, extract=lambda p, **k: fresh)
    assert collections.Counter(v for _r, v, _n in results)[V.ABSENT] == 7
    # with it, the file is known not to have changed, so none of them is believed
    results, per_doc = V.verify(stored, fetch=lambda u: pieces,
                                extract=lambda p, **k: fresh,
                                previous={"https://x/y.xlsx": digest})
    assert collections.Counter(v for _r, v, _n in results)[V.ABSENT] == 0
    assert per_doc["https://x/y.xlsx"]["unchanged_since_last_run"] is True


def test_a_file_that_did_change_is_judged_normally():
    stored = [_stored(n, id=n, lot=str(n), street=f"{n} Some Rd") for n in range(1, 21)]
    fresh = [_fresh(n, lot=str(n), street=f"{n} Some Rd") for n in range(1, 14)]
    results, per_doc = V.verify(stored, fetch=lambda u: [("a", b"new bytes today")],
                                extract=lambda p, **k: fresh,
                                previous={"https://x/y.xlsx": "an-older-digest"})
    assert collections.Counter(v for _r, v, _n in results)[V.ABSENT] == 7
    assert per_doc["https://x/y.xlsx"]["unchanged_since_last_run"] is False


def test_a_reshuffled_folder_is_not_a_changed_price_list():
    """A Dropbox folder does not promise to list its files in the same order twice."""
    assert V._digest([("a", b"one"), ("b", b"two")]) == \
        V._digest([("b", b"two"), ("a", b"one")])
    assert V._digest([("a", b"one")]) != V._digest([("a", b"ONE")])
    assert V._digest([]) == ""


def test_a_portal_page_is_reported_apart_from_an_unreadable_file():
    """remote_stocklist answers KIND_HTML with NO bytes on purpose: it is telling the
    caller to open this one in a browser, which sources/e_agent.py does with
    adaptive_extract. 223 Hermitage package pages and 30 Torsion brochure endpoints are
    that shape.

    Those rows ARE verified -- by the harvest, which drives Playwright -- just not here.
    Counting them as "unreadable" understates coverage by 254 listings and reads as a
    failure of the check rather than a boundary of it. No claim either way, but the report
    has to say which it is.
    """
    rows = [_stored(24, id=1)]
    results, per_doc = V.verify(rows, fetch=lambda u: V._A_PAGE_NOT_A_FILE,
                                extract=lambda p, **k: [])
    assert [v for _r, v, _n in results] == [V.UNREADABLE]
    assert per_doc["https://x/y.xlsx"]["a_page_not_a_file"] is True


def test_a_file_that_simply_would_not_download_is_not_called_a_page():
    rows = [_stored(24, id=1)]
    _results, per_doc = V.verify(rows, fetch=lambda u: [], extract=lambda p, **k: [])
    assert not per_doc["https://x/y.xlsx"].get("a_page_not_a_file")


def test_applying_a_saved_report_writes_only_what_the_report_says():
    """A full pass re-downloads 484 documents and takes an hour, and the verdicts it
    produces are not guaranteed identical to the ones a person just read -- a builder can
    edit a sheet in between. When the decision is "hide 95 properties", the thing applied
    has to be the thing that was checked.
    """
    import json
    import sqlite3 as _sq
    import tempfile
    from pathlib import Path

    from database import ResearchDatabase

    base = {"source_channel": "E-Agent", "attribution_scope": "builder",
            "builder_name": "Tomorrow Homes", "suburb": "Fraser Rise",
            "lot_address": "2102 Racecourse Rd", "source_url": "https://x/y.xlsx",
            "source_row": 24, "advertised_package_price": 724841,
            "availability_status": "Available"}
    tmp = Path(tempfile.mkdtemp())
    db = ResearchDatabase(db_path=tmp / "apply.db")
    con = _sq.connect(str(tmp / "apply.db"))
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_buildings_content_hash "
                "ON buildings(content_hash)")
    con.commit(); con.close()
    db.record_building(dict(base, lot_number="2102"))
    db.record_building(dict(base, lot_number="2103", lot_address="2103 Racecourse Rd"))
    stored = {b["lot_number"]: b["id"] for b in db.get_buildings()}

    report = tmp / "report.json"
    report.write_text(json.dumps({"generated": "2026-09-09T18:00:00", "rows": [
        {"id": stored["2102"], "verdict": "absent", "new_row": None},
        {"id": stored["2103"], "verdict": "moved", "new_row": 11},
        {"id": 999999, "verdict": "absent", "new_row": None},      # no longer live
    ]}), encoding="utf-8")

    con = _sq.connect(str(tmp / "apply.db"))
    con.row_factory = _sq.Row
    rows = con.execute("SELECT * FROM buildings").fetchall()
    V._apply_report(con, rows, str(report))
    after = {b["lot_number"]: b for b in db.get_buildings()}
    con.close()

    assert after["2102"]["availability_status"] == V.ABSENT_STATUS, "the absent lot was not hidden"
    assert after["2102"]["status_previous"] == "Available"
    assert after["2102"]["source_row"] == 24, "an absent lot's position was altered"
    assert after["2103"]["source_row"] == 11, "the moved lot was not corrected"
    assert after["2103"]["availability_status"] == "Available", "a moved lot was hidden"


def test_applying_a_report_skips_rows_that_are_no_longer_live():
    """A report is read minutes or hours after it was written. A row superseded in between
    must not be resurrected by an id lookup that quietly misses."""
    import inspect
    src = inspect.getsource(V._apply_report)
    assert 'v["id"] in by_id' in src, "the apply no longer guards against a stale id"
    assert "no longer live and were skipped" in src, "a skipped row is not reported"
