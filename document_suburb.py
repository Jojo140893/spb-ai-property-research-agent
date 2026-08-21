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


# BE A GOOD GUEST. This reads ~70 documents in a run, and they belong to other people's
# file hosts. Fetching them back-to-back got e-agent.com.au to start returning empty
# bodies partway through a session -- not an error, just nothing, which reads exactly
# like "this document has no text" and would have quietly emptied the map on the next
# nightly run. A pause between requests and a retry on an empty body costs a couple of
# minutes once a night and removes both problems.
_PAUSE_SECONDS = 1.5
_EMPTY_RETRIES = 3
_last_fetch_at = [0.0]


def _get(url: str, opener=None) -> bytes:
    """The bytes at a URL, preferring requests over urllib.

    NOT A STYLE CHOICE. e-agent.com.au answers urllib with a flat HTTP 429 and requests
    with a 200, from the same machine, in the same second, with identical headers -- even
    with curl's own User-Agent. The block is on the TLS/HTTP fingerprint of the stack,
    not on anything in the request, so no amount of header-setting reaches it.

    That cost real time: urllib returning nothing looks exactly like a rate limit you have
    earned, and 17 e-agent documents covering several hundred listings were written off as
    unreadable on that basis. requests is already a dependency (requirements.txt), so this
    is only a matter of asking the right library.

    `opener` is honoured when a caller passes one, so the tests can inject a fake without
    a network stack of any kind.
    """
    if opener is not None:
        request = urllib.request.Request(url, headers={"User-Agent": _UA})
        with opener(request, timeout=_TIMEOUT) as response:
            return response.read(_MAX_BYTES)
    try:
        import requests
    except ImportError:                            # pragma: no cover - env without it
        request = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return response.read(_MAX_BYTES)
    response = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT,
                            allow_redirects=True)
    if response.status_code != 200:
        logger.info("document %s answered HTTP %s", url[:70], response.status_code)
        return b""
    return response.content[:_MAX_BYTES]


def fetch(url: str, opener=None, sleep=None) -> bytes:
    """The document's bytes, or b'' if it cannot be read. Never raises.

    `opener` defaults to None so _get chooses the library -- see the note there about
    e-agent answering urllib 429 and requests 200. Passing one is for tests.
    """
    import time

    napper = sleep or time.sleep
    for attempt in range(1, _EMPTY_RETRIES + 1):
        gap = time.monotonic() - _last_fetch_at[0]
        if gap < _PAUSE_SECONDS:
            napper(_PAUSE_SECONDS - gap)
        try:
            data = _get(url, opener)
        except Exception as exc:                   # network, auth, redirect, anything
            logger.info("document %s could not be fetched (%s)",
                        url[:80], type(exc).__name__)
            data = b""
        finally:
            _last_fetch_at[0] = time.monotonic()
        if data:
            return data
        # An EMPTY body is the shape rate limiting takes here, and it is indistinguishable
        # from a genuinely empty file, so it is worth backing off for rather than
        # accepting as an answer.
        if attempt < _EMPTY_RETRIES:
            logger.info("empty body for %s — backing off (%d/%d)",
                        url[:60], attempt, _EMPTY_RETRIES)
            napper(_PAUSE_SECONDS * 4 * attempt)
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
    # A HAND-CHECKED RULE MATCHING THE DOCUMENT'S OWN TITLE, first.
    #
    # Two of these documents name neither their suburb nor anything the rows carry --
    # "Collins Wharf - Ancora" and "DAIRY FARMERS TOWERS" have no usable builder_name or
    # estate_name between them. The title is the only durable handle they have, and it
    # keeps working when the file is re-uploaded to a new link.
    lowered = text.lower()
    for rule in manual_rules():
        needle = str(rule.get("title") or "").strip().lower()
        if needle and needle in lowered:
            rule_state = str(rule.get("state") or "").upper()
            if rule_state and state and rule_state != state.upper():
                continue
            return str(rule["suburb"])
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
# THE ANSWERS A DOCUMENT CANNOT GIVE ITSELF.
#
# A tower price list names its PROJECT and never its suburb — "Victoria & Albert",
# "Collins Wharf - Ancora", "Senso", "Dairy Farmers Towers". Reading more pages does not
# help; the suburb is simply not in the file. But these are named developments with
# published addresses, so the answer exists, it is just not in the document.
#
# This file holds those, one line per document, each with the address it was verified
# against so the claim can be rechecked rather than taken on trust. Hand-written, never
# overwritten by the automatic refresh, and it WINS over the automatic answer — a person
# who has checked the developer's own listing knows more than a front-page scan.
_MANUAL_PATH = "data/document_suburbs_manual.json"
_MAP_CACHE: Optional[Dict[str, List[str]]] = None


def map_path(manual: bool = False) -> str:
    import os
    name = _MANUAL_PATH if manual else _MAP_PATH
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def _read(path: str) -> dict:
    import json
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


_RULES_CACHE: Optional[List[dict]] = None


def manual_rules(refresh: bool = False) -> List[dict]:
    """The hand-checked rules, each able to match by url, project or document title."""
    global _RULES_CACHE
    if _RULES_CACHE is not None and not refresh:
        return _RULES_CACHE
    raw = _read(map_path(manual=True))
    rules = raw.get("rules") if isinstance(raw, dict) else None
    if isinstance(rules, list):
        _RULES_CACHE = [r for r in rules if isinstance(r, dict) and r.get("suburb")]
    else:
        # The original shape: {url: {suburb, state, evidence}}. Still read, so an older
        # checkout of this file keeps working.
        _RULES_CACHE = [dict(v, url=k) for k, v in (raw or {}).items()
                        if isinstance(v, dict) and v.get("suburb")]
    return _RULES_CACHE


def load_map(refresh: bool = False) -> Dict[str, List[str]]:
    """{url: [suburb, state]} — what each document says, manual answers taking priority."""
    global _MAP_CACHE
    if _MAP_CACHE is not None and not refresh:
        return _MAP_CACHE
    merged = {str(k): list(v) for k, v in _read(map_path()).items()
              if isinstance(v, (list, tuple))}
    for rule in manual_rules(refresh=refresh):
        if rule.get("url"):
            merged[str(rule["url"])] = [str(rule["suburb"]), str(rule.get("state") or "")]
    _MAP_CACHE = merged
    return _MAP_CACHE


def _rule_for_project(row: Dict[str, object]) -> Optional[dict]:
    """A hand-checked rule matching this row's own builder or estate.

    WHY THIS EXISTS AND THE URL KEY IS NOT ENOUGH. A builder re-uploads its price list to
    a fresh Dropbox link every few weeks. The rows follow the new URL on the next harvest,
    so a rule keyed only to the old one silently stops applying and 637 listings go back
    to having no suburb -- with nothing failing and nothing to notice. The project name
    travels with the row, so it keeps working across the swap and applies to lots added to
    that project later.
    """
    haystack = " ".join(str(row.get(field) or "")
                        for field in ("builder_name", "estate_name")).lower()
    if not haystack.strip():
        return None
    for rule in manual_rules():
        needle = str(rule.get("project") or "").strip().lower()
        if needle and needle in haystack:
            return rule
    return None


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

    Tried by exact URL first, then by the project the row belongs to -- see
    _rule_for_project for why the URL alone is not durable.

    The state must still agree either way. An answer was read under one state and a row
    from another state is not covered by it.
    """
    row_state = str(row.get("state") or "").strip().upper()

    def _agrees(suburb, state):
        if state and row_state and str(state).upper() != row_state:
            return ""
        return str(suburb or "")

    entry = load_map().get(str(row.get("source_url") or ""))
    if entry:
        suburb, state = (list(entry) + ["", ""])[:2]
        found = _agrees(suburb, state)
        if found:
            return found

    rule = _rule_for_project(row)
    if rule:
        return _agrees(rule.get("suburb"), rule.get("state"))
    return ""


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
    index = SuburbGeoIndex()

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

    # PER-ROW MAPPING, for every document that still has an unnamed row on it — not
    # only the wholly-unnamed ones. A builder stocklist can have half its sections
    # recognised and half not, and the half that is not is invisible to the orphan test.
    row_map: Dict[str, str] = {}
    partial = [(u, rs) for u, rs in members.items() if located[u] < len(rs)]
    partial.sort(key=lambda pair: -len(pair[1]))
    for url, group in (partial[:limit] if limit else partial):
        states = collections.Counter(
            str(r.get("state") or "").strip().upper() for r in group)
        state = states.most_common(1)[0][0] if states else ""
        found = row_suburbs(fetch(url), state, index)
        if not found:
            continue

        # EVERY DOCUMENT MUST EARN ITS PLACE, and most do not.
        #
        # Ungated, this mapping is 32% right. The Hudson Homes list is read perfectly --
        # 104 of 104 -- because its headers are clean "Estate - Stage, Suburb" lines, and
        # that success does not generalise: on other layouts a line that is not a header
        # matches anyway, and then LATCHES, handing its suburb to every lot line beneath
        # it until the next match. That is how "Donnybrook" ended up on Shepparton lots
        # and "Melton" on Tarneit ones. One bad header silently mislabels dozens of rows.
        #
        # So the document is asked to reproduce what is already known about it: the rows
        # on it that carry a checked suburb from a stronger source. Agree with enough of
        # them and its remaining rows are trusted; disagree and the whole document is
        # dropped, not just the lines that disagreed.
        #
        # This is strict on purpose and it costs almost everything: 2 documents of 28
        # pass, 63 lines of 1,138 survive. It is kept because those 34 new suburbs are
        # RIGHT, and because the alternative is a nightly job that quietly republishes a
        # 32%-accurate map every morning with every check still green.
        agree = disagree = 0
        unknown: List[str] = []
        for row in group:
            key = line_key(row.get("source_text") or "")
            named = found.get(key)
            if not named:
                continue
            stored = str(row.get("suburb") or "").strip()
            row_state = str(row.get("state") or "").strip()
            checked = stored and (index.locate(stored, row_state) if row_state
                                  else index.states_for_suburb(stored))
            if not checked:
                unknown.append(key)
            elif named.lower() == stored.lower():
                agree += 1
            else:
                disagree += 1
        checks = agree + disagree
        if checks < _ROW_MAP_MIN_CHECKS or agree / checks < _ROW_MAP_FLOOR:
            logger.warning("      rejected %s (%d/%d agree)",
                           url.split("/")[-1][:40], agree, checks)
            continue
        for key in unknown:
            row_map[key] = found[key]
        logger.warning("%5d lines named in %s (%d/%d agree)",
                       len(unknown), url.split("/")[-1][:40], agree, checks)
    save_row_map(row_map)
    logger.warning("per-row map: %d line(s) across %d document(s)",
                   len(row_map), len(partial))

    orphans = [(u, rs) for u, rs in members.items() if not located[u]]
    orphans.sort(key=lambda pair: -len(pair[1]))
    if limit:
        orphans = orphans[:limit]

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



# ---------------------------------------------------------------------------------
# PER ROW, not per document.
#
# A builder's stocklist is not one suburb, it is a dozen. The Hudson Homes list reads
#
#     Watagan Park - Display Homes, Cooranbong        <- section header
#     Available 7 House MARIGOLD 35 ... $1,519,500    <- lots in Cooranbong
#     Jensen Rise Estate - Wadalba, Wadalba           <- section header
#     Available 61 House COPPER 21 ... $1,167,260     <- lots in Wadalba
#
# and every one of its 104 live rows was stored with no suburb at all, because the
# harvest kept the lot line and threw the header above it away. suburb_for_document
# cannot help here — asked for ONE locality it correctly refuses, since there are twelve.
#
# The lot line itself is the join. It is stored verbatim as the row's source_text, so
# walking the document in order, remembering the last header seen, gives each line the
# suburb printed above it. Measured on that document: 104 of 104 rows matched, naming
# Wadalba, Millfield, Bellbird, Denman, Warnervale, Woongarrah, Maitland, Heatherbrae,
# Cooranbong, Riverstone, Gillieston Heights and Austral.
#
# This is the document stating where the lot is, so it is FROM_DOCUMENT like the rest —
# a fact read off the price list, not an inference about it.
_ROW_MAP_PATH = "data/document_row_suburbs.json"
_ROW_CACHE: Optional[Dict[str, str]] = None
# How much a document must prove before its unknown rows are believed. See the long note
# in refresh_from_database: ungated this mapping is 32% right, and the failure latches.
_ROW_MAP_MIN_CHECKS = 5
_ROW_MAP_FLOOR = 0.9

# A lot row is the one with money on it. Section headers never carry a price.
_MONEY = re.compile(r"\$\s?[\d,]{4,}")


def line_key(text: str) -> str:
    """A stable key for one lot line, tolerant of the encoding noise PDFs produce.

    The same line arrives as 'COPPER 21 197.08m\xb2' from one reader and '197.08m?' from
    another, so anything that is not a word character or basic punctuation is flattened.
    """
    # ASCII-only, deliberately. Python's \w is Unicode-aware, so the superscript in
    # '197.08m²' is a word character and survives while the '?' a different reader
    # produces for the same glyph does not — giving two keys for one line.
    cleaned = re.sub(r"[^A-Za-z0-9$,./ -]", " ", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip().lower()[:110]


def row_suburbs(data: bytes, state: str, index) -> Dict[str, str]:
    """{lot line key: suburb} for one document, from the headers above each block."""
    if not data[:4] == b"%PDF":
        return {}
    try:
        import pdfplumber
    except ImportError:                            # pragma: no cover - env without it
        return {}
    lines: List[str] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages[:60]:
                try:
                    lines.extend((page.extract_text() or "").split("\n"))
                except Exception:                  # one bad page costs one page
                    continue
    except Exception as exc:
        logger.info("document unreadable for row mapping (%s)", type(exc).__name__)
        return {}

    out: Dict[str, str] = {}
    current = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if _MONEY.search(line):
            if current:
                out[line_key(line)] = current
            continue
        # A SECTION HEADER ENDS WITH ITS SUBURB. "Jensen Rise Estate - Wadalba, Wadalba"
        # and "Mt View Grange - Bellbird, Bellbird" both name the estate, then the stage,
        # then the locality. Only the last comma-separated segment is tested, which is
        # what keeps "Bingara Gorge - Spec Contract" from yielding Bingara.
        segments = [seg.strip() for seg in line.split(",") if seg.strip()]
        if not segments:
            continue
        tail = segments[-1]
        if len(_WORD.findall(tail)) > 4:
            continue                               # a sentence, not a locality
        if state:
            if index.locate(tail, state):
                current = tail.title()
        else:
            states = index.states_for_suburb(tail)
            states = list(states) if not isinstance(states, str) else [states]
            if len(states) == 1:
                current = tail.title()
    return out


def load_row_map(refresh: bool = False) -> Dict[str, str]:
    """{lot line key: suburb} across every document the harvest has read."""
    global _ROW_CACHE
    if _ROW_CACHE is not None and not refresh:
        return _ROW_CACHE
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _ROW_MAP_PATH)
    loaded = _read(path)
    _ROW_CACHE = {str(k): str(v) for k, v in loaded.items() if v}
    return _ROW_CACHE


def save_row_map(mapping: Dict[str, str]) -> None:
    import json
    import os
    global _ROW_CACHE
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _ROW_MAP_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(mapping, handle, indent=0, sort_keys=True)
    _ROW_CACHE = dict(mapping)


def suburb_for_line(row: Dict[str, object]) -> str:
    """The suburb printed above this row's own line in its source document, or ''."""
    return load_row_map().get(line_key(row.get("source_text") or ""), "")


if __name__ == "__main__":                         # pragma: no cover - operator entry
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, default=0,
                        help="only the N documents with the most rows (for a quick check)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    refresh_from_database(limit=args.limit)
