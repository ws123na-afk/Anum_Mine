"""PostgreSQL side of the organisation export and purge (docs/organisation-lifecycle.md).

* ``SqlJobStore``: ``tenant_lifecycle_jobs`` through a session whose tenant context is set
  (application role, forced RLS on ``anum.tenant_id``).
* ``discover_due_jobs``: one rolled-back transaction as ``anum_maintenance``.
* ``PostgresTenantData``: the export reads each batch in its own short transaction as
  the application role inside the workspace's RLS context; the purge deletes each batch
  in its own short transaction as ``anum_tenant_purger`` (``SET LOCAL ROLE``) with the
  tenant context set. Neither holds a transaction while files are copied or deleted.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import maintenance
from ..settings import settings
from ..tenant_lifecycle import (
    DELETED_ORGANISATION,
    DELETED_WORKSPACE,
    LEASE,
    SYSTEM_ACTOR,
    JobConflict,
    JobKind,
    JobStatus,
    LifecycleJob,
    _ordered,
)
from ..tenant_lifecycle_tables import BY_NAME, PURGED_TABLES, TenantTable

PURGER_ROLE = "anum_tenant_purger"
_COLUMNS = (
    "id, tenant_id, workspace_id, kind, status, requested_by, requested_at, purge_after, started_at, finished_at, "
    "cancelled_at, cancelled_by, lease_until, expires_at, archive_key, archive_bytes, archive_sha256, row_count, "
    "file_count, counts, error, updated_at"
)


def _job(row: Any) -> LifecycleJob:
    data = dict(row._mapping)
    data["counts"] = data.get("counts") or {}
    return LifecycleJob(**data)


def _params(job: LifecycleJob) -> dict[str, Any]:
    data = job.model_dump()
    data["kind"] = job.kind.value
    data["status"] = job.status.value
    data["counts"] = json.dumps(job.counts)
    return data


class SqlJobStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_jobs(self, tenant_id: str, *, kind: JobKind | None = None, limit: int = 20) -> list[LifecycleJob]:
        clause = "and kind = :kind" if kind is not None else ""
        rows = self.session.execute(
            text(f"select {_COLUMNS} from tenant_lifecycle_jobs where tenant_id = :tenant {clause} order by requested_at desc, id desc limit :limit"),  # nosec B608 - constant text
            {"tenant": tenant_id, "kind": kind.value if kind else None, "limit": limit},
        )
        return [_job(row) for row in rows]

    def get(self, tenant_id: str, job_id: str) -> LifecycleJob | None:
        row = self.session.execute(
            text(f"select {_COLUMNS} from tenant_lifecycle_jobs where tenant_id = :tenant and id = :id"),  # nosec B608 - constant text
            {"tenant": tenant_id, "id": job_id},
        ).first()
        return _job(row) if row is not None else None

    def insert(self, job: LifecycleJob) -> LifecycleJob:
        names = [name.strip() for name in _COLUMNS.split(",")]
        values = ", ".join("cast(:counts as jsonb)" if name == "counts" else f":{name}" for name in names)
        try:
            with self.session.begin_nested():
                self.session.execute(text(f"insert into tenant_lifecycle_jobs ({_COLUMNS}) values ({values})"), _params(job))  # nosec B608 - constant text
        except IntegrityError as exc:
            raise JobConflict from exc
        return job

    def save(self, job: LifecycleJob) -> None:
        names = [name.strip() for name in _COLUMNS.split(",") if name.strip() not in ("id", "tenant_id")]
        assignments = ", ".join("counts = cast(:counts as jsonb)" if n == "counts" else f"{n} = :{n}" for n in names)
        self.session.execute(
            text(f"update tenant_lifecycle_jobs set {assignments} where tenant_id = :tenant_id and id = :id"),  # nosec B608 - constant text
            _params(job),
        )

    def claim(
        self, tenant_id: str, job_id: str, from_statuses: frozenset[JobStatus], to: JobStatus, now: datetime
    ) -> LifecycleJob | None:
        row = self.session.execute(
            text(
                f"""
                update tenant_lifecycle_jobs
                set status = :to, lease_until = :lease, started_at = coalesce(started_at, :now), updated_at = :now
                where tenant_id = :tenant and id = :id and status = any(:from_statuses)
                  and (lease_until is null or lease_until <= :now)
                  and (status <> 'scheduled' or purge_after <= :now)
                returning {_COLUMNS}
                """  # nosec B608 - constant text
            ),
            {
                "to": to.value,
                "lease": now + LEASE,
                "now": now,
                "tenant": tenant_id,
                "id": job_id,
                "from_statuses": sorted(s.value for s in from_statuses),
            },
        ).first()
        return _job(row) if row is not None else None

    def cancel(self, tenant_id: str, job_id: str, by: str, now: datetime) -> LifecycleJob | None:
        # Conditional on 'scheduled': a purge that claimed the job first wins.
        row = self.session.execute(
            text(
                f"""
                update tenant_lifecycle_jobs
                set status = 'cancelled', cancelled_at = :now, cancelled_by = :by, updated_at = :now
                where tenant_id = :tenant and id = :id and status = 'scheduled'
                returning {_COLUMNS}
                """  # nosec B608 - constant text
            ),
            {"now": now, "by": by, "tenant": tenant_id, "id": job_id},
        ).first()
        return _job(row) if row is not None else None

    def delete_except(self, tenant_id: str, keep_id: str) -> list[LifecycleJob]:
        rows = self.session.execute(
            text(f"delete from tenant_lifecycle_jobs where tenant_id = :tenant and id <> :keep returning {_COLUMNS}"),  # nosec B608 - constant text
            {"tenant": tenant_id, "keep": keep_id},
        )
        return [_job(row) for row in rows]


_DUE = text(
    """
    select id, tenant_id, workspace_id, kind, status, purge_after, lease_until, expires_at
    from tenant_lifecycle_jobs
    where status = 'queued'
       or (status in ('running', 'purging') and (lease_until is null or lease_until <= :now))
       or (status = 'scheduled' and purge_after <= :now)
       or (status = 'ready' and expires_at <= :now)
    order by purge_after nulls first, id
    limit 50
    """
)


def discover_due_jobs(now: datetime) -> list[LifecycleJob]:
    """Jobs that need work in every tenant, as ``anum_maintenance`` (scope columns only)."""
    rows = maintenance.discover(maintenance.default_session_factory, _DUE, {"now": now})
    return [
        LifecycleJob(
            id=row.id,
            tenant_id=row.tenant_id,
            workspace_id=row.workspace_id,
            kind=row.kind,
            status=row.status,
            requested_by="",
            requested_at=now,
            purge_after=row.purge_after,
            lease_until=row.lease_until,
            expires_at=row.expires_at,
            updated_at=now,
        )
        for row in rows
    ]


_EXPORT_WORKSPACES = text(
    "select id from workspaces where tenant_id = :tenant order by id"
)
_PRIMARY_KEY = text(
    """
    select a.attname
    from pg_index i
    join pg_attribute a on a.attrelid = i.indrelid and a.attnum = any(i.indkey)
    where i.indrelid = cast(:table as regclass) and i.indisprimary
    order by array_position(cast(i.indkey as int2[]), a.attnum)
    """
)
_FOREIGN_KEYS = text(
    """
    select cast(conrelid as regclass)::text as child, cast(confrelid as regclass)::text as parent
    from pg_constraint where contype = 'f'
    """
)


def _session() -> Session:
    from . import session as db_session

    return db_session.SessionLocal()


class PostgresTenantData:
    def __init__(self) -> None:
        self._keys: dict[str, list[str]] = {}

    def workspace_ids(self, tenant_id: str) -> list[str]:
        # Admitted by the maintenance policy only while an export of this tenant runs.
        rows = maintenance.discover(maintenance.default_session_factory, _EXPORT_WORKSPACES, {"tenant": tenant_id})
        return [row.id for row in rows]

    def _primary_key(self, session: Session, table: str) -> list[str]:
        if table not in self._keys:
            self._keys[table] = [row.attname for row in session.execute(_PRIMARY_KEY, {"table": table})]
        return self._keys[table]

    def export_batches(self, tenant_id: str, workspace_id: str, table: TenantTable) -> Iterator[list[dict[str, Any]]]:
        if table.name not in BY_NAME:
            raise ValueError(table.name)
        size = settings.tenant_export_batch_rows
        last: tuple[Any, ...] | None = None
        while True:
            with maintenance.scoped_unit(maintenance.default_session_factory, tenant_id, workspace_id) as session:
                keys = self._primary_key(session, table.name) or ["ctid"]
                scope = "tenant_id = :tenant"
                params: dict[str, Any] = {"tenant": tenant_id, "limit": size}
                if table.workspace_column is not None:
                    scope += f" and {table.workspace_column} = :workspace"
                    params["workspace"] = workspace_id
                order = ", ".join(keys)
                after = ""
                if last is not None:
                    names = [f"k{i}" for i in range(len(keys))]
                    if keys == ["ctid"]:
                        after = " and ctid > cast(:k0 as tid)"
                    else:
                        after = f" and ({order}) > ({', '.join(':' + n for n in names)})"
                    params.update(dict(zip(names, last, strict=True)))
                select_list = "*, ctid::text as anum_ctid" if keys == ["ctid"] else "*"
                rows = [
                    dict(row._mapping)
                    for row in session.execute(
                        text(f"select {select_list} from {table.name} where {scope}{after} order by {order} limit :limit"),  # nosec B608 - registry table names, bound values
                        params,
                    )
                ]
            if not rows:
                return
            if keys == ["ctid"]:
                last = (rows[-1]["anum_ctid"],)
                for row in rows:
                    row.pop("anum_ctid", None)
            else:
                last = tuple(rows[-1][key] for key in keys)
            yield rows
            if len(rows) < size:
                return

    def read_object(self, key: str) -> bytes:
        from ..files import file_store

        return file_store.storage.get(key)

    def purge_order(self) -> list[TenantTable]:
        session = _session()
        try:
            edges = [(row.child, row.parent) for row in session.execute(_FOREIGN_KEYS)]
            session.rollback()
        finally:
            session.close()
        return _ordered(list(PURGED_TABLES), edges)

    def purge_batch(self, job: LifecycleJob, table: TenantTable, keep_workspace: str) -> tuple[int, int]:
        """Delete up to one batch of the covered rows as the purge role; returns (rows, files)."""
        from ..files import file_store
        from .session import set_tenant_context

        scope = "tenant_id = :tenant"
        params: dict[str, Any] = {"tenant": job.tenant_id, "limit": settings.tenant_purge_batch_rows}
        if job.kind == JobKind.WORKSPACE_DELETION:
            if table.workspace_column is None:
                return 0, 0
            scope += f" and {table.workspace_column} = :workspace"
            params["workspace"] = job.workspace_id
        if table.name == "workspaces":
            scope += " and id <> :keep"
            params["keep"] = keep_workspace
        key_column = table.storage_key_column
        select_list = f"ctid::text as row_ctid{', ' + key_column + ' as object_key' if key_column else ''}"
        session = _session()
        try:
            session.execute(text(f"set local role {PURGER_ROLE}"))
            set_tenant_context(session, job.tenant_id)
            found = session.execute(
                text(f"select {select_list} from {table.name} where {scope} limit :limit"),  # nosec B608 - registry table names, bound values
                params,
            ).all()
            if not found:
                session.rollback()
                return 0, 0
            objects = [row.object_key for row in found if key_column and row.object_key]
            deleted = session.execute(
                text(f"delete from {table.name} where ctid = any(cast(:ctids as tid[]))"),  # nosec B608 - registry table name
                {"ctids": [row.row_ctid for row in found]},
            ).rowcount
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()
        # The rows are gone; now the bytes, with no transaction open.
        for key in objects:
            file_store.storage.delete(key)
        return int(deleted or 0), len(objects)


def tombstone(session: Session, tenant_id: str, workspace_id: str, *, organisation: bool, now: datetime) -> None:
    """The receipt's tenant and workspace rows, in the application role's transaction."""
    if organisation:
        session.execute(
            text("update tenants set name = :name, status = 'deleted', updated_at = :now where id = :tenant"),
            {"name": DELETED_ORGANISATION, "now": now, "tenant": tenant_id},
        )
    session.execute(
        text(
            "update workspaces set name = :name, archived_at = coalesce(archived_at, :now), archived_by = :by, updated_at = :now "
            "where tenant_id = :tenant and id = :workspace"
        ),
        {"name": DELETED_WORKSPACE, "now": now, "by": SYSTEM_ACTOR, "tenant": tenant_id, "workspace": workspace_id},
    )


# ----------------------------------------------------------------------------- console receipts

_RECEIPT_COLUMNS = (
    "tenant_id, job_id, operator_user_id, display_name, plan, requested_by, requested_at, purge_started_at, deleted_at, "
    "status, row_count, file_count, counts, identity_status, identity_removed, identity_detached, identity_finished_at"
)


def _receipt(row: Any) -> Any:
    from ..tenant_lifecycle import DeletedOrganisation

    data = dict(row._mapping)
    data["counts"] = data.get("counts") or {}
    return DeletedOrganisation(**data)


class SqlReceiptStore:
    """``deleted_organizations`` through the application role's session (tenant context set)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, tenant_id: str) -> Any:
        row = self.session.execute(
            text(f"select {_RECEIPT_COLUMNS}, identity_attempts from deleted_organizations where tenant_id = :t"),  # nosec B608 - constant text
            {"t": tenant_id},
        ).first()
        return _receipt(row) if row is not None else None

    def open(self, job: LifecycleJob, now: datetime) -> None:
        # Copied from platform_organizations before the purge removes it.
        self.session.execute(
            text(
                """
                insert into deleted_organizations
                  (tenant_id, job_id, operator_user_id, display_name, plan, requested_by, requested_at, purge_started_at)
                select cast(:t as varchar), :job, p.operator_user_id, p.display_name, p.plan, :by, :at, :now
                from (select 1) as one
                left join platform_organizations p on p.tenant_id = :t
                on conflict (tenant_id) do nothing
                """
            ),
            {"t": job.tenant_id, "job": job.id, "by": job.requested_by, "at": job.requested_at, "now": now},
        )

    def save(self, record: Any) -> None:
        self.session.execute(
            text(
                """
                update deleted_organizations set identity_status = :identity_status, identity_removed = :identity_removed,
                  identity_detached = :identity_detached, identity_attempts = :identity_attempts,
                  identity_finished_at = :identity_finished_at, updated_at = now()
                where tenant_id = :tenant_id
                """
            ),
            record.model_dump(
                include={"tenant_id", "identity_status", "identity_removed", "identity_detached", "identity_attempts", "identity_finished_at"}
            ),
        )

    def complete(self, tenant_id: str, now: datetime, counts: dict[str, int], files: int, identity: str) -> None:
        self.session.execute(
            text(
                """
                update deleted_organizations set status = 'deleted', deleted_at = :now, counts = cast(:counts as jsonb),
                  row_count = :rows, file_count = :files, identity_status = :identity, updated_at = :now
                where tenant_id = :t
                """
            ),
            {"t": tenant_id, "now": now, "counts": json.dumps(counts), "rows": sum(counts.values()), "files": files, "identity": identity},
        )


_IDENTITY_DUE = text(
    "select tenant_id from deleted_organizations where status = 'deleted' and identity_status = 'pending' order by tenant_id limit 50"
)


def discover_identity_cleanups() -> list[str]:
    """Deleted organisations whose Keycloak cleanup is still to do, as ``anum_maintenance``."""
    return [row.tenant_id for row in maintenance.discover(maintenance.default_session_factory, _IDENTITY_DUE)]


def deleted_for_operator(operator: str) -> list[Any]:
    """The operator's deleted organisations, through ``anum_platform_reader`` (rolled back)."""
    session = _session()
    try:
        session.execute(text("set local role anum_platform_reader"))
        session.execute(text("select set_config('anum.user_id', :user_id, true)"), {"user_id": operator})
        rows = session.execute(
            text(f"select {_RECEIPT_COLUMNS} from deleted_organizations where operator_user_id = :operator"),  # nosec B608 - constant text
            {"operator": operator},
        ).all()
        return [_receipt(row) for row in rows]
    finally:
        session.rollback()
        session.close()
