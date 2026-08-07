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

# Reasons whose suburb may be used AS A LOCATION.
_LOCATED = (STATED, FROM_COMPOSITE, FROM_POSTCODE, FROM_ADDRESS)


def is_located(why: str) -> bool:
    """Whether resolve()'s answer is safe to geocode, distance-filter or benchmark."""
    return why in _LOCATED

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
    if not raw:
        carried = str(row.get("suburb_source") or "").strip()
        if carried in (BLANK_NOT_A_PLACE, BLANK_NO_STATE):
            return "", carried

    if not index.loaded:                       # no locality data in this environment
        return raw, STATED if raw else BLANK_ABSENT

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
        return "", BLANK_NO_STATE if raw else BLANK_ABSENT

    if raw:
        inner = index.resolve_locality(raw, state)
        if inner:
            return _canonical_spelling(inner, state, index), FROM_COMPOSITE

    anchor = _postcode_anchor(row, state, index)
    if anchor:
        return _canonical_spelling(anchor, state, index), FROM_POSTCODE

    return "", BLANK_NOT_A_PLACE if raw else BLANK_ABSENT


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
