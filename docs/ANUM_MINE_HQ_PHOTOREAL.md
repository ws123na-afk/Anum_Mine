# ANUM MINE headquarters: the photographic look and its quality tiers

Status: built on 26 October 2026 as the next pass after the [building look](ANUM_MINE_HQ_BUILDING_LOOK.md). The owner compared the campus
with the concept renders (images 21 to 30 of the [visual reference](ANUM_MINE_VISUAL_REFERENCE.md)) and said the buildings still looked built
from simple shapes. This pass raises the fidelity of materials, light, occlusion and the final image, and keeps the software GL path that CI
runs exactly as light as it was: every new effect that costs frame time belongs to a higher tier that a software rasteriser never enables
on its own. The renders are style references only; no name, number, person or activity of them is reproduced (scenery only, data truth rules
unchanged).

## Quality tiers

One number, 0 to 4, decides what is drawn (`lib/hqQualityTier.ts`, pure and unit tested). It starts from what the browser reports and moves with
measured frame time.

| Tier | Name | What is on | Who gets it |
|---|---|---|---|
| 0 | basic | simple lit materials (Lambert), no shadow map, no bloom, no reflection map, baked occlusion | a software rasteriser (SwiftShader, llvmpipe, Basic Render Driver): all of CI |
| 1 | light | PBR materials, reflection map, shadows 1024 | pin only |
| 2 | standard | shadows 1024, bloom at night | a very large canvas (more than 4 million device pixels) on hardware |
| 3 | high | shadows 2048, bloom, contact discs, roof and window detail | the default on hardware |
| 4 | photo | tier 3 plus everything listed under "Tier 4" below | a pin, or the measured promotion on a discrete or Apple GPU |

How the tier is found:

1. **Renderer string.** `WEBGL_debug_renderer_info` on a scratch canvas is classified (`classifyRenderer`): software, discrete (NVIDIA, AMD Radeon RX
   and Pro, Intel Arc), Apple, integrated (Intel UHD and Iris, Radeon Graphics), mobile (Adreno, Mali, PowerVR) or unknown.
2. **Pixels.** Device pixels of the canvas at the current pixel ratio: above 4 million the start is tier 2.
3. **Measured frame time.** At tier 3 on a discrete or Apple GPU, once and about a second after the frames are warm, the renderer draws 24
   frames back to back with a GPU finish after each (so it times the GPU, not command submission). If the median is at most 7 ms and none is
   above 21 ms, it steps up to tier 4. It never promotes after a pin, after a step down, under reduced motion, on integrated, mobile or unknown
   GPUs, or on a canvas above 4 million pixels (`shouldPromote`).
4. **The ladder down.** Twenty slow frames in a row take one step off (a frame is slow above 30 ms, above 22 ms at tier 4). Ambient scenery motion
   goes first, then tier 4 returns to tier 3 for good (PBR detail, post stack and point lights off, no promotion again), then bloom, the shadow map
   size, shadows and the pixel ratio, as before. A pass that throws on a GPU (a shader that does not compile) also returns to tier 3 and the frame is
   drawn the plain way.

**Reaching tier 4 by hand (owner, on a real GPU).** In the browser console of the headquarters page:

```js
localStorage.setItem('anum.hq.quality', '4'); location.reload();
```

`0` to `3` pin the lower tiers, `localStorage.removeItem('anum.hq.quality')` returns to detection. The canvas shows what is running:
`data-tier` is the tier and `data-fx` is a list of the effects on (`ao,contact,pbr,gtao,dof,shafts,grain,lights,cascade,agx`). To judge one
effect, switch it off by name (`localStorage.setItem('anum.hq.fxoff', 'gtao,dof')`, names: `ao`, `detail`, `contact`, `pbr`, `gtao`, `dof`,
`shafts`, `grain`, `bloom`, `lights`, `cascade`) and reload. With `localStorage.setItem('anum.hq.bench', '1')` the page also offers
`__anumHqBench(3, ['gtao'])` in the console: median milliseconds of 3 frames (each ended by a GPU finish) and the triangles and draw calls, with the
named effects off. That is how the costs below were measured, and the owner can read the same numbers on the target machine.

## What each tier adds

### Material library (procedural, tier 4)

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
- **Soft shadows with a cascade like fit (tier 4):** one 4096 shadow map fitted to what the camera looks at, not to the whole campus
  (`fitShadow`): the radius follows the camera distance, the centre is snapped to whole texels so edges do not crawl, and the filter radius is 4.
  Tiers 2 and 3 keep the region wide map (1024 and 2048).
- **Warm interior point lights (tier 4):** the four lamp and entrance halos nearest the look-at point become real point lights (2700 K style warm
  colours from the scene's halo data), fully on from the blue hour, off by day. Four is the budget: the shader count never changes, so nothing
  recompiles while the camera moves.
- **Emissive window glow and bloom:** unchanged (lit panes are emissive, bloom from tier 2 at night). Tier 4 keeps a faint bloom by day too.
- **Light shafts (tier 4):** a 28 step radial blur of the pixels brighter than the threshold towards the sun's screen position, in linear light before
  tone mapping; strongest at the blue hour, faded when the sun is outside the picture or behind the camera, off under reduced motion
  (`sunOnScreen`, `gradeFor`).

### Occlusion

- **Baked vertex AO (every tier, costs nothing per frame):** `lib/hqBakedAo.ts`. Triangles become area weighted occluder samples on a hash grid; each
  vertex sums the form factor of the samples in 3 m that are in front of it and face it, so inside corners, balcony undersides and the space between desks
  darken while flat ground and coplanar neighbours do not. Walls also darken towards the floor they stand on, and tall wall triangles are cut at 0.6, 1.8, 3.6
  and 7.2 m and so on above their floor so a 40 m wall has vertices to carry the gradient. Windows and lit surfaces are left alone. Each distinct scene is
  baked once (cached by vertex array and by content, so a floor redrawn with the same geometry is not baked again). A software renderer bakes at most
  260 000 vertices.
- **Contact shadows (tiers 1 to 4, campus):** soft discs under low objects (the side faces of anything 0.35 to 3.2 m high that stands on a floor level:
  cars, benches, planters), under every canopy of foliage, and along the foot of building walls, as one transparent mesh with a radial falloff. At tier 4
  the discs stay out of the occlusion and focus prepasses (they rendered as solid black patches there, found and fixed in this pass).
- **Screen space occlusion (tier 4 only):** GTAO with a world radius of 2.6 m, 12 samples and a Poisson denoise.

### Final image (tier 4 only)

AgX tone mapping (flatter and without ACES's built in exposure gain, so exposure, saturation and a gentle contrast curve per preset in `gradeFor`
compensate), a subtle depth of field (the focus follows the camera distance and the blur is capped at 0.4 percent of the picture, so a close camera
softens a far skyline and a far camera barely changes anything), then, after tone mapping, a vignette, film grain (static under reduced motion) and an
ordered dither against banding.

### Geometry detail (tiers 1 to 4, campus)

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

COST_TABLE

CPU work that happens once per scene (node, same machine): generating all eight PBR maps 63 ms; baking 84 000 vertices 175 to 225 ms; deriving contact
discs 40 to 90 ms; roof and window detail for the seven building kinds 270 to 400 ms in all.

## Budgets

BUDGET_TEXT

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
- Tier 4 on a software rasteriser takes minutes for its first frame (shader compile and a heavy post stack); it was checked there for single frames only
  (screenshots), never in a CI loop. Real GPU frame cost is not measured here.
- The step down ladder reads CPU time per frame. A GPU bound slow frame shows up there only through the work the driver blocks on; the promotion probe
  does time the GPU (it finishes each frame).
- The PBR maps bind to surface classes, so one marble map serves every tile surface, polished lobby floors and outdoor paving alike.
- Software GL, and every browser without WebGL 2, keep the previous look (plus baked occlusion on WebGL 2 software).

## Tests

Unit (`node --test`): `test/hqQualityTier.test.ts` (renderer strings, pins, promotion, fx names), `test/hqPbrTextures.test.ts` (determinism, tiling,
ranges, anisotropy, seams, wear), `test/hqBakedAo.test.ts` (levels, wall refinement, corner and base occlusion, ground stays 1, contacts, caps, content
key, timing), `test/hqDetailPass.test.ts` (coping and seams, plant clear of obstacles, sills, planters, caps, all seven exterior kinds),
`test/hqLightRig.test.ts` (fitted shadow, point lights, sun on screen, grade per preset, depth of field), `test/hqPhotoShaders.test.ts` (chunk shape and
names). Browser (`e2e/hq-photoreal.spec.ts`): non blank frame, no sideways scroll, tier 0 and baked occlusion on a software rasteriser at 1440x900,
1366x768, 1024x768, 768x1024 and 390x844; the quality pin sets the tier; contact discs arrive from the pinned tier 3; a bad pin is ignored; pin 4 loads
the photographic chunk and the fitted 4096 shadow map (checked at its start). Screenshot and cost tools (not CI): `e2e/tools/hqPhotoShots.shots.ts` and
`e2e/tools/hqPhotoBench.shots.ts`, run with `playwright.hq.shots.config.ts`.
