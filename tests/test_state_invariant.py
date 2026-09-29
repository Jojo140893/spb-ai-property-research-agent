"""Never a state origin/main would not give -- the differential harness (29 Sep 2026, round 2).

Round 1 fixed why rows landed in the wrong state -- a lot number read as a postcode (AVIA
2234), a suburb guessed from an estate name (Silkwood 11510), from an "X, Locality" label
(Verv 11494), from a house design (Hattan 14913/14914) -- and on the night's rows every
change was right. But both reviews then found new ways to a wrong state on rows the night
did not hold: "Lot 5, Wyndham Vale Jubilee" off the VIC page went to QLD, the Eternal Homes
lot at "77 Brooklyn Park Rise" off the NSW page to SA, "63 Oxford Street, Joyner Banksia"
off the QLD page to NSW; and the replay's misfile check went blind to misfiles origin/main
catches ("Available Tarneit Paddington 180m2" filed QLD on Paddington). A reading can be
better on every row a night holds and still worse on the next harvest's.

So the bar is stated against origin/main 31d13ef itself, frozen in tests/_origin_main_*.py:
the new code may leave a row blank, and may correct round 1's misreadings, but must never
put a row in a state origin/main would not, unless it is

  (i)   the row's own page state (source_state_hint), or its file name's;
  (ii)  proven by the row's own postcode, written where a postcode is written (after a
        place of its state, or its state's code) and not its lot or another number; or
  (iii) round 1's one documented correction that is neither: with no page state, the
        longer place name the row writes around origin/main's guess ("Clyde North" for
        Clyde, "Wyndham Vale" for Wyndham -- 24 Hattan email rows);

and the misfile check (state_resolver.text_locality_elsewhere, which enrich's row-text
overrule and search_regressions.locality_check both call) may keep quiet where origin/main
spoke only on a design its row's page backs (AVIA 7810, "Rhodes Paddington 180m2") --
never name a place origin/main's would not.

Each is checked over a corpus generated from real suburbs, estates, designs, facades,
streets, lot numbers, prices and page hints of every state (tests/_state_corpus.py), run
through the real enrich_buildings.main() on a temporary database and through origin/main's
decision, frozen. The scratch harness this is cut down from ran the same checks over the 29
Sep copy's 23,448 rows plus 25,000 generated ones (and 2.1 million asks of the misfile
check) with 0 exceptions; this keeps a representative 2,000 per regime, fast enough for
the suite.
"""
import re
import sqlite3

import pytest

import config
import enrich_buildings
import state_resolver as sr
from database import ResearchDatabase
from geo import SuburbGeoIndex
from tests import _origin_main_enrich as origin
from tests import _state_corpus as corpus

_COLS = ("id", "builder_name", "source_channel", "lot_address", "suburb", "state",
         "state_source", "estate_name", "source_text", "source_url", "stocklist_file",
         "source_state_hint", "postcode", "lot_number", "superseded_by", "content_hash",
         "price", "last_seen")
_W = re.compile(r"[A-Za-z][A-Za-z'\-]*")
STATES = corpus.STATES


@pytest.fixture(scope="module")
def geo():
    return SuburbGeoIndex()


# --------------------------------------------------------------- the allowed exceptions
#
# Stated here independently of the code under test, so the harness does not grade the code
# with the code's own rules.

def _longer_names(text, guess, geo):
    """(iii): the states of one-state place names the text writes that contain origin/main's
    guess as whole words and are longer."""
    toks = [t.rstrip("-'") for t in _W.findall(str(text or ""))]
    want = [w.lower() for w in _W.findall(str(guess or ""))]
    out = set()
    if not want:
        return out
    for size in (2, 3):
        for k in range(len(toks) - size + 1):
            span = [t.lower() for t in toks[k:k + size]]
            if any(span[j:j + len(want)] == want for j in range(size - len(want) + 1)):
                states = geo.states_for_suburb(" ".join(toks[k:k + size]))
                if len(states) == 1:
                    out.add(states[0])
    return out


def _stated_postcode(r, geo):
    """(ii): the state of the row's postcode when its text writes it straight after that
    state's code or a place of that state, and it is neither the lot_number nor the number
    opening a line."""
    pc = re.sub(r"\D", "", str(r["postcode"] or ""))
    st = sr.state_from_postcode(pc)
    if len(pc) != 4 or not st:
        return ""
    if pc.lstrip("0") in {n.lstrip("0") for n in re.findall(r"\d+", str(r["lot_number"] or ""))}:
        return ""
    for text in (str(r["source_text"] or ""), str(r["lot_address"] or "")):
        if re.match(r"^\W*" + pc + r"\b", text):
            return ""
        for m in re.finditer(r"(?<![\d.$-])" + pc + r"(?![\d.])", text):
            head = text[:m.start()].rstrip(" ,")
            if head.endswith(" " + st) or head == st:
                return st
            words = _W.findall(head[-40:])
            for size in (3, 2, 1):
                if (len(words) >= size and re.search(r"[A-Za-z]$", head)
                        and st in geo.states_for_suburb(" ".join(words[-size:]))):
                    return st
    return ""


def _allowed(r, old_state, geo):
    page = sr.normalise_state(r["source_state_hint"])
    ok = {old_state or "", "", page, origin.state_from_filename(r["source_url"] or ""),
          _stated_postcode(r, geo)}
    if not (r["suburb"] or "").strip() and not page:
        guess = origin.old_guess(geo, r["lot_address"] or "", (r["state"] or "").strip().upper())
        ok |= _longer_names(r["lot_address"], guess, geo)
    return ok


# --------------------------------------------------------------------------- the runs

def _rows(geo, n, seed, stock=False):
    rows = []
    records = corpus.generate(geo, n, seed=seed)
    for k, rec in enumerate(records):
        row = {c: rec.get(c) for c in _COLS}
        row.update(id=k + 1, source_channel="Generated", content_hash=f"v2:gen{seed}-{k}",
                   stocklist_file=f"generated:{seed}:{k}", source_url=f"generated:{seed}:{k}",
                   superseded_by=None, price=500000.0, last_seen="29/09/2026")
        rows.append(row)
    if stock:
        # the catalogue stocking every place the corpus writes, in each of its states
        names = sorted({w for rec in records
                        for w in list(rec["words"].values()) + [rec["suburb"] or ""] if w})
        k = len(rows)
        for name in names:
            for st in corpus._places(geo, name):
                k += 1
                rows.append({c: None for c in _COLS} | dict(
                    id=k, builder_name="Stock", source_channel="Generated",
                    lot_address=f"Lot 1, {name}", source_text=f"Lot 1 {name} {st}",
                    suburb=name, state=st, state_source="listing text",
                    source_state_hint=st, stocklist_file=f"stock:{k}",
                    source_url=f"stock:{k}", content_hash=f"v2:stock{seed}-{k}",
                    price=500000.0, last_seen="29/09/2026"))
    return rows


def _enrich(tmp_path, monkeypatch, rows):
    db = tmp_path / "enrich.db"
    ResearchDatabase(db)
    conn = sqlite3.connect(str(db))
    for r in rows:
        conn.execute(f"INSERT INTO buildings ({', '.join(_COLS)}) "
                     f"VALUES ({', '.join('?' for _ in _COLS)})", [r[c] for c in _COLS])
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DATABASE_PATH", db)
    assert enrich_buildings.main([]) == 0
    conn = sqlite3.connect(str(db))
    try:
        return {i: (s, src) for i, s, src in
                conn.execute("SELECT id, state, state_source FROM buildings")}
    finally:
        conn.close()


def _violations(rows, got, geo):
    before = origin.old_enrich(rows, geo)
    out, changed = [], 0
    for r in rows:
        new, old = got[r["id"]][0] or "", before[r["id"]][0] or ""
        changed += new != old
        if new != old and new not in _allowed(r, old, geo):
            out.append((r["id"], old, new, got[r["id"]][1], r["lot_address"],
                        r["source_state_hint"], r["suburb"], r["state"], r["state_source"]))
    return out, changed


@pytest.mark.parametrize("seed, stock", [(29092026, False), (4242, True)])
def test_enrich_never_gives_a_state_origin_main_would_not(tmp_path, monkeypatch, geo, seed,
                                                         stock):
    rows = _rows(geo, 2000, seed, stock)
    got = _enrich(tmp_path, monkeypatch, rows)
    bad, changed = _violations(rows, got, geo)
    assert bad == [], f"{len(bad)} rows in a state origin/main would not give: {bad[:10]}"
    # and the corpus is one the two codes answer differently on, or this proves nothing
    # (measured: 150 of 2,000 rows unstocked, 65 with every place stocked)
    assert changed > 50


def test_the_harness_can_fail(tmp_path, monkeypatch, geo):
    """A silent check reads as a pass: let the reading through unbounded -- round 1's code
    path, a reading taken wherever no page contradicts it -- and the harness must see it."""
    monkeypatch.setattr(enrich_buildings, "guess_suburb",
                        lambda r, geo_, stocked, decide, origin_=None: sr.suburb_in_row_text(
                            r["lot_address"] or "", geo_, prefer=r["source_state_hint"] or "",
                            estate=r["estate_name"], stocked=stocked))
    monkeypatch.setattr(enrich_buildings, "extends_the_guess", lambda *a: True)
    rows = _rows(geo, 2000, 29092026)
    bad, _changed = _violations(rows, _enrich(tmp_path, monkeypatch, rows), geo)
    assert len(bad) > 10            # measured: 19


# ------------------------------------------------------------------- the misfile check

def test_the_misfile_check_never_names_what_origin_main_would_not(geo):
    """Every generated row asked as if filed in each state, on its own column, a blank one
    or a place-word of its line of that state, with its own page, the filed state's or
    none: the new answer is origin/main's or nothing, and nothing only on a design its
    page backs (the documented 7810 case)."""
    named, quiet = [], []
    for rec in corpus.generate(geo, 400, seed=11):
        text = " ".join(str(rec[k] or "") for k in ("source_text", "lot_address"))
        toks, breaks = sr._tokens(text)
        places = {" ".join(toks[i:i + s]): st
                  for (i, s), st in sr._place_spans(toks, breaks, geo).items()}
        stocked = {(p.lower(), s) for p, sts in places.items() for s in sts}
        for filed in STATES:
            old = origin.sr.text_locality_elsewhere(text, filed, geo, stocked,
                                                    estate=rec["estate_name"] or "")
            columns = {rec["suburb"] or "", ""} | {p for p, sts in places.items()
                                                   if filed in sts}
            for column in columns:
                for page in {rec["source_state_hint"] or "", filed, ""}:
                    new = sr.text_locality_elsewhere(text, filed, geo, stocked,
                                                     estate=rec["estate_name"] or "",
                                                     suburb=column, page=page)
                    if new is not None and new != old:
                        named.append((text, filed, column, page, old, new))
                    elif old and new is None and not (
                            page == filed and column and not sr._written_in(toks, breaks,
                                                                           column)):
                        quiet.append((text, filed, column, page, old))
    assert named == [], named[:5]
    assert quiet == [], quiet[:5]


def test_a_rows_proof_is_one_origin_main_gives(geo):
    """own_state -- what a row proves, its vote on whether its file is national and a state
    that overrules its page -- on the guess enrich makes: never a state origin/main's
    enrich does not give the row, bar (i)-(iii)."""
    rows = _rows(geo, 1500, 7)
    evidence = origin.overrule_evidence(rows, geo)
    before = origin.old_enrich(rows, geo, evidence=evidence)

    def decide(r, suburb):          # enrich's, for a row of its own file
        mine, _ = sr.own_state(postcode=r["postcode"], suburb=suburb, estate=r["estate_name"],
                               address=r["lot_address"], geo=geo, lot_number=r["lot_number"],
                               row_text=enrich_buildings.row_text(r), street_cell=False)
        stored = (r["state"] or "").strip().upper()
        if stored and enrich_buildings.STATE_RE.fullmatch(stored) \
                and (r["state_source"] or "") not in enrich_buildings.HINT_ONLY_SIGNALS \
                and not (enrich_buildings.cites_a_postcode(r["state_source"])
                         and sr.postcode_is_lot_number(r["postcode"], r["lot_number"])):
            hit = _check(r, suburb, stored, r["state_source"])
            return (hit[0] if hit else stored), mine or "", True
        st, sig = sr.resolve_state(postcode=r["postcode"], suburb=suburb,
                                   page_state=r["source_state_hint"] or "", geo=geo,
                                   listing_state=mine, lot_number=r["lot_number"],
                                   row_text=enrich_buildings.row_text(r))
        hit = _check(r, suburb, st, sig)
        st = hit[0] if hit else st
        return st or enrich_buildings.state_from_filename(r["source_url"] or "") or "", \
            mine or "", False

    def _check(r, suburb, state, signal):
        if not (state and enrich_buildings.rests_on_a_suburb_match(signal, False)):
            return None
        return enrich_buildings.overruled_state(r, suburb, state, geo, evidence)

    bad = []
    for r in rows:
        column = (r["suburb"] or "").strip()
        guess = origin.old_guess(geo, r["lot_address"] or "", (r["state"] or "").strip().upper())
        new_guess = column or enrich_buildings.guess_suburb(r, geo, evidence[0], decide,
                                                            guess or "")
        proof, _ = sr.own_state(postcode=r["postcode"], suburb=new_guess,
                                estate=r["estate_name"], address=r["lot_address"], geo=geo,
                                lot_number=r["lot_number"],
                                row_text=enrich_buildings.row_text(r),
                                street_cell=bool(column))
        old_proof, _ = origin.sr.own_state(postcode=r["postcode"], suburb=column or guess,
                                           estate=r["estate_name"], address=r["lot_address"],
                                           geo=geo, lot_number=r["lot_number"])
        if proof and proof != old_proof and proof not in _allowed(r, before[r["id"]][0], geo):
            bad.append((r["id"], r["lot_address"], old_proof, proof))
    assert bad == [], bad[:10]


# ------------------------------------------------------ the reviews' own broken inputs

_REVIEW_PLANTS = [
    # (lot_address, source_text, suburb, page, stored state, stored signal, estate, want)
    # review 1 (verify-2): the new wrong-move paths, each through the real enrich
    ("Lot 5, Wyndham Vale Jubilee", None, None, "VIC", None, None, None, {"VIC"}),
    ("Lot 5, Wyndham Vale Jubilee", None, None, None, None, None, None, {None}),
    ("63 Oxford Street, Joyner Banksia", None, None, "QLD", None, None, None, {"QLD"}),
    ("Lot 518, Davis Vineyard", "518 Davis Vineyard Estate Diggers Rest Sold Swindon 20",
     None, "VIC", None, None, "Davis Vineyard", {"VIC"}),
    ("77 Brooklyn Park Rise, Park Rise . 28 items",
     "House & Land NSW Dora Creek Lot 77 Brooklyn Park Rise 517 sqm", None, "NSW", None, None,
     "Park Rise . 28 items", {"NSW"}),
    ("Lot 5 Wollert Rhodes 25", None, "Rhodes", None, "NSW", "suburb", None, {"VIC"}),
    ("Lot 5 Tarneit Rosewood 25", None, "Rosewood", None, "QLD", "suburb", None, {"VIC"}),
    ("Lot 12 Point Cook Coastal Village", None, None, None, None, None, None, {"VIC"}),
    ("Lot 5 Tarneit Traditional", None, None, None, None, None, None, {"VIC"}),
    ("Grange West 56 Werribee Titled 400", None, None, None, None, None, None, {"VIC"}),
    ("Lot 5 Glenroy Classic", None, None, None, "VIC", "suburb", None, {"VIC"}),
    # the rows round 1 put right, which must stay right
    ("5629 The Grove, Tarneit", "2-Part Detached SS West 5629 The Grove Tarneit 300 $350,000",
     None, "VIC", "NSW", "listing locality", None, {"VIC"}),
    ("Available Titled Townhouse 44 215 10.86 Verona SG Corner | Roselle 15.72 3 2 1", None,
     None, None, "NSW", "suburb", "Springvale Double", {None}),
    ("Available Clyde North House 263 10.5 Langdon 16 3 2 2 $377,100", None, None, None,
     None, None, None, {"VIC"}),
    ("Lot 27102, Windermere Mambourin", None, None, "QLD", None, None, None, {"VIC"}),
]


def test_the_reviews_broken_inputs_land_where_they_should(tmp_path, monkeypatch, geo):
    rows = []
    for k, (label, line, column, page, state, signal, estate, _want) in enumerate(
            _REVIEW_PLANTS, 1):
        rows.append({c: None for c in _COLS} | dict(
            id=k, builder_name="Review Plant", source_channel="Generated", lot_address=label,
            source_text=line or label, suburb=column, source_state_hint=page, state=state,
            state_source=signal, estate_name=estate, stocklist_file=f"review:{k}",
            source_url=f"review:{k}", content_hash=f"v2:review{k:03d}", price=500000.0,
            last_seen="29/09/2026"))
    # the catalogue's stock the reviews planted them beside (their plantB): Jubilee QLD and
    # Banksia NSW proved by postcode, Wollert, Tarneit and Mambourin VIC by listing text
    k = len(rows)
    for name, st, pc in (("Jubilee", "QLD", "4300"), ("Banksia", "NSW", "2216"),
                         ("Wollert", "VIC", None), ("Tarneit", "VIC", None),
                         ("Mambourin", "VIC", None), ("Vineyard", "NSW", None)):
        k += 1
        rows.append({c: None for c in _COLS} | dict(
            id=k, builder_name="Stock", source_channel="Generated", lot_address=f"Lot 1, {name}",
            source_text=f"Lot 1 {name} {st} {pc or ''}", suburb=name, state=st,
            state_source="suburb + postcode" if pc else "listing text", postcode=pc,
            source_state_hint=st, stocklist_file=f"stock:{k}", source_url=f"stock:{k}",
            content_hash=f"v2:stock{k:03d}", price=500000.0, last_seen="29/09/2026"))
    got = _enrich(tmp_path, monkeypatch, rows)
    wrong = [(label, got[k][0], want) for k, (label, *_x, want) in enumerate(_REVIEW_PLANTS, 1)
             if got[k][0] not in want]
    assert wrong == []
    bad, _changed = _violations(rows, got, geo)
    assert bad == []


@pytest.mark.parametrize("text, prefer, estate, want", [
    ("63 Oxford Street, Joyner Banksia", "QLD", "", "Joyner"),
    ("155 Wattle Green, Burpengary Banksia", "QLD", "", "Burpengary"),
    ("Available Clyde North Double Storey 186 6 Embla F | Ceduna 9.66", "VIC", "", "Clyde North"),
    ("Lot 3 Wadalba Estate Ceduna 9.66", "NSW", "", "Wadalba"),
    ("Lot 3 Mambourin Estate Rhodes 20", "VIC", "", "Mambourin"),
    ("Lot 50 Springvale Single 3 2 1 Verona 20", "VIC", "", "Springvale"),
    ("Lot 5, Redbank Plains Sienna Eden", "QLD", "", "Redbank Plains"),
    ("Lot 1, Riverstone Springs", "QLD", "", None),
    ("Lot 9, Ellenbrook Springs", "", "", "Ellenbrook"),
    ("Lot 39, Logan Reserve Riverleigh", "", "", "Logan Reserve"),
    ("18 Wiltipoll Road, Wiltipoll Road Mambourin Windermere", "", "", "Mambourin"),
    ("REGISTER NOW Sebastopol Ringwood North Estate August 2027", "VIC", "Ringwood North",
     "Sebastopol"),
])
def test_the_reviews_readings(geo, text, prefer, estate, want):
    assert sr.suburb_in_row_text(text, geo, prefer, estate) == want


@pytest.mark.parametrize("text, filed, column, want", [
    ("Available Wollert Riverhills Titled Townhouse 618", "QLD", "Riverhills",
     ("Wollert", "VIC")),
    ("Available Tarneit Paddington 180m2 4 2 2", "QLD", "Paddington", ("Tarneit", "VIC")),
    ("Available Tarneit Rhodes 20 4 2 2", "NSW", "Rhodes", ("Tarneit", "VIC")),
    ("66 Taylors Ridge Walloon Banksia 183 - Urban", "NSW", "Banksia", ("Walloon", "QLD")),
])
def test_the_misfile_check_sees_what_origin_main_sees(geo, text, filed, column, want):
    """Review round 1 (verify-3): each was missed once the catalogue stocked the misleading
    word in the filed state -- Paddington QLD is stocked tonight, by AVIA's facade rows."""
    stocked = {(want[0].lower(), want[1]), (column.lower(), filed)}
    for page in ("", filed):
        assert sr.text_locality_elsewhere(text, filed, geo, stocked, suburb=column,
                                          page=page) == want
