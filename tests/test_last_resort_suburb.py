"""The floor: a suburb taken from a row's siblings when nothing else names one.

Asked for on the standing instruction to leave no listing unfindable. Everything else in
the pipeline needs the row, its document or its estate to actually name a place; this is
what happens when none of them does.

It is deliberately the weakest thing published, and these tests exist to hold it there.
"""
import sys

sys.path.insert(0, "api")

import suburb_quality
from api._candidates import last_resort_map, last_resort_suburb


def test_it_is_inferred_and_never_located():
    """The whole safety of the rule. Holdout puts it at 50% on a builder and 37% on a
    document, so roughly one row in two carries the wrong town: it must never be
    geocoded, distance-filtered or benchmarked, and must never reach a comparable."""
    why = suburb_quality.INFERRED_FROM_SIBLINGS
    assert suburb_quality.is_searchable(why), "the point: the row becomes findable"
    assert suburb_quality.is_inferred(why)
    assert not suburb_quality.is_located(why), "and it is never treated as a position"


def test_the_grade_survives_the_snapshot_round_trip():
    """resolve() re-runs on every request. If it re-graded a carried inferred value as
    STATED, the badge would vanish and a guess would read as the source's own word --
    which is exactly the firewall bug that put 1,933 guessed suburbs into comparables."""
    row = {"suburb": "Officer", "state": "VIC",
           "suburb_source": suburb_quality.INFERRED_FROM_SIBLINGS}
    _sub, why = suburb_quality.resolve(row)
    assert why == suburb_quality.INFERRED_FROM_SIBLINGS
    assert not suburb_quality.is_located(why)


def test_the_builder_is_asked_before_the_document():
    """Ordered by measured accuracy, best first: builder 50%, document 37%."""
    rows = [{"builder_name": "B", "source_doc": "d1", "suburb": "Officer",
             "state": "VIC"}] * 3 + [
           {"builder_name": "C", "source_doc": "d1", "suburb": "Clyde",
            "state": "VIC"}] * 9
    table = last_resort_map(rows)
    orphan = {"builder_name": "B", "source_doc": "d1"}
    assert last_resort_suburb(orphan, table) == "Officer", "its builder, not its document"


def test_a_row_whose_siblings_name_nothing_gets_nothing():
    """78 live rows are in this position -- Solara, Lakeview, Knew Street -- where not
    one listing by that builder or on that document is located. There is nothing to
    borrow, and inventing one from the project's trading name is how "Hudson Homes"
    became a town 1,500 km away."""
    table = last_resort_map([{"builder_name": "B", "suburb": "Nowhereville",
                              "state": "VIC"}])
    assert last_resort_suburb({"builder_name": "Solara"}, table) == ""
