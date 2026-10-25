"""The third adversarial review of the retirement ledger (25 Oct 2026, on e7fc7ea): six ways
a read that was cut short still retired lots for sale that main 81b2262 never retires,
each through the real nightly chain (the harness of tests/test_retirement_multinight.py:
record_building, supersede_duplicates, repair_rows --apply, one call per night). Each
safety test here fails on e7fc7ea; each twin shows the guard that now holds is what holds,
or that a sale still retires.

  1. RULE B'S SUCCESSOR GATE WAS 60%. A re-upload that silently kept 60-99% of a list --
     20, 24 or 27 of 30, a list split in two with one half cut, a renamed file in a clean
     folder cut to 20, a re-upload stranded before the ledger began -- left the dropped
     lots (still for sale) on a link "no longer offered", and they retired after 14
     counted nights. On the same link the step guard holds the same cut.
  6. ...AND THE ALBERTINE SHAPE: a folder link of 39 lots re-uploaded in the same place
     under a new document, carrying 36: the 3 left behind, all Available, retired.
     A vanished unit with a successor that leaves 2 or more lots still for sale behind now
     waits for a proof or a person's --accept of the successor's list.
  2. TWO CHANNELS AGREEING, LEANING ON AN E-AGENT READ THE LEDGER HELD PARTIAL THE SAME
     NIGHT. The pooled portal list had no whole read; an agreement on page 1 proved it.
  3. AN EXPORT CSV'S SLACK WAS JUDGED BEFORE THE STEP GUARD. max(3, 15%) of the CSV rows
     is wider than a step, so a parser dropping 12 of Tomorrow Homes' 117 lots was proven.
  4. "OK" WAS PROOF FOR THE STOCKLIST SEARCH. A walk that ended early -- a page that
     failed, a server ignoring page= -- still wrote state ok, short false.
  5. A CUT UNDER THE STEP BAR ON A LIST MAIN HOLDS PARTIAL. A 90% cut drops floor(10%),
     one short of a step unless the list is a multiple of ten, and a cut row whose status
     says gone was not counted (Luxton 10 -> 8). There a step is now 2 rows.

Beyond the six, the same holes one step further, each failing on e7fc7ea too: the
agreement laundered once the old rule retires E-Agent's dropped copies; an old Proxima
counter's slack (max(3, 10%)) proving a cut; a 2-lot drop judged on a night main still
trusted the list, retired after main gave up on it; and the reviewer's non-blocking
staircases (4a, 4b2, 3a) and second-design set-aside (4c).
"""
import sqlite3

import pytest

import harvest_manifest
from tests.test_retirement_multinight import (Nights, _lot, _proxima, _project_entry,
                                              _page, _eagent_night, _link, _px_run,
                                              _portal_entry, _ledger_reads, PORTAL)


def _say(n, night, want):
    got = sorted(want & n.retired(), key=lambda s: (len(s), s))
    return f"night {night}: retired {len(got)} for-sale lot(s): {got[:12]}"


def _lots(want):
    return {str(i) for i in want}


# ------------------------------------------------------------------ 1/6. rule B's successors

def _reupload(n, night, keep, total=30, cut_from=6, ratchet=False, same_link=False,
              status="", left_status=None):
    """Link 0 carries lots 1..total. From `cut_from` it is re-uploaded under a new link in
    the same place (page, heading, label) and the new link's read silently yields lots
    1..keep: outcome read, nothing failed, nothing refused. `ratchet`: 25 rows the sheet
    itself said were Sold, stored 20 days before night 1 -- main's 40% rule holds such a
    list partial for good. `same_link`: the cut happens on link 0 itself (the control).
    `left_status`: what the lots past `keep` said on link 0 (they were already "Sold")."""
    links = [_page(k) for k in range(1, 12)]
    old_doc = "https://files.example/link-0"
    extra = []
    if ratchet and night == 1:
        extra = [_lot(i, -20, doc=old_doc, builder="Builder 0", status="Sold")
                 for i in range(101, 126)]
    if night < cut_from:
        lots = extra + [_lot(i, night, doc=old_doc, builder="Builder 0",
                             status=(left_status if left_status and i > keep else status))
                        for i in range(1, total + 1)]
        _eagent_night(n, night, links, extra_lots=lots,
                      extra_entries=[_link(old_doc, total, heading="Builder 0")])
        return
    doc = old_doc if same_link else "https://files.example/link-0-reuploaded"
    lots = [_lot(i, night, doc=doc, builder="Builder 0", status=status)
            for i in range(1, keep + 1)]
    _eagent_night(n, night, links, extra_lots=lots,
                  extra_entries=[_link(doc, keep, heading="Builder 0")])


@pytest.mark.parametrize("keep", [20, 27])
def test_1a_a_reupload_silently_keeping_two_thirds_or_more_retires_nothing(
        tmp_path, monkeypatch, keep):
    """The re-upload carries 20 (27) of 30: over the 60% successor bar, so on e7fc7ea the
    10 (3) it dropped -- still for sale -- counted as no longer offered and retired on
    night 19. Main never retires them: the old link's list is stale."""
    n = Nights(tmp_path, monkeypatch)
    lost = _lots(range(keep + 1, 31))
    for night in range(1, 30):
        _reupload(n, night, keep)
        assert not lost & n.retired(), _say(n, night, lost)
    assert "its successor dropped 2 or more of its lots still listed for sale" in n.out, \
        n.out[-2000:]


@pytest.mark.parametrize("same_link", [False, True])
def test_1b_a_cut_reupload_of_a_list_main_holds_partial_retires_nothing(
        tmp_path, monkeypatch, same_link):
    """A list main holds partial (25 'Sold' rows of 55); from night 10 its re-upload
    silently carries 20 of its 30 lots for sale. On e7fc7ea the 10 retired from night 23;
    the control (the same cut on link 0 itself) is held by the step guard on both."""
    n = Nights(tmp_path, monkeypatch)
    lost = _lots(range(21, 31))
    for night in range(1, 36):
        _reupload(n, night, 20, cut_from=10, ratchet=True, same_link=same_link)
        assert not lost & n.retired(), _say(n, night, lost)


def test_1c_a_list_split_in_two_with_one_half_cut_retires_nothing(tmp_path, monkeypatch):
    """Link 0 (30 lots) is replaced by two links in its place: B carries 1-15 whole, C
    should carry 16-30 but its read silently stops at 20."""
    n = Nights(tmp_path, monkeypatch)
    lost = _lots(range(21, 31))
    links = [_page(k) for k in range(1, 12)]
    old = "https://files.example/link-0"
    b, c = "https://files.example/link-0-b", "https://files.example/link-0-c"
    for night in range(1, 30):
        if night < 6:
            lots = [_lot(i, night, doc=old, builder="Builder 0") for i in range(1, 31)]
            ents = [_link(old, 30, heading="Builder 0")]
        else:
            lots = [_lot(i, night, doc=b, builder="Builder 0") for i in range(1, 16)]
            lots += [_lot(i, night, doc=c, builder="Builder 0") for i in range(16, 21)]
            ents = [_link(b, 15, heading="Builder 0", label="Price List A"),
                    _link(c, 5, heading="Builder 0", label="Price List B")]
        _eagent_night(n, night, links, extra_lots=lots, extra_entries=ents)
        assert not lost & n.retired(), _say(n, night, lost)


def test_1d_stranded_before_the_ledger_with_a_successor_carrying_two_thirds(tmp_path,
                                                                           monkeypatch):
    """Link 0's 30 lots were stored 20 days before the ledger began; its re-upload is on
    the page every night and silently carries lots 1-20. 10 stranded of 30 was under the
    40% never-offered bar, so on e7fc7ea they retired from night 17."""
    n = Nights(tmp_path, monkeypatch)
    lost = _lots(range(21, 31))
    links = [_page(k) for k in range(1, 12)]
    new = ("https://files.example/link-0-reuploaded", "Builder 0", list(range(1, 21)),
           "Price List")
    for night in range(1, 30):
        extra = [_lot(i, -20, doc="https://files.example/link-0", builder="Builder 0")
                 for i in range(1, 31)] if night == 1 else []
        _eagent_night(n, night, [new] + links, extra_lots=extra)
        assert not lost & n.retired(), _say(n, night, lost)


def _folder_night(n, night, keep, old_status=""):
    """One cleanly read folder link: f.pdf (30 lots) nights 1-5, then g.pdf in its place,
    whose read silently stops at lot `keep`."""
    links = [_page(k) for k in range(1, 12)]
    folder = "https://files.example/folder"
    name, nums = ("f.pdf", range(1, 31)) if night <= 5 else ("g.pdf", range(1, keep + 1))
    lots = [_lot(i, night, doc=f"{folder}/{name}", stocklist_file=folder, builder="Builder F",
                 status=old_status if (night <= 5 and i > keep) else "")
            for i in nums]
    entry = _link(folder, len(lots), heading="Builder F", pieces=[
        {"name": name, "outcome": "read", "printed": len(lots), "why": ""}])
    _eagent_night(n, night, links, extra_lots=lots, extra_entries=[entry])


@pytest.mark.parametrize("keep", [20, 28])
def test_1e_a_file_renamed_in_a_clean_folder_and_cut_retires_nothing(tmp_path, monkeypatch,
                                                                    keep):
    """f.pdf replaced by g.pdf inside one cleanly read folder; g.pdf's read silently stops
    at lot 20 (28). Over the 60% bar, so on e7fc7ea what f.pdf left behind retired."""
    n = Nights(tmp_path, monkeypatch)
    lost = _lots(range(keep + 1, 31))
    for night in range(1, 30):
        _folder_night(n, night, keep)
        assert not lost & n.retired(), _say(n, night, lost)
        assert not _lots(range(1, keep + 1)) & n.retired()
    assert "a file new to its folder dropped 2 or more lots" in n.out, n.out[-2000:]


def test_twin_a_file_renamed_carrying_all_but_one_retires_the_one(tmp_path, monkeypatch):
    """The twin: g.pdf carries 29 of f.pdf's 30. One lot left behind may count, and it
    retires 14 nights after f.pdf went."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 30):
        _folder_night(n, night, 29)
        assert not _lots(range(1, 30)) & n.retired()
    assert "30" in n.retired()


def _albertine_night(n, night):
    """Albertine's External Agent list on the 26 Sep copy: a Dropbox folder link of 39
    lots, re-uploaded on night 5 under a new folder link in the same place (page, heading,
    label) with a new document, read clean ('read', nothing failed or refused), carrying
    36 of the 39. Lots 37-39 are Available and still on the builder's document."""
    links = [_page(k) for k in range(1, 12)]
    a, b = "https://files.example/albertine-a", "https://files.example/albertine-b"
    folder, doc, nums = ((a, a + "/price-list.pdf", range(1, 40)) if night < 5 else
                         (b, b + "/price-list-october.pdf", range(1, 37)))
    lots = [_lot(i, night, doc=doc, stocklist_file=folder, builder="Albertine Homes",
                 status="Available") for i in nums]
    entry = _link(folder, len(lots), heading="Albertine Homes", label="External Agent")
    _eagent_night(n, night, links, extra_lots=lots, extra_entries=[entry])


def test_6_the_albertine_reupload_leaves_its_three_lots_live(tmp_path, monkeypatch):
    """On e7fc7ea rule B retired lots 37-39 on night 18 ('link offered no more for 14
    counted night(s)'): the successor carried 36 of 39, over 60%. Main retires none."""
    n = Nights(tmp_path, monkeypatch)
    lost = _lots(range(37, 40))
    for night in range(1, 31):
        _albertine_night(n, night)
        assert not lost & n.retired(), _say(n, night, lost)
        assert not _lots(range(1, 37)) & n.retired()
    assert "waits: E-Agent | albertine homes" in n.out, n.out[-2000:]


def test_twin_a_reupload_carrying_28_of_30_waits_and_a_person_can_accept_it(tmp_path,
                                                                          monkeypatch):
    """The twin that changed (it retired the two on e7fc7ea): a re-upload carrying 28 of 30
    leaves two lots still listed for sale behind, and they wait -- main never retires them
    either. A person who checks the builder's list accepts the successor's read
    (repair_rows --accept LIST_KEY, printed in section 9b); from then the old link's nights
    count, and the two retire 14 counted nights later. The 28 never do."""
    import list_ledger
    n = Nights(tmp_path, monkeypatch)
    kept, left = _lots(range(1, 29)), {"29", "30"}
    for night in range(1, 12):
        _reupload(n, night, 28)
        assert not (kept | left) & n.retired(), _say(n, night, kept | left)
    lk = list_ledger.list_key(("https://files.example/link-0-reuploaded", ""))
    assert lk in n.out, "the successor's list key is not printed for a person"
    con = sqlite3.connect(str(n.path))
    assert list_ledger.accept(con, lk) is not None
    con.commit()
    con.close()
    for night in range(12, 30):
        _reupload(n, night, 28)
        assert not kept & n.retired(), _say(n, night, kept)
        if night <= 23:
            assert not left & n.retired(), f"night {night}: retired before 14 counted nights"
    assert left <= n.retired(), sorted(left - n.retired())


def test_twin_lots_left_behind_that_already_say_sold_still_retire(tmp_path, monkeypatch):
    """The twin: the two lots the re-upload leaves behind already said Sold on the old
    list. Nothing for sale is at stake, so they count and retire as before."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 24):
        _reupload(n, night, 28, left_status="Sold")
        assert not _lots(range(1, 29)) & n.retired()
    assert {"29", "30"} <= n.retired()


def test_twin_without_the_successor_hold_the_dropped_lots_retire(tmp_path, monkeypatch):
    """The twin: SUCCESSOR_HOLD_ROWS switched off, the 20-of-30 re-upload retires the 10 it
    dropped, as on e7fc7ea -- the hold is what keeps them, not the harness."""
    import list_ledger
    monkeypatch.setattr(list_ledger, "SUCCESSOR_HOLD_ROWS", 10 ** 6)
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 30):
        _reupload(n, night, 20)
    assert _lots(range(21, 31)) <= n.retired()


def test_twin_a_link_taken_down_with_no_successor_still_retires(tmp_path, monkeypatch):
    """The twin: link 0 leaves the page and nothing takes its place or carries its lots.
    Rule B's own case, unchanged: its two lots retire 14 counted nights later."""
    n = Nights(tmp_path, monkeypatch)
    links = [_page(k) for k in range(12)]
    for night in range(1, 20):
        _eagent_night(n, night, links if night <= 5 else links[1:])
    assert {"1", "2"} <= n.retired()


# ------------------------------------------------------------------ 2. two channels agree

def _torsion(n, night, carried, eagent, sold=False):
    host = "referrer.torsion.example"
    page = f"https://{host}/stocklist"
    kw = {"channel": PORTAL, "builder": "Torsion Homes"}
    lots = [_lot(i, night, doc=f"https://{host}/brochure?lot={i}", **kw) for i in carried]
    lots += [_lot(1000 + i, night, doc=page, builder="Torsion Homes",
                  source_text=f"E-Agent copy of lot {i}") for i in eagent]
    if night == 1 and sold:
        lots += [_lot(2000 + i, -20, doc=page, status="Sold", **kw) for i in range(1, 61)]
    n.run(night, lots, entries=[_portal_entry("Torsion Homes", len(carried), host=host),
                                _link(page, len(eagent), heading="Torsion Homes")])


def test_2_an_agreement_leaning_on_an_e_agent_read_held_partial_proves_nothing(
        tmp_path, monkeypatch):
    """The page was cut to page 1 (24 of 120) before the ledger began; the portal stores
    one link per card. Nights 1-3: E-Agent's link to the page reads 30, no agreement, the
    pooled list is partial. From night 4 E-Agent stops at 24 too: its own list is PARTIAL
    ('dropped 6 of 30'), and on e7fc7ea the same night the portal list was WHOLE ('two
    channels agree: portal 24, e-agent 24') -- 96 lots retired from night 8. Run past
    night 18, when the old rule retires E-Agent's 6 dropped copies (6 of 30 is under its
    40%) and E-Agent's list reads whole again at 24: the agreement still leans on a read
    a step under the 30 that list once read whole, and proves nothing."""
    n = Nights(tmp_path, monkeypatch)
    host = "referrer.torsion.example"
    rest = _lots(range(25, 121))
    for night in range(1, 30):
        if night == 1:
            for i in range(1, 121):
                n.db.record_building(_lot(i, -20, channel=PORTAL, builder="Torsion Homes",
                                          doc=f"https://{host}/brochure?lot={i}"))
        _torsion(n, night, range(1, 25), range(1, 31) if night < 4 else range(1, 25))
        assert not rest & n.retired(), _say(n, night, rest)
    portal = [r for r in _ledger_reads(n.path) if r["channel"] == PORTAL]
    assert all(r["verdict"] == "partial" for r in portal), portal[-1]
    bases = [r["basis"] for r in portal]
    assert any("the E-Agent list it leans on is partial tonight" in b for b in bases)
    assert "under the 30 the E-Agent list it leans on has read whole" in bases[-1], bases[-1]


STAIR = [120, 120, 120, 111, 102, 94, 86, 79, 72]


def test_2b_an_agreed_staircase_on_a_list_main_holds_partial_retires_nothing(tmp_path,
                                                                            monkeypatch):
    """The reviewer's 3a (non-blocking there, closed by the strict bar): both readers stop
    a little earlier each night, 120 to 72, each night under max(2, 10%) of the last whole
    read; 60 'Sold' rows make main hold the page's list partial. On e7fc7ea 48 retired."""
    n = Nights(tmp_path, monkeypatch)
    host = "referrer.torsion.example"
    page = f"https://{host}/stocklist"
    kw = {"channel": PORTAL, "builder": "Torsion Homes"}
    rest = _lots(range(73, 121))
    for night in range(1, 36):
        top = STAIR[night - 1] if night <= len(STAIR) else STAIR[-1]
        lots = [_lot(i, night, doc=page, **kw) for i in range(1, top + 1)]
        lots += [_lot(1000 + i, night, doc=page, builder="Torsion Homes",
                      source_text=f"E-Agent copy of lot {i}") for i in range(1, top + 1)]
        if night == 1:
            lots += [_lot(2000 + i, -20, doc=page, status="Sold", **kw) for i in range(1, 61)]
        n.run(night, lots, entries=[_portal_entry("Torsion Homes", top, host=host),
                                    _link(page, top, heading="Torsion Homes")])
        assert not rest & n.retired(), _say(n, night, rest)


# ------------------------------------------------------------------ 3. an export's slack

def _tab_night(n, night, doc, nums, csv_rows, sold, builder):
    docs = {harvest_manifest.stocklist_key(doc): {"csv_rows": csv_rows, "endpoint": "export"}}
    lots = [_lot(i, night, doc=doc, builder=builder, status="Available") for i in nums]
    if night == 1:
        lots += [_lot(i, -20, doc=doc, builder=builder, status="Sold") for i in sold]
    n.run(night, lots, documents=docs, entries=[_link(doc, len(lots), heading=builder)])


@pytest.mark.parametrize("total, keep, csv_rows, sold", [
    (30, 27, 31, range(101, 126)),           # the reviewer's 6b: a drop of 3 for 31 rows
    (117, 105, 123, range(1001, 1081)),      # 6c, Tomorrow Homes' scale (117 for 123)
])
def test_3_a_parser_dropping_lots_inside_the_csv_slack_retires_nothing(
        tmp_path, monkeypatch, total, keep, csv_rows, sold):
    """A Google tab on a list main holds partial. From night 4 the extractor silently drops
    lots keep+1..total while the export still has its rows: 27 listings for 31 rows (105
    for 123) is inside max(3, 15%), so on e7fc7ea the export proved the read whole before
    the step guard was consulted, and the dropped lots retired on night 17."""
    n = Nights(tmp_path, monkeypatch)
    doc = "https://docs.google.com/spreadsheets/d/tab/edit#gid=1"
    lost = _lots(range(keep + 1, total + 1))
    for night in range(1, 30):
        _tab_night(n, night, doc, range(1, (total if night < 4 else keep) + 1), csv_rows,
                   sold, "Tab Homes")
        assert not lost & n.retired(), _say(n, night, lost)
    last = [r for r in _ledger_reads(n.path) if r["rows_seen"] == keep][-1]
    assert last["verdict"] == "partial" and "does not account for the drop" in \
        last["basis"], last


def test_3_a_cut_under_an_old_proxima_counter_retires_nothing(tmp_path, monkeypatch):
    """Beyond the review, the same slack in Proxima's proof: a read is 'proven' under an
    OLD stored counter when it is up to max(3, 10%) under it (harvest_manifest.
    old_counter_gap_ok). Project 53, 60 lots for sale and 50 'Not Available' rows from
    before the ledger (main holds it partial), its counter stuck at 60: from night 4 the
    read silently stops at 55. On e7fc7ea 'proxima uncapped 55/60' proved it, and lots
    56-60 retired. Twin: test_twin_a_proven_cut_retires (the counter falls with the read)."""
    n = Nights(tmp_path, monkeypatch)
    lost = _lots(range(56, 61))
    for night in range(1, 26):
        top = 60 if night < 4 else 55
        lots = [_proxima(i, night, pid="53", status="Available") for i in range(1, top + 1)]
        if night == 1:
            lots += [_proxima(i, -20, pid="53", status="Not Available")
                     for i in range(101, 151)]
        n.run(night, lots, entries=[_project_entry("53", top, stated=60, whole="uncapped")])
        assert not lost & n.retired(), _say(n, night, lost)
    last = [r for r in _ledger_reads(n.path) if r["rows_seen"] == 55][-1]
    assert last["verdict"] == "partial" and "does not account for the drop" in \
        last["basis"], last


def test_twin_a_tab_whose_rows_fall_with_its_lots_retires_them(tmp_path, monkeypatch):
    """The twin: Tomorrow's tab sells 3 lots and its export loses the 3 rows with them (117
    for 123, then 114 for 120). The step (3 of 117, on a list main holds partial) is
    accounted for by the tab's own rows, so the export proves the read and the 3 retire."""
    n = Nights(tmp_path, monkeypatch)
    doc = "https://docs.google.com/spreadsheets/d/tomorrow/edit#gid=1"
    for night in range(1, 22):
        top, rows = (117, 123) if night < 4 else (114, 120)
        _tab_night(n, night, doc, range(1, top + 1), rows, range(1001, 1081), "Tomorrow")
        assert not _lots(range(1, 115)) & n.retired()
    assert _lots(range(115, 118)) <= n.retired(), sorted(_lots(range(115, 118)) - n.retired())


def test_twin_one_lot_leaving_a_tab_still_retires(tmp_path, monkeypatch):
    """The twin: one lot leaves the tab (a drop under any step) while its export keeps its
    rows -- the sale signal, on a list main holds partial. It retires."""
    n = Nights(tmp_path, monkeypatch)
    doc = "https://docs.google.com/spreadsheets/d/tab-one/edit#gid=1"
    for night in range(1, 22):
        _tab_night(n, night, doc, range(1, 31 if night < 4 else 30), 31, range(101, 126),
                   "Tab Homes")
        assert not _lots(range(1, 30)) & n.retired()
    assert "30" in n.retired()


# ------------------------------------------------------------------ 4. the Stocklist search

def _search_entry(printed, state="ok", short=False, **kw):
    e = {"channel": "E-Agent Stocklist", "kind": "search", "name": "E-Agent Stocklist",
         "state": state, "printed": printed, "short": short,
         "outcome": "read" if printed else "zero-yield",
         "stored": {"new": 0, "updated": 0, "unchanged": printed, "rejected": 0}}
    e.update(kw)
    return e


def _search_night(n, night, nums, stated=False, sold=False):
    doc = "https://members.e-agent.example/api/packages"
    lots = [_lot(i, night, channel="E-Agent Stocklist", builder=f"Builder {i % 7}", doc=doc)
            for i in nums]
    if sold and night == 1:
        lots += [_lot(i, -20, channel="E-Agent Stocklist", builder="Builder 0", doc=doc,
                      status="Sold") for i in range(1001, 1061)]
    kw = {"stated": len(nums), "records": len(nums)} if stated else {}
    n.run(night, lots, entries=[_search_entry(len(nums), **kw)])


def test_4_a_search_that_says_ok_without_a_total_proves_nothing(tmp_path, monkeypatch):
    """The reviewer's 6a: 120 packages read on nights 1-3, then 60 a night, the line saying
    state ok, short false (as the harvest wrote it whatever the walk did). On e7fc7ea
    'stocklist search ok' proved the read and the 60 retired from night 17; main holds
    the list partial (50% missing)."""
    n = Nights(tmp_path, monkeypatch)
    lost = _lots(range(61, 121))
    for night in range(1, 30):
        _search_night(n, night, range(1, 121) if night < 4 else range(1, 61))
        assert not lost & n.retired(), _say(n, night, lost)


def test_twin_a_search_that_read_its_stated_total_proves_its_read(tmp_path, monkeypatch):
    """The twin: the site states its total every night, the walk reads all of it, and 30
    packages sell at once on a list main holds partial (60 'Sold' rows). The stated total
    proves the read, so the 30 retire."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 22):
        _search_night(n, night, range(1, 121) if night < 4 else range(1, 91), stated=True,
                      sold=True)
        assert not _lots(range(1, 91)) & n.retired()
    assert _lots(range(91, 121)) <= n.retired(), sorted(_lots(range(91, 121)) - n.retired())


from tests.test_eagent_stocklist import (FakeSite, make_records, _source,  # noqa: E402,F401
                                         creds, site_factory)
from sources import eagent_stocklist as es                                  # noqa: E402


def test_4_reader_a_page_that_fails_mid_walk_is_short(creds, site_factory, tmp_path,
                                                      monkeypatch):
    """The reader: pages count from 1 with a stated total, and page 3 fails. The walk
    stops at 50 of 83 -- 'short', with the total stated, where e7fc7ea said nothing but a
    log warning."""
    site = site_factory(make_records(83), paging="page1", page_size=25)
    real = es.EAgentStocklistSource._page

    def failing(self, path, params):
        if str(params.get("page")) == "3":
            self._page_failures += 1
            return None, None
        return real(self, path, params)
    monkeypatch.setattr(es.EAgentStocklistSource, "_page", failing)
    src = _source(site, tmp_path)
    rows = src.search({})
    assert src.state == "ok" and len(rows) == 50, (src.state, len(rows))
    assert src.total_stated == 83
    assert src.short_why.startswith("50 of the 83 record(s)"), src.short_why
    assert not src.truncated


def test_4_reader_a_failed_page_with_no_total_is_short(creds, site_factory, tmp_path,
                                                       monkeypatch):
    """Pages counted from 0 and no total stated: a page that fails ends the walk, and the
    reader says so."""
    site = site_factory(make_records(83), paging="page0", page_size=20)
    real = es.EAgentStocklistSource._page

    def failing(self, path, params):
        if str(params.get("page")) == "3":
            self._page_failures += 1
            return None, None
        return real(self, path, params)
    monkeypatch.setattr(es.EAgentStocklistSource, "_page", failing)
    src = _source(site, tmp_path)
    src.search({})
    assert src.total_stated is None
    assert "page fetch(es) failed" in src.short_why, src.short_why


def test_4_reader_a_server_that_ignores_paging_is_short(creds, site_factory, tmp_path):
    """A server that states 83 records and a page size of 20, and answers page 1 whatever
    is asked: one full page, short of its stated total."""
    recs = make_records(83)
    site = site_factory(recs, paging="page1", page_size=20)
    site.page = lambda q, path: {"ok": True, "data": recs[:20], "total": 83, "limit": 20}
    src = _source(site, tmp_path)
    rows = src.search({})
    assert len(rows) == 20
    assert src.short_why.startswith("20 of the 83"), src.short_why


@pytest.mark.parametrize("paging", ["page1", "all"])
def test_twin_reader_a_whole_walk_is_not_short(creds, site_factory, tmp_path, paging):
    """The twin: a walk read to its stated total (page1) is not short and states its total;
    a server answering everything at once with no size stated (all) is not short either --
    and, stating no total, proves nothing."""
    site = site_factory(make_records(83), paging=paging, page_size=20)
    src = _source(site, tmp_path)
    assert len(src.search({})) == 83
    assert src.short_why == ""
    assert src.total_stated == (83 if paging == "page1" else None)


def test_4_the_harvest_writes_the_stated_total_and_why_it_is_short(monkeypatch, tmp_path):
    """harvest_buildings' search line carries the reader's stated total, the records read
    and its short flag with the reason -- what list_ledger.Tonight._search judges."""
    import config
    import harvest_buildings as hb
    from database import ResearchDatabase
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "h.db")
    monkeypatch.setattr(hb, "ResearchDatabase",
                        lambda *a, **k: ResearchDatabase(db_path=tmp_path / "h.db"))
    rows = [es.map_record(r, "https://x", "https://x/api/p")[0] for r in make_records(25)]
    for r in rows:
        r.pop("_state_stated", None)

    class Short:
        channel_name = es.CHANNEL
        configured = True
        signed_in = True
        state, why, truncated = "ok", "25 of 25 rows priced and placed", False
        total_stated, records_seen = 40, 25
        short_why = "25 of the 40 record(s) the site states"

        def __init__(self, *a, **k):
            pass

        def search(self, filters):
            return [dict(r) for r in rows]

    monkeypatch.setattr(hb, "EAgentStocklistSource", Short)
    hb.harvest(eagent=False, portals=False, email=False, proxima=False, rea=False,
               eagent_stocklist=True)
    mine = [e for e in harvest_manifest.ENTRIES if e["channel"] == es.CHANNEL]
    assert len(mine) == 1, harvest_manifest.ENTRIES
    assert mine[0]["short"] is True and mine[0]["stated"] == 40 and \
        mine[0]["records"] == 25 and mine[0]["short_why"].startswith("25 of the 40")


# ------------------------------------------------------------------ 5. cuts under the step bar

def _ratchet_cut_night(n, night, total, keep, na=(), doc="https://docs.example/ratchet-90"):
    """A list main holds partial: `total` lots for sale (`na` of them 'Not Available' on
    every read) and as many rows the sheet said were Sold 20 days before night 1. From
    night 4 the read silently keeps lots 1..keep."""
    carried = range(1, (total if night < 4 else keep) + 1)
    lots = [_lot(i, night, doc=doc, builder="Ratchet Homes",
                 status="Not Available" if i in na else "Available") for i in carried]
    if night == 1:
        lots += [_lot(i, -20, doc=doc, builder="Ratchet Homes", status="Sold")
                 for i in range(1001, 1001 + total)]
    n.run(night, lots)


@pytest.mark.parametrize("total", [34, 42, 114])
def test_5_a_ninety_percent_cut_on_a_list_main_holds_partial_retires_nothing(
        tmp_path, monkeypatch, total):
    """A silent cut keeping 90% of the read for 20 nights drops floor(10% of n) lots -- 3 of
    34, 4 of 42, 11 of 114 -- one short of max(2, 10%) rounded up, so on e7fc7ea the read
    was 'history N of M', whole, and the cut lots retired on night 17. Main holds these
    lists partial and retires none."""
    n = Nights(tmp_path, monkeypatch)
    keep = total - total // 10
    lost = _lots(range(keep + 1, total + 1))
    for night in range(1, 24):
        _ratchet_cut_night(n, night, total, keep)
        assert not lost & n.retired(), _say(n, night, lost)
    last = [r for r in _ledger_reads(n.path) if r["rows_seen"] == keep][-1]
    assert last["verdict"] == "partial" and "the old rule holds this list partial" in \
        last["basis"], last


def test_5_luxton_cut_to_eight_with_a_not_available_row_retires_nothing(tmp_path,
                                                                        monkeypatch):
    """Luxton's 10-row tab on 30 Sep, cut to 8: of the 2 cut rows one (lot 10) says 'Not
    Available', so only 1 dropped row was counted, under the bar of 2 -- 'history 8 of 10',
    then 'a 8-row list is not judged by a share' -- and lot 9, Available, retired on night
    17. The row that says gone now counts toward the drop when a lot for sale went with
    it."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 24):
        _ratchet_cut_night(n, night, 10, 8, na={10}, doc="https://docs.example/luxton")
        assert "9" not in n.retired(), _say(n, night, {"9"})


def test_5_a_cut_judged_before_main_gave_up_on_the_list_waits(tmp_path, monkeypatch):
    """Beyond the review: the strict bar is judged per night. 30 lots for sale and 9 'Sold'
    rows; on night 4 the read drops lots 29-30 at once -- 2 of 30, under max(2, 10%) = 3,
    and main still trusts the list (11 of 39 missing), so the read is whole. Then a lot a
    night sells, and from night 9 main holds the list partial. On night 17, when 29-30 have
    been gone a fortnight, main retires nothing; on e7fc7ea rule A retired both. They left
    on one read, on a list main now holds partial: they wait. The lots that left one at a
    time retire."""
    n = Nights(tmp_path, monkeypatch)
    doc = "https://docs.example/borderline"
    for night in range(1, 27):
        top = 30 if night < 4 else 28 if night == 4 else max(22, 28 - (night - 4))
        lots = [_lot(i, night, doc=doc, builder="Edge Homes", status="Available")
                for i in range(1, top + 1)]
        if night == 1:
            lots += [_lot(i, -20, doc=doc, builder="Edge Homes", status="Sold")
                     for i in range(101, 110)]
        n.run(night, lots)
        assert not {"29", "30"} & n.retired(), _say(n, night, {"29", "30"})
        assert not _lots(range(1, 23)) & n.retired()
    assert _lots(range(23, 29)) <= n.retired(), sorted(_lots(range(23, 29)) - n.retired())


def test_twin_without_the_check_at_retirement_the_borderline_cut_retires(tmp_path,
                                                                        monkeypatch):
    """The twin: with the check at retirement off (repair_rows._left_together), the same
    nights retire lots 29-30 -- the read was whole on the night they left."""
    import repair_rows as RR
    monkeypatch.setattr(RR, "_left_together", lambda *a, **k: frozenset())
    n = Nights(tmp_path, monkeypatch)
    doc = "https://docs.example/borderline"
    for night in range(1, 20):
        top = 30 if night < 4 else 28 if night == 4 else max(22, 28 - (night - 4))
        lots = [_lot(i, night, doc=doc, builder="Edge Homes", status="Available")
                for i in range(1, top + 1)]
        if night == 1:
            lots += [_lot(i, -20, doc=doc, builder="Edge Homes", status="Sold")
                     for i in range(101, 110)]
        n.run(night, lots)
    assert {"29", "30"} <= n.retired()


def test_twin_without_the_strict_bar_the_ninety_percent_cut_retires(tmp_path, monkeypatch):
    """The twin: with no list counted as one main holds partial, the same 34-row cut
    retires its 3 lots, as on e7fc7ea -- the strict bar is what keeps them."""
    import repair_rows as RR
    monkeypatch.setattr(RR, "main_holds_ids", lambda rows, today: frozenset())
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 24):
        _ratchet_cut_night(n, night, 34, 31)
    assert _lots(range(32, 35)) <= n.retired()


def test_twin_a_ratchet_list_that_stops_showing_its_sold_rows_still_retires_a_sale(
        tmp_path, monkeypatch):
    """The twin: a Proxima project shows 20 'Not Available' lots on night 1 and stops
    showing them on night 2 -- nothing for sale dropped with them, so that is no step --
    and from night 4 one lot sells. Main holds the project partial for good; the lot that
    sold retires a fortnight after it left."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 22):
        carried = range(1, 13) if night < 4 else range(1, 12)
        lots = [_proxima(i, night, pid="53", status="Available") for i in carried]
        if night == 1:
            lots += [_proxima(i, 1, pid="53", status="Not Available")
                     for i in range(100, 120)]
        n.run(night, lots, entries=[_project_entry("53", len(lots), whole="uncapped")])
        assert not _lots(range(1, 12)) & n.retired()
    assert "12" in n.retired()


def test_5_a_sale_that_drops_with_rows_that_say_gone_waits(tmp_path, monkeypatch):
    """The safety side of the twin above: on night 2 the read loses lot 6 (Available) and
    20 'Not Available' rows at once -- 26 rows read, then 5. On a list main holds partial
    that is a step (21 of 26), whatever the gone rows say: lot 6 waits for a proof or a
    person. Main never retires it either."""
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 20):
        carried = range(1, 7) if night == 1 else range(1, 6)
        lots = [_proxima(i, night, pid="53", status="Available") for i in carried]
        if night == 1:
            lots += [_proxima(i, 1, pid="53", status="Not Available")
                     for i in range(100, 120)]
        n.run(night, lots, entries=[_project_entry("53", len(carried), whole="uncapped")])
        assert "6" not in n.retired(), f"night {night}"


def test_5_a_staircase_cut_on_a_proxima_project_main_holds_partial(tmp_path, monkeypatch):
    """The reviewer's 4a (non-blocking there, closed by the strict bar): project 53, no
    counter, 60 lots for sale and 50 'Not Available' rows; from night 4 the read loses 4
    lots a night for 5 nights and stays at 40. On e7fc7ea all 20 retired."""
    n = Nights(tmp_path, monkeypatch)
    lost = _lots(range(41, 61))
    for night in range(1, 30):
        top = 60 if night < 4 else max(40, 60 - 4 * (night - 3))
        lots, entries = [], []
        for p in range(200, 220):
            lots += [_lot(p * 10 + i, night, channel="Proxima", builder=f"Developer {p}",
                          doc="https://proxima.example/projects", source_project_id=str(p))
                     for i in (1, 2)]
            entries.append(_project_entry(str(p), 2, stated=2, whole="uncapped"))
        lots += [_proxima(i, night, pid="53", status="Available") for i in range(1, top + 1)]
        if night == 1:
            lots += [_proxima(i, 1, pid="53", status="Not Available") for i in range(101, 151)]
        entries.append(_project_entry("53", top + (50 if night == 1 else 0),
                                      whole="uncapped"))
        n.run(night, lots, entries=entries, runs=_px_run(len(entries)))
        assert not lost & n.retired(), _say(n, night, lost)


def test_5_a_second_design_cut_is_neither_set_aside_nor_hidden(tmp_path, monkeypatch):
    """The reviewer's 4c (non-blocking there): a list offering two designs on each of 20
    lots (40 packages) and 30 'Sold' rows. From night 4 the read silently loses design B of
    lots 11-20. On e7fc7ea those 10 rows were matched one-for-one to their own design-A
    siblings -- listed beside them for nights -- set aside from every count, and hidden as
    'older identity, not sold': 10 packages still for sale no longer offered, while main
    kept them live. A new identity must have first appeared after the row was last seen."""
    n = Nights(tmp_path, monkeypatch)
    doc = "https://docs.example/two-designs"
    for night in range(1, 26):
        lots = []
        for i in range(1, 21):
            lots.append(_lot(i, night, doc=doc, builder="Design Homes", house_design="Alpha",
                             source_text=f"Lot {i} Alpha $600,000", status="Available"))
            if night < 4 or i <= 10:
                lots.append(_lot(i, night, doc=doc, builder="Design Homes",
                                 house_design="Beta", source_text=f"Lot {i} Beta $650,000",
                                 status="Available"))
        if night == 1:
            lots += [_lot(i, -20, doc=doc, builder="Design Homes", status="Sold")
                     for i in range(101, 131)]
        n.run(night, lots)
    con = sqlite3.connect(str(n.path))
    beta = con.execute("SELECT lot_number, superseded_by FROM buildings WHERE source_text "
                       "LIKE '%Beta%' AND CAST(lot_number AS INTEGER) BETWEEN 11 AND 20"
                       ).fetchall()
    con.close()
    assert len(beta) == 10 and not [lot for lot, mark in beta if mark], beta


def test_5_a_staircase_cut_on_an_e_agent_list_main_holds_partial(tmp_path, monkeypatch):
    """The reviewer's 4b2: 60 lots for sale and 50 'Sold' rows; from night 4 the read loses
    4 lots a night for 5 nights ('history 40 of 40' on e7fc7ea, 20 retired)."""
    n = Nights(tmp_path, monkeypatch)
    lost = _lots(range(41, 61))
    doc = "https://docs.example/ratchet-stair"
    for night in range(1, 30):
        top = 60 if night < 4 else max(40, 60 - 4 * (night - 3))
        lots = [_lot(i, night, doc=doc, builder="Ratchet Homes", status="Available")
                for i in range(1, top + 1)]
        if night == 1:
            lots += [_lot(i, -20, doc=doc, builder="Ratchet Homes", status="Sold")
                     for i in range(101, 151)]
        n.run(night, lots)
        assert not lost & n.retired(), _say(n, night, lost)
