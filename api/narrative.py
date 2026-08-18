"""A first draft of each option's "Investment strengths", written from its own figures.

Coleen and Colin, 18 Aug 2026: the app should "use AI to auto-generate Investment
Strengths for each property ... a strong starting point for manual review", so that
Coleen opens the report, reads it, and saves it as a PDF.

WHY THE KEY IS HERE AND NOT IN THE PAGE. The report is assembled in the browser, because
the deployed site is a static snapshot and a selection spans several searches. Anything
index.html needs, every viewer can read — so a Groq key placed there would be published,
gated or not, and billable to whoever found it. This endpoint holds the key in the Vercel
project environment, is reachable only through the site login (middleware.js exempts one
path, /r, and this is not it), and returns prose. The key is never echoed, never logged
and never reaches the page.

FIGURES ONLY, AND ENFORCED RATHER THAN REQUESTED.

Ahsan chose this explicitly on 18 Aug over the richer alternative. llama-3.1-8b-instant is
an 8-billion-parameter model; asked about Armstrong Creek or Denman it will produce a
confident, specific train line, primary school or council project that does not exist, and
these documents go to people spending eight hundred thousand dollars. A prompt asking it
not to is not a control — it is a request. So:

  * the model is sent ONLY the numbers and short labels the row itself carries. No suburb
    prose, no estate marketing text, nothing free-form for it to riff on.
  * every draft is then CHECKED before it is returned. A number that was not in the input
    is a fabrication, and so is any mention of the amenity topics we never supplied. A
    draft that fails is dropped, not repaired: the box falls back to empty, which is the
    state Coleen has been working with all along and is honest about what we know.

The suburb NAME is passed and may be repeated, because naming where a property is is not
a claim about it. Nothing may be asserted ABOUT the suburb.
"""
import json
import os
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
TIMEOUT = 25
MAX_OPTIONS = 8              # the report itself caps a selection at 8

# Kept low: this is a rewrite of figures into a sentence, not a creative task, and a
# deterministic draft means Coleen sees the same words when she reopens the report.
TEMPERATURE = 0.2
MAX_TOKENS = 160

SYSTEM = (
    "You write one short factual note for a buyer's advocate in Australia. "
    "You are given the ONLY facts you may use. Write two sentences, at most 45 words, "
    "in plain Australian English, describing what these figures mean for a buyer. "
    "Rules you must follow exactly: use only the figures given; never mention schools, "
    "transport, shops, hospitals, parks, beaches, employment, population, councils, "
    "development plans or anything else about the area; never invent a number; never "
    "estimate rent, yield, growth or value; do not name a builder; do not use bullet "
    "points, headings or quotation marks. If the figures are too sparse to say anything "
    "useful, reply with exactly: INSUFFICIENT"
)

# Topics we never supply, so any mention of one is the model drawing on its own knowledge
# of the area rather than on the row. This is the check the whole design rests on.
_INVENTED_TOPIC = re.compile(
    r"\b("
    r"school|schools|schooling|education|childcare|kindergarten|university|campus|tafe|"
    r"train|rail|railway|station|tram|bus|transport|commute|freeway|motorway|highway|"
    r"airport|shop|shops|shopping|retail|mall|centre|hospital|medical|clinic|"
    r"park|parkland|reserve|beach|coast|river|lake|"
    r"council|zoning|rezoning|precinct|corridor|infrastructure|amenity|amenities|"
    r"population|employment|jobs|demand|supply|market|median|"
    r"capital growth|appreciate|appreciation|rental yield|rent|yield|"
    r"investment return|roi|equity|"
    r"minutes from|km from|close to|near|nearby|walking distance|proximity"
    r")\b", re.I)

# Words that make a claim we cannot support from a stocklist row.
_UNSUPPORTED_CLAIM = re.compile(
    r"\b(guarantee|guaranteed|certain|assured|will increase|will rise|will grow|"
    r"safe bet|no risk|risk-free|bargain|undervalued|below market|above market)\b", re.I)


def _key():
    return (os.environ.get("GROQ_API_KEY") or "").strip()


def _digits(text):
    """Every run of digits in a string, commas and spaces inside numbers removed.

    "$812,450" and "812450" both yield {"812450"}, so a figure the model reformats is
    still recognised as one it was given.
    """
    return set(re.findall(r"\d+", re.sub(r"(?<=\d)[,\s](?=\d)", "", str(text or ""))))


# The fields a draft may be written from. Numbers and short labels only — deliberately no
# source_text, estate marketing copy, amenities_summary or recommendation_reason, which
# are free-form and would give the model prose to elaborate on.
_FACTS = (
    ("suburb", "Suburb"), ("state", "State"), ("bedrooms", "Bedrooms"),
    ("bathrooms", "Bathrooms"), ("car_spaces", "Car spaces"),
    ("land_size_sqm", "Land size (m2)"), ("house_size_sqm", "House size (m2)"),
    ("advertised_package_price", "Package price (AUD)"),
    ("incentive_amount", "Rebate (AUD)"), ("title_status", "Title status"),
    ("frontage_m", "Frontage (m)"), ("storeys", "Storeys"),
)


def _facts_block(opt):
    """The prompt's entire factual content, and the whitelist the answer is checked on."""
    lines, allowed = [], set()
    for key, label in _FACTS:
        v = opt.get(key)
        if v in (None, "", 0, "0"):
            continue
        s = str(v).strip()
        if not s:
            continue
        lines.append(f"{label}: {s}")
        allowed |= _digits(s)
    return "\n".join(lines), allowed


def _acceptable(draft, allowed, suburb):
    """(ok, reason). A draft is returned only when every claim in it was handed over."""
    text = (draft or "").strip()
    if not text or text.upper().startswith("INSUFFICIENT"):
        return False, "model declined"
    if len(text) > 400:
        return False, "too long"
    m = _INVENTED_TOPIC.search(text)
    if m:
        return False, f"mentions {m.group(0)!r}, which was never supplied"
    m = _UNSUPPORTED_CLAIM.search(text)
    if m:
        return False, f"claims {m.group(0)!r}"
    stray = _digits(text) - allowed
    # A bare ordinal the model uses to count ("two bedrooms") is spelled, not written as a
    # digit, so anything numeric that was not in the facts is invented.
    if stray:
        return False, f"invented the number(s) {sorted(stray)}"
    return True, ""


def _groq(prompt):
    """One completion. Returns (text, error)."""
    body = json.dumps({
        "model": MODEL,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        GROQ_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {_key()}",
                 "Content-Type": "application/json",
                 "User-Agent": "spb-dashboard"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            payload = json.loads(r.read().decode("utf-8", "replace"))
        return (payload["choices"][0]["message"]["content"] or "").strip(), ""
    except urllib.error.HTTPError as e:
        # The body can echo request content; the status is enough to act on and cannot
        # leak the key.
        return "", {401: "Groq rejected the key.",
                    429: "Groq rate limit reached — try again shortly.",
                    }.get(e.code, f"Groq returned {e.code}.")
    except Exception:                                                  # noqa: BLE001
        return "", "Could not reach Groq."


def drafts_for(options):
    """[{index, text, skipped}] — one entry per option, in the order given."""
    out = []
    for i, opt in enumerate(options[:MAX_OPTIONS]):
        facts, allowed = _facts_block(opt or {})
        if len(facts.splitlines()) < 3:
            out.append({"index": i, "text": "", "skipped": "too few figures to write from"})
            continue
        prompt = ("Facts about one house and land package. Use only these:\n\n"
                  + facts + "\n\nWrite the note now.")
        text, err = _groq(prompt)
        if err:
            out.append({"index": i, "text": "", "skipped": err})
            continue
        ok, why = _acceptable(text, allowed, (opt or {}).get("suburb"))
        out.append({"index": i, "text": text if ok else "", "skipped": "" if ok else why})
    return out


class handler(BaseHTTPRequestHandler):
    """Vercel Python runtime entrypoint (a BaseHTTPRequestHandler named `handler`).

    Reachable only through the site login: middleware.js matches "/(.*)" and exempts one
    exact path (/r, the client share page). This is not it.
    """

    def _reply(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        """Whether the button should offer to draft at all."""
        self._reply(200, {"configured": bool(_key()), "model": MODEL})

    def do_POST(self):
        if not _key():
            # Names the missing knob without hinting at its value.
            return self._reply(503, {
                "configured": False,
                "error": "Draft notes are not connected yet. Set GROQ_API_KEY on the "
                         "Vercel project."})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n).decode("utf-8", "replace")) if n else {}
            options = data.get("options") or []
            if not isinstance(options, list):
                raise ValueError("options must be a list")
        except Exception:                                              # noqa: BLE001
            return self._reply(400, {"configured": True, "error": "Unreadable request."})
        if not options:
            return self._reply(200, {"configured": True, "drafts": []})
        self._reply(200, {"configured": True, "model": MODEL,
                          "drafts": drafts_for(options)})
