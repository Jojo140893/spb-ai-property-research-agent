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

THE NUMBER OPENING THE ROW IS ITS LOT TOO (29 Sep 2026), lot_number or not: AVIA 2234,
"2120 Stage 21 ...", kept the postcode 2120 an older parser stored, proved NSW with it,
and made AVIA's QLD sheet national. Both rules above take it (state_resolver.
postcode_opens_the_row), and it is cleared off every live row the same way.

A STATE THAT RESTED ON A GUESS THE GUESS NO LONGER MAKES IS RE-RESOLVED (29 Sep 2026).
The suburb guessed for a row with a blank suburb column is state_resolver.
suburb_in_row_text, which does not take the row's estate ("Round Mountain Estate"), the
estate before the comma ("5629 The Grove, Tarneit") or a house design ("Verona SG"). A
settled state whose signal names the suburb, on such a row, is re-resolved when tonight's
guess is not a place in that state and the row does not prove it (rests_on_a_withdrawn_
guess). Every such row is printed with its old and new state and signal and the words the
guess reads now -- the record needed to undo one by hand.

A POSTCODE THE ROW WRITES ONLY AS ANOTHER NUMBER IS NONE EITHER (review, 29 Sep 2026): a
land area ("Titled 2064 39 x 54"), a decimal ("2730.00") or a hyphenated number ("Cold
Shell 03-7076") -- state_resolver.postcode_written_as_another_number, taken by the same
two rules and cleared the same way. And a settled state the row's own proof took over
its page is re-resolved when the row proves nothing tonight: "21 The Grove Boondall",
NSW over its QLD page on the street name in the suburb column (overrode_the_page_on_a_
withdrawn_proof).

Measured over a copy of the 29 Sep nightly against origin/main's enrich: 954 rows change
(214 live tonight, 195 of those in state), every one attributed; five sheets that were
national only on a misreading are not any more -- AVIA's, Silkwood's, Verv's, APLACE's
VIC sheet (a land area read as a postcode) and Creation Homes' QLD sheet (a street name
in the suburb column) -- and their unsettled rows take the sheet's state; 30 settled
states are re-resolved, 47 misread postcodes leave the column, and a second run writes
nothing. A file whose own rows prove two states is still national on ONE row: the
Victorian stocklist 069fe0_993f lists 23154 "Smithfield Plains, South Australia, 5114"
beside Murray Bridge, SA, lot 23153, which proves nothing itself -- a two-vote rule would
stamp that one VIC. So each wrong vote is fixed where it is made instead.

NEVER A STATE ORIGIN/MAIN WOULD NOT GIVE (round 2, 29 Sep 2026). Both reviews of the rules
above found rows the night did not hold that they would put in a wrong state origin/main
never gave -- "Lot 5, Wyndham Vale Jubilee" to QLD, the Eternal Homes lot at "77 Brooklyn
Park Rise" to SA over its NSW page -- and a differential harness over 25,000 generated rows
found 1,192 more. So a row's answer is held to origin/main 31d13ef's: its guess
(geo.find_suburb_in_text with the stored state), its own_state and resolve_state, its
national files and its row-text check, run on the same stored columns (origin_main_state).
Tonight's answer is kept when it is origin/main's, none, the row's page or file-name state,
its stated postcode's (state_resolver.postcode_stated), or -- with no page, and only where
origin/main's answer is blank -- the longer name the row's own line opens with around
origin/main's guess ("Available Clyde North ..." for Clyde: round 1's Hattan rows, see
longer_name_stands); anything else gets origin/main's answer, and the row is printed
(bound). The blank-suburb reading is weighed the same way before it votes (guess_suburb).
Over the 29 Sep copy no row is held -- the reading and the bound between them change 2 rows
from round 1's result, both superseded -- and over the generated rows, with the catalogue
as it is or stocking every place, no row lands in a state origin/main would not give
(tests/test_state_invariant.py).

ROUND 3 (29 Sep 2026). Both reviews of round 2 found the longer-name exception a road to
any state: a label is as often "Lot <n>, <estate>", and estates are named after places
anywhere ("Lot 3772, Redwood Park" on a Clyde North lot went to SA; "Lot 340, Boat
Harbour Beach" on a "CARINGBAH, NSW, 2229" lot to TAS). It now needs the row's own line
to open with the name and nothing else in the row to name another state, and it never
overrules a state origin/main gives (longer_name_stands, bound). Nor where the row's address
names a one-state place of another state after its street ("... Redhead Street, Jetsonville").

OVER MANY NIGHTS (round 3, 29 Sep 2026). The bound above is anchored to origin/main on the
stored columns -- and from the second night those are this step's own answers. Both reviews
of round 2 found rows it changed on a later night that origin/main's own nights never change:
rows round 1 correctly gives their page's state on a guessed suburb, moved by origin/main's
row-text check the first night a word of their line was stocked elsewhere (Verv 18833 to NSW
on its estate Balmain, Silkwood 9299 to VIC on its design Balwyn), and rows going VIC, blank,
VIC, blank ("Grange West 56 Werribee Titled 400"). A multi-night harness (scratch, cut down in
tests/test_state_multinight.py) runs both codes' real enrich for nine nights on their own
output, stocking every design, facade and estate word that is a place and then every place
any row writes, dropping sheets' page hints for a night and changing status words. On
c27f9dc it found 51 rows changing more than once, 33 going back and forth and 854 moved off
their page and their own reading by a name. The causes, each fixed where it is made:
  * a page and the row's own reading agreeing are two signals, and a place name elsewhere
    in the line does not move the row off them (page_and_guess_hold) -- bar the label's own
    "<estate> <locality>" pair, the 9568 catch;
  * guess_suburb weighs a reading by what pass 2 will really do with it (decide_row, one
    function for both), against origin/main's guess with the PAGE's state preferred, not the
    stored one, and a row pass 2 re-opens is guessed as a fresh row;
  * a file's vote is its rows' proof on the fresh row's guess, so a file's national status
    no longer follows its own rows' stored answers;
  * a hint missing for a night is not a page that disagrees (page_of), and a lone suburb match
    written while it was missing is re-resolved once it is back (a_page_now_disagrees);
  * a guessed suburb that is the row's own estate proves nothing against its page
    (the_estate_is_the_proof); a state origin/main's check writes is written with the
    check's signal, so it is not re-checked; the bound also admits origin/main's answer on
    the postcode column as tonight leaves it (a lot number this step clears);
  * a file-name state is one capitalised code in the file's name (state_code_in_filename),
    and a reading no longer leans on it.
Over both harness corpora (23,448 real rows plus 10,600 and 15,800 generated): 0 rows change
more than once or go back and forth, 0 are moved off their page and reading by a name, the
single-night bound holds every night, and tonight's 954 changes are unchanged.

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
from state_resolver import (TEXT_LOCALITY_SOURCE, _estate_key, disagreement,
                            file_is_national, label_tail_is_the_pair,
                            longer_name_of_the_line, normalise_state, own_state,
                            places_after_a_street, postcode_is_lot_number, postcode_stated,
                            reading_refuses, resolve_state, state_from_postcode,
                            states_written_in, suburb_in_row_text, text_locality_elsewhere)

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


def row_text(r) -> tuple:
    """The row's own line and label, as state_resolver's `row_text` takes them."""
    keys = r.keys()
    return tuple(str(r[k] or "") for k in ("source_text", "lot_address") if k in keys)


def rests_on_its_lot_number(r, geo=None) -> bool:
    """A stored state decided by a postcode that is the row's own lot number.

    Coleen, 28 Sep 2026: a QLD search offered two "Jubilee, QLD" lots that are the Jubilee
    estate in Wyndham Vale, VIC. Their line reads "...SS West 4327 Jubilee Wyndhamvale...",
    4327 is the lot, and it was stored as the postcode too; own_state then proved QLD by
    "suburb + postcode" over the VIC page. Such a state is SETTLED by the rule below, so
    fixing state_resolver alone would never reach the rows already written -- the same
    trap as the Hudson rows the hint-only rule exists for. On the 28 Sep nightly 18 live
    rows are in this position and 12 are in the wrong state.

    The lot counts whether or not the lot_number column holds it (29 Sep 2026): the number
    opening the row's own line is its lot too ("2120 Stage 21 ..." -- AVIA 2234, whose
    lot_number is empty). See state_resolver.postcode_opens_the_row.
    """
    return (cites_a_postcode(r["state_source"])
            and postcode_is_lot_number(r["postcode"], r["lot_number"], row_text(r), geo))


# Signals whose state was decided with the row's SUBURB -- the column, or on a row with a
# blank column the guess enrich makes from its label (suburb_in_row_text).
_SUBURB_SIGNALS = ("suburb", "listing locality")


def rests_on_a_withdrawn_guess(r, suburb, mine, from_column, geo) -> bool:
    """A settled state that rested on a suburb GUESSED from the row's label, which the
    guess made tonight no longer supports.

    29 Sep 2026. Verv 11494 ("5629 The Grove, Tarneit", NSW by 'listing locality') and
    Silkwood 11510 ("... New Beith Round Mountain Estate ...", NSW the same way) were
    settled on a guess -- "The Grove", "Round Mountain" -- that the guess no longer makes
    (it now reads Tarneit and New Beith, see state_resolver.suburb_in_row_text), and
    Hattan 14913/14914 on "Verona", which it now refuses as a design. A settled state is
    never looked at again, so fixing the guess alone would never have reached them: stale,
    not wrong when written, and the refresh path is what has to change. So a state is
    re-resolved when all of these hold:
      * the suburb column is blank, so the only suburb the state can have used is a guess;
      * its signal names the suburb ('suburb ...', 'listing locality ...');
      * tonight's guess is not a locality of that state, and what the row proves tonight
        (own_state) is not that state either.
    A guess that still names a place in the stored state leaves it alone, which is what
    keeps this from re-resolving every 'suburb' state -- the change that blanked 64 correct
    ones (see the settled-state check in main()).
    """
    if from_column:
        return False
    parts = [p.strip() for p in str(r["state_source"] or "").split("+")]
    if not any(p.startswith(_SUBURB_SIGNALS) for p in parts):
        return False
    stored = (r["state"] or "").strip().upper()
    if mine == stored:
        return False
    if suburb:
        try:
            if stored in geo.states_for_suburb(suburb):
                return False
        except Exception:                                             # noqa: BLE001
            return False
    return True


def overrode_the_page_on_a_withdrawn_proof(r, mine) -> bool:
    """A settled state the row's OWN proof took over its page ('listing text (over QLD)'),
    when tonight the row proves nothing at all.

    Review, 29 Sep 2026. Creation Homes' Boondall lots "21 The Grove Boondall ..." (992,
    993, 4752, 7893) were proved NSW by the street name 'The Grove' in their suburb column
    and written 'listing text (over QLD)' against their QLD page. own_state no longer takes
    a cell that is the row's own street (state_resolver.suburb_cell_is_the_street), but a
    settled state is never looked at again -- so the overrule of the page would outlive its
    only evidence. Re-resolved, the page is back. Only when own_state proves NOTHING: of
    the 24 plain 'listing text' states it no longer proves on the 29 Sep copy, all are
    right (Aberglasslyn, Clyde, Flagstone ...), written from cells an older extractor
    filled, and a proof of another state would be a different question. Measured on the
    29 Sep copy: 4 of the 80 'listing text (over ...)' states, exactly those four.
    """
    return str(r["state_source"] or "").startswith("listing text (over ") and not mine


def state_from_filename(url: str) -> str:
    """'...?dn=NSW%20Dual%20Jul.xlsx' -> 'NSW'  /  'DUAL QLD JUL.xlsx' -> 'QLD'.

    origin/main's reading, kept as it is for origin_main_state: any of the eight codes, in
    any case, anywhere in the dn= name or -- with no dn= -- the whole URL. This step's own
    decisions use state_code_in_filename."""
    if not url:
        return ""
    name = unquote(url).split("dn=")[-1] if "dn=" in url else unquote(url)
    m = STATE_RE.search(name)
    return m.group(1).upper() if m else ""


_STANDALONE_CODE = re.compile(r"(?<![A-Za-z])(VIC|NSW|QLD|SA|WA|NT|ACT|TAS)(?![A-Za-z])")


def state_code_in_filename(url: str) -> str:
    """The state a file's own NAME states, as this step trusts it: one state code, in
    capitals and standing on its own, in the dn= name or the last part of the path
    ("NSW Dual SEP NEW (1).xlsx", "DUAL QLD SEP.xlsx", "VIC SQ Price List 23.03.2026.pdf",
    an e-mail subject "... Fraser Rise VIC"); '' otherwise, or when it names two.

    29 Sep 2026, round 3, decided by measurement on the 29 Sep copy. A file-name state is
    not as trustworthy as the page: where the row proves its own state, the page agrees on
    2,697 of 2,877 rows (93.7%), the file name on 59 of 67 (88.1% -- the 8 are Murray Bridge
    SA lots in 'VIC Regional Jun (2).xlsx'). Every file-name state origin/main reads on
    that copy is such a code already (12 files), so tonight nothing changes; what the rule
    drops is a code read case-blind anywhere in a URL -- "Act", "wa", "sa" in a word or a
    path -- and a name giving two states ("QLD NSW Combined.xlsx"). A review's generated
    rows took 705 states off a file name alone. The file name stays a LAST resort and an
    allowed answer of pass 2's bound (a state the file claims for every row in it, like the
    page); a reading no longer leans on it (guess_suburb)."""
    if not url:
        return ""
    u = unquote(url)
    name = u.split("dn=")[-1] if "dn=" in u else u.rsplit("/", 1)[-1]
    found = {m.group(1) for m in _STANDALONE_CODE.finditer(name)}
    return found.pop() if len(found) == 1 else ""


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

    'e-agent page (conflicting signals)' is NOT one of these (review, 29 Sep 2026): the page
    won against the suburb, so the state rests on the page, and a bare word the guess read
    from the line is no reason to move it -- "2-Part Detached SS West 4327 Jubilee
    Wyndhamvale" (Verv 19094, VIC, Jubilee a QLD estate name) would go to Queensland the
    first night Jubilee QLD is stocked. Exactly origin/main's two signals: round 1 also
    looked again at such a state when the guess was the label's "X, Locality" word
    (page_overrode_the_label), and the review of it moved a Diggers Rest VIC lot to NSW
    through it ("Lot 518, Davis Vineyard": Vineyard, its estate's word) and a Wyndham Vale
    lot to QLD ("Lot 5, Wyndham Vale Jubilee"). The one row it was for, 9700 ("Lot 27102,
    Windermere Mambourin" off the QLD page), reaches VIC the way origin/main took it: its
    guess is Windermere again (guess_suburb), and this check reads Mambourin in its line.
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


def overruled_state(r, suburb, state, geo, evidence, page_backs=True):
    """(other_state, evidence text, how, name) when the row's own text overrules `state`,
    else None. `how` is 'text' or 'estate', `name` the place or estate named (29 Sep 2026,
    round 3: enrich's check tells a place name in the line from the estate's stock --
    page_and_guess_hold).

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
    page = page_of(r) if page_backs else ""
    hit = text_locality_elsewhere(text, state, geo, stocked, estate=r["estate_name"],
                                  suburb=suburb, page=page or "")
    if hit:
        return hit[1], f"text names {hit[0]!r}: {_excerpt(text, hit[0])!r}", "text", hit[0]
    estate = (r["estate_name"] or "").strip().lower()
    builder = (r["builder_name"] or "").strip().lower()
    if builder and estate and suburb and suburb.strip().lower() == estate:
        key = (builder, estate)
        where = {st for st, ids in estates.get(key, {}).items() if ids - {r["id"]}}
        if len(where) == 1 and state not in where:
            other = next(iter(where))
            return (other, f"estate {r['estate_name']!r} is stocked in {other} by the same "
                           f"builder", "estate", r["estate_name"])
    return None


def page_and_guess_hold(r, state, other, name, reading, guess, geo) -> bool:
    """True when the row's page states `state` and the suburb the row's own words are read
    as is a place of `state`: then `name`, a place of the state `other` somewhere else in
    its line, is no reason to move it there (29 Sep 2026, round 3). The row's own guessed
    suburb is `reading` (state_resolver.suburb_in_row_text's, the page's state preferred)
    where there is one, else `guess`, the suburb enrich guessed.

    Both reviews of round 2 found rows this branch correctly gives their page's state
    moved off it on a LATER night, by the row-text check alone, the first night some word of
    their line was stocked in another state: Verv 18833 ("... 817 Balmain Donnybrook ...",
    VIC page, read Donnybrook) to NSW once one Balmain NSW listing arrived -- Balmain is its
    estate; Silkwood 9299 ("... Morayfield North Harbour Estate ... Balwyn 192 ...", QLD
    page, read Morayfield) to VIC once Balwyn VIC did -- Balwyn is its design; Verv 16203
    ("613 Little Springs Deanside") to WA on Springs. origin/main never moved them: their
    sheets were national, so they had no state to move. Round 1 took the sheets out of
    'national' and left the rows on 'suburb + e-agent page', the signal origin/main's check
    reads as "a suburb match alone" because the suburb is a guess -- but here the page and
    the row's own reading are two signals, and they agree. A third word agreeing with
    neither is weaker than both: a design, an estate, a facade named after a place anywhere.
    The multi-night harness (tests/test_state_multinight.py keeps a reduced one) stocks
    every design, facade and estate word of the catalogue that is a place: 854 generated
    rows flipped this way on c27f9dc.

    Not when the label's own "X, <A> <B>" part is nothing but the reading and `name`, the
    reading is a place of `other` too (state_resolver.label_tail_is_the_pair), and the
    row's line, where it is not the label itself, does not write `name`: the label then
    names an estate and a locality, and it was the page that picked one -- the page alone
    backs `state`, and the check moves the row as origin/main's does. That is 9568 ("Lot
    27102, Windermere Mambourin" off a QLD page, read Windermere: NSW/QLD/TAS/VIC, the line
    "Lot 27102 Bounty X Inv/OO 400 sqm ..."), which origin/main moved to VIC on Mambourin,
    and the Armstrong Creek lots 9565/9566 ("Lot 11950, Warralily Armstrong Creek"): a QLD
    sheet whose rows are all Victorian. A first cut let every such "place of both states"
    reading through, wherever the other name stood; the harness found it moving rows off a
    right page on a design deep in the line ("... Palmerston Wellington Point Estate ...
    Rochedale South 192 ..." off the NT page to QLD, Palmerston being a QLD place too), a
    Verv "<lot> <estate> <suburb>" row whose suburb is a place of the estate's state too
    ("1795 Rockbank Brighton" off the TAS page) to VIC, and Creation Homes' "<street>,
    <suburb> <design>" label over a line that writes the design with its size ("50 Zoe
    Place Brighton Girgarre East 226 - Caolina") to VIC. A row that STATES another state --
    its code, a postcode written as one -- or names it in its suburb column is decided by
    own_state and resolve_state, not by this check; and the nightly replay still warns
    about the rest, as origin/main's does.

    The same holds against the check's second way, a guessed suburb that is the row's
    estate stocked by the same builder in one other state (overruled_state): it is not a
    postcode, a state code or the suburb column either, and the harness caught it taking
    "Lot 1259, Sebastopol" off its NSW page, read Sebastopol, to QLD.

    The page is page_of's: a hint missing for a night is not a page that disagrees, so one
    night's missing hint cannot open the row to the check and leave it moved for good once
    the hint is back.
    """
    own = reading or guess
    if not (state and own and page_of(r) == state and state in geo.states_for_suburb(own)):
        return False
    label = r["lot_address"] or ""
    line = r["source_text"] if "source_text" in r.keys() else ""
    return not (other in geo.states_for_suburb(own)
                and label_tail_is_the_pair(label, own, name)
                and (not line or line.strip() == label.strip()
                     or not _writes(line, name)))


def _writes(text, name) -> bool:
    """True when `text` writes the place name `name` as whole words."""
    words = [re.escape(w) for w in str(name or "").split()]
    return bool(words) and re.search(r"\b" + r"\W+".join(words) + r"\b", str(text or ""),
                                     re.I) is not None


def _real_postcode_state(r, geo) -> str:
    """The state of the row's own postcode, when the row writes it as one (state_resolver.
    postcode_stated) and it is not its lot or some other number
    (state_resolver.postcode_is_lot_number); '' otherwise."""
    pc = r["postcode"]
    if not pc or postcode_is_lot_number(pc, r["lot_number"], row_text(r), geo):
        return ""
    return postcode_stated(pc, row_text(r), geo)


def longer_name_stands(r, reading, origin, geo, stocked) -> str:
    """The state round 1's one correction that is neither a page nor a postcode may give a
    row -- the longer place name around origin/main's guess ("Wyndham Vale" for Wyndham,
    "Clyde North" for Clyde, "Springvale South" for Springvale: 23 Hattan email rows) --
    or '' wherever the row cannot tell, and origin/main's answer stands.

    Round 3, 29 Sep 2026. Round 2 let any longer name the LABEL wrote through its bound on
    a row with no page, whatever its state, and both reviews found it a road to states
    origin/main never gives, over the row's own words: "Lot 3772, Redwood Park" (the
    label's estate) sent a Clyde North lot to SA over origin/main's right VIC; "Lot 340,
    Boat Harbour Beach" a "CARINGBAH, NSW, 2229" lot to TAS; "..., DIGGERS REST 3427,
    Verona Sands" a Diggers Rest lot to TAS; "Lot 5, Capri DG Corner Murray Bridge" a
    "Dual Key QLD ... 4244" lot to SA -- 13 and 23 wrong moves in the reviews' corpora,
    and 542 of 695 when the label's estate was a random place. What the 23 rows have, and
    those do not, is now required of every one:
      * no page state (as before);
      * the row's own line opens with the name, after nothing but its availability
        ("Available ..."), with no street or estate word after it, and it is not the
        row's estate (state_resolver.longer_name_of_the_line);
      * nothing else the row says names another state: no state code or name, no
        postcode written as one (states_written_in, _real_postcode_state), no state in
        its file's name, and no one-state place of another state written straight after
        a street -- its address's locality (state_resolver.places_after_a_street; round 3
        review, 29 Sep 2026: "Available Regents Park Dc Lot 7, Redhead Street,
        Jetsonville ..." went to NSW over the TAS place its address names);
      * and the row-text check, asked as origin/main asks it, finds no stocked place of
        another state in the line ("Available Truganina ..." under the label "Lot 1364,
        Murray Bridge").
    The caller also takes it only where origin/main's answer is blank, as on all 23: a
    state origin/main gives is never overruled by it.
    """
    keys = r.keys()
    if page_of(r):
        return ""
    line = r["source_text"] if "source_text" in keys else ""
    state = longer_name_of_the_line(reading, origin, line, geo, r["estate_name"])
    if not state:
        return ""
    stated = (states_written_in(row_text(r), geo) | places_after_a_street(row_text(r), geo)
              | {_real_postcode_state(r, geo), state_from_filename(r["source_url"] or "")})
    if stated - {"", state}:
        return ""
    text = " ".join(row_text(r))
    if text_locality_elsewhere(text, state, geo, stocked, estate=r["estate_name"]):
        return ""
    return state


def bound(r, origin_state, longer, geo) -> set:
    """The states pass 2 may leave in a row (round 2, 29 Sep 2026 -- the acceptance bar, see
    tests/test_state_invariant.py): origin/main's own answer `origin_state`, none, the
    row's page (page_of) or file-name state (state_code_in_filename), its stated
    postcode's -- and `longer`, the state of round 1's longer name (longer_name_stands),
    only where origin/main's answer is blank. Round 3: never over a state origin/main
    gives ("Lot 3772, Redwood Park" on a Clyde North lot that origin/main's row-text check
    files VIC went to SA)."""
    page = page_of(r)
    allowed = {origin_state or "", "", page,
               state_code_in_filename(r["source_url"] or ""), _real_postcode_state(r, geo)}
    if longer and not origin_state:
        allowed.add(longer)
    return allowed


def page_of(r) -> str:
    """The page state a row is read with: its E-Agent page, or -- when tonight's row carries
    none -- the state its stored signal says the page and another signal agreed on
    ('suburb + e-agent page', 'postcode + e-agent page').

    29 Sep 2026, round 3. A hint missing for a night is not a page that disagrees: read
    without it, a Verv row off the WA page ("2-Part Detached SS West 816 Bohnock Lancelin
    ...") was guessed Bohnock instead of Lancelin, the row-text check moved it to NSW on
    that word, and that state, being the row-text check's, stood for good once the hint
    was back -- 494 of the multi-night harness's page-backed rows, where origin/main's own
    lineage kept them blank. Enrich never writes the hint, so the stored state it falls
    back on was decided with the page present; and the harvest writes it on INSERT only
    (database.record_building), so a stored row loses it only if some other path ever
    rewrites the column. The row-text check, pass 2's bound (i), guess_suburb and the
    reading read the page this way; resolve_state is given the column itself (decide_row),
    and origin_main_state reads the column, as origin/main does."""
    keys = r.keys()
    page = normalise_state(r["source_state_hint"] if "source_state_hint" in keys else "")
    if page:
        return page
    stored = normalise_state(r["state"])
    parts = [p.strip() for p in str(r["state_source"] or "").split("+")]
    # Not the page alone ('e-agent page', '... (conflicting signals)'): a state nothing but
    # the page ever backed is re-resolved without it, as origin/main re-resolves it -- the
    # row's own postcode then decides ("Lot 158 Unit B511, 10-14 Hinkler Av, CARINGBAH,
    # NSW, 2229" stored VIC by 'e-agent page' goes to NSW; round 2's review plant).
    if stored and "e-agent page" in parts and len(parts) > 1:
        return stored
    return ""


def a_page_now_disagrees(r) -> bool:
    """A settled state resting on a suburb match alone ('suburb') on a row whose page now
    names another state (29 Sep 2026, round 3).

    resolve_state never writes 'suburb' against a page -- a one-state suburb and a page
    of another state are "conflicting signals", and the page wins -- so such a state was
    written on a night the row had no page, and its page is back: a sheet read once without
    its page hint moved the Crestwood Cranbourne lots 1841-1844 (VIC page, 'Crestwood' in
    the suburb column) to NSW, and the Ballina Crescent lots 433/434 likewise, and being
    settled they stayed there after the hint returned, in origin/main too. Re-resolved,
    the row gets what it got before the hint went missing. None of the 29 Sep copy's 486
    'suburb' states is in this position."""
    keys = r.keys()
    page = normalise_state(r["source_state_hint"] if "source_state_hint" in keys else "")
    state = normalise_state(r["state"])
    return (str(r["state_source"] or "").strip() == "suburb" and bool(page) and bool(state)
            and page != state)


def the_estate_is_the_proof(r, suburb, mine) -> bool:
    """True when a GUESSED suburb that is the row's own estate name would prove a state
    against the row's page (29 Sep 2026, round 3).

    own_state never reads the estate column as a place ("an estate name is marketing text
    by definition"), but a guess read off the label can be that very name, and own_state
    then takes it as a suburb cell: "5545 Nautical Circuit, The Grove", estate The Grove, off
    the VIC page, proved NSW by 'listing locality' on the two-word name alone and went to
    NSW over its page -- a review's generated row, which went TAS, VIC, NSW over three
    nights. The guess still votes as a suburb (resolve_state weighs it against the page);
    it proves nothing by itself. Where it agrees with the page it is kept: Innes Park
    12373-12375 ("Innes Park Available 86 Innes Park Estate ...", QLD page) are the only
    three rows of the 29 Sep copy it would touch, and they keep 'listing text'."""
    page = page_of(r)
    estate = _estate_key(r["estate_name"])
    return bool(page and mine and mine != page and estate and suburb
                and _estate_key(suburb) == estate)


def the_reading(r, geo, stocked):
    """state_resolver.suburb_in_row_text's reading of a blank-suburb row's label: the page's
    state preferred (page_of), the row's estate refused (guess_suburb,
    page_and_guess_hold)."""
    return suburb_in_row_text(r["lot_address"] or "", geo, prefer=page_of(r),
                              estate=r["estate_name"], stocked=stocked)


def guess_suburb(r, geo, stocked, decide, origin=None, reading=False, actual=None):
    """The suburb a row with a blank suburb column is read as: state_resolver.
    suburb_in_row_text's reading, or origin/main's guess (geo.find_suburb_in_text with the
    row's stored state) wherever the reading would take the row somewhere origin/main's
    would not.

    Round 2, 29 Sep 2026. The reading is right far more often: over 25,000 generated rows
    (the differential harness, cut down in tests/test_state_invariant.py) round 1's put
    1,021 fewer rows in a wrong state than origin/main's guess. But review found new ways
    for it to be wrong that origin/main never had -- "Lot 5, Wyndham Vale Jubilee" read as
    Jubilee, QLD; "77 Brooklyn Park Rise" off the NSW page as Brooklyn Park, SA, which is
    also a proof that overrules the page and casts a national-file vote -- and the harness
    found 1,192 rows it put in a state origin/main would not give. A reading is judged by
    where it takes the row, so both are carried through what the row would get from them
    (`decide`: own_state, resolve_state, the row-text check and the file-name hint ->
    (state, what the row proves, whether it is settled)), and the reading is used only
    when:
      * every state it gives the row -- the state, and the proof that votes on the file --
        is one origin/main's guess gives it too, or the row's own page state, its file
        name's, its real postcode's, or none; and, when it gives no state, origin/main's
        gave none either, or gave the guess's own state and the reading is sure that guess
        is no place here (state_resolver.reading_refuses: "Verona SG", a month, a street
        word -- but not "Grange West 56 Werribee", where the reading took Grange and
        origin/main Werribee);
      * or origin/main's guess gives the row no state, and the reading is the longer name
        the row's own line opens with around that guess, and nothing else in the row names
        another state (longer_name_stands -- round 3): "Clyde North" for Clyde, "Wyndham
        Vale" for Wyndham -- round 1's 23 Hattan email rows, the one correction that is
        neither.
    Otherwise origin/main's guess is used, and the row lands where origin/main put it.

    THE SAME VIEW EVERY NIGHT (round 3, 29 Sep 2026). `origin` is origin/main's guess
    with the row's PAGE state preferred (page_of) -- not its stored state, which is this
    step's own answer from the night before: a guess that prefers it read the row one way
    on the night the answer was written and another way every night after. "5545 Nautical
    Circuit, The Grove" off the VIC page went TAS, VIC, NSW; a Verv row off the WA page read
    without its hint went to NSW on "Bohnock" for good. `decide` is pass 2's own decision
    (decide_row), so a reading is weighed by what the row really gets from it -- round 2's
    assumed a settled row keeps its state, and pass 2 then re-opened it on the reading:
    "Grange West 56 Werribee Titled 400" went VIC, blank, VIC, blank. origin/main's own
    guess (stored state preferred) is still what the bound in pass 2 holds the answer to,
    and `actual` passes it for the longer-name exception, which is taken around it or
    around `origin` ("Glen Waverley Lot 7035 ...": no longer name around Glen Waverley,
    one around Waverley). A reading no longer leans on the file's name.
    """
    text = r["lot_address"] or ""
    page = page_of(r)
    if origin is None:
        origin = geo.find_suburb_in_text(text, (r["state"] or "").strip().upper())
    if reading is False:          # not read by the caller
        reading = the_reading(r, geo, stocked)
    if (reading or "").lower() == (origin or "").lower():
        return reading or origin
    old_state, old_proof, settled = decide(r, origin)
    new_state, new_proof, _settled = decide(r, reading)
    # Not the file's name (round 3, 29 Sep 2026 -- state_code_in_filename): a reading that
    # lands on the state a file's name claims is still a reading origin/main's guess does
    # not make ("Available Purdeet ... Mackay SG Corner | Wreck Bay 16.34 ..." in "ACT
    # Dual.xlsx" went to ACT on the design Wreck Bay over its line's own Purdeet, VIC).
    allowed = {old_state, old_proof, "", page, _real_postcode_state(r, geo)}
    # A reading that gives no state may stand when origin/main's gave none either, or the
    # same one to a row pass 2 resolves again (a page that beat a design: only the signal
    # moves -- a settled row would be re-resolved on the lost guess), or when origin/main's is
    # its guess's own state and the reading is sure that guess is no place here ("Verona SG
    # Corner"). Not when origin/main's state came from the row-text check naming another
    # place in the line ("Lot 116, Vesper SG Langham": Vesper, VIC, a design, moved to NSW
    # on "Hunterview" -- the design's own state never stood).
    refused = (old_state in geo.states_for_suburb(origin or "")
               and reading_refuses(text, origin, geo, stocked))
    withdrawn = not old_state or (new_state == old_state and not settled) or refused
    if reading and new_state in allowed and new_proof in allowed:
        if new_state or withdrawn:
            return reading
        return origin
    # Around origin/main's own guess or the fresh row's (round 3, 29 Sep 2026): stored VIC,
    # origin/main's guess of "Glen Waverley Lot 7035 Hume Coastal" is Glen Waverley itself
    # and there is no longer name around it; stored blank it is Waverley and there is. The
    # row went VIC, blank, VIC, blank. Its own line opens with Glen Waverley either way.
    longer = ((longer_name_stands(r, reading, actual or origin, geo, stocked)
               or (actual and longer_name_stands(r, reading, origin, geo, stocked)))
              if not old_state else "")
    if longer and {new_state, new_proof} <= allowed | {longer}:
        return reading
    # The reading goes where origin/main's would not, or finds nothing. origin/main's guess
    # then -- unless the row is sure that guess is no place here and origin/main's state is
    # its own: no guess at all, which may leave the row blank but never names a state
    # ("LOT 1030 POUND RD, CLYDE NORTH - 7 STAR": not North Star, NSW).
    return None if (withdrawn if not reading else refused) else origin


def main(argv=None):
    # 29 Sep 2026 (round 3): the report quotes rows' own words, and a row holding a
    # character the console's code page lacks (U+FF0C, a full-width comma) crashed a
    # by-hand run AFTER the commit, exit 1. run_daily launches this with -X utf8, so the
    # nightly never met it; a report line is not worth a failed step either way.
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
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
    # Settled states that rested on a guessed suburb tonight's guess no longer supports
    # (rests_on_a_withdrawn_guess), or on a proof over the page that the row no longer
    # gives (overrode_the_page_on_a_withdrawn_proof), or on a suburb match alone that the
    # row's page now contradicts (a_page_now_disagrees), each re-resolved.
    withdrawn_guesses: List[tuple] = []
    # Rows whose answer tonight was a state origin/main would not give, and so were given
    # origin/main's answer instead (round 2 -- see the bound in pass 2).
    held: List[tuple] = []

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
    # What each row votes on its file, (suburb, state, why): its proof on the fresh row's
    # guess (pass 1).
    votes: Dict[int, tuple] = {}
    # origin/main's reading of each row (origin_main_state): its suburb, proof and votes.
    origin: Dict[int, tuple] = {}
    per_file_o: Dict[str, set] = {}
    # origin/main's reading with a lot number out of the postcode column (clears_a_lot_number)
    origin_cleared: Dict[int, tuple] = {}
    per_file_o2: Dict[str, set] = {}

    def clears_a_lot_number(r):
        """True when pass 2 takes this live row's postcode out of the column tonight
        because it is the row's own lot number by this step's rule and not by origin/main's
        (state_resolver.postcode_is_lot_number with the row's text)."""
        return (bool(r["postcode"]) and not r["superseded_by"]
                and not postcode_is_lot_number(r["postcode"], r["lot_number"])
                and postcode_is_lot_number(r["postcode"], r["lot_number"], row_text(r), geo))
    # The state of the longer name the row's line opens with around origin/main's guess, on
    # a row with no page state and nothing else naming a state (longer_name_stands, round
    # 1's correction C) -- otherwise empty.
    longer: Dict[int, str] = {}
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

    check_errors: List[str] = []
    # suburb_in_row_text's reading of each blank-suburb row, kept for the row-text check
    # (page_and_guess_hold) whether or not guess_suburb uses it.
    readings: Dict[int, str] = {}

    def _check(r, suburb, from_col, state, signal, as_origin_main=False):
        """The row-text check's answer for `state`, or None when the check is off, the row
        is superseded or the state does not rest on a suburb match. Raises what
        overruled_state raises. `as_origin_main`: without the one silence round 2 adds (a
        design on a row its page backs -- text_locality_elsewhere), as origin/main asks it.

        Round 3 (29 Sep 2026): nor, unless asked as origin/main, a place name in the line
        over a guessed suburb its page agrees with (page_and_guess_hold) -- the reviews'
        lineage flips, 18833 Balmain, 9299 Balwyn, 16203 Springs."""
        if not (overrule_on and state and not r["superseded_by"]
                and rests_on_a_suburb_match(signal, from_col)):
            return None
        hit = overruled_state(r, suburb, state, geo, evidence, page_backs=not as_origin_main)
        if (hit and not as_origin_main and not from_col
                and page_and_guess_hold(r, state, hit[0], hit[3], readings.get(r["id"]),
                                        suburb, geo)):
            return None
        return hit

    def text_overrules(r, suburb, state, signal):
        """(other_state, evidence) when this row's own text overrules a suburb-match state.

        None when the check is off, the row is superseded, the state does not rest on a
        suburb match, or the check itself raised -- in which case the row keeps today's
        answer and the run is reported degraded.
        """
        try:
            return _check(r, suburb, from_column[r["id"]], state, signal)
        except Exception as exc:                                       # noqa: BLE001
            if not check_errors:
                traceback.print_exc()
            check_errors.append(f"id {r['id']}: {exc!r}")
            return None

    def _quiet_check(r, suburb, state, signal):
        """text_overrules for decide(): pass 2 asks the same and reports any error."""
        try:
            return _check(r, suburb, from_column[r["id"]], state, signal)
        except Exception:                                              # noqa: BLE001
            return None

    def proof(r, suburb, from_col):
        """(state, why): what the row proves on `suburb` (own_state) -- nothing, from a
        guessed suburb that is its own estate naming a state against its page
        (the_estate_is_the_proof). Pass 1 and decide() both ask it."""
        mine, why = own_state(postcode=r["postcode"], suburb=suburb,
                              estate=r["estate_name"], address=r["lot_address"], geo=geo,
                              lot_number=r["lot_number"], row_text=row_text(r),
                              street_cell=from_col)
        if not from_col and the_estate_is_the_proof(r, suburb, mine):
            mine, why = own_state(postcode=r["postcode"], suburb="",
                                  estate=r["estate_name"], address=r["lot_address"],
                                  geo=geo, lot_number=r["lot_number"],
                                  row_text=row_text(r), street_cell=False)
        return mine, why

    def decide_row(r, suburb, mine, why, national_file, quiet=False):
        """(state, signal, hit, settled, how): what pass 2 leaves in a row whose suburb is
        `suburb` and whose own proof is (mine, why), before the bound. `how` names the rule
        that re-opened a settled state: 'reopened' (its lot number), 'reguessed' (a guess
        withdrawn), 'reproved' (a proof over its page withdrawn), or ''.

        ONE function for pass 2 and for guess_suburb's weighing of a reading (decide, below)
        -- 29 Sep 2026, round 3. decide() used to assume a settled row keeps its stored
        state, while pass 2 re-opens it when tonight's guess no longer names a place in it
        (rests_on_a_withdrawn_guess). So guess_suburb took a reading on the promise that
        the state stays, and pass 2 then re-opened the row on that very reading: "Grange West
        56 Werribee Titled 400" (no page, blank suburb) went VIC, blank, VIC, blank on
        successive nights, and a review found 32 of 5,000 fresh two-place rows doing the
        same. Weighed by what pass 2 will really do, the reading that would re-open the row
        to a blank is seen for what it is, and the row keeps the state it was given."""
        rid = r["id"]
        state = (r["state"] or "").strip().upper()
        check = _quiet_check if quiet else text_overrules
        settled = bool(state) and bool(STATE_RE.fullmatch(state or "")) \
            and (r["state_source"] or "") not in HINT_ONLY_SIGNALS
        how = ""
        if settled and rests_on_its_lot_number(r, geo):
            settled, how = False, "reopened"
        if settled and a_page_now_disagrees(r):
            settled, how = False, "repaged"
        hit = None
        new_state, signal = state, r["state_source"]
        if settled:
            hit = check(r, suburb, state, r["state_source"])
            if hit:
                new_state, signal = hit[0], TEXT_LOCALITY_SOURCE
        if settled and not hit and rests_on_a_withdrawn_guess(r, suburb, mine,
                                                               from_column[rid], geo):
            settled, how = False, "reguessed"
        if settled and not hit and overrode_the_page_on_a_withdrawn_proof(r, mine):
            settled, how = False, "reproved"
        if not settled:
            if national_file:
                new_state, signal = mine, why
            else:
                # The hint itself, not page_of: a row resolved again is resolved on what it
                # carries tonight. Resolved on a stored page-agreeing state as its page, a
                # re-opened row came out 'e-agent page' on that state alone -- a signal
                # page_of does not carry forward -- and went blank the next night (round 3).
                page = r["source_state_hint"] if "source_state_hint" in r.keys() else ""
                new_state, signal = resolve_state(postcode=r["postcode"], suburb=suburb,
                                                  page_state=page, geo=geo,
                                                  listing_state=mine,
                                                  lot_number=r["lot_number"],
                                                  row_text=row_text(r))
                hit = check(r, suburb, new_state, signal)
                if hit:
                    new_state, signal = hit[0], TEXT_LOCALITY_SOURCE
                if not new_state:                  # last resort: the file's own name
                    from_file = state_code_in_filename(r["source_url"] or "")
                    if from_file:
                        new_state, signal = from_file, "stocklist file name"
        return new_state or "", signal, hit, settled, how

    def reopens(r, suburb):
        """True when pass 2 re-opens this settled row on `suburb` (decide_row's `how`)."""
        if not (normalise_state(r["state"])
                and (r["state_source"] or "") not in HINT_ONLY_SIGNALS):
            return False
        mine, why = proof(r, suburb, False)
        try:
            return bool(decide_row(r, suburb, mine, why, False, quiet=True)[4])
        except Exception:                                              # noqa: BLE001
            return False

    def decide(r, suburb):
        """(state, proof, settled): what pass 2 gives a row, in a file that is not national,
        whose suburb is `suburb`, what the row then proves, and whether it stays settled --
        how guess_suburb weighs a reading. Pass 2's own decision (decide_row), so a reading
        is weighed by what the row will really get from it."""
        mine, why = proof(r, suburb, False)
        try:
            state, _signal, _hit, settled, _how = decide_row(r, suburb, mine, why, False,
                                                             quiet=True)
        except Exception:                                              # noqa: BLE001
            return "", mine or "", False   # pass 2 asks the same and reports the error
        return state, mine or "", settled

    for r in rows:
        rid = r["id"]
        suburb = (r["suburb"] or "").strip()
        from_column[rid] = bool(suburb)
        origin_suburb = suburb
        longer[rid] = ""
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
            #
            # THE GUESS ITSELF (29 Sep 2026) is state_resolver.suburb_in_row_text, not
            # geo.find_suburb_in_text: that took the estate ("Round Mountain Estate"), the
            # estate before the comma ("5629 The Grove, Tarneit") and the design ("Verona
            # SG"), and it preferred the row's STORED state -- which a wrong guess had
            # written on an earlier night, so the wrong guess preferred itself for good.
            # The page's state is preferred instead; enrich never writes that. And the
            # reading is used only where it takes the row nowhere origin/main's guess
            # would not (round 2 -- see guess_suburb).
            stored = (r["state"] or "").strip().upper()
            origin_suburb = geo.find_suburb_in_text(r["lot_address"] or "", stored) or ""
            # origin/main's guess with the page's state preferred, not the stored one -- the
            # view guess_suburb weighs a reading against on every night alike (round 3)
            ref = geo.find_suburb_in_text(r["lot_address"] or "", page_of(r)) or ""
            readings[rid] = the_reading(r, geo, evidence[0])
            found = guess_suburb(r, geo, evidence[0], decide, ref, readings[rid],
                                 origin_suburb)
            # A settled row pass 2 will re-open on this guess is resolved as a fresh row, so
            # it is guessed as one (round 3, 29 Sep 2026): weighed as settled, the reading
            # that re-opens it looks like a way off its stored state, origin/main's answer,
            # and is dropped -- the row goes blank, and the next night, fresh, takes the
            # reading after all. "Lot 865 Verona DG Aintree" (stored NSW on the design) went
            # NSW, blank, VIC, and then NSW again on the design once Verona was stocked.
            fresh_row = dict(r)
            fresh_row.update(state=None, state_source=None)
            fresh_guess = found
            if normalise_state(r["state"]) and (r["state_source"] or "") not in \
                    HINT_ONLY_SIGNALS:
                fresh_guess = guess_suburb(fresh_row, geo, evidence[0], decide, ref,
                                           readings[rid], origin_suburb)
            if reopens(r, found):
                found = fresh_guess
            if found:
                suburb = found
                if found.lower() != origin_suburb.lower():
                    longer[rid] = longer_name_stands(r, found, origin_suburb, geo,
                                                     evidence[0])
            # THE VOTE IS THE FRESH ROW'S (round 3, 29 Sep 2026): what a blank-suburb row
            # proves about its file is read off the guess a row with no stored state gets
            # -- round 2's guess_suburb weighs a reading by where it takes the row, which
            # depends on the state this step stored the night before, and that let a
            # file's votes follow its own rows' answers. "NSW Dual SEP.xlsx" (a generated
            # sheet of ten) was national one night, not the next and national again: two
            # settled rows proved their states only while settled, and the other eight went
            # blank, NSW by the file's name, blank.
            vote, vote_why = proof(r, fresh_guess or "", False)
            vote_sub = fresh_guess or ""
        state, why = proof(r, suburb, from_column[rid])
        proven[rid] = (suburb, state, why)
        if from_column[rid]:
            vote, vote_why, vote_sub = state, why, suburb
        # And what origin/main 31d13ef reads in the same row: its guess, and what it proves
        # by its rules -- no row_text (a postcode is ignored only as the lot_number
        # column's), no street-cell rule. For origin_main_state, pass 2's bound.
        o_state, o_why = own_state(postcode=r["postcode"], suburb=origin_suburb,
                                   estate=r["estate_name"], address=r["lot_address"],
                                   geo=geo, lot_number=r["lot_number"], street_cell=False)
        origin[rid] = (origin_suburb, o_state, o_why)
        per_file_o.setdefault(file_of(r), set()).add(o_state)
        # And the same once tonight's own write has taken a lot number out of the postcode
        # column (round 3, 29 Sep 2026 -- see origin_main_state): only a number origin/main
        # itself does not read as the lot (the one opening the line, an area, a decimal)
        # on a live row, which pass 2 clears whatever the row's state.
        if clears_a_lot_number(r):
            o2_state, o2_why = own_state(postcode=None, suburb=origin_suburb,
                                         estate=r["estate_name"], address=r["lot_address"],
                                         geo=geo, lot_number=r["lot_number"],
                                         street_cell=False)
            origin_cleared[rid] = (origin_suburb, o2_state, o2_why)
        else:
            o2_state = o_state
        per_file_o2.setdefault(file_of(r), set()).add(o2_state)
        # EVERY row votes, superseded and stale ones included -- decided by measurement,
        # 29 Sep 2026. Three sheets were national on one wrong vote each from an old row
        # (AVIA 2234 last seen 28/09, Silkwood 11510 22/08, Verv 11494 03/09), so counting
        # only live rows looked like the fix. It is not: an E-Agent PDF is a snapshot, and
        # when a newer file lists the same lots its rows are superseded. Tallied over
        # non-superseded rows only, 4 truly national PDFs stop being national -- Hudson's
        # (NSW 67 / QLD 11 over all its rows) among them, which would stamp its Queensland
        # page on its NSW lots again; over each file's latest read 6, over tonight's rows
        # 10. Over every row, with the votes computed by tonight's rules, exactly the three
        # wrong sheets drop out. So the wrong vote is fixed where it is MADE -- own_state
        # ignores the lot number opening the row, the guess no longer takes an estate or a
        # design -- and since every vote is recomputed each night from the stored columns,
        # a rule fixed in code reaches every old row the same night. Nothing here reads a
        # column this step writes except the postcode it clears, which own_state already
        # ignores, so a second run tallies exactly as the first. (Review, 29 Sep: the same
        # way took APLACE's VIC sheet and Creation Homes' QLD sheet out -- a land area read
        # as a postcode, a street in the suburb column. Nor does a file need two votes
        # before it is national: see the module docstring, lot 23153.)
        per_file.setdefault(file_of(r), set()).add(vote)
        votes[rid] = (vote_sub, vote, vote_why)

    national = {f for f, states in per_file.items() if file_is_national(states)}
    national_o = {f for f, states in per_file_o.items() if file_is_national(states)}
    national_o2 = {f for f, states in per_file_o2.items() if file_is_national(states)}

    def origin_main_state(r, cleared=False):
        """(state, signal) origin/main 31d13ef's enrich would leave in this row tonight: its
        guess, its own_state and resolve_state (postcode ignored only as the lot_number
        column's), its national files, its row-text check -- the same rules, run on the
        same stored columns. The bound pass 2 holds every answer to (round 2, 29 Sep
        2026); tests/test_state_invariant.py holds this to a frozen copy of origin/main.

        `cleared` (round 3, 29 Sep 2026): the same on the columns as they stand once pass
        2 has cleared a lot number origin/main does not see out of the postcode column
        (clears_a_lot_number) -- what origin/main gives the row the NEXT night, on this
        step's own write. None when there is no such number, or origin/main keeps the
        row's stored state whatever its postcode (settled). Without it, a row whose line
        opens with its lot ("9671 Second Avenue Baldivis ...", postcode 9671 -- a QLD
        number against its WA suburb) was held blank to origin/main's answer on the night
        its postcode left the column, and took WA the next: two changes where one does."""
        rid = r["id"]
        if cleared:
            if rid not in origin_cleared:
                return None
            o_suburb, o_mine, o_why = origin_cleared[rid]
            r = dict(r)
            r["postcode"] = None
        else:
            o_suburb, o_mine, o_why = origin[rid]
        stored = (r["state"] or "").strip().upper()
        signal0 = r["state_source"]
        o_settled = bool(stored) and bool(STATE_RE.fullmatch(stored)) \
            and (signal0 or "") not in HINT_ONLY_SIGNALS
        if o_settled and cites_a_postcode(signal0) \
                and postcode_is_lot_number(r["postcode"], r["lot_number"]):
            o_settled = False
        if o_settled:
            if cleared:
                return None
            moved = _as_origin_main(r, o_suburb, stored, signal0)
            return (moved[0], TEXT_LOCALITY_SOURCE) if moved else (stored, signal0)
        if file_of(r) in (national_o2 if cleared else national_o):
            return o_mine, o_why
        o_page = r["source_state_hint"] if "source_state_hint" in r.keys() else ""
        o_state, o_signal = resolve_state(postcode=r["postcode"], suburb=o_suburb,
                                          page_state=o_page, geo=geo, listing_state=o_mine,
                                          lot_number=r["lot_number"])
        moved = _as_origin_main(r, o_suburb, o_state, o_signal)
        if moved:
            o_state, o_signal = moved[0], TEXT_LOCALITY_SOURCE
        if not o_state:
            from_file = state_from_filename(r["source_url"] or "")
            if from_file:
                o_state, o_signal = from_file, "stocklist file name"
        return o_state, o_signal

    def _as_origin_main(r, suburb, state, signal):
        try:
            return _check(r, suburb, from_column[r["id"]], state, signal, as_origin_main=True)
        except Exception:                                              # noqa: BLE001
            return None         # text_overrules asks the same and reports the error

    # --- pass 2: resolve ------------------------------------------------------
    for r in rows:
        rid = r["id"]
        suburb, mine, why = proven[rid]
        state = (r["state"] or "").strip().upper()
        builder = (r["builder_name"] or "").strip()

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
        #
        # The decision itself is decide_row (round 3, 29 Sep 2026), the same function
        # guess_suburb weighs a reading with, in this order:
        #   * A STATE READ OFF THE ROW'S OWN LOT NUMBER is not settled (Coleen, 28 Sep 2026
        #     -- see rests_on_its_lot_number). It is re-resolved with that number ignored,
        #     and the number comes out of the postcode column below: it was never the
        #     postcode, and left there it is what the next reader of the column trusts.
        #     postcode is not a content_hash field (database._HASH_FIELDS), so clearing it
        #     does not re-identify the row; _write refuses a column that is.
        #   * A SETTLED STATE THAT RESTS ON A SUBURB MATCH is still checked against the
        #     row's own text. Settled rows are never re-resolved, which is right for most of
        #     them -- re-resolving every 'suburb' state blanked 64 correct ones, because
        #     their files prove more than one state -- but it also meant a state resting on
        #     an estate name in the suburb column was never looked at again. Checked, not
        #     re-resolved: the state changes only when the text names another state's
        #     locality -- and not over a guess its page agrees with (page_and_guess_hold).
        #   * A SETTLED STATE THAT RESTED ON A GUESS THE GUESS NO LONGER MAKES is not
        #     settled either (29 Sep 2026 -- see rests_on_a_withdrawn_guess): "The Grove",
        #     "Round Mountain", "Verona". Re-resolved with tonight's guess -- AFTER the check
        #     above, so a row it moves is moved exactly as before: 9568, "Lot 27102,
        #     Windermere Mambourin" filed QLD on the old guess 'Windermere', goes to VIC on
        #     its text even in a file whose rows prove two states, where re-resolving would
        #     leave it blank.
        #   * AN OVERRULE OF THE PAGE WHOSE PROOF IS GONE (29 Sep 2026 -- see
        #     overrode_the_page_on_a_withdrawn_proof): "21 The Grove Boondall", NSW over its
        #     QLD page on the street name. Re-resolved the same way.
        #   * A LONE SUBURB MATCH ITS PAGE NOW CONTRADICTS (round 3 -- see
        #     a_page_now_disagrees): written on a night the row had no page hint. Checked
        #     first, so the row is resolved as it was before the hint went missing.
        #   * In a NATIONAL file nothing said ABOUT the file is a claim about this row: not
        #     the page it hangs on, not its own name, and not a lone gazetteer hit on a
        #     suburb column that the file has already been caught mislabelling. Only what
        #     the row proves counts, or nothing. Elsewhere resolve_state, the same row-text
        #     check (so the answer does not depend on whether the state was already written
        #     when the check first ran), and the file's own name as the last resort.
        page = r["source_state_hint"] if "source_state_hint" in r.keys() else ""
        new_state, signal, hit, settled, how = decide_row(r, suburb, mine, why,
                                                          file_of(r) in national)
        reopened, reguessed = how == "reopened", how == "reguessed"
        reproved = how in ("reproved", "repaged")
        if how:
            was = (state, r["state_source"])
        if reopened:
            _write(conn, rid, postcode=None)
        # NEVER A STATE ORIGIN/MAIN WOULD NOT GIVE (round 2, 29 Sep 2026 -- the acceptance
        # bar, see origin_main_state and tests/test_state_invariant.py). The answer above is
        # kept when it is origin/main's own, none, the row's page or file-name state, its
        # stated postcode's, or -- where origin/main's answer is blank -- the longer name
        # round 1 reads for origin/main's guess (longer_name_stands); anything else is
        # origin/main's answer instead. Printed, row by row.
        new_state = new_state or ""
        origin_answer = origin_main_state(r)
        allowed = bound(r, origin_answer[0] or "", longer[rid], geo)
        origin_next = origin_main_state(r, cleared=True)
        if origin_next:
            allowed.add(origin_next[0] or "")
        if new_state not in allowed:
            held.append((rid, new_state, signal, origin_answer[0] or "", origin_answer[1]))
            new_state, signal = origin_answer[0] or "", origin_answer[1]
            hit = None
            if signal == TEXT_LOCALITY_SOURCE and new_state != state:
                hit = (new_state, "as origin/main's row-text check moves it")
        # THE STATE ORIGIN/MAIN'S ROW-TEXT CHECK GIVES, RECORDED AS ITS (round 3, 29 Sep
        # 2026). Where tonight's answer is the state origin/main's check writes, but rests on
        # a suburb match -- a guess read in the line -- the row is written with the check's
        # signal, as origin/main writes it. Otherwise it stays open to origin/main's check
        # on every later night, which origin/main's own row is not: a state the check wrote
        # is not a suburb match and is never checked again. Tonight's catch 18659 ("Murray
        # Bridge, South $853,000.00 ..."), cloned fresh, was read Murray Bridge, SA by
        # 'suburb', as origin/main's check puts it; the first night Murray, QLD was stocked
        # origin/main's check -- which the bound above holds a row with no page to -- read
        # the Murray of Murray Bridge and moved it to QLD, while origin/main's own lineage
        # kept SA. None of tonight's rows is written this way: origin/main's check moves no
        # row of the 29 Sep copy.
        if (new_state and signal != TEXT_LOCALITY_SOURCE
                and origin_answer[1] == TEXT_LOCALITY_SOURCE
                and new_state == (origin_answer[0] or "")
                and rests_on_a_suburb_match(signal, from_column[rid])):
            signal = TEXT_LOCALITY_SOURCE
            if new_state != state:
                hit = (new_state, "as origin/main's row-text check puts it")
        if hit and signal == TEXT_LOCALITY_SOURCE:
            overruled.append((rid, state or "-", hit[0], suburb, from_column[rid], hit[1]))
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
        if not settled:
            if new_state:
                by_signal[signal] = by_signal.get(signal, 0) + 1
            clash = disagreement(r["postcode"], suburb, page, geo,
                                 lot_number=r["lot_number"], row_text=row_text(r))
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
            if reguessed or reproved:
                withdrawn_guesses.append((rid, was[0], was[1], state, signal if state else "",
                                          suburb, _excerpt(r["lot_address"], suburb or "")))

        # A LOT NUMBER LEFT IN THE POSTCODE COLUMN of a live row comes out too, whatever the
        # row's state rests on (review, 28 Sep 2026). The re-open above only reached rows
        # whose settled state CITED the postcode, which left 94 live rows showing their lot
        # as a postcode -- 39 of them another state's number, e.g. 11847, VIC, 7608 (TAS).
        # Done after the state is decided and changes no input to it: own_state,
        # resolve_state and disagreement all ignore this number already, so the next run
        # resolves the row exactly as this one did. Live rows only; the re-open above has
        # already cleared its own rows, superseded or not.
        # The number opening the row's own line counts as its lot here too (29 Sep 2026):
        # AVIA 2234's "2120 Stage 21 ...", stored as postcode 2120 by an older parser and
        # never refreshed, because postcode is fill-if-empty.
        if not reopened and not r["superseded_by"] \
                and postcode_is_lot_number(r["postcode"], r["lot_number"], row_text(r), geo):
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
    # Zero once the first run has re-resolved them: a guess only changes when the code does.
    # Every row is printed with the words it now reads, for the same reason as above.
    # The line keeps its old ending (tests and log readers match "the row no longer gives:").
    print(f"  state   re-resolved, it rested on a guessed suburb tonight's guess no longer "
          f"makes, on a suburb match its page now contradicts, or on a proof over its page "
          f"the row no longer gives: {len(withdrawn_guesses)}")
    for rid, was, was_src, now, now_src, sub, words in withdrawn_guesses:
        print(f"            id {rid:<7} {was or '-':<3} -> {now or '-':<3}  was {was_src!r}, "
              f"now {now_src or 'blank'!r}, suburb now {sub or 'none'!r}: {words!r}")
    # Zero on tonight's rows (round 2, 29 Sep 2026): a row here was answered with a state
    # origin/main would not give it, from evidence no review has looked at, and was given
    # origin/main's answer instead. Every one is printed -- each is a rule to look at.
    print(f"  state   kept at origin/main's answer, tonight's rules gave a state it would "
          f"not: {len(held)}")
    for rid, now, now_src, kept, kept_src in held:
        print(f"            id {rid:<7} {now or '-':<3} ({now_src or 'blank'}) -> "
              f"{kept or '-':<3} ({kept_src or 'blank'})")
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
                sub, st, why = votes[r["id"]]
                if st:
                    says.setdefault(st, (why, sub, r["id"]))
            hints = sorted({(r["source_state_hint"] or "-") for r in its})
            blank = sum(1 for r in its if not votes[r["id"]][1])
            print(f"        {len(its):>4} row(s), hint said {'/'.join(hints)}, the rows say "
                  f"{'/'.join(sorted(says))}   {f[-58:]}")
            # The id is the one to look at when a file should not be national (29 Sep 2026:
            # the three wrongly national sheets were each one row, found by hand).
            for st, (why, sub, rid) in sorted(says.items()):
                print(f"               {st:<4} {why} — {sub!r} (e.g. id {rid})")
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
