"""The review of the retirement ledger (24 Oct 2026): three ways a failed read was still
taken as "everything sold", each through the real nightly chain (the harness of
tests/test_retirement_multinight.py: record_building, supersede_duplicates, repair_rows
--apply, one call per night).

  1. A RECORDED VETO WAS OVERWRITTEN THE NEXT NIGHT. A manifest written before 06:00 put
     yesterday's date among tonight's reads, so yesterday's read was judged again against
     tonight's manifest; a project unreadable tonight has no line in it, and the bare
     anchor then rewrote "partial / veto: proxima short 15/20" as whole. Tonight's reads
     are now the run's own (from when it began), a recorded verdict is re-judged only on a
     night whose manifest has a line for its list, and a recorded veto is never replaced.
  2. A RE-UPLOAD KEPT ITS OLD LINK. record_building moved source_url and never
     stocklist_file, and rule B and the manifest's vetoes are keyed on the link: a lot
     carried onto a re-upload whose new link then failed retired as "no longer offered",
     and a refused lot on the new link never vetoed its document. stocklist_file now
     travels with the read, and rows stored before that resolve through their document.
  3. AN UNREADABLE PROXIMA PROJECT LOOKED LIKE ONE THAT LEFT THE DROPDOWN. sources/proxima.py
     skips it before its manifest line, so rule B counted its absence and retired its lots
     after 14 nights. harvest_buildings now writes the reader's own counts into the
     manifest, and Proxima's rule B is off on a night any listed project failed to read.

The verifier's own tests are here as they were written (A1-A3), with the twins that show
each one can fail.
"""
import contextlib
import io
import json
import sqlite3
from datetime import timedelta

import pytest

from tests.test_retirement_multinight import (Nights, _lot, _proxima, _project_entry,
                                              _page, _eagent_night, _ledger_reads,
                                              _night_dt, _link, _px_run, PORTAL)


def _others(night, n=20):
    """20 other Proxima projects, read whole every night, so one project missing from a
    night never makes the index look narrowed."""
    lots, entries = [], []
    for p in range(200, 200 + n):
        lots += [_lot(p * 10 + i, night, channel="Proxima", builder=f"Developer {p}",
                      doc="https://proxima.example/projects", source_project_id=str(p))
                 for i in (1, 2)]
        entries.append(_project_entry(str(p), 2, stated=2, whole="uncapped"))
    return lots, entries


# ------------------------------------------------------------------ 1. a recorded veto stands

def _short_every_other_night(n, proven):
    """Project 53: 20 lots and 12 'Not Available' captures on night 1, 20 on night 2, then
    15 on every even night (the other nights it is unreadable: no rows, no manifest line).
    `proven`: the manifest proves each read whole (15 of 15) instead of short (15 of 20)."""
    for night in range(1, 30):
        lots, entries = _others(night)
        if night == 1:
            lots += [_proxima(i, 1, pid="53", status="Available") for i in range(1, 21)]
            lots += [_proxima(i, 1, pid="53", status="Not Available")
                     for i in range(100, 112)]
            entries.append(_project_entry("53", 32, stated=20, whole="uncapped"))
        elif night == 2:
            lots += [_proxima(i, 2, pid="53", status="Available") for i in range(1, 21)]
            entries.append(_project_entry("53", 20, stated=20, whole="uncapped"))
        elif night % 2 == 0:
            lots += [_proxima(i, night, pid="53", status="Available") for i in range(1, 16)]
            entries.append(_project_entry("53", 15, stated=15, whole="uncapped") if proven
                           else _project_entry("53", 15, stated=20, short=True))
        n.run(night, lots, entries=entries)
        yield night


def test_a_short_read_stays_vetoed_on_a_night_its_project_is_not_read(tmp_path, monkeypatch):
    """A1. Every read of project 53 after night 2 is SHORT by the manifest's own word, and
    the nights between it is unreadable. Lots 16-20 are never on a read the manifest calls
    whole: they must never retire. (Main retires nothing here -- its 12 'Not Available'
    captures hold it over 40% -- and the branch before this review retired them on night 17:
    the recorded veto was rewritten 'whole / anchor 5/20' the next night.)"""
    n = Nights(tmp_path, monkeypatch)
    for night in _short_every_other_night(n, proven=False):
        gone = {str(i) for i in range(16, 21)} & n.retired()
        assert not gone, f"night {night}: lots every read of which was short retired: " \
                         f"{sorted(gone)}"
    reads = [r for r in _ledger_reads(n.path) if r["label"].endswith("project 53")]
    assert reads and all(r["basis"].startswith("veto: ") for r in reads
                         if r["rows_seen"] == 15), \
        "a recorded veto was rewritten: " + repr([(r["read_date"], r["basis"])
                                                  for r in reads])


def test_twin_the_same_lots_retire_when_each_read_is_proven_whole(tmp_path, monkeypatch):
    """The twin: the same nights, each read proven whole by its own counter (15 of 15).
    Lots 16-20 left; they retire, and the recorded verdicts are what lets them."""
    n = Nights(tmp_path, monkeypatch)
    for _night in _short_every_other_night(n, proven=True):
        pass
    assert {str(i) for i in range(16, 21)} <= n.retired()
    assert not {str(i) for i in range(1, 16)} & n.retired()


def test_the_capped_read_stays_vetoed_the_night_after(tmp_path, monkeypatch):
    """A1. The render-cap case (12 lots, 10 read, short) with ONE night, 17, on which the
    project is unreadable. (Main retires lots 11-12 on night 16 already -- its render-cap
    hole; the branch before this review vetoed that and then lost the veto on night 17.)"""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 21):
        lots, entries = _others(night)
        if night != 17:
            carried = range(1, 13) if night <= 2 else range(1, 11)
            lots += [_proxima(i, night, status="Available") for i in carried]
            entries.append(_project_entry("587", len(carried), stated=12,
                                          whole="uncapped" if night <= 2 else "",
                                          short=night > 2))
        n.run(night, lots, entries=entries)
        assert not {"11", "12"} & n.retired(), f"night {night}: lots past the cap retired"


def _midnight_nights(n, given_start, whole):
    """Each night's harvest begins at 23:40 the evening before and stamps project 587's
    rows at 23:50 -- so every read is dated the day before its manifest, written at 00:30.
    `given_start`: the manifest says when the run began (harvest_manifest.reset); else the
    ledger works it out from the rows' checked_at. `whole`: the manifest proves each read
    whole (12 of 12 on the board, so 11-12 really left) instead of short (10 of 12)."""
    for night in range(1, 21):
        begin = _night_dt(night).replace(hour=0, minute=0) - timedelta(minutes=20)
        stamp = begin + timedelta(minutes=10)
        carried = range(1, 13) if night <= 2 else range(1, 11)
        lots, entries = _others(night)
        lots += [_proxima(i, night, status="Available", date_checked=stamp.strftime(
            "%d/%m/%Y"), checked_at=stamp.isoformat(timespec="seconds")) for i in carried]
        if whole or night <= 2:
            entries.append(_project_entry("587", len(carried), stated=len(carried),
                                          whole="uncapped"))
        else:
            entries.append(_project_entry("587", len(carried), stated=12, short=True))
        n.run(night, lots, entries=entries, started=begin if given_start else None,
              generated=_night_dt(night).replace(hour=0, minute=30))
        yield night


@pytest.mark.parametrize("given_start", [True, False])
def test_a_run_that_crosses_midnight_judges_its_reads_by_its_own_manifest(
        tmp_path, monkeypatch, given_start):
    """The start of the run decides which dates are tonight's, not the hour the manifest
    was written. A read stamped before midnight by tonight's run is tonight's, and its
    manifest line (short) vetoes it: lots 11-12, past the cap, never retire. Main retires
    them on night 16."""
    n = Nights(tmp_path, monkeypatch)
    for night in _midnight_nights(n, given_start, whole=False):
        assert not {"11", "12"} & n.retired(), f"night {night}: lots past the cap retired"


def test_twin_a_midnight_run_judged_without_its_manifest_retires_them(tmp_path,
                                                                      monkeypatch):
    """The twin, harness-can-fail: told the run began at the manifest's own midnight, the
    ledger takes each pre-midnight read for yesterday's and judges it with no manifest at
    all -- the anchor calls 10 of 12 whole and lots 11-12 retire. That is what the test
    above is guarding against."""
    import list_ledger
    monkeypatch.setattr(list_ledger, "run_started", lambda m, rows, made: made.replace(
        hour=0, minute=0, second=0, microsecond=0))
    n = Nights(tmp_path, monkeypatch)
    for _night in _midnight_nights(n, True, whole=False):
        pass
    assert {"11", "12"} <= n.retired()


def test_twin_a_midnight_run_proven_whole_retires_what_left(tmp_path, monkeypatch):
    """The positive twin: the same midnight-crossing runs, each read proven whole (10 of
    10): lots 11-12 really left, and they retire."""
    n = Nights(tmp_path, monkeypatch)
    for _night in _midnight_nights(n, True, whole=True):
        pass
    assert n.retired() & {str(i) for i in range(1, 13)} == {"11", "12"}


# ------------------------------------------------------------------ 2. a re-upload's link

def _reupload(links, night, fail_from, take_down=False):
    old = (links[0][0], links[0][1], [1, 2, 3], links[0][3])
    new = ("https://files.example/link-0-reuploaded", links[0][1], [1, 2, 3], links[0][3])
    if night <= 5:
        return [old] + links[1:]
    if night < fail_from:
        return [new] + links[1:]
    return links[1:] if take_down else [(new[0], new[1], [], new[3])] + links[1:]


def test_a_reuploaded_list_whose_new_link_then_fails_retires_nothing(tmp_path, monkeypatch):
    """A2. E-Agent re-uploads link 0 under a new link in the same place; its three lots are
    read there (same identity). From night 21 the NEW link is still on the page but its
    fetch fails -- a dead link still offered, which never counts. Main keeps them; the
    branch before this review retired them on night 34 as 'link offered no more'."""
    n = Nights(tmp_path, monkeypatch)
    links = [_page(k) for k in range(12)]
    for night in range(1, 46):
        _eagent_night(n, night, _reupload(links, night, fail_from=21))
        assert not {"1", "2", "3"} & n.retired(), \
            f"night {night}: the lots of a link still on the page retired " \
            f"({sorted({'1', '2', '3'} & n.retired())})"


def test_twin_the_same_lots_retire_when_the_new_link_is_taken_down(tmp_path, monkeypatch):
    """The twin: the new link leaves the page on night 21. Then they retire."""
    n = Nights(tmp_path, monkeypatch)
    links = [_page(k) for k in range(12)]
    for night in range(1, 46):
        _eagent_night(n, night, _reupload(links, night, fail_from=21, take_down=True))
    assert {"1", "2", "3"} <= n.retired()


def _as_stored_before_the_fix(path):
    """What a harvest before this review left: every row of link 0 still naming the link
    it was first stored under, whatever link it was read through since."""
    con = sqlite3.connect(str(path))
    con.execute("UPDATE buildings SET stocklist_file='https://files.example/link-0' "
                "WHERE stocklist_file='https://files.example/link-0-reuploaded'")
    # ...and no record of the link the latest read came through (the merge's column).
    con.execute("UPDATE buildings SET last_stocklist_file=NULL")
    con.commit()
    con.close()


@pytest.mark.parametrize("take_down", [False, True])
def test_rows_stored_before_the_fix_still_wait_for_a_failing_new_link(tmp_path, monkeypatch,
                                                                      take_down):
    """The 1,301 rows of 30 Sep that name a link other than their document's: re-read
    through the new link but never told so. While the new link holds link 0's place on the
    page and fails, nothing it may carry is counted gone. Twin (take_down): the place is
    emptied, and they retire."""
    n = Nights(tmp_path, monkeypatch)
    links = [_page(k) for k in range(12)]
    for night in range(1, 46):
        _eagent_night(n, night, _reupload(links, night, fail_from=21, take_down=take_down),
                      after_harvest=_as_stored_before_the_fix)
        if not take_down:
            assert not {"1", "2", "3"} & n.retired(), f"night {night}: retired"
    if take_down:
        assert {"1", "2", "3"} <= n.retired()


@pytest.mark.parametrize("reuploaded", [True, False])
def test_a_refused_lot_voids_the_proof_after_a_reupload_too(tmp_path, monkeypatch,
                                                            reuploaded):
    """A2. The 'a lot refused by a constraint voids the proof' case, with one change: the
    tab was re-uploaded under a new link on night 4 (same lots). Tonight's manifest says
    the NEW link had a lot refused; the veto must still reach its document. Twin
    (reuploaded=False) is test_a_lot_refused_by_a_constraint_voids_the_proof."""
    import harvest_manifest
    n = Nights(tmp_path, monkeypatch)
    old = "https://docs.example/verv-tab"
    new = "https://docs.example/verv-tab-reuploaded" if reuploaded else old
    for night in range(1, 4):
        n.run(night, [_lot(i, night, doc=old) for i in range(1, 31)])
    for night in range(4, 6):                    # all 30 read through the new link
        n.run(night, [_lot(i, night, doc=new) for i in range(1, 31)])
    for night in range(6, 20):
        n.run(night, [_lot(i, night, doc=new) for i in range(1, 13)])
    for night in (20, 21):
        n.run(night, [_lot(i, night, doc=new) for i in range(1, 13)],
              documents={harvest_manifest.stocklist_key(new): {"csv_rows": 13,
                                                              "endpoint": "export"}},
              entries=[_link(new, 12, rejected=1)])
    assert n.retired() == set(), "a read with a refused lot proved its list whole"


def test_twin_the_reuploaded_tab_with_nothing_refused_retires_its_backlog(tmp_path,
                                                                         monkeypatch):
    """The twin of the test above: the re-uploaded tab's export proves the read and
    nothing was refused, so the 18 lots it no longer carries retire."""
    import harvest_manifest
    n = Nights(tmp_path, monkeypatch)
    old, new = "https://docs.example/verv-tab", "https://docs.example/verv-tab-reuploaded"
    for night in range(1, 4):
        n.run(night, [_lot(i, night, doc=old) for i in range(1, 31)])
    for night in range(4, 6):
        n.run(night, [_lot(i, night, doc=new) for i in range(1, 31)])
    for night in range(6, 22):
        proof = night >= 20
        n.run(night, [_lot(i, night, doc=new) for i in range(1, 13)],
              documents={harvest_manifest.stocklist_key(new): {
                  "csv_rows": 13, "endpoint": "export"}} if proof else None,
              entries=[_link(new, 12)] if proof else ())
    assert n.retired() == {str(i) for i in range(13, 31)}


@pytest.mark.parametrize("first_link_stays", [True, False])
def test_a_document_two_links_lead_to_waits_while_either_is_offered(tmp_path, monkeypatch,
                                                                    first_link_stays):
    """One document reached through two links: lots 9001-9003 read through link 1, lot
    9004 through link 2 -- so the document resolves to link 2, its most recently stored
    row's. Link 2 leaves the page; link 1 stays but reads nothing (zero-yield: fetched, no
    lot came out, which is not a list of what sold). While link 1 is offered the
    document's rows are still offered. Twin: link 1 leaves too, and they retire."""
    doc = "https://files.example/doc-x"
    l1, l2 = "https://files.example/link-x1", "https://files.example/link-x2"
    lots_x = {"9001", "9002", "9003", "9004"}
    n = Nights(tmp_path, monkeypatch)
    links = [_page(k) for k in range(20)]
    for night in range(1, 30):
        lots, entries = [], []
        if night <= 5:
            lots = [_lot(i, night, doc=doc, stocklist_file=l1, builder="Builder X")
                    for i in (9001, 9002, 9003)]
            lots.append(_lot(9004, night, doc=doc, stocklist_file=l2, builder="Builder X"))
            entries = [_link(l1, 3, heading="Builder X"),
                       _link(l2, 1, heading="Builder X", label="Second List")]
        elif first_link_stays:
            entries = [_link(l1, 0, heading="Builder X", outcome="zero-yield")]
        _eagent_night(n, night, links, extra_lots=lots, extra_entries=entries)
        if first_link_stays:
            assert not lots_x & n.retired(), f"night {night}: retired"
    if not first_link_stays:
        assert lots_x <= n.retired()


def test_a_reread_keeps_the_rows_first_link_and_records_the_one_it_came_through(tmp_path):
    """MERGED WITH MAIN 1858d55. This review first moved stocklist_file with each read;
    main's per-file state votes (enrich_buildings, tests/test_aplace_sections.py) rest on a
    row keeping the link it was first stored under, so record_building keeps it again and
    writes the link each read came through to last_stocklist_file. A read that names no
    link (a portal, an inbox) leaves both as they were."""
    from database import ResearchDatabase
    db = ResearchDatabase(db_path=tmp_path / "r.db")
    db.record_building(_lot(1, 1, doc="https://files.example/a"))
    db.record_building(_lot(1, 2, doc="https://files.example/a",
                            stocklist_file="https://files.example/a-reuploaded"))
    con = sqlite3.connect(str(tmp_path / "r.db"))
    q = "SELECT stocklist_file, last_stocklist_file, COUNT(*) FROM buildings"
    assert con.execute(q).fetchone() == ("https://files.example/a",
                                         "https://files.example/a-reuploaded", 1)
    db.record_building(_lot(1, 3, doc="https://files.example/a", stocklist_file=None))
    assert con.execute(q).fetchone() == ("https://files.example/a",
                                         "https://files.example/a-reuploaded", 1)
    con.close()


def test_the_ledger_finds_the_link_a_row_came_through(tmp_path):
    """The ledger reaches a re-shared link's line -- a lot refused, a folder budget bound --
    by the row's last_stocklist_file; for a row stored before that column, by tonight's
    manifest naming the documents each link served ("docs",
    harvest_manifest.count_outcomes), or by the document's own url where that is the link.
    Twin: a row with neither, and a line that names no documents, reaches nothing."""
    import harvest_manifest
    import list_ledger as LL
    from database import ResearchDatabase
    from datetime import datetime
    db = ResearchDatabase(db_path=tmp_path / "r.db")
    db.record_building(_lot(1, 1, doc="https://files.example/a"))
    db.record_building(_lot(1, 2, doc="https://files.example/a",
                            stocklist_file="https://files.example/a-reuploaded"))
    con = sqlite3.connect(str(tmp_path / "r.db"))
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM buildings")]
    con.close()
    legacy = [dict(r, last_stocklist_file=None) for r in rows]
    # The harvest's own line for the new link, with the document it served tonight.
    harvest_manifest.reset()
    e = harvest_manifest.entry("E-Agent", "stock link", "k-new", name="Example Homes",
                               stocklist_key=harvest_manifest.stocklist_key(
                                   "https://files.example/a-reuploaded"))
    harvest_manifest.add_piece(e, "file", "read", 1)
    listing = _lot(1, 2, doc="https://files.example/a")
    harvest_manifest.tag([listing], e)
    harvest_manifest.count_outcomes([listing], ["rejected"])
    assert e["docs"] == [harvest_manifest.stocklist_key("https://files.example/a")]
    line = json.loads(json.dumps(e))
    bare = {k: v for k, v in line.items() if k != "docs"}
    clean = dict(bare, stored=dict(line["stored"], rejected=0))
    notes = {line["stocklist_key"]: {"truncated": True, "why": "file budget"}}
    m = {"schema": 1, "entries": [line], "_made": datetime(2026, 9, 3, 3, 0)}
    key = ("https://files.example/a", "")
    refused = "link had 1 lot(s) refused by a constraint"

    def veto(rs, *entries, **extra):
        return LL.Tonight(dict(m, entries=list(entries), **extra), rs)._eagent(key, rs)[0]
    # The row's own record of the link it came through: no docs needed.
    assert veto(rows, bare) == refused
    assert veto(rows, clean, link_notes=notes) == "link truncated (file budget)"
    assert veto(rows, clean) is None
    # A row stored before the column: tonight's docs say which link served its document.
    assert veto(legacy, line) == refused
    assert veto(legacy, dict(clean, docs=line["docs"]), link_notes=notes) == \
        "link truncated (file budget)"
    # Twin: neither, and nothing is reached.
    assert veto(legacy, bare) is None
    assert veto(legacy, clean, link_notes=notes) is None
    # And a document that is its own link (a direct PDF re-uploaded: the document's url is
    # the new link, the row still names the old) is found by its url, docs or not.
    new_doc = "https://files.example/a-reuploaded"
    moved = [dict(r, source_url=new_doc) for r in legacy]
    assert LL.Tonight(dict(m, entries=[bare]), moved)._eagent((new_doc, ""), moved)[0] \
        == refused
    assert LL.Tonight(dict(m, entries=[clean]), moved)._eagent((new_doc, ""), moved) == \
        (None, None)


def _forget_the_read_link(path):
    """Rows as stored before last_stocklist_file existed."""
    con = sqlite3.connect(str(path))
    con.execute("UPDATE buildings SET last_stocklist_file=NULL")
    con.commit()
    con.close()


@pytest.mark.parametrize("how, retires", [
    ("the row records its link", False),
    ("rows stored before the column, the line names its documents", False),
    ("rows stored before the column, a line that names none", True),     # the twin
])
def test_a_refused_lot_on_a_reshared_link_voids_the_proof(tmp_path, monkeypatch, how,
                                                          retires):
    """The refused-lot case once more, with the link re-shared on night 4 while the
    document keeps its url (a folder shared again: the files inside are the same). Each
    row keeps its first link; tonight's NEW link had a lot refused, and that veto must
    reach the list -- through the link each row's latest read came through
    (last_stocklist_file), or, for rows stored before that column, through tonight's line
    naming the documents it served. Then nothing retires. The twin, legacy rows and a line
    that names no documents: the export proves the read and the 18 lots retire -- the gate
    this guards."""
    import harvest_manifest
    n = Nights(tmp_path, monkeypatch)
    doc = "https://docs.example/verv-tab"
    new = "https://docs.example/verv-folder-reshared"
    legacy = how.startswith("rows stored before")
    hook = _forget_the_read_link if legacy else None
    for night in range(1, 4):
        n.run(night, [_lot(i, night, doc=doc) for i in range(1, 31)], after_harvest=hook)
    for night in range(4, 6):
        n.run(night, [_lot(i, night, doc=doc, stocklist_file=new) for i in range(1, 31)],
              after_harvest=hook)
    for night in range(6, 20):
        n.run(night, [_lot(i, night, doc=doc, stocklist_file=new) for i in range(1, 13)],
              after_harvest=hook)
    for night in (20, 21):
        line = _link(new, 12, rejected=1)
        if how.endswith("names its documents"):
            line["docs"] = [harvest_manifest.stocklist_key(doc)]
        n.run(night, [_lot(i, night, doc=doc, stocklist_file=new) for i in range(1, 13)],
              documents={harvest_manifest.stocklist_key(doc): {"csv_rows": 13,
                                                              "endpoint": "export"}},
              entries=[line], after_harvest=hook)
    assert {r["stocklist_file"] for r in n.rows().values()} == {doc}
    if retires:
        assert n.retired() == {str(i) for i in range(13, 31)}
    else:
        assert n.retired() == set(), "a read with a refused lot proved its list whole"


# ------------------------------------------------------------------ 3. what Proxima could not read

def _dropdown_nights(n, runs_for, gone=205, from_night=4, nights=25):
    for night in range(1, nights + 1):
        lots, entries = [], []
        for p in range(200, 212):
            if p == gone and night >= from_night:
                continue
            lots += [_lot(p * 10 + i, night, channel="Proxima", builder=f"Developer {p}",
                          doc="https://proxima.example/projects", source_project_id=str(p))
                     for i in (1, 2)]
            entries.append(_project_entry(str(p), 2, stated=2, whole="uncapped"))
        n.run(night, lots, entries=entries, runs=runs_for(night, len(entries)))
        yield night


def _failing_from_night_4(stats=None, extra=1, errors=0):
    """A clean run note on nights 1-3; from night 4 one listed project (205) has no line
    and the note says why, as the reader would."""
    def runs_for(night, k):
        if night < 4:
            return _px_run(k)
        return _px_run(k + extra, logged=k, errors=errors, stats=stats)
    return runs_for


RUN_NOTES = {
    "no run note (a harvest from before this)": lambda night, k: None,
    "project unreadable": _failing_from_night_4({"project unreadable": 1}),
    "project errored": _failing_from_night_4({"project errored": 1}),
    "off-index project errored": _failing_from_night_4({"off-index project errored": 1}),
    "off-index project published no lots": _failing_from_night_4(
        {"off-index project published no lots": 1}),
    "a listed project with no line": _failing_from_night_4(),
    "the reader logged an error": _failing_from_night_4(extra=0, errors=1),
}


@pytest.mark.parametrize("note", list(RUN_NOTES), ids=list(RUN_NOTES))
def test_an_unreadable_project_still_in_the_dropdown_retires_nothing(tmp_path, monkeypatch,
                                                                     note):
    """A3. Project 205 stays in the account's dropdown, but from night 4 its accordion does
    not open: proxima.py bumps 'project unreadable' (or 'project errored') and continues
    before _log_project, so tonight's manifest has no line for it -- the same manifest a
    project removed from the dropdown gives. A failed read must not retire its lots. Main
    keeps them; the branch before this review retired them on night 17."""
    n = Nights(tmp_path, monkeypatch)
    for night in _dropdown_nights(n, RUN_NOTES[note]):
        assert not {"2051", "2052"} & n.retired(), f"night {night}: retired"
    assert "rule B, Proxima: not counted tonight" in n.out, n.out[-1500:]


def test_twin_a_project_that_left_the_dropdown_retires(tmp_path, monkeypatch):
    """The twin: the reader read every project it listed (a clean run note), and 205 was
    not among them. Its lots retire after 14 counted nights."""
    n = Nights(tmp_path, monkeypatch)
    for night in _dropdown_nights(n, lambda night, k: _px_run(k)):
        if night == 16:
            assert not {"2051", "2052"} & n.retired(), "retired before 14 counted nights"
    assert {"2051", "2052"} <= n.retired()
    assert not {str(p * 10 + i) for p in range(200, 212) if p != 205 for i in (1, 2)} \
        & n.retired()


def test_the_harvest_writes_what_proxima_said_of_its_run(tmp_path, monkeypatch):
    """harvest_buildings puts the Proxima reader's own counts, and when the harvest began,
    in the manifest -- counts only, never a link."""
    import config
    import harvest_buildings as HB
    import harvest_manifest as HM
    from database import ResearchDatabase
    import logging

    path = tmp_path / "m.db"
    monkeypatch.setattr(config, "DATABASE_PATH", path)
    ResearchDatabase(db_path=path)

    class FakePx:
        def __init__(self, *a, **k):
            self.projects_with_stock, self.projects_seen = 1, 3
            self.cross_listed, self.filter_found, self.short_projects = [], {}, []
            self.stats = {"project unreadable": 1, "project errored": 1}
            self.project_log = [{"pid": "724", "name": "Lindfield House", "on_index": True,
                                 "via": "accordion", "header_counter": None, "stated": 3,
                                 "printed": 3, "replays": 0, "bands": 0, "short": False}]

        def search(self, filters):
            logging.getLogger("spb.scraper.proxima").error("Proxima harvest crashed: x")
            return [{"lot_address": f"Lot {i}, 1 X St, LINDFIELD, NSW, 2070",
                     "source_channel": "Proxima", "source_project_id": "724",
                     "advertised_package_price": 900000 + i} for i in range(3)]

    monkeypatch.setattr(HB, "ProximaSource", FakePx)
    monkeypatch.setattr(HB, "_require_readers", lambda: True)
    monkeypatch.setattr(HB, "BuilderRegistry", lambda: type(
        "R", (), {"get_all_builders": lambda s: []})())
    out = tmp_path / "harvest_manifest.json"
    monkeypatch.setattr(HM, "write", lambda path=None, _w=HM.write: _w(out))
    monkeypatch.setattr(HB, "_record_unreadable_stocklists", lambda: None)
    with contextlib.redirect_stdout(io.StringIO()):
        HB.harvest(eagent=False, portals=False, email=False, proxima=True, rea=False)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["runs"]["Proxima"] == {"stats": {"project unreadable": 1,
                                                 "project errored": 1},
                                       "projects_seen": 3, "projects_logged": 1,
                                       "errors": 1}, data["runs"]
    assert data["started"] and len(data["started"]) == len("24/10/2026 09:00")
    import list_ledger
    tonight = list_ledger.Tonight(dict(data, _made=list_ledger.datetime.now()), [])
    assert "2 listed project(s) could not be read" in list_ledger.proxima_run_problem(tonight)


# ------------------------------------------------------------------ probes that should hold

def test_a_lot_that_comes_back_and_leaves_again(tmp_path, monkeypatch):
    """Retired, re-listed (status restored), then leaves again: retires again, and its
    status comes back right a second time."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 18):
        carried = range(1, 13) if night < 4 else range(1, 12)
        n.run(night, [_lot(i, night, status="Available") for i in carried])
    assert n.retired() == {"12"}
    for night in range(18, 21):
        n.run(night, [_lot(i, night) for i in range(1, 13)])     # no status column
    r = n.rows()["12"]
    assert r["superseded_by"] is None and r["availability_status"] == "Available", r
    for night in range(21, 40):
        n.run(night, [_lot(i, night) for i in range(1, 12)])
    assert n.retired() == {"12"}
    for night in range(40, 42):
        n.run(night, [_lot(i, night) for i in range(1, 13)])
    r = n.rows()["12"]
    assert r["superseded_by"] is None and r["availability_status"] == "Available", r


def test_proxima_down_eleven_nights_then_back(tmp_path, monkeypatch):
    """12 lots, lot 12 retired; Proxima reads nothing for 11 nights (no project lines);
    then back whole. Nothing for sale retires, the retirement holds throughout."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 45):
        lots, entries = _others(night)
        if 20 <= night <= 30:
            lots, entries = [], []
        else:
            carried = range(1, 13) if night < 4 else range(1, 12)
            lots += [_proxima(i, night, status="Available") for i in carried]
            entries.append(_project_entry("587", len(carried), stated=len(carried),
                                          whole="uncapped"))
        n.run(night, lots, entries=entries, runs=_px_run(len(entries)))
        if night >= 17:
            assert n.retired() & {str(i) for i in range(1, 13)} == {"12"}, \
                (night, sorted(n.retired()))


def test_a_list_read_every_other_night_whole(tmp_path, monkeypatch):
    """A sheet read whole every other night (the nights between, its fetch fails and the
    link is in the manifest as failed). Lot 12 leaves on night 4: it retires; the rest
    never do."""
    doc = "https://docs.example/alternate"
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 40):
        if night % 2:
            carried = range(1, 13) if night < 4 else range(1, 12)
            n.run(night, [_lot(i, night, doc=doc) for i in carried],
                  entries=[_link(doc, len(carried))])
        else:
            n.run(night, [], entries=[_link(doc, 0, outcome="fetch failed")])
        assert not {str(i) for i in range(1, 12)} & n.retired(), night
    assert "12" in n.retired()


def test_torsion_portal_copy_retired_eagent_copies_read_tonight(tmp_path, monkeypatch):
    """A portal capture retired; the same line read tonight through E-Agent AND email:
    both fresher than the retired copy. Neither is hidden behind it."""
    import supersede_duplicates as SD
    from database import WITHDRAWN_FROM_SOURCE
    n = Nights(tmp_path, monkeypatch)
    line = "Lot 7 Banksia Rise 4 bed $707,000"
    n.db.record_building(_lot(7, 1, channel=PORTAL, builder="Torsion Homes",
                              doc="https://portal.example/lot/7", source_text=line,
                              suburb="Tarneit", state="VIC", bedrooms=4, bathrooms=2,
                              car_spaces=2, land_sqm=400, house_sqm=200,
                              advertised_package_price=707000))
    con = sqlite3.connect(str(n.path))
    con.execute("UPDATE buildings SET superseded_by=?, availability_status='Sold' "
                "WHERE lot_number='7'", (WITHDRAWN_FROM_SOURCE,))
    con.commit()
    con.close()
    n.db.record_building(_lot(77, 30, source_text=line, advertised_package_price=707000,
                              builder="Torsion Homes"))
    n.db.record_building(_lot(78, 30, channel="digital email", doc="inbox",
                              source_text=line, advertised_package_price=707000,
                              builder="Torsion Homes"))
    with contextlib.redirect_stdout(io.StringIO()):
        SD.main([])
    rows = n.rows()
    live = [k for k in ("77", "78") if rows[k]["superseded_by"] is None]
    assert live, "the lot its sources listed tonight was hidden"
