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


# --- a lot number is not a postcode ------------------------------------------------------
#
# Coleen, 28 Sep 2026: "I searched for QLD properties, however, the app is reporting on VIC
# properties." Her comparison report for a QLD lead under $800k offered two "Jubilee, QLD"
# lots. Both are Verv Projects lots in the Jubilee estate at Wyndham Vale, Victoria, off
# E-Agent's VICTORIA page. Their line reads
#
#     "2-Part Detached SS West 4327 Jubilee Wyndhamvale 263 $338,000 Enzo 10.5 ..."
#
# and 4327 is the LOT. The extractor also stored it as the postcode (the compass-word guard
# in sources/feature_extract.parse_postcode only fires when "West" opens the row, and here
# "2-Part Detached SS" comes first). 4327 is a Queensland postcode, Jubilee is a
# Queensland locality in the gazetteer, so own_state proved QLD by "suburb + postcode", the
# E-Agent VIC page was overruled, and enrich has treated the row as settled ever since.
#
# Measured on the 28 Sep nightly: 18 live rows rest on a postcode equal to their own lot
# number and 12 of them are in the wrong state -- 6 Jubilee VIC lots filed QLD, 5 Tomorrow
# Homes Clyde VIC lots filed NSW, a Yarrabilba QLD lot filed WA -- and the Clyde five made
# Tomorrow Homes' whole sheet "national", leaving 128 of its rows with no state at all. A
# number the row already uses as its lot cannot also be its postcode, so it is ignored
# before any state is read from it. Stated once here and taken by all three functions
# below, so no caller can pass the lot number to one and forget the others.
#
# COMPARED NUMBER BY NUMBER, not as one run of digits (review, 28 Sep 2026). The first
# version stripped every non-digit out of the lot and compared what was left, so Geocon's
# "GRD-T2-601" became "2601" -- and id 21209, "Lot GRD-T2-601 Unit 149, 70 Allara Street,
# CANBERRA, ACT, 2601", had its REAL Canberra postcode thrown away: without its line's
# own "ACT", own_state answered ('', '') instead of ACT by "suburb + postcode", and
# record_building would not store 2601 at all. 241 rows carry lots of that GRD-T9-999
# shape, every one of them on postcode 2601; T2-601 is the one whose digits happen to
# spell it. Each run of digits in the lot is one of its numbers ("GRD-T2-601" is 2 and
# 601; "Lot 4327" and "4327A" are 4327), and the postcode is the lot number only when it
# IS one of them. Leading zeros are dropped on both sides, so "0810" and a lot "810" are
# the same number.
def _number(digits: str) -> str:
    return digits.lstrip("0") or "0"


def postcode_is_lot_number(postcode: Any, lot_number: Any, row_text: Any = None,
                           geo: Any = None) -> bool:
    """True when the stored postcode is the row's own lot number.

    "Lot 4327" / "4327A" / " 4327 " all match a stored 4327; "GRD-T2-601" does not match
    2601 (its numbers are 2 and 601). A row with no lot number (or no postcode) never
    matches on that count -- there is nothing to compare.

    `row_text`: the row's own line(s). A postcode that is the number OPENING one of them is
    the lot as well, with or without a lot number to compare (postcode_opens_the_row).
    """
    pc = re.sub(r"\D", "", str(postcode or ""))
    if not pc:
        return False
    if _number(pc) in {_number(n) for n in re.findall(r"\d+", str(lot_number or ""))}:
        return True
    return postcode_opens_the_row(pc, row_text, geo)


# --- the number that opens the row is its lot, lot number or not ---------------------
#
# 29 Sep 2026. AVIA Homes 2234 reads "2120 Stage 21 400 $627,000 July 2026 Madrid 190
# Paddington ..." -- lot 2120 of stage 21 at White Rock, QLD -- and an older parser stored
# 2120 as its postcode. Its lot_number column is EMPTY (AVIA prints the lot bare, with no
# "Lot"), so the rule above had nothing to compare; 2120 is a NSW code, White Rock is in
# NSW and QLD, and own_state proved NSW by "listing locality + postcode". That one vote
# made AVIA's QLD sheet "national" and voided its page state for every unsettled row on it:
# 74 live AVIA rows with no state at all on the 29 Sep nightly.
#
# sources/feature_extract.parse_postcode has refused a number that opens the row since it
# met "2226 Whiterock ..." -- "an Australian postcode follows the suburb, it never leads" --
# but postcode is fill-if-empty (database.record_building), so a value an older parser
# stored stays in the column for good. So the rule is stated here too, where the stored
# value is READ, and enrich clears it off every live row each night.
#
# Measured on the 29 Sep copy: 41 rows hold a postcode equal to the number opening their
# line or label, and all 41 are a lot or house number -- "2120 Stage 21", "5519 Celestial
# Way", "2954 Lockington Road", "3076 Nest Street". None is a postcode. The one shape that
# could be, a line written postcode-first ("3029 Tarneit VIC"), is kept: the number stays a
# postcode when the words after it are a locality of that postcode's own state and not the
# name of a street ("3064 Donnybrook Road" is a house on Donnybrook Road).
_OPENING_NUMBER = re.compile(r"^[^\w$]*(\d+)(?![\d.,/])")
# The run of words straight after that number, up to a digit, a comma or other punctuation.
_WORDS_AFTER = re.compile(r"\s+([A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,3})")


def postcode_opens_the_row(postcode: Any, row_text: Any, geo: Any = None) -> bool:
    """True when the postcode is the number that opens the row's own text, in lot position.

    `row_text` is one string or several (source_text, lot_address); any of them counts.
    Not when the words straight after the number are a locality of that postcode's state
    ("3029 Tarneit VIC ..."), which is a postcode written first.
    """
    pc = re.sub(r"\D", "", str(postcode or ""))
    if len(pc) != 4:
        return False
    texts = row_text if isinstance(row_text, (list, tuple)) else [row_text]
    for text in texts:
        m = _OPENING_NUMBER.match(str(text or ""))
        if not m or _number(m.group(1)) != _number(pc):
            continue
        if _locality_of_state_follows(str(text)[m.end():], state_from_postcode(pc), geo):
            continue
        return True
    return False


_GAZETTEER: List[Any] = []


def _gazetteer(geo: Any) -> Any:
    """`geo`, or one SuburbGeoIndex loaded on first use (record_building has none to pass)."""
    if geo is not None:
        return geo
    if not _GAZETTEER:
        try:
            from geo import SuburbGeoIndex
            _GAZETTEER.append(SuburbGeoIndex())
        except Exception:                                             # noqa: BLE001
            _GAZETTEER.append(None)
    return _GAZETTEER[0]


def _locality_of_state_follows(rest: str, state: str, geo: Any) -> bool:
    """True when `rest` opens with a locality of `state` that is not a street's name."""
    geo = _gazetteer(geo)
    if not state or geo is None:
        return False
    from geo import _STREET_TYPE
    m = _WORDS_AFTER.match(rest)
    words = m.group(1).split() if m else []
    for size in (3, 2, 1):
        if len(words) < size:
            continue
        name = " ".join(words[:size])
        nxt = words[size] if len(words) > size else ""
        try:
            if geo.locate(name, state) and not (nxt and _STREET_TYPE.fullmatch(nxt)):
                return True
        except Exception:                                             # noqa: BLE001
            return False
    return False


def _unless_lot_number(postcode: Any, lot_number: Any, row_text: Any = None,
                       geo: Any = None) -> Any:
    """The postcode, or None when it is the lot number (see postcode_is_lot_number)."""
    return None if postcode_is_lot_number(postcode, lot_number, row_text, geo) else postcode


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


# Administrative areas that are not localities. Used by locality_tail_in_cell.
_ADMIN_AREA = re.compile(r"\b(council|city of|shire|regional|region)\b", re.IGNORECASE)


def locality_tail_in_cell(cell: Any, geo: Any) -> str:
    """"<estate> <locality>" with no separator — 'Windermere Mambourin' -> 'Mambourin'.

    locality_in_listing above requires a separator, and that rule earns its keep: it is
    what stops the bare estate names and column headers in the suburb column ('Mandalay',
    'Jubilee', 'Price', 'Titled Packages') being read as places. But plenty of files glue
    the estate to the locality with nothing but a space, and refusing those cost a whole
    file its national detection:

        one E-Agent QLD stocklist, 73 rows, every one stamped QLD from the page hint —
        while its own cells read 'Windermere Mambourin', 'Coridale Lara' and 'Warralily
        Armstrong Creek', all Victorian. Armstrong Creek exists in QLD too, so those two
        lots geocoded to Caboolture, about 1,400 km from the Geelong estate they are in.

    Two guards keep this as strict as the separator rule it supplements:
      * at least two words, so a single bare word can never qualify — that is exactly the
        'Jubilee' / 'Mandalay' case, and it stays refused;
      * the LONGEST trailing tail that is a locality, so 'Smythes Creek' beats 'Creek'.

    Ambiguity is not resolved here. own_state still requires the name to belong to
    exactly one state before it counts as proof of anything.
    """
    raw = str(cell or "")
    # An LGA is not a suburb, and its tail is a trap. 'CITY OF LOGAN' is a Queensland
    # council; Logan is a locality in VICTORIA and nowhere else, so the tail walk proves
    # VIC for a Queensland lot — the same collision an existing test already pins down
    # for the suburb column. 'Logan City Council' (27 rows), 'COAST COUNCIL' (40) and
    # 'Moreton Bay Regional Council' (21) are all live values in this column.
    if _ADMIN_AREA.search(raw):
        return ""
    words = raw.split()
    if len(words) < 2 or geo is None:
        return ""
    try:
        # The WHOLE cell first, or a two-word locality loses its first word:
        # 'Mount Duneed' would answer 'Duneed', a different question entirely.
        for start in range(0, len(words)):
            tail = " ".join(words[start:])
            if geo.states_for_suburb(tail):
                return tail
    except Exception:                                                 # noqa: BLE001
        return ""
    return ""


def own_state(postcode: Any = None, suburb: Any = "", estate: Any = "",
              address: Any = "", geo: Any = None, lot_number: Any = None,
              row_text: Any = None) -> Tuple[str, str]:
    """What the ROW proves on its own, ignoring every page and file. ('', '') if nothing.

    `lot_number`: a postcode equal to it is no postcode at all (postcode_is_lot_number) --
    the Jubilee lots proved QLD here on "suburb + postcode" from their lot number, 4327.
    `row_text`: the row's line(s); a postcode that is the number opening one is its lot
    too -- AVIA 2234, "2120 Stage 21 ...", proved NSW here off lot 2120.

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
    postcode = _unless_lot_number(postcode, lot_number, row_text, geo)
    named, _ = state_named_in_listing(suburb, estate, address)
    if named:
        return named, "listing text"

    loc = locality_in_listing(suburb, estate)
    if not loc:
        # No separator, but the SUBURB cell may still be "<estate> <locality>". Same
        # standard of proof applies below: one state or it proves nothing.
        #
        # THE SUBURB CELL ONLY, never estate_name. An estate name is marketing text by
        # definition, and its trailing word lands on a locality often enough to matter:
        # 'Redbank Plains Sienna Eden' answers Eden NSW for a lot in Redbank Plains QLD,
        # and 'The Grove' answers Grove TAS. Measured over the live table, allowing the
        # estate cell here manufactured 31 false proofs — and a false proof is worse than
        # no proof, because this is the standard used to overrule a whole file's hint.
        loc = locality_tail_in_cell(suburb, geo)
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
                  geo: Any = None, listing_state: Any = "",
                  lot_number: Any = None, row_text: Any = None) -> Tuple[str, str]:
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

    `lot_number`: a postcode equal to it casts no vote (postcode_is_lot_number). "2704
    Talladega Rd, Clyde" stored 2704 as its postcode -- NSW -- and Clyde, a locality in NSW
    and VIC, agreed with it, so five Tomorrow Homes lots off the VIC page went to NSW.
    `row_text` likewise: the number opening the row is its lot (postcode_opens_the_row).
    """
    postcode = _unless_lot_number(postcode, lot_number, row_text, geo)
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


# --- a state that rests on a suburb match, and a row whose own text says otherwise --------
#
# Colin, 15 Sep 2026, "QLD under $750k": one of the two results was "Lot 618, Riverhills
# QLD", $553,284. Its line reads "Available Wollert Titled Townhouse 618 ...", Wollert is in
# Victoria, and the same lot is live twice more as VIC off E-Agent. The email extractor had
# put the ESTATE, Riverhills, in the suburb column; Riverhills is also a Brisbane suburb and
# nowhere else, so resolve_state took that lone gazetteer hit as the state and enrich has
# treated the row as settled ever since. Nothing downstream looks again:
# repair_rows.find_refuted_states only fires when the suburb is NOT a locality of the stated
# state, and Riverhills is one.
#
# Measured on the 17 Sep copy: 25 live rows are in that position, all reviewed by hand as
# misfiled (Wollert, Armstrong Creek, Mambourin, Murray Bridge, Officer, Fraser Rise, Clyde
# North, Moss Vale). Every guard below exists because a looser version fired on a correct
# row: house designs ("Rhodes 15", "Ceduna 9.66"), regions ("FRASER COAST"), estates ("The
# Grove", "Riverstone Springs") and dates ("March 2027").
TEXT_LOCALITY_SOURCE = "row text locality (over suburb column)"

_TEXT_TOKEN = re.compile(r"[A-Za-z][A-Za-z'\-]*|\d+(?:\.\d+)?")
_MONTH_WORD = re.compile(
    r"^(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|"
    r"sep|sept|september|oct|october|nov|november|dec|december)$", re.IGNORECASE)
# The word AFTER a name that makes the name something other than the lot's locality: a
# region ("Fraser Coast"), an estate or development ("Riverstone Springs", "Fraser Rise"
# read one word short), or a design and its size ("Rhodes 15", "Lawson 20").
_NOT_A_LOCALITY_AFTER = re.compile(
    r"^(?:point|park|springs|estate|heights|homes|group|house|range|river|lakes?|green|"
    r"rise|views?|hills?|waters|shores|quarter|collection|coast|single|double|storey|"
    r"custom|facade|sg|dg|\d+(?:\.\d+)?)$", re.IGNORECASE)
# The design half of that list, plus the facade styles: a word that, straight after a name,
# is the design's own garage, storey or facade variant -- "Verona SG Corner", "Vesper DG",
# "HARWOOD SINGLE", "Magnolia Traditional", "Somerset Coastal". Added 29 Sep 2026: in the
# 29 Sep copy every place name followed by one of these words is a house design (Vesper
# 54, Verona 43, Harwood 23, Magnolia 26, Chelsea 24, Eden 19, Somerset 19, Concord 15 ...,
# Kuraby 5 -- a Brisbane suburb on a NSW list). "Dual" is NOT one of them: "Weston
# Available 14 Weston Dual Living ..." is G Developments' estate named after its suburb.
# Also AVIA's facades that are no place's name, typos included (its others -- Paddington,
# Yamba, Newstead -- are places, and 'followed' reads them): once AVIA's sheet stopped
# being national, "91 Stage 2 354m2 ... Rhodes Fortitude 180m2" (12359) was filed QLD and
# the replay read Rhodes, NSW, in it. Rhodes Fortitude 5, Sorrento Carolina 4, Sorrento
# Fortitude 2 in the 29 Sep copy, every one a design.
_DESIGN_VARIANT_AFTER = re.compile(
    r"^(?:single|double|storey|custom|facade|sg|dg|traditional|modern|coastal|"
    r"contemporary|hamptons|classic|fortitude|fortitdue|carolina|caolina|lennox|"
    r"padington)$", re.IGNORECASE)
_ESTATE_WORD = re.compile(r"^estates?$", re.IGNORECASE)
# The first word of a region or a longer name, whose last word alone is some other place:
# "SOUTHERN HIGHLANDS" is not Highlands VIC, "LAKE MACQUARIE" is not Macquarie TAS.
_NAME_HEAD = re.compile(r"^(?:north|south|east|west|northern|southern|eastern|western|"
                        r"central|upper|lower|greater|inner|outer|lake|mount|mt|port|"
                        r"cape)$", re.IGNORECASE)
_STATE_CODE = r"(?:VIC|NSW|QLD|SA|WA|NT|ACT|TAS)"


def _estate_key(estate: Any) -> str:
    """'Riverhills Estate' / ' riverhills ' -> 'riverhills'. '' when there is no name."""
    words = [t.lower() for t in _TEXT_TOKEN.findall(str(estate or ""))]
    return " ".join(w for w in words if w != "estate")


def _tokens(text: Any) -> Tuple[List[str], set]:
    """(tokens, breaks): _TEXT_TOKEN's split of `text`, trailing '-' and "'" stripped, and
    the indices of the tokens that punctuation -- not just a space -- parts from the one
    before ("Verona SG Corner | Roselle", "ARMSTRONG CREEK (GEELONG)")."""
    toks: List[str] = []
    breaks = set()
    end = None
    raw = str(text or "")
    for m in _TEXT_TOKEN.finditer(raw):
        tok = m.group(0).rstrip("-'")
        if not tok:
            continue
        if end is not None and raw[end:m.start()].strip():
            breaks.add(len(toks))
        toks.append(tok)
        end = m.end()
    return toks, breaks


# --- a place name that is something else in this row --------------------------------------
#
# 29 Sep 2026. Three stored states came from a word that was never the lot's locality, and
# each one also made its whole stocklist "national" (file_is_national), so its page state
# was dropped for every unsettled row on it:
#
#   * Silkwood 11510, "REGISTER NOW New Beith Round Mountain Estate ... Doncaster 191" --
#     NSW by "Round Mountain", the ESTATE. The lot is in New Beith, QLD.
#   * Verv 11494, label "5629 The Grove, Tarneit" -- NSW by "The Grove", the estate. The
#     lot is in Tarneit, VIC, after the comma.
#   * Hattan 14913/14914, "... Verona SG Corner | Roselle 15.72 ..." -- NSW by "Verona",
#     the house DESIGN (SG: single garage). The same lots, read with their suburb column
#     (12348/12349), are in Springvale South, VIC.
#
# and the nightly replay has warned every night that AVIA 7810 ("... Rhodes Paddington
# 180m2 ...", design Rhodes, facade Paddington) names Rhodes, NSW. One rule for all of it,
# used by the enrich blank-suburb guess (suburb_in_row_text) and by text_locality_elsewhere,
# which enrich's overrule and search_regressions.locality_check both call -- so the three
# can never disagree about what a word in a row is.
def not_the_locality_here(toks: List[str], i: int, size: int, geo: Any, estate: Any = "",
                          breaks: Any = (), state: Any = "") -> str:
    """Why the place name toks[i:i+size] is not the lot's locality in this row, or ''.

    'design'   the next word is a design's garage, storey or facade variant ("Verona SG",
               "Magnolia Traditional");
    'followed' straight after it, with nothing but a space between, comes another place
               name that does not extend it: a house design and its facade ("Rhodes
               Paddington"), an estate and its suburb ("Windermere Mambourin", "The Grove
               Tarneit", "Oaklands Bonnie Brook"). The FIRST name is not the locality either
               way. "Clyde North", "Point Cook" and "Box Hill North" extend their first
               words, so they are one place. The name after does not count when it is
               itself the name of an estate or a street ("New Beith Round Mountain Estate",
               the Silkwood layout, suburb then estate; "Joyner Oxford Street", "Glenvale
               Alford Grove"), nor when `state` is given and it is not a place there
               ("Flemington Racecourse" on a VIC list: Racecourse is in QLD);
    'estate'   the row's own estate: "Estate" follows ("Round Mountain Estate"), or the span
               IS the row's estate name. An estate is often named after its own suburb
               ("Wadalba Estate" in Wadalba), so the guess DEMOTES this rather than refuse it.

    `toks`/`breaks` are _tokens' split of the row, numbers included, so a number between
    two names ("Tarneit 300 ... Enzo") keeps them apart, and so does punctuation.
    """
    name = " ".join(toks[i:i + size])
    j = i + size
    nxt = toks[j] if j < len(toks) and j not in breaks else ""
    if nxt and _DESIGN_VARIANT_AFTER.match(nxt):
        return "design"
    if nxt and _place_follows(toks, j, name, geo, breaks, state):
        return "followed"
    if nxt and _ESTATE_WORD.match(nxt):
        return "estate"
    own = _estate_key(estate)
    if own and name.lower() == own:
        return "estate"
    return ""


def _place_follows(toks: List[str], j: int, name: str, geo: Any, breaks: Any = (),
                   state: Any = "") -> bool:
    """True when toks[j:] opens with a place name, standing on its own, that does not
    extend `name` (see not_the_locality_here, 'followed')."""
    from geo import _STREET_TYPE
    st = normalise_state(state)
    for size in (3, 2, 1):
        span = toks[j:j + size]
        if (len(span) < size or any(t[0].isdigit() or len(t) < 2 for t in span)
                or any(k in breaks for k in range(j + 1, j + size))):
            continue
        after = " ".join(span)
        states = _places(after, geo)
        if not states or (st and st not in states) or _places(name + " " + after, geo):
            continue
        k = j + size
        if k < len(toks) and k not in breaks and (_ESTATE_WORD.match(toks[k])
                                                  or _STREET_TYPE.fullmatch(toks[k])):
            continue
        return True
    return False


def _places(name: str, geo: Any) -> List[str]:
    try:
        return list(geo.states_for_suburb(name))
    except Exception:                                                 # noqa: BLE001
        return []


def suburb_in_row_text(text: Any, geo: Any, prefer: Any = "",
                       estate: Any = "") -> Optional[str]:
    """The locality a row's own label or line names, for a row with a blank suburb column.

    enrich's blank-suburb guess. It used geo.find_suburb_in_text, which had two faults for
    this job besides the words not_the_locality_here now refuses:
      * with no state to prefer it walked the states in the gazetteer file's order, which
        starts with NSW -- so any NSW name anywhere in a line beat the real locality of
        every other state: "The Grove" over Tarneit, "Round Mountain" over New Beith,
        "Clyde" over Clyde North, "Verona" over nothing;
      * enrich passed it the row's STORED state, which the guess itself had decided on an
        earlier night -- so a wrong guess preferred itself for good.

    So:
      1. "X, Locality": the last comma-separated part of a label is where the locality is
         written ("5629 The Grove, Tarneit" -> Tarneit), the same fact
         geo.resolve_locality leans on. Its longest locality tail wins -- not the tail of a
         region ("SOUTHERN HIGHLANDS") and not a council.
      2. Otherwise every 1-3 word span that is a locality: those of `prefer` first (the
         page's state -- never one enrich wrote), then every state, longest span first and
         then left to right.
    Refused: 'design' and 'followed' (not_the_locality_here), a month ("March 2027") and a
    bare street word ("Grove"). Demoted to a last resort, as find_suburb_in_text demotes a
    street's name: 'estate' -- with every shorter name inside it, so "Innes Park Estate"
    cannot hand the lot to Innes -- and, after it, a name followed by a street type: an
    estate is often named after its suburb, a name before "Rd" is a street's ("114 Neale
    Rd, Parkside", estate Parkside, is in Parkside). None when nothing qualifies.
    """
    if geo is None or not text:
        return None
    from geo import _STREET_TYPE

    raw = str(text)
    # last resorts, best first: a name the row's estate shares, then a street's name
    fallback: Dict[str, List[str]] = {"estate": [], "street": []}

    def verdict(toks, breaks, i, size, state="", estate_words=None):
        """'take', 'refuse', or the demotion 'estate' / 'street', for toks[i:i+size]."""
        span = toks[i:i + size]
        if _MONTH_WORD.match(span[0]) or (size == 1 and _STREET_TYPE.fullmatch(span[0])):
            return "refuse"
        why = not_the_locality_here(toks, i, size, geo, estate, breaks, state)
        if why in ("design", "followed"):
            return "refuse"
        j = i + size
        if why == "estate" or (estate_words and estate_words & set(range(i, j))):
            if estate_words is not None:
                estate_words.update(range(i, j))
            return "estate"
        nxt = toks[j] if j < len(toks) and j not in breaks else ""
        if nxt and _STREET_TYPE.fullmatch(nxt):
            return "street"
        return "take"

    # 1. the last part of an "X, Locality" label. A comma straight before a digit is a
    # thousands separator ("$1,084,400"), not an address comma.
    parts = [p.strip() for p in re.split(r",(?!\d)", raw)]
    parts = [p for p in parts if p and not re.fullmatch(
        r"[\s\d]*|" + _STATE_CODE + r"(?:\s+\d{4})?", p)]
    if len(parts) > 1 and not _ADMIN_AREA.search(parts[-1]):
        # "Marsden Park (Lot 1) (Block B)", "COLAC 3250", "Tarneit VIC 3029" -> the name
        last = re.sub(r"\s*\([^)]*\)", " ", parts[-1])
        last = re.sub(r"(?:\s+" + _STATE_CODE + r")?(?:\s+\d{4})?\s*$", "", last).strip()
        toks, breaks = _tokens(last)
        for start in range(len(toks)):
            span = toks[start:]
            if (len(span) > 3 or any(t[0].isdigit() or len(t) < 2 for t in span)
                    or (start and _NAME_HEAD.match(toks[start - 1]))):
                continue
            name = " ".join(span)
            if not _places(name, geo):
                continue
            how = verdict(toks, breaks, start, len(span))
            if how == "take":
                return name.title()
            if how in fallback:
                fallback[how].append(name.title())

    # 2. every span of the whole text
    toks, breaks = _tokens(raw)
    first = normalise_state(prefer)
    for only in ([first] if first else []) + [""]:
        estate_words: set = set()
        for size in (3, 2, 1):
            for i in range(len(toks) - size + 1):
                span = toks[i:i + size]
                if (any(t[0].isdigit() or len(t) < 2 for t in span)
                        or any(k in breaks for k in range(i + 1, i + size))):
                    continue
                name = " ".join(span)
                states = _places(name, geo)
                if not states or (only and only not in states):
                    continue
                how = verdict(toks, breaks, i, size, only, estate_words)
                if how == "take":
                    return name.title()
                if how in fallback:
                    fallback[how].append(name.title())
    for kind in ("estate", "street"):
        if fallback[kind]:
            return fallback[kind][0]
    return None


def design_the_state_rests_on(row_text: Any, suburb: Any, state: Any,
                              geo: Any) -> Optional[str]:
    """The place name a suburb-match state rests on, when the row's own text names it only
    as a house design; None otherwise.

    29 Sep 2026: Hattan 14913/14914, "... Verona SG Corner | Roselle 15.72 ...", were filed
    NSW by 'suburb' -- Verona, the design -- and text_locality_elsewhere cannot see it,
    because nothing in the line names a VIC place: the lots' suburb (Springvale South) is
    not in this capture. What CAN be seen is that the only name the state could rest on is
    written as a design. So:
      * `suburb` given (the snapshot's, or the column): that name;
      * no suburb: every name in the text that is a place of `state` -- the ones a guess
        could have used;
    and a name is reported when it appears, and every appearance is followed by a design's
    garage, storey or facade word (not_the_locality_here's 'design'). A name also written
    any other way in the row is its locality as far as the row can say. NOT 'followed':
    two place names side by side are a design and its facade ("Rhodes Paddington") but
    also a suburb and its design -- Creation Homes' QLD list reads "4 Pilny Street Camira
    Banksia 183 - Classic" -- and counting them put 40 correct rows on this WARN in the
    29 Sep copy. Pure, like text_locality_elsewhere; the caller decides the state_source
    it applies to.
    """
    if geo is None or not row_text:
        return None
    toks, breaks = _tokens(row_text)
    stored = normalise_state(state)
    names: List[str] = []
    if str(suburb or "").strip():
        names = [str(suburb).strip()]
    elif stored:
        for size in (3, 2, 1):
            for i in range(len(toks) - size + 1):
                span = toks[i:i + size]
                if (any(t[0].isdigit() or len(t) < 2 for t in span)
                        or any(k in breaks for k in range(i + 1, i + size))):
                    continue
                name = " ".join(span)
                if stored in _places(name, geo) and name.lower() not in \
                        [n.lower() for n in names]:
                    names.append(name)
    for name in names:
        want = [w.lower() for w in _TEXT_TOKEN.findall(name)]
        if not want:
            continue
        seen = plain = 0
        for i in range(len(toks) - len(want) + 1):
            if [t.lower() for t in toks[i:i + len(want)]] != want:
                continue
            if any(k in breaks for k in range(i + 1, i + len(want))):
                continue
            seen += 1
            if not_the_locality_here(toks, i, len(want), geo, "", breaks) != "design":
                plain += 1
        if seen and not plain:
            return name
    return None


def text_locality_elsewhere(row_text: Any, state: Any, geo: Any,
                            stocked: Any, estate: Any = "") -> Optional[Tuple[str, str]]:
    """(locality, other_state) when the row's own text names a place in ANOTHER state.

    Only a claim strong enough to overrule a state that rests on a suburb match alone. A
    1-3 word span of the text qualifies when all of these hold:

      * it is a locality in exactly one state, and that state is not `state`;
      * it is at least 5 characters, does not start with a month or "The ", and is not
        the tail of a "The ..." name or a bare street word ("The Grove" -> Grove TAS);
      * it is not the row's own `estate` name. Estates are named after places anywhere in
        the country, and a line quotes its estate: the CORRECT Hattan copy of lot 618 has
        suburb Wollert, estate Riverhills and an address "Lot 618, Riverhills". Without
        this, Riverhills QLD's stock pulls that VIC lot into Queensland -- Colin's bug,
        backwards;
      * the word after it is not a street type, a number (a design size) or a region or
        estate word;
      * the row does not show it to be a design or an estate (not_the_locality_here):
        "Verona SG", "Rhodes Paddington" (design, facade), "Windermere Mambourin" (estate,
        suburb) -- the rule enrich's blank-suburb guess uses too (29 Sep 2026);
      * `stocked` holds (locality lower-case, that state): the catalogue has live rows
        filed under that locality in that state, so the place is where this kind of stock
        actually is rather than a gazetteer coincidence.

    Longest span first, then left to right. None when nothing qualifies. Pure: reads no
    database and writes nothing, so enrich decides what to do with the answer.
    """
    if geo is None or not row_text:
        return None
    from geo import _STREET_TYPE

    own_estate = _estate_key(estate)
    stored = normalise_state(state)
    toks, breaks = _tokens(row_text)
    for size in (3, 2, 1):
        for i in range(len(toks) - size + 1):
            span = toks[i:i + size]
            if any(t[0].isdigit() for t in span):
                continue
            cand = " ".join(span)
            if len(cand) < 5 or _MONTH_WORD.match(span[0]) or span[0].lower() == "the":
                continue
            if i > 0 and toks[i - 1].lower() == "the":
                continue
            if size == 1 and _STREET_TYPE.fullmatch(cand):
                continue
            if own_estate and cand.lower() == own_estate:
                continue
            states = [s for s in geo.states_for_suburb(cand) if s in VALID_STATES]
            if len(states) != 1 or states[0] == stored:
                continue
            nxt = toks[i + size] if i + size < len(toks) else ""
            if nxt and (_STREET_TYPE.fullmatch(nxt) or _NOT_A_LOCALITY_AFTER.match(nxt)):
                continue
            if (cand.lower(), states[0]) not in stocked:
                continue
            if not_the_locality_here(toks, i, size, geo, estate, breaks):
                continue
            return cand, states[0]
    return None


def disagreement(postcode: Any, suburb: str, page_state: Any, geo: Any,
                 lot_number: Any = None, row_text: Any = None) -> Optional[Dict[str, Any]]:
    """Report when the row's own postcode contradicts the page it came from.

    Worth surfacing rather than silently preferring one: it means either a builder has
    listed interstate stock on a state page, or a postcode was misparsed. Both are things
    a human should see, and both would otherwise be invisible.

    `lot_number`: a postcode equal to it is not the row's postcode, so it contradicts
    nothing (postcode_is_lot_number, `row_text` included). enrich clears that postcode itself.
    """
    postcode = _unless_lot_number(postcode, lot_number, row_text, geo)
    pc_state = state_from_postcode(postcode)
    hint = normalise_state(page_state)
    if pc_state and hint and pc_state != hint:
        return {"postcode": str(postcode), "postcode_state": pc_state,
                "page_state": hint, "suburb": suburb}
    return None
