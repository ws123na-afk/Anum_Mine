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

All files are in `apps/web/public/hq/models/`; `NOTICE.txt` next to them carries the same record. Total shipped: 700 624 bytes (a unit test caps it at 800 000). A scene fetches only what it needs: the ground floor of the test building loads four files, 534 052 bytes.

| File | What | Source | Licence | Bytes |
|---|---|---|---|---|
| `sofa.glb` | navy velvet sofa, 2.5k triangles | glTF-Sample-Assets, GlamVelvetSofa, Wayfair, LLC | CC BY 4.0 (attribution in NOTICE) | 100 376 |
| `armchair.glb` | velvet lounge chair, 6k triangles, fabric recoloured teal grey | glTF-Sample-Assets, SheenChair, Wayfair, LLC | CC BY 4.0 (attribution in NOTICE) | 321 764 |
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

Setup: 1440x900, quality pinned to 3 (2048 px shadow map, night bloom), pixel ratio 1, headless Chromium with SwiftShader, the
test fixture (6 desks, 9 rooms, 2 floors, 6 campus buildings). `tris` is what the renderer reports for the frame, shadow pass included.

| View | Triangles | Draw calls | JS draw time | Frame interval under SwiftShader |
|---|---|---|---|---|
| Campus, day | 40 696 | 49 | 3.4 ms | about 1.4 s |
| Campus, night (glow on) | 40 710 | 63 | 4.8 ms | about 2.3 s |
| Building ground floor, day | 61 341 (121 756 on the first frame) | 25 | 3.7 ms | about 2.2 s |
| Building ground floor, night (glow on) | 121 770 | 57 | 4.3 ms | about 3.0 s |

Notes on reading the table:

- "JS draw time" is the time the page spends submitting a frame. The software rasteriser then spends one to three seconds actually
  shading it, which is why the frame interval is so long here: these frame intervals are not GPU numbers and say nothing about a
  real GPU. The page was not measured on a GPU in this work.
- Models: ground floor 11 placements (2 people, 9 sofas and armchairs) from 4 files, 534 052 bytes, loaded in 0.8 to 1.5 s from the
  local preview server (no network latency). A floor that shows another person look loads one more person file (about 55 kB). Loading is lazy and starts only when a scene asks for a model.
- Added draw calls from this change: about 6 for the models (one instanced mesh per part and look) and one for the contact shadows;
  the halo layer is one instanced draw; the rest of the difference between day and night is the bloom passes.
- Bundle: the 3D chunk (`hqThreeRenderer`) grew from about 546 kB (136 kB gzip) to 677 kB (172 kB gzip) because it now carries
  `GLTFLoader` and the meshopt decoder. It still loads only when the 3D stage mounts. The model files are separate and fetched on demand.
- The quality ladder is unchanged (3 to 0, then the pixel ratio). Models, halos and contact shadows are cheap enough to stay on at
  every level; the glass Fresnel and sky reflection are skipped on the software rasteriser path (Lambert materials), like before.


## Still different from the concept renders

Compared screen by screen with the concept renders (shots in the session scratchpad folder `hq-shots-realism`, twelve files:
campus, building, reception and executive office, day and night at 1440x900, campus and building at 390x844):

1. **Interior life and density.** The concept floors hold 20 to 30 desks and a person at almost every one. The fixture and any real
   workspace hold only what the registry says; with six desks the building looks empty next to the mock. This is by design and cannot be
   closed by rendering.
2. **People.** The seated people are stylised toy characters (large heads, simple faces), not realistic office staff. They sit, in one
   pose, at desks that are assigned. There are no standing, walking or talking people (and by the truth rules there never are unless a
   record backs them). Realistic characters need models we are allowed to ship.
3. **Furniture.** Only sofas and lounge chairs are real models. Desks, task chairs, monitors, shelving, plants and partitions are still
   low poly boxes in flat colours; the mocks show wood grain, marble, fabric and brushed metal.
4. **Materials and lighting.** No baked global illumination, ambient occlusion or interior reflections in glass. At night the rooms are lit
   by emissive surfaces and halos only, so the warm pools of light and the dark, rich contrast of the mock are only partly there. Floors are
   flat colour with joint lines, not stone or timber.
5. **Campus massing.** The mock campus is low rise stone and glass round a courtyard and a big fountain with dense tropical planting.
   Ours is a ring of low blocks round three tall towers; facades now have fins, ledges and lighter glass, but there are no courtyards,
   balconies, curved glass, planted terraces or real trees. The planting is low poly.
6. **Camera.** The default angle and framing are closer to the mock, but the eye level lobby views (a person walking through the
   reception) are not built; the camera is the existing orbit with keyboard and reduced motion support, with no new cinematic moves.
7. **UI chrome.** The mock overlays a building directory and a detail panel inside the canvas; our page keeps its own panels and labels
   (style only was to be taken from the mocks).
8. **Mood.** The mocks are dusk or night scenes; day is the default here and looks brighter and flatter.


## Owner decisions needed

1. **Density.** The scene is as sparse as the registry. Decide whether a clearly labelled concept fixture with many desks may be shown
   (the Concept preview toggle already exists and stays separate), or whether furniture only (plants, shelves, rugs in every room, no
   people) should be made denser. Nothing invents a person or a desk in the real view either way.
2. **Characters.** Keep the stylised CC0 characters, or supply realistic rigged corporate characters (glTF, a sit clip, a licence that lets
   us ship them), or approve buying a licensed pack. Realistic people cannot be made from the assets reachable here.
3. **Licences.** The two Wayfair models are CC BY 4.0: approve the attribution in `NOTICE.txt` and add a credits line in the app, or replace
   them. The Kenney people came through an npm mirror; check the CC0 terms on kenney.nl before a public release.
4. **Furniture and textures.** Supply office desk, task chair, shelving, plant and stone or timber texture files (CC0 or licensed), or
   ask for an allowlist entry for `polyhaven.com`, `dl.polyhaven.org`, `ambientcg.com`, `quaternius.com` and `kenney.nl` so they can be
   fetched, or approve a further round of procedural improvement.
5. **Campus layout.** The concept campus is low rise. Approve changing the campus layout rules (tower heights, courtyard, ring spacing)
   if the three tall towers should go.
6. **Default mood.** Approve night (or dusk) as the first view, as in the mocks.

