/**
 * Where each ghost floor of the cut-away should stand (docs/ANUM_MINE_HQ_BUILDING_MODULES.md, "Floor transitions"). The
 * open floor is drawn in full by the building itself, so its ghost is at 0 at once. A floor below the open one (or every
 * floor with "All floors") is at 1; a floor above it is cut away at 0. The floor the person just left starts from 1, so it
 * eases away (or in) instead of vanishing. Pure: the unit runner covers it.
 */
import type { LiftSpec } from './hqRenderTypes.ts';

export function floorLifts(floorIndexes: readonly number[], active: number, showAll: boolean, left: number | null): Record<string, LiftSpec> {
  const out: Record<string, LiftSpec> = {};
  for (const fi of floorIndexes) {
    if (fi === active) { out[`floor:${fi}`] = { to: 0, from: 0 }; continue; }
    const to = showAll || fi < active ? 1 : 0;
    out[`floor:${fi}`] = fi === left ? { to, from: 1 } : { to };
  }
  return out;
}
