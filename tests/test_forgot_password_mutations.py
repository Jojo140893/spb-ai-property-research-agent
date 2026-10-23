"""Can the forgot-password tests FAIL? Take one safeguard out at a time and see that they do.

A security test that has never been seen to fail proves nothing: it may be asserting something that is
true whatever the code does. So for each safeguard below this takes a COPY of the code (never the real
files), removes exactly that safeguard, runs the test that is meant to guard it and requires that test
to go red, having first required it to be green on the unmutated copy.

    node mutants   run tests/forgot_password_checks.mjs, limited to the sections that should notice
                   (SPB_ONLY), against the mutated middleware.js and/or mutated api/ copy. The named
                   check(s) must be among the FAIL lines.
    python mutants run the named tests of tests/test_forgot_password.py against the mutated api/ copy.

Each mutation is a text replacement that must match exactly once, so if the code under test is
refactored this fails loudly ("anchor not found") instead of quietly testing nothing.

SPB_AUTH_API_DIR (read by tests/authkit/backend.py) and SPB_MW_PATH (read by the Node suite) point the
suites at the copies.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
CHECKS = APP / "tests" / "forgot_password_checks.mjs"

# --------------------------------------------------------------------------------- the mutants
# (id, what is removed, target, edits, how it is caught)
#   target "auth": api/_spb_auth.py      target "mw": the generated middleware.js
#   caught ("node", SPB_ONLY words, [check-name fragments that must FAIL])
#   caught ("py", pytest -k expression, [test names that must FAIL])
M = []


def mutant(id, removed, target, edits, caught):
    M.append(pytest.param(id, removed, target, edits, caught, id=id))


# ---- the code and its attempts
mutant("no-attempts-decrement", "the stored decrement of attempts_left on a wrong code", "auth",
       [('nxt["attempts_left"] = int(rec["attempts_left"]) - 1', 'nxt["attempts_left"] = int(rec["attempts_left"]) - 0')],
       ("node", "WRONG CODES", ["the fifth wrong try burned the code in the store",
                                "the RIGHT code is refused after five wrong tries"]))
mutant("no-nonce-binding", "the nonce from the stored code hash", "auth",
       [('return b64url(_hmac(key, "reset-code|" + nonce + "|" + code))',
         'return b64url(_hmac(key, "reset-code|" + code))')],
       ("py", "bound_to_its_nonce", ["test_the_code_hash_is_bound_to_its_nonce"]))
mutant("no-server-key-in-code-hash", "the server secret from the code hash", "auth",
       [('def _reset_key():\n    return _hmac(_service_key(), "spb-auth|reset-code-key|v1")',
         'def _reset_key():\n    return b"public"')],
       ("py", "keyed_by_a_server_secret", ["test_the_code_hash_is_keyed_by_a_server_secret"]))
mutant("code-compared-with-equals", "constant-time comparison of the emailed code (Python)", "auth",
       [('return hmac.compare_digest(expect.encode("ascii"), rec["code_mac"].encode("ascii"))',
         'return expect == rec["code_mac"]')],
       ("py", "compared_with_compare_digest", ["test_the_code_is_compared_with_compare_digest"]))
mutant("stored-password-compared-with-equals", "constant-time comparison of the stored env_fp / mac / hash (Python)", "auth",
       [('if not hmac.compare_digest(rec["env_fp"].encode("utf-8"), env_fp().encode("ascii")):',
         'if rec["env_fp"] != env_fp():')],
       ("py", "stored_password_checks_use_compare_digest", ["test_the_stored_password_checks_use_compare_digest"]))
mutant("no-burn-on-success", "burning the code when it is used", "auth",
       [('            store.write(RESET_PATH, json.dumps(_burned(rec, now)), if_match=etag)\n        except Conflict:\n            continue                         # somebody changed',
         '            pass\n        except Conflict:\n            continue                         # somebody changed')],
       ("node", "REPLAY", ["the same code used again is refused"]))
mutant("no-conditional-write", "the ETag condition on a write (no compare-and-swap)", "auth",
       [('        if if_match:\n            headers["x-if-match"] = if_match', '        if if_match:\n            pass')],
       ("node", "CONCURRENCY", ["exactly one wins"]))
mutant("no-expiry", "the 15 minute expiry", "auth",
       [('and rec["expires_at"] > now and', 'and True and')],
       ("node", "EXPIRY", ["at 15 minutes the code has expired"]))
mutant("no-rate-limit", "the rate limit on code requests", "auth",
       [('        if rate_limited(issued, now):', '        if False:')],
       ("node", "RATE LIMITS", ["two requests inside 60 s: still one email"]))
mutant("no-timing-floor", "the minimum time /forgot takes to answer", "auth",
       [('    sleep(max(0.0, MIN_FORGOT_SECONDS - (time.monotonic() - started)))', '    pass')],
       ("node", "TIMING", ["both take at least the floor"]))
mutant("forgot-takes-an-email-field", "the refusal of a body on /forgot (no email field)", "auth",
       [('    if body:\n        return _refuse(400, "Send an empty JSON object: {}.")', '    if False:\n        return _refuse(400, "Send an empty JSON object: {}.")')],
       ("node", "SAME-ORIGIN", ["forgot with a body is refused"]))

# ---- the request
mutant("no-origin-check", "the same-origin check on both POSTs", "auth",
       [('    if not same_origin(headers):\n        return _refuse(403', '    if False:\n        return _refuse(403')],
       ("node", "SAME-ORIGIN", ["POST /api/auth/forgot with a foreign Origin -> 403",
                                "POST /api/auth/reset with a foreign Origin -> 403"]))
mutant("no-origin-check-direct", "the same-origin check (function called directly)", "auth",
       [('    if not same_origin(headers):\n        return _refuse(403', '    if False:\n        return _refuse(403')],
       ("py", "checks_the_origin_itself", ["test_the_function_checks_the_origin_itself"]))
mutant("no-content-type-check", "the JSON content-type requirement", "auth",
       [('    if ctype != "application/json":', '    if False:')],
       ("node", "SAME-ORIGIN", ["reset with Content-Type text/plain -> 415"]))
mutant("no-password-length-rule", "the 12 character minimum", "auth",
       [('    if len(pw) < MIN_PASSWORD:', '    if False:')],
       ("node", "PASSWORD RULES", ["password rule: 11 characters -> 400 weak_password"]))
mutant("a-code-in-the-log", "keeping the code out of the log", "auth",
       [('log("forgot: code sent")', 'log("forgot: code sent " + code)')],
       ("node", "HAPPY,LOGS", ["none of "]))
mutant("a-code-in-the-log-static", "keeping the code out of the log (source scan)", "auth",
       [('log("forgot: code sent")', 'log("forgot: code sent " + code)')],
       ("py-source", "no_log_line_can_carry_a_secret", ["test_no_log_line_can_carry_a_secret"]))

# ---- reads and writes
mutant("python-reads-may-be-cached", "?cache=0 on the functions' reads", "auth",
       [('"https://%s.private.blob.vercel-storage.com/%s?cache=0"', '"https://%s.private.blob.vercel-storage.com/%s"')],
       ("node", "WRONG CODES,TWO RESETS", ["every read the middleware and the functions made asked for ?cache=0"]))

# ---- the middleware
mutant("no-env-fp-check-edge", "the env_fp check that retires a record when SITE_PASSWORD changes (middleware)", "mw",
       [('  if (!timingSafeEqual(rec.env_fp, fp)) return { mode: "site", key: password };',
         '  if (false) return { mode: "site", key: password };')],
       ("node", "SITE_PASSWORD CHANGED", ["the owner's new SITE_PASSWORD signs in at once"]))
mutant("no-env-fp-check-python", "the env_fp check (Python)", "auth",
       [('    if not hmac.compare_digest(rec["env_fp"].encode("utf-8"), env_fp().encode("ascii")):\n        return None',
         '    if False:\n        return None')],
       ("py", "only_good_for_the_deployment", ["test_a_record_is_only_good_for_the_deployment_that_wrote_it"]))
mutant("no-record-mac-edge", "the record mac (middleware)", "mw",
       [('  if (!timingSafeEqual(rec.mac, b64url(await hmacRaw(macKey, canon)))) throw bad();', '')],
       ("node", "TAMPERED", ["does NOT get in (the mac)"]))
mutant("no-record-mac-python", "the record mac (Python)", "auth",
       [('        if not hmac.compare_digest(mac.encode("ascii"), record_mac(rec).encode("ascii")):\n            return None', '')],
       ("py", "mac_stops_a_writer", ["test_the_mac_stops_a_writer_who_copied_the_real_env_fp"]))
mutant("session-key-is-the-stored-hash", "the server secret in the session key", "mw",
       [('  const key = b64url(await hmacRaw(keyBytes, "spb-session-key|v1|" + rec.hash + "|" + rec.epoch));',
         '  const key = rec.hash;')],
       ("node", "HAPPY", ["a cookie forged from the stored hash is rejected"]))
mutant("old-sessions-survive-a-reset", "the new session key (old sessions die at a reset)", "mw",
       [('  return { mode: "override", rec: rec, salt: salt, key: key };', '  return { mode: "override", rec: rec, salt: salt, key: password };')],
       ("node", "HAPPY", ["the old session cookie is rejected once the instance has re-read the store",
                          "the new cookie is NOT signed with SITE_PASSWORD"]))
mutant("edge-reads-may-be-cached", "?cache=0 on the middleware's read", "mw",
       [('RESET_PW_PATH + "?cache=0"', 'RESET_PW_PATH')],
       ("node", "TWO RESETS,CONFIGURED", ["the second password signs in straight away",
                                           "and ?cache=0"]))
mutant("falls-back-to-site-password-on-error", "failing closed when the store errors", "mw",
       [('    if (standIn) return storeCache.text;\n    throw e;\n  }\n}', '    if (standIn) return storeCache.text;\n    return null;\n  }\n}')],
       ("node", "STORAGE OUTAGE", ["cold instance + store down: the old password is NOT accepted"]))
mutant("no-stale-if-error", "serving the last good read through a short outage", "mw",
       [('  const standIn = storeCache && storeCache.key === key && now - storeCache.at <= STORE_STALE_MS;',
         '  const standIn = false;')],
       ("node", "STORAGE OUTAGE", ["store down, cache 31 s old: the session still works"]))
mutant("a-record-that-fails-its-checks-is-ignored", "failing closed on an unreadable record", "mw",
       [('    throw new StoreError("the saved password record cannot be read");',
         '    return { mode: "site", key: password };')],
       ("node", "TAMPERED", ["record garbled (not JSON) -> 503 for everyone"]))
mutant("login-reads-may-use-the-cache", "the fresh read at a login (cache would hold the old password for 30 s)", "mw",
       [('  const auth = await resolveAuth(password, true);\n  const given = body.get("password") || "";',
         '  const auth = await resolveAuth(password);\n  const given = body.get("password") || "";')],
       ("node", "HAPPY,TWO RESETS,PASSWORD RULES", ["the old password is refused at once"]))
mutant("no-read-floor-on-forced-reads", "the 2 second floor on forced reads (a login flood becomes a read flood)", "mw",
       [('  const window = force ? STORE_MIN_REFRESH_MS : STORE_FRESH_MS;', '  const window = force ? 0 : STORE_FRESH_MS;')],
       ("node", "LOGIN FLOODS", ["at most 2 reads of the store"]))
mutant("forgot-prefix-match", "exact matching of /forgot (a prefix would open more)", "mw",
       [('request.method === "GET" && path === "/forgot"', 'request.method === "GET" && path.startsWith("/forgot")')],
       ("node", "ROUTE TABLE", ["nothing is open that should not be"]))
mutant("api-any-method", "the POST-only rule on the two API routes", "mw",
       [('request.method === "POST" && (path === "/api/auth/forgot" || path === "/api/auth/reset")', '(path === "/api/auth/forgot" || path === "/api/auth/reset")')],
       ("node", "ROUTE TABLE", ["nothing is open that should not be"]))
mutant("js-compare-with-equals", "constant-time comparison inside timingSafeEqual", "mw",
       [("function timingSafeEqual(a, b) {\n  const A = enc.encode(a), B = enc.encode(b);", "function timingSafeEqual(a, b) {\n  return a === b;\n  const A = enc.encode(a), B = enc.encode(b);")],
       ("node", "CONSTANT-TIME", ["the helper reads every byte whether the difference is first or last"]))
mutant("js-mac-compared-with-equals", "using the constant-time helper on the record mac", "mw",
       [('timingSafeEqual(rec.mac, b64url(await hmacRaw(macKey, canon)))', '(rec.mac === b64url(await hmacRaw(macKey, canon)))')],
       ("node", "CONSTANT-TIME", ["compared in constant time: timingSafeEqual(rec.mac"]))
mutant("inline-script-allowed", "the CSP nonce on the page script", "mw",
       [("script-src 'nonce-\" + nonce + \"'; ", "script-src 'unsafe-inline'; ")],
       ("node", "PAGES", ["CSP forbids framing, other hosts and inline script without the nonce"]))


# ---------------------------------------------------------------------------------- the machinery

def _copy_auth(dest):
    api = dest / "api"
    (api / "auth").mkdir(parents=True)
    shutil.copyfile(APP / "api" / "_spb_auth.py", api / "_spb_auth.py")
    for name in ("forgot.py", "reset.py"):
        shutil.copyfile(APP / "api" / "auth" / name, api / "auth" / name)
    return api


def _apply(text, edits, where):
    for old, new in edits:
        n = text.count(old)
        assert n == 1, "anchor %r found %d times in %s: the code changed, update the mutation" % (old[:70], n, where)
        text = text.replace(old, new)
    return text


def _build(tmp, target, edits):
    """(api_dir, middleware_path) for a copy with `edits` applied to the chosen target."""
    import build_web

    api = _copy_auth(tmp)
    mw_text = build_web.MIDDLEWARE_JS
    if target == "auth":
        p = api / "_spb_auth.py"
        p.write_bytes(_apply(p.read_text(encoding="utf-8"), edits, "_spb_auth.py").encode("utf-8"))
    else:
        mw_text = _apply(mw_text, edits, "middleware.js")
    mw = tmp / "middleware.mjs"
    mw.write_bytes(mw_text.encode("utf-8"))
    return api, mw


def _node(api, mw, only):
    env = dict(os.environ, SPB_MW_PATH=str(mw), SPB_AUTH_API_DIR=str(api), PYTHONIOENCODING="utf-8")
    if only:
        env["SPB_ONLY"] = only
    else:
        env.pop("SPB_ONLY", None)
    p = subprocess.run([NODE, str(CHECKS)], cwd=str(APP), env=env, capture_output=True, text=True,
                       encoding="utf-8", timeout=600)
    lines = p.stdout.splitlines()
    return p.returncode, [l.strip()[6:].strip() for l in lines if l.strip().startswith("PASS")], \
        [l.strip()[6:].strip() for l in lines if l.strip().startswith("FAIL")], p


def _pytest(api, expr, source_dir=None):
    env = dict(os.environ, SPB_AUTH_API_DIR=str(api), PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-m", "pytest", "tests/test_forgot_password.py", "-k", expr, "-q",
                        "-p", "no:cacheprovider", "--no-header", "-rf"],
                       cwd=str(APP), env=env, capture_output=True, text=True, encoding="utf-8", timeout=600)
    return p.returncode, p.stdout


@pytest.fixture(scope="module")
def control(tmp_path_factory):
    """The suite on UNMUTATED copies: every check a mutant is expected to break must pass here."""
    if not NODE:
        pytest.skip("node is not installed")
    tmp = tmp_path_factory.mktemp("control")
    api, mw = _build(tmp, "auth", [])
    rc, passed, failed, p = _node(api, mw, "")
    assert rc == 0 and not failed, "the control run is not green:\n" + "\n".join(failed) + p.stderr[-1500:]
    return {"passed": passed}


@pytest.mark.parametrize("id,removed,target,edits,caught", M)
def test_removing_a_safeguard_turns_a_test_red(id, removed, target, edits, caught, control, tmp_path):
    how, selector, expected = caught
    work = tmp_path / "mutant"
    work.mkdir()
    api, mw = _build(work, target, edits)
    if how == "node":
        for fragment in expected:
            assert any(fragment in name for name in control["passed"]), (
                "the control run has no PASS line containing %r: rename the check or the mutant" % fragment)
        rc, passed, failed, p = _node(api, mw, selector)
        assert rc != 0, "MUTANT SURVIVED: with %s removed every check in [%s] still passed" % (removed, selector)
        for fragment in expected:
            assert any(fragment in name for name in failed), (
                "with %s removed, the check %r did not fail. Failed instead: %s" % (removed, fragment, failed[:6]))
        return
    # Python tests: green on an untouched copy first, then red on the mutant.
    clean = tmp_path / "clean"
    clean.mkdir()
    clean_api, _ = _build(clean, "auth", [])
    rc0, out0 = _pytest(clean_api, selector)
    assert rc0 == 0, "the guarding test is not green on the unmutated copy:\n" + out0[-1500:]
    if how == "py-source":
        src = (APP / "api" / "_spb_auth.py").read_text(encoding="utf-8")
        rc, out = _scan_mutated_source(_apply(src, edits, "_spb_auth.py"), selector)
    else:
        rc, out = _pytest(api, selector)
    assert rc != 0, "MUTANT SURVIVED: with %s removed the tests [%s] still passed" % (removed, selector)
    for name in expected:
        assert name in out, "with %s removed, %s did not fail:\n%s" % (removed, name, out[-1500:])


def _scan_mutated_source(mutated, selector):
    """Run the source-scanning test against mutated text without touching the real file: the test reads
    api/<name> through _module_source, which the child process is told to read from a copy."""
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="spb_mut_"))
    try:
        (tmp / "_spb_auth.py").write_bytes(mutated.encode("utf-8"))
        env = dict(os.environ, SPB_SOURCE_DIR=str(tmp), PYTHONIOENCODING="utf-8")
        p = subprocess.run([sys.executable, "-m", "pytest", "tests/test_forgot_password.py", "-k", selector, "-q",
                            "-p", "no:cacheprovider", "--no-header", "-rf"],
                           cwd=str(APP), env=env, capture_output=True, text=True, encoding="utf-8", timeout=300)
        return p.returncode, p.stdout
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
