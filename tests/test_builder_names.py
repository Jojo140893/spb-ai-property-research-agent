"""
Tests for builder_names.py — one builder, one name.

`hattan.com.au` (105 listings) and `Hattan Homes` (64) were stored as two builders.
That splits a builder's stock in the dashboard filter, inflates the builder count,
and defeats cross-builder price comparison: 12 of 13 "two builders, same design"
candidates were one builder spelled two ways.

Half these tests are about what must NOT be merged. Over-merging suppliers is a
judgement about the client's business, and getting it wrong silently attributes one
builder's stock to another.
"""

from builder_names import (BuilderNameCanonicaliser, domain_label,
                           looks_like_domain)

KNOWN = ["Hattan Homes", "Bathla Development", "Bathla Group", "Bathla",
         "Strike Developments", "Torsion Homes", "Creation Homes NSW Pty Ltd"]


def _c():
    return BuilderNameCanonicaliser(KNOWN)


# --------------------------------------------------------------- what merges

def test_a_domain_resolves_to_the_builder_it_belongs_to():
    assert _c().canonical("hattan.com.au") == "Hattan Homes"
    assert _c().canonical("www.hattan.com.au") == "Hattan Homes"
    assert _c().canonical("HATTAN.COM.AU") == "Hattan Homes"


def test_case_and_punctuation_do_not_make_a_second_builder():
    assert _c().canonical("HATTAN HOMES") == "Hattan Homes"
    assert _c().canonical("hattan  homes") == "Hattan Homes"


def test_a_trailing_plural_is_the_same_builder():
    assert _c().canonical("Strike Development") == "Strike Developments"


# ----------------------------------------------------------- what must NOT

def test_bathla_variants_are_left_alone():
    """They share a word. A development arm and a group can be different entities,
    and deciding they are one is the client's call, not the parser's."""
    for name in ("Bathla Development", "Bathla Group", "Bathla"):
        assert _c().canonical(name) == name, name


def test_an_unknown_domain_is_kept_not_invented_into_a_name():
    """Better a domain in the column than a builder that does not exist."""
    assert _c().canonical("someunknownbuilder.com.au") == "someunknownbuilder.com.au"


def test_an_unknown_builder_passes_through_unchanged():
    assert _c().canonical("Brand New Builder") == "Brand New Builder"


def test_a_domain_is_never_learned_as_a_canonical_target():
    """Otherwise the first domain seen becomes the name everything else maps to."""
    c = BuilderNameCanonicaliser(["hattan.com.au"])
    assert c.canonical("Hattan Homes") == "Hattan Homes"


def test_blank_stays_blank():
    assert _c().canonical("") == ""
    assert _c().canonical(None) == ""


def test_two_builders_sharing_a_first_word_are_not_confused():
    c = BuilderNameCanonicaliser(["Creation Homes NSW Pty Ltd", "Creation Homes QLD"])
    # A domain can only resolve to one of them; it must pick deterministically and
    # must never rename one real builder into the other.
    assert c.canonical("Creation Homes QLD") == "Creation Homes QLD"
    assert c.canonical("Creation Homes NSW Pty Ltd") == "Creation Homes NSW Pty Ltd"


# ------------------------------------------------------------------- helpers

def test_domain_detection():
    assert looks_like_domain("hattan.com.au")
    assert not looks_like_domain("Hattan Homes")
    assert not looks_like_domain("")
    assert domain_label("hattan.com.au") == "hattan"
    assert domain_label("Hattan Homes") == ""


# ------------------------------------------- the email fallback Coleen asked for

def test_the_client_own_domain_is_never_used_as_a_builder():
    """Most of the digital@ inbox is FORWARDED mail, so the sender is the client's
    own domain. Filing supplier stock under "Smartpropertybuying" would be worse
    than the blank it replaces."""
    from sources.email_inbox import EmailStocklistSource
    for bad in ("smartpropertybuying.com.au", "mail.smartpropertybuying.com.au",
                "gmail.com", "outlook.com", "bigpond.com"):
        assert EmailStocklistSource._builder_from_sender_domain(
            EmailStocklistSource.__new__(EmailStocklistSource), bad) == "", bad


def run_all():
    tests = [
        ("domain resolves to its builder", test_a_domain_resolves_to_the_builder_it_belongs_to),
        ("case/punctuation not a new builder", test_case_and_punctuation_do_not_make_a_second_builder),
        ("trailing plural is the same builder", test_a_trailing_plural_is_the_same_builder),
        ("Bathla variants left alone", test_bathla_variants_are_left_alone),
        ("unknown domain kept, not invented", test_an_unknown_domain_is_kept_not_invented_into_a_name),
        ("unknown builder passes through", test_an_unknown_builder_passes_through_unchanged),
        ("a domain is never a canonical target", test_a_domain_is_never_learned_as_a_canonical_target),
        ("blank stays blank", test_blank_stays_blank),
        ("shared first word not confused", test_two_builders_sharing_a_first_word_are_not_confused),
        ("domain helpers", test_domain_detection),
        ("client's own domain never a builder", test_the_client_own_domain_is_never_used_as_a_builder),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] builder-names: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] builder-names: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
