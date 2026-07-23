"""
Builder Website Asset Scraper (Playwright).

Given a builder's public website, this crawls a small set of relevant pages
(home + house-and-land / packages / display homes / brochures / downloads),
finds downloadable marketing assets — brochures, fliers, booklets, floor plans,
price lists (PDFs and offered downloads) — downloads them, and records each in
the DB (deduped by file hash) under that builder, sorted into assets/<builder>/.

Politeness & safety:
- Honours robots.txt (skips disallowed paths).
- Same-origin only; capped pages, capped assets, capped file size.
- Rate-limited between requests.
- Never fabricates: a site that yields nothing simply records zero assets.

Copyright note: these are the builders' own marketing materials, harvested for an
authorised reseller (SPB) to present to buyers. Assets are stored locally with
their source URL for attribution; nothing is republished automatically.
"""

import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import config
from sources.scraper_base import PlaywrightScraper, PLAYWRIGHT_AVAILABLE
from database import ResearchDatabase

try:
    import pdfplumber
    PDF_TEXT_AVAILABLE = True
except ImportError:
    PDF_TEXT_AVAILABLE = False

logger = logging.getLogger("spb.scraper.website")

# Pages worth visiting (link text / href keywords)
RELEVANT_HINTS = (
    "house-and-land", "house and land", "house-land", "and-land", "package", "display",
    "brochure", "booklet", "download", "floor-plan", "floorplan", "floor plan", "price",
    "home-design", "home design", "designs", "our-homes", "our homes", "our-range", "range",
    "new-homes", "new homes", "home-and-land", "inclusions", "specification", "spec-sheet",
)
# Link text that signals a downloadable asset even when the href is not a .pdf
# (e.g. a "Download Brochure" button that serves a PDF from a non-.pdf URL).
ASSET_TEXT_HINTS = (
    "brochure", "booklet", "flyer", "flier", "download", "floor plan", "floorplan",
    "price list", "pricelist", "inclusions", "spec", "fact sheet", "factsheet",
)
# Common builder page paths to try when a site's nav is JS-rendered / links are missed.
CANDIDATE_PATHS = (
    "/house-and-land", "/house-land-packages", "/house-and-land-packages", "/packages",
    "/home-designs", "/designs", "/our-homes", "/our-range", "/range", "/new-homes",
    "/display-homes", "/display-centres", "/brochures", "/downloads", "/floor-plans",
)
# Asset link classification
ASSET_KEYWORDS = {
    "floorplan": ("floor-plan", "floorplan", "floor plan"),
    "pricelist": ("price", "pricing", "pricelist"),
    "brochure": ("brochure", "booklet", "flyer", "flier", "range", "inclusions", "spec"),
}
PDF_RE = re.compile(r"\.pdf($|\?)", re.I)
CHALLENGE_MARKERS = ("just a moment", "checking your browser", "attention required", "enable javascript")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "builder"


def _classify(text: str, url: str) -> str:
    blob = f"{text} {url}".lower()
    for atype, kws in ASSET_KEYWORDS.items():
        if any(k in blob for k in kws):
            return atype
    return "brochure"


class WebsiteAssetScraper:
    def __init__(self, db: Optional[ResearchDatabase] = None):
        self.db = db or ResearchDatabase()
        self.max_pages = config.SCRAPER_MAX_PAGES
        self.max_assets = config.SCRAPER_MAX_ASSETS
        self.max_bytes = int(config.SCRAPER_MAX_FILE_MB * 1024 * 1024)

    def _robots(self, base: str) -> RobotFileParser:
        rp = RobotFileParser()
        try:
            rp.set_url(urljoin(base, "/robots.txt"))
            rp.read()
        except Exception:
            pass  # no robots -> allow
        return rp

    def _same_origin(self, url: str, origin: str) -> bool:
        return urlparse(url).netloc.replace("www.", "") == urlparse(origin).netloc.replace("www.", "")

    @staticmethod
    def _registrable(host: str) -> str:
        """Registrable domain, e.g. cdn.foo.com.au -> foo.com.au ; www.foo.com -> foo.com.
        IPs / localhost are returned unchanged so fixture tests stay exact-match."""
        host = host.lower().split(":")[0]
        if host.replace(".", "").isdigit() or host in ("localhost", "127.0.0.1"):
            return host
        labels = host.split(".")
        if len(labels) >= 3 and labels[-2] in ("com", "net", "org", "gov", "edu") and len(labels[-1]) == 2:
            return ".".join(labels[-3:])  # e.g. foo.com.au
        return ".".join(labels[-2:])

    def _same_site(self, url: str, origin: str) -> bool:
        """True if url is on the same registrable domain as origin (allows CDN subdomains)."""
        return self._registrable(urlparse(url).netloc) == self._registrable(urlparse(origin).netloc)

    def _extract_pdf_text(self, path: Path) -> str:
        if not PDF_TEXT_AVAILABLE:
            return ""
        try:
            with pdfplumber.open(path) as pdf:
                chunks = []
                for pg in pdf.pages[:8]:  # first few pages hold the key building details
                    chunks.append(pg.extract_text() or "")
                    if sum(len(c) for c in chunks) > 6000:
                        break
                return re.sub(r"\s+\n", "\n", "\n".join(chunks)).strip()[:6000]
        except Exception as e:
            logger.warning("PDF text extraction failed for %s: %s", path.name, e)
            return ""

    def _safe_goto(self, page, url: str) -> bool:
        """Navigate resiliently: tolerate timeouts (salvage partial loads), let JS-rendered
        navigation settle, and wait out a bot-challenge interstitial once. Returns True if a
        usable DOM is present."""
        timeout = config.SCRAPER_NAV_TIMEOUT_MS
        loaded = False
        for attempt, wait_until in enumerate(("domcontentloaded", "commit")):
            try:
                page.goto(url, wait_until=wait_until, timeout=timeout)
                loaded = True
                break
            except Exception as e:
                # a timeout can still leave a partially-rendered, usable page
                try:
                    if page.query_selector("body"):
                        loaded = True
                        logger.info("[goto] %s timed out (%s) but a partial DOM is usable", url, wait_until)
                        break
                except Exception:
                    pass
                if attempt == 0:
                    logger.info("[goto] retrying %s with a looser wait after: %s", url, e)
        if not loaded:
            return False
        # let JS-injected nav/links render, then check for a bot challenge
        for _ in range(2):
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            try:
                page.wait_for_timeout(1200)
                title = (page.title() or "").lower()
                body = (page.inner_text("body") or "").lower()[:400]
            except Exception:
                return True
            if any(m in title or m in body for m in CHALLENGE_MARKERS):
                logger.info("[goto] %s shows a bot challenge; waiting it out", url)
                try:
                    page.wait_for_timeout(4000)
                except Exception:
                    pass
                continue
            break
        return True

    def scrape_builder(self, builder_name: str, website: str) -> Dict[str, Any]:
        result = {"builder": builder_name, "website": website, "pages_visited": 0,
                  "assets_found": 0, "assets_new": 0, "error": None}
        if not PLAYWRIGHT_AVAILABLE:
            result["error"] = "Playwright not installed"
            return result
        if not website:
            result["error"] = "no website"
            return result

        origin = website if "://" in website else "https://" + website
        rp = self._robots(origin)
        ua = "SPB-ResearchBot"
        out_dir = config.ASSETS_DIR / _slug(builder_name)

        try:
            scraper = PlaywrightScraper(session_name="web_" + _slug(builder_name))
            with scraper.session():
                page = scraper.page
                to_visit = [origin]
                # For real sites (not localhost/IP fixtures), also try common builder paths
                # in case the nav is JS-rendered and its links are missed.
                if self._registrable(urlparse(origin).netloc) not in ("localhost", "127.0.0.1") \
                        and not urlparse(origin).netloc.replace(".", "").isdigit():
                    to_visit += [urljoin(origin, p) for p in CANDIDATE_PATHS]
                visited: Set[str] = set()
                asset_links: Dict[str, str] = {}  # url -> link text

                while to_visit and len(visited) < self.max_pages:
                    url = to_visit.pop(0)
                    if url in visited or not self._same_origin(url, origin):
                        continue
                    if not rp.can_fetch(ua, url):
                        logger.info("[%s] robots.txt disallows %s", builder_name, url)
                        continue
                    if not self._safe_goto(page, url):
                        logger.warning("[%s] could not load %s", builder_name, url)
                        continue
                    visited.add(url)
                    scraper.throttle()

                    for a in page.query_selector_all("a[href]"):
                        href = a.get_attribute("href") or ""
                        if not href or href.startswith(("mailto:", "tel:", "#", "javascript:")):
                            continue
                        full = urljoin(url, href)
                        text = (a.inner_text() or "").strip()[:120]
                        blob = (text + " " + full).lower()
                        if PDF_RE.search(full):
                            if self._same_site(full, origin):  # builder's own domain incl. CDN subdomains
                                asset_links.setdefault(full, text)
                        elif (self._same_site(full, origin)
                              and any(h in blob for h in ASSET_TEXT_HINTS)):
                            # a "Download Brochure"-style link whose URL isn't .pdf. It may be a
                            # direct PDF (verified by content-type at download; non-PDFs rejected)
                            # OR a downloads page — so also crawl it for the PDFs it links to.
                            asset_links.setdefault(full, text)
                            if (self._same_origin(full, origin) and full not in visited
                                    and full not in to_visit and len(to_visit) < self.max_pages * 3):
                                to_visit.append(full)
                        elif (len(to_visit) < self.max_pages * 3
                              and self._same_origin(full, origin)
                              and full not in visited and full not in to_visit
                              and any(h in blob for h in RELEVANT_HINTS)):
                            to_visit.append(full)

                result["pages_visited"] = len(visited)
                result["assets_found"] = len(asset_links)

                out_dir.mkdir(parents=True, exist_ok=True)
                for i, (asset_url, text) in enumerate(asset_links.items()):
                    if i >= self.max_assets:
                        logger.info("[%s] asset cap (%d) reached; stopping.", builder_name, self.max_assets)
                        break
                    if not rp.can_fetch(ua, asset_url):
                        continue
                    saved = self._download(scraper, builder_name, asset_url, text, out_dir, origin)
                    if saved:
                        result["assets_new"] += 1
                    scraper.throttle()
        except Exception as e:
            logger.exception("[%s] website scrape crashed: %s", builder_name, e)
            result["error"] = str(e)
        return result

    def _download(self, scraper, builder_name, url, text, out_dir: Path, origin: str) -> bool:
        try:
            resp = scraper.page.context.request.get(url, timeout=config.SCRAPER_NAV_TIMEOUT_MS)
            if not resp.ok:
                return False
            ctype = (resp.headers.get("content-type", "") or "").lower()
            # Only keep actual PDF documents (rejects HTML "download" pages and images).
            is_pdf = bool(PDF_RE.search(url)) or "application/pdf" in ctype
            if not is_pdf:
                return False
            body = resp.body()
            if not body or len(body) > self.max_bytes or not body[:5].startswith(b"%PDF"):
                return False
            sha = hashlib.sha256(body).hexdigest()
            fname = Path(urlparse(url).path).name or f"{sha[:12]}.pdf"
            fname = re.sub(r"[^A-Za-z0-9._-]", "_", fname)[:120]
            if not fname.lower().endswith(".pdf"):
                fname += ".pdf"
            dest = out_dir / fname
            dest.write_bytes(body)
            extracted = self._extract_pdf_text(dest)
            recorded = self.db.record_asset({
                "builder_name": builder_name,
                "asset_type": _classify(text, url),
                "title": text or fname,
                "source_url": url,
                "local_path": str(dest),
                "file_size": len(body),
                "sha256": sha,
                "scraped_from": origin,
                "extracted_text": extracted,
            })
            if not recorded:
                # duplicate content already stored elsewhere; drop the redundant file copy
                try:
                    dest.unlink()
                except OSError:
                    pass
                return False
            logger.info("[%s] saved %s (%d KB)", builder_name, fname, len(body) // 1024)
            return True
        except Exception as e:
            logger.warning("[%s] failed to download %s: %s", builder_name, url, e)
            return False
