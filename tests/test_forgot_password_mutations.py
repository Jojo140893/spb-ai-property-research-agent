"""Forgot password: prove the important checks can FAIL.

A security test that cannot go red reads exactly like one that passes. So each safeguard below is
removed, one at a time, from a copy of the code (never the real file), and the check written for it
must go red. A mutant that survives means the test is not testing what its name says.

Two kinds:
  * the middleware (build_web.MIDDLEWARE_JS): the text is edited and tests/forgot_password_checks.mjs
    runs against it under Node, with records from the real reset flow;
  * the Python half (api/_spb_auth.py): one function or setting is swapped in memory, and the test in
    tests/test_forgot_password.py written for it is run against that.
"""
import inspect

import pytest

import tests.test_forgot_password as T

# (what is removed, (original text, mutated text), section to run, check that must go red)
MW_MUTANTS = [
    ("the SITE_PASSWORD fingerprint check",
     ('  if (!timingSafeEqual(m[3], fp)) return { mode: "site", key: password, epoch: 0, id: "none" };\n', "\n"),
     "owner", "owner changed SITE_PASSWORD: the new SITE_PASSWORD signs in"),
    ("checking the mac before the fingerprint",
     ('  const body = raw.slice(0, raw.lastIndexOf("."));\n',
      '  const fp0 = bytesToHex(await hmacRaw(await authKey(secret, "env-fp"), password)).slice(0, 16);\n'
      '  if (!timingSafeEqual(m[3], fp0)) return { mode: "site", key: password, epoch: 0, id: "none" };\n'
      '  const body = raw.slice(0, raw.lastIndexOf("."));\n'),
     "cannot be trusted", "untrusted record (secret changed): SITE_PASSWORD gets 503, not a session"),
    # (A tampered box is also caught by decryption: garbage iterations are out of range. A record signed
    # under another secret is caught only by the mac.)
    ("the record's mac check",
     ('    throw new AuthConfigError("SPB_PW_RECORD failed its check");', "    ;"),
     "cannot be trusted", "untrusted record (signed elsewhere): SITE_PASSWORD gets 503, not a session"),
    ("failing closed on a malformed record",
     ('  if (!m) throw new AuthConfigError("SPB_PW_RECORD is not a record this version understands");',
      '  if (!m) return { mode: "site", key: password, epoch: 0 };'),
     "cannot be trusted", "untrusted record (malformed): SITE_PASSWORD gets 503, not a session"),
    ("the session key derived from the record",
     ("id: m[6].slice(0, ID_LEN), key: key };", "id: m[6].slice(0, ID_LEN), key: password };"),
     "reset is live", "after a reset: a session from before it is dead"),
    ("keeping the routes shut while unconfigured",
     ("  if (resetOn) {\n    if (request.method", "  if (true) {\n    if (request.method"),
     "off", "OFF: /forgot is gated like any page"),
    ("exact matching of the endpoint paths",
     ('(path === "/api/reset_code" || path === "/api/reset_password")', '(path.startsWith("/api/reset"))'),
     "on, no reset", "ON: /api/reset_codex still needs a login"),
]


def _fingerprint_first(A):
    real = A.active_record

    def active_record():                    # the order before the 23 Oct review: fp, then mac
        rec = A.parse_record(A._env("SPB_PW_RECORD"))
        if rec is not None and A._env("SPB_AUTH_SECRET") and rec["fp"] != A.env_fp():
            return None
        return real()
    return active_record


def _early_code_ok(A):
    def code_ok(code, now, vid):            # stops at the first match: no longer constant-time
        bucket = int(now // A.BUCKET)
        return any(A.hmac.compare_digest(A.code_for(bucket - k, vid), code) for k in range(A.BUCKETS))
    return code_ok


# (what is removed, how, the test that must go red)
PY_MUTANTS = [
    ("the 20-minute limit on a code",
     lambda A, mp: mp.setattr(A, "BUCKETS", 5), "test_a_code_lives_fifteen_to_twenty_minutes"),
    ("the constant-time compare across buckets",
     lambda A, mp: mp.setattr(A, "code_ok", _early_code_ok(A)),
     "test_a_code_is_compared_against_every_live_bucket_in_constant_time"),
    ("binding the code to the live password",
     lambda A, mp: mp.setattr(A, "verifier_id", lambda rec: "site|" + A.env_fp()),
     "test_after_a_reset_goes_live_the_old_code_is_dead"),
    ("the same-origin check",
     lambda A, mp: mp.setattr(A, "same_origin", lambda headers: True),
     "test_respond_refuses_everything_but_small_same_origin_json"),
    ("waiting for GitHub to accept the record",
     lambda A, mp: mp.setattr(A, "dispatch", lambda record: True),
     "test_when_github_refuses_nothing_is_promised_and_the_code_still_works"),
    ("refusing SITE_PASSWORD as the new password after a reset",
     lambda A, mp: mp.setattr(A, "is_current_password", lambda pw, rec: rec is None and pw == T.FAKE_SITE),
     "test_a_password_that_changes_nothing_is_refused_after_the_code"),
    ("the double-click guard",
     lambda A, mp: mp.setattr(A.LIMITS, "spent", lambda code, now: False),
     "test_a_double_click_does_not_hand_over_twice"),
    ("keying the wrong-code limit by client",
     lambda A, mp: mp.setattr(A.LIMITS, "too_many_wrong",
                              lambda ip, now, real=A.LIMITS.too_many_wrong: real("everyone", now))
     or mp.setattr(A.LIMITS, "wrong_code", lambda ip, now, real=A.LIMITS.wrong_code: real("everyone", now)),
     "test_wrong_codes_block_only_the_client_sending_them"),
    ("the cap on hand-offs (Vercel deploys)",
     lambda A, mp: mp.setattr(A.LIMITS, "may_hand_off", lambda now: True),
     "test_hand_offs_are_capped_so_the_days_deploys_last"),
    ("checking the mac before the fingerprint",
     lambda A, mp: mp.setattr(A, "active_record", _fingerprint_first(A)),
     "test_a_changed_secret_is_refused_not_ignored"),
]


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        w = T.World(mp)
        try:
            return T._fixtures(w)
        finally:
            w.close()


@pytest.fixture(scope="module")
def control(fixtures, tmp_path_factory):
    """The suite on the UNMUTATED middleware: every check a mutant must break has to pass here."""
    p = T.run_checks(tmp_path_factory.mktemp("control"), fixtures)
    assert p.returncode == 0, "the control run is not green:\n" + p.stdout[-3000:] + p.stderr[-1000:]
    return [ln.strip()[6:].strip() for ln in p.stdout.splitlines() if ln.strip().startswith("PASS")]


@pytest.mark.parametrize("removed,edit,section,expected", MW_MUTANTS, ids=[m[0] for m in MW_MUTANTS])
def test_removing_a_middleware_safeguard_turns_its_check_red(removed, edit, section, expected,
                                                             fixtures, control, tmp_path):
    import build_web
    assert expected in control, "the control run has no check named %r" % expected
    original, mutated = edit
    text = build_web.MIDDLEWARE_JS.replace("\r\n", "\n")
    assert text.count(original) == 1, "the mutant no longer matches the code: %r" % original
    p = T.run_checks(tmp_path, fixtures, mw_text=text.replace(original, mutated), only=section)
    failed = [ln.strip()[6:].strip() for ln in p.stdout.splitlines() if ln.strip().startswith("FAIL")]
    assert "NO CHECKS RAN" not in p.stdout, "section filter %r matches no section" % section
    assert p.returncode != 0, "MUTANT SURVIVED: with %s removed, every check in [%s] passed" % (removed, section)
    assert any(f.startswith(expected) for f in failed), (
        "with %s removed, %r did not fail. Failed instead: %s" % (removed, expected, failed[:6]))


def _run(test_name, monkeypatch, mutate=None):
    w = T.World(monkeypatch)
    try:
        if mutate:
            mutate(w.A, monkeypatch)
        fn = getattr(T, test_name)
        kwargs = {"world": w, "monkeypatch": monkeypatch}
        fn(**{k: kwargs[k] for k in inspect.signature(fn).parameters})
    finally:
        w.close()


@pytest.mark.parametrize("removed,mutate,test_name", PY_MUTANTS, ids=[m[0] for m in PY_MUTANTS])
def test_removing_a_python_safeguard_turns_its_test_red(removed, mutate, test_name):
    with pytest.MonkeyPatch.context() as mp:
        _run(test_name, mp)                  # green on the real code first
    with pytest.MonkeyPatch.context() as mp:
        # A failed assert, or pytest.raises reporting "DID NOT RAISE": both mean the test went red.
        with pytest.raises((AssertionError, pytest.fail.Exception)):
            _run(test_name, mp, mutate)
