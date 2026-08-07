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
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sources.base import PropertySource
from sources.scraper_base import PlaywrightScraper, PLAYWRIGHT_AVAILABLE
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
_FILTER_FIELDS = ('select[name^="property["], '
                  'input[name^="property["]:not([type=hidden])')

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
    const name = (el.getAttribute('name') || '').replace(/^property\[/, '').replace(/\]$/, '');
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
_CLEAR_AND_SUBMIT_JS = r"""
(cfg) => {
  const {sel, defaults} = cfg;
  let used = [];
  for (const s of ['#search_clear', '.filter-form-reset', '#search_reset',
                   '[data-action-code="RESET"]']) {
    const el = document.querySelector(s);
    if (el) { try { el.scrollIntoView(); } catch (e) {} el.click(); used.push(s); break; }
  }
  document.querySelectorAll(sel).forEach(el => {
    const name = (el.getAttribute('name') || '').replace(/^property\[/, '').replace(/\]$/, '');
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

    def __init__(self, registry=None):
        self.registry = registry
        self.stats: Dict[str, int] = {}
        self.projects_seen = 0
        self.projects_with_stock = 0
        self.cross_listed: List = []
        self.via_api = 0
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
        """
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
        return data

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

    def search(self, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        if not PLAYWRIGHT_AVAILABLE:
            logger.error("Proxima skipped: Playwright is not installed.")
            return []

        scraper = PlaywrightScraper(session_name=self.SESSION)
        if not (scraper.profile_dir.exists() or scraper.session_file.exists()):
            logger.error("Proxima has no saved sign-in. Run once, in a real browser: "
                         "python portal_login.py portal_proxima --profile")
            return []

        results: List[Dict[str, Any]] = []
        try:
            with scraper.session():
                scraper.goto(PROJECTS_URL)
                if "twofactor" in scraper.page.url or "/login" in scraper.page.url:
                    logger.error("Proxima bounced to %s — the saved sign-in has expired. "
                                 "Re-run: python portal_login.py portal_proxima --profile",
                                 scraper.page.url)
                    return []

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

                for i, pid in enumerate(pids, 1):
                    try:
                        data = self._open_and_read(scraper, pid)
                        if not data:
                            self._bump("project unreadable")
                            continue
                        # Where an availability view exists, it beats the accordion:
                        # uncapped, and it includes lots that are no longer for sale.
                        if pid in avail_pids:
                            api = self._read_via_api(scraper, pid)
                            if api:
                                if len(api) >= len(data["props"]):
                                    logger.info("     project %s: availability view has "
                                                "%d lot(s) vs %d in the page",
                                                pid, len(api), len(data["props"]))
                                    data = {"header": data["header"], "props": api}
                                    self.via_api += 1
                                else:
                                    logger.info("     project %s: availability view had "
                                                "fewer (%d < %d) — keeping the page",
                                                pid, len(api), len(data["props"]))
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
        except Exception as e:
            logger.exception("Proxima harvest crashed: %s", e)
            return results

        results = self._collapse_cross_listed(results)
        logger.info("Proxima: %d lot(s) from %d/%d project(s) "
                    "(%d read via the availability view). Skipped: %s",
                    len(results), self.projects_with_stock, self.projects_seen,
                    self.via_api, self.stats or "none")
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
        here instead makes the choice deterministic — first project wins, in page
        order — and, more importantly, COUNTED, so the drop shows up in the log rather
        than looking like lots that were never found.
        """
        from database import building_content_hash
        seen: Dict[str, Dict[str, Any]] = {}
        dropped = []
        for r in rows:
            h = building_content_hash(r)
            if h in seen:
                dropped.append((r.get("lot_address", ""), seen[h].get("estate_name", ""),
                                r.get("estate_name", "")))
                continue
            seen[h] = r
        if dropped:
            self.cross_listed = dropped
            logger.info("  %d lot(s) cross-listed under a second project; kept once:",
                        len(dropped))
            for addr, kept, also in dropped[:10]:
                logger.info("     %s  [kept: %s | also in: %s]", addr[:58], kept, also)
        return list(seen.values())

    def verify(self, package: Dict[str, Any]) -> Dict[str, Any]:
        return {"verified": False, "status": "Pending Confirmation", "price_change": 0.0}
