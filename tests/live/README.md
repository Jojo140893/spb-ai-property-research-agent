# Live checks — run against the deployed site, not localhost

These drive `https://vercelsite-three-psi.vercel.app` with a real browser. They are kept
out of `run_tests.py` because they need the network and take minutes; run them after a
deploy, and before a call with the client.

    python tests/live/verify_call_items.py    # every item from the 5 Aug call, quoted
    python tests/live/full_site_sweep.py      # 58 checks across every tab
    python tests/live/customer_journeys.py    # 8 personas, 51 checks
    python tests/live/customer_briefs.py      # 45 briefs against /api/research
    python tests/live/price_filter.py         # 12 checks on the price filter

`verify_call_items.py` is the one to read first: each check quotes what Colin actually
said, so what is being verified is his requirement rather than a paraphrase of it. It
reports BLOCKED (not passed) for anything that needs him — the logo file, the Proxima
sign-in.

Windows note: run with `python -X utf8`, or the quoted transcript lines fail to encode
on a cp1252 console.
