"""
Client-Facing Report Generator (2026-07-22 client requirement).
Produces the report Coleen presents to her buyers: the top 3 recommended
properties with purchase price vs average market price, rent and yield data,
and the reasons behind each recommendation.

Output: a single self-contained, print-ready HTML file (no external assets).
"""

import html
import re
from datetime import datetime
from typing import List
from schema import ClientBrief, CandidateProperty


def _money(v) -> str:
    return f"${v:,.0f}" if v is not None else "TBC"


# api/research.py's "no ceiling" (JavaScript's largest safe integer): what a brief with no
# Max budget carries. Anything this large is not a budget anyone set.
_NO_CEILING_FROM = 1e15


def _budget_text(budget_max) -> str:
    """'Budget up to $750,000', or 'No budget limit' for a search with no ceiling -- not
    'Budget up to $9,007,199,254,740,991' (7 Oct 2026)."""
    try:
        if budget_max is None or float(budget_max) <= 0 or float(budget_max) >= _NO_CEILING_FROM:
            return "No budget limit"
    except (TypeError, ValueError):
        return "No budget limit"
    return "Budget up to " + _money(budget_max)


def _fillable(value, unit: str) -> str:
    """A spec the consultant can correct in the report, blank where the source was silent.

    Rendered as one contenteditable span per figure so a click lands on the number and
    nothing else. A value the source DID state is still shown normally -- editing it is
    allowed but not invited.
    """
    if value is None or value == "":
        # The UNIT travels inside the wrapper so print can hide the pair together. Leave
        # a stranded "bed" behind and the line reads as broken rather than incomplete.
        return (f'<span class="spec empty">'
                f'<span class="fill" contenteditable="true" '
                f'title="not stated by the source - click to add">&nbsp;&nbsp;</span>'
                f' {_esc(unit)}</span>')
    return (f'<span class="spec"><span class="fill" contenteditable="true">'
            f'{_esc(value)}</span> {_esc(unit)}</span>')


def _suburb_caveat(p) -> str:
    """A visible mark when the suburb is OUR reading rather than the source's statement.

    This document is read by the buyer. A suburb the pipeline inferred from a listing's
    own text is right about 85% of the time, which is fine for deciding what to show a
    consultant and not fine for a line a client will take as the property's address. The
    inferred ones came from things like a house design named "Magnolia" or an apartment
    type labelled "Theodore", so the failure is not a near-miss suburb — it is a town
    hundreds of kilometres away.

    Printed inline, not as a footnote, because the address is what gets copied out of
    here into an email.
    """
    try:
        import suburb_quality
    except ImportError:                            # pragma: no cover
        return ""
    why = str(getattr(p, "suburb_source", "") or "")
    if why and not suburb_quality.is_located(why) and str(getattr(p, "suburb", "")):
        return " (suburb not stated by the source &mdash; confirm before relying on it)"
    return ""


def _state_label(state) -> str:
    """The brief's state as a reader should see it: a blank state is a national search.

    Printed raw it was an empty gap, and before brief_parser stopped defaulting a blank
    state to QLD it printed "QLD" on reports for searches that covered every state.
    Shared with comparison_report so both headers say the same thing.
    """
    return str(state or "").strip().upper() or "Any state"


def _esc(v) -> str:
    """Any value that came from a brief or a vendor file, made safe to put in HTML.

    This document is client-facing and index.html renders it with document.write(), so
    an unescaped angle bracket is not a formatting bug, it is script execution. A brief
    is typed by a consultant and a listing's text comes from a builder's spreadsheet:
    neither is trusted input.

        client_name = "<script>alert(1)</script>"
        -> "Prepared for <strong><script>alert(1)</script></strong>"   and it ran.

    Everything printed around these values is written in the templates below, so
    escaping the DATA cannot damage the intended markup or the &middot;/&sup2; entities.
    """
    if v is None:
        return ""
    return html.escape(str(v), quote=True)


# The internal framings ScoringEngine.assign_recommendation puts in front of the reason.
# "Strong match (92.5/100)." is a number for a consultant deciding what to put forward,
# not for the buyer reading the result of that decision.
_INTERNAL_LEAD = re.compile(
    r"^\s*(?:strong match|satisfactory match|good match|low suitability score|"
    r"weak match)\s*\([0-9.]+\s*/\s*100\)\s*\.?\s*", re.I)
# Whole sentences that address the consultant rather than the client.
_INTERNAL_SENTENCE = re.compile(
    r"(?:requires? consultant [^.]*\.?|see risk register|"
    r"\d+\s+open risk item\(s\)[^.;]*|awaiting scoring evaluation\.?|"
    r"verification status:[^.]*\.?)", re.I)


def _client_reason(p) -> str:
    """Why this property is being put forward, in words written for the buyer.

    Coleen, 19 Aug: prefilling this box "put the internal score and the invented
    allowance straight into a document a buyer reads" -- she saw it live. The invented
    $15,000 was taken out of turnkey_calculator at the time and the web editor stopped
    prefilling the box, but THIS renderer was never changed: every client report generated
    since, including the two dated 21 Aug, opens "Why we recommend it" with
    "Strong match (92.5/100)."

    A score out of 100 is how the tool ranked the stock. The buyer is reading the answer,
    not the working, and a number they cannot check invites the one question the document
    exists to avoid. What survives is the part that is about the PROPERTY -- "Fully
    satisfies budget, location, and structural requirements", or a real caveat such as
    "inclusions are not stated by the source, confirm with the builder".

    Returns "" when nothing client-facing is left, and the caller drops the heading with
    it: a section reading "Why we recommend it: (nothing)" is worse than no section.
    """
    text = str(getattr(p, "recommendation_reason", "") or "")
    text = _INTERNAL_LEAD.sub("", text)
    text = _INTERNAL_SENTENCE.sub("", text)
    # "Note: ; ." and similar wreckage left where a caveat was removed
    text = re.sub(r"\bNote:\s*(?=[;.]|$)", "", text)
    text = re.sub(r"\s*;\s*(?=[.;]|$)", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ;-")
    # WHAT IS LEFT MUST BE A SENTENCE, not the punctuation of one. Stripping an entirely
    # internal reason used to leave a bare "." sitting under the heading.
    if not re.search(r"[A-Za-z0-9]", text):
        return ""
    return text.strip()


def _market_section(p) -> str:
    """Suburb context for a recommended option, or nothing at all.

    Coleen, 22 Jul: "your purchase price is this, but the average house price is this,
    right, so that they can see that they are doing a good buy."

    OMITTED ENTIRELY rather than hedged when the comparison is weak. A low-confidence
    number, or one padded out with established homes that carry no new-build premium,
    exists internally but does not support a claim — and this document is one a buyer
    acts on. Every figure that does appear carries its source, its comp count and its
    date, because an undated benchmark is not a benchmark.
    """
    ctx = getattr(p, "market_context", None)
    if not ctx or not getattr(ctx, "client_safe", False):
        return ""
    rows = [f"<div class='row'><span class='label'>Comparable market price</span>"
            f"<span>{_money(ctx.avg_market_price)}</span></div>"]
    if ctx.variance_pct is not None:
        better = ctx.variance_pct < 0
        rows.append(
            f"<div class='row'><span class='label'>This purchase</span><span>"
            f"<span class='{'good' if better else ''}'>"
            f"{abs(ctx.variance_pct):.1f}% {'below' if better else 'above'}</span>"
            f" the comparable market price</span></div>")
    st = getattr(ctx, "stats", None)
    for label, value in (("Suburb median", getattr(st, "median_price", None)),
                         ("Median rent", getattr(st, "median_rent", None)),
                         ("Gross yield", getattr(st, "gross_yield", None)),
                         ("12-month growth", getattr(st, "growth_12m", None)),
                         ("Days on market", getattr(st, "days_on_market", None))):
        if value:
            shown = (_money(value) if "median" in label.lower() or "rent" in label.lower()
                     else f"{value}")
            rows.append(f"<div class='row'><span class='label'>{_esc(label)}</span>"
                        f"<span>{_esc(shown)}</span></div>")
    comps = "".join(
        f"<tr><td>{_esc(c.get('suburb'))}, {_esc(c.get('state'))}</td>"
        f"<td>{_esc(c.get('bedrooms'))} bed</td><td>{_money(c.get('price'))}</td>"
        f"<td>{_esc(c.get('source'))}</td></tr>" for c in (ctx.comparables or [])[:5])
    table = (f"<table class='comps'><tr><th>Comparable</th><th>Beds</th><th>Price</th>"
             f"<th>Source</th></tr>{comps}</table>" if comps else "")
    return (f"<h3>How this compares to the suburb</h3>{''.join(rows)}{table}"
            f"<p class='pending'>{_esc(ctx.data_note or '')} "
            f"Source: {_esc(ctx.source or 'not stated')}"
            f"{' &middot; as at ' + _esc(ctx.as_at) if ctx.as_at else ''} &middot; "
            f"{ctx.n_comps} comparable(s).</p>")


class ClientReportGenerator:
    @staticmethod
    def generate_html(brief: ClientBrief, shortlist: List[CandidateProperty],
                      date_str: str = "", limit: int = 3) -> str:
        """`limit` options, best first. Pass limit=0 to render every one given.

        WAS a hard `shortlist[:3]`. That was written when the shortlist itself was capped
        at 5 (kommo_agent's old `final_pool[:5]`), so "top 3 of 5" was a deliberate
        editorial choice. The cap was lifted on 13 Aug and nothing here changed, so it
        silently became "top 3 of 544" — and the sentence below still printed the
        TRUNCATED count, telling a client we had reviewed 3 options when the search had
        ranked hundreds. Coleen, 14 Aug: "the reporting feature is restricted to the top 3
        identified properties. We require the ability to generate reports for any specific
        property selected."

        The decision now belongs to the caller, which is the only place that knows whether
        this is the automatic post-search document (3 is a sensible default for something
        nobody asked for) or a consultant's own selection (render exactly what was
        picked). Default unchanged so the existing document is byte-identical.
        """
        date_str = date_str or datetime.now().strftime("%d/%m/%Y")
        selected = list(shortlist) if not limit or limit <= 0 else shortlist[:limit]

        cards = []
        for rank, p in enumerate(selected, 1):
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
                              f"{_money(avg_market)} ({_esc(bm.get('classification', ''))})")
            else:
                value_line = f"{_money(pb.realistic_total_price)}"

            comp_rows = "".join(
                f"<tr><td>{_esc(c['suburb'])}, {_esc(c['state'])}</td><td>{_esc(c['bedrooms'])} bed</td>"
                f"<td>{_money(c['price'])}</td><td>{_esc(c['source'])}</td></tr>"
                for c in bm.get('comparables', [])
            )
            comp_table = (
                f"<table class='comps'><tr><th>Comparable</th><th>Beds</th><th>Price</th><th>Source</th></tr>{comp_rows}</table>"
                if comp_rows else (
                    f"<p class='pending'>{_esc(bm.get('data_note') or '')}</p>" if internal
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
            title_text = _esc(str(p.title_status or "Not stated in source").strip())
            if str(p.expected_title_date or "").strip():
                title_text += f" ({_esc(p.expected_title_date)})"

            # Only print the location note when it says something. "No amenity
            # assessment captured for this listing." under a heading called Location
            # reads, to a buyer, as though the area is a problem.
            amenity = str(p.amenities_summary or "").strip()
            amenity_line = (f"<p><em>Location:</em> {_esc(amenity)}</p>"
                            if amenity and not amenity.lower().startswith("no amenity")
                            else "")

            dist = getattr(p, 'distance_km_from_target', None)
            dist_line = f" &middot; {dist} km from {_esc(brief.primary_suburbs[0])}" if dist not in (None, 0.0) and brief.primary_suburbs else ""

            # What "Partial Turnkey" actually costs the buyer, itemised.
            #
            # The turnkey calculator has always produced this list — driveway, fencing,
            # landscaping, flooring, site costs — and the report threw it away, printing
            # only "Partial Turnkey — allow $15,000 additional costs". Colin's own
            # builder-comparison report (shown on the 5 Aug call) devotes a whole section
            # to exactly this: the items that "would still need to be confirmed, supplied
            # or arranged separately", then a quoted price -> completed position table.
            # A buyer cannot compare two builders without it, because the cheaper quote
            # is routinely the one that hands over less.
            turnkey_block = ""
            if pb.missing_inclusions or pb.estimated_additional_costs:
                items = "".join(f"<li>{_esc(m)}</li>" for m in (pb.missing_inclusions or []))
                item_list = (f"<p>Still to be confirmed, supplied or arranged separately:</p>"
                             f"<ul class='risks'>{items}</ul>" if items else "")
                turnkey_block = f"""
      <h3>What it costs to complete</h3>
      {item_list}
      <table class='comps'>
        <tr><th>Cost position</th><th>Amount</th></tr>
        <tr><td>Quoted package price</td><td>{_money(pb.advertised_package_price)}</td></tr>
        <tr><td>Estimated completion items</td><td>{_money(pb.estimated_additional_costs)}</td></tr>
        <tr><td><strong>Indicative completed position</strong></td>
            <td><strong>{_money(pb.realistic_total_price)}</strong></td></tr>
      </table>
      <p class='pending'>The completion allowance is an estimate only. Actual cost depends
      on site conditions, fencing lengths, landscaping scope and supplier pricing.</p>"""

            risk_items = "".join(f"<li>{_esc(r.risk_name)} ({_esc(r.rating.value)}): {_esc(r.proposed_mitigation)}</li>" for r in p.risks)
            risk_block = f"<ul class='risks'>{risk_items}</ul>" if risk_items else "<p>No medium or high risks identified at time of checking.</p>"

            # A spec the stocklist never stated is omitted from this line, not printed as
            # "None bed" and not formatted at all — f"{None:,.0f}" raises. The client sees
            # the facts that were verified; the gaps are named in the advisories instead.
            # THE CONFIGURATION IS EDITABLE, because the source portals leave it blank.
            #
            # Colin, 31 Aug: reports need an edit function "specifically for the bedroom
            # configuration field (e.g. 1/1), to correct data gaps from source portals".
            # A stocklist that never stated a bed count is common -- it is the single most
            # frequent gap in the catalogue -- and until now the report simply omitted it,
            # so a consultant had no way to put the number in without editing the PDF.
            #
            # A MISSING value is now a blank to fill rather than nothing at all, so the
            # gap is visible and clickable. The editing affordance is screen-only; see the
            # print rules in the stylesheet, which also hide any blank left unfilled so a
            # client never receives a dotted placeholder.
            spec = " &middot; ".join(bit for bit in (
                _fillable(p.bedrooms, "bed"),
                _fillable(p.bathrooms, "bath"),
                _fillable(p.car_spaces, "car"),
                f"{p.land_size_sqm:,.0f} m&sup2; land" if p.land_size_sqm else "",
                f"{p.house_size_sqm:,.0f} m&sup2; house" if p.house_size_sqm else "",
                _esc(p.builder_name),
            ) if bit)

            # THE BUYER'S VERSION, never the scorer's. See _client_reason.
            _why = _client_reason(p)
            why_block = (("<h3>Why we recommend it</h3>\n      "
                          f"<p>{_esc(_why)}</p>") if _why else "")
            cards.append(f"""
    <div class="card">
      <div class="rank">Option {rank}</div>
      <h2>{_esc(p.lot_address)}, {_esc(p.suburb)}{_suburb_caveat(p)} {_esc(p.state)}</h2>
      <p class="sub">{spec}{dist_line}</p>
      <div class="row"><span class="label">Your price</span><span>{value_line}</span></div>
      {rent_row}
      {yield_row}
      <div class="row"><span class="label">Turnkey status</span><span>{_esc(pb.turnkey_status.value)}{' &mdash; allow ' + _money(pb.estimated_additional_costs) + ' additional costs' if pb.estimated_additional_costs else ''}</span></div>
      <div class="row"><span class="label">Title</span><span>{title_text}</span></div>
      {turnkey_block}
      {why_block}
      {amenity_line}
      {_market_section(p)}
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
  .fill {{ border-bottom: 1px dashed #1450c8; min-width: 1.1em; display: inline-block;
           padding: 0 .15em; outline: none; }}
  .fill:focus {{ background: #eef4ff; }}
  .spec.empty .fill {{ background: #fffbe6; }}
  .editnote {{ font-size: .8rem; color: #8a6d3b; font-style: italic; margin: .5rem 0 1rem; }}
  @media print {{
    /* A CLIENT NEVER SEES THE SCAFFOLDING. The dashes and the highlight are there to
       invite a correction on screen; on paper they would read as a defect. A blank left
       unfilled hides its unit too, so the line does not print a stranded 'bed'. */
    .fill {{ border-bottom: none; background: none !important; }}
    .spec.empty {{ display: none; }}
    .editnote {{ display: none; }}
  }}
</style>
<script>
  // A FILLED BLANK MUST PRINT. The wrapper carries .empty so print can hide a gap the
  // consultant chose to leave; the moment they type into it, it is no longer a gap.
  document.addEventListener('input', function (e) {{
    var fill = e.target.closest && e.target.closest('.fill');
    if (!fill) return;
    var spec = fill.parentNode;
    spec.classList.toggle('empty', !fill.textContent.trim());
  }});
</script>
<header>
  {_logo_html()}
  <div class="titles">
    <h1>Property Recommendations</h1>
    <p class="meta">Prepared for <strong>{_esc(brief.client_name)}</strong> &middot; {date_str} &middot; {_budget_text(brief.budget_max)} &middot; {_esc(_state_label(brief.state))}</p>
  </div>
</header>
<p class="editnote">Any figure with a dashed underline can be clicked and corrected before you print &mdash; the source portals leave the bed / bath / car configuration blank more often than anything else. A blank you leave unfilled is hidden when the report prints.</p>
<p>Based on your requirements ({brief.bedrooms_min}+ bedrooms, {brief.bathrooms_min}+ bathrooms,
{brief.car_spaces_min}+ car spaces{', ' + ', '.join(_esc(s) for s in brief.primary_suburbs) if brief.primary_suburbs else ''}),
we reviewed available stock and shortlisted the following {len(selected)} option(s) for you.</p>
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
                   # The artwork actually supplied. Ahead of the fallback, behind the
                   # explicit spb-logo.* names so dropping one of those still wins.
                   "brand/logo.webp",
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
