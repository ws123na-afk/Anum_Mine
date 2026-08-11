# Scaling

ANUM should scale by protecting clear boundaries before splitting everything into services. The first version should be a modular monolith with durable workflows and evented boundaries. Service extraction should follow measured bottlenecks.

## Early Scaling Priorities

- Keep database queries tenant-filtered and indexed.
- Use queues and workflows for slow work.
- Make tool execution idempotent.
- Cap model costs and concurrent runs.
- Separate realtime fanout from request handling.
- Store large artifacts outside PostgreSQL.

## Bottleneck Areas

Agent runs can consume model latency, external API quotas, workflow worker capacity, and memory retrieval resources. The platform should track queue depth, run duration, token usage, approval wait time, vector search latency, and integration rate limits.

## Service Extraction

Likely extraction candidates are model gateway workers, tool execution workers, event fanout, embedding/indexing workers, and integration webhook ingestion. Extraction should preserve the same contracts and tenant context.

## Data Scaling

PostgreSQL should start with good schema design, indexes, query plans, and RLS tests. Later options include partitioning, read replicas, tenant sharding, dedicated enterprise databases, and analytics replicas.

## Now

Design for idempotency, observability, background workers, and cost limits.

## Later

Add autoscaling, regional routing, advanced cache policy, event replay capacity, large-tenant isolation, and disaster recovery objectives.