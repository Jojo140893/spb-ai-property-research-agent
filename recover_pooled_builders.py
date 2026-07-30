"""Recover the builder on E-Agent's POOLED stocklist rows, on evidence only.

WHY THIS EXISTS
    255 rows carry attribution_scope='state_pooled' and an empty builder_name. They
    came from E-Agent's pooled files (VIC Regional.xlsx, NSW Dual.xlsx, DUAL QLD.xlsx
    and 3 PDFs), which list several builders' stock without saying, per row, whose it
    is. 2,452 rows carry attribution_scope='builder' and DO name the builder.

THE RULE THIS RESPECTS
    Never guess a builder. A wrong attribution is worse than a blank, because a blank
    is visibly a blank. So every candidate key is scored on PRECISION first, a pooled
    row that matches two different builders stays blank, and the keys that fail are
    reported with the evidence that failed them (--rejected).

THREE CHANNELS SURVIVED, ONE WAS CAUGHT LYING

  1. LOT MATCH - the same lot appears in a per-builder file.
     Keys: price+lot_number, price+land_sqm, the whole normalised source row, and the
     set of dollar amounts in the row (>=3 of them). All four score 100% precision in
     HOLDOUT A and <=0.2% false positives in HOLDOUT B.

  2. STOCK-CODE PREFIX - the row carries a builder's own stock reference.
     Only two code schemes exist in the whole DB and each belongs to exactly one
     builder: PR#### -> Silkwood Homes (78/78 rows), PK#### -> Ausbuild (70/70).

  3. FILE TITLE - the pooled file names its builder, once, for the whole file.
     One of the "pooled" PDFs is really a single-builder brochure: 25 of 25 rows
     carry "Creation Homes Victoria" in the column the extractor parked the page
     header in. Uniform over 100% of the file, so it is a title, not a section break.

  REJECTED: SECTION HEADER inside a mixed file. "HUDSON HOMES: DUAL & DUPLEX" is
     stuck in estate_name on 12 of the 72 NSW Dual rows - and 9 of those 12 are
     Eternal Homes lots, proven by an exact text match to Eternal's own stocklist.
     A sticky header bleeds down the sheet. 75% wrong. See --rejected.

HOW PRECISION IS MEASURED (channel 1)
    Two holdouts over the 2,273 named rows that carry source_text:

      HOLDOUT A - "can it recover a builder that IS in the pool?"
          Blank one row's builder in memory; the pool is every other named row. A
          confident answer that differs from the truth is a precision failure.

      HOLDOUT B - "does it invent a builder that is NOT in the pool?"
          Blank one row AND delete its builder from the pool entirely. Every
          confident answer is now wrong by construction, so the rate of confident
          answers IS the false-positive rate. This is the decisive number: only ~40
          of the 255 pooled rows have any counterpart in the named set at all, so
          most pooled rows are the HOLDOUT B situation.

    Leave-one-FILE-out is not a usable holdout here: 34 of 36 builders have exactly
    one source file, so dropping a row's file erases its builder's whole footprint
    and makes the right answer unavailable by construction. HOLDOUT B is the honest
    version of that test.

USAGE
    python -X utf8 recover_pooled_builders.py              # report only (default)
    python -X utf8 recover_pooled_builders.py --verbose    # + evidence for every proposal
    python -X utf8 recover_pooled_builders.py --rejected   # + why the weak keys lost
    python -X utf8 recover_pooled_builders.py --apply      # write

--apply CHANGES ROW IDENTITY: builder_name is one of database._HASH_FIELDS. Read the
WRITE CAVEAT the script prints before using it.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "spb_research_audit.db"

# ---- channel 1 keys -------------------------------------------------------- #
# Ordered strongest first. Kept separate so the report can show which fired.
ACCEPTED_KEYS = ("price_lot", "price_landsqm", "text_norm", "money_set3", "price_numfp")
REJECTED_KEYS = ("money_cols_triple", "price_suburb", "price_only",
                 "design_token", "design_strong")
ALL_KEYS = ACCEPTED_KEYS + REJECTED_KEYS

# A design name is only "strong" evidence if the named set attaches it to one builder
# this many times. Below it, street and estate proper nouns dominate ("Wildflower
# Circuit" -> Thomas Paul Constructions x4).
DESIGN_STRONG_SUPPORT = 10

# price_numfp: two rows are the same lot if the package price matches exactly AND they
# share at least this many OTHER numbers (lot number, land area, house area, frontage,
# bed/bath/car counts, completion year). 5 was chosen by sweeping 2..6 against both
# holdouts (A precision / B false-positive rate / pooled rows named):
#     N=2  94.87% / 3.80% / 49      N=4  99.77% / 0.08% / 47
#     N=3  98.12% / 1.50% / 47      N=5 100.00% / 0.00% / 47   <- chosen
#                                   N=6 100.00% / 0.00% / 37
# 5 is the first threshold with no error of either kind, and 6 costs 10 pooled rows
# to buy nothing. This key exists because the pooled files put the lot
# number and land area in columns the extractor never mapped, so price_lot and
# price_landsqm cannot fire on them even though the numbers are right there in
# source_text.
NUMFP_MIN_SHARED = 5

# A stock-code prefix is only usable if ONE builder uses it, on at least this many
# rows. Two schemes clear the bar in this DB; nothing else uses codes at all.
CODE_PREFIX_MIN_SUPPORT = 20

# A label only counts as a FILE TITLE if it is on every pooled row of the file.
FILE_TITLE_MIN_COVERAGE = 1.0


# --------------------------------------------------------------------------- #
# normalisation
# --------------------------------------------------------------------------- #

# Mirrors database._variant_key: strip everything about a listing that moves over its
# life (price, availability, title status, dates, yields) so the same package in two
# different files normalises to the same string.
_STRIP_MONEY = re.compile(r"\$\s?[\d,\.]+\s*k?", re.I)
_STRIP_STATUS = re.compile(r"\b(available|not\s*available|unavailable|on\s*hold|hold|"
                           r"under\s*offer|sold|reserved|leased|tbc|"
                           r"titled|untitled|registered|unregistered)\b", re.I)
_STRIP_DATES = re.compile(r"\b(?:q[1-4][\s-]*\d{2,4}|quarter\s*[1-4][\s,-]*\d{2,4}|"
                          r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
                          r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
                          r"[a-z]*[\s-]*\d{2,4})\b", re.I)
_STRIP_PCT = re.compile(r"\d+(?:\.\d+)?\s*%")

_MONEY_RX = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")
_NUM_RX = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
_DESIGN_RX = re.compile(r"\b([A-Z][a-zA-Z]{3,}|[A-Z]{4,})\b")
_WORD_RX = re.compile(r"[a-z]{3,}")
# A builder's own stock reference: 2-4 letters then digits, e.g. PR8735, PK1042.
_CODE_RX = re.compile(r"\b([A-Z]{2,4})-?(\d{3,5})\b")


def norm_text(t: str | None) -> str:
    t = re.sub(r"\s+", " ", (t or "")).strip().lower()
    for rx in (_STRIP_MONEY, _STRIP_STATUS, _STRIP_DATES, _STRIP_PCT):
        t = rx.sub(" ", t)
    t = re.sub(r"[^a-z0-9\.\+]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def money_set(t: str | None) -> tuple[int, ...]:
    """Every dollar amount >= $1,000 in the row, as a sorted set.

    A stocklist row normally carries land price, build price and package price, so
    three exact amounts is a three-way numeric coincidence to fake.
    """
    vals = set()
    for m in _MONEY_RX.findall(t or ""):
        try:
            v = float(m.replace(",", ""))
        except ValueError:
            continue
        if v >= 1000:
            vals.add(int(round(v)))
    return tuple(sorted(vals))


def numeric_fingerprint(t: str | None, price: float | None) -> frozenset[float]:
    """Every number in the row except the package price itself.

    Deliberately includes small counts and the completion year: a lot's identity in a
    stocklist is the whole numeric row, and requiring NUMFP_MIN_SHARED of them to
    coincide on top of an exact price is what makes this safe.
    """
    out = set()
    for m in _NUM_RX.findall(t or ""):
        try:
            out.add(round(float(m.replace(",", "")), 2))
        except ValueError:
            continue
    if price:
        out.discard(round(float(price), 2))
    return frozenset(out)


def canon_builder(name: str | None) -> str:
    """'G Developments', 'G-Developments' and 'G DEVELOPMENTS' are one builder."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def code_prefixes(t: str | None) -> set[str]:
    return {p.upper() for p, _ in _CODE_RX.findall(t or "")}


def codes(t: str | None) -> set[str]:
    return {f"{p.upper()}{d}" for p, d in _CODE_RX.findall(t or "")}


_GENERIC = set("""available unavailable hold onhold sold reserved leased under offer
titled untitled registered unregistered dual key duplex house lot lots land build
price total single double storey story beds bath baths bathrooms car cars spaces
sqm frontage estate road street drive circuit avenue court close place way parade
crescent boulevard terrace lane grove rise park hill view council city shire region
north south east west mid contract part portal link due quarter january february
march april may june july august september october november december jan feb mar apr
jun jul aug sep oct nov dec corner adaptable yield rent week deposit custom occ
living design facade type status option options plus classic modern traditional
contemporary elite bright elevate pavilion split std upgrade included approx tbc
nil none new home homes property properties package packages""".split())


def design_tokens(text: str | None, place_words: set[str]) -> frozenset[str]:
    """House-design candidates: capitalised/all-caps words that are not place names.

    House designs ("Vesper", "SODIUM", "Tuvala") really are builder-proprietary, so
    this LOOKS like the strongest signal available. HOLDOUT B says otherwise; see
    --rejected.
    """
    out = {t.lower() for t in _DESIGN_RX.findall(text or "")}
    return frozenset(t for t in out if t not in place_words and t not in _GENERIC)


def row_keys(r: sqlite3.Row, place_words: set[str]) -> dict[str, object]:
    """Every candidate key for one row. None == this row cannot form that key."""
    price = r["price"]
    text = r["source_text"] or ""
    ntext = norm_text(text)
    land_p, build_p = r["land_price"], r["build_price"]
    land_sqm, lot = r["land_sqm"], (r["lot_number"] or "").strip().lower()
    suburb = re.sub(r"[^a-z]", "", (r["suburb"] or "").lower())
    monies = money_set(text)

    k: dict[str, object] = {
        "price_lot": ("PN", round(price), lot) if (price and lot) else None,
        "price_landsqm": ("PL", round(price), round(float(land_sqm), 1))
                         if (price and land_sqm) else None,
        "text_norm": ("T", ntext) if len(ntext) >= 30 else None,
        "money_set3": ("M",) + monies if len(monies) >= 3 else None,
        # not a hash key: the price only selects the candidates, the numeric
        # fingerprint overlap is scored pairwise in Corpus.lookup
        "price_numfp": ("NF", round(price)) if price and text else None,
        "money_cols_triple": ("C", round(land_p), round(build_p), round(price))
                             if (land_p and build_p and price) else None,
        "price_suburb": ("PS", round(price), suburb) if (price and suburb) else None,
        "price_only": ("P", round(price)) if price else None,
    }
    k["design_token"] = design_tokens(text, place_words) or None
    k["design_strong"] = k["design_token"]
    return k


# --------------------------------------------------------------------------- #
# corpus
# --------------------------------------------------------------------------- #

class Corpus:
    def __init__(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        self.rows = conn.execute("SELECT * FROM buildings").fetchall()
        conn.close()

        self.pooled = [r for r in self.rows
                       if r["attribution_scope"] == "state_pooled"
                       and not (r["builder_name"] or "").strip()]
        self.named = [r for r in self.rows
                      if r["attribution_scope"] == "builder"
                      and (r["builder_name"] or "").strip()]
        self.named_text = [r for r in self.named if r["source_text"]]

        # Place-name stoplist mined from the DB itself, so a suburb or an estate can
        # never be mistaken for a house design. lot_address is deliberately NOT used:
        # on E-Agent rows it is a jammed blob that contains the design name itself
        # ("Lot 54, HUDSON HOMES: DUAL & DUPLEX"), so feeding it in would blacklist the
        # very tokens the design lexicon exists to read.
        self.place_words: set[str] = set()
        for r in self.rows:
            for col in ("suburb", "estate_name", "state", "builder_name"):
                self.place_words.update(_WORD_RX.findall((r[col] or "").lower()))

        self.keys = {r["id"]: row_keys(r, self.place_words) for r in self.rows}

        spellings: dict[str, Counter] = defaultdict(Counter)
        for r in self.named:
            spellings[canon_builder(r["builder_name"])][r["builder_name"]] += 1
        self.display = {c: s.most_common(1)[0][0] for c, s in spellings.items()}

        self.numfp = {r["id"]: numeric_fingerprint(r["source_text"], r["price"])
                      for r in self.rows}
        self.by_price: dict[int, list[sqlite3.Row]] = defaultdict(list)

        self.index: dict[str, dict[object, Counter]] = {k: defaultdict(Counter)
                                                        for k in ALL_KEYS}
        for r in self.named_text:
            b = canon_builder(r["builder_name"])
            if r["price"]:
                self.by_price[round(r["price"])].append(r)
            for kn in ALL_KEYS:
                kv = self.keys[r["id"]][kn]
                if kv is None or kn == "price_numfp":
                    continue
                if kn in ("design_token", "design_strong"):
                    for tok in kv:                              # type: ignore[union-attr]
                        self.index[kn][tok][b] += 1
                else:
                    self.index[kn][kv][b] += 1

        self.relabel = self._detect_relabels()
        self.prefix_owner, self.prefix_support = self._code_prefix_owners()

    # ---- a data artefact that has to be named, or every figure below lies
    def _detect_relabels(self) -> dict[str, set[str]]:
        """'Builders' that are really the same stock filed under several labels.

        Returns {} against a clean database, and did so as of 2026-07-30. It was built
        for a specific artefact: three values in builder_name were ESTATES rather than
        builders - 'Kemps Estate - Austral', 'Emerald Grove - Jordan Springs' and
        'Bingara Gorge - Wilton' - holding the same listings two or three times over,
        because E-Agent's NSW page groups part of itself by estate and two spellings of
        one Google Sheets URL were read as two files. Both faults are fixed in
        `sources/e_agent.py`, and `fix_estate_builder_labels.py` relabelled the 229 rows
        to Creation Homes (or to a blank at project scope, where no file names a builder).

        Kept, because it is found from the DATA and nothing here is hardcoded: any future
        source that files one builder's stock under two labels would otherwise make every
        key in this report look imprecise when it is not. When it fires, the fold is
        printed, so a reader can see it happened rather than having it applied silently.

        Two labels are the same stock when they share the same lot - identical
        normalised text, OR the same price plus NUMFP_MIN_SHARED other numbers - on at
        least 3 rows AND on at least 40% of the smaller label's rows. A couple of
        coincidences is not a relabel; 94% of 107 rows was.
        """
        rows_per: Counter = Counter(canon_builder(r["builder_name"])
                                    for r in self.named_text)
        shared: Counter = Counter()
        groups: dict[object, set[str]] = defaultdict(set)
        for r in self.named_text:
            kv = self.keys[r["id"]]["text_norm"]
            if kv:
                groups[kv].add(canon_builder(r["builder_name"]))
        for members in groups.values():
            for a in members:
                for b in members:
                    if a < b:
                        shared[(a, b)] += 1
        for price, cands in self.by_price.items():
            for i, x in enumerate(cands):
                for y in cands[i + 1:]:
                    a, b = sorted((canon_builder(x["builder_name"]),
                                   canon_builder(y["builder_name"])))
                    if a == b:
                        continue
                    if len(self.numfp[x["id"]] & self.numfp[y["id"]]) >= NUMFP_MIN_SHARED:
                        shared[(a, b)] += 1

        eq: dict[str, set[str]] = {}
        for (a, b), hits in shared.items():
            floor = 0.40 * min(rows_per[a], rows_per[b])
            if hits >= 3 and hits >= floor:
                merged = eq.get(a, {a}) | eq.get(b, {b}) | {a, b}
                for m in merged:
                    eq[m] = merged
        return eq

    def _code_prefix_owners(self) -> tuple[dict[str, str], dict[str, Counter]]:
        """Stock-code prefix -> the single builder that uses it (if exactly one does)."""
        support: dict[str, Counter] = defaultdict(Counter)
        for r in self.named_text:
            for p in code_prefixes(r["source_text"]):
                support[p][canon_builder(r["builder_name"])] += 1
        owner = {p: next(iter(c)) for p, c in support.items()
                 if len(c) == 1 and sum(c.values()) >= CODE_PREFIX_MIN_SUPPORT}
        return owner, support

    def same_builder(self, a: str, b: str) -> bool:
        return a == b or b in self.relabel.get(a, ())

    def collapse(self, builders: set[str]) -> set[str]:
        out: set[str] = set()
        for b in builders:
            if not any(self.same_builder(b, seen) for seen in out):
                out.add(b)
        return out

    def lookup(self, row: sqlite3.Row, kn: str, *,
               drop_builder: str | None = None,
               drop_self: bool = False) -> set[str]:
        """Canonical builders the named set offers for this row under key `kn`."""
        kv = self.keys[row["id"]][kn]
        if kv is None:
            return set()
        me = canon_builder(row["builder_name"])

        if kn == "price_numfp":
            mine = self.numfp[row["id"]]
            out = set()
            for cand in self.by_price.get(round(row["price"]), ()):
                b = canon_builder(cand["builder_name"])
                if drop_self and cand["id"] == row["id"]:
                    continue
                if drop_builder is not None and self.same_builder(drop_builder, b):
                    continue
                if len(mine & self.numfp[cand["id"]]) >= NUMFP_MIN_SHARED:
                    out.add(b)
            return out

        is_design = kn in ("design_token", "design_strong")
        floor = DESIGN_STRONG_SUPPORT if kn == "design_strong" else 1
        toks = kv if is_design else (kv,)                           # type: ignore[assignment]
        out: set[str] = set()
        for tok in toks:                                            # type: ignore[union-attr]
            cnt = Counter(self.index[kn].get(tok, ()))
            if drop_builder is not None:
                for b in list(cnt):
                    if self.same_builder(drop_builder, b):
                        del cnt[b]
            if drop_self:
                cnt[me] -= 1
            hits = {b for b, v in cnt.items() if v >= floor}
            if is_design:
                # a design name is only evidence when it points at ONE builder
                if len(self.collapse({b for b, v in cnt.items() if v > 0})) == 1:
                    out |= hits
            else:
                out |= {b for b, v in cnt.items() if v > 0}
        return out

    # ---- file grouping
    def pooled_files(self) -> dict[str, list[sqlite3.Row]]:
        files: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for p in self.pooled:
            files[p["source_url"] or "(no url)"].append(p)
        return files

    def resolve_label(self, label: str | None) -> str | None:
        """A known builder whose name is CONTAINED in this label, if exactly one is.

        Containment runs label-contains-builder only. 'Creation Homes Victoria'
        resolves to Creation Homes; 'GDEV' does NOT resolve to G-Developments, because
        expanding an abbreviation would be a guess, not a reading.
        """
        c = canon_builder(label)
        if len(c) < 8:
            return None
        hits = {b for b in self.display if len(b) >= 8 and b in c}
        hits = self.collapse(hits)
        if len(hits) != 1:
            return None
        return next(iter(hits))


def short_file(url: str) -> str:
    return url.split("dn=")[-1] if "dn=" in url else url.rsplit("/", 1)[-1]


# --------------------------------------------------------------------------- #
# holdouts
# --------------------------------------------------------------------------- #

def run_holdouts(c: Corpus) -> dict[str, tuple]:
    out: dict[str, tuple] = {}
    n = len(c.named_text)
    for kn in ALL_KEYS:
        a_u = a_ok = a_bad = a_amb = b_fp = 0
        for r in c.named_text:
            truth = canon_builder(r["builder_name"])
            got = c.collapse(c.lookup(r, kn, drop_self=True))
            if got:
                if len(got) == 1:
                    a_u += 1
                    if c.same_builder(truth, next(iter(got))):
                        a_ok += 1
                    else:
                        a_bad += 1
                else:
                    a_amb += 1
            gotb = c.collapse(c.lookup(r, kn, drop_builder=truth))
            if len(gotb) == 1:
                b_fp += 1
        out[kn] = (a_u, a_ok, a_bad, a_amb, b_fp, n)

    # stock-code prefix, same two holdouts
    a_u = a_ok = a_bad = b_fp = 0
    for r in c.named_text:
        truth = canon_builder(r["builder_name"])
        for drop, is_b in ((None, False), (truth, True)):
            got: set[str] = set()
            for p in code_prefixes(r["source_text"]):
                cnt = Counter(c.prefix_support.get(p, ()))
                if drop is not None:
                    cnt.pop(drop, None)
                else:
                    cnt[truth] -= 1
                cnt = Counter({b: v for b, v in cnt.items()
                               if v >= CODE_PREFIX_MIN_SUPPORT})
                if len(cnt) == 1:
                    got.add(next(iter(cnt)))
            if not is_b and len(got) == 1:
                a_u += 1
                a_ok += 1 if c.same_builder(truth, next(iter(got))) else 0
                a_bad += 0 if c.same_builder(truth, next(iter(got))) else 1
            if is_b and len(got) == 1:
                b_fp += 1
    out["code_prefix"] = (a_u, a_ok, a_bad, 0, b_fp, n)
    return out


# --------------------------------------------------------------------------- #
# proposals
# --------------------------------------------------------------------------- #

def channel_lot_match(c: Corpus) -> tuple[list[dict], list[dict]]:
    accept, clash = [], []
    for p in c.pooled:
        per_key = {}
        for kn in ACCEPTED_KEYS:
            got = c.collapse(c.lookup(p, kn))
            if got:
                per_key[kn] = got
        if not per_key:
            continue
        union: set[str] = set()
        for v in per_key.values():
            union |= v
        union = c.collapse(union)
        rec = {"row": p, "builders": union, "keys": sorted(per_key),
               "channel": "lot_match",
               "builder": c.display[next(iter(union))] if len(union) == 1 else None}
        (accept if len(union) == 1 else clash).append(rec)
    return accept, clash


def channel_code_prefix(c: Corpus) -> tuple[list[dict], list[dict]]:
    accept, clash = [], []
    for p in c.pooled:
        got = {c.prefix_owner[pr] for pr in code_prefixes(p["source_text"])
               if pr in c.prefix_owner}
        got = c.collapse(got)
        if not got:
            continue
        pre = sorted(pr for pr in code_prefixes(p["source_text"]) if pr in c.prefix_owner)
        rec = {"row": p, "builders": got, "keys": [f"code:{'+'.join(pre)}"],
               "channel": "code_prefix",
               "builder": c.display[next(iter(got))] if len(got) == 1 else None}
        (accept if len(got) == 1 else clash).append(rec)
    return accept, clash


def channel_file_title(c: Corpus) -> tuple[list[dict], list[str]]:
    """A pooled file whose EVERY row carries the same builder label names its builder."""
    accept, notes = [], []
    for url, rows in c.pooled_files().items():
        per_row_labels = []
        for r in rows:
            found = set()
            for col in ("suburb", "estate_name", "lot_address"):
                b = c.resolve_label(r[col])
                if b:
                    found.add(b)
            per_row_labels.append(found)
        counts: Counter = Counter()
        for f in per_row_labels:
            for b in f:
                counts[b] += 1
        if not counts:
            continue
        for b, n in counts.most_common():
            cov = n / len(rows)
            if cov >= FILE_TITLE_MIN_COVERAGE:
                for r in rows:
                    accept.append({"row": r, "builders": {b}, "keys": ["file_title"],
                                   "channel": "file_title", "builder": c.display[b]})
                notes.append(f"ACCEPTED  {c.display[b]:<24} on {n}/{len(rows)} rows "
                             f"(100%)  {short_file(url)[:52]}")
            else:
                notes.append(f"rejected  {c.display[b]:<24} on {n}/{len(rows)} rows "
                             f"({cov * 100:.0f}%) {short_file(url)[:52]}"
                             f"  -- section header, not a title")
    return accept, notes


def merge_channels(c: Corpus, groups: list[list[dict]]) -> tuple[list[dict], list[dict]]:
    """One proposal per row. Channels that disagree cancel each other out."""
    by_row: dict[int, list[dict]] = defaultdict(list)
    for g in groups:
        for rec in g:
            by_row[rec["row"]["id"]].append(rec)
    accept, conflict = [], []
    for rid, recs in by_row.items():
        names = c.collapse({b for r in recs for b in r["builders"]})
        if len(names) == 1:
            accept.append({"row": recs[0]["row"],
                           "builder": c.display[next(iter(names))],
                           "keys": sorted({k for r in recs for k in r["keys"]}),
                           "channels": sorted({r["channel"] for r in recs})})
        else:
            conflict.append({"row": recs[0]["row"],
                             "builders": sorted(c.display[b] for b in names),
                             "channels": sorted({r["channel"] for r in recs})})
    accept.sort(key=lambda r: r["row"]["id"])
    return accept, conflict


def review_tier(c: Corpus, taken: set[int]) -> list[dict]:
    """Rows carrying a STRONG design name. Reported for a human, never written.

    These are not proposals. design_strong fails HOLDOUT B (see --rejected), so it may
    not be written - but a row whose design name belongs to one builder >=
    DESIGN_STRONG_SUPPORT times, with no rival strong name in the same row, is exactly
    the row a human can clear in seconds. The section header, where present, is shown
    beside it as agreeing or disagreeing.
    """
    out = []
    for p in c.pooled:
        if p["id"] in taken:
            continue
        design = c.collapse(c.lookup(p, "design_strong"))
        if len(design) != 1:
            continue
        b = next(iter(design))
        header = c.collapse({x for x in
                             (c.resolve_label(p[col])
                              for col in ("estate_name", "lot_address", "suburb"))
                             if x})
        if len(header) == 1:
            note = ("section header agrees"
                    if c.same_builder(b, next(iter(header)))
                    else f"section header DISAGREES ({c.display[next(iter(header))]})")
        else:
            note = "no section header"
        toks = sorted(t for t in (c.keys[p["id"]]["design_strong"] or ())
                      if c.index["design_strong"].get(t, {}).get(b, 0)
                      >= DESIGN_STRONG_SUPPORT)
        out.append({"row": p, "builder": c.display[b], "note": note, "tokens": toks})
    return out


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #

def section(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def report_population(c: Corpus) -> None:
    section("POPULATION")
    print(f"  named rows (attribution_scope='builder', builder known) : {len(c.named):>5}")
    print(f"    ... carrying source_text, so usable as evidence       : {len(c.named_text):>5}")
    print(f"  pooled rows with a blank builder                        : {len(c.pooled):>5}")
    print(f"  distinct builders after canonicalisation                : {len(c.display):>5}")
    for g in {frozenset(v) for v in c.relabel.values()}:
        print("  relabel group folded into one builder: "
              + " == ".join(sorted(c.display[m] for m in g)))
    print()
    print("  pooled rows per source file, and how many have a lot-level counterpart")
    print("  in the named set (channel 1 only):")
    for url, rows in sorted(c.pooled_files().items(), key=lambda x: -len(x[1])):
        m = sum(1 for p in rows
                if any(c.lookup(p, kn) for kn in ACCEPTED_KEYS))
        print(f"    {len(rows):>4} rows, {m:>4} matchable   {short_file(url)[:60]}")
    print()
    print("  This is the ceiling on channel 1: VIC Regional's designs (Empley 15,")
    print("  Westgarth, Arklay 17, Dunestone 22) occur ZERO times in the named set, so")
    print("  its builders are simply not in this database yet.")


def report_pooled_keys(c: Corpus) -> None:
    section("EVERY CANDIDATE KEY, MEASURED ON THE REAL POOLED ROWS")
    print("  has_key = pooled rows that can even form this key")
    print("  unique  = lands on exactly ONE builder (usable)")
    print("  ambig   = lands on several DIFFERENT builders (must stay blank)")
    print()
    print(f"  {'key':<20}{'has_key':>8}{'matched':>9}{'unique':>8}{'ambig':>7}   verdict")
    print("  " + "-" * 73)
    for kn in ALL_KEYS:
        has = matched = uniq = amb = 0
        for p in c.pooled:
            if c.keys[p["id"]][kn] is None:
                continue
            has += 1
            got = c.collapse(c.lookup(p, kn))
            if not got:
                continue
            matched += 1
            if len(got) == 1:
                uniq += 1
            else:
                amb += 1
        print(f"  {kn:<20}{has:>8}{matched:>9}{uniq:>8}{amb:>7}   "
              f"{'ACCEPTED' if kn in ACCEPTED_KEYS else 'rejected -- see HOLDOUT B'}")


def report_holdouts(c: Corpus, stats: dict[str, tuple]) -> None:
    section("HOLDOUT A / HOLDOUT B  --  the numbers that decide")
    print(f"  Both run over all {len(c.named_text)} named rows that carry source_text, by")
    print("  blanking the builder in memory and asking the key to put it back.")
    print()
    print("  A prec = of the rows it answered confidently, how many it got RIGHT.")
    print("  B fp   = how often it answers confidently when the true builder is not in")
    print("           the pool at all. Every such answer is WRONG. Most pooled rows are")
    print("           in exactly that situation, so B decides what gets used.")
    print()
    print(f"  {'key':<20}{'A uniq':>8}{'A ok':>7}{'A bad':>7}{'A prec':>9}"
          f"{'B falsepos':>12}{'B fp rate':>11}")
    print("  " + "-" * 76)
    for kn in ALL_KEYS + ("code_prefix",):
        a_u, a_ok, a_bad, _amb, b_fp, n = stats[kn]
        prec = f"{a_ok / a_u * 100:.1f}%" if a_u else "n/a"
        mark = " <-" if kn in ACCEPTED_KEYS or kn == "code_prefix" else ""
        print(f"  {kn:<20}{a_u:>8}{a_ok:>7}{a_bad:>7}{prec:>9}"
              f"{b_fp:>12}{b_fp / n * 100:>10.1f}%{mark}")
    print()
    print("  Stock-code prefixes found, and who owns them:")
    for p, cnt in sorted(c.prefix_support.items(), key=lambda x: -sum(x[1].values())):
        owners = ", ".join(f"{c.display[b]} x{v}" for b, v in cnt.most_common())
        verdict = "exclusive -> usable" if p in c.prefix_owner else "shared -> unusable"
        print(f"    {p:<6} {owners:<44} {verdict}")


def report_rejected(c: Corpus, stats: dict[str, tuple], file_notes: list[str]) -> None:
    section("WHY THE PLAUSIBLE-LOOKING SIGNALS WERE REJECTED")

    print("1. THE SECTION HEADER INSIDE A MIXED FILE  (the dangerous one)")
    print("   NSW Dual Jul.xlsx has builder banner rows. The extractor stored the last")
    print("   banner it saw in estate_name, and it STICKS: 'HUDSON HOMES: DUAL & DUPLEX'")
    print("   is on 12 of the 72 rows. It is right on 3 of them (Mt View Grange lots,")
    print("   whose SODIUM designs are Hudson's) and WRONG on 9 - those 9 are Eternal")
    print("   Homes lots, proven by an exact text match to Eternal's own stocklist:")
    shown = 0
    for p in c.pooled:
        hdr = c.resolve_label(p["estate_name"])
        if not hdr:
            continue
        lot = c.collapse(set().union(*[c.lookup(p, kn) for kn in ACCEPTED_KEYS]) or set())
        if len(lot) == 1 and not c.same_builder(hdr, next(iter(lot))):
            if shown < 4:
                print(f"     row {p['id']}: header says {c.display[hdr]}, the lot itself is "
                      f"{c.display[next(iter(lot))]}")
                print(f"              {(p['source_text'] or '')[:88]}")
            shown += 1
    print(f"   {shown} row(s) where the header is directly contradicted by hard evidence.")
    print("   A header is therefore only trusted at 100% file coverage (channel 3).")
    print()
    for n in file_notes:
        print("     " + n)

    print()
    print("2. THE HOUSE-DESIGN TOKEN  (the intuitive winner, and a trap)")
    a_u, a_ok, a_bad, _amb, b_fp, n = stats["design_token"]
    print(f"   HOLDOUT A looks superb: {a_ok}/{a_u} = "
          f"{a_ok / a_u * 100 if a_u else 0:.1f}%. Design names really are")
    print("   builder-proprietary - SODIUM -> Hudson Homes 47/47, Vesper -> Eternal")
    print("   Homes 21/21, Tuvala -> G-Developments 4/4.")
    print(f"   HOLDOUT B destroys it: {b_fp}/{n} = {b_fp / n * 100:.1f}% of rows get a")
    print("   confident WRONG builder once the true builder leaves the pool. On the")
    print("   pooled rows it happily labels VIC Regional lots 'Ausbuild' and 'Silkwood")
    print("   Homes' - builders whose design ranges share not one name with them.")

    print()
    print("3. PRICE ALONE")
    a_u, a_ok, a_bad, _amb, b_fp, n = stats["price_only"]
    print(f"   {a_u} confident answers in HOLDOUT A, {a_bad} of them wrong; "
          f"{b_fp}/{n} = {b_fp / n * 100:.1f}%")
    print("   false positives in HOLDOUT B. Round prices collide across states: pooled")
    print("   row 187 (VIC, Woodstock, $630,000) matches four FRD Homes lots in QLD.")

    print()
    print("4. PRICE + SUBURB and LAND+BUILD+PRICE COLUMNS  (clean, but dominated)")
    for kn in ("price_suburb", "money_cols_triple"):
        a_u, a_ok, a_bad, _amb, b_fp, n = stats[kn]
        print(f"   {kn:<18} A {a_ok}/{a_u} = "
              f"{a_ok / a_u * 100 if a_u else 0:.1f}%, B fp {b_fp}/{n}")
    print("   Both pass both holdouts, and both are still left out, for two reasons:")
    print("   they add ZERO pooled rows that price_numfp does not already cover, and")
    print("   price_suburb's clean score is an artefact the holdout cannot see - named")
    print("   rows have tidy suburbs, whereas the pooled side's suburb column carries")
    print("   'COAST COUNCIL', 'Dual Key', 'Duplex', 'GDEV', 'Price' and 'Available'.")
    print("   Adding a key that buys nothing can only add risk.")

    print()
    print("5. WHY price_lot AND price_landsqm ARE THIN, AND WHAT price_numfp FIXES")
    n_ls = sum(1 for p in c.pooled if p["land_sqm"])
    n_lot = sum(1 for p in c.pooled if (p["lot_number"] or "").strip())
    print(f"   land_sqm is populated on {n_ls} of {len(c.pooled)} pooled rows and")
    print(f"   lot_number on {n_lot}, because the pooled files put those numbers in")
    print("   columns the extractor never mapped - yet the numbers ARE in source_text.")
    print("   price_numfp reads them straight out of the text, which is why it covers")
    print(f"   all {len(c.pooled)} rows and finds 47 lots instead of 23.")

    print()
    print("6. EXACT STOCK CODE (not just its prefix)")
    owners: dict[str, set[str]] = defaultdict(set)
    for r in c.named_text:
        for code in codes(r["source_text"]):
            owners[code].add(canon_builder(r["builder_name"]))
    shared_codes = sum(1 for v in owners.values() if len(c.collapse(v)) > 1)
    resolved = sum(1 for p in c.pooled
                   if len(c.collapse(set().union(*[owners.get(x, set())
                                                   for x in codes(p["source_text"])]
                                                 or [set()]))) == 1)
    print(f"   {len(owners)} distinct codes in the named set, {shared_codes} of them")
    print(f"   shared between builders - a perfect key that resolves {resolved} pooled rows,")
    print("   because the pooled files list different lots, so no code appears on both")
    print("   sides. Kept in the report because the PREFIX does work (channel 2).")


def report_proposals(c: Corpus, accept: list[dict], conflict: list[dict],
                     clash: list[dict], review: list[dict], verbose: bool) -> None:
    section("PROPOSALS")
    n_pool = len(c.pooled)
    print(f"  rows a builder can be named on  : {len(accept):>4} of {n_pool}"
          f"   ({len(accept) / n_pool * 100:.1f}%)")
    print(f"  rows where channels disagreed   : {len(conflict):>4}   (stay blank)")
    print(f"  rows where a key hit >1 builder : {len(clash):>4}   (stay blank)")
    named_ids = {r["row"]["id"] for r in accept} | {r["row"]["id"] for r in conflict} \
        | {r["row"]["id"] for r in clash}
    print(f"  rows with no evidence at all    : {n_pool - len(named_ids):>4}   (stay blank)")
    print()
    for b, n in Counter(r["builder"] for r in accept).most_common():
        print(f"    {n:>4}  {b}")
    print()
    print("  by channel:")
    for ch, n in Counter(tuple(r["channels"]) for r in accept).most_common():
        print(f"    {n:>4}  {' + '.join(ch)}")
    print()
    print("  corroboration - independent keys agreeing per row:")
    for k, n in sorted(Counter(len(r["keys"]) for r in accept).items()):
        print(f"    {n:>4} row(s) backed by {k} independent key(s)")

    # state cross-check
    states: dict[str, set[str]] = defaultdict(set)
    for r in c.named:
        states[canon_builder(r["builder_name"])].add((r["state"] or "").upper())
    clashes = [r for r in accept
               if r["row"]["state"]
               and (r["row"]["state"] or "").upper()
               not in states[canon_builder(r["builder"])]]
    print()
    print(f"  INDEPENDENT CROSS-CHECK: proposals whose state is not one the named rows")
    print(f"  say that builder trades in: {len(clashes)}")
    if clashes:
        by_b = Counter(r["builder"] for r in clashes)
        for b, k in by_b.most_common():
            cb = canon_builder(b)
            print(f"    {k:>4}  {b}: pooled rows say "
                  f"{sorted({(r['row']['state'] or '?') for r in clashes if r['builder'] == b})}"
                  f", named rows say {sorted(states[cb])}")
        print("    Read this the other way round. The pooled rows carry real NSW")
        print("    addresses and postcodes (Wadalba 2259, Bellbird 2325, Denman 2328);")
        print("    the named rows' state came from a file-level source_state_hint, not")
        print("    from the listing. The match is what EXPOSES the wrong state on the")
        print("    named side - it is not evidence against the match. Worth a separate")
        print("    look at state_source='stocklist file name' rows.")

    for label, bucket in (("CHANNELS DISAGREED", conflict), ("KEY HIT >1 BUILDER", clash)):
        if bucket:
            print()
            print(f"  {label} (left blank on purpose):")
            for r in bucket:
                names = r.get("builders") or sorted(c.display[b] for b in r["builders"])
                if isinstance(names, set):
                    names = sorted(names)
                print(f"    row {r['row']['id']:>5}  {' / '.join(map(str, names))}")
                print(f"           {(r['row']['source_text'] or '')[:88]}")

    if review:
        print()
        print("  REVIEW TIER - NOT proposed, NOT written by --apply.")
        print(f"  Rows carrying a design name that the named set gives to ONE builder")
        print(f"  >= {DESIGN_STRONG_SUPPORT} times. design_strong fails HOLDOUT B so it may not be")
        print(f"  written, but these are the {len(review)} row(s) a human can clear fastest:")
        for r in review:
            print(f"    row {r['row']['id']:>5}  probably {r['builder']:<18} "
                  f"via {','.join(r['tokens'])}  [{r['note']}]")
            print(f"           {(r['row']['source_text'] or '')[:88]}")

    if verbose and accept:
        print()
        print("  EVERY PROPOSAL WITH ITS EVIDENCE:")
        for r in accept:
            p = r["row"]
            print()
            print(f"    row {p['id']}  ->  {r['builder']}   "
                  f"[{', '.join(r['channels'])}: {', '.join(r['keys'])}]")
            print(f"      pooled : {(p['source_text'] or '')[:118]}")
            if r["channels"] == ["file_title"]:
                print(f"      title  : the whole file declares this builder")
                continue
            if r["channels"] == ["code_prefix"]:
                pre = [k for k in r["keys"] if k.startswith("code:")]
                print(f"      code   : {', '.join(pre)} is used by this builder and no other")
                continue
            twin, via, shared = None, None, None
            want = canon_builder(r["builder"])
            for kn in ACCEPTED_KEYS:
                kv = c.keys[p["id"]][kn]
                if kv is None or kn not in r["keys"]:
                    continue
                for n in c.named_text:
                    if canon_builder(n["builder_name"]) != want:
                        continue
                    if kn == "price_numfp":
                        ov = c.numfp[p["id"]] & c.numfp[n["id"]]
                        # show the BEST twin, not the first, so the evidence line is
                        # the strongest one available rather than an arbitrary sibling
                        if round(n["price"]) == round(p["price"]) \
                                and len(ov) >= NUMFP_MIN_SHARED \
                                and (shared is None or len(ov) > len(shared)):
                            twin, via, shared = n, kn, sorted(ov)
                        continue
                    elif c.keys[n["id"]][kn] == kv:
                        twin, via = n, kn
                        break
                if twin:
                    break
            if twin is not None:
                print(f"      named  : {(twin['source_text'] or '')[:118]}")
                print(f"      via    : {via}   file: {short_file(twin['source_url'] or '')[:56]}")
                if shared:
                    print(f"      shared : price ${round(p['price']):,} + {shared}")


# --------------------------------------------------------------------------- #
# write
# --------------------------------------------------------------------------- #

WRITE_CAVEAT = """
WRITE CAVEAT - read this before --apply
  1. builder_name is one of database._HASH_FIELDS, so writing it CHANGES the row's
     content_hash, i.e. its identity. --apply must be followed by:
         python -X utf8 migrate_buildings_identity.py --force
  2. Even then the patch is NOT durable. The next harvest recomputes the hash from
     the pooled FILE, where the builder is still blank, will not find the row it
     renamed, and will INSERT A FRESH BLANK ROW next to it. The durable fix is to do
     this matching in the extractor, BEFORE the hash is computed, so a harvested row
     is born with its builder. Treat --apply as a stopgap for the current export.
  3. attribution_scope stays 'state_pooled' on purpose: it records where the row came
     from, and rewriting it would move the row's identity a second time.
"""

BUILDER_SOURCE_TAG = "recovered from per-builder rows"


def apply_writes(db_path: Path, accept: list[dict]) -> int:
    print(WRITE_CAVEAT)
    if not accept:
        print("[i] nothing to write")
        return 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = str(db_path) + f".bak-prebuilderrecover-{stamp}"
    shutil.copy2(str(db_path), dest)
    print(f"[+] backup written: {dest}")

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        n = 0
        for r in accept:
            cur.execute(
                "UPDATE buildings SET builder_name=?, builder_matched=?, builder_source=? "
                "WHERE id=? AND TRIM(COALESCE(builder_name,''))=''",
                (r["builder"], r["builder"],
                 f"{BUILDER_SOURCE_TAG} [{'+'.join(r['keys'])}]", r["row"]["id"]))
            n += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    print(f"[+] {n} row(s) updated")
    print("[!] NOW RUN: python -X utf8 migrate_buildings_identity.py --force")
    return n


# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="write the accepted builder names (default: report only)")
    ap.add_argument("--verbose", action="store_true",
                    help="print every proposal with the named row it matched")
    ap.add_argument("--rejected", action="store_true",
                    help="print why the weaker signals were rejected")
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[ABORT] no such database: {db_path}")
        return 1

    c = Corpus(db_path)
    report_population(c)
    report_pooled_keys(c)
    stats = run_holdouts(c)
    report_holdouts(c, stats)

    lot_ok, lot_clash = channel_lot_match(c)
    code_ok, code_clash = channel_code_prefix(c)
    title_ok, file_notes = channel_file_title(c)
    accept, conflict = merge_channels(c, [lot_ok, code_ok, title_ok])
    clash = lot_clash + code_clash
    taken = {r["row"]["id"] for r in accept} | {r["row"]["id"] for r in conflict}
    review = review_tier(c, taken)

    if args.rejected:
        report_rejected(c, stats, file_notes)
    report_proposals(c, accept, conflict, clash, review, args.verbose)

    if args.apply:
        apply_writes(db_path, accept)
    else:
        print()
        print("  (report only - nothing written. Use --apply to write.)")
        print(WRITE_CAVEAT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
