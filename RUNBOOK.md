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
| A lot that sold is still shown, or a lot for sale was retired | `python repair_rows.py --explain <row id>` prints its list (by key and label, never a link), every recorded read of that list with what decided it, how long the lot has been absent, and rule B's count for its link or project. A wrong retirement: `python repair_rows.py --undo-retirements <stamp>` (the stamp is in the log and the `retirements` table) puts the mark and the earlier status back, and the undo holds until the source lists the lot again. A retirement also undoes itself the moment the source lists the lot again |
| A list stuck as a failed read in section 12 of the repair log | Each partial list says what decided it ("collapsed 1 of 29", "veto: proxima short 80/85", "anchor 217/306 accountable missing, no proof"). Fix the read; or, after checking the builder's own list, `python repair_rows.py --accept <list key> --apply` records a person's proof for that night's read (a veto from that night's manifest still wins). A lot needs two whole reads after its last sighting before it retires |
| Section 12 says "dropped N of M since its last whole read" (or "since its first recorded read (none whole)") | The list lost at least max(2, 10%) of its for-sale rows at once, and nothing in tonight's manifest proves the read whole: a cut read and a big sale look the same. It stays partial (what the old rule retires on it still goes) until the lots come back, a proof arrives (Proxima's counter, a tab's export), or a person checks the builder's own list and runs `python repair_rows.py --accept <list key> --apply`. The read it is measured from is never pruned. "two channels agree on N, under 60% of ..." or "... a step of K or more down from M" in the same line: the portal and E-Agent read the same page short (page 1 only, or a page that stopped loading), so their agreement proved nothing; "... but the E-Agent list it leans on is partial tonight" or "... under the M the E-Agent list it leans on has read whole": the E-Agent read of that page is itself cut, so it proves nothing about the portal's |
| A sold lot's copy from the inbox stays hidden although the email was read after the lot retired | By design. A copy hidden behind a retired one comes back only when a re-listing source (E-Agent, Proxima, a builder portal, the Stocklist search, RealNet) read it after the retirement: that read says the lot is listed tonight, and its own list retires it again when it leaves. An email is a snapshot of what its sender held when it was sent, and the nightly re-reads the last 7 days of the inbox, so its read date says nothing about tonight. If the builder really is selling it again, a re-listing source will carry it; until then it stays hidden, as it always did before the ledger |
| Section 9b, rule B "its successor on the page carries under 60% of it" or "a step (max(2, 10%)) of its builder's live rows are on units no longer offered" | A link left the page, but a link that replaced it (its lots were read through it, or it holds the same page, heading and label) carries under 60% of what the old one held; or a link gone before the ledger began belongs to a builder a tenth or more of whose rows are on links no longer offered. The stranded lots are not retired: a re-upload whose read is cut short looks exactly like this. Check the re-upload against the builder's list and fix its read; until then they stay live, as they always did under the old rule |
| Section 9b, rule B "its successor dropped 2 or more of its lots still listed for sale" (or "a file new to its folder dropped 2 or more lots"), with "waits: ... its successor's list(s): KEY" lines | A link (or a file in a clean folder) was re-uploaded and the new one carries most of the old one's lots, but two or more lots that do not say Sold were left behind. A silently cut re-upload looks exactly like this, so they stay live (as on the old rule) until the new list is proven whole (a tab's export) or a person checks the builder's own list and accepts the successor's read: `python repair_rows.py --accept <KEY> --apply`, the key from the "waits:" line. From that night the old link's nights count, and the lots left behind retire 14 counted nights later. One lot left behind, or lots that already say Sold, retire without it |
| Section 12 says "(the old rule holds this list partial)" after "a step of 2 or more" | On a list the old 40% rule gives up on (the ratchet: Hermitage, Tomorrow, AVIA, Silkwood...), the old rule retires nothing, so every retirement there is the ledger's own: two or more lots for sale leaving at once (or one with a row that says gone) waits for a proof or `--accept`, and so does a lot that left on the same read as another, when its fortnight is up. One lot leaving at a time still retires. A tab's export still proves a drop its own rows fell with; an export, an old Proxima counter or the Stocklist search whose unaccounted rows grew by a step says "... does not account for the drop" |
| The E-Agent Stocklist search line in the manifest says `short` with `short_why`, or section 12 shows its list on the anchor | The search proves its read whole only when the site stated how many records it holds (`stated`) and the walk read them all (`records`). A page that failed mid-walk, a server that ignored paging, or a stated total not met is `short` (a veto); a walk with no stated total proves nothing, and the step guard and the anchor judge it |
| Retirements look wrong after the retirement ledger (24 Oct 2026) | `SPB_RETIREMENT_RULE=old` in the daily-harvest workflow `env:` puts back the old rule (supersede clears every mark nightly, repair judges over every stored row) and records nothing; the ledger tables stay. For its first 7 recorded nights the ledger only prints what its new rules "would retire"; the old rule's retirements still go from night 1 |
| A RealNet package is still shown although it is off the read, section 9 says "RealNet: ... kept live" | A RealNet package retires (after the usual 14 days off the read) only on a night whose `data/harvest_manifest.json` line for the folder was read clean and lists the folder's files (`files_listed`, each Drive file id hashed), and its own file is not among them. A package whose file is still in the folder is kept however its download or brochure went ("its file is still in the folder"): a file that will not download, a brochure that does not print the price its name states, a refused row or a name the reader cannot parse drops it out of the read exactly as a sale would. The harvest log's RealNet lines name the file (`fetch failed:` / `zero-yield:` / `no price list:`); ask RealNet to fix it, or to take the file out of the folder if the package is gone. A night with no manifest, no line for the folder, or a listing that failed holds every package. Meanwhile `link_check.py` still takes a deleted file's package off the search |
| A withdrawn builder's lots (Bathla) and retirement | The ledger sets a withdrawn builder's rows (builder_names: by name, project or capture) aside in every list and never retires one: they neither count as missing nor as read, though they still count toward a list's size for the 10-row floor. The old rule's retirement on a list holding them goes ahead only when the ledger calls the list whole without them ("held back ... not whole once a withdrawn builder's rows are set aside" in section 9). `SPB_RETIREMENT_RULE=old` counts them, as before |
| "9b. rule B, Proxima: not counted tonight" every night | Rule B counts a Proxima project gone from the dropdown only on a night every project the reader listed was read: `data/harvest_manifest.json` `runs.Proxima` holds the reader's own counts (`project unreadable`, `project errored`, `off-index project ...`, ERROR records) and the log line names the one that stopped it. A project that cannot be read looks exactly like one that left, so this fails closed; fix the read (or the session) and it counts again. A manifest from before 24 Oct has no `runs` and never counts |
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
| `list_ledger.py` | the retirement ledger: each list's judged reads, every retirement and its evidence, what each source offered (`list_reads`, `retirements`, `source_offers`, `channel_nights`) |
| `drive_input/vendors.csv` | vendor list + credentials (gitignored) |
| `.sessions/`, `assets/`, `*.db` | runtime data (gitignored) |
