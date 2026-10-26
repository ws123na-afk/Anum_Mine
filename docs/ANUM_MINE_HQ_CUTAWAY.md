# ANUM MINE headquarters: the cutaway section of the building view

Status: built on 26 October 2026. The owner's concept renders 22 and 24 show a building as a dollhouse section: the front facade and the roof are gone, every floor is seen in depth from a three quarter, raised angle, and the side and back walls stay as a glass and solid frame. This page says what the building view now does, how it is built, what other layers can read from it, and what still differs from the renders.

## What the person sees

- The building view opens as a **cutaway**: every floor is drawn at once (up to 8 floors; a taller tower opens on the open floor alone and the "All floors" box turns the stack on), the two outer walls that face the camera and the roof are taken away, and the other two walls stay as a glass and solid frame with a base, posts and a rail.
- The **open floor** is the one with the bright outline and full colour and full detail; the other floors are dressed at the lite detail level, about 20 percent darker, with their lit rooms, desks and room kits still drawn.
- **Cutaway / Closed** is a pair of buttons beside the floor menu, each with `aria-pressed`. Closed puts the walls and the roof back (a glass building: the rooms are still seen through the glass). A polite live region (`data-testid="hq-facade-live"`) says "Cutaway view: ..." or "Closed view: ..." when the choice changes.
- **Rotate left and right** turn the building by 90 degrees to the next diagonal view (yaw 35, 125, 215, 305) and pick again which walls are cut; the camera is fitted again for that turn. Q and E, dragging and the compass still turn freely; the cut follows the camera.
- The **floor overview** and the **Top** view look straight down at the open floor, so they leave the floors above out and keep the roof open, even in Closed. **Inside** keeps the stack and freezes the cut.
- Reduced motion: walls and the roof jump, the camera jumps, floors change at once.

## How it is built

| Part | Where |
|---|---|
| Which walls face the camera, with a dead band (9 degrees of azimuth) so a slow orbit never flickers; 90 degree snaps; the lift targets; `CutawayInfo` | `lib/hqCutaway.ts` (pure) |
| Section camera: elevation 32 degrees, fitted by the pinhole fit (`fitDistance`, `buildingFitCamera(box, aspect, pitch, yaw)`) to the whole stack, every floor's corners, at each of the four turns | `lib/hqCamera.ts`, `BuildingView.tsx` |
| The outer skin as pieces: per dressed floor and per side one mesh (`BuildingScene.walls`, `WallPiece`), plus the roof; each is its own mesh in lift group `cut:<side>` or `cut:roof`, collapsing to its floor's level in the existing 360 ms eased lift of the renderer | `lib/buildingScene.ts`, `BuildingView.tsx` (no renderer change) |
| Dimming of the floors that are not open: vertex colour and glow times 0.8 (never the glass class), halos too | `lib/buildingScene.ts` (`dimmed`) |
| Stack off for overview and Top; the facade forced open there | `BuildingView.tsx` (`stackOn`, `effectiveFacade`) |

Why a lift and not an opacity fade: the renderer already eases a group of meshes to a flat, hidden state, and adding per mesh opacity would have cost renderer bytes the budget does not have. A wall that sinks into its slab in 360 ms reads as taken away, not as popping.

Ceilings: no room has a ceiling of its own (only light panels hung under the cut), so a room is never hidden by a ceiling of its own floor. The slab of the floor above is real and does hide the back of a lower floor from a low camera (at 32 degrees the front 6 to 7 metres of each lower floor are seen, the open floor above them in full). That is the physics of the pitch of 4.2 m; the concept render shows more of each lower floor than a real section would.

## For the label layer (hook)

Labels are owned by the label module; this view does not edit it. It exposes:

- `BuildingScene.rooms`: every dressed floor's rooms, each with `floorIndex` and a world space `anchor` (above the room, at the middle of its depth), and `BuildingScene.desks` with `anchor`. In the cutaway all floors are present, not only the open one.
- `data-facade` (`cutaway` or `closed`), `data-cut-sides` (for example `south east`), `data-roof-open`, `data-stack` and `data-camera-yaw` on `.hqSceneWrap`.
- `cutawayInfo(facade, sides, floors, activeIndex)` in `lib/hqCutaway.ts`: the same facts as a value, for code that works in the same module.

A label whose room is on a floor under a cut wall can stay where it is: the wall is removed, so the anchor is in the open.

## Cost

Measured on the synthetic floors of `test/hqCutaway.test.ts` (3 rooms, 10 desks each) in Node: a 3 floor stack builds in about 0.5 s, 8 floors about 1.4 s and 53,000 and 129,000 triangles, 18 floors about 2.4 s and 282,000 triangles. A floor change rebuilds the stack (the highlight is part of the geometry), which is why a tower above 8 floors opens on the open floor alone. Gap: the dimmed floors are not cached between floor changes the way the shells of the single floor view are. Bundle: the lazy HQ chunk grew by 1.3 kB gzip (limit 140,000 unchanged); the renderer chunk did not change.

## Tests

- Unit `test/hqCutaway.test.ts`: front walls by azimuth, the dead band against flicker, the 90 degree snaps, the fit of every corner of the whole stack at six stage shapes and the four turns, the scene pieces, dimming, and the triangle budget of 8 and 18 floors.
- Browser `e2e/hq-cutaway.spec.ts`: cutaway is the default, the toggle and its announcement with the keyboard, rotation re-cuts, reduced motion, overview and Top, All floors off, five viewports, Arabic (the 3D scene is not mirrored).
- Local render check (not in CI): `e2e/hq-cutaway.local.spec.ts`, screenshots for the owner review.

## Honest list of what still differs from renders 22 and 24

See the comparison written with the pull request: the concept renders are painted images with a camera, light and people no procedural scene matches. Known differences: lower floors show only their front part; no people on paths and no photographic materials; the stage of the building view is a panel, not the full screen; room names and agent cards on floors other than the open one are the label module's work; clicking a room on another floor does not switch floors yet.
