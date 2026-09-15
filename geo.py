"""
Suburb Geolocation & Distance Search Module.
Implements the distance-based search requested in the 2026-07-22 client review:
"within N km of a target suburb" when the exact suburb has no stock.

Backed by data/au_suburbs.csv (17,500+ Australian localities with coordinates,
derived from the public australian_postcodes dataset).
"""

import csv
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import PROJECT_ROOT

SUBURBS_CSV = PROJECT_ROOT / "data" / "au_suburbs.csv"

# Words that make the name before them a STREET rather than a locality. Used by
# find_suburb_in_text; see the comment there for the 33 live rows this corrects.
_STREET_TYPE = re.compile(
    r"(?:st|street|rd|road|dr|drive|ave|avenue|av|cres|crescent|cct|circuit|pde|parade|"
    r"way|wy|ct|court|cl|close|pl|place|blvd|boulevard|tce|terrace|gr|grove|gve|lane|ln|"
    r"esp|esplanade|hwy|highway|mews|walk|loop|link|vista|gdns|gardens)",
    re.IGNORECASE)

# HOW A PERSON SAYS A LOCALITY, as opposed to how the gazetteer spells it.
#
# Clare, live call 15 Sep 2026: the caller said "Red Bank Plains". The QLD locality is
# "Redbank Plains", the index was keyed on the exact lower-case name, so locate() said
# None and api/research.py got a search area of ONE suburb instead of the 154 that
# "Redbank Plains" reaches at 20 km. Every nearby listing was then refused with "Location
# could not be verified ... so the 20 km radius from Red Bank Plains cannot be confirmed"
# -- the caller's own suburb, full of stock, reported as a place we could not find.
# Speech arrives through a transcriber, so spacing, hyphens, apostrophes and Mt/Mount,
# St/Saint are the normal case, not the odd one: "Mt Cotton", "Saint Albans",
# "Pointcook", "Wyndham-Vale". The Research tab form takes typed names the same way.
#
# The key folds exactly those differences and nothing else: 'mount' -> 'mt' and
# 'saint' -> 'st' as whole words, then every non-letter goes. It is a FALLBACK, asked
# only when the exact name misses, and it answers only when it names exactly ONE
# locality in the state. 37 keys collide inside a state in data/au_suburbs.csv -- Red
# Head / Redhead (NSW, 100 km apart), Willow Vale / Willowvale (QLD), Crystal Brook /
# Crystalbrook (QLD) and a run of apostrophe and mojibake duplicates -- and every one of
# them stays unmatched rather than guessed.
#
# OPT-IN (spoken=True), and only where the name came from a PERSON: a search origin
# (expand_search_suburbs, suburbs_within_radius), distance_between, and the brief that
# api/research.py respells before scoring. Every other caller hands these methods
# fragments of free text -- n-grams of a stocklist row, a banner, an estate name -- and
# folding there invents places. Measured over vercel_site/stock.json with the fallback on
# for every lookup: 13 rows from one price list whose builder is "Lakeview" became Lake
# View, South Australia, and 415 text n-grams resolved that never had, among them
# "Springridge" x84, "Whiterock" x74 and "Lakeview" x60 -- single-state answers that
# state_resolver would have taken as proof of a row's state. With the default off, every
# answer over stored stock is exactly what it was.
_MOUNT = re.compile(r"\bmount\b")
_SAINT = re.compile(r"\bsaint\b")
_NOT_A_LETTER = re.compile(r"[^a-z]")
_DIGIT = re.compile(r"\d")
# Word breaks for the lone-letter rule in _spoken_hits: anything but a letter or an
# apostrophe (straight or curly), so "Lot-A" is two words and "O'Connor" is one.
_WORD_BREAK = re.compile(r"[^a-z'‘’]+")


def spoken_key(name: str) -> str:
    """'Red Bank Plains', 'Redbank-Plains' -> 'redbankplains'; 'Mount Cotton' -> 'mtcotton'."""
    folded = _SAINT.sub("st", _MOUNT.sub("mt", str(name or "").strip().lower()))
    return _NOT_A_LETTER.sub("", folded)


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    p = math.radians
    a = math.sin(p(lat2 - lat1) / 2) ** 2 + \
        math.cos(p(lat1)) * math.cos(p(lat2)) * math.sin(p(lng2 - lng1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


class SuburbGeoIndex:
    def __init__(self, csv_path: Optional[Path] = None):
        self.csv_path = csv_path or SUBURBS_CSV
        # {(suburb_lower, state): (lat, lng)}
        self._index: Dict[Tuple[str, str], Tuple[float, float]] = {}
        self._by_state: Dict[str, List[Tuple[str, float, float]]] = {}
        # {suburb_lower: {states}} — the reverse lookup, for answering "which state is
        # this suburb in?" when no state is known yet. Roughly a fifth of Australian
        # locality names exist in more than one state, so the caller has to be told when
        # the answer is ambiguous rather than handed the first match.
        self._states_by_suburb: Dict[str, set] = {}
        # {suburb_lower: the dataset's own spelling} — so 'GLENVALE' and 'Glenvale' stop
        # being two suburbs in the facet, the distance lookup and the benchmark cohort.
        # Taken from the locality dataset rather than .title(), which mangles the real
        # names: McKinnon, O'Connor, Kurri Kurri.
        self._canonical: Dict[str, str] = {}
        # {(spoken_key, state): {suburb_lower}} — the fallback described at spoken_key.
        # A SET, not a name, so a key two localities share is visible as ambiguous and
        # refused, instead of quietly answering with whichever row loaded last.
        self._spoken: Dict[Tuple[str, str], set] = {}
        # {(spoken_key, state): {suburb_lower}} for names the STOCK knows as estates, and
        # the suburbs that estate's listings are filed under. Empty until know_estates().
        self._estates: Dict[Tuple[str, str], set] = {}
        self._load()

    def _load(self):
        if not self.csv_path.exists():
            return
        with open(self.csv_path, encoding='utf-8') as f:
            for row in csv.DictReader(f):
                suburb = row['suburb'].strip()
                state = row['state'].strip().upper()
                lat, lng = float(row['lat']), float(row['lng'])
                self._index[(suburb.lower(), state)] = (lat, lng)
                self._by_state.setdefault(state, []).append((suburb, lat, lng))
                self._states_by_suburb.setdefault(suburb.lower(), set()).add(state)
                self._canonical.setdefault(suburb.lower(), suburb)
                self._spoken.setdefault((spoken_key(suburb), state), set()).add(suburb.lower())

    @property
    def loaded(self) -> bool:
        return bool(self._index)

    def know_estates(self, estates) -> None:
        """Tell the spoken fallback which names are ESTATES, and where each one's stock is.

        `estates` is (estate_name, suburb, state) for every live listing. Research tab,
        15 Sep 2026: "Newpark" -- the estate at Melonba, NSW, three live listings --
        folded to New Park, NSW, 604 km away. api/research.py respelt the brief to it,
        searched 43 suburbs around it and measured every lot from there, where the same
        input used to come back as an area of one labelled "Newpark": visibly a place we
        could not find. The gazetteer cannot tell an estate from a spelling variant; the
        stock can.

        So a name whose key is an estate's key stops folding onto any locality that estate
        is not filed under. Whiterock (QLD) still folds to White Rock, which is where its
        listings sit; over vercel_site/stock.json those two are the only estate names that
        fold at all. An estate spelt exactly as a locality of its own state ("Springfield",
        filed under Springfield Lakes) is not recorded: said, that name is the locality.

        REPLACES what was known. research.py calls it on every request with the same
        snapshot, so the answer is the same each time and nothing accumulates.
        """
        known: Dict[Tuple[str, str], set] = {}
        for estate, suburb, state in estates:
            name = str(estate or "").strip().lower()
            st = str(state or "").strip().upper()
            key = spoken_key(name)
            if not key or not st or (name, st) in self._index:
                continue
            filed = known.setdefault((key, st), set())
            where = str(suburb or "").strip().lower()
            if where:
                filed.add(where)
        self._estates = known

    def _estate_elsewhere(self, name: str, state: str, found) -> bool:
        """True when `name` is an estate in `state` whose stock is not filed under `found`."""
        filed = self._estates.get((spoken_key(name), state))
        return filed is not None and not (set(found) & filed)

    def estate_suburbs(self, name: str, state: str) -> Optional[List[str]]:
        """The localities the stock files the estate called `name` under in `state`, in the
        dataset's spelling -- or None when the stock knows no estate by that name there.

        Public for api/_location_resolver.py, which checks a language model's reading of a
        phrase against it. Review, 15 Sep 2026: "Newpark estate" answered "New Park" passed
        locate(), which matches an exact spelling before it ever asks about estates, and
        the search went 604 km from Melonba again. An empty list is an estate whose
        listings carry no suburb: known, and filed nowhere a name could match.
        """
        filed = self._estates.get((spoken_key(name), str(state or "").strip().upper()))
        if filed is None:
            return None
        return sorted(self._canonical.get(s, s.title()) for s in filed)

    def localities(self, state: str) -> List[str]:
        """Every locality of `state`, in the dataset's spelling. Empty for an unknown state.

        Public for api/_location_resolver.py. Live, 15 Sep 2026: gpt-oss-20b at low effort
        answered "Wyndum Vale" unknown and left "Cragieburn" out of its answer, so the
        resolver now looks for the closest spellings itself and hands them to the model.
        """
        return [s for s, _lat, _lng in self._by_state.get(str(state or "").strip().upper(), ())]

    def _spoken_hits(self, suburb: str) -> Dict[str, set]:
        """{state: {suburb_lower}} sharing this name's spoken key. {} if it has none.

        A DIGIT OR A LONE LETTER IS NEVER HOW A NAME IS SAID. Stripping non-letters would
        otherwise turn "Tarneit 3029" or "Stage 5A" into a bare locality key — a second,
        looser postcode rule beside the narrow one resolve_locality already owns — and
        glue fragments into places: in stock.json text "Lot A" folds to Lota (QLD), "NE A"
        to Nea (NSW) and "do n" to Don (TAS). The dataset's own names of that shape ("No 4
        Branch", "O B Flat") still match exactly; they are simply never reached by folding.

        A WORD ENDS AT ANY NON-LETTER BUT AN APOSTROPHE. Split on spaces alone, the same
        fragments came straight back through punctuation: "Lot-A" reached Lota and "Ne-A"
        Nea. The apostrophe stays inside the word so O'Connor and Tam O'Shanter still fold.
        """
        name = str(suburb or "").strip().lower()
        if not name or _DIGIT.search(name):
            return {}
        if any(len(_NOT_A_LETTER.sub("", word)) == 1 for word in _WORD_BREAK.split(name)):
            return {}
        key = spoken_key(name)
        if not key:
            return {}
        hits = {}
        for st in self._by_state:
            found = self._spoken.get((key, st))
            if found:
                hits[st] = found
        return hits

    def _dataset_name(self, suburb: str, state: str, spoken: bool = False) -> str:
        """The index's lower-case name for what was said, in this state, or ''.

        The exact name first, always, so nothing that matched before can match
        differently now. The spoken key is only asked when the caller opted in and the
        exact name missed, and it only answers when it names exactly one locality in the
        state and the name is not an estate filed somewhere else (know_estates).
        """
        name = str(suburb or "").strip().lower()
        st = str(state or "").strip().upper()
        if not name or not st:
            return ""
        if (name, st) in self._index:
            return name
        if not spoken:
            return ""
        found = self._spoken_hits(name).get(st) or ()
        if len(found) != 1 or self._estate_elsewhere(name, st, found):
            return ""
        return next(iter(found))

    def canonical_suburb(self, suburb: str, state: str = "", spoken: bool = False) -> str:
        """The locality dataset's own spelling of this name, or '' if unknown.

        With spoken=True a variant ("Red Bank Plains") gets the dataset's spelling too.
        With a state it resolves exactly as locate() does, and only when nothing in that
        state answers does it fall back to the exact spelling from another state. Without
        a state it folds only when the key names a single locality across every state.
        """
        name = str(suburb or "").strip().lower()
        exact = self._canonical.get(name, "")
        if not name or not spoken:
            return exact
        # THE STATE BEFORE THE SPELLING. Research tab, radius 0: "South Bank" said in VIC
        # is Melbourne's Southbank, but it is spelt exactly as Brisbane's South Bank, and
        # answering with that exact spelling first left api/research.py's respelt brief
        # saying "South Bank" -- no VIC stock -- while locate() and the search area said
        # Southbank. 93 names in data/au_suburbs.csv are spelt exactly as a locality in
        # one state and fold to a different locality in another ("Campbell Town" NSW,
        # "Black Rock" QLD).
        if state:
            found = self._dataset_name(name, state, spoken=True)
            return self._canonical.get(found, "") if found else exact
        if exact:
            return exact
        hits = self._spoken_hits(name)
        names = set().union(*hits.values()) if hits else set()
        if len(names) != 1 or any(len(found) != 1 or self._estate_elsewhere(name, st, found)
                                  for st, found in hits.items()):
            return ""
        return self._canonical.get(next(iter(names)), "")

    def states_for_suburb(self, suburb: str, spoken: bool = False) -> List[str]:
        """Every state that has a locality of this name, sorted. Empty if unknown.

        A single element means the suburb identifies its state on its own; several means
        the name is shared and something else has to break the tie. spoken=True adds the
        fallback described at spoken_key.
        """
        if not suburb:
            return []
        exact = self._states_by_suburb.get(suburb.strip().lower())
        if exact or not spoken:
            return sorted(exact or ())
        hits = self._spoken_hits(suburb)
        # EVERY STATE MUST BE UNAMBIGUOUS, not merely some of them. Dropping only the
        # states where the key collides would hand back a narrower answer than the truth
        # — a key that is two localities in NSW and one in TAS would come back as TAS
        # alone — and state_resolver takes a single-state answer as PROOF of a row's
        # state. A key that collides anywhere, or is an estate filed elsewhere
        # (know_estates), answers nothing.
        if any(len(found) != 1 or self._estate_elsewhere(suburb, st, found)
               for st, found in hits.items()):
            return []
        return sorted(hits)

    def locate(self, suburb: str, state: str,
               spoken: bool = False) -> Optional[Tuple[float, float]]:
        name = self._dataset_name(suburb, state, spoken)
        return self._index.get((name, str(state or "").strip().upper())) if name else None

    def resolve_locality(self, suburb: str, state: str = "") -> str:
        """The real locality inside a suburb value, or '' if there is not one.

        The suburb column collects whatever landed in that position of a stocklist.
        Some of it is a locality, some is an estate glued to one — "Stage 5A,
        Greenbank", "Waler Heights, Mango Hill", "Walloon (Owner Occupiers Only),
        Walloon" — and 59% is neither: postcodes ("2026"), stray words ("offer"),
        header fragments ("IN TERNAL BALCONY TOTAL"), councils and regions.

        The locality is the LAST comma-separated part. That is a fact about how an
        address is written, not a guess about which word looks like a suburb — which
        matters, because guessing picks the street out of "COLEDALE DRIVE, MELTON".

        Lives here so the scoring pipeline and the benchmark agree. They did not:
        _candidates.py ungluedthe composite and benchmark_buildings.py did not, so a
        lot in "Stage 5A, Greenbank" was shortlisted with no benchmark and its peers
        were never grouped with the other Greenbank stock.
        """
        raw = str(suburb or "").strip()
        if not raw:
            return ""
        if self.locate(raw, state or ""):
            return raw
        # Colon is used the same way as a comma by several stocklists — "Estate : Dream
        # Sebastopol" — and a trailing postcode blocks the match on the part that holds
        # the locality: "Wyndham Gardens, Wyndham Vale 3024". Between them these two
        # shapes cost 43 real VIC lots, each of which was dropped from every search.
        for part in reversed([p.strip(" ()") for p in re.split(r"[,:]", raw) if p.strip(" ()")]):
            part = re.sub(r"\s*\([^)]*\)\s*", " ", part).strip()
            if part and part.lower() != raw.lower() and self.locate(part, state or ""):
                return part
            # Only ever a trailing postcode, and only when what remains resolves in the
            # index. Nothing is inferred: an unmatched remainder is still rejected.
            trimmed = re.sub(r"\s+\d{4}$", "", part).strip()
            if trimmed and trimmed != part and self.locate(trimmed, state or ""):
                return trimmed
            # "Dream Sebastopol" / "Pinnacle Smythes Creek" — an estate name prepended to
            # the locality. Walk the words from the right and keep the longest tail that
            # is a real locality, so "Smythes Creek" wins over "Creek".
            words = part.split()
            for start in range(1, len(words)):
                tail = " ".join(words[start:])
                if self.locate(tail, state or ""):
                    return tail
        return ""

    def find_suburb_in_text(self, text: str, state: str = "") -> Optional[str]:
        """Recover a suburb from a free-text address.

        Stocklist rows often carry the locality only inside the address string
        ("LOT 79 STELLA ST, COLAC 3250"), leaving the suburb field empty. Without
        this the property cannot be geocoded, so a distance filter cannot judge it.
        Matches the longest known locality name present, preferring `state`.
        """
        if not text or not self._index:
            return None
        words = re.findall(r"[A-Za-z][A-Za-z'\-]+", text)
        states = [state.strip().upper()] if state else []
        states += [s for s in self._by_state.keys() if s not in states]

        # The STATE is the outer loop, so a locality in the row's own state always beats
        # one that merely appears earlier in the text.
        #
        # It used to be the innermost loop, which made text position win despite the
        # docstring saying otherwise. On a real NSW row reading
        #
        #     "Jensen Rise Estate - Wadalba 49 Road #5 Wadalba 2259 CENTRAL COAST COUNCIL"
        #
        # it returned "Jensen" — an estate name, and a suburb that exists only in QLD —
        # while Wadalba, a real NSW locality present twice in the same line, was never
        # reached. The consequence is not cosmetic: this feeds distance filtering and
        # scoring (kommo_agent.py:136-142), so the lot was being placed in the wrong
        # town, and it would have benchmarked a Wadalba property against Jensen comps.
        # A NAME FOLLOWED BY A STREET TYPE IS A STREET, NOT THE SUBURB.
        #
        # "12 Windsor Street, BRISBANE NORTH ... Woodford" returned Windsor — a real
        # locality, and the wrong one, while Woodford sat in the same line. Measured on
        # live stock: 33 rows, every one of them geocoded to its own street. Same shape
        # as "612 Oxford Street ... Joyner", "2427 Cathcart Ave ... Tarneit" and
        # "Strathmore Street, Morayfield". resolve_locality already refuses this by
        # taking the last comma-separated part; free text has no commas to lean on.
        #
        # DEMOTED, NOT DROPPED. A street-suffixed candidate is still returned if nothing
        # else in the row matches, because "Wadalba 49 Road" — where the house number is
        # stripped by the word regex above — makes a genuine suburb look street-suffixed.
        # Demoting can only ever improve a match; dropping could lose one.
        fallback = None
        for st in states:
            for size in (3, 2, 1):          # longest match wins within a state
                for i in range(len(words) - size + 1):
                    cand = " ".join(words[i:i + size])
                    if (cand.lower(), st) not in self._index:
                        continue
                    after = words[i + size] if i + size < len(words) else ""
                    if after and _STREET_TYPE.fullmatch(after):
                        fallback = fallback or cand.title()
                        continue
                    return cand.title()
        return fallback

    def distance_between(self, suburb_a: str, suburb_b: str, state: str) -> Optional[float]:
        # spoken: kommo_agent measures a lot against the brief's primary suburb here, and
        # that is the name the caller said. See spoken_key.
        a = self.locate(suburb_a, state, spoken=True)
        b = self.locate(suburb_b, state, spoken=True)
        if not a or not b:
            return None
        return round(haversine_km(a[0], a[1], b[0], b[1]), 1)

    def suburbs_within_radius(self, origin_suburb: str, state: str, radius_km: float) -> List[Dict]:
        """
        All suburbs in `state` within radius_km of origin_suburb,
        sorted nearest-first. Origin itself is included at 0.0 km.
        """
        origin = self.locate(origin_suburb, state, spoken=True)     # a name a person gave
        if not origin or radius_km <= 0:
            return []
        out = []
        for suburb, lat, lng in self._by_state.get(state.strip().upper(), []):
            d = haversine_km(origin[0], origin[1], lat, lng)
            if d <= radius_km:
                out.append({'suburb': suburb, 'state': state.upper(), 'distance_km': round(d, 1)})
        out.sort(key=lambda x: x['distance_km'])
        return out

    def expand_search_suburbs(self, primary_suburbs: List[str], state: str, radius_km: Optional[float]) -> List[Dict]:
        """
        Builds the full search area: every primary suburb plus, when a radius is
        given, every suburb within radius_km of each primary suburb.
        Returns [{'suburb', 'state', 'distance_km', 'origin'}] nearest-first, deduped.
        """
        # THE DATASET'S SPELLING for every origin that resolves, because listings are
        # filed under it. Labelled .title() from what was said, a spoken "Red Bank Plains"
        # became its own 0.0 km entry beside the real "Redbank Plains" hit — a suburb no
        # listing carries — and the origin column named a place that does not exist. An
        # origin that does not resolve keeps its .title() label, exactly as before.
        labels = []
        for origin in primary_suburbs:
            found = self._dataset_name(origin, state, spoken=True)
            labels.append((origin, self._canonical.get(found) if found else origin.strip().title()))
        seen: Dict[str, Dict] = {}
        for _origin, label in labels:
            seen.setdefault(label.lower(), {
                'suburb': label, 'state': state.upper(),
                'distance_km': 0.0, 'origin': label
            })
        if radius_km and radius_km > 0:
            for origin, label in labels:
                for hit in self.suburbs_within_radius(origin, state, radius_km):
                    key = hit['suburb'].lower()
                    if key not in seen or hit['distance_km'] < seen[key]['distance_km']:
                        seen[key] = {**hit, 'origin': label}
        return sorted(seen.values(), key=lambda x: x['distance_km'])
