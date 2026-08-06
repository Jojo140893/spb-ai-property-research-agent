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
from typing import Any, Dict, List, Optional

from sources.base import PropertySource
from sources.scraper_base import PlaywrightScraper, PLAYWRIGHT_AVAILABLE
from builder_names import is_not_a_builder_name

logger = logging.getLogger("spb.scraper.proxima")

PROJECTS_URL = "https://portal.proxima.com.au/agent/projects/index/"

_STATES = ("NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT")

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


def _lot_number(raw: str) -> str:
    """'00000014/00000014' -> '14'. Returns '' when there is nothing real."""
    if not raw:
        return ""
    first = str(raw).split("/")[0].strip()
    trimmed = first.lstrip("0")
    return trimmed or ""


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
    if parts and parts[-1].upper() in _STATES:
        out["state"] = parts.pop().upper()
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

    @property
    def channel_name(self) -> str:
        # Must match PortalConfig("proxima.com.au").source_channel: it is part of the
        # identity hash AND it is the entry Colin filters on in the dashboard.
        return "Proxima"

    def _bump(self, key: str) -> None:
        self.stats[key] = self.stats.get(key, 0) + 1

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

        project = (header.get("project") or "").strip()
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
            "estate_name": project,
            "lot_number": lot,
            "postcode": a["postcode"],
            "advertised_package_price": price,
            # Kept separately when the package price supersedes it, so the breakdown is
            # visible and the two are never confused again.
            "land_price": rop if (pkg and rop and abs(pkg - rop) > 1) else None,
            "bedrooms": _int(d.get("room")),
            "bathrooms": _int(d.get("bathroom")),
            "car_spaces": _int(d.get("carspace")),
            "land_size_sqm": _num(d.get("landsize")),
            "house_size_sqm": _num(d.get("internal_area")),
            "frontage_m": _num(d.get("propertywidth")),
            "title_status": (d.get("_titled") or "").strip(),
            # What Proxima itself displays ("For Sale", "Sold", ...). Never derived
            # from the reservation class, which answers a different question.
            "availability_status": (d.get("_status") or "").strip(),
            "floorplan_url": (d.get("_floorplan") or "").strip(),
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
