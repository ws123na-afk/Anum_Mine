/**
 * The real floor plans of a registry building as the campus needs them: one storey per above-ground floor, each with its own
 * footprint (rooms, margin and the shared service core, exactly the slab the interior pipeline draws, `buildingLayout`), the
 * frame that maps plan metres to world metres, and the stable keys that say when a plan changed.
 *
 * Data truth (docs/ANUM_MINE_HQ_GLASS_PLAN.md): everything here is read from registry records (rooms, desks, assignments) and
 * presence. A desk is "lit" only when a presence record says its agent is working or in a meeting. Nothing is invented.
 * Pure: no WebGL, no DOM. Light enough to live in the main headquarters chunk; the heavy parts (shell, atlas) are in `hqPlanKit.ts`.
 */
import type { SceneBuilding, SceneFloor } from './headquartersProjection.ts';
import { FLOOR_PITCH, buildingLayout } from './buildingScene.ts';
import { presenceForDesk, type HqPresenceIndex } from './hqPresence.ts';

/** What the glass impostor of one building is made from. `lit` and `seated` hold desk ids. */
export type PlanInput = Readonly<{
  floors: readonly SceneFloor[];
  /** Desks whose agent is working or in a meeting right now (presence says so). */
  lit: ReadonlySet<string>;
  /** Desks that have an agent assigned (a registry fact). */
  seated: ReadonlySet<string>;
}>;

/** Height of the plinth: floor 0 of the shell stands this high; floor n is at `PLINTH + n * STOREY_PITCH`. */
export const PLINTH = 0.9;
/** The one storey pitch of the stack (the interior's `FLOOR_PITCH`). */
export const STOREY_PITCH = FLOOR_PITCH;
/** Smallest footprint a planned building is drawn with (metres), so a one-room building is still a building. */
export const MIN_SHELL = Object.freeze({ w: 12, d: 9 });

export type PlanRect = Readonly<{ x0: number; z0: number; x1: number; z1: number }>;
export type PlanStorey = Readonly<{
  /** Index of the floor in the registry order (what the interior stacks by). */
  floorIndex: number;
  /** Storey number above the ground, 0 first. */
  n: number;
  rect: PlanRect;
  /** The service core of this storey (plan metres, minimum corner), or null on a floor without one. */
  core: Readonly<{ x: number; z: number }> | null;
}>;

export type PlanFrame = Readonly<{
  /** Union of the storey footprints, in plan metres. */
  rect: PlanRect;
  /** Middle of `rect`: the world position of the building placement. */
  cx: number; cz: number;
  w: number; d: number;
  storeys: readonly PlanStorey[];
  /** Roof height of the stack (top of the last storey) in metres above the ground. */
  roof: number;
}>;

/** True when the registry building has at least one room: a building without rooms keeps the generic look. */
export function hasPlan(floors: readonly SceneFloor[]): boolean {
  return floors.some((f) => f.rooms.length > 0);
}

/** The storeys above ground (level 0 and up) of a registry building, each with its own footprint. Null without rooms. */
export function planFrame(floors: readonly SceneFloor[]): PlanFrame | null {
  if (!hasPlan(floors)) return null;
  const layout = buildingLayout(floors);
  const above: number[] = [];
  floors.forEach((f, i) => { if (f.level >= 0) above.push(i); });
  // A building whose floors are all below ground (or numbered oddly) draws every floor rather than nothing.
  const order = above.length ? above : floors.map((_, i) => i);
  const storeys: PlanStorey[] = order.map((floorIndex, n) => {
    const b = layout.boxes[floorIndex];
    return { floorIndex, n, rect: { x0: b.x0, z0: b.z0, x1: b.x1, z1: b.z1 }, core: b.core };
  });
  let x0 = Math.min(...storeys.map((s) => s.rect.x0)), z0 = Math.min(...storeys.map((s) => s.rect.z0));
  let x1 = Math.max(...storeys.map((s) => s.rect.x1)), z1 = Math.max(...storeys.map((s) => s.rect.z1));
  // A tiny plan is widened evenly round its middle so the shell stays a plausible building.
  const grow = (lo: number, hi: number, min: number): [number, number] => (hi - lo >= min ? [lo, hi] : [(lo + hi) / 2 - min / 2, (lo + hi) / 2 + min / 2]);
  [x0, x1] = grow(x0, x1, MIN_SHELL.w);
  [z0, z1] = grow(z0, z1, MIN_SHELL.d);
  return { rect: { x0, z0, x1, z1 }, cx: (x0 + x1) / 2, cz: (z0 + z1) / 2, w: x1 - x0, d: z1 - z0, storeys, roof: PLINTH + storeys.length * STOREY_PITCH };
}

const fixed = (n: number): string => (Math.round(n * 100) / 100).toString();

/**
 * The layout revision of a building as the glass sees it: every room (id, kind, box, door side) and desk (id, room, position) on
 * every floor, in order. It changes exactly when `office.layout_changed` led to a different plan, and never for presence.
 */
export function layoutKey(floors: readonly SceneFloor[]): string {
  const parts: string[] = [];
  for (const f of floors) {
    parts.push(`f${f.id}:${f.level}`);
    for (const r of f.rooms) parts.push(`${r.id}:${r.kind}:${fixed(r.x)},${fixed(r.z)},${fixed(r.width)},${fixed(r.depth)}:${r.doorSide ?? '-'}`);
    for (const d of f.desks) parts.push(`${d.id}@${d.roomId}:${fixed(d.position.x)},${fixed(d.position.z)}`);
  }
  return hash32(parts.join('|'));
}

/** Who sits where and who is lit: changes with presence and assignments, not with the layout. */
export function occupancyKey(input: Pick<PlanInput, 'lit' | 'seated'>): string {
  return hash32(`${[...input.lit].sort().join(',')}#${[...input.seated].sort().join(',')}`);
}

/** FNV-1a, 32 bits, as 8 hex digits: a stable short revision label (not a security hash). */
export function hash32(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  return h.toString(16).padStart(8, '0');
}

/** The statuses that light a desk: only these say somebody is at work. Vacant, idle, unknown and the rest stay dim. */
export const LIT_STATUSES: ReadonlySet<string> = new Set(['working', 'meeting']);

/** The plan input of one registry building from the presence index (null presence: nothing is lit). */
export function planInputOf(building: SceneBuilding, presence: HqPresenceIndex | null): PlanInput | null {
  if (!hasPlan(building.floors)) return null;
  const lit = new Set<string>(), seated = new Set<string>();
  for (const f of building.floors) {
    for (const d of f.desks) {
      const record = presenceForDesk(presence, d);
      if (d.agentId || record) seated.add(d.id);
      if (record && !record.stale && LIT_STATUSES.has(record.status)) lit.add(d.id);
    }
  }
  return { floors: building.floors, lit, seated };
}

/** The footprint a planned building is placed with (metres) and how many storeys it has above ground. */
export function planSize(floors: readonly SceneFloor[]): Readonly<{ w: number; d: number; levels: number; height: number }> | null {
  const frame = planFrame(floors);
  return frame ? { w: frame.w, d: frame.d, levels: frame.storeys.length, height: frame.roof } : null;
}
