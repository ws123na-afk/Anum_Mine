"""Private tasks, and the workflow version a schedule or trigger was accepted at (expand only).

docs/tasks.md#private-tasks, docs/automation.md#accepted-version. Security audit, November 2026.

* ``tasks`` gains ``private_to`` (the one user who may see the task, its runs, approvals and events; null for a workspace
  task) and ``private_kind`` (``data``: a run read that person's own data, permanent; ``chat``: the task came from their
  private chat). Both nullable and set together.
* ``domain_events`` gains ``private_to``: an event of a private task, copied from the task when the event is recorded and
  whenever the task's marker changes. The outbox relay cannot see such a row, so it is never published.
* Restrictive row policies, next to the tenant policies that stay as they are (forced RLS is unchanged):
  ``private_task_tasks`` and ``private_task_domain_events`` on ``private_to is null or private_to = anum.user_id``;
  ``private_task_agent_runs`` and ``private_task_approvals`` (the row's task must be visible) and
  ``private_task_agent_run_steps`` (the step's run must be visible). The tenant purge role is admitted everywhere (it
  only deletes); the maintenance and platform reader roles keep what they could read on ``tasks`` and ``approvals``
  (scope ids, status and timestamps through their column grants, never a title, a prompt or a result). For the policy
  sub-selects those roles get ``select`` on the id columns named below and nothing else. A session that sets no
  ``anum.user_id`` sees no private row.
* Backfill, conservative: a task named by a chat card (``conversation_turns.card ->> 'task_id'``) becomes ``chat`` private
  to the chat's person, unless one of its runs waited for an approval (those were shared with approvers and stay
  shared); a task whose run proposed a tool that reads the caller's own data (``finance.*`` and the own-mailbox reading
  tools, from ``agent_run_steps.step_metadata ->> 'tool'``) becomes ``data`` private to its creator. A task whose run read
  a mailbox connected as private through ``search_email`` or ``read_email`` cannot be told apart from one that read a
  shared mailbox and is not hidden retroactively.
* ``automation_schedules`` and ``automation_triggers`` gain ``accepted_version`` (nullable): the workflow version the
  schedule's or trigger's actor accepted. Backfilled with the workflow's current version, so nothing stops firing at
  the upgrade.

Chained after ``0162_event_stream_retention``.

Expand only. The previous release ignores the new columns; while it still serves, a task it creates is a workspace
task and a private task it cannot see stays hidden from it too (its sessions set no ``anum.user_id`` for tasks), which
is the safe direction. Rollback: ``alembic downgrade 0162_event_stream_retention`` drops the policies, the grants and the
columns, which makes every private task a workspace task again: only for a test database; production rolls back the
application and keeps the schema.
"""

import sqlalchemy as sa
from alembic import op

revision = "0163_private_tasks"
down_revision = "0162_event_stream_retention"
branch_labels = None
depends_on = None

ME = "nullif(current_setting('anum.user_id', true), '')"
MINE = f"private_to is null or private_to = {ME}"
PURGER = "current_user = 'anum_tenant_purger'::name"
# Roles that read only scope ids, status and timestamps of these tables through column grants.
COUNTERS = "current_user = any (array['anum_tenant_purger', 'anum_maintenance', 'anum_platform_reader']::name[])"
TASK_VISIBLE = (
    "exists (select 1 from tasks t where t.tenant_id = {table}.tenant_id "
    "and t.workspace_id = {table}.workspace_id and t.id = {table}.task_id)"
)
RUN_VISIBLE = (
    "exists (select 1 from agent_runs r where r.tenant_id = agent_run_steps.tenant_id "
    "and r.workspace_id = agent_run_steps.workspace_id and r.id = agent_run_steps.run_id)"
)
# Tools that read the caller's own data (ToolDefinition.caller_scoped at this revision).
CALLER_SCOPED = "tool like 'finance.%' or tool in ('summarize_mail', 'classify_mail', 'classify_mail_page', 'request_mail_action', 'draft_mail_reply')"
BACKFILLED = ("tasks", "domain_events", "automation_schedules", "automation_triggers")


def upgrade() -> None:
    op.add_column("tasks", sa.Column("private_to", sa.String(160), nullable=True))
    op.add_column("tasks", sa.Column("private_kind", sa.String(10), nullable=True))
    op.create_check_constraint("ck_tasks_private_kind", "tasks", "private_kind is null or private_kind in ('chat', 'data')")
    op.create_check_constraint("ck_tasks_private_pair", "tasks", "(private_to is null) = (private_kind is null)")
    op.add_column("domain_events", sa.Column("private_to", sa.String(160), nullable=True))
    op.add_column("automation_schedules", sa.Column("accepted_version", sa.Integer(), nullable=True))
    op.add_column("automation_triggers", sa.Column("accepted_version", sa.Integer(), nullable=True))

    # RLS is forced on these tables even for their owner: lift FORCE for the backfill only.
    for table in BACKFILLED:
        op.execute(f"alter table {table} no force row level security")
    op.execute(
        f"""
        update tasks t set private_to = t.created_by_user_id, private_kind = 'data'
        where exists (
            select 1 from agent_runs r
            join agent_run_steps s on s.tenant_id = r.tenant_id and s.workspace_id = r.workspace_id and s.run_id = r.id
            cross join lateral (select s.step_metadata ->> 'tool' as tool) named
            where r.tenant_id = t.tenant_id and r.workspace_id = t.workspace_id and r.task_id = t.id
              and s.type = 'tool_proposal' and ({CALLER_SCOPED})
        )
        """
    )
    op.execute(
        """
        update tasks t set private_to = c.user_id, private_kind = 'chat'
        from conversation_turns c
        where c.tenant_id = t.tenant_id and c.workspace_id = t.workspace_id and c.card ->> 'task_id' = t.id
          and c.user_id = t.created_by_user_id and t.private_to is null
          and not exists (
              select 1 from approvals a
              where a.tenant_id = t.tenant_id and a.workspace_id = t.workspace_id and a.task_id = t.id
          )
        """
    )
    op.execute(
        """
        update domain_events e set private_to = t.private_to
        from tasks t
        where t.private_to is not null and e.tenant_id = t.tenant_id and e.workspace_id = t.workspace_id
          and e.correlation_id = t.id
        """
    )
    for table in ("automation_schedules", "automation_triggers"):
        op.execute(
            f"""
            update {table} x set accepted_version = w.version
            from automation_workflows w
            where w.tenant_id = x.tenant_id and w.workspace_id = x.workspace_id and w.id = x.workflow_id
            """
        )
    for table in BACKFILLED:
        op.execute(f"alter table {table} force row level security")

    # The policy sub-selects are checked with the querying role's privileges: ids only.
    op.execute("grant select (id) on tasks to anum_tenant_purger")
    op.execute("grant select (id) on agent_runs to anum_tenant_purger")
    op.execute("grant select (id, workspace_id) on tasks to anum_maintenance")

    op.execute(f"create policy private_task_tasks on tasks as restrictive using (({MINE}) or ({COUNTERS})) with check ({MINE})")
    op.execute(
        f"create policy private_task_domain_events on domain_events as restrictive "
        f"using (({MINE}) or ({PURGER})) with check ({MINE})"
    )
    for table in ("agent_runs", "approvals"):
        visible = TASK_VISIBLE.format(table=table)
        admitted = COUNTERS if table == "approvals" else PURGER
        op.execute(
            f"create policy private_task_{table} on {table} as restrictive "
            f"using (({admitted}) or {visible}) with check ({visible})"
        )
    op.execute(
        f"create policy private_task_agent_run_steps on agent_run_steps as restrictive "
        f"using (({PURGER}) or {RUN_VISIBLE}) with check ({RUN_VISIBLE})"
    )


def downgrade() -> None:
    # Only for a test database: every private task becomes a workspace task again.
    for table in ("agent_run_steps", "approvals", "agent_runs", "domain_events", "tasks"):
        op.execute(f"drop policy if exists private_task_{table} on {table}")
    op.execute("revoke select (id, workspace_id) on tasks from anum_maintenance")
    op.execute("revoke select (id) on agent_runs from anum_tenant_purger")
    op.execute("revoke select (id) on tasks from anum_tenant_purger")
    op.drop_column("automation_triggers", "accepted_version")
    op.drop_column("automation_schedules", "accepted_version")
    op.drop_column("domain_events", "private_to")
    op.drop_constraint("ck_tasks_private_pair", "tasks", type_="check")
    op.drop_constraint("ck_tasks_private_kind", "tasks", type_="check")
    op.drop_column("tasks", "private_to")
    op.drop_column("tasks", "private_kind")
