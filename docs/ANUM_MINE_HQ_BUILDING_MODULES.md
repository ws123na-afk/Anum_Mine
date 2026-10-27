# ANUM MINE headquarters: building modules, the command centre room kind, the personal office and floor transitions

Status: built in this change (API, one migration, contract, web, tests). Web and API only; the mobile apps and the help articles are not touched.

This change closes four gaps of the [completion matrix](ANUM_MINE_FEATURE_COMPLETION_MATRIX.md): a real command centre room kind (2.4, 7.2), each building showing its own modules (4.4), the personal office drawn as the villa (4.2), and eased floor changes with a clearly marked active floor (3.5). It also audits every room of the [starter campus](ANUM_MINE_STARTER_CAMPUS.md) against the room to module table (14.3).

## 1. The command centre is a room kind

`command` joins the room kinds (`office, meeting, reception, workshop, warehouse, lounge, command`). It is not an office with a clever name:

| Where | What |
|---|---|
| API | `office_layout.ROOM_KINDS` and `DESK_ROOM_KINDS` (operator desks are allowed), the `RoomKind` literal, the template `command_center` (14 by 10 metres, six desks, monitors, four displays, a network switch, crimson) and the provisioning work rooms (`office_provisioning.WORK_ROOM_KINDS`: an agent can be seated at a command desk) |
| Database | Migration `0113_room_kind_command` widens `ck_office_room_kind` only. Every value the previous release writes stays valid, so a rollback of the application keeps working against this schema. No data is rewritten. Down-migration (test workspaces only) turns command rooms into offices first |
| Contract | `OFFICE_ROOM_KINDS`, `OFFICE_DESK_ROOM_KINDS`, `OFFICE_TEMPLATE_IDS` in `packages/contracts/src/officeBuilder.ts` |
| Builder | The room type list, the "Add from a template" list (the template names come from the API), labels in English and Arabic (`hqb.kind.command`, `hq.roomKind.command`, `hqSc.kind.command`) |
| 3D | The room is dressed as the operations room whatever it is called (`interiorKindFor('command')` is `operations`: desks, the video wall); the floor and accent colours have their own entry |
| Module | `lib/hqRoomModule.ts` `KIND_MODULES.command` opens the live flow (the same module as the operations department). A stored department still wins, so a command room owned by finance opens Finance |
| Wall | The operations wall of [the live scene](ANUM_MINE_HQ_LIVE_SCENE.md) attaches to any room whose module is the live flow, so it now follows the kind: in the room inspector and as the board over the selected room |
| Starter campus | The "Command Center" of ANUM HQ is created with kind `command` (six desks, the wall equipment) |

Rooms made before this change keep their kind. A starter-campus "Command Center" that is already an office stays one (the starter campus never rewrites rooms); its decoration by name still works as before, and the owner can change its type in the builder, which is audited and revision checked like every room edit. The name only chooses decoration for an `office` room; for a `command` room it chooses nothing.

## 2. Each building shows its own modules

`lib/hqBuildingModules.ts` (pure) decides the panels under the 3D scene:

1. **Starter campus buildings** are known by the stable id their record carries (`campus-02-commerce`): ANUM HQ and the corporate buildings (Corporate Services, Knowledge & AI, New Ventures, Personal Office, Career & Employment) show **Team status** and the **ANUM OS wall** (open tasks, blocked work, approvals waiting, running work from `GET /office/flow`; agents by state from the presence list). ANUM 360 Commerce shows **Live operations** and **Current flow** (orders today, waiting to ship, late, **low stock**, open alerts, returns). Tyre Pulse shows **Tyre fleet** (vehicles, **tyres fitted**, **tyres in stock**, below minimum, overdue inspections, due in 30 days).
2. **Any other building** is read from the departments stored on its rooms: tyre engineering adds the tyre panel, sales and support add the commerce panel.
3. **A building with neither** keeps the two original panels (commerce and tyre), so nothing that was there before goes missing.

Data truth: the numbers come from the existing endpoints of the **open workspace** (`/commerce/setup` and `/commerce/dashboard`, `/tyre/setup` and `/tyre/dashboard`, `/office/flow`, `/office/presence`). They are the workspace's, not split per building: the starter campus puts all eight buildings in one workspace, so the Commerce building and the Tyre building each read their module for that workspace. A source that is not set up says **Not connected** with a link to its setup, a role without access says **No access** and who can see it, an error says what failed; no number is estimated. The low stock and tyres in stock figures are shown only when the answer carries them.

## 3. "Open module" for every room

The room to module table (`lib/hqRoomModule.ts`) already covered Marketing (`#marketing`), Technology (`#developer`), Research (`#knowledge`) and Legal (`#files`) on `main`; the matrix was stale there. This change verifies the whole starter campus with a unit test over all 62 rooms (`test/hqBuildingModules.test.ts`, fixture `test/fixtures/starterCampusRooms.json` that an API test keeps equal to the master list):

* Every room resolves to a view that exists in the shell, the live flow, a meeting, or an honest "no module" with a reason. Exactly two have none: the **Reception** (there is no visitor register, owner decision O4) and **Procurement** (no procurement module exists).
* Thirteen rooms had no stored department (the decisions are in the table below). Only the **Executive Office** now has one: `executive`, a new storable department (migration `0116_room_dept_executive`, expand only, no backfill), so it opens the executive desk by its stored department and no longer by the room name. The other twelve stay empty and open a generic module (Tasks, Calendar); the owner sees "set a department" and can store one in the builder. The list of twelve is pinned by the test so a new room cannot slip in unnoticed.

| # | Starter room (building) | Decision | Reason |
|---|---|---|---|
| 1 | Executive Office (ANUM HQ) | **Set to `executive`** | The name leaves no doubt and the department exists since 0116. Opens the executive desk by department, in any building kind |
| 2 | Facilities Office (ANUM HQ) | Left empty | Operations, security or procurement are all arguable; a judgment call for the owner |
| 3 | AI Organization (ANUM HQ) | Left empty | Technology, research or operations; the agent organisation is not one department |
| 4 | AI Labs (Knowledge & AI) | Left empty | Research or technology |
| 5 | Data (Knowledge & AI) | Left empty | Research, technology or operations |
| 6 | Automation (Knowledge & AI) | Left empty | Technology or operations |
| 7 | Future Businesses (New Ventures) | Left empty | No department matches new ventures |
| 8 | Career Office (Career & Employment) | Left empty | A personal career room, not the company HR department |
| 9 | Employment Desk (Career & Employment) | Left empty | HR would be a guess: it may be the owner's own employment |
| 10 | Personal Office (Personal Office) | Left empty | No department matches a personal office |
| 11 | Terrace (ANUM HQ) | Left empty | A lounge; no department applies |
| 12 | Events Hall (ANUM HQ) | Left empty | A lounge; no department applies (marketing events would be a guess) |
| 13 | Lounge (Personal Office) | Left empty | A lounge; no department applies |

Existing campuses are not rewritten. The starter planner's one existing exception still applies: when the owner re-runs **Set up my campus**, reviews the plan and clicks confirm, a master list room whose department is empty or `custom` gets the master list department, shown in the plan as `department_set` and audited, so an existing Executive Office adopts `executive` through that click and never silently. A department the owner chose is never replaced. Procurement is already wired to its department (`procurement`); there is still no procurement or supplier module, so it keeps its honest "No module connected" message.
* A defect found by the audit is fixed: the name pattern for support matched the word "care" inside "Career", so the Career Office opened Inbox and support. It now matches the whole word.
* Rooms of a **registry building** (the starter campus HQ is drawn as the executive tower) are decided by department, kind and name only. The executive tower rule ("every room opens the executive desk") applies to a workspace's own building kind, never to a building of the office registry, so Facilities and the Terrace do not open the executive desk (`moduleBuildingKind`).

## 4. The personal office

The starter campus already creates **Personal Office** (building 7, `campus-07-personal`: a personal office and a lounge, two desks) in the same reviewed, click-confirmed flow as every other building (`Set up my campus`, `workspace:manage`, `input_mode: "click"`, payload hash). It is ticked by default, it is skipped when it exists, and nothing creates it without that click. This change makes the campus draw it as a person's building: a registry building whose stable starter id says `personal` has the kind `personal` and is drawn as the **villa** (`archetypeOf`); ANUM HQ stands as the executive tower, Tyre Pulse as the saw-tooth workshop, ANUM 360 Commerce as the glazed shop pavilion (`campusRegistry.registryLook`). The look follows the id the starter campus wrote, never a name. The card still shows no building type for a registry building (the registry holds none), so nothing is labelled that the owner did not classify.

## 5. Floor transitions

`BuildingView` builds the scene in cut-away mode (`buildBuildingScene` option `cutaway`): the active floor is drawn in full as before; every other floor is a **ghost floor** of its own mesh, built once and cached by floor record (selecting a room or changing a desk state does not rebuild them). The renderer lifts each ghost floor in or out with an eased motion (`hqThreeRenderer` `startLifts`, 360 ms, cubic in and out, `LIFT_MS`, `easeLift`): a floor above the active one is cut away by collapsing it flat onto its own level and hiding it, a floor below (or every floor with "All floors") comes back the same way. The floor the person just left starts from fully shown, so it eases away instead of vanishing. At rest the cost is the same as before: a collapsed floor is not drawn and casts no shadow.

* **Active floor highlight**: the open floor is outlined in a bright line along its near sides (the other floors are dimmed), and the floor menu shows "N / M".
* **Camera**: a change of floor glides the camera to the new floor with the same easing (`BuildingView` `glide`, no announcement of its own, ended by the person's own camera input); the Inside view keeps its own camera. The floor menu's clipped controls (shown while the floor rail is open) now unclip only for keyboard focus on the buttons or the menu, so "All floors" can be clicked.
* **List view**: the list now shows the floors and rooms of every building of the campus (it showed none once the starter campus's eight buildings were expanded), and opening a room opens its building first (`openRoomById`).
* **Controls**: the previous and next floor buttons (toolbar and floor control, disabled at the ends), the floor menu, and **Page Up / Page Down** on the building (ignored while a field has focus or a modifier key is held). A polite live region announces "Showing Floor 2, floor 2 of 3."
* **Reduced motion**: floors change at once, the camera jumps, nothing animates.
* **Cost**: the renderer chunk is 154,879 bytes gzip (limit 160,000), the lazy headquarters chunk 131,231 (limit 135,000), CSS 11,542 (limit 17,000). The ghost floors are simple shells (slab, room floors, walls); the furnished interiors are built only for the active floor. The e2e `moving floors keeps the frame time bounded` reads the renderer's average draw time under software GL.

"Only this floor" (hide everything else) is the existing default: floors above the active one are cut away and the floors below are shown as dimmed shells; "All floors" shows the whole stack.

## 6. Tests

API: `test_office_layout.py` (kind, desk kinds, template), `test_office_starter_campus.py` (the command kind, the fixture), PostgreSQL as the app role in `test_postgres_office_builder.py` (the kind is stored after the migration, a command room keeps its desks, an unknown kind is refused, the template creates a command room with six desks).

Web unit: `test/hqBuildingModules.test.ts` (panels per building, the 62 room audit (the Executive Office opens `#executive` by its stored department; the twelve undecided rooms are pinned), looks, command kind), `test/hqFloorCutaway.test.ts` (cut-away scene, ghost cache, lifts, easing), `test/hqRoomModule.test.ts` (existing table, still green).

Browser: `e2e/hq-building-modules.spec.ts` (each building's panels with real numbers, Not connected and No access, the command centre room with the wall and the flow dock, floor buttons, keyboard, reduced motion, frame time, Arabic, accessibility scan, five viewports 1440x900, 1366x768, 1024x768, 768x1024 and 390x844).

## 7. Not done, said plainly

* The panel numbers are per workspace, not per building: two buildings in one workspace read the same commerce or tyre figures.
* A starter-campus "Command Center" created before this change stays an office until the owner changes its type; there is no bulk migration of room kinds.
* The Reception still has no module (no visitor register) and Procurement none (no module exists); twelve starter rooms have no department and use a generic module until the owner sets one (each is a judgment call listed above).
* The camera glide uses the existing fly-to; the floors themselves collapse flat rather than sliding away. No isolate-one-floor toggle beyond the cut-away and "All floors".
* Frame times were measured with software GL only; a run on a real GPU or device is still missing (matrix 3.10).
* Tyre Pulse's saw-tooth look needs the local drawing hint `workshop`; it is not stored and not a building type the owner can pick.

## Tyre Pulse on the 3D wall

In a building whose panels include the tyre panel (Tyre Pulse), the 3D Operations wall also draws the figures of the Tyre fleet cards (Vehicles, Tyres fitted, Tyres in stock, Below minimum, Overdue inspections, Due in 30 days) from the same dashboard read, with the same labels; with the feed off it says "<building name>: Not connected". See [live screens](ANUM_MINE_HQ_LIVE_SCREENS.md).
