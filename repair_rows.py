"""
Repair stored rows that older, buggier extractor versions wrote.

The extractors have been fixed several times, but a fix only helps rows harvested after
it. These repairs re-judge what is already stored, using today's code, and correct or
retract what today's code would not have produced. Nothing here invents a value: a
repair either replaces a value with what the current extractor derives from the row's
own stored source_text, or it clears a value that can no longer be justified.

    python repair_rows.py            # report only, changes nothing
    python repair_rows.py --apply    # write the corrections

Three repairs:

1. POSTCODES FABRICATED FROM A LOT NUMBER.
   "2226 Whiterock White Rock White Rock 156 - Facade A ..." opens with the lot number.
   The old parser read it as NSW postcode 2226 and state_resolver then set the state to
   NSW — on a property in White Rock, QLD. The dashboard showed the same lot in two
   states, because another channel had it right. The postcode goes, and any state that
   was derived from that postcode alone goes with it: an unknown state is the honest
   answer, and the standing rule is that a blank beats a plausible guess.

2. PRICES TOO SMALL TO BE A PROPERTY.
   $7 taken from a marketing paragraph, $149 from an internal-area table. Twelve rows,
   and the stock table sorts by price ascending, so they were the first four listings a
   client ever saw. Cleared rather than adjusted: we know the number is not the price
   and we do not know what the price is. A priceless row is excluded from every
   shortlist and visibly blank in the table, which is the honest outcome.

3. THE SAME LISTING STORED SEVERAL TIMES.
   395 groups of rows share a byte-identical source_text — the same row reached us
   through more than one channel (an e-agent portal and the emailed price list). They
   are not variants: they are one listing. The copies disagree, and the weaker copy is
   the one a client tends to meet first, because it has no builder name and no price
   fields to sort it down the page. The most complete copy is kept and the others are
   marked superseded — the same mechanism used for stale captures, so nothing is
   deleted and the dashboard can still show them on request.
"""

import argparse
import collections
import sqlite3
import sys
from datetime import datetime

import config
from sources.feature_extract import parse_postcode
from sources.scraper_base import MIN_PLAUSIBLE_PRICE

# A state that rests on nothing but the postcode cannot survive the postcode being
# withdrawn. A state read off the page or the suburb stands on its own and is left alone.
STATE_SOURCES_THAT_DIE_WITH_THE_POSTCODE = ("postcode",)


def _rows(conn):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute("SELECT * FROM buildings")]


def find_fabricated_postcodes(rows):
    """Stored postcodes that today's parser will not produce from the row's own text."""
    out = []
    for r in rows:
        if not r["postcode"]:
            continue
        text = " ".join(str(r[k] or "") for k in ("lot_address", "suburb", "source_text"))
        if str(parse_postcode(text) or "") == str(r["postcode"]).strip():
            continue
        drop_state = (r["state"] and
                      str(r["state_source"] or "").strip().lower()
                      in STATE_SOURCES_THAT_DIE_WITH_THE_POSTCODE)
        out.append((r, drop_state))
    return out


def _completeness(r):
    """How much of a listing a row actually carries. Higher wins when copies collide."""
    score = 0
    if (r["builder_name"] or "").strip():
        score += 40                      # the field a client sorts and filters on first
    if (r["suburb"] or "").strip():
        score += 12
    if r["state"] and str(r["state_source"] or "").strip().lower() != "postcode":
        score += 8                       # a state from the page beats one from a number
    elif r["state"]:
        score += 3
    for field, worth in (("price", 6), ("land_sqm", 4), ("house_sqm", 4), ("bedrooms", 3),
                         ("bathrooms", 2), ("car_spaces", 2), ("postcode", 3),
                         ("lot_number", 2), ("listing_url", 3), ("brochure_url", 2),
                         ("floorplan_url", 2), ("estate_name", 2), ("title_status", 1),
                         ("availability_status", 2)):
        if r[field] not in (None, "", 0):
            score += worth
    # A tie goes to the row seen most recently, then to the lowest id, so the choice is
    # deterministic and a re-run picks the same winner.
    return (score, str(r["last_seen"] or ""), -int(r["id"]))


# Fields that distinguish one listing from another. If a copy holds a DIFFERENT
# non-empty value in any of them it is not a redundant copy, whatever its source_text
# says, and both rows stay.
IDENTIFYING = ("builder_name", "lot_address", "suburb", "state", "price", "land_sqm",
               "house_sqm", "bedrooms", "bathrooms", "car_spaces", "postcode",
               "lot_number", "availability_status", "estate_name", "product_type")


def _norm(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    return str(value).strip().lower() or None


def subsumed_by(loser, keeper):
    """True when the loser carries nothing the keeper does not already carry.

    Byte-identical source_text is NOT sufficient on its own. 133 groups here share one
    source_text across two builders — the same lot listed under two programmes, e.g.
    "Lot 107, Eternal Homes" and "Lot 107, HUDSON HOMES: DUAL & DUPLEX" — and another 12
    hold genuinely different prices. Collapsing those would delete a real listing and
    silently pick one builder over another, which is exactly the kind of guess this
    project refuses to make. So a copy is only redundant when every identifying field is
    either empty or an exact match.
    """
    for field in IDENTIFYING:
        a, b = _norm(loser[field]), _norm(keeper[field])
        if a is not None and a != b:
            return False
    return True


def find_duplicate_groups(rows):
    """Groups of live rows sharing a source_text, reduced to strictly redundant copies."""
    by = collections.defaultdict(list)
    for r in rows:
        if r["superseded_by"]:
            continue
        text = (r["source_text"] or "").strip()
        if text:
            by[text].append(r)
    groups = []
    for text, g in by.items():
        if len(g) < 2:
            continue
        g = sorted(g, key=_completeness, reverse=True)
        keeper, rest = g[0], g[1:]
        redundant = [r for r in rest if subsumed_by(r, keeper)]
        if redundant:
            groups.append((keeper, redundant))
    return groups


def main(apply_changes):
    conn = sqlite3.connect(str(config.DATABASE_PATH))
    rows = _rows(conn)
    live = [r for r in rows if not r["superseded_by"]]
    print(f"{len(rows)} rows stored, {len(live)} live\n")

    fabricated = find_fabricated_postcodes(rows)
    states_dropped = sum(1 for _, drop in fabricated if drop)
    print(f"1. postcodes today's parser will not reproduce: {len(fabricated)}")
    print(f"   states resting on one of them, to be cleared: {states_dropped}")
    for r, drop in fabricated[:6]:
        print(f"     pc={r['postcode']} state={r['state']} ({r['state_source']}) "
              f"{'-> state cleared' if drop else '-> state kept, has another source'}")
        print(f"       {str(r['source_text'])[:88]}")

    cheap = [r for r in live
             if r["price"] and 0 < float(r["price"]) < MIN_PLAUSIBLE_PRICE]
    print(f"\n2. prices too small to be a property: {len(cheap)}")
    for r in cheap[:5]:
        print(f"     ${float(r['price']):>10,.0f}  {(r['builder_name'] or '?')[:24]:24} "
              f"{str(r['source_text'])[:56]}")

    groups = find_duplicate_groups(live)
    losers = [r for _, rest in groups for r in rest]
    print(f"\n2. identical-source_text groups: {len(groups)}, "
          f"redundant copies to supersede: {len(losers)}")
    gained = sum(1 for keep, rest in groups
                 if (keep["builder_name"] or "").strip()
                 and any(not (r["builder_name"] or "").strip() for r in rest))
    print(f"   groups where keeping the fuller copy removes an unnamed row: {gained}")
    print(f"   channels losing a copy: "
          f"{dict(collections.Counter(r['source_channel'] for r in losers).most_common())}")
    for keep, rest in groups[:3]:
        print(f"     keep  [{keep['source_channel']}] builder={keep['builder_name']!r} "
              f"state={keep['state']} score={_completeness(keep)[0]}")
        for r in rest:
            print(f"     drop  [{r['source_channel']}] builder={r['builder_name']!r} "
                  f"state={r['state']} score={_completeness(r)[0]}")

    if not apply_changes:
        print("\n(report only — pass --apply to write these corrections)")
        return 0

    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.cursor()
    for r in cheap:
        # Cleared, not adjusted. We know the stored number is not the price; we do not
        # know what the price is, and the "no price" gate already keeps a priceless row
        # out of every shortlist while leaving it visible in the table.
        cur.execute("UPDATE buildings SET price=NULL WHERE id=?", (r["id"],))
    for r, drop_state in fabricated:
        if drop_state:
            cur.execute("UPDATE buildings SET postcode=NULL, state=NULL, "
                        "state_source=? WHERE id=?",
                        ("withdrawn: postcode was the lot number", r["id"]))
        else:
            cur.execute("UPDATE buildings SET postcode=NULL WHERE id=?", (r["id"],))
    for keep, rest in groups:
        for r in rest:
            cur.execute("UPDATE buildings SET superseded_by=?, superseded_at=? WHERE id=?",
                        (keep["content_hash"] or str(keep["id"]), now, r["id"]))
    conn.commit()
    print(f"\napplied: {len(fabricated)} postcode(s) withdrawn "
          f"({states_dropped} state(s) cleared), {len(cheap)} implausible price(s) cleared, "
          f"{len(losers)} duplicate row(s) superseded")
    after = len([r for r in _rows(conn) if not r["superseded_by"]])
    print(f"live rows: {len(live)} -> {after}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the corrections")
    sys.exit(main(ap.parse_args().apply))
