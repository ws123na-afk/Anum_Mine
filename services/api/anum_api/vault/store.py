"""Storage of the document vault (migration ``0133_document_vault``).

Eight workspace tables under forced RLS, plus the ``vault`` column of ``workspace_files``. With
PostgreSQL one unit of work (``open_vault_unit``) holds one session for the vault tables and the
file metadata, with ``anum.tenant_id``, ``anum.workspace_id``, ``anum.user_id`` and ``anum.vault``
set, so a document and its file row commit or roll back together and the row policies decide
what the caller sees: hidden vault files only here, a private document only for its owner and the
people it is shared with (``vault_documents_private``). The daily job sets ``anum.vault_job``.

The in-memory backend (local runs and tests) keeps the same rows in the process and applies the
same visibility rules in code. Every call runs in a worker thread, never on the event loop and
never across an OCR or model call (docs/realtime.md#event-loop).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from threading import RLock
from typing import Any, Protocol

from fastapi import HTTPException, status
from sqlalchemy import text

from ..audit import AuditRecord
from ..schemas import TenantContext
from ..settings import settings
from .models import (
    AccessEvent,
    AccessGrant,
    CategoryRecord,
    DeviceRecord,
    DocumentQuery,
    DocumentRecord,
    ExtractionRecord,
    LinkRecord,
    VaultSettings,
)


class StaleDocument(Exception):
    """The document changed (or was deleted) since the caller read it."""


class CategoryInUse(Exception):
    pass


class CategoryTaken(Exception):
    pass


_JOB: ContextVar[bool] = ContextVar("anum_vault_job", default=False)


@dataclass(frozen=True, slots=True)
class Summary:
    active: int = 0
    private: int = 0
    expiring: int = 0
    expired: int = 0
    review: int = 0
    trash: int = 0


class VaultStore(Protocol):
    def get_settings(self, context: TenantContext) -> VaultSettings: ...
    def save_settings(self, context: TenantContext, value: VaultSettings) -> VaultSettings: ...
    def categories(self, context: TenantContext) -> list[CategoryRecord]: ...
    def add_category(self, context: TenantContext, record: CategoryRecord) -> CategoryRecord: ...
    def delete_category(self, context: TenantContext, category_id: str) -> bool: ...
    def create_document(self, context: TenantContext, record: DocumentRecord) -> DocumentRecord: ...
    def get_document(self, context: TenantContext, document_id: str, *, for_update: bool = False) -> DocumentRecord | None: ...
    def save_document(self, context: TenantContext, record: DocumentRecord, expected_version: int) -> DocumentRecord: ...
    def remove_document(self, context: TenantContext, document_id: str) -> bool: ...
    def list_documents(self, context: TenantContext, query: DocumentQuery, today: date) -> list[DocumentRecord]: ...
    def same_content(self, context: TenantContext, sha256: str) -> list[DocumentRecord]: ...
    def summary(self, context: TenantContext, today: date, within_days: int) -> Summary: ...
    def access(self, context: TenantContext, document_id: str) -> list[AccessGrant]: ...
    def grant(self, context: TenantContext, grant: AccessGrant) -> AccessGrant: ...
    def revoke(self, context: TenantContext, document_id: str, user_id: str) -> bool: ...
    def links(self, context: TenantContext, document_id: str) -> list[LinkRecord]: ...
    def add_link(self, context: TenantContext, record: LinkRecord) -> LinkRecord: ...
    def delete_link(self, context: TenantContext, document_id: str, link_id: str) -> bool: ...
    def add_extraction(self, context: TenantContext, record: ExtractionRecord) -> ExtractionRecord: ...
    def save_extraction(self, context: TenantContext, record: ExtractionRecord) -> ExtractionRecord: ...
    def extraction(self, context: TenantContext, extraction_id: str) -> ExtractionRecord | None: ...
    def extractions(self, context: TenantContext, document_id: str) -> list[ExtractionRecord]: ...
    def open_extractions(self, context: TenantContext, limit: int) -> list[ExtractionRecord]: ...
    def log(self, context: TenantContext, event: AccessEvent) -> AccessEvent: ...
    def events(self, context: TenantContext, document_id: str, limit: int) -> list[AccessEvent]: ...
    def devices(self, context: TenantContext, owner_user_id: str) -> list[DeviceRecord]: ...
    def device(self, context: TenantContext, device_id: str) -> DeviceRecord | None: ...
    def save_device(self, context: TenantContext, record: DeviceRecord) -> DeviceRecord: ...
    def expiring(self, context: TenantContext, until: date) -> list[DocumentRecord]: ...
    def trash_due(self, context: TenantContext, now: datetime) -> list[DocumentRecord]: ...
    def retention_due(self, context: TenantContext, today: date) -> list[DocumentRecord]: ...
    def owned_by(self, context: TenantContext, user_id: str) -> list[DocumentRecord]: ...
    def set_file_vault(self, context: TenantContext, file_id: str, value: str) -> None: ...
    def record_audit(self, record: AuditRecord) -> AuditRecord: ...


def _matches(record: DocumentRecord, query: DocumentQuery, today: date) -> bool:
    if record.status != query.status:
        return False
    if query.category and record.category != query.category:
        return False
    if query.access_class and record.access_class != query.access_class:
        return False
    if query.tag and query.tag.lower() not in {tag.lower() for tag in record.tags}:
        return False
    if query.expired is True and not (record.expiry_date is not None and record.expiry_date < today):
        return False
    if query.expiring_within_days is not None and not (
        record.expiry_date is not None and today <= record.expiry_date <= today + timedelta(days=query.expiring_within_days)
    ):
        return False
    if query.search:
        needle = query.search.lower()
        haystack = " ".join([record.title, record.issuer or "", record.description, " ".join(record.tags), record.number_last4 or ""]).lower()
        if needle not in haystack:
            return False
    return True


class InMemoryVaultStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._settings: dict[tuple[str, str], VaultSettings] = {}
        self._categories: dict[tuple[str, str, str], CategoryRecord] = {}
        self._documents: dict[tuple[str, str, str], DocumentRecord] = {}
        self._access: dict[tuple[str, str, str, str], AccessGrant] = {}
        self._links: dict[tuple[str, str, str], LinkRecord] = {}
        self._extractions: dict[tuple[str, str, str], ExtractionRecord] = {}
        self._events: list[tuple[str, str, AccessEvent]] = []
        self._devices: dict[tuple[str, str, str], DeviceRecord] = {}
        self._file_vault: dict[tuple[str, str, str], str] = {}

    def clear(self) -> None:
        with self._lock:
            for item in (self._settings, self._categories, self._documents, self._access, self._links, self._extractions, self._devices):
                item.clear()
            self._events.clear()

    @staticmethod
    def _ws(context: TenantContext) -> tuple[str, str]:
        return (context.tenant_id, context.workspace_id)

    def _visible(self, context: TenantContext, record: DocumentRecord) -> bool:
        """The memory copy of the row policy ``vault_documents_private``."""
        if record.access_class == "workspace" or record.owner_user_id == context.user_id or _JOB.get():
            return True
        return (context.tenant_id, context.workspace_id, record.id, context.user_id) in self._access

    def _scoped_docs(self, context: TenantContext) -> list[DocumentRecord]:
        return [d for (t, w, _), d in self._documents.items() if (t, w) == self._ws(context) and self._visible(context, d)]

    def get_settings(self, context: TenantContext) -> VaultSettings:
        with self._lock:
            return self._settings.get(self._ws(context), VaultSettings()).model_copy()

    def save_settings(self, context: TenantContext, value: VaultSettings) -> VaultSettings:
        with self._lock:
            self._settings[self._ws(context)] = value.model_copy()
            return value

    def categories(self, context: TenantContext) -> list[CategoryRecord]:
        with self._lock:
            rows = [c for (t, w, _), c in self._categories.items() if (t, w) == self._ws(context)]
            return sorted(rows, key=lambda c: c.label.lower())

    def add_category(self, context: TenantContext, record: CategoryRecord) -> CategoryRecord:
        with self._lock:
            if any(c.label.lower() == record.label.lower() for c in self.categories(context)):
                raise CategoryTaken(record.label)
            self._categories[(*self._ws(context), record.id)] = record
            return record

    def delete_category(self, context: TenantContext, category_id: str) -> bool:
        with self._lock:
            # Counted across every document of the workspace, private ones included (as the SQL check constraint would).
            used = any(d.category == category_id for (t, w, _), d in self._documents.items() if (t, w) == self._ws(context))
            if used:
                raise CategoryInUse(category_id)
            return self._categories.pop((*self._ws(context), category_id), None) is not None

    def create_document(self, context: TenantContext, record: DocumentRecord) -> DocumentRecord:
        with self._lock:
            self._documents[(*self._ws(context), record.id)] = record.model_copy(deep=True)
            return record

    def get_document(self, context: TenantContext, document_id: str, *, for_update: bool = False) -> DocumentRecord | None:
        with self._lock:
            row = self._documents.get((*self._ws(context), document_id))
            return row.model_copy(deep=True) if row is not None and self._visible(context, row) else None

    def save_document(self, context: TenantContext, record: DocumentRecord, expected_version: int) -> DocumentRecord:
        with self._lock:
            current = self.get_document(context, record.id)
            if current is None or current.row_version != expected_version:
                raise StaleDocument(record.id)
            saved = record.model_copy(update={"row_version": expected_version + 1}, deep=True)
            self._documents[(*self._ws(context), record.id)] = saved
            return saved.model_copy(deep=True)

    def remove_document(self, context: TenantContext, document_id: str) -> bool:
        with self._lock:
            key = (*self._ws(context), document_id)
            row = self._documents.get(key)
            if row is None or not self._visible(context, row):
                return False
            del self._documents[key]
            for store in (self._access, self._links, self._extractions):
                for item_key in [k for k, v in store.items() if k[:2] == self._ws(context) and getattr(v, "document_id", None) == document_id]:
                    del store[item_key]
            self._events = [(t, w, e) for t, w, e in self._events if not ((t, w) == self._ws(context) and e.document_id == document_id)]
            return True

    def list_documents(self, context: TenantContext, query: DocumentQuery, today: date) -> list[DocumentRecord]:
        with self._lock:
            rows = [d for d in self._scoped_docs(context) if _matches(d, query, today)]
        rows.sort(key=lambda d: (d.created_at, d.id), reverse=True)
        if query.after is not None:
            rows = [d for d in rows if (d.created_at, d.id) < query.after]
        return [d.model_copy(deep=True) for d in rows[: query.limit]]

    def same_content(self, context: TenantContext, sha256: str) -> list[DocumentRecord]:
        with self._lock:
            return [d.model_copy(deep=True) for d in self._scoped_docs(context) if d.content_sha256 == sha256]

    def summary(self, context: TenantContext, today: date, within_days: int) -> Summary:
        with self._lock:
            docs = self._scoped_docs(context)
            visible = {d.id for d in docs}
            review = sum(1 for (t, w, _), e in self._extractions.items()
                         if (t, w) == self._ws(context) and e.status in ("queued", "pending") and e.document_id in visible)
        active = [d for d in docs if d.status == "active"]
        return Summary(
            active=len(active),
            private=sum(1 for d in active if d.access_class == "private"),
            expiring=sum(1 for d in active if d.expiry_date and today <= d.expiry_date <= today + timedelta(days=within_days)),
            expired=sum(1 for d in active if d.expiry_date and d.expiry_date < today),
            review=review,
            trash=sum(1 for d in docs if d.status == "deleted"),
        )

    def access(self, context: TenantContext, document_id: str) -> list[AccessGrant]:
        with self._lock:
            rows = [g for (t, w, d, _), g in self._access.items() if (t, w) == self._ws(context) and d == document_id]
            return sorted(rows, key=lambda g: (g.granted_at, g.user_id))

    def grant(self, context: TenantContext, grant: AccessGrant) -> AccessGrant:
        with self._lock:
            self._access.setdefault((*self._ws(context), grant.document_id, grant.user_id), grant)
            return self._access[(*self._ws(context), grant.document_id, grant.user_id)]

    def revoke(self, context: TenantContext, document_id: str, user_id: str) -> bool:
        with self._lock:
            return self._access.pop((*self._ws(context), document_id, user_id), None) is not None

    def links(self, context: TenantContext, document_id: str) -> list[LinkRecord]:
        with self._lock:
            rows = [link for (t, w, _), link in self._links.items() if (t, w) == self._ws(context) and link.document_id == document_id]
            return sorted(rows, key=lambda r: (r.created_at, r.id))

    def add_link(self, context: TenantContext, record: LinkRecord) -> LinkRecord:
        with self._lock:
            for link in self.links(context, record.document_id):
                if (link.target_type, link.target_id) == (record.target_type, record.target_id):
                    return link
            self._links[(*self._ws(context), record.id)] = record
            return record

    def delete_link(self, context: TenantContext, document_id: str, link_id: str) -> bool:
        with self._lock:
            row = self._links.get((*self._ws(context), link_id))
            if row is None or row.document_id != document_id:
                return False
            del self._links[(*self._ws(context), link_id)]
            return True

    def add_extraction(self, context: TenantContext, record: ExtractionRecord) -> ExtractionRecord:
        with self._lock:
            self._extractions[(*self._ws(context), record.id)] = record.model_copy(deep=True)
            return record

    def save_extraction(self, context: TenantContext, record: ExtractionRecord) -> ExtractionRecord:
        with self._lock:
            if (*self._ws(context), record.id) not in self._extractions:
                raise LookupError(record.id)
            self._extractions[(*self._ws(context), record.id)] = record.model_copy(deep=True)
            return record

    def extraction(self, context: TenantContext, extraction_id: str) -> ExtractionRecord | None:
        with self._lock:
            row = self._extractions.get((*self._ws(context), extraction_id))
            if row is None or self.get_document(context, row.document_id) is None:
                return None
            return row.model_copy(deep=True)

    def extractions(self, context: TenantContext, document_id: str) -> list[ExtractionRecord]:
        with self._lock:
            rows = [e for (t, w, _), e in self._extractions.items() if (t, w) == self._ws(context) and e.document_id == document_id]
            return [e.model_copy(deep=True) for e in sorted(rows, key=lambda e: (e.created_at, e.id), reverse=True)]

    def open_extractions(self, context: TenantContext, limit: int) -> list[ExtractionRecord]:
        with self._lock:
            visible = {d.id for d in self._scoped_docs(context) if d.status == "active"}
            rows = [e for (t, w, _), e in self._extractions.items()
                    if (t, w) == self._ws(context) and e.status in ("queued", "pending") and e.document_id in visible]
            return [e.model_copy(deep=True) for e in sorted(rows, key=lambda e: (e.created_at, e.id))[:limit]]

    def log(self, context: TenantContext, event: AccessEvent) -> AccessEvent:
        with self._lock:
            self._events.append((context.tenant_id, context.workspace_id, event))
            return event

    def events(self, context: TenantContext, document_id: str, limit: int) -> list[AccessEvent]:
        with self._lock:
            rows = [e for t, w, e in self._events if (t, w) == self._ws(context) and e.document_id == document_id]
            return sorted(rows, key=lambda e: (e.created_at, e.id), reverse=True)[:limit]

    def devices(self, context: TenantContext, owner_user_id: str) -> list[DeviceRecord]:
        with self._lock:
            rows = [d for (t, w, _), d in self._devices.items() if (t, w) == self._ws(context) and d.owner_user_id == owner_user_id]
            return sorted(rows, key=lambda d: (d.created_at, d.id), reverse=True)

    def device(self, context: TenantContext, device_id: str) -> DeviceRecord | None:
        with self._lock:
            row = self._devices.get((*self._ws(context), device_id))
            return row.model_copy(deep=True) if row else None

    def save_device(self, context: TenantContext, record: DeviceRecord) -> DeviceRecord:
        with self._lock:
            self._devices[(*self._ws(context), record.id)] = record.model_copy(deep=True)
            return record

    def expiring(self, context: TenantContext, until: date) -> list[DocumentRecord]:
        with self._lock:
            return [d.model_copy(deep=True) for d in self._scoped_docs(context)
                    if d.status == "active" and d.expiry_date is not None and d.expiry_date <= until]

    def trash_due(self, context: TenantContext, now: datetime) -> list[DocumentRecord]:
        with self._lock:
            return [d.model_copy(deep=True) for d in self._scoped_docs(context)
                    if d.status == "deleted" and d.purge_after is not None and d.purge_after <= now]

    def retention_due(self, context: TenantContext, today: date) -> list[DocumentRecord]:
        with self._lock:
            return [d.model_copy(deep=True) for d in self._scoped_docs(context)
                    if d.status == "active" and d.retention_until is not None and d.retention_until <= today]

    def owned_by(self, context: TenantContext, user_id: str) -> list[DocumentRecord]:
        with self._lock:
            return [d.model_copy(deep=True) for (t, w, _), d in self._documents.items() if (t, w) == self._ws(context) and d.owner_user_id == user_id]

    def set_file_vault(self, context: TenantContext, file_id: str, value: str) -> None:
        from ..files import file_store

        with file_store._lock:
            record = file_store.records.get(file_id)
            if record is not None and (record.tenant_id, record.workspace_id) == self._ws(context):
                file_store.records[file_id] = record.model_copy(update={"vault": value})

    def workspaces_with_work(self) -> list[tuple[str, str]]:
        """Every workspace with a document (memory discovery for the daily job)."""
        with self._lock:
            return sorted({(t, w) for (t, w, _) in self._documents})

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        from ..dependencies import memory_repository

        return memory_repository.record_audit(record)


memory_vault_store = InMemoryVaultStore()


# --------------------------------------------------------------------------- PostgreSQL

_DOC_COLUMNS = (
    "id, tenant_id, workspace_id, file_id, owner_user_id, title, category, access_class, status, content_sha256, issue_date, expiry_date, "
    "issuer, country, tags, description, number_ciphertext, number_last4, receipt, assistant_may_read, reminder_days, reminders_sent, "
    "retention_until, source, row_version, deleted_at, deleted_by, purge_after, created_by, created_at, updated_by, updated_at"
)
_EXTRACTION_COLUMNS = (
    "id, document_id, file_sha256, kind, status, engine, languages, fields, items, warnings, error, requested_by, decided_by, "
    "decided_at, created_at, updated_at"
)
_DEVICE_COLUMNS = "id, owner_user_id, name, token_hash, hint, scopes, status, last_seen_at, last_error, upload_count, created_at, revoked_at"


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class SqlVaultStore:
    def __init__(self, session: Any) -> None:
        from ..db.repository import SqlAlchemyRepository

        self.session = session
        self._audit = SqlAlchemyRepository(session)

    @staticmethod
    def _p(context: TenantContext, **extra: Any) -> dict[str, Any]:
        return {"t": context.tenant_id, "w": context.workspace_id, **extra}

    @staticmethod
    def _doc(row: Any) -> DocumentRecord:
        data = dict(row._mapping)
        for key in ("tags", "receipt", "reminders_sent"):
            data[key] = _json(data.get(key))
        data["tags"] = data.get("tags") or []
        data["reminders_sent"] = data.get("reminders_sent") or []
        return DocumentRecord.model_validate(data)

    @staticmethod
    def _extraction(row: Any) -> ExtractionRecord:
        data = dict(row._mapping)
        for key in ("fields", "items", "warnings"):
            data[key] = _json(data.get(key))
        return ExtractionRecord.model_validate(data)

    @staticmethod
    def _device(row: Any) -> DeviceRecord:
        data = dict(row._mapping)
        data["scopes"] = _json(data.get("scopes")) or []
        return DeviceRecord.model_validate(data)

    # -- settings and categories

    def get_settings(self, context: TenantContext) -> VaultSettings:
        row = self.session.execute(text(
            "select reminder_days, trash_days, ocr_receipts, ocr_identity_dates, updated_by, updated_at from vault_settings "
            "where tenant_id = :t and workspace_id = :w"), self._p(context)).one_or_none()
        return VaultSettings.model_validate(dict(row._mapping)) if row is not None else VaultSettings()

    def save_settings(self, context: TenantContext, value: VaultSettings) -> VaultSettings:
        from ..db.scope import require_workspace

        require_workspace(self.session, context.tenant_id, context.workspace_id)
        self.session.execute(text(
            "insert into vault_settings (tenant_id, workspace_id, reminder_days, trash_days, ocr_receipts, ocr_identity_dates, updated_by, updated_at) "
            "values (:t, :w, :reminder, :trash, :receipts, :identity, :by, :at) on conflict (tenant_id, workspace_id) do update set "
            "reminder_days = excluded.reminder_days, trash_days = excluded.trash_days, ocr_receipts = excluded.ocr_receipts, "
            "ocr_identity_dates = excluded.ocr_identity_dates, updated_by = excluded.updated_by, updated_at = excluded.updated_at"),
            self._p(context, reminder=value.reminder_days, trash=value.trash_days, receipts=value.ocr_receipts, identity=value.ocr_identity_dates,
                    by=value.updated_by or context.user_id, at=value.updated_at))
        return value

    def categories(self, context: TenantContext) -> list[CategoryRecord]:
        rows = self.session.execute(text(
            "select id, label, created_by, created_at from vault_categories where tenant_id = :t and workspace_id = :w order by lower(label), id"),
            self._p(context)).all()
        return [CategoryRecord.model_validate(dict(row._mapping)) for row in rows]

    def add_category(self, context: TenantContext, record: CategoryRecord) -> CategoryRecord:
        from sqlalchemy.exc import IntegrityError

        from ..db.scope import require_workspace

        require_workspace(self.session, context.tenant_id, context.workspace_id)
        try:
            with self.session.begin_nested():
                self.session.execute(text(
                    "insert into vault_categories (tenant_id, workspace_id, id, label, created_by, created_at) values (:t, :w, :id, :label, :by, :at)"),
                    self._p(context, id=record.id, label=record.label, by=record.created_by, at=record.created_at))
        except IntegrityError as exc:
            raise CategoryTaken(record.label) from exc
        return record

    def delete_category(self, context: TenantContext, category_id: str) -> bool:
        # The job flag lets the check count private documents of other people too (ids only, nothing returned).
        self.session.execute(text("select set_config('anum.vault_job', 'on', true)"))
        try:
            used = self.session.execute(text(
                "select exists (select 1 from vault_documents where tenant_id = :t and workspace_id = :w and category = :c)"),
                self._p(context, c=category_id)).scalar_one()
        finally:
            self.session.execute(text("select set_config('anum.vault_job', :v, true)"), {"v": "on" if _JOB.get() else ""})
        if used:
            raise CategoryInUse(category_id)
        result = self.session.execute(text("delete from vault_categories where tenant_id = :t and workspace_id = :w and id = :id"),
                                      self._p(context, id=category_id))
        return bool(result.rowcount)

    # -- documents

    def _doc_values(self, context: TenantContext, record: DocumentRecord) -> dict[str, Any]:
        return self._p(
            context, id=record.id, file_id=record.file_id, owner=record.owner_user_id, title=record.title, category=record.category,
            access_class=record.access_class, status=record.status, sha=record.content_sha256, issue=record.issue_date,
            expiry=record.expiry_date, issuer=record.issuer, country=record.country, tags=json.dumps(record.tags, ensure_ascii=False),
            description=record.description, number=record.number_ciphertext, last4=record.number_last4,
            receipt=json.dumps(record.receipt, ensure_ascii=False) if record.receipt is not None else None,
            assistant=record.assistant_may_read, reminder=record.reminder_days, sent=json.dumps(record.reminders_sent),
            retention=record.retention_until, source=record.source, version=record.row_version, deleted_at=record.deleted_at,
            deleted_by=record.deleted_by, purge_after=record.purge_after, created_by=record.created_by, created_at=record.created_at,
            updated_by=record.updated_by, updated_at=record.updated_at,
        )

    def create_document(self, context: TenantContext, record: DocumentRecord) -> DocumentRecord:
        self.session.execute(text(
            "insert into vault_documents (" + _DOC_COLUMNS + ") values (:id, :t, :w, :file_id, :owner, :title, :category, :access_class, "  # nosec B608
            ":status, :sha, :issue, :expiry, :issuer, :country, cast(:tags as jsonb), :description, :number, :last4, cast(:receipt as jsonb), "
            ":assistant, :reminder, cast(:sent as jsonb), :retention, :source, :version, :deleted_at, :deleted_by, :purge_after, :created_by, "
            ":created_at, :updated_by, :updated_at)"), self._doc_values(context, record))
        return record

    def get_document(self, context: TenantContext, document_id: str, *, for_update: bool = False) -> DocumentRecord | None:
        lock = " for update" if for_update else ""
        row = self.session.execute(text(
            f"select {_DOC_COLUMNS} from vault_documents where tenant_id = :t and workspace_id = :w and id = :id{lock}"),  # nosec B608
            self._p(context, id=document_id)).one_or_none()
        return self._doc(row) if row is not None else None

    def save_document(self, context: TenantContext, record: DocumentRecord, expected_version: int) -> DocumentRecord:
        values = self._doc_values(context, record)
        values["expected"] = expected_version
        result = self.session.execute(text(
            "update vault_documents set title = :title, category = :category, access_class = :access_class, status = :status, "
            "content_sha256 = :sha, issue_date = :issue, expiry_date = :expiry, issuer = :issuer, country = :country, tags = cast(:tags as jsonb), "
            "description = :description, number_ciphertext = :number, number_last4 = :last4, receipt = cast(:receipt as jsonb), "
            "assistant_may_read = :assistant, reminder_days = :reminder, reminders_sent = cast(:sent as jsonb), retention_until = :retention, "
            "row_version = row_version + 1, deleted_at = :deleted_at, deleted_by = :deleted_by, purge_after = :purge_after, "
            "updated_by = :updated_by, updated_at = :updated_at "
            "where tenant_id = :t and workspace_id = :w and id = :id and row_version = :expected"), values)
        if result.rowcount != 1:
            raise StaleDocument(record.id)
        return record.model_copy(update={"row_version": expected_version + 1})

    def remove_document(self, context: TenantContext, document_id: str) -> bool:
        result = self.session.execute(text("delete from vault_documents where tenant_id = :t and workspace_id = :w and id = :id"),
                                      self._p(context, id=document_id))
        return bool(result.rowcount)

    def list_documents(self, context: TenantContext, query: DocumentQuery, today: date) -> list[DocumentRecord]:
        where = ["tenant_id = :t", "workspace_id = :w", "status = :status"]
        params = self._p(context, status=query.status, limit=query.limit, today=today)
        if query.category:
            where.append("category = :category")
            params["category"] = query.category
        if query.access_class:
            where.append("access_class = :access_class")
            params["access_class"] = query.access_class
        if query.tag:
            where.append("exists (select 1 from jsonb_array_elements_text(tags) tag where lower(tag) = lower(:tag))")
            params["tag"] = query.tag
        if query.expired:
            where.append("expiry_date < :today")
        if query.expiring_within_days is not None:
            where.append("expiry_date between :today and :until")
            params["until"] = today + timedelta(days=query.expiring_within_days)
        if query.search:
            where.append(
                "(title ilike :q escape '\\' or coalesce(issuer, '') ilike :q escape '\\' or description ilike :q escape '\\' "
                "or tags::text ilike :q escape '\\' or coalesce(number_last4, '') ilike :q escape '\\')"
            )
            params["q"] = _like(query.search)
        if query.after is not None:
            where.append("(created_at, id) < (:after_at, :after_id)")
            params["after_at"], params["after_id"] = query.after
        rows = self.session.execute(text(
            f"select {_DOC_COLUMNS} from vault_documents where {' and '.join(where)} order by created_at desc, id desc limit :limit"),  # nosec B608 - fixed fragments, bound values
            params).all()
        return [self._doc(row) for row in rows]

    def same_content(self, context: TenantContext, sha256: str) -> list[DocumentRecord]:
        rows = self.session.execute(text(
            f"select {_DOC_COLUMNS} from vault_documents where tenant_id = :t and workspace_id = :w and content_sha256 = :sha "  # nosec B608
            "order by created_at limit 5"), self._p(context, sha=sha256)).all()
        return [self._doc(row) for row in rows]

    def summary(self, context: TenantContext, today: date, within_days: int) -> Summary:
        row = self.session.execute(text(
            """
            select
              count(*) filter (where status = 'active') as active,
              count(*) filter (where status = 'active' and access_class = 'private') as private,
              count(*) filter (where status = 'active' and expiry_date between :today and :until) as expiring,
              count(*) filter (where status = 'active' and expiry_date < :today) as expired,
              count(*) filter (where status = 'deleted') as trash
            from vault_documents where tenant_id = :t and workspace_id = :w
            """), self._p(context, today=today, until=today + timedelta(days=within_days))).one()
        review = self.session.execute(text(
            "select count(*) from vault_extractions e join vault_documents d on d.tenant_id = e.tenant_id and d.workspace_id = e.workspace_id "
            "and d.id = e.document_id where e.tenant_id = :t and e.workspace_id = :w and e.status in ('queued', 'pending') and d.status = 'active'"),
            self._p(context)).scalar_one()
        return Summary(active=row.active, private=row.private, expiring=row.expiring, expired=row.expired, review=int(review), trash=row.trash)

    # -- access, links

    def access(self, context: TenantContext, document_id: str) -> list[AccessGrant]:
        rows = self.session.execute(text(
            "select document_id, user_id, granted_by, granted_at from vault_document_access where tenant_id = :t and workspace_id = :w "
            "and document_id = :d order by granted_at, user_id"), self._p(context, d=document_id)).all()
        return [AccessGrant.model_validate(dict(row._mapping)) for row in rows]

    def grant(self, context: TenantContext, grant: AccessGrant) -> AccessGrant:
        self.session.execute(text(
            "insert into vault_document_access (tenant_id, workspace_id, document_id, user_id, granted_by, granted_at) "
            "values (:t, :w, :d, :u, :by, :at) on conflict do nothing"),
            self._p(context, d=grant.document_id, u=grant.user_id, by=grant.granted_by, at=grant.granted_at))
        return grant

    def revoke(self, context: TenantContext, document_id: str, user_id: str) -> bool:
        result = self.session.execute(text(
            "delete from vault_document_access where tenant_id = :t and workspace_id = :w and document_id = :d and user_id = :u"),
            self._p(context, d=document_id, u=user_id))
        return bool(result.rowcount)

    def links(self, context: TenantContext, document_id: str) -> list[LinkRecord]:
        rows = self.session.execute(text(
            "select id, document_id, target_type, target_id, label, status, created_by, created_at from vault_document_links "
            "where tenant_id = :t and workspace_id = :w and document_id = :d order by created_at, id"), self._p(context, d=document_id)).all()
        return [LinkRecord.model_validate(dict(row._mapping)) for row in rows]

    def add_link(self, context: TenantContext, record: LinkRecord) -> LinkRecord:
        self.session.execute(text(
            "insert into vault_document_links (tenant_id, workspace_id, id, document_id, target_type, target_id, label, status, created_by, created_at) "
            "values (:t, :w, :id, :d, :type, :target, :label, :status, :by, :at) on conflict (tenant_id, workspace_id, document_id, target_type, target_id) do nothing"),
            self._p(context, id=record.id, d=record.document_id, type=record.target_type, target=record.target_id, label=record.label,
                    status=record.status, by=record.created_by, at=record.created_at))
        for link in self.links(context, record.document_id):
            if (link.target_type, link.target_id) == (record.target_type, record.target_id):
                return link
        return record

    def delete_link(self, context: TenantContext, document_id: str, link_id: str) -> bool:
        result = self.session.execute(text(
            "delete from vault_document_links where tenant_id = :t and workspace_id = :w and document_id = :d and id = :id"),
            self._p(context, d=document_id, id=link_id))
        return bool(result.rowcount)

    # -- extractions

    def _extraction_values(self, context: TenantContext, record: ExtractionRecord) -> dict[str, Any]:
        return self._p(
            context, id=record.id, d=record.document_id, sha=record.file_sha256, kind=record.kind, status=record.status, engine=record.engine[:80],
            languages=record.languages[:40], fields=json.dumps(record.fields, ensure_ascii=False), items=json.dumps(record.items, ensure_ascii=False),
            warnings=json.dumps(record.warnings), error=(record.error or None) and record.error[:300], requested_by=record.requested_by,
            decided_by=record.decided_by, decided_at=record.decided_at, created_at=record.created_at, updated_at=record.updated_at,
        )

    def add_extraction(self, context: TenantContext, record: ExtractionRecord) -> ExtractionRecord:
        self.session.execute(text(
            "insert into vault_extractions (tenant_id, workspace_id, " + _EXTRACTION_COLUMNS + ") values (:t, :w, :id, :d, :sha, :kind, :status, "  # nosec B608
            ":engine, :languages, cast(:fields as jsonb), cast(:items as jsonb), cast(:warnings as jsonb), :error, :requested_by, :decided_by, "
            ":decided_at, :created_at, :updated_at)"), self._extraction_values(context, record))
        return record

    def save_extraction(self, context: TenantContext, record: ExtractionRecord) -> ExtractionRecord:
        result = self.session.execute(text(
            "update vault_extractions set status = :status, engine = :engine, languages = :languages, fields = cast(:fields as jsonb), "
            "items = cast(:items as jsonb), warnings = cast(:warnings as jsonb), error = :error, decided_by = :decided_by, "
            "decided_at = :decided_at, updated_at = :updated_at where tenant_id = :t and workspace_id = :w and id = :id"),
            self._extraction_values(context, record))
        if result.rowcount != 1:
            raise LookupError(record.id)
        return record

    def extraction(self, context: TenantContext, extraction_id: str) -> ExtractionRecord | None:
        # Joined to the document so a private document's extraction follows its visibility.
        row = self.session.execute(text(
            "select " + ", ".join(f"e.{c.strip()}" for c in _EXTRACTION_COLUMNS.split(",")) + " from vault_extractions e "  # nosec B608
            "join vault_documents d on d.tenant_id = e.tenant_id and d.workspace_id = e.workspace_id and d.id = e.document_id "
            "where e.tenant_id = :t and e.workspace_id = :w and e.id = :id"), self._p(context, id=extraction_id)).one_or_none()
        return self._extraction(row) if row is not None else None

    def extractions(self, context: TenantContext, document_id: str) -> list[ExtractionRecord]:
        rows = self.session.execute(text(
            f"select {_EXTRACTION_COLUMNS} from vault_extractions where tenant_id = :t and workspace_id = :w and document_id = :d "  # nosec B608
            "order by created_at desc, id desc limit 20"), self._p(context, d=document_id)).all()
        return [self._extraction(row) for row in rows]

    def open_extractions(self, context: TenantContext, limit: int) -> list[ExtractionRecord]:
        rows = self.session.execute(text(
            "select " + ", ".join(f"e.{c.strip()}" for c in _EXTRACTION_COLUMNS.split(",")) + " from vault_extractions e "  # nosec B608
            "join vault_documents d on d.tenant_id = e.tenant_id and d.workspace_id = e.workspace_id and d.id = e.document_id "
            "where e.tenant_id = :t and e.workspace_id = :w and e.status in ('queued', 'pending') and d.status = 'active' "
            "order by e.created_at, e.id limit :limit"), self._p(context, limit=limit)).all()
        return [self._extraction(row) for row in rows]

    # -- access log

    def log(self, context: TenantContext, event: AccessEvent) -> AccessEvent:
        self.session.execute(text(
            "insert into vault_access_log (tenant_id, workspace_id, id, document_id, actor, action, detail, created_at) "
            "values (:t, :w, :id, :d, :actor, :action, cast(:detail as jsonb), :at)"),
            self._p(context, id=event.id, d=event.document_id, actor=event.actor, action=event.action, detail=json.dumps(event.detail), at=event.created_at))
        return event

    def events(self, context: TenantContext, document_id: str, limit: int) -> list[AccessEvent]:
        rows = self.session.execute(text(
            "select id, document_id, actor, action, detail, created_at from vault_access_log where tenant_id = :t and workspace_id = :w "
            "and document_id = :d order by created_at desc, id desc limit :limit"), self._p(context, d=document_id, limit=limit)).all()
        return [AccessEvent.model_validate({**dict(row._mapping), "detail": _json(row.detail) or {}}) for row in rows]

    # -- devices

    def devices(self, context: TenantContext, owner_user_id: str) -> list[DeviceRecord]:
        rows = self.session.execute(text(
            f"select {_DEVICE_COLUMNS} from vault_devices where tenant_id = :t and workspace_id = :w and owner_user_id = :o "  # nosec B608
            "order by created_at desc, id desc"), self._p(context, o=owner_user_id)).all()
        return [self._device(row) for row in rows]

    def device(self, context: TenantContext, device_id: str) -> DeviceRecord | None:
        row = self.session.execute(text(
            f"select {_DEVICE_COLUMNS} from vault_devices where tenant_id = :t and workspace_id = :w and id = :id"),  # nosec B608
            self._p(context, id=device_id)).one_or_none()
        return self._device(row) if row is not None else None

    def save_device(self, context: TenantContext, record: DeviceRecord) -> DeviceRecord:
        from ..db.scope import require_workspace

        require_workspace(self.session, context.tenant_id, context.workspace_id)
        self.session.execute(text(
            "insert into vault_devices (tenant_id, workspace_id, " + _DEVICE_COLUMNS + ") values (:t, :w, :id, :owner, :name, :hash, :hint, "  # nosec B608
            "cast(:scopes as jsonb), :status, :seen, :error, :count, :created, :revoked) on conflict (tenant_id, workspace_id, id) do update set "
            "name = excluded.name, status = excluded.status, last_seen_at = excluded.last_seen_at, last_error = excluded.last_error, "
            "upload_count = excluded.upload_count, revoked_at = excluded.revoked_at"),
            self._p(context, id=record.id, owner=record.owner_user_id, name=record.name, hash=record.token_hash, hint=record.hint,
                    scopes=json.dumps(record.scopes), status=record.status, seen=record.last_seen_at, error=record.last_error,
                    count=record.upload_count, created=record.created_at, revoked=record.revoked_at))
        return record

    # -- the daily job (anum.vault_job is set, so private documents are included)

    def expiring(self, context: TenantContext, until: date) -> list[DocumentRecord]:
        rows = self.session.execute(text(
            f"select {_DOC_COLUMNS} from vault_documents where tenant_id = :t and workspace_id = :w and status = 'active' "  # nosec B608
            "and expiry_date is not null and expiry_date <= :until order by expiry_date, id limit 500"), self._p(context, until=until)).all()
        return [self._doc(row) for row in rows]

    def trash_due(self, context: TenantContext, now: datetime) -> list[DocumentRecord]:
        rows = self.session.execute(text(
            f"select {_DOC_COLUMNS} from vault_documents where tenant_id = :t and workspace_id = :w and status = 'deleted' "  # nosec B608
            "and purge_after <= :now order by purge_after, id limit 200"), self._p(context, now=now)).all()
        return [self._doc(row) for row in rows]

    def retention_due(self, context: TenantContext, today: date) -> list[DocumentRecord]:
        rows = self.session.execute(text(
            f"select {_DOC_COLUMNS} from vault_documents where tenant_id = :t and workspace_id = :w and status = 'active' "  # nosec B608
            "and retention_until <= :today order by retention_until, id limit 200"), self._p(context, today=today)).all()
        return [self._doc(row) for row in rows]

    def owned_by(self, context: TenantContext, user_id: str) -> list[DocumentRecord]:
        rows = self.session.execute(text(
            f"select {_DOC_COLUMNS} from vault_documents where tenant_id = :t and workspace_id = :w and owner_user_id = :u "  # nosec B608
            "order by created_at, id"), self._p(context, u=user_id)).all()
        return [self._doc(row) for row in rows]

    def set_file_vault(self, context: TenantContext, file_id: str, value: str) -> None:
        self.session.execute(text("update workspace_files set vault = :v where tenant_id = :t and workspace_id = :w and id = :id"),
                             self._p(context, v=value, id=file_id))

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        return self._audit.record_audit(record)


# --------------------------------------------------------------------------- units of work


@dataclass
class VaultUnit:
    store: VaultStore
    files: Any  # files.FileMetadataStore, sharing the vault's session with PostgreSQL


@contextmanager
def open_vault_unit(context: TenantContext, *, job: bool = False) -> Iterator[VaultUnit]:
    """One unit of work over the vault tables and the file metadata (hidden vault files visible).

    ``job``: the daily job's unit, which sees every document of the workspace (anum.vault_job).
    """
    from ..files import file_store, vault_files_visible
    from ..scoped_store import ScopeNotProvisionedError

    job_token = _JOB.set(job)
    try:
        with vault_files_visible():
            if settings.repository_backend == "memory":
                yield VaultUnit(memory_vault_store, file_store)
                return
            from ..db import session as db_session
            from ..db.workspace_settings_repository import SqlAlchemyFileMetadataStore

            session = db_session.SessionLocal()
            try:
                db_session.set_tenant_context(session, context.tenant_id, context.workspace_id, user_id=context.user_id)
                session.execute(text("select set_config('anum.vault', 'on', true)"))
                if job:
                    session.execute(text("select set_config('anum.vault_job', 'on', true)"))
                yield VaultUnit(SqlVaultStore(session), SqlAlchemyFileMetadataStore(session))
                session.commit()
            except ScopeNotProvisionedError as exc:
                session.rollback()
                raise HTTPException(status.HTTP_409_CONFLICT, "Complete onboarding for this workspace first") from exc
            except BaseException:
                session.rollback()
                raise
            finally:
                session.close()
    finally:
        _JOB.reset(job_token)


__all__ = [
    "CategoryInUse", "CategoryTaken", "InMemoryVaultStore", "SqlVaultStore", "StaleDocument", "Summary", "VaultStore", "VaultUnit",
    "memory_vault_store", "open_vault_unit",
]
