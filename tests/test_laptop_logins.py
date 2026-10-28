"""ops/laptop-runner/laptop_logins.py never shows a secret, and checks tokens before storing them.

The vault and the two CLIs are faked: nothing here reads Credential Manager or the network.
"""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "ops" / "laptop-runner" / "laptop_logins.py"
_spec = importlib.util.spec_from_file_location("laptop_logins", _PATH)
lg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lg)

SB_TOKEN = "sbp_0123456789abcdef0123456789abcdef01234567"
V_TOKEN = "AbCdEf0123456789XyZ01234"


@pytest.fixture
def vault(monkeypatch):
    store = {}
    monkeypatch.setattr(lg.keyring, "get_password", lambda svc, k: store.get((svc, k)))
    monkeypatch.setattr(lg.keyring, "set_password", lambda svc, k, v: store.__setitem__((svc, k), v))
    monkeypatch.setattr(lg.keyring, "delete_password", lambda svc, k: store.pop((svc, k)))
    return store


@pytest.fixture
def clis(monkeypatch):
    """Supabase sees the SPB project only with SB_TOKEN; Vercel deploys only with V_TOKEN."""
    seen = []

    def run(cmd, env=None, timeout=90):
        seen.append(cmd)
        if cmd[1:3] == ["projects", "list"]:
            ok = (env or {}).get("SUPABASE_ACCESS_TOKEN") == SB_TOKEN
            body = [{"ref": lg.SPB_REF if ok else "someotherref"}]
            return subprocess.CompletedProcess(cmd, 0, json.dumps(body), "")
        good = "--token" in cmd and cmd[cmd.index("--token") + 1] == V_TOKEN
        if cmd[1] == "whoami":
            return subprocess.CompletedProcess(cmd, 0 if good else 1, "spb-owner\n" if good else "", "")
        if cmd[1:3] == ["project", "ls"]:
            return subprocess.CompletedProcess(cmd, 0, "", f"  {lg.VERCEL_PROJECT}  https://x  1d\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(lg, "_run", run)
    return seen


def test_status_reports_ready_without_printing_any_value(vault, clis, capsys):
    vault[(lg.VAULT, "supabase:access_token")] = SB_TOKEN
    vault[(lg.VAULT, "vercel:token")] = V_TOKEN
    vault[(lg.VAULT, "e_agent:username")] = "agent@example.com"
    vault[(lg.VAULT, "e_agent:password")] = "hunter2-e-agent"

    assert lg.status() == 0

    out = capsys.readouterr().out
    assert "Ready: the nightly can run." in out
    for secret in (SB_TOKEN, V_TOKEN, "agent@example.com", "hunter2-e-agent"):
        assert secret not in out


def test_status_is_not_ready_without_e_agent(vault, clis, capsys):
    vault[(lg.VAULT, "supabase:access_token")] = SB_TOKEN
    vault[(lg.VAULT, "vercel:token")] = V_TOKEN
    assert lg.status() == 1
    assert "E-Agent login MISSING" in capsys.readouterr().out


def test_a_token_that_cannot_see_the_project_is_not_stored(vault, clis, monkeypatch, capsys):
    monkeypatch.setattr(lg.getpass, "getpass", lambda prompt="": "sbp_" + "f" * 40)
    lg.STEPS["supabase"][1]()
    assert (lg.VAULT, "supabase:access_token") not in vault
    assert "NOT stored" in capsys.readouterr().out


def test_a_good_vercel_token_is_stored_and_not_echoed(vault, clis, monkeypatch, capsys):
    monkeypatch.setattr(lg.getpass, "getpass", lambda prompt="": V_TOKEN)
    lg.STEPS["vercel"][1]()
    assert vault[(lg.VAULT, "vercel:token")] == V_TOKEN
    assert V_TOKEN not in capsys.readouterr().out


@pytest.mark.parametrize("junk", ["abc&calc.exe&0123456789abcdef", 'tok"en0123456789abcdefgh', "short"])
def test_anything_but_a_plain_token_never_reaches_a_command_line(vault, clis, monkeypatch, junk):
    """vercel is a .cmd shim that cmd.exe parses: a '&' in a token would run a command."""
    monkeypatch.setattr(lg.getpass, "getpass", lambda prompt="": junk)
    lg.STEPS["vercel"][1]()
    assert clis == [] and (lg.VAULT, "vercel:token") not in vault


def test_a_malformed_stored_token_is_reported_not_run(vault, clis, capsys):
    vault[(lg.VAULT, "supabase:access_token")] = SB_TOKEN
    vault[(lg.VAULT, "vercel:token")] = "abc&calc.exe&0123456789abcdef"
    vault[(lg.VAULT, "e_agent:username")] = "u"
    vault[(lg.VAULT, "e_agent:password")] = "p"
    assert lg.status() == 1
    assert not any(c[0].endswith(("vercel", "vercel.cmd")) or "whoami" in c for c in clis)
    assert "not a plain token" in capsys.readouterr().out


def test_the_inbox_login_can_be_stored_now_the_tls_fix_is_in():
    """PR #42 checks Gmail's certificate before LOGIN, so the step is offered. It must
    name the vault keys the inbox sweep reads (secrets_store email_inbox)."""
    import mail_tls                                   # the fix this step waited for
    assert mail_tls.verified_context
    assert lg.INBOX_ENABLED is True and "inbox" in lg.STEPS
    assert "email_inbox:username" in lg.MANAGED and "email_inbox:password" in lg.MANAGED


def test_forget_removes_every_managed_login(vault, capsys):
    vault[(lg.VAULT, "vercel:token")] = V_TOKEN
    vault[(lg.VAULT, "e_agent:password")] = "x"
    vault[("other-service", "keep:me")] = "y"
    lg.forget()
    assert vault == {("other-service", "keep:me"): "y"}
