from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from anum_api.db.repository import SqlAlchemyRepository
from anum_api.schemas import TenantContext
from anum_api.settings import settings


API_ROOT = Path(__file__).parents[1]
APP_ROLE = "anum_test_app"
FIXED_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)

TENANT_A = "tenant_test_a"
TENANT_B = "tenant_test_b"
WORKSPACE_A = "workspace_test_a"
WORKSPACE_A2 = "workspace_test_a2"
WORKSPACE_B = "workspace_test_b"

TABLES_IN_DELETE_ORDER = (
    "memories",
    "domain_events",
    "approvals",
    "agent_run_steps",
    "agent_runs",
    "tasks",
    "workspace_memberships",
    "workspaces",
    "tenants",
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.getenv("ANUM_TEST_DATABASE_URL"):
        return

    skip = pytest.mark.skip(reason="ANUM_TEST_DATABASE_URL is not configured")
    for item in items:
        if "database" in item.keywords:
            item.add_marker(skip)


def tenant_context(
    tenant_id: str = TENANT_A,
    workspace_id: str = WORKSPACE_A,
) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id="user_test",
        roles=["owner"],
    )


def _alembic_config(database_url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def run_migration(database_url: str, direction: str, revision: str) -> None:
    original_url = settings.database_url
    settings.database_url = database_url
    try:
        migration_command = command.upgrade if direction == "upgrade" else command.downgrade
        migration_command(_alembic_config(database_url), revision)
    finally:
        settings.database_url = original_url


def grant_app_role(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                do $$
                begin
                    if not exists (select 1 from pg_roles where rolname = '{APP_ROLE}') then
                        create role {APP_ROLE} nologin;
                    end if;
                end
                $$
                """
            )
        )
        connection.execute(text(f"grant {APP_ROLE} to current_user"))
        connection.execute(text(f"grant usage on schema public to {APP_ROLE}"))
        connection.execute(
            text(f"grant select, insert, update, delete on all tables in schema public to {APP_ROLE}")
        )


@pytest.fixture(scope="session")
def test_database_url() -> str:
    value = os.getenv("ANUM_TEST_DATABASE_URL")
    if not value:
        pytest.skip("ANUM_TEST_DATABASE_URL is not configured")
    return value


@pytest.fixture(scope="session")
def database_engine(test_database_url: str) -> Iterator[Engine]:
    run_migration(test_database_url, "downgrade", "base")
    run_migration(test_database_url, "upgrade", "head")
    engine = create_engine(test_database_url, poolclass=NullPool, pool_pre_ping=True)
    grant_app_role(engine)
    yield engine
    engine.dispose()


def _clean_database(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(f"truncate table {', '.join(TABLES_IN_DELETE_ORDER)} cascade"))


@pytest.fixture
def clean_database(database_engine: Engine) -> Iterator[None]:
    _clean_database(database_engine)
    yield
    _clean_database(database_engine)


@pytest.fixture
def seed_scopes(database_engine: Engine, clean_database: None) -> None:
    with database_engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into tenants (id, name) values
                    (:tenant_a, 'Tenant A'),
                    (:tenant_b, 'Tenant B')
                """
            ),
            {"tenant_a": TENANT_A, "tenant_b": TENANT_B},
        )

    workspaces = (
        (TENANT_A, WORKSPACE_A, "Workspace A"),
        (TENANT_A, WORKSPACE_A2, "Workspace A2"),
        (TENANT_B, WORKSPACE_B, "Workspace B"),
    )
    for tenant_id, workspace_id, name in workspaces:
        with database_engine.begin() as connection:
            connection.execute(text(f"set local role {APP_ROLE}"))
            connection.execute(
                text("select set_config('anum.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            connection.execute(
                text("select set_config('anum.workspace_id', :workspace_id, true)"),
                {"workspace_id": workspace_id},
            )
            connection.execute(
                text(
                    """
                    insert into workspaces (id, tenant_id, name)
                    values (:workspace_id, :tenant_id, :name)
                    """
                ),
                {"tenant_id": tenant_id, "workspace_id": workspace_id, "name": name},
            )


SessionFactory = Callable[[TenantContext, bool], Iterator[Session]]


@pytest.fixture
def app_session(database_engine: Engine) -> Callable[..., Iterator[Session]]:
    @contextmanager
    def factory(
        context: TenantContext | None = None,
        *,
        commit: bool = False,
    ) -> Iterator[Session]:
        connection = database_engine.connect()
        transaction = connection.begin()
        session = Session(bind=connection, expire_on_commit=False)
        try:
            connection.execute(text(f"set local role {APP_ROLE}"))
            if context is not None:
                connection.execute(
                    text("select set_config('anum.tenant_id', :tenant_id, true)"),
                    {"tenant_id": context.tenant_id},
                )
                connection.execute(
                    text("select set_config('anum.workspace_id', :workspace_id, true)"),
                    {"workspace_id": context.workspace_id},
                )
            yield session
            session.flush()
            if commit:
                transaction.commit()
            else:
                transaction.rollback()
        except Exception:
            if transaction.is_active:
                transaction.rollback()
            raise
        finally:
            session.close()
            connection.close()

    return factory


@pytest.fixture
def repository_factory(
    app_session: Callable[..., Iterator[Session]],
) -> Callable[..., Iterator[SqlAlchemyRepository]]:
    @contextmanager
    def factory(
        context: TenantContext | None = None,
        *,
        commit: bool = False,
    ) -> Iterator[SqlAlchemyRepository]:
        with app_session(context, commit=commit) as session:
            yield SqlAlchemyRepository(session, created_by_user_id="user_test")

    return factory
