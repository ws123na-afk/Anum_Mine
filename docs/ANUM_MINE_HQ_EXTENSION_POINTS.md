# ANUM MINE headquarters: campus, building cutaway and extension points

Web only. Code: `apps/web/src/HeadquartersLive.tsx` (container), `CampusView.tsx`, `BuildingView.tsx`, `CampusListView.tsx`, `HqStage.tsx` (WebGL stage and label cards), `HqChrome.tsx` (scene controls), `HqPanels.tsx`, and the pure libraries `lib/campusModel.ts`, `campusScene.ts`, `buildingScene.ts`, `hqGeometry.ts`, `hqMath.ts`, `hqCamera.ts`, `hqRenderer.ts`, `hqPresence.ts`, `hqModules.ts`, `hqExtensions.ts`. Visual reference: the owner-approved concept renders (screens 1 to 4); they are concept art, not data.

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
| Agent counts, statuses | `GET /api/v1/office/presence` (states with provenance and `stale`; a private agent is `desk:<id>`); without it the browser reads workstation evidence (agents plus open tasks) and participants of `active` agent meetings | "Presence unavailable"; a status those sources cannot tell (meetings when the list failed) shows "-" |
| Desk work panel | `GET /api/v1/office/workstations/{desk}/work` (tasks with run phase, checkpoint and steps, approvals, errors, documents) | Task titles from presence, with a note |
| Meeting room state | `liveMeetingRoomIds` prop supplied by the meetings workstream; sessions are not bound to rooms in the current API | The room shows no meeting state |
| Live operations, current flow | Commerce setup and dashboard of the open workspace | "Not connected" with the setup link, "No access", or the error |
| Tyre panel | Tyre setup and dashboard of the open workspace | Same |

A queued task reads as Queued, never as working. Workspaces other than the open one show no counts until the campus endpoint supplies verified presence.

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

The PA moves the view only through `hqCommands.navigateTo`, so the dock needs no other access to the scene. The tour list comes from `GET /api/v1/executive-pa/tour`; until that endpoint is available the built-in Guided visit (Office tools) builds its stops from the rooms of the open floor.

## How the scene is drawn

The scenes are procedural low-poly meshes (`campusScene.ts`, `buildingScene.ts`) with shared props in `hqProps.ts`. They hold decoration only: nothing in the geometry encodes a name, count or status. Status shows through desk monitors, room tiles and the HTML cards.

- **Buildings**: glass cores with floor bands, fins between window bays, a lobby storey, roof plant (air units, water tank, aerial, solar rows or a roof garden, skylight), a lit entrance canopy with steps, planters and a sign board carrying the ANUM mark. The finish (white, sandstone, slate) is picked from the workspace id, so a campus does not read as clones. The Executive HQ has the tower, crown, helipad and a large lit ANUM mark.
- **Campus**: a ring road with kerbs and a dashed line, a gate with the ANUM mark, parked and passing cars, car parks, a truck yard, ponds with jets, palms, round trees, cypresses, bushes, hedges, flower beds, benches, lamps and a far skyline.
- **Building interior**: tiled corridors and room floors, solid wall bases with glass and mullions above, an outer glass skin on the two back sides, and furniture per room kind: reception (walnut brand wall with the ANUM mark, marble desk, waiting area), meeting (table, chairs on both sides, wall screen), warehouse (racking with crates, a forklift, floor markings), workshop (bench, tool board, tyre stacks, wall display), lounge (sofas, shelf, lamp) and office (storage wall, whiteboard, printer, cooler). Desks come only from the registry; a generic seated figure appears only at a desk that has an assignment.
- **Entrance signs**: an HTML sign (ANUM MINE over the real building name) is pinned to each campus building and to the open building's front lawn. It shows only when the camera is close, and it is hidden from assistive technology because the building and room cards already carry the same names.
- **Windows**: an emissive weight of 2 or more marks a window. The shader shows reflective glass by day and a warm lit pane at night; the part above 2 is the glow.
- **Shadows**: baked. Buildings, trees and cars cast a ground shadow away from the sun direction; furniture gets a darker contact patch on the floor. Walls darken towards the ground in the shader.
- **Budget**: about 45 000 triangles for a ten building campus (the unit test allows 250 000) and a few thousand per floor. Culling, render on demand and the adaptive pixel ratio are unchanged.

## Camera, keyboard and performance

Orbit by dragging, pan with Shift or the right button, zoom with the wheel or pinch. With the scene focused: arrows pan, Q and E rotate, plus and minus zoom, 0 resets. Reduced motion jumps the camera instead of tweening. The renderer is a small WebGL 1 engine (`hqRenderer.ts`): depth buffer, hemisphere and sun light, night emissive windows, fog, a glass pass, per-building frustum culling, render on demand, device pixel ratio capped at 2 and lowered after sustained slow frames. A lost context shows "Restoring the 3D view" and rebuilds the buffers; if it does not come back in 5 seconds the page switches to the List view. The canvas dataset (`data-draw-ms`, `data-triangles`, `data-drawn`, `data-culled`, `data-dpr`) exposes the measured draw time for tests.

## Not finished here

- Counts for workspaces other than the open one need the campus endpoint with verified presence (data workstream).
- Meeting rooms need a room binding on live meetings (meetings workstream) to show a real state.
- Visual details that the concept shows but that have no data (weather, visitor check-in flow, marketplace logos) are intentionally absent.
