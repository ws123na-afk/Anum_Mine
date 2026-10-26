"""Room kind "command" for the operations command centre (expand only).

``office_rooms.kind`` may now also hold ``command``. The check constraint is only widened: every value the
previous release writes is still valid, so a rollback of the application keeps working against this schema.
Existing rooms keep their kind; no data is rewritten. No new table: forced row level security and the purge
policies of 0097 already cover the column.
"""
from alembic import op

revision = "0113_room_kind_command"
down_revision = "0112_layout_history"
branch_labels = None
depends_on = None

OLD = "'office','meeting','reception','workshop','warehouse','lounge'"
NEW = OLD + ",'command'"


def upgrade() -> None:
    op.drop_constraint("ck_office_room_kind", "office_rooms", type_="check")
    op.create_check_constraint("ck_office_room_kind", "office_rooms", f"kind in ({NEW})")


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute("update office_rooms set kind = 'office' where kind = 'command'")
    op.drop_constraint("ck_office_room_kind", "office_rooms", type_="check")
    op.create_check_constraint("ck_office_room_kind", "office_rooms", f"kind in ({OLD})")
