"""OIDC membership resolution through the PostgreSQL repository, under row-level security."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from anum_api import dependencies
from anum_api.schemas import TenantContext, WorkspaceMembership
from anum_api.settings import settings

from conftest import APP_ROLE, FIXED_NOW, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2


pytestmark = pytest.mark.database

USER = "oidc-subject-1"


def context(tenant_id: str, workspace_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=USER, roles=[])


def test_oidc_membership_lookup_is_rls_scoped(
    database_engine: Engine,
    seed_scopes: None,
    repository_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with repository_factory(context(TENANT_A, WORKSPACE_A), commit=True) as repository:
        repository.save_membership(
            WorkspaceMembership(
                tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id=USER, role="member",
                created_at=FIXED_NOW, updated_at=FIXED_NOW,
            )
        )

    def app_role_session() -> Session:
        session = Session(bind=database_engine)
        session.execute(text(f"set local role {APP_ROLE}"))
        return session

    import anum_api.db.session as db_session

    monkeypatch.setattr(db_session, "SessionLocal", app_role_session)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")

    found = dependencies.lookup_membership(context(TENANT_A, WORKSPACE_A))
    other_workspace = dependencies.lookup_membership(context(TENANT_A, WORKSPACE_A2))
    other_tenant = dependencies.lookup_membership(context(TENANT_B, WORKSPACE_A))

    assert found is not None and found.role == "member"
    assert other_workspace is None
    assert other_tenant is None


def test_workspace_has_members_sees_only_its_own_workspace(seed_scopes: None, repository_factory) -> None:
    with repository_factory(context(TENANT_A, WORKSPACE_A), commit=True) as repository:
        assert repository.workspace_has_members(context(TENANT_A, WORKSPACE_A)) is False
        repository.save_membership(
            WorkspaceMembership(
                tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="someone-else", role="owner",
                created_at=FIXED_NOW, updated_at=FIXED_NOW,
            )
        )

    with repository_factory(context(TENANT_A, WORKSPACE_A)) as repository:
        assert repository.workspace_has_members(context(TENANT_A, WORKSPACE_A)) is True
    with repository_factory(context(TENANT_A, WORKSPACE_A2)) as repository:
        assert repository.workspace_has_members(context(TENANT_A, WORKSPACE_A2)) is False
