# Memory

ANUM memory is the system's controlled long-term context layer. It should improve continuity without becoming an opaque store of everything the user has ever said.

## Memory Types

- Task memory: notes, decisions, and artifacts tied to a task.
- User memory: preferences and stable facts approved for reuse. Facts remembered from a user's assistant chats are private to that user ([From conversations](#from-conversations)).
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

Both tables force RLS on `anum.tenant_id` and `anum.workspace_id`, like every workspace table, and the store also names the tenant and workspace in every statement. The embedding column is untyped because models differ in dimension; a check ties it to `dimensions`, and a search only compares chunks of the query's model and dimension. Search uses cosine distance, bounded to one workspace and model by `ix_retrieval_chunks_scope_model`, and may use an approximate index ([Vector search](#vector-search)). With `ANUM_REPOSITORY_BACKEND=memory` the same index lives in process memory.

### Vector search

pgvector builds an HNSW index only over vectors of one fixed dimension, and `embedding` is untyped. Migration `0016_retrieval_hnsw` therefore adds pgvector's documented workaround: one *partial expression* index per dimension, `using hnsw ((embedding::vector(N)) vector_cosine_ops) where dimensions = N`, for N in 256 (the local embedder), 384, 768 (`nomic-embed-text`), 1024 and 1536 (`text-embedding-3-small`). They are built `CONCURRENTLY`; check `pg_index.indisvalid` after the migration, and if a build failed, drop the invalid index and re-run the migration's statement.

How a search chooses (`anum_api/db/retrieval_repository.py`, `search_sql`):

- **Approximate** when the query's dimension has an index *and* pgvector is 0.8 or later. The statement takes the distance on `embedding::vector(N)` and writes `dimensions = N` as a literal, which is what lets PostgreSQL match the partial index; it sets `hnsw.iterative_scan = strict_order` and `hnsw.ef_search` (at least 100) for the transaction, takes the nearest `ANUM_RETRIEVAL_TOP_K`, and orders them by distance and chunk id. The tenant, workspace, model and liveness filters are the same as the exact search, and RLS applies either way. The planner still picks the scope b-tree and an exact sort when that is cheaper (a small workspace), so small workspaces stay exact.
- **Exact** for any other dimension, dimensions above 2,000 (HNSW's limit for `vector`), and on pgvector before 0.8. Without iterative scans an HNSW scan returns at most `ef_search` candidates *before* the tenant and workspace filters, so a small workspace in a large shared index could get fewer results than exist; ANUM does not take that risk and stays exact there.

Trade-offs: HNSW recall is high but not guaranteed, so a large workspace may occasionally miss a chunk the exact scan would rank in the top k (ties at the cut-off may also resolve differently). With iterative scans, `hnsw.max_scan_tuples` (20,000 by default) bounds how far a scan goes, so a workspace that holds a tiny fraction of a very large index of one dimension can still get fewer than k results; raise it, or rely on the planner's exact path. Indexes are not created when a new model first appears: the application role does not own the table and never runs DDL. A model of a new dimension is searched exactly until a migration adds its index. A typed column per model (or `halfvec` for dimensions up to 4,000) would remove the casts; it is not needed for the dimensions in use.

Tests: `tests/test_postgres_retrieval.py` checks every index exists and is valid, that the planner uses it for the approximate statement, and that approximate results equal the exact ones on a small index (where HNSW is exact).

**When indexing happens.** Creating a memory indexes it in the same transaction; uploading a text file indexes it after its metadata commits ([Workspace files](files.md#retrieval)). Indexing is best effort: a provider error or an exhausted budget is recorded on the source row and never fails the create. Deleting a memory or file deletes its index rows (see [Retention](#retention)). Unchanged text with the same embedding model is never embedded again.

### Retention

Search ignores a chunk the moment its memory expires or its memory or file is deleted. The rows themselves are removed too:

- **Deleted memories and files.** Migration `0015_retrieval_retention` adds `AFTER DELETE` row triggers on `memories` and `workspace_files` that delete the matching `retrieval_sources` row (its chunks cascade) in the same transaction, whatever deleted the record (the API, a cascade from a deleted task, an operator). The trigger functions are `SECURITY INVOKER`: the delete runs as the role that deleted the record and is checked by the same tenant-isolation policies. The API's delete hooks remain and find nothing left to do.
- **Expired memories.** The retention purge `python -m anum_api.retrieval_retention [--dry-run]` deletes the index rows of memories whose retention passed. It runs as part of `python -m anum_api.voice_retention` (the daily retention CronJob; its output adds a `retrieval_index` entry), so no extra schedule is needed. It follows the [maintenance pattern](multi-tenancy.md#maintenance-role): workspaces holding a source of an expired memory are discovered as `anum_maintenance`, which may read only the scope, id and expiry columns of exactly those `retrieval_sources` rows; each workspace is then purged as the application role in its own tenant and workspace RLS context. In that workspace it also removes rows whose memory is gone or expired or whose file metadata is gone (orphans left before `0015`). Output: counts of workspaces, sources and chunks, never text.
- Orphans in a workspace with no expired memory source are not discovered by the purge; `POST /api/v1/retrieval/index` removes them (below). The memory records themselves are not deleted by the purge; reads hide them once expired.

### Embeddings

`ANUM_EMBEDDING_BACKEND` picks the embedder:

| Value | Embedder |
|---|---|
| `gateway` (default) | The workspace's own model gateway (`budgeted_model_gateway`): its saved Ollama or OpenAI-compatible endpoint, or the server default, through the SSRF guard, with retries, redacted logging and metrics, and the monthly budget (each embedding call is checked before and recorded after, like a text call; [Model gateway](model-gateway.md#embeddings)). The model is `ANUM_EMBEDDING_MODEL`, or `nomic-embed-text` for Ollama and `text-embedding-3-small` otherwise. With the `mock` provider (the local and test default) it is the deterministic local embedder. |
| `local` | The deterministic local embedder only (`anum-local-hash-v1`): feature hashing of word tokens into 256 signed buckets, L2-normalised. No model server, no network, no budget; it finds word overlap rather than meaning. |

An embedder is built per tenant context and only ever receives that workspace's text, so no model call carries another tenant's data. Changing the embedding model leaves older chunks unused (`stale` in the status, counted per old model in `stale_models`) until `POST /api/v1/retrieval/index` re-embeds them (below); a search only compares chunks of the current model, so a workspace finds nothing it has not re-embedded yet.

### At run time

Before planning, the runtime (inline and the Temporal worker) asks the retriever for the task prompt's nearest chunks:

1. Callers without memory read permission get no retrieval (the step says so). Retrieval with `ANUM_RETRIEVAL_TOP_K=0` is off.
2. If the workspace has nothing indexed with the current model, no embedding call is made.
3. The prompt is embedded (budgeted) and the top `ANUM_RETRIEVAL_TOP_K` (5) chunks of *this tenant and workspace* are read. A memory chunk counts only while its memory exists and has not expired, a file chunk only while its file metadata exists.
4. Each chunk is wrapped by `label_untrusted` (`source=memory` or `source=file`, `origin=<record id>/chunk-<n>`, fresh nonce, truncation flag), at most `ANUM_RETRIEVAL_BLOCK_MAX_CHARS` (1,500) characters each and `ANUM_RETRIEVAL_MAX_CHARS` (6,000) in total. The model prompt is the user's task, then `UNTRUSTED_DATA_RULES`, then the blocks ([Threat model](threat-model.md#provenance-labels-for-untrusted-prompt-text-g3-g5)).
5. Skills and the tool are chosen from the user's prompt only; tool policy, approvals and tenant context are decided by the runtime afterwards. Retrieved text is not stored on the run, the checkpoint or any approval payload.

A failed query embedding (provider down) lets the run continue without context (`unavailable`); an exhausted budget fails the run like any other model call (`402`).

The run's `retrieval` step is the audit record. Its metadata: `status` (`ok`, `no_results`, `skipped`, `unavailable`), `reason`, `embedding_model`, `truncated`, `max_chars` and `sources`, a list of `{chunk_id, source_type, source_id, chunk_index, score, truncated}`. The web Tasks view and the Flutter task detail show it as "Sources used" ([Flutter mobile](mobile.md#implemented)).

### API

- `GET /api/v1/memories` (memory read) lists the workspace's shared notes and the caller's own private memories; `source_type=conversation` keeps only memories remembered from chats. A private memory has `task_id: null`, `owner_user_id` (the caller) and `provenance.source_id` = the conversation; `provenance.metadata.conversation_title` names it. `DELETE /api/v1/memories/{id}` (memory delete) deletes it with its index rows; nobody else, not even a workspace owner, can see or delete another user's private memory.
- `GET /api/v1/retrieval/status` (memory read): the embedding model in use and, per source, status, error code, chunk count and model; counts of `indexed`, `failed`, `skipped`, `stale` sources and of chunks, and `stale_models` (stale sources per model they were indexed with).
- `POST /api/v1/retrieval/index?limit=100` (memory create, because it spends budget): indexes memories and files that are missing, failed, changed or on another model (at most `limit` embedded per call, `remaining` says how many are left) and deletes index rows whose memory or file is gone or expired. Unchanged sources on the current model are not counted against `limit`. After an embedding model change (`ANUM_EMBEDDING_MODEL`, the provider, or a workspace's saved endpoint) it re-embeds the sources still on the old model: `reembedded` counts them (they are also in `indexed`), `stale_remaining` counts those left for the next call, and the response's `status` shows what is still `stale`. Call it until `stale_remaining` is 0. Use it after upgrading to `0014_retrieval_index` to index existing memories and files.

## From conversations

The [assistant chat](conversations.md) has long-term memory. Code: `anum_api/conversation_memory.py`; storage: migration `0021_memory_sources`.

**Remembering.** When an assistant reply is complete and stored, a background pass runs after the whole SSE body has been sent (a Starlette background task), so it never delays, blocks or fails the stream:

1. It runs only when `ANUM_CONVERSATION_MEMORY_ENABLED` is true (the default), the caller has memory create permission (owner and member), and the tenant's memory governance allows it: an `allowed_source_types` list that does not contain `conversation` turns it off for the tenant (an empty list allows every source).
2. The workspace's model (the same budgeted gateway as the chat: checked before, metered after) gets one **non-streaming** call, `complete_chat` with `max_tokens` = `ANUM_CONVERSATION_MEMORY_MAX_TOKENS` (256): the question and the reply, framed as data, and an instruction to return `{"facts": [...]}`, at most `ANUM_CONVERSATION_MEMORY_MAX_FACTS` (5) durable facts the *user* stated about themselves ("The user prefers short answers."). With the mock provider (local and tests) no model is called; a deterministic rule-based extractor rewrites first-person statements ("My name is Sam" becomes "The user's name is Sam.").
3. The reply is parsed tolerantly (code fences and prose around the JSON are ignored; anything unparsable yields nothing). Facts shorter than 8 or longer than 240 characters, duplicates, and facts that look sensitive (passwords, keys, tokens, card or account numbers, long secrets) are dropped.
4. Each fact is upserted into the user's private memories in one unit of work: the same text (ignoring case and punctuation) is left alone; otherwise the fact is embedded (budgeted, once per pass: the same vector is reused to index it) and compared with the user's own conversation memories through the retrieval index (`source_type = memory`). At cosine similarity `ANUM_CONVERSATION_MEMORY_DEDUPE_THRESHOLD` (0.85) or more the nearest memory is **updated** in place (new text, `provenance.source_id` = the newest conversation, `updated_at` set) and re-indexed; below it a new memory is created: `source_type = conversation`, `source_id` = the conversation id, no task, `owner_user_id` = the user, retention indefinite unless memory governance disallows permanent retention (then `default_retention_days`). If the provider cannot embed, only exact-text dedupe applies for that pass.
5. One pass at a time per user (an in-process lock), bounded by `ANUM_CONVERSATION_MEMORY_TIMEOUT_SECONDS` (60). Any failure (provider down, budget exhausted, timeout, database error) is logged as `conversation_memory_failed stage=<stage> error_class=<class>`, never with text, and nothing else happens. A successful pass logs counts only (`conversation_memory_pass`).

**Privacy.** Conversations are private, so their memories are too. `memories.owner_user_id` and `retrieval_sources.owner_user_id` hold the owner; a `RESTRICTIVE` policy on each table (AND-ed with tenant isolation) makes an owned row visible and writable only while `anum.user_id` names its owner, and every statement repeats the rule. Memory and retrieval units of work therefore set `anum.user_id` (`memory_repository_context`, `open_memory_repository`, `open_retrieval_store`). Chunks are read only through their source row. A task run's retrieval sees the caller's own private memories and the workspace's shared ones, never another user's. Sessions without a user (the retention purge, the previous release) do not see owned rows, so they neither return nor purge them; the owner's next memory pass deletes their own expired conversation memories (with their index rows). Deleting a conversation keeps the memories drawn from it; the Memory view then shows the source as a deleted chat.

**Recall.** Every ask (`POST /api/v1/conversations/{id}/ask`), before the reply streams, embeds the question and retrieves the nearest memories the caller may read (`source_type = memory` only: their private memories and the workspace's shared notes; never files), at most `ANUM_CONVERSATION_MEMORY_RECALL_K` (4) and `ANUM_CONVERSATION_MEMORY_RECALL_CHARS` (2,000) characters in total. Each is wrapped by `label_untrusted` and placed in the system message after a short preface and `UNTRUSTED_DATA_RULES`. Recall needs memory read permission, spends no embedding call when nothing is indexed, and is skipped (the question is answered without it) when it fails, the budget is exhausted, or it takes longer than `ANUM_CONVERSATION_MEMORY_RECALL_TIMEOUT_SECONDS` (3). The SSE `turn` event lists the recalled memory ids in `memories_used`.

**Web.** The Memory view labels a memory from a chat "From chat" with the conversation's title and an **Open chat** link to it in the Assistant view, alongside delete. It has no task column for such a memory.

## Governance

Users need ways to inspect, edit, disable, and delete memory. Memories should have source links, creation reasons, last-used timestamps, and retention rules. Sensitive data should not become global memory automatically.

## Now

Task-linked notes with retention and deletion, chunked embeddings in pgvector for memories and text files, scoped retrieval into task prompts with provenance labels, per-run source records, and private long-term memory from assistant chats (extraction, dedupe by embedding, recall into the chat, deletion from the Memory view).

## Later

Approximate-nearest-neighbour indexes for further dimensions as models are adopted, hybrid (keyword plus vector) ranking, re-indexing in the background when the embedding model changes, memory graph relationships, conflict resolution (two contradictory facts below the dedupe threshold are both kept), a per-user switch for conversation memory (today: the server setting and tenant governance), editing a memory in place from the Memory view, freshness scoring, automatic summarization, user-visible memory management, and organization retention policies.