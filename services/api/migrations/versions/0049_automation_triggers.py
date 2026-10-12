"""Automation triggers, waiting steps and retries (docs/automation.md#triggers).

* ``automation_triggers``: event and webhook triggers of a workflow. A webhook's HMAC
  secret is stored as Fernet ciphertext (``ANUM_SECRETS_KEY``) and shown once; event
  triggers keep a ``cursor_at`` so the scheduler pass can catch up on events another
  process committed. Forced workspace RLS like the other automation tables; the
  composite workflow foreign key keeps a trigger in its workflow's workspace.
* ``automation_runs``: ``trigger_id``, ``trigger_type`` and ``trigger_payload`` (what the
  trigger delivered), ``wake_at`` (when a waiting step or a retry is next due) and
  ``waiting_task_id`` (the agent task the current step waits on), with indexes for both.

``anum_maintenance`` may discover, ids and scope only: runs whose ``wake_at`` has passed
and enabled event triggers. The work itself runs as the application role in each
tenant's context.

Expand only: a new table and nullable columns; the previous release ignores them. Run
statuses are unchanged (a waiting step is a running step with ``waiting_for`` in its
JSON state), so the previous release still reads every run.

Chained after ``0039_team_work``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0049_automation_triggers"
down_revision = "0039_team_work"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)


def upgrade() -> None:
    op.create_table(
        "automation_triggers",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("workflow_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("event_type", sa.String(160), nullable=True),
        sa.Column("conditions", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("replay_window_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("secret_hint", sa.String(8), nullable=True),
        sa.Column("secret_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outcome", sa.String(40), nullable=True),
        sa.Column("fire_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name="fk_automation_triggers_workspace"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "workflow_id"],
            ["automation_workflows.tenant_id", "automation_workflows.workspace_id", "automation_workflows.id"],
            name="fk_automation_triggers_workflow",
        ),
        sa.CheckConstraint("kind in ('event', 'webhook')", name="ck_automation_triggers_kind"),
        sa.CheckConstraint(
            "(kind = 'event' and event_type is not null) or (kind = 'webhook' and secret_ciphertext is not null)",
            name="ck_automation_triggers_shape",
        ),
        sa.CheckConstraint("rate_limit_per_minute between 1 and 600", name="ck_automation_triggers_rate"),
        sa.CheckConstraint("replay_window_seconds between 30 and 3600", name="ck_automation_triggers_replay"),
    )
    op.create_index(
        "ix_automation_triggers_event", "automation_triggers", ["tenant_id", "workspace_id", "event_type", "enabled"]
    )
    op.create_index("ix_automation_triggers_workflow", "automation_triggers", ["tenant_id", "workspace_id", "workflow_id"])
    op.execute("alter table automation_triggers enable row level security")
    op.execute("alter table automation_triggers force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_automation_triggers on automation_triggers
        using (
          {PREDICATE}
        )
        with check (
          {PREDICATE}
        )
        """
    )

    op.add_column("automation_runs", sa.Column("trigger_id", sa.String(80), nullable=True))
    op.add_column("automation_runs", sa.Column("trigger_type", sa.String(160), nullable=True))
    op.add_column(
        "automation_runs",
        sa.Column("trigger_payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("automation_runs", sa.Column("wake_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("automation_runs", sa.Column("waiting_task_id", sa.String(80), nullable=True))
    op.create_index(
        "ix_automation_runs_wake", "automation_runs", ["wake_at"], postgresql_where=sa.text("wake_at is not null")
    )
    op.create_index(
        "ix_automation_runs_waiting_task",
        "automation_runs",
        ["tenant_id", "workspace_id", "waiting_task_id"],
        postgresql_where=sa.text("waiting_task_id is not null"),
    )
    op.create_index("ix_automation_runs_trigger", "automation_runs", ["tenant_id", "workspace_id", "trigger_id", "created_at"])

    # Discovery for the scheduler pass: ids and scope only (anum_api.maintenance).
    op.execute("grant select (id, tenant_id, workspace_id, wake_at) on automation_runs to anum_maintenance")
    op.execute("grant select (id, tenant_id, workspace_id, kind, enabled) on automation_triggers to anum_maintenance")
    # Permissive policies are OR-ed per role, so these widen access for this role only.
    op.execute(
        """
        create policy maintenance_due_runs on automation_runs
        for select to anum_maintenance
        using (status = 'running' and wake_at is not null and wake_at <= now())
        """
    )
    op.execute(
        """
        create policy maintenance_event_triggers on automation_triggers
        for select to anum_maintenance
        using (enabled and kind = 'event')
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists maintenance_event_triggers on automation_triggers")
    op.execute("drop policy if exists maintenance_due_runs on automation_runs")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke select (id, tenant_id, workspace_id, wake_at) on automation_runs from anum_maintenance;
          end if;
        end
        $$
        """
    )
    op.drop_index("ix_automation_runs_trigger", table_name="automation_runs")
    op.drop_index("ix_automation_runs_waiting_task", table_name="automation_runs")
    op.drop_index("ix_automation_runs_wake", table_name="automation_runs")
    for column in ("waiting_task_id", "wake_at", "trigger_payload", "trigger_type", "trigger_id"):
        op.drop_column("automation_runs", column)
    op.drop_table("automation_triggers")
