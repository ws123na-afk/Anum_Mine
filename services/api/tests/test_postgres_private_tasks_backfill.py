"""The backfill of migration ``0163_private_tasks`` the way production runs it.

Production migrates as ``anum_migrator``: it owns the tables, has no ``BYPASSRLS`` and sets no
``anum.tenant_id``, and RLS is forced on every table the backfill reads. The first version of
the migration lifted FORCE only on the four tables it wrote, so each sub-select on ``agent_runs``,
``agent_run_steps``, ``approvals``, ``conversation_turns`` and ``automation_workflows`` saw no row and
every backfill UPDATE matched nothing: no existing finance or chat task became private and
``accepted_version`` stayed null (review finding, November 2026). The schema is put back to 0162
inside one transaction, rows are seeded as the superuser, the upgrade runs as a non-superuser
owner role, and the transaction is rolled back. Every row is SYNTHETIC.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from conftest import TENANT_A, WORKSPACE_A
from sqlalchemy import Connection, Engine, text

pytestmark = pytest.mark.database

OWNER_ROLE = "anum_test_private_tasks_owner"
# Every table the backfill writes or reads through a sub-select.
TABLES = (
    "tasks", "domain_events", "automation_schedules", "automation_triggers",
    "agent_runs", "agent_run_steps", "approvals", "conversation_turns", "automation_workflows",
)
SCOPE = {"t": TENANT_A, "w": WORKSPACE_A}


def _migration() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0163_private_tasks.py"
    spec = importlib.util.spec_from_file_location("migration_0163_private_tasks", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _migration()


def _run(connection: Connection, step: str) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        getattr(MIGRATION, step)()


def _seed_at_0162(connection: Connection) -> None:
    """Rows a workspace had before private tasks existed (no ``private_to`` column yet)."""
    connection.execute(text("insert into tenants (id, name) values (:t, 'Tenant A')"), SCOPE)
    connection.execute(text("insert into workspaces (id, tenant_id, name) values (:w, :t, 'Workspace A')"), SCOPE)
    tasks = {
        "task_finance": "alice",   # a run proposed finance.summary: data private to alice
        "task_mail": "alice",      # a run proposed summarize_mail: data private to alice
        "task_chat": "bob",        # named by bob's chat card, no approval: chat private to bob
        "task_chat_approved": "bob",  # named by a chat card but its run waited for an approval: stays shared
        "task_shared": "carol",    # an ordinary task: stays shared
    }
    for task_id, user in tasks.items():
        connection.execute(text(
            "insert into tasks (id, tenant_id, workspace_id, created_by_user_id, title, prompt, status) "
            "values (:id, :t, :w, :u, 'Title', 'Prompt', 'completed')"), {**SCOPE, "id": task_id, "u": user})
        connection.execute(text(
            "insert into agent_runs (id, tenant_id, workspace_id, task_id, status) values (:r, :t, :w, :id, 'completed')"),
            {**SCOPE, "id": task_id, "r": f"run_{task_id}"})
        connection.execute(text(
            "insert into domain_events (id, tenant_id, workspace_id, type, subject, correlation_id, payload) "
            "values (:e, :t, :w, 'task.created', :id, :id, '{}')"), {**SCOPE, "id": task_id, "e": f"evt_{task_id}"})
    for task_id, tool in (("task_finance", "finance.summary"), ("task_mail", "summarize_mail"), ("task_shared", "anum.respond")):
        connection.execute(text(
            "insert into agent_run_steps (id, tenant_id, workspace_id, run_id, type, summary, step_metadata) "
            "values (:s, :t, :w, :r, 'tool_proposal', 'Proposed', jsonb_build_object('tool', cast(:tool as text)))"),
            {**SCOPE, "s": f"step_{task_id}", "r": f"run_{task_id}", "tool": tool})
    connection.execute(text(
        "insert into conversations (id, tenant_id, workspace_id, user_id, title) values ('conv_bob', :t, :w, 'bob', 'Chat')"), SCOPE)
    for task_id in ("task_chat", "task_chat_approved"):
        connection.execute(text(
            "insert into conversation_turns (id, tenant_id, workspace_id, user_id, conversation_id, role, content, status, card) "
            "values (:c, :t, :w, 'bob', 'conv_bob', 'assistant', 'Done', 'complete', jsonb_build_object('task_id', cast(:id as text)))"),
            {**SCOPE, "c": f"turn_{task_id}", "id": task_id})
    connection.execute(text(
        "insert into approvals (id, tenant_id, workspace_id, task_id, action, risk_level, status, reason, arguments) "
        "values ('approval_chat', :t, :w, 'task_chat_approved', 'send_email', 'high', 'approved', 'Send it', '{}')"), SCOPE)
    connection.execute(text(
        "insert into automation_workflows (id, tenant_id, workspace_id, name, description, steps, status, version, created_by, overlap_policy, "
        "created_at, updated_at) values ('wf_1', :t, :w, 'Daily', '', '[]', 'active', 4, 'alice', 'skip', now(), now())"), SCOPE)
    connection.execute(text(
        "insert into automation_schedules (id, tenant_id, workspace_id, workflow_id, name, cron, timezone, enabled, created_by, created_at, updated_at) "
        "values ('sched_1', :t, :w, 'wf_1', 'Every morning', '0 8 * * *', 'Asia/Dubai', true, 'alice', now(), now())"), SCOPE)
    connection.execute(text(
        "insert into automation_triggers (id, tenant_id, workspace_id, workflow_id, name, kind, event_type, conditions, enabled, "
        "rate_limit_per_minute, replay_window_seconds, fire_count, created_by, created_at, updated_at) "
        "values ('trig_1', :t, :w, 'wf_1', 'On task', 'event', 'task.completed', '{}', true, 10, 300, 0, 'alice', now(), now())"), SCOPE)


def _backfilled(connection: Connection) -> dict[str, object]:
    tasks = connection.execute(text("select id, private_to, private_kind from tasks order by id")).all()
    events = connection.execute(text("select id, private_to from domain_events order by id")).all()
    return {
        "tasks": {row.id: (row.private_to, row.private_kind) for row in tasks},
        "events": {row.id: row.private_to for row in events},
        "schedule": connection.execute(text("select accepted_version from automation_schedules")).scalar_one(),
        "trigger": connection.execute(text("select accepted_version from automation_triggers")).scalar_one(),
    }


EXPECTED = {
    "tasks": {
        "task_chat": ("bob", "chat"),
        "task_chat_approved": (None, None),
        "task_finance": ("alice", "data"),
        "task_mail": ("alice", "data"),
        "task_shared": (None, None),
    },
    "events": {
        "evt_task_chat": "bob",
        "evt_task_chat_approved": None,
        "evt_task_finance": "alice",
        "evt_task_mail": "alice",
        "evt_task_shared": None,
    },
    "schedule": 4,
    "trigger": 4,
}


def test_the_backfill_happens_for_a_non_superuser_owner_under_forced_rls(database_engine: Engine, clean_database: None) -> None:
    connection = database_engine.connect()
    transaction = connection.begin()
    try:
        _run(connection, "downgrade")  # the schema as 0162 left it
        _seed_at_0162(connection)
        exists = connection.execute(text("select 1 from pg_roles where rolname = :r"), {"r": OWNER_ROLE}).first()
        if not exists:
            connection.execute(text(f"create role {OWNER_ROLE} nologin nosuperuser nobypassrls"))
        connection.execute(text(f"grant {OWNER_ROLE} to current_user"))
        connection.execute(text(f"grant usage on schema public to {OWNER_ROLE}"))
        for table in TABLES:
            connection.execute(text(f"alter table {table} owner to {OWNER_ROLE}"))
        connection.execute(text(f"set local role {OWNER_ROLE}"))
        # Forced RLS and no tenant context: the owner sees no row of the tables the backfill reads.
        for table in ("agent_run_steps", "conversation_turns", "automation_workflows", "approvals"):
            assert connection.execute(text(f"select count(*) from {table}")).scalar_one() == 0, table  # nosec B608 - fixed names
        _run(connection, "upgrade")
        connection.execute(text("reset role"))
        assert _backfilled(connection) == EXPECTED
        # Forced RLS is back on every table the backfill touched.
        for table in TABLES:
            forced = connection.execute(text(
                "select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            assert forced, table
        assert connection.execute(text("select count(*) from pg_policies where policyname like 'private_task_%'")).scalar_one() == 5
    finally:
        transaction.rollback()  # ownership, the seed and the schema change go back
        connection.close()
