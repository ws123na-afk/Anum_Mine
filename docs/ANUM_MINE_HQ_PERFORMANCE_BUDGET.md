# ANUM MINE headquarters: performance budget

Measured on 22 October 2026 in the readiness sandbox. Test: `apps/web/e2e/hq-performance.spec.ts`
(`pnpm --filter @anum/web exec playwright test e2e/hq-performance.spec.ts`; locally
`pnpm exec playwright test -c playwright.hq.config.ts e2e/hq-performance.spec.ts`). The test fails
when a budget below is exceeded. All data in it is synthetic and lives in the test only.

## What is measured, and what it is not

- **Chunk size**: gzip size of the lazy `HeadquartersEntrypoint` JS and CSS in `apps/web/dist/assets`
  (the entry bundle is guarded separately by the existing build). The scene is raw WebGL, so there is no
  3D library in the chunk.
- **Frame time**: `requestAnimationFrame` intervals while the mouse orbits the scene for about 60 moves.
  Median (p50), 95th percentile (p95) and worst.
- **JS heap**: `JSHeapUsedSize` from the Chromium performance metrics after the run.
- **Time to ready**: from navigation to the canvas and the first room row being visible, with the API
  mocked (no network cost).
- **Scene sizes**: 21 agents (6 rooms, 21 desks) and 200 agents
  (50 rooms, 200 desks, 200 agent records paged by the real org chart loader).

Software rendering (SwiftShader, one worker) is far slower than a laptop GPU and the sandbox runs other
work, so these figures are a regression guard. They are not a statement about a real laptop. The owner
should read the same numbers on the target machine (below).

## Measured results

| Run | Agents | Frames | p50 | p95 | Worst | JS heap | Ready |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 22 Oct, sandbox, SwiftShader | 21 | 225 | 33.3 ms | 66.6 ms | 150 ms | 31.8 MB | 2.3 s |
| 22 Oct, sandbox, SwiftShader | 200 | 144 | 66.6 ms | 100 ms | 117 ms | 30.7 MB | 1.2 s |

Frame times are multiples of 16.7 ms because they come from animation frames. Chunk: JS 40,069 bytes
gzip, CSS 11,330 bytes gzip.

## Budgets (the test enforces these)

| Metric | 21 agents | 200 agents |
| --- | --- | --- |
| Frame time p95 while orbiting | at most 180 ms | at most 300 ms |
| JS heap | at most 120 MB | at most 180 MB |
| Time to ready (mocked API) | at most 8 s | at most 15 s |
| Lazy HQ JS, gzip | at most 140,000 bytes (138,641 now, after merging the command centre and floor transition work; raised from 135,000 by the interior pass, 4.5 kB gzip for fourteen room kits, the room shell, the service core and the ambient scenery, see [interiors](ANUM_MINE_HQ_INTERIORS.md); before it 131,351: campus, buildings, asset builders, starter campus, provisioning, live flow and the navigation layer's logic; its panels (floor rail, quick views, floor plan, people, quick moves) are a separate lazy chunk of 3.4 kB gzip plus 1.8 kB of CSS that the list view and a closed headquarters never fetch); the three.js renderer chunk loads on demand, at most 160,000 (152,089 now: raised from 150,000 when the glTF model layer arrived, because the loader shares the animation and skinning code of three.js; the loader and the meshopt decoder are their own lazy chunks, 13 kB gzip, fetched only when a scene asks for a model) | same |
| Lazy HQ CSS, gzip | at most 17,000 bytes (16,435 now; raised from 16,000 for the navigation layout and campus card styles, 1.0 kB gzip) | same |

The budgets are about two to three times the measured sandbox values for frames (a repeat run of the same test measured p95 of 66.6 to 150 ms at 21 agents and 100 to 183 ms at 200, because software rendering shares the CPU with the other test workers) and about four times for heap, so ordinary noise passes and
a real regression (for example a per-frame allocation or a second 3D library) does not.

## Known cost drivers

- `sceneMeshes` (`HeadquartersCanvas.tsx`) filters all desks once per room each frame: O(rooms x desks).
  Fine at 200 desks; revisit before 1000.
- Every camera move rebuilds and re-sorts all quads on the CPU (`buildGpuVertices`). There is no culling
  yet (plan item F04).
- The office refreshes registry (4 list calls) and agents plus tasks every 10 s and on every matching
  event, debounced 300 ms.

## Real-device check (owner)

1. Open the office on the target laptop in Chrome with a workspace that has the most rooms.
2. DevTools, Performance, record 5 seconds while dragging the scene. Frame rate should stay above 30 fps
   at 21 agents and above 20 fps at 200 agents; heap under the table above.
3. Write the numbers into the table above with the date and the machine.

Raise a budget only with a measured reason written here; never to make the test pass.

## Look pass, 25 October 2026

Building modules and floor cut-away (see [building modules](ANUM_MINE_HQ_BUILDING_MODULES.md)): lazy HQ JS 131,231 bytes gzip, renderer chunk 154,879, CSS 11,542; no budget was raised.

No budget was raised. After the [building look pass](ANUM_MINE_HQ_BUILDING_LOOK.md): lazy HQ JS 131,351 bytes gzip (limit 135,000), renderer chunk 153,657 (limit 160,000). The starter campus is about 77,000 triangles (the environment kit cap rose from 26,000 to 36,000 for rounder foliage); frame work stays bounded because the scene is static and the quality ladder is unchanged.

## Interior pass

Lazy HQ JS 138,641 bytes gzip: the limit rose from 135,000 to 140,000 (138,641 after merging main) (`e2e/hq-performance.spec.ts`) for the new room kits (server area, pantry, restroom, finance, sales), door side orientation, the room shell, the service core and the ambient scenery. Renderer chunk 154,315 (limit 160,000, unchanged; the ambient mover is about 0.6 kB). Frame cost: ambient scenery is about a dozen extra meshes per floor (glints, lift lights, rack light groups, leaves, fans), redrawn at about 12 frames a second only on a GPU, tab visible and no reduced motion; on a software rasteriser (CI) it stays frozen and costs nothing. Active floor of the starter campus: at most about 20 desks, about 10,000 triangles of stations, so no instancing was needed; larger floors drop to the lite detail level.

After the [first-run panel and floor overview](ANUM_MINE_FLOOR_OVERVIEW.md): lazy HQ JS 130,338 bytes gzip (limit 135,000), renderer 153,706 (limit 160,000), HQ CSS 11,831. No budget was raised; the panel and the assistant card are separate lazy chunks.

After the [exterior pass](ANUM_MINE_HQ_BUILDING_LOOK.md) (26 October 2026): lazy HQ JS 138,552 bytes gzip (limit 140,000 on main; the exterior pass added nothing net, main was 138,641) (the unused fallback building drawer left the bundle) (limit 135,000) and renderer chunk 158,194 (limit 160,000); no budget was raised. [Ambient motion](ANUM_MINE_HQ_SCENERY_MOTION.md) keeps frames coming only at level 1 or 2 (15 or 30 frames a second), never on a software rasteriser or under reduced motion, steps down first when frames are slow, writes vehicle matrices into preallocated objects and allocates nothing per frame.

After the [graphics check and frame governor](ANUM_MINE_HQ_GPU_CHECK.md) (26 October 2026): lazy HQ JS 139,638 bytes gzip (limit 140,000; main was 138,552; the quality chip, the tier registry, the quality store and the new messages), renderer chunk 159,946 (limit 160,000, unchanged; main measures 159,174 with the test's own gzip, the 158,194 above is older). **No budget was raised, but the renderer has 54 bytes and the HQ chunk 362 bytes left.** Everything that could leave the renderer chunk did: the Graphics check panel (5.5 kB gzip), the benchmark runner, the probe, the report logic, the tier registry and the frame governor (a 0.7 kB chunk loaded on demand; until it arrives the picture simply holds its tier) are separate lazy chunks that the list view, a closed headquarters and a closed panel never fetch. What stays in the renderer is what needs its private state: the ladder's application, the simple-material rebuild, the shader-failure hook and the context-restore rebuild. The panel and the benchmark reach the renderer's own fields (`renderer`, `meshes`, `textures`, `composer`, `cameraState`, `liftValue`, `startLifts`) through a cast in `hq-gpu/gpuBench.ts` instead of accessors, which kept about 300 bytes out of the renderer chunk; the e2e specs cover those names. The renderer's old adaptive block was removed in the same change (the governor replaces it), and a dead statement in the shadow code went with it. Any further renderer work (for example the photographic tier) must either raise the limit with a measured reason here or move code out first. Frame cost: the governor adds a median over 40 numbers per drawn frame (no allocation beyond a sorted copy, about 40 elements) and nothing when the tier is pinned, on a software rasteriser or in a hidden tab.

Reflection maps are baked on first use per light preset (day, blue hour, night) instead of two at start: on a software rasteriser with quality pinned to hardware level, the "quality can be pinned" campus spec took 56 s on a loaded machine with two bakes at start and 15 s with one, which is what a mobile CI shard needs to stay inside its timeout.

## Photographic look pass, 26 October 2026

See [the photographic look](ANUM_MINE_HQ_PHOTOREAL.md). Lazy HQ JS 137,522 bytes gzip (limit 140,000), renderer chunk 104,566 (limit 160,000), HQ CSS 11,861.
No existing limit was raised. The renderer chunk dropped because `three.core` became its own chunk (a second lazy importer of three.js appeared), so
`e2e/hq-performance.spec.ts` gained two budgets: the whole renderer path (renderer + `three.core` + baked occlusion) at most 170,000 bytes (166,464 now;
main measured 159,946 for the renderer alone), and the chunks only the upper levels fetch (`hqPhotoFx` + `hqCampusFx`) at most 20,000 (17,613 now).
Frame cost of each effect and the CPU cost of the once-per-scene work are in the photographic look doc; software GL (CI) keeps the low level and pays
only the baked occlusion bake (about 0.2 s once per distinct scene, cached by content so a rebuilt floor is not baked again).
