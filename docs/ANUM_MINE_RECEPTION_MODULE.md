# ANUM MINE Reception & Visitor Operations

**Status: retired demonstration.** The local reception simulation (visitor arrival, check-in, host and room assignment, escort) was removed from the live office earlier and, in the data truth change for security review S-11, deleted completely, together with `src/ReceptionOperations.tsx`, `src/receptionOperations.css`, `src/lib/receptionOperations.ts` and `test/receptionOperations.test.ts`. It invented hosts ("Reception coordinator", "Finance Controller") and rooms, and nothing in the product may offer a record made with an invented person.

**What a reception room does now.** The owner decided O4 on 27 October 2026: Reception is a real visitor register. A room of kind `reception` opens it and shows the stored number of visitors checked in there. See [reception visitor register](ANUM_MINE_HQ_RECEPTION.md). The simulation is not coming back.

**Guards.** `test/conceptOfficeData.test.ts` fails if the simulation or its example strings return anywhere in `src/`; `e2e/hq-room-modules.spec.ts` fails if an invented name appears in the live office.

## Backend integration contract (superseded)

Built as one table, `office_visitors`, with a status and its times instead of three tables; the rules (tenant and workspace in every key, owner-only writes, revisions, server timestamps, audit and events after commit, real members as hosts) are kept. See [reception visitor register](ANUM_MINE_HQ_RECEPTION.md). The original note:
Introduce server-side `visits`, `visit_events`, `visit_assignments` with tenant/workspace ID in every key, role-limited creation and transition rights, idempotency keys, monotonic revisions, server timestamps and an audited event outbox. Hosts come from the workspace's real members (the members API that `MembersView` already uses), never from a fixed list; with no members the form says "No members". A visit moves to `escorting` only after a real host accepts the handover. Visitor privacy controls, a retention policy, reception roles and customer consent are required before real use.
