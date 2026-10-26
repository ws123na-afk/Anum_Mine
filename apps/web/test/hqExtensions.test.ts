import assert from 'node:assert/strict';
import { test } from 'node:test';
import { HqCommandBus, resolveNavTarget, type NavScene } from '../src/lib/hqExtensions.ts';
import type { Campus, CampusBuilding } from '../src/lib/campusModel.ts';

const b = (id: string, name: string, over: Partial<CampusBuilding> = {}): CampusBuilding => ({ workspace_id: id, tenant_id: 't', name, kind: 'business', icon: null, floors: 2, accessible: true, totals: null, presence_verified_at: null, ...over });
const campus: Campus = { buildings: [b('ws-a', 'Al Noor Fleet'), b('ws-b', 'Tyre Pulse HQ'), b('ws-c', 'Tyre Archive'), b('ws-x', 'Legal', { accessible: false })], totals: null, generated_at: '2026-10-22T10:00:00Z' };
const scene: NavScene = {
  workspaceId: 'ws-a',
  floors: [
    { id: 'f0', level: 0, name: 'Ground floor', rooms: [{ id: 'r-meet', label: 'Meeting Room' }, { id: 'r-care', label: 'Customer Care' }] },
    { id: 'f1', level: 1, name: 'First floor', rooms: [{ id: 'r-meet2', label: 'Meeting Room B' }, { id: 'r-fin', label: 'Finance' }] },
  ],
  agents: [{ id: 'agent_fleet', name: 'Fleet Planner', roomId: 'r-fin' }, { id: 'agent_x', name: 'Floating Agent', roomId: null }],
};

test('a room name finds its floor; an agent finds its room', () => {
  assert.deepEqual(resolveNavTarget({ room: 'finance' }, campus, scene), { kind: 'apply', workspaceId: 'ws-a', floorId: 'f1', roomId: 'r-fin', agentId: null });
  assert.deepEqual(resolveNavTarget({ agent: 'Fleet Planner' }, campus, scene), { kind: 'apply', workspaceId: 'ws-a', floorId: 'f1', roomId: 'r-fin', agentId: 'agent_fleet' });
  assert.deepEqual(resolveNavTarget({ agent: 'floating' }, campus, scene), { kind: 'apply', workspaceId: 'ws-a', floorId: null, roomId: null, agentId: 'agent_x' });
});

test('floor accepts a level number, a name or an id, and narrows the room search', () => {
  assert.equal((resolveNavTarget({ floor: 1 }, campus, scene) as { floorId: string }).floorId, 'f1');
  assert.equal((resolveNavTarget({ floor: 'ground' }, campus, scene) as { floorId: string }).floorId, 'f0');
  assert.equal((resolveNavTarget({ floor: 'f1' }, campus, scene) as { floorId: string }).floorId, 'f1');
  const meeting = resolveNavTarget({ floor: 0, room: 'meeting' }, campus, scene);
  assert.equal((meeting as { roomId: string }).roomId, 'r-meet');
});

test('ambiguous and unknown names are refused with a message, not guessed', () => {
  const both = resolveNavTarget({ room: 'meeting' }, campus, scene);
  assert.equal(both.kind, 'error');
  assert.equal((both as { code: string }).code, 'ambiguous');
  assert.equal((resolveNavTarget({ room: 'cafeteria' }, campus, scene) as { code: string }).code, 'not-found');
  assert.equal((resolveNavTarget({ floor: 9 }, campus, scene) as { code: string }).code, 'not-found');
  assert.equal((resolveNavTarget({ building: 'Tyre' }, campus, scene) as { code: string }).code, 'ambiguous');
});

test('a building in another workspace is a switch, an inaccessible one is refused', () => {
  assert.deepEqual(resolveNavTarget({ building: 'Tyre Pulse HQ', room: 'x' }, campus, scene), { kind: 'switch', workspaceId: 'ws-b' });
  assert.deepEqual(resolveNavTarget({ building: 'ws-a', floor: 1 }, campus, scene), { kind: 'apply', workspaceId: 'ws-a', floorId: 'f1', roomId: null, agentId: null });
  assert.equal((resolveNavTarget({ building: 'Legal' }, campus, scene) as { code: string }).code, 'not-accessible');
  assert.equal(resolveNavTarget({ room: 'Finance' }, campus, null).kind, 'error');
});

function fakeSession() {
  const store = new Map<string, string>();
  (globalThis as { sessionStorage?: unknown }).sessionStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); }, removeItem: (k: string) => { store.delete(k); } };
  return store;
}

test('the command bus keeps a pending move for a minute and hands it to the next screen once', () => {
  fakeSession();
  const bus = new HqCommandBus();
  bus.keepPending({ building: 'Tyre Pulse HQ', room: 'Meeting Room' });
  assert.deepEqual(bus.takePending(), { building: 'Tyre Pulse HQ', room: 'Meeting Room' });
  assert.equal(bus.takePending(), null);
  bus.keepPending({ room: 'x' });
  assert.equal(bus.takePending(Date.now() + 61_000), null);
});

test('an attached handler receives commands and a pending move on attach', async () => {
  fakeSession();
  const bus = new HqCommandBus();
  bus.keepPending({ floor: 1 });
  const seen: unknown[] = [];
  const off = bus.attach(async (target) => { seen.push(target); return { ok: true, applied: { workspaceId: null, floorId: null, roomId: null, agentId: null } }; });
  assert.deepEqual(seen, [{ floor: 1 }]);
  assert.equal(bus.mounted, true);
  const result = await bus.navigateTo({ room: 'Finance' });
  assert.equal(result.ok, true);
  assert.deepEqual(seen[1], { room: 'Finance' });
  off();
  assert.equal(bus.mounted, false);
  const after = await bus.navigateTo({ room: 'Finance' });
  assert.equal(after.ok, false);
  assert.equal((after as { code: string }).code, 'not-mounted');
});
