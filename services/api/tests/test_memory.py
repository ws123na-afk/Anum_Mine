from datetime import datetime, timedelta, timezone

import pytest

from anum_api.memory import (
    InMemoryMemoryRepository,
    MemoryAccess,
    MemoryCreate,
    MemoryListFilters,
    MemoryService,
    RetentionKind,
    RetentionPolicy,
)
from anum_api.schemas import TenantContext


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


def context(
    tenant_id: str = "tenant_a",
    workspace_id: str = "workspace_a",
    user_id: str = "user_a",
) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        roles=["member"],
    )


@pytest.fixture
def service() -> MemoryService:
    return MemoryService(InMemoryMemoryRepository(), clock=lambda: NOW)


def create_note(
    service: MemoryService,
    ctx: TenantContext,
    *,
    task_id: str = "task_a",
    content: str = "Customer prefers weekly project summaries",
    retention: RetentionPolicy | None = None,
):
    return service.create(
        ctx,
        MemoryCreate(
            task_id=task_id,
            content=content,
            source_type="task_message",
            source_id="message_1",
            source_metadata={"turn": 3},
            retention=retention or RetentionPolicy(),
        ),
    )


def test_create_and_list_preserves_scope_and_provenance(service: MemoryService) -> None:
    ctx = context()
    created = create_note(service, ctx)

    listed = service.list(ctx, MemoryAccess(readable_task_ids={"task_a"}))

    assert listed == [created]
    assert created.tenant_id == "tenant_a"
    assert created.workspace_id == "workspace_a"
    assert created.task_id == "task_a"
    assert created.provenance.created_by_user_id == "user_a"
    assert created.provenance.source_type == "task_message"
    assert created.provenance.source_id == "message_1"
    assert created.provenance.metadata == {"turn": 3}


def test_tenant_workspace_and_task_permissions_isolate_notes(service: MemoryService) -> None:
    created = create_note(service, context())
    create_note(service, context(workspace_id="workspace_b"), task_id="task_b")
    create_note(service, context(tenant_id="tenant_b"), task_id="task_c")

    assert service.list(
        context(), MemoryAccess(readable_task_ids={created.task_id})
    ) == [created]
    assert service.list(context(), MemoryAccess(readable_task_ids={"task_private"})) == []
    assert service.list(
        context(tenant_id="tenant_b"), MemoryAccess(readable_task_ids={"task_a"})
    ) == []


def test_retrieval_uses_deterministic_text_and_provenance_filters(
    service: MemoryService,
) -> None:
    ctx = context()
    first = create_note(service, ctx, content="Weekly project summary is preferred")
    create_note(service, ctx, content="Daily billing report is preferred")

    matches = service.list(
        ctx,
        MemoryAccess(can_read_all_workspace_tasks=True),
        MemoryListFilters(
            query="PROJECT weekly",
            source_types={"task_message"},
        ),
    )

    assert matches == [first]


def test_expired_notes_are_hidden_unless_explicitly_requested() -> None:
    clock = [NOW]
    service = MemoryService(InMemoryMemoryRepository(), clock=lambda: clock[0])
    note = create_note(
        service,
        context(),
        retention=RetentionPolicy(
            kind=RetentionKind.EXPIRES_AT,
            expires_at=NOW + timedelta(hours=1),
        ),
    )
    access = MemoryAccess(readable_task_ids={"task_a"})
    clock[0] = NOW + timedelta(hours=2)

    assert service.list(context(), access) == []
    assert service.list(
        context(), access, MemoryListFilters(include_expired=True)
    ) == [note]


def test_invalid_retention_policy_is_rejected(service: MemoryService) -> None:
    with pytest.raises(ValueError, match="requires an expiry"):
        create_note(
            service,
            context(),
            retention=RetentionPolicy(kind=RetentionKind.EXPIRES_AT),
        )

    with pytest.raises(ValueError, match="only valid"):
        create_note(
            service,
            context(),
            retention=RetentionPolicy(
                kind=RetentionKind.INDEFINITE,
                expires_at=NOW + timedelta(days=1),
            ),
        )


def test_delete_requires_visibility_and_owner_or_elevated_permission(
    service: MemoryService,
) -> None:
    owner = context()
    note = create_note(service, owner)
    collaborator = context(user_id="user_b")
    readable = MemoryAccess(readable_task_ids={"task_a"})

    assert service.delete(collaborator, note.id, readable) is False
    assert service.delete(
        collaborator,
        note.id,
        MemoryAccess(readable_task_ids={"task_a"}, can_delete_any=True),
    ) is True
    assert service.list(owner, readable) == []


def test_cross_scope_delete_does_not_reveal_or_remove_note(service: MemoryService) -> None:
    owner = context()
    note = create_note(service, owner)
    elevated = MemoryAccess(can_read_all_workspace_tasks=True, can_delete_any=True)

    assert service.delete(context(workspace_id="workspace_b"), note.id, elevated) is False
    assert service.delete(context(tenant_id="tenant_b"), note.id, elevated) is False
    assert service.list(owner, MemoryAccess(readable_task_ids={"task_a"})) == [note]
