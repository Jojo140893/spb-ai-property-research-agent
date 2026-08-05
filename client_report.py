"""
Client-Facing Report Generator (2026-07-22 client requirement).
Produces the report Coleen presents to her buyers: the top 3 recommended
properties with purchase price vs average market price, rent and yield data,
and the reasons behind each recommendation.

Output: a single self-contained, print-ready HTML file (no external assets).
"""

from datetime import datetime
from typing import List
from schema import ClientBrief, CandidateProperty


def _money(v) -> str:
    return f"${v:,.0f}" if v is not None else "TBC"


class ClientReportGenerator:
    @staticmethod
    def generate_html(brief: ClientBrief, shortlist: List[CandidateProperty],
                      date_str: str = "") -> str:
        date_str = date_str or datetime.now().strftime("%d/%m/%Y")
        top3 = shortlist[:3]

        cards = []
        for rank, p in enumerate(top3, 1):
            pb = p.price_breakdown
            bm = getattr(p, 'benchmark', None) or {}
            avg_market = bm.get('avg_market_price')
            saving = (avg_market - pb.realistic_total_price) if avg_market else None
            rent_mid = (p.estimated_rent_weekly_min + p.estimated_rent_weekly_max) / 2.0
            gross_yield = (rent_mid * 52 / pb.realistic_total_price * 100.0) if pb.realistic_total_price else 0

            # An internal comparison is against other stock we hold, NOT the market,
            # and this document goes to a buyer — so it is never called a market
            # price. Saying "comparable listings" is both true and still useful.
            internal = bm.get('basis') == 'internal'
            what = "comparable listings" if internal else "average market price"
            if saving is not None and saving > 0:
                value_line = (f"<span class='good'>{_money(pb.realistic_total_price)}</span> vs "
                              f"{what} {_money(avg_market)} &mdash; a saving of "
                              f"<strong>{_money(saving)}</strong>")
            elif avg_market:
                value_line = (f"{_money(pb.realistic_total_price)} vs {what} "
                              f"{_money(avg_market)} ({bm.get('classification', '')})")
            else:
                value_line = f"{_money(pb.realistic_total_price)}"

            comp_rows = "".join(
                f"<tr><td>{c['suburb']}, {c['state']}</td><td>{c['bedrooms']} bed</td><td>{_money(c['price'])}</td><td>{c['source']}</td></tr>"
                for c in bm.get('comparables', [])
            )
            comp_table = (
                f"<table class='comps'><tr><th>Comparable</th><th>Beds</th><th>Price</th><th>Source</th></tr>{comp_rows}</table>"
                if comp_rows else (
                    f"<p class='pending'>{bm.get('data_note') or ''}</p>" if internal
                    else "<p class='pending'>Market comparables to be confirmed.</p>")
            )

            # A row with nothing in it is worse than no row. Stored stock carries no
            # rent figure, so this document was telling an INVESTOR "Estimated rent
            # $0-$0 / week" and "Estimated gross yield 0.00%" — which does not read as
            # "not captured", it reads as "this earns nothing". Omit instead.
            rent_row = ""
            if p.estimated_rent_weekly_min or p.estimated_rent_weekly_max:
                rent_row = ('<div class="row"><span class="label">Estimated rent</span>'
                            f'<span>{_money(p.estimated_rent_weekly_min)}&ndash;'
                            f'{_money(p.estimated_rent_weekly_max)} / week</span></div>')
            yield_row = ""
            if gross_yield:
                yield_row = ('<div class="row"><span class="label">Estimated gross yield</span>'
                             f'<span>{gross_yield:.2f}%</span></div>')

            # "August 2026 ()" — an empty bracket where no date was captured.
            title_text = str(p.title_status or "Not stated in source").strip()
            if str(p.expected_title_date or "").strip():
                title_text += f" ({p.expected_title_date})"

            # Only print the location note when it says something. "No amenity
            # assessment captured for this listing." under a heading called Location
            # reads, to a buyer, as though the area is a problem.
            amenity = str(p.amenities_summary or "").strip()
            amenity_line = (f"<p><em>Location:</em> {amenity}</p>"
                            if amenity and not amenity.lower().startswith("no amenity")
                            else "")

            dist = getattr(p, 'distance_km_from_target', None)
            dist_line = f" &middot; {dist} km from {brief.primary_suburbs[0]}" if dist not in (None, 0.0) and brief.primary_suburbs else ""

            risk_items = "".join(f"<li>{r.risk_name} ({r.rating.value}): {r.proposed_mitigation}</li>" for r in p.risks)
            risk_block = f"<ul class='risks'>{risk_items}</ul>" if risk_items else "<p>No medium or high risks identified at time of checking.</p>"

            # A spec the stocklist never stated is omitted from this line, not printed as
            # "None bed" and not formatted at all — f"{None:,.0f}" raises. The client sees
            # the facts that were verified; the gaps are named in the advisories instead.
            spec = " &middot; ".join(bit for bit in (
                f"{p.bedrooms} bed" if p.bedrooms is not None else "",
                f"{p.bathrooms} bath" if p.bathrooms is not None else "",
                f"{p.car_spaces} car" if p.car_spaces is not None else "",
                f"{p.land_size_sqm:,.0f} m&sup2; land" if p.land_size_sqm else "",
                f"{p.house_size_sqm:,.0f} m&sup2; house" if p.house_size_sqm else "",
                p.builder_name or "",
            ) if bit)

            cards.append(f"""
    <div class="card">
      <div class="rank">Option {rank}</div>
      <h2>{p.lot_address}, {p.suburb} {p.state}</h2>
      <p class="sub">{spec}{dist_line}</p>
      <div class="row"><span class="label">Your price</span><span>{value_line}</span></div>
      {rent_row}
      {yield_row}
      <div class="row"><span class="label">Turnkey status</span><span>{pb.turnkey_status.value}{' &mdash; allow ' + _money(pb.estimated_additional_costs) + ' additional costs' if pb.estimated_additional_costs else ''}</span></div>
      <div class="row"><span class="label">Title</span><span>{title_text}</span></div>
      <h3>Why we recommend it</h3>
      <p>{p.recommendation_reason}</p>
      {amenity_line}
      <h3>{'Price comparison' if internal else 'Market comparison'}</h3>
      {comp_table}
      <h3>Things to be aware of</h3>
      {risk_block}
    </div>""")

        return f"""<!-- SPB client report generated {date_str} -->
<style>
  body {{ font-family: Georgia, 'Times New Roman', serif; color: #1a1a2e; max-width: 820px; margin: 2rem auto; padding: 0 1rem; }}
  header {{ display: flex; align-items: center; gap: 1.1rem; border-bottom: 3px solid #1450c8;
             padding-bottom: 1rem; margin-bottom: 1.5rem; }}
  header img.logo {{ width: 74px; height: 74px; border-radius: 10px; flex: 0 0 auto; }}
  header .titles {{ flex: 1 1 auto; }}
  h1 {{ font-size: 1.6rem; margin: 0; color: #1450c8; }} h2 {{ font-size: 1.15rem; margin: 0 0 .25rem; }}
  h3 {{ font-size: .95rem; margin: 1rem 0 .35rem; text-transform: uppercase; letter-spacing: .05em; color: #444; }}
  .meta {{ color: #555; font-size: .9rem; }}
  .card {{ border: 1px solid #ccc; border-radius: 6px; padding: 1.25rem 1.5rem; margin-bottom: 1.5rem; page-break-inside: avoid; }}
  .rank {{ font-size: .8rem; font-weight: bold; letter-spacing: .1em; text-transform: uppercase; color: #8a6d3b; }}
  .sub {{ color: #555; font-size: .9rem; margin-top: 0; }}
  .row {{ display: flex; gap: 1rem; padding: .3rem 0; border-bottom: 1px dotted #ddd; font-size: .95rem; }}
  .label {{ min-width: 180px; font-weight: bold; color: #333; }}
  .good {{ color: #1e7a34; font-weight: bold; }}
  .pending {{ color: #8a6d3b; font-style: italic; }}
  table.comps {{ border-collapse: collapse; width: 100%; font-size: .9rem; }}
  table.comps th, table.comps td {{ border: 1px solid #ddd; padding: .35rem .5rem; text-align: left; }}
  ul.risks {{ margin: .25rem 0; padding-left: 1.25rem; }}
  footer {{ font-size: .8rem; color: #666; border-top: 1px solid #ccc; margin-top: 2rem; padding-top: 1rem; }}
</style>
<header>
  {_logo_html()}
  <div class="titles">
    <h1>Property Recommendations</h1>
    <p class="meta">Prepared for <strong>{brief.client_name}</strong> &middot; {date_str} &middot; Budget up to {_money(brief.budget_max)} &middot; {brief.state}</p>
  </div>
</header>
<p>Based on your requirements ({brief.bedrooms_min}+ bedrooms, {brief.bathrooms_min}+ bathrooms,
{brief.car_spaces_min}+ car spaces{', ' + ', '.join(brief.primary_suburbs) if brief.primary_suburbs else ''}),
we reviewed available stock and shortlisted the following {len(top3)} option(s) for you.</p>
{''.join(cards)}
<footer>
  <strong>Disclaimer.</strong>
  Information checked on {date_str} and subject to availability and independent verification.
  Rental estimates and price comparisons are indicative only. Where a comparison is shown
  against comparable listings it is drawn from stock we currently hold, not from a market
  valuation. This report does not constitute legal, financial, building, tax or valuation
  advice; grants and finance outcomes are subject to eligibility and professional advice.
</footer>
"""


# --------------------------------------------------------------------- branding

# Where the logo is expected to live. Colin, 5 Aug: "I'm going to give you a logo, right?
# When we do this one, right? We need a logo. You can get the logo from our Facebook page,
# Smart Property Buying."
# Order matters: the OFFICIAL artwork wins. Dropping brand/spb-logo.png supersedes the
# reproduction below with no code change, which is the whole point of the fallback being
# last — a stand-in must never outrank the real thing.
LOGO_CANDIDATES = ("brand/spb-logo.png", "brand/spb-logo.svg",
                   "brand/logo.png", "brand/logo.svg",
                   "brand/spb-logo-fallback.svg")

_LOGO_CACHE = None


def _logo_html() -> str:
    """The logo as an inline data URI, or nothing at all if no file is present.

    Inlined rather than linked because this report is opened in a new window and then
    emailed onward to the buyer — an <img src> pointing at our own host would render as
    a broken icon on their screen. Base64 keeps the report a single self-contained file.

    Returns "" when no logo file exists, so the report still generates rather than
    showing a broken image. Drop a file at brand/spb-logo.png and it appears.
    """
    global _LOGO_CACHE
    if _LOGO_CACHE is not None:
        return _LOGO_CACHE
    import base64
    import mimetypes
    from pathlib import Path
    here = Path(__file__).parent
    _LOGO_CACHE = ""
    for rel in LOGO_CANDIDATES:
        path = here / rel
        if not path.is_file():
            continue
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        _LOGO_CACHE = ('<img class="logo" alt="Smart Property Buying" '
                       'src="data:%s;base64,%s">' % (mime, data))
        break
    return _LOGO_CACHE
