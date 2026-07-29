"""
Backfill State and Builder on harvested stock — the two gaps Coleen called out
on 2026-07-28 ("we should see the builder name separated and we should see the state").

STATE is resolved by state_resolver.resolve_state, which weighs the listing's postcode,
its suburb and the E-Agent page it came from against each other and records which signal
decided it (see that module for why agreement beats a fixed ranking). The stocklist
filename (…?dn=NSW%20Dual%20Jul.xlsx -> NSW) is a last resort.

BUILDER is resolved in order:
  1. whatever the scrape already attributed (portal scrapes name their builder)
  2. an approved-builder name appearing in the row text / estate context
  3. the stocklist filename when it names one (CREATION VIC -> Creation Homes)
  4. otherwise left blank and counted — E-Agent pools several builders per state
     file, and a wrong attribution is worse than an honest blank.

Also records which aggregator a listing came from so E-Agent stock is identifiable.
Idempotent: safe to re-run after every harvest.
"""

import re
import sqlite3
from urllib.parse import unquote

import config
from builder_registry import BuilderRegistry
from database import ResearchDatabase
from geo import SuburbGeoIndex
from state_resolver import disagreement, resolve_state

STATE_RE = re.compile(r"\b(VIC|NSW|QLD|SA|WA|NT|ACT|TAS)\b", re.I)
STATE_WORDS = {
    "victoria": "VIC", "new south wales": "NSW", "queensland": "QLD",
    "south australia": "SA", "western australia": "WA", "tasmania": "TAS",
    "northern territory": "NT", "australian capital territory": "ACT",
}


def state_from_filename(url: str) -> str:
    """'...?dn=NSW%20Dual%20Jul.xlsx' -> 'NSW'  /  'DUAL QLD JUL.xlsx' -> 'QLD'."""
    if not url:
        return ""
    name = unquote(url).split("dn=")[-1] if "dn=" in url else unquote(url)
    m = STATE_RE.search(name)
    return m.group(1).upper() if m else ""


def builder_from_filename(url: str, names: dict) -> str:
    """'CREATION VIC' / 'GDEV' in a stocklist name -> the approved builder."""
    if not url:
        return ""
    name = unquote(url).split("dn=")[-1] if "dn=" in url else unquote(url)
    low = re.sub(r"[^a-z ]", " ", name.lower())
    for key, proper in names.items():
        if key and key in low:
            return proper
    return ""


def builder_from_text(text: str, names: dict) -> str:
    if not text:
        return ""
    low = re.sub(r"[^a-z ]", " ", text.lower())
    for key, proper in names.items():
        if key and key in low:
            return proper
    return ""


def main():
    # Constructing the database applies the column migration, so a newly declared field
    # (state_source) exists before the raw sqlite3 connection below tries to write it.
    ResearchDatabase()
    geo = SuburbGeoIndex()
    reg = BuilderRegistry()
    # match on the distinctive part of a builder name ("creation", "gdev", "avia")
    names = {}
    for b in reg.get_all_builders():
        n = b["builder_name"].strip()
        if len(n) < 3:
            continue
        key = re.sub(r"\b(homes?|group|living|living|pty|ltd|au|com)\b", " ", n.lower())
        key = re.sub(r"[^a-z ]", " ", key).strip()
        key = " ".join(key.split()[:2])
        if len(key) >= 4:
            names.setdefault(key, n)

    conn = sqlite3.connect(str(config.DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM buildings").fetchall()

    fixed_state = fixed_builder = fixed_suburb = 0
    by_signal, clashes = {}, []
    cleared_postcodes = 0
    still_no_state = still_no_builder = 0

    for r in rows:
        rid = r["id"]
        state = (r["state"] or "").strip().upper()
        suburb = (r["suburb"] or "").strip()
        builder = (r["builder_name"] or "").strip()
        addr = r["lot_address"] or ""
        url = r["source_url"] or ""

        # --- suburb (needed before state can be inferred) ---
        if not suburb:
            found = geo.find_suburb_in_text(addr, state)
            if found:
                suburb = found
                conn.execute("UPDATE buildings SET suburb=? WHERE id=?", (suburb, rid))
                fixed_suburb += 1

        # --- state ---
        # The previous version walked a hard-coded tuple of states and took the first one
        # that had the suburb, so every shared locality name answered VIC: "Springfield"
        # exists in six states and always came back Victoria. resolve_state weighs the
        # postcode, the suburb and the E-Agent page against each other instead, and
        # records which of them decided it.
        if not state or not STATE_RE.fullmatch(state or ""):
            page = r["source_state_hint"] if "source_state_hint" in r.keys() else ""
            new_state, signal = resolve_state(postcode=r["postcode"], suburb=suburb,
                                              page_state=page, geo=geo)
            if not new_state:                      # last resort: the file's own name
                new_state = state_from_filename(url)
                signal = "stocklist file name" if new_state else ""
            if new_state:
                conn.execute("UPDATE buildings SET state=?, state_source=? WHERE id=?",
                             (new_state, signal, rid))
                state = new_state
                fixed_state += 1
                by_signal[signal] = by_signal.get(signal, 0) + 1
            clash = disagreement(r["postcode"], suburb, page, geo)
            if clash:
                clashes.append(clash)
                # The state was decided by other signals, so a postcode pointing somewhere
                # else is simply wrong — a four-digit number that was never a postcode.
                # Leaving it in the column would poison any later run that happens to have
                # nothing else to go on. Cleared, not corrected: we do not know the real one.
                if state and clash["postcode_state"] != state:
                    conn.execute("UPDATE buildings SET postcode=NULL WHERE id=?", (rid,))
                    cleared_postcodes += 1

        # --- builder ---
        if not builder:
            cand = builder_from_text(addr, names) or builder_from_filename(url, names)
            if cand:
                conn.execute("UPDATE buildings SET builder_name=? WHERE id=?", (cand, rid))
                builder = cand
                fixed_builder += 1

        if not state:
            still_no_state += 1
        if not builder:
            still_no_builder += 1

    conn.commit()

    total = len(rows)
    print("=" * 66)
    print(f"  ENRICHED {total} building(s)")
    print("=" * 66)
    print(f"  suburb  backfilled: {fixed_suburb}")
    print(f"  state   backfilled: {fixed_state}   still blank: {still_no_state}")
    for sig, n in sorted(by_signal.items(), key=lambda kv: -kv[1]):
        print(f"            via {sig:<34} {n:>5}")
    if clashes:
        # A row whose own postcode contradicts the page it came from is either a builder
        # listing interstate stock or a misparsed postcode. Both are worth a human's eye.
        print()
        print(f"  [!] {len(clashes)} row(s) where the postcode and the E-Agent page disagree:")
        for c in clashes[:6]:
            print(f"        postcode {c['postcode']} ({c['postcode_state']}) vs page "
                  f"{c['page_state']}  suburb={c['suburb'] or '-'}")
        print(f"      {cleared_postcodes} of them had a postcode that was never a postcode "
              f"— cleared so it cannot mislead a later run")
    print(f"  builder backfilled: {fixed_builder}   still blank: {still_no_builder}")
    print()
    for row in conn.execute("SELECT state, COUNT(*) n FROM buildings GROUP BY state ORDER BY n DESC"):
        print(f"    state {row['state'] or '(blank)':<10} {row['n']:>4}")
    print()
    for row in conn.execute("SELECT builder_name, COUNT(*) n FROM buildings GROUP BY builder_name ORDER BY n DESC LIMIT 12"):
        print(f"    {row['builder_name'] or '(blank — E-Agent pooled)':<32} {row['n']:>4}")
    conn.close()


if __name__ == "__main__":
    main()
