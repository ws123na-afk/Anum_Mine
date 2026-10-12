from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from anum_api.settings import settings


def _connect_args() -> dict[str, object]:
    # A bounded wait for row locks: a statement stuck behind a lock fails instead of
    # holding its caller (and, on the event loop, every request) indefinitely.
    if settings.database_lock_timeout_ms and settings.database_url.startswith("postgresql"):
        return {"options": f"-c lock_timeout={int(settings.database_lock_timeout_ms)}"}
    return {}


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_timeout=settings.database_pool_timeout_seconds,
    connect_args=_connect_args(),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def set_tenant_context(
    session: Session,
    tenant_id: str,
    workspace_id: str | None = None,
    *,
    user_id: str | None = None,
) -> None:
    """Set the transaction-local RLS context.

    ``user_id`` is only needed by per-user tables (voice sessions and transcripts, whose
    policies also check ``anum.user_id``); every other table ignores it.
    """
    session.execute(text("select set_config('anum.tenant_id', :tenant_id, true)"), {"tenant_id": tenant_id})
    if workspace_id:
        session.execute(
            text("select set_config('anum.workspace_id', :workspace_id, true)"),
            {"workspace_id": workspace_id},
        )
    if user_id:
        session.execute(
            text("select set_config('anum.user_id', :user_id, true)"),
            {"user_id": user_id},
        )
