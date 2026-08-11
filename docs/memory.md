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

Retrieval should be tenant-scoped, workspace-aware, permission-filtered, and explainable. The runtime should know why a memory was included and expose that provenance in task traces where appropriate.

## Governance

Users need ways to inspect, edit, disable, and delete memory. Memories should have source links, creation reasons, last-used timestamps, and retention rules. Sensitive data should not become global memory automatically.

## Now

Create a minimal memory table, embedding support, scoped retrieval, task-linked notes, and deletion paths.

## Later

Add memory graph relationships, conflict resolution, freshness scoring, automatic summarization, user-visible memory management, and organization retention policies.