"""
Listing feature extraction — the fields Coleen asked for on 28/29 July.

Most of what he wants is already present in the harvested text and was simply
never parsed out. A real stored row:

    Lot 82 Aberdeen  282  142.8  12.0  Sep-26  Empley 15  3x2x2  $205,000  $335,220  $540,220  Available
    └lot  └estate    land  house  frontage  title  design   bed×bath×car  land$    build$    total$   STATUS

This module takes one flattened row of text and returns those fields. It is kept
separate from adaptive_extract so it can be unit-tested without Playwright, and it
is called by every channel (XLSX/PDF stocklists, HTML portals, email bodies).

Two rules it holds to:
  * **Never guess.** A field it cannot establish is returned as None. A wrong
    storey or a wrong availability is worse for the client than a blank.
  * **Must see the UNTRUNCATED row.** Availability is the last token on VIC rows,
    so a truncated string loses it — that is why ~30% of already-stored rows can
    never have it recovered.
"""

import re
from typing import Any, Dict, Optional


def _clean(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


# ---------- availability ----------
# Order matters: multi-word and negated forms are tested before the bare word.
_AVAILABILITY = (
    (re.compile(r"\bunder\s*offer\b", re.I), "Under Offer"),
    (re.compile(r"\b(?:on\s*hold|onhold)\b", re.I), "On Hold"),
    (re.compile(r"\b(?:not\s*available|unavailable)\b", re.I), "Not Available"),
    (re.compile(r"\bsold\b", re.I), "Sold"),
    (re.compile(r"\b(?:reserved|deposit\s*taken)\b", re.I), "Reserved"),
    (re.compile(r"\bleased\b", re.I), "Leased"),
    (re.compile(r"\bavailable\b", re.I), "Available"),
    (re.compile(r"\bhold\b", re.I), "On Hold"),
)

# ---------- storey ----------
_STOREY_EXPLICIT = re.compile(r"\b(single|double|two|triple)[\s-]*(?:storey|story|level)s?\b", re.I)
_STOREY_NUMERIC = re.compile(r"\b([12])\s*(?:storey|story|level)s?\b", re.I)
# Bare SINGLE / DOUBLE as a standalone token — how the QLD dual-occ stocklists print it.
_STOREY_BARE = re.compile(r"\b(SINGLE|DOUBLE)\b")
# Words that make a bare SINGLE/DOUBLE mean something else entirely.
# "Single Contract" is the big one — Paramount labels every package that way.
_STOREY_FALSE_FRIEND = re.compile(
    r"\b(?:contract|garage|lock[\s-]?up|glaz|glazed|title|titled|vanity|bowl|sink|shower|"
    r"basin|storey\s*height|key|occupancy|bed|door|gate|carport|driveway|width|fronted)\b", re.I)
_STOREY_WORD = {"single": "SINGLE", "double": "DOUBLE", "two": "DOUBLE", "triple": "TRIPLE",
                "1": "SINGLE", "2": "DOUBLE"}

# ---------- lot / stock identifier ----------
_LOT_NUM = re.compile(r"\blot\s*([0-9]{1,6}[A-Za-z]?)\b", re.I)
_STOCK_CODE = re.compile(r"\b([A-Z]{2,4}-\d{3,5})\b")

# ---------- estate ----------
_ESTATE = re.compile(r"\b([A-Z][A-Za-z'’]+(?:\s+[A-Z][A-Za-z'’]+){0,3})\s+Estate\b")

# ---------- frontage ----------
# Labelled, or an explicit metre unit. A bare decimal (the "12.0" above) is NOT
# taken — position in a flattened row is not reliable enough to be worth a wrong number.
_FRONTAGE_LABELLED = re.compile(r"frontage\s*(?:\(m\))?\s*:?\s*([0-9]{1,2}(?:\.[0-9]{1,2})?)", re.I)
_FRONTAGE_UNIT = re.compile(r"\b([0-9]{1,2}(?:\.[0-9]{1,2})?)\s*m\s*(?:frontage|wide|width)\b", re.I)

# ---------- postcode ----------
_FOUR_DIGIT = re.compile(r"\b(\d{4})\b")
# Postcode ranges a HOUSE can be in. Australia Post reserves several blocks for
# "large volume receivers" — bulk PO-box holders — and no dwelling is ever in one, so a
# four-digit number landing there is something else: 1501, 1026 and 1528 were all lot or
# design numbers on Victorian rows, and each one placed a Geelong lot in New South Wales.
_DWELLING_POSTCODE_RANGES = (
    (800, 899),        # NT      (0900-0999 is NT's LVR block)
    (2000, 2599), (2600, 2618), (2619, 2899), (2900, 2920), (2921, 2999),   # NSW + ACT
    (3000, 3999),      # VIC     (8000-8999 is VIC's LVR block)
    (4000, 4999),      # QLD     (9000-9999 is QLD's LVR block)
    (5000, 5799),      # SA      (5800-5999 LVR)
    (6000, 6797),      # WA      (6800-6999 LVR)
    (7000, 7799),      # TAS     (7800-7999 LVR)
)


def _is_dwelling_postcode(val: int) -> bool:
    return any(low <= val <= high for low, high in _DWELLING_POSTCODE_RANGES)

# ---------- incentives ----------
_INCENTIVE_CONTEXT = (r"rebate|incentive|bonus|cash\s*back|cashback|credit|discount|gift|"
                      r"free\s+upgrade|promo(?:tion)?|grant|contribution|allowance|saving|"
                      r"off\s+the\s+price|vendor\s*(?:paid|rebate)")
_AMOUNT = r"\$\s?([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,6}|[0-9]{1,3}\s*k)"
_INCENTIVE_BEFORE = re.compile(rf"(?:{_INCENTIVE_CONTEXT})[^$\n]{{0,40}}{_AMOUNT}", re.I)
_INCENTIVE_AFTER = re.compile(rf"{_AMOUNT}[^$\n]{{0,40}}(?:{_INCENTIVE_CONTEXT})", re.I)
# Anything that means the figure is rent or a yield, not a rebate.
# Bounded: an unbounded p\.?a\.? matches the "pa" in "part"/"Package" — see the note
# on adaptive_extract._RENTY, where that cost five builders their whole stocklist.
_MONEY_NOT_INCENTIVE = re.compile(
    r"(?<![A-Za-z])p\.?\s?w\.?(?![A-Za-z])"
    r"|(?<![A-Za-z])p\.?\s?a\.?(?![A-Za-z])"
    r"|per\s*week|per\s*annum|weekly|\brent\w*|\byield\w*|%", re.I)


# ---------- bed / bath / car ----------
# THE PROBLEM THIS SOLVES. Only 18% of stored rows carry a bedroom count, yet 3,383 of
# the rows that lack one have a populated source_text that states it. A client brief
# filters on bed/bath/car, so a blank here is the difference between a lot being
# matchable and being invisible.
#
# THE NOTATIONS, counted over the 4,129 rows that have a source_text:
#   "5 beds / 3 baths / 2 cars"   737   FRD / Hudson / Alete / Land Build Direct
#   "4 / 2 / 2"                    83   Verv Projects, Knew Street, Silkwood
#   "3x2x2", "3x2.5x1"             76   VIC regional stocklists (blank builder)
#   "4 | 2 | 2", "5 | 2.5 | 2"     69   APLACE by Glenville
#   "1 BED + 1 BATH + STUDY"       85   Atlas
#   "5 + 5 + 3"                    31   QLD dual-occupancy (CC- stock codes)
#   "4 - 2- 2 -2"                  13   Invision Homes
#   "3BR", "2 BRM + 2 BATH"        40   Thomas Paul, Rockdale
#   "3B2B", "3B3B"                 24   Murcia
#   "3B2B2C"                        1   The Albertine
#
# WHAT IS DELIBERATELY NOT PARSED. 2,363 of the 3,383 target rows write the triple as
# bare space-separated numbers with no delimiter and no label — Aldrich's
# "...Titled South 274.00 10.50 24.76 137.34 Single 3 2 Double", Kemps' "15.8 502
# 245.16 6 3.5 2 Q2 2027". Reading those means inferring a field from a number's
# POSITION, which is forbidden: the same layout slot is land size for one builder and
# frontage for the next. They stay blank until the harvester preserves column headers.
#
# THE GUARDS ARE THE HARD PART, not the patterns. Every one below was written against
# a real false positive found in the live data — see _mask_non_spec_numbers.
_BBC_RANGES = {"bedrooms": (1, 10), "bathrooms": (1, 10), "car_spaces": (0, 6)}

# Contexts whose digits can never be a bed/bath/car count. Each is blanked out (with a
# same-length run of '#', so offsets stay valid) BEFORE any spec pattern is applied.
_MASKS = (
    # A Google Drive link. AVIA Homes' rows end in one, and the file id contains "1X7"
    # and "0X2" — which read as 1 bed 7 bath and 0 bed 2 bath under the NxN rule.
    re.compile(r"https?://\S+", re.I),
    # Money. "$ 2,330" is a weekly rent; "$ 1 ,757,400" is a package price whose
    # thousands separator got split by the flattener.
    re.compile(r"\$\s*\d[\d,\.]*(?:\s*[,\.]\d{3})*"),
    # A yield or a rate. "8.03%" and "4.64% - 5.25%" both sit next to the triple on
    # the QLD dual-occ rows.
    re.compile(r"\d+(?:\.\d+)?\s*%"),
    # An area or a length. Masking these is what stops "451m² 167.9m²" and the
    # frontage "10.5 x 30" from being read as counts.
    re.compile(r"\d+(?:\.\d+)?\s*(?:m2|m²|sqm|sq\s?m|sqs?|ha|km)\b", re.I),
    re.compile(r"\d+(?:\.\d+)?\s*m(?![A-Za-z0-9])"),
    # Dates and title quarters. "Q1-2026", "Sep-26", "Jul-26", "2026-09-26 00:00:00",
    # "Mid 2026" — Invision's "4 - 2- 2 -2" sits four tokens from "August 26".
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}(?:\s\d{2}:\d{2}:\d{2})?"),
    re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s-]*\d{2,4}\b", re.I),
    re.compile(r"\bq[1-4][\s-]*\d{2,4}\b", re.I),
    re.compile(r"\b20[123]\d\b"),
)

# A number that is one half of a dual-occupancy split — Hudson's "2+1 baths",
# Bramwell's "Azalea 137 3/3 1/1 carport" (1+1 carports). Filling bathrooms=1 from the
# trailing half understates the lot, and summing it to 3 assumes the two dwellings are
# let together, which the row does not say — so the field is refused outright.
#
# The delimiter must bind TIGHTLY (at most one space) for this to be a split. Loosely
# spaced delimiters are field separators, not splits: "5 beds / 3 baths / 2 cars" is
# three fields, and an earlier version of this guard read the " / " after "5 beds" as a
# split and threw away the bedroom count on all 737 rows that use that notation.
_DUAL_SPLIT_BEFORE = re.compile(r"\d\s?[+/]\s?$")
_DUAL_SPLIT_AFTER = re.compile(r"^[+/]\d")

# Parking that is stated but never counted — "SLUG" (single lock-up garage), "DLUG".
# Canvas Cooroy writes "3 Bed + 2.5 Bath + SLUG + 1 Carport": the 1 belongs to the
# carport only, so car_spaces=1 would lose the garage. Ambiguous total, so no value.
_UNCOUNTED_PARKING = re.compile(r"\b[SD]?LUG\b|\block[\s-]?up\s+garage\b", re.I)
_CARPORT = re.compile(r"\bcarports?\b", re.I)

_NUM = r"\d{1,2}(?:\.5)?"
# Labelled forms, scanned independently per field. Independent scanning is on purpose:
# it needs no assumption about which field comes first, so it reads Rockdale's
# "2 BRM + 2 BATH 1 Standard" and Atlas' "3 BED + 2 BATH + STUDY, 1 Car" unchanged.
# "car"/"cars" is anchored with \b — without it "0 Cartier" and "201 Cardrona Way"
# (a design name and a street) both returned a car space count.
_LABELLED = {
    "bedrooms": re.compile(rf"(?<![\d.,]){_NUM}\s*(?:bed|beds|bedroom|bedrooms|br|brs|brm|brms|b/r)\b", re.I),
    "bathrooms": re.compile(rf"(?<![\d.,]){_NUM}\s*(?:bath|baths|bathroom|bathrooms)\b", re.I),
    "car_spaces": re.compile(rf"(?<![\d.,]){_NUM}\s*(?:car|cars|carspace|carspaces|car\s?space|car\s?spaces|carport|carports|garage|garages)\b", re.I),
}
_LEADING_NUM = re.compile(rf"^{_NUM}")

# "3B2B2C" / "3B2B" / "3B3B" — Murcia and The Albertine write the whole spec as a code.
_CODE_BBC = re.compile(r"(?<![A-Za-z0-9])(\d)\s?B\s?(\d(?:\.5)?)\s?B\s?(\d)\s?C(?![A-Za-z0-9])")
_CODE_BB = re.compile(r"(?<![A-Za-z0-9])(\d)\s?B\s?(\d(?:\.5)?)\s?B(?![A-Za-z0-9])")

# A delimited triple. The delimiter must REPEAT (\2) — that single requirement is what
# separates a spec from a lot dimension: "4 | 2 | 2" and "3x2.5x1" match, while
# APLACE's frontage-by-depth "10.5 x 28" and "8.5 x 21" have only one delimiter and
# are left alone. Invision's "4 - 2- 2 -2" carries a fourth group; only the first
# three are read, because nothing in the row says what the fourth is.
_DELIM_TRIPLE = re.compile(
    rf"(?<![0-9A-Za-z.,])({_NUM})\s*([xX|/+\-])\s*({_NUM})\s*\2\s*({_NUM})(?![0-9A-Za-z]|\.\d)")
# A FOURTH group after the triple. Silkwood writes "4/2/2/2", Invision "4 - 2- 2 -2",
# Knew Street "3/2.5/1+1", and nothing in any of those rows says what the extra number
# is. Slots 1 and 2 are safe — every LABELLED form in the corpus ("5 beds / 3 baths /
# 2 cars", "3 BED + 2 BATH + 2 CAR", "3B2B2C") orders them bed then bath — but whether
# the extra column sits before or after the garage is unknown, so car_spaces is refused.
# It costs 85 car values (72 Silkwood, 13 Invision) and that is the correct trade: on
# Knew Street's "1+1" the third slot is provably only HALF the parking.
_DELIM_FOURTH = re.compile(rf"^\s*[xX|/+\-]\s*{_NUM}(?![0-9A-Za-z]|\.\d)")

# A row that is not one listing. Gallery Group's rows are 640-935 character captures of
# a whole stocklist PAGE — header line, disclaimer, "Current as of:", and a dozen
# packages mashed together. Row 868 carries a single "3B2B" from one of those packages;
# attaching it to the row would put one package's spec on all of them.
_MONEY_RUN = re.compile(r"\$\s*\d[\d,\.\s]{4,}")


def _mask_non_spec_numbers(text: str) -> str:
    """Blank every digit that belongs to money, a percentage, an area, a date or a URL.

    Same-length replacement so that offsets into the returned string still line up
    with the original — the caller quotes the original text back as evidence.
    """
    t = text
    for rx in _MASKS:
        t = rx.sub(lambda m: "#" * len(m.group(0)), t)
    return t


def _in_range(field: str, val: float) -> bool:
    low, high = _BBC_RANGES[field]
    if not (low <= val <= high):
        return False
    # A half is meaningful for a bathroom ("2.5 bath" is an ensuite plus a powder
    # room) and meaningless for a bedroom or a garage.
    return field == "bathrooms" or float(val).is_integer()


def _coerce(field: str, raw: str) -> Optional[float]:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if not _in_range(field, val):
        return None
    return val if field == "bathrooms" else int(val)


def _add(votes: Dict[str, set], field: str, raw: Any, notation: str,
         notations: set) -> None:
    val = _coerce(field, str(raw))
    if val is None:
        return
    votes[field].add(val)
    notations.add(notation)


def parse_bed_bath_car(text: str) -> Dict[str, Any]:
    """Bedrooms / bathrooms / car spaces from one flattened source row.

    Returns ``{"bedrooms": .., "bathrooms": .., "car_spaces": .., "bbc_notation": ..}``
    with None for anything the row does not state in a form that can be defended from
    its own text. Where two notations in the same row disagree on a field, that field
    comes back None: a disagreement means one of the two readings is wrong and there is
    no way to tell which, so guessing it would be the wrong kind of confident.

    Bathrooms may be a half ("2.5"); bedrooms and car spaces are always whole.
    """
    raw = _clean(text)
    out: Dict[str, Any] = {"bedrooms": None, "bathrooms": None,
                           "car_spaces": None, "bbc_notation": None}
    if not raw:
        return out
    # A whole-page capture rather than one listing: many packages, one row.
    if len(raw) > 400 and len(_MONEY_RUN.findall(raw)) >= 6:
        return out
    t = _mask_non_spec_numbers(raw)
    votes: Dict[str, set] = {"bedrooms": set(), "bathrooms": set(), "car_spaces": set()}
    notations: set = set()
    car_refused = False

    # 1. labelled, per field
    for field, rx in _LABELLED.items():
        for m in rx.finditer(t):
            if "#" in m.group(0):
                continue                                   # overlapped a masked run
            before, after = t[max(0, m.start() - 4):m.start()], t[m.end():m.end() + 4]
            if before.rstrip().endswith("$") or after.lstrip().startswith("%"):
                continue
            if _DUAL_SPLIT_BEFORE.search(before) or _DUAL_SPLIT_AFTER.match(after):
                continue                                   # half of a dual-occ split
            num = _LEADING_NUM.match(m.group(0))
            if num:
                _add(votes, field, num.group(0), "labelled", notations)

    # 2. code form — 3B2B2C, 3B2B
    for m in _CODE_BBC.finditer(t):
        if "#" in m.group(0):
            continue
        _add(votes, "bedrooms", m.group(1), "code", notations)
        _add(votes, "bathrooms", m.group(2), "code", notations)
        _add(votes, "car_spaces", m.group(3), "code", notations)
    # _CODE_BB cannot double-fire on a 3B2B2C row: the trailing "2C" fails its
    # right-hand boundary. So it runs unconditionally, which is how Murcia's
    # "5B2.5B SH01 5 Bedroom Option" yields the 2.5 bathrooms as well as the 5 beds.
    for m in _CODE_BB.finditer(t):
        if "#" in m.group(0):
            continue
        _add(votes, "bedrooms", m.group(1), "code", notations)
        _add(votes, "bathrooms", m.group(2), "code", notations)

    # 3. delimited triple — 3x2x2, 4 | 2 | 2, 5 + 5 + 3, 4 / 2 / 2, 4 - 2- 2
    for m in _DELIM_TRIPLE.finditer(t):
        if "#" in m.group(0):
            continue
        trio = (_coerce("bedrooms", m.group(1)),
                _coerce("bathrooms", m.group(3)),
                _coerce("car_spaces", m.group(4)))
        if None in trio:
            continue          # all three must be plausible or the match is not a spec
        _add(votes, "bedrooms", m.group(1), f"delim{m.group(2)}", notations)
        _add(votes, "bathrooms", m.group(3), f"delim{m.group(2)}", notations)
        if _DELIM_FOURTH.match(t[m.end():m.end() + 6]):
            car_refused = True       # a 4th, unexplained group follows the triple
        else:
            _add(votes, "car_spaces", m.group(4), f"delim{m.group(2)}", notations)

    # A counted carport alongside an uncounted lock-up garage: the stated number is
    # only part of the parking, so the total cannot be established.
    if car_refused or (_CARPORT.search(t) and _UNCOUNTED_PARKING.search(t)):
        votes["car_spaces"] = set()

    for field, vals in votes.items():
        if len(vals) == 1:
            out[field] = vals.pop()
    if any(out[f] is not None for f in _BBC_RANGES):
        out["bbc_notation"] = "+".join(sorted(notations))
    return out


# ---------- land / house size ----------
# Only ONE unit-bearing area is attributable, and it is a narrow case. Across all 4,129
# rows with a source_text there is not a single labelled area: no "Land: 450m2", no
# "House Size". 795 rows carry TWO areas in a row and nothing but their ORDER says which
# is which — and order is not safe here, because Thomas Paul's townhouses print
# "193m² 208.4m²" with the DWELLING larger than the block, so even "the bigger one is
# the land" is wrong. The one honest case is an apartment: an apartment has no land
# area, so when such a row states exactly one area, that area is the internal size.
_APARTMENT = re.compile(r"\bapartment\b", re.I)
_AREA_UNIT = re.compile(r"(\d{1,4}(?:\.\d{1,3})?)\s*(?:m2|m²|sqm|sq\s?m)\b", re.I)
# The trap: "House" and "Town House" are the PRODUCT TYPE column, and on all 110 Thomas
# Paul rows the number after that word is the LAND size. A "house 462m² -> house_sqm"
# rule would have written the block area into the dwelling area on every one of them.


def parse_areas(text: str) -> Dict[str, Any]:
    """house_sqm for an apartment row that states exactly one area. Otherwise blanks."""
    out: Dict[str, Any] = {"land_sqm": None, "house_sqm": None}
    t = _clean(text)
    if not t or not _APARTMENT.search(t):
        return out
    found = [float(m.group(1)) for m in _AREA_UNIT.finditer(t)]
    if len(found) == 1 and 15.0 <= found[0] <= 600.0:
        out["house_sqm"] = found[0]
    return out


def parse_availability(text: str) -> Optional[str]:
    t = _clean(text)
    for rx, label in _AVAILABILITY:
        if rx.search(t):
            return label
    return None


def parse_storey(text: str) -> Optional[str]:
    t = _clean(text)
    m = _STOREY_EXPLICIT.search(t)
    if m:
        return _STOREY_WORD.get(m.group(1).lower())
    m = _STOREY_NUMERIC.search(t)
    if m:
        return _STOREY_WORD.get(m.group(1))
    # Bare token: only trust it when nothing nearby redefines it.
    for m in _STOREY_BARE.finditer(t):
        window = t[max(0, m.start() - 30):m.end() + 30]
        if not _STOREY_FALSE_FRIEND.search(window):
            return m.group(1).upper()
    return None


def parse_lot_number(text: str) -> Optional[str]:
    t = _clean(text)
    m = _STOCK_CODE.search(t)          # CC-0122 style stock codes
    if m:
        return m.group(1)
    m = _LOT_NUM.search(t)
    return m.group(1) if m else None


def parse_estate(text: str, context: str = "") -> Optional[str]:
    for source in (text, context):
        m = _ESTATE.search(_clean(source))
        if m:
            return _clean(m.group(1))
    # A group/banner row is often just the estate line ("Aberdeen - Winter Valley - VIC - House & Land")
    if context:
        first = _clean(re.sub(r"^[^A-Za-z0-9]+", "", context)).split(" - ")[0]
        if 2 < len(first) <= 40 and "$" not in first:
            return first
    return None


def parse_frontage(text: str) -> Optional[float]:
    t = _clean(text)
    for rx in (_FRONTAGE_LABELLED, _FRONTAGE_UNIT):
        m = rx.search(t)
        if m:
            try:
                v = float(m.group(1))
                if 3.0 <= v <= 60.0:
                    return v
            except ValueError:
                pass
    return None


def parse_postcode(text: str) -> Optional[str]:
    """A 4-digit AU postcode. Lot numbers, dates, years and areas are excluded."""
    t = _clean(text)
    # Strip anything that produces a 4-digit look-alike, in order:
    #   dates   — "25/03/2026" otherwise yields postcode 2026
    #   months  — "Sep-26", "Q1-2026"
    #   lots    — "LOT 5532" otherwise yields postcode 5532
    t = re.sub(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", " ", t)
    t = re.sub(r"\b\d{4}-\d{1,2}-\d{1,2}\b", " ", t)
    t = re.sub(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s-]*\d{2,4}\b", " ", t, flags=re.I)
    t = re.sub(r"\bq[1-4][\s-]*\d{2,4}\b", " ", t, flags=re.I)
    t = _LOT_NUM.sub(" ", t)
    t = _STOCK_CODE.sub(" ", t)
    candidates = []
    for m in _FOUR_DIGIT.finditer(t):
        val = int(m.group(1))
        before = t[:m.start()]
        after = t[m.end():m.end() + 6].lower()
        if re.match(r"\s*(?:m2|m²|sqm|sq)", after):        # an area, not a postcode
            continue
        if 2015 <= val <= 2035:                            # a bare year
            continue
        # EVO's layout opens with the frontage aspect and then the LOT number:
        # "West 2236 Grandview Truganina". That one pattern put a wrong postcode on 885
        # rows — a NSW code on Truganina lots, WA and QLD codes on Werribee lots.
        # Anchored to the START of the row on purpose: compass words are also ordinary
        # parts of suburb names, and matching them anywhere threw away the real postcodes
        # of "Melton South 3338" and "BRISBANE NORTH 4514".
        if re.fullmatch(r"(?:north|south|east|west|northeast|northwest|southeast|"
                        r"southwest|ne|nw|se|sw)\s+", before, re.I):
            continue
        # A four-digit number that OPENS the row is the lot or stock number, not a
        # postcode: an Australian postcode follows the suburb, it never leads. Several
        # price lists print the lot bare, with no "Lot" prefix for _LOT_NUM to catch —
        #   "2226 Whiterock White Rock White Rock 156 - Facade A Available 3 2 2 252 ..."
        # which read as NSW 2226 (Oyster Bay) and put a NSW state on a QLD property in
        # White Rock. The same listing arrived from another channel correctly marked QLD,
        # so the dashboard showed one lot in two states.
        if not before.strip():
            continue
        if _is_dwelling_postcode(val):
            candidates.append(m.group(1))
    # The LAST one, not the first. An Australian postcode follows the suburb at the end of
    # an address, so the first four-digit number in a row is usually the street or lot
    # number: "1368 Margery St, Toolern Waters, Melton South 3338" was returning 1368.
    return candidates[-1] if candidates else None


def parse_incentive(text: str, package_price: Optional[float] = None) -> Dict[str, Any]:
    """Cash incentives / rebates — Coleen: "$15,000 being offered, $7.5 being offered".

    Runs as its OWN scan. The package-price detector is untouched: widening that to
    reach sub-$50k figures would pull weekly rent ($2,330) into the price list and
    make the existing land-price bug worse.
    """
    t = _clean(text)
    found, phrases = [], []
    for rx in (_INCENTIVE_BEFORE, _INCENTIVE_AFTER):
        for m in rx.finditer(t):
            window = t[max(0, m.start() - 40):m.end() + 40]
            if _MONEY_NOT_INCENTIVE.search(window):
                continue                                   # rent / yield, not a rebate
            raw = m.group(1).replace(",", "").strip().lower()
            try:
                val = float(raw[:-1]) * 1000 if raw.endswith("k") else float(raw)
            except ValueError:
                continue
            if not (1_000 <= val <= 150_000):
                continue
            if package_price and val >= 0.25 * package_price:
                continue                                   # too big to be an incentive
            found.append(val)
            phrases.append(_clean(m.group(0))[:80])
    if not found:
        return {"incentive_amount": None, "incentive_text": None}
    # Largest wins; never sum — stacking rules are unknowable.
    return {"incentive_amount": max(found),
            "incentive_text": " | ".join(dict.fromkeys(phrases))[:200]}


def parse_listing_features(text: str, context: str = "",
                           package_price: Optional[float] = None) -> Dict[str, Any]:
    """All of the above in one pass. Call with the UNTRUNCATED row text."""
    out: Dict[str, Any] = {
        "availability_status": parse_availability(text),
        "storey": parse_storey(text),
        "lot_number": parse_lot_number(text),
        "estate_name": parse_estate(text, context),
        "frontage_m": parse_frontage(text),
        "postcode": parse_postcode(text),
    }
    out.update(parse_incentive(text, package_price))
    # bed/bath/car and the one attributable area. Keys are the DB column names so a
    # caller can hand the dict straight to record_building, which only ever fills a
    # column that is currently empty.
    bbc = parse_bed_bath_car(text)
    out["bedrooms"] = bbc["bedrooms"]
    out["bathrooms"] = bbc["bathrooms"]
    out["car_spaces"] = bbc["car_spaces"]
    out["bbc_notation"] = bbc["bbc_notation"]
    out.update(parse_areas(text))
    return out
