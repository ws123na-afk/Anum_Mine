"""Restore drill against the test PostgreSQL (docs/runbooks.md#backup-and-restore).

Runs ``infra/backup/anum_backup.py drill`` as a subprocess, exactly as an operator
would: pg_dump (custom format, one snapshot) -> restore into a fresh
``anum_restore_*`` database -> verify revision, policies, RLS flags, exact row counts
per table and per tenant, and RLS isolation as a non-superuser role -> drop.

Needs the PostgreSQL client tools (pg_dump, pg_restore >= the server version) on
PATH, in ANUM_PG_BIN or in /usr/lib/postgresql/16/bin.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from anum_api.events import CanonicalEventName, create_event
from anum_api.schemas import Task, TaskStatus

from conftest import FIXED_NOW, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B, tenant_context

pytestmark = pytest.mark.database

SCRIPT = Path(__file__).parents[3] / "infra" / "backup" / "anum_backup.py"


def _seed(repository_factory) -> dict[str, int]:
    counts = {TENANT_A: 0, TENANT_B: 0}
    for tenant, workspace, how_many in ((TENANT_A, WORKSPACE_A, 3), (TENANT_B, WORKSPACE_B, 2)):
        context = tenant_context(tenant, workspace)
        with repository_factory(context, commit=True) as repository:
            for index in range(how_many):
                task = Task(
                    id=f"task_drill_{tenant}_{index}",
                    title="Drill",
                    prompt="Restore drill fixture",
                    status=TaskStatus.CREATED,
                    tenant_id=tenant,
                    workspace_id=workspace,
                    created_at=FIXED_NOW,
                    updated_at=FIXED_NOW,
                )
                repository.create_task(task)
                repository.record_event(
                    create_event(
                        CanonicalEventName.TASK_CREATED,
                        context,
                        task.id,
                        {"task_id": task.id},
                        created_at=FIXED_NOW + timedelta(seconds=index),
                    ).event
                )
                counts[tenant] += 1
    return counts


def _run(*args: str, test_database_url: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ}
    env.pop("PGPASSWORD", None)
    return subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_restore_drill_round_trips_every_tenant_and_keeps_rls(
    database_engine: Engine, repository_factory, seed_scopes, tmp_path: Path, test_database_url: str
) -> None:
    counts = _seed(repository_factory)
    result = _run(
        "drill",
        "--database-url",
        test_database_url,
        "--admin-url",
        test_database_url,
        "--work-dir",
        str(tmp_path / "backups"),
        test_database_url=test_database_url,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    report = json.loads(result.stdout)

    assert report["result"] == "passed"
    tables = report["verify"]["tables"]
    assert tables >= 10
    assert report["verify"]["tenants"] >= 2
    isolation = report["verify"]["rls_isolation"]
    assert isolation["tables"]["tasks"]["other_tenants"] == 0
    assert isolation["tables"]["tasks"]["without_context"] == 0
    assert isolation["visible_in_scope"] > 0

    manifest = json.loads(Path(report["backup"]["manifest"]).read_text())
    assert manifest["tables"]["tasks"]["by_tenant"] == counts
    assert manifest["tables"]["tasks"]["force_rls"] is True
    assert "domain_events.outbox_relay_read" in manifest["policies"]
    assert manifest["alembic_revision"]
    # Dump and manifest hold every tenant's data: owner-only permissions.
    for path in (Path(report["backup"]["dump"]), Path(report["backup"]["manifest"])):
        assert path.stat().st_mode & 0o077 == 0
    # The scratch database is dropped and the probe role removed after the drill.
    with database_engine.connect() as connection:
        assert connection.execute(
            text("select count(*) from pg_database where datname like 'anum_restore_%'")
        ).scalar_one() == 0
        assert connection.execute(
            text("select count(*) from pg_roles where rolname like 'anum_restore_probe_%'")
        ).scalar_one() == 0
    # Nothing secret in the report (the URL carries the password in CI).
    assert "postgresql" not in result.stdout


def test_verify_fails_when_a_restore_lost_rows_or_rls(
    database_engine: Engine, repository_factory, seed_scopes, tmp_path: Path, test_database_url: str
) -> None:
    _seed(repository_factory)
    backups = tmp_path / "backups"
    backed_up = _run("backup", "--database-url", test_database_url, "--out", str(backups),
                     test_database_url=test_database_url)
    assert backed_up.returncode == 0, backed_up.stderr[-3000:]
    report = json.loads(backed_up.stdout)
    target = f"anum_restore_tamper_{os.getpid()}"
    restored = _run("restore", "--admin-url", test_database_url, "--dump", report["dump"],
                    "--manifest", report["manifest"], "--target", target,
                    test_database_url=test_database_url)
    assert restored.returncode == 0, restored.stderr[-3000:]
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool

    restored_url = make_url(test_database_url).set(database=target)
    engine = create_engine(restored_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text("alter table tasks no force row level security"))
            connection.execute(text("delete from tasks where tenant_id = :tenant"), {"tenant": TENANT_B})
        verified = _run(
            "verify",
            "--database-url",
            restored_url.render_as_string(hide_password=False),
            "--manifest",
            report["manifest"],
            test_database_url=test_database_url,
        )
    finally:
        engine.dispose()
        with database_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text(f'drop database if exists "{target}" with (force)'))

    assert verified.returncode == 1
    error = json.loads(verified.stderr)["error"]
    assert "tasks.rows" in error
    assert "tasks.force_rls" in error
    assert "postgresql" not in verified.stderr

    # Restores never overwrite: an existing target is refused.
    again = _run("restore", "--admin-url", test_database_url, "--dump", report["dump"],
                 "--target", "anum_test", test_database_url=test_database_url)
    assert again.returncode == 1
    assert "anum_restore_" in json.loads(again.stderr)["error"]
