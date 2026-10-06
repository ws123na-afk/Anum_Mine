"""Workspace invitations and membership management on PostgreSQL, under RLS."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from anum_api.audit import AuditRecord
from anum_api.schemas import WorkspaceInvitation, WorkspaceMembership, utc_now
from anum_api.settings import settings
from anum_api.workspace_members import _ensure_not_last_owner, _member_or_404, hash_invitation_token

from conftest import (
    APP_ROLE,
    FIXED_NOW,
    TENANT_A,
    TENANT_B,
    WORKSPACE_A,
    WORKSPACE_A2,
    WORKSPACE_B,
    tenant_context,
)


pytestmark = pytest.mark.database


def headers(user_id: str, *, roles: str = "viewer", tenant: str = TENANT_A,
            workspace: str = WORKSPACE_A) -> dict[str, str]:
    return {
        "x-tenant-id": tenant,
        "x-workspace-id": workspace,
        "x-user-id": user_id,
        "x-user-roles": roles,
    }


OWNER = headers("owner_pg", roles="owner")


def membership(user_id: str, role: str, *, tenant: str = TENANT_A,
               workspace: str = WORKSPACE_A, active: bool = True) -> WorkspaceMembership:
    return WorkspaceMembership(
        tenant_id=tenant, workspace_id=workspace, user_id=user_id, role=role, active=active,
        created_at=FIXED_NOW, updated_at=FIXED_NOW,
    )


@pytest.fixture
def api(database_engine: Engine, seed_scopes: None, repository_factory,
        monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    import anum_api.db.session as db_session

    def app_role_session() -> Session:
        session = Session(bind=database_engine)
        session.execute(text(f"set local role {APP_ROLE}"))
        return session

    monkeypatch.setattr(db_session, "SessionLocal", app_role_session)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(settings, "auth_mode", "headers")
    for tenant, workspace in ((TENANT_A, WORKSPACE_A), (TENANT_B, WORKSPACE_B)):
        with repository_factory(tenant_context(tenant, workspace), commit=True) as repository:
            repository.save_membership(membership("owner_pg", "owner", tenant=tenant, workspace=workspace))
    from anum_api.main import app

    yield TestClient(app)


def invite(client: TestClient, **body) -> dict:
    response = client.post("/api/v1/workspace-invitations", headers=OWNER, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def accept(client: TestClient, token: str, caller: dict[str, str]):
    return client.post("/api/v1/workspace-invitations/accept", headers=caller, json={"token": token})


def test_invitation_flow_persists_hash_membership_events_and_audit(
    api: TestClient, database_engine: Engine, repository_factory
) -> None:
    created = invite(api, role="member", invitee_user_id="user_pg")
    token = created["token"]

    with database_engine.connect() as connection:
        stored = connection.execute(
            text("select token_hash from workspace_invitations where id = :id"),
            {"id": created["invitation"]["id"]},
        ).scalar_one()
        dump = connection.execute(text("select row_to_json(i)::text from workspace_invitations i")).scalar_one()
    assert stored == hash_invitation_token(token)
    assert token not in dump

    response = accept(api, token, headers("user_pg"))
    assert response.status_code == 200, response.text
    assert accept(api, token, headers("user_pg")).status_code == 409

    scope = tenant_context(TENANT_A, WORKSPACE_A)
    with repository_factory(scope) as repository:
        member = repository.get_member_for_update("user_pg", scope)
        assert member is not None and member.role == "member" and member.active
        actions = {record.action for record in repository.list_audit_records(scope)}
        types = {event.type for event in repository.list_events(scope)}
    assert {"workspace_invitation.create", "workspace_invitation.accept", "workspace_member.add"} <= actions
    assert {"workspace_invitation.created", "workspace_invitation.accepted", "workspace_member.added"} <= types


def test_expired_wrong_user_and_cross_tenant_tokens_are_refused(
    api: TestClient, database_engine: Engine
) -> None:
    created = invite(api, role="viewer", invitee_user_id="user_bound")
    token = created["token"]

    assert accept(api, token, headers("someone_else")).status_code == 403
    assert accept(api, token, headers("user_bound", tenant=TENANT_B, workspace=WORKSPACE_B)).status_code == 404
    assert accept(api, token, headers("user_bound", workspace=WORKSPACE_A2)).status_code == 404

    with database_engine.begin() as connection:
        connection.execute(
            text("update workspace_invitations set expires_at = now() - interval '1 second'")
        )
    expired = accept(api, token, headers("user_bound"))
    assert expired.status_code == 410

    with database_engine.connect() as connection:
        status, members = connection.execute(
            text(
                "select (select status from workspace_invitations), "
                "(select count(*) from workspace_memberships where user_id = 'user_bound')"
            )
        ).one()
    assert (status, members) == ("pending", 0)


def test_other_tenant_owner_cannot_see_or_revoke_invitations(api: TestClient) -> None:
    created = invite(api, role="member", invitee_email="pg@example.com")
    other_owner = headers("owner_pg", roles="owner", tenant=TENANT_B, workspace=WORKSPACE_B)

    assert api.get("/api/v1/workspace-invitations", headers=other_owner).json() == []
    revoke = api.post(
        f"/api/v1/workspace-invitations/{created['invitation']['id']}/revoke", headers=other_owner
    )
    assert revoke.status_code == 404
    assert [m["user_id"] for m in api.get("/api/v1/workspace-members", headers=other_owner).json()] == [
        "owner_pg"
    ]


def test_last_owner_is_protected_through_the_api(api: TestClient) -> None:
    assert api.put(
        "/api/v1/workspace-members/owner_pg/role", headers=OWNER, json={"role": "viewer"}
    ).status_code == 409
    assert api.post("/api/v1/workspace-members/owner_pg/deactivate", headers=OWNER).status_code == 409

    created = invite(api, role="owner", invitee_user_id="owner_two")
    assert accept(api, created["token"], headers("owner_two")).status_code == 200
    assert api.post("/api/v1/workspace-members/owner_pg/deactivate", headers=OWNER).status_code == 200
    owner_two = headers("owner_two", roles="owner")
    assert api.put(
        "/api/v1/workspace-members/owner_two/role", headers=owner_two, json={"role": "member"}
    ).status_code == 409


def test_rls_scopes_invitations_and_audit_records(seed_scopes: None, repository_factory) -> None:
    scope_a = tenant_context(TENANT_A, WORKSPACE_A)
    scope_b = tenant_context(TENANT_B, WORKSPACE_B)
    now = utc_now()
    invitation = WorkspaceInvitation(
        id="invitation_rls", tenant_id=TENANT_A, workspace_id=WORKSPACE_A, role="member",
        invitee_user_id="u", token_hash="a" * 64, created_by_user_id="owner",
        expires_at=now + timedelta(hours=1), created_at=now, updated_at=now,
    )
    with repository_factory(scope_a, commit=True) as repository:
        repository.save_invitation(invitation)
        repository.record_audit(
            AuditRecord(
                id="audit_rls", tenant_id=TENANT_A, workspace_id=WORKSPACE_A, actor="owner",
                action="workspace_invitation.create", target="invitation_rls", outcome="success",
                correlation_id="c", created_at=now, metadata={"nested": {"list": (1, 2)}},
            )
        )

    with repository_factory(scope_b) as repository:
        assert repository.find_invitation_by_token_hash_for_update("a" * 64, scope_b) is None
        assert repository.list_invitations(scope_b) == []
        assert repository.list_audit_records(scope_b) == []
        assert repository.session.execute(text("select count(*) from workspace_invitations")).scalar_one() == 0
        assert repository.session.execute(text("select count(*) from audit_records")).scalar_one() == 0

    with repository_factory(scope_a) as repository:
        found = repository.find_invitation_by_token_hash_for_update("a" * 64, scope_a)
        assert found is not None and found.id == "invitation_rls"
        [audit] = repository.list_audit_records(scope_a)
        assert audit.metadata["nested"]["list"] == (1, 2)

    # Audit history is append-only for the application role.
    with repository_factory(scope_a, commit=True) as repository:
        updated = repository.session.execute(text("update audit_records set outcome = 'tampered'"))
        deleted = repository.session.execute(text("delete from audit_records"))
        assert updated.rowcount == 0 and deleted.rowcount == 0
    with repository_factory(scope_a) as repository:
        assert repository.list_audit_records(scope_a)[0].outcome == "success"


def test_concurrent_owner_demotions_cannot_remove_every_owner(
    database_engine: Engine, seed_scopes: None, repository_factory, app_session
) -> None:
    scope = tenant_context(TENANT_A, WORKSPACE_A)
    with repository_factory(scope, commit=True) as repository:
        repository.save_membership(membership("owner_1", "owner"))
        repository.save_membership(membership("owner_2", "owner"))

    from anum_api.db.repository import SqlAlchemyRepository

    first_locked = threading.Event()
    results: dict[str, str] = {}

    def demote(user_id: str, *, hold: bool) -> None:
        with app_session(scope, commit=True) as session:
            repository = SqlAlchemyRepository(session)
            try:
                member = _member_or_404(repository, scope, user_id)
                _ensure_not_last_owner(repository, scope, member)
            except HTTPException as exc:
                results[user_id] = f"refused {exc.status_code}"
                return
            if hold:
                first_locked.set()
                time.sleep(0.3)  # the other demotion must wait for these owner locks
            repository.save_membership(member.model_copy(update={"role": "member"}))
            results[user_id] = "demoted"

    first = threading.Thread(target=demote, args=("owner_1",), kwargs={"hold": True})
    first.start()
    assert first_locked.wait(5)
    second = threading.Thread(target=demote, args=("owner_2",), kwargs={"hold": False})
    second.start()
    first.join(10)
    second.join(10)

    assert results == {"owner_1": "demoted", "owner_2": "refused 409"}
    with repository_factory(scope) as repository:
        assert [m.user_id for m in repository.list_active_owners_for_update(scope)] == ["owner_2"]
