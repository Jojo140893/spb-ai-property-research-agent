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
    the lot as well, with or without a lot number to compare (postcode_opens_the_row). So is
    one they write only as part of another number or as a land area
    (postcode_written_as_another_number): no postcode at all, whatever the name says.
    """
    pc = re.sub(r"\D", "", str(postcode or ""))
    if not pc:
        return False
    if _number(pc) in {_number(n) for n in re.findall(r"\d+", str(lot_number or ""))}:
        return True
    return (postcode_opens_the_row(pc, row_text, geo)
            or postcode_written_as_another_number(pc, row_text, geo))


# --- a postcode the row writes only as some other number ------------------------------
#
# Review, 29 Sep 2026. With the lot numbers out of the way, two stocklists were still
# national on one vote each, and both votes were a four-digit number read as a postcode:
#   * APLACE 12153, "Yarrawonga 2077 Buloke Circuit Glanmire Park Titled 2064 39 x 54
#     $390,000" -- 2064 is the land area, before the lot's 39 x 54 m. Yarrawonga is in NSW
#     and VIC, 2064 is a NSW code, so own_state proved NSW "suburb + postcode", and the VIC
#     sheet's page state was void for its 56 other unsettled rows;
#   * six Mornington storage units, "... Cold Shell 03-7076 71,550.00 ..." -- TAS 7076,
#     Mornington being in TAS and VIC.
# Measured on the 29 Sep copy: 21 stored postcodes appear in their row's text only inside
# a longer number or as an area -- "2730.00", "2066.91m2", "$2576.1 per year", "3815.58",
# the "3653" of an Adobe link's "30d49294- 3653-47da-...", "03-7076", "2064 39 x 54" --
# and not one is a postcode; the other 6,774 are written plainly at least once. A stored
# postcode the text does not contain at all is kept: nothing says it is anything else.
_JOINED_BEFORE = re.compile(r"\d[-.]\s?$")
_JOINED_AFTER = re.compile(r"[-.]\d")
_AREA_AFTER = re.compile(r"\s*(?:m2|m\u00b2|sqm|sq\b)|\s+\d+(?:\.\d+)?\s*[xX]\s*\d",
                         re.IGNORECASE)


def postcode_written_as_another_number(postcode: Any, row_text: Any, geo: Any = None) -> bool:
    """True when the row's own text writes the postcode's number, and every time only as
    part of a longer number ("2730.00", "03-7076", "3653-47da") or as a land area ("2064
    39 x 54", "2066.91m2").

    Before a frontage and depth the number is a postcode after all when a locality of its
    own state is written just before it: "Tarneit 3029 12.5 x 28".
    """
    pc = re.sub(r"\D", "", str(postcode or ""))
    if len(pc) != 4:
        return False
    texts = row_text if isinstance(row_text, (list, tuple)) else [row_text]
    seen = False
    for text in texts:
        text = str(text or "")
        for m in re.finditer(r"(?<![\d.])" + pc + r"(?!\d)", text):
            seen = True
            before, after = text[:m.start()], text[m.end():]
            if _JOINED_BEFORE.search(before) or _JOINED_AFTER.match(after):
                continue
            area = _AREA_AFTER.match(after)
            if area and ("x" in area.group(0).lower()
                         and _locality_of_state_ends(before, state_from_postcode(pc), geo)):
                return False
            if not area:
                return False
    return seen


def postcode_stated(postcode: Any, row_text: Any, geo: Any = None) -> str:
    """The postcode's state when the row's own text writes it where a postcode is written --
    straight after a locality of that state, or after that state's code ("COLAC 3250",
    "Tarneit VIC 3029", "Clifton Springs, VIC 3325") -- and never only as part of another
    number; '' otherwise.

    Round 2, 29 Sep 2026: what "proven by the row's own postcode" means for
    enrich_buildings' bound on a state origin/main would not give. A four-digit number the
    column holds is not enough: "Lot 5882, The Quarry Leppington" stored 5882 -- the lot,
    in SA's range -- and once the guess stopped contradicting it, it alone put a Leppington
    NSW lot in SA.
    """
    pc = re.sub(r"\D", "", str(postcode or ""))
    state = state_from_postcode(pc)
    if len(pc) != 4 or not state:
        return ""
    texts = row_text if isinstance(row_text, (list, tuple)) else [row_text]
    for text in texts:
        text = str(text or "")
        for m in re.finditer(r"(?<![\d.])" + pc + r"(?!\d)", text):
            before, after = text[:m.start()], text[m.end():]
            if _JOINED_BEFORE.search(before) or _JOINED_AFTER.match(after):
                continue
            head = before.rstrip(" ,")
            if (re.search(r"\b" + state + r"$", head)
                    or _locality_of_state_ends(head, state, geo)):
                return state
    return ""


def _locality_of_state_ends(before: str, state: str, geo: Any) -> bool:
    """True when `before` ends with a locality of `state` ("Lot 5 Tarneit")."""
    geo = _gazetteer(geo)
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", before[-60:])
    if not state or geo is None or not words or not re.search(r"[A-Za-z]\W*$", before):
        return False
    if words[-1].upper() in VALID_STATES:                   # "Tarneit VIC 3029"
        words = words[:-1]
    for size in (3, 2, 1):
        if len(words) >= size and state in _places(" ".join(words[-size:]), geo):
            return True
    return False


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
# A dash, colon or bar may stand between the two (review, 29 Sep 2026: "3978 - Clyde
# North" is Clyde North's postcode written first, and needing a bare space dropped it).
_WORDS_AFTER = re.compile(r"(?:\s*[-:|\u2013\u2014]\s*|\s+)"
                          r"([A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,3})")


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
    """True when `rest` opens with a locality of `state` that is not a street's name.

    The LONGEST place name there decides, whatever its state (review, 29 Sep 2026): "2765
    Clyde North" opens with Clyde North, VIC only, so 2765 -- a NSW code -- is the lot,
    although Clyde alone is a NSW place too; the same for "2560 Springfield Lakes" (QLD).
    """
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
            states = geo.states_for_suburb(name)
        except Exception:                                             # noqa: BLE001
            return False
        if states:
            return state in states and not (nxt and _STREET_TYPE.fullmatch(nxt))
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


def suburb_cell_is_the_street(suburb: Any, texts: Any) -> bool:
    """True when the suburb cell ends in a street type and the row's own label or line
    writes it straight after a house number: "21 The Grove" is a street, whatever The
    Grove is in the gazetteer. Measured on the 29 Sep copy: 6 rows prove a state from such
    a cell, all six Boondall (QLD) lots proved NSW by 'The Grove'; the 124 other cells that
    end in a street type are never written after a number, and are left alone."""
    from geo import _STREET_TYPE
    words = str(suburb or "").split()
    if not words or not _STREET_TYPE.fullmatch(words[-1]):
        return False
    pattern = r"\b\d+[A-Za-z]?\s+" + r"\s+".join(map(re.escape, words)) + r"\b"
    return any(re.search(pattern, str(t or ""), re.IGNORECASE) for t in texts)


def own_state(postcode: Any = None, suburb: Any = "", estate: Any = "",
              address: Any = "", geo: Any = None, lot_number: Any = None,
              row_text: Any = None, street_cell: bool = True) -> Tuple[str, str]:
    """What the ROW proves on its own, ignoring every page and file. ('', '') if nothing.

    `lot_number`: a postcode equal to it is no postcode at all (postcode_is_lot_number) --
    the Jubilee lots proved QLD here on "suburb + postcode" from their lot number, 4327.
    `row_text`: the row's line(s); a postcode that is the number opening one is its lot
    too -- AVIA 2234, "2120 Stage 21 ...", proved NSW here off lot 2120.
    `street_cell`: whether `suburb` is the row's suburb COLUMN, which proves nothing when
    it is the row's own street (suburb_cell_is_the_street). False for a suburb enrich
    guessed from the label, which is read as origin/main read it (round 2, 29 Sep 2026: the
    rule is about what an extractor put in the column; applied to a guess it took the
    proof off "Lot 75, The Grove" off the NSW page, and origin/main's row-text check then
    moved that lot to WA on another word of its line).

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
        #
        # Nor a cell that is the row's own STREET (review, 29 Sep 2026): Creation Homes'
        # QLD sheet has "21 The Grove Boondall Rosewood 215 ..." with 'The Grove' in the
        # suburb column, and The Grove is a NSW locality -- the one NSW vote that kept that
        # Queensland sheet national, its page state void for 61 rows.
        if not (street_cell and suburb_cell_is_the_street(suburb, (address,) + tuple(
                row_text if isinstance(row_text, (list, tuple)) else (row_text,)))):
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
#
# origin/main's list, word for word (29 Sep 2026, round 2). text_locality_elsewhere scans
# with it exactly as origin/main does, so the misfile check can never name a place that
# origin/main's would not; the first cut added sg|dg here, which let the scan run on past
# a skipped "Verona SG" to some other name origin/main never reached.
_NOT_A_LOCALITY_AFTER = re.compile(
    r"^(?:point|park|springs|estate|heights|homes|group|house|range|river|lakes?|green|"
    r"rise|views?|hills?|waters|shores|quarter|collection|coast|single|double|storey|"
    r"custom|facade|\d+(?:\.\d+)?)$", re.IGNORECASE)
# A word that, straight after a name, is a house design's own garage or facade -- "Verona
# SG Corner", "Vesper DG", "Rhodes Fortitude" -- so the name is the design's, whatever else
# the row says. Garage codes and AVIA's facades that are no place's name (once AVIA's sheet
# stopped being national, "91 Stage 2 354m2 ... Rhodes Fortitude 180m2" (12359) was filed
# QLD and the replay read Rhodes, NSW, in it), with the data's own typos: "Verona
# SGCormer" (17430), "Fortitue". (Its other facades -- Paddington, Yamba, Newstead -- are
# places, and 'followed' reads them.) "Dual" is NOT one of these: "Weston Available 14
# Weston Dual Living ..." is G Developments' estate named after its suburb.
_DESIGN_VARIANT_AFTER = re.compile(
    r"^(?:facade|sg|dg|sgcorner|sgcormer|dgcorner|fortitude|fortitdue|fortitue|carolina|"
    r"caolina|lennox|padington)$", re.IGNORECASE)
# A facade STYLE after a name. Measured on the 29 Sep copy: every place name followed by
# one of these is a design -- Verona 33, Magnolia 45 (with "Magnolia Tradtional", 12736),
# Somerset, Chelsea, Moogerah, Roseberry, Kuraby -- never the row's suburb. But a style
# word CAN follow a suburb in a layout the night did not hold, and review round 1 wrote
# three ("Lot 5 Tarneit Traditional", "Lot 12 Point Cook Coastal Village", "Lot 5 Glenroy
# Classic") that the first cut left blank where origin/main read the suburb. So a style
# makes the name a design when (29 Sep 2026, round 2):
#   * the design's layout follows it, as it does in every one of those rows: its size or
#     frontage ("Somerset Coastal 4 2 2", "Verona Traditional 12"), a storey count or
#     "Light"/"OG" ("Magnolia Traditional Double", "Rosebery Traditional Light Single");
#   * or nothing follows it but punctuation or the end of the text, and the catalogue does
#     not stock the name as a place in any state (`stocked`, where the caller has it):
#     "Lot 5 Tarneit Traditional" is a Tarneit lot, Tarneit VIC holding live stock, while
#     Magnolia, Verona and Eden hold none.
# Never when another word follows ("Point Cook Coastal Village" is Point Cook's estate).
# Where the reading is not sure, origin/main's guess stands (reading_refuses).
_STYLE_AFTER = re.compile(r"^(?:traditional|tradtional|trad|modern|coastal|contemporary|"
                          r"hamptons|classic)$", re.IGNORECASE)
_AFTER_A_STYLE = re.compile(r"^(?:\d.*|single|double|storey|custom|light|og)$", re.IGNORECASE)
# A word that, straight after a name, is the PRODUCT -- storey count or a custom design --
# which follows a design and a suburb alike: "HARWOOD SINGLE", "CONCORD SINGLE" (designs),
# but "Lot 50, Springvale Double" (11 Hattan rows), "Crangan Bay ... 701 Crangan Bay Custom
# Dual Occ" and "Available Clyde North Double Storey" (suburbs). Review, 29 Sep 2026: the
# first cut refused these as designs, which dropped Springvale off 11 rows and, worse, let
# a shorter name inside the refused one through ("Fraser Rise Double Storey" -> Fraser,
# ACT). So they DEMOTE the name in the blank-suburb guess instead of refusing it, and
# design_the_state_rests_on does not count them.
_PRODUCT_AFTER = re.compile(r"^(?:single|double|storey|custom)$", re.IGNORECASE)
_ESTATE_WORD = re.compile(r"^estates?$", re.IGNORECASE)
# The first word of a region or a longer name, whose last word alone is some other place:
# "SOUTHERN HIGHLANDS" is not Highlands VIC, "LAKE MACQUARIE" is not Macquarie TAS.
_NAME_HEAD = re.compile(r"^(?:north|south|east|west|northern|southern|eastern|western|"
                        r"central|upper|lower|greater|inner|outer|lake|mount|mt|port|"
                        r"cape)$", re.IGNORECASE)
# The heads of those that only ever begin a region: "SOUTHERN HIGHLANDS", "LAKE MACQUARIE".
_REGION_HEAD = re.compile(r"^(?:northern|southern|eastern|western|central|upper|lower|"
                          r"greater|inner|outer|lake)$", re.IGNORECASE)
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


def _stocked_anywhere(name: str, stocked: Any) -> bool:
    low = name.lower()
    return bool(stocked) and any((low, s) in stocked for s in VALID_STATES)


def _design_word(toks: List[str], breaks: Any, j: int, geo: Any, name: str,
                 stocked: Any = None) -> bool:
    """True when toks[j], straight after the name, makes it a house design's (see
    _DESIGN_VARIANT_AFTER and _STYLE_AFTER)."""
    word = toks[j]
    after = toks[j + 1] if j + 1 < len(toks) and j + 1 not in breaks else ""
    if _DESIGN_VARIANT_AFTER.match(word):
        # Not the first word of a place of its own: "Ballina Lennox Head".
        return not (after and _places(word + " " + after, geo))
    if _STYLE_AFTER.match(word):
        if after:
            return bool(_AFTER_A_STYLE.match(after))
        # Nothing after the style: the layout does not say, so the catalogue does -- a
        # name it stocks is a suburb ("Lot 5 Tarneit Traditional"), while a stocked name
        # with the layout after it is still the design ("Rosebery Traditional Light
        # Single", 19725: Rosebery NSW is stocked, and this is Creation Homes' design).
        return not _stocked_anywhere(name, stocked)
    return False


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
                          breaks: Any = (), state: Any = "", suburb: Any = "",
                          stocked: Any = None) -> str:
    """Why the place name toks[i:i+size] is not the lot's locality in this row, or ''.

    'design'   the next word is a design's garage or facade ("Verona SG", "Rhodes
               Fortitude"), or a facade style with the design's layout after it
               ("Magnolia Traditional Double") -- or with nothing after it, on a name
               `stocked` does not hold anywhere (_design_word);
    'product'  the next word is a storey count or "Custom" ("HARWOOD SINGLE", but also
               "Lot 50, Springvale Double"), which follows a design and a suburb alike --
               a doubt, not a refusal (see _PRODUCT_AFTER);
    'followed' straight after it, with nothing but a space between, comes another place
               name that does not extend it: a house design and its facade ("Rhodes
               Paddington"), an estate and its suburb ("Windermere Mambourin", "The Grove
               Tarneit", "Oaklands Bonnie Brook"). The FIRST name is not the locality
               then, when the second could be. "Clyde North", "Point Cook" and "Box Hill
               North" extend their first words, so they are one place. The name after does
               not count when it is itself the name of an estate or a street ("New Beith
               Round Mountain Estate", the Silkwood layout, suburb then estate; "Joyner
               Oxford Street", "Glenvale Alford Grove"), nor when `state` is given and it
               is not a place there ("Flemington Racecourse" on a VIC list: Racecourse is
               in QLD) -- nor, it being the other reading of the same two words, is the
               first name then 'followed': "Joyner Banksia" on a QLD page is Joyner;
    'estate'   the row's own estate: "Estate" follows ("Round Mountain Estate"), or the span
               IS the row's estate name. An estate is often named after its own suburb
               ("Wadalba Estate" in Wadalba), so the guess DEMOTES this rather than refuse it.

    'followed' reads two names side by side, and the same layout is just as often a SUBURB
    and then the word that caused a misfile -- its estate, design or street (review, 29 Sep
    2026: the first cut blinded the row-text check to exactly that). So a name is never
    'followed' when:
      * the name after it repeats it: "Mambourin Mambourin Green", "Fraser Rise Fraser Rise"
        -- a suburb and the estate named after it, or a doubled cell. A design and its
        facade are never one word twice;
      * it is written straight after a street type: "63 Oxford Street Joyner Banksia 183",
        "Trawler Walk Tarneit The Grove", "19 The Grove Boondall Rosewood 215". A name
        after the street is the address's locality, the fact geo.find_suburb_in_text
        leans on the other way round;
      * `suburb` -- the row's suburb column, the word its state rests on -- is this name or
        the name after it: "Tarneit The Grove" filed NSW on a suburb column 'The Grove' is
        the misfile itself, not a design and its facade.

    `toks`/`breaks` are _tokens' split of the row, numbers included, so a number between
    two names ("Tarneit 300 ... Enzo") keeps them apart, and so does punctuation.
    """
    name = " ".join(toks[i:i + size])
    j = i + size
    nxt = toks[j] if j < len(toks) and j not in breaks else ""
    if nxt and _design_word(toks, breaks, j, geo, name, stocked):
        return "design"
    if nxt and _PRODUCT_AFTER.match(nxt):
        return "product"
    column = " ".join(str(suburb or "").lower().split())
    if (nxt and name.lower() != column and not _after_a_street(toks, i)
            and _place_follows(toks, j, name, geo, breaks, state, column)):
        return "followed"
    if nxt and _ESTATE_WORD.match(nxt):
        return "estate"
    own = _estate_key(estate)
    if own and name.lower() == own:
        return "estate"
    return ""


def _after_a_street(toks: List[str], i: int) -> bool:
    """True when toks[i] comes straight after a street type ("Oxford Street Joyner")."""
    from geo import _STREET_TYPE
    return i > 0 and bool(_STREET_TYPE.fullmatch(toks[i - 1]))


def _place_follows(toks: List[str], j: int, name: str, geo: Any, breaks: Any = (),
                   state: Any = "", column: str = "") -> bool:
    """True when toks[j:] opens with a place name, standing on its own, that does not
    extend `name`, repeat it or be the row's suburb `column` (not_the_locality_here,
    'followed'). A place name means the gazetteer's (data/au_suburbs.csv), never the
    catalogue's stock, which files AVIA's facade Paddington as a QLD suburb."""
    from geo import _STREET_TYPE
    st = normalise_state(state)
    own = [t.lower() for t in name.split()]
    if [t.lower() for t in toks[j:j + len(own)]] == own:
        return False
    for size in (3, 2, 1):
        span = toks[j:j + size]
        if (len(span) < size or any(t[0].isdigit() or len(t) < 2 for t in span)
                or any(k in breaks for k in range(j + 1, j + size))):
            continue
        after = " ".join(span)
        if column and after.lower() == column:
            return False
        states = _places(after, geo)
        if not states or (st and st not in states) or _places(name + " " + after, geo):
            continue
        # Two places that name the same state(s) leave nothing to choose between (round 2,
        # 29 Sep 2026): "Lot 9, Ellenbrook Springs" is Ellenbrook, WA, and "Lot 39, Logan
        # Reserve Riverleigh" Logan Reserve, QLD, whichever of the two is the estate.
        if sorted(states) == sorted(_places(name, geo)):
            continue
        k = j + size
        if k < len(toks) and k not in breaks and (_ESTATE_WORD.match(toks[k])
                                                  or _STREET_TYPE.fullmatch(toks[k])):
            # The estate's or street's name, whole: a shorter name inside it is no second
            # place either (round 2, 29 Sep 2026: "Sebastopol Ringwood North Estate" read
            # Ringwood, and Sebastopol was refused for the estate's first word).
            return False
        return True
    return False


def _place_spans(toks: List[str], breaks: Any, geo: Any) -> Dict[Tuple[int, int], List[str]]:
    """{(i, size): states} for every 1-3 word span of the row that is a place name: no
    number or one-letter word in it and no punctuation inside it."""
    spans: Dict[Tuple[int, int], List[str]] = {}
    for size in (3, 2, 1):
        for i in range(len(toks) - size + 1):
            span = toks[i:i + size]
            if (any(t[0].isdigit() or len(t) < 2 for t in span)
                    or any(k in breaks for k in range(i + 1, i + size))):
                continue
            states = _places(" ".join(span), geo)
            if states:
                spans[(i, size)] = states
    return spans


def _inside_a_longer_place(spans: Any, i: int, size: int, state: Any = "") -> bool:
    """True when toks[i:i+size] lies inside a longer place name the row writes -- one that
    is not a place of `state`, when that is given.

    Review, 29 Sep 2026: "Fraser Rise Double Storey" with Fraser Rise doubted gave Fraser,
    ACT, and "Marsden Park Single Storey" Marsden, QLD. The row wrote the longer name; a
    name inside it is not a second place, whatever is decided about the longer one.
    `state` is for a caller that asks only which STATE the text names
    (text_locality_elsewhere): Cranbourne inside "Cranbourne East" names Victoria as surely
    as the whole name does.
    """
    return any(s > size and i2 <= i and i + size <= i2 + s
               and not (state and state in spans[(i2, s)]) for (i2, s) in spans)


def _inside_a_longer_place_of(spans: Any, i: int, size: int, state: str) -> bool:
    """True when toks[i:i+size] lies inside a longer place name the row writes that IS a
    place of `state` -- the page-state pass of suburb_in_row_text (round 2, 29 Sep 2026):
    on a NSW page "Lot 77 Brooklyn Park Rise" is Brooklyn, NSW, not Brooklyn Park, SA, and
    "Clyde North" is Clyde, as origin/main read them. A longer name in ANOTHER state does
    not beat a place of the page's state; which of the two the row means cannot be told
    from the words (the one reading moved an Eternal Homes NSW lot to SA over its page),
    so the page's is taken, and a longer name wins only where no page says otherwise."""
    return any(s > size and i2 <= i and i + size <= i2 + s and state in spans[(i2, s)]
               for (i2, s) in spans)


def _place_before(spans: Any, toks: List[str], breaks: Any, i: int, size: int,
                  geo: Any) -> Optional[List[str]]:
    """The states of the place name written straight before toks[i:i+size] -- with nothing
    but a space between, not inside a longer name, not the same words -- or None."""
    if i == 0 or i in breaks:
        return None
    name = " ".join(toks[i:i + size]).lower()
    for (i2, s2), states in spans.items():
        if i2 + s2 != i or _inside_a_longer_place(spans, i2, s2):
            continue
        if " ".join(toks[i2:i]).lower() == name:
            continue
        return states
    return None


def _places(name: str, geo: Any) -> List[str]:
    try:
        return list(geo.states_for_suburb(name))
    except Exception:                                                 # noqa: BLE001
        return []


def suburb_in_row_text(text: Any, geo: Any, prefer: Any = "", estate: Any = "",
                       stocked: Any = None) -> Optional[str]:
    """The locality a row's own label or line names, for a row with a blank suburb column.

    enrich's blank-suburb READING (enrich_buildings.guess_suburb decides whether to use it
    or origin/main's guess). It replaces geo.find_suburb_in_text, which had two faults for
    this job besides the words not_the_locality_here refuses:
      * with no state to prefer it walked the states in the gazetteer file's order, which
        starts with NSW -- so any NSW name anywhere in a line beat the real locality of
        every other state: "The Grove" over Tarneit, "Round Mountain" over New Beith,
        "Clyde" over Clyde North, "Verona" over nothing;
      * enrich passed it the row's STORED state, which the guess itself had decided on an
        earlier night -- so a wrong guess preferred itself for good.

    So:
      1. "X, Locality": the last comma-separated part of a label is where the locality is
         written ("5629 The Grove, Tarneit" -> Tarneit), the same fact
         geo.resolve_locality leans on -- not a region's tail ("SOUTHERN HIGHLANDS"), not a
         council, and not a word of the row's own estate name ("Lot 518, Davis Vineyard",
         estate Davis Vineyard, is no Vineyard NSW lot). When that part writes two or more
         places (round 2, 29 Sep 2026): the one straight after a street type ("Wiltipoll
         Road Mambourin Windermere" -> Mambourin), else the one in `prefer`'s state
         ("63 Oxford Street, Joyner Banksia" on a QLD page -> Joyner, "Lot 5, Wyndham Vale
         Jubilee" on a VIC page -> Wyndham Vale, "Lot 5, Redbank Plains Sienna Eden" ->
         Redbank Plains), else one only if they all name the same states. Otherwise the
         label cannot tell, and step 2 reads the whole text.
      2. Otherwise every 1-3 word span that is a locality: those of `prefer` first (the
         page's state -- never one enrich wrote), then every state, longest span first and
         then left to right. A name of the page's state that is only doubted (below) is
         taken before any other state's plain name ("Available Clyde North Double Storey
         ... | Ceduna 9.66" on a VIC page is Clyde North, not Ceduna, SA).
    Refused: 'design' and 'followed' (not_the_locality_here), a month ("March 2027"), a
    bare street word ("Grove"), a name inside a longer place name the row writes
    (_inside_a_longer_place: not Fraser out of "Fraser Rise" -- in the page pass only a
    longer name of the page's state counts, see _inside_a_longer_place_of), and, where no
    page state picks, the second of two places side by side whose states differ ("Lot 1,
    Riverstone Springs" is neither Riverstone, NSW, nor Springs, WA, as far as its words
    can say). Demoted to a last resort, as find_suburb_in_text demotes a street's name,
    best first: 'product' ("Lot 50, Springvale Double" -- when nothing else in the row is
    a place, the name before the storey count is the lot's suburb); 'estate' -- with every
    shorter name inside it, so "Innes Park Estate" cannot hand the lot to Innes; and a name
    followed by a street type: an estate is often named after its suburb, a name before
    "Rd" is a street's ("114 Neale Rd, Parkside", estate Parkside, is in Parkside). None
    when nothing qualifies.

    `stocked`, where the caller has the catalogue's {(locality, state)}, keeps a facade
    style from refusing a name the catalogue stocks (_design_word).
    """
    if geo is None or not text:
        return None

    raw = str(text)
    first = normalise_state(prefer)
    # last resorts, best first
    fallback: Dict[str, List[str]] = {"product": [], "estate": [], "street": []}

    # 1. the last part of an "X, Locality" label
    for name, how in _label_tail_verdicts(raw, geo, estate, first, stocked):
        if how == "take":
            return name.title()
        if how in fallback:
            fallback[how].append(name.title())

    # 2. every span of the whole text
    toks, breaks = _tokens(raw)
    spans = _place_spans(toks, breaks, geo)
    for only in ([first] if first else []) + [""]:
        estate_words: set = set()
        doubted: Dict[str, List[str]] = {"product": [], "estate": [], "street": []}
        for size in (3, 2, 1):
            for i in range(len(toks) - size + 1):
                states = spans.get((i, size))
                if not states or (only and only not in states):
                    continue
                if (_inside_a_longer_place_of(spans, i, size, only) if only
                        else _inside_a_longer_place(spans, i, size)):
                    continue
                # The tail of a region's name, as step 1 refuses it: "129 Millfield Rise,
                # SOUTHERN HIGHLANDS" is not Highlands, VIC, "3 Watson Road, LAKE
                # MACQUARIE" not Macquarie (9612-9616, 29 Sep 2026). Not after North, South
                # and the like, which also start a development's name: "stage 2 North
                # Flemington Racecourse" is a Flemington lot (10243, live).
                if i and i not in breaks and _REGION_HEAD.match(toks[i - 1]):
                    continue
                # Two places side by side, with no page state to pick between them: the
                # first is refused as 'followed' below, and the second is no surer --
                # "Wyndham Vale Jubilee" is a suburb and its estate, "Windermere Mambourin"
                # an estate and its suburb, "Riverstone Springs" an estate alone (round 2,
                # 29 Sep 2026: the first cut took Jubilee, QLD, for a Wyndham Vale lot).
                if not only:
                    before = _place_before(spans, toks, breaks, i, size, geo)
                    if before is not None and sorted(before) != sorted(states):
                        continue
                name = " ".join(toks[i:i + size])
                how = _verdict(toks, breaks, i, size, geo, estate, only, estate_words,
                               stocked)
                if how == "take":
                    return name.title()
                if how in doubted:
                    doubted[how].append(name.title())
        if only:
            # A name of the page's state that is only doubted beats every other state's
            # plain name (round 2, 29 Sep 2026: "Lot 50 Springvale Single ... Verona 20" on a
            # VIC page gave Verona, NSW; "Lot 3 Wadalba Estate Ceduna 9.66" Ceduna, SA).
            for kind in ("product", "estate", "street"):
                if doubted[kind]:
                    return doubted[kind][0]
        else:
            for kind in fallback:
                fallback[kind] += doubted[kind]
    for kind in ("product", "estate", "street"):
        if fallback[kind]:
            return fallback[kind][0]
    return None


def _verdict(toks: List[str], breaks: Any, i: int, size: int, geo: Any, estate: Any = "",
             state: Any = "", estate_words: Any = None, stocked: Any = None) -> str:
    """suburb_in_row_text's reading of the place name toks[i:i+size]: 'take', 'refuse', or
    the demotion 'product' / 'estate' / 'street'."""
    from geo import _STREET_TYPE
    span = toks[i:i + size]
    if _MONTH_WORD.match(span[0]) or (size == 1 and _STREET_TYPE.fullmatch(span[0])):
        return "refuse"
    why = not_the_locality_here(toks, i, size, geo, estate, breaks, state, stocked=stocked)
    if why in ("design", "followed"):
        return "refuse"
    if why == "product":
        return "product"
    j = i + size
    nxt = toks[j] if j < len(toks) and j not in breaks else ""
    if why == "estate" or (estate_words and estate_words & set(range(i, j))):
        if estate_words is not None:
            estate_words.update(range(i, j))
        return "estate"
    if nxt and _STREET_TYPE.fullmatch(nxt):
        return "street"
    return "take"


def label_tail_is_the_pair(label: Any, one: Any, other: Any) -> bool:
    """True when the last part of an "X, <A> <B>" label is nothing but the two place names
    `one` and `other`, in either order ("Lot 27102, Windermere Mambourin"; "Lot 11950,
    Warralily Armstrong Creek") -- the part suburb_in_row_text's step 1 reads, split and
    trimmed the same way.

    29 Sep 2026, round 3 (enrich_buildings.page_and_guess_hold): such a part is an estate
    and a locality, and nothing in it says which is which, so a page that picked one of
    them is not backed by the label against the other."""
    parts = [p.strip() for p in re.split(r",(?!\d)", str(label or ""))]
    parts = [p for p in parts if p and not re.fullmatch(
        r"[\s\d]*|" + _STATE_CODE + r"(?:\s+\d{4})?", p)]
    if len(parts) < 2:
        return False
    last = re.sub(r"\s*\([^)]*\)", " ", parts[-1])
    last = re.sub(r"(?:\s+" + _STATE_CODE + r")?(?:\s+\d{4})?\s*$", "", last).strip()
    low = [t.lower() for t in _tokens(last)[0]]
    a = [w.lower() for w in _TEXT_TOKEN.findall(str(one or ""))]
    b = [w.lower() for w in _TEXT_TOKEN.findall(str(other or ""))]
    return bool(a and b) and low in (a + b, b + a)


def _label_tail_verdicts(raw: str, geo: Any, estate: Any = "", prefer: Any = "",
                         stocked: Any = None) -> List[Tuple[str, str]]:
    """[(name, verdict)] for the place names the last part of an "X, Locality" label
    writes -- step 1 of suburb_in_row_text. At most one is 'take', and only when the part
    says which place it means; the rest are demotions or 'undecided'. Empty when the text
    has no such part: one part only, or a last part that is a council or region."""
    # A comma straight before a digit is a thousands separator ("$1,084,400"), not an
    # address comma.
    parts = [p.strip() for p in re.split(r",(?!\d)", raw)]
    parts = [p for p in parts if p and not re.fullmatch(
        r"[\s\d]*|" + _STATE_CODE + r"(?:\s+\d{4})?", p)]
    if len(parts) < 2 or _ADMIN_AREA.search(parts[-1]):
        return []
    # "Marsden Park (Lot 1) (Block B)", "COLAC 3250", "Tarneit VIC 3029" -> the name
    last = re.sub(r"\s*\([^)]*\)", " ", parts[-1])
    last = re.sub(r"(?:\s+" + _STATE_CODE + r")?(?:\s+\d{4})?\s*$", "", last).strip()
    toks, breaks = _tokens(last)
    spans = _place_spans(toks, breaks, geo)
    first = normalise_state(prefer)
    own = _estate_key(estate).split()
    out: List[Tuple[str, str]] = []
    takes: List[Tuple[int, int, str, List[str]]] = []
    for (i, size), states in sorted(spans.items(), key=lambda kv: (-kv[0][1], kv[0][0])):
        if _inside_a_longer_place(spans, i, size) or (i and _NAME_HEAD.match(toks[i - 1])):
            continue
        name = " ".join(toks[i:i + size])
        page = first if first in states else ""
        how = _verdict(toks, breaks, i, size, geo, estate, page, None, stocked)
        # A word of the row's own estate name is the estate's, not a place after it (round
        # 2, 29 Sep 2026): "Lot 518, Davis Vineyard" with estate 'Davis Vineyard' guessed
        # Vineyard, NSW, for a Diggers Rest VIC lot.
        words = [t.lower() for t in toks[i:i + size]]
        if how == "take" and own and any(own[k:k + size] == words
                                         for k in range(len(own) - size + 1)):
            how = "estate"
        if how == "take" and not page:
            before = _place_before(spans, toks, breaks, i, size, geo)
            if before is not None and sorted(before) != sorted(states):
                how = "undecided"
        if how == "take":
            takes.append((i, size, name, states))
        else:
            out.append((name, how))
    chosen = None
    after_street = [t for t in takes if _after_a_street(toks, t[0])]
    in_page = [t for t in takes if first and first in t[3]]
    if after_street:
        chosen = after_street[0]
    elif in_page:
        chosen = in_page[0]
    elif takes and len({tuple(sorted(t[3])) for t in takes}) == 1:
        chosen = takes[0]
    for t in takes:
        if t is chosen:
            out.insert(0, (t[2], "take"))
        else:
            out.append((t[2], "undecided"))
    return out


def reading_refuses(text: Any, name: Any, geo: Any, stocked: Any = None) -> bool:
    """True when suburb_in_row_text refuses `name` wherever `text` writes it, for a reason
    the row itself makes sure of: a month, a bare street word, a house design with its
    garage code or its layout after it (not_the_locality_here's 'design',
    _laid_out_as_a_design), the tail of a region's name, or a name inside a longer place
    name the row writes -- or the row never writes it as one name at all: origin/main's
    guess reads words across numbers and punctuation, and "LOT 1030 POUND RD, CLYDE NORTH -
    7 STAR ENERGY RATING" gave North Star, NSW, for a Clyde North lot. Not 'followed', nor
    two places side by side that no page picks between -- those readings the words cannot
    settle.

    enrich_buildings.guess_suburb asks this of origin/main's guess (round 2, 29 Sep 2026):
    a refusal the row is sure of stands ("Verona SG Corner", "Magnolia Traditional Double",
    "129 Millfield Rise, SOUTHERN HIGHLANDS"), and one it is not sure of gives origin/main's
    guess back ("Grange West 56 Werribee Titled 400" -- Werribee, VIC, where the reading
    took Grange, QLD or SA).
    """
    from geo import _STREET_TYPE
    if geo is None or not text or not name:
        return False
    toks, breaks = _tokens(text)
    spans = _place_spans(toks, breaks, geo)
    want = [w.lower() for w in _TEXT_TOKEN.findall(str(name))]
    seen = False
    for i in range(len(toks) - len(want) + 1):
        if [t.lower() for t in toks[i:i + len(want)]] != want:
            continue
        if any(k in breaks for k in range(i + 1, i + len(want))):
            continue
        seen = True
        size = len(want)
        sure = (_MONTH_WORD.match(toks[i])
                or (size == 1 and _STREET_TYPE.fullmatch(toks[i]))
                or _inside_a_longer_place(spans, i, size)
                or (i and i not in breaks and _REGION_HEAD.match(toks[i - 1]))
                or (not_the_locality_here(toks, i, size, geo, "", breaks,
                                          stocked=stocked) == "design"
                    and _laid_out_as_a_design(toks, breaks, i + size)))
        if not sure:
            return False
    return True


def _laid_out_as_a_design(toks: List[str], breaks: Any, j: int) -> bool:
    """True when the word at toks[j] is a garage or facade code, or a facade style with the
    design's own layout after it -- its size, a storey count, "Light", "OG". A style that
    ends the text ("Lot 5 Glenroy Classic") is a design as far as the reading goes, but not
    surely enough to take away a state origin/main gives (review round 1: a settled VIC
    'Glenroy Classic' went blank)."""
    if j >= len(toks) or j in breaks:
        return False
    if _DESIGN_VARIANT_AFTER.match(toks[j]):
        return True
    return bool(j + 1 < len(toks) and j + 1 not in breaks and _AFTER_A_STYLE.match(toks[j + 1]))


def extends_the_guess(reading: Any, origin: Any, text: Any) -> bool:
    """True when the reading is a longer place name the row writes around origin/main's
    guess -- "Clyde North" for Clyde, "Wyndham Vale" for Wyndham, "Springvale South" for
    Springvale (round 1's correction C: 24 Hattan email rows, no page state, read in full)
    -- and the word after it is no street, estate or region word: "Lot 77 Brooklyn Park
    Rise" is a street in Dora Creek, not Brooklyn Park, SA."""
    from geo import _STREET_TYPE
    long_ = [w.lower() for w in _TEXT_TOKEN.findall(str(reading or ""))]
    short = [w.lower() for w in _TEXT_TOKEN.findall(str(origin or ""))]
    if not short or len(long_) <= len(short):
        return False
    if not any(long_[k:k + len(short)] == short for k in range(len(long_) - len(short) + 1)):
        return False
    toks, breaks = _tokens(text)
    low = [t.lower() for t in toks]
    for i in range(len(low) - len(long_) + 1):
        if low[i:i + len(long_)] != long_:
            continue
        j = i + len(long_)
        nxt = toks[j] if j < len(toks) and j not in breaks else ""
        if not nxt or not (_STREET_TYPE.fullmatch(nxt) or _NOT_A_PLACE_AFTER.match(nxt)):
            return True
    return False


def longer_name_of_the_line(reading: Any, origin: Any, line: Any, geo: Any,
                            estate: Any = "") -> str:
    """The one state of `reading` when it is round 1's longer name as the row's own LINE
    writes it (enrich_buildings.longer_name_stands); '' otherwise.

    Round 3, 29 Sep 2026. Round 2 took the longer name around origin/main's guess wherever
    the LABEL wrote it, and both reviews found that a road to any state at all: a label is
    as often "Lot <n>, <estate>", and estates are named after places anywhere -- "Lot 3772,
    Redwood Park" on a Clyde North lot went to SA, "Lot 340, Boat Harbour Beach" on a
    Caringbah NSW 2229 lot to TAS, "LOT 58 MONTANA CRES, DIGGERS REST 3427, Verona Sands"
    to TAS. The 23 rows it was for (Hattan's email lists) write the name in the line, and
    first: "Available Wyndham Vale Q3 2026 Townhouse ...". So all of these must hold:
      * it extends the guess and the line writes it with no street, estate or region word
        after it (extends_the_guess, on the line, not the label);
      * the line opens with it, after nothing but its availability (opens_with);
      * it is a place of exactly one state;
      * it is not the row's estate, a part of it, or around it ("Lot 44, Brooklyn Park",
        estate Brooklyn Park; "Lot 1768, Emu Downs").
    """
    if geo is None or not str(line or "").strip():
        return ""
    if not (extends_the_guess(reading, origin, line) and opens_with(line, reading, geo)):
        return ""
    states = _places(str(reading), geo)
    if len(states) != 1:
        return ""
    long_ = [w.lower() for w in _TEXT_TOKEN.findall(str(reading))]
    own = _estate_key(estate).split()

    def _within(a, b):
        return bool(a) and any(b[k:k + len(a)] == a for k in range(len(b) - len(a) + 1))

    if own and (_within(own, long_) or _within(long_, own)):
        return ""
    return states[0]


# The words a stocklist line may open with before its locality: its availability.
_OPENING_STATUS = re.compile(r"^(?:available|register|now|sold|on|hold|under|offer|"
                             r"reserved|release)$", re.IGNORECASE)


def opens_with(text: Any, name: Any, geo: Any) -> bool:
    """True when `text` opens with the place name `name`, after nothing but its availability
    ("Available Clyde North ...", "REGISTER NOW New Beith ...") -- and so no other place
    name comes before it. Round 3, 29 Sep 2026 -- enrich_buildings.longer_name_stands: the
    line of every row round 1's longer name put right opens with its locality ("Available
    Wyndham Vale Q3 2026 ...", "Available Springvale South Titled ..."), while a place
    further in is an estate, a design or a region as often: "2-Part Detached SS North 762
    Moore Park Beach MunnoParaDowns" (a review's generated Verv row) is the estate Moore
    Park Beach before an SA suburb written as one word, as "4327 Jubilee Wyndhamvale" is."""
    want = [w.lower() for w in _TEXT_TOKEN.findall(str(name or ""))]
    if geo is None or not want:
        return False
    toks, breaks = _tokens(text)
    spans = _place_spans(toks, breaks, geo)
    starts = [i for (i, size) in spans
              if size == len(want) and [t.lower() for t in toks[i:i + size]] == want]
    if not starts:
        return False
    at = min(starts)
    return (all(_OPENING_STATUS.match(t) for t in toks[:at])
            and not any(i < at for (i, _size) in spans))


def states_written_in(row_text: Any, geo: Any = None) -> set:
    """Every state the row's own text states outright: a state's code as a capitalised word
    or its name spelled out ("CARINGBAH, NSW, 2229", "Dual Key QLD"), and any four-digit
    number written where a postcode is written (postcode_stated: "DIGGERS REST 3427") --
    whether or not the postcode column holds it. Round 3, 29 Sep 2026: a longer name that
    names another state than one of these is not taken (enrich_buildings.
    longer_name_stands)."""
    texts = row_text if isinstance(row_text, (list, tuple)) else [row_text]
    out = set()
    for text in texts:
        text = str(text or "")
        out.update(m.group(1) for m in _STATE_TOKEN.finditer(text))
        low = text.lower()
        out.update(st for word, st in _STATE_WORDS.items()
                   if re.search(r"\b" + word + r"\b", low))
        for m in re.finditer(r"(?<![\d.])\d{4}(?!\d)", text):
            st = postcode_stated(m.group(0), texts, geo)
            if st:
                out.add(st)
    return out


def places_after_a_street(row_text: Any, geo: Any = None) -> set:
    """The states of every place of ONE state the row writes straight after a street --
    the locality of its address ("Lot 7, Redhead Street, Jetsonville" -> TAS), with a comma
    or nothing between (29 Sep 2026, round 3). Only a name standing on its own: not one a
    longer place name the row writes holds, nor one with a street type of its own after it
    (a second street), nor a month.

    A review of round 2 found round 1's longer-name correction winning over that address:
    "Available Regents Park Dc Lot 7, Redhead Street, Jetsonville Acacia - Haven ..." went
    to NSW on "Regents Park Dc" around origin/main's guess Regents Park, though the row
    writes a one-state TAS place after its street. enrich_buildings.longer_name_stands
    counts these as states the row names."""
    from geo import _STREET_TYPE
    if geo is None:
        return set()
    texts = row_text if isinstance(row_text, (list, tuple)) else [row_text]
    out = set()
    for text in texts:
        toks, breaks = _tokens(text)
        spans = _place_spans(toks, breaks, geo)
        for j, tok in enumerate(toks):
            if j == 0 or not _STREET_TYPE.fullmatch(tok) or toks[j - 1][:1].isdigit():
                continue
            for size in (3, 2, 1):
                states = spans.get((j + 1, size))
                if not states or len(states) != 1 or _MONTH_WORD.match(toks[j + 1]):
                    continue
                if _inside_a_longer_place(spans, j + 1, size):
                    continue
                k = j + 1 + size
                if k < len(toks) and _STREET_TYPE.fullmatch(toks[k]):
                    continue
                out.add(states[0])
                break
    return out


# The words of _NOT_A_LOCALITY_AFTER that make a name part of a street's or an estate's --
# not its numbers, storey counts or "House", which follow a suburb in round 1's own rows
# ("Available Clyde North House 263", "Available Clyde North Double Storey", "Available
# Wyndham Vale Q3 2026").
_NOT_A_PLACE_AFTER = re.compile(
    r"^(?:point|park|springs|estate|heights|range|river|lakes?|green|rise|views?|hills?|"
    r"waters|shores|quarter)$", re.IGNORECASE)


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
    garage or facade word (not_the_locality_here's 'design'). A name also written any other
    way in the row is its locality as far as the row can say. Not a storey count
    ('product'): "Available Tarneit Double" is a Tarneit lot. NOT 'followed':
    two place names side by side are a design and its facade ("Rhodes Paddington") but
    also a suburb and its design -- Creation Homes' QLD list reads "4 Pilny Street Camira
    Banksia 183 - Classic" -- and counting them put 40 correct rows on this WARN in the
    29 Sep copy. Pure, like text_locality_elsewhere; the caller decides the state_source
    it applies to. A warning names no state, so it can only add to what the replay sees.
    """
    if geo is None or not row_text:
        return None
    toks, breaks = _tokens(row_text)
    stored = normalise_state(state)
    names: List[str] = []
    if str(suburb or "").strip():
        names = [str(suburb).strip()]
    elif stored:
        spans = _place_spans(toks, breaks, geo)
        for (i, size), states in spans.items():
            name = " ".join(toks[i:i + size])
            if (stored in states and not _inside_a_longer_place(spans, i, size)
                    and name.lower() not in [n.lower() for n in names]):
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


def _written_in(toks: List[str], breaks: Any, cell: Any) -> bool:
    """True when some part of `cell` (split on its own separators) is written in the row,
    word for word -- the row's suburb column is then one of the row's own words."""
    low = [t.lower() for t in toks]
    for part in _components(cell):
        want = [w.lower().rstrip("-'") for w in _TEXT_TOKEN.findall(part)]
        want = [w for w in want if w]
        if not want:
            continue
        for i in range(len(low) - len(want) + 1):
            if low[i:i + len(want)] == want and not any(
                    k in breaks for k in range(i + 1, i + len(want))):
                return True
    return False


def text_locality_elsewhere(row_text: Any, state: Any, geo: Any, stocked: Any,
                            estate: Any = "", suburb: Any = "",
                            page: Any = "") -> Optional[Tuple[str, str]]:
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
      * `stocked` holds (locality lower-case, that state): the catalogue has live rows
        filed under that locality in that state, so the place is where this kind of stock
        actually is rather than a gazetteer coincidence.

    Longest span first, then left to right. That is origin/main's rule, word for word, and
    its first answer is the only one ever given (round 2, 29 Sep 2026): the check may keep
    quiet where origin/main spoke, but never names a place, or a state, that origin/main's
    would not. The first cut went further -- it skipped names in the scan and read on, and
    let 'followed' go whenever the catalogue stocked the suburb column in the row's state
    -- and review found it blind to misfiles origin/main catches ("Available Tarneit
    Paddington 180m2" filed QLD on the column Paddington, stocked QLD by AVIA's facade rows;
    7889 "66 Taylors Ridge Walloon Banksia 183" filed NSW on Banksia).

    Given `suburb` and `page` (enrich's overrule passes them), it keeps quiet about that
    answer in ONE case: AVIA 7810 ("... Rhodes Paddington 180m2 ...") and 12359 ("... Rhodes
    Fortitude 180m2 ..."), a design and its facade read as Rhodes, NSW -- the row stays on
    its page's state, which is never a state origin/main would not give. The nightly
    replay asks without them and warns wherever origin/main warns; where this silence
    would apply it words the WARN as a facade (search_regressions.locality_check, round
    3). All of these must hold:
      * the row's own `page` state is the state it is filed in, so two claims that do not
        come from the line -- the E-Agent page and the suburb column -- agree (7810: QLD
        page, GLENVALE, 'suburb + e-agent page');
      * `suburb`, that column, is given and no part of it is written in the row's text: it
        came from the sheet's own heading, not from a word of the line. A misfile is a
        word of the row put in the column (Banksia in "Walloon Banksia", The Grove in
        "Tarneit The Grove") -- or a column the page does not back: Colin's 14905 was filed
        QLD on 'Riverhills', a word its line never writes, with no page state at all;
      * every time the row writes the name, a facade CODE follows it -- a garage code or a
        facade that is no place ("SG", "Fortitude"), or one of AVIA's facades that are
        places with the design's size after it ("Paddington 180m2") -- see
        _only_ever_a_facaded_design;
      * and the line names no other place outside the state the row is filed in, nor
        states another state's code or postcode (states_written_in: "... Soutbank, VIC,
        3006 Illawarra SG" filed QLD is a VIC lot): the design is then the only
        other-state name the row writes, so its page is the only claim to its locality
        there is, and it backs the filed state.
    Round 3, 29 Sep 2026: the round-2 silence also let a name go when a place of the filed
    state came straight after it ('followed') or a facade style did ("Chelsea Traditional
    12.5"). Both reviews found that blind to genuine misfiles origin/main catches, whenever
    the page backed the wrong state and the column was a heading: Creation Homes' real
    layout is "<suburb> <design> <size>" (7889 "66 Taylors Ridge Walloon Banksia 183"
    filed NSW on Cameron Park; "534 Bellevue Ripley Coolamon 213"), and Silkwood's
    "REGISTER NOW Parkinson Paradise", G Developments' "Oran Park Clontarf". The words
    cannot tell that layout from a design and its facade; only a facade code and the
    design's house area can, so only that is let go -- and even that, the words cannot
    prove against a page that is wrong, so the replay no longer keeps quiet at all.
    Measured: over 2.8 million asks (verify-0's fn_tle: every row of the 29 Sep copy and
    6,000 mutated ones, filed in four states, on four columns, three pages, three stocked
    sets) it never names a place origin/main does not, and keeps quiet where origin/main
    answers on 337 asks (2,730 in round 2), each a name with a garage code or AVIA facade
    after it. Enrich hardly meets it: its row-text check looks at a state resting on the
    column alone ('suburb', no page behind it) or on a guess, which is always one of the
    line's own words.

    None when nothing qualifies. Pure: reads no database and writes nothing, so enrich
    decides what to do with the answer.
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
            # origin/main answers here.
            if (stored and normalise_state(page) == stored and str(suburb or "").strip()
                    and not _written_in(toks, breaks, suburb)
                    and _only_ever_a_facaded_design(toks, breaks, span, geo, stored)
                    and not states_written_in(row_text, geo) - {stored}):
                return None
            return cand, states[0]
    return None


# AVIA's facades that are also place names (tests/fixtures/state_corpus_vocab.json holds the
# night's facades): straight after a design, with the design's size after them, they are
# the facade -- "Rhodes Paddington 180m2" (7810), and so Yamba and Newstead. The rest of
# its facades are no place and are in _DESIGN_VARIANT_AFTER.
_FACADE_PLACE = re.compile(r"^(?:paddington|yamba|newstead)$", re.IGNORECASE)


def _house_area_at(toks: List[str], breaks: Any, k: int) -> bool:
    """True when toks[k:] is a house area in square metres, written as one word: "180m2"
    (AVIA's "Rhodes Paddington 180m2"). Not a bare number: Creation Homes' designs are
    sized bare -- "Munno Para West Yamba 28 - Newstead" is a Munno Para West lot, design
    Yamba (the review's generated row, round 3)."""
    return (k + 2 < len(toks) and toks[k][:1].isdigit() and toks[k + 1].lower() == "m"
            and toks[k + 2] == "2" and not {k, k + 1, k + 2} & set(breaks))


def _only_ever_a_facaded_design(toks: List[str], breaks: Any, span: List[str], geo: Any,
                                state: str) -> bool:
    """True when every time the row writes `span` a facade code follows it, and the row
    names no other place outside `state` (text_locality_elsewhere's one silence).

    A facade code is a garage code or a facade that is no place (_DESIGN_VARIANT_AFTER:
    "Verona SG", "Rhodes Fortitude" -- not the first word of a place, "Lennox Head"), or a
    facade that is a place (_FACADE_PLACE) with the design's house area after it
    ("Rhodes Paddington 180m2", _house_area_at). Not a place of `state` after it, nor a
    facade style, nor a bare size: a
    suburb is followed by those as often as a design is (round 3, 29 Sep 2026 -- see
    text_locality_elsewhere). One plain mention ("... Display home: Rhodes") and the row
    names the place after all; one other place of another state anywhere in the line
    ("Lot 5 Rhodes SG Tarneit" filed QLD) and the answer stands too, because the row is
    then not on its page's word alone."""
    want = [t.lower() for t in span]
    seen = False
    covered = set()
    for i in range(len(toks) - len(want) + 1):
        if [t.lower() for t in toks[i:i + len(want)]] != want:
            continue
        if any(k in breaks for k in range(i + 1, i + len(want))):
            continue
        seen = True
        j = i + len(want)
        if j >= len(toks) or j in breaks:
            return False
        after = toks[j + 1] if j + 1 < len(toks) and j + 1 not in breaks else ""
        if _DESIGN_VARIANT_AFTER.match(toks[j]):
            if after and _places(toks[j] + " " + after, geo):
                return False
        elif not (_FACADE_PLACE.match(toks[j]) and _house_area_at(toks, breaks, j + 1)):
            return False
        covered.update(range(i, j + 1))
    if not seen:
        return False
    for (i, size), states in _place_spans(toks, breaks, geo).items():
        if covered & set(range(i, i + size)):
            continue
        if state not in states:
            return False
    return True


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
