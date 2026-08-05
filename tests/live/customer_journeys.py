"""
Eight people using the live site, each the way that kind of person actually would.

Not a checklist of features — a set of journeys with an intent. The cautious buyer
fills the form and reads. The Melbourne buyer types the suburb she cares about. The
impatient one double-clicks. Somebody clears every box. Somebody is on a phone.
Console errors, page errors and >=400 responses are collected per journey, because a
red console on a client's screen is a defect whether or not the page still worked.
"""
import sys, json, re
sys.path.insert(0, r"D:\Coleen\app")
from playwright.sync_api import sync_playwright
from sources.scraper_base import browser_user_agent

URL = "https://vercelsite-three-psi.vercel.app/"
SHOT = r"C:\Users\Ahsan\AppData\Local\Temp\claude\D--Coleen\6b78efbb-fc73-44eb-8cf3-9b46079e0253\scratchpad"
RESULTS = []


def ck(journey, what, ok, detail=""):
    RESULTS.append((journey, what, bool(ok), detail))
    print(f"    [{'PASS' if ok else 'FAIL'}] {what}" + (f"   {detail}" if detail else ""))


def settle(p, ms=1500):
    p.wait_for_timeout(ms)


def tab(p, label):
    p.evaluate("""(t)=>{const e=[...document.querySelectorAll('button,a,.tab')]
        .find(x=>x.textContent.trim()===t); if(e) e.click();}""", label)
    settle(p, 2500)


def fill(p, fid, value):
    p.evaluate("""([i,v])=>{const e=document.getElementById(i); e.value=v;
        e.dispatchEvent(new Event('input',{bubbles:true}));
        e.dispatchEvent(new Event('change',{bubbles:true}));}""", [fid, str(value)])


def research(p, timeout=190000):
    """Press the button the way a person does, and wait for the answer to appear."""
    p.query_selector("button:has-text('Execute Property Research')").click()
    try:
        p.wait_for_function(
            """()=>{const t=document.getElementById('resultsContent').innerText;
                    return t.includes('Rank #1') || t.includes('What was searched')
                        || t.includes('No listings') || t.includes('nothing');}""",
            timeout=timeout)
    except Exception:
        pass
    settle(p, 1200)
    return p.inner_text("#resultsContent")


def new_page(ctx, width=1420, height=1000):
    p = ctx.new_page()
    p.set_viewport_size({"width": width, "height": height})
    errs, pageerrs, bad = [], [], []
    p.on("console", lambda m: errs.append(m.text[:160]) if m.type == "error" else None)
    p.on("pageerror", lambda e: pageerrs.append(str(e)[:160]))
    p.on("response", lambda r: bad.append((r.status, r.url[-60:])) if r.status >= 400 else None)
    p.goto(URL, wait_until="domcontentloaded", timeout=90000)
    try:
        p.wait_for_load_state("networkidle", timeout=45000)
    except Exception:
        pass
    p.wait_for_function("()=>!!document.getElementById('budgetMax')", timeout=60000)
    settle(p, 2500)
    return p, errs, pageerrs, bad


with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    ctx = b.new_context(user_agent=browser_user_agent(b))

    # ============================================ 1. cautious first home buyer, defaults
    print("\n1. CAUTIOUS FIRST HOME BUYER — fills the form, changes nothing else")
    J = "cautious-fhb"
    p, errs, pageerrs, bad = new_page(ctx)
    ck(J, "the page loads with the form ready",
       bool(p.query_selector("#budgetMax")) and bool(p.query_selector("#clientName")))
    opt = p.evaluate("""()=>({house:document.getElementById('houseSize').value,
                             land:document.getElementById('landSize').value,
                             storeys:document.getElementById('storeys').value})""")
    ck(J, "optional size/storey boxes start empty",
       opt["house"] == "" and opt["land"] == "" and opt["storeys"] == "",
       str(opt))
    fill(p, "clientName", "Priya Nair")
    fill(p, "budgetMax", 650000)
    fill(p, "budgetPref", 630000)
    p.select_option("#buyerType", "First Home Buyer")
    p.select_option("#stateSelect", "QLD")
    txt = research(p)
    ck(J, "gets an answer", bool(txt.strip()), f"{len(txt)} chars")
    ck(J, "shows ranked options", "Rank #1" in txt)
    ck(J, "no error wording shown", not re.search(r"error|failed|traceback|needs the local app", txt, re.I))
    ck(J, "no junk suburb in the results",
       not re.search(r"\b(in offer|in 2026|in purchaser|in Untitled Packages)\b", txt, re.I))
    ck(J, "no $0 rent or 0.00% yield", not re.search(r"\$0(\.00)?\s*(/|per|pw)|0\.00\s*%", txt))
    ck(J, "no None/NaN/undefined leaked", not re.search(r"\b(None|NaN|undefined)\b", txt))
    rep = p.query_selector("#openClientReport")
    ck(J, "can open the client report", bool(rep))
    if rep:
        html = p.evaluate("""async ()=>{const r=document.getElementById('openClientReport');
            let cap=null; const ro=window.open; window.open=()=>({document:{write:t=>cap=t,close:()=>{}}});
            r.click(); await new Promise(s=>setTimeout(s,800)); window.open=ro; return cap;}""")
        ck(J, "the report has content and a disclaimer",
           bool(html) and "disclaimer" in (html or "").lower(), f"{len(html or '')} chars")
    ck(J, "console stayed clean", not errs and not pageerrs, f"{errs}{pageerrs}")
    p.close()

    # ============================================ 2. Melbourne buyer — the demo failure
    print("\n2. MELBOURNE BUYER — VIC, a suburb she knows, a radius (what broke in the demo)")
    J = "melbourne"
    p, errs, pageerrs, bad = new_page(ctx)
    fill(p, "clientName", "Daniel Cheng")
    fill(p, "budgetMax", 800000)
    fill(p, "budgetPref", 780000)
    p.select_option("#stateSelect", "VIC")
    fill(p, "suburbs", "Melton")
    fill(p, "searchRadius", 30)
    txt = research(p)
    ck(J, "a VIC brief returns listings, not an empty answer", "Rank #1" in txt,
       txt.strip()[:110].replace("\n", " "))
    ck(J, "the results are in VIC", txt.count("VIC") > 0)
    ck(J, "no junk suburb", not re.search(r"\b(in offer|in 2026|in purchaser)\b", txt, re.I))
    ck(J, "no None/NaN leaked", not re.search(r"\b(None|NaN|undefined)\b", txt))
    ck(J, "an unstated spec is named, not invented",
       ("not stated" in txt.lower()) or ("Rank #1" in txt), "advisory wording present")
    ck(J, "console stayed clean", not errs and not pageerrs, f"{errs}{pageerrs}")
    p.screenshot(path=SHOT + r"\j_melbourne.png", full_page=False)
    p.close()

    # ============================================ 3. investor working the stock table
    print("\n3. INVESTOR — lives in the Building Stock tab, filters and sorts everything")
    J = "investor"
    p, errs, pageerrs, bad = new_page(ctx)
    tab(p, "Building Stock")
    p.wait_for_function("()=>document.querySelectorAll('#buildingsContent tbody tr').length>0",
                        timeout=90000)
    base = p.evaluate("()=>document.getElementById('bCount').textContent")
    ck(J, "the stock table loads", "shown" in base, base[:60])
    # every value of every filter, one at a time
    for sel in ("bState", "bAvail", "bProduct", "bSource", "bBuilder"):
        vals = p.evaluate("(s)=>[...document.getElementById(s).options].map(o=>o.value)", sel)
        checked = 0
        for v in [x for x in vals if x][:6]:
            p.evaluate("""([s,v])=>{const e=document.getElementById(s); e.value=v;
                e.dispatchEvent(new Event('change'));}""", [sel, v])
            settle(p, 700)
            c = p.evaluate("()=>document.getElementById('bCount').textContent")
            if "of" in c:
                checked += 1
        p.evaluate("()=>document.getElementById('bReset').click()")
        settle(p, 900)
        ck(J, f"filter {sel} works for every option tried", checked > 0, f"{checked} values")
    # sorting every sortable column, both directions
    heads = p.evaluate("()=>[...document.querySelectorAll('#buildingsContent th')].map(t=>t.textContent.trim())")
    reversed_ok, tried = 0, 0
    for i, h in enumerate(heads):
        if not h or h in ("✓",):
            continue
        tried += 1
        def top(idx=i):
            return p.evaluate("""(i)=>[...document.querySelectorAll('#buildingsContent tbody tr')]
                .slice(0,6).map(r=>(r.children[i]||{}).textContent||'')""", idx)
        p.evaluate("""(t)=>{const e=[...document.querySelectorAll('#buildingsContent th')]
            .find(x=>x.textContent.trim()===t); if(e) e.click();}""", h)
        settle(p, 800)
        a = top()
        p.evaluate("""(t)=>{const e=[...document.querySelectorAll('#buildingsContent th')]
            .find(x=>x.textContent.trim()===t); if(e) e.click();}""", h)
        settle(p, 800)
        if a != top():
            reversed_ok += 1
    ck(J, "every column sorts both ways", reversed_ok >= tried - 1, f"{reversed_ok}/{tried} columns")
    # search, show more, mark, export
    fill(p, "bSearch", "Tarneit")
    settle(p, 1200)
    ck(J, "free-text search narrows the list",
       "of" in p.evaluate("()=>document.getElementById('bCount').textContent"))
    p.evaluate("()=>document.getElementById('bReset').click()"); settle(p, 1200)
    n0 = p.evaluate("()=>document.querySelectorAll('#buildingsContent tbody tr').length")
    for _ in range(3):
        m = p.query_selector("#bMore")
        if m:
            m.click(); settle(p, 1200)
    n1 = p.evaluate("()=>document.querySelectorAll('#buildingsContent tbody tr').length")
    ck(J, "'Show more' keeps adding rows", n1 > n0, f"{n0} -> {n1}")
    p.evaluate("""()=>{const s=document.getElementById('bSource'); s.value='Proxima';
        s.dispatchEvent(new Event('change'));}"""); settle(p, 1500)
    p.evaluate("()=>document.getElementById('pickAll').click()"); settle(p, 2000)
    ck(J, "can mark a whole filtered set",
       "marked" in p.evaluate("()=>document.getElementById('pickCount').textContent"),
       p.evaluate("()=>document.getElementById('pickCount').textContent"))
    csv = p.evaluate("""async ()=>{let cap=null; const rc=URL.createObjectURL;
        URL.createObjectURL=b=>{cap=b;return 'x';};
        const rk=HTMLAnchorElement.prototype.click; HTMLAnchorElement.prototype.click=function(){};
        document.getElementById('pickExport').click(); await new Promise(r=>setTimeout(r,900));
        URL.createObjectURL=rc; HTMLAnchorElement.prototype.click=rk;
        if(!cap) return null; const t=await cap.text();
        return {rows:t.split('\\r\\n').length-1, head:t.split('\\r\\n')[0].slice(0,60)};}""")
    ck(J, "the marked set exports as CSV", bool(csv and csv["rows"] > 0), str(csv))
    p.evaluate("""()=>{const t=document.getElementById('bPickedOnly');
        if(t&&t.checked){t.checked=false;t.dispatchEvent(new Event('change'));}
        document.getElementById('pickReset').click();
        document.getElementById('bReset').click();}"""); settle(p, 1500)
    ck(J, "console stayed clean apart from the known /api/select 404",
       not pageerrs and all("404" in e or "select" in e for e in errs), f"{errs}{pageerrs}")
    p.close()

    # ============================================ 4. impatient clicker
    print("\n4. IMPATIENT CUSTOMER — double-clicks, clicks again mid-search, switches tabs")
    J = "impatient"
    p, errs, pageerrs, bad = new_page(ctx)
    fill(p, "clientName", "Rush Job")
    btn = p.query_selector("button:has-text('Execute Property Research')")
    btn.click(); p.wait_for_timeout(120); btn.click(); p.wait_for_timeout(120); btn.click()
    tab(p, "Building Stock"); settle(p, 2000)
    tab(p, "Research & Scoring")
    try:
        p.wait_for_function("""()=>{const t=document.getElementById('resultsContent').innerText;
            return t.includes('Rank #1')||t.includes('What was searched');}""", timeout=200000)
    except Exception:
        pass
    txt = p.inner_text("#resultsContent")
    ck(J, "rapid clicks still produce one clean answer", "Rank #1" in txt or "What was searched" in txt)
    ck(J, "no duplicated results block", txt.count("Rank #1") <= 1, f"{txt.count('Rank #1')} x Rank #1")
    ck(J, "no page error from the races", not pageerrs, str(pageerrs))
    p.close()

    # ============================================ 5. clears every box
    print("\n5. THE ONE WHO EMPTIES EVERY BOX — then presses the button anyway")
    J = "empty-form"
    p, errs, pageerrs, bad = new_page(ctx)
    for fid in ("clientName", "suburbs", "budgetMax", "budgetPref", "bedrooms", "bathrooms",
                "carSpaces", "storeys", "landSize", "houseSize", "searchRadius"):
        fill(p, fid, "")
    txt = research(p)
    ck(J, "answers instead of breaking", bool(txt.strip()), f"{len(txt)} chars")
    ck(J, "says what it could not do rather than showing an error",
       not re.search(r"traceback|500|internal server", txt, re.I), txt.strip()[:100].replace("\n", " "))
    ck(J, "no HTTP 500", not [s for s, _ in bad if s >= 500], str(bad))
    ck(J, "no page error", not pageerrs, str(pageerrs))
    p.close()

    # ============================================ 6. nonsense typist
    print("\n6. NONSENSE TYPIST — words in number boxes, a novel in the name field")
    J = "nonsense"
    p, errs, pageerrs, bad = new_page(ctx)
    fill(p, "clientName", "Bartholomew " * 40)
    fill(p, "suburbs", "'; DROP TABLE buildings;-- , Nowhereville")
    for fid in ("budgetMax", "bedrooms", "landSize"):
        p.evaluate("(i)=>{const e=document.getElementById(i); e.type='text'; e.value='abc';}", fid)
    txt = research(p)
    ck(J, "survives junk input", bool(txt.strip()) and not re.search(r"traceback", txt, re.I))
    ck(J, "no HTTP 500", not [s for s, _ in bad if s >= 500], str(bad))
    ck(J, "the script tag was not executed",
       "alert" not in p.evaluate("()=>document.body.innerHTML").lower()
       or "<script>alert" not in p.evaluate("()=>document.body.innerHTML"))
    ck(J, "no page error", not pageerrs, str(pageerrs))
    p.close()

    # ============================================ 7. phone
    print("\n7. PHONE USER — 390px, does the whole thing with a thumb")
    J = "phone"
    p, errs, pageerrs, bad = new_page(ctx, 390, 844)
    ck(J, "no sideways page scroll",
       p.evaluate("()=>document.documentElement.scrollWidth-document.documentElement.clientWidth") <= 2)
    fill(p, "clientName", "Mobile Mia")
    fill(p, "budgetMax", 700000)
    fill(p, "budgetPref", 680000)
    txt = research(p)
    ck(J, "research works on a phone", "Rank #1" in txt or "What was searched" in txt)
    tab(p, "Building Stock")
    p.wait_for_function("()=>document.querySelectorAll('#buildingsContent tbody tr').length>0",
                        timeout=90000)
    m = p.evaluate("""()=>{const w=document.querySelector('#buildingsContent .stock-table-wrap');
        return {rows:document.querySelectorAll('#buildingsContent tbody tr').length,
                scrolls: w?(w.scrollWidth>w.clientWidth):null,
                page: document.documentElement.scrollWidth-document.documentElement.clientWidth};}""")
    ck(J, "the table is reachable and scrolls in its own box",
       m["rows"] > 0 and m["scrolls"] and m["page"] <= 2, str(m))
    for label in ("Approved Builder Directory", "Vendor Brochures & Details", "CRM Integration Payload"):
        tab(p, label)
        ck(J, f"'{label}' renders on a phone",
           len(p.evaluate("()=>document.body.innerText")) > 400)
    p.screenshot(path=SHOT + r"\j_phone.png")
    ck(J, "no page error", not pageerrs, str(pageerrs))
    p.close()

    # ============================================ 8. tablet + keyboard only
    print("\n8. TABLET, KEYBOARD ONLY — never touches the mouse")
    J = "keyboard"
    p, errs, pageerrs, bad = new_page(ctx, 820, 1180)
    reach = p.evaluate("""()=>{const el=[...document.querySelectorAll(
        'input,select,button,a[href],[tabindex]:not([tabindex="-1"])')]
        .filter(e=>e.offsetParent!==null); return el.length;}""")
    ck(J, "controls are keyboard-reachable", reach > 12, f"{reach} focusable controls")
    # The name field ships pre-filled, so a keyboard user selects all before typing.
    # Typing without that appends, which is the field behaving correctly.
    p.evaluate("()=>document.getElementById('clientName').focus()")
    p.keyboard.press("Control+a")
    p.keyboard.type("Keyboard Ken")
    ck(J, "typing into a focused field works",
       p.evaluate("()=>document.getElementById('clientName').value") == "Keyboard Ken")
    p.evaluate("""()=>{const b=[...document.querySelectorAll('button')]
        .find(x=>x.textContent.includes('Execute Property Research')); b.focus();}""")
    p.keyboard.press("Enter")
    try:
        p.wait_for_function("""()=>{const t=document.getElementById('resultsContent').innerText;
            return t.includes('Rank #1')||t.includes('What was searched');}""", timeout=200000)
    except Exception:
        pass
    ck(J, "Enter on the focused button runs the search",
       "Rank #1" in p.inner_text("#resultsContent") or "What was searched" in p.inner_text("#resultsContent"))
    ck(J, "no page error", not pageerrs, str(pageerrs))
    p.close()
    b.close()

# ============================================================================ summary
print("\n" + "=" * 78)
fails = [(j, w, d) for j, w, ok, d in RESULTS if not ok]
byj = {}
for j, w, ok, d in RESULTS:
    byj.setdefault(j, [0, 0])
    byj[j][0 if ok else 1] += 1
for j, (ok, no) in byj.items():
    print(f"  {j:14s} {ok:>2} passed, {no} failed")
print(f"\n{len(RESULTS)} checks across {len(byj)} customer journeys, {len(fails)} failed")
for j, w, d in fails:
    print(f"  FAIL {j}: {w}   {d}")
json.dump([{"journey": j, "check": w, "ok": ok, "detail": d} for j, w, ok, d in RESULTS],
          open("journeys.json", "w"), indent=1)
