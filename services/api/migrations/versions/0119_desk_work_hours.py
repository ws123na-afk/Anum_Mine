"""Working hours of a seat (expand only).

Owner answer of 28 October 2026 (docs/ANUM_MINE_GREEN_CONCRETE.md): every person has their own working hours, not one
day and night pair per building. ``office_desks.work_start`` and ``work_end`` ("HH:MM", read in the building's own time
zone) hold the hours of the person seated at the desk. Both are set or both are null, and they differ; an end before the
start is a window over midnight (22:00 to 06:00).

Null means the desk has no hours of its own: an older Day or Night seat keeps reading the building's ``shift_day_start``
and ``shift_night_start`` (0115) and any other desk is always on. The 0115 columns are kept, so the previous release, which
never reads the new columns, keeps working against this schema (rollback safe). No new table, so the forced row level
security and the purge policies of 0097 already cover the columns. Nothing is backfilled: hours are the owner's choice,
written only by the owner-triggered "Add Green Concrete" action or the builder.
"""
import sqlalchemy as sa
from alembic import op

revision = "0119_desk_work_hours"
down_revision = "0118_career_visitor_register"
branch_labels = None
depends_on = None

HOUR = "^([01][0-9]|2[0-3]):[0-5][0-9]$"


def upgrade() -> None:
    op.add_column("office_desks", sa.Column("work_start", sa.String(5), nullable=True))
    op.add_column("office_desks", sa.Column("work_end", sa.String(5), nullable=True))
    op.create_check_constraint("ck_office_desk_work_start", "office_desks", f"work_start is null or work_start ~ '{HOUR}'")
    op.create_check_constraint("ck_office_desk_work_end", "office_desks", f"work_end is null or work_end ~ '{HOUR}'")
    op.create_check_constraint("ck_office_desk_work_pair", "office_desks",
                               "(work_start is null) = (work_end is null) and (work_start is null or work_start <> work_end)")


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    for name in ("ck_office_desk_work_pair", "ck_office_desk_work_end", "ck_office_desk_work_start"):
        op.drop_constraint(name, "office_desks", type_="check")
    op.drop_column("office_desks", "work_end")
    op.drop_column("office_desks", "work_start")
