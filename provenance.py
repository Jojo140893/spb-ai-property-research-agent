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


# A search term has to be something the destination will actually match. Two things
# disqualify a value outright:
#   * length — 1,573 E-Agent rows hold the whole flattened spreadsheet line in
#     lot_address ("AVAILABLE 121 10C Bingara Drive Hamptons 15 450 248.14 6 3.5 2 Mid
#     2026 Split $665,000 $786,000"). Pasting that into a search box matches nothing.
#   * money — if a value carries a price it is a data row, not a name.
_TOO_LONG = 70
_HAS_MONEY = re.compile(r"\$\s*[\d,]")


def _clean_ref(value: Any) -> str:
    v = re.sub(r"\s+", " ", str(value or "")).strip().strip(",")
    if not v or len(v) > _TOO_LONG or _HAS_MONEY.search(v):
        return ""
    return v


def _find_term(row: Dict[str, Any], opens: str) -> str:
    """The best identifier this row has for THAT kind of destination, or "".

    Which field is searchable depends on what is being opened, not on the channel:
      * a price list is a spreadsheet, so the term is what its Lot column holds;
      * a portal indexes developments, so the term is the project name;
      * a document is about one lot, so the term is that lot's street address.
    Field coverage is uneven across channels (E-Agent has a lot number on half its rows
    and a street address on 8%), so each is a fallback chain ending in "" — no term at
    all beats a term that returns an error, which is the whole complaint here.
    """
    lot_no = _clean_ref(row.get("lot_number"))
    street = _clean_ref(row.get("street_address"))
    estate = _clean_ref(_PROJECT_COUNTER.sub("", str(row.get("estate_name") or "")))
    addr = _clean_ref(row.get("lot_address"))
    suburb = _clean_ref(row.get("suburb"))

    if opens in ("portal", "project"):
        # Developments are indexed by name. Fall back to the street, never to a lot.
        return estate or street or addr
    if opens == "price list":
        # A sheet's own Lot column is the one column that identifies a row. Qualify it
        # with the estate where we have one, because "121" alone matches everywhere.
        if lot_no and estate:
            return f"{lot_no} {estate}"
        if lot_no and suburb:
            return f"{lot_no} {suburb}"
        return street or addr or estate
    # A per-lot document or page: the address is the name.
    return street or addr or (f"{lot_no} {estate}".strip() if lot_no or estate else "")


def _kind(label: str, url: str, opens: str, search: str = "") -> Dict[str, str]:
    # `search` is the EXACT string to paste into the destination's own search box.
    #
    # Colin, 6 Aug: "the name should be same so when i search on their portal it should
    # give me exact building instead of an error." He searched our headline address,
    # "Lot 2 Unit 2, 275 Twelfth Avenue, AUSTRAL, NSW, 2179", and Proxima returned
    # nothing — because that string is Proxima's name for the LOT, and its search box
    # indexes PROJECTS. The lot lives inside the project accordion.
    #
    # So the term we hand over is whatever that particular destination will actually
    # match on, which is not always the address we show on the card. It is copied
    # verbatim from a value the row already holds; nothing here is constructed.
    return {"label": label, "url": url, "opens": opens, "search": search}


def source_links(row: Dict[str, Any]) -> list:
    """Every place this listing can be traced to, best first.

    Each entry is {label, url, opens} where `opens` says what the consultant will
    actually land on: "lot" | "project" | "price list" | "email" | "document" |
    "portal" (the portal's front door, when nothing narrower is available).
    """
    out = []
    channel = str(row.get("source_channel") or "").strip()

    def add(label, url, opens, search=None):
        url = str(url or "").strip()
        if url and url.lower().startswith(("http://", "https://")) and \
                not any(e["url"] == url for e in out):
            # "Here is the file" is only half an answer when the file is a spreadsheet
            # with thousands of lines; the other half is what to find in it. Every
            # channel gets a term, derived from what that destination actually indexes.
            term = _find_term(row, opens) if search is None else search
            out.append(_kind(label, url, opens, term))

    # A per-lot document is the closest thing to "the building" that exists.
    add("Lot PDF", row.get("listing_url"), "lot")
    add("Floorplan", row.get("floorplan_url"), "lot")
    add("Brochure", row.get("brochure_url"), "document")

    if channel == "Proxima":
        pid = str(row.get("source_project_id") or "").strip()
        if pid.isdigit():
            # The project page opens directly; the lot is one row inside its accordion,
            # and this is what that row is called there.
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
                else "Proxima projects list", PROXIMA_INDEX_URL, "portal",
                # The project name, NOT the lot address on the card. This is the whole
                # point: searching the address is what returned an error.
                search=project)  # the project name, never the lot address

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
        # A price list is thousands of spreadsheet rows; the lot label is what to find
        # in it. E-Agent rows carry the whole flattened row as lot_address, so prefer
        # the bare lot number there — that is what the sheet's own Lot column holds.
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
