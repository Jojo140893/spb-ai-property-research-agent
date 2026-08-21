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
# NOT AUSTRALIAN. 633 live listings are Dubai and Manila off-plan apartments (DAMAC
# Islands, SAFA GATE, SMDC Sail/Sands/Gold/Jade/Glade Residences) sitting in the same
# pool as Melbourne house-and-land. They will never have an Australian suburb, so
# counting them as a suburb gap overstates the gap and hides that they are in there at
# all. Coleen sells Australian property; this is the honest label for the rest.
BLANK_OVERSEAS = "the listing is not in Australia"

# Reasons whose suburb may be used AS A LOCATION.
_LOCATED = (STATED, FROM_COMPOSITE, FROM_POSTCODE, FROM_ADDRESS,
            FROM_ADDRESS_TAIL, FROM_DOCUMENT)

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
_INFERRED = (INFERRED_FROM_TEXT,)


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
    if not raw:
        carried = str(row.get("suburb_source") or "").strip()
        if carried in (BLANK_NOT_A_PLACE, BLANK_NO_STATE):
            return "", carried

    if not index.loaded:                       # no locality data in this environment
        return raw, STATED if raw else BLANK_ABSENT

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
        if named:
            return _canonical_spelling(named, "", index), FROM_DOCUMENT
        return "", BLANK_NO_STATE if raw else BLANK_ABSENT

    if raw:
        inner = index.resolve_locality(raw, state)
        if inner:
            return _canonical_spelling(inner, state, index), FROM_COMPOSITE

    anchor = _postcode_anchor(row, state, index)
    if anchor:
        return _canonical_spelling(anchor, state, index), FROM_POSTCODE

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

# Money, a settlement date, a status word or a size means these are table CELLS, not an
# address. This is the gate that removed the house-design class of wrong answers.
_TABLE_ROW = re.compile(
    r"(\$\s*[\d,]{3,})"
    r"|(\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s\-']*\d{2,4}\b)"
    r"|(\b(?:available|sold|on hold|under offer|registered|titled|resale|deposit taken)\b)"
    r"|(\b\d+\s*(?:m2|sqm)\b)|(\b\d{1,2}\.\d\s*x\s*\d)", re.I)

# Somewhere that is not Australia. Matched against the row's own text.
_OVERSEAS = re.compile(
    r"\b(dubai|dxb|abu\s*dhabi|u\.?a\.?e|damac|emaar|safa\s*gate|"
    r"iloilo|manila|makati|philippines|smdc|brgy\.?|barangay|"
    r"bali|phuket|bangkok|singapore|kuala\s*lumpur)\b", re.I)


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
    if not state:
        return ""
    words = _ADDR_WORD.findall(_row_text(row))
    for size in (3, 2, 1):
        for start in range(len(words) - size + 1):
            candidate = " ".join(words[start:start + size])
            if size == 1 and _NOT_ALONE.match(candidate):
                continue
            after = start + size
            if after < len(words) and words[after].lower().strip(".") in _STREET_TOKENS:
                continue
            if index.locate(candidate, state):
                return candidate
    return ""


def _document_suburb(row: Dict[str, Any]) -> str:
    """What this row's own source document names, or ''. Never raises, never fetches.

    The map is built by the harvest (document_suburb.refresh_from_database) because
    reading a document needs the network and a serverless request has no business making
    one. Import is lazy so suburb_quality stays usable in environments that do not ship
    the map at all.
    """
    try:
        import document_suburb
        return document_suburb.suburb_for_row(row)
    except Exception:                              # missing module, unreadable map
        return ""


def is_overseas(row: Dict[str, Any]) -> bool:
    """Is this listing outside Australia? See BLANK_OVERSEAS."""
    return bool(_OVERSEAS.search(_row_text(row)))


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
