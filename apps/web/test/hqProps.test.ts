import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MeshBuilder, VERTEX_FLOATS, hex } from '../src/lib/hqGeometry.ts';
import { WINDOW_BASE, anumMark, car, castShadow, flatPoly, parkingLot, truck } from '../src/lib/hqProps.ts';
import { seeded } from '../src/lib/hqGeometry.ts';
import { buildCampusScene } from '../src/lib/campusScene.ts';
import { buildBuildingScene } from '../src/lib/buildingScene.ts';
import { layoutCampus, type CampusBuilding } from '../src/lib/campusModel.ts';
import { lightingFor } from '../src/lib/hqLighting.ts';
import type { SceneFloor } from '../src/lib/headquartersProjection.ts';

const normals = (b: MeshBuilder): Array<[number, number, number]> => {
  const v = b.toFloat32();
  const out: Array<[number, number, number]> = [];
  for (let i = 0; i < v.length; i += VERTEX_FLOATS) out.push([v[i + 3], v[i + 4], v[i + 5]]);
  return out;
};
const bld = (id: string, kind: CampusBuilding['kind'] = 'business'): CampusBuilding => ({ workspace_id: id, tenant_id: 't', name: id, kind, icon: null, floors: 3, accessible: true, totals: null, presence_verified_at: null });

test('flat polygons face up in either winding order', () => {
  for (const pts of [[[0, 0], [0, 4], [3, 4], [3, 0]], [[3, 0], [3, 4], [0, 4], [0, 0]]] as const) {
    const b = new MeshBuilder();
    flatPoly(b, pts, 0.1, [1, 1, 1]);
    assert.equal(b.triangleCount, 2);
    assert.ok(normals(b).every((n) => n[1] > 0.99));
  }
});

test('a cast shadow lies on the ground and spreads away from the sun', () => {
  const b = new MeshBuilder();
  castShadow(b, 0, 0, 10, 10, 20);
  const box = b.bounds();
  assert.ok(box.min.y === box.max.y);
  assert.ok(box.min.x < 0 && box.min.z < 0, 'the shadow reaches towards -x and -z');
  assert.ok(box.max.x <= 10 && box.max.z <= 10);
  assert.ok(normals(b).every((n) => n[1] > 0.99));
});

test('the ANUM mark faces +z and cars and trucks keep their footprint for every quarter turn', () => {
  const m = new MeshBuilder();
  anumMark(m, 0, 0, 5, 2);
  assert.ok(normals(m).every((n) => n[2] > 0.99));
  for (let rot = 0; rot < 4; rot++) {
    const c = new MeshBuilder();
    car(c, 0, 0, hex('#ffffff'), rot);
    const t = new MeshBuilder();
    truck(t, 0, 0, hex('#ffffff'), rot);
    assert.ok(c.triangleCount > 100 && t.triangleCount > 100);
    const wide = rot % 2 === 1;
    for (const part of [c.bounds(), t.bounds()]) {
      const sx = part.max.x - part.min.x, sz = part.max.z - part.min.z;
      assert.ok(wide ? sx > sz : sz > sx, `rotation ${rot} runs along the ${wide ? 'x' : 'z'} axis`);
    }
  }
});

test('a car park draws bays and only some cars, deterministically', () => {
  const a = new MeshBuilder(), b = new MeshBuilder();
  parkingLot(a, 0, 0, 4, 2, seeded('lot'), 0.5);
  parkingLot(b, 0, 0, 4, 2, seeded('lot'), 0.5);
  assert.equal(a.triangleCount, b.triangleCount);
  const full = new MeshBuilder();
  parkingLot(full, 0, 0, 4, 2, seeded('lot'), 1);
  assert.ok(full.triangleCount > a.triangleCount);
});

test('campus buildings carry windows (emissive 2 or more) and an entrance sign anchor near their door', () => {
  const scene = buildCampusScene(layoutCampus([bld('exec', 'executive'), bld('a'), bld('b', 'department'), bld('c', 'personal')]));
  for (const item of scene.buildings) {
    const v = item.mesh.vertices;
    let windows = 0;
    for (let i = 9; i < v.length; i += VERTEX_FLOATS) if (v[i] >= WINDOW_BASE) windows += 1;
    assert.ok(windows > 60, `${item.workspaceId} has window panes (${windows})`);
    assert.ok(item.sign.y > 2 && item.sign.y < 12, 'the sign sits above the door');
    assert.ok(Math.abs(item.sign.x - item.door.x) < 0.01, 'the sign is over the door');
    assert.ok(item.sign.z < item.door.z, 'the sign is on the building side of the door apron');
  }
  // Scenery windows (the far skyline) use the same convention, and the scene never shows more than the data: no text or names in geometry.
  assert.ok(scene.scenery.triangles > 5000);
});

test('the same workspace id always gets the same finish, different ids can differ', () => {
  const one = buildCampusScene(layoutCampus([bld('alpha')]));
  const again = buildCampusScene(layoutCampus([bld('alpha')]));
  assert.deepEqual(Array.from(one.buildings[0].mesh.vertices.slice(0, 400)), Array.from(again.buildings[0].mesh.vertices.slice(0, 400)));
  const finishes = new Set(['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'].map((id) => buildCampusScene(layoutCampus([bld(id)])).buildings[0].mesh.vertices.reduce((sum, v) => sum + v, 0).toFixed(3)));
  assert.ok(finishes.size > 1);
});

const floor: SceneFloor = {
  id: 'f0', name: 'Ground', level: 0,
  rooms: (['office', 'meeting', 'reception', 'warehouse', 'workshop', 'lounge'] as const).map((kind, i) => ({ id: 'r' + i, floorId: 'f0', buildingId: 'b', label: kind, kind, x: (i % 3) * 8, z: Math.floor(i / 3) * 6, width: 6.5, depth: 5, height: 3.2, capacity: 4, revision: 1, deskIds: [], evidence: 'registry' as const })),
  desks: [{ id: 'd0', roomId: 'r0', label: 'Desk 1', position: { x: 3, y: 0.78, z: 2.5 }, agentId: 'a1', agent: null, assignmentVisible: true }],
  bounds: { width: 30, depth: 20 },
};

test('every room kind is furnished, and a person is drawn only at a desk with an assignment', () => {
  const empty = buildBuildingScene([floor], { activeIndex: 0 });
  const seated = buildBuildingScene([floor], { activeIndex: 0, people: new Map([['d0', 'a1']]) });
  assert.ok(seated.triangles > empty.triangles, 'a seated figure adds geometry');
  const bare = buildBuildingScene([{ ...floor, rooms: floor.rooms.map((r) => ({ ...r, kind: 'office' as const })) }], { activeIndex: 0 });
  assert.notEqual(bare.triangles, empty.triangles, 'room kinds change the furniture');
  assert.ok(empty.triangles > 8000, 'the interior and its landscaping are dense enough: ' + empty.triangles);
  assert.ok(empty.sign.y > 0 && empty.sign.z > empty.bounds.min.z && Number.isFinite(empty.sign.x));
  assert.ok(empty.opaque.vertices.every(Number.isFinite));
  assert.equal(buildBuildingScene([floor], { activeIndex: 0, people: new Map([['d0', 'a1']]) }).triangles, seated.triangles);
});

test('night is dim but never black: the ambient and moon light stay above a floor', () => {
  const night = lightingFor(1), day = lightingFor(0);
  assert.ok(night.sky.every((c) => c >= 0.25));
  assert.ok(night.ground.every((c) => c >= 0.14));
  assert.ok(day.sky[0] > night.sky[0]);
});
