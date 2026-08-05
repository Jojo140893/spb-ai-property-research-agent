"""
"Where this came from" — a link that takes a consultant to the source of a listing.

Colin, 5 Aug 2026: *"The idea is to avoid all that extra, because then I have to go back
and start looking for it. If there's a URL that I can click and then…"* He spent roughly
eight minutes of that call trying to find one Rouse Hill lot and never found it.

What "directly to the building" can mean differs per source, and saying which is the
point — a link labelled "price list" sets a different expectation from "lot page", and a
consultant who clicks expecting the lot and lands on a spreadsheet has been misled by
the label, not by the data.

  E-Agent      the exact price-list file on e-agent.com.au (100% of rows), plus the
               per-lot PDF where the row carries one. E-Agent publishes FILES, not
               per-listing pages, so there is no lot page in existence to link to.
  Proxima      the project page, built from the project id the harvest already reads.
               Falls back to the agent projects index when the id is absent.
  Email        a Gmail search for the exact subject, so the price list itself opens.
  Portals      whatever real URL the row already carries.

Nothing here invents a destination: every link is built from a value the row already
holds, and a row with nothing to point at gets no link rather than a guess.
"""

import re
from typing import Any, Dict, Optional
from urllib.parse import quote

PROXIMA_PROJECT_URL = "https://portal.proxima.com.au/agent/projects/view/id/%s"
PROXIMA_INDEX_URL = "https://portal.proxima.com.au/agent/projects/index/"
GMAIL_SEARCH_URL = "https://mail.google.com/mail/u/0/#search/%s"

# source_url on an emailed price list is stored as "email:<subject>", not a link.
_EMAIL_REF = re.compile(r"^\s*email:\s*(.+)$", re.I)


def _kind(label: str, url: str, opens: str) -> Dict[str, str]:
    return {"label": label, "url": url, "opens": opens}


def source_links(row: Dict[str, Any]) -> list:
    """Every place this listing can be traced to, best first.

    Each entry is {label, url, opens} where `opens` says what the consultant will
    actually land on: "lot" | "project" | "price list" | "email" | "document".
    """
    out = []
    channel = str(row.get("source_channel") or "").strip()

    def add(label, url, opens):
        url = str(url or "").strip()
        if url and url.lower().startswith(("http://", "https://")) and \
                not any(e["url"] == url for e in out):
            out.append(_kind(label, url, opens))

    # A per-lot document is the closest thing to "the building" that exists.
    add("Lot PDF", row.get("listing_url"), "lot")
    add("Floorplan", row.get("floorplan_url"), "lot")
    add("Brochure", row.get("brochure_url"), "document")

    if channel == "Proxima":
        pid = str(row.get("source_project_id") or "").strip()
        if pid.isdigit():
            add("Open in Proxima", PROXIMA_PROJECT_URL % pid, "project")
        else:
            # No project id stored for this row — the index is honest but coarse, and
            # the label says so rather than promising the lot.
            add("Proxima projects list", PROXIMA_INDEX_URL, "project")

    # The file the row was read out of. For E-Agent this is the price list itself and
    # it is the only e-agent destination that exists.
    for field, label in (("stocklist_file", "Price list"), ("source_url", "Source file")):
        value = str(row.get(field) or "").strip()
        if not value:
            continue
        match = _EMAIL_REF.match(value)
        if match:
            subject = match.group(1).strip()
            if subject:
                add("Find the email", GMAIL_SEARCH_URL % quote(subject), "email")
            continue
        if value.rstrip("/") == PROXIMA_INDEX_URL.rstrip("/") and any(
                e["opens"] == "project" for e in out):
            continue                      # already covered by a real project link
        add(label, value, "price list" if label == "Price list" else "document")

    return out


def primary_source_link(row: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """The one link to put on a recommendation card, or None if the row has none."""
    links = source_links(row)
    if not links:
        return None
    rank = {"lot": 0, "project": 1, "price list": 2, "email": 3, "document": 4}
    return sorted(links, key=lambda e: rank.get(e["opens"], 9))[0]
