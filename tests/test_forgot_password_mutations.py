"""Forgot password: prove the important checks can FAIL.

A security test that cannot go red reads exactly like one that passes. So each safeguard below is
removed, one at a time, from a copy of the code (never the real file), and the check written for it
must go red. A mutant that survives means the test is not testing what its name says.

Two kinds:
  * the middleware (build_web.MIDDLEWARE_JS): the text is edited and tests/forgot_password_checks.mjs
    runs against it under Node, with records from the real reset flow;
  * the Python half (api/_spb_auth.py): one function or setting is swapped in memory, and the test in
    tests/test_forgot_password.py written for it is run against that;
  * the workflow's first step, which runs api/_spb_auth.py in a subprocess: its text is edited, or it
    is pointed at an edited copy of the module, and the test that runs the step is run against that.
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
     ('(path === "/api/reset_code" || path === "/api/reset_password" ||\n'
      '                                      path === "/api/reset_check")', '(path.startsWith("/api/reset"))'),
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


def _shared_ceiling(A):
    """The code-email limiter before the 25 Oct review: per exact IP, plus 20 an hour per instance for
    everyone together, which a stranger could use up."""
    sends = []

    def send_decision(ip, now, window):
        sends[:] = [(t, i) for t, i in sends if now - t < 3600]
        mine = [t for t, i in sends if i == ip]
        if (mine and now - mine[-1] < A.IP_GAP) or len(mine) >= A.IP_PER_HOUR or len(sends) >= 20:
            return A.LIMITED
        sends.append((now, ip))
        return A.SEND
    return send_decision


def _username_before_the_code(A):
    real = A.password_problem

    def password_problem(password, confirm):  # the order before the 25 Oct review
        if isinstance(password, str) and A.normalise(password).lower() == A.site_username().lower():
            return "Do not use the sign-in email address as the password."
        return real(password, confirm)
    return password_problem


def _started_is_enough(A):
    """run_state before the second review: the first step seen running was "started" (and so "saved")."""
    real = A._run_state_once

    def _run_state_once(since, end):
        state = real(since, end)
        return A.STARTED if state == A.RUNNING else state
    return _run_state_once


def _older_only(A):
    """verify_record before the second review: only an OLDER number than the live one was refused."""
    real = A.verify_record

    def verify_record(text, now):
        why = real(text, now)
        if why == "not_newer_than_live":
            live = A.parse_record(A._env("SPB_PW_RECORD"))
            return "older_than_live" if A.parse_record(text)["epoch"] < live["epoch"] else None
        return why
    return verify_record


def _early_code_ok(A):
    def code_ok(code, now, vid):            # stops at the first match: no longer constant-time
        bucket = int(now // A.BUCKET)
        return any(A.hmac.compare_digest(A.code_for(bucket - k, vid), code) for k in range(A.BUCKETS))
    return code_ok


# The workflow's first step runs `python3 api/_spb_auth.py` in a subprocess, so an in-memory swap does not
# reach it. These mutants edit the step's text (T._check_step_script, which run_check_step reads), or
# point it at an edited copy of api/_spb_auth.py. A mutant that no longer matches raises LookupError, not
# AssertionError: an AssertionError here would be taken for the test going red.

def _step_without(start, end):
    """The step with the lines from `start` up to `end` taken out."""
    def mutate(A, mp, tmp_path):
        script = T._check_step_script()
        if script.count(start) != 1 or script.count(end) != 1:
            raise LookupError("the mutant no longer matches the step: %r .. %r" % (start, end))
        i, j = script.index(start), script.index(end)
        mp.setattr(T, "_check_step_script", lambda: script[:i] + script[j:])
    return mutate


def _module_edited(original, mutated, mode="check-run"):
    """The step with `python3 api/_spb_auth.py <mode>` running a copy of the module with one edit."""
    def mutate(A, mp, tmp_path):
        src = (T.API / "_spb_auth.py").read_text(encoding="utf-8")
        script, call = T._check_step_script(), "python3 api/_spb_auth.py " + mode
        if src.count(original) != 1:
            raise LookupError("the mutant no longer matches the code: %r" % original)
        if script.count(call) != 1:
            raise LookupError("the step no longer runs %r" % call)
        copy = tmp_path / "mutant_api" / "_spb_auth.py"
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text(src.replace(original, mutated), encoding="utf-8")
        mp.setattr(T, "_check_step_script",
                   lambda: script.replace(call, 'python3 "%s" %s' % (copy.as_posix(), mode)))
    return mutate


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
     lambda A, mp: mp.setattr(A, "dispatch", lambda record, timeout=None: True),
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
    # Review, 25 Oct 2026.
    ("checking the mac in /api/reset_check (shape only, as the workflow did)",
     lambda A, mp: mp.setattr(A, "verify_record", lambda text, now: None if A.parse_record(text) else "malformed"),
     "test_reset_check_vouches_only_for_records_this_dashboard_signed"),
    ("watching that GitHub started the run",
     lambda A, mp: mp.setattr(A, "run_state", lambda since, end=None: A.STARTED),
     "test_a_run_github_refuses_to_start_is_not_called_saved"),
    ("telling a run that stopped from one that started",
     lambda A, mp: mp.setattr(A, "run_state", lambda since, end=None: A.UNKNOWN),
     "test_a_run_that_stops_before_storing_is_not_called_saved"),
    ("one code email per window, with no shared ceiling",
     lambda A, mp: mp.setattr(A.LIMITS, "send_decision", _shared_ceiling(A)),
     "test_a_stranger_cannot_use_up_coleens_code_email"),
    ("freeing the window when the email fails",
     lambda A, mp: mp.setattr(A.LIMITS, "window_failed", lambda window: None),
     "test_a_failed_email_does_not_hold_the_window"),
    ("counting an IPv6 client by its /64",
     lambda A, mp: mp.setattr(A, "limit_key", lambda ip: ip),
     "test_ipv6_clients_are_counted_by_their_64"),
    ("asking the username rule only with the right code",
     lambda A, mp: mp.setattr(A, "password_problem", _username_before_the_code(A)),
     "test_the_username_rule_is_asked_only_with_the_right_code"),
    # Second review, 25 Oct 2026.
    ("watching a started run until its check step answers",
     lambda A, mp: mp.setattr(A, "_run_state_once", _started_is_enough(A)),
     "test_no_answer_at_the_wait_says_saved_or_gives_a_time"),
    ("counting only runs that stored a password",
     lambda A, mp: mp.setattr(A, "_steps_run", lambda jobs: {A.STORE_STEP: ("completed", "success")}),
     "test_the_daily_cap_counts_only_runs_that_stored_a_password"),
    ("holding each wait to the deadline in wall time",
     lambda A, mp: mp.setattr(A, "_bounded", lambda seconds, fn, *a, **k: ("ok", fn(*a, **k))),
     "test_watching_the_run_keeps_to_its_deadline"),
    ("refusing a record of the live number",
     lambda A, mp: mp.setattr(A, "verify_record", _older_only(A)),
     "test_a_record_is_applied_only_over_an_older_number"),
    # Third review, 25 Oct 2026: a run the reset page called "Nothing changed" could still be applied.
    ("refusing a re-run by GITHUB_RUN_ATTEMPT",
     _step_without("# Only the first attempt of a run", "# Shape and age"),
     "test_a_rerun_is_never_applied"),
    ("refusing a re-run by the run_attempt GitHub reports",
     _module_edited("    if attempt != 1:\n", "    if False:\n"),
     "test_a_rerun_is_never_applied"),
    ("binding the record to the run GitHub created for it",
     _module_edited("    if not (lo <= gap <= hi):\n", "    if False:\n"),
     "test_a_record_copied_into_a_later_run_is_refused"),
]


# The workflow's first step without the question to the live site: exactly what it was before the review.
def _without_the_site_check(script):
    start, end = script.index("# Signed by the dashboard"), script.index("# Newer than the password")
    return script[:start] + script[end:]


def test_removing_the_workflows_question_to_the_site_lets_a_forgery_through(tmp_path):
    """The real check is test_the_workflow_stores_nothing_the_dashboard_did_not_sign; this proves the
    step it runs can fail, by running the step without the /api/reset_check call on a forged record."""
    import time
    with pytest.MonkeyPatch.context() as mp:
        w = T.World(mp)
        try:
            w.now = time.time()
            forged = T.forged_record(1, w.now)
            code, out, outputs, gh = T.run_check_step(w, tmp_path / "control", forged)
            assert code != 0, "control: the real step let a forgery through:\n" + out
            code, out, outputs, gh = T.run_check_step(w, tmp_path / "mutant", forged,
                                                      script=_without_the_site_check(T._check_step_script()))
            assert code == 0 and outputs.startswith("epoch=1") and gh, \
                "MUTANT DID NOT CHANGE THE OUTCOME: without the site check the forgery is still refused\n" + out
        finally:
            w.close()


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


def _run(test_name, monkeypatch, tmp_path, mutate=None):
    w = T.World(monkeypatch)
    try:
        if mutate and len(inspect.signature(mutate).parameters) == 3:
            mutate(w.A, monkeypatch, tmp_path)
        elif mutate:
            mutate(w.A, monkeypatch)
        fn = getattr(T, test_name)
        kwargs = {"world": w, "monkeypatch": monkeypatch, "tmp_path": tmp_path}
        fn(**{k: kwargs[k] for k in inspect.signature(fn).parameters})
    finally:
        w.close()


@pytest.mark.parametrize("removed,mutate,test_name", PY_MUTANTS, ids=[m[0] for m in PY_MUTANTS])
def test_removing_a_python_safeguard_turns_its_test_red(removed, mutate, test_name, tmp_path):
    with pytest.MonkeyPatch.context() as mp:
        _run(test_name, mp, tmp_path / "control")    # green on the real code first
    with pytest.MonkeyPatch.context() as mp:
        # A failed assert, or pytest.raises reporting "DID NOT RAISE": both mean the test went red.
        with pytest.raises((AssertionError, pytest.fail.Exception)):
            _run(test_name, mp, tmp_path / "mutant", mutate)
