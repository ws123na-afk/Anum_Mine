# ADR-0003: Office projection and live agent meetings

## Status

Proposed on 22 October 2026 by the headquarters readiness workstream (F01). It records what `main`
already does and the boundaries the remaining headquarters work must keep. It changes no behaviour.
Owner decision needed on the three open points at the end.

## Context

ANUM MINE is the virtual headquarters in the web app (`#office-preview`). The master plan
(`docs/ANUM_MINE_VIRTUAL_HEADQUARTERS_MASTER_PLAN.md`) asks for a truthful office: no status without a
persisted record, no cross-tenant data, and live meetings between agents that are real saved turns.
`main` already holds three separate layers:

- an office registry (buildings, floors, rooms, desks with room geometry) in PostgreSQL behind
  `/api/v1/office-spaces` (migrations 0097, 0099 create keys, 0100 room geometry);
- live agent meeting sessions, participants and turns (migration 0098, `/api/v1/agent-meetings`);
- a web office that joins the registry with agent and task evidence in the browser
  (`HeadquartersController`, `headquartersProjection.ts`, `officePresence.ts`).

## Decision

1. **Projection, not a second source of truth.** The office is a read model. Rooms and desks come from
   the registry; presence comes from the canonical agent, run, task, approval and meeting records. The
   web never stores a status of its own and never invents one. Where evidence is missing the office
   shows "state not available", never idle.
2. **Presence is derived on the server once it is served.** Today the browser derives presence from
   `GET /agents` and `GET /tasks` (`officeWorkstations.ts`). The target is one read-only presence
   snapshot per workspace, with provenance, last verified time and a stale flag, plus deltas from the
   existing workspace event stream (`GET /api/v1/events/stream`, `Last-Event-ID`). The browser keeps the
   snapshot and applies deltas; a full snapshot is the recovery path after any gap. No presence write
   endpoint exists.
3. **One event stream per tab and scope.** The office reuses the workspace stream. It does not open a
   stream per avatar. When the tenant or workspace changes, the stream is re-opened for the new scope and
   every response of the old scope is discarded (`HeadquartersController` generation counter, tested in
   `apps/web/test/headquartersTenantRace.test.ts`).
4. **Live meetings are persisted turn-taking, not a simulation.** A meeting is a row set (session,
   participants, turns, leases). A turn is written to PostgreSQL before any event announces it. Limits
   (turns, duration, tokens, cost, participants) are checked in the domain layer on every turn. The office
   shows speaking only while a real turn is being generated or streamed. Agents get no tools inside a
   meeting; any later tool use goes through the existing runtime mediation and approvals.
5. **Meetings never reach across tenants.** A participant must be an agent or member of the same
   workspace. A cross-workspace summary for the owner is a separate feature (F10) that needs an explicit,
   audited grant and returns minimum data; it is never a join.
6. **Renderer.** The shipped renderer is a small hand-written WebGL pass with a 2D canvas fallback
   (`HeadquartersWebGL.tsx`, `HeadquartersCanvas.tsx`), about 40 kB gzip for the whole lazy chunk. The
   master plan suggested React Three Fiber. Keep the current renderer until a measured need appears
   (materials, instancing, glTF): the budget in `docs/ANUM_MINE_HQ_PERFORMANCE_BUDGET.md` is the test for
   switching. Never add a second 3D library next to it.
7. **Tables.** Every new office, presence, meeting and executive table: forced RLS on tenant and
   workspace, composite scope foreign keys, expand-only migrations, a purge policy for the tenant
   lifecycle job. `services/api/tests/test_postgres_hq_rls.py` discovers tables by prefix and fails when
   one lacks these.
8. **Rollback.** The route is behind the build flag `VITE_ANUM_HQ` (`off` hides the route and link).
   Migrations are expand-only, so the previous release keeps running against the new schema
   (`docs/ANUM_MINE_HQ_DEPLOYMENT_AND_ROLLBACK.md`).

## Consequences

- The browser keeps a bounded amount of derivation until the presence API lands. That derivation is
  tested and honest (unknown stays unknown), but it costs three list requests per refresh.
- Meeting transcripts are as visible as tasks (`task:read`) until a transcript policy exists (see
  `docs/ANUM_MINE_HQ_SECURITY_REVIEW.md`, finding S-03).
- A 3D engine swap is possible later because the renderer reads only `SceneFloor` data
  (`headquartersProjection.ts`), not the network.

## Open owner decisions

1. Who may read meeting transcripts: every task reader (today), meeting participants and managers, or
   managers only.
2. A workspace-wide daily meeting budget (cost and turns) on top of the per-meeting caps.
3. Whether the presence snapshot is a polled read model only, or also pushes deltas over SSE in the first
   release (the plan assumes deltas).
