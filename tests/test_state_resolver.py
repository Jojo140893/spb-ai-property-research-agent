"""
Tests for resolving a listing's state.

Coleen, 28 July: "we should see the state. That's one of the main things." Only 406 of
4,308 harvested rows had one, because builders' stocklists rarely spell it out — each
file covers one region and assumes you know.

Every fixture below is a real stored row. Two of them are the reason the resolver decides
by agreement rather than by a fixed ranking: cross-checking the signals against each
other on the live data showed each one can be wrong alone.
"""

from geo import SuburbGeoIndex
from state_resolver import (file_is_national, locality_in_listing, normalise_state,
                            own_state, resolve_state, state_from_postcode,
                            state_named_in_listing)

_GEO = SuburbGeoIndex()


def test_postcode_ranges_cover_every_state():
    cases = {
        "3000": "VIC", "3338": "VIC", "8000": "VIC",          # Melbourne + LVR
        "2000": "NSW", "2328": "NSW", "1010": "NSW",
        "4000": "QLD", "4207": "QLD", "9000": "QLD",
        "5000": "SA", "5114": "SA",
        "6000": "WA", "6164": "WA",
        "7000": "TAS",
        "0800": "NT", "0810": "NT",                            # leading zero
        "2600": "ACT", "2617": "ACT", "2900": "ACT", "0200": "ACT",
        # the boundary either side of the ACT block inside NSW's 2xxx range
        "2599": "NSW", "2619": "NSW", "2620": "NSW", "2921": "NSW",
    }
    for pc, want in cases.items():
        assert state_from_postcode(pc) == want, f"{pc} -> {state_from_postcode(pc)}, want {want}"
    # not postcodes
    for junk in ("", None, "12", "12345", "abcd", "0000"):
        assert state_from_postcode(junk) == "", repr(junk)


def test_normalise_state_accepts_what_the_files_actually_say():
    for raw, want in {"vic": "VIC", " NSW ": "NSW", "Victoria": "VIC",
                      "queensland": "QLD", "South Australia": "SA",
                      "BRISBANE NORTH QLD": "QLD", "": "", "Nowhere": ""}.items():
        assert normalise_state(raw) == want, f"{raw!r} -> {normalise_state(raw)!r}"


def test_a_misparsed_postcode_does_not_move_a_listing_interstate():
    """Real rows: lots in Clyde North and Truganina — plainly Victorian, and off E-Agent's
    Victoria page — carry 1028, 1030 and 2209, which are NSW codes. A four-digit number in
    the row was read as a postcode. Ranking postcode first would have exported them as NSW."""
    for bad_pc, suburb in (("1028", "Clyde North"), ("1030", "Clyde North"),
                           ("2209", "Truganina")):
        state, signal = resolve_state(postcode=bad_pc, suburb=suburb,
                                      page_state="VIC", geo=_GEO)
        assert state == "VIC", f"{suburb} with postcode {bad_pc} -> {state} ({signal})"
        assert "suburb" in signal and "page" in signal, signal


def test_a_region_name_in_the_suburb_column_does_not_win_on_its_own():
    """Real rows: the QLD stocklists put "LOGAN" in the suburb column. The locality
    dataset has a Logan in Victoria and nowhere else, so the suburb alone answers VIC for
    a Queensland lot. The postcode and the page both say QLD, and they carry it."""
    assert _GEO.states_for_suburb("LOGAN") == ["VIC"], "fixture no longer reproduces"
    state, signal = resolve_state(postcode="4207", suburb="LOGAN",
                                  page_state="QLD", geo=_GEO)
    assert state == "QLD", f"-> {state} ({signal})"
    assert "postcode" in signal and "page" in signal, signal


def test_an_unambiguous_suburb_resolves_on_its_own():
    assert _GEO.states_for_suburb("Shepparton") == ["VIC"]
    assert resolve_state(suburb="Shepparton", geo=_GEO) == ("VIC", "suburb")
    assert resolve_state(suburb="Winter Valley", geo=_GEO) == ("VIC", "suburb")


def test_a_shared_locality_name_is_decided_by_the_page_it_came_from():
    """"Springfield" exists in six states. On its own it must decide nothing."""
    assert len(_GEO.states_for_suburb("Springfield")) > 1
    assert resolve_state(suburb="Springfield", geo=_GEO) == ("", "")
    state, signal = resolve_state(suburb="Springfield", page_state="QLD", geo=_GEO)
    assert (state, signal) == ("QLD", "suburb + e-agent page")


def test_when_an_unambiguous_suburb_fights_the_page_the_page_wins():
    """Settled from the data, not from first principles. Exactly 8 of 4,308 rows have an
    unambiguous suburb that contradicts the page, and in every one the SUBURB is the wrong
    field:

      * "Jubilee" on the Victoria page, builder Hattan Homes — a Victorian estate name
        stored in the suburb column, which the locality dataset only knows as a QLD place.
      * "Price" on the Queensland page — a column-header word captured as a suburb, which
        happens to be a real locality in South Australia.

    The page state was read off E-Agent's own navigation, so it is the sturdier of the two.
    It is labelled as contested so the export never hides the disagreement.
    """
    state, signal = resolve_state(suburb="Jubilee", page_state="VIC", geo=_GEO)
    assert state == "VIC", f"-> {state} ({signal})"
    assert "conflicting" in signal, signal
    state2, signal2 = resolve_state(suburb="Price", page_state="QLD", geo=_GEO)
    assert state2 == "QLD" and "conflicting" in signal2, (state2, signal2)
    # ...and a real postcode agreeing with the page settles it outright, no longer contested
    state3, signal3 = resolve_state(postcode="3029", suburb="Jubilee", page_state="VIC",
                                    geo=_GEO)
    assert state3 == "VIC", (state3, signal3)
    assert "postcode" in signal3 and "conflicting" not in signal3, signal3


def test_the_page_alone_is_used_but_labelled_as_such():
    assert resolve_state(page_state="NSW", geo=_GEO) == ("NSW", "e-agent page")
    assert resolve_state(suburb="Nowhere At All", page_state="SA", geo=_GEO) == (
        "SA", "e-agent page")


def test_nothing_reliable_leaves_it_blank():
    assert resolve_state(geo=_GEO) == ("", "")
    assert resolve_state(suburb="Nowhere At All", geo=_GEO) == ("", "")
    # postcode and suburb conflict with nothing to break the tie -> refuse to choose
    state, signal = resolve_state(postcode="2000", suburb="Shepparton", geo=_GEO)
    assert state == "" and signal == "", (state, signal)


def test_a_real_postcode_carries_a_row_with_no_other_signal():
    assert resolve_state(postcode="4207", geo=_GEO) == ("QLD", "postcode")
    assert resolve_state(postcode="0810", geo=_GEO) == ("NT", "postcode")


def test_a_listing_that_names_its_own_state_is_read():
    """Real cells. Thomas Paul's file writes "<ESTATE> / <SUBURB> / <STATE>", Hudson's
    writes "<estate> <SUBURB> <STATE>, <suburb>". 167 stored rows name their state this way
    and the resolver read none of them, because a state was only ever taken from the page."""
    for cell, want in (("STAGE 2 / DORA CREEK / NSW", "NSW"),
                       ("KEARNEYS SPRING / QLD", "QLD"),
                       ("TOOWOOMBA / QLD", "QLD"),
                       ("NSW / WOONGARRAH / NSW", "NSW"),
                       ("Bethania QLD, Bethania", "QLD"),
                       ("Buchanan NSW, Buchanan", "NSW"),
                       ("Truganina VIC", "VIC"),
                       ("Evergreen Estate Bellbird Park QLD, Bellbird Park", "QLD")):
        got, _ = state_named_in_listing(cell)
        assert got == want, f"{cell!r} -> {got!r}, want {want}"


def test_a_listing_that_names_no_state_says_nothing():
    """Every one of these is a real stored cell. None of them may produce a state: a
    two-letter code appears inside ordinary words, and a lower-case one is not a label."""
    for cell in ("Street # Type", "7 Star Energy Rating", "Titled Packages",
                 "m²", "Residence", "DUPLEX", "Car Space * * *",
                 "LOT STREET DESIGN BEDS BATH TYPE LAND SLAB LAND PACKAGE RENT YIELD",
                 "Warnervale Display Homes 2026, Warnervale",   # locality, no state
                 "Highfields Estate, Denman",
                 "116 Neale Road", "122 Atlas Crescent", "N/A", "Wa", "Sanctuary",
                 "321 Cunningham Street", "Price $1,630,000 $4,700,000"):
        got, ev = state_named_in_listing(cell)
        assert got == "", f"{cell!r} -> {got!r} via {ev!r}"


def test_the_locality_is_taken_only_when_the_cell_has_a_separator():
    """The separator is the extractor's own record of "<context><sep><locality>". Without
    it the cell is one blob, and matching 17,500 locality names against a blob is how
    "122 Atlas Crescent" became Crescent SA and estate "Mandalay" became Mandalay QLD."""
    for cell, want in (("Highfields Estate, Denman", "Denman"),
                       ("Bethania QLD, Bethania", "Bethania"),
                       ("STAGE 9 / TOOWOOMBA", "TOOWOOMBA"),
                       ("KEARNEYS SPRING / QLD", "KEARNEYS SPRING"),
                       ("Leppington HomeWorld Display Homes, Leppington", "Leppington"),
                       ("Display Homes, Morayfield", "Morayfield")):
        assert locality_in_listing(cell) == want, f"{cell!r} -> {locality_in_listing(cell)!r}"
    # separator-less cells: the ones that produced every false positive
    for cell in ("Crescent", "Mandalay", "Jubilee", "Price", "Highlands", "Springs",
                 "The Grove", "Neale", "Lyra", "Hobart"):
        assert locality_in_listing(cell) == "", f"{cell!r} -> {locality_in_listing(cell)!r}"


def test_own_state_refuses_a_bare_postcode_and_a_bare_suburb_word():
    """own_state is the standard of proof for throwing away a whole file's hint, and the
    only signal left once a file is known to be national, so it holds out for more than a
    four-digit number or a lone word in the suburb column. Both are documented liars:
    "Warnervale 2026" is a titling date, and "118 Hobart Street" leaves Hobart in the
    suburb column of a NSW Central Coast builder's file."""
    assert own_state(postcode="2259", geo=_GEO) == ("", "")
    assert own_state(suburb="Hobart", address="118 Hobart Street", geo=_GEO) == ("", "")
    assert own_state(suburb="Greenway", address="54 Greenway Street", geo=_GEO) == ("", "")
    state, why = own_state(postcode="2259", suburb="Wadalba", geo=_GEO)
    assert (state, why) == ("NSW", "suburb + postcode"), (state, why)
    # Warnervale is NSW-only, so the separator-delimited locality answers on its own and
    # the 2026 sitting beside it takes no part.
    state2, why2 = own_state(postcode="2026", suburb="Warnervale Display Homes, Warnervale",
                             geo=_GEO)
    assert (state2, why2) == ("NSW", "listing locality"), (state2, why2)


def test_a_named_state_outranks_the_page_it_came_from():
    """Rows 2529 and 2536: a Toowoomba lot on E-Agent's New South Wales page. The row says
    QLD in its own cell, so it is QLD, and the signal records what it beat."""
    state, signal = resolve_state(page_state="NSW", listing_state="QLD", geo=_GEO)
    assert state == "QLD", (state, signal)
    assert "listing text" in signal and "NSW" in signal, signal
    # ...and where nothing disagrees, the signal is not dressed up as a conflict
    state2, signal2 = resolve_state(page_state="NSW", listing_state="NSW", geo=_GEO)
    assert (state2, signal2) == ("NSW", "listing text"), (state2, signal2)


def test_a_national_file_is_recognised_from_its_own_rows():
    """Hudson Homes' PDF, 149 rows, hangs on E-Agent's Queensland page. Its own rows name
    Wadalba, Warnervale, Bellbird and Denman NSW next to Yarrabilba and Flagstone QLD, so
    the page's claim is not true of the file, let alone of any row in it."""
    hudson = [own_state(suburb=s, geo=_GEO)[0] for s in
              ("Wadalba, Wadalba", "Warnervale, Warnervale", "Bellbird, Bellbird",
               "Yarrabilba, Yarrabilba", "Bethania QLD, Bethania")]
    assert set(hudson) == {"NSW", "QLD"}, hudson
    assert file_is_national(hudson)
    # a genuinely single-state file keeps its hint, blank rows and all
    vic = [own_state(suburb=s, geo=_GEO)[0] for s in
           ("Kilmore Estate, Kilmore", "Creekstone North, Tarneit", "Street # Type", "")]
    assert set(vic) == {"VIC", ""}, vic
    assert not file_is_national(vic)



def test_a_locality_in_the_rows_own_state_beats_one_earlier_in_the_text():
    """find_suburb_in_text placed a Wadalba NSW lot in Jensen QLD.

    The state loop was the INNERMOST of three, so the first candidate by text POSITION
    won and fell through to any state that happened to contain it. On a real NSW row:

        "Jensen Rise Estate - Wadalba 49 Road #5 Wadalba 2259 CENTRAL COAST COUNCIL"

    it returned "Jensen" -- an estate name, and a locality that exists only in QLD --
    while Wadalba, a real NSW suburb present twice in the same line, was never reached.
    The docstring already claimed it preferred the given state; it did not.

    This is not cosmetic: the value feeds distance filtering and scoring
    (kommo_agent.py:136-142), so the lot was geocoded to the wrong town.
    """
    from geo import SuburbGeoIndex

    geo = SuburbGeoIndex()
    if not geo.loaded:
        return                      # no suburb data in this environment; nothing to assert

    row = ("Available 23/04/2025 Jensen Rise Estate - Wadalba 49 Road #5 Wadalba "
           "2259 CENTRAL COAST COUNCIL NSW November 2026")
    assert geo.find_suburb_in_text(row, "NSW") == "Wadalba", "an estate name won again"
    assert geo.locate("Jensen", "NSW") is None, "fixture assumption: Jensen is not in NSW"

    # The case the docstring was written for still works, with and without a state.
    assert geo.find_suburb_in_text("LOT 79 STELLA ST, COLAC 3250", "VIC") == "Colac"
    assert geo.find_suburb_in_text("LOT 79 STELLA ST, COLAC 3250", "") == "Colac"


def test_an_estate_glued_to_a_locality_still_proves_its_state():
    """One E-Agent QLD stocklist, 73 rows, every one stamped QLD off the page hint --
    while its own cells read 'Windermere Mambourin', 'Coridale Lara' and 'Warralily
    Armstrong Creek', all Victorian.

    own_state could not see it because locality_in_listing requires a SEPARATOR, and
    these cells glue the estate to the locality with a space. So the file never looked
    national, the hint stood, and the two Warralily lots geocoded to Armstrong Creek
    QLD -- about 1,400 km from the Geelong estate they are actually in.
    """
    from geo import SuburbGeoIndex
    from state_resolver import file_is_national, locality_tail_in_cell, own_state

    geo = SuburbGeoIndex()
    if not geo.loaded:
        return

    assert locality_tail_in_cell("Windermere Mambourin", geo) == "Mambourin"
    assert locality_tail_in_cell("Coridale Lara", geo) == "Lara"
    assert own_state(suburb="Windermere Mambourin", geo=geo)[0] == "VIC"
    assert own_state(suburb="Riverbank Estate Caboolture", geo=geo)[0] == "QLD"

    # Which is what makes the file national, and voids its QLD page hint.
    assert file_is_national({"VIC", "QLD"})


def test_the_separator_rule_it_supplements_is_not_weakened():
    """Each of these is why locality_in_listing demands a separator in the first place.

    A single bare word must never qualify -- that is the 'Jubilee' / 'Mandalay' case,
    estate names and column headers sitting in the suburb column. And an LGA is not a
    locality: 'CITY OF LOGAN' is a Queensland council, while Logan is a locality in
    VICTORIA and nowhere else, so its tail proves VIC for a Queensland lot.
    """
    from geo import SuburbGeoIndex
    from state_resolver import locality_tail_in_cell, own_state

    geo = SuburbGeoIndex()
    if not geo.loaded:
        return

    for single in ("Jubilee", "Mandalay", "Price", "Woodstock"):
        assert locality_tail_in_cell(single, geo) == "", single

    for lga in ("CITY OF LOGAN", "Logan City Council", "COAST COUNCIL",
                "Moreton Bay Regional Council"):
        assert locality_tail_in_cell(lga, geo) == "", lga
        assert own_state(suburb=lga, geo=geo)[0] != "VIC", lga

    # A two-word locality must not lose its first word.
    assert locality_tail_in_cell("Mount Duneed", geo) == "Mount Duneed"


def test_an_estate_name_is_never_read_as_a_place():
    """The tail walk runs on the SUBURB cell only.

    Allowed on estate_name it manufactured false proofs: 'Redbank Plains Sienna Eden'
    answers Eden NSW for a lot in Redbank Plains QLD, and 'The Grove' answers Grove TAS.
    A false proof is worse than no proof here -- this is the standard used to throw away
    a whole file's hint.
    """
    from geo import SuburbGeoIndex
    from state_resolver import own_state

    geo = SuburbGeoIndex()
    if not geo.loaded:
        return

    assert own_state(suburb="SILKWOOD HOMES", estate="Redbank Plains Sienna Eden",
                     geo=geo) == ("", ""), "an estate tail was read as a place"
    assert own_state(suburb="10% Deposit", estate="The Grove", geo=geo) == ("", "")


def test_a_street_name_does_not_become_the_suburb():
    """"12 Windsor Street ... Woodford" was geocoded to Windsor.

    Windsor is a real locality, which is exactly why it won: the scan matched it on name
    alone and never looked at the word after it. Measured on live stock, 33 rows were
    placed in their own street this way — "612 Oxford Street ... Joyner",
    "2427 Cathcart Ave ... Tarneit", "Strathmore Street, Morayfield". resolve_locality
    already refuses this shape by taking the last comma-separated part; free text has no
    commas to lean on, so the street type itself has to be the signal.

    Demotion, not exclusion: a street-suffixed candidate is still returned when the row
    offers nothing else, because stripping the house number out of "Wadalba 49 Road"
    leaves a genuine suburb looking street-suffixed.
    """
    from geo import SuburbGeoIndex

    geo = SuburbGeoIndex()
    if not geo.loaded:
        return

    assert geo.find_suburb_in_text(
        "Lot 130, Ausbuild Stock List. Strathmore Street Lot 130, Strathmore Street, "
        "Morayfield Montrose", "QLD") == "Morayfield", "a street name won"

    # The demotion must not cost a recovery when the street-suffixed name is all there is.
    assert geo.find_suburb_in_text("49 Wadalba Road", "NSW") == "Wadalba", \
        "demoting a street-suffixed match must never drop it entirely"

    # And the two cases the function was written for still hold.
    assert geo.find_suburb_in_text("LOT 79 STELLA ST, COLAC 3250", "VIC") == "Colac"
    assert geo.find_suburb_in_text(
        "Available 23/04/2025 Jensen Rise Estate - Wadalba 49 Road #5 Wadalba "
        "2259 CENTRAL COAST COUNCIL NSW November 2026", "NSW") == "Wadalba"


def run_all():
    tests = [
        ("own-state locality beats text position",
         test_a_locality_in_the_rows_own_state_beats_one_earlier_in_the_text),
        ("a street name does not become the suburb",
         test_a_street_name_does_not_become_the_suburb),
        ("estate+locality proves its state",
         test_an_estate_glued_to_a_locality_still_proves_its_state),
        ("the separator rule is not weakened",
         test_the_separator_rule_it_supplements_is_not_weakened),
        ("an estate name is never a place",
         test_an_estate_name_is_never_read_as_a_place),
        ("postcode ranges cover every state", test_postcode_ranges_cover_every_state),
        ("state names normalise", test_normalise_state_accepts_what_the_files_actually_say),
        ("misparsed postcode does not move a lot", test_a_misparsed_postcode_does_not_move_a_listing_interstate),
        ("region name in suburb column loses", test_a_region_name_in_the_suburb_column_does_not_win_on_its_own),
        ("unambiguous suburb resolves alone", test_an_unambiguous_suburb_resolves_on_its_own),
        ("shared locality decided by page", test_a_shared_locality_name_is_decided_by_the_page_it_came_from),
        ("page wins a straight conflict", test_when_an_unambiguous_suburb_fights_the_page_the_page_wins),
        ("page alone used but labelled", test_the_page_alone_is_used_but_labelled_as_such),
        ("nothing reliable stays blank", test_nothing_reliable_leaves_it_blank),
        ("real postcode carries a lone row", test_a_real_postcode_carries_a_row_with_no_other_signal),
        ("a listing's own state is read", test_a_listing_that_names_its_own_state_is_read),
        ("no state means no state", test_a_listing_that_names_no_state_says_nothing),
        ("locality needs a separator", test_the_locality_is_taken_only_when_the_cell_has_a_separator),
        ("own_state refuses a bare postcode or suburb word", test_own_state_refuses_a_bare_postcode_and_a_bare_suburb_word),
        ("named state outranks the page", test_a_named_state_outranks_the_page_it_came_from),
        ("a national file is recognised", test_a_national_file_is_recognised_from_its_own_rows),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] state: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] state: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
