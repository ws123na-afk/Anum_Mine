"""A calendar-only email switch and the language of calendar reminder emails (expand only).

docs/calendar.md#reminders, docs/notifications.md.

* ``notification_preferences`` gains ``calendar_email`` (boolean, not null, default true): whether
  this person's calendar reminders also go by email (on top of the general Email switch), and
  ``calendar_email_language`` (``en`` or ``ar``, null): the language those emails are written in
  (null = English followed by Arabic, as before). The row keeps its forced RLS on tenant,
  workspace and user; nothing else is granted.

Chained after ``0173_favorites_position``.

Expand only. The previous release never reads or writes the columns: its inserts get the server
default (on) and no language, so reminder emails behave as before. Rollback:
``alembic downgrade 0173_favorites_position`` drops the columns.
"""

import sqlalchemy as sa
from alembic import op

revision = "0174_calendar_email_prefs"
down_revision = "0173_favorites_position"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_preferences",
        sa.Column("calendar_email", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("notification_preferences", sa.Column("calendar_email_language", sa.String(2), nullable=True))
    op.create_check_constraint(
        "ck_notification_preferences_calendar_email_language",
        "notification_preferences",
        "calendar_email_language in ('en', 'ar')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_notification_preferences_calendar_email_language", "notification_preferences", type_="check")
    op.drop_column("notification_preferences", "calendar_email_language")
    op.drop_column("notification_preferences", "calendar_email")
