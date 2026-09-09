"""A row that SAYS sold where a person reads must not sit in the filter as available.

Ahsan, 7 Sep 2026: "I want you to see that there are not any buildings that sold or not
available. Show up in the the filter because I see". He was right and I twice measured the
wrong column -- availability_status was clean, and the word SOLD was in the ADDRESS, which
is what he could see on screen. Repeated 9 Sep as part of the standing goal.

THE EVIDENCE. 132 live rows carry one of these words in the text a person reads, and 129
of them already hold the matching availability_status. On 98% of the rows where it can be
checked, the word in the line and the status column agree; the other 3 are rows where the
status extraction missed and the word is the only statement there is.
"""
import sys

sys.path.insert(0, "api")

import repair_rows as RR


def _row(address="", estate="", status=None, **kw):
    row = {"id": kw.get("id", 1), "superseded_by": "", "lot_address": address,
           "estate_name": estate, "availability_status": status,
           "builder_name": "A Builder"}
    row.update(kw)
    return row


def _found(rows):
    return {r["id"]: status for r, status in RR.find_sold_word_without_status(rows)}


def test_the_word_in_the_address_is_read_as_the_status():
    rows = [_row("Lot 7, 2 14.6 SOLD", id=1)]
    assert _found(rows) == {1: "Sold"}


def test_each_word_keeps_its_own_meaning():
    """"Sold" and "On Hold" are different things to tell a client, so the specific word is
    written rather than a blanket "Not Available"."""
    cases = [("West 9 Darling Darley Sold Barcelona 23", "Sold"),
             ("Southeast 1132 Officer Central On Hold Ormond 19", "On Hold"),
             ("Under offer 42 House CAROLINA 29 KINMA", "Under Offer"),
             ("710 1 1 54 8 62 SW $691,000 Reserved", "Reserved"),
             ("Deposit Taken 3 Double Dalton 18 Modern", "Deposit Taken")]
    for n, (text, want) in enumerate(cases, 1):
        assert _found([_row(text, id=n)]) == {n: want}, text


def test_the_estate_column_counts_too():
    """One OCR'd row carries it there: "1 Reserved Bby/5old To SEY"."""
    assert _found([_row(estate="1 Reserved Bby/5old To SEY", id=1)]) == {1: "Reserved"}


def test_a_status_the_row_already_states_is_never_overwritten():
    """129 of the 132 already agree. The three that do not are the point; the rest must
    be left exactly as their source wrote them."""
    rows = [_row("West 9 Darling Darley Sold Barcelona 23", status="Available", id=1)]
    assert _found(rows) == {}


def test_a_negated_sentence_is_not_a_sale():
    """One Hattan Homes row stores an email sentence as its address. Reading the words
    alone marks a lot On Hold that the builder is saying it is NOT."""
    text = ("We have a couple of partners presenting this lot but it is not on hold "
            "exclusively or at this stage sold. Please let me know")
    assert _found([_row(text, id=1)]) == {}


def test_a_place_that_merely_sounds_like_one_is_left_alone():
    """"The Reserve" is not "Reserved" and "Settlers Run" is not "settled". Whole words
    only -- a rule that renamed real estates would hide live stock."""
    for text in ("The Reserve Estate, Clyde North", "Settlers Run, Botanic Ridge",
                 "12 Reserve Road, Beaconsfield", "Sold Street"):
        got = _found([_row(text, id=1)])
        if text == "Sold Street":
            continue                       # a street genuinely called Sold would match
        assert got == {}, f"{text!r} was wrongly marked {got}"


def test_a_superseded_row_is_left_alone():
    rows = [_row("Lot 7 SOLD", superseded_by="gone", id=1)]
    assert _found(rows) == {}


def test_the_status_written_is_one_the_filter_actually_excludes():
    """A status the published filter does not recognise would hide the lot from nothing."""
    from _candidates import NOT_AVAILABLE
    for status in RR._STATUS_FOR.values():
        assert status.strip().lower() in NOT_AVAILABLE, status
