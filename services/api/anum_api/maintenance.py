"""Cross-tenant maintenance without bypassing RLS.

Four jobs have to find work in every tenant: the automation scheduler (due schedules),
the voice retention purge (expired transcripts), the retrieval index purge (sources of
expired memories, migration 0015) and ``python -m anum_api.rotate_secrets`` (stored
provider keys). They follow one pattern (migration 0011):

1. **Discover** in a short read-only transaction that starts with
   ``SET LOCAL ROLE anum_maintenance``. That role can read only a few id and timestamp
   columns, and only of rows that need work (dedicated RLS policies). It cannot read
   content and cannot write.
2. **Act** per scope in a separate transaction as the application role, with the
   discovered tenant, workspace (and user) set as the RLS context. Every content read
   and every write is checked by the same tenant-isolation policies as an API request.

The API login (or the operator login running the command) must be granted
``anum_maintenance``; the application role itself never gains ``BYPASSRLS``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

MAINTENANCE_ROLE = "anum_maintenance"
_SET_MAINTENANCE_ROLE = text("set local role anum_maintenance")

SessionFactory = Callable[[], Session]


def default_session_factory() -> Session:
    # Resolved at call time so tests (and later configuration) can swap SessionLocal.
    from .db import session as db_session

    return db_session.SessionLocal()


def discover(session_factory: SessionFactory, statement: Any, params: dict[str, Any] | None = None) -> list[Any]:
    """Run one discovery query as ``anum_maintenance`` and return its rows.

    The transaction is always rolled back and the session closed before returning, so no
    discovery transaction (or its role) outlives the call.
    """
    session = session_factory()
    try:
        session.execute(_SET_MAINTENANCE_ROLE)
        rows = list(session.execute(statement, params or {}).all())
        session.rollback()
        return rows
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def scoped_unit(
    session_factory: SessionFactory,
    tenant_id: str,
    workspace_id: str,
    *,
    user_id: str | None = None,
) -> Iterator[Session]:
    """One application-role transaction inside a tenant's RLS context.

    Commits when the block ends normally, rolls back otherwise, and always closes.
    """
    from .db.session import set_tenant_context

    session = session_factory()
    try:
        set_tenant_context(session, tenant_id, workspace_id, user_id=user_id)
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
