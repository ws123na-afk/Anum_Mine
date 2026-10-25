import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MAT, MeshBuilder, VERTEX_FLOATS, hex, seeded } from '../src/lib/hqGeometry.ts';
import { WINDOW_BASE, anumMark, canalPiece, car, flatPoly, orientedBox, parkingLot, tennisCourt, truck } from '../src/lib/hqProps.ts';
import { buildCampusScene } from '../src/lib/campusScene.ts';
import { archetypeOf } from '../src/lib/campusBuildings.ts';
import { buildBuildingScene } from '../src/lib/buildingScene.ts';
import { layoutCampus, type CampusBuilding } from '../src/lib/campusModel.ts';
import { lightingFor } from '../src/lib/hqLighting.ts';
import { drawMaterialTexture, drawSignAtlas, signAtlasLayout } from '../src/lib/hqTextures.ts';
import type { SceneFloor } from '../src/lib/headquartersProjection.ts';

const normals = (b: MeshBuilder): Array<[number, number, number]> => {
  const v = b.toFloat32();
  const out: Array<[number, number, number]> = [];
  for (let i = 0; i < v.length; i += VERTEX_FLOATS) out.push([v[i + 3], v[i + 4], v[i + 5]]);
  return out;
};
const bld = (id: string, kind: CampusBuilding['kind'] = 'business', icon: string | null = null): CampusBuilding => ({ workspace_id: id, tenant_id: 't', name: id, kind, icon, floors: 3, accessible: true, totals: null, presence_verified_at: null });

test('flat polygons face up in either winding order', () => {
  for (const pts of [[[0, 0], [0, 4], [3, 4], [3, 0]], [[3, 0], [3, 4], [0, 4], [0, 0]]] as const) {
    const b = new MeshBuilder();
    flatPoly(b, pts, 0.1, [1, 1, 1]);
    assert.equal(b.triangleCount, 2);
    assert.ok(normals(b).every((n) => n[1] > 0.99));
  }
});

test('the vertex layout carries a surface class and texture coordinates', () => {
  assert.equal(VERTEX_FLOATS, 13);
  const b = new MeshBuilder();
  b.withMat(MAT.grass, () => b.plane(0, 0, 0, 2, 2, [1, 1, 1]));
  b.quadUV([0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0.1, 0.2], [0.3, 0.2], [0.3, 0.4], [0.1, 0.4], [1, 1, 1]);
  const v = b.toFloat32();
  assert.equal(v[10], MAT.grass);
  assert.equal(b.mat, MAT.flat, 'withMat restores the previous class');
  const last = v.length - VERTEX_FLOATS;
  assert.ok(Math.abs(v[last + 11] - 0.1) < 1e-6);
  assert.ok(Math.abs(v[last + 12] - 0.4) < 1e-6);
});

test('oriented boxes turn to any heading and keep their volume', () => {
  const b = new MeshBuilder();
  orientedBox(b, 5, 0, 5, 1, 1, 10, 2, 1, [1, 1, 1]);
  const box = b.bounds();
  assert.ok(box.max.y === 1 && box.min.y === 0);
  assert.ok(box.max.x - box.min.x > 7 && box.max.z - box.min.z > 7, 'a diagonal box spreads over both axes');
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
    assert.ok(c.triangleCount > 150 && t.triangleCount > 100, 'a car has a shaped body, glass, lamps and wheels');
    const wide = rot % 2 === 1;
    for (const part of [c.bounds(), t.bounds()]) {
      const sx = part.max.x - part.min.x, sz = part.max.z - part.min.z;
      assert.ok(wide ? sx > sz : sz > sx, `rotation ${rot} runs along the ${wide ? 'x' : 'z'} axis`);
    }
  }
  const small = new MeshBuilder(), full = new MeshBuilder();
  car(small, 0, 0, hex('#ffffff'), 0, 0.6);
  car(full, 0, 0, hex('#ffffff'), 0, 1);
  assert.ok(small.bounds().max.z < full.bounds().max.z, 'a scaled car is smaller');
});

test('car windows are marked as glass and the body as paint', () => {
  const c = new MeshBuilder();
  car(c, 0, 0, hex('#8a2f3a'), 0);
  const v = c.toFloat32();
  const paint = new Set<number>(), windows = new Set<number>();
  for (let i = 0; i < v.length; i += VERTEX_FLOATS) { paint.add(v[i + 10]); if (v[i + 9] >= WINDOW_BASE) windows.add(i); }
  assert.ok(paint.has(MAT.paint));
  assert.ok(windows.size >= 6);
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

test('a tennis court and a canal piece are drawn on the ground with the right surface classes', () => {
  const court = new MeshBuilder();
  tennisCourt(court, 0, 0, 0);
  const box = court.bounds();
  assert.ok(box.max.x - box.min.x > 28 && box.max.z - box.min.z > 14, 'court plus its run-off and fence');
  assert.ok(box.max.y > 2.5, 'the fence stands 3 m tall');
  const water = new MeshBuilder();
  canalPiece(water, 0, 0, 10, 0, 4);
  const v = water.toFloat32();
  const classes = new Set<number>();
  for (let i = 10; i < v.length; i += VERTEX_FLOATS) classes.add(v[i]);
  assert.ok(classes.has(MAT.water) && classes.has(MAT.tile));
});

test('building types follow the owner icon, then the kind, then a stable seed', () => {
  const p = (kind: CampusBuilding['kind'], icon: string | null, id = 'x') => archetypeOf({ kind, icon, workspaceId: id });
  assert.equal(p('executive', 'truck'), 'executive');
  assert.equal(p('personal', 'factory'), 'villa');
  assert.equal(p('business', 'truck'), 'warehouse');
  assert.equal(p('department', 'factory'), 'warehouse');
  assert.equal(p('business', 'chart'), 'tower');
  assert.equal(p('department', 'briefcase'), 'tower');
  assert.equal(p('business', 'shield'), 'admin');
  assert.equal(p('business', null, 'same'), p('business', null, 'same'));
  const seen = new Set(Array.from({ length: 40 }, (_, i) => p('department', null, 'ws' + i)));
  assert.ok(seen.has('workshop') && seen.has('warehouse'), 'unset department buildings vary');
});

test('the sign atlas packs every cell inside the texture without overlap', () => {
  for (const count of [1, 2, 3, 7, 14, 40]) {
    const layout = signAtlasLayout(count);
    assert.ok(Number.isInteger(Math.log2(layout.height)), 'the height is a power of two');
    const cells = Array.from({ length: count }, (_, i) => layout.cell(i));
    for (const c of cells) assert.ok(c.u0 >= 0 && c.u1 <= 1 && c.v0 >= 0 && c.v1 <= 1 && c.u1 > c.u0 && c.v1 > c.v0);
    for (let i = 0; i < count; i++) for (let j = i + 1; j < count; j++) {
      const a = cells[i], b = cells[j];
      const overlap = a.u0 < b.u1 && b.u0 < a.u1 && a.v0 < b.v1 && b.v0 < a.v1;
      assert.ok(!overlap, `cells ${i} and ${j} overlap`);
    }
  }
});

test('texture drawing needs a browser and says so by returning null instead of throwing', () => {
  assert.equal(drawMaterialTexture('grass'), null);
  assert.equal(drawSignAtlas([{ title: 'ANUM MINE', subtitle: 'Example' }]), null);
});

test('campus buildings carry windows, a sign cell, a type, and the scene a canal and a shadow region', () => {
  const scene = buildCampusScene(layoutCampus([bld('exec', 'executive'), bld('a'), bld('b', 'department'), bld('c', 'personal'), bld('d', 'business', 'truck'), bld('e', 'business', 'chart')]));
  assert.equal(scene.signCount, scene.buildings.length + 1, 'one cell per building and one for the gate');
  const types = new Set(scene.buildings.map((b) => b.archetype));
  assert.ok(types.has('executive') && types.has('villa') && types.has('warehouse') && types.has('tower'));
  scene.buildings.forEach((item, index) => {
    assert.equal(item.signIndex, index);
    const v = item.mesh.vertices;
    let windows = 0, signs = 0;
    for (let i = 9; i < v.length; i += VERTEX_FLOATS) {
      if (v[i] >= WINDOW_BASE) windows += 1;
      if (v[i + 1] === MAT.sign) signs += 1;
    }
    assert.ok(windows > 60, `${item.workspaceId} has window panes (${windows})`);
    assert.ok(signs >= 6, `${item.workspaceId} has a lettered sign board`);
  });
  assert.ok(scene.canalRadius > 20, 'a canal ring runs between the buildings and the road');
  assert.ok(scene.shadow.radius >= scene.extent.radius, 'the shadow region covers the buildings');
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
  assert.equal(empty.signCount, 1);
  assert.ok(empty.shadow.radius > 20 && Number.isFinite(empty.shadow.center.x));
  assert.ok(empty.opaque.vertices.every(Number.isFinite));
  assert.equal(buildBuildingScene([floor], { activeIndex: 0, people: new Map([['d0', 'a1']]) }).triangles, seated.triangles);
});

test('night is dim but never black: the ambient and moon light stay above a floor', () => {
  const night = lightingFor(1), day = lightingFor(0);
  assert.ok(night.sky.every((c) => c >= 0.25));
  assert.ok(night.ground.every((c) => c >= 0.14));
  assert.ok(day.sky[0] > night.sky[0]);
  assert.ok(day.sunIntensity > night.sunIntensity * 3, 'the sun is much stronger than the moon');
  assert.ok(night.sunIntensity > 0.3 && night.hemiIntensity > 0.3, 'the night scene keeps a readable light');
  assert.ok(night.exposure >= 1);
});
