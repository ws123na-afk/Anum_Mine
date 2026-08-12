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
