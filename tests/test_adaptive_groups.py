"""Every group of listing cards on a page, not just the biggest one.

FIND_CARDS_JS chose the single largest repeated group and discarded the rest, so a page
split into a featured strip plus a main grid, or by estate, or by availability status,
kept only whichever happened to be biggest. Measured on three Chromium fixtures each
publishing 6 priced cards: 4 returned, every time. Every caller logs only the surviving
count, so the other two were invisible.

This runs the real JS in real Chromium, because the bug is entirely in the DOM walk — a
Python reimplementation would be testing the reimplementation.
"""
import pytest

from sources.adaptive_extract import FIND_CARDS_JS

sync_playwright = pytest.importorskip(
    "playwright.sync_api", reason="playwright is not installed").sync_playwright


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        yield pg
        browser.close()


def cards(page, body):
    page.set_content("<html><body>" + body + "</body></html>")
    out = page.evaluate(FIND_CARDS_JS)
    assert isinstance(out, dict), "the JS must return a denominator alongside the cards"
    return out


def _card(lot, price, beds="4 bed"):
    return f"<div class=card><h3>{lot}</h3><p>{beds}</p><p>{price}</p></div>"


GRID = "<div id=g>" + "".join(
    _card(f"Lot {i} Elm", f"$6{i}5,000") for i in range(1, 5)) + "</div>"


# ------------------------------------------------------------------ the union

def test_two_sections_both_survive(page):
    """MEASURED BEFORE THE FIX: 4 of 6. A portal splitting Available from Under Offer
    lost a whole section."""
    body = ("<h2>Available</h2><div id=a>"
            + "".join(_card(f"Lot 1{i} Rose", f"$70{i},000") for i in range(3)) + "</div>"
            "<h2>Under Offer</h2><div id=b>"
            + "".join(_card(f"Lot 2{i} Rose", f"$74{i},000") for i in range(3)) + "</div>")
    out = cards(page, body)
    assert len(out["cards"]) == 6
    text = " ".join(c["text"] for c in out["cards"])
    assert "Lot 10 Rose" in text and "Lot 20 Rose" in text


def test_a_single_grid_is_unchanged(page):
    out = cards(page, GRID)
    assert len(out["cards"]) == 4


def test_a_lone_featured_hero_is_still_ignored(page):
    """What taking the max was really protecting against: a one-off "from $X" banner.
    It sits below the group floor, so the union does not pick it up."""
    body = ("<div id=f><div class=hero><h3>Lot 9 Featured</h3>"
            "<p>from $599,000</p></div></div>" + GRID)
    out = cards(page, body)
    assert len(out["cards"]) == 4
    assert "Featured" not in " ".join(c["text"] for c in out["cards"])


# ------------------------------------------------------------------ no nesting

def test_a_lot_is_never_returned_twice(page):
    """The union must not take the cards AND the bare <p>$705,000</p> inside them, nor
    the containers around them — that would store each lot three times, once as a
    priceless fragment and once as a run-on of its whole section."""
    out = cards(page, GRID)
    texts = [c["text"] for c in out["cards"]]
    assert len(texts) == len(set(texts)), "no duplicates"
    for t in texts:
        assert t.count("$") == 1, f"a card holding several prices is a container: {t!r}"
        assert "Lot" in t, f"a card with no identity is a price fragment: {t!r}"


def test_a_price_only_element_is_not_a_card(page):
    body = "<div id=g>" + "".join(
        f"<div class=card><h3>Lot {i} Elm</h3><p>$6{i}5,000</p></div>"
        for i in range(1, 5)) + "</div>"
    out = cards(page, body)
    assert all("Lot" in c["text"] for c in out["cards"])


# ------------------------------------------------------------------ the denominator

def test_the_page_reports_what_it_saw(page):
    out = cards(page, GRID)
    assert out["found"] == len(out["cards"]) == 4
    assert out["fallback"] is False
    assert out["oversize"] == 0


def test_an_oversize_row_is_counted_not_just_skipped(page):
    """A wide table whose cells share a signature puts every real row over the 12-child
    line and returns nothing — measured: 0 of 6 rows on a 13-column table. Silently,
    that reads as "this page has no stock"."""
    cells = "".join(f"<td>c{i}</td>" for i in range(12))
    body = ("<table><tbody>" + "".join(
        f"<tr>{cells}<td>$70{i},000</td></tr>" for i in range(3)) + "</tbody></table>")
    out = cards(page, body)
    assert out["oversize"] >= 1, "the exclusion must be visible to the caller"


def test_the_fallback_says_it_fell_back(page):
    """No repeated structure at all: one priced block, no twins."""
    out = cards(page, "<div><h3>Lot 1 Elm</h3><p>$605,000</p></div>")
    assert out["fallback"] is True


def test_an_empty_page_is_not_an_error(page):
    out = cards(page, "<p>No stock currently available.</p>")
    assert out["cards"] == [] and out["found"] == 0
