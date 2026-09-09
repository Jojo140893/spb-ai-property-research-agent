"""Email sentences stored as listings, with prices attached.

Four live rows are email bodies the digital-email reader kept as stock, and three carry a
price -- $553,284, $713,118, $600,000 -- so they were being offered to clients as
properties.

The first of them is also why find_sold_word_without_status has a negation guard: it
contains "on hold" and "sold" inside a sentence saying the lot is neither. Reading it as a
status was the wrong fix. It is not a listing at all.
"""
import sys

sys.path.insert(0, "api")

import repair_rows as RR


def _row(address, **kw):
    row = {"id": kw.get("id", 1), "superseded_by": "", "lot_address": address,
           "price": 553284, "builder_name": "Hattan Homes"}
    row.update(kw)
    return row


def _found(rows):
    return {r["id"] for r in RR.find_prose_rows(rows)}


def test_the_four_real_sentences_are_caught():
    cases = [
        "We have a couple of partners presenting this lot but it is not on hold "
        "exclusively or at this stage sold. Please let me know",
        "Hi Cheryl, We are interested in the property at Lot 1052, Westwood Walk and "
        "would like to know about the turnkey inclusions",
        "Stage 4 and lot 7- what is the full Turnkey package is the block registered "
        "and when can we expect titles",
        "OUR PROMISE At Strike Developments, we are dedicated to delivering homes that "
        "are built with care for the families who will live in them",
    ]
    for n, text in enumerate(cases, 1):
        assert _found([_row(text, id=n)]) == {n}, text[:40]


def test_a_price_list_line_is_not_prose():
    """A price-list line is nouns, numbers and money and scores nearly zero. A rule that
    caught these would withdraw live stock by the thousand."""
    for text in (
        "West 9 Darling Darley Sold Barcelona 23 Elevate $365,500",
        "Winterset West 610 Manor Lakes Jul 2027 294.00 LX 18E2 Grand $812,400",
        "Lot 414 Clearwater Estate (Arklay 17) 392 162.1 14.0 Q2-2026 Arklay 17 4x2x2",
        "2102 Racecourse Rd, Fraser Rise 10.5 294 SAVANA 1 4 $369,200 $355,641",
        "Building B Level 2 Type 16 B203 3 BRM + 2 BATH 2 Standard Sold",
    ):
        assert _found([_row(text, id=1)]) == set(), text[:40]


def test_a_short_line_is_never_prose():
    """Ten words is the floor: an address with "the" and "at" in it is still an address."""
    assert _found([_row("12 The Avenue at Ridge of the Park", id=1)]) == set()


def test_a_long_address_without_the_grammar_is_kept():
    """Length alone is not evidence -- flattened rows are long. It is the FUNCTION WORDS
    that make a sentence."""
    text = ("Munno Para Downs ON HOLD 214 Springfield Wilpena Edinburgh 4 2 2 375 "
            "$358,000 November 2026 Registered")
    assert _found([_row(text, id=1)]) == set()


def test_a_superseded_row_is_left_alone():
    text = "We have a couple of partners presenting this lot but it is not on hold yet"
    assert _found([_row(text, id=1, superseded_by="gone")]) == set()
