"""
One builder, one name.

`hattan.com.au` and `Hattan Homes` were being stored as two different builders — 105
listings under one, 64 under the other. That inflates the builder count, splits a
builder's stock in the dashboard filter, and defeats the whole point of comparing what
two builders charge for the same product: 12 of 13 "two builders, same design"
candidates turned out to be one builder spelled two ways.

WHERE THIS RUNS, AND WHY IT MATTERS. At WRITE time, in record_building, before
content_hash is computed — never as an UPDATE over stored rows. builder_name feeds
`builder_key` in the hash, so rewriting it afterwards changes the identity of every
row it touches, and the next harvest re-inserts them all as new. That is exactly the
mechanism that produced 777 duplicate captures. Canonicalising before the hash means
the same builder produces the same identity every run.

DELIBERATELY NARROW. Only two transformations, both of which are spelling and not
judgement:

  1. A bare domain that matches a known builder's name  ->  the builder's name.
     "hattan.com.au" -> "Hattan Homes".
  2. A trailing plural on an otherwise identical name.
     "Strike Development" -> "Strike Developments".

Everything else is left exactly as it was found, EXCEPT where the client has told us
two names are one supplier. That is not an inference we are allowed to make, so it
lives in CLIENT_CONFIRMED_ALIASES with a note of who said so and when — the judgement
belongs to the client, and the record shows it was theirs.

Standing rule still holds for everything not on that list: a blank, or an unmerged
name, beats a plausible guess.
"""

import re
from functools import lru_cache
from typing import Dict, FrozenSet, Iterable, Optional, Tuple

# Looks like a bare domain: no spaces, at least one dot, a plausible TLD tail.
_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)+$", re.I)
_TLD_TAIL = re.compile(r"\.(?:com\.au|net\.au|org\.au|com|net|org|au|io|co|id)$", re.I)


def looks_like_domain(name: str) -> bool:
    return bool(_DOMAIN.match(str(name or "").strip()))


def domain_label(name: str) -> str:
    """'hattan.com.au' -> 'hattan'. '' if it is not a domain."""
    s = str(name or "").strip().lower()
    if not looks_like_domain(s):
        return ""
    s = re.sub(r"^www\.", "", s)
    s = _TLD_TAIL.sub("", s)
    return s.split(".")[0]


# Names the CLIENT has confirmed are one supplier. Never add to this from a similarity
# rule — only from someone with authority saying so, and record who and when.
#
#   Bathla — Colin Nduru, call of 5 Aug 2026: "there's Bathla Development, and there's
#   another Bathla here, Bathla Group, and there's another Bathla. It's all the same
#   builder." Previously left deliberately unmerged, because a development arm and a
#   group can be separate entities and that call was not ours to make.
#   Level 33 — Colin Nduru, same call: "Level 33 is not a builder. There's no builder
#   called Level 33... the builder is called Atchison and Kenny." Corroborated by our own
#   data without needing his word for it: every one of those 318 rows carries the project
#   title "Atchison and Kenny Wollongong Building A/B". Proxima's project header put a
#   FLOOR in the developer field and the harvest stored it, exactly as it was told to.
CLIENT_CONFIRMED_ALIASES = {
    "bathla": "Bathla Development",
    "bathlagroup": "Bathla Development",
    "bathladevelopment": "Bathla Development",
    "bathladevelopments": "Bathla Development",
    "level33": "Atchison and Kenny",
}

# Builders the CLIENT has withdrawn. No harvest reads their portal, no search returns
# their lots and nothing recommends them, whichever channel the lot arrived through. Like
# the aliases above, only from someone with authority saying so, recorded with who and when.
#
#   Bathla — Colin Nduru, call of 23 Oct 2026: "Take off Butler. They went off bust.
#   We're no longer using them. So just, just delete them." Fathom heard "Bathla" as
#   "Butler"; Bathla is the only name on the sign-in table he was reading from, and Bathla
#   Group entered voluntary administration on 25 Aug 2026. Most of its stock came through
#   Proxima (198 of its 226 rows in the 26 Sep database), not its own portal, so this is a
#   rule about the builder and not about the portal.
#
# The stored rows are left in the database. Taking a name off this list brings them back.
WITHDRAWN_BUILDERS = frozenset({"Bathla Development"})


def is_withdrawn_builder(name: str) -> bool:
    """True when this builder name, in any spelling the client has merged, is withdrawn.

    Matched through CLIENT_CONFIRMED_ALIASES and nothing looser: "Bathla Group" on a
    Proxima row and "Bathla" on the portal are withdrawn, and a name that merely
    contains "bathla" is not.
    """
    raw = str(name or "").strip()
    if not raw:
        return False
    return _withdrawn_name(raw)


# ONE ANSWER PER NAME, kept. _candidates.load_snapshot runs on every search request, Clare's
# hook included, and the project and capture rules below ask this of every row of the
# snapshot up to three times over: 29,723 rows on 12 Oct, with 163 names among them. Both
# tables it reads are constants, so a name's answer cannot change while the process runs,
# and the regex in _squash was most of the cost. On a snapshot build_web wrote (18,618 rows,
# 26 Sep), the whole withdrawn filter takes 28 ms, against 24 ms for the name rule alone.
@lru_cache(maxsize=4096)
def _withdrawn_name(raw: str) -> bool:
    return CLIENT_CONFIRMED_ALIASES.get(_squash(raw), raw) in WITHDRAWN_BUILDERS


# A WITHDRAWN BUILDER'S PROJECT, ON THE NIGHTS ITS DEVELOPER FIELD COMES BACK BLANK (23 Oct
# 2026, the night the name rule above went live). Proxima files every lot under a project
# and reads the developer off that project's header, and the harvest stores a header only
# when it reads as a company. So one project can name its developer on one night and
# nobody on the next: project 499 "Kensington Lot1 Townhouse" (Riverstone) said "Bathla
# Development" on 9 Sep, and from 22 Sep returned the SAME lot numbers at the SAME prices
# with a blank builder. Those rows passed every rule keyed on the name, and that night six
# of them were on the live NSW answer, one ranked #2 for Riverstone as "Builder not
# identified in source". Project 591 "Futurity Street Box Hill" has the same shape.
#
# So a project that ANY row, live or superseded, files under a withdrawn builder is a
# withdrawn project, and a row that names no builder in it goes with it. Two limits keep
# this a rule about the withdrawn builder's own projects:
#   * a row that names a different builder is never withdrawn by its project, whatever
#     else shares it -- a blank is the only thing the project is allowed to fill in;
#   * a blank project id is never a key. 18 of Bathla's Proxima rows and all 28 of its
#     portal rows carried none on 12 Oct, and (channel, blank) names a channel, not a project:
#     the first such row on E-Agent, where no row has a project id, would take all 1,699
#     of E-Agent's blank-builder rows (12 Oct snapshot) with it.
#
# Like the name rule, nothing is deleted: the rows stay in the database, and taking the
# builder off WITHDRAWN_BUILDERS brings its projects back with it.
def project_key(row) -> Optional[Tuple[str, str]]:
    """(source_channel, source_project_id) of a row, or None when it names no project."""
    project = str(row.get("source_project_id") or "").strip()
    if not project:
        return None
    return str(row.get("source_channel") or "").strip(), project


def withdrawn_projects(rows: Iterable[dict]) -> FrozenSet[Tuple[str, str]]:
    """Every project_key that one of `rows` files under a withdrawn builder.

    Pass EVERY row, superseded ones included: by 23 Oct every row of project 499 that
    named Bathla was superseded, and only its blank-builder captures were live."""
    keys = set()
    for row in rows:
        if is_withdrawn_builder(row.get("builder_name")):
            key = project_key(row)
            if key is not None:
                keys.add(key)
    return frozenset(keys)


# AND ANOTHER CAPTURE OF A WITHDRAWN ROW'S LOT, WHEREVER PROXIMA FILED IT (24 Oct 2026, the
# review of the project rule). supersede_duplicates marks the older capture of a lot with
# the content_hash of the row that replaced it, so superseded_by is the database's own
# statement that two rows are ONE lot. Bathla's project 593 "124 Old Pitt Town Road Box
# Hill Land Only" listed Lot 128, 65 Futurity Street and Lot 119, 27 Mount Carmel Drive at
# $819,990 under "Bathla Development" on 30 Aug, and the captures that replaced them carry
# the same lots at the same price with a BLANK builder, filed under project 724 "Lindfield
# House - Lindfield" -- which no row naming Bathla is filed under, so the project rule could
# not see them. Both were live, and on the 12 Oct snapshot the Research tab put them first
# and second under Needs confirming for Box Hill, as "Builder not identified in source".
#
# So a blank-builder row joined by superseded_by to a withdrawn row -- the capture that
# replaced it, or one it replaced -- is withdrawn, and so is a blank row joined to THAT one:
# 481 rows of the 26 Sep database point at a row that is itself superseded, so one step
# would leave the end of a chain live. The walk stops at a row that names a builder, by the
# same limit as the project rule: a withdrawn builder's row is withdrawn by its name already,
# and another builder's row is that builder's lot, whatever it is joined to. A blank hash
# joins nothing. On the 26 Sep database and the 12 Oct snapshot alike this adds exactly the
# two Box Hill lots, both newer captures, and no row is joined to another builder's as well.
def withdrawn_captures(rows: Iterable[dict],
                       projects: FrozenSet[Tuple[str, str]] = frozenset()) -> FrozenSet[str]:
    """The content_hash of every blank-builder row outside `projects` that `rows` join,
    through superseded_by and blank-builder rows only, to a row withdrawn by its name or by
    one of `projects` -- the rows those two rules miss, and nothing they already catch.

    Pass EVERY row, superseded ones included, as for withdrawn_projects: the rows naming
    Bathla on project 593 were the superseded half of each pair."""
    rows = list(rows)
    # Every row the name and project rules withdraw starts a walk, so a row they catch is
    # walked from there and never needs to be in `found` itself. A snapshot build_web
    # wrote holds none of them, and the search reads one on every request: no walk, no maps.
    todo = [row for row in rows if is_withdrawn_row(row, projects)]
    if not todo:
        return frozenset()
    by_hash, older = {}, {}
    for row in rows:
        h = _content_hash(row)
        if h:
            by_hash.setdefault(h, row)
        newer = str(row.get("superseded_by") or "").strip()
        if newer:
            older.setdefault(newer, []).append(row)
    found = set()
    while todo:
        row = todo.pop()
        newer = by_hash.get(str(row.get("superseded_by") or "").strip())
        for linked in ([newer] if newer is not None else []) + older.get(_content_hash(row), []):
            h = _content_hash(linked)
            if (h and h not in found and not str(linked.get("builder_name") or "").strip()
                    and not is_withdrawn_row(linked, projects)):
                found.add(h)
                todo.append(linked)
    return frozenset(found)


def _content_hash(row) -> str:
    return str(row.get("content_hash") or "").strip()


def is_withdrawn_row(row, projects: FrozenSet[Tuple[str, str]] = frozenset(),
                     captures: FrozenSet[str] = frozenset()) -> bool:
    """True when nothing may offer this row: its builder is withdrawn, or it names no
    builder and sits in one of `projects` (withdrawn_projects of every row) or is one of
    `captures` (withdrawn_captures of every row)."""
    if str(row.get("builder_name") or "").strip():
        return is_withdrawn_builder(row.get("builder_name"))
    key = project_key(row)
    if key is not None and key in projects:
        return True
    h = _content_hash(row)
    return bool(h) and h in captures


# Values that cannot be a company, whatever field they arrived in. A floor, a stage or a
# tower is a place inside a development, and storing one as the builder puts a fiction in
# front of a buyer — "Level 33" reached 318 listings that way. Structural, so the next
# "Level 4" or "Stage 2" is caught without anyone having to notice it first.
_NOT_A_COMPANY = re.compile(
    r"^\s*(?:level|lvl|floor|storey|story|stage|tower|building|block|unit|apt|apartment|"
    r"precinct|release)\s*[-#]?\s*[0-9]{0,4}[a-z]?\s*$", re.I)


def is_not_a_builder_name(value: str) -> bool:
    """True when this value is a place inside a development, not a company."""
    return bool(_NOT_A_COMPANY.match(str(value or "")))


def _squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _depluralised(name: str) -> str:
    """'strike developments' -> 'strike development' (last word only)."""
    parts = str(name or "").strip().split()
    if not parts:
        return ""
    last = parts[-1]
    if len(last) > 3 and last.lower().endswith("s") and not last.lower().endswith("ss"):
        parts[-1] = last[:-1]
    return " ".join(parts)


class BuilderNameCanonicaliser:
    """Resolves a raw builder name against the names already known."""

    def __init__(self, known: Optional[Iterable[str]] = None):
        self._by_squash: Dict[str, str] = {}
        self._by_label: Dict[str, str] = {}
        self._by_plural: Dict[str, str] = {}
        for name in known or ():
            self.learn(name)

    def learn(self, name: str) -> None:
        """Register a real builder name as a canonical target.

        A domain is never a canonical target — it is the thing being resolved away —
        so learning "hattan.com.au" must not make it the name everything maps to.
        """
        n = str(name or "").strip()
        if not n or looks_like_domain(n):
            return
        self._by_squash.setdefault(_squash(n), n)
        self._by_plural.setdefault(_squash(_depluralised(n)), n)
        # First word of the name, so a domain label can find it: "Hattan Homes" -> hattan
        first = re.split(r"[^A-Za-z0-9]+", n.strip())
        if first and len(first[0]) >= 4:
            self._by_label.setdefault(first[0].lower(), n)

    def canonical(self, name: str) -> str:
        raw = str(name or "").strip()
        if not raw:
            return ""

        # 0. a merge the client has confirmed. First, because it outranks every
        #    spelling rule below — it is a stated fact about their suppliers.
        confirmed = CLIENT_CONFIRMED_ALIASES.get(_squash(raw))
        if confirmed:
            return confirmed

        # 1. a bare domain -> the builder whose name it belongs to
        label = domain_label(raw)
        if label:
            hit = self._by_label.get(label)
            if hit:
                return hit
            return raw          # unknown domain: keep it, do not invent a name

        # 2. exact (case/punctuation-insensitive) match to a name we know
        hit = self._by_squash.get(_squash(raw))
        if hit:
            return hit

        # 3. singular/plural of a name we know
        hit = self._by_plural.get(_squash(_depluralised(raw)))
        if hit:
            return hit

        return raw
