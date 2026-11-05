"""PostgreSQL store for the business books (migrations ``0137_business_books`` and ``0138_books_v2``, docs/business-books.md#storage).

The session carries the tenant and workspace RLS context (``open_scoped_store``); every query
also filters on the scope explicitly. The tables are described from the record models, so a
column the model gains must be added by a migration first (``tests/test_postgres_business_books.py``
reads and writes every kind). Intercompany entries are written in the other business by setting
``anum.workspace_id`` for that statement only (the caller's membership there was checked first,
``books_service.manageable_workspace``), the pattern of ``finance_links_repository``.
"""

from __future__ import annotations

import types
import typing
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anum_api.business.books_models import KINDS, TABLES, BookSettings
from anum_api.business.books_store import AtLeast, DuplicateError, plain
from anum_api.schemas import TenantContext

from .scope import require_workspace

_metadata = MetaData()


def _sql_type(annotation: Any) -> Any:
    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
        inner = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        return _sql_type(inner[0])
    if typing.get_origin(annotation) in (dict, list) or annotation in (dict, list):
        return JSONB()
    if isinstance(annotation, type):
        if issubclass(annotation, bool):
            return Boolean()
        if issubclass(annotation, int):
            return Integer()
        if issubclass(annotation, Decimal):
            return Numeric(24, 10)
        if issubclass(annotation, datetime):
            return DateTime(timezone=True)
        if issubclass(annotation, date):
            return Date()
        if issubclass(annotation, (str, Enum)):
            return String()
    raise TypeError(f"no column type for {annotation!r}")


def _table(name: str, model: type[BaseModel]) -> Table:
    columns = [Column(field, _sql_type(info.annotation), primary_key=field == "id") for field, info in model.model_fields.items()]
    return Table(name, _metadata, *columns)


_TABLES: dict[str, Table] = {kind: _table(TABLES[kind], model) for kind, model in KINDS.items()}
_SETTINGS = _table("business_book_settings", BookSettings)


def _values(item: BaseModel) -> dict[str, Any]:
    return {name: plain(getattr(item, name)) for name in type(item).model_fields}


def _load(model: type[BaseModel], row: Any) -> Any:
    return model.model_validate(dict(row._mapping))


class SqlBooksStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _scope(self, table: Table, workspace_id: str | None = None) -> list[Any]:
        return [table.c.tenant_id == self.context.tenant_id, table.c.workspace_id == (workspace_id or self.context.workspace_id)]

    @contextmanager
    def _write(self) -> Iterator[None]:
        """A unique violation becomes :class:`DuplicateError`; the savepoint keeps the unit of work usable."""
        try:
            with self.session.begin_nested():
                yield
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == "23505" or "unique" in str(exc.orig).lower():
                raise DuplicateError(str(exc.orig).splitlines()[0]) from None
            raise

    @contextmanager
    def _in_workspace(self, workspace_id: str) -> Iterator[None]:
        self.session.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": workspace_id})
        try:
            yield
        finally:
            self.session.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": self.context.workspace_id})

    def get(self, kind: str, item_id: str, *, for_update: bool = False) -> Any:
        table = _TABLES[kind]
        statement = select(table).where(*self._scope(table), table.c.id == item_id)
        if for_update:
            statement = statement.with_for_update()
        row = self.session.execute(statement).first()
        return _load(KINDS[kind], row) if row is not None else None

    def add(self, kind: str, item: Any) -> Any:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        with self._write():
            self.session.execute(_TABLES[kind].insert().values(**_values(item)))
        return item

    def save(self, kind: str, item: Any, *, expected_version: int | None = None) -> bool:
        table = _TABLES[kind]
        where = [*self._scope(table), table.c.id == item.id]
        if expected_version is not None:
            where.append(table.c.version == expected_version)
        values = {k: v for k, v in _values(item).items() if k not in ("id", "tenant_id", "workspace_id", "created_at")}
        with self._write():
            result = self.session.execute(table.update().where(*where).values(**values))
        return bool(result.rowcount)

    def delete(self, kind: str, item_id: str) -> bool:
        table = _TABLES[kind]
        result = self.session.execute(table.delete().where(*self._scope(table), table.c.id == item_id))
        return bool(result.rowcount)

    def _where(self, table: Table, where: dict[str, Any]) -> list[Any]:
        clauses = []
        for name, wanted in where.items():
            column = table.c[name]
            if isinstance(wanted, AtLeast):
                clauses.append(column >= wanted.value)
            elif isinstance(wanted, (set, frozenset, list, tuple)):
                clauses.append(column.in_([plain(value) for value in wanted]))
            elif wanted is None:
                clauses.append(column.is_(None))
            else:
                clauses.append(column == plain(wanted))
        return clauses

    def first(self, kind: str, **where: Any) -> Any:
        table = _TABLES[kind]
        row = self.session.execute(select(table).where(*self._scope(table), *self._where(table, where)).limit(1)).first()
        return _load(KINDS[kind], row) if row is not None else None

    def find(self, kind: str, *, where: dict[str, Any] | None = None, text: str | None = None, text_fields: tuple[str, ...] = (),
             order_by: str = "created_at", descending: bool = True, limit: int = 50, offset: int = 0) -> tuple[list[Any], int]:
        table = _TABLES[kind]
        clauses = [*self._scope(table), *self._where(table, where or {})]
        if text and text_fields:
            pattern = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            clauses.append(or_(*(table.c[name].ilike(pattern, escape="\\") for name in text_fields)))
        total = int(self.session.scalar(select(func.count()).select_from(table).where(*clauses)) or 0)
        order = table.c[order_by].desc() if descending else table.c[order_by].asc()
        tie = table.c.created_at.desc() if descending else table.c.created_at.asc()
        rows = self.session.execute(select(table).where(*clauses).order_by(order, tie, table.c.id).offset(offset).limit(limit)).all()
        return [_load(KINDS[kind], row) for row in rows], total

    def get_settings(self) -> BookSettings | None:
        row = self.session.execute(select(_SETTINGS).where(*self._scope(_SETTINGS))).first()
        return _load(BookSettings, row) if row is not None else None

    def save_settings(self, value: BookSettings, *, expected_version: int | None) -> bool:
        if expected_version is None:
            require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
            try:
                with self._write():
                    self.session.execute(_SETTINGS.insert().values(**_values(value)))
            except DuplicateError:
                return False  # another tab saved the first settings at the same time
            return True
        values = {k: v for k, v in _values(value).items() if k not in ("id", "tenant_id", "workspace_id", "created_at")}
        result = self.session.execute(_SETTINGS.update().where(*self._scope(_SETTINGS), _SETTINGS.c.version == expected_version).values(**values))
        return bool(result.rowcount)

    def add_elsewhere(self, kind: str, item: Any) -> Any:
        if item.tenant_id != self.context.tenant_id:
            raise ValueError("never another tenant")
        with self._in_workspace(item.workspace_id), self._write():
            self.session.execute(_TABLES[kind].insert().values(**_values(item)))
        return item

    def get_elsewhere(self, kind: str, workspace_id: str, **where: Any) -> Any:
        table = _TABLES[kind]
        with self._in_workspace(workspace_id):
            row = self.session.execute(select(table).where(*self._scope(table, workspace_id), *self._where(table, where))
                                       .with_for_update().limit(1)).first()
        return _load(KINDS[kind], row) if row is not None else None

    def save_elsewhere(self, kind: str, item: Any) -> None:
        table = _TABLES[kind]
        values = {k: v for k, v in _values(item).items() if k not in ("id", "tenant_id", "workspace_id", "created_at")}
        with self._in_workspace(item.workspace_id):
            self.session.execute(table.update().where(*self._scope(table, item.workspace_id), table.c.id == item.id).values(**values))


__all__ = ["SqlBooksStore"]
