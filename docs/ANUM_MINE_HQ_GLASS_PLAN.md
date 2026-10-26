# ANUM MINE headquarters: the glass shows the real floors

Status: built on `claude/hq-glass-plan` (26 October 2026). It replaces the repeated fake room behind every glass pane of the campus with the
real layout of the building: the rooms, desks and walls of each floor, lit only where presence says somebody is at work.

## What the owner asked for

Through the glass at campus distance you see that building's own floors and rooms, in the place they really are, and the building shell is
generated from its floor plans instead of a generic box. Nothing may be invented: no person, no lit desk without a working or meeting agent.

## How it works

1. **Floor plans to a frame** (`lib/hqPlanFrame.ts`, main chunk, small). For a registry building, `planFrame(floors)` takes every floor with level 0
   or more (a basement is below ground) and gives each one its own footprint: its rooms, a 1.2 m margin and the shared service core, exactly the
   slab the interior pipeline draws (`buildingLayout`). The union of the storeys is the footprint of the building, and its middle is where the
   building stands on the campus. The storey pitch is `FLOOR_PITCH` (4.2 m, the same number as the interior stack); floor 0 stands on a 0.9 m plinth.
   A plan smaller than 12 by 9 m is widened evenly so a one-room building is still a building.
2. **Shell from the plans** (`hqAssets/exterior/planShell.ts`). One curtain wall storey per floor on that floor's footprint: an upper floor that is
   smaller sets back (a roof deck is added on the biggest free strip), a larger one overhangs, the service core rises above the roof as a lift
   tower. The silhouette of the building kind is decoration on top of that footprint: the executive crown and portico, the tower crown with plant room and beacon, the
   admin block's louvres and terrace, the shop canopy, workshop and warehouse roller bays, the villa roof terrace with a pool. The campus layout uses the
   real footprints (`layoutCampus(buildings, sizes)`): rings widen so planned buildings never overlap; without plans the layout is exactly what it was.
3. **Plan impostor atlas** (`lib/hqPlanAtlas.ts`). Each storey is drawn top down into a 128 px tile (a row per building, a column per storey, nearest filtering,
   at most 16 buildings): room floors by kind, the corridor, the lobby and lift shaft of the core, one pixel partition walls with a gap at the door side,
   desks with a screen, a seated figure, the meeting table and the screen wall of a command centre. The alpha byte is `0` outside the plan, bit 7 for a wall and
   the rest the light level. It is deterministic: the same plans give the same bytes. A building atlas is regenerated only when its revision changes:
   the layout key (rooms, kinds, boxes, door sides, desks) moves when `office.layout_changed` led to a different plan, and the occupancy key moves with presence.
4. **Interior mapping in the glass** (`lib/hqGlassShader.ts`, a lazy chunk the renderer fetches when a scene has a plan). Every pane carries its atlas row in
   the whole part of its u coordinate (`tagPaneUv`). The fragment shader gets the storey from the pane height, follows the view ray to the floor or the ceiling
   of that storey in up to six steps (stopping at a partition wall), reads the atlas texel and shades it: that is parallax, so rooms have depth. Ceiling
   lights show only over lit rooms. Panes far from the camera (150 m and more) and the whole software path read one texel 2.8 m inside the facade instead, with no
   parallax and one texture fetch.
5. **Fallback**. A building with no registry floors, a workspace's building that is not in the open registry, and a registry building with no rooms keep the
   generic shell and the generic room behind the glass (`hq_room`). A plan that is not loaded yet shows the generic look and is swapped in when the lazy chunk arrives.

## Occupancy truth

- A screen is lit only for a desk whose agent has a presence record that is fresh and says `working` or `meeting`. Queued, waiting, blocked, idle, paused,
  unknown, stale and no presence at all stay dim (`LIT_STATUSES`).
- A room is lit (0.8) only while one of its desks is lit; otherwise it is dim (0.26); the corridor is 0.32. Light falls off steeply in the shader, so vacant rooms
  and corridors are dark glass, an occupied room glows, a lit screen is bright blue.
- A seated figure is a coloured dot only at a desk that has an assigned agent (a registry fact), the same rule as the interior pipeline. No person is drawn anywhere else.
- Nothing in the atlas encodes a name or a count.

## Data flow

`HeadquartersLive` (`registryPlans`) gives `CampusView` a map of plans by campus building id, built from the open workspace's projection and presence.
`CampusView` sizes the placements from the plans, draws the planned shells (the kit is `import('./lib/hqPlanKit.ts')`, so the entry chunk never carries
it) and passes the atlas to `HqStage`, which hands it to the renderer through the plan shader chunk. A presence change moves only the atlas bytes (no scene rebuild); a layout
change rebuilds the shells and the atlas. The canvas shows `data-glass-plan` (the revision on the GPU) and `data-glass-plan-wanted` (the revision the scene asks for).
The stage fetches the plan shader chunk the first time a scene has a plan; that chunk adds its uniforms, rewrites the glass program (`patchGlass`, with its own program cache key) and uploads the atlas, so the renderer chunk carries only a small hook (`glassHooks`). A glass program that no longer contains the expected lines is left unchanged (the generic rooms stay).

## Tests

- `test/hqGlassPlan.test.ts`: frame from floor plans (basement skipped, setbacks, storey pitch), no plan without rooms, layout unchanged without plans, rings widen for real
  footprints, atlas determinism and revisions (layout and presence move them separately), alpha codes, room and corridor colours, occupancy truth (working, meeting,
  idle, stale, unknown, seated figure, room light), a facade pane maps to the room behind it (for every sampled pane of the 5 floor headquarters of the starter campus the atlas
  texel is the floor colour of the real room at that position, or a desk, or the table), pane tagging, all seven kinds drawn on the real footprint, the scene using the plan shell only for
  planned buildings, shader source.
- `e2e/hq-glass-plan.spec.ts`: the atlas reaches the GPU and the frame is not blank; day, dusk and night; a close view of the headquarters (parallax path switched on) at dusk and night and one on the software path;
  changing a room in the registry moves the atlas revision without a reload; the campus at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844 has no sideways scroll.

## Budgets

Measured with the performance spec's own gzip, after merging main (fae3323): lazy HQ entry chunk 127,926 bytes gzip (limit 140,000; main is 134,518), renderer chunk 159,995 (limit
160,000; main is about 159,985, so the plan hooks cost about 10 bytes), plan kit 3,900 and plan shader 2,141, both fetched only when a campus has a registry building with rooms. No limit was raised. To fit,
the plan code that would have gone into the renderer lives in the shader chunk (uniforms, program rewrite, texture upload) and the dynamic import sits in the stage. Read this honestly: with a
third importer the bundler now hoists the geometry and ambient code into a shared chunk (about 13,300 bytes gzip) that loads together with the entry, so the file the performance spec measures shrank
while the headquarters payload (entry plus shared chunk, about 140,500) grew by about 6 kB gzip over main. That is an artefact of chunking, not a limit met by moving code.

Software GL cost (SwiftShader, 1440x900, dusk, orbiting the campus, plan impostor on against the plan switched off in a scratch build, three runs each): the average draw time is dominated by noise
(last samples 16.7, 8.3 and 21.4 ms with the plan against 36.5, 6.7 and 18.8 ms without), so the one texel lookup of the lean path costs nothing measurable and stays on. The first frames after the
plan chunk arrives include one recompile of the glass program (a few hundred milliseconds on SwiftShader), the same size of hitch as any first compile there.

## Honest gaps

- The parallax path was verified on a software rasteriser with the rich glass switched on by hand (`anum.hq.ambient`), not on a real GPU.
- Rotation of a room is not in the scene projection (`layoutRoomsOnFloor` ignores the stored `rotation`), so neither the interior pipeline nor this atlas rotates rooms; both stay in step.
- The campus never draws interior meshes, so there is nothing to cross-dissolve to: opening a building swaps the campus scene for the building scene (the existing flight). The impostor reduces its own detail with distance instead.
- Nearest filtering of the atlas can shimmer on a pane smaller than a texel at the far edge of the campus; panes beyond 150 m use the single lookup to keep it calm.
- The facade of a planned building is simpler than the hand drawn executive HQ (no atrium vault, turnstiles or balcony runs): the plan footprint, setbacks and glass come first.
- The buildings of other workspaces are never expanded, so they keep the generic look by design.
