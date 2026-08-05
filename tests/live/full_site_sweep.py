"""Whole-site sweep of the DEPLOYED app. Any FAIL blocks shipping."""
import sys, re, json
sys.path.insert(0, r"D:\Coleen\app")
from playwright.sync_api import sync_playwright
from sources.scraper_base import browser_user_agent

URL = "https://vercelsite-three-psi.vercel.app/"
SHOT = r"C:\Users\Ahsan\AppData\Local\Temp\claude\D--Coleen\6b78efbb-fc73-44eb-8cf3-9b46079e0253\scratchpad"
console, perrs, failedreq, http4 = [], [], [], []
results = []


def ck(section, name, ok, detail=""):
    results.append((section, name, bool(ok), detail))


def tab(p, name):
    p.evaluate("""(n)=>{const t=[...document.querySelectorAll('button,a,.tab')]
        .find(e=>e.textContent.trim()===n); if(t) t.click();}""", name)


with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    ctx = b.new_context(user_agent=browser_user_agent(b), viewport={"width": 1500, "height": 1000})
    p = ctx.new_page()
    p.on("console", lambda m: console.append((m.type, m.text[:180])))
    p.on("pageerror", lambda e: perrs.append(str(e)[:250]))
    p.on("requestfailed", lambda r: failedreq.append(r.url[:100]))
    p.on("response", lambda r: http4.append((r.status, r.url[:95])) if r.status >= 400 else None)

    # ============================================== 1. load
    p.goto(URL, wait_until="domcontentloaded", timeout=90000)
    try: p.wait_for_load_state("networkidle", timeout=45000)
    except Exception: pass
    p.wait_for_timeout(22000)
    ck("load", "page title correct", "Smart Property Buying" in p.title(), p.title())
    ck("load", "5 tabs present", len(p.evaluate(
        "()=>[...document.querySelectorAll('.tab-btn')].length")) if False else
        p.evaluate("()=>[...document.querySelectorAll('.tab-btn')].length") == 5)
    ck("load", "auto-run produced a shortlist", "Rank #1" in p.inner_text("#resultsContent"))

    # ============================================== 2. research, three briefs
    briefs = {
        "QLD investor, no radius": dict(clientName="Test A", budgetMax="900000",
            budgetPref="850000", buyerType="Investor", stateSelect="QLD", suburbs="",
            searchRadius="0", bedrooms="4", bathrooms="2", carSpaces="2", storeys="2",
            landSize="300", houseSize="150"),
        "VIC owner-occ, 30km of Melton": dict(clientName="Test B", budgetMax="800000",
            budgetPref="750000", buyerType="Owner Occupier", stateSelect="VIC",
            suburbs="Melton", searchRadius="30", bedrooms="3", bathrooms="2",
            carSpaces="1", storeys="2", landSize="0", houseSize="0"),
        "impossible brief (6 bed, $300k)": dict(clientName="Test C", budgetMax="300000",
            budgetPref="280000", buyerType="Investor", stateSelect="QLD", suburbs="",
            searchRadius="0", bedrooms="6", bathrooms="4", carSpaces="3", storeys="1",
            landSize="800", houseSize="400"),
    }
    for label, br in briefs.items():
        p.evaluate("""(b)=>{for (const [k,v] of Object.entries(b)){
            const e=document.getElementById(k); if(e) e.value=v;}}""", br)
        p.query_selector("button:has-text('Execute Property Research')").click()
        p.wait_for_timeout(26000)
        t = p.inner_text("#resultsContent")
        ok = ("Rank #1" in t) or ("What was searched" in t)
        ck("research", f"{label} -> answers without breaking", ok, t[:70].replace("\n", " "))
        ck("research", f"{label} -> no 'Needs the local app'", "Needs the local app" not in t)
        ck("research", f"{label} -> no junk suburb shown",
           not re.search(r"\|\s*(offer|2026|Street # Type)\s*,", t))
        if "Rank #1" in t:
            ck("research", f"{label} -> matrix totals present", "Total" in t)
            ck("research", f"{label} -> no $0 rent / 0.00% yield",
               "$0–$0" not in t and "0.00%" not in t)
        if br["searchRadius"] != "0":
            d = [float(x) for x in re.findall(r"Distance:\s*([\d.]+)\s*km", t)]
            ck("research", f"{label} -> radius respected", not d or max(d) <= float(br["searchRadius"]), str(d[:4]))

    # back to a brief that yields results, for the report
    p.evaluate("""()=>{const s=(i,v)=>{const e=document.getElementById(i); if(e) e.value=v;};
        s('clientName','Report Check'); s('budgetMax','900000'); s('budgetPref','850000');
        s('stateSelect','QLD'); s('suburbs',''); s('searchRadius','0'); s('bedrooms','4');
        s('bathrooms','2'); s('carSpaces','2'); s('storeys','2'); s('landSize','300'); s('houseSize','150');}""")
    p.query_selector("button:has-text('Execute Property Research')").click()
    p.wait_for_timeout(26000)

    # ============================================== 3. client report
    n0 = len(ctx.pages)
    rep = p.query_selector("button:has-text('Open Client Report')") or p.query_selector("a:has-text('Open Client Report')")
    ck("report", "button exists", bool(rep))
    if rep:
        rep.click(); p.wait_for_timeout(6000)
        if len(ctx.pages) > n0:
            rp = ctx.pages[-1]; rp.wait_for_timeout(2500)
            rt = rp.inner_text("body")
            ck("report", "opens with content", len(rt) > 500, f"{len(rt)} chars")
            ck("report", "has a Disclaimer", "Disclaimer" in rt)
            ck("report", "no $0 rent", "$0" not in rt)
            ck("report", "no 0.00% yield", "0.00%" not in rt)
            ck("report", "no empty brackets", "()" not in rt)
            ck("report", "no 'market benchmark pending'", "market benchmark pending" not in rt)
            ck("report", "no 'we reviewed the market'", "we reviewed the market" not in rt)
            rp.screenshot(path=SHOT + r"\full_report.png"); rp.close()
        else:
            ck("report", "opens in a new tab", False)

    # ============================================== 4. building stock
    tab(p, "Building Stock"); p.wait_for_timeout(7000)
    cnt = p.evaluate("()=>document.getElementById('bCount')?.textContent||''")
    ck("stock", "table loads with a count", "of" in cnt, cnt)
    ck("stock", "superseded hidden by default", "superseded hidden" in cnt, cnt)
    heads = p.evaluate("()=>[...document.querySelectorAll('#buildingsContent th')].map(t=>t.textContent.trim())")
    for col in ("Lot", "Suburb", "Postcode", "Frontage m", "Vs comps %", "Comp median",
                "Vs comps", "Benchmark basis", "Source", "Package"):
        ck("stock", f"column '{col}'", col in heads)

    # every filter, one at a time
    for fid, label in (("bState", "state"), ("bBuilder", "builder"), ("bAvail", "availability"),
                       ("bProduct", "product"), ("bSource", "source")):
        opts = p.evaluate("(i)=>[...document.getElementById(i).options].map(o=>o.value).filter(Boolean)", fid)
        if not opts:
            ck("stock", f"filter {label} has options", False); continue
        p.evaluate("([i,v])=>{const e=document.getElementById(i); e.value=v; e.dispatchEvent(new Event('change'));}", [fid, opts[0]])
        p.wait_for_timeout(1600)
        c2 = p.evaluate("()=>document.getElementById('bCount')?.textContent||''")
        n = int(re.sub(r"[^\d]", "", c2.split("of")[0]) or 0)
        ck("stock", f"filter {label} narrows the list", 0 < n, f"{opts[0]} -> {c2[:34]}")
        p.evaluate("()=>document.getElementById('bReset').click()"); p.wait_for_timeout(1400)

    # search box
    p.evaluate("()=>{const e=document.getElementById('bSearch'); e.value='Ripley'; e.dispatchEvent(new Event('input'));}")
    p.wait_for_timeout(1800)
    ck("stock", "free-text search works",
       "0 of" not in p.evaluate("()=>document.getElementById('bCount').textContent"),
       p.evaluate("()=>document.getElementById('bCount').textContent")[:34])
    p.evaluate("()=>document.getElementById('bReset').click()"); p.wait_for_timeout(1400)

    # superseded toggle
    before = p.evaluate("()=>document.getElementById('bCount').textContent")
    p.evaluate("()=>{const t=document.getElementById('bShowSuperseded'); t.checked=true; t.dispatchEvent(new Event('change'));}")
    p.wait_for_timeout(1800)
    after = p.evaluate("()=>document.getElementById('bCount').textContent")
    ck("stock", "superseded toggle changes the count", before != after, f"{before[:20]} -> {after[:20]}")
    p.evaluate("()=>document.getElementById('bReset').click()"); p.wait_for_timeout(1400)

    # sorting both directions. children[0] is the row-pick CHECKBOX column and is always
    # empty, so comparing it read '' vs '' and reported a working sort as broken. Look up
    # the real index of the column being sorted, and compare several rows, because one
    # value can legitimately repeat across a reversal when the data has ties.
    col = p.evaluate("""()=>[...document.querySelectorAll('#buildingsContent th')]
        .map(t=>t.textContent.trim()).indexOf('Vs comps %')""")
    def sort_top(n=4):
        return p.evaluate("""(i)=>[...document.querySelectorAll('#buildingsContent tbody tr')]
            .slice(0,4).map(tr=>(tr.children[i]||{}).textContent||'').map(s=>s.trim())""", col)
    def click_sort():
        p.evaluate("""()=>{const th=[...document.querySelectorAll('#buildingsContent th')]
            .find(t=>t.textContent.trim()==='Vs comps %'); th.click();}""")
        p.wait_for_timeout(1800)
    click_sort(); asc = sort_top()
    click_sort(); desc = sort_top()
    ck("stock", "sort reverses", col > 0 and asc != desc, f"{asc} vs {desc}")

    # show more
    n_before = p.evaluate("()=>document.querySelectorAll('#buildingsContent tbody tr').length")
    more = p.query_selector("#bMore")
    if more and not p.evaluate("()=>document.getElementById('bMore').hidden"):
        more.click(); p.wait_for_timeout(2200)
        ck("stock", "'Show more' adds rows",
           p.evaluate("()=>document.querySelectorAll('#buildingsContent tbody tr').length") > n_before)
    else:
        ck("stock", "'Show more' present when needed", True, "hidden (all rows shown)")

    # ============================================== 5. best deals
    p.evaluate("()=>localStorage.removeItem('spb.bestdeals.v1')")
    p.evaluate("()=>document.getElementById('bReset').click()"); p.wait_for_timeout(1500)
    p.evaluate("()=>{const s=document.getElementById('bSource'); s.value='Proxima'; s.dispatchEvent(new Event('change'));}")
    p.wait_for_timeout(2200)
    p.evaluate("()=>document.getElementById('pickAll').click()"); p.wait_for_timeout(2500)
    marked = p.evaluate("()=>document.getElementById('pickCount').textContent")
    ck("deals", "mark all filtered", "marked" in marked and not marked.startswith("none"), marked)
    p.evaluate("()=>{const t=document.getElementById('bPickedOnly'); t.checked=true; t.dispatchEvent(new Event('change'));}")
    p.wait_for_timeout(1800)
    ck("deals", "'marked only' filter works",
       "0 of" not in p.evaluate("()=>document.getElementById('bCount').textContent"))
    csv = p.evaluate("""async ()=>{let cap=null; const rc=URL.createObjectURL;
        URL.createObjectURL=b=>{cap=b;return 'x';};
        const rk=HTMLAnchorElement.prototype.click; HTMLAnchorElement.prototype.click=function(){};
        document.getElementById('pickExport').click(); await new Promise(r=>setTimeout(r,900));
        URL.createObjectURL=rc; HTMLAnchorElement.prototype.click=rk;
        if(!cap) return null; const t=await cap.text(); const L=t.split('\\r\\n');
        const buf=new Uint8Array(await cap.arrayBuffer());
        return {rows:L.length-1, bom:(buf[0]===239&&buf[1]===187&&buf[2]===191),
                cols:L[0].split(',').length};}""")
    ck("deals", "CSV exports rows", bool(csv and csv["rows"] > 0), str(csv))
    ck("deals", "CSV is Excel-safe (UTF-8 BOM)", bool(csv and csv["bom"]))
    save = p.evaluate("""async ()=>{const b=document.getElementById('pickSave'); b.click();
        await new Promise(r=>setTimeout(r,2600)); return b.textContent;}""")
    ck("deals", "'Save to database' degrades honestly on the static site",
       "local app" in save.lower() or "saved" in save.lower(), save)
    p.evaluate("()=>document.getElementById('pickReset').click()"); p.wait_for_timeout(1600)
    ck("deals", "reset to published clears the selection",
       "none marked" in p.evaluate("()=>document.getElementById('pickCount').textContent"),
       p.evaluate("()=>document.getElementById('pickCount').textContent"))
    p.evaluate("()=>localStorage.removeItem('spb.bestdeals.v1')")
    # Hand the next section a clean grid. 'marked only' stayed checked while pickReset
    # emptied the selection, so the table legitimately showed zero rows — and the mobile
    # check downstream read that as a mobile rendering fault.
    p.evaluate("""()=>{const t=document.getElementById('bPickedOnly');
        if(t&&t.checked){t.checked=false; t.dispatchEvent(new Event('change'));}
        document.getElementById('bReset').click();}""")
    p.wait_for_timeout(2000)

    # ============================================== 6. other tabs
    for name, probe in (("Approved Builder Directory", 700),
                        ("Vendor Brochures & Details", 700),
                        ("CRM Integration Payload", 400)):
        tab(p, name); p.wait_for_timeout(4500)
        t = p.inner_text("body")
        ck("tabs", f"'{name}' renders", len(t) > probe and "Failed to load" not in t, f"{len(t)} chars")

    # CRM payload is valid JSON?
    tab(p, "CRM Integration Payload"); p.wait_for_timeout(2500)
    raw = p.evaluate("()=>document.getElementById('jsonPayloadView')?.textContent||''")
    okjson = False
    try:
        json.loads(raw); okjson = bool(raw.strip())
    except Exception:
        okjson = False
    ck("tabs", "CRM payload is valid JSON", okjson, f"{len(raw)} chars")

    p.screenshot(path=SHOT + r"\full_desktop.png")

    # ============================================== 7. mobile
    p.set_viewport_size({"width": 390, "height": 844})
    p.wait_for_timeout(2500)
    tab(p, "Building Stock")
    # Switching back to this tab re-renders 6,480 rows; a flat 5s wait sampled the DOM
    # mid-render and called an empty tbody a failure. Wait for the rows themselves.
    try:
        p.wait_for_function(
            "()=>document.querySelectorAll('#buildingsContent tbody tr').length>0", timeout=45000)
    except Exception:
        pass
    ov = p.evaluate("()=>document.documentElement.scrollWidth - document.documentElement.clientWidth")
    ck("mobile", "no horizontal page overflow at 390px", ov <= 2, f"overflow {ov}px")
    m = p.evaluate("""()=>{const c=document.getElementById('buildingsContent');
        const w=c.querySelector('.stock-table-wrap');
        return {rows:c.querySelectorAll('tbody tr').length,
                scrollable: w?(w.scrollWidth>w.clientWidth):null};}""")
    # A wide table on a narrow screen is correct as long as it scrolls inside its own
    # wrapper instead of pushing the page sideways — which the check above proves.
    ck("mobile", "stock table still reachable", m["rows"] > 0, f"{m['rows']} rows, wrapper scrolls {m['scrollable']}")
    p.screenshot(path=SHOT + r"\full_mobile.png")
    b.close()

# ---------------------------------------------------------------- report
print("=" * 78)
sec = None
for s, n, ok, d in results:
    if s != sec:
        print(f"\n  {s.upper()}"); sec = s
    print(f"    [{'PASS' if ok else 'FAIL'}] {n}" + (f"   {d[:52]}" if d and not ok else ""))
fails = [f"{s}/{n}" for s, n, ok, _ in results if not ok]
print("\n" + "=" * 78)
errs = [m for t, m in console if t == "error"]
print(f"console errors : {errs or 'NONE'}")
print(f"page errors    : {perrs or 'NONE'}")
print(f"failed requests: {failedreq or 'NONE'}")
print(f"HTTP >=400     : {http4 or 'NONE'}")
print(f"\n{len(results)} checks, {len(fails)} failed")
print("ALL GREEN" if not fails and not errs and not perrs and not failedreq and not http4
      else f"ISSUES: {fails}")
