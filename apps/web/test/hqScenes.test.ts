import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MeshBuilder, VERTEX_FLOATS, seeded } from '../src/lib/hqGeometry.ts';
import { buildCampusScene } from '../src/lib/campusScene.ts';
import { buildBuildingScene, frameBuilding } from '../src/lib/buildingScene.ts';
import { layoutCampus, type CampusBuilding } from '../src/lib/campusModel.ts';
import type { SceneFloor } from '../src/lib/headquartersProjection.ts';

const bld = (id: string, kind: CampusBuilding['kind'] = 'business', floors = 3): CampusBuilding => ({ workspace_id: id, tenant_id: 't', name: id, kind, icon: null, floors, accessible: true, totals: null, presence_verified_at: null });

test('box normals face outward and every float is finite', () => {
  const b = new MeshBuilder();
  b.box(0, 0, 0, 2, 2, 2, [1, 1, 1]);
  assert.equal(b.triangleCount, 12);
  const v = b.toFloat32();
  assert.equal(v.length, 36 * VERTEX_FLOATS);
  for (let i = 0; i < v.length; i += VERTEX_FLOATS) {
    const out = (v[i] - 1) * v[i + 3] + (v[i + 1] - 1) * v[i + 4] + (v[i + 2] - 1) * v[i + 5];
    assert.ok(out > 0, 'normal points into the box at vertex ' + i / VERTEX_FLOATS);
  }
  assert.ok(v.every(Number.isFinite));
});

test('skipped faces are not emitted and bounds follow the geometry', () => {
  const b = new MeshBuilder();
  b.box(1, 2, 3, 4, 5, 6, [1, 1, 1], { skip: ['top', 'bottom'] });
  assert.equal(b.triangleCount, 8);
  assert.deepEqual(b.bounds(), { min: { x: 1, y: 2, z: 3 }, max: { x: 5, y: 7, z: 9 } });
});

test('seeded random is deterministic', () => {
  const a = seeded('x'), b = seeded('x'), c = seeded('y');
  assert.equal(a(), b());
  assert.notEqual(a(), c());
});

test('campus scene has one mesh per building, finite data and a sane triangle budget', () => {
  const list = [bld('exec', 'executive'), ...Array.from({ length: 9 }, (_, i) => bld('w' + i))];
  const scene = buildCampusScene(layoutCampus(list));
  assert.equal(scene.buildings.length, 10);
  assert.ok(scene.triangles > 2000 && scene.triangles < 250000, 'triangles ' + scene.triangles);
  for (const item of [scene.scenery, ...scene.buildings.map((x) => x.mesh)]) assert.ok(item.vertices.every(Number.isFinite));
  const exec = scene.buildings.find((x) => x.workspaceId === 'exec')!;
  const other = scene.buildings.find((x) => x.workspaceId === 'w0')!;
  assert.ok(exec.box.max.y > other.box.max.y, 'the executive tower is the tallest');
  assert.deepEqual(buildCampusScene(layoutCampus(list)).triangles, scene.triangles);
});

test('an empty campus still builds a ground and a monument', () => {
  const scene = buildCampusScene([]);
  assert.equal(scene.buildings.length, 0);
  assert.ok(scene.scenery.triangles > 50);
});

const floor = (level: number, rooms: number): SceneFloor => ({
  id: 'f' + level, name: 'Floor ' + level, level,
  rooms: Array.from({ length: rooms }, (_, i) => ({ id: `r${level}-${i}`, floorId: 'f' + level, buildingId: 'b', label: 'Room ' + i, kind: (['office', 'meeting', 'reception', 'warehouse', 'workshop', 'lounge'] as const)[i % 6], x: i * 7, z: 0, width: 6, depth: 4.5, height: 3.2, capacity: 4, revision: 1, deskIds: [], evidence: 'registry' as const })),
  desks: [{ id: `d${level}`, roomId: `r${level}-0`, label: 'Desk 1', position: { x: 3, y: 0.78, z: 2 }, agentId: 'a1', agent: null, assignmentVisible: true }],
  bounds: { width: 50, depth: 20 },
});

test('building cutaway draws rooms from the saved geometry and only floors up to the active one', () => {
  const floors = [floor(0, 6), floor(1, 3), floor(2, 2)];
  const mid = buildBuildingScene(floors, { activeIndex: 1 });
  assert.equal(mid.rooms.length, 3);
  assert.ok(mid.rooms.every((r) => r.floorIndex === 1));
  assert.equal(mid.desks.length, 1);
  const all = buildBuildingScene(floors, { activeIndex: 1, showAll: true });
  assert.ok(all.bounds.max.y > mid.bounds.max.y);
  assert.ok(all.triangles > mid.triangles);
  const handle = mid.rooms[0];
  assert.equal(handle.id, 'r1-0');
  assert.ok(handle.box.min.x === 0 && handle.box.max.x === 6);
  const frame = frameBuilding(mid, 1);
  assert.ok(frame.distance >= 18 && Number.isFinite(frame.target.x));
});

test('desk state changes the monitor colour, selection changes the room tile', () => {
  const floors = [floor(0, 2)];
  const base = buildBuildingScene(floors, { activeIndex: 0 });
  const working = buildBuildingScene(floors, { activeIndex: 0, deskState: new Map([['d0', 'working']]) });
  const picked = buildBuildingScene(floors, { activeIndex: 0, selectedRoomId: 'r0-1' });
  assert.notDeepEqual(Array.from(base.opaque.vertices), Array.from(working.opaque.vertices));
  assert.notDeepEqual(Array.from(base.opaque.vertices), Array.from(picked.opaque.vertices));
});

test('a floor with no rooms still builds a base and no handles', () => {
  const scene = buildBuildingScene([{ id: 'f', name: 'F', level: 0, rooms: [], desks: [], bounds: { width: 20, depth: 16 } }], { activeIndex: 0 });
  assert.equal(scene.rooms.length, 0);
  assert.ok(scene.opaque.triangles > 0);
  assert.equal(buildBuildingScene([], { activeIndex: 0 }).rooms.length, 0);
});
