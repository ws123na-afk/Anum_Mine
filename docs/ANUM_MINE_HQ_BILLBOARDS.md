# ANUM MINE headquarters: building billboards

Date: 26 October 2026, updated 27 October 2026 (quiet campus and live boards; then boards, signs and labels: brand header, logo, department icons, near and far faces, cheap boards on the lowest tier). Web only (`apps/web`). No API, contract or migration change.

The owner asked that each building around the campus has its own billboard that says what the building does and shows its work details. A billboard is two things that belong together: a board in the 3D campus in front of the building, which now carries the live details itself, and a card in the page (an HTML overlay) with the full details for the building that is focused, hovered or selected. The campus is calm by default: the boards say it, the page only has a few small chips (see [labels](ANUM_MINE_HQ_LABELS.md#quiet-campus)).

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
| `purpose` | "What it does" | A fixed sentence per kind (`hqBillboard.purpose.executive`, `business`, `department`, `personal`, `career`; a typed map, `PURPOSE_KEYS`, so a kind without its sentence is a build error), or "Its building type is not set yet" when the owner has not set one. The sentences describe the kind and never claim activity |
| `floors` | "3 floors" | `CampusBuilding.floors` (registry floors of the building) |
| `roomCount` | "7 rooms" | `CampusBuilding.rooms`, else the rooms of the building's floor plan |
| `departments` | Departments, up to 4 then "+N more" | `teamDepartment` of the building's registry rooms, only departments the catalogue names (`team.dept.*`), never a guessed or raw machine name |
| `roomNames` | Rooms, up to 5 then "+N more" | `label` and `labelAr` of the building's registry rooms. A building without a floor plan (another workspace's) says Not connected |
| `presence` | "Agents now: 6", then Working, In meetings, Queued, Waiting for approval, Blocked, Idle (a state only when it has agents; Working always) | `CampusBuilding.totals` (the campus endpoint, or the presence index for the open workspace's registry buildings): the same numbers the campus panel and the building card use |
| `presenceTime` | "Verified 14:03:22", or "Stale: last verified ..." when older than 90 seconds, or "Verification time not reported" | `CampusBuilding.presence_verified_at` (the "Verified" time the campus panel shows) |
| `work` | "Open work": Open tasks, Waiting for approval, Blocked | The live flow read (`OfficeFlowSnapshot.items`): task items a record placed in one of the building's rooms (`location.building_id`), not done; "waiting" is `waiting` set, "blocked" is stage `blocked`. A truncated read shows "N+". Only the open workspace has a flow: any other building says Not connected; a denied read says No access; a flow whose tasks source is not connected says Not connected |
| `workTime` | "Verified ..." or "Stale: last verified ..." | `OfficeFlowSnapshot.as_of`; a failed read that kept the last answer is called stale, as the flow dock does |
| `brand` | The header band of the 3D board (behind the name) and the accent rule along its bottom, in the owner's brand colour. The band is used only when the colour passes a contrast check: the lettering takes white or the dark ink, whichever reads better, and that pair must reach 4.5 to 1 (WCAG AA). A colour that fails keeps the standard slate header and stays on the accent rule | `CampusBuilding.sign.color` (the owner's brand colour of the building's sign); a missing or invalid colour keeps the standard header and teal |
| `logo` | The owner's logo in the header of the 3D board, on a white plate, as given (never recoloured) | `CampusBuilding.sign.logoSha256` and `sign.buildingId`. The picture is the bitmap the campus sign loader already decoded from the authenticated logo route (`GET /api/v1/office-spaces/buildings/{id}/logo`, the person's own credentials, at most 256 px, the nearest 16 buildings first, see [logo signs](ANUM_MINE_HQ_LOGO_SIGNS.md)). Until it is decoded, or when it fails, the board keeps its plain header |
| `icons` | A row of icons on the 3D board (far view) and before the departments line (close view) | `teamDepartment` of the building's registry rooms (only departments of the catalogue), else the room's `kind`; one icon set (`lib/hqIcons.ts`, see [labels](ANUM_MINE_HQ_LABELS.md#icons)), at most six |
| `streamLink` | The "Live" tick on the 3D board, only while the presence stream is really connected and the numbers are fresh | The presence stream link of the page (`usePresenceLive`, link `live`); any other link (polling, reconnecting, down) shows no tick |
| `open` | The "Open building" button (its accessible name adds the building name, so it never clashes with the detail panel's "Open {name}" button) | Fixed text; hidden (and "This building is not available to you right now" shown) when the building is not accessible |

`BILLBOARD_FIELDS` in `apps/web/src/hq-billboards/billboardModel.ts` is this table in code, and a unit test fails when this file stops listing a field.

## The board in the 3D campus

- One board per building, merged into one mesh (about 150 triangles per board), two posts on plinths, a dark panel in a metal frame, lit teal edge rules, two lamp heads and a night halo. It is two to one (`BOARD_ASPECT`), 9 to 13 m wide, three metres up. Materials come from the existing kit (metal posts, concrete plinths); the faces are unlit like the facade signs and take the same night gain (about 0.9 by day, up to 2.2 at night), so they glow at dusk and night.
- **The face is a live texture.** Every board has a cell (512 x 256 px; 256 x 128 on the lowest quality tier, or on a campus with more than 16 boards, smaller past 64) of one shared canvas atlas (at most 2048 x 1024, `boardAtlasLayout`). `boardFace.ts` turns the finished billboard model into the face, `billboardTexture.ts` draws it. Nothing on the face is a person, a task title or an estimate: every line is a fixed message or a value of the model (`test/hqBoardFace.test.ts` checks every printed text against the model's texts, with poisoned inputs).
- **Header.** A band across the top carries the building name, white and large (Arabic shaped and right to left in Arabic mode, the same fonts as the facade signs). With a valid brand colour that passes the contrast check the band is that colour and the name takes the more readable of white and dark ink; otherwise the band is the standard slate with a teal rule. With a decoded logo, the logo sits at the start edge of the band on a white plate and the name moves over.
- **Near and far.** From the campus camera a board is a few dozen to a few hundred pixels wide, so far away it prints only two things, both large and high contrast: the name (up to 60 px of the 512 px design) and the key line (56 px, wrapped onto two lines at 42 px when it does not fit): "5 working, 2 in meetings", "No agents" or **Not connected** (a light grey, never a zero), with the status dot before it, plus the room icons and the Live tick. The **close view** is drawn when the building is selected or hovered, or its board is within 60 m of the camera eye (`closeBoards`, `CLOSE_M`): a smaller header, the fixed sentence for the kind (only when the owner set a type), the key line (40 px), the other states with agents ("9 agents, Idle 1, Paused 1"), "Open tasks 3, Blocked 1" (or "Open work: Not connected"), the departments with their icons, "Verified 14:03:22" (or **"Stale: last verified ..."**, in amber) and the Live tick. A far board draws again only when something it prints changes (`faceKey` ignores the detail lines it does not print).
- **Live tick.** Shows only while the presence stream is connected and the numbers are fresh.
- **Real time, drawn in place.** `startBoardFeed` reads the faces when the campus, the presence index, the flow read, the stream link, the language or the Arabic font changes, and on a slow 5 second tick (so a record that stops being confirmed turns Stale with no new data; the tick stops while the tab is hidden). `BoardAtlas.update` compares each face with the one on the canvas and draws only the boards that changed, at most once a second each (a held back board is drawn by a timer when due). The renderer uploads the same canvas again (`HqRenderer.setBoards`): the scene, the meshes and the other textures are not rebuilt. The stage reports the state in `data-board-version` and `data-board-texts` on `.hqSceneWrap` (QA only, never an input).
- **Cross-fade.** When a board's text changes, the new face fades in over the old one in 400 ms (`BOARD_FADE_MS`): the old cell is kept in a cell-sized canvas of a small reused pool (at most four boards fade at once, `BOARD_FADE_MAX`; any more change at the same moment, a language switch for example, and the rest change at once) and the cell is drawn again in 50 ms timer steps (`BOARD_FADE_STEP_MS`, no frame loop: an idle campus draws nothing). A first draw does not fade.
- Reduced motion: no fade, the new text replaces the old at once (the feed reads the setting at every change), and the Live tick is a still dot.
- Software GL stays light: one 2048 x 1024 canvas at most, one upload per change, only the changed cells drawn.
- **Quality tiers** (`lib/hqQualityTiers.ts`, `boardCell` and `boardUnlit`, read through `boardSpec`). The lowest tier (`low`, which a software rasteriser runs) and the governor's last rung (the simple materials) draw 256 x 128 cells, keep the far face for every board (the focused building's card carries the details), and draw the posts, frame, plinths and lamps with one flat unlit material (`boardFlat` in the renderer: no lighting, no shadows, dimmed at night) instead of the lit kit materials. The face itself is unlit at every tier. Medium and above draw 512 x 256 cells and the lit frame.
- Placement (`boardSites`, deterministic): in front of the building, on the side of its door (from `buildings[].door` and the placement), beside the walkway end and outside the garden beds. Each candidate is checked against every building box, the plaza and roundabout disc (`scene.plaza.outer`), the walkways (`scene.plaza.spokes`), the avenues and the other boards; the first candidate with no fault wins, else the least faulty (a building, the plaza or another board are hard faults, a path under a post is a soft one). Boards sit three metres up on posts, so a path can run under one.
- Size scales with the campus (`boardWidth`, 9 to 13 m) so a board stays readable from the framing camera of a larger campus.
- Clicking a board picks its building (it is a pick target of the building's id).
- The board mesh is added only once its atlas exists, so a face is never drawn blank or white.

## The card

- A label like the other cards (`hq-billboards/BillboardCard.tsx`), anchored above the board and placed by `lib/hqLabels.ts`. **Only the focused building has a card** (selected, hovered or keyboard focused): the unfocused compact cards of the first version are gone, because the board carries the details now. A selected or hovered building gets the full card (what it does, floors and rooms, departments, room names, presence with state counts and verified time, open work, an Open button); if the full card does not fit it gives up detail (compact: name and one line) before it covers another card or a control. A card whose building is off the stage parks inside it, invisible, and a focus brings it forward and expands it; the focused card itself (the selected or hovered building, `priority` below 0 in `lib/hqLabels.ts`) is never parked while its board is in front of the camera: it is pulled into the stage, so its Close button is always on screen (a phone's 3/4 view of the front used to leave it parked out of sight).
- **Closing.** A focused card has its own Close button (`Close billboard`, 44 px, at the end of its header): it lets go of the building, so the card and the details panel close and focus goes back to the scene. Escape does the same wherever focus is, unless another panel used that Escape (a dialog, a drawer or an open office panel call `preventDefault`, and the campus checks after every listener had the key) or the key went to a field. Clicking empty ground also lets go. A hover is a mouse hover only (`pointerType === 'mouse'`): a tap on a phone also sends mouse events, and the hover it set never ended, which kept a card open with no way to close it.
- Keyboard: the header is a button (select; press again to open); the Close and Open buttons follow it; all are in the tab order. Screen readers get a labelled group per building (`Billboard for {name}`) with the rows as a description list; the decorative glyph and dot are hidden.
- Arabic reads right to left; Latin values (an English name) are isolated by the message layer. Under 641 px the departments and room names rows are left out and the card is narrower.
- The billboards are a separate label pass (`HqStage` `extraLabels`): the building cards are placed exactly as without billboards, and the billboard pass keeps round the placed building cards, the detail panel and the camera controls. An unfocused card is not in the page at all.

## Code

| Piece | File |
|---|---|
| Model (the data truth) | `apps/web/src/hq-billboards/billboardModel.ts` |
| Sites and mesh | `apps/web/src/hq-billboards/billboardSites.ts` |
| Card and styles | `apps/web/src/hq-billboards/BillboardCard.tsx`, `hqBillboards.css` |
| Chunk entry | `apps/web/src/hq-billboards/index.ts` (one lazy chunk, loaded when a campus has buildings) |
| Hook | `apps/web/src/CampusView.tsx` (a dynamic import, the pick boxes, one opaque mesh, the halos, the labels), `HeadquartersLive.tsx` (passes the flow read) |
| Board face and texture | `apps/web/src/hq-billboards/boardFace.ts`, `billboardTexture.ts` (renderer hook: `HqRenderer.setBoards`, `RenderItem.board`, `HqStage` prop `boardAtlas`) |
| Messages | `apps/web/src/lib/messages/hqBillboard.en.ts`, `hqBillboard.ar.ts` |
| Icons | `apps/web/src/lib/hqIcons.ts` (the HQ icon set, shared with the room cards) |
| Tests | `apps/web/test/hqBillboard.test.ts`, `apps/web/test/hqBoardFace.test.ts`, `apps/web/test/hqIcons.test.ts`, `apps/web/e2e/hq-billboards.spec.ts`, `apps/web/e2e/hq-quiet-campus.spec.ts` (local renders: `hq-quiet-campus.local.spec.ts`) |

`lib/campusScene.ts` is not changed: the boards are built from the finished scene (`scene.buildings[].door`, `box`, `signIndex`, `scene.plaza`).

## Budgets

The billboard code is a lazy chunk of its own (`hq-billboards`, JS and CSS), not part of the renderer or the HQ entry chunk. The measured effect on the existing limits is in [the performance budget](ANUM_MINE_HQ_PERFORMANCE_BUDGET.md#billboards). No limit was raised.

## What is not done

- Verified on software GL only; no real GPU look was checked (the owner's Ultra tier was not available).
- From the framing camera of a large campus a board can still be only a few dozen pixels wide; the far face then shows the name and key line, and the card carries the details for the focused building.
- On the lowest tier (software GL) the board never shows the close view; the e2e spec checks the close view with the medium tier pinned.
- Only the open workspace has a flow, so another workspace's building shows Not connected for open work; the registry rooms and departments are likewise known only for registry buildings of the open workspace.
- A building whose registry has no rooms shows "No rooms in the registry yet" and no departments.

## Logo signs

The owner's own logo, brand colour and sign style are drawn on the building's sign (the facade sign atlas cell). The board takes the same logo (the same decoded bitmap, no second download) and brand colour into its header, as above; the sign style stays on the facade sign only. See [logo and brand signs](ANUM_MINE_HQ_LOGO_SIGNS.md).
