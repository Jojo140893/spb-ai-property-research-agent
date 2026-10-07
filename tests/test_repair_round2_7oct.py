"""Repair round 2 of the parity release (7 Oct 2026): what the three verifiers found on the
combined branch, each as a test that fails on par-search 068569a and passes after.

  * THE FRESH GUARD HID WHAT IT COULD NOT PROVE GONE. 1,050 rows read in the last 14 days
    were taken out of every list with no flag. The two rules that judge a list by what ELSE
    was read (the builder's other lists, a newer file in its folder) now send the row to
    "Needs confirming"; every rule has an action an env var can change; and a builder's
    lists are judged in the row's own state.
  * A PROXIMA PROJECT READ AGAIN, WHOLE, WITHOUT A LOT still shortlisted it as verified
    (Ascenta Living Coliving: 5 lots against a counter of 5).
  * ONE LOT, TWO CARDS, THE WRONG ONE KEPT. Hermitage's E-Agent "918 Sheffield St" was kept
    over the portal's "LOT 918 SHEFFIELD ST" and Clare could no longer pin the lot; "729
    Active Walk, Society" kept a suburb guessed from its list over the portal's Fraser Rise;
    Hattan's "Lot 3755, Q3 Single" (Clyde) over "Lot 3755, Ceduna" (Clyde North). And
    "Springvale Double", a place and a product, was read as an estate, so lot 58 showed
    twice.
  * A ROW THAT WRITES ITS OWN ADDRESS ("Mt Duneed,Victoria,3217") was given its builder's
    commonest suburb, Tarneit, and shortlisted in a Tarneit search.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for _p in (str(APP / "api"), str(APP), str(TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _candidates as C  # noqa: E402
import _synthetic_stock as S  # noqa: E402
import suburb_quality  # noqa: E402

TODAY = S.TODAY                 # 16/09/2026 12:00


def _packages(rows, brief=None):
    packages, _counts = C.build_packages(dict(brief or S.BRIEF), rows, today=TODAY)
    return {p["content_hash"]: p for p in packages}


def _list(prefix, n, day, **kw):
    base = dict(source_doc="doc-" + prefix, estate_name=prefix.title() + " Rise",
                date_checked=day)
    base.update(kw)
    return [S.row("v2:%s-%02d" % (prefix, i), lot_number=str(100 + i),
                  lot_address="Lot %d, %s Rise" % (100 + i, prefix.title()),
                  line_key="k-%s-%02d" % (prefix, i), price=600000.0 + 1000 * i, **base)
            for i in range(n)]


def _replaced_list():
    return (_list("hudson-old", 4, "05/09/2026", builder_name="Lineage Homes")
            + _list("hudson-new", 4, "15/09/2026", builder_name="Lineage Homes"))


# ------------------------------------------------------------- what each rule does

def test_a_list_the_builder_replaced_goes_to_needs_confirming_not_nowhere(monkeypatch):
    monkeypatch.delenv(C.FRESH_GUARD_ENV, raising=False)
    old = _packages(_replaced_list())["v2:hudson-old-00"]
    assert not old["verified"]
    assert old.get("confirm_guard") is None, "hidden with no flag: the round-1 behaviour"
    assert "builder_documents_reread" in old["confirm"]


def test_a_re_upload_in_the_same_folder_goes_to_needs_confirming(monkeypatch):
    monkeypatch.delenv(C.FRESH_GUARD_ENV, raising=False)
    old = _list("vanda", 3, "05/09/2026", builder_name="Vanda", source_folder="f-va")
    new = _list("va", 3, "07/09/2026", builder_name="Victoria & Albert", source_folder="f-va")
    pk = _packages(old + new)
    assert pk["v2:vanda-00"].get("confirm_guard") is None
    assert "newer_upload_in_folder" in pk["v2:vanda-00"]["confirm"]
    assert pk["v2:va-00"]["verified"]


def test_every_rule_can_be_hidden_or_switched_off_without_a_code_change(monkeypatch):
    monkeypatch.setenv(C.FRESH_GUARD_ENV, "builder_documents_reread=hide")
    old = _packages(_replaced_list())["v2:hudson-old-00"]
    assert old["confirm_guard"] == "builder_documents_reread"
    monkeypatch.setenv(C.FRESH_GUARD_ENV, " builder_documents_reread = off , nonsense=hide,"
                                          "newer_capture_exists=sideways")
    old = _packages(_replaced_list())["v2:hudson-old-00"]
    assert old["verified"], "switched off: the row is a verified result again"
    assert C.fresh_guard_actions()["newer_capture_exists"] == "hide", "a bad action is ignored"
    monkeypatch.delenv(C.FRESH_GUARD_ENV)
    assert C.fresh_guard_actions() == C.FRESH_GUARD_ACTIONS


def test_a_builders_list_in_another_state_says_nothing_about_this_one(monkeypatch):
    """E-Agent lists a builder's stock one state page at a time. The builder's QLD list read
    last night is no evidence that its NSW list, unread for 11 days, came down."""
    monkeypatch.delenv(C.FRESH_GUARD_ENV, raising=False)
    rows = (_list("nsw-old", 3, "05/09/2026", builder_name="Twostate Homes", state="NSW",
                  suburb="Box Hill")
            + _list("qld-new", 3, "15/09/2026", builder_name="Twostate Homes", state="QLD",
                    suburb="Logan Reserve"))
    pk = _packages(rows, dict(S.BRIEF, state="NSW"))
    assert pk["v2:nsw-old-00"]["verified"], pk["v2:nsw-old-00"].get("confirm")
    # ...and on the stale path too (needs confirming, never hidden by another state's read)
    stale = [dict(r, date_checked="20/08/2026") for r in rows if "nsw" in r["content_hash"]]
    prepared = C.prepare_rows(stale + [r for r in rows if "qld" in r["content_hash"]])
    assert C.confirm_guard(stale[0], prepared, TODAY) is None


# ------------------------------------------------------------- a Proxima project read again

def _project(pid, n, day, counter=None, prefix=None, first=200, **kw):
    prefix = prefix or "p%s" % pid
    return [S.row("v2:%s-%03d" % (prefix, i), source_channel="Proxima",
                  source_project_id=pid, project_available=counter, state="NSW",
                  suburb="Zetland", estate_name="Project %s" % pid,
                  source_doc="doc-proxima-index", lot_number=str(first + i),
                  lot_address="Lot %d Unit %d, 1 Example Street, ZETLAND, NSW, 2017"
                              % (first + i, first + i),
                  date_checked=day, price=700000.0 + 1000 * (first + i), **kw)
            for i in range(n)]


NSW = dict(S.BRIEF, state="NSW")


def test_a_lot_a_whole_project_read_left_out_is_not_a_verified_result():
    """Ascenta Living Coliving (project 785): 5 lots read on 06/10 against a counter of 5;
    three of 25/09 it no longer carried were shortlisted."""
    rows = (_project("785", 5, "15/09/2026", counter=5)
            + _project("785", 3, "10/09/2026", counter=8, prefix="old785", first=500))
    pk = _packages(rows, NSW)
    assert pk["v2:old785-000"]["confirm_guard"] == "project_reread_without_row"
    assert pk["v2:p785-000"]["verified"]


def test_a_project_read_that_stopped_at_its_cap_or_short_of_its_counter_says_nothing():
    capped = (_project("814", 80, "15/09/2026")
              + _project("814", 5, "10/09/2026", prefix="old814", first=500))
    assert _packages(capped, NSW)["v2:old814-000"]["verified"]
    short = (_project("820", 6, "15/09/2026", counter=40)
             + _project("820", 5, "10/09/2026", counter=40, prefix="old820", first=500))
    assert _packages(short, NSW)["v2:old820-000"]["verified"]


def test_a_project_with_no_counter_is_shown_to_confirm_only_after_a_steady_read():
    """No counter proves the read whole, so the row is not hidden; and a read that lost
    most of the project overnight (Second Avenue: 5 lots after 312) is a read cut short."""
    steady = (_project("762", 40, "15/09/2026")
              + _project("762", 2, "14/09/2026", prefix="old762", first=500))
    row = _packages(steady, NSW)["v2:old762-000"]
    assert not row["verified"] and row.get("confirm_guard") is None
    assert "project_reread_uncounted" in row["confirm"]
    cut = (_project("53", 5, "15/09/2026")
           + _project("53", 30, "14/09/2026", prefix="old53", first=500))
    assert _packages(cut, NSW)["v2:old53-000"]["verified"]


def test_a_stale_proxima_lot_is_not_hidden_because_another_project_was_read():
    """Every Proxima row shares the portal's index page as its document, so a stale lot of
    a project nobody re-read was hidden from Needs confirming as 'document_reread_without_row'
    whenever ANY project was read in the last three days (found by search_regressions'
    evidence check, repair round 2). Only its own project read again, whole, hides it."""
    stale = _project("448", 3, "20/08/2026", prefix="stale448")
    other = _project("701", 5, "15/09/2026", counter=5)
    prepared = C.prepare_rows(stale + other)
    assert C.confirm_guard(stale[0], prepared, TODAY) is None
    whole = _project("448", 4, "15/09/2026", counter=4, first=700)
    prepared = C.prepare_rows(stale + other + whole)
    assert C.confirm_guard(stale[0], prepared, TODAY) == "project_reread_without_row"


# ------------------------------------------------------------- one lot, two cards

def _dedupe(rows, brief=None):
    packages, _ = C.build_packages(dict(brief or S.BRIEF), rows, today=TODAY)
    merged = {}
    kept = C.dedupe_packages(packages, rows, merged_into=merged)
    return {p["content_hash"]: p for p in kept}, merged


def test_the_kept_card_takes_the_twins_title_that_names_the_lot():
    """Hermitage lot 918: E-Agent '918 Sheffield St, Heritage Place , Morwell 3840' (lot 918,
    also the street number, so Clare cannot pin it) and the portal's 'LOT 918 SHEFFIELD ST,
    MORWELL 3840', the same night, $628,200 and 437 m2 on both."""
    rows = [S.row("v2:ea918", builder_name="Hermitage Homes", lot_number="918",
                  lot_address="918 Sheffield St, Heritage Place , Morwell 3840",
                  estate_name="Heritage Place , Morwell 3840", suburb="Morwell",
                  price=628200.0, land_sqm=437.0, source_doc="doc-herm", line_key="k-ea"),
            S.row("v2:po918", builder_name="Hermitage Homes", lot_number=None,
                  lot_address="LOT 918 SHEFFIELD ST, MORWELL 3840", estate_name=None,
                  suburb="Morwell", price=628200.0, land_sqm=437.0, bedrooms=None,
                  house_sqm=None, availability_status=None,
                  source_channel="Direct Builder Portal (live)", source_doc="doc-portal",
                  line_key="k-po")]
    kept, merged = _dedupe(rows)
    assert len(kept) == 1, kept
    card = next(iter(kept.values()))
    assert card["lot_address"].upper().startswith("LOT 918"), card["lot_address"]


def test_the_kept_card_takes_a_twins_located_suburb_over_a_guess():
    """Hermitage lot 729: '729 Active Walk, Society', its suburb Wyndham Vale guessed from
    its list's other lots, and the portal's 'LOT 729 ACTIVE WALK, FRASER RISE'."""
    guessed = suburb_quality.INFERRED_FROM_SIBLINGS
    rows = [S.row("v2:ea729", builder_name="Hermitage Homes", lot_number="729",
                  lot_address="729 Active Walk, Society", estate_name="Society",
                  suburb="Wyndham Vale", suburb_source=guessed, price=565033.0,
                  land_sqm=113.0, source_doc="doc-herm", line_key="k-ea"),
            S.row("v2:po729", builder_name="Hermitage Homes", lot_number=None,
                  lot_address="LOT 729 ACTIVE WALK, FRASER RISE", estate_name=None,
                  suburb="Fraser Rise", price=565033.0, land_sqm=113.0, bedrooms=None,
                  house_sqm=None, availability_status=None,
                  source_channel="Direct Builder Portal (live)", source_doc="doc-portal",
                  line_key="k-po")]
    kept, _ = _dedupe(rows)
    card = next(iter(kept.values()))
    assert len(kept) == 1 and card["suburb"] == "Fraser Rise", card
    assert suburb_quality.is_located(card["suburb_source"])


def test_on_a_tie_the_card_whose_estate_names_a_place_is_kept():
    """Hattan lot 3755, read the same night as 'Lot 3755, Ceduna' (Clyde North, emailed)
    and 'Lot 3755, Q3 Single' (Clyde, E-Agent): the second was kept."""
    common = dict(builder_name="Hattan Homes", lot_number="3755", price=604929.0,
                  land_price=317129.0, build_price=287800.0, land_sqm=186.0)
    rows = [S.row("v2:q3", lot_address="Lot 3755, Q3 Single", estate_name="Q3 Single",
                  suburb="Clyde", source_doc="doc-ea", line_key="k-q3", **common),
            S.row("v2:ced", lot_address="Lot 3755, Ceduna", estate_name="Ceduna",
                  suburb="Clyde North", source_channel="digital email",
                  source_doc="doc-email", line_key="k-ced", **common)]
    kept, _ = _dedupe(rows)
    assert set(kept) == {"v2:ced"}, set(kept)


def test_a_place_and_a_product_name_no_estate():
    """Hattan's E-Agent sheet files lot 58 under 'Springvale Double'; the emailed list
    under 'Roselle', in Springvale South. $984,950 and 160 m2 on both: one lot."""
    common = dict(builder_name="Hattan Homes", lot_number="58", price=984950.0,
                  land_sqm=160.0)
    rows = [S.row("v2:sd58", lot_address="Lot 58, Springvale Double",
                  estate_name="Springvale Double", suburb="Springvale", bedrooms=None,
                  source_doc="doc-ea", line_key="k-sd", **common),
            S.row("v2:ro58", lot_address="Lot 58, Roselle", estate_name="Roselle",
                  suburb="Springvale South", source_channel="digital email",
                  source_doc="doc-email", line_key="k-ro", **common)]
    kept, _ = _dedupe(rows)
    assert set(kept) == {"v2:ro58"}, set(kept)
    # Two real estates' lot 58s stay two lots.
    other = [dict(rows[0], estate_name="Riverdale", lot_address="Lot 58, Riverdale")] + rows[1:]
    kept, _ = _dedupe(other)
    assert len(kept) == 2


# ------------------------------------------------------------- the address a row writes

@pytest.mark.parametrize("text, want", [
    ("Mt Duneed,Victoria,3217 $836,000.00 325 m2 195 m2 4 2 2", "Mount Duneed"),
    ("ClydeNorth,Victoria,3978 $819,000.00 350 m2", "Clyde North"),
    ("Bonniebrook,Victoria,3335 $756,000.00 294 m2", "Bonnie Brook"),
    ("Lot 19 ThorntonAvenue, StLeonards,Victoria,3223", "St Leonards"),
    ("Tarniet,Victoria,3029", "Tarneit"),
])
def test_a_row_that_writes_its_own_address_is_placed_there(text, want):
    row = {"lot_address": text, "state": "VIC", "suburb": None}
    assert suburb_quality.resolve(row) == (want, suburb_quality.FROM_WRITTEN_ADDRESS)


def test_the_written_address_outranks_a_neighbouring_cell_and_agrees_with_itself():
    # The suburb column held another lot's place ('Merbein', 400 km away).
    row = {"lot_address": "Clyde North,Victoria,3978 $819,000.00", "state": "VIC",
           "suburb": "Merbein"}
    assert suburb_quality.resolve(row)[0] == "Clyde North"
    # Where the column already agrees, its answer and grade stand.
    same = {"lot_address": "Tarneit VIC,Victoria,3029 $746,000.00", "state": "VIC",
            "suburb": "Tarneit"}
    assert suburb_quality.resolve(same) == ("Tarneit", suburb_quality.STATED)
    # A street named after a state, or a state with no postcode, is not an address.
    assert suburb_quality.written_address({"lot_address": "12 Victoria Street, Tarneit"}) is None


def test_a_guess_from_the_rows_siblings_never_contradicts_its_written_address():
    row = {"lot_address": "Mt Duneed,Victoria,3217 $836,000.00"}
    assert not suburb_quality.written_address_agrees(row, "Tarneit", "VIC")
    assert suburb_quality.written_address_agrees(row, "Mount Duneed", "VIC")
    assert suburb_quality.written_address_agrees({"lot_address": "Tarniet,Victoria,3029"},
                                                 "Tarneit", "VIC")
    assert suburb_quality.written_address_agrees({"lot_address": "Lot 4, Somewhere"},
                                                 "Tarneit", "VIC")


def test_the_search_does_not_borrow_a_suburb_against_the_rows_own_address():
    """The page-sibling map voted Tarneit for every row of the list; the row writes
    Bonniebrook, a place the search can read."""
    sibs = [S.row("v2:t%d" % i, source_doc="doc-1part", suburb="Tarneit", lot_number=str(i),
                  lot_address="Lot %d, Tarneit" % i) for i in range(1, 5)]
    mine = S.row("v2:bb", source_doc="doc-1part", suburb=None, suburb_source="",
                 lot_number=None, estate_name="32EsmeraldaAvenue",
                 lot_address="Bonniebrook,Victoria,3335 $756,000.00 294 m2 201 m2 4 2 2",
                 price=756000.0)
    card = _packages(sibs + [mine])["v2:bb"]
    assert card["suburb"] == "Bonnie Brook", card["suburb"]
