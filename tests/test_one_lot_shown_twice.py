"""One property, two live rows, and the client sees it twice.

`suburb_norm` is one of the eight fields in content_hash, so the day an extractor starts
reading a suburb better, every affected row acquires a NEW identity: the harvest inserts
the improved row and the old one is never matched again. It sits there, live, a second
copy of a property Coleen can sell once.

find_suburb_twins collapsed those only when the older copy's suburb column was literally
EMPTY. Found by client testing on 10 Sep 2026: the older copy usually holds something --
whatever the extractor of the day made of the line -- so the rule missed the whole cohort.

  Lot CC-0116, one price, two live rows:
     6 Aug  "Truganina Montare Road, Truganina VIC"  -> shows Truganina, READ off the
            road name in its own address
     3 Sep  "Manor Lakes"                            -> shows Manor Lakes, STATED by the
            source
"""
import sys

sys.path.insert(0, "api")

import repair_rows as RR


def _row(rid, suburb, checked, **kw):
    row = {"id": rid, "superseded_by": "", "suburb": suburb, "date_checked": checked,
           "builder_name": "EVO Homes", "lot_number": "CC-0116", "house_design": "CONCORD",
           "land_sqm": 240.0, "source_channel": "E-Agent", "attribution_scope": "builder",
           "state": "VIC", "price": 955832.0, "lot_address": "CC-0116",
           "source_text": "CC-0116 448 Titled 240 m2 CONCORD SINGLE $955,832 Available",
           "variant_key": "", "postcode": None, "bedrooms": 5, "bathrooms": 2,
           "car_spaces": 2, "house_sqm": None, "estate_name": "", "storey": None,
           "state_source": "listing text", "land_price": None, "build_price": None,
           "listing_url": "", "floorplan_url": "", "brochure_url": "",
           "incentive_text": "", "incentive_amount": None, "frontage_m": None,
           "availability_status": "Available", "title_status": "", "product_type": "",
           "source_url": "https://x/y.xlsx", "stocklist_file": "", "content_hash": "",
           "source_row": None, "source_page": None, "source_sheet": "",
           "source_project_id": "", "project_available": None, "project_total": None,
           "last_seen": checked, "first_seen": checked, "checked_at": None,
           "extraction_confidence": 0.9, "suburb_source": "", "superseded_at": None}
    row.update(kw)
    return row


def _collapsed(rows):
    return {r["id"] for _keep, rest in RR.find_suburb_twins(rows) for r in rest}


def _kept(rows):
    return {keep["id"] for keep, _rest in RR.find_suburb_twins(rows)}


def test_a_junk_suburb_is_as_blank_as_an_empty_one():
    """"Shepparton Goulburn Valley Hwy" shows the client nothing: suburb_quality refuses
    it. Requiring the COLUMN to be empty missed every row like it."""
    rows = [_row(1, "Shepparton", "21/08/2026"),
            _row(2, "Shepparton Goulburn Valley Hwy", "29/07/2026")]
    # the junk value still RESOLVES to Shepparton, read rather than stated -- so it is
    # not blank, it is weaker, and the stated copy is the keeper either way
    assert _collapsed(rows) == {2} and _kept(rows) == {1}


def test_the_source_s_own_word_beats_our_reading_of_a_road_name():
    """Both are "located", so both used to count as named and the pair was left alone --
    and Coleen was shown one property twice."""
    # the stored value verbatim from lot CC-0116: the whole address jammed into the
    # suburb column, which suburb_quality reads a locality OUT of rather than accepting
    rows = [_row(1, "Manor Lakes", "03/09/2026"),
            _row(2, "Truganina Montare Road, Truganina VIC", "06/08/2026",
                 lot_address="CC-0116, Truganina Montare Road, Truganina VIC")]
    assert _collapsed(rows) == {2}, "the weaker-evidence copy was not collapsed"
    assert _kept(rows) == {1}


def test_two_rows_the_source_itself_names_differently_are_left_alone():
    """A genuine disagreement about where a property is. Guessing between two statements
    is exactly the call this project refuses to make."""
    rows = [_row(1, "Manor Lakes", "03/09/2026"),
            _row(2, "Tarneit", "06/08/2026")]
    assert _collapsed(rows) == set()


def test_two_rows_with_equally_weak_evidence_are_left_alone():
    """No strongest copy to defer to means no keeper, so nothing is collapsed."""
    rows = [_row(1, "Some Road, Truganina VIC", "03/09/2026",
                 lot_address="CC-0116, Some Road, Truganina VIC"),
            _row(2, "Other Road, Tarneit VIC", "06/08/2026",
                 lot_address="CC-0116, Other Road, Tarneit VIC")]
    assert _collapsed(rows) == set()


def test_a_different_lot_is_never_collapsed_into_this_one():
    rows = [_row(1, "Manor Lakes", "03/09/2026"),
            _row(2, "Truganina", "06/08/2026", lot_number="CC-9999")]
    assert _collapsed(rows) == set()


def test_the_evidence_order_is_suburb_quality_s_own():
    """3 stated, 2 read, 1 inferred, 0 nothing shown -- not a new opinion."""
    stated = _row(1, "Manor Lakes", "03/09/2026")
    junk = _row(2, "Agent Stock List 167 Ingleburn Road", "06/08/2026")
    assert RR._suburb_evidence(stated) == 3
    assert RR._suburb_evidence(junk) < 3
    assert RR._suburb_evidence(_row(3, "", "06/08/2026")) == 0
    assert RR._shows_a_suburb(_row(3, "", "06/08/2026")) is False


def test_a_superseded_row_is_not_a_twin():
    rows = [_row(1, "Manor Lakes", "03/09/2026"),
            _row(2, "", "06/08/2026", superseded_by="gone")]
    assert _collapsed(rows) == set()
