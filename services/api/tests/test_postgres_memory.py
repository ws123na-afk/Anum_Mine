from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from anum_api.db.memory_repository import SqlAlchemyMemoryRepository
from anum_api.db.repository import SqlAlchemyRepository
from anum_api.memory import MemoryAccess, MemoryCreate, MemoryListFilters, MemoryService
from anum_api.schemas import Task, TaskStatus

from conftest import FIXED_NOW, tenant_context


pytestmark = pytest.mark.database


def test_memory_round_trip_is_scoped_and_durable(
    seed_scopes: None,
    app_session: Callable[..., Iterator[Session]],
) -> None:
    context = tenant_context()
    with app_session(context, commit=True) as session:
        SqlAlchemyRepository(session).create_task(
            Task(
                id="task_repo_a",
                title="Memory task",
                prompt="Remember this",
                status=TaskStatus.CREATED,
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                created_at=FIXED_NOW,
                updated_at=FIXED_NOW,
            )
        )
        service = MemoryService(
            SqlAlchemyMemoryRepository(session),
            clock=lambda: FIXED_NOW,
        )
        created = service.create(
            context,
            MemoryCreate(
                task_id="task_repo_a",
                content="The launch decision is Friday.",
                source_type="user_note",
            ),
        )

    with app_session(context) as session:
        service = MemoryService(
            SqlAlchemyMemoryRepository(session),
            clock=lambda: FIXED_NOW + timedelta(minutes=1),
        )
        notes = service.list(
            context,
            MemoryAccess(can_read_all_workspace_tasks=True),
            MemoryListFilters(query="launch Friday"),
        )
        assert [note.id for note in notes] == [created.id]
        assert notes[0].provenance.created_by_user_id == context.user_id

    other_workspace = tenant_context(workspace_id="workspace_test_a2")
    with app_session(other_workspace) as session:
        service = MemoryService(SqlAlchemyMemoryRepository(session))
        assert service.list(
            other_workspace,
            MemoryAccess(can_read_all_workspace_tasks=True),
        ) == []


def test_memory_list_limit_keeps_the_newest_notes(
    seed_scopes: None,
    app_session: Callable[..., Iterator[Session]],
) -> None:
    context = tenant_context()
    with app_session(context, commit=True) as session:
        SqlAlchemyRepository(session).create_task(
            Task(
                id="task_repo_a",
                title="Memory task",
                prompt="Remember this",
                status=TaskStatus.CREATED,
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                created_at=FIXED_NOW,
                updated_at=FIXED_NOW,
            )
        )
        created = []
        for index in range(4):
            service = MemoryService(
                SqlAlchemyMemoryRepository(session),
                clock=lambda index=index: FIXED_NOW + timedelta(minutes=index),
            )
            created.append(
                service.create(
                    context,
                    MemoryCreate(task_id="task_repo_a", content=f"note {index}", source_type="user_note"),
                ).id
            )

    with app_session(context) as session:
        repository = SqlAlchemyMemoryRepository(session)
        now = FIXED_NOW + timedelta(hours=1)
        assert [n.id for n in repository.list(context, MemoryListFilters(limit=2), now)] == created[2:]
        assert [n.id for n in repository.list(context, MemoryListFilters(), now)] == created


def test_workspace_notes_edit_count_and_page_in_postgres(
    seed_scopes: None,
    app_session: Callable[..., Iterator[Session]],
) -> None:
    from sqlalchemy import text

    from anum_api.memory import MemoryUpdate

    context = tenant_context()
    other = context.model_copy(update={"user_id": "user_other"})
    with app_session(context, commit=True) as session:
        service = MemoryService(SqlAlchemyMemoryRepository(session), clock=lambda: FIXED_NOW)
        notes = [
            service.create(context, MemoryCreate(content=f"Workspace fact {index}", source_type="user"))
            for index in range(3)
        ]
        assert all(note.task_id is None and note.owner_user_id is None for note in notes)
        kinds = session.execute(text("select distinct kind from memories")).scalars().all()
        assert kinds == ["workspace_note"]

    with app_session(other, commit=True) as session:
        service = MemoryService(SqlAlchemyMemoryRepository(session), clock=lambda: FIXED_NOW + timedelta(minutes=5))
        # Shared with the workspace: another member lists and counts them, pages them, and an owner edits.
        access = MemoryAccess(can_read_all_workspace_tasks=True)
        assert [n.content for n in service.list(other, access)] == [f"Workspace fact {i}" for i in range(3)]
        assert service.count(other, MemoryListFilters()) == 3
        page = service.list(other, access, MemoryListFilters(limit=1, offset=1))
        assert [n.content for n in page] == ["Workspace fact 1"]
        assert service.count(other, MemoryListFilters(query="fact 2")) == 1
        with pytest.raises(PermissionError):
            service.update(other, notes[0].id, MemoryUpdate(content="not mine"), access)
        edited = service.update(other, notes[0].id, MemoryUpdate(content="Edited by an owner"), MemoryAccess(can_read_all_workspace_tasks=True, can_edit_any=True))
        assert edited is not None and edited.updated_at == FIXED_NOW + timedelta(minutes=5)

    with app_session(context) as session:
        stored = SqlAlchemyMemoryRepository(session).get(notes[0].id, context)
        assert stored is not None and stored.content == "Edited by an owner"
        assert stored.provenance.created_by_user_id == context.user_id
        assert stored.provenance.metadata["last_edited_by"] == "user_other"

    elsewhere = tenant_context(context.tenant_id, "workspace_test_a2")
    with app_session(elsewhere) as session:
        repository = SqlAlchemyMemoryRepository(session)
        assert repository.count(elsewhere, MemoryListFilters(), FIXED_NOW) == 0
        assert repository.update_content(notes[0].id, elsewhere, "x", notes[0].provenance, FIXED_NOW) is None
