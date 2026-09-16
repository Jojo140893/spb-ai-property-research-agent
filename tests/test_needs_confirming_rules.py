"""Which unverified listings a consultant may be shown to confirm, and which stay hidden.

Colin searched "QLD under $750k" and the stock he could have been shown -- Shape Homes'
Dawn lots in Walloon, $721,900-$725,900 -- was hidden by the 14-day rule, because their
emailed list was last read on 20 Aug. Widening the rule is not the answer: at 30 or 45 days
the three "Windermere Mambourin" lots filed QLD "Available" on 21 Aug come back too, and
the 16 Sep read of the same builder, estate and lot 27102 files them VIC and On Hold.

So an unverified row carries `confirm` (what to check) and `confirm_guard` (None, or the
catalogue's own evidence that it is gone), and an unstated row is admitted to confirm only
from an exceptions-only list whose line prints the whole price. The shapes below are the
17 Sep rows behind Colin's search (ids in the names); everything is frozen at 16/09/2026.
"""
import importlib.util
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
for _p in (str(APP / "api"), str(APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _candidates as C  # noqa: E402
import price_kind  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "make_colin_subset", APP / "tests" / "fixtures" / "make_colin_subset.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

TODAY = datetime(2026, 9, 16)
QLD_750K = {"state": "QLD", "budget_max": 750000, "preferred_spending_cap": 750000}
ADMIT = ("availability_unstated",)


def _row(content_hash, **kw):
    """A listing that clears every gate but the ones a test is about."""
    row = {"content_hash": content_hash, "builder_name": "Prosperity Residential",
           "estate_name": "Windermere Mambourin", "lot_number": "27102",
           "lot_address": "Lot 27102 Windermere", "suburb": "Walloon", "state": "QLD",
           "availability_status": "Available", "price": 741900.0, "bedrooms": 4,
           "bathrooms": 2, "car_spaces": 2, "house_sqm": 180.0,
           "product_type": "House & Land", "source_channel": "E-Agent",
           "source_doc": "doc-windermere", "superseded_by": None,
           "date_checked": "21/08/2026", "price_kind": "package",
           "text_price_max": None, "price_in_text": True,
           "email_list": None, "email_first_seen": None}
    row.update(kw)
    if row.get("text_price_max") is None and row.get("price_in_text"):
        row["text_price_max"] = row["price"]
    return row


def _prepared(rows):
    return C.prepare_rows(rows)


def _guard(row, rows):
    return C.confirm_guard(row, _prepared(rows), TODAY)


def _packages(rows, brief=QLD_750K, **kw):
    return C.build_packages(dict(brief), rows, today=TODAY, **kw)


# ------------------------------------------------------------------ newer captures (#8)

def test_windermere_a_later_capture_on_hold_in_another_state_guards_the_old_one():
    """9568 (QLD, Available, 21/08) and 16024 (VIC, On Hold, 16/09): same builder, estate
    and lot. The state is not in the key, or this pair is never caught."""
    old = _row("v2:d32b17a9")
    new = _row("v2:ec6684bf", state="VIC", suburb="Mambourin",
               availability_status="On Hold", date_checked="16/09/2026")
    rows = [old, new]
    assert _guard(old, rows) == "newer_capture_disagrees"

    packages, counts = _packages(rows)
    (pkg,) = packages
    assert pkg["content_hash"] == "v2:d32b17a9"
    assert pkg["verified"] is False
    assert pkg["confirm"] == ["stale"]
    assert pkg["confirm_guard"] == "newer_capture_disagrees"
    assert counts["confirm_guarded"] == {"newer_capture_disagrees": 1}


def test_a_capture_read_the_same_day_is_not_newer():
    """Dawn's twins (11878 / 14940) were written from one read at 09:46:43 on 20/08."""
    rows = [_row("v2:aec54350", availability_status="Available"),
            _row("v2:c48b4599", availability_status="Sold")]
    assert _guard(rows[0], rows) is None


def test_a_lot_number_on_another_estate_is_another_lot():
    """(builder, lot) alone guarded 213 rows for no reason, e.g. v2:673c4c23 (VIC lot 624)
    by NSW 'The CARLING' lot 624. Blank builders are keyed on their document instead."""
    vic = _row("v2:673c4c23", builder_name="", estate_name="Harpley", lot_number="624",
               state="VIC", suburb="Werribee", source_doc="doc-harpley",
               date_checked="25/08/2026", source_channel="digital email")
    nsw = _row("v2:carling", builder_name="", estate_name="The CARLING", lot_number="624",
               state="NSW", suburb="Box Hill", availability_status="Sold",
               source_doc="doc-carling", date_checked="16/09/2026",
               source_channel="digital email")
    assert C.capture_key(vic) != C.capture_key(nsw)
    assert _guard(vic, [vic, nsw]) is None
    # Same named builder, another estate: v2:06dfc6a6 (VIC lot 118) vs WA Lancelin South.
    a = _row("v2:06dfc6a6", builder_name="Hudson Homes", estate_name="Aurora",
             lot_number="118", date_checked="25/08/2026", source_channel="digital email")
    b = _row("v2:lancelin", builder_name="Hudson Homes", estate_name="Lancelin South",
             lot_number="118", availability_status="Sold", state="WA",
             date_checked="16/09/2026", source_channel="digital email")
    assert _guard(a, [a, b]) is None
    # Blank builder, SAME document and estate: that is the same lot.
    same_doc = dict(nsw, estate_name="Harpley", source_doc="doc-harpley",
                    content_hash="v2:harpley-later")
    assert _guard(vic, [vic, same_doc]) == "newer_capture_disagrees"


def test_a_later_capture_marked_gone_disagrees_at_any_price():
    """A lot re-priced and then put On Hold is still on hold: the 2% band is for
    'newer_capture_exists' only. Aldrich lot 633, Timbarra: Available $608,060 on 10/09,
    Sold at $619,370 on 16/09."""
    old = _row("v2:old-633", date_checked="25/08/2026", source_channel="digital email")
    repriced_gone = _row("v2:gone-633", date_checked="10/09/2026", price=741900.0 * 1.10,
                         availability_status="Sold", source_channel="digital email")
    repriced_live = dict(repriced_gone, content_hash="v2:live-633",
                         availability_status="Available")
    assert _guard(old, [old, repriced_gone]) == "newer_capture_disagrees"
    assert _guard(old, [old, repriced_live]) is None
    fresh = dict(old, content_hash="v2:fresh-633", date_checked="09/09/2026")
    (pkg,), counts = _packages([fresh, repriced_gone])
    assert pkg["verified"] is False and counts["newer_capture_demoted"] == 1


def test_newer_capture_exists_only_at_the_same_price():
    old = _row("v2:old", date_checked="25/08/2026", source_channel="digital email")
    within = _row("v2:within", date_checked="10/09/2026", price=741900.0 * 1.019,
                  source_channel="digital email")
    beyond = _row("v2:beyond", date_checked="10/09/2026", price=741900.0 * 1.05,
                  source_channel="digital email")
    assert _guard(old, [old, within]) == "newer_capture_exists"
    assert _guard(old, [old, beyond]) is None


# ---------------------------------------------------------- documents re-read (#9)

def _doc_rows(doc, day, n, **kw):
    return [_row("v2:%s-%s-%d" % (doc, day.replace("/", ""), i), source_doc=doc,
                 lot_number=str(9000 + i), lot_address="Lot %d, %s %s" % (9000 + i, doc, day),
                 date_checked=day, **kw) for i in range(n)]


def test_an_e_agent_row_its_document_was_fully_reread_without_is_hidden():
    """20 days old, and its document was read in full yesterday without it."""
    row = _row("v2:left-out", source_doc="doc-a", lot_number="1", date_checked="27/08/2026")
    rows = [row] + _doc_rows("doc-a", "15/09/2026", 10)
    assert _guard(row, rows) == "document_reread_without_row"


def test_wacol_15899_its_sheet_was_reread_on_16_sep():
    """No lot number, so no capture can speak for it; the sheet's own read does."""
    wacol = _row("v2:3b141dd8", builder_name="Silkwood Homes", estate_name="Wacol Maplewood",
                 lot_number=None, availability_status=None, state=None,
                 source_doc="aa285df384618124", date_checked="01/09/2026", price=595000.0)
    rows = ([wacol] + _doc_rows("aa285df384618124", "16/09/2026", 51)
            + _doc_rows("aa285df384618124", "14/09/2026", 9)
            + _doc_rows("aa285df384618124", "09/09/2026", 16))
    assert _guard(wacol, rows) == "document_reread_without_row"


def test_a_partial_read_is_not_evidence_the_row_was_dropped():
    """80 rows read on 14/09 and 20 on 15/09: 20 is 25% of the largest recent read."""
    row = _row("v2:maybe", source_doc="doc-p", lot_number="1", date_checked="27/08/2026",
               builder_name="")
    rows = ([row] + _doc_rows("doc-p", "15/09/2026", 20, builder_name="")
            + _doc_rows("doc-p", "14/09/2026", 80, builder_name=""))
    verdict, notes = C.confirm_verdict(row, _prepared(rows), TODAY)
    assert verdict is None
    assert notes == ["document_partially_read"]
    packages, _counts = _packages(rows, {"state": "QLD", "budget_max": 800000})
    pkg = next(p for p in packages if p["content_hash"] == "v2:maybe")
    assert pkg["confirm"] == ["stale", "document_partially_read"]
    assert pkg["confirm_guard"] is None


def test_latest_read_is_full_compares_with_the_last_seven_reads():
    day = [datetime(2026, 9, d) for d in range(1, 17)]
    assert C.latest_read_is_full({day[15]: 20, day[14]: 80}) is False
    assert C.latest_read_is_full({day[15]: 48, day[14]: 80}) is True       # exactly 60%
    assert C.latest_read_is_full({day[15]: 47, day[14]: 80}) is False
    # A big read eight reads ago is outside the window.
    reads = {day[15 - i]: 10 for i in range(7)}
    reads[day[15]] = 20
    reads[day[3]] = 200
    assert C.latest_read_is_full(reads) is True
    assert C.latest_read_is_full({}) is False


def test_a_builder_whose_other_documents_were_just_read():
    row = _row("v2:frd-old", builder_name="FRD Homes", estate_name="Old List",
               source_doc="doc-old", lot_number="5", date_checked="27/08/2026")
    newer = _row("v2:frd-new", builder_name="FRD Homes", estate_name="New List",
                 source_doc="doc-new", lot_number="77", date_checked="15/09/2026")
    assert _guard(row, [row, newer]) == "builder_documents_reread"
    # Not for a blank builder, and not for an emailed list, which is never re-listed.
    blank = dict(row, builder_name="", content_hash="v2:blank")
    assert _guard(blank, [blank, dict(newer, builder_name="")]) is None
    emailed = dict(row, source_channel="digital email", content_hash="v2:emailed")
    assert _guard(emailed, [emailed, dict(newer, source_channel="digital email")]) is None


def test_a_newer_emailed_list_from_the_same_sender():
    """Today nothing could ever retire Dawn: its list is the only Shape Homes list. A
    later list with the same subject stem, read after the row, does."""
    shape = C.email_list_key("email:Fwd: Shape Homes - Stock List 13.08.2026")
    assert shape and shape == C.email_list_key("email:Shape Homes - Stock List 10.09.2026")
    dawn = _row("v2:aec54350", builder_name="", estate_name="Dawn, Walloon",
                lot_number="1228", source_channel="digital email", source_doc="doc-13aug",
                email_list=shape, email_first_seen="17/08/2026", date_checked="20/08/2026",
                price=721900.0)
    twin = dict(dawn, content_hash="v2:c48b4599", email_first_seen="19/08/2026")
    assert _guard(dawn, [dawn, twin]) is None, "a re-read of the same email is not newer"
    newer_list = _row("v2:sept-list", builder_name="", estate_name="Dawn, Walloon",
                      lot_number="2001", source_channel="digital email",
                      source_doc="doc-10sep", email_list=shape,
                      date_checked="12/09/2026")
    assert _guard(dawn, [dawn, twin, newer_list]) == "newer_list_from_sender"
    other = dict(newer_list, email_list=C.email_list_key("email:Hattan Update - 10 Sep 2026"))
    assert _guard(dawn, [dawn, twin, other]) is None


def test_email_list_stems():
    stem = C.email_list_stem
    assert stem("email:Fwd: Shape Homes - Stock List 13.08.2026") == "shape home stock list"
    assert stem("email:Hattan Update - 17 August 2026") == \
        stem("email:Fwd: Hattan Update - 31 August 2026") == "hattan update"
    assert stem("email:Hattan Stock Lists - 3 August 2026") == \
        stem("email:Hattan Stock List - 14 September 2026")
    assert stem("email:Fw: H&L Current Availability - 28th of July") == \
        "h l current availability"
    # A reply about an old list is not a new list.
    assert stem("email:RE: Hattan Update - 17 August 2026") != stem(
        "email:Hattan Update - 10 September 2026")
    assert stem("https://docs.google.com/spreadsheets/d/x") == ""
    assert stem("email:Fwd: 13.08.2026") == "" and C.email_list_key("") == ""


# ------------------------------------------------------------------ the 45-day limit

def test_over_the_confirm_limit():
    fifty = _row("v2:fifty", date_checked="27/07/2026", source_channel="digital email",
                 lot_number="50", lot_address="Lot 50 Windermere")
    forty_five = _row("v2:fortyfive", date_checked="02/08/2026", lot_number="45",
                      lot_address="Lot 45 Windermere", source_channel="digital email")
    assert _guard(fifty, [fifty]) == "over_confirm_limit"
    assert _guard(forty_five, [forty_five]) is None
    packages, counts = _packages([fifty, forty_five])
    assert counts["stale_over_limit"] == 1
    assert {p["content_hash"]: p["confirm_guard"] for p in packages} == {
        "v2:fifty": "over_confirm_limit", "v2:fortyfive": None}


def test_an_emailed_row_ages_from_its_first_read_not_its_last_sweep():
    """Critique 27: an emailed row's date_checked is the last night its message sat in the
    7-day inbox sweep. 20 days by that date, 50 by the night the list was first read."""
    row = _row("v2:email", source_channel="digital email", builder_name="",
               source_doc="doc-email", date_checked="27/08/2026",
               email_first_seen="27/07/2026")
    assert _guard(row, [row]) == "over_confirm_limit"
    assert _guard(dict(row, email_first_seen=None), [row]) is None
    # A non-email row never ages from it, even if a cell were filled.
    portal = dict(row, source_channel="Proxima", email_first_seen="27/07/2026")
    assert C._confirm_age_days(portal, TODAY) == 20
    (pkg,), _counts = _packages([row])
    assert pkg["email_first_seen"] == "27/07/2026"
    assert pkg["list_age_days"] == 51 and pkg["age_days"] == 20


# ----------------------------------------------------------------- AEST today (#W11.3)

def test_the_day_is_brisbanes_not_the_servers(monkeypatch):
    """16/09 23:30 UTC is 17/09 09:30 in Brisbane. A row checked 02/09 is 15 days old
    there -- stale -- and would be 14 and verified on a UTC clock."""
    assert C._now_aest(datetime(2026, 9, 16, 23, 30, tzinfo=timezone.utc)) == \
        datetime(2026, 9, 17, 9, 30)
    assert C._now_aest(datetime(2026, 9, 16, 23, 30)) == datetime(2026, 9, 17, 9, 30)
    monkeypatch.setattr(C, "_utc_now",
                        lambda: datetime(2026, 9, 16, 23, 30, tzinfo=timezone.utc))
    row = {"date_checked": "02/09/2026"}
    verified, seen, raw = C._fresh(row)
    assert (verified, seen, raw) == (False, datetime(2026, 9, 2), "02/09/2026")
    assert C._age_days(row) == 15
    packages, _counts = C.build_packages({"state": "QLD", "budget_max": 800000},
                                         [_row("v2:aest", date_checked="02/09/2026",
                                               source_doc="doc-aest")])
    assert packages[0]["age_days"] == 15 and packages[0]["verified"] is False


# ------------------------------------------------ the main shortlist, defence in depth (#28)

def test_a_fresh_row_whose_lot_was_later_read_on_hold_is_not_verified():
    """Avia lot 57, The Hills Collection: Available 03/09, On Hold 16/09."""
    fresh = _row("v2:46bc9cca", builder_name="Avia Homes", estate_name="THE HILLS COLLECTION",
                 lot_number="57", date_checked="10/09/2026", source_doc="doc-avia")
    on_hold = dict(fresh, content_hash="v2:d66e257b", availability_status="On Hold",
                   date_checked="16/09/2026", state=None)
    (pkg,), counts = _packages([fresh, on_hold])
    assert pkg["verified"] is False
    assert pkg["confirm"] == ["newer_capture"]
    assert pkg["confirm_guard"] == "newer_capture_disagrees"
    assert counts["newer_capture_demoted"] == 1
    (alone,), counts = _packages([fresh])
    assert alone["verified"] is True and "confirm" not in alone
    assert counts["newer_capture_demoted"] == 0
    # Newer and still available is not a disagreement.
    (pkg,), _ = _packages([fresh, dict(on_hold, availability_status="Available")])
    assert pkg["verified"] is True


# --------------------------------------------- exceptions-only admission (#1, W11.6)

HEATHWOOD_15897 = ("PR8831 8 Heathwood The Crest Estate September 2026 400 Build Only "
                   "$390,639 $820 Brighton 191 4/2/2/2")
SOUTH_RIPLEY_330 = ("DUPLEX PR8638 545 South Ripley Providence Estate Registered 534 "
                    "$725,000 Offers Over 780615 1505615 1240 0.0439 Custom 297 3 + 3 "
                    "Finance Considered")
REDBANK_329 = ("DUPLEX PR8735 106 Redbank Plains Sienna Eden Estate 2026-09-01 00:00:00 505 "
               "$595,000 $732,285 732285 1327285 1400 0.0548 Glenyoy 261 3 + 3 Finance "
               "Considered")
CABOOLTURE_19812 = ("PR0022 33 Caboolture Central Springs Estate PR8949 $534 Burpengary "
                    "Sage Estate Registered $262 45000000.00% $382,201.00 30/09/2025")
PLAIN_LINE = "PR9001 12 Walloon Estate Registered 450 $601,500 Hampton 220 4/2/2"


def _evidenced(content_hash, text, price, product_type="House & Land", **kw):
    """A row carrying the evidence build_web computes from its text, then losing the text
    -- which is exactly what the deployed snapshot holds."""
    base = {"price": price, "product_type": product_type, "source_text": text,
            "lot_address": text[:120]}
    evidence = price_kind.price_evidence(base)
    row = _row(content_hash, price=price, product_type=product_type,
               lot_address=text[:120], availability_status=None, builder_name="",
               estate_name="Exceptions Estate", lot_number=content_hash[-4:],
               source_doc="doc-exceptions", date_checked="15/09/2026", **evidence)
    row.update(kw)
    return row


def _exceptions_list(*blank_rows, marked="On Hold", marked_live=True):
    marker = _row("v2:marked", builder_name="", estate_name="Exceptions Estate",
                  lot_number="1", availability_status=marked, source_doc="doc-exceptions",
                  date_checked="15/09/2026" if marked_live else "01/09/2026",
                  superseded_by=None if marked_live else "v2:someone-newer")
    return [marker] + list(blank_rows)


def test_a_list_whose_only_statement_is_a_superseded_on_hold_is_confirmed_not_recommended():
    """Critique 1. 17 documents say On Hold or Sold only on SUPERSEDED captures; their 53
    live rows are blank, 40 of them fresh. Counting live rows alone would call those lists
    silent and recommend every blank row. They are exceptions-only lists: nothing reaches
    the shortlist, and the rows are there to confirm."""
    blanks = [_evidenced("v2:gd-%04d" % i, PLAIN_LINE.replace("$601,500", "$60%d,500" % i),
                         600500.0 + i * 1000) for i in range(1, 4)]
    rows = _exceptions_list(*blanks, marked_live=False)
    packages, counts = _packages(rows, {"state": "QLD", "budget_max": 900000})
    assert packages == [], "no blank row on an exceptions-only list is recommended"
    assert counts["availability_unstated"] == 3
    assert counts["available_by_the_sheet"] == 0

    admitted, admitted_counts = _packages(rows, {"state": "QLD", "budget_max": 900000},
                                          admit=ADMIT, only_admitted=True)
    assert sorted(p["content_hash"] for p in admitted) == ["v2:gd-0001", "v2:gd-0002",
                                                           "v2:gd-0003"]
    for p in admitted:
        assert p["confirm"] == ["availability_unstated"]
        assert p["confirm_guard"] is None and p["verified"] is False
    assert admitted_counts["admitted"] == {"availability_unstated": 3}


def test_the_exceptions_only_admission_rules():
    ok = _evidenced("v2:ok-0001", PLAIN_LINE, 601500.0)
    heathwood = _evidenced("v2:hw-15897", HEATHWOOD_15897, 390639.0)
    ripley = _evidenced("v2:sr-0330", SOUTH_RIPLEY_330, 725000.0, product_type=None)
    redbank = _evidenced("v2:rb-0329", REDBANK_329, 732285.0, product_type=None)
    caboolture = _evidenced("v2:cb-9812", CABOOLTURE_19812, 382201.0)
    unprinted = dict(ok, content_hash="v2:np-0002", lot_number="np2", price=611500.0,
                     text_price_max=601500, price_in_text=False)
    no_evidence = dict(ok, content_hash="v2:ne-0003", lot_number="ne3", price_kind=None,
                       text_price_max=None, price_in_text=None)
    rows = _exceptions_list(ok, heathwood, ripley, redbank, caboolture, unprinted,
                            no_evidence)
    prepared = _prepared(rows)
    why = {r["content_hash"]: C.admission_refusal(r, prepared, TODAY)[0] for r in rows[1:]}
    assert why == {"v2:ok-0001": None, "v2:hw-15897": "build_only",
                   "v2:sr-0330": "larger_total_in_text",
                   "v2:rb-0329": "larger_total_in_text",
                   "v2:cb-9812": "larger_total_in_text",
                   "v2:np-0002": "price_not_in_text", "v2:ne-0003": "no_price_evidence"}

    admitted, counts = _packages(rows, {"state": "QLD", "budget_max": 800000},
                                 admit=ADMIT, only_admitted=True)
    assert [p["content_hash"] for p in admitted] == ["v2:ok-0001"]
    assert counts["unstated_not_admitted"] == {
        "build_only": 1, "larger_total_in_text": 3, "price_not_in_text": 1,
        "no_price_evidence": 1}

    # The list itself: one row stated Available makes a blank a real gap, and a list that
    # never marks a lot gone is not an exceptions-only list.
    stated = _exceptions_list(ok, dict(ok, content_hash="v2:av", lot_number="av",
                                       availability_status="Available"))
    assert C.admission_refusal(ok, _prepared(stated), TODAY)[0] == "document_states_available"
    silent_but_sold_long_ago = [dict(ok, source_doc="doc-x"),
                                dict(ok, content_hash="v2:x-av", source_doc="doc-x",
                                     availability_status="Available",
                                     superseded_by="v2:newer")]
    assert C.admission_refusal(silent_but_sold_long_ago[0],
                               _prepared(silent_but_sold_long_ago),
                               TODAY)[0] == "document_marks_no_exceptions"
    assert C.admission_refusal(dict(ok, source_doc=""), _prepared(rows), TODAY)[0] == \
        "no_document"
    # And the sold-stock guards apply to admission too.
    stale = dict(ok, content_hash="v2:stale-ok", lot_number="st", date_checked="25/07/2026")
    stale_list = [dict(r, date_checked="25/07/2026") for r in _exceptions_list(stale)]
    assert C.admission_refusal(stale, _prepared(stale_list), TODAY)[0] == "over_confirm_limit"


BUILDING_B_UNDER_CONTRACT = ("Building B Level 3 Type 17 B302 2 BRM + 2 BATH 1 Standard South "
                             "Balcony 72 14 86 $910,000 Under contract")
BUILDING_B_CUT_OFF = ("Building B Level 1 Type 12 B101 2 BRM + 2 BATH 1 Standard North South "
                      "Balcony 76 32 108 $956,000 Under contrac")
BUILDING_B_OPEN = ("Building B level 4 Type 13 B404 2 BRM + 2 BATH 1 Standard North Balcony "
                   "73.5 16 89.5 $929,000")
UNDER_CONSTRUCTION = "PR9002 14 Walloon Estate Under Construction 450 $611,500 Hampton 220 4/2/2"


def test_a_line_that_says_it_has_gone_is_not_offered_to_confirm():
    """The NSW 'Building B' list marks 7 lots Sold, which the status column holds, and 10
    'Under contract' / 'Under contact', which it does not. Those 10 are blank on a list
    that marks lots gone -- exactly the exceptions-only shape -- and 6 passed every other
    rule on the 17 Sep copy. Their own line says they are under contract."""
    contract = _evidenced("v2:bb-b302", BUILDING_B_UNDER_CONTRACT, 910000.0,
                          product_type="Apartment")
    cut_off = _evidenced("v2:bb-b101", BUILDING_B_CUT_OFF, 956000.0, product_type="Apartment")
    typo = dict(contract, content_hash="v2:bb-b105", lot_number="b105",
                lot_address=BUILDING_B_UNDER_CONTRACT.replace("contract", "contact"))
    open_lot = _evidenced("v2:bb-b404", BUILDING_B_OPEN, 929000.0, product_type="Apartment")
    building = _evidenced("v2:uc-9002", UNDER_CONSTRUCTION, 611500.0)
    negated = dict(open_lot, content_hash="v2:bb-neg", lot_number="neg",
                   lot_address="Lot 7 is not on hold exclusively $929,000")
    rows = _exceptions_list(contract, cut_off, typo, open_lot, building, negated)
    prepared = _prepared(rows)
    why = {r["content_hash"]: C.admission_refusal(r, prepared, TODAY)[0] for r in rows[1:]}
    assert why == {"v2:bb-b302": "text_marks_unavailable", "v2:bb-b101": "text_marks_unavailable",
                   "v2:bb-b105": "text_marks_unavailable", "v2:bb-b404": None,
                   "v2:uc-9002": None, "v2:bb-neg": None}
    # A row that still carries its source_text (a database-joined row) is read there too.
    address_only = dict(contract, content_hash="v2:bb-b6", lot_number="b6",
                        lot_address="1 Standard North Court, Yard",
                        source_text=BUILDING_B_UNDER_CONTRACT)
    assert C.admission_refusal(address_only, _prepared(rows + [address_only]),
                               TODAY)[0] == "text_marks_unavailable"

    admitted, counts = _packages(rows, {"state": "QLD", "budget_max": 1000000},
                                 admit=ADMIT, only_admitted=True)
    assert sorted(p["content_hash"] for p in admitted) == ["v2:bb-b404", "v2:bb-neg",
                                                           "v2:uc-9002"]
    assert counts["unstated_not_admitted"] == {"text_marks_unavailable": 3}


# ------------------------------------------------------ Colin's search, on the fixture

WINDERMERE = ("v2:d32b17a9", "v2:50cabe9b", "v2:b399f290")
DAWN = ("v2:aec54350", "v2:30440e59", "v2:325f8457")


@pytest.fixture(scope="module")
def subset_rows():
    return gen.load_rows(gen.FIXTURE)


@pytest.fixture(scope="module")
def subset_prepared(subset_rows):
    return C.prepare_rows(subset_rows)


@pytest.mark.parametrize("budget,windermere", [
    (750000, WINDERMERE), (760000, WINDERMERE + ("v2:022e96c6",)),
    (770000, WINDERMERE + ("v2:022e96c6",))])
def test_colins_pending_rows_dawn_to_confirm_windermere_hidden(
        budget, windermere, subset_rows, subset_prepared):
    brief = dict(gen.BRIEFS[0][1], budget_max=budget, preferred_spending_cap=budget)
    packages, counts = C.build_packages(brief, subset_rows, today=gen.TODAY,
                                        prepared=subset_prepared)
    pending = {p["content_hash"][:11]: p for p in packages if not p["verified"]}
    assert set(pending) == set(DAWN) | set(windermere)
    for h in DAWN:
        assert pending[h]["confirm"] == ["stale"]
        assert pending[h]["confirm_guard"] is None, h
        assert pending[h]["email_first_seen"] and pending[h]["list_age_days"] >= 27
    for h in windermere:
        assert pending[h]["confirm_guard"] == "newer_capture_disagrees", h
    assert counts["confirm_guarded"] == {"newer_capture_disagrees": len(windermere)}
    if budget == 750000:
        assert counts["scored"] == 8, "was 16: 8 scoring slots went to rows over $750,000"


def test_colins_unstated_rows_are_all_refused_for_the_right_reason(subset_rows,
                                                                    subset_prepared):
    """Plan section 4's hidden rows, each refused by the rule written for it."""
    expected = {"v2:0c599a1c": "build_only",            # 15897 Heathwood, Build Only
                "v2:89ed40df": "build_only",            # 2140 Heathwood, Build Only
                "v2:f0865179": "larger_total_in_text",  # 19812 Caboolture, garbled
                "v2:3b141dd8": "document_reread_without_row",   # 15899 Wacol
                # 330 South Ripley / 329 Redbank Plains: their list states Available on
                # other rows, so it is not an exceptions-only list before their duplex
                # totals are even read.
                "v2:99dfea91": "document_states_available",
                "v2:487e9a1f": "document_states_available"}
    rows = {r["content_hash"][:11]: r for r in subset_rows}
    got = {h: C.admission_refusal(rows[h], subset_prepared, gen.TODAY)[0] for h in expected}
    assert got == expected
    brief = dict(gen.BRIEFS[0][1])
    admitted, counts = C.build_packages(brief, subset_rows, today=gen.TODAY,
                                        prepared=subset_prepared, admit=ADMIT,
                                        only_admitted=True)
    assert admitted == []
    assert counts["unstated_not_admitted"] == dict(Counter(expected.values()))


# ------------------------------------------------------------- the snapshot's fields (W11.7)

def test_price_evidence_and_the_emailed_list_are_snapshot_fields():
    import build_web
    for f in ("price_kind", "text_price_max", "price_in_text", "email_list",
              "email_first_seen"):
        assert f in C.SNAPSHOT_FIELDS, f
        assert f in build_web.BUILDING_DERIVED_FIELDS, f
        assert f not in build_web.BUILDING_FIELDS, f"{f} would reach the SQL SELECT"
    assert build_web.EMAIL_LIST_FIELDS == ["email_list", "email_first_seen"]
    build_web._check("buildings", build_web.BUILDING_FIELDS + build_web.BUILDING_DERIVED_FIELDS)
    failures = []
    email = {"source_channel": "digital email", "first_seen": "17/08/2026",
             "source_url": "email:Fwd: Shape Homes - Stock List 13.08.2026"}
    assert build_web._email_list_cells(email, failures) == {
        "email_list": C.email_list_key(email["source_url"]), "email_first_seen": "17/08/2026"}
    assert build_web._email_list_cells(dict(email, source_channel="E-Agent"), failures) == {
        "email_list": None, "email_first_seen": None}
    assert failures == []


def test_a_row_that_breaks_the_email_cells_does_not_stop_the_build(monkeypatch):
    """build_web.py is a required nightly step (critique 5): an exception computing one
    row's emailed-list cells must publish nulls and record the failure, never abort the
    chain and leave the dashboard on yesterday's snapshot."""
    import build_web
    import api._candidates as bundled

    def boom(row):
        raise ValueError("unreadable subject")

    monkeypatch.setattr(bundled, "snapshot_email_cells", boom)
    failures = []
    cells = build_web._email_list_cells(
        {"content_hash": "v2:bad", "source_channel": "digital email",
         "source_url": "email:Fwd: Shape Homes - Stock List 13.08.2026"}, failures)
    assert cells == {"email_list": None, "email_first_seen": None}
    assert failures == [("v2:bad", "ValueError: unreadable subject")]


def test_the_email_cells_are_taken_before_source_url_and_first_seen_are_dropped():
    src = (APP / "build_web.py").read_text(encoding="utf-8")
    body = src[src.index("def build(out_dir"):]
    call = body.index("_email_list_cells(b, _evidence_failures)")
    assert call < body.index('for _internal in ("source_text", "source_url", "stocklist_file")')
    assert call < body.index('b.pop("first_seen", None)')
    assert '"first_seen"]' in body[:body.index("rows = conn.execute(")]


def test_the_sqlite_reader_computes_the_cells_a_snapshot_database_predates(tmp_path):
    """W11.7. A snapshot database written before these cells existed still reads, and
    computes them the way build_web does; its source_text never rides on the row."""
    legacy = [f for f in C.SNAPSHOT_FIELDS if f not in C._COMPUTED_SNAPSHOT_FIELDS]
    db = tmp_path / "snapshot.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE buildings (%s)" % ", ".join(
        legacy + ["source_text", "source_url", "first_seen"]))
    values = dict.fromkeys(legacy)
    values.update(content_hash="v2:sr", price=725000.0, lot_address=SOUTH_RIPLEY_330[:120],
                  source_channel="E-Agent")
    email = dict(values, content_hash="v2:dawn", price=721900.0,
                 lot_address="1228 Available Medium Density $721,900",
                 source_channel="digital email")
    for v, text, url, first in ((values, SOUTH_RIPLEY_330, "https://x/y", "01/08/2026"),
                                (email, "1228 Available $270,000 $451,900 $721,900",
                                 "email:Fwd: Shape Homes - Stock List 13.08.2026",
                                 "17/08/2026")):
        conn.execute("INSERT INTO buildings VALUES (%s)" % ",".join("?" * (len(legacy) + 3)),
                     [v[f] for f in legacy] + [text, url, first])
    conn.commit()
    conn.close()
    rows = {r["content_hash"]: r for r in C._read_sqlite(db)}
    assert rows["v2:sr"]["text_price_max"] == 1505615
    assert rows["v2:sr"]["price_in_text"] is True
    assert rows["v2:sr"]["email_list"] is None and rows["v2:sr"]["email_first_seen"] is None
    assert (rows["v2:dawn"]["price_in_text"], rows["v2:dawn"]["text_price_max"]) == \
        (True, 721900)
    assert rows["v2:dawn"]["email_list"] == C.email_list_key("Shape Homes - Stock List")
    assert rows["v2:dawn"]["email_first_seen"] == "17/08/2026"
    for row in rows.values():
        assert not {"source_text", "source_url", "first_seen"} & set(row)
        assert set(row) == set(C.SNAPSHOT_FIELDS)


def test_the_fixture_carries_the_build_time_cells_on_the_rows_it_was_cut_with(subset_rows):
    fixture = json.loads(gen.FIXTURE.read_text(encoding="utf-8"))
    assert set(gen.BUILD_TIME_KEYS) <= set(fixture["keys"])
    golden = json.loads(gen.GOLDEN.read_text(encoding="utf-8"))
    assert gen.fingerprint(subset_rows) == golden["subset_fingerprint"]
    dawn = next(r for r in subset_rows if r["content_hash"].startswith("v2:aec54350"))
    assert (dawn["price_in_text"], dawn["text_price_max"], dawn["email_first_seen"]) == \
        (True, 721900, "17/08/2026")
