"""Avatar style on agent profiles and team member profiles (expand only).

Adds one nullable JSONB column ``avatar`` to ``agent_profiles`` and to ``team_member_profiles``. It holds the
visual style the owner chose for the headquarters scene (build, skin tone, hair, outfit, accent colour) and
nothing else: no name, no description of the real person. NULL means "the neutral default derived from the
id", so every existing row stays valid and the previous release, which never reads the column, keeps working
(rollback safe). Both tables already have forced row level security on the tenant and workspace (and, for a
private agent, the owner), so the new column is covered by those policies and no role gains anything.

The shape is validated by the API (``anum_api/avatar_appearance.py``); the database only checks that a stored
value is a JSON object.

Chained after ``0109_staff_desks``; numbers are renumbered at merge when parallel branches collide.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0110_avatar_appearance"
down_revision = "0109_staff_desks"
branch_labels = None
depends_on = None

TABLES = ("agent_profiles", "team_member_profiles")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("avatar", postgresql.JSONB(), nullable=True))
        op.create_check_constraint(f"ck_{table}_avatar_object", table, "avatar is null or jsonb_typeof(avatar) = 'object'")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_constraint(f"ck_{table}_avatar_object", table, type_="check")
        op.drop_column(table, "avatar")
