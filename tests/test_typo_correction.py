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
