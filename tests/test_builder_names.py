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

def test_bathla_variants_merge_because_the_client_said_so():
    """They share a word, and a development arm and a group CAN be different entities —
    so this was deliberately left unmerged until Colin confirmed on 5 Aug 2026 that it
    is one builder. The merge lives in CLIENT_CONFIRMED_ALIASES precisely so the record
    shows it was the client's call and not a similarity rule."""
    for name in ("Bathla Development", "Bathla Group", "Bathla", "BATHLA GROUP"):
        assert _c().canonical(name) == "Bathla Development", name


def test_a_similar_name_the_client_has_not_confirmed_is_still_left_alone():
    """The confirmed list must not become a licence to merge on a shared word."""
    for name in ("Bathla Constructions Trust", "Bathurst Homes"):
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
        ("Bathla merges — client confirmed", test_bathla_variants_merge_because_the_client_said_so),
        ("an unconfirmed similar name is left alone",
         test_a_similar_name_the_client_has_not_confirmed_is_still_left_alone),
        ("unknown domain kept, not invented", test_an_unknown_domain_is_kept_not_invented_into_a_name),
        ("unknown builder passes through", test_an_unknown_builder_passes_through_unchanged),
        ("a domain is never a canonical target", test_a_domain_is_never_learned_as_a_canonical_target),
        ("blank stays blank", test_blank_stays_blank),
        ("shared first word not confused", test_two_builders_sharing_a_first_word_are_not_confused),
        ("domain helpers", test_domain_detection),
        ("client's own domain never a builder", test_the_client_own_domain_is_never_used_as_a_builder),
        ("postcode/stray word is not a location", test_a_postcode_or_stray_word_is_not_a_location),
        ("a real locality passes", test_a_real_locality_passes_through),
        ("estate unglued from locality", test_an_estate_glued_to_a_locality_is_unglued),
        ("missing index does not empty results", test_a_missing_index_does_not_empty_every_shortlist),
        ("export merges spellings, not judgement calls",
         test_the_export_canonicaliser_merges_spellings_but_not_judgement_calls),
        ("a domain never becomes the canonical name",
         test_the_export_canonicaliser_never_makes_a_domain_the_canonical_name),
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


# --------------------------------------------- the locality gate on the shortlist

def test_a_postcode_or_stray_word_is_not_a_location():
    """What made the research tab look broken.

    A QLD search returned "Lot 507, 2026 in 2026, QLD" and "Lot 507, offer in offer,
    QLD" as four of its five recommendations. The filtering was fine — 77 rows scored
    — but the results were unusable, because a value being PRESENT in the suburb
    column is not the same as it being a place.
    """
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "api"))
    from _candidates import clean_locality
    for junk in ("2026", "offer", "IN TERNAL BALCONY TOTAL", "Logan City Council",
                 "BRISBANE WEST", "Ausbuild Stock List.", "EOI Form"):
        assert clean_locality(junk, "QLD") == "", junk


def test_a_real_locality_passes_through():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "api"))
    from _candidates import clean_locality
    for good in ("Ripley", "Morayfield", "Toowoomba"):
        assert clean_locality(good, "QLD").lower() == good.lower(), good


def test_an_estate_glued_to_a_locality_is_unglued():
    """"Waler Heights, Mango Hill" is 38 rows. The locality is the LAST part — a fact
    about how addresses are written, not a guess about which word looks like a suburb.
    Recovering it saves the row AND fixes what the client is shown."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "api"))
    from _candidates import clean_locality
    assert clean_locality("Stage 2, Ripley", "QLD") == "Ripley"
    assert clean_locality("Waler Heights, Mango Hill", "QLD") == "Mango Hill"
    assert clean_locality("Walloon (Owner Occupiers Only), Walloon", "QLD") == "Walloon"


def test_a_missing_index_does_not_empty_every_shortlist():
    """Degrading to "accept everything" keeps the pipeline exactly as it was; a
    missing data file must not be able to silently return no results."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "api"))
    import _candidates as C
    saved_geo, saved_cache = C._GEO, dict(C._LOCALITY_CACHE)
    try:
        C._GEO, C._LOCALITY_CACHE = False, {}
        assert C._is_real_locality("anything at all", "QLD") is True
    finally:
        C._GEO, C._LOCALITY_CACHE = saved_geo, saved_cache


def test_the_export_canonicaliser_merges_spellings_but_not_judgement_calls():
    """One builder, one name in the exported copy — without merging distinct entities.

    builder_names runs at WRITE time only, because builder_name feeds content_hash and
    rewriting stored rows changes their identity. So rows harvested before a spelling was
    known kept it, and the live dashboard still showed 89 listings whose builder was the
    bare domain "hattan.com.au". The export canonicalises for display instead.
    """
    import os, sys
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if here not in sys.path:
        sys.path.insert(0, here)
    from build_web import _display_name_canonicaliser

    stock = (["Avia Homes"] * 5 + ["AVIA Homes"] * 3 + ["hattan.com.au"] * 4
             + ["Hattan Homes"] * 6 + ["G Developments"] * 5 + ["G DEVELOPMENTS"] * 2
             + ["G-Developments"] + ["Strike Developments"] * 4 + ["Strike Development"]
             + ["Bathla Development"] * 5 + ["Bathla Group"] * 3 + ["Bathla"] * 2)
    canon = _display_name_canonicaliser(stock)
    out = {n: canon.canonical(n) for n in set(stock)}

    # case, punctuation, a bare domain and a trailing plural are spelling, so they merge
    assert out["AVIA Homes"] == out["Avia Homes"]
    assert out["hattan.com.au"] == out["Hattan Homes"] == "Hattan Homes"
    assert out["G DEVELOPMENTS"] == out["G-Developments"] == out["G Developments"]
    assert out["Strike Developments"] == out["Strike Development"], (
        "singular/plural is the module's own documented example and must merge")
    # a development arm and a group may be different companies; that is the client's call
    # Colin confirmed on 5 Aug 2026 that these are one supplier, so they now merge —
    # via CLIENT_CONFIRMED_ALIASES, which records that the call was the client's.
    assert len({out["Bathla"], out["Bathla Development"], out["Bathla Group"]}) == 1, (
        "Bathla variants are a client-confirmed merge — see CLIENT_CONFIRMED_ALIASES")
    # nothing is invented: every output is a spelling that appeared in the input
    assert set(out.values()) <= set(stock)


def test_the_export_canonicaliser_never_makes_a_domain_the_canonical_name():
    import os, sys
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if here not in sys.path:
        sys.path.insert(0, here)
    from build_web import _display_name_canonicaliser
    # the domain is far more common than the real name, and still must not win
    canon = _display_name_canonicaliser(["hattan.com.au"] * 50 + ["Hattan Homes"])
    assert canon.canonical("hattan.com.au") == "Hattan Homes"
