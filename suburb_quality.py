"""
What a row's suburb value actually IS, and where the answer came from.

The `suburb` column collects whatever landed in that position of a builder's stocklist.
On live stock: 2,907 rows hold a real locality, 2,693 hold nothing, and 2,328 hold
something that is not a place at all — '2026', 'offer', 'GARAGE', 'Logan City Council',
'New South Wales', 'IN TERNAL BALCONY TOTAL', 'Rooms Rooms m2 m2 m2'. In the published
snapshot 3,854 rows show one of those to Coleen as though it were a suburb.

WHY THIS IS A MODULE AND NOT AN UPDATE STATEMENT
------------------------------------------------
`suburb_norm` is one of the eight inputs to database.building_content_hash. Rewriting the
stored column changes the identity of every row it touches, so the next harvest INSERTS a
duplicate instead of updating in place — the mechanism that produced 777 duplicate
captures when builder_name was rewritten the same way. So the stored value is left
exactly as the source wrote it, and the answer is computed on the way out. That is the
same shape as build_web._display_name_canonicaliser for builder names, and it converges
by itself: a re-harvest through a fixed extractor writes the clean value at insert time.

WHAT IT WILL AND WILL NOT DO
----------------------------
Recovery is structural and state-checked, never a guess:

  * the value already IS a locality in the row's state            -> keep it (canonical spelling)
  * ...unless the row's own address contradicts it AND shows the
    value to be a STREET ('LOT 2950 LOCKINGTON RD, TARNEIT 3029'
    was published as Lockington, a town 200 km away)              -> the address wins
  * the value CONTAINS a locality, as the last comma/colon part or
    the longest tail ('Cloverton Estate , Kalkallo 3064')         -> use it   (626 live rows)
  * the row's own text names a locality immediately before a
    postcode, and that postcode belongs to the row's state        -> use it   (25 live rows)
  * the value is a locality NAME but the row records no state     -> show it, marked
                                                                     unchecked (706 rows)
  * anything else                                                 -> BLANK, with the reason

SHOWN IS NOT THE SAME AS LOCATED. 706 rows name a place with no state to check it
against. Blanking them all would throw away 'Mango Hill' x38 and 'Clyde North' x32, which
the source plainly did state; trusting them geocodes '122 Atlas Crescent' to the SA town
of Crescent. So they are displayed with the caveat and refused as a location. Callers
that need to geocode, distance-filter or benchmark must ask is_located(), never merely
whether the suburb is non-empty.

Blanking is the point, not a shortfall. The client's standing instruction is that a blank
with a stated reason beats a plausible guess, and this codebase has the scars to prove
it: estate-name recovery put a Wadalba NSW lot in Jensen QLD; banner substring matching
put a Wilton lot in Bingara, 500 km away. Both are held shut by tests, and neither is
reintroduced here.

Free-text scanning is deliberately NOT used to fill a suburb. Measured over the 953 rows
where it finds something, it returns the row's own STREET ('Windsor Street ... Woodford'),
its HOUSE DESIGN ('Newhaven', 'Montrose', 'Fernlea' are all Australian localities) or a
REGION HEADER ('BRISBANE NORTH' -> Brisbane) often enough that the result cannot be
published. geo.find_suburb_in_text keeps its street guard for callers that need a
best-effort geocode; it is not evidence of a suburb.
"""

from typing import Any, Dict, Optional, Tuple

import re

# geo is imported LAZILY, inside _index(), and must stay that way.
#
# geo does `from config import PROJECT_ROOT`, and config.py runs OUTPUT_DIR.mkdir() at
# module level. On Vercel that path is under the read-only /var/task, so the mkdir raises
# EROFS and the import dies — taking api/research.py with it and returning HTTP 500 for
# every search. api/_bootstrap wraps that one import in a tolerant mkdir, but only when
# IT imports config; a module that reaches config first, before bootstrap has done so,
# gets the raw failure. api/_candidates.py already imports geo lazily for exactly this
# reason, and importing it here at module level silently reintroduced the fault through
# the back door. Verified: this is what the deployed 500 was.
#
# address_label and state_resolver import nothing but `re` and `typing`, so they are safe
# at module level.
from address_label import display_suburb as _display_suburb
from state_resolver import state_from_postcode

# Where a displayed suburb came from. Stored alongside the value so a reader can always
# see whether it was stated or derived — the same contract as `state_source`.
STATED = "stated by the source"
FROM_COMPOSITE = "read from the composite the source stored"
FROM_POSTCODE = "read from the postcode in the row's own text"
# The suburb column named the row's own STREET, and its address named the suburb.
FROM_ADDRESS = ("read from the row's own address, which contradicted the suburb column")
# A locality NAME with no state to check it against. Shown, because the source really did
# say it and 'Mango Hill' x38 and 'Clyde North' x32 are plainly right — but never treated
# as a location, because it cannot be geocoded, distance-filtered or benchmarked, and
# because a fifth of Australian locality names exist in more than one state. Use
# is_located() rather than testing for a non-empty suburb.
UNCHECKED_NO_STATE = "stated by the source; no state recorded, so it could not be checked"
BLANK_NOT_A_PLACE = "the source's value is not a locality"
BLANK_NO_STATE = "no state recorded, so the value cannot be checked against a locality"
BLANK_ABSENT = "the source did not state one"
# The address named a locality even though no postcode did. Weaker evidence than a
# postcode-anchored read, which is why it runs last, and stronger than nothing at all:
# 4,230 live rows had no usable suburb and 2,873 of them had no value in the column to
# check, so every one of them was invisible to a suburb search.
FROM_ADDRESS_TAIL = "read from the locality named in the row's own address"
# The row's own source document says where the project is, on its front page. Used only
# where NOT ONE row on that document has a suburb between them — 1,857 live listings whose
# lot_address is a bare spec string ("001 $1,888,000 4BED 2 BATH 2 CAR 272SQM") with no
# estate and no sibling that knows better. Nothing in the database can name those; the
# price list they were read from can, and still answers at the URL they came from.
FROM_DOCUMENT = "read from the front page of the row's own source document"
# The source named a locality and misspelled it by exactly one character.
FROM_TYPO = "the suburb the source stated, corrected for a single-character misspelling"
# A hand-checked correction where the row's own STATE is the thing that was wrong.
FROM_VERIFIED = ("checked by hand against the development's published address; the "
                 "state recorded on the row was taken from the page it was listed on")
# NOT AUSTRALIAN. 633 live listings are Dubai and Manila off-plan apartments (DAMAC
# Islands, SAFA GATE, SMDC Sail/Sands/Gold/Jade/Glade Residences) sitting in the same
# pool as Melbourne house-and-land. They will never have an Australian suburb, so
# counting them as a suburb gap overstates the gap and hides that they are in there at
# all. Coleen sells Australian property; this is the honest label for the rest.
BLANK_OVERSEAS = "the listing is not in Australia"

# Reasons whose suburb may be used AS A LOCATION.
_LOCATED = (STATED, FROM_COMPOSITE, FROM_POSTCODE, FROM_ADDRESS,
            FROM_ADDRESS_TAIL, FROM_DOCUMENT, FROM_TYPO, FROM_VERIFIED)

# INFERRED, and deliberately not LOCATED.
#
# Ahsan, 21 Aug, after being shown the accuracy: fill the rest from their addresses, he
# wants every listing findable. These are the rows where nothing states a suburb, no
# postcode anchors one, the address is not address-shaped, no sibling agrees and the
# source document does not name one — so the only thing left is the longest locality
# appearing anywhere in the row's own text. Measured on holdout that is 83.8% to 86.8%
# right, against 97.2% for the address rule proper.
#
# The split between LOCATED and INFERRED is what makes that acceptable. A row here is
# SEARCHABLE, so Coleen finds it when she searches its suburb and it stops being missing
# stock. It is NOT located, so it is still refused by everything that needs the position
# to be true: distance filtering, benchmarking and comparables all gate on is_located and
# keep doing so. A wrong suburb costs a slightly noisy search result; a wrong comparable
# costs a client a wrong valuation.
INFERRED_FROM_TEXT = ("inferred from the row's own text - the source did not state a "
                      "suburb, so this is our best reading and may be wrong")
# UNCHECKED_NO_STATE joins them, for the same reason and with the same limits.
#
# These are rows where the SOURCE stated a real locality name — 'Kialla', 'Bald Hills',
# 'One Mile', 'The Gap', 'Toowoomba' — and the only thing missing is a state to check it
# against. That is strictly STRONGER evidence than INFERRED_FROM_TEXT, which is a name
# this app dug out of a listing's own text and which is already accepted as searchable.
# Dropping the stated ones while keeping the inferred ones was incoherent.
#
# Searchable, never located: the reason a state is required has not gone away. A fifth of
# Australian locality names exist in more than one state, so these cannot be geocoded,
# distance-filtered or benchmarked, and is_located() still says no.
# THE LAST RESORT, and the weakest thing this module will put a name to.
#
# Asked for on the standing instruction "if you can not find suburb assign them using
# their address i want all 100% in there fixed", after every evidence-based route was
# exhausted on the rows it covers. It is NOT evidence. Measured by holdout across the
# whole catalogue -- hide a row's known suburb, ask its siblings to name it:
#
#     the estate's other lots      92%   (already used, and LOCATED)
#     the builder's other lots     50%
#     the document's other lots    37%
#
# So roughly one row in two carries the wrong town under this reason. It is INFERRED,
# never located: is_located() stays false, so nothing here is geocoded, distance-filtered
# or benchmarked, and it cannot reach a client's comparables. It shows in the app with
# the inferred badge and in the client report with a caveat, which is the whole reason
# the two grades exist.
INFERRED_FROM_SIBLINGS = ("inferred from other listings by the same builder or on the "
                          "same document - the source did not state one and the row's "
                          "own text names no locality")

_INFERRED = (INFERRED_FROM_TEXT, UNCHECKED_NO_STATE, INFERRED_FROM_SIBLINGS)


def is_located(why: str) -> bool:
    """Whether resolve()'s answer is safe to geocode, distance-filter or benchmark."""
    return why in _LOCATED


def is_inferred(why: str) -> bool:
    """Whether the suburb is our reading of the row rather than the source's statement."""
    return why in _INFERRED


def is_searchable(why: str) -> bool:
    """Whether a suburb search should return this row.

    Wider than is_located on purpose. A listing whose suburb we inferred is worth finding
    — it is real stock at a real price, and leaving it out is what made Coleen report the
    app as missing properties it had. It is still not safe to benchmark, which is why
    the two questions are asked separately.
    """
    return is_located(why) or is_inferred(why)

# "<Place> <4-digit postcode>". The postcode is what makes this safe: on its own a
# capitalised word before a number matches lot numbers, years and floor areas. Live
# examples that only the state-range check rejects: 'Lot 2427, Newhaven' (a lot number),
# 'Raceview 2026-08-01' (a date), 'Fraser Rise 1106' (not a postcode at all).
_BEFORE_POSTCODE = re.compile(
    r"\b([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){0,2})\s+(\d{4})\b")

_TEXT_FIELDS = ("lot_address", "street_address", "source_text")
_MAX_TEXT = 700

_INDEX: Optional[Any] = None


def _index():
    global _INDEX
    if _INDEX is None:
        import geo as _geo_mod          # lazy on purpose — see the import note above
        _INDEX = _geo_mod.SuburbGeoIndex()
    return _INDEX


def _canonical_spelling(name: str, state: str, index) -> str:
    """The index's own spelling, so 'GLENVALE' and 'Glenvale' stop being two suburbs.

    13 localities are stored under two spellings across 164 live rows, and each pair
    splits the suburb facet, the distance lookup and the benchmark cohort in half.
    Case only — no word is added, removed or reordered.

    Taken from the locality dataset rather than .title() so the one authoritative source
    decides, and an improvement there propagates instead of being overridden here.
    """
    canon = getattr(index, "canonical_suburb", None)
    if callable(canon):
        out = canon(name, state)
        if out:
            return out
    return name.strip().title() if name.isupper() or name.islower() else name.strip()


def resolve(row: Dict[str, Any], index=None) -> Tuple[str, str]:
    """(suburb_to_display, why) for one row. Never raises, never guesses."""
    index = index or _index()
    # 56 Proxima rows hold suburb="New South Wales" because the address parser only knew
    # the abbreviated form and swallowed Wilton into the street. display_suburb rebuilds
    # it from the row's own comma-structured address. It runs first and its answer is
    # then validated here, which it never did for itself — it returned its candidate
    # untested, so a bad parse became a suburb.
    raw = str(_display_suburb(row) or row.get("suburb") or "").strip()
    state = str(row.get("state") or "").strip().upper()

    # A row that has ALREADY been through here keeps the reason it was given. The
    # deployed search reads the published snapshot, where the suburb is blanked and the
    # reason travels beside it in suburb_source — without this the coverage sentence
    # reported every one of them as "the source did not state one", collapsing "we
    # refused this value" and "there was never a value" into a single number and losing
    # the more useful half.
    carried = str(row.get("suburb_source") or "").strip()
    if not raw:
        if carried in (BLANK_NOT_A_PLACE, BLANK_NO_STATE, BLANK_OVERSEAS):
            return "", carried
    elif carried and (carried in _INFERRED or carried in _LOCATED):
        # A GRADE THIS FUNCTION GAVE, SURVIVING THE ROUND TRIP THROUGH THE SNAPSHOT.
        #
        # This is the line that makes the located/inferred split mean anything on the
        # deployed path, and without it the split was decorative. build_web writes the
        # RESOLVED suburb back into the published `suburb` column and puts the grade
        # beside it in suburb_source. The API then re-resolves each snapshot row — and
        # the first branch below sees a non-empty value that IS a locality in the row's
        # state and calls it STATED. Measured on the shipped snapshot: of 2,849 rows
        # published as INFERRED, 1,933 came back as "stated by the source" with
        # is_located() True, and 436 of those were live, available and priced.
        #
        # The consequence was not cosmetic. is_located() is the gate every consumer that
        # needs a TRUE position asks — benchmark_competitive, benchmark_market,
        # kommo_agent's distance scoring — so a suburb we had GUESSED was being used to
        # build comparables and suburb medians for a client report. 'Theodore' is an
        # apartment-type label on a Brisbane listing and a Banana Shire town 550 km away;
        # 'Magnolia' and 'Dalton 18' are house designs; 'Denver' came off a lot in
        # Montana estate. Each was benchmarked as a real location.
        #
        # suburb_source is only ever written by this function, so trusting it is trusting
        # our own earlier answer, not the source's claim about itself.
        # EVERY grade is carried, not only the weak ones. Carrying just the inferred
        # ones fixed the safety hole but flattened the rest: 1,337 rows read off their
        # own source document came back as "stated by the source", so the snapshot could
        # no longer tell a value a stocklist published from one this app read off a price
        # list. Both are located and neither is wrong -- but the provenance is the point.
        return _canonical_spelling(raw, state, index) if index.loaded else raw, carried

    if not index.loaded:                       # no locality data in this environment
        return raw, STATED if raw else BLANK_ABSENT

    # A HAND-CHECKED RULE THAT CONTRADICTS THE ROW'S STATE. The only path here that
    # overrides stored data, used where the state itself is the error -- see
    # document_suburb.override_for_row for the evidence each one carries.
    try:
        import document_suburb as _ds
        _sub, _st = _ds.override_for_row(row)
    except Exception:                              # missing module, unreadable rules
        _sub, _st = "", ""
    if _sub and _st and index.locate(_sub, _st):
        return _canonical_spelling(_sub, _st, index), FROM_VERIFIED

    # BEFORE any Australian reasoning, because none of it applies. These rows reach the
    # end of this function as "the source did not state one", which reads as a scraping
    # gap to be fixed; it is not. See BLANK_OVERSEAS.
    if not (raw and index.states_for_suburb(raw)) and is_overseas(row):
        return "", BLANK_OVERSEAS

    if raw and index.states_for_suburb(raw):
        # A real locality name. Still requires the state to agree when one is recorded:
        # 'LOGAN' is a locality in Victoria and nowhere else, and QLD stocklists put it
        # in this column, so accepting it on name alone geocodes a Queensland lot to
        # Victoria.
        if state and index.locate(raw, state):
            # The column value is a locality in this state — but so is the name of the
            # road. 'LOT 2950 LOCKINGTON RD, TARNEIT 3029' was published as Lockington,
            # a real Victorian town 200 km from the lot, because the spreadsheet put the
            # street's name in the suburb position. An ADDRESS is stronger evidence than
            # a spreadsheet column, so where the row's own address both contradicts the
            # column AND shows the column value to be a street, the address wins.
            #
            # Both conditions are required. Either alone would start overriding values
            # that are simply correct.
            anchor = _postcode_anchor(row, state, index)
            if anchor and anchor.lower() != raw.lower() and _looks_like_a_street(raw, row):
                return _canonical_spelling(anchor, state, index), FROM_ADDRESS
            return _canonical_spelling(raw, state, index), STATED
        if not state:
            # Nothing to check it against — 860 live rows. It is still reported, because
            # the source did say it and 'Mango Hill' x38 is obviously right, but it is
            # NOT a location: see UNCHECKED_NO_STATE.
            #
            # One check does not need a state, though, and it matters: a STREET is not a
            # suburb. 'Crescent' (a locality in SA) came from '122 Atlas Crescent' and
            # 'Paramatta' from '132 Paramatta Street', and both were published as places.
            if _looks_like_a_street(raw, row):
                return "", BLANK_NOT_A_PLACE
            return _canonical_spelling(raw, "", index), UNCHECKED_NO_STATE

    if not state:
        # A stateless row can still be named by its own document, and 400-odd of these
        # are exactly that: a spec-string lot on a price list that says on page one which
        # suburb it is selling. Checked here because the no-state return below is final.
        named = _document_suburb(row)
        # STILL HAS TO BE A PLACE. The main path below only ever returns FROM_DOCUMENT
        # after index.locate has agreed; this branch returned whatever the map held. The
        # map is built from localities so it should never differ — but "should never"
        # is what the rest of this module exists to stop trusting, and a hand-edited
        # entry in document_suburbs_manual.json reaches production through here.
        if named and index.states_for_suburb(named):
            return _canonical_spelling(named, "", index), FROM_DOCUMENT
        # And the last-resort read, for the same reason: this return is final, so a
        # stateless row would otherwise never reach it. It only accepts a nationally
        # unambiguous name — see _longest_locality_in_text.
        guess = _longest_locality_in_text(row, "", index)
        if guess:
            return _canonical_spelling(guess, "", index), INFERRED_FROM_TEXT
        return "", BLANK_NO_STATE if raw else BLANK_ABSENT

    if raw:
        inner = index.resolve_locality(raw, state)
        if inner:
            return _canonical_spelling(inner, state, index), FROM_COMPOSITE

    anchor = _postcode_anchor(row, state, index)
    if anchor:
        return _canonical_spelling(anchor, state, index), FROM_POSTCODE

    # A SINGLE-CHARACTER SLIP IN A VALUE THE SOURCE DID STATE, before falling back to
    # reading the address. See _typo_correction: 'Soutbank' is Southbank on 77 listings.
    if raw:
        corrected = _typo_correction(raw, state, index)
        if corrected:
            return _canonical_spelling(corrected, state, index), FROM_TYPO

    # LAST, and only once everything better has failed. A postcode-anchored read is
    # stronger evidence and runs above; this is what is left for the rows that have an
    # address and nothing else. See _address_locality for the measured accuracy and for
    # the three approaches that scored worse.
    tail = _address_locality(row, state, index)
    if tail:
        return _canonical_spelling(tail, state, index), FROM_ADDRESS_TAIL

    # THE DOCUMENT'S OWN FRONT PAGE, last of all. It is only ever consulted for rows that
    # have nothing else, because the map behind it is built only for source documents on
    # which not one row carries a suburb — see document_suburb.refresh_from_database.
    named = _document_suburb(row)
    if named and index.locate(named, state):
        return _canonical_spelling(named, state, index), FROM_DOCUMENT

    # THE LAST RESORT, and the only rule here that is allowed to be wrong. See
    # INFERRED_FROM_TEXT for why it exists and what is withheld from it.
    guess = _longest_locality_in_text(row, state, index)
    if guess:
        return _canonical_spelling(guess, state, index), INFERRED_FROM_TEXT

    return "", BLANK_NOT_A_PLACE if raw else BLANK_ABSENT


# ---------------------------------------------------------------------------------
# The locality named in the row's own address, when no postcode anchors it.
#
# Measured on 4,678 live rows that already carry a checked suburb: hide it, recover it,
# compare. This rule answers 3,267 of them at 97.2%, and a fair share of the residual
# "errors" are it CORRECTING the stored value — 'LOT 2950 LOCKINGTON RD, TARNEIT 3029'
# is stored as suburb 'Lockington', which is the street.
#
# Four things had to be true before it was safe. Each was measured, not assumed:
#
#   the state is required     93.5% -> unusable without it. 'Caboolture Central Springs'
#                             tails to Springs, a locality in Western Australia, for a
#                             Queensland lot.
#   an address gate           93.5% -> 96.7%. Half of lot_address is not an address at
#                             all but a flattened table row, and its tail is a HOUSE
#                             DESIGN: London, Madrid, Paddington, Ormond, Belmont and
#                             Bright are all builder design names AND real localities.
#   a street guard            96.7% -> 97.1%. '614 Buckley Avenue' is on Buckley Avenue,
#                             not in Buckley.
#   drop the parenthetical    97.1% -> 97.3%. 'ARMSTRONG CREEK (GEELONG)' names the
#                             suburb and then the region; right-to-left takes the region.
#
# What was tried and REJECTED, both measured worse:
#   source_text as a source   it is every column of a row glued together; it turned
#                             'Armstrong Creek ... Warralily' into Gower and 'Redbank
#                             Plains Eden's Crossing' into Tecoma.
#   rejecting any candidate   85.9%. Estates are usually named after the suburb they sit
#   matching the estate name  in ('Officer Central' in Officer), so this threw away far
#                             more right answers than the 'Exford Waters' -> Exford class
#                             of wrong ones it caught.
_PARENS = re.compile(r"\([^)]*\)")

# Marketing after the locality: 'KALKALLO - 7 STAR ENERGY RATING', 'MELTON - 5% DEPOSIT'.
_TAIL_MARKETING = re.compile(
    r"\s*[-–—]\s*(?:\d+\s*%|\$|\d+\s*star|titled|registered|sold|available|"
    r"deposit|fixed|turn\s*key|turnkey|house\s*&\s*land|h&l|spec|rebate|discount|save|"
    r"promo|offer|special|incentive|now\s|due\s|eta\s|settle).*$", re.I)

# One word that is never a locality on its own.
_NOT_ALONE = re.compile(
    r"^(lot|unit|apt|apartment|st|street|rd|road|dr|drive|ave|avenue|cres|crescent|"
    r"ct|court|pl|place|way|blvd|boulevard|pde|parade|tce|terrace|cl|close|gr|grove|"
    r"ln|lane|hwy|highway|cnr|corner|estate|stage|block|level|floor|land|only|house|"
    r"and|the|of|at|in|on|to|for|with|from|"
    r"available|sold|titled|registered|deposit|star|energy|rating|discount|rebate|"
    r"total|price|package|contract|part|detached|duplex|villa|townhouse|granny|flat|"
    r"bed|bath|car|garage|sqm|approx|tba|tbc|na|nil|inc|incl|gst|resale|owner|occupier)$",
    re.I)

_ADDR_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")

_STREET_WORDS = (
    r"st|street|rd|road|dr|drive|ave|avenue|av|cres|crescent|cct|circuit|cir|circle|"
    r"ct|court|pl|place|way|wy|blvd|boulevard|bvd|pde|parade|tce|terrace|cl|close|"
    r"gr|grove|gdns|gardens|ln|lane|hwy|highway|esp|esplanade|rise|view|walk|loop|"
    r"link|mews|green|ridge|chase|row|track|trail|bend|crest|glade|grange|pkwy|"
    r"parkway|prom|promenade|quay|retreat|square|sq|vista|wynd")
_HAS_STREET = re.compile(r"\b(" + _STREET_WORDS + r")\b\.?", re.I)
_STREET_TOKENS = frozenset(_STREET_WORDS.split("|"))

# Words that turn a locality name into the name of a THING IN it rather than the place
# itself -- a park, a shopping centre, a tower. See the guard in
# _longest_locality_in_text for the case that earned this list.
_FEATURE_TOKENS = frozenset("""
    park gardens garden reserve common square plaza quarter precinct estate towers
    tower residences apartments village centre center mall arcade wetlands
""".split())

# Money, a settlement date, a status word or a size means these are table CELLS, not an
# address. This is the gate that removed the house-design class of wrong answers.
_TABLE_ROW = re.compile(
    r"(\$\s*[\d,]{3,})"
    r"|(\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s\-']*\d{2,4}\b)"
    r"|(\b(?:available|sold|on hold|under offer|registered|titled|resale|deposit taken)\b)"
    r"|(\b\d+\s*(?:m2|sqm)\b)|(\b\d{1,2}\.\d\s*x\s*\d)", re.I)

# Somewhere that is not Australia. Matched against the row's own text.
# NAMES THAT MEAN A PROPERTY IS FOREIGN, whatever else the row says. Developers,
# addressing conventions and specific projects — none of these appear in an Australian
# address by accident.
#
# 'Sanaya Residences' is here because it has to be: ten 3-bedroom villas in Pecatu,
# Uluwatu, BALI, whose price list names no country, quotes $ per SQUARE FOOT, and arrived
# stored as NSW. Ten Indonesian villas sitting in an Australian buyer's advocate's stock
# list as New South Wales property. Nothing in the row text says Indonesia; only the name
# and the sqft pricing do.
_OVERSEAS_STRONG = re.compile(
    r"\b(dxb|abu\s*dhabi|u\.?a\.?e|damac|emaar|safa\s*gate|"
    r"iloilo|makati|philippines|smdc|brgy\.?|barangay|"
    r"sanaya\s*residences)\b", re.I)

# PLACE NAMES THAT ALSO APPEAR IN AUSTRALIAN ADDRESSES. 'Bali Hai Avenue' is on the Gold
# Coast; there are Singapore and London streets in half the capitals. These only count
# where the row records NO Australian state — which is true of every one of the 633
# overseas rows in this catalogue, and false of any Gold Coast lot.
_OVERSEAS_WEAK = re.compile(
    r"\b(dubai|manila|bali|phuket|bangkok|singapore|kuala\s*lumpur|"
    r"uluwatu|pecatu|seminyak|canggu)\b", re.I)

_AU_STATES = frozenset(("NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"))


# A SPELLING SLIP IS NOT A MISSING SUBURB.
#
# 'Soutbank' is Southbank with a letter dropped, on 77 live listings. 'CRESSNOCK' is
# Cessnock with one doubled. Both were refused outright -- the value is not a locality, so
# the row had no location at all -- when the source plainly said where the property is and
# simply mistyped it.
#
# Four constraints keep this from becoming fuzzy matching, which would be far worse than
# the problem. Measured across every stored suburb value: it corrects 78 rows and finds
# nothing ambiguous at all.
#
#   exactly one character   an insertion, a deletion or a substitution. Not two.
#   at least 7 characters   short words collide: 'Land' is one edit from 'Lang', a real
#                           Victorian locality, and 'Lane' from several. Nothing under
#                           seven characters is worth the risk.
#   the row's own state     the candidate pool is that state's localities and no others.
#   exactly one candidate   two localities a single edit away is not a typo, it is a
#                           coin toss. Refused, and the row stays blank.
_TYPO_MIN_LENGTH = 7


def _one_edit_apart(a: str, b: str) -> bool:
    """True when a and b differ by one insertion, deletion, substitution or swap.

    THE SWAP IS THE FOURTH TYPO and it was missing: 'Mickelham' for Mickleham is two
    substitutions to this function and one slip of the fingers to a person. Transposing
    a pair counts as one edit (Damerau), which is what the name of this function has
    always claimed.

    THE LENGTH FLOOR STAYS WHERE IT IS. Lowering _TYPO_MIN_LENGTH to 6 would also fix
    'Subury' -> Sunbury, one row -- and turn 'LOGAN' into 'Kogan' on nine, moving them
    from metropolitan Brisbane to a town of 300 people 250 km west. Measured across the
    whole catalogue: at 7 characters the swap adds one correction and no errors; at 6 it
    adds two corrections and nine errors.
    """
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        differs = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        if len(differs) == 2 and differs[1] == differs[0] + 1 \
                and a[differs[0]] == b[differs[1]] and a[differs[1]] == b[differs[0]]:
            return True
        return len(differs) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    i = j = 0
    skipped = False
    while i < len(short) and j < len(long_):
        if short[i] != long_[j]:
            if skipped:
                return False
            skipped = True
            j += 1
            continue
        i += 1
        j += 1
    return True


def _typo_correction(raw: str, state: str, index) -> str:
    """The one locality in this state that `raw` is a single character away from, or ''."""
    value = (raw or "").strip()
    if len(value) < _TYPO_MIN_LENGTH or not state:
        return ""
    try:
        entries = index._by_state.get(state.upper()) or []
    except Exception:                                  # pragma: no cover - index shape
        return ""
    lowered = value.lower()
    hits = {name for name, *_rest in entries if _one_edit_apart(lowered, name.lower())}
    return next(iter(hits)) if len(hits) == 1 else ""


def _is_the_builders_own_name(candidate, builder) -> bool:
    """True when the locality is just the builder's name in company clothing.

    Shares document_suburb._names_the_builder so the two rules cannot drift apart --
    imported lazily because that module reaches back into this one.
    """
    try:
        from document_suburb import _names_the_builder
    except Exception:                               # pragma: no cover - import guard
        return False
    return _names_the_builder(candidate, builder)


def _longest_locality_in_text(row: Dict[str, Any], state: str, index) -> str:
    """The longest locality named anywhere in the row's own text, or ''.

    This is the rule every other one in this module was written to avoid. It reads a
    whole flattened row rather than a structured address, so a house design named after
    a suburb ('Paddington', 'Ormond') or a street named after a town can win. Measured
    83.8% to 86.8% correct on holdout.

    It runs only after everything else has declined, and its answer is marked
    INFERRED_FROM_TEXT so nothing that needs a true position will accept it.

    The street guard and the not-a-locality list still apply — they cost nothing and each
    removes a whole class of the errors this rule is prone to.
    """
    # THE ESTATE NAME IS READ HERE AND NOWHERE ELSE IN THIS MODULE.
    #
    # It is deliberately absent from _row_text, because _postcode_anchor and
    # _looks_like_a_street would both change behaviour if it appeared there. But the last
    # documents holding out are multi-suburb BUILDER price lists — one sheet covering 57
    # estates — and on those the estate is the only thing that names a place at all:
    # "Bahrs Scrub Brookhaven", "Coridale Lara", "Central Springs Caboolture", "Logan
    # Reserve Riverleigh". Reading it is 68%-87% right on its own, which is why it is
    # confined to this function and its answer is marked INFERRED.
    # THE PROJECT NAME IS READ HERE TOO, for the same reason the estate name is.
    # E-Agent derives a project's name from its own link label and those labels
    # routinely carry the suburb -- "Eastbury Wheelers Hill", "Villea Palm Beach",
    # "Arca, Stafford". When a vendor withdraws the price list the name is all that is
    # left: Eastbury's Dropbox link now answers with an HTML page instead of the PDF,
    # so the document map lost it and 16 live rows went unnameable overnight.
    #
    # Measured on every row that already HAS a located suburb: 94% agreement once the
    # feature guard below is applied, against 83% without it. Of the disagreements
    # that remain, the four "Arca, Stafford" rows are this rule being RIGHT and the
    # stored value being the Building column ("Theodore"), and fourteen are Coburg
    # against Coburg North -- a neighbour, not a different town. That is at or above
    # the accuracy of this function's existing sources, which is why it lands at the
    # same INFERRED grade and not a better one.
    text = " ".join([_row_text(row), str(row.get("estate_name") or ""),
                     str(row.get("builder_name") or "")])
    words = _ADDR_WORD.findall(text)
    stateless_hits: list = []
    for size in (3, 2, 1):
        for start in range(len(words) - size + 1):
            candidate = " ".join(words[start:start + size])
            if size == 1 and _NOT_ALONE.match(candidate):
                continue
            after = start + size
            if after < len(words) and words[after].lower().strip(".") in _STREET_TOKENS:
                continue
            # A LOCALITY FOLLOWED BY A FEATURE WORD NAMES THE FEATURE. "Fawkner Park
            # West" is a development beside Fawkner PARK, on St Kilda Road; reading
            # "Fawkner" out of it moves the listing 15 km. Same shape as the street
            # guard above, and worth 11 points of accuracy on its own.
            if after < len(words) and words[after].lower().strip(".") in _FEATURE_TOKENS:
                continue
            # AND THE BUILDER IS NOT A TOWN. Reading the project name pulled 'Hudson'
            # out of "Hudson Homes" -- a real QLD locality 1,500 km from the Bald Hills
            # and Bethania lots that list carries. document_suburb refuses the same
            # candidate when it is offered as a document title; the test is what is LEFT
            # OVER once the candidate is removed, so "Eastbury Wheelers Hill" keeps its
            # suburb and "Hudson Homes" does not.
            if _is_the_builders_own_name(candidate, row.get("builder_name")):
                continue
            if state:
                if index.locate(candidate, state):
                    return candidate
                continue
            # NO STATE RECORDED. A nationally UNAMBIGUOUS name is still worth offering:
            # 'Mambourin' and 'Caboolture' exist in exactly one state each, so there is
            # no state for them to be wrong about.
            #
            # BUT UNAMBIGUOUS IS NOT THE SAME AS ALONE. 'Central Springs Caboolture' is a
            # Queensland estate, and it contains TWO unambiguous names: Springs, which is
            # in Western Australia and nowhere else, and Caboolture, which is in
            # Queensland. Taking the first match found labelled a Caboolture lot as being
            # in WA -- the exact trap that made a state mandatory everywhere else, walked
            # straight back in through the stateless door.
            #
            # So every candidate in the text is collected, and they must agree on ONE
            # state before any of them is offered. Disagreement means the text names two
            # places in two states and nothing here can say which the lot is in.
            found = index.states_for_suburb(candidate)
            found = list(found) if not isinstance(found, str) else [found]
            if len(found) == 1:
                stateless_hits.append((candidate, found[0]))

    if stateless_hits:
        agreed = {st for _name, st in stateless_hits}
        if len(agreed) == 1:
            return stateless_hits[0][0]
    return ""


def _document_suburb(row: Dict[str, Any]) -> str:
    """What this row's own source document names, or ''. Never raises, never fetches.

    The map is built by the harvest (document_suburb.refresh_from_database) because
    reading a document needs the network and a serverless request has no business making
    one. Import is lazy so suburb_quality stays usable in environments that do not ship
    the map at all.
    """
    # THE PER-ROW SECTION-HEADER MAP WAS TRIED HERE AND REMOVED. See
    # document_suburb.row_suburbs for what it did and the four ways it went wrong; the
    # short version is that it bought 34 listings and cost four defects, one of which
    # silently mislabelled dozens of rows at a time.
    try:
        import document_suburb
        return document_suburb.suburb_for_row(row)
    except Exception:                              # missing module, unreadable map
        return ""


def is_overseas(row: Dict[str, Any]) -> bool:
    """Is this listing outside Australia? See BLANK_OVERSEAS.

    A strong signal counts on its own. A weak one — a place name that also appears in
    Australian addresses — only counts where the row records no Australian state.
    """
    # builder_name and estate_name are scanned here and not in _row_text, which feeds the
    # postcode and street checks where they would change unrelated behaviour. The project
    # name is often the ONLY thing that says where a listing is: 'Sanaya Residences' is
    # the entire evidence that those ten villas are in Bali.
    text = " ".join((_row_text(row), str(row.get("estate_name") or ""),
                     str(row.get("builder_name") or "")))
    if _OVERSEAS_STRONG.search(text):
        return True
    if not _OVERSEAS_WEAK.search(text):
        return False
    return str(row.get("state") or "").strip().upper() not in _AU_STATES


def _is_addressy(text: str) -> bool:
    if not text:
        return False
    if _TABLE_ROW.search(text):
        return False
    return bool(_HAS_STREET.search(text)) or (
        "," in text and len(_ADDR_WORD.findall(text)) <= 8)


def _address_locality(row: Dict[str, Any], state: str, index) -> str:
    """The locality named at the tail of the row's own address, or ''."""
    raw_addr = str(row.get("lot_address") or row.get("street_address") or "")
    if not state or not _is_addressy(raw_addr):
        return ""
    text = _TAIL_MARKETING.sub("", _PARENS.sub(" ", raw_addr))
    text = re.sub(r"\b\d{4}\b\s*$", "", text).strip(" ,-")
    segs = [seg.strip() for seg in re.split(r"[,|/]", text) if seg.strip()]
    for seg in reversed(segs):
        # A segment ENDING in "Estate" names a development, not a locality:
        # 'Rangewood Estate, Lot 109' is in Lowood, not Rangewood.
        #
        # Only as a SUFFIX. "Estate : Ooranya Beveridge" is a labelled field whose value
        # ends with the suburb, and that segment is exactly where the Donnybrook lot
        # Coleen could not find had its locality hiding. Skipping the whole segment
        # because the word "Estate" appears anywhere in it puts that bug straight back.
        if len(segs) > 1 and re.search(r"\bestates?\s*$", seg, re.I):
            continue
        words = _ADDR_WORD.findall(seg)
        # Windows ending at each position, right to left, longest first — so
        # 'Armstrong Creek' is preferred over 'Creek'.
        for end in range(len(words), 0, -1):
            # A locality immediately followed by a street type IS the street.
            if end < len(words) and words[end].lower().strip(".") in _STREET_TOKENS:
                continue
            for size in (3, 2, 1):
                if end - size < 0:
                    continue
                cand = " ".join(words[end - size:end])
                if size == 1 and _NOT_ALONE.match(cand):
                    continue
                if index.locate(cand, state):
                    return cand
    return ""


def _postcode_anchor(row: Dict[str, Any], state: str, index) -> str:
    """A locality named immediately before a postcode that belongs to the row's state.

    The postcode is what makes this safe. Without the state-range check the same pattern
    reads a lot number ('Lot 2427, Newhaven'), a date ('Raceview 2026-08-01') and a plain
    non-postcode ('Fraser Rise 1106') as suburbs — all three are live rows.
    """
    for name, postcode in _BEFORE_POSTCODE.findall(_row_text(row)):
        if state_from_postcode(postcode) == state and index.locate(name, state):
            return name
    return ""


def _looks_like_a_street(name: str, row: Dict[str, Any]) -> bool:
    """Is this value the row's own street rather than its suburb?

    Two shapes, both from live rows that were being published as places:
      * the value IS a street type — 'Crescent', 'Grove', 'Rise' are all real localities
        somewhere in Australia, and all three appear here as the tail of a street name;
      * the value is followed by a street type in the row's own address —
        '132 Paramatta Street' produced the suburb 'Paramatta'.

    Only used where no state is recorded. With a state, index.locate already settles it.
    """
    import geo as _g                        # lazy — see the import note at the top
    if _g._STREET_TYPE.fullmatch(name.strip()):
        return True
    text = _row_text(row)
    if not text:
        return False
    after = re.search(re.escape(name.strip()) + r"\s+([A-Za-z]+)", text, re.IGNORECASE)
    return bool(after and _g._STREET_TYPE.fullmatch(after.group(1)))


def _row_text(row: Dict[str, Any]) -> str:
    return " ".join(str(row.get(f) or "") for f in _TEXT_FIELDS)[:_MAX_TEXT]


def summarise(rows) -> Dict[str, int]:
    """Counts by reason, for the daily run and the build to report."""
    index = _index()
    out: Dict[str, int] = {}
    for row in rows:
        _, why = resolve(row, index)
        out[why] = out.get(why, 0) + 1
    return out
