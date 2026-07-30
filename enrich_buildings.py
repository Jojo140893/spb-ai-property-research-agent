"""
Backfill State and Builder on harvested stock — the two gaps Coleen called out
on 2026-07-28 ("we should see the builder name separated and we should see the state").

STATE is resolved by state_resolver.resolve_state, which weighs the listing's postcode,
its suburb and the E-Agent page it came from against each other and records which signal
decided it (see that module for why agreement beats a fixed ranking). The stocklist
filename (…?dn=NSW%20Dual%20Jul.xlsx -> NSW) is a last resort.

Both of those are hints about a WHOLE FILE, so the state pass runs twice over the rows:
once to ask each row what it proves about itself, and again to resolve it. A file whose
own rows prove two different states is national, and its hint is then void for every row
in it — the fix for Hudson Homes' 149 lots, which sit on E-Agent's Queensland page and
were exported as Queensland while naming Wadalba, Warnervale, Bellbird and Denman NSW.
A row of a national file that proves nothing itself is left BLANK, not guessed.

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
from typing import Dict
from urllib.parse import unquote

import config
from builder_registry import BuilderRegistry
from database import ResearchDatabase
from geo import SuburbGeoIndex
from state_resolver import (disagreement, file_is_national, own_state, resolve_state)

STATE_RE = re.compile(r"\b(VIC|NSW|QLD|SA|WA|NT|ACT|TAS)\b", re.I)

# Signals that are only ever a claim about the file a row arrived in. A state resting on
# one of these is re-resolved on every run, not left alone like a blank field: the whole
# Hudson error was already written to the column, so a pass that only filled blanks would
# never have corrected it — and would not correct it after the next harvest either.
HINT_ONLY_SIGNALS = ("e-agent page", "e-agent page (conflicting signals)",
                     "stocklist file name")
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


def file_of(row) -> str:
    """The stocklist a row came out of — the unit a state hint is actually a claim about.

    `stocklist_file` where the harvest recorded one; `source_url` otherwise, which is the
    same string for E-Agent's own PDFs and is all a portal scrape has.
    """
    keys = row.keys()
    for k in ("stocklist_file", "source_url"):
        if k in keys and (row[k] or "").strip():
            return (row[k] or "").strip()
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
    cleared_postcodes = cleared_states = corrected_states = retraced_states = 0
    still_no_state = still_no_builder = 0

    # --- pass 1: suburb, and what each row proves about its own state ---------
    # A hint has to be judged against the rows it was stamped on, so the rows have to be
    # read before any of them is resolved.
    proven: Dict[int, tuple] = {}
    per_file: Dict[str, set] = {}
    for r in rows:
        rid = r["id"]
        suburb = (r["suburb"] or "").strip()
        if not suburb:
            found = geo.find_suburb_in_text(r["lot_address"] or "",
                                            (r["state"] or "").strip().upper())
            if found:
                suburb = found
                conn.execute("UPDATE buildings SET suburb=? WHERE id=?", (suburb, rid))
                fixed_suburb += 1
        state, why = own_state(postcode=r["postcode"], suburb=suburb,
                               estate=r["estate_name"], address=r["lot_address"], geo=geo)
        proven[rid] = (suburb, state, why)
        per_file.setdefault(file_of(r), set()).add(state)

    national = {f for f, states in per_file.items() if file_is_national(states)}

    # --- pass 2: resolve ------------------------------------------------------
    for r in rows:
        rid = r["id"]
        suburb, mine, why = proven[rid]
        state = (r["state"] or "").strip().upper()
        builder = (r["builder_name"] or "").strip()
        addr = r["lot_address"] or ""
        url = r["source_url"] or ""

        # --- state ---
        # The first version walked a hard-coded tuple of states and took the first one that
        # had the suburb, so every shared locality name answered VIC: "Springfield" exists
        # in six states and always came back Victoria. resolve_state weighs the postcode,
        # the suburb, what the row says about itself and the E-Agent page against each other
        # instead, and records which of them decided it.
        #
        # A state already resting on a file-level hint is reconsidered rather than left
        # alone. That is the only way the Hudson rows get corrected: their state was
        # already written, so a fill-the-blanks pass would skip all 149 of them.
        settled = bool(state) and bool(STATE_RE.fullmatch(state or "")) \
            and (r["state_source"] or "") not in HINT_ONLY_SIGNALS
        if not settled:
            page = r["source_state_hint"] if "source_state_hint" in r.keys() else ""
            if file_of(r) in national:
                # The file covers more than one state, so nothing said ABOUT the file is a
                # claim about this row: not the page it hangs on, not its own name, and not
                # a lone gazetteer hit on a suburb column that the file has already been
                # caught mislabelling. Only what the row proves counts, or nothing.
                new_state, signal = mine, why
            else:
                new_state, signal = resolve_state(postcode=r["postcode"], suburb=suburb,
                                                  page_state=page, geo=geo,
                                                  listing_state=mine)
                if not new_state:                  # last resort: the file's own name
                    from_file = state_from_filename(url)
                    if from_file:
                        new_state, signal = from_file, "stocklist file name"
            # Written when the SIGNAL moves as well as when the state does. A row that now
            # answers out of its own text but happens to land on the state the page claimed
            # would otherwise keep 'e-agent page' in the column, and the point of recording
            # the signal is that a state in front of a buyer can be traced to what decided it.
            if new_state != state or (signal or None) != r["state_source"]:
                # NULL, never '': an empty string would sort and group as a second kind of
                # blank beside the rows that never had a state at all.
                conn.execute("UPDATE buildings SET state=?, state_source=? WHERE id=?",
                             (new_state or None, signal or None, rid))
                if new_state == state:
                    retraced_states += 1
                elif not new_state:
                    # Held a hint-derived state that the file has now disproved, and proves
                    # nothing itself. Blank beats a coin flip between two states.
                    cleared_states += 1
                elif state:
                    corrected_states += 1
                else:
                    fixed_state += 1
                state = new_state
            if new_state:
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
    print(f"  state   corrected : {corrected_states}   cleared: {cleared_states}"
          f"   same state, better signal: {retraced_states}")
    for sig, n in sorted(by_signal.items(), key=lambda kv: -kv[1]):
        print(f"            via {sig:<34} {n:>5}")
    if national:
        # The file, not the row, is what was wrong. Printed in full because each one is a
        # decision to stop trusting a whole file, and a human should be able to check it.
        print()
        print(f"  [!] {len(national)} stocklist(s) are NATIONAL — their own rows prove more")
        print(f"      than one state, so the page/filename hint was dropped for every row:")
        for f in sorted(national):
            its = [r for r in rows if file_of(r) == f]
            says = {}
            for r in its:
                sub, st, why = proven[r["id"]]
                if st:
                    says.setdefault(st, (why, sub))
            hints = sorted({(r["source_state_hint"] or "-") for r in its})
            blank = sum(1 for r in its if not proven[r["id"]][1])
            print(f"        {len(its):>4} row(s), hint said {'/'.join(hints)}, the rows say "
                  f"{'/'.join(sorted(says))}   {f[-58:]}")
            for st, (why, sub) in sorted(says.items()):
                print(f"               {st:<4} {why} — {sub!r}")
            print(f"               {blank} row(s) prove nothing themselves and stay blank")

    # What is still taken on trust, and how much of it could be checked at all. A hint in a
    # file where no row names a place anywhere is the shape the Hudson error had, and the
    # only reason it is kept is that E-Agent's navigation is the sole signal those rows have
    # — apartment schedules read "1 101 2 BED + 2 BATH" and name nothing. Printed so the
    # number is visible rather than implied, and so it can be watched.
    speaks = {f for f, states in per_file.items() if states - {""}}
    on_hint = corroborated = unfalsifiable = 0
    for r in conn.execute("SELECT state_source, stocklist_file, source_url FROM buildings"):
        if (r["state_source"] or "") not in HINT_ONLY_SIGNALS:
            continue
        on_hint += 1
        if file_of(r) in speaks:
            corroborated += 1
        else:
            unfalsifiable += 1
    print()
    print(f"  {on_hint} row(s) still rest on a page/filename hint alone:")
    print(f"      {corroborated} in a file where another row proves that state")
    print(f"      {unfalsifiable} in a file where no row names any place — the hint cannot be "
          f"checked")
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
