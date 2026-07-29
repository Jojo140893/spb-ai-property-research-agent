"""
Tests for listing identity (content_hash) and the column spec.

The pinned-hash test is the guard that stops an innocent refactor of
building_content_hash from silently duplicating the client's whole stock table on
the next harvest.
"""

import sqlite3
import tempfile
from pathlib import Path

from database import (BUILDINGS_EXTRA_COLUMNS, HASH_RECIPE_VERSION,
                      ResearchDatabase, building_content_hash)

# A real E-Agent row shape (VIC Regional stocklist).
_ROW = {
    "source_channel": "E-Agent",
    "attribution_scope": "builder",
    "builder_name": "EVO Homes",
    "suburb": "Winter Valley",
    "lot_number": "Lot 82",
    "house_design": "Empley 15",
    "land_size_sqm": 282,
    "lot_address": "Lot 82 Aberdeen 282 142.8 12.0 Sep-26 Empley 15 3x2x2 $205,000 $335,220 $540,220 Available",
}

# Pinned: if this changes, every stored row's identity changes and the next
# harvest re-inserts the entire table as new rows. Change it only deliberately,
# alongside a HASH_RECIPE_VERSION bump and a migration.
_PINNED = "v2:d62b7860f32b1f228e7e475b478149a98afbf9bae45e0585a020b7c4c62670d1"


def test_hash_matches_pinned_value():
    """If this fails, listing identity has changed — every stored row would be
    re-inserted as new on the next harvest. Only update _PINNED deliberately,
    together with a HASH_RECIPE_VERSION bump and a migration for existing rows."""
    assert building_content_hash(_ROW) == _PINNED, (
        "content_hash recipe changed — this WILL duplicate the stock table unless "
        "HASH_RECIPE_VERSION is bumped and existing rows are re-hashed")


def test_hash_ignores_volatile_fields():
    """Price, availability, title and date must NOT affect identity — they update in place."""
    base = building_content_hash(_ROW)
    for volatile in (
        {"price": 999_999}, {"advertised_package_price": 1_234_567},
        {"availability_status": "On Hold"}, {"title_status": "Titled"},
        {"date_checked": "01/01/2027"}, {"extraction_confidence": 0.1},
        {"incentive_amount": 15000},
        # written by the enrichment pass — must not change identity
        {"state": "VIC"}, {"builder_matched": "Evo Homes Pty Ltd"},
    ):
        assert building_content_hash({**_ROW, **volatile}) == base, f"{volatile} changed the hash"


def test_hash_distinguishes_real_listings():
    """Different lots / builders / suburbs must be different rows."""
    base = building_content_hash(_ROW)
    for distinct in ({"lot_number": "Lot 83"}, {"builder_name": "Hattan Homes"},
                     {"suburb": "Tarneit"}, {"house_design": "Westgarth 22"},
                     {"land_size_sqm": 400}, {"attribution_scope": "state_pooled"}):
        assert building_content_hash({**_ROW, **distinct}) != base, f"{distinct} collided"


def test_hash_separates_designs_on_the_same_lot():
    """Verified live: the VIC stocklist lists Lot 414 twice, as "Arklay 17" and
    "Dunestone 22" — different packages at different prices, so different rows.
    NSW does the same with "Vesper SG" / "Vesper DG"."""
    base = {"source_channel": "E-Agent", "attribution_scope": "state_pooled",
            "builder_name": "", "suburb": "Colac", "lot_number": "414"}
    arklay = {**base, "lot_address": "Lot 414 Clearwater Estate (Arklay 17) 392 162.1 14.0 "
                                     "Q2-2026 Arklay 17 4x2x2 $249,000 $366,228 $615,228 Available"}
    dune = {**base, "lot_address": "Lot 414 Clearwater Estate (Dunestone 22) 392 203.5 14.0 "
                                   "Q2-2026 Dunestone 22 4x2x2 $249,000 $397,121 $646,121 Available"}
    assert building_content_hash(arklay) != building_content_hash(dune)

    # ...but the SAME package keeps its identity when price and status move
    moved = {**base, "lot_address": "Lot 414 Clearwater Estate (Arklay 17) 392 162.1 14.0 "
                                    "Q2-2026 Arklay 17 4x2x2 $249,000 $370,000 $625,000 On hold"}
    assert building_content_hash(arklay) == building_content_hash(moved)


def test_identity_survives_the_110_char_truncation_boundary():
    """The guarantee protecting the 373 rows Coleen has already reviewed.

    Those rows were stored with the whole source row jammed into `lot_address` and cut
    at 110 characters. The extractor now keeps the full row in `source_text` and puts a
    short label in `lot_address`. Both must resolve to the SAME identity, or the first
    harvest after this change re-inserts the entire reviewed table as new rows.

    Every fixture below is a real stored row, chosen because its tail is what the cut
    used to damage: a half-written status word, a spelled-out title quarter, a price
    sliced mid-number.
    """
    rows = [
        # status word cut in half: "... Available" -> "... avail"
        "Lot 235 Brookfield lakes Estate 652 208.1 14.5 Feb-26 $550 Freemont 208 4x2x2 "
        "$230,000 $424,066 $654,066 Available",
        # title word cut in half: "... Registered" -> "... Registe"
        "Dual Key NSW Lochinvar Lot 609 Vesper DG Hillcrest Estate 465 sqm 210 sqm "
        "$550,000 $639,011 $1,189,011 Available Registered",
        # spelled-out quarter: "... Quarter 4, 2026" -> "... qu"
        "Dual Key NSW Hunterview Lot 116 Vesper SG Langham Estate 596 sqm 210 sqm "
        "$414,000 $642,388 $1,056,388 Available Quarter 4, 2026",
        # price cut mid-number: "$ 1,533,199" -> "$ 1,533,1"
        "CC-0114 506 Titled 200 5 + 5 + 3 MORETON SINGLE $ 2,380 $ 123,760 8.07% "
        "Download $ 839,000 $ 694,199 $ 1,533,199",
        # a QLD portal row whose tail is a bare "$"
        "Under offer 10/06/2026 1 Galahad Street Lot 91 Galahad Street Marsden 4132 "
        "LOGAN QLD September 2026 402 14.9 $630,000 $555,000 $1,185,000",
    ]
    for full in rows:
        stored_old = {"source_channel": "E-Agent", "lot_address": full[:110]}
        harvested_new = {"source_channel": "E-Agent", "source_text": full,
                         "lot_address": "Lot 82, Aberdeen"}   # the short label
        assert building_content_hash(stored_old) == building_content_hash(harvested_new), (
            f"re-harvesting this row would duplicate it:\n  {full[:90]}...")


def test_price_only_siblings_are_numbered_so_both_survive():
    """Verified on the live APLACE / Met Invest / Paramount stocklists: two rows in one
    file can be identical in every field identity uses and still be two real packages,
    differing only by price — which identity ignores on purpose so a price move updates
    in place. 10 of 983 per-builder listings were silently replacing each other.

    Byte-identical rows must still collapse: those are one row detected twice.
    """
    from sources.spreadsheet_extract import _assign_variant_ordinals

    def row(text, price):
        return {"source_channel": "E-Agent", "attribution_scope": "builder",
                "builder_name": "Met Invest by Metricon", "suburb": "Bulla",
                "source_text": text, "advertised_package_price": price}

    text = "420 Alves Street Bulla VIC St Ronans House & Land 350.00 4 2 2 2 Part Contract"
    variants = [row(f"{text} ${p:,}", p) for p in (736_364, 760_471)]
    assert building_content_hash(variants[0]) == building_content_hash(variants[1]), \
        "fixture no longer reproduces the collision it exists to test"
    assert _assign_variant_ordinals(variants) == 1
    assert building_content_hash(variants[0]) != building_content_hash(variants[1])

    duplicates = [row(f"{text} $736,364", 736_364) for _ in range(3)]
    assert _assign_variant_ordinals(duplicates) == 0, "identical rows must still collapse"
    assert len({building_content_hash(d) for d in duplicates}) == 1


def test_ordinal_zero_leaves_the_hash_byte_identical():
    """The ordinal is appended to the hash basis ONLY when non-zero, so adding the
    concept did not re-identify a single one of the rows already stored."""
    base = building_content_hash(_ROW)
    assert building_content_hash({**_ROW, "variant_ordinal": 0}) == base
    assert building_content_hash({**_ROW, "variant_ordinal": None}) == base
    assert building_content_hash({**_ROW, "variant_ordinal": 1}) != base
    assert base == _PINNED


def test_hash_never_empty_for_degenerate_rows():
    """A row with no lot number or design still gets a distinct, non-null hash."""
    a = building_content_hash({"source_channel": "E-Agent", "lot_address": "some blob A"})
    b = building_content_hash({"source_channel": "E-Agent", "lot_address": "some blob B"})
    assert a and b and a != b
    assert a.startswith(HASH_RECIPE_VERSION + ":")


def test_column_spec_types_are_valid():
    """Guards the ALTER TABLE f-string: only known types, no duplicates."""
    names = [c for c, _t in BUILDINGS_EXTRA_COLUMNS]
    assert len(names) == len(set(names)), "duplicate column in spec"
    for col, coltype in BUILDINGS_EXTRA_COLUMNS:
        assert coltype in ("TEXT", "REAL", "INTEGER"), f"{col} has bad type {coltype}"
        assert col.replace("_", "").isalnum(), f"{col} is not a safe identifier"
    spec = dict(BUILDINGS_EXTRA_COLUMNS)
    # postcode must be TEXT (leading-zero NT postcodes) and frontage numeric
    assert spec["postcode"] == "TEXT"
    assert spec["frontage_m"] == "REAL"


def test_migration_is_idempotent_and_adds_every_column():
    tmp = Path(tempfile.mkdtemp()) / "ident.db"
    ResearchDatabase(db_path=tmp)
    ResearchDatabase(db_path=tmp)          # second init must not error
    cols = [r[1] for r in sqlite3.connect(str(tmp)).execute("PRAGMA table_info(buildings)")]
    for col, _t in BUILDINGS_EXTRA_COLUMNS:
        assert col in cols, f"{col} missing after init"


def test_schema_meta_roundtrip():
    tmp = Path(tempfile.mkdtemp()) / "meta.db"
    db = ResearchDatabase(db_path=tmp)
    assert db.get_meta("buildings_schema_version") is None
    db.set_meta("buildings_schema_version", "2")
    assert db.get_meta("buildings_schema_version") == "2"
    db.set_meta("buildings_schema_version", "3")          # upsert, not duplicate
    assert db.get_meta("buildings_schema_version") == "3"


def run_all():
    tests = [
        ("hash matches pinned value", test_hash_matches_pinned_value),
        ("hash ignores volatile fields", test_hash_ignores_volatile_fields),
        ("hash distinguishes real listings", test_hash_distinguishes_real_listings),
        ("hash separates designs on same lot", test_hash_separates_designs_on_the_same_lot),
        ("identity survives 110-char truncation", test_identity_survives_the_110_char_truncation_boundary),
        ("price-only siblings both survive", test_price_only_siblings_are_numbered_so_both_survive),
        ("ordinal 0 changes no stored hash", test_ordinal_zero_leaves_the_hash_byte_identical),
        ("hash never empty for degenerate rows", test_hash_never_empty_for_degenerate_rows),
        ("column spec types valid", test_column_spec_types_are_valid),
        ("migration idempotent + complete", test_migration_is_idempotent_and_adds_every_column),
        ("schema_meta roundtrip", test_schema_meta_roundtrip),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f" [PASS] identity: {name}")
        except AssertionError as e:
            failed += 1
            print(f" [FAIL] identity: {name}: {e}")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if run_all() else 0)
