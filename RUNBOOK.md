# SPB AI Property Research Agent — Runbook

Two commands get you all the building stock. No selector mapping, no developer
required.

> **Security:** `drive_input/vendors.csv` and `.env` hold live passwords and are
> gitignored — never commit them. The old committed `Book1(Builders) List.csv`
> still has real passwords in git history; rotate those.

---

## Setup (once)

```bash
python -m venv .venv && .venv\Scripts\activate      # mac/linux: source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python run_tests.py                                  # expect 17/17 PASS
```

Put the vendor list at **`drive_input/vendors.csv`** (the "Agent 2 - Book1(Builders) List" file).

---

## Step 1 — Credentials (pick ONE)

### Option A (recommended): store them in the OS vault, agent runs unattended

```bash
pip install keyring
python setup_credentials.py            # hidden prompts -> Windows Credential Manager
python setup_credentials.py --status   # shows where each credential lives (masked)
```

Then `harvest_buildings.py` authenticates by itself every run — no sign-in, no
typing, works from Task Scheduler/cron. Resolution order at run time is
**OS vault → environment/.env → vendor CSV**, so the vault always wins.

Already have the passwords in the vendor CSV? Move them across in one step:

```bash
python setup_credentials.py --import-csv
```

**Then clear the EMAIL/PASSWORD columns in `drive_input/vendors.csv`** — the vault
copy is authoritative and the CSV copy is plaintext sitting on disk.

### Option B: sign in once in a browser, reuse the session

```bash
python portal_login.py             # a real browser opens; you sign in; session saved
python portal_login.py --verify    # confirm each session is still logged in
```

Use this for portals with SSO/2FA, or if you'd rather no password is stored at all.

## Step 1b — Sign in once per portal (Option B detail)

```bash
python portal_login.py
```

A real browser window opens at each portal. **You sign in** (type it, or let your
password manager fill it), then press Enter in the terminal. The session is saved
to `.sessions/` and reused from then on — no password is stored by the tool or
needed again until the session expires.

```bash
python portal_login.py e_agent      # just one portal
python portal_login.py --status     # which sessions exist
python portal_login.py --verify     # actually check each one is still logged in
```

Portals: **E-Agent** (covers 14 builders), Paramount Living, FRD Homes, Torsion
Homes, Hermitage Homes, Bathla, Proxima.

## Step 2 — Harvest all the stock

```bash
python harvest_buildings.py
```

Logs in using the saved sessions, pulls every listing, and stores it in the
`buildings` table (deduped by builder + lot + suburb + price). Safe to re-run.

```bash
python harvest_buildings.py --eagent-only
python harvest_buildings.py --portals-only
```

## Step 3 — Look at it

```bash
python server.py        # http://localhost:8000
```

- **Building Stock** tab — everything harvested in Step 2
- **Vendor Brochures & Details** tab — brochures + extracted building details
- **Research & Scoring** tab — the client-facing SOP pipeline

---

## Also available

**Brochures from public builder websites** (no login needed):

```bash
python import_and_scrape.py drive_input/vendors.csv
python import_and_scrape.py drive_input/vendors.csv --retry-empty   # only builders with nothing yet
```

Already run once: 56 brochures from WB Home Builders, Celebration Homes, Dale
Alcock, 101 Residential and Novus Homes, each with building details extracted
from the PDF.

**Market comparables** for benchmarking: drop CoreLogic/REA exports at
`drive_input/comparables*.csv` with columns
`suburb,state,bedrooms,price,rent_weekly,land_sqm,source,date_checked`.
Without them, properties are honestly reported as "Unbenchmarked — Pending Market Data".

---

## How listings are read (no selector mapping)

The scrapers first try any hand-mapped selectors in `sources/portal_config.py`.
If none match — which is normal for a portal nobody has mapped — an **adaptive
extractor** (`sources/adaptive_extract.py`) infers the listings from the page
itself: it finds the repeated blocks containing a price and reads address,
suburb, beds/baths/cars, land/house m² and title status out of each one. It is
tested against deliberately different unknown layouts (card grid and plain
table), so new or redesigned portals generally work with no code change.

Each listing carries an `extraction_confidence`. Nothing is ever invented: a
field that can't be found stays empty, and a portal that yields nothing records
nothing.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `no saved session and no credentials` | `python portal_login.py <name>` |
| Harvest returns 0 for one portal | `python portal_login.py --verify` — session likely expired; sign in again |
| Portal blocks the automated browser | Sign in via `portal_login.py` (it uses a real visible browser); the saved session carries the pass |
| Listings look wrong/partial | Check `extraction_confidence`; optionally hand-map that portal in `sources/portal_config.py` and set `verified=True` |
| Torsion is a SharePoint share link | If the stock list is a folder view, point `listings_url` at it in `portal_config.py` |
| Supersede's "status changed on the same line" pass | ON in the daily-harvest workflow since 17 Sep 2026 (list of 102 reviewed that day); OFF in code unless set. Every run (and `python supersede_duplicates.py --dry-run`) prints every retirement the pass WOULD make, QLD first, with the state a blank-state row's kept row and document are filed under. Once that list is signed off, add `SPB_STATUS_PASS: "1"` to the daily-harvest workflow `env:` |
| Lots vanished after supersede's "status changed on the same line" retirements | Remove `SPB_STATUS_PASS` from the daily-harvest workflow `env:` (or set it to `0`) and re-run: `supersede_duplicates.py` goes back to its three original passes |
| Run marked DEGRADED: "a new pass failed; fell back to the previous rule" | A step exited 70 (`DEGRADED_EXIT`): its new pass raised, it logged the traceback and wrote today's behaviour instead. Data still published; fix the traceback in the log |
| Run marked DEGRADED: "7/7  Replay Colin's searches" | `search_regressions.py` replayed Colin's searches against tonight's build and a `[FAIL]` line in the log names the brief and the row (hash and database id). The stock was still published. Reproduce on a copy with `python search_regressions.py --snapshot vercel_site/stock.json --only <brief id>`; trace one row with `python trace_listing.py --hash <v2:prefix> --state QLD --budget 750000`. When the catalogue has legitimately changed (a `[WARN]` says so for the Dawn lots), update `data/colin_searches.json` |
| Run marked DEGRADED: "7/7  Source-to-search parity" | `parity_check.py` searched every state the way Coleen does (whole state, no budget) and the app showed too few of the lots the sources listed in the last 3 nights, showed lots their own list no longer carries, fell over 15% under its 7-night median, or (with `data/harvest_manifest.json`) read a document short. Each `FAIL` line names the state, the reason and the database ids; the Settings tab shows the same table. The stock was still published. Reproduce on a copy with `python parity_check.py --snapshot vercel_site/stock.json --db <copy> --today dd/mm/yyyy --history <scratch>.json --report <scratch>.json`, and trace one lot with `python trace_listing.py --id <id> --state VIC` |
| Parity fails every night on rows only the client or owner can fix | Name them in `data/parity_acknowledged.json` (group, reason or `"bar": "unread"`, the ids or list names, `why`, `until` dd/mm/yyyy): they become notes until that date, then fail again. Never acknowledge without a reason; delete an entry once the data is fixed |
| A list's lots were hidden or sent to "Needs confirming" by the fresh-row guard and E-Agent still shows them | Set `SPB_FRESH_GUARD` (comma-separated `rule=action`, the action one of hide, confirm or off, e.g. `builder_documents_reread=off`) on the Vercel project AND in the daily-harvest workflow `env:` (so the nightly checks search the way the site does), and redeploy; no code change. Defaults: `api/_candidates.py` FRESH_GUARD_ACTIONS. The parity table's "guard hid/confirm" column counts what it kept off the shortlist per state |

**Not harvested by design:** the 9 email-only builders (stock arrives by email)
and Shape Homes (weekly Google Drive PDF).

---

## File map

| Path | What |
|---|---|
| `portal_login.py` | one-time interactive sign-in → saves sessions |
| `harvest_buildings.py` | pulls all stock from E-Agent + portals |
| `import_and_scrape.py` | vendor import + website brochure harvest |
| `sources/adaptive_extract.py` | layout-agnostic listing extractor |
| `sources/portal_config.py` | optional per-portal selector overrides |
| `database.py` | `buildings`, `builder_assets`, `builders` tables |
| `drive_input/vendors.csv` | vendor list + credentials (gitignored) |
| `.sessions/`, `assets/`, `*.db` | runtime data (gitignored) |
