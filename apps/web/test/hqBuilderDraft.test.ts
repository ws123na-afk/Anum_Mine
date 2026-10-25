import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { OfficeDesk, OfficeRoom } from '../src/lib/officeSpaceClient.ts';
import {
  canRedo, canUndo, changedDeskIds, changedRoomIds, commit, isDirty, mapHistory, rebase, redo, rotateRoom, setDeskPosition,
  setRect, snapshotFromRecords, startHistory, toLayoutRequest, undo, withEntries, withoutEntries, withRoom, HISTORY_LIMIT,
  type Snapshot,
} from '../src/hqBuilder/draftModel.ts';
import { validateDraft, hasBlocking, roomSeverity } from '../src/hqBuilder/validate.ts';

const stamp = '2026-10-25T08:00:00Z';
const record = { tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: stamp, updated_at: stamp };
const room = (id: string, x: number | null, z: number | null, w = 6, d = 5, revision = 1): OfficeRoom => ({
  ...record, id, revision, floor_id: 'f', name: id, kind: 'office', capacity: 6,
  scene_x: x, scene_z: z, scene_width: x == null ? null : w, scene_depth: x == null ? null : d,
});
const desk = (id: string, roomId: string, x: number | null, z: number | null, revision = 1): OfficeDesk => ({
  ...record, id, revision, room_id: roomId, label: id, agent_id: null, pos_x: x, pos_z: z,
});
const floor = { width: 60, depth: 40 };

function load() {
  return snapshotFromRecords(
    [room('a', 0, 0), room('b', 10, 0), room('c', null, null)],
    [desk('d1', 'a', 1, 1), desk('d2', 'a', null, null)],
  );
}

test('a snapshot reads placed and unplaced rooms and desks from the records', () => {
  const { snapshot, revisions } = load();
  assert.equal(snapshot.rooms.a.placed, true);
  assert.equal(snapshot.rooms.c.placed, false);
  assert.equal(snapshot.desks.d1.posX, 1);
  assert.equal(snapshot.desks.d2.posX, null);
  assert.equal(revisions.a, 1);
  assert.equal(isDirty(snapshot, snapshot), false);
});

test('draft diff to payload: only what changed, with the loaded revisions, and the door and rotation with it', () => {
  const { snapshot, revisions } = load();
  revisions.b = 4;
  let next = setRect(snapshot, 'b', { x: 12, z: 3, width: 6, depth: 5 });
  next = setDeskPosition(next, 'd1', 2, 2);
  assert.deepEqual(changedRoomIds(snapshot, next), ['b']);
  assert.deepEqual(changedDeskIds(snapshot, next), ['d1']);
  assert.deepEqual(toLayoutRequest(snapshot, next, revisions), {
    rooms: [{ id: 'b', expected_revision: 4, x: 12, z: 3, width: 6, depth: 5, rotation: 0, door_side: 'south' }],
    desks: [{ id: 'd1', expected_revision: 1, pos_x: 2, pos_z: 2 }],
  });
  assert.deepEqual(toLayoutRequest(snapshot, snapshot, revisions), {});
});

test('placing an unplaced room and a door change are part of the payload; clearing a desk is not', () => {
  const { snapshot, revisions } = load();
  let next = setRect(snapshot, 'c', { x: 20, z: 0, width: 6, depth: 5 });
  next = withRoom(next, 'a', { doorSide: 'north' });
  next = setDeskPosition(next, 'd2', 3, 3);
  const body = toLayoutRequest(snapshot, next, revisions);
  assert.deepEqual(body.rooms?.map((r) => [r.id, r.door_side]), [['a', 'north'], ['c', 'south']]);
  assert.deepEqual(body.desks?.map((d) => d.id), ['d2']);
  const cleared = setDeskPosition(snapshot, 'd1', null, null);
  assert.deepEqual(toLayoutRequest(snapshot, cleared, revisions), {});
});

test('rotating swaps the footprint, turns rotation and door, and turns the desks with the room', () => {
  const { snapshot } = load();
  const turned = rotateRoom(snapshot, 'a', floor);
  assert.equal(turned.rooms.a.width, 5);
  assert.equal(turned.rooms.a.depth, 6);
  assert.equal(turned.rooms.a.rotation, 90);
  assert.equal(turned.rooms.a.doorSide, 'west');
  // d1 was at (1, 1) in a 6 wide, 5 deep room: it becomes (5 - 1, 1) = (4, 1) in the 5 wide, 6 deep room.
  assert.deepEqual([turned.desks.d1.posX, turned.desks.d1.posZ], [4, 1]);
  assert.equal(validateDraft(snapshot, turned, floor).some((i) => i.code === 'DESK_OUT_OF_ROOM'), false);
  const four = [1, 2, 3, 4].reduce<Snapshot>((s) => rotateRoom(s, 'a', floor), snapshot);
  assert.equal(four.rooms.a.rotation, 0);
  assert.deepEqual([four.rooms.a.width, four.rooms.a.depth], [6, 5]);
  assert.deepEqual([four.desks.d1.posX, four.desks.d1.posZ], [1, 1]);
});

test('undo and redo walk the history; a new edit drops the redo branch', () => {
  const { snapshot } = load();
  let h = startHistory(snapshot);
  h = commit(h, setRect(h.present, 'a', { x: 1, z: 0, width: 6, depth: 5 }));
  h = commit(h, setRect(h.present, 'a', { x: 2, z: 0, width: 6, depth: 5 }));
  assert.equal(canUndo(h), true);
  assert.equal(canRedo(h), false);
  h = undo(h);
  assert.equal(h.present.rooms.a.x, 1);
  assert.equal(canRedo(h), true);
  h = redo(h);
  assert.equal(h.present.rooms.a.x, 2);
  h = undo(undo(h));
  assert.equal(h.present.rooms.a.x, 0);
  assert.equal(undo(h), h);
  h = commit(h, setRect(h.present, 'a', { x: 9, z: 0, width: 6, depth: 5 }));
  assert.equal(canRedo(h), false);
  assert.equal(redo(h), h);
});

test('steps with the same key close together share one undo step; different keys or a pause do not', () => {
  const { snapshot } = load();
  let h = startHistory(snapshot);
  h = commit(h, setRect(h.present, 'a', { x: 1, z: 0, width: 6, depth: 5 }), 'move:a', 1000);
  h = commit(h, setRect(h.present, 'a', { x: 2, z: 0, width: 6, depth: 5 }), 'move:a', 1200);
  h = commit(h, setRect(h.present, 'a', { x: 3, z: 0, width: 6, depth: 5 }), 'move:a', 1400);
  assert.equal(h.past.length, 1);
  assert.equal(h.present.rooms.a.x, 3);
  h = undo(h);
  assert.equal(h.present.rooms.a.x, 0);
  let g = startHistory(snapshot);
  g = commit(g, setRect(g.present, 'a', { x: 1, z: 0, width: 6, depth: 5 }), 'move:a', 1000);
  g = commit(g, setRect(g.present, 'a', { x: 2, z: 0, width: 6, depth: 5 }), 'move:a', 5000);
  assert.equal(g.past.length, 2);
});

test('the history is capped and an unchanged snapshot is not a step', () => {
  const { snapshot } = load();
  let h = startHistory(snapshot);
  assert.equal(commit(h, h.present), h);
  for (let i = 1; i <= HISTORY_LIMIT + 20; i += 1) h = commit(h, setRect(h.present, 'a', { x: i, z: 0, width: 6, depth: 5 }));
  assert.equal(h.past.length, HISTORY_LIMIT);
});

test('entries the server created or deleted join or leave every history step', () => {
  const { snapshot } = load();
  let h = startHistory(snapshot);
  h = commit(h, setRect(h.present, 'a', { x: 1, z: 0, width: 6, depth: 5 }));
  const fresh = snapshotFromRecords([room('n', 30, 0)], []).snapshot;
  h = mapHistory(h, (s) => withEntries(s, [fresh.rooms.n], []));
  h = undo(h);
  assert.ok(h.present.rooms.n, 'an undo does not make the new room vanish');
  h = mapHistory(h, (s) => withoutEntries(s, ['b'], []));
  assert.equal(redo(h).present.rooms.b, undefined);
});

test('live checks: overlap and out of bounds block, old problems only warn', () => {
  const { snapshot } = snapshotFromRecords([room('a', 0, 0), room('b', 10, 0), room('old1', 30, 30), room('old2', 32, 31)], []);
  const clean = validateDraft(snapshot, snapshot, floor);
  assert.equal(hasBlocking(clean), false);
  assert.equal(clean.filter((i) => i.code === 'ROOM_OVERLAP').every((i) => i.severity === 'warning'), true);
  const overlapping = setRect(snapshot, 'b', { x: 4, z: 0, width: 6, depth: 5 });
  const issues = validateDraft(snapshot, overlapping, floor);
  assert.equal(hasBlocking(issues), true);
  assert.deepEqual([...roomSeverity(issues).entries()].filter(([, s]) => s === 'error').map(([id]) => id).sort(), ['a', 'b']);
  const outside = setRect(snapshot, 'a', { x: 58, z: 0, width: 6, depth: 5 });
  assert.equal(validateDraft(snapshot, outside, floor).some((i) => i.code === 'ROOM_OUT_OF_BOUNDS' && i.severity === 'error'), true);
  assert.equal(validateDraft(snapshot, outside, null).some((i) => i.code === 'ROOM_OUT_OF_BOUNDS'), false);
});

test('live checks: a desk outside its room blocks when its room is being saved', () => {
  const { snapshot } = snapshotFromRecords([room('a', 0, 0)], [desk('d1', 'a', 5, 4)]);
  const shrunk = setRect(snapshot, 'a', { x: 0, z: 0, width: 3, depth: 3 });
  const issues = validateDraft(snapshot, shrunk, floor);
  assert.deepEqual(issues.filter((i) => i.code === 'DESK_OUT_OF_ROOM').map((i) => [i.deskId, i.severity]), [['d1', 'error']]);
});

test('500 rooms validate quickly', () => {
  const rooms = Array.from({ length: 500 }, (_, i) => room(`r${i}`, (i % 25) * 7, Math.floor(i / 25) * 6));
  const { snapshot } = snapshotFromRecords(rooms, []);
  const moved = setRect(snapshot, 'r0', { x: 1, z: 1, width: 6, depth: 5 });
  const started = Date.now();
  const issues = validateDraft(snapshot, moved, { width: 200, depth: 130 });
  assert.ok(Date.now() - started < 500);
  assert.equal(issues.length, 0);
});

test('reload and reapply keeps untouched changes, takes the other editor version of what moved, drops what was deleted', () => {
  const old = snapshotFromRecords([room('a', 0, 0), room('b', 10, 0), room('c', 20, 0), room('d', 30, 0)], []).snapshot;
  let mine = setRect(old, 'a', { x: 1, z: 1, width: 6, depth: 5 });
  mine = setRect(mine, 'b', { x: 11, z: 1, width: 6, depth: 5 });
  mine = setRect(mine, 'c', { x: 21, z: 1, width: 6, depth: 5 });
  mine = setRect(mine, 'd', { x: 31, z: 1, width: 6, depth: 5 });
  // Meanwhile: someone moved b, someone deleted c, someone saved the same place for d, nobody touched a.
  const latest = snapshotFromRecords([room('a', 0, 0, 6, 5, 2), room('b', 15, 15, 6, 5, 2), room('d', 31, 1, 6, 5, 2), room('e', 40, 0)], []).snapshot;
  const result = rebase(old, mine, latest);
  assert.deepEqual([result.present.rooms.a.x, result.present.rooms.a.z], [1, 1]);
  assert.deepEqual([result.present.rooms.b.x, result.present.rooms.b.z], [15, 15]);
  assert.equal(result.present.rooms.c, undefined);
  assert.ok(result.present.rooms.e, 'a room someone else added appears');
  assert.deepEqual(result.dropped.map((d) => [d.id, d.reason]).sort(), [['b', 'changed'], ['c', 'removed']]);
  assert.equal(result.kept, 1);
  assert.equal(isDirty(latest, result.present), true);
  assert.deepEqual(changedRoomIds(latest, result.present), ['a']);
});

test('reload and reapply for desks', () => {
  const old = snapshotFromRecords([room('a', 0, 0)], [desk('d1', 'a', 1, 1), desk('d2', 'a', 2, 2)]).snapshot;
  let mine = setDeskPosition(old, 'd1', 3, 3);
  mine = setDeskPosition(mine, 'd2', 4, 4);
  const latest = snapshotFromRecords([room('a', 0, 0)], [desk('d1', 'a', 1, 1, 2), desk('d2', 'a', 5, 5, 2)]).snapshot;
  const result = rebase(old, mine, latest);
  assert.deepEqual([result.present.desks.d1.posX, result.present.desks.d1.posZ], [3, 3]);
  assert.deepEqual([result.present.desks.d2.posX, result.present.desks.d2.posZ], [5, 5]);
  assert.deepEqual(result.dropped.map((d) => d.id), ['d2']);
});
