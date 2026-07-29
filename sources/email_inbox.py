"""
Email stocklist source — reads builder stock out of the shared inbox.

Nine approved builders never publish stock on a portal; they email it to
digital@smartpropertybuying.com.au. This connects over IMAP, finds those emails,
and parses their attachments (XLSX/PDF/CSV) and bodies with the same adaptive
stocklist extractor used for E-Agent, attributing each listing to the builder
that sent it.

Design rules:
  * READ-ONLY. The mailbox is opened with readonly=True; nothing is ever marked
    read, moved, flagged or deleted.
  * PRIVACY-SCOPED. Only messages that look like builder stock are examined —
    sender domain matching an approved builder, or a stocklist-ish subject with a
    data attachment. Email bodies are never logged; only extracted property rows
    leave this module.
  * Credentials come from the OS vault / env at run time (key: email_inbox),
    never from source.
  * Nothing is invented: an inbox with no matching mail yields no listings.

Config (env or vault):
    EMAIL_INBOX_USERNAME / EMAIL_INBOX_PASSWORD
    IMAP_HOST (default imap.hostinger.com)  IMAP_PORT (default 993, SSL)
"""

import email
import imaplib
import logging
import os
import re
from datetime import datetime, timedelta
from email.header import decode_header
from typing import Any, Dict, List, Optional, Tuple

from sources.base import PropertySource
from sources.spreadsheet_extract import extract_stocklist
from sources.adaptive_extract import parse_fields, _clean
from secrets_store import get_credentials

logger = logging.getLogger("spb.source.email")

IMAP_HOST = os.getenv("IMAP_HOST", "imap.hostinger.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))

DATA_EXT = re.compile(r"\.(xlsx|xls|pdf|csv)$", re.I)
STOCK_SUBJECT = re.compile(
    r"stock|stocklist|stock list|availability|available|price|pricing|"
    r"packages?|house\s*&?\s*land|h\s*[/&]\s*l\b|inventory|lots?\b|weekly|release|"
    r"contracts?\b|dual\s*key|flyer|masterplan|opportunit", re.I)


def _decode(raw: Optional[str]) -> str:
    """Decode an RFC2047 header safely."""
    if not raw:
        return ""
    out = []
    try:
        for part, enc in decode_header(raw):
            if isinstance(part, bytes):
                out.append(part.decode(enc or "utf-8", errors="replace"))
            else:
                out.append(part)
    except Exception:
        return str(raw)
    return _clean(" ".join(out))


def _sender_domain(from_hdr: str) -> str:
    m = re.search(r"@([A-Za-z0-9.\-]+)", from_hdr or "")
    return m.group(1).lower().strip(">").strip() if m else ""


class EmailStocklistSource(PropertySource):
    def __init__(self, registry=None, days_back: int = 60):
        self.days_back = days_back
        # Fallback: the shared inbox login is stored against the email-only builders
        # in the vendor sheet (portal link "Login to Smart Property Buying outlook").
        csv_user = csv_pass = ""
        if registry is not None:
            for b in registry.get_all_builders():
                login = (b.get("portal_login_email") or "").lower()
                # must be the shared DIGITAL mailbox, not another SPB account
                # (e.g. the E-Agent login coleenn@… lives on the same domain)
                if login.startswith("digital@") and b.get("portal_login_password"):
                    csv_user, csv_pass = b["portal_login_email"], b["portal_login_password"]
                    break
        self.username, self.password, self.cred_source = get_credentials("email_inbox", (csv_user, csv_pass))
        # domain -> builder name, from the approved builder directory
        self.domain_map: Dict[str, str] = {}
        if registry is not None:
            for b in registry.get_all_builders():
                for field in ("contact_email", "email", "portal_login_email"):
                    dom = _sender_domain(b.get(field) or "")
                    if dom and dom not in ("smartpropertybuying.com.au", "gmail.com", "e-agent.com.au"):
                        self.domain_map.setdefault(dom, b["builder_name"])
                # builders whose stock arrives by email often name their site in notes
                for dom in re.findall(r"([a-z0-9\-]+\.com\.au)", (b.get("notes") or "").lower()):
                    self.domain_map.setdefault(dom, b["builder_name"])

    @property
    def channel_name(self) -> str:
        return "Builder email stocklist"

    # ---------- connection ----------
    def _connect(self) -> Optional[imaplib.IMAP4_SSL]:
        if not (self.username and self.password):
            logger.warning("Email source skipped: no credentials. "
                           "Run: python setup_credentials.py email_inbox")
            return None
        try:
            box = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            box.login(self.username, self.password)
            logger.info("Email: connected to %s as %s (credentials from %s)",
                        IMAP_HOST, self.username, self.cred_source)
            return box
        except Exception as e:
            logger.error("Email: IMAP connection/login failed on %s:%s — %s", IMAP_HOST, IMAP_PORT, e)
            return None

    def _relevant(self, from_hdr: str, subject: str, has_data_attachment: bool,
                  attachment_names: str = "") -> Tuple[bool, str]:
        """Is this message builder stock? Returns (relevant, builder_name).

        Attachment FILENAMES are considered as well as the subject: builders often
        send 'McMullan Bird - Printable Stocklist.pdf' under a subject like
        'Re: Ballarat H/L $506k' that carries no stock wording at all.
        """
        dom = _sender_domain(from_hdr)
        builder = self.domain_map.get(dom, "")
        if builder:
            return True, builder
        if has_data_attachment and STOCK_SUBJECT.search(f"{subject or ''} {attachment_names}"):
            # try to name the builder from the sender's domain or the filenames
            stem = dom.split(".")[0].replace("-", " ")
            guess = ""
            for d, name in self.domain_map.items():
                if stem and stem in d:
                    guess = name
                    break
            return True, guess
        return False, ""

    # ---------- extraction ----------
    def _from_message(self, msg, builder: str, subject: str, when: str) -> List[Dict[str, Any]]:
        listings: List[Dict[str, Any]] = []

        # 1) attachments — the usual channel
        for part in msg.walk():
            fname = _decode(part.get_filename())
            if not fname or not DATA_EXT.search(fname):
                continue
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                continue
            if not payload:
                continue
            got = extract_stocklist(payload, source_label=f"email:{fname}", builder_hint=builder)
            for g in got:
                g["email_subject"] = subject[:120]
                g["email_date"] = when
                g["attachment"] = fname[:80]
            listings.extend(got)

        # 2) body text — some builders paste stock inline
        if not listings:
            body = ""
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        body = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
                        break
                    except Exception:
                        continue
            for line in body.splitlines():
                line = _clean(line)
                if not line or len(line) > 400:
                    continue
                f = parse_fields(line)
                if not f.get("advertised_package_price"):
                    continue
                if not f.get("lot_address"):
                    f["lot_address"] = line[:110]
                listings.append({**f, "builder_name": builder,
                                 "email_subject": subject[:120], "email_date": when,
                                 "extraction_confidence": 0.6})

        for l in listings:
            l["source_channel"] = self.channel_name
            l["source_url_or_ref"] = f"email:{subject[:70]}"
            l["date_checked"] = datetime.now().strftime("%d/%m/%Y")
            # A document is a point-in-time snapshot — confirm before presenting.
            l["verified"] = False
        return listings

    # ---------- PropertySource ----------
    def search(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        box = self._connect()
        if not box:
            return []
        out: List[Dict[str, Any]] = []
        scanned = matched = 0
        try:
            box.select("INBOX", readonly=True)          # READ-ONLY: never mutate the mailbox
            since = (datetime.now() - timedelta(days=self.days_back)).strftime("%d-%b-%Y")
            typ, data = box.search(None, f'(SINCE "{since}")')
            ids = data[0].split() if data and data[0] else []
            logger.info("Email: %d message(s) since %s", len(ids), since)
            for mid in reversed(ids):                   # newest first
                scanned += 1
                try:
                    typ, raw = box.fetch(mid, "(RFC822)")
                    if not raw or not raw[0]:
                        continue
                    msg = email.message_from_bytes(raw[0][1])
                except Exception:
                    continue
                from_hdr = _decode(msg.get("From"))
                subject = _decode(msg.get("Subject"))
                when = _decode(msg.get("Date"))[:31]
                att_names = " ".join(_decode(p.get_filename()) or "" for p in msg.walk())
                has_data = bool(DATA_EXT.search(att_names))
                ok, builder = self._relevant(from_hdr, subject, has_data, att_names)
                if not ok:
                    continue
                matched += 1
                got = self._from_message(msg, builder, subject, when)
                if got:
                    logger.info("Email: %d listing(s) from %s (%s)",
                                len(got), builder or _sender_domain(from_hdr), subject[:44])
                out.extend(got)
        except Exception as e:
            logger.error("Email: inbox scan failed — %s", e)
        finally:
            try:
                box.close(); box.logout()
            except Exception:
                pass

        logger.info("Email: scanned %d message(s), %d relevant, %d listing(s).", scanned, matched, len(out))
        max_budget = float(filters.get("budget_max", 10_000_000))
        return [r for r in out if (r.get("advertised_package_price") or 0) <= max_budget + 50_000]

    def verify(self, package: Dict[str, Any]) -> Dict[str, Any]:
        # An emailed document is a snapshot; a consultant must confirm with the builder.
        return {"verified": False, "status": "Pending Confirmation",
                "date_checked": package.get("date_checked", ""), "price_change": 0.0}
