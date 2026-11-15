"""The command sweep: waiting steps continue without NATS (expand only). docs/command.md#the-sweep.

A command step that waits for earlier steps is started by the server when they complete
(``anum_api/command_continuation.py``). A run the durable worker finished reached the API only through
NATS, and the in-process queue loses a continuation on a restart, so on a VM without NATS a waiting step
could stay unstarted. The sweep (``anum_api/command_sweep.py``) finds such steps and continues each once.

* ``commands.waiting_task_ids``: a stored generated column, the task ids of the steps that wait for an
  earlier step (``depends_on`` not empty). Ids only, so the discovery role never reads a step's text.
* ``command_step_continuations``: one row per waiting step the server tried to continue (the in-process
  continuer or the sweep), whatever the outcome, so a step refused by the budget or the emergency stop is
  never retried on its own. Forced RLS on ``anum.tenant_id`` and ``anum.workspace_id``; the
  ``anum_tenant_purger`` grants and purge policy of ``0079_tenant_lifecycle``; listed in
  ``anum_api/tenant_lifecycle_tables.py``.
* ``anum_maintenance`` (never BYPASSRLS) reads only scope ids, ``created_by``, ``waiting_task_ids`` and
  ``created_at`` of commands that have a waiting step, the scope ids, status and times of tasks that were
  never started, and the scope ids and task id of ``command_step_continuations``. The work then runs as the
  application role in the command creator's own scope, exactly like ``POST /tasks/{id}/run``.

Chained after ``0181_attendance_manager_view``. Expand only: the previous release never names the new
column or table, and the new policies admit only the discovery role. Rollback never reverses it in
production.
"""

import sqlalchemy as sa
from alembic import op

revision = "0182_command_sweep"
down_revision = "0181_attendance_manager_view"
branch_labels = None
depends_on = None

SCOPE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)


def upgrade() -> None:
    op.execute(
        "alter table commands add column waiting_task_ids jsonb generated always as "
        "(jsonb_path_query_array(steps, '$[*] ? (@.depends_on.size() > 0).task_id')) stored"
    )
    op.create_table(
        "command_step_continuations",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("task_id", sa.String(80), nullable=False),
        sa.Column("command_id", sa.String(80), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "task_id", name="pk_command_step_continuations"),
        sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"],
                                name="fk_command_step_continuations_workspace"),
        sa.CheckConstraint("outcome in ('started', 'already_started', 'refused')", name="ck_command_step_continuations_outcome"),
        sa.CheckConstraint("source in ('event', 'sweep')", name="ck_command_step_continuations_source"),
    )
    op.execute("alter table command_step_continuations enable row level security")
    op.execute("alter table command_step_continuations force row level security")
    op.execute(
        f"create policy tenant_isolation_command_step_continuations on command_step_continuations "
        f"using ({SCOPE}) with check ({SCOPE})"
    )
    op.execute("grant select (tenant_id, ctid, workspace_id), delete on command_step_continuations to anum_tenant_purger")
    op.execute(
        """
        create policy lifecycle_purge_command_step_continuations on command_step_continuations
        for all to anum_tenant_purger
        using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and exists (
            select 1 from tenant_lifecycle_jobs j
            where j.tenant_id = command_step_continuations.tenant_id and j.status = 'purging'
              and (j.kind = 'organisation_deletion'
                   or (j.kind = 'workspace_deletion' and j.workspace_id = command_step_continuations.workspace_id))
          )
        )
        with check (false)
        """
    )

    # Discovery as anum_maintenance: ids, status and times only.
    op.execute("grant select (tenant_id, workspace_id, id, created_by, waiting_task_ids, created_at) on commands to anum_maintenance")
    op.execute(
        "create policy maintenance_command_sweep on commands for select to anum_maintenance "
        "using (jsonb_array_length(waiting_task_ids) > 0)"
    )
    op.execute("grant select (tenant_id, status, archived_at, created_at) on tasks to anum_maintenance")
    op.execute("create policy maintenance_command_sweep on tasks for select to anum_maintenance using (status = 'created')")
    op.execute("grant select (tenant_id, workspace_id, task_id) on command_step_continuations to anum_maintenance")
    op.execute(
        "create policy maintenance_command_sweep on command_step_continuations for select to anum_maintenance using (true)"
    )


def downgrade() -> None:
    # Only for an empty test database: production rolls back by restoring a backup.
    op.execute("drop policy if exists maintenance_command_sweep on tasks")
    op.execute("drop policy if exists maintenance_command_sweep on commands")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke select (tenant_id, status, archived_at, created_at) on tasks from anum_maintenance;
            revoke select (tenant_id, workspace_id, id, created_by, waiting_task_ids, created_at) on commands from anum_maintenance;
          end if;
        end
        $$
        """
    )
    op.drop_table("command_step_continuations")
    op.execute("alter table commands drop column waiting_task_ids")
