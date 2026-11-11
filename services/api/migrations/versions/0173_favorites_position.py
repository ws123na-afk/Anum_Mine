"""A favorite's place in its owner's own order (expand only).

docs/knowledge-base.md#favorites.

* ``favorites`` gains ``position`` (integer, null): where the person put the item when they
  arranged their favorites by hand (drag, or Move up and Move down). ``GET /api/v1/favorites?sort=custom``
  orders by it (rows without one last, newest first), and ``POST /api/v1/favorites/{id}/move``
  renumbers the caller's own rows. The row keeps its forced RLS on tenant, workspace and user
  (``0042_library``); nothing else is granted.

Chained after ``0172_commerce_money_numeric``.

Expand only. The previous release never reads or writes the column, so its stars keep working and
simply land without a place (they show after the arranged ones). Rollback:
``alembic downgrade 0172_commerce_money_numeric`` drops the column.
"""

import sqlalchemy as sa
from alembic import op

revision = "0173_favorites_position"
down_revision = "0172_commerce_money_numeric"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("favorites", sa.Column("position", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("favorites", "position")
