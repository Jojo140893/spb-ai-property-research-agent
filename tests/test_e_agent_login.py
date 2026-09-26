"""The E-Agent login reaches the email form from either Wix panel, and says what it saw when
it cannot.

From 24 Sep 2026 every nightly harvest logged "E-Agent: login form never appeared (site
layout may have changed)" and read 0 E-Agent listings, so lots that sold after 24 Sep stayed
in the search (Coleen, 26 Sep: the top of an SA search opened dead Kenekt pages). Measured
26 Sep from a fresh browser: the layout had NOT changed. The saved session had died with its
cookies still in the browser, and a returning visitor can land on the LOG-IN panel, where
the old first selector "button:has-text('Log In')" -- a case-insensitive substring --
matched "Log in with Google". The real page was driven to the form with the new code and
nothing typed (scratchpad eagent_login/reach_form.py); these tests hold the shape of it
offline.
"""
import inspect
import logging
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from sources import e_agent  # noqa: E402
from sources.e_agent import EAgentSource  # noqa: E402


def _code(fn):
    """A function's code without its comments and docstring, which quote the old selector,
    re-printed by ast (so its quoting is ast's: double quotes become single)."""
    import ast
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    body = tree.body[0].body
    if body and isinstance(body[0], ast.Expr) and isinstance(
            getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
        body.pop(0)
    return ast.unparse(tree)


def _login_source():
    return _code(EAgentSource._login)


def test_no_substring_button_match_can_pick_log_in_with_google():
    login, form = _login_source(), _code(e_agent.open_members_form)
    for src in (login, form):
        assert "has-text('Log In')" not in src
        assert "has-text(\"Log In\")" not in src
    assert "get_by_role('button', name='Log In', exact=True)" in login, "the submit is exact"
    assert "open_members_form(page, self.cfg.password_selector)" in login
    assert "name='Log in with Email', exact=True" in form
    assert "signUp.switchToSignUp" in form, "the sign-up panel's switch, by its test id"


def test_the_manual_sign_in_takes_the_same_exact_path_for_e_agent():
    import portal_login
    src = _code(portal_login._open_login_form)
    head = src[:src.index("steps = [")]
    assert "if name == 'e_agent':" in head and "open_members_form(page)" in head, \
        "E-Agent must never reach the substring steps below"


def test_a_saved_browser_profile_keeps_its_cookies():
    src = _login_source()
    guard = src.index("getattr(scraper, '_using_profile', False)")
    assert guard < src.index("clear_cookies"), "the profile check comes first"
    assert "else:" in src[guard:src.index("clear_cookies")]


def test_the_login_starts_as_a_new_visitor_and_only_forgets_e_agent():
    src = _login_source()
    at = src.index("clear_cookies(domain=re.compile(")
    assert "e-agent" in src[at:at + 80], "only E-Agent's cookies"
    import re
    only_e_agent = re.compile(r"(^|\.)e-agent\.com\.au$")
    assert only_e_agent.search(".www.e-agent.com.au")
    assert not only_e_agent.search("note-agent.com.au")
    assert not only_e_agent.search("portal.proxima.com.au")
    # Cleared AFTER the saved session was tried: a working session is never thrown away.
    assert src.index("self._authenticated(scraper)") < src.index("clear_cookies")


class _Dialog:
    def __init__(self, text=None):
        self._text = text

    def count(self):
        return 0 if self._text is None else 1

    def inner_text(self, timeout=None):
        return self._text


class _Page:
    url = "https://www.e-agent.com.au/access-projects"

    def __init__(self):
        self.shots = []
        self.events = []

    def title(self):
        return "e-agent"

    def inner_text(self, selector, timeout=None):
        return "Access Projects\n  Members only"

    def evaluate(self, script):
        self.events.append(("evaluate", script))

    def screenshot(self, path, full_page=False):
        Path(path).write_bytes(b"png")
        self.shots.append(path)
        self.events.append(("screenshot", path))

    def content(self):
        self.events.append(("content", None))
        return "<html>dialog</html>"


def test_a_failure_says_what_the_dialog_showed_and_keeps_a_copy(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(e_agent.config, "PROJECT_ROOT", tmp_path)
    src = EAgentSource.__new__(EAgentSource)
    page = _Page()
    with caplog.at_level(logging.ERROR):
        src._record_login_failure(page, _Dialog("Log In\n  New to this site? Sign Up  Email"))
    line = " ".join(r.getMessage() for r in caplog.records)
    assert "login form never appeared" in line
    assert "dialog='Log In New to this site? Sign Up Email'" in line, "whitespace folded"
    assert (tmp_path / "logs" / "e_agent_login_failure.png").exists()
    assert (tmp_path / "logs" / "e_agent_login_failure.html").read_text(
        encoding="utf-8") == "<html>dialog</html>"
    assert "site layout may have changed" not in line, "the guess that was wrong on 24 Sep"


def test_every_input_is_blanked_before_anything_is_captured(tmp_path, monkeypatch, caplog):
    """After a submit the email and password are in the page; the capture must not be."""
    monkeypatch.setattr(e_agent.config, "PROJECT_ROOT", tmp_path)
    src = EAgentSource.__new__(EAgentSource)
    page = _Page()
    with caplog.at_level(logging.ERROR):
        src._record_login_failure(page, _Dialog("Log In Wrong email or password"),
                                  "the login dialog stayed open after the credentials "
                                  "were submitted")
    kinds = [k for k, _ in page.events]
    assert kinds[0] == "evaluate", "blanked first"
    assert "querySelectorAll('input, textarea')" in page.events[0][1]
    assert kinds.index("evaluate") < kinds.index("screenshot") < kinds.index("content")
    assert "the login dialog stayed open after the credentials were submitted" in caplog.text
    assert "Wrong email or password" in caplog.text, "Wix's own reason reaches the log"
    assert "page='Access Projects Members only'" in caplog.text


def test_after_the_submit_the_login_waits_for_the_dialog_and_records_both_failures():
    src = _login_source()
    submit = src.index("get_by_role('button', name='Log In', exact=True).click(")
    assert src.index("dialog.wait_for(state='hidden'", submit) > submit
    assert "the login dialog stayed open after the credentials were submitted" in src
    assert "signed in but no stocklist files are reachable" in src
    assert src.count("_record_login_failure(") == 3, "form missing, dialog open, no files"


def test_the_quick_check_uses_the_nightly_login_and_saves_no_session():
    import e_agent_login_check as check
    src = inspect.getsource(check)
    assert "source._login(scraper)" in src
    assert "read_only=True" in src
    from sources.scraper_base import SESSION_DIR, PROFILE_DIR
    assert not (SESSION_DIR / f"{check.SESSION_NAME}.json").exists()
    assert not (PROFILE_DIR / check.SESSION_NAME).exists()
    wf = (APP / ".github" / "workflows" / "e-agent-login-check.yml").read_text(
        encoding="utf-8")
    assert "workflow_dispatch:" in wf and "schedule:" not in wf, "by hand only"
    assert "python e_agent_login_check.py" in wf
    assert "path: logs/e_agent_login_failure.*" in wf


def test_no_dialog_at_all_is_said_as_such(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(e_agent.config, "PROJECT_ROOT", tmp_path)
    src = EAgentSource.__new__(EAgentSource)
    with caplog.at_level(logging.ERROR):
        src._record_login_failure(_Page(), _Dialog(None))
    assert "(no members dialog)" in caplog.text


def test_the_capture_is_never_committed_and_the_nightly_keeps_it():
    assert "logs/" in (APP / ".gitignore").read_text(encoding="utf-8").splitlines()
    wf = (APP / ".github" / "workflows" / "daily-harvest.yml").read_text(encoding="utf-8")
    assert "path: logs/e_agent_login_failure.*" in wf
    assert "actions/upload-artifact@v4" in wf
