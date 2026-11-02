/**
 * How the renderer turns scene items into draw calls (docs/ANUM_MINE_HQ_GPU_CHECK.md, "Draw calls"). Pure: no three.js and no DOM, so the
 * unit runner counts the draw calls of a real campus without a GPU.
 *
 * Every item is cut by surface class (one material each), and the pieces of all items that draw the same way are merged: one mesh per
 * class per batch instead of one per class per building. Items that must stay apart for a reason the renderer acts on are in batches of
 * their own: a lift group (a cut-away floor that eases in and out), a model fallback (hidden once the model has loaded), the billboards
 * (their own materials per tier), items that are never culled, moving parts and transparent glass (sorted back to front one by one).
 * The hover and selection glow still knows each building: every vertex carries its item's pick number (`aPick`).
 */
import { MAT, VERTEX_FLOATS } from './hqGeometry.ts';
import type { RenderItem } from './hqRenderTypes.ts';

export type Cls = 'paint' | 'flat' | 'concrete' | 'tile' | 'asphalt' | 'grass' | 'wood' | 'metal' | 'water' | 'glass' | 'roller' | 'sign' | 'court' | 'pane' | 'foliage' | 'cloth' | 'beacon' | 'board' | 'boardFlat' | 'screen';

export const CLASS_OF_MAT: Readonly<Record<number, Cls>> = {
  [MAT.flat]: 'flat', [MAT.concrete]: 'concrete', [MAT.tile]: 'tile', [MAT.asphalt]: 'asphalt', [MAT.grass]: 'grass', [MAT.wood]: 'wood',
  [MAT.metal]: 'metal', [MAT.water]: 'water', [MAT.glass]: 'glass', [MAT.roller]: 'roller', [MAT.sign]: 'sign', [MAT.court]: 'court', [MAT.paint]: 'paint', [MAT.screen]: 'screen',
  [MAT.foliage]: 'foliage', [MAT.cloth]: 'cloth', [MAT.beacon]: 'beacon',
};

/** The surface class of triangle `t` (its first vertex): glass panes, emissive glass (emission 2 and up) or the material id. */
export function classOf(v: Float32Array, t: number, transparent: boolean, still: (cls: Cls) => Cls = (c) => c): Cls {
  const base = t * VERTEX_FLOATS;
  if (transparent) return 'pane';
  if (v[base + 9] >= 2) return 'glass';
  return still(CLASS_OF_MAT[v[base + 10]] ?? 'flat');
}

/** Items with the same key are merged. A key of null keeps the item alone (see the module note). */
export function batchKey(item: RenderItem, transparent = false): string | null {
  if (transparent) return null;
  return `${item.cull === false ? 'all' : 'cull'}|${item.board ? 'board' : ''}|${item.fallbackOf ?? ''}|${item.lift ? `${item.lift.group}@${item.lift.y}` : ''}`;
}

/** The items in batches, in the order their first item came (so the draw order of the scene stays as it was). */
export function batchItems<T extends RenderItem>(items: readonly T[], transparent = false): T[][] {
  const out: T[][] = [];
  const at = new Map<string, number>();
  for (const item of items) {
    const key = batchKey(item, transparent);
    if (key === null) { out.push([item]); continue; }
    const i = at.get(key);
    if (i === undefined) { at.set(key, out.length); out.push([item]); } else out[i].push(item);
  }
  return out;
}

/** The surface classes one batch draws, in first seen order: one mesh (one draw call) each. */
export function batchClasses(batch: readonly Readonly<{ vertices: Float32Array }>[], transparent = false, still?: (cls: Cls) => Cls): Cls[] {
  const seen = new Set<Cls>();
  for (const item of batch) {
    const total = item.vertices.length / VERTEX_FLOATS;
    for (let t = 0; t + 2 < total; t += 3) seen.add(classOf(item.vertices, t, transparent, still));
  }
  return [...seen];
}

/** Meshes (draw calls before culling) the renderer makes for these items: what the unit budget test counts. */
export function plannedDraws(items: readonly RenderItem[], transparent = false, still?: (cls: Cls) => Cls): number {
  return batchItems(items, transparent).reduce((n, batch) => n + batchClasses(batch, transparent, still).length, 0);
}

/** Pick numbers for the hover and selection glow: 0 is "no item", so a mesh without the attribute never lights up. */
export function pickNumbers(ids: Iterable<string>): Map<string, number> {
  const out = new Map<string, number>();
  for (const id of ids) if (!out.has(id)) out.set(id, out.size + 1);
  return out;
}
