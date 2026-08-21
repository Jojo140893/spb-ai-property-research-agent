"""What a minimum cost you, said where it can be acted on.

TWICE NOW a property has been reported as missing from the app when it was in it. A
Liverpool apartment (present, no bedroom count) and a Donnybrook lot (present, no suburb).
Both were named in the coverage paragraph, and that paragraph is a wall of counts nobody
can act on.

Measured on the live snapshot of 8,908 listings: 70% state no house size, 46% no car
spaces, 32% no suburb, 30% no bedrooms. Only 36% are findable by a real suburb AND a
bedroom minimum — which is how Coleen searches. A house-size minimum alone removes seven
listings in ten, silently.

A listing dropped for stating 4 bedrooms against a required 5 is a correct decision. One
dropped because its source never stated a bedroom count is a DATA GAP. They were reported
identically.
"""
import json
import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
INDEX = APP / "index.html"
RESEARCH = APP / "api" / "research.py"


def test_the_counts_reach_the_browser_not_only_the_sentence():
    """coverage_sentence already named every gap — as prose. The UI cannot act on prose."""
    src = RESEARCH.read_text(encoding="utf-8")
    entry = src[src.index("coverage_entry = {"):]
    # to the blank line that ends the literal — slicing to the first "}" stops inside
    # `counts.get('missing_fields') or {}`
    entry = entry[:entry.index(chr(10) + chr(10))]
    for field in ("missing_fields", "no_suburb", "incomplete_facts"):
        assert field in entry, f"{field} is not on the wire"


def test_the_note_is_rendered_before_the_folded_rejection_log():
    """The rejection log is folded away by design — thirty red cards buried the results.
    A data gap must not be folded with it: it is the thing that makes a consultant say
    the app does not have a property it does have."""
    html = INDEX.read_text(encoding="utf-8")
    gap = html.index("gap-note")
    log = html.index("Filtered Rejection Log.")
    assert gap < log, "the gap note must render above the folded log"


def test_every_offered_minimum_maps_to_a_real_form_field():
    """The button clears a minimum and re-runs. A wrong id would silently do nothing."""
    html = INDEX.read_text(encoding="utf-8")
    block = html[html.index("const MIN_FIELD = {"):]
    block = block[:block.index("};") + 2]
    ids = set(re.findall(r"'([A-Za-z]+)'\s*[,}]", block))
    ids = {i for i in ids if i not in {"bedroom", "count", "size", "car", "space", "house"}}
    for field in ("bedrooms", "bathrooms", "carSpaces", "houseSize"):
        assert field in block, f"{field} is not offered"
        assert f'id="{field}"' in html, f"{field} is not a real form field"


def test_the_labels_match_what_the_pipeline_actually_reports():
    """REQUIRED_FACTS names the labels; a mismatch means the button never appears."""
    cand = (APP / "api" / "_candidates.py").read_text(encoding="utf-8")
    block = cand[cand.index("REQUIRED_FACTS = ("):]
    block = block[:block.index(")")]
    labels = re.findall(r'"([a-z ]+)"\s*\)', block)
    html = INDEX.read_text(encoding="utf-8")
    mapping = html[html.index("const MIN_FIELD = {"):]
    mapping = mapping[:mapping.index("};")]
    for label in labels:
        assert f"'{label}'" in mapping, (
            f"the pipeline reports {label!r} and the UI has no button for it")


def test_the_form_it_resubmits_exists():
    html = INDEX.read_text(encoding="utf-8")
    assert "getElementById('propertyForm')" in html
    assert 'id="propertyForm"' in html


def test_the_note_never_prints_a_guess():
    """The whole point is that these rows are NOT guessed into the shortlist."""
    html = INDEX.read_text(encoding="utf-8")
    note = html[html.index("gap-note"):]
    note = note[:note.index("Filtered Rejection Log.")]
    assert "set aside rather than guessed at" in note
