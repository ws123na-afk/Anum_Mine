"""Short, RLS-scoped read sessions for the office read models.

One session per workspace: the tenant, workspace and (optionally) user RLS settings are set
for that single workspace and the transaction is rolled back (or committed for audit) when
the block ends. Nothing here joins workspaces or tenants; callers loop over workspaces and
open one session each, the way the maintenance jobs act per tenant.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from .schemas import TenantContext
from .settings import settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def require_postgres() -> None:
    if settings.repository_backend != "postgresql":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Office persistence needs the PostgreSQL backend",
        )


@contextmanager
def workspace_session(context: TenantContext, *, commit: bool = False) -> Iterator["Session"]:
    """A session scoped to exactly ``context``'s tenant, workspace and user."""
    require_postgres()
    from .db import session as db_session

    session = db_session.SessionLocal()
    try:
        db_session.set_tenant_context(session, context.tenant_id, context.workspace_id, user_id=context.user_id)
        yield session
        if commit:
            session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.rollback()
        session.close()
