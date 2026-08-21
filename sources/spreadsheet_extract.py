"""
Adaptive spreadsheet/PDF stocklist extractor.

E-Agent distributes stock as downloadable files rather than HTML listings, and
every builder formats theirs differently. Observed live on e-agent.com.au:

  * "VIC Regional"  — one column of fixed-width text
                      ('Lot 82 Aberdeen      282   142.8  12.0  Sep-26 ...')
  * "NSW Dual"      — a proper multi-column grid with Status/AVAILABLE rows
  * "DUAL QLD"      — multi-column, builder + region + estate header rows

Rather than hand-mapping each, every row is flattened to one text line and fed to
the SAME field parser used for HTML listings (adaptive_extract.parse_fields), so
prices, sizes, bed/bath/car, suburb and titles are recognised wherever they sit.
Group/estate header rows (no price, few numbers) are remembered and used as
context for the lot rows beneath them.

Per-row hyperlinks are kept. A stocklist's "Download" cell carries the link to
that lot's own flyer or floorplan, which is the only per-listing link these files
contain — the file URL itself is shared by every row in the file. In XLSX that
link lives on `cell.hyperlink` (or inside a `=HYPERLINK()` formula); in PDF it is
a page annotation, matched to a row by vertical position.

Nothing is invented: a row without a recognisable price is skipped, and fields
that cannot be found stay None.
"""

import csv
import io
import os
import logging
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

from sources.adaptive_extract import parse_fields, _clean
from sources.feature_extract import parse_listing_features

logger = logging.getLogger("spb.extract.sheet")

try:
    import openpyxl
    XLSX_AVAILABLE = True
except ImportError:  # pragma: no cover
    XLSX_AVAILABLE = False

try:
    import pdfplumber
    PDF_AVAILABLE = True
except ImportError:  # pragma: no cover
    PDF_AVAILABLE = False

LOT_TOKEN = re.compile(r"\blot\s*\d+", re.I)
# Estate/region/builder header lines look like 'ʊ  Aberdeen - Winter Valley - VIC - House & Land'
HEADER_JUNK = re.compile(r"^[^\w]*", re.M)

# A link is only labelled a floorplan/brochure when it says so. Anything else is
# the lot's own page or file, which is still far more use than the shared
# stocklist URL currently stored on every row.
_LINK_KINDS = (
    ("floorplan_url", re.compile(r"floor\s*-?\s*plan|floorplan|site\s*plan", re.I)),
    ("brochure_url", re.compile(r"brochure|flyer|booklet|fact\s*sheet|spec\w*sheet", re.I)),
)
# openpyxl loses a =HYPERLINK() target when the workbook is opened for values.
_HYPERLINK_FN = re.compile(r'HYPERLINK\(\s*"([^"]+)"', re.I)
LinkList = List[Tuple[str, str]]          # [(anchor text, url), ...]
# One parsed source row: its flattened text, its hyperlinks, and whatever its
# table stated column by column. The third member is {} for any source that has no
# columns to state — see _pdf_rows_from_lines.
PdfRow = Tuple[str, LinkList, Dict[str, str]]


def _row_to_text(cells: List[Any]) -> str:
    """Flatten a spreadsheet row to a single normalised text line."""
    parts = []
    for v in cells:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            parts.append(s)
    return _clean(" ".join(parts))


def _classify_link(url: str, anchor: str = "") -> str:
    for key, rx in _LINK_KINDS:
        if rx.search(f"{anchor} {url}"):
            return key
    return "listing_url"


def _link_fields(links: LinkList) -> Dict[str, str]:
    """First link of each kind wins — stocklists list them left to right."""
    out: Dict[str, str] = {}
    for anchor, url in links:
        if url:
            out.setdefault(_classify_link(url, anchor), url)
    return out


STREET_RE = re.compile(
    r"\b\d+[A-Za-z]?(?:[/-]\d+[A-Za-z]?)?\s+[A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+)?\s+"
    r"(?:Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Court|Ct|Place|Pl|Crescent|Cres|Way|"
    r"Circuit|Cct|Parade|Pde|Boulevard|Blvd|Terrace|Tce|Close|Cl|Lane|Ln|Grove|Gr|"
    r"Rise|Loop|Esplanade|Esp|Walk|Mews|Green)\b\.?")


# Several per-builder stocklists open with a bare lot number rather than "Lot N"
# — Aldrich "103 Samara Estate Fraser Rise ...", Hermitage "1123 COMPLETED HOME 444
# ...". Only read it as a lot when the following word is not a street type (that case
# is a street number, and STREET_RE has already claimed it) and not a status or month.
_LEADING_LOT = re.compile(r"^(\d{1,5})[A-Za-z]?\s+(?=[A-Za-z])")
_NOT_AN_ESTATE_WORD = re.compile(
    r"^(available|not|unavailable|under|on|hold|sold|reserved|titled|untitled|registered|"
    r"completed|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|single|double|house|land|"
    r"eoi|new|lot|stage|from|price|total)$", re.I)


def _leading_lot(text: str) -> Optional[str]:
    m = _LEADING_LOT.match(_clean(text))
    if not m or STREET_RE.search(_clean(text)[:60]):
        return None
    nxt = _clean(text)[m.end():].split(" ")[0]
    return m.group(1) if nxt and not _NOT_AN_ESTATE_WORD.match(nxt) else None


_PRICE_ANY = re.compile(r"\$\s?[\d,\.]+")
_COMPACT_OK = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9'\-/&,. ]{4,55}$")
_PLACE_SEG = re.compile(r"^[A-Za-z][A-Za-z'\-]*(?: [A-Za-z'\-]+){0,2}(?: \d{4})?$")
# Gallery Group's sheet puts its own title in the row: "100 Park Royal Crescent,
# GALLERY STOCK LIST 2026". That is not a locality.
_NOT_A_PLACE = re.compile(r"\b(stock|list|price\w*|package\w*|availabilit\w*|"
                          r"release|stage|status|update[d]?|sheet|report)\b", re.I)
# Words that mark a stocklist ROW rather than an address. Without these, a jammed row
# that happens to be short once its prices are removed ("103 Samara Estate Fraser Rise
# Available Bristol 15") passes for an address and lands in the client's sheet whole.
_ROW_NOT_ADDRESS = re.compile(
    r"\b(available|unavailable|under\s*offer|on\s*hold|hold|sold|reserved|leased|tbc|"
    r"titled|untitled|registered|single|double|storey|completed|eoi|"
    r"house\s*&?\s*land|dual\s*key)\b", re.I)


def _already_an_address(text: str) -> Optional[str]:
    """Some builders' PDFs give a row that IS just an address ("Lot 444, Hillcrest",
    "4 Windsor Street, BRISBANE NORTH"). Composing a label from parts would only throw
    half of it away, so keep it — minus the price."""
    s = re.sub(r"\s+,", ",", _clean(_PRICE_ANY.sub(" ", text))).strip(" ,-")
    if len(s) > 55 or not _COMPACT_OK.match(s) or _ROW_NOT_ADDRESS.search(s):
        return None
    # must actually look addressed: a leading number, a "Lot N", or a street type.
    # Otherwise marketing copy ("Package from $650,000 enquire now") qualifies.
    if not (re.match(r"^\d", s) or re.search(r"\blot\s*\d", s, re.I) or STREET_RE.search(s)):
        return None
    # a lot or street number, plus maybe a postcode, is fine; a grid of figures is not
    return s if len(re.findall(r"(?<![\w/])\d+(?:\.\d+)?(?!\w)", s)) <= 2 else None


def _place_suffix(text: str, after: int) -> Optional[str]:
    """The first place-looking comma segment after a street match, so "520 Stony Drive"
    keeps its ", Alluvium" instead of being trimmed to the street alone."""
    for seg in _PRICE_ANY.sub(" ", text[after:]).split(","):
        seg = _clean(seg).strip(" -")
        if seg and _PLACE_SEG.match(seg) and not _NOT_A_PLACE.search(seg):
            return seg
    return None


def _address_label(text: str, fields: dict, context: str = "") -> Optional[str]:
    """A short, human-readable label for the client's sheet, or None if the row
    offers nothing better than its own raw text.

    Coleen's complaint was that the address column held the entire stocklist row —
    `parse_fields` sets `lot_address` to the whole line containing "Lot N", which for
    a flattened spreadsheet row is everything. Prefer "105 Almond Street, Denman" or
    "Lot 82, Aberdeen". The full row is never lost: it is kept in `source_text`.
    """
    parts = []
    street = STREET_RE.search(text)
    lot = fields.get("lot_number") or _leading_lot(text)
    if street:
        parts.append(_clean(street.group(0)))
        place = _place_suffix(text, street.end())
        if place:
            parts.append(place)
            return ", ".join(parts)
    elif (compact := _already_an_address(text)):
        return compact
    elif lot:
        parts.append(f"Lot {lot}" if not str(lot).lower().startswith(("lot", "cc-")) else str(lot))
    estate = _clean(re.sub(r"^[^A-Za-z0-9]+", "",
                           str(fields.get("estate_name") or _estate_from_context(context) or "")))
    if parts and estate and 2 < len(estate) <= 44 and "$" not in estate \
            and estate.lower() not in parts[0].lower():
        parts.append(estate)
    return ", ".join(parts) if parts else None


def _estate_from_context(header: str) -> Optional[str]:
    """'ʊ Aberdeen - Winter Valley - VIC - House & Land' -> 'Aberdeen'."""
    if not header:
        return None
    first = _clean(re.sub(r"^[^A-Za-z0-9]+", "", header)).split(" - ")[0].strip()
    return first or None


# Column-name vocabulary. A header row has no price and few digits, so it used to be
# mistaken for an estate banner and remembered as context — which is how six rows ended
# up addressed "Gallery Stock List Hold Tradition Lot Land Title Date House Design ...".
_HEADER_WORDS = frozenset("""
status lot lots street st no num number land size sizes sqm m2 area floor price prices
title titled est estimated building build design designs name facade type types bed beds
bedroom bedrooms bath baths bathroom bathrooms car cars garage contract package packages
portal link links suburb estate address frontage storey storeys total totals yield rent
rental house houses furniture date dates stage release avail available availability
region state postcode deposit gross net weekly annual comments notes ref code product
completion settlement developer builder agent width depth aspect orientation
""".split())


def _is_column_header(text: str) -> bool:
    """True for a row of column names rather than a lot or an estate banner."""
    if _PRICE_ANY.search(text):
        return False
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z']*", text.lower()) if len(w) > 1]
    if not words:
        return False
    known = sum(1 for w in words if w in _HEADER_WORDS)
    # a wrapped second header line is short but entirely column names ("Street # Type")
    if known == len(words) and len(words) >= 2:
        return True
    return len(words) >= 4 and known / len(words) >= 0.6


# Which column headers name money. "Land Price" is money; "Land Size" is not, and
# "Total (sqm)" is not either — hence the exclusion list, which is checked first.
_PRICE_WORD = re.compile(r"price|cost|\$|total|amount|value|deposit|rrp|package|pkg", re.I)
_NOT_PRICE_WORD = re.compile(
    r"size|area|sqm|m2|m²|width|depth|frontage|bed|bath|car|garage|storey|living|"
    r"no\.|number|lot|date|title|status|design|facade|link|brochure|yield|%|"
    r"floor|internal|external|aspect|orientation|stage|type|name|comment|note", re.I)


def _is_price_header(label: str) -> bool:
    label = label or ""
    return bool(_PRICE_WORD.search(label)) and not bool(_NOT_PRICE_WORD.search(label))


# Columns whose header NAMES a fact, so the value can be read instead of inferred from
# the flattened row. render() joins a row into one string and every fact is then scraped
# back out of that text, which works for money (a "$" is unmistakable) and fails for bare
# numbers: on
#
#   "AVAILABLE Option 2 Single Kuraby Traditional 10 344.3 164.93 4 2 1 Q4 2026"
#                                        frontage ┘ land ─┘ house ┘ b b c
#
# nothing marks which number is which, so bedrooms, bathrooms, car spaces and house size
# were all left blank -- on 35%, 40%, 53% and 63% of live rows respectively. A brief that
# states any minimum then excludes those rows entirely, correctly but expensively.
#
# The sheet's own header row says "Bedrooms | Bathrooms | Garage | Land Size (sqm) |
# House Size (sqm)". Reading the column it names is not a guess.
_SPEC_HEADERS = (
    ("bedrooms",   re.compile(r"\bbed(?:room)?s?\b", re.I),                 1, 12),
    ("bathrooms",  re.compile(r"\bbath(?:room)?s?\b", re.I),                1, 12),
    ("car_spaces", re.compile(r"\b(?:car(?:\s*space)?s?|garage|gge)\b", re.I), 0, 12),
    # These two carry the extractor's own field names, not the database column names —
    # parse_fields emits land_size_sqm / house_size_sqm and the mapping to land_sqm /
    # house_sqm happens downstream. Naming them after the columns filled nothing.
    ("land_size_sqm",  re.compile(r"\b(?:land|lot)\s*(?:size|area)\b", re.I), 30, 100_000),
    ("house_size_sqm", re.compile(r"\b(?:house|home|build(?:ing)?|floor|living|internal)"
                                  r"\s*(?:size|area)\b", re.I),              20, 3_000),
    ("frontage_m", re.compile(r"\bfrontage\b", re.I),                       1, 200),
)

# A header naming money is never a spec column: "Land Price" is money, "Land Size" is not.
_SPEC_DISQUALIFIER = re.compile(r"price|cost|\$|deposit|total|rent", re.I)


class _MoneyColumns:
    """Remembers which columns a stocklist's own header row says hold money.

    Every off-site host surfaced the same blocker: an xlsx or Google Sheets tab stores
    prices as NUMBERS, so a flattened row reads "519000.0" and `parse_fields` — which
    requires a literal "$" — throws the whole listing away. Measured on live files: Kemps
    yielded 0 listings instead of 107, AVIA 0 of 91, Invision 0 of 13, DBN Homes and
    Prosperity Residential 0 entirely, and all four Dropbox workbooks 0.

    The spreadsheet already knew which columns were money. This puts the "$" back from
    that knowledge, rather than loosening the price pattern — which would immediately
    start reading land sizes and floor areas as prices.
    """

    MIN_MONEY = 1000        # below this a bare number in a price column is not a price

    def __init__(self):
        self.cols: set = set()
        self._labels: Dict[int, str] = {}
        # Set here as well as in feed_header: a sheet with no recognisable header row
        # never reaches the assignment there, and specs() would then raise on every row.
        self.spec_cols: Dict[int, str] = {}
        self._prev_was_header = False

    def feed_header(self, cells: List[Tuple[str, bool, int]], is_header: bool) -> None:
        """Header rows are often wrapped over two lines ("Land" / "Price"), so
        consecutive header rows accumulate per column instead of replacing."""
        if not is_header:
            self._prev_was_header = False
            return
        if not self._prev_was_header:
            self._labels = {}
        for text, _cur, idx in cells:
            if text:
                self._labels[idx] = f"{self._labels.get(idx, '')} {text}".strip()
        self.cols = {i for i, lab in self._labels.items() if _is_price_header(lab)}
        self.spec_cols = {}
        for idx, label in self._labels.items():
            if _SPEC_DISQUALIFIER.search(label):
                continue
            for field, pattern, _lo, _hi in _SPEC_HEADERS:
                if pattern.search(label) and field not in self.spec_cols.values():
                    self.spec_cols[idx] = field
                    break
        self._prev_was_header = True

    def specs(self, cells: List[Tuple[str, bool, int]]) -> Dict[str, float]:
        """Facts read from the columns this sheet's header row named.

        Out-of-range values are dropped rather than clamped: a "Bedrooms" column holding
        0 or 45 means the header mapping is wrong for that row, and a wrong bedroom count
        sails through a "minimum 4 bedrooms" filter exactly like an invented one would.
        """
        out: Dict[str, float] = {}
        bounds = {f: (lo, hi) for f, _p, lo, hi in _SPEC_HEADERS}
        for text, _is_currency, idx in cells:
            field = self.spec_cols.get(idx)
            if not field or field in out:
                continue
            raw = str(text or "").strip().replace(",", "")
            m = re.match(r"^(\d+(?:\.\d+)?)", raw)
            if not m:
                continue
            value = float(m.group(1))
            lo, hi = bounds[field]
            if lo <= value <= hi:
                out[field] = value
        return out

    def render(self, cells: List[Tuple[str, bool, int]]) -> str:
        parts = []
        for text, is_currency, idx in cells:
            if not text:
                continue
            if "$" not in text and (is_currency or idx in self.cols):
                try:
                    value = float(str(text).replace(",", "").strip())
                except ValueError:
                    parts.append(text)
                    continue
                if abs(value) >= self.MIN_MONEY:
                    parts.append(f"${value:,.0f}")
                    continue
            parts.append(text)
        return _clean(" ".join(parts))



def _apply_header_specs(listings: List[Dict[str, Any]], specs: Dict[str, float]) -> None:
    """Fill a fact the flattened-text scan missed, using the column the header named.

    Only ever fills a BLANK. A value the text scan already found stays, because it was
    read from the row's own words ("4 beds / 2 baths") which is at least as good evidence
    as a column position, and because overriding it could silently rewrite a fact that is
    currently correct. This can add coverage; it cannot change an existing answer.
    """
    if not specs:
        return
    for row in listings:
        for field, value in specs.items():
            if not row.get(field):
                row[field] = int(value) if field in ("bedrooms", "car_spaces") else value


# A "header" that is really a stray line, and what it costs when one is believed.
#
# A section header sets the context for every lot BENEATH it, so a line wrongly accepted
# as one does not lose a suburb — it steals one, from every row until the next real
# header. Measured on the Hudson Homes stocklist (104 lots, twelve suburbs): 26 rows had
# their estate recorded as "NSW" and 18 as "DUPLEX", and 47 of 104 ended with no suburb
# at all, because two lines were being read as sections:
#
#   NSW      the document's own state line, three lines under the title
#   DUPLEX   the tail of a wrapped cell — "MAGNESIUM 37 -" breaks, and "DUPLEX" lands on
#            a line of its own
#
# Both pass the size-and-digits test below, because both genuinely are short lines with
# few digits. They are rejected by name instead: a bare state is never a section, and a
# single bare product word is a fragment of the row above, not a place.
_NOT_A_SECTION = re.compile(
    r"^(?:vic|nsw|qld|sa|wa|nt|act|tas"
    r"|duplex|dual\s*key|house|townhouse|apartment|villa|terrace|unit|land"
    r"|single|double|storey|traditional|medium|narrow"
    r"|available|sold|titled|registered|under\s*offer|on\s*hold"
    r"|offer|hold|pending|reserved|deposit|contract|settled"
    r"|total|subtotal|continued|cont\.?|page)$", re.I)

# A sentence, not a banner. Every page of these stocklists ends with a disclaimer, and it
# is short and digit-free enough to pass every other test here.
_PROSE = re.compile(
    r"\b(please|contact us|further details|clarification|disclaimer|subject to change"
    r"|indicative only|terms and conditions|images|renders|floor plans|e&oe)\b", re.I)


def _is_group_header(text: str, fields: Dict[str, Any]) -> bool:
    """Estate/builder/region banner rather than a lot row."""
    if fields.get("advertised_package_price"):
        return False
    if LOT_TOKEN.search(text):
        return False
    stripped = str(text or "").strip().strip("-–—:|").strip()
    # Too short to be a name, or the encoding fragments a PDF leaves behind ("m²" comes
    # through as "m?" or worse and was being stored as an estate on two rows).
    if len(stripped) < 4:
        return False
    if _NOT_A_SECTION.match(stripped):
        return False
    # A NUMBER IS NEVER A SECTION. "January 2026" wraps and drops "2026" on a line of its
    # own, four characters of pure digits, which slipped under the ceiling below and took
    # the estate of 9 rows on the FRD Homes stocklist.
    if re.fullmatch(r"[\d\s.,/-]+(?:\s+[A-Z]{1,3})?", stripped):
        return False
    # PROSE IS NEVER A SECTION. The disclaimer at the foot of every page — "For further
    # details or clarification, please contact us." — is short enough and digit-free
    # enough to pass every test here, and it claimed 6 rows.
    if _PROSE.search(stripped):
        return False
    # short-ish line, mostly words, few digits
    digits = sum(c.isdigit() for c in text)
    if bool(text) and len(text) < 90 and digits <= 4:
        return True
    # A HEADER MAY CARRY A STREET NUMBER AND A STAGE NUMBER AND STILL BE A HEADER.
    #
    # The digit ceiling above is what a lot row trips over, and it also throws away real
    # sections: "152-156 Bancroft Terrace - Stage 1, Deception Bay" has seven digits, so
    # every lot beneath it kept the previous section's suburb. On the FRD Homes stocklist
    # that was 43 of 98 rows — the same document shape as Hudson's, failing for a
    # different reason.
    #
    # These lists name the section as "<place or street> - Stage N, SUBURB", so the tail
    # after the last comma is the tell: a short run of plain words, no digits, no money.
    # A lot row cannot look like that — it ends in a price, a date or a bed/bath count.
    if not text or len(text) >= 120 or "$" in text:
        return False
    if "," not in text:
        return False
    tail = text.rsplit(",", 1)[-1].strip()
    words = tail.split()
    return (1 <= len(words) <= 3
            and all(w.replace("-", "").replace("'", "").isalpha() for w in words)
            and not _NOT_A_SECTION.match(tail))


_BANNER_SPLIT = re.compile(r"\s*[|/,\-–—]\s*")
_STATE_TOKENS = ("VIC", "NSW", "QLD", "SA", "WA", "NT", "ACT", "TAS")
_NOT_A_LOCALITY = re.compile(
    r"house|land|terrace|townhouse|apartment|dual|key|stage|estate|stock|list|"
    r"contract|price|package|available|updated|as of|smsf|rating|energy|star", re.I)

# Words of a banner segment, for the trailing-locality fallback below.
_BANNER_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")

_BANNER_GEO = None


_BANNER_POSTCODE = re.compile(r"(?<!\d)(\d{4})(?!\d)")


def _state_from_banner_postcode(header: str) -> str:
    """The state a section banner's own postcode proves, or "".

    Only used where the rows record no state at all -- see the note in _context_suburb.
    Every postcode in the banner must agree, so a header carrying two of them (a range,
    or two projects on one line) proves nothing and is refused.
    """
    from state_resolver import state_from_postcode

    found = {state_from_postcode(code) for code in _BANNER_POSTCODE.findall(header or "")}
    found.discard("")
    found.discard(None)
    return next(iter(found)) if len(found) == 1 else ""


def _context_suburb(header: str, state: str = "") -> Optional[str]:
    """The LOCALITY named in a section banner, or None.

    'Aberdeen - Winter Valley - VIC - House & Land'        -> 'Winter Valley'
    'Harvest Hill | 1377 Hue Hue Road, Wyee'               -> 'Wyee'
    'WILLOWGLEN ESTATE - STAGE 1 / WARNERVALE / NSW / SMSF'-> 'Warnervale'
    'One Part Contracts'                                   -> None

    This used to split on "-" and return whichever fragment sat in position 1, with no
    check that the result was a place. That is where the catalogue's fake suburbs came
    from: a banner reading "One Part Contracts" or "7 Star Energy Rating" was stored
    verbatim as the suburb, and those became the largest cohorts in the whole database —
    47 and 54 listings each, pointed at somewhere that does not exist.

    Every candidate is now checked against the suburb index, and the LAST match wins
    because an Australian address ends with its locality ('… Hue Hue Road, Wyee'). If
    nothing in the banner is a real locality the answer is None, and the row keeps an
    empty suburb rather than a confident wrong one.
    """
    if not header:
        return None
    global _BANNER_GEO
    if _BANNER_GEO is None:
        from geo import SuburbGeoIndex
        _BANNER_GEO = SuburbGeoIndex()
    if not _BANNER_GEO.loaded:
        return None

    cleaned = re.sub(r"^[^A-Za-z0-9]+", "", header)
    found = None
    for bit in _BANNER_SPLIT.split(cleaned):
        bit = bit.strip()
        if not bit or bit.upper() in _STATE_TOKENS or _NOT_A_LOCALITY.search(bit):
            continue
        # The WHOLE segment must be a locality. Searching INSIDE it for a known suburb
        # name is the estate-name trap: the banner "Bingara Gorge - Spec Contract" then
        # yields "Bingara", a real NSW town 500 km from the estate, whose lots are
        # actually in Wilton. An estate is frequently named after somewhere else, so a
        # substring match is not evidence of location.
        # locate() is keyed on (suburb, state), so with no state it never matches —
        # fall back to "is this a locality in ANY state". The banner usually names the
        # state alongside, and the row's own state is passed in where the parser found one.
        known = (_BANNER_GEO.locate(bit, state) if state
                 else _BANNER_GEO.states_for_suburb(bit))
        if known:
            found = bit.title()
    if found:
        return found

    # THE LOCALITY AT THE END OF A SEGMENT, when the whole segment is not one.
    #
    # Coleen, 19 Aug: a Donnybrook lot she was looking at in E-Agent could not be found in
    # the app. It was there, priced and bedroomed, with NO SUBURB — because its banner
    # reads "Estate : Balmain Donnybrook", which is a single segment, and _NOT_A_LOCALITY
    # matches "Estate" in it, so the whole thing was discarded. "Donnybrook" was sitting
    # at the end of it the entire time.
    #
    # This is NOT the substring search the loop above refuses. Searching anywhere inside a
    # segment is the estate-name trap — "Bingara Gorge - Spec Contract" yields "Bingara",
    # a real town 500 km from the lots. An Australian banner ENDS with its locality, so
    # the tail is a different claim from any substring, and the trap survives it:
    # "Bingara Gorge" tails to "Gorge" and "Bingara Gorge", neither of which is a place.
    #
    # THE STATE IS REQUIRED HERE, unlike above. Measured across the catalogue, a stateless
    # tail is not safe even when it is unambiguous: "Caboolture Central Springs" tails to
    # "Springs", which exists in exactly one state — Western Australia — while Caboolture
    # is in Queensland. Requiring the row's own state costs about 37 recoveries and
    # removes the whole class. "Donnybrook" itself exists in QLD, VIC and WA.
    #
    # Longest tail first, so "Armstrong Creek" wins over "Creek".
    #
    # THE BANNER'S OWN POSTCODE COUNTS AS THE STATE. A stocklist that records no state on
    # its rows still writes it into the section header: "Toolern Waters , Melton South
    # 3338", "Mambourin Green Estate , Mambourin 3024", "Lyra Estate, Beveridge 3753".
    # 3338 is a Victorian postcode, so the locality beside it is the Victorian one, and
    # the ambiguity this rule exists to refuse is not present.
    #
    # Without this the whole document is refused: the Goldstate list names its suburb
    # above every block and carries no state column, so 76 of its 89 lots had no suburb
    # while the answer sat two words to the left of the number that proves it.
    if not state:
        state = _state_from_banner_postcode(header)
    if not state:
        return None
    for bit in _BANNER_SPLIT.split(cleaned):
        words = _BANNER_WORD.findall(bit or "")
        for n in (3, 2, 1):
            if len(words) < n:
                continue
            cand = " ".join(words[-n:])
            if cand.upper() in _STATE_TOKENS or _NOT_A_LOCALITY.search(cand):
                continue
            if _BANNER_GEO.locate(cand, state):
                return cand.title()
    return None


# Column headers that name a locality outright. Deliberately short: "City" is excluded
# because builders use it for the nearest capital ("Sydney") rather than the lot's own
# locality, and "Area" is a land size far more often than a place.
_SUBURB_COLUMN = re.compile(r"^(suburb|localit(y|ies)|town)s?$", re.I)


def _column_suburb(value: Any, state: str = "") -> Optional[str]:
    """A Suburb COLUMN's own value, where it names a real locality.

    Held to exactly the same bar as a banner segment — see _context_suburb. A stated
    column is better evidence than a banner (it is per-row, so it survives an estate
    that spans two towns), but it is not self-certifying: the same stocklists put
    "TBA", "Various" and an estate name in that column, and a repair that trusted the
    header alone once composed "Lot 800, GARAGE SA" out of a spreadsheet heading.
    """
    if not value:
        return None
    bit = _clean(str(value)).strip(" ,-|/")
    if not bit or bit.upper() in _STATE_TOKENS or _NOT_A_LOCALITY.search(bit):
        return None
    global _BANNER_GEO
    if _BANNER_GEO is None:
        from geo import SuburbGeoIndex
        _BANNER_GEO = SuburbGeoIndex()
    if not _BANNER_GEO.loaded:
        return None
    known = (_BANNER_GEO.locate(bit, state) if state
             else _BANNER_GEO.states_for_suburb(bit))
    return bit.title() if known else None


def _column_map(values: List[List[Any]]) -> Dict[int, str]:
    """column index -> canonical name, read from the table's OWN header row.

    Only the first few rows are considered: a stocklist repeats its header on every page
    but never halfway down one, and scanning further starts matching lot rows that happen
    to be wordy.
    """
    for cells in values[:4]:
        flat = [_clean(str(c)) if c is not None else "" for c in cells]
        joined = " ".join(x for x in flat if x)
        if not joined or not _is_column_header(joined):
            continue
        out = {i: "suburb" for i, name in enumerate(flat)
               if _SUBURB_COLUMN.match(name.strip())}
        if out:
            return out
    return {}


# ---------------------------------------------------------------- row iterators

def _formula_links(data: bytes) -> Dict[Tuple[int, int, int], str]:
    """(sheet index, row, column) -> url for `=HYPERLINK("...")` cells.

    Only consulted when a workbook carries no real cell hyperlinks, since loading
    it a second time for formulas is pure overhead otherwise.
    """
    out: Dict[Tuple[int, int, int], str] = {}
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=False)
    except Exception:
        return out
    for si, sheet in enumerate(wb.worksheets):
        for row in sheet.iter_rows():
            for c in row:
                if isinstance(c.value, str) and "HYPERLINK(" in c.value.upper():
                    m = _HYPERLINK_FN.search(c.value)
                    if m:
                        out[(si, c.row, c.column)] = m.group(1)
    return out


_CURRENCY_FORMAT = re.compile(r"\$|currency|\[\$", re.I)


def _iter_rows_xlsx(data: bytes) -> Iterator[
        Tuple[int, List[Tuple[str, bool, int]], LinkList, str, int]]:
    """Yield (sheet index, cells, links, sheet name, row number).

    Cells are read as objects rather than values so two things survive that
    `values_only=True` discards: `cell.hyperlink` (the per-lot link) and
    `cell.number_format` (which says a bare number is money).

    The sheet NAME and ROW NUMBER are carried out too, because a stocklist URL on its
    own opens a 400-row workbook and leaves Colin to find the lot by eye — his ask of
    9 Aug was the row, not the file. openpyxl already knows both (`sheet.title`,
    `cell.row`); this loop simply threw them away.
    """
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    formulas = {} if any(getattr(ws, "_hyperlinks", None) for ws in wb.worksheets) \
        else _formula_links(data)
    for si, sheet in enumerate(wb.worksheets):
        for row in sheet.iter_rows():
            cells: List[Tuple[str, bool, int]] = []
            links: LinkList = []
            row_no = 0
            for c in row:
                row_no = c.row                    # 1-based, and what Excel shows Colin
                s = str(c.value).strip() if c.value is not None else ""
                is_currency = bool(isinstance(c.value, (int, float))
                                   and _CURRENCY_FORMAT.search(c.number_format or ""))
                if s:
                    cells.append((s, is_currency, c.column))
                url = getattr(getattr(c, "hyperlink", None), "target", None) \
                    or formulas.get((si, c.row, c.column))
                if url:
                    links.append((s, str(url)))
            if cells or links:
                yield si, cells, links, str(sheet.title or ""), row_no


def _annot_links(page: Any) -> List[Tuple[float, float, str]]:
    """(vertical centre, x0, uri) for every URI annotation on the page."""
    out: List[Tuple[float, float, str]] = []
    for a in (getattr(page, "annots", None) or []):
        uri = a.get("uri") or ((a.get("data") or {}).get("A") or {}).get("URI")
        if isinstance(uri, bytes):
            uri = uri.decode("utf-8", "ignore")
        if not uri or a.get("top") is None or a.get("bottom") is None:
            continue
        try:
            yc = (float(a["top"]) + float(a["bottom"])) / 2.0
            out.append((yc, float(a.get("x0") or 0.0), str(uri)))
        except (TypeError, ValueError):
            continue
    return out


def _links_in_band(annots: List[Tuple[float, float, str]], top: Any, bottom: Any,
                   pad: float = 2.0) -> LinkList:
    """Annotations whose centre falls on this row, ordered left to right."""
    try:
        top, bottom = float(top), float(bottom)
    except (TypeError, ValueError):
        return []
    hits = [(x0, uri) for (yc, x0, uri) in annots if top - pad <= yc <= bottom + pad]
    return [("", uri) for _, uri in sorted(hits)]


def _pdf_rows_from_tables(page: Any, annots: List[Tuple[float, float, str]]
                          ) -> List["PdfRow"]:
    try:
        tables = page.find_tables() or []
    except Exception as e:                                              # noqa: BLE001
        # Re-raised, not swallowed. Returning [] made "this page has no tables" and
        # "this page could not be read" identical, so a page no strategy could read
        # passed as an empty one and was never counted as lost.
        logger.warning("pdf page %s: table detection failed (%s)",
                       getattr(page, "page_number", "?"), e)
        raise
    rows: List[PdfRow] = []
    dropped = 0
    for table in tables:
        try:
            values = table.extract()
        except Exception as e:                                          # noqa: BLE001
            # Counted and reported below. A page with two tables can lose one and still
            # beat the lines strategy on count, so the truncated read wins the page and
            # nothing says a table went missing.
            dropped += 1
            logger.warning("pdf page %s: 1 of %d table(s) could not be extracted (%s)",
                           getattr(page, "page_number", "?"), len(tables), e)
            continue
        # THE COLUMNS ARE THE POINT OF THIS STRATEGY, AND THEY USED TO BE THROWN AWAY.
        #
        # _row_to_text flattens the cells to one string, which is all the price/bed/bath
        # parsers need — but it also destroys the one thing the text can never recover.
        # E-Agent stocklists publish a SUBURB COLUMN ("Lot 324 Dapple | Armstrong |
        # Palomino Estate | ..."), and once flattened, "Armstrong" is just another word
        # in a row full of them. 3,070 live rows had no suburb while the PDF they came
        # from stated one in its own column.
        colmap = _column_map(values)
        for row_obj, cells in zip(getattr(table, "rows", []), values):
            cells = list(cells)
            text = _row_to_text(cells)
            bbox = getattr(row_obj, "bbox", None)
            links = _links_in_band(annots, bbox[1], bbox[3]) if bbox else []
            columns = {name: _clean(str(cells[i]))
                       for i, name in colmap.items()
                       if i < len(cells) and cells[i]}
            if text or links:
                rows.append((text, links, columns))
    return rows


def _pdf_rows_from_lines(page: Any, annots: List[Tuple[float, float, str]]
                         ) -> List["PdfRow"]:
    try:
        lines = page.extract_text_lines() or []
    except Exception:
        # The fallback routes through the same .chars that just failed, so it usually
        # fails too — and it sat OUTSIDE the except, which meant it re-raised and took
        # the page with it. Guarded: an unreadable page yields no lines, and the tables
        # strategy still gets its turn at the same page.
        try:
            lines = [{"text": l, "top": None, "bottom": None}
                     for l in (page.extract_text() or "").splitlines()]
        except Exception as e:                                          # noqa: BLE001
            # Re-raised on purpose. The caller guards each STRATEGY separately, so the
            # tables strategy still gets its turn at this page, and a page where every
            # strategy failed is counted as unreadable rather than passing as empty.
            logger.warning("pdf page %s: no text could be read (%s)",
                           getattr(page, "page_number", "?"), e)
            raise
    out = []
    for ln in lines:
        text = _clean(ln.get("text", ""))
        if text:
            # No columns: a text line has none to state. The empty dict keeps both
            # strategies the same shape so _rows_to_listings never has to ask which
            # produced a row.
            out.append((text, _links_in_band(annots, ln.get("top"), ln.get("bottom")), {}))
    return out


# Both strategies are tried on every page and the one yielding more LISTINGS wins.
# Preferring tables outright cost five builders entirely — FRD, Hudson (QLD and NSW),
# Alete and Land Build Direct publish borderless PDFs where find_tables() detects a
# handful of spurious regions, and merely producing rows was enough to suppress the
# text-line path that reads them correctly.
# How many pages of one PDF are read. Named so the log can say when it binds.
PDF_PAGE_CAP = int(os.environ.get("SPB_PDF_PAGE_CAP", "25"))

_PDF_STRATEGIES = (("tables", _pdf_rows_from_tables), ("lines", _pdf_rows_from_lines))


# ------------------------------------------------------------------- extraction

# Words that carry no identity — they label a figure rather than name a property.
_MONEY_LABEL = re.compile(
    r"\b(land|build|building|house|package|total|price|prices|from|inc|incl|gst|"
    r"deposit|balance|rrp|est|approx|only|now|was|save|saving|plus)\b", re.I)


def _is_price_fragment(text: str, fields: Dict[str, Any]) -> bool:
    """True when a row is a PRICE BROKEN OUT OF a listing, not a listing.

    Some emailed PDFs print a property's figures on their own lines, so the extractor saw
    "$1,266,900*", "Land $714,900" and "Build $552,000" as three separate properties —
    each inheriting the suburb from the heading above, so each looked complete enough to
    store, and each landed in Coleen's sheet with a price in the address column.

    Deliberately narrow, because a first attempt at this deleted 112 rows and most were
    real: an APARTMENT pricelist row is almost entirely numeric —
    "501 2 1 1 70 27 97 SE $913,000" is unit 501, 2 bed, 1 bath, 1 car, 97 m², south-east
    aspect. Judging on "has no words" threw those away. So a row only counts as a fragment
    when it carries no other FIGURES either: a unit number and a room count are identity,
    even with no letters attached to them.
    """
    if fields.get("lot_number") or fields.get("bedrooms") or fields.get("land_size_sqm"):
        return False
    if STREET_RE.search(text or ""):
        return False
    stripped = _PRICE_ANY.sub(" ", text or "")
    if len(re.sub(r"[^A-Za-z]", "", _MONEY_LABEL.sub(" ", stripped))) >= 4:
        return False                      # it has words of its own
    # ...and no numbers of its own beyond the prices themselves
    return len(re.findall(r"\d+(?:\.\d+)?", stripped)) <= 1


def _listing_from_row(text: str, links: LinkList, context: str, source_label: str,
                      builder_hint: str,
                      columns: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """A parsed listing, or None if the row is not one."""
    fields = parse_fields(text)
    # availability/storey/lot/postcode/estate/incentives, from the FULL row
    fields.update({k: v for k, v in parse_listing_features(
        text, context, fields.get("advertised_package_price")).items()
        if v is not None})
    if not fields.get("advertised_package_price"):
        return None
    if _is_price_fragment(text, fields):
        return None
    # A row that is nothing but a wall of prices is a summary or a transposed layout, not a
    # listing — seen live on the commercial pages ("$1,300,000 $1,250,000 $1,250,000 ...").
    # Guarded by "and nothing that identifies a property", so the QLD dual-occupancy rows
    # (five figures, but a stock code) are unaffected.
    if len(_PRICE_ANY.findall(text)) >= 5 and not (
            fields.get("lot_number") or fields.get("bedrooms")
            or _leading_lot(text) or STREET_RE.search(text)):
        return None
    fields["source_text"] = text                 # UNtruncated: the parser needs it all
    if not fields.get("estate_name"):
        fields["estate_name"] = _estate_from_context(context)
    label = _address_label(text, fields, context)
    if label:                                    # beats the raw row parse_fields found
        fields["lot_address"] = label
    elif not fields.get("lot_address"):
        fields["lot_address"] = _clean(text)[:110]
    if not fields.get("suburb"):
        # The row's own state where the parser found one, so an ambiguous banner
        # segment resolves in the right state ("Springfield" is QLD, NSW and SA).
        state = fields.get("state") or ""
        # A STATED COLUMN BEATS A BANNER. The banner names the estate's town, which is
        # one town; a Suburb column is per-row and stays right where an estate spans
        # two. Both are checked against the suburb index, so neither can invent a place.
        fields["suburb"] = (_column_suburb((columns or {}).get("suburb"), state)
                            or _context_suburb(context, state))
    fields.update(_link_fields(links))
    filled = sum(1 for k in ("bedrooms", "land_size_sqm", "suburb", "house_size_sqm")
                 if fields.get(k))
    return {
        **fields,
        "builder_name": builder_hint,
        "estate_context": _clean(context)[:110],
        "source_url_or_ref": source_label,
        "extraction_confidence": round(min(1.0, 0.5 + 0.12 * filled), 2),
    }


def _rows_to_listings(rows: List[Any], context: str, source_label: str,
                      builder_hint: str,
                      banner: str = "") -> Tuple[List[Dict[str, Any]], str, str]:
    """Parse a batch of rows, carrying the estate/group header context through them.

    Returns (listings, context, banner) rather than mutating, so two competing PDF
    strategies can each be tried on a page without one polluting the other's state.

    `banner` is the FIRST group header in the file — the document's own title row, which
    the estate banners beneath it overwrite in `context`. It is the one place a builder
    who publishes a multi-estate workbook names itself ("CREATION HOMES NSW STOCK LIST"),
    so it is kept and stamped on every listing for the caller to attribute from.
    """
    out: List[Dict[str, Any]] = []
    for row in rows:
        # Two members from the spreadsheet readers, three from the PDF ones. Tolerated
        # rather than normalised at every call site, so a reader that has no columns to
        # state does not have to say so.
        text, links = row[0], row[1]
        columns = row[2] if len(row) > 2 else {}
        if not text:
            continue
        if _is_column_header(text):
            continue                         # column names: neither a lot nor a banner
        listing = _listing_from_row(text, links, context, source_label, builder_hint,
                                    columns)
        if listing is None:
            if _is_group_header(text, {}):   # no price: _listing_from_row already told us
                # THE DOCUMENT'S OWN TITLE, REPEATED ON EVERY PAGE, IS NOT A NEW SECTION.
                #
                # A multi-page stocklist prints its title as a running header, and each
                # repeat was resetting the context — so every lot after the first page
                # break inherited "Hudson Homes Stocklist" instead of the estate it sits
                # in. That was 39 of 104 rows on one document, and it scales with page
                # count: the longer the price list, the more of it loses its suburb.
                #
                # `banner` is already the first header seen, which IS the title. A later
                # line identical to it is the same title again, never a section called
                # after the whole document.
                if banner and text.strip() == banner.strip():
                    continue
                context = text
                banner = banner or text
            continue
        listing["source_banner"] = banner
        out.append(listing)
    return out, context, banner


def _assign_variant_ordinals(rows: List[Dict[str, Any]]) -> int:
    """Number rows that share an identity but are genuinely different packages.

    Identity ignores price so a price move updates in place. The cost is that two rows
    in one file which differ ONLY by price collapse into one — verified on the APLACE,
    Met Invest and Paramount stocklists. Numbering the siblings keeps both.

    Byte-identical rows are left alone: those are duplicates in the source file (a
    table detected twice across a page break) and SHOULD collapse. Returns the number
    of siblings numbered, so a caller can report it.
    """
    from database import building_content_hash          # local: avoids an import cycle

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(building_content_hash(r), []).append(r)
    numbered = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        seen, n = set(), 0
        for r in group:
            sig = (str(r.get("source_text")), r.get("advertised_package_price"))
            if sig in seen:
                continue                    # true duplicate — let it collapse
            seen.add(sig)
            if n:
                r["variant_ordinal"] = n
                numbered += 1
            n += 1
    return numbered


def _log_yield(kind: str, source_label: str, out: List[Dict[str, Any]]) -> None:
    numbered = _assign_variant_ordinals(out)
    links = sum(1 for o in out if o.get("listing_url") or o.get("floorplan_url")
                or o.get("brochure_url"))
    logger.info("%s %s -> %d listing(s), %d with a per-lot link%s", kind,
                source_label or "(file)", len(out), links,
                f", {numbered} price-only sibling(s) numbered" if numbered else "")


def extract_from_xlsx(data: bytes, source_label: str = "", builder_hint: str = "") -> List[Dict[str, Any]]:
    if not XLSX_AVAILABLE:
        logger.error("openpyxl not installed — cannot read xlsx stocklists.")
        return []
    try:
        rows = list(_iter_rows_xlsx(data))
    except Exception as e:
        logger.warning("could not open xlsx %s: %s", source_label, e)
        return []

    out: List[Dict[str, Any]] = []
    context, sheet, money = "", -1, _MoneyColumns()
    banner = ""
    for si, cells, links, sheet_name, row_no in rows:
        if si != sheet:
            context, sheet, money = "", si, _MoneyColumns()   # headers are per sheet
        # RENDER BEFORE DECIDING WHETHER THIS IS A HEADER.
        #
        # _is_column_header returns False the moment it sees money (_PRICE_ANY), which is
        # what stops a lot row being mistaken for column names. But a numeric sheet stores
        # the price as 705000 with the dollar sign living in the cell FORMAT, so the raw
        # text carries no "$" and the guard cannot fire — on exactly the sheets
        # _MoneyColumns exists to rescue. A row like
        #
        #     45  Available  Titled  House & Land  375  4  2  2  705000
        #
        # is then all header vocabulary, and feed_header rebuilds the column map out of
        # the lot's own data before the `continue`, emptying self.cols. Every row below it
        # loses its price and _listing_from_row drops it. Measured: 5 data rows in, 2 out;
        # on a sheet whose only text columns are Status and Title, 0 of 3.
        #
        # render() uses the map built from the rows ABOVE, so a data row gets its "$" back
        # before the test and a real header row is unaffected — it has no money to render.
        rendered = money.render(cells)
        if not rendered:
            continue
        is_header = _is_column_header(rendered)
        money.feed_header(cells, is_header)
        if is_header:
            continue
        listings, context, banner = _rows_to_listings(
            [(rendered, links)], context, source_label, builder_hint, banner)
        _apply_header_specs(listings, money.specs(cells))
        # Stamped on every listing this row produced. One row can yield several (priced
        # siblings that differ only by package), and they all genuinely live on that row —
        # sending Colin to the row is right even when it holds more than one lot.
        for _l in listings:
            _l["source_sheet"] = sheet_name
            _l["source_row"] = row_no
        out.extend(listings)
    _log_yield("xlsx", source_label, out)
    return out


def extract_from_pdf(data: bytes, source_label: str = "", builder_hint: str = "") -> List[Dict[str, Any]]:
    if not PDF_AVAILABLE:
        logger.error("pdfplumber not installed — cannot read pdf stocklists.")
        return []
    out: List[Dict[str, Any]] = []
    used, hurt = {}, []
    pages_total = 0
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            context, banner = "", ""
            pages_total = len(pdf.pages)
            for page in pdf.pages[:PDF_PAGE_CAP]:
                # ONE BAD PAGE COSTS ONE PAGE.
                #
                # This try used to wrap the whole loop, so the first page whose glyphs
                # pdfplumber could not read ended the document — and `out`, built outside
                # it, was returned as though it were the file. Measured on a clean
                # 10-page list publishing 50 lots with page 4 poisoned: 15 rows, from
                # pages 1-3, logged as "-> 15 listing(s)" like any success. The biggest
                # single source in this pipeline is a PDF (2,799 live rows), so the
                # difference between "this page failed" and "this file failed" is
                # thousands of listings.
                try:
                    annots = _annot_links(page)
                except Exception:                                       # noqa: BLE001
                    annots = []
                best, best_ctx, best_banner, best_name = [], context, banner, ""
                others, failed = [], 0
                for name, strategy in _PDF_STRATEGIES:
                    # PER STRATEGY, not per page. `lines` failing on a page whose text
                    # layer is broken must not cost the `tables` read of the same page.
                    try:
                        listings, ctx, ban = _rows_to_listings(
                            strategy(page, annots), context, source_label, builder_hint,
                            banner)
                    except Exception as e:                              # noqa: BLE001
                        failed += 1
                        logger.warning("pdf %s: page %s failed the %s strategy (%s)",
                                       source_label or "(file)",
                                       getattr(page, "page_number", "?"), name, e)
                        continue
                    if len(listings) > len(best):
                        if best:
                            others.append(best)
                        best, best_ctx, best_banner, best_name = listings, ctx, ban, name
                    elif listings:
                        others.append(listings)
                if failed == len(_PDF_STRATEGIES):
                    # Every way of reading this page failed. Say so — an unreadable page
                    # and a blank one both yield nothing, and only one is a problem.
                    hurt.append(getattr(page, "page_number", 0) or 0)
                    continue
                # UNION, NOT MAX. The strict `>` tie-break to `tables` is right — it
                # carries the columns map that `lines` cannot produce — but the loser
                # was thrown away whole, and a row only one strategy could read went
                # with it. Normally `lines` is a text superset of `tables` and the
                # union IS the max, so this adds nothing; it bites when a table's rows
                # split badly across baselines while parseable content sits outside it.
                # Deduped on source_text, keeping the winner's copy, because a naive
                # concatenation would double-count every row both strategies read.
                if others:
                    seen_text = {(_l.get("source_text") or "").strip() for _l in best}
                    for group in others:
                        for _l in group:
                            key = (_l.get("source_text") or "").strip()
                            if key and key not in seen_text:
                                seen_text.add(key)
                                best.append(_l)
                # Which PAGE the lot was read from. A price list URL alone opens a 30-page
                # PDF; 2,799 of the live rows come from one, far more than from any
                # spreadsheet, so this is the answer to Colin's ask for most of the stock.
                # page_number is pdfplumber's own 1-based value — the page the reader
                # shows, not an index.
                for _l in best:
                    _l["source_page"] = getattr(page, "page_number", 0) or 0
                out.extend(best)
                context, banner = best_ctx, best_banner
                if best_name:
                    used[best_name] = used.get(best_name, 0) + len(best)
    except Exception as e:
        # Now only ever a failure to OPEN the file, which really is fatal to it.
        logger.warning("could not read pdf %s: %s", source_label, e)
    if hurt:
        logger.warning("pdf %s: %d of %d page(s) could not be read: %s",
                       source_label or "(file)", len(hurt), min(pages_total, PDF_PAGE_CAP),
                       ", ".join(str(p) for p in hurt[:12]))
    # A CAP THAT BINDS IS A FACT ABOUT THE FILE, NOT A DETAIL. Silently reading 25 of 40
    # pages is the same shape of loss as everything else in this module.
    if pages_total > PDF_PAGE_CAP:
        logger.warning("pdf %s: %d pages, only the first %d read — pages %d+ are NOT in "
                       "this harvest.", source_label or "(file)", pages_total,
                       PDF_PAGE_CAP, PDF_PAGE_CAP + 1)
    if len(used) > 1:
        logger.info("pdf %s: mixed page layouts — %s", source_label or "(file)",
                    ", ".join(f"{k} {v}" for k, v in used.items()))
    _log_yield("pdf", source_label, out)
    return out


def extract_from_csv(data: bytes, source_label: str = "", builder_hint: str = "") -> List[Dict[str, Any]]:
    """CSV/TSV stocklists — what a Google Sheets or Smartsheet tab exports as."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        logger.warning("could not decode csv %s", source_label)
        return []
    sample = text[:4000]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    # One row at a time, like the xlsx path, so each listing can be matched back to the
    # cells it came from. Batching them lost that mapping, and with it any chance of
    # reading a fact out of the column its header names.
    out: List[Dict[str, Any]] = []
    context, banner = "", ""
    money = _MoneyColumns()
    # enumerate from 1: a CSV exported from Google Sheets lines up with the row numbers
    # the sheet itself shows, which is the number Colin will be looking at.
    for line_no, raw in enumerate(csv.reader(io.StringIO(text), delimiter=delimiter), 1):
        cells = [(str(v).strip(), False, i) for i, v in enumerate(raw) if str(v).strip()]
        # See the xlsx path: render before the header test, or a numeric price column
        # makes a lot row look like column names and wipes the money map.
        rendered = money.render(cells)
        if not rendered:
            continue
        is_header = _is_column_header(rendered)
        money.feed_header(cells, is_header)
        if is_header:
            continue
        listings, context, banner = _rows_to_listings(
            [(rendered, [])], context, source_label, builder_hint, banner)
        _apply_header_specs(listings, money.specs(cells))
        for _l in listings:
            _l["source_row"] = line_no
        out.extend(listings)
    _log_yield("csv", source_label, out)
    return out


def extract_stocklist(data: bytes, source_label: str = "", builder_hint: str = "") -> List[Dict[str, Any]]:
    """Dispatch on content. Magic bytes for the binary formats, then a text sniff — a
    Google Sheets or Smartsheet export arrives as CSV with no magic bytes at all."""
    if not data:
        return []
    if data[:2] == b"PK":
        return extract_from_xlsx(data, source_label, builder_hint)
    if data[:4] == b"%PDF":
        return extract_from_pdf(data, source_label, builder_hint)
    head = data[:600].lstrip()
    if head[:1] in (b"<",):
        # An HTML page where a file was expected is a sign-in wall or an error page,
        # never stock. Say so plainly rather than parsing a login form.
        logger.warning("stocklist %s returned an HTML page, not a file — the link most "
                       "likely needs a signed-in session.", source_label)
        return []
    if b"," in head or b"\t" in head:
        return extract_from_csv(data, source_label, builder_hint)
    logger.warning("unrecognised stocklist format for %s (starts %r)", source_label, data[:12])
    return []
