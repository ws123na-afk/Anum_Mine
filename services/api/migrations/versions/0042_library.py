"""Favorites, knowledge base collections and reports (docs/knowledge-base.md, docs/reports.md).

* ``favorites``: the items one user starred in one workspace. Private to that user: the
  forced RLS policy checks ``anum.user_id`` as well as the tenant and workspace, like
  conversations.
* ``kb_collections`` and ``kb_items``: knowledge base collections and the documents
  (workspace files) and notes (memories) organised in them. A collection is shared with
  the workspace (``owner_user_id`` is null) or private to its owner; an item copies its
  collection's owner. Tenant and workspace isolation is the permissive policy; a
  ``RESTRICTIVE`` owner policy (the one ``memories`` has since 0021) hides a private
  collection and its items from everyone but the owner.
* ``reports``: generated reports (type, period, metrics, data snapshot, Markdown), shared
  with the workspace or private to their owner under the same two policies.

Nothing reads these tables across tenants, so no other role is granted anything.

Expand only: four new tables; the previous release ignores them.

Chained after ``0044_commands`` (the lead re-chains on landing).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0042_library"
down_revision = "0044_commands"
branch_labels = None
depends_on = None

SCOPE_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
USER_PREDICATE = SCOPE_PREDICATE + "\n          and user_id = nullif(current_setting('anum.user_id', true), '')"
OWNER_PREDICATE = (
    "owner_user_id is null\n"
    "          or owner_user_id = nullif(current_setting('anum.user_id', true), '')"
)
OWNED_TABLES = ("kb_collections", "kb_items", "reports")


def _timestamp(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def _json(name: str, default: str) -> sa.Column:
    return sa.Column(name, postgresql.JSONB(), nullable=False, server_default=sa.text(f"'{default}'::jsonb"))


def _policy(table: str, name: str, predicate: str, *, restrictive: bool = False) -> None:
    kind = "as restrictive\n            " if restrictive else ""
    op.execute(
        f"""
        create policy {name}_{table} on {table}
        {kind}using (
          {predicate}
        )
        with check (
          {predicate}
        )
        """
    )


def upgrade() -> None:
    op.create_table(
        "favorites",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("item_type", sa.String(20), nullable=False),
        sa.Column("item_id", sa.String(200), nullable=False),
        sa.Column("label", sa.String(200), nullable=False, server_default=""),
        _timestamp("created_at"),
        _workspace_fk("favorites"),
        sa.UniqueConstraint(
            "tenant_id", "workspace_id", "user_id", "item_type", "item_id", name="uq_favorites_item"
        ),
        sa.CheckConstraint(
            "item_type in ('conversation', 'task', 'agent', 'file', 'report', 'memory', 'automation', "
            "'collection')",
            name="ck_favorites_item_type",
        ),
    )
    op.create_index("ix_favorites_scope_created", "favorites", ["tenant_id", "workspace_id", "user_id", "created_at"])

    op.create_table(
        "kb_collections",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False, server_default=""),
        sa.Column("owner_user_id", sa.String(160), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _workspace_fk("kb_collections"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "id", name="uq_kb_collections_scope_id"),
    )
    op.create_index("ix_kb_collections_scope", "kb_collections", ["tenant_id", "workspace_id", "updated_at"])

    op.create_table(
        "kb_items",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("collection_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("source_id", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        _json("tags", "[]"),
        sa.Column("created_by", sa.String(160), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "collection_id"],
            ["kb_collections.tenant_id", "kb_collections.workspace_id", "kb_collections.id"],
            name="fk_kb_items_collection",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "workspace_id", "collection_id", "kind", "source_id", name="uq_kb_items_source"
        ),
        sa.CheckConstraint("kind in ('file', 'note')", name="ck_kb_items_kind"),
    )
    op.create_index("ix_kb_items_collection", "kb_items", ["tenant_id", "workspace_id", "collection_id"])

    op.create_table(
        "reports",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("report_type", sa.String(60), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        _json("metrics", "[]"),
        _json("highlights", "[]"),
        _json("data", "{}"),
        sa.Column("body_markdown", sa.Text(), nullable=False, server_default=""),
        sa.Column("owner_user_id", sa.String(160), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_by_kind", sa.String(20), nullable=False),
        sa.Column("source_conversation_id", sa.String(80), nullable=True),
        sa.Column("source_turn_id", sa.String(80), nullable=True),
        sa.Column("source_automation_run_id", sa.String(80), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _workspace_fk("reports"),
        sa.CheckConstraint("created_by_kind in ('user', 'agent', 'automation')", name="ck_reports_created_by_kind"),
        sa.CheckConstraint(
            "period_start is null or period_end is null or period_start <= period_end", name="ck_reports_period"
        ),
    )
    op.create_index("ix_reports_scope_created", "reports", ["tenant_id", "workspace_id", "created_at"])
    op.create_index(
        "ix_reports_conversation",
        "reports",
        ["tenant_id", "workspace_id", "source_conversation_id"],
        postgresql_where=sa.text("source_conversation_id is not null"),
    )

    for table in ("favorites", *OWNED_TABLES):
        op.execute(f"alter table {table} enable row level security")
        op.execute(f"alter table {table} force row level security")
    _policy("favorites", "tenant_isolation", USER_PREDICATE)
    for table in OWNED_TABLES:
        _policy(table, "tenant_isolation", SCOPE_PREDICATE)
        _policy(table, "owner_only", OWNER_PREDICATE, restrictive=True)


def downgrade() -> None:
    op.drop_table("reports")
    op.drop_table("kb_items")
    op.drop_table("kb_collections")
    op.drop_table("favorites")
