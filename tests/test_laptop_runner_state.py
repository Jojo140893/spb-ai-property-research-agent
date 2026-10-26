"""ops/laptop-runner/runner_state.py: one harvest per Melbourne day across laptops.

The laptop runner (nightly.ps1) asks this helper whether another machine already finished
today, whether a harvest is running somewhere, and, after a good run, marks the day done. The
CLI is replaced by a fake that keeps the real one's habits: it never overwrites a local file,
a failed download leaves an empty one, and an upload over an existing object is refused.
"""
import importlib.util
import json
import re
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "ops" / "laptop-runner" / "runner_state.py"
_spec = importlib.util.spec_from_file_location("runner_state", _PATH)
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)

NOW = datetime(2026, 10, 27, 1, 0, tzinfo=timezone.utc)


class FakeBucket:
    def __init__(self, app):
        self.app = Path(app)
        self.objects = {}
        self.calls = []
        self.broken = False

    @staticmethod
    def key(uri):
        m = re.match(r"^ss:///[^/]+/(.*)$", uri)
        return m.group(1) if m else None

    def __call__(self, app, *args, timeout=120):
        self.calls.append(args)
        res = lambda rc, out="", err="": types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)
        if self.broken:
            return res(1, err="failed to execute http request: dial tcp: no such host")
        sub = args[1]
        if sub == "ls":
            prefix = self.key(args[2]) or ""
            names = sorted({k[len(prefix):].split("/")[0] + ("/" if "/" in k[len(prefix):] else "")
                            for k in self.objects if k.startswith(prefix)})
            return res(0, "\n".join(names) + ("\n" if names else ""))
        if sub == "rm":
            self.objects.pop(self.key(args[2]), None)
            return res(0)
        if sub == "cp":
            src, dst = args[2], args[3]
            if self.key(src) is not None:
                assert not re.match(r"^[A-Za-z]:|^[/\\]", dst), "the CLI refuses absolute paths"
                local = self.app / dst
                if local.exists():
                    return res(1, err=f"failed to create file: open {local}: The file exists.")
                local.write_bytes(b"")
                body = self.objects.get(self.key(src))
                if body is None:
                    return res(1, err="Error status 404: Object not found")
                local.write_bytes(body)
                return res(0)
            if self.key(dst) in self.objects:
                return res(1, err="Error status 409: KeyAlreadyExists")
            self.objects[self.key(dst)] = (self.app / src).read_bytes()
            return res(0)
        raise AssertionError(args)


@pytest.fixture
def bucket(tmp_path, monkeypatch):
    fake = FakeBucket(tmp_path)
    monkeypatch.setattr(rs, "cli", fake)
    monkeypatch.setattr(rs, "_code", lambda app: "3e9a17b Merge pull request #44")
    monkeypatch.setenv("COMPUTERNAME", "JAZIB-LAPTOP")
    fake.objects[rs.DB_NAME] = b"sqlite"
    fake.objects["builders/vendors.csv"] = b"name,portal\n"
    return fake


def run(bucket, *argv):
    return rs.main(["--app", str(bucket.app), *argv])


# --- parsing -------------------------------------------------------------------------------

def test_ls_output_keeps_folders_and_drops_paths():
    out = "laptop-runs/\nsessions/\nharvest.lock.json\n  spb_research_audit.db  \n\nlaptop-runs/2026-10-27.json\n"
    assert rs.parse_ls(out) == ["laptop-runs/", "sessions/", "harvest.lock.json",
                                "spb_research_audit.db", "2026-10-27.json"]


@pytest.mark.parametrize("day", ["2026-10-27; rm", "../x", "27/10/2026", ""])
def test_a_malformed_day_never_names_an_object(day):
    with pytest.raises(ValueError):
        rs.marker_name(day)


# --- the day marker ------------------------------------------------------------------------

def test_a_day_no_laptop_has_ever_marked_is_not_done(bucket, capsys):
    assert run(bucket, "day-status", "--day", "2026-10-27") == 0
    assert "no marker has ever been written" in capsys.readouterr().out


def test_a_day_marked_by_another_laptop_is_done(bucket, capsys):
    bucket.objects["laptop-runs/2026-10-27.json"] = json.dumps(
        rs.marker_body("2026-10-27", "AHSAN-LAPTOP", "3e9a17b", "2026-10-27T07:31:00+05:00", True)).encode()
    bucket.objects["laptop-runs/2026-10-26.json"] = b"{}"

    assert run(bucket, "day-status", "--day", "2026-10-27") == rs.DONE
    out = capsys.readouterr().out
    assert "already done" in out and "AHSAN-LAPTOP" in out


def test_yesterdays_marker_does_not_count_for_today(bucket):
    bucket.objects["laptop-runs/2026-10-26.json"] = b"{}"
    assert run(bucket, "day-status", "--day", "2026-10-27") == 0


def test_an_unreadable_marker_still_counts_as_done(bucket, capsys):
    bucket.objects["laptop-runs/2026-10-27.json"] = b"not json"
    assert run(bucket, "day-status", "--day", "2026-10-27") == rs.DONE
    assert "cannot read" in capsys.readouterr().out


def test_a_bucket_that_cannot_be_listed_is_could_not_tell(bucket):
    bucket.broken = True
    assert run(bucket, "day-status", "--day", "2026-10-27") == rs.UNKNOWN


def test_a_leftover_read_copy_does_not_hide_the_marker(bucket):
    """The CLI never overwrites a local file: a copy left by a killed run must be cleared first."""
    (bucket.app / ".laptop-run.read.json").write_text("{}")
    bucket.objects["laptop-runs/2026-10-27.json"] = json.dumps({"machine": "AHSAN-LAPTOP"}).encode()
    assert run(bucket, "day-status", "--day", "2026-10-27") == rs.DONE
    assert not (bucket.app / ".laptop-run.read.json").exists()


def test_marking_a_day_writes_who_when_and_what(bucket, capsys):
    assert run(bucket, "day-mark", "--day", "2026-10-27") == 0
    body = json.loads(bucket.objects["laptop-runs/2026-10-27.json"])
    assert body["melbourne_day"] == "2026-10-27"
    assert body["machine"] == "JAZIB-LAPTOP"
    assert body["code"].startswith("3e9a17b")
    assert datetime.fromisoformat(body["finished_at"]).tzinfo is not None
    assert not (bucket.app / ".laptop-run.write.json").exists()
    assert "day marker written" in capsys.readouterr().out


def test_marking_a_day_twice_replaces_the_first_marker(bucket):
    """`cp` refuses to overwrite a bucket object, so the old one must be removed first."""
    bucket.objects["laptop-runs/2026-10-27.json"] = b'{"machine": "AHSAN-LAPTOP"}'
    assert run(bucket, "day-mark", "--day", "2026-10-27") == 0
    assert json.loads(bucket.objects["laptop-runs/2026-10-27.json"])["machine"] == "JAZIB-LAPTOP"


def test_a_failed_mark_says_so(bucket, capsys):
    bucket.broken = True
    assert run(bucket, "day-mark", "--day", "2026-10-27") == 1
    assert "could not write the day marker" in capsys.readouterr().out


# --- the bucket lock -------------------------------------------------------------------------

def _lock(hours_ago):
    return json.dumps({"holder": "AHSAN-LAPTOP:12368",
                       "taken_at": (datetime.now(timezone.utc) - timedelta(hours=hours_ago))
                       .isoformat(timespec="seconds")}).encode()


def test_no_lock_is_free(bucket):
    assert run(bucket, "lock-status") == 0


def test_a_running_harvest_elsewhere_is_held(bucket, capsys):
    bucket.objects["harvest.lock.json"] = _lock(2)
    assert run(bucket, "lock-status", "--wait-hours", "8") == rs.HELD
    assert "AHSAN-LAPTOP:12368" in capsys.readouterr().out


def test_a_lock_older_than_the_wait_is_reported_as_abandoned(bucket):
    bucket.objects["harvest.lock.json"] = _lock(9)
    assert run(bucket, "lock-status", "--wait-hours", "8") == rs.HELD_LONG


def test_an_unreadable_lock_is_still_held():
    assert rs.lock_verdict({}, NOW, 8)[0] == rs.HELD
    assert rs.lock_verdict({"holder": "x", "taken_at": "yesterday"}, NOW, 8)[0] == rs.HELD


def test_a_naive_lock_time_is_read_as_utc():
    lock = {"holder": "x", "taken_at": "2026-10-26T20:00:00"}
    assert rs.lock_verdict(lock, NOW, 8)[0] == rs.HELD
    assert rs.lock_verdict(lock, NOW, 4)[0] == rs.HELD_LONG


# --- reading only -----------------------------------------------------------------------------

def test_the_status_commands_never_write_to_the_bucket(bucket):
    bucket.objects["harvest.lock.json"] = _lock(1)
    bucket.objects["laptop-runs/2026-10-27.json"] = b"{}"
    before = dict(bucket.objects)
    run(bucket, "bucket-check")
    run(bucket, "day-status", "--day", "2026-10-27")
    run(bucket, "lock-status")
    assert bucket.objects == before
    assert {c[1] for c in bucket.calls} <= {"ls", "cp"}
    assert all(not c[2].startswith(".") for c in bucket.calls if c[1] == "cp"), "an upload"


def test_bucket_check_wants_the_database(bucket):
    assert run(bucket, "bucket-check") == 0
    del bucket.objects[rs.DB_NAME]
    assert run(bucket, "bucket-check") == 1


def test_bucket_check_wants_the_builder_list(bucket, capsys):
    """A fresh laptop with no builder list would harvest no direct portals: the dry check says so."""
    del bucket.objects["builders/vendors.csv"]
    assert run(bucket, "bucket-check") == 1
    assert "0 builders" in capsys.readouterr().out
    bucket.objects["builders/other.csv"] = b"x"
    assert run(bucket, "bucket-check") == 1
