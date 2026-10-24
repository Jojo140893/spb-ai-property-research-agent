"""The second adversarial review of the retirement ledger (24 Oct 2026, on f7bed38): four
ways a read that was cut short, or a copy that was stale, still retired -- or offered --
a lot, each through the real nightly chain (the harness of
tests/test_retirement_multinight.py: record_building, supersede_duplicates, repair_rows
--apply, one call per night). Main 1858d55 does none of them.

  1. A SILENT CUT ON A RE-UPLOAD (rule B). E-Agent re-uploads a list under a new link and
     the new link's read carries only part of it, cleanly: outcome read, nothing failed.
     The old link's unit left the page, so its stranded lots counted as "no longer
     offered" and retired after 14 nights -- the 40% rule that holds the same cut on the
     same link never saw it. A unit stranded before the ledger began was gated only by
     "its builder has a list read whole tonight", which the cut re-upload itself was.
  2. A SOLD LOT CAME BACK THROUGH A STALE SECOND COPY. supersede un-hid a duplicate loser
     read after the retired winner's last SIGHTING, not after its retirement: an email
     that carried the lot a day after its list dropped it went live the night after the
     list copy retired, and the inbox never retires by absence.
  3. TWO CHANNELS AGREEING ON PAGE ONE. A pooled portal list was proven whole when its
     read and an E-Agent link on the same host agreed -- which both do when the page
     paginates and neither reader follows it.
  4. A SILENT CUT ON A LIST THE OLD RULE HOLDS PARTIAL. The ledger trusts lists the old
     rule gave up on (the ratchet), by history or by the accountable anchor. A cut the
     manifest says nothing about that keeps 60%+ of the read walked the history baseline
     down in a week (or was under the anchor's 40%, or left a list under 10 rows "not
     judged by a share"), and the cut lots retired while still for sale.

The verifier's tests are here as written (A, A2, A3, G, R), with the ratchet attack as a
multi-night test, and the twins that show each guard is what holds. The repair (25 Oct)
added the same holes one step further, each failing on f7bed38 and on the first repair's
unfinished edits: an email the nightly's 7-day inbox sweep reads again after the list
copy retired (R2), two channels agreeing on a page that stopped loading at 70% (G4), a
cut on a list the ledger never read whole (vetoed on its first nights), and the read a
step is measured from pruned away.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

from database import WITHDRAWN_FROM_SOURCE
from tests.test_retirement_multinight import (Nights, _lot, _proxima, _project_entry,
                                              _page, _eagent_night, _link, _px_run,
                                              _portal_entry, _ledger_reads, PORTAL)


def _say(n, night, want):
    got = sorted(want & n.retired(), key=lambda s: (len(s), s))
    return f"night {night}: retired {len(got)} for-sale lot(s): {got[:12]}"


# ------------------------------------------------------------------ 1. a cut read on a re-upload

def _reupload_cut(n, night, keep, total=30, cut_from=6, label="Price List",
                  heading="Builder 0"):
    """Link 0 carries lots 1..total. From `cut_from` E-Agent re-uploads it under a new link
    (in the same place unless `label` or `heading` says otherwise), and tonight's read of
    the new link yields only lots 1..keep -- outcome read, nothing failed, nothing refused:
    a silent cut (a parser that stops at page 2 of a re-laid-out PDF). Eleven other links
    read whole."""
    links = [_page(k) for k in range(1, 12)]
    old = ("https://files.example/link-0", "Builder 0", list(range(1, total + 1)),
           "Price List")
    new = ("https://files.example/link-0-reuploaded", "Builder 0", list(range(1, keep + 1)),
           label)
    if night < cut_from:
        _eagent_night(n, night, [old] + links)
        return
    lots = [_lot(i, night, doc=new[0], builder=new[1]) for i in new[2]]
    entry = _link(new[0], len(new[2]), heading=heading, label=label)
    _eagent_night(n, night, links, extra_lots=lots, extra_entries=[entry])


@pytest.mark.parametrize("keep", [10, 15])
def test_A_a_silent_cut_on_a_reuploaded_list_retires_nothing(tmp_path, monkeypatch, keep):
    """30 lots; the re-upload's read carries 10 (or 15) of them. On the SAME link that read
    is 66% (50%) missing -- partial by the 40% rule, on main and on the branch alike. On a
    new link the 20 (15) lots it lost sit under the old link, which left the page: main
    never retires them (their list is stale). They are still for sale."""
    n = Nights(tmp_path, monkeypatch)
    lost = {str(i) for i in range(keep + 1, 31)}
    for night in range(1, 36):
        _reupload_cut(n, night, keep)
        assert not lost & n.retired(), _say(n, night, lost)
    assert "its successor on the page carries under 60% of it" in n.out, n.out[-1500:]


@pytest.mark.parametrize("label, heading", [("Price List Oct", "Builder 0"),
                                            ("Price List", "Builder 0 Homes")])
def test_A4_the_same_cut_under_a_new_label_or_heading_retires_nothing(tmp_path, monkeypatch,
                                                                     label, heading):
    """The same 10-of-30 cut, but the re-upload sits under a new label or a new heading,
    so it does not hold the old link's place. Its rows say where they came from: the lots
    it carries were stored under the old link, so it is the old link's successor."""
    n = Nights(tmp_path, monkeypatch)
    lost = {str(i) for i in range(11, 31)}
    for night in range(1, 36):
        _reupload_cut(n, night, 10, label=label, heading=heading)
        assert not lost & n.retired(), _say(n, night, lost)


def test_A5_a_second_reupload_under_a_new_label_retires_nothing(tmp_path, monkeypatch):
    """Beyond the review: link A carries 30 lots (nights 1-5); its re-upload B, in the same
    place, carries all 30 (nights 6-10); then B is re-uploaded as C under a new label, and
    C's read silently stops at lot 10. The lots C carries were first stored under A, not B,
    so they do not say B is their source -- but C is a link of B's builder that appeared
    when B went, so B has a successor, and it carries none of B's rows that say so."""
    n = Nights(tmp_path, monkeypatch)
    links = [_page(k) for k in range(1, 12)]
    lost = {str(i) for i in range(11, 31)}
    for night in range(1, 40):
        if night <= 5:
            doc, nums, label = "https://files.example/link-a", range(1, 31), "Price List"
        elif night <= 10:
            doc, nums, label = "https://files.example/link-b", range(1, 31), "Price List"
        else:
            doc, nums, label = "https://files.example/link-c", range(1, 11), "Price List Nov"
        lots = [_lot(i, night, doc=doc, builder="Builder 0") for i in nums]
        _eagent_night(n, night, links, extra_lots=lots,
                      extra_entries=[_link(doc, len(lots), heading="Builder 0", label=label)])
        assert not lost & n.retired(), _say(n, night, lost)


@pytest.mark.parametrize("keep, retires", [(10, False), (28, True)])
def test_A6_a_file_renamed_inside_its_folder_and_cut(tmp_path, monkeypatch, keep, retires):
    """Beyond the review, A3 through _plan_files itself: one Dropbox folder link, read clean
    every night. Its file f.pdf (30 lots) is replaced by g.pdf, whose read silently stops at
    lot `keep`. f.pdf is a file the clean folder no longer serves -- but g.pdf is new to the
    folder and the lots it carries were stored before it appeared: it carries 10 of 30, and
    nothing f.pdf left behind may retire. Twin: g.pdf carries 28, and lots 29-30 retire 14
    nights after f.pdf went."""
    n = Nights(tmp_path, monkeypatch)
    links = [_page(k) for k in range(1, 12)]
    folder = "https://files.example/folder"
    dropped = {str(i) for i in range(keep + 1, 31)}
    for night in range(1, 30):
        name, nums = ("f.pdf", range(1, 31)) if night <= 5 else ("g.pdf", range(1, keep + 1))
        lots = [_lot(i, night, doc=f"{folder}/{name}", stocklist_file=folder,
                     builder="Builder F") for i in nums]
        entry = _link(folder, len(lots), heading="Builder F", pieces=[
            {"name": name, "outcome": "read", "printed": len(lots), "why": ""}])
        _eagent_night(n, night, links, extra_lots=lots, extra_entries=[entry])
        assert not {str(i) for i in range(1, keep + 1)} & n.retired()
        if not retires:
            assert not dropped & n.retired(), _say(n, night, dropped)
    if retires:
        assert dropped <= n.retired(), sorted(dropped - n.retired())


def test_twin_a_reupload_carrying_28_of_30_retires_the_two_it_dropped(tmp_path, monkeypatch):
    """The twin (with test_a_reuploaded_list_neither_resurrects_nor_strands_its_rows, 2 of
    3): a re-upload that carries most of its list says the other lots are gone, and they
    retire 14 counted nights after the old link left. The 28 never do."""
    n = Nights(tmp_path, monkeypatch)
    kept = {str(i) for i in range(1, 29)}
    for night in range(1, 24):
        _reupload_cut(n, night, 28)
        assert not kept & n.retired(), _say(n, night, kept)
        if night == 18:
            assert not {"29", "30"} & n.retired(), "retired before 14 counted nights"
    assert {"29", "30"} <= n.retired()


def test_A2_stranded_before_the_ledger_began(tmp_path, monkeypatch):
    """The re-upload happened BEFORE the ledger's first night (the 201 + 35 stale documents
    of 24 Oct): link 0's 30 lots were stored 20 days ago; its re-upload (same place) is on
    the page every night and its read silently carries lots 1-10 only. Lots 11-30 are still
    for sale. The unit was 'never offered since the ledger began', so no slot, page or
    heading check applies: only 'its builder has a list read whole tonight' -- which the
    cut re-upload itself is (10 of 10)."""
    n = Nights(tmp_path, monkeypatch)
    lost = {str(i) for i in range(11, 31)}
    links = [_page(k) for k in range(1, 12)]
    new = ("https://files.example/link-0-reuploaded", "Builder 0", list(range(1, 11)),
           "Price List")
    for night in range(1, 30):
        extra = [_lot(i, -20, doc="https://files.example/link-0", builder="Builder 0")
                 for i in range(1, 31)] if night == 1 else []
        _eagent_night(n, night, [new] + links, extra_lots=extra)
        assert not lost & n.retired(), _say(n, night, lost)


@pytest.mark.parametrize("moved", [True, False])
def test_A2b_a_builder_mostly_on_lists_no_longer_offered_waits(tmp_path, monkeypatch, moved):
    """Creation Homes on 30 Sep: 116 live rows on links no longer offered against 51 read.
    A stranded unit whose builder's rows are 40% or more on lists no longer offered waits
    for a proof -- whether or not its rows say they moved (`moved`: the lots on the new
    list were stored under the old link). Twin below: a builder read whole almost
    everywhere."""
    n = Nights(tmp_path, monkeypatch)
    lost = {str(i) for i in range(31, 51)}
    links = [_page(k) for k in range(1, 12)]
    new = ("https://files.example/builder-0-current", "Builder 0", list(range(1, 31)),
           "Price List")
    for night in range(1, 30):
        extra = []
        if night == 1:
            extra = [_lot(i, -20, doc="https://files.example/builder-0-old",
                          builder="Builder 0") for i in range(31, 51)]
            if moved:
                extra += [_lot(i, -20, doc="https://files.example/builder-0-old",
                               builder="Builder 0") for i in range(1, 11)]
        _eagent_night(n, night, [new] + links, extra_lots=extra)
        assert not lost & n.retired(), _say(n, night, lost)


def test_twin_a_stranded_link_of_a_builder_read_whole_elsewhere_retires(tmp_path,
                                                                        monkeypatch):
    """The twin: Builder 0's current list (30 lots) reads whole every night, and a link of
    its taken down before the ledger began left 2 lots behind -- 2 of 32, under 40%. They
    count from night 4 (the channel needs three recorded nights) and retire on night 17."""
    n = Nights(tmp_path, monkeypatch)
    links = [_page(k) for k in range(1, 12)]
    new = ("https://files.example/builder-0-current", "Builder 0", list(range(1, 31)),
           "Price List")
    for night in range(1, 20):
        extra = [_lot(i, -20, doc="https://files.example/builder-0-old", builder="Builder 0")
                 for i in (31, 32)] if night == 1 else []
        _eagent_night(n, night, [new] + links, extra_lots=extra)
        assert not {str(i) for i in range(1, 31)} & n.retired()
    assert {"31", "32"} <= n.retired()


def test_A3_reupload_cut_through_a_dropbox_folder(tmp_path, monkeypatch):
    """The re-upload is a Dropbox folder link whose old file (30 lots) and new file (same
    lots, new name) sit under different links: the new file's read stops at lot 10. Same
    outcome as A, through _plan_files' cousin path: the old link's unit leaves the page."""
    n = Nights(tmp_path, monkeypatch)
    lost = {str(i) for i in range(11, 31)}
    links = [_page(k) for k in range(1, 12)]
    old_folder, new_folder = "https://files.example/folder-a", "https://files.example/folder-b"
    for night in range(1, 36):
        if night < 6:
            lots = [_lot(i, night, doc=old_folder + "/f.pdf", stocklist_file=old_folder,
                         builder="Builder 0") for i in range(1, 31)]
            e = _link(old_folder, 30, heading="Builder 0")
        else:
            lots = [_lot(i, night, doc=new_folder + "/f.pdf", stocklist_file=new_folder,
                         builder="Builder 0") for i in range(1, 11)]
            e = _link(new_folder, 10, heading="Builder 0")
        _eagent_night(n, night, links, extra_lots=lots, extra_entries=[e])
        assert not lost & n.retired(), _say(n, night, lost)


# ------------------------------------------------------------------ 2. a stale second copy

def _copies(n, lot):
    con = sqlite3.connect(str(n.path))
    con.row_factory = sqlite3.Row
    out = [dict(r) for r in con.execute(
        "SELECT id, source_channel, superseded_by, availability_status, date_checked "
        "FROM buildings WHERE lot_number=?", (lot,))]
    con.close()
    return out


def _second_copy_nights(n, second_on, nights=41, reads=1, channel="digital email",
                        doc="inbox"):
    """Lot 7 is on list A (a builder sheet, well described: suburb, state, spec) nights 1-9
    and leaves it on night 10 -- sold. On `second_on` the same line arrives through
    `channel` (the inbox), less well described, so supersede makes it the duplicate loser
    of list A's copy, and it is read on `reads` nights from then (the nightly sweeps the
    inbox's last 7 days: an email is read again on each of them). List A's copy retires on
    night 23. Yields each night after the run."""
    line = "Lot 7 Banksia Rise 4 bed $707,000"
    rich = dict(source_text=line, suburb="Tarneit", state="VIC", bedrooms=4, bathrooms=2,
                car_spaces=2, land_sqm=400, house_sqm=200, advertised_package_price=707000)
    for night in range(1, nights):
        lots = [_lot(i, night, status="Available") for i in range(1, 13) if i != 7]
        if night < 10:
            lots.append(_lot(7, night, status="Available", **rich))
        if second_on <= night < second_on + reads:
            lots.append(_lot(7, night, channel=channel, doc=doc,
                             source_text=line, advertised_package_price=707000))
        n.run(night, lots)
        yield night


def _live_copies(n):
    return [c for c in _copies(n, "7") if c["superseded_by"] is None
            and str(c["availability_status"] or "").lower() not in ("sold",)]


def test_R_a_sold_lot_comes_back_through_a_stale_second_copy(tmp_path, monkeypatch):
    """The verifier's R [digital email]: the inbox copy came on night 11, a day after list
    A stopped carrying the lot and twelve nights before list A's copy retired. It is no
    evidence the lot is for sale since: no copy of lot 7 may be live and offerable after
    night 23. Main keeps the inbox copy hidden behind list A's every night."""
    n = Nights(tmp_path, monkeypatch)
    first_live = None
    for night in _second_copy_nights(n, second_on=11):
        if night >= 24 and first_live is None and _live_copies(n):
            first_live = (night, _live_copies(n))
    assert first_live is None, f"a sold lot is offered again: {first_live}"
    retired = [c for c in _copies(n, "7") if c["source_channel"] == "E-Agent"]
    assert retired and retired[0]["availability_status"] == "Sold", retired


def test_R2_an_email_read_again_after_the_retirement_is_no_listing(tmp_path, monkeypatch):
    """Beyond the review, the same hole through the inbox sweep: the email carrying lot 7
    arrives on night 20, eleven nights after list A dropped the lot, and the nightly's
    7-day sweep reads it again on nights 20-26. List A's copy retires on night 23, so the
    email's copy is "read" on nights 24-26, after the retirement -- but nothing new was
    sent: it is the same email. No copy of lot 7 may be live and offerable after night 23.
    Main keeps the email's copy hidden behind list A's every night."""
    n = Nights(tmp_path, monkeypatch)
    first_live = None
    for night in _second_copy_nights(n, second_on=20, reads=7):
        if night >= 24 and first_live is None and _live_copies(n):
            first_live = (night, _live_copies(n))
    assert first_live is None, f"a sold lot is offered again: {first_live}"
    retired = [c for c in _copies(n, "7") if c["source_channel"] == "E-Agent"]
    assert retired and retired[0]["availability_status"] == "Sold", retired
    emailed = [c for c in _copies(n, "7") if c["source_channel"] == "digital email"]
    assert emailed and emailed[0]["date_checked"] == "27/09/2026", \
        "the harness meant the email to be read after the retirement"


def test_twin_a_copy_read_after_the_retirement_is_live(tmp_path, monkeypatch):
    """The twin: the builder's portal -- a re-listing source, which lists its whole stock
    on every read -- carries lot 7 on night 25, two nights AFTER list A's copy retired. A
    source has listed it since it retired, so the retired copy does not hide it
    (test_a_lot_read_tonight_is_never_hidden_behind_a_retired_copy, through the chain)."""
    n = Nights(tmp_path, monkeypatch)
    for night in _second_copy_nights(n, second_on=25, nights=28, reads=3, channel=PORTAL,
                                     doc="https://portal.example/lot/7"):
        if night == 24:
            assert not _live_copies(n)
        if night >= 25:
            live = _live_copies(n)
            assert [c["source_channel"] for c in live] == [PORTAL], (night, live)


def test_the_relisting_channels_are_repair_rows():
    """supersede_duplicates' re-listing channels -- the reads that say a lot is listed
    tonight -- are repair_rows': the channels whose lists retire a lot by absence."""
    import repair_rows as RR
    import supersede_duplicates as SD
    assert SD.RELISTING_CHANNELS == RR.RELISTING_CHANNELS


def test_the_retirement_bound_is_repair_rows_absence():
    """Without a stamp, a retirement is dated no earlier than its copy's last sighting plus
    repair_rows.WITHDRAWN_AFTER_DAYS: no rule retires a lot sooner."""
    import repair_rows as RR
    import supersede_duplicates as SD
    assert SD.RETIRED_AFTER_DAYS == RR.WITHDRAWN_AFTER_DAYS


# ------------------------------------------------------------------ 3. two channels, one page

@pytest.mark.parametrize("page_len", [24, 50])
def test_G_two_channels_agree_on_page_one(tmp_path, monkeypatch, page_len):
    """TORSION-like: one Stocklist page that E-Agent also links, read by both channels.
    From night 4 the page paginates (or lazy-loads) and NEITHER reader follows it: both
    read page 1, and their counts agree exactly -- 'two channels agree' proved the read
    whole. The other lots are still for sale. Main: 40% rule, partial, nothing retires.
    Twin: test_two_channels_reading_one_page_prove_the_pooled_list."""
    n = Nights(tmp_path, monkeypatch)
    host = "referrer.torsion.example"
    page = f"https://{host}/stocklist"
    kw = {"channel": PORTAL, "builder": "Torsion Homes"}
    total = 120
    rest = {str(i) for i in range(page_len + 1, total + 1)}
    for night in range(1, 36):
        carried = range(1, total + 1) if night < 4 else range(1, page_len + 1)
        lots = [_lot(i, night, doc=page, **kw) for i in carried]
        lots += [_lot(1000 + i, night, doc=page, builder="Torsion Homes",
                      source_text=f"E-Agent copy of lot {i}") for i in carried]
        n.run(night, lots, entries=[_portal_entry("Torsion Homes", len(carried), host=host),
                                    _link(page, len(carried), heading="Torsion Homes")])
        assert not rest & n.retired(), _say(n, night, rest)
    portal = [r for r in _ledger_reads(n.path) if r["channel"] == PORTAL]
    assert portal[-1]["verdict"] == "partial" and "two channels agree on" in \
        portal[-1]["basis"], portal[-1]


@pytest.mark.parametrize("per_card", [True, False])
def test_G2_page_one_through_per_card_links(tmp_path, monkeypatch, per_card):
    """The same, as the real Torsion portal stores it: each card under its own link (a
    brochure url per lot), so no portal row names the page. The agreed count is held to
    the list's own whole reads (and to the page's E-Agent rows) instead."""
    n = Nights(tmp_path, monkeypatch)
    host = "referrer.torsion.example"
    page = f"https://{host}/stocklist"
    rest = {str(i) for i in range(25, 121)}
    for night in range(1, 36):
        carried = range(1, 121) if night < 4 else range(1, 25)
        lots = [_lot(i, night, channel=PORTAL, builder="Torsion Homes",
                     doc=(f"https://{host}/brochure?lot={i}" if per_card else page))
                for i in carried]
        lots += [_lot(1000 + i, night, doc=page, builder="Torsion Homes",
                      source_text=f"E-Agent copy of lot {i}") for i in carried]
        n.run(night, lots, entries=[_portal_entry("Torsion Homes", len(carried), host=host),
                                    _link(page, len(carried), heading="Torsion Homes")])
        assert not rest & n.retired(), _say(n, night, rest)


def _lazy_load_night(n, night, seen):
    """Torsion's Stocklist page (120 lots, stored from the page by both channels) read
    whole on nights 1-3; from night 4 it lazy-loads and both readers stop at lot `seen`
    and agree. 60 rows the portal itself listed as "Sold" 20 days ago are stored from the
    same page: on main's 40% rule over every stored row the cut list is a failed read
    ((60 + 36) of 180), so main retires none of lots 85-120."""
    host = "referrer.torsion.example"
    page = f"https://{host}/stocklist"
    kw = {"channel": PORTAL, "builder": "Torsion Homes"}
    carried = range(1, 121) if night < 4 else range(1, seen + 1)
    lots = [_lot(i, night, doc=page, **kw) for i in carried]
    lots += [_lot(1000 + i, night, doc=page, builder="Torsion Homes",
                  source_text=f"E-Agent copy of lot {i}") for i in carried]
    if night == 1:
        lots += [_lot(2000 + i, -20, doc=page, status="Sold", **kw) for i in range(1, 61)]
    n.run(night, lots, entries=[_portal_entry("Torsion Homes", len(carried), host=host),
                                _link(page, len(carried), heading="Torsion Homes")])


def test_G4_two_channels_agree_on_a_page_that_stopped_loading(tmp_path, monkeypatch):
    """Beyond the review: the page stops loading at lot 84 of 120 -- 70%, over every share
    the agreement was held to (60% of the page's stored lots and of its whole reads, half
    its 28-day max). Both readers agree on 84, and that proved the list whole: lots 85-120,
    still for sale, retired on night 17, while main holds the list partial. A count that
    fell by a step since the list's last whole read proves nothing: the step guard decides,
    and it holds the 36."""
    n = Nights(tmp_path, monkeypatch)
    rest = {str(i) for i in range(85, 121)}
    for night in range(1, 30):
        _lazy_load_night(n, night, 84)
        assert not rest & n.retired(), _say(n, night, rest)
    portal = [r for r in _ledger_reads(n.path) if r["channel"] == PORTAL]
    assert portal[-1]["verdict"] == "partial" and "a step of" in portal[-1]["basis"], \
        portal[-1]


def test_twin_without_the_step_guard_the_lazy_load_retires(tmp_path, monkeypatch):
    """The twin: with the step guard off (on the rows and on the agreed count), the same
    nights retire lots 85-120 -- the guard holding is what keeps them."""
    import list_ledger
    monkeypatch.setattr(list_ledger, "STEP_MIN_ROWS", 10 ** 6)
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 30):
        _lazy_load_night(n, night, 84)
    assert {str(i) for i in range(85, 121)} <= n.retired()


def test_G3_page_one_from_the_ledgers_first_night_copies_hidden(tmp_path, monkeypatch):
    """Beyond the review: the cut began before the ledger, so it has no whole read to hold
    the agreed count to; the portal stores each card under its own link; and E-Agent's
    copies of the page are hidden as duplicates of the portal's better-described ones, so
    none is live. The page's stock is still on record -- 120 lots captured from it -- and
    24 agreed is not that page."""
    n = Nights(tmp_path, monkeypatch)
    host = "referrer.torsion.example"
    page = f"https://{host}/stocklist"
    rich = dict(suburb="Tarneit", state="VIC", bedrooms=4, bathrooms=2, car_spaces=2,
                land_sqm=400, house_sqm=200)
    rest = {str(i) for i in range(25, 121)}

    def both(i, night):
        return [_lot(i, night, channel=PORTAL, builder="Torsion Homes",
                     doc=f"https://{host}/brochure?lot={i}", **rich),
                _lot(i, night, doc=page, builder="Torsion Homes")]
    def by_channel(channel):
        # The two channels share lot numbers here, so Nights.rows() (keyed by lot) cannot
        # tell them apart.
        con = sqlite3.connect(str(n.path))
        out = {lot: mark for lot, mark in con.execute(
            "SELECT lot_number, superseded_by FROM buildings WHERE source_channel=? AND "
            "lot_number <> '900'", (channel,))}
        con.close()
        return out
    for night in range(1, 30):
        lots = [r for i in range(1, 25) for r in both(i, night)]
        if night == 1:
            lots += [r for i in range(1, 121) for r in both(i, -20)]
        n.run(night, lots, entries=[_portal_entry("Torsion Homes", 24, host=host),
                                    _link(page, 24, heading="Torsion Homes")])
        gone = {lot for lot, mark in by_channel(PORTAL).items()
                if mark == WITHDRAWN_FROM_SOURCE}
        assert not rest & gone, f"night {night}: retired {len(rest & gone)} for-sale lot(s)"
    eagent = by_channel("E-Agent")
    assert len(eagent) == 120 and all(eagent.values()), \
        "the harness meant E-Agent's copies to be hidden as duplicates"


# ------------------------------------------------------------------ 4. the ratchet's lists, cut

def _ratchet_lists(kind):
    """(lots read whole, rows the source itself says are gone, lots a 70% cut keeps)."""
    if kind == "history":
        return range(1, 31), range(101, 126), range(1, 22)
    if kind in ("small", "luxton"):
        return range(1, 11), range(101, 111), range(1, 8)
    return range(1, 21), range(101, 126), range(1, 15)          # anchor (Proxima)


# Luxton on 30 Sep: one of the ten lots its tab lists says "Not Available" -- the cut of
# three drops it and two lots for sale, 2 of 9.
_NOT_AVAILABLE_ON_THE_READ = {"luxton": {10}}


def _ratchet_cut(kind):
    """The lots for sale a 70% cut drops."""
    whole, _gone, kept = _ratchet_lists(kind)
    return {str(i) for i in whole if i not in kept
            and i not in _NOT_AVAILABLE_ON_THE_READ.get(kind, ())}


def _ratchet_night(n, night, kind, proof=False, sell=0):
    """A list the old rule holds partial for good: besides the lots it lists, rows the
    source itself said were gone ("Sold" on an E-Agent sheet 20 days ago, "Not Available"
    on one Proxima read) are 40%+ of everything stored. The ledger sets them aside, so it
    trusts the list -- by history (E-Agent, 30 lots), by history under 10 rows (E-Agent, 10
    lots: "small", and "luxton", one of whose lots is Not Available) or by the anchor
    (Proxima, no counter: project 701). From night 4 the read silently keeps 70% of the
    list; with `sell`, instead, its top `sell` lots leave one a night from night 4.
    `proof`: Proxima's counter states each read's size."""
    whole, gone, kept = _ratchet_lists(kind)
    if sell:
        top = max(whole) - min(sell, max(0, night - 3))
        carried = [i for i in whole if i <= top]
    else:
        carried = list(kept) if night >= 4 else list(whole)
    if kind == "anchor":
        lots, entries = [], []
        for p in range(200, 220):
            lots += [_lot(p * 10 + i, night, channel="Proxima", builder=f"Developer {p}",
                          doc="https://proxima.example/projects", source_project_id=str(p))
                     for i in (1, 2)]
            entries.append(_project_entry(str(p), 2, stated=2, whole="uncapped"))
        lots += [_proxima(i, night, pid="53", status="Available") for i in carried]
        if night == 1:
            lots += [_proxima(i, 1, pid="53", status="Not Available") for i in gone]
        printed = len(carried) + (len(gone) if night == 1 else 0)
        entries.append(_project_entry("53", printed, whole="uncapped",
                                      stated=printed if proof else None))
        n.run(night, lots, entries=entries, runs=_px_run(len(entries)))
        return
    doc = f"https://docs.example/ratchet-{kind}"
    lots = [_lot(i, night, doc=doc, builder="Ratchet Homes",
                 status="Not Available" if i in _NOT_AVAILABLE_ON_THE_READ.get(kind, ())
                 else "Available") for i in carried]
    if night == 1:
        lots += [_lot(i, -20, doc=doc, builder="Ratchet Homes", status="Sold") for i in gone]
    n.run(night, lots)


KINDS = ["history", "small", "luxton", "anchor"]


@pytest.mark.parametrize("kind", KINDS)
def test_a_silent_cut_on_a_list_the_old_rule_holds_partial_retires_nothing(
        tmp_path, monkeypatch, kind):
    """THE SAFETY RULE on the lists the ledger newly trusts. Main holds each partial (40%+
    of everything stored missing) and retires nothing. A cut that keeps 70% of the read and
    that the manifest says nothing about lasts 16+ nights: none of the cut lots for sale may
    retire. f7bed38 retired them on night 17 (history walked down to the cut in a week; a
    list under 10 rows 'is not judged by a share'; the anchor's 30% is under 40%)."""
    n = Nights(tmp_path, monkeypatch)
    _whole, _gone, kept = _ratchet_lists(kind)
    cut = _ratchet_cut(kind)
    for night in range(1, 25):
        _ratchet_night(n, night, kind)
        assert not cut & n.retired(), _say(n, night, cut)
    reads = [r for r in _ledger_reads(n.path) if r["rows_seen"] == len(kept)]
    assert reads and all(r["verdict"] == "partial" for r in reads), reads[-1:]
    assert "since its last whole read" in reads[-1]["basis"], reads[-1]


@pytest.mark.parametrize("kind", KINDS)
def test_twin_without_the_step_guard_the_cut_lots_retire(tmp_path, monkeypatch, kind):
    """The twin: the step guard switched off, the same nights retire every cut lot -- the
    guard holding is what keeps them, not the harness idling."""
    import list_ledger
    monkeypatch.setattr(list_ledger, "STEP_MIN_ROWS", 10 ** 6)
    n = Nights(tmp_path, monkeypatch)
    cut = _ratchet_cut(kind)
    for night in range(1, 25):
        _ratchet_night(n, night, kind)
    assert cut <= n.retired(), sorted(cut - n.retired())


def test_twin_a_proven_cut_retires(tmp_path, monkeypatch):
    """The twin with a proof: Proxima's own counter states each read's size, so a read
    that drops 6 lots is whole and they retire -- they really sold."""
    n = Nights(tmp_path, monkeypatch)
    _whole, _gone, kept = _ratchet_lists("anchor")
    cut = _ratchet_cut("anchor")
    for night in range(1, 25):
        _ratchet_night(n, night, "anchor", proof=True)
        assert not {str(i) for i in kept} & n.retired()
    assert cut <= n.retired(), sorted(cut - n.retired())


def _vetoed_then_cut_night(n, night):
    """A ratchet list (30 lots for sale, 25 "Sold" rows stored 20 days ago: main holds it
    partial, 25 of 55 missing, and 34 of 55 once cut) whose link had a lot refused by a
    constraint on nights 1-3 -- vetoed, so the ledger has no whole read of it -- and from
    night 4 reads clean but silently keeps 21 of its 30 lots."""
    doc = "https://docs.example/ratchet-vetoed"
    carried = range(1, 31) if night < 4 else range(1, 22)
    lots = [_lot(i, night, doc=doc, builder="Ratchet Homes", status="Available")
            for i in carried]
    if night == 1:
        lots += [_lot(i, -20, doc=doc, builder="Ratchet Homes", status="Sold")
                 for i in range(101, 126)]
    n.run(night, lots, entries=[_link(doc, len(carried), heading="Ratchet Homes",
                                      rejected=1 if night < 4 else 0)])


def test_a_silent_cut_on_a_list_never_read_whole_retires_nothing(tmp_path, monkeypatch):
    """Beyond the review: the step guard measured a drop only from a list's last WHOLE
    read, and this list has none -- every read before the cut was vetoed. So the cut read
    went to the bare anchor, 9 of 30 accountable missing (under 40%), was called whole, and
    lots 22-30, still for sale, retired a fortnight later. Main holds the list partial on
    every night. Measured from the list's first recorded read instead, the cut is a step
    of 9, and nothing retires."""
    n = Nights(tmp_path, monkeypatch)
    cut = {str(i) for i in range(22, 31)}
    for night in range(1, 30):
        _vetoed_then_cut_night(n, night)
        assert not cut & n.retired(), _say(n, night, cut)
    reads = [r for r in _ledger_reads(n.path) if r["rows_seen"] == 21]
    assert reads and all(r["verdict"] == "partial" for r in reads), reads[-1:]
    assert "since its first recorded read (none whole)" in reads[-1]["basis"], reads[-1]


def test_twin_measured_only_from_a_whole_read_the_cut_retires(tmp_path, monkeypatch):
    """The twin: the step guard measuring only from a whole read (as before), the same
    nights retire lots 22-30."""
    import list_ledger

    def whole_only(self, lk, newest):
        whole = [(d, n) for d, n, v, _b in self.reads.get(lk, ())
                 if v == list_ledger.WHOLE and d < newest]
        return max(whole) + ("its last whole read",) if whole else None
    monkeypatch.setattr(list_ledger.Ledger, "step_reference", whole_only)
    n = Nights(tmp_path, monkeypatch)
    for night in range(1, 30):
        _vetoed_then_cut_night(n, night)
    assert {str(i) for i in range(22, 31)} <= n.retired()


def test_the_read_a_step_is_measured_from_is_never_pruned():
    """A cut that lasts longer than PRUNE_NIGHTS must not lose the read it is measured
    against: prune keeps each list's latest whole read, and the first recorded read of a
    list never read whole, however old; everything else older than PRUNE_NIGHTS goes."""
    import list_ledger
    today = datetime(2026, 12, 31)
    con = sqlite3.connect(":memory:")
    list_ledger.ensure_tables(con)

    def read(lk, days_ago, verdict):
        con.execute("INSERT INTO list_reads (list_key, read_date, verdict, basis) "
                    "VALUES (?,?,?,?)", (lk, (today - timedelta(days=days_ago)).strftime(
                        "%Y-%m-%d"), verdict, ""))
    for lk, reads in {"cut": [(200, "whole"), (150, "partial"), (10, "partial")],
                      "never": [(200, "partial"), (150, "partial"), (10, "partial")],
                      "both": [(200, "whole"), (130, "whole"), (125, "partial"),
                               (10, "partial")],
                      "fresh": [(200, "partial"), (100, "whole"), (10, "partial")],
                      "unjudged": [(400, None), (121, None), (10, None)]}.items():
        for days_ago, verdict in reads:
            read(lk, days_ago, verdict)
    list_ledger.prune(con, today)
    kept = {}
    for lk, rd, v in con.execute("SELECT list_key, read_date, verdict FROM list_reads"):
        kept.setdefault(lk, []).append(((today - datetime.strptime(rd, "%Y-%m-%d")).days, v))
    assert sorted(kept["cut"]) == [(10, "partial"), (200, "whole")], kept["cut"]
    assert sorted(kept["never"]) == [(10, "partial"), (200, "partial")], kept["never"]
    assert sorted(kept["both"]) == [(10, "partial"), (130, "whole")], kept["both"]
    assert sorted(kept["fresh"]) == [(10, "partial"), (100, "whole")], kept["fresh"]
    # A NULL verdict never makes the keep-test NULL (which would keep every old read).
    assert sorted(kept["unjudged"]) == [(10, None), (400, None)], kept["unjudged"]


@pytest.mark.parametrize("kind, sell", [("history", 9), ("anchor", 6)])
def test_twin_one_lot_a_night_sold_off_a_ratchet_list_retires(tmp_path, monkeypatch, kind,
                                                              sell):
    """The twin in ordinary selling: one lot a night leaves the same lists from night 4
    (30% of each in the end, under the anchor's 40%). Each night is a step under max(2,
    10%), so the list stays whole and every lot that left retires a fortnight later -- the
    ratchet stays fixed."""
    n = Nights(tmp_path, monkeypatch)
    whole, _gone, _kept = _ratchet_lists(kind)
    top = max(whole)
    left = {str(top - k): 4 + k for k in range(sell)}      # lot -> the night it left
    for night in range(1, 30):
        _ratchet_night(n, night, kind, sell=sell)
        early = {k for k in n.retired() & set(left) if left[k] + 13 > night}
        assert not early, f"night {night}: retired before a fortnight's absence: {early}"
        assert not ({str(i) for i in whole} - set(left)) & n.retired()
    assert set(left) <= n.retired(), sorted(set(left) - n.retired())
