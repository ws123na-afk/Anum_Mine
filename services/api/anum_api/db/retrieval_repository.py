"""PostgreSQL/pgvector store for retrieval chunks (migration ``0014_retrieval_index``).

The session must already carry the tenant and workspace RLS context
(``set_tenant_context``); both tables force RLS on ``anum.tenant_id`` and
``anum.workspace_id``. Every statement also names the tenant and workspace explicitly,
so a missing context finds nothing rather than relying on one layer alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from anum_api.maintenance import SessionFactory, discover, scoped_unit
from anum_api.retrieval import (
    ChunkInput,
    IndexStatus,
    RetrievalSource,
    RetrievedChunk,
    SourceType,
)
from anum_api.schemas import TenantContext

from .scope import require_workspace



def vector_literal(values: Sequence[float]) -> str:
    """pgvector's text form, ``[0.1,0.2,...]`` (floats only; never user text)."""
    return "[" + ",".join(repr(float(value)) for value in values) + "]"


# Dimensions with a partial HNSW expression index (migration 0016_retrieval_hnsw).
ANN_DIMENSIONS = frozenset({256, 384, 768, 1024, 1536})
ITERATIVE_SCAN_VERSION = (0, 8)
HNSW_EF_SEARCH = 100


def pgvector_version(value: str | None) -> tuple[int, ...]:
    """``"0.8.0"`` -> ``(0, 8, 0)``; anything unparsable -> ``()``."""
    parts: list[int] = []
    for piece in (value or "").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


_LIVE_CHUNKS = """
    from retrieval_chunks c
    join retrieval_sources s
      on s.tenant_id = c.tenant_id and s.workspace_id = c.workspace_id
     and s.source_type = c.source_type and s.source_id = c.source_id
    where c.tenant_id = :tenant_id and c.workspace_id = :workspace_id
      and c.embedding_model = :model and c.dimensions = {dimensions}
      and s.status = 'indexed'
      and (s.source_expires_at is null or s.source_expires_at > :now)
      and (
        (c.source_type = 'memory' and exists (
          select 1 from memories m
          where m.tenant_id = c.tenant_id and m.workspace_id = c.workspace_id
            and m.id = c.source_id
            and (m.retention_expires_at is null or m.retention_expires_at > :now)))
        or (c.source_type = 'file' and exists (
          select 1 from workspace_files f
          where f.tenant_id = c.tenant_id and f.workspace_id = c.workspace_id
            and f.id = c.source_id))
      )
"""
_COLUMNS = "c.id, c.tenant_id, c.workspace_id, c.source_type, c.source_id, c.chunk_index, c.content"


def search_sql(dimensions: int, *, ann: bool) -> str:
    """The search statement for one query dimension.

    Exact: ``embedding <=> query`` on the untyped column, ``dimensions`` bound as a
    parameter, ties broken by chunk id. ANN: the same filters, but the distance is taken
    on ``embedding::vector(N)`` and the predicate is ``dimensions = N`` written as a
    literal, which is what lets PostgreSQL match the partial HNSW index
    ``ix_retrieval_chunks_hnsw_N``; the nearest ``limit`` come from the index scan and are
    then ordered by distance and chunk id. ``N`` is an int from :data:`ANN_DIMENSIONS`,
    never caller text.
    """
    if not ann:
        return (
            f"select {_COLUMNS}, 1 - (c.embedding <=> cast(:query as vector)) as score "
            + _LIVE_CHUNKS.format(dimensions=":dimensions")
            + " order by c.embedding <=> cast(:query as vector), c.id limit :limit"
        )
    if dimensions not in ANN_DIMENSIONS:
        raise ValueError("no HNSW index for this dimension")
    dims = int(dimensions)
    distance = f"(c.embedding::vector({dims}) <=> cast(:query as vector({dims})))"
    # Only constants and an int from the ANN_DIMENSIONS allow-list are interpolated;
    # every value (tenant, workspace, model, query vector, limit) is a bound parameter.
    return (
        "select * from ("  # nosec B608
        f"select {_COLUMNS}, {distance} as distance, 1 - {distance} as score "
        + _LIVE_CHUNKS.format(dimensions=dims)
        + f" order by {distance} limit :limit"
        ") ranked order by distance, id"
    )


class SqlAlchemyRetrievalStore:
    def __init__(self, session: Session, *, ann: bool | None = None) -> None:
        """``ann``: use the HNSW indexes (True), never (False), or when pgvector >= 0.8 (None)."""
        self.session = session
        self.ann = ann
        self._iterative_scan: bool | None = None

    def _supports_iterative_scan(self) -> bool:
        if self._iterative_scan is None:
            version = self.session.execute(
                text("select extversion from pg_extension where extname = 'vector'")
            ).scalar_one_or_none()
            self._iterative_scan = pgvector_version(version) >= ITERATIVE_SCAN_VERSION
        return self._iterative_scan

    def uses_ann(self, dimensions: int) -> bool:
        if dimensions not in ANN_DIMENSIONS or self.ann is False:
            return False
        return True if self.ann else self._supports_iterative_scan()

    def _tune_hnsw(self, limit: int) -> None:
        # Transaction-local. ef_search bounds the candidates one HNSW scan returns; with
        # pgvector 0.8+ an iterative scan keeps going (in distance order) while the
        # tenant, workspace and liveness filters drop candidates.
        self.session.execute(
            text("select set_config('hnsw.ef_search', :ef, true)"),
            {"ef": str(max(HNSW_EF_SEARCH, min(1000, limit)))},
        )
        if self._supports_iterative_scan():
            self.session.execute(text("select set_config('hnsw.iterative_scan', 'strict_order', true)"))

    @staticmethod
    def _scope(context: TenantContext) -> dict[str, str]:
        return {"tenant_id": context.tenant_id, "workspace_id": context.workspace_id}

    @staticmethod
    def _source(row) -> RetrievalSource:  # type: ignore[no-untyped-def]
        return RetrievalSource(
            tenant_id=row.tenant_id,
            workspace_id=row.workspace_id,
            source_type=SourceType(row.source_type),
            source_id=row.source_id,
            name=row.name,
            content_sha256=row.content_sha256,
            status=IndexStatus(row.status),
            error=row.error,
            chunk_count=row.chunk_count,
            embedding_model=row.embedding_model,
            source_expires_at=row.source_expires_at,
            indexed_at=row.indexed_at,
        )

    def get_source(self, context: TenantContext, source_type: SourceType, source_id: str) -> RetrievalSource | None:
        row = self.session.execute(
            text(
                "select tenant_id, workspace_id, source_type, source_id, name, content_sha256, status, "
                "error, chunk_count, embedding_model, source_expires_at, indexed_at from retrieval_sources "
                "where tenant_id = :tenant_id and workspace_id = :workspace_id "
                "and source_type = :source_type and source_id = :source_id"
            ),
            {**self._scope(context), "source_type": str(source_type), "source_id": source_id},
        ).first()
        return self._source(row) if row is not None else None

    def list_sources(self, context: TenantContext) -> list[RetrievalSource]:
        rows = self.session.execute(
            text(
                "select tenant_id, workspace_id, source_type, source_id, name, content_sha256, status, "
                "error, chunk_count, embedding_model, source_expires_at, indexed_at from retrieval_sources "
                "where tenant_id = :tenant_id and workspace_id = :workspace_id "
                "order by source_type, source_id"
            ),
            self._scope(context),
        ).all()
        return [self._source(row) for row in rows]

    def has_indexed(self, context: TenantContext, embedding_model: str) -> bool:
        return bool(
            self.session.execute(
                text(
                    "select exists (select 1 from retrieval_sources "
                    "where tenant_id = :tenant_id and workspace_id = :workspace_id "
                    "and status = 'indexed' and embedding_model = :model)"
                ),
                {**self._scope(context), "model": embedding_model},
            ).scalar_one()
        )

    def save_source(self, context: TenantContext, source: RetrievalSource, chunks: Sequence[ChunkInput]) -> None:
        if (source.tenant_id, source.workspace_id) != (context.tenant_id, context.workspace_id):
            raise ValueError("source belongs to another tenant or workspace")
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        key = {**self._scope(context), "source_type": str(source.source_type), "source_id": source.source_id}
        self.session.execute(
            text(
                "delete from retrieval_chunks where tenant_id = :tenant_id and workspace_id = :workspace_id "
                "and source_type = :source_type and source_id = :source_id"
            ),
            key,
        )
        self.session.execute(
            text(
                "insert into retrieval_sources (tenant_id, workspace_id, source_type, source_id, name, "
                "content_sha256, status, error, chunk_count, embedding_model, source_expires_at, indexed_at) "
                "values ("
                ":tenant_id, :workspace_id, :source_type, :source_id, :name, :content_sha256, :status, "
                ":error, :chunk_count, :embedding_model, :source_expires_at, :indexed_at) "
                "on conflict (tenant_id, workspace_id, source_type, source_id) do update set "
                "name = excluded.name, content_sha256 = excluded.content_sha256, status = excluded.status, "
                "error = excluded.error, chunk_count = excluded.chunk_count, "
                "embedding_model = excluded.embedding_model, source_expires_at = excluded.source_expires_at, "
                "indexed_at = excluded.indexed_at"
            ),
            {
                **key,
                "name": source.name,
                "content_sha256": source.content_sha256,
                "status": source.status.value,
                "error": source.error,
                "chunk_count": len(chunks),
                "embedding_model": source.embedding_model,
                "source_expires_at": source.source_expires_at,
                "indexed_at": source.indexed_at,
            },
        )
        for chunk in chunks:
            self.session.execute(
                text(
                    "insert into retrieval_chunks (id, tenant_id, workspace_id, source_type, source_id, "
                    "chunk_index, content, content_sha256, embedding_model, dimensions, embedding, created_at) "
                    "values (:id, :tenant_id, :workspace_id, :source_type, :source_id, :chunk_index, :content, "
                    ":content_sha256, :embedding_model, :dimensions, cast(:embedding as vector), :created_at)"
                ),
                {
                    **key,
                    "id": chunk.id,
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                    "content_sha256": chunk.content_sha256,
                    "embedding_model": source.embedding_model,
                    "dimensions": len(chunk.embedding),
                    "embedding": vector_literal(chunk.embedding),
                    "created_at": source.indexed_at,
                },
            )
        self.session.flush()

    def delete_source(self, context: TenantContext, source_type: SourceType, source_id: str) -> bool:
        key = {**self._scope(context), "source_type": str(source_type), "source_id": source_id}
        # Chunks cascade from the source row.
        result = self.session.execute(
            text(
                "delete from retrieval_sources where tenant_id = :tenant_id and workspace_id = :workspace_id "
                "and source_type = :source_type and source_id = :source_id"
            ),
            key,
        )
        self.session.flush()
        return bool(result.rowcount)

    def search(
        self,
        context: TenantContext,
        embedding: Sequence[float],
        embedding_model: str,
        limit: int,
        now: datetime,
    ) -> list[RetrievedChunk]:
        """Nearest chunks by cosine distance, live sources of this workspace only.

        A memory chunk counts only while its memory exists and has not expired; a file
        chunk only while its file metadata exists. For a dimension in
        :data:`ANN_DIMENSIONS` (and pgvector 0.8 or later, unless ``ann`` was given) the
        query is written so PostgreSQL may use that dimension's HNSW index (approximate);
        otherwise the search is exact, bounded to one workspace and model by
        ``ix_retrieval_chunks_scope_model``.
        """
        dimensions = len(embedding)
        ann = self.uses_ann(dimensions)
        if ann:
            self._tune_hnsw(limit)
        rows = self.session.execute(
            text(search_sql(dimensions, ann=ann)),
            {
                **self._scope(context),
                "query": vector_literal(embedding),
                "model": embedding_model,
                "dimensions": dimensions,
                "now": now,
                "limit": limit,
            },
        ).all()
        return [
            RetrievedChunk(
                chunk_id=row.id,
                tenant_id=row.tenant_id,
                workspace_id=row.workspace_id,
                source_type=SourceType(row.source_type),
                source_id=row.source_id,
                chunk_index=row.chunk_index,
                content=row.content,
                score=float(row.score),
            )
            for row in rows
        ]


# Retention purge ---------------------------------------------------------------------

# Runs as anum_maintenance (migration 0015): its policy shows only sources of memories
# whose retention has passed, and its grants cover the scope and id columns only.
_WORKSPACES_WITH_EXPIRED_SOURCES = text(
    """
    select tenant_id, workspace_id
    from retrieval_sources
    group by tenant_id, workspace_id
    order by tenant_id, workspace_id
    limit :limit
    """
)

# As the application role inside one workspace's RLS context. A memory source is purged
# when its recorded expiry passed or its memory is gone or expired; a file source when its
# file metadata is gone. Chunks cascade from the source row.
_COUNT_PURGEABLE = text(
    """
    select count(*) as sources, coalesce(sum(s.chunk_count), 0) as chunks
    from retrieval_sources s
    where s.tenant_id = :tenant_id and s.workspace_id = :workspace_id
      and (
        (s.source_type = 'memory' and (
          (s.source_expires_at is not null and s.source_expires_at <= now())
          or not exists (
            select 1 from memories m
            where m.tenant_id = s.tenant_id and m.workspace_id = s.workspace_id and m.id = s.source_id
              and (m.retention_expires_at is null or m.retention_expires_at > now()))))
        or (s.source_type = 'file' and not exists (
            select 1 from workspace_files f
            where f.tenant_id = s.tenant_id and f.workspace_id = s.workspace_id and f.id = s.source_id))
      )
    """
)
# The same predicate as _COUNT_PURGEABLE.
_DELETE_PURGEABLE = text(
    """
    delete from retrieval_sources s
    where s.tenant_id = :tenant_id and s.workspace_id = :workspace_id
      and (
        (s.source_type = 'memory' and (
          (s.source_expires_at is not null and s.source_expires_at <= now())
          or not exists (
            select 1 from memories m
            where m.tenant_id = s.tenant_id and m.workspace_id = s.workspace_id and m.id = s.source_id
              and (m.retention_expires_at is null or m.retention_expires_at > now()))))
        or (s.source_type = 'file' and not exists (
            select 1 from workspace_files f
            where f.tenant_id = s.tenant_id and f.workspace_id = s.workspace_id and f.id = s.source_id))
      )
    returning s.chunk_count
    """
)


@dataclass
class RetrievalPurgeResult:
    workspaces: int = 0
    sources: int = 0
    chunks: int = 0
    dry_run: bool = False


def purge_expired_retrieval_sources(
    session_factory: SessionFactory,
    *,
    maintenance_session_factory: SessionFactory | None = None,
    batch_size: int = 500,
    dry_run: bool = False,
) -> RetrievalPurgeResult:
    """Delete index rows of expired or deleted memories and of deleted files.

    Workspaces are discovered as ``anum_maintenance`` (scope only, and only where an
    expired memory's source remains); each workspace is then purged as the application
    role inside its tenant and workspace RLS context, which also removes rows whose memory
    or file is gone in that workspace. Deleting a memory or file already removes its rows
    in the same transaction (migration 0015 triggers), so orphans are rare.
    """
    result = RetrievalPurgeResult(dry_run=dry_run)
    seen: set[tuple[str, str]] = set()
    while True:
        rows = discover(
            maintenance_session_factory or session_factory, _WORKSPACES_WITH_EXPIRED_SOURCES, {"limit": batch_size}
        )
        fresh = [(row.tenant_id, row.workspace_id) for row in rows if (row.tenant_id, row.workspace_id) not in seen]
        if not fresh:
            return result
        for tenant_id, workspace_id in fresh:
            seen.add((tenant_id, workspace_id))
            scope = {"tenant_id": tenant_id, "workspace_id": workspace_id}
            with scoped_unit(session_factory, tenant_id, workspace_id) as session:
                if dry_run:
                    counted = session.execute(_COUNT_PURGEABLE, scope).one()
                    sources, chunks = int(counted.sources), int(counted.chunks)
                    session.rollback()
                else:
                    deleted = list(session.execute(_DELETE_PURGEABLE, scope).scalars().all())
                    sources, chunks = len(deleted), int(sum(deleted))
            if sources:
                result.workspaces += 1
                result.sources += sources
                result.chunks += chunks
        if len(rows) < batch_size:
            return result
