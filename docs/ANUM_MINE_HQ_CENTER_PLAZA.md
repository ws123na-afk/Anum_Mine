# ANUM MINE headquarters: the centre plaza

Date: 26 October 2026. Web only (`apps/web`). Owner request: "In the center we have a big fountain and road and on side there are buildings around it."
The per building billboards are a separate change; this one is the centre of the campus and where the buildings stand round it.

## What the campus centre is now

From the middle outwards (metres, `PLAZA` in `lib/campusModel.ts`; the scene and the layout read the same numbers):

| Radius | What |
|---|---|
| 0 to 12.5 | Big three tier fountain: outer basin, middle basin and upper basin of lit water, a column and the lit globe on top. 46 jets (20 arch from the middle basin to the outer one, 12 from the outer rim inwards, 8 from the upper basin, 10 spouts, 6 round the column), 50 lit studs on the copings |
| 12.5 to 18.8 | Paved plaza (concentric paving bands and radial joints), 12 benches facing the water, 6 garden lamps, 8 topiary, clipped hedge arcs |
| 21.8 to 29.8 | Roundabout road (centre line 25.8, 8 m wide) with kerbs, pavements, a dashed centre line and zebra crossings where a walkway crosses it |
| 32.6 | Outer edge of the roundabout pavement; walkways start here |
| 35 | Nothing but walkways and the two avenues inside this radius: no building lot may touch it |

Two avenues join the roundabout to the existing ring road at its two junctions (south and east), with street lamps. The ring road, the canal, the
bridges, the gate house and the fence are unchanged and still surround everything.

## Where the buildings stand

`layoutCampus` (`lib/campusModel.ts`) puts the executive building due north of the plaza (its door looks down on the fountain) and every other
building on rings round the plaza. It replaces the old layout that put the executive tower on the centre.

- A building's **lot** is its footprint plus a margin at the sides and back (6 m) and its front garden on the +z side where the door is (16 m).
  The executive building drawn from its own asset has a longer forecourt (31 m) and a wider podium. A lot never touches the plaza, either avenue or
  another lot (1 m apart at least). Footprints keep their real size: a building drawn from floor plans keeps the footprint of its plans.
- **Any number of buildings**: 0 to 20 and more. Rings widen with the largest real footprint; a building that finds no free slot on a ring goes to the next one out.
- **Deterministic and stable**: buildings are placed in the order of a hash of their workspace id, never of the list order. Each building has its own fixed
  shuffle of the slots of a ring (a hash of its id, the ring and the slot), tries four slots on the first ring and all on the outer ones, and its radius
  depends on the slot and its own size only, never on its neighbours. Adding or removing one building moves the few buildings that wanted the same
  slot (measured over 3 to 20 buildings: 21 percent of the buildings move on average and never more than 9 at once; the layout this replaced moved 54 percent on average and up to 13). It is still open addressing on a busy ring, so a single addition can move a short chain.
- The executive building is 30 by 26 m (the footprint its asset really has; it was laid out as 15 by 15). The order of the placements (executive first, then by name) is unchanged, so the sign order is too.

Doors still face +z (south): the exterior kit draws every front on the +z side and the floor plan atlas is mapped in world axes, so buildings are not
rotated. A building south of the plaza has its door on the side away from it; its walkway reaches the door round the side.

## Walkways to every door

`lib/campusSpokes.ts` routes one walkway per building from the roundabout (radius 32.8) to the end of the door's front garden. The route is found on a
2.5 m ground grid round every footprint (A*, keeping 3.6 m from a wall and away from walls by a cost), then straightened where the line of sight is
clear. It always ends exactly on the door and never crosses a footprint (`test/hqCenterPlaza.test.ts`). Bends are rounded, lamps stand along it,
ponds, trees and car parks keep out of it, and a zebra crossing is painted where it meets the roundabout.

## Ambient motion

Scenery only, as before (docs/ANUM_MINE_HQ_SCENERY_MOTION.md): a third lane runs round the roundabout (counter clockwise, one speed, at most three
vehicles) beside the two ring road lanes, and the fountain is the first ripple source of the water shader (radius of the outer basin). Nothing implies a
person, a task or a count; software GL and reduced motion keep it still.

## Budgets

- Triangles (`hqAssets/environment/budgets.ts`, enforced by `test/hqEnvironmentAssets.test.ts`): centre plaza and fountain 5,804 (cap 6,400), roundabout with two mouths and seven crossings 1,550 (cap 1,900), avenue of 90 m 486 (cap 700). The old globe monument and its 8 benches are no longer drawn in the campus (about 2,000 triangles less); the whole scene grows because the campus is larger (ring road, canal and planting are longer): 58,067 to 75,279 triangles for four buildings, 79,042 to 94,595 for eight and 107,709 to 131,309 for thirteen. The environment cap for the kit is unchanged.
- Bytes: all geometry is in the lazy headquarters chunk, nothing in the renderer. See [the performance budget](ANUM_MINE_HQ_PERFORMANCE_BUDGET.md) for the measured numbers.

## Checks

- `test/hqCenterPlaza.test.ts`: 0 to 20 buildings with and without the executive building, with real plan footprints, no overlap with the plaza, avenues or each other, determinism and independence of list order, stability when one building comes or goes, a walkway ending on every door and crossing no building, buildings inside the ring road, triangle caps.
- `e2e/hq-center-plaza.spec.ts`: day, blue hour and night, five viewports (1440x900, 1366x768, 1024x768, 768x1024, 390x844), English and Arabic, not blank, no sideways scroll. Set `PLAZA_DIR` to keep the screenshots.

## Honest gaps

- Buildings are not rotated: only the buildings north of the plaza look straight at it.
- The campus is larger than before (the executive forecourt alone is 31 m): the extent radius is 92 m for four or eight buildings (it was 45) and 128 m for thirteen (it was 74), so buildings are smaller on screen at the home camera.
- The fountain, plaza furniture and roundabout are scenery with no name or number; the water is not simulated, only the existing ripple shader.
