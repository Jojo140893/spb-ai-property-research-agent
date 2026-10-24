"""
Fetch a stocklist that a builder publishes somewhere other than E-Agent.

E-Agent's category pages carry one "Live Packages" link per builder, but only some of
those links point at E-Agent's own file store. Counted live on 2026-07-29, of 142 stock
links across the twelve category pages just 24 were `e-agent.com.au/_files/ugd/...`:

    96  www.dropbox.com             shared folders ("Access Portal") — whole product
                                    categories: apartments VIC/QLD/NSW, townhouses
                                    VIC/QLD, commercial, land estates
    24  www.e-agent.com.au          direct xlsx / pdf
    16  docs.google.com             Google Sheets, incl. Tomorrow Homes
     3  app.smartsheet.com          "publish to web" sheets (G Developments x3)
     1  dbnhomes-my.sharepoint.com  DBN Homes
     1  onedrive.live.com           Prosperity Residential
     1  referrer.torsionhomes.au    an ASP.NET page, not a file

Filtering links on `_files/ugd` therefore missed 21 builders and estates — among them
Tomorrow Homes, which the client reaches by hand and asked for by name.

**No host in this file needs a login.** Every method below was established against the
live links and then independently reproduced by a second pass. Where a specific link
genuinely cannot be read (one Dropbox URL points into the owner's private file browser)
the handler says so and returns nothing.

Each handler returns a LIST, because one link is not always one file: a Dropbox folder
holds several, and a Google workbook holds one tab per estate.

Nothing here guesses. A host that answers with HTML where a file was expected is
reported, never parsed — a sign-in page must not become stock.
"""

import base64
import csv
import io
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger("spb.remote")

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:  # pragma: no cover
    REQUESTS_AVAILABLE = False

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# How the caller should treat what came back.
KIND_FILE = "file"      # bytes for sources.spreadsheet_extract.extract_stocklist
KIND_HTML = "html"      # a page for sources.adaptive_extract.extract_listings


@dataclass
class Fetched:
    label: str = ""               # describes this piece ("Tomorrow Homes (tab Stock List)")
    kind: str = KIND_FILE
    data: bytes = b""             # file bytes, when kind == KIND_FILE
    url: str = ""                 # the URL actually fetched
    problem: str = ""             # non-empty => nothing usable; what went wrong
    meta: Dict[str, Any] = field(default_factory=dict)


def _session() -> Any:
    """A cookie-keeping session. The cookie jar is load-bearing for SharePoint and
    OneDrive: redeeming a share link sets a cookie on the first hop that the second hop
    needs, so a cookie-less fetch of the same URL returns 56 KB of HTML instead of the
    workbook (and a 403 for OneDrive)."""
    s = requests.Session()
    s.headers["User-Agent"] = UA
    # ONE FAILED FETCH COSTS A WHOLE PRODUCT CATEGORY. A Dropbox folder link is the only
    # route to its estate's stock, and a single connection reset was enough to lose it
    # for the night: the caller drops a piece that carries a problem. A transient 429 or
    # 5xx now gets three attempts with backoff before it counts as a real failure.
    try:
        from urllib3.util.retry import Retry
        from requests.adapters import HTTPAdapter
        retry = Retry(total=3, backoff_factor=2,
                      status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset(("GET", "HEAD")),
                      raise_on_status=False)
        s.mount("https://", HTTPAdapter(max_retries=retry))
        s.mount("http://", HTTPAdapter(max_retries=retry))
    except Exception as e:                                              # noqa: BLE001
        logger.warning("could not install HTTP retries (%s) — a transient failure will "
                       "cost that link's stock for this run.", e)
    return s


def _looks_like_a_file(data: Optional[bytes]) -> bool:
    if not data or len(data) < 32:
        return False
    head = data[:400].lstrip().lower()
    return not (head[:1] == b"<" or b"<!doctype html" in head or b"<html" in head)


# ------------------------------------------------------------------- Google Sheets

GOOGLE_SHEET_ID = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})")
_GID = re.compile(r"(?:^|[&#?])gid=(\d+)")
# The tab switcher is bootstrapped in the htmlview page as items.push({name:..., gid:...})
_TAB_ITEM = re.compile(r'items\.push\(\{name:\s*"((?:[^"\\]|\\.)*)".*?gid:\s*"(-?\d+)"', re.S)


def _js_unescape(s: str) -> str:
    s = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    for a, b in (("\\/", "/"), ('\\"', '"'), ("\\'", "'"), ("\\\\", "\\")):
        s = s.replace(a, b)
    return s


def _google_tabs(sess: Any, sheet_id: str) -> Dict[str, str]:
    """{gid: tab name} for the visible tabs. One request, and the only name source that
    also works on sheets whose owner disabled downloading."""
    try:
        r = sess.get(f"https://docs.google.com/spreadsheets/d/{sheet_id}/htmlview",
                     timeout=60)
        if not r.ok:
            return {}
        return {gid: _js_unescape(name) for name, gid in _TAB_ITEM.findall(r.text)}
    except Exception as e:
        # WARNING, not DEBUG. This is the input to the stale-gid guard: when it is empty
        # the guard cannot run, and the harvest log is the only place that shows why.
        logger.warning("google: tab list failed for %s (%s) — the stale-gid guard "
                       "cannot run for this workbook.", sheet_id, e)
        return {}


def _fetch_google(url: str, label: str, sess: Any) -> List[Fetched]:
    """Google Sheets, as CSV.

    Two deliberate choices, both from measurement:

    * CSV, not `format=xlsx`. The xlsx export writes prices as bare numbers, and while
      the header-aware money detection in spreadsheet_extract now recovers most of that,
      CSV keeps the sheet's own "$519,000" text and is strictly more faithful.
    * `gviz/tq` as a fallback, not an alternative. Verv Projects and **Tomorrow Homes**
      are shared "anyone with the link can view" with downloading disabled, which makes
      every `/export` and `/pub` URL answer 401. `gviz` is not covered by that
      restriction. Without the fallback we lose Tomorrow Homes.
    """
    m = GOOGLE_SHEET_ID.search(url)
    if not m:
        return []
    sheet_id = m.group(1)
    parsed = urlparse(url)
    gid_match = _GID.search(parsed.fragment or "") or _GID.search(parsed.query or "")
    gid = gid_match.group(1) if gid_match else None

    tabs = _google_tabs(sess, sheet_id)
    # A gid the workbook no longer has is the dangerous case: `export` answers 400, but
    # `gviz` answers 200 with the FIRST tab's data, so eight estates pointing at stale
    # gids would all silently receive the same sheet. Never fall through to gviz for a
    # gid the tab list does not know.
    if gid and tabs and gid not in tabs:
        return [Fetched(label=label, url=url, problem=(
            f"tab gid={gid} no longer exists in this workbook (it has: "
            f"{', '.join(sorted(tabs.values())) or 'none visible'}) — the link on E-Agent "
            f"is stale. Not substituting another tab."))]

    tab_name = tabs.get(gid or "", "")
    piece = f"{label} (tab {tab_name or gid})".strip() if (tab_name or gid) else label
    targets = []
    if gid:
        targets.append(f"https://docs.google.com/spreadsheets/d/{sheet_id}"
                       f"/export?format=csv&gid={gid}")
        # THE GVIZ FALLBACK IS ONLY SAFE WHEN THE GID IS KNOWN TO BE REAL.
        #
        # The guard above needs `tabs`, and _google_tabs returns {} on any non-200 —
        # historically with no log at all — so a single 429 or a markup change disabled
        # it and the harvest fell straight through to gviz, which answers 200 with the
        # FIRST tab for a gid that no longer exists. That is precisely the silent
        # substitution the guard was written to prevent, and every section on that
        # workbook would have received the same sheet.
        #
        # `export` is not affected: it 400s correctly on a stale gid. So when the tab
        # list is unknown, only the fallback is withheld — the normal path still works,
        # and the workbooks the fallback exists for (download-disabled ones, where
        # export 401s for every gid) simply report a problem instead of inventing data.
        if tabs:
            targets.append(f"https://docs.google.com/spreadsheets/d/{sheet_id}"
                           f"/gviz/tq?tqx=out:csv&gid={gid}")
        else:
            logger.warning("google %s: the tab list could not be read, so the gviz "
                           "fallback is withheld for gid=%s — it would return the "
                           "workbook's first tab if that gid is stale.", label, gid)
    else:
        targets.append(f"https://docs.google.com/spreadsheets/d/{sheet_id}"
                       f"/export?format=csv")
        targets.append(f"https://docs.google.com/spreadsheets/d/{sheet_id}"
                       f"/gviz/tq?tqx=out:csv")
    for target in targets:
        try:
            r = sess.get(target, timeout=90)
        except Exception as e:
            logger.debug("google: %s failed: %s", target, e)
            continue
        if r.status_code == 200 and "csv" in (r.headers.get("Content-Type") or ""):
            # WHICH ENDPOINT, AND HOW MANY ROWS (7 Oct 2026). On 6 Oct the harvest read 22
            # listings from the Havenwood tab and the sheet check read 53 from the same
            # link the same night; every stored row from that workbook came through gviz,
            # and nothing recorded which endpoint served which read, so the difference
            # could not be traced. Said for every piece, by both readers.
            # Rows as a CSV reader counts them (a quoted cell can hold a line break), and
            # never the link itself: the sheet check passes the URL as its label, and a
            # sheet link opens the sheet to anyone who has it.
            endpoint = "gviz" if "/gviz/" in target else "export"
            rows = _csv_rows(r.content)
            who = (piece if not label.lower().startswith("http")
                   else f"sheet {sheet_id[:6]}... (tab {tab_name or gid or 'first'})")
            logger.info("google %s: served by %s, %d CSV row(s)", who, endpoint, rows)
            _note_document(target, csv_rows=rows, endpoint=endpoint)
            return [Fetched(label=piece, kind=KIND_FILE, data=r.content, url=target,
                            meta={"tab": tab_name, "gid": gid, "endpoint": endpoint,
                                  "csv_rows": rows})]
    return [Fetched(label=piece, url=url, problem=(
        "every CSV endpoint refused (export and gviz) — the sheet's sharing was probably "
        "tightened. Ask the builder to keep it link-viewable."))]


def _note_document(url: str, **fields: Any) -> None:
    """Tell tonight's harvest manifest a document's own size (harvest_manifest.DOCUMENTS),
    for the retirement ledger's whole-read proof. The webhook bundle ships this module
    without harvest_manifest, and the sheet check runs in its own process: both skip it."""
    try:
        import harvest_manifest
        harvest_manifest.note_document(url, **fields)
    except Exception:                                                  # noqa: BLE001
        pass


def _note_truncated(url: str, why: str) -> None:
    """Tell tonight's harvest manifest that this stock link's read was cut short by the
    reader itself (harvest_manifest.LINK_NOTES): its lists are not whole tonight, whatever
    they yield. Guarded like _note_document."""
    try:
        import harvest_manifest
        harvest_manifest.note_link(url, truncated=True, why=why)
    except Exception:                                                  # noqa: BLE001
        pass


def _csv_rows(body: bytes) -> int:
    """Non-blank rows in a CSV body. Diagnostic only: never raises."""
    import csv
    import io
    try:
        text = (body or b"").decode("utf-8", "replace")
        return sum(1 for row in csv.reader(io.StringIO(text)) if any(c.strip() for c in row))
    except Exception:                                                  # noqa: BLE001
        return -1


# --------------------------------------------------------- SharePoint / OneDrive

def _fetch_msoffice(url: str, label: str, sess: Any) -> List[Fetched]:
    """SharePoint and OneDrive share links. `download=1` on the share URL itself.

    Do NOT rewrite the `:x:/g/` path to `download.aspx?UniqueId=`: that works for the
    SharePoint tenant link but sends the OneDrive one to a different host that the share
    cookie does not cover, which answers 200 with HTML.
    """
    target = url + ("&" if "?" in url else "?") + "download=1"
    try:
        r = sess.get(target, timeout=180)
    except Exception as e:
        return [Fetched(label=label, url=target, problem=f"fetch failed: {e}")]
    if r.status_code == 200 and _looks_like_a_file(r.content):
        return [Fetched(label=label, kind=KIND_FILE, data=r.content, url=target)]
    return [Fetched(label=label, url=target, problem=(
        f"HTTP {r.status_code} and no file body — the share link may have been revoked "
        f"or now requires a Microsoft sign-in."))]


# ----------------------------------------------------------------------- Dropbox

_DBX_B64 = re.compile(rb"[A-Za-z0-9+/=]{500,}")
_DBX_ENTRY = re.compile(
    rb"https://www\.dropbox\.com/(?:scl/fo|sh)/([A-Za-z0-9_\-]+)/([A-Za-z0-9_\-]+)"
    rb"((?:/[A-Za-z0-9_\-%.,()'!+&~$@\[\]{}= ]+)*)"
    rb"\?(?:rlkey=[a-z0-9]+&)?dl=0")
_DBX_FILEISH = re.compile(r"^.+\.[A-Za-z0-9]{1,5}$")
# Only descend into a subfolder that says it holds prices — keeps the listing cost at
# ~1 request per folder instead of walking the whole tree.
_DBX_PRICE_FOLDER = re.compile(r"price|pricing|stock|availab|sales?[ _-]?list|^stage[ _-]?\d", re.I)
# What is worth downloading out of a folder full of flyers, renders and videos.
#
# "stock" ONLY AS A WORD OF ITS OWN (7 Oct 2026). The bare substring matched "Stockland":
# six "Stockland - EOI Request Form.pdf" / VOI forms were downloaded and parsed as price
# lists on 6 Oct, while a real one beside them was skipped for its name. "Stock List",
# "Stocklist", "VIC Stock.pdf" and "Current Stock" still match.
_DBX_STOCK_FILE = re.compile(
    r"(price|pricing|stock(?:[ _-]?(?:list|update|sheet|report)|s?(?![a-z]))|availab|"
    r"sales?[ _-]?list|inventory|packages?)"
    r".*\.(xlsx|xlsm|xls|csv|pdf)$", re.I)
_DBX_ANY_SHEET = re.compile(r"\.(xlsx|xlsm|xls|csv)$", re.I)
# A subfolder that SAYS it holds prices: whatever document is inside is taken, whatever
# it is called. "Price List/report_murcia-residences_2026-8-22.pdf" is a price list by
# where it sits. Stage folders order the walk (above) but say nothing about content.
_DBX_PRICE_NAMED = re.compile(r"price|pricing|stock(?!land)|availab|sales?[ _-]?list", re.I)
_DBX_DOCUMENT = re.compile(r"\.(pdf|xlsx|xlsm|xls|csv)$", re.I)
# NOT A PRICE LIST, BY ITS NAME: what the 6 Oct "no price list" folders actually held --
# brochures, floor plans, renders, site photos, inclusions, contracts, EOI/VOI forms, maps,
# school lists. Used only for the fallback below, never to refuse a keyword match.
_DBX_NOT_PRICES = re.compile(
    r"brochure|floor[ _-]?plans?|floorplans?|\bplans?\b|render|photo|image|gallery|"
    r"inclusion|finishes|specification|contract|\beoi\b|\bvoi\b|expression[ _-]?of[ _-]?"
    r"interest|request[ _-]?form|application|flyer|\bmap\b|aerial|school|amenit|drawing|"
    r"presentation|video|finance|inspection|terms", re.I)
# A guess is only worth a download when it is small: price lists here are tens of KB to a
# few hundred, while the brochures this would otherwise pull run to 10-40 MB.
_DBX_GUESS_MAX_BYTES = 2 * 1024 * 1024


# A GUESSED FILE MUST READ AS A PRICE LIST (repair round 1, 7 Oct 2026). The fallback above
# trusted the extractor to return nothing for a brochure; it does not. A project brochure
# gave 4 rows ("One bedroom residences from $450,000"), a residence-types sheet 4 ("Type A
# 1 Bed ... from $455,000"), an EOI form 1 ("Lot 1234 Altrove Estate $350,000"). A price
# list's lines each print their own figures -- unit or lot, rooms, areas -- beside the
# price; a brochure's lines print an indicative "from" price and little else.
_FROM_PRICE = re.compile(r"\b(?:from|starting(?:\s+at)?|priced?\s+from)\s*(?:only\s*)?"
                         r"(?:AUD\s*)?\$", re.I)
_MONEY = re.compile(r"[A-Za-z]?\$\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmM]\b)?|\b\d{1,3}(?:,\d{3})+"
                    r"(?:\.\d+)?\b")
_FIGURE = re.compile(r"\d+(?:\.\d+)?")
# Figures besides the price that a line of a price list prints, and lines a list has.
GUESS_MIN_FIGURES = 3
GUESS_MIN_ROWS = 2
# EACH LINE NAMES ITS OWN LOT (repair round 2, 7 Oct 2026, data verifier). A brochure's
# residence-type table without "from" -- "Residence A 2 Bed 2 Bath 1 Car 85 sqm $650,000",
# "Level 3 | 2 Bed | 2 Bath | 1 Car | $650,000" -- prints figures enough on each line. A
# price list's line names the lot or unit it prices: "A-201 1 1 - 60 13 73 $850,000",
# "Lot 12 ...", "Unit 1203 ...". A type letter ("Residence A") or a level is not one.
_GUESS_LOT_ANYWHERE = re.compile(
    r"\b(?:lot|unit|apt|apartment|townhouse|villa|th)\s*[#:.]?\s*([a-z]{0,3}[-.]?\d{1,5}[a-z]?)\b",
    re.I)
_GUESS_LOT_FIRST = re.compile(r"^\W*([A-Za-z]{0,3}[-.]?\d{1,5}[A-Za-z]?)(?=[\s|,;:]|$)")


def _guessed_lot(line: str) -> str:
    """The lot or unit a price-list line opens with or names, or ''."""
    m = _GUESS_LOT_ANYWHERE.search(line) or _GUESS_LOT_FIRST.match(line)
    return m.group(1).lower() if m else ""


def guessed_price_list(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The rows of a GUESSED file worth storing: [] unless it reads as a price list.

    A row is kept when its line is not a "from" price, prints GUESS_MIN_FIGURES figures
    or more besides its money amounts, and names a lot or unit of its own; the file is kept
    only when GUESS_MIN_ROWS rows or more survive, naming GUESS_MIN_ROWS different lots.
    Everything else -- a brochure, a type sheet, a form -- stores nothing and is reported as
    "no price list" by the caller."""
    kept, lots = [], set()
    for r in rows:
        line = str(r.get("source_text") or r.get("lot_address") or "")
        if _FROM_PRICE.search(line):
            continue
        if len(_FIGURE.findall(_MONEY.sub(" ", line))) < GUESS_MIN_FIGURES:
            continue
        lot = _guessed_lot(line)
        if not lot:
            continue
        lots.add(lot)
        kept.append(r)
    return kept if len(kept) >= GUESS_MIN_ROWS and len(lots) >= GUESS_MIN_ROWS else []


def _download_capped(sess: Any, url: str, max_bytes: int):
    """(response, bytes or None, why) -- None when the file is larger than max_bytes."""
    r = sess.get(url, timeout=180, stream=True)
    try:
        size = int((getattr(r, "headers", {}) or {}).get("Content-Length") or 0)
    except (TypeError, ValueError):
        size = 0
    if size > max_bytes:
        _close(r)
        return r, None, f"{size:,} bytes, over the {max_bytes:,}-byte limit for a guess"
    chunks = getattr(r, "iter_content", None)
    if chunks is None:                           # a response that is not streamed
        data = r.content
    else:
        data, got = [], 0
        for chunk in chunks(65536):
            got += len(chunk)
            if got > max_bytes:
                _close(r)
                return r, None, f"over the {max_bytes:,}-byte limit for a guess"
            data.append(chunk)
        data = b"".join(data)
    return r, data, ""


def _close(r: Any) -> None:
    try:
        r.close()
    except Exception:                                                  # noqa: BLE001
        pass


def _dropbox_entries(sess: Any, url: str) -> List[Tuple[str, bool, str]]:
    """[(name, is_folder, per-entry url)] for a shared folder, in ONE request.

    A Dropbox folder page is a JS shell: the listing is neither in the visible HTML nor
    in any later XHR. It is a base64 protobuf blob inside the first response, and every
    child appears there as a complete per-entry share link.
    """
    try:
        html = sess.get(url, timeout=60).content
    except Exception as e:
        logger.debug("dropbox: list failed for %s: %s", url, e)
        return []
    found: List[Tuple[str, int, List[str], str]] = []
    for m in _DBX_B64.finditer(html):
        for pad in (b"", b"=", b"==", b"==="):
            try:
                blob = base64.b64decode(m.group(0) + pad, validate=False)
            except Exception:
                continue
            if b"dropbox.com/scl/fo/" in blob or b"dropbox.com/sh/" in blob:
                for e in _DBX_ENTRY.finditer(blob):
                    u = e.group(0).decode("utf-8", "replace")
                    parts = [x for x in urlparse(u).path.split("/") if x]
                    found.append((parts[2] if len(parts) > 2 else "", len(parts), parts, u))
                break
    if not found:
        return []
    # The folder's own canonical URL sits at the shallowest depth; its children are
    # exactly one segment deeper under the same folder id. Depth must come from the blob,
    # not from the requested URL — an old /sh/<key>/<hash> link re-emits everything in
    # canonical /scl/fo/<id>/<hash>/... form.
    base_depth = min(f[1] for f in found)
    base_id = next(f[0] for f in found if f[1] == base_depth)
    out, seen = [], set()
    for fid, depth, parts, u in found:
        if fid != base_id or depth != base_depth + 1 or u in seen:
            continue
        seen.add(u)
        name = unquote(parts[-1])
        out.append((name, not bool(_DBX_FILEISH.match(name)), u))
    return out


# Request budgets for one Dropbox link. Both are announced when they bind:
# a truncated read that says nothing is the whole failure this module was
# audited for.
_DBX_MAX_FILES = int(os.environ.get("SPB_DBX_MAX_FILES", "12"))
_DBX_MAX_FOLDERS = int(os.environ.get("SPB_DBX_MAX_FOLDERS", "20"))

def _fetch_dropbox(url: str, label: str, sess: Any) -> List[Fetched]:
    """Dropbox "Access Portal" folders — the whole apartment, townhouse, commercial and
    land-estate inventory. List the folder, then download only the stocklist entry:
    `dl=1` on the ROOT would zip everything (1.8 MB vs 112 KB for the one workbook)."""
    if "/home/" in urlparse(url).path:
        return [Fetched(label=label, url=url, problem=(
            "this is a link into the owner's private Dropbox file browser, not a share "
            "link — it cannot be read without the eAgent Dropbox account. Ask Coleen to "
            "re-share it as a normal share link."))]
    entries = _dropbox_entries(sess, url)
    if not entries:
        _note_truncated(url, "folder listing came back empty")
        return [Fetched(label=label, url=url,
                        problem="folder listing came back empty (layout change or dead link)")]

    files = [(n, u) for n, is_dir, u in entries if not is_dir]
    folders = [(n, u) for n, is_dir, u in entries if is_dir]

    # UNION, NOT FALLBACK — three times over, and each `if not picks:` cost real stock.
    #
    # 96 of E-Agent's 142 stock links are Dropbox folders, so this is the dominant source
    # in the pipeline, and it used to harvest ONE file from a folder that holds many:
    #
    #   * a keyword file suppressed every other spreadsheet beside it. A folder holding
    #     "VIC Price List.xlsx", "Brisbane Apartments.xlsx" and "Marina Towers Stage
    #     2.xlsx" yielded the first. "Report Murcia" — an actually observed filename —
    #     matches no keyword at all.
    #   * any root-level file at all suppressed the per-estate SUBFOLDERS beneath it. A
    #     category folder laid out as one summary sheet plus twelve estate subfolders,
    #     each with its own price list, contributed exactly one file. _fetch_dropbox
    #     contained no logging, and the "no price list in this folder" problem below is
    #     unreachable the moment any pick exists — so this was completely silent.
    #
    # The subfolder walk still only descends one level and still costs one request per
    # folder, which is the documented tradeoff; what changed is that it now runs whether
    # or not the root yielded something. The name filter is gone as a GATE — real
    # subfolders are project-named ("eAgent/APARTMENTS/NSW/Babylon"), which matches
    # nothing — and is kept only to order the walk, so price-named folders are visited
    # first and the budget below is spent where it is most likely to pay.
    picks = [(n, u) for n, u in files if _DBX_STOCK_FILE.search(n)]
    seen_urls = {u for _n, u in picks}
    for n, u in files:
        if u not in seen_urls and _DBX_ANY_SHEET.search(n):
            picks.append((n, u))
            seen_urls.add(u)

    folders.sort(key=lambda f: 0 if _DBX_PRICE_FOLDER.search(f[0]) else 1)
    # Documents no name rule accepted, kept in case nothing else in the folder is a price
    # list: (name shown, url).
    leftovers = [(n, u) for n, u in files if u not in seen_urls and _DBX_DOCUMENT.search(n)]
    for name, sub in folders[:_DBX_MAX_FOLDERS]:
        try:
            children = [(n, u) for n, d, u in _dropbox_entries(sess, sub) if not d]
        except Exception as e:                                          # noqa: BLE001
            logger.warning("dropbox %s: subfolder %r could not be listed (%s) — its "
                           "price list is NOT in this harvest", label, name, e)
            _note_truncated(url, "a subfolder could not be listed")
            continue
        price_named = bool(_DBX_PRICE_NAMED.search(name))
        for n, u in children:
            if u in seen_urls:
                continue
            if (_DBX_STOCK_FILE.search(n) or _DBX_ANY_SHEET.search(n)
                    or (price_named and _DBX_DOCUMENT.search(n)
                        and not _DBX_NOT_PRICES.search(n))):
                picks.append((f"{name}/{n}", u))
                seen_urls.add(u)
            elif _DBX_DOCUMENT.search(n) and not _DBX_NOT_PRICES.search(name):
                leftovers.append((f"{name}/{n}", u))
    if len(folders) > _DBX_MAX_FOLDERS:
        logger.warning("dropbox %s: %d subfolder(s), only the first %d walked — the rest "
                       "are NOT in this harvest: %s", label, len(folders),
                       _DBX_MAX_FOLDERS,
                       ", ".join(n for n, _u in folders[_DBX_MAX_FOLDERS:][:6]))
        _note_truncated(url, "the folder budget bound")

    # NO FILE IS NAMED LIKE A PRICE LIST: TRY THE ONES THAT COULD BE ONE (7 Oct 2026).
    # Murcia's folder now holds "report_murcia-residences_2026-8-22_134434 (1)-2.pdf" -- a
    # price export, like the report_ files read for Fawkner Park West and The Brightly --
    # and the name filter skipped it: 25 lots unread since 22 Sep. Eight VIC townhouse
    # folders ("Fraser Rise Hattan.pdf", "Phoenix Cranbourne New.pdf", "Edison Park
    # VB.pdf") have never produced a row. So every small document whose name is not a
    # brochure, plan, render, form or the like is handed to the extractor, which already
    # returns nothing for a brochure. Marked as a guess, so a guess that yields nothing is
    # reported as "no price list" rather than as an unreadable price list.
    guessed = False
    if not picks:
        guesses = [(n, u) for n, u in leftovers if not _DBX_NOT_PRICES.search(n)]
        if guesses:
            guessed = True
            picks = guesses
            logger.info("dropbox %s: no file named as a price list; trying %d document(s) "
                        "that could be one: %s", label, len(guesses),
                        ", ".join(n for n, _u in guesses[:6]))

    if not picks:
        return [Fetched(label=label, url=url, problem=(
            f"no price list in this folder — it holds only "
            f"{', '.join(n for n, _d, _u in entries[:4])}"
            f"{' ...' if len(entries) > 4 else ''}. Nothing to harvest until the vendor "
            f"uploads one."))]

    if len(picks) > _DBX_MAX_FILES:
        logger.warning("dropbox %s: %d price list(s) found, only %d downloaded — %s "
                       "are NOT in this harvest", label, len(picks), _DBX_MAX_FILES,
                       ", ".join(n for n, _u in picks[_DBX_MAX_FILES:][:6]))
        _note_truncated(url, "the file budget bound")
    out: List[Fetched] = []
    for name, entry_url in picks[:_DBX_MAX_FILES]:
        target = re.sub(r"([?&])dl=0", r"\1dl=1", entry_url)
        meta = {"guessed": True} if guessed else {}
        try:
            if guessed:
                r, data, too_big = _download_capped(sess, target, _DBX_GUESS_MAX_BYTES)
                if data is None:
                    logger.info("dropbox %s / %s: not downloaded -- %s", label, name, too_big)
                    out.append(Fetched(label=f"{label} / {name}", url=target, meta=meta,
                                       problem=f"no price list: not one by its name, and "
                                               f"{too_big}"))
                    continue
            else:
                r = sess.get(target, timeout=180)
                data = r.content
        except Exception as e:
            out.append(Fetched(label=f"{label} / {name}", url=target, meta=meta,
                               problem=f"download failed: {e}"))
            continue
        if r.status_code == 200 and _looks_like_a_file(data):
            out.append(Fetched(label=f"{label} / {name}", kind=KIND_FILE,
                               data=data, url=target, meta=meta))
        else:
            out.append(Fetched(label=f"{label} / {name}", url=target, meta=meta,
                               problem=f"HTTP {r.status_code} and no file body"))
    return out


# -------------------------------------------------------------------- Smartsheet

_SS_BASE = "https://app.smartsheet.com/b/publish"
_SS_CLIENT_STATE = re.compile(r"var clientState=(\{.*?\});clientState\.loadTimeClient", re.S)
_SS_SHEET_ID = re.compile(r"TABLE_INDEX_DESKTOPSTATE[\d,]*\],false,\d+,\d+,(\d+),")
_SS_QUOTED = r"'(?:[^'\\]|\\.)*'"


def _ss_unquote(v: str) -> str:
    if v in (None, "null"):
        return ""
    return v[1:-1].replace("\\'", "'").replace("\\\\", "\\") if v.startswith("'") else v


def _fetch_smartsheet(url: str, label: str, sess: Any) -> List[Fetched]:
    """Smartsheet "publish to web" sheets, rebuilt as CSV.

    A published token has no supported export, so this reads the published app's own
    unauthenticated endpoints. That means parsing a private wire format, so it is written
    to fail LOUDLY: zero rows is reported as a problem, never as an empty stocklist.
    Currency cells are re-symbolised from their format flag, because a bare integer would
    be discarded by the price parser.
    """
    token = (parse_qs(urlparse(url).query).get("EQBCT") or [""])[0]
    if not token:
        return [Fetched(label=label, url=url, problem="no EQBCT publish token in the URL")]
    try:
        r1 = sess.get(_SS_BASE, params={"EQBCT": token}, timeout=60)
        m = _SS_CLIENT_STATE.search(r1.text)
        if not m:
            return [Fetched(label=label, url=url, problem=(
                "published page no longer exposes clientState — Smartsheet changed its "
                "app shell and this reader needs re-deriving."))]
        cs = json.loads(m.group(1))
        sid, sk, ver = cs["sessionID"], cs["sessionKey"], cs["version"]
        if (cs.get("publishType") or "READ_ONLY") != "READ_ONLY":
            return [Fetched(label=label, url=url, problem=(
                f"publishType={cs.get('publishType')!r} is not a read-only sheet publish "
                f"(a report or dashboard) — not parsed."))]
        common = {"sk": sk, "to": "68000", "pk": token, "sid": sid}
        headers = {"Referer": f"{_SS_BASE}?EQBCT={token}"}
        r2 = sess.post(_SS_BASE,
                       params={"formName": "desktop", "formAction": "loadData", "ss_v": ver},
                       data=dict(common, formName="desktop", formAction="loadData",
                                 parm1="", parm2="2", parm3="1",
                                 parm4=ver.rsplit(".", 1)[0],
                                 parmJson=json.dumps({"queryParameters": {"EQBCT": token}})),
                       headers=headers, timeout=90)
        ids = _SS_SHEET_ID.findall(r2.text)
        if not ids:
            return [Fetched(label=label, url=url,
                            problem="could not find the sheet id in loadData")]
        r3 = sess.post(_SS_BASE,
                       params={"formName": "ajax", "formAction": "fa_loadSheet", "ss_v": ver},
                       data=dict(common, formName="ajax", formAction="fa_loadSheet",
                                 errorAsJson="true", parm1=ids[0], parm2="2", parm3="",
                                 parm4="1"),
                       headers=headers, timeout=120)
        if "setAjaxResponseStatus(true" not in r3.text:
            return [Fetched(label=label, url=url,
                            problem="fa_loadSheet did not report success")]
        data = _ss_to_csv(r3.text)
    except Exception as e:
        return [Fetched(label=label, url=url, problem=f"published-sheet read failed: {e}")]
    if not data:
        return [Fetched(label=label, url=url, problem=(
            "parsed 0 rows out of the published sheet — treat as a failure, not an empty "
            "stocklist; Smartsheet's wire format has probably shifted."))]
    return [Fetched(label=label, kind=KIND_FILE, data=data, url=url)]


def _ss_to_csv(body: str) -> bytes:
    """Rebuild the published grid as CSV from the ajax payload."""
    # Anchor every pattern on the grid id, which is the THIRD field of a column record
    # (10, colId, gridId, ...). Reading it as the second field silently matches nothing.
    grid = re.search(r"GRIDCOLUMNDEF[^\]]*\],false,10,\d+,(\d+),", body)
    gid = grid.group(1) if grid else r"\d+"
    cols = {}
    for col_id, title, order in re.findall(
            rf"10,(\d+),{gid},0,({_SS_QUOTED}),0,null,\d+,\d+,(?:true|false),(\d+),", body):
        cols[col_id] = (int(order), _ss_unquote(title))
    cells: Dict[str, Dict[str, str]] = {}
    row_order: List[str] = []
    for _cell_id, row_id, col_id, _n, sval, nval, fmt in re.findall(
            rf"10,(\d+),(\d+),(\d+),\d+,{gid},0,({_SS_QUOTED}|null),0,"
            rf"({_SS_QUOTED}|null),(-?\d+(?:\.\d+)?|null),0,(?:{_SS_QUOTED}|null),(\d+),",
            body):
        if row_id not in cells:
            cells[row_id] = {}
            row_order.append(row_id)
        text = _ss_unquote(sval)
        if not text and nval not in (None, "null"):
            try:
                value = float(nval)
                # (fmt >> 12) & 0x3FFF marks a currency cell; the column name is the
                # backstop if that flag ever drifts.
                money = ((int(fmt) >> 12) & 0x3FFF) in (4644, 4645, 4646, 4647) or \
                    re.search(r"price|rent|cost|deposit|\$", cols.get(col_id, (0, ""))[1], re.I)
                text = f"${value:,.0f}" if money and abs(value) >= 1000 else (
                    f"{value:g}")
            except (TypeError, ValueError):
                text = ""
        if text:
            cells[row_id][col_id] = text
    if not cols or not cells:
        return b""
    ordered = [c for c, _ in sorted(cols.items(), key=lambda kv: kv[1][0])]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([cols[c][1] for c in ordered])
    for row_id in row_order:
        w.writerow([cells[row_id].get(c, "") for c in ordered])
    return buf.getvalue().encode("utf-8")


# --------------------------------------------------------------- Torsion / direct

def _fetch_torsion(url: str, label: str, sess: Any) -> List[Fetched]:
    """Torsion Homes renders stock as HTML cards rather than serving a file.

    The DriveID+FolderID pair in the query string is itself the capability token: the app
    bounces through Login.aspx, issues a guest cookie and returns the full stocklist, so
    no credentials are involved. Both parameters are load-bearing.
    """
    return [Fetched(label=label, kind=KIND_HTML, url=url)]


def _fetch_direct(url: str, label: str, sess: Any) -> List[Fetched]:
    """E-Agent's own file store, and any other plain link to a file."""
    try:
        r = sess.get(url, timeout=120)
    except Exception as e:
        return [Fetched(label=label, url=url, problem=f"fetch failed: {e}")]
    if r.status_code == 200 and _looks_like_a_file(r.content):
        return [Fetched(label=label, kind=KIND_FILE, data=r.content, url=url)]
    return [Fetched(label=label, url=url,
                    problem=f"HTTP {r.status_code} and no file body")]


_HANDLERS: Tuple[Tuple[Any, Callable[..., List[Fetched]]], ...] = (
    (re.compile(r"(^|\.)docs\.google\.com$", re.I), _fetch_google),
    (re.compile(r"(^|\.)sharepoint\.com$", re.I), _fetch_msoffice),
    (re.compile(r"(^|\.)onedrive\.live\.com$|(^|\.)1drv\.ms$", re.I), _fetch_msoffice),
    (re.compile(r"(^|\.)dropbox\.com$", re.I), _fetch_dropbox),
    (re.compile(r"(^|\.)app\.smartsheet\.com$", re.I), _fetch_smartsheet),
    (re.compile(r"(^|\.)referrer\.torsionhomes\.au$", re.I), _fetch_torsion),
)


def handler_for(url: str) -> Callable[..., List[Fetched]]:
    host = (urlparse(url).netloc or "").lower()
    for rx, fn in _HANDLERS:
        if rx.search(host):
            return fn
    return _fetch_direct


def is_offsite(url: str) -> bool:
    """True when the link is NOT one of E-Agent's own hosted files."""
    return "e-agent.com.au" not in (urlparse(url).netloc or "").lower()


def fetch_stocklist(url: str, label: str = "", sess: Any = None) -> List[Fetched]:
    """Everything downloadable behind one "Live Packages" link, whatever hosts it."""
    if not REQUESTS_AVAILABLE:
        logger.error("requests not installed — cannot read off-site stocklists.")
        return []
    own = sess is None
    sess = sess or _session()
    try:
        got = handler_for(url)(url, label, sess) or []
    except Exception as e:
        logger.warning("remote: %s failed: %s", url, e)
        got = []
    finally:
        if own:
            try:
                sess.close()
            except Exception:
                pass
    for f in got:
        if f.problem:
            logger.warning("remote: %s — %s", f.label or url, f.problem)
    return got
