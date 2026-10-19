"""Workspace file versions (docs/files.md#versions).

``workspace_files`` gains ``version`` (the current version number, default 1, so every
existing file is version 1 and the previous release, which never writes it, keeps inserting
version 1 rows) and a unique index on ``(tenant_id, workspace_id, id)`` built concurrently, the
target of the scoped foreign key below.

``workspace_file_versions`` holds the earlier versions of a file: the type, size, SHA-256 and
object key of the bytes, who uploaded them and when, who replaced them and when, and their scan
state. The bytes stay in object storage; identical content of one file shares one object (a
restore points at the bytes it restores). The table is under forced RLS on ``anum.tenant_id``
and ``anum.workspace_id`` like ``workspace_files`` (``tenant_isolation_<table>``), and its rows
reference their file with the scope (``(tenant_id, workspace_id, file_id)``), so a version can
never point into another tenant or workspace; deleting a file deletes its versions. No role is
granted anything and no discovery role reads it.

Expand only: one defaulted column, one concurrently built index and one new table. The previous
release ignores all three; a file it deletes takes its versions with it.

Numbered 0085 and chained after ``0080_business_change_orders``, the head of ``main``; renumbered at
merge if other migrations land first.
"""

import sqlalchemy as sa
from alembic import op

revision = "0085_file_versions"
down_revision = "0080_business_change_orders"
branch_labels = None
depends_on = None

TABLE = "workspace_file_versions"
TS = sa.DateTime(timezone=True)
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)


def upgrade() -> None:
    op.add_column("workspace_files", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    # Built concurrently, so uploads keep working while a large table is indexed.
    with op.get_context().autocommit_block():
        op.execute(
            "create unique index concurrently if not exists uq_workspace_files_scope_id "
            "on workspace_files (tenant_id, workspace_id, id)"
        )
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("file_id", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("replaced_by", sa.String(160), nullable=False),
        sa.Column("replaced_at", TS, nullable=False),
        sa.Column("scan_status", sa.String(16), nullable=True),
        sa.Column("scan_signature", sa.String(200), nullable=True),
        sa.Column("scanned_at", TS, nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{TABLE}_workspace"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "file_id"],
            ["workspace_files.tenant_id", "workspace_files.workspace_id", "workspace_files.id"],
            name=f"fk_{TABLE}_file",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "workspace_id", "file_id", "version", name=f"uq_{TABLE}_number"),
        sa.CheckConstraint("size_bytes > 0 and version > 0", name=f"ck_{TABLE}_values"),
        sa.CheckConstraint("scan_status is null or scan_status in ('clean', 'infected')", name=f"ck_{TABLE}_scan_status"),
    )
    op.create_index(f"ix_{TABLE}_sha", TABLE, ["tenant_id", "workspace_id", "file_id", "sha256"])
    op.execute(f"alter table {TABLE} enable row level security")
    op.execute(f"alter table {TABLE} force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_{TABLE} on {TABLE}
        using (
          {PREDICATE}
        )
        with check (
          {PREDICATE}
        )
        """
    )


def downgrade() -> None:
    op.drop_table(TABLE)
    with op.get_context().autocommit_block():
        op.execute("drop index concurrently if exists uq_workspace_files_scope_id")
    op.drop_column("workspace_files", "version")
