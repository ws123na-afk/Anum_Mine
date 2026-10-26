# ANUM MINE headquarters: building and campus look pass

Status: draft, second pass after the [realism pass](ANUM_MINE_HQ_REALISM_ASSETS.md). The owner's concept renders (22 October 2026, the
five images in the [visual reference](ANUM_MINE_VISUAL_REFERENCE.md)) are style references only: premium, bright, clean, glassy,
landscaped. Their names, numbers, people and activity are never reproduced; vehicles and trees here are scenery.

## What differed, per concept render (measured on 25 October 2026, 1440x900, software GL)

| Concept render | What it shows | What our render showed before |
|---|---|---|
| Campus, 13 buildings, blue hour | Camera at about 35 degrees, buildings fill the frame; warm lit paths and plazas; a glowing globe fountain in a ring pool; dense dark green palms and trees between buildings; ponds and canals with planted banks; a dusk sky with a warm horizon and a distant skyline | A high camera (44 degrees, 170 m away) with a large empty lawn; one flat saturated green; a bright blue canal; low faceted "stone" trees; a small blob monument; black ground and no horizon at night |
| Campus, 5 buildings | Mid-rise glass buildings with terraces, forecourts with paving and lawn beds, palms along walks, parked cars | Eight storey towers stacked in grey tiers, forecourts that were bare lawn, walks 3 m wide and unlit |
| Commerce and Tyre Pulse floor cutaways | Dense desks, many people, shelving and trucks, warm pools of light, glass walls with warm interior behind | Same room kits as now but dim at night and framed far away; desk and people density is a data limit (registry records only) |
| Headquarters, three floors | Whole building cut open, floors read as a model, lit reception | Floors read as a model; slab and glass edges flat and dark at night |

## What changed

- Camera: campus distance about 25 percent closer and 4 degrees lower (40 degrees); building view 16 percent closer and 6 degrees lower.
- Sky: a sky dome that follows the camera (gradient, warm horizon band, sun or moon disc) whose horizon is the fog colour, so no page colour shows
  behind the scene. Day sun is lower and warmer (longer shadows); night is brighter and bluer so ground, paths and trees read.
- Foliage: smooth shaded, lumpy canopies (`MeshBuilder.smoothBlob`, a normal and a tone per vertex) replace the faceted stones; palms have four
  segment arched fronds; darker, richer greens. Stamped trees keep their normals (`stamp` now copies normals).
- Campus: a globe monument in a pool on a banded plaza with jets and lit rings (`environment/plaza.ts`); a garden for every building (paving
  bands, hedged lawn beds with palms, trees and topiary, flank tree rows); paths 4.6 m wide with a lit centre; more trees; a darker lawn and a
  teal canal.
- Facades: near floor-to-ceiling glass with slim mullions and a pale slab edge between storeys; the tower is six storeys, not eight.

## Honest list of what still differs

- It is a low-poly procedural scene, not a photographic render: no reflections of surroundings on glass, no ambient occlusion, no volumetric light,
  no real water motion, no people on paths or at forecourts (that would invent activity).
- Building shapes are the seven families (executive tower, glass tower, admin block, retail pavilion, tyre workshop, warehouse, villa); the concept's
  curved canopies, green walls and rooftop pools are not built.
- Interiors keep the room kits and glTF furniture. The concept's 20 to 30 desks per floor and a person at every desk are not drawn: desks and people
  come only from registry records.
- The concept's skyline and city are the existing skyline backdrop only.
- Software GL pins the light quality (no shadows, no bloom, no reflection map); the owner's GPU path is what the shadows and glow need.

## Budgets

Renderer chunk 153.7 kB gzip (limit 160 kB, no change), HQ entry chunk 131.4 kB gzip (limit 135 kB). Campus environment triangle cap raised
from 26 000 to 36 000 and the tree cap from 300 to 520 triangles because of the rounder foliage; the starter campus (8 buildings) is about 77 000
triangles in all. Light quality ladder unchanged (bloom, then shadow size, then shadows, then pixel ratio).

## Tests

`test/hqBuildingLook.test.ts` (smooth blob, stamped normals, globe and garden determinism and caps, lighting), `e2e/hq-building-look.spec.ts`
(non-blank frame, geometry counts and no sideways scroll at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844, campus by day and building at night).

## Exterior pass, 26 October 2026 (silhouettes, facades, blue hour, scenery motion)

Compared at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844 in software GL (SwiftShader) by day, at the blue hour and at night, against the
concept renders 21 to 30 of the [visual reference](ANUM_MINE_VISUAL_REFERENCE.md). Screenshots are in the pull request description. The renderer
is the same; the buildings, the light and the motion changed.

### What now matches

- **A distinct silhouette per building kind.** Executive HQ: a three storey podium with a columned portico, a glazed barrel vault atrium and a roof
  garden with a pool on the podium roof, a stepped tower with balconies, and a crown of two raked blades that meet like the ANUM mark, with a blinking
  beacon. Glass office tower: a two storey podium with a planted roof edge, a shaft that steps in on one side, a roof deck with a plunge pool, balconies
  with glass balustrades, a louvred plant enclosure with solar rows and a beacon. Admin block: louvres over the top floor, balconies on both wings, a
  roof deck with a pergola, an atrium bay with a columned portico. Retail: a wide cantilevered canopy with a lit soffit, awnings, a blank signage frame
  (two posts and a dark panel with an LED edge, no text or brand). Tyre workshop: bay doors rolled up two thirds with the roller texture, lit bays with
  a hoist frame. Warehouse: roller doors, dock bay and canopy, beacon. Personal villa (new): offset white volumes, a cantilevered upper floor with a
  balcony, a timber clad entrance, a roof terrace with a plunge pool, a garden pool with loungers, palms, and a gate wall with the sign board.
- **Facade detail.** Dark spandrels under every window row, slab edges, vertical fins, ledges, mullion gaps, balconies, louvres, a lit lobby and a
  lit canopy soffit. The building name is lettered from the real building name on the sign board (the board itself is blank in the mesh).
- **See-through glass with lit rooms** (hardware GL, or any renderer with `localStorage["anum.hq.ambient"]` pinned; a software rasteriser gets plain glass, plain water and a cloudless sky, which compile and run faster and keep CI inside its timeouts). Every exterior window pane carries local coordinates and the glass shader draws a room behind it (ceiling light,
  darker floor, desks and a few screens, warm or cool white per room), dim by day, glowing from the blue hour on, with a thin lit floor plate between
  storeys so floors read from outside. It is interior mapping in the fragment shader, not alpha blending: no extra overdraw and no sorting, so the
  software path pays only a few arithmetic operations per glass pixel. The executive lobby has turnstiles under the portico. It is a fake interior:
  the real furnished floors are drawn by the interior pipeline when a building is opened, and the exterior shell is not yet generated from their
  stacked volume (see the gaps below).
- **Reflective glass.** Glass reflects a sky gradient chosen by the way the face looks, with a sun glint and a Fresnel edge, in every light preset, so
  neighbouring faces read differently. It is computed in the shader, so it also works on the software path.
- **Blue hour.** A third light preset: deep blue zenith, warm orange horizon band, lavender haze, low warm sun, lit windows against bluish glass.
- **Reflection maps** for day, blue hour and night on hardware GL.
- **Scenery in motion**, see [ambient motion](ANUM_MINE_HQ_SCENERY_MOTION.md).
- **Heights.** Measured on the campus sizes: the glass office tower is 37 m to its roof plant (was 49 m), the executive HQ stays the landmark at about 58 m to the tip of the crown (was 58 m, but with a thin spire instead of the blades). The tower no longer repeats the executive silhouette.

### What still differs (honest list)

- It is still a low poly procedural scene. Glass shows no interior behind it (only lit panes), there is no ambient occlusion, no volumetric light, no
  bloom or shadow on software GL, and no real reflection of neighbouring buildings.
- The concept renders show 3 to 5 storey wide blocks with whole walls of glass and visible furnished interiors from outside; ours keep paned grids.
- No people on paths and plazas, no crowds, no delivery or forklift activity: that would invent activity (data rule).
- No tree-lined canals with boats, no stadium, no distant city skyline beyond the existing backdrop.
- Plaza lighting is a few halos, not the continuous glowing paths of the concept.
- Curved facades and green walls are not built; the vault and the raked crown blades are the only curved or raked forms.
- No CC0 glTF building models were added: nothing was found and licence reviewed in this pass, so the credits line is unchanged.
- Motion is off by default on software GL (CI) and was therefore verified there by switching it on (`anum.hq.ambient`), not by eye on a real GPU.

- The see-through rooms are a shader illusion repeated per pane, not the real floors: they do not match the interior layout or count of a building. The storey pitch of every multi-storey shell is now `STOREY` = 4.2 m, equal to `FLOOR_PITCH` in `lib/buildingScene.ts` (a unit test keeps them equal), so a glass storey lines up with floor n of the interior stack. The shell is still built from its own plan, not from each floor's plan, and its plinth sits 0.3 to 0.9 m above floor 0.

### Budgets

Renderer chunk 158.2 kB gzip (limit 160 kB), HQ entry chunk 138.6 kB (limit 140 kB on main); neither limit was raised. Per-asset triangle caps rose
(office tower 3800, admin block 2600, executive HQ 5600, personal villa 2400) because of the added detail; the campus uses the smaller sizes.

## Cutaway section, 26 October 2026

The building view opens as a dollhouse section like concept renders 22 and 24: front walls and roof removed by camera azimuth, all floors stacked, the open floor highlighted and the others dimmed, a Cutaway / Closed toggle and 90 degree rotate snaps. What it is, how it is built and what still differs from the renders: [cutaway](ANUM_MINE_HQ_CUTAWAY.md).
