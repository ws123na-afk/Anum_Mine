import assert from 'node:assert/strict';
import { test } from 'node:test';
import { arcPath, buildingStatus, campusFromApi, campusFromMemberships, campusTotals, donutArcs, filterBuildings, layoutCampus, readingOrder, stepBuilding, type CampusBuilding, type CampusTotals } from '../src/lib/campusModel.ts';

const totals = (over: Partial<CampusTotals> = {}): CampusTotals => ({ agents: 0, working: 0, queued: 0, meeting: 0, waiting_approval: 0, blocked: 0, idle: 0, paused: 0, unknown: 0, ...over });
const b = (id: string, over: Partial<CampusBuilding> = {}): CampusBuilding => ({ workspace_id: id, tenant_id: 'tenant-a', name: id, kind: 'business', icon: null, floors: 2, accessible: true, totals: null, presence_verified_at: null, ...over });

test('campus totals only count buildings with verified presence', () => {
  const sum = campusTotals([b('a', { totals: totals({ agents: 3, working: 2, idle: 1 }) }), b('b'), b('c', { totals: totals({ agents: 2, meeting: 2 }) })]);
  assert.equal(sum.totals?.agents, 5);
  assert.equal(sum.totals?.working, 2);
  assert.equal(sum.reporting, 2);
  assert.equal(sum.missing, 1);
  assert.equal(campusTotals([b('x')]).totals, null);
});

test('building status never guesses without presence', () => {
  assert.equal(buildingStatus(b('a')), 'unavailable');
  assert.equal(buildingStatus(b('a', { totals: totals() })), 'empty');
  assert.equal(buildingStatus(b('a', { totals: totals({ agents: 2, working: 1, blocked: 1 }) })), 'blocked');
  assert.equal(buildingStatus(b('a', { totals: totals({ agents: 2, working: 1, idle: 1 }) })), 'working');
});

test('donut arcs cover the circle once and skip empty statuses', () => {
  const arcs = donutArcs(totals({ agents: 4, working: 2, meeting: 1, idle: 1 }));
  assert.deepEqual(arcs.map((a) => a.key), ['working', 'meeting', 'idle']);
  assert.ok(Math.abs(arcs[arcs.length - 1].end - Math.PI * 2) < 1e-9);
  assert.deepEqual(donutArcs(totals()), []);
  assert.match(arcPath(50, 50, 40, 28, 0, Math.PI * 2), /^M .* Z M .* Z$/);
});

test('layout puts the executive tower in the middle and keeps every building apart', () => {
  const list = [b('exec', { kind: 'executive', floors: 3 }), ...Array.from({ length: 12 }, (_, i) => b('w' + i))];
  const layout = layoutCampus(list);
  assert.equal(layout.length, 13);
  assert.deepEqual([layout[0].x, layout[0].z, layout[0].kind], [0, 0, 'executive']);
  for (let i = 0; i < layout.length; i++) {
    for (let j = i + 1; j < layout.length; j++) {
      const p = layout[i], q = layout[j];
      const overlapX = Math.abs(p.x - q.x) < (p.width + q.width) / 2;
      const overlapZ = Math.abs(p.z - q.z) < (p.depth + q.depth) / 2;
      assert.ok(!(overlapX && overlapZ), `${p.workspaceId} overlaps ${q.workspaceId}`);
    }
  }
  assert.deepEqual(layoutCampus(list), layout);
});

test('layout without an executive building has no tower and height follows real floors', () => {
  const layout = layoutCampus([b('a', { floors: 6 }), b('b', { floors: 1 })]);
  assert.ok(layout.every((p) => p.kind !== 'executive'));
  const tall = layout.find((p) => p.workspaceId === 'a')!;
  const low = layout.find((p) => p.workspaceId === 'b')!;
  assert.ok(tall.height > low.height);
});

test('adapter from memberships shows presence only for the open workspace', () => {
  const campus = campusFromMemberships(
    [{ workspaceId: 'ws-a', workspaceName: 'Alpha' }, { workspaceId: 'ws-b', workspaceName: null }, { workspaceId: 'ws-a', workspaceName: 'Alpha' }],
    { tenantId: 'tenant-a', workspaceId: 'ws-a' },
    { totals: totals({ agents: 2, working: 1, idle: 1 }), floors: 3, verifiedAt: '2026-10-22T10:00:00Z' },
  );
  assert.equal(campus.buildings.length, 2);
  const [a, bb] = campus.buildings;
  assert.equal(a.totals?.agents, 2);
  assert.equal(a.floors, 3);
  assert.equal(bb.totals, null);
  assert.equal(bb.name, 'ws-b');
  assert.equal(campus.totals?.agents, 2);
});

test('adapter adds the open workspace when memberships do not list it', () => {
  const campus = campusFromMemberships([], { tenantId: 't', workspaceId: 'ws-own' }, { totals: null, floors: null, verifiedAt: null });
  assert.equal(campus.buildings.length, 1);
  assert.equal(campus.buildings[0].totals, null);
});

test('campus endpoint payload (contract v1) is read defensively', () => {
  const states = (over: Record<string, number> = {}) => ({ working: 1, queued: 1, meeting: 0, waiting_approval: 0, blocked: 0, idle: 1, paused: 0, unknown: 0, ...over });
  const campus = campusFromApi({
    version: 1, tenant_id: 'tenant-a', generated_at: '2026-10-22T10:00:00Z',
    buildings: [
      { workspace_id: 'w1', name: 'One', kind: 'executive', icon: 'landmark', profile_revision: 2, role: 'owner', archived: false, accessible: true, floors: [{ id: 'f' }, { id: 'g' }, { id: 'h' }], rooms: 6, desks: 9, agents: { total: 3, by_state: states() }, presence_verified_at: '2026-10-22T10:00:00Z', presence_as_of: '2026-10-22T10:00:01Z' },
      { workspace_id: 'w1', name: 'Duplicate' },
      { workspace_id: 'w2', name: 'Two', kind: null, accessible: true, floors: [], agents: { total: -1, by_state: states() } },
      { workspace_id: 'w3', name: 'Three', kind: 'castle', accessible: false, agents: { total: 2, by_state: states() } },
      { name: 'No id' }, null,
    ],
  });
  assert.equal(campus.buildings.length, 3);
  const [one, two, three] = campus.buildings;
  assert.deepEqual([one.kind, one.kindKnown, one.floors, one.tenant_id, one.role, one.profileRevision, one.icon], ['executive', true, 3, 'tenant-a', 'owner', 2, 'landmark']);
  assert.equal(one.totals?.agents, 3);
  assert.equal(one.totals?.queued, 1);
  assert.equal(one.presence_verified_at, '2026-10-22T10:00:00Z');
  assert.deepEqual([two.kind, two.kindKnown, two.floors, two.totals], ['business', false, null, null]);
  assert.deepEqual([three.kindKnown, three.accessible, three.totals], [false, false, null]);
  assert.equal(campus.totals?.agents, 3);
  assert.deepEqual(campusFromApi(undefined).buildings, []);
});

test('list filter, reading order and keyboard stepping', () => {
  const list = [b('z', { name: 'Zeta' }), b('e', { name: 'Exec', kind: 'executive' }), b('a', { name: 'Ärgerlich' })];
  assert.deepEqual(readingOrder(list).map((x) => x.workspace_id), ['e', 'a', 'z']);
  assert.deepEqual(filterBuildings(list, 'arger').map((x) => x.workspace_id), ['a']);
  assert.deepEqual(filterBuildings(list, 'executive').map((x) => x.workspace_id), ['e']);
  const order = ['e', 'a', 'z'];
  assert.equal(stepBuilding(order, null, 1), 'e');
  assert.equal(stepBuilding(order, null, -1), 'z');
  assert.equal(stepBuilding(order, 'z', 1), 'e');
  assert.equal(stepBuilding(order, 'e', -1), 'z');
  assert.equal(stepBuilding([], 'e', 1), null);
});
