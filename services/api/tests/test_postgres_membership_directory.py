"""PostgreSQL: the caller's memberships across workspaces through ``anum_membership_reader``.

The API runs as the non-owner app role and reads the directory in its own transaction
as ``anum_membership_reader`` (migration 0013). These tests check that the role sees
only the caller's active rows in the caller's tenant, only the granted columns, cannot
write, and that its policies never widen what the app role itself sees.
See docs/identity.md#my-workspaces.
"""

from __future__ import annotations


import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from anum_api.main import app

from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture)

pytestmark = pytest.mark.database

ROUTE = "/api/v1/me/workspace-memberships"
USER = "user_directory"


def _scoped(connection: Connection, tenant: str, workspace: str | None = None, user: str | None = None) -> None:
    connection.execute(text("select set_config('anum.tenant_id', :value, true)"), {"value": tenant})
    if workspace:
        connection.execute(text("select set_config('anum.workspace_id', :value, true)"), {"value": workspace})
    if user:
        connection.execute(text("select set_config('anum.user_id', :value, true)"), {"value": user})


def _add_member(engine: Engine, tenant: str, workspace: str, user: str, role: str, active: bool = True) -> None:
    with engine.begin() as connection:
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, tenant, workspace)
        connection.execute(
            text(
                "insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) "
                "values (:user, :tenant, :workspace, :role, :active)"
            ),
            {"user": user, "tenant": tenant, "workspace": workspace, "role": role, "active": active},
        )


@pytest.fixture
def memberships(database_engine: Engine, seed_scopes: None) -> None:
    _add_member(database_engine, TENANT_A, WORKSPACE_A, USER, "owner")
    _add_member(database_engine, TENANT_A, WORKSPACE_A2, USER, "viewer")
    _add_member(database_engine, TENANT_A, WORKSPACE_A2, "someone_else", "owner")
    _add_member(database_engine, TENANT_B, WORKSPACE_B, USER, "owner")


@pytest.fixture
def client(postgres_backend: None, memberships: None) -> TestClient:  # noqa: F811
    return TestClient(app)


def _headers(user: str = USER, tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": "member"}


def test_route_lists_the_callers_active_memberships_in_their_tenant(client: TestClient) -> None:
    response = client.get(ROUTE, headers=_headers())
    assert response.status_code == 200, response.text
    assert [(item["workspace_id"], item["workspace_name"], item["role"]) for item in response.json()] == [
        (WORKSPACE_A, "Workspace A", "owner"),
        (WORKSPACE_A2, "Workspace A2", "viewer"),
    ]
    other_tenant = client.get(ROUTE, headers=_headers(tenant=TENANT_B, workspace=WORKSPACE_B)).json()
    assert [item["workspace_id"] for item in other_tenant] == [WORKSPACE_B]
    assert [item["workspace_id"] for item in client.get(ROUTE, headers=_headers("someone_else")).json()] == [
        WORKSPACE_A2
    ]
    assert client.get(ROUTE, headers=_headers("nobody")).json() == []


def test_deactivated_memberships_are_not_listed(client: TestClient, database_engine: Engine) -> None:
    with database_engine.begin() as connection:
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, TENANT_A, WORKSPACE_A2)
        connection.execute(
            text("update workspace_memberships set active = false where user_id = :user"), {"user": USER}
        )
    assert [item["workspace_id"] for item in client.get(ROUTE, headers=_headers()).json()] == [WORKSPACE_A]


def _as_reader(engine: Engine, tenant: str | None, user: str | None, statement: str) -> list:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("set local role anum_membership_reader"))
            if tenant is not None:
                _scoped(connection, tenant, None, user)
            return list(connection.execute(text(statement)).all())
        finally:
            transaction.rollback()


def test_reader_sees_only_the_users_rows_and_workspaces_in_the_tenant(
    database_engine: Engine, memberships: None
) -> None:
    rows = _as_reader(database_engine, TENANT_A, USER, "select workspace_id, user_id from workspace_memberships")
    assert sorted(rows) == [(WORKSPACE_A, USER), (WORKSPACE_A2, USER)]
    names = _as_reader(database_engine, TENANT_A, USER, "select id, name from workspaces order by id")
    assert [row.id for row in names] == [WORKSPACE_A, WORKSPACE_A2]
    # Without the user or the tenant setting the role sees nothing (fail closed).
    assert _as_reader(database_engine, TENANT_A, None, "select workspace_id from workspace_memberships") == []
    assert _as_reader(database_engine, None, None, "select workspace_id from workspace_memberships") == []
    assert _as_reader(database_engine, None, None, "select id from workspaces") == []


@pytest.mark.parametrize(
    "statement",
    [
        "select created_at from workspace_memberships",
        "select created_at from workspaces",
        "select id from tasks",
        "update workspace_memberships set role = 'owner'",
        "delete from workspace_memberships",
        "insert into workspace_memberships (user_id, tenant_id, workspace_id, role) "
        "values ('x', 'tenant_test_a', 'workspace_test_a', 'owner')",
    ],
)
def test_reader_has_no_other_columns_tables_or_writes(
    database_engine: Engine, memberships: None, statement: str
) -> None:
    with pytest.raises(DBAPIError) as error:
        _as_reader(database_engine, TENANT_A, USER, statement)
    assert "permission denied" in str(error.value)


def test_reader_policies_never_widen_the_app_role(database_engine: Engine, memberships: None) -> None:
    """Granted WITH INHERIT FALSE (as bootstrap-database.sql does), the reader's policies
    do not apply to the app role's own queries, which stay one workspace wide."""
    with database_engine.begin() as connection:
        connection.execute(text(f"grant anum_membership_reader to {APP_ROLE} with inherit false"))
    try:
        with database_engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text(f"set local role {APP_ROLE}"))
                _scoped(connection, TENANT_A, WORKSPACE_A, USER)
                rows = connection.execute(text("select workspace_id from workspace_memberships")).scalars().all()
            finally:
                transaction.rollback()
        assert set(rows) == {WORKSPACE_A}
    finally:
        with database_engine.begin() as connection:
            connection.execute(text(f"revoke anum_membership_reader from {APP_ROLE}"))


def test_reader_role_exists_without_login_or_bypass(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        role = connection.execute(
            text(
                "select rolcanlogin, rolbypassrls, rolsuper from pg_roles "
                "where rolname = 'anum_membership_reader'"
            )
        ).one()
    assert (role.rolcanlogin, role.rolbypassrls, role.rolsuper) == (False, False, False)
