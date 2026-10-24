"""
Proxima — live stock harvest from the agent portal.

Colin's #1 ask from the 30 July meeting. Two things made this awkward and both are
now handled rather than worked around:

  * PROXIMA ENFORCES 2FA, and re-challenges per browser context — an exported
    storage_state is bounced straight to /customerauth/twofactor/. Only the
    persistent browser profile written by `portal_login.py portal_proxima --profile`
    survives. PlaywrightScraper prefers that profile automatically; nothing here has
    to know about it. No password is ever typed by this module.

  * THE STOCK IS NOT ON A PAGE. /agent/projects/index/ renders 40 collapsed project
    accordions. Expanding one reveals its lots — and each lot carries the whole
    record in data-* attributes, already typed:

        data-name  "Lot 14 Unit 14, 7 Example Avenue, SAMPLETON, NSW, 2765"
        data-lot   "00000014/00000014"      data-landsize   "318.00"
        data-rop   "829990"                 data-propertywidth "10.200000"
        data-room / data-bathroom / data-carspace / data-aspect

    (Invented specifics above, real grammar — this repo is public.)

    So there is no text parsing here at all, which is why this source can populate
    postcode and frontage that the stocklist-derived sources mostly cannot.

  * THE ACCORDION UNDERSTATES THE STOCK, twice over: it renders at most
    `data-propertylimit` (80) lots, and it only ever shows lots still for sale. So
    wherever a project offers an AVAILABILITY VIEW (9 of 40), `_read_via_api` is used
    instead — the same REST call the availability app makes, issued same-origin from
    the portal page. That returns the whole inventory with a per-lot reservation
    status: 305 lots for one project where the accordion showed 2.

Nothing is inferred. A builder is used only where the project header states one, a
missing bed/bath/car stays None rather than becoming 0, and a lot with no price is
not a listing and is dropped with a counted reason.
"""

import json
import logging
import re
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sources.base import PropertySource
from sources.scraper_base import PlaywrightScraper, PLAYWRIGHT_AVAILABLE, record_harvest_login
from builder_names import is_not_a_builder_name

logger = logging.getLogger("spb.scraper.proxima")

PROJECTS_URL = "https://portal.proxima.com.au/agent/projects/index/"

_STATES = ("NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT")

# The same eight, written out. Sources mix the two forms freely inside one feed, and a
# full name that goes unrecognised is not a harmless miss: it is taken for the suburb.
_STATE_NAMES = {
    "NEW SOUTH WALES": "NSW", "VICTORIA": "VIC", "QUEENSLAND": "QLD",
    "SOUTH AUSTRALIA": "SA", "WESTERN AUSTRALIA": "WA", "TASMANIA": "TAS",
    "NORTHERN TERRITORY": "NT", "AUSTRALIAN CAPITAL TERRITORY": "ACT",
}

# Read one project accordion.
#
# SCOPE MATTERS MORE THAN IT LOOKS. Every project's lots live in ONE shared
# .custom-accordion-tab-loop container, so `closest(loop).querySelectorAll(
# '.properties-container')` returns every OTHER project's lots too — 50 where the
# project has 10. A first cut did exactly that and produced 10,774 rows: each lot
# repeated under several projects, wearing the wrong estate and, worse, the wrong
# builder. Proxima tags each lot with a per-project class, so that is the only
# correct scope.
#
# The header is read from named spans rather than by splitting the label's text,
# for the same reason: the label's innerText runs on into the next project.
_COLLECT_JS = r"""
(pid) => {
  const lab = document.querySelector(`label.tab-label[data-project_id="${pid}"]`);
  if (!lab) return null;
  const q = sel => { const e = lab.querySelector(sel); return e ? (e.innerText||'').trim() : ''; };

  const header = {
    project:   q(`.project-name-count${pid}`) || q('.pro-value'),
    status:    q('.project-suburb-span'),           // misleadingly named: holds "For Sale"
    location:  q('.project-postcode-span'),         // "NSW" or "BRADDON, ACT, 2612"
    developer: q('.project-developers-span'),
    sunset:    q('.project-sunset-date-span'),
    deposit:   q('.project-deposite-exchange-span')
  };

  const props = [...document.querySelectorAll(`.properties-container-sort-${pid}`)].map(c => {
    const d = {};
    [...c.attributes].forEach(a => { if (a.name.startsWith('data-')) d[a.name.slice(5)] = a.value; });
    const pick = sel => { const e = c.querySelector(sel); return e ? (e.innerText||'').trim() : ''; };
    d._titled = pick('.property-titled-date');
    // .property-status also appears on icon-only controls, and a sibling
    // .capsule-price carries the same words as the price. Take the first one that
    // reads like a status and not like money.
    d._status = '';
    for (const el of c.querySelectorAll('.property-status')) {
      const t = (el.innerText||'').trim();
      if (t && !t.startsWith('$')) { d._status = t; break; }
    }
    d._reserved = /(^|\s)non-res(\s|$)/.test(c.className) ? 'no' : 'yes';
    return d;
  });
  return {header, props};
}
"""


def _num(v: Any) -> Optional[float]:
    """'829990' / '318.00' / '10.200000' -> float. Blank or zero -> None.

    Zero is treated as absent on purpose: Proxima writes "0" for a field it does not
    hold (a land lot's length, a lot with no recorded width), and a real 0 m² frontage
    does not exist. Storing 0 would look like a measurement.
    """
    if v is None:
        return None
    s = str(v).replace(",", "").replace("$", "").strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if f > 0 else None


def _int(v: Any) -> Optional[int]:
    """'00' and '' both mean 'not recorded' here, NOT zero bedrooms."""
    f = _num(v)
    return int(f) if f is not None else None


# The largest count any of these fields can plausibly hold. Three live listings reached
# the public dashboard advertising 41 and 51 BEDROOMS (Dunleary Avenue, Nirimba Fields).
# Whatever produced those figures — a dual-occupancy "4+1" losing its separator upstream,
# or the portal's own field — the number is not a bedroom count, and no sane brief can
# filter it out because every "minimum N bedrooms" test passes.
_MAX_COUNT = 12


def _count(v: Any) -> Optional[int]:
    """A bed/bath/car count, or None when the value cannot be one.

    Dropped rather than clamped: 41 clamped to 12 is still a fabricated number, and the
    rule here is that a blank with a stated reason beats a plausible guess.
    """
    n = _int(v)
    return n if n is not None and 0 < n <= _MAX_COUNT else None


# Ask the portal for a project's availability-view iframe. The encrypted project id
# and the form_key are BOTH per-session, so they are read live from the page — a
# captured value is dead on the next sign-in.
_AVAILABILITY_POST_JS = r"""
async (pid) => {
  const btn = document.querySelector(`a.availability-view-btn[data-project_id="${pid}"]`);
  if (!btn) return null;
  const fk = (document.cookie.match(/form_key=([^;]+)/)||[])[1] || '';
  const body = new URLSearchParams({project_id: btn.getAttribute('data-id'), form_key: fk});
  const r = await fetch(
    'https://portal.proxima.com.au/virtualpropertybooking/projects/availabilityview?isAjax=true',
    {method:'POST', body, headers:{'X-Requested-With':'XMLHttpRequest'}, credentials:'include'});
  return await r.text();
}
"""


# THE PROJECTS PAGE REMEMBERS A FILTER, AND IT IS NOT IN THE URL.
#
# Proxima holds the filter in the Magento SESSION, and the harvest deliberately reuses
# the persistent browser profile a human signs in with (`.browser_profiles/portal_proxima`,
# required because Proxima re-challenges 2FA for every new browser context). So a filter
# somebody left set while browsing the portal is STILL APPLIED when the harvest opens the
# page hours later, and nothing in the URL, the title or the log says so.
#
# On 2026-08-07 a leftover `property[state]=NSW` rendered 8 projects instead of 40 and the
# harvest stored 52 lots instead of 1,293 — small enough to read as a quiet day, large
# enough to pass every "did anything come back" check. Clearing the filter restored
# 40 projects / 9 availability views immediately; that is the known-good line.
#
# Hidden inputs are excluded: the portal carries its own bookkeeping fields under the same
# `property[...]` name, and counting those would make every run look filtered.
# KEYWORD FIELDS ARE INCLUDED, AND THEY ARE HIDDEN ONES.
#
# The Quick-Filter chips ("Atchison and Kenny Wollongong Building B ×") are backed by
# `keyword[projectname][]`, a HIDDEN input holding the project id. The exclusion above
# skipped it, so _real_filters returned {} on a page filtered down to a single project and
# _clear_filters reported the page clean. On 17 Aug 2026 that read 16 lots against 1,396
# stored — a 99% collapse the filter guard was written to prevent and could not see.
#
# The hidden-input exclusion still applies to `property[...]`, where it was earned: the
# portal keeps its own bookkeeping under those names and counting them made every run look
# filtered. `keyword[...]` carries no such traffic — anything there IS a chip the user set.
# THE PROJECT-LEVEL FILTERS WERE INVISIBLE TO THIS GUARD UNTIL 18 AUG 2026.
#
# Everything above concerns `property[...]`, which filters LOTS. The projects page also
# carries `projects_filter[...]` — Project Category, Project Type, Country — and those
# filter PROJECTS, which is strictly worse: a lot filter hides lots inside projects we
# still enumerate, a project filter removes whole projects from the list.
#
# Setting `projects_filter[product_category]=Land` took the page from 40 projects to 13,
# and _clear_filters read that page as clean, because nothing here matched the field. That
# is the 2026-08-07 failure again through a door the fix never covered. `select[name=
# "projects"]` is the same story for the Project Name dropdown.
#
# Hidden inputs stay excluded for the reason earned on `property[...]`: the portal keeps
# bookkeeping under these names and counting it would make every run look filtered.
_FILTER_FIELDS = ('select[name^="property["], '
                  'input[name^="property["]:not([type=hidden]), '
                  'input[name^="keyword["], '
                  'select[name^="projects_filter["], '
                  'input[name^="projects_filter["]:not([type=hidden]), '
                  'select[name^="table_filter["], '
                  'input[name^="table_filter["]:not([type=hidden]), '
                  'select[name="projects"]')

# What the form reads when it is NOT filtered. A default is not a filter, and treating
# one as a filter is worse than missing a real one: it puts "the page came up FILTERED"
# in the log every single night until nobody reads it any more.
#
# Confirmed live on 2026-08-07. A run arrived with property[property_status]=SALE and
# nothing else, read 40/40 projects and 1,293 lots — the known-good line, so the value
# hides nothing — and the reset control left it in place, which is what restoring a form
# default looks like.
#
# Keyed on the PAIR, not the field: property_status=SOLD is still a filter and is still
# reported. Adding to this needs the same evidence — a full read with the value present.
_UNFILTERED_DEFAULTS = {"property_status": "SALE"}


def _real_filters(snapshot: Dict[str, str]) -> Dict[str, str]:
    """The set fields that actually narrow the page, with form defaults removed."""
    return {k: v for k, v in (snapshot or {}).items()
            if _UNFILTERED_DEFAULTS.get(k) != v}


_FILTER_SNAPSHOT_JS = r"""
(sel) => {
  const out = {};
  document.querySelectorAll(sel).forEach(el => {
    const raw = el.getAttribute('name') || '';
    // property[state] -> state, so it can be matched against _UNFILTERED_DEFAULTS.
    // keyword[projectname][] keeps its own shape: it has no default, anything in it is a
    // chip somebody set, and flattening it to "projectname" would collide with a
    // property field of the same name.
    const name = raw.startsWith('property[')
      ? raw.replace(/^property\[/, '').replace(/\]$/, '')
      : raw;
    if (!name) return;
    if (el.type === 'checkbox' || el.type === 'radio') {
      if (el.checked && el.value) out[name] = el.value;
      return;
    }
    const v = (el.value || '').trim();
    if (v) out[name] = v;
  });
  return out;
}
"""

# CLEARING THE FORM IS NOT CLEARING THE FILTER. Measured live on 2026-08-07: clicking
# `#search_clear` empties the select in the DOM and nothing else — the Magento session
# still holds the filter, so the listing on screen is still the narrowed one and the next
# page load brings the filter straight back. A first version of this fix clicked that
# control, re-read the now-empty field, declared the page clear, and harvested 1,049 lots
# against the unfiltered 1,293 while printing [SUCCESS].
#
# So the SUBMIT is the whole point: only posting the form updates the session. The clear
# control is still clicked first, because it empties the form the way the portal expects,
# but its click is never taken as evidence of anything.
#
# Fields are restored to their DEFAULTS rather than blanked, so property_status goes back
# to SALE instead of empty — blanking it would ask a different question of the portal than
# the unfiltered page does, and the known-good line (40 projects / 9 availability views)
# is measured with it set.
#
# Both controls sit BELOW a 1440x900 headless viewport, so Playwright's own `.click()`
# refuses them with "element is outside of the viewport". A click dispatched from inside
# the page has no such constraint.
# What a healthy, unfiltered Proxima run looks like — measured repeatedly in August 2026
# and the same bar export_proxima_session.py applies before it will save a session
# (MIN_PROJECTS / MIN_AVAIL). Kept here so the harvest can say when it is looking at less.
_HEALTHY_PROJECTS = 40
_HEALTHY_AVAIL = 9

# THE ACCORDION STOPS AT 80 LOTS, AND SAYS NOTHING ABOUT IT.
#
# Every project label carries `data-propertylimit="80"`, but the cap is enforced by the
# SERVER: raising the attribute to 2000 and re-expanding still renders 80. There is no
# "load more" control and the POST body carries no limit field, so the page simply cannot
# be asked for lot 81.
#
# What it CAN be asked for is the other end of the same list. The body carries
# `sort[sort_attributes]`, so price-asc returns the cheapest 80 and price-desc the dearest
# 80; union them and a project of up to 160 lots is read whole. Measured on DAMAC Islands
# Stage 2 (project 587) on 2026-08-18, which the harvest had been storing at 80 for weeks:
#
#     price-asc     80 lots
#     price-desc    80 lots  (+49 new)   union 129 = the 129 the project header states
#
# Only the price sorts help. rooms-asc/desc and area-asc/desc each returned 80 and added
# nothing new — they re-rank the same capped slice rather than re-selecting it.
#
# Above 160 lots this is still short. Price BANDS work there (splitting
# property_range_filter[rop] into ranges returned 49 + 80 = the same 129). Since 7 Oct 2026
# _top_up_capped walks them: see _CEILING_FIELD.
_RENDER_CAP = 80

_SORT_FIELD = "sort[sort_attributes]"
# THE MIDDLE OF A BIG PROJECT IS REACHED BY WALKING THE PRICE CEILING DOWN (7 Oct 2026).
# The page's own body carries this field (26,000,000 on every captured body: the slider's
# top). Replayed price-desc under a ceiling, the server returns the dearest 80 lots AT OR
# BELOW it; the next ceiling is the cheapest of those, inclusive, so lots sharing a price
# straddle no gap. Each step that comes back with exactly 80 is the cap talking again, so
# the walk goes on until a band comes back short or meets the cheapest 80 already read.
_CEILING_FIELD = "property_range_filter[rop][range_two]"
# Extra POSTs one capped project may cost, so a huge project cannot keep the shared signed-
# in session busy for long. 24 reaches about 1,900 lots.
_MAX_REPLAYS = 24
# What a capped read looks like when nothing says how big the project is: the cap, the
# cap plus the 3 lots the old donor-body bug added (6 Oct: "83 in hand" thirteen times),
# and both price sorts full.
_CAP_SIGNATURES = (_RENDER_CAP, _RENDER_CAP + 3, 2 * _RENDER_CAP)


def stored_project_counters(db_path=None) -> Dict[str, int]:
    """{project id: the available count its header last stated}, from the database.

    Only the INDEX states a project's (available/total) counter; a project that has
    rotated off the index states none, and on 6 Oct that left _top_up_capped with no
    number to be short of -- Live Caringbah stated 140 on 26 Sep and was read at 80 every
    night after, with no warning. record_building refreshes project_available in place
    whenever a header states it, so the newest stated value per project is the last one
    the portal published. Never raises: no database means no counters, not no harvest.
    """
    return {pid: avail for pid, (avail, _on) in stored_project_counter_details(db_path).items()}


def stored_project_counter_details(db_path=None) -> Dict[str, Tuple[int, str]]:
    """{project id: (the available count its header last stated, the day it was read as
    dd/mm/YYYY or "")} -- stored_project_counters with the counter's DATE.

    8 Oct 2026: a stored counter is the number the index printed on the last night the
    project was on it, and that can be weeks ago. The CARLING Carlingford stated 79 on
    27 Sep; read whole off the index on 8 Oct it held 72, and the 8 lots of the 27 Sep read
    missing from it were last read on 29 Sep or 5 Oct -- gone from the list since, not
    missed. The date is what lets a reader tell an old number from tonight's (see
    _warn_if_short)."""
    import sqlite3
    from pathlib import Path
    try:
        if db_path is None:
            import config
            db_path = config.DATABASE_PATH
        path = Path(db_path)
        if not path.exists():
            return {}
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            out: Dict[str, Tuple[int, str]] = {}
            for pid, avail, checked in conn.execute(
                    "SELECT source_project_id, project_available, checked_at FROM buildings "
                    "WHERE source_channel='Proxima' AND project_available IS NOT NULL "
                    "AND COALESCE(source_project_id,'') <> '' "
                    "ORDER BY (checked_at IS NULL), checked_at DESC"):
                if str(pid) not in out:
                    out[str(pid)] = (int(avail), _day_of(checked))
            return out
        finally:
            conn.close()
    except Exception as e:                                            # noqa: BLE001
        logger.warning("Proxima: could not read the stored project counters (%s) -- "
                       "off-index projects will have no count to be short of.", e)
        return {}


def stored_row_projects(db_path=None) -> Dict[str, str]:
    """{content_hash: the project id its stored Proxima row was captured under}, from the
    database -- a live row's project before a superseded one's. Never raises.

    8 Oct 2026 (second repair round). A lot printed under two projects shares one identity
    (content_hash leaves the project out), and record_building only FILLS a stored row's
    project and estate, never rewrites them: whichever copy updates the row, the row keeps
    the project of its first capture. So _collapse_cross_listed gives the identity to the
    copy of the project the stored row already belongs to. Without that, Lancelin South
    Stage C (802) updated rows 19965-19967 -- stored under Stage C Land Only (767), product
    'Land' -- with its house-and-land prices, and a $358,600 land lot was carded at
    $701,500 under the Land Only label."""
    import sqlite3
    from pathlib import Path
    try:
        if db_path is None:
            import config
            db_path = config.DATABASE_PATH
        path = Path(db_path)
        if not path.exists():
            return {}
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            out: Dict[str, str] = {}
            for h, pid in conn.execute(
                    "SELECT content_hash, source_project_id FROM buildings "
                    "WHERE source_channel='Proxima' AND COALESCE(content_hash,'') <> '' "
                    "AND COALESCE(source_project_id,'') <> '' "
                    "ORDER BY (superseded_by IS NOT NULL AND superseded_by <> ?), id",
                    (_WITHDRAWN,)):
                out.setdefault(str(h), str(pid))
            return out
        finally:
            conn.close()
    except Exception as e:                                            # noqa: BLE001
        logger.warning("Proxima: could not read the stored rows' projects (%s) -- a lot "
                       "printed under two projects keeps the lowest project id's identity.", e)
        return {}


# database.WITHDRAWN_FROM_SOURCE, spelled here so this module still imports without it: a
# row retired that way is the listing's own row, back on the next read (record_building).
_WITHDRAWN = "withdrawn: the source stopped listing it"


def _offer_key(r: Dict[str, Any]) -> Tuple[float, str]:
    """What makes two copies of one lot the SAME offer: the price they print, to the dollar,
    and whether they state it for sale (8 Oct 2026, second repair round). By class, not by
    word: the accordion says "For Sale" and an availability view "Available" for the same
    thing, and a lot one view calls Sold and another Not Available is unavailable in both --
    told apart by the word, one lot would be stored, and shown, twice."""
    price = _num(r.get("advertised_package_price")) or _num(r.get("price")) or 0.0
    return (round(float(price)), _status_class(r.get("availability_status")))


def _status_class(status: Any) -> str:
    """'available' (For Sale, Available), '' (no status stated) or 'not available'."""
    said = re.sub(r"\s+", " ", str(status or "")).strip().lower()
    if not said:
        return ""
    return "available" if said in ("for sale", "available") else "not available"


def _pid_order(pid: str) -> Tuple[int, Any]:
    """Project ids in a stable order whatever the page order: numerically, else by text."""
    return (0, int(pid)) if str(pid).isdigit() else (1, str(pid))


def _offer_ordinal(pid: str, used: set) -> int:
    """The variant_ordinal a second offer of one lot is stored under: its project id, so it
    names the same row every night whatever order the projects come in (a page-order
    ordinal would swap two offers' rows, and their prices, the night the order changed)."""
    import hashlib
    base = int(pid) if str(pid).isdigit() else \
        int(hashlib.sha1(str(pid).encode("utf-8")).hexdigest()[:6], 16)
    ordinal, n = base, 0
    while ordinal in used or ordinal == 0:
        n += 1
        ordinal = base * 1000 + n
    used.add(ordinal)
    return ordinal


def _day_of(stamp: Any) -> str:
    """'2026-09-27T11:51:07' -> '27/09/2026'; '' for anything else."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(stamp or "").strip())
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else ""


def _has_field(body: str, key: str) -> bool:
    return any(urllib.parse.unquote_plus(kv.partition("=")[0]) == key
               for kv in (body or "").split("&"))


def _rop(d: Dict[str, str]) -> Optional[float]:
    return _num(d.get("rop"))

# Replays the page's own accordion POST with one field changed, and returns the lot tiles
# shaped exactly like _COLLECT_JS's, so _row cannot tell which path a lot arrived by.
_REPLAY_ACCORDION_JS = r"""
async ([body]) => {
  const r = await fetch('/agent/projects/properties/?isAjax=true',
    {method:'POST', body,
     headers:{'Content-Type':'application/x-www-form-urlencoded',
              'X-Requested-With':'XMLHttpRequest'},
     credentials:'include'});
  if (!r.ok) return null;
  const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
  return [...doc.querySelectorAll('[class*="properties-container-sort"]')].map(c => {
    const d = {};
    [...c.attributes].forEach(a => { if (a.name.startsWith('data-')) d[a.name.slice(5)] = a.value; });
    const pick = sel => { const e = c.querySelector(sel); return e ? (e.textContent||'').trim() : ''; };
    d._titled = pick('.property-titled-date');
    d._status = '';
    for (const el of c.querySelectorAll('.property-status')) {
      const t = (el.textContent||'').trim();
      if (t && !t.startsWith('$')) { d._status = t; break; }
    }
    d._reserved = /(^|\s)non-res(\s|$)/.test(c.className) ? 'no' : 'yes';
    d._floorplan = '';
    return d;
  });
}
"""


def _with_field(body: str, key: str, value: str) -> str:
    """Return the captured POST body with one field replaced, everything else verbatim.

    Rebuilt field by field rather than parsed and re-encoded: the body carries the whole
    search form, and round-tripping it through a parser changed the encoding of fields the
    server was fussy about. A hand-built minimal body was tried first and the server
    answered 10 lots where the page showed 80, so the page's own body is the only one
    trusted here.
    """
    out = []
    for kv in body.split("&"):
        k, _, v = kv.partition("=")
        if urllib.parse.unquote_plus(k) == key:
            v = urllib.parse.quote_plus(value)
        out.append(f"{k}={v}")
    return "&".join(out)

# THE INDEX IS A FRONT PAGE, NOT THE PORTFOLIO.
#
# Every project label on the index carries class "top-projects", and the index renders 40.
# The account's own project dropdown lists 118. The other 78 are not filtered out and not
# archived — they simply never appear on the page the harvest reads, so for months the
# harvest saw 34% of the stock this login can reach and nothing said so.
#
# Found on 2026-08-18 from a listing Coleen was looking at and the app did not hold: "The
# Pinnacle Liverpool, 3 bed, $744K". There is no Liverpool project on the index. There are
# two in the dropdown — The Pinnacle Tower A (273) and Tower B (275) — and Tower B holds
# exactly that lot:
#
#     Lot 73 Unit B1001, 10 Norfolk Street, LIVERPOOL   $744,581   3 bed   For Sale
#
# Kemps Estate - Austral and Leppington Rise Estate are off-index too, which is why a
# Liverpool-area search came back with land lots and no apartments.
#
# The accordion's own POST honours a projectId that is not on the page, so these are
# readable without touching the filter — see _read_off_index. The dropdown is the only
# enumeration of them we have.
_PROJECT_OPTIONS_JS = r"""
() => {
  const s = document.querySelector('select[name="projects"]');
  if (!s) return [];
  return [...s.options]
    .map(o => ({id: (o.value || '').trim(), name: (o.text || '').trim()}))
    .filter(o => o.id && o.name && /^\d+$/.test(o.id));
}
"""


_CLEAR_AND_SUBMIT_JS = r"""
(cfg) => {
  const {sel, defaults} = cfg;
  let used = [];
  for (const s of ['#search_clear', '.filter-form-reset', '#search_reset',
                   '[data-action-code="RESET"]']) {
    const el = document.querySelector(s);
    if (el) { try { el.scrollIntoView(); } catch (e) {} el.click(); used.push(s); break; }
  }
  // Keyword chips are REMOVED, not blanked. keyword[projectname][] is an array field, so
  // an emptied one still posts a member and the portal can read that as "filter by
  // nothing", keeping the narrowed result. Taking the input out of the form is the only
  // way to post no keyword at all. The visible chip goes too, so the page does not show a
  // filter it is no longer applying.
  document.querySelectorAll('input[name^="keyword["]').forEach(el => {
    used.push('-' + (el.getAttribute('name') || 'keyword'));
    el.remove();
  });
  document.querySelectorAll('.keyword_projectname_field_group, [class*="keyword_"][class*="field_group"]')
    .forEach(el => el.remove());

  document.querySelectorAll(sel).forEach(el => {
    if (!el.isConnected) return;                    // already removed above
    const raw = el.getAttribute('name') || '';
    const name = raw.startsWith('property[')
      ? raw.replace(/^property\[/, '').replace(/\]$/, '')
      : raw;
    const def = Object.prototype.hasOwnProperty.call(defaults, name) ? defaults[name] : '';
    if (el.type === 'checkbox' || el.type === 'radio') {
      el.checked = false;
    } else {
      el.value = def;
      if (el.tagName === 'SELECT' && (el.value || '') !== def) el.selectedIndex = -1;
    }
    el.dispatchEvent(new Event('change', {bubbles: true}));
  });
  const submit = document.querySelector('#search_submit');
  if (!submit) return '';
  try { submit.scrollIntoView(); } catch (e) {}
  submit.click();
  used.push('#search_submit');
  return used.join(' + ');
}
"""


def _lot_number(raw: str) -> str:
    """'00000014/00000014' -> '14'. Returns '' when there is nothing real."""
    if not raw:
        return ""
    first = str(raw).split("/")[0].strip()
    trimmed = first.lstrip("0")
    return trimmed or ""


# A project header is A NAME PLUS A LIVE COUNTER: "Ahlei (85/110)" says 85 of Ahlei's
# 110 lots are available right now.
#
# WHICH NUMBER IS WHICH was checked against our own stored rows on 7 Aug 2026 rather
# than assumed — Ahlei "(85/110)" stored 85 "Available" and 25 "Not Available", Arcadia
# Estate "(3/28)" stored 3 and 25, Ashbury Terraces "(16/30)" stored 16 and 14. So it is
# available/total. Two downstream workarounds call it "(sold/total)"; they strip it and
# never read it, so the mislabel was harmless there, but it is not what the numbers mean.
#
# THE COUNTER MOVES AS LOTS SELL AND COME BACK, and it was being stored as part of the
# estate's name, which made the name itself volatile. The same Wollongong building:
#
#     03/08/2026   "Atchison and Kenny Wollongong Building A (2/305)"
#     07/08/2026   "Atchison and Kenny Wollongong Building A (3/305)"
#
# estate_name is deliberately not part of building_content_hash, so this never split one
# lot into two rows. What it did do is fragment the estate itself: 324 of 379 duplicated
# Proxima addresses differ on estate_name and on nothing else, the dashboard lists one
# estate under several names, and any comparison by estate across two harvests is
# unreliable by construction.
#
# ANCHORED TO A TRAILING PAIR OF INTEGERS ROUND A SLASH, because parentheses are also
# part of real names in this portal — "Ascenta Living (DBN Homes)", "Creation Homes (Qld)
# Pty Ltd" — and a looser pattern would amputate those.
_PROJECT_COUNTER = re.compile(r"\s*\(\s*(\d+)\s*/\s*(\d+)\s*\)\s*$")


def parse_project_title(title: Any) -> Tuple[str, Optional[int], Optional[int]]:
    """'Ahlei (85/110)' -> ('Ahlei', 85, 110). A title with no counter comes back as is.

    The counts are handed back rather than dropped on the floor. Where a project is read
    through the availability view they are recoverable from the per-lot statuses, but
    where it is read from the accordion we only ever SEE the lots still for sale, so this
    header is the only statement of how big the project really is: DAMAC Islands Stage 2
    stored 80 lots — the DOM's own `data-propertylimit` cap — under a header reading
    (142/264). Discarding the pair would throw away the only measure of that project's
    true size and its sell-through.
    """
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    m = _PROJECT_COUNTER.search(text)
    if not m:
        return text, None, None
    name = text[:m.start()].strip()
    # A header that is ONLY a counter leaves nothing to keep. Hand back the raw string:
    # a blank estate is the one outcome a grouping cannot recover from, and it would be
    # this fix causing it.
    return (name or text), int(m.group(1)), int(m.group(2))


def parse_property_name(name: str) -> Dict[str, str]:
    """'Lot 14 Unit 14, 7 Example Avenue, SAMPLETON, NSW, 2765' -> parts.

    (Invented specifics, real grammar — this repo is public.)

    Read from the END, because the leading part varies (a lot label, a unit label,
    both, or neither) while the tail is consistently ... suburb, STATE, postcode.
    Anything not positively identified is left blank rather than guessed at.
    """
    out = {"label": "", "street": "", "suburb": "", "state": "", "postcode": ""}
    if not name:
        return out
    parts = [p.strip() for p in str(name).split(",") if p.strip()]
    if not parts:
        return out

    if re.fullmatch(r"\d{4}", parts[-1]):
        out["postcode"] = parts.pop()
    # Proxima writes the state either way — "…, AUSTRAL, NSW, 2179" but also
    # "…, BINGARA DRIVE, WILTON, New South Wales, 2571". Matching only the abbreviation
    # meant the full name was never recognised as a state, so it was taken as the SUBURB
    # and the real suburb was absorbed into the street: 56 live rows stored
    # suburb="New South Wales" with Wilton buried in the street field. A wrong suburb
    # breaks distance search, benchmarking, and matching the lot against another channel.
    tail = parts[-1].upper() if parts else ""
    if tail in _STATES:
        out["state"] = parts.pop().upper()
    elif tail in _STATE_NAMES:
        out["state"] = _STATE_NAMES[tail]
        parts.pop()
    if parts:
        out["suburb"] = parts.pop().title()
    if parts:
        # A leading "Lot .. Unit .." is the label; whatever remains is the street.
        if re.match(r"^\s*(lot|unit)\b", parts[0], re.I):
            out["label"] = parts.pop(0)
        out["street"] = ", ".join(parts)
    return out


class ProximaSource(PropertySource):
    """Every lot Proxima publishes to this agent account."""

    SESSION = "portal_proxima"

    def __init__(self, registry=None, stored_counters: Optional[Dict[str, int]] = None):
        self.registry = registry
        self.stats: Dict[str, int] = {}
        self.projects_seen = 0
        self.projects_with_stock = 0
        self.cross_listed: List = []
        self.via_api = 0
        self.topped_up = 0
        self.off_index = 0
        # {pid: available count its header last stated}; read from the database when the
        # harvest starts if not handed in. See stored_project_counters.
        self.stored_counters = stored_counters
        # {pid: the day its stored counter was stated, dd/mm/YYYY}; empty when unknown.
        self.stored_counter_dates: Dict[str, str] = {}
        # {content_hash: the project its stored row was captured under}; read from the
        # database when the harvest starts if not handed in. See stored_row_projects.
        self.stored_row_projects: Optional[Dict[str, str]] = None
        # Projects whose read ends short of their counter or on a cap signature, by name.
        self.short_projects: List[Dict[str, Any]] = []
        # Projects read WHOLE that hold fewer lots than an old stored counter (8 Oct 2026):
        # named in the log and the manifest, never called short. See _warn_if_short.
        self.stale_counters: List[Dict[str, Any]] = []
        # One entry per project read, for data/harvest_manifest.json.
        self.project_log: List[Dict[str, Any]] = []
        self._topup: Dict[str, Any] = {}
        # What the projects page had filtered on when this run opened it, if anything.
        # Kept so the run summary can say the harvest walked into a filtered page rather
        # than leaving that to be inferred from a low count. See _clear_filters.
        self.filter_found: Dict[str, str] = {}

    @property
    def channel_name(self) -> str:
        # Must match PortalConfig("proxima.com.au").source_channel: it is part of the
        # identity hash AND it is the entry Colin filters on in the dashboard.
        return "Proxima"

    def _bump(self, key: str) -> None:
        self.stats[key] = self.stats.get(key, 0) + 1

    # Long enough for a cold runner on a slow morning; the poll exits as soon as the
    # count holds, so a healthy page costs ~3 seconds, not 60.
    _PROJECT_SETTLE_SECONDS = 60

    @staticmethod
    def _settle_project_list(scraper) -> int:
        """Block until the project count stops changing, and return it.

        Mirrors export_proxima_session._counts, which learned this the expensive way: the
        accordion is filled by AJAX, so counting once counts a moment in the middle of it.
        Three identical reads is the bar — two can both land inside the same pause between
        batches.

        Returns the settled count rather than nothing, so a caller can log what it waited
        for. Never raises: a page that cannot be counted is a bigger problem than a slow
        one and surfaces immediately afterwards as an empty harvest.
        """
        sel = "label.tab-label[data-project_id]"
        last, stable, waited = -1, 0, 0
        try:
            while waited < ProximaSource._PROJECT_SETTLE_SECONDS:
                n = scraper.page.locator(sel).count()
                stable = stable + 1 if n == last else 0
                last = n
                if n and stable >= 2:
                    break
                scraper.page.wait_for_timeout(1000)
                waited += 1
        except Exception as e:                                        # noqa: BLE001
            logger.warning("Proxima: could not settle the project list (%s) — "
                           "counting what is on screen.", e)
        return max(last, 0)

    @staticmethod
    def _settle(scraper) -> None:
        """Wait for the projects list to come back after a reset re-renders it."""
        for wait in (lambda: scraper.page.wait_for_load_state("networkidle", timeout=15000),
                     lambda: scraper.page.wait_for_selector(
                         "label.tab-label[data-project_id]", timeout=15000),
                     lambda: scraper.page.wait_for_timeout(800)):
            try:
                wait()
            except Exception:
                pass

    # The only field whose unfiltered value is CONFIRMED against the live page: clearing
    # property[state] took the projects page from 1,049 lots and 8 availability views back
    # to 1,293 and 9 (2026-08-07), and an unfiltered page shows that select with nothing
    # chosen.
    #
    # A refusal rests on this field alone, deliberately. Every filter found is cleared,
    # but another `property[...]` control could carry a non-empty default nobody has
    # looked at, and hard-failing the nightly harvest on a guess about one would cost
    # more than this bug does. Anything else left set is logged loudly and left to the
    # partial-read guard in harvest_buildings, which is the general net for exactly this.
    _CONFIRMED_FILTER = "state"

    # Two goes at the same thing. Not a second strategy — there is only one that works —
    # but the submit reloads the page, and a reload that lands badly is worth one retry
    # before giving up on the night's harvest.
    _CLEAR_ATTEMPTS = 2

    def _clear_filters(self, scraper) -> bool:
        """Leave the projects page unfiltered, or say that it could not be done.

        True means the page can be counted — nothing was set, or the filter was cleared
        AND a fresh load of the page came back without it. False means the state filter
        survived, and every count taken after it would be a subset of the truth wearing
        the shape of a full read.

        THE VERIFICATION IS A FRESH PAGE LOAD, and it has to be. Re-reading the same DOM
        is what the first version of this fix did, and the portal's clear control empties
        the form without touching the session — so the field read empty, the check passed,
        and the harvest went on to read the still-filtered listing: 1,049 lots against
        1,293, reported as [SUCCESS]. Only a reload asks the server what it actually holds.

        An unreadable page is reported as clear on purpose: it is a bigger problem than a
        filter and it surfaces a moment later as an empty harvest. Turning it into a
        refusal here would only bury the real cause under this one.
        """
        cfg = {"sel": _FILTER_FIELDS, "defaults": _UNFILTERED_DEFAULTS}
        try:
            before = _real_filters(scraper.page.evaluate(_FILTER_SNAPSHOT_JS,
                                                         _FILTER_FIELDS))
        except Exception as e:
            logger.warning("Proxima: could not read the projects filter (%s) — "
                           "continuing, and the read count is the check.", e)
            return True
        if not before:
            return True

        self.filter_found = dict(before)
        logger.warning("Proxima: the projects page came up FILTERED (%s). The filter "
                       "lives in the portal session, so one left behind by a manual "
                       "sign-in still applies to this harvest. Clearing it before "
                       "anything is counted.",
                       ", ".join(f"{k}={v}" for k, v in sorted(before.items())))

        for attempt in range(1, self._CLEAR_ATTEMPTS + 1):
            label = f"clear+submit (attempt {attempt})"
            try:
                hit = scraper.page.evaluate(_CLEAR_AND_SUBMIT_JS, cfg)
                if not hit:
                    logger.warning("     %s: no #search_submit on the page. Emptying the "
                                   "form without posting it leaves the session's filter "
                                   "exactly where it was.", label)
                    break
                self._settle(scraper)
                # Ask the SERVER, not the DOM we just edited.
                scraper.goto(PROJECTS_URL)
                after = _real_filters(scraper.page.evaluate(_FILTER_SNAPSHOT_JS,
                                                            _FILTER_FIELDS))
            except Exception as e:
                logger.warning("     %s failed: %s", label, e)
                continue
            if not after:
                logger.info("     filter cleared via %s, and a fresh load of the page "
                            "confirms it is gone.", hit)
                return True
            left = ", ".join(f"{k}={v}" for k, v in sorted(after.items()))
            if self._CONFIRMED_FILTER not in after:
                logger.warning("     %s left %s set. Counting anyway: property[state] is "
                               "the only field with a confirmed unfiltered value, and "
                               "stopping the nightly run over a field nobody has verified "
                               "would cost more than this bug does. If the read comes "
                               "back short, look here first.", label, left)
                return True
            logger.warning("     %s left %s still set.", label, left)
        return False

    def _open_and_read(self, scraper, pid: str, attempts: int = 3):
        """Expand one project and read its lots, retrying an empty result.

        Worth the retry: on the first full run "Central Quarter Merrylands" returned
        nothing while a minute earlier it had returned 80 lots — the largest project
        on the page, lazy-loading behind a heavy DOM. An empty read is indistinguishable
        from a project that genuinely publishes no stock, so without retrying, 80 real
        lots silently become a line in the skip log.

        The POST the expand fires is captured on the way past and returned as `_body`.
        It is the whole search form as the page itself serialises it, and it is what
        _top_up_capped replays to reach past the 80-lot render cap — see _RENDER_CAP.
        """
        captured: Dict[str, str] = {}

        def _grab(req):
            if ("agent/projects/properties" in req.url and req.method == "POST"
                    and "body" not in captured):
                try:
                    captured["body"] = req.post_data or ""
                except Exception:
                    pass

        scraper.page.on("request", _grab)
        try:
            data = None
            for attempt in range(1, attempts + 1):
                lab = scraper.page.query_selector(f'label.tab-label[data-project_id="{pid}"]')
                if not lab:
                    return None
                lab.click()
                try:
                    scraper.page.wait_for_selector(f".properties-container-sort-{pid}",
                                                   timeout=8000 * attempt)
                except Exception:
                    pass
                scraper.page.wait_for_timeout(400 * attempt)
                data = scraper.page.evaluate(_COLLECT_JS, pid)
                if data and data["props"]:
                    # Collapse again: 40 open accordions makes every later read crawl.
                    try:
                        lab.click()
                        scraper.page.wait_for_timeout(200)
                    except Exception:
                        pass
                    data["_body"] = captured.get("body", "")
                    return data
                # Leave it closed before retrying so the expand handler fires cleanly.
                try:
                    lab.click()
                    scraper.page.wait_for_timeout(600)
                except Exception:
                    pass
                if attempt < attempts:
                    logger.info("     project %s came back empty (attempt %d) — retrying",
                                pid, attempt)
            if isinstance(data, dict):
                data["_body"] = captured.get("body", "")
            return data
        finally:
            try:
                scraper.page.remove_listener("request", _grab)
            except Exception:
                pass

    def _top_up_capped(self, scraper, pid: str, body: str,
                       props: List[Dict[str, str]],
                       stated_available: Optional[int],
                       name: str = "") -> List[Dict[str, str]]:
        """Read the lots the 80-tile render cap hid: both price sorts, then price bands.

        Only runs for a project that came back sitting ON the cap and has no availability
        view to read instead. See _RENDER_CAP for why the sort flip is the lever and why
        raising data-propertylimit is not, and _CEILING_FIELD for the bands.

        `body` MUST be this project's own body. Until 7 Oct 2026 an off-index project was
        topped up with the DONOR body search() lends it -- every replay re-read project
        761 (180 George Parramatta, 3 lots), "83 in hand" thirteen times on 6 Oct, three
        foreign lots filed under Lindfield House, and no capped project ever got past 80.

        A replay that fails keeps every lot already in hand; it never shrinks a read.
        """
        self._topup = {"replays": 0, "bands": 0, "complete": False}
        if not body:
            logger.warning("     project %s is capped at %d lot(s) but no POST body was "
                           "captured, so the rest cannot be reached", pid, len(props))
            self._bump("capped project, no body to replay")
            return props

        def key(d):
            return f"{(d.get('lot') or '').strip()}|{(d.get('name') or '').strip()}"

        have = {key(d) for d in props}
        start = len(props)

        def replay(b):
            """Tiles for one body, or None when the POST failed."""
            if self._topup["replays"] >= _MAX_REPLAYS:
                # Out of budget is not the end of the project (repair round 2): say so.
                self._topup["exhausted"] = True
                return None
            self._topup["replays"] += 1
            try:
                return scraper.page.evaluate(_REPLAY_ACCORDION_JS, [b]) or []
            except Exception as e:                                    # noqa: BLE001
                logger.warning("     project %s: replay failed (%s) -- keeping the %d "
                               "lot(s) in hand", pid, e, len(props))
                self._bump("capped project, replay failed")
                return None

        def take(tiles) -> int:
            added = 0
            for d in tiles:
                k = key(d)
                if k not in have:
                    have.add(k)
                    props.append(d)
                    added += 1
            return added

        if not _has_field(body, _SORT_FIELD):
            logger.warning("     project %s: its POST body carries no %s, so the two price "
                           "sorts are the same read", pid, _SORT_FIELD)

        # BOTH ENDS, EXPLICITLY. The expand that produced `props` sorts however the page
        # happened to be sorted; asking for each end by name does not depend on that.
        sorted_reads: Dict[str, List[Dict[str, str]]] = {}
        for srt in ("price-asc", "price-desc"):
            got = replay(_with_field(body, _SORT_FIELD, srt))
            if got is None:
                break
            sorted_reads[srt] = got
            take(got)

        asc, desc = sorted_reads.get("price-asc"), sorted_reads.get("price-desc")
        both_full = (asc is not None and desc is not None
                     and len(asc) >= _RENDER_CAP and len(desc) >= _RENDER_CAP)
        disjoint = both_full and not ({key(d) for d in asc} & {key(d) for d in desc})
        # PROVEN WHOLE when the two ends met: one came back short, or they overlap and
        # between them hold more than one page. Two identical full pages prove nothing --
        # that is also what a server ignoring the sort returns.
        # MET BY PRICE, NOT ONLY BY KEY (repair round 1, 7 Oct 2026): both ends cut at the
        # SAME price share their boundary lots and still miss the rest at that price (90
        # lots at $600,000: 155 of 240 read, called whole). The cheapest 80 hold every lot
        # below their dearest, the dearest 80 every lot above their cheapest; only when the
        # second is strictly below the first is every price covered.
        # THE SORT MUST HAVE BEEN HONOURED (repair round 2, 7 Oct 2026, data verifier). A
        # server that ignores the sort but honours the ceiling answers both "sorts" with the
        # cheapest 80; the band walk then started from the cheapest price, got one lot back
        # below it, and called 80 of 300 lots whole. The dearest read must come back dearest
        # first and reach at least as high as the cheapest read; when it does not, nothing
        # past the cap can be reached by price, and the read is named short. Judged on what
        # came back, not on the order of the tiles: two full reads holding the SAME lots
        # with nothing dearer in the "dearest" one cannot both be ends of a project that
        # overflows the cap.
        honoured = True
        if (asc is not None and desc is not None and len(asc) >= _RENDER_CAP
                and len(desc) >= _RENDER_CAP):
            asc_prices = [p for p in (_rop(d) for d in asc) if p]
            desc_prices = [p for p in (_rop(d) for d in desc) if p]
            same_lots = {key(d) for d in asc} == {key(d) for d in desc}
            dearer = (bool(asc_prices) and bool(desc_prices)
                      and max(desc_prices) > max(asc_prices))
            # A project of exactly 80 lots gives both reads the same lots; only the order
            # the tiles came back in can then show the sort was asked and answered.
            ordered = (len(set(desc_prices)) > 1
                       and desc_prices == sorted(desc_prices, reverse=True)
                       and asc_prices == sorted(asc_prices))
            honoured = not same_lots or dearer or ordered
            if not honoured:
                self._topup["unsorted"] = True
                logger.warning("     project %s: the portal ignored the price sort (the dearest "
                               "read is not dearest first), so lots past the %d-lot cap cannot "
                               "be reached by price", pid, _RENDER_CAP)
                self._bump("capped project, sort not honoured")
        if asc is not None and desc is not None:
            union = len({key(d) for d in asc} | {key(d) for d in desc})
            top_of_asc = max((p for p in (_rop(d) for d in asc) if p), default=None)
            low_of_desc = min((p for p in (_rop(d) for d in desc) if p), default=None)
            crossed = (top_of_asc is not None and low_of_desc is not None
                       and low_of_desc < top_of_asc)
            self._topup["complete"] = honoured and (
                len(asc) < _RENDER_CAP or len(desc) < _RENDER_CAP
                or (not disjoint and union > _RENDER_CAP and crossed))
        # Bands when the two ends did not meet (a middle exists that neither reached), or
        # the read is still below the counter. Disjoint decides even with a counter: the
        # stored one can be weeks old and lower than the project is tonight. And when they
        # met only at one shared price, which the walk must try to split.
        needs_bands = honoured and desc is not None and len(desc) >= _RENDER_CAP and (
            disjoint or bool(stated_available and len(props) < stated_available)
            or not self._topup["complete"])
        if needs_bands:
            if not _has_field(body, _CEILING_FIELD):
                logger.warning("     project %s: over 2 x %d lots but its POST body has no "
                               "%s, so its middle cannot be read in price bands",
                               pid, _RENDER_CAP, _CEILING_FIELD)
            else:
                self._walk_bands(pid, body, asc, desc, replay, take)

        self.topped_up += 1
        logger.info("     project %s (%s): render cap reached -- price-asc and price-desc "
                    "replayed, %d price band(s), %d POST(s): %d -> %d lot(s) in hand",
                    pid, (name or "?")[:40], self._topup["bands"], self._topup["replays"],
                    start, len(props))
        if stated_available and len(props) < stated_available:
            self._bump("capped project still short after the sort flip")
        return props

    def _walk_bands(self, pid, body, asc, desc, replay, take) -> None:
        """Walk the price ceiling down from the dearest 80 until it meets the cheapest 80.

        REPAIR ROUND 1 (7 Oct 2026, data verifier), three changes:
          * the ceiling SENT is a dollar above the one meant, so the lots AT the boundary
            price come back whether the portal's ceiling includes its own value or not
            (unverified on the live portal; an exclusive one lost 8 of 300 lots priced in
            blocks of six, and the read was called complete);
          * a full band that cannot get below its own ceiling (80 or more lots at one
            price) is asked FIRST, and is never a whole read: before, "the band met the
            cheapest 80" was tested first, and 240 lots with 90 at $600,000 read 155 and
            were called complete;
          * the walk has met the cheapest 80 only when the band reaches STRICTLY below the
            dearest of them: at one shared price either read may have been cut."""
        floor = max((p for p in (_rop(d) for d in (asc or [])) if p), default=None)
        prices = [p for p in (_rop(d) for d in desc) if p]
        if not prices:
            return
        ceiling = min(prices)
        by_desc = _with_field(body, _SORT_FIELD, "price-desc")
        while True:
            sent = ceiling + 1
            label = f"{sent:.0f}" if float(sent).is_integer() else f"{sent}"
            band = replay(_with_field(by_desc, _CEILING_FIELD, label))
            if band is None:
                break
            self._topup["bands"] += 1
            added = take(band)
            if len(band) < _RENDER_CAP:
                self._topup["complete"] = True
                break                          # the band came back short: nothing below
            low = min((p for p in (_rop(d) for d in band) if p), default=None)
            if low is None:
                break
            if low >= ceiling:
                self._topup["stuck"] = ceiling
                logger.warning("     project %s: %d or more lots at $%s -- the price band "
                               "cannot split them further, so the read is NOT whole",
                               pid, _RENDER_CAP, f"{ceiling:,.0f}")
                self._bump("capped project, one price holds the cap")
                break
            if floor is not None and low < floor:
                self._topup["complete"] = True
                break                          # met the cheapest 80 read by price-asc
            if not added:
                break
            ceiling = low

    def _whole(self, read: int, used_api: bool = False) -> str:
        """How tonight's read of a project is known to be whole, or "" when it is not.

          "availability view"  the whole inventory, every lot with its status;
          "uncapped"           the portal's own list ended before its 80-lot render cap;
          "price sorts met"    capped, and the two price sorts and the bands met
                               (_top_up_capped proved it: self._topup["complete"]).
        Judged on the read just made, so call it straight after the top-up."""
        if used_api:
            return "availability view"
        if self._topup.get("replays"):
            return "price sorts met" if self._topup.get("complete") else ""
        return "uncapped" if read < _RENDER_CAP else ""

    def _warn_if_short(self, pid: str, name: str, read: int,
                       stated: Optional[int], stated_source: Optional[str] = None,
                       whole: str = "") -> None:
        """Say BY NAME when a project's read looks capped or falls below its counter.

        Two signals, either enough. Below the counter it last stated (the header tonight,
        or the database for a project off the index). Or ending on a cap signature --
        exactly 80, 83 or 160 -- which needs no counter at all: on 6 Oct 13 projects ended
        on 83 and nothing said so, because none of them stated a count.

        AN OLD COUNTER IS NOT A SHORT READ (8 Oct 2026). stated_source "stored" means the
        counter is the database's -- the index's number on the last night the project was
        on it -- and `whole` says the read just made was proven complete. Then the gap is
        lots sold or taken off since that night, and the project is named in
        stale_counters (and the log, at INFO) instead of short_projects. On 8 Oct seven
        projects, all off the index and read whole, were called short against counters of
        9 Sep - 1 Oct: 197 Jasmine 2/3, 499 Kensington 8/11, 726 KIARA 27/28, 283 Live
        Caringbah 138/140, 762 The CARLING 72/79, 529 The Grande 108/117, 810 VERTEX 27/28
        -- five read under the cap, two (283, 529) topped up until the price bands met, so
        a lot the counter's night held and tonight lacks was not on the portal's For Sale
        list tonight. Tonight's own header counter is never discounted, and a read not
        proven whole is short as before.

        AND ONLY A SMALL GAP (8 Oct 2026, repair round, correctness verifier). For a read
        under the render cap, "whole" only says the list ended there -- and a narrowed list
        (the session filter trap) ends under the cap as well. So 2 lots against an old 79,
        or none against 40, is short as it always was; only a gap old_counter_gap_ok allows
        (harvest_manifest: at most 3 lots or 10% of the counter, a read of at least one)
        is a stale counter.
        """
        from harvest_manifest import old_counter_gap_ok
        below = bool(stated) and read < stated
        if (below and stated_source == "stored" and whole
                and old_counter_gap_ok(read, stated)):
            self.stale_counters.append({"pid": str(pid), "name": name, "read": read,
                                        "stated": stated,
                                        "stated_on": self.stored_counter_dates.get(str(pid))
                                        or "", "whole": whole})
            logger.info("Proxima: project %s '%s' read %d lot(s), whole (%s), against the %d "
                        "its counter stated%s -- the difference was sold or withdrawn since, "
                        "not missed.", pid, name, read, whole, stated,
                        (" on " + self.stored_counter_dates[str(pid)])
                        if self.stored_counter_dates.get(str(pid)) else "")
            return
        # ...unless the top-up just proved the read whole: a project can hold 83 lots.
        signature = read in _CAP_SIGNATURES and not self._topup.get("complete")
        # A band walk that met 80 or more lots at one price could not read past them,
        # whatever the count (repair round 1, 7 Oct 2026) -- nor one whose portal ignored the
        # sort, or that ran out of its _MAX_REPLAYS posts before the ends met (round 2).
        stuck = ((self._topup.get("stuck") is not None or self._topup.get("unsorted")
                  or self._topup.get("exhausted")) and not self._topup.get("complete"))
        if not (below or signature or stuck):
            return
        self.short_projects.append({"pid": pid, "name": name, "read": read,
                                    "stated": stated})
        if stuck and not below and not signature:
            if self._topup.get("stuck") is not None:
                logger.warning("Proxima: project %s '%s' read %d lot(s), but %d or more of its "
                               "lots share the price $%s and the portal shows at most %d of "
                               "them -- some may be missing from tonight's harvest.", pid, name,
                               read, _RENDER_CAP, f"{self._topup['stuck']:,.0f}", _RENDER_CAP)
            elif self._topup.get("unsorted"):
                logger.warning("Proxima: project %s '%s' read %d lot(s), but the portal ignored "
                               "the price sort, so lots past its %d-lot cap could not be "
                               "reached -- some may be missing from tonight's harvest.", pid,
                               name, read, _RENDER_CAP)
            else:
                logger.warning("Proxima: project %s '%s' read %d lot(s) and used all %d of its "
                               "replays before the price bands met -- some may be missing "
                               "from tonight's harvest.", pid, name, read, _MAX_REPLAYS)
            return
        if below:
            logger.warning("Proxima: project %s '%s' read %d lot(s) against the %d its "
                           "counter last stated -- %d may be missing from tonight's "
                           "harvest.", pid, name, read, stated, stated - read)
        else:
            logger.warning("Proxima: project %s '%s' ends at exactly %d lot(s), what the "
                           "%d-lot render cap leaves -- lots past it are probably NOT in "
                           "tonight's harvest.", pid, name, read, _RENDER_CAP)

    def _read_off_index(self, scraper, pid: str, name: str, body: str):
        """One project that the index never renders, read by id through the same POST.

        Returns data shaped like _open_and_read's so _row cannot tell them apart. The
        HEADER is thin on purpose: the index is where a project states its developer,
        location and (available/total) counter, and a project that is not on the index
        states none of them here. The name comes from the dropdown option, and everything
        else is left blank rather than guessed — the lot's own address still carries its
        suburb, state and postcode, which is what search actually needs.
        """
        if not body:
            return None
        own = _with_field(body, "projectId", pid)
        try:
            tiles = scraper.page.evaluate(_REPLAY_ACCORDION_JS, [own])
        except Exception as e:
            logger.warning("     off-index project %s (%s) failed: %s", pid, name[:30], e)
            return None
        if not tiles:
            return None
        # `_body` is THIS project's body. It used to be the donor's, unchanged, and the
        # top-up replayed that -- see _top_up_capped.
        return {"header": {"project": name, "status": "", "location": "",
                           "developer": "", "sunset": "", "deposit": ""},
                "props": tiles, "_body": own}

    def _log_project(self, pid: str, name: str, on_index: bool, via_api: bool,
                     header_counter, stated: Optional[int], read: int,
                     stated_source: Optional[str] = None, whole: str = "") -> None:
        """One line of data/harvest_manifest.json for this project (see harvest_manifest).

        8 Oct 2026 adds what the parity check's READ bar needs to judge a read without
        guessing: where `stated` came from (stated_source "header" = tonight's index,
        "stored" = the database's, with stated_on its day), how the read is known whole
        (`whole`, see _whole), and -- set later, by search() and _collapse_cross_listed --
        whether the project is overseas and how many of its lots were stored once under
        another project (cross_listed, cross_listed_into)."""
        avail, total = header_counter
        self.project_log.append({
            "pid": str(pid), "name": name, "on_index": on_index,
            "via": "availability view" if via_api else "accordion",
            "header_counter": [avail, total] if avail is not None else None,
            "stated": stated, "stated_source": stated_source if stated is not None else None,
            "stated_on": (self.stored_counter_dates.get(str(pid)) or None)
            if stated_source == "stored" else None,
            "printed": read, "whole": whole,
            "replays": self._topup.get("replays", 0), "bands": self._topup.get("bands", 0),
            "short": any(s["pid"] == str(pid) for s in self.short_projects),
            "stale_counter": any(s["pid"] == str(pid) for s in self.stale_counters),
            "overseas": False, "cross_listed": 0, "cross_listed_into": {}})

    def _mark_overseas(self, pid: str, rows: List[Dict[str, Any]]) -> None:
        """Finish this project's manifest line from the lots it made: `not_a_listing` (tiles
        that made none), and `overseas` when its lots are not in Australia
        (8 Oct 2026): no lot names an Australian state and suburb_quality calls every one
        overseas. That night 8 projects were (DAMAC and Safa Gate in Dubai, five SMDC towers
        in the Philippines priced in pesos: 5,258 lots); two of them, SMDC Glade Residences
        ILOILO (696, 80+ lots at one price) and SMDC Gold Residences (693, all 24 replays
        spent), read short, and the parity check, which judges Australian stock, names them
        instead of failing on them."""
        if not self.project_log or self.project_log[-1]["pid"] != str(pid):
            return
        # Tiles that made no lot (no price, no address): printed and never stored, and said
        # so rather than left for the stored-vs-printed bar to find unexplained.
        self.project_log[-1]["not_a_listing"] = max(0, int(self.project_log[-1]["printed"])
                                                    - len(rows))
        if not rows or any(str(r.get("state") or "").strip().upper() in _STATES
                           for r in rows):
            return
        try:
            import suburb_quality
            overseas = all(suburb_quality.is_overseas(dict(r)) for r in rows)
        except Exception:                                             # noqa: BLE001
            overseas = False
        self.project_log[-1]["overseas"] = bool(overseas)

    def _read_via_api(self, scraper, pid: str) -> Optional[List[Dict[str, str]]]:
        """A project's FULL inventory, via the availability view's own REST call.

        Preferred over the DOM wherever a project offers an availability view, for two
        reasons measured on 2026-08-01:

          * The accordion renders at most `data-propertylimit` (80) lots. Ahlei claims
            85 available and Central Quarter Merrylands 81, so both were silently
            truncated — 6 real lots that simply never appeared.
          * The DOM lists only what is still for sale. The API returned 110 and 112
            for those two projects — the whole inventory, each lot carrying its own
            reservation status. More stock AND an honest sold/available flag.

        Returns rows shaped like the DOM's data-* dicts so one mapper serves both.
        """
        html = scraper.page.evaluate(_AVAILABILITY_POST_JS, pid)
        if not html:
            return None
        # The iframe URL carries the project's encProjectId — the only thing the REST
        # call needs. Reading it from here means the availability app never has to be
        # loaded at all: no second tab, no waiting on a Next.js bundle, and no race to
        # intercept a response. It also reaches projects the app itself never asks
        # about — Arcadia Estate renders a site plan and never calls properties/list,
        # so interception returned nothing there while a direct call returns all 28.
        m = re.search(r"[?&]projectId=([^&\"']+)", html)
        if not m:
            return None
        enc = m.group(1)

        # Same-origin from the portal page, so the session cookie is the whole auth
        # story — the Bearer token the iframe uses is not needed and is short-lived
        # (60s) anyway. Cross-origin from the availability app this same call fails
        # CORS preflight, which is why it is issued from here.
        raw = scraper.page.evaluate(
            "async (enc) => { const r = await fetch('/rest/V1/properties/list',"
            " {method:'POST', headers:{'Content-Type':'application/json'},"
            "  credentials:'include',"
            "  body: JSON.stringify({encProjectId: enc, encBlockId:'', encLevelId:''})});"
            " return r.ok ? await r.text() : ''; }", enc)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            props = payload[0]["output"]["properties"]
        except Exception as e:
            logger.warning("     availability payload for %s unreadable: %s", pid, e)
            return None

        out = []
        for p in props:
            res = (p.get("reservation_details") or [{}])[0]
            out.append({
                "name": p.get("property_name") or "",
                # The API has no zero-padded lot code; the label carries the lot.
                "lot": (p.get("property_label") or "").replace("Lot ", "").split(" Unit")[0].strip(),
                "room": p.get("bed"), "bathroom": p.get("bath"), "carspace": p.get("car"),
                "landsize": p.get("area"), "rop": str(p.get("price") or "").replace(",", ""),
                "propertywidth": "", "packageprice": "0",
                "_titled": "",
                # "Available" / "Not Available" — the DOM pass can only ever say
                # "For Sale", because it never sees a sold lot at all.
                "_status": res.get("reservation_status") or "",
                "_floorplan": p.get("floor_Plan") or "",
            })
        return out

    def _row(self, d: Dict[str, str], header: Dict[str, str],
             project_id: str = "") -> Optional[Dict[str, Any]]:
        # PACKAGE PRICE FIRST, and `rop` only as a fallback.
        #
        # This order used to be the other way round, and it understated listings by
        # roughly $450,000 each. Colin opened Proxima on 6 Aug against our own card:
        #
        #     Lot 2 Unit 2, 275 Twelfth Avenue, AUSTRAL   we said $675,000
        #     Proxima "Package Price" column              $1,127,000
        #
        # Across that project our figure tracked LAND AREA (250.1 m2 -> $675,000,
        # 300.1 m2 -> $740,000) and sat at ~58% of the package every time, which is what
        # a land component looks like. `rop` is the land price, not the finished cost.
        #
        # Where rop is the only figure we cannot tell which of the two it is, so the
        # pairing is logged: the next authenticated harvest resolves this from real data
        # instead of from inference.
        pkg = _num(d.get("packageprice"))
        rop = _num(d.get("rop"))
        price = pkg or rop
        if pkg and rop and abs(pkg - rop) > 1:
            logger.info("     %s: package %s vs rop %s — using the package, keeping rop "
                        "as the land component", (d.get("lot") or "?"), pkg, rop)
        if not price:
            self._bump("no price")
            return None

        name = (d.get("name") or "").strip()
        if not name:
            self._bump("no address")
            return None
        a = parse_property_name(name)

        project, avail_count, total_count = parse_project_title(header.get("project"))
        developer = (header.get("developer") or "").strip()
        # Proxima's developer field sometimes holds a place inside the development rather
        # than a company: one project published "Level 33" there and 318 listings were
        # stored under a builder that does not exist. Colin spotted it on 5 Aug — "there's
        # no builder called Level 33". Dropped rather than replaced: the project title is
        # not the builder's name, and guessing one is exactly what this file refuses to do.
        if developer and is_not_a_builder_name(developer):
            logger.info("     project %s: developer field is %r, which is a place and not "
                        "a company — left blank", project[:40] or "?", developer)
            self._bump("developer field was not a company name")
            developer = ""
        # The project header's location is "NSW" or "BRADDON, ACT, 2612"; take a state
        # from it only where one is actually written.
        location = ""
        for tok in re.split(r"[,\s]+", (header.get("location") or "").upper()):
            if tok in _STATES:
                location = tok
                break

        state = a["state"] or location
        lot = _lot_number(d.get("lot", ""))

        return {
            "lot_address": name,
            "suburb": a["suburb"],
            "state": state,
            # Proxima states it, so it is read, never inferred. Blank if the header
            # names nobody — a blank with a reason beats a plausible guess.
            "builder_name": developer,
            "builder_source": "proxima project header" if developer else "",
            "attribution_scope": "builder" if developer else "project",
            # The estate's NAME, with the live counter taken off it — see
            # parse_project_title. The two numbers are kept as numbers below.
            "estate_name": project,
            # How the project stood the last time it was read. Volatile on purpose:
            # record_building refreshes both in place every harvest rather than holding
            # the first reading, because a stale availability is worse than none.
            "project_available": avail_count,
            "project_total": total_count,
            "lot_number": lot,
            "postcode": a["postcode"],
            "advertised_package_price": price,
            # Kept separately when the package price supersedes it, so the breakdown is
            # visible and the two are never confused again.
            "land_price": rop if (pkg and rop and abs(pkg - rop) > 1) else None,
            "bedrooms": _count(d.get("room")),
            "bathrooms": _count(d.get("bathroom")),
            "car_spaces": _count(d.get("carspace")),
            "land_size_sqm": _num(d.get("landsize")),
            "house_size_sqm": _num(d.get("internal_area")),
            "frontage_m": _num(d.get("propertywidth")),
            "title_status": (d.get("_titled") or "").strip(),
            # What Proxima itself displays ("For Sale", "Sold", ...). Never derived
            # from the reservation class, which answers a different question.
            "availability_status": (d.get("_status") or "").strip(),
            # None, not "": an empty string is truthy in the columnar snapshot, so
            # every renderer testing `if row.floorplan_url` emitted a dead anchor.
            "floorplan_url": (d.get("_floorplan") or "").strip() or None,
            "source_channel": self.channel_name,
            # The project this lot belongs to. Stored so a recommendation can link
            # straight to its Proxima project page — the harvest has always known the
            # id and always threw it away, leaving the agent projects index as the only
            # "source" on 1,212 rows, which is no help at all when you are hunting one
            # lot. See provenance.py.
            "source_project_id": str(project_id or "").strip(),
            "source_url_or_ref": PROJECTS_URL,
            "source_text": name,
            "date_checked": datetime.now().strftime("%d/%m/%Y"),
            "extraction_confidence": 1.0,   # typed attributes, nothing parsed out of prose
            "verified": True,
        }

    def _reauthenticate(self, scraper) -> bool:
        """Sign back in with the stored password when the server session has lapsed.

        WHY THIS CAN WORK AT ALL, given Proxima enforces 2FA. Two different things expire
        and they were being treated as one. The Magento SERVER session (PHPSESSID) dies
        every few days — twice in nine days in August — and that is what bounces the
        harvest to /login. DEVICE TRUST is a separate cookie, `otp_authentication`, and it
        runs to 28 Jan 2027; while it is alive a correct password does NOT re-challenge
        for a code. So the common failure needs a password, which the runner already has
        (PORTAL_PROXIMA_USERNAME / _PASSWORD), not a human with an inbox.

        Until now this branch just gave up and told someone to run portal_login.py on the
        laptop, which is exactly the dependency the whole GitHub-Actions move existed to
        remove — and it meant a session lapse cost every night's Proxima stock until a
        person noticed.

        HONEST WHEN IT CANNOT. If the device-trust cookie HAS expired, the password lands
        on /customerauth/twofactor/ and no automation can pass it. That is reported as the
        distinct thing it is — a human with the account's inbox — rather than as a generic
        failure, because the two need completely different responses.
        """
        from secrets_store import get_credentials
        from sources.portal_config import config_for_url

        cfg = config_for_url(PROJECTS_URL)
        user, password, src = get_credentials(self.SESSION)
        # EVERY ENDING IS WRITTEN DOWN (record_harvest_login), because this is the only
        # place Proxima's sign-in is ever measured. portal_login --check never opens it, so
        # from 12 Oct 20:51 to 23 Oct 11:31 the line below -- "signed in but still bounced
        # ... the password may have changed" -- was logged on 11 of the 12 runs (on 21 Oct
        # the form's click timed out instead, the "error" ending), and the Settings tab's
        # Login cell said NOT CHECKED for all eleven days.
        if not (user and password):
            logger.error("Proxima bounced to %s and no stored credentials are available "
                         "to sign back in. Run: python portal_login.py portal_proxima "
                         "--profile", scraper.page.url)
            record_harvest_login(self.SESSION, "bounced",
                                 "the saved sign-in has lapsed and no password is stored "
                                 "to sign back in with", scraper.page.url)
            return False

        logger.info("Proxima: the saved sign-in has lapsed — signing back in "
                    "(credentials from %s).", src)
        try:
            scraper.goto(cfg.login_url)
            scraper.page.wait_for_selector(cfg.password_selector, timeout=15000)
            scraper.page.fill(cfg.email_selector, user, timeout=10000)
            scraper.page.fill(cfg.password_selector, password, timeout=10000)
            scraper.page.click(cfg.submit_selector, timeout=10000)
            scraper.page.wait_for_load_state("networkidle", timeout=25000)
        except Exception as e:                                        # noqa: BLE001
            logger.error("Proxima: sign-in attempt failed (%s).", e)
            # The form broke, not necessarily the password: no verdict either way.
            record_harvest_login(self.SESSION, "error",
                                 "the sign-in form failed: %s" % type(e).__name__,
                                 scraper.page.url)
            return False

        if "twofactor" in scraper.page.url:
            # Device trust is gone. This is the one case that genuinely needs a person.
            logger.error(
                "Proxima asked for a TWO-FACTOR CODE, so this device is no longer "
                "trusted. No automation can pass that. Someone with the account's inbox "
                "must sign in once: python portal_login.py portal_proxima --profile")
            record_harvest_login(self.SESSION, "bounced",
                                 "Proxima asked for a two-factor code: someone with the "
                                 "account's inbox must sign in once", scraper.page.url)
            return False

        scraper.goto(PROJECTS_URL)
        if "/login" in scraper.page.url or "twofactor" in scraper.page.url:
            logger.error("Proxima: signed in but still bounced to %s — the password may "
                         "have changed.", scraper.page.url)
            record_harvest_login(self.SESSION, "bounced",
                                 "the stored password did not sign in (still bounced to "
                                 "the login page): it may have changed",
                                 scraper.page.url)
            return False
        logger.info("Proxima: signed back in.")
        record_harvest_login(self.SESSION, "signed_in",
                             "the saved sign-in had lapsed; signed back in with the stored "
                             "password", scraper.page.url)
        return True

    def search(self, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        if not PLAYWRIGHT_AVAILABLE:
            logger.error("Proxima skipped: Playwright is not installed.")
            return []

        scraper = PlaywrightScraper(session_name=self.SESSION)
        if not (scraper.profile_dir.exists() or scraper.session_file.exists()):
            logger.error("Proxima has no saved sign-in. Run once, in a real browser: "
                         "python portal_login.py portal_proxima --profile")
            record_harvest_login(self.SESSION, "missing", "no saved sign-in on this machine")
            return []

        results: List[Dict[str, Any]] = []
        if self.stored_counters is None:
            details = stored_project_counter_details()
            self.stored_counters = {pid: avail for pid, (avail, _on) in details.items()}
            self.stored_counter_dates = {pid: on for pid, (_avail, on) in details.items()}
        if self.stored_row_projects is None:
            self.stored_row_projects = stored_row_projects()
        try:
            with scraper.session():
                scraper.goto(PROJECTS_URL)
                if "twofactor" in scraper.page.url or "/login" in scraper.page.url:
                    if not self._reauthenticate(scraper):
                        return []
                else:
                    record_harvest_login(self.SESSION, "session",
                                         "the saved sign-in was accepted", scraper.page.url)

                # BEFORE enumerating anything. A filtered page does not look broken —
                # it looks like a smaller portfolio — so the count has to be taken from
                # a page known to be showing everything.
                if not self._clear_filters(scraper):
                    logger.error(
                        "Proxima: the projects page is STILL filtered by state (%s) "
                        "after trying to clear it, so refusing to harvest. A filtered "
                        "read is worse than none: it stores a subset and stamps it as "
                        "today's stock, leaving the rest to go stale silently — on "
                        "2026-08-07 that was 52 lots in place of 1,293. Open the portal, "
                        "clear the projects filter, and re-run.",
                        ", ".join(f"{k}={v}" for k, v in sorted(self.filter_found.items())))
                    return []

                # WAIT FOR THE ACCORDION TO STOP GROWING BEFORE COUNTING ANYTHING.
                #
                # The project list arrives by AJAX after the document loads, so a single
                # query_selector_all counts whatever had rendered at that instant. On
                # 2026-08-17 the GitHub runner read ONE project and harvested 16 lots
                # against the previous night's 1,291 — and it did not look like a race, it
                # looked exactly like the filter trap this function guards against: a
                # smaller portfolio, reported as a success. The filter check above had
                # already passed (nothing was set), so nothing waited for the page.
                #
                # The same race produced 0, then 8, then 40, then 0 from one profile
                # within minutes during the August session work, and was misread as a
                # filtered session twice before being measured. export_proxima_session.py
                # settled it there with this poll; the HARVEST never got the same fix.
                self._settle_project_list(scraper)
                labels = scraper.page.query_selector_all("label.tab-label[data-project_id]")
                pids, seen = [], set()
                for el in labels:
                    p = el.get_attribute("data-project_id")
                    if p and p not in seen:
                        seen.add(p)
                        pids.append(p)
                self.projects_seen = len(pids)
                avail_pids = {
                    el.get_attribute("data-project_id")
                    for el in scraper.page.query_selector_all("a.availability-view-btn")
                }
                logger.info("Proxima: %d project(s) listed, %d with an availability view.",
                            len(pids), len(avail_pids))
                # The known-good line, recorded from repeated healthy runs. Said out loud
                # rather than enforced: a genuinely smaller portfolio is possible and
                # hard-failing on a remembered number would stop the harvest on a day the
                # portal was merely quieter. harvest_buildings' partial-read guard is the
                # net that acts — it now fails the CI run — and this line tells whoever
                # reads the log which of the two it is looking at.
                if len(pids) < _HEALTHY_PROJECTS:
                    logger.warning(
                        "Proxima: only %d project(s) after the list settled — a healthy "
                        "run lists %d with %d availability views. Everything read below "
                        "is a SUBSET and will be stamped as today's stock; the rest goes "
                        "stale silently. Check the portal for a filter, or a session that "
                        "is only partly signed in.",
                        len(pids), _HEALTHY_PROJECTS, _HEALTHY_AVAIL)

                # The accordion POST is the only way to reach an off-index project, and
                # it has to be a body the page itself produced — see _with_field. The
                # first project that yields one lends it to all 78.
                off_body = ""

                for i, pid in enumerate(pids, 1):
                    try:
                        data = self._open_and_read(scraper, pid)
                        if data and data.get("_body") and not off_body:
                            off_body = data["_body"]
                        if not data:
                            self._bump("project unreadable")
                            continue
                        # Where an availability view exists, it beats the accordion:
                        # uncapped, and it includes lots that are no longer for sale.
                        used_api = False
                        if pid in avail_pids:
                            api = self._read_via_api(scraper, pid)
                            if api:
                                if len(api) >= len(data["props"]):
                                    logger.info("     project %s: availability view has "
                                                "%d lot(s) vs %d in the page",
                                                pid, len(api), len(data["props"]))
                                    data = {"header": data["header"], "props": api}
                                    self.via_api += 1
                                    used_api = True
                                else:
                                    logger.info("     project %s: availability view had "
                                                "fewer (%d < %d) — keeping the page",
                                                pid, len(api), len(data["props"]))
                        # Sitting exactly on the render cap means the page stopped
                        # early, not that the project holds 80 lots. Projects read via
                        # the availability view are already whole and are left alone.
                        pname, stated_avail, stated_total = parse_project_title(
                            data["header"].get("project"))
                        stated = (stated_avail if stated_avail is not None
                                  else self.stored_counters.get(pid))
                        stated_source = ("header" if stated_avail is not None
                                         else "stored" if stated is not None else None)
                        self._topup = {}
                        if not used_api and len(data.get("props") or []) >= _RENDER_CAP:
                            data["props"] = self._top_up_capped(
                                scraper, pid, data.get("_body", ""),
                                data["props"], stated, name=pname)
                        whole = self._whole(len(data["props"]), used_api)
                        if not used_api:
                            self._warn_if_short(pid, pname, len(data["props"]), stated,
                                                stated_source, whole)
                        self._log_project(pid, pname, True, used_api,
                                          (stated_avail, stated_total), stated,
                                          len(data["props"]), stated_source, whole)
                        if not data["props"]:
                            self._bump("project published no lots")
                            logger.info("  [%2d/%d] project %s (%s): no lots published",
                                        i, len(pids), pid, data["header"].get("project", "")[:40])
                            continue
                        # pid travels with the row: it is the only thing that can turn
                        # "somewhere in Proxima" into a link to the project page, and
                        # Colin lost eight minutes of a call to not having it.
                        rows = [r for r in (self._row(d, data["header"], pid)
                                            for d in data["props"]) if r]
                        self._mark_overseas(pid, rows)
                        if rows:
                            self.projects_with_stock += 1
                        else:
                            self._bump("project with no priced lot")
                        results.extend(rows)
                        logger.info("  [%2d/%d] project %s: %d lot(s) of %d  %s",
                                    i, len(pids), pid, len(rows), len(data["props"]),
                                    (data["header"].get("project") or "")[:44])
                    except Exception as e:
                        self._bump("project errored")
                        logger.warning("  project %s failed: %s", pid, e)
                # ---------------------------------------------------------------
                # THE PROJECTS THE INDEX NEVER RENDERS. See _PROJECT_OPTIONS_JS: the
                # index is a "top projects" front page of 40, the account's dropdown
                # lists 118, and the 78 in between were simply never harvested. That is
                # where Coleen's Liverpool apartment was.
                try:
                    options = scraper.page.evaluate(_PROJECT_OPTIONS_JS) or []
                except Exception as e:                                   # noqa: BLE001
                    logger.warning("Proxima: could not read the project dropdown: %s", e)
                    options = []
                extra = [o for o in options
                         if o.get("id") and o["id"] not in seen and o.get("name")]
                if extra and not off_body:
                    # Said out loud rather than skipped quietly: this is 66% of the
                    # portfolio and its absence used to be invisible.
                    logger.warning(
                        "Proxima: %d project(s) are in the account dropdown but not on the "
                        "index, and no POST body was captured to read them with. They are "
                        "NOT in this harvest.", len(extra))
                elif extra:
                    logger.info("Proxima: %d project(s) off the index — reading by id.",
                                len(extra))
                    for j, o in enumerate(extra, 1):
                        pid, name = o["id"], o["name"]
                        try:
                            data = self._read_off_index(scraper, pid, name, off_body)
                            if not data:
                                self._bump("off-index project published no lots")
                                continue
                            props = data["props"]
                            # Its OWN body (data["_body"]), never off_body: replaying the
                            # donor is how 13 projects read "83 in hand" on 6 Oct. The
                            # header states no counter off the index, so the stored one
                            # is what a shortfall is measured against.
                            stated = self.stored_counters.get(pid)
                            stated_source = "stored" if stated is not None else None
                            self._topup = {}
                            if len(props) >= _RENDER_CAP:
                                props = self._top_up_capped(scraper, pid, data["_body"],
                                                            props, stated, name=name)
                            whole = self._whole(len(props))
                            self._warn_if_short(pid, name, len(props), stated,
                                                stated_source, whole)
                            self._log_project(pid, name, False, False, (None, None),
                                              stated, len(props), stated_source, whole)
                            rows = [r for r in (self._row(d, data["header"], pid)
                                                for d in props) if r]
                            self._mark_overseas(pid, rows)
                            if rows:
                                self.projects_with_stock += 1
                                self.off_index += 1
                            else:
                                self._bump("off-index project with no priced lot")
                            results.extend(rows)
                            logger.info("  [off %2d/%d] project %s: %d lot(s) of %d  %s",
                                        j, len(extra), pid, len(rows), len(props), name[:40])
                        except Exception as e:                           # noqa: BLE001
                            self._bump("off-index project errored")
                            logger.warning("  off-index project %s failed: %s", pid, e)
                    self.projects_seen += len(extra)
        except Exception as e:
            logger.exception("Proxima harvest crashed: %s", e)
            # No verdict on the login, and never outranks one this run already recorded.
            record_harvest_login(self.SESSION, "error",
                                 "the harvest crashed: %s" % type(e).__name__)
            return results

        results = self._collapse_cross_listed(results)
        logger.info("Proxima: %d lot(s) from %d/%d project(s) "
                    "(%d read via the availability view, %d topped up past the render "
                    "cap, %d off the index). Skipped: %s",
                    len(results), self.projects_with_stock, self.projects_seen,
                    self.via_api, self.topped_up, self.off_index, self.stats or "none")
        return results

    def _collapse_cross_listed(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """One physical lot can appear under two projects; store it once.

        Ascenta Living publishes the same lots under both "Coliving" and
        "Traditional" — identical address, lot number, price, land size and builder,
        differing only in the project name. That is one lot offered under two
        programmes, not two lots, and letting both through would overstate available
        stock by however many are cross-listed.

        They also collide on content_hash (estate_name is deliberately not part of
        identity), so the upsert would silently keep whichever landed last. Collapsing
        here instead makes the choice deterministic and, more importantly, COUNTED, so
        the drop shows up in the log rather than looking like lots that were never found.

        ONLY A COPY IS COLLAPSED (8 Oct 2026, second repair round, correctness verifier).
        Copies that print the same price and state the same status are one offer: kept
        once, under the project the stored row already belongs to (stored_row_projects),
        else the first in page order. Copies that DISAGREE are not copies, and each is
        stored under its own project. Before, the first in page order won whatever the
        others said, and on 8 Oct that lost two real offers:
          * Atchison and Kenny Building B (793) printed units B-1301 and B-1803 For Sale;
            Building A's availability view (161) lists them Not Available, and came first.
            Both lots were kept as A's Not Available rows (10949, 11009) and hidden from
            every search, while B's own list -- 19 for sale -- stored 17;
          * Lancelin South Stage C Land Only (767) printed lots 71, 129 and 130 as land at
            $358,600-$390,900; Stage C (802) prints the same lots as house-and-land at
            $701,500-$733,800, and came first. The land offer was gone, and the rows the
            house-and-land copy updated (19965-19967) are 767's -- carded "Land Only" at the
            house-and-land price.
        Now the offer whose project owns the stored row keeps the lot's identity (else the
        lowest project id, so it does not swap with page order), and every other offer is
        stored as a variant of it (variant_ordinal: its project id, see _offer_ordinal).

        THE MANIFEST SAYS WHICH LOT WENT WHERE. A collapsed copy is counted on its own
        project's line (cross_listed, cross_listed_into; duplicates for a lot printed twice
        in one list) AND named, with the price and status it printed and the identity of
        the row that holds it (cross_listed_lots, duplicate_lots), so the parity check's
        READ bar can count it as stored only on row-level proof: a live row under the kept
        project, read tonight, at that price and status (parity_check
        cross_listed_row_facts).
        """
        from database import building_content_hash
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            groups.setdefault(building_content_hash(r), []).append(r)
        log_of = {e["pid"]: e for e in self.project_log}
        owners = self.stored_row_projects or {}

        def pid_of(r):
            return str(r.get("source_project_id") or "")

        out: List[Dict[str, Any]] = []
        dropped, split = [], []
        for h, copies in groups.items():
            if len(copies) == 1:
                out.append(copies[0])
                continue
            owner = owners.get(h, "")
            offers: Dict[Tuple[float, str], List[Dict[str, Any]]] = {}
            for r in copies:
                offers.setdefault(_offer_key(r), []).append(r)
            # One kept copy per offer: the stored row's project's, else the first printed.
            kept = [(next((r for r in same if pid_of(r) == owner), same[0]), same)
                    for same in offers.values()]
            if len(kept) > 1:
                base = next((i for i, (r, _same) in enumerate(kept) if pid_of(r) == owner),
                            None)
                if base is None:
                    base = min(range(len(kept)), key=lambda i: _pid_order(pid_of(kept[i][0])))
                used: set = set()
                for i, (r, _same) in enumerate(kept):
                    if i != base:
                        r["variant_ordinal"] = _offer_ordinal(pid_of(r), used)
                split.append((copies[0].get("lot_address", ""),
                              ["%s (%s, $%s)" % (r.get("estate_name") or pid_of(r),
                                                 r.get("availability_status") or "no status",
                                                 format(int(_offer_key(r)[0]), ","))
                               for r, _same in kept]))
                for r, _same in kept:
                    mine = log_of.get(pid_of(r))
                    if mine is not None:
                        mine["cross_listed_offers"] = int(mine.get("cross_listed_offers")
                                                          or 0) + 1
            for keep, same in kept:
                out.append(keep)
                final = building_content_hash(keep)
                into = pid_of(keep)
                for other in same:
                    if other is keep:
                        continue
                    dropped.append((other.get("lot_address", ""),
                                    keep.get("estate_name", ""), other.get("estate_name", "")))
                    mine = log_of.get(pid_of(other))
                    if mine is None:
                        continue
                    lot = {"hash": final, "lot": str(other.get("lot_number") or ""),
                           "address": str(other.get("lot_address") or "")[:90],
                           "price": _offer_key(other)[0],
                           "status": str(other.get("availability_status") or ""),
                           "into": into}
                    if into and into != mine["pid"]:
                        mine["cross_listed"] = int(mine.get("cross_listed") or 0) + 1
                        where = mine.setdefault("cross_listed_into", {})
                        where[into] = where.get(into, 0) + 1
                        mine.setdefault("cross_listed_lots", []).append(lot)
                    else:
                        # The same lot printed twice in ONE project: stored once, said so.
                        mine["duplicates"] = int(mine.get("duplicates") or 0) + 1
                        mine.setdefault("duplicate_lots", []).append(lot)
        if dropped:
            self.cross_listed = dropped
            logger.info("  %d lot(s) cross-listed under a second project; kept once:",
                        len(dropped))
            for addr, kept_as, also in dropped[:10]:
                logger.info("     %s  [kept: %s | also in: %s]", addr[:58], kept_as, also)
        if split:
            logger.info("  %d lot(s) printed under two projects as DIFFERENT offers (price or "
                        "status); each stored under its own project:", len(split))
            for addr, said in split[:10]:
                logger.info("     %s  [%s]", addr[:58], " | ".join(said))
        return out

    def verify(self, package: Dict[str, Any]) -> Dict[str, Any]:
        return {"verified": False, "status": "Pending Confirmation", "price_change": 0.0}
