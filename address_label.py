"""
Display labels: a readable address for a listing, and a readable title for a document.

Both solve the same shape of problem — what the scraper captured is not what a client
should be shown — and both obey the same rule: derive from what the source actually
said, never invent.

A readable address for a listing whose address column swallowed the whole spreadsheet row.

47% of live stock (2,670 of 5,703) arrives looking like

    DUPLEX PR8735 106 Redbank Plains Sienna Eden Estate 2026-09-01 00:00:00 505 $595,000 $732,285 732285
    2-Part Detached SS West 2103 Seventh Bend Weir Views Sep-26 332 $348,000 LX 18N 4 / 2 / 2 $375,700

because the source is a price list where the address is one column of many and the
extractor concatenated the row. That is a client-facing problem: it is the headline of
every shortlist card and the title of every report.

Only tokens that CANNOT belong to an Australian street address are removed:

  * an ISO timestamp                     2026-09-01 00:00:00
  * a money amount                       $595,000   $ 661,000
  * an integer of five digits or more    732285, 1327285   (no street number is 50,000+)
  * a decimal number                     227.2   (a floor area, never an address)
  * a title-availability date            Sep-26, Q4 2026
  * a bed/bath/car triplet               4 / 2 / 2

Matching against the row's other columns was tried first and rejected: bedrooms=2 made
"2 Bed 2 Bath" render as "Bed Bath", and land_size=2 turned "in stage 2" into "in stage".
Small integers legitimately appear in address text, so they are never touched. This is a
display label only — `lot_address` keeps whatever the source gave us.
"""

import re
from typing import Any, Dict, Optional

_STRIPPERS = (
    # 2026-09-01 00:00:00 / 2026-09-01T00:00 / 2026-09-01
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b"),
    # $595,000 and "$ 661,000" — the space after the sign appears in several lists
    re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?"),
    # Sep-26 / Sept 2026 / Dec/26 — a title month. Requires the digits, so a street
    # called "May" or "March" is untouched.
    re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
               r"[\s\-/]*\d{2,4}\b", re.I),
    # Q4 2026 / Q4-26
    re.compile(r"\bQ[1-4][\s\-/]*\d{2,4}\b", re.I),
    # 4 / 2 / 2 — bed/bath/car, appended by several price lists. All THREE parts are
    # required: a two-part "3/45" is a unit address ("Unit 3/45 May Street"), and
    # stripping it deleted a real street number.
    re.compile(r"(?<![\w$])\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{1,2}(?![\w/])"),
    # A bare price that lost its dollar sign. Five digits or more: the highest street
    # number in the country is four, so this can only be money.
    re.compile(r"(?<![\w$.,/-])\d{5,}(?![\w./-])"),
    # 227.2 — a floor area or a frontage, never part of an address
    re.compile(r"(?<![\w$/-])\d{1,4}\.\d{1,2}(?![\w/-])"),
)

_MULTISPACE = re.compile(r"\s{2,}")
_EDGE_JUNK = re.compile(r"^[\s,;:|/\-]+|[\s,;:|/\-]+$")
# ", ," or " - - " left behind between removed tokens
_ORPHAN_SEPARATORS = re.compile(r"\s*([,;:|])\s*(?=[,;:|])")


def clean_display_address(raw: Any, row: Optional[Dict[str, Any]] = None) -> str:
    """The address with the non-address tokens removed.

    `row` is accepted so callers do not have to change if this ever needs the rest of the
    record; it is deliberately unused, because matching against sibling columns removed
    digits that were part of the text.

    Never returns an empty string for a non-empty input: a cluttered address beats a
    blank one on a client-facing card.
    """
    original = str(raw or "").strip()
    if not original:
        return ""

    text = original
    for pattern in _STRIPPERS:
        text = pattern.sub(" ", text)

    text = _ORPHAN_SEPARATORS.sub("", text)
    text = _MULTISPACE.sub(" ", text)
    text = _EDGE_JUNK.sub("", text).strip()

    # Reduced to nothing, or to punctuation and digits with no words left — keep the mess.
    if not text or not re.search(r"[A-Za-z]", text):
        return original
    return text


# --------------------------------------------------------------------------- documents

# Anchor text on a builder's download button, which is the same on every document they
# publish. Sixteen brochures shared the title "Icon to represent a home design brochure
# Download Brochure", so the vendor tab listed sixteen indistinguishable links and a
# consultant could not tell which one to send.
_GENERIC_TITLES = {
    "download brochure", "download floorplan", "download", "brochure", "floorplan",
    "floorplans", "specification", "specifications", "download specification",
    "view brochure", "view floorplan", "download pdf", "pdf", "more info",
    "view full terms and conditions", "terms and conditions", "download flyer", "flyer",
}
# The alt text of the little image inside the button, captured along with the anchor
# text. It appears both before the label ("Icon to represent a home design brochure
# Download Brochure") and after it ("Floorplans icon Floorplan"), so rather than trying
# to locate it, treat any title mentioning an icon as describing a picture, not a
# document. A real document title does not contain the word.
_MENTIONS_ICON = re.compile(r"\bicons?\b", re.I)
# The call to action tacked onto the end of an otherwise useful title.
_TRAILING_CTA = re.compile(
    r"[\s\-–—:|]*\b(?:download|view)?\s*(?:brochure|floorplans?|specifications?|flyer|pdf)\s*$",
    re.I)
_FILE_JUNK = re.compile(r"^\d{6,8}[-_]?|[-_](?:v?\d+|final|web|lr|hr|copy|updated)$", re.I)


def clean_asset_title(title: Any, source_url: Any = "") -> str:
    """A title that tells one document apart from another.

    Prefers the scraped title when it says something, and falls back to the file's own
    name in the URL — "Denmark-185-Scullery-Brochure.pdf" becomes "Denmark 185 Scullery
    Brochure". That is the vendor's own name for the file, not a guess about it.
    """
    text = _MULTISPACE.sub(" ", str(title or "").replace("\n", " ")).strip()
    if _MENTIONS_ICON.search(text):
        text = ""
    if text and text.lower() not in _GENERIC_TITLES:
        trimmed = _TRAILING_CTA.sub("", text).strip(" -–—:|")
        # Only accept the trim if it left something meaningful behind.
        if trimmed and len(trimmed) > 3 and trimmed.lower() not in _GENERIC_TITLES:
            return trimmed
        return text

    name = str(source_url or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    name = re.sub(r"\.(pdf|docx?|xlsx?|jpe?g|png)$", "", name, flags=re.I)
    name = _FILE_JUNK.sub("", name)
    name = _MULTISPACE.sub(" ", name.replace("-", " ").replace("_", " ")).strip()
    if name and re.search(r"[A-Za-z]{3}", name):
        # Leave existing capitalisation alone where the vendor used it; only title-case
        # a name that is entirely lower case, so "SQ-Brochure" does not become "Sq".
        return name.title() if name.islower() else name
    return text or "Untitled document"


# --------------------------------------------------------------------- suburb recovery

# The eight states, abbreviated and spelled out. A value equal to one of these is not a
# suburb, whatever column it is sitting in.
_STATE_TOKENS = {
    "NSW": "NSW", "VIC": "VIC", "QLD": "QLD", "SA": "SA", "WA": "WA", "TAS": "TAS",
    "NT": "NT", "ACT": "ACT",
    "NEW SOUTH WALES": "NSW", "VICTORIA": "VIC", "QUEENSLAND": "QLD",
    "SOUTH AUSTRALIA": "SA", "WESTERN AUSTRALIA": "WA", "TASMANIA": "TAS",
    "NORTHERN TERRITORY": "NT", "AUSTRALIAN CAPITAL TERRITORY": "ACT",
}


def display_suburb(row: Dict[str, Any]) -> str:
    """The row's suburb, recovering it where a STATE NAME was stored in that column.

    56 live Proxima rows hold suburb="New South Wales". The address parser only knew the
    abbreviated form, so on "…, BINGARA DRIVE, WILTON, New South Wales, 2571" the full
    name was taken for the suburb and Wilton was swallowed by the street. Those rows
    cannot be found by suburb, cannot be distance-searched, and cannot be matched against
    the same lot in another channel — which is how the Proxima price error was confirmed.

    Corrected on READ, not rewritten in the database, because suburb_norm is part of
    building_content_hash (database.py:225): editing it in place would change the row's
    identity and the next harvest would insert a second copy instead of updating it.
    The parser fix in sources/proxima.py is what makes the stored value right; this
    makes the rows usable until that harvest can run.

    Returns the stored suburb unchanged whenever there is nothing to correct — this
    never invents a suburb, it only reads the one the row's own address already states.
    """
    stored = str(row.get("suburb") or "").strip()
    if stored.upper() not in _STATE_TOKENS:
        return stored

    parts = [p.strip() for p in str(row.get("lot_address") or "").split(",") if p.strip()]
    if len(parts) < 2:
        return stored
    if re.fullmatch(r"\d{4}", parts[-1]):          # postcode
        parts.pop()
    if parts and parts[-1].upper() in _STATE_TOKENS:
        parts.pop()
    # Whatever now sits at the end is the suburb, unless it is another state token or
    # carries a street number — in which case the address never named one.
    if parts:
        candidate = parts[-1].strip()
        if candidate.upper() not in _STATE_TOKENS and not re.match(r"^\d+\s", candidate):
            return candidate.title()
    return stored
