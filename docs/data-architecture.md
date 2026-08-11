# Data Architecture

ANUM's data layer should be boring, durable, and explicit. PostgreSQL is the canonical store for relational data, tenant boundaries, audit records, and memory metadata. pgvector adds semantic retrieval. S3-compatible object storage holds large artifacts. Valkey handles ephemeral state.

## Core Entities

Initial entities should include tenants, users, memberships, workspaces, agents, tasks, agent runs, run steps, approvals, tools, integrations, credentials metadata, memories, files, events, and audit records.

## PostgreSQL

PostgreSQL should enforce schema integrity, foreign keys, timestamps, optimistic concurrency where useful, and row-level security. Tables containing tenant data should include `tenant_id`. Migrations should be reviewed and reversible where practical.

## pgvector

Embeddings should be stored with memory records or related vector tables. Vector search must always be filtered by tenant, workspace, permissions, and retention status before results reach an agent.

## Object Storage

S3-compatible storage should store attachments, exports, transcripts, generated files, and large integration payloads. Database rows should reference objects by bucket, key, checksum, size, content type, owner, and tenant.

## Valkey

Valkey should be used for short-lived locks, idempotency windows, presence, rate limits, and transient caches. Critical state must not live only in Valkey.

## Now

Define migrations for core tables, RLS policies, vector support, object metadata, audit logs, and idempotency keys.

## Later

Add partitions, archival stores, analytical replicas, tenant data exports, regional residency, and dedicated database options for large tenants.