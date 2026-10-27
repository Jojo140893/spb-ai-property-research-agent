"""A file sync that fails never costs a pull its run, nor the bucket its lock (27 Oct 2026).

`cmd_pull` takes the bucket lock BEFORE it touches anything, and only the push at the end of
the run gives it back. So between the two, nothing may raise: run_daily.py treats a failed
pull as "do not harvest" and never reaches the push, the lock stays held, and the backup
laptop's lock check stands down for the rest of the Melbourne day. Nobody harvests.

The sessions and the builder CSV are the steps that can raise there, and their docstrings
already promised "Never fatal". _fetch (which replaces the local copy) broke that promise:
Windows refuses os.replace onto a file another program holds open without
FILE_SHARE_DELETE -- Excel holding vendors.csv, or any plain open() -- and the
PermissionError went straight up through cmd_pull. On origin/main the same situation only
logged "builder csv NOT pulled" and the run went on.
"""
import os
import socket
import sqlite3
import sys
from argparse import Namespace
from pathlib import Path

import pytest

import config
import state_sync
from tests.test_state_sync_pull_replaces import FakeCli, NEW_CSV, OLD_CSV


@pytest.fixture
def cli(tmp_path, monkeypatch):
    fake = FakeCli(tmp_path)
    monkeypatch.setattr(state_sync, "APP", tmp_path)
    monkeypatch.setattr(state_sync, "DB", tmp_path / "spb_research_audit.db")
    monkeypatch.setattr(state_sync, "SESSION_DIR", tmp_path / ".sessions")
    monkeypatch.setattr(state_sync, "_cli", fake)
    monkeypatch.setattr(config, "DRIVE_INPUT_DIR", tmp_path / "drive_input")
    monkeypatch.setattr(config, "BUILDER_CSV_PATH", tmp_path / "drive_input" / "vendors.csv")
    return fake


THIS_RUN = f"{socket.gethostname()}:{os.getpid()}"


def _db_bytes(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE buildings (id INTEGER PRIMARY KEY)")
    con.executemany("INSERT INTO buildings VALUES (?)", [(i,) for i in range(rows)])
    con.commit()
    con.close()
    return path.read_bytes()


def _bucket_with_a_database(cli, tmp_path_factory, rows=5):
    src = tmp_path_factory.mktemp("bucket") / "db.sqlite"
    cli.objects["spb_research_audit.db"] = _db_bytes(src, rows)


def _lock_holder(cli):
    import json
    body = cli.objects.get("harvest.lock.json")
    return json.loads(body)["holder"] if body is not None else None


def _leftovers(root):
    return sorted(p.name for p in root.rglob("*") if p.name.endswith(".pulling"))


# --- _fetch itself: (False, why) and `dest` untouched, never an exception -------------------

def _refuse_for_staging(real, exc):
    """Wrap `real` so it raises `exc` when its first argument is a .pulling staging file."""
    def wrapped(first, *a, **k):
        if str(first).endswith(".pulling"):
            raise exc
        return real(first, *a, **k)
    return wrapped


def test_a_replace_that_windows_refuses_is_reported_not_raised(cli, monkeypatch):
    dest = cli.app / "drive_input" / "vendors.csv"
    dest.parent.mkdir()
    dest.write_text(OLD_CSV)
    cli.objects["builders/vendors.csv"] = NEW_CSV.encode()
    monkeypatch.setattr(state_sync.os, "replace",
                        _refuse_for_staging(os.replace, PermissionError(13, "Access is denied")))

    ok, why = state_sync._fetch("ss:///spb-state/builders/vendors.csv", dest, min_bytes=512)

    assert ok is False and "Access is denied" in why
    assert dest.read_text() == OLD_CSV
    assert _leftovers(cli.app) == []


def test_a_staging_file_that_cannot_be_deleted_is_reported_not_raised(cli, monkeypatch):
    """Debris from a killed run, held open (an antivirus scan, say): no copy, no crash."""
    dest = cli.app / "drive_input" / "vendors.csv"
    dest.parent.mkdir()
    dest.write_text(OLD_CSV)
    (dest.parent / ".vendors.csv.pulling").write_text("half a file")
    cli.objects["builders/vendors.csv"] = NEW_CSV.encode()
    monkeypatch.setattr(Path, "unlink",
                        _refuse_for_staging(Path.unlink, PermissionError(13, "Access is denied")))

    ok, why = state_sync._fetch("ss:///spb-state/builders/vendors.csv", dest, min_bytes=512)

    assert ok is False and why
    assert dest.read_text() == OLD_CSV


def test_a_replace_and_a_cleanup_that_both_fail_are_reported_not_raised(cli, monkeypatch):
    dest = cli.app / ".sessions" / "e_agent.json"
    dest.parent.mkdir()
    dest.write_text('{"cookies": "old"}')
    cli.objects["sessions/e_agent.json"] = b'{"cookies": "new"}'
    real_unlink = Path.unlink
    monkeypatch.setattr(state_sync.os, "replace",
                        _refuse_for_staging(os.replace, PermissionError(13, "Access is denied")))

    def unlink_refused_once_staged(self, *a, **k):
        if self.name.endswith(".pulling") and self.exists():
            raise PermissionError(13, "Access is denied")
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", unlink_refused_once_staged)

    ok, why = state_sync._fetch("ss:///spb-state/sessions/e_agent.json", dest)

    assert ok is False and "Access is denied" in why
    assert dest.read_text() == '{"cookies": "old"}'


def test_a_session_that_cannot_be_replaced_is_counted_as_not_pulled(cli, monkeypatch, capsys):
    sessions = cli.app / ".sessions"
    sessions.mkdir()
    (sessions / "e_agent.json").write_text('{"cookies": "old"}')
    cli.objects["sessions/e_agent.json"] = b'{"cookies": "new"}'
    cli.objects["sessions/portal_proxima.json"] = b'{"cookies": "p"}'
    real_replace = os.replace

    def refuse_e_agent(src, dst, *a, **k):
        if Path(dst).name == "e_agent.json":
            raise PermissionError(13, "Access is denied")
        return real_replace(src, dst, *a, **k)
    monkeypatch.setattr(state_sync.os, "replace", refuse_e_agent)

    state_sync._sync_sessions("pull")

    out = capsys.readouterr().out
    assert "sessions pulled: 1 file(s)" in out
    assert "1 session(s) NOT pulled: e_agent.json" in out
    assert (sessions / "e_agent.json").read_text() == '{"cookies": "old"}'
    assert (sessions / "portal_proxima.json").read_text() == '{"cookies": "p"}'


# --- the pull and the push around them -------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to replace an open file")
def test_a_pull_with_the_builder_csv_held_open_keeps_its_lock_and_goes_on(
        cli, tmp_path_factory, capsys):
    """The reviewer's case, for real: an ordinary open() of vendors.csv during the 3 am pull."""
    _bucket_with_a_database(cli, tmp_path_factory)
    cli.objects["builders/vendors.csv"] = NEW_CSV.encode()
    local = cli.app / "drive_input" / "vendors.csv"
    local.parent.mkdir()
    local.write_text(OLD_CSV)

    held = open(local, "r")                  # no FILE_SHARE_DELETE, as Excel holds a CSV
    try:
        rc = state_sync.cmd_pull(Namespace(force=False))
    finally:
        held.close()

    assert rc == 0
    assert _lock_holder(cli) == THIS_RUN                   # taken by this run, for the push
    assert local.read_text() == OLD_CSV                    # untouched, not half-replaced
    assert (local.parent / ".vendors.csv.unpulled").exists()
    assert _leftovers(cli.app) == []
    out = capsys.readouterr().out
    assert "builder csv NOT pulled" in out and "using this machine's copy" in out
    assert state_sync._row_count(state_sync.DB) == 5

    # ... and the run's push gives the lock back WITHOUT sending that older copy over the
    # bucket's: the .unpulled marker stops it.
    assert state_sync.cmd_push(Namespace(force=False, files_only=False)) == 0
    assert "harvest.lock.json" not in cli.objects
    assert cli.objects["builders/vendors.csv"] == NEW_CSV.encode()
    assert "last pull of it failed" in capsys.readouterr().out


def _raise(label):
    def boom(_direction):
        raise RuntimeError(f"{label} blew up")
    return boom


def test_a_crash_in_a_file_sync_never_costs_the_pull_its_run(
        cli, tmp_path_factory, monkeypatch, capsys):
    """Belt and braces: whatever the sessions or the CSV raise, the pull still returns 0."""
    _bucket_with_a_database(cli, tmp_path_factory)
    monkeypatch.setattr(state_sync, "_sync_sessions", _raise("sessions"))
    monkeypatch.setattr(state_sync, "_sync_builder_csv", _raise("builder csv"))

    assert state_sync.cmd_pull(Namespace(force=False)) == 0

    assert _lock_holder(cli) == THIS_RUN
    assert state_sync._row_count(state_sync.DB) == 5
    out = capsys.readouterr().out
    assert "!! sessions pull failed" in out and "sessions blew up" in out
    assert "!! builder csv pull failed" in out and "builder csv blew up" in out
    # nightly.ps1 reads "^\s*!! locked by" as "another machine holds the lock": never this.
    assert not any(line.lstrip().startswith("!! locked by") for line in out.splitlines())


def test_a_crash_in_a_file_sync_never_keeps_the_push_from_releasing_the_lock(
        cli, tmp_path_factory, monkeypatch, capsys):
    _bucket_with_a_database(cli, tmp_path_factory)
    assert state_sync.cmd_pull(Namespace(force=False)) == 0
    assert _lock_holder(cli) == THIS_RUN
    capsys.readouterr()
    monkeypatch.setattr(state_sync, "_sync_sessions", _raise("sessions"))
    monkeypatch.setattr(state_sync, "_sync_builder_csv", _raise("builder csv"))

    assert state_sync.cmd_push(Namespace(force=False, files_only=False)) == 0

    assert "harvest.lock.json" not in cli.objects
    out = capsys.readouterr().out
    assert "!! sessions push failed" in out and "!! builder csv push failed" in out
    # The line nightly.ps1 checks the push by (Select-String "^\s+lock released").
    assert any(line.startswith(" ") and line.strip() == "lock released"
               for line in out.splitlines())
