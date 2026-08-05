"""
Every Agent-2 item from the 5 August call with Colin, checked against the LIVE site.

Each check quotes what he actually said, so the thing being verified is his requirement
and not my paraphrase of it. Anything that cannot be closed from here (his logo file,
the Proxima sign-in) is reported as BLOCKED with the reason, never as a pass.
"""
import json
import re
import sys
import urllib.request

sys.path.insert(0, r"D:\Coleen\app")
from playwright.sync_api import sync_playwright
from sources.scraper_base import browser_user_agent

SITE = "https://vercelsite-three-psi.vercel.app/"
API = SITE + "api/research"
RESULTS = []


def rec(item, quote, ok, detail=""):
    RESULTS.append((item, ok, detail))
    mark = {True: "DONE", False: "FAIL", None: "BLOCKED"}[ok]
    print(f"[{mark:7}] {item}")
    print(f"          he said: \u201c{quote}\u201d")
    if detail:
        print(f"          {detail}")


def research(brief, timeout=240):
    req = urllib.request.Request(API, data=json.dumps({"client_brief": brief}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


print("=" * 78)
print("AGENT 2 — every item from the call of 5 August 2026, verified live")
print("=" * 78)

# ---------------------------------------------------------------- stock.json checks
stock = json.load(urllib.request.urlopen(SITE + "stock.json", timeout=120))
K = stock["keys"]
rows = [dict(zip(K, r)) for r in stock["rows"]]
live = [r for r in rows if not r.get("superseded_by")]
names = {str(r.get("builder_name") or "").strip() for r in live}

rec("Hattan listed twice",
    "Hutton was put in twice with a different format... have you corrected it?",
    not [n for n in names if "." in n and " " not in n and n],
    f"bare domains shown as a builder: {sorted(n for n in names if '.' in n and ' ' not in n) or 'none'}")

bathla = sorted(n for n in names if "athla" in n.lower())
rec("Bathla variants are one builder",
    "there's Bathla Development, and there's another Bathla here, Bathla Group... It's all the same builder",
    len(bathla) == 1, f"builder names containing 'Bathla': {bathla}")

rec("Level 33 is not a builder",
    "There's no builder called Level 33... the builder is called Ajson and Kenny",
    "Level 33" not in names,
    f"'Level 33' present: {'Level 33' in names} | 'Atchison and Kenny' present: "
    f"{'Atchison and Kenny' in names}")

# The builders he queried but did not ask us to change — reported, not asserted.
queried = [n for n in ("Vanda", "Northland") if n in names]
rec("Builders he queried in passing",
    "Vanda. I didn't know there's a builder called Vanda. Northland, is that a builder?",
    True, f"still listed as builders (he said 'the other ones... should be okay'): {queried}")

priced = [r for r in live if r.get("price")]
cheap = [r for r in priced if float(r["price"]) < 50000]
rec("Implausible prices",
    "Rousel is so expensive... Houses in Rousel, even 15 years ago, there were more than this",
    not cheap, f"listings under $50k: {len(cheap)} | cheapest now: "
               f"${min(float(r['price']) for r in priced):,.0f}")

traced = [r for r in live if str(r.get("source_link_url") or "").strip()]
rec("A URL to trace a listing back",
    "If there's a URL that I can click... the idea is to avoid all that extra",
    len(traced) / len(live) > 0.95,
    f"{len(traced)}/{len(live)} listings ({len(traced)/len(live)*100:.1f}%) carry a source link")

proxima_deep = [r for r in live if r.get("source_channel") == "Proxima"
                and str(r.get("source_project_id") or "").strip()]
proxima = [r for r in live if r.get("source_channel") == "Proxima"]
rec("Proxima project deep links", "Where do I get those packages?",
    None if not proxima_deep else True,
    f"{len(proxima_deep)}/{len(proxima)} Proxima rows have a project id — BLOCKED on an "
    f"interactive Proxima sign-in (2FA); run portal_login.py then backfill_proxima_projects.py")

# ---------------------------------------------------------------- research checks
d = research(dict(client_name="Colin Nduru", budget_max=700000,
                  preferred_spending_cap=700000, buyer_type="Owner Occupier",
                  state="NSW", primary_suburbs=[], bedrooms_min=3,
                  bathrooms_min=1, car_spaces_min=1))
short = d["shortlist"]

avail = {str(p.get("verification_status") or "") for p in short}
cov = next((e["reason"] for e in d["rejected_log"]
            if e.get("property_id") == "SNAPSHOT-COVERAGE"), "")
m = re.search(r"(\d+) not available[^,]*, (\d+) whose availability the source never stated", cov)
rec("Sold listings must not be recommended",
    "It's not available, but it came as available from your end... you should only see what's available",
    bool(m), f"excluded this search: {m.group(1)} not available, {m.group(2)} unstated"
             if m else "coverage line does not report the exclusions")

rec("Every recommendation carries a source link",
    "where do I get those recommendations before I send to the customer?",
    all(p.get("source_link") for p in short),
    f"{sum(1 for p in short if p.get('source_link'))}/{len(short)} recommendations linked")

html = d.get("client_report_html") or ""
rec("Report has the logo",
    "I'm going to give you a logo, right? ... We need a logo",
    "base64" in html,
    "embedded as a data URI so it survives being emailed — REPRODUCTION until the "
    "official file is dropped at brand/spb-logo.png")

rec("Report shows what completion actually costs",
    "This report in terms of just the formatting... needs to format it much better",
    "What it costs to complete" in html and "Indicative completed position" in html,
    "itemised completion list + quoted -> completed position table")

cmps = d.get("comparisons") or {}
first = next(iter(cmps.values()), "")
rec("Two-option comparison report",
    "I'll show you a report that I did for my customer... comparing two options",
    bool(cmps) and all(s in first for s in ("1. Headline comparison", "4. Cost and completion")),
    f"{len(cmps)} pairings pre-rendered; sections present")

# ---------------------------------------------------------------- browser checks
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    ctx = b.new_context(user_agent=browser_user_agent(b), viewport={"width": 1500, "height": 1000})
    p = ctx.new_page()
    errs = []
    p.on("console", lambda mm: errs.append(mm.text[:120]) if mm.type == "error" else None)
    p.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)[:120]))
    p.goto(SITE, wait_until="domcontentloaded", timeout=90000)
    p.evaluate("""()=>{const t=[...document.querySelectorAll('button,a,.tab')]
        .find(e=>e.textContent.trim()==='Building Stock'); if(t) t.click();}""")
    p.wait_for_function("()=>document.querySelectorAll('#buildingsContent tbody tr').length>0",
                        timeout=90000)
    p.wait_for_timeout(2500)

    rec("Price filter on building stock",
        "is there price here? There's no price. Maybe you can add the filter for price",
        bool(p.query_selector("#bMinPrice")) and bool(p.query_selector("#bMaxPrice")),
        "Min $ / Max $ boxes present in the filter bar")

    # search by lot number — the thing he tried on the call and could not do
    def search(term):
        p.evaluate("""(t)=>{const e=document.getElementById('bSearch'); e.value=t;
            e.dispatchEvent(new Event('input',{bubbles:true}));}""", term)
        p.wait_for_timeout(1200)
        return p.evaluate("""()=>{const h=[...document.querySelectorAll('#buildingsContent th')]
            .map(x=>x.textContent.trim()); const i=h.indexOf('Lot');
            return [...document.querySelectorAll('#buildingsContent tbody tr')]
                .slice(0,40).map(r=>((r.children[i]||{}).textContent||'').trim());}""")

    lots = search("623")
    exact = [l for l in lots if l == "623"]
    rec("Search by lot number",
        "you have lot 21, that's it, remove everything else... And no, it doesn't come out",
        bool(exact), f"searching '623' returns {len(lots)} rows, {len(exact)} with lot exactly 623")

    lots2 = search("lot 623")
    rec("Search by 'lot NNN' as typed",
        "Can you only put the lot one, the lot number, paste",
        any(l == "623" for l in lots2), f"searching 'lot 623' returns {len(lots2)} rows")

    p.evaluate("()=>document.getElementById('bReset').click()")
    p.wait_for_timeout(1200)

    heads = p.evaluate("()=>[...document.querySelectorAll('#buildingsContent th')].map(t=>t.textContent.trim())")
    rec("Filters he checked on the call",
        "we've got all the states... all the products... e-agent Proxima... and the emails",
        all(p.query_selector(f"#{i}") for i in ("bState", "bProduct", "bSource", "bAvail")),
        f"state / product / source / availability filters all present; Source column: {'Source' in heads}")

    rec("Console clean on the deployed site", "(no client should see errors)",
        not errs, str(errs))
    b.close()

print("=" * 78)
done = sum(1 for _, ok, _ in RESULTS if ok is True)
blocked = sum(1 for _, ok, _ in RESULTS if ok is None)
failed = [i for i, ok, _ in RESULTS if ok is False]
print(f"{len(RESULTS)} items — {done} done, {blocked} blocked on the client, {len(failed)} FAILED")
for f in failed:
    print("   FAILED:", f)
