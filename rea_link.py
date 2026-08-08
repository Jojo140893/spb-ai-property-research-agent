"""
A realestate.com.au search URL for a listing we hold — built, never fetched.

Coleen, this hour: *"the link that opens that building inside the realestate.com.au
website so Coleen can compare them"*. The stored half of that needs a PropTrack key
nobody has bought yet. This half needs nothing: a search URL is a string, and the
request is made by HER browser when she clicks it, not by us. No API, no key, no
credential, and nothing here ever contacts realestate.com.au.

WHY A SEARCH AND NOT THE LISTING ITSELF. We do not know the listing. The site's own
per-property path is `property-{type}-{state}-{suburb}-{id}` — an id alone is not
enough to build it, and it has never been established that a PropTrack listingId even
equals the id in that path (PropTrack is sales-gated, so the response contract in
comp_provider_proptrack.py is a guess by its own admission). A constructed listing URL
that 404s in front of a buyer is worse than an honest suburb search, so this builds the
search and benchmarks.py stores the provider's own URL verbatim when one exists.

THE SHAPE, and how much of it is actually evidenced:

    https://www.realestate.com.au/buy/[with-N-bedrooms-]in-{suburb},+{state}/list-1

The location slug is confirmed against REA Group's own static host, which serves
`in-tarneit,+vic+3029/list-2`: lowercase, LITERAL comma, spaces as plus signs, /list-N
pagination. `safe=","` in quote_plus is load-bearing — without it Python emits `%2C`,
which is not REA's form.

Two parts are NOT confirmed and are the reason each clause is a named constant that can
be dropped in one line: the two-part `suburb,+state` slug is evidenced only for REGIONS
(`in-melbourne+-+northern+region,+vic`), never verbatim for a suburb, and
`with-N-bedrooms` immediately after `/buy/` is composed from an example where a
property-type clause preceded it. Both must be opened by hand in a browser once before
this goes in front of a client.

WHAT IS DELIBERATELY LEFT OUT, each because including it makes the answer worse:

  POSTCODE.  data/au_suburbs.csv has columns suburb,state,lat,lng,sal_code — no postcode
             column, so a missing one cannot be backfilled. Only 1,439 of the 2,927
             linkable live rows store one at all, and 25 of those carry a postcode whose
             state range contradicts the row's own state. Requiring one would cut the
             feature from 2,927 rows across 223 suburbs to 1,414 across 69, to buy a
             field that is wrong on 25 rows.
  PRICE.     Filtering the market to a band around our own asking price hides the exact
             evidence the link was opened to find. benchmark_market.py already warns
             about this shape of question-begging; this is the same error in a URL.
  TYPE.      Our dominant product_type is "House & Land", which has no single REA
             equivalent — PROPTRACK_TYPES already expands the same concept to a LIST of
             three — and a wrong list narrows her comparison silently rather than
             visibly.

THE GATE. A link is emitted only where suburb_quality.is_located() vouches for the
suburb: 2,927 of 6,141 live rows, 47.7%. That is not a shortfall to be papered over. The
stored suburb column holds 'GARAGE', '2026' and 'Logan City Council', and `in-,+nsw` is
a broken search, not a comparison. Because the STATE travels inside the slug, the worst
case for a row that passes the gate is an empty results page — never a different
suburb's properties.
"""

from typing import Any, Dict, Optional
from urllib.parse import quote_plus

BASE = "https://www.realestate.com.au/buy/"
# /list-1 is the first page of results. REA's own pagination form; omitting it is
# untested and there is no reason to find out.
PAGE = "/list-1"

# REA's bedroom filter tops out at "5+", so 6 is not a narrower search than 5 — it is a
# clause the site may not honour. 24 linkable rows claim 6 or more bedrooms (three of
# them 7 or 9, which are data faults rather than houses). Outside 1-5 the clause is
# dropped and the location-only link is used, which is still correct.
MIN_BEDROOMS = 1
MAX_BEDROOMS = 5

LABEL = "realestate.com.au"
OPENS = "market"


def location_slug(suburb: str, state: str) -> str:
    """`tarneit,+vic` — lowercase, literal comma, spaces as plus signs.

    safe="," is required. Plain quote_plus emits `clyde+north%2C+vic`, and REA's own
    host serves the comma unescaped (`in-tarneit,+vic+3029/list-2`).
    """
    suburb = " ".join(str(suburb or "").split()).strip()
    state = str(state or "").strip()
    if not suburb or not state:
        return ""
    return quote_plus("%s, %s" % (suburb.lower(), state.lower()), safe=",")


def search_url(suburb: str, state: str, bedrooms: Optional[Any] = None) -> str:
    """The URL, or "" when there is not enough to build one that would work.

    Returning "" rather than a partial URL is the whole point: `/buy/in-,+nsw/list-1`
    is a page that loads and answers the wrong question, which is harder to spot than
    a link that was never rendered.
    """
    slug = location_slug(suburb, state)
    if not slug:
        return ""
    clauses = []
    beds = _bedrooms(bedrooms)
    if beds:
        clauses.append("with-%d-bedrooms" % beds)
    clauses.append("in-" + slug)
    return BASE + "-".join(clauses) + PAGE


def search_term(suburb: str, state: str) -> str:
    """What to paste into REA's own search box if the slug ever stops matching.

    Same contract as provenance._kind's `search`: the link may go stale, the term the
    destination actually indexes does not.
    """
    suburb = " ".join(str(suburb or "").split()).strip()
    state = str(state or "").strip().upper()
    if not suburb or not state:
        return ""
    return "%s, %s" % (suburb, state)


def compare_link(row: Dict[str, Any]) -> Dict[str, str]:
    """{url, label, opens, search} for one BUILDING row, or blanks with a reason.

    The row must already have been through suburb_quality.resolve — this reads the
    RESOLVED suburb and the reason beside it, never the stored column. build_web.py
    does that resolution a few lines before it calls here.

    A row that fails the gate gets url="" and keeps the reason, so index.html can print
    why rather than an empty cell. A blank with a stated reason beats a plausible guess
    is this codebase's standing rule, and an unexplained gap on two of every three
    E-Agent rows would read as a broken feature rather than a data fact.
    """
    import suburb_quality                    # lazy: suburb_quality imports geo lazily
    why = str(row.get("suburb_source") or "").strip()
    suburb = str(row.get("suburb") or "").strip()
    state = str(row.get("state") or "").strip().upper()
    if not (suburb_quality.is_located(why) and suburb and state):
        return {"url": "", "label": LABEL, "opens": "",
                # No label either: paintStock falls through to the suburb's own
                # blankReason, which already carries the better sentence.
                "search": ""}
    return {"url": search_url(suburb, state, row.get("bedrooms")),
            "label": LABEL, "opens": OPENS,
            "search": search_term(suburb, state)}


def _bedrooms(value: Any) -> Optional[int]:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    return n if MIN_BEDROOMS <= n <= MAX_BEDROOMS else None
