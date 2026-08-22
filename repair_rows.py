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

3. PRICES UNDERSTATED BY A SPLIT MONEY AMOUNT.
   "$ 9 32,900" is $932,900 with a space in it, courtesy of a PDF copy. It parsed as $9,
   was discarded as implausible, and a COMPONENT of the package became the headline
   price: 113 rows published roughly $400,000 below what the package costs. Corrected
   from the row's own text, and only where that text clearly beats what we published.

4. LAND SOLD AS IF IT WERE A HOUSE.
   Rows whose own project title says "Land Only" and which carry no bedrooms and no
   house size are typed as Land, so a block of dirt is not read as a dwelling. The
   companion rule — beds plus a house size means "House & Land" — was measured against
   the 1,670 rows already typed and rejected at 79% accuracy: 306 of them are
   apartments and 43 townhouses. A rule that mislabels one row in five is worse than
   the blank it replaces.

5. THE SAME LISTING STORED SEVERAL TIMES.
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
import os
import re
import sqlite3
import sys
from datetime import datetime

import config
from sources.feature_extract import parse_postcode
from sources.scraper_base import MIN_PLAUSIBLE_PRICE, normalise_money_spacing

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


_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")
_UNDERSTATED_BY = 1.25          # only correct a price the row's own text clearly beats


def find_understated_prices(rows):
    """Rows whose own text states a package total ABOVE the price we published.

    The cause is a money amount split by a space — "$ 9 32,900" for $932,900 — which
    parsed as $9, was dropped as implausible, and left a COMPONENT as the headline
    price. The land and build figures survive intact beside it, so the row reads
    land $397,900, build $535,000 and we published $535,000 for a $932,900 package.

    Only corrected when the recovered total clearly exceeds the stored price, so a
    correct price is never rewritten by a mis-read of the same line.
    """
    out = []
    for r in rows:
        if not r["price"]:
            continue
        text = str(r["source_text"] or "")
        fixed = normalise_money_spacing(text)
        if fixed == text:
            continue                       # nothing was broken in this row
        amounts = []
        for m in _MONEY.finditer(fixed):
            try:
                v = float(m.group(0).replace("$", "").replace(",", "").strip())
            except ValueError:
                continue
            if MIN_PLAUSIBLE_PRICE <= v <= 5_000_000:
                amounts.append(v)
        if not amounts:
            continue
        total = max(amounts)
        if total > float(r["price"]) * _UNDERSTATED_BY:
            # Document order in every observed stocklist is land, build, total.
            land = build = None
            if len(amounts) >= 3 and amounts[-1] == total:
                land, build = amounts[-3], amounts[-2]
            out.append((r, total, land, build))
    return out


# The source SAYS the product is land — "…Box Hill Land Only (10/27)" — rather than us
# concluding it from an absence.
_SAYS_LAND_ONLY = re.compile(r"land\s*only|\bvacant\s+land\b", re.I)


def find_land_only(rows):
    """Rows the source itself calls land only, and which carry no dwelling at all.

    Colin, 5 Aug: "This one is a lot. It's land. It's not a house... how come they have
    bedrooms and baths?" Typing these stops a block of dirt being read as a house.

    The obvious companion rule — beds + house size means "House & Land" — was measured
    and REJECTED: of 1,670 already-typed rows carrying both, only 79% are a house
    product; 306 are apartments and 43 townhouses. A rule that mislabels one row in five
    is worse than the blank it replaces, so product_type stays empty for those.
    """
    out = []
    for r in rows:
        if str(r["product_type"] or "").strip():
            continue
        if r["bedrooms"] or r["house_sqm"] or not r["land_sqm"]:
            continue          # anything with a dwelling on it is not land only
        text = "%s %s" % (r["estate_name"] or "", r["source_text"] or "")
        if _SAYS_LAND_ONLY.search(text):
            out.append(r)
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


def _iso(d):
    """DD/MM/YYYY -> YYYYMMDD, so dates compare as dates.

    A plain string compare puts '29/07/2026' above '13/08/2026' — it reads the leading
    digit and stops. That mistake once made a channel four days stale look nineteen days
    stale and pointed an investigation at the wrong incident.
    """
    p = str(d or "").strip().split("/")
    return f"{p[2]}{p[1]}{p[0]}" if len(p) == 3 else ""


def _identity_without_suburb(r):
    """The row's stored identity basis with the SUBURB taken out of it.

    Built from database's own _HASH_FIELDS rather than a fresh guess, minus suburb_norm,
    so two rows group together here exactly when content_hash would have matched had the
    suburb agreed. _variant_key is imported rather than reimplemented — it is the part
    that strips prices, statuses and dates out of the row text, and a second copy of that
    rule would drift from the one identity actually depends on.
    """
    from database import _variant_key
    return (
        _norm(r["source_channel"]),
        _norm(r["attribution_scope"]),
        _norm(r["builder_name"]),
        _norm(r["lot_number"]),
        # house_design is hashed but never STORED as a column, so it cannot be read back
        # here. _variant_key covers it: the design name is written in source_text
        # ("... Duval 04 149.49 4 2 2 ..."), which is what that key is derived from, so
        # two designs on one lot still land in different groups.
        _norm(r["land_sqm"]),
        _variant_key({"source_text": r["source_text"], "lot_address": r["lot_address"]}),
    )


def find_suburb_twins(rows):
    """One package stored twice because it only recently started stating its suburb.

    THE MECHANISM. `suburb_norm` is one of the eight fields in content_hash, so the day a
    stocklist's Suburb COLUMN began to be read (18 Aug 2026, see
    spreadsheet_extract._pdf_rows_from_tables) every affected row acquired a NEW identity.
    The harvest inserted it, and the old suburb-less row was never matched again — so it
    was never updated and never superseded. It just sits there, live, a second copy of a
    property Coleen can sell once.

    PRICE IS ALLOWED TO DIFFER, unlike find_duplicate_groups. The suburb-less copy is an
    OLDER capture, so a price move between then and now is expected — it is the normal
    case, not evidence of a different package. Everything identity actually turns on is
    required to match: builder, lot number, design, land size, channel, and the row text
    with prices, statuses and dates stripped out of it. Superseding marks rather than
    deletes, per the standing rule, so the older capture and its advertised price remain
    real history.

    Only a BLANK-suburb row is ever the loser. Two rows that each name a suburb, and name
    different ones, are left alone: that is a disagreement about where a property is, and
    guessing which is right is exactly the kind of call this project refuses to make.
    """
    by = collections.defaultdict(list)
    for r in rows:
        if r["superseded_by"]:
            continue
        by[_identity_without_suburb(r)].append(r)

    pairs = []
    for _, g in by.items():
        if len(g) < 2:
            continue
        named = [r for r in g if (r["suburb"] or "").strip()]
        blank = [r for r in g if not (r["suburb"] or "").strip()]
        if not named or not blank:
            continue
        # Where the named copies disagree about the suburb there is no single keeper to
        # point the blank ones at, so nothing is collapsed.
        if len({_norm(r["suburb"]) for r in named}) > 1:
            continue
        # THE KEEPER IS THE ROW THE HARVEST STILL SEES, not the fullest one.
        #
        # Ranking on completeness alone chose the stale copy 
        # and would have retired live stock: one group kept a row last checked 06/08
        # whose suburb read "PART" and whose price was $0, and superseded tonight's
        # $60,000 row for it. A suburb-less row that is NEWER than every named copy is
        # not an old duplicate at all — the source has stopped stating a suburb — so it
        # is left alone rather than collapsed backwards.
        keeper = sorted(named, key=lambda r: (_iso(r["date_checked"]), _completeness(r)),
                        reverse=True)[0]
        cut = _iso(keeper["date_checked"])
        stale = [r for r in blank if _iso(r["date_checked"]) < cut]
        if stale:
            pairs.append((keeper, stale))
    return pairs



# ---------------------------------------------------------------------------------
def _row_key(text):
    """A stable key for one source row, tolerant of the noise a PDF reader leaves."""
    cleaned = re.sub(r"[^A-Za-z0-9$,./ -]", " ", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip().lower()[:120]


def find_suburbs_in_own_document(rows, fetch=None, extract=None):
    """Rows whose OWN source document names their suburb when read with today's code.

    NOT AN INFERENCE. The same document, the same row, re-parsed by a better extractor --
    matched on the row's own text, so a listing only takes the suburb of the line it was
    read from. Nothing here reasons about which suburb a row might belong to.

    THE CASE IT EXISTS FOR. Every extractor fix only helps rows harvested after it, and a
    document that stops being published is never harvested again -- so its rows keep
    whatever the parser of the day made of them, forever. Two FRD stocklists last read on
    29 July and 6 August extract at 80 of 80 and 82 of 82 today, while their stored rows
    sit at 28 of 30 and 34 of 35, because E-Agent no longer lists those files. 224 of the
    385 rows with no suburb are in that state.

    Only fills a BLANK. A row that already resolves keeps what it has, so re-reading can
    never overwrite a value some other rule earned.
    """
    import suburb_quality

    if fetch is None or extract is None:                # imported lazily: needs network
        import document_suburb
        from sources.spreadsheet_extract import extract_stocklist
        fetch = fetch or document_suburb.fetch
        extract = extract or extract_stocklist

    wanted = collections.defaultdict(list)
    for r in rows:
        if r["superseded_by"]:
            continue
        url = str(r["source_url"] or "")
        if not url.startswith("http"):
            continue
        # BLANK, OR A VALUE TODAY'S CODE REFUSES. Not the same as overwriting a good one.
        #
        # Filling only blanks missed the rows that need this most. Two FRD stocklists
        # store '2026', 'offer' and 'RH' in the suburb column -- wrapped fragments the
        # extractor read as section headers before those were fixed today -- and 48 rows
        # were skipped because a junk value is not a blank. suburb_quality already refuses
        # every one of them, so the published column shows nothing either way; the
        # difference is whether the row can be repaired.
        stored = str(r["suburb"] or "").strip()
        if stored:
            _sub, why = suburb_quality.resolve(dict(r, suburb_source=""))
            if suburb_quality.is_searchable(why):
                continue                       # a value that stands up today is kept
        wanted[url].append(r)

    found = []
    for url, group in sorted(wanted.items(), key=lambda kv: -len(kv[1])):
        data = fetch(url)
        if not data:
            continue
        try:
            listings = extract(data, source_label="repair", builder_hint="")
        except Exception:                               # a document we cannot parse today
            continue
        named = {_row_key(l.get("source_text")): str(l.get("suburb") or "").strip()
                 for l in listings if str(l.get("suburb") or "").strip()}
        if not named:
            continue
        for r in group:
            suburb = named.get(_row_key(r["source_text"]))
            if suburb:
                found.append((r, suburb))
    return found


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

    understated = find_understated_prices(live)
    print(f"\n3. prices UNDERSTATED because the row's total was split mid-number: "
          f"{len(understated)}")
    for r, total, land, build in understated[:5]:
        print(f"     published ${float(r['price']):>10,.0f}  ->  actual ${total:>10,.0f}  "
              f"({(r['builder_name'] or '?')[:18]:18} {str(r['suburb'] or '-')[:14]:14})")
        print(f"       {str(r['source_text'])[:96]}")

    land_only = find_land_only(live)
    print(f"\n4. rows the source itself calls land only: {len(land_only)}")
    for r in land_only[:3]:
        print(f"     land={r['land_sqm']}m2 no dwelling :: "
              f"{str(r['estate_name'] or r['lot_address'])[:64]}")

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

    twins = find_suburb_twins(live)
    twin_losers = [r for _, rest in twins for r in rest]
    print(f"\n5. one package stored twice, the older copy missing only its suburb: "
          f"{len(twins)} group(s), {len(twin_losers)} copy(ies) to supersede")
    for keep, rest in twins[:5]:
        print(f"     keep  suburb={keep['suburb']!r} checked {keep['date_checked']} "
              f"${float(keep['price'] or 0):,.0f}")
        for r in rest:
            print(f"     drop  suburb=''            checked {r['date_checked']} "
                  f"${float(r['price'] or 0):,.0f}  {str(r['lot_address'])[:44]}")

    # 6. RE-READ EACH BLANK ROW'S OWN DOCUMENT with today's extractor. Network-bound, so
    #    it runs last, and a document that cannot be fetched costs that document alone.
    #    SPB_SKIP_REREAD=1 turns it off for an offline run.
    reread = []
    if os.environ.get("SPB_SKIP_REREAD") != "1":
        try:
            reread = find_suburbs_in_own_document(rows)
        except Exception as exc:                        # noqa: BLE001 - never fatal
            print("\n6. re-reading source documents failed (%s)" % type(exc).__name__)
    print("\n6. blank rows whose own document names their suburb today: %d" % len(reread))
    for r, suburb in reread[:6]:
        print(f"     {suburb:<18} checked {r['date_checked']}  "
              f"{str(r['lot_address'])[:52]}")

    if not apply_changes:
        print("\n(report only — pass --apply to write these corrections)")
        return 0

    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.cursor()
    for r, total, land, build in understated:
        if land and build:
            cur.execute("UPDATE buildings SET price=?, land_price=?, build_price=? WHERE id=?",
                        (total, land, build, r["id"]))
        else:
            cur.execute("UPDATE buildings SET price=? WHERE id=?", (total, r["id"]))
    for r in land_only:
        cur.execute("UPDATE buildings SET product_type='Land' WHERE id=?", (r["id"],))
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
    for keep, rest in list(groups) + list(twins):
        for r in rest:
            cur.execute("UPDATE buildings SET superseded_by=?, superseded_at=? WHERE id=?",
                        (keep["content_hash"] or str(keep["id"]), now, r["id"]))
    for r, suburb in reread:
        # The stored content_hash goes stale here, exactly as it does for every other
        # correction above. That is the documented trade: the identity is recomputed at
        # the next insert, and find_suburb_twins collapses the pair if one appears.
        # `suburb` only. suburb_source is NOT a stored column -- build_web computes it
        # from suburb_quality.resolve on the way out, which is the whole point of that
        # module. Writing to it here failed the transaction and rolled back every other
        # repair in the same run.
        cur.execute("UPDATE buildings SET suburb=? WHERE id=?", (suburb, r["id"]))
    conn.commit()
    print(f"\napplied: {len(fabricated)} postcode(s) withdrawn "
          f"({states_dropped} state(s) cleared), {len(cheap)} implausible price(s) cleared, "
          f"{len(understated)} understated price(s) corrected, "
          f"{len(land_only)} row(s) typed as land, "
          f"{len(losers)} duplicate row(s) superseded, "
          f"{len(twin_losers)} suburb-less twin(s) superseded, "
          f"{len(reread)} suburb(s) re-read from the row's own document")
    after = len([r for r in _rows(conn) if not r["superseded_by"]])
    print(f"live rows: {len(live)} -> {after}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the corrections")
    sys.exit(main(ap.parse_args().apply))
