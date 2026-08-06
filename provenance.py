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
               Without that id it can only reach the agent projects index, so the label
               carries the PROJECT NAME to search for — Proxima indexes lots inside
               project accordions, never by address, so looking up a lot address there
               returns nothing.
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


# Proxima project titles carry a live "(sold/total)" counter — "275 Twelfth Ave Austral
# (6/6)" — which moves as lots sell and is not part of the name you search for.
_PROJECT_COUNTER = re.compile(r"\(\s*\d+\s*/\s*\d+\s*\)\s*$")


def _project_name(row: Dict[str, Any]) -> str:
    """The project title as it appears in Proxima's own list, ready to search for."""
    name = _PROJECT_COUNTER.sub("", str(row.get("estate_name") or "")).strip()
    return re.sub(r"\s+", " ", name)


def _kind(label: str, url: str, opens: str) -> Dict[str, str]:
    return {"label": label, "url": url, "opens": opens}


def source_links(row: Dict[str, Any]) -> list:
    """Every place this listing can be traced to, best first.

    Each entry is {label, url, opens} where `opens` says what the consultant will
    actually land on: "lot" | "project" | "price list" | "email" | "document" |
    "portal" (the portal's front door, when nothing narrower is available).
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
            # No project id stored, so this can only reach the index. Say the PROJECT
            # NAME, because that is the one thing that makes the index usable.
            #
            # Proxima does not index a lot by its address: every lot lives inside a
            # project accordion, so searching "Lot 2 Unit 2, 275 Twelfth Avenue, AUSTRAL"
            # returns nothing at all — which is exactly what Colin hit. The project is
            # "275 Twelfth Ave Austral"; find that, expand it, and the lot is inside.
            # Labelling this "opens the project" was an over-promise.
            project = _project_name(row)
            add(f'Proxima - find the project "{project}"' if project
                else "Proxima projects list", PROXIMA_INDEX_URL, "portal")

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
    rank = {"lot": 0, "project": 1, "price list": 2, "email": 3, "document": 4,
            "portal": 5}
    return sorted(links, key=lambda e: rank.get(e["opens"], 9))[0]
