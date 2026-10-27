"""Desk seats, shifts and shift hours for the office registry (expand only).

Adds nullable columns, nothing else:

* ``office_desks.shift`` ('day' or 'night'), ``seat_order`` (place along a table group, 1 is the left end),
  ``facing`` (the wall the seated person looks at), ``seat_kind`` ('team' or 'manager') and ``table_group`` (desks
  with the same group are drawn as one table, for example an L shaped table).
* ``office_buildings.shift_day_start`` and ``shift_night_start`` ("HH:MM", read in the building's own time zone).

NULL everywhere means "not a shift seat" and "the default hours": every existing row stays valid and the previous
release, which never reads the columns, keeps working (rollback safe). No new table, so the forced row level security
and the purge policies of 0097 already cover the new columns. Nothing is backfilled: a shift, a seat and the hours are
choices the owner (or the owner-triggered "Add Green Concrete" action) makes.
"""
import sqlalchemy as sa
from alembic import op

revision = "0115_desk_seats_shifts"
down_revision = "0114_building_floor_name_ar"
branch_labels = None
depends_on = None

HOUR = "^([01][0-9]|2[0-3]):[0-5][0-9]$"


def upgrade() -> None:
    op.add_column("office_desks", sa.Column("shift", sa.String(8), nullable=True))
    op.add_column("office_desks", sa.Column("seat_order", sa.Integer(), nullable=True))
    op.add_column("office_desks", sa.Column("facing", sa.String(5), nullable=True))
    op.add_column("office_desks", sa.Column("seat_kind", sa.String(8), nullable=True))
    op.add_column("office_desks", sa.Column("table_group", sa.String(40), nullable=True))
    op.create_check_constraint("ck_office_desk_shift", "office_desks", "shift is null or shift in ('day', 'night')")
    op.create_check_constraint("ck_office_desk_seat_order", "office_desks", "seat_order is null or (seat_order >= 1 and seat_order <= 999)")
    op.create_check_constraint("ck_office_desk_facing", "office_desks", "facing is null or facing in ('north', 'south', 'east', 'west')")
    op.create_check_constraint("ck_office_desk_seat_kind", "office_desks", "seat_kind is null or seat_kind in ('team', 'manager')")
    op.add_column("office_buildings", sa.Column("shift_day_start", sa.String(5), nullable=True))
    op.add_column("office_buildings", sa.Column("shift_night_start", sa.String(5), nullable=True))
    op.create_check_constraint("ck_office_building_day_start", "office_buildings", f"shift_day_start is null or shift_day_start ~ '{HOUR}'")
    op.create_check_constraint("ck_office_building_night_start", "office_buildings", f"shift_night_start is null or shift_night_start ~ '{HOUR}'")


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.drop_constraint("ck_office_building_night_start", "office_buildings", type_="check")
    op.drop_constraint("ck_office_building_day_start", "office_buildings", type_="check")
    op.drop_column("office_buildings", "shift_night_start")
    op.drop_column("office_buildings", "shift_day_start")
    for name in ("ck_office_desk_seat_kind", "ck_office_desk_facing", "ck_office_desk_seat_order", "ck_office_desk_shift"):
        op.drop_constraint(name, "office_desks", type_="check")
    for column in ("table_group", "seat_kind", "facing", "seat_order", "shift"):
        op.drop_column("office_desks", column)
