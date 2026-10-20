"""Billing storage: one small row API over PostgreSQL (forced RLS) and an in-memory twin.

``open_billing(tenant_id)`` is one unit of work inside one organisation's RLS context
(``anum.tenant_id``). Every read and write sees only that organisation's rows: in
PostgreSQL the row policies enforce it, in memory :class:`MemoryRows` filters the same
way and refuses a row of another tenant. ``as_tenant`` switches the context for a few
statements inside the same transaction (the issuer's number sequence, settings and
webhook events), which is what keeps invoice numbers gapless: the number and the invoice
commit or roll back together.

Filters are ``{column: value}`` for equality or ``{column: (op, value)}`` with ``op`` one
of ``in``, ``<``, ``<=``, ``>``, ``>=``, ``!=``, ``is_null``.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from typing import Any

from ..settings import settings
from .tables import HEAVY_COLUMNS, TABLES

Where = dict[str, Any]
Search = tuple[Sequence[str], str] | None


class RowConflict(Exception):
    """A unique constraint refused the write (a duplicate number, provider id or event)."""


def _matches(row: dict[str, Any], where: Where | None) -> bool:
    for column, condition in (where or {}).items():
        value = row.get(column)
        if isinstance(condition, tuple):
            op, operand = condition
            if op == "in":
                if value not in operand:
                    return False
            elif op == "is_null":
                if (value is None) != bool(operand):
                    return False
            elif value is None:
                return False
            elif op == "<" and not value < operand:
                return False
            elif op == "<=" and not value <= operand:
                return False
            elif op == ">" and not value > operand:
                return False
            elif op == ">=" and not value >= operand:
                return False
            elif op == "!=" and not value != operand:
                return False
        elif value != condition:
            return False
    return True


def _searched(row: dict[str, Any], search: Search) -> bool:
    if not search or not search[1].strip():
        return True
    needle = search[1].strip().casefold()
    return any(needle in str(row.get(column) or "").casefold() for column in search[0])


def _sort_key(value: Any) -> tuple[int, Any]:
    if value is None:
        return (0, "")
    if isinstance(value, datetime):
        return (1, value.timestamp())
    return (1, value)


# Unique keys the in-memory store enforces like the migration's indexes.
_UNIQUE: dict[str, list[tuple[str, ...]]] = {
    "billing_invoices": [("issuer_tenant_id", "number")],
    "billing_credit_notes": [("issuer_tenant_id", "number")],
    "billing_payments": [("issuer_tenant_id", "provider", "provider_payment_id")],
    "billing_webhook_events": [("tenant_id", "provider", "event_id")],
    "billing_plans": [("tenant_id", "code")],
    "billing_signups": [("user_id",)],
    "billing_accounts": [("tenant_id",)],
    "billing_settings": [("tenant_id",)],
    "billing_usage": [("tenant_id", "period", "meter")],
}


class MemoryBillingDB:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {name: [] for name in TABLES}
        self.lock = RLock()

    def clear(self) -> None:
        with self.lock:
            for rows in self.tables.values():
                rows.clear()

    def all_rows(self, table: str) -> list[dict[str, Any]]:
        """Every tenant's rows: only for the reader and maintenance stand-ins below."""
        with self.lock:
            return [copy.deepcopy(row) for row in self.tables[table]]


memory_db = MemoryBillingDB()


class MemoryRows:
    def __init__(self, db: MemoryBillingDB, tenant_id: str) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.session = None

    def _mine(self, row: dict[str, Any]) -> bool:
        return row.get("tenant_id") == self.tenant_id

    def get(self, table: str, *, for_update: bool = False, **key: Any) -> dict[str, Any] | None:
        found = self.select(table, key, limit=1, heavy=True)
        return found[0] if found else None

    def select(
        self, table: str, where: Where | None = None, *, search: Search = None,
        order: Sequence[tuple[str, bool]] = (), limit: int | None = None, offset: int = 0, heavy: bool = False,
    ) -> list[dict[str, Any]]:
        with self.db.lock:
            rows = [copy.deepcopy(r) for r in self.db.tables[table] if self._mine(r) and _matches(r, where) and _searched(r, search)]
        for column, descending in reversed(list(order)):
            rows.sort(key=lambda r: _sort_key(r.get(column)), reverse=descending)
        if not heavy:
            for row in rows:
                for column in HEAVY_COLUMNS.get(table, ()):
                    row.pop(column, None)
        rows = rows[offset:]
        return rows[:limit] if limit is not None else rows

    def count(self, table: str, where: Where | None = None, *, search: Search = None) -> int:
        with self.db.lock:
            return sum(1 for r in self.db.tables[table] if self._mine(r) and _matches(r, where) and _searched(r, search))

    def insert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        if not self._mine(row):
            raise PermissionError("new row violates row-level security policy")
        full = {column.name: None for column in TABLES[table].columns}
        full.update(copy.deepcopy(row))
        with self.db.lock:
            for key in _UNIQUE.get(table, []):
                values = tuple(full.get(column) for column in key)
                if any(value is None for value in values):
                    continue
                if any(tuple(other.get(column) for column in key) == values for other in self.db.tables[table]):
                    raise RowConflict(f"{table} {key}")
            if table == "billing_subscriptions" and full.get("status") != "cancelled" and any(
                other["tenant_id"] == full["tenant_id"] and other["status"] != "cancelled" for other in self.db.tables[table]
            ):
                raise RowConflict("one live subscription per organisation")
            self.db.tables[table].append(full)
        return copy.deepcopy(full)

    def insert_ignore(self, table: str, row: dict[str, Any]) -> bool:
        try:
            self.insert(table, row)
        except RowConflict:
            return False
        return True

    def update(self, table: str, key: Where, values: dict[str, Any]) -> int:
        changed = 0
        with self.db.lock:
            for row in self.db.tables[table]:
                if self._mine(row) and _matches(row, key):
                    row.update(copy.deepcopy(values))
                    changed += 1
        return changed

    def delete(self, table: str, key: Where) -> int:
        with self.db.lock:
            before = len(self.db.tables[table])
            self.db.tables[table][:] = [r for r in self.db.tables[table] if not (self._mine(r) and _matches(r, key))]
            return before - len(self.db.tables[table])

    def upsert_usage(self, period: str, meter: str, quantity: int, now: datetime) -> None:
        if not self.update("billing_usage", {"period": period, "meter": meter}, {"quantity": quantity, "updated_at": now}):
            self.insert("billing_usage", {"tenant_id": self.tenant_id, "period": period, "meter": meter, "quantity": quantity, "updated_at": now})

    def next_number(self, kind: str) -> int:
        """The next number of ``kind`` in the current tenant's sequence (call inside ``as_tenant(issuer)``)."""
        with self.db.lock:
            for row in self.db.tables["billing_number_sequences"]:
                if self._mine(row) and row["kind"] == kind:
                    value = int(row["next_value"])
                    row["next_value"] = value + 1
                    return value
            self.db.tables["billing_number_sequences"].append({"tenant_id": self.tenant_id, "kind": kind, "next_value": 2})
            return 1

    @contextmanager
    def as_tenant(self, tenant_id: str) -> Iterator[MemoryRows]:
        previous, self.tenant_id = self.tenant_id, tenant_id
        try:
            yield self
        finally:
            self.tenant_id = previous


class SqlRows:
    def __init__(self, session: Any, tenant_id: str) -> None:
        self.session = session
        self.tenant_id = tenant_id

    def _where(self, table: Any, where: Where | None, search: Search) -> list[Any]:
        from sqlalchemy import or_

        clauses = []
        for column, condition in (where or {}).items():
            col = table.c[column]
            if isinstance(condition, tuple):
                op, operand = condition
                clauses.append({
                    "in": lambda: col.in_(list(operand)),
                    "<": lambda: col < operand,
                    "<=": lambda: col <= operand,
                    ">": lambda: col > operand,
                    ">=": lambda: col >= operand,
                    "!=": lambda: col != operand,
                    "is_null": lambda: col.is_(None) if operand else col.is_not(None),
                }[op]())
            else:
                clauses.append(col.is_(None) if condition is None else col == condition)
        if search and search[1].strip():
            needle = "%" + search[1].strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            clauses.append(or_(*[table.c[column].ilike(needle, escape="\\") for column in search[0]]))
        # The explicit tenant filter as well as the row policy.
        if "tenant_id" in table.c:
            clauses.append(table.c.tenant_id == self.tenant_id)
        return clauses

    def _columns(self, table: Any, heavy: bool) -> list[Any]:
        skip = () if heavy else HEAVY_COLUMNS.get(table.name, ())
        return [column for column in table.columns if column.name not in skip]

    def get(self, table: str, *, for_update: bool = False, **key: Any) -> dict[str, Any] | None:
        from sqlalchemy import select

        t = TABLES[table]
        statement = select(*self._columns(t, True)).where(*self._where(t, key, None)).limit(1)
        if for_update:
            statement = statement.with_for_update()
        row = self.session.execute(statement).first()
        return dict(row._mapping) if row else None

    def select(
        self, table: str, where: Where | None = None, *, search: Search = None,
        order: Sequence[tuple[str, bool]] = (), limit: int | None = None, offset: int = 0, heavy: bool = False,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import select

        t = TABLES[table]
        statement = select(*self._columns(t, heavy)).where(*self._where(t, where, search))
        for column, descending in order:
            statement = statement.order_by(t.c[column].desc().nulls_last() if descending else t.c[column].asc().nulls_first())
        if offset:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        return [dict(row._mapping) for row in self.session.execute(statement).all()]

    def count(self, table: str, where: Where | None = None, *, search: Search = None) -> int:
        from sqlalchemy import func, select

        t = TABLES[table]
        return int(self.session.scalar(select(func.count()).select_from(t).where(*self._where(t, where, search))) or 0)

    def insert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        from sqlalchemy import insert
        from sqlalchemy.exc import IntegrityError

        try:
            with self.session.begin_nested():
                self.session.execute(insert(TABLES[table]).values(**row))
        except IntegrityError as exc:
            raise RowConflict(table) from exc
        return dict(row)

    def insert_ignore(self, table: str, row: dict[str, Any]) -> bool:
        from sqlalchemy.dialects.postgresql import insert

        t = TABLES[table]
        key = next(iter(t.primary_key.columns))
        result = self.session.execute(insert(t).values(**row).on_conflict_do_nothing().returning(key))
        return result.first() is not None

    def update(self, table: str, key: Where, values: dict[str, Any]) -> int:
        from sqlalchemy import update

        t = TABLES[table]
        return int(self.session.execute(update(t).where(*self._where(t, key, None)).values(**values)).rowcount or 0)

    def delete(self, table: str, key: Where) -> int:
        from sqlalchemy import delete

        t = TABLES[table]
        return int(self.session.execute(delete(t).where(*self._where(t, key, None))).rowcount or 0)

    def upsert_usage(self, period: str, meter: str, quantity: int, now: datetime) -> None:
        from sqlalchemy.dialects.postgresql import insert

        t = TABLES["billing_usage"]
        statement = insert(t).values(tenant_id=self.tenant_id, period=period, meter=meter, quantity=quantity, updated_at=now)
        self.session.execute(statement.on_conflict_do_update(
            index_elements=[t.c.tenant_id, t.c.period, t.c.meter], set_={"quantity": quantity, "updated_at": now}
        ))

    def next_number(self, kind: str) -> int:
        """Take the next number; the upsert holds the sequence row's lock until this transaction ends."""
        from sqlalchemy import text

        return int(self.session.execute(
            text(
                "insert into billing_number_sequences (tenant_id, kind, next_value) values (:tenant, :kind, 2) "
                "on conflict (tenant_id, kind) do update set next_value = billing_number_sequences.next_value + 1 "
                "returning next_value - 1"
            ),
            {"tenant": self.tenant_id, "kind": kind},
        ).scalar_one())

    @contextmanager
    def as_tenant(self, tenant_id: str) -> Iterator[SqlRows]:
        from sqlalchemy import text

        previous = self.tenant_id
        self.session.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": tenant_id})
        self.tenant_id = tenant_id
        try:
            yield self
        finally:
            self.session.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": previous})
            self.tenant_id = previous


Rows = MemoryRows | SqlRows


@contextmanager
def open_billing(tenant_id: str, *, session: Any = None) -> Iterator[Rows]:
    """One unit of work in ``tenant_id``'s RLS context; commits when the block ends normally.

    With ``session`` (a caller's open transaction, already in that tenant's context) the
    rows join it and the caller commits.
    """
    if settings.repository_backend == "memory":
        yield MemoryRows(memory_db, tenant_id)
        return
    if session is not None:
        yield SqlRows(session, tenant_id)
        return
    from ..db import session as db_session

    own = db_session.SessionLocal()
    try:
        db_session.set_tenant_context(own, tenant_id)
        yield SqlRows(own, tenant_id)
        own.commit()
    except BaseException:
        own.rollback()
        raise
    finally:
        own.close()


def reader_rows(role: str, settings_values: dict[str, str], statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One read as a narrow NOLOGIN role (``anum_platform_reader`` or ``anum_maintenance``).

    Its own transaction: ``SET LOCAL ROLE``, the role's ``anum.*`` settings, one statement,
    rollback. The role was granted to the API login ``WITH INHERIT FALSE``, so its
    policies never widen the application role's own queries.
    """
    from sqlalchemy import text

    from ..db import session as db_session

    if role not in ("anum_platform_reader", "anum_maintenance"):
        raise ValueError("unknown reader role")
    session = db_session.SessionLocal()
    try:
        session.execute(text(f"set local role {role}"))  # nosec B608 - role checked against a fixed list
        for name, value in settings_values.items():
            session.execute(text("select set_config(:name, :value, true)"), {"name": name, "value": value})
        return [dict(row._mapping) for row in session.execute(text(statement), params or {}).all()]
    finally:
        session.rollback()
        session.close()
