"""A row's state over many nights -- the multi-night harness, reduced (29 Sep 2026, round 3).

tests/test_state_invariant.py holds ONE night to origin/main 31d13ef: never a state origin/main
would not give on the same stored columns, bar (i)-(iv). Both reviews of round 2 then found
what that cannot see, because on the next night "the same stored columns" are this branch's
own answer from the night before:

  * LINEAGE FLIPS. Rows round 1 correctly gives their page's state, read with a guessed
    suburb ('suburb + e-agent page'), were open to origin/main's row-text check on every
    later night, and moved the first night a word of their line was stocked in another
    state: Verv 18833 "... 817 Balmain Donnybrook ..." (VIC page) to NSW once a Balmain
    NSW listing arrived, Silkwood 9299 "... Morayfield North Harbour Estate ... Balwyn
    192 ..." (QLD page) to VIC on Balwyn, Verv 16203 "613 Little Springs Deanside" to WA.
    origin/main never moved them -- their sheets were national, so they had no state.
  * OSCILLATION. guess_suburb weighed a reading assuming a settled row keeps its state,
    and pass 2 then re-opened it on that very reading: "Grange West 56 Werribee Titled
    400" went VIC, blank, VIC, blank.

The scratch harness this is cut down from (stateA3/fixer/mn/: mn_build.py, mn_run.py,
mn_metrics.py) runs the REAL enrich of origin/main and of this branch, each on its own
previous night's output, for nine nights over tonight's 23,448 rows plus 10,600 generated
ones (and a second corpus of 15,800), with the catalogue stocking every design, facade and
estate word that is a place, then every one-state place any row writes; a sheet's page
hints dropped for a night and back; status words changed. On c27f9dc it found 51 rows
changing more than once, 33 going back and forth, 854 later flips off a page and the row's
own reading, and the generated rows' wrong states growing from 1,552 on night 1 to 2,410 by
night 9. Now 0, 0 and 0 on both corpora, with origin/main's single-night bound held every
night, and the wrong states falling from 1,501 to 1,277 (origin/main 2,268 to 2,171).

ROUND 3'S REVIEW (29 Sep 2026) ran the same kind of harness with other perturbations -- a
vocabulary word stocked in ONE random state, half of all stock sold on a later night, a
second hint drop -- and found what the nights above never did:
  * rows going back and forth as stock came and went: "LOT 1856 TAIT STREET, SUMMER HILL
    CREEK, Plumbago" NSW, blank, NSW in a national no-page sheet (the reading was weighed as
    in a sheet that is not national, and with tonight's stock); "937 Banksia Rise, Maitland
    Vale" SA, NSW, SA as one Maitland Vale NSW listing came and went; "Lot 632 Kennedy SG
    Corner Whitlam" ACT, blank, ACT;
  * the label-tail pair exception moving rows whose page is RIGHT, the night the pair's
    other name was stocked: "Lot 5, Balmain Richmond" VIC -> NSW, "Lot 5313, Rosevale
    Acacia Ridge" TAS -> QLD -- 385 of 387 such moves wrong in one corpus;
  * no-page rows moved later where origin/main's own rows never are: "Lot 1334, Underwood
    Mount Collins Leigh" NSW 'suburb' -> WA, origin/main's NSW resting on its proof;
  * a catch lost: a fresh "Lot 11950, Warralily Armstrong Creek" off the QLD page stayed
    QLD where origin/main moves it to VIC on the Armstrong inside it;
  * file-name states origin/main never reads ("Stock_List_QLD_Sep.xlsx").
The fixes are in enrich_buildings (decider, page_and_guess_hold's ONCE, THEN HELD, the
postcodes a night clears, origin/main's proof of the same state, state_code_in_filename).

This keeps the named rows of all those findings and a small generated corpus, for eight
nights, through the real enrich_buildings.main():
  night 1, 2  nothing changes          night 3  every one-state place any row writes is
  night 4     some sheets lose their             stocked, in its state
              page hint                 night 5  the hints are back
  night 6     a status word changes     night 7  the night-3 stock is sold (superseded)
  night 8     nothing changes
and checks, on every night:
  (1) no row whose own columns did not change changes state more than once over nights
      2-8, nor comes back to a state it left;
  (2) no row whose page states its state, with its suburb -- its column, or read off its
      own words -- a place of that state, is moved to another state by a place name (bar a
      state the row writes);
  (3) the single-night bound of tests/test_state_invariant.py;
  (4) each named row lands where it should.
"""
import json
import os
import re
import sqlite3

import pytest

import config
import enrich_buildings
import state_resolver as sr
from database import ResearchDatabase
from geo import SuburbGeoIndex
from tests import _state_corpus as corpus
from tests.test_state_invariant import (_COLS, _stated_postcode, _states_stated,
                                        _violations)

STATES = corpus.STATES
_TOK = re.compile(r"[A-Za-z][A-Za-z'\-]*|\d+(?:\.\d+)?")

# (label, line, suburb column, page, stored state, stored signal, estate, postcode, lot,
#  file, the states it may hold on every night -- but a night its page hint is missing,
#  for a sheet whose rows rest on the page alone)
_NAMED = [
    # review round 2 (verify-2, verify-3): the lineage flips
    ("2-Part Detached SS North 817 Balmain Donnybrook Dec-26 352 $380,000 Elara 18 4 / 2 / 2 "
     "$388,950 $768,950 Brochure", None, None, "VIC", None, None, None, None, None, "verv-vic",
     {"VIC"}),
    ("PR8821 1292 Morayfield North Harbour Estate September 2026 407 $630,000 $411,467 "
     "$1,041,467 $770 3.84% Balwyn", "PR8821 1292 Morayfield North Harbour Estate September "
     "2026 407 $630,000 $411,467 $1,041,467 $770 3.84% Balwyn 192 4/2/2/2 Finance Considered",
     None, "QLD", None, None, "Morayfield North Harbour", None, None, "silkwood-qld", {"QLD"}),
    ("2-Part Dual Key SS West 613 Little Springs Deanside Titled 400 $445,000 Vanta 23 Nest 1 "
     "= 3 + 2 + 2", None, None, "VIC", None, None, None, None, None, "verv-vic2", {"VIC"}),
    ("2-Part Detached SS West 816 Bohnock Lancelin Dec-26 260 $541,498 Chelsea 10.5 3 / 2 / 2",
     None, None, "WA", None, None, None, None, None, "verv-wa-dropped", {"WA"}),
    # a design deep in the line, of a state the page's suburb is a place of too
    ("PR8555 3017 Palmerston Wellington Point Estate September 2026 316 $729,622 $770 3.84% "
     "Rochedale South 192 4/2/2/2 Finance Considered", None, None, "NT", None, None,
     "Palmerston Wellington Point", None, None, "silkwood-nt", {"NT"}),
    ("2672 Stage 12 417m2 $450,395 September 2027 Wellington Point Newstead 180m2 4 2 2 1 "
     "$450,395 Volos Available", None, None, "TAS", None, None, None, None, None, "avia-tas",
     {"TAS"}),
    # the catch that must still happen: an estate and its locality in the label, page wrong
    ("Lot 27102, Windermere Mambourin", "Lot 27102 Bounty X Inv/OO 400 sqm 184 sqm 4 2 1 2 "
     "$340,000 $401,900 $741,900 Titled Available", None, "QLD", None, None, None, None, None,
     "prosperity-qld", {"VIC"}),
    # the oscillations
    ("Grange West 56 Werribee Titled 400", "Grange West 56 Werribee Titled 400 $380,000",
     None, None, None, None, None, None, None, "grange", {"VIC"}),
    ("Glen Waverley Lot 7035 Hume Coastal", None, None, None, "VIC", "listing locality", None,
     None, None, "glen-waverley", {"VIC"}),
    ("5545 Nautical Circuit, The Grove", None, None, "VIC", "TAS", "suburb", "The Grove", None,
     None, "the-grove", {"VIC", ""}),
    ("Lot 9671, Eddystone Point", "9671 Second Avenue Baldivis Kennedy 182 - Fortitue "
     "Available 4 2 2 354 $400,437", "Baldivis", None, None, None, None, "9671", None,
     "baldivis", None),
    ("Lot 865 Verona DG Aintree", None, None, None, "NSW", "listing locality", None, None,
     "865", "verona-aintree", None),
    # a fresh copy of tonight's catch 18659 (SA once Murray Bridge is stocked, and not QLD
    # on the Murray of Murray Bridge once Murray is), and a sheet read once without its page
    # (NSW on its suburb column that night, VIC again once the hint is back)
    ("Murray Bridge, South $853,000.00 501 m2 223 m2 2 2 $900.00 October 2026 Available", None,
     None, None, None, None, "Australia, 5114", None, None, "murray-bridge", {"", "SA"}),
    ("2-Part Detached SS Southeast 114 Crestwood Cranbourne 315 $426,000", None, "Crestwood",
     "VIC", None, None, None, None, None, "crestwood-dropped", {"VIC"}),
    # round 3's review (verify-0, verify-1), 29 Sep 2026: the label-tail pair on a RIGHT
    # page, its other name stocked on night 3 -- held on the page (15-18)
    ("Lot 5, Balmain Richmond", None, None, "VIC", None, None, None, None, None,
     "pair-vic", {"VIC"}),
    ("Lot 1225, Bundaberg Sturt", None, None, "SA", None, None, None, None, None, "pair-sa",
     {"SA"}),
    ("Lot 5313, Rosevale Acacia Ridge", "Lot 5313 Bounty X Inv/OO 392 sqm 184 sqm 4 2 1 2 "
     "$398,000 Titled Available", None, "TAS", None, None, None, None, None, "pair-tas",
     {"TAS"}),
    ("Lot 2245, Kingston Wittenoom Hills", "Lot 2245 Arcadia Inv/OO 392 sqm 165 sqm 4 2 1 2 "
     "$398,000 Titled Available", None, "TAS", None, None, None, None, None, "pair-tas2",
     {"TAS"}),
    # tonight's catches 9565 and 9568, fresh and in their pre-move state, in files that are
    # not national: VIC from the first night, as origin/main moves them (19-22)
    ("Lot 11950, Warralily Armstrong Creek", "Lot 11950 Bounty X Inv/OO 392 sqm 184 sqm 4 2 "
     "1 2 $398,000 $395,900 $793,900 Q1 27 Available Brochure Link", None, "QLD", None, None,
     "Warralily Armstrong Creek", None, None, "armstrong-fresh", {"VIC"}),
    ("Lot 11950, Warralily Armstrong Creek", "Lot 11950 Bounty X Inv/OO 392 sqm 184 sqm 4 2 "
     "1 2 $398,000 $395,900 $793,900 Q1 27 Available Brochure Link", None, "QLD", "QLD",
     "suburb + e-agent page", "Warralily Armstrong Creek", None, None, "armstrong-premove",
     {"VIC"}),
    ("Lot 27177, Windermere Mambourin", "Lot 27177 Bounty X Inv/OO 400 sqm 184 sqm 4 2 1 2 "
     "$340,000 Titled Available", None, "QLD", "QLD", "suburb + e-agent page", None, None,
     None, "windermere-premove", {"VIC"}),
    ("Lot 1364, Murray Bridge", "Available Truganina Titled House 2538 350 12.5 Denver 17 "
     "Single Storey 4 2 2 $402,000 $365,350 $767,350", None, None, None, None,
     "Murray Bridge", None, None, "murray-truganina", {"VIC"}),
    # the oscillations (23-27): a listing of the label's place arrives and is sold; a stored
    # state the reading had nothing to say about; no-page two-place rows
    ("937 Banksia Rise, Maitland Vale", "2-Part Detached SS West 937 Banksia Rise Maitland "
     "Vale 522 $1361,863 Drake 10.5 3 / 2 / 2 $359,180", None, "SA", None, None, None, None,
     None, "maitland", None),
    ("Bell Bay 918 Muluckine Titled 430", "Bell Bay 918 Muluckine Titled 430 $516,569", None,
     None, "WA", "listing locality", None, None, None, "bell-bay", None),
    # (QLD once the design Kennedy SG is stocked in QLD: origin/main's check moves it, and
    # with no page nothing lets the row keep ACT against it -- one move, never back)
    ("Lot 632 Kennedy SG Corner Whitlam", None, None, None, "ACT", "suburb", None, None, "632",
     "whitlam", {"ACT", "QLD"}),
    ("Lot 133 Allendale North Coopers Plains Cura", "Lot 133 Allendale North Coopers Plains "
     "Cura $395,494", None, None, "QLD", "listing locality", None, None, None, "coopers",
     {"QLD"}),
    ("Lot 1334, Underwood Mount Collins Leigh", None, None, None, None, None, None, None, None,
     "underwood", {"NSW"}),
    # a place name against a state the row writes, and a column its page agrees with (28-31)
    ("LOT 4508 KELLY STREET, GIRRAL 2175, Grassy Spur", "LOT 4508 KELLY STREET, GIRRAL 2175",
     None, "TAS", None, None, None, "2175", None, "girral", {"TAS", "NSW", ""}),
    ("LOT 54 BOWERBIRD CRESCENT, ELLENBROOK 6502, Boambee East", "LOT 54 BOWERBIRD CRESCENT, "
     "ELLENBROOK 6502", None, "WA", None, None, None, None, None, "ellenbrook", {"WA"}),
    ("LOT 58 MONTANA CRES, DIGGERS REST 3427, Verona Sands", "LOT 58 MONTANA CRES, DIGGERS "
     "REST 3427", None, None, None, None, None, None, None, "diggers", {"", "VIC"}),
    ("Lot 460, Balwyn Yamba", "Cameron Park Lot 460 Balwyn Yamba 224 $259,411", "Yamba",
     "NSW", "NSW", "suburb", None, None, None, "yamba", {"NSW"}),
    # the second run of the reviews' harnesses (32-36): a guess its page agrees with, read
    # another way by the reading it refused; a guessed 'suburb' its page agrees with; a
    # stored state its address names after its street, or only its estate contradicts (WA
    # and TAS once Baudin and Burns Creek are stocked: origin/main's check, and no page)
    ("68 Hillier Circuit, Estate : Woodlands Nagambie | West Lamington", "68 Hillier Circuit "
     "640 16 40 $271,000 2027-04-01 00:00:00 Unity 26 6 3 2 $479,500 $750,500 Brochure "
     "Available West Lamington 20", None, "VIC", None, None, "Estate : Woodlands Nagambie",
     None, None, "hillier", {"VIC"}),
    ("Lot 4384, Windermere | Shepparton South", "Available White Rock Titled House Lot 4384 "
     "350 12.5 Gower 20 Single Storey 4 2 2 $426,570", None, "QLD", "QLD", "suburb", None, None,
     None, "shepparton", {"QLD"}),
    ("Lot 41, Baudin Beach", "41 Fishburn Crescent Bald Knob Verona 26 - Padington Available "
     "4 2 2 629 $1206,801", None, None, "QLD", "suburb", "Baudin Beach", None, None, "baudin",
     {"QLD", "WA"}),
    ("Lot 1768, Emu Downs", "11 1103&1104 3H N 221 20.7 241.7 $4,730,000 $21,403 available",
     None, None, "VIC", "listing locality", "Emu Downs", None, None, "emu-downs", {"VIC"}),
    ("Lot 943 Burns Creek Wyee Baker", "Lot 943 Burns Creek Wyee Baker $299,993", None, None,
     "NSW", "suburb", None, None, None, "burns-creek", {"", "NSW", "TAS"}),
    # the reviews' page-only and label rows that went blank -> page -> elsewhere, or to the
    # label's own locality later, as the places in them were stocked (37-42)
    ("Lot 926, Pentland Hills", "Available Ormeau Titled House Lot 926 350 12.5 Gower 20 "
     "Single Storey 4 2 2 $426,570", None, "QLD", None, None, None, None, None, "pentland",
     {"QLD", "VIC"}),
    ("Lot 5031, Harmony | Preston Beach", None, None, "VIC", None, None, None, None, None,
     "preston", {"VIC", "WA"}),
    ("21 Forest Road, Bushy Park Williamstown North", None, None, "QLD", None, None, None,
     None, None, "bushy-park", None),
    ("Lot 4329, Marion Bay Munno Para West", None, None, "TAS", None, None, None, None, None,
     "marion-bay", None),
    ("Lot 812, Gretna Norwood", None, None, "SA", None, None, None, None, None, "gretna",
     None),
    ("2-Part Detached SS North 9 Tambo Crossing Balah 283 $412,179 Eildon 190m2 3 / 2 / 2",
     None, None, None, None, None, None, None, None, "tambo", None),
    # 24 Oct 2026, APLACE's lists (tests/test_aplace_sections.py): its two Queensland lots,
    # live VIC on the page alone, QLD on their address from the first night; a Victorian lot
    # the harvest stored QLD with no signal, VIC on its own words; and pages that are right
    # against a bare place word -- Goldstate's Mandalay estate, Verv 19094's Jubilee (43-47)
    ("1524 Gilchrist Street, BRISBANE WEST", "Dawn 1524 Gilchrist Street Dawn Nov-26 392 "
     "Rectangular $485,000 Murray A 3 200.63 4 | 2 | 2 $373,520 $858,520 EOI Received", None,
     "VIC", "VIC", "e-agent page (conflicting signals)", "BRISBANE WEST", None, None,
     "aplace-gilchrist", {"QLD"}),
    ("10 New Road, BRISBANE NORTH", "Joyner 10 New Road Park Lane Dec-26 199 Rectangular "
     "$380,000 TERRACE Type C 239.31 4 | 3.5 | 2 $510,000 $890,000 EOI Received", None, "VIC",
     "VIC", "e-agent page (conflicting signals)", "BRISBANE NORTH", None, None, "aplace-joyner",
     {"QLD"}),
    ("143 Hadar Way, VIC", "Clyde North 143 Hadar Way Alma Jun-27 294 10.5 x 28 $397,000 Mackay "
     "01 167.08 4 | 2.5 | 2 $344,300 $716,300 Aplace Exclusive", "Bundaberg", "VIC", "QLD", None,
     "VIC", None, None, "aplace-hadar", {"VIC"}),
    ("5708 Piebald St, Mandalay", "Mar-27 5708 Piebald St North West 12.5 28 350 $334,000 Turin "
     "23 Panorama 4 Single 210 $372,000 $706,000 Available", None, "VIC", "VIC",
     "e-agent page (conflicting signals)", "Mandalay", None, "5708", "mandalay", {"VIC"}),
    ("2-Part Detached SS West 4327 Jubilee Wyndhamvale 263 $338,000 Enzo 10.5 3 / 2 / 2",
     "2-Part Detached SS West 4327 Jubilee Wyndhamvale 263 $338,000 Enzo 10.5 3 / 2 / 2 "
     "$347,050 $685,050 Brochure Masterplan Stage 43 - POS Available", None, "VIC", "VIC",
     "e-agent page (conflicting signals)", None, None, "4327", "jubilee", {"VIC"}),
]
_ENDS = {13: "SA"}          # and where they stand after night 8
_EXEMPT = {14: {4}}         # the night the Crestwood sheet has no hint
# a generated sheet whose national vote followed its rows' own stored answers (it was
# national, not, national over three nights)
_SHEET = [
    ("Lot 923, Hume Weir", "Lot 923 Unit B2911, 10-14 Hobart Street, KALKIE, QLD, 4110",
     "Vineyard", "4110", None),
    ("Lot 508, Glenvale (Lot 22)", None, None, None, None),
    ("Available Hervey Bay Custom 60 6 Somerset F | Zeta 9.66", None, None, None, "9"),
    ("6 Perkins Avenue, Upper Coomera", "6 Perkins Avenue Upper Coomera Somerset 191 - "
     "Newstead Available 4 2 2 582 $765,803", "Newstead", None, None),
    ("Available Titled Townhouse 8 215 10.86 Alder DG | Willow 15.72 3 2 1 $388,990", None,
     "Contemporary", None, None),
    ("Lot 36 Hurstville Rise Estate Donnybrook", None, "Donnybrook", None, None),
    ("Nirranda East Lot 6199 Gama Hamptons", None, None, "6199", None),
    ("Lot 307 Lillico Kennedy Newstead 193m2", None, None, None, "307"),
    ("Lot 70, Pinnacle Morisset Lucca 2314", "Lot 70, Pinnacle Morisset Lucca NSW 2314", None,
     "2314", None),
    ("Lot 571 Cunyarie Cypress Fortitue 186m2", None, None, None, None),
]
_SHEET_URL = "https://drive.example/export?id=470&dn=NSW%20Dual%20SEP.xlsx"
# 24 Oct 2026: Verv's [VG] email (tests/test_document_locations.py), all 114 rows as the
# harvest stored them -- the Jubilee lots with their lot number in the postcode column -- in
# one page-less file read by its own Location column: VIC on every night.
_VG_URL = "email:Fwd: [VG] - Projects | House & Land | Dual Key Packages"


def _vg_email():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                        "verv_vg_email.json")
    with open(path, encoding="ascii") as f:
        return json.load(f)["rows"]
# Round 3's review: a no-page sheet two of whose rows prove two states -- national -- and
# its two-place rows, which went back and forth as the places in them were stocked and sold
_TWO_PLACE = [
    "LOT 1856 TAIT STREET, SUMMER HILL CREEK, Plumbago",
    "Trayning 272 Eurimbla Titled 607",
    "LOT 110 SOCKET DR, HIGHLAND PLAINS, Ainslie",
    "Enzo 800 Mount Napier Titled 535",
    "St George Ranges Lot 646 Mackay Traditional",
    "LOT 210 TAYLORS RISE, WALTER HILL, Whitlam",
    "LOT 390 DAMSELFLY WAY, MANLY EAST, Moomin",
    "Lot 7, 12 Main Street, Tarneit VIC",
    "Lot 8, 3 Beach Road, Kalkie QLD",
]
# stocked from the first night, as the real catalogue stocks them
_STOCK0 = (("Mambourin", "VIC"), ("Armstrong", "VIC"), ("Truganina", "VIC"))
_STATUS = (("Available", "Sold"), ("Sold", "Available"), ("REGISTER NOW", "Available"))


@pytest.fixture(scope="module")
def geo():
    return SuburbGeoIndex()


def _row(k, **cols):
    r = {c: None for c in _COLS}
    r.update(id=k, source_channel="Generated", content_hash=f"v2:mn{k:06d}", price=500000.0,
             last_seen="29/09/2026", builder_name="Night Plant")
    r.update(cols)
    return r


def _corpus(geo):
    """The named rows, the sheet, and 400 generated ones -- half each in its own file, half
    in sheets of ten sharing a page."""
    rows, want = [], {}
    for k, (label, line, col, page, st, sig, est, pc, lot, f, ok) in enumerate(_NAMED, 1):
        rows.append(_row(k, lot_address=label, source_text=line or label, suburb=col,
                         source_state_hint=page, state=st, state_source=sig, estate_name=est,
                         postcode=pc, lot_number=lot, stocklist_file=f"night:{f}",
                         source_url=f"night:{f}"))
        if ok is not None:
            want[k] = ok
    k = len(rows)
    for label, line, col, pc, lot in _SHEET:
        k += 1
        rows.append(_row(k, lot_address=label, source_text=line or label, suburb=col,
                         postcode=pc, lot_number=lot, stocklist_file=None,
                         source_url=_SHEET_URL))
    for label in _TWO_PLACE:
        k += 1
        rows.append(_row(k, lot_address=label, source_text=f"{label} $646,681",
                         stocklist_file="night:two-place", source_url="night:two-place"))
    for v in _vg_email():
        k += 1
        rows.append(_row(k, lot_address=v["lot_address"], source_text=v.get("source_text"),
                         estate_name=v.get("estate_name"), postcode=v.get("postcode"),
                         source_url=_VG_URL))
    rng_page = STATES + (None,)
    for j, rec in enumerate(corpus.generate(geo, 400, seed=30092026)):
        k += 1
        sheet = f"night:gen-sheet-{j // 10}" if j >= 200 else f"night:gen-{j}"
        page = rng_page[(j // 10) % len(rng_page)] if j >= 200 else rec["source_state_hint"]
        rows.append(_row(k, lot_address=rec["lot_address"], source_text=rec["source_text"],
                         suburb=rec["suburb"], estate_name=rec["estate_name"],
                         source_state_hint=page, postcode=rec["postcode"],
                         lot_number=rec["lot_number"], state=rec["state"],
                         state_source=rec["state_source"], stocklist_file=sheet,
                         source_url=sheet, builder_name="Gen Builder"))
    for name, st in _STOCK0:
        k += 1
        rows.append(_stock(k, name, st))
    return rows, want


def _stock(k, name, st):
    addr = f"Unit 4, 20 Harbour Street, {name.upper()}, {st}"
    return _row(k, builder_name="Harvest Stock", lot_address=addr, source_text=addr,
                suburb=name, state=st, state_source="listing text", source_state_hint=st,
                stocklist_file=f"night:stock:{st}", source_url=f"night:stock:{st}")


def _one_state_places(rows, geo):
    """{(name, state)}: every 1-3 word span of any row's text that is a place of one state."""
    out = set()
    for r in rows:
        for text in (r["source_text"] or "", r["lot_address"] or ""):
            toks = [t.rstrip("-'") for t in _TOK.findall(text)]
            for size in (1, 2, 3):
                for i in range(len(toks) - size + 1):
                    span = toks[i:i + size]
                    if any(not t or t[0].isdigit() or len(t) < 2 for t in span):
                        continue
                    name = " ".join(span)
                    states = [s for s in geo.states_for_suburb(name) if s in STATES]
                    if len(states) == 1 and len(name) >= 5:
                        out.add((name.title(), states[0]))
    return out


def _run_night(tmp_path, monkeypatch, rows, night):
    db = tmp_path / f"night{night}.db"
    ResearchDatabase(db)
    conn = sqlite3.connect(str(db))
    for r in rows:
        conn.execute(f"INSERT INTO buildings ({', '.join(_COLS)}) "
                     f"VALUES ({', '.join('?' for _ in _COLS)})", [r[c] for c in _COLS])
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DATABASE_PATH", db)
    assert enrich_buildings.main([]) == 0
    conn = sqlite3.connect(str(db))
    try:
        return {i: (s, src, pc) for i, s, src, pc in
                conn.execute("SELECT id, state, state_source, postcode FROM buildings")}
    finally:
        conn.close()


def _nights(tmp_path, monkeypatch, geo):
    """Eight nights. Returns (rows as night 8 leaves them, {id: [state night 0..8]},
    {id: nights its own columns changed}, want, the bound's violations, the later flips)."""
    rows, want = _corpus(geo)
    named = len(_NAMED)
    history = {r["id"]: [(r["state"] or "")] for r in rows}
    own = {}
    hidden = {}
    drop = {r["id"] for r in rows if (r["stocklist_file"] or "").endswith("-dropped")
            or (r["stocklist_file"] or "").startswith("night:gen-sheet-")
            and int(r["stocklist_file"].rsplit("-", 1)[1]) % 3 == 0}
    bound_bad, flips = [], []
    night3 = []
    for night in range(1, 9):
        if night == 3:
            k = max(r["id"] for r in rows)
            stocked = {(str(r["suburb"]).lower(), r["state"]) for r in rows
                       if r["builder_name"] == "Harvest Stock"}
            for name, st in sorted(_one_state_places(rows, geo)):
                if (name.lower(), st) not in stocked:
                    k += 1
                    rows.append(_stock(k, name, st))
                    night3.append(rows[-1])
        if night == 7:
            for r in night3:
                r["superseded_by"] = "v2:sold"
        if night == 4:
            for r in rows:
                if r["id"] in drop and r["source_state_hint"]:
                    hidden[r["id"]] = r["source_state_hint"]
                    r["source_state_hint"] = None
                    own.setdefault(r["id"], set()).update({4, 5})
        if night == 5:
            for r in rows:
                if r["id"] in hidden:
                    r["source_state_hint"] = hidden[r["id"]]
        if night == 6:
            for j, r in enumerate(rows):
                if r["builder_name"] == "Harvest Stock" or j % 2:
                    continue
                for a, b in _STATUS:
                    if a in (r["source_text"] or ""):
                        r["source_text"] = r["source_text"].replace(a, b, 1)
                        r["lot_address"] = (r["lot_address"] or "").replace(a, b, 1)
                        own.setdefault(r["id"], set()).add(6)
                        break
        before = [dict(r) for r in rows]
        got = _run_night(tmp_path, monkeypatch, rows, night)
        bad, _changed = _violations(before, got, geo)
        bound_bad += [(night,) + b for b in bad]
        for r in rows:
            prev = (r["state"] or "")
            r["state"], r["state_source"], r["postcode"] = got[r["id"]]
            cur = r["state"] or ""
            history.setdefault(r["id"], [""] * night).append(cur)
            # (2): off its page and its own suburb -- its column, or its reading -- by a name
            page = sr.normalise_state(r["source_state_hint"])
            if (night > 1 and prev and cur and cur != prev and page == prev
                    and night not in own.get(r["id"], ())):
                column = (r["suburb"] or "").strip()
                reading = column or sr.suburb_in_row_text(r["lot_address"] or "", geo,
                                                          prefer=page, estate=r["estate_name"])
                if prev in geo.states_for_suburb(reading or "") and cur not in (
                        _states_stated(r, geo) | {_stated_postcode(r, geo)}) and not (
                        column and cur in geo.states_for_suburb(column)):
                    flips.append((night, r["id"], prev, cur, reading, r["lot_address"]))
    return rows, history, own, want, bound_bad, flips, named


def _changes(history, own, ids):
    """(changing more than once over nights 2-8, coming back over nights 1-8) on nights
    without an own change -- rows whose own columns never changed, for the second."""
    multi, back = [], []
    for i in ids:
        h, o = history[i], own.get(i, set())
        changes = [k for k in range(2, len(h)) if k not in o and h[k] != h[k - 1]]
        if len(changes) > 1:
            multi.append((i, h))
        if not o:
            seen = []
            for v in h[1:]:
                if not seen or v != seen[-1]:
                    if v in seen:
                        back.append((i, h))
                        break
                    seen.append(v)
    return multi, back


def test_a_row_changes_state_at_most_once_over_eight_nights(tmp_path, monkeypatch, geo):
    rows, history, own, want, bound_bad, flips, named = _nights(tmp_path, monkeypatch, geo)
    ids = [r["id"] for r in rows if r["builder_name"] != "Harvest Stock"]
    multi, back = _changes(history, own, ids)
    assert multi == [], multi[:5]
    assert back == [], back[:5]
    assert flips == [], flips[:5]
    assert bound_bad == [], bound_bad[:5]
    wrong = [(i, _NAMED[i - 1][0][:50], history[i], sorted(ok)) for i, ok in want.items()
             if any(h not in ok for k, h in enumerate(history[i])
                    if k and k not in _EXEMPT.get(i, ()))]
    assert wrong == [], wrong
    assert {i: history[i][-1] for i in _ENDS} == _ENDS
    vg = [r["id"] for r in rows if r["source_url"] == _VG_URL]
    assert len(vg) == 114
    assert [(i, history[i]) for i in vg if any(h != "VIC" for h in history[i][1:])] == []
    # and the nights did something: the stock moved rows, the dropped hints changed some
    moved = sum(1 for i in ids if len(set(history[i][1:])) > 1)
    assert moved > 10, moved


def _flipped_named(tmp_path, monkeypatch, geo):
    _rows, history, _own, want, _bb, flips, _n = _nights(tmp_path, monkeypatch, geo)
    wrong = [i for i, ok in want.items() if any(h not in ok for h in history[i][1:])]
    return wrong, flips


def test_the_harness_sees_the_lineage_flips(tmp_path, monkeypatch, geo):
    """A silent check reads as a pass: let the row-text check move a row off its page and
    its own reading again (round 2's rule) and the flips are back."""
    monkeypatch.setattr(enrich_buildings, "page_and_guess_hold", lambda *a, **k: False)
    wrong, flips = _flipped_named(tmp_path, monkeypatch, geo)
    assert {1, 2, 3} <= set(wrong), wrong
    assert len(flips) >= 3, flips


def test_the_harness_sees_the_pair_move_a_right_page(tmp_path, monkeypatch, geo):
    """Round 3's review: without PAGE_HELD_SIGNAL, the label-tail pair's other name stocked
    on night 3 moves rows whose page is right ("Lot 5, Balmain Richmond" to NSW)."""
    monkeypatch.setattr(enrich_buildings, "names_the_page_cannot_tell", lambda *a, **k: False)
    wrong, flips = _flipped_named(tmp_path, monkeypatch, geo)
    assert {15, 16} <= set(wrong), wrong
    assert len(flips) >= 2, flips


def test_the_harness_sees_a_dropped_page_move_a_row(tmp_path, monkeypatch, geo):
    """Read a row with its hint missing as a row with no page (round 2's reading) and the
    Verv WA row the check moves on the night the hint is gone stays moved."""
    monkeypatch.setattr(enrich_buildings, "page_of", lambda r: sr.normalise_state(
        r["source_state_hint"] if "source_state_hint" in r.keys() else ""))
    wrong, _flips = _flipped_named(tmp_path, monkeypatch, geo)
    assert 4 in wrong, wrong


@pytest.mark.parametrize("text, want", [
    ("Available Regents Park Dc Lot 7, Redhead Street, Jetsonville Acacia - Haven $1,318,500",
     {"TAS"}),
    ("Lot 12 Unit 3.04, 10 DARLING STREET, BALMAIN, NSW", {"NSW"}),
    ("Available Clyde North Q3 2026 Townhouse 3673 168 6 Embla D | Ceduna 9.66", set()),
    ("Available Wyndham Vale Q3 2026 Townhouse 413 168 6 Westgarth E | Mambourin Green", set()),
])
def test_the_place_a_row_writes_after_its_street(geo, text, want):
    """Round 3 review: round 1's longer name ("Regents Park Dc") must yield to a one-state
    place the row writes after its street (Jetsonville, TAS) -- and round 1's own Hattan
    rows write none."""
    assert sr.places_after_a_street(text, geo) == want


def test_the_longer_name_yields_to_the_address(geo):
    line = ("Available Regents Park Dc Lot 7, Redhead Street, Jetsonville Acacia - Haven "
            "$1,318,500 Laurent Active PK19551")
    r = _row(1, lot_address=line, source_text=line, source_url="night:x")
    assert enrich_buildings.longer_name_stands(r, "Regents Park Dc", "Regents Park", geo,
                                               set()) == ""


@pytest.mark.parametrize("url, want", [
    ("https://x/export?id=1&dn=NSW%20Dual%20SEP%20NEW%20(1).xlsx", "NSW"),
    ("https://x/export?id=1&dn=DUAL%20QLD%20SEP.xlsx", "QLD"),
    ("https://www.dropbox.com/scl/fi/abc/VIC SQ Price List 23.03.2026.pdf?rlkey=x&dl=1", "VIC"),
    ("email:RE: Enquiry: Lot 1052, Westwood, Fraser Rise VIC", "VIC"),
    ("https://x/export?id=1&dn=Act%20Now%20Stock%20List.xlsx", ""),
    ("https://x/export?id=1&dn=Sales%20wa%20update.xlsx", ""),
    ("https://x/export?id=1&dn=QLD%20NSW%20Combined.xlsx", ""),
    ("https://x/sa/stock.pdf", ""),
    # round 3's review: a code origin/main's case-blind word boundary does not read there,
    # or reads as another state
    ("https://files.example/p/Stock_List_QLD_Sep.xlsx", ""),
    ("https://drive.example/export?id=9&dn=VIC_Stock.xlsx", ""),
    ("https://files.example/p/NSW2026.pdf", ""),
    ("https://drive.example/export?id=9&dn=Act%20of%20Sale%20NT.pdf", ""),
])
def test_a_file_name_state_is_a_standalone_code(url, want):
    """Round 3: a file-name state is taken only as one capitalised code in the file's own
    name -- every file-name state of the 29 Sep copy is one (12 files) -- not a word read
    case-blind anywhere in the URL, nor a name giving two; and only where origin/main reads
    that same code (round 3's review), so it is never a state origin/main would not give."""
    got = enrich_buildings.state_code_in_filename(url)
    assert got == want
    assert not got or got == enrich_buildings.state_from_filename(url)


@pytest.mark.parametrize("label, one, want", [
    ("Lot 27102, Windermere Mambourin", "Windermere", "Mambourin"),
    ("Lot 11950, Warralily Armstrong Creek", "Armstrong Creek", "Warralily"),
    ("Lot 5, Balmain Richmond", "Richmond", "Balmain"),
    ("Lot 5, Richmond", "Richmond", ""),
    ("Lot 4384, Windermere | Shepparton South", "Windermere", "Shepparton South"),
    ("5629 The Grove Tarneit", "Tarneit", ""),
])
def test_the_other_half_of_a_label_pair(label, one, want):
    assert sr.label_tail_partner(label, one) == want


@pytest.mark.parametrize("label, line, page, reading, exposed", [
    # the pair's other name is a one-state place of a state the reading is a place of too
    ("Lot 5, Balmain Richmond", None, "VIC", "Richmond", True),
    ("Lot 27102, Windermere Mambourin", "Lot 27102 Bounty X Inv/OO 400 sqm", "QLD",
     "Windermere", True),
    # the name the check reads lies inside the reading
    ("Lot 11950, Warralily Armstrong Creek", "Lot 11950 Bounty X Inv/OO 392 sqm", "QLD",
     "Armstrong Creek", True),
    # a reading of one state, or a page it is no place of: nothing the page cannot tell
    ("Lot 5, Wyndham Vale Jubilee", None, "VIC", "Wyndham Vale", False),
    ("Lot 5, Balmain Richmond", None, "WA", "Richmond", False),
    # the line writes the pair's other name itself: the check's own business
    ("Lot 5, Balmain Richmond", "Lot 5 Balmain Estate Richmond 400", "VIC", "Richmond",
     False),
])
def test_which_rows_are_held_on_their_page(geo, label, line, page, reading, exposed):
    """Round 3's review: the rows written 'suburb (page-held) + e-agent page' once the check
    has been asked about them -- the pair's shapes the words cannot settle."""
    r = _row(1, lot_address=label, source_text=line or label, source_state_hint=page)
    assert enrich_buildings.names_the_page_cannot_tell(r, page, reading, geo) is exposed


def test_a_held_page_is_not_a_suburb_match_to_either_check():
    signal = enrich_buildings.PAGE_HELD_SIGNAL
    assert not enrich_buildings.rests_on_a_suburb_match(signal, False)
    r = _row(1, state="QLD", state_source=signal)
    assert enrich_buildings.page_of(r) == "QLD"      # a missing hint reads the page from it
    assert signal.startswith("suburb")               # the nightly replay still checks it


@pytest.mark.parametrize("pc, text, want", [
    ("1639", "Blaxland 1639 Lancelin Titled 249", ""),
    ("8123", "Lot 5 Tarneit 8123 Available", ""),
    ("3427", "LOT 58 MONTANA CRES, DIGGERS REST 3427", "VIC"),
    ("2175", "LOT 4508 KELLY STREET, GIRRAL 2175", "NSW"),
])
def test_a_large_volume_receiver_is_no_lots_postcode(geo, pc, text, want):
    assert sr.postcode_stated(pc, [text], geo) == want
