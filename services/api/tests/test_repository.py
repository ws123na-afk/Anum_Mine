from anum_api.repository import InMemoryRepository
from anum_api.schemas import Task, TaskStatus, TenantContext, utc_now
from anum_api.store import InMemoryStore


def make_context(tenant_id: str = "tenant_a") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        workspace_id="workspace_a",
        user_id="user_a",
        roles=["owner"],
    )


def make_task(tenant_id: str = "tenant_a") -> Task:
    now = utc_now()
    return Task(
        id="task_1",
        title="Repository test",
        prompt="Check repository behavior",
        status=TaskStatus.CREATED,
        tenant_id=tenant_id,
        workspace_id="workspace_a",
        created_at=now,
        updated_at=now,
    )


def test_repository_returns_task_for_matching_tenant_context() -> None:
    repository = InMemoryRepository(InMemoryStore())
    task = repository.create_task(make_task())

    assert repository.get_task(task.id, make_context()) == task


def test_repository_hides_task_for_other_tenant() -> None:
    repository = InMemoryRepository(InMemoryStore())
    task = repository.create_task(make_task())

    assert repository.get_task(task.id, make_context("tenant_b")) is None
