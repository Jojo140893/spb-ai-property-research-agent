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

Everything else is left exactly as it was found. In particular "Bathla Development",
"Bathla Group" and "Bathla" are NOT merged: they share a word, but a development arm
and a group can be different entities, and deciding they are the same is a judgement
about the client's suppliers that belongs to the client. Standing rule — a blank, or
an unmerged name, beats a plausible guess.
"""

import re
from typing import Dict, Iterable, Optional

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
