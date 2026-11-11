"""Workspace file folders and tags (docs/files.md#folders, docs/files.md#tags).

``workspace_file_folders`` is a tree per workspace: ``name`` and ``parent_id`` (null at the root);
the API computes depth and path from the tree, which is capped per workspace. Two folders at one level cannot share a name (a unique index on the scope,
``coalesce(parent_id, '')`` and ``lower(name)``), a folder's parent is in the same tenant and
workspace (a scoped foreign key onto the table's own ``(tenant_id, workspace_id, id)``), and
deleting a parent deletes its subfolders (``ON DELETE CASCADE``; the API deals with the
contents first, docs/files.md#folders).

``workspace_files`` gains ``folder_id`` (null: the root), a scoped foreign key onto the folders
with ``ON DELETE SET NULL (folder_id)`` as a safety net, and a concurrently built index on the scope, the
folder and the id for paging within a folder.

``workspace_file_tags`` holds the workspace's tag names (unique per workspace, case folded) and
``workspace_file_tag_links`` the many-to-many to files: the primary key is the scope, the file
and the tag, so a file cannot carry a tag twice, and both references are scoped and cascade,
so deleting a file or a tag removes its links.

Every new table is under forced RLS on ``anum.tenant_id`` and ``anum.workspace_id`` like
``workspace_files`` (``tenant_isolation_<table>``), and the organisation and workspace purge
(docs/organisation-lifecycle.md) may delete its rows: the ``anum_tenant_purger`` role gets
``DELETE`` and ``SELECT`` on the scope columns with the purge policy ``lifecycle_purge_<table>``
(0079's ``_purge_policy``). No discovery role reads the tables.

Expand only: three new tables, one nullable column and two concurrently built indexes. The
previous release ignores all of them (its uploads land at the root and its deletes cascade the
links). Rollback: ``alembic downgrade 0167_calendar_recurrence``.

Chained after ``0167_calendar_recurrence``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0168_files_folders_tags"
down_revision = "0167_calendar_recurrence"
branch_labels = None
depends_on = None

FOLDERS = "workspace_file_folders"
TAGS = "workspace_file_tags"
LINKS = "workspace_file_tag_links"
PURGER = "anum_tenant_purger"
JOBS = "tenant_lifecycle_jobs"
TS = sa.DateTime(timezone=True)
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)


def upgrade() -> None:
    op.create_table(
        FOLDERS,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("parent_id", sa.String(80), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_by", sa.String(160), nullable=True),
        sa.Column("updated_at", TS, nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{FOLDERS}_workspace"
        ),
        sa.UniqueConstraint("tenant_id", "workspace_id", "id", name=f"uq_{FOLDERS}_scope_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "parent_id"],
            [f"{FOLDERS}.tenant_id", f"{FOLDERS}.workspace_id", f"{FOLDERS}.id"],
            name=f"fk_{FOLDERS}_parent",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("parent_id is null or parent_id <> id", name=f"ck_{FOLDERS}_not_own_parent"),
    )
    # Two folders at one level never share a name (case folded); the root is parent ''.
    op.execute(
        f"create unique index uq_{FOLDERS}_name on {FOLDERS} "
        "(tenant_id, workspace_id, coalesce(parent_id, ''), lower(name))"
    )
    op.create_index(f"ix_{FOLDERS}_parent", FOLDERS, ["tenant_id", "workspace_id", "parent_id"])
    _isolate(FOLDERS)
    _purge_policy(FOLDERS)

    op.create_table(
        TAGS,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{TAGS}_workspace"
        ),
        sa.UniqueConstraint("tenant_id", "workspace_id", "id", name=f"uq_{TAGS}_scope_id"),
    )
    op.execute(f"create unique index uq_{TAGS}_name on {TAGS} (tenant_id, workspace_id, lower(name))")
    _isolate(TAGS)
    _purge_policy(TAGS)

    op.create_table(
        LINKS,
        sa.Column("tenant_id", sa.String(80), primary_key=True),
        sa.Column("workspace_id", sa.String(80), primary_key=True),
        sa.Column("file_id", sa.String(80), primary_key=True),
        sa.Column("tag_id", sa.String(80), primary_key=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{LINKS}_workspace"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "file_id"],
            ["workspace_files.tenant_id", "workspace_files.workspace_id", "workspace_files.id"],
            name=f"fk_{LINKS}_file",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "tag_id"],
            [f"{TAGS}.tenant_id", f"{TAGS}.workspace_id", f"{TAGS}.id"],
            name=f"fk_{LINKS}_tag",
            ondelete="CASCADE",
        ),
    )
    op.create_index(f"ix_{LINKS}_tag", LINKS, ["tenant_id", "workspace_id", "tag_id", "file_id"])
    _isolate(LINKS)
    _purge_policy(LINKS)

    op.add_column("workspace_files", sa.Column("folder_id", sa.String(80), nullable=True))
    op.create_foreign_key(
        "fk_workspace_files_folder",
        "workspace_files",
        FOLDERS,
        ["tenant_id", "workspace_id", "folder_id"],
        ["tenant_id", "workspace_id", "id"],
        # PostgreSQL 15+: only the folder column is cleared, never the scope columns.
        ondelete="SET NULL (folder_id)",
    )
    # Built concurrently, so uploads keep working while a large table is indexed.
    with op.get_context().autocommit_block():
        op.execute(
            "create index concurrently if not exists ix_workspace_files_scope_folder "
            "on workspace_files (tenant_id, workspace_id, folder_id, id)"
        )


def _isolate(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_{table} on {table}
        using (
          {PREDICATE}
        )
        with check (
          {PREDICATE}
        )
        """  # nosec B608 - fixed table name
    )


def _purge_policy(table: str) -> None:
    """Deletion of the organisation or of one workspace may remove these rows (0079's ``_purge_policy``)."""
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to {PURGER}")
    op.execute(
        f"""
        create policy lifecycle_purge_{table} on {table}
        for all to {PURGER}
        using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and exists (
            select 1 from {JOBS} j
            where j.tenant_id = {table}.tenant_id and j.status = 'purging'
              and (j.kind = 'organisation_deletion'
                   or (j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id))
          )
        )
        with check (false)
        """  # nosec B608 - fixed table name
    )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("drop index concurrently if exists ix_workspace_files_scope_folder")
    op.drop_constraint("fk_workspace_files_folder", "workspace_files", type_="foreignkey")
    op.drop_column("workspace_files", "folder_id")
    for table in (LINKS, TAGS, FOLDERS):
        op.execute(f"drop policy if exists lifecycle_purge_{table} on {table}")
        op.drop_table(table)
