// Every building stands on the ground (docs/ANUM_MINE_HQ_CUTAWAY.md#structure): a podium from the site to the ground floor, entrance
// steps, every upper slab carried by columns and the core whose foot is on the slab below, outer walls up to the slab above, and the
// site (lawn, paving, car park) flush with the foot of the podium. Checked for every starter campus building, Green Concrete,
// synthetic stacks, every view (stacked cutaway, single floor, closed) and the campus exteriors and plan shells.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, test } from 'node:test';
import { FLOOR_PITCH, SLAB, buildBuildingScene, type BuildingScene } from '../src/lib/buildingScene.ts';
import { COLUMN, PLINTH, columnsOf, ringOf, slabPlans, type Rect } from '../src/lib/buildingStructure.ts';
import { projectHeadquarters, type Registry, type RoomGeometry, type SceneFloor } from '../src/lib/headquartersProjection.ts';
import { VERTEX_FLOATS } from '../src/lib/hqGeometry.ts';
import { EXTERIOR_BUILDERS } from '../src/hqAssets/exterior/index.ts';
import { buildPlanShell } from '../src/hqAssets/exterior/planShell.ts';
import { planFrame } from '../src/lib/hqPlanFrame.ts';
import { EXTERIOR_OF } from '../src/lib/campusExteriors.ts';

const rev = { revision: 1 };
const load = (name: string) => JSON.parse(readFileSync(new URL(`./fixtures/${name}`, import.meta.url), 'utf8'));
const starter = load('starterCampus.json');
const green = load('greenConcrete.json');
const scope = { tenantId: 't', workspaceId: 'w' };
const campus = projectHeadquarters(scope, {
  buildings: starter.buildings.map((b: object) => ({ ...b, ...rev, timezone: 'Asia/Dubai' })), floors: starter.floors.map((f: object) => ({ ...f, ...rev })),
  rooms: starter.rooms.map((r: object) => ({ ...r, ...rev })), desks: starter.desks.map((d: object) => ({ ...d, ...rev })),
} as unknown as Registry, []).buildings;
const greenConcrete = projectHeadquarters(scope, {
  buildings: [{ ...green.building, ...rev }], floors: [{ ...green.floor, ...rev }], rooms: [{ ...green.room, ...rev }], desks: green.desks.map((d: object) => ({ ...d, ...rev })),
} as unknown as Registry, []).buildings;

const room = (id: string, x: number, z: number, w: number, d: number, kind: RoomGeometry['kind'] = 'office'): RoomGeometry => ({
  id, floorId: 'f', buildingId: 'b', label: id, kind, x, z, width: w, depth: d, height: 3, capacity: 4, revision: 1, deskIds: [], evidence: 'registry',
});
const floor = (i: number, rooms: RoomGeometry[]): SceneFloor => ({ id: `s${i}`, name: `Floor ${i}`, level: i, bounds: { width: 40, depth: 30 }, rooms, desks: [] });
// Synthetic stacks: an upper floor larger than the one under it (an overhang the structure must carry), and a one room floor
// (no core of its own) between floors that have one.
const overhang = [floor(0, [room('a0', 0, 0, 8, 6), room('b0', 9, 0, 6, 6)]), floor(1, [room('a1', 0, 0, 14, 8), room('b1', 15, 0, 10, 12)]), floor(2, [room('a2', 0, 0, 10, 6), room('b2', 11, 0, 6, 6)])];
const gap = [floor(0, [room('a0', 0, 0, 10, 8), room('b0', 12, 0, 8, 8)]), floor(1, [room('a1', 0, 0, 10, 8)]), floor(2, [room('a2', 0, 0, 10, 8), room('b2', 12, 0, 8, 8)])];

const buildings: Array<[string, readonly SceneFloor[]]> = [
  ...campus.map((b) => [b.name, b.floors] as [string, readonly SceneFloor[]]),
  ...greenConcrete.map((b) => [`Green Concrete: ${b.name}`, b.floors] as [string, readonly SceneFloor[]]),
  ['overhanging stack', overhang], ['stack with a one room floor', gap],
];

const near = (a: number, b: number, eps = 1e-6) => Math.abs(a - b) <= eps;
const within = (inner: Rect, outer: Rect, eps = 1e-6) => inner.x0 >= outer.x0 - eps && inner.z0 >= outer.z0 - eps && inner.x1 <= outer.x1 + eps && inner.z1 <= outer.z1 + eps;
/** Every vertex array the stage draws for this scene (the building, the walls, the shells of the other floors). */
const meshes = (s: BuildingScene) => [s.opaque.vertices, ...s.walls.map((w) => w.opaque.vertices), ...s.ghosts.map((g) => g.opaque.vertices)];
const hasGeometryAt = (s: BuildingScene, x: number, y: number, z: number, r: number) =>
  meshes(s).some((v) => { for (let i = 0; i < v.length; i += VERTEX_FLOATS) if (Math.abs(v[i] - x) <= r && Math.abs(v[i + 1] - y) <= r && Math.abs(v[i + 2] - z) <= r) return true; return false; });

function assertGrounded(label: string, s: BuildingScene, floorsDrawn: number): void {
  const { ground, plinth, steps, slabs, columns, cores } = s.structure;
  assert.equal(ground, -PLINTH, `${label}: the site is at the foot of the podium`);
  // The podium meets the ground and the ground floor sits on it with no gap.
  assert.equal(plinth.y0, ground, `${label}: podium on the ground`);
  const g0 = slabs.find((sl) => sl.floorIndex === 0)!;
  assert.ok(g0, `${label}: ground floor slab`);
  assert.equal(g0.y1, 0, `${label}: ground floor level`);
  assert.ok(plinth.y1 >= g0.y0 && plinth.y1 <= g0.y1, `${label}: the ground floor slab sits in the top of the podium`);
  assert.ok(within(g0, plinth), `${label}: the podium is under the whole ground floor`);
  // Entrance steps from the podium front down to the ground.
  assert.equal(steps.y0, ground);
  assert.ok(near(steps.z0, plinth.z1) && steps.x0 > plinth.x0 && steps.x1 < plinth.x1, `${label}: steps at the front of the podium`);
  assert.ok(hasGeometryAt(s, steps.x0, ground + PLINTH / 4, steps.z1, 0.05), `${label}: the lowest step is drawn`);
  // Every floor drawn has its slab; each upper slab lies on the one below and is carried at all four corners.
  assert.equal(slabs.length, floorsDrawn, `${label}: slabs`);
  for (const sl of slabs) {
    assert.ok(near(sl.y1, sl.floorIndex * FLOOR_PITCH) && near(sl.y0, sl.y1 - SLAB), `${label}: slab ${sl.floorIndex} level`);
    if (sl.floorIndex === 0) continue;
    const below = slabs.find((b) => b.floorIndex === sl.floorIndex - 1)!;
    assert.ok(within(sl, below), `${label}: slab ${sl.floorIndex} does not overhang the floor under it`);
    const carriers = columns.filter((c) => c.floorIndex === sl.floorIndex - 1);
    for (const [x, z] of [[sl.x0, sl.z0], [sl.x1, sl.z0], [sl.x0, sl.z1], [sl.x1, sl.z1]] as const) {
      const c = carriers.find((k) => Math.abs(k.x - x) <= COLUMN && Math.abs(k.z - z) <= COLUMN);
      assert.ok(c, `${label}: corner ${x},${z} of slab ${sl.floorIndex} stands on a column`);
      assert.ok(near(c.y0, below.y1) && near(c.y1, sl.y0), `${label}: that column runs from floor ${below.floorIndex} to the underside of slab ${sl.floorIndex}`);
    }
  }
  // Columns: on their slab, up to the slab above (or the roof), drawn in the mesh at mid height.
  for (const c of columns) {
    const own = slabs.find((b) => b.floorIndex === c.floorIndex)!;
    assert.ok(c.x >= own.x0 && c.x <= own.x1 && c.z >= own.z0 && c.z <= own.z1, `${label}: column on its slab`);
    assert.ok(near(c.y0, own.y1) && near(c.y1, own.y1 + FLOOR_PITCH - SLAB), `${label}: column floor to slab`);
  }
  const sample = columns.filter((_, i) => i % 5 === 0);
  for (const c of sample) assert.ok(hasGeometryAt(s, c.x + COLUMN / 2, (c.y0 + c.y1) / 2, c.z, 2), `${label}: column at ${c.x},${c.z} is drawn`);
  // The core runs through every floor from the lowest one under a floor that has a core, floor to slab above, with no gap.
  const coreFloors = cores.map((c) => c.floorIndex).sort((a, b) => a - b);
  coreFloors.forEach((fi, i) => { if (i > 0) assert.equal(fi, coreFloors[i - 1] + 1, `${label}: the core has no missing floor`); });
  for (const c of cores) assert.ok(near(c.y1 - c.y0, FLOOR_PITCH - SLAB), `${label}: core reaches the slab above`);
  // Outer walls reach the slab above (no open band of sky between floors).
  for (const w of s.walls) if (w.side !== 'roof') assert.ok(near(w.opaque.bounds.max.y, w.y + FLOOR_PITCH - SLAB, 1e-4), `${label}: ${w.side} wall of floor ${w.floorIndex} reaches the slab above`);
}

/** Upward flat triangles: their height, area and middle. */
function flats(v: Float32Array) {
  const out: { y: number; area: number; x: number; z: number }[] = [];
  for (let o = 0; o + 3 * VERTEX_FLOATS <= v.length; o += 3 * VERTEX_FLOATS) {
    if (v[o + 4] < 0.97) continue;
    const y = v[o + 1];
    if (!near(v[o + VERTEX_FLOATS + 1], y, 0.005) || !near(v[o + 2 * VERTEX_FLOATS + 1], y, 0.005)) continue;
    const ax = v[o + VERTEX_FLOATS] - v[o], az = v[o + VERTEX_FLOATS + 2] - v[o + 2], bx = v[o + 2 * VERTEX_FLOATS] - v[o], bz = v[o + 2 * VERTEX_FLOATS + 2] - v[o + 2];
    out.push({ y, area: Math.abs(ax * bz - az * bx) / 2, x: (v[o] + v[o + VERTEX_FLOATS] + v[o + 2 * VERTEX_FLOATS]) / 3, z: (v[o + 2] + v[o + VERTEX_FLOATS + 2] + v[o + 2 * VERTEX_FLOATS + 2]) / 3 });
  }
  return out;
}

describe('every building stands on the ground', () => {
  for (const [name, floors] of buildings) {
    test(`${name}: stacked cutaway, every single floor, and the plain view`, () => {
      const n = floors.length;
      assertGrounded(`${name} stacked`, buildBuildingScene(floors, { activeIndex: n - 1, showAll: true, cutaway: true }), n);
      for (let fi = 0; fi < n; fi++) assertGrounded(`${name} floor ${fi}`, buildBuildingScene(floors, { activeIndex: fi, cutaway: true }), fi + 1);
      assertGrounded(`${name} plain`, buildBuildingScene(floors, { activeIndex: 0 }), 1);
    });
  }

  test('the site is flush with the foot of the podium: lawn, paving and car park, and nothing left at the old ground', () => {
    for (const [name, floors] of buildings) {
      const s = buildBuildingScene(floors, { activeIndex: 0, showAll: true, cutaway: true });
      const { plinth, ground } = s.structure;
      const f = flats(s.opaque.vertices);
      const lawn = f.filter((t) => near(t.y, ground - 0.02, 0.003)).reduce((a, t) => a + t.area, 0);
      assert.ok(lawn > (plinth.x1 - plinth.x0) * (plinth.z1 - plinth.z0), `${name}: a lawn round the building at the foot of the podium (${lawn.toFixed(0)} m2)`);
      // Nothing is left at the old ground level outside the podium: the lawn, paving and car park all moved down with the site.
      const plinthRect = { x0: plinth.x0 - 0.1, z0: plinth.z0 - 0.1, x1: plinth.x1 + 0.1, z1: plinth.z1 + 0.1 };
      const stale = f.filter((t) => t.y > -0.03 && t.y < -0.004 && t.area > 0.5 && !(t.x >= plinthRect.x0 && t.x <= plinthRect.x1 && t.z >= plinthRect.z0 && t.z <= plinthRect.z1));
      assert.equal(stale.length, 0, `${name}: ground surfaces still at the ground floor level outside the podium`);
      // The halos of the site lamps moved down with the site.
      for (let i = 1; i < s.halos.length; i += 8) assert.ok(s.halos[i] > ground, `${name}: halo above the ground`);
    }
  });

  test('a larger upper floor makes the floors under it larger, and the free part of a lower slab gets a terrace roof', () => {
    const plans = slabPlans([{ x0: 0, z0: 0, x1: 10, z1: 8 }, { x0: -2, z0: 0, x1: 14, z1: 10 }, { x0: 0, z0: 0, x1: 6, z1: 6 }]);
    assert.deepEqual(plans[0], { x0: -2, z0: 0, x1: 14, z1: 10 });
    assert.deepEqual(plans[2], { x0: 0, z0: 0, x1: 6, z1: 6 });
    const ring = ringOf(plans[1], plans[2]);
    assert.equal(ring.reduce((a, r) => a + (r.x1 - r.x0) * (r.z1 - r.z0), 0), 16 * 10 - 6 * 6, 'the terrace is the slab less the floor above');
    const s = buildBuildingScene(overhang, { activeIndex: 2, showAll: true, cutaway: true });
    assert.ok(s.walls.some((w) => w.side === 'roof' && w.floorIndex === 1), 'floor 1 has a terrace roof where floor 2 does not cover it');
    const cols = columnsOf(1, plans[1], plans[2], FLOOR_PITCH, 2 * FLOOR_PITCH - SLAB);
    for (const [x, z] of [[plans[2].x1, plans[2].z1]] as const) assert.ok(cols.some((c) => Math.abs(c.x - x) <= COLUMN && Math.abs(c.z - z) <= COLUMN), 'an inner corner of the slab above is carried');
  });

  test('the structure stays cheap: an 8 floor stack draws under 3 percent more for it', () => {
    const big = Array.from({ length: 8 }, (_, i) => floor(i, [room(`a${i}`, 0, 0, 14, 8), room(`b${i}`, 15, 0, 8, 8, 'meeting')]));
    const s = buildBuildingScene(big, { activeIndex: 3, showAll: true, cutaway: true });
    const colTris = s.structure.columns.length * 8;
    assert.ok(colTris < s.triangles * 0.03, `${colTris} of ${s.triangles}`);
  });
});

describe('campus buildings stand on the ground', () => {
  test('every campus archetype has an exterior whose base covers its footprint at ground level', () => {
    for (const [arch, kind] of Object.entries(EXTERIOR_OF)) {
      const a = EXTERIOR_BUILDERS[kind]({ seed: 'g' });
      const v = a.mesh.vertices;
      let minY = Infinity, lx0 = Infinity, lx1 = -Infinity;
      for (let i = 0; i < v.length; i += VERTEX_FLOATS) { minY = Math.min(minY, v[i + 1]); if (v[i + 1] <= 0.05) { lx0 = Math.min(lx0, v[i]); lx1 = Math.max(lx1, v[i]); } }
      assert.ok(minY >= -0.01 && minY < 0.1, `${arch}: on the ground (${minY})`);
      const w = a.mesh.bounds.max.x - a.mesh.bounds.min.x;
      assert.ok(lx1 - lx0 >= w * 0.7, `${arch}: the base reaches across the building at ground level`);
    }
  });
  test('a plan shell never has a storey wider than the one under it, and stands on its plinth', () => {
    for (const [name, floors] of buildings) {
      const frame = planFrame(floors);
      if (!frame) continue;
      frame.storeys.forEach((s, i) => { if (i > 0) assert.ok(within(s.rect, frame.storeys[i - 1].rect), `${name}: storey ${i} sits on storey ${i - 1}`); });
      const shell = buildPlanShell('adminBlock', frame, { seed: 'g', row: 0 });
      assert.ok(shell.mesh.bounds.min.y >= -0.01 && shell.mesh.bounds.min.y < 0.1, `${name}: shell on the ground`);
    }
  });
});
