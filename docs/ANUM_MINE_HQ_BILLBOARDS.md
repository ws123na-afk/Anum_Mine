# ANUM MINE headquarters: building billboards

Date: 26 October 2026. Web only (`apps/web`). No API, contract or migration change.

The owner asked that each building around the campus has its own billboard that says what the building does and shows its work details. A billboard is two things that belong together: a board in the 3D campus in front of the building, and a card in the page (an HTML overlay) with the details.

## The rule: a billboard shows only what a real record says

Every string on a billboard is one of two things:

1. a fixed message of the catalogue (`hqBillboard.*`, plus `hq.status.*`, `hq.kind.*`, `hq.building.*`, `hqNav.card.*`) in English and Arabic: row headings, one sentence per building kind, "Not connected";
2. a field of a real record, listed in the table below.

Nothing is estimated, no trend, no person, no task title, no customer, no meeting text, no prompt. A value that is not available says **Not connected** (or **No access** when the server said so), never a zero that implies data. A value that is old says **Stale** and when it was last verified. A registry count of zero rooms or no agents is real data and is shown as such ("No agents", "No rooms in the registry yet").

Tests keep this true (`apps/web/test/hqBillboard.test.ts`): the model is read through a recording proxy and fails when it touches a property outside `BILLBOARD_READS`; poisoned inputs (people, agent names, task titles, room ids, workspace names) must never appear in any string; a seeded property test removes each feed in turn and checks that the matching row says "Not connected".

## What a billboard shows, and where each field comes from

| Field id | Shown as | Source record |
|---|---|---|
| `name` | Board lettering and card title, Arabic name in Arabic mode | `CampusBuilding.name` and `nameAr` (office registry building `name`, `name_ar`; `lib/registryNames.ts`) |
| `kind` | The building type under the title | `CampusBuilding.kind`, only when `kindKnown` is not false (the owner's building type; a registry building has none, so none is shown) |
| `purpose` | "What it does" | A fixed sentence per kind (`hqBillboard.purpose.executive`, `business`, `department`, `personal`), or "Its building type is not set yet" when the owner has not set one. The sentences describe the kind and never claim activity |
| `floors` | "3 floors" | `CampusBuilding.floors` (registry floors of the building) |
| `roomCount` | "7 rooms" | `CampusBuilding.rooms`, else the rooms of the building's floor plan |
| `departments` | Departments, up to 4 then "+N more" | `teamDepartment` of the building's registry rooms, only departments the catalogue names (`team.dept.*`), never a guessed or raw machine name |
| `roomNames` | Rooms, up to 5 then "+N more" | `label` and `labelAr` of the building's registry rooms. A building without a floor plan (another workspace's) says Not connected |
| `presence` | "Agents now: 6", then Working, In meetings, Queued, Waiting for approval, Blocked, Idle (a state only when it has agents; Working always) | `CampusBuilding.totals` (the campus endpoint, or the presence index for the open workspace's registry buildings): the same numbers the campus panel and the building card use |
| `presenceTime` | "Verified 14:03:22", or "Stale: last verified ..." when older than 90 seconds, or "Verification time not reported" | `CampusBuilding.presence_verified_at` (the "Verified" time the campus panel shows) |
| `work` | "Open work": Open tasks, Waiting for approval, Blocked | The live flow read (`OfficeFlowSnapshot.items`): task items a record placed in one of the building's rooms (`location.building_id`), not done; "waiting" is `waiting` set, "blocked" is stage `blocked`. A truncated read shows "N+". Only the open workspace has a flow: any other building says Not connected; a denied read says No access; a flow whose tasks source is not connected says Not connected |
| `workTime` | "Verified ..." or "Stale: last verified ..." | `OfficeFlowSnapshot.as_of`; a failed read that kept the last answer is called stale, as the flow dock does |
| `open` | The "Open building" button (its accessible name adds the building name, so it never clashes with the detail panel's "Open {name}" button) | Fixed text; hidden (and "This building is not available to you right now" shown) when the building is not accessible |

`BILLBOARD_FIELDS` in `apps/web/src/hq-billboards/billboardModel.ts` is this table in code, and a unit test fails when this file stops listing a field.

## The board in the 3D campus

- One board per building, merged into one mesh (`billboardMesh`, 134 triangles per board), two posts on plinths, a dark panel, lit teal edge rules, two lamp heads and a night halo. It is drawn with `MAT.sign`, so it is lit at night like the facade signs.
- Lettering: the board is textured with the building's own cell of the sign atlas, so it reads "ANUM MINE" over the building name, shaped right to left in Arabic mode (the same code as the facade sign, `drawSignAtlas`). Static lettering is the building name and nothing else.
- Placement (`boardSites`, deterministic): in front of the building, on the side of its door (from `buildings[].door` and the placement), beside the walkway end and outside the garden beds. Each candidate is checked against every building box, the plaza disc, the walkways, the ring path and the other boards; the first candidate with no fault wins, else the least faulty (a building, the plaza or another board are hard faults, a path under a post is a soft one). Boards sit three metres up on posts, so a path can run under one.
- Size scales with the campus (`boardWidth`, 9 to 13 m) so a board stays readable from the framing camera of a larger campus.
- Clicking a board picks its building (it is a pick target of the building's id).

## The card

- A label like the other cards (`hq-billboards/BillboardCard.tsx`), anchored above the board and placed by `lib/hqLabels.ts`. A selected or hovered building gets the full card (what it does, floors and rooms, departments, room names, presence with state counts and verified time, open work, an Open button); if the full card does not fit it gives up detail (compact: name and one line) before it covers another card or a control. An unfocused card shows compact when the camera is within about 85 metres of the board, and is a dot when crowded. A crowded or off stage card parks inside the stage, invisible, and a focus brings it forward and expands it.
- Keyboard: the header is a button (select; press again to open); the Open button enters the building; both are in the tab order. Screen readers get a labelled group per building (`Billboard for {name}`) with the rows as a description list; the decorative glyph and dot are hidden.
- Arabic reads right to left; Latin values (an English name) are isolated by the message layer. Under 641 px the departments and room names rows are left out and the card is narrower.
- The campus stage now passes `labelKeepOut` (the detail panel and the camera controls), so no label card, building or billboard, is placed under them.

## Code

| Piece | File |
|---|---|
| Model (the data truth) | `apps/web/src/hq-billboards/billboardModel.ts` |
| Sites and mesh | `apps/web/src/hq-billboards/billboardSites.ts` |
| Card and styles | `apps/web/src/hq-billboards/BillboardCard.tsx`, `hqBillboards.css` |
| Chunk entry | `apps/web/src/hq-billboards/index.ts` (one lazy chunk, loaded when a campus has buildings) |
| Hook | `apps/web/src/CampusView.tsx` (a dynamic import, the pick boxes, one opaque mesh, the halos, the labels), `HeadquartersLive.tsx` (passes the flow read) |
| Messages | `apps/web/src/lib/messages/hqBillboard.en.ts`, `hqBillboard.ar.ts` |
| Tests | `apps/web/test/hqBillboard.test.ts`, `apps/web/e2e/hq-billboards.spec.ts` |

`lib/campusScene.ts` is not changed: the boards are built from the finished scene (`scene.buildings[].door`, `box`, `signIndex`, `scene.monument`), so a new central plaza only needs to pass its extra round areas as `avoid` in `boardSites` (and move `PLAZA_CLEAR`).

## Budgets

The billboard code is a lazy chunk of its own (`hq-billboards`, JS and CSS), not part of the renderer or the HQ entry chunk. The measured effect on the existing limits is in [the performance budget](ANUM_MINE_HQ_PERFORMANCE_BUDGET.md#billboards). No limit was raised.

## What is not done

- Verified on software GL only; no real GPU look was checked.
- The 3D board carries only the name. The details are the card (they need real, changing text), so they are not on the board itself.
- Only the open workspace has a flow, so another workspace's building shows Not connected for open work; the registry rooms and departments are likewise known only for registry buildings of the open workspace.
- A building whose registry has no rooms shows "No rooms in the registry yet" and no departments.
- The board is not drawn as an unlit shape under reduced quality tiers; it is one cheap mesh at every tier.
