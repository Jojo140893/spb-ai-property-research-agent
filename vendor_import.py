"""
Vendor CSV importer.

Coleen's "Book1(Builders) List" is a single CSV holding several stacked
sections with different headers and layouts:
  - Primary vendor table (NAME,EMAIL,PHONE,BUILDER,STATES,...,WEB PORTAL LINK,EMAIL,PASSWORD,NOTES)
  - LinkedIn prospect lists (NAME,...,Title,LinkedIn Link,STATUS)  -> people, not vendors
  - Regional builder lists (Kingaroy / WA / SA) with a website column
  - "BUILDERS IN PERTH" (NAME,Email,Phone,Website,City)
  - "Builders from Agents" free-text lines

This importer walks the file section by section, extracts BUILDER + website +
state + contact wherever they exist, skips the people-only prospect rows and
junk lines, dedupes by builder name, and upserts into the DB `builders` table.

The website is what the asset scraper needs; rows without one are flagged
has_website=0 so they can be chased or discovered separately.
"""

import csv
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

from database import ResearchDatabase

URL_RE = re.compile(r"https?://[^\s,]+", re.I)
BARE_DOMAIN_RE = re.compile(r"\b([a-z0-9-]+\.(?:com\.au|net\.au|au|com))\b", re.I)


def _clean(s: Optional[str]) -> str:
    return (s or "").strip().strip('"').strip()


def _first_url(*fields: str) -> str:
    """Extract the first real website URL from any of the given fields."""
    for f in fields:
        f = _clean(f)
        if not f:
            continue
        m = URL_RE.search(f)
        if m:
            url = m.group(0).rstrip("/\\")
            # skip Google Drive links and login/portal wp-admin URLs (handled elsewhere)
            if "drive.google" in url:
                continue
            return url
        m2 = BARE_DOMAIN_RE.search(f)
        if m2 and "e agent" not in f.lower() and "@" not in f:
            return "https://" + m2.group(1).rstrip("/\\")
    return ""


def _is_portal(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(k in host for k in ("portal.", "partners.", "referrer.")) or "wp-admin" in url.lower()


class VendorImporter:
    # header signatures that mark the start of a section
    PRIMARY_HDR = ("BUILDER", "WEB PORTAL LINK")
    LINKEDIN_HDR = ("LinkedIn Link",)
    PERTH_HDR = ("BUILDERS IN PERTH",)

    def __init__(self, db: Optional[ResearchDatabase] = None):
        self.db = db or ResearchDatabase()

    def parse(self, csv_path: Path) -> List[Dict[str, Any]]:
        rows = list(csv.reader(open(csv_path, encoding="utf-8", errors="ignore")))
        builders: Dict[str, Dict[str, Any]] = {}
        section = "primary"

        for raw in rows:
            if not raw or not any(_clean(c) for c in raw):
                continue
            line = ",".join(raw)

            # Section switches
            if "LinkedIn Link" in line:
                section = "linkedin"
                continue
            if "BUILDERS IN PERTH" in line:
                section = "perth"
                continue
            if raw[0].strip() == "NAME" and "BUILDER" in line:
                section = "primary"
                continue
            if raw[0].strip() == "Builders from Agents":
                section = "agents"
                continue

            rec = self._parse_row(raw, section, line)
            if not rec:
                continue
            key = rec["builder_name"].strip().lower()
            if not key:
                continue
            if key in builders:
                # merge — prefer non-empty fields, especially a website
                for f in ("email", "phone", "states", "website", "portal_url", "contact_name", "notes"):
                    if not builders[key].get(f) and rec.get(f):
                        builders[key][f] = rec[f]
                builders[key]["is_on_e_agent"] = builders[key].get("is_on_e_agent") or rec.get("is_on_e_agent")
            else:
                builders[key] = rec
        return list(builders.values())

    def _parse_row(self, raw: List[str], section: str, line: str) -> Optional[Dict[str, Any]]:
        cells = [_clean(c) for c in raw]

        if section == "linkedin":
            return None  # people/prospects, not vendors

        if section == "perth":
            # NAME, Email, Phone, Website, City
            name = cells[0] if cells else ""
            if not name or name.lower() in ("nulook homes",) and False:
                pass
            website = _first_url(cells[3] if len(cells) > 3 else "")
            return self._make(name, email=cells[1] if len(cells) > 1 else "",
                              phone=cells[2] if len(cells) > 2 else "",
                              states=cells[4] if len(cells) > 4 else "",
                              website=website, section="perth")

        if section == "agents":
            # free-text like "Tom Gaskin - 0423 272 420" or "Micson constructions,ashley@..."
            name = re.split(r"[-,]", cells[0])[0].strip() if cells and cells[0] else ""
            if not name or len(name) < 3:
                return None
            return self._make(name, section="agents")

        # primary / regional sections share the primary column layout
        # 0 NAME,1 EMAIL,2 PHONE,3 BUILDER,4 STATES,5 Contract,6 E-Agent,7 WEB PORTAL LINK,8 EMAIL,9 PASSWORD,10 NOTES
        builder = cells[3] if len(cells) > 3 else ""
        contact = cells[0] if len(cells) > 0 else ""
        # Regional rows (Kingaroy/WA/SA/Emerald) put the builder in col 0 and a website in col 5
        if not builder and contact:
            builder = contact
            contact = ""
        if not builder:
            return None

        portal_or_link = cells[7] if len(cells) > 7 else ""
        col5 = cells[5] if len(cells) > 5 else ""  # regional sections use col5 for website
        website = _first_url(portal_or_link, col5)
        portal_url = website if website and _is_portal(website) else ""
        if portal_url:
            website = ""  # a login portal is not a public marketing site
        e_agent = "yes" in (cells[6].lower() if len(cells) > 6 else "")

        return self._make(
            builder, contact_name=contact,
            email=cells[1] if len(cells) > 1 else "",
            phone=cells[2] if len(cells) > 2 else "",
            states=cells[4] if len(cells) > 4 else "",
            website=website, portal_url=portal_url, is_on_e_agent=e_agent,
            notes=cells[10] if len(cells) > 10 else "", section=section,
        )

    def _make(self, builder_name, contact_name="", email="", phone="", states="",
              website="", portal_url="", is_on_e_agent=False, notes="", section="primary") -> Optional[Dict[str, Any]]:
        builder_name = builder_name.strip()
        # drop obvious non-vendor / junk names
        if not builder_name or builder_name.lower() in ("do not contact", "n/a", ""):
            return None
        if builder_name.lower().startswith(("builders in perth", "builders from agents")):
            return None
        return {
            "builder_name": builder_name,
            "contact_name": contact_name,
            "email": email if "@" in email else "",
            "phone": phone,
            "states": states.upper(),
            "website": website,
            "portal_url": portal_url,
            "is_on_e_agent": is_on_e_agent,
            "notes": notes,
            "source_section": section,
        }

    def import_to_db(self, csv_path: Path) -> Dict[str, Any]:
        builders = self.parse(csv_path)
        for b in builders:
            self.db.upsert_builder(b)
        with_site = [b for b in builders if b.get("website")]
        return {
            "total_builders": len(builders),
            "with_website": len(with_site),
            "with_portal": len([b for b in builders if b.get("portal_url")]),
            "website_builders": [{"builder_name": b["builder_name"], "website": b["website"]} for b in with_site],
        }


if __name__ == "__main__":
    import sys
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else (config_default := None)
    if path is None:
        import config
        path = config.DRIVE_INPUT_DIR / "vendors.csv"
    imp = VendorImporter()
    summary = imp.import_to_db(path)
    print(f"Imported {summary['total_builders']} builders "
          f"({summary['with_website']} with a public website, {summary['with_portal']} with a login portal).")
    for b in summary["website_builders"]:
        print(f"  - {b['builder_name']}: {b['website']}")
