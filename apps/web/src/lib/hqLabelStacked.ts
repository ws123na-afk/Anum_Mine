/**
 * The stacked view's extras (docs/ANUM_MINE_HQ_LABELS.md): the slab of each floor as a solid box, the slabs over a floor, and one tag per
 * floor on its edge nearest the eye. A separate module, loaded when the stacked view is first asked for.
 */
import type { Aabb } from './hqMath.ts';
import type { Vec3, SceneFloor } from './headquartersProjection.ts';
import type { Density } from './hqLabels.ts';
import { FLOOR_PITCH_M, SLAB_M } from './hqLabelModel.ts';

export type RoomRect = Readonly<{ x0: number; x1: number; z0: number; z1: number }>;

/** The ground extent of a floor: its rooms' rectangles. Null for an empty floor. */
export function floorExtent(floor: Pick<SceneFloor, 'rooms'>): RoomRect | null {
  if (!floor.rooms.length) return null;
  let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
  for (const r of floor.rooms) { x0 = Math.min(x0, r.x); x1 = Math.max(x1, r.x + r.width); z0 = Math.min(z0, r.z); z1 = Math.max(z1, r.z + r.depth); }
  return { x0, x1, z0, z1 };
}

/** The slab under floor `index` as a solid box, a little wider than its rooms (the floor plate overhangs). */
export function slabBox(extent: RoomRect, index: number, pitch = FLOOR_PITCH_M): Aabb {
  const y = index * pitch;
  return { min: { x: extent.x0 - 0.6, y: y - SLAB_M, z: extent.z0 - 0.6 }, max: { x: extent.x1 + 0.6, y: y + 0.02, z: extent.z1 + 0.6 } };
}

/** For each floor, the slabs of the floors above it: what can stand between the eye and a label hung inside that floor. */
export function slabsAbove(floors: readonly Pick<SceneFloor, 'rooms'>[], pitch = FLOOR_PITCH_M): Aabb[][] {
  const slabs = floors.map((f, i) => { const e = floorExtent(f); return e ? slabBox(e, i, pitch) : null; });
  return floors.map((_, i) => slabs.slice(i + 1).filter((b): b is Aabb => b !== null));
}


export type FloorTagSpec = Readonly<{
  id: string; kind: 'floor'; floor: number; floorId: string; anchor: Vec3; alternatives: readonly Vec3[]; priority: number; tiers: readonly Density[]; maxDistance: number;
}>;


/** One tag per floor for the stacked view: it sits on the floor's edge nearest the eye, so no slab can cover it. */
export function floorTagSpecs(args: Readonly<{ floors: readonly Pick<SceneFloor, 'id' | 'rooms'>[]; activeIndex: number; pitch?: number }>): FloorTagSpec[] {
  const pitch = args.pitch ?? FLOOR_PITCH_M;
  const out: FloorTagSpec[] = [];
  args.floors.forEach((floor, fi) => {
    const e = floorExtent(floor);
    if (!e) return;
    const y = fi * pitch + 1.1, cx = (e.x0 + e.x1) / 2, cz = (e.z0 + e.z1) / 2, out1 = 1.6;
    const sides: Vec3[] = [{ x: cx, y, z: e.z0 - out1 }, { x: cx, y, z: e.z1 + out1 }, { x: e.x0 - out1, y, z: cz }, { x: e.x1 + out1, y, z: cz }];
    out.push({ id: `floor:${floor.id}`, kind: 'floor', floor: fi, floorId: floor.id, anchor: sides[0], alternatives: sides.slice(1), priority: fi === args.activeIndex ? 0 : 1, tiers: ['full', 'compact'], maxDistance: 200 });
  });
  return out;
}

