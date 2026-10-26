# ANUM MINE headquarters: the photographic look and its quality tiers

Status: built on 26 October 2026 as the next pass after the [building look](ANUM_MINE_HQ_BUILDING_LOOK.md). The owner compared the campus
with the concept renders (images 21 to 30 of the [visual reference](ANUM_MINE_VISUAL_REFERENCE.md)) and said the buildings still looked built
from simple shapes. This pass raises the fidelity of materials, light, occlusion and the final image, and keeps the software GL path that CI
runs exactly as light as it was: every new effect that costs frame time belongs to a higher tier that a software rasteriser never enables
on its own. The renders are style references only; no name, number, person or activity of them is reproduced (scenery only, data truth rules
unchanged).

## Quality tiers

The registered tiers are the ones of `lib/hqQualityTiers.ts` (the tier registry the Graphics check and the frame governor read): 0 low, 1 medium, 2 high,
3 ultra. The photographic look is an **extension on top of the ultra tier**, not a fifth registered tier: the Graphics check benchmarks every registered
tier in CI on a software rasteriser, and a photographic frame takes minutes to compile there, so it stays out of the registry and is reached by its own
pin or its own measured promotion. Decisions that are pure live in `lib/hqQualityTier.ts` (singular: GPU classes, promotion, effect names) and are unit tested.

| Level | What is on | Who gets it |
|---|---|---|
| 0 low | simple lit materials, no shadow map, no bloom, no reflection map, baked occlusion | a software rasteriser (SwiftShader, llvmpipe, Basic Render Driver): all of CI |
| 1 medium | PBR materials, reflection map, shadows 1024, contact discs, roof and window detail | a pin, or a rung of the governor |
| 2 high | shadows 1024, bloom at night | a very large canvas (more than 4 million device pixels) on hardware |
| 3 ultra | shadows 2048, bloom, everything above | the default on hardware |
| photo (pin 4) | ultra plus everything under "Photographic extension" below | a pin, or the measured promotion on a discrete or Apple GPU |

How the level is found:

1. **Renderer string.** `WEBGL_debug_renderer_info` on a scratch canvas is classified (`classifyRenderer`): software, discrete (NVIDIA, AMD Radeon RX
   and Pro, Intel Arc), Apple, integrated (Intel UHD and Iris, Radeon Graphics), mobile (Adreno, Mali, PowerVR) or unknown. Software starts at low.
2. **Pixels.** Device pixels of the canvas at the current pixel ratio: above 4 million the start is high instead of ultra.
3. **Measured frame time, up.** On ultra, on a discrete or Apple GPU, once and about a second after the frames are warm, the renderer draws 24 frames
   back to back, each ended by a one pixel read that waits for the GPU (`finish` does not wait in Chrome, so the probe times the GPU and not command
   submission). If the median is at most 7 ms and none is above 21 ms, the photographic effects switch on (`shouldPromote`). Never after a pin or a
   step down, under reduced motion, on integrated, mobile or unknown GPUs, or on a canvas above 4 million pixels.
4. **Measured frame time, down.** The frame governor of `hqGovernor.ts` walks its ladder on slow frames (pixel ratio, shadow map, shadows, bloom, simple
   materials). The photographic effects run on the full rung only: the first step down ends them for good (PBR detail, post stack and point lights off,
   no promotion again). A pass that throws on a GPU also ends them and the frame is drawn the plain way.

**Reaching the photographic extension by hand (owner, on a real GPU).** In the browser console of the headquarters page:

```js
localStorage.setItem('anum.hq.quality', '4'); location.reload();
```

`0` to `3` pin the registered tiers (or use the Graphics check), `localStorage.removeItem('anum.hq.quality')` returns to detection. A pin wins over the
governor, so with `4` nothing steps the effects down: the owner judges them as drawn. The canvas shows what is running: `data-tier` is 4 once the
extension has drawn a frame (3 before) and `data-fx` lists the effects on (`ao,contact,pbr,gtao,dof,shafts,grain,lights,cascade,agx`). To judge one
effect, switch it off by name (`localStorage.setItem('anum.hq.fxoff', 'gtao,dof')`, names: `ao`, `detail`, `contact`, `pbr`, `gtao`, `dof`, `shafts`,
`grain`, `bloom`, `lights`, `cascade`) and reload. With `localStorage.setItem('anum.hq.bench', '1')` the page also offers `__anumHqBench(3, ['gtao'])`
(median milliseconds of 3 frames, each ended by a GPU wait, with the triangles and draw calls, the named effects off) and `__anumHqTune({grade:
{contrast: 0.5}, aoRadius: 3})` (grade and occlusion overrides while running). That is how the costs below were measured and the look was tuned, and the owner
can read the same numbers on the target machine.

## What the look adds

### Material library (procedural, photographic extension)

`lib/hqPbrTextures.ts`: eight generated 128 x 128 maps, 64 kB each, made at start from an integer hash (no random state, no image files, no network),
all deterministic and tileable. Each texel holds a tangent normal (R, G), a roughness multiplier (B) and a cavity or thickness value (A).

| Kind | What it models | Used on |
|---|---|---|
| concrete | pores, tie holes, formwork joints, soft blotches | concrete surface class (walls, plinths, slabs) |
| brushedMetal | streaks along one axis, anisotropic roughness | metal and roller shutters |
| wood | four planks with rings, seams, grain gloss | wood floors and decks |
| marble | polished stone, veins that roughen the surface | tile class (interior floors, lobby slabs, paving) |
| carpet | tufts, very rough | cloth class |
| asphaltWorn | aggregate, polished wear blotches, cracks | roads and car parks |
| paving | 4 x 4 slabs with grout and height steps | courts (the plain paving albedo keeps its own joints) |
| leaf | midrib, veins, thin blade | foliage, with translucency below |

`lib/hqPhotoShaders.ts` samples them triplanar from the world position (no UV unwrap), bends the view space normal, scales roughness and darkens
cavities, all faded out between the distances the albedo detail already uses, so far surfaces do not shimmer. Foliage also gets sunlight through thin
parts of a leaf when the sun is behind it. Facade glass is the existing shader (sky reflection per facing, Fresnel, sun glint, a lit room behind each
pane) with the reflection probe of the light preset; it was already the glass of the previous pass.

### Lighting

- **Image based light:** the sky shader is baked once per preset (day, blue hour, night) into a reflection and diffuse probe (unchanged, lazy per
  preset). Sun and sky colours per preset are the `lib/hqLighting.ts` keyframes.
- **Soft shadows with a cascade like fit (photographic extension):** one 4096 shadow map fitted to what the camera looks at, not to the whole campus
  (`fitShadow`): the radius follows the camera distance, the centre is snapped to whole texels so edges do not crawl, and the filter radius is 4.
  The registered tiers keep the region wide map (1024 and 2048).
- **Warm interior point lights (photographic extension):** the four lamp and entrance halos nearest the look-at point become real point lights (2700 K style warm
  colours from the scene's halo data), fully on from the blue hour, off by day. Four is the budget: the shader count never changes, so nothing
  recompiles while the camera moves.
- **Emissive window glow and bloom:** unchanged (lit panes are emissive, bloom from tier 2 at night). The extension keeps a faint bloom by day too.
- **Light shafts (photographic extension):** a 28 step radial blur of the pixels brighter than the threshold towards the sun's screen position, in linear light before
  tone mapping; strongest at the blue hour, faded when the sun is outside the picture or behind the camera, off under reduced motion
  (`sunOnScreen`, `gradeFor`).

### Occlusion

- **Baked vertex AO (every tier, costs nothing per frame):** `lib/hqBakedAo.ts`. Triangles become area weighted occluder samples on a hash grid; each
  vertex sums the form factor of the samples in 3 m that are in front of it and face it, so inside corners, balcony undersides and the space between desks
  darken while flat ground and coplanar neighbours do not. Walls also darken towards the floor they stand on, and tall wall triangles are cut at 0.6, 1.8, 3.6
  and 7.2 m above their floor so a wide wall has vertices to carry the gradient (thin fins and mullions are left alone: cutting every tall quad nearly doubled the triangles, cutting wide faces only adds 9 percent). Windows and lit surfaces are left alone. Each distinct scene is
  baked once (cached by vertex array and by content, so a floor redrawn with the same geometry is not baked again). A software renderer bakes at most
  260 000 vertices.
- **Contact shadows (every level but low, campus):** soft discs under low objects (the side faces of anything 0.35 to 3.2 m high that stands on a floor level:
  cars, benches, planters), under every canopy of foliage, and along the foot of building walls, as one transparent mesh with a radial falloff. In the extension
  the discs stay out of the occlusion and focus prepasses (they rendered as solid black patches there, found and fixed in this pass).
- **Screen space occlusion (photographic extension only):** GTAO with a world radius of 3.2 m, 16 samples and a Poisson denoise.

### Final image (photographic extension only)

AgX tone mapping (flatter and without ACES's built in exposure gain, so exposure, saturation and a gentle contrast curve per preset in `gradeFor`
compensate), a subtle depth of field (the focus follows the camera distance and the blur is capped at 0.4 percent of the picture, so a close camera
softens a far skyline and a far camera barely changes anything), then, after tone mapping, a vignette, film grain (static under reduced motion) and an
ordered dither against banding.

### Geometry detail (every level but low, campus)

Derived from the scene's own triangles (`lib/hqDetailPass.ts`, loaded as `hqCampusFx`), not hand placed per building, so it fits every building kind:

- **Roof coping:** a stone cap 0.5 m wide and 0.22 m high along every real roof edge (an edge used once whose other side is not roof; the seam between
  two roof pieces gets none).
- **Rooftop plant:** air conditioning units with fans, vent stacks and plant housings on free roof area (nothing else standing on the roof, 1 m from the edge).
- **Window sills:** a slim stone sill under every sixth pane, at most 160 a building, so the sun draws a shadow line under the glass.
- **Planters:** concrete troughs with a clipped shrub along the foot of ground floor walls every 12 m, never next to a lit entrance or on something already there.
- All capped at 2600 triangles per building. Nothing here draws a person, a name, a count or a brand.

## Measured cost

Measured on 26 October 2026 in the sandbox on a **software rasteriser (SwiftShader)** shared with other agents (load average above 30): the
milliseconds are CPU rasterising time, good for ratios only. They are not a claim about a GPU. The owner measures the same numbers on the target
machine with `__anumHqBench` (above).

Starter campus (8 buildings) at night, 1280 x 720, photographic extension pinned, median of 3 frames each ended by a GPU wait
(`e2e/tools/hqPhotoBench.shots.ts`). Triangles are everything the frame drew, counting every pass: the occlusion and focus passes each draw the scene
once more (130 000 triangles each here), the shadow pass is one more.

| Configuration | Milliseconds (software, noisy) | Triangles drawn | Draw calls |
|---|---|---|---|
| everything on | 4377 | 396 201 | 421 |
| baked occlusion off | 3508 | 379 017 | 421 |
| roof and window detail off | 4105 | 356 949 | 316 |
| contact discs off | 4058 | 387 135 | 420 |
| PBR detail maps off | 3924 | 396 201 | 421 |
| GTAO off | 3752 | 267 159 | 284 |
| depth of field off | 3997 | 267 162 | 287 |
| light shafts off | 4125 | 396 200 | 420 |
| grain, vignette and grade off | 4584 | 396 200 | 420 |
| bloom off | 3750 | 396 188 | 408 |
| point lights off | 3761 | 396 201 | 421 |
| camera fitted shadow off | 3940 | 396 201 | 421 |
| everything off (the ultra tier as it was) | 1770 | 110 227 | 99 |

How to read it: the whole extension costs about 2.5 times the ultra frame on this software rasteriser, and the machine was shared (load average 12
to 35), so a single row is good to about plus or minus 500 ms: what is solid is the sum, the triangle counts (exact) and the order of the big items (the
two prepass based passes, bloom and the shadow and lighting work) against the shader only ones (PBR maps, shafts, grain, which add no triangles and a
few arithmetic operations per pixel). Counted over all passes, baked occlusion adds about 17 000 triangles (wall cuts: 9 percent more wall triangles), the detail pass about 39 000 and the
contact discs about 9 000. A GPU will not show these ratios; measure with `__anumHqBench`.


CPU work that happens once per scene (node, same machine): generating all eight PBR maps 63 ms; baking 84 000 vertices 175 to 225 ms; deriving contact
discs 40 to 90 ms; roof and window detail for the seven building kinds 270 to 400 ms in all.

## Budgets

Gzip sizes of the built chunks (26 October 2026, after merging main with the frame governor, the Graphics check and the Arabic names):

| Chunk | Bytes | Limit | Loaded by |
|---|---|---|---|
| `HeadquartersEntrypoint` JS | 134 318 | 140 000 (unchanged) | the headquarters page |
| `hqThreeRenderer` | 104 562 | 160 000 (unchanged) | the 3D scene |
| `three.core` (new, split out) | 58 675 | - | the 3D scene |
| `hqSceneFx` + `hqBakedAo` | 269 + 2 963 | - | every level, baked occlusion |
| `hqCampusFx` | 3 592 | - | every level but low: roof and window detail, contact discs |
| `hqPhotoFx` | 14 034 | - | the photographic extension only |

The renderer chunk alone fell from 159 967 (33 bytes under its limit on main) to 104 562 because the bundler moved `three.core` into its own chunk when
a second lazy chunk (`hqPhotoFx`) also imported three.js. That is a re-shuffle, not a saving, so `e2e/hq-performance.spec.ts` now also budgets the whole
path a lower level downloads: renderer + `three.core` + baked occlusion = 166 469 bytes against a new limit of 170 000 (main's equivalent was 159 967: the
extension costs a low level 6.5 kB, all of it baked occlusion and the glue), and the two chunks only the upper levels fetch, 17 626 bytes against 20 000.
No existing limit was raised; the two new ones are justified here. Software GL at the low level fetches the renderer path only (it bakes at most 260 000
vertices, no detail, no contact discs).


## Before and after

COMPARE_TEXT

## Honest list of what still differs

- It is still a low poly procedural scene. The post stack makes it read as lit and deep; it does not make it a photograph. The concept renders have
  thousands of small objects per building (people, vehicles in motion on every road, signage, planting in every bed): people are excluded by the data rule
  and the rest is not built.
- Bevels and chamfers on building edges are not built (only roof coping and window sills add edge relief), and no window reveals (a recessed frame
  around each pane): the sills and the baked occlusion stand in for them.
- Street furniture is the existing set plus planters; instanced vegetation variety (extra species and size jitter) was not added.
- Contact discs cover the campus, not the interior floors: the floor cut-away moves and flattens floors, and discs under desks would have to follow
  them. Baked AO covers the interiors.
- The shadow fit leaves the shadow map's sphere hard edged: beyond it there are no shadows (fog hides most of it).
- The photographic extension on a software rasteriser takes minutes for its first frame (shader compile and a heavy post stack); it was checked there for single frames only
  (screenshots), never in a CI loop. Real GPU frame cost is not measured here.
- The step down ladder reads CPU time per frame. A GPU bound slow frame shows up there only through the work the driver blocks on; the promotion probe
  does time the GPU (it finishes each frame).
- The PBR maps bind to surface classes, so one marble map serves every tile surface, polished lobby floors and outdoor paving alike.
- Software GL, and every browser without WebGL 2, keep the previous look (plus baked occlusion on WebGL 2 software).

## Tests

Unit (`node --test`): `test/hqQualityTier.test.ts` (renderer strings, promotion, fx names), `test/hqPbrTextures.test.ts` (determinism, tiling,
ranges, anisotropy, seams, wear), `test/hqBakedAo.test.ts` (levels, wall refinement, corner and base occlusion, ground stays 1, contacts, caps, content
key, timing), `test/hqDetailPass.test.ts` (coping and seams, plant clear of obstacles, sills, planters, caps, all seven exterior kinds),
`test/hqLightRig.test.ts` (fitted shadow, point lights, sun on screen, grade per preset, depth of field), `test/hqPhotoShaders.test.ts` (chunk shape and
names). Browser (`e2e/hq-photoreal.spec.ts`): non blank frame, no sideways scroll, tier 0 and baked occlusion on a software rasteriser at 1440x900,
1366x768, 1024x768, 768x1024 and 390x844; the quality pin sets the tier; contact discs arrive from the pinned ultra tier; a bad pin is ignored; pin 4 loads
the photographic chunk (checked at its start). Screenshot and cost tools (not CI): `e2e/tools/hqPhotoShots.shots.ts` and
`e2e/tools/hqPhotoBench.shots.ts`, run with `playwright.hq.shots.config.ts`.
