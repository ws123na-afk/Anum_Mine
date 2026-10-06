# Memory

ANUM memory is the system's controlled long-term context layer. It should improve continuity without becoming an opaque store of everything the user has ever said.

## Memory Types

- Task memory: notes, decisions, and artifacts tied to a task.
- User memory: preferences and stable facts approved for reuse.
- Workspace memory: shared project context visible to authorized members.
- Integration memory: derived summaries from connected tools, scoped by consent.
- Operational memory: runtime metadata used for debugging and quality, separated from user-facing recall.

## Storage

PostgreSQL should store canonical memory records, provenance, scope, retention, and permissions. pgvector should store embeddings for semantic retrieval. S3-compatible storage should hold large source artifacts, attachments, transcripts, and generated files. Valkey may cache recent retrieval results but should not be the source of truth.

## Retrieval

Retrieval is tenant-scoped, workspace-aware, permission-filtered and explainable: the runtime records which memory and file passages it used on the run, and every passage reaches the model as labeled untrusted data (threat model G5). Code: `anum_api/retrieval.py` (indexing, search, prompt assembly), `anum_api/db/retrieval_repository.py` (PostgreSQL/pgvector), `anum_api/retrieval_api.py` (API and hooks).

### Index

| Table (migration `0014_retrieval_index`) | Holds |
|---|---|
| `retrieval_sources` | One row per memory note or file: `source_type` (`memory`, `file`), `source_id`, status (`indexed`, `failed`, `skipped`), error code (`embedding_failed`, `budget_exceeded`, `empty`, `not_text`, `not_utf8`, `too_large`), chunk count, embedding model, SHA-256 of the indexed text, the memory's expiry. Never text. |
| `retrieval_chunks` | Chunk text (about 1,200 characters with 150 of overlap, at most 200 chunks per source), its embedding (pgvector `vector`), `embedding_model` and `dimensions`. Cascades from its source row. |

Both tables force RLS on `anum.tenant_id` and `anum.workspace_id`, like every workspace table, and the store also names the tenant and workspace in every statement. The embedding column is untyped because models differ in dimension; a check ties it to `dimensions`, and a search only compares chunks of the query's model and dimension. Search is exact (cosine distance), bounded to one workspace and model by `ix_retrieval_chunks_scope_model`; an approximate (HNSW) index per model is later work for large workspaces. With `ANUM_REPOSITORY_BACKEND=memory` the same index lives in process memory.

**When indexing happens.** Creating a memory indexes it in the same transaction; uploading a text file indexes it after its metadata commits ([Workspace files](files.md#retrieval)). Indexing is best effort: a provider error or an exhausted budget is recorded on the source row and never fails the create. Deleting a memory or file deletes its index rows. Unchanged text with the same embedding model is never embedded again.

### Embeddings

`ANUM_EMBEDDING_BACKEND` picks the embedder:

| Value | Embedder |
|---|---|
| `gateway` (default) | The workspace's own model gateway (`budgeted_model_gateway`): its saved Ollama or OpenAI-compatible endpoint, or the server default, through the SSRF guard, with retries, redacted logging and metrics, and the monthly budget (each embedding call is checked before and recorded after, like a text call; [Model gateway](model-gateway.md#embeddings)). The model is `ANUM_EMBEDDING_MODEL`, or `nomic-embed-text` for Ollama and `text-embedding-3-small` otherwise. With the `mock` provider (the local and test default) it is the deterministic local embedder. |
| `local` | The deterministic local embedder only (`anum-local-hash-v1`): feature hashing of word tokens into 256 signed buckets, L2-normalised. No model server, no network, no budget; it finds word overlap rather than meaning. |

An embedder is built per tenant context and only ever receives that workspace's text, so no model call carries another tenant's data. Changing the embedding model leaves older chunks unused (`stale` in the status) until they are re-indexed.

### At run time

Before planning, the runtime (inline and the Temporal worker) asks the retriever for the task prompt's nearest chunks:

1. Callers without memory read permission get no retrieval (the step says so). Retrieval with `ANUM_RETRIEVAL_TOP_K=0` is off.
2. If the workspace has nothing indexed with the current model, no embedding call is made.
3. The prompt is embedded (budgeted) and the top `ANUM_RETRIEVAL_TOP_K` (5) chunks of *this tenant and workspace* are read. A memory chunk counts only while its memory exists and has not expired, a file chunk only while its file metadata exists.
4. Each chunk is wrapped by `label_untrusted` (`source=memory` or `source=file`, `origin=<record id>/chunk-<n>`, fresh nonce, truncation flag), at most `ANUM_RETRIEVAL_BLOCK_MAX_CHARS` (1,500) characters each and `ANUM_RETRIEVAL_MAX_CHARS` (6,000) in total. The model prompt is the user's task, then `UNTRUSTED_DATA_RULES`, then the blocks ([Threat model](threat-model.md#provenance-labels-for-untrusted-prompt-text-g3-g5)).
5. Skills and the tool are chosen from the user's prompt only; tool policy, approvals and tenant context are decided by the runtime afterwards. Retrieved text is not stored on the run, the checkpoint or any approval payload.

A failed query embedding (provider down) lets the run continue without context (`unavailable`); an exhausted budget fails the run like any other model call (`402`).

The run's `retrieval` step is the audit record. Its metadata: `status` (`ok`, `no_results`, `skipped`, `unavailable`), `reason`, `embedding_model`, `truncated`, `max_chars` and `sources`, a list of `{chunk_id, source_type, source_id, chunk_index, score, truncated}`. The web Tasks view shows it as "Sources used".

### API

- `GET /api/v1/retrieval/status` (memory read): the embedding model in use and, per source, status, error code, chunk count and model; counts of `indexed`, `failed`, `skipped`, `stale` sources and of chunks.
- `POST /api/v1/retrieval/index?limit=100` (memory create, because it spends budget): indexes memories and files that are missing, failed, changed or on another model (at most `limit` per call, `remaining` says how many are left) and deletes index rows whose memory or file is gone or expired. Use it after upgrading to `0014_retrieval_index` to index existing memories and files.

## Governance

Users need ways to inspect, edit, disable, and delete memory. Memories should have source links, creation reasons, last-used timestamps, and retention rules. Sensitive data should not become global memory automatically.

## Now

Task-linked notes with retention and deletion, chunked embeddings in pgvector for memories and text files, scoped retrieval into task prompts with provenance labels, and per-run source records.

## Later

Add an approximate-nearest-neighbour index per embedding model, hybrid (keyword plus vector) ranking, re-indexing in the background when the embedding model changes, memory graph relationships, conflict resolution, freshness scoring, automatic summarization, user-visible memory management, and organization retention policies.