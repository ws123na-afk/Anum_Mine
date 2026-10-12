"""Task planning fields: priority and due date (docs/tasks.md).

* ``tasks.priority``: ``low``, ``medium`` or ``high``; nullable, so the previous
  release (which never writes it) keeps working, and a null reads as ``medium``.
* ``tasks.due_at``: when the person wants the result; null for no due date.

Neither field changes what an agent may do or when it runs; the task lists show
them and ``PUT /api/v1/tasks/{id}`` edits them.

Expand only. The tasks table keeps its forced row-level security; new columns are
covered by the existing tenant/workspace policies and table grants.
"""

import sqlalchemy as sa
from alembic import op

revision = "0048_task_planning"
down_revision = "0047_chat_cards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("priority", sa.String(length=10), nullable=True))
    op.add_column("tasks", sa.Column("due_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_tasks_priority",
        "tasks",
        "priority is null or priority in ('low', 'medium', 'high')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tasks_priority", "tasks", type_="check")
    op.drop_column("tasks", "due_at")
    op.drop_column("tasks", "priority")
