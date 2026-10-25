import assert from 'node:assert/strict';
import { test } from 'node:test';
import { campusCamera, buildingCamera } from '../src/lib/hqCamera.ts';
import { cameraEye } from '../src/lib/headquartersSpatial.ts';
import { easeInOutCubic, eyeInside, flightDuration, planFlight } from '../src/hq-nav/flight.ts';
import { cardDensities, cardTags, currentStopId, departmentsOf, floorEntries, initialsOf, peopleAtPosts, planModel, roomStops, stripStops } from '../src/hq-nav/navModel.ts';
import type { Campus, CampusBuilding } from '../src/lib/campusModel.ts';
import type { RoomGeometry, SceneBuilding, SceneDesk, SceneFloor } from '../src/lib/headquartersProjection.ts';
import { indexPresence } from '../src/lib/hqPresence.ts';

const room = (id: string, kind: RoomGeometry['kind'], x = 0, z = 0, label = id): RoomGeometry => ({ id, floorId: 'f', buildingId: 'b', label, kind, x, z, width: 6, depth: 4, height: 3, capacity: 4, revision: 1, deskIds: [], evidence: 'registry' });
const desk = (id: string, roomId: string, agentId: string | null, name?: string): SceneDesk => ({ id, roomId, label: id, position: { x: 1, y: 0, z: 1 }, agentId, agent: agentId && name ? { id: agentId, name, status: 'idle', observedAt: '', taskIds: [], source: 'authenticated-agent-snapshot' } : null, assignmentVisible: true });
const floor = (id: string, level: number, rooms: RoomGeometry[], desks: SceneDesk[] = []): SceneFloor => ({ id, name: `Floor ${level}`, level, rooms, desks, bounds: { width: 10, depth: 10 } });
const building = (floors: SceneFloor[]): SceneBuilding => ({ id: 'b', name: 'B', timezone: 'UTC', floors });
const cb = (id: string, over: Partial<CampusBuilding> = {}): CampusBuilding => ({ workspace_id: id, tenant_id: 't', name: id, kind: 'business', kindKnown: true, icon: null, floors: 2, accessible: true, totals: { agents: 1, working: 0, queued: 0, meeting: 0, waiting_approval: 0, blocked: 0, idle: 1, paused: 0, unknown: 0 }, presence_verified_at: null, ...over });

test('floors list the top floor first and count only saved rooms and assignments', () => {
  const b = building([floor('f0', 0, [room('r1', 'reception'), room('r2', 'office')], [desk('d1', 'r2', 'a1')]), floor('f1', 1, [])]);
  const rows = floorEntries(b);
  assert.deepEqual(rows.map((r) => r.id), ['f1', 'f0']);
  assert.equal(rows[0].rooms, 0);
  assert.equal(rows[1].assigned, 1);
  assert.deepEqual(rows[1].kinds.map((k) => k.kind), ['office', 'reception']);
  assert.deepEqual(floorEntries(null), []);
});

test('strip: campus, every building, then rooms; kinds only when the building is large', () => {
  const small = building([floor('f0', 0, [room('r1', 'office'), room('r2', 'reception'), room('r3', 'meeting')])]);
  assert.deepEqual(roomStops(small).map((s) => s.id), ['room:r2', 'room:r1', 'room:r3']);
  const many = building([floor('f0', 0, Array.from({ length: 12 }, (_, i) => room(`o${i}`, i < 10 ? 'office' : 'meeting')))]);
  assert.equal(roomStops(many).length, 2);
  const campus: Campus = { buildings: [cb('w2', { name: 'Zed' }), cb('w1', { name: 'Alpha' }), cb('w3', { archived: true })], totals: null, generated_at: '' };
  const stops = stripStops(campus, small, 'w1');
  assert.deepEqual(stops.map((s) => s.id).slice(0, 3), ['campus', 'building:w1', 'building:w2']);
  assert.ok(!stops.some((s) => s.id === 'building:w3'));
  assert.deepEqual(stripStops(null, null, 'w1').map((s) => s.id), ['campus']);
});

test('current stop follows level and selection', () => {
  assert.equal(currentStopId({ level: 'campus', selectedBuildingId: null, selectedRoomId: null, currentWorkspaceId: 'w' }), 'campus');
  assert.equal(currentStopId({ level: 'campus', selectedBuildingId: 'x', selectedRoomId: null, currentWorkspaceId: 'w' }), 'building:x');
  assert.equal(currentStopId({ level: 'building', selectedBuildingId: null, selectedRoomId: 'r', currentWorkspaceId: 'w' }), 'room:r');
  assert.equal(currentStopId({ level: 'building', selectedBuildingId: null, selectedRoomId: null, currentWorkspaceId: 'w' }), 'building:w');
});

test('card density: all full up to six, then pinned and busiest stay full, the rest shrink, none disappear', () => {
  const six = Array.from({ length: 6 }, (_, i) => cb(`b${i}`));
  assert.ok([...cardDensities(six, { selectedId: null, currentId: 'b0' }).values()].every((d) => d === 'full'));
  const twenty = Array.from({ length: 20 }, (_, i) => cb(`b${i}`, { totals: i === 19 ? null : { ...cb('x').totals!, agents: i } }));
  const d = cardDensities(twenty, { selectedId: 'b0', currentId: 'b1' });
  assert.equal(d.size, 20);
  assert.equal(d.get('b0'), 'full');
  assert.equal(d.get('b1'), 'full');
  assert.equal([...d.values()].filter((x) => x === 'full').length, 6);
  assert.equal([...d.values()].filter((x) => x === 'compact').length, 8);
  assert.equal([...d.values()].filter((x) => x === 'dot').length, 6);
  const crowded = Array.from({ length: 20 }, (_, i) => cb(`c${i}`, { totals: null }));
  assert.equal(cardDensities(crowded, { selectedId: 'c19', currentId: 'c18' }).get('c19'), 'full');
});

test('card density on a phone: two full, a few compact, the rest dots', () => {
  const many = Array.from({ length: 13 }, (_, i) => cb(`m${i}`, { totals: { ...cb('x').totals!, agents: i } }));
  const d = cardDensities(many, { selectedId: null, currentId: 'm0' }, 2, 6);
  assert.equal([...d.values()].filter((x) => x === 'full').length, 2);
  assert.equal([...d.values()].filter((x) => x === 'compact').length, 4);
  assert.equal([...d.values()].filter((x) => x === 'dot').length, 7);
  assert.equal(d.get('m0'), 'full');
});

test('card tags: a registry building names its workspace, then its floors', () => {
  const tags = cardTags(cb('b', { workspaceName: 'Home', floors: 3, rooms: 9, kindKnown: false }), ['Finance']);
  assert.deepEqual(tags, [{ kind: 'workspace', value: 'Home' }, { kind: 'floors', value: 3 }]);
});

test('card tags come from real fields only', () => {
  assert.deepEqual(cardTags(cb('a', { kindKnown: false, floors: null }), []), []);
  assert.deepEqual(cardTags(cb('a', { rooms: 7 }), ['Finance']).map((t) => t.kind), ['type', 'department', 'floors']);
  assert.deepEqual(departmentsOf(['Sales', 'Finance', 'Sales', null, ' ']), ['Sales', 'Finance']);
  assert.equal(initialsOf('Fleet Planner'), 'FP');
  assert.equal(initialsOf(''), '');
});

test('people list: assigned agents only, real state, private names hidden', () => {
  const f = floor('f', 0, [room('r1', 'office', 0, 0, 'Care')], [desk('d1', 'r1', 'a1', 'Ann'), desk('d2', 'r1', null), desk('d3', 'r1', 'a3', 'Cy'), desk('d4', 'r1', 'a4')]);
  const idx = indexPresence([{ agentId: 'a1', name: 'Ann', role: 'Support', status: 'working', taskTitles: [], observedAt: '' }, { agentId: 'desk:d2', name: '', role: null, status: 'unknown', taskTitles: [], observedAt: '', deskId: 'd2', reason: 'agent_not_visible' }]);
  const rows = peopleAtPosts(f, idx);
  assert.deepEqual(rows.map((r) => [r.key, r.state]), [['d1', 'known'], ['d3', 'not_connected'], ['d2', 'private'], ['d4', 'not_connected']]);
  assert.equal(rows.find((r) => r.key === 'd2')?.name, null);
  assert.equal(rows.find((r) => r.key === 'd4')?.name, null);
  assert.equal(peopleAtPosts(f, null).every((r) => r.state === 'not_connected'), true);
  assert.deepEqual(peopleAtPosts(floor('e', 0, [room('r', 'office')], [desk('x', 'r', null)]), idx), []);
});

test('plan model fits the saved geometry inside the box', () => {
  const f = floor('f', 0, [room('a', 'office', 0, 0), room('b', 'office', 20, 10)], [desk('d', 'a', 'x')]);
  const m = planModel(f, 100);
  assert.equal(m.rooms.length, 2);
  for (const r of m.rooms) { assert.ok(r.x >= 0 && r.y >= 0 && r.x + r.w <= m.width + 1e-6 && r.y + r.h <= m.height + 1e-6); }
  assert.equal(planModel(floor('e', 0, []), 100).rooms.length, 0);
});

test('flight: exact ends, shortest yaw, duration scales with distance, instant when reduced', () => {
  const from = campusCamera(80);
  const to = { ...from, target: { x: 60, y: 0, z: -40 }, distance: 70, yaw: 330 };
  const near = { ...from, target: { x: 4, y: 0, z: 0 } };
  const plan = planFlight({ ...from, yaw: 20 }, to);
  assert.equal(plan.at(0).yaw, 20);
  assert.equal(plan.at(1), to);
  assert.ok(Math.abs(plan.at(0.5).yaw - 355) < 30 || Math.abs(plan.at(0.5).yaw + 5) < 30, 'goes through north, not the long way');
  assert.ok(plan.duration > planFlight(from, near).duration);
  assert.ok(plan.duration <= 1800 && planFlight(from, near).duration >= 450);
  assert.equal(planFlight(from, to, { reducedMotion: true }).duration, 0);
  assert.equal(planFlight(from, to, { reducedMotion: true }).at(0.3), to);
  assert.equal(flightDuration(0, false), 0);
  assert.equal(easeInOutCubic(0), 0);
  assert.equal(easeInOutCubic(1), 1);
});

test('flight never puts the eye inside a building', () => {
  const tower = { min: { x: -10, y: 0, z: -10 }, max: { x: 10, y: 60, z: 10 } };
  const from = { ...campusCamera(80), target: { x: -60, y: 0, z: 0 }, distance: 40, pitch: 8 };
  const to = { ...campusCamera(80), target: { x: 60, y: 0, z: 0 }, distance: 40, pitch: 8, yaw: 215 };
  assert.equal(eyeInside(from, [tower]), false);
  assert.equal(eyeInside(to, [tower]), false);
  const plan = planFlight(from, to, { obstacles: [tower] });
  for (let i = 0; i <= 60; i++) {
    const c = plan.at(i / 60);
    const eye = cameraEye(c);
    assert.ok(!(eye.x > -10 && eye.x < 10 && eye.z > -10 && eye.z < 10 && eye.y < 60), `sample ${i} eye ${JSON.stringify(eye)}`);
    assert.ok(c.pitch >= 3 && c.pitch <= 87 && c.distance >= c.minDistance && c.distance <= c.maxDistance);
  }
  assert.ok(buildingCamera({ x: 0, y: 0, z: 0 }, 30).distance > 0);
});

test('layout by width: rail and side panel become slim, hidden or drawers, and the person can override', async () => {
  const { layoutFor } = await import('../src/hq-nav/useNavLayout.ts');
  assert.deepEqual([layoutFor(1440, null, null).rail, layoutFor(1440, null, null).side], ['full', 'open']);
  assert.deepEqual([layoutFor(1366, null, null).rail, layoutFor(1366, null, null).side], ['slim', 'open']);
  assert.deepEqual([layoutFor(1024, null, null).rail, layoutFor(1024, null, null).side], ['slim', 'drawer']);
  assert.deepEqual([layoutFor(768, null, null).rail, layoutFor(768, null, null).side], ['drawer', 'drawer']);
  assert.equal(layoutFor(390, null, null).compact, true);
  assert.equal(layoutFor(1440, 'closed', null).rail, 'slim');
  assert.equal(layoutFor(1366, 'open', null).rail, 'full');
  assert.equal(layoutFor(700, 'open', 'open').rail, 'drawer');
  assert.equal(layoutFor(1440, null, 'closed').side, 'hidden');
});

test('a workspace with several registry buildings draws each; other workspaces stay as they are', async () => {
  const { expandCampus, registryKey } = await import('../src/lib/campusRegistry.ts');
  const { workspaceOf } = await import('../src/lib/campusModel.ts');
  const names = ['ANUM HQ', 'Commerce', 'Tyre', 'Corporate', 'Knowledge', 'Ventures', 'Personal', 'Career'];
  const registry = names.map((n, i): SceneBuilding => building([floor(`f${i}`, 0, [room(`r${i}`, 'office', 0, 0, `${n} Office`)], i === 0 ? [desk('d1', `r0`, 'a1', 'Ann'), desk('d2', 'r0', 'a2', 'Bo')] : [])]) && ({ id: `b${i}`, name: n, timezone: 'UTC', floors: [floor(`f${i}`, 0, [room(`r${i}`, 'office', 0, 0, `${n} Office`)], i === 0 ? [desk('d1', 'r0', 'a1', 'Ann'), desk('d2', 'r0', 'a2', 'Bo')] : [])] }));
  const campus: Campus = { buildings: [cb('w1', { name: 'Home' }), cb('w2', { name: 'Other' })], totals: null, generated_at: '' };
  const presence = indexPresence([{ agentId: 'a1', name: 'Ann', role: 'Support', status: 'working', taskTitles: [], observedAt: '' }]);
  const out = expandCampus(campus, 'w1', registry, presence);
  assert.equal(out.buildings.length, 9);
  const keys = out.buildings.map((b) => b.workspace_id);
  assert.equal(new Set(keys).size, 9);
  assert.ok(keys.includes('w2'), 'the other workspace is untouched');
  assert.ok(out.buildings.find((b) => b.workspace_id === 'w2')?.registryBuildingId === undefined);
  const hq = out.buildings.find((b) => b.registryBuildingId === 'b0')!;
  assert.equal(hq.workspace_id, registryKey('w1', 'b0'));
  assert.equal(workspaceOf(hq), 'w1');
  assert.equal(hq.workspaceName, 'Home');
  assert.equal(hq.kindKnown, false);
  assert.equal(hq.totals?.agents, 2);
  assert.equal(hq.totals?.working, 1);
  assert.equal(hq.totals?.unknown, 1, 'an assigned agent without a presence record is unknown, not idle');
  assert.deepEqual(hq.people, ['A', 'B']);
  assert.equal(out.buildings.find((b) => b.registryBuildingId === 'b3')?.totals?.agents, 0);
  // Without presence the count is unavailable, not zero.
  assert.equal(expandCampus(campus, 'w1', registry, null).buildings.find((b) => b.registryBuildingId === 'b0')?.totals, null);
  // One registry building (or none) is the workspace building as before; an inaccessible workspace is never expanded.
  assert.equal(expandCampus(campus, 'w1', registry.slice(0, 1), presence), campus);
  assert.equal(expandCampus(campus, 'w1', [], presence), campus);
  assert.equal(expandCampus({ ...campus, buildings: [cb('w1', { accessible: false })] }, 'w1', registry, presence).buildings.length, 1);
});

test('a registry building resolves by name to the open workspace, another workspace is a switch', async () => {
  const { resolveNavTarget } = await import('../src/lib/hqExtensions.ts');
  const campus: Campus = { buildings: [cb('w1~b0', { name: 'ANUM HQ', realWorkspaceId: 'w1', registryBuildingId: 'b0' }), cb('w1~b1', { name: 'Tyre', realWorkspaceId: 'w1', registryBuildingId: 'b1' }), cb('w2', { name: 'Other' })], totals: null, generated_at: '' };
  const open = { workspaceId: 'w1', buildingId: 'b0', floors: [{ id: 'f0', level: 0, name: 'Ground', rooms: [{ id: 'r0', label: 'Lobby' }] }], agents: [] };
  assert.deepEqual(resolveNavTarget({ building: 'Tyre' }, campus, open), { kind: 'apply', workspaceId: 'w1', buildingId: 'b1', floorId: null, roomId: null, agentId: null });
  assert.deepEqual(resolveNavTarget({ building: 'ANUM HQ', room: 'Lobby' }, campus, open), { kind: 'apply', workspaceId: 'w1', buildingId: 'b0', floorId: 'f0', roomId: 'r0', agentId: null });
  assert.deepEqual(resolveNavTarget({ building: 'Other' }, campus, open), { kind: 'switch', workspaceId: 'w2' });
});
