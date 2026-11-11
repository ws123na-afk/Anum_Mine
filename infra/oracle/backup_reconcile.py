#!/usr/bin/env python3
"""One-off reconcile of the off-site backup bucket (docs/deploy-oracle.md#reconciling-the-off-site-bucket).

Lists the objects under the bucket's ``anum-backups/`` prefix that no backup manifest references
and, only with ``--delete``, deletes those older than the retention window. A dry run (the default)
changes nothing.

How backups are named (``infra/backup/anum_backup.py``, ``infra/oracle/backup.sh``): every night
writes ``anum/anum-<UTC stamp>.dump`` with ``anum/anum-<UTC stamp>.manifest.json`` (its
``dump_file`` names the dump) and ``keycloak/keycloak-<UTC stamp>.dump``. ``rclone copy`` sends
them, encrypted, under ``anum-backups/``; rclone's crypt layer adds ``.bin`` to the stored names,
and the ``sealed:`` remote of the ``backup-upload`` container shows them without it.

An object is kept when:

* it is an ANUM manifest whose dump is in the bucket, or the dump such a manifest names;
* it is a Keycloak dump taken on a UTC day that has such a complete ANUM backup (Keycloak dumps
  have no manifest; they belong to the night's run).

Everything else is an orphan: a dump whose manifest never arrived (a run that failed half way),
a manifest whose dump is missing, an unreadable manifest, a stray or partial upload. An orphan
is deleted only when it is older than the retention window (35 days, the bucket's lifecycle rule,
and never less): its age is counted from the newer of its stored time and the stamp in its name,
so nothing a recent run may still complete is touched.

Run it on the VM through ``backup.sh reconcile`` (the ``backup-upload`` container holds the
bucket keys and the crypt passphrase). Nothing secret is printed. The backup key may have no
delete right (docs/deploy-oracle.md#file-storage); each refused delete is reported, not hidden.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404: runs docker compose with fixed arguments, never a shell
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

RETENTION_DAYS = 35
MANIFEST = re.compile(r"^anum/anum-(?P<stamp>\d{8}T\d{6}Z)\.manifest\.json$")
DUMP = re.compile(r"^anum/anum-(?P<stamp>\d{8}T\d{6}Z)\.dump$")
KEYCLOAK = re.compile(r"^keycloak/keycloak-(?P<stamp>\d{8}T\d{6}Z)\.dump$")
STAMP = re.compile(r"(\d{8}T\d{6}Z)")


@dataclass(frozen=True)
class RemoteObject:
    path: str  # as the sealed: remote shows it, e.g. anum/anum-20261011T024100Z.dump
    size: int
    modified: datetime


@dataclass(frozen=True)
class Finding:
    item: RemoteObject
    reason: str
    age_days: float
    deletable: bool


class Remote(Protocol):
    def list(self) -> list[RemoteObject]: ...
    def read(self, path: str) -> bytes: ...
    def delete(self, path: str) -> None: ...


class ReconcileError(Exception):
    pass


def _stamp_time(path: str) -> datetime | None:
    found = STAMP.search(path)
    if not found:
        return None
    try:
        return datetime.strptime(found.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def age_days(item: RemoteObject, now: datetime) -> float:
    """Days since the later of the stored time and the stamp in the name (the safer, younger reading)."""
    newest = max(filter(None, (item.modified, _stamp_time(item.path))))
    return (now - newest).total_seconds() / 86400


def _referenced_dump(remote: Remote, manifest: RemoteObject) -> str | None:
    """The dump a manifest names, as a bucket path, or None when the manifest cannot be read."""
    try:
        data = json.loads(remote.read(manifest.path))
    except (ReconcileError, ValueError, UnicodeDecodeError):
        return None
    name = data.get("dump_file") if isinstance(data, dict) else None
    if not isinstance(name, str) or "/" in name or not DUMP.match(f"anum/{name}"):
        return None
    return f"anum/{name}"


def reconcile(remote: Remote, *, now: datetime, retention_days: int = RETENTION_DAYS) -> tuple[list[RemoteObject], list[Finding]]:
    """(objects kept, orphans) for the bucket as ``remote`` lists it. Reads manifests, deletes nothing."""
    if retention_days < RETENTION_DAYS:
        raise ReconcileError(f"the retention window cannot be shorter than {RETENTION_DAYS} days")
    objects = remote.list()
    by_path = {item.path: item for item in objects}
    kept: set[str] = set()
    reasons: dict[str, str] = {}
    complete_days: set[str] = set()
    for item in objects:
        if not MANIFEST.match(item.path):
            continue
        dump = _referenced_dump(remote, item)
        if dump is None:
            reasons[item.path] = "manifest unreadable or names no ANUM dump"
        elif dump not in by_path:
            reasons[item.path] = f"manifest whose dump {dump} is not in the bucket"
        else:
            kept.update((item.path, dump))
            day = STAMP.search(dump)
            if day:
                complete_days.add(day.group(1)[:8])
    for item in objects:
        keycloak = KEYCLOAK.match(item.path)
        if keycloak and keycloak.group("stamp")[:8] in complete_days:
            kept.add(item.path)
    findings: list[Finding] = []
    for item in sorted(objects, key=lambda entry: entry.path):
        if item.path in kept:
            continue
        reason = reasons.get(item.path)
        if reason is None:
            if DUMP.match(item.path):
                reason = "ANUM dump that no manifest names"
            elif KEYCLOAK.match(item.path):
                reason = "Keycloak dump from a night with no complete ANUM backup"
            else:
                reason = "not a backup file"
        age = age_days(item, now)
        findings.append(Finding(item=item, reason=reason, age_days=age, deletable=age >= retention_days))
    return [by_path[path] for path in sorted(kept)], findings


def run(remote: Remote, *, now: datetime, delete: bool, retention_days: int, out: Callable[[str], None]) -> int:
    kept, findings = reconcile(remote, now=now, retention_days=retention_days)
    out(f"Kept: {len(kept)} objects referenced by a complete backup.")
    if not findings:
        out("No orphans: every object belongs to a complete backup.")
        return 0
    out(f"Orphans: {len(findings)} ({sum(f.item.size for f in findings)} bytes).")
    for finding in findings:
        when = "older than the retention window" if finding.deletable else f"younger than {retention_days} days, kept"
        out(f"  {finding.item.path}  {finding.item.size} bytes  {finding.age_days:.1f} days  {finding.reason}  ({when})")
    eligible = [finding for finding in findings if finding.deletable]
    if not delete:
        out(f"Dry run: nothing was deleted. {len(eligible)} could be deleted with --delete.")
        return 0
    failed = 0
    for finding in eligible:
        try:
            remote.delete(finding.item.path)
            out(f"Deleted {finding.item.path}")
        except ReconcileError as exc:
            failed += 1
            out(f"Not deleted {finding.item.path}: {exc}")
    out(f"Deleted {len(eligible) - failed} of {len(eligible)}.")
    return 1 if failed else 0


class RcloneRemote:
    """The ``sealed:`` remote through the ``backup-upload`` container (decrypted names and contents)."""

    def __init__(self, compose_dir: Path, runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run) -> None:
        self.base = ["docker", "compose", "--project-directory", str(compose_dir), "-f", str(compose_dir / "compose.yaml"),
                     "run", "--rm", "-T", "backup-upload"]
        self.runner = runner

    def _rclone(self, *args: str) -> bytes:
        done = self.runner([*self.base, *args], capture_output=True, check=False)  # nosec B603: fixed argument list
        if done.returncode != 0:
            # rclone's own message (paths and reasons, never keys); the last lines are enough.
            detail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
            raise ReconcileError(" ".join(detail) or f"rclone exited with {done.returncode}")
        return done.stdout

    def list(self) -> list[RemoteObject]:
        rows = json.loads(self._rclone("lsjson", "sealed:", "--recursive", "--files-only"))
        return [RemoteObject(path=row["Path"], size=int(row.get("Size") or 0), modified=_parse_time(row["ModTime"])) for row in rows]

    def read(self, path: str) -> bytes:
        return self._rclone("cat", f"sealed:{path}")

    def delete(self, path: str) -> None:
        self._rclone("deletefile", f"sealed:{path}")


def _parse_time(value: str) -> datetime:
    # rclone writes RFC 3339 with up to nine fraction digits; Python reads six.
    text = re.sub(r"(\.\d{6})\d+", r"\1", value.replace("Z", "+00:00"))
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def main(argv: Sequence[str] | None = None, *, remote: Remote | None = None, now: datetime | None = None,
         out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(description="List (and with --delete remove) off-site backup objects no manifest references.")
    parser.add_argument("--compose-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--delete", action="store_true", help="delete the orphans older than the retention window")
    parser.add_argument("--retention-days", type=int, default=RETENTION_DAYS,
                        help=f"never delete anything younger than this many days (at least {RETENTION_DAYS})")
    args = parser.parse_args(argv)
    try:
        return run(remote or RcloneRemote(args.compose_dir), now=now or datetime.now(UTC), delete=args.delete,
                   retention_days=args.retention_days, out=out)
    except ReconcileError as exc:
        out(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
