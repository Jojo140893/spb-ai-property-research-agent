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
# The marker this repair writes and record_building recognises. Imported rather than
# retyped: two spellings of it would silently stop a re-listed lot coming back.
from database import WITHDRAWN_FROM_SOURCE
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
    suburb = (r["suburb"] or "").strip()
    if suburb:
        # A SUBURB THAT DOES NOT RESOLVE IS NOT A SUBURB. 'EOI Form' is a non-empty
        # string and scored the same 12 as 'Redbank Plains', so which copy of AVIA lot
        # 1392 survived the collapse rested on the last_seen tiebreak below rather than
        # on which copy knows where the property is. It happened to pick correctly; that
        # is not the same as being correct.
        import suburb_quality
        score += 12 if suburb_quality.is_searchable(
            suburb_quality.resolve(dict(r, suburb_source=""))[1]) else 2
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


# The vendor's own "last updated" column, which changes without the listing changing.
_VOLATILE_DATE = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b")


def _line_identity(text):
    """A row's line with the vendor's date stamp removed.

    THE DATE COLUMN MINTS A NEW LISTING EVERY TIME IT IS TOUCHED. suburb is part of
    content_hash and so is source_text, so AVIA's sheet -- which prints an "updated"
    date on every row -- produces a brand new row each time that date ticks, and the
    previous one is never superseded because its hash no longer matches anything. Lot
    1392 is in the catalogue three times: 01.08, 05.08 and 21.08, same price, same
    everything, and the two older copies still carry 'EOI Form' where the suburb goes.

    Grouping on the exact text could not see it. 78 live rows repeat another row's line
    once the date is ignored, 50 of them at an identical price.
    """
    return re.sub(r"\s+", " ", _VOLATILE_DATE.sub(" ", str(text or ""))).strip()


# A row whose ADDRESS is one line of a spec table. See find_spec_fragments.
_SPEC_FRAGMENT = re.compile(r"^\s*(?:land|house|build|home)\s+size\s*:", re.I)

# A TRANSPOSED table puts its attributes down the page and its units across it, so a
# "row" is one attribute holding a number for every unit at once. See
# find_spec_fragments.
_TRANSPOSED_LABEL = re.compile(
    r"^\s*(?:total|internal|external|beds?|bath|balcony|carpark|car|unit\s*no|"
    r"reserved|level|storage|aspect|price|body\s*corp)\b", re.I)
_BARE_NUMBER = re.compile(r"(?<![\w$.])\d+(?:\.\d+)?(?![\w%])")


def _is_transposed_row(text):
    """True for one attribute line of a table written sideways."""
    value = str(text or "")
    return (bool(_TRANSPOSED_LABEL.match(value))
            and len(_BARE_NUMBER.findall(value)) >= 5)


def find_refuted_states(rows):
    """Rows whose stored state is contradicted by their own suburb and by their document.

    "West End" is a locality in Queensland and Western Australia and in neither New South
    Wales nor anywhere else. One of the eight West End Commercial tenancies records NSW;
    its seven siblings on the same price list record no state at all. So the state is
    refuted twice over -- by the suburb sitting beside it, and by every other row read
    from the same file -- and it costs the row its only location, because a suburb that
    is not a locality in the stated state is refused.

    The state goes and the suburb stays, unchecked, exactly as its siblings are. This
    does not decide WHICH West End the tenancies are in; that would be inventing an
    answer. It stops a value we know to be wrong from suppressing one we do not.

    Deliberately narrow: the suburb must be a real locality SOMEWHERE (or this is just a
    junk suburb, not a wrong state), the row must currently resolve to nothing, and no
    other live row on the same document may carry the same state.
    """
    import collections

    import suburb_quality
    from geo import SuburbGeoIndex

    index = SuburbGeoIndex()
    if not index.loaded:
        return []
    by_document = collections.defaultdict(list)
    for r in rows:
        if not r["superseded_by"]:
            by_document[str(r["source_url"] or "")].append(r)

    out = []
    for r in rows:
        if r["superseded_by"]:
            continue
        suburb = str(r["suburb"] or "").strip()
        state = str(r["state"] or "").strip().upper()
        if not (suburb and state):
            continue
        if suburb_quality.is_searchable(
                suburb_quality.resolve(dict(r, suburb_source=""))[1]):
            continue
        if index.locate(suburb, state):
            continue                       # the state agrees with the suburb: nothing here
        if not any(index.locate(suburb, s) for s in
                   ("VIC", "NSW", "QLD", "SA", "WA", "NT", "ACT", "TAS")):
            continue                       # not a locality anywhere: a junk suburb, not a
                                           # wrong state, and clearing the state gains it
                                           # nothing
        siblings = by_document[str(r["source_url"] or "")]
        if any(o is not r and str(o["state"] or "").strip().upper() == state
               for o in siblings):
            continue                       # its own document backs the state up
        out.append(r)
    return out


def find_control_rows(rows):
    """A spreadsheet's FILTER row, stored as a property.

    A sheet published with its filters left on prints the control strip as a row of its
    own -- "All [v] Storage [v] All [v] All [v] $225,500.00 $1,298,154.00 All [v]", each
    [v] a dropdown arrow from an icon font, each "All" a column's unfiltered state. Two
    prices was enough to look like a listing, so it was stored as one.

    Three are live. Two are the ONLY rows builder "mcmullanbird.com.au" has; the third
    has "STOCKLIST" in its suburb column. They were the last rows in the catalogue that
    no suburb rule could reach, and none should have tried: "All" is not a place and
    these are not properties.

    sources.spreadsheet_extract._is_a_control_row now refuses them at the door, which
    only helps rows harvested after it. These three are already stored.
    """
    from sources.spreadsheet_extract import (_is_a_control_row,
                                             _is_a_price_range_sentence)

    return [r for r in rows
            if _is_a_control_row(r["source_text"])
            or _is_a_price_range_sentence(r["source_text"], r)]


def find_spec_fragments(rows):
    """Rows that are half a spec table, not a property.

    The Torsion builder portal prints each lot as a pair of lines --

        LAND SIZE : 408 m2   LAND PRICE : $ 549,000
        HOUSE SIZE : 160 m2  HOUSE PRICE : $ 395,529

    -- and the scraper stored each line as its own listing. All 26 carry a price and
    nothing else: no lot number, no bedrooms, no suburb, no address beyond the fragment
    itself. 23 of the 26 repeat a price that a NAMED Torsion row already records as its
    land_price or build_price, so the property is in the catalogue twice over, once
    whole and once in halves.

    They were being counted as listings missing a suburb. They are not listings. A
    search for a $549,000 property would return one, showing a land-size line where the
    address goes.

    Every one of the four conditions is required: an address that is a spec line, and no
    lot number, no bedrooms and no usable suburb. A real listing that happens to print
    its land size first still has one of those.
    """
    import suburb_quality

    out = []
    for r in rows:
        if r["superseded_by"]:
            continue
        text = str(r["lot_address"] or "")
        # TWO SHAPES, one cause: a table the reader could not follow.
        #
        # The second is a table written SIDEWAYS -- the Lakeview price list runs its
        # units across the page and its attributes down it, so "Total 151 151 42 50 50
        # 42 External 22 22 3 0 0 0 0" is the Total row holding a figure for six units
        # at once. Seven rows of that document are attribute lines, and not one of them
        # is a property: no lot, no bedrooms, and no price at all.
        # THREE SHAPES, one cause: a page the reader could not follow.
        #
        # The third is the general case the first two are instances of -- a row carrying
        # NO property fact at all. Not a price, not a lot number, not a bedroom count,
        # not a land or house size. Six rows in the whole catalogue are in that state and
        # every one is a sentence: "OUR PROMISE At Strike Developments, we are dedicated
        # to delivering more than just a home", "We can convert our 2-Part packages from
        # our stock list into a 1-Part Contract", and four attribute lines off sideways
        # tables. A property has at least one of those five facts; a paragraph has none.
        _no_facts = not (r["price"] or r["lot_number"] or r["bedrooms"]
                         or r["land_sqm"] or r["house_sqm"])
        if not (_SPEC_FRAGMENT.match(text) or _is_transposed_row(text) or _no_facts):
            continue
        if r["lot_number"] or r["bedrooms"] or r["price"]:
            continue
        _sub, why = suburb_quality.resolve(dict(r, suburb_source=""))
        if suburb_quality.is_searchable(why):
            continue
        out.append(r)
    return out


# Sources that RE-LIST everything they hold on every run. For these, a listing dropping
# off the list is the source telling us it is gone. An inbox is not one of them: a builder
# emails a price list when they have one, so silence there means nothing at all.
# Channels that publish a PAGE per lot rather than a document listing many. Their
# re-listing unit is the portal, not the url: Hermitage and Torsion give every listing its
# own page, so grouping by url made 280 live listings into groups of one -- and a group of
# one can never be behind its own newest read, so retirement could never fire for them.
_PAGE_PER_LOT = frozenset({"Direct Builder Portal (live)"})

RELISTING_CHANNELS = frozenset({
    "E-Agent", "Proxima", "Direct Builder Portal (live)",
    # E-Agent's Stocklist search (7 Oct 2026) lists every package it holds on every read,
    # all from one endpoint -- one document, so a lot it stops listing is retired here and
    # a half-read list is caught by partial_reads like any other.
    "E-Agent Stocklist",
})
# How long a listing must be absent from its OWN list before we take the hint.
WITHDRAWN_AFTER_DAYS = 14
# ...and how recently that list must have been read for its silence to mean anything.
SOURCE_READ_WITHIN_DAYS = 3


def _as_date(value):
    try:
        d, m, y = str(value or "").split("/")
        return datetime(int(y), int(m), int(d))
    except Exception:                                      # noqa: BLE001
        return None


def _col(row, name):
    """row[name], or None when the row simply has no such column.

    Rows arrive here as sqlite3.Row in production and as plain dicts from the tests, and
    the two raise different exceptions for a missing column. A repair that reasons about
    an OPTIONAL field should not care which.
    """
    try:
        return row[name]
    except (KeyError, IndexError):
        return None


def _by_document(rows):
    """Live re-listing rows grouped by the document they were read out of.

    The row's own document. source_doc is a BUILD-time field (build_web hashes the url
    into the snapshot); in the database the document is source_url, with the stocklist
    file behind it, and a channel+builder pair as the last resort for a portal that has
    no per-document URL at all.

    A PORTAL PROJECT IS ITS OWN LIST. Every Proxima row stores the same source_url --
    the projects index -- so without source_project_id in the key all 3,928 of them are
    treated as ONE list, and one project going quiet is read as evidence about the other
    thirty-nine. That is exactly the "a channel is far too broad" error the retirement
    repair says it avoids, and it had already retired 151 Proxima listings on it. The id
    is stored on every one of those rows; it was simply not being used.

    Shared by the two repairs that reason about a source going quiet about one row, so
    they can never disagree about which rows share a list.
    """
    groups = collections.defaultdict(list)
    for r in rows:
        if r["superseded_by"] or str(r["source_channel"] or "") not in RELISTING_CHANNELS:
            continue
        key = (str(r["source_url"] or "").strip()
               or str(r["stocklist_file"] or "").strip()
               or "%s|%s" % (r["source_channel"] or "", r["builder_name"] or ""))
        project = str(_col(r, "source_project_id") or "").strip()
        groups[(key, project) if project else (key, "")].append(r)

    # A URL BEHIND A SINGLE LISTING IS NOT A LIST.
    #
    # Hermitage and Torsion give every listing its own page, so each one formed a group of
    # ONE -- and a group of one can never be behind its own newest read. Retirement could
    # therefore never fire for 280 live listings however long the portal ignored them,
    # which is why Torsion lots unseen for 21 days were still being shown while the
    # Settings tab said "ONLY PART OF ITS STOCK".
    #
    # The re-listing unit for those is the PORTAL: it publishes its whole stock each run,
    # so the channel-and-builder key the fallback above already uses for a row with no url
    # is the right one for a url that backs only one row. Regrouping rather than widening
    # the key wholesale, so a real multi-row document is untouched.
    regrouped = collections.defaultdict(list)
    for key, members in groups.items():
        if len(members) == 1 and str(members[0]["source_channel"] or "") in _PAGE_PER_LOT:
            # ONLY where the channel is a portal that pages every lot separately. A
            # SPREADSHEET that happens to hold one row is still its own document, and
            # pooling two of those from one builder is the "a channel is far too broad"
            # error this module exists to avoid -- one sheet going quiet would retire the
            # other's stock.
            r = members[0]
            key = ("%s|%s" % (r["source_channel"] or "", r["builder_name"] or ""), "")
        regrouped[key].extend(members)
    return regrouped


# A list that comes back missing this much of itself has not been read, whatever it
# returned. Fitted to the shape of the real data on 9 Sep 2026, where the honest lists sat
# at 2-27% missing and the broken ones at 42-96% -- there is a wide gap between them, so
# the exact figure inside it is not load-bearing.
PARTIAL_READ_SHARE = 40
# Below this a percentage says nothing: a 3-row list losing one lot is 33% "missing" and
# is simply a lot that sold.
PARTIAL_READ_MIN_ROWS = 10


def partial_reads(rows, today=None):
    """Lists whose latest read came back missing so much of themselves it cannot be real.

    Returns (key, missing, total) per list. Reported rather than silently skipped: a
    source that has quietly stopped returning its stock is a thing to go and fix, and the
    nightly log is where it gets noticed.
    """
    today = today or datetime.now()
    out = []
    for key, members in _by_document(rows).items():
        if len(members) < PARTIAL_READ_MIN_ROWS:
            continue
        dates = [d for d in (_as_date(r["date_checked"]) for r in members) if d]
        if not dates:
            continue
        newest = max(dates)
        if (today - newest).days > SOURCE_READ_WITHIN_DAYS:
            continue
        missing = sum(1 for r in members
                      if (_as_date(r["date_checked"]) or newest) < newest)
        if missing * 100 // len(members) >= PARTIAL_READ_SHARE:
            out.append((key, missing, len(members)))
    return out


def find_withdrawn_from_source(rows, today=None):
    """Listings whose own source has gone on re-listing everything except them.

    THE APP WAS SHOWING PROPERTIES THAT HAD SOLD. Colin, 7 Sep: he searched Geelong at
    $650-750k, the app offered a DBN Homes lot at $668,000, and the builder's live list
    had a different property on that row at $722,000 marked sold. The $668,000 one was not
    on the list at all. It had gone, and we were still advertising it.

    These portals re-list their whole stock every run, so a listing DISAPPEARING is the
    only "sold" signal they ever send. Nothing here acted on it, so a sold lot stayed live
    forever: 1,418 listings across 49 builders, 60 of them DBN Homes.

    THE GATE IS THE ROW'S OWN LIST, not its channel. A channel is far too broad -- E-Agent
    covers a hundred documents, and one of them going quiet says nothing about the rest.
    So rows are grouped by the document they came from, and a row is only withdrawn when
    THAT document has been read within SOURCE_READ_WITHIN_DAYS and has gone
    WITHDRAWN_AFTER_DAYS without including this row. On the DBN list that is exactly the
    shape: 287 of its rows were re-listed four days ago and 61 were not.

    A HALF-READ LIST IS NOT A SALE. The evidence is "the source published its stock and
    left this one out", which only holds if the source really did publish its stock. One
    Proxima project came back with 3 of its 85 lots on 9 Sep -- the accordion's 80-lot
    render cap, or a dropped session -- and the fortnight rule would have quietly retired
    the other 82 as sold. So a list that comes back missing PARTIAL_READ_SHARE of itself
    is treated as a failed read and retires nothing, and says so out loud rather than
    passing in silence: see partial_reads().

    Reversible on purpose, and reversible in fact since 9 Sep. The row is superseded,
    never deleted, and record_building clears the marker the next time the source lists
    the lot again (database.WITHDRAWN_FROM_SOURCE). Before that it was one-way -- the
    docstring claimed a recovery that no code performed.
    """
    today = today or datetime.now()
    out = []
    partial = {k for k, _n, _t in partial_reads(rows, today)}
    for key, members in _by_document(rows).items():
        if key in partial:
            continue                       # a failed read, not a list of what sold
        dates = [d for d in (_as_date(r["date_checked"]) for r in members) if d]
        if not dates:
            continue
        newest = max(dates)
        if (today - newest).days > SOURCE_READ_WITHIN_DAYS:
            continue                       # the list itself is stale; its silence proves nothing
        for r in members:
            seen = _as_date(r["date_checked"])
            if seen and (newest - seen).days >= WITHDRAWN_AFTER_DAYS:
                out.append((r, (newest - seen).days))
    return out


def _has_position(row) -> bool:
    """True when the row names a line or page inside its document."""
    for c in ("source_row", "source_page"):
        try:
            if int(row[c] or 0) > 0:
                return True
        except (TypeError, ValueError, IndexError, KeyError):
            continue
    return False


def find_stale_source_position(rows, today=None):
    """Rows carrying a row number their own document no longer confirms.

    Coleen, 9 Sep: she clicked "Price list — row 24" and a DIFFERENT property opened.
    Third report of the same thing. The listing was real and the pointer had been right
    when it was written on 2 Sep; Tomorrow Homes re-sorts that workbook as stock sells, so
    by the 9th row 24 held a Woodlea lot at Aintree instead.

    A row number is a POSITION, and a position is only true for the read that produced it.
    database.py now overwrites it on every read, so a row the harvest keeps finding stays
    correct by itself. This is the other half: a row the harvest STOPPED finding. Its
    document was re-read and it was not in it, so the stored number describes a line that
    now belongs to someone else, and it will keep describing one for the fourteen days
    find_withdrawn_from_source waits before retiring the listing.

    Ahsan, 9 Sep: "you should verify the row every day and if it is removed from that row
    it means it is sold or removed." Removal is acted on in two steps, because the two
    claims do not carry the same risk. Saying "this lot is gone" on one missed read would
    delete live stock over a single partial parse, so retirement still waits. Saying
    "row 24" when today's read of that file did not put it there is simply false now, and
    costs nothing to stop saying -- provenance falls back to the plain "Price list" label
    and the lot number to search for.

    Cleared, never guessed at. We do not know which line the lot moved to; we know we can
    no longer name one. The next read that finds the row writes the position back.
    """
    today = today or datetime.now()
    out = []
    for key, members in _by_document(rows).items():
        dates = [d for d in (_as_date(r["date_checked"]) for r in members) if d]
        if not dates:
            continue
        newest = max(dates)
        if (today - newest).days > SOURCE_READ_WITHIN_DAYS:
            continue                    # nothing re-read this file lately; it proves nothing
        for r in members:
            if not _has_position(r):
                continue                # no pointer to be wrong about
            seen = _as_date(r["date_checked"])
            if seen and seen < newest:
                out.append(r)
    return out


# The words a price list uses to say a lot cannot be bought, matched only as whole words
# so "The Reserve" is not "Reserved" and "Settlers Run" is not "settled".
_SOLD_WORD = re.compile(
    r"\b(sold|on\s*hold|under\s*offer|reserved|withdrawn|deposit\s*taken|settled)\b",
    re.I)
# A negation in front of it turns the sentence inside out. One Hattan Homes row stores an
# email sentence as its address -- "it is not on hold exclusively or at this stage sold" --
# and reading the words alone marks a lot On Hold that the builder is saying is NOT.
_NEGATED = re.compile(
    r"\b(not|isn'?t|nor|never|no\s+longer\s+than)\s+(?:\w+\s+){0,2}?"
    r"(sold|on\s*hold|under\s*offer|reserved|withdrawn)\b", re.I)

# How the word found is spelled back into availability_status. The published filter
# lowercases before testing, so only the value matters, not its case -- but a consultant
# reads this column, so it is written the way the rest of the column is written.
_STATUS_FOR = {"sold": "Sold", "on hold": "On Hold", "onhold": "On Hold",
               "under offer": "Under Offer", "underoffer": "Under Offer",
               "reserved": "Reserved", "withdrawn": "Withdrawn",
               "deposit taken": "Deposit Taken", "deposittaken": "Deposit Taken",
               "settled": "Settled"}


# The function words English sentences string together and a table row never does. A
# price list line is "West 9 Darling Darley Sold Barcelona 23 Elevate $365,500" -- nouns,
# numbers and money, with almost none of these in it.
_FUNCTION_WORD = re.compile(
    r"\b(we|our|you|your|they|have|has|is|are|was|were|but|and|or|this|that|these|"
    r"those|please|let|know|would|will|can|could|the|of|to|for|with|at|from|about)\b",
    re.I)
_PROSE_MIN_WORDS = 10
_PROSE_MIN_FUNCTION_WORDS = 6


def find_project_oversupply(rows, today=None):
    """Portal lots we show as available that the project itself says are not.

    THE PORTAL COUNTS ITS OWN STOCK. Every Proxima project title carries a live counter --
    "275 Twelfth Ave Austral (6/6)" -- and sources/proxima.py stores the pair as
    project_available / project_total. 49 of the 123 projects with live stock state it.
    Nothing has ever read it back.

    Damac Properties, project 587: 231 lots on the board, all shown as available. The
    project's own header says 129 are. A hundred and two properties were being offered to
    clients that the portal itself says are gone.

    TWO INDEPENDENT SIGNALS, AND THEY AGREE. The header gives a NUMBER. The harvest gives
    IDENTITIES: rows the project has gone on re-listing without. Across the seven projects
    showing more than they state, the excess is 130 rows and 128 of them are also rows the
    latest read did not carry -- and on five of the seven the two counts match exactly.
    A count from the portal's own header and a set of rows from a different mechanism
    landing on the same lots is about as close to proof as this data offers.

    So a lot is marked only when BOTH say so: its project states a shortfall, AND that lot
    is one the project stopped re-listing. Neither alone is enough -- the header could be
    stale, and a single missed read is not a sale.

    CAPPED AT THE STATED SHORTFALL, oldest sighting first. Project 587 has 135 rows the
    latest read missed but states only 102 too many; marking all 135 would hide 33 lots
    that are for sale. The portal's own number is the ceiling, never the floor.
    """
    today = today or datetime.now()
    out = []
    projects = collections.defaultdict(list)
    for r in rows:
        if r["superseded_by"] or str(r["source_channel"] or "") != "Proxima":
            continue
        pid = str(_col(r, "source_project_id") or "").strip()
        if pid:
            projects[pid].append(r)

    for members in projects.values():
        stated = _col(members[0], "project_available")
        if stated is None:
            continue                       # the project never said; infer nothing
        try:
            stated = int(stated)
        except (TypeError, ValueError):
            continue
        shown = [r for r in members if _is_shown(r)]
        excess = len(shown) - stated
        if excess <= 0:
            continue                       # we show no more than the portal offers
        dates = [d for d in (_as_date(r["date_checked"]) for r in members) if d]
        if not dates:
            continue
        newest = max(dates)
        if (today - newest).days > SOURCE_READ_WITHIN_DAYS:
            continue                       # nobody has read this project lately
        missed = [(d, r) for r in shown
                  for d in [_as_date(r["date_checked"])] if d and d < newest]
        missed.sort(key=lambda pair: pair[0])          # least recently seen first
        out.extend(r for _d, r in missed[:excess])
    return out


def _is_shown(row) -> bool:
    """Whether this row currently reaches the client-facing filter.

    Asks api/_candidates.py, the set the published app actually filters on, rather than
    keeping a fourth copy of it here. Copies of this list have drifted before: "not
    available" was missing from one of them while it was the literal value on 581 rows,
    and a sold Rouse Hill lot was recommended to Colin on 5 Aug. api/ is not on the path
    when this module runs as a script from the app directory, so it is added here.
    """
    global _NOT_AVAILABLE
    if _NOT_AVAILABLE is None:
        api = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api")
        if api not in sys.path:
            sys.path.insert(0, api)
        from _candidates import NOT_AVAILABLE
        _NOT_AVAILABLE = NOT_AVAILABLE
    return str(row["availability_status"] or "").strip().lower() not in _NOT_AVAILABLE


_NOT_AVAILABLE = None


def find_prose_rows(rows):
    """Rows whose address is a SENTENCE somebody wrote, not a property.

    Four live rows are email bodies that the digital-email reader stored as listings, and
    three of them carry a price -- $553,284, $713,118, $600,000 -- so they were being
    offered to clients as stock:

        "We have a couple of partners presenting this lot but it is not on hold..."
        "Hi Cheryl, We are interested in the property at Lot 1052, Westwood Walk..."
        "Stage 4 and lot 7- what is the full Turnkey package..."
        "OUR PROMISE At Strike Developments, we are dedicated to delivering..."

    The first of those is also why find_sold_word_without_status has a negation guard: it
    contains the words "on hold" and "sold" inside a sentence that says the lot is neither.
    Reading it as a status was the wrong fix for it. It is not a listing at all.

    THE TEST IS SHAPE, NOT SUBJECT. Ten or more words of which six or more are English
    function words. A price-list line is nouns, numbers and money and scores nearly zero,
    so this catches exactly those four rows and nothing else across 10,876 live listings.
    """
    out = []
    for r in rows:
        if r["superseded_by"]:
            continue
        text = str(r["lot_address"] or "")
        if len(text.split()) < _PROSE_MIN_WORDS:
            continue
        if len(_FUNCTION_WORD.findall(text)) >= _PROSE_MIN_FUNCTION_WORDS:
            out.append(r)
    return out


def find_sold_word_without_status(rows):
    """Rows that SAY sold where a person reads, while the status column says nothing.

    Ahsan, 7 Sep: "I want you to see that there are not any buildings that sold or not
    available. Show up in the the filter because I see". He was right and I twice measured
    the wrong column: availability_status was clean, and the word SOLD was sitting in the
    address, which is what he could see on screen.

    THE EVIDENCE THAT THIS IS SAFE. 132 live rows carry one of these words in the text a
    person reads. 129 of them ALREADY hold the matching availability_status -- Sold, On
    Hold, Under Offer, Reserved -- so on 98% of the rows where it can be checked, the word
    in the line and the status column agree. The remaining 3 are rows where the status
    extraction missed and the word is the only statement we have.

    So the word is taken as the statement, and written into the column that hides the lot.
    Not "Not Available": the specific word, because a consultant reads this column and
    "Sold" and "On Hold" are different things to tell a client.
    """
    out = []
    for r in rows:
        if r["superseded_by"]:
            continue
        if str(r["availability_status"] or "").strip():
            continue                      # the row already states one; never overwrite it
        for col in ("lot_address", "estate_name"):
            text = str(r[col] or "")
            match = _SOLD_WORD.search(text)
            if not match or _NEGATED.search(text):
                continue
            word = re.sub(r"\s+", " ", match.group(1)).strip().lower()
            status = _STATUS_FOR.get(word) or _STATUS_FOR.get(word.replace(" ", ""))
            if status:
                out.append((r, status))
            break
    return out


def find_tally_estates(rows):
    """Rows whose estate name is a running count, not a place.

    Eastbury's price list prints "12 SOLD" and "24 SOLD" between its stages, and the
    section test read them as estate names. 93 live listings were published with an estate
    of "12 SOLD" — every one of them AVAILABLE, at $1,299,000 and up — so the word SOLD sat
    in the estate column of stock that is very much for sale.

    That is what a person scanning the table reacts to, and it is why the app looked like
    it was offering sold property while the availability filter was working perfectly:
    nothing marked unavailable was ever shown, but the word was on screen anyway.

    Cleared rather than corrected. The tally says nothing about which estate the lot is in,
    and a blank estate is honest where an invented one is not.
    """
    from sources.spreadsheet_extract import _A_STATUS_WORD, _A_TALLY

    def _is_a_tally(name):
        # Either shape: a count ("12 SOLD", "4.01 RESERVED"), or a whole status COLUMN
        # flattened onto one line ("Sale Status SOLD AVAILABLE SOLD AVAILABLE SOLD"),
        # which named the estate of three available lots. A place is named once.
        return (bool(_A_TALLY.fullmatch(name))
                or len(_A_STATUS_WORD.findall(name)) >= 2)

    return [r for r in rows
            if not r["superseded_by"]
            and str(r["estate_name"] or "").strip()
            and _is_a_tally(str(r["estate_name"]).strip())]


def status_pass_keepers(rows):
    """{id: the hash its stale copies should point at} for rows supersede's status pass
    retired tonight. See supersede_duplicates.status_pass_marks.

    These rows still count as members of a duplicate line or a suburb-twin group, as they
    did when they were live, so a copy they used to keep out stays out. They are never
    retired again here (their own mark stands), and a copy retired in their favour points
    at the END of their superseded_by chain -- the capture that is live -- not at them.

    Never raises: on any error this is {} and both passes behave as they did before the
    status pass existed (repair_rows is an optional step, but a crash would cost every
    other repair of the night).
    """
    try:
        import supersede_duplicates
        marks = supersede_duplicates.status_pass_marks(rows)
    except Exception as exc:                                         # noqa: BLE001
        print(f"   (status-pass retirements could not be worked out: {type(exc).__name__}; "
              f"twins and duplicates are grouped over live rows only)")
        return {}
    by_hash = {}
    for r in rows:
        h = str(r["content_hash"] or "")
        if h and h not in by_hash:
            by_hash[h] = r
    out = {}
    for rid, target in marks.items():
        seen = set()
        # Follow the chain while it stays on content hashes; a rule marker ("an email
        # sentence, not a listing") or a live row ends it.
        while target in by_hash and target not in seen:
            seen.add(target)
            nxt = str(by_hash[target]["superseded_by"] or "")
            if not nxt.startswith("v2:"):
                break
            target = nxt
        out[rid] = target
    return out


def _keeper_mark(keeper, status_keepers):
    """What a copy retired in favour of `keeper` points at."""
    if keeper["id"] in (status_keepers or {}):
        return status_keepers[keeper["id"]]
    return keeper["content_hash"] or str(keeper["id"])


def find_duplicate_groups(rows, status_keepers=None):
    """Groups of live rows sharing a source_text, reduced to strictly redundant copies.

    status_keepers ({id: hash}, status_pass_keepers) are rows the status pass retired
    tonight: grouped as though live, so they can still be the keeper; never a loser."""
    status_keepers = status_keepers or {}
    by = collections.defaultdict(list)
    for r in rows:
        if r["superseded_by"] and r["id"] not in status_keepers:
            continue
        text = _line_identity(r["source_text"])
        # THE LINE ALONE, across documents. Keying on (source_url, text) was a caution
        # too far and it disabled the case this function was written for -- see the
        # docstring above: the same row reaching us through more than one channel, an
        # e-agent file and the emailed price list. 11 unnamed rows have a byte-identical
        # twin in another document that IS named, "Available Officer Q3 2027 House Lot
        # 152 400 25.2 Porter 13 Single Storey" on both sides, and the pair could not
        # meet. `subsumed_by` still decides whether either copy loses anything.
        if text:
            by[text].append(r)
    groups = []
    for text, g in by.items():
        if len(g) < 2:
            continue
        g = sorted(g, key=_completeness, reverse=True)
        keeper, rest = g[0], g[1:]
        redundant = [r for r in rest
                     if r["id"] not in status_keepers and subsumed_by(r, keeper)]
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


def _suburb_evidence(row) -> int:
    """How strong the evidence behind this row's suburb is. Higher wins.

    The order is suburb_quality's own, not a new opinion:
      4  the row's own lot_address repeats it -- two fields agreeing
      3  resolve() calls it STATED, which means only that the column held a locality
      2  it was READ -- off the row's address, its postcode, its document's front page
      1  it was INFERRED from the row's other text, or could not be checked for want of
         a state
      0  the app shows nothing at all

    find_suburbs_in_own_document already turns on exactly this distinction -- "A DOCUMENT
    THAT NAMES THE SUBURB BEATS A GUESS AT IT ... Re-reading it replaces a reading with a
    statement". Two captures of one lot disagreeing is not a coin toss when one of them is
    the source's own word and the other is our reading of a road name.
    """
    try:
        import suburb_quality as SQ
    except ImportError:                                # pragma: no cover
        return 1 if str(row["suburb"] or "").strip() else 0
    shown, why = SQ.resolve(dict(row, suburb_source=""))
    shown = str(shown or "").strip()
    if not shown:
        return 0
    # THE ROW'S OWN ADDRESS IS THE STRONGEST THING IT HAS, and it outranks the column.
    #
    # resolve() returns STATED for ANY value in the suburb column that happens to be a
    # locality in that state -- it makes no claim about where the value came from, and
    # the docstring at the top of suburb_quality says what that column actually holds:
    # OCR debris, section headers, "Price", "City", "Haven". So a section header carried
    # onto the wrong block of rows scored 3 and beat an address read at 2.
    #
    # It did: one E-Agent PDF put "Manor Lakes" on three lots whose own addresses read
    # Bairnsdale, Truganina and Donnybrook -- up to 280 km apart, so at most one can be
    # right. This rule kept all three and superseded the address-evidenced copies. 24 of
    # 60 supersessions on 10 Sep 2026 had that shape, caught in review before deploy.
    #
    # A suburb the row's OWN address repeats is corroborated by a second field; a column
    # value contradicted by that address is not. That is the same reasoning suburb_quality
    # applies in FROM_ADDRESS, applied one level up.
    if shown.lower() in str(_col(row, "lot_address") or "").lower():
        return 4
    if why in (SQ.STATED, SQ.FROM_TYPO):
        return 3
    return 2 if SQ.is_located(why) else 1


def _shows_a_suburb(row) -> bool:
    """Whether the app displays a suburb for this row at all.

    Not `row["suburb"]` -- build_web publishes suburb_quality.resolve()'s answer, which
    refuses a value that is not a locality. A row storing "Truganina Montare Road,
    Truganina VIC" shows the client nothing, and is a blank row wearing a full column.
    """
    # ALWAYS ASK resolve, even for an empty column. Returning False early on a blank
    # disagreed with _suburb_evidence, which asks resolve and can be handed a suburb
    # rescued from the row's own address -- so one helper called a row blank while the
    # other scored it as located, and the pair of them decided who to supersede.
    return _suburb_evidence(row) > 0


def find_suburb_twins(rows, status_keepers=None):
    """One package stored twice because it only recently started stating its suburb.

    status_keepers ({id: hash}, status_pass_keepers): rows supersede's status pass retired
    tonight, grouped as though live so a twin they kept retired stays retired, and never
    retired again here.

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

    BLANK MEANS BLANK ON SCREEN, NOT EMPTY IN THE COLUMN. The older copy usually does hold
    something -- it is what the extractor of the day made of the line. Lot CC-0116 stores
    "Truganina Montare Road, Truganina VIC" against the newer copy's "Manor Lakes":
    suburb_quality refuses the first outright, so the client is shown one lot twice, once
    with a suburb and once with nothing. Requiring the column to be literally empty missed
    every one of those -- 31 groups, 62 rows, on 10 Sep 2026. The test is what the app
    SHOWS, which is the same test the published snapshot applies, and it is the same
    widening find_suburbs_in_own_document already makes for the same reason.
    """
    status_keepers = status_keepers or {}
    by = collections.defaultdict(list)
    for r in rows:
        if r["superseded_by"] and r["id"] not in status_keepers:
            continue
        by[_identity_without_suburb(r)].append(r)

    pairs = []
    for _, g in by.items():
        if len(g) < 2:
            continue
        named = [r for r in g if _shows_a_suburb(r)]
        blank = [r for r in g if not _shows_a_suburb(r)]
        if not named:
            continue
        # Where the named copies disagree about the suburb there is no single keeper to
        # point the blank ones at -- UNLESS one of them is the source's own word and the
        # rest are our reading of it.
        #
        # Lot CC-0116 is two live rows at one price: a 6 Aug capture showing "Truganina",
        # read off the road name in its own address, and a 3 Sep capture showing "Manor
        # Lakes", stated by the source. Both are "located", so both counted as named and
        # the pair was left alone -- and Coleen is shown one property twice. 31 groups on
        # 10 Sep 2026.
        #
        # Deferring to the statement is not picking a winner between two opinions; it is
        # the rule find_suburbs_in_own_document already applies. Where the strongest
        # evidence is not unique, or the strongest rows still disagree, nothing is
        # collapsed and the disagreement stands.
        if len({_norm(r["suburb"]) for r in named}) > 1:
            best = max(_suburb_evidence(r) for r in named)
            strongest = [r for r in named if _suburb_evidence(r) == best]
            weaker = [r for r in named if _suburb_evidence(r) < best]
            if not weaker or len({_norm(r["suburb"]) for r in strongest}) > 1:
                continue
            named, blank = strongest, blank + weaker
        if not blank:
            continue                       # one capture only, or all equally well evidenced
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
        stale = [r for r in blank
                 if _iso(r["date_checked"]) < cut and r["id"] not in status_keepers]
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
            # A DOCUMENT THAT NAMES THE SUBURB BEATS A GUESS AT IT.
            #
            # This kept any value today's resolve accepts, INCLUDING an inferred one, and
            # that left the Eternal Homes rows sitting at 83-94% when their own price
            # list states the answer outright. Those rows store "Dual Key" -- the TYPE
            # column -- and resolve rescues them by finding "Millfield" elsewhere in the
            # row's text, so they read correctly and are refused for geocoding and
            # benchmarking. The document has a column headed SUBURB. Re-reading it
            # replaces a reading with a statement.
            #
            # A LOCATED value is still never touched: only blank, refused, or inferred.
            if suburb_quality.is_located(why):
                continue                       # evidence already; nothing to improve
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
            # ONLY A VALUE THAT STANDS UP. The re-read offered 'Whiterock' (an estate),
            # 'Subury' (the document's own misspelling) and four rows of 'Warnervale'
            # with no state to check it against -- five of seven candidates were values
            # suburb_quality refuses, which is the exact condition that qualified those
            # rows for repair in the first place. Writing them would swap one refused
            # value for another and call it a fix.
            # THE RE-READ MUST BE EVIDENCE, NOT JUST DIFFERENT.
            #
            # Widening this to upgrade an INFERRED value made the bar matter. Re-reading
            # every document proposed 378 changes, and 260 of them were not localities at
            # all -- 'COAST COUNCIL', 'Whiterock', and 'Dual Key' from the very TYPE
            # column this run was meant to stop publishing. 63 would have REPLACED a
            # correct suburb with one: Millfield -> Dual Key, Lochinvar -> Dual Key.
            #
            # is_searchable was too weak a test, because it accepts an inferred reading
            # of the new value. A repair that swaps one guess for another is not a
            # repair. The re-read is taken only when it makes the row LOCATED.
            if not suburb:
                continue
            _why_new = suburb_quality.resolve(dict(r, suburb=suburb, suburb_source=""))[1]
            if not suburb_quality.is_located(_why_new):
                continue
            # WHERE THE ROW ALREADY READS AS SOMEWHERE, THE DOCUMENT MUST AGREE.
            #
            # A blank row has nothing to lose and takes the document's answer. A row that
            # already shows an inferred suburb is a different case: the re-read is only
            # allowed to upgrade the EVIDENCE, not to change the answer. Of 118 re-reads,
            # 9 disagreed and every one of them was a loss -- 'Seacliff' -> 'Adelaide'
            # trades a suburb for a city, 'Bald Hills' -> 'Ripley' is 40 km. A
            # disagreement between a document and a row is a data question and it is not
            # settled by whichever ran last.
            _shown = suburb_quality.resolve(dict(r, suburb_source=""))[0]
            if _shown and _shown.strip().lower() != suburb.strip().lower():
                continue
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

    # Rows supersede's status pass retired tonight still hold their twins and duplicate
    # lines retired (status_pass_keepers), as they did while they were live.
    status_keepers = status_pass_keepers(rows)
    grouped = live + [r for r in rows if r["id"] in status_keepers and r["superseded_by"]]
    if status_keepers:
        print(f"   ({len(status_keepers)} row(s) the status pass retired tonight still keep "
              f"their duplicates and twins retired)")
    groups = find_duplicate_groups(grouped, status_keepers)
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

    refuted = find_refuted_states(live)
    print(f"\n8. states refuted by the row's own suburb and its document: {len(refuted)}")
    for r in refuted[:3]:
        print(f"     {(r['builder_name'] or '?')[:22]:22} suburb={r['suburb']!r} "
              f"state={r['state']!r} -> state cleared")

    controls = find_control_rows(live)
    withdrawn = find_withdrawn_from_source(rows)
    tallies = find_tally_estates(rows)
    stale_pos = find_stale_source_position(rows)
    half_read = partial_reads(rows)
    oversupply = find_project_oversupply(rows)
    print(f"\n15. portal lots we show that the project says are gone: {len(oversupply)}")
    for r in oversupply[:4]:
        print(f"     {str(r['builder_name'] or '?')[:24]:26} last seen {r['date_checked']}"
              f"   {str(r['lot_address'] or '')[:38]}")
    prose = find_prose_rows(rows)
    print(f"\n14. email sentences stored as listings: {len(prose)}")
    for r in prose[:4]:
        print(f"     ${float(r['price'] or 0):>9,.0f}  {str(r['lot_address'])[:58]!r}")
    sold_words = find_sold_word_without_status(rows)
    print(f"\n13. rows that SAY sold where a person reads, with no status set: "
          f"{len(sold_words)}")
    for r, status in sold_words[:5]:
        print(f"     -> {status:14} {str(r['builder_name'] or '?')[:18]:20} "
              f"{str(r['lot_address'] or r['estate_name'] or '')[:44]}")
    print(f"\n12. lists that came back missing most of themselves "
          f"(retiring nothing from these): {len(half_read)}")
    for key, missing, total in sorted(half_read, key=lambda x: -x[1])[:6]:
        print(f"     {missing:4}/{total:<4} ({missing*100//total:>2}% missing)  "
              f"{str(key[0])[:56]}")
    print(f"\n11. row numbers their own document no longer confirms: {len(stale_pos)}")
    for r in stale_pos[:4]:
        print(f"     {str(r['builder_name'] or '?')[:22]:<24} row {r['source_row']} "
              f"read {r['date_checked']}   {str(r['lot_address'] or '')[:44]}")
    print(f"\n8. spreadsheet filter rows stored as listings: {len(controls)}")
    print(f"\n10. estate names that are a sold tally, not a place: {len(tallies)}")
    print(f"\n9. listings their own source has stopped re-listing: {len(withdrawn)}")
    for r, silent in sorted(withdrawn, key=lambda x: -x[1])[:6]:
        print(f"     {str(r['builder_name'] or '?')[:24]:<26} "
              f"silent {silent} day(s)   {str(r['lot_address'] or '')[:48]}")
    for r in controls[:3]:
        print(f"     {str(r['builder_name'] or '?')[:24]:<26} {str(r['source_text'])[:58]!r}")
    fragments = find_spec_fragments(live)
    print(f"\n7. spec-table fragments stored as listings: {len(fragments)}")
    for r in fragments[:3]:
        print(f"     ${float(r['price'] or 0):>10,.0f}  {(r['builder_name'] or '?')[:20]:20} "
              f"{str(r['lot_address'])[:52]}")

    twins = find_suburb_twins(grouped, status_keepers)
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
                        (_keeper_mark(keep, status_keepers), now, r["id"]))
    for r in refuted:
        cur.execute("UPDATE buildings SET state=NULL, state_source=? WHERE id=?",
                    ("withdrawn: refuted by the row's own suburb", r["id"]))
    for r in fragments:
        cur.execute("UPDATE buildings SET superseded_by=?, superseded_at=? WHERE id=?",
                    ("spec-table fragment, not a listing", now, r["id"]))
    for r in controls:
        cur.execute("UPDATE buildings SET superseded_by=?, superseded_at=? WHERE id=?",
                    ("spreadsheet filter row, not a listing", now, r["id"]))
    for r in tallies:
        cur.execute("UPDATE buildings SET estate_name=NULL WHERE id=?", (r["id"],))
    for r in oversupply:
        # The portal's own count says this lot is gone and the project stopped listing it.
        # Not superseded: the row is real and may come back, and availability is the
        # reversible statement -- the next read that carries the lot writes it back.
        cur.execute("UPDATE buildings SET availability_status=?, status_previous=? "
                    "WHERE id=?", ("Sold", r["availability_status"], r["id"]))
    for r in prose:
        cur.execute("UPDATE buildings SET superseded_by=?, superseded_at=? WHERE id=?",
                    ("an email sentence, not a listing", now, r["id"]))
    for r, status in sold_words:
        # The word the row itself carries, not a blanket "Not Available": a consultant
        # reads this column and "Sold" and "On Hold" are different things to tell a client.
        cur.execute("UPDATE buildings SET availability_status=? WHERE id=?",
                    (status, r["id"]))
    for r in stale_pos:
        # The position only. The listing itself is untouched -- it may well still be for
        # sale, just no longer on the line we wrote down.
        cur.execute("UPDATE buildings SET source_row=NULL, source_page=NULL, "
                    "source_sheet=NULL WHERE id=?", (r["id"],))
    for r, _silent in withdrawn:
        # SAY IT IN THE COLUMN A PERSON READS, not only in superseded_by.
        #
        # Ahsan, 9 Sep: "the buildings that are no longer in the sheets are marked sold".
        # Superseding already hides the row from the filter, which is most of what he
        # asked for -- but anyone who ticks "show superseded", opens the export, or reads
        # the row through the API still saw it carrying "Available", which is the one
        # thing it is not. The previous value is kept in status_previous, as it is for
        # every other status change, so an incorrect retirement is legible afterwards and
        # not just reversible.
        cur.execute("UPDATE buildings SET superseded_by=?, superseded_at=?, "
                    "availability_status=?, status_previous=? WHERE id=?",
                    (WITHDRAWN_FROM_SOURCE, now, "Sold", r["availability_status"],
                     r["id"]))
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
          f"{len(fragments)} spec fragment(s) withdrawn, "
          f"{len(controls)} filter row(s) withdrawn, "
          f"{len(withdrawn)} sold/withdrawn listing(s) retired, "
          f"{len(tallies)} tally estate name(s) cleared, "
          f"{len(stale_pos)} unconfirmed row number(s) cleared, "
          f"{len(sold_words)} unstated sold/hold status(es) read off the row, "
          f"{len(prose)} email sentence(s) withdrawn, "
          f"{len(oversupply)} portal lot(s) the project says are gone marked Sold, "
          f"{len(refuted)} refuted state(s) cleared, "
          f"{len(reread)} suburb(s) re-read from the row's own document")
    after = len([r for r in _rows(conn) if not r["superseded_by"]])
    print(f"live rows: {len(live)} -> {after}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the corrections")
    sys.exit(main(ap.parse_args().apply))
