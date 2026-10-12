"""Automation overlap policy, run versions and schedule outcomes (docs/automation.md).

- ``automation_workflows.overlap_policy``: what a new run does while another run of the
  same workflow is still active (``allow``, ``skip`` or ``cancel_previous``). Existing
  workflows keep today's behaviour through the ``allow`` server default.
- ``automation_workflows.updated_by``: who last edited the workflow (null until edited).
- ``automation_runs.workflow_version``: the workflow version a run executed (null for runs
  recorded before this migration).
- ``automation_schedules.last_outcome``: what the scheduler did at the last fire time
  (``fired``, ``skipped_overlap``, ``workflow_paused``, ``owner_inactive``); null until the
  next fire.
- ``ix_automation_runs_workflow``: per-workflow run history, newest first.

Expand only: new nullable or defaulted columns and one index. The previous release never
reads them and its inserts get the defaults, so it keeps running against this schema.

Chained after ``0030_integration_health``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0031_automation_policy"
down_revision = "0030_integration_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "automation_workflows",
        sa.Column("overlap_policy", sa.String(20), nullable=False, server_default="allow"),
    )
    op.create_check_constraint(
        "ck_automation_workflows_overlap_policy",
        "automation_workflows",
        "overlap_policy in ('allow', 'skip', 'cancel_previous')",
    )
    op.add_column("automation_workflows", sa.Column("updated_by", sa.String(160), nullable=True))
    op.add_column("automation_runs", sa.Column("workflow_version", sa.Integer(), nullable=True))
    op.add_column("automation_schedules", sa.Column("last_outcome", sa.String(40), nullable=True))
    # Run history can be large: build the index without blocking writes.
    with op.get_context().autocommit_block():
        op.execute(
            "create index concurrently if not exists ix_automation_runs_workflow "
            "on automation_runs (tenant_id, workspace_id, workflow_id, created_at)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("drop index concurrently if exists ix_automation_runs_workflow")
    op.drop_column("automation_schedules", "last_outcome")
    op.drop_column("automation_runs", "workflow_version")
    op.drop_column("automation_workflows", "updated_by")
    op.drop_constraint("ck_automation_workflows_overlap_policy", "automation_workflows", type_="check")
    op.drop_column("automation_workflows", "overlap_policy")
