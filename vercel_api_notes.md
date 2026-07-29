# Making "Research & Scoring" work on the Vercel deployment

`index.html` POSTs `{"client_brief": {...}}` to `/api/research`. That route only exists in
`server.py`, so on Vercel it 404s and the tab shows *"Needs the local app."*

`api/research.py` is that route as a Vercel Python function. **The frontend needs no
change** — the response is field-for-field identical to `server.py`'s `do_POST`, and the
handler answers `200 application/json`, which is exactly what `index.html:1071` tests
before deciding to show the offline note.

Nothing in `index.html`, `build_web.py`, `server.py` or `vercel_site/vercel.json` was
edited. This file is the integration list.

---

## 1. What was added (all new files)

| File | Purpose |
|---|---|
| `api/research.py` | The function. `handler` (a `BaseHTTPRequestHandler` subclass) — the entrypoint shape `@vercel/python` expects. `do_POST` → `run_research(payload)`; `do_GET` → a health/introspection JSON. |
| `api/_bootstrap.py` | Makes the pipeline importable and survivable on a read-only FS, and refuses to run if a credential-bearing file was deployed. |
| `api/_candidates.py` | Reads the deployed stock snapshot and turns it into `candidate_packages`, without inventing any fact. |
| `api/_export_builders.py` | Build-time writer for `api/_data/builders_public.csv` — the credential-free, PII-free builder registry the function reads. |
| `api/_data/builders_public.csv` | Generated artefact (33 builders). Regenerate at build time. |

Files starting with `_` are not turned into functions by Vercel, so only
`api/research.py` becomes a route.

## 2. It does not scrape

`run_property_research(brief, candidate_packages)` only crawls when
`candidate_packages` is falsy (`kommo_agent.py:73`). Three independent locks:

1. `api/research.py` always passes a **non-empty** list. If the pool is empty it
   returns a correctly-shaped empty result **without calling the pipeline at all** —
   passing `[]` would be falsy and would start a live crawl of every approved builder
   from a public URL.
2. `_bootstrap._disarm_sources()` replaces `search()`/`verify()` on
   `EAgentSource` / `BuilderPortalSource` / `DrivePdfSource` with a raiser
   (`ScrapeAttempted`), so a regression fails loudly instead of hitting the vendors.
3. No credential is deployed, so a crawl could not authenticate anyway.

The constructors themselves are network-free — verified by reading them: they only read
paths/credentials and build a lazy `requests.Session`. `config.DRIVE_INPUT_DIR` is also
re-pointed at an empty `/tmp` dir, so `DrivePdfSource` and `BenchmarkEngine` cannot pick
up a file that was not deliberately deployed.

## 3. No credentials, and no SQLite DB, in the bundle

`BuilderRegistry` reads `Book1(Builders) List.csv`, whose column 9 is a plaintext portal
password (and `config.BUILDER_CSV_PATH` prefers `drive_input/vendors.csv`, the live
credentialed copy). **Neither file may be deployed.** The pipeline still needs the
registry for `BuilderConfidenceModel` (Step 8) and the `builder_coverage` bar, so:

* `api/_export_builders.py` rewrites the registry in the same positional 11-column
  layout with columns 0,1,2 (contact name/email/phone) and 8,9 (portal login/password)
  **empty**, then reads the file back and aborts the build if any of them is non-empty.
* Every field it keeps — name, states, contract availability, E-Agent flag, portal URL,
  notes — is already published by `build_web.py` in `builders.json`. Nothing new is
  disclosed even if the CSV were served.
* `_bootstrap` points `config.BUILDER_CSV_PATH` at it so the inner `BuilderRegistry()`
  inside `BuilderPortalSource` loads the safe file too, and refuses to serve if the
  loaded registry has any login populated.

**The database is not bundled either.** `spb_research_audit.db` carries vendor contact
emails/phones and brochure text and would be a downloadable static asset. The function
scores `vercel_site/stock.json` instead — the field-allow-listed export `build_web.py`
already writes and already serves. Identical results, verified below.

`_bootstrap.bundle_violations()` is a runtime tripwire, enforced when `VERCEL` is set.
If any of these reach the deployment the function returns **500 with the offending
path** rather than serving traffic:
`Book1(Builders) List.csv`, `drive_input/vendors.csv`, `.env`, `credentials.json`,
`spb_research_audit.db`, `spb_research_audit.db.bak-*`, `.sessions/*.json`.
A leaked `.sessions/*.json` is a live portal cookie reusable by anyone who finds it.

## 4. Read-only filesystem

* `config.py` calls `mkdir()` at import time (`output/`, `drive_input/`, `assets/`).
  Under `/var/task` that raises `EROFS` and kills the import of every pipeline module.
  `_bootstrap` wraps `Path.mkdir` **for that one import only** and restores it
  immediately, so no pipeline code runs against a patched `mkdir`.
* `config.OUTPUT_DIR`, `ASSETS_DIR`, `DRIVE_INPUT_DIR` → `/tmp/spb/...`.
  The client report is written there, so `client_report_path` is returned as `""`
  (`client_report_html` — what the UI actually opens — is returned in full).
* `config.DATABASE_PATH` → `/tmp/spb/audit_ephemeral.db`. `ResearchDatabase.__init__`
  runs `CREATE TABLE`, so a read-only bundled `.db` would make the agent constructor
  throw. **Consequence to accept: the Step 14 audit trail written by
  `save_research_run` is ephemeral and dies with the instance.** Nothing else depends
  on it; the local app remains the system of record.
* The snapshot is opened read-only. The SQLite path (only used if you set
  `SPB_SNAPSHOT_DB`) uses `file:...?mode=ro&immutable=1`, which also stops SQLite
  wanting to create `-wal`/`-shm` beside a read-only file — verified: no such files
  appeared.

## 5. Add to `vercel_site/vercel.json`

Keep the existing `headers` block, add `functions`. Two layouts; **prefer A**.

### A — pipeline under `api/_lib/` (recommended)

Vercel's zero-config static builder excludes `api/**`, so the pipeline source is not
downloadable from the site. Only `index.html` and the JSON snapshots stay public
(verified locally: the servable set is exactly `index.html`, `stock.json`).

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "headers": [
    { "source": "/(.*)", "headers": [ { "key": "X-Robots-Tag", "value": "noindex, nofollow" } ] }
  ],
  "functions": {
    "api/research.py": {
      "memory": 1024,
      "maxDuration": 30,
      "includeFiles": "{api/_bootstrap.py,api/_candidates.py,api/_export_builders.py,api/_data/**,api/_lib/**,stock.json}"
    }
  }
}
```

### B — pipeline flat at the site root (repo layout)

Works identically; the `.py` files and `data/au_suburbs.csv` then *are* served as static
assets. Acceptable only because the repo is public and that CSV is open data — but
confirm on the first deploy by requesting `/config.py` and `/kommo_agent.py`.

```json
"functions": {
  "api/research.py": {
    "memory": 1024,
    "maxDuration": 30,
    "includeFiles": "{api/_*.py,api/_data/**,data/**,sources/**,benchmark.py,brief_parser.py,builder_registry.py,client_report.py,config.py,database.py,drive_ingest.py,geo.py,kommo_agent.py,kommo_client.py,qa_checker.py,report_generator.py,schema.py,scoring_engine.py,secrets_store.py,turnkey_calculator.py,stock.json}"
  }
}
```

`includeFiles` globs are relative to the project root. `_bootstrap` detects which
layout it is in (`api/_lib` if present, else the site root) — no code change either way.
`maxDuration`/`memory` are comfortable rather than necessary; the defaults (10 s,
1024 MB) also fit.

## 6. Add to `build_web.py`

1. **Copy the function** into `out_dir/api/`:
   `research.py`, `_bootstrap.py`, `_candidates.py`, `_export_builders.py`.
2. **Generate the safe registry** (needs the live CSV, which stays on the build
   machine):
   ```python
   from api._export_builders import write_public_registry
   path, n = write_public_registry(out_dir)     # out_dir/api/_data/builders_public.csv
   ```
3. **Copy the 28 pipeline modules + the suburb index**, into `out_dir/api/_lib/`
   (layout A) or `out_dir/` (layout B):

   root (16): `benchmark.py brief_parser.py builder_registry.py client_report.py
   config.py database.py drive_ingest.py geo.py kommo_agent.py kommo_client.py
   qa_checker.py report_generator.py schema.py scoring_engine.py secrets_store.py
   turnkey_calculator.py`

   `sources/` (12): `__init__.py adaptive_extract.py base.py builder_portals.py
   dedupe.py drive_pdf.py e_agent.py feature_extract.py portal_config.py
   remote_stocklist.py scraper_base.py spreadsheet_extract.py`

   plus `data/au_suburbs.csv` (kept beside `config.py`, since `geo.py` resolves it as
   `PROJECT_ROOT/data/au_suburbs.csv`).

   That is the exact transitive import closure of `kommo_agent`, measured by importing
   it and listing project files in `sys.modules`.
4. **Extend the generated `vercel.json`** with the `functions` block from §5 — note
   `build_web.py` currently *overwrites* `vercel.json` on every build, so the block has
   to be added to that literal or it will be lost.
5. **`stock.json` is already written** and is what the function scores. No change.

### Must NOT be copied into `vercel_site/`

* `requirements.txt` — **the important one.** The function needs **zero** third-party
  packages; every heavy import in the pipeline is guarded
  (`playwright`, `pdfplumber`, `openpyxl`, `requests`, `keyring` all sit behind
  `try/except ImportError`). Ship a `requirements.txt` and Vercel will pip-install
  Playwright, pdfminer, openpyxl and the Google client into the function: hundreds of
  MB, and cold start goes from **0.6 s to 3.8 s** (measured — `import kommo_agent` alone
  costs 3.2 s once `pdfplumber` is importable, versus 0.25 s without it).
* `Book1(Builders) List.csv`, `drive_input/` (esp. `vendors.csv`), `.sessions/`,
  `.env`, `credentials.json`, `spb_research_audit.db`, `spb_research_audit.db.bak-*`,
  `output/`, `assets/` source PDFs beyond what `build_web.py` already copies.
* `server.py`, `build_web.py`, `harvest_buildings.py`, `portal_login.py`,
  `import_and_scrape.py`, `diagnose_*.py`, `enrich_buildings.py`,
  `migrate_buildings_identity.py`, `vendor_import.py`, `setup_credentials.py`,
  `tests/` — none are imported by the function.
* `__pycache__/` anywhere (including `api/__pycache__/`) — stale bytecode compiled for
  the build machine's Python, and pure upload weight.

A defensive `vercel_site/.vercelignore` with those entries is cheap insurance if anyone
ever deploys from the repo root instead.

## 7. Size and cold start (measured)

Assembled the exact bundle from §6 into a temp tree and ran the function out of it with
`VERCEL=1`, with `playwright/pdfplumber/openpyxl/requests/keyring` blocked from import
to reproduce the deployment's dependency set.

| | |
|---|---|
| Function code + data | **890 KB** — pipeline `.py` 251.5 KB, `api/` 44.7 KB, `data/au_suburbs.csv` 593.8 KB |
| Static site (unchanged, already deployed) | 1.39 MB (`index.html`, `stock.json`, `builders.json`, `vendor-assets.json`) |
| Total upload | **2.33 MB, 38 files** (limit is 250 MB unzipped) |
| Installed dependencies | none |
| Cold start, first request end-to-end | **1.1 – 1.6 s** |
| — agent construction | 596 ms (geo CSV 134 ms · scratch audit DB 272 ms · registry 1 ms) |
| — snapshot parse (4,308 rows) | 36 ms |
| — scoring + report | 70 – 200 ms |
| Warm request | **70 – 200 ms** |
| Response size | ~87 KB (mostly `search_area`: 626 suburbs for a 60 km radius) |

Vercel CPU is slower than this machine; budget roughly 2× — comfortably inside the
default 10 s limit.

## 8. Behaviour differences the parent should know about

The pipeline is unmodified; the candidates come from stored stock instead of a live
crawl. Consequences, all surfaced in the UI rather than hidden:

* **`run_property_research` invents defaults for missing package fields** —
  `bedrooms=4`, `land 400 m²`, `house 180 m²`, rent `$550–600/wk`, title
  `"Expected Q4 2026"`, `"Close to schools, train station & town centre"`. Fine for a
  live scrape that captured the real numbers; **not** fine here, where 3,543 of 4,308
  rows have no bedroom count and a fabricated 4 would sail through a "minimum 4
  bedrooms" brief. So `_candidates.py`:
  * **skips** any row missing a fact a mandatory filter reads (bedrooms, bathrooms,
    car spaces, house size), naming the missing field in the coverage note;
  * passes explicit blanks (`0`, `""`) for descriptive fields rather than letting the
    defaults fire — unknown land size scores 0 (costs points, never invents them),
    unknown storeys is `0` not `1`, rent is `0` (so the client report shows a 0.00 %
    yield instead of a made-up one), title is `"Not stated in source"`.
  * A blank builder becomes `"Builder not identified in source"`, **not** `""` —
    `BuilderRegistry.search_builder_by_name` does `query in registry_name`, so an empty
    query matches the *first* builder in the directory and would misattribute the lot.
    (That is a live bug for E-Agent packages, which set `builder_name: ""`;
    out of scope here, worth a separate look.)
* **The first "rejected" card is a coverage summary**, not a property: how many rows
  were scored and exactly why the rest were not. Example, real output for a QLD brief
  at $1.05 M: *scored 33 of 4,308 — 3,046 outside QLD, 86 with no state, 88 not
  available, 580 over the ceiling, 69 with no suburb, 399 without the facts the brief's
  minimums are checked against.* `rejected_count` counts in-state rows only; rows for
  other states are out of scope, not "filtered".
* **`rejected_log` is capped at 30 pipeline entries** (`SPB_MAX_REJECTED_LOG`) because
  `index.html` renders one card per entry. `rejected_count` stays exact and a card says
  the list was trimmed.
* **Verification lapses instead of being asserted.** A stored row *was* verified — by
  the scrape that captured it (`sources/e_agent.py:467`). The deployment cannot
  re-verify it, so the recorded verification is honoured for
  `SPB_SNAPSHOT_FRESH_DAYS` (default 14) and after that the row is passed through
  unverified, which the pipeline's own three-state rule turns into "Pending
  Confirmation" and keeps out of the final list. Verified with `SPB_SNAPSHOT_FRESH_DAYS=0`:
  the shortlist shrank 5 → 3 and the coverage note said so. **A stale deployment
  therefore degrades to an empty shortlist rather than to confident, out-of-date
  recommendations** — so redeploy after each harvest.
* **Same-lot duplicates are merged.** The table legitimately holds the same lot twice
  when two channels captured it (portal on the 27th, E-Agent on the 29th); the
  pipeline's `DedupeEngine` will not merge them because its key includes
  `house_design` and only one channel recorded a product type. Rows identical in
  builder, lot, suburb, price and all five size/count facts are collapsed
  (freshest capture kept, 7 in the QLD sample), then `DedupeEngine` runs as it does
  locally. Known residual: the same lot at two *different* prices ($964,227 and
  $964,813 for Central Springs Lot 1460) still yields two cards. That is a real price
  discrepancy in the source data — picking one would be a guess, so both are shown.
* **Rows marked Sold / On Hold / Under Offer / Reserved / Leased / Withdrawn are not
  candidates.** A blank availability is *not* treated as sold (most stocklists have no
  such column) — it just does not help the row.
* Budget pre-filter is `budget_max + $50,000`, the same headroom the live sources apply
  (`sources/e_agent.py:524`, `sources/builder_portals.py:204`).
* Benchmarking returns *"Unbenchmarked – Pending Market Data"* for everything, because
  comparables come from `drive_input/comparables*.csv` and none is deployed (they are
  client data). Scoring already handles that with a neutral 7.5/15 and a
  `needs_manual_benchmark` flag. If Coleen supplies a comparables file that is safe to
  publish, drop it in the bundle's `drive_input/`.

## 9. Environment variables (all optional)

| Var | Default | Effect |
|---|---|---|
| `SPB_SNAPSHOT_JSON` | `stock.json` beside the function | Explicit snapshot path |
| `SPB_SNAPSHOT_DB` | unset | Read stock from SQLite instead (`mode=ro&immutable=1`) |
| `SPB_SNAPSHOT_FRESH_DAYS` | `14` | How long a captured verification is honoured |
| `SPB_MAX_CANDIDATES` | `600` | Per-request scoring cap; truncation is reported |
| `SPB_MAX_REJECTED_LOG` | `30` | Rejection cards returned |
| `SPB_SCRATCH` | system temp | Where the ephemeral output/audit DB live |

## 10. Verification performed locally

No harvest was run; nothing in the repo was modified except the new files.
`python -X utf8 run_tests.py` → **24 PASSED, 0 FAILED** (unchanged).

* Imported `api/research.py` and invoked `run_research()` with a real brief
  (QLD, $1.05 M / $950 K preferred, Caboolture, 60 km, 4/2/2, ≤2 storeys, ≥300 m² land,
  ≥150 m² house) → `status: success`, 5 shortlisted, `rejected_count` 1163,
  `qa_passed: true`, 5 reports, 626-suburb search area, `builder_coverage`
  13 of 33 builders in scope (9 E-Agent, 4 direct portal), 5.9 KB of client-report HTML.
  Top: *Central Springs Estate, Lot 1460, Caboolture* — Torsion Homes, $964,227,
  score 91.1, "Recommend", "Verified".
* Full HTTP round trip through the `handler` class on a local `ThreadingHTTPServer`:
  `200 application/json` (which is what `index.html` requires), response keys identical
  to `server.py`'s.
* Ran it out of an assembled fake deployment tree with `VERCEL=1` in **both** layouts of
  §5 — same shortlist, `bundle_violations: NONE`, cold 1.1–1.6 s, warm 70 ms.
* Copied `Book1(Builders) List.csv` into that tree → the request failed with
  `BootstrapError: refusing to run: credential-bearing file(s) were deployed:
  Book1(Builders) List.csv`. Tripwire confirmed.
* `SPB_SNAPSHOT_DB` against the real 4,308-row database produced the **same shortlist**
  as `stock.json`, and left no `-wal`/`-shm` beside it.
* Empty pool (`state: "WA"`, no WA stock): `200`, `shortlist_count 0`,
  `qa_passed false`, identical response keys, **and the pipeline was never called** —
  the case that would otherwise have started a live crawl.
* Called each disarmed source directly: all three raised `ScrapeAttempted`.
* Malformed body → `500 {"status":"error", ...}` as JSON, which `index.html` renders as
  the existing offline note.

Not verified (no deploy performed): that Vercel's static builder really excludes
`api/**` (§5 A) — confirm by requesting `/api/_bootstrap.py` and, in layout B,
`/config.py`. And `GET /api/research` returns
`{"snapshot": "stock.json", "snapshot_rows": 4308, "scrapes": false,
"bundle_violations": []}` — check that first after deploying.
