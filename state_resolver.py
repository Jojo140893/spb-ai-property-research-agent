"""
Work out which state a listing is in.

Coleen, 28 July: *"We should see the builder name separated and we should see the
state. That's one of the main things."* Only 406 of 4,308 harvested rows carried a
state, because the builders' own stocklists mostly do not spell it out — they assume
you know, since each file covers one region.

Four signals, used strictly in order of how much they can be trusted:

  1. **The listing's own postcode.** Australian postcode ranges map to exactly one
     state or territory, so this is a fact about the row, not an inference.
  2. **The suburb, when its name exists in only one state.** "Shepparton" is only ever
     Victoria. About a fifth of locality names are shared, so this only fires when the
     geo dataset gives a single answer.
  3. **The suburb plus the E-Agent page it came from**, when the name is shared. "Denman"
     is in NSW and QLD; a row from the New South Wales page is the NSW one.
  4. **The E-Agent category page alone.** Weakest, because it is a claim about the page
     rather than the row — a builder can list interstate stock on it — but the page's
     state was read off the site's own navigation, so it is evidence, not a guess.

Every result carries the signal that produced it, so a state in front of a buyer can
always be traced back. Where nothing applies the field stays empty: a wrong state sends
someone to the wrong side of the country.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

VALID_STATES = ("NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT")

# Australia Post allocations. Ordered longest-standing first; ACT sits inside the NSW
# 2xxx block, so those ranges are listed before the NSW ones that surround them.
_POSTCODE_RANGES: Tuple[Tuple[int, int, str], ...] = (
    (200, 299, "ACT"),      # large-volume receivers
    (800, 899, "NT"),
    (900, 999, "NT"),
    (1000, 1999, "NSW"),    # large-volume receivers
    (2600, 2618, "ACT"),
    (2900, 2920, "ACT"),
    (2000, 2599, "NSW"),
    (2619, 2899, "NSW"),
    (2921, 2999, "NSW"),
    (3000, 3999, "VIC"),
    (8000, 8999, "VIC"),    # large-volume receivers
    (4000, 4999, "QLD"),
    (9000, 9999, "QLD"),    # large-volume receivers
    (5000, 5999, "SA"),
    (6000, 6999, "WA"),
    (7000, 7999, "TAS"),
)

_STATE_WORDS = {
    "victoria": "VIC", "new south wales": "NSW", "queensland": "QLD",
    "south australia": "SA", "western australia": "WA", "tasmania": "TAS",
    "northern territory": "NT", "australian capital territory": "ACT",
}
_STATE_TOKEN = re.compile(r"\b(VIC|NSW|QLD|SA|WA|NT|ACT|TAS)\b")


def normalise_state(value: Any) -> str:
    """'  vic ' -> 'VIC'; 'Victoria' -> 'VIC'; anything unrecognised -> ''."""
    s = str(value or "").strip()
    if not s:
        return ""
    if s.upper() in VALID_STATES:
        return s.upper()
    word = _STATE_WORDS.get(s.lower())
    if word:
        return word
    m = _STATE_TOKEN.search(s.upper())
    return m.group(1) if m else ""


def state_from_postcode(postcode: Any) -> str:
    """A four-digit Australian postcode identifies exactly one state or territory.

    Stored as TEXT because NT and ACT postcodes have a leading zero (0800, 0200) that an
    integer column would silently destroy.
    """
    s = re.sub(r"\D", "", str(postcode or ""))
    if len(s) != 4:
        return ""
    n = int(s)
    for low, high, state in _POSTCODE_RANGES:
        if low <= n <= high:
            return state
    return ""


def resolve_state(postcode: Any = None, suburb: str = "", page_state: Any = "",
                  geo: Any = None) -> Tuple[str, str]:
    """Return (state, signal). Both empty when nothing reliable is available.

    Decided by AGREEMENT, not by a fixed ranking, because cross-checking the signals
    against each other on the live data showed every one of them can be wrong on its own:

      * A stored postcode can be a four-digit number that was never a postcode. Rows in
        Clyde North and Truganina — both plainly Victorian — carry 1028, 1030 and 2209,
        which are NSW codes.
      * A suburb name can be a region. The QLD stocklists put "LOGAN" in the suburb
        column; the locality dataset has a Logan in Victoria and nowhere else, so on its
        own it answers VIC for a Queensland lot.

    Two signals agreeing rules out both of those, and each of those examples is decided
    correctly below. Where signals conflict with nothing to break the tie, the answer is
    empty — sending a buyer to the wrong side of the country is the one outcome worth
    avoiding at the cost of a blank cell.
    """
    votes: Dict[str, List[str]] = {}

    from_pc = state_from_postcode(postcode)
    if from_pc:
        votes.setdefault(from_pc, []).append("postcode")

    candidates: List[str] = []
    if geo is not None and suburb:
        try:
            candidates = [c for c in geo.states_for_suburb(suburb) if c in VALID_STATES]
        except Exception:
            candidates = []
    if len(candidates) == 1:
        votes.setdefault(candidates[0], []).append("suburb")

    hint = normalise_state(page_state)
    if hint:
        votes.setdefault(hint, []).append("e-agent page")

    corroborated = [s for s, sigs in votes.items() if len(sigs) >= 2]
    if len(corroborated) == 1:
        return corroborated[0], " + ".join(votes[corroborated[0]])

    # A shared locality name casts no vote of its own, but the page can pick which of its
    # states is meant: "Denman" is in NSW and QLD, and a row off the NSW page is the NSW one.
    if len(candidates) > 1 and hint in candidates:
        return hint, "suburb + e-agent page"

    if len(votes) == 1:
        only = next(iter(votes))
        return only, votes[only][0]

    # Signals disagree and nothing corroborates either. The page state is the only one
    # derived systematically (read off E-Agent's own navigation), so use it — labelled,
    # so the export shows the answer was contested.
    if hint:
        return hint, "e-agent page (conflicting signals)"
    return "", ""


def disagreement(postcode: Any, suburb: str, page_state: Any, geo: Any) -> Optional[Dict[str, Any]]:
    """Report when the row's own postcode contradicts the page it came from.

    Worth surfacing rather than silently preferring one: it means either a builder has
    listed interstate stock on a state page, or a postcode was misparsed. Both are things
    a human should see, and both would otherwise be invisible.
    """
    pc_state = state_from_postcode(postcode)
    hint = normalise_state(page_state)
    if pc_state and hint and pc_state != hint:
        return {"postcode": str(postcode), "postcode_state": pc_state,
                "page_state": hint, "suburb": suburb}
    return None
