"""One-off reconcile of the Files bucket (docs/deploy-oracle.md#reconciling-the-files-bucket).

Lists the objects in the API's bucket that no database row references and, only with
``--delete``, deletes those older than a safety age. A dry run (the default) changes nothing.

Why it exists: before November 2026 the nightly retention job on the Oracle VM ran without the
bucket settings, so emptied vault trash, expired mail attachments and call recordings lost their
database rows while their objects stayed in the bucket. Nothing points at those objects any
more, so no job finds them.

What references an object (every writer of ``file_store.storage``):

* ``tenants/<tenant>/workspaces/<workspace>/files/...``: ``workspace_files.storage_key`` (every
  file, vault files and call and meeting recordings included) and
  ``workspace_file_versions.storage_key`` (older versions);
* ``tenants/<tenant>/workspaces/<workspace>/mail/...``: ``mail_attachments.storage_key``;
* ``tenants/<tenant>/workspaces/<workspace>/lifecycle/...``: ``tenant_lifecycle_jobs.archive_key``
  (organisation exports);
* ``public-logos/<organisation>/...``: the organisation's ``manifest.json`` in the bucket itself
  (``anum_api.public_logos``): a logo it lists, and the manifest, are kept.

The database is read per tenant found in the bucket, as ``anum_maintenance`` inside that
tenant's RLS context (``SET LOCAL ROLE``, migration ``0176_files_audit_probe``: the tenant and
storage key columns only, rows of the tenant named by ``anum.tenant_id`` only), in a short
read-only transaction. The role never writes; deleting an object needs no database write.

Safety:

* an object younger than ``--min-age-days`` (default 7, never less than 2) is never deleted,
  so an upload whose row is not committed yet is safe;
* before deleting, the tenant's references are read again and any object referenced by then is
  kept;
* a tenant whose references cannot be read, a key that is not an ANUM file key and a file kind
  this script does not know are reported and never deleted;
* each refused delete (a key without delete rights) is reported, not hidden.

Run it where the API's bucket settings and the application login are, on the Oracle VM:
``docker compose --profile jobs run --rm retention python -m anum_api.files_reconcile``
(add ``--delete`` after reading the list). Nothing secret is printed; keys name ids and a
content hash only.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

MIN_AGE_DAYS = 7
MIN_AGE_FLOOR_DAYS = 2
_PART = r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}"
TENANT_KEY = re.compile(rf"^tenants/(?P<tenant>{_PART})/workspaces/(?P<workspace>{_PART})/(?P<kind>{_PART})/.+$")
LOGO_KEY = re.compile(r"^public-logos/(?P<org>[0-9a-f]{32})/(?P<name>[^/]+)$")
KNOWN_KINDS = frozenset({"files", "mail", "lifecycle"})

# As anum_maintenance in the tenant's context (migration 0176_files_audit_probe): one literal
# statement per table (bandit B608).
_REFERENCES = (
    "select storage_key from workspace_files where tenant_id = :tenant",
    "select storage_key from workspace_file_versions where tenant_id = :tenant",
    "select storage_key from mail_attachments where tenant_id = :tenant",
    "select archive_key from tenant_lifecycle_jobs where tenant_id = :tenant and archive_key is not null",
)


class ReconcileError(Exception):
    pass


@dataclass(frozen=True)
class BucketObject:
    key: str
    size: int
    modified: datetime


class Bucket(Protocol):
    def list(self) -> Iterable[BucketObject]: ...
    def read(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


# tenant id -> every object key the database references for it; raises when it cannot read.
References = Callable[[str], set[str]]


@dataclass(frozen=True)
class Finding:
    item: BucketObject
    reason: str
    age_days: float
    deletable: bool
    tenant_id: str | None = None


@dataclass
class Report:
    kept: int = 0
    kept_bytes: int = 0
    orphans: list[Finding] = field(default_factory=list)
    # Reported, never deleted: unknown keys and kinds, tenants whose references could not be read.
    skipped: list[Finding] = field(default_factory=list)
    unreadable_tenants: dict[str, str] = field(default_factory=dict)


def age_days(item: BucketObject, now: datetime) -> float:
    return (now - item.modified).total_seconds() / 86400


def _logo_manifests(bucket: Bucket, objects: list[BucketObject]) -> dict[str, set[str] | None]:
    """organisation -> names its manifest lists (None: no manifest or an unreadable one)."""
    import json

    found: dict[str, set[str] | None] = {}
    for item in objects:
        match = LOGO_KEY.match(item.key)
        if not match or match.group("name") != "manifest.json":
            continue
        try:
            names = json.loads(bucket.read(item.key))
            found[match.group("org")] = {str(name) for name in names} if isinstance(names, list) else None
        except (ReconcileError, ValueError, UnicodeDecodeError, OSError):
            found[match.group("org")] = None
    return found


def reconcile(bucket: Bucket, references: References, *, now: datetime, min_age_days: int = MIN_AGE_DAYS) -> Report:
    """What the bucket holds that nothing references. Reads the bucket and the database, deletes nothing."""
    if min_age_days < MIN_AGE_FLOOR_DAYS:
        raise ReconcileError(f"the safety age cannot be shorter than {MIN_AGE_FLOOR_DAYS} days")
    objects = sorted(bucket.list(), key=lambda entry: entry.key)
    report = Report()
    referenced: dict[str, set[str]] = {}
    for tenant in sorted({m.group("tenant") for item in objects if (m := TENANT_KEY.match(item.key))}):
        try:
            referenced[tenant] = references(tenant)
        except Exception as exc:  # noqa: BLE001 - any failure means: do not judge this tenant's objects
            report.unreadable_tenants[tenant] = type(exc).__name__
    manifests = _logo_manifests(bucket, objects)

    def keep(item: BucketObject) -> None:
        report.kept += 1
        report.kept_bytes += item.size

    for item in objects:
        age = age_days(item, now)
        tenant_match = TENANT_KEY.match(item.key)
        logo = LOGO_KEY.match(item.key)
        if tenant_match:
            tenant = tenant_match.group("tenant")
            if tenant in report.unreadable_tenants:
                report.skipped.append(Finding(item, f"the database references of tenant {tenant} could not be read", age, False, tenant))
            elif item.key in referenced[tenant]:
                keep(item)
            elif tenant_match.group("kind") not in KNOWN_KINDS:
                report.skipped.append(Finding(item, f"a file kind this script does not know ({tenant_match.group('kind')})", age, False, tenant))
            else:
                reason = {
                    "files": "a file or file version no row references (deleted file, emptied vault trash, call recording)",
                    "mail": "a mail attachment no row references (expired or deleted draft)",
                    "lifecycle": "an organisation export no job references",
                }[tenant_match.group("kind")]
                report.orphans.append(Finding(item, reason, age, age >= min_age_days, tenant))
        elif logo:
            listed = manifests.get(logo.group("org"))
            if logo.group("name") == "manifest.json" or (listed is not None and logo.group("name") in listed):
                keep(item)
            elif logo.group("org") in manifests and listed is None:
                report.skipped.append(Finding(item, "a public logo whose manifest cannot be read", age, False))
            else:
                report.orphans.append(Finding(item, "a public logo its organisation's manifest does not list", age, age >= min_age_days))
        else:
            report.skipped.append(Finding(item, "not an ANUM file key", age, False))
    return report


def _size(value: int) -> str:
    for unit, size in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if value >= size:
            return f"{value / size:.1f} {unit}"
    return f"{value} B"


def run(bucket: Bucket, references: References, *, now: datetime, delete: bool, min_age_days: int,
        out: Callable[[str], None]) -> int:
    report = reconcile(bucket, references, now=now, min_age_days=min_age_days)
    out(f"Kept: {report.kept} objects ({_size(report.kept_bytes)}) referenced by the database.")
    for tenant, error in sorted(report.unreadable_tenants.items()):
        out(f"Not checked: tenant {tenant}, its references could not be read ({error}). Its objects are kept.")
    if report.skipped:
        out(f"Left alone: {len(report.skipped)} objects this script will not judge (never deleted).")
        for finding in report.skipped:
            out(f"  {finding.item.key}  {_size(finding.item.size)}  {finding.age_days:.1f} days  {finding.reason}")
    if not report.orphans:
        out("No orphans: every ANUM file in the bucket is referenced.")
        return 0
    out(f"Orphans: {len(report.orphans)} ({_size(sum(f.item.size for f in report.orphans))}).")
    for finding in report.orphans:
        when = "older than the safety age" if finding.deletable else f"younger than {min_age_days} days, kept"
        out(f"  {finding.item.key}  {_size(finding.item.size)}  {finding.age_days:.1f} days  {finding.reason}  ({when})")
    eligible = [finding for finding in report.orphans if finding.deletable]
    if not delete:
        out(f"Dry run: nothing was deleted. {len(eligible)} could be deleted with --delete.")
        return 0
    failed = deleted = 0
    fresh: dict[str, set[str]] = {}
    for finding in eligible:
        tenant = finding.tenant_id
        if tenant is not None:
            if tenant not in fresh:
                try:
                    fresh[tenant] = references(tenant)
                except Exception as exc:  # noqa: BLE001 - the re-check failed: delete nothing of this tenant
                    out(f"Not deleted {finding.item.key}: the re-check of tenant {tenant} failed ({type(exc).__name__}).")
                    failed += 1
                    continue
            if finding.item.key in fresh[tenant]:
                out(f"Kept {finding.item.key}: a row references it now.")
                continue
        try:
            bucket.delete(finding.item.key)
        except Exception as exc:  # noqa: BLE001 - reported per object (a key without delete rights refuses)
            failed += 1
            out(f"Not deleted {finding.item.key}: {_error_text(exc)}")
            continue
        deleted += 1
        out(f"Deleted {finding.item.key}")
    out(f"Deleted {deleted} of {len(eligible)}.")
    return 1 if failed else 0


def _error_text(exc: Exception) -> str:
    response = getattr(exc, "response", None) or {}
    code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
    return f"{code or type(exc).__name__}" + (f" ({exc})" if isinstance(exc, ReconcileError) else "")


class S3Bucket:
    """The API's bucket through its own settings (``ANUM_S3_*``); lists the current objects."""

    def __init__(self, client: Any, bucket: str) -> None:
        self.client, self.bucket = client, bucket

    def list(self) -> Iterable[BucketObject]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket):
            for row in page.get("Contents", []) or []:
                modified = row["LastModified"]
                if modified.tzinfo is None:
                    modified = modified.replace(tzinfo=UTC)
                yield BucketObject(key=row["Key"], size=int(row.get("Size") or 0), modified=modified)

    def read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()  # type: ignore[no-any-return]

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def postgres_references(tenant_id: str, session_factory: Callable[[], Any] | None = None) -> set[str]:
    """The tenant's referenced keys, read as ``anum_maintenance`` in the tenant's RLS context."""
    from sqlalchemy import text

    from .db.session import set_tenant_context
    from .maintenance import _SET_MAINTENANCE_ROLE, default_session_factory

    session = (session_factory or default_session_factory)()
    try:
        session.execute(text("set transaction read only"))
        set_tenant_context(session, tenant_id)
        session.execute(_SET_MAINTENANCE_ROLE)
        keys: set[str] = set()
        for statement in _REFERENCES:
            keys.update(row[0] for row in session.execute(text(statement), {"tenant": tenant_id}) if row[0])
        session.rollback()
        return keys
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def main(argv: Sequence[str] | None = None, *, bucket: Bucket | None = None, references: References | None = None,
         now: datetime | None = None, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(description="List (and with --delete remove) Files bucket objects no database row references.")
    parser.add_argument("--delete", action="store_true", help="delete the orphans older than the safety age")
    parser.add_argument("--min-age-days", type=int, default=MIN_AGE_DAYS,
                        help=f"never delete anything younger than this many days (default {MIN_AGE_DAYS}, at least {MIN_AGE_FLOOR_DAYS})")
    args = parser.parse_args(argv)
    try:
        if bucket is None or references is None:
            from .settings import settings

            if settings.object_storage_backend != "s3" or settings.repository_backend != "postgresql":
                raise ReconcileError("run it with the API's settings: ANUM_OBJECT_STORAGE_BACKEND=s3 and ANUM_REPOSITORY_BACKEND=postgresql")
            if bucket is None:
                from .files import create_s3_client

                bucket = S3Bucket(create_s3_client(settings), settings.s3_bucket)
            references = references or postgres_references
        return run(bucket, references, now=now or datetime.now(UTC), delete=args.delete,
                   min_age_days=args.min_age_days, out=out)
    except ReconcileError as exc:
        out(f"ERROR: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - listing the bucket failed: say what, never a traceback with settings
        out(f"ERROR: the bucket or the database could not be read ({_error_text(exc)}). Nothing was deleted.")
        return 2


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["BucketObject", "Finding", "Report", "S3Bucket", "main", "postgres_references", "reconcile", "run"]
