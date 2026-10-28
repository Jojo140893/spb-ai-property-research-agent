"""The alert path, which was wired to nobody for the whole life of this workflow.

run_daily._alert() returns early when SPB_ALERT_TO is empty, and it was empty on every
run: the variable appeared nowhere but the two lines of run_daily.py that read it. All
three alert paths — FAILED, PARTIAL, DEGRADED — printed "nobody was notified of this run"
and stopped. The 12 Aug run aborted at step 1/7 having read 0 of 8,580 stored listings
and told no one.
"""
from pathlib import Path

import yaml

import run_daily

WORKFLOW = Path(run_daily.__file__).resolve().parent / ".github" / "workflows" / "daily-harvest.yml"


def test_the_runner_is_told_who_to_notify():
    """The wiring. The secret can exist in GitHub and still never reach the process."""
    spec = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    env = spec["jobs"]["harvest"]["env"]
    assert "SPB_ALERT_TO" in env, (
        "run_daily._alert() reads SPB_ALERT_TO from the environment; without this line "
        "the job cannot see the secret and every alert is skipped")
    assert "secrets.SPB_ALERT_TO" in env["SPB_ALERT_TO"]


def test_it_sends_when_a_recipient_is_set(monkeypatch):
    """Not just that it does not return early — that it reaches the SMTP call with a
    message addressed to the right person."""
    sent = {}

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            sent["host"] = host
            sent["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            sent["login"] = user

        def send_message(self, msg):
            sent["to"] = msg["To"]
            sent["subject"] = msg["Subject"]

    import smtplib

    monkeypatch.setenv("SPB_ALERT_TO", "someone@example.com")
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr("secrets_store.get_credentials",
                        lambda portal, csv_fallback=("", ""): ("inbox@example.com", "pw", "env"))

    run_daily._alert("SPB daily run FAILED — test", "body")

    assert sent.get("to") == "someone@example.com", (
        "the alert never reached send_message; it returned early somewhere")
    assert sent["subject"].startswith("SPB daily run FAILED")
    assert sent["host"] == "smtp.gmail.com" and sent["port"] == 465
    assert sent["login"] == "inbox@example.com", "sends through the inbox credentials"


def test_it_says_so_loudly_when_there_is_no_recipient(monkeypatch, capsys):
    """The behaviour that made the gap visible in the first place. It must keep printing,
    because a silently swallowed alert recreates the exact problem."""
    monkeypatch.delenv("SPB_ALERT_TO", raising=False)
    run_daily._alert("subject", "body")
    assert "SPB_ALERT_TO is not set" in capsys.readouterr().out


def test_a_broken_mailer_is_loud_rather_than_silent(monkeypatch, capsys):
    """An alert that fails must not become the next thing that dies unnoticed."""
    import smtplib

    def _explode(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setenv("SPB_ALERT_TO", "someone@example.com")
    monkeypatch.setattr(smtplib, "SMTP_SSL", _explode)
    monkeypatch.setattr("secrets_store.get_credentials",
                        lambda portal, csv_fallback=("", ""): ("inbox@example.com", "pw", "env"))
    run_daily._alert("subject", "body")
    assert "COULD NOT NOTIFY" in capsys.readouterr().out
