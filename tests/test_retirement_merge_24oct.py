"""The retirement ledger merged with main 1858d55 (24 Oct 2026): three things main brought
that the ledger must agree with, each through the real nightly chain where it can be
(the harness of tests/test_retirement_multinight.py: record_building, a harvest manifest,
supersede_duplicates, repair_rows --apply, one call per night).

  1. REALNET'S DRIVE FOLDER (realnet-wa-drive) joined RELISTING_CHANNELS: a package whose
     file leaves the folder retires after a fortnight. A package whose file is still there
     but did not download, whose brochure does not print its name's price, that a
     constraint refused or whose new name the reader cannot parse drops out of the read
     just the same -- one file in 42 is far under 40% -- so a RealNet package retires only
     on a night whose manifest line lists the folder's files and its own file is not among
     them. A failed file holds its own package, never the folder: the real folder fails the
     Echo Beach brochure every night. A night that cannot list the folder holds them all.
  2. ONE READ DATE PER RUN (fix-proxima-day). Every row a run reads carries the day its
     block opened; harvest_manifest notes that day beside "started", and the ledger counts
     it among tonight's read dates -- a block opened at 23:59:59 has started=00:00 the next
     day, and without the stated date its reads were taken for yesterday's and judged
     without their manifest lines.
  3. A WITHDRAWN BUILDER'S ROWS (fix-bathla-projects) stay in the database and Proxima still
     reads Bathla's projects whole. Counted, they carry a cut read over the 40% line, or
     hold a list partial for good once they are no longer read; they are set aside in
     every list and never retired.

Each safety test has a twin that opens the one gate it guards and then retires.
"""
import contextlib
import io
import json
import sqlite3
from datetime import datetime, timedelta

import pytest

import harvest_manifest
import list_ledger as LL
import repair_rows as RR
from tests.test_retirement_multinight import (Nights, _lot, _proxima, _project_entry,
                                              _ledger_reads, _night_dt)

FOLDER_ID = "113X94kcwbypLLEnmdGPVBHOg9nKd6hrO"
FOLDER = "https://drive.google.com/drive/folders/%s" % FOLDER_ID
RN = "RealNet Stocklist"


# ------------------------------------------------------------------ 1. RealNet

def _fid(i):
    return "f%d" % i


def _rn(i, night):
    """One RealNet package as the folder reader stores it: no builder, no status, the
    folder as its list, its own file as its lot page."""
    return _lot(i, night, channel=RN, builder="", doc=FOLDER, attribution_scope="project",
                listing_url="https://drive.google.com/file/d/%s/view" % _fid(i))


def _name(i):
    return "H+L (4x2), Package %d Estate, Haynes, JM - $%dk.pdf" % (i, 800 + i)


# What the real folder holds beside its packages, as the reader writes it (24 Oct 2026): the
# Echo Beach brochure, which prints no price, read past for its price list, which does --
# the brochure is a "zero-yield" piece on EVERY night, so a folder held whole for any file
# left unread would never retire anything -- a template whose name states no price, and a
# sub-folder. (Drive id, name, outcome, why.)
_BENIGN = (("eBro", "DUAL (2+1), Echo Beach, Mandurah - Brochure - $760k.pdf", "zero-yield",
            "it does not print the price its name states"),
           ("ePri", "DUAL (2+1), Echo Beach, Mandurah - Pricelist - $760k.pdf", "read", ""),
           ("eSmsf", "SMSF DUAL (2+1), Echo Beach, Mandurah - Brochure.pdf", "no price list",
            "its name states no package and price"),
           ("eArch", "Archive", "not a price list", "a sub-folder"))


def _folder_line(in_folder, read=None, failed=(), unread=(), unparsed=(), state="ok",
                 rejected=0, listed=True):
    """RealNet's folder line, made the way harvest_buildings makes it: one entry, one piece
    per file the reader met, and -- `listed` -- the hashed id of every file Drive listed:
    the packages `in_folder` and the benign files."""
    read = list(in_folder if read is None else read)
    harvest_manifest.reset()
    e = harvest_manifest.entry(RN, "stock link", "folder",
                               name="RealNet Stocklist (Google Drive)", state=state,
                               stocklist_key=harvest_manifest.stocklist_key(FOLDER))
    for i in read:
        harvest_manifest.add_piece(e, _name(i), "read", 1, "")
    for i in failed:
        harvest_manifest.add_piece(e, _name(i), "fetch failed", 0, "HTTP 500")
    for i in unread:
        harvest_manifest.add_piece(e, _name(i), "zero-yield", 0,
                                   "it does not print the price its name states")
    for i in unparsed:
        harvest_manifest.add_piece(e, "H+L (4x2) Package %d Estate Haynes JM.pdf" % i,
                                   "no price list", 0, "its name states no package and price")
    for _id, name, outcome, why in _BENIGN:
        harvest_manifest.add_piece(e, name, outcome, 1 if outcome == "read" else 0, why)
    if listed:
        harvest_manifest.note_listed(e, [_fid(i) for i in in_folder] + [b[0] for b in _BENIGN])
    e["stored"]["unchanged"] = len(read)
    e["stored"]["rejected"] = rejected
    return json.loads(json.dumps(e))


@pytest.mark.parametrize("how, retires", [
    ("its file will not download", False),
    ("its brochure does not print its price", False),
    ("a constraint refused it", False),
    ("its new name does not parse", False),
    ("its file left the folder", True),               # the twin: the gate the others guard
    ("its file will not download, the hold off", True),   # the harness can fail
])
def test_a_realnet_package_left_unread_never_retires(tmp_path, monkeypatch, how, retires):
    """Package 12 drops out of every read from night 4. While its file is still in the
    folder -- a download that fails every night, a brochure the reader holds back, a row a
    constraint refuses, a rename the reader cannot parse -- it must never retire: 1 of 12
    missing is far under 40%, so the old rule (main) retires it on night 17. The failure
    holds that package only: the folder's read stays whole. The twin: the file really left
    the folder, with the Echo Beach brochure unread beside it as on every real night, and
    the package retires on night 17 as on main. The harness twin: the per-package hold
    switched off, and the failed download retires on night 17."""
    if how.endswith("the hold off"):
        monkeypatch.setattr(LL, "realnet_hold", lambda tonight, row: None)
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 31):
        carried = list(range(1, 13)) if night < 4 else list(range(1, 12))
        gone = () if night < 4 else (12,)
        left = how == "its file left the folder" and night >= 4
        refused = how == "a constraint refused it"
        line = _folder_line(carried if left else list(range(1, 13)),
                            read=carried + list(gone if refused else ()),
                            failed=gone if how.startswith("its file will not download") else (),
                            unread=gone if how.startswith("its brochure") else (),
                            unparsed=gone if how.startswith("its new name") else (),
                            rejected=len(gone) if refused else 0)
        n.run(night, [_rn(i, night) for i in carried], entries=[line])
        if night == 16:
            assert n.retired() == set()
    if retires:
        assert n.retired() == {"12"}, "the package whose file left the folder stayed live"
        assert n.rows()["12"]["availability_status"] == "Sold"
    else:
        assert n.retired() == set(), "a package still in the folder retired on a failed read"
        assert "kept live -- 1: its file is still in the folder" in n.out
    # One file held is never the folder held: every read of it is whole.
    reads = [r for r in _ledger_reads(n.path) if r["channel"] == RN]
    assert reads and all(r["verdict"] == "whole" for r in reads), reads[-1]


def test_a_realnet_night_without_its_manifest_line_retires_nothing(tmp_path, monkeypatch):
    """Package 12's file leaves the folder on night 4. Nights 15-24 have no manifest (it
    was not written, or is too old to be tonight's): nothing can say which files are in the
    folder, so nothing retires -- main retires it on night 17. The read is vetoed (section
    12 says why), and the package would be held without the veto too
    (test_realnet_hold_reads_the_folders_own_listing). Night 25's line retires it."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 26):
        carried = list(range(1, 13)) if night < 4 else list(range(1, 12))
        n.run(night, [_rn(i, night) for i in carried],
              entries=[_folder_line(carried)], manifest=not 15 <= night <= 24)
        if night == 24:
            assert n.retired() == set(), "a RealNet package retired with no manifest line"
            assert "realnet stocklist: no manifest line proves the read clean" in n.out
    assert n.retired() == {"12"}


@pytest.mark.parametrize("night_says, retires", [
    ("the folder's files", True),                      # the twin
    ("a line from before files_listed", False),
    ("no line for its folder", False),
    ("a line for another folder", False),
])
def test_a_realnet_night_that_cannot_list_the_folder_retires_nothing(tmp_path, monkeypatch,
                                                                     night_says, retires):
    """Package 12's file leaves the folder on night 4 and every read is clean. On a night
    whose manifest cannot say which files the folder holds -- a line written before
    files_listed, no RealNet line at all, only another folder's -- nothing retires; main
    retires it on night 17. The twin: the line lists the folder's files, and it retires on
    night 17."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 21):
        carried = list(range(1, 13)) if night < 4 else list(range(1, 12))
        line = _folder_line(carried, listed=night_says != "a line from before files_listed")
        if night_says == "a line for another folder":
            line["stocklist_key"] = harvest_manifest.stocklist_key(FOLDER + "-moved")
        entries = [] if night_says == "no line for its folder" else [line]
        n.run(night, [_rn(i, night) for i in carried], entries=entries)
    assert n.retired() == ({"12"} if retires else set())


def test_realnet_hold_reads_the_folders_own_listing():
    """Tonight.realnet_hold, case by case: a package is cleared to retire only when its
    folder's line is clean, lists the folder's files, and its own file is not among them."""
    def row(i, **kw):
        return dict(_rn(i, 1), id=i, source_url=FOLDER, superseded_by=None, **kw)

    def tonight(*lines):
        return LL.Tonight({"schema": 1, "entries": list(lines),
                           "_made": datetime(2026, 10, 24, 12, 0)}, [])

    t = tonight(_folder_line([1, 2], read=[1], failed=[2]))
    assert LL.realnet_hold(t, row(2)) == "its file is still in the folder"
    assert LL.realnet_hold(t, row(1)) == "its file is still in the folder"
    assert LL.realnet_hold(t, row(3)) is None, "a file gone from the listing is cleared"
    assert LL.realnet_hold(None, row(3)) == "no harvest manifest tonight"
    assert LL.realnet_hold(tonight(), row(3)) == "tonight's manifest has no line for its folder"
    assert LL.realnet_hold(tonight(_folder_line([1], listed=False)), row(3)) == \
        "tonight's line for its folder does not list the folder's files"
    for field, value in (("state", "not_shared"), ("state", ""), ("outcome", "fetch failed")):
        bad = dict(_folder_line([1]), **{field: value})
        assert LL.realnet_hold(tonight(bad), row(3)) == "its folder was not read clean tonight"
    assert LL.realnet_hold(t, row(3, listing_url="")) == "its file cannot be named"
    assert LL.realnet_hold(None, row(3, source_channel="E-Agent")) is None, \
        "only a RealNet row is held"
    # The line names files by hash, never by the id that opens them.
    assert not {_fid(i) for i in (1, 2)} & set(json.dumps(t.folders).replace('"', " ").split())


def test_the_folder_line_is_not_an_eagent_link():
    """RealNet's folder is a "stock link" line too. It is not one of E-Agent's units, pages
    or link keys, and its lists are judged on their own line: a file that failed is no veto
    of the folder (it holds its own package), and a clean read is no proof."""
    link = {"channel": "E-Agent", "kind": "stock link", "page": "house-and-land-1",
            "heading": "Example Homes", "label": "Price List", "outcome": "read",
            "printed": 2, "stocklist_key": harvest_manifest.stocklist_key("https://x/a"),
            "unit": "u1", "pieces": [{"name": "f", "outcome": "read", "printed": 2}]}
    folder = _folder_line([1, 2, 3], read=[1, 3], failed=[2])
    m = {"schema": 1, "entries": [link, folder], "_made": datetime(2026, 10, 24, 12, 0)}
    t = LL.Tonight(m, [])
    units, pages, failed = LL.night_units(t)["E-Agent"]
    assert (units, pages, failed) == (1, {"house-and-land-1": 1}, [])
    assert harvest_manifest.stocklist_key(FOLDER) not in t.links_by_key
    assert t.folders == [folder]
    rows = [dict(_rn(i, 1), id=i, source_url=FOLDER, superseded_by=None,
                 date_checked="24/10/2026") for i in (1, 3)]
    key = RR._doc_key(rows[0], True)
    assert t.has_line(key, rows)
    assert t.veto_and_proof(key, rows, 2) == (None, None)
    moved = LL.Tonight(dict(m, entries=[link, dict(folder, stocklist_key="0" * 16)]), [])
    assert not moved.has_line(key, rows)
    assert moved.veto_and_proof(key, rows, 2) == (
        "realnet: tonight's manifest has no line for its folder", None)
    unlisted = LL.Tonight(dict(m, entries=[dict(folder, outcome="fetch failed")]), [])
    assert unlisted.veto_and_proof(key, rows, 2)[0] == "realnet folder fetch failed (ok)"


def test_realnet_is_pinned_to_its_reader():
    from sources import realnet_drive
    assert LL.REALNET == realnet_drive.CHANNEL
    assert LL.CLEAN_READ_REQUIRED <= RR.RELISTING_CHANNELS
    assert LL.REALNET not in LL.RULE_B_CHANNELS
    assert LL.REALNET not in LL.PROOF_REQUIRED
    m = LL._DRIVE_FILE.search(realnet_drive.FILE_URL % "1a-B_c9")
    assert m and m.group(1) == "1a-B_c9", "the ledger cannot name a package's file"


def _drive(monkeypatch, files, pdfs):
    from sources import realnet_drive as R
    from tests.test_realnet_drive import _Drive
    import harvest_buildings as HB
    drive = _Drive(files, pdfs)
    monkeypatch.setattr(HB, "RealNetDriveSource",
                        lambda *a, **k: R.RealNetDriveSource(folder=FOLDER_ID, session=drive))


@pytest.mark.parametrize("fails", [True, False])
def test_the_real_harvest_line_names_the_folders_files(tmp_path, monkeypatch, fails):
    """End to end through harvest_buildings.harvest and the real folder reader, two package
    files, and in one variant Rokeby's download answers 404. The folder's line lists both
    files, hashed -- no id reaches the manifest -- so a stored Rokeby package is kept (its
    file is still in the folder) whether or not its download failed, a package whose file
    is not listed is cleared, and the failure is no veto of the folder. And the run's one
    read date -- here a block that opened at 23:59:59 the day before -- is the date the
    rows carry and is noted in the manifest, so the read is tonight's though "started" is
    today."""
    pytest.importorskip("fitz")
    import config
    import harvest_buildings as HB
    import sources.base as SB
    from database import ResearchDatabase
    from tests.test_realnet_drive import HAYNES, ROKEBY, JM_HAYNES, _pdf

    path = tmp_path / "h.db"
    monkeypatch.setattr(config, "DATABASE_PATH", path)
    monkeypatch.setattr(HB, "ResearchDatabase", lambda *a, **k: ResearchDatabase(db_path=path))
    monkeypatch.setattr(HB, "BuilderRegistry", lambda *a, **k: None)
    monkeypatch.setattr(HB, "_record_unreadable_stocklists", lambda: None)
    out = tmp_path / "harvest_manifest.json"
    monkeypatch.setattr(harvest_manifest, "write",
                        lambda path=None, _w=harvest_manifest.write: _w(out))
    pdfs = {"fHaynes": _pdf(JM_HAYNES)}
    if not fails:
        pdfs["fRokeby"] = _pdf("$749,000\n")
    _drive(monkeypatch, [HAYNES, ROKEBY], pdfs)
    now = datetime.now()
    eve = (now - timedelta(days=1)).replace(hour=23, minute=59, second=59)

    class _Eve(datetime):
        @classmethod
        def now(cls, tz=None):
            return eve
    monkeypatch.setattr(SB, "datetime", _Eve)
    with contextlib.redirect_stdout(io.StringIO()):
        HB.harvest(eagent=False, portals=False, email=False, proxima=False, rea=False,
                   realnet=True)
    text = out.read_text(encoding="utf-8")
    data = json.loads(text)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM buildings")]
    con.close()
    assert rows and {r["date_checked"] for r in rows} == {eve.strftime("%d/%m/%Y")}
    assert data["read_date"] == eve.strftime("%d/%m/%Y")
    assert data["started"][:10] == now.strftime("%d/%m/%Y")
    [line] = [e for e in data["entries"] if e["channel"] == RN]
    assert line["files_listed"] == sorted(harvest_manifest.stocklist_key(i)
                                          for i in ("fHaynes", "fRokeby"))
    assert "fHaynes" not in text and "fRokeby" not in text, "a Drive id reached the manifest"
    tonight = LL.Tonight(dict(data, _made=datetime.now()), rows)
    newest = LL._as_date(rows[0]["date_checked"])
    assert tonight.read_tonight(rows, newest), "the run's own reads were not tonight's"
    assert tonight.night == LL._iso(newest)
    without = {k: v for k, v in data.items() if k != "read_date"}
    assert not LL.Tonight(dict(without, _made=datetime.now()), rows).read_tonight(
        rows, newest), "premise: without the stated date the read looks like yesterday's"
    key = RR._doc_key(rows[0], True)
    assert tonight.veto_and_proof(key, rows, len(rows)) == (None, None)
    rokeby = dict(rows[0], listing_url="https://drive.google.com/file/d/fRokeby/view")
    assert LL.realnet_hold(tonight, rokeby) == "its file is still in the folder"
    gone = dict(rows[0], listing_url="https://drive.google.com/file/d/fGone/view")
    assert LL.realnet_hold(tonight, gone) is None


# ------------------------------------------------------------------ 2. one read date per run

def _midnight_run(night):
    """A run whose read-date block opened just before midnight: every row carries the day
    before night `night`, is stamped just after midnight, and the manifest says
    started=00:00 and read_date=the day before."""
    midnight = _night_dt(night).replace(hour=0, minute=0, second=0)
    return midnight, midnight - timedelta(days=1)


@pytest.mark.parametrize("stated", [True, False])
def test_a_short_read_from_a_run_opened_before_midnight_stays_vetoed(tmp_path, monkeypatch,
                                                                     stated):
    """Proxima project 53 reads 15 of its 20 lots from night 4, and the manifest calls each
    read short. Every run opened its read date at 23:59:59, so its rows carry the day
    before while "started" says 00:00. With the run's read date in the manifest the veto
    holds and lots 16-20 never retire. The twin, a manifest without it (the hole): the
    reads are taken for yesterday's, judged by the bare anchor (5 of 20), and lots 16-20
    retire on night 17 -- as on main, which has no veto."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 25):
        midnight, run_day = _midnight_run(night)
        carried = range(1, 21) if night < 4 else range(1, 16)
        stamp = {"date_checked": run_day.strftime("%d/%m/%Y"),
                 "checked_at": (midnight + timedelta(seconds=30)).isoformat()}
        lots = [_proxima(i, night, pid="53", status="Available", **stamp) for i in carried]
        entry = (_project_entry("53", 20, stated=20, whole="uncapped") if night < 4
                 else _project_entry("53", 15, stated=20, short=True))
        n.run(night, lots, entries=[entry], started=midnight,
              generated=midnight + timedelta(minutes=40),
              read_date=run_day if stated else None)
    if stated:
        assert n.retired() == set(), "a read the manifest called short retired its lots"
        reads = [r for r in _ledger_reads(n.path) if r["channel"] == "Proxima"]
        assert reads[-1]["basis"] == "veto: proxima short 15/20", reads[-1]
    else:
        assert n.retired() == {str(i) for i in range(16, 21)}


def test_the_run_names_the_night_and_its_read_date():
    """Tonight's night is the run's read date, and that date is always one of its read
    dates. A stated date more than a day before the manifest was written names nothing."""
    made = datetime(2026, 10, 24, 0, 40)
    m = {"schema": 1, "entries": [], "_made": made, "started": "24/10/2026 00:00",
         "read_date": "23/10/2026"}
    t = LL.Tonight(m, [])
    assert t.night == "2026-10-23" and t.run_date == datetime(2026, 10, 23)
    assert t.read_dates == {"23/10/2026", "24/10/2026"}
    bare = LL.Tonight({k: v for k, v in m.items() if k != "read_date"}, [])
    assert (bare.night, bare.read_dates, bare.run_date) == ("2026-10-24", {"24/10/2026"},
                                                            None)
    old = LL.Tonight(dict(m, read_date="20/10/2026"), [])
    assert (old.night, old.read_dates, old.run_date) == ("2026-10-24", {"24/10/2026"}, None)


class _History:
    """A ledger's recorded nights, as plan_rule_b asks for them: 20 E-Agent links on one
    page, each offered the night before."""

    def __init__(self, links, night):
        self.links, self.night = links, night

    def channel_history(self, ch):
        if ch != "E-Agent":
            return []
        return [{"night": "2026-10-%02d" % d, "units": len(self.links),
                 "pages": {"p1": len(self.links)}, "healthy": True} for d in (19, 20, 21)]

    def offers(self, ch):
        if ch != "E-Agent":
            return {}
        return {u: [(self.night, "p1", h, "Price List", h.lower(), "read")]
                for u, h in self.links}


@pytest.mark.parametrize("stated", [True, False])
def test_rule_b_counts_absence_from_the_runs_read_date(monkeypatch, stated):
    """A run whose block opened on 23 Oct and whose repair ran at 00:50 on the 24th. A link
    taken down tonight (counted at once here) holds a lot last read on 10 Oct: 13 days
    before the run's read date, so it is not yet absent long enough to retire. The twin, a
    manifest that does not state the run's date: measured from the repair's own clock it is
    14 days, and it retires a day early."""
    from sources.e_agent import stocklist_file_key
    monkeypatch.setattr(LL, "UNOFFERED_NIGHTS", 1)
    links = ["https://files.example/link-%d" % k for k in range(20)]
    units = [(harvest_manifest.stocklist_key(stocklist_file_key(h)), "Builder %d" % k)
             for k, h in enumerate(links)]
    entries = [{"channel": "E-Agent", "kind": "stock link", "page": "p1", "heading": b,
                "label": "Price List", "outcome": "read", "printed": 1,
                "stored": {"rejected": 0}, "unit": u,
                "stocklist_key": harvest_manifest.stocklist_key(h),
                "pieces": [{"name": "f", "outcome": "read", "printed": 1}]}
               for h, (u, b) in zip(links, units)][1:]           # link 0 is taken down
    made = datetime(2026, 10, 24, 0, 40)
    m = {"schema": 1, "entries": entries, "_made": made, "started": "24/10/2026 00:00"}
    if stated:
        m["read_date"] = "23/10/2026"
    gone = [{"id": 1, "source_channel": "E-Agent", "builder_name": "Builder 0",
             "source_url": links[0], "stocklist_file": links[0], "superseded_by": None,
             "date_checked": "10/10/2026", "lot_number": "1"}]
    tonight = LL.Tonight(m, gone)
    plan = LL.plan_rule_b(_History(units, "2026-10-23"), tonight, gone, {},
                          datetime(2026, 10, 24, 0, 50))
    assert plan.counted, (plan.off, plan.blocked)
    retired = [(r["id"], days) for r, days, _rule, _basis in plan.retire]
    assert retired == ([] if stated else [(1, 14)])


@pytest.mark.parametrize("docs", [True, False])
def test_rule_b_never_counts_a_link_whose_document_a_new_link_served(monkeypatch, docs):
    """A row keeps its first link (merged with main: database.record_building). Link 0 is
    re-shared tonight under a new link with another heading, and its document is read
    through the new one: the old link is gone from the page, but what it carried is not.
    Tonight's manifest says the new link served the document ("docs"), so rule B does not
    count the old link -- and lot 2, last read 20 days ago, is rule A's to judge on the
    read, never "link offered no more". The twin, a line that does not say which documents
    it served: the old link is counted, and lot 2 retires by rule B."""
    from sources.e_agent import stocklist_file_key
    monkeypatch.setattr(LL, "UNOFFERED_NIGHTS", 1)
    links = ["https://files.example/link-%d" % k for k in range(20)]
    units = [(harvest_manifest.stocklist_key(stocklist_file_key(h)), "Builder %d" % k)
             for k, h in enumerate(links)]

    def entry(href, heading, doc=None):
        e = {"channel": "E-Agent", "kind": "stock link", "page": "p1", "heading": heading,
             "label": "Price List", "outcome": "read", "printed": 1,
             "stored": {"rejected": 0},
             "unit": harvest_manifest.stocklist_key(stocklist_file_key(href)),
             "stocklist_key": harvest_manifest.stocklist_key(href),
             "pieces": [{"name": "f", "outcome": "read", "printed": 1}]}
        if doc and docs:
            e["docs"] = [harvest_manifest.stocklist_key(doc)]
        return e
    doc = "https://docs.example/builder-0-tab"
    entries = [entry(h, b) for h, (_u, b) in zip(links, units)][1:]
    entries.append(entry("https://files.example/link-0-reshared", "Builder 0 (new list)", doc))
    made = datetime(2026, 10, 24, 12, 0)
    m = {"schema": 1, "entries": entries, "_made": made, "started": "24/10/2026 11:00",
         "read_date": "24/10/2026"}
    rows = [{"id": i, "source_channel": "E-Agent", "builder_name": "Builder 0",
             "source_url": doc, "stocklist_file": links[0], "superseded_by": None,
             "date_checked": seen, "lot_number": str(i)}
            for i, seen in ((1, "24/10/2026"), (2, "04/10/2026"))]
    tonight = LL.Tonight(m, rows)
    plan = LL.plan_rule_b(_History(units, "2026-10-23"), tonight, rows, {}, made)
    retired = [r["id"] for r, _days, _rule, _basis in plan.retire]
    if docs:
        assert not plan.counted and retired == [], (plan.counted, retired)
        assert plan.blocked["a link its rows were read through is still offered"] == 1
    else:
        assert retired == [2], (plan.off, plan.blocked)


def test_the_manifest_notes_the_runs_read_date(monkeypatch):
    """harvest_manifest.reset() inside a run's read-date block notes the block's date (the
    one every row carries); outside a run, today's."""
    import sources.base as SB
    monkeypatch.setattr(SB, "_RUN_DATE", "23/10/2026")
    harvest_manifest.reset()
    assert harvest_manifest.snapshot()["read_date"] == "23/10/2026"
    monkeypatch.setattr(SB, "_RUN_DATE", None)
    harvest_manifest.reset()
    assert harvest_manifest.snapshot()["read_date"] == datetime.now().strftime("%d/%m/%Y")


# ------------------------------------------------------------------ 3. withdrawn builders

def _sheet(nums, night, builder="Example Homes"):
    return [_lot(i, night, builder=builder, status="Available") for i in nums]


@pytest.mark.parametrize("extra", ["Bathla Development", "Other Homes"])
def test_a_withdrawn_builders_lots_never_carry_a_cut_read_over_the_line(tmp_path, monkeypatch,
                                                                        extra):
    """One price list: 12 of Example Homes' lots and 10 of Bathla's, all read every night.
    From night 4 the read is cut to 5 of Example's 12, Bathla's 10 still on it: 7 of 22 is
    under 40%, so the old rule (main) calls it whole and retires lots 6-12 on night 17.
    Bathla's rows set aside, it is 7 of 12: nothing retires. The twin: the same 10 lots
    named for a builder nobody withdrew, and lots 6-12 retire on night 17, as on main."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 31):
        mine = range(1, 13) if night < 4 else range(1, 6)
        n.run(night, _sheet(mine, night) + _sheet(range(21, 31), night, builder=extra))
    if extra.startswith("Bathla"):
        assert n.retired() == set(), "a cut read retired lots for sale"
        reads = [r for r in _ledger_reads(n.path) if r["channel"] == "E-Agent"
                 and r["members"] == 12]
        assert reads and reads[-1]["verdict"] == "partial", reads[-1:]
    else:
        assert n.retired() == {str(i) for i in range(6, 13)}


def test_the_old_rule_still_counts_a_withdrawn_builders_rows(tmp_path, monkeypatch):
    """SPB_RETIREMENT_RULE=old is main, exactly: Bathla's rows count, and lots 6-12 retire
    on night 17."""
    monkeypatch.setenv("SPB_RETIREMENT_RULE", "old")
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 19):
        mine = range(1, 13) if night < 4 else range(1, 6)
        n.run(night, _sheet(mine, night) + _sheet(range(21, 31), night,
                                                  builder="Bathla Development"))
    assert n.retired() == {str(i) for i in range(6, 13)}


@pytest.mark.parametrize("extra", ["Bathla Development", "Other Homes"])
def test_withdrawn_rows_no_longer_read_never_stall_a_list(tmp_path, monkeypatch, extra):
    """10 of Example Homes' lots and 12 of Bathla's, read on nights 1-2; from night 3 the
    harvest stores Bathla's no more, and Example's lot 10 sells on night 4. Counted, the 12
    unread rows hold the list over 40% missing for good (main never retires lot 10). Set
    aside, lot 10 retires on night 17 -- and Bathla's rows, gone from the read just as long,
    are never retired: they are offered nowhere already. The twin: the 12 named for a
    builder nobody withdrew stall the list, and nothing retires (main's behaviour)."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 23):
        mine = range(1, 11) if night < 4 else range(1, 10)
        lots = _sheet(mine, night)
        if night <= 2:
            lots += _sheet(range(21, 33), night, builder=extra)
        n.run(night, lots)
    if extra.startswith("Bathla"):
        assert n.retired() == {"10"}
        rows = n.rows()
        assert all(rows[str(i)]["superseded_by"] is None
                   and rows[str(i)]["availability_status"] == "Available"
                   for i in range(21, 33)), "a withdrawn builder's row was retired"
    else:
        assert n.retired() == set()


@pytest.mark.parametrize("extra", ["Bathla Development", "Other Homes"])
def test_a_withdrawn_builders_rows_are_never_retired_even_by_the_old_rule(tmp_path,
                                                                         monkeypatch, extra):
    """20 of Example Homes' lots and 2 of Bathla's on one list, all read on nights 1-2;
    from night 3 Bathla's two are on the read no more. 2 of 22 is far under 40%, so the old
    rule (main) retires both on night 17 -- and main parity would too, did it not skip
    them. They are offered nowhere already, and taking Bathla off WITHDRAWN_BUILDERS must
    bring them back as they were, so they stay as stored. The twin: the 2 named for a
    builder nobody withdrew retire on night 17, as on main."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 19):
        lots = _sheet(range(1, 21), night)
        if night <= 2:
            lots += _sheet((31, 32), night, builder=extra)
        n.run(night, lots)
    if extra.startswith("Bathla"):
        assert n.retired() == set(), "a withdrawn builder's row was retired"
        assert {n.rows()[k]["availability_status"] for k in ("31", "32")} == {"Available"}
    else:
        assert n.retired() == {"31", "32"}


def _row(i, days_ago=0, builder="Example Homes", pid="", channel="E-Agent", **kw):
    today = datetime(2026, 10, 24)
    r = {"id": i, "source_channel": channel, "builder_name": builder,
         "source_url": "https://docs.example/sheet", "stocklist_file":
         "https://docs.example/sheet", "source_project_id": pid, "superseded_by": None,
         "availability_status": "", "lot_number": str(i), "lot_address": f"Lot {i} X Street",
         "estate_name": "", "suburb": "", "content_hash": f"v2:h{i}",
         "date_checked": (today - timedelta(days=days_ago)).strftime("%d/%m/%Y")}
    r.update(kw)
    return r


def _verdict(members):
    led = LL.Ledger.empty(datetime(2026, 10, 24))
    return RR.list_verdicts(members, datetime(2026, 10, 24), led)[RR._doc_key(members[0],
                                                                             True)]


def test_withdrawn_rows_are_found_by_name_project_and_capture():
    """The same three arms builder_names gives the build and the checkers: a row naming
    Bathla (superseded or not), a blank-builder row in a project Bathla is filed under, and
    a blank-builder capture joined to one by superseded_by. Another builder's row in that
    project is not withdrawn."""
    rows = [_row(1, builder="Bathla Development", pid="499", channel="Proxima",
                 superseded_by="v2:h2"),
            _row(2, builder="", pid="499", channel="Proxima"),
            _row(3, builder="Other Developer", pid="499", channel="Proxima"),
            _row(4, builder="Bathla Group", pid="593", channel="Proxima",
                 superseded_by="v2:h5"),
            _row(5, builder="", pid="724", channel="Proxima"),
            _row(6, builder="", pid="724", channel="Proxima")]
    assert RR.withdrawn_builder_ids(rows) == {1, 2, 4, 5}


def test_a_render_cap_is_read_off_the_whole_read():
    """Proxima's 80-lot render cap is where the read stopped, whoever's lots are on it: 75
    of Other Developer's and 5 of Bathla's on the read is still a read of 80, vetoed. The
    twin, a read of 81, is not on a cap."""
    for printed, vetoed in ((80, True), (81, False)):
        members = [_row(i, 0, pid="776", channel="Proxima", builder="Other Developer")
                   for i in range(1, printed - 4)]
        members += [_row(500 + i, 0, pid="776", channel="Proxima",
                         builder="Bathla Development") for i in range(5)]
        members += [_row(900 + i, 20, pid="776", channel="Proxima",
                         builder="Other Developer") for i in range(10)]
        v = _verdict(members)
        assert (v.members, v.withdrawn, v.seen) == (printed - 5 + 10, 5, printed - 5)
        assert v.vetoed is vetoed, v.basis
        if vetoed:
            assert v.basis == "veto: proxima read ends on a render cap (80)"


@pytest.mark.parametrize("floor_on_members", [True, False])
def test_setting_withdrawn_rows_aside_never_makes_a_big_list_small(monkeypatch,
                                                                   floor_on_members):
    """20 of Bathla's lots on the read and 1 of 9 others: the share is over the 9 (8 of 9
    missing) but the 10-row floor is on all 29 the list holds, so it is a failed read. The
    twin, the floor on the rows counted: 9 is under 10, and the cut read is 'whole'."""
    monkeypatch.setattr(LL, "ANCHOR_FLOOR_ON_MEMBERS", floor_on_members)
    members = [_row(i, 0, builder="Bathla Development") for i in range(1, 21)]
    members += [_row(100, 0)] + [_row(100 + i, 20) for i in range(1, 9)]
    v = _verdict(members)
    assert (v.members, v.seen, v.withdrawn) == (9, 1, 20)
    assert "anchor 8/9 accountable missing" in v.basis and "20 withdrawn" in v.basis
    assert v.whole is (not floor_on_members)


def test_a_list_of_nothing_but_withdrawn_rows_is_not_judged():
    members = [_row(i, 0, builder="Bathla Development") for i in range(1, 12)]
    assert RR.list_verdicts(members, datetime(2026, 10, 24),
                            LL.Ledger.empty(datetime(2026, 10, 24))) == {}
    led = LL.Ledger.empty(datetime(2026, 10, 24))
    v = led.judge(RR._doc_key(members[0], True), members, datetime(2026, 10, 24),
                  withdrawn=RR.withdrawn_builder_ids(members))
    assert not v.whole and v.members == 0, "judged directly, it is never whole"
