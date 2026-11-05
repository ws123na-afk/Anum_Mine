"""Attendance daily reports gain a submitted time (expand only). docs/attendance.md#records.

* ``attendance_daily_reports.submitted_at``: set once when the person submits the day's report from
  the web (``PUT /daily-reports/{day}`` with ``submit: true``). Empty means a draft. Later edits keep
  it, so a report never goes back to draft.

The existing forced RLS policies, grants and purge cover the new column: it is on the same rows,
written only by the owner and read by the owner and a viewer whose active share includes reports.

Expand only: one new nullable column. The previous release never writes it and ignores it, so every
report it saves stays a draft. Chained after ``0138_books_v2``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0139_attendance_report_submit"
down_revision = "0138_books_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("attendance_daily_reports", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("attendance_daily_reports", "submitted_at")
