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

            if saving is not None and saving > 0:
                value_line = f"<span class='good'>{_money(pb.realistic_total_price)}</span> vs average market price {_money(avg_market)} &mdash; a saving of <strong>{_money(saving)}</strong>"
            elif avg_market:
                value_line = f"{_money(pb.realistic_total_price)} vs average market price {_money(avg_market)} ({bm.get('classification', '')})"
            else:
                value_line = f"{_money(pb.realistic_total_price)} &mdash; market benchmark pending"

            comp_rows = "".join(
                f"<tr><td>{c['suburb']}, {c['state']}</td><td>{c['bedrooms']} bed</td><td>{_money(c['price'])}</td><td>{c['source']}</td></tr>"
                for c in bm.get('comparables', [])
            )
            comp_table = (
                f"<table class='comps'><tr><th>Comparable</th><th>Beds</th><th>Price</th><th>Source</th></tr>{comp_rows}</table>"
                if comp_rows else "<p class='pending'>Market comparables to be confirmed.</p>"
            )

            dist = getattr(p, 'distance_km_from_target', None)
            dist_line = f" &middot; {dist} km from {brief.primary_suburbs[0]}" if dist not in (None, 0.0) and brief.primary_suburbs else ""

            risk_items = "".join(f"<li>{r.risk_name} ({r.rating.value}): {r.proposed_mitigation}</li>" for r in p.risks)
            risk_block = f"<ul class='risks'>{risk_items}</ul>" if risk_items else "<p>No medium or high risks identified at time of checking.</p>"

            cards.append(f"""
    <div class="card">
      <div class="rank">Option {rank}</div>
      <h2>{p.lot_address}, {p.suburb} {p.state}</h2>
      <p class="sub">{p.bedrooms} bed &middot; {p.bathrooms} bath &middot; {p.car_spaces} car &middot; {p.land_size_sqm:,.0f} m&sup2; land &middot; {p.house_size_sqm:,.0f} m&sup2; house &middot; {p.builder_name}{dist_line}</p>
      <div class="row"><span class="label">Your price</span><span>{value_line}</span></div>
      <div class="row"><span class="label">Estimated rent</span><span>{_money(p.estimated_rent_weekly_min)}&ndash;{_money(p.estimated_rent_weekly_max)} / week</span></div>
      <div class="row"><span class="label">Estimated gross yield</span><span>{gross_yield:.2f}%</span></div>
      <div class="row"><span class="label">Turnkey status</span><span>{pb.turnkey_status.value}{' &mdash; allow ' + _money(pb.estimated_additional_costs) + ' additional costs' if pb.estimated_additional_costs else ''}</span></div>
      <div class="row"><span class="label">Title</span><span>{p.title_status} ({p.expected_title_date})</span></div>
      <h3>Why we recommend it</h3>
      <p>{p.recommendation_reason}</p>
      <p><em>Location:</em> {p.amenities_summary}</p>
      <h3>Market comparison</h3>
      {comp_table}
      <h3>Things to be aware of</h3>
      {risk_block}
    </div>""")

        return f"""<!-- SPB client report generated {date_str} -->
<style>
  body {{ font-family: Georgia, 'Times New Roman', serif; color: #1a1a2e; max-width: 820px; margin: 2rem auto; padding: 0 1rem; }}
  header {{ border-bottom: 3px solid #1a1a2e; padding-bottom: 1rem; margin-bottom: 1.5rem; }}
  h1 {{ font-size: 1.6rem; margin: 0; }} h2 {{ font-size: 1.15rem; margin: 0 0 .25rem; }}
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
  <h1>Smart Property Buying &mdash; Property Recommendations</h1>
  <p class="meta">Prepared for <strong>{brief.client_name}</strong> &middot; {date_str} &middot; Budget up to {_money(brief.budget_max)} &middot; {brief.state}</p>
</header>
<p>Based on your requirements ({brief.bedrooms_min}+ bedrooms, {brief.bathrooms_min}+ bathrooms,
{brief.car_spaces_min}+ car spaces{', ' + ', '.join(brief.primary_suburbs) if brief.primary_suburbs else ''}),
we reviewed the market and shortlisted the following {len(top3)} option(s) for you.</p>
{''.join(cards)}
<footer>
  Information checked on {date_str} and subject to availability and independent verification.
  Rental estimates and market comparisons are indicative only. This report does not constitute
  legal, financial, building, tax or valuation advice; grants and finance outcomes are subject to
  eligibility and professional advice.
</footer>
"""
