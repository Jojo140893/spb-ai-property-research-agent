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

BUILDER is NOT resolved here any more, and this step no longer writes builder_name.
It is decided at WRITE time by builder_names.py, before content_hash is computed, which
is the only place a name can be settled without changing the row's identity. What this
step used to do instead — guess from the row text and the stocklist filename, and UPDATE
the column afterwards — put 319 rows under a builder called "Proxima" and 192 under one
called "hattan.com.au", and 63 of those are still live and can never self-heal. This
step now only REPORTS what is still blank, and how many stored names disagree with the
canonical spelling of themselves.

Also records which aggregator a listing came from so E-Agent stock is identifiable.
Idempotent: safe to re-run after every harvest.
"""

import re
import sqlite3
from typing import Dict
from urllib.parse import unquote

import config
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


def _assert_no_hashed_fields(cols):
    """Refuse the write if it touches a column that decides row identity.

    This file has grown the same defect TWICE. It backfilled `suburb` (603 writes
    queued, all 603 identity-breaking) and it backfilled `builder_name` — 319 rows
    stamped "Proxima", then 192 stamped "hattan.com.au", of which 63 are still live and
    still cannot self-heal: their content_hash encodes builder_key='', so the harvest
    matches them and record_building's COALESCE fill can never correct a non-empty wrong
    value. Both times the write was removed and replaced by a comment saying not to add
    it back. A comment is not a guard, and it lost twice.

    Mirrors reparse_from_source._assert_no_hashed_fields. Named columns are compared
    against _HASH_FIELDS with the two aliases undone (builder_key -> builder_name,
    suburb_norm -> suburb), so both shapes are caught.
    """
    from database import _HASH_FIELDS
    hashed = {f.replace("_norm", "").replace("builder_key", "builder_name")
              for f in _HASH_FIELDS}
    clash = sorted(set(cols) & hashed)
    if clash:
        raise SystemExit(
            f"[ABORT] enrich would write {clash}, which decide content_hash. Writing "
            f"them re-identifies the row, so the next harvest INSERTs a duplicate "
            f"instead of updating in place -- the mechanism behind 777 duplicate "
            f"captures, and behind the 63 live rows still stuck on 'hattan.com.au'. "
            f"Resolve those at write time (builder_names.py) or at read time "
            f"(suburb_quality.py) instead.")


def _write(conn, rid, **cols):
    """The only way this module is allowed to write to `buildings`."""
    _assert_no_hashed_fields(cols)
    sets = ", ".join(f"{c}=?" for c in cols)
    conn.execute(f"UPDATE buildings SET {sets} WHERE id=?", (*cols.values(), rid))


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


def main():
    # Constructing the database applies the column migration, so a newly declared field
    # (state_source) exists before the raw sqlite3 connection below tries to write it.
    ResearchDatabase()
    geo = SuburbGeoIndex()
    # The builder name-key map that used to live here is GONE, along with
    # builder_from_text and builder_from_filename. It stripped \b(au|com)\b out of every
    # registry name, so `hattan.com.au` and `Hattan Homes` both collapsed onto the key
    # "hattan" and `names.setdefault` handed that key to whichever the sheet listed
    # first -- the website, on line 21. That map is what named the builder
    # "hattan.com.au" on 192 rows.
    #
    # Its write was disarmed on 7 Aug but the guess itself was left computing, feeding
    # nothing but a `builder backfilled:` line in this report. Live code that works out
    # a wrong name and only almost writes it is how the write got re-added once already,
    # so the guess is removed rather than muted, and the report below counts blanks
    # truthfully instead of counting an attribution nothing stores. Builder names are
    # resolved at WRITE time by builder_names.py, before the hash, which is the only
    # place it is safe to do.

    conn = sqlite3.connect(str(config.DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM buildings").fetchall()

    fixed_state = fixed_suburb = 0
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
            # IN MEMORY ONLY. This used to also
            #
            #     conn.execute("UPDATE buildings SET suburb=? WHERE id=?", ...)
            #
            # which is the one write this codebase must never make: suburb_norm is one of
            # the eight inputs to building_content_hash, so filling the column changes the
            # row's identity and the next harvest INSERTS a second copy instead of
            # updating it. Replayed against live stock this had 603 writes queued and all
            # 603 broke identity — the same mechanism that produced 777 duplicate captures
            # when builder_name was rewritten. It had already fired on six nightly runs
            # (275, 2, 0, 13, 189, 6 rows).
            #
            # It was also wrong on the merits. 602 of the 603 rows have no state at all,
            # so find_suburb_in_text free-runs over every state and returns the house
            # DESIGN, the estate or the region rather than the locality:
            #
            #     'West 777 Harlow Tarneit Available Carlton 18'  -> 'Carlton'   (design; lot is Tarneit)
            #     'West 705 Balmoral Strathtulloh Sold London 22' -> 'Balmoral'  (lot is Strathtulloh)
            #     'Geelong 11921 Warralily Armstrong Cree k'      -> 'Geelong'   (region; lot is Armstrong Creek)
            #
            # The value is still computed and still feeds own_state below, unchanged, so
            # state resolution behaves exactly as before — `state` is deliberately NOT a
            # hash field, which is why writing it is safe and writing this is not. What a
            # row's suburb should DISPLAY as is decided on the way out, by
            # suburb_quality.resolve.
            found = geo.find_suburb_in_text(r["lot_address"] or "",
                                            (r["state"] or "").strip().upper())
            if found:
                suburb = found
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
                _write(conn, rid, state=new_state or None, state_source=signal or None)
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
                    _write(conn, rid, postcode=None)
                    cleared_postcodes += 1

        if not state:
            still_no_state += 1
        if not builder:
            still_no_builder += 1

    conn.commit()

    total = len(rows)
    print("=" * 66)
    print(f"  ENRICHED {total} building(s)")
    print("=" * 66)
    # Always 0, and kept visible on purpose: this step used to backfill the suburb
    # column and must never do so again (see the note in the loop above). A line that
    # reads 0 every night is the cheapest possible alarm if someone re-adds the write.
    print(f"  suburb  backfilled: {fixed_suburb}  (never written -- part of content_hash)")
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
    # Always 0, kept visible for the same reason the suburb line above is: this step
    # wrote builder_name on 192 rows and the number never appeared as an alarm, because
    # the line it printed counted a guess rather than a write. The blank count is now the
    # true one -- a bigger number, and an honest one.
    print(f"  builder backfilled: 0   still blank: {still_no_builder}"
          f"   (never written -- part of content_hash)")
    print()
    for row in conn.execute("SELECT state, COUNT(*) n FROM buildings GROUP BY state ORDER BY n DESC"):
        print(f"    state {row['state'] or '(blank)':<10} {row['n']:>4}")
    print()
    for row in conn.execute("SELECT builder_name, COUNT(*) n FROM buildings GROUP BY builder_name ORDER BY n DESC LIMIT 12"):
        print(f"    {row['builder_name'] or '(blank — E-Agent pooled)':<32} {row['n']:>4}")
    _report_non_canonical_names(conn)
    conn.close()


def _report_non_canonical_names(conn):
    """Which stored builder names are not the canonical spelling of themselves.

    A REPORT, not a gate. A fail-closed check on this column would raise
    sqlite3.IntegrityError, and record_building catches exactly that at database.py:523
    and turns it into "unchanged" — so an over-strict rule would silently DROP live
    listings down the same path that once lost 160 of them. All the alarm value, none of
    that risk.

    Zero is the healthy reading. It went to 192 while `hattan.com.au` sat in this column
    and nobody saw it for eight days, because nothing counted it.
    """
    from builder_names import BuilderNameCanonicaliser
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT builder_name, COUNT(*) FROM buildings "
        "WHERE TRIM(COALESCE(builder_name,'')) <> '' GROUP BY builder_name")}
    canon = BuilderNameCanonicaliser(counts)
    odd = {n: c for n, c in counts.items() if canon.canonical(n) != n}
    print()
    print(f"  {sum(odd.values())} row(s) hold a builder name that is not its own canonical "
          f"spelling ({len(odd)} name(s)):")
    for n, c in sorted(odd.items(), key=lambda kv: -kv[1]):
        print(f"        {n!r:<34} {c:>5}  ->  {canon.canonical(n)!r}")
    if not odd:
        print(f"        none — every stored name is the one name for that builder")


if __name__ == "__main__":
    main()
