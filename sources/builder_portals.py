"""
Direct Builder Portal Search Source — LIVE Playwright scraper.

For builders NOT on E-Agent that have their own credentialed portal (Hermitage,
Bathla, FRD, Torsion, Paramount, ...). Iterates the approved builder directory,
logs into each portal with the credentials stored against that builder, and
scrapes its stock list.

Credentials come from the builder registry (Book1 CSV / env). A builder with no
portal URL or no credentials is skipped with a logged reason. A portal whose
selectors are unverified logs a "needs re-mapping" warning. Nothing is ever
fabricated — a failed portal contributes zero listings, not fake ones.
"""

import logging
import re
from datetime import datetime
from typing import List, Dict, Any

from sources.base import PropertySource
from sources.scraper_base import PlaywrightScraper, parse_price, parse_int, PLAYWRIGHT_AVAILABLE, SESSION_DIR
from sources.scraper_base import record_harvest_login, signed_out_reason
from sources.portal_config import config_for_url
from sources import adaptive_extract
from sources.adaptive_extract import extract_listings
from secrets_store import get_credentials
# NOT A HARD IMPORT (7 Oct 2026): this module is shipped inside the research function and
# harvest_manifest.py is not -- see the same note in sources/e_agent.py.
try:
    import harvest_manifest
except ImportError:                                                     # pragma: no cover
    from types import SimpleNamespace as _Stub
    harvest_manifest = _Stub(entry=lambda *a, **k: {}, tag=lambda *a, **k: None)
from builder_registry import BuilderRegistry
from builder_names import is_withdrawn_builder

logger = logging.getLogger("spb.scraper.portals")


# How hard we try to reach the bottom of a stock list. 40 rounds at ~600ms is under a
# minute per portal, and every portal seen so far settles in under 10.
# Widens any "show N entries" control to its largest option. Returns the value chosen,
# or "" when the page has no such control. Deliberately blind to the table library: it
# only looks for a <select> whose options are numbers.
_EXPAND_PAGE_LENGTH_JS = r"""
() => {
  const sel = [...document.querySelectorAll('select')].find(
    e => [...e.options].length > 1 && [...e.options].every(o => /^-?\d+$/.test(o.value)));
  if (!sel) return '';
  const best = [...sel.options].map(o => o.value)
      .sort((a, b) => Math.abs(+b) - Math.abs(+a))[0];
  if (sel.value === best) return '';
  sel.value = best;
  sel.dispatchEvent(new Event('change', { bubbles: true }));
  return best;
}
"""


def _stamp_page(rows, url: str):
    """Every row names the page it was read from when its card has no link of its own.

    A row with no source_url has no document for the search to group it with, and is
    refused as "no_document" (api/_candidates admission_refusal). On 6 Oct 32 live portal
    rows had none -- Torsion 17, Bathla 14, Hermitage 1 (7 Oct 2026). A card's own link,
    where it has one, still wins: it is the better pointer.
    """
    for r in rows or ():
        if not str(r.get("source_url_or_ref") or "").strip():
            r["source_url_or_ref"] = url
    return rows


_SCROLL_ROUNDS = 40
_MORE_BUTTON = ("button:has-text('Load more'), button:has-text('Show more'), "
                "a:has-text('Load more'), a:has-text('Show more'), "
                "button:has-text('Next'), a[rel=next]")


def _load_every_card(scraper, cfg) -> None:
    """Scroll and click until the stock list stops growing.

    NOTHING IN THIS FILE PAGED. The listings URL was opened once and whatever had
    rendered was taken as the whole stock, so a portal that lazy-loads or paginates gave
    us its first screen and no more. On 3 Sep that read Hermitage at 92 against the 171
    its own config records at /allpackages, and Torsion at exactly 80 -- a round number is
    a render cap, not a coincidence, and this codebase has met one before: Proxima's
    accordion serves 80 lots per project and needed a price-asc/price-desc union to get
    past it. The dashboard showed all three as "ONLY PART OF ITS STOCK" and the cause was
    on our side.

    Counting is what makes it safe to stop: the loop ends when a round adds nothing, so a
    portal that fits on one screen costs one extra count and a page that never settles is
    bounded by _SCROLL_ROUNDS rather than running forever.
    """
    # COUNT WHAT THE HARVEST ACTUALLY READS. The configured listing_card_selector is a
    # snapshot of a page's markup and goes stale: on Hermitage it now matches NOTHING,
    # and the 92 listings the harvest reports come from the adaptive extractor instead.
    # A loop that counted the selector saw 0, concluded the list was fully loaded, and
    # returned before scrolling once.
    from .adaptive_extract import FIND_CARDS_JS

    selector = cfg.listing_card_selector or ""

    def _count():
        n = 0
        if selector:
            try:
                n = len(scraper.page.query_selector_all(selector))
            except Exception:                               # noqa: BLE001
                n = 0
        if n:
            return n
        try:
            found = scraper.page.evaluate(FIND_CARDS_JS)
            return len((found or {}).get("cards") or [])
        except Exception:                                   # noqa: BLE001
            return 0

    # A PAGED TABLE SHOWS TEN ROWS AND CALLS IT THE STOCK LIST.
    #
    # Hermitage's /allpackages is a DataTables grid with a "1 2 3 4 5 ... 10" pager and a
    # page-length select defaulting to 10. Nothing here ever touched either, so the
    # harvest read the first page and reported it as the whole portal -- 92 listings
    # against the 171 the portal's own config records, and "ONLY PART OF ITS STOCK" on
    # the dashboard every day. Scrolling does not help: the pager is JS, not lazy load.
    #
    # Setting the length select to its largest option is one action and needs no
    # knowledge of the pager's markup. Measured live on Hermitage: 92 -> 182.
    try:
        widened = scraper.page.evaluate(_EXPAND_PAGE_LENGTH_JS)
        if widened:
            scraper.page.wait_for_timeout(2500)
            logger.info("[%s] page length set to %s so the whole table renders.",
                        cfg.name, widened)
    except Exception:                                       # noqa: BLE001
        pass

    seen = _count()
    start = seen
    for _ in range(_SCROLL_ROUNDS):
        try:
            scraper.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            more = scraper.page.query_selector(_MORE_BUTTON)
            if more:
                try:
                    more.click(timeout=2000)
                except Exception:                           # noqa: BLE001
                    pass
            scraper.page.wait_for_timeout(600)
        except Exception:                                   # noqa: BLE001
            break
        now = _count()
        if now <= seen:
            break
        seen = now
    if seen > start:
        logger.info("[%s] %d card(s) after loading the whole list, up from %d on first paint.",
                    cfg.name, seen, start)


class BuilderPortalSource(PropertySource):
    def __init__(self, registry: BuilderRegistry = None):
        self.registry = registry or BuilderRegistry()

    @property
    def channel_name(self) -> str:
        return "Direct Builder Portal (live)"

    def _login(self, scraper: PlaywrightScraper, cfg, email: str, password: str) -> bool:
        """Sign in, and leave in self._login_how / self._login_reason how it went: the
        harvest's own sign-in outcome, which the Settings tab's Login column reports
        (scraper_base.record_harvest_login)."""
        page = scraper.page
        self._login_how, self._login_reason = "bounced", ""
        scraper.goto(cfg.login_url)
        if cfg.logged_in_selector and scraper.is_logged_in(cfg.logged_in_selector):
            self._login_how = "session"
            self._login_reason = "the saved session was accepted at the login page"
            return True  # reused a saved session (created by portal_login.py)
        if not (email and password):
            logger.error("[%s] no saved session and no credentials. Run: python portal_login.py \"%s\"",
                         cfg.name, cfg.name)
            self._login_how, self._login_reason = "missing", "no password stored"
            return False
        try:
            if cfg.open_login_selector:
                try:
                    page.click(cfg.open_login_selector, timeout=6000)
                    scraper.throttle()
                except Exception:
                    pass
            page.fill(cfg.email_selector, email, timeout=10000)
            # Two-step portals (Bathla, FRD): submit the identifier first, then the
            # password appears on the next screen.
            if cfg.continue_selector and not page.query_selector(cfg.password_selector):
                try:
                    page.click(cfg.continue_selector, timeout=8000)
                    page.wait_for_selector(cfg.password_selector, timeout=15000)
                    scraper.throttle()
                except Exception as e:
                    logger.error("[%s] two-step login stalled after the identifier step: %s", cfg.name, e)
                    self._login_reason = ("the password step never appeared after the "
                                          "identifier (%s)" % type(e).__name__)
                    return False
            page.fill(cfg.password_selector, password, timeout=10000)
            page.click(cfg.submit_selector, timeout=10000)
            page.wait_for_load_state("networkidle", timeout=20000)
            scraper.throttle()
        except Exception as e:
            logger.error("[%s] login interaction failed: %s", cfg.name, e)
            # The form broke, not necessarily the login: no verdict either way.
            self._login_how = "error"
            self._login_reason = "the sign-in form failed: %s" % type(e).__name__
            return False
        if cfg.logged_in_selector and not scraper.is_logged_in(cfg.logged_in_selector):
            logger.error("[%s] login did not reach an authenticated state.", cfg.name)
            self._login_reason = ("the stored password did not sign in: %s"
                                  % signed_out_reason(page))
            return False
        self._login_how = "signed_in"
        self._login_reason = "signed in with the stored password"
        return True

    def _scrape_builder(self, builder: Dict[str, Any], filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """One portal's stock, with its line in data/harvest_manifest.json."""
        cfg = config_for_url(builder["portal_url"])
        if not cfg:
            return []
        from urllib.parse import urlparse
        entry = harvest_manifest.entry(
            cfg.source_channel or self.channel_name, "portal", builder["builder_name"],
            name=builder["builder_name"], host=urlparse(cfg.listings_url or "").netloc)
        self._outcome = ""
        self._page_stats = {"found": 0, "read": 0}
        rows = self._scrape_builder_pages(builder, filters, cfg)
        page = self._page_stats
        harvest_manifest.tag(rows, entry)
        # A builder licensed in two states is scraped twice; one good read is a read.
        outcome = self._outcome or ("read" if rows else "zero-yield")
        entry.update(outcome="read" if entry.get("outcome") == "read" else outcome,
                     printed=max(int(entry.get("printed") or 0), len(rows)),
                     cards_found=max(int(entry.get("cards_found") or 0), page["found"]),
                     cards_read=max(int(entry.get("cards_read") or 0), page["read"]))
        return rows

    def _scrape_builder_pages(self, builder: Dict[str, Any], filters: Dict[str, Any],
                              cfg) -> List[Dict[str, Any]]:
        if not cfg.verified:
            logger.warning("[%s] portal selectors are UNVERIFIED — confirm against live DOM in portal_config.py.", cfg.name)
        # Per-portal channel where one is configured (Proxima), else the generic one.
        channel = cfg.source_channel or self.channel_name
        session_name = "portal_" + re.sub(r"[^a-z0-9]+", "_", builder["builder_name"].lower()).strip("_")
        # Credentials at RUN TIME: OS vault -> env/.env -> vendor CSV.
        email, password, cred_source = get_credentials(
            session_name,
            (builder.get("portal_login_email") or "", builder.get("portal_login_password") or ""),
        )
        if email:
            logger.info("[%s] credentials resolved from %s", cfg.name, cred_source)
        has_session = (SESSION_DIR / f"{session_name}.json").exists()
        results: List[Dict[str, Any]] = []
        try:
            scraper = PlaywrightScraper(session_name=session_name)
            with scraper.session():
                # Try the stock list FIRST — it may be public, or the saved session
                # may still be valid. Only authenticate if nothing is readable.
                scraper.goto(cfg.listings_url)
                st0 = builder["states"][0] if builder.get("states") else filters.get("state", "")
                probe = _stamp_page(extract_listings(
                    scraper.page, builder_hint=builder["builder_name"], state_hint=st0),
                    cfg.listings_url)

                # A SIGNED-OUT PORTAL THAT STILL RENDERS A FEW CARDS READ AS A SUCCESS.
                #
                # The test below is "did anything at all come back", so a public teaser
                # page — the handful of listings a portal shows a logged-out visitor —
                # satisfied it and the saved session was never checked. has_session was
                # computed and then never referenced again; is_logged_in() is only called
                # inside _login, which this path never enters. Every row was stamped
                # verified: True regardless.
                #
                # scraper_base already names this hazard: a partially authenticated
                # session is worse than none, because it looks like a small day.
                # _collapsed_channels cannot catch it either — all five portals pool into
                # one "Direct Builder Portal (live)" channel and its floor is disabled
                # below 20 rows, so one portal degrading stays invisible.
                #
                # So when we HAVE a session, it has to prove itself: if the logged-in
                # marker is absent, that is a login trigger, not a result.
                signed_in, gate_measured = True, False
                if getattr(cfg, "no_login_required", False):
                    # Nothing to be signed out OF. Asked the question anyway, this portal
                    # answered False for the honest reason that it has no Log Out marker,
                    # and the branch below binned a perfectly good public read every
                    # night because there were no credentials to "recover" with.
                    pass
                elif has_session and cfg.logged_in_selector:
                    try:
                        gate_measured = True
                        # is_logged_in, NOT query_selector on the whole config string.
                        # Every logged_in_selector in portal_config starts with "text="
                        # and contains a comma, and a text= selector takes everything
                        # after the "=" as ONE literal string -- commas alternate only in
                        # the CSS engine. So this line asked each portal for the literal
                        # text "Log Out, text=Logout, text=Dashboard", never found it, and
                        # was CONSTANT-FALSE for every portal on every night since it was
                        # written. is_logged_in splits the alternatives, vetoes on a
                        # visible password field and falls back to a DOM heuristic -- its
                        # own docstring names this exact hazard, and L149/L180 already
                        # call it. Only this gate, the one that decides whether to trust a
                        # saved session, went round it.
                        #
                        # Measured 21 Sep: Hermitage renders "Welcome, Lee ... Logout" and
                        # 138 rows with its session and redirects to /login with 0 rows
                        # without it -- provably signed in, declared dead nightly, and
                        # re-authenticated with stored credentials against a live session.
                        # Torsion is worse: a public 235-row stocklist thrown away every
                        # night because the forced re-auth finds no credentials to use.
                        signed_in = scraper.is_logged_in(cfg.logged_in_selector)
                    except Exception:                                   # noqa: BLE001
                        gate_measured = False     # cannot tell, so nothing is recorded
                        signed_in = True          # cannot tell; do not throw away a read
                    if not signed_in:
                        logger.warning("[%s] a saved session exists but the page is not "
                                       "signed in — %d listing(s) visible are the public "
                                       "view, not the full stock. Re-authenticating.",
                                       cfg.name, len(probe or []))
                    elif gate_measured:
                        # The harvest's own answer for the Settings tab: this read is
                        # signed in, on the saved session (record_harvest_login).
                        self._note_sign_in(scraper, session_name, "session",
                                           "the saved session was accepted")

                if (not probe and not scraper.page.query_selector_all(cfg.listing_card_selector)) \
                        or not signed_in:
                    if not (email and password):
                        logger.warning("[%s] stock list not readable and no credentials stored. "
                                       "Sign in once: python portal_login.py \"%s\"",
                                       cfg.name, builder["builder_name"])
                        self._outcome = "no credentials"
                        self._note_sign_in(
                            scraper, session_name, "bounced" if has_session else "missing",
                            "the saved session is not signed in (%s) and no password is "
                            "stored" % signed_out_reason(scraper.page) if has_session
                            else "no saved session and no password stored")
                        return []
                    if not self._login(scraper, cfg, email, password):
                        self._outcome = "login failed"
                        self._note_sign_in(scraper, session_name, self._login_how,
                                           self._login_reason)
                        if probe and not signed_in:
                            logger.error("[%s] could not sign in; refusing the %d public "
                                         "listing(s) rather than storing a teaser as the "
                                         "whole stock.", cfg.name, len(probe))
                        return []
                    self._note_sign_in(scraper, session_name, self._login_how,
                                       self._login_reason)
                    scraper.goto(cfg.listings_url)
                # JS-rendered stock grids (e.g. Paramount's filter UI) finish painting
                # well after networkidle — wait for the configured card selector before
                # reading, otherwise extraction runs against an empty grid.
                if cfg.listing_card_selector:
                    try:
                        scraper.page.wait_for_selector(cfg.listing_card_selector, timeout=15000)
                        scraper.page.wait_for_timeout(1200)
                    except Exception:
                        pass

                _load_every_card(scraper, cfg)

                # Re-probe adaptively (page may have changed after a login) and take
                # whichever path actually yields listings. A loose generic selector
                # like 'tr' can "match" rows that hold no readable fields, so match
                # count alone must never decide.
                adaptive = _stamp_page(extract_listings(
                    scraper.page, builder_hint=builder["builder_name"], state_hint=st0),
                    cfg.listings_url) or probe
                # the main grid's found/read, before an extra page overwrites LAST_PAGE
                self._page_stats = dict(adaptive_extract.LAST_PAGE)
                cards = scraper.page.query_selector_all(cfg.listing_card_selector)
                fs = cfg.field_selectors
                for card in cards:
                    title = scraper.text_or_none(card, fs.get("title", "h3"))
                    price = parse_price(scraper.text_or_none(card, fs.get("price", ".price")))
                    if not title or not price:
                        continue
                    link_el = card.query_selector(cfg.link_selector)
                    href = link_el.get_attribute("href") if link_el else cfg.listings_url
                    results.append({
                        "lot_address": title,
                        "suburb": scraper.text_or_none(card, fs.get("suburb", ".suburb")) or "",
                        "state": builder["states"][0] if builder.get("states") else filters.get("state", ""),
                        "builder_name": builder["builder_name"],
                        "advertised_package_price": price,
                        "bedrooms": parse_int(scraper.text_or_none(card, fs.get("beds", ".beds"))),
                        "bathrooms": parse_int(scraper.text_or_none(card, fs.get("baths", ".baths"))),
                        "car_spaces": parse_int(scraper.text_or_none(card, fs.get("cars", ".cars"))),
                        "source_channel": channel,
                        "source_url_or_ref": href,
                        "date_checked": datetime.now().strftime("%d/%m/%Y"),
                        "verified": True,
                    })
                # Some portals split inventory across several pages (e.g. Bathla
                # /display + /granny) — collect those too.
                # MERGED INTO BOTH LISTS, not just the adaptive one. These are pages
                # the main grid does not contain at all (Bathla /display + /granny), so
                # they are additive to whichever path wins — but they were appended only
                # to `adaptive`, and the comparison below then discarded them wholesale
                # whenever the mapped selectors read more. The log had already claimed
                # them: "[Bathla] +4 listing(s) from .../granny", for listings that were
                # then thrown away.
                for extra_url in (cfg.extra_listings_urls or ()):
                    try:
                        scraper.goto(extra_url)
                        more = _stamp_page(extract_listings(
                            scraper.page, builder_hint=builder["builder_name"],
                            state_hint=st0), extra_url)
                        if more:
                            logger.info("[%s] +%d listing(s) from %s", cfg.name, len(more), extra_url)
                            adaptive = (adaptive or []) + more
                            results = (results or []) + more
                        else:
                            # /promotions has produced zero log lines in ten runs, and
                            # nobody can tell "this page is empty" from "this page 404s".
                            logger.info("[%s] extra stock page %s returned nothing.",
                                        cfg.name, extra_url)
                    except Exception as e:
                        logger.warning("[%s] extra stock page %s failed: %s", cfg.name, extra_url, e)

                # Whichever path read more real listings wins.
                if len(adaptive) > len(results):
                    for a in adaptive:
                        a["source_channel"] = channel
                        a["date_checked"] = datetime.now().strftime("%d/%m/%Y")
                        a["verified"] = True
                    logger.info("[%s] captured %d live listing(s) (adaptive).", cfg.name, len(adaptive))
                    return adaptive
            if results:
                logger.info("[%s] captured %d live listing(s) (mapped selectors).", cfg.name, len(results))
            else:
                logger.warning("[%s] no readable listings on %s.", cfg.name, cfg.listings_url)
        except Exception as e:
            logger.exception("[%s] scraper crashed: %s", cfg.name, e)
            self._outcome = "crashed"
            # No verdict on the login, and never outranks one this run already recorded.
            record_harvest_login(session_name, "error",
                                 "the harvest crashed: %s" % type(e).__name__)
            return []
        return results

    @staticmethod
    def _note_sign_in(scraper, session_name: str, outcome: str, reason: str) -> None:
        """The harvest's own sign-in outcome for one portal, with the page it ended on."""
        try:
            url = scraper.page.url
        except Exception:                                             # noqa: BLE001
            url = ""
        record_harvest_login(session_name, outcome, reason, url)

    def search(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not PLAYWRIGHT_AVAILABLE:
            logger.error("Builder portal search skipped: Playwright not installed.")
            return []
        state = filters.get("state", "").upper()
        max_budget = float(filters.get("budget_max", 10_000_000))

        # Full coverage: every state-eligible builder with a real direct portal.
        # Skip E-Agent (handled by EAgentSource), email-only pseudo-links
        # ("Login to ... outlook"), Google Drive links, and blank/junk entries.
        # "proxima" is excluded because sources/proxima.py owns it now: its stock is
        # not on a scrapable stock page (it lives in data-* attributes inside project
        # accordions) and it needs the persistent browser profile. Left in this loop,
        # the generic scraper logs a failed login against the same portal and emits
        # rows under the SAME "Proxima" source_channel, which would collide with the
        # real harvest's identities.
        def _real_portal(u: str) -> bool:
            u = (u or "").strip().lower()
            return ("." in u) and not any(x in u for x in ("outlook", "e-agent", "drive.google",
                                                           "proxima", "----"))
        # A real non-E-Agent portal URL is its own stock source, so scrape it even if
        # the builder is also listed on E-Agent (E-Agent is handled separately).
        # A builder the client has withdrawn is not signed in to at all (builder_names).
        targets = [
            b for b in self.registry.get_builders_by_state(state)
            if _real_portal(b.get("portal_url"))
            and not is_withdrawn_builder(b.get("builder_name"))
        ]
        if not targets:
            logger.info("Builder portal search: no direct-portal builders in scope for %s.", state)
            return []

        all_results: List[Dict[str, Any]] = []
        for builder in targets:
            all_results.extend(self._scrape_builder(builder, filters))

        return [r for r in all_results if r["advertised_package_price"] <= max_budget + 50_000]

    def verify(self, package: Dict[str, Any]) -> Dict[str, Any]:
        # Per-builder re-verification would re-login to that builder's portal;
        # until selectors are confirmed we mark for human confirmation.
        return {"verified": False, "status": "Pending Confirmation", "price_change": 0.0}
