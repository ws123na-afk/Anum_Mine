"""When an owner removed an agent from a live meeting (expand only).

People leave a meeting through the room's attendance (``agent_meeting_attendance.left_at``, 0104). Nothing recorded an AI
agent leaving: an agent stayed a participant, spoke and was seated in the headquarters until the meeting ended. Now the
meeting owner can remove an agent from an active or paused meeting (``POST /api/v1/agent-meetings/{id}/agents/{agent}/remove``,
docs/ANUM_MINE_LIVE_MEETING_ROOM_UI.md#removing-an-agent), and the participant row keeps when and by whom:

* ``agent_meeting_participants.left_at``: the server time of the removal; null while the agent takes part.
* ``agent_meeting_participants.removed_by``: the owner who removed it; set exactly when ``left_at`` is.

Only an agent participant can have them (a person leaves through the attendance). The row itself stays, so the
transcript's speaker ids keep their participant. Two nullable columns on a table that already has forced row level
security and the tenant purge policy (0098): no new policy is needed. The previous release never reads the columns
and keeps working against this schema (rollback safe). Nothing is backfilled: no earlier leave was ever recorded.
"""
import sqlalchemy as sa
from alembic import op

revision = "0121_meeting_agent_leave"
down_revision = "0120_record_retention"
branch_labels = None
depends_on = None

TABLE = "agent_meeting_participants"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("left_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(TABLE, sa.Column("removed_by", sa.String(160), nullable=True))
    op.create_check_constraint(
        "ck_agent_meeting_participant_left", TABLE,
        "(left_at is null and removed_by is null) or (kind = 'agent' and left_at is not null and removed_by is not null)",
    )


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.drop_constraint("ck_agent_meeting_participant_left", TABLE, type_="check")
    op.drop_column(TABLE, "removed_by")
    op.drop_column(TABLE, "left_at")
