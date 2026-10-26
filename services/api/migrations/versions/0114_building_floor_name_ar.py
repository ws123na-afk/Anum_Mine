"""Arabic names for office buildings and floors (expand only).

Adds a nullable ``name_ar`` to ``office_buildings`` and ``office_floors``, the same column rooms got in 0105.
Existing rows keep NULL: nothing is backfilled here, because an Arabic name is a choice the owner (or the
owner-triggered "Add Arabic names" action) makes. No new table, so the forced row level security and the purge
policies of 0097 already cover the new columns, and the previous release keeps working against this schema.
"""
import sqlalchemy as sa
from alembic import op

revision = "0114_building_floor_name_ar"
down_revision = "0113_room_kind_command"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("office_buildings", sa.Column("name_ar", sa.String(100), nullable=True))
    op.add_column("office_floors", sa.Column("name_ar", sa.String(100), nullable=True))


def downgrade() -> None:
    # Only for an empty test workspace: production rolls back by restoring a backup.
    op.drop_column("office_floors", "name_ar")
    op.drop_column("office_buildings", "name_ar")
