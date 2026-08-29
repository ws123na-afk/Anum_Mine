from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from conftest import (
    APP_ROLE,
    TENANT_A,
    TENANT_B,
    WORKSPACE_A,
    WORKSPACE_A2,
    WORKSPACE_B,
    grant_app_role,
    run_migration,
    tenant_context,
)


pytestmark = pytest.mark.database

TENANT_TABLES = {
    "workspace_memberships",
    "workspaces",
    "tasks",
    "agent_runs",
    "agent_run_steps",
    "approvals",
    "domain_events",
    "memories",
}


def test_migration_upgrade_and_downgrade_are_reversible(
    database_engine: Engine,
    test_database_url: str,
) -> None:
    inspector = inspect(database_engine)
    assert TENANT_TABLES | {"tenants"} <= set(inspector.get_table_names())

    try:
        run_migration(test_database_url, "downgrade", "base")
        assert (TENANT_TABLES | {"tenants"}).isdisjoint(
            inspect(database_engine).get_table_names()
        )
    finally:
        run_migration(test_database_url, "upgrade", "head")
        grant_app_role(database_engine)

    assert TENANT_TABLES | {"tenants"} <= set(inspect(database_engine).get_table_names())


def test_migration_enables_rls_and_creates_a_policy_for_every_tenant_table(
    database_engine: Engine,
) -> None:
    with database_engine.connect() as connection:
        rls_state = {
            row.relname: (row.relrowsecurity, row.relforcerowsecurity)
            for row in connection.execute(
                text(
                    """
                    select relname, relrowsecurity, relforcerowsecurity
                    from pg_class
                    where relnamespace = 'public'::regnamespace
                      and relname = any(:table_names)
                    """
                ),
                {"table_names": sorted(TENANT_TABLES)},
            )
        }
        policy_tables = set(
            connection.execute(
                text("select tablename from pg_policies where schemaname = 'public'")
            ).scalars()
        )

    assert set(rls_state) == TENANT_TABLES
    assert all(enabled and forced for enabled, forced in rls_state.values())
    assert TENANT_TABLES <= policy_tables


def test_rls_is_exercised_by_a_non_owner_application_role(
    database_engine: Engine,
    seed_scopes: None,
    app_session: Callable[..., Iterator[Session]],
) -> None:
    with app_session(tenant_context()) as session:
        identity = session.execute(
            text(
                """
                select current_user,
                       pg_get_userbyid(c.relowner) = current_user as owns_tasks
                from pg_class c
                where c.oid = 'public.tasks'::regclass
                """
            )
        ).one()

    assert identity.current_user == APP_ROLE
    assert identity.owns_tasks is False


def test_rls_hides_other_tenants_and_rejects_cross_tenant_writes(
    seed_scopes: None,
    app_session: Callable[..., Iterator[Session]],
) -> None:
    for context, task_id in (
        (tenant_context(TENANT_A, WORKSPACE_A), "task_rls_a"),
        (tenant_context(TENANT_B, WORKSPACE_B), "task_rls_b"),
    ):
        with app_session(context, commit=True) as session:
            session.execute(
                text(
                    """
                    insert into tasks
                        (id, tenant_id, workspace_id, created_by_user_id, title, prompt, status)
                    values
                        (:task_id, :tenant_id, :workspace_id, 'user_test', 'Task', 'Task', 'created')
                    """
                ),
                {
                    "task_id": task_id,
                    "tenant_id": context.tenant_id,
                    "workspace_id": context.workspace_id,
                },
            )

    with app_session(tenant_context()) as session:
        assert session.execute(text("select id from tasks order by id")).scalars().all() == [
            "task_rls_a"
        ]

    with pytest.raises(DBAPIError):
        with app_session(tenant_context()) as session:
            session.execute(
                text(
                    """
                    insert into tasks
                        (id, tenant_id, workspace_id, created_by_user_id, title, prompt, status)
                    values
                        ('task_bad_tenant', :tenant_b, :workspace_b, 'user_a', 'Bad', 'Bad', 'created')
                    """
                ),
                {"tenant_b": TENANT_B, "workspace_b": WORKSPACE_B},
            )


def test_rls_denies_access_when_tenant_context_is_missing(
    seed_scopes: None,
    app_session: Callable[..., Iterator[Session]],
) -> None:
    with app_session(tenant_context(), commit=True) as session:
        session.execute(
            text(
                """
                insert into tasks
                    (id, tenant_id, workspace_id, created_by_user_id, title, prompt, status)
                values ('task_no_context', :tenant_id, :workspace_id, 'user_a', 'A', 'A', 'created')
                """
            ),
            {
                "tenant_id": TENANT_A,
                "workspace_id": WORKSPACE_A,
            },
        )

    with app_session() as session:
        assert session.execute(text("select id from tasks")).scalars().all() == []


def test_rls_enforces_workspace_boundary_within_a_tenant(
    seed_scopes: None,
    app_session: Callable[..., Iterator[Session]],
) -> None:
    for workspace_id in (WORKSPACE_A, WORKSPACE_A2):
        with app_session(tenant_context(TENANT_A, workspace_id), commit=True) as session:
            session.execute(
                text(
                    """
                    insert into tasks
                        (id, tenant_id, workspace_id, created_by_user_id, title, prompt, status)
                    values
                        (:task_id, :tenant_id, :workspace_id, 'user_a', 'Task', 'Task', 'created')
                    """
                ),
                {
                    "task_id": f"task_{workspace_id}",
                    "tenant_id": TENANT_A,
                    "workspace_id": workspace_id,
                },
            )

    with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
        visible = session.execute(text("select id from tasks order by id")).scalars().all()

    assert visible == [f"task_{WORKSPACE_A}"]


def test_child_foreign_keys_reject_cross_scope_relationships(
    database_engine: Engine,
    seed_scopes: None,
    app_session: Callable[..., Iterator[Session]],
) -> None:
    columns = {column["name"] for column in inspect(database_engine).get_columns("agent_runs")}
    assert "workspace_id" in columns

    with app_session(tenant_context(), commit=True) as session:
        session.execute(
            text(
                """
                insert into tasks
                    (id, tenant_id, workspace_id, created_by_user_id, title, prompt, status)
                values
                    ('task_fk_a', :tenant_a, :workspace_a, 'user_a', 'A', 'A', 'created')
                """
            ),
            {"tenant_a": TENANT_A, "workspace_a": WORKSPACE_A},
        )

    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_B, WORKSPACE_B)) as session:
            session.execute(
                text(
                    """
                    insert into agent_runs
                        (id, tenant_id, workspace_id, task_id, status)
                    values
                        ('run_bad_scope', :tenant_b, :workspace_b, 'task_fk_a', 'running')
                    """
                ),
                {"tenant_b": TENANT_B, "workspace_b": WORKSPACE_B},
            )
