"""The command sweep (docs/command.md#the-sweep) against the in-memory backend: a waiting step whose
completion never reached the in-process continuer (no NATS, a restart) is continued once by the sweep,
a refused step is never retried on its own, and a step still waiting stays for a later pass."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from fastapi.testclient import TestClient

from anum_api.command_continuation import command_continuer, memory_attempts
from anum_api.command_sweep import (
    CommandSweeper,
    build_command_sweeper,
    discover_waiting,
    run_sweep_pass,
)
from anum_api.commands import memory_command_store
from anum_api.main import app, store
from anum_api.schemas import utc_now
from anum_api.settings import settings

client = TestClient(app)
owner = {"x-tenant-id": "tenant_a", "x-workspace-id": "workspace_a", "x-user-id": "user_owner", "x-user-roles": "owner,member"}
other_tenant = {**owner, "x-tenant-id": "tenant_b", "x-workspace-id": "workspace_b"}
NO_TYRE = (
    "Check my emails, prepare a summary, and schedule a meeting with the team tomorrow at 10 a.m. "
    "Then send the summary to the team."
)


def setup_function() -> None:
    store.tasks.clear()
    store.runs.clear()
    store.approvals.clear()
    store.events.clear()
    store.audit_records.clear()
    memory_command_store.clear()
    memory_attempts.clear()
    command_continuer.clear()


def _create(headers: dict[str, str] = owner) -> dict:
    plan = client.post("/api/v1/commands/preview", headers=headers, json={"text": NO_TYRE})
    assert plan.status_code == 200, plan.text
    steps = [{"instruction": s["instruction"], "depends_on": s["depends_on"]} for s in plan.json()["steps"]]
    created = client.post("/api/v1/commands", headers=headers, json={"text": NO_TYRE, "steps": steps})
    assert created.status_code == 201, created.text
    return created.json()


def _run(task_id: str, headers: dict[str, str] = owner) -> None:
    response = client.post(f"/api/v1/tasks/{task_id}/run", headers=headers)
    assert response.status_code == 200, response.text


def test_the_sweep_continues_a_step_whose_completion_was_lost_exactly_once() -> None:
    command = _create()
    ids = [step["task_id"] for step in command["steps"]]
    assert command["steps"][2]["depends_on"] == [1, 2]
    # Before its earlier steps complete the waiting step is found but not ready.
    assert list(discover_waiting().values()) == [[ids[2]]]
    assert asyncio.run(run_sweep_pass()) == {"commands": 0, "started": 0, "waiting": 1}
    for task_id in ids[:2]:
        _run(task_id)
    # The completions never reach the continuer (the worker finished them without NATS, or the API restarted).
    command_continuer.clear()
    assert store.tasks[ids[2]].status == "created"
    counts = asyncio.run(run_sweep_pass())
    assert counts == {"commands": 1, "started": 1, "waiting": 0}
    assert store.tasks[ids[2]].status != "created"
    assert memory_attempts.rows[("tenant_a", "workspace_a", ids[2])] == (command["id"], "started", "sweep")
    # Once: a second pass finds nothing, and the step ran once.
    assert discover_waiting() == {}
    assert asyncio.run(run_sweep_pass())["started"] == 0
    assert len([run for run in store.runs.values() if run.task_id == ids[2]]) == 1
    # It started as the command's creator, through the same path as POST /tasks/{id}/run.
    view = client.get(f"/api/v1/commands/{command['id']}", headers=owner).json()
    assert view["steps"][2]["status"] == "waiting_approval"


def test_a_step_the_continuer_already_tried_is_left_alone() -> None:
    command = _create()
    ids = [step["task_id"] for step in command["steps"]]
    for task_id in ids[:2]:
        _run(task_id)
    from anum_api.agent_controls import memory_agent_controls_store

    engaged = client.put("/api/v1/agent-controls/emergency-stop", headers=owner, json={"engaged": True, "reason": "Check first"})
    assert engaged.status_code == 200, engaged.text
    try:
        # The in-process continuer heard the completion and was refused by the emergency stop.
        started = []
        while (item := command_continuer._take()) is not None:
            started.extend(asyncio.run(command_continuer.handle(item)))
        assert started == [] and store.tasks[ids[2]].status == "created"
        assert memory_attempts.rows[("tenant_a", "workspace_a", ids[2])][1:] == ("refused", "event")
    finally:
        memory_agent_controls_store.clear()  # the stop is lifted
    # The sweep does not start it on its own once the stop is lifted; the person does.
    assert discover_waiting() == {}
    assert asyncio.run(run_sweep_pass())["started"] == 0
    assert store.tasks[ids[2]].status == "created"
    again = client.post(f"/api/v1/commands/{command['id']}/start", headers=owner).json()
    assert again["started"] == [ids[2]]


def test_a_refusal_during_the_sweep_is_recorded_and_not_retried() -> None:
    command = _create()
    ids = [step["task_id"] for step in command["steps"]]
    for task_id in ids[:2]:
        _run(task_id)
    command_continuer.clear()
    from fastapi import HTTPException

    original = command_continuer.runner
    calls: list[str] = []

    async def refuse(context, task_id):  # type: ignore[no-untyped-def]
        calls.append(task_id)
        raise HTTPException(status_code=402, detail="Budget used up")

    command_continuer.configure(runner=refuse)
    try:
        assert asyncio.run(run_sweep_pass())["started"] == 0
        assert asyncio.run(run_sweep_pass())["started"] == 0
    finally:
        command_continuer.configure(runner=original)  # type: ignore[arg-type]
    assert calls == [ids[2]]
    assert memory_attempts.rows[("tenant_a", "workspace_a", ids[2])][1:] == ("refused", "sweep")


def test_old_archived_and_other_creators_steps_are_out_of_scope() -> None:
    mine, theirs = _create(), _create(other_tenant)
    for command, headers in ((mine, owner), (theirs, other_tenant)):
        for step in command["steps"][:2]:
            _run(step["task_id"], headers)
    command_continuer.clear()
    # Older than the window: not found.
    found = discover_waiting(now=utc_now() + timedelta(days=8))
    assert found == {}
    # Each command is acted on in its own tenant, as its own creator.
    found = discover_waiting()
    assert sorted(key[0] for key in found) == ["tenant_a", "tenant_b"]
    assert asyncio.run(run_sweep_pass())["started"] == 2


def test_the_sweeper_runs_only_with_the_scheduler_and_records_its_pass(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "automation_scheduler_enabled", False)
    assert build_command_sweeper(settings) is None
    monkeypatch.setattr(settings, "automation_scheduler_enabled", True)
    sweeper = build_command_sweeper(settings)
    assert isinstance(sweeper, CommandSweeper) and sweeper.poll_interval == settings.command_sweep_seconds

    async def once() -> None:
        sweeper.start()
        for _ in range(100):
            if sweeper.last_pass_at is not None:
                break
            await asyncio.sleep(0.02)
        await sweeper.stop()

    asyncio.run(once())
    from anum_api.system_health import last_beat

    assert sweeper.last_pass_at is not None and last_beat("command_sweep") is not None
