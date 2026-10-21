"""Real PostgreSQL application-role tests for live meeting API and lifecycle."""
from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import sessionmaker

from anum_api.db import session as db_session
from anum_api.live_agent_meeting_store import SqlLiveMeetingStore, LiveMeetingStoreError
from anum_api.schemas import TenantContext
from anum_api.settings import settings

pytestmark = pytest.mark.database


@pytest.fixture
def store_factory(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)
    @event.listens_for(factory, "after_begin")
    def _app_role(session, transaction, connection):
        connection.execute(text(f"set local role {APP_ROLE}"))
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    yield factory


def context(tenant=TENANT_A, workspace=WORKSPACE_A, owner="office_owner"):
    return TenantContext(tenant_id=tenant, workspace_id=workspace, user_id=owner, roles=["owner"])


def create(store, ctx, meeting_id):
    return store.create(ctx, {
        "id": meeting_id, "title": "Daily operations",
        "max_turns": 6, "max_duration_seconds": 300, "max_tokens": 2000,
        "max_cost_usd": 0.50, "max_participants": 4,
    })


def test_session_creation_list_and_state_with_expected_revision(store_factory):
    from anum_api.db.session import set_tenant_context
    ctx=context()
    with store_factory() as session:
        set_tenant_context(session, ctx.tenant_id, ctx.workspace_id, user_id=ctx.user_id)
        store=SqlLiveMeetingStore(session)
        meeting_id="meeting-"+uuid4().hex
        initial=create(store,ctx,meeting_id)
        assert initial["revision"] == 1
        assert initial["status"] == "scheduled"
        assert store.get(ctx,meeting_id)["id"] == meeting_id
        with pytest.raises(LiveMeetingStoreError,match="participant"):
            store.transition(ctx, meeting_id, 1, "active")
        next_state=store.add_self(ctx,meeting_id,"participant-"+uuid4().hex,1)
        assert next_state["revision"] == 2
        active=store.transition(ctx,meeting_id,2,"active")
        assert active["revision"] == 3
        assert active["started_at"] is not None
        with pytest.raises(LiveMeetingStoreError,match="changed"):
            store.transition(ctx,meeting_id,2,"finished")
        finished=store.transition(ctx,meeting_id,3,"finished")
        assert finished["ended_at"] is not None
        assert store.turns(ctx,meeting_id) == []
        session.rollback()


def test_workspace_isolation_and_owner_only_mutation(store_factory):
    from anum_api.db.session import set_tenant_context
    ctx=context()
    meeting_id="scope-"+uuid4().hex
    with store_factory() as session:
        set_tenant_context(session,ctx.tenant_id,ctx.workspace_id,user_id=ctx.user_id)
        store=SqlLiveMeetingStore(session)
        create(store,ctx,meeting_id)
        unprivileged=context(owner="another_owner")
        with pytest.raises(LiveMeetingStoreError,match="owner"):
            store.transition(unprivileged, meeting_id, 1, "cancelled")
        other=context(TENANT_B,WORKSPACE_B)
        set_tenant_context(session,other.tenant_id,other.workspace_id,user_id=other.user_id)
        assert store.get(other,meeting_id) is None
        session.rollback()
