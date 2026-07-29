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

import io
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
    lot = fields.get("lot_number")
    if street:
        parts.append(_clean(street.group(0)))
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


def _is_group_header(text: str, fields: Dict[str, Any]) -> bool:
    """Estate/builder/region banner rather than a lot row."""
    if fields.get("advertised_package_price"):
        return False
    if LOT_TOKEN.search(text):
        return False
    # short-ish line, mostly words, few digits
    digits = sum(c.isdigit() for c in text)
    return bool(text) and len(text) < 90 and digits <= 4


def _context_suburb(header: str) -> Optional[str]:
    """'ʊ Aberdeen - Winter Valley - VIC - House & Land' -> 'Winter Valley' (the locality part)."""
    if not header:
        return None
    cleaned = re.sub(r"^[^A-Za-z0-9]+", "", header)
    bits = [b.strip() for b in cleaned.split("-") if b.strip()]
    # drop state codes and product types
    bits = [b for b in bits if b.upper() not in ("VIC", "NSW", "QLD", "SA", "WA", "NT", "ACT", "TAS")
            and not re.search(r"house|land|terrace|townhouse|apartment|dual|key", b, re.I)]
    return bits[1] if len(bits) > 1 else (bits[0] if bits else None)


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


def _iter_rows_xlsx(data: bytes) -> Iterator[Tuple[int, str, LinkList]]:
    """Yield (sheet index, row text, links). Cells are read as objects rather than
    values so `cell.hyperlink` survives — that is the per-lot link."""
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    formulas = {} if any(getattr(ws, "_hyperlinks", None) for ws in wb.worksheets) \
        else _formula_links(data)
    for si, sheet in enumerate(wb.worksheets):
        for row in sheet.iter_rows():
            parts: List[str] = []
            links: LinkList = []
            for c in row:
                s = str(c.value).strip() if c.value is not None else ""
                if s:
                    parts.append(s)
                url = getattr(getattr(c, "hyperlink", None), "target", None) \
                    or formulas.get((si, c.row, c.column))
                if url:
                    links.append((s, str(url)))
            text = _clean(" ".join(parts))
            if text or links:
                yield si, text, links


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


def _iter_rows_pdf(page: Any) -> Iterator[Tuple[str, LinkList]]:
    """Tables first (most stocklists are tabular), then plain text lines. Either way
    the row's vertical extent is kept so link annotations can be matched to it."""
    annots = _annot_links(page)
    emitted = False
    try:
        tables = page.find_tables() or []
    except Exception:
        tables = []
    for table in tables:
        try:
            values = table.extract()
        except Exception:
            continue
        for row_obj, cells in zip(getattr(table, "rows", []), values):
            text = _row_to_text(list(cells))
            bbox = getattr(row_obj, "bbox", None)
            links = _links_in_band(annots, bbox[1], bbox[3]) if bbox else []
            if text or links:
                emitted = True
                yield text, links
    if emitted:
        return
    try:
        lines = page.extract_text_lines() or []
    except Exception:
        lines = [{"text": l, "top": None, "bottom": None}
                 for l in (page.extract_text() or "").splitlines()]
    for ln in lines:
        text = _clean(ln.get("text", ""))
        if text:
            yield text, _links_in_band(annots, ln.get("top"), ln.get("bottom"))


# ------------------------------------------------------------------- extraction

def _listing_from_row(text: str, links: LinkList, context: str, source_label: str,
                      builder_hint: str) -> Optional[Dict[str, Any]]:
    """A parsed listing, or None if the row is not one."""
    fields = parse_fields(text)
    # availability/storey/lot/postcode/estate/incentives, from the FULL row
    fields.update({k: v for k, v in parse_listing_features(
        text, context, fields.get("advertised_package_price")).items()
        if v is not None})
    if not fields.get("advertised_package_price"):
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
        fields["suburb"] = _context_suburb(context)
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
    context, sheet = "", -1
    for si, text, links in rows:
        if si != sheet:
            context, sheet = "", si
        if not text:
            continue
        listing = _listing_from_row(text, links, context, source_label, builder_hint)
        if listing is None:
            if _is_group_header(text, {}):   # no price: _listing_from_row already told us
                context = text
            continue
        out.append(listing)
    logger.info("xlsx %s -> %d listing(s), %d with a per-lot link", source_label or "(file)",
                len(out), sum(1 for o in out if o.get("listing_url") or o.get("floorplan_url")
                              or o.get("brochure_url")))
    return out


def extract_from_pdf(data: bytes, source_label: str = "", builder_hint: str = "") -> List[Dict[str, Any]]:
    if not PDF_AVAILABLE:
        logger.error("pdfplumber not installed — cannot read pdf stocklists.")
        return []
    out: List[Dict[str, Any]] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            context = ""
            for page in pdf.pages[:25]:
                for text, links in _iter_rows_pdf(page):
                    listing = _listing_from_row(text, links, context, source_label, builder_hint)
                    if listing is None:
                        if _is_group_header(text, {}):
                            context = text
                        continue
                    out.append(listing)
    except Exception as e:
        logger.warning("could not read pdf %s: %s", source_label, e)
    logger.info("pdf %s -> %d listing(s), %d with a per-lot link", source_label or "(file)",
                len(out), sum(1 for o in out if o.get("listing_url") or o.get("floorplan_url")
                              or o.get("brochure_url")))
    return out


def extract_stocklist(data: bytes, source_label: str = "", builder_hint: str = "") -> List[Dict[str, Any]]:
    """Dispatch on file magic bytes."""
    if data[:2] == b"PK":
        return extract_from_xlsx(data, source_label, builder_hint)
    if data[:4] == b"%PDF":
        return extract_from_pdf(data, source_label, builder_hint)
    logger.warning("unrecognised stocklist format for %s", source_label)
    return []
