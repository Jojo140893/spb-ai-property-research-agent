"""
data/harvest_manifest.json -- what every source gave the harvest tonight, one entry each.

WHY (7 Oct 2026). Coleen phoned that day: the same search shows far more in E-Agent and the
builder portals than in the app. The audit that followed had to rebuild, from a 2,500-line
log and the database, what each E-Agent link, Proxima project and portal yielded -- which
folders held no price list, which projects stopped at the 80-lot cap, which lots a
constraint dropped. This file writes that down every night, so the parity check can read
it instead of re-deriving it, and a gap can be traced to its source in one lookup.

It never holds a link. Dropbox and SharePoint share URLs are capabilities and this repo is
public, so a document is identified by `stocklist_key`: the first 16 hex characters of the
SHA-256 of the exact `stocklist_file` value stored on its rows -- computable from the
database by anyone who already holds the link, and from nothing else.

SCHEMA (version 1)

    {
      "schema": 1,
      "generated": "07/10/2026 21:14",          # dd/mm/YYYY HH:MM, local time
      "entries": [ENTRY, ...],
      "totals": {"by_channel": {CHANNEL: {"entries", "printed", "new", "updated",
                                          "unchanged", "rejected"}},
                 "by_outcome": {OUTCOME: count}}
    }

  Every ENTRY carries:
      id         int, unique within the file
      channel    "E-Agent" | "Proxima" | the portal's source_channel
      kind       "stock link" | "project" | "portal"
      name       what a person calls it: the E-Agent heading, the project, the builder
      outcome    one of OUTCOMES below
      printed    lots the extractor produced for it tonight
      stored     {"new", "updated", "unchanged", "rejected"}: what record_building did with
                 those lots ("rejected" = refused by a constraint and NOT stored)

  kind "stock link" (E-Agent, one per link on a category page or the pooled list) adds:
      page, state, product_type   the category page (its URL's last segment), as configured
      heading, label              the section heading above the link, and the link's text
      stocklist_key               see above
      pieces   [{"name", "outcome", "printed", "why"}] -- one per file behind the link (a
               Dropbox folder holds several, a Google workbook one per tab)

  kind "project" (Proxima, one per project read) adds:
      pid, on_index, via ("accordion" | "availability view"),
      header_counter [available, total] as the index header stated it tonight, or null,
      stated     the available count the read was measured against (tonight's header, else
                 the last one stored), or null,
      replays, bands   POSTs spent past the 80-lot cap, and how many were price bands,
      short      true when the read ended below `stated` or on a cap signature
    8 Oct 2026 (sources/proxima.py _log_project), so the parity check judges a read on what
    the harvest knew rather than re-deriving it:
      stated_source  "header" (tonight's index) or "stored" (the database's, from the last
                 night the project was on the index); stated_on  that night, dd/mm/YYYY
      whole      how the read is known complete: "uncapped" (the list ended under the
                 render cap), "price sorts met", "availability view"; "" when it is not
      stale_counter  true when a read proven whole is below an old stored counter by no
                 more than old_counter_gap_ok allows (see there)
      overseas   true when no lot names an Australian state and every lot is overseas
      cross_listed, cross_listed_into  lots printed here and stored once under another
                 project ({pid: n}); duplicates  lots printed twice in this list
      not_a_listing  tiles that made no lot (no price or no address)

  kind "portal" (direct builder portals, one per builder) adds:
      host, cards_found, cards_read   priced cards on the page vs cards read

  kind "search" (E-Agent Stocklist, one per night a company login is stored) adds:
      state    the reader's own word: ok | refused | unreachable | no_endpoint |
               gate_refused | error
      short    true when the read stopped at its page or record cap

OUTCOMES
      read                     at least one lot came out of it
      zero-yield               fetched and parsed, and no lot came out
      no price list            a folder with no file that could be a price list
      empty folder             a folder whose listing came back with nothing in it
      private link             a link into someone's private Dropbox, not a share
      fetch failed             the host refused or the download broke
      skipped by label filter  a stock-host link whose text is not a stock label
      not a price list         the link says brochure / EOI / inclusions / builder info
      no builder heading       a house-and-land link with no builder above it
      no credentials | login failed | crashed   (portals)
"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA = 1
OUTCOMES = ("read", "zero-yield", "no price list", "empty folder", "private link",
            "fetch failed",
            "skipped by label filter", "not a price list", "no builder heading",
            "no credentials", "login failed", "crashed")
_STORED = ("new", "updated", "unchanged", "rejected")

# HOW FAR BELOW AN OLD COUNTER A WHOLE READ MAY FALL (8 Oct 2026, repair round). A Proxima
# project off tonight's index is measured against the database's counter -- the index's
# number on the last night the project was on it -- and a read proven whole below it is the
# lots sold or withdrawn since (sources/proxima.py _warn_if_short, parity_check read_bar).
# "Proven whole" for a read under the render cap only says the list ended there, and a
# portal that serves a narrowed list (a session filter: browsing Proxima signed in narrows
# the next harvest) ends under the cap too. So the gap that is excused is bounded by what
# the first live night showed lots leaving at, with room: on 8 Oct the seven whole reads
# below old counters were 2/3, 8/11, 27/28, 138/140, 72/79, 108/117 and 27/28 -- at most
# 3 lots, or 8.9% of the counter (CARLING 7 of 79; The Grande 9 of 117 is 7.7%), and each
# missing lot's last read was on an earlier night (five of The Grande's on 07/10, so a
# lot can leave the list within a night). A read of nothing is never excused.
OLD_COUNTER_GAP_LOTS = 3
OLD_COUNTER_GAP_SHARE = 0.15


def old_counter_gap_ok(read: Any, stated: Any) -> bool:
    """True when a whole read of `read` lots is close enough under an old counter `stated`
    to be lots gone since that night: at least one lot read, and at most
    max(OLD_COUNTER_GAP_LOTS, OLD_COUNTER_GAP_SHARE of the counter) missing."""
    try:
        read, stated = int(read), int(stated)
    except (TypeError, ValueError):
        return False
    gap = stated - read
    return read > 0 and 0 <= gap <= max(OLD_COUNTER_GAP_LOTS,
                                        int(stated * OLD_COUNTER_GAP_SHARE))


ENTRIES: List[Dict[str, Any]] = []
_BY_KEY: Dict[tuple, Dict[str, Any]] = {}
_BY_ID: Dict[int, Dict[str, Any]] = {}


def reset() -> None:
    ENTRIES.clear()
    _BY_KEY.clear()
    _BY_ID.clear()


def stocklist_key(stocklist_file: str) -> str:
    """The id a stock link is known by here; never the link itself."""
    return hashlib.sha256(str(stocklist_file or "").encode("utf-8")).hexdigest()[:16]


def entry(channel: str, kind: str, key: str, **fields: Any) -> Dict[str, Any]:
    """The entry for (channel, kind, key), created on first use; `fields` are merged in.

    Get-or-create because the same thing can be met twice in one night -- a portal builder
    licensed in two states is scraped once per state -- and it is still one source.
    """
    k = (channel, kind, str(key))
    e = _BY_KEY.get(k)
    if e is None:
        e = {"id": len(ENTRIES) + 1, "channel": channel, "kind": kind, "name": "",
             "outcome": "", "printed": 0, "stored": {s: 0 for s in _STORED}}
        if kind == "stock link":
            e["pieces"] = []
        ENTRIES.append(e)
        _BY_KEY[k] = e
        _BY_ID[e["id"]] = e
    e.update({f: v for f, v in fields.items() if v is not None})
    return e


# A link inside free text -- "download failed: HTTPSConnectionPool(...): ... url:
# /scl/fo/<id>?rlkey=<key>" is what requests puts in an exception -- is cut out before the
# sentence is kept, so a share link cannot ride into this file inside an error message.
# A file name keeps its "Price List/" folder, so names lose only an outright URL.
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_LINKISH = re.compile(r"(?:https?://|www\.)\S+|(?:/[\w.%-]+){2,}\S*|\?\S*rlkey=\S+", re.I)


def _no_links(text: str, rx: "re.Pattern[str]" = _LINKISH) -> str:
    return rx.sub("<link>", str(text or ""))


def add_piece(e: Dict[str, Any], name: str, outcome: str, printed: int = 0,
              why: str = "") -> None:
    """One file behind a stock link, and what it gave. The link's outcome follows."""
    e.setdefault("pieces", []).append({"name": _no_links(name, _URL)[:120],
                                       "outcome": outcome,
                                       "printed": int(printed or 0),
                                       "why": _no_links(why)[:200]})
    e["printed"] = sum(p["printed"] for p in e["pieces"])
    e["outcome"] = link_outcome(e["pieces"])


def link_outcome(pieces: Iterable[Dict[str, Any]]) -> str:
    """read if any piece read; else the first problem; else zero-yield."""
    pieces = list(pieces)
    if any(p["outcome"] == "read" for p in pieces):
        return "read"
    for p in pieces:
        if p["outcome"] not in ("zero-yield", "read"):
            return p["outcome"]
    return "zero-yield" if pieces else ""


def tag(rows: Iterable[Dict[str, Any]], e: Dict[str, Any]) -> None:
    """Mark each lot with the entry it came from, so its storage outcome is counted there.
    record_building ignores the key: it stores only the columns it names."""
    for r in rows:
        r["_manifest_id"] = e["id"]


def count_outcomes(listings: List[Dict[str, Any]], outcomes: List[str]) -> None:
    for listing, outcome in zip(listings, outcomes):
        e = _BY_ID.get(listing.get("_manifest_id"))
        if e is not None and outcome in e["stored"]:
            e["stored"][outcome] += 1


def problem_outcome(problem: str) -> str:
    """A remote_stocklist problem sentence, as one of OUTCOMES."""
    p = (problem or "").lower()
    if "private dropbox" in p:
        return "private link"
    if "no price list" in p:
        return "no price list"
    if "came back empty" in p:
        return "empty folder"
    return "fetch failed"


def snapshot() -> Dict[str, Any]:
    by_channel: Dict[str, Dict[str, int]] = {}
    by_outcome: Dict[str, int] = {}
    for e in ENTRIES:
        c = by_channel.setdefault(e["channel"], {"entries": 0, "printed": 0,
                                                 **{s: 0 for s in _STORED}})
        c["entries"] += 1
        c["printed"] += int(e.get("printed") or 0)
        for s in _STORED:
            c[s] += int(e["stored"].get(s) or 0)
        by_outcome[e.get("outcome") or "unknown"] = by_outcome.get(
            e.get("outcome") or "unknown", 0) + 1
    return {"schema": SCHEMA,
            "generated": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "entries": ENTRIES,
            "totals": {"by_channel": by_channel, "by_outcome": by_outcome}}


def write(path: Optional[Path] = None) -> Path:
    path = Path(path or Path(__file__).resolve().parent / "data" / "harvest_manifest.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Belt and braces: whatever field a future caller fills, no URL reaches the file.
    text = re.sub(r"(?:https?://|www\.)[^\s\"]+", "<link>", json.dumps(snapshot(), indent=1))
    path.write_text(text, encoding="utf-8")
    return path
