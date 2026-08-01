"""
Tests for the Proxima harvest (sources/proxima.py).

Every fixture is a real value taken from the live portal on 2026-08-01. The parsing
here is deliberately thin — Proxima hands over typed data-* attributes rather than
prose — so these tests guard the places where a wrong answer would be silent:
a lot filed under the wrong project, a "0" read as a real measurement, or a
cross-listed lot counted twice.
"""

from sources.proxima import (ProximaSource, parse_property_name, _num, _int,
                             _lot_number)


# ------------------------------------------------------------------ address

def test_the_address_forms_proxima_actually_uses():
    cases = {
        "Lot 106 Unit 106, 9 Turffontein Avenue, BOX HILL, NSW, 2765":
            ("Box Hill", "NSW", "2765"),
        "Lot 1218 Pinnacle Unit 1218 Pinnacle, Invicta Drive, SMYTHES CREEK, VIC, 3351":
            ("Smythes Creek", "VIC", "3351"),
        "Lot 405 Dream Unit 405 Dream, Tait Street, SEBASTOPOL, VIC, 3356":
            ("Sebastopol", "VIC", "3356"),
    }
    for raw, (sub, st, pc) in cases.items():
        a = parse_property_name(raw)
        assert (a["suburb"], a["state"], a["postcode"]) == (sub, st, pc), (raw, a)


def test_a_missing_tail_is_left_blank_not_guessed():
    """Standing rule: a blank with a reason beats a plausible guess."""
    a = parse_property_name("Lot 12, Some Street")
    assert a["state"] == "" and a["postcode"] == "", a
    assert parse_property_name("")["suburb"] == ""
    assert parse_property_name(None)["state"] == ""


def test_a_suburb_is_never_mistaken_for_a_state():
    a = parse_property_name("Unit 5, 1 Smith Street, NORTHLAND, VIC, 3072")
    assert a["suburb"] == "Northland" and a["state"] == "VIC", a


# -------------------------------------------------------------------- values

def test_zero_means_not_recorded_not_zero():
    """Proxima writes "0"/"00"/"" for fields it does not hold.

    Storing 0 would look like a measurement — a 0 m frontage or a 0-bedroom house
    that nobody recorded. The scoring pipeline treats a real 0 very differently from
    a None, so this distinction has to survive into the row.
    """
    for blank in ("0", "00", "", "0.00", None):
        assert _num(blank) is None, blank
        assert _int(blank) is None, blank
    assert _num("318.00") == 318.0
    assert _num("10.200000") == 10.2
    assert _int("3") == 3


def test_price_strings_parse():
    assert _num("829990") == 829990.0
    assert _num("$1,234,567.00") == 1234567.0


def test_lot_number_loses_the_padding_but_not_the_lot():
    assert _lot_number("00000106/00000106") == "106"
    assert _lot_number("000000103") == "103"
    assert _lot_number("") == ""
    # A lot that is genuinely all zeros must not become "0"
    assert _lot_number("00000/00000") == ""


# --------------------------------------------------------------- row building

_HEADER = {
    "project": "124 Old Pitt Town Road Box Hill Land Only (10/27)",
    "status": "For Sale",
    "location": "NSW",
    "developer": "Bathla Development",
}

_LOT = {
    "name": "Lot 106 Unit 106, 9 Turffontein Avenue, BOX HILL, NSW, 2765",
    "lot": "00000106/00000106", "room": "00", "bathroom": "", "carspace": "",
    "propertywidth": "10.200000", "propertylength": "", "landsize": "318.00",
    "rop": "829990", "packageprice": "0",
    "_titled": "Land Registered", "_status": "For Sale",
}


def test_a_lot_maps_onto_the_schema():
    r = ProximaSource()._row(dict(_LOT), dict(_HEADER))
    assert r["state"] == "NSW"
    assert r["suburb"] == "Box Hill"
    assert r["postcode"] == "2765"
    assert r["lot_number"] == "106"
    assert r["advertised_package_price"] == 829990.0
    assert r["land_size_sqm"] == 318.0
    assert r["frontage_m"] == 10.2
    assert r["availability_status"] == "For Sale"
    assert r["title_status"] == "Land Registered"
    assert r["source_channel"] == "Proxima"
    # "00" bedrooms is not zero bedrooms
    assert r["bedrooms"] is None and r["bathrooms"] is None and r["car_spaces"] is None


def test_the_builder_is_read_from_the_header_never_invented():
    r = ProximaSource()._row(dict(_LOT), dict(_HEADER))
    assert r["builder_name"] == "Bathla Development"
    assert r["builder_source"] == "proxima project header"
    assert r["attribution_scope"] == "builder"

    blank = ProximaSource()._row(dict(_LOT), {**_HEADER, "developer": ""})
    assert blank["builder_name"] == "", "a nameless project must not acquire a builder"
    assert blank["attribution_scope"] == "project"
    assert blank["builder_source"] == ""


def test_the_estate_is_the_project_the_lot_actually_sits_in():
    """The bug that made the first run wrong.

    Every project's lots share one DOM container, so an unscoped read gave each lot
    whichever project came last — wrong estate AND wrong builder, on 10,774 rows.
    """
    r = ProximaSource()._row(dict(_LOT), dict(_HEADER))
    assert r["estate_name"] == _HEADER["project"]


def test_a_lot_with_no_price_is_not_a_listing():
    src = ProximaSource()
    assert src._row({**_LOT, "rop": "0", "packageprice": "0"}, _HEADER) is None
    assert src.stats.get("no price") == 1


def test_a_lot_with_no_address_is_refused():
    src = ProximaSource()
    assert src._row({**_LOT, "name": ""}, _HEADER) is None
    assert src.stats.get("no address") == 1


def test_the_state_falls_back_to_the_project_location():
    """Only where the project header actually states one."""
    lot = {**_LOT, "name": "Lot 9, Some Road, SOMEWHERE"}
    r = ProximaSource()._row(lot, {**_HEADER, "location": "BRADDON, ACT, 2612"})
    assert r["state"] == "ACT", r["state"]
    blank = ProximaSource()._row(lot, {**_HEADER, "location": ""})
    assert blank["state"] == "", "no stated state anywhere means no state"


# ------------------------------------------------------------- cross-listing

def test_a_cross_listed_lot_is_stored_once_and_counted():
    """Ascenta Living publishes the same lots under Coliving AND Traditional.

    One physical lot, two programmes. Both would collide on content_hash and the
    upsert would silently keep the last, so the collapse happens here where it can
    be counted instead of vanishing.
    """
    src = ProximaSource()
    a = src._row(dict(_LOT), {**_HEADER, "project": "Ascenta Living Coliving (9/11)"})
    b = src._row(dict(_LOT), {**_HEADER, "project": "Ascenta Living Traditional (56/86)"})
    out = src._collapse_cross_listed([a, b])
    assert len(out) == 1, out
    assert len(src.cross_listed) == 1
    assert out[0]["estate_name"] == "Ascenta Living Coliving (9/11)", "first wins, deterministically"


def test_genuinely_different_lots_both_survive():
    src = ProximaSource()
    a = src._row(dict(_LOT), dict(_HEADER))
    b = src._row({**_LOT, "lot": "00000107/00000107",
                  "name": "Lot 107 Unit 107, 11 Turffontein Avenue, BOX HILL, NSW, 2765"},
                 dict(_HEADER))
    assert len(src._collapse_cross_listed([a, b])) == 2


def test_the_channel_matches_the_portal_config():
    """The channel is part of identity AND is Colin's by-source filter entry."""
    from sources.portal_config import BUILDER_PORTAL_CONFIGS
    assert ProximaSource().channel_name == BUILDER_PORTAL_CONFIGS["proxima.com.au"].source_channel


def run_all():
    tests = [
        ("address forms parse", test_the_address_forms_proxima_actually_uses),
        ("missing tail stays blank", test_a_missing_tail_is_left_blank_not_guessed),
        ("suburb is not a state", test_a_suburb_is_never_mistaken_for_a_state),
        ("zero means not recorded", test_zero_means_not_recorded_not_zero),
        ("prices parse", test_price_strings_parse),
        ("lot number unpadded", test_lot_number_loses_the_padding_but_not_the_lot),
        ("a lot maps onto the schema", test_a_lot_maps_onto_the_schema),
        ("builder read, never invented", test_the_builder_is_read_from_the_header_never_invented),
        ("estate is the right project", test_the_estate_is_the_project_the_lot_actually_sits_in),
        ("no price is not a listing", test_a_lot_with_no_price_is_not_a_listing),
        ("no address is refused", test_a_lot_with_no_address_is_refused),
        ("state falls back to project", test_the_state_falls_back_to_the_project_location),
        ("cross-listed stored once", test_a_cross_listed_lot_is_stored_once_and_counted),
        ("different lots both survive", test_genuinely_different_lots_both_survive),
        ("channel matches portal_config", test_the_channel_matches_the_portal_config),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] proxima-src: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] proxima-src: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
