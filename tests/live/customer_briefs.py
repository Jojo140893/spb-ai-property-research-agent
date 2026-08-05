"""
Every brief a real customer could plausibly submit, fired at the LIVE research API.

Not a smoke test: each scenario asserts the answers are *correct for that customer*,
not merely that the server replied. A shortlist that quietly includes a $1.2m house for
a $650k buyer is a worse failure than an HTTP 500, because nobody notices it.
"""
import json, re, sys, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

API = "https://vercelsite-three-psi.vercel.app/api/research"

BASE = dict(client_name="Test Buyer", budget_max=700000, preferred_spending_cap=680000,
            deposit_amount=100000, finance_status="Pre-approved",
            buyer_type="Owner Occupier", state="QLD", primary_suburbs=[],
            bedrooms_min=4, bathrooms_min=2, car_spaces_min=2, storeys_max=2,
            land_size_min_sqm=350, house_size_min_sqm=150)


def brief(**kw):
    b = dict(BASE)
    b.update(kw)
    return b


# ---------------------------------------------------------------- the customers
SCENARIOS = [
    # ---- ordinary, believable briefs, one per buyer type and state
    ("FHB QLD $650k",          brief(client_name="Priya Nair", buyer_type="First Home Buyer",
                                     state="QLD", budget_max=650000, preferred_spending_cap=630000)),
    ("FHB VIC $550k tight",    brief(buyer_type="First Home Buyer", state="VIC",
                                     budget_max=550000, preferred_spending_cap=540000,
                                     bedrooms_min=3, land_size_min_sqm=300)),
    ("Investor QLD + rent",    brief(buyer_type="Investor", state="QLD", budget_max=720000,
                                     target_rent_weekly=600, target_gross_yield_pct=4.5)),
    ("Investor NSW 25km",      brief(buyer_type="Investor", state="NSW", budget_max=950000,
                                     preferred_spending_cap=900000,
                                     primary_suburbs=["Marsden Park"], search_radius_km=25)),
    ("SMSF WA $600k",          brief(buyer_type="SMSF Buyer", state="WA", budget_max=600000,
                                     preferred_spending_cap=590000)),
    ("Owner-occ SA $520k",     brief(state="SA", budget_max=520000, preferred_spending_cap=500000,
                                     bedrooms_min=3)),
    ("Owner-occ VIC 30km",     brief(state="VIC", budget_max=800000,
                                     primary_suburbs=["Melton"], search_radius_km=30)),
    ("Downsizer 3/2/1 single", brief(bedrooms_min=3, bathrooms_min=2, car_spaces_min=1,
                                     storeys_max=1, land_size_min_sqm=250)),
    ("Big family 5 bed",       brief(bedrooms_min=5, bathrooms_min=3, car_spaces_min=2,
                                     budget_max=1100000, preferred_spending_cap=1050000)),
    ("Premium NSW $2m",        brief(state="NSW", budget_max=2000000,
                                     preferred_spending_cap=1900000, house_size_min_sqm=250)),
    ("Acreage 800sqm land",    brief(land_size_min_sqm=800, budget_max=900000,
                                     preferred_spending_cap=880000)),
    ("Yield chaser 6%",        brief(buyer_type="Investor", target_gross_yield_pct=6.0,
                                     target_rent_weekly=750)),

    # ---- briefs nothing can satisfy: must answer honestly, never crash or invent
    ("Impossible 6bed $300k",  brief(bedrooms_min=6, budget_max=300000,
                                     preferred_spending_cap=300000)),
    ("Impossible 1000sqm hse", brief(house_size_min_sqm=1000)),
    ("Impossible $50k",        brief(budget_max=50000, preferred_spending_cap=50000)),
    ("Impossible 10 bed",      brief(bedrooms_min=10, bathrooms_min=6, car_spaces_min=4)),

    # ---- location input the way people actually type it
    ("No suburb at all",       brief(primary_suburbs=[])),
    ("Misspelled suburb",      brief(state="VIC", primary_suburbs=["Melbrne"], search_radius_km=20)),
    ("Suburb that isn't real", brief(primary_suburbs=["Nowhereville"], search_radius_km=20)),
    ("Suburb wrong state",     brief(state="QLD", primary_suburbs=["Melton"], search_radius_km=25)),
    ("Eight suburbs listed",   brief(state="VIC", primary_suburbs=[
                                     "Tarneit", "Truganina", "Wyndham Vale", "Melton South",
                                     "Rockbank", "Aintree", "Fraser Rise", "Deanside"])),
    ("Suburbs as a string",    brief(state="VIC", primary_suburbs="Tarneit, Truganina")),
    ("Tiny radius 2km",        brief(state="VIC", primary_suburbs=["Tarneit"], search_radius_km=2)),
    ("Huge radius 500km",      brief(state="VIC", primary_suburbs=["Melton"], search_radius_km=500)),
    ("Radius, no suburb",      brief(search_radius_km=30, primary_suburbs=[])),

    # ---- the form filled in wrongly, which customers do constantly
    ("All numbers cleared",    brief(budget_max=None, preferred_spending_cap=None,
                                     bedrooms_min=None, bathrooms_min=None, car_spaces_min=None,
                                     storeys_max=None, land_size_min_sqm=None,
                                     house_size_min_sqm=None, search_radius_km=None)),
    ("All numbers zero",       brief(budget_max=0, preferred_spending_cap=0, bedrooms_min=0,
                                     bathrooms_min=0, car_spaces_min=0, storeys_max=0,
                                     land_size_min_sqm=0, house_size_min_sqm=0)),
    ("Negative numbers",       brief(budget_max=-500000, preferred_spending_cap=-1,
                                     bedrooms_min=-2, land_size_min_sqm=-300)),
    ("Absurdly large numbers", brief(budget_max=1e15, preferred_spending_cap=1e15,
                                     land_size_min_sqm=1e9)),
    ("Text in number fields",  brief(budget_max="abc", bedrooms_min="four",
                                     land_size_min_sqm="lots")),
    ("Numbers as strings",     brief(budget_max="750,000", preferred_spending_cap="$720,000",
                                     bedrooms_min="4")),
    ("Cap above max budget",   brief(budget_max=600000, preferred_spending_cap=900000)),
    ("Cap of zero",            brief(budget_max=700000, preferred_spending_cap=0)),
    ("Blank client name",      brief(client_name="")),
    ("500-char client name",   brief(client_name="Bartholomew " * 42)),
    ("Emoji / unicode name",   brief(client_name="Zoë Müller 🏡 客户")),
    ("Script tag in name",     brief(client_name="<script>alert('x')</script>")),
    ("SQL-ish in suburb",      brief(primary_suburbs=["'; DROP TABLE buildings;--"])),
    ("Unknown buyer type",     brief(buyer_type="Time Traveller")),
    ("Unknown state",          brief(state="ZZ")),
    ("Lowercase state",        brief(state="qld")),
    ("Junk extra fields",      brief(favourite_colour="blue", nested={"a": [1, 2]})),

    # ---- malformed at the envelope level
    ("Empty JSON object",      None),
    ("Brief is null",          "NULL_BRIEF"),
    ("Brief is a string",      "STRING_BRIEF"),
]

JUNK_SUBURB = re.compile(
    r"^\s*(display|home|homes|house|land|lot|package|available|new|sold|coming"
    r"|soon|from|now|stage|release|title|titled|design|facade|inclusions?|std"
    r"|standard|option|upgrade|call|tbc|n/?a|price|deposit|est|estate)\s*$", re.I)
TRACEBACK = re.compile(r"Traceback \(most recent call last\)|File \"/var/task", re.I)


def post(scenario):
    name, b = scenario
    if b is None:
        body = {}
    elif b == "NULL_BRIEF":
        body = {"client_brief": None}
    elif b == "STRING_BRIEF":
        body = {"client_brief": "just a sentence"}
    else:
        body = {"client_brief": b}
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            return name, b, r.status, json.loads(r.read().decode()), None
    except urllib.error.HTTPError as e:
        return name, b, e.code, None, e.read().decode()[:400]
    except Exception as e:
        return name, b, -1, None, f"{type(e).__name__}: {e}"


def audit(name, b, code, data, err):
    """Return a list of customer-visible faults. Empty list = this customer was served well."""
    bad = []
    malformed = name in ("Brief is a string",)
    if code == 400 and malformed:
        return []                       # telling the caller their body was wrong is right
    if code != 200:
        return [f"HTTP {code}: {(err or '')[:180]}"]
    if not isinstance(data, dict):
        return ["response was not a JSON object"]
    if data.get("status") != "success":
        bad.append(f"status={data.get('status')!r}")

    blob = json.dumps(data)
    if TRACEBACK.search(blob):
        bad.append("a Python traceback leaked into the response")

    shortlist = data.get("shortlist") or []
    if not isinstance(data.get("shortlist_count"), int):
        bad.append(f"shortlist_count is {type(data.get('shortlist_count')).__name__}")
    elif data["shortlist_count"] != len(shortlist):
        bad.append(f"count {data['shortlist_count']} != {len(shortlist)} entries")

    # the mandatory criteria this customer actually stated
    def stated(key):
        v = (b or {}).get(key) if isinstance(b, dict) else None
        try:
            v = float(str(v).replace(",", "").replace("$", ""))
        except (TypeError, ValueError):
            return None
        return v if v > 0 and v < 1e12 else None

    cap, beds = stated("budget_max"), stated("bedrooms_min")
    baths, cars = stated("bathrooms_min"), stated("car_spaces_min")
    land, storey = stated("land_size_min_sqm"), stated("storeys_max")

    for p in shortlist:
        who = (p.get("lot_address") or p.get("property_id") or "?")[:34]
        price = p.get("realistic_total_price")
        if cap and isinstance(price, (int, float)) and price > cap:
            bad.append(f"over budget: {who} at ${price:,.0f} > ${cap:,.0f}")
        for label, want, got in (("bed", beds, p.get("bedrooms")),
                                 ("bath", baths, p.get("bathrooms")),
                                 ("car", cars, p.get("car_spaces"))):
            if want and isinstance(got, (int, float)) and got < want:
                bad.append(f"under {label}: {who} has {got}, asked {want:g}")
        if storey and isinstance(p.get("storeys"), (int, float)) and p["storeys"] > storey:
            bad.append(f"too many storeys: {who} {p['storeys']} > {storey:g}")
        if JUNK_SUBURB.match(str(p.get("suburb") or "")):
            bad.append(f"junk suburb shown: {p.get('suburb')!r}")
        if p.get("builder_name") and str(p["builder_name"]).strip().lower() in (
                "unknown", "none", "n/a", "null", "not named"):
            bad.append(f"placeholder builder: {p['builder_name']!r}")

    # the client-facing report must not print zeros or empty brackets
    html = data.get("client_report_html") or ""
    for pat, msg in (
            (r"\$0(?:\.00)?\s*(?:/|per\b|pw\b|a week)", "report shows $0 rent"),
            (r"\b0\.00\s*%", "report shows 0.00% yield"),
            (r"\(\s*\)", "report has empty brackets"),
            (r"market benchmark pending", "report says 'market benchmark pending'"),
            (r"we reviewed the market", "report claims to have reviewed 'the market'"),
            (r"\bNaN\b|\bundefined\b|\bNone\b", "report leaks NaN/undefined/None")):
        if re.search(pat, html, re.I):
            bad.append(msg)
    if html and "disclaimer" not in html.lower():
        bad.append("report has no Disclaimer")

    if data.get("qa_passed") is False and shortlist:
        bad.append(f"qa_failures with a non-empty shortlist: {data.get('qa_failures')}")

    # a radius search around a real suburb has to produce a search area
    if isinstance(b, dict) and b.get("search_radius_km") and b.get("primary_suburbs"):
        if not data.get("search_area") and data.get("shortlist_count"):
            bad.append("radius given, but search_area came back empty")
    return bad


if __name__ == "__main__":
    print(f"{len(SCENARIOS)} customer scenarios against {API}\n" + "=" * 78)
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(post, SCENARIOS))

    faults, rows = {}, []
    for name, b, code, data, err in results:
        bad = audit(name, b, code, data, err)
        n = (data or {}).get("shortlist_count", "-")
        rej = (data or {}).get("rejected_count", "-")
        rows.append((name, code, n, rej, bad))
        if bad:
            faults[name] = bad

    for name, code, n, rej, bad in rows:
        tag = "PASS" if not bad else "FAIL"
        print(f"  [{tag}] {name:24s} http={code} shortlist={n:>4} rejected={rej:>5}")
        for f in bad:
            print(f"         - {f}")

    print("=" * 78)
    print(f"{len(rows)} scenarios, {len(faults)} with faults")
    json.dump({k: v for k, v in faults.items()}, open("api_faults.json", "w"), indent=1)
    sys.exit(0)
