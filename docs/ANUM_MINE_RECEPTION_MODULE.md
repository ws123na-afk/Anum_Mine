# ANUM MINE Reception & Visitor Operations

**Status: retired demonstration.** The local reception simulation (visitor arrival, check-in, host and room assignment, escort) was removed from the live office earlier and, in the data truth change for security review S-11, deleted completely, together with `src/ReceptionOperations.tsx`, `src/receptionOperations.css`, `src/lib/receptionOperations.ts` and `test/receptionOperations.test.ts`. It invented hosts ("Reception coordinator", "Finance Controller") and rooms, and nothing in the product may offer a record made with an invented person.

**What a reception room does now.** A room of kind `reception` shows its desks and presence and says plainly that there is no visitor register (`no_visitor_register` in `lib/hqRoomModule.ts`). This stays so until the owner decides on a real visitor registry (decision O4, still open).

**Guards.** `test/conceptOfficeData.test.ts` fails if the simulation or its example strings return anywhere in `src/`; `e2e/hq-room-modules.spec.ts` fails if an invented name appears in the live office.

## Backend integration contract (future, if O4 is decided)
Introduce server-side `visits`, `visit_events`, `visit_assignments` with tenant/workspace ID in every key, role-limited creation and transition rights, idempotency keys, monotonic revisions, server timestamps and an audited event outbox. Hosts come from the workspace's real members (the members API that `MembersView` already uses), never from a fixed list; with no members the form says "No members". A visit moves to `escorting` only after a real host accepts the handover. Visitor privacy controls, a retention policy, reception roles and customer consent are required before real use.
