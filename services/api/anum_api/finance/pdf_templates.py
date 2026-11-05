"""PDF statement layouts the owner taught (docs/finance-statements.md#templates).

When the owner corrects the column mapping or the date format in the PDF preview and imports, the
choice is kept against the layout's fingerprint (header words and column positions, nothing personal).
The next statement whose layout has the same fingerprint is read with it. Only the person who taught a
template sees or uses it (owner-only RLS, migration ``0139_finance_pdf_templates``); they can list and
delete their templates in Finance, Import.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field

from ..schemas import TenantContext
from ..scoped_store import open_scoped_store

MAX_TEMPLATES = 100


class StatementTemplate(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    fingerprint: str = Field(pattern=r"^[0-9a-f]{32}$")
    labels: list[str] = Field(max_length=12)
    roles: list[str] = Field(max_length=12)
    date_format: str | None = None
    uses: int = 0
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None


class TemplateStore(Protocol):
    def by_fingerprint(self, context: TenantContext, fingerprint: str) -> StatementTemplate | None: ...
    def list(self, context: TenantContext) -> list[StatementTemplate]: ...
    def save(self, template: StatementTemplate) -> StatementTemplate: ...
    def delete(self, context: TenantContext, template_id: str) -> bool: ...


def _mine(item: StatementTemplate, context: TenantContext) -> bool:
    return (item.tenant_id, item.workspace_id, item.owner_user_id) == (context.tenant_id, context.workspace_id, context.user_id)


class InMemoryTemplateStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.items: dict[str, StatementTemplate] = {}

    def clear(self) -> None:
        with self._lock:
            self.items.clear()

    def by_fingerprint(self, context: TenantContext, fingerprint: str) -> StatementTemplate | None:
        with self._lock:
            return next((t.model_copy() for t in self.items.values() if _mine(t, context) and t.fingerprint == fingerprint), None)

    def list(self, context: TenantContext) -> list[StatementTemplate]:
        with self._lock:
            found = [t.model_copy() for t in self.items.values() if _mine(t, context)]
        return sorted(found, key=lambda t: t.updated_at, reverse=True)

    def save(self, template: StatementTemplate) -> StatementTemplate:
        with self._lock:
            same = next((k for k, t in self.items.items() if t.fingerprint == template.fingerprint and t.tenant_id == template.tenant_id
                         and t.workspace_id == template.workspace_id and t.owner_user_id == template.owner_user_id and k != template.id), None)
            if same is not None:
                del self.items[same]
            self.items[template.id] = template.model_copy()
        return template

    def delete(self, context: TenantContext, template_id: str) -> bool:
        with self._lock:
            found = self.items.get(template_id)
            if found is None or not _mine(found, context):
                return False
            del self.items[template_id]
            return True


    def count_everything(self, context: TenantContext) -> int:
        with self._lock:
            return sum(1 for t in self.items.values() if _mine(t, context))

    def delete_everything(self, context: TenantContext) -> int:
        with self._lock:
            mine_ids = [k for k, t in self.items.items() if _mine(t, context)]
            for key in mine_ids:
                del self.items[key]
            return len(mine_ids)


template_memory_store = InMemoryTemplateStore()


@contextmanager
def open_template_store(context: TenantContext) -> Iterator[TemplateStore]:
    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.pdf_template_repository import SqlAlchemyTemplateStore

        return SqlAlchemyTemplateStore(session, context)

    with open_scoped_store(context, template_memory_store, sql_store, user_scoped=True) as store:
        yield store


__all__ = ["MAX_TEMPLATES", "InMemoryTemplateStore", "StatementTemplate", "TemplateStore", "open_template_store", "template_memory_store"]
