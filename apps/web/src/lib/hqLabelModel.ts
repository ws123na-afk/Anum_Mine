/**
 * What the building labels say and where they hang (docs/ANUM_MINE_HQ_LABELS.md). Pure functions: the room card text comes from
 * the registry (kind, department, desk count) and nothing else, anchors sit at room centres at the right floor height, and a
 * label exists only for the floors the view shows. The stacked view hangs labels inside each floor (below the slab above) and
 * lets the depth test hide the ones a slab covers, plus one tag per floor on the edge nearest the eye.
 */
import type { Aabb } from './hqMath.ts';
import type { Vec3, RoomGeometry, SceneFloor } from './headquartersProjection.ts';
import type { Density } from './hqLabels.ts';
import { CATALOGS, t, type MessageKey, type Vars } from './i18n.ts';

export const FLOOR_PITCH_M = 4.2;
export const SLAB_M = 0.3;
/** The height a room label hangs at inside a stacked floor (above the desks, below the slab above). */
export const STACKED_LABEL_Y = 1.9;
const TOP_LABEL_LIFT = 0.5;

export type Translate = (key: MessageKey, vars?: Vars) => string;

/** The department a room carries, in words, only when the catalogue knows the key (never a guessed or raw machine name). */
export function departmentText(department: string | null | undefined, tr: Translate = t): string | null {
  if (!department) return null;
  const key = `team.dept.${department}` as MessageKey;
  return key in CATALOGS.en ? tr(key) : null;
}

/** The one line under a room name: its kind, its desks, its department when set (the longest part goes last, so a narrow card cuts it first). Facts from the registry only. */
export function roomSubtitle(room: Pick<RoomGeometry, 'kind' | 'teamDepartment'>, deskCount: number, tr: Translate = t): string {
  const parts = [tr(`hq.roomKind.${room.kind}` as MessageKey)];
  if (deskCount > 0) parts.push(tr('hqLabels.desks', { count: deskCount }));
  const dept = departmentText(room.teamDepartment, tr);
  if (dept) parts.push(dept);
  return parts.join(' · ');
}

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

export type LabelMode = 'single' | 'stacked';

export type RoomLabelSpec = Readonly<{
  id: string; kind: 'room'; roomId: string; floor: number; anchor: Vec3; priority: number; tiers: readonly Density[]; occluders: readonly Aabb[]; maxDistance: number;
}>;
export type FloorTagSpec = Readonly<{
  id: string; kind: 'floor'; floor: number; floorId: string; anchor: Vec3; alternatives: readonly Vec3[]; priority: number; tiers: readonly Density[]; maxDistance: number;
}>;

/** Anchor of a room label: above the room on the shown floor, inside the room (under the slab above) in the stacked view. */
export function roomAnchor(room: Pick<RoomGeometry, 'x' | 'z' | 'width' | 'depth'>, floorIndex: number, mode: LabelMode, wallHeight: number, pitch = FLOOR_PITCH_M): Vec3 {
  const y = floorIndex * pitch;
  return { x: room.x + room.width / 2, y: y + (mode === 'stacked' ? STACKED_LABEL_Y : wallHeight + TOP_LABEL_LIFT), z: room.z + room.depth * (mode === 'stacked' ? 0.5 : 0.45) };
}

/**
 * The room labels of a view. Single floor: only the active floor's rooms (every other floor is cut away, so it has no labels).
 * Stacked: every floor's rooms, each hung inside its floor and hidden by the depth test while a slab above stands in the way.
 */
export function roomLabelSpecs(args: Readonly<{
  floors: readonly Pick<SceneFloor, 'id' | 'rooms'>[]; activeIndex: number; mode: LabelMode; selectedRoomId: string | null; wallHeight: number; pitch?: number;
}>): RoomLabelSpec[] {
  const pitch = args.pitch ?? FLOOR_PITCH_M;
  const above = slabsAbove(args.floors, pitch);
  const out: RoomLabelSpec[] = [];
  args.floors.forEach((floor, fi) => {
    if (args.mode === 'single' && fi !== args.activeIndex) return;
    for (const room of floor.rooms) {
      const selected = room.id === args.selectedRoomId;
      out.push({
        id: `room:${room.id}`, kind: 'room', roomId: room.id, floor: fi,
        anchor: roomAnchor(room, fi, args.mode, args.wallHeight, pitch), priority: selected ? 0 : 1,
        tiers: selected ? ['full'] : args.mode === 'stacked' ? ['compact', 'dot'] : ['full', 'compact', 'dot'],
        occluders: args.mode === 'stacked' ? above[fi] : [], maxDistance: 160,
      });
    }
  });
  return out;
}

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

/** Whether the labels of a floor are drawn at all in a mode: the stacked view shows all floors, the single view only the active one. */
export function floorShowsLabels(mode: LabelMode, floorIndex: number, activeIndex: number): boolean {
  return mode === 'stacked' || floorIndex === activeIndex;
}

/** A floor name split at its first colon: "Ground floor: E-commerce, Retail" is the short name "Ground floor" and the rest. Only the stored name is used. */
export function floorNameParts(name: string): { short: string; rest: string | null } {
  const i = name.indexOf(':');
  if (i <= 0) return { short: name.trim(), rest: null };
  const rest = name.slice(i + 1).trim();
  return { short: name.slice(0, i).trim(), rest: rest || null };
}
