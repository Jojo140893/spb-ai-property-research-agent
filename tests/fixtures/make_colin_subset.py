"""Build the Colin search fixture and the build_packages golden outputs.

    python tests/fixtures/make_colin_subset.py subset --db <read-only 17 Sep DB> --work <scratch dir>
        [--snapshot <stock.json already built from that DB>]
    python tests/fixtures/make_colin_subset.py golden --snapshot <full 17 Sep stock.json>
    python tests/fixtures/make_colin_subset.py enrich --snapshot <stock.json built by today's build_web>

WHY A SUBSET. Colin searched "QLD under $750k" on 15 Sep and got two wrong answers. The
offline replay of that search against a snapshot built from the 17 Sep database matched
the live API exactly, which makes it the test oracle for Part C -- but the snapshot is
19,800 rows of catalogue data with capability URLs in it, and cannot be committed. The
subset keeps only what Colin's QLD searches can reach:

  * every row (superseded ones included) on any document holding a live QLD or
    blank-state row priced at or under $850,000;
  * a document-less seed row on its own (a blank source_doc is not a document);
  * every row in any state sharing (builder_name, lot_number) with a seed row, both
    non-blank -- the newer-capture guard (W11) needs a lot's other captures;
  * with every link stripped: source_link_*, rea_*, listing_url, floorplan_url,
    brochure_url, and any URL written inside a text value.

It then PROVES it is a faithful stand-in: the QLD 750k / 760k / 770k shortlist hashes and
scored pools on the subset must equal the full snapshot's, with today frozen at
16/09/2026 (the day the live replays matched). It refuses to write a subset that does
not.

WHY TODAY IS FROZEN. build_packages ages every row against today. Left at "now", the
Dawn lots cross the 30-day line on 19 Sep and the goldens would drift daily.

NEVER RUN ON A DATABASE IN A REPO. --db is copied into --work before anything opens it,
and both the copy and the build go to --work, which must not be inside a git checkout.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parents[1]
FIXTURE = HERE / "colin_17sep_subset.json"
GOLDEN = HERE / "golden_build_packages.json"

TODAY = datetime(2026, 9, 16)
TODAY_TEXT = "16/09/2026"
SEED_PRICE_MAX = 850_000

# Fields build_packages adds from W04 on. The goldens were saved BEFORE that change, so
# they are compared with these removed and the test asserts they are the ONLY additions.
# W11 added the confirm guard, the emailed list's own age and the informational counts.
W04_PACKAGE_KEYS = ("age_days", "confirm")
W11_PACKAGE_KEYS = ("confirm_guard", "email_first_seen", "list_age_days")
NEW_PACKAGE_KEYS = W04_PACKAGE_KEYS + W11_PACKAGE_KEYS
W04_COUNT_KEYS = ("counts_in_range", "admitted")
W11_COUNT_KEYS = ("just_over", "stale_over_limit", "newer_capture_demoted",
                  "confirm_guarded", "unstated_not_admitted")
# 26 Sep: the lot's own page answering 404/410 (link_check.py) is a gate of its own. The
# fixture's rows were cut before the check existed and carry no lot_page_gone, so it is 0.
LINK_CHECK_COUNT_KEYS = ("lot_page_gone",)
NEW_COUNT_KEYS = W04_COUNT_KEYS + W11_COUNT_KEYS + LINK_CHECK_COUNT_KEYS

# Cells build_web derives at build time that the fixture did not carry when it was cut
# (W07 price evidence, W11 emailed-list identity). `enrich` adds them to the SAME rows.
BUILD_TIME_KEYS = ("price_kind", "text_price_max", "price_in_text",
                   "email_list", "email_first_seen")

# Link-bearing fields. Never committed.
_LINK_FIELDS = ("listing_url", "floorplan_url", "brochure_url", "source_url",
                "stocklist_file")
_LINK_PREFIXES = ("source_link_", "rea_")
_URL_IN_TEXT = re.compile(r"(?:https?://|www\.)\S*", re.I)
URL_PLACEHOLDER = "[link removed]"

# The briefs, written out literally. Every Research-tab default is spelled, because the
# form posts all of them and a golden must replay exactly what was sent.
_FORM = {
    "client_name": "Part C golden", "budget_min": 0, "budget_max": 750000,
    "preferred_spending_cap": 750000, "buyer_type": "", "state": "QLD",
    "primary_suburbs": [], "bedrooms_min": 0, "bathrooms_min": 0, "car_spaces_min": 0,
    "storeys_max": 3, "land_size_min_sqm": 0, "house_size_min_sqm": 0,
    "search_radius_km": 0, "product_type": [], "requires_incentive": False,
}
MAX_SAFE = 9007199254740991


def _brief(**changes):
    out = json.loads(json.dumps(_FORM))
    out.update(changes)
    return out


# (name, brief, runs on) -- "subset" briefs are committed in full; "full" ones need the
# 17 Sep snapshot and are checked locally only (see the golden file's note).
BRIEFS = [
    ("qld_750k", _brief(), "subset"),
    ("qld_760k", _brief(budget_max=760000, preferred_spending_cap=760000), "subset"),
    ("qld_770k", _brief(budget_max=770000, preferred_spending_cap=770000), "subset"),
    # Variant A: the budget typed as "750" -- a $750 ceiling.
    ("variant_A_budget_typed_750",
     _brief(budget_max=750, preferred_spending_cap=750), "subset"),
    # Variant B: QLD selected and "Queensland" typed into Target Suburbs.
    ("variant_B_qld_plus_suburb_queensland",
     _brief(primary_suburbs=["Queensland"]), "subset"),
    # Variant C: "Any state" left selected and "Queensland" typed as a suburb.
    ("variant_C_anystate_suburb_queensland",
     _brief(state="", primary_suburbs=["Queensland"]), "subset"),
    # Variant D: "Any state", no suburb.
    ("variant_D_anystate_no_suburb", _brief(state=""), "subset"),
    # Variant E: Max left blank (the form sends MAX_SAFE_INTEGER), $750k in Preferred.
    ("variant_E_max_blank_pref_750k",
     _brief(budget_max=MAX_SAFE, preferred_spending_cap=750000), "subset"),
    # Not QLD stock: the subset cannot answer these, so they are local-only.
    ("werribee_vic_700k_4bed",
     _brief(state="VIC", primary_suburbs=["Werribee"], budget_max=700000,
            preferred_spending_cap=700000, bedrooms_min=4), "full"),
    ("vic_900k_4bed",
     _brief(state="VIC", budget_max=900000, preferred_spending_cap=900000,
            bedrooms_min=4), "full"),
    ("nsw_750k", _brief(state="NSW"), "full"),
    # The twelfth: the plan names eleven. This one reaches the gates the others never
    # do -- a price floor, binding bedroom/bathroom minimums and a single-storey brief --
    # so a reordered gate cannot hide behind unexercised code.
    ("qld_600k_800k_4bed_2bath_single_storey",
     _brief(budget_min=600000, budget_max=800000, preferred_spending_cap=800000,
            bedrooms_min=4, bathrooms_min=2, storeys_max=1), "subset"),
    # And one for the product-type and incentive gates, which no other brief turns on
    # (no QLD row carries an incentive, so it has to be Any state to package anything).
    ("anystate_house_land_requires_incentive",
     _brief(state="", budget_max=MAX_SAFE, preferred_spending_cap=MAX_SAFE,
            product_type=["House & Land"], requires_incentive=True), "subset"),
]

# The QLD searches the subset must reproduce exactly.
PROOF_BRIEFS = ("qld_750k", "qld_760k", "qld_770k")

# Fields that decide whether two snapshots are the same catalogue for build_packages.
FINGERPRINT_FIELDS = (
    "content_hash", "builder_name", "lot_address", "street_address", "lot_number",
    "suburb", "suburb_source", "state", "availability_status", "state_source", "price",
    "land_price", "build_price", "bedrooms", "bathrooms", "car_spaces", "land_sqm",
    "house_sqm", "storey", "title_status", "estate_name", "incentive_amount",
    "incentive_text", "product_type", "source_channel", "attribution_scope",
    "date_checked", "superseded_by", "source_doc")


# ----------------------------------------------------------------------------- helpers

def _api():
    sys.dont_write_bytecode = True
    for p in (str(APP / "api"), str(APP)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import _candidates
    return _candidates


def load_rows(path):
    return _api()._read_json(Path(path))


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def scrub_urls(value):
    """Replace any URL inside a value, recursively. Deterministic, so a golden built
    from scrubbed output compares against output scrubbed the same way."""
    if isinstance(value, str):
        if "://" in value or "www." in value.lower():
            return _URL_IN_TEXT.sub(URL_PLACEHOLDER, value) if _URL_IN_TEXT.search(value) \
                else "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        return value
    if isinstance(value, dict):
        return {k: scrub_urls(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_urls(v) for v in value]
    return value


def canonical_packages(packages):
    """Packages as compared: W04's additive keys removed, URLs scrubbed."""
    out = []
    for p in packages:
        out.append(scrub_urls({k: v for k, v in p.items() if k not in NEW_PACKAGE_KEYS}))
    return out


def canonical_counts(counts):
    """[(key, value)] in the dict's own order, W04's additive keys removed."""
    return [[k, v] for k, v in counts.items() if k not in NEW_COUNT_KEYS]


def digest(obj):
    raw = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=True,
                     separators=(",", ":"))
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def fingerprint(rows):
    """sha256 over the fields build_packages reads, in row order."""
    h = hashlib.sha256()
    for r in rows:
        h.update(json.dumps([r.get(f) for f in FINGERPRINT_FIELDS], default=str,
                            ensure_ascii=True).encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def golden_record(packages, counts, keep_packages_up_to=60):
    canon = canonical_packages(packages)
    record = {
        "counts": canonical_counts(counts),
        "content_hashes": [p.get("content_hash") for p in packages],
        "packages_digest": digest(canon),
    }
    if len(canon) <= keep_packages_up_to:
        record["packages"] = canon
    return record


def _refuse_repo_path(path):
    p = Path(path).resolve()
    for parent in [p] + list(p.parents):
        if (parent / ".git").exists():
            raise SystemExit("refusing to write a snapshot or database copy inside a git "
                             "checkout: %s" % parent)
    if str(p).lower().replace("\\", "/").startswith("d:/coleen/app"):
        raise SystemExit("refusing to touch D:/Coleen/app: %s" % p)


# ------------------------------------------------------------------------------ subset

def select_subset(rows):
    """The rows Colin's QLD searches can reach. See the module docstring for the rule."""
    seeds = [r for r in rows
             if not r.get("superseded_by")
             and str(r.get("state") or "").strip().upper() in ("QLD", "")
             and (_num(r.get("price")) or 0) > 0
             and _num(r.get("price")) <= SEED_PRICE_MAX]
    docs = {str(r.get("source_doc") or "") for r in seeds}
    docs.discard("")
    seed_ids = {id(r) for r in seeds}
    lots = {(str(r.get("builder_name") or "").strip().lower(),
             str(r.get("lot_number") or "").strip().lower())
            for r in seeds
            if str(r.get("builder_name") or "").strip()
            and str(r.get("lot_number") or "").strip()}
    kept = []
    for r in rows:
        key = (str(r.get("builder_name") or "").strip().lower(),
               str(r.get("lot_number") or "").strip().lower())
        if (str(r.get("source_doc") or "") in docs or id(r) in seed_ids
                or key in lots):
            kept.append(r)
    return kept


def strip_links(row):
    out = {}
    for k, v in row.items():
        if k in _LINK_FIELDS or k.startswith(_LINK_PREFIXES):
            continue
        if isinstance(v, str) and _URL_IN_TEXT.search(v):
            v = _URL_IN_TEXT.sub(URL_PLACEHOLDER, v)
        out[k] = v
    return out


def research_outcome(snapshot_path, brief, work):
    """(shortlist hashes, scored-pool hashes) from the REAL run_research, today frozen."""
    os.environ["SPB_SNAPSHOT_JSON"] = str(snapshot_path)
    os.environ["SPB_SCRATCH"] = str(Path(work) / "scratch")
    os.environ.pop("SPB_COMP_PROVIDER", None)
    os.environ.pop("GROQ_API_KEY", None)
    _candidates = _api()
    import research
    real_build = _candidates.build_packages
    captured = {}

    def frozen(brief_dict, rows, today=None, **kw):
        return real_build(brief_dict, rows, today=TODAY, **kw)

    agent = research._bootstrap.get_agent()
    real_rpr = agent.run_property_research

    def rpr(brief_dict, packages):
        captured["scored"] = sorted(str(p.get("content_hash")) for p in packages)
        return real_rpr(brief_dict, packages)

    _candidates.build_packages = frozen
    agent.run_property_research = rpr
    try:
        res = research.run_research({"client_brief": dict(brief)})
    finally:
        _candidates.build_packages = real_build
        agent.run_property_research = real_rpr
    return (sorted(s["content_hash"] for s in res["shortlist"]),
            captured.get("scored", []))


def cmd_subset(args):
    work = Path(args.work)
    _refuse_repo_path(work)
    work.mkdir(parents=True, exist_ok=True)
    if args.snapshot:
        full = Path(args.snapshot)
    else:
        if not args.db:
            raise SystemExit("--db or --snapshot is required")
        db_copy = work / "subset-source.db"
        shutil.copyfile(args.db, db_copy)
        os.environ["SPB_DATABASE_PATH"] = str(db_copy)
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        sys.path.insert(0, str(APP))
        import build_web
        import config
        # build_web reads config.DATABASE_PATH, fixed when config is first imported. If
        # anything imported it before the variable above was set, it would read the
        # checkout's own database instead of the scratch copy -- refuse, never guess.
        if Path(config.DATABASE_PATH).resolve() != db_copy.resolve():
            raise SystemExit("config.DATABASE_PATH is %s, not the scratch copy %s; run "
                             "this command in a fresh process" % (config.DATABASE_PATH,
                                                                  db_copy))
        out = work / "site"
        build_web.build(out, with_assets=False)
        full = out / "stock.json"
    with open(full, encoding="utf-8") as f:
        payload = json.load(f)
    keys = payload["keys"]
    rows = [dict(zip(keys, r)) for r in payload["rows"]]
    subset = [strip_links(r) for r in select_subset(rows)]
    out_keys = [k for k in keys if not (k in _LINK_FIELDS or k.startswith(_LINK_PREFIXES))]
    fixture = {
        "note": ("Colin's QLD search fixture, cut from a build_web snapshot of the 17 Sep "
                 "2026 database by tests/fixtures/make_colin_subset.py. Every link "
                 "removed. Reproduces the full snapshot's QLD 750k/760k/770k shortlists "
                 "and scored pools with today frozen at %s." % TODAY_TEXT),
        "source_snapshot_generated": payload.get("generated"),
        "source_snapshot_rows": len(rows),
        "source_snapshot_fingerprint": fingerprint(rows),
        "rule": "live QLD or blank-state rows priced <= $%d, every row on their "
                "documents, and every (builder_name, lot_number) twin" % SEED_PRICE_MAX,
        "today": TODAY_TEXT,
        "keys": out_keys,
        "rows": [[r.get(k) for k in out_keys] for r in subset],
    }
    blob = json.dumps(fixture, ensure_ascii=False, separators=(",", ":"))
    for bad in ("://", "www.", "dropbox", "rlkey"):
        assert bad not in blob.lower(), "the fixture still carries %r" % bad
    tmp = work / "colin_17sep_subset.candidate.json"
    tmp.write_text(blob, encoding="utf-8")

    brief_of = {name: b for name, b, _where in BRIEFS}
    failures = []
    for name in PROOF_BRIEFS:
        on_full = research_outcome(full, brief_of[name], work)
        on_subset = research_outcome(tmp, brief_of[name], work)
        print("%s  full shortlist %d pool %d | subset shortlist %d pool %d" % (
            name, len(on_full[0]), len(on_full[1]), len(on_subset[0]), len(on_subset[1])))
        if on_full != on_subset:
            failures.append((name, on_full, on_subset))
    if failures:
        for name, a, b in failures:
            print("MISMATCH", name, "\n  full  ", a, "\n  subset", b)
        raise SystemExit("the subset does not reproduce the full snapshot; not written")
    target = Path(args.out) if args.out else FIXTURE
    target.write_text(blob, encoding="utf-8")
    print("wrote %s: %d rows of %d" % (target, len(subset), len(rows)))


def cmd_enrich(args):
    """Add BUILD_TIME_KEYS to the committed fixture's rows, from a snapshot built from the
    same database by a build_web that writes them. The row set, order and every
    fingerprint field must come out unchanged -- so the goldens keyed on the fixture's
    fingerprint stay valid -- or nothing is written."""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    keys = fixture["keys"]
    rows = [dict(zip(keys, r)) for r in fixture["rows"]]
    before = fingerprint(rows)
    with open(args.snapshot, encoding="utf-8") as f:
        payload = json.load(f)
    missing = [k for k in BUILD_TIME_KEYS if k not in payload["keys"]]
    if missing:
        raise SystemExit("the snapshot has no %s; build it with the current build_web"
                         % missing)
    built = {}
    for r in payload["rows"]:
        row = dict(zip(payload["keys"], r))
        built.setdefault(row.get("content_hash"), []).append(row)
    for row in rows:
        twins = built.get(row.get("content_hash")) or []
        if len(twins) != 1:
            raise SystemExit("fixture row %s is not exactly once in the snapshot"
                             % row.get("content_hash"))
        source = strip_links(twins[0])          # compared as the fixture was cut
        for f in FINGERPRINT_FIELDS:
            if row.get(f) != source.get(f):
                raise SystemExit("fixture row %s differs from the snapshot on %s; this "
                                 "snapshot is not the one the fixture was cut from"
                                 % (row.get("content_hash"), f))
        for k in BUILD_TIME_KEYS:
            row[k] = scrub_urls(source.get(k))
    if fingerprint(rows) != before:
        raise SystemExit("enriching moved the fingerprint; not written")
    out_keys = keys + [k for k in BUILD_TIME_KEYS if k not in keys]
    fixture["keys"] = out_keys
    fixture["rows"] = [[r.get(k) for k in out_keys] for r in rows]
    fixture["build_time_keys"] = (
        "%s added by `make_colin_subset.py enrich` from a snapshot of the same 17 Sep "
        "database built by the W11 build_web; rows and fingerprint unchanged"
        % list(BUILD_TIME_KEYS))
    blob = json.dumps(fixture, ensure_ascii=False, separators=(",", ":"))
    for bad in ("://", "www.", "dropbox", "rlkey"):
        assert bad not in blob.lower(), "the fixture still carries %r" % bad
    FIXTURE.write_text(blob, encoding="utf-8")
    print("enriched %s: %d rows, keys %s" % (FIXTURE, len(rows), list(BUILD_TIME_KEYS)))


# ------------------------------------------------------------------------------ golden

# Why the goldens are what they are. A regeneration that is not one of these is a
# behaviour change nobody documented, and must add its own entry with its proof.
GOLDEN_HISTORY = (
    "W04: saved with default arguments before the _gate split, today 16/09/2026.",
    "W11: regenerated for exactly two documented changes. Proof: the W11 code run with "
    "BUDGET_HEADROOM patched back to 50,000 and the newer-capture check switched off "
    "reproduced every pre-W11 golden (counts, content_hashes and packages_digest) for all "
    "13 briefs on both the subset and the full 17 Sep snapshot. (1) BUDGET_HEADROOM "
    "50,000 -> 0: packages priced in (budget, budget + $50k] now stop at over_budget and "
    "are counted in counts['just_over'] -- qld_750k -8 (just_over 8), qld_760k -9, "
    "qld_770k -16, qld_600k_800k -20, werribee -21, nsw_750k -177 -- and where the 600-row "
    "cap binds the freed slots re-spread (vic_900k_4bed: truncated -68, 214 in-budget "
    "packages swapped by the cap). Rows those packages used to carry past later gates move "
    "their counts too (stale_unverified, incomplete_facts, same_listing_collapsed, "
    "storey_unknown_and_binding, no_suburb). (2) Critique 28: a fresh package whose lot a "
    "later capture marks gone is no longer verified -- v2:46bc9cca (Avia lot 57, variant_E, "
    "both fixtures) and v2:b0dfaf90 (variant_C, variant_D, werribee; full snapshot). The "
    "live-only availability change the plan listed (W11.2) was not made (critique 1).",
)


def cmd_golden(args):
    _candidates = _api()
    subset_rows = load_rows(FIXTURE)
    full_rows = load_rows(args.snapshot) if args.snapshot else None
    record = {
        "note": ("build_packages(brief, rows, today=16/09/2026) with default arguments, "
                 "first saved BEFORE W04 split the loop into _gate and regenerated only "
                 "for the changes listed in 'history'. 'subset' briefs run on "
                 "colin_17sep_subset.json and are checked everywhere. 'full' outputs need "
                 "the 17 Sep snapshot (matched by full_snapshot_fingerprint) and are "
                 "checked only where SPB_SNAPSHOT_JSON points at it; they are stored as "
                 "content_hashes plus a digest because the packages carry links. Packages "
                 "are compared with %s removed and URLs scrubbed; counts with %s removed."
                 % (list(NEW_PACKAGE_KEYS), list(NEW_COUNT_KEYS))),
        "history": list(GOLDEN_HISTORY),
        "today": TODAY_TEXT,
        "subset_fingerprint": fingerprint(subset_rows),
        "full_snapshot_fingerprint": fingerprint(full_rows) if full_rows else None,
        "fingerprint_fields": list(FINGERPRINT_FIELDS),
        "briefs": {},
    }
    for name, brief, where in BRIEFS:
        entry = {"brief": brief, "runs_on": where}
        if where == "subset":
            packages, counts = _candidates.build_packages(brief, subset_rows, today=TODAY)
            entry["subset"] = golden_record(packages, counts)
        if full_rows is not None:
            packages, counts = _candidates.build_packages(brief, full_rows, today=TODAY)
            full = golden_record(packages, counts, keep_packages_up_to=0)
            entry["full_snapshot"] = full
        record["briefs"][name] = entry
        print(name, {k: len(v.get("content_hashes", [])) for k, v in entry.items()
                     if isinstance(v, dict) and "content_hashes" in v})
    blob = json.dumps(record, ensure_ascii=False, indent=1)
    for bad in ("://", "www.", "dropbox", "rlkey"):
        assert bad not in blob.lower(), "the golden file still carries %r" % bad
    GOLDEN.write_text(blob + "\n", encoding="utf-8")
    print("wrote", GOLDEN)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("subset")
    s.add_argument("--db", help="the read-only 17 Sep database (copied before use)")
    s.add_argument("--work", required=True, help="scratch directory outside any repo")
    s.add_argument("--snapshot", help="a stock.json already built from that database")
    s.add_argument("--out", help="write the fixture here instead of tests/fixtures/")
    g = sub.add_parser("golden")
    g.add_argument("--snapshot", help="the full 17 Sep stock.json (for local-only goldens)")
    e = sub.add_parser("enrich")
    e.add_argument("--snapshot", required=True,
                   help="a stock.json built from the 17 Sep database by the current build_web")
    args = ap.parse_args(argv)
    {"subset": cmd_subset, "golden": cmd_golden, "enrich": cmd_enrich}[args.cmd](args)


if __name__ == "__main__":
    main()
