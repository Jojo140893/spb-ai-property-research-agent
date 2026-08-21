"""A spelling slip is not a missing suburb — but fuzzy matching is worse than the problem.

'Soutbank' is Southbank with a letter dropped, on 77 live listings. 'CRESSNOCK' is
Cessnock with one doubled. Both were refused outright, so the row had no location at all,
when the source plainly said where the property is and simply mistyped it.
"""
import suburb_quality as SQ


def resolve(suburb, state="VIC"):
    return SQ.resolve({"suburb": suburb, "state": state})


def test_a_single_dropped_letter_is_corrected():
    suburb, why = resolve("Soutbank", "VIC")
    assert (suburb, why) == ("Southbank", SQ.FROM_TYPO)
    assert SQ.is_located(why), "a corrected spelling is still the source's own statement"


def test_a_single_substituted_letter_is_corrected():
    assert resolve("Michleham", "VIC")[0] == "Mickleham"
    assert resolve("CRESSNOCK", "NSW")[0] == "Cessnock"


def test_a_short_value_is_never_corrected():
    """'Land' is one edit from 'Lang', a real Victorian locality, and 'Lane' from several
    more. Nothing under seven characters is worth the risk."""
    for short in ("Land", "Lot", "Duplex", "Villa", "House"):
        suburb, why = resolve(short, "VIC")
        assert why != SQ.FROM_TYPO, f"{short!r} -> {suburb!r}"


def test_two_edits_is_not_a_typo():
    assert resolve("Soutbnk", "VIC")[1] != SQ.FROM_TYPO


def test_the_correction_must_be_in_the_rows_own_state():
    """Southbank is Victorian. A Queensland row does not get it."""
    suburb, why = resolve("Soutbank", "QLD")
    assert why != SQ.FROM_TYPO, suburb


def test_a_value_that_is_already_a_locality_is_left_alone():
    assert resolve("Officer", "VIC") == ("Officer", SQ.STATED)


def test_junk_is_still_refused():
    for junk in ("Dual Key", "One Part Contracts", "COAST COUNCIL", "Agent Stock List"):
        suburb, why = resolve(junk, "NSW")
        assert why != SQ.FROM_TYPO, f"{junk!r} -> {suburb!r}"


def test_an_ambiguous_near_miss_is_refused_rather_than_guessed():
    """Two localities a single edit away is not a typo, it is a coin toss.

    Verified against the whole index rather than a fixture: whatever the data holds, no
    value may resolve to a correction when more than one candidate is that close."""
    index = SQ._index()
    if not index.loaded:
        return
    for name, *_rest in (index._by_state.get("VIC") or [])[:400]:
        if len(name) < SQ._TYPO_MIN_LENGTH:
            continue
        hits = {n for n, *_r in index._by_state["VIC"]
                if SQ._one_edit_apart(name.lower(), n.lower())}
        if len(hits) > 1:
            assert SQ._typo_correction(name, "VIC", index) == "", name


# ------------------------------------- the stateless door the WA trap walked back through
def test_two_localities_in_two_states_names_neither():
    """'Central Springs Caboolture' is a QUEENSLAND estate containing two nationally
    unambiguous names: Springs, which is in Western Australia and nowhere else, and
    Caboolture, which is in Queensland.

    The stateless inference took whichever it found first and labelled a Caboolture lot as
    being in WA — the exact trap that made a state mandatory everywhere else, walking back
    in through the one door left open. Candidates must now agree on one state."""
    for text in ("Central Springs Caboolture", "Caboolture Central Springs"):
        suburb, _why = SQ.resolve({"estate_name": text, "state": ""})
        assert suburb != "Springs", f"{text!r} -> {suburb!r} (WA trap reopened)"
        assert suburb == "", f"{text!r} -> {suburb!r}; two states means neither"


def test_an_unambiguous_name_on_its_own_is_still_offered():
    """The guard is against DISAGREEMENT, not against stateless rows as a class."""
    assert SQ.resolve({"estate_name": "Mambourin", "state": ""})[0] == "Mambourin"


def test_a_multi_state_builder_lends_no_state():
    """A builder selling in one state is evidence for a row of theirs that forgot to say
    so. A builder selling in several is evidence of nothing, and the 95% floor refuses
    them by construction — Creation Homes (QLD 138 / NSW 122), Hudson Homes, G
    Developments, Hattan Homes, Prosperity Residential and three others never qualify."""
    from api._candidates import _sibling_state_map

    rows = ([{"builder_name": "Split Co", "state": "QLD"}] * 6
            + [{"builder_name": "Split Co", "state": "NSW"}] * 4
            + [{"builder_name": "Single Co", "state": "VIC"}] * 20)
    got = _sibling_state_map(rows)
    assert ("builder_name", "single co") in got
    assert got[("builder_name", "single co")] == "VIC"
    assert ("builder_name", "split co") not in got, "a multi-state builder lent a state"
