"""
File the brochures that arrive in the digital@ inbox, and link each to its building.

Coleen, 30 July: *"Identify the builder from the email sender — Creation Homes, say —
then attach the brochure to the correct building in the app."* Only 656 of 4,192 rows
carry a document, and for builders whose stocklists contain no per-lot link the inbox is
the only place those documents exist.

Matching is by evidence, in this order, and stops at the first that fits:

  1. **A lot number in the file name**, against a lot of the same builder.
     "LOT-623-Yankee-Street-Shepparton-North-Dartmouth-117.pdf" -> lot 623.
  2. **A street address in the file name**, against that lot's address.
  3. **A house design name in the file name**, against every lot of that builder built
     to that design — one brochure legitimately covers all of them.

Where none of those fits, the brochure is still FILED against the builder — it just is
not linked to a listing. That is the honest outcome: a brochure on the wrong lot is worse
than a brochure the agent has to find by builder, and it is Coleen's own rule applied to
documents rather than names.

`brochure_url` is not part of building_content_hash, so linking moves no row identity and
needs no re-hash.

Usage:
    python attach_email_brochures.py              # report only
    python attach_email_brochures.py --apply      # file and link
    python attach_email_brochures.py --days 120   # look further back
"""

import hashlib
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import config
from builder_registry import BuilderRegistry
from database import ResearchDatabase

# "LOT 623", "Lot-623", "L623" — but not the "117" of a design name, so the token must
# be introduced by the word lot.
_LOT_IN_NAME = re.compile(r"\bl(?:ot)?[\s\-_]*(\d{1,5})\b", re.I)
_STREET_IN_NAME = re.compile(
    r"\b(\d+[A-Za-z]?)[\s\-_]+([A-Za-z][A-Za-z\-]+(?:[\s\-_]+[A-Za-z][A-Za-z\-]+)?)"
    r"[\s\-_]+(st|street|rd|road|ave|avenue|dr|drive|ct|court|pl|place|cres|crescent|"
    r"way|cct|circuit|pde|parade|blvd|boulevard|tce|terrace|cl|close|ln|lane|gr|grove|"
    r"rise|loop|esp|esplanade|walk|mews|green)\b", re.I)
_NOISE = re.compile(
    r"\.(pdf|xlsx|xls|csv)$|\b(brochure|flyer|floor\s*plan|floorplan|package|price\s*list|"
    r"pricelist|final|draft|copy|v\d+|rev\d*|web|digital|lo?res|hi?res)\b", re.I)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "builder").lower()).strip("_") or "builder"


def _design_tokens(filename: str) -> list:
    """Words in a file name that could be a house-design name.

    Deliberately loose — the match on the other side is against a design the builder
    actually sells, so a stray word simply finds nothing.
    """
    stem = _NOISE.sub(" ", filename)
    stem = re.sub(r"[_\-]+", " ", stem)
    return [w for w in re.findall(r"[A-Za-z][A-Za-z']{3,}", stem)
            if w.lower() not in ("lot", "the", "and", "with", "from", "home", "homes",
                                 "stage", "street", "road", "estate")]


def _global_candidates(conn, fname: str):
    """Rows matching a lot number AND a second token from the file name, across all
    builders. None when the file name does not supply both signals."""
    m = _LOT_IN_NAME.search(fname)
    if not m:
        return None
    tokens = _design_tokens(fname)
    if not tokens:
        return None
    lot = m.group(1).lstrip("0") or "0"
    rows = conn.execute(
        "SELECT id, lot_number, lot_address, suburb, source_text, estate_name FROM buildings "
        "WHERE TRIM(COALESCE(lot_number,'')) <> ''").fetchall()
    hits = []
    for r in rows:
        if str(r["lot_number"] or "").strip().lstrip("0") != lot:
            continue
        haystack = " ".join(str(r[k] or "") for k in
                           ("lot_address", "suburb", "estate_name", "source_text"))
        if any(re.search(rf"\b{re.escape(t)}\b", haystack, re.I) for t in tokens):
            hits.append(r)
    return hits


def _match(conn, brochure) -> tuple:
    """(list of building ids, how it matched). Empty list when nothing fits."""
    builder = (brochure["builder_name"] or "").strip()
    fname = brochure["filename"]

    if builder:
        rows = conn.execute(
            "SELECT id, lot_number, lot_address, suburb, source_text FROM buildings "
            "WHERE builder_name = ? COLLATE NOCASE", (builder,)).fetchall()
    else:
        # Most of this inbox is forwarded mail, so the sender is SPB and the builder is
        # unknown. A lot number alone is then far too weak — plenty of builders have a lot
        # 714. But a file name carrying a lot number AND an estate or design that appear
        # together on exactly one stored row is two signals agreeing, which is the same
        # standard used for resolving a listing's state.
        rows = _global_candidates(conn, fname)
        if rows is None:
            return [], ""
        matched = [r["id"] for r in rows]
        if len(matched) == 1:
            return matched, "lot + estate/design in the file name (builder not named)"
        return [], ""
    if not rows:
        return [], ""

    m = _LOT_IN_NAME.search(fname)
    if m:
        lot = m.group(1).lstrip("0") or "0"
        hit = [r["id"] for r in rows
               if str(r["lot_number"] or "").strip().lstrip("0") == lot]
        if len(hit) == 1:
            return hit, f"lot {m.group(1)} in the file name"
        if len(hit) > 1:
            # Same lot number twice for one builder means different estates; the file name
            # does not say which, so this is not a match.
            return [], ""

    sm = _STREET_IN_NAME.search(fname)
    if sm:
        street = re.sub(r"[\s\-_]+", " ", f"{sm.group(1)} {sm.group(2)}").lower()
        hit = [r["id"] for r in rows
               if street in re.sub(r"[\s\-_]+", " ", (r["lot_address"] or "").lower())]
        if len(hit) == 1:
            return hit, f"address '{street}' in the file name"

    for token in _design_tokens(fname):
        hit = [r["id"] for r in rows
               if re.search(rf"\b{re.escape(token)}\b", r["source_text"] or "", re.I)]
        # A design brochure covers every lot built to that design, but a token matching
        # nearly the whole builder is a word like "Available", not a design.
        if hit and len(hit) <= max(3, len(rows) // 4):
            return hit, f"design '{token}' in the file name"
    return [], ""


def main(apply: bool = False, days: int = 90) -> int:
    from sources.email_inbox import EmailStocklistSource

    ResearchDatabase()                      # apply any pending column migration
    reg = BuilderRegistry()
    src = EmailStocklistSource(registry=reg, days_back=days)
    if not getattr(src, "username", ""):
        print("[abort] no inbox credentials available (vault key email_inbox, or the "
              "shared login in the vendor sheet).")
        return 1

    print(f"[i] sweeping the inbox, last {days} days — READ ONLY, nothing is marked or moved")
    listings = src.search({"budget_max": 10 ** 9, "primary_suburbs": []})
    print(f"[+] {len(listings)} listing(s) and {len(src.brochures)} brochure(s) found")
    if not src.brochures:
        print("    no brochures to file.")
        return 0

    if apply:
        dest = f"{config.DATABASE_PATH}.bak-brochures-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy2(str(config.DATABASE_PATH), dest)
        print(f"[+] backup written: {dest}")
    else:
        print("[i] report only — nothing written. Use --apply.")

    conn = sqlite3.connect(str(config.DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    root = Path(config.PROJECT_ROOT) / "assets" / "email"
    filed = linked = unlinked = duplicate = 0
    linked_rows = 0
    report = []

    for b in src.brochures:
        digest = hashlib.sha256(b["data"]).hexdigest()
        already = conn.execute("SELECT id FROM builder_assets WHERE sha256 = ?",
                              (digest,)).fetchone()
        rel = f"assets/email/{_slug(b['builder_name'])}/{re.sub(r'[^A-Za-z0-9._-]+', '_', b['filename'])}"
        target = Path(config.PROJECT_ROOT) / rel
        if apply and not already:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b["data"])
            conn.execute(
                "INSERT INTO builder_assets (builder_name, asset_type, title, source_url, "
                "local_path, file_size, sha256, scraped_from, extracted_text, downloaded_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (b["builder_name"], "brochure", b["filename"][:120],
                 f"email:{b['email_subject']}", str(target), len(b["data"]), digest,
                 "digital email", "", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        if already:
            duplicate += 1
        else:
            filed += 1

        ids, how = _match(conn, b)
        if ids:
            linked += 1
            linked_rows += len(ids)
            if apply:
                conn.executemany(
                    "UPDATE buildings SET brochure_url = ? WHERE id = ? "
                    "AND (brochure_url IS NULL OR TRIM(brochure_url) = '')",
                    [(rel, i) for i in ids])
        else:
            unlinked += 1
        report.append((b["builder_name"], b["filename"], len(ids), how))

    if apply:
        conn.commit()

    print("=" * 74)
    print(f"  filed {filed} new brochure(s), {duplicate} already held")
    print(f"  linked {linked} to a building ({linked_rows} row(s) given a brochure)")
    print(f"  {unlinked} filed against the builder only — the file name names no lot,")
    print(f"    address or design, and guessing one would be worse than a blank")
    print("=" * 74)
    for builder, fname, n, how in report[:40]:
        mark = f"-> {n} row(s) via {how}" if n else "-> builder only"
        print(f"  {str(builder)[:20]:<20} {fname[:46]:<46} {mark}")
    if not apply:
        print("\n  (nothing written — re-run with --apply)")
    conn.close()
    return 0


if __name__ == "__main__":
    d = 90
    if "--days" in sys.argv:
        d = int(sys.argv[sys.argv.index("--days") + 1])
    sys.exit(main(apply="--apply" in sys.argv, days=d))
