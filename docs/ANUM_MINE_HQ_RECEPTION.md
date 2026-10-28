# ANUM MINE headquarters: reception visitor register and the career building

Owner decisions of 27 October 2026, recorded as answered in the [feature completion matrix](ANUM_MINE_FEATURE_COMPLETION_MATRIX.md):

- **O2: career is its own building kind**, not a personal workspace. It is the fifth campus kind next to executive, business, department and personal.
- **O4: Reception is a real visitor register.** The old reception simulation stays deleted ([retired module](ANUM_MINE_RECEPTION_MODULE.md)); no visitor is ever simulated or invented.

## The visitor register

### What the person sees

- **Opening it**: Office tools, "Reception: visitors"; or a reception room's panel (room inspector or the room list), "Open the visitor register". Opened from a room, the add form starts with that room.
- **The panel** is a side drawer from 761 px and a bottom sheet below, like the [history panel](ANUM_MINE_HQ_HISTORY_PANEL.md) (the docked panel rules of the [page layout](ANUM_MINE_HQ_PAGE_LAYOUT.md): nothing is drawn over the scene). Focus moves to its close button, Tab stays inside, Escape closes a delete question, then the form, then the panel, and focus goes back to the button that opened it.
- **Today by default**: visits expected today, checked in today, or still checked in from an earlier day. "All dates" shows the whole register.
- **Status filters with counts**: All, Expected, Checked in, Checked out, Cancelled. The counts are the server's for the dates and search in force.
- **Search** (name, company or purpose), sent after a short pause in typing.
- **Paging**: 25 visits a page with a keyset cursor, "Load more visitors", "Showing N of M visitors", and "End of the list for these filters" only when the server has no cursor left.
- **Owners** add a visitor (name, company, purpose, expected time, host, room; "Already here: check in now" for a walk-in), edit an open visit, check in, check out, cancel an expected visit, and delete a record after a confirmation.
- **Hosts** are the workspace's active members (`GET /workspace-members`, owners only) and the agents the office already shows; the server checks the host again. A host or room that no longer exists is shown as such, never guessed.

### States

| State | What it says |
|---|---|
| Loading | "Reading the visitor register..." with a spinner; the panel keeps its place |
| Empty | "No visitor is expected today and nobody is checked in." (or "The visitor register is empty.") with "Add a visitor" for an owner, and who can add for anyone else |
| Filtered empty | "No visitor matches these filters." with "Clear the filters" |
| Error | What failed and "Try again"; loaded rows stay |
| Permission denied (read) | Who to ask for a role with read access |
| Not an owner | The list is readable; "Only a workspace owner can add, check in or change visitors" and no write buttons |
| Stale revision | "Someone changed this visit a moment ago", then the list reloads |
| No PostgreSQL on the server | The register needs PostgreSQL; nothing fake is shown |
| Long lists | Paging and search, above |

### The 3D reception room

A room of kind `reception` shows only the number of visitors the server counts as checked in there now (`GET /office/visitors/summary`, counts only, never a name): its room card in the building view leads with "1 visitor checked in" or "No visitors checked in", and the room panel shows the same line. A visit counts at its own room; a visit with a building and no room counts at that building's reception room only when the building has exactly one; a visit with neither counts only when the workspace has exactly one reception room. Anything else stays in the panel and is never spread over rooms. With no answer (no permission, no PostgreSQL, offline) the room shows no visitor line at all. The count is read when the open office has a reception room, every minute while the tab is visible, and after each change in the panel (`useVisitorSummary.ts`, rules in `lib/hqReceptionCount.ts`).

### API

Under `/api/v1/office/visitors` (`services/api/anum_api/office_visitors_api.py`, contract `packages/contracts/src/officeVisitors.ts`). PostgreSQL only; the memory backend answers 503, never fake rows.

| Route | Permission | What it does |
|---|---|---|
| `GET /` | `task:read` | Page of visits: `q`, `status`, `since` and `until` (both or neither), `limit` (at most 100), `cursor`. Answers `items`, `next_cursor`, `total`, `counts` per status and `can_manage` |
| `GET /summary` | `task:read` | Checked-in counts by room, by building (no room) and unplaced |
| `POST /` | `workspace:manage`, click | Add a visit; optional client `id` makes a retry answer the stored visit (200) and a different visit under that id 409; `arrived: true` checks a walk-in in |
| `PATCH /{id}` | `workspace:manage`, click, revision | Change an open visit; a finished one answers 409 |
| `POST /{id}/check-in`, `/check-out`, `/cancel` | `workspace:manage`, click, revision | Expected to checked in, checked in to checked out, expected to cancelled; any other move 409 |
| `DELETE /{id}` | `workspace:manage`, click, revision | Remove the record |

Governance follows the other owner-only office writes (the builder and the [logo signs](ANUM_MINE_HQ_LOGO_SIGNS.md)): `workspace:manage`, `input_mode: "click"` (a typed or spoken answer is refused with 403 and no registered agent tool calls these routes), and the `expected_revision` the person was shown (409 when stale). No payload hash is used: a visit has no cost, creates no agent and is undone by a further click, unlike the reviewed batch confirms of [Staff my campus](ANUM_MINE_STAFF_DESKS.md). Refusals are answered in Arabic for `Accept-Language: ar` (`localization.py`). Times are server times; the expected time must carry a time zone and lie within 400 days of today. Text is trimmed, inner spaces collapsed and control characters refused.

### Storage and privacy

Migration `0118_career_visitor_register` (expand only) adds `office_visitors`: tenant, workspace, id, optional building and room (composite foreign keys, so another workspace's building or room can never be linked; deleting the building or room clears only that column with `ON DELETE SET NULL (column)`), visitor name, optional company, optional host (`user` or `agent` and its id), purpose, expected, checked in, checked out and cancelled times, status, revision, created and updated by and at. Check constraints tie the times to the status. Forced row level security by tenant and workspace, the tenant purge policy (organisation and workspace deletion), and an entry in `tenant_lifecycle_tables.py` (the organisation export carries the register, the purge deletes it).

Audit (`office.visitor.create`, `update`, `check_in`, `check_out`, `cancel`, `delete`) and the event `office.visitors_changed` (after the commit) carry ids, statuses, revisions, the host kind and id, whether a company was given and, for an update, the names of the changed fields. Never the visitor's name, company or purpose, in audit, events or logs. The history panel names these actions in English and Arabic.

## The career building kind

- **Contract and API**: `CampusBuildingKind` gains `career` (`packages/contracts/src/officeCampus.ts`), `PUT /office/campus/profile` accepts it, and the migration widens `ck_campus_profile_kind`. The previous release reads `career` as an unknown kind and draws the plain business look, so a rollback is safe.
- **Builder**: the building type form of the campus (Building type: Executive, Business, Department, Personal, Career) with a graduation cap icon; English "Career", Arabic "المسار المهني".
- **Starter campus**: every master list building carries `campus_kind` in the preview; Career & Employment (`campus-08-career`) is `career` and the dialog shows the kind beside each building. The web maps the same id to the career look (`campusRegistry.ts` `STARTER_LOOK`).
- **3D look and sign**: the career kind is always drawn as the career centre, whatever icon is saved: two learning wings with ribbon windows and amber sunshade reveals, a glass barrel-vault entrance hall between them, and a free-standing sign pylon on the forecourt carrying the building's lettered sign or the owner's logo sign (`hqAssets/exterior/careerCentre.ts`, triangle cap 2,600; the simple tier draws a matching block in `campusBuildings.ts`). Accent colour amber `#e8a24a`.

## Checks

- API: `services/api/tests/test_office_visitors.py` (routes, 503 without PostgreSQL, viewer refused, text cleaning, cursor, click only, career kind, starter kinds) and `test_postgres_office_visitors.py` (migration, every route, paging, search, day window, host and place checks, set null on room delete, audit and events without names, Arabic refusal, another tenant sees and changes nothing, including direct SQL as the app role).
- Web: `test/hqReception.test.ts` (query, parsing, counts per room, the room line, form rules and bodies, actions, client paths, career look and starter mapping, no example people) and `e2e/hq-reception.spec.ts` (owner journey with the 3D count, check in, cancel, edit, delete, stale revision, paging and search, viewer, denied, error and retry, opening from a reception room, axe, Arabic). `e2e/hq-room-modules.spec.ts` checks that the reception room opens the register and that no simulated names return.

## Not done

- **No automatic retention.** Records stay until an owner deletes them. A purge after a set number of days needs the owner's choice of period (decision O5 covers retention windows) and a maintenance job through `anum_maintenance`.
- **No host notification** when a visitor is checked in, and no visitor badge printing. Neither was part of decision O4.
- **Registry buildings carry no stored kind.** Only a workspace has a campus kind; the eight starter buildings take their look from their stable starter id, so a hand-made registry building cannot yet be marked as a career building.
- **The 3D count refreshes once a minute** (and after the panel's own changes); the `office.visitors_changed` event is not yet pushed into the presence stream.
