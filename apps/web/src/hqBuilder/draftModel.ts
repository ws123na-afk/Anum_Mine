// The builder's local DRAFT: staged room and desk placement kept in memory until the owner saves it
// as one atomic PUT /floors/{id}/layout. Nothing here is stored in the browser: closing the page
// loses an unsaved draft on purpose (the server is the only place a layout lives).
// Pure, so the unit tests cover undo, redo, the diff to the payload and the reload-and-reapply step.
import type { OfficeDoorSide, OfficeFloorLayoutRequest, OfficeLayoutDesk, OfficeLayoutRoom, OfficeRotation } from '@anum/contracts';
import type { OfficeDesk, OfficeRoom } from '../lib/officeSpaceClient.ts';
import { nextDoorSide, nextRotation, rotateDeskPoint, rotateRect90, round3, sameRect, type Bounds, type Rect } from './geometry.ts';

export interface DraftRoom {
  id: string;
  /** False while the server has no placement for the room (it waits in the tray). */
  placed: boolean;
  x: number;
  z: number;
  width: number;
  depth: number;
  rotation: OfficeRotation;
  doorSide: OfficeDoorSide;
}
export interface DraftDesk { id: string; roomId: string; posX: number | null; posZ: number | null }
export interface Snapshot { rooms: Record<string, DraftRoom>; desks: Record<string, DraftDesk> }
/** Row revisions live beside the snapshots, so an undo never brings back an old revision. */
export type Revisions = Record<string, number>;

const num = (value: unknown, fallback: number): number => {
  const parsed = typeof value === 'string' ? Number(value) : value;
  return typeof parsed === 'number' && Number.isFinite(parsed) ? parsed : fallback;
};

export function roomFromRecord(room: OfficeRoom): DraftRoom {
  const placed = room.scene_x != null && room.scene_z != null && room.scene_width != null && room.scene_depth != null;
  return {
    id: room.id,
    placed,
    x: placed ? num(room.scene_x, 0) : 0,
    z: placed ? num(room.scene_z, 0) : 0,
    width: placed ? num(room.scene_width, 6) : 6,
    depth: placed ? num(room.scene_depth, 5) : 5,
    rotation: ([0, 90, 180, 270] as const).includes(room.rotation as OfficeRotation) ? (room.rotation as OfficeRotation) : 0,
    doorSide: (['north', 'south', 'east', 'west'] as const).includes(room.door_side as OfficeDoorSide) ? (room.door_side as OfficeDoorSide) : 'south',
  };
}

export function deskFromRecord(desk: OfficeDesk): DraftDesk {
  const placed = desk.pos_x != null && desk.pos_z != null;
  return { id: desk.id, roomId: desk.room_id, posX: placed ? num(desk.pos_x, 0) : null, posZ: placed ? num(desk.pos_z, 0) : null };
}

export function snapshotFromRecords(rooms: readonly OfficeRoom[], desks: readonly OfficeDesk[]): { snapshot: Snapshot; revisions: Revisions } {
  const snapshot: Snapshot = { rooms: {}, desks: {} };
  const revisions: Revisions = {};
  for (const room of rooms) { snapshot.rooms[room.id] = roomFromRecord(room); revisions[room.id] = room.revision; }
  for (const desk of desks) { snapshot.desks[desk.id] = deskFromRecord(desk); revisions[desk.id] = desk.revision; }
  return { snapshot, revisions };
}

export const rectOf = (room: DraftRoom): Rect => ({ id: room.id, x: room.x, z: room.z, width: room.width, depth: room.depth });

// ---- Edits (each returns a new snapshot and shares every untouched entry) ----

export function withRoom(s: Snapshot, id: string, patch: Partial<DraftRoom>): Snapshot {
  const room = s.rooms[id];
  if (!room) return s;
  return { ...s, rooms: { ...s.rooms, [id]: { ...room, ...patch } } };
}

export function setRect(s: Snapshot, id: string, rect: Pick<Rect, 'x' | 'z' | 'width' | 'depth'>): Snapshot {
  const room = s.rooms[id];
  if (!room || (room.placed && sameRect(room, rect))) return s;
  return withRoom(s, id, { placed: true, x: round3(rect.x), z: round3(rect.z), width: round3(rect.width), depth: round3(rect.depth) });
}

export function setDeskPosition(s: Snapshot, id: string, posX: number | null, posZ: number | null): Snapshot {
  const desk = s.desks[id];
  if (!desk || (desk.posX === posX && desk.posZ === posZ)) return s;
  return { ...s, desks: { ...s.desks, [id]: { ...desk, posX, posZ } } };
}

/**
 * A quarter turn clockwise in one step: the footprint swaps width and depth, the rotation and the
 * door turn with it, and the desks inside turn too, so nothing falls outside the room it was in.
 */
export function rotateRoom(s: Snapshot, id: string, bounds: Bounds | null): Snapshot {
  const room = s.rooms[id];
  if (!room) return s;
  const turned = rotateRect90(rectOf(room), bounds);
  let next = withRoom(s, id, {
    placed: true, x: turned.x, z: turned.z, width: turned.width, depth: turned.depth,
    rotation: nextRotation(room.rotation), doorSide: nextDoorSide(room.doorSide),
  });
  const desks = { ...next.desks };
  for (const desk of Object.values(s.desks)) {
    if (desk.roomId !== id || desk.posX == null || desk.posZ == null) continue;
    const point = rotateDeskPoint(desk.posX, desk.posZ, room.depth);
    desks[desk.id] = { ...desk, posX: point.x, posZ: point.z };
  }
  next = { ...next, desks };
  return next;
}

// ---- Adding and removing entries that the server created or deleted ----

export function withEntries(s: Snapshot, rooms: readonly DraftRoom[], desks: readonly DraftDesk[]): Snapshot {
  const next: Snapshot = { rooms: { ...s.rooms }, desks: { ...s.desks } };
  for (const room of rooms) next.rooms[room.id] = room;
  for (const desk of desks) next.desks[desk.id] = desk;
  return next;
}
export function withoutEntries(s: Snapshot, roomIds: readonly string[], deskIds: readonly string[]): Snapshot {
  const next: Snapshot = { rooms: { ...s.rooms }, desks: { ...s.desks } };
  for (const id of roomIds) delete next.rooms[id];
  for (const id of deskIds) delete next.desks[id];
  return next;
}

// ---- Diff to the payload ----

const sameRoom = (a: DraftRoom, b: DraftRoom): boolean => a.placed === b.placed && (!a.placed || sameRect(a, b)) && a.rotation === b.rotation && a.doorSide === b.doorSide;
const samePos = (a: DraftDesk, b: DraftDesk): boolean => a.posX === b.posX && a.posZ === b.posZ;

export function changedRoomIds(base: Snapshot, present: Snapshot): string[] {
  return Object.values(present.rooms).filter((room) => room.placed && (!base.rooms[room.id] || !sameRoom(base.rooms[room.id], room))).map((room) => room.id);
}
export function changedDeskIds(base: Snapshot, present: Snapshot): string[] {
  return Object.values(present.desks)
    .filter((desk) => desk.posX != null && desk.posZ != null && present.rooms[desk.roomId]?.placed && (!base.desks[desk.id] || !samePos(base.desks[desk.id], desk)))
    .map((desk) => desk.id);
}
export const isDirty = (base: Snapshot, present: Snapshot): boolean => changedRoomIds(base, present).length > 0 || changedDeskIds(base, present).length > 0;

/** The body of PUT /floors/{id}/layout: only what changed, each item with the revision it was loaded at. */
export function toLayoutRequest(base: Snapshot, present: Snapshot, revisions: Revisions): OfficeFloorLayoutRequest {
  const rooms: OfficeLayoutRoom[] = changedRoomIds(base, present).map((id) => {
    const room = present.rooms[id];
    return { id, expected_revision: revisions[id] ?? 1, x: room.x, z: room.z, width: room.width, depth: room.depth, rotation: room.rotation, door_side: room.doorSide };
  });
  const desks: OfficeLayoutDesk[] = changedDeskIds(base, present).map((id) => {
    const desk = present.desks[id];
    return { id, expected_revision: revisions[id] ?? 1, pos_x: desk.posX as number, pos_z: desk.posZ as number };
  });
  return { ...(rooms.length ? { rooms } : {}), ...(desks.length ? { desks } : {}) };
}

// ---- Undo and redo ----

export interface History { past: Snapshot[]; present: Snapshot; future: Snapshot[]; lastKey: string | null; lastAt: number }
export const HISTORY_LIMIT = 100;
const COALESCE_MS = 700;

export const startHistory = (present: Snapshot): History => ({ past: [], present, future: [], lastKey: null, lastAt: 0 });

/**
 * Records `next` as the new present. Steps with the same key close together (repeated arrow
 * presses on one room) share one undo step.
 */
export function commit(history: History, next: Snapshot, key: string | null = null, now = Date.now()): History {
  if (next === history.present) return history;
  if (key && key === history.lastKey && now - history.lastAt < COALESCE_MS && history.past.length) {
    return { ...history, present: next, future: [], lastAt: now };
  }
  const past = [...history.past, history.present];
  if (past.length > HISTORY_LIMIT) past.shift();
  return { past, present: next, future: [], lastKey: key, lastAt: now };
}
export const canUndo = (h: History): boolean => h.past.length > 0;
export const canRedo = (h: History): boolean => h.future.length > 0;
export function undo(h: History): History {
  if (!h.past.length) return h;
  const previous = h.past[h.past.length - 1];
  return { past: h.past.slice(0, -1), present: previous, future: [h.present, ...h.future], lastKey: null, lastAt: 0 };
}
export function redo(h: History): History {
  if (!h.future.length) return h;
  const [next, ...rest] = h.future;
  return { past: [...h.past, h.present], present: next, future: rest, lastKey: null, lastAt: 0 };
}
/** Apply the same entry change to every step, so undo never resurrects a deleted room or hides a new one. */
export function mapHistory(h: History, change: (s: Snapshot) => Snapshot): History {
  return { ...h, past: h.past.map(change), present: change(h.present), future: h.future.map(change) };
}

// ---- STALE_REVISION: reload, then reapply ----

export interface DroppedChange { kind: 'room' | 'desk'; id: string; reason: 'changed' | 'removed' }
export interface RebaseResult { present: Snapshot; kept: number; dropped: DroppedChange[] }

/**
 * After the latest records are loaded: keep every staged change whose item nobody else touched
 * (the server still has the version this draft started from); take the other person's version of
 * anything that moved meanwhile, and drop changes for anything that was deleted. The result is a
 * fresh draft on the new base that the owner reviews and saves again.
 */
export function rebase(oldBase: Snapshot, present: Snapshot, newBase: Snapshot): RebaseResult {
  const dropped: DroppedChange[] = [];
  let kept = 0;
  const rooms: Record<string, DraftRoom> = { ...newBase.rooms };
  for (const room of Object.values(present.rooms)) {
    const before = oldBase.rooms[room.id];
    const now = newBase.rooms[room.id];
    const mine = before ? !sameRoom(before, room) : room.placed;
    if (!mine) continue;
    if (!now) { dropped.push({ kind: 'room', id: room.id, reason: 'removed' }); continue; }
    if (sameRoom(now, room)) continue;
    if (before && sameRoom(before, now)) { rooms[room.id] = room; kept += 1; } else dropped.push({ kind: 'room', id: room.id, reason: 'changed' });
  }
  const desks: Record<string, DraftDesk> = { ...newBase.desks };
  for (const desk of Object.values(present.desks)) {
    const before = oldBase.desks[desk.id];
    const now = newBase.desks[desk.id];
    const mine = before ? !samePos(before, desk) : desk.posX != null;
    if (!mine) continue;
    if (!now || !rooms[now.roomId]) { dropped.push({ kind: 'desk', id: desk.id, reason: 'removed' }); continue; }
    if (samePos(now, desk)) continue;
    if (before && samePos(before, now)) { desks[desk.id] = desk; kept += 1; } else dropped.push({ kind: 'desk', id: desk.id, reason: 'changed' });
  }
  return { present: { rooms, desks }, kept, dropped };
}
