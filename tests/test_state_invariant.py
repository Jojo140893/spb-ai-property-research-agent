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

  (i)   the row's own page state (source_state_hint -- or, with none tonight, the state its
        stored signal records the page and another signal agreeing on: _page), or its
        file name's;
  (ii)  proven by the row's own postcode, written where a postcode is written (after a
        place of its state, or its state's code) and not its lot or another number;
  (iii) round 1's one documented correction that is neither: with no page state, where
        origin/main's answer is blank, the longer place name the row's own LINE opens
        with around origin/main's guess ("Available Wyndham Vale ..." for Wyndham,
        "Available Clyde North ..." for Clyde -- 23 Hattan email rows), when nothing else
        in the row names another state -- and on a generated row, only its true state; or
  (iv)  origin/main's answer on the postcode column as the run leaves it (round 3): a lot
        number enrich takes out of the column is out of it for origin/main the next night;
  (v)   (24 Oct 2026) off a page, the state of a one-state place the row writes straight
        after a street, where every place it writes is a place of that state and it states
        no other, and the place is not taken from its estate -- "1524 Gilchrist Street,
        BRISBANE WEST", QLD, off APLACE's VIC page (_address_state) -- and on a generated
        row, only its true state;

(i)'s missing hint and (iv) are round 3's (29 Sep 2026), for the harness over many nights
(tests/test_state_multinight.py): without them the bound walked with this step's own writes
-- a page-backed row's hint dropped for a night, or its lot number cleared from the postcode
column, moved it once to origin/main's answer and once more the next night;

and the misfile check (state_resolver.text_locality_elsewhere, which enrich's row-text
overrule calls) may keep quiet where origin/main spoke only on a design and its facade code
its row's page backs (AVIA 7810, "Rhodes Paddington 180m2") -- never name a place
origin/main's would not -- while the nightly replay's WARN (search_regressions.
locality_check) is given wherever origin/main's is.

Round 3, 29 Sep 2026: round 2's (iii) was any one-state longer name anywhere in the LABEL,
and its misfile silence took a place of the filed state after the name ('followed') as a
facade; both reviews of round 2 found wrong moves through the first ("Lot 3772, Redwood
Park" on a Clyde North lot to SA) and genuine misfiles lost through the second (7889
"Walloon Banksia" filed NSW on a heading column). The harness had graded the code with the
code's own (iii). It now states (iii) the narrow way, checks it against the generated
rows' truth, generates the reviews' shapes (tests/_state_corpus.py, templates 35-42), and
checks that it sees round 2's rule when that rule is put back.

Each is checked over a corpus generated from real suburbs, estates, designs, facades,
streets, lot numbers, prices and page hints of every state (tests/_state_corpus.py), run
through the real enrich_buildings.main() on a temporary database and through origin/main's
decision, frozen. The scratch harness this is cut down from ran the same checks over the 29
Sep copy's 23,448 rows plus 32,000 generated ones, and the reviews' own harnesses over
about 140,000 more, with 0 exceptions; this keeps a representative 2,000 per regime, fast
enough for the suite.
"""
import io
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

_STATUS = {"available", "register", "now", "sold", "on", "hold", "under", "offer",
           "reserved", "release"}
_STREETISH = re.compile(r"^(?:st|street|rd|road|dr|drive|ave|avenue|av|cres|crescent|cct|"
                        r"circuit|pde|parade|way|ct|court|cl|close|pl|place|blvd|boulevard|"
                        r"tce|terrace|gr|grove|lane|ln|walk|link|vista|estate|estates|park|"
                        r"rise|point|springs|heights|range|river|lakes?|green|views?|hills?|"
                        r"waters|shores|quarter)$", re.IGNORECASE)
_FULL_NAMES = {"NSW": "new south wales", "VIC": "victoria", "QLD": "queensland",
               "SA": "south australia", "WA": "western australia", "TAS": "tasmania",
               "NT": "northern territory", "ACT": "australian capital territory"}


def _states_stated(r, geo):
    """Every state the row itself states: a state code in capitals, a state's name spelled
    out, or a four-digit number of a state's range written straight after that state's code
    or a place of that state -- anywhere in its line or label, postcode column or not."""
    out = set()
    for text in (str(r["source_text"] or ""), str(r["lot_address"] or "")):
        out |= set(re.findall(r"\b(NSW|VIC|QLD|SA|WA|NT|ACT|TAS)\b", text))
        out |= {st for st, name in _FULL_NAMES.items() if name in text.lower()}
        for m in re.finditer(r"(?<![\d.$-])(\d{4})(?![\d.])", text):
            st = sr.state_from_postcode(m.group(1))
            head = text[:m.start()].rstrip(" ,")
            words = _W.findall(head[-40:])
            if st and re.search(r"[A-Za-z]$", head) and (
                    head.endswith(" " + st) or head == st
                    or any(len(words) >= k and st in geo.states_for_suburb(" ".join(words[-k:]))
                           for k in (3, 2, 1))):
                out.add(st)
    return out


def _longer_name(r, guess, geo):
    """(iii), round 1's one correction that is neither a page nor a postcode, as its 23
    Hattan rows show it ("Available Wyndham Vale Q3 2026 Townhouse ...", origin/main's
    guess Wyndham): the row's LINE opens -- after nothing but "Available" or the like --
    with a one-state place name of two or three words that holds origin/main's guess as
    whole words, with no street, estate or region word after it; it is not the row's estate
    or a part of it; and nothing else the row says names another state. The state, or ''.

    Round 3, 29 Sep 2026: round 2 read (iii) as any such name anywhere in the LABEL, and
    both reviews found it a road to states origin/main never gives ("Lot 3772, Redwood
    Park" on a Clyde North lot to SA)."""
    toks = [t.rstrip("-'") for t in _W.findall(str(r["source_text"] or ""))]
    want = [w.lower() for w in _W.findall(str(guess or ""))]
    low = [t.lower() for t in toks]
    k = 0
    while k < len(low) and low[k] in _STATUS:
        k += 1
    for size in (3, 2):
        span = low[k:k + size]
        if len(span) < size or len(want) >= size or not any(
                span[j:j + len(want)] == want for j in range(size - len(want) + 1)):
            continue
        states = geo.states_for_suburb(" ".join(toks[k:k + size]))
        nxt = toks[k + size] if k + size < len(toks) else ""
        if len(states) != 1 or (nxt and _STREETISH.match(nxt)):
            continue
        estate = [w.lower() for w in _W.findall(str(r["estate_name"] or ""))]
        if estate and (" ".join(estate) in " ".join(span) or " ".join(span) in " ".join(estate)):
            return ""
        if _states_stated(r, geo) - {states[0]}:
            return ""
        return states[0]
    return ""


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


def _page(r):
    """(i)'s page: the row's hint -- or, when tonight's row carries none, the state its
    stored signal records the page and another signal agreeing on ('suburb + e-agent page',
    'postcode + e-agent page'; not the page alone). Round 3, 29 Sep
    2026: a hint missing for a night is not a page that disagrees; read as none, the
    multi-night harness's night with a sheet's hints dropped moved 494 page-backed rows
    by origin/main's row-text check, for good, where origin/main's own lineage kept them
    blank. (The harvest writes the hint on INSERT only, so a stored row never loses it in
    production; the harness takes it away to see what the code does if it ever does.)"""
    page = sr.normalise_state(r["source_state_hint"])
    if page:
        return page
    st = sr.normalise_state(r["state"])
    parts = [p.strip() for p in str(r["state_source"] or "").split("+")]
    if st and "e-agent page" in parts and len(parts) > 1:
        return st
    return ""


_STREET_WORD = re.compile(r"^(?:st|street|rd|road|dr|drive|ave|avenue|av|cres|crescent|cct|"
                          r"circuit|pde|parade|way|ct|court|cl|close|pl|place|blvd|boulevard|"
                          r"tce|terrace|lane|ln|esplanade|esp|hwy|highway|grove|gr|walk|"
                          r"link|loop|mews|row|square|track|trail|rise|green|retreat|chase|"
                          r"glade|parkway|promenade|outlook|crest|bend)$", re.IGNORECASE)
_COMPASS_WORD = re.compile(r"^(?:north|south|east|west|central)$", re.IGNORECASE)


def _address_state(r, geo):
    """(v), 24 Oct 2026: off a page, the state of a place of ONE state the row writes straight
    after a street word ("1524 Gilchrist Street, BRISBANE WEST", VIC page: QLD) -- when every
    place the row writes anywhere, line, label, estate or suburb, is a place of that state,
    the row states no other, and the place is not its estate's name or inside it, bar an
    estate that is the place and a compass word ("BRISBANE WEST", the section heading the
    estate column holds). Not the brief's first rule, which a bare place word in a line was
    enough for: Verv 19094 "... SS West 4327 Jubilee Wyndhamvale" is VIC, as its page says."""
    page = sr.normalise_state(r["source_state_hint"])
    if not page:
        return ""
    found = set()
    for text in (r["source_text"], r["lot_address"]):
        for part in re.split(r"[|/]", str(text or "")):
            toks = re.findall(r"[A-Za-z][A-Za-z'\-]*|\d+[A-Za-z]?|,", part)
            for j, tok in enumerate(toks[:-1]):
                if not _STREET_WORD.match(tok) or j == 0 or not toks[j - 1][:1].isalpha():
                    continue
                k = j + 2 if toks[j + 1] == "," else j + 1
                for size in (3, 2, 1):
                    span = toks[k:k + size]
                    if len(span) < size or not all(t[:1].isalpha() for t in span):
                        continue
                    states = geo.states_for_suburb(" ".join(span))
                    if len(states) == 1:
                        found.add((" ".join(span).lower(), states[0]))
                        break
    states = {st for _n, st in found}
    if len(states) != 1 or page in states:
        return ""
    other = next(iter(states))
    estate = [w.lower() for w in _W.findall(str(r["estate_name"] or ""))]
    for name, _st in found:
        words = name.split()
        inside = any(estate[i:i + len(words)] == words for i in range(len(estate)))
        heading = (estate[:len(words)] == words and len(estate) > len(words)
                   and all(_COMPASS_WORD.match(w) for w in estate[len(words):]))
        if inside and not heading:
            return ""
    if _states_stated(r, geo) - {other}:
        return ""
    for text in (r["source_text"], r["lot_address"], r["estate_name"], r["suburb"]):
        for part in re.split(r"[,|/()]", str(text or "")):
            words = [w.rstrip("-'") for w in _W.findall(part)]
            for size in (1, 2, 3):
                for i in range(len(words) - size + 1):
                    span = words[i:i + size]
                    if any(len(w) < 2 for w in span):
                        continue
                    states = geo.states_for_suburb(" ".join(span))
                    if states and other not in states:
                        return ""
    return other


def _allowed(r, old_state, geo, truth=None):
    """The states a row may be given that origin/main does not give it: (i) its page's
    (_page) or its file name's, (ii) its stated postcode's, (iii) round 1's longer name --
    only where origin/main's answer is blank, (v) its address's over its page
    (_address_state) -- and, for (iii) and (v), on a generated row whose true state is known,
    only that one."""
    page = _page(r)
    file_state = origin.state_from_filename(r["source_url"] or "")
    ok = {old_state or "", "", page, file_state, _stated_postcode(r, geo)}
    if not (r["suburb"] or "").strip() and not page and not old_state and not file_state:
        guess = origin.old_guess(geo, r["lot_address"] or "", (r["state"] or "").strip().upper())
        longer = _longer_name(r, guess, geo)
        if longer and (not truth or longer == truth):
            ok.add(longer)
    address = _address_state(r, geo)
    if address and (not truth or address == truth):
        ok.add(address)
    return ok


# --------------------------------------------------------------------------- the runs

def _rows(geo, n, seed, stock=False):
    rows = []
    records = corpus.generate(geo, n, seed=seed)
    for k, rec in enumerate(records):
        row = {c: rec.get(c) for c in _COLS}
        row.update(id=k + 1, source_channel="Generated", content_hash=f"v2:gen{seed}-{k}",
                   stocklist_file=f"generated:{seed}:{k}", source_url=f"generated:{seed}:{k}",
                   superseded_by=None, price=500000.0, last_seen="29/09/2026",
                   _truth=rec["truth"])
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
        return {i: (s, src, pc) for i, s, src, pc in
                conn.execute("SELECT id, state, state_source, postcode FROM buildings")}
    finally:
        conn.close()


def _violations(rows, got, geo):
    """Rows given a state outside (i)-(iii) of origin/main's answer on the same columns --
    or (iv, round 3, 29 Sep 2026) of origin/main's answer on the postcode column as
    tonight's run leaves it: a lot number enrich takes out of the column is out of it for
    origin/main the next night, so its answer then is one the night's own write reaches
    (a row whose line opens with its lot, "9671 Second Avenue Baldivis ...", postcode 9671)."""
    before = origin.old_enrich(rows, geo)
    written = {r["id"]: got[r["id"]][2] for r in rows if len(got[r["id"]]) > 2}
    moved_pc = {i for i, pc in written.items()
                if (pc or None) != (next(r for r in rows if r["id"] == i)["postcode"] or None)}
    after_pc = (origin.old_enrich([dict(r, postcode=written[r["id"]]) if r["id"] in moved_pc
                                   else r for r in rows], geo) if moved_pc else before)
    out, changed = [], 0
    for r in rows:
        new, old = got[r["id"]][0] or "", before[r["id"]][0] or ""
        changed += new != old
        if new != old and new == (after_pc[r["id"]][0] or "") and r["id"] in moved_pc:
            continue
        if new != old and new not in _allowed(r, old, geo, r.get("_truth")):
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
                        lambda r, geo_, stocked, decide, origin_=None, reading_=None,
                        actual_=None:
                        sr.suburb_in_row_text(
                            r["lot_address"] or "", geo_, prefer=r["source_state_hint"] or "",
                            estate=r["estate_name"], stocked=stocked))
    monkeypatch.setattr(enrich_buildings, "bound", lambda *a: set(STATES) | {""})
    rows = _rows(geo, 2000, 29092026)
    bad, _changed = _violations(rows, _enrich(tmp_path, monkeypatch, rows), geo)
    assert len(bad) > 10


def _round_2s_longer_name(r, reading, origin_guess, geo_, stocked):
    """Round 2's exception, as both its reviews found it: any one-state longer name the
    LABEL writes around origin/main's guess, on a row with no page."""
    if sr.normalise_state(r["source_state_hint"]) or not sr.extends_the_guess(
            reading, origin_guess, r["lot_address"] or ""):
        return ""
    states = geo_.states_for_suburb(reading or "")
    return states[0] if len(states) == 1 else ""


def test_the_harness_sees_round_2s_longer_name(tmp_path, monkeypatch, geo):
    """Round 3, 29 Sep 2026: the committed harness passed round 2's longer-name exception,
    because its (iii) was the same rule. Put that rule back -- in the reading and in the
    bound, over a state origin/main gives too -- and the harness must now see it."""
    monkeypatch.setattr(enrich_buildings, "longer_name_stands", _round_2s_longer_name)
    real_bound = enrich_buildings.bound

    def round_2s_bound(r, origin_state, longer, geo_):
        return real_bound(r, origin_state, "", geo_) | ({longer} if longer else set())

    monkeypatch.setattr(enrich_buildings, "bound", round_2s_bound)
    rows = _rows(geo, 2000, 29092026)
    bad, _changed = _violations(rows, _enrich(tmp_path, monkeypatch, rows), geo)
    assert len(bad) > 5, bad


def _address_rows(geo, n, seed):
    rows = []
    for k, rec in enumerate(corpus.generate_address_rows(geo, n, seed=seed)):
        row = {c: rec.get(c) for c in _COLS}
        row.update(id=k + 1, source_channel="Generated", content_hash=f"v2:addr{seed}-{k}",
                   stocklist_file=f"generated:addr:{seed}:{k}",
                   source_url=f"generated:addr:{seed}:{k}", superseded_by=None,
                   price=500000.0, last_seen="24/10/2026", _truth=rec["truth"])
        rows.append(row)
    return rows


def test_the_address_over_the_page_is_inside_the_bound(tmp_path, monkeypatch, geo):
    """24 Oct 2026, (v): APLACE's and Goldstate's shapes (corpus.generate_address_rows) off
    their own page, another state's or none. The address takes rows off their page, and only
    ever to their true state; nothing else leaves origin/main's answer."""
    rows = _address_rows(geo, 600, 24102026)
    got = _enrich(tmp_path, monkeypatch, rows)
    bad, _changed = _violations(rows, got, geo)
    assert bad == [], f"{len(bad)} rows in a state origin/main would not give: {bad[:10]}"
    moved = [r for r in rows if got[r["id"]][1] == sr.ADDRESS_OVER_PAGE_SOURCE]
    assert len(moved) > 10, len(moved)
    assert [(r["lot_address"], got[r["id"]][0], r["_truth"]) for r in moved
            if got[r["id"]][0] != r["_truth"]] == []


def test_the_harness_sees_the_briefs_first_rule(tmp_path, monkeypatch, geo):
    """A silent check reads as a pass: the rule first asked for -- a suburb of one other
    state, nothing in the row a place of the page's state, no street and no estate guard --
    put in the reading and the bound, and the harness must see Goldstate's estates moved."""
    from tests.test_aplace_sections import _the_briefs_rule
    monkeypatch.setattr(sr, "address_over_page", _the_briefs_rule)
    monkeypatch.setattr(enrich_buildings, "address_over_page", _the_briefs_rule)
    rows = _address_rows(geo, 600, 24102026)
    bad, _changed = _violations(rows, _enrich(tmp_path, monkeypatch, rows), geo)
    assert len(bad) > 10, bad


# ------------------------------------------------------------------- the misfile check

# A heading a sheet puts in the suburb column: a stocked place of the filed state that the
# row's own line need not write (7810's GLENVALE; the reviews' "Cameron Park" plants).
_HEADINGS = {"NSW": "Cameron Park", "VIC": "Tarneit", "QLD": "Glenvale", "SA": "Munno Para",
             "WA": "Baldivis", "TAS": "Kingston", "NT": "Zuccoli", "ACT": "Whitlam"}
_FACADE_CODE = re.compile(r"^(?:facade|sg|dg|sgcorner|sgcormer|dgcorner|fortitude|fortitdue|"
                          r"fortitue|carolina|caolina|lennox|padington)$", re.IGNORECASE)
_PLACE_FACADE = re.compile(r"^(?:paddington|yamba|newstead)$", re.IGNORECASE)


def _a_facaded_design(text, name, filed, geo):
    """The one case the misfile check may keep quiet on, stated independently: every time
    the text writes `name` a facade code follows it -- a garage code or a facade that is no
    place (not "Lennox Head"), or AVIA's place-named facade with its house area after it
    ("Paddington 180m2") -- and the text names no other place outside `filed`, nor states
    another state (_states_stated: "Soutbank, VIC, 3006 Illawarra SG" filed QLD). Round 3,
    29 Sep 2026: round 2 also let a name go when a place of the filed state came after it,
    which is Creation Homes' "<suburb> <design> <size>" as often as a design's facade."""
    toks = re.findall(r"[A-Za-z][A-Za-z'\-]*|\d+(?:\.\d+)?", str(text))
    toks = [t.rstrip("-'") for t in toks]
    low = [t.lower() for t in toks]
    want = [w.lower() for w in _W.findall(name)]
    covered, seen = set(), False
    for i in range(len(low) - len(want) + 1):
        if low[i:i + len(want)] != want:
            continue
        seen = True
        j = i + len(want)
        nxt = toks[j] if j < len(toks) else ""
        after = toks[j + 1:j + 4]
        if _FACADE_CODE.match(nxt):
            if after and geo.states_for_suburb(nxt + " " + after[0]):
                return False
        elif not (_PLACE_FACADE.match(nxt) and len(after) == 3 and after[0][:1].isdigit()
                  and after[1].lower() == "m" and after[2] == "2"):
            return False
        covered |= set(range(i, j + 1))
    if not seen:
        return False
    for size in (1, 2, 3):
        for i in range(len(toks) - size + 1):
            span = toks[i:i + size]
            if covered & set(range(i, i + size)) or any(t[0].isdigit() or len(t) < 2
                                                        for t in span):
                continue
            states = geo.states_for_suburb(" ".join(span))
            if states and filed not in states:
                return False
    return not _states_stated({"source_text": text, "lot_address": ""}, geo) - {filed}


def test_the_misfile_check_never_names_what_origin_main_would_not(geo):
    """Every generated row asked as if filed in each state, on its own column, a blank one,
    a heading of that state or a place-word of its line of that state, with its own page,
    the filed state's or none: the new answer is origin/main's or nothing, and nothing only
    on a design and its facade code on a row its page backs (the documented 7810 case,
    _a_facaded_design)."""
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
            columns = {rec["suburb"] or "", "", _HEADINGS[filed]} | {
                p for p, sts in places.items() if filed in sts}
            for column in columns:
                for page in {rec["source_state_hint"] or "", filed, ""}:
                    new = sr.text_locality_elsewhere(text, filed, geo, stocked,
                                                     estate=rec["estate_name"] or "",
                                                     suburb=column, page=page)
                    if new is not None and new != old:
                        named.append((text, filed, column, page, old, new))
                    elif old and new is None and not (
                            page == filed and column
                            and not sr._written_in(toks, breaks, column)
                            and _a_facaded_design(text, old[0], filed, geo)):
                        quiet.append((text, filed, column, page, old))
    assert named == [], named[:5]
    assert quiet == [], quiet[:5]


def test_the_replay_warns_wherever_origin_main_warns(geo):
    """The nightly replay's misfile WARN (search_regressions.locality_check) over the
    corpus's rows filed in every state, on their own column, a heading or none, with their
    page, the filed state's or none: a WARN wherever origin/main's check gives one (round
    3, 29 Sep 2026 -- round 2 kept quiet on 36 of 1,308 of a review's heading-column plants
    of real rows, 7889 "Walloon Banksia" among them). Wherever the row is a design and its
    facade code its page backs, the WARN says so ('facade')."""
    import search_regressions as SR

    rows, db, asks = [], [], []
    for k, rec in enumerate(corpus.generate(geo, 250, seed=31)):
        text = " ".join(str(rec[x] or "") for x in ("source_text", "lot_address"))
        for filed in STATES:
            for column in {rec["suburb"] or "", _HEADINGS[filed]}:
                for page in {rec["source_state_hint"] or "", filed}:
                    for signal in ("suburb", "suburb + e-agent page"):
                        h = f"v2:replay{len(asks):06d}"
                        rows.append({"content_hash": h, "state": filed, "state_source": signal,
                                     "suburb": column or None, "lot_address": text[:60],
                                     "estate_name": rec["estate_name"], "price": 500000.0,
                                     "superseded_by": None})
                        db.append({"id": len(asks) + 1, "content_hash": h, "state": filed,
                                   "state_source": signal, "suburb": column or None,
                                   "source_state_hint": page or None, "source_text": text})
                        asks.append((h, text, filed, rec["estate_name"]))
    # the catalogue stocks every place the corpus writes, in each of its states
    names = set()
    for text in {a[1] for a in asks}:
        toks, breaks = sr._tokens(text)
        names |= {" ".join(toks[i:i + s]) for (i, s) in sr._place_spans(toks, breaks, geo)}
    for name in sorted(names):
        for st in geo.states_for_suburb(name):
            rows.append({"content_hash": f"v2:stock-{name}-{st}", "state": st,
                         "state_source": "listing text", "suburb": name, "price": 1.0,
                         "superseded_by": None})
    catalogue = SR.Catalogue(rows, db)
    check = SR.locality_check(catalogue, SR.Report(io.StringIO()))
    stocked = {(str(r["suburb"]).lower(), r["state"]) for r in rows
               if r["suburb"] and not r["state_source"].startswith("suburb")}
    lost, warned = [], 0
    for h, text, filed, estate in asks:
        old = origin.sr.text_locality_elsewhere(text, filed, geo, stocked, estate=estate or "")
        hit = check(h)
        if old:
            warned += 1
            if not hit or tuple(hit[:2]) != tuple(old):
                lost.append((text, filed, old, hit))
    assert lost == [], lost[:5]
    assert warned > 500              # and the corpus is one origin/main warns on


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


_HATTAN_CLYDE = ("Available Clyde North Q3 2026 Townhouse Lot 3772 168 6 Embla D | Ceduna 9.66 "
                 "Single Storey 3 1 1 $301,500 $287,800 $589,300")
_HATTAN_TRUGANINA = ("Available Truganina Titled House 2538 350 12.5 Denver 17 Single Storey "
                     "4 2 2 $402,000 $365,350 $767,350")
_HATTAN_DONNYBROOK = ("Available Donnybrook Q1 2027 Townhouse 820 176 8 Lume J | Lot 820 | "
                      "Olivine 12.36 Single Storey 3 2 1 $205,502 $368,948 $574,450")
_WYNDHAM_VALE = ("Available Wyndham Vale Q3 2026 Townhouse 413 168 6 Westgarth E | Mambourin "
                 "Green 16.65 Double Storey 4 2 2 $185,000 $403,950 $588,950")
_SPRINGVALE_SOUTH = ("Available Springvale South Titled Townhouse 44 215 10.86 Verona SG Corner | "
                     "Roselle 15.72 Double Storey 3 2 1 $954,950")

_ROUND_2_PLANTS = [
    # (lot_address, source_text, postcode, lot_number, stored state, signal, estate, want)
    # verify-0 (round 2): a longer name in the label, of a state origin/main never gives
    ("Lot 340, Boat Harbour Beach", "Lot 158 Unit B511, 10-14 Hinkler Av, CARINGBAH, NSW, 2229",
     "2229", None, None, None, None, {"NSW"}),
    ("Lot 340, Boat Harbour Beach", "Lot 158 Unit B511, 10-14 Hinkler Av, CARINGBAH, NSW, 2229",
     "2229", None, "VIC", "e-agent page", "Live Caringbah", {"NSW"}),
    ("LOT 58 MONTANA CRES, DIGGERS REST 3427, Verona Sands", "LOT 58 MONTANA CRES, DIGGERS REST 3427",
     None, None, None, None, None, {None, "VIC"}),
    ("Lot 1364, Murray Bridge", _HATTAN_TRUGANINA, None, None, None, None, "Murray Bridge",
     {"VIC"}),
    ("Lot 1836, Upper Esk", _HATTAN_DONNYBROOK, None, None, None, None, "Olivine", {None, "VIC"}),
    ("86 Wellington Point, Clyde Kilburn North",
     "LOT 2402 BURGENSTEIN DRIVE, CHARLEMONT - 7 STAR ENERGY RATING", None, None, None, None,
     None, {None, "VIC"}),
    ("Lot 2952, Olivine | Boat Harbour Beach", _HATTAN_DONNYBROOK, None, None, None, None,
     "Olivine", {None, "VIC"}),
    (_HATTAN_TRUGANINA + " | Boat Harbour Beach", _HATTAN_TRUGANINA + " | Boat Harbour Beach",
     None, None, None, None, None, {None, "VIC"}),
    # verify-1 (round 2): the label's estate over the row's own line, postcode and state code
    ("Lot 3772, Redwood Park", _HATTAN_CLYDE, None, None, None, None, "Redwood Park", {"VIC"}),
    ("Lot 3772, Glen Eden", _HATTAN_CLYDE, None, None, None, None, "Glen Eden", {"VIC"}),
    ("Lot 44, Brooklyn Park", "Available Dora Creek Titled House Lot 44 448 14 Patterson 23 4 2 2",
     "2265", "44", None, None, "Brooklyn Park", {"NSW"}),
    ("Lot 44, Brooklyn Park", "Available Dora Creek Titled House Lot 44 448 14 Patterson 23 4 2 2",
     "2265", "44", "NSW", "suburb + postcode", "Brooklyn Park", {"NSW"}),
    ("Lot 5, Capri DG Corner Murray Bridge",
     "Dual Key QLD Cameron Corner Lot 5 Capri DG Corner 448 Murray Bridge Estate $402,000",
     "4244", "5", None, None, None, {"QLD"}),
    # a review's generated Verv row: the estate Moore Park Beach, the suburb written as one word
    ("2-Part Detached SS North 762 Moore Park Beach MunnoParaDowns 283 $1061,795 Orwell 190m2",
     None, None, None, None, None, None, {None, "SA"}),
    # round 1's longer-name rows, which must stay right
    (_WYNDHAM_VALE, _WYNDHAM_VALE, None, "413", None, None, "Mambourin Green", {"VIC"}),
    (_SPRINGVALE_SOUTH, _SPRINGVALE_SOUTH, None, None, None, None, "Roselle", {"VIC"}),
    (_HATTAN_CLYDE, _HATTAN_CLYDE, None, None, None, None, "Q3 Lot Single", {"VIC"}),
]


def test_round_2s_reviews_broken_inputs_land_where_they_should(tmp_path, monkeypatch, geo):
    """Round 3, 29 Sep 2026: every wrong move both reviews of round 2 reported, through the
    real enrich -- none may land where origin/main does not put it -- beside round 1's
    Hattan rows, which keep their state. Each is a row of its own file with no page, blank
    suburb column, and the catalogue stocking the places their lines name."""
    rows = []
    for k, (label, line, pc, lot, state, signal, estate, _want) in enumerate(_ROUND_2_PLANTS, 1):
        rows.append({c: None for c in _COLS} | dict(
            id=k, builder_name="Review Plant", source_channel="Generated", lot_address=label,
            source_text=line or label, postcode=pc, lot_number=lot, state=state,
            state_source=signal, estate_name=estate, stocklist_file=f"review2:{k}",
            source_url=f"review2:{k}", content_hash=f"v2:review2{k:03d}", price=500000.0,
            last_seen="29/09/2026"))
    k = len(rows)
    for name, st in (("Truganina", "VIC"), ("Clyde North", "VIC"), ("Donnybrook", "VIC"),
                     ("Dora Creek", "NSW"), ("Caringbah", "NSW"), ("Diggers Rest", "VIC"),
                     ("Charlemont", "VIC"), ("Mambourin", "VIC")):
        k += 1
        rows.append({c: None for c in _COLS} | dict(
            id=k, builder_name="Stock", source_channel="Generated", lot_address=f"Lot 1, {name}",
            source_text=f"Lot 1 {name} {st}", suburb=name, state=st, state_source="listing text",
            source_state_hint=st, stocklist_file=f"stock2:{k}", source_url=f"stock2:{k}",
            content_hash=f"v2:stock2{k:03d}", price=500000.0, last_seen="29/09/2026"))
    got = _enrich(tmp_path, monkeypatch, rows)
    wrong = [(label, got[k][0], want)
             for k, (label, *_x, want) in enumerate(_ROUND_2_PLANTS, 1) if got[k][0] not in want]
    assert wrong == []
    bad, _changed = _violations(rows, got, geo)
    assert bad == []


def test_round_2s_longer_name_would_have_moved_them(tmp_path, monkeypatch, geo):
    """The same rows with round 2's longer-name exception put back: the harness sees them --
    so the test above can fail."""
    monkeypatch.setattr(enrich_buildings, "longer_name_stands", _round_2s_longer_name)
    real_bound = enrich_buildings.bound
    monkeypatch.setattr(enrich_buildings, "bound",
                        lambda r, o, longer, g: real_bound(r, o, "", g) | ({longer} - {""}))
    rows = [{c: None for c in _COLS} | dict(
        id=k, builder_name="Review Plant", source_channel="Generated", lot_address=label,
        source_text=line or label, postcode=pc, lot_number=lot, state=state,
        state_source=signal, estate_name=estate, stocklist_file=f"review2:{k}",
        source_url=f"review2:{k}", content_hash=f"v2:review2{k:03d}", price=500000.0,
        last_seen="29/09/2026")
        for k, (label, line, pc, lot, state, signal, estate, _w) in enumerate(_ROUND_2_PLANTS, 1)]
    got = _enrich(tmp_path, monkeypatch, rows)
    bad, _changed = _violations(rows, got, geo)
    assert len(bad) >= 3, bad


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
