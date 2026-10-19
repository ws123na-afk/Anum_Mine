"""Organisation export and deletion, and workspace deletion (docs/organisation-lifecycle.md).

* An organisation administrator who is an owner of the request's workspace queues an
  export, gets a signed short-lived link to the finished archive, and schedules (or
  cancels) deleting the whole organisation. A workspace owner schedules (or cancels)
  deleting that workspace. Every check is server side.
* Routes only write the job row (and an audit record) in one short transaction. The
  background runner (``LifecycleRunner``, a worker thread per pass) finds due jobs as
  ``anum_maintenance``, claims each one in a short transaction, and then works with no
  transaction open: the export reads each table in batches, one short read transaction
  per batch, and the purge deletes in batches as ``anum_tenant_purger``, one short
  transaction per batch (anum_api/db/tenant_lifecycle_repository.py).
* A deletion waits for its cooling-off period and can be cancelled until the purge
  starts. What stays afterwards is a receipt: the tenant row (renamed, status
  ``deleted``), one tombstone workspace and one audit record with counts only.
"""

from __future__ import annotations

import asyncio
import base64
import csv
import dataclasses
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import tempfile
import zipfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import _unit_of_work, has_permission, memory_repository, tenant_context
from .schemas import TenantContext, new_id, utc_now
from .settings import settings
from .tenant_lifecycle_tables import ACCESS_TABLES, EXPORTED_TABLES, PURGED_TABLES, TenantTable

logger = logging.getLogger("anum_api.tenant_lifecycle")
router = APIRouter(prefix="/api/v1", tags=["organisation lifecycle"])

FORMAT = "anum.organisation-export/v1"
LEASE = timedelta(minutes=15)
DELETED_ORGANISATION = "Deleted organisation"
DELETED_WORKSPACE = "Deleted workspace"
SYSTEM_ACTOR = "system:lifecycle"

ORGANISATION_REQUIRED = (
    "Exporting or deleting the organisation requires the organisation administrator role (org_admin) "
    "and the owner role in this workspace. Ask your organisation administrator."
)
WORKSPACE_REQUIRED = "Deleting this workspace requires the owner role in it. Ask a workspace owner."

# Columns never exported: secrets of every kind, and embeddings (derived from exported text).
_SECRET_COLUMN = re.compile(
    r"(ciphertext|encrypted|_enc$|secret|password|credential|api_key|(^|_)token$|token_hash|^embedding$)"
)
NOT_INCLUDED = (
    "Secrets: every column holding a ciphertext, token, token hash, password, credential, API key or secret. "
    "Integrations have to be connected again.",
    "Embeddings: they are derived from the text that is included.",
    "Rows private to one person (conversations, voice transcripts, private memories, personal finance, private "
    "mailboxes, learning, preferences, in-app calls). Each person downloads their own from Settings > Privacy.",
    "Sign-in accounts (Keycloak) and mail kept on the mail server.",
    "Rows are read in batches, so a busy organisation's archive is not a point-in-time snapshot.",
)


class JobKind(StrEnum):
    ORGANISATION_EXPORT = "organisation_export"
    ORGANISATION_DELETION = "organisation_deletion"
    WORKSPACE_DELETION = "workspace_deletion"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"
    EXPIRED = "expired"
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    PURGING = "purging"
    COMPLETED = "completed"


OPEN_EXPORT = frozenset({JobStatus.QUEUED, JobStatus.RUNNING})
OPEN_DELETION = frozenset({JobStatus.SCHEDULED, JobStatus.PURGING})


class LifecycleJob(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    kind: JobKind
    status: JobStatus
    requested_by: str
    requested_at: datetime
    purge_after: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancelled_by: str | None = None
    lease_until: datetime | None = None
    expires_at: datetime | None = None
    archive_key: str | None = None
    archive_bytes: int | None = None
    archive_sha256: str | None = None
    row_count: int = 0
    file_count: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    error: str | None = None
    updated_at: datetime


class JobView(BaseModel):
    id: str
    kind: JobKind
    status: JobStatus
    workspace_id: str
    requested_by: str
    requested_at: datetime
    purge_after: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    cancelled_at: datetime | None
    cancelled_by: str | None
    expires_at: datetime | None
    archive_bytes: int | None
    archive_sha256: str | None
    row_count: int
    file_count: int
    error: str | None


def view(job: LifecycleJob) -> JobView:
    return JobView(**job.model_dump(exclude={"tenant_id", "lease_until", "archive_key", "counts", "updated_at"}))


class LifecycleSummary(BaseModel):
    tenant_id: str
    workspace_id: str
    workspace_name: str | None
    can_manage_organisation: bool
    can_delete_workspace: bool
    organisation_confirmation: str
    workspace_confirmation: str | None
    cooling_off_hours: int
    export_link_ttl_seconds: int
    export_retention_hours: int
    exports: list[JobView]
    organisation_deletion: JobView | None
    workspace_deletion: JobView | None


class OrganisationDeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: str = Field(min_length=1, max_length=200)


class WorkspaceDeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_name: str = Field(min_length=1, max_length=200)


class ExportLink(BaseModel):
    url: str
    expires_at: datetime


class JobConflict(Exception):
    """Another open job of the same kind (the partial unique indexes)."""


# ----------------------------------------------------------------------------- job store


class JobStore(Protocol):
    def list_jobs(self, tenant_id: str, *, kind: JobKind | None = None, limit: int = 20) -> list[LifecycleJob]: ...
    def get(self, tenant_id: str, job_id: str) -> LifecycleJob | None: ...
    def insert(self, job: LifecycleJob) -> LifecycleJob: ...
    def save(self, job: LifecycleJob) -> None: ...
    def claim(
        self, tenant_id: str, job_id: str, from_statuses: frozenset[JobStatus], to: JobStatus, now: datetime
    ) -> LifecycleJob | None: ...
    def delete_except(self, tenant_id: str, keep_id: str) -> list[LifecycleJob]: ...
    def cancel(self, tenant_id: str, job_id: str, by: str, now: datetime) -> LifecycleJob | None: ...


class MemoryJobStore:
    """Jobs for the ``memory`` backend (local runs and tests)."""

    def __init__(self) -> None:
        self.jobs: dict[str, LifecycleJob] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.jobs.clear()

    def list_jobs(self, tenant_id: str, *, kind: JobKind | None = None, limit: int = 20) -> list[LifecycleJob]:
        with self._lock:
            jobs = [j for j in self.jobs.values() if j.tenant_id == tenant_id and (kind is None or j.kind == kind)]
        return sorted(jobs, key=lambda j: (j.requested_at, j.id), reverse=True)[:limit]

    def get(self, tenant_id: str, job_id: str) -> LifecycleJob | None:
        job = self.jobs.get(job_id)
        return job if job is not None and job.tenant_id == tenant_id else None

    def insert(self, job: LifecycleJob) -> LifecycleJob:
        with self._lock:
            for other in self.jobs.values():
                if other.tenant_id != job.tenant_id or other.kind != job.kind:
                    continue
                if job.kind == JobKind.ORGANISATION_EXPORT and other.status in OPEN_EXPORT:
                    raise JobConflict
                if job.kind == JobKind.ORGANISATION_DELETION and other.status in OPEN_DELETION:
                    raise JobConflict
                if job.kind == JobKind.WORKSPACE_DELETION and other.status in OPEN_DELETION and other.workspace_id == job.workspace_id:
                    raise JobConflict
            self.jobs[job.id] = job
        return job

    def save(self, job: LifecycleJob) -> None:
        with self._lock:
            self.jobs[job.id] = job

    def claim(
        self, tenant_id: str, job_id: str, from_statuses: frozenset[JobStatus], to: JobStatus, now: datetime
    ) -> LifecycleJob | None:
        with self._lock:
            job = self.get(tenant_id, job_id)
            if job is None or job.status not in from_statuses:
                return None
            if job.lease_until is not None and job.lease_until > now:
                return None
            if job.status == JobStatus.SCHEDULED and (job.purge_after is None or job.purge_after > now):
                return None
            claimed = job.model_copy(
                update={"status": to, "lease_until": now + LEASE, "started_at": job.started_at or now, "updated_at": now}
            )
            self.jobs[job_id] = claimed
            return claimed

    def delete_except(self, tenant_id: str, keep_id: str) -> list[LifecycleJob]:
        with self._lock:
            gone = [j for j in self.jobs.values() if j.tenant_id == tenant_id and j.id != keep_id]
            for job in gone:
                self.jobs.pop(job.id, None)
        return gone

    def cancel(self, tenant_id: str, job_id: str, by: str, now: datetime) -> LifecycleJob | None:
        with self._lock:
            job = self.get(tenant_id, job_id)
            if job is None or job.status != JobStatus.SCHEDULED:
                return None
            cancelled = job.model_copy(update={"status": JobStatus.CANCELLED, "cancelled_at": now, "cancelled_by": by, "updated_at": now})
            self.jobs[job_id] = cancelled
            return cancelled

    def due(self, now: datetime) -> list[LifecycleJob]:
        with self._lock:
            return [j for j in self.jobs.values() if is_due(j, now)]


memory_lifecycle_store = MemoryJobStore()


def is_due(job: LifecycleJob, now: datetime) -> bool:
    """The same rule as the discovery query and the ``anum_maintenance`` policy."""
    lease_free = job.lease_until is None or job.lease_until <= now
    if job.status == JobStatus.QUEUED:
        return True
    if job.status in (JobStatus.RUNNING, JobStatus.PURGING):
        return lease_free
    if job.status == JobStatus.SCHEDULED:
        return job.purge_after is not None and job.purge_after <= now
    if job.status == JobStatus.READY:
        return job.expires_at is not None and job.expires_at <= now
    return False


@contextmanager
def lifecycle_unit(context: TenantContext, *, require_membership: bool = True) -> Iterator[tuple[JobStore, Any]]:
    """One transaction in the context's tenant and workspace: the job store and the
    repository (for audit records) share it, so a job change and its audit record commit
    together."""
    with _unit_of_work(context, require_membership=require_membership) as repository:
        if settings.repository_backend == "memory":
            yield memory_lifecycle_store, repository
            return
        from .db.tenant_lifecycle_repository import SqlJobStore

        yield SqlJobStore(repository.session), repository


def _system(tenant_id: str, workspace_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=SYSTEM_ACTOR, roles=["owner"])


def _audit(context: TenantContext, action: str, target: str, metadata: dict[str, Any], outcome: str = "success") -> AuditRecord:
    return AuditRecord(
        id=new_id("audit"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        actor=context.user_id,
        action=action,
        target=target,
        outcome=outcome,
        correlation_id=new_id("correlation"),
        created_at=utc_now(),
        metadata=metadata,
    )


# ----------------------------------------------------------------------------- permissions


def can_manage_organisation(context: TenantContext) -> bool:
    """org_admin of this tenant who is also an owner of the request's workspace."""
    return has_permission(context, Permission.ORGANIZATION_LIFECYCLE) and has_permission(
        context, Permission.WORKSPACE_DELETE
    )


def can_delete_workspace(context: TenantContext) -> bool:
    return has_permission(context, Permission.WORKSPACE_DELETE)


def _require(allowed: bool, detail: str) -> None:
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


# ----------------------------------------------------------------------------- routes


def _open(jobs: list[LifecycleJob], statuses: frozenset[JobStatus]) -> LifecycleJob | None:
    return next((job for job in jobs if job.status in statuses), None)


def _visible_deletion(jobs: list[LifecycleJob]) -> LifecycleJob | None:
    """The open deletion, else a failed one the person should see."""
    return _open(jobs, OPEN_DELETION) or _open(jobs, frozenset({JobStatus.FAILED}))


@router.get("/organization/lifecycle", response_model=LifecycleSummary)
def lifecycle_summary(context: TenantContext = Depends(tenant_context)) -> LifecycleSummary:
    manage = can_manage_organisation(context)
    with lifecycle_unit(context) as (store, repository):
        workspace = repository.get_workspace(context.workspace_id, context)
        exports = store.list_jobs(context.tenant_id, kind=JobKind.ORGANISATION_EXPORT) if manage else []
        org_jobs = store.list_jobs(context.tenant_id, kind=JobKind.ORGANISATION_DELETION, limit=5)
        ws_jobs = [
            job
            for job in store.list_jobs(context.tenant_id, kind=JobKind.WORKSPACE_DELETION, limit=200)
            if job.workspace_id == context.workspace_id
        ]
    org_deletion = _visible_deletion(org_jobs)
    ws_deletion = _visible_deletion(ws_jobs)
    return LifecycleSummary(
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        workspace_name=workspace.name if workspace is not None else None,
        can_manage_organisation=manage,
        can_delete_workspace=can_delete_workspace(context),
        organisation_confirmation=context.tenant_id,
        workspace_confirmation=workspace.name if workspace is not None else None,
        cooling_off_hours=settings.tenant_deletion_cooling_off_hours,
        export_link_ttl_seconds=settings.tenant_export_link_ttl_seconds,
        export_retention_hours=settings.tenant_export_retention_hours,
        exports=[view(job) for job in exports],
        organisation_deletion=view(org_deletion) if org_deletion is not None else None,
        workspace_deletion=view(ws_deletion) if ws_deletion is not None else None,
    )


def _new_job(context: TenantContext, kind: JobKind, job_status: JobStatus, **extra: Any) -> LifecycleJob:
    now = utc_now()
    return LifecycleJob(
        id=new_id("lifecycle"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        kind=kind,
        status=job_status,
        requested_by=context.user_id,
        requested_at=now,
        updated_at=now,
        **extra,
    )


def _purging(store: JobStore, tenant_id: str) -> bool:
    return any(job.status == JobStatus.PURGING for job in store.list_jobs(tenant_id, kind=JobKind.ORGANISATION_DELETION, limit=5))


@router.post("/organization/lifecycle/exports", response_model=JobView, status_code=status.HTTP_202_ACCEPTED)
def request_export(context: TenantContext = Depends(tenant_context)) -> JobView:
    _require(can_manage_organisation(context), ORGANISATION_REQUIRED)
    job = _new_job(context, JobKind.ORGANISATION_EXPORT, JobStatus.QUEUED)
    try:
        with lifecycle_unit(context) as (store, repository):
            if _purging(store, context.tenant_id):
                raise HTTPException(status.HTTP_409_CONFLICT, "This organisation is being deleted, so it cannot be exported.")
            store.insert(job)
            repository.record_audit(_audit(context, "organisation.export.requested", job.id, {"job_id": job.id}))
    except JobConflict:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An export is already being prepared. Wait for it to finish, then download it."
        ) from None
    return view(job)


# --- signed download links


_LINK_CONTEXT = b"anum-organisation-export-link-v1"
_PROCESS_LINK_KEY = secrets.token_bytes(32)


def _link_keys() -> list[bytes]:
    raw = settings.secrets_key.get_secret_value() if settings.secrets_key else ""
    keys = [part.strip() for part in raw.split(",") if part.strip()]
    if not keys:
        return [_PROCESS_LINK_KEY]
    return [hmac.new(key.encode(), _LINK_CONTEXT, hashlib.sha256).digest() for key in keys]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True)
class LinkClaims:
    tenant_id: str
    workspace_id: str
    user_id: str
    job_id: str
    sha256: str
    expires_at: datetime


class InvalidLink(ValueError):
    pass


def sign_link(claims: LinkClaims) -> str:
    payload = json.dumps(
        {
            "t": claims.tenant_id,
            "w": claims.workspace_id,
            "u": claims.user_id,
            "j": claims.job_id,
            "h": claims.sha256,
            "e": int(claims.expires_at.timestamp()),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"{_b64(payload)}.{_b64(hmac.new(_link_keys()[0], payload, hashlib.sha256).digest())}"


def verify_link(value: str, now: datetime | None = None) -> LinkClaims:
    try:
        encoded_payload, encoded_signature = value.split(".", 1)
        payload, signature = _unb64(encoded_payload), _unb64(encoded_signature)
    except ValueError as exc:
        raise InvalidLink("malformed") from exc
    if not any(hmac.compare_digest(hmac.new(key, payload, hashlib.sha256).digest(), signature) for key in _link_keys()):
        raise InvalidLink("signature")
    try:
        data = json.loads(payload)
        claims = LinkClaims(
            tenant_id=str(data["t"]),
            workspace_id=str(data["w"]),
            user_id=str(data["u"]),
            job_id=str(data["j"]),
            sha256=str(data["h"]),
            expires_at=datetime.fromtimestamp(int(data["e"]), tz=UTC),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidLink("payload") from exc
    if claims.expires_at <= (now or datetime.now(UTC)):
        raise InvalidLink("expired")
    return claims


def _export_for(store: JobStore, tenant_id: str, job_id: str) -> LifecycleJob:
    job = store.get(tenant_id, job_id)
    if job is None or job.kind != JobKind.ORGANISATION_EXPORT:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export not found")
    if job.status == JobStatus.EXPIRED:
        raise HTTPException(status.HTTP_410_GONE, "This export has expired and its archive was deleted. Request a new export.")
    if job.status != JobStatus.READY or job.archive_key is None or job.archive_sha256 is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This export is not ready yet.")
    return job


@router.post("/organization/lifecycle/exports/{job_id}/link", response_model=ExportLink)
def export_link(job_id: str, context: TenantContext = Depends(tenant_context)) -> ExportLink:
    _require(can_manage_organisation(context), ORGANISATION_REQUIRED)
    expires_at = utc_now().replace(microsecond=0) + timedelta(seconds=settings.tenant_export_link_ttl_seconds)
    with lifecycle_unit(context) as (store, repository):
        job = _export_for(store, context.tenant_id, job_id)
        token = sign_link(
            LinkClaims(context.tenant_id, context.workspace_id, context.user_id, job.id, job.archive_sha256 or "", expires_at)
        )
        repository.record_audit(_audit(context, "organisation.export.link_issued", job.id, {"job_id": job.id}))
    return ExportLink(url=f"{router.prefix}/organization/lifecycle/exports/download/{token}", expires_at=expires_at)


@router.get("/organization/lifecycle/exports/download/{token}")
def download_export(token: str) -> Response:
    """The archive behind a signed link. The link is the credential (no bearer token); it
    names the tenant, workspace, person and job, and with OIDC the person must still be an
    active owner of that workspace."""
    try:
        claims = verify_link(token)
    except InvalidLink as exc:
        raise HTTPException(status.HTTP_410_GONE, "This download link has expired or is invalid. Ask for a new link.") from exc
    context = TenantContext(tenant_id=claims.tenant_id, workspace_id=claims.workspace_id, user_id=claims.user_id, roles=["owner"])
    if settings.auth_mode == "oidc":
        from .dependencies import lookup_membership

        membership = lookup_membership(context)
        if membership is None or not membership.active or membership.role != "owner":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "The person who asked for this link is no longer an owner of the workspace.")
    with lifecycle_unit(context, require_membership=False) as (store, _):
        try:
            job = _export_for(store, claims.tenant_id, claims.job_id)
        except HTTPException as exc:
            raise HTTPException(status.HTTP_410_GONE, "This export is no longer available. Request a new export.") from exc
    if job.archive_sha256 != claims.sha256 or job.archive_key is None:
        raise HTTPException(status.HTTP_410_GONE, "This export is no longer available. Request a new export.")
    from .files import file_store

    try:
        content = file_store.storage.get(job.archive_key)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_410_GONE, "This export is no longer available. Request a new export.") from exc
    with lifecycle_unit(context, require_membership=False) as (_, repository):
        repository.record_audit(_audit(context, "organisation.export.downloaded", job.id, {"job_id": job.id, "bytes": len(content)}))
    name = f"anum-organisation-{claims.tenant_id}-{job.requested_at:%Y%m%d}.zip"
    return Response(
        content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


# --- deletion


def _schedule(context: TenantContext, kind: JobKind, action: str) -> LifecycleJob:
    purge_after = utc_now() + timedelta(hours=settings.tenant_deletion_cooling_off_hours)
    job = _new_job(context, kind, JobStatus.SCHEDULED, purge_after=purge_after)
    try:
        with lifecycle_unit(context) as (store, repository):
            if kind == JobKind.WORKSPACE_DELETION and _purging(store, context.tenant_id):
                raise HTTPException(status.HTTP_409_CONFLICT, "This organisation is already being deleted.")
            store.insert(job)
            repository.record_audit(
                _audit(context, action, job.id, {"job_id": job.id, "purge_after": purge_after.isoformat(), "workspace_id": context.workspace_id})
            )
    except JobConflict:
        raise HTTPException(status.HTTP_409_CONFLICT, "A deletion is already scheduled. Cancel it first to schedule a new one.") from None
    return job


def _cancel(context: TenantContext, kind: JobKind, action: str) -> LifecycleJob:
    now = utc_now()
    with lifecycle_unit(context) as (store, repository):
        jobs = store.list_jobs(context.tenant_id, kind=kind, limit=200)
        if kind == JobKind.WORKSPACE_DELETION:
            jobs = [job for job in jobs if job.workspace_id == context.workspace_id]
        if _open(jobs, frozenset({JobStatus.PURGING})) is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "The deletion has already started and can no longer be cancelled.")
        job = _open(jobs, frozenset({JobStatus.SCHEDULED}))
        if job is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "No deletion is scheduled.")
        cancelled = store.cancel(context.tenant_id, job.id, context.user_id, now)
        if cancelled is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "The deletion has already started and can no longer be cancelled.")
        repository.record_audit(_audit(context, action, job.id, {"job_id": job.id, "workspace_id": job.workspace_id}))
    return cancelled


@router.post("/organization/lifecycle/deletion", response_model=JobView, status_code=status.HTTP_202_ACCEPTED)
def schedule_organisation_deletion(payload: OrganisationDeletionRequest, context: TenantContext = Depends(tenant_context)) -> JobView:
    _require(can_manage_organisation(context), ORGANISATION_REQUIRED)
    if payload.confirm.strip() != context.tenant_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Type the organisation id exactly to confirm. Nothing was scheduled.")
    return view(_schedule(context, JobKind.ORGANISATION_DELETION, "organisation.deletion.scheduled"))


@router.post("/organization/lifecycle/deletion/cancel", response_model=JobView)
def cancel_organisation_deletion(context: TenantContext = Depends(tenant_context)) -> JobView:
    _require(can_manage_organisation(context), ORGANISATION_REQUIRED)
    return view(_cancel(context, JobKind.ORGANISATION_DELETION, "organisation.deletion.cancelled"))


@router.post("/workspaces/current/deletion", response_model=JobView, status_code=status.HTTP_202_ACCEPTED)
def schedule_workspace_deletion(payload: WorkspaceDeletionRequest, context: TenantContext = Depends(tenant_context)) -> JobView:
    _require(can_delete_workspace(context), WORKSPACE_REQUIRED)
    with lifecycle_unit(context) as (_, repository):
        workspace = repository.get_workspace(context.workspace_id, context)
    if workspace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    if payload.confirm_name.strip() != workspace.name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Type the workspace name exactly to confirm. Nothing was scheduled.")
    return view(_schedule(context, JobKind.WORKSPACE_DELETION, "workspace.deletion.scheduled"))


@router.post("/workspaces/current/deletion/cancel", response_model=JobView)
def cancel_workspace_deletion(context: TenantContext = Depends(tenant_context)) -> JobView:
    _require(can_delete_workspace(context), WORKSPACE_REQUIRED)
    return view(_cancel(context, JobKind.WORKSPACE_DELETION, "workspace.deletion.cancelled"))


# ----------------------------------------------------------------------------- tenant data


class TenantData(Protocol):
    """Reads and deletes a tenant's rows and files; PostgreSQL or the in-memory stores."""

    def workspace_ids(self, tenant_id: str) -> list[str]: ...
    def export_batches(self, tenant_id: str, workspace_id: str, table: TenantTable) -> Iterator[list[dict[str, Any]]]: ...
    def read_object(self, key: str) -> bytes: ...
    def purge_order(self) -> list[TenantTable]: ...
    def purge_batch(self, job: LifecycleJob, table: TenantTable, keep_workspace: str) -> tuple[int, int]: ...


class MemoryTenantData:
    """The in-process stores of the ``memory`` backend."""

    def _collections(self) -> dict[str, tuple[Any, ...]]:
        from .files import file_store

        store = memory_repository.store
        return {
            "workspaces": ("dict", store.workspaces),
            "workspace_memberships": ("dict", store.memberships),
            "workspace_invitations": ("dict", store.invitations),
            "tasks": ("dict", store.tasks),
            "agent_runs": ("dict", store.runs),
            "approvals": ("dict", store.approvals),
            "domain_events": ("list", store.events),
            "audit_records": ("list", store.audit_records),
            "workspace_files": ("dict", file_store.records),
            "workspace_file_versions": ("dict", file_store.versions),
        }

    def workspace_ids(self, tenant_id: str) -> list[str]:
        return sorted(w.id for w in memory_repository.store.workspaces.values() if w.tenant_id == tenant_id)

    @staticmethod
    def _workspace_of(table: TenantTable, item: Any) -> str | None:
        if table.name == "workspaces":
            return item.id
        return getattr(item, "workspace_id", None)

    def _items(self, table: TenantTable) -> list[Any]:
        found = self._collections().get(table.name)
        if found is None:
            return []
        shape, collection = found
        return list(collection.values()) if shape == "dict" else list(collection)

    def export_batches(self, tenant_id: str, workspace_id: str, table: TenantTable) -> Iterator[list[dict[str, Any]]]:
        rows = []
        for item in self._items(table):
            if getattr(item, "tenant_id", None) != tenant_id:
                continue
            if table.workspace_column is not None and self._workspace_of(table, item) != workspace_id:
                continue
            if hasattr(item, "model_dump"):
                data = item.model_dump(mode="json")
            elif dataclasses.is_dataclass(item):
                data = {f.name: getattr(item, f.name) for f in dataclasses.fields(item)}
            else:
                data = dict(vars(item))
            rows.append(data)
        if rows:
            yield rows

    def read_object(self, key: str) -> bytes:
        from .files import file_store

        return file_store.storage.get(key)

    def purge_order(self) -> list[TenantTable]:
        return _ordered(list(PURGED_TABLES), [])

    def purge_batch(self, job: LifecycleJob, table: TenantTable, keep_workspace: str) -> tuple[int, int]:
        found = self._collections().get(table.name)
        if found is None:
            return 0, 0
        shape, collection = found

        def doomed(item: Any) -> bool:
            if getattr(item, "tenant_id", None) != job.tenant_id:
                return False
            workspace = self._workspace_of(table, item)
            if table.name == "workspaces" and workspace == keep_workspace:
                return False
            if job.kind == JobKind.WORKSPACE_DELETION:
                return table.workspace_column is not None and workspace == job.workspace_id
            return True

        files = 0
        if shape == "dict":
            keys = [key for key, item in collection.items() if doomed(item)]
            for key in keys:
                item = collection.pop(key)
                if table.storage_key_column:
                    from .files import file_store

                    file_store.storage.delete(getattr(item, table.storage_key_column))
                    files += 1
            return len(keys), files
        kept = [item for item in collection if not doomed(item)]
        removed = len(collection) - len(kept)
        collection[:] = kept
        return removed, files

    def finish(self, job: LifecycleJob, keep_workspace: str, now: datetime) -> None:
        store = memory_repository.store
        if job.kind == JobKind.ORGANISATION_DELETION:
            tenant = store.tenants.get(job.tenant_id)
            if tenant is not None:
                store.tenants[job.tenant_id] = tenant.model_copy(update={"name": DELETED_ORGANISATION, "status": "deleted", "updated_at": now})
        workspace = store.workspaces.get(keep_workspace)
        if workspace is not None and workspace.tenant_id == job.tenant_id:
            store.workspaces[keep_workspace] = workspace.model_copy(
                update={"name": DELETED_WORKSPACE, "archived_at": now, "archived_by": SYSTEM_ACTOR, "updated_at": now}
            )


def _ordered(tables: list[TenantTable], edges: list[tuple[str, str]]) -> list[TenantTable]:
    """Children before parents (``edges`` are (child, parent) foreign keys), access tables
    first where the foreign keys allow, otherwise the registry order."""
    names = [t.name for t in tables]
    known = set(names)
    priority: dict[str, int] = {}
    for index, name in enumerate([*ACCESS_TABLES, *names]):
        priority.setdefault(name, index)
    referenced_by: dict[str, set[str]] = {name: set() for name in names}
    for child, parent in edges:
        if child in known and parent in known and child != parent:
            referenced_by[parent].add(child)
    by_name = {t.name: t for t in tables}
    done: list[str] = []
    remaining = set(names)
    while remaining:
        ready = [n for n in remaining if not (referenced_by[n] & remaining)]
        if not ready:  # a cycle: fall back to the registry order for the rest
            ready = list(remaining)
        pick = min(ready, key=lambda n: priority[n])
        done.append(pick)
        remaining.discard(pick)
    return [by_name[n] for n in done]


def tenant_data() -> TenantData:
    if settings.repository_backend == "memory":
        return MemoryTenantData()
    from .db.tenant_lifecycle_repository import PostgresTenantData

    return PostgresTenantData()


# ----------------------------------------------------------------------------- export


def json_value(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return {"base64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, Mapping):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [json_value(v) for v in value]
    if isinstance(value, StrEnum):
        return value.value
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def exportable_columns(columns: list[str]) -> tuple[list[str], list[str]]:
    kept = [c for c in columns if not _SECRET_COLUMN.search(c.lower())]
    return kept, [c for c in columns if c not in kept]


def csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict | list):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    # A spreadsheet must not run a cell as a formula.
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .") or "file"
    return cleaned[:120]


class ArchiveTooLarge(Exception):
    pass


def build_archive(job: LifecycleJob, data: TenantData, path: str, renew: Any = None) -> dict[str, Any]:
    """Write the zip for ``job`` to ``path``; returns the manifest. No transaction is held
    here: ``data`` opens one short transaction per batch."""
    limit = settings.tenant_export_max_bytes
    workspaces = data.workspace_ids(job.tenant_id) or [job.workspace_id]
    manifest: dict[str, Any] = {
        "format": FORMAT,
        "organisation_id": job.tenant_id,
        "job_id": job.id,
        "generated_at": utc_now().isoformat(),
        "workspaces": workspaces,
        "tables": {},
        "files": 0,
        "not_included": list(NOT_INCLUDED),
    }
    file_rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for table in EXPORTED_TABLES:
            scopes = workspaces if table.workspace_column is not None else workspaces[:1]
            entry = {"domain": table.domain, "rows": 0, "columns_left_out": []}
            jsonl = archive.open(f"data/{table.domain}/{table.name}.jsonl", "w", force_zip64=True)
            csv_buffer = tempfile.TemporaryFile("w+", encoding="utf-8", newline="")
            writer: Any = None
            header: list[str] = []
            with jsonl:
                for workspace_id in scopes:
                    for batch in data.export_batches(job.tenant_id, workspace_id, table):
                        for row in batch:
                            if writer is None:
                                header, left_out = exportable_columns(list(row))
                                entry["columns_left_out"] = left_out
                                writer = csv.writer(csv_buffer)
                                writer.writerow(header)
                            clean = {c: json_value(row.get(c)) for c in header}
                            jsonl.write((json.dumps(clean, ensure_ascii=False, sort_keys=True) + "\n").encode())
                            writer.writerow([csv_cell(clean[c]) for c in header])
                            entry["rows"] += 1
                            if table.name == "workspace_files" and row.get("storage_key"):
                                file_rows.append({**clean, "storage_key": row["storage_key"]})
                        if os.path.getsize(path) + csv_buffer.tell() > limit:
                            raise ArchiveTooLarge
            csv_buffer.seek(0)
            with archive.open(f"csv/{table.domain}/{table.name}.csv", "w", force_zip64=True) as csv_entry:
                for chunk in iter(lambda: csv_buffer.read(1024 * 1024), ""):
                    csv_entry.write(chunk.encode("utf-8"))
            csv_buffer.close()
            manifest["tables"][table.name] = entry
            if renew is not None:
                renew()
        for row in file_rows:
            try:
                content = data.read_object(row["storage_key"])
            except FileNotFoundError:
                continue
            archive.writestr(f"files/{row.get('workspace_id')}/{row.get('id')}/{_safe_name(str(row.get('name') or 'file'))}", content)
            manifest["files"] += 1
            if os.path.getsize(path) > limit:
                raise ArchiveTooLarge
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    if os.path.getsize(path) > limit:
        raise ArchiveTooLarge
    return manifest


def _human(limit: int) -> str:
    return f"{limit / (1024 * 1024):.0f} MiB"


def run_export(job: LifecycleJob, data: TenantData) -> None:
    from .files import file_store, workspace_object_key

    context = _system(job.tenant_id, job.workspace_id)

    def renew() -> None:
        with lifecycle_unit(context, require_membership=False) as (store, _):
            current = store.get(job.tenant_id, job.id)
            if current is not None and current.status == JobStatus.RUNNING:
                store.save(current.model_copy(update={"lease_until": utc_now() + LEASE, "updated_at": utc_now()}))

    handle, path = tempfile.mkstemp(prefix="anum-export-", suffix=".zip")
    os.close(handle)
    try:
        try:
            manifest = build_archive(job, data, path, renew)
        except ArchiveTooLarge:
            _finish_export(context, job, error=f"The archive is larger than {_human(settings.tenant_export_max_bytes)}, the limit set by ANUM_TENANT_EXPORT_MAX_BYTES. Ask the administrator to raise it.")
            return
        digest = hashlib.sha256()
        with open(path, "rb") as handle_in:
            for chunk in iter(lambda: handle_in.read(1024 * 1024), b""):
                digest.update(chunk)
        size = os.path.getsize(path)
        key = workspace_object_key(job.tenant_id, job.workspace_id, "lifecycle", job.id, "export.zip")
        with open(path, "rb") as handle_in:
            file_store.storage.put(key, handle_in.read(), "application/zip")
        rows = sum(entry["rows"] for entry in manifest["tables"].values())
        _finish_export(
            context,
            job,
            archive_key=key,
            archive_bytes=size,
            archive_sha256=digest.hexdigest(),
            row_count=rows,
            file_count=manifest["files"],
            counts={name: entry["rows"] for name, entry in manifest["tables"].items()},
        )
    except Exception as exc:  # noqa: BLE001 - recorded on the job; ids only in the log
        logger.exception("Organisation export failed", extra={"anum_lifecycle_job": job.id})
        _finish_export(context, job, error=f"The export failed ({type(exc).__name__}). Try again; if it fails again, contact support.")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _finish_export(context: TenantContext, job: LifecycleJob, *, error: str | None = None, **ready: Any) -> None:
    now = utc_now()
    with lifecycle_unit(context, require_membership=False) as (store, repository):
        current = store.get(job.tenant_id, job.id) or job
        if error is not None:
            store.save(current.model_copy(update={"status": JobStatus.FAILED, "error": error, "finished_at": now, "lease_until": None, "updated_at": now}))
            repository.record_audit(_audit(context, "organisation.export.failed", job.id, {"job_id": job.id}, outcome="failure"))
            return
        expires = now + timedelta(hours=settings.tenant_export_retention_hours)
        store.save(current.model_copy(update={**ready, "status": JobStatus.READY, "finished_at": now, "expires_at": expires, "lease_until": None, "error": None, "updated_at": now}))
        repository.record_audit(
            _audit(context, "organisation.export.ready", job.id, {"job_id": job.id, "bytes": ready.get("archive_bytes"), "rows": ready.get("row_count"), "files": ready.get("file_count")})
        )


def expire_export(job: LifecycleJob) -> None:
    from .files import file_store

    if job.archive_key:
        file_store.storage.delete(job.archive_key)
    now = utc_now()
    with lifecycle_unit(_system(job.tenant_id, job.workspace_id), require_membership=False) as (store, _):
        current = store.get(job.tenant_id, job.id)
        if current is not None and current.status == JobStatus.READY:
            store.save(current.model_copy(update={"status": JobStatus.EXPIRED, "archive_key": None, "updated_at": now}))


# ----------------------------------------------------------------------------- purge


def run_purge(job: LifecycleJob, data: TenantData) -> None:
    """Delete every covered row and file in batches, until a full pass finds nothing."""
    from .files import file_store

    context = _system(job.tenant_id, job.workspace_id)
    keep_workspace = job.workspace_id
    if job.kind == JobKind.ORGANISATION_DELETION:
        # Export archives go first: their rows are removed by the application role below.
        with lifecycle_unit(context, require_membership=False) as (store, repository):
            archives = [j.archive_key for j in store.list_jobs(job.tenant_id, kind=JobKind.ORGANISATION_EXPORT, limit=10_000) if j.archive_key]
            # The console receipt, opened before platform_organizations is purged.
            receipts(repository).open(job, utc_now())
        for key in archives:
            file_store.storage.delete(key)
    counts: dict[str, int] = dict(job.counts)
    files = job.file_count
    if job.kind == JobKind.ORGANISATION_DELETION:
        # Every public logo version the organisation published for email headers
        # (public_logos.py): they are served without sign-in, so they go first. Idempotent.
        from . import public_logos

        files += public_logos.purge(job.tenant_id)
    order = data.purge_order()
    for _ in range(5):
        removed_this_pass = 0
        for table in order:
            if job.kind == JobKind.WORKSPACE_DELETION and table.workspace_column is None:
                continue
            while True:
                rows, removed_files = data.purge_batch(job, table, keep_workspace)
                if rows:
                    counts[table.name] = counts.get(table.name, 0) + rows
                files += removed_files
                removed_this_pass += rows
                if rows < settings.tenant_purge_batch_rows:
                    break
            _renew_purge(context, job, counts, files)
        if removed_this_pass == 0:
            break
    _finish_purge(job, data, keep_workspace, counts, files)


def _renew_purge(context: TenantContext, job: LifecycleJob, counts: dict[str, int], files: int) -> None:
    now = utc_now()
    with lifecycle_unit(context, require_membership=False) as (store, _):
        current = store.get(job.tenant_id, job.id)
        if current is not None and current.status == JobStatus.PURGING:
            store.save(current.model_copy(update={"lease_until": now + LEASE, "counts": counts, "file_count": files, "row_count": sum(counts.values()), "updated_at": now}))


def _finish_purge(job: LifecycleJob, data: TenantData, keep_workspace: str, counts: dict[str, int], files: int) -> None:
    now = utc_now()
    context = _system(job.tenant_id, keep_workspace)
    org = job.kind == JobKind.ORGANISATION_DELETION
    action = "organisation.deleted" if org else "workspace.deleted"
    # Keycloak accounts are removed right after (remove_identities), or by the operator.
    identity = ("pending" if keycloak_configured() else "not_configured") if org else None
    with lifecycle_unit(context, require_membership=False) as (store, repository):
        if isinstance(data, MemoryTenantData):
            data.finish(job, keep_workspace, now)
        else:
            from .db.tenant_lifecycle_repository import tombstone

            tombstone(repository.session, job.tenant_id, keep_workspace, organisation=org, now=now)
        if org:
            store.delete_except(job.tenant_id, job.id)
        current = store.get(job.tenant_id, job.id) or job
        store.save(
            current.model_copy(
                update={
                    "status": JobStatus.COMPLETED,
                    "finished_at": now,
                    "lease_until": None,
                    "counts": counts,
                    "row_count": sum(counts.values()),
                    "file_count": files,
                    "error": None,
                    "updated_at": now,
                }
            )
        )
        repository.record_audit(
            AuditRecord(
                id=new_id("audit"),
                tenant_id=job.tenant_id,
                workspace_id=keep_workspace,
                actor=SYSTEM_ACTOR,
                action=action,
                target=job.tenant_id if org else job.workspace_id,
                outcome="success",
                correlation_id=new_id("correlation"),
                created_at=now,
                metadata={
                    "job_id": job.id,
                    "requested_by": job.requested_by,
                    "requested_at": job.requested_at.isoformat(),
                    "rows": sum(counts.values()),
                    "files": files,
                    "counts": counts,
                    **({"identity_cleanup": identity} if identity else {}),
                },
            )
        )
        if org and identity:
            receipts(repository).complete(job.tenant_id, now, counts, files, identity)


def _record_purge_error(job: LifecycleJob, exc: Exception) -> None:
    now = utc_now()
    with lifecycle_unit(_system(job.tenant_id, job.workspace_id), require_membership=False) as (store, _):
        current = store.get(job.tenant_id, job.id)
        if current is not None and current.status == JobStatus.PURGING:
            store.save(current.model_copy(update={"error": f"The deletion stopped ({type(exc).__name__}) and will be retried.", "lease_until": None, "updated_at": now}))


# ----------------------------------------------------------------------------- console receipts


IDENTITY_MAX_ATTEMPTS = 5


class IdentityCleanup(BaseModel):
    status: str
    removed: int = 0
    detached: int = 0
    finished_at: datetime | None = None


class DeletedOrganisation(BaseModel):
    tenant_id: str
    job_id: str
    operator_user_id: str | None = None
    display_name: str | None = None
    plan: str | None = None
    requested_by: str
    requested_at: datetime
    purge_started_at: datetime
    deleted_at: datetime | None = None
    status: str = "purging"
    row_count: int = 0
    file_count: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    identity_status: str = "pending"
    identity_removed: int = 0
    identity_detached: int = 0
    identity_attempts: int = 0
    identity_finished_at: datetime | None = None


class DeletedOrganisationView(BaseModel):
    tenant_id: str
    display_name: str | None
    plan: str | None
    requested_by: str
    requested_at: datetime
    purge_started_at: datetime
    deleted_at: datetime | None
    status: str
    row_count: int
    file_count: int
    counts: dict[str, int]
    identity_cleanup: IdentityCleanup


def deleted_view(record: DeletedOrganisation) -> DeletedOrganisationView:
    return DeletedOrganisationView(
        **record.model_dump(include=set(DeletedOrganisationView.model_fields) - {"identity_cleanup"}),
        identity_cleanup=IdentityCleanup(
            status=record.identity_status, removed=record.identity_removed, detached=record.identity_detached,
            finished_at=record.identity_finished_at,
        ),
    )


class ReceiptStore(Protocol):
    def get(self, tenant_id: str) -> DeletedOrganisation | None: ...
    def open(self, job: LifecycleJob, now: datetime) -> None: ...
    def save(self, record: DeletedOrganisation) -> None: ...

    def complete(self, tenant_id: str, now: datetime, counts: dict[str, int], files: int, identity: str) -> None: ...


class MemoryReceiptStore:
    def __init__(self) -> None:
        self.records: dict[str, DeletedOrganisation] = {}

    def clear(self) -> None:
        self.records.clear()

    def get(self, tenant_id: str) -> DeletedOrganisation | None:
        return self.records.get(tenant_id)

    def open(self, job: LifecycleJob, now: datetime) -> None:
        if job.tenant_id in self.records:
            return
        from .organisations import memory_branding

        operated = memory_branding.platform.get(job.tenant_id) or {}
        self.records[job.tenant_id] = DeletedOrganisation(
            tenant_id=job.tenant_id, job_id=job.id, operator_user_id=operated.get("operator_user_id"),
            display_name=operated.get("display_name"), plan=operated.get("plan"), requested_by=job.requested_by,
            requested_at=job.requested_at, purge_started_at=now,
        )

    def save(self, record: DeletedOrganisation) -> None:
        self.records[record.tenant_id] = record

    def complete(self, tenant_id: str, now: datetime, counts: dict[str, int], files: int, identity: str) -> None:
        record = self.records.get(tenant_id)
        if record is not None:
            self.records[tenant_id] = record.model_copy(update={
                "status": "deleted", "deleted_at": now, "counts": counts, "row_count": sum(counts.values()),
                "file_count": files, "identity_status": identity,
            })
        from .organisations import memory_branding

        memory_branding.platform.pop(tenant_id, None)

    def list_for_operator(self, operator: str) -> list[DeletedOrganisation]:
        return [r for r in self.records.values() if r.operator_user_id == operator]

    def due(self) -> list[str]:
        return [t for t, r in self.records.items() if r.status == "deleted" and r.identity_status == "pending"]


memory_receipts = MemoryReceiptStore()


def receipts(repository: Any) -> ReceiptStore:
    if settings.repository_backend == "memory":
        return memory_receipts
    from .db.tenant_lifecycle_repository import SqlReceiptStore

    return SqlReceiptStore(repository.session)


def keycloak_configured() -> bool:
    from . import keycloak_admin

    return keycloak_admin.build_admin() is not None


def due_identity_cleanups() -> list[str]:
    if settings.repository_backend == "memory":
        return memory_receipts.due()
    from .db.tenant_lifecycle_repository import discover_identity_cleanups

    return discover_identity_cleanups()


def remove_identities(tenant_id: str) -> str:
    """Remove the organisation's Keycloak accounts (only people in no other organisation)
    and record the outcome on the receipt and in the audit trail. Idempotent."""
    from . import keycloak_admin

    jobs = memory_lifecycle_store if settings.repository_backend == "memory" else None
    with _receipt_unit(tenant_id, jobs) as (record, workspace_id):
        if record is None or record.status != "deleted" or record.identity_status != "pending":
            return "skipped"
    admin = keycloak_admin.build_admin()
    context = _system(tenant_id, workspace_id)
    now = utc_now()
    if admin is None:
        outcome, update, audit_outcome, metadata = "not_configured", {"identity_status": "not_configured"}, "skipped", {"reason": "not_configured"}
    else:
        try:
            result = keycloak_admin.remove_organisation_users(admin, tenant_id)
        except Exception as exc:  # noqa: BLE001 - recorded, retried by the next pass
            logger.exception("Keycloak cleanup failed", extra={"anum_lifecycle_tenant": tenant_id})
            attempts = record.identity_attempts + 1
            status_after = "failed" if attempts >= IDENTITY_MAX_ATTEMPTS else "pending"
            outcome, update, audit_outcome = ("failed" if status_after == "failed" else "retry"), {"identity_attempts": attempts, "identity_status": status_after}, "failure"
            metadata = {"error": type(exc).__name__, "attempts": attempts}
        else:
            outcome, audit_outcome = "removed", "success"
            update = {"identity_status": "removed", "identity_removed": result.removed, "identity_detached": result.detached, "identity_finished_at": now}
            metadata = {"removed": result.removed, "detached": result.detached}
    with lifecycle_unit(context, require_membership=False) as (_, repository):
        store = receipts(repository)
        current = store.get(tenant_id)
        if current is not None:
            store.save(current.model_copy(update=update))
        repository.record_audit(_audit(context, "organisation.identities_removed", tenant_id, {"job_id": record.job_id, **metadata}, outcome=audit_outcome))
    return outcome


@contextmanager
def _receipt_unit(tenant_id: str, memory_jobs: MemoryJobStore | None) -> Iterator[tuple[DeletedOrganisation | None, str]]:
    """The receipt and the workspace that holds the audit trail (the deletion job's)."""
    if memory_jobs is not None:
        record = memory_receipts.get(tenant_id)
        job = memory_jobs.get(tenant_id, record.job_id) if record else None
        yield record, (job.workspace_id if job else "")
        return
    from .db import session as db_session
    from .db.tenant_lifecycle_repository import SqlJobStore, SqlReceiptStore

    session = db_session.SessionLocal()
    try:
        db_session.set_tenant_context(session, tenant_id)
        record = SqlReceiptStore(session).get(tenant_id)
        job = SqlJobStore(session).get(tenant_id, record.job_id) if record else None
        yield record, (job.workspace_id if job else "")
    finally:
        session.rollback()
        session.close()


def _deleted_for_operator(operator: str) -> list[DeletedOrganisation]:
    if settings.repository_backend == "memory":
        return memory_receipts.list_for_operator(operator)
    from .db.tenant_lifecycle_repository import deleted_for_operator

    return deleted_for_operator(operator)


@router.get("/platform/organizations/deleted", response_model=list[DeletedOrganisationView])
def list_deleted_organisations(context: TenantContext = Depends(tenant_context)) -> list[DeletedOrganisationView]:
    """Read-only receipts of the organisations this platform operator onboarded that were
    deleted (or are being purged): dates and counts, never content."""
    from .organisations import _require_operator

    _require_operator(context)
    records = sorted(_deleted_for_operator(context.user_id), key=lambda r: (r.deleted_at or r.purge_started_at), reverse=True)
    return [deleted_view(record) for record in records]


# ----------------------------------------------------------------------------- runner


def due_jobs(now: datetime) -> list[LifecycleJob]:
    if settings.repository_backend == "memory":
        return memory_lifecycle_store.due(now)
    from .db.tenant_lifecycle_repository import discover_due_jobs

    return discover_due_jobs(now)


def handle_job(job: LifecycleJob, now: datetime, data: TenantData | None = None) -> str:
    """Claim and run one due job; returns what happened."""
    data = data or tenant_data()
    context = _system(job.tenant_id, job.workspace_id)
    if job.kind == JobKind.ORGANISATION_EXPORT:
        if job.status == JobStatus.READY:
            expire_export(job)
            return "expired"
        with lifecycle_unit(context, require_membership=False) as (store, _):
            claimed = store.claim(job.tenant_id, job.id, frozenset({JobStatus.QUEUED, JobStatus.RUNNING}), JobStatus.RUNNING, now)
        if claimed is None:
            return "skipped"
        run_export(claimed, data)
        return "exported"
    with lifecycle_unit(context, require_membership=False) as (store, _):
        claimed = store.claim(job.tenant_id, job.id, frozenset({JobStatus.SCHEDULED, JobStatus.PURGING}), JobStatus.PURGING, now)
    if claimed is None:
        return "skipped"
    try:
        run_purge(claimed, data)
    except Exception as exc:  # noqa: BLE001 - the job stays purging and the next pass retries
        logger.exception("Purge failed", extra={"anum_lifecycle_job": job.id})
        _record_purge_error(claimed, exc)
        return "error"
    if claimed.kind == JobKind.ORGANISATION_DELETION:
        remove_identities(claimed.tenant_id)
    return "purged"


def run_lifecycle_pass(now: datetime | None = None) -> dict[str, int]:
    now = now or utc_now()
    counts: dict[str, int] = {}
    for tenant_id in due_identity_cleanups():
        outcome = "identities_" + remove_identities(tenant_id)
        counts[outcome] = counts.get(outcome, 0) + 1
    for job in due_jobs(now):
        try:
            outcome = handle_job(job, now)
        except Exception:  # noqa: BLE001 - one job never stops the others
            logger.exception("Lifecycle job failed", extra={"anum_lifecycle_job": job.id})
            outcome = "error"
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


class LifecycleRunner:
    """Every ``ANUM_TENANT_LIFECYCLE_POLL_SECONDS``: one pass in a worker thread. ``stop``
    lets a pass in progress finish its short transactions instead of cancelling it."""

    def __init__(self, poll_interval: float) -> None:
        self.poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self._pass: asyncio.Future[Any] | None = None

    def start(self) -> None:
        if self._task is None:
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="anum-tenant-lifecycle")

    async def stop(self, *, timeout: float = 30.0) -> None:
        task, self._task = self._task, None
        if self._stop is not None:
            self._stop.set()
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except TimeoutError:
                task.cancel()
            except Exception:
                logger.exception("Lifecycle runner stopped with an error")
        if self._pass is not None and not self._pass.done():
            await asyncio.wait({self._pass})
        self._pass = None

    async def _run(self) -> None:
        stop = self._stop
        if stop is None:
            raise RuntimeError("Lifecycle runner started without a stop event")
        while not stop.is_set():
            self._pass = asyncio.ensure_future(asyncio.to_thread(run_lifecycle_pass))
            try:
                await asyncio.shield(self._pass)
            except Exception:
                logger.exception("Lifecycle pass failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass


def build_lifecycle_runner(config: Any) -> LifecycleRunner | None:
    if not config.tenant_lifecycle_enabled:
        return None
    return LifecycleRunner(config.tenant_lifecycle_poll_seconds)
