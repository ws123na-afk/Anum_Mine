"""Retrieval index: chunked, embedded workspace memories and files (threat model G5).

* ``retrieval_sources``: one row per indexed memory note or file with its index state
  (status, error code, chunk count, embedding model, content digest, memory expiry).
  Never the text.
* ``retrieval_chunks``: the chunk text and its pgvector embedding. The column is an
  untyped ``vector`` because embedding models differ in dimensions; ``dimensions``
  and ``embedding_model`` are stored with each chunk and search only compares chunks of
  the query's model and dimension. ``ix_retrieval_chunks_scope_model`` bounds every
  search to one workspace and model (exact search; an ANN index per model is later
  work, docs/memory.md#retrieval). Chunks cascade from their source row.

Both tables force RLS on ``anum.tenant_id`` and ``anum.workspace_id`` like every other
workspace table; no role bypasses it. Expand only: the previous release ignores both
tables, so a rollback of the application keeps working against this schema.
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_retrieval_index"
down_revision = "0013_approvers_and_directory"
branch_labels = None
depends_on = None

WORKSPACE_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)


def _enable_rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_{table} on {table}
        using (
          {WORKSPACE_PREDICATE}
        )
        with check (
          {WORKSPACE_PREDICATE}
        )
        """
    )


def upgrade() -> None:
    # 0001 creates it; repeated here so a database restored without it fails loudly.
    op.execute("create extension if not exists vector")
    op.create_table(
        "retrieval_sources",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.String(80), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding_model", sa.String(200), nullable=True),
        sa.Column("source_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id", "workspace_id", "source_type", "source_id", name="pk_retrieval_sources"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_retrieval_sources_workspace",
        ),
        sa.CheckConstraint("source_type in ('memory', 'file')", name="ck_retrieval_sources_type"),
        sa.CheckConstraint(
            "status in ('indexed', 'failed', 'skipped')", name="ck_retrieval_sources_status"
        ),
        sa.CheckConstraint("chunk_count >= 0", name="ck_retrieval_sources_chunk_count"),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_retrieval_sources_sha256"
        ),
    )
    op.create_table(
        "retrieval_chunks",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_id", sa.String(80), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("embedding_model", sa.String(200), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "source_type", "source_id"],
            [
                "retrieval_sources.tenant_id",
                "retrieval_sources.workspace_id",
                "retrieval_sources.source_type",
                "retrieval_sources.source_id",
            ],
            name="fk_retrieval_chunks_source",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "workspace_id", "source_type", "source_id", "chunk_index",
            name="uq_retrieval_chunks_source_index",
        ),
        sa.CheckConstraint("chunk_index >= 0", name="ck_retrieval_chunks_index"),
        sa.CheckConstraint("dimensions between 1 and 16000", name="ck_retrieval_chunks_dimensions"),
    )
    # pgvector column, untyped so models of any dimension fit; the check ties it to
    # the stored dimension used to filter searches.
    op.execute("alter table retrieval_chunks add column embedding vector not null")
    op.execute(
        "alter table retrieval_chunks add constraint ck_retrieval_chunks_embedding_dims "
        "check (vector_dims(embedding) = dimensions)"
    )
    op.create_index(
        "ix_retrieval_chunks_scope_model",
        "retrieval_chunks",
        ["tenant_id", "workspace_id", "embedding_model", "dimensions"],
    )
    for table in ("retrieval_sources", "retrieval_chunks"):
        _enable_rls(table)


def downgrade() -> None:
    op.drop_table("retrieval_chunks")
    op.drop_table("retrieval_sources")
