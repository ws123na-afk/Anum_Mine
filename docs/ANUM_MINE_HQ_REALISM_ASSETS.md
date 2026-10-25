# ANUM MINE headquarters: realism pass, glTF models and gap analysis

Status: vertical slice (draft). It adds a glTF model pipeline to the existing three.js renderer, three loadable
furniture and person models, glass and facade upgrades and a night glow, and records, honestly, what still differs from the
owner's concept renders. The extension points and the scene description live in
[ANUM_MINE_HQ_EXTENSION_POINTS.md](ANUM_MINE_HQ_EXTENSION_POINTS.md); the style source is the owner-approved
[visual reference](ANUM_MINE_VISUAL_REFERENCE.md). Only the style of the concept renders is used, never their panels or data.

## Gap analysis (concept renders against the render before this change)

Compared: "ANUM MINE Virtual Headquarters - Final Concept", "Futuristic Office Command Center" and "Digital Twin Campus" (all in the
owner's mock folder) against the building and campus renders at 1440x900. Ranked by visual impact.

| Rank | Area | Concept renders | Our render before | Status after this change |
|---|---|---|---|---|
| 1 | Interior density and life | 20 to 30 desks per floor, a person at nearly every desk, plants in every corner, shelving, rugs, lounge sets | the registry fixture has 6 desks on 2 floors; rooms hold the stock furniture only | Not fixable by the renderer: people and desks come only from registry records (data truth). Furniture density per room is unchanged. Owner decision 1 |
| 2 | Light and mood | warm pools of light, dusk or night, glowing glass, reflections on stone and metal | flat colours, one sun, no glow around lamps | Warm night halos at lamps, entrances and the executive crown; sky reflecting glass; soft contact shadows under models |
| 3 | Camera and framing | oblique 35 to 40 degrees, the building fills the frame, floors read as a model | building filled about half of the stage, wide lawn and parking around it | Tighter framing (about 25 percent closer) and a smaller lawn; pitch unchanged |
| 4 | Facade detail and massing | stone and glass campus: fins, deep reveals, ledges, terraces, a columned entrance, a stepped tower | near black flat glass boxes, thin bands only | Lighter sky blue glass that brightens with height and varies pane to pane; stone fins and sunshade ledges on the executive HQ, the glass tower and the admin block; the executive tower now steps in with a terrace. Courtyards, balconies and deep colonnades are not built |
| 5 | Glass | clear partitions, believable reflections, mullions | a flat 22 percent tint | Partition glass uses a Fresnel term (clear face on, mirror like at grazing angles); outer glass reflects the sky map |
| 6 | Character realism | small realistic people in business clothing, many poses | boxy figures | Seated corporate people from glTF models (stylised, see limits below). Realistic people need models the owner supplies. Owner decision 2 |
| 7 | Materials | wood grain, stone, brushed metal, fabric | procedural canvas textures, flat plastic look on furniture | Navy velvet sofa and lounge chair with real sheen and textures; rest unchanged |
| 8 | Planting and ground | lush tropical palms, paving, water | low poly palms, flat green lawn | unchanged |
| 9 | UI chrome | directory panel left, detail panel right, floating labels inside the canvas | page panels below and around the canvas | out of scope (style only) |

## Loadable models

The renderer loads glTF binaries lazily, with the 3D chunk and only for the keys the scene asks for.

- Catalogue and truth rules: `apps/web/src/lib/hqModels.ts` (pure). A person is placed only for a slot the interior builders
  created for a station that carries an `occupantSeed` (a registry assignment or a live presence record); the look comes from a
  hash of that seed, never from a name; nothing invents a person, a walker or a crowd.
- Cache and fallback: `lib/hqModelCache.ts` (pure, injected loader). Each model loads once, never rejects, times out after 20 s.
  A group is ready when its models loaded (all of them for furniture, any person look for people); otherwise the procedural piece
  stays on screen. The stand-ins are tagged triangles of the same mesh (`MeshBuilder.tagged`), shipped as separate render items
  (`fallbackOf`) and hidden only while a loaded model replaces them, so a rebuild of the scene never flickers.
- Renderer: `lib/hqModelLayer.ts` (three.js). `GLTFLoader` with the meshopt decoder; each model is baked to static geometry
  (a seated person is the file's `sit` clip, skinned once on the CPU, so there is no per frame skinning) and drawn as one
  `InstancedMesh` per part; a soft contact shadow (one instanced plane) sits under every placed model; the night halos are one
  instanced additive billboard layer. On a software rasteriser the model materials switch to Lambert.
- Truth hooks: `sofa()` and `armchair()` (furniture) and `seatedFigure()` (figures) note a slot on the builder; nothing else does.
- Test hooks: the canvas dataset shows `data-models-placed`, `data-model-figures`, `data-models-loaded` (loaded over asked),
  `data-model-bytes`, `data-model-load-ms` and `data-model-fallbacks`.

### Assets, licences and sizes

All files are in `apps/web/public/hq/models/`; `NOTICE.txt` next to them carries the same record. Total shipped: see the table.

| File | What | Source | Licence | Bytes |
|---|---|---|---|---|
| `sofa.glb` | navy velvet sofa, 2.5k triangles | glTF-Sample-Assets, GlamVelvetSofa, Wayfair, LLC | CC BY 4.0 (attribution in NOTICE) | 100 376 |
| `armchair.glb` | orange velvet lounge chair, 6k triangles | glTF-Sample-Assets, SheenChair, Wayfair, LLC | CC BY 4.0 (attribution in NOTICE) | 285 372 |
| `person_0.glb` to `person_4.glb` | seated person in business clothing, about 750 triangles each | Kenney, Mini Characters | CC0 1.0 | 55 080 to 56 320 each |

Changes made to the originals: simplified, textures resized (WebP), meshopt compressed, embedded lights, material variants and
unused animation clips removed. The Kenney files came from the npm package `@netforge/asset-library-files` 0.2.0 (a third party
mirror whose NOTICE states the CC0 terms) because kenney.nl is not reachable from the build sandbox: confirm the licence on
kenney.nl before a public release (owner decision 3).

### Where assets can and cannot be fetched (build sandbox, 25 October 2026)

Reachable through the proxy: `raw.githubusercontent.com` (any public repository file, used for the Khronos sample assets),
`registry.npmjs.org` (used for the gltf-transform tools and for the mirror above). Blocked (proxy 403 or refused):
`github.com`, `codeload.github.com`, `api.github.com`, `cdn.jsdelivr.net`, `unpkg.com`, `quaternius.com`, `kenney.nl`,
`polyhaven.com`, `dl.polyhaven.org`, `ambientcg.com`, `poly.pizza`, `opengameart.org`, `sketchfab.com`, `market.pmnd.rs`.
So Quaternius, Poly Haven and ambientCG assets could not be fetched; no office desk, office chair or realistic person exists
among the permissive assets that could be reached. Desks, task chairs, monitors, shelving and plants therefore stay procedural.
The honest alternatives: the owner supplies GLB files (see owner decisions), or the procedural pieces are improved further.

## What changed in the renderer and the scenes

- Glass: the outer glass reflects a sky gradient (brighter with height) with a small per pane change; partition glass has a
  Fresnel term. Both are shader patches in `hqThreeRenderer.ts`; the software rasteriser path is unchanged.
- Facades: `fins()` and `ledge()` in `hqAssets/exterior/parts.ts`; used by the executive HQ, the glass tower and the admin block.
  The executive tower steps in above a terrace. Triangle caps stay inside `TRIANGLE_CAPS`.
- Night: `MeshBuilder.halo()` notes a halo; street lamps, floor lamps and entrances call it; the renderer draws one additive billboard
  each, scaled by the night factor. Bloom stays as it was (quality 2 and above, never under reduced motion).
- Building view: tighter framing, a glass balustrade with a steel rail on the two near sides, a smaller lawn.

## Measured (software rasteriser: not GPU numbers)

Measured in headless Chromium on SwiftShader (software GL), so the milliseconds below say nothing about a real GPU; the triangle,
draw call and byte counts do not depend on the rasteriser.

MEASURED_TABLE

## Still different from the concept renders

STILL_DIFFERENT

## Owner decisions needed

OWNER_DECISIONS
