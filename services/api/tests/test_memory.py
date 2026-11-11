from datetime import datetime, timedelta, timezone

import pytest

from anum_api.memory import (
    InMemoryMemoryRepository,
    MemoryAccess,
    MemoryCreate,
    MemoryListFilters,
    MemoryService,
    MemoryUpdate,
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


def test_workspace_notes_need_no_task_and_only_their_author_or_an_owner_edits_them(service: MemoryService) -> None:
    author, other = context(user_id="user_a"), context(user_id="user_b")
    note = service.create(author, MemoryCreate(content="Shared fact", source_type="user", retention=RetentionPolicy(kind=RetentionKind.INDEFINITE)))
    assert note.task_id is None and note.owner_user_id is None
    assert [n.id for n in service.list(other, MemoryAccess())] == [note.id]

    edited = service.update(author, note.id, MemoryUpdate(content="Shared fact, corrected"), MemoryAccess())
    assert edited is not None and edited.content == "Shared fact, corrected" and edited.updated_at == NOW
    assert edited.provenance.created_by_user_id == "user_a" and edited.provenance.metadata["last_edited_by"] == "user_a"
    with pytest.raises(PermissionError):
        service.update(other, note.id, MemoryUpdate(content="no"), MemoryAccess())
    by_owner = service.update(other, note.id, MemoryUpdate(content="Owner edit"), MemoryAccess(can_edit_any=True))
    assert by_owner is not None and by_owner.provenance.metadata["last_edited_by"] == "user_b"
    # Invisible notes answer None (another workspace, or a task the caller cannot read).
    assert service.update(context(workspace_id="workspace_z"), note.id, MemoryUpdate(content="x"), MemoryAccess(can_edit_any=True)) is None
    task_note = create_note(service, author, task_id="task_private")
    assert service.update(other, task_note.id, MemoryUpdate(content="x"), MemoryAccess(readable_task_ids=set(), can_edit_any=True)) is None
    # A private chat memory is invisible to everyone but its owner: an owner's edit finds nothing.
    private = service.repository.create(note.model_copy(update={"id": "memory_private", "owner_user_id": "user_a", "provenance": note.provenance}))
    assert service.update(context(user_id="user_c"), private.id, MemoryUpdate(content="x"), MemoryAccess(can_edit_any=True)) is None
    assert service.update(author, private.id, MemoryUpdate(content="mine"), MemoryAccess()) is not None


def test_offset_skips_the_newest_matches_and_count_ignores_paging(service: MemoryService) -> None:
    ctx = context()
    repository = service.repository
    for index in range(5):
        service.clock = lambda index=index: NOW + timedelta(minutes=index)
        create_note(service, ctx, content=f"note {index}")
    service.clock = lambda: NOW + timedelta(hours=1)
    everything = service.list(ctx, MemoryAccess(can_read_all_workspace_tasks=True))
    assert [n.content for n in everything] == [f"note {i}" for i in range(5)]
    page = service.list(ctx, MemoryAccess(can_read_all_workspace_tasks=True), MemoryListFilters(limit=2, offset=1))
    assert [n.content for n in page] == ["note 2", "note 3"]
    tail = service.list(ctx, MemoryAccess(can_read_all_workspace_tasks=True), MemoryListFilters(limit=2, offset=4))
    assert [n.content for n in tail] == ["note 0"]
    assert service.list(ctx, MemoryAccess(can_read_all_workspace_tasks=True), MemoryListFilters(limit=2, offset=7)) == []
    assert service.count(ctx, MemoryListFilters(limit=2, offset=4)) == 5
    assert service.count(ctx, MemoryListFilters(query="note 3")) == 1
    assert repository.count(context(workspace_id="other"), MemoryListFilters(), NOW) == 0
