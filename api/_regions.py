"""Region names, and the suburbs a person means when they type one.

Coleen searched "Geelong" for a $845,000 townhouse and got nothing, then said exactly
what was wrong: "Geelong shows region here not the suburb maybe its not showing in the
app?". She was right. Of the 60 Geelong-area listings the catalogue holds, 24 are filed
under Armstrong Creek and the rest under Lara, Leopold and Kensington — only 4 say
"Geelong". The search matches suburbs exactly, so a region name reaches almost none of
its own stock.

WHY A LIST AND NOT A RULE. There is no field in the data that says "this is a region".
Geelong, Newcastle and Ipswich are all localities in their own right AND the names people
use for everything around them, and no amount of reading the catalogue distinguishes the
two senses. Australia Post has no region concept either. So the names are written down,
each with the distance that actually covers the area a buyer means, and anything not on
the list keeps behaving exactly as it does today.

The radii are deliberately modest. A region search should widen to the towns that trade
as part of that market, not to everything within an hour's drive: Geelong at 25 km
reaches Armstrong Creek, Lara, Leopold, Charlemont and Mount Duneed, and stops well short
of Melbourne's south-west.
"""

# name -> how far out that name is understood to reach, in km.
REGIONS = {
    # VIC
    "geelong": 25,
    "ballarat": 20,
    "bendigo": 20,
    "mornington peninsula": 35,
    "latrobe valley": 30,
    # NSW
    "newcastle": 25,
    "wollongong": 25,
    "central coast": 30,
    "hunter valley": 40,
    "illawarra": 35,
    # QLD
    "gold coast": 30,
    "sunshine coast": 30,
    "ipswich": 25,
    "logan": 20,
    "moreton bay": 30,
    "toowoomba": 20,
    "fraser coast": 40,
    # SA / WA
    "adelaide hills": 25,
    "peel": 35,
}


def radius_for(name: str) -> int:
    """How far a region name reaches, or 0 when the name is not a region."""
    return REGIONS.get(str(name or "").strip().lower(), 0)


def expand(suburbs, state, index, known=None):
    """The suburbs a search should cover, with any region name widened to its area.

    `known` is the set of suburbs the catalogue actually holds stock in, lowercased.
    Passing it keeps the expansion to places there is something to find — a region can
    otherwise add two hundred localities to a search that has stock in five of them, and
    every one of those becomes a line in the rejection log.

    Returns (suburbs, expanded) where `expanded` is {region: [suburbs it added]}, so the
    caller can tell a consultant what the search actually covered rather than silently
    answering a different question from the one they typed.
    """
    if not suburbs or not state or index is None or not getattr(index, "loaded", False):
        return list(suburbs or []), {}

    out, seen, expanded = [], set(), {}
    for name in suburbs:
        key = str(name or "").strip()
        if not key:
            continue
        if key.lower() not in seen:
            out.append(key)
            seen.add(key.lower())
        km = radius_for(key)
        if not km:
            continue
        added = []
        for entry in index.suburbs_within_radius(key, state, km) or []:
            near = str(entry.get("suburb") or "").strip()
            if not near or near.lower() in seen:
                continue
            if known is not None and near.lower() not in known:
                continue
            out.append(near)
            seen.add(near.lower())
            added.append(near)
        if added:
            expanded[key] = sorted(added)
    return out, expanded
