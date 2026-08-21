"""The suburb a stock document is FOR, read from the document itself.

WHY THIS EXISTS. 1,857 live listings sit on 70 source documents where not one row carries
a suburb. Their lot_address is a pure spec string — "001 $1,888,000 4BED 2 BATH 2 CAR
272SQM", "143 3bed.2bath.2 car 3 2 0 0 2 Sea Breeze" — with no estate name and no sibling
that has a locality, so nothing in the database can name them. They are invisible to
every suburb search, which is how a property Coleen was looking at in E-Agent came back
as "the app doesn't have it".

Guessing was measured and rejected: the strongest address-only rule reaches 86.8%, and a
wrong suburb in a client report is worse than a blank one. But these documents are not
lost — they are price lists for ONE project, they say where that project is on their
front page, and they are still at the URL the row was read from. So this fetches the
document and reads its own answer.

WHAT MAKES IT SAFE, and why each guard is here:

  the FIRST PAGE only        A price list names its project at the top. Later pages are
                             lot tables full of street names, and a street named after a
                             town is the single commonest way a wrong suburb gets in.
  the state must agree       Where the rows carry a state, the locality has to be in it.
                             'Springs' is a real place in WA and appears in Queensland
                             documents as part of an estate name.
  one document, one answer   If the first page names two localities in the row's state,
                             this returns nothing. A multi-project price list cannot be
                             collapsed to a single suburb, and picking the first one
                             would silently mislabel the rest.
  the rows must agree        Applied only where NO row on that document already has a
                             located suburb, so this can never overwrite a stated value.

The result is recorded as FROM_DOCUMENT, a located reason distinct from STATED, so the
provenance travels with the value and a reader can always see the app read it off the
price list rather than being told it by a stock feed.
"""
from __future__ import annotations

import io
import logging
import re
import urllib.request
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; SPB-research/1.0)"
_TIMEOUT = 45
_MAX_BYTES = 8_000_000
# A price list names its project on page one. Page two onward is the lot table, where
# every row carries a street name and a street named after a town reads as a locality.
_FRONT_PAGES = 1
_MAX_CHARS = 4_000

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")

# Words that are a locality nowhere, but appear constantly on the front page of a price
# list. Without these the first "locality" found is usually a product word.
_NOT_A_PLACE = re.compile(
    r"^(price|pricelist|list|stock|new|the|and|of|for|master|update|current|report|"
    r"availability|package|packages|stage|estate|home|homes|living|land|lot|lots|"
    r"house|apartment|apartments|townhouse|townhomes|villa|villas|terrace|terraces|"
    r"residence|residences|release|precinct|collection|display|available|sold|"
    r"registered|titled|deposit|contract|total|inclusions|inclusion|bed|bath|car|"
    r"garage|sqm|approx|from|only|now|due|eta|gst|inc|incl|plan|plans|type|types|"
    r"frontage|size|reg|area|internal|external|level|floor|street|road|drive|avenue|"
    r"court|place|way|close|rise|view|park|parks|green|grove|ridge|creek|waters|"
    r"heights|gardens|valley|hill|hills|beach|bay|point|central|north|south|east|"
    r"west|stages|sale|sales|agent|agents|builder|builders|developer|group|pty|ltd|"
    # 'Sale' is a real Victorian town, and "Warehouse Price List ... Sales" put it on a
    # Bayswater document. 'Page' is a suburb of Canberra and also the footer of every
    # document ever printed — it named a Sydney commercial price list before this line.
    r"warehouse|commercial|residential|luxury|premium|boutique|exclusive|limited|"
    r"page|pages|version|date|dated|updated|revision|rev|contact|phone|email|www)$",
    re.I)

# A locality immediately followed by a street type is a STREET. 'Bourke' named a price
# list because the document mentions Bourke Street; Bourke is a town 760 km from it.
_STREET_TYPES = frozenset("""
    st street rd road dr drive ave avenue av cres crescent cct circuit cir circle ct
    court pl place way wy blvd boulevard bvd pde parade tce terrace cl close gr grove
    gdns gardens ln lane hwy highway esp esplanade rise view walk loop link mews green
    ridge chase row track trail bend crest glade grange pkwy parkway prom promenade
    quay retreat square sq vista wynd
""".split())


def fetch(url: str, opener=urllib.request.urlopen) -> bytes:
    """The document's bytes, or b'' if it cannot be read. Never raises."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": _UA})
        with opener(request, timeout=_TIMEOUT) as response:
            return response.read(_MAX_BYTES)
    except Exception as exc:                       # network, auth, redirect, anything
        logger.info("document %s could not be fetched (%s)", url[:80], type(exc).__name__)
        return b""


# The top-left block of a spreadsheet, which is where a stock list puts its banner —
# the same place sources/spreadsheet_extract reads its context from.
_SHEET_ROWS = 12
_SHEET_COLS = 12


def front_page_text(data: bytes) -> str:
    """The text of the document's first page or first sheet. '' if unreadable.

    Both shapes are needed: a third of these documents are .xlsx, and reading only PDFs
    left 10 of them unnamed for no better reason than the file extension.
    """
    if data[:4] == b"%PDF":
        try:
            import pdfplumber
        except ImportError:                        # pragma: no cover - env without it
            logger.info("pdfplumber not installed — cannot read document front pages.")
            return ""
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                pages = pdf.pages[:_FRONT_PAGES]
                return "\n".join((p.extract_text() or "") for p in pages)[:_MAX_CHARS]
        except Exception as exc:
            logger.info("front page unreadable (%s)", type(exc).__name__)
            return ""

    if data[:2] == b"PK":                          # xlsx is a zip
        try:
            import openpyxl
        except ImportError:                        # pragma: no cover - env without it
            return ""
        try:
            book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            sheet = book[book.sheetnames[0]]
            cells = []
            for row in sheet.iter_rows(min_row=1, max_row=_SHEET_ROWS,
                                       max_col=_SHEET_COLS, values_only=True):
                cells.extend(str(v) for v in row if v is not None)
            book.close()
            return " ".join(cells)[:_MAX_CHARS]
        except Exception as exc:
            logger.info("workbook unreadable (%s)", type(exc).__name__)
            return ""

    return ""


def localities_in(text: str, state: str, index) -> List[str]:
    """Every distinct locality named in the text, longest match first.

    Longest wins so 'Armstrong Creek' is one answer rather than 'Creek', and
    'South Melbourne' rather than 'Melbourne'.
    """
    words = _WORD.findall(text or "")
    found: List[str] = []
    claimed = set()
    for size in (3, 2, 1):
        for start in range(len(words) - size + 1):
            if any(pos in claimed for pos in range(start, start + size)):
                continue
            candidate = " ".join(words[start:start + size])
            if size == 1 and _NOT_A_PLACE.match(candidate):
                continue
            after = start + size
            if after < len(words) and words[after].lower().strip(".") in _STREET_TYPES:
                continue                           # 'Bourke Street', not Bourke
            if state:
                if not index.locate(candidate, state):
                    continue
            else:
                states = index.states_for_suburb(candidate)
                states = list(states) if not isinstance(states, str) else [states]
                if len(states) != 1:               # ambiguous nationally: not evidence
                    continue
            name = candidate.title()
            if name not in found:
                found.append(name)
            claimed.update(range(start, start + size))
    return found


def suburb_for_document(url: str, state: str, index, fetcher=fetch) -> str:
    """The one locality this document is for, or '' if it does not name exactly one."""
    text = front_page_text(fetcher(url))
    if not text:
        return ""
    names = localities_in(text, state, index)
    # EXACTLY ONE. A price list covering several projects cannot be collapsed to a single
    # suburb, and taking the first would quietly mislabel every lot in the others.
    return names[0] if len(names) == 1 else ""


def resolve_documents(groups: Iterable[Tuple[str, str]], index,
                      fetcher=fetch) -> Dict[Tuple[str, str], str]:
    """{(url, state): suburb} for the documents that name exactly one locality."""
    out: Dict[Tuple[str, str], str] = {}
    for url, state in groups:
        if not url:
            continue
        suburb = suburb_for_document(url, state, index, fetcher=fetcher)
        if suburb:
            out[(url, state)] = suburb
            logger.info("document names %s: %s", suburb, url[:70])
    return out


# ---------------------------------------------------------------------------------
# The stored answer.
#
# Reading a document needs the network, so it cannot happen inside a serverless request
# — and it must not be written back onto the rows either, because `suburb` is part of
# content_hash: filling it in place gives every touched row a new identity and orphans
# the old one. So the answer lives beside the database as a small map from document to
# suburb, refreshed by the harvest and read by everything else.
_MAP_PATH = "data/document_suburbs.json"
_MAP_CACHE: Optional[Dict[str, List[str]]] = None


def map_path() -> str:
    import os
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), _MAP_PATH)


def load_map(refresh: bool = False) -> Dict[str, List[str]]:
    """{url: [suburb, state]} as last read from the documents themselves."""
    global _MAP_CACHE
    if _MAP_CACHE is not None and not refresh:
        return _MAP_CACHE
    import json
    import os
    try:
        with open(map_path(), encoding="utf-8") as handle:
            loaded = json.load(handle)
        _MAP_CACHE = {str(k): list(v) for k, v in loaded.items()} if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        _MAP_CACHE = {}
    return _MAP_CACHE


def save_map(mapping: Dict[str, List[str]]) -> None:
    import json
    import os
    global _MAP_CACHE
    os.makedirs(os.path.dirname(map_path()), exist_ok=True)
    with open(map_path(), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(mapping, handle, indent=1, sort_keys=True)
    _MAP_CACHE = dict(mapping)


def suburb_for_row(row: Dict[str, object]) -> str:
    """The suburb this row's own source document names, or ''.

    The state must still agree. A document's answer was read under one state and a row
    from another state on the same URL is not covered by it.
    """
    entry = load_map().get(str(row.get("source_url") or ""))
    if not entry:
        return ""
    suburb, state = (list(entry) + ["", ""])[:2]
    row_state = str(row.get("state") or "").strip().upper()
    if state and row_state and state != row_state:
        return ""
    return str(suburb or "")


def refresh_from_database(db_path: Optional[str] = None, limit: int = 0) -> Dict[str, List[str]]:
    """Re-read every source document whose rows have no suburb between them.

    Only those. A document whose rows already say where they are needs no network call,
    and re-reading one could only disagree with a stated value.
    """
    import collections
    import sqlite3

    import config
    import suburb_quality
    from geo import SuburbGeoIndex

    path = db_path or config.DATABASE_PATH
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM buildings WHERE (superseded_by IS NULL OR superseded_by = '')")]
    conn.close()

    located = collections.Counter()
    members = collections.defaultdict(list)
    for row in rows:
        row.pop("suburb_source", None)
        url = str(row.get("source_url") or "")
        if not url.startswith("http"):
            continue
        members[url].append(row)
        _suburb, why = suburb_quality.resolve(row)
        if suburb_quality.is_located(why) and why != suburb_quality.FROM_DOCUMENT:
            located[url] += 1

    orphans = [(u, rs) for u, rs in members.items() if not located[u]]
    orphans.sort(key=lambda pair: -len(pair[1]))
    if limit:
        orphans = orphans[:limit]

    index = SuburbGeoIndex()
    mapping: Dict[str, List[str]] = {}
    for url, group in orphans:
        states = collections.Counter(
            str(r.get("state") or "").strip().upper() for r in group)
        state = states.most_common(1)[0][0] if states else ""
        suburb = suburb_for_document(url, state, index)
        if suburb:
            mapping[url] = [suburb, state]
            logger.warning("%5d rows -> %s (%s)  %s",
                           len(group), suburb, state or "?", url.split("/")[-1][:44])
    save_map(mapping)
    covered = sum(len(members[u]) for u in mapping)
    logger.warning("%d document(s) named a suburb, covering %d listing(s); "
                   "%d document(s) did not.",
                   len(mapping), covered, len(orphans) - len(mapping))
    return mapping


if __name__ == "__main__":                         # pragma: no cover - operator entry
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, default=0,
                        help="only the N documents with the most rows (for a quick check)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    refresh_from_database(limit=args.limit)
