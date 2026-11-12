"""The one-off Files bucket reconcile (anum_api/files_reconcile.py, docs/deploy-oracle.md#reconciling-the-files-bucket),
against a fake bucket and a fake reference reader."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from anum_api import files_reconcile
from anum_api.files_reconcile import BucketObject, ReconcileError, main, reconcile

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
T1, T2 = "tenant_alpha", "tenant_beta"
LOGO_ORG = "a" * 32


def key(tenant: str, kind: str, *rest: str) -> str:
    return "/".join(("tenants", tenant, "workspaces", "workspace_main", kind, *rest))


class FakeBucket:
    def __init__(self, objects: dict[str, tuple[int, float]], contents: dict[str, bytes] | None = None,
                 refuse: set[str] | None = None) -> None:
        self.objects = {k: BucketObject(k, size, NOW - timedelta(days=days)) for k, (size, days) in objects.items()}
        self.contents = contents or {}
        self.refuse = refuse or set()
        self.deleted: list[str] = []

    def list(self):  # type: ignore[no-untyped-def]
        return list(self.objects.values())

    def read(self, key: str) -> bytes:
        if key not in self.contents:
            raise ReconcileError("missing")
        return self.contents[key]

    def delete(self, key: str) -> None:
        if key in self.refuse:
            raise PermissionError("AccessDenied")
        self.deleted.append(key)
        self.objects.pop(key)


LIVE_FILE = key(T1, "files", "file_1", "a" * 64)
OLD_VERSION = key(T1, "files", "file_1", "b" * 64)
VAULT_TRASH = key(T1, "files", "file_trashed", "c" * 64)
RECORDING = key(T1, "files", "file_call", "d" * 64)
MAIL_EXPIRED = key(T1, "mail", "att_1", "e" * 64)
EXPORT = key(T1, "lifecycle", "job_1", "export.zip")
FRESH_UPLOAD = key(T1, "files", "file_new", "f" * 64)
OTHER_TENANT = key(T2, "files", "file_9", "9" * 64)
UNKNOWN_KIND = key(T1, "thumbnails", "x.png")
STRAY = "scratch/notes.txt"
LOGO_LISTED = f"public-logos/{LOGO_ORG}/{'1' * 64}.png"
LOGO_UNLISTED = f"public-logos/{LOGO_ORG}/{'2' * 64}.png"
LOGO_MANIFEST = f"public-logos/{LOGO_ORG}/manifest.json"


def bucket(**kwargs) -> FakeBucket:  # type: ignore[no-untyped-def]
    return FakeBucket(
        {
            LIVE_FILE: (100, 400), OLD_VERSION: (90, 400), VAULT_TRASH: (2048, 60), RECORDING: (5 << 20, 45),
            MAIL_EXPIRED: (700, 30), EXPORT: (1 << 30, 20), FRESH_UPLOAD: (10, 0.2), OTHER_TENANT: (5, 100),
            UNKNOWN_KIND: (1, 100), STRAY: (1, 100), LOGO_LISTED: (300, 100), LOGO_UNLISTED: (300, 100), LOGO_MANIFEST: (50, 100),
        },
        contents={LOGO_MANIFEST: json.dumps([f"{'1' * 64}.png"]).encode()},
        **kwargs,
    )


REFERENCED = {T1: {LIVE_FILE, OLD_VERSION}, T2: {OTHER_TENANT}}


def references(tenant: str) -> set[str]:
    return set(REFERENCED.get(tenant, set()))


def test_referenced_objects_are_kept_and_every_kind_of_orphan_is_found() -> None:
    report = reconcile(bucket(), references, now=NOW)
    orphans = {f.item.key: f for f in report.orphans}
    assert set(orphans) == {VAULT_TRASH, RECORDING, MAIL_EXPIRED, EXPORT, FRESH_UPLOAD, LOGO_UNLISTED}
    assert report.kept == 5  # the live file, its old version, the other tenant's file, the listed logo, the manifest
    assert "mail attachment" in orphans[MAIL_EXPIRED].reason and "export" in orphans[EXPORT].reason
    assert orphans[FRESH_UPLOAD].deletable is False, "younger than the safety age"
    assert all(orphans[k].deletable for k in (VAULT_TRASH, RECORDING, MAIL_EXPIRED, EXPORT, LOGO_UNLISTED))
    skipped = {f.item.key: f.reason for f in report.skipped}
    assert set(skipped) == {UNKNOWN_KIND, STRAY}
    assert all(not f.deletable for f in report.skipped)


def test_a_dry_run_deletes_nothing_and_says_what_it_would_delete() -> None:
    lines: list[str] = []
    fake = bucket()
    assert main([], bucket=fake, references=references, now=NOW, out=lines.append) == 0
    assert fake.deleted == []
    text = "\n".join(lines)
    assert "Dry run: nothing was deleted. 5 could be deleted with --delete." in text
    assert VAULT_TRASH in text and "1.0 GiB" in text and "younger than 7 days, kept" in text
    assert "not an ANUM file key" in text and "never deleted" in text


def test_delete_removes_only_old_orphans_and_rechecks_the_database_first() -> None:
    fake = bucket()
    calls: list[str] = []

    def rereading(tenant: str) -> set[str]:
        calls.append(tenant)
        found = references(tenant)
        # A row that appeared between the listing and the delete wins: that object stays.
        if len([c for c in calls if c == T1]) > 1:
            found.add(RECORDING)
        return found

    lines: list[str] = []
    assert main(["--delete"], bucket=fake, references=rereading, now=NOW, out=lines.append) == 0
    assert set(fake.deleted) == {VAULT_TRASH, MAIL_EXPIRED, EXPORT, LOGO_UNLISTED}
    assert RECORDING in fake.objects and FRESH_UPLOAD in fake.objects and LIVE_FILE in fake.objects
    assert UNKNOWN_KIND in fake.objects and STRAY in fake.objects and LOGO_LISTED in fake.objects
    assert f"Kept {RECORDING}: a row references it now." in lines
    assert "Deleted 4 of 5." in lines


def test_refused_deletes_are_reported_and_fail_the_run() -> None:
    fake = bucket(refuse={MAIL_EXPIRED})
    lines: list[str] = []
    assert main(["--delete"], bucket=fake, references=references, now=NOW, out=lines.append) == 1
    assert f"Not deleted {MAIL_EXPIRED}: PermissionError" in lines
    assert MAIL_EXPIRED in fake.objects and VAULT_TRASH in fake.deleted


def test_a_tenant_whose_references_cannot_be_read_is_never_judged() -> None:
    def failing(tenant: str) -> set[str]:
        if tenant == T1:
            raise RuntimeError("database unavailable")
        return references(tenant)

    fake = bucket()
    lines: list[str] = []
    assert main(["--delete"], bucket=fake, references=failing, now=NOW, out=lines.append) == 0
    assert all(not k.startswith(f"tenants/{T1}/") for k in fake.deleted)
    assert fake.deleted == [LOGO_UNLISTED]
    assert any(line.startswith(f"Not checked: tenant {T1}") and "RuntimeError" in line for line in lines)


def test_the_safety_age_has_a_floor_and_can_be_raised() -> None:
    lines: list[str] = []
    assert main(["--min-age-days", "1"], bucket=bucket(), references=references, now=NOW, out=lines.append) == 2
    assert lines == ["ERROR: the safety age cannot be shorter than 2 days"]
    report = reconcile(bucket(), references, now=NOW, min_age_days=50)
    deletable = {f.item.key for f in report.orphans if f.deletable}
    assert deletable == {VAULT_TRASH, LOGO_UNLISTED}


def test_an_unreadable_logo_manifest_keeps_that_organisation_s_logos() -> None:
    fake = bucket()
    fake.contents[LOGO_MANIFEST] = b"not json"
    report = reconcile(fake, references, now=NOW)
    assert LOGO_UNLISTED not in {f.item.key for f in report.orphans}
    assert {f.item.key for f in report.skipped} >= {LOGO_LISTED, LOGO_UNLISTED}


def test_an_empty_bucket_and_a_clean_bucket_say_so() -> None:
    lines: list[str] = []
    clean = FakeBucket({LIVE_FILE: (1, 400)})
    assert main([], bucket=clean, references=references, now=NOW, out=lines.append) == 0
    assert "No orphans: every ANUM file in the bucket is referenced." in lines


def test_a_failed_listing_reports_without_a_traceback() -> None:
    class Broken(FakeBucket):
        def list(self):  # type: ignore[no-untyped-def]
            raise OSError("connection refused")

    lines: list[str] = []
    assert main([], bucket=Broken({}), references=references, now=NOW, out=lines.append) == 2
    assert lines == ["ERROR: the bucket or the database could not be read (OSError). Nothing was deleted."]


def test_without_the_api_s_bucket_settings_it_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.settings import settings

    monkeypatch.setattr(settings, "object_storage_backend", "local")
    lines: list[str] = []
    assert main([], out=lines.append) == 2
    assert "ANUM_OBJECT_STORAGE_BACKEND=s3" in lines[0]


def test_the_s3_adapter_pages_through_the_whole_bucket() -> None:
    pages = [
        {"Contents": [{"Key": "a", "Size": 1, "LastModified": NOW}]},
        {"Contents": [{"Key": "b", "Size": 2, "LastModified": NOW.replace(tzinfo=None)}]},
        {},
    ]

    class Paginator:
        def paginate(self, **kwargs):  # type: ignore[no-untyped-def]
            assert kwargs == {"Bucket": "files"}
            return iter(pages)

    class Client:
        deleted: list[str] = []

        def get_paginator(self, name: str) -> Paginator:
            assert name == "list_objects_v2"
            return Paginator()

        def delete_object(self, *, Bucket: str, Key: str) -> None:  # noqa: N803 - boto3's names
            self.deleted.append(f"{Bucket}/{Key}")

    client = Client()
    s3 = files_reconcile.S3Bucket(client, "files")
    listed = list(s3.list())
    assert [(o.key, o.size) for o in listed] == [("a", 1), ("b", 2)]
    assert all(o.modified.tzinfo is not None for o in listed)
    s3.delete("a")
    assert client.deleted == ["files/a"]


def test_the_reference_queries_cover_every_table_that_names_an_object() -> None:
    from anum_api.tenant_lifecycle_tables import TENANT_TABLES

    with_keys = {table.name for table in TENANT_TABLES if table.storage_key_column}
    queried = {statement.split(" from ")[1].split(" ")[0] for statement in files_reconcile._REFERENCES}
    assert with_keys <= queried and "tenant_lifecycle_jobs" in queried


def test_the_vm_runs_it_in_the_retention_job_s_container_with_the_bucket_settings() -> None:
    import pathlib

    import yaml

    oracle = pathlib.Path(__file__).resolve().parents[3] / "infra" / "oracle"
    script = (oracle / "backup.sh").read_text()
    assert 'dc run --rm --no-deps retention python -m anum_api.files_reconcile "${@:2}"' in script
    retention = yaml.safe_load((oracle / "compose.yaml").read_text())["services"]["retention"]["environment"]
    assert retention["ANUM_OBJECT_STORAGE_BACKEND"] == "s3" and retention["ANUM_REPOSITORY_BACKEND"] == "postgresql"
    assert "anum_app:" in retention["ANUM_DATABASE_URL"], "the application login, never the migration login"
