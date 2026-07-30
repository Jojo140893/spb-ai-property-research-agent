"""
Work out which state a listing is in.

Coleen, 28 July: *"We should see the builder name separated and we should see the
state. That's one of the main things."* Only 406 of 4,308 harvested rows carried a
state, because the builders' own stocklists mostly do not spell it out — they assume
you know, since each file covers one region.

Five signals, used strictly in order of how much they can be trusted:

  1. **A state the listing names itself.** Some stocklists label their own sections —
     "STAGE 2 / DORA CREEK / NSW", "Bethania QLD" — and that is the row speaking, so it
     outranks anything said about the file the row arrived in.
  2. **The listing's own postcode.** Australian postcode ranges map to exactly one
     state or territory, so this is a fact about the row, not an inference.
  3. **The suburb, when its name exists in only one state.** "Shepparton" is only ever
     Victoria. About a fifth of locality names are shared, so this only fires when the
     geo dataset gives a single answer.
  4. **The suburb plus the E-Agent page it came from**, when the name is shared. "Denman"
     is in NSW and QLD; a row from the New South Wales page is the NSW one.
  5. **The E-Agent category page alone.** Weakest, because it is a claim about the page
     rather than the row — a builder can list interstate stock on it — but the page's
     state was read off the site's own navigation, so it is evidence, not a guess.

Every result carries the signal that produced it, so a state in front of a buyer can
always be traced back. Where nothing applies the field stays empty: a wrong state sends
someone to the wrong side of the country.

A page or file hint is a claim about a WHOLE FILE, and a file can be national. Two of
E-Agent's are: Hudson Homes' PDF sits on the Queensland page and carries Wadalba,
Warnervale, Bellbird and Denman NSW beside Yarrabilba and Flagstone QLD; Thomas Paul's
sits on the New South Wales page and carries Toowoomba QLD. Applied per row, the hint put
all 149 Hudson lots in Queensland and 12 Toowoomba lots in New South Wales. So a hint is
only ever used for a file the file's own rows do not contradict — see `file_is_national`
and `own_state`, which is deliberately stricter than `resolve_state`: it is the standard
of proof needed to throw away a whole file's hint.
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


def _components(cell: Any) -> List[str]:
    """A stocklist cell split on the separators the files themselves use.

    The extractor writes what it captured as "<context><sep><locality>", and the files
    supply the separator: Thomas Paul uses " / " ("STAGE 2 / DORA CREEK / NSW"), Hudson
    uses ", " ("Highfields Estate, Denman"). The separator is the whole precision guard
    below — a cell with none of them is one blob of text, and matching a gazetteer against
    a blob is how "122 Atlas Crescent" becomes Crescent SA and "Neale Road" becomes
    Neale WA.
    """
    return [p.strip() for p in re.split(r"[/|,]", str(cell or "")) if p.strip()]


def state_named_in_listing(*cells: Any) -> Tuple[str, str]:
    """A state the listing states OUTRIGHT, and the text that said it. ('', '') if none.

    167 rows do this and the resolver never looked, because a state was only ever read out
    of `page_state`. It is the row speaking, so it is a fact about the row.

    Only whole separator-delimited components count, and only in upper case: a state code
    is two or three letters that a lower-case word can supply by accident ("Wa", "Act",
    "Sa"), whereas a file labelling a section shouts it.
    """
    for cell in cells:
        parts = _components(cell)
        for p in parts:
            up = p.upper()
            # A code has to be shouted: "Wa", "Act" and "Sa" are ordinary words, "WA" is a
            # label. The spelled-out names are unambiguous in any case.
            if up in {k.upper() for k in _STATE_WORDS} or (up in VALID_STATES and p == up):
                return normalise_state(up), str(p)
        # "Bethania QLD", "Truganina VIC", "BRISBANE NORTH QLD" — a locality with its
        # state after it. Bounded to a short component so a whole address never qualifies.
        for p in parts:
            if len(p.split()) > 5:
                continue
            m = _STATE_TOKEN.search(p)          # p, not p.upper(): upper case required
            if m and p.rstrip().endswith(m.group(1)):
                return m.group(1), str(p)
    return "", ""


def locality_in_listing(*cells: Any) -> str:
    """The locality out of a "<context><sep><locality>" cell, '' when there is no separator.

    Takes the last component that is not itself a state, so "STAGE 9 / TOOWOOMBA" gives
    Toowoomba, "KEARNEYS SPRING / QLD" gives Kearneys Spring and "Bethania QLD, Bethania"
    gives Bethania. Refusing separator-less cells is what keeps the estate names and
    column headers sitting in the suburb column ("Mandalay", "Jubilee", "Price",
    "Titled Packages") from being read as places.
    """
    for cell in cells:
        parts = _components(cell)
        if len(parts) < 2:
            continue
        for p in reversed(parts):
            bare = _STATE_TOKEN.sub("", p.upper()).strip(" -")
            if bare and len(bare) > 2:
                # give back the original casing of the words we kept
                keep = [w for w in p.split() if w.upper() not in VALID_STATES]
                return " ".join(keep)
    return ""


def own_state(postcode: Any = None, suburb: Any = "", estate: Any = "",
              address: Any = "", geo: Any = None) -> Tuple[str, str]:
    """What the ROW proves on its own, ignoring every page and file. ('', '') if nothing.

    Deliberately stricter than `resolve_state`, and in two ways, because this is both the
    standard of proof for overruling a hint that covers a whole file AND the only signal
    left once a file has been shown to be national:

      * A bare postcode does not qualify. A four-digit number in a stocklist is more often
        a land area, a unit number or a titling year — the Clyde North rows carrying 1028
        and 2209, "Warnervale 2026" where 2026 is the titling date.
      * A bare suburb-column word does not qualify either; it has to arrive with the
        separator its file put there. Thomas Paul's file has "118 Hobart Street" and
        "54 Greenway Street" reduced to Hobart and Greenway in the suburb column, and the
        gazetteer answers TAS and ACT for a Central Coast NSW builder.
    """
    named, _ = state_named_in_listing(suburb, estate, address)
    if named:
        return named, "listing text"

    loc = locality_in_listing(suburb, estate)
    if loc and geo is not None:
        try:
            cand = [c for c in geo.states_for_suburb(loc) if c in VALID_STATES]
        except Exception:
            cand = []
        if len(cand) == 1:
            return cand[0], "listing locality"
        from_pc = state_from_postcode(postcode)
        if len(cand) > 1 and from_pc in cand:
            return from_pc, "listing locality + postcode"

    # A postcode is only ever accepted here alongside a locality that agrees with it.
    from_pc = state_from_postcode(postcode)
    if from_pc and geo is not None and suburb:
        try:
            whole = [c for c in geo.states_for_suburb(str(suburb)) if c in VALID_STATES]
        except Exception:
            whole = []
        if from_pc in whole:
            return from_pc, "suburb + postcode"
    return "", ""


def file_is_national(proven_states: Any) -> bool:
    """True when the rows of ONE stocklist prove more than one state between them.

    Then the file is national and its page/filename hint is not a fact about any row in
    it: Hudson Homes' PDF proves NSW on 67 rows and QLD on 11, and the E-Agent Queensland
    page it hangs on made all 149 Queensland. The hint has to be dropped for the whole
    file, including the rows that prove nothing themselves — those are the rows a hint
    cannot be checked against, which is exactly why it must not be trusted on them.
    """
    return len({s for s in proven_states if s}) > 1


def resolve_state(postcode: Any = None, suburb: str = "", page_state: Any = "",
                  geo: Any = None, listing_state: Any = "") -> Tuple[str, str]:
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

    `listing_state` is the exception to the agreement rule, and the only one: a row that
    NAMES its state has already answered the question, and no amount of agreement between
    weaker signals can outvote it. It is the signal that was missing when 12 Toowoomba lots
    were exported as New South Wales off a file whose own cells read "KEARNEYS SPRING / QLD".
    """
    votes: Dict[str, List[str]] = {}

    stated = normalise_state(listing_state)
    if stated:
        # Recorded when something disagrees, so a contested row is still traceable.
        others = {s for s in (state_from_postcode(postcode), normalise_state(page_state))
                  if s and s != stated}
        return stated, "listing text" + (" (over " + "/".join(sorted(others)) + ")"
                                         if others else "")

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
