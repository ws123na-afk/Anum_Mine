"""Wider department set for rooms and a room link on meetings (expand only).

* ``office_rooms.team_department`` may now also hold marketing, technology, research, legal, compliance,
  security, hr and procurement. The check constraint is only widened: every value the previous release
  writes is still valid, so a rollback of the application keeps working against this schema.
* ``agent_meeting_sessions.room_id`` is a nullable link from a live meeting to the office room it is held
  in. It is a composite foreign key on (tenant, workspace, room) so a room of another workspace or tenant
  can never be linked; deleting a room clears only ``room_id`` (``ON DELETE SET NULL (room_id)``, PostgreSQL
  15 or newer) and leaves the meeting and its transcript untouched. Existing meetings keep ``room_id`` null.
  No new table: the forced row level security and the purge policies of 0098 already cover the column.
"""
import sqlalchemy as sa
from alembic import op

revision = "0108_departments_meeting_room"
down_revision = "0107_agent_meeting_budget"
branch_labels = None
depends_on = None

OLD = "'sales','support','operations','finance','tyre_engineering','custom'"
NEW = ("'sales','support','operations','finance','tyre_engineering','marketing','technology','research',"
       "'legal','compliance','security','hr','procurement','custom'")


def upgrade() -> None:
    op.drop_constraint("ck_office_room_department", "office_rooms", type_="check")
    op.create_check_constraint(
        "ck_office_room_department", "office_rooms", f"team_department is null or team_department in ({NEW})",
    )
    op.add_column("agent_meeting_sessions", sa.Column("room_id", sa.String(80), nullable=True))
    op.execute(
        "alter table agent_meeting_sessions add constraint fk_agent_meeting_sessions_room "
        "foreign key (tenant_id, workspace_id, room_id) "
        "references office_rooms (tenant_id, workspace_id, id) on delete set null (room_id)"
    )
    op.create_index(
        "ix_agent_meeting_sessions_room", "agent_meeting_sessions", ["tenant_id", "workspace_id", "room_id"],
        postgresql_where=sa.text("room_id is not null"),
    )


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.drop_index("ix_agent_meeting_sessions_room", table_name="agent_meeting_sessions")
    op.drop_constraint("fk_agent_meeting_sessions_room", "agent_meeting_sessions", type_="foreignkey")
    op.drop_column("agent_meeting_sessions", "room_id")
    op.execute(
        "update office_rooms set team_department = 'custom' where team_department not in "
        "('sales','support','operations','finance','tyre_engineering','custom')"
    )
    op.drop_constraint("ck_office_room_department", "office_rooms", type_="check")
    op.create_check_constraint(
        "ck_office_room_department", "office_rooms", f"team_department is null or team_department in ({OLD})",
    )
