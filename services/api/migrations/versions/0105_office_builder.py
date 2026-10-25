"""Office builder: floor bounds, room details and desk positions (expand only).

Adds nullable or defaulted columns only, so the previous release keeps working against
the new schema. Existing rows keep their values: floors without bounds stay unbounded,
rooms get rotation 0 and a south door, desks stay unpositioned. No new table, so the
forced row level security and purge policies of 0097 already cover every column.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0105_office_builder"
down_revision = "0104_meeting_room"
branch_labels = None
depends_on = None

COLORS = "'slate','forest','brass','sapphire','amethyst','crimson','teal','sand'"
DEPARTMENTS = "'sales','support','operations','finance','tyre_engineering','custom'"


def upgrade() -> None:
    op.add_column("office_floors", sa.Column("width_m", sa.Float(), nullable=True))
    op.add_column("office_floors", sa.Column("depth_m", sa.Float(), nullable=True))
    op.create_check_constraint(
        "ck_office_floor_bounds", "office_floors",
        "(width_m is null and depth_m is null) or "
        "(width_m between 1 and 1000 and depth_m between 1 and 1000)",
    )

    op.add_column("office_rooms", sa.Column("rotation", sa.SmallInteger(), nullable=False, server_default="0"))
    op.add_column("office_rooms", sa.Column("door_side", sa.String(5), nullable=False, server_default="south"))
    op.add_column("office_rooms", sa.Column("description", sa.Text(), nullable=False, server_default=""))
    op.add_column("office_rooms", sa.Column("notes", sa.Text(), nullable=False, server_default=""))
    op.add_column("office_rooms", sa.Column("color", sa.String(12), nullable=True))
    op.add_column("office_rooms", sa.Column("name_ar", sa.String(100), nullable=True))
    op.add_column(
        "office_rooms",
        sa.Column("equipment", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("office_rooms", sa.Column("team_department", sa.String(40), nullable=True))
    op.create_check_constraint("ck_office_room_rotation", "office_rooms", "rotation in (0, 90, 180, 270)")
    op.create_check_constraint(
        "ck_office_room_door_side", "office_rooms", "door_side in ('north', 'south', 'east', 'west')"
    )
    op.create_check_constraint(
        "ck_office_room_color", "office_rooms", f"color is null or color in ({COLORS})"
    )
    op.create_check_constraint(
        "ck_office_room_department", "office_rooms",
        f"team_department is null or team_department in ({DEPARTMENTS})",
    )
    op.create_check_constraint(
        "ck_office_room_text_sizes", "office_rooms",
        "char_length(description) <= 500 and char_length(notes) <= 2000",
    )
    op.create_check_constraint(
        "ck_office_room_equipment_array", "office_rooms", "jsonb_typeof(equipment) = 'array'"
    )

    op.add_column("office_desks", sa.Column("pos_x", sa.Float(), nullable=True))
    op.add_column("office_desks", sa.Column("pos_z", sa.Float(), nullable=True))
    op.create_check_constraint(
        "ck_office_desk_position", "office_desks",
        "(pos_x is null and pos_z is null) or "
        "(pos_x between 0 and 1000 and pos_z between 0 and 1000)",
    )


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.drop_constraint("ck_office_desk_position", "office_desks", type_="check")
    op.drop_column("office_desks", "pos_z")
    op.drop_column("office_desks", "pos_x")
    for name in (
        "ck_office_room_equipment_array", "ck_office_room_text_sizes", "ck_office_room_department",
        "ck_office_room_color", "ck_office_room_door_side", "ck_office_room_rotation",
    ):
        op.drop_constraint(name, "office_rooms", type_="check")
    for column in (
        "team_department", "equipment", "name_ar", "color", "notes", "description", "door_side", "rotation"
    ):
        op.drop_column("office_rooms", column)
    op.drop_constraint("ck_office_floor_bounds", "office_floors", type_="check")
    op.drop_column("office_floors", "depth_m")
    op.drop_column("office_floors", "width_m")
