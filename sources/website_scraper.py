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

logger = logging.getLogger("spb.scraper.website")

# Pages worth visiting (link text / href keywords)
RELEVANT_HINTS = (
    "house-and-land", "house and land", "package", "display", "brochure", "booklet",
    "download", "floor-plan", "floorplan", "price", "home-design", "our-homes", "range",
)
# Asset link classification
ASSET_KEYWORDS = {
    "brochure": ("brochure", "booklet", "flyer", "flier", "range"),
    "floorplan": ("floor-plan", "floorplan", "floor plan"),
    "pricelist": ("price", "pricing", "pricelist"),
}
PDF_RE = re.compile(r"\.pdf($|\?)", re.I)


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
                visited: Set[str] = set()
                asset_links: Dict[str, str] = {}  # url -> link text

                while to_visit and len(visited) < self.max_pages:
                    url = to_visit.pop(0)
                    if url in visited or not self._same_origin(url, origin):
                        continue
                    if not rp.can_fetch(ua, url):
                        logger.info("[%s] robots.txt disallows %s", builder_name, url)
                        continue
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=config.SCRAPER_NAV_TIMEOUT_MS)
                    except Exception as e:
                        logger.warning("[%s] could not load %s: %s", builder_name, url, e)
                        continue
                    visited.add(url)
                    scraper.throttle()

                    for a in page.query_selector_all("a[href]"):
                        href = a.get_attribute("href") or ""
                        if not href or href.startswith(("mailto:", "tel:", "#", "javascript:")):
                            continue
                        full = urljoin(url, href)
                        text = (a.inner_text() or "").strip()[:120]
                        if PDF_RE.search(full):
                            if self._same_origin(full, origin):  # only harvest the builder's own assets
                                asset_links.setdefault(full, text)
                        elif (len(visited) + len(to_visit) < self.max_pages
                              and self._same_origin(full, origin)
                              and full not in visited and full not in to_visit
                              and any(h in (text + full).lower() for h in RELEVANT_HINTS)):
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
            body = resp.body()
            if not body or len(body) > self.max_bytes:
                return False
            sha = hashlib.sha256(body).hexdigest()
            fname = Path(urlparse(url).path).name or f"{sha[:12]}.pdf"
            fname = re.sub(r"[^A-Za-z0-9._-]", "_", fname)[:120]
            dest = out_dir / fname
            dest.write_bytes(body)
            recorded = self.db.record_asset({
                "builder_name": builder_name,
                "asset_type": _classify(text, url),
                "title": text or fname,
                "source_url": url,
                "local_path": str(dest),
                "file_size": len(body),
                "sha256": sha,
                "scraped_from": origin,
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
