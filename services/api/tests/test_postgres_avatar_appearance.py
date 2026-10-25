"""Avatar styles in PostgreSQL under the non-owner app role (migration 0107_avatar_appearance).

The style lives in a new nullable JSONB column of two tables that already have forced row level security: a
private agent's style is invisible to everyone else, nothing crosses workspaces or tenants, a style cannot be
written into another workspace's profile, and the database refuses a stored value that is not a JSON object.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from anum_api.agent_profiles import agent_store
from test_postgres_agents import app_factory, client as agent_client, headers  # noqa: F401 (fixtures)
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture)
from test_postgres_team_work import OWNER as TEAM_OWNER, client as team_client  # noqa: F401 (fixtures)

pytestmark = pytest.mark.database

STYLE = {"avatar_style": "masculine", "skin_tone": "tone_5", "hair_style": "parted", "hair_color": "dark_brown", "outfit": "formal", "accent": "navy"}


def _count(app_session: Callable[..., Iterator[Session]], sql: str, user_id: str | None, tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> int:
    with app_session(tenant_context(tenant, workspace)) as session:
        if user_id is not None:
            session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": user_id})
        return int(session.execute(text(sql)).scalar_one())


def test_the_style_persists_and_a_private_agents_style_stays_private(
    agent_client: TestClient, app_session: Callable[..., Iterator[Session]]
) -> None:
    mine = agent_client.post("/api/v1/agents", headers=headers(), json={"name": "Desk A", "avatar": STYLE}).json()
    shared = agent_client.post("/api/v1/agents", headers=headers(), json={"name": "Desk B", "sharing": "workspace", "avatar": {"accent": "teal"}}).json()
    agent_store.clear()  # restart: read back from PostgreSQL
    assert agent_client.get(f"/api/v1/agents/{mine['id']}", headers=headers()).json()["avatar"]["skin_tone"] == "tone_5"
    assert agent_client.get(f"/api/v1/agents/{shared['id']}", headers=headers("user_other")).json()["avatar"]["accent"] == "teal"
    assert agent_client.get(f"/api/v1/agents/{mine['id']}", headers=headers("user_other")).status_code == 404

    sql = "select count(*) from agent_profiles where avatar is not null"
    assert _count(app_session, sql, "user_test") == 2
    assert _count(app_session, sql, "user_other") == 1
    assert _count(app_session, sql, None) == 1
    assert _count(app_session, sql, "user_test", workspace=WORKSPACE_A2) == 0
    assert _count(app_session, sql, "user_test", TENANT_B, WORKSPACE_B) == 0

    # The style changes and clears through the API, audited without any text of the profile.
    patched = agent_client.patch(f"/api/v1/agents/{mine['id']}", headers=headers(), json={"avatar": None})
    assert patched.status_code == 200 and patched.json()["avatar"] is None
    assert _count(app_session, "select count(*) from agent_profiles where avatar is not null", "user_test") == 1
    with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
        row = session.execute(text("select metadata::text as m from audit_records where action = 'agent.updated'")).one()
    assert '"avatar"' in row.m and "Desk A" in row.m  # the summary names the configuration (name, style); never instructions


def test_the_database_refuses_a_style_that_is_not_an_object_and_a_foreign_write(
    agent_client: TestClient, app_session: Callable[..., Iterator[Session]]
) -> None:
    mine = agent_client.post("/api/v1/agents", headers=headers(), json={"name": "Desk A", "avatar": STYLE}).json()
    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            session.execute(text("update agent_profiles set avatar = '[1]'::jsonb where id = :id"), {"id": mine["id"]})
    for tenant, workspace in ((TENANT_A, WORKSPACE_A2), (TENANT_B, WORKSPACE_B)):
        with app_session(tenant_context(tenant, workspace)) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            assert session.execute(text("update agent_profiles set avatar = null where id = :id"), {"id": mine["id"]}).rowcount == 0
    assert agent_client.get(f"/api/v1/agents/{mine['id']}", headers=headers()).json()["avatar"]["accent"] == "navy"


def test_a_team_members_style_is_scoped_to_the_workspace(
    team_client: TestClient, app_session: Callable[..., Iterator[Session]], database_engine: Engine
) -> None:
    body = {"display_name": "Owner A", "timezone": "Asia/Riyadh", "avatar": STYLE}
    assert team_client.put("/api/v1/team/people/owner_a", headers=TEAM_OWNER, json=body).status_code == 200
    again = team_client.put("/api/v1/team/people/owner_a", headers=TEAM_OWNER, json={"display_name": "Owner A", "timezone": "Asia/Riyadh"})
    assert again.json()["avatar"]["outfit"] == "formal"  # omitted keeps the stored style
    sql = "select count(*) from team_member_profiles where avatar is not null"
    assert _count(app_session, sql, "owner_a") == 1
    assert _count(app_session, sql, "owner_a", workspace=WORKSPACE_A2) == 0
    assert _count(app_session, sql, "owner_b", TENANT_B, WORKSPACE_B) == 0
    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
            session.execute(text("update team_member_profiles set avatar = '\"x\"'::jsonb where user_id = 'owner_a'"))
    with database_engine.connect() as connection:
        columns = connection.execute(text(
            "select table_name from information_schema.columns where column_name = 'avatar' and table_name in ('agent_profiles', 'team_member_profiles') order by 1"
        )).scalars().all()
    assert columns == ["agent_profiles", "team_member_profiles"]
