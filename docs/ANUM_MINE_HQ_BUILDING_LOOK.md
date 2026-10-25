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
