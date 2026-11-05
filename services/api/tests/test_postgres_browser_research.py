"""Browser research's workspace switch on PostgreSQL (docs/browser-research.md#workspace-switch).

Off in every workspace until an owner turns it on; the switch is a row in the RLS-protected
``integration_configurations`` table, read by the tool in the caller's own tenant scope.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import httpx
import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from anum_api import main
from anum_api.agent_tools import ToolCall
from anum_api.browser_research import BROWSE_TOOL, BROWSER_INTEGRATION_ID
from anum_api.browser_research.tool import BrowserResearchTool
from anum_api.db import session as db_session
from anum_api.integrations import IntegrationRegistry, browser_research_integration
from anum_api.settings import settings

pytestmark = pytest.mark.database

TOKEN = "browser-service-token-for-tests-only-example"


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, roles: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": "user_test", "x-user-roles": roles}


@pytest.fixture
def registry(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[IntegrationRegistry]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    configured = settings.model_copy(update={"browser_research_url": "http://browser:8090", "browser_research_token": TOKEN})
    integrations = IntegrationRegistry([browser_research_integration(configured)])
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(main, "integration_registry", integrations)
    yield integrations


def _rows(database_engine: Engine, sql: str) -> list[tuple]:
    with database_engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(sql))]


def test_the_switch_is_off_by_default_and_on_only_where_an_owner_turned_it_on(registry: IntegrationRegistry, database_engine: Engine) -> None:
    api = TestClient(main.app)
    enabled = registry.workspace_enabled(BROWSER_INTEGRATION_ID)
    a, a2, b = tenant_context(TENANT_A, WORKSPACE_A), tenant_context(TENANT_A, WORKSPACE_A2), tenant_context(TENANT_B, WORKSPACE_B)
    assert not enabled(a) and not enabled(a2) and not enabled(b)

    member = api.put(f"/api/v1/integrations/{BROWSER_INTEGRATION_ID}/configuration", headers=headers(roles="member"), json={"enabled": True})
    assert member.status_code == 403
    saved = api.put(f"/api/v1/integrations/{BROWSER_INTEGRATION_ID}/configuration", headers=headers(), json={"enabled": True})
    assert saved.status_code == 200, saved.text

    assert enabled(a) and not enabled(a2) and not enabled(b)
    assert _rows(database_engine, "select tenant_id, workspace_id, integration_id, enabled from integration_configurations") == [
        (TENANT_A, WORKSPACE_A, BROWSER_INTEGRATION_ID, True)
    ]
    audit = _rows(database_engine, "select tenant_id, workspace_id, action, target from audit_records where action = 'integration.configured'")
    assert audit == [(TENANT_A, WORKSPACE_A, "integration.configured", f"integration:{BROWSER_INTEGRATION_ID}")]

    health = {item["id"]: item for item in api.get("/api/v1/integrations", headers=headers()).json()}
    assert health[BROWSER_INTEGRATION_ID]["enabled"] is True
    other = {item["id"]: item for item in api.get("/api/v1/integrations", headers=headers(workspace_id=WORKSPACE_A2)).json()}
    assert other[BROWSER_INTEGRATION_ID]["enabled"] is False
    assert other[BROWSER_INTEGRATION_ID]["detail"] == "Off for this workspace until an owner turns it on."

    # The tool reads the switch in the caller's scope: only workspace A reaches the service.
    calls: list[httpx.Request] = []

    def service(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"outcome": "succeeded", "url": "https://site.example/", "final_url": "https://site.example/", "title": "T", "text": "x", "links": [], "stats": {}})

    tool = BrowserResearchTool(base_url="http://browser:8090", token=TOKEN, enabled=enabled, client=httpx.AsyncClient(transport=httpx.MockTransport(service)))
    call = ToolCall(name=BROWSE_TOOL, arguments={"url": "https://site.example/"})
    assert asyncio.run(tool(call, a)).status == "succeeded"
    assert asyncio.run(tool(call, a2)).audit_metadata["outcome"] == "workspace_disabled"
    assert asyncio.run(tool(call, b)).audit_metadata["outcome"] == "workspace_disabled"
    assert len(calls) == 1

    # Turning it off again stops it at once.
    assert api.put(f"/api/v1/integrations/{BROWSER_INTEGRATION_ID}/configuration", headers=headers(), json={"enabled": False}).status_code == 200
    assert not enabled(a)
