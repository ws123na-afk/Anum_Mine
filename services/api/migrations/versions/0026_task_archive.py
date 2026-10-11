"""Task archive columns and keyset paging of a task's runs (docs/tasks.md).

* ``tasks.archived_at`` / ``tasks.archived_by_user_id``: nullable, so the previous
  release (which never writes them) keeps working and lists every task as before.
  ``POST /api/v1/tasks/{id}/archive`` sets them; the default task list hides archived
  rows. Archiving never deletes runs, approvals or events.
* ``ix_agent_runs_task_created`` serves ``GET /api/v1/tasks/{id}/runs`` newest first
  (``where (created_at, id) < (...) order by created_at desc, id desc``). Built
  ``CONCURRENTLY`` outside the migration transaction so writes continue.

Expand only. The tasks table keeps its forced row-level security; new columns are
covered by the existing tenant/workspace policies and table grants.
"""

import sqlalchemy as sa
from alembic import op

revision = "0026_task_archive"
down_revision = "0025_user_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("archived_by_user_id", sa.String(length=120), nullable=True))
    with op.get_context().autocommit_block():
        op.execute(
            "create index concurrently if not exists ix_agent_runs_task_created "
            "on agent_runs (tenant_id, workspace_id, task_id, created_at desc, id desc)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("drop index concurrently if exists ix_agent_runs_task_created")
    op.drop_column("tasks", "archived_by_user_id")
    op.drop_column("tasks", "archived_at")
