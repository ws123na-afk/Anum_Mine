"""Approximate (HNSW) vector indexes for retrieval search (docs/memory.md#vector-search).

``retrieval_chunks.embedding`` is an untyped ``vector`` because embedding models differ
in dimension, and pgvector can only build an HNSW index over a column of one fixed
dimension. pgvector's documented answer is an *expression* index on a cast to a typed
vector, made *partial* on the stored dimension so the cast only ever sees vectors of that
size::

    create index ... using hnsw ((embedding::vector(768)) vector_cosine_ops)
        where dimensions = 768

One index per dimension in :data:`ANN_DIMENSIONS` (the common embedding sizes: the local
hashing embedder's 256, MiniLM's 384, nomic-embed-text's 768, 1024, and OpenAI's
text-embedding-3-small at 1536). A search uses the index only when its query names the
same expression and predicate (``anum_api/db/retrieval_repository.py``); any other
dimension keeps the exact scan bounded by ``ix_retrieval_chunks_scope_model``. HNSW on
``vector`` supports at most 2,000 dimensions, so larger models stay exact.

Indexes are not created on the fly when a new model appears: the application role does
not own the table and must not run DDL. A new dimension needs a migration that adds one
more index the same way.

Built ``CONCURRENTLY`` (outside the migration transaction) so writes continue during
the build. Expand only: the previous release ignores the indexes.
"""

from alembic import op

revision = "0016_retrieval_hnsw"
down_revision = "0015_retrieval_retention"
branch_labels = None
depends_on = None

# Keep in sync with anum_api.db.retrieval_repository.ANN_DIMENSIONS (a unit test checks).
ANN_DIMENSIONS = (256, 384, 768, 1024, 1536)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for dims in ANN_DIMENSIONS:
            op.execute(
                f"create index concurrently if not exists ix_retrieval_chunks_hnsw_{dims} "
                f"on retrieval_chunks using hnsw ((embedding::vector({dims})) vector_cosine_ops) "
                f"where dimensions = {dims}"
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for dims in ANN_DIMENSIONS:
            op.execute(f"drop index concurrently if exists ix_retrieval_chunks_hnsw_{dims}")
