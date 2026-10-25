"""
EAgent Stocklist -- E-Agent's pooled house-and-land search, at eagent.mekooflow.com.

RUNBOOK (owner) -- read this first
==================================
WHAT IT IS. E-Agent's members page now says "Search All H&L -- New search functionality
allowing agents to search all packages available via location and price etc. ... contact
your BDM for access" and links to https://eagent.mekooflow.com/login, a separate site
titled "EAgent Stocklist". It is where E-Agent moved the pooled house-and-land lists that
stopped on 24 Sep 2026, and it is most likely the search Coleen compares the app against
when she says "E-Agent always shows more" (phone call, 7 Oct 2026).

1. GET THE LOGIN. It is a COMPANY name plus a password, issued by E-Agent's BDM. Coleen
   asks her BDM for it. Nobody guesses it and nothing here tries.

2. STORE IT ON THE LAPTOP (for a hand run):
       python setup_credentials.py eagent_stocklist
   Type the COMPANY name at the "username" prompt and the password at the hidden prompt.
   It goes into Windows Credential Manager; nothing is written to the repo.

3. STORE IT FOR THE NIGHTLY RUNNER:
       gh secret set EAGENT_STOCKLIST_USERNAME --repo Jojo140893/spb-ai-property-research-agent
       gh secret set EAGENT_STOCKLIST_PASSWORD --repo Jojo140893/spb-ai-property-research-agent
   gh asks for each value at a hidden prompt. Type it there -- never pass it with --body
   or on the command line, where it lands in the shell history. Both are optional: with
   neither set, the harvest logs one line and skips this source; it never fails the run.

4. READ THE FIRST NIGHT'S CAPTURE. Nobody has seen the signed-in site yet, so the reader
   discovers its JSON API and maps fields by their key names, and it writes down exactly
   what it saw so the mapping can be finished in minutes instead of guessed at:
       gh run list --workflow daily-harvest.yml --limit 1
       gh run download <run id> --name eagent-stocklist-capture --dir capture
   capture/README.txt is the summary (signed in? which endpoint? how many records? stored
   or refused, and why). Then mapping.json (which JSON key fed which field, and every key
   that fed nothing), discovery.json (every /api path found in the page's scripts and what
   each answered), payload_1..3.json (the first three JSON answers, cut to 50 records) and
   home.html / login.html (the pages, every form value blanked). The capture never holds
   cookies, headers or the login, and contact details inside records are masked.

5. A HAND RUN THAT STORES NOTHING:
       python -m sources.eagent_stocklist --dry-run
   Signs in, reads, applies the plausibility gate, writes the capture, prints the summary.

WHAT IT STORES. Rows in the shape every other harvest source yields, under source_channel
"E-Agent Stocklist" -- a separate identity from the E-Agent price-list rows, because the
same lot will often arrive from both. supersede_duplicates.py collapses that twin when
the lot number, the estate, the price and the state agree (see find_superseded).

WHAT IT REFUSES. Nothing is stored unless the read looks like stock: at least
MIN_ROWS rows, at least MIN_GOOD_SHARE of them priced between $150k and $10m and placed
by a suburb or an estate, and every stated state an Australian one. A wrong field mapping
fails that gate loudly rather than writing a thousand rows of nonsense into the catalogue.

NO BROWSER. Measured unauthenticated on 7 Oct 2026: the login page is a plain HTML form
POST to /login (fields `company`, `password`, no scripts); the server is Express behind
Caddy; every non-/api path redirects to /login and every /api/* path answers 401
{"ok":false,"error":"Authentication required"} -- a session-cookie JSON API. A
requests.Session is all it needs.
"""

import csv
import io
import json
import logging
import os
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode, urljoin, urlparse, parse_qsl

import config
from secrets_store import get_credentials
from sources.base import read_date

logger = logging.getLogger("spb.scraper.eagent_stocklist")

CHANNEL = "E-Agent Stocklist"
PORTAL_KEY = "eagent_stocklist"
DEFAULT_BASE = "https://eagent.mekooflow.com"
STOCKLIST_HOST = "eagent.mekooflow.com"
# What the Settings tab says when there is nothing to sign in with (portal_login --check).
MISSING_TEXT = "Needs the EAgent Stocklist company login from your E-Agent BDM"
REFUSED_TEXT = ("the EAgent Stocklist refused the stored company login -- check it with "
                "your E-Agent BDM, then store it again: python setup_credentials.py "
                "eagent_stocklist")

# Where the nightly leaves its notes. logs/ is gitignored and not synced to the bucket,
# so a status file can never be yesterday's read posing as tonight's.
LOG_DIR = Path(config.PROJECT_ROOT) / "logs"
STATUS_PATH = LOG_DIR / "eagent_stocklist_status.json"
CAPTURE_DIR = LOG_DIR / "eagent_stocklist_capture"

# Limits. Generous, because the whole point is to read everything E-Agent shows; hard,
# because an API that ignores its paging parameter would otherwise be read forever.
MAX_JS_FILES = 15
MAX_JS_BYTES = 4_000_000
MAX_PROBES = 14
MAX_PAGES = 600
MAX_RECORDS = 60_000
MAX_EXPORTS = 4
CAPTURE_PAYLOADS = 3
CAPTURE_RECORDS = 50
TIMEOUT = (15, 90)

# The plausibility gate (see the module docstring).
MIN_ROWS = 20
MIN_GOOD_SHARE = 0.70
GATE_PRICE_MIN, GATE_PRICE_MAX = 150_000, 10_000_000
MIN_AU_STATE_SHARE = 0.95
# A PACKAGE NAMES ITS LOT OR ITS HOUSE (repair round 1, 7 Oct 2026). An estate list --
# {name, suburb, state, price}, a "from" price per estate -- is priced and placed on every
# row and passed the gate as 40 "lots" with no lot number, no design and no build price.
# At least this share of rows must name a lot, a house design or a build price.
MIN_NAMED_SHARE = 0.50

DEFAULT_ENDPOINTS = ("/api/packages", "/api/stock", "/api/listings", "/api/search",
                     "/api/properties")
# Where a JSON answer keeps its records, when they are not the whole answer.
LIST_KEYS = ("data", "items", "results", "packages", "rows", "records", "listings",
             "stock", "properties", "docs", "hits")

_AU_STATES = ("NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT")
_STATE_NAMES = {"new south wales": "NSW", "victoria": "VIC", "queensland": "QLD",
                "south australia": "SA", "western australia": "WA", "tasmania": "TAS",
                "northern territory": "NT", "australian capital territory": "ACT"}


def is_stocklist_url(url: Any) -> bool:
    """True for a link on the EAgent Stocklist site, which is behind its login.

    The nightly checkers that open documents anonymously (verify_sheet_positions) skip
    these: the answer would be a 401 or the login page, which says nothing about any lot.
    """
    try:
        return (urlparse(str(url or "")).hostname or "").lower() == STOCKLIST_HOST
    except ValueError:
        return False


# ---------------------------------------------------------------------------------------
# Field mapping -- by KEY NAME, never by position
# ---------------------------------------------------------------------------------------
#
# Nobody has seen a record yet, so every field is found by what its key is called, in
# preference order. A record that names its price "packagePrice" and one that nests it as
# {"price": {"total": ...}} both map: keys are lower-cased with everything but letters and
# digits taken out, and a nested key is its parent's name plus its own ("pricetotal").
# Values are range-checked as well, so a key that means something else (a "total" that is
# a count, a "title" that is the card's headline) fails the check and maps nothing.
_ALIASES = (
    ("price", ("packageprice", "totalpackageprice", "pricepackage", "pricetotal",
               "totalprice", "fullprice", "hlprice", "houseandlandprice", "packagetotal",
               "price", "saleprice", "listprice", "askingprice", "total")),
    ("land_price", ("landprice", "priceland", "landcost", "landvalue", "landpriceamount")),
    ("build_price", ("buildprice", "pricebuild", "houseprice", "pricehouse", "buildcost",
                     "homeprice", "constructionprice")),
    ("lot", ("lotnumber", "lotno", "lotnum", "lot", "lotlabel", "unitnumber", "unitno",
             "unit", "lotunit")),
    ("estate", ("estatename", "estate", "projectname", "project", "developmentname",
                "development", "communityname", "community")),
    ("suburb", ("suburb", "suburbname", "locality", "town", "addresssuburb",
                "locationsuburb", "estatesuburb", "addresslocality")),
    ("state", ("state", "statecode", "addressstate", "locationstate", "estatestate")),
    ("postcode", ("postcode", "postalcode", "zipcode", "zip", "addresspostcode",
                  "locationpostcode", "estatepostcode")),
    ("street", ("streetaddress", "street", "address", "address1", "addressline1",
                "lotaddress", "propertyaddress", "fulladdress", "addressstreet",
                "addressfull", "addressline")),
    ("builder", ("buildername", "builder", "builderbrand", "homebuilder",
                 "buildercompany", "brand")),
    ("design", ("designname", "housedesign", "homedesign", "design", "housename",
                "house", "model", "floorplanname", "designtitle")),
    ("facade", ("facade", "facadename", "elevation")),
    ("bedrooms", ("bedrooms", "bedroom", "beds", "bed", "bedroomcount", "br")),
    ("bathrooms", ("bathrooms", "bathroom", "baths", "bath", "bathroomcount")),
    ("car_spaces", ("carspaces", "carspace", "cars", "car", "garages", "garage",
                    "parking", "carparks")),
    ("land_size", ("landsize", "landarea", "lotsize", "lotarea", "landsqm", "landm2",
                   "sitearea", "blocksize", "land")),
    ("house_size", ("housesize", "housearea", "homesize", "buildarea", "buildsize",
                    "floorarea", "livingarea", "internalarea", "housesqm", "totalarea")),
    ("frontage", ("frontage", "lotfrontage", "frontagem", "lotwidth", "width")),
    ("status", ("availabilitystatus", "availability", "status", "salestatus",
                "stockstatus", "salesstatus", "lotstatus")),
    ("title", ("titlestatus", "titledate", "titled", "title", "registration",
               "registrationdate", "titlerelease", "expectedtitles", "titles")),
    ("listing_url", ("listingurl", "packageurl", "detailsurl", "detailurl", "url", "link",
                     "permalink", "weburl")),
    ("brochure_url", ("brochureurl", "brochure", "flyerurl", "flyer", "pdfurl", "pdf",
                      "marketingflyer")),
    ("floorplan_url", ("floorplanurl", "floorplan", "floorplanpdf", "planurl")),
    ("updated", ("updatedat", "lastupdated", "updated", "modifiedat", "modified",
                 "dateupdated", "updatedon", "lastmodified")),
    ("record_id", ("id", "_id", "packageid", "listingid", "stockid", "uuid", "recordid")),
    ("project_id", ("projectid", "estateid", "developmentid")),
    ("product_type", ("producttype", "propertytype", "packagetype", "type", "category")),
    ("storey", ("storey", "storeys", "story", "stories", "levels")),
    ("headline", ("name", "title", "heading", "packagename", "displayname", "label")),
)
_ALIAS_MAP = dict(_ALIASES)
# Children promoted to a bare key when the record does not already have it at the top:
# {"address": {"suburb": "Wollert"}} supplies "suburb" as well as "addresssuburb". ONLY
# place words. A promoted "price" out of {"land": {"price": 300000}} would be read as the
# PACKAGE price of a lot whose package price is twice that, and builder.name and
# estate.name must not both become "name".
_PROMOTED = frozenset(("suburb", "suburbname", "locality", "town", "state", "statecode",
                       "postcode", "postalcode", "street", "streetaddress", "address1",
                       "addressline1",
                       # and the room counts, which nothing else can be confused with:
                       # {"specs": {"bed": 4, "bath": 2, "car": 2}}
                       "bed", "beds", "bedrooms", "bath", "baths", "bathrooms", "car",
                       "cars", "carspaces", "garage", "garages"))
# Boolean flags that state availability outright.
_AVAILABLE_FLAGS = ("available", "isavailable", "forsale", "onsale")
_SOLD_FLAGS = ("sold", "issold")


def _norm_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key or "").lower())


def _flatten(record: Dict[str, Any]) -> Dict[str, Any]:
    """{normalised key: scalar value} for one record, two levels of nesting deep."""
    out: Dict[str, Any] = {}

    def walk(obj, prefix, depth):
        for k, v in obj.items():
            nk = prefix + _norm_key(k)
            if isinstance(v, dict):
                if depth < 2:
                    walk(v, nk, depth + 1)
            elif isinstance(v, list):
                if v and all(not isinstance(x, (dict, list)) for x in v):
                    out.setdefault(nk, ", ".join(str(x) for x in v if x is not None))
            else:
                out.setdefault(nk, v)

    walk(record, "", 0)
    for k, v in record.items():
        if isinstance(v, dict):
            for ck, cv in v.items():
                n = _norm_key(ck)
                if n in _PROMOTED and not isinstance(cv, (dict, list)):
                    out.setdefault(n, cv)
    return out


def _clean_text(value: Any, limit: int = 120) -> Optional[str]:
    if value is None or isinstance(value, (dict, list)):
        return None
    if isinstance(value, bool):
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    if not s or s.lower() in ("-", "--", "n/a", "na", "null", "none", "undefined", "nil"):
        return None
    return s[:limit]


def _money(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        x = float(value)
    else:
        s = str(value).strip().lower().replace(",", "").replace("$", "").replace("aud", "")
        # The first amount: "From $650,000" and "$650,000 - $700,000" are both 650,000.
        m = re.search(r"(\d+(?:\.\d+)?)\s*(k|m|mil|million)?\b", s)
        if not m:
            return None
        x = float(m.group(1))
        unit = m.group(2) or ""
        if unit == "k":
            x *= 1_000
        elif unit:
            x *= 1_000_000
    if x != x or x <= 0 or x >= 1e9:
        return None
    return x


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        x = float(value)
    else:
        m = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
        if not m:
            return None
        x = float(m.group(0))
    return x if x == x else None


def _count(value: Any) -> Optional[int]:
    x = _number(value)
    if x is None or not (0 <= x <= 20):
        return None
    return int(x)


def _area(value: Any, low: float, high: float) -> Optional[float]:
    x = _number(value)
    if x is None or not (low <= x <= high):
        return None
    return round(x, 2)


def _state(value: Any) -> Tuple[Optional[str], bool]:
    """(AU state code or None, whether a value was stated at all)."""
    s = _clean_text(value, 40)
    if not s:
        return None, False
    up = s.upper().replace(".", "").strip()
    if up in _AU_STATES:
        return up, True
    named = _STATE_NAMES.get(s.lower())
    return (named, True) if named else (None, True)


def _postcode(value: Any) -> Optional[str]:
    s = _clean_text(value, 12)
    if not s:
        return None
    m = re.fullmatch(r"0?(\d{3,4})", s.strip())
    if not m:
        return None
    code = m.group(1).zfill(4)
    return code if 800 <= int(code) <= 7999 else None


def _lot(value: Any) -> Optional[str]:
    s = _clean_text(value, 40)
    if not s:
        return None
    s = re.sub(r"^(?:lot|unit|apt|apartment)\s*[:#.]?\s*", "", s, flags=re.I).strip(" #:-")
    if not s or len(s) > 16:
        return None
    return s


def _url(value: Any, base: str) -> Optional[str]:
    s = _clean_text(value, 600)
    if not s:
        return None
    url = urljoin(base + "/", s)
    return url if url.lower().startswith(("http://", "https://")) else None


# A "title" key often holds the card's headline ("Lot 12 Aurora - Ascot 25"), which is not
# a title status. Only a value that reads like one is taken.
_TITLE_STATUS = re.compile(
    r"\b(un)?titled\b|\b(un)?registered\b|\btitles?\b|\bq[1-4]\b|\b20\d\d\b|"
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\b(mid|late|early)\b|"
    r"\bnow\b|\btb[ac]\b|\bregistration\b", re.I)


def _title(value: Any) -> Optional[str]:
    s = _clean_text(value, 60)
    if not s or len(s) > 40 or not _TITLE_STATUS.search(s):
        return None
    return s


# Status words. The vocabulary every other channel reads (feature_extract) first; then the
# two additions the owner allowed on 7 Oct 2026: a one-letter misspelling of "available"
# ('Avallable', Ceduna Hattan), and DEPOSIT / CONTRACT ISSUED / EXCHANGED, which say the
# lot is gone. EOI, RESALE and 'Exclusive' are Coleen's call and read exactly as they do on
# every other channel: as no stated availability. The raw word stays in source_text.
_GONE_WORDS = re.compile(r"\bdeposit(?:ed| paid| taken)?\b|\bcontracts?\s+issued\b|"
                         r"\bexchanged\b", re.I)


def _one_edit(a: str, b: str) -> bool:
    """True when a and b differ by at most one substitution, insertion or deletion."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(long_)):
        if long_[:i] + long_[i + 1:] == short:
            return True
    return False


def normalise_status(raw: Any) -> Optional[str]:
    if isinstance(raw, bool):
        return None
    s = _clean_text(raw, 60)
    if not s:
        return None
    from sources.feature_extract import parse_availability
    found = parse_availability(s)
    if found:
        return found
    if _GONE_WORDS.search(s):
        return "Not Available"
    word = re.sub(r"[^a-z]", "", s.lower())
    if word != "available" and len(word) >= 8 and _one_edit(word, "available"):
        return "Available"
    return None


def _product(value: Any) -> Optional[str]:
    s = (_clean_text(value, 60) or "").lower()
    if not s:
        return None
    if re.search(r"house\s*(?:&|and|\+)\s*land|\bh\s*&\s*l\b|\bhnl\b|\bpackage", s):
        return "House & Land"
    if re.search(r"townhouse|terrace", s):
        return "Townhouse"
    if re.search(r"apartment", s) or s in ("unit", "units"):
        return "Apartment"
    if re.fullmatch(r"(?:vacant\s+)?land(?:\s+only)?", s):
        return "Land"
    if "commercial" in s:
        return "Commercial"
    return None


def _storey(value: Any) -> Optional[str]:
    s = (_clean_text(value, 30) or "").lower()
    if s in ("1", "1.0", "single", "single storey", "single story", "one"):
        return "SINGLE"
    if s in ("2", "2.0", "double", "double storey", "double story", "two"):
        return "DOUBLE"
    return None


def _money_text(x: float) -> str:
    return "${:,.0f}".format(x)


def _num_text(x: float) -> str:
    return ("%g" % x) if x != int(x) else str(int(x))


def map_record(record: Dict[str, Any], base: str, source_url: str,
               used: Optional[Dict[str, Dict[str, int]]] = None
               ) -> Tuple[Optional[Dict[str, Any]], str]:
    """(row in the harvest's dict shape, "") or (None, why it is not a listing).

    `used` collects {field: {original-ish key: times it supplied the value}} so the
    capture can say which key fed which field.
    """
    flat = _flatten(record) if isinstance(record, dict) else {}
    if not flat:
        return None, "not an object"

    def pick(field, parse):
        for alias in _ALIAS_MAP[field]:
            if alias not in flat:
                continue
            value = parse(flat[alias])
            if value is not None:
                if used is not None:
                    used.setdefault(field, {})
                    used[field][alias] = used[field].get(alias, 0) + 1
                return value
        return None

    price = pick("price", lambda v: (lambda x: x if x and 50_000 <= x <= 20_000_000
                                     else None)(_money(v)))
    land_price = pick("land_price", lambda v: (lambda x: x if x and x >= 10_000
                                               else None)(_money(v)))
    build_price = pick("build_price", lambda v: (lambda x: x if x and x >= 10_000
                                                 else None)(_money(v)))
    if price is None and land_price and build_price:
        price = land_price + build_price
    if not price:
        return None, "no price"
    # Components larger than the whole are not components of it.
    if land_price and land_price >= price:
        land_price = None
    if build_price and build_price >= price:
        build_price = None

    headline = pick("headline", lambda v: _clean_text(v, 120))
    lot = pick("lot", _lot)
    estate = pick("estate", lambda v: _clean_text(v, 80))
    suburb = pick("suburb", lambda v: _clean_text(v, 60))
    state_raw = None
    for alias in _ALIAS_MAP["state"]:
        if alias in flat and _clean_text(flat[alias], 40):
            state_raw = flat[alias]
            if used is not None:
                used.setdefault("state", {})
                used["state"][alias] = used["state"].get(alias, 0) + 1
            break
    state, state_stated = _state(state_raw)
    postcode = pick("postcode", _postcode)
    street = pick("street", lambda v: _clean_text(v, 120))
    builder = pick("builder", lambda v: _clean_text(v, 80))
    design = pick("design", lambda v: _clean_text(v, 80))
    facade = pick("facade", lambda v: _clean_text(v, 60))
    beds = pick("bedrooms", _count)
    baths = pick("bathrooms", _count)
    cars = pick("car_spaces", _count)
    land_size = pick("land_size", lambda v: _area(v, 20, 100_000))
    house_size = pick("house_size", lambda v: _area(v, 20, 2_000))
    frontage = pick("frontage", lambda v: _area(v, 3, 60))
    status_raw = pick("status", lambda v: _clean_text(v, 60))
    status = normalise_status(status_raw)
    if status is None:
        for flag in _AVAILABLE_FLAGS:
            if isinstance(flat.get(flag), bool):
                status = "Available" if flat[flag] else "Not Available"
                break
        for flag in _SOLD_FLAGS:
            if flat.get(flag) is True:
                status = "Sold"
                break
    title = pick("title", _title)
    project_id = pick("project_id", lambda v: _clean_text(v, 60))
    product = pick("product_type", _product)
    storey = pick("storey", _storey)
    updated = pick("updated", lambda v: _clean_text(v, 40))
    # Counted for the capture, and kept on the row (popped before storing) so price-only
    # siblings are numbered in the same order every night (_finish_rows).
    record_id = pick("record_id", lambda v: _clean_text(v, 80))
    links = {f: pick(f, lambda v: _url(v, base))
             for f in ("listing_url", "brochure_url", "floorplan_url")}
    # A link on the Stocklist site itself is behind its login. link_check.py asks every
    # lot page anonymously each night and would only ever get the sign-in page, so those
    # are left out; the card links to the site with a search term instead (provenance.py).
    links = {f: (u if u and not is_stocklist_url(u) else None) for f, u in links.items()}

    # From the headline, only what the structured fields left empty.
    if headline:
        from sources.feature_extract import parse_lot_number, parse_estate
        lot = lot or _lot(parse_lot_number(headline))
        estate = estate or parse_estate(headline)

    # An address that carries its own locality: "5 Smith St, Wollert VIC 3750".
    if street and not suburb:
        m = re.search(r",\s*([A-Za-z][A-Za-z' \-]{2,40}?)\s*,?\s+(NSW|VIC|QLD|SA|WA|TAS|NT|ACT)"
                      r"\b(?:,?\s*(\d{4}))?\s*$", street, re.I)
        if m:
            suburb = m.group(1).strip()
            if not state:
                state, state_stated = m.group(2).upper(), True
            postcode = postcode or _postcode(m.group(3))
            street = street[:m.start()].strip(" ,")
    if suburb and suburb.isupper():
        suburb = suburb.title()

    if not product and (design or build_price):
        # The site is E-Agent's "Search All H&L": a record naming a house design or a
        # build price is a house-and-land package. Nothing else is assumed.
        product = "House & Land"
    house_design = " - ".join(x for x in (design, facade) if x) or None

    place = " ".join(x for x in (suburb, state, postcode) if x)
    lot_part = ("Lot %s" % lot) if lot and not re.match(r"(?i)^(lot|unit)\b", lot) else lot
    if street and lot and re.match(r"(?i)^\s*(lot|unit)\b", street):
        lot_part = None
    label = ", ".join(x for x in (lot_part, street or estate, place) if x)
    if not label:
        label = headline or (builder or "") + " package"

    # THE ROW'S OWN LINE, composed from the record's own values in a fixed order. It is
    # what identity reads (database._variant_key: the first 100 characters, prices and
    # status words stripped), so the stable facts come first and the volatile ones last.
    #   * Every price printed here is the package price or one of its components, never
    #     more, so the search's price evidence (price_in_text, text_price_max) reads it.
    #   * The locality comes AFTER the design and the sizes. repair_rows clears any stored
    #     postcode that feature_extract.parse_postcode cannot reproduce from this text, and
    #     that parser takes the LAST four-digit number it trusts: a design called
    #     "Hamilton 2400" printed after "Wollert VIC 3750" would cost the row its postcode
    #     every night. Nothing after the locality prints four digits in a row.
    #   * No record id, for the same reason: an id like "4012" reads as a QLD postcode.
    spec = " ".join(x for x in (
        ("%d bed" % beds) if beds is not None else None,
        ("%d bath" % baths) if baths is not None else None,
        ("%d car" % cars) if cars is not None else None) if x)
    bits = [("Lot %s" % lot) if lot else None, estate, street, builder, house_design,
            spec or None,
            ("land %s m2" % _num_text(land_size)) if land_size else None,
            ("house %s m2" % _num_text(house_size)) if house_size else None,
            ("frontage %s m" % _num_text(frontage)) if frontage else None,
            storey, product, title, place or None,
            ("land " + _money_text(land_price)) if land_price else None,
            ("build " + _money_text(build_price)) if build_price else None,
            _money_text(price), status_raw]
    source_text = " | ".join(str(b) for b in bits if b)

    row = {
        "source_channel": CHANNEL,
        "lot_address": label[:120],
        "suburb": suburb or "",
        "state": state or "",
        "postcode": postcode,
        "lot_number": lot,
        "estate_name": estate,
        "builder_name": builder or "",
        "builder_source": "eagent stocklist builder field" if builder else None,
        "attribution_scope": "builder" if builder else "project",
        "house_design": house_design,
        "advertised_package_price": float(price),
        "land_price": land_price,
        "build_price": build_price,
        "bedrooms": beds,
        "bathrooms": baths,
        "car_spaces": cars,
        "land_size_sqm": land_size,
        "house_size_sqm": house_size,
        "frontage_m": frontage,
        "storey": storey,
        "product_type": product,
        "title_status": title or "",
        "availability_status": status,
        "listing_url": links["listing_url"],
        "brochure_url": links["brochure_url"],
        "floorplan_url": links["floorplan_url"],
        "source_project_id": project_id,
        # The state the source itself states, in the slot E-Agent's rows give their page:
        # the resolver weighs it with the suburb and the postcode, as it does theirs.
        "source_state_hint": state or None,
        # The DOCUMENT is the list endpoint, shared by every row it returned -- not one
        # url per lot. repair_rows groups a re-listing channel by source_url to decide
        # that a lot the list stopped carrying has gone, and the search's admission rules
        # read the same grouping; a url per lot would make every lot a list of one, and
        # neither could ever fire.
        "source_url_or_ref": source_url,
        "stocklist_file": source_url,
        "source_text": source_text,
        "source_updated": updated,
        # The run's day, not this row's moment: see sources.base.read_date.
        "date_checked": read_date(),
        "extraction_confidence": 1.0,
        "verified": True,
        "_state_stated": state_stated,
        "_record_id": record_id or "",
    }
    return row, ""


# ---------------------------------------------------------------------------------------
# The plausibility gate
# ---------------------------------------------------------------------------------------

def plausibility(rows: List[Dict[str, Any]], records_seen: int) -> Tuple[bool, str, Dict[str, Any]]:
    """(store?, why, numbers). Nothing is stored unless the read looks like stock."""
    n = len(rows)
    good = sum(1 for r in rows
               if GATE_PRICE_MIN <= float(r.get("advertised_package_price") or 0) <= GATE_PRICE_MAX
               and (str(r.get("suburb") or "").strip() or str(r.get("estate_name") or "").strip()))
    stated = [r for r in rows if r.get("_state_stated")]
    au = sum(1 for r in stated if r.get("state") in _AU_STATES)
    named = sum(1 for r in rows if str(r.get("lot_number") or "").strip()
                or str(r.get("house_design") or "").strip() or r.get("build_price"))
    stats = {"records_seen": records_seen, "rows_mapped": n, "priced_and_placed": good,
             "states_stated": len(stated), "states_australian": au,
             "named_lots_or_designs": named}
    if n < MIN_ROWS:
        return False, ("only %d row(s) mapped from %d record(s); at least %d are needed "
                       "before anything is stored" % (n, records_seen, MIN_ROWS)), stats
    share = good / n
    stats["priced_and_placed_share"] = round(share, 3)
    if share < MIN_GOOD_SHARE:
        return False, ("only %.0f%% of %d rows have a price between $150k and $10m AND a "
                       "suburb or an estate (need %.0f%%) -- the field mapping is probably "
                       "wrong" % (share * 100, n, MIN_GOOD_SHARE * 100)), stats
    if named < n * MIN_NAMED_SHARE:
        return False, ("only %d of %d rows name a lot number, a house design or a build "
                       "price (need %.0f%%) -- this reads like a list of estates or "
                       "projects with a 'from' price, not of packages" % (
                           named, n, MIN_NAMED_SHARE * 100)), stats
    if stated and au / len(stated) < MIN_AU_STATE_SHARE:
        return False, ("%d of %d stated states are not Australian state codes -- the "
                       "'state' key probably means something else" % (len(stated) - au,
                                                                       len(stated))), stats
    return True, "%d of %d rows priced and placed" % (good, n), stats


# ---------------------------------------------------------------------------------------
# Pages: forms, scripts, links
# ---------------------------------------------------------------------------------------

class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms: List[Dict[str, Any]] = []
        self.scripts: List[str] = []
        self.inline: List[str] = []
        self.links: List[str] = []
        self.title = ""
        self._form = None
        self._in_script = False
        self._in_title = False
        self._buf: List[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "form":
            self._form = {"action": a.get("action", ""), "method": a.get("method", "get"),
                          "inputs": []}
            self.forms.append(self._form)
        elif tag in ("input", "button", "select", "textarea") and self._form is not None:
            if a.get("name"):
                self._form["inputs"].append({"name": a["name"],
                                             "type": (a.get("type") or "text").lower(),
                                             "value": a.get("value", "")})
        elif tag == "script":
            if a.get("src"):
                self.scripts.append(a["src"])
            else:
                self._in_script, self._buf = True, []
        elif tag == "link" and a.get("href", "").split("?")[0].endswith(".js"):
            self.scripts.append(a["href"])
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        elif tag == "script" and self._in_script:
            self.inline.append("".join(self._buf))
            self._in_script = False
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_script:
            self._buf.append(data)
        elif self._in_title:
            self.title += data


def _parse_page(html: str) -> _PageParser:
    p = _PageParser()
    try:
        p.feed(html or "")
        p.close()
    except Exception:                                                   # noqa: BLE001
        pass
    return p


_API_LITERAL = re.compile(r"""["'`](?:https?://[^"'`\s/]+)?(/?api/[A-Za-z0-9_\-./:]*)""")
_JS_REF = re.compile(r"""["'`]((?:\.{0,2}/)?[A-Za-z0-9_\-./]+\.m?js)(?:\?[^"'`]*)?["'`]""")
_EXPORT_WORD = re.compile(r"export|download|\.csv\b|\.xlsx?\b", re.I)
_AUTH_SEGMENTS = frozenset(("login", "logout", "auth", "session", "sessions", "me", "user",
                            "users", "password", "token", "csrf", "health", "ping",
                            "version", "config", "settings", "profile", "account"))
_ENDPOINT_WORDS = (("package", 4), ("stock", 4), ("listing", 4), ("lot", 2),
                   ("search", 3), ("propert", 3), ("inventory", 3), ("estate", 1),
                   ("export", -2), ("download", -2))


def _api_paths(text: str) -> List[str]:
    """Every '/api/...' literal in a script or a page, cleaned to a path."""
    out: List[str] = []
    for m in _API_LITERAL.finditer(text or ""):
        p = "/" + m.group(1).lstrip("/")
        segs = [s for s in p.split("/") if s]
        # "/api/packages/:id" and "/api/packages/" + id are the collection.
        while segs and (segs[-1].startswith(":") or segs[-1] in (".", "..")):
            segs.pop()
        p = "/" + "/".join(segs)
        p = p.rstrip(".")
        if len(segs) < 2 or any(s.lower() in _AUTH_SEGMENTS for s in segs[1:]):
            continue
        if p not in out:
            out.append(p)
    return out


def _rank_endpoints(discovered: Iterable[str], defaults: Iterable[str] = DEFAULT_ENDPOINTS
                    ) -> List[str]:
    """Discovered paths first -- the site's own code says they exist -- the likeliest list
    endpoints among them leading; then the common names it did not mention."""
    found = list(dict.fromkeys(discovered))

    def score(p):
        low = p.lower()
        return -sum(w for word, w in _ENDPOINT_WORDS if word in low)
    ranked = sorted(found, key=lambda p: (score(p), found.index(p)))
    return ranked + [p for p in defaults if p not in ranked]


# ---------------------------------------------------------------------------------------
# JSON shapes
# ---------------------------------------------------------------------------------------

def _ci_get(obj: Dict[str, Any], key: str):
    for k, v in obj.items():
        if str(k).lower() == key:
            return v
    return None


def _is_record_list(v: Any) -> bool:
    return (isinstance(v, list) and len(v) > 0
            and sum(1 for x in v if isinstance(x, dict) and len(x) >= 3) >= max(1, len(v) // 2))


def find_records(payload: Any) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """(records, where) -- the array of objects in a JSON answer, or (None, "")."""
    if _is_record_list(payload):
        return [x for x in payload if isinstance(x, dict)], "(top level)"
    if not isinstance(payload, dict):
        return None, ""
    for key in LIST_KEYS:
        v = _ci_get(payload, key)
        if _is_record_list(v):
            return [x for x in v if isinstance(x, dict)], key
        if isinstance(v, dict):
            for k2 in LIST_KEYS:
                v2 = _ci_get(v, k2)
                if _is_record_list(v2):
                    return [x for x in v2 if isinstance(x, dict)], key + "." + k2
    # Anything else must look like stock: most of its objects carry a price-like key.
    best = None
    for k, v in payload.items():
        if _is_record_list(v):
            priced = sum(1 for x in v if isinstance(x, dict)
                         and any("price" in _norm_key(kk) for kk in _flatten(x)))
            if priced * 2 >= len(v) and (best is None or len(v) > len(best[0])):
                best = ([x for x in v if isinstance(x, dict)], str(k))
    return best if best else (None, "")


_TOTAL_KEYS = ("total", "totalcount", "totalitems", "totalrecords", "totalresults",
               "recordstotal", "totalrows", "count", "totalhits")
_PAGES_KEYS = ("totalpages", "pages", "pagecount", "lastpage", "numpages")
_NEXT_KEYS = ("next", "nextpage", "nextpageurl", "nexturl", "nextlink")
_CURSOR_KEYS = ("nextcursor", "cursor", "nexttoken", "continuationtoken", "after")
_META_KEYS = ("meta", "pagination", "paging", "pageinfo", "page", "links", "_links")


def paging_meta(payload: Any, n_items: int) -> Dict[str, Any]:
    """What a JSON answer says about the rest of the list: total, pages, next, cursor."""
    out: Dict[str, Any] = {}
    if not isinstance(payload, dict):
        return out
    scopes = [payload] + [v for k, v in payload.items()
                          if _norm_key(k) in _META_KEYS and isinstance(v, dict)]
    for scope in scopes:
        flat = {_norm_key(k): v for k, v in scope.items()}
        for k in _TOTAL_KEYS:
            v = flat.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > n_items:
                out.setdefault("total", int(v))
        for k in _PAGES_KEYS:
            v = flat.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 1:
                out.setdefault("pages", int(v))
        for k in _NEXT_KEYS:
            v = flat.get(k)
            if isinstance(v, dict):
                v = v.get("href") or v.get("url")
            if isinstance(v, str) and v.strip():
                out.setdefault("next_url", v.strip())
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                out.setdefault("next_page", int(v))
        for k in _CURSOR_KEYS:
            v = flat.get(k)
            if isinstance(v, str) and v.strip() and k != "after":
                out.setdefault("cursor", v.strip())
        # The page size, under the name the API itself uses ("perPage", "page_size"), so
        # the next request asks in its own words.
        for orig, v in scope.items():
            if (_norm_key(orig) in ("limit", "perpage", "pagesize", "size")
                    and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0):
                out.setdefault("limit_param", str(orig))
                out.setdefault("limit", int(v))
    return out


def _fingerprint(record: Dict[str, Any]) -> str:
    flat = _flatten(record)
    for alias in _ALIAS_MAP["record_id"]:
        if flat.get(alias) not in (None, ""):
            return "id:" + str(flat[alias])
    return "json:" + json.dumps(record, sort_keys=True, default=str)


# ---------------------------------------------------------------------------------------
# Spreadsheet exports
# ---------------------------------------------------------------------------------------

def tabular_records(data: bytes) -> List[Dict[str, Any]]:
    """Rows of an exported CSV/XLSX as {header: value} dicts, or [] if no header is found.

    The header is the first row that names at least three of the fields this module
    maps. Mapping by header beats reading the row text, because the export says which
    column is the state and which is the builder; extract_stocklist is the fallback.
    """
    rows: List[List[Any]] = []
    if data[:2] == b"PK":
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            ws = wb.worksheets[0]
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            wb.close()
        except Exception as exc:                                        # noqa: BLE001
            logger.info("EAgent Stocklist: export is not a readable workbook (%s)", exc)
            return []
    else:
        text = None
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None or text.lstrip()[:1] in ("<", "{", "["):
            return []
        delim = "\t" if text[:4000].count("\t") > text[:4000].count(",") else ","
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    known = {a for _f, aliases in _ALIASES for a in aliases}
    for i, row in enumerate(rows[:30]):
        names = [_clean_text(c, 60) or "" for c in row]
        if sum(1 for n in names if _norm_key(n) in known) >= 3:
            out = []
            for r in rows[i + 1:]:
                rec = {names[j]: r[j] for j in range(min(len(names), len(r)))
                       if names[j] and r[j] not in (None, "")}
                if len(rec) >= 3:
                    out.append(rec)
            return out
    return []


# ---------------------------------------------------------------------------------------
# The capture -- what the first signed-in night saw, with nothing secret in it
# ---------------------------------------------------------------------------------------

_SECRET_KEY = re.compile(r"pass|secret|token|cookie|session|auth|csrf|xsrf|api_?key|bearer|"
                         r"signature|jwt|credential", re.I)
_CONTACT_KEY = re.compile(r"e-?mail|phone|mobile|contact|agent|bdm|consultant|salesperson|"
                          r"owner|manager", re.I)
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")
_PHONE = re.compile(r"(?:\+?61|\b0)[\s\-]?[2-478](?:[\s\-]?\d){8}\b|\b1[38]00[\s\-]?\d{3}[\s\-]?\d{3}\b")
_INPUT_VALUE = re.compile(r"(<input\b[^>]*?\bvalue\s*=\s*)(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_TEXTAREA = re.compile(r"(<textarea\b[^>]*>)(.*?)(</textarea>)", re.I | re.S)
_META_TOKEN = re.compile(r"(<meta\b[^>]*?(?:csrf|token|xsrf)[^>]*?\bcontent\s*=\s*)"
                         r"(\"[^\"]*\"|'[^']*')", re.I)
_SCRIPT_SECRET = re.compile(r"""((?:token|csrf|xsrf|secret|session|password|apikey|api_key)"""
                            r"""["']?\s*[:=]\s*)(["'`])[^"'`]*\2""", re.I)
# REPAIR ROUND 1 (7 Oct 2026, data verifier): the capture still kept a page's inline
# session state ("jwt", "auth": "Bearer ...", "sid", "refresh", headers.Authorization,
# "X-Api-Key"), document.cookie='connect.sid=...', a data-api-key attribute, a download
# link's ?token=, a login form posting to '/login?_csrf=...', presigned S3 brochure links
# (X-Amz-Signature) and the BDM's phone numbers. The capture is a CI artifact kept 14 days.
#   * any quoted value after a key that names a credential, however the key is spelt;
_SECRET_WORDS = (r"token|csrf|xsrf|secret|session|passw(?:or)?d|api[_\-]?key|jwt|auth\w*|"
                 r"bearer|sid|refresh|cookie|signature|credential|access[_\-]?key")
_SCRIPT_KEYED = re.compile(
    r"""((?:["'][\w\-.]*(?:%s)[\w\-.]*["']|\b[\w\-.]*(?:%s)[\w\-.]*)\s*[:=]\s*)"""
    r"""(["'`])[^"'`]*\2""" % (_SECRET_WORDS, _SECRET_WORDS), re.I)
#   * a bearer token and a JWT wherever they stand;
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*", re.I)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*")
#   * a data-* attribute named for a credential;
_DATA_SECRET = re.compile(r"""(\bdata-[\w\-]*(?:%s)[\w\-]*\s*=\s*)("[^"]*"|'[^']*'|[^\s>]+)"""
                          % _SECRET_WORDS, re.I)
#   * and the query string of every link -- a link's path says what it is; its query is
#     where sites put tokens and signatures.
_URL_QUERY = re.compile(r"((?:https?:)?//[^\s\"'<>?#`]+|(?<![\w/])/[\w\-./%]*)\?[^\s\"'<>`#]*",
                        re.I)
# REPAIR ROUND 2 (7 Oct 2026, data verifier) -- two more ways a secret reached the capture:
#   * a value handed to browser storage or a header call rather than written key: value,
#     `localStorage.setItem("authToken", "...")`, `headers.set('Authorization', '...')`:
#     anything put in session or local storage is the page's session state, whatever its
#     key, and a set/append call whose first argument names a credential carries one;
_STORAGE_VALUE = re.compile(
    r"""(\b(?:local|session)Storage\s*\.\s*setItem\s*\(\s*(["'`])[^"'`]*\2\s*,\s*)"""
    r"""(["'`])[^"'`]*\3""", re.I)
_CALL_KEYED = re.compile(
    r"""(\.\s*(?:set|append|setRequestHeader)\s*\(\s*(["'`])[^"'`]*(?:%s)[^"'`]*\2\s*,\s*)"""
    r"""(["'`])[^"'`]*\3""" % _SECRET_WORDS, re.I)
#   * and the label a payload is filed under, which is the request that fetched it -- the
#     path and the parameters of the API's own "next" link, ?access_token= and all.
_PARAM_KEYED = re.compile(r"(\b[\w\-.]*(?:%s)[\w\-.]*=)[^&\s#]*" % _SECRET_WORDS, re.I)


def scrub_links_and_tokens(text: str) -> str:
    """`text` with every link's query string, bearer token and JWT taken out."""
    text = _URL_QUERY.sub(lambda m: m.group(1) + "?[query removed]", str(text or ""))
    text = _BEARER.sub("Bearer [redacted]", text)
    return _JWT.sub("[jwt]", text)


class Capture:
    """Writes logs/eagent_stocklist_capture/. Never cookies, headers or the login."""

    def __init__(self, folder: Optional[Path], secrets: Iterable[str] = ()):
        self.folder = folder
        self.secrets = [s for s in secrets if s and len(s) >= 3]
        self.discovery: Dict[str, Any] = {"probes": []}
        self.payloads = 0
        self.mapping: Dict[str, Any] = {}
        self.summary: List[str] = []

    def _scrub_text(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, "[redacted]")
        return text

    def scrub_html(self, html: str) -> str:
        html = _INPUT_VALUE.sub(lambda m: m.group(1) + '""', html or "")
        html = _TEXTAREA.sub(lambda m: m.group(1) + m.group(3), html)
        html = _META_TOKEN.sub(lambda m: m.group(1) + '""', html)
        html = _SCRIPT_SECRET.sub(lambda m: m.group(1) + m.group(2) + m.group(2), html)
        html = _SCRIPT_KEYED.sub(lambda m: m.group(1) + m.group(2) + m.group(2), html)
        html = _STORAGE_VALUE.sub(lambda m: m.group(1) + m.group(3) + m.group(3), html)
        html = _CALL_KEYED.sub(lambda m: m.group(1) + m.group(3) + m.group(3), html)
        html = _DATA_SECRET.sub(lambda m: m.group(1) + '""', html)
        html = scrub_links_and_tokens(html)
        html = _EMAIL.sub("[email]", html)
        html = _PHONE.sub("[phone]", html)
        return self._scrub_text(html)

    def scrub_value(self, value: Any, key: str = "") -> Any:
        if key and _SECRET_KEY.search(key):
            return "[redacted]"
        if key and _CONTACT_KEY.search(key) and not isinstance(value, (dict, list)):
            return "[contact]"
        if isinstance(value, dict):
            return {k: self.scrub_value(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            out = [self.scrub_value(v, key) for v in value[:CAPTURE_RECORDS]]
            if len(value) > CAPTURE_RECORDS:
                out.append({"_truncated": "%d more not kept" % (len(value) - CAPTURE_RECORDS)})
            return out
        if isinstance(value, str):
            v = _EMAIL.sub("[email]", value)
            v = _PHONE.sub("[phone]", v)
            v = scrub_links_and_tokens(v)
            return self._scrub_text(v)
        return value

    def page(self, name: str, html: str) -> None:
        self._write(name, self.scrub_html(html))

    def scrub_label(self, label: str) -> str:
        """A payload's label -- "GET /api/v2/lots p=2&access_token=..." -- with every
        credential-named parameter's value, link query, token, e-mail and phone taken out."""
        text = _PARAM_KEYED.sub(lambda m: m.group(1) + "[redacted]", str(label or ""))
        text = _EMAIL.sub("[email]", scrub_links_and_tokens(text))
        return self._scrub_text(_PHONE.sub("[phone]", text))

    def payload(self, label: str, data: Any) -> None:
        if self.payloads >= CAPTURE_PAYLOADS:
            return
        self.payloads += 1
        self._write("payload_%d.json" % self.payloads,
                    json.dumps({"from": self.scrub_label(label),
                                "body": self.scrub_value(data)}, indent=1, default=str))

    def _write(self, name: str, text: str) -> None:
        if self.folder is None:
            return
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / name).write_text(text, encoding="utf-8")
        except OSError as exc:
            logger.warning("EAgent Stocklist: could not write capture %s: %s", name, exc)

    def reset(self) -> None:
        """Tonight's capture only: a stale file from an earlier night must not be read as
        what this run saw."""
        if self.folder is None or not self.folder.is_dir():
            return
        for f in self.folder.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                except OSError:
                    pass

    def finish(self) -> None:
        self._write("discovery.json", self._scrub_text(
            json.dumps(self.scrub_value(self.discovery), indent=1, default=str)))
        self._write("mapping.json", self._scrub_text(
            json.dumps(self.scrub_value(self.mapping), indent=1, default=str)))
        self._write("README.txt", self._scrub_text("\n".join(self.summary) + "\n"))


# ---------------------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------------------

class EAgentStocklistSource:
    """Reads E-Agent's EAgent Stocklist site into harvest rows. See the module runbook."""

    channel_name = CHANNEL

    def __init__(self, base_url: Optional[str] = None, session: Any = None,
                 capture_dir: Any = "default", status_path: Any = "default",
                 throttle: Optional[float] = None):
        self.base = (base_url or os.getenv("EAGENT_STOCKLIST_URL") or DEFAULT_BASE).rstrip("/")
        self.username, self.password, self.cred_source = get_credentials(PORTAL_KEY)
        self._http = session
        self.capture_dir = CAPTURE_DIR if capture_dir == "default" else (
            Path(capture_dir) if capture_dir else None)
        self.status_path = STATUS_PATH if status_path == "default" else (
            Path(status_path) if status_path else None)
        if throttle is None:
            try:
                throttle = float(os.getenv("SPB_EAGENT_STOCKLIST_THROTTLE", "0.5"))
            except ValueError:
                throttle = 0.5
        self.throttle = max(0.0, throttle)
        self._requests = 0
        # What happened, for the harvest summary, the Settings tab and the capture.
        self.state = "not_run"     # skipped | refused | unreachable | no_endpoint |
        #                            gate_refused | ok | error
        self.why = ""
        self.signed_in = False
        self.endpoint = ""
        self.records_seen = 0
        self.truncated = False
        # WHETHER THE WALK READ THE WHOLE LIST (third review of the retirement ledger, 25 Oct
        # 2026). `truncated` is set at the record and page caps alone, so a walk that ended
        # early read as "ok": a page fetch that failed, a server that ignored page= and
        # every offset, "the API says 120 record(s), 60 were read" (a log warning only).
        # The ledger took that line as proof the list was whole and retired the lots past
        # the stopping point. `total_stated` is the site's own count of its records (None
        # when it states none) and `short_why` says why the walk is known to have stopped
        # early; the harvest writes both into the manifest's search line, and only a stated
        # total that was met proves the read (list_ledger.Tonight._search).
        self.total_stated: Optional[int] = None
        self.short_why = ""
        self._page_failures = 0
        self.gate: Dict[str, Any] = {}
        self.capture = Capture(self.capture_dir, (self.username, self.password))

    @property
    def configured(self) -> bool:
        return bool(self.username and self.password)

    # -- HTTP --------------------------------------------------------------------------

    def _session(self):
        if self._http is None:
            import requests
            s = requests.Session()
            try:
                from sources.remote_stocklist import UA
                s.headers["User-Agent"] = UA
            except Exception:                                           # noqa: BLE001
                pass
            try:
                from urllib3.util.retry import Retry
                from requests.adapters import HTTPAdapter
                retry = Retry(total=3, backoff_factor=2,
                              status_forcelist=(429, 500, 502, 503, 504),
                              allowed_methods=frozenset(("GET",)), raise_on_status=False)
                s.mount("https://", HTTPAdapter(max_retries=retry))
                s.mount("http://", HTTPAdapter(max_retries=retry))
            except Exception:                                           # noqa: BLE001
                pass
            self._http = s
        return self._http

    def _pause(self):
        if self._requests and self.throttle:
            time.sleep(self.throttle)
        self._requests += 1

    def _get(self, path_or_url: str, params: Optional[Dict[str, Any]] = None, json_ok=False):
        self._pause()
        url = path_or_url if "://" in path_or_url else self.base + path_or_url
        headers = {"Accept": "application/json, text/plain, */*"} if json_ok else {}
        return self._session().get(url, params=params, headers=headers, timeout=TIMEOUT,
                                   allow_redirects=True)

    def _same_origin(self, url: str) -> bool:
        return urlparse(url).netloc.lower() == urlparse(self.base).netloc.lower()

    @staticmethod
    def _path_of(resp) -> str:
        return urlparse(getattr(resp, "url", "") or "").path.rstrip("/") or "/"

    # -- the run -----------------------------------------------------------------------

    def search(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Every package the site lists, mapped, or [] -- never raises."""
        if not self.configured:
            self.state, self.why = "skipped", MISSING_TEXT
            logger.info("EAgent Stocklist: no company login stored -- skipped. %s, then "
                        "run: python setup_credentials.py eagent_stocklist", MISSING_TEXT)
            self._write_status()
            return []
        self.capture.reset()
        self.total_stated, self.short_why, self._page_failures = None, "", 0
        self.capture.summary.append("EAgent Stocklist capture, %s" %
                                    datetime.now().strftime("%d/%m/%Y %H:%M"))
        self.capture.summary.append("site: %s" % urlparse(self.base).netloc)
        rows: List[Dict[str, Any]] = []
        try:
            rows = self._run()
        except Exception as exc:                                        # noqa: BLE001
            self.state, self.why = "error", "%s: %s" % (type(exc).__name__, exc)
            logger.exception("EAgent Stocklist: the read failed -- nothing stored: %s", exc)
            rows = []
        self.capture.summary.append("outcome: %s -- %s" % (self.state, self.why))
        self.capture.summary.append("rows handed to the harvest: %d" % len(rows))
        self.capture.summary.append(
            "files: discovery.json (endpoints found and what each answered), mapping.json "
            "(which key fed which field, and the keys that fed nothing), payload_*.json "
            "(the first JSON answers, cut to %d records), login.html / home.html (the pages, "
            "form values blanked)." % CAPTURE_RECORDS)
        self.capture.finish()
        self._write_status(len(rows))
        return rows

    def _run(self) -> List[Dict[str, Any]]:
        home_html = self._login()
        if home_html is None:
            return []
        self.signed_in = True
        page = _parse_page(home_html)
        self.capture.page("home.html", home_html)
        self.capture.discovery["home_title"] = page.title.strip()[:120]

        api_paths, exports = self._discover(home_html, page)
        candidates = _rank_endpoints(api_paths)
        self.capture.discovery["api_paths_found"] = api_paths
        self.capture.discovery["export_links_found"] = exports
        self.capture.discovery["candidate_order"] = candidates
        self.capture.summary.append("api paths found in the page's scripts: %d; export "
                                    "links: %d" % (len(api_paths), len(exports)))

        # PREFER THE EXPORT FILE when the site offers one: one request for the whole list.
        for url in exports[:MAX_EXPORTS]:
            rows = self._read_export(url)
            if rows is not None:
                return rows

        found = self._probe(candidates)
        if not found:
            self.state = "no_endpoint"
            self.why = ("signed in, but none of %d candidate endpoints answered with a "
                        "list of records" % len(candidates))
            logger.warning("EAgent Stocklist: %s -- see logs/eagent_stocklist_capture/"
                           "discovery.json", self.why)
            return []
        path, params, payload, records, where = found
        self.endpoint = path
        self.capture.discovery["chosen"] = {"path": path, "params": params,
                                            "records_under": where}
        records = self._read_all(path, params, payload, records)
        self.records_seen = len(records)
        source_url = self.base + path
        return self._map_and_gate(records, source_url, "json %s" % path)

    # -- login -------------------------------------------------------------------------

    def _login(self) -> Optional[str]:
        """The signed-in home page's HTML, or None (state and why are set)."""
        try:
            resp = self._get("/login")
        except Exception as exc:                                        # noqa: BLE001
            self.state, self.why = "unreachable", "the login page did not load (%s)" % (
                type(exc).__name__)
            logger.error("EAgent Stocklist: %s", self.why)
            return None
        html = resp.text or ""
        self.capture.page("login.html", html)
        form = next((f for f in _parse_page(html).forms
                     if any(i["type"] == "password" for i in f["inputs"])), None)
        fields = (form or {}).get("inputs", [])
        self.capture.discovery["login_form"] = {
            "found": form is not None,
            "action": (form or {}).get("action", ""),
            "method": (form or {}).get("method", ""),
            "fields": [{"name": i["name"], "type": i["type"]} for i in fields]}
        if form is None and self._path_of(resp) != "/login":
            # Already signed in (a session the site remembered). Nothing to submit.
            return html
        data: Dict[str, str] = {}
        company_field = password_field = None
        for i in fields:
            if i["type"] == "password" and password_field is None:
                password_field = i["name"]
            elif i["type"] in ("hidden",):
                data[i["name"]] = i["value"]
            elif i["type"] in ("text", "email", "") and company_field is None:
                company_field = i["name"]
        for i in fields:   # a field NAMED for the company beats the first text box
            if i["type"] in ("text", "email", "") and re.search(
                    r"company|org|account|user|login|email", i["name"], re.I):
                company_field = i["name"]
                break
        data[company_field or "company"] = self.username
        data[password_field or "password"] = self.password
        action = urljoin(resp.url or (self.base + "/login"),
                         (form or {}).get("action") or "/login")
        if not self._same_origin(action):
            self.state, self.why = "refused", "the login form posts off-site; not sent"
            logger.error("EAgent Stocklist: %s", self.why)
            return None
        try:
            self._pause()
            posted = self._session().post(action, data=data, timeout=TIMEOUT,
                                          allow_redirects=True)
            home = self._get("/")
        except Exception as exc:                                        # noqa: BLE001
            self.state, self.why = "unreachable", "the sign-in did not complete (%s)" % (
                type(exc).__name__)
            logger.error("EAgent Stocklist: %s", self.why)
            return None
        landed = self._path_of(home)
        # Back on the sign-in page, or shown the sign-in form again: refused. A password
        # field alone is not enough -- a signed-in page can carry a change-password form --
        # so the form has to be the login's own (it asks for the company field again).
        again = any(any(i["type"] == "password" for i in f["inputs"])
                    and any(i["name"] == (company_field or "company") for i in f["inputs"])
                    for f in _parse_page(home.text or "").forms)
        if landed == "/login" or again:
            self.state = "refused"
            self.why = ("the site did not accept the stored company login (HTTP %s, back "
                        "on the sign-in page)" % getattr(posted, "status_code", "?"))
            logger.error("EAgent Stocklist: %s. %s", self.why, REFUSED_TEXT)
            self.capture.summary.append("sign-in REFUSED -- nothing was read")
            return None
        logger.info("EAgent Stocklist: signed in (credentials from %s).", self.cred_source)
        self.capture.summary.append("signed in: yes (landed on %s)" % landed)
        return home.text or ""

    # -- discovery ---------------------------------------------------------------------

    def _discover(self, home_html: str, page: _PageParser) -> Tuple[List[str], List[str]]:
        texts = [home_html] + page.inline
        queue = [urljoin(self.base + "/", s) for s in page.scripts]
        seen_js, fetched = set(), []
        while queue and len(fetched) < MAX_JS_FILES:
            url = queue.pop(0)
            if url in seen_js or not self._same_origin(url):
                continue
            seen_js.add(url)
            try:
                r = self._get(url)
            except Exception as exc:                                    # noqa: BLE001
                self.capture.discovery.setdefault("scripts_failed", []).append(
                    [urlparse(url).path, type(exc).__name__])
                continue
            body = (r.content or b"")[:MAX_JS_BYTES].decode("utf-8", "replace")
            fetched.append(urlparse(url).path)
            texts.append(body)
            for m in _JS_REF.finditer(body):          # chunks a bundle imports
                queue.append(urljoin(url, m.group(1)))
        self.capture.discovery["scripts_read"] = fetched
        api: List[str] = []
        for t in texts:
            for p in _api_paths(t):
                if p not in api:
                    api.append(p)
        exports: List[str] = []
        for href in page.links + [p for p in api if _EXPORT_WORD.search(p)]:
            url = urljoin(self.base + "/", href)
            # A per-package brochure "download" is not the stock list.
            if re.search(r"\.(pdf|jpe?g|png|gif|docx?|zip)(\?|$)", href, re.I):
                continue
            if (self._same_origin(url) and _EXPORT_WORD.search(href)
                    and url not in exports):
                exports.append(url)
        api = [p for p in api if not _EXPORT_WORD.search(p)]
        return api, exports

    def _json(self, resp) -> Any:
        ctype = str(getattr(resp, "headers", {}).get("Content-Type", "")).lower()
        text = (resp.text or "").lstrip()
        if "json" not in ctype and text[:1] not in ("{", "["):
            return None
        try:
            return resp.json()
        except Exception:                                               # noqa: BLE001
            try:
                return json.loads(text)
            except ValueError:
                return None

    def _probe(self, candidates: List[str]):
        """(path, params, payload, records, where) for the first list endpoint, or None.

        Plain GET first, in the order given. Only an endpoint that answered but listed
        nothing (a 200 with no records, a 400 or 422 asking for parameters) is asked again
        with paging parameters, and a search endpoint once more with an empty query: a 404
        is not worth a second request.
        """
        tries = [{}, {"page": 1, "limit": 100}, {"q": "", "page": 1, "limit": 100}]
        answered: List[str] = []
        # A LOWER-RANKED LIST IS ONLY A FALLBACK while a higher-ranked endpoint that asked
        # for parameters has not been asked with them (repair round 1, 7 Oct 2026):
        # /api/packages answered a plain GET with 400 "page required", /api/estates answered
        # with 40 estates, and the estates were taken as the stock.
        fallback = None
        probes = 0
        for n_try, params in enumerate(tries):
            for path in (candidates if n_try == 0 else answered):
                if probes >= MAX_PROBES:
                    return fallback
                if params.get("q") is not None and "search" not in path.lower():
                    continue
                probes += 1
                try:
                    r = self._get(path, params=params or None, json_ok=True)
                except Exception as exc:                                # noqa: BLE001
                    self.capture.discovery["probes"].append(
                        {"path": path, "params": params, "error": type(exc).__name__})
                    continue
                payload = self._json(r)
                records, where = find_records(payload) if payload is not None else (None, "")
                shape: Dict[str, Any] = {
                    "path": path, "params": params, "status": r.status_code,
                    "content_type": str(r.headers.get("Content-Type", ""))[:60],
                    "landed": self._path_of(r)}
                if isinstance(payload, dict):
                    shape["top_level_keys"] = list(payload.keys())[:30]
                if records:
                    shape["records_under"] = where
                    shape["records_on_this_page"] = len(records)
                    shape["first_record_keys"] = list(records[0].keys())[:60]
                self.capture.discovery["probes"].append(shape)
                if payload is not None:
                    self.capture.payload("GET %s %s" % (path, urlencode(params)), payload)
                if r.status_code == 200 and records:
                    logger.info("EAgent Stocklist: %s answers with %d record(s) under %s",
                                path, len(records), where)
                    hit = (path, params, payload, records, where)
                    if n_try == 0 and answered:
                        # Something ranked above it answered without listing: ask those
                        # again with paging parameters before settling for this one.
                        fallback = hit
                        break
                    return hit
                if (n_try == 0 and path not in answered
                        and ((r.status_code == 200 and payload is not None)
                             or r.status_code in (400, 422))):
                    answered.append(path)
        return fallback

    def _page(self, path: str, params: Dict[str, Any]):
        try:
            r = self._get(path, params=params, json_ok=True)
        except Exception as exc:                                        # noqa: BLE001
            logger.warning("EAgent Stocklist: page %s failed: %s", params, type(exc).__name__)
            self._page_failures += 1
            return None, None
        payload = self._json(r)
        if r.status_code != 200 or payload is None:
            self._page_failures += 1
            return None, None
        self.capture.payload("GET %s %s" % (path, urlencode(params)), payload)
        records, _where = find_records(payload)
        return payload, (records or [])

    def _read_all(self, path: str, params: Dict[str, Any], payload: Any,
                  first: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Every record the endpoint lists: follow its paging to the end, within the caps."""
        records = list(first)
        seen = {_fingerprint(r) for r in records}
        meta = paging_meta(payload, len(first))
        total = meta.get("total")
        failures_before = self._page_failures
        # Ask for the next page in the size the server chose, under the name it uses.
        page_size = meta.get("limit") or len(first)
        limit_param = meta.get("limit_param") or "limit"
        state = {"pages": 1, "strategy": "single page"}

        def add(batch) -> int:
            new = 0
            for rec in batch or []:
                fp = _fingerprint(rec)
                if fp not in seen:
                    seen.add(fp)
                    records.append(rec)
                    new += 1
            return new

        def done() -> bool:
            return bool((total and len(records) >= total) or len(records) >= MAX_RECORDS
                        or state["pages"] >= MAX_PAGES)

        def fetch(p_path, extra, keep_size=True):
            q = dict(params)
            q.update(extra)
            if keep_size and page_size:
                q.setdefault(limit_param, page_size)
            state["pages"] += 1
            return self._page(p_path, q)

        def walk(next_params) -> int:
            """Fetch pages while each brings records not seen before. Returns the count."""
            got = 0
            while not done():
                extra = next_params()
                if extra is None:
                    break
                _payload, batch = fetch(path, extra)
                fresh = add(batch)
                if not fresh:
                    break
                got += fresh
            return got

        if meta.get("next_url"):
            state["strategy"] = "next link"
            nxt = meta["next_url"]
            while nxt and not done():
                url = urljoin(self.base + path, nxt)
                if not self._same_origin(url):
                    break
                u = urlparse(url)
                payload, batch = fetch(u.path, dict(parse_qsl(u.query)), keep_size=False)
                if not add(batch):
                    break
                nxt = paging_meta(payload, len(batch)).get("next_url")
        elif meta.get("cursor"):
            state["strategy"] = "cursor"
            cursor = meta["cursor"]
            while cursor and not done():
                payload, batch = fetch(path, {"cursor": cursor})
                if not add(batch):
                    break
                cursor = paging_meta(payload, len(batch)).get("cursor")
        elif first and not done() and (total or meta.get("pages") or len(first) >= 10):
            # PAGE NUMBERS -- and which numbering. page=1 repeating the first answer means
            # pages count from 1; page=1 bringing new records means they count from 0 and
            # page=1 was the second page. Either way the walk continues from page=2 for as
            # long as each page brings records not seen before.
            _payload, batch = fetch(path, {"page": 1})
            zero_based = bool(add(batch))
            counter = {"n": 1}

            def next_page():
                counter["n"] += 1
                return {"page": counter["n"]}

            moved = walk(next_page) if batch else 0
            if zero_based or moved:
                state["strategy"] = "page numbers from %d" % (0 if zero_based else 1)
            else:
                # The server ignored page=. An offset is the other common way to ask.
                for key in ("offset", "skip", "start"):
                    if done():
                        break
                    if walk(lambda key=key: {key: len(records)}):
                        state["strategy"] = "%s from the count read" % key
                        break
                else:
                    state["strategy"] = "single page (no paging parameter moved it)"
        pages, strategy = state["pages"], state["strategy"]
        more_expected = (not total) or len(records) < total
        if (len(records) >= MAX_RECORDS or pages >= MAX_PAGES) and more_expected:
            self.truncated = True
            logger.warning("EAgent Stocklist: stopped at the cap (%d records, %d pages) -- "
                           "the list is LONGER than this read.", len(records), pages)
        if total and len(records) < total:
            logger.warning("EAgent Stocklist: the API says %d record(s), %d were read.",
                           total, len(records))
        # Whether the walk is known to have stopped early (see total_stated): short of a
        # stated total; or, with none stated, a page fetch that failed, or one page the
        # server would not move past although it was full (as long as the page size it
        # said it serves). A single answer with no size stated may be the whole list -- some
        # servers answer everything at once -- so it is not short; with no total it proves
        # nothing either, and the ledger's step guard and anchor judge it.
        self.total_stated = int(total) if total else None
        failed = self._page_failures - failures_before
        declared = meta.get("limit")
        if total and len(records) < total:
            self.short_why = "%d of the %d record(s) the site states" % (len(records), total)
        elif not total and failed:
            self.short_why = "%d page fetch(es) failed during the walk" % failed
        elif not total and strategy.startswith("single page (no paging") and declared \
                and len(first) >= int(declared):
            self.short_why = "one full page, and no paging parameter moved past it"
        self.capture.discovery["paging"] = {"strategy": strategy, "pages": pages,
                                            "records": len(records), "total_stated": total,
                                            "truncated": self.truncated,
                                            "short": self.short_why}
        self.capture.summary.append("paging: %s, %d page(s), %d record(s)%s" % (
            strategy, pages, len(records),
            "" if not total else " of %d stated" % total))
        return records

    # -- exports -----------------------------------------------------------------------

    def _read_export(self, url: str) -> Optional[List[Dict[str, Any]]]:
        """Rows from an export file, or None to fall back to the JSON API."""
        try:
            r = self._get(url)
        except Exception as exc:                                        # noqa: BLE001
            self.capture.discovery.setdefault("exports_tried", []).append(
                {"path": urlparse(url).path, "error": type(exc).__name__})
            return None
        data = r.content or b""
        tried = {"path": urlparse(url).path, "status": r.status_code, "bytes": len(data),
                 "content_type": str(r.headers.get("Content-Type", ""))[:60]}
        self.capture.discovery.setdefault("exports_tried", []).append(tried)
        if r.status_code != 200 or not data or self._path_of(r) == "/login":
            return None
        payload = self._json(r)
        if payload is not None:
            records, _where = find_records(payload)
            if records:
                self.capture.payload("GET %s (export)" % urlparse(url).path, payload)
                self.endpoint = urlparse(url).path
                self.records_seen = len(records)
                rows = self._map_and_gate(records, url.split("?")[0], "export %s" % self.endpoint)
                return rows if self.state == "ok" else None
            return None
        records = tabular_records(data)
        tried["header_rows"] = len(records)
        if records:
            self.endpoint = urlparse(url).path
            self.records_seen = len(records)
            rows = self._map_and_gate(records, url.split("?")[0], "export %s" % self.endpoint)
            if self.state == "ok":
                return rows
        # The generic stocklist reader, which reads a price list a line at a time.
        from sources.spreadsheet_extract import extract_stocklist
        got = extract_stocklist(data, source_label=url.split("?")[0], builder_hint="")
        tried["extract_stocklist_rows"] = len(got)
        for g in got:
            g.update({"source_channel": CHANNEL, "stocklist_file": url.split("?")[0],
                      "source_url_or_ref": url.split("?")[0],
                      "attribution_scope": "builder" if g.get("builder_name") else "project",
                      "date_checked": read_date(), "verified": True,
                      "_state_stated": bool(g.get("state"))})
        ok, why, stats = plausibility(got, len(got))
        tried["gate"] = {"store": ok, "why": why, **stats}
        if not ok:
            return None
        self.state, self.why = "ok", "export read by the stocklist reader: " + why
        self.endpoint = urlparse(url).path
        return self._finish_rows(got)

    # -- mapping -----------------------------------------------------------------------

    def _map_and_gate(self, records: List[Dict[str, Any]], source_url: str,
                      label: str) -> List[Dict[str, Any]]:
        used: Dict[str, Dict[str, int]] = {}
        rows: List[Dict[str, Any]] = []
        dropped: Dict[str, int] = {}
        for rec in records:
            row, why = map_record(rec, self.base, source_url, used)
            if row is None:
                dropped[why] = dropped.get(why, 0) + 1
                continue
            if not any(str(row.get(f) or "").strip()
                       for f in ("suburb", "estate_name", "postcode", "state")):
                # NOWHERE AT ALL (repair round 1): no search can place it, and the gate's
                # 70% bar let 12 of 40 such rows into the database.
                why = "no suburb, estate, postcode or state"
                dropped[why] = dropped.get(why, 0) + 1
                continue
            if row.get("_state_stated") and not row.get("state"):
                why = "state not Australian (row kept, state left blank)"
                dropped[why] = dropped.get(why, 0) + 1
            rows.append(row)
        ok, why, stats = plausibility(rows, len(records))
        self.gate = {"store": ok, "why": why, **stats}
        all_keys: Dict[str, List[Any]] = {}
        for rec in records[:500]:
            for k, v in _flatten(rec).items():
                samples = all_keys.setdefault(k, [])
                if len(samples) < 3 and v not in (None, "") and v not in samples:
                    samples.append(v if not isinstance(v, str) else v[:40])
        consumed = {alias for counts in used.values() for alias in counts}
        self.capture.mapping = {
            "read_from": label,
            "fields": {f: {"key": max(c, key=c.get), "rows": c[max(c, key=c.get)],
                           "all_keys_used": c, "of_records": len(records)}
                       for f, c in sorted(used.items())},
            "keys_that_fed_nothing": {k: v for k, v in sorted(all_keys.items())
                                      if k not in consumed},
            "dropped": dropped,
            "gate": self.gate,
            "why_these_keys": "each field takes the first key in its alias list (see "
                              "_ALIASES in sources/eagent_stocklist.py) whose value passes "
                              "that field's range check; 'rows' is how often each key "
                              "supplied the value"}
        self.capture.summary.append("mapped %d row(s) from %d record(s) (%s)" % (
            len(rows), len(records), label))
        self.capture.summary.append("plausibility gate: %s -- %s" % (
            "STORE" if ok else "REFUSED, nothing stored", why))
        if not ok:
            self.state, self.why = "gate_refused", why
            logger.error("EAgent Stocklist: read %d record(s) but stored NOTHING: %s. "
                         "See logs/eagent_stocklist_capture/mapping.json.", len(records), why)
            return []
        self.state, self.why = "ok", why
        if self.truncated:
            self.why += " (STOPPED AT THE READ CAP -- the list is longer)"
        elif self.short_why:
            self.why += " (THE READ STOPPED EARLY: %s)" % self.short_why
        logger.info("EAgent Stocklist: %d row(s) from %d record(s) via %s; %s.",
                    len(rows), len(records), label, why)
        return self._finish_rows(rows)

    @staticmethod
    def _finish_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # IN A STABLE ORDER BEFORE SIBLINGS ARE NUMBERED (repair round 1, 7 Oct 2026): two
        # packages identical but for the price take their variant ordinal from where they
        # stand, and the API's order is not a promise -- reversed, it swapped which price
        # each identity held. By the record's own id, then the price.
        rows.sort(key=lambda r: (str(r.get("_record_id") or ""),
                                 float(r.get("advertised_package_price") or 0),
                                 str(r.get("source_text") or "")))
        for r in rows:
            r.pop("_state_stated", None)
            r.pop("_record_id", None)
        try:
            # Two packages identical in every identity field but the price stay two rows
            # (database.building_content_hash); the same numbering the file readers use.
            from sources.spreadsheet_extract import _assign_variant_ordinals
            _assign_variant_ordinals(rows)
        except Exception as exc:                                        # noqa: BLE001
            logger.warning("EAgent Stocklist: could not number price-only siblings: %s", exc)
        return rows

    # -- status for the Settings tab ---------------------------------------------------

    def _write_status(self, rows: int = 0) -> None:
        if self.status_path is None:
            return
        try:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            self.status_path.write_text(json.dumps({
                "state": self.state, "why": self.why, "rows": rows,
                "signed_in": self.signed_in, "endpoint": self.endpoint,
                "truncated": self.truncated,
                "checked_at": datetime.now().isoformat(timespec="seconds")}, indent=1),
                encoding="utf-8")
        except OSError as exc:
            logger.warning("EAgent Stocklist: could not write %s: %s", self.status_path, exc)


def read_status(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The last harvest's outcome for this source, or None if it has not run here."""
    try:
        return json.loads(Path(path or STATUS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def login_row(status: Optional[Dict[str, Any]] = None, configured: Optional[bool] = None,
              now: Optional[datetime] = None) -> Dict[str, Any]:
    """The Settings tab's login entry for this source (portal_login.py --check).

    MEASURED BY THE HARVEST, not by a second sign-in: --check runs after the harvest in the
    same nightly, and signing in twice a night to ask the same question is one more chance
    to look like a bot to a site we have only just been given access to.
    """
    now = now or datetime.now()
    if configured is None:
        u, p, _src = get_credentials(PORTAL_KEY)
        configured = bool(u and p)
    status = read_status() if status is None else status
    row: Dict[str, Any] = {"session": "", "label": "%s (H&L search)" % CHANNEL,
                           "channel": CHANNEL, "age_days": None,
                           "checked_at": now.strftime("%d/%m/%Y %H:%M")}
    if not configured:
        row.update(state="missing", self_service=False, blocked_by=MISSING_TEXT,
                   note=MISSING_TEXT)
        return row
    if not status:
        row.update(state="unknown", note="the login is stored; the harvest has not tried "
                                         "it on this machine yet")
        return row
    when = str(status.get("checked_at") or "")
    try:
        age_h = (now - datetime.fromisoformat(when)).total_seconds() / 3600
    except ValueError:
        age_h = None
    if age_h is None or age_h > 36:
        row.update(state="unknown", note="the last harvest that tried it was %s" % (
            when[:16] or "never"))
        return row
    state = status.get("state")
    if state == "refused":
        row.update(state="expired", self_service=False, blocked_by=REFUSED_TEXT,
                   note=REFUSED_TEXT)
    elif status.get("signed_in"):
        row.update(state="ok")
        if state != "ok":
            row["note"] = "signed in, but: %s" % (status.get("why") or state)
    else:
        row.update(state="unknown", note=str(status.get("why") or state or "")[:200])
    return row


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Read the EAgent Stocklist without storing it.")
    ap.add_argument("--dry-run", action="store_true", required=True,
                    help="sign in, read, gate and capture; store nothing")
    ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="    %(levelname)s %(message)s")
    src = EAgentStocklistSource()
    rows = src.search({})
    print("EAgent Stocklist: %s -- %s" % (src.state, src.why))
    print("  %d row(s) would be stored (dry run: none were). Capture: %s" % (
        len(rows), src.capture_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
