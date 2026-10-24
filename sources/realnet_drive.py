"""
RealNet's stock folder on Google Drive: WA (and a few Tasmanian) house-and-land packages.

Coleen, 24 Oct 2026: *"can we add these WA properties from E-Agent. They are all in the
drive here..."* -- a Google Drive folder titled "RealNet Stocklist", kept by RealNet
Projects (realnet.net.au), one PDF per package. Until then the app held no WA stock except
Proxima's Lancelin lots.

WHY THE FOLDER AND NOT AN E-AGENT PAGE. E-Agent's category pages are read every night, and
reading the lists there would avoid a second copy. But E-Agent publishes no WA page: its
site's own page table (35 pages, read 24 Oct 2026) has house-and-land, townhouse, apartment,
land and commercial pages for VIC, QLD, NSW, SA and ACT, and nothing for WA. None of these
suburbs was in the catalogue either, so reading the folder double-counts nothing.

NO LOGIN. The folder is shared "anyone with the link", and Drive's embedded folder view
lists it to an anonymous request, like the Google Sheets remote_stocklist reads. The runner
needs no Google account. A folder whose sharing is tightened answers with a sign-in page,
which is reported as unreadable and never parsed.

ONE LISTING PER FILE NAME, BECAUSE THE NAME IS THE ONE PART THAT IS NEVER STALE. Each name
is RealNet's own summary of its package:

    DUAL (3+2), Ravenswood Waters Estate, Ravenswood, BG - $975k.pdf
    H+L (4x2), Haynes Park, Haynes, JM - $864k.pdf
    H+L (3x2), Bayview Subdivision, Rokeby, TASMANIA, CD - $749k.pdf

-- product and bedrooms, estate or street, suburb, the state when it is not WA, a builder
code, and the price. The PDFs are made from templates, and their text layers carry what an
earlier version of the template said: every JM and MC brochure opens with a hidden $879,000
"Panthenon" package at Haynes Park before its own, and the Echo Beach price table's text
says units 1, 5 and 8 are available where the page shows units 7 and 8. So a brochure's
detail (land size and price, build size and price, design, title, lot) is taken ONLY from
the block that starts at the price its file name states, and only when that block names the
same suburb. A file whose name states no price (the two SMSF Echo Beach copies) is not a
listing; a file whose details cannot be matched still is, on its name alone.

THE BUILDER IS NOT NAMED. RealNet sells for builders it identifies only by a code in the
file name (BG, DG, JM, MC, CD); no brochure prints a company. builder_name stays blank (the
search shows "Builder not identified in source") and the code is kept in the row's text, so
a consultant can ask RealNet. A wrong builder is worse than a blank.

AVAILABILITY. The brochures never state it: RealNet lists what it is selling and removes a
file when it goes. availability_status is therefore left blank, and the search treats the
folder as a list that never states availability ("available by the sheet", flagged on the
card), exactly as it treats a price list with no status column.

THE FOLDER IS THE LIST. source_url is the folder, shared by every row, so repair_rows sees
one re-listing document: a package whose file leaves the folder is retired after
WITHDRAWN_AFTER_DAYS, and a half-read folder (partial_reads) retires nothing. listing_url
is the package's own file, which the card links as its PDF and link_check.py asks every
night: a deleted file answers 404 and the package leaves the search the next morning.

STATE. WA unless the file name names another state (RealNet writes "TASMANIA" on its
Tasmanian files and nothing on its WA ones; every WA brochure names WA or Perth). Given as
the row's state and as its source_state_hint, the slot E-Agent's page state uses. No
postcode is taken from a brochure: one prints "Ravenswood WA 6107", and Ravenswood is 6208.
"""

import html
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from database import building_content_hash

logger = logging.getLogger("spb.realnet")

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:  # pragma: no cover
    REQUESTS_AVAILABLE = False

CHANNEL = "RealNet Stocklist"
# The folder Coleen sent on 24 Oct 2026. SPB_REALNET_FOLDER overrides it (an id or a link)
# if RealNet moves its stock to another folder; "off" switches the source off.
FOLDER_ID = "113X94kcwbypLLEnmdGPVBHOg9nKd6hrO"
FOLDER_ENV = "SPB_REALNET_FOLDER"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
LIST_URL = "https://drive.google.com/embeddedfolderview?id=%s"
FOLDER_URL = "https://drive.google.com/drive/folders/%s"
FILE_URL = "https://drive.google.com/file/d/%s/view"
DOWNLOAD_URL = "https://drive.usercontent.google.com/download?id=%s&export=download&confirm=t"
# The largest brochure on 24 Oct was 10 MB (a 15-page development brochure). Bigger than
# this is not a package flyer, and is not downloaded.
MAX_FILE_BYTES = 30 * 1024 * 1024

_STATE_NAMES = {
    "WA": "WA", "WESTERN AUSTRALIA": "WA", "TAS": "TAS", "TASMANIA": "TAS",
    "VIC": "VIC", "VICTORIA": "VIC", "NSW": "NSW", "NEW SOUTH WALES": "NSW",
    "QLD": "QLD", "QUEENSLAND": "QLD", "SA": "SA", "SOUTH AUSTRALIA": "SA",
    "NT": "NT", "NORTHERN TERRITORY": "NT", "ACT": "ACT",
}
# Everything RealNet has named a file with, read off the folder on 24 Oct 2026.
_NAME = re.compile(
    r"^\s*(?P<smsf>SMSF\s+)?(?P<kind>DUAL|TRIPLE|H\s*\+\s*L|H\s*&\s*L)\s*"
    r"\(\s*(?P<config>\d+(?:\s*[x+]\s*\d+)*)\s*\)\s*,\s*(?P<rest>.+?)\s*$", re.I)
_NAME_PRICE = re.compile(r"\s*-\s*\$\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>[kKmM])?\s*$")
_DOC_WORD = re.compile(r"\s*-\s*(?P<word>brochure|price\s*-?\s*list)\s*$", re.I)
_BUILDER_CODE = re.compile(r"^[A-Z]{2,3}$")


def folder_id_from(value: str) -> str:
    """An id, or the id inside a folder link."""
    value = (value or "").strip()
    m = re.search(r"/folders/([\w-]+)", value) or re.search(r"[?&]id=([\w-]+)", value)
    return m.group(1) if m else value


def name_price(num: str, unit: str) -> Optional[float]:
    """'$1.068M' -> 1068000.0, '$864k' -> 864000.0, '$749,000' -> 749000.0."""
    try:
        v = float(num.replace(",", ""))
    except ValueError:
        return None
    unit = (unit or "").lower()
    if unit == "k":
        v *= 1_000
    elif unit == "m":
        v *= 1_000_000
    v = float(round(v))                       # 1.021 * 1e6 is 1020999.9999999999
    return v if 50_000 <= v <= 20_000_000 else None


def parse_file_name(name: str) -> Optional[Dict[str, Any]]:
    """What a RealNet file name says, or None when it is not a package name with a price.

    The price in a name is cut, not rounded ("$1,068,490" is "$1.068M"), so it names the
    thousand the package price starts with -- see price_matches.
    """
    stem = re.sub(r"\.pdf\s*$", "", name or "", flags=re.I).strip()
    m = _NAME_PRICE.search(stem)
    if not m:
        return None
    price = name_price(m.group("num"), m.group("unit"))
    if price is None:
        return None
    stem = stem[:m.start()]
    doc_word = ""
    d = _DOC_WORD.search(stem)
    if d:
        doc_word = re.sub(r"\W", "", d.group("word")).lower()
        stem = stem[:d.start()]
    head = _NAME.match(stem)
    if not head:
        return None
    parts = [p.strip() for p in head.group("rest").split(",") if p.strip()]
    code = ""
    if parts and _BUILDER_CODE.match(parts[-1]) and parts[-1] not in _STATE_NAMES:
        code = parts.pop()
    state, state_named = "WA", False
    for i, p in enumerate(parts):
        if p.upper() in _STATE_NAMES:
            state, state_named = _STATE_NAMES[p.upper()], True
            del parts[i]
            break
    if not parts:
        return None
    suburb = parts[-1]
    place = ", ".join(parts[:-1])
    kind = re.sub(r"\s", "", head.group("kind")).upper().replace("&", "+")
    config = re.sub(r"\s", "", head.group("config")).lower()
    return {
        "kind": kind,                       # DUAL | TRIPLE | H+L
        "smsf": bool(head.group("smsf")),
        "config": config,                   # "3+2", "4x2", "2+1+1", "2x1"
        "place": place,                     # estate or street; may be ""
        "suburb": suburb,
        "state": state,
        "state_named": state_named,
        "builder_code": code,
        "price": price,
        "doc_word": doc_word,               # "brochure" | "pricelist" | ""
        # The name with its price taken out: what stays the same when the price moves.
        "identity": re.sub(r"\s+", " ", stem).strip(),
    }


def rooms_from_config(kind: str, config: str) -> Tuple[Optional[int], Optional[int]]:
    """(bedrooms, bathrooms) the name states, for the MAIN dwelling.

    "H+L (4x2)" is 4 bed 2 bath. "DUAL (3+2)" is two dwellings, of 3 and 2 bedrooms, and
    stores 3 -- the dual-living rows already in the catalogue store the main dwelling the
    same way ("3+2 2+1 1+1" is 3 bedrooms). "DUAL (2x1)" is two 1-bedroom units ("2 UNIT
    COMPLEX" on its brochure). A dual's bathrooms are not in its name.
    """
    nums = [int(n) for n in re.findall(r"\d+", config or "")]
    if not nums:
        return None, None
    if kind == "H+L":
        return nums[0], (nums[1] if len(nums) > 1 and "x" in config else None)
    if "x" in config and len(nums) == 2:
        return nums[1], None
    return nums[0], None


# --------------------------------------------------------------------- brochure text

_MONEY = re.compile(r"\$\s*(\d{1,3}(?:,\d{3})+|\d{4,})(?:\.\d{2})?(?![\d,])")
_BARE_MONEY = re.compile(r"^\s*\$?\s*(\d{1,3}(?:,\d{3})+|\d{5,})(?:\.\d{2})?\s*\*?\s*$")
_SIZE = re.compile(r"(\d{2,5}(?:[.,]\d{1,3})?)\s*(?:m2|m²|sqm|sq\.?\s*m)\b", re.I)
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_TITLE_VALUE = re.compile(
    r"(?i)^(yes\b.*|no\b.*|registered|titled|tbc|tba|"
    r"((early|mid|late)\s+)?%s(\s*'?\s*\d{2,4})?|q[1-4]\s*'?\s*\d{2,4}|20\d\d)$" % _MONTH)

# Every label RealNet's five templates print, and the kind of value it takes. Only the
# named fields are kept. The others are listed because some templates print a run of
# labels and then a run of values (CD: "House Area / Build Price / Land Area / Land Price /
# Est. Rent P/W", then "132 m2 / $391,000 / 483 m2 / $358,000 / $650-$670"), and the values
# can only be lined up with labels if every label in the run is known.
_LABELS = tuple((field, re.compile(r"^(?:%s)\b\s*(?:\([^)]*\))?\s*:?\s*" % rx, re.I), kind)
                for field, rx, kind in (
    ("land_sqm", r"land\s*(size|area)", "size"),
    ("house_sqm", r"build(ing)?\s*size|house\s*area", "size"),
    ("land_price", r"land\s*price", "money"),
    ("build_price", r"build(ing)?\s*price", "money"),
    ("suburb", r"suburb", "place"),
    ("estate", r"estate|location", "place"),
    ("house_design", r"house\s*design", "place"),
    ("title", r"titles?\s*registered|titled\s*date|title\s*date", "title"),
    ("rooms", r"bed\s*/\s*bath\s*/\s*garage", "rooms"),
    ("", r"(land|build(ing)?)\s*deposit", "money"),
    ("", r"est(imated|\.)?\s*build(ing)?\s*time", "duration"),
    ("", r"rental\s*estimate|est\.?\s*rent", "rent"),
    ("", r"rental\s*yield", "pct"),
    ("", r"strata\s*levies", "any"),
))
# Kinds whose values cannot be mistaken for another kind's: a line that is not the next
# label's value is offered to the first waiting label of one of these.
_DISTINCT = ("size", "money", "title", "duration", "pct", "rooms")
# A lot line: "Lot 2071 Elevator Street, Brabham", "LOT 38 BAYVIEW SUBDIVISION, ROKEBY
# 7019", "UNIT 2 / 116 DOLINA DRIVE, ROKEBY", "UNITS 1-2 / LOT 21 DERWENT LINKS, ...".
_LOT_LINE = re.compile(r"^(?:units?\s+[\d\-]+\s*/\s*)?(?:lot\s+(?P<lot>\d+[A-Z]?)|unit\s+\d+)\b.{3,}",
                       re.I)


def _money(text: str, bare: bool = False) -> Optional[float]:
    """A dollar amount; with `bare`, also "350,000" alone (a DG land price has no $)."""
    m = _MONEY.search(text or "") or (_BARE_MONEY.match(text or "") if bare else None)
    return float(m.group(1).replace(",", "")) if m else None


def _size(text: str) -> Optional[float]:
    m = _SIZE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _clean_cell(value: str) -> str:
    """One cell, as text. The templates print a curly apostrophe ("January '27") that
    some PDF fonts give back as U+FFFD."""
    v = value or ""
    for ch in ("�", "’", "‘", "\x92"):
        v = v.replace(ch, "'")
    v = v.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", v).strip(" :\t")


def _place_value(value: str) -> bool:
    """A suburb, estate or design cell, not a sentence, a size, a price or a label."""
    v = value.strip()
    return (0 < len(v) <= 45 and not v[:1].islower() and len(v.split()) <= 7
            and not _SIZE.search(v) and not _MONEY.search(v) and not _label(v))


def _value(kind: str, text: str) -> Any:
    """`text` read as a value of `kind`, or None."""
    t = _clean_cell(text)
    if not t:
        return None
    if kind == "size":
        return _size(t)
    if kind == "money":
        return _money(t, bare=True)
    if kind == "place":
        return t if _place_value(t) else None
    if kind == "title":
        return t if _TITLE_VALUE.match(t) else None
    if kind == "rooms":
        return t if re.search(r"\d\s*x\s*\d", t, re.I) else None
    if kind == "duration":
        return t if re.match(r"(?i)\d+\s*(months?|mths?|weeks?|wks?)\b", t) else None
    if kind == "rent":
        return t if "$" in t else None
    if kind == "pct":
        return t if re.fullmatch(r"\d+(\.\d+)?\s*%", t) else None
    return t


def _label(line: str) -> Optional[Tuple[str, str, str]]:
    """(field, kind, the text after the label) when `line` is a label."""
    for field, rx, kind in _LABELS:
        m = rx.match(line or "")
        if m:
            return field, kind, line[m.end():]
    return None


def _read_fields(lines: List[str]) -> Dict[str, Any]:
    """The labelled values in one package's lines.

    A value follows its label on the same line ("LAND SIZE:  375m2"), on the next line,
    or -- where a template prints a run of labels -- in the same order in a run after it.
    A label that comes after a value line starts afresh: a label still waiting by then
    printed no value ("LAND PRICE / INCLUDED").
    """
    out: Dict[str, Any] = {}
    queue: List[Tuple[str, str]] = []
    prev_label = False
    for line in lines:
        if not line.strip():
            continue
        lab = _label(line)
        if lab:
            field, kind, rest = lab
            if not prev_label:
                queue = []
            prev_label = True
            v = _value(kind, rest)
            if v is not None:
                if field:
                    out.setdefault(field, v)
                continue
            # "Titles Registered." on a price list is the statement itself.
            if kind == "title" and re.fullmatch(r"(?i)titles?\s+registered\s*\.", line.strip()):
                out.setdefault(field, "Yes")
                continue
            queue.append((field, kind))
            continue
        prev_label = False
        if not queue:
            continue
        field, kind = queue[0]
        v = _value(kind, line)
        if v is not None:
            queue.pop(0)
        else:
            # Not the next label's value. MC prints "TITLE REGISTERED / ESTIMATED BUILD
            # TIME / 10 Months / Registered": "10 Months" belongs to the second.
            for n, (f2, k2) in enumerate(queue[1:], 1):
                if k2 in _DISTINCT and _value(k2, line) is not None:
                    field, kind, v = f2, k2, _value(k2, line)
                    queue.pop(n)
                    break
        if v is not None and field:
            out.setdefault(field, v)
    return out


def price_matches(found: float, named: float) -> bool:
    """True when a package price printed in a brochure is the one its file name states.

    Names cut the price to the thousand ($864,360 is "$864k", $1,068,490 is "$1.068M"),
    so the printed price must sit in [named, named + 1,000). $1 of slack below covers a
    name that rounded instead.
    """
    return named - 1 <= found < named + 1_000


def _norm_place(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def _labelled_money(lines: List[str], i: int) -> bool:
    """True when the money on line i is a land, build, rent or deposit figure."""
    before = next((lines[j] for j in range(i - 1, max(-1, i - 3), -1) if lines[j].strip()), "")
    return bool(re.search(r"(land|build(ing)?)\s*price|deposit|rent", before + " " + lines[i],
                          re.I))


def find_price(lines: List[str], named_price: float) -> Optional[Tuple[int, float]]:
    """(line, price) where the brochure prints the package price its name states."""
    for i, line in enumerate(lines):
        v = _money(line)
        if v is not None and price_matches(v, named_price) and not _labelled_money(lines, i):
            return i, v
    return None


def _headline_price(lines: List[str], i: int, floor: float) -> bool:
    """True when line i prints a package price: at least `floor`, and not a labelled
    land, build, rent or deposit figure."""
    v = _money(lines[i])
    return v is not None and v >= floor and not _labelled_money(lines, i)


def field_groups(lines: List[str]) -> List[Tuple[int, int]]:
    """(first, last) label line of each run of labels that describes ONE package.

    A label printed a second time starts the next package: the JM and MC templates carry
    a hidden earlier package ($879,000, 604 m2, "The Panthenon") before their own.
    """
    groups, seen, first, last, last_field = [], set(), None, None, ""
    for i, line in enumerate(lines):
        lab = _label(line)
        f = lab[0] if lab else ""
        if not f:
            continue
        # "SUBURB" printed twice in a row (the MC triple-key brochure) is one label.
        if f == last_field and last is not None and not any(lines[last + 1:i]):
            last = i
            continue
        if f in seen:
            groups.append((first, last))
            seen, first = set(), i
        if first is None:
            first = i
        seen.add(f)
        last, last_field = i, f
    if first is not None:
        groups.append((first, last))
    return groups


def lot_line_of(lines: List[str], suburb: str) -> Tuple[str, str]:
    """(line, lot number) when the brochure prints exactly one lot or unit address, in the
    named suburb."""
    found = {}
    for line in lines:
        m = _LOT_LINE.match(line)
        if m and _norm_place(suburb) in _norm_place(line):
            found.setdefault(_norm_place(line), (_clean_cell(line), m.group("lot") or ""))
    if len(found) != 1:
        return "", ""
    return next(iter(found.values()))


def parse_brochure(pages: List[str], named: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """(details, why). details carries "price", the package price exactly as printed, and
    the fields of the package printed at it. Empty, with a reason, when the brochure does
    not print the price its name states.

    The package is the run of labels on the price's own side. A template prints its price
    above its fields (DG, JM, MC, most BG) or below them (BG street lots); where a page
    holds one run there is no choice to make, and where it holds two, the run that starts
    after the price is the one it heads -- unless another package price stands between.
    """
    lines = [ln.strip() for ln in "\n".join(pages).splitlines()]
    hit = find_price(lines, named["price"])
    if hit is None:
        return {}, "it does not print the price its name states"
    at, printed = hit
    groups = field_groups(lines)
    floor = named["price"] * 0.6
    heads = [i for i in range(len(lines)) if i != at and _headline_price(lines, i, floor)]

    def clear(a: int, b: int) -> bool:
        return not any(a < h < b for h in heads)

    pick = None
    inside = [n for n, (f, l) in enumerate(groups) if f < at < l]
    if len(groups) == 1:
        pick = 0
    elif inside:
        # DG prints "Package Price: $810,522" between its land and build details.
        pick = inside[0]
    else:
        after = [n for n, (f, _l) in enumerate(groups) if f > at and clear(at, f)]
        before = [n for n, (_f, l) in enumerate(groups) if l < at and clear(l, at)]
        pick = after[0] if after else (before[-1] if before else None)
    out: Dict[str, Any] = {"price": printed}
    if pick is not None:
        first = groups[pick][0]
        end = groups[pick + 1][0] if pick + 1 < len(groups) else len(lines)
        out.update(_read_fields(lines[first:end]))
    line, lot = lot_line_of(lines, named["suburb"])
    if line:
        out["lot_line"] = line
        if lot:
            out["lot_number"] = lot
    said = out.get("suburb", "")
    if said and _norm_place(named["suburb"]) not in _norm_place(said):
        return {"price": printed}, f"its fields name the suburb {said!r}"
    return out, ""


def checked_details(d: Dict[str, Any], price: float) -> Dict[str, Any]:
    """Only the values that agree with the package and with each other."""
    out = dict(d)
    for f in ("land_price", "build_price"):
        v = out.get(f)
        if v is not None and not (20_000 <= v < price):
            out.pop(f)
    lp, bp = out.get("land_price"), out.get("build_price")
    if lp and bp and abs(lp + bp - price) > 0.03 * price:
        out.pop("land_price")
        out.pop("build_price")
    v = out.get("land_sqm")
    if v is not None and not (80 <= v <= 5_000):
        out.pop("land_sqm")
    v = out.get("house_sqm")
    if v is not None and not (40 <= v <= 700):
        out.pop("house_sqm")
    return out


def _pretty(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip(" ,")
    return s.title() if s.isupper() else s


def _rooms_stated(value: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """'4x2x2' -> (4, 2, 2); 'MAIN: 2x1  GUEST: 1x1' and 'UNIT A 2x1, UNIT B 1x1, ...'
    -> (2, 1, None): the main dwelling, as rooms_from_config counts it."""
    v = value or ""
    m = re.search(r"(?:main|unit\s*a)\s*:?\s*(\d)\s*x\s*(\d)", v, re.I)
    if m:
        return int(m.group(1)), int(m.group(2)), None
    m = re.fullmatch(r"\s*(\d)\s*x\s*(\d)(?:\s*x\s*(\d))?\s*", v)
    if m:
        return int(m.group(1)), int(m.group(2)), (int(m.group(3)) if m.group(3) else None)
    return None, None, None


def _title_status(value: str) -> str:
    """'Yes' / 'YES (READY TO BUILD)' -> 'Titled'; 'AUGUST' -> 'Titles expected August'."""
    v = _clean_cell(value)
    if not v:
        return ""
    if re.match(r"(?i)(yes|titled|registered)\b", v):
        return "Titled"
    if re.match(r"(?i)(no|tbc|tba)\b", v):
        return ""
    return "Titles expected " + (v.title() if v.isupper() else v)


_PRODUCT_WORDS = {"DUAL": "Dual key", "TRIPLE": "Triple key", "H+L": "House and land"}
_STREET = re.compile(r"(?i)^\s*(u(nit)?\s*)?\d*[a-z]?\s*\b[\w' ]*\b(road|rd|street|st|way|drive|dr|"
                     r"avenue|ave|parade|pde|crescent|cres|court|ct|place|pl|lane|ln|loop|"
                     r"close|terrace|tce|boulevard|blvd|circuit|cct|approach|grove|highway|hwy)\s*$")


def listing_from(entry: Dict[str, Any], named: Dict[str, Any], details: Dict[str, Any],
                 folder_url: str, identity: str, today: str,
                 shares_name: bool = False) -> Dict[str, Any]:
    """The row for one package. `details` is what parse_brochure read from the file that
    printed the named price; its "price" is the package price to the dollar."""
    price = float(details.get("price") or named["price"])
    d = checked_details({k: v for k, v in details.items() if k != "price"}, price)
    # The page outranks the name when it states the rooms: "DUAL (4+2), Atlantis Beach"
    # prints "MAIN: 3x2  GUEST: 2x1" at the price its name states.
    beds, baths = rooms_from_config(named["kind"], named["config"])
    s_beds, s_baths, cars = _rooms_stated(d.get("rooms", ""))
    if s_beds is not None:
        beds, baths = s_beds, s_baths
    state = named["state"]
    suburb = _pretty(named["suburb"])
    estate = _pretty(d.get("estate") or "") or None
    place = _pretty(named["place"]) or None
    # BG prints the street under LOCATION when the lot is not in an estate ("5A KALLANG
    # ROAD"): a street is not an estate.
    if estate and _STREET.search(estate):
        estate = None
    # The name's place, where it is not a street, is the estate as RealNet names it.
    if place and not _STREET.search(place):
        estate = place
    lot_line = d.get("lot_line")
    if lot_line:
        # "UNITS 1-2 / LOT 21 DERWENT LINKS, NEW NORFOLK TAS 7140" -> "Units 1-2 / Lot 21
        # Derwent Links": the place goes after it, once, from the name.
        lot_line = re.sub(r"\s+\d{4}\s*$", "", lot_line)
        lot_line = re.sub(r"(?i)[\s,]+(WA|TAS|VIC|NSW|QLD|SA|NT|ACT)\s*$", "", lot_line)
        lot_line = re.sub(r"(?i)[\s,]+%s\s*$" % re.escape(named["suburb"]), "", lot_line)
        lot_line = _pretty(lot_line.strip(" ,")) or None
    product = _PRODUCT_WORDS.get(named["kind"], named["kind"])
    if named["smsf"]:
        product = "SMSF " + product
    product_type = "House & Land"
    if re.match(r"(?i)^u(nit)?\s*\d", named["place"] or ""):
        product_type = "Townhouse"
    # The card's label: the lot where the brochure names one, else the street or estate
    # the name gives, then the place. The dwelling configuration rides in front, and two
    # files whose names differ only by price ("Oyster Harbour, Albany, MC -$955k" and
    # "-$975k") carry it too: supersede_duplicates takes two live rows with one label,
    # place and spec for two captures of one lot, and would retire one of the packages.
    where = lot_line or place or ""
    label = ", ".join(x for x in (
        "%s %s" % (product, named["config"]), where, "%s %s" % (suburb, state)) if x)
    if shares_name and not lot_line:
        label += " - $%s package" % format(int(named["price"]), ",")
    design = _pretty(d.get("house_design") or "") or None
    title = _title_status(d.get("title", ""))
    code = named["builder_code"]
    # The row's own line, in the order the Stocklist reader uses: stable facts first,
    # the locality next, money last (database._variant_key strips the money).
    bits = [product, "(%s)" % named["config"], where or None, estate if estate != where else None,
            design, ("land %s m2" % _num(d["land_sqm"])) if d.get("land_sqm") else None,
            ("house %s m2" % _num(d["house_sqm"])) if d.get("house_sqm") else None,
            title or None, "%s %s" % (suburb, state),
            ("RealNet builder code %s" % code) if code else None,
            ("land $%s" % format(int(d["land_price"]), ",")) if d.get("land_price") else None,
            ("build $%s" % format(int(d["build_price"]), ",")) if d.get("build_price") else None,
            "$%s" % format(int(price), ",")]
    source_text = " | ".join(str(b) for b in bits if b)
    row = {
        "source_channel": CHANNEL,
        "lot_address": label[:120],
        "suburb": suburb,
        "state": state,
        "postcode": None,
        "lot_number": d.get("lot_number"),
        "estate_name": estate,
        "builder_name": "",
        "builder_source": None,
        "attribution_scope": "project",
        "house_design": design,
        "advertised_package_price": float(price),
        "land_price": d.get("land_price"),
        "build_price": d.get("build_price"),
        "bedrooms": beds,
        "bathrooms": baths,
        "car_spaces": cars,
        "land_size_sqm": d.get("land_sqm"),
        "house_size_sqm": d.get("house_sqm"),
        "product_type": product_type,
        "title_status": title,
        "availability_status": None,
        "listing_url": FILE_URL % entry["id"],
        "source_state_hint": state,
        "source_url_or_ref": folder_url,
        "stocklist_file": folder_url,
        "source_text": source_text,
        "date_checked": today,
        "extraction_confidence": 1.0 if d else 0.6,
        "verified": False,
    }
    # IDENTITY FROM THE NAME AND THE FILE, NOT THE PARSE (`identity`, see search). The
    # default identity mixes in land size and design, so a night that read a brochure
    # differently would give the same package a second row.
    row["content_hash"] = building_content_hash({
        "source_channel": CHANNEL, "attribution_scope": "project",
        "suburb": suburb, "source_text": identity})
    return row


def _num(v: float) -> str:
    return ("%.2f" % v).rstrip("0").rstrip(".")


# ------------------------------------------------------------------------ the source

class RealNetDriveSource:
    """Reads the folder. `state` is ok | off | unreachable | not_shared | error, `why` says
    it in words, and `files` holds one (name, outcome, printed, why) per file."""

    def __init__(self, folder: Optional[str] = None, session: Any = None):
        import os
        raw = folder if folder is not None else os.environ.get(FOLDER_ENV, "")
        self.off = raw.strip().lower() == "off"
        self.folder_id = folder_id_from(raw) if raw.strip() and not self.off else FOLDER_ID
        self.folder_url = FOLDER_URL % self.folder_id
        self.sess = session
        self.state, self.why = "", ""
        self.files: List[Tuple[str, str, int, str]] = []
        self.listed = 0

    # -- network -------------------------------------------------------------------------
    def _session(self):
        if self.sess is None:
            self.sess = requests.Session()
            self.sess.headers.update({"User-Agent": UA})
        return self.sess

    def list_folder(self) -> List[Dict[str, str]]:
        """[{id, name, kind}] for every entry, or [] with state/why set."""
        try:
            r = self._session().get(LIST_URL % self.folder_id, timeout=60)
        except Exception as e:                                         # noqa: BLE001
            self.state, self.why = "unreachable", f"folder listing failed: {e}"
            return []
        body = r.text if r.status_code == 200 else ""
        # A folder no longer shared by link answers with Google's sign-in page.
        if r.status_code != 200 or ("flip-entry" not in body
                                    and "accounts.google" in (r.url or "")):
            self.state = "not_shared"
            self.why = (f"the folder answered HTTP {r.status_code} -- its sharing was "
                        "probably tightened. Ask RealNet to keep it 'anyone with the link'.")
            return []
        if 'class="flip-entries"' not in body and "flip-entry" not in body:
            self.state, self.why = "not_shared", ("the folder page lists no entries and no "
                                                  "folder view -- not a shared folder")
            return []
        return parse_listing(body)

    def _download(self, file_id: str) -> Tuple[Optional[bytes], str]:
        try:
            r = self._session().get(DOWNLOAD_URL % file_id, timeout=120, stream=True)
        except Exception as e:                                         # noqa: BLE001
            return None, f"download failed: {e}"
        try:
            if r.status_code != 200:
                return None, f"HTTP {r.status_code}"
            chunks, size = [], 0
            for chunk in r.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    return None, f"over {MAX_FILE_BYTES // (1024 * 1024)} MB"
                chunks.append(chunk)
            data = b"".join(chunks)
        finally:
            r.close()
        if not data.startswith(b"%PDF"):
            return None, "not a PDF (Drive answered with a page)"
        return data, ""

    # -- the read ------------------------------------------------------------------------
    def search(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        if self.off:
            self.state, self.why = "off", f"{FOLDER_ENV}=off"
            return []
        if not REQUESTS_AVAILABLE:
            self.state, self.why = "error", "requests is not installed"
            return []
        entries = self.list_folder()
        if not entries:
            if not self.state:
                self.state, self.why = "error", "the folder listing came back empty"
            logger.warning("RealNet: %s", self.why)
            return []
        self.listed = len(entries)
        today = datetime.now().strftime("%d/%m/%Y")
        named_files = []
        for e in entries:
            if e["kind"] != "file":
                self.files.append((e["name"], "not a price list", 0, "a sub-folder"))
                continue
            if not e["name"].lower().endswith(".pdf"):
                self.files.append((e["name"], "not a price list", 0, "not a PDF"))
                continue
            named = parse_file_name(e["name"])
            if named is None:
                self.files.append((e["name"], "no price list", 0,
                                   "its name states no package and price"))
                continue
            named_files.append((e, named))
        # One package can have two files -- a brochure and its price list, one name and one
        # price ("Echo Beach, Mandurah - Brochure - $760k" and "- Pricelist - $760k"). It
        # is one listing, read from whichever file prints the price.
        by_key: Dict[Tuple[str, float], List] = {}
        for e, named in named_files:
            by_key.setdefault((_identity_key(named["identity"]), named["price"]),
                              []).append((e, named))
        name_count: Dict[str, int] = {}
        for ident, _price in by_key:
            name_count[ident] = name_count.get(ident, 0) + 1
        out = []
        for (ident, _price), files in sorted(by_key.items(), key=lambda kv: kv[1][0][0]["name"]):
            files.sort(key=lambda en: (en[1]["doc_word"] != "brochure", en[0]["name"]))
            chosen, problems = None, []
            for e, named in files:
                if chosen is not None:
                    self.files.append((e["name"], "zero-yield", 0,
                                       f"the same package as {chosen[0]['name']}"))
                    continue
                data, problem = self._download(e["id"])
                if data is None:
                    problems.append((e, "fetch failed", problem))
                    continue
                details, why = parse_brochure(pdf_pages(data), named)
                if "price" not in details:
                    problems.append((e, "zero-yield", why))
                    continue
                chosen = (e, named, details, why)
            # NOT LISTED WHEN THE NAME'S PRICE IS PRINTED NOWHERE. "H+L (4x2), The Spires
            # Estate, Baldivis, BG - $984k" prints $948,500 (24 Oct 2026): one of the two is
            # wrong and nothing here can say which, so the package waits for RealNet rather
            # than going out at a price a buyer may be held to.
            for e, outcome, why in problems:
                self.files.append((e["name"], outcome, 0, why))
            if chosen is None:
                continue
            e, named, details, why = chosen
            # IDENTITY: the name without its price, and the file. The id survives a rename
            # (a price change); a file deleted and uploaded again is a new listing, and the
            # old one's page answers 404 that night (link_check).
            identity = "%s [%s]" % (named["identity"], e["id"])
            row = listing_from(e, named, details, self.folder_url, identity, today,
                               shares_name=name_count[ident] > 1)
            row["_drive_name"] = e["name"]
            out.append(row)
            self.files.append((e["name"], "read", 1, why))
        self.state = "ok"
        self.why = "%d package(s) from %d folder entr%s" % (
            len(out), self.listed, "y" if self.listed == 1 else "ies")
        return out


def _identity_key(identity: str) -> str:
    return re.sub(r"[^a-z0-9+]", "", (identity or "").lower())


def parse_listing(body: str) -> List[Dict[str, str]]:
    """Entries of Drive's embedded folder view."""
    out = []
    for block in body.split('<div class="flip-entry" id="entry-')[1:]:
        fid = re.match(r"[\w-]+", block)
        name = re.search(r'<div class="flip-entry-title">(.*?)</div>', block, re.S)
        href = re.search(r'<a href="([^"]+)"', block)
        if not (fid and name):
            continue
        h = html.unescape(href.group(1)) if href else ""
        out.append({"id": fid.group(0), "name": html.unescape(name.group(1)).strip(),
                    "kind": "folder" if "/folders/" in h else "file"})
    return out


def pdf_pages(data: bytes, limit: int = 1) -> List[str]:
    """Text of the first page. Every template prints its package on page 1; page 2 is a
    floor plan or an inclusions list whose labels ("HOUSE AREA") and repeated address
    ("UNIT 2 / 116 DOLINA DRIVE, ROKEBY - FLOOR PLAN") only add noise -- measured over all
    42 packages on 24 Oct 2026, page 1 alone reads the same fields and two more lots."""
    try:
        try:
            import pymupdf as fitz      # PyMuPDF 2 warns on the old name
        except ImportError:
            import fitz
    except ImportError:
        logger.error("RealNet: PyMuPDF is not installed -- no brochure can be checked "
                     "against its name, so no package is listed.")
        return []
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:                                             # noqa: BLE001
        logger.warning("RealNet: unreadable PDF: %s", e)
        return []
    try:
        return [doc[i].get_text() for i in range(min(limit, doc.page_count))]
    finally:
        doc.close()
