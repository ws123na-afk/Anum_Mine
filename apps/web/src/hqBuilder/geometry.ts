// Pure plan geometry for the office builder. Units are metres; a floor's origin is its north west
// corner (x grows east, z grows south). The overlap and bounds rules mirror
// services/api/anum_api/office_layout.py, so the editor shows the same problems the server would
// refuse. No DOM here: `pnpm --filter @anum/web test:unit` exercises all of it.
import type { OfficeDoorSide, OfficeRotation } from '@anum/contracts';

export const EPS = 1e-6;
export const MIN_ROOM_SIDE_M = 1;
export const MAX_ROOM_SIDE_M = 1000;
export const DEFAULT_NEW_ROOM = { width: 6, depth: 5 } as const;

export interface Rect { id: string; x: number; z: number; width: number; depth: number }
export interface Bounds { width: number; depth: number }
export type Handle = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw';
export const HANDLES: readonly Handle[] = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];

/** Values are kept to millimetres so float noise never shows in a payload or a field. */
export function round3(value: number): number {
  return Math.round(value * 1000) / 1000;
}

/** Snap to a grid step (0 or less = no snapping). */
export function snap(value: number, step: number): number {
  if (!(step > 0)) return round3(value);
  return round3(Math.round(value / step) * step);
}

/** True when the interiors intersect; rooms that only share an edge do not overlap. */
export function overlaps(a: Rect, b: Rect): boolean {
  return Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x) > EPS
    && Math.min(a.z + a.depth, b.z + b.depth) - Math.max(a.z, b.z) > EPS;
}

export function withinBounds(rect: Rect, bounds: Bounds | null): boolean {
  if (!bounds) return true;
  return rect.x >= -EPS && rect.z >= -EPS && rect.x + rect.width <= bounds.width + EPS && rect.z + rect.depth <= bounds.depth + EPS;
}

/** Desk positions are metres from the room's north west corner and must lie inside the room. */
export function pointInRoom(room: Pick<Rect, 'width' | 'depth'>, posX: number, posZ: number): boolean {
  return posX >= -EPS && posZ >= -EPS && posX <= room.width + EPS && posZ <= room.depth + EPS;
}

export function sameRect(a: Pick<Rect, 'x' | 'z' | 'width' | 'depth'>, b: Pick<Rect, 'x' | 'z' | 'width' | 'depth'>): boolean {
  return Math.abs(a.x - b.x) < 5e-4 && Math.abs(a.z - b.z) < 5e-4 && Math.abs(a.width - b.width) < 5e-4 && Math.abs(a.depth - b.depth) < 5e-4;
}

/** Keeps a rect fully inside the bounds when it is small enough; larger rects are only pinned to the origin. */
export function clampInto(rect: Rect, bounds: Bounds | null): Rect {
  if (!bounds) return rect;
  const x = Math.max(0, Math.min(rect.x, bounds.width - rect.width));
  const z = Math.max(0, Math.min(rect.z, bounds.depth - rect.depth));
  return { ...rect, x: round3(Math.max(0, x)), z: round3(Math.max(0, z)) };
}

/** Move by a pointer or key delta; the new corner snaps to the grid. */
export function moveRect(rect: Rect, dx: number, dz: number, step: number): Rect {
  return { ...rect, x: snap(rect.x + dx, step), z: snap(rect.z + dz, step) };
}

/** Resize from one handle: the dragged edge snaps to the grid, the opposite edge stays put, minimum side one metre. */
export function resizeRect(rect: Rect, handle: Handle, dx: number, dz: number, step: number, minSide = MIN_ROOM_SIDE_M): Rect {
  let left = rect.x;
  let top = rect.z;
  let right = rect.x + rect.width;
  let bottom = rect.z + rect.depth;
  if (handle.includes('w')) left = Math.min(snap(left + dx, step), right - minSide);
  if (handle.includes('e')) right = Math.max(snap(right + dx, step), left + minSide);
  if (handle.includes('n')) top = Math.min(snap(top + dz, step), bottom - minSide);
  if (handle.includes('s')) bottom = Math.max(snap(bottom + dz, step), top + minSide);
  const width = Math.min(round3(right - left), MAX_ROOM_SIDE_M);
  const depth = Math.min(round3(bottom - top), MAX_ROOM_SIDE_M);
  return { ...rect, x: round3(left), z: round3(top), width, depth };
}

/** Grow or shrink one side by a key press; the north west corner stays put. */
export function resizeByKey(rect: Rect, key: 'ArrowLeft' | 'ArrowRight' | 'ArrowUp' | 'ArrowDown', step: number, minSide = MIN_ROOM_SIDE_M): Rect {
  const amount = step > 0 ? step : 0.1;
  const width = key === 'ArrowRight' ? rect.width + amount : key === 'ArrowLeft' ? rect.width - amount : rect.width;
  const depth = key === 'ArrowDown' ? rect.depth + amount : key === 'ArrowUp' ? rect.depth - amount : rect.depth;
  return {
    ...rect,
    width: round3(Math.min(MAX_ROOM_SIDE_M, Math.max(minSide, width))),
    depth: round3(Math.min(MAX_ROOM_SIDE_M, Math.max(minSide, depth))),
  };
}

/** The next quarter turn clockwise. */
export function nextRotation(rotation: OfficeRotation): OfficeRotation {
  return ((rotation + 90) % 360) as OfficeRotation;
}

const DOOR_CLOCKWISE: Record<OfficeDoorSide, OfficeDoorSide> = { north: 'east', east: 'south', south: 'west', west: 'north' };
export function nextDoorSide(side: OfficeDoorSide): OfficeDoorSide {
  return DOOR_CLOCKWISE[side];
}

/**
 * A quarter turn clockwise: width and depth swap (the server stores a footprint only), the centre
 * stays where it is, and the result is pulled back inside the floor when it can fit.
 */
export function rotateRect90(rect: Rect, bounds: Bounds | null): Rect {
  const cx = rect.x + rect.width / 2;
  const cz = rect.z + rect.depth / 2;
  const turned: Rect = { ...rect, width: rect.depth, depth: rect.width, x: round3(cx - rect.depth / 2), z: round3(cz - rect.width / 2) };
  return clampInto(turned, bounds);
}

/** Where a desk lands when its room turns a quarter clockwise (old room depth becomes the new width). */
export function rotateDeskPoint(posX: number, posZ: number, oldDepth: number): { x: number; z: number } {
  return { x: round3(oldDepth - posZ), z: round3(posX) };
}

/** Clamp a desk position into its room. */
export function clampDesk(room: Pick<Rect, 'width' | 'depth'>, posX: number, posZ: number, step = 0): { x: number; z: number } {
  const x = Math.max(0, Math.min(room.width, step > 0 ? snap(posX, step) : round3(posX)));
  const z = Math.max(0, Math.min(room.depth, step > 0 ? snap(posZ, step) : round3(posZ)));
  return { x: round3(x), z: round3(z) };
}

/** A free spot for a new room: beside the source, below it, then a coarse scan (the server's rule). */
export function findFreeSpot(placed: readonly Rect[], width: number, depth: number, bounds: Bounds | null, near?: Rect | null, searchLimit = 200): { x: number; z: number } | null {
  const limitW = bounds ? bounds.width : searchLimit;
  const limitD = bounds ? bounds.depth : searchLimit;
  const free = (x: number, z: number): boolean => {
    const candidate: Rect = { id: 'new', x, z, width, depth };
    if (x + width > limitW + EPS || z + depth > limitD + EPS) return false;
    return !placed.some((other) => overlaps(candidate, other));
  };
  if (near) {
    for (const [x, z] of [[near.x + near.width, near.z], [near.x, near.z + near.depth]] as const) {
      if (free(x, z)) return { x, z };
    }
  }
  const step = Math.max(1, Math.ceil(Math.max(limitW, limitD) / 100));
  for (let z = 0; z + depth <= limitD + EPS; z += step) {
    for (let x = 0; x + width <= limitW + EPS; x += step) {
      if (free(x, z)) return { x, z };
    }
  }
  return null;
}

/** First free grid cell for a desk inside a room, away from the walls; null when the room is full. */
export function freeDeskSpot(room: Pick<Rect, 'width' | 'depth'>, taken: readonly { x: number; z: number }[], spacing = 1.5): { x: number; z: number } | null {
  const margin = Math.min(0.75, Math.min(room.width, room.depth) / 2);
  for (let z = margin; z <= room.depth - margin + EPS; z += spacing) {
    for (let x = margin; x <= room.width - margin + EPS; x += spacing) {
      if (!taken.some((t) => Math.abs(t.x - x) < spacing / 2 && Math.abs(t.z - z) < spacing / 2)) return { x: round3(x), z: round3(z) };
    }
  }
  return null;
}

/** The smallest rectangle holding every rect (null for none). */
export function extent(rects: readonly Rect[]): Bounds | null {
  if (!rects.length) return null;
  return {
    width: Math.max(...rects.map((r) => r.x + r.width)),
    depth: Math.max(...rects.map((r) => r.z + r.depth)),
  };
}

/** Converts a pointer delta in pixels to metres. */
export function pxToMetres(px: number, pxPerMetre: number): number {
  return pxPerMetre > 0 ? px / pxPerMetre : 0;
}

/** The drawn area of a plan in metres: the floor outline, or the rooms plus room to grow when the floor has no bounds. */
export function planView(rects: readonly Rect[], bounds: Bounds | null): Bounds {
  const used = extent(rects);
  const width = Math.max(bounds?.width ?? 0, used?.width ?? 0, bounds ? 0 : 24);
  const depth = Math.max(bounds?.depth ?? 0, used?.depth ?? 0, bounds ? 0 : 16);
  return { width: width + (bounds ? 0 : 6), depth: depth + (bounds ? 0 : 6) };
}
