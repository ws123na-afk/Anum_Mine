"""Push devices remember the app's language (docs/notifications.md#phone-push).

``push_devices.language`` (``en`` or ``ar``, nullable): the phone sends the language its app
shows when it registers, and pushes to it use that language's fixed text. Older rows and
older clients leave it empty and get English, as before.

Expand only: one nullable column with a check; the previous release ignores it.

Chained after ``0065_finance_intel``.
"""

from alembic import op
import sqlalchemy as sa

revision = "0070_push_device_language"
down_revision = "0065_finance_intel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("push_devices", sa.Column("language", sa.String(length=8), nullable=True))
    op.create_check_constraint("ck_push_devices_language", "push_devices", "language is null or language in ('en', 'ar')")


def downgrade() -> None:
    op.drop_constraint("ck_push_devices_language", "push_devices", type_="check")
    op.drop_column("push_devices", "language")
