"""
E-Agent Portal Search Source — LIVE Playwright scraper.

Primary source for approved builders listed on E-Agent (e-agent.com.au, a Wix
members site). Logs in with credentials from the environment, scrapes the
Access Projects stock list, and returns real candidate packages.

No credentials, Playwright missing, or login/DOM failure -> logs the reason and
returns [] (never fabricated data). Selectors live in portal_config.EAGENT_CONFIG.
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse

import config
from sources.base import PropertySource
from sources.scraper_base import PlaywrightScraper, ScraperError, parse_price, parse_int, PLAYWRIGHT_AVAILABLE, SESSION_DIR
from sources.portal_config import EAGENT_CONFIG
from sources.adaptive_extract import extract_listings
from sources.spreadsheet_extract import extract_stocklist
from sources import remote_stocklist as remote
from secrets_store import get_credentials

logger = logging.getLogger("spb.scraper.eagent")

# Words to strip out of a stocklist FILE name to leave the development it belongs to.
_FILENAME_NOISE = re.compile(
    r"\b(master\s*-?\s*price\s*-?\s*list|masterpricelist|price\s*-?\s*list|pricelist|"
    r"pricing|stock\s*-?\s*list|stocklist|availabilit\w*|release|as\s*at|bonus|"
    r"master|final|current|updated?|copy|new|report|flyer|brochure|sales?|"
    r"january|february|march|april|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|"
    r"v\d+|rev\s*\d*|jr|"
    # last, so the multi-word forms above win the alternation
    r"price|prices|list|sheet)\b", re.I)
# Structural words. A name made only of these describes a slice of a development, not
# the development: "Stage 1" tells the client nothing about whose stock it is.
_STRUCTURAL_WORDS = frozenset(
    "stage lot lots tower level precinct block building stock unit units apartment "
    "apartments townhouse townhouses villa villas house houses land one two three four "
    "five part final release north south east west".split())


def _structural_only(name: str) -> bool:
    tokens = [t for t in re.split(r"[\s\W]+", name.lower()) if t]
    return bool(tokens) and all(t in _STRUCTURAL_WORDS or t.isdigit() for t in tokens)


def _project_from_label(label: str) -> str:
    """Name the development from the FILE it was published as.

    The apartment, townhouse and commercial pages group stock by DEVELOPMENT, not by
    builder, and on some of them the development name is in no heading element at all —
    but it is in the file name ("eastbury-wheelers-hill-pricelist.pdf").

    Held to a high bar on purpose. A first pass at this put "Masterpricelist Millwell
    New", "Report Murcia", "Pricelist" and "Access Portal" in the builder column across
    374 rows, which is worse than a blank: it looks like data. Anything that does not
    reduce to a plausible name returns "" so the field stays empty and honest.
    """
    label = label or ""
    # Must be a file name from a folder listing. Without this the LINK's own text leaks
    # through and every apartment project is called "Access Portal".
    if "/" not in label:
        return ""
    name = label.rsplit("/", 1)[-1].strip()
    name = re.sub(r"\.(xlsx|xlsm|xls|csv|pdf)$", "", name, flags=re.I)
    # Dates first, whole: "15-6-26" must go before "-" becomes a space, or the 6 survives.
    name = re.sub(r"\d{1,2}[-./ ]\d{1,2}[-./ ]\d{2,4}", " ", name)
    name = re.sub(r"[_\-]+", " ", name)
    name = re.sub(r"\[.*?\]|\(.*?\)", " ", name)            # "[ ]", "(1)"
    # Split letter->digit runs BEFORE matching noise words: "Pricelist2026.xlsx" has no
    # word boundary after "Pricelist", so \bpricelist\b never matched it and 75 rows were
    # attributed to a builder called "Pricelist".
    name = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", name)
    name = _FILENAME_NOISE.sub(" ", name)
    name = re.sub(r"[\d.]{3,}", " ", name)                  # version/serial runs
    name = re.sub(r"\s+", " ", name).strip(" -–—.,&")
    name = re.sub(r"(?:\s+\b[A-Za-z]\b)+$", "", name)       # trailing stray initials
    name = re.sub(r"\s+", " ", name).strip(" -–—.,&")
    # A real name has a word in it. Engineering-plan file names reduce to initialisms
    # ("1101438-16S-PS-V1.pdf" -> "16S PS"), which name nothing.
    if not (2 < len(name) <= 44) or not re.search(r"[A-Za-z]{4}", name):
        return ""
    if _structural_only(name):
        return ""
    return name.title()


"""Words that make a name a COMPANY rather than a place.

`_project_from_label` may return an estate ("Eastbury Wheelers Hill") because its caller
only wants the development. A banner is read for the opposite purpose — to put a name in
builder_name — so a much stricter test applies: it has to look like a business. Covers 31
of the 36 builders currently in the database; the five it misses (Bathla, Ausbuild, APLACE
by Glenville, Met Invest by Metricon, Proxima) return "" and stay blank, which is the
intended failure. Guessing an estate into builder_name is the bug this file is fixing.
"""
_COMPANY_WORDS = re.compile(
    r"\b(homes?|construction|constructions|constructs?|developments?|developers?|"
    r"projects?|group|builders?|living|residential|building|build|properties|property|"
    r"corporation|holdings|investments?|pty|ltd)\b", re.I)
# State names and codes sit inside a banner ("CREATION HOMES NSW STOCK LIST") but are not
# part of the company's name.
_STATE_WORDS = re.compile(
    r"\b(new\s+south\s+wales|victoria|queensland|south\s+australia|western\s+australia|"
    r"tasmania|northern\s+territory|australian\s+capital\s+territory|"
    r"nsw|vic|qld|sa|wa|nt|act|tas|australia)\b", re.I)


# Where a banner-derived name is recorded, so a reader can tell it from a heading.
_BANNER_SOURCE = "e-agent stocklist banner"

# Headings that are page furniture, not builders. Without this filter the crawl
# would attribute stock to "Live Packages" — a wrong builder is worse than a blank.
# Module level because a stocklist file's own title row is held to the same test.
_NOT_A_BUILDER = re.compile(
    r"^(live\s*packages?|builder\s*info|request\s*(a\s*)?marketing\s*flyer|"
    r"package\s*request|marketing\s*flyer|price\s*list|stock\s*list|available\s*stock|"
    r"house\s*&?\s*land|townhouses?|apartments?|commercial(\s*propert\w*)?|"
    r"dual\s*(key|occupancy)|projects?|our\s*builders?|new\s*(homes?|stock)|"
    r"home|about|contact|log\s*in|login|sign\s*up|access\s*projects|"
    # the site-wide page title; it is what the heading walk returns when a link has
    # no section heading of its own, so it must never be taken for a builder
    r"wholesale\s*agent\s*platform|e-?agent|"
    r"victoria|new\s*south\s*wales|queensland|south\s*australia|"
    r"vic|nsw|qld|sa|wa|nt|act|tas)$", re.I)

# Google hands out several spellings of ONE Sheets tab: the account picker adds `pli=1`,
# a copied link keeps `usp=sharing`, and the tab is named by both `?gid=` and `#gid=`.
# Eight of the ten sections on the NSW page link the same workbook and two spellings of
# it got past the seen-set, which is why the same 107 listings are in the database twice.
# Only parameters that cannot change WHICH file is served are dropped.
_DROP_QUERY_KEYS = frozenset((
    "pli", "authuser", "usp", "urp", "hl", "rtpof", "sd", "ts", "utm_source",
    "utm_medium", "utm_campaign", "utm_content", "utm_term", "_hsmi", "_hsenc"))
_VIEW_SUFFIX = re.compile(r"/(edit|export|view|preview|htmlview|pub)$", re.I)


def stocklist_file_key(href: str) -> str:
    """Identity of a stocklist FILE. Two spellings of one tab give one key.

    `#gid=` is folded into the query rather than dropped: two estates really can share a
    workbook and differ only by tab, so the gid IS part of the file's identity — but
    `?gid=0#gid=0` and `?pli=1&gid=0#gid=0` are the same tab and must not count twice.
    """
    href = (href or "").strip()
    if not href:
        return ""
    try:
        u = urlparse(href)
    except Exception:
        return href.lower()
    if not u.netloc:
        return href.lower()
    keep = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
            if k.lower() not in _DROP_QUERY_KEYS]
    if u.fragment.startswith("gid=") and not any(k.lower() == "gid" for k, _ in keep):
        keep.append(("gid", u.fragment[4:]))
    path = _VIEW_SUFFIX.sub("", u.path.rstrip("/"))     # /edit and /export are one doc
    query = urlencode(sorted(keep))
    return f"{u.scheme.lower()}://{u.netloc.lower()}{path}" + (f"?{query}" if query else "")


def _builder_from_banner(banner: str) -> str:
    """Name the builder from the stocklist file's OWN title row.

    A builder who publishes one workbook covering eleven of its estates names itself in
    cell A1 and nowhere else: 'CREATION HOMES NSW STOCK LIST' -> 'Creation Homes'. That
    row is the only builder-level fact in the file, and the E-Agent page above it says
    only which ESTATE the link belongs to.

    Held to the same high bar as `_project_from_label`, plus a company-shape test: a
    banner that does not reduce to a business name returns "" rather than a guess. The
    Leppington Rise file's banner ('Agent Stock List 167 Ingleburn Road, Leppington') is
    a street address and correctly yields nothing.
    """
    raw = (banner or "").strip(" -–—:|")
    name = _clean_banner(banner)
    if not name:
        return ""
    # Tested on the raw banner as well as the cleaned one: "New Homes" is furniture, but
    # cleaning strips "new" and leaves "Homes", which would sail past both tests below.
    if _NOT_A_BUILDER.match(name) or _NOT_A_BUILDER.match(raw):
        return ""
    if not _COMPANY_WORDS.search(name):
        return ""
    # A company word alone is not a company: "Homes" and "Group" name nobody. Something
    # has to be left once the generic part is removed — one letter is enough, because
    # "G-Developments" is a real builder on the NSW page.
    if not re.search(r"[A-Za-z]", _COMPANY_WORDS.sub(" ", name)):
        return ""
    return name.title()


def _clean_banner(banner: str) -> str:
    """Strip a banner down to the name it carries, or "" if nothing survives."""
    name = (banner or "").strip()
    # A banner is one row of a file; a row of lot data is not a banner. Cheap guard so a
    # mis-detected group header can never be read as a company.
    if "$" in name or len(name) > 90:
        return ""
    name = re.sub(r"\d{1,2}[-./ ]\d{1,2}[-./ ]\d{2,4}", " ", name)   # dates, whole
    name = re.sub(r"[_|/]+", " ", name)
    name = re.sub(r"\[.*?\]|\(.*?\)", " ", name)
    name = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", name)                 # "Pricelist2026"
    name = _FILENAME_NOISE.sub(" ", name)
    name = _STATE_WORDS.sub(" ", name)
    name = re.sub(r"[\d.]{2,}", " ", name)
    name = re.sub(r"\s*[-–—]\s*", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" -–—.,&:")
    if not (2 < len(name) <= 44) or not re.search(r"[A-Za-z]{4}", name):
        return ""
    if _structural_only(name):
        return ""
    return name


class EAgentSource(PropertySource):
    def __init__(self, registry=None):
        self.cfg = EAGENT_CONFIG
        # Credentials resolve at RUN TIME: OS vault -> env/.env -> vendor CSV.
        # Nothing is hardcoded and the plaintext never has to live in the repo.
        csv_user = csv_pass = ""
        if registry is not None:
            for b in registry.get_all_builders():
                if b.get("is_on_e_agent") and b.get("portal_login_email") and b.get("portal_login_password") \
                        and "e-agent" in (b.get("portal_url") or "").lower():
                    csv_user, csv_pass = b["portal_login_email"], b["portal_login_password"]
                    break
        csv_user = csv_user or config.E_AGENT_USERNAME
        csv_pass = csv_pass or config.E_AGENT_PASSWORD
        self.username, self.password, self.cred_source = get_credentials("e_agent", (csv_user, csv_pass))
        if self.username:
            logger.info("E-Agent credentials resolved from %s", self.cred_source)
        # One session for every off-site host, so cookies persist across a folder's
        # list-then-download pair (SharePoint and OneDrive will not serve the file
        # otherwise). Created lazily; none of these hosts needs a login.
        self._http = remote._session() if remote.REQUESTS_AVAILABLE else None

    @property
    def channel_name(self) -> str:
        return "E-Agent Portal (live)"

    def _login(self, scraper: PlaywrightScraper) -> bool:
        page = scraper.page
        scraper.goto(self.cfg.login_url)
        if scraper.is_logged_in(self.cfg.logged_in_selector):
            return True  # reused a saved session (created by portal_login.py)
        if not (self.username and self.password):
            logger.error("E-Agent: no saved session and no credentials. "
                         "Run: python portal_login.py e_agent")
            return False
        try:
            # E-Agent is a Wix members site: the page opens on a SIGN-UP panel and the
            # email/password fields only exist after clicking through
            # "Log In" -> "Log in with Email". The modal is slow, so click, wait, and
            # re-check for the password field rather than assuming one click is enough.
            for sel in ("button:has-text('Log In')", "button:has-text('Already a member')",
                        "a:has-text('Log In')", "button:has-text('Log in with Email')",
                        "button:has-text('Sign in with email')"):
                if page.query_selector(self.cfg.password_selector):
                    break
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        el.click()
                        page.wait_for_timeout(3000)
                except Exception:
                    continue
            try:
                page.wait_for_selector(self.cfg.password_selector, timeout=12000)
            except Exception:
                logger.error("E-Agent: login form never appeared (site layout may have changed).")
                return False
            page.fill(self.cfg.email_selector, self.username, timeout=10000)
            page.fill(self.cfg.password_selector, self.password, timeout=10000)
            page.click(self.cfg.submit_selector, timeout=10000)
            page.wait_for_load_state("networkidle", timeout=20000)
            scraper.throttle()
        except Exception as e:
            logger.error("E-Agent login interaction failed: %s", e)
            return False
        if not scraper.is_logged_in(self.cfg.logged_in_selector):
            logger.error("E-Agent login did not reach an authenticated state (check credentials/selectors).")
            return False
        return True

    # JS, not Python: the builder name is only recoverable from the DOM position of
    # the link relative to its section heading, which Playwright cannot express as a
    # selector. Climbs from each stocklist link collecting EVERY heading above it —
    # previous siblings first at each level, then up a level, nearest first.
    #
    # The whole chain, not just the nearest heading: the NSW House & Land page divides
    # itself into a "Builders" half and a "Projects" half, and the divider is an ancestor
    # of the per-section headings. Reading only the nearest heading cannot see it, which
    # is how 229 rows came to be filed under an ESTATE name in builder_name.
    _LINKS_WITH_HEADINGS_JS = """() => {
        const HEAD = 'h1,h2,h3,h4,h5,h6,[role="heading"]';
        const txt = e => ((e.innerText || e.textContent || '').replace(/\\s+/g, ' ')).trim();
        const chain = (el) => {
            const out = [];
            let node = el;
            while (node && node !== document.body) {
                let sib = node.previousElementSibling;
                while (sib) {
                    if (sib.matches && sib.matches(HEAD)) { const t = txt(sib); if (t) out.push(t.slice(0, 80)); }
                    const inner = sib.querySelectorAll ? [...sib.querySelectorAll(HEAD)] : [];
                    for (let i = inner.length - 1; i >= 0; i--) {
                        const t = txt(inner[i]); if (t) out.push(t.slice(0, 80));
                    }
                    sib = sib.previousElementSibling;
                }
                node = node.parentElement;
            }
            return out;
        };
        return [...document.querySelectorAll('a[href]')].map(a => {
            const c = chain(a).slice(0, 40);
            return {
                href: a.href,
                label: txt(a).slice(0, 60),
                heading: c.length ? c[0] : '',
                chain: c,
            };
        });
    }"""

    # The page's own division of itself. E-Agent's NSW House & Land page carries an
    # H1 "Builders" above five builder sections and an H1 "Projects" above ten ESTATE
    # sections; both halves look identical one heading up, so the divider is the only
    # thing that distinguishes "Bramwell Homes" (a builder) from "Kemps Estate - Austral"
    # (an estate). Matched whole, so a builder called "Project Homes" is unaffected.
    _SECTION_BUILDERS = re.compile(r"^(our\s+)?builders?$", re.I)
    _SECTION_PROJECTS = re.compile(r"^(projects?|developments?|estates?)$", re.I)

    @classmethod
    def _section_kind(cls, chain) -> str:
        """'builder', 'project', or '' — how the page says this section is grouped.

        The chain is nearest-first, so the FIRST divider found is the one that governs
        this link; a link in the Projects half sees "Projects" before it ever reaches the
        "Builders" heading further up the page.
        """
        for h in chain or ():
            h = (h or "").strip(" -–—:|")
            if cls._SECTION_BUILDERS.match(h):
                return "builder"
            if cls._SECTION_PROJECTS.match(h):
                return "project"
        return ""

    # Shared with `_builder_from_banner`: a file whose title row is "Available Stock" or
    # "Projects" must not name a builder either, and "Projects" would otherwise pass the
    # company-word test.
    _NOT_A_BUILDER = _NOT_A_BUILDER

    # Not every file under a builder's heading is stock. Goldstate Homes publishes a
    # "Builder Info" company document beside its "Live Packages" stocklist; parsing it
    # as stock would put marketing copy in the client's sheet.
    _NOT_STOCK_LABEL = re.compile(
        r"builder\s*info|marketing\s*flyer|package\s*request|eoi|expression\s*of\s*interest|"
        r"brochure|company\s*profile|inclusion|contract|terms|price\s*guide\s*only", re.I)

    def _is_builder_heading(self, heading: str) -> bool:
        h = (heading or "").strip(" -–—:|")
        if not (2 < len(h) <= 60) or not re.search(r"[A-Za-z]{3}", h):
            return False
        return not self._NOT_A_BUILDER.match(h)

    # A stocklist is identified by what the LINK SAYS, not by where it points. Filtering
    # on `_files/ugd` (E-Agent's own file store) missed 21 builders — including Tomorrow
    # Homes, which the client reaches by hand — because builders publish their live
    # packages to Google Sheets, Smartsheet, SharePoint, OneDrive and Dropbox instead.
    _STOCK_LABEL = re.compile(
        r"live\s*packages?|price\s*-?\s*list|pricelist|stock\s*-?\s*list|stocklist|"
        r"availabilit\w*|access\s*portal|part\s*h\s*&?\s*l|current\s*stock|inventory", re.I)
    _DIRECT_FILE = re.compile(r"\.(xlsx|xlsm|xls|csv|pdf)(\?|$)", re.I)

    def _stocklist_links(self, scraper: PlaywrightScraper) -> List[Dict[str, str]]:
        try:
            links = scraper.page.evaluate(self._LINKS_WITH_HEADINGS_JS)
        except Exception as e:
            logger.warning("E-Agent: could not read stocklist links: %s", e)
            return []
        out, seen = [], set()
        for l in links or []:
            href = (l.get("href") or "").strip()
            label = l.get("label") or ""
            if not href or href.lower().startswith("mailto:"):
                continue
            if not (self._STOCK_LABEL.search(label) or self._DIRECT_FILE.search(href)):
                continue
            if self._NOT_STOCK_LABEL.search(label):
                continue
            # Two estates can share one workbook and differ only by #gid=, so the tab is
            # part of a Google Sheets file's identity — but `pli=1` and `usp=` are not,
            # and taking the raw href as the key read one workbook twice.
            key = stocklist_file_key(href)
            if key in seen:
                continue
            seen.add(key)
            out.append(l)
        return out

    def _scrape_html_stocklist(self, scraper: PlaywrightScraper, url: str,
                               builder_hint: str, state_hint: str) -> List[Dict[str, Any]]:
        """Some builders publish a PAGE rather than a file (Torsion Homes). The adaptive
        HTML extractor reads it unchanged."""
        try:
            scraper.goto(url)
        except Exception as e:
            logger.warning("E-Agent: %s unreachable: %s", url, e)
            return []
        rows = extract_listings(scraper.page, builder_hint=builder_hint, state_hint=state_hint)
        if not rows:
            # An expired capability id still answers 200 with a normal-looking page and
            # no cards, so an empty result here is a failure to report, not "no stock".
            logger.warning("E-Agent: %s rendered no stock cards — the link's id has "
                           "probably been rotated.", url)
        return rows

    @staticmethod
    def _name_from_banner(rows: List[Dict[str, Any]], builder_hint: str,
                          allow_banner: bool) -> str:
        """Fill builder_name from the file's OWN title row, in place.

        Only when the page did not already name the builder. One batch of rows is one
        file, so one banner governs all of them. Returns the name used, or "".
        """
        if builder_hint or not allow_banner or not rows:
            return ""
        name = _builder_from_banner(rows[0].get("source_banner") or "")
        if not name:
            return ""
        for r in rows:
            r["builder_name"] = name
            r["builder_source"] = _BANNER_SOURCE
        return name

    def _fetch_pieces(self, scraper: PlaywrightScraper, href: str, label: str,
                      builder_hint: str, state_hint: str,
                      allow_banner: bool = False) -> List[Dict[str, Any]]:
        """Everything parseable behind one stock link, wherever it is hosted."""
        if not remote.is_offsite(href):
            try:
                data = scraper.page.context.request.get(
                    href, timeout=config.SCRAPER_NAV_TIMEOUT_MS).body()
            except Exception as e:
                logger.warning("E-Agent: could not download stocklist %s: %s", label, e)
                return []
            got = extract_stocklist(data, source_label=href, builder_hint=builder_hint)
            self._name_from_banner(got, builder_hint, allow_banner)
            return got

        out: List[Dict[str, Any]] = []
        for piece in remote.fetch_stocklist(href, builder_hint or label, self._http):
            if piece.problem:
                continue
            if piece.kind == remote.KIND_HTML:
                out.extend(self._scrape_html_stocklist(scraper, piece.url, builder_hint,
                                                       state_hint))
                continue
            # A project folder names its development in the file, not in a page heading.
            derived = "" if builder_hint else _project_from_label(piece.label)
            hint = builder_hint or derived
            got = extract_stocklist(piece.data, source_label=piece.url, builder_hint=hint)
            for g in got:
                g["stocklist"] = piece.label        # more use than a bare "Access Portal"
                if derived:
                    g["builder_source"] = "e-agent stocklist file name"
            # Per PIECE, not per link: one folder can hold several builders' files, so the
            # banner is resolved against the rows of the file it came from. A company name
            # inside the file beats a development name guessed off the file name.
            self._name_from_banner(got, builder_hint, allow_banner)
            out.extend(got)
        return out

    def _parse_stocklist(self, scraper: PlaywrightScraper, link: Dict[str, str],
                         builder_hint: str, scope: str, state_hint: str = "",
                         product_type: str = "",
                         allow_banner: bool = False) -> List[Dict[str, Any]]:
        href = link.get("href", "")
        got = self._fetch_pieces(scraper, href, link.get("label", ""), builder_hint,
                                 state_hint, allow_banner)
        for g in got:
            g["source_channel"] = self.channel_name
            g["date_checked"] = datetime.now().strftime("%d/%m/%Y")
            g["verified"] = True
            g.setdefault("stocklist", link.get("label", ""))
            g["stocklist_file"] = href
            # A link the PAGE grouped by project is still builder-attributed when the FILE
            # named its builder. `project` scope means "this name is a development, not a
            # builder" — the API blanks builder_name on those rows — so leaving a row the
            # file did name at project scope would throw the name away.
            g["attribution_scope"] = ("builder" if g.get("builder_source") == _BANNER_SOURCE
                                      else scope)
            if builder_hint:
                g.setdefault("builder_source", "e-agent category heading")
            if state_hint:
                g["source_state_hint"] = state_hint
            if product_type:
                g["product_type"] = product_type
        scraper.throttle()
        return got

    def _scrape_stocklist_files(self, scraper: PlaywrightScraper) -> List[Dict[str, Any]]:
        """The POOLED state stocklists on /access-projects. Kept because they are the
        proven source of the existing rows, but they name no builder, so they are
        tagged `state_pooled` and stay a separate identity from per-builder rows."""
        out: List[Dict[str, Any]] = []
        links = self._stocklist_links(scraper)
        for l in links:
            out.extend(self._parse_stocklist(scraper, l, "", "state_pooled"))
        logger.info("E-Agent: %d pooled listing(s) from %d stocklist file(s).", len(out), len(links))
        return out

    def _scrape_category_pages(self, scraper: PlaywrightScraper,
                               seen_files: Optional[set] = None) -> List[Dict[str, Any]]:
        """Per-builder stock, the fix for Coleen's blank builder column.

        Each category page stacks one section per builder, so the builder is the
        nearest heading above its "Live Packages" link. Never guesses: a link whose
        heading is page furniture is skipped and counted, not attributed.
        """
        page = scraper.page
        out: List[Dict[str, Any]] = []
        seen_files = seen_files if seen_files is not None else set()
        builders, unattributed = {}, 0
        for cat in (self.cfg.category_pages or ()):
            # One retry. The Victoria apartments page timed out on a live run and took
            # all 24 of its developments with it, which reads exactly like "no stock here".
            loaded = False
            for attempt in (1, 2):
                try:
                    scraper.goto(cat.url)
                    loaded = True
                    break
                except Exception as e:
                    if attempt == 2:
                        logger.error("E-Agent: category page %s unreachable after 2 "
                                     "attempts (%s) — its stock is NOT in this harvest.",
                                     cat.url, e)
                    else:
                        logger.warning("E-Agent: %s timed out, retrying once.", cat.url)
                        scraper.throttle()
            if not loaded:
                continue
            # Route-change guard. E-Agent is a Wix client-side-routed SPA: a failed
            # navigation leaves the PREVIOUS page rendered, which would attribute the
            # same VIC files to NSW, SA and QLD builders in turn.
            want = urlparse(cat.url).path.rstrip("/").lower()
            got_path = urlparse(page.url or "").path.rstrip("/").lower()
            if want != got_path:
                logger.warning("E-Agent: %s did not load (still on %s) — skipping so its "
                               "files are not attributed to the wrong builders.",
                               cat.url, page.url)
                continue
            # A Wix 404 renders AT the requested path, so the route check above passes.
            # Report it, because a silently-dead route looks like "this state has no
            # stock" — which is how the NSW page went unnoticed.
            try:
                if page.query_selector("text=ERROR: PAGE NOT FOUND"):
                    logger.error("E-Agent: %s is a dead route (404) — the config needs "
                                 "updating from the site navigation.", cat.url)
                    continue
            except Exception:
                pass
            links = self._stocklist_links(scraper)
            fresh = [l for l in links if stocklist_file_key(l["href"]) not in seen_files]
            logger.info("E-Agent: %s (%s) -> %d stocklist file(s), %d new",
                        cat.url.rsplit("/", 1)[-1], cat.state or "-", len(links), len(fresh))
            for l in fresh:
                heading = (l.get("heading") or "").strip(" -–—:|")
                if self._NOT_STOCK_LABEL.search(l.get("label") or ""):
                    logger.debug("E-Agent: %r under %r is not a stocklist — skipped.",
                                 l.get("label"), heading)
                    continue
                # Apartments, townhouses and commercial are grouped by DEVELOPMENT, not
                # by builder — that is how E-Agent presents them, and the heading is the
                # project. Tagged `project` so they never mix with builder-attributed
                # rows; where no heading exists the development is read off the file name.
                #
                # A page can also say so about PART of itself. The NSW House & Land page
                # runs a "Builders" half and a "Projects" half, and the ten headings in the
                # Projects half are estates ("Kemps Estate - Austral"). They look exactly
                # like builder headings one level up, so before this the page's own divider
                # was ignored and 229 rows were filed under an estate name.
                section = self._section_kind(l.get("chain"))
                is_builder = self._is_builder_heading(heading) and section != "project"
                project_page = (cat.product_type not in ("House & Land", "")
                                or section == "project")
                if not is_builder and not project_page:
                    unattributed += 1
                    logger.debug("E-Agent: no builder heading for %s (saw %r)",
                                 l.get("label"), heading)
                    continue
                scope = "builder" if (is_builder and not project_page) else "project"
                hint = heading if is_builder else ""
                # Only house-and-land pages may promote a name read out of the FILE to
                # builder scope. On the apartment, townhouse, land and commercial pages the
                # thing being sold really is a development, and re-scoping those 1,479 rows
                # would change their identity for no gain.
                allow_banner = cat.product_type in ("House & Land", "")
                seen_files.add(stocklist_file_key(l["href"]))
                got = self._parse_stocklist(scraper, l, hint, scope, cat.state,
                                            cat.product_type, allow_banner)
                if got:
                    named = hint or (got[0].get("builder_name") or "").strip() or "(unnamed)"
                    builders[named] = builders.get(named, 0) + len(got)
                out.extend(got)
        if builders:
            logger.info("E-Agent: per-builder stock: %s",
                        ", ".join(f"{b} ({n})" for b, n in sorted(builders.items())))
        if unattributed:
            logger.warning("E-Agent: %d stocklist file(s) had no recognisable builder heading "
                           "and were skipped rather than guessed.", unattributed)
        logger.info("E-Agent: %d per-builder listing(s) across %d builder(s).",
                    len(out), len(builders))
        return out

    def _scrape_listings(self, scraper: PlaywrightScraper) -> List[Dict[str, Any]]:
        page = scraper.page
        scraper.goto(self.cfg.listings_url)
        # Stocklist FILES are E-Agent's real inventory channel — try them first.
        from_files = self._scrape_stocklist_files(scraper)
        if from_files:
            return from_files
        cards = page.query_selector_all(self.cfg.listing_card_selector)
        if not cards:
            # No hand-mapped selector matched — infer the listing structure from the
            # page itself instead of failing (no per-portal mapping required).
            adaptive = extract_listings(page, builder_hint="", state_hint="")
            if adaptive:
                logger.info("E-Agent: adaptive extractor found %d listing(s).", len(adaptive))
                for a in adaptive:
                    a["source_channel"] = self.channel_name
                    a["date_checked"] = datetime.now().strftime("%d/%m/%Y")
                    a["verified"] = True
                return adaptive
            logger.warning("E-Agent: no listings found on %s (page may need a different stock-list URL).",
                           self.cfg.listings_url)
            return []
        results = []
        fs = self.cfg.field_selectors
        for card in cards:
            title = scraper.text_or_none(card, fs.get("title", "h2"))
            price = parse_price(scraper.text_or_none(card, fs.get("price", ".price")))
            if not title or not price:
                continue
            link_el = card.query_selector(self.cfg.link_selector)
            href = link_el.get_attribute("href") if link_el else self.cfg.listings_url
            results.append({
                "lot_address": title,
                "suburb": scraper.text_or_none(card, fs.get("suburb", ".suburb")) or "",
                "builder_name": "",  # E-Agent aggregates many builders; resolved downstream by registry
                "advertised_package_price": price,
                "bedrooms": parse_int(scraper.text_or_none(card, fs.get("beds", ".beds"))),
                "bathrooms": parse_int(scraper.text_or_none(card, fs.get("baths", ".baths"))),
                "car_spaces": parse_int(scraper.text_or_none(card, fs.get("cars", ".cars"))),
                "source_channel": self.channel_name,
                "source_url_or_ref": href,
                "date_checked": datetime.now().strftime("%d/%m/%Y"),
                "verified": True,  # scraped live from the portal this run
            })
        logger.info("E-Agent: captured %d live listing(s).", len(results))
        return results

    def search(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not PLAYWRIGHT_AVAILABLE:
            logger.error("E-Agent search skipped: Playwright not installed.")
            return []
        # A saved session (from portal_login.py) is enough — credentials optional.
        has_session = (SESSION_DIR / "e_agent.json").exists()
        if not has_session and not (self.username and self.password):
            logger.warning("E-Agent skipped: no saved session and no credentials. "
                           "Run: python portal_login.py e_agent")
            return []
        try:
            scraper = PlaywrightScraper(session_name="e_agent")
            with scraper.session():
                # E-Agent shows no reliable "Log Out" marker, so authentication is
                # judged FUNCTIONALLY: if the stocklist files are reachable we are in.
                # Only log in when they are not.
                scraper.goto(self.cfg.listings_url)
                pooled = self._scrape_stocklist_files(scraper)
                logged_in = bool(pooled)
                if not pooled:
                    logger.info("E-Agent: no stocklists visible anonymously — logging in.")
                    if not self._login(scraper):
                        return []
                    logged_in = True
                    scraper.goto(self.cfg.listings_url)
                    pooled = self._scrape_stocklist_files(scraper)

                # The pooled files reaching us used to END the run, so the per-builder
                # category pages were never visited and every E-Agent row landed with a
                # blank builder. They are now always crawled.
                seen_files = set()
                per_builder = self._scrape_category_pages(scraper, seen_files)
                if not per_builder and not logged_in:
                    if self._login(scraper):
                        per_builder = self._scrape_category_pages(scraper, seen_files)

                listings = per_builder + pooled
                if not listings:
                    listings = self._scrape_listings(scraper)
                logger.info("E-Agent: %d listing(s) total (%d per-builder, %d pooled).",
                            len(listings), len(per_builder), len(pooled))
        except ScraperError as e:
            logger.error("E-Agent scraper error: %s", e)
            return []
        except Exception as e:
            logger.exception("E-Agent scraper crashed: %s", e)
            return []

        max_budget = float(filters.get("budget_max", 10_000_000))
        suburbs = [s.lower() for s in filters.get("primary_suburbs", [])]
        out = []
        for r in listings:
            if r["advertised_package_price"] > max_budget + 50_000:
                continue
            if suburbs and r.get("suburb") and r["suburb"].lower() not in suburbs:
                continue
            out.append(r)
        return out

    def verify(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """Re-open the listing URL and confirm it still exists / same price."""
        url = package.get("source_url_or_ref")
        if not (PLAYWRIGHT_AVAILABLE and self.username and url and url.startswith("http")):
            return {"verified": False, "status": "Pending Confirmation", "price_change": 0.0}
        try:
            scraper = PlaywrightScraper(session_name="e_agent")
            with scraper.session():
                if not self._login(scraper):
                    return {"verified": False, "status": "Pending Confirmation", "price_change": 0.0}
                scraper.goto(url)
                live_price = parse_price(scraper.text_or_none(scraper.page, self.cfg.field_selectors.get("price", ".price")))
            old = float(package.get("advertised_package_price", 0) or 0)
            change = (live_price - old) if (live_price and old) else 0.0
            return {
                "verified": live_price is not None,
                "status": "Verified" if live_price is not None else "Unavailable",
                "date_checked": datetime.now().strftime("%d/%m/%Y"),
                "price_change": change,
            }
        except Exception as e:
            logger.error("E-Agent verify failed: %s", e)
            return {"verified": False, "status": "Pending Confirmation", "price_change": 0.0}
