# SPB AI Property Research Agent — Operator Runbook (for Jojo)

How to run the data harvest and finish the one-time selector confirmation so the
credentialed sources (E-Agent + builder portals) actually return listings.

> **Security note:** the vendor CSV and `.env` contain live passwords. They are
> gitignored — never commit them. The committed `Book1(Builders) List.csv` still
> holds old credentials from earlier; those should be rotated.

---

## 1. One-time setup

```bash
# from the repo root
python -m venv .venv && .venv\Scripts\activate      # Windows (use source .venv/bin/activate on mac/linux)
pip install -r requirements.txt
python -m playwright install chromium
```

Put the current vendor list here (gitignored):

```
drive_input/vendors.csv
```

That's the "Agent 2 - Book1(Builders) List" CSV. The app reads builder
credentials from it automatically (E-Agent login + the direct-portal logins).

Optional `.env` (overrides the CSV if you prefer env creds):

```
E_AGENT_USERNAME=...
E_AGENT_PASSWORD=...
SCRAPER_HEADLESS=False        # watch the browser while confirming selectors
SCRAPER_NAV_TIMEOUT_MS=55000
```

Sanity check everything is wired:

```bash
python run_tests.py          # expect 16/16 PASS
```

---

## 2. What harvests what

| Command | Source | Into |
|---|---|---|
| `python harvest_buildings.py` | E-Agent (1 login → 14 builders) + 6 direct portals (Paramount, FRD, Torsion, Hermitage, Bathla, Proxima) | `buildings` table |
| `python import_and_scrape.py drive_input/vendors.csv` | Public builder websites (brochures/fliers/booklets PDFs) | `builder_assets` table + `assets/<builder>/` |

**Skipped by design:** the 9 email-only builders (stock arrives by email) and
Shape Homes (weekly Drive PDF).

View results in the dashboard:

```bash
python server.py             # http://localhost:8000  → "Building Stock" and "Vendor Brochures & Details" tabs
```

---

## 3. Run the building-stock harvest

```bash
python harvest_buildings.py                 # E-Agent + all portals
python harvest_buildings.py --eagent-only   # just E-Agent
python harvest_buildings.py --portals-only  # just the 6 portals
```

It logs in with the CSV credentials, pulls the full stock list, and stores each
listing (deduped) in the `buildings` table. Re-running is safe — duplicates are
ignored by a builder+lot+suburb+price key.

**Expected on the very first run:** some sources will print

```
[E-Agent] no listing cards matched '<selector>' — selectors need re-mapping
```

That means login worked but the code doesn't yet know which HTML elements hold
each listing on the logged-in page. Fix that once per source (Section 4).

---

## 4. One-time selector confirmation (the only manual step)

The login selectors are confirmed; the **listing-card** selectors are best-effort
placeholders flagged `verified=False` in `sources/portal_config.py`. Map them
against the real logged-in page:

1. Run with a visible browser so you can watch:
   ```bash
   set SCRAPER_HEADLESS=False   &&   python harvest_buildings.py --eagent-only
   ```
2. When the stock list is on screen, open DevTools (F12) and inspect one listing
   "card" (the repeated block for a single house/lot). Note:
   - the selector that matches **one listing** (→ `listing_card_selector`)
   - within a card, the elements holding **title/address, price, beds, baths,
     cars, suburb** (→ `field_selectors`)
   - the `listings_url` (the exact stock-list page URL after login)
   - a `logged_in_selector` — any element only present when logged in (e.g. a
     "Log Out" link) so the saved session is detected on later runs.
3. Edit that source's entry in `sources/portal_config.py`, fill the selectors,
   and set `verified=True`.
4. Re-run `python harvest_buildings.py` — listings should now populate.

**If you'd rather hand it back to me to map:** just save the logged-in stock-list
page and send it over —
`right-click → Save As → "Webpage, Complete"` (or DevTools → Elements → copy
`outerHTML`) — drop the `.html` into `drive_input/` and I'll write the exact
selectors for that portal. One file per portal is enough.

Portals to confirm (each has a stub in `portal_config.py`):
E-Agent, Paramount Living, FRD Homes, Torsion Homes, Hermitage Homes, Bathla, Proxima.

---

## 5. Notes & gotchas

- **Sessions are cached** in `.sessions/` (gitignored) so you won't re-login every
  run. Delete a session file to force a fresh login.
- **Bot protection:** if a portal blocks headless Chromium, run with
  `SCRAPER_HEADLESS=False` once to pass the challenge; the saved session carries
  the pass forward.
- **Torsion** is a SharePoint stock-list share link (no login) — it may just need
  the `listings_url` pointed at the shared folder and the row selector confirmed.
- **Public-website brochure harvest** already ran once: 56 brochures from 5
  builders (WB Home Builders, Celebration Homes, Dale Alcock, 101 Residential,
  Novus Homes), each with building details extracted. Re-run
  `import_and_scrape.py` any time to refresh; dedup by file hash prevents repeats.
- **Nothing is fabricated:** a source that returns nothing stores nothing.

---

## 6. Where things live

| Path | What |
|---|---|
| `harvest_buildings.py` | building-stock harvester (E-Agent + portals) |
| `import_and_scrape.py` | website brochure harvester |
| `sources/portal_config.py` | **per-portal selectors — the file you edit in Section 4** |
| `sources/e_agent.py`, `sources/builder_portals.py`, `sources/website_scraper.py` | the scrapers |
| `database.py` | `buildings`, `builder_assets`, `builders` tables |
| `drive_input/vendors.csv` | vendor list + credentials (gitignored) |
| `assets/<builder>/` | downloaded brochures (gitignored) |
| `spb_research_audit.db` | SQLite DB (gitignored) |

Questions on any portal's DOM → send me the saved HTML and I'll map it.
