# ANUM MINE headquarters: campus, building cutaway and extension points

Web only. Code: `apps/web/src/HeadquartersLive.tsx` (container), `CampusView.tsx`, `BuildingView.tsx`, `CampusListView.tsx`, `HqStage.tsx` (WebGL stage and label cards), `HqChrome.tsx` (scene controls), `HqPanels.tsx`, and the pure libraries `lib/campusModel.ts`, `campusScene.ts`, `buildingScene.ts`, `campusBuildings.ts`, `hqProps.ts`, `hqGeometry.ts`, `hqMath.ts`, `hqCamera.ts`, `hqLighting.ts`, `hqTextures.ts`, `hqRenderTypes.ts`, `hqThreeRenderer.ts`, `hqPresence.ts`, `hqModules.ts`, `hqExtensions.ts`. Visual reference: the owner-approved concept renders (screens 1 to 4); they are concept art, not data.

## What the person sees

- **Campus**: one building per authorised workspace around the ANUM MINE monument. The Executive HQ tower stands in the centre when the campus data has a building of kind `executive`. Each building has a label card (icon, name, "N agents, M working", status dot, avatar stack). Click a label to select and fly to it, click again (or use the detail card) to open it. Opening a building in another workspace asks the app to switch workspace first; nothing is read across workspaces.
- **Building cutaway**: floor selector, "Back to campus", counters, rooms as zones with label cards, desks with agent cards (name, department, status pill), the glass partitions and furniture of each room kind. Geometry is the saved room geometry (`scene_x/z/width/depth`); rooms without it use the deterministic grid layout from `headquartersProjection.ts`.
- **Controls**: view switch 3D, Top, List; compass, zoom, reset, day and night; rotate left and right, previous and next (building on the campus, floor in a building), full screen; minimap with the current view outline; totals panel with a donut.
- **List view** is the accessible list of every building and, for the open workspace, every floor and room. It is also what shows when WebGL is missing (the building level falls back to the 2D floor plan renderer).
- The app's own sidebar and top bar are the only navigation. `#office-preview` (and `#office`) open the view inside that shell. The "Authenticated office / Concept preview" switch stays; the concept preview is the old illustrative tour and says so.

## Data truth

| On screen | Source | Without a source |
|---|---|---|
| Buildings | `GET /api/v1/office/campus` (contract `officeCampus.ts`, one per authorised workspace; `kind` null stays "not set" until an owner saves the building type with `PUT /office/campus/profile`); a deployment without it falls back to the caller's workspace memberships | Building omitted |
| Registry buildings on the campus | The open workspace's office registry (`GET /api/v1/office-spaces/buildings`, floors, rooms, desks). With two or more registry buildings (the starter campus creates eight) each is its own campus building with its own card, floors and rooms; the workspace's single building is replaced by them (`lib/campusRegistry.ts`, `expandCampus`). Their card says the workspace name, shows no building type (the registry has none; it is drawn as a plain business building), and counts the agents assigned to its desks with the state of their presence record (an assigned agent without a record is Unknown; without presence the card reads "Presence unavailable"). Other workspaces stay one building each and their registry is never read | One registry building or none: the workspace is the single building as before |
| Agent counts, statuses | `GET /api/v1/office/presence` (states with provenance and `stale`; a private agent is `desk:<id>`); without it the browser reads workstation evidence (agents plus open tasks) and participants of `active` agent meetings | "Presence unavailable"; a status those sources cannot tell (meetings when the list failed) shows "-" |
| Desk work panel | `GET /api/v1/office/workstations/{desk}/work` (tasks with run phase, checkpoint and steps, approvals, errors, documents) | Task titles from presence, with a note |
| Meeting room state | `liveMeetingRoomIds` prop supplied by the meetings workstream; sessions are not bound to rooms in the current API | The room shows no meeting state |
| Live operations, current flow | Commerce setup and dashboard of the open workspace (orders, waiting, late, low stock, alerts, returns); shown by the buildings that sell (see [building modules](ANUM_MINE_HQ_BUILDING_MODULES.md)) | "Not connected" with the setup link, "No access", or the error |
| ANUM OS wall | `GET /office/flow` and the presence list of the open workspace; shown under ANUM HQ and the corporate buildings | "Not connected" per tile |
| Tyre panel | Tyre setup and dashboard of the open workspace | Same |

A queued task reads as Queued, never as working. Workspaces other than the open one show no counts until the campus endpoint supplies verified presence.

## Navigation layer (`apps/web/src/hq-nav/`)

A layer around the scene that makes it easy to move between offices. It holds no data of its own; `HeadquartersLive` hands it the campus, the open building's floors, the presence index and a few callbacks, and it composes with the Team panel, the flow dock, provisioning, the builder and the PA dock.

| Part | What it does | Source of truth |
|---|---|---|
| Floor rail (`FloorRail.tsx`) | Floors of the open building, top first, with a thumbnail (a scene snapshot once taken, else a plan drawn from the saved room geometry), room types, rooms and assigned agents. Active state, arrow keys, collapse to a slim rail, a drawer under 900 px | `floorEntries` over the projection |
| Quick views (`ViewStrip.tsx`) | Campus aerial, one stop per non-archived building (locked when not accessible), then rooms of the open building: every room up to 8, otherwise the first room of each room kind, at most 12. Current stop outlined, Collapse, arrow keys | `stripStops`, `roomStops` |
| Campus cards (`CampusView.tsx`) | Tags from real fields (building type only when an owner set it, floors, rooms, one department for the open workspace), agent count, initials of the open workspace's agents. Density rule: 6 or fewer buildings all full; more: the selected, open and executive buildings stay full, then the busiest up to six, the next to fourteen are compact (name and count), the rest a dot. Nothing is removed | `cardDensities`, `cardTags` |
| Side panel (`SidePanel.tsx`, `PanelSection.tsx`, `OfficeStatus.tsx`) | Total agents, quick moves, the PA dock; inside a building the floor plan with the 2D (top-down) and 3D switch and the people at their posts. The selected room or building docks at its top. Each panel has the shared header with a live chip and a fold button ([page layout](ANUM_MINE_HQ_PAGE_LAYOUT.md)). A drawer under 1280 px | `planModel`, `peopleAtPosts` |
| Camera (`flight.ts`, `cameraBridge.ts`) | Eased fly-to: duration 450 to 1800 ms from the travel (square-root curve), shortest yaw, a gentle rise over the ground, extra height wherever the eye would sit inside a building box. Reduced motion jumps. A pointer, wheel or key press on the stage ends a flight. One requestAnimationFrame loop runs only while flying | pure, unit tested |
| Thumbnails (`thumbs.ts`) | The stage hands its canvas to `ThumbCapture` inside the renderer's frame callback; a 192 by 108 JPEG is kept in memory and in this tab's sessionStorage, keyed by tenant, workspace, a layout signature, the place and day or night. A place not yet seen has none and the strip shows an icon | `HqStage` `onCanvasFrame` |
| Live region | "Moving to X." then "Now showing X." (or "Showing X." when the camera jumped) | bridge events |

Several buildings in one workspace: the card key of a registry building is `<workspace id>~<building id>` (`CampusBuilding.realWorkspaceId` and `registryBuildingId` say what it is). Opening one selects that registry building (floor rail, plan, people and room stops follow it); opening a building of another workspace still switches workspace. `hqCommands.navigateTo({ building })` matches registry buildings by name or key; a target that also names a floor or room of a registry building other than the open one only opens the building. The total on the campus is the sum of the cards, so an agent with no desk is in no building (the building view's Team status still counts it).

Truth rules: the strip, rail and plan list only saved records; the people list shows an agent only when a desk assignment or a presence record exists and says "Not connected" or "Presence unavailable" otherwise; a private agent shows no name; counts say "agents" (they are agents, not employees). Thumbnails are snapshots of the scene or icons, never stock images. Presence never implies someone is physically there.

Wiring: the campus and building views take one `nav` prop (`{ bridge, capture }`). Each registers a camera controller with the bridge (`frame(id)` returns the camera that frames a building or a room), and their own label clicks fly through it. Quick moves call `hqCommands.navigateTo`, so they behave exactly like the assistant's move; suggested phrases name only buildings the person may open and rooms of the open building. Layout widths: 1440 and up rail 200 px and side 320 px; 1280 to 1439 slim rail (64 px); 900 to 1279 slim rail and a side drawer; under 900 both are drawers (bottom sheets under 760). The person's collapse choices are kept in `localStorage` (`anum.hq.nav.rail|side|strip|focus`). From 900 px a toolbar button ("Hide side panels" / "Show side panels", `aria-expanded`) folds the floor rail and the side column away so the scene has the whole width; the button stays in the toolbar to bring them back, the floor menu and the quick views stay, and the assistant dock moves under the scene.

## Extension points

All are optional props of `HeadquartersLive` (and `HeadquartersEntrypoint`, which forwards them); types are in `lib/hqExtensions.ts`.

```ts
onSelectRoom(room: HqRoomRef)            // a room was selected (scene, list, tour, command)
renderRoomPanel(room, { close })         // replaces the built-in room panel (the meetings workstream)
onSelectAgent(agent: HqAgentRef)         // an agent card or desk was opened
renderWorkstationPanel(agent, { close }) // replaces the built-in workstation panel
renderPaDock({ close })                  // a dock beside the scene (up to 380 px); the executive PA panel goes here
liveMeetingRoomIds: ReadonlySet<string>  // rooms to light up as "In a meeting"
onSwitchWorkspace / onSwitchOrganisation // supplied by the shell; used to open another building
```

### Navigation commands (executive assistant)

```ts
import { hqCommands } from './lib/hqExtensions.ts';
await hqCommands.navigateTo({ building: 'Tyre Pulse', floor: 1, room: 'Meeting Room', agent: 'Fleet Planner', view: '3d' });
```

- Every field is optional and may be an id or a name (case and accent insensitive, partial names allowed when they match one item). `floor` also accepts a level number.
- Result: `{ ok: true, applied }` or `{ ok: false, code: 'not-found' | 'not-accessible' | 'ambiguous' | 'not-mounted', message }`.
- A building in another workspace resolves to a workspace switch. The target is kept in `sessionStorage` for 60 seconds and applied once the new workspace has loaded.
- The same call is available as a DOM event for code outside the bundle: `window.dispatchEvent(new CustomEvent('anum:hq-navigate', { detail: target }))`.
- If the headquarters is not open, the call stores the target, opens `#office-preview` and returns `not-mounted`; the move applies on mount.
- `resolveNavTarget(target, campus, scene)` is pure and unit tested.

### Wiring the executive PA dock (after both branches merge)

`HeadquartersLive` already renders `renderPaDock` in a right-hand column (`.hqPaDock`, at most 380 px, under the scene below 1100 px) and shows nothing when the prop is absent. The executive workstream exports `ExecutivePaDock` from `apps/web/src/ExecutivePanel.tsx`. This branch does not import it, so the two branches never depend on each other. After both are merged, pass it where the headquarters is mounted (`HeadquartersEntrypoint` forwards the slots). `ExecutivePaDock` takes the same props as `ExecutivePanel` (`onOpenView(view)` and an optional `onLocation`); `openView` is the shell's own view switch:

```tsx
import { ExecutivePaDock } from './ExecutivePanel';
<HeadquartersEntrypoint renderPaDock={() => <ExecutivePaDock onOpenView={openView} />} />
```

The PA moves the view only through `hqCommands.navigateTo`, so the dock needs no other access to the scene. The tour list comes from `GET /api/v1/executive-pa/tour`; the Guided visit (Office tools) reads the same endpoint and moves the view with `hqCommands.navigateTo`, so it shows only rooms the person can access. The daily briefing is `GET /api/v1/executive-pa/briefing` and the `daily_briefing` tool ([Executive PA](ANUM_MINE_EXECUTIVE_PA.md#daily-briefing)); a new briefing source is one more loader in `anum_api/pa_briefing.py` (`_LOADERS`, `SECTION_KEYS`) plus its `TEXT` templates in both languages.

## Layout history and templates

A new layout action, a new room or desk column, and whole-office template export are described under "Extension points" in [ANUM_MINE_LAYOUT_HISTORY.md](ANUM_MINE_LAYOUT_HISTORY.md). The History and Templates tabs of the builder are lazy chunks (`HqBuilder.tsx`); keep any new builder tab lazy as well.

## History panel

`HqHistoryPanel` (docs: [ANUM_MINE_HQ_HISTORY_PANEL.md](ANUM_MINE_HQ_HISTORY_PANEL.md)) is mounted by `HeadquartersLive` and opened from three places: Office tools, History; a "Room history" button under the room panel and an "Agent history" button under the workstation panel (both also when `renderRoomPanel` or `renderWorkstationPanel` replaces the built-in panel); and `HqTeamPanel`'s optional `onHistory({ agentId, name })`, which adds a "History of NAME" link to each team card. Another workstream that opens the history passes a `HistoryLaunch` (`{ id, scope, tab? }`, scope `office`, `room`, `agent` or `correlation`, see `lib/hqHistory.ts`); the panel needs the room list (`id`, `label`, `deskIds`), the agent list and an id to name map from the caller. It is read only and uses the same permission as `/api/v1/office-history`.

## Room modules

The module a room opens follows its stored department (`team_department`, set by the builder and the starter campus), never the words in its name. Pure resolver: `apps/web/src/lib/hqRoomModule.ts`; the link is `HqRoomModule.tsx`, shown in the room inspector and in the room list (a real link or button, keyboard reachable, English and Arabic).

| Stored department | Opens |
|---|---|
| `finance` | Finance (`#finance`) |
| `sales` | Sales and CRM (`#whatsapp`) |
| `support` | Inbox and support (`#inbox`) |
| `operations` | The live flow dock inside the office |
| `tyre_engineering` | Tyre Planner (`#tyre`) |
| `marketing` | Marketing (`#marketing`) |
| `technology` | Developer mode (`#developer`, owners; others see who can use it) |
| `research` | Knowledge Base collections (`#knowledge`) |
| `legal` | Documents (`#files`) |
| `compliance` | Governance (`#governance`) |
| `security` | Audit log (`#audit`) |
| `hr` | Members and roles (`#members`) |
| `procurement`, `custom` or any unknown value | No module: "No module connected for this department" and the Organisation gaps button |
| `executive`, `commerce`, `meeting` | Accepted if a later contract stores them: `#executive`, `#commerce`, `#meetings` (not storable today) |

No department stored: the room kind decides (meeting to `#meetings`, workshop to `#tyre`, warehouse to `#commerce`, reception to no module, with "no visitor register"); then a room in an executive building opens `#executive`; then a word in the room name; last, office to Tasks and lounge to Calendar. In every one of these cases the owner (`workspace:manage`) sees "Set a department in the builder". No route is invented: every target is a view that exists in the shell.

A room with a live meeting (the stored `room_id` of an active meeting) adds the meeting to the module link: a meeting room's link opens `#office/meeting/<id>`, and a room of another department also shows "Open the live meeting". See [departments, modules and meeting rooms](ANUM_MINE_DEPARTMENTS_AND_MEETING_ROOMS.md).

Reception: the live office has no visitor workflow and shows no invented host or room names (`ReceptionOperations` is a local simulation and is mounted only by the labelled Concept preview). A visitor registry is not built: owner decision O4 is open.

## Asset modules

Three standalone module sets under `apps/web/src/hqAssets/` build the scene's pieces. They are pure (no DOM, no three.js), take a `MeshBuilder` or return a mesh, are seeded, and never encode a name, count or status. `lib/campusScene.ts` and `lib/buildingScene.ts` call them.

| Folder | What it builds | Used by |
|---|---|---|
| `exterior/` | `warehouse`, `tyreWorkshop`, `officeTower`, `adminBlock`, `retailBlock`, `executiveHq` buildings with facades, roofs, entrances, lit windows and a blank sign board | `campusExteriors.ts` picks one per campus building by type (the villa for the personal office stays in `campusBuildings.ts`) |
| `interior/` | `buildInteriorRoom` for reception, meeting, office, lounge, customer care, operations, warehouse, workshop and executive rooms; workstations and seated figures | `buildingScene.ts`, one room per registry room (`interiorKindFor` maps kind and name) |
| `environment/` | `campusEnvironment` (ring road, pavements, lamps, tree lines, hedges, canal with bridges, gate house, fence, skyline), plus `carPark`, `car`, `truck`, `van`, `pond`, `tennisCourt`, `tree` and more | `campusScene.ts`, `buildingScene.ts` (cars) |

How they are wired:

- **Truth rules stay in the integrator.** Desks are the registry's (one station each, state from presence). `occupantSeed` is passed only for a desk with an assignment, so a figure appears only where a record says someone sits. Rooms the interior set does not know fall back to the older furnishing in `buildingScene.ts`.
- **Lettering.** The exterior assets draw a blank sign board and report where it is. `campusExteriors.ts` lays the lettered texture quad over it; the text (`ANUM MINE` and the real building name) is drawn at render time.
- **Surface classes.** The asset builders paint plain colours. `hqSurfaces.ts` (`classifySurfaces`) gives their flat ground surfaces a class (grass, paving, asphalt, water) and large walls and roofs the concrete texture, from colour and orientation. Windows, signs and anything an asset classified itself are left alone.
- **No baked shadows.** `castShadow`, `blobShadow` and the environment's `castShadowPoly` draw nothing now, because the renderer draws a real shadow map.
- **Budget.** Density of the environment drops with campus size (`density` 0.8, 0.6 above ten buildings), and interiors switch to `detail: 'lite'` for tall or large buildings. Measured scene triangles: about 34 000 to 60 000 for 4 to 14 buildings; a nine room floor about 20 000.
- **Sizes.** The exterior assets have size ranges of their own (the executive building is at least 30 m wide, a tower at least 8 storeys), so they can be larger than the campus footprint; the ring road and canal are sized from the campus extent.

## How the scene is drawn

The scenes are procedural meshes (`campusScene.ts`, `buildingScene.ts`, building types in `campusBuildings.ts`, shared props in `hqProps.ts`). They hold decoration only: nothing in the geometry encodes a name, count or status. Status shows through desk monitors, room tiles and the HTML cards. Each vertex carries a surface class (`MAT` in `hqGeometry.ts`) and texture coordinates; the renderer turns the classes into materials.

- **Building types** (drawn by the exterior assets): the owner's icon decides where there is one (truck or factory: a warehouse shed with roller doors, a loading apron and a truck; chart or briefcase: a glass office tower on a lobby podium; store: a retail block; building, shield or landmark: a low admin block with ribbon windows and sunshades; home or user: a villa). Without an icon the kind and a stable seed pick: business is a tower or an admin block, department a workshop (saw-tooth roof, big door, tyre stacks), a warehouse or an admin block. The executive building is always the tower with a crown, helipad and a lit ANUM mark; the personal office is always the villa. The finish (white, sandstone, slate) varies by workspace id. `archetypeOf` is pure and unit tested.
- **Campus**: a ring road with kerbs and a dashed line, a gate, parked and passing cars, car parks, a truck yard, a canal ring between the buildings and the road (drawn only where it clears every building and the plaza, with a footbridge where a walkway crosses it), ponds with jets, a tennis court at the first free spot, palms, round trees, cypresses, hedges, flower beds, benches, lamps and a far skyline.
- **Building interior**: tiled corridors and room floors (stone, wood or concrete by room kind), solid wall bases with glass and mullions above, an outer glass skin on the two back sides, and furniture per room kind: reception (walnut brand wall with the ANUM mark, marble desk, waiting area), meeting (table, chairs, wall screen), warehouse (racking with crates, a forklift, floor markings), workshop (bench, tool board, tyre stacks, wall display), lounge (sofas, shelf, lamp) and office (storage wall, whiteboard, printer, cooler). Desks come only from the registry; a generic seated figure appears only at a desk that has an assignment. Outside, the plinth stands in a lawn with a car park (cars at 62 percent size), trees and a sign wall.
- **Vehicles**: cars have a profiled body (hood, windscreen, roof, rear window, boot) in glossy paint, glass, lamps, mirrors and wheels with rims; trucks have a cab and a container.
- **Sign lettering**: each entrance sign (and the gate) is a board whose face is a canvas texture. The text is drawn at render time from the real names: "ANUM MINE" over the building name from the campus data, or the open workspace's name in a building. `signAtlasLayout` (pure) says where each cell is; `drawSignAtlas` (browser) draws the cells. No other text is in the mesh.
- **Textures**: procedural canvas textures (`hqTextures.ts`, no image files) for concrete, paving tile, asphalt, grass, wood, corrugated metal, roller doors and court surface, sampled in world space (triplanar, so boxes need no coordinates) and faded with distance so they do not shimmer.
- **Windows**: an emissive weight of 2 or more marks a window. By day it is reflective glass (it reflects a procedural sky baked into a reflection map); at night it is a warm lit pane (the part above 2 is the glow).
- **Shadows and light**: one directional light (sun by day, moon at night) with a real shadow map covering the buildings (`shadow` region of the scene), a hemisphere light and the sky reflection map. The map is drawn again only when the scene or the light changes. Walls also darken towards the ground.
- **Night glow**: a bloom pass (three.js `UnrealBloomPass` on an `EffectComposer` with MSAA) lifts lit windows, signs, lamps and fountain jets. It only runs at night, and never under reduced motion.
- **Quality**: quality 3 is a 2048 px shadow map plus night glow; 2 a 1024 px map plus glow; 1 a 1024 px map; 0 no shadows and no glow. The renderer starts at 3 (2 on very large canvases, 1 on a software rasteriser such as SwiftShader or llvmpipe) and steps down after three seconds of slow frames, then lowers the pixel ratio. A person (or a test) can pin it with `localStorage["anum.hq.quality"]` set to 0 to 3. The canvas dataset shows `data-shadow` (map size, 0 = off) and `data-bloom`.
- **Budget**: about 45 000 to 75 000 scene triangles for a campus of 4 to 14 buildings (the unit test allows 250 000) and 15 000 to 25 000 for a building cutaway; the frame also draws the shadow pass.

### Dependency

three.js 0.184.0 (MIT, no runtime dependencies, pinned exactly) and `@types/three` 0.184.0 (dev only; MIT, its own type dependencies are dev only). three.js lives in its own chunk (`hqThreeRenderer`, about 546 kB, 136 kB gzip) that `HqStage.tsx` loads with a dynamic import when the 3D stage mounts, so the list view, the no-WebGL fallback and every other page never download it. A browser without WebGL 2 gets the list and the 2D floor plan.

### Loadable models (glTF), halos and facade relief

The scene can place glTF models (sofas and armchairs, and a seated person only at a desk with a registry assignment) through the
same three.js renderer: `lib/hqModels.ts` (catalogue and truth rules), `lib/hqModelCache.ts` (cache and fallback), `lib/hqModelLayer.ts`
(loader, instancing, contact shadows, night halos). The procedural pieces they replace stay in the scene as tagged fallbacks and show
whenever a file cannot be loaded. Licences, sizes, measurements, the gap analysis and the owner decisions are in
[ANUM_MINE_HQ_REALISM_ASSETS.md](ANUM_MINE_HQ_REALISM_ASSETS.md). To add a model: put the file in `apps/web/public/hq/models/`, add it to
`MODEL_SPECS` (size, licence, author, source) and `NOTICE.txt`, and give the builder that should use it a slot (`MeshBuilder.slots`).
The canvas dataset adds `data-models-placed`, `data-model-figures`, `data-models-loaded`, `data-model-bytes`, `data-model-load-ms`
and `data-model-fallbacks`.

### Avatar styles (people figures)

Each seated figure is drawn from a style: the avatar the owner chose on the agent profile, else a default drawn from the agent id
alone (`lib/hqAvatar.ts`, never a name). The scene gets it through `BuildingSceneOptions.looks` (desk id to the agent's avatar and
department); `figureLook()` resolves it, `StationSpec.look` and `FigureOptions.look` carry it to `seatedFigure()`, the model slot
keeps it, and `placementsFromSlots()` picks the character model of the hair shape. The renderer tints the model per instance
(`hqModelLayer.ts`, `hqFigureRoles.ts`) and adds a headset or lanyard badge as one instanced mesh each. Details, palette, defaults,
sizes and limits: [ANUM_MINE_HQ_REALISM_ASSETS.md](ANUM_MINE_HQ_REALISM_ASSETS.md#avatar-styles). The canvas dataset adds
`data-figure-looks` (build/hair/outfit/skin codes) and `data-figure-styles` (figures per build).

## Camera, keyboard and performance

Orbit by dragging, pan with Shift or the right button, zoom with the wheel or pinch. With the scene focused: arrows pan, Q and E rotate, plus and minus zoom, 0 resets. Reduced motion jumps the camera instead of tweening. The renderer is three.js (see "How the scene is drawn"): per-mesh frustum culling, render on demand, the shadow map redrawn only when needed, a pixel ratio capped at 2 and lowered after sustained slow frames. A lost context shows "Restoring the 3D view" and three.js rebuilds its buffers; if it does not come back in 5 seconds the page switches to the List view. The canvas dataset (`data-engine`, `data-draw-ms`, `data-triangles`, `data-drawn`, `data-culled`, `data-dpr`, `data-shadow`, `data-bloom`) exposes the renderer and the measured draw time for tests.

## Live scene

Docs: [ANUM_MINE_HQ_LIVE_SCENE.md](ANUM_MINE_HQ_LIVE_SCENE.md). Extension points:

- **Presence source.** `usePresenceLive()` is the only place the page reads presence. Another consumer calls it too and shares the one stream (`acquirePresenceHub` keys the hub by tenant and workspace). Never open a second stream.
- **A new desk state or marker.** Add the state to `STATE_LOOK` and `visualOfStatus` in `hqAssets/interior/stateVisual.ts`, a marker shape to `markerParts`, and the label and help text to `hq.*` and `hq.stateHelp.*`. `test/hqStateVisual.test.ts` fails if two states look alike.
- **A wall tile.** Add the id to `WallTileId` and a `tile(...)` line in `lib/hqCommandWall.ts` with its source, then the `hq.wall.<id>` label. A tile must say "Not connected" when its source is down.
- **A camera preset.** `lib/hqCamera.ts` holds pure presets (`insideCamera`, `clampInside`); `BuildingView` decides when to use them.
- **Opening the flow dock for a room.** `RoomModuleAction` passes its `roomId` to `onOpenFlow(roomId)`; the host sets the dock's room filter.

## Not finished here

- Thumbnails exist only for places seen in this tab (a second renderer for a background survey was judged too heavy for the performance budget); until then the strip shows an icon and the rail shows the plan.

- Counts for workspaces other than the open one need the campus endpoint with verified presence (data workstream).
- Meeting rooms need a room binding on live meetings (meetings workstream) to show a real state.
- Visual details that the concept shows but that have no data (weather, visitor check-in flow (owner decision O4 open), marketplace logos) are intentionally absent.
