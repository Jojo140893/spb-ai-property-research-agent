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

Nothing is invented: a row without a recognisable price is skipped, and fields
that cannot be found stay None.
"""

import io
import logging
import re
from typing import Any, Dict, List, Optional

from sources.adaptive_extract import parse_fields, _clean

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


def extract_from_xlsx(data: bytes, source_label: str = "", builder_hint: str = "") -> List[Dict[str, Any]]:
    if not XLSX_AVAILABLE:
        logger.error("openpyxl not installed — cannot read xlsx stocklists.")
        return []
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception as e:
        logger.warning("could not open xlsx %s: %s", source_label, e)
        return []

    out: List[Dict[str, Any]] = []
    for sheet in wb.worksheets:
        context = ""
        for row in sheet.iter_rows(values_only=True):
            text = _row_to_text(list(row))
            if not text:
                continue
            fields = parse_fields(text)
            if _is_group_header(text, fields):
                context = text
                continue
            if not fields.get("advertised_package_price"):
                continue
            if not fields.get("lot_address"):
                fields["lot_address"] = text[:110]
            if not fields.get("suburb"):
                fields["suburb"] = _context_suburb(context)
            filled = sum(1 for k in ("bedrooms", "land_size_sqm", "suburb", "house_size_sqm") if fields.get(k))
            out.append({
                **fields,
                "builder_name": builder_hint,
                "estate_context": _clean(context)[:110],
                "source_url_or_ref": source_label,
                "extraction_confidence": round(min(1.0, 0.5 + 0.12 * filled), 2),
            })
    logger.info("xlsx %s -> %d listing(s)", source_label or "(file)", len(out))
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
                # tables first (most stocklists are tabular), then plain lines
                rows: List[str] = []
                try:
                    for table in (page.extract_tables() or []):
                        for r in table:
                            rows.append(_row_to_text(list(r)))
                except Exception:
                    pass
                if not rows:
                    rows = [_clean(l) for l in (page.extract_text() or "").splitlines()]
                for text in rows:
                    if not text:
                        continue
                    fields = parse_fields(text)
                    if _is_group_header(text, fields):
                        context = text
                        continue
                    if not fields.get("advertised_package_price"):
                        continue
                    if not fields.get("lot_address"):
                        fields["lot_address"] = text[:110]
                    if not fields.get("suburb"):
                        fields["suburb"] = _context_suburb(context)
                    filled = sum(1 for k in ("bedrooms", "land_size_sqm", "suburb", "house_size_sqm") if fields.get(k))
                    out.append({
                        **fields,
                        "builder_name": builder_hint,
                        "estate_context": _clean(context)[:110],
                        "source_url_or_ref": source_label,
                        "extraction_confidence": round(min(1.0, 0.5 + 0.12 * filled), 2),
                    })
    except Exception as e:
        logger.warning("could not read pdf %s: %s", source_label, e)
    logger.info("pdf %s -> %d listing(s)", source_label or "(file)", len(out))
    return out


def extract_stocklist(data: bytes, source_label: str = "", builder_hint: str = "") -> List[Dict[str, Any]]:
    """Dispatch on file magic bytes."""
    if data[:2] == b"PK":
        return extract_from_xlsx(data, source_label, builder_hint)
    if data[:4] == b"%PDF":
        return extract_from_pdf(data, source_label, builder_hint)
    logger.warning("unrecognised stocklist format for %s", source_label)
    return []
