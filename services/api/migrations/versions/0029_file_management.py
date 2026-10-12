"""Workspace file management (docs/files.md): rename columns and paging indexes.

``workspace_files`` gains ``updated_by`` and ``updated_at`` (the last rename; null for a
file never renamed) and two indexes for keyset paging by name (case-insensitive, in
bytewise ``C`` order, so every database sorts names the same way) and by size within a
workspace. The table keeps its forced tenant and workspace RLS policy from
0008; nothing is granted to any other role.

Expand only: two nullable columns and two concurrently built indexes. The previous
release neither reads nor writes them, and a rename it cannot do leaves them null.

Chained after ``0025_user_preferences``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_file_management"
down_revision = "0028_workspace_admin"
branch_labels = None
depends_on = None

TABLE = "workspace_files"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("updated_by", sa.String(160), nullable=True))
    op.add_column(TABLE, sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    # Built concurrently, so uploads keep working while a large table is indexed.
    with op.get_context().autocommit_block():
        op.execute(
            "create index concurrently if not exists ix_workspace_files_scope_name "
            f'on {TABLE} (tenant_id, workspace_id, (lower(name) collate "C"), id)'
        )
        op.execute(
            "create index concurrently if not exists ix_workspace_files_scope_size "
            f"on {TABLE} (tenant_id, workspace_id, size_bytes, id)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("drop index concurrently if exists ix_workspace_files_scope_size")
        op.execute("drop index concurrently if exists ix_workspace_files_scope_name")
    op.drop_column(TABLE, "updated_at")
    op.drop_column(TABLE, "updated_by")
