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
  * the value CONTAINS one, as the last comma/colon part or the
    longest tail ('Cloverton Estate , Kalkallo 3064')             -> use it   (626 live rows)
  * the row's own text names a locality immediately before a
    postcode, and that postcode belongs to the row's state        -> use it   (23 live rows)
  * anything else                                                 -> BLANK, with the reason

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

import geo as _geo_mod
from address_label import display_suburb as _display_suburb
from state_resolver import state_from_postcode

# Where a displayed suburb came from. Stored alongside the value so a reader can always
# see whether it was stated or derived — the same contract as `state_source`.
STATED = "stated by the source"
FROM_COMPOSITE = "read from the composite the source stored"
FROM_POSTCODE = "read from the postcode in the row's own text"
BLANK_NOT_A_PLACE = "the source's value is not a locality"
BLANK_NO_STATE = "no state recorded, so the value cannot be checked against a locality"
BLANK_ABSENT = "the source did not state one"

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
        # Victoria. With no state stored there is nothing to check it against.
        if not state:
            return _canonical_spelling(raw, "", index), STATED
        if index.locate(raw, state):
            return _canonical_spelling(raw, state, index), STATED

    if not state:
        return "", BLANK_NO_STATE if raw else BLANK_ABSENT

    if raw:
        inner = index.resolve_locality(raw, state)
        if inner:
            return _canonical_spelling(inner, state, index), FROM_COMPOSITE

    for name, postcode in _BEFORE_POSTCODE.findall(_row_text(row)):
        if state_from_postcode(postcode) == state and index.locate(name, state):
            return _canonical_spelling(name, state, index), FROM_POSTCODE

    return "", BLANK_NOT_A_PLACE if raw else BLANK_ABSENT


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
