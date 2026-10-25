import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MeshBuilder, VERTEX_FLOATS, hex } from '../src/lib/hqGeometry.ts';
import * as E from '../src/hqAssets/environment/index.ts';

const floats = (b: MeshBuilder): Float32Array => b.toFloat32();
const normalsY = (b: MeshBuilder): number[] => {
  const v = floats(b);
  const out: number[] = [];
  for (let i = 0; i < v.length; i += VERTEX_FLOATS) out.push(v[i + 4]);
  return out;
};

test('every asset stays inside its triangle budget', () => {
  const rows = E.measureEnvironmentKit();
  assert.ok(rows.length > 50);
  for (const r of rows) {
    assert.ok(r.triangles > 0, `${r.name} draws something`);
    assert.ok(r.triangles <= r.cap, `${r.name}: ${r.triangles} triangles, cap ${r.cap}`);
  }
});

test('builders are deterministic for a seed and differ between seeds', () => {
  const run = (seed: string) => {
    const b = new MeshBuilder();
    E.carPark(b, 0, 0, { cols: 6, seed });
    E.pond(b, 30, 0, 6, { seed });
    E.treeGrove(b, 0, 40, 30, 30, 10, { seed });
    E.skylineBackdrop(b, { cx: 0, cz: 0, radius: 200, count: 8, seed });
    return Array.from(floats(b));
  };
  assert.deepEqual(run('a'), run('a'));
  assert.notDeepEqual(run('a'), run('b'));
});

test('all vertex data is finite and every triangle has a unit normal', () => {
  const b = new MeshBuilder();
  E.campusEnvironment(b, { cx: 0, cz: 0, halfW: 80, halfD: 60, seed: 'finite', density: 0.5 });
  E.tennisCourt(b, 0, 0); E.footballPitch(b, 0, 80); E.helipad(b, 80, 0); E.fountain(b, -60, 0);
  E.bus(b, 0, 0); E.truck(b, 20, 0, { kind: 'semi' }); E.van(b, 40, 0);
  const v = floats(b);
  for (let i = 0; i < v.length; i += VERTEX_FLOATS) {
    for (let k = 0; k < VERTEX_FLOATS; k++) assert.ok(Number.isFinite(v[i + k]), `finite component ${k}`);
    assert.ok(Math.abs(Math.hypot(v[i + 3], v[i + 4], v[i + 5]) - 1) < 1e-4, 'unit normal');
  }
});

test('cars have a plausible size, turn with their heading and light their lamps', () => {
  for (const kind of ['sedan', 'hatch', 'suv', 'coupe', 'pickup'] as const) {
    const b = new MeshBuilder();
    const r = E.car(b, 5, 7, { kind, color: hex('#8a2f3a') });
    const size = { x: r.bounds.max.x - r.bounds.min.x, y: r.bounds.max.y, z: r.bounds.max.z - r.bounds.min.z };
    assert.ok(size.z > 3.6 && size.z < 5.8, `${kind} length ${size.z}`);
    assert.ok(size.x > 1.6 && size.x < 2.5, `${kind} width ${size.x}`);
    assert.ok(size.y > 1.2 && size.y < 2.0, `${kind} height ${size.y}`);
    assert.ok(r.glow.length >= 2, 'head lamps glow at night');
    const turned = new MeshBuilder();
    const t = E.car(turned, 5, 7, { kind, rot: Math.PI / 2 });
    assert.ok(t.bounds.max.x - t.bounds.min.x > t.bounds.max.z - t.bounds.min.z, 'a quarter turn runs the car along x');
  }
  const low = new MeshBuilder(), full = new MeshBuilder();
  assert.ok(E.car(low, 0, 0, { kind: 'sedan', detail: 'low' }).triangles < E.car(full, 0, 0, { kind: 'sedan' }).triangles * 0.7);
});

test('vans, trucks and the bus are larger than a car and sit on the ground', () => {
  const sizes = (r: E.AssetResult) => ({ len: Math.max(r.bounds.max.x - r.bounds.min.x, r.bounds.max.z - r.bounds.min.z), min: r.bounds.min.y });
  const b = new MeshBuilder();
  assert.ok(sizes(E.van(b, 0, 0)).len > 4.8);
  assert.ok(sizes(E.truck(b, 0, 0, { kind: 'box' })).len > 8.5);
  assert.ok(sizes(E.truck(b, 0, 0, { kind: 'semi' })).len > 12);
  assert.ok(sizes(E.bus(b, 0, 0)).len > 11.5);
  for (const r of [E.bus(b, 0, 0), E.truck(b, 0, 0, { kind: 'tanker' })]) assert.ok(r.bounds.min.y >= -0.01, 'nothing below the ground');
});

test('flat ground assets never face down', () => {
  for (const make of [
    (b: MeshBuilder) => E.ringRoad(b, { cx: 0, cz: 0, halfW: 40, halfD: 30, junctionEdges: [1] }),
    (b: MeshBuilder) => E.walkway(b, [[0, 0], [30, 0], [40, 10]]),
    (b: MeshBuilder) => E.canal(b, [[0, 0], [30, 5], [60, 0]]),
    (b: MeshBuilder) => E.pond(b, 0, 0, 6, { rocks: 0, reeds: 0, lilies: 0 }),
    (b: MeshBuilder) => E.crosswalk(b, 0, 0),
  ]) {
    const b = new MeshBuilder();
    make(b);
    assert.ok(normalsY(b).every((n) => n > -0.2), 'no triangle looks at the ground');
  }
});

test('trees come from cached templates and instancing matches stamping', () => {
  assert.equal(E.treeTemplate('oak', 1), E.treeTemplate('oak', 1));
  assert.notEqual(E.treeTemplate('oak', 1), E.treeTemplate('oak', 2));
  const t = E.treeTemplate('datePalm', 0);
  const b = new MeshBuilder();
  E.stamp(b, t, { x: 10, z: -4, rot: 0, scale: 1 });
  assert.equal(b.triangleCount, t.triangles);
  const raw = new MeshBuilder();
  E.stamp(raw, t, { x: 0, z: 0 });
  assert.ok(Math.abs(b.bounds().min.x - raw.bounds().min.x - 10) < 1e-3 && Math.abs(b.bounds().min.z - raw.bounds().min.z + 4) < 1e-3);
  const scaled = new MeshBuilder();
  E.stamp(scaled, t, { x: 0, z: 0, scale: 2 });
  assert.ok(Math.abs(scaled.bounds().max.y - raw.bounds().max.y * 2) < 1e-3);
  const line = new MeshBuilder();
  const res = E.treeLine(line, [[0, 0], [100, 0]], { spacing: 10, species: ['datePalm', 'oak'], seed: 'line' });
  const placed = res.instances.reduce((n, s) => n + s.placements.length, 0);
  assert.equal(placed, 10);
  assert.ok(res.instances.every((s) => s.template.triangles <= E.TREE_TRIANGLES_CAP));
  const capped = new MeshBuilder();
  const few = E.treeLine(capped, [[0, 0], [100, 0]], { spacing: 10, maxTrees: 3 });
  assert.equal(few.instances.reduce((n, s) => n + s.placements.length, 0), 3);
});

test('a grove keeps its minimum gap and stays inside its rectangle', () => {
  const b = new MeshBuilder();
  const r = E.treeGrove(b, 10, 20, 40, 30, 12, { minGap: 4, seed: 'g' });
  const spots = r.instances.flatMap((s) => s.placements);
  assert.ok(spots.length > 6);
  for (const p of spots) assert.ok(p.x >= 10 && p.x <= 50 && p.z >= 20 && p.z <= 50);
  for (let i = 0; i < spots.length; i++) for (let j = i + 1; j < spots.length; j++) assert.ok(Math.hypot(spots[i].x - spots[j].x, spots[i].z - spots[j].z) >= 4 - 1e-9);
});

test('lamps return glow points above the ground and lamp lines follow the path', () => {
  const b = new MeshBuilder();
  for (const kind of ['street', 'double', 'garden', 'bollard'] as const) {
    const r = E.lamp(b, 0, 0, { kind });
    assert.ok(r.glow.length >= (kind === 'double' ? 2 : 1));
    assert.ok(r.glow.every((g) => g.y > 0.4 && g.radius > 0 && g.intensity > 0));
  }
  const line = E.lampLine(new MeshBuilder(), [[0, 0], [120, 0]], { spacing: 30, offset: 3 });
  assert.equal(line.glow.length, 4);
  assert.ok(line.glow.every((g) => g.z > 0));
});

test('the car park draws bays, fills by ratio and respects an empty lot', () => {
  const empty = E.carPark(new MeshBuilder(), 0, 0, { cols: 8, fill: 0, seed: 'e' });
  const busy = E.carPark(new MeshBuilder(), 0, 0, { cols: 8, fill: 1, seed: 'e' });
  assert.ok(empty.triangles > 100 && empty.triangles < 1200, 'bays and lamps only');
  assert.ok(busy.triangles > empty.triangles + 16 * 300 * 0.8, 'sixteen cars when full');
  const size = E.carParkSize(8);
  assert.ok(busy.bounds.max.x - busy.bounds.min.x >= size.w - 0.7);
});

test('the campus composition fits its budget, keeps anchors, respects exclusions and the density option', () => {
  const base = { cx: 0, cz: 0, halfW: 110, halfD: 80, seed: 'env' };
  const full = E.campusEnvironment(new MeshBuilder(), base);
  assert.ok(full.triangles <= E.CAMPUS_ENVIRONMENT_TRIANGLES_CAP);
  assert.ok(full.glow.length > 15, 'lamps, gate house and bridges give night lights');
  assert.ok(Math.abs(full.anchors.southBridge.z - (80 + E.CANAL_OFFSET)) < 1e-9);
  assert.ok(full.anchors.gate.z > full.anchors.southBridge.z);
  const sparse = E.campusEnvironment(new MeshBuilder(), { ...base, density: 0.4 });
  assert.ok(sparse.triangles < full.triangles * 0.75);
  const blocked = E.campusEnvironment(new MeshBuilder(), { ...base, exclusions: [{ x: -200, z: -200, w: 400, d: 400 }] });
  assert.ok(blocked.triangles < full.triangles, 'no benches or beds inside an excluded area');
  const noSky = E.campusEnvironment(new MeshBuilder(), { ...base, skyline: false });
  assert.ok(noSky.triangles < full.triangles - 1500);
});

test('skyline towers face the centre and hold their budget for every shape', () => {
  for (const shape of ['slab', 'setback', 'tapered', 'twin', 'round', 'spire'] as const) {
    const b = new MeshBuilder();
    const r = E.skylineTower(b, 0, 0, { shape, h: 100, seed: shape });
    assert.ok(r.triangles <= E.TOWER_TRIANGLES_CAP, `${shape}: ${r.triangles}`);
    assert.ok(r.bounds.max.y >= 95);
  }
  const b = new MeshBuilder();
  const r = E.skylineBackdrop(b, { cx: 0, cz: 0, radius: 200, count: 12, fromAngle: Math.PI, toAngle: 2 * Math.PI });
  assert.ok(r.bounds.min.z < -150 && r.bounds.max.z <= 40, 'towers stand on the chosen northern arc');
});

test('water and sports pieces report night lights and sit near the ground plane', () => {
  const b = new MeshBuilder();
  assert.ok(E.fountain(b, 0, 0).glow.length >= 4);
  assert.ok(E.helipad(b, 0, 0).glow.length >= 8);
  assert.ok(E.tennisCourt(b, 0, 0).glow.length === 4);
  assert.ok(E.footballPitch(b, 0, 0).glow.length === 4);
  const canal = E.canal(new MeshBuilder(), [[0, 0], [40, 0]], { width: 8 });
  assert.ok(canal.bounds.max.y < 0.5 && canal.bounds.min.y >= 0);
  const high = E.helipad(new MeshBuilder(), 0, 0, { y: 30 });
  assert.ok(high.bounds.min.y >= 29.99, 'a roof helipad lifts with y');
});

test('no builder in the kit is named for people, crowds or text', () => {
  const banned = /person|people|pedestrian|crowd|human|figure|avatar|label|sign(?!al)/i;
  for (const name of Object.keys(E)) assert.ok(!banned.test(name), `export ${name}`);
});
