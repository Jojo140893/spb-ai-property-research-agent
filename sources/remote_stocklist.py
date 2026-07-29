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
        logger.debug("google: tab list failed for %s: %s", sheet_id, e)
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
        targets.append(f"https://docs.google.com/spreadsheets/d/{sheet_id}"
                       f"/gviz/tq?tqx=out:csv&gid={gid}")
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
            return [Fetched(label=piece, kind=KIND_FILE, data=r.content, url=target,
                            meta={"tab": tab_name, "gid": gid})]
    return [Fetched(label=piece, url=url, problem=(
        "every CSV endpoint refused (export and gviz) — the sheet's sharing was probably "
        "tightened. Ask the builder to keep it link-viewable."))]


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
_DBX_STOCK_FILE = re.compile(
    r"(price|pricing|stock|availab|sales?[ _-]?list|inventory|packages?)"
    r".*\.(xlsx|xlsm|xls|csv|pdf)$", re.I)
_DBX_ANY_SHEET = re.compile(r"\.(xlsx|xlsm|xls|csv)$", re.I)


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
        return [Fetched(label=label, url=url,
                        problem="folder listing came back empty (layout change or dead link)")]

    files = [(n, u) for n, is_dir, u in entries if not is_dir]
    picks = [(n, u) for n, u in files if _DBX_STOCK_FILE.search(n)]
    if not picks:
        picks = [(n, u) for n, u in files if _DBX_ANY_SHEET.search(n)]
    if not picks:
        for name, is_dir, sub in entries:
            if is_dir and _DBX_PRICE_FOLDER.search(name):
                for n, u in [(n, u) for n, d, u in _dropbox_entries(sess, sub) if not d]:
                    if _DBX_STOCK_FILE.search(n) or _DBX_ANY_SHEET.search(n):
                        picks.append((f"{name}/{n}", u))
    if not picks:
        return [Fetched(label=label, url=url, problem=(
            f"no price list in this folder — it holds only "
            f"{', '.join(n for n, _d, _u in entries[:4])}"
            f"{' ...' if len(entries) > 4 else ''}. Nothing to harvest until the vendor "
            f"uploads one."))]

    out: List[Fetched] = []
    for name, entry_url in picks[:4]:
        target = re.sub(r"([?&])dl=0", r"\1dl=1", entry_url)
        try:
            r = sess.get(target, timeout=180)
        except Exception as e:
            out.append(Fetched(label=f"{label} / {name}", url=target,
                               problem=f"download failed: {e}"))
            continue
        if r.status_code == 200 and _looks_like_a_file(r.content):
            out.append(Fetched(label=f"{label} / {name}", kind=KIND_FILE,
                               data=r.content, url=target))
        else:
            out.append(Fetched(label=f"{label} / {name}", url=target,
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
