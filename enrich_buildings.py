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

A STATE THAT RESTS ON A SUBURB MATCH IS CHECKED AGAINST THE ROW'S OWN TEXT (Part C, W02).
When the only thing behind a state is a suburb-name match -- signal 'suburb', or 'suburb +
e-agent page' on a suburb guessed from the address -- and the row's own line names a
locality in exactly one OTHER state where the catalogue holds live stock ("Available
Wollert Titled Townhouse 618" filed QLD off an estate called Riverhills), the state is
rewritten to that other state with state_source 'row text locality (over suburb column)'.
Every such write is printed with its evidence. See state_resolver.text_locality_elsewhere.

Switched off by SPB_TEXT_LOCALITY_OVERRULE=0. ROLLBACK -- re-resolve every row this check
moved, by today's rules with the check switched off:

    SPB_DATABASE_PATH=<db> python enrich_buildings.py --rollback-text-locality

That undoes the writes for this run only; the check re-applies on the next run unless the
change is reverted or SPB_TEXT_LOCALITY_OVERRULE=0 is set where the nightly runs. After a
code revert the flag no longer exists, and the same rollback is the SQL below followed by
a plain `python enrich_buildings.py`, which re-resolves the blanked rows:

    UPDATE buildings SET state=NULL, state_source=NULL
     WHERE state_source='row text locality (over suburb column)';

Re-resolved is not restored: a row in a stocklist whose own rows prove two states comes
back blank rather than with the state the suburb match once gave it.
"""

import argparse
import os
import re
import sqlite3
import sys
import traceback
from typing import Dict, List
from urllib.parse import unquote

import config
from database import ResearchDatabase
from geo import SuburbGeoIndex
from state_resolver import (TEXT_LOCALITY_SOURCE, disagreement, file_is_national, own_state,
                            resolve_state, text_locality_elsewhere)

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


def rests_on_a_suburb_match(signal, suburb_from_column: bool) -> bool:
    """True when a state's only support is a suburb-name match.

    'suburb' is a lone gazetteer vote, whether the suburb came from the column or was
    guessed from the address. 'suburb + e-agent page' counts only when the suburb was
    GUESSED: then the page picked between the states of a name the extractor never read
    as a suburb ("Windermere" out of "Windermere Mambourin").

    The same test is applied to a state being resolved now and to one already written, so
    a row lands on the same answer whichever way it reaches the check, and a second run
    has nothing left to change.
    """
    signal = (signal or "").strip()
    return signal == "suburb" or (signal == "suburb + e-agent page" and not suburb_from_column)


def overrule_evidence(rows, geo):
    """What the catalogue itself says about places: (stocked, estates).

    stocked  {(locality lower-case, state)} for every live row whose suburb COLUMN is a
             locality of its stored state.
    estates  {(builder lower-case, estate lower-case): {state: {row ids}}} over live rows.
    """
    stocked, estates = set(), {}
    for r in rows:
        if r["superseded_by"]:
            continue
        st = (r["state"] or "").strip().upper()
        if not STATE_RE.fullmatch(st or ""):
            continue
        sub = (r["suburb"] or "").strip()
        if sub and geo.locate(sub, st):
            stocked.add((sub.lower(), st))
        estate = (r["estate_name"] or "").strip().lower()
        if estate:
            key = ((r["builder_name"] or "").strip().lower(), estate)
            estates.setdefault(key, {}).setdefault(st, set()).add(r["id"])
    return stocked, estates


def overruled_state(r, suburb, state, geo, evidence):
    """(other_state, evidence text) when the row's own text overrules `state`, else None.

    Two ways, in this order:
      1. its text names a locality of one other state that holds stock there
         (state_resolver.text_locality_elsewhere);
      2. its suburb value IS its estate name, and that builder's other live rows of the
         same estate sit in exactly one state, which is not this one. Riverhills: the
         column holds the estate, Riverhills is a QLD suburb, and Hattan's other Riverhills
         rows are all VIC. A NAMED builder only: estate names repeat across the country,
         and rows with no builder are every unnamed builder at once.
    """
    stocked, estates = evidence
    text = " ".join(str(r[k] or "") for k in ("source_text", "lot_address"))
    hit = text_locality_elsewhere(text, state, geo, stocked)
    if hit:
        return hit[1], f"text names {hit[0]!r}"
    estate = (r["estate_name"] or "").strip().lower()
    builder = (r["builder_name"] or "").strip().lower()
    if builder and estate and suburb and suburb.strip().lower() == estate:
        key = (builder, estate)
        where = {st for st, ids in estates.get(key, {}).items() if ids - {r["id"]}}
        if len(where) == 1 and state not in where:
            other = next(iter(where))
            return other, f"estate {r['estate_name']!r} is stocked in {other} by the same builder"
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Backfill state on harvested stock.")
    ap.add_argument("--rollback-text-locality", action="store_true",
                    help=f"re-resolve every row whose state_source is "
                         f"'{TEXT_LOCALITY_SOURCE}', with that check switched off")
    args = ap.parse_args([] if argv is None else argv)
    rollback = args.rollback_text_locality
    overrule_on = (os.environ.get("SPB_TEXT_LOCALITY_OVERRULE", "1").strip() != "0"
                   and not rollback)
    # Problems in the NEW check only. It runs inside a required nightly step, so it must
    # never be what stops the run: on any error the row keeps the state today's rules give
    # it, the error is printed, and the exit code is 2 so run_daily marks the run
    # degraded and CI goes red AFTER publishing.
    degraded: List[str] = []
    overruled: List[tuple] = []

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
    if rollback:
        # Not committed on its own: the rows are re-resolved below on this connection,
        # and the clear lands in the same commit as their new states or not at all.
        n = conn.execute("UPDATE buildings SET state=NULL, state_source=NULL "
                         "WHERE state_source=?", (TEXT_LOCALITY_SOURCE,)).rowcount
        print(f"  ROLLBACK: {n} row(s) held a state from '{TEXT_LOCALITY_SOURCE}' -- "
              f"cleared, and re-resolved below with that check switched off")
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
    # Whether the suburb pass 2 votes with came out of the column or was guessed from the
    # address below -- a guessed "Windermere" is weaker than a stated one.
    from_column: Dict[int, bool] = {}
    evidence = (set(), {})
    if overrule_on:
        try:
            evidence = overrule_evidence(rows, geo)
        except Exception as exc:                                       # noqa: BLE001
            overrule_on = False
            degraded.append(f"building the stock-by-place evidence failed: {exc!r}")
            traceback.print_exc()
    for r in rows:
        rid = r["id"]
        suburb = (r["suburb"] or "").strip()
        from_column[rid] = bool(suburb)
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

    check_errors: List[str] = []

    def text_overrules(r, suburb, state, signal):
        """(other_state, evidence) when this row's own text overrules a suburb-match state.

        None when the check is off, the row is superseded, the state does not rest on a
        suburb match, or the check itself raised -- in which case the row keeps today's
        answer and the run is reported degraded.
        """
        if not (overrule_on and state and not r["superseded_by"]
                and rests_on_a_suburb_match(signal, from_column[r["id"]])):
            return None
        try:
            return overruled_state(r, suburb, state, geo, evidence)
        except Exception as exc:                                       # noqa: BLE001
            if not check_errors:
                traceback.print_exc()
            check_errors.append(f"id {r['id']}: {exc!r}")
            return None

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
        # A SETTLED STATE THAT RESTS ON A SUBURB MATCH is still checked against the row's
        # own text. Settled rows are never re-resolved, which is right for most of them --
        # re-resolving every 'suburb' state blanked 64 correct ones, because their files
        # prove more than one state -- but it also meant a state resting on an estate name
        # in the suburb column was never looked at again. Checked, not re-resolved: the
        # state changes only when the text names another state's locality.
        if settled:
            hit = text_overrules(r, suburb, state, r["state_source"])
            if hit:
                _write(conn, rid, state=hit[0], state_source=TEXT_LOCALITY_SOURCE)
                overruled.append((rid, state, hit[0], suburb, from_column[rid], hit[1]))
                corrected_states += 1
                state = hit[0]
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
                # The same check as for a settled row, so the answer does not depend on
                # whether the state was already written when the check first ran.
                hit = text_overrules(r, suburb, new_state, signal)
                if hit:
                    overruled.append((rid, state or "-", hit[0], suburb, from_column[rid],
                                      hit[1]))
                    new_state, signal = hit[0], TEXT_LOCALITY_SOURCE
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
    # Every move, with what justified it. Printed in full because each one changes which
    # state's searches a listing appears in, and a person reviewing the nightly log must be
    # able to check it against the line itself.
    print(f"  state   overruled by the row's own text: {len(overruled)}"
          + ("" if overrule_on else
             "   (check switched off" + (" -- rollback run)" if rollback else ")")))
    for rid, was, now, sub, col, why in overruled:
        print(f"            id {rid:<7} {was:<3} -> {now:<3}  suburb {sub!r} "
              f"({'column' if col else 'guessed'})  {why}")
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

    if check_errors:
        degraded.append(f"the row-text check raised on {len(check_errors)} row(s), first "
                        f"{check_errors[0]}; those rows kept today's state")
    if degraded:
        print()
        for line in degraded:
            print(f"  [DEGRADED] ERROR in the row-text state check: {line}")
        print("  Every other state decision above was written as normal. Exit 2 so the run "
              "is marked degraded and goes red after publishing.")
        return 2
    return 0


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
    raise SystemExit(main(sys.argv[1:]))
