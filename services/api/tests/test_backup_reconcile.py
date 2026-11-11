"""The one-off off-site backup reconcile (infra/oracle/backup_reconcile.py, docs/deploy-oracle.md#reconciling-the-off-site-bucket).

A fake remote stands in for the bucket: nothing here reaches a real bucket or runs rclone. Names follow
anum_backup.py and backup.sh: anum/anum-<stamp>.dump with anum/anum-<stamp>.manifest.json, keycloak/keycloak-<stamp>.dump.
"""

from __future__ import annotations

import json
import subprocess
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "infra" / "oracle" / "backup_reconcile.py"
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def _module() -> types.ModuleType:
    # Executed from source, so no __pycache__ is written next to the server files.
    module = types.ModuleType("oracle_backup_reconcile")
    module.__file__ = str(SCRIPT)
    sys.modules[module.__name__] = module
    exec(compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec"), module.__dict__)  # noqa: S102  # nosec B102
    return module


R = _module()


def stamp(days_ago: float, minute: int = 41) -> str:
    return (NOW - timedelta(days=days_ago)).replace(hour=2, minute=minute, second=0).strftime("%Y%m%dT%H%M%SZ")


class FakeRemote:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, datetime]] = {}
        self.deleted: list[str] = []
        self.refuse: set[str] = set()

    def put(self, path: str, body: bytes, days_ago: float) -> None:
        self.objects[path] = (body, NOW - timedelta(days=days_ago))

    def backup(self, days_ago: float, *, manifest: bool = True, dump: bool = True, keycloak: bool = True) -> str:
        when = stamp(days_ago)
        if dump:
            self.put(f"anum/anum-{when}.dump", b"PGDMP", days_ago)
        if manifest:
            self.put(f"anum/anum-{when}.manifest.json", json.dumps({"dump_file": f"anum-{when}.dump", "sha256": "0" * 64}).encode(), days_ago)
        if keycloak:
            self.put(f"keycloak/keycloak-{stamp(days_ago, 42)}.dump", b"PGDMP", days_ago)
        return when

    def list(self):  # type: ignore[no-untyped-def]
        return [R.RemoteObject(path=path, size=len(body), modified=when) for path, (body, when) in self.objects.items()]

    def read(self, path: str) -> bytes:
        return self.objects[path][0]

    def delete(self, path: str) -> None:
        if path in self.refuse:
            raise R.ReconcileError("AccessDenied: the key has no deleteFiles capability")
        self.deleted.append(path)
        del self.objects[path]


def run(remote: FakeRemote, *args: str) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = R.main(list(args), remote=remote, now=NOW, out=lines.append)
    return code, lines


def test_complete_backups_are_kept_and_orphans_are_found() -> None:
    remote = FakeRemote()
    remote.backup(40)  # complete, old
    remote.backup(1)  # complete, recent
    failed_night = remote.backup(50, manifest=False)  # dump and keycloak, no manifest
    lost_dump = remote.backup(45, dump=False, keycloak=False)  # manifest whose dump is gone
    remote.put("anum/anum-20261001T024100Z.dump.partial", b"x", 60)
    remote.put(f"anum/anum-{stamp(70)}.manifest.json", b"not json", 70)
    kept, findings = R.reconcile(remote, now=NOW)
    assert len(kept) == 6  # two nights: manifest, dump and Keycloak dump each
    reasons = {finding.item.path: finding.reason for finding in findings}
    assert reasons[f"anum/anum-{failed_night}.dump"] == "ANUM dump that no manifest names"
    assert reasons[f"keycloak/keycloak-{stamp(50, 42)}.dump"].startswith("Keycloak dump from a night with no complete")
    assert "is not in the bucket" in reasons[f"anum/anum-{lost_dump}.manifest.json"]
    assert reasons["anum/anum-20261001T024100Z.dump.partial"] == "not a backup file"
    assert reasons[f"anum/anum-{stamp(70)}.manifest.json"] == "manifest unreadable or names no ANUM dump"
    assert all(finding.deletable for finding in findings)


def test_a_dry_run_is_the_default_and_deletes_nothing() -> None:
    remote = FakeRemote()
    remote.backup(50, manifest=False)
    before = dict(remote.objects)
    code, lines = run(remote)
    assert code == 0 and remote.deleted == [] and remote.objects == before
    assert any(line.startswith("Dry run: nothing was deleted. 2 could be deleted") for line in lines)


def test_delete_removes_only_orphans_older_than_the_retention_window() -> None:
    remote = FakeRemote()
    remote.backup(40)
    old = remote.backup(50, manifest=False, keycloak=False)
    recent = remote.backup(3, manifest=False, keycloak=False)  # a recent failed run: too young to touch
    # Stored long ago but named today (a re-upload of an old file under a new stamp): the younger reading wins.
    remote.put(f"anum/anum-{stamp(0)}.dump", b"PGDMP", 90)
    code, lines = run(remote, "--delete")
    assert code == 0
    assert remote.deleted == [f"anum/anum-{old}.dump"]
    assert f"anum/anum-{recent}.dump" in remote.objects and f"anum/anum-{stamp(0)}.dump" in remote.objects
    assert any("younger than 35 days, kept" in line for line in lines)


def test_the_window_cannot_be_shortened_and_refused_deletes_are_reported() -> None:
    remote = FakeRemote()
    remote.backup(50, manifest=False, keycloak=False)
    code, lines = run(remote, "--delete", "--retention-days", "7")
    assert code == 2 and remote.deleted == [] and lines == ["ERROR: the retention window cannot be shorter than 35 days"]
    remote.refuse = set(remote.objects)
    code, lines = run(remote, "--delete")
    assert code == 1 and remote.deleted == [] and any(line.startswith("Not deleted anum/anum-") and "AccessDenied" in line for line in lines)
    # A longer window keeps more.
    remote.refuse = set()
    code, lines = run(remote, "--delete", "--retention-days", "60")
    assert code == 0 and remote.deleted == []


def test_nothing_to_do_on_a_clean_bucket() -> None:
    remote = FakeRemote()
    remote.backup(2)
    code, lines = run(remote, "--delete")
    assert code == 0 and remote.deleted == [] and lines[-1] == "No orphans: every object belongs to a complete backup."


def test_the_rclone_remote_runs_the_backup_upload_container_with_fixed_arguments(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    listing = json.dumps([{"Path": "anum/anum-20261010T024100Z.dump", "Size": 5, "ModTime": "2026-10-10T02:41:07.123456789Z"}]).encode()

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        assert kwargs == {"capture_output": True, "check": False}
        if command[-1] == "sealed:anum/missing.manifest.json":
            return subprocess.CompletedProcess(command, 1, b"", b"ERROR : object not found\n")
        return subprocess.CompletedProcess(command, 0, listing if "lsjson" in command else b"{}", b"")

    remote = R.RcloneRemote(tmp_path, runner=runner)
    (item,) = remote.list()
    assert item.path == "anum/anum-20261010T024100Z.dump" and item.size == 5 and item.modified.tzinfo is not None
    remote.read("anum/x.manifest.json")
    remote.delete("anum/anum-20261010T024100Z.dump")
    with pytest.raises(R.ReconcileError, match="object not found"):
        remote.read("anum/missing.manifest.json")
    prefix = ["docker", "compose", "--project-directory", str(tmp_path), "-f", str(tmp_path / "compose.yaml"), "run", "--rm", "-T", "backup-upload"]
    assert [call[:len(prefix)] for call in calls] == [prefix] * 4
    assert [call[len(prefix):] for call in calls][:3] == [
        ["lsjson", "sealed:", "--recursive", "--files-only"], ["cat", "sealed:anum/x.manifest.json"],
        ["deletefile", "sealed:anum/anum-20261010T024100Z.dump"]]
