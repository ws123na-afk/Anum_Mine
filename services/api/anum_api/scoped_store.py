"""Open a control-plane store for one tenant/workspace unit of work.

The skills, governance, marketplace, routing, integration, file and notification
stores each have an in-memory implementation (``ANUM_REPOSITORY_BACKEND=memory``, the
local and test default) and a PostgreSQL implementation. With PostgreSQL the store gets
a session whose tenant and workspace RLS context is already set, the unit of work
commits when the ``with`` block ends normally and rolls back otherwise. Per-user stores
(voice) pass ``user_scoped=True`` so ``anum.user_id`` is set as well.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, TypeVar

from fastapi import HTTPException, status

from .schemas import TenantContext
from .settings import settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

StoreT = TypeVar("StoreT")


class ScopeNotProvisionedError(LookupError):
    """The tenant or workspace has not been onboarded, so its rows cannot be written."""


@contextmanager
def open_scoped_store(
    context: TenantContext,
    memory_store: StoreT,
    sql_store: Callable[["Session"], StoreT],
    *,
    user_scoped: bool = False,
) -> Iterator[StoreT]:
    if settings.repository_backend == "memory":
        yield memory_store
        return
    if settings.repository_backend != "postgresql":
        raise RuntimeError(f"Unsupported repository backend: {settings.repository_backend}")

    from .db import session as db_session

    session = db_session.SessionLocal()
    try:
        db_session.set_tenant_context(
            session,
            context.tenant_id,
            context.workspace_id,
            user_id=context.user_id if user_scoped else None,
        )
        yield sql_store(session)
        session.commit()
    except ScopeNotProvisionedError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Complete onboarding for this workspace first",
        ) from exc
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
