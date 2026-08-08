"""
Mark older captures of a lot that is also stored fresher.

WHY THERE ARE ANY. `content_hash` includes `lot_number` and a key derived from the
source row's text. Both come out of the extractor, so when the extractor's output for
a lot changes between harvests — a field starts or stops being populated, a stocklist
reformats a line — the identity changes with it and the next run INSERTS a second row
instead of updating the first. Nothing is wrong with the upsert; it is doing exactly
what it was asked to. The identity simply is not stable across extractor changes, and
every improvement to extraction is therefore also a duplication event.

Found on 2026-08-01: 777 rows, 12% of the table, are surplus copies of a lot already
stored. One lot was in there three times —

    27 Jul  Direct Portal  lot_number '9'   no source text   $  962,351
    29 Jul  E-Agent        lot_number '9'   text populated   $1,058,877
    03 Aug  Direct Portal  lot_number NULL  text populated   $1,058,877

WHY THIS IS NOT "THE SAME LOT, CHEAPER ELSEWHERE". It looks like a price comparison
and it is not one. Of 225 lots carrying more than one price, 224 conflict WITHIN a
single channel — the same seller quoting two numbers, which cannot be a competing
offer — and in 152 the cheaper row is simply the OLDEST capture. Surfacing "cheaper
elsewhere" from this would recommend a July price that has since risen, which is worse
than saying nothing.

WHAT COUNTS AS THE SAME LOT. Deliberately conservative: same state, same suburb, same
address label, and an IDENTICAL spec (beds, baths, cars, house size, land size). Two
genuinely different packages on one lot differ in spec and are left alone — 36 groups
did, and they are not touched.

AND THE SAME LINE ARRIVING TWICE. Added 8 Aug 2026 while unpicking the Hattan split.
The same builder stocklist reaches us as E-Agent's pooled PDF and as the builder's own
email, and BOTH copies were live: 62 of Hattan's 180 live rows were surplus, $46.1m of
$129.8m. That is not a naming problem — renaming the rows would have left every one of
them standing — so it is fixed here, in the rule, and not by a script writing
superseded_by directly. main() below CLEARS superseded_by table-wide on every nightly
run and re-derives it, so a hand-set mark evaporates at 03:27 with no error.

MARKED, NOT DELETED. The older capture is real history and its price is what was
advertised then. It keeps its row and gains a pointer to the row that replaced it.

    python supersede_duplicates.py --dry-run
    python supersede_duplicates.py
"""

import argparse
import re
import shutil
import sqlite3
import sys
from collections import defaultdict

from sources.scraper_base import normalise_money_spacing
from datetime import datetime

import config

FIELDS = ("superseded_by", "superseded_at")


def _norm(v):
    s = str(v or "").strip().lower()
    return s or None


def _parsed_date(value):
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value or "").strip(), fmt)
        except (ValueError, TypeError):
            continue
    return None


def lot_key(row):
    """Same physical lot AND the same product on it."""
    key = (_norm(row["state"]), _norm(row["suburb"]), _norm(row["lot_address"]),
           row["bedrooms"], row["bathrooms"], row["car_spaces"],
           row["house_sqm"], row["land_sqm"])
    # A row with no locality or no address label cannot be matched to anything with
    # confidence, so it is never superseded — the spec alone is not an identity.
    if key[0] is None or key[1] is None or key[2] is None:
        return None
    return key


def _freshness(row):
    """Newest wins. date_checked first, then last_seen, then insertion order.

    id is the final tie-break rather than anything clever: when two captures carry the
    same date the later INSERT is the later harvest, and picking deterministically
    matters more than picking cleverly — a run that chose differently each time would
    move Colin's selection around underneath him.
    """
    return (_parsed_date(row["date_checked"]) or datetime.min,
            _parsed_date(row["last_seen"]) or datetime.min,
            row["id"])


def text_key(row):
    """The row's own source line — the strongest evidence two captures are one listing.

    lot_key needs an IDENTICAL spec, and the spec is exactly what is unstable: the
    extractor recorded land_sqm as NULL on one harvest and 455.0 on the next, and
    lot_number as NULL then '68', so seven captures of "68 Amory Ripley Tallow 170"
    formed seven groups of one and none was superseded. 1,038 surplus rows survived
    that way and the stock table showed a listing five times over.

    Two captures carrying byte-identical source text from the same channel cannot be two
    different products — the line contains the lot, the address, the spec and the price.
    Money spacing is normalised first, because a stocklist that reformats "$ 9 89,900"
    into "$989,900" is the same line, and that reformatting alone created 46 duplicates.

    The PRICE has to match too. This repo deliberately treats two rows identical in every
    other field but the price as two real packages (see variant_ordinal in
    database.building_content_hash), so those are left alone: 11 groups differ only on
    price and none of them is touched.
    """
    text = normalise_money_spacing(str(row.get("source_text") or ""))
    # Drop the unit suffix before comparing. A vendor re-exporting "269" as "269 m2" is
    # the same line, and that alone left 33 pairs live -- one of them the same Tarneit
    # lot twice, differing only by two characters.
    text = re.sub(r"(?<=\d)\s*(?:m2|m²|sqm|sq\.?m)\b", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip().lower()
    if not text:
        return None
    return (_norm(row.get("source_channel")), text,
            round(float(row.get("price") or 0), 2))


def cross_channel_text_key(row):
    """The same source line, arriving through two different channels.

    text_key deliberately includes source_channel, because source_channel is the first
    element of database._HASH_FIELDS and two channels are two identities by design. That
    is right for identity and wrong for counting stock. Hattan's stocklist reaches us
    twice — once as E-Agent's pooled PDF, once as the builder's own email — and both
    copies stayed live: 46 lots were counted twice under two spellings of one builder,
    and 16 more under the single correct spelling. $46.1m of $129.8m of live Hattan
    stock was the same houses, twice.

    The evidence is stronger here than in text_key, not weaker: the two rows carry
    BYTE-IDENTICAL source text at the SAME price. A line that names the lot, the estate,
    the spec and the price cannot describe two different houses because it was forwarded
    down a different pipe.

    Measured before shipping, over all 6,141 live rows: 122 groups, 122 surplus rows,
    122 of them cross-channel (121 E-Agent vs digital email, 1 Portal vs E-Agent). The
    same-channel pass already catches everything else, so this adds nothing outside the
    gap it exists to close.

    A CONTRADICTION ON STATE STOPS THE MERGE — see find_superseded, not this key. Keyed
    on text and price alone, 13 of 148 cross-channel groups held rows that CONTRADICT
    each other on state: "Jubilee" stored as both QLD and VIC, "Grandview" as both NSW
    and VIC. The survivor was then picked arbitrarily, and for Jubilee it kept the
    VIC-labelled copy — Colin's Queensland brief went from one qualified listing to none,
    because the only lot that matched had been retired in favour of a row claiming to be
    in another state.

    The guard is applied to the GROUP rather than folded into the key here, and the
    difference is worth 370 listings. Putting the state in the key also splits a capture
    that never stated one away from the capture that did, which is not a contradiction —
    it is one row knowing less. That version retired 44 cross-channel duplicates against
    417, losing ~370 correct merges to prevent 13 wrong ones.
    """
    k = text_key(row)
    return None if k is None else k[1:]          # drop the channel, keep text + price


def _detail(row):
    """How much this capture actually knows. The survivor rule for the cross-channel
    pass, and NOT plain freshness.

    Measured: under _freshness alone the sparse digital-email copy wins 68 of the 122
    groups and 60 of those are decided by nothing but the id tie-break, which would
    destroy the suburb, the spec and the benchmark on 56 lots that have them — the two
    copies of a Hattan lot are 'Weir Views, 4/2/2, benchmarked' against a row whose
    suburb is NULL. Freshness is still the last word, so a tie between two equally
    detailed captures resolves exactly as the other two passes resolve it.
    """
    filled = sum(1 for c in ("suburb", "state", "bedrooms", "bathrooms", "car_spaces",
                             "house_sqm", "land_sqm", "postcode", "estate_name",
                             "builder_name")
                 if row.get(c) not in (None, "", 0))
    benchmarked = 1 if row.get("benchmark_median") not in (None, "", 0) else 0
    return (filled, benchmarked) + _freshness(row)


def find_superseded(rows):
    """[(loser_row, winner_row)] for every surplus capture.

    Three independent groupings, because each catches what the others miss: lot_key
    matches the same lot across DIFFERENT source lines, text_key matches the same source
    line whose extracted spec came out differently, cross_channel_text_key matches the
    same source line that arrived down two pipes. A row caught by more than one is
    recorded once, and a row that has already lost is never reconsidered — so the
    cross-channel pass can only ADD retirements, never move an existing one.
    """
    # ONE COMPONENT, ONE SURVIVOR — and why the obvious version of this was wrong.
    #
    # The first version of the three-pass rule assigned a winner per pass and then walked
    # a loser->winner chain to find who finally survived. Three passes disagree far more
    # often than two, so winners became losers elsewhere and the chains closed into
    # cycles. When a cycle sent a winner's chain back to the loser it started from, the
    # pair was dropped and the row simply stayed live. Measured on the 11,257 stored rows:
    # 6,933 duplicates were correctly identified, 3,272 of them were then discarded by the
    # chain walk, and the run retired 3,661 where the two-pass rule had retired 5,116.
    # A pass added to REMOVE double counting would have published 1,455 more duplicates as
    # sellable stock — the precise harm it exists to prevent, and invisible in the output,
    # which reported a smaller number as though it were a cleaner database.
    #
    # Grouping is transitive — if two rows are duplicates under ANY key they belong to one
    # group — so the honest structure is connected components, not per-pass winners. Each
    # component elects exactly one survivor and everything else points straight at it.
    # A cycle cannot form, because no row is ever both a survivor and a loser.
    parent = {r["id"]: r["id"] for r in rows}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]        # path halving, so deep chains stay cheap
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for key_fn in (lot_key, text_key, cross_channel_text_key):
        groups = defaultdict(list)
        for r in rows:
            k = key_fn(r)
            if k is not None:
                groups[k].append(r)
        for members in groups.values():
            # Two captures that disagree about which STATE a house is in are not proven
            # to be one listing — one of them is simply wrong, and merging picks the
            # wrong one half the time. Keeping both duplicates is the cheaper error: a
            # duplicate is visible and countable, a lot filed under the wrong state is
            # neither, and it is the one that reaches a client. A BLANK state is not a
            # contradiction, only a capture that knows less, so it still merges.
            #
            # Applied to the cross-channel pass alone: lot_key already requires the state
            # to match, and text_key compares captures of one channel, where a state
            # flip between harvests is the state resolver improving rather than two
            # sources disagreeing.
            if key_fn is cross_channel_text_key:
                stated = {_norm(m.get("state")) for m in members if _norm(m.get("state"))}
                if len(stated) > 1:
                    continue
            for other in members[1:]:
                union(members[0]["id"], other["id"])

    by_id = {r["id"]: r for r in rows}
    components = defaultdict(list)
    for r in rows:
        components[find(r["id"])].append(r)

    out = []
    for members in components.values():
        if len(members) < 2:
            continue
        # The cross-channel case keeps the better-described capture rather than the newest
        # one (see _detail): the two copies of a Hattan lot are "Weir Views, 4/2/2,
        # benchmarked" against a row whose suburb is NULL, and freshness alone would keep
        # the empty one. Within a single channel, detail is stable and freshness is the
        # whole question, so the original rule is preserved there untouched.
        spans_channels = len({_norm(m.get("source_channel")) for m in members}) > 1
        rank = _detail if spans_channels else _freshness
        ordered = sorted(members, key=rank, reverse=True)
        winner = ordered[0]
        for loser in ordered[1:]:
            out.append((by_id[loser["id"]], winner))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = ap.parse_args(argv)

    print("=" * 66)
    print("  Supersede older captures of the same lot")
    print("=" * 66)

    if not args.dry_run:
        dest = f"{config.DATABASE_PATH}.bak-supersede-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy2(str(config.DATABASE_PATH), dest)
        print(f"[+] backup written: {dest}")
    else:
        print("[i] report only — nothing will be written.")

    # Instantiating ResearchDatabase is what runs the additive-column migration, so
    # the superseded_* columns exist before the first raw query touches them.
    from database import ResearchDatabase
    ResearchDatabase()

    conn = sqlite3.connect(str(config.DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM buildings")]
    pairs = find_superseded(rows)

    total = len(rows)
    live = total - len(pairs)
    print(f"    {total} row(s) stored")
    print(f"    {len(pairs)} superseded by a fresher capture of the same lot")
    print(f"    {live} distinct lot(s) remain live")

    if pairs:
        moved = [(l, w) for l, w in pairs
                 if round(float(l["price"] or 0), 2) != round(float(w["price"] or 0), 2)]
        cheaper_stale = sum(1 for l, w in moved
                            if float(l["price"] or 0) < float(w["price"] or 0))
        print()
        print(f"    of those, {len(moved)} had a price different from the fresher row")
        print(f"    and {cheaper_stale} were CHEAPER — the stale prices that would have been")
        print(f"    surfaced as 'deals' if superseded rows were left in the running")

        # The cross-channel retirements are named, not folded into the total. They are the
        # only ones where the row disappearing is not an older capture of the same feed —
        # it is the same listing arriving down a different pipe, so the per-channel daily
        # figures move ('digital email' drops by 121) and a reader who is not told will
        # reasonably think stock went missing overnight.
        cross = [(l, w) for l, w in pairs
                 if _norm(l["source_channel"]) != _norm(w["source_channel"])]
        if cross:
            seen = {}
            for l, w in cross:
                pair = " vs ".join(sorted({str(l["source_channel"]), str(w["source_channel"])}))
                seen[pair] = seen.get(pair, 0) + 1
            print()
            print(f"    {len(cross)} of them are the SAME source line arriving through two")
            print(f"    channels — one listing counted twice, not an older capture:")
            for pair, n in sorted(seen.items(), key=lambda kv: -kv[1]):
                print(f"        {n:>5}  {pair}")
            by_builder = {}
            for l, _w in cross:
                by_builder[l["builder_name"] or "(blank)"] = \
                    by_builder.get(l["builder_name"] or "(blank)", 0) + 1
            for name, n in sorted(by_builder.items(), key=lambda kv: -kv[1])[:6]:
                print(f"        {n:>5}  retired from {name}")

    if not args.dry_run and pairs:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Clear first: a row that is no longer a duplicate (because the row that beat
        # it was itself removed, or the spec changed) must not stay flagged.
        conn.execute("UPDATE buildings SET superseded_by=NULL, superseded_at=NULL")
        conn.executemany(
            "UPDATE buildings SET superseded_by=?, superseded_at=? WHERE id=?",
            [(w["content_hash"], stamp, l["id"]) for l, w in pairs])
        conn.commit()
        print(f"\n    marked. Superseded rows keep their price and history; they are")
        print(f"    excluded from the benchmark and hidden from the stock table by default.")

    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
