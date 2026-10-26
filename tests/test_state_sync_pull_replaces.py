"""state_sync's pulls REPLACE what a machine already has (26 Oct 2026).

The Supabase CLI never overwrites a local file: `storage cp ss:///... <local>` opens the
destination exclusively and fails with "The file exists" when it is there, and a copy that
fails after that open (a 404, a dropped connection) leaves a 0-byte file behind. Both were
measured with CLI 2.107.0 on 26 Oct 2026, and FakeCli below does exactly the same.

A GitHub runner starts from a fresh checkout every night and never met either. The laptop
runner keeps its checkout, so there the builder CSV was pulled once (30 Sep) and every later
pull logged "!! builder csv NOT pulled", ran on that 30 Sep copy and pushed it back over the
bucket's. Each test here fails against the state_sync.py of origin/main 3e9a17b.
"""
import json
import os
import re
import sqlite3
import time
import types
from argparse import Namespace

import pytest

import config
import state_sync

OLD_CSV = "builder,portal,username,password\n" + "old builder,https://old.example,u,p\n" * 20
NEW_CSV = "builder,portal,username,password\n" + "new builder,https://new.example,u,p\n" * 20


class FakeCli:
    """The Supabase CLI's storage commands as measured, over a dict standing in for the bucket."""

    def __init__(self, app):
        self.app = app
        self.objects = {}           # key under the bucket -> bytes
        self.calls = []

    @staticmethod
    def _key(arg):
        m = re.match(r"^ss:///[^/]+/(.*)$", arg)
        return m.group(1) if m else None

    def _local(self, arg):
        # The CLI url-parses its arguments: "C:\\x" or "C:/x" reads as scheme "c" and the
        # copy is refused as a local-to-local one. Only a relative path works.
        if re.match(r"^[A-Za-z]:", arg) or arg.startswith(("/", "\\")):
            return None
        return self.app / arg.replace("\\", "/")

    def __call__(self, *args, timeout=900):
        self.calls.append(args)
        sub = args[1]
        ok = types.SimpleNamespace(returncode=0, stdout="", stderr="")

        def fail(msg):
            return types.SimpleNamespace(returncode=1, stdout="", stderr=msg + "\n")

        if sub == "rm":
            self.objects.pop(self._key(args[2]), None)
            return ok
        if sub == "ls":
            prefix = self._key(args[2]) or ""
            names = sorted({k[len(prefix):].split("/")[0] + ("/" if "/" in k[len(prefix):] else "")
                            for k in self.objects if k.startswith(prefix)})
            return types.SimpleNamespace(returncode=0, stdout="\n".join(names) + "\n", stderr="")
        if sub == "mv":
            src, dst = self._key(args[2]), self._key(args[3])
            if src not in self.objects:
                return fail("Object not found")
            self.objects[dst] = self.objects.pop(src)
            return ok
        if sub == "cp":
            src, dst = args[2], args[3]
            if self._key(src) is not None:                       # download
                local = self._local(dst)
                if local is None:
                    return fail("Unsupported operation")
                if local.exists():
                    return fail(f"failed to create file: open {local}: The file exists.")
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(b"")                         # opened before the fetch
                body = self.objects.get(self._key(src))
                if body is None:
                    return fail('Error status 404: {"error":"not_found","message":"Object not found"}')
                local.write_bytes(body)
                return ok
            local = self._local(src)                             # upload
            if local is None:
                return fail("Unsupported operation")
            key = self._key(dst)
            if key in self.objects:
                return fail("Error status 409: KeyAlreadyExists")
            self.objects[key] = local.read_bytes()
            return ok
        raise AssertionError(f"unexpected CLI call {args}")


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


def _leftovers(root):
    return sorted(p.name for p in root.rglob("*") if p.name.endswith(".pulling"))


def test_the_fake_refuses_to_overwrite_as_the_real_cli_does(cli):
    """The premise of every test below: if the fake overwrote, they would all pass vacuously."""
    (cli.app / "x.csv").write_text("mine")
    cli.objects["x.csv"] = b"theirs"
    p = cli("storage", "cp", "ss:///spb-state/x.csv", "x.csv")
    assert p.returncode == 1 and "The file exists" in p.stderr
    assert (cli.app / "x.csv").read_text() == "mine"
    p = cli("storage", "cp", "ss:///spb-state/missing.csv", "y.csv")
    assert p.returncode == 1 and (cli.app / "y.csv").stat().st_size == 0


def test_a_pull_replaces_the_builder_csv_this_machine_already_has(cli, capsys):
    local = cli.app / "drive_input" / "vendors.csv"
    local.parent.mkdir()
    local.write_text(OLD_CSV)
    cli.objects["builders/vendors.csv"] = NEW_CSV.encode()

    state_sync._sync_builder_csv("pull")

    assert local.read_text() == NEW_CSV
    assert "builder csv pulled: vendors.csv" in capsys.readouterr().out
    assert _leftovers(cli.app) == []


def test_a_pull_keeps_the_copy_it_replaced(cli, capsys):
    """On a dev checkout the local CSV can hold a hand edit not pushed yet: a pull must not lose it."""
    local = cli.app / "drive_input" / "vendors.csv"
    local.parent.mkdir()
    local.write_text(OLD_CSV)
    cli.objects["builders/vendors.csv"] = NEW_CSV.encode()

    state_sync._sync_builder_csv("pull")

    assert local.read_text() == NEW_CSV
    assert (local.parent / ".vendors.csv.before-pull").read_text() == OLD_CSV
    assert ".vendors.csv.before-pull" in capsys.readouterr().out


def test_a_failed_first_pull_leaves_no_empty_csv_behind(cli, capsys):
    """An empty vendors.csv is worse than none: config picks it, and no pull replaced it."""
    state_sync._sync_builder_csv("pull")

    assert not (cli.app / "drive_input" / "vendors.csv").exists()
    out = capsys.readouterr().out
    assert "builder csv NOT pulled" in out and "0 builders" in out
    assert _leftovers(cli.app) == []


def test_a_failed_pull_keeps_the_local_copy_and_says_so(cli, capsys):
    local = cli.app / "drive_input" / "vendors.csv"
    local.parent.mkdir()
    local.write_text(OLD_CSV)

    state_sync._sync_builder_csv("pull")

    assert local.read_text() == OLD_CSV
    out = capsys.readouterr().out
    assert "using this machine's copy from" in out and "0 builders" not in out


def test_a_copy_whose_pull_failed_is_not_pushed_back_over_the_bucket(cli, capsys, monkeypatch):
    """What the laptop did from 25 Oct: pull refused, harvest on the 30 Sep copy, push it back."""
    local = cli.app / "drive_input" / "vendors.csv"
    local.parent.mkdir()
    local.write_text(OLD_CSV)
    cli.objects["builders/vendors.csv"] = NEW_CSV.encode()

    real_fetch = state_sync._fetch
    monkeypatch.setattr(state_sync, "_fetch", lambda *a, **k: (False, "network down"))
    state_sync._sync_builder_csv("pull")
    monkeypatch.setattr(state_sync, "_fetch", real_fetch)
    state_sync._sync_builder_csv("push")

    assert cli.objects["builders/vendors.csv"] == NEW_CSV.encode()
    assert "last pull of it failed" in capsys.readouterr().out

    # Edited by hand after that failed pull: deliberate, so it goes.
    later = time.time() + 5
    os.utime(local, (later, later))
    state_sync._sync_builder_csv("push")
    assert cli.objects["builders/vendors.csv"] == local.read_bytes()

    # And a pull that works clears the guard.
    cli.objects["builders/vendors.csv"] = NEW_CSV.encode()
    state_sync._sync_builder_csv("pull")
    assert not (cli.app / "drive_input" / ".vendors.csv.unpulled").exists()
    assert local.read_text() == NEW_CSV


def test_a_stub_in_the_bucket_never_replaces_a_real_directory(cli, capsys):
    local = cli.app / "drive_input" / "vendors.csv"
    local.parent.mkdir()
    local.write_text(OLD_CSV)
    cli.objects["builders/vendors.csv"] = b"builder,portal\n"

    state_sync._sync_builder_csv("pull")

    assert local.read_text() == OLD_CSV
    assert "only 15 bytes" in capsys.readouterr().out


def test_a_pull_replaces_the_sessions_this_machine_already_has(cli, capsys):
    sessions = cli.app / ".sessions"
    sessions.mkdir()
    (sessions / "portal_proxima.json").write_text('{"cookies": "old"}')
    cli.objects["sessions/portal_proxima.json"] = b'{"cookies": "new"}'
    cli.objects["sessions/e_agent.json"] = b'{"cookies": "e"}'

    state_sync._sync_sessions("pull")

    assert (sessions / "portal_proxima.json").read_text() == '{"cookies": "new"}'
    assert (sessions / "e_agent.json").read_text() == '{"cookies": "e"}'
    assert "sessions pulled: 2 file(s)" in capsys.readouterr().out
    assert _leftovers(cli.app) == []


def test_a_leftover_lock_copy_does_not_hide_the_lock(cli):
    """A refused read of the lock is 'no lock', and that is how two runners start at once."""
    (cli.app / ".harvest.lock.read.json").write_text("{}")      # a run killed mid-read
    cli.objects["harvest.lock.json"] = json.dumps(
        {"holder": "JAZIB-LAPTOP:4242", "taken_at": "2026-10-27T01:00:00+00:00"}).encode()

    lock = state_sync._read_lock()

    assert lock and lock["holder"] == "JAZIB-LAPTOP:4242"


def _sqlite_bytes(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE buildings (id INTEGER PRIMARY KEY)")
    con.executemany("INSERT INTO buildings VALUES (?)", [(i,) for i in range(rows)])
    con.commit()
    con.close()
    return path.read_bytes()


def test_a_leftover_download_does_not_block_the_pull(cli, tmp_path_factory):
    src = tmp_path_factory.mktemp("bucket") / "db.sqlite"
    cli.objects["spb_research_audit.db"] = _sqlite_bytes(src, 3)
    (cli.app / ".pulled.db").write_bytes(b"half a database")       # a run killed mid-pull

    assert state_sync.cmd_pull(Namespace(force=False)) == 0

    assert state_sync._row_count(state_sync.DB) == 3
    assert "harvest.lock.json" in cli.objects                      # the lock was taken


def test_every_local_path_handed_to_the_cli_is_relative_with_forward_slashes(cli):
    """The CLI refuses absolute local paths on Windows; one spelling for every relative one."""
    (cli.app / ".sessions").mkdir()
    cli.objects["sessions/e_agent.json"] = b"{}"
    cli.objects["builders/vendors.csv"] = NEW_CSV.encode()
    state_sync._sync_sessions("pull")
    state_sync._sync_builder_csv("pull")
    state_sync._sync_builder_csv("push")

    local_args = [a for call in cli.calls if call[1] == "cp" for a in call[2:4]
                  if not a.startswith("ss:///")]
    assert local_args
    for arg in local_args:
        assert "\\" not in arg and not re.match(r"^[A-Za-z]:|^/", arg), arg
