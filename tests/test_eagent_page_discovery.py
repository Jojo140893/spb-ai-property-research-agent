"""The E-Agent category pages the configured list does not know about.

THE SHAPE THIS GUARDS. EAGENT_CONFIG.category_pages was "read off the live navigation on
2026-07-29" and walked as though it were the site ever since: four VIC pages, three QLD,
two NSW, one SA, two commercial. No NSW land page, no NSW townhouse page, no QLD land
page, nothing for SA beyond house and land — and nothing in the harvest ever asked whether
those exist.

It is the same failure that hid 78 of Proxima's 118 projects: a snapshot of a listing,
walked as though it were the listing. From the database it looks like a builder with no
stock, which is exactly what Gdev (QLD/NSW/SA) and Choice Homes (QLD) look like — both
flagged as being on E-Agent, neither ever producing a single row, live or superseded.

So the configured list is a SEED and the site's own navigation is read at harvest time.
These tests cover what counts as a category page, because visiting the wrong things costs
a page load each and attributing them costs correctness.
"""
import pytest

from sources.e_agent import EAgentSource
from sources.portal_config import EAGENT_CONFIG


SRC = EAgentSource()
CONFIGURED = {c.url.rstrip("/").lower() for c in EAGENT_CONFIG.category_pages}


class _Scraper:
    def __init__(self, links):
        outer = self

        class _Page:
            def evaluate(self, js):
                return outer._links

        self._links = links
        self.page = _Page()


def _discover(links):
    return SRC._discover_category_pages(_Scraper(links))


def L(href, label):
    return {"href": href, "label": label}


# ------------------------------------------------------------------ what counts

def test_a_page_the_site_links_and_we_do_not_walk_is_found():
    out = _discover([L("https://www.e-agent.com.au/land-nsw", "Land Estates NSW")])
    assert len(out) == 1
    assert out[0].url == "https://www.e-agent.com.au/land-nsw"
    assert out[0].state == "NSW"
    assert out[0].product_type == "Land Estate"


def test_a_page_already_configured_is_not_returned_twice():
    known = EAGENT_CONFIG.category_pages[0].url
    assert _discover([L(known, "House & Land VIC")]) == []


def test_the_same_page_linked_twice_is_returned_once():
    out = _discover([L("https://www.e-agent.com.au/land-nsw", "Land NSW"),
                     L("https://www.e-agent.com.au/land-nsw/", "Land Estates NSW")])
    assert len(out) == 1


@pytest.mark.parametrize("label", [
    "House and Land NSW", "House & Land", "Land Estates VIC", "Apartments QLD",
    "Townhouses", "Commercial Properties", "Duplex NSW", "Terraces", "Villas",
])
def test_labels_that_name_a_product(label):
    out = _discover([L("https://www.e-agent.com.au/some-page", label)])
    assert len(out) == 1, f"{label!r} should look like a category page"


@pytest.mark.parametrize("label", [
    "Contact Us", "About", "Our Team", "Log In", "Blog", "FAQ", "Privacy Policy", "",
])
def test_labels_that_do_not_name_a_product_are_left_alone(label):
    assert _discover([L("https://www.e-agent.com.au/whatever", label)]) == []


@pytest.mark.parametrize("href", [
    "https://www.e-agent.com.au/account",
    "https://www.e-agent.com.au/login",
    "https://www.e-agent.com.au/blog",
    "https://www.e-agent.com.au/contact",
    "https://www.e-agent.com.au/access-projects",
    "https://www.e-agent.com.au/stock/list.xlsx",
    "https://www.e-agent.com.au/files/plan.pdf",
])
def test_utility_and_file_routes_are_skipped(href):
    """/access-projects is already read separately as the pooled set; visiting a
    utility route costs a page load and yields nothing."""
    assert _discover([L(href, "House & Land NSW")]) == []


def test_another_domain_is_never_followed():
    assert _discover([L("https://www.example.com/house-and-land", "House & Land")]) == []


# ------------------------------------------------------------------ the state

def test_the_state_comes_from_the_label_not_the_slug():
    """E-Agent slugs lie: /copy-of-house-and-land-qld is the NEW SOUTH WALES page."""
    out = _discover([L("https://www.e-agent.com.au/copy-of-house-and-land-qld-2",
                       "House & Land NSW")])
    assert out[0].state == "NSW", "the label wins"


def test_a_spelled_out_state_is_recognised():
    out = _discover([L("https://www.e-agent.com.au/hl-sa", "House & Land South Australia")])
    assert out[0].state == "SA"


def test_a_label_with_no_state_leaves_it_blank_rather_than_guessing():
    out = _discover([L("https://www.e-agent.com.au/townhouses-2", "Townhouses")])
    assert out[0].state == ""


# ------------------------------------------------------------------ failure modes

def test_a_navigation_that_cannot_be_read_is_not_fatal():
    class _Boom:
        class page:
            @staticmethod
            def evaluate(js):
                raise RuntimeError("detached frame")
    assert SRC._discover_category_pages(_Boom()) == []


def test_no_links_means_no_extra_pages():
    assert _discover([]) == []
    assert SRC._discover_category_pages(_Scraper(None)) == []


def test_the_configured_list_is_still_the_seed():
    """Discovery adds; it must never replace. Losing the configured twelve would be a
    far bigger regression than missing a thirteenth."""
    assert len(EAGENT_CONFIG.category_pages) >= 12
    assert all(c.url.startswith("https://www.e-agent.com.au/")
               for c in EAGENT_CONFIG.category_pages)
