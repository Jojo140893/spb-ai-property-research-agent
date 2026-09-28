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
change is reverted or SPB_TEXT_LOCALITY_OVERRULE=0 is set where the nightly runs.

Reverting the code alone undoes NOTHING: the moved rows carry a state_source the old code
treats as settled, so it never looks at them again. After a revert the flag above no
longer exists, and the rollback is this SQL followed by a plain `python enrich_buildings.py`,
which re-resolves the blanked rows:

    UPDATE buildings SET state=NULL, state_source=NULL
     WHERE state_source='row text locality (over suburb column)';

Re-resolved is not restored: a row in a stocklist whose own rows prove two states comes
back blank rather than with the state the suburb match once gave it.

The nightly's database lives in the Supabase bucket, so either rollback is run on the file
`python state_sync.py pull` fetches (the pull takes the harvest lock, so it cannot race a
nightly) and published with `python state_sync.py push`.

A STATE READ OFF THE ROW'S OWN LOT NUMBER IS RE-RESOLVED (Coleen, 28 Sep 2026). A stored
state whose state_source cites a postcode, on a row whose postcode is its own lot number
("...SS West 4327 Jubilee Wyndhamvale...": lot 4327, "postcode" 4327, QLD), is treated as
not settled: the postcode is cleared and the state resolved again with it ignored. See
rests_on_its_lot_number. Every such row is printed with its old and new state and the
number, which is the whole record needed to undo it by hand -- the postcode column is
cleared, so no SQL can find these rows afterwards.

A LOT NUMBER IN THE POSTCODE COLUMN OF ANY LIVE ROW IS CLEARED (review, 28 Sep 2026), not
only on the rows re-resolved above. The resolver already ignores it, so no state moves;
but the column is shown to Coleen and goes out in the pick CSV, and once the re-resolve
above had run over a copy of the 28 Sep nightly, 94 live rows still showed their lot
there -- 39 of them a number from another state's range, like 11847 "7608 Sugar Maple
Cct, Aintree", VIC, postcode 7608 (a TAS number).
Printed with a count line, id by id, for the same reason as above.

KNOWN EDGE, left as it is: Hattan 5375-5377 and 8359 ("Available Clyde North Q3 2026
Townhouse 3673 ... | Ceduna") carry the house design "Ceduna" -- a South Australian
place -- in their suburb column. Their lot numbers 3673-3677 happen to be VIC postcodes,
which used to cancel that SA vote to a blank; with the lot numbers ignored, the suburb
column is the only vote left, and they come out VIC only because the text-locality check
above reads "Clyde North" in their line. With SPB_TEXT_LOCALITY_OVERRULE=0, or after
--rollback-text-locality (they carry that check's state_source), resolve_state files all
four SA by "suburb". Anyone switching that check off should re-check these four.
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
                            postcode_is_lot_number, resolve_state, state_from_postcode,
                            text_locality_elsewhere)

STATE_RE = re.compile(r"\b(VIC|NSW|QLD|SA|WA|NT|ACT|TAS)\b", re.I)

# The exit code for "every state was written by today's rules, the row-text check failed
# and was dropped". It must equal run_daily.DEGRADED_EXIT (tests pin them): run_daily reads
# it as finished-but-DEGRADED, so the chain publishes and CI goes red afterwards. Not 2:
# run_daily files 2 as a PARTIAL harvest and CI then names a dead channel and prescribes a
# portal login, which is the wrong cure for a crash in this check.
DEGRADED_EXIT = 70

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


def cites_a_postcode(signal) -> bool:
    """True when a stored state_source names the postcode as one of its signals.

    The signals are joined with " + " ("suburb + postcode", "postcode + e-agent page",
    "listing locality + postcode"), so the test is on the parts, not a substring: the
    repair note "withdrawn: postcode was the lot number" is not a signal, and a state
    that says "listing text (over NSW)" was decided by the row's own words.
    """
    return "postcode" in [p.strip() for p in str(signal or "").split("+")]


def rests_on_its_lot_number(r) -> bool:
    """A stored state decided by a postcode that is the row's own lot number.

    Coleen, 28 Sep 2026: a QLD search offered two "Jubilee, QLD" lots that are the Jubilee
    estate in Wyndham Vale, VIC. Their line reads "...SS West 4327 Jubilee Wyndhamvale...",
    4327 is the lot, and it was stored as the postcode too; own_state then proved QLD by
    "suburb + postcode" over the VIC page. Such a state is SETTLED by the rule below, so
    fixing state_resolver alone would never reach the rows already written -- the same
    trap as the Hudson rows the hint-only rule exists for. On the 28 Sep nightly 18 live
    rows are in this position and 12 are in the wrong state.
    """
    return (cites_a_postcode(r["state_source"])
            and postcode_is_lot_number(r["postcode"], r["lot_number"]))


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
             locality of its stored state -- unless that state itself rests on nothing
             but the suburb column. Such a row is the very claim under suspicion, so it
             cannot vouch for its own place: 8349 ('Ceduna' in the column, SA from that
             alone) would otherwise make Ceduna SA "stocked", and its correct VIC sibling
             in the same Hattan list ("... Clyde North ... Lot 3755, Ceduna") would be
             moved to South Australia on the same night 8349 is moved out of it.
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
        if sub and geo.locate(sub, st) \
                and not rests_on_a_suburb_match(r["state_source"], suburb_from_column=True):
            stocked.add((sub.lower(), st))
        estate = (r["estate_name"] or "").strip().lower()
        if estate:
            key = ((r["builder_name"] or "").strip().lower(), estate)
            estates.setdefault(key, {}).setdefault(st, set()).add(r["id"])
    return stocked, estates


_URL_OR_EMAIL = re.compile(r"(?:https?://|www\.)\S+|\S+@\S+", re.I)


def _excerpt(text, name, width=45):
    """The stretch of the row's line around `name`, for the nightly log.

    The name alone is not reviewable -- "text names 'Armstrong'" on a lot in Armstrong
    Creek reads like a bug until the line is beside it. Links and e-mail addresses are
    dropped: this goes to a CI log.
    """
    flat = " ".join(_URL_OR_EMAIL.sub(" ", str(text or "")).split())
    words = [re.escape(w) for w in str(name or "").split()]
    m = re.search(r"\b" + r"[^A-Za-z0-9]+".join(words) + r"\b", flat, re.I) if words else None
    if not m:
        return flat[:2 * width]
    lo, hi = max(0, m.start() - width), min(len(flat), m.end() + width)
    return ("..." if lo else "") + flat[lo:hi] + ("..." if hi < len(flat) else "")


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
    hit = text_locality_elsewhere(text, state, geo, stocked, estate=r["estate_name"])
    if hit:
        return hit[1], f"text names {hit[0]!r}: {_excerpt(text, hit[0])!r}"
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
    overrule_on = (os.environ.get("SPB_TEXT_LOCALITY_OVERRULE", "1").strip().lower()
                   not in ("0", "false", "off", "no")
                   and not rollback)
    # Problems in the NEW check only. It runs inside a required nightly step, so it must
    # never be what stops the run: on any error the row keeps the state today's rules give
    # it, the error is printed, and the exit code is DEGRADED_EXIT so run_daily marks the
    # run degraded and CI goes red AFTER publishing.
    degraded: List[str] = []
    overruled: List[tuple] = []
    # Settled states that rested on the row's own lot number read as a postcode, each
    # re-resolved with its postcode cleared (rests_on_its_lot_number).
    lot_postcodes: List[tuple] = []
    # Every other live row whose postcode is its own lot number: postcode cleared, state
    # not touched (the resolver already ignores that number).
    lot_numbers_cleared: List[tuple] = []

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
                               estate=r["estate_name"], address=r["lot_address"], geo=geo,
                               lot_number=r["lot_number"])
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
        # A STATE READ OFF THE ROW'S OWN LOT NUMBER is not settled either (Coleen, 28 Sep
        # 2026 -- see rests_on_its_lot_number). It is re-resolved below with that number
        # ignored, and the number comes out of the postcode column: it was never the
        # postcode, and left there it is what the next reader of the column trusts.
        # postcode is not a content_hash field (database._HASH_FIELDS), so clearing it
        # does not re-identify the row; _write refuses a column that is.
        reopened = settled and rests_on_its_lot_number(r)
        if reopened:
            settled = False
            _write(conn, rid, postcode=None)
            was = (state, r["state_source"])
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
                                                  listing_state=mine,
                                                  lot_number=r["lot_number"])
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
            clash = disagreement(r["postcode"], suburb, page, geo,
                                 lot_number=r["lot_number"])
            if clash:
                clashes.append(clash)
                # The state was decided by other signals, so a postcode pointing somewhere
                # else is simply wrong — a four-digit number that was never a postcode.
                # Leaving it in the column would poison any later run that happens to have
                # nothing else to go on. Cleared, not corrected: we do not know the real one.
                if state and clash["postcode_state"] != state:
                    _write(conn, rid, postcode=None)
                    cleared_postcodes += 1
            if reopened:
                lot_postcodes.append((rid, r["postcode"], was[0], was[1], state,
                                      signal if state else ""))

        # A LOT NUMBER LEFT IN THE POSTCODE COLUMN of a live row comes out too, whatever the
        # row's state rests on (review, 28 Sep 2026). The re-open above only reached rows
        # whose settled state CITED the postcode, which left 94 live rows showing their lot
        # as a postcode -- 39 of them another state's number, e.g. 11847, VIC, 7608 (TAS).
        # Done after the state is decided and changes no input to it: own_state,
        # resolve_state and disagreement all ignore this number already, so the next run
        # resolves the row exactly as this one did. Live rows only; the re-open above has
        # already cleared its own rows, superseded or not.
        if not reopened and not r["superseded_by"] \
                and postcode_is_lot_number(r["postcode"], r["lot_number"]):
            _write(conn, rid, postcode=None)
            lot_numbers_cleared.append((rid, r["postcode"], state))

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
    # Zero on a healthy night: the first run clears every such postcode, and the write-time
    # guard in database.record_building stops a harvest storing another. A number here
    # after the first night means a path round that guard. Every row is printed, because
    # each one changes which state's searches the lot appears in.
    print(f"  state   re-resolved, its postcode was its own lot number (postcode cleared): "
          f"{len(lot_postcodes)}")
    for rid, pc, was, was_src, now, now_src in lot_postcodes:
        print(f"            id {rid:<7} {was or '-':<3} -> {now or '-':<3}  lot/postcode {pc}  "
              f"was {was_src!r}, now {now_src or 'blank'!r}")
    # Zero on a healthy night too. Each row is printed with the number, because the column
    # is cleared and the log is then the only record of what it held; the number's own
    # range is shown so the ones that named another state can be picked out by eye.
    print(f"  postcode cleared, it was the live row's own lot number (state unchanged): "
          f"{len(lot_numbers_cleared)}")
    for rid, pc, st in lot_numbers_cleared:
        print(f"            id {rid:<7} {st or '-':<3}  lot/postcode {pc} "
              f"({state_from_postcode(pc) or 'no'} range)")
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
        print(f"  Every other state decision above was written as normal. Exit "
              f"{DEGRADED_EXIT} so the run is marked degraded and goes red after publishing.")
        return DEGRADED_EXIT
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
