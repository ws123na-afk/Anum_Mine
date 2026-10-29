# ANUM MINE: campus, live presence, workstation work and executive overview (backend)

Status: **built on branch `claude/hq-data`, tested locally (unit and PostgreSQL), CI pending.** Covers master plan F03 (presence), F05 data (work panel), the data side of workstream 2 (campus) and the backend of F10 (executive command centre). No UI is part of this change; the visual and executive agents build on the contracts below.

Contracts (wire format snake_case, `version: 1`): `packages/contracts/src/officeCampus.ts`, `officePresence.ts`, `officeWorkstation.ts`, `executive.ts`. Web adapters (no UI): `apps/web/src/lib/officeCampusClient.ts`, `officePresenceClient.ts`, `executiveClient.ts`. A new optional field keeps `version: 1`; a breaking change bumps the version and ships beside the old one for one release.

## Truth rules

- A state, count or name appears only if a persisted record backs it. Concept names and figures from design renders are never returned.
- `idle` means "no active run, task, approval or meeting record was found at `last_verified_at`". It is never a guess. An agent is never marked busy without evidence.
- Every workspace is read in its **own** tenant and workspace RLS context with the application role: one short session per workspace, never a cross-tenant or cross-workspace query, no BYPASSRLS, no new SECURITY DEFINER.
- Personal, employer and business workspaces are never pooled. Data of two tenants never meets (the token names one tenant).

## Endpoints

All need an active membership in the request's workspace (`x-workspace-id`). The memory backend answers 503 (no fake success).

### `GET /api/v1/office/campus` (permission `task:read`)
One building per workspace where the caller has an **active membership** and `task:read` (read through the narrow `anum_membership_reader`; a workspace the person cannot access is not listed). Up to 50 buildings (`truncated: true` beyond).

```json
{ "version": 1, "tenant_id": "...", "current_workspace_id": "...", "generated_at": "ISO", "truncated": false,
  "buildings": [{ "workspace_id": "...", "name": "...", "kind": "executive|business|department|personal|career|null",
    "icon": "building|briefcase|store|truck|home|user|landmark|factory|shield|chart|null", "profile_revision": 1,
    "role": "owner", "archived": false, "accessible": true,
    "floors": [{ "id": "...", "building_id": "...", "level": 0, "name": "..." }],
    "office_buildings": 1, "rooms": 3, "desks": 8,
    "agents": { "total": 5, "by_state": { "working": 1, "queued": 0, "meeting": 0, "waiting_approval": 0, "blocked": 0, "idle": 4, "paused": 0, "unknown": 0 }, "off_shift": 0 },
    "presence_verified_at": "ISO|null", "presence_as_of": "ISO" }] }
```
Shifts ([Green Concrete](ANUM_MINE_GREEN_CONCRETE.md#shifts)): an agent seated at a `day` or `night` desk whose shift is not in force now (the building's stored `shift_day_start` and `shift_night_start`, else 07:00 and 19:00, in the building's time zone) is counted in `off_shift` and not in `by_state`; `total` still counts every agent. `presence_verified_at` reads only the people on shift. The web treats a missing `off_shift` (an older server) as zero, and shows the count as **Off shift** in every total.

`kind` and `icon` are null until an owner sets them; nothing is guessed. A workspace that fails to read is listed with `accessible: false`, `agents: null` and zero counts.

### `GET /api/v1/office/campus/boards` (permission `task:read`)
The billboard facts and signs of every other workspace the caller may read (open work counts, a rooms and departments summary, the owner's sign of each registry building), each read in that workspace's own tenant and workspace RLS context with the caller's role there. Paged (`limit` at most 16, `cursor`), cached 15 seconds per workspace, user and role, memberships read fresh every call. The open workspace is never in it. See [billboards: every building](ANUM_MINE_HQ_BILLBOARDS.md#every-building). Tests: `test_office_campus_boards.py`, `test_postgres_office_campus_boards.py`.

### `PUT /api/v1/office/campus/profile` (owner, `workspace:manage`)
Body `{ "kind", "icon", "expected_revision"? }` for the **current** workspace. First save: no `expected_revision`. Later saves must send the current revision (409 when stale). Audited (`office.campus.profile`). Answers `{ workspace_id, kind, icon, revision }`.

### `GET /api/v1/office/presence` (permission `task:read`)
Snapshot: `{ version, workspace_id, as_of, cursor, totals{state:n}, items[] }`. Item: `{ key, agent_id, agent_name, desk_id, state, provenance{kind,run_id,task_id,approval_id,meeting_id,reason}, task{id,title}|null, last_verified_at, stale }`. `key` is the agent id, or `desk:<id>` for a desk whose agent is private to someone else (state `unknown`, reason `agent_not_visible`). `cursor` is the newest domain event id (use it as `Last-Event-ID`).

Derivation (server side, `office_presence.py`, pure function of records), highest priority first:

| State | Evidence required |
|---|---|
| `paused` | `agent_profiles.status = paused` |
| `meeting` | an `agent_meeting_sessions` row with status `active` and the agent as participant |
| `waiting_approval` | task `waiting_approval` plus a `pending` approval that has not expired |
| `working` | task `running` plus the task's latest run `running`, updated within the last hour |
| `blocked` | task `waiting_approval` whose approval expired or is missing; or a task `failed` within 24 hours |
| `queued` | task `queued` |
| `idle` | none of the above |
| `unknown` | a `running` run silent for over 1 hour (`run_silent`); or a desk with a hidden agent |

Cancelled and completed work proves nothing and reads as idle. `stale` is true when the evidence is older than 5 minutes (render greyed out).

### `GET /api/v1/office/presence/stream` (permissions `task:read`, `event:read`; SSE)
Frames: `event: snapshot` (always first, also after a reconnect with `Last-Event-ID`; full snapshot plus `sequence: 0`) then `event: delta` `{ version, sequence, as_of, totals, changed[], removed[] }` with only what changed. `id:` is the newest event cursor. A `sequence` gap means reconnect. Keep-alive comment about every 15 s.
Why duplicate and out-of-order events are safe: the stream uses events only as a hint to re-read, and the state always comes from the records, so a duplicate changes nothing and an old event cannot move state backwards. It also re-reads every 10 s, because expiry and staleness are time based. **No database session is held between polls** (CLAUDE.md streaming rule): each poll opens a short session in a worker thread and closes it. Client disconnect ends the loop. Limit: polling once a second per open stream; the NATS hub is not used yet (see Not done).

### `GET /api/v1/office/workstations/{desk_id}/work` (permission `task:read`)
`{ version, workspace_id, as_of, desk, agent{id,name,status}|null, presence|null, reason: null|"unassigned"|"agent_not_visible", tasks[], approvals[], errors[], documents[] }`. Tasks: the agent's 10 newest non-archived tasks with the latest run (phase, checkpoint version, last step id, up to 15 newest steps) and `links` to the existing task APIs (`/api/v1/tasks/{id}`, `/runs`). Prompts and tool arguments are never returned. `approvals` need `approval:read`, `documents` (files the agent's runs retrieved, taken from the `retrieval` step sources, infected files excluded) need `file:read`; otherwise they are empty. `errors` come from failed runs (their recorded result). Unknown desk, other workspace or other tenant: 404.

### Executive grants and overview (`/api/v1/executive/...`)
Owner management (`workspace:manage` in the **granted** workspace):

| Call | Effect |
|---|---|
| `POST /grants` `{grantee_user_id, scopes[], expires_at?}` | Creates a **pending** grant, returns `payload_hash`, `approval_required: true`. Gives no access. One live request per person (409). Request valid 72 h. `expires_at` at most one year ahead. |
| `POST /grants/{id}/approve` `{payload_hash, reason?}` | Governed step: activates only when the hash equals the one shown (recomputed from the stored content, constant time compare; 409 otherwise). Replaces an earlier active grant for the same person (`superseded`). With the workspace two-person rule on, the requester cannot approve their own request (403). |
| `POST /grants/{id}/reject` | Rejects a pending request. |
| `PATCH /grants/{id}` `{expected_revision, scopes?, expires_at?}` | Narrow an active grant at once (subset of scopes, earlier expiry). Widening is 409: create a new request. |
| `DELETE /grants/{id}` | Revoke (idempotent, 204). |
| `GET /grants?limit=` | The workspace's grants with derived `status` (`expired` when due). |

Scopes: `counts` (buildings, floors, rooms, desks, agents by state; shared agents only), `tasks` (open task counts by status, blocked agents), `approvals` (pending count, oldest pending time), `cost` (this month's estimated USD, calls, unpriced calls), `meetings` (active, scheduled, paused counts). Titles, prompts, names, transcripts, documents and approval texts **never** leave the workspace.

`GET /api/v1/executive/overview` returns `{ version, tenant_id, home_workspace_id, generated_at, truncated, workspaces[] }`. Items: `access: "granted"` (with `grant_id`, `scopes`, `expires_at`, `summary` holding only the granted scopes), `access: "not_granted"` (workspaces where the caller is a member and has no grant, `open_with: "workspace_switch"`; no summary), or `access: "unavailable"` (the read or its audit failed; nothing returned). The caller needs an active membership in the request's workspace but **not** in the granted workspaces.

How the grants are found without a cross-tenant query: a short transaction runs `SET LOCAL ROLE anum_executive_reader` (NOLOGIN, migration 0101): column grants `id, tenant_id, workspace_id, grantee_user_id, scopes, status, expires_at, revoked_at` and one policy that admits only the caller's own **active, unexpired, unrevoked** grants in the token's tenant. Then each granted workspace is opened in its own RLS context, the grant is re-checked there, and an `executive.overview.read` audit record (actor = the executive, `grant_id`, scopes) is written into **that workspace** before any data is returned. If the audit write fails the workspace item is `unavailable`. The owner of the granted workspace sees every read in the audit log.

## Presence and shifts

Presence knows nothing about shifts: it is derived from records, per agent. The web adds the shift on top (`lib/hqShifts.ts`): a person whose desk belongs to the shift that is not in force is drawn **Off shift** with a dark screen even when a record says work is waiting, and the card says **Off shift, work waiting** with the real record's title and its `last_verified_at`. Room and building label cards count only people on shift as working; the campus card counts every agent from the presence list. See [Green Concrete](ANUM_MINE_GREEN_CONCRETE.md).

## Data and migration `0101_executive_grants`

Expand only (new tables and one new role; nothing existing changes). Chained on `0100_office_room_geometry` (file `0100_office_room_geometry.py`); numbers are renumbered at merge (data agent 0101, meetings 0102, executive 0103).

- `campus_workspace_profiles` (tenant, workspace, kind, icon, revision, updated_by): forced RLS by tenant and workspace.
- `executive_grants`: forced RLS by tenant and workspace, check constraints (scopes subset of five names, 1 to 5 scopes, hash shape, active needs approver), one active grant per person and workspace (partial unique index), index on grantee.
- Both tables: lifecycle purge policy (`anum_tenant_purger`), registered in `tenant_lifecycle_tables.py`.
- Role `anum_executive_reader`: created by the migration if missing and in `infra/helm/bootstrap-database.sql`; granted to the API login `WITH INHERIT FALSE, SET TRUE` and reached only with `SET LOCAL ROLE`, so its policy never widens the application role's own queries (tested).

## Compatibility matrix

| Consumer | Needs | Notes |
|---|---|---|
| Visual agent (campus view, desks, avatars) | `GET /office/campus`, `/office/presence`, stream, `/office/workstations/{id}/work`, contracts above | Existing `/api/v1/office-spaces` unchanged. `agent_id` on a desk may be null for a private agent: use presence `key`. |
| Executive agent (command centre) | `/executive/overview`, `/executive/grants*` | Show "not granted" items as such; opening one is a workspace switch. |
| Meetings agent | none required | `meeting` state reads `agent_meeting_sessions` status `active` + participants. |
| Old web or API release | none | New tables unused by the previous release; extra role ignored. |

## Tests

- `services/api/tests/test_office_presence.py` (no database): every state, stale, silent run, hidden agent, diff, stream snapshot/delta, duplicate and out-of-order events, reconnect, disconnect, grant hash and strict bodies.
- `services/api/tests/test_postgres_office_hq.py` (PostgreSQL as the non-owner role): campus per active membership and isolation, profile owner-only and revision, presence from records and tenant isolation, expired approval, private agent hidden, meeting and pause, work panel real data and 404 across tenant and workspace, grant flow (pending gives nothing, wrong hash 409, minimum data, audit per read, revoked, expired, superseded, narrow vs widen, two-person rule, cross-tenant), reader role columns and inheritance, forced RLS.
- `apps/web/test/officeHqData.test.ts`: adapters, scope checks, delta reducer, SSE parsing, leak refusal.
- Existing `test_tenant_lifecycle.py` and `test_migrations.py` cover the new tables and revision chain.

## Rollback

`helm rollback` never reverses migrations; 0101 is expand only, so the previous release runs against the new schema. To disable the feature, stop the clients calling the endpoints (or remove the routers); the data is kept. `alembic downgrade 0100_office_room_geometry` drops `executive_grants` and `campus_workspace_profiles` and removes the reader policy and grants: use it only in an empty test workspace; in production restore from backup instead of deleting real grants. The role `anum_executive_reader` is left in place (shared role, no login).

## Not done (and why)

- **Grants are not rows of the `approvals` table.** That table binds every approval to a task run and a tool call and the runtime resumes the run when it is decided; a grant has neither. The grant flow uses the same contract (SHA-256 payload hash shown, hash returned, owner only, two-person rule, audit) in its own table. Owner decision: keep it, or add a task-less approval kind later.
- The presence stream polls the event log once a second; it does not use the NATS hub yet (the polling path is the existing fallback and is correct; the hub only saves latency and reads).
- No check that `grantee_user_id` names a real person (identities live in the identity provider). A grant to an unknown id exposes nothing.
- No UI, no Flutter, no e2e browser test: those belong to the visual and executive workstreams.
- Presence for agents is per workspace; a person-level "everything I am doing" view is not built.
- Load test at 200+ agents / 1000+ desks is still open (queries are bounded to 2000 rows per kind).

## On the 3D monitors

A desk monitor reads the presence record behind its desk (`status`, `stale`, `last_verified_at`) and the live flow item the flow places at the desk (`since`, `waiting.since`). Working and meeting show a scan line only while the record is fresh; a shift desk off shift is dark. Elapsed time counts from the real start of the record; a record without a start shows none. See [live screens](ANUM_MINE_HQ_LIVE_SCREENS.md).

## Career building kind (27 October 2026)

Owner decision O2: `career` is its own campus kind. `PUT /office/campus/profile` accepts it (migration `0118_career_visitor_register` widens `ck_campus_profile_kind`; the previous release shows an unknown kind as the business look), and the campus draws it as the career centre. See [reception and career](ANUM_MINE_HQ_RECEPTION.md).
