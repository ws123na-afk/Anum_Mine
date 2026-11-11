"""Sessions, the step log and saved credentials of the action browser (migration ``0130_action_browser``).

Three workspace tables under forced RLS; the in-memory backend keeps the same rows in the
process for local runs and tests. Every call runs in its own short unit of work in a worker
thread (``open_action_store`` through ``open_scoped_store``), never on the event loop and never
across a browser or model call (docs/realtime.md#event-loop).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from typing import Any, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import text

from ..audit import AuditRecord
from ..schemas import TenantContext
from ..scoped_store import open_scoped_store

TERMINAL = frozenset({"completed", "failed", "stopped"})


class SessionRecord(BaseModel):
    id: str
    task_id: str
    run_id: str
    created_by: str
    status: str = "running"
    goal: str
    start_url: str
    sites: list[str] = Field(default_factory=list)
    # Login handle to the site it belongs to; card handles. Fixed at the scope approval.
    logins: dict[str, str] = Field(default_factory=dict)
    cards: list[str] = Field(default_factory=list)
    # ``card:host:form`` keys the owner approved for card entry.
    grants: list[str] = Field(default_factory=list)
    browser_id: str | None = None
    step_count: int = 0
    work_seconds: float = 0.0
    current_url: str | None = None
    current_title: str | None = None
    waiting: str | None = None
    pending: dict[str, Any] | None = None
    outcome: str | None = None
    message: str = ""
    # {"answer", "fields": {...}, "columns": [...], "rows": [[...]], "files": [{"id", "name", "size"}]}
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None

    @property
    def ended(self) -> bool:
        return self.status in TERMINAL


class StepRecord(BaseModel):
    session_id: str
    seq: int
    kind: str
    summary: str
    url: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CredentialRecord(BaseModel):
    id: str
    kind: str
    handle: str
    site: str | None = None
    hint: str
    secret_ciphertext: str = Field(repr=False)
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime


class ActionStore(Protocol):
    def create_session(self, context: TenantContext, record: SessionRecord) -> SessionRecord: ...
    def get_session(self, context: TenantContext, session_id: str) -> SessionRecord | None: ...
    def save_session(self, context: TenantContext, record: SessionRecord) -> SessionRecord: ...
    def sessions_for_task(self, context: TenantContext, task_id: str) -> list[SessionRecord]: ...
    def recent_sessions(self, context: TenantContext, *, limit: int, offset: int = 0) -> list[SessionRecord]: ...
    def count_since(self, context: TenantContext, since: datetime) -> int: ...
    def add_step(self, context: TenantContext, step: StepRecord) -> StepRecord: ...
    def steps(self, context: TenantContext, session_id: str, *, after: int = 0, limit: int = 200) -> list[StepRecord]: ...
    def credentials(self, context: TenantContext) -> list[CredentialRecord]: ...
    def credential(self, context: TenantContext, credential_id: str, *, for_update: bool = False) -> CredentialRecord | None: ...
    def credential_by_handle(self, context: TenantContext, kind: str, handle: str) -> CredentialRecord | None: ...
    def save_credential(self, context: TenantContext, record: CredentialRecord) -> CredentialRecord: ...
    def delete_credential(self, context: TenantContext, credential_id: str) -> bool: ...
    def record_audit(self, record: AuditRecord) -> AuditRecord: ...


class InMemoryActionStore:
    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str, str], SessionRecord] = {}
        self._steps: dict[tuple[str, str, str], list[StepRecord]] = {}
        self._credentials: dict[tuple[str, str, str], CredentialRecord] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._steps.clear()
            self._credentials.clear()

    @staticmethod
    def _key(context: TenantContext, item_id: str) -> tuple[str, str, str]:
        return (context.tenant_id, context.workspace_id, item_id)

    def create_session(self, context: TenantContext, record: SessionRecord) -> SessionRecord:
        with self._lock:
            self._sessions[self._key(context, record.id)] = record.model_copy(deep=True)
            return record

    def get_session(self, context: TenantContext, session_id: str) -> SessionRecord | None:
        with self._lock:
            row = self._sessions.get(self._key(context, session_id))
            return row.model_copy(deep=True) if row else None

    def save_session(self, context: TenantContext, record: SessionRecord) -> SessionRecord:
        with self._lock:
            if self._key(context, record.id) not in self._sessions:
                raise LookupError("session not found")
            self._sessions[self._key(context, record.id)] = record.model_copy(deep=True)
            return record

    def sessions_for_task(self, context: TenantContext, task_id: str) -> list[SessionRecord]:
        with self._lock:
            rows = [row for (t, w, _), row in self._sessions.items() if (t, w) == (context.tenant_id, context.workspace_id) and row.task_id == task_id]
            return [row.model_copy(deep=True) for row in sorted(rows, key=lambda r: r.created_at)]

    def recent_sessions(self, context: TenantContext, *, limit: int, offset: int = 0) -> list[SessionRecord]:
        """The workspace's sessions, newest first (the runs list, docs/action-browser.md#api)."""
        with self._lock:
            rows = [row for (t, w, _), row in self._sessions.items() if (t, w) == (context.tenant_id, context.workspace_id)]
            rows.sort(key=lambda r: (r.created_at, r.id), reverse=True)
            return [row.model_copy(deep=True) for row in rows[offset:offset + limit]]

    def count_since(self, context: TenantContext, since: datetime) -> int:
        with self._lock:
            return sum(1 for (t, w, _), row in self._sessions.items() if (t, w) == (context.tenant_id, context.workspace_id) and row.created_at >= since)

    def add_step(self, context: TenantContext, step: StepRecord) -> StepRecord:
        with self._lock:
            rows = self._steps.setdefault(self._key(context, step.session_id), [])
            saved = step.model_copy(update={"seq": len(rows) + 1})
            rows.append(saved)
            return saved

    def steps(self, context: TenantContext, session_id: str, *, after: int = 0, limit: int = 200) -> list[StepRecord]:
        with self._lock:
            return [row.model_copy() for row in self._steps.get(self._key(context, session_id), []) if row.seq > after][:limit]

    def credentials(self, context: TenantContext) -> list[CredentialRecord]:
        with self._lock:
            rows = [row for (t, w, _), row in self._credentials.items() if (t, w) == (context.tenant_id, context.workspace_id)]
            return [row.model_copy() for row in sorted(rows, key=lambda r: (r.kind, r.handle))]

    def credential(self, context: TenantContext, credential_id: str, *, for_update: bool = False) -> CredentialRecord | None:
        with self._lock:
            row = self._credentials.get(self._key(context, credential_id))
            return row.model_copy() if row else None

    def credential_scopes(self) -> list[tuple[str, str, str]]:
        """(tenant, workspace, id) of every saved credential, for the card-code cleanup in local runs."""
        with self._lock:
            return sorted(self._credentials)

    def credential_by_handle(self, context: TenantContext, kind: str, handle: str) -> CredentialRecord | None:
        with self._lock:
            for (t, w, _), row in self._credentials.items():
                if (t, w) == (context.tenant_id, context.workspace_id) and row.kind == kind and row.handle == handle:
                    return row.model_copy()
            return None

    def save_credential(self, context: TenantContext, record: CredentialRecord) -> CredentialRecord:
        with self._lock:
            clash = self.credential_by_handle(context, record.kind, record.handle)
            if clash is not None and clash.id != record.id:
                raise ValueError("handle_taken")
            self._credentials[self._key(context, record.id)] = record.model_copy()
            return record

    def delete_credential(self, context: TenantContext, credential_id: str) -> bool:
        with self._lock:
            return self._credentials.pop(self._key(context, credential_id), None) is not None

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        from ..dependencies import memory_repository

        return memory_repository.record_audit(record)


memory_action_store = InMemoryActionStore()

_SESSION_COLUMNS = (
    "id, task_id, run_id, created_by, status, goal, start_url, sites, logins, cards, grants, browser_id, step_count, work_seconds, "
    "current_url, current_title, waiting, pending, outcome, message, result, created_at, updated_at, ended_at"
)
_CREDENTIAL_COLUMNS = "id, kind, handle, site, hint, secret_ciphertext, created_by, created_at, updated_by, updated_at"


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class SqlActionStore:
    """The three tables of migration 0130 through the session's tenant and workspace RLS context."""

    def __init__(self, session: Any) -> None:
        from ..db.repository import SqlAlchemyRepository

        self.session = session
        self._audit = SqlAlchemyRepository(session)

    @staticmethod
    def _params(context: TenantContext) -> dict[str, str]:
        return {"t": context.tenant_id, "w": context.workspace_id}

    @staticmethod
    def _session(row: Any) -> SessionRecord:
        data = dict(row._mapping)
        for key in ("sites", "logins", "cards", "grants", "pending", "result"):
            data[key] = _json(data.get(key))
        if data.get("grants") is None:
            data["grants"] = []
        if data.get("result") is None:
            data["result"] = {}
        return SessionRecord.model_validate(data)

    def _session_values(self, context: TenantContext, record: SessionRecord) -> dict[str, Any]:
        return {
            **self._params(context), "id": record.id, "task_id": record.task_id, "run_id": record.run_id, "created_by": record.created_by,
            "status": record.status, "goal": record.goal, "start_url": record.start_url, "sites": json.dumps(record.sites),
            "logins": json.dumps(record.logins), "cards": json.dumps(record.cards), "grants": json.dumps(record.grants),
            "browser_id": record.browser_id, "step_count": record.step_count, "work_seconds": record.work_seconds,
            "current_url": record.current_url, "current_title": record.current_title, "waiting": record.waiting,
            "pending": json.dumps(record.pending) if record.pending is not None else None, "outcome": record.outcome,
            "message": record.message[:500], "result": json.dumps(record.result, ensure_ascii=False), "created_at": record.created_at, "updated_at": record.updated_at, "ended_at": record.ended_at,
        }

    def create_session(self, context: TenantContext, record: SessionRecord) -> SessionRecord:
        from ..db.scope import require_workspace

        require_workspace(self.session, context.tenant_id, context.workspace_id)
        self.session.execute(text(
            "insert into action_browser_sessions (tenant_id, workspace_id, " + _SESSION_COLUMNS + ") values (:t, :w, :id, :task_id, :run_id, "  # nosec B608
            ":created_by, :status, :goal, :start_url, cast(:sites as jsonb), cast(:logins as jsonb), cast(:cards as jsonb), cast(:grants as jsonb), "
            ":browser_id, :step_count, :work_seconds, :current_url, :current_title, :waiting, cast(:pending as jsonb), :outcome, :message, "
            "cast(:result as jsonb), :created_at, :updated_at, :ended_at)"), self._session_values(context, record))
        return record

    def get_session(self, context: TenantContext, session_id: str) -> SessionRecord | None:
        row = self.session.execute(text(
            f"select {_SESSION_COLUMNS} from action_browser_sessions where tenant_id = :t and workspace_id = :w and id = :id"),  # nosec B608 - fixed columns
            {**self._params(context), "id": session_id}).one_or_none()
        return self._session(row) if row is not None else None

    def save_session(self, context: TenantContext, record: SessionRecord) -> SessionRecord:
        result = self.session.execute(text(
            "update action_browser_sessions set status = :status, grants = cast(:grants as jsonb), sites = cast(:sites as jsonb), "
            "browser_id = :browser_id, step_count = :step_count, work_seconds = :work_seconds, current_url = :current_url, "
            "current_title = :current_title, waiting = :waiting, pending = cast(:pending as jsonb), outcome = :outcome, message = :message, result = cast(:result as jsonb), "
            "run_id = :run_id, updated_at = :updated_at, ended_at = :ended_at where tenant_id = :t and workspace_id = :w and id = :id"),
            self._session_values(context, record))
        if result.rowcount != 1:
            raise LookupError("session not found")
        return record

    def sessions_for_task(self, context: TenantContext, task_id: str) -> list[SessionRecord]:
        rows = self.session.execute(text(
            f"select {_SESSION_COLUMNS} from action_browser_sessions where tenant_id = :t and workspace_id = :w and task_id = :task "  # nosec B608
            "order by created_at, id limit 50"), {**self._params(context), "task": task_id}).all()
        return [self._session(row) for row in rows]

    def recent_sessions(self, context: TenantContext, *, limit: int, offset: int = 0) -> list[SessionRecord]:
        rows = self.session.execute(text(
            f"select {_SESSION_COLUMNS} from action_browser_sessions where tenant_id = :t and workspace_id = :w "  # nosec B608 - fixed columns
            "order by created_at desc, id desc limit :limit offset :offset"),
            {**self._params(context), "limit": limit, "offset": offset}).all()
        return [self._session(row) for row in rows]

    def count_since(self, context: TenantContext, since: datetime) -> int:
        return int(self.session.execute(text(
            "select count(*) from action_browser_sessions where tenant_id = :t and workspace_id = :w and created_at >= :since"),
            {**self._params(context), "since": since}).scalar_one())

    def add_step(self, context: TenantContext, step: StepRecord) -> StepRecord:
        # One writer per session at a time (the task's run); the primary key refuses a race.
        seq = int(self.session.execute(text(
            "select coalesce(max(seq), 0) + 1 from action_browser_steps where tenant_id = :t and workspace_id = :w and session_id = :s"),
            {**self._params(context), "s": step.session_id}).scalar_one())
        saved = step.model_copy(update={"seq": seq})
        self.session.execute(text(
            "insert into action_browser_steps (tenant_id, workspace_id, session_id, seq, kind, summary, url, detail, created_at) "
            "values (:t, :w, :s, :seq, :kind, :summary, :url, cast(:detail as jsonb), :at)"),
            {**self._params(context), "s": step.session_id, "seq": seq, "kind": step.kind[:30], "summary": step.summary[:300],
             "url": (step.url or None) and step.url[:2048], "detail": json.dumps(step.detail), "at": step.created_at})
        return saved

    def steps(self, context: TenantContext, session_id: str, *, after: int = 0, limit: int = 200) -> list[StepRecord]:
        rows = self.session.execute(text(
            "select session_id, seq, kind, summary, url, detail, created_at from action_browser_steps "
            "where tenant_id = :t and workspace_id = :w and session_id = :s and seq > :after order by seq limit :limit"),
            {**self._params(context), "s": session_id, "after": after, "limit": limit}).all()
        return [StepRecord.model_validate({**dict(row._mapping), "detail": _json(row.detail) or {}}) for row in rows]

    def credentials(self, context: TenantContext) -> list[CredentialRecord]:
        rows = self.session.execute(text(
            f"select {_CREDENTIAL_COLUMNS} from action_browser_credentials where tenant_id = :t and workspace_id = :w order by kind, handle"),  # nosec B608
            self._params(context)).all()
        return [CredentialRecord.model_validate(dict(row._mapping)) for row in rows]

    def credential(self, context: TenantContext, credential_id: str, *, for_update: bool = False) -> CredentialRecord | None:
        lock = " for update" if for_update else ""
        row = self.session.execute(text(
            f"select {_CREDENTIAL_COLUMNS} from action_browser_credentials where tenant_id = :t and workspace_id = :w and id = :id{lock}"),  # nosec B608
            {**self._params(context), "id": credential_id}).one_or_none()
        return CredentialRecord.model_validate(dict(row._mapping)) if row is not None else None

    def credential_by_handle(self, context: TenantContext, kind: str, handle: str) -> CredentialRecord | None:
        row = self.session.execute(text(
            f"select {_CREDENTIAL_COLUMNS} from action_browser_credentials where tenant_id = :t and workspace_id = :w "  # nosec B608
            "and kind = :kind and handle = :handle"), {**self._params(context), "kind": kind, "handle": handle}).one_or_none()
        return CredentialRecord.model_validate(dict(row._mapping)) if row is not None else None

    def save_credential(self, context: TenantContext, record: CredentialRecord) -> CredentialRecord:
        from ..db.scope import require_workspace

        clash = self.credential_by_handle(context, record.kind, record.handle)
        if clash is not None and clash.id != record.id:
            raise ValueError("handle_taken")
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        self.session.execute(text(
            "insert into action_browser_credentials (tenant_id, workspace_id, " + _CREDENTIAL_COLUMNS + ") values (:t, :w, :id, :kind, :handle, "  # nosec B608
            ":site, :hint, :secret, :created_by, :created_at, :updated_by, :updated_at) on conflict (tenant_id, workspace_id, id) do update set "
            "handle = excluded.handle, site = excluded.site, hint = excluded.hint, secret_ciphertext = excluded.secret_ciphertext, "
            "updated_by = excluded.updated_by, updated_at = excluded.updated_at"),
            {**self._params(context), "id": record.id, "kind": record.kind, "handle": record.handle, "site": record.site, "hint": record.hint,
             "secret": record.secret_ciphertext, "created_by": record.created_by, "created_at": record.created_at,
             "updated_by": record.updated_by, "updated_at": record.updated_at})
        return record

    def delete_credential(self, context: TenantContext, credential_id: str) -> bool:
        result = self.session.execute(text(
            "delete from action_browser_credentials where tenant_id = :t and workspace_id = :w and id = :id"),
            {**self._params(context), "id": credential_id})
        return bool(result.rowcount)

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        return self._audit.record_audit(record)


@contextmanager
def open_action_store(context: TenantContext) -> Iterator[ActionStore]:
    with open_scoped_store(context, memory_action_store, SqlActionStore) as store:
        yield store


__all__ = ["ActionStore", "CredentialRecord", "InMemoryActionStore", "SessionRecord", "SqlActionStore", "StepRecord", "TERMINAL", "memory_action_store", "open_action_store"]
