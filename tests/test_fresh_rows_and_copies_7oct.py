"""What the state-wide search must NOT show, now that it shows everything (repair round 1).

7 Oct 2026. Once the 600-row candidate cap went, every candidate in a state is scored, and
the verifiers of the parity release found what the cap had been hiding on the shortlist --
not lost lots this time, but wrong cards:

  * NOT A HOME AT ALL, as a verified result: an agent's "$50,000bonus.Purchaserrebate"
    line filed an Apartment, Goldstate's "$60,000 conversion fee" sentence, Eastbury's 5%
    deposits, storage units ("STORAGF Cold Shell"), warehouses filed Commercial, and a
    Hattan email line that is only "$667,418". The confirm path refused such prices; a
    stated "Available" row was never asked (_candidates.not_a_home).
  * STALE, as a verified result: 1,514 of 7,923 state-wide cards read 3 to 14 days ago,
    most of them on a list the builder had REPLACED (E-Agent hosts each upload under a new
    file), on a list re-read in full without them (AVIA lot 28's 27/09 package, which Clare
    read aloud), in a Dropbox folder whose file was re-uploaded (Victoria & Albert: 292
    cards for 127 apartments), or an older capture of a lot read since at another price
    (_candidates.fresh_row_guard). And a Proxima unit two projects disagree about is shown
    to confirm, not recommended.
  * ONE LOT, TWO CARDS: the same line of one list read twice under two card spellings, and
    the same lot on E-Agent and in an email or on a portal -- about 2% of a state's cards
    (_candidates.collapse_same_lot). Two packages on one lot (Goldstate 5710: Atlanta 206 m2
    and Trinidad 210 m2 at one price) stay two cards; they were merged into one.
  * AT EQUAL SCORES, THE FRESHER READ FIRST: AVIA lot 28's 27/09 package ranked ahead of
    its 06/10 one, and Clare said "as at 27 September" (kommo_agent.score_candidates).

Every test here fails on the branch before repair round 1 (47a8833) and passes after.
"""
import sys
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for _p in (str(APP / "api"), str(APP), str(TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _candidates as C  # noqa: E402
import _synthetic_stock as S  # noqa: E402

TODAY = S.TODAY                 # 16/09/2026 12:00


def _packages(rows, brief=None):
    packages, _counts = C.build_packages(dict(brief or S.BRIEF), rows, today=TODAY)
    return {p["content_hash"]: p for p in packages}


def _hidden_as(package):
    return None if package.get("verified") else package.get("confirm_guard")


# ------------------------------------------------------------------- not a home at all

def _junk():
    return [
        S.row("v2:good", lot_number="7", lot_address="Lot 7, Example Rise"),
        S.row("v2:commission", lot_number=None, bedrooms=None, product_type="Apartment",
              lot_address="4 SE- Chapel St, Agent commission=6%plus", price=50000.0,
              estate_name="Agent commission=6%plus"),
        S.row("v2:storage", lot_number=None, bedrooms=None, product_type=None,
              lot_address="Unit 33 1o+ S55 39.00 39.00 STORAGF Cold Shell Compi ated 14",
              price=764000.0, estate_name="Comp eted Dowmload"),
        S.row("v2:warehouse", lot_number=None, bedrooms=None, product_type="Commercial",
              lot_address="Unit 15 Seaford 195.00 90.00 285.00 Completed", price=980000.0,
              estate_name="Commercial"),
        S.row("v2:fragment", lot_number=None, bedrooms=None, estate_name=None,
              lot_address="$667,418", price=667418.0),
    ]


def test_a_line_that_is_not_a_home_is_never_a_verified_result():
    pk = _packages(_junk())
    assert pk["v2:good"]["verified"] and not pk["v2:good"].get("confirm_guard")
    assert _hidden_as(pk["v2:commission"]) == "price_not_a_home"
    assert _hidden_as(pk["v2:storage"]) == "commercial"
    assert _hidden_as(pk["v2:warehouse"]) == "commercial"
    assert _hidden_as(pk["v2:fragment"]) == "fragment"


def test_a_brief_that_asks_for_commercial_still_sees_it():
    brief = dict(S.BRIEF, product_type=["Commercial"])
    pk = _packages(_junk(), brief)
    assert pk["v2:warehouse"]["verified"], pk["v2:warehouse"].get("confirm_guard")


def test_a_stale_junk_row_is_not_offered_to_confirm_either():
    rows = [dict(r, date_checked="20/08/2026") for r in _junk()]
    pk = _packages(rows)
    assert pk["v2:commission"]["confirm_guard"] == "price_not_a_home"
    assert pk["v2:fragment"]["confirm_guard"] == "fragment"


def test_an_apartment_with_a_storage_cage_is_a_home():
    rows = [S.row("v2:aria", lot_number=None, product_type="Apartment", bedrooms=2,
                  lot_address="4 413 2 Bed 2 Bath, 1 Car 2C.1 SW Single Car Space Storage "
                              "Cage 78.7 8.3 87 $1,030,000", price=1030000.0)]
    assert _packages(rows)["v2:aria"]["verified"]


# ------------------------------------------------------------------- fresh, and stale

def _list(prefix, n, day, **kw):
    """n lots of one E-Agent price list read on `day`."""
    base = dict(source_doc="doc-" + prefix, estate_name=prefix.title() + " Rise",
                date_checked=day)
    base.update(kw)
    return [S.row("v2:%s-%02d" % (prefix, i), lot_number=str(100 + i),
                  lot_address="Lot %d, %s Rise" % (100 + i, prefix.title()),
                  line_key="k-%s-%02d" % (prefix, i), price=600000.0 + 1000 * i, **base)
            for i in range(n)]


def test_a_lot_its_own_list_was_re_read_without_is_not_a_verified_result():
    """AVIA Homes QLD: the 06/10 read of the list no longer carried lot 28's 27/09
    package. The read was whole (10 lots then, 10 now)."""
    rows = _list("avia", 10, "15/09/2026") + [
        S.row("v2:avia-old", lot_number="28", lot_address="Lot 28, Avia Rise",
              source_doc="doc-avia", estate_name="Avia Rise", date_checked="13/09/2026",
              line_key="k-avia-old", price=876800.0)]
    pk = _packages(rows)
    assert _hidden_as(pk["v2:avia-old"]) == "document_reread_without_row"
    assert all(pk["v2:avia-%02d" % i]["verified"] for i in range(10))


def test_a_partial_re_read_hides_nothing():
    """2 lots read last night of a list that held 10 the night before: a read cut short,
    not 8 lots sold."""
    rows = _list("part", 8, "13/09/2026") + [
        S.row("v2:part-new-%d" % i, lot_number=str(900 + i), source_doc="doc-part",
              lot_address="Lot %d, Part Rise" % (900 + i), estate_name="Part Rise",
              date_checked="15/09/2026", line_key="k-part-new-%d" % i)
        for i in range(2)]
    pk = _packages(rows)
    assert all(pk["v2:part-%02d" % i]["verified"] for i in range(8))


def test_a_proxima_project_is_not_judged_by_the_shared_index_page():
    rows = _list("prox", 10, "15/09/2026", source_channel="Proxima") + [
        S.row("v2:prox-old", source_channel="Proxima", source_doc="doc-prox",
              lot_number="555", lot_address="Lot 555, Prox Rise", estate_name="Prox Rise",
              date_checked="13/09/2026", line_key="k-prox-old")]
    assert _packages(rows)["v2:prox-old"]["verified"]


def test_a_list_the_builder_replaced_is_not_a_verified_result():
    """Hudson Homes QLD on 6 Oct: 35 lots of a list last read 24/09 while the builder's
    new upload was read every night. Only E-Agent re-lists that way."""
    rows = (_list("hudson-old", 5, "05/09/2026", builder_name="Lineage Homes")
            + _list("hudson-new", 5, "15/09/2026", builder_name="Lineage Homes")
            + _list("alone", 5, "05/09/2026", builder_name="Only One List Homes"))
    pk = _packages(rows)
    assert all(_hidden_as(pk["v2:hudson-old-%02d" % i]) == "builder_documents_reread"
               for i in range(5))
    assert all(pk["v2:hudson-new-%02d" % i]["verified"] for i in range(5))
    # A builder whose only list has not been read for days: nothing says it was replaced.
    assert all(pk["v2:alone-%02d" % i]["verified"] for i in range(5))
    # On a builder portal the same shape is a package page the grid did not reach.
    portal = [dict(r, source_channel="Direct Builder Portal (live)") for r in rows]
    pk = _packages(portal)
    assert all(pk["v2:hudson-old-%02d" % i]["verified"] for i in range(5))


def test_a_re_upload_in_the_same_folder_replaces_the_old_file():
    """Victoria & Albert, Broadbeach: 'VandA Pricelist' (builder 'Vanda', read 22/09) and
    'Victoria & Albert - Sales List - 22 Sep' (builder 'Victoria & Albert 22 20 Pm', read
    24/09) in one Dropbox folder."""
    old = _list("vanda", 6, "05/09/2026", builder_name="Vanda", source_folder="f-va")
    new = _list("va", 6, "07/09/2026", builder_name="Victoria & Albert 22 20 Pm",
                source_folder="f-va")
    pk = _packages(old + new)
    assert all(_hidden_as(pk["v2:vanda-%02d" % i]) == "newer_upload_in_folder"
               for i in range(6))
    assert all(pk["v2:va-%02d" % i]["verified"] for i in range(6))


def test_a_folder_is_hashed_from_the_link_and_never_published_raw():
    url = ("https://www.dropbox.com/scl/fo/db9kwomup9qkyxo5c9ifo/AAA/VandA%20Pricelist.pdf"
           "?rlkey=abc&dl=1")
    folder = C.source_folder_of({"source_url": url})
    assert folder and "db9kwomup9qkyxo5c9ifo" not in folder and len(folder) == 16
    assert C.source_folder_of({"stocklist_file": "https://www.dropbox.com/scl/fo/"
                                                 "db9kwomup9qkyxo5c9ifo/h?rlkey=x"}) == folder
    assert C.source_folder_of({"source_url": "https://e-agent.example/a.pdf"}) == ""
    assert "source_folder" in C.SNAPSHOT_FIELDS
    assert "source_folder" in C._COMPUTED_SNAPSHOT_FIELDS


def test_an_older_capture_of_a_lot_read_since_is_not_a_verified_result():
    """Ausbuild Lot 25 Honour Place: $1,626,800 read 24/09 beside $1,594,400 read 06/10.
    Another suburb's lot 25 under the same list heading is another lot."""
    common = dict(builder_name="Ausbuild", estate_name="Ausbuild Stock List.",
                  lot_number="25", source_channel="digital email")
    rows = [S.row("v2:old25", date_checked="08/09/2026", price=1626800.0,
                  lot_address="Lot 25, Honour Place", source_doc="doc-a1", **common),
            S.row("v2:new25", date_checked="15/09/2026", price=1594400.0,
                  lot_address="Lot 25, Honour Place", source_doc="doc-a2", **common),
            S.row("v2:other25", date_checked="08/09/2026", price=1601000.0,
                  lot_address="Lot 25, Other Street", source_doc="doc-a3",
                  suburb="Werribee", **common)]
    pk = _packages(rows)
    assert _hidden_as(pk["v2:old25"]) == "newer_capture_exists"
    assert pk["v2:new25"]["verified"]
    assert pk["v2:other25"]["verified"]


def test_a_unit_two_portal_projects_disagree_about_is_shown_to_confirm():
    unit = "Lot 259 Unit B-1702, 30 Ellen Street, WOLLONGONG, NSW, 2500"
    common = dict(source_channel="Proxima", lot_number="259", lot_address=unit,
                  suburb="Wollongong", state="NSW", source_doc="doc-proxima-index",
                  date_checked="15/09/2026")
    rows = [S.row("v2:b1702-793", source_project_id="793", price=740000.0,
                  estate_name="Building B", availability_status="For Sale", **common),
            S.row("v2:b1702-161", source_project_id="161", price=755000.0,
                  estate_name="Building A", availability_status="Not Available", **common)]
    pk = _packages(rows, dict(S.BRIEF, state="NSW"))
    shown = pk["v2:b1702-793"]
    assert not shown["verified"] and "conflicting_capture" in shown.get("confirm", [])
    assert shown.get("confirm_guard") is None, "a disagreement is for a person to confirm"


def test_a_row_with_no_state_takes_its_own_address_before_its_lists():
    """The "1 Part & Under Construction" list: Merbein and Tarneit lots, all VIC, and one
    "Lot64HillStreet ... MurrayBridge,SouthAustralia,5253" with no state, which borrowed VIC
    from its neighbours and sat on the VIC shortlist (found by the nothing-wrong check,
    repair round 1)."""
    vic = [S.row("v2:merbein-%d" % i, lot_number=str(10 + i), source_doc="doc-1part",
                 suburb="Merbein", estate_name="Merbein,Victoria,3505",
                 lot_address="Lot %d Foster Street" % (10 + i)) for i in range(6)]
    murray = S.row("v2:murray", state=None, suburb="Murray Bridge", lot_number="64",
                   source_doc="doc-1part", estate_name="MurrayBridge,SouthAustralia,5253",
                   lot_address="Lot64HillStreet $853,000.00 501 223 4 $900.00 Oct",
                   price=853000.0)
    assert "v2:murray" not in _packages(vic + [murray])
    assert "v2:murray" in _packages(vic + [murray], dict(S.BRIEF, state="SA"))
    assert C.state_named_in_text(murray) == "SA"
    # A street or a design named after a state is not an address.
    assert C.state_named_in_text({"lot_address": "12 Victoria Street, Tarneit"}) == ""
    assert C.state_named_in_text({"lot_address": "Queensland 2000 Collection"}) == ""
    # Written with full-width commas, as that list writes it.
    assert C.state_named_in_text({"estate_name": "Merbein\uff0cVictoria\uff0c3505"}) == "VIC"


# ------------------------------------------------------------------- one lot, two cards

def _dedupe(rows, brief=None):
    packages, _ = C.build_packages(dict(brief or S.BRIEF), rows, today=TODAY)
    merged = {}
    kept = C.dedupe_packages(packages, rows, merged_into=merged)
    return {p["content_hash"] for p in kept}, merged


def test_one_line_of_one_list_read_twice_is_one_card():
    """Celia St, Leppington: the list read with and without a heading glued on as its
    estate -- '32 Celia St' and '32 Celia St, G-Floor room Updated', one line, one price."""
    common = dict(source_doc="doc-celia", lot_number=None, line_key="k-32-celia",
                  price=1230000.0, suburb="Leppington", state="NSW", builder_name="")
    rows = [S.row("v2:c1", lot_address="32 Celia St", estate_name="Agent Stock List",
                  date_checked="14/09/2026", **common),
            S.row("v2:c2", lot_address="32 Celia St, G-Floor room Updated",
                  estate_name="G-Floor room Updated", date_checked="15/09/2026", **common)]
    kept, merged = _dedupe(rows, dict(S.BRIEF, state="NSW"))
    assert kept == {"v2:c2"} and merged == {"v2:c1": "v2:c2"}


def test_two_lines_carded_alike_on_one_list_stay_two_cards():
    """'30 Celia St, G-Floor room Updated' is printed for the 4-bed and the 5-bed design,
    at one price: two lines, two packages."""
    common = dict(source_doc="doc-celia", lot_number=None, price=1230000.0,
                  suburb="Leppington", state="NSW", builder_name="",
                  lot_address="30 Celia St, G-Floor room Updated",
                  estate_name="G-Floor room Updated")
    rows = [S.row("v2:ctm4", line_key="k-ctm4", bedrooms=4, **common),
            S.row("v2:ctm5", line_key="k-ctm5", bedrooms=5, **common)]
    kept, _ = _dedupe(rows, dict(S.BRIEF, state="NSW"))
    assert kept == {"v2:ctm4", "v2:ctm5"}


def test_one_lot_on_e_agent_and_in_an_email_is_one_card():
    """Hattan lot 3208 Olivine: the E-Agent line and the sub-$650k email, $331,000 land and
    $368,990 build on both; the email states no land size and no bedrooms."""
    rows = [S.row("v2:ea3208", builder_name="Hattan Homes", lot_number="3208",
                  lot_address="Lot 3208, Olivine", estate_name="Olivine",
                  suburb="Donnybrook", price=699990.0, land_price=331000.0,
                  build_price=368990.0, source_doc="doc-hattan", line_key="k-ea"),
            S.row("v2:em3208", builder_name="Hattan Homes", lot_number="3208",
                  lot_address="Lot 3208, Hattan", estate_name="Hattan",
                  suburb="Donnybrook", price=699990.0, land_price=331000.0,
                  build_price=368990.0, land_sqm=None, bedrooms=None,
                  source_channel="digital email", source_doc="doc-email", line_key="k-em")]
    kept, merged = _dedupe(rows)
    assert len(kept) == 1, kept


def test_two_packages_on_one_lot_at_one_price_stay_two_cards():
    """Goldstate lot 5710, Piebald St: Atlanta 23 (206 m2) and Trinidad 23 (210 m2), both
    $712,000, two lines of one list. They were folded into one card, and the parity check
    named the Trinidad merged into a different package."""
    common = dict(builder_name="Goldstate Homes", lot_number="5710", price=712000.0,
                  lot_address="5710 Piebald St, Tarneit VIC 3029", estate_name="Pair",
                  source_doc="doc-gold")
    rows = [S.row("v2:atlanta", house_sqm=206.0, line_key="k-atlanta", **common),
            S.row("v2:trinidad", house_sqm=210.0, line_key="k-trinidad", **common)]
    kept, _ = _dedupe(rows)
    assert kept == {"v2:atlanta", "v2:trinidad"}


def test_two_lot_numbers_are_two_lots_whatever_the_line_shares():
    """Verv prints lots 542 and 550 of Fraser Rise as '1056 Fraser Rise, Brochure Masterplan'
    at one price: the stored line is the same, the lots are not."""
    line = "1056 Fraser Rise Brochure Masterplan 2-Part Detached SS West 350 $727,155 4 2 2"
    common = dict(builder_name="Verv Projects", price=727155.0, lot_address=line,
                  estate_name="Brochure Masterplan", source_doc="doc-verv")
    rows = [S.row("v2:542", lot_number="542", line_key="k-542", **common),
            S.row("v2:550", lot_number="550", line_key="k-550", **common)]
    kept, _ = _dedupe(rows)
    assert kept == {"v2:542", "v2:550"}


# ------------------------------------------------------------------- the ranking

def test_at_equal_scores_the_fresher_read_ranks_first():
    from kommo_agent import KommoPropertyResearchAgent
    base = {"builder_name": "AVIA Homes", "lot_address": "Lot 28, The Hills Collection",
            "suburb": "Glenvale", "state": "QLD", "house_design": "House & Land",
            "bedrooms": 4, "bathrooms": 2, "car_spaces": 2, "land_size_sqm": 448.0,
            "house_size_sqm": 200.0, "advertised_package_price": 880000.0,
            "verified": True, "lot_number": "28", "date_checked": "",
            "source_channel": "E-Agent - database snapshot"}
    older = dict(base, content_hash="v2:old-28", age_days=9, date_checked="27/09/2026")
    newer = dict(base, content_hash="v2:new-28", age_days=0, date_checked="06/10/2026")
    agent = KommoPropertyResearchAgent()
    brief = {"client_name": "T", "state": "QLD", "budget_max": 9007199254740991,
             "primary_suburbs": []}
    processed, _rejected = agent.score_candidates(brief, [older, newer])
    assert [c.content_hash for c in processed] == ["v2:new-28", "v2:old-28"], [
        (c.content_hash, c.scoring.total_score) for c in processed]
