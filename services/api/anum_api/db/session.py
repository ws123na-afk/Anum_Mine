from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from anum_api.settings import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
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
