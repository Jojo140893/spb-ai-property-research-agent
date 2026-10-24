"""The generated corpus of tests/test_state_invariant.py: stocklist rows built from real
suburbs, estate names, house designs, facades, streets, prices, lot numbers and page hints of
every state (29 Sep 2026, round 2).

generate(geo, n, seed) -> [record]; each record carries the columns enrich reads plus
`truth` (the true state, '' when the line names no locality), `words` (the place-like words
the line holds, by role) and `pc_real` (the postcode was written as the lot's own).

The layouts are the builders' own -- AVIA's "<lot> Stage <k> <m2> $<price> <Month> <Year>
<design> <facade> <m2>", Hattan's "Available Titled Townhouse ... <design> SG Corner |
<design> <size>", Verv's "2-Part Detached SS West <lot> <estate> <suburb>", Creation Homes'
"<no> <street> <suburb> <design> <size> - <facade>", Silkwood's "REGISTER NOW <suburb>
<estate> Estate", Eternal's "House & Land <STATE> <suburb> Lot <no> <street>" -- with the
words drawn at random, so a design or an estate is as often another state's place as a
place of the lot's own state. The vocabularies are tests/fixtures/state_corpus_vocab.json;
the gazetteer adds a random 4% of its multi-word names and 1% of the rest.
"""
import json
import os
import random

STATES = ("NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT")
PC_RANGE = {"NSW": (2000, 2599), "VIC": (3000, 3999), "QLD": (4000, 4999), "SA": (5000, 5999),
            "WA": (6000, 6999), "TAS": (7000, 7999), "NT": (800, 899), "ACT": (2600, 2618)}
STYLES = ("Traditional", "Classic", "Modern", "Coastal", "Contemporary", "Hamptons", "Facade",
          "Urban", "Tradtional", "Trad")
GARAGE = ("SG", "DG", "SG Corner", "DG Corner", "SGCorner")
PRODUCT = ("Single", "Double", "Double Storey", "Single Storey", "Custom")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
# Place names the data uses as designs, facades and estates (tonight's rows).
PLACE_DESIGNS = ("Rhodes", "Banksia", "Verona", "Magnolia", "Ceduna", "Balwyn", "Doncaster",
                 "Vesper", "Chelsea", "Somerset", "Roseberry", "Moogerah", "Kuraby", "Eden",
                 "Madrid", "Tecoma", "Caulfield", "Yamba", "Sorrento", "Brighton", "Oxley",
                 "Mackay", "Hudson", "Gower", "Elwood", "Denver", "Rosewood", "Glenbrook",
                 "Coolamon", "Lucas", "Drake", "Hume", "Kennedy", "Jackson", "Eyre", "Duval",
                 "Vista", "Davenport", "Harwood", "Concord", "Carlton", "Balmoral", "London")
PLACE_FACADES = ("Paddington", "Yamba", "Newstead", "Lennox", "Fortitude", "Carolina",
                 "Padington", "Fortitue")
PLACE_ESTATES = ("Jubilee", "Riverhills", "The Grove", "Round Mountain", "Sienna Eden",
                 "Riverstone Springs", "Davis Vineyard", "Park Rise", "Windermere", "Oaklands",
                 "Harmony", "Aura", "Mandalay", "Lyra", "Evergreen", "Highfields", "Westbrook",
                 "Parklands", "Ironbark", "Bloomfield", "Highlands", "Silverwood", "Emerald",
                 "Pinnacle", "Hillcrest", "Kimberley", "One Mile", "Riverleigh", "Rangewood",
                 "Bonnie Brook", "Mambourin Green", "Northern Gateway", "Waterford",
                 "Springs", "Eden", "Vineyard", "Newhaven", "The Meadows", "Ashbourne")
TRICKY_SUBURBS = (("Clyde North", "VIC"), ("Clyde", "VIC"), ("Clyde", "NSW"),
                  ("Springvale South", "VIC"), ("Springvale", "VIC"), ("Wyndham Vale", "VIC"),
                  ("Box Hill North", "VIC"), ("Box Hill", "NSW"), ("Point Cook", "VIC"),
                  ("Redbank Plains", "QLD"), ("Riverstone", "NSW"), ("Fraser Rise", "VIC"),
                  ("Marsden Park", "NSW"), ("Brooklyn", "NSW"), ("Dora Creek", "NSW"),
                  ("Diggers Rest", "VIC"), ("Joyner", "QLD"), ("Burpengary", "QLD"),
                  ("Walloon", "QLD"), ("Camira", "QLD"), ("Tarneit", "VIC"), ("Wollert", "VIC"),
                  ("Mambourin", "VIC"), ("Glenroy", "VIC"), ("Werribee", "VIC"),
                  ("New Beith", "QLD"), ("Boondall", "QLD"), ("Rosewood", "QLD"),
                  ("Springfield Lakes", "QLD"), ("Armstrong Creek", "VIC"), ("Logan Reserve", "QLD"),
                  ("Ellenbrook", "WA"), ("Wadalba", "NSW"), ("Oran Park", "NSW"),
                  ("Gregory Hills", "NSW"), ("Craigieburn", "VIC"), ("Deanside", "VIC"),
                  ("Cranbourne East", "VIC"), ("Mount Duneed", "VIC"), ("Park Ridge", "QLD"),
                  ("White Rock", "QLD"), ("Ripley", "QLD"), ("Glenvale", "QLD"),
                  ("Yarrabilba", "QLD"), ("Warnervale", "NSW"), ("Leppington", "NSW"),
                  ("Baldivis", "WA"), ("Munno Para", "SA"), ("Angle Vale", "SA"),
                  ("Murray Bridge", "SA"), ("Smithfield Plains", "SA"), ("Brighton", "TAS"),
                  ("Kingston", "TAS"), ("Palmerston", "NT"), ("Zuccoli", "NT"),
                  ("Denman Prospect", "ACT"), ("Whitlam", "ACT"), ("Taylor", "ACT"))


def _places(geo, name):
    return [s for s in geo.states_for_suburb(name) if s in STATES]


def _pc(rng, state):
    lo, hi = PC_RANGE[state]
    n = rng.randint(lo, hi)
    return f"{n:04d}"


def _money(rng):
    return f"${rng.randint(250, 1400)},{rng.randint(0, 999):03d}"


class Gen:
    def __init__(self, geo, vocab, seed):
        self.geo, self.rng = geo, random.Random(seed)
        rng = self.rng
        cat = [(n, st) for n, st, _k in vocab["catalogue_suburbs"]
               if st in _places(geo, n)]
        gaz = []
        for (name, st) in list(geo._index.keys()):
            if st in STATES and " " in name and len(name) < 22 and rng.random() < 0.04:
                gaz.append((geo._canonical.get(name, name.title()), st))
        singles = []
        for (name, st) in list(geo._index.keys()):
            if st in STATES and " " not in name and len(name) >= 5 and rng.random() < 0.01:
                singles.append((geo._canonical.get(name, name.title()), st))
        self.suburbs = cat * 3 + list(TRICKY_SUBURBS) * 6 + gaz + singles
        self.designs = list(vocab["designs"]) + list(PLACE_DESIGNS) * 3
        self.facades = list(vocab["facades"]) + list(PLACE_FACADES) * 2 + list(STYLES)
        est = [e for e in vocab["estates"] if 3 <= len(e) <= 24 and len(e.split()) <= 3]
        self.estates = est + list(PLACE_ESTATES) * 4
        self.streets = [" ".join(s) for s in vocab["streets"]]
        other_places = []
        for (name, st) in list(geo._index.keys()):
            if st in STATES and 5 <= len(name) < 18 and rng.random() < 0.01:
                other_places.append(geo._canonical.get(name, name.title()))
        self.other_places = other_places
        # Round 3 (29 Sep 2026): multi-word places with a shorter place inside them
        # ("Redwood Park", "Boat Harbour Beach", "Murray Bridge"), as a label's estate or an
        # appended tail -- the reviews' road to a state origin/main never gives through the
        # longer-name rule. PO-box names ("Adelaide BC") left out.
        nested = []
        for (name, st) in list(geo._index.keys()):
            words = name.split()
            if (st not in STATES or len(words) < 2 or len(name) >= 24
                    or words[-1] in ("bc", "dc", "po", "mc", "msc")):
                continue
            subs = [" ".join(words[:k]) for k in range(1, len(words))]
            if any(_places(geo, x) for x in subs) and rng.random() < 0.3:
                nested.append(geo._canonical.get(name, name.title()))
        self.nested = nested

    def pick(self, seq):
        return self.rng.choice(seq)

    def suburb(self):
        return self.pick(self.suburbs)

    def estate(self):
        r = self.rng.random()
        if r < 0.25:
            return self.pick(self.other_places)
        return self.pick(self.estates)

    def design(self):
        return self.pick(self.designs)

    def facade(self):
        return self.pick(self.facades)

    def street(self):
        return self.pick(self.streets)

    # ---- templates: (label, line, estate_name, lot_number, words, truth_named)
    def build(self):
        rng = self.rng
        T, st = self.suburb()
        E, D, D2, F = self.estate(), self.design(), self.design(), self.facade()
        S = self.street()
        n = str(rng.choice([rng.randint(1, 99), rng.randint(100, 999), rng.randint(1000, 9999)]))
        size = str(rng.choice([rng.randint(12, 45), rng.randint(150, 320)]))
        area = str(rng.randint(250, 700))
        k = str(rng.randint(1, 60))
        mon = self.pick(MONTHS)
        yr = str(rng.randint(2025, 2028))
        style = self.pick(STYLES)
        garage = self.pick(GARAGE)
        prod = self.pick(PRODUCT)
        tid = rng.randint(1, 42)
        P = self.pick(self.nested)
        pcode = _pc(rng, st)
        est_name = None
        named = True                   # the line names the true suburb
        if tid == 1:
            label = f"Lot {n}, {T}"; line = label
        elif tid == 2:
            label = f"Lot {n}, {T} {E}"; line = label
        elif tid == 3:
            label = f"Lot {n}, {E} {T}"; line = label
        elif tid == 4:
            label = f"{n} {S}, {T}"; line = f"{n} {S} {T} {D} {size} - {F} Available 4 2 2 {area} {_money(rng)}"
        elif tid == 5:
            label = f"{n} {S}, {T} {D}"; line = f"{k} {n} {S} {T} {D} {size} - {F} Available 4 2 2 {area} {_money(rng)} Split"
        elif tid == 6:
            label = f"{n} {E}, {T}"; line = f"2-Part Detached SS West {n} {E} {T} {area} {_money(rng)} {D} 10.5 3 / 2 / 2 {_money(rng)}"
        elif tid == 7:
            label = f"Lot {n} {T} {D} {size}"; line = label
        elif tid == 8:
            est_name = E
            label = f"Lot {n}, {E}"; line = f"{n} {E} Estate {T} Sold {D} {size} {_money(rng)} Titled North 338.00 12.50"
        elif tid == 9:
            label = f"Lot {n} {T} {style}"; line = label
        elif tid == 10:
            label = f"{n} {S} {T}"; line = label
        elif tid == 11:            # AVIA: no locality on the line
            named = False
            label = f"{n} Stage {k} {area}m2 {_money(rng)} {mon} {yr} {D} {F} {size}m2 4 2 2 1 {_money(rng)} {_money(rng)} Volos Available Owner"
            line = label + " Occ & Investor 18.09.2026"
        elif tid == 12:            # Hattan townhouse: no locality
            named = False
            label = f"Available Titled Townhouse {k} 215 10.86 {D} {garage} | {D2} 15.72 3 2 1 {_money(rng)}"
            line = label
        elif tid == 13:
            label = f"Available {T} {prod} {k} 6 {D} F | {D2} 9.66"; line = label
        elif tid == 14:
            label = f"2-Part Detached SS West {n} {E} {T} {area} {_money(rng)} {D} 10.5 3 / 2 / 2 {_money(rng)} Brochure M"
            line = label + "asterplan Stage 43 - POS Available"
        elif tid == 15:
            label = f"{n} {S}, {T} {D}"; line = f"{n} {S} {T} {D} {size} - {style} Available 4 2 2 {area} $ 428,900"
        elif tid == 16:
            est_name = f"{T} {E}" if rng.random() < 0.5 else E
            label = f"REGISTER NOW {T} {E} Estate {mon} {yr} {_money(rng)} {_money(rng)} {D} {size} 4/2/1/2"
            line = label
        elif tid == 17:
            label = f"{n} {S}, {E}"; line = f"House & Land {st} {T} Lot {n} {S} {area} sqm 222 sqm {_money(rng)} Sold Registered"
        elif tid == 18:
            label = f"Lot {n} {E} Estate {T}"; line = label
        elif tid == 19:
            label = f"{T} Lot {n} {D} {style}"; line = label
        elif tid == 20:
            label = f"Lot {n}, {T} (Lot {k})"; line = label
        elif tid == 21:
            label = f"Lot {n} {T} {E} Estate"; line = label
        elif tid == 22:
            label = f"Lot {n}, {E}, {T}"; line = label
        elif tid == 23:
            label = f"Lot {n} {D} {garage} {T}"; line = label
        elif tid == 24:
            label = f"Lot {n}, {T} {D} {style}"; line = label
        elif tid == 25:
            label = f"{T} {D} {size}"; line = f"{n} {E} {T} {D} {size} - {F} {_money(rng)}"
        elif tid == 26:
            label = f"Lot {n} {E} {D} {F} {size}m2"; line = label; named = False
        elif tid == 27:
            label = f"{n} {S} {T} {E} {D} {size}"; line = label
        elif tid == 28:
            label = f"Lot {n}, {T} {prod}"; line = f"Available {T} {prod} Storey {size} {D} {F} | {D2} {size}"
        elif tid == 29:
            est_name = E
            label = f"Lot {n}, {T}"; line = f"{E} {D} {F} {size} {T} {_money(rng)}"
        elif tid == 30:
            label = f"Lot {n}, {D} {F}"; line = f"{T} Lot {n} {D} {F} {size} {_money(rng)}"
        elif tid == 31:
            label = f"Lot {n}, {T}, {st} {_pc(rng, st) if st != 'ACT' else '2611'}"; line = label
        elif tid == 32:
            label = f"{n} {S}, {T} {st}"; line = label
        elif tid == 33:
            label = f"Lot {n}, {E} {T} {D}"; line = label
        # Round 3 (29 Sep 2026): the reviews' shapes around the longer-name rule -- a
        # multi-word place P in the label only, over a line naming the real locality.
        elif tid == 35:            # Hattan email row, the label holding its estate
            est_name = P if rng.random() < 0.5 else None
            label = f"Lot {n}, {P}"
            line = f"Available {T} Q3 2026 Townhouse Lot {n} 168 6 {D} D | {D2} 9.66 Single Storey 3 1 1 {_money(rng)}"
        elif tid == 36:            # Proxima: the address with its state and postcode
            label = f"Lot {n}, {P}"
            line = f"Lot {n} Unit B{k}11, 10-14 {S}, {T.upper()}, {st}, {pcode}"
        elif tid == 37:            # Hermitage: the address, then an estate-like tail
            label = f"LOT {n} {S.upper()}, {T.upper()} {pcode}, {P}"
            line = f"LOT {n} {S.upper()}, {T.upper()} {pcode}"
        elif tid == 38:            # Hattan line == label, a place appended
            label = f"Available {T} Titled House Lot {n} 350 12.5 {D} 20 Single Storey 4 2 2 {_money(rng)} | {P}"
            line = label
        elif tid == 39:            # Verv: the estate, then the suburb written as one word
            label = f"2-Part Detached SS North {n} {P} {T.replace(' ', '')} 283 {_money(rng)} {D} 190m2 3 / 2 / 2"
            line = label
        elif tid == 40:            # Hattan: the line opens with the locality (round 1's rows)
            label = f"Available {T} Q3 2026 Townhouse {n} 168 6 {D} E | {E} 16.65 Double Storey 4 2 2 {_money(rng)}"
            line = label
        elif tid == 41:            # an estate label over a Creation Homes line
            est_name = P if rng.random() < 0.5 else None
            label = f"Lot {n}, {P}"
            line = f"{n} {S} {T} {D} {size} - {F} Available 4 2 2 {area} {_money(rng)}"
        elif tid == 42:            # a place appended to the label only
            label = f"Lot {n}, {E} | {P}"
            line = f"Available {T} Titled House Lot {n} 350 12.5 {D} 20 Single Storey 4 2 2 {_money(rng)}"
        else:
            label = f"Lot {n} {mon} {yr} {T} {D} {size}"; line = label
        words = {"T": T, "E": E, "D": D, "D2": D2, "F": F, "S": S, "P": P, "PC": pcode}
        return label, line, est_name, n, st, named, words, tid

    def record(self):
        rng = self.rng
        label, line, est_name, n, st, named, words, tid = self.build()
        truth = st
        # page hint: the lot's state, another state, or none
        r = rng.random()
        if tid >= 35:
            # the longer-name rule reads only rows with no page state
            r = 0.75 + r / 2.8 if r < 0.7 else (r - 0.7) * 2.5
        if r < 0.5:
            page = st
        elif r < 0.75:
            page = self.pick([s for s in STATES if s != st])
        else:
            page = None
        if not named:
            # a line with no locality: the page is the only claim, so the truth is the page's
            truth = page or ""
        # postcode
        pc, lot = None, None
        r = rng.random()
        pc_real = False
        if tid in (36, 37):
            pc_real, pc = True, words["PC"]
        elif r < 0.12 and st != "ACT" and named:
            pc_real = True
            pc = _pc(rng, st)
            label = label + f" {pc}" if pc not in label else label
            line = line if pc in line else line + f" {st} {pc}"
        elif r < 0.20:
            pc, lot = n.zfill(4) if len(n) == 4 else None, n
        elif r < 0.26 and len(n) == 4:
            pc = n                         # a lot number read as the postcode
        elif r < 0.30:
            lot = n
        # stored state / signal
        r = rng.random()
        col = None
        if r < 0.45:
            state, src = None, None
        elif r < 0.55:
            state, src = page, None
        elif r < 0.80:
            state, src = self._earlier(words, st), rng.choice(["suburb", "suburb + e-agent page",
                                                                "listing locality", "suburb"])
        else:
            state, src = None, None
        # the suburb column
        r = rng.random()
        if r < 0.6:
            col = None
        elif r < 0.75 and named:
            col = words["T"]
        else:
            w = self.pick(["E", "D", "F", "T"])
            col = words[w] if (w != "T" or named) else None
        if col and state and src in ("suburb", "suburb + e-agent page"):
            places = _places(self.geo, col)
            if places:
                state = state if state in places else self.pick(places)
        rec = dict(lot_address=label, source_text=line, suburb=col, estate_name=est_name,
                   source_state_hint=page, postcode=pc, lot_number=lot, state=state,
                   state_source=src, builder_name="Gen Builder", truth=truth,
                   tmpl=tid, words=words, named=named, pc_real=pc_real)
        return rec

    def _earlier(self, words, st):
        """A state an earlier night could have written: the truth, or a state some place-like
        word of the line names."""
        cands = [st]
        for k in ("E", "D", "F", "D2", "P"):
            cands += _places(self.geo, words[k])
        return self.pick(cands)


_VOCAB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                      "state_corpus_vocab.json")
COMPASS = ("WEST", "NORTH", "SOUTH", "EAST")


def _vocab(vocab):
    if vocab is None:
        with open(_VOCAB, encoding="ascii") as f:
            vocab = json.load(f)
        vocab["catalogue_suburbs"] = [[n_, st, 1] for n_, st in vocab["catalogue_suburbs"]]
    return vocab


def generate(geo, n, seed=29092026, vocab=None):
    g = Gen(geo, _vocab(vocab), seed)
    return [g.record() for _ in range(n)]


def generate_address_rows(geo, n, seed=24102026, vocab=None):
    """24 Oct 2026: the shapes around state_resolver.address_over_page, kept apart from
    generate() so its corpora stay as they are. Half APLACE's -- "<no> <street>, <PLACE>
    <COMPASS>" under that section heading for an estate, the line opening with a suburb the
    gazetteer may not know ("Dawn 1524 Gilchrist Street Dawn ...") -- and half Goldstate's
    "<no> <street>, <estate>" over a line that names no locality (the Mandalay estate in
    Beveridge, VIC; Mandalay is a QLD place), each off its own state's page, another
    state's or none; stored blank, or on its page alone as the live rows are."""
    g = Gen(geo, _vocab(vocab), seed)
    rng = g.rng
    out = []
    for _ in range(n):
        T, st = g.suburb()
        S, D, E = g.street(), g.design(), g.pick(PLACE_ESTATES + tuple(g.other_places))
        no = str(rng.randint(1, 5999))
        r = rng.random()
        page = st if r < 0.4 else (g.pick([s for s in STATES if s != st]) if r < 0.85
                                   else None)
        if rng.random() < 0.5:
            heading = f"{T.upper()} {g.pick(COMPASS)}"
            opener = g.pick(("Dawn", "Joyner", "Coral Cove", T, D))
            label = f"{no} {S}, {heading}"
            line = (f"{opener} {no} {S} {opener} Nov-26 392 Rectangular {_money(rng)} {D} A 3 "
                    f"200.63 4 | 2 | 2 {_money(rng)} {_money(rng)} EOI Received")
            estate, truth, named = heading, st, True
        else:
            label = f"{no} {S}, {E}"
            line = (f"Mar-27 {no} {S} North West 12.5 28 350 {_money(rng)} {D} 23 "
                    f"{g.facade()} 4 Single 210 {_money(rng)} {_money(rng)} Available")
            estate, truth, named = E, page or "", False
        stored = (page, "e-agent page (conflicting signals)") if page and rng.random() < 0.5 \
            else (None, None)
        out.append(dict(lot_address=label, source_text=line, suburb=None, estate_name=estate,
                        source_state_hint=page, postcode=None, lot_number=None,
                        state=stored[0], state_source=stored[1], builder_name="Gen Builder",
                        truth=truth, tmpl=43 if named else 44,
                        words={"T": T, "E": E, "D": D, "S": S}, named=named, pc_real=False))
    return out
