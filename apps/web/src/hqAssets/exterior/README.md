# Exterior building assets

Pure builders for the headquarters campus. One builder per building type, same mesh format as
`lib/campusScene.ts` (`{ vertices, bounds, triangles }`, 10 floats per vertex), same conventions as
`lib/hqProps.ts` (emissive weight 2 or more marks a window pane, flat normals, no data in geometry).

```ts
import { buildExterior } from './hqAssets/exterior/index.ts';
const a = buildExterior('officeTower', { seed: workspaceId, width: 22, levels: 18, origin: { x, z }, rotation: 0 });
// a.mesh        -> render item (id, vertices, bounds), a.triangles measured
// a.sign        -> pin the HTML entrance sign (real building name) here; a.sign.nx/nz is the board normal
// a.door        -> end of the walkway; a.anchor -> label card position; a.box -> ground box for culling
```

Builders: `buildWarehouse`, `buildTyreWorkshop`, `buildOfficeTower`, `buildAdminBlock`,
`buildRetailBlock`, `buildExecutiveHq` (all `(params?: ExteriorParams) => ExteriorAsset`), plus
`EXTERIOR_BUILDERS`, `buildExterior(kind, params)`, `TRIANGLE_CAPS` and `WINDOW_RULES`.

Frame: origin is the middle of the footprint on the ground, the entrance faces +z. `rotation`
(quarter turns) and `origin` are applied at the end to vertices, normals, bounds and anchors.
The mesh holds decoration only. Signs are blank dark boards with the ANUM mark; the name is HTML.
