"""Allow the executive department on a room (expand only).

``office_rooms.team_department`` may now also hold ``executive``, so a room whose stored department is
executive opens the executive desk whatever kind of building it is in. The check constraint is only widened:
every value the previous release writes is still valid, so rolling the application back keeps working against
this schema. No data is backfilled and no existing room changes; an owner (or the starter campus confirm
click, which fills only an empty or custom department) sets it. No new table, so the forced row level security
and the purge policies stay as they are.
"""
from alembic import op

revision = "0116_room_dept_executive"
down_revision = "0115_desk_seats_shifts"
branch_labels = None
depends_on = None

OLD = ("'sales','support','operations','finance','tyre_engineering','marketing','technology','research',"
       "'legal','compliance','security','hr','procurement','custom'")
NEW = ("'sales','support','operations','finance','tyre_engineering','marketing','technology','research',"
       "'legal','compliance','security','hr','procurement','executive','custom'")


def _constraint(values: str) -> None:
    op.drop_constraint("ck_office_room_department", "office_rooms", type_="check")
    op.create_check_constraint(
        "ck_office_room_department", "office_rooms", f"team_department is null or team_department in ({values})",
    )


def upgrade() -> None:
    _constraint(NEW)


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.execute("update office_rooms set team_department = 'custom' where team_department = 'executive'")
    _constraint(OLD)
