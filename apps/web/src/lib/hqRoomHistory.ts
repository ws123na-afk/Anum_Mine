/**
 * Room history slider: the pure rules that turn the audit records of one room into scrubber steps.
 *
 * Data truth (owner decision O8 is open, so no snapshots are stored):
 *  - A step is one stored audit record. Its room values are that record's own `after` snapshot.
 *  - The state before the oldest loaded record is that record's own `before` snapshot, nothing more.
 *  - When a record's `before` does not match the previous record's `after`, something changed without
 *    a record. The time between the two records is then "Cannot be reconstructed for this time".
 *  - Nothing is interpolated, no movement is drawn between steps, and presence is never rebuilt.
 */
import type { OfficeHistoryEvidence } from '@anum/contracts';

export type Snap = Record<string, unknown>;

/** The audited room fields (services/api/anum_api/office_space_api.py `_AUDIT_FIELDS`). */
export const ROOM_FIELDS = [
  'floor_id', 'name', 'kind', 'capacity', 'scene_x', 'scene_z', 'scene_width', 'scene_depth', 'rotation', 'door_side',
  'description', 'notes', 'color', 'name_ar', 'equipment', 'team_department',
] as const;
export const DESK_FIELDS = ['room_id', 'label', 'agent_id', 'pos_x', 'pos_z'] as const;

export interface FieldChange { field: string; before: unknown; after: unknown }
export interface DeskChange { deskId: string; before: Snap | null; after: Snap | null; fields: FieldChange[] }

export interface RoomChange {
  record: OfficeHistoryEvidence;
  at: number;
  kind: 'room' | 'desk' | 'layout' | 'copy_source';
  /** Room snapshots carried by the record; null when the record has none for this room. */
  roomBefore: Snap | null;
  roomAfter: Snap | null;
  hasRoomSnapshot: boolean;
  fields: FieldChange[];
  desks: DeskChange[];
}

const isSnap = (value: unknown): value is Snap => typeof value === 'object' && value !== null && !Array.isArray(value);
const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

export function diffFields(before: Snap | null, after: Snap | null, fields: readonly string[]): FieldChange[] {
  const out: FieldChange[] = [];
  for (const field of fields) {
    const b = before ? before[field] ?? null : null;
    const a = after ? after[field] ?? null : null;
    if (!same(b, a)) out.push({ field, before: b, after: a });
  }
  return out;
}

function deskChange(id: string, before: Snap | null, after: Snap | null): DeskChange {
  return { deskId: id, before, after, fields: diffFields(before, after, DESK_FIELDS) };
}

/** What one record changed for this room, or null when it does not concern the room. */
export function changeFor(record: OfficeHistoryEvidence, roomId: string): RoomChange | null {
  const meta = record.metadata;
  const before = isSnap(meta.before) ? meta.before : null;
  const after = isSnap(meta.after) ? meta.after : null;
  const at = Date.parse(record.timestamp);
  if (Number.isNaN(at)) return null;
  const base = { record, at };
  const action = record.action;
  if (!action.startsWith('office.')) return null;

  if (action === 'office.floor.layout') {
    const rooms = (side: unknown) => (isSnap(side) && Array.isArray(side.rooms) ? side.rooms.filter(isSnap) : []);
    const desks = (side: unknown) => (isSnap(side) && Array.isArray(side.desks) ? side.desks.filter(isSnap) : []);
    const rb = rooms(before).find((r) => r.id === roomId) ?? null;
    const ra = rooms(after).find((r) => r.id === roomId) ?? null;
    const ids = new Set<string>();
    for (const d of [...desks(before), ...desks(after)]) if (typeof d.id === 'string') ids.add(d.id);
    const deskChanges: DeskChange[] = [];
    for (const id of ids) {
      const db = desks(before).find((d) => d.id === id) ?? null;
      const da = desks(after).find((d) => d.id === id) ?? null;
      if (db?.room_id !== roomId && da?.room_id !== roomId) continue;
      const change = deskChange(id, db, da);
      if (change.fields.length) deskChanges.push(change);
    }
    const fields = diffFields(rb, ra, ROOM_FIELDS);
    if (!rb && !ra) return deskChanges.length ? { ...base, kind: 'layout', roomBefore: null, roomAfter: null, hasRoomSnapshot: false, fields: [], desks: deskChanges } : null;
    if (!fields.length && !deskChanges.length) return null;
    return { ...base, kind: 'layout', roomBefore: rb, roomAfter: ra, hasRoomSnapshot: true, fields, desks: deskChanges };
  }

  if (action.startsWith('office.desk.')) {
    if (before?.room_id !== roomId && after?.room_id !== roomId) return null;
    const id = record.target;
    return { ...base, kind: 'desk', roomBefore: null, roomAfter: null, hasRoomSnapshot: false, fields: [], desks: [deskChange(id, before, after)] };
  }

  if (action.startsWith('office.room.')) {
    if (record.target === roomId) {
      return { ...base, kind: 'room', roomBefore: before, roomAfter: after, hasRoomSnapshot: true, fields: diffFields(before, after, ROOM_FIELDS), desks: [] };
    }
    if (action === 'office.room.copy' && after?.copied_from === roomId) {
      return { ...base, kind: 'copy_source', roomBefore: null, roomAfter: null, hasRoomSnapshot: false, fields: [], desks: [] };
    }
  }
  return null;
}

export type Step =
  | { kind: 'baseline'; index: number; /** The room as the oldest loaded record found it; null when that record created it. */ state: Snap | null; created: boolean; olderUnloaded: boolean; at: number }
  | { kind: 'change'; index: number; change: RoomChange; /** The room's values after this record, null while no record carries them. */ state: Snap | null; deleted: boolean }
  | { kind: 'gap'; index: number; from: number; to: number; fields: string[] };

export interface RoomTimeline {
  roomId: string;
  steps: Step[];
  changes: RoomChange[];
  /** True when older records exist that were not loaded, so earlier changes may be missing. */
  olderUnloaded: boolean;
  /** The room was created by a loaded record. */
  createdAt: number | null;
}

/** Builds the scrubber steps, oldest first. `records` may be in any order. */
export function buildRoomTimeline(records: readonly OfficeHistoryEvidence[], roomId: string, olderUnloaded: boolean): RoomTimeline {
  const changes = records.map((r) => changeFor(r, roomId)).filter((c): c is RoomChange => c !== null)
    .sort((a, b) => a.at - b.at || a.record.id.localeCompare(b.record.id));
  const steps: Step[] = [];
  if (changes.length === 0) return { roomId, steps, changes, olderUnloaded, createdAt: null };

  const firstWithState = changes.find((c) => c.hasRoomSnapshot);
  const created = firstWithState ? firstWithState.roomBefore === null && firstWithState.roomAfter !== null && firstWithState.record.action !== 'office.floor.layout' : false;
  steps.push({
    kind: 'baseline', index: 0, state: firstWithState?.roomBefore ?? null,
    created, olderUnloaded: olderUnloaded && !created, at: changes[0].at,
  });

  let known: Snap | null = created ? null : (firstWithState?.roomBefore ?? null);
  let knownAt = changes[0].at;
  let createdAt: number | null = created && firstWithState ? firstWithState.at : null;
  for (const change of changes) {
    if (change.hasRoomSnapshot) {
      // The record's `before` is the state just before it. If it differs from the previous record's `after`,
      // something changed in between that no record explains.
      if (known && change.roomBefore) {
        const broken = diffFields(known, change.roomBefore, ROOM_FIELDS).map((f) => f.field);
        if (broken.length) steps.push({ kind: 'gap', index: steps.length, from: knownAt, to: change.at, fields: broken });
      }
      known = change.roomAfter;
      knownAt = change.at;
      if (change.record.action === 'office.room.create' || change.record.action === 'office.room.copy') createdAt = createdAt ?? change.at;
      steps.push({ kind: 'change', index: steps.length, change, state: known, deleted: change.roomAfter === null });
    } else {
      steps.push({ kind: 'change', index: steps.length, change, state: known, deleted: false });
    }
  }
  return { roomId, steps, changes, olderUnloaded, createdAt };
}

export type StateAtResult =
  | { status: 'known'; stepIndex: number }
  | { status: 'unknown'; reason: 'future' | 'older' | 'gap' | 'invalid' | 'empty'; stepIndex: number | null }
  | { status: 'not_created'; stepIndex: number };

/**
 * Which step describes the room at a time. Anything the records cannot back says so; there is no nearest
 * guess. A time earlier than the oldest loaded record is unknown (the records before it may be unloaded or gone),
 * unless a loaded record created the room, which proves it did not exist before.
 */
export function stateAt(timeline: RoomTimeline, isoOrMs: string | number, now: number = Date.now()): StateAtResult {
  const t = typeof isoOrMs === 'number' ? isoOrMs : Date.parse(isoOrMs);
  if (Number.isNaN(t)) return { status: 'unknown', reason: 'invalid', stepIndex: null };
  if (timeline.steps.length === 0) return { status: 'unknown', reason: 'empty', stepIndex: null };
  if (t > now) return { status: 'unknown', reason: 'future', stepIndex: null };
  const firstAt = timeline.changes[0].at;
  if (t < firstAt) {
    if (timeline.createdAt !== null && timeline.createdAt <= firstAt) return { status: 'not_created', stepIndex: 0 };
    return { status: 'unknown', reason: 'older', stepIndex: 0 };
  }
  let best = -1;
  for (const step of timeline.steps) {
    if (step.kind === 'change' && step.change.at <= t) best = step.index;
  }
  // A gap step sits just before the change it precedes: if time falls between its bounds the state is unknown.
  for (const step of timeline.steps) {
    if (step.kind === 'gap' && t >= step.from && t < step.to) return { status: 'unknown', reason: 'gap', stepIndex: step.index };
  }
  return best < 0 ? { status: 'unknown', reason: 'older', stepIndex: 0 } : { status: 'known', stepIndex: best };
}

/** The recorded desk states in this room as of a step: only desks that have a record, never the rest. */
export function desksAt(timeline: RoomTimeline, stepIndex: number): { deskId: string; state: Snap; source: 'before' | 'after' }[] {
  const step = timeline.steps[stepIndex];
  if (!step) return [];
  const current = new Map<string, { state: Snap | null; source: 'before' | 'after' }>();
  // Before a desk's first record, that record's `before` is the only thing known about it.
  for (const change of timeline.changes) for (const d of change.desks) if (!current.has(d.deskId)) current.set(d.deskId, { state: d.before, source: 'before' });
  if (step.kind !== 'baseline') {
    const limit = step.kind === 'change' ? step.change : null;
    const until = step.kind === 'change' ? step.change.at : step.from;
    for (const change of timeline.changes) {
      if (change.at > until || (limit && change.at === until && change.record.id.localeCompare(limit.record.id) > 0)) break;
      for (const d of change.desks) current.set(d.deskId, { state: d.after, source: 'after' });
    }
  }
  const out: { deskId: string; state: Snap; source: 'before' | 'after' }[] = [];
  for (const [deskId, v] of current) if (v.state && v.state.room_id === timeline.roomId) out.push({ deskId, state: v.state, source: v.source });
  return out;
}

export interface DiffGroup { key: string; label: string | null; fields: FieldChange[] }

const SKIP = new Set(['id', 'revision']);
function unionKeys(a: Snap | null, b: Snap | null): string[] {
  return [...new Set([...Object.keys(a ?? {}), ...Object.keys(b ?? {})])].filter((k) => !SKIP.has(k));
}

/**
 * The before and after values a record carries, as groups of changed fields. A floor layout record has one group
 * per room or desk that differs. Only recorded values appear; nothing is inferred.
 */
export function recordDiffs(record: OfficeHistoryEvidence): DiffGroup[] {
  const meta = record.metadata;
  const before = isSnap(meta.before) ? meta.before : null;
  const after = isSnap(meta.after) ? meta.after : null;
  if (!before && !after) return [];
  if (record.action === 'office.floor.layout') {
    const list = (side: Snap | null, key: string) => (side && Array.isArray(side[key]) ? (side[key] as unknown[]).filter(isSnap) : []);
    const groups: DiffGroup[] = [];
    for (const key of ['rooms', 'desks'] as const) {
      const ids = new Set<string>([...list(before, key), ...list(after, key)].map((s) => String(s.id)));
      for (const id of ids) {
        const b = list(before, key).find((s) => s.id === id) ?? null;
        const a = list(after, key).find((s) => s.id === id) ?? null;
        const fields = diffFields(b, a, key === 'rooms' ? ROOM_FIELDS : DESK_FIELDS);
        if (fields.length) groups.push({ key: `${key}:${id}`, label: String(a?.name ?? a?.label ?? b?.name ?? b?.label ?? id), fields });
      }
    }
    return groups;
  }
  const fields = diffFields(before, after, unionKeys(before, after));
  return fields.length ? [{ key: 'record', label: null, fields }] : [];
}
