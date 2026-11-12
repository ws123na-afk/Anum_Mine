"""Calendar sync: the account's calendars and which of them sync (expand only).

docs/calendar.md#choosing-calendars.

* ``calendar_connections`` gains ``calendars`` (jsonb, not null, default ``[]``): the connected account's
  calendars as last listed by the provider (id, name, primary, can_edit, color) with the person's
  choice (``selected``). The primary calendar always syncs both ways; the other chosen calendars are
  read only, their events kept as copies. The row keeps its forced tenant and workspace RLS of
  ``0092_calendar_sync``; no grant changes (the scheduler's column grant to ``anum_maintenance`` does not
  need the new column).

Chained after ``0182_command_sweep``.

Expand only. The previous release never reads or writes the column (the default fills it), so for it the
primary calendar syncs as before. Rollback: ``alembic downgrade 0182_command_sweep`` drops it.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0183_calendar_choices"
down_revision = "0182_command_sweep"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calendar_connections",
        sa.Column("calendars", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("calendar_connections", "calendars")
