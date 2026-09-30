"""origin/main 31d13ef's enrich_buildings state decision, frozen -- the reference
tests/test_state_invariant.py holds tonight's enrich to (29 Sep 2026, round 2).

old_enrich(rows, geo) replays origin/main's enrich_buildings.main() over `rows` (dicts,
every column enrich reads) without writing anything, and returns {id: (state,
state_source)}: what origin/main's enrich leaves in each row. It uses origin/main's own
state_resolver (tests/_origin_main_state_resolver.py). Checked when it was written against
the real origin/main enrich_buildings.py on copies of the 29 Sep nightly, with 25,000
generated rows planted in them, stocked and unstocked: 0 rows of 48,448 and 0 of 49,675
differ.

old_guess(geo, text, state) is origin/main's geo.find_suburb_in_text, copied, so a later
change to geo.py cannot move the reference.
"""
import importlib.util
import os
import re
from urllib.parse import unquote

_spec = importlib.util.spec_from_file_location(
    "_origin_main_state_resolver",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "_origin_main_state_resolver.py"))
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)

STATE_RE = re.compile(r"\b(VIC|NSW|QLD|SA|WA|NT|ACT|TAS)\b", re.I)
HINT_ONLY_SIGNALS = ("e-agent page", "e-agent page (conflicting signals)",
                     "stocklist file name")
TEXT_LOCALITY_SOURCE = "row text locality (over suburb column)"

_STREET_TYPE = re.compile(
    r"(?:st|street|rd|road|dr|drive|ave|avenue|av|cres|crescent|cct|circuit|pde|parade|"
    r"way|wy|ct|court|cl|close|pl|place|blvd|boulevard|tce|terrace|gr|grove|gve|lane|ln|"
    r"esp|esplanade|hwy|highway|mews|walk|loop|link|vista|gdns|gardens)",
    re.IGNORECASE)


def old_guess(geo, text, state=""):
    """geo.find_suburb_in_text as origin/main has it."""
    if not text or not geo._index:
        return None
    words = re.findall(r"[A-Za-z][A-Za-z'\-]+", text)
    states = [state.strip().upper()] if state else []
    states += [s for s in geo._by_state.keys() if s not in states]
    fallback = None
    for st in states:
        for size in (3, 2, 1):
            for i in range(len(words) - size + 1):
                cand = " ".join(words[i:i + size])
                if (cand.lower(), st) not in geo._index:
                    continue
                after = words[i + size] if i + size < len(words) else ""
                if after and _STREET_TYPE.fullmatch(after):
                    fallback = fallback or cand.title()
                    continue
                return cand.title()
    return fallback


def _g(r, k):
    return r.get(k) if isinstance(r, dict) else (r[k] if k in r.keys() else None)


def cites_a_postcode(signal):
    return "postcode" in [p.strip() for p in str(signal or "").split("+")]


def rests_on_a_suburb_match(signal, suburb_from_column):
    signal = (signal or "").strip()
    return signal == "suburb" or (signal == "suburb + e-agent page" and not suburb_from_column)


def state_from_filename(url):
    if not url:
        return ""
    name = unquote(url).split("dn=")[-1] if "dn=" in url else unquote(url)
    m = STATE_RE.search(name)
    return m.group(1).upper() if m else ""


def file_of(r):
    for k in ("stocklist_file", "source_url"):
        if (_g(r, k) or "").strip():
            return (_g(r, k) or "").strip()
    return ""


def overrule_evidence(rows, geo):
    stocked, estates = set(), {}
    for r in rows:
        if _g(r, "superseded_by"):
            continue
        st = (_g(r, "state") or "").strip().upper()
        if not STATE_RE.fullmatch(st or ""):
            continue
        sub = (_g(r, "suburb") or "").strip()
        if sub and geo.locate(sub, st) \
                and not rests_on_a_suburb_match(_g(r, "state_source"), suburb_from_column=True):
            stocked.add((sub.lower(), st))
        estate = (_g(r, "estate_name") or "").strip().lower()
        if estate:
            key = ((_g(r, "builder_name") or "").strip().lower(), estate)
            estates.setdefault(key, {}).setdefault(st, set()).add(_g(r, "id"))
    return stocked, estates


def overruled_state(r, suburb, state, geo, evidence):
    stocked, estates = evidence
    text = " ".join(str(_g(r, k) or "") for k in ("source_text", "lot_address"))
    hit = sr.text_locality_elsewhere(text, state, geo, stocked, estate=_g(r, "estate_name"))
    if hit:
        return hit[1]
    estate = (_g(r, "estate_name") or "").strip().lower()
    builder = (_g(r, "builder_name") or "").strip().lower()
    if builder and estate and suburb and suburb.strip().lower() == estate:
        where = {st for st, ids in estates.get((builder, estate), {}).items()
                 if ids - {_g(r, "id")}}
        if len(where) == 1 and state not in where:
            return next(iter(where))
    return None


def old_enrich(rows, geo, evidence=None):
    """{id: (state, state_source)} as origin/main's enrich_buildings.main() leaves them."""
    if evidence is None:
        evidence = overrule_evidence(rows, geo)
    proven, per_file, from_column = {}, {}, {}
    for r in rows:
        rid = _g(r, "id")
        suburb = (_g(r, "suburb") or "").strip()
        from_column[rid] = bool(suburb)
        if not suburb:
            found = old_guess(geo, _g(r, "lot_address") or "",
                              (_g(r, "state") or "").strip().upper())
            if found:
                suburb = found
        state, why = sr.own_state(postcode=_g(r, "postcode"), suburb=suburb,
                                  estate=_g(r, "estate_name"), address=_g(r, "lot_address"),
                                  geo=geo, lot_number=_g(r, "lot_number"))
        proven[rid] = (suburb, state, why)
        per_file.setdefault(file_of(r), set()).add(state)
    national = {f for f, states in per_file.items() if sr.file_is_national(states)}

    def text_overrules(r, suburb, state, signal):
        if not (state and not _g(r, "superseded_by")
                and rests_on_a_suburb_match(signal, from_column[_g(r, "id")])):
            return None
        return overruled_state(r, suburb, state, geo, evidence)

    out = {}
    for r in rows:
        rid = _g(r, "id")
        suburb, mine, why = proven[rid]
        state = (_g(r, "state") or "").strip().upper()
        signal0 = _g(r, "state_source")
        settled = bool(state) and bool(STATE_RE.fullmatch(state or "")) \
            and (signal0 or "") not in HINT_ONLY_SIGNALS
        reopened = settled and cites_a_postcode(signal0) \
            and sr.postcode_is_lot_number(_g(r, "postcode"), _g(r, "lot_number"))
        if reopened:
            settled = False
        result = (state or None, signal0)
        if settled:
            hit = text_overrules(r, suburb, state, signal0)
            if hit:
                result = (hit, TEXT_LOCALITY_SOURCE)
        if not settled:
            page = _g(r, "source_state_hint") or ""
            if file_of(r) in national:
                new_state, signal = mine, why
            else:
                new_state, signal = sr.resolve_state(postcode=_g(r, "postcode"), suburb=suburb,
                                                     page_state=page, geo=geo,
                                                     listing_state=mine,
                                                     lot_number=_g(r, "lot_number"))
                hit = text_overrules(r, suburb, new_state, signal)
                if hit:
                    new_state, signal = hit, TEXT_LOCALITY_SOURCE
                if not new_state:
                    from_file = state_from_filename(_g(r, "source_url") or "")
                    if from_file:
                        new_state, signal = from_file, "stocklist file name"
            if new_state != state or (signal or None) != signal0:
                result = (new_state or None, signal or None)
        out[rid] = result
    return out
