# ANUM MINE Office Space & Desk Registry

## Module status
Complete **interactive local preview module** comprising a typed domain engine, business/floor/room/desk UI, capacity rules, assignment actions, optimistic revision checks, responsive styling and tests. Not production persisted storage.

## Source files
- `apps/web/src/lib/officeSpaceRegistry.ts` - authoritative preview state machine, validations, room queries and occupation metrics
- `apps/web/src/OfficeSpaceRegistry.tsx` - full office management UI
- `apps/web/src/officeSpaceRegistry.css` - responsive styles
- `apps/web/test/officeSpaceRegistry.test.ts` - domain test suite
- `apps/web/src/OfficeTourView.tsx` - opens this module within the authenticated office preview

## Capabilities
- Four separate business buildings, individual floors and rooms, with configured IANA zones and lat/long metadata
- Search floors and rooms, distinguish room types and capacities
- Create a room, create permanent workstations only in eligible rooms, assign a desk or vacate it
- Prevent the same agent ID occupying two desks; prevent existing desks being duplicated; reject invalid coordinates and time zones
- Optimistic concurrency through expected revision; guard against invalid records and room removal when desks exist
- Every change is explicitly identified as an **ephemeral preview**, not a saved enterprise action

## Production requirements before release
1. Backend storage: tenant- and workspace-scoped building/floor/room/desk tables; foreign keys and constraints; migration and rollback
2. Permission and audit: owner/manage role checks, accepted scope from auth context (never trust caller-supplied IDs); append-only audited changes; optimistic DB concurrency
3. Agent identity: verify an agent belongs to the requesting tenant/workspace before assignment; synchronize with agent deletion or movement
4. Integrity: enforce unique occupancy per workspace and room capacity transactionally under concurrent assignment attempts
5. API: typed versioned read/write endpoints with request IDs, throttling, pagination, clear 409 responses, reversible assignment edits
6. Privacy and security: protect private office locations, enforce field-level access, no unauthenticated visitor access to agent assignments
7. UI: replace in-memory reducer with server API and confirmed results, graceful concurrent conflict refresh, remove ephemeral banner only when verified
8. Performance: room geometry and desk occupancy indexed, bounded floor/page retrieval; validate 500+ agents
9. Integration: use canonical agent IDs, synchronized live work-state projection and permissioned asset registry
10. Release gates: frontend/backend tests, 2-tenant PostgreSQL isolation tests, CI, review and deployment plan

## Acceptance
In `#office-preview` click **Office Space Management**. Select four buildings and their floors; inspect room capacities, create a room, add a workstation, assign and unassign a sample agent ID, test duplicate occupancy and revision guards. Close/reopen to observe preview reset, and ensure no claims of persistence appear.

**No merging into main without review.** This module is deliberately isolated from the real backend until its permissions and migrations are ready.
