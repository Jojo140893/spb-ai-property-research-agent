"""A suburb the source stated, held back only for want of a state.

318 live rows carried a locality the source plainly wrote and no state column to check it
against, so they published as "stated by the source; no state recorded" — searchable, but
refused for geocoding, distance and benchmarking.

That refusal is right in the abstract: 'Lucknow' is a real place in Victoria and another
in New South Wales, and a fifth of Australian locality names exist in more than one state.

It is not right when the row's OWN price list answers the question. Where the other rows
on that document agree on a state, that is evidence about this row — the same evidence
api/_candidates already trusts at request time, which is why these rows resolved for a
search and were still published unlocated.
"""
import sys

sys.path.insert(0, "api")

import _candidates
import suburb_quality as SQ


def _row(suburb, state="", doc="doc-a", i=0):
    return {"suburb": suburb, "state": state, "source_url": doc,
            "lot_address": "Lot %d" % i, "content_hash": "h%d" % i,
            "superseded_by": "", "suburb_source": ""}


def test_a_document_that_agrees_settles_the_state():
    """The premise and the mechanism, in one: the map is built from the siblings."""
    rows = [_row("Lucknow", "VIC", i=1), _row("Bairnsdale", "VIC", i=2),
            _row("Lucknow", "", i=3)]
    smap = _candidates._sibling_state_map(rows)
    assert smap.get(("source_url", "doc-a")) == "VIC"

    orphan = rows[-1]
    _before, why_before = SQ.resolve(dict(orphan))
    assert why_before == SQ.UNCHECKED_NO_STATE, "the premise: unlocatable on its own"

    suburb, why = SQ.resolve({**orphan, "state": "VIC", "suburb_source": ""})
    assert suburb == "Lucknow"
    assert SQ.is_located(why), "its own document settled it, so it is evidence"


def test_a_divided_document_settles_nothing():
    """_sibling_state_map's 95% floor. A price list spanning two states cannot vouch for
    a row that names neither."""
    rows = ([_row("Lucknow", "VIC", i=i) for i in range(5)]
            + [_row("Orange", "NSW", i=10 + i) for i in range(5)]
            + [_row("Lucknow", "", i=99)])
    smap = _candidates._sibling_state_map(rows)
    assert smap.get(("source_url", "doc-a")) is None


def test_the_suburb_itself_is_never_invented():
    """Only the STATE is borrowed. A row with no suburb of its own gains nothing here —
    that is a different rule with a different grade."""
    rows = [_row("Lucknow", "VIC", i=1), _row("", "", i=2)]
    smap = _candidates._sibling_state_map(rows)
    suburb, why = SQ.resolve({**rows[-1], "state": smap.get(("source_url", "doc-a")) or "",
                              "suburb_source": ""})
    assert suburb == ""
    assert not SQ.is_located(why)


def test_the_build_applies_it():
    """The wiring. Without this the rows resolve for a search and still publish
    unlocated, which is the exact state this fixed."""
    import inspect
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("build_web.py").read_text(
        encoding="utf-8")
    assert "UNCHECKED_NO_STATE" in source, "build_web must handle the stated-no-state case"
    assert "_state_by_document" in source
    # BUILT ONCE, OUTSIDE THE ROW LOOP. An estate-containment lookup written inside one
    # took ten minutes a request before it was hoisted, so this is worth pinning.
    # Checked by indentation rather than position: build_web has more than one
    # `for b in buildings:` and the first is not the one this sits in.
    built = [l for l in source.splitlines() if l.strip().startswith("_state_by_document = ")]
    assert built, "the map is never assigned"
    assert all(len(l) - len(l.lstrip()) <= 8 for l in built), (
        "the map is being rebuilt inside a loop; build it once")
    assert source.index("_state_by_document = ") < source.index("_state_by_document.get(")
