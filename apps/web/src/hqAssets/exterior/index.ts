/**
 * Exterior building assets for the headquarters campus: one pure builder per type.
 *
 *   const asset = buildExterior('warehouse', { seed: workspaceId, width: 40, origin: { x, z } });
 *   // asset.mesh = { vertices, bounds, triangles } in the format of `campusScene.ts` (SceneMesh)
 *
 * See `docs/ANUM_MINE_HQ_EXTERIOR_ASSETS.md`-style notes in the module comments of each builder.
 */
import { buildAdminBlock } from './adminBlock.ts';
import { buildExecutiveHq } from './executiveHq.ts';
import { buildOfficeTower } from './officeTower.ts';
import { buildPersonalVilla } from './personalVilla.ts';
import { buildRetailBlock } from './retailBlock.ts';
import { buildTyreWorkshop } from './tyreWorkshop.ts';
import { buildWarehouse } from './warehouse.ts';
import type { ExteriorAsset, ExteriorBuilder, ExteriorKind, ExteriorParams } from './types.ts';

export { buildAdminBlock, buildExecutiveHq, buildOfficeTower, buildPersonalVilla, buildRetailBlock, buildTyreWorkshop, buildWarehouse };
export { EXTERIOR_KINDS } from './types.ts';
export type { ExteriorAsset, ExteriorBuilder, ExteriorKind, ExteriorParams, ExteriorSchemeName, SceneMeshData, SignAnchor } from './types.ts';

export const EXTERIOR_BUILDERS: Readonly<Record<ExteriorKind, ExteriorBuilder>> = {
  warehouse: buildWarehouse,
  tyreWorkshop: buildTyreWorkshop,
  officeTower: buildOfficeTower,
  adminBlock: buildAdminBlock,
  retailBlock: buildRetailBlock,
  executiveHq: buildExecutiveHq,
  personalVilla: buildPersonalVilla,
};

/** Triangle caps per kind at default size and full detail (measured, with headroom; tests enforce them). */
export const TRIANGLE_CAPS: Readonly<Record<ExteriorKind, number>> = {
  warehouse: 1700, tyreWorkshop: 1500, officeTower: 3800, adminBlock: 2600, retailBlock: 1300, executiveHq: 5600, personalVilla: 2400,
};

/**
 * Night window rules (the shader reads the emissive weight, see `hqProps.ts`):
 * - a window pane has an emissive weight of WINDOW_BASE (2) or more;
 * - lit at night: WINDOW_BASE + 0.45 to 0.95 (warm 86 percent, cool white 14 percent);
 * - dark at night: exactly WINDOW_BASE (reflective glass by day, dark glass at night);
 * - the share of lit panes is `litWindows` (default per kind below), drawn from the seed;
 * - signs, canopy undersides, door glass and lamps use plain emissive weights below 2 (they glow at all times, more at night).
 */
export const WINDOW_RULES: Readonly<Record<ExteriorKind, number>> = {
  warehouse: 0.3, tyreWorkshop: 0.6, officeTower: 0.55, adminBlock: 0.5, retailBlock: 0.62, executiveHq: 0.58, personalVilla: 0.5,
};

export function buildExterior(kind: ExteriorKind, params: ExteriorParams = {}): ExteriorAsset {
  return EXTERIOR_BUILDERS[kind](params);
}
