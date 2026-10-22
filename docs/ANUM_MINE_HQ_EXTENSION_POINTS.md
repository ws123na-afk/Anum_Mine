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
| Agent counts, statuses | Workstation evidence (agents plus open tasks) and participants of `active` agent meetings | "Presence unavailable"; a status the sources cannot tell (meetings when the meeting list failed) shows "-" |
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

## Camera, keyboard and performance

Orbit by dragging, pan with Shift or the right button, zoom with the wheel or pinch. With the scene focused: arrows pan, Q and E rotate, plus and minus zoom, 0 resets. Reduced motion jumps the camera instead of tweening. The renderer is a small WebGL 1 engine (`hqRenderer.ts`): depth buffer, hemisphere and sun light, night emissive windows, fog, a glass pass, per-building frustum culling, render on demand, device pixel ratio capped at 2 and lowered after sustained slow frames. A lost context shows "Restoring the 3D view" and rebuilds the buffers; if it does not come back in 5 seconds the page switches to the List view. The canvas dataset (`data-draw-ms`, `data-triangles`, `data-drawn`, `data-culled`, `data-dpr`) exposes the measured draw time for tests.

## Not finished here

- Counts for workspaces other than the open one need the campus endpoint with verified presence (data workstream).
- Meeting rooms need a room binding on live meetings (meetings workstream) to show a real state.
- Visual details that the concept shows but that have no data (weather, visitor check-in flow, marketplace logos) are intentionally absent.
