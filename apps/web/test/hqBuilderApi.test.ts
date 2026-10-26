import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiError, apiErrorFrom } from '../src/lib/errors.ts';
import { createBuilderApi } from '../src/hqBuilder/builderApi.ts';
import { explainError, locationId } from '../src/hqBuilder/explain.ts';
import { setLocale } from '../src/lib/i18n.ts';

function recorder() {
  const calls: { path: string; init: RequestInit }[] = [];
  const request = async <T,>(path: string, init: RequestInit): Promise<T> => { calls.push({ path, init }); return {} as T; };
  const remove = async (path: string, init: RequestInit): Promise<void> => { calls.push({ path, init }); };
  return { calls, api: createBuilderApi(request, remove) };
}

test('the builder calls use the documented paths, methods and bodies', async () => {
  const { calls, api } = recorder();
  await api.saveLayout('floor 1', { rooms: [{ id: 'a', expected_revision: 2, x: 1, z: 2, width: 6, depth: 5, rotation: 90, door_side: 'east' }] });
  await api.patchRoom('room-1', { expected_revision: 3, name: 'Sales' });
  await api.fromTemplate({ id: 'room-2', floor_id: 'f', template_id: 'open_office', with_desks: true });
  await api.copyRoom('room-1', { id: 'room-3', copy_desks: false });
  await api.deleteRoom('room-1', 4);
  await api.deleteFloor('f', 2);
  await api.deleteBuilding('b', 1);
  await api.assignDesk('d1', null, 5);
  await api.team('room-1');
  await api.templates();
  assert.deepEqual(calls.map((c) => `${c.init.method} ${c.path}`), [
    'PUT /api/v1/office-spaces/floors/floor%201/layout',
    'PATCH /api/v1/office-spaces/rooms/room-1',
    'POST /api/v1/office-spaces/rooms/from-template',
    'POST /api/v1/office-spaces/rooms/room-1/copy',
    'DELETE /api/v1/office-spaces/rooms/room-1?expected_revision=4',
    'DELETE /api/v1/office-spaces/floors/f?expected_revision=2',
    'DELETE /api/v1/office-spaces/buildings/b?expected_revision=1',
    'PATCH /api/v1/office-spaces/desks/d1/assignment',
    'GET /api/v1/office-spaces/rooms/room-1/team',
    'GET /api/v1/office-spaces/templates',
  ]);
  assert.equal(JSON.parse(String(calls[0].init.body)).rooms[0].door_side, 'east');
  assert.deepEqual(JSON.parse(String(calls[7].init.body)), { agent_id: null, expected_revision: 5 });
  // Scope is never in a body.
  for (const call of calls) assert.equal(/tenant|workspace/.test(String(call.init.body ?? '')), false);
});

const envelope = (status: number, details: { location?: string; message: string; type?: string }[], message = 'refused') =>
  apiErrorFrom(status, { error: { code: status === 409 ? 'conflict' : 'validation_error', message, details } });

test('error details keep the stable type code', () => {
  const error = envelope(422, [{ location: 'rooms.a', message: 'Room a overlaps room b', type: 'ROOM_OVERLAP' }]);
  assert.equal(error.details[0].type, 'ROOM_OVERLAP');
  assert.equal(locationId('rooms.a'), 'a');
  assert.equal(locationId(null), null);
});

test('every server code is explained in plain words, in both languages', () => {
  const names: Record<string, string> = { a: 'Sales', b: 'Lobby', d1: 'Desk 1' };
  const nameOf = (id: string) => names[id] ?? id;
  const codes = ['ROOM_OVERLAP', 'ROOM_OUT_OF_BOUNDS', 'DESK_OUT_OF_ROOM', 'ROOM_NOT_PLACED', 'ROOM_NOT_ON_FLOOR', 'DESK_NOT_ON_FLOOR', 'STALE_REVISION', 'NO_FREE_SPACE', 'ROOM_NAME_TAKEN', 'FLOOR_LEVEL_TAKEN', 'DESK_LABEL_TAKEN', 'AGENT_ALREADY_SEATED', 'KIND_FORBIDS_DESKS', 'CAPACITY_BELOW_DESKS'];
  for (const locale of ['en', 'ar'] as const) {
    setLocale(locale);
    for (const code of codes) {
      const explained = explainError(envelope(422, [{ location: 'rooms.a', message: 'Room a overlaps room b', type: code }]), nameOf);
      assert.equal(explained.lines.length, 1);
      assert.notEqual(explained.lines[0], 'Room a overlaps room b', `${code} has its own sentence in ${locale}`);
      assert.equal(/ROOM_|DESK_|STALE_/.test(explained.lines[0]), false, 'no raw code is shown');
    }
  }
  setLocale('en');
  const overlap = explainError(envelope(422, [{ location: 'rooms.a', message: 'Room a overlaps room b', type: 'ROOM_OVERLAP' }]), nameOf);
  assert.match(overlap.lines[0], /Sales/);
  assert.match(overlap.lines[0], /Lobby/);
});

test('stale revisions, forbidden, unavailable and blocked deletes are told apart', () => {
  const nameOf = (id: string) => id;
  const stale = explainError(envelope(409, [{ location: 'rooms.a', message: 'changed', type: 'STALE_REVISION' }]), nameOf);
  assert.equal(stale.stale, true);
  assert.equal(explainError(new ApiError(403, null, 'no'), nameOf).forbidden, true);
  assert.equal(explainError(new ApiError(503, null, 'down'), nameOf).unavailable, true);
  const blocked = explainError(envelope(409, [
    { location: 'rooms.r1', message: 'Room Sales (2 desk(s))', type: 'ROOM_PRESENT' },
    { location: 'rooms.r2', message: 'Room Lobby (0 desk(s))', type: 'ROOM_PRESENT' },
  ]), nameOf);
  assert.match(blocked.title, /rooms/i);
  assert.deepEqual(blocked.lines, ['Room Sales (2 desk(s))', 'Room Lobby (0 desk(s))']);
  assert.equal(blocked.stale, false);
  const network = explainError(new Error('boom'), nameOf);
  assert.match(network.title, /could not be reached/);
});
