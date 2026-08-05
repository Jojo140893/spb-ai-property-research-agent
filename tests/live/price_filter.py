"""The price filter Colin asked for, tested on the live site. Nothing else covered it."""
import re
import sys

sys.path.insert(0, r"D:\Coleen\app")
from playwright.sync_api import sync_playwright
from sources.scraper_base import browser_user_agent

URL = "https://vercelsite-three-psi.vercel.app/"
ok, bad = [], []


def ck(what, cond, detail=""):
    (ok if cond else bad).append(what)
    print(f"  [{'PASS' if cond else 'FAIL'}] {what}" + (f"   {detail}" if detail else ""))


def prices(page):
    """The Package column of every rendered row, as numbers."""
    heads = page.evaluate(
        "()=>[...document.querySelectorAll('#buildingsContent th')].map(t=>t.textContent.trim())")
    idx = heads.index("Package")
    cells = page.evaluate(
        """(i)=>[...document.querySelectorAll('#buildingsContent tbody tr')]
               .map(r=>(r.children[i]||{}).textContent||'')""", idx)
    out = []
    for c in cells:
        digits = re.sub(r"[^\d]", "", c)
        if digits:
            out.append(int(digits))
    return out


def set_box(page, box_id, value):
    page.evaluate("""([i,v])=>{const e=document.getElementById(i); e.value=v;
        e.dispatchEvent(new Event('input',{bubbles:true}));
        e.dispatchEvent(new Event('change',{bubbles:true}));}""", [box_id, str(value)])
    page.wait_for_timeout(1600)


def count(page):
    return page.evaluate("()=>document.getElementById('bCount').textContent")


with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    ctx = b.new_context(user_agent=browser_user_agent(b), viewport={"width": 1500, "height": 1000})
    p = ctx.new_page()
    errs = []
    p.on("console", lambda m: errs.append(m.text[:140]) if m.type == "error" else None)
    p.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)[:140]))

    p.goto(URL, wait_until="domcontentloaded", timeout=90000)
    p.evaluate("""()=>{const t=[...document.querySelectorAll('button,a,.tab')]
        .find(e=>e.textContent.trim()==='Building Stock'); if(t) t.click();}""")
    p.wait_for_function("()=>document.querySelectorAll('#buildingsContent tbody tr').length>0",
                        timeout=90000)
    p.wait_for_timeout(2500)

    ck("the two price boxes exist",
       bool(p.query_selector("#bMinPrice")) and bool(p.query_selector("#bMaxPrice")))

    base_count = count(p)
    base_rows = len(prices(p))
    ck("stock loads before filtering", base_rows > 0, base_count[:44])

    # --- a minimum
    set_box(p, "bMinPrice", 900000)
    lo_prices = prices(p)
    ck("Min $ excludes everything cheaper",
       bool(lo_prices) and min(lo_prices) >= 900000,
       f"{len(lo_prices)} rows, cheapest ${min(lo_prices):,}" if lo_prices else "no rows")
    ck("Min $ actually narrowed the list", count(p) != base_count, count(p)[:44])

    # --- a maximum on top of it (a band)
    set_box(p, "bMaxPrice", 1000000)
    band = prices(p)
    ck("Min + Max gives a band",
       bool(band) and min(band) >= 900000 and max(band) <= 1000000,
       f"{len(band)} rows, ${min(band):,}-${max(band):,}" if band else "no rows")

    # --- clearing the min leaves only the max
    set_box(p, "bMinPrice", "")
    hi_only = prices(p)
    ck("clearing Min leaves the Max bound working",
       bool(hi_only) and max(hi_only) <= 1000000,
       f"{len(hi_only)} rows, dearest ${max(hi_only):,}" if hi_only else "no rows")
    ck("a blank box means no bound, not zero", len(hi_only) >= len(band))

    # --- an impossible band answers honestly rather than breaking
    set_box(p, "bMinPrice", 90000000)
    set_box(p, "bMaxPrice", 99000000)
    ck("an impossible band shows zero rows and says so",
       len(prices(p)) == 0 and "0 of" in count(p), count(p)[:44])

    # --- Reset clears them
    p.evaluate("()=>document.getElementById('bReset').click()")
    p.wait_for_timeout(1800)
    boxes = p.evaluate("""()=>({lo:document.getElementById('bMinPrice').value,
                               hi:document.getElementById('bMaxPrice').value})""")
    ck("Reset clears both price boxes", boxes["lo"] == "" and boxes["hi"] == "", str(boxes))
    ck("Reset restores the full list", count(p) == base_count, count(p)[:44])

    # --- it composes with another filter rather than fighting it
    p.evaluate("""()=>{const s=document.getElementById('bState'); s.value='VIC';
        s.dispatchEvent(new Event('change'));}""")
    p.wait_for_timeout(1400)
    set_box(p, "bMinPrice", 800000)
    both = prices(p)
    states = p.evaluate(
        """()=>{const h=[...document.querySelectorAll('#buildingsContent th')]
                  .map(t=>t.textContent.trim()); const i=h.indexOf('State');
               return [...document.querySelectorAll('#buildingsContent tbody tr')]
                  .map(r=>((r.children[i]||{}).textContent||'').trim());}""")
    ck("price + state compose",
       bool(both) and min(both) >= 800000 and set(states) <= {"VIC"},
       f"{len(both)} rows, cheapest ${min(both):,}, states {sorted(set(states))}"
       if both else "no rows")

    ck("no console or page errors throughout", not errs, str(errs))
    b.close()

print(f"\n{len(ok) + len(bad)} checks, {len(bad)} failed")
