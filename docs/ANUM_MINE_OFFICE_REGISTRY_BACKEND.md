# ANUM MINE Office Registry - Production Backend

## Scope and current release state
Backend implementation on `workstream/office-production-backend`, based on integrated office modules. This branch defines the PostgreSQL schema, authenticated resource API, row-level security and transactional desk integrity. **Do not deploy or merge until PostgreSQL migration tests and the full API tests pass.** The frontend registry is still an explicit ephemeral preview until a later adapter replaces it with the server API and handles conflict refresh.

## Schema, transaction rules and isolation
Migration `0097_office_spaces` creates `office_buildings`, `office_floors`, `office_rooms`, and `office_desks`. Each row is scoped by tenant and workspace; the three-part (tenant, workspace, id) primary key allows separate businesses to reuse the same stable IDs. Children reference parents using three-part composite foreign keys, preventing cross-workspace parent pointers.

Every table has PostgreSQL `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`, using the existing `anum.tenant_id` and `anum.workspace_id` context variables. The lifecycle-purge role receives narrowly scoped delete privileges using the same workspace/organization deletion conditions as existing 0096 migrations. Tests must exercise operations under the *non-owner app database role*.

Constraints and transaction behavior:
- Building geographic coordinates limited to latitude ±90°, longitude ±180°. The API checks the IANA time zone.
- Floor level unique within one building. Room name unique within its floor. Desk labels unique within rooms.
- Desk occupancy is unique for each (tenant, workspace, agent). SQL partial unique index enforces this on concurrent updates.
- Desk creation locks the parent room row before checking room category and capacity to avoid two simultaneous inserts exceeding capacity.
- Every rename, assignment or deletion requires the expected row revision; stale clients receive 409 and must reload.
- Room deletion is refused while desks exist, using restrictive foreign keys.
- Agent assignment verifies visibility of an existing agent from `agent_profiles` using user-scoped RLS. It cannot assign an invisible private agent or another workspace's agent.

## API
Prefix: `/api/v1/office-spaces`

| Operation | Method | Endpoint | Permission |
|---|---|---|---|
| List scoped records (cursor-paged, up to 100) | GET | `/{buildings|floors|rooms|desks}?limit=50&cursor=...` | `task:read` |
| Read specific record | GET | `/{kind}/{item_id}` | `task:read` |
| Create building | POST | `/buildings` | `workspace:manage` |
| Create floor | POST | `/floors` | `workspace:manage` |
| Create room | POST | `/rooms` | `workspace:manage` |
| Create desk | POST | `/desks` | `workspace:manage` |
| Rename room | PATCH | `/rooms/{room_id}` | `workspace:manage` |
| Assign/release agent | PATCH | `/desks/{desk_id}/assignment` | `workspace:manage` |
| Remove empty room | DELETE | `/rooms/{room_id}?expected_revision=N` | `workspace:manage` |

Every mutation records an append-only audit record within the same PostgreSQL transaction, with user attribution, resource ID and generated correlation ID. API responses use 422 for validation, 403 for missing authorization, 404 for nonexistent scoped resources, 409 for stale/conflicting writes. Memory backend returns **503**, not fake success.

The backend derives tenant, workspace and actor exclusively from the authenticated request context. Client-provided scope properties are rejected by strict input models.

## Tests
- `services/api/tests/test_office_space_api.py`: strict input schemas; memory mode fails closed.
- `services/api/tests/test_postgres_office_spaces.py`: migrated database, tenant/workspace scope, same IDs across tenants, owner-only writes, capacity constraint, stale revision and scope-separated foreign keys.
- Existing repository checks and app CI must also pass. Database tests require the PostgreSQL test fixture and migration head.

## Remaining gates (not production complete until all pass)
1. Backend CI, database migration under non-owner app role, and PostgreSQL integration tests pass.
2. Cursor pagination is implemented (page size 1-100). Verify cursor stability under parallel edits and cover very large workspaces with load tests.
3. Add server-issued outbox events for room/desk changes and replay through existing SSE; audit records alone are insufficient for real-time desk sync.
4. Add idempotency keys for create operations and stronger API versions/ETags; revisions currently cover mutation concurrency.
5. Integrate the frontend with real APIs and teach it to handle 409/reload, unauthorized reads, offline state, multi-workspace switch and user feedback.
6. Implement row geometry/coordinates, walkable paths, and 3D scene assets in a separate feature.
7. Test migration downgrade only in an empty test workspace. Production rollback must disable the feature and restore backup instead of deleting real office records.
8. Test many concurrent assignments, cache invalidation, tenant deletion and API performance at 200+ agents / 1000+ desks.
9. Feature-flag and deploy first to staging, validate security and observability, then seek owner approval before production.
10. Resolve the unrelated Rust certificate audit advisories tracked in issue #190.

## Smoke test flow
On PostgreSQL with the migration applied and an owner in the current workspace:

1. POST building with valid zone.
2. POST floor referencing that building.
3. POST office room with capacity 1.
4. POST one desk, reject second desk (409).
5. PATCH room rename at revision 1; attempt same revision again (409).
6. PATCH desk assignment using a visible agent ID and current revision; verify agent-only-one-desk constraint.
7. GET all records in a second tenant/workspace; confirm no first-workspace data is returned.
8. GET/POST as member/viewer; confirm write is rejected.
9. Confirm audit log includes each successful mutation. Failed operations must leave no partial writes.

**Merging policy:** This backend branch targets the office integration branch via a draft PR, never directly to main. The parent integration and production release still require owner authorization.
