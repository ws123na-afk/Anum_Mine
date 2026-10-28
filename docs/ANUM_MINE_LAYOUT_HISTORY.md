# ANUM MINE layout history, restore and templates

Rows 8.6, 11.6, 11.8 and 14.10 of the [feature completion matrix](ANUM_MINE_FEATURE_COMPLETION_MATRIX.md).
Builder context: [Office registry backend](ANUM_MINE_OFFICE_REGISTRY_BACKEND.md) and the owner help page `docs/help/office-builder.md`.

## What it does

* Every committed change to a floor's layout (rooms, desks, positions, rotation, doors, equipment, floor size) writes one
  **append-only snapshot** in the same transaction as the change. A failed or refused save writes nothing.
* The owner can list past versions of a floor, preview any version with a diff, and **restore** one.
* A floor or a whole building can be saved as an owner-named **template**, and a new floor or building can be made from one.

Agents are never part of a snapshot or a template. A restore changes the layout, not who sits where.

## Storage (migration `0112_layout_history`, expand only)

| Table | Purpose |
|---|---|
| `office_floor_revisions` | Primary key `(tenant_id, workspace_id, floor_id, revision_no)`. `snapshot` (rooms, desks with their seat fields, floor size, the building's shift hours, sorted by id, no agents; see "Seats and shift hours" below), `snapshot_hash` (SHA-256 of the canonical content), `summary` (counts of added, removed, moved and changed rooms and desks), `action`, `actor`, `restored_from` and `restored_over`. A trigger refuses `UPDATE`; only the cap prune deletes. Foreign key to the floor with `ON DELETE CASCADE`, so deleting an empty floor removes its history. |
| `office_layout_templates` | `(id, tenant_id, workspace_id)`, `kind` (`floor` or `building`), unique name per kind and workspace, `definition` JSON (rooms with position, size, rotation, door, equipment, department, and desk labels with positions), `revision` for optimistic edits. At most 100 per workspace. |

Both tables have forced workspace RLS (`tenant_isolation_*`) and the purge policy and grants of migration 0097, and both are in
`anum_api/tenant_lifecycle_tables.py` (export and purge). The previous release never reads them, so a rollback is safe.

### Retention

`ANUM_OFFICE_LAYOUT_REVISION_CAP` (default 50, 5 to 500) is the number of revisions kept per floor. After each insert the rows
older than `head - cap` are deleted in the same transaction, so the newest `cap` revisions always remain and the head is never pruned.

## How a revision is written

All layout writers (`PUT /floors/{id}/layout`, room and desk create, patch, delete, geometry, copy, template rooms, floor patch)
take the **floor lock first**, so there is one lock order and no deadlock between a desk edit and a layout save. The first change
to a floor that has no history writes a `baseline` revision of the state **before** that change, so even the first tracked change
can be undone. Each request records its floors once, when its unit of work ends and before it commits
(`_store` in `office_space_api.py`). A change that leaves the layout identical to the head (for example an assignment, which is not a layout
action) writes nothing.

### Seats and shift hours (snapshot version 2)

Version 2 snapshots (`"v": 2`) add each desk's seat fields (`shift`, `seat_order`, `facing`, `seat_kind`, `table_group`, migration
`0115_desk_seats_shifts`) and `shifts` (`shift_day_start`, `shift_night_start` of the floor's building), all in the content hash. A restore
puts them back exactly, re-creating a removed desk with its seat (and no agent) and setting the building's hours (the building row is
locked after the floor, as every restore locks). The diff counts `seats_changed` (desks whose seat fields changed and nothing else) and
`shift_hours_changed`; both are in the restore audit summary as counts. A version 1 revision never recorded seats or hours: it is compared
with the floor through its own fields only (`as_version_of`), so it still restores exactly what it holds and leaves seats and hours as they
are. Hours are one pair per building: restoring a revision of one floor restores them for the whole building. No migration was needed.

## API (`/api/v1/office-layout`, `office_layout_history_api.py`)

| Route | Permission | Notes |
|---|---|---|
| `GET /floors/{floor}/revisions?before=&limit=` | `task:read` | Newest first, `head_revision`, `cap`, `can_restore`, paging by `next_before`. |
| `GET /floors/{floor}/revisions/{n}?against=current\|previous` | `task:read` | The diff (rooms added, removed, moved, changed; desk counts), a mini plan, the **unassign list**, `payload_hash`. |
| `POST /floors/{floor}/revisions/{n}/restore` | `workspace:manage` | Body: `payload_hash`, `expected_head`, `confirm_unassign`, `input_mode`. |
| `GET /templates?kind=` | `task:read` | Summaries only. |
| `POST /templates/from-floor`, `POST /templates/from-building` | `workspace:manage` | Owner named. Rooms that are not placed on the plan are left out and counted. |
| `PATCH /templates/{id}`, `DELETE /templates/{id}?expected_revision=` | `workspace:manage` | Rename and delete with an optimistic revision. |
| `POST /floors/from-template` | `workspace:manage` | New floor in an existing building. Keeps the template's Arabic floor and room names; a new English `name` needs its own `name_ar` (the old Arabic name would no longer fit). |
| `POST /buildings/from-template` | `workspace:manage` | New building with all the template's floors, each with its Arabic name. The building takes the optional `name_ar` of the request. |

### Restore rules

1. **Click only.** `input_mode` must be `click`. Typed, voice and assistant requests get 403 before anything runs.
2. **The person must have been shown what they confirm.** `payload_hash` is SHA-256 over the action, tenant, workspace, **user**, floor,
   revision number and the revision's content hash. A hash shown to someone else, or for another revision, is refused with 409
   `PAYLOAD_HASH_MISMATCH`.
3. **Conflict.** `expected_head` is the head revision number the person saw. If the floor changed meanwhile the answer is 409
   `STALE_LAYOUT` and nothing changes; the web reloads the history.
4. **Occupied desks.** If the restore would remove a desk (or a room with desks) that has an agent, the server computes the list under
   lock and refuses with 409 `UNASSIGN_NOT_CONFIRMED` (and the list) until `confirm_unassign` is exactly that set of desk ids. The agent
   profile stays in the workspace; only the desk and its seat are removed. A private agent shows as "a private agent".
5. **Exact.** Rooms and desks are re-created with their original ids, names swap safely (placeholders inside the transaction), the
   floor size is restored, and the result is checked against the target's content hash. A mismatch rolls everything back (`RESTORE_MISMATCH`).
6. **New revision, never a rewrite.** Success appends revision `head + 1` with `restored_from` and `restored_over`. Older revisions stay,
   so a restore is undone by restoring the revision it came from.
7. **Idempotent.** A retry with the same `expected_head` after a success returns the same result with `replayed: true`. Restoring a
   version the floor already matches writes nothing (`noop: true`).
8. **Audited.** `office.floor.restore` with before and after counts, the revision restored, the payload hash, `input_mode: click`, the summary
   and the ids of the unassigned desks; the usual `office.layout_changed` event is published after the commit.

Errors carry stable codes in `details[].type` and fixed messages. Exception text is never returned.

## Templates

A template holds layout, never agents. Applying one creates the floor, its rooms (through the builder's own bounds and overlap checks) and
**empty** desks with the template's positions, in one transaction. Room and desk ids are generated from the new floor id (`<floor>-r1`, `<room>-d1`).
Names are unique per kind in a workspace (409 `TEMPLATE_NAME_TAKEN`); a floor level already used in the building is 409.

## Web (`apps/web/src/hqBuilder/`)

Two new tabs in the builder panel, loaded lazily (they are separate chunks, so the entry and the HQ performance budget do not grow):

* **History** (`HistoryTab.tsx`): versions with who, when and a summary; a preview with a mini plan (`MiniPlan.tsx`) that marks rooms that come back, are removed, move or change,
  with text and chips as well as colour; "If restored now" and "Changed in this version" views; paging; **Restore** opens `RestoreDialog.tsx`.
  The dialog lists the changes and the desks that would lose an agent and needs a tick for that list; Escape closes it and focus returns. Restore is disabled while the plan has unsaved changes.
* **Templates** (`TemplatesTab.tsx`): save the floor or the building under a name, apply (new floor with a level, new building with a time zone), rename, delete.

States: loading, empty, error with retry, denied (explains that only a workspace owner restores or manages templates; viewers can still read), service down, and conflict (409 with a reload button).
English and Arabic (right to left), keyboard and screen reader labels, five viewports.

## Tests

* API unit: `tests/test_office_layout_history.py` (snapshot, hash, diff, plan, payload binding, migration shape), `test_tenant_lifecycle.py`.
* PostgreSQL as the app role: `tests/test_postgres_layout_history.py` (atomic snapshot with the save and no revision for a refused save, exact restore, deleted rooms and desks coming back, name swaps, click only,
  hash and head checks, occupied desks, idempotency, pruning, append only trigger, tenant isolation and RLS, templates).
* Web unit: `test/hqLayoutHistory.test.ts`. Browser: `e2e/hq-layout-history.spec.ts` with the mock `e2e/support/hqLayoutHistoryApi.ts`.

## Extension points

* A new layout action: add its audit action name to `LAYOUT_ACTIONS` in `office_layout_history.py`; every `_audit` call with a floor then records a revision.
* A new column on rooms or desks: add it to `ROOM_FIELDS` or `DESK_FIELDS` (it becomes part of snapshots, diffs and restores) and bump `SNAPSHOT_VERSION` if old snapshots lack it.
* Whole-office template export and import as JSON: the definition is plain JSON (`capture_floor`, `build_floor_from_definition`), so an export route is a read of `definition`
  and an import is a validated insert through `_insert_template`.

## Not built here

* Whole-office template JSON export and import (optional in the brief).
* Applying a template to an existing floor (it creates a new floor or building).
* History does not cover building level or workspace level changes (building rename, new building); only floors.
* A restore does not touch the "Staff my campus" ledger (`office_desk_staffing`), the same as deleting a desk today.
* Revisions made before this migration do not exist; each floor starts with a `baseline` at its first tracked change.
* The seat fields of a desk (shift, place at the table, facing, seat type, table group, migration 0115) and the shift hours of a building are not part of a revision: a restore keeps a desk's current seat fields, and a desk a restore recreates has none ([Green Concrete](ANUM_MINE_GREEN_CONCRETE.md)).
