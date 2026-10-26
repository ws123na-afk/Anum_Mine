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

export type LabelMode = 'single' | 'stacked';

export type RoomLabelSpec = Readonly<{
  id: string; kind: 'room'; roomId: string; floor: number; anchor: Vec3; priority: number; tiers: readonly Density[]; occluders: readonly Aabb[]; maxDistance: number;
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
  /** The slabs over each floor (`slabsAbove` in `hqLabelStacked.ts`), needed in the stacked view only. */
  above?: readonly (readonly Aabb[])[];
}>): RoomLabelSpec[] {
  const pitch = args.pitch ?? FLOOR_PITCH_M;
  const out: RoomLabelSpec[] = [];
  args.floors.forEach((floor, fi) => {
    if (args.mode === 'single' && fi !== args.activeIndex) return;
    for (const room of floor.rooms) {
      const selected = room.id === args.selectedRoomId;
      out.push({
        id: `room:${room.id}`, kind: 'room', roomId: room.id, floor: fi,
        anchor: roomAnchor(room, fi, args.mode, args.wallHeight, pitch), priority: selected ? 0 : 1,
        tiers: selected ? ['full'] : args.mode === 'stacked' ? ['compact', 'dot'] : ['full', 'compact', 'dot'],
        occluders: args.mode === 'stacked' ? args.above?.[fi] ?? [] : [], maxDistance: 160,
      });
    }
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
