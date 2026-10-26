# ANUM MINE headquarters: interior assets, floors and scenery

Status: built in this change (web only). The owner's concept renders of the commerce floor, the Tyre Pulse floor and the three floor headquarters
(see the [visual reference](ANUM_MINE_VISUAL_REFERENCE.md)) are style references. Their people, names and numbers are never reproduced: desks and
people come only from registry records, and a vacant desk stays vacant.

## What a floor draws

A cut-away floor is built by `lib/buildingScene.ts` from the persisted room geometry, never from a template.

| Part | Where | What |
|---|---|---|
| Every room of the floor | `buildBuildingScene` | All rooms of the active floor at their stored position and size. `test/hqFloorCompleteness.test.ts` builds all 18 floors of the 8 building starter campus (62 rooms, 220 desks, the fixture is the planner's own output) and fails if a room or desk is missing, two rooms overlap, a desk lies outside its room or a room is outside the scene bounds. No real cause was found: the earlier "one room" look came from the camera and the lighting (below), not from missing rooms. |
| Floors stacked at their own height | `buildingLayout`, `FLOOR_PITCH` | Floor n has its floor surface at y = n * `FLOOR_PITCH` (4.2 m, exported by `lib/buildingScene.ts`, with a slab of `SLAB` = 0.3 m under it; the top of a floor, rail included, stays under the next slab). All floors share one origin: each floor's slab is its own plan (its rooms, a margin and the service core), so a smaller floor sits inside the plan of a larger one, the floors under it stay visible beside it, and the lift and stair core lines up from floor to floor. The earlier code used one slab for the union of all floors and aimed the camera at the middle of it. `frameBuilding` frames the active floor, or the whole stack height when stacked. |
| Stacked view | `showAll` | Every floor up to the top is dressed and drawn; floors other than the open one at the lite detail level and about 20 percent darker (`DIM`), the open floor at full detail and outlined. In the building view this is the default (up to 8 floors) and it is the **cutaway section**: the two outer walls facing the camera and the roof are removed, see [cutaway](ANUM_MINE_HQ_CUTAWAY.md). The earlier low `STACK_PITCH` camera is gone; the section camera is 32 degrees, fitted to the whole stack. Single floor view (All floors off): only the open floor is dressed and the floors above are cut away. |
| Room shell | `hqAssets/interior/shell.ts` | Floor finish by kind (warm tile for offices, planks for meeting and lounge, stone with a brass border for reception, parquet for the executive suite, concrete with safety edges for warehouse and workshop, raised floor tiles for the server area, check tiles for the pantry), skirting, a solid base with a glass partition above, the door opening in the wall the room's `door_side` names, a door frame with a brass threshold and a parked sliding door, ceiling light panels and a name plate over the door. |
| Door side | `buildInteriorRoom` (`doorSide`) | The kits are dressed with the door in the south wall and turned about the room's middle for north, east and west (stations, model slots and light halos are mapped with it). `rotation` is bookkeeping of the builder; the door side is what decides the layout. |
| Name plates | `signs` of the scene | One sign cell per room of the active floor, lettered by the renderer with the real room name (a cell after the entrance sign), plus a floor sign on the lift wall. Names come from the registry; nothing is invented. |
| Service core | `hqAssets/interior/core.ts` | A lift bank with call panels and position indicators and a stair flight with a glass balustrade, on the east side of every floor that has two or more rooms. Architecture only: no lift car, no person. Restrooms are the `restroom` kit (below), used for a registry room of that name; the core does not add rooms the registry does not have. |
| Desks | kits | A desk is a registry record. Warehouse and workshop rooms did not draw the desks they were given; they do now (`drawRoom` draws stations for kits that do not place desks themselves). |

## Room kits

`hqAssets/interior/index.ts` (`INTERIOR_KINDS`, `interiorKindFor`). The kind comes from the registry kind plus words in the room name; the name never produces data.

| Kind | Kit | Picked for |
|---|---|---|
| office | open workstations: desks (registry), monitors, task chairs, storage wall, lockers, whiteboard, display, plants | office |
| finance | open office plus a filing wall, a safe and a statement chart (abstract bars) | finance, wealth, audit, treasury, procurement |
| sales | open office plus a pipeline board of cards and a target board | sales, marketing, commerce, retail, marketplace, orders |
| customer-care | acoustic wall, quiet pod, sofa nook, headset stations | customer, care, support, tickets, messages |
| operations | video-wall frame (lit only for a live room), rack, briefing table, three monitor consoles | operations, command, control, dispatch, security |
| executive | large desk, sofa set, bookshelves, window wall | executive, board, owner, personal |
| meeting | table, chairs, wall screen (lit only for a live meeting), credenza | meeting |
| reception, lounge | marble desk and brand wall, waiting area; sofas, coffee table, shelf | reception, lounge |
| server | racks in rows with blinking status lights, cable tray, cooling unit with a spinning fan, service bench, stations | server, network, engineering, AI Labs, automation, deployments, incidents |
| workshop, warehouse | Tyre Pulse bays (two-post lift, tyre changer, balancer, tool wall, tyre racks) and racking, dock door, forklift, safety markings | workshop, warehouse |
| pantry | counter run with sink and hob, upper cabinets with a warm strip, fridge, coffee machine, table and stools | pantry, kitchen, cafe, break room |
| restroom | stalls with doors, vanity with basins and mirrors, dryer, bin | restroom, toilet, washroom |

Triangle caps per kit are measured and enforced by `test/hqInteriorAssets.test.ts` (`INTERIOR_TRIANGLE_CAP`); a floor with more than 48 desks or 24 rooms is dressed lite.

## Ambient scenery (motion)

Scenery only: it never implies a person or work. Parts: the light glints that sweep along each ceiling panel, the lift position lights that step through four stops with a pause at each, rack status lights that blink in two alternating groups, the cooling fan, and plant leaves that drift a few centimetres. Not animated, by rule: desks, screens, figures, markers and any state of a station (a vacant desk stays vacant). The tests assert the ambient geometry is identical with and without people and desk states.

How: the kits tag the triangles of a moving piece (`ambientTag`); `buildBuildingScene` splits them into `scene.ambient` parts; the stage hands them to the renderer as `ambient` items; `HqRenderer` animates each part with its own matrix about the middle of its bounds (`poseAt`, pure and unit tested). Rules:

- frozen (rest pose, no timer) under `prefers-reduced-motion`, while the tab is hidden, at quality 0, and on a software rasteriser (SwiftShader, llvmpipe) unless `localStorage["anum.hq.ambient"] = "on"` pins it. `"off"` pins it off.
- about 12 frames a second, 4 once drawing averages above 35 ms; no timer when frozen.
- the canvas carries `data-ambient="<parts>"` and `"<parts> moving"` while moving.

## Lighting

Warm floors and walls (the night sky is blue; neutral grey read as cold), diffuser panels that read as lit fixtures from the cut-away camera, at most four warm halos per room (`MAX_HALOS_PER_ROOM`), accent strip along each door wall and soft contact patches under desks. Shadows and bloom stay on the existing quality ladder (no shadows on software GL).

## Honest list of what still differs from the concept renders

- Low-poly procedural scene: no reflections, ambient occlusion or volumetric light.
- The concept shows 20 to 30 desks and a person at every desk; here a desk exists only for a registry record and a person only for an assignment.
- Ceilings are not drawn as planes (the camera looks down into the cut); light panels hang under the cut. Ceiling planes show only as the slab of the floor above in the all-floors view.
- The service core is the same on every floor; restrooms and a pantry appear only when the registry has such a room.
- Compared with the mock of the three floor headquarters (rendered at 1800x1250, night, the 3 floor Commerce building): the levels, setbacks, glass edges, slab lips and warm room light read alike; the mock has a full front facade cut so every floor's whole depth shows (ours shows the front of each floor and what the floor above does not cover), richer room dressing and people everywhere (people only from records here), and floating agent cards (the label layer, not this module).
- The room close-up ("Inside") camera and the room panels belong to the live scene, not to this module.
- Instancing was measured as unnecessary: the active floor of the starter campus has at most about 20 desks (about 10,000 triangles of stations); only the active floor is dressed, lower floors are shells.

## Tests

`test/hqFloorCompleteness.test.ts` (starter campus, all rooms and desks), `test/hqInteriorShell.test.ts` (orientation, door openings, finishes, core, ambient poses, framing, signs, stacked heights and slabs for an 8 floor tower), `test/hqInteriorAssets.test.ts` (caps, bounds, determinism, no invented people, new kits), `e2e/hq-interior.spec.ts` (five viewports, non blank, ambient parts, reduced motion, the stacked view of a 3 floor building). `e2e/hq-interior.local.spec.ts` is a screenshot runner for local review, not run in CI.
