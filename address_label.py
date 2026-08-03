"""
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
