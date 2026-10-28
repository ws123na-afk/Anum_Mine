/**
 * The plan kit: everything the campus needs to draw buildings from their real floor plans, loaded on demand
 * (`import('./hqPlanKit.ts')`) so the headquarters entry chunk does not carry it. It gives the campus scene a drawer for the
 * planned buildings (shell from the floor plans, panes tagged with their atlas row) and builds the atlas the glass shader reads.
 */
import type { Archetype } from './campusBuildings.ts';
import { EXTERIOR_OF, shapeOf, type ExteriorShape } from './campusExteriors.ts';
import type { Placement } from './campusModel.ts';
import { buildPlanShell, tagPaneUv } from '../hqAssets/exterior/planShell.ts';
import { buildCareerCentre } from '../hqAssets/exterior/careerCentre.ts';
import { buildGlassPlan, MAX_PLAN_BUILDINGS, type GlassPlanData, type PlanEntry } from './hqPlanAtlas.ts';
import { planFrame, type PlanInput } from './hqPlanFrame.ts';

export type { GlassPlanData } from './hqPlanAtlas.ts';
export * from './hqPlanAtlas.ts';

/** Atlas rows of the planned buildings, in placement order (at most 16; the rest keep the generic look). */
export function plannedRows(placements: readonly Placement[], plans: ReadonlyMap<string, PlanInput>): ReadonlyMap<string, number> {
  const rows = new Map<string, number>();
  for (const p of placements) if (plans.has(p.workspaceId) && rows.size < MAX_PLAN_BUILDINGS && planFrame(plans.get(p.workspaceId)!.floors)) rows.set(p.workspaceId, rows.size);
  return rows;
}

/** The drawer for `buildCampusScene`: the shell of a planned building from its plans; undefined for any other building. */
export function plannedDrawer(rows: ReadonlyMap<string, number>, plans: ReadonlyMap<string, PlanInput>) {
  return (p: Placement, archetype: Archetype, signIndex: number, signCount: number): ExteriorShape | undefined => {
    const row = rows.get(p.workspaceId);
    const input = plans.get(p.workspaceId);
    const frame = input ? planFrame(input.floors) : null;
    if (row === undefined || !frame) return undefined;
    const asset = buildPlanShell(EXTERIOR_OF[archetype], frame, { seed: 'bld:' + p.workspaceId, origin: { x: p.x, z: p.z }, rotation: p.turn, row });
    tagPaneUv(asset.mesh.vertices, row, frame.roof);
    return shapeOf(asset, signIndex, signCount);
  };
}

/**
 * The career centre drawer (owner decision O2, docs/ANUM_MINE_HQ_RECEPTION.md): it rides in this lazy kit so the entry chunk does
 * not carry the asset and no new lazy importer splits the shared chunks differently. A career building without a floor plan
 * gets the career centre; every other building returns undefined and keeps its own look.
 */
export function careerDrawer() {
  return (p: Placement, archetype: Archetype, signIndex: number, signCount: number): ExteriorShape | undefined => {
    if (archetype !== 'career') return undefined;
    const asset = buildCareerCentre({ seed: 'bld:' + p.workspaceId, width: p.width, depth: p.depth, levels: p.levels, origin: { x: p.x, z: p.z }, rotation: p.turn });
    return shapeOf(asset, signIndex, signCount);
  };
}

/** The plan atlas of the planned buildings, or null when none is planned. */
export function planAtlas(placements: readonly Placement[], plans: ReadonlyMap<string, PlanInput>, rows: ReadonlyMap<string, number>): GlassPlanData | null {
  const entries: PlanEntry[] = [];
  for (const p of placements) {
    const row = rows.get(p.workspaceId);
    const input = plans.get(p.workspaceId);
    const frame = input ? planFrame(input.floors) : null;
    if (row === undefined || !input || !frame) continue;
    entries[row] = { origin: { x: p.x, z: p.z }, turn: p.turn, frame, input };
  }
  return entries.length ? buildGlassPlan(entries) : null;
}
