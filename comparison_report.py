"""
Two options, compared field by field — the report Colin already writes by hand.

On the 5 August call he shared `Builder_Comparison_Report_HomeGroup_vs_Royston.pdf` and
said ours "needs to format it much better". Watching the recording rather than reading
the transcript showed what he actually meant: his is not a prettier version of our
document, it is a DIFFERENT one. Ours is "here are the top 3, one card each". His is
"here are two, side by side, and here is what the difference costs you" — for a buyer
who has already narrowed it down and wants to decide.

His structure, and what this reproduces:

  1. Headline comparison   a field-by-field table of the two packages
  2. What each one gives    the factual advantages of each, and what to confirm
  3. Inclusions             what one includes that the other does not
  4. Cost and completion    quoted -> completion items -> indicative completed position
  5. Things to be aware of  the risks already attached to each candidate

WHAT THIS WILL NOT DO. His report carries a line like "Main design limitation: the
outdoor living area functions more like a compact covered patio" and a closing "choose
Royston unless the larger master bedroom is worth the completion cost". Those are HIS
professional judgements about a buyer he has met. This generates the arithmetic and the
stated facts; the recommendation sentence it writes is explicitly grounded in the score
and the completed position, and it says so, because a system that invents a lifestyle
opinion and signs Smart Property Buying's name to it is worse than one that stays quiet.

Fields absent from our data — ceiling height, garage area, a prose description of the
living areas — are simply not rendered. A row appears when at least one side states a
value, and the side that does not say reads "not stated".
"""

from datetime import datetime
from typing import Any, Callable, List, Optional, Sequence

from schema import CandidateProperty, ClientBrief


def _money(v) -> str:
    return f"${v:,.0f}" if v not in (None, "") else "not stated"


def _sqm(v) -> str:
    return f"{v:,.0f} m&sup2;" if v else "not stated"


def _plain(v) -> str:
    text = str(v).strip() if v not in (None, "") else ""
    return text or "not stated"


def _spec(p: CandidateProperty) -> str:
    bits = [f"{p.bedrooms} bed" if p.bedrooms is not None else None,
            f"{p.bathrooms} bath" if p.bathrooms is not None else None,
            f"{p.car_spaces} car" if p.car_spaces is not None else None]
    stated = [b for b in bits if b]
    return " &middot; ".join(stated) if stated else "not stated"


def _title(p: CandidateProperty) -> str:
    text = str(p.title_status or "").strip() or "not stated"
    extra = str(p.expected_title_date or "").strip()
    return f"{text} ({extra})" if extra else text


def _benchmark(p: CandidateProperty) -> str:
    bm = getattr(p, "benchmark", None) or {}
    median, pct = bm.get("avg_market_price"), bm.get("variance_pct")
    if not median:
        return "no comparable set"
    against = ("comparable listings" if bm.get("basis") == "internal"
               else "the market comparables supplied")
    out = f"{bm.get('classification', '')} &mdash; median of {against} {_money(median)}"
    return f"{out} ({pct:+.1f}%)" if pct is not None else out


def _completed(p: CandidateProperty) -> float:
    return float(p.price_breakdown.realistic_total_price or 0)


# One row of the headline table: label, how to read it off a property, and whether a
# difference between the two is worth calling out.
_ROWS: Sequence = (
    ("Builder", lambda p: _plain(p.builder_name), False),
    ("Design", lambda p: _plain(p.house_design), False),
    ("Address", lambda p: f"{_plain(p.lot_address)}, {_plain(p.suburb)} {p.state or ''}".strip(), False),
    ("Bedrooms / bathrooms / cars", _spec, False),
    ("Internal house area", lambda p: _sqm(p.house_size_sqm), True),
    ("Land area", lambda p: _sqm(p.land_size_sqm), True),
    ("Storeys", lambda p: _plain(p.storeys), False),
    ("Quoted package price", lambda p: _money(p.price_breakdown.advertised_package_price), True),
    ("Estimated completion items", lambda p: _money(p.price_breakdown.estimated_additional_costs), True),
    ("Indicative completed position", lambda p: f"<strong>{_money(_completed(p))}</strong>", True),
    ("Handover condition", lambda p: _plain(p.price_breakdown.turnkey_status.value), False),
    ("Title", _title, False),
    ("Price vs comparable stock", _benchmark, False),
    ("Builder confidence", lambda p: _plain(p.builder_confidence_rating), False),
    ("Match score", lambda p: f"{p.scoring.total_score:.1f} / 100" if p.scoring else "not scored", True),
)


def _advantages(a: CandidateProperty, b: CandidateProperty) -> List[str]:
    """Where A beats B, stated as measured differences and nothing more."""
    out = []
    ca, cb = _completed(a), _completed(b)
    if ca and cb and ca < cb:
        out.append(f"{_money(cb - ca)} lower once completion items are allowed for")
    if a.house_size_sqm and b.house_size_sqm and a.house_size_sqm > b.house_size_sqm:
        out.append(f"{a.house_size_sqm - b.house_size_sqm:,.0f} m&sup2; more internal house area")
    if a.land_size_sqm and b.land_size_sqm and a.land_size_sqm > b.land_size_sqm:
        out.append(f"{a.land_size_sqm - b.land_size_sqm:,.0f} m&sup2; more land")
    for label, mine, theirs in (("bedroom", a.bedrooms, b.bedrooms),
                                ("bathroom", a.bathrooms, b.bathrooms),
                                ("car space", a.car_spaces, b.car_spaces)):
        if mine is not None and theirs is not None and mine > theirs:
            out.append(f"{mine - theirs} more {label}{'s' if mine - theirs > 1 else ''}")
    extra_a = float(a.price_breakdown.estimated_additional_costs or 0)
    extra_b = float(b.price_breakdown.estimated_additional_costs or 0)
    if extra_b > extra_a:
        out.append("hands over more complete &mdash; "
                   f"{_money(extra_b - extra_a)} less to arrange separately")
    if a.scoring and b.scoring and a.scoring.total_score > b.scoring.total_score:
        out.append(f"scores {a.scoring.total_score:.1f} against {b.scoring.total_score:.1f} "
                   "on the brief")
    da, db = a.distance_km_from_target, b.distance_km_from_target
    if da is not None and db is not None and da < db:
        out.append(f"{db - da:.1f} km closer to the target suburb")
    return out


def _to_confirm(p: CandidateProperty) -> List[str]:
    """Gaps a consultant has to close before this goes to a buyer."""
    out = []
    advisories = str(getattr(p.scoring, "advisories", "") or "").strip()
    if advisories:
        out.extend(part.strip() for part in advisories.split(";") if part.strip())
    if p.verification_status and p.verification_status.value != "Verified":
        out.append(f"Listing status is {p.verification_status.value.lower()}")
    return out


def _verdict(brief: ClientBrief, a: CandidateProperty, b: CandidateProperty) -> str:
    """A defensible one-liner, grounded in the two numbers we actually computed."""
    ca, cb = _completed(a), _completed(b)
    lead, other = (a, b) if ca <= cb else (b, a)
    gap = abs(ca - cb)
    parts = []
    if gap:
        parts.append(f"<strong>{lead.builder_name or 'Option 1'}</strong> lands "
                     f"{_money(gap)} lower once completion items are allowed for")
    if a.scoring and b.scoring:
        # Only claim a lead when there IS one. Two lots from the same builder routinely
        # score identically, and "scores higher (92.2)" against an equal 92.2 is a lie
        # the reader can check in the table directly below it.
        if a.scoring.total_score != b.scoring.total_score:
            best = a if a.scoring.total_score > b.scoring.total_score else b
            parts.append(f"<strong>{best.builder_name or 'it'}</strong> scores higher "
                         f"against the brief ({best.scoring.total_score:.1f} / 100)")
        else:
            parts.append(f"both score {a.scoring.total_score:.1f} / 100 against the brief")
    if not parts:
        return ("Both options meet the brief on every figure recorded. The table below "
                "sets out the fields where they differ.")
    joined = ", and ".join(parts)
    return (f"On the figures the sources state, {joined}. "
            f"This compares what is recorded for each package &mdash; the fit with "
            f"{brief.client_name or 'the buyer'}&rsquo;s priorities is a judgement for "
            f"your consultant.")


class ComparisonReportGenerator:
    """Side-by-side comparison of exactly two candidate packages."""

    @staticmethod
    def generate_html(brief: ClientBrief, pair: Sequence[CandidateProperty],
                      date_str: str = "") -> str:
        if len(pair) != 2:
            raise ValueError("a comparison needs exactly two properties, got %d" % len(pair))
        a, b = pair
        date_str = date_str or datetime.now().strftime("%d/%m/%Y")

        # Two lots from ONE builder is a perfectly ordinary comparison, and the first
        # pairing the tool produced was exactly that — which rendered as "Verv Projects
        # vs Verv Projects" with two identical column heads and no way to tell the
        # columns apart. When the builders match, the lot identifies the option instead.
        same_builder = (str(a.builder_name or "").strip().lower()
                        == str(b.builder_name or "").strip().lower())

        def _short_lot(p):
            text = str(p.lot_address or "").strip()
            return (text[:44].rstrip() + "…") if len(text) > 45 else (text or "this option")

        def col_head(p, n):
            name = _plain(p.builder_name)
            second = _short_lot(p) if same_builder else str(p.house_design or "").strip()
            head = f"<span class='muted'>Option {n}</span><br>{name}"
            return f"{head}<br><span class='muted'>{second}</span>" if second else head

        head_a, head_b = col_head(a, 1), col_head(b, 2)

        def option_name(p, n):
            """How to refer to an option in prose. Two lots from the same builder need
            the option number or every sentence is ambiguous."""
            name = _plain(p.builder_name)
            return f"Option {n} &mdash; {name}" if same_builder else name

        name_a, name_b = option_name(a, 1), option_name(b, 2)

        rows = []
        for label, read, _ in _ROWS:
            va, vb = read(a), read(b)
            if va == "not stated" and vb == "not stated":
                continue                       # neither side says: the row adds nothing
            differs = " class='differs'" if va != vb else ""
            rows.append(f"<tr><th>{label}</th><td{differs}>{va}</td><td{differs}>{vb}</td></tr>")

        def advantage_block(one, two):
            wins = _advantages(one, two)
            confirm = _to_confirm(one)
            body = ("<ul>" + "".join(f"<li>{w}</li>" for w in wins) + "</ul>" if wins
                    else "<p class='muted'>Nothing in the recorded figures favours this "
                         "option over the other.</p>")
            todo = ("<p class='confirm'><strong>Confirm before presenting:</strong> "
                    + "; ".join(confirm) + "</p>") if confirm else ""
            return f"<h4>{option_name(one, 1 if one is a else 2)}</h4>{body}{todo}"

        # The inclusions each side still needs arranged. Presented as the union, so a
        # blank cell means "this one already covers it" rather than "unknown".
        every_item = []
        for item in list(a.price_breakdown.missing_inclusions or []) + \
                    list(b.price_breakdown.missing_inclusions or []):
            if item not in every_item:
                every_item.append(item)
        if every_item:
            inc_rows = "".join(
                "<tr><th>%s</th><td>%s</td><td>%s</td></tr>" % (
                    item,
                    "To arrange" if item in (a.price_breakdown.missing_inclusions or [])
                    else "Included",
                    "To arrange" if item in (b.price_breakdown.missing_inclusions or [])
                    else "Included")
                for item in every_item)
            inclusions = (f"<table class='cmp'><tr><th>Item</th><th>{head_a}</th>"
                          f"<th>{head_b}</th></tr>{inc_rows}</table>")
        else:
            inclusions = ("<p class='muted'>Neither price list itemises work left to "
                          "arrange. Confirm the handover condition with both builders.</p>")

        def risk_block(p):
            items = "".join(f"<li>{r.risk_name} ({r.rating.value}): {r.proposed_mitigation}</li>"
                            for r in p.risks)
            inner = (f"<ul>{items}</ul>" if items
                     else "<p class='muted'>No medium or high risks identified at time of "
                          "checking.</p>")
            return f"<h4>{option_name(p, 1 if p is a else 2)}</h4>{inner}"

        from client_report import _logo_html
        # The document title. Two different builders read as "A vs B"; two lots from one
        # builder must not read as "Verv Projects vs Verv Projects".
        if same_builder:
            names = f"{_plain(a.builder_name)} &mdash; two options compared"
        else:
            names = f"{_plain(a.builder_name)} vs {_plain(b.builder_name)}"

        return f"""<!-- SPB builder comparison generated {date_str} -->
<style>
  body {{ font-family: Georgia, 'Times New Roman', serif; color: #1a1a2e; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }}
  header {{ display: flex; align-items: center; gap: 1.1rem; border-bottom: 3px solid #1450c8; padding-bottom: 1rem; margin-bottom: 1.25rem; }}
  header img.logo {{ width: 74px; height: 74px; border-radius: 10px; flex: 0 0 auto; }}
  .eyebrow {{ font-size: .72rem; letter-spacing: .14em; text-transform: uppercase; color: #6b7280; margin: 0 0 .2rem; }}
  h1 {{ font-size: 1.5rem; margin: 0; color: #1450c8; }}
  h2 {{ font-size: 1.05rem; margin: 1.6rem 0 .5rem; border-bottom: 1px solid #e5e7eb; padding-bottom: .3rem; }}
  h4 {{ font-size: .95rem; margin: .9rem 0 .3rem; }}
  .meta {{ color: #555; font-size: .9rem; margin: .15rem 0 0; }}
  .verdict {{ background: #f3f6fd; border-left: 4px solid #1450c8; padding: .8rem 1rem; font-size: .95rem; }}
  table.cmp {{ border-collapse: collapse; width: 100%; font-size: .9rem; margin-top: .4rem; }}
  table.cmp th, table.cmp td {{ border: 1px solid #dcdfe5; padding: .4rem .6rem; text-align: left; vertical-align: top; }}
  table.cmp tr th:first-child {{ width: 30%; background: #f7f8fa; font-weight: bold; }}
  table.cmp tr:first-child th {{ background: #1450c8; color: #fff; }}
  td.differs {{ background: #fffdf2; }}
  .muted {{ color: #6b7280; font-weight: normal; font-size: .85rem; }}
  .confirm {{ color: #8a6d3b; font-size: .85rem; }}
  ul {{ margin: .3rem 0; padding-left: 1.2rem; font-size: .92rem; }}
  .cols {{ display: flex; gap: 1.6rem; }} .cols > div {{ flex: 1 1 0; }}
  footer {{ font-size: .78rem; color: #666; border-top: 1px solid #ccc; margin-top: 2rem; padding-top: 1rem; }}
  @media print {{ body {{ margin: 0; }} h2 {{ page-break-after: avoid; }} }}
</style>
<header>
  {_logo_html()}
  <div>
    <p class="eyebrow">Builder comparison report</p>
    <h1>{names}</h1>
    <p class="meta">Prepared for <strong>{_plain(brief.client_name)}</strong> &middot; {date_str} &middot; {brief.state}</p>
  </div>
</header>

<p class="verdict">{_verdict(brief, a, b)}</p>

<h2>1. Headline comparison</h2>
<table class="cmp">
  <tr><th>Comparison item</th><th>{head_a}</th><th>{head_b}</th></tr>
  {''.join(rows)}
</table>

<h2>2. What each one gives you</h2>
<div class="cols">
  <div>{advantage_block(a, b)}</div>
  <div>{advantage_block(b, a)}</div>
</div>

<h2>3. Inclusions still to be arranged</h2>
{inclusions}

<h2>4. Cost and completion</h2>
<table class="cmp">
  <tr><th>Cost position</th><th>{head_a}</th><th>{head_b}</th></tr>
  <tr><th>Quoted package price</th><td>{_money(a.price_breakdown.advertised_package_price)}</td><td>{_money(b.price_breakdown.advertised_package_price)}</td></tr>
  <tr><th>Estimated completion items</th><td>{_money(a.price_breakdown.estimated_additional_costs)}</td><td>{_money(b.price_breakdown.estimated_additional_costs)}</td></tr>
  <tr><th>Indicative completed position</th><td><strong>{_money(_completed(a))}</strong></td><td><strong>{_money(_completed(b))}</strong></td></tr>
  <tr><th>Difference</th><td colspan="2">{
      _money(abs(_completed(a) - _completed(b))) + ' in favour of ' +
      (name_a if _completed(a) <= _completed(b) else name_b)
      if _completed(a) != _completed(b) else 'The completed positions are the same.'}</td></tr>
</table>
<p class="confirm"><strong>Important.</strong> The completion allowance is an estimate.
Actual cost depends on site conditions, fencing lengths, landscaping scope and supplier
pricing, and should be confirmed with each builder in writing.</p>

<h2>5. Things to be aware of</h2>
<div class="cols">
  <div>{risk_block(a)}</div>
  <div>{risk_block(b)}</div>
</div>

<footer>
  <strong>Disclaimer.</strong> {names} prepared by Smart Property Buying &middot; {date_str}.
  Information checked on {date_str} and subject to availability and independent
  verification. Figures are drawn from each builder&rsquo;s own price list; where a
  comparison is shown against comparable listings it is drawn from stock we currently
  hold, not from a market valuation. This report does not constitute legal, financial,
  building, tax or valuation advice.
</footer>
"""
