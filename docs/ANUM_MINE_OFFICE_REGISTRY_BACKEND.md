# ANUM MINE Office Registry - Production Backend

## Scope and current release state
Backend implementation on `workstream/office-production-backend`, based on integrated office modules. This branch defines the PostgreSQL schema, authenticated resource API, row-level security and transactional desk integrity. **Do not deploy or merge until PostgreSQL migration tests and the full API tests pass.** The connected management panel uses the server API; the older local registry remains an explicitly labeled ephemeral preview.

## Schema, transaction rules and isolation
Migration `0097_office_spaces` creates `office_buildings`, `office_floors`, `office_rooms`, and `office_desks`. Migration `0099_office_create_keys` adds workspace-scoped create claims after the agent meeting migration. Each row is scoped by tenant and workspace; the three-part (tenant, workspace, id) primary key allows separate businesses to reuse the same stable IDs. Children reference parents using three-part composite foreign keys, preventing cross-workspace parent pointers.

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

Create requests may include `Idempotency-Key` (8 to 200 characters, letters, numbers, period, underscore, colon or hyphen). The first successful request returns 201. Retrying the same key and canonical request in the same workspace returns the existing record with 200 and does not append another audit record. Reusing that key with another payload or resource kind returns 409. A failed create rolls back its key claim, so it can be retried after the cause is fixed. Keys are isolated by tenant and workspace and remain reserved if the resource is later deleted; retrying then returns 409. Without a key, duplicate IDs retain their existing 409 behavior.

The backend derives tenant, workspace and actor exclusively from the authenticated request context. Client-provided scope properties are rejected by strict input models.

## Connected web operations (implemented, CI verification pending)
The ANUM MINE office now opens `OfficeSpaceLiveManager.tsx` instead of the old local registry. Its authenticated API adapter `officeSpaceClient.ts` loads server records with bounded, cursor-paged reads and rejects tenant/workspace mismatches. The management UI includes building and floor creation, room creation/renaming/deletion, workstation creation/deletion, assignment to a real visible agent, vacancy, current occupancy, server reload, and error/409 messaging. Writes re-read canonical server state before declaring success.

When the PostgreSQL API is not configured, the panel shows an error and an **explicitly local, nonpersistent demo**. No write is reported saved when the API returned an error. User workspace switches invalidate cached data and block stale-scope writes. The production API rejects caller-supplied scope fields.

An existing production agent status or meeting status is not created by adding a desk. Those depend on the separate presence and multi-agent-meeting services.

## Tests
- `services/api/tests/test_office_space_api.py`: strict input schemas; memory mode fails closed.
- `apps/web/test/officeSpaceClient.test.ts`: cursor pagination, scope isolation, duplicate/invalid pages and mutation contracts.
- `services/api/tests/test_postgres_office_spaces.py`: migrated database, tenant/workspace scope, same IDs across tenants, owner-only writes, capacity constraint, stale revision and scope-separated foreign keys.
- Existing repository checks and app CI must also pass. Database tests require the PostgreSQL test fixture and migration head.

## Office builder API (migration `0105_office_builder`)

Layout revision history, restore and floor and building templates (migration `0112_layout_history`) are in [ANUM_MINE_LAYOUT_HISTORY.md](ANUM_MINE_LAYOUT_HISTORY.md). Every layout write below takes the floor lock first and records a revision when its unit of work ends.
Data and API for the owner's office builder. The visual editor in the web app (section "Web builder" below) calls these endpoints. Everything below is under `/api/v1/office-spaces`, needs PostgreSQL (memory mode answers 503) and runs in one RLS-scoped transaction with its audit record and its event.

### Decisions (owner delegated, 25 October 2026)
| Topic | Decision |
|---|---|
| Units | Metres. A floor's origin is its north west corner: x grows east, z grows south. |
| Floor bounds | Stored per floor as `width_m` and `depth_m` (1 to 1000). New floors default to 60 by 40. Floors made before this change have no bounds (unbounded, as before) until the owner sets them; setting bounds is refused while a placed room would fall outside. The server checks bounds on every placement, single or layout save. |
| Room shape | Axis aligned rectangle (`scene_x`, `scene_z`, `scene_width`, `scene_depth`). `rotation` (0, 90, 180, 270) and `door_side` (north, south, east, west) describe the interior; overlap and bounds use the footprint only. To turn a room by 90 degrees the client swaps width and depth in the same save. |
| Team link | The existing team concept is `team_models.Department` (sales, support, operations, finance, tyre_engineering, marketing, technology, research, legal, compliance, security, hr, procurement, custom), carried by `team_member_profiles.department` and `assistant_profiles.department`. There is no agent group or team table, so a room has a nullable `team_department` (one department per room; a department may have many rooms). `GET /rooms/{id}/team` returns the real counts of member profiles and enabled assistants with that department; with no link the counts are null. Nothing is invented, no member is copied onto the room, agents still sit at desks only through the assignment endpoint. |
| Permissions | Every layout edit needs `workspace:manage` (owner), as before. Assigning an agent keeps its current permission (`workspace:manage`). Reads need `task:read`. No new permission. |
| Equipment and notes | Equipment is a list of `{kind, quantity, note?}` from a fixed catalogue (monitor, display, whiteboard, projector, conference_phone, video_conference_kit, printer, server_rack, network_switch, workbench, shelving, locker, charging_station, first_aid_kit), each kind once, at most 20. Free text lives in `description` (500) and `notes` (2000). Stored as one JSON column on the room. |
| Room details | `description`, `color` (palette name: slate, forest, brass, sapphire, amethyst, crimson, teal, sand), optional Arabic name `name_ar`. |
| Arabic names | Buildings, floors and rooms each have an optional `name_ar` (1 to 100 characters, one line, no control characters; `null` clears it). Create bodies accept it, `PATCH` sets or clears it, templates and copies carry it, and the English `name` stays the stable one. See [Arabic names](ANUM_MINE_ARABIC_NAMES.md). |
| Sign | A building has an optional `sign_style` (`plain`, `plaque`, `illuminated`, `banner`), `brand_color` (`#rrggbb`) and `logo_sha256`; `PATCH` sets or clears the first two (`null` clears). The logo itself is a separate resource, `PUT`/`GET`/`DELETE /api/v1/office-spaces/buildings/{id}/logo` (raw image body, `workspace:manage`, click only, re-encoded to a PNG of at most 512 by 512, served privately with an `ETag`). See [logo and brand signs](ANUM_MINE_HQ_LOGO_SIGNS.md). |
| Templates | Five fixed server side templates: open_office, meeting_room, reception, warehouse, workshop (`GET /templates`, English and Arabic names, size, capacity, equipment). No owner saved templates yet. |
| Deleting | A building is deleted only when it has no floors, a floor only when it has no rooms, a room only when it has no desks. Otherwise 409 with a `details` list naming what blocks it. |
| Edits | Live with a revision check on every change (no draft step). A multi room save is atomic. |
| Audit and events | Every write appends an audit record whose metadata holds `before` and `after` values. A change also writes the `office.layout_changed` event in the same transaction (durable outbox row, then the workspace event stream). |

### Endpoints
| Operation | Method and path | Notes |
|---|---|---|
| Edit building | `PATCH /buildings/{id}` | name, timezone, latitude, longitude (null clears coordinates), `expected_revision` |
| Edit floor | `PATCH /floors/{id}` | name, level, `width_m` and `depth_m` together; level unique per building |
| Delete building / floor | `DELETE /buildings/{id}`, `/floors/{id}?expected_revision=N` | only when empty, 409 lists the blockers |
| Edit room | `PATCH /rooms/{id}` | name, kind, capacity, rotation, door_side, description, notes, color, name_ar, equipment, team_department. Send only what changes; `null` clears color, name_ar, team_department. Capacity below the desk count and a kind that forbids desks are refused while desks exist. |
| Edit desk | `PATCH /desks/{id}` | label; `pos_x`, `pos_z` together (metres from the room's corner, inside the room, room must be placed; both null clears) |
| Atomic layout save | `PUT /floors/{id}/layout` | `{rooms: [{id, expected_revision, x, z, width, depth, rotation?, door_side?}], desks: [{id, expected_revision, pos_x, pos_z}]}`. At most 500 rooms and 2000 desks. The final layout is validated as a whole, then everything is written or nothing is, so two rooms can swap places. Rooms not listed keep their place and still count for overlap. Each saved room and desk gets a new revision. |
| Single placement | `PATCH /rooms/{id}/geometry` | as before, now also bounds and desk checks, under the same floor lock |
| Copy room | `POST /rooms/{id}/copy` | `{id, name?, floor_id?, x?, z?, copy_desks?}`; copies kind, size, details, equipment, team link; desks only on request and never with agents; without x and z the server picks a free spot (beside, below, then a scan) or answers `NO_FREE_SPACE` |
| Room from template | `POST /rooms/from-template` | `{id, floor_id, template_id, name?, x?, z?, with_desks?}` |
| Templates and catalogues | `GET /templates` | templates, equipment kinds, colours, departments, units |
| Team counts | `GET /rooms/{id}/team` | see the team link decision |

### Errors
Validation answers 422, stale revisions and blocked deletes 409, other tenants' records 404, missing permission 403. The error envelope's `details` lists every problem with `location` (for example `rooms.meeting-1`) and a stable `type`: `ROOM_OVERLAP`, `ROOM_OUT_OF_BOUNDS`, `DESK_OUT_OF_ROOM`, `ROOM_NOT_PLACED`, `ROOM_NOT_ON_FLOOR`, `DESK_NOT_ON_FLOOR`, `STALE_REVISION`, `NO_FREE_SPACE`, `ROOM_NAME_TAKEN`, `FLOOR_LEVEL_TAKEN`, `DESK_LABEL_TAKEN`, `KIND_FORBIDS_DESKS`, `CAPACITY_BELOW_DESKS`, `BUILDING_NOT_EMPTY`, `FLOOR_NOT_EMPTY`, `ROOM_NOT_EMPTY` (the blockers use `FLOOR_PRESENT`, `ROOM_PRESENT`, `DESK_PRESENT`). A layout save reports all overlap, bounds and desk problems at once. Old overlaps between rooms nobody is saving never block a save.

### Event
`office.layout_changed`, subject = the floor id when known: payload `{action, resource_kind, resource_ids (first 50), resource_count, floor_id?}`. Ids only. Open browsers re-read the floor.

### Schema
Expand only. `office_floors`: `width_m`, `depth_m`. `office_rooms`: `rotation` (default 0), `door_side` (default south), `description`, `notes`, `color`, `name_ar`, `equipment` (jsonb array), `team_department`, with check constraints. `office_desks`: `pos_x`, `pos_z`. No new table, so the forced RLS and purge policies of `0097` already cover the columns. The previous release keeps working against the new schema. Numbers are renumbered at merge if another migration lands first.

### Arabic names (migration `0114_building_floor_name_ar`)
Expand only: a nullable `name_ar varchar(100)` on `office_buildings` and `office_floors` (rooms have had it since 0105). No backfill, no new table: the forced RLS and purge policies of `0097` already cover the columns, and the previous release keeps working against the new schema. Details, the Add Arabic names action and the web behaviour are in [ANUM_MINE_ARABIC_NAMES.md](ANUM_MINE_ARABIC_NAMES.md).

### Building signs (migration `0117_building_logo_signs`)

Expand only: three nullable columns on `office_buildings` (`sign_style varchar(12)`, `brand_color varchar(7)`, `logo_sha256 varchar(64)`, each with a check constraint) and one new table, `office_building_logos` (the re-encoded PNG, its hash, size and uploader), with forced workspace RLS, a composite scope foreign key (`ON DELETE CASCADE`) and the tenant purge policy. No backfill; the previous release never reads any of it. Details, the upload pipeline and the web behaviour are in [ANUM_MINE_HQ_LOGO_SIGNS.md](ANUM_MINE_HQ_LOGO_SIGNS.md).

### Desk seats and shift hours (migration `0115_desk_seats_shifts`)
Expand only, nullable columns: `office_desks.shift` (`day` or `night`), `seat_order` (1 to 999), `facing` (`north`, `south`, `east`, `west`), `seat_kind` (`team` or `manager`) and `table_group`; `office_buildings.shift_day_start` and `shift_night_start` ("HH:MM" in the building's time zone). Check constraints hold the values; no backfill, no new table (forced RLS and purge policies of `0097` cover them), rollback safe. Desk reads return the five seat fields; `PATCH /desks/{id}` accepts them (null clears one) and `PATCH /buildings/{id}` accepts the two hours (null on both means the unconfirmed defaults 07:00 and 19:00; two equal starts are refused). Used by [Green Concrete](ANUM_MINE_GREEN_CONCRETE.md). The layout history does not capture them yet.

### Tests
`tests/test_office_layout.py` (geometry rules, catalogue drift, request models), `tests/test_postgres_office_builder.py` (database role, atomic save, delete guards, copy, templates, audit values, event, team counts, member and cross tenant refusals). Contract types: `packages/contracts/src/officeBuilder.ts`.

### Not in the API
Owner saved templates, a separate layout editor permission, idempotency keys on copy and template creation (the client id already makes a retry a 409), copying a whole floor, free angle rooms or polygons, walls and windows.

## Remaining gates (not production complete until all pass)
1. Backend CI, database migration under non-owner app role, and PostgreSQL integration tests pass.
2. Cursor pagination is implemented (page size 1-100). Verify cursor stability under parallel edits and cover very large workspaces with load tests.
3. Office changes now emit `office.layout_changed` (see the builder section); replay through SSE for the web client and event coverage of desk assignment are still to be verified end to end.
4. Create idempotency keys are implemented in migration `0098`; stronger API versions/ETags remain. Revisions currently cover mutation concurrency.
5. The web manager and API adapter are implemented. Validate browser behavior for 409/reload, unauthorized reads, offline state, and multi-workspace switching with Playwright before release.
6. Room geometry, floor bounds and the builder API exist (builder section); walkable paths, walls and 3D scene assets remain separate features.
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

### Web builder (`apps/web/src/hqBuilder/`)
The owner's visual builder. Entry: headquarters, Manage spaces, **Open visual builder** (`OfficeSpaceLiveManager`). Help article: [Office builder](help/office-builder.md). It uses only the endpoints above plus the existing assignment endpoint; there is no new API and no browser storage of layouts.

| Topic | How it works |
|---|---|
| Who edits | The role from `GET /api/v1/workspace-memberships/current`: only `owner` (the holder of `workspace:manage`) gets edit controls. Anyone else gets the same plan read only with the reason. If the role cannot be read, editing stays off. The server checks every write anyway, and a 403 is explained on screen. |
| Draft | Moves, resizes, rotations, door side and desk positions are staged in memory (`draftModel.ts`). A dirty indicator shows the number of staged items. **Save layout** sends one `PUT /floors/{id}/layout` with only the changed rooms and desks, each with the revision it was loaded at. **Discard** drops the draft. Closing the page loses an unsaved draft on purpose. |
| Undo and redo | A history of at most 100 steps; repeated key presses on one room within 0.7 seconds share a step. Control or Command with Z, Shift with Z, or Y. Revisions are kept outside the history, so an undo never brings back an old revision. |
| Live checks | `geometry.ts` and `validate.ts` mirror `office_layout.py`: overlap (edges may touch), floor bounds, desk inside its room. A problem that involves a room being saved blocks the save; an old overlap between rooms nobody is saving is shown as a warning and never blocks, like the server. |
| Rotate | Swaps width and depth around the room's centre (pulled back inside the floor when it fits), turns `rotation` and `door_side` a quarter turn clockwise, and turns the desks inside so they stay in the room, all in the same layout save. |
| Immediate actions | Create from template, blank room, copy, delete, room details (`PATCH /rooms/{id}`), desk add, rename, delete, seat or unseat an agent, floor and building edits and deletes. Each merges into the draft without losing it. A blank room is created unplaced and given a free spot as a staged change. A template or copy gets a free spot computed from both the saved and the staged layout. Moving an agent between desks is an unseat then a seat, each with its desk revision. |
| Stale revision | A 409 `STALE_REVISION` keeps the draft and shows **Reload and reapply my changes**: the latest records are loaded and each staged change is put back only where the server still has the version the draft started from; items someone else moved or deleted take the server's version and are listed (`rebase` in `draftModel.ts`). The owner reviews and saves again. |
| Errors | `explain.ts` maps every stable `details[].type` code to a plain sentence in English and Arabic (overlap, out of bounds, desk outside room, no free space, taken names, blocked deletes with the server's list of what remains). A code the client does not know shows the server's own text. |
| Team counts | `GET /rooms/{id}/team`; with no linked department or on any failure the panel says **Not connected**. |
| Large floors | The plan is SVG; only a moved room redraws; validation sweeps along x; the room list is searchable and paged by 60. Tested with 500 rooms. |
| Input | Mouse and touch drag (a touch moves a room only once it is selected, and the plan stops panning while something is selected), eight resize handles, 0.5, 1 or 2 metre snap or none. Keyboard: arrows move, Shift with arrows resizes, R rotates, square brackets cycle rooms, Control or Command with S saves; changes go to a polite live region. |
| Tests | Unit: `apps/web/test/hqBuilderGeometry.test.ts`, `hqBuilderDraft.test.ts`, `hqBuilderApi.test.ts`. Browser: `apps/web/e2e/hq-builder.spec.ts` (main path, overlap, stale revision, permissions, keyboard, Arabic, 500 rooms, axe) and `hq-builder-responsive.spec.ts` (five viewports, touch). The API mock `e2e/support/hqBuilderApi.ts` applies the server's own rules. |

Not built yet: other open browsers are not told live (the page does not subscribe to `office.layout_changed`; a change by someone else shows on save as a stale revision, or on **Reload from server**); alignment guides and rulers; walls, windows and free angle rooms; copying a whole floor; a separate layout editor permission (owner decision).

### Starter campus
`POST /api/v1/office/starter-campus/confirm` (preview, click only, `workspace:manage`) creates the standard campus through the same store paths as the builder: `create` for buildings and floors, `create_planned_room` (floor lock, `_place` bounds and overlap checks) for rooms and their empty desks. Records get stable ids from template keys (`campus-01-hq-...`), so a re-run adds only what is missing and never changes an existing record; one transaction per building, audit action `office.starter_campus.building`, event `office.layout_changed`. No agents are created or seated. See [Starter campus](ANUM_MINE_STARTER_CAMPUS.md). `POST /api/v1/office/staff-desks/confirm` (one building per call, click only, `workspace:manage`) then seats a paused agent at each empty desk through the same store paths; see [Staff my campus](ANUM_MINE_STAFF_DESKS.md).
